-- WoWBridge hot loading. CODE records "<job> <i>/<n>[ <addon> <chunk name>[ reset|unload|reload]]\n<bytes>" carry Lua an
-- agent sends through the companion ("run <lua>", "load <file>"; src/wuxianworkshop/agent/commands.py). With every part
-- of a job in, it runs under that chunk name (error messages name the file), with (addon, the namespace that addon
-- registered in WoWBridgeNS) as "..."; the result goes back typed as a RUN message: "<job> ok <chunk> (<bytes> B, <ms>
-- ms, <n> values)[: "<value>", ...][ (value <i> cut at <kept> of <size> bytes[, <m> more not sent])][ (<note>)...]" or
-- "<job> error <chunk>: <error>\n<stack>". Each returned value is framed by Quote, so that no ", " or note in it, nor a
-- colour code the link strips, blurs where it ends; at most LIMIT bytes of them go (src/wuxianworkshop/daemon/api.py,
-- parse_run, reads it). Jobs that ran are kept in the saved variables (the last DONE_KEPT), so parts sent again never run
-- one twice; a job older than STALE by the PC's clock (the id is the companion's clock in ms, mod 1e10) is not run at all:
-- its packet waited in the mailbox (a game restart, a slot read again), and nobody waits for it any more. A secret value
-- or one whose tostring raises comes back as "<secret>" / "<type: ...>", and a result that cannot be put into words still
-- goes back as an error: the agent always gets an answer. The setting hotLoad (/wb set hotLoad off) refuses them (not
-- the data calls of CALL records, A.Call below: those carry no code).
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
local DONE_KEPT = 500                 -- job ids kept as run
local STALE = 900000                  -- ms: a job older than this is not run

local function Secret(v)
	return issecretvalue ~= nil and issecretvalue(v)
end

-- tostring that never raises: a secret value, or a __tostring that errors, says what it is
local function Text(v)
	if Secret(v) then return "<secret>" end
	local ok, s = pcall(tostring, v)
	if ok and type(s) == "string" then return s end
	return ("<%s: tostring failed>"):format(type(v))
end

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
		if Secret(k) then return "[<secret>]" end
		if type(k) == "string" and k:match("^[%a_][%w_]*$") then return k end
		return "[" .. (type(k) == "string" and ("%q"):format(k) or Text(k)) .. "]"
	end
	local function before(a, b)                    -- numbers, then strings, then the rest
		if Secret(a) or Secret(b) then return Secret(b) and not Secret(a) end
		local ta, tb = type(a), type(b)
		if ta ~= tb then return ta == "number" or (ta == "string" and tb ~= "number") end
		if ta == "number" or ta == "string" then return a < b end
		return Text(a) < Text(b)
	end
	local function walk(v, level, indent)
		if Secret(v) then return put("<secret>") end
		local t = type(v)
		if t == "string" then return put(("%q"):format(v)) end
		if t ~= "table" then return put(Text(v)) end
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
	return Text(e) .. "\n" .. stack:gsub("%[tail call%]: %?\n", ""):sub(1, 1500)
end

-- the values of a RUN result: (as the debug window shows them, framed for the result, the note on a cut, how many framed)
local function Values(res)
	local out, framed, room, cut = {}, {}, LIMIT, ""
	for k = 2, res.n do
		local x = res[k]
		local v = (not Secret(x) and type(x) == "table") and Dump(x) or Text(x)
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
	return table.concat(out, ", "), table.concat(framed, ", "), cut, #framed
end

-- the job ids that ran, kept in the saved variables (the last DONE_KEPT), looked up through a set
local done

local function Done(id)
	if not done then
		done = {}
		for _, d in ipairs(ns.db.codeDone or {}) do done[d] = true end
	end
	return done[id]
end

local function MarkDone(id)
	Done(id)
	local list = ns.db.codeDone or {}
	ns.db.codeDone = list
	list[#list + 1] = id
	done[id] = true
	while #list > DONE_KEPT do done[table.remove(list, 1)] = nil end
end

-- a job older than STALE by this PC's clock (its id is the companion's clock in ms, mod 1e10); one a little in the
-- future is a clock set back, not an old job
local function Stale(id)
	local age = (time() * 1000 - tonumber(id)) % 1e10
	return age > STALE and age < 1e10 - 3600000
end

-- the newest job that ran (the HELLO says it: after a /reload the companion knows which of its jobs ran before it)
function A.LastJob()
	local list = ns.db and ns.db.codeDone
	return list and list[#list] or "0"
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
	return ok and " (OnUnload ok)" or (" (OnUnload error: %s)"):format(Text(value))
end

local function Reload(addon, space)
	local kept = unloaded[addon]
	unloaded[addon] = nil
	if not (space and type(space.OnReload) == "function") then return "" end
	local ok, err = pcall(space.OnReload, kept and kept.value)
	return ok and " (OnReload ok)" or (" (OnReload error: %s)"):format(Text(err))
end

local function RunJob(id, job)
	jobs[id] = nil
	MarkDone(id)
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
		local fine, shown, framed, cut, n = pcall(Values, res)
		if fine then
			values = shown
			Result(("%s ok %s (%d B, %.1f ms, %d value%s)%s%s%s"):format(id, job.name, #code, ms, res.n - 1,
				res.n == 2 and "" or "s", n > 0 and (": " .. framed) or "", cut, note))
		else
			failure = "it ran, but its values could not be put into words: " .. Text(shown)
			Result(("%s error %s: %s%s"):format(id, job.name, failure, note))
		end
	else
		failure = Text(res[2])
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
	if not id or i < 1 or i > n or Done(id) or Stale(id) then return end
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

-- Data calls (link features 2, 0.9.7): a CALL record "<job> <i>/<n>[ <verb>]\n<JSON>", in parts as a CODE job's, carries
-- what the agent asks of an addon's API (API.lua) as data: "call" {"a": addon, "n": name, "d": args} runs a function the
-- addon exposed, "reply" {"r": request id, "d": data} answers its request, "describe" {"a": addon or null} says what
-- addons expose, "piece" {"k": job, "i": n} brings piece n of a long answer. WoWBridge reads them itself and compiles
-- nothing: they work with hot loading off, and the addon's function runs as any addon code does, without the taint the
-- client gives code loaded at run time. The answer goes back as a RUN result whose one value is "<i>/<n>\n<piece i>",
-- as a probe's does (src/wuxianworkshop/agent/probes.py): PIECE bytes a piece, the rest kept ANSWER_KEPT seconds.
local PIECE, ANSWER_MOST, ANSWER_KEPT = 3900, 256 * 1024, 300
local CHUNK = { call = "=call", reply = "=respond", describe = "=describe", piece = "=piece" }
local answers = {}                    -- job id -> { s, cuts, left, at }: answers longer than a piece

-- an answer cut into pieces between two UTF-8 characters: (the text, the end of each piece)
local function Pieces(s)
	if #s > ANSWER_MOST then
		s = ns.Json.Encode({ error = ("the answer is %d KB, more than the %d KB a call brings back"):format(
			math.ceil(#s / 1024), ANSWER_MOST / 1024) })
	end
	local cuts, from = {}, 1
	repeat
		local to = math.min(#s, from + PIECE - 1)
		for _ = 1, 3 do                       -- between two UTF-8 characters: the next byte starts one
			local b = s:byte(to + 1)
			if not b or b < 128 or b >= 192 then break end
			to = to - 1
		end
		cuts[#cuts + 1] = to
		from = to + 1
	until from > #s
	return s, cuts
end

-- (the answer's text, or nil + why not) for a data call
local function Answer(verb, req)
	local WB = _G.WoWBridge
	if verb == "call" then return WB.Call(req.a, req.n, req.d) end
	if verb == "reply" then return WB.Reply(req.r, req.d) end
	if verb == "describe" then return WB.Describe(req.a) end
	if verb ~= "piece" then return nil, ("no data call %q"):format(tostring(verb)) end
	local key, i = tostring(req.k), tonumber(req.i)
	local a = answers[key]
	if not a or not i or i < 2 or not a.cuts[i] then
		return nil, "the rest of this answer is no longer kept in the game (the UI reloaded?)"
	end
	a.left = a.left - 1
	if a.left <= 0 then answers[key] = nil end
	return ("%d/%d\n%s"):format(i, #a.cuts, a.s:sub(a.cuts[i - 1] + 1, a.cuts[i]))
end

local function RunCall(id, job)
	jobs[id] = nil
	MarkDone(id)
	local t0, body, chunk = debugprofilestop(), table.concat(job.parts), CHUNK[job.verb] or "=call"
	local req, err = ns.Json.Decode(body)
	local text, failure
	if err then
		failure = "its data is not JSON: " .. err
	elseif type(req) ~= "table" then
		failure = "its data is not a JSON object"
	else
		local ok, a, why = pcall(Answer, job.verb, req)
		if not ok then failure = Text(a) elseif a == nil then failure = why else text = a end
	end
	if text and job.verb ~= "piece" then
		local s, cuts = Pieces(text)
		if #cuts > 1 then
			for k, kept in pairs(answers) do
				if GetTime() - kept.at > ANSWER_KEPT then answers[k] = nil end   -- left by a daemon that never asked
			end
			answers[id] = { s = s, cuts = cuts, left = #cuts - 1, at = GetTime() }
		end
		text = "1/" .. #cuts .. "\n" .. s:sub(1, cuts[1])
	end
	local ms = debugprofilestop() - t0
	if failure then
		Result(("%s error %s: %s"):format(id, chunk, failure))
	else
		Result(("%s ok %s (%d B, %.1f ms, 1 value): %s"):format(id, chunk, #body, ms, Quote(text)))
	end
	if job.verb ~= "call" and job.verb ~= "reply" then return end      -- the app's own looks, as probes are
	local addon = type(req) == "table" and (job.verb == "call" and req.a or "-") or "-"
	Count(chunk, failure == nil)
	pcall(ns.Console.Job, chunk, tostring(addon), nil, failure == nil, ms, text or "", failure)
end

-- a CALL record (Link.lua hands them over); its parts gather in the same table as CODE jobs' (the ids never meet)
function A.Call(data)
	local head, body = data:match("^([^\n]*)\n(.*)$")
	local id, i, n, verb = (head or ""):match("^(%d+) (%d+)/(%d+) ?(%S*)$")
	i, n = tonumber(i), tonumber(n)
	if not id or i < 1 or i > n or Done(id) or Stale(id) then return end
	local job = jobs[id]
	if not job then
		job = { n = n, parts = {}, got = 0 }
		jobs[id] = job
	end
	if i == 1 then job.verb = verb end
	if not job.parts[i] then job.parts[i], job.got = body, job.got + 1 end
	if job.got == job.n and job.verb then RunCall(id, job) end
end
