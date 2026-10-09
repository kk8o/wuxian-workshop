-- JSON text of a Lua value, for the data WoWBridge hands the companion (API.lua: events and call answers), and the value
-- of a JSON text the companion sends (Json.Decode, below: the data calls of Agent.lua). Encode follows the same rules
-- as the probes' encoder (src/wuxianworkshop/agent/probes.py, LUA_JSON), which this client taught: a table is an array when
-- it has a sequence (or is empty), else an object with its keys as strings; a number that is not finite is null, told by
-- its text (on this client NaN equals every number, math.huge too); a whole number within 32 bits goes through %d (the
-- client's %d raises past them), one up to 2^53 through %.0f (every digit: a call's arguments come back as they went),
-- others through %.14g; a string has the quote, the backslash and every byte below 32
-- escaped; a secret value is "<secret>", a frame "<Type name>", a function or other thing its tostring; past `depth`
-- levels a table is "<table>".
local _, ns = ...

local Json = {}
ns.Json = Json

local format, floor, abs = string.format, math.floor, math.abs

local function Escape(c)
	return format("\\u%04x", c:byte())
end

local function Encode(v, depth)
	if issecretvalue and issecretvalue(v) then return '"<secret>"' end
	local t = type(v)
	if t == "nil" then return "null" end
	if t == "boolean" then return v and "true" or "false" end
	if t == "number" then
		if not format("%.4f", v):find("^%-?%d+%.%d+$") then return "null" end     -- inf, -inf, nan, -nan(ind)
		if v == floor(v) and abs(v) < 2147483648 then return format("%d", v) end
		if v == floor(v) and abs(v) <= 9007199254740992 then return format("%.0f", v) end    -- exact up to 2^53
		return format("%.14g", v)
	end
	if t == "string" then
		return '"' .. v:gsub('[%z\1-\31"\\]', Escape) .. '"'
	end
	if t == "table" then
		if type(rawget(v, 0)) == "userdata" and type(v.GetObjectType) == "function" then   -- a frame or a region
			local okType, kind = pcall(v.GetObjectType, v)
			local okName, name = pcall(v.GetName, v)
			return Encode(("<%s %s>"):format(okType and kind or "widget", okName and name or "(no name)"))
		end
		if depth <= 0 then return '"<table>"' end
		local out, n = {}, #v
		if n > 0 or next(v) == nil then
			for i = 1, n do out[i] = Encode(v[i], depth - 1) end
			return "[" .. table.concat(out, ",") .. "]"
		end
		for k, x in pairs(v) do
			out[#out + 1] = Encode(tostring(k), 0) .. ":" .. Encode(x, depth - 1)
		end
		return "{" .. table.concat(out, ",") .. "}"
	end
	local ok, s = pcall(tostring, v)
	return Encode(ok and s or ("<" .. t .. ">"), 0)
end

-- (value, depth): the JSON text; depth defaults to 6 levels
function Json.Encode(v, depth)
	return Encode(v, depth or 6)
end

-- The other way, for what the companion sends as data (Agent.lua's data calls): an object is a table with string keys,
-- an array a sequence (a null in it leaves a hole), a string with its escapes undone (\u as UTF-8, a surrogate pair as one
-- character, a lone surrogate as U+FFFD), a number through tonumber, true / false, null nil. At most DEPTH levels.
local DEPTH = 32
local ESC = { ['"'] = '"', ["\\"] = "\\", ["/"] = "/", b = "\b", f = "\f", n = "\n", r = "\r", t = "\t" }
local char = string.char

local function Utf8(c)
	if c < 0x80 then return char(c) end
	if c < 0x800 then return char(0xC0 + floor(c / 64), 0x80 + c % 64) end
	if c < 0x10000 then return char(0xE0 + floor(c / 4096), 0x80 + floor(c / 64) % 64, 0x80 + c % 64) end
	return char(0xF0 + floor(c / 262144), 0x80 + floor(c / 4096) % 64, 0x80 + floor(c / 64) % 64, 0x80 + c % 64)
end

local function Decode(s)
	local pos = 1
	local function fail(what) error({ json = ("%s at byte %d of %d"):format(what, pos, #s) }, 0) end
	local function blank() pos = s:find("[^ \t\r\n]", pos) or #s + 1 end
	local value

	local function text()                               -- pos is on the opening quote
		local out, i = {}, pos + 1
		while true do
			local a = s:find('["\\%z\1-\31]', i)
			if not a then fail("a string with no end") end
			out[#out + 1] = s:sub(i, a - 1)
			local c = s:sub(a, a)
			if c == '"' then
				pos = a + 1
				return table.concat(out)
			elseif c ~= "\\" then
				pos = a
				fail("a control byte in a string")
			end
			local e = s:sub(a + 1, a + 1)
			if e == "u" then
				local hex = s:match("^%x%x%x%x", a + 2)
				if not hex then pos = a; fail("a \\u escape without four hex digits") end
				local c1 = tonumber(hex, 16)
				i = a + 6
				if c1 >= 0xD800 and c1 <= 0xDBFF then         -- the high half of a pair: the low half must follow
					local lo = s:match("^\\u(%x%x%x%x)", i)
					local c2 = lo and tonumber(lo, 16)
					if c2 and c2 >= 0xDC00 and c2 <= 0xDFFF then
						c1, i = 0x10000 + (c1 - 0xD800) * 1024 + (c2 - 0xDC00), i + 6
					else
						c1 = 0xFFFD
					end
				elseif c1 >= 0xDC00 and c1 <= 0xDFFF then
					c1 = 0xFFFD
				end
				out[#out + 1] = Utf8(c1)
			elseif ESC[e] then
				out[#out + 1], i = ESC[e], a + 2
			else
				pos = a
				fail("an unknown escape")
			end
		end
	end

	local function number()
		local a, b = s:find("^-?%d+", pos)
		if not a then fail("not a JSON value") end
		local _, f = s:find("^%.%d+", b + 1)
		b = f or b
		local _, x = s:find("^[eE][-+]?%d+", b + 1)
		b = x or b
		local n = tonumber(s:sub(a, b))
		pos = b + 1
		return n
	end

	function value(depth)
		blank()
		local c = s:sub(pos, pos)
		if c == "{" or c == "[" then
			if depth >= DEPTH then fail(("more than %d levels"):format(DEPTH)) end
			local t, close = {}, c == "{" and "}" or "]"
			pos = pos + 1
			blank()
			if s:sub(pos, pos) == close then
				pos = pos + 1
				return t
			end
			local n = 0
			while true do
				if c == "{" then
					blank()
					if s:sub(pos, pos) ~= '"' then fail("an object key that is not a string") end
					local k = text()
					blank()
					if s:sub(pos, pos) ~= ":" then fail("no colon after an object key") end
					pos = pos + 1
					t[k] = value(depth + 1)
				else
					n = n + 1
					t[n] = value(depth + 1)
				end
				blank()
				local d = s:sub(pos, pos)
				pos = pos + 1
				if d == close then return t end
				if d ~= "," then pos = pos - 1; fail("no comma or " .. close) end
			end
		elseif c == '"' then
			return text()
		elseif s:sub(pos, pos + 3) == "true" then
			pos = pos + 4
			return true
		elseif s:sub(pos, pos + 4) == "false" then
			pos = pos + 5
			return false
		elseif s:sub(pos, pos + 3) == "null" then
			pos = pos + 4
			return nil
		end
		return number()
	end

	local v = value(0)
	blank()
	if pos <= #s then fail("text after the value") end
	return v
end

-- (text): its value, or nil + what is wrong and where (a JSON null is nil with no reason)
function Json.Decode(s)
	if type(s) ~= "string" then return nil, "not a string" end
	local ok, v = pcall(Decode, s)
	if ok then return v end
	return nil, type(v) == "table" and v.json or tostring(v)
end
