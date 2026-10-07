-- WoWBridge frame codec: CRC-16/CCITT-FALSE, CRC-32 and bit packing.
-- Mirrors src/wuxianworkshop/core/frame.py, which is the reference for the format.
local _, ns = ...

local band, bxor, lshift, rshift = bit.band, bit.bxor, bit.lshift, bit.rshift
local floor = math.floor

local Codec = {}
ns.Codec = Codec

Codec.VERSION = 1
Codec.TYPE_DATA, Codec.TYPE_PROBE, Codec.TYPE_CONTROL = 0, 1, 2   -- control: HELLO, HB, PONG (Link.lua)
Codec.HEADER_LEN = 15
Codec.BITS = { [0] = 1, [1] = 3, [2] = 6 }

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
