-- WoWBridge hot loading. CODE records "<job> <i>/<n>[ <addon> <chunk name>[ reset|unload|reload]]\n<bytes>" carry Lua an
-- agent sends through the companion ("run <lua>", "load <file>"; src/wuxianworkshop/agent/commands.py). With every part
-- of a job in, it runs under that chunk name (error messages name the file), with (addon, the namespace that addon
-- registered in WoWBridgeNS) as "..."; the result goes back typed as a RUN message: "<job> ok <chunk> (<bytes> B, <ms>
-- ms, <n> values)[: "<value>", ...][ (value <i> cut at <kept> of <size> bytes[, <m> more not sent])][ (<note>)...]" or
-- "<job> error <chunk>: <error>\n<stack>". Each returned value is framed by Quote, so that no ", " or note in it, nor a
-- colour code the link strips, blurs where it ends; at most LIMIT bytes of them go (src/wuxianworkshop/daemon/api.py,
-- parse_run, reads it). Jobs that ran are kept in the saved variables, so parts sent again never run one twice. The
-- setting hotLoad (/wb set hotLoad off) refuses them.
-- Lifecycle: a first part flagged "reset" calls WoWBridgeNS[addon].OnUnload() before the code runs (pcall) and
-- OnReload(<what OnUnload returned>) after it; "unload" and "reload" do one half each, so that a whole-addon load calls
-- OnUnload before its first file and OnReload after its last.
local addonName, ns = ...

local A = {}
ns.Agent = A
A.stats = { runs = 0, failed = 0, last = nil }   -- this UI session's jobs, for the panel: last = { name, ok, at }

WoWBridgeNS = WoWBridgeNS or {}       -- addon name -> its namespace; an addon registers itself with one line
WoWBridgeNS[addonName] = ns
local jobs = {}
local unloaded = {}                   -- addon -> { value = what OnUnload returned }, until the reload half runs

-- a readable dump of a value: tables down to `depth` levels (50 entries each, keys sorted), widgets as <Type name>,
-- at most `limit` bytes. RUN results use it; agent code can call WoWBridge.Dump(value, depth, limit)
local function Dump(value, depth, limit)
	depth, limit = depth or 3, limit or 4000
	local out, size, path = {}, 0, {}
	local function put(s)
		out[#out + 1] = s
		size = size + #s
		return size < limit
	end
	local function keyText(k)
		if type(k) == "string" and k:match("^[%a_][%w_]*$") then return k end
		return "[" .. (type(k) == "string" and ("%q"):format(k) or tostring(k)) .. "]"
	end
	local function before(a, b)                    -- numbers, then strings, then the rest
		local ta, tb = type(a), type(b)
		if ta ~= tb then return ta == "number" or (ta == "string" and tb ~= "number") end
		if ta == "number" or ta == "string" then return a < b end
		return tostring(a) < tostring(b)
	end
	local function walk(v, level, indent)
		local t = type(v)
		if t == "string" then return put(("%q"):format(v)) end
		if t ~= "table" then return put(tostring(v)) end
		if type(rawget(v, 0)) == "userdata" and type(v.GetObjectType) == "function" then
			local okType, kind = pcall(v.GetObjectType, v)
			local okName, name = pcall(v.GetName, v)
			return put(("<%s %s>"):format(okType and kind or "widget", okName and name or "(no name)"))
		end
		if path[v] then return put("<cycle>") end
		local keys = {}
		for k in pairs(v) do keys[#keys + 1] = k end
		if #keys == 0 then return put("{}") end
		if level >= depth then return put(("{... %d entries}"):format(#keys)) end
		path[v] = true
		table.sort(keys, before)
		if not put("{\n") then return false end
		local inner = indent .. "  "
		for n, k in ipairs(keys) do
			if n > 50 then
				put(("%s... %d more\n"):format(inner, #keys - 50))
				break
			end
			if not (put(inner .. keyText(k) .. " = ") and walk(v[k], level + 1, inner) and put(",\n")) then return false end
		end
		path[v] = nil
		return put(indent .. "}")
	end
	walk(value, 0, "")
	local s = table.concat(out)
	if #s > limit then s = s:sub(1, limit) .. (" ...(cut at %d bytes)"):format(limit) end
	return s
end
A.Dump = Dump

local function Pack(...) return { n = select("#", ...), ... } end

local LIMIT = 4000                    -- bytes of returned values a RUN result carries

-- a returned value for the RUN result: in double quotes, with \\, \" and \ddd for "|" and the control bytes but newline
-- and tab (a dump stays readable in the log)
local ESCAPE = { ['"'] = '\\"', ["\\"] = "\\\\", ["|"] = "\\124", ["\127"] = "\\127" }
for b = 0, 31 do
	if b ~= 9 and b ~= 10 then ESCAPE[string.char(b)] = ("\\%03d"):format(b) end
end
local function Quote(s)
	return '"' .. (s:gsub('[%z\1-\31\127"\\|]', ESCAPE)) .. '"'
end

-- the first `size` bytes of s, or up to 3 fewer: between two UTF-8 characters
local function Fit(s, size)
	for _ = 1, 3 do
		local b = s:byte(size + 1)
		if size == 0 or not b or b < 128 or b >= 192 then break end
		size = size - 1
	end
	return s:sub(1, size)
end

local function Traceback(e)
	local stack = debugstack and debugstack(2) or ""
	stack = stack:match("^(.-)%[C%]: in function 'xpcall'") or stack   -- the job's own frames, not the loader's
	return tostring(e) .. "\n" .. stack:gsub("%[tail call%]: %?\n", ""):sub(1, 1500)
end

local function Result(text)
	ns.Link.Send(text, "run")
end

local function Count(name, ok)
	local stats = A.stats
	stats.runs = stats.runs + 1
	if not ok then stats.failed = stats.failed + 1 end
	stats.last = { name = name, ok = ok, at = GetTime() }
end

-- the lifecycle hooks of the addon a job belongs to; each returns a note for the RUN result ("" when there is no hook)
local function Unload(addon, space)
	if not (space and type(space.OnUnload) == "function") then return "" end
	local ok, value = pcall(space.OnUnload)
	unloaded[addon] = { value = ok and value or nil }
	return ok and " (OnUnload ok)" or (" (OnUnload error: %s)"):format(tostring(value))
end

local function Reload(addon, space)
	local kept = unloaded[addon]
	unloaded[addon] = nil
	if not (space and type(space.OnReload) == "function") then return "" end
	local ok, err = pcall(space.OnReload, kept and kept.value)
	return ok and " (OnReload ok)" or (" (OnReload error: %s)"):format(tostring(err))
end

local function RunJob(id, job)
	jobs[id] = nil
	local db = ns.db
	db.codeDone = db.codeDone or {}
	table.insert(db.codeDone, id)
	if #db.codeDone > 50 then table.remove(db.codeDone, 1) end
	local code, short = table.concat(job.parts), job.name:gsub("^@Interface[/\\]AddOns[/\\]", "")
	if ns.Setting("hotLoad") == false then
		Result(("%s error %s: hot loading is off (/wb set hotLoad on)"):format(id, job.name))
		Count(job.name, false)
		return
	end
	local f, err = loadstring(code, job.name)
	local t0, res, note = debugprofilestop(), nil, ""
	if f then
		local space = WoWBridgeNS[job.addon]
		if job.addon ~= "-" and not space then          -- one new table for the addon, shared by all its files from now on
			space, note = {}, (" (no namespace registered for %s: it got a new one, kept for its later loads)"):format(job.addon)
			WoWBridgeNS[job.addon] = space
		end
		if job.flag == "reset" or job.flag == "unload" then note = note .. Unload(job.addon, space) end
		if job.addon == "-" then
			res = Pack(xpcall(function() return f() end, Traceback))
		else
			res = Pack(xpcall(function() return f(job.addon, space) end, Traceback))
		end
		if job.flag == "reset" or job.flag == "reload" then note = note .. Reload(job.addon, space) end
	end
	local ms = debugprofilestop() - t0
	local values, failure = "", nil
	if not f then
		failure = tostring(err)
		Result(("%s error %s: %s"):format(id, job.name, failure))
	elseif res[1] then
		local out, framed, room, cut = {}, {}, LIMIT, ""
		for k = 2, res.n do
			local v = type(res[k]) == "table" and Dump(res[k]) or tostring(res[k])
			if #v > room then                         -- this one cut, the ones after it left out
				local size = #v
				v = Fit(v, room)
				cut = (" (value %d cut at %d of %d bytes%s)"):format(k - 1, #v, size,
					k < res.n and (", %d more not sent"):format(res.n - k) or "")
			end
			room = room - #v
			out[#out + 1], framed[#framed + 1] = v, Quote(v)
			if cut ~= "" then break end
		end
		values = table.concat(out, ", ")
		Result(("%s ok %s (%d B, %.1f ms, %d value%s)%s%s%s"):format(id, job.name, #code, ms, res.n - 1,
			res.n == 2 and "" or "s", #framed > 0 and (": " .. table.concat(framed, ", ")) or "", cut, note))
	else
		failure = tostring(res[2])
		Result(("%s error %s: %s%s"):format(id, job.name, failure, note))
	end
	local ok = failure == nil
	if job.name == "=probe" then return end              -- the app's own look into the game, not the agent's code
	Count(job.name, ok)
	pcall(ns.Console.Job, job.name, job.addon, job.flag, ok, ms, values, failure and (failure .. note) or nil)
	if ns.Setting("toasts") == false then                 -- no notice on the screen: the chat says it, as before
		ns.Print(ns.L.AGENT_CODE:format(short, #code, ok and ns.L.AGENT_OK or ns.L.AGENT_ERROR))
	end
end

local FLAGS = { reset = true, unload = true, reload = true }

-- a CODE record (Link.lua hands them over)
function A.Code(data)
	local head, body = data:match("^([^\n]*)\n(.*)$")
	local id, i, n, rest = (head or ""):match("^(%d+) (%d+)/(%d+) ?(.*)$")
	i, n = tonumber(i), tonumber(n)
	if not id or i < 1 or i > n then return end
	for _, done in ipairs(ns.db.codeDone or {}) do
		if done == id then return end
	end
	local job = jobs[id]
	if not job then
		job = { n = n, parts = {}, got = 0 }
		jobs[id] = job
	end
	if i == 1 then
		local addon, name = rest:match("^(%S+) (.+)$")
		if name then
			local flag = name:match(" (%a+)$")
			if flag and FLAGS[flag] then
				name = name:sub(1, -(#flag + 2))
			else
				flag = nil
			end
			job.addon, job.name, job.flag = addon, name, flag
		end
	end
	if not job.parts[i] then job.parts[i], job.got = body, job.got + 1 end
	if job.got == job.n and job.name then RunJob(id, job) end
end
