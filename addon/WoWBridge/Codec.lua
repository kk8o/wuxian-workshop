-- WoWBridge frame codec: CRC-16/CCITT-FALSE, CRC-32 and bit packing.
-- Mirrors src/wuxianworkshop/core/frame.py, which is the reference for the format.
-- Also the squeeze of a long message's blocks (Link.lua, 0.9.9), as src/wuxianworkshop/core/squeeze.py reads it.
local _, ns = ...

local band, bxor, lshift, rshift = bit.band, bit.bxor, bit.lshift, bit.rshift
local floor = math.floor

local Codec = {}
ns.Codec = Codec

Codec.VERSION = 1
Codec.TYPE_DATA, Codec.TYPE_PROBE, Codec.TYPE_CONTROL = 0, 1, 2   -- control: HELLO, HB, PONG (Link.lua)
Codec.HEADER_LEN = 15
Codec.BITS = { [0] = 1, [1] = 3, [2] = 6, [3] = 3 }   -- mode 3: mode 1's cells laid along ∞ (Frame.InfOrder)

function Codec.CRC16(bytes, first, last)
	local crc = 0xFFFF
	for i = first, last do
		crc = bxor(crc, lshift(bytes[i], 8))
		for _ = 1, 8 do
			if band(crc, 0x8000) ~= 0 then
				crc = band(bxor(lshift(crc, 1), 0x1021), 0xFFFF)
			else
				crc = band(lshift(crc, 1), 0xFFFF)
			end
		end
	end
	return crc
end

local CRC32_TABLE = {}
for i = 0, 255 do
	local c = i
	for _ = 1, 8 do
		if band(c, 1) ~= 0 then
			c = bxor(0xEDB88320, rshift(c, 1))
		else
			c = rshift(c, 1)
		end
	end
	CRC32_TABLE[i] = c
end

-- Returns the CRC as a signed 32-bit number (bit library convention); take bytes with band(rshift(crc, n), 0xFF).
function Codec.CRC32(bytes, first, last)
	local crc = 0xFFFFFFFF
	for i = first, last do
		crc = bxor(rshift(crc, 8), CRC32_TABLE[band(bxor(crc, bytes[i]), 0xFF)])
	end
	return bxor(crc, 0xFFFFFFFF)
end

-- bytes -> cell values of `bits` bits each, most significant bit first; the last cell is padded with zeros
function Codec.Pack(bytes, bits)
	local out, acc, n, base = {}, 0, 0, 2 ^ bits
	for i = 1, #bytes do
		acc = acc * 256 + bytes[i]
		n = n + 8
		while n >= bits do
			n = n - bits
			local p = 2 ^ n
			out[#out + 1] = floor(acc / p) % base
			acc = acc % p
		end
	end
	if n > 0 then
		out[#out + 1] = (acc * 2 ^ (bits - n)) % base
	end
	return out
end

-- header (15 bytes) + payload + CRC-32, as a byte array
function Codec.Stream(ftype, W, H, session, msg, part, parts, payload)
	local len = #payload
	local b = { 0x57, 0x42, Codec.VERSION * 16 + ftype, W, H, floor(session / 256) % 256, session % 256,
		floor(msg / 256) % 256, msg % 256, part, parts, floor(len / 256) % 256, len % 256 }
	local c = Codec.CRC16(b, 1, 13)
	b[14], b[15] = floor(c / 256), c % 256
	for i = 1, len do
		b[15 + i] = payload:byte(i)
	end
	local crc = Codec.CRC32(b, 1, 15 + len)
	b[#b + 1] = band(rshift(crc, 24), 0xFF)
	b[#b + 1] = band(rshift(crc, 16), 0xFF)
	b[#b + 1] = band(rshift(crc, 8), 0xFF)
	b[#b + 1] = band(crc, 0xFF)
	return b
end

-- largest payload a frame of this layout carries in this mode, leaving `reserve` data cells free (probe ramps)
function Codec.Capacity(dataCells, mode, reserve)
	local bits = (dataCells - (reserve or 0)) * Codec.BITS[mode]
	return math.max(0, floor((bits - 8 * (Codec.HEADER_LEN + 4)) / 8))
end

-- Squeeze: one block of a message (Codec.SQUEEZE_BLOCK bytes at most) in LZ4's block format: a token (literals << 4 |
-- match length - 4; 15 says more follows: bytes of 255 and a last one below 255), the literals, the match's offset back
-- (2 bytes, low first) and so on, the last token only literals. Greedy, one candidate per 4-byte hash. Byte by byte only:
-- in the client's Lua (5.1, every bit operation a call) that runs at about 0.2-0.3 ms per KB, where a Huffman stage
-- (DEFLATE) would cost several times as much for 30-50 % less. One byte buffer and one hash array serve every block,
-- made when first needed: a slot holds base + position, and base moves past each block, so nothing is cleared and the
-- blocks leave little garbage (a fresh table per block made 50 times the input in garbage).
Codec.SQUEEZE_BLOCK = 4096
local SQUEEZE_HASH = 4096
local bytes, seen, base

local function Run(out, o, r)                  -- a length past 15: bytes of 255, then the rest
	if r >= 255 then
		o = o + 1
		out[o] = string.rep("\255", floor(r / 255))
		r = r % 255
	end
	o = o + 1
	out[o] = string.char(r)
	return o
end

function Codec.Squeeze(s)
	local n, char, byte = #s, string.char, string.byte
	if not seen then
		bytes, seen, base = {}, {}, 0
		for k = 1, SQUEEZE_HASH do seen[k] = 0 end
	end
	local b, k = bytes, 1
	while k + 7 <= n do                          -- into the same table, 8 at a time
		b[k], b[k + 1], b[k + 2], b[k + 3], b[k + 4], b[k + 5], b[k + 6], b[k + 7] = byte(s, k, k + 7)
		k = k + 8
	end
	while k <= n do
		b[k] = byte(s, k)
		k = k + 1
	end
	local out, o, i, anchor = {}, 0, 1, 1
	while i <= n - 3 do
		local b1, b2, b3, b4 = b[i], b[i + 1], b[i + 2], b[i + 3]
		local h = (b1 * 7919 + b2 * 263 + b3 * 31 + b4) % SQUEEZE_HASH + 1
		local cand = seen[h] - base
		seen[h] = base + i
		if cand >= 1 and b[cand] == b1 and b[cand + 1] == b2 and b[cand + 2] == b3 and b[cand + 3] == b4 then
			local m = 4
			while i + m <= n and b[cand + m] == b[i + m] do m = m + 1 end
			local lit, ml = i - anchor, m - 4
			o = o + 1
			out[o] = char((lit < 15 and lit or 15) * 16 + (ml < 15 and ml or 15))
			if lit >= 15 then o = Run(out, o, lit - 15) end
			if lit > 0 then
				o = o + 1
				out[o] = s:sub(anchor, i - 1)
			end
			local off = i - cand
			o = o + 1
			out[o] = char(off % 256, floor(off / 256))
			if ml >= 15 then o = Run(out, o, ml - 15) end
			i = i + m
			anchor = i
		else
			i = i + 1
		end
	end
	base = base + n + 1
	local lit = n - anchor + 1
	o = o + 1
	out[o] = char((lit < 15 and lit or 15) * 16)
	if lit >= 15 then o = Run(out, o, lit - 15) end
	if lit > 0 then
		o = o + 1
		out[o] = s:sub(anchor)
	end
	return table.concat(out)
end
