-- JSON text of a Lua value, for the data WoWBridge hands the companion (API.lua: events and call answers). The same rules
-- as the probes' encoder (src/wuxianworkshop/agent/probes.py, LUA_JSON), which this client taught: a table is an array when
-- it has a sequence (or is empty), else an object with its keys as strings; a number that is not finite is null, told by
-- its text (on this client NaN equals every number, math.huge too); a whole number within 32 bits goes through %d (the
-- client's %d raises past them), others through %.14g; a string has the quote, the backslash and every byte below 32
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
