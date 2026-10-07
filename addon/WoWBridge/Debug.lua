-- WoWBridge debug forwarding: Lua errors (of every addon, with the stack), print() output, Lua warnings and blocked
-- actions go to the companion as debug messages "ERR <error>\n<stack>", "OUT <lines>", "WARN <warning>",
-- "BLOCKED <event> <addon> <function>" (Link.lua sends them typed as debug; src/wuxianworkshop/transport/link.py reads
-- them). The hooks are set when this file loads, so that errors in the files and addons loading later are caught; what
-- !WuxianWorkshop kept from before WoWBridge loaded (WuxianWorkshopEarly) is taken over here. A text seen before only
-- counts, and the count goes out with it at most every REPEAT_EVERY seconds; print() lines are gathered for OUT_FLUSH
-- seconds; at most DEBUG_MAX debug messages wait at a time, more are dropped and counted ("DROPPED n ..."). The setting
-- forwardDebug (Config.lua, /wb set forwardDebug off) turns the forwarding off; the client's own handlers run as before.
local _, ns = ...

local D = {}
ns.Debug = D
D.stats = { errors = 0, warnings = 0, blocked = 0 }  -- this UI session's, forwarded or not, for the panel

local DEBUG_MAX = 20
local OUT_FLUSH, OUT_MAX = 0.5, 1200
local REPEAT_EVERY = 10
local buffer = {}                      -- messages from before the link started
local outLines, outBytes, outSince = {}, 0, nil
local seen, seenCount, dropped = {}, 0, 0

local function Enabled()
	local v = ns.cfg and ns.cfg.forwardDebug
	if v == nil then v = (WoWBridge_Config or {}).forwardDebug end
	return v ~= false
end

-- queue a debug message for the companion; kept here until the link starts
function D.Queue(text)
	local link = ns.Link
	local waiting = #buffer + (link and link.Waiting("debug") or 0)
	if waiting >= DEBUG_MAX then
		dropped = dropped + 1
		return
	end
	if dropped > 0 and waiting < DEBUG_MAX - 1 then
		local n = dropped
		dropped = 0
		D.Queue(("DROPPED %d debug messages (more than %d were waiting)"):format(n, DEBUG_MAX))
	end
	if link and link.State() ~= "off" then
		link.Send(text, "debug")
	else
		buffer[#buffer + 1] = text
	end
end

-- the first time a text comes: send it (detail on the lines below); after that only count it
local function Seen(text, detail)
	local e = seen[text]
	if e then
		e.n = e.n + 1
		return
	end
	seenCount = seenCount + 1
	if seenCount > 200 then seen, seenCount = {}, 1 end   -- many different ones: forget the old
	seen[text] = { n = 0, at = GetTime() }
	D.Queue(detail and detail ~= "" and (text .. "\n" .. detail) or text)
end

local function FlushOut()
	if #outLines == 0 then return end
	local text = table.concat(outLines, "\n")
	outLines, outBytes, outSince = {}, 0, nil
	D.Queue("OUT " .. text)
end

local function OnError(msg)
	D.stats.errors = D.stats.errors + 1
	if not Enabled() then return end
	local text = "ERR " .. tostring(msg)
	if seen[text] then return Seen(text) end
	-- the stack from the error on: without this handler's own line and the C call into it
	local stack = debugstack and debugstack(3) or ""
	stack = stack:gsub("^%[[^%]]*WoWBridge[/\\]Debug%.lua%][^\n]*\n", ""):gsub("^%[C%]: %?\n", "")
	Seen(text, stack:sub(1, 1500))
end

local function OnPrint(...)
	if not Enabled() then return end
	local parts = {}
	for i = 1, select("#", ...) do parts[i] = tostring((select(i, ...))) end
	local line = table.concat(parts, " "):sub(1, OUT_MAX)
	if outBytes + #line + 1 > OUT_MAX then FlushOut() end
	outLines[#outLines + 1] = line
	outBytes = outBytes + #line + 1
	outSince = outSince or GetTime()
end

local function OnDebugEvent(_, event, ...)
	local stats = D.stats
	if event == "LUA_WARNING" then stats.warnings = stats.warnings + 1 else stats.blocked = stats.blocked + 1 end
	if not Enabled() then return end
	local args = {}
	for i = 1, select("#", ...) do args[i] = tostring((select(i, ...))) end
	if event == "LUA_WARNING" then
		Seen("WARN " .. table.concat(args, " "))
	else
		Seen(("BLOCKED %s %s"):format(event, table.concat(args, " ")))
	end
end

-- runs every 0.5 s (Link.Tick): gathered print() lines and repeat counts go out
function D.Tick(now)
	if outSince and now - outSince >= OUT_FLUSH then FlushOut() end
	for text, e in pairs(seen) do
		if e.n > 0 and now - e.at >= REPEAT_EVERY then
			D.Queue(("%s\n(%d more times)"):format(text, e.n))
			e.n, e.at = 0, now
		end
	end
end

-- the link has started: what came before goes out (or away, if the saved setting says no forwarding)
function D.Start()
	local waiting = buffer
	buffer = {}
	if not Enabled() then return end
	for _, text in ipairs(waiting) do ns.Link.Send(text, "debug") end
end

-- the hooks: every error handler and print handler before ours still runs
if seterrorhandler and geterrorhandler then
	local previous = geterrorhandler()
	seterrorhandler(function(msg, ...)
		pcall(OnError, msg)
		if previous then return previous(msg, ...) end
	end)
end
if setprinthandler and getprinthandler then
	local previous = getprinthandler()
	setprinthandler(function(...)
		pcall(OnPrint, ...)
		return previous(...)
	end)
end
local events = CreateFrame("Frame")
for _, e in ipairs({ "LUA_WARNING", "ADDON_ACTION_BLOCKED", "ADDON_ACTION_FORBIDDEN" }) do
	pcall(events.RegisterEvent, events, e)          -- an event this client does not know is skipped
end
events:SetScript("OnEvent", function(...) pcall(OnDebugEvent, ...) end)

-- what !WuxianWorkshop (loaded before other addons) kept from before WoWBridge loaded: report it, and tell it to keep
-- nothing more
local early = WuxianWorkshopEarly
if early and not early.taken then
	early.taken = true
	D.stats.errors = D.stats.errors + #early.errors
	D.stats.warnings = D.stats.warnings + #early.warnings
	if Enabled() then
		for _, e in ipairs(early.errors) do
			local stack = e.stack:gsub("^%[[^%]]*!WuxianWorkshop[/\\]Core%.lua%][^\n]*\n", ""):gsub("^%[C%]: %?\n", "")
			Seen("ERR " .. e.msg, (stack:sub(1, 1500) .. "(before WoWBridge loaded)"))
		end
		for _, w in ipairs(early.warnings) do Seen("WARN " .. w) end
		for _, line in ipairs(early.prints) do pcall(OnPrint, line) end
	end
end
