-- WoWBridge link (v0.8): a connection to the companion over two one-way channels, the frames on the screen (addon ->
-- companion) and the font mailbox (companion -> addon, Mailbox.lua). src/wuxianworkshop/transport/link.py is the
-- companion's side. This file is the transport only: debug output is Debug.lua, hot loading Agent.lua, dialogs UI.lua.
-- Handshake: a HELLO frame (session, mailbox slot, oldest unacknowledged message) until the first mailbox packet
-- arrives, normally a WELCOME. Then a small heartbeat frame whose content changes every 2 s, while the companion writes
-- a packet at least every `hb` seconds (its WELCOME says); three missed and the link goes back to HELLO. HELLO and HB
-- also name the slot the mailbox last skipped as damaged (bad=<slot>), so that the companion sends its records again.
-- Messages: the first byte says what the message is (TYPE_*: user text, debug, a RUN result, a RELOAD report, a link
-- test), then the text. One frame (64x16) when it fits, otherwise parts of up to 255 bigger frames (128x32). Up to 16
-- messages in flight, every frame up at least 0.25 s, parts in order. Message ids run 1..65535 and wrap. A message is
-- dropped when a packet's cumulative ack covers it; 4 s after a message's last part was shown, the parts the companion
-- has not confirmed (PARTS records) are shown again.
local _, ns = ...
local Codec, Mailbox = ns.Codec, ns.Mailbox
local band, floor = bit.band, math.floor

local L = {}
ns.Link = L
L.VERSION = "0.9.4"

-- the first byte of a message; Send's `kind` picks it
L.TYPE_TEXT, L.TYPE_DEBUG, L.TYPE_RUN, L.TYPE_RELOAD, L.TYPE_TEST = 0, 1, 2, 3, 4
local TYPE_OF = { debug = L.TYPE_DEBUG, run = L.TYPE_RUN, reload = L.TYPE_RELOAD,
	burst = L.TYPE_TEST, long = L.TYPE_TEST, stream = L.TYPE_TEST, test = L.TYPE_TEST }

local P = {
	helloW = 64, helloH = 16, hbW = 32, hbH = 8,
	dataW = 64, dataH = 16,     -- a message that fits one frame
	bigW = 128, bigH = 32,      -- the parts of a longer one
	cell = 4, mode = 1,
	show = 0.25,     -- a frame stays up at least this long
	rto = 4,         -- unconfirmed parts are shown again this long after a message's last part was shown
	win = 16,        -- messages in flight
	offline = 15,    -- no mailbox packet for this long: back to HELLO (3 heartbeats, at least 15 s; see L.Welcome)
	hb = 5,          -- the companion's heartbeat, as its WELCOME says
	beat = 2,        -- the heartbeat frame changes this often
}
L.P = P

local db, Print
local state = "off"          -- off | hello | online
local queue = {}             -- messages not acknowledged yet (see Send)
local nextId                 -- the id the next message gets (1..65535, kept across /reload)
local frame                  -- what the link has on the bus: { kind, since }
local lastPacket, lastAck = nil, 0
local beat, ctlId = 0, 0
local pong                   -- a ping id to answer
local stats = { packets = 0, acked = 0, resent = 0, sum = 0, max = 0, draws = 0, drawSum = 0, drawMax = 0 }
local burst                  -- { n, last, t0, acked, sum, max, resent0 }
local stream                 -- { n, last, t0, acked, sum, max, resent0, ending }
local handlers = {}          -- companion commands besides ping / reload: name -> function(rest) (L.OnCommand)

-- a <= b for ids that wrap after 65535
local function Before(a, b)
	return (b - a) % 65535 < 32768
end

local function Oldest()
	return queue[1] and queue[1].id or nextId
end

local function Hex32(x)
	return ("%04x%04x"):format(floor(x / 65536), x % 65536)   -- %x of a number >= 2^31 overflows on 32-bit longs
end

local function Draw(kind, W, H, ftype, msg, payload, mode, part, parts)
	local t0 = debugprofilestop()
	ns.ShowFrame({ kind = kind, W = W, H = H, cell = P.cell, mode = mode or 1, ftype = ftype, msg = msg, payload = payload,
		part = part or 0, parts = parts or 1 })
	local ms = debugprofilestop() - t0
	stats.draws, stats.drawSum = stats.draws + 1, stats.drawSum + ms
	if ms > stats.drawMax then stats.drawMax = ms end
	frame = { kind = kind, since = GetTime() }
end

local function Control(kind, payload)
	ctlId = ctlId % 65535 + 1
	if kind == "hello" then
		Draw(kind, P.helloW, P.helloH, Codec.TYPE_CONTROL, ctlId, payload)
	else
		Draw(kind, P.hbW, P.hbH, Codec.TYPE_CONTROL, ctlId, payload)
	end
end

-- ";bad=<slots>": the slots the mailbox skipped as damaged (the last n), or nothing
local function Skipped(n)
	local s = Mailbox.Skipped(n)
	return s and (";bad=" .. s) or ""
end

local function ControlPayload(kind)
	if kind == "hello" then
		local pw, ph = GetPhysicalScreenSize()
		local version, build, _, toc = GetBuildInfo()          -- the program installs its addons with this ## Interface
		return ("HELLO;v=%s;s=%d;k=%d;m=%d;p=%s;pw=%d;ph=%d;es=%.6g;gv=%s.%s;i=%d%s"):format(L.VERSION, ns.session,
			Mailbox.Next(), Oldest(), Mailbox.Proc(), pw, ph, UIParent:GetEffectiveScale(), version or "?", build or "?",
			tonumber(toc) or 0, Skipped(8))
	end
	return ("HB;n=%d;k=%d;m=%d;a=%d;q=%d%s"):format(beat % 100000, Mailbox.Next(), Oldest(), lastAck, #queue, Skipped(1))
end

-- the part of message m to show next, or nil: first every part in order, then (rto after the last part of a pass was
-- shown) every part the companion has not confirmed
local function NextPart(m, now)
	if not m.pending then
		if now - m.passEnd < P.rto then return nil end
		local p = {}
		for i = 1, #m.frags do
			if not m.ok[i] then p[#p + 1] = i end
		end
		if #p == 0 then return nil end
		m.pending, m.resent = p, m.resent + #p
		stats.resent = stats.resent + #p
	end
	while m.pending[1] and m.ok[m.pending[1]] do table.remove(m.pending, 1) end
	if not m.pending[1] then
		m.pending, m.passEnd = nil, now
		return nil
	end
	return m.pending[1]
end

-- what goes on the bus next; runs every 0.05 s
function L.Step()
	if state == "off" then return end
	local now = GetTime()
	local cur = ns.CurrentFrame()
	if cur and cur.kind == "probe" then   -- an experiment has the bus
		frame = nil
		return
	end
	if frame and now - frame.since < P.show then return end
	if pong then                          -- answered before any data: it measures the round trip
		Control("pong", ("PONG;id=%s;k=%d"):format(pong, Mailbox.Next()))
		pong = nil
		return
	end
	for n, m in ipairs(queue) do
		if n > P.win then break end
		local i = NextPart(m, now)
		if i then
			m.firstShown = m.firstShown or now
			Draw("data", m.W, m.H, Codec.TYPE_DATA, m.id, m.frags[i], P.mode, i - 1, #m.frags)
			table.remove(m.pending, 1)
			if not m.pending[1] then m.pending, m.passEnd = nil, now end
			return
		end
	end
	local kind = state == "online" and "hb" or "hello"
	if frame and frame.kind == kind and now - frame.since < P.beat then return end
	beat = beat + 1
	Control(kind, ControlPayload(kind))
end

function L.Welcome(text)
	local f = {}
	for k, v in text:gmatch("(%w+)=([^;]*)") do f[k] = v end
	local n = function(k) return tonumber(f[k]) end
	if n("mode") and n("mode") >= 0 and n("mode") <= 2 then P.mode = n("mode") end
	if n("w") and n("h") and n("w") >= 24 and n("w") <= 255 and n("h") >= 8 and n("h") <= 255 then P.dataW, P.dataH = n("w"), n("h") end
	if n("bw") and n("bh") and n("bw") >= 24 and n("bw") <= 255 and n("bh") >= 8 and n("bh") <= 255 then P.bigW, P.bigH = n("bw"), n("bh") end
	if n("win") and n("win") >= 1 and n("win") <= 64 then P.win = n("win") end
	if n("show") and n("show") >= 0.1 and n("show") <= 2 then P.show = n("show") end
	if n("hb") and n("hb") >= 1 and n("hb") <= 60 then          -- offline after three heartbeats missed
		P.hb, P.offline = n("hb"), math.max(15, 3 * n("hb"))
	end
	Print(ns.L.COMPANION:format(tostring(f.v), P.dataW, P.dataH, P.bigW, P.bigH, P.mode, P.win, P.show, P.hb))
end

-- another addon handles a companion command (WoWBridge_Lab: "probe")
function L.OnCommand(name, fn)
	handlers[name] = fn
end

function L.Command(text)
	local cmd, rest = text:match("^(%S+)%s*(.-)$")
	if cmd == "ping" then
		pong = rest
	elseif cmd == "reload" then
		-- "reload <nonce>": on this client only a click or key press may reload the UI (ReloadUI and ConsoleExec from a
		-- timer: "interface action failed"), so a button asks the user. The nonce is saved first: a packet read again
		-- cannot ask twice
		if rest == "" or rest == db.reloadNonce then return end
		db.reloadNonce = rest
		ns.UI.AskReload()
	elseif handlers[cmd] then
		handlers[cmd](rest)
	else
		Print(ns.L.COMMAND:format(text))
	end
end

-- PARTS "<id>:<hex>": bit i of byte i // 8 = part i is in
local function Parts(text)
	local id, hex = text:match("^(%d+):(%x*)$")
	id = tonumber(id)
	for _, m in ipairs(queue) do
		if m.id == id then
			for i = 0, #hex / 2 - 1 do
				local byte = tonumber(hex:sub(2 * i + 1, 2 * i + 2), 16)
				for b = 0, 7 do
					if band(byte, 2 ^ b) ~= 0 then m.ok[8 * i + b + 1] = true end
				end
			end
			return
		end
	end
end

-- the stream's summary, once it has stopped sending and every message of it is acknowledged
local function StreamDone(now)
	local s = stream
	if not (s and s.ending) then return end
	for _, m in ipairs(queue) do
		if m.kind == "stream" then return end
	end
	stream = nil
	if s.n > 0 then
		L.Send(("STREAM DONE n=%d acked=%d resent=%d avg=%.2fs max=%.2fs total=%.1fs"):format(s.n, s.acked,
			stats.resent - s.resent0, s.sum / math.max(s.acked, 1), s.max, now - s.t0), "test")
	end
end

local function Acked(m, now)
	local dt = now - (m.firstShown or now)
	stats.acked, stats.sum = stats.acked + 1, stats.sum + dt
	if dt > stats.max then stats.max = dt end
	if m.kind == "long" then
		L.Send(("LONG DONE id=%d bytes=%d parts=%d shown_again=%d time=%.1fs draw=%.1f/%.1fms"):format(m.id, m.size, #m.frags,
			m.resent, dt, stats.drawSum / math.max(stats.draws, 1), stats.drawMax), "test")
	end
	if stream and m.kind == "stream" then
		stream.acked, stream.sum = stream.acked + 1, stream.sum + dt
		if dt > stream.max then stream.max = dt end
		StreamDone(now)
	end
	if burst then
		burst.acked, burst.sum = burst.acked + 1, burst.sum + dt
		if dt > burst.max then burst.max = dt end
		if m.id == burst.last then
			local b = burst
			burst = nil
			L.Send(("BURST DONE n=%d acked=%d resent=%d avg=%.2fs max=%.2fs total=%.1fs"):format(b.n, b.acked,
				stats.resent - b.resent0, b.sum / math.max(b.acked, 1), b.max, now - b.t0), "test")
		end
	end
end

local function OnPacket(pkt)
	local now = GetTime()
	lastPacket = now
	stats.packets = stats.packets + 1
	if state ~= "online" then
		state = "online"
		Print(ns.L.CONNECTED:format(pkt.slot))
		ns.Console.Add("LINK", ns.L.CON_LINK_UP)
	end
	if pkt.session == ns.session and pkt.ack > 0 then
		lastAck = pkt.ack
		while queue[1] and Before(queue[1].id, pkt.ack) do
			Acked(table.remove(queue, 1), now)
		end
	end
	for _, r in ipairs(pkt.records) do
		if r.kind == "WELCOME" then
			L.Welcome(r.data)
		elseif r.kind == "TEXT" then
			Print("> " .. r.data)
		elseif r.kind == "COMMAND" then
			L.Command(r.data)
		elseif r.kind == "CODE" then
			ns.Agent.Code(r.data)
		elseif r.kind == "PARTS" and pkt.session == ns.session then
			Parts(r.data)
		end
	end
end

-- runs every 0.5 s
function L.Tick()
	local now = GetTime()
	if state == "online" and lastPacket and now - lastPacket > P.offline then
		state = "hello"
		Print(ns.L.OFFLINE:format(P.offline, #queue))
		ns.Console.Add("LINK", ns.L.CON_LINK_DOWN)
	end
	ns.Debug.Tick(now)
	ns.UI.CheckSlots()
end

-- queue a message of a kind (nil = user text, "debug", "run", "reload", or a link test: "burst", "long", "stream",
-- "test"); returns its id and its number of parts
function L.Send(text, kind)
	local body = string.char(TYPE_OF[kind] or L.TYPE_TEXT) .. text
	local W, H = P.dataW, P.dataH
	local cap = Codec.Capacity(ns.Frame.DataCells(W, H), P.mode, 0)
	if #body > cap then
		W, H = P.bigW, P.bigH
		cap = Codec.Capacity(ns.Frame.DataCells(W, H), P.mode, 0)
	end
	if #body > 255 * cap then
		Print(ns.L.CUT:format(#text, 255 * cap - 1))
		body = body:sub(1, 255 * cap)
	end
	local frags, pending = {}, {}
	for i = 1, #body, cap do
		frags[#frags + 1] = body:sub(i, i + cap - 1)
		pending[#pending + 1] = #frags
	end
	local id = nextId
	nextId = nextId % 65535 + 1
	db.nextData = nextId
	queue[#queue + 1] = { id = id, frags = frags, ok = {}, pending = pending, passEnd = 0, resent = 0, W = W, H = H,
		size = #body - 1, kind = kind }
	return id, #frags
end

-- how many messages of a kind wait in the queue (Debug.lua's cap)
function L.Waiting(kind)
	local n = 0
	for _, m in ipairs(queue) do
		if m.kind == kind then n = n + 1 end
	end
	return n
end

function L.Burst(n)
	n = math.max(1, math.min(n or 20, 200))
	burst = { n = n, t0 = GetTime(), acked = 0, sum = 0, max = 0, resent0 = stats.resent }
	for i = 1, n do
		burst.last = L.Send(("burst %d/%d %s"):format(i, n, ("WoWBridge "):rep(i % 30)), "burst")
	end
	return n
end

-- a test message of n bytes: "LONG n=<n> crc=<crc32 of the body>" and a body of ASCII and Chinese text
function L.Long(n)
	n = math.max(64, math.min(n or 8000, 60000))
	local head = ("LONG n=%d crc=00000000\n"):format(n)
	local unit = "WoWBridge 长消息分片测试 0123456789 "
	local body = unit:rep(math.ceil((n - #head) / #unit)):sub(1, n - #head)
	local t = {}
	for i = 1, #body do t[i] = body:byte(i) end
	local text = ("LONG n=%d crc=%s\n"):format(n, Hex32(Codec.CRC32(t, 1, #body) % 4294967296)) .. body
	return L.Send(text, "long")
end

-- E2 / E3 test data: "S <i> crc=<crc32 of body> <body>", 40-300 bytes of letters, digits and Chinese (4 KB every 20th)
local ALPHABET = { "W", "o", "B", "r", "i", "d", "g", "e", "0", "1", "2", "3", "4", "5", " ", "风", "暴", "要", "塞", "联" }

local function StreamText(i)
	local x = (i * 7919) % 65537
	local n = i % 20 == 0 and 4000 or 40 + x % 261
	local out, len = {}, 0
	while len < n do
		x = (x * 75 + 74) % 65537
		local c = ALPHABET[x % #ALPHABET + 1]
		out[#out + 1], len = c, len + #c
	end
	local body = table.concat(out)
	local t = {}
	for k = 1, #body do t[k] = body:byte(k) end
	return ("S %d crc=%s %s"):format(i, Hex32(Codec.CRC32(t, 1, #body) % 4294967296), body)
end

function L.Stream(seconds)
	seconds = math.max(5, math.min(seconds or 120, 3600))
	local t0, i = GetTime(), 0
	stream = { n = 0, t0 = t0, acked = 0, sum = 0, max = 0, resent0 = stats.resent }
	local s = stream
	local ticker
	ticker = C_Timer.NewTicker(0.3, function()
		if GetTime() - t0 >= seconds or stream ~= s then
			ticker:Cancel()
			s.ending = true            -- STREAM DONE goes out when the last one is acknowledged
			StreamDone(GetTime())
			return
		end
		if #queue >= 8 then return end
		i = i + 1
		s.n, s.last = i, L.Send(StreamText(i), "stream")
	end)
	return seconds
end

function L.State() return state end

function L.SlotsLeft()
	return Mailbox.Phase() == "full" and 0 or Mailbox.POOL - Mailbox.Next() + 1
end

function L.Status()
	local avg = stats.acked > 0 and stats.sum / stats.acked or 0
	return ns.L.LINK_STATUS:format(ns.StateName(state), Mailbox.Next(), Mailbox.Phase(), L.SlotsLeft(), stats.packets, #queue, stats.acked,
		avg, stats.max, stats.resent, stats.drawSum / math.max(stats.draws, 1), stats.drawMax)
end

-- what the settings panel shows: state, how many wait, how many were acknowledged, the heartbeat
function L.Numbers()
	return { state = state, waiting = #queue, acked = stats.acked, hb = P.hb, packets = stats.packets }
end

local tickers = {}

function L.Start(savedDb, print)
	db, Print = savedDb, print
	if #tickers > 0 then return end     -- already running
	nextId = db.nextData or 1
	state = "hello"
	Mailbox.Start(db, OnPacket)
	tickers = { C_Timer.NewTicker(0.25, Mailbox.Tick), C_Timer.NewTicker(0.05, L.Step), C_Timer.NewTicker(0.5, L.Tick) }
	ns.Debug.Start()                     -- what came before the link started
end

-- the link off: no frame, no mailbox reading (the companion sees the link down and waits; L.Start picks it up again)
function L.Stop()
	for _, t in ipairs(tickers) do t:Cancel() end
	tickers = {}
	state = "off"
	ns.Frame.Hide()
end
