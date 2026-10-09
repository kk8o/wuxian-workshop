-- Font mailbox (v0.5): the companion -> addon channel. mail\00001.ttf .. 04096.ttf stay empty until the companion
-- writes one packet into a slot, as a font whose glyph widths carry the bytes (src/wuxianworkshop/core/mailbox.py is the
-- reference). Slots are read in order. On 1.60.1.70235 a font is cached by path until the client exits (/reload
-- included) and a failed load is not, so: an empty slot can be polled, and a slot is read once per game process.
-- mail\proc.ttf holds a stamp of the game process; when it differs from the saved one, reading starts at slot 1 again
-- (not when the saved one is "?", a stamp that could not be read: see M.Tick).
-- A slot whose packet keeps failing its checks is skipped after BAD_LIMIT tries and remembered (db.mail.skipped, the
-- last SKIPPED_KEPT); the link names it in its control frames so that the companion sends the slot's records again.
local _, ns = ...
local Font, Codec = ns.Font, ns.Codec
local byte = string.byte

local M = {}
ns.Mailbox = M
M.POOL = 4096
M.KINDS = { [1] = "WELCOME", [2] = "HEARTBEAT", [3] = "TEXT", [4] = "COMMAND", [5] = "PARTS", [6] = "CODE", [7] = "CALL" }

local DIR = "Interface\\AddOns\\WoWBridge\\mail\\"
local RESET_AFTER = 1.0     -- a FontString gets SetFont again this often while the slot is not readable: loading is
                            -- asynchronous, and a SetFont sooner might start the load over
local METERS = 2            -- FontStrings that take turns, so that an empty slot is still tried every 0.5 s
local BAD_LIMIT = 8         -- a slot whose packet keeps failing its checks is skipped after this many tries
local SKIPPED_KEPT = 8      -- skipped slots remembered for the control frames

local st                    -- { phase = "proc" | "poll" | "full", meters, lastSet, slot, t0, db, onPacket, proc, bad }

local function SlotPath(k)
	return DIR .. ("%05d.ttf"):format(k)
end

local function U16(s, i) return byte(s, i) * 256 + byte(s, i + 1) end
local function U32(s, i) return U16(s, i) * 65536 + U16(s, i + 2) end

-- a WM packet (the payload of the font's "WF" envelope): a table, or nil + reason
function M.Parse(s)
	if #s < 20 or s:sub(1, 2) ~= "WM" or byte(s, 3) ~= 1 then return nil, "head" end
	local finish = 16 + U16(s, 15)
	if #s < finish + 4 then return nil, "short" end
	local t = {}
	for i = 1, finish do t[i] = byte(s, i) end
	if Codec.CRC32(t, 1, finish) % 4294967296 ~= U32(s, finish + 1) then return nil, "crc" end
	local pkt = { slot = U32(s, 5), session = U16(s, 9), ack = U16(s, 11), records = {} }
	local i = 17
	for _ = 1, U16(s, 13) do
		if i + 2 > finish then return nil, "records" end
		local kind, len = byte(s, i), U16(s, i + 1)
		pkt.records[#pkt.records + 1] = { kind = M.KINDS[kind] or tostring(kind), data = s:sub(i + 3, i + 2 + len) }
		i = i + 3 + len
	end
	return pkt
end

-- meter i is set to slot k now (i = 1) or RESET_AFTER / METERS * (i - 1) later
local function Want(k)
	local now = GetTime()
	st.slot, st.bad = k, 0
	Font.Load(st.meters[1], SlotPath(k))
	st.lastSet[1] = now
	for i = 2, METERS do
		st.lastSet[i] = now - RESET_AFTER + RESET_AFTER / METERS * (i - 1)
	end
end

-- db: WoWBridgeDB (keeps .mail = { proc, next }); onPacket(pkt) for every packet, in slot order
function M.Start(db, onPacket)
	db.mail = db.mail or { next = 1 }
	st = { phase = "proc", meters = {}, lastSet = {}, db = db, onPacket = onPacket, t0 = GetTime() }
	for i = 1, METERS do st.meters[i] = Font.Meter() end
	Font.Load(st.meters[1], DIR .. "proc.ttf")
	st.lastSet[1] = GetTime()
end

function M.Next() return st and st.slot or (WoWBridgeDB and WoWBridgeDB.mail and WoWBridgeDB.mail.next) or 1 end
function M.Proc() return st and st.proc or "?" end
function M.Phase() return st and st.phase or "off" end

-- the last n slots skipped as damaged in this game process, oldest first, as "k1,k2"; nil when none
function M.Skipped(n)
	local list = st and st.db.mail.skipped
	if not list or #list == 0 then return nil end
	local out = {}
	for i = math.max(1, #list - n + 1), #list do out[#out + 1] = list[i] end
	return table.concat(out, ",")
end

local function Skip(k)
	local list = st.db.mail.skipped or {}
	list[#list + 1] = k
	while #list > SKIPPED_KEPT do table.remove(list, 1) end
	st.db.mail.skipped = list
end

function M.Tick()
	if not st then return end
	local now = GetTime()
	if st.phase == "proc" then
		local stamp = Font.Packet(st.meters[1])
		if stamp or now - st.t0 > 5 then
			st.proc = stamp or "?"
			-- a stamp unlike the saved one: the game was restarted, every slot is unread again. One read after a stamp that
			-- could not be read ("?": no companion had written proc.ttf before the login) is taken as it is: most likely the
			-- same process, whose slots read so far sit in the client's font cache, and from slot 1 they would all come back
			if stamp and stamp ~= st.db.mail.proc then
				if st.db.mail.proc ~= "?" then st.db.mail.next, st.db.mail.skipped = 1, nil end
				st.db.mail.proc = stamp
			elseif not stamp then
				st.db.mail.proc = "?"
			end
			st.phase = "poll"
			Want(st.db.mail.next)
		elseif now - st.lastSet[1] >= RESET_AFTER then
			Font.Load(st.meters[1], DIR .. "proc.ttf")
			st.lastSet[1] = now
		end
		return
	end
	if st.phase ~= "poll" then return end
	local k = st.slot
	for i, meter in ipairs(st.meters) do
		-- "WF" envelope (4 bytes), then "WM", version, flags, slot: until slot k has loaded, a FontString still shows an
		-- earlier font (or the stock one), so look at the slot number before reading everything
		local head = Font.Head(meter, 12)
		if head and head[5] == 87 and head[6] == 77 and ((head[9] * 256 + head[10]) * 256 + head[11]) * 256 + head[12] == k then
			local s = Font.Packet(meter)
			local pkt = s and M.Parse(s)
			if pkt and pkt.slot == k then
				st.db.mail.next = k + 1
				if k + 1 <= M.POOL then
					Want(k + 1)
				else                      -- all read: say slot POOL + 1, so the companion does not think the last one is lost
					st.phase, st.slot = "full", k + 1
				end
				st.onPacket(pkt)
				return
			end
			st.bad = st.bad + 1
			if st.bad >= BAD_LIMIT then   -- a damaged packet: skip it rather than stall the mailbox
				Skip(k)
				st.db.mail.next = k + 1
				Want(k + 1)
			end
			return
		end
	end
	for i, meter in ipairs(st.meters) do
		if now - st.lastSet[i] >= RESET_AFTER then
			Font.Load(meter, SlotPath(k))
			st.lastSet[i] = now
		end
	end
end
