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
-- record names it after that (the companion asks for what it misses). Passes nobody asked for come further apart each
-- time (rto, 2 rto, 4 rto ... at most AGAIN_MAX s) and never before a message that has not gone up yet: the cumulative
-- ack waits for that one (0.9.9; before, a stream of RUN results shown again could keep it from ever going up, and the
-- frame never stopped changing). /wb flush, or the companion's "flush", drops every message waiting.
-- A WELCOME that says mode=3 (0.9.9) has data frames, heartbeats and PONGs drawn in mode 3 (Frame.lua: the cells laid
-- along ∞, drawn up to 1.6 times wider than its own proportions to fill the frame), each frame about as wide as its ∞
-- (heartbeat 32 x 12; data frames w3 x h and bw3 x bh, which the WELCOME may set: 56 x 16 and 128 x 32). From 0.9.14
-- every frame is an ∞: before any WELCOME the link draws in mode 3 too (HELLO, BYE), as every app since 0.9.9 reads it;
-- a WELCOME without mode= (an older app) means mode 1, and an addon that never heard an app that stands by draws every
-- other HELLO in mode 1 once nobody answered for FLAT_AFTER s (an app before 0.9.9 reads only those). In mode 3 no frame
-- is filled past BREATH_HIGH of its room (fuller, the ∞ swells into a blob), and the parts of a long message breathe: their sizes go
-- from BREATH_LOW to BREATH_HIGH of a big frame's room and back every BREATH_PERIOD parts, so that its ∞ swells and
-- shrinks as it goes up (each part's header says its length; the companion joins them as ever).
-- From 0.9.6 (FEATURES, f= in HELLO and PONG) a WELCOME may also set rto (the companion then acknowledges every few
-- seconds instead of every second, which saves mailbox slots, and asks at once for what went missing) and pack=1: a small
-- message joins the last one of its kind not shown yet, as a batch (TYPE_BATCH: type, length (2 bytes), text, ...), so
-- that a burst of small messages takes a few frames instead of one each. HB says up to which id every message has been
-- shown (l=), so that the companion knows what it misses also when nothing comes after it.
-- A WELCOME that says z=1 (0.9.9) has a message that takes two frames or more go squeezed when that takes fewer
-- (TYPE_ZIP: blocks of its type byte and text, each a 2-byte length (high bit: stored as it is) and its bytes,
-- Codec.Squeeze); a short body is squeezed in Send, a longer one a block a Step (each about 1 ms), and the link tests
-- go as they are, so that they still measure the link.
-- Pours (FEATURES 4, 0.9.13): the rest of a long answer goes up whole, a segment a message (TYPE_POUR: "<key> <i>/<m>\n"
-- and the segment's bytes as they are; Agent.lua's data call "pour" cuts them): the one way it goes. L.Pour takes what
-- hands out the segments; a Step queues the next one while fewer than POUR_AHEAD of them wait to go up, so that one is
-- squeezed while the one before it goes up and the queue never holds the whole answer. They are no RUN results: those
-- still go first, and the segments go in the window in order as other messages do. With no mailbox packet for
-- POUR_QUIET seconds (the companion does not read the screen: the game minimized, a loading screen) no new segment is
-- queued until one comes.
-- Standing by (FEATURES 5, 0.9.14): the frame shows only in a session, while things pass between the app and the addon;
-- the rest of the time the link stands by, the mailbox read and nothing on the screen (the app only peeks, with GDI, at
-- the frame's place once a second: the doorbell). Nobody above the link opens or ends a session: the link does.
--   The app's records go in a packet stamped "at <ms>" (a COMMAND record, the app's clock in ms). Standing by, a packet
--   stamped less than STAMP_TTL s ago opens a session (HELLO, then on as ever; its records are the session's first);
--   one stamped earlier waited in the mailbox (the character screen, the link off), and the app has given up on it: its
--   records are dropped, as is every request that comes while standing by unstamped or off.
--   The addon's own data (a result, an event, a pour, the player's text; not prints and errors, which wait for the next
--   session) rings the doorbell: the HELLO frame goes up at the frame's place, which the HELLO says (o=x,y: whole pixels
--   from the client area's top left; the app keeps it). Nobody answering in HELLO_GIVEUP s, the addon stands by again
--   and rings again after RING_AGAIN s, twice that the next time, at most RING_MAX. The frame unlocked (L.Hold) rings too.
--   The addon ends the session: nothing waits to go up, the frame is locked, and nothing passed for the WELCOME's idle= s
--   (0: never; requests and the addon's own data count; the app's heartbeats, acks, prints and errors do not; a 0.9.14 app says a few seconds: a session is
--   over when what passes is done), nor within a "hold <s>" the app sent (it listens s more seconds for prints and errors,
--   which do not ring): a BYE control frame ("BYE;why=idle") up for LEAVE_SHOW s, then standing by. So do the player (L.Hide: /wb hide, the panel, the key binding: why=hide; /wb off:
--   why=off) and the app's "rest <ms>" (its 收起: why=rest).
-- The app also says "here <ms> <app>" (it runs: at its start and every few minutes) and "bye <ms>" (it quits: no
-- doorbell until it is back). The addon stands by only after an app said "here" or stamped a packet (db.appNew): an
-- older app does neither, and the addon shows its HELLO as ever; and entering the world in a new game process with no app
-- heard within LOGIN_QUIET s (an app started after the addon read the mailbox, or an older one) shows it LOGIN_SHOW s. Off, the mailbox is still read (no backlog builds up
-- meanwhile) and only "here" and "bye" are heard. A session kept across /reload (db.link) shows its HELLO at once, as
-- ever; a new app ("here" with another <app>) reads the frames of one going on and takes it over.
-- Who keeps a session (0.10.0, for the panel's first line: L.Kept): a WELCOME with idle=0 means a session always, by the
-- app's settings, or, when it also says by=<title>,<title> (the first few) and byn=<how many>, by the apps that hold the
-- link while they run. The titles are data: the panel shows them as plain text.
local _, ns = ...
local Codec, Mailbox = ns.Codec, ns.Mailbox
local band, floor, char = bit.band, math.floor, string.char

local L = {}
ns.Link = L
L.VERSION = "0.10.0"
-- 1: takes rto and pack from a WELCOME, shows again at once what a PARTS record asks for, HB l=;
-- 2 (0.9.7): also takes CALL records (data calls, Agent.lua);
-- 3 (0.9.9): also inflates the CODE and CALL jobs that come compressed (Agent.lua, Inflate.lua; a client that read the
-- .toc before Inflate.lua came, until the game restarts, does not load it);
-- 4 (0.9.13): also pours a long answer's rest (the data call "pour", TYPE_POUR messages);
-- 5 (0.9.14): also stands by: a session only while things pass (a stamped packet opens one, the addon's data rings the
-- doorbell, idle= ends it, "hold" keeps it), "here", "bye", "rest", BYE frames, o= in HELLO
L.FEATURES = ns.Inflate and 5 or 2

-- the first byte of a message; Send's `kind` picks it
L.TYPE_TEXT, L.TYPE_DEBUG, L.TYPE_RUN, L.TYPE_RELOAD, L.TYPE_TEST, L.TYPE_EVENT, L.TYPE_BATCH = 0, 1, 2, 3, 4, 5, 6
L.TYPE_ZIP = 7                                   -- a squeezed message (0.9.9): the companion unpacks it, then reads it
L.TYPE_POUR = 8                                  -- a segment of a pour (0.9.13)
local TYPE_OF = { debug = L.TYPE_DEBUG, run = L.TYPE_RUN, reload = L.TYPE_RELOAD, event = L.TYPE_EVENT,
	burst = L.TYPE_TEST, long = L.TYPE_TEST, stream = L.TYPE_TEST, test = L.TYPE_TEST, pour = L.TYPE_POUR }
local URGENT = { run = true, reload = true }     -- shown before everything else, never packed
local AGAIN_MAX = 120                            -- seconds between a message's passes nobody asked for, at most
local POUR_AHEAD = 2                             -- segments of pours queued and not shown yet, at most
local POUR_QUIET = 20                            -- seconds without a mailbox packet after which no segment is queued
local STAMP_TTL = 8                              -- seconds an app's stamped packet opens a session for: the app gives up on
                                                 -- it after 12 s (link.py WAKE_WAIT), so what came with it runs then or never
local HELLO_GIVEUP = 30                          -- seconds of HELLO without a packet before an addon that can stand by does
local RING_AGAIN, RING_MAX = 60, 600             -- seconds before the doorbell rings again after nobody answered it (doubling)
local FLAT_AFTER = 4                             -- seconds of HELLO unanswered after which an addon that never heard an
                                                 -- app that stands by draws every other HELLO in mode 1 (apps before 0.9.9)
local LEAVE_SHOW = 0.8                           -- seconds the BYE frame stays up before the frame goes
local LOGIN_QUIET, LOGIN_SHOW = 20, 10           -- entering the world in a new game process, standing by: no app heard
                                                 -- within LOGIN_QUIET s (it says "here" first thing in a game process it
                                                 -- sees), the HELLO shows LOGIN_SHOW s: an app that started after the
                                                 -- addon read the mailbox's stamp, or an older one, learns where it reads.
                                                 -- The doorbell waits for it (no app there: 10 s of frame, not 30)
local REACH = 660                                -- s an app that stands by counts as there after its last packet (it says
                                                 -- "here" every 600 s while there is no session: link.py PRESENCE_EVERY)

local P = {
	helloW = 64, helloH = 16, hbW = 32, hbH = 8,
	hbW3 = 32, hbH3 = 12,       -- mode 3: the heartbeat, about as wide as the ∞ its cells make ...
	dataW3 = 56, bigW3 = 128,   -- ... and the data frames' widths (WELCOME w3, bw3; heights as in mode 1)
	dataW = 64, dataH = 16,     -- a message that fits one frame
	bigW = 128, bigH = 32,      -- the parts of a longer one
	cell = 4, mode = 3,  -- (mode 3 until a WELCOME says otherwise: every app since 0.9.9 reads it)
	show = 0.25,     -- a frame stays up at least this long
	rto = 4,         -- unconfirmed parts are shown again this long after a message's last part was shown
	win = 16,        -- messages in flight
	pack = false,    -- small messages of a kind join into one (WELCOME pack=1)
	zip = false,     -- long messages go squeezed (WELCOME z=1: the companion unpacks them)
	offline = 15,    -- no mailbox packet for this long: back to HELLO (3 heartbeats, at least 15 s; see L.Welcome)
	hb = 5,          -- the companion's heartbeat, as its WELCOME says
	beat = 2,        -- the heartbeat frame changes this often; in HELLO a HELLO goes up this often also while messages wait
	busy = 8,        -- online, a heartbeat frame goes up at least this often while messages wait
	keep = 256,      -- messages kept at most; past that the oldest go (nobody acknowledges them: no companion)
	idle = 0,        -- seconds without anything passing after which the addon ends a session (WELCOME idle=; 0: never)
}
L.P = P

local db, Print
local state = "off"          -- off | standby | hello | online | leaving (the BYE frame up, then `leaving.to`)
local helloSince             -- when the link last went to HELLO
local helloJob               -- the agent's newest job before the wake that started this session (HELLO j=): the jobs
                             -- of the wake's packet run in this session, and their results are on their way
local leaving                -- { to = "standby" | "off", why, at = when the frame goes }
local appAway                -- the app said "bye" (the panel and the minimap say so); nil: unknown or here
local lastBusy               -- when something last passed in this session (a request, a message up): idle= counts
local holdUntil = 0          -- the app listens until then ("hold <s>"): the session does not end idle before
local ringAfter, ringBackoff = 0, RING_AGAIN      -- the doorbell rings again from then on; the wait after the next giveup
local held                   -- the frame is unlocked (L.Hold): a session, that does not end idle
local heardApp               -- an app that stands by said "here" or stamped a packet since this UI loaded
local loginCheck             -- when to show the HELLO if no app was heard by then (LOGIN_QUIET)
local helloLimit             -- seconds this HELLO shows unanswered before standing by again (nil: HELLO_GIVEUP)
local kept                   -- who keeps the session, as the last WELCOME of an app that stands by said: { always, by =
                             -- { titles }, n = how many hold it }; nil: not heard since the UI loaded, or the app is gone
local slots0                 -- { left, at }: the app messages left when the link started (L.SlotsPace)
local queue = {}             -- messages not acknowledged yet (see Send)
local Squeeze                -- a block of a long message into its squeeze (defined with Shape)
local Pouring                -- the next segments of the pours into the queue (defined with L.Pour)
local nextId                 -- the id the next message gets (1..65535, kept across /reload)
local frame                  -- what the link has on the bus: { kind, since }
local lastControl            -- when the last HELLO / HB went up
local lastPacket, lastAck = nil, 0
local beat, ctlId = 0, 0
local hellos = 0              -- HELLO frames drawn (every other one flat: Control)
local pong                   -- a ping id to answer
local connected              -- the link was online before in this UI session: the chat says "again"
local offlineSaid            -- the chat said the link was lost: it says when it is back
local stats = { packets = 0, acked = 0, resent = 0, sum = 0, max = 0, draws = 0, drawSum = 0, drawMax = 0, lost = 0 }
local burst                  -- { n, last, t0, acked, sum, max, resent0 }
local stream                 -- { n, last, t0, acked, sum, max, resent0, ending }
local handlers = {}          -- companion commands besides ping / reload: name -> function(rest) (L.OnCommand)
local pours = {}             -- the pours going on, oldest first (L.Pour)

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
	-- the next one as soon as this one's time is up: at the 0.05 s ticks alone, the tick due with it often came a game
	-- frame early, and 0.15 s frames stayed 0.2 s (measured 2026-10-11: 35 parts in 6.8 s)
	C_Timer.After(P.show, L.Step)
end

-- a control frame in mode 3 (an ∞) unless the WELCOME said otherwise; a HELLO nobody answers, of an addon that never
-- heard an app that stands by, every other time in mode 1 (an app before 0.9.9 reads only that)
local function Control(kind, payload)
	ctlId = ctlId % 65535 + 1
	if kind == "hello" then
		hellos = hellos + 1
		local flat = P.mode ~= 3 or (not (db and db.appNew) and GetTime() - (helloSince or 0) > FLAT_AFTER and hellos % 2 == 0)
		Draw(kind, P.helloW, P.helloH, Codec.TYPE_CONTROL, ctlId, payload, flat and 1 or 3)
	elseif P.mode == 3 then
		Draw(kind, P.hbW3, P.hbH3, Codec.TYPE_CONTROL, ctlId, payload, 3)
	else
		Draw(kind, P.hbW, P.hbH, Codec.TYPE_CONTROL, ctlId, payload)
	end
end

-- ";bad=<slots>": the slots the mailbox skipped as damaged (the last n), or nothing
local function Skipped(n)
	local s = Mailbox.Skipped(n)
	return s and (";bad=" .. s) or ""
end

-- the frame's place, "x,y" in whole pixels from the client area's top left (Frame.Offset: kept on the screen)
local function Place()
	local ok, x, y = pcall(ns.Frame.Offset)
	return ok and ("%d,%d"):format(x or 0, y or 0) or "0,0"
end

local function ControlPayload(kind)
	if kind == "hello" then
		local pw, ph = GetPhysicalScreenSize()
		local version, build, _, toc = GetBuildInfo()          -- the program installs its addons with this ## Interface
		return ("HELLO;v=%s;s=%d;k=%d;m=%d;p=%s;pw=%d;ph=%d;es=%.6g;gv=%s.%s;i=%d;f=%d;j=%s;o=%s%s"):format(L.VERSION,
			ns.session, Mailbox.Next(), Oldest(), Mailbox.Proc(), pw, ph, UIParent:GetEffectiveScale(), version or "?",
			build or "?", tonumber(toc) or 0, L.FEATURES, helloJob or ns.Agent.LastJob(), Place(), Skipped(8))
	elseif kind == "bye" then
		return ("BYE;why=%s;k=%d;m=%d"):format(leaving and leaving.why or "hide", Mailbox.Next(), Oldest())
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
	m.pending, m.passEnd, m.shown, m.asked = nil, now, true, nil
	return nil
end

-- a new pass over the parts the companion has not confirmed: at once when it asked for them (m.rush: a PARTS record
-- named the message), else rto after the last pass ended, twice that after the next one and so on, at most AGAIN_MAX s
-- (a message the companion cannot take, or an ack held up, must not keep the frame busy; the count starts over when the
-- link comes back); the first part of it, or nil
local function PassAgain(m, now)
	if m.pending or not (m.rush or now - m.passEnd >= math.min(P.rto * 2 ^ m.again, AGAIN_MAX)) then return nil end
	if not m.rush then m.again = m.again + 1 end
	local asked = m.rush
	m.rush = nil
	local p = {}
	for i = 1, #m.frags do
		if not m.ok[i] then p[#p + 1] = i end
	end
	m.passEnd = now
	if #p == 0 then return nil end                     -- every part is in: the cumulative ack is on its way
	m.pending, m.resent, m.asked = p, m.resent + #p, asked
	stats.resent = stats.resent + #p
	return p[1]
end

-- the message whose part goes up next: a RUN result or RELOAD report first (its first pass, a pass going on, or one the
-- companion asked for; also past the window: the agent waits on it), then the first message not shown yet, then the
-- first whose parts are due again (a RUN result's also past the window). A pass nobody asked for never comes before a
-- message's first: the companion's cumulative ack waits for that one
local function Pick(now)
	local fresh, held
	for n, m in ipairs(queue) do
		if m.urgent then
			if PassPart(m, now) or (m.rush and PassAgain(m, now)) then return m end
		elseif n <= P.win and m.shown and (m.asked and PassPart(m, now) or m.rush and PassAgain(m, now)) then
			return m                                -- the companion asked for it: before the fresh ones
		elseif n <= P.win and not fresh and not held and not m.shown then
			if m.squeeze then
				held = true                         -- being squeezed: the ones after it wait for it
			elseif PassPart(m, now) then
				fresh = m
			end
		end
	end
	if fresh then return fresh end
	for n, m in ipairs(queue) do
		if (m.urgent or n <= P.win) and m.shown and (PassPart(m, now) or PassAgain(m, now)) then return m end
	end
end

local function Hidden()
	frame = nil
	if ns.HideFrame then ns.HideFrame() else ns.Frame.Hide() end
end

-- the session over: no frame, the mailbox read; the app wakes it again (db.link is kept across /reload)
local function StandBy()
	state, helloJob, leaving = "standby", nil, nil
	db.link = "standby"
	Hidden()
end

local function Off()
	state, helloJob, leaving = "off", nil, nil
	db.link = "standby"
	Hidden()
end

-- a session: HELLO at once, then as ever. kept: the app's stamped packet opened it (the app reads the screen from its
-- write on, and waits for what it asked across a /reload too); a doorbell's is kept once the app answers
local function Open(kept, limit)
	state, helloSince, helloJob, lastControl, leaving = "hello", GetTime(), ns.Agent.LastJob(), nil, nil
	helloLimit = limit
	lastBusy = GetTime()
	if kept then db.link = "session" end
	for _, m in ipairs(queue) do m.again = 0 end    -- what waited goes up within rto
end

-- the BYE frame, up for LEAVE_SHOW s; then standing by (to "standby") or off. why: idle, hide, rest or off
local function Leave(to, why)
	leaving = { to = to, why = why or to, at = GetTime() + LEAVE_SHOW }
	state = "leaving"
	beat = beat + 1
	lastControl = GetTime()
	Control("bye", ControlPayload("bye"))
end

-- what goes on the bus next; runs every 0.05 s, and when a frame's display time is up (Draw)
function L.Step()
	if state == "off" or state == "standby" then return end
	if state == "leaving" then
		if GetTime() >= leaving.at then
			if leaving.to == "off" then Off() else StandBy() end
		end
		return
	end
	if pours[1] then Pouring(GetTime()) end
	for _, m in ipairs(queue) do               -- a block of the first long message being squeezed
		if m.squeeze then
			Squeeze(m)
			break
		end
	end
	local now = GetTime()
	local cur = ns.CurrentFrame()
	if cur and cur.kind == "probe" then   -- an experiment has the bus
		frame = nil
		return
	end
	if frame and now - frame.since < P.show - 0.005 then return end   -- GetTime counts whole ms: Draw's timer may see 0.149
	if pong then                         -- answered before any data: it measures the round trip
		Control("pong", ("PONG;id=%s;k=%d;f=%d;v=%s"):format(pong, Mailbox.Next(), L.FEATURES, L.VERSION))
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
		if m.kind ~= "debug" then lastBusy = now end   -- the addon's own data going up: the session is in use
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
	P.mode = n("mode") and n("mode") >= 0 and n("mode") <= 3 and n("mode") or 1   -- none: an older app, mode 1
	if n("w") and n("h") and n("w") >= 24 and n("w") <= 255 and n("h") >= 8 and n("h") <= 255 then P.dataW, P.dataH = n("w"), n("h") end
	if n("bw") and n("bh") and n("bw") >= 24 and n("bw") <= 255 and n("bh") >= 8 and n("bh") <= 255 then P.bigW, P.bigH = n("bw"), n("bh") end
	if n("w3") and n("w3") >= 24 and n("w3") <= 255 then P.dataW3 = n("w3") end
	if n("bw3") and n("bw3") >= 24 and n("bw3") <= 255 then P.bigW3 = n("bw3") end
	if n("win") and n("win") >= 1 and n("win") <= 64 then P.win = n("win") end
	if n("show") and n("show") >= 0.1 and n("show") <= 2 then P.show = n("show") end
	if n("hb") and n("hb") >= 1 and n("hb") <= 60 then          -- offline after three heartbeats missed
		P.hb, P.offline = n("hb"), math.max(15, 3 * n("hb"))
	end
	-- an app before 0.9.14 says no idle=, and ends no session either (its addon never stands by)
	P.idle = n("idle") and n("idle") >= 0 and n("idle") <= 86400 and n("idle") or 0
	kept = nil
	if f.idle then                      -- (0.10.0) who keeps it: the settings, or the apps the WELCOME names
		local by = {}
		for title in (f.by or ""):gmatch("[^,]+") do
			if #by < 4 then by[#by + 1] = title end
		end
		kept = { always = P.idle == 0, by = by, n = math.max(#by, math.floor(n("byn") or 0)) }
	end
	-- a companion before 0.9.6 says neither: it acknowledges every second and reads no batches (also when it takes over
	-- from a newer one in this session)
	P.rto = n("rto") and n("rto") >= 2 and n("rto") <= 60 and n("rto") or 4
	P.pack = f.pack == "1"
	P.zip = f.z == "1"
end

-- another addon handles a companion command (WoWBridge_Lab: "probe")
function L.OnCommand(name, fn)
	handlers[name] = fn
end

-- "reload <nonce> [reason]": the nonce is the companion's clock in ms (mod 1e8); one older than this many ms is from a
-- packet that waited out a game restart in the mailbox and asks nothing any more. The reason (the agent's words, one
-- line; a companion before 0.9.9 gives none) shows in the dialog and the chat
local RELOAD_STALE = 600000

function L.Command(text)
	local cmd, rest = text:match("^(%S+)%s*(.-)$")
	if cmd == "ping" then
		pong = rest
	elseif cmd == "flush" then
		Print(ns.L.FLUSHED:format(L.Flush()))
	elseif cmd == "reload" then
		-- "reload <nonce>": on this client only a click or key press may reload the UI (ReloadUI and ConsoleExec from a
		-- timer: "interface action failed"), so a button asks the user. The nonce is saved first: a packet read again
		-- cannot ask twice
		local nonce, reason = rest:match("^(%S*)%s*(.-)$")
		if nonce == "" or nonce == db.reloadNonce then return end
		db.reloadNonce = nonce
		local age = (time() * 1000 - (tonumber(nonce) or 0)) % 1e8
		if age > RELOAD_STALE and age < 1e8 - 60000 then return end
		ns.UI.AskReload(reason ~= "" and reason or nil)
	elseif cmd == "at" or cmd == "wake" or cmd == "rest" or cmd == "here" or cmd == "bye" or cmd == "hold" then
		return                               -- the session's say, done in OnPacket
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
			-- what the companion has of it now, in place of what was known: a companion that started over (a restart in
			-- the middle of the message) has none of the parts the one before it had, and they have to go again (adding
			-- to the old marks, the message never came whole and was given up)
			local ok = {}
			for i = 0, #hex / 2 - 1 do
				local byte = tonumber(hex:sub(2 * i + 1, 2 * i + 2), 16)
				for b = 0, 7 do
					if band(byte, 2 ^ b) ~= 0 then ok[8 * i + b + 1] = true end
				end
			end
			m.ok = ok
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
	if m.pour then                                -- a segment the companion has: its pour hears so (Agent.lua keeps the answer)
		local ok, err = pcall(m.pour.Acked, m.segment)
		if not ok then geterrorhandler()(err) end
	end
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

-- whether an app's <ms> is recent: a packet stamped longer than STAMP_TTL ago waited in the mailbox (the clocks are this
-- PC's)
local function Fresh(ms)
	local age = time() - (tonumber(ms) or 0) / 1000
	return age > -60 and age < STAMP_TTL
end

-- the app's say about the session, before the packet's other records: "at" (the packet's stamp; "wake" in the 0.9.14
-- test builds), "rest", "here", "bye", and "hold" (the app listens a while: prints and errors do not ring; only in a
-- session, its own or the one the packet opened: one that came while off or too late keeps nothing). Gives whether
-- the packet opened a session
local function Say(pkt)
	local opened
	for _, r in ipairs(pkt.records) do
		local cmd, a, b
		if r.kind == "COMMAND" then cmd, a, b = r.data:match("^(%a+)%s*(%S*)%s*(%S*)") end
		if (cmd == "here" or cmd == "at" or cmd == "wake") and not db.appNew then
			Print(ns.L.MET_APP)                         -- the first app that rings and stands by: said once
		end
		if cmd == "here" or cmd == "at" or cmd == "wake" then heardApp = true end
		if cmd == "here" then                           -- (another app, started anew, takes a session going on over:
			db.appNew, appAway = true, nil              -- it reads the frames and welcomes them)
			if b ~= "" then db.appId = b end
		elseif cmd == "bye" then
			appAway, kept = true, nil
			if state == "hello" or state == "online" then StandBy() end
		elseif cmd == "at" or cmd == "wake" then
			db.appNew, appAway = true, nil
			if Fresh(a) and (state == "standby" or (state == "leaving" and leaving.to == "standby")) then
				opened = true                           -- (the player's hide a moment before: the app's request wins)
				Open(true)
			end
		elseif cmd == "hold" and tonumber(a) and (opened or state == "hello" or state == "online") then
			holdUntil = GetTime() + math.max(0, math.min(tonumber(a), 600))
		elseif cmd == "rest" and Fresh(a) and (state == "hello" or state == "online") then
			Leave("standby", "rest")
		end
	end
	return opened
end

-- what makes a session busy: a request (not the app's heartbeat, its say about the session, or a ping)
local SAY = { at = true, wake = true, rest = true, here = true, bye = true, ping = true, hold = true }

local function Busy(pkt)
	for _, r in ipairs(pkt.records) do
		if r.kind == "CODE" or r.kind == "CALL" or r.kind == "TEXT" then return true end
		if r.kind == "COMMAND" and not SAY[r.data:match("^(%a*)")] then return true end
	end
	return false
end

-- standing by, off or leaving, a packet's records are not done (the app's say aside): a stamped one opened a session,
-- and one that did not came unasked or too late (the app has given up on it)
local function OnPacket(pkt)
	local now = GetTime()
	lastPacket = now
	db.heardAt = time()                              -- (kept across /reload: L.Reachable)
	stats.packets = stats.packets + 1
	ringAfter, ringBackoff = 0, RING_AGAIN           -- an app reads the mailbox: the doorbell may ring at once
	local opened = Say(pkt)
	if pkt.session == ns.session and pkt.ack > 0 then       -- also standing by: what the app has needs no session
		lastAck = pkt.ack
		while queue[1] and Before(queue[1].id, pkt.ack) do     -- (the idle time counts from what went up, not
			Acked(table.remove(queue, 1), now)                  -- from its ack: done is done)
		end
	end
	if state == "off" or state == "standby" or state == "leaving" then return end   -- the app's say alone
	if Busy(pkt) then lastBusy = now end
	if state ~= "online" and not opened then
		state, helloJob, lastBusy = "online", nil, now
		db.link = "session"
		if not db.appNew then                          -- an app that does not stand by: online as ever
			Print(connected and ns.L.RECONNECTED or ns.L.CONNECTED)
		elseif offlineSaid then                        -- sessions come and go unsaid; a loss said is said back
			Print(ns.L.RECONNECTED)
		end
		connected, offlineSaid = true, nil
		ns.Console.Add("LINK", ns.L.CON_LINK_UP)
		for _, m in ipairs(queue) do m.again = 0 end    -- what went up while nobody read it goes again within rto
	end
	for _, r in ipairs(pkt.records) do     -- one record failing does not lose the others (the slot is read once)
		local ok, err = pcall(Record, r, pkt)
		if not ok then geterrorhandler()(err) end
	end
end

-- data of the addon's own waits to go up (rings the doorbell): a message not shown yet that is not a print or an
-- error, a pour's segments, an addon's event (API.lua keeps those while the link is not online). One shown and not
-- acknowledged yet does not: the app most likely has it, and asks for what it misses in the next session
local function Wanted()
	for _, m in ipairs(queue) do
		if m.kind ~= "debug" and not m.shown then return true end
	end
	return pours[1] ~= nil or (ns.EventsWaiting ~= nil and ns.EventsWaiting() > 0)
end

-- a message of the addon's own (not a print or an error) not acknowledged yet
local function Owed()
	for _, m in ipairs(queue) do
		if m.kind ~= "debug" then return true end
	end
	return false
end

-- nothing passes, nothing of the addon's own waits and the app does not listen: the session may end once that has
-- lasted P.idle s. Prints and errors neither ring nor keep a session (an addon printing every second would keep it for
-- good): what is left of them goes in the next one
local function Quiet(now)
	return not Owed() and not pours[1] and not held and not (ns.EventsWaiting and ns.EventsWaiting() > 0)
		and now >= holdUntil and now - (lastBusy or now) >= P.idle
end

-- runs every 0.5 s
function L.Tick()
	local now = GetTime()
	if state == "online" and lastPacket and now - lastPacket > P.offline then
		state, helloSince = "hello", now
		if not db.appNew or Wanted() then           -- standing by, a loading screen is no news; data waiting is
			Print(ns.L.OFFLINE:format(#queue))
			offlineSaid = true
		end
		ns.Console.Add("LINK", ns.L.CON_LINK_DOWN)
	elseif state == "hello" and db.appNew and now - math.max(helloSince or 0, lastPacket or 0) > (helloLimit or HELLO_GIVEUP) then
		StandBy()                            -- nobody answers: stand by rather than show HELLO; ring again later
		ringAfter, ringBackoff = now + ringBackoff, math.min(ringBackoff * 2, RING_MAX)
	elseif state == "online" and db.appNew and P.idle > 0 and Quiet(now) then
		Leave("standby", "idle")
	elseif state == "standby" and db.appNew and not appAway and now >= ringAfter
			and (held or (Wanted() and (heardApp or not loginCheck))) then
		Open(false)                          -- the doorbell (after a login, not before LOGIN_QUIET unless an app was heard)
	end
	if loginCheck and now >= loginCheck then
		loginCheck = nil
		if state == "standby" and not heardApp then Open(false, LOGIN_SHOW) end
	end
	ns.Debug.Tick(now)
	ns.UI.CheckSlots()
end

local function Capacity(W, H)
	return Codec.Capacity(ns.Frame.DataCells(W, H), P.mode, 0)
end

local BREATH_LOW, BREATH_HIGH, BREATH_PERIOD = 0.70, 0.90, 12   -- 0.45 / 0.70 took 75 % more parts (2026-10-11)

-- the data frames' widths in this mode: mode 3's as wide as their ∞
local function DataW() return P.mode == 3 and P.dataW3 or P.dataW end
local function BigW() return P.mode == 3 and P.bigW3 or P.bigW end

-- the most a frame of this size takes in one go: in mode 3, BREATH_HIGH of its room
local function Most(W, H)
	local cap = Capacity(W, H)
	return P.mode == 3 and floor(cap * BREATH_HIGH) or cap
end

-- the size of part i (0, 1, ...) of a long message in a frame with room for cap bytes: in mode 3 a cosine from
-- BREATH_LOW of it up to BREATH_HIGH and back over BREATH_PERIOD parts, else all of it
local function PartSize(cap, i)
	if P.mode ~= 3 then return cap end
	local f = BREATH_LOW + (BREATH_HIGH - BREATH_LOW) * (1 - math.cos(2 * math.pi * i / BREATH_PERIOD)) / 2
	return floor(cap * f)
end

-- the frames of message m for this body: one small frame when it fits, else parts of big ones (255 at most, the rest cut)
local function Shape(m, body)
	local W, H = DataW(), P.dataH
	if #body > Most(W, H) then W, H = BigW(), P.bigH end
	local cap, whole = Capacity(W, H), #body <= Most(W, H)
	local frags, pending, at = {}, {}, 1
	while at <= #body do
		if #frags == 255 then                   -- the rest is cut
			Print(ns.L.CUT:format(#body - 1, at - 2))
			body = body:sub(1, at - 1)
			break
		end
		local n = whole and #body or PartSize(cap, #frags)
		frags[#frags + 1] = body:sub(at, at + n - 1)
		pending[#pending + 1] = #frags
		at = at + n
	end
	m.W, m.H, m.frags, m.pending, m.bytes, m.size = W, H, frags, pending, #body, #body - 1
end

-- the frames a body of n bytes takes (as Shape cuts it)
local function Frames(n)
	if n <= Most(BigW(), P.bigH) then return 1 end           -- one frame, small or big, as Shape cuts it
	local cap, parts, at = Capacity(BigW(), P.bigH), 0, 0
	while at < n do
		at = at + PartSize(cap, parts)
		parts = parts + 1
	end
	return parts
end

local SQUEEZE_NOW = 5000     -- bytes squeezed at once in Send (about 1.3 ms); a longer body goes a block a Step

-- the next block of message m's body into its squeeze; after the last, its frames: the squeezed body if that takes
-- fewer, else the body as it is
function Squeeze(m)
	local z = m.squeeze
	local block = z.body:sub(z.at, z.at + Codec.SQUEEZE_BLOCK - 1)
	local packed = Codec.Squeeze(block)
	if #packed < #block then
		z.out[#z.out + 1] = char(floor(#packed / 256), #packed % 256) .. packed
	else                                      -- nothing to gain: stored
		z.out[#z.out + 1] = char(128 + floor(#block / 256), #block % 256) .. block
	end
	z.at = z.at + #block
	if z.at <= #z.body then return end
	m.squeeze = nil
	local squeezed = table.concat(z.out)
	if Frames(#squeezed) < Frames(#z.body) then
		m.zipped = true
		Shape(m, squeezed)
		m.bytes = #z.body                     -- Waiting counts what was sent (API.lua's and Debug.lua's caps)
	else
		Shape(m, z.body)
	end
end

-- a batch: TYPE_BATCH, then each message as its type, its length (2 bytes) and its text
local function Batch(m)
	local out = { char(L.TYPE_BATCH) }
	for _, text in ipairs(m.items) do
		out[#out + 1] = char(m.type, floor(#text / 256), #text % 256) .. text
	end
	return table.concat(out)
end

-- queue a message of a kind (nil = user text, "debug", "run", "reload", "event" (API.lua), "pour" (a segment, with the
-- pour it belongs to and its number there), or a link test: "burst", "long", "stream", "test"); returns its id and its
-- number of parts (0 while a long body is being squeezed). With pack on, a message joins the last one of its kind while
-- that one has not gone up and the two still fit one frame (they share the id then); segments never do
function L.Send(text, kind, pour, segment)
	local t = TYPE_OF[kind] or L.TYPE_TEXT
	local last = queue[#queue]
	if P.pack and not URGENT[kind] and not pour and last and last.kind == kind and not last.firstShown and #last.frags == 1
		and not last.zipped then
		local size = (#last.items == 1 and 4 + #last.items[1] or last.bytes) + 3 + #text
		if size <= Most(BigW(), P.bigH) then
			last.items[#last.items + 1] = text
			Shape(last, Batch(last))
			return last.id, 1
		end
	end
	local id = nextId
	nextId = nextId % 65535 + 1
	db.nextData = nextId
	local m = { id = id, type = t, items = { text }, kind = kind, urgent = URGENT[kind], ok = {}, passEnd = 0, resent = 0,
		again = 0, pour = pour, segment = segment }
	local body = char(t) .. text
	if P.zip and t ~= L.TYPE_TEST and Frames(#body) > 1 then
		m.squeeze = { body = body, at = 1, out = { char(L.TYPE_ZIP) } }
		m.frags, m.bytes, m.size = {}, #body, #body - 1    -- no frames yet: Pick passes it by
		if #body <= SQUEEZE_NOW then
			while m.squeeze do Squeeze(m) end
		end
	else
		Shape(m, body)
	end
	queue[#queue + 1] = m
	while #queue > P.keep do            -- nobody acknowledges them: the companion learns of the gap from HB m=
		table.remove(queue, 1)
		stats.lost = stats.lost + 1
	end
	return id, #m.frags
end

-- a pour (Agent.lua): p.key, p.Next() (the next segment's text and its number, nil once there is none) and
-- p.Acked(number) (that segment acknowledged). A pour of the same key asked again takes the place of the one going on
function L.Pour(p)
	for i = #pours, 1, -1 do
		if pours[i].key == p.key then table.remove(pours, i) end
	end
	pours[#pours + 1] = p
end

-- while the companion reads the screen, the next segments into the queue, until POUR_AHEAD of them wait to go up
function Pouring(now)
	if state ~= "online" or not lastPacket or now - lastPacket > POUR_QUIET then return end
	local ahead = 0
	for _, m in ipairs(queue) do
		if m.pour and not m.shown then ahead = ahead + 1 end
	end
	while ahead < POUR_AHEAD and pours[1] do
		local p = pours[1]
		local ok, text, segment = pcall(p.Next)
		if not ok then geterrorhandler()(text) end
		if ok and text then
			L.Send(text, "pour", p, segment)
			ahead = ahead + 1
		else
			table.remove(pours, 1)                -- all of it queued (or broken): the next pour
		end
	end
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

-- drops every message waiting to go up (/wb flush, or the companion's "flush"): the frame goes quiet. The next control
-- frame says the oldest message (m=), and the companion moves its ack past the ones dropped; a RUN result dropped so is
-- never answered (the agent's call times out), and the pours stop. Returns how many messages went
function L.Flush()
	local n = 0
	for i = #queue, 1, -1 do
		n = n + #queue[i].items
		queue[i] = nil
	end
	for i = #pours, 1, -1 do pours[i] = nil end   -- the pours stop too
	lastControl = nil                    -- that control frame goes up next
	return n
end

function L.State() return state end

-- the app said "bye" since it last said "here" (the panel, the minimap)
function L.AppAway() return appAway == true end

-- whether the app can reach the addon now (WoWBridge.Online, for addons that tell the player): a session on, or standing
-- by (also opening or ending one) with an app that stands by, heard within REACH s and not gone ("bye")
function L.Reachable()
	if state == "online" then return true end
	if not (db and db.appNew) or appAway or state == "off" then return false end
	return time() - (db.heardAt or 0) < REACH
end

-- who keeps the session, as the app last said (the panel's first line): whether it is there always, the titles of the
-- apps that hold it (none: the app's settings do) and how many they are; nothing while that is not known
function L.Kept()
	if kept then return kept.always, kept.by, kept.n end
end

-- whether an app that wakes the addon was heard (db.appNew): /wb on stands by then, else shows HELLO
function L.CanStandBy() return db ~= nil and db.appNew == true end

-- the session ended by the player (/wb hide, the panel, the key binding): the BYE frame, then standing by. False when
-- there is no session, or the app cannot wake the addon again (an older one: /wb off is the way)
function L.Hide()
	if not (db and db.appNew) or not (state == "hello" or state == "online") then return false end
	Leave("standby", "hide")
	ringAfter = GetTime() + RING_AGAIN       -- what still waits rings later, not at once
	return true
end

-- the frame unlocked (on) or locked again: unlocked, a session (the frame shows where it goes, and the app follows it)
-- that does not end idle; locked, the idle time counts from then
function L.Hold(on)
	held = on and true or nil
	if held and state == "standby" and db and db.appNew and not appAway then
		Open(false)
	elseif not held then
		lastBusy = GetTime()                         -- the idle time counts from now
	end
end

-- the link off (/wb off): no session, nothing heard but "here" and "bye" (the mailbox is still read)
function L.Off()
	if state == "off" or (state == "leaving" and leaving.to == "off") then return end
	if (state == "hello" or state == "online") and db.appNew then
		Leave("off", "off")
	elseif state == "leaving" then
		leaving.to, leaving.why = "off", "off"
	else
		Off()
	end
end

-- the link on again (/wb on): standing by when an app wakes it, else HELLO (an older app)
function L.On()
	if state ~= "off" then return end
	if db.appNew then StandBy() else
		state, helloSince, lastControl = "hello", GetTime(), nil
		db.link = "session"
	end
end

function L.SlotsLeft()
	return Mailbox.Phase() == "full" and 0 or Mailbox.POOL - Mailbox.Next() + 1
end

-- the hours the app messages left last at the pace they went since the link started (the panel's meter); nil while that
-- says nothing: less than PACE_AFTER s, or fewer than PACE_SLOTS of them gone
local PACE_AFTER, PACE_SLOTS = 120, 3

function L.SlotsPace()
	if not slots0 then return nil end
	local left = L.SlotsLeft()
	if left > slots0.left then slots0 = { left = left, at = GetTime() } end   -- a new game process: every slot unread again
	local seconds, gone = GetTime() - slots0.at, slots0.left - left
	if left <= 0 or seconds < PACE_AFTER or gone < PACE_SLOTS then return nil end
	return left / (gone / seconds) / 3600
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
	return ns.L.LINK_STATUS:format(ns.StateName(state), L.SlotsLeft(), stats.packets, Unsent(), stats.acked, avg, stats.max,
		stats.resent, stats.drawSum / math.max(stats.draws, 1), stats.drawMax)
end

-- what the settings panel shows: state, how many wait to go up, how many were acknowledged, the heartbeat
function L.Numbers()
	return { state = state, waiting = Unsent(), acked = stats.acked, hb = P.hb, packets = stats.packets, away = appAway == true }
end

local tickers = {}
local mailEvery = 0.05         -- s between looks at the mailbox (0.25 before 0.9.14; L.MailEvery)

-- on: false starts the link off (autoLink off: the mailbox read, nothing heard but "here" and "bye"). Stands by when an
-- app that wakes the addon was heard and no session was going on before the /reload; else HELLO, as ever. initial: the
-- first entering of the world in this game process (PLAYER_ENTERING_WORLD's isInitialLogin): LOGIN_QUIET
function L.Start(savedDb, print, on, initial)
	db, Print = savedDb, print
	if #tickers > 0 then return end     -- already running
	nextId = db.nextData or 1
	lastControl, helloJob, leaving = nil, nil, nil
	if on == false then
		state = "off"
	elseif db.appNew and db.link ~= "session" then
		state = "standby"
		loginCheck = initial and GetTime() + LOGIN_QUIET or nil
	else
		state, helloSince = "hello", GetTime()
	end
	Mailbox.Start(db, OnPacket)
	slots0 = slots0 or { left = L.SlotsLeft(), at = GetTime() }
	tickers = { C_Timer.NewTicker(mailEvery, Mailbox.Tick), C_Timer.NewTicker(0.05, L.Step), C_Timer.NewTicker(0.5, L.Tick) }
	ns.Debug.Start()                     -- what came before the link started
end

-- how often the mailbox is looked at (s; 0.25 from L.Start): the ticker made again. Gives the interval in use
function L.MailEvery(seconds)
	mailEvery = math.max(0.02, math.min(tonumber(seconds) or mailEvery, 2))
	if tickers[1] then
		tickers[1]:Cancel()
		tickers[1] = C_Timer.NewTicker(mailEvery, Mailbox.Tick)
	end
	return mailEvery
end

-- the link stopped altogether: no frame, no mailbox reading (L.Start picks it up again: the panel's 重新连接)
function L.Stop()
	for _, t in ipairs(tickers) do t:Cancel() end
	tickers = {}
	state, leaving = "off", nil
	Hidden()
end
