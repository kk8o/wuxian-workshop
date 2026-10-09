-- WoWBridge link (v0.9.6): a connection to the companion over two one-way channels, the frames on the screen (addon ->
-- companion) and the font mailbox (companion -> addon, Mailbox.lua). src/wuxianworkshop/transport/link.py is the
-- companion's side. This file is the transport only: debug output is Debug.lua, hot loading Agent.lua, dialogs UI.lua.
-- Handshake: a HELLO frame (session, mailbox slot, oldest unacknowledged message) until the first mailbox packet
-- arrives, normally a WELCOME. Then a small heartbeat frame whose content changes every 2 s, while the companion writes
-- a packet at least every `hb` seconds (its WELCOME says); three missed and the link goes back to HELLO. HELLO and HB
-- also name the slot the mailbox last skipped as damaged (bad=<slot>), so that the companion sends its records again.
-- A control frame also goes up while messages wait: in HELLO every `beat` seconds, online every `busy` seconds (a queue
-- that never emptied once kept the link from ever coming up: the companion welcomes only after a control frame).
-- Messages: the first byte says what the message is (TYPE_*: user text, debug, a RUN result, a RELOAD report, a link
-- test, an addon's event, a batch), then the text. One frame (64x16) when it fits, otherwise parts of up to 255 bigger
-- frames (128x32). Up to `win` messages in flight, every frame up at least 0.25 s. RUN results and RELOAD reports go
-- first (the agent waits on them), then the messages not shown yet in order, then parts shown again. Message ids run
-- 1..65535 and wrap. A message is dropped when a packet's cumulative ack covers it; `rto` seconds after a message's last
-- part was shown, the parts the companion has not confirmed (PARTS records) are shown again, and at once when a PARTS
-- record names it after that (the companion asks for what it misses).
-- From 0.9.6 (FEATURES, f= in HELLO and PONG) a WELCOME may also set rto (the companion then acknowledges every few
-- seconds instead of every second, which saves mailbox slots, and asks at once for what went missing) and pack=1: a small
-- message joins the last one of its kind not shown yet, as a batch (TYPE_BATCH: type, length (2 bytes), text, ...), so
-- that a burst of small messages takes a few frames instead of one each. HB says up to which id every message has been
-- shown (l=), so that the companion knows what it misses also when nothing comes after it.
local _, ns = ...
local Codec, Mailbox = ns.Codec, ns.Mailbox
local band, floor, char = bit.band, math.floor, string.char

local L = {}
ns.Link = L
L.VERSION = "0.9.7"
L.FEATURES = 2     -- 1: takes rto and pack from a WELCOME, shows again at once what a PARTS record asks for, HB l=;
                   -- 2 (0.9.7): also takes CALL records (data calls, Agent.lua)

-- the first byte of a message; Send's `kind` picks it
L.TYPE_TEXT, L.TYPE_DEBUG, L.TYPE_RUN, L.TYPE_RELOAD, L.TYPE_TEST, L.TYPE_EVENT, L.TYPE_BATCH = 0, 1, 2, 3, 4, 5, 6
local TYPE_OF = { debug = L.TYPE_DEBUG, run = L.TYPE_RUN, reload = L.TYPE_RELOAD, event = L.TYPE_EVENT,
	burst = L.TYPE_TEST, long = L.TYPE_TEST, stream = L.TYPE_TEST, test = L.TYPE_TEST }
local URGENT = { run = true, reload = true }     -- shown before everything else, never packed

local P = {
	helloW = 64, helloH = 16, hbW = 32, hbH = 8,
	dataW = 64, dataH = 16,     -- a message that fits one frame
	bigW = 128, bigH = 32,      -- the parts of a longer one
	cell = 4, mode = 1,
	show = 0.25,     -- a frame stays up at least this long
	rto = 4,         -- unconfirmed parts are shown again this long after a message's last part was shown
	win = 16,        -- messages in flight
	pack = false,    -- small messages of a kind join into one (WELCOME pack=1)
	offline = 15,    -- no mailbox packet for this long: back to HELLO (3 heartbeats, at least 15 s; see L.Welcome)
	hb = 5,          -- the companion's heartbeat, as its WELCOME says
	beat = 2,        -- the heartbeat frame changes this often; in HELLO a HELLO goes up this often also while messages wait
	busy = 8,        -- online, a heartbeat frame goes up at least this often while messages wait
	keep = 256,      -- messages kept at most; past that the oldest go (nobody acknowledges them: no companion)
}
L.P = P

local db, Print
local state = "off"          -- off | hello | online
local queue = {}             -- messages not acknowledged yet (see Send)
local nextId                 -- the id the next message gets (1..65535, kept across /reload)
local frame                  -- what the link has on the bus: { kind, since }
local lastControl            -- when the last HELLO / HB went up
local lastPacket, lastAck = nil, 0
local beat, ctlId = 0, 0
local pong                   -- a ping id to answer
local welcomed               -- what the last WELCOME set, as the chat line says it (said once)
local stats = { packets = 0, acked = 0, resent = 0, sum = 0, max = 0, draws = 0, drawSum = 0, drawMax = 0, lost = 0 }
local burst                  -- { n, last, t0, acked, sum, max, resent0 }
local stream                 -- { n, last, t0, acked, sum, max, resent0, ending }
local handlers = {}          -- companion commands besides ping / reload: name -> function(rest) (L.OnCommand)

-- a <= b for ids that wrap after 65535
local function Before(a, b)
	return (b - a) % 65535 < 32768
end

local function Prev(id)
	return (id - 2) % 65535 + 1
end

local function Oldest()
	return queue[1] and queue[1].id or nextId
end

-- the id up to which every message has gone up at least once (HB l=)
local function ShownThrough()
	for _, m in ipairs(queue) do
		if not m.shown then return Prev(m.id) end
	end
	return Prev(nextId)
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
		return ("HELLO;v=%s;s=%d;k=%d;m=%d;p=%s;pw=%d;ph=%d;es=%.6g;gv=%s.%s;i=%d;f=%d;j=%s%s"):format(L.VERSION, ns.session,
			Mailbox.Next(), Oldest(), Mailbox.Proc(), pw, ph, UIParent:GetEffectiveScale(), version or "?", build or "?",
			tonumber(toc) or 0, L.FEATURES, ns.Agent.LastJob(), Skipped(8))
	end
	return ("HB;n=%d;k=%d;m=%d;l=%d;q=%d%s"):format(beat % 100000, Mailbox.Next(), Oldest(), ShownThrough(), #queue, Skipped(1))
end

-- the next part of message m's pass (parts the companion confirmed are left out); nil when no pass is going on, or when
-- this one just ended
local function PassPart(m, now)
	local p = m.pending
	if not p then return nil end
	while p[1] and m.ok[p[1]] do table.remove(p, 1) end
	if p[1] then return p[1] end
	m.pending, m.passEnd, m.shown = nil, now, true
	return nil
end

-- a new pass over the parts the companion has not confirmed: rto after the last one ended, or at once when it asked for
-- them (m.rush: a PARTS record named the message); the first part of it, or nil
local function PassAgain(m, now)
	if m.pending or not (m.rush or now - m.passEnd >= P.rto) then return nil end
	m.rush = nil
	local p = {}
	for i = 1, #m.frags do
		if not m.ok[i] then p[#p + 1] = i end
	end
	m.passEnd = now
	if #p == 0 then return nil end                     -- every part is in: the cumulative ack is on its way
	m.pending, m.resent = p, m.resent + #p
	stats.resent = stats.resent + #p
	return p[1]
end

-- the message whose part goes up next: a RUN result or RELOAD report first (its first pass, or a pass again; also past
-- the window: the agent waits on it), then the first message not shown yet, then the first whose parts are due again
local function Pick(now)
	local fresh
	for n, m in ipairs(queue) do
		if m.urgent then
			if PassPart(m, now) or (m.shown and PassAgain(m, now)) then return m end
		elseif n <= P.win and not fresh and not m.shown and PassPart(m, now) then
			fresh = m
		end
	end
	if fresh then return fresh end
	for n, m in ipairs(queue) do
		if n > P.win then break end
		if not m.urgent and m.shown and (PassPart(m, now) or PassAgain(m, now)) then return m end
	end
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
		Control("pong", ("PONG;id=%s;k=%d;f=%d"):format(pong, Mailbox.Next(), L.FEATURES))
		pong = nil
		return
	end
	local kind = state == "online" and "hb" or "hello"
	local due = not lastControl or now - lastControl >= (state == "online" and P.busy or P.beat)
	local m = not due and Pick(now)
	if m then
		local i = table.remove(m.pending, 1)
		m.firstShown = m.firstShown or now
		Draw("data", m.W, m.H, Codec.TYPE_DATA, m.id, m.frags[i], P.mode, i - 1, #m.frags)
		if not m.pending[1] then m.pending, m.passEnd, m.shown = nil, now, true end
		return
	end
	if not due and frame and frame.kind == kind and now - frame.since < P.beat then return end
	beat = beat + 1
	lastControl = now
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
	-- a companion before 0.9.6 says neither: it acknowledges every second and reads no batches (also when it takes over
	-- from a newer one in this session)
	P.rto = n("rto") and n("rto") >= 2 and n("rto") <= 60 and n("rto") or 4
	P.pack = f.pack == "1"
	local said = ns.L.COMPANION:format(tostring(f.v), P.dataW, P.dataH, P.bigW, P.bigH, P.mode, P.win, P.show, P.hb)
	if said ~= welcomed then             -- welcomed again (the companion restarted, or learned what this link does): once
		welcomed = said
		Print(said)
	end
end

-- another addon handles a companion command (WoWBridge_Lab: "probe")
function L.OnCommand(name, fn)
	handlers[name] = fn
end

-- "reload <nonce>": the nonce is the companion's clock in ms (mod 1e8); one older than this many ms is from a packet that
-- waited out a game restart in the mailbox and asks nothing any more
local RELOAD_STALE = 600000

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
		local age = (time() * 1000 - (tonumber(rest) or 0)) % 1e8
		if age > RELOAD_STALE and age < 1e8 - 60000 then return end
		ns.UI.AskReload()
	elseif handlers[cmd] then
		handlers[cmd](rest)
	else
		Print(ns.L.COMMAND:format(text))
	end
end

-- PARTS "<id>:<hex>": bit i of byte i // 8 = part i is in. A message shown whose pass is over goes again at once
-- (what it misses): the companion names one when something after it came, or when nothing of it has come for a while
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
			if m.shown and not m.pending then m.rush = true end
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
	local dt, n = now - (m.firstShown or now), #m.items
	stats.acked, stats.sum = stats.acked + n, stats.sum + dt * n
	if dt > stats.max then stats.max = dt end
	if m.kind == "long" then
		L.Send(("LONG DONE id=%d bytes=%d parts=%d shown_again=%d time=%.1fs draw=%.1f/%.1fms"):format(m.id, m.size, #m.frags,
			m.resent, dt, stats.drawSum / math.max(stats.draws, 1), stats.drawMax), "test")
	end
	if stream and m.kind == "stream" then
		stream.acked, stream.sum = stream.acked + n, stream.sum + dt * n
		if dt > stream.max then stream.max = dt end
		StreamDone(now)
	end
	if burst then
		burst.acked, burst.sum = burst.acked + n, burst.sum + dt * n
		if dt > burst.max then burst.max = dt end
		if m.id == burst.last then
			local b = burst
			burst = nil
			L.Send(("BURST DONE n=%d acked=%d resent=%d avg=%.2fs max=%.2fs total=%.1fs"):format(b.n, b.acked,
				stats.resent - b.resent0, b.sum / math.max(b.acked, 1), b.max, now - b.t0), "test")
		end
	end
end

local function Record(r, pkt)
	if r.kind == "WELCOME" then
		L.Welcome(r.data)
	elseif r.kind == "TEXT" then
		Print("> " .. r.data)
	elseif r.kind == "COMMAND" then
		L.Command(r.data)
	elseif r.kind == "CODE" then
		ns.Agent.Code(r.data)
	elseif r.kind == "CALL" then
		ns.Agent.Call(r.data)
	elseif r.kind == "PARTS" and pkt.session == ns.session then
		Parts(r.data)
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
	for _, r in ipairs(pkt.records) do     -- one record failing does not lose the others (the slot is read once)
		local ok, err = pcall(Record, r, pkt)
		if not ok then geterrorhandler()(err) end
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

local function Capacity(W, H)
	return Codec.Capacity(ns.Frame.DataCells(W, H), P.mode, 0)
end

-- the frames of message m for this body: one small frame when it fits, else parts of big ones (255 at most, the rest cut)
local function Shape(m, body)
	local W, H, cap = P.dataW, P.dataH, Capacity(P.dataW, P.dataH)
	if #body > cap then
		W, H, cap = P.bigW, P.bigH, Capacity(P.bigW, P.bigH)
	end
	if #body > 255 * cap then
		Print(ns.L.CUT:format(#body - 1, 255 * cap - 1))
		body = body:sub(1, 255 * cap)
	end
	local frags, pending = {}, {}
	for i = 1, #body, cap do
		frags[#frags + 1] = body:sub(i, i + cap - 1)
		pending[#pending + 1] = #frags
	end
	m.W, m.H, m.frags, m.pending, m.bytes, m.size = W, H, frags, pending, #body, #body - 1
end

-- a batch: TYPE_BATCH, then each message as its type, its length (2 bytes) and its text
local function Batch(m)
	local out = { char(L.TYPE_BATCH) }
	for _, text in ipairs(m.items) do
		out[#out + 1] = char(m.type, floor(#text / 256), #text % 256) .. text
	end
	return table.concat(out)
end

-- queue a message of a kind (nil = user text, "debug", "run", "reload", "event" (API.lua), or a link test: "burst",
-- "long", "stream", "test"); returns its id and its number of parts. With pack on, a message joins the last one of its
-- kind while that one has not gone up and the two still fit one frame (they share the id then)
function L.Send(text, kind)
	local t = TYPE_OF[kind] or L.TYPE_TEXT
	local last = queue[#queue]
	if P.pack and not URGENT[kind] and last and last.kind == kind and not last.firstShown and #last.frags == 1 then
		local size = (#last.items == 1 and 4 + #last.items[1] or last.bytes) + 3 + #text
		if size <= Capacity(P.bigW, P.bigH) then
			last.items[#last.items + 1] = text
			Shape(last, Batch(last))
			return last.id, 1
		end
	end
	local id = nextId
	nextId = nextId % 65535 + 1
	db.nextData = nextId
	local m = { id = id, type = t, items = { text }, kind = kind, urgent = URGENT[kind], ok = {}, passEnd = 0, resent = 0 }
	Shape(m, char(t) .. text)
	queue[#queue + 1] = m
	while #queue > P.keep do            -- nobody acknowledges them: the companion learns of the gap from HB m=
		table.remove(queue, 1)
		stats.lost = stats.lost + 1
	end
	return id, #m.frags
end

-- how many messages of a kind wait to go up, and their bytes (Debug.lua's and API.lua's caps): while online the ones not
-- shown yet (a shown one only waits for the companion's ack, which may take seconds), otherwise every one not
-- acknowledged (nobody acknowledges them then, and the queue must not grow)
function L.Waiting(kind)
	local n, bytes, all = 0, 0, state ~= "online"
	for _, m in ipairs(queue) do
		if m.kind == kind and (all or not m.shown) then
			n, bytes = n + #m.items, bytes + m.bytes
		end
	end
	return n, bytes
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
		if L.Waiting("stream") >= 8 then return end
		i = i + 1
		s.n, s.last = i, L.Send(StreamText(i), "stream")
	end)
	return seconds
end

function L.State() return state end

function L.SlotsLeft()
	return Mailbox.Phase() == "full" and 0 or Mailbox.POOL - Mailbox.Next() + 1
end

-- messages not shown yet
local function Unsent()
	local n = 0
	for _, m in ipairs(queue) do
		if not m.shown then n = n + #m.items end
	end
	return n
end

function L.Status()
	local avg = stats.acked > 0 and stats.sum / stats.acked or 0
	return ns.L.LINK_STATUS:format(ns.StateName(state), Mailbox.Next(), Mailbox.Phase(), L.SlotsLeft(), stats.packets, Unsent(),
		stats.acked, avg, stats.max, stats.resent, stats.drawSum / math.max(stats.draws, 1), stats.drawMax)
end

-- what the settings panel shows: state, how many wait to go up, how many were acknowledged, the heartbeat
function L.Numbers()
	return { state = state, waiting = Unsent(), acked = stats.acked, hb = P.hb, packets = stats.packets }
end

local tickers = {}

function L.Start(savedDb, print)
	db, Print = savedDb, print
	if #tickers > 0 then return end     -- already running
	nextId = db.nextData or 1
	state, lastControl = "hello", nil
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
