-- WoWBridge inflate: raw DEFLATE (RFC 1951) decoding for the CODE and CALL jobs the companion sends compressed (link
-- features 3, 0.9.9: zlib's deflate with wbits -15, src/wuxianworkshop/agent/commands.py). Decode only, in plain
-- arithmetic (no bit library): the bit buffer is a number below 2^32, bits come off it with % and / by powers of two.
-- Each Huffman code gets a lookup table of its next LBITS bits; a longer code is decoded bit by bit over the canonical
-- counts, as zlib's puff.c does. tests/addon/test_inflate.py checks it against zlib's own output.
local _, ns = ...
local byte, char, concat, unpack = string.byte, string.char, table.concat, unpack
local min = math.min

local Inflate = {}
ns.Inflate = Inflate

local P2 = {}
for k = 0, 31 do P2[k] = 2 ^ k end
local LBITS = 9
local LSIZE = P2[LBITS]

-- length symbols 257..285 (1..29 here) and distance symbols 0..29 (1..30 here): base and extra bits
local LBASE = { 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227,
	258 }
local LEXT = { 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0 }
local DBASE = { 1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097,
	6145, 8193, 12289, 16385, 24577 }
local DEXT = { 0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13 }
local ORDER = { 16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15 }   -- the code length code's lengths

-- a Huffman code from the code lengths lens[0 .. n - 1]: its canonical counts and symbols (puff.c), and a lookup table
-- indexed by the next LBITS bits of the stream. The stream gives a code's bits most significant first into a buffer that
-- fills from the least significant end, so each code goes in bit-reversed, at every index its low bits match. In a
-- prefix code no shorter code can stand where a longer one's first LBITS bits are, so those find no entry
local function Build(lens, n)
	local count, offs, symbol, lut, len = {}, {}, {}, {}, {}
	for l = 0, 15 do count[l] = 0 end
	for s = 0, n - 1 do count[lens[s]] = count[lens[s]] + 1 end
	local left = 1
	for l = 1, 15 do
		left = left * 2 - count[l]
		if left < 0 then error("an over-subscribed Huffman code", 0) end
	end
	offs[1] = 0
	for l = 1, 14 do offs[l + 1] = offs[l] + count[l] end
	for s = 0, n - 1 do
		local l = lens[s]
		if l ~= 0 then
			symbol[offs[l]] = s
			offs[l] = offs[l] + 1
		end
	end
	local code, index = 0, 0
	for l = 1, LBITS do
		local step = P2[l]
		for _ = 1, count[l] do
			local r, c = 0, code
			for _ = 1, l do
				local b = c % 2
				r, c = r * 2 + b, (c - b) / 2
			end
			local s = symbol[index]
			for j = r, LSIZE - 1, step do lut[j], len[j] = s, l end
			index, code = index + 1, code + 1
		end
		code = code * 2
	end
	return { count = count, symbol = symbol, lut = lut, len = len }
end

local fixedLen, fixedDist      -- the fixed codes of block type 1, built when first needed

local function Fixed()
	if not fixedLen then
		local l, d = {}, {}
		for s = 0, 143 do l[s] = 8 end
		for s = 144, 255 do l[s] = 9 end
		for s = 256, 279 do l[s] = 7 end
		for s = 280, 287 do l[s] = 8 end
		for s = 0, 29 do d[s] = 5 end
		fixedLen, fixedDist = Build(l, 288), Build(d, 30)
	end
	return fixedLen, fixedDist
end

-- the bytes a raw DEFLATE stream holds. Raises an error (a string) when the stream is damaged or ends early, or when it
-- would come out longer than `most` bytes
function Inflate.Raw(s, most)
	most = most or math.huge
	local pos, size = 1, #s
	local buf, cnt = 0, 0          -- bits read but not used: cnt of them, the next one the least significant
	local out, n = {}, 0

	local function need(k)         -- k bits in the buffer, or as many as the input has left
		while cnt < k and pos <= size do
			buf = buf + byte(s, pos) * P2[cnt]
			pos, cnt = pos + 1, cnt + 8
		end
	end

	local function bits(k)
		if cnt < k then
			need(k)
			if cnt < k then error("the stream ends early", 0) end
		end
		local p = P2[k]
		local v = buf % p
		buf, cnt = (buf - v) / p, cnt - k
		return v
	end

	local function decode(h)
		if cnt < LBITS then need(LBITS) end
		local i = buf % LSIZE
		local l = h.len[i]
		if l and l <= cnt then
			local p = P2[l]
			buf, cnt = (buf - buf % p) / p, cnt - l
			return h.lut[i]
		end
		local count, symbol = h.count, h.symbol    -- longer than LBITS bits, or the stream's last bits: one bit at a time
		local code, first, index = 0, 0, 0
		for k = 1, 15 do
			code = code + bits(1)
			local c = count[k]
			if code - c < first then return symbol[index + code - first] end
			index, first, code = index + c, (first + c) * 2, code * 2
		end
		error("a code the block's table does not have", 0)
	end

	local function dynamic()       -- the codes a block of type 2 brings
		local nlen, ndist, ncode = bits(5) + 257, bits(5) + 1, bits(4) + 4
		if nlen > 286 or ndist > 30 then error("too many codes in a block header", 0) end
		local lens = {}
		for k = 1, 19 do lens[ORDER[k]] = 0 end
		for k = 1, ncode do lens[ORDER[k]] = bits(3) end
		local lencode = Build(lens, 19)
		local all, k, total = {}, 0, nlen + ndist
		while k < total do
			local sym = decode(lencode)
			if sym < 16 then
				all[k], k = sym, k + 1
			else
				local v, rep = 0, 0
				if sym == 16 then
					if k == 0 then error("a repeat with no length before it", 0) end
					v, rep = all[k - 1], 3 + bits(2)
				elseif sym == 17 then
					rep = 3 + bits(3)
				else
					rep = 11 + bits(7)
				end
				if k + rep > total then error("too many code lengths", 0) end
				for _ = 1, rep do all[k], k = v, k + 1 end
			end
		end
		if all[256] == 0 then error("a block without an end-of-block code", 0) end
		local ll, dl = {}, {}
		for j = 0, nlen - 1 do ll[j] = all[j] end
		for j = 0, ndist - 1 do dl[j] = all[nlen + j] end
		return Build(ll, nlen), Build(dl, ndist)
	end

	repeat
		local last, kind = bits(1), bits(2)
		if kind == 0 then                          -- stored: to the byte boundary, LEN, NLEN, then LEN bytes as they are
			bits(cnt % 8)
			local len = bits(16)
			if bits(16) ~= 65535 - len then error("a stored block's length does not check", 0) end
			if n + len > most then error("the data comes out longer than said", 0) end
			while len > 0 and cnt >= 8 do         -- whole bytes the buffer still holds
				n, len = n + 1, len - 1
				out[n] = bits(8)
			end
			if pos + len - 1 > size then error("the stream ends early", 0) end
			for j = pos, pos + len - 1 do
				n = n + 1
				out[n] = byte(s, j)
			end
			pos = pos + len
		elseif kind == 1 or kind == 2 then
			local lcode, dcode
			if kind == 1 then lcode, dcode = Fixed() else lcode, dcode = dynamic() end
			while true do
				local sym = decode(lcode)
				if sym < 256 then
					n = n + 1
					if n > most then error("the data comes out longer than said", 0) end
					out[n] = sym
				elseif sym == 256 then
					break
				else
					sym = sym - 256
					if sym > 29 then error("a length code out of range", 0) end
					local length = LBASE[sym] + bits(LEXT[sym])
					local d = decode(dcode) + 1
					if d > 30 then error("a distance code out of range", 0) end
					local dist = DBASE[d] + bits(DEXT[d])
					if dist > n then error("a distance back past the start", 0) end
					if n + length > most then error("the data comes out longer than said", 0) end
					local from = n - dist
					for j = 1, length do out[n + j] = out[from + j] end
					n = n + length
				end
			end
		else
			error("a block of an unknown type", 0)
		end
	until last == 1

	local pieces, k = {}, 1
	while k <= n do
		local j = min(n, k + 4095)
		pieces[#pieces + 1] = char(unpack(out, k, j))
		k = j + 1
	end
	return concat(pieces)
end

-- the Adler-32 of a string (zlib's adler32), as the companion puts it in a compressed job's first part
function Inflate.Adler32(s)
	local a, b, i, size = 1, 0, 1, #s
	while i <= size do
		local j = min(size, i + 3999)          -- the sums stay far below 2^53 before they are reduced
		for k = i, j do
			a = a + byte(s, k)
			b = b + a
		end
		a, b = a % 65521, b % 65521
		i = j + 1
	end
	return b * 65536 + a
end
