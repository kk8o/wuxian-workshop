-- Font return channel (experiment E0f): the companion writes bytes into the advance widths of a TrueType font
-- (src/wuxianworkshop/core/fontpack.py is the reference). A FontString on a pixel-exact frame loads the font and measures one
-- glyph at a time: U+E000 and U+E001 calibrate byte values 0 and 255, data byte k is glyph U+E002 + k. A packet is "WF",
-- the payload length (2 bytes), the payload and the CRC-16 of everything before it.
-- GetStringWidth reports the text's extent up to the last glyph's ink, not the sum of advances: a glyph measured alone
-- gives its ink width (the same for every glyph here), so each glyph is measured followed by a fixed tail glyph, whose
-- position then depends on the measured glyph's advance.
local _, ns = ...
local Codec = ns.Codec
local char, floor, abs = string.char, math.floor, math.abs

local Font = {}
ns.Font = Font
Font.SIZE = 64
Font.MAX_PACKET = 4096

local holder

-- UTF-8 of U+E000 + i
local function Glyph(i)
	local cp = 0xE000 + i
	return char(0xE0 + floor(cp / 4096), 0x80 + floor(cp / 64) % 64, 0x80 + cp % 64)
end

local TAIL = Glyph(0)

local function Holder()
	if not holder then
		holder = CreateFrame("Frame", nil, UIParent)
		holder:SetIgnoreParentScale(true)
		holder:SetSize(1, 1)
		holder:SetPoint("TOPLEFT")
		holder:SetAlpha(0)
	end
	local _, physH = GetPhysicalScreenSize()
	holder:SetScale(768 / physH)   -- one UI unit = one physical pixel, like the bus: size 64 means 64 pixels
	return holder
end

-- after the physical size changed (window resized, resolution, mode): one UI unit must stay one physical pixel, or the
-- glyph widths stop being whole pixels and bytes misread
function Font.Rescale()
	if holder then
		local _, physH = GetPhysicalScreenSize()
		holder:SetScale(768 / physH)
	end
end

-- a FontString to measure with; it starts with a stock font so that SetText works even if no SetFont ever succeeds
function Font.Meter()
	local fs = Holder():CreateFontString(nil, "ARTWORK")
	fs:SetFontObject(GameFontNormal)
	fs:SetPoint("TOPLEFT")
	fs:SetWordWrap(false)
	fs:SetNonSpaceWrap(false)
	return fs
end

-- SetFont, timed. Returns ok (no Lua error), its return value, whether GetFont() names the file afterwards, milliseconds.
function Font.Load(fs, path, size)
	local t0 = debugprofilestop()
	local ok, res = pcall(fs.SetFont, fs, path, size or Font.SIZE, "")
	local ms = debugprofilestop() - t0
	local cur = fs:GetFont()
	local match = type(cur) == "string" and cur:gsub("/", "\\"):lower() == path:lower()
	return ok, (ok and res) and true or false, match, ms
end

-- "<ok><return value><path match>" as digits, e.g. "110"
function Font.LoadFlags(fs, path, size)
	local ok, res, match, ms = Font.Load(fs, path, size)
	return (ok and "1" or "0") .. (res and "1" or "0") .. (match and "1" or "0"), ms
end

local function Width(fs, i)
	if not pcall(fs.SetText, fs, Glyph(i) .. TAIL) then return nil end
	return fs:GetStringWidth()
end

-- glyph i measured alone (its ink width; for the record)
function Font.InkWidth(fs, i)
	if not pcall(fs.SetText, fs, Glyph(i)) then return nil end
	return fs:GetStringWidth()
end

-- (width of one byte step, width of byte 0, width of byte 255); nil first when this is not one of our fonts
function Font.Calibrate(fs)
	local w0, w255 = Width(fs, 0), Width(fs, 1)
	if not (w0 and w255) or w255 - w0 < 25 then return nil, w0, w255 end
	return (w255 - w0) / 255, w0, w255
end

-- data bytes first .. first + n - 1 (0-based), appended to `out`; also the worst distance from a whole step (0 = exact).
-- nil + error code when a glyph does not measure as a byte.
function Font.Bytes(fs, step, w0, first, n, out)
	out = out or {}
	local worst = 0
	for k = first, first + n - 1 do
		local w = Width(fs, 2 + k)
		if not w then return nil, "text" end
		local v = (w - w0) / step
		local r = floor(v + 0.5)
		if r < 0 or r > 255 then return nil, "range" end
		if abs(v - r) > worst then worst = abs(v - r) end
		out[#out + 1] = r
	end
	return out, worst
end

-- the first n data bytes, or nil: a cheap look before reading a whole packet
function Font.Head(fs, n)
	local step, w0 = Font.Calibrate(fs)
	if not step then return nil end
	return (Font.Bytes(fs, step, w0, 0, n))
end

-- the packet in the font the FontString has now: payload string, or nil + error code
function Font.Packet(fs)
	local step, w0 = Font.Calibrate(fs)
	if not step then return nil, "cal" end
	local b, err = Font.Bytes(fs, step, w0, 0, 4)
	if not b then return nil, err end
	if b[1] ~= 87 or b[2] ~= 70 then return nil, "magic" end   -- "WF"
	local len = b[3] * 256 + b[4]
	if len > Font.MAX_PACKET then return nil, "len" end
	b, err = Font.Bytes(fs, step, w0, 4, len + 2, b)
	if not b then return nil, err end
	if Codec.CRC16(b, 1, 4 + len) ~= b[5 + len] * 256 + b[6 + len] then return nil, "crc" end
	local s = {}
	for i = 5, 4 + len do s[#s + 1] = char(b[i]) end
	return table.concat(s)
end
