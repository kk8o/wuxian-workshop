-- WoWBridge for addons: WoWBridge.Bind(<the addon's name>) gives a handle with the calls an addon makes for the agent that
-- works on it through 无限工坊 (MCP `events`, `call`, `respond`, `addon_api`):
--   handle:Emit(topic, data)       an event: what happened, as data (no chat line, no print); the agent reads or waits for
--                                  it with `events`
--   handle:Expose(name, fn, doc)   a function the agent may call with `call`: fn gets the call's arguments (a table made
--                                  from JSON) and its first return value goes back as JSON; `doc` says what it does
--   handle:Request(topic, data, callback, timeout)   a question for the agent: an event that carries a request id, which
--                                  the agent answers with `respond`; callback(reply) then, or callback(nil, "timeout") when
--                                  no answer came in `timeout` seconds (default 60), or callback(nil, "dropped") at once
--                                  when it could not go (the link busy); returns the request id
-- An event goes out as an EVENT message {"a":addon,"t":topic,"d":data[,"x":events dropped before it][,"r":request id,
-- "w":seconds the request waits]} (Json.lua), at most
-- RATE a second for an addon (BURST at once), its data at most MAX bytes; while the link is not online the last KEEP wait.
-- Events are best effort: the link carries about 4 messages a second, in order, so at most QUEUE of them wait in its queue
-- (more are dropped and counted) and a burst of events never holds up a run's result by more than a couple of seconds.
-- An addon that uses this names WoWBridge in its .toc (## OptionalDeps: WoWBridge): addons load in name order, and one
-- that loads before WoWBridge finds no WoWBridge to bind to.
-- A call is a chunk the daemon sends (agent/probes.py, call_chunk) that runs WoWBridge.Call(addon, name, args) and
-- returns its answer as JSON; WoWBridge.Describe(addon) is what `addon_api` shows. Only data crosses: the agent calls what
-- an addon exposed and nothing else. Without 无限工坊 there is no WoWBridge: the new_addon template takes a stub then, on
-- which the same calls do nothing, so the addon runs for players as it is.
local _, ns = ...
local WB = _G.WoWBridge
local Json = ns.Json

local RATE, BURST, MAX, KEEP, QUEUE = 10, 20, 8000, 20, 8

local addons = {}     -- addon -> { exposed = { name -> { fn, doc } }, order = { names }, topics = { topic -> n }, tokens, at, dropped }
local waiting = {}    -- events from before the link was online, oldest first
local pending = {}    -- request id -> { addon, topic, callback }, until answered or timed out
local asked = 0       -- requests made in this UI session (the ids: "<WoWBridge session>.<n>")

local function Entry(addon)
	local e = addons[addon]
	if not e then
		e = { exposed = {}, order = {}, topics = {}, tokens = BURST, at = GetTime(), dropped = 0 }
		addons[addon] = e
	end
	return e
end

local function Online()
	local link = ns.Link
	return link ~= nil and link.State() == "online"
end

-- what waited from before the link was online, as far as the link's queue has room (the ticker below comes back for more)
local function Flush()
	if #waiting == 0 or not Online() then return end
	while #waiting > 0 and ns.Link.Waiting("event") < QUEUE do
		ns.Link.Send(table.remove(waiting, 1), "event")
	end
end

-- an event out (or waiting for the link), with `extra` added to its JSON; false when it was dropped
local function Send(self, topic, data, extra)
	local e = Entry(self.addon)
	local now = GetTime()
	e.tokens, e.at = math.min(BURST, e.tokens + (now - e.at) * RATE), now
	local online = Online()
	if e.tokens < 1 or (online and ns.Link.Waiting("event") >= QUEUE) then   -- too many, or the link is busy: counted,
		e.dropped = e.dropped + 1                                              -- and the next one that goes says so
		return false
	end
	e.tokens = e.tokens - 1
	e.topics[topic] = (e.topics[topic] or 0) + 1
	local body = Json.Encode(data)
	if #body > MAX then body = ('{"cut":true,"bytes":%d}'):format(#body) end
	local text = '{"a":' .. Json.Encode(self.addon) .. ',"t":' .. Json.Encode(topic) .. ',"d":' .. body
		.. (e.dropped > 0 and (',"x":' .. e.dropped) or "") .. (extra or "") .. "}"
	e.dropped = 0
	if online and #waiting == 0 then
		ns.Link.Send(text, "event")
	else                                                    -- behind the ones still waiting, in order
		if #waiting >= KEEP then table.remove(waiting, 1) end
		waiting[#waiting + 1] = text
		Flush()
	end
	return true
end

local function Emit(self, topic, data)
	if type(topic) ~= "string" or topic == "" then error("Emit(topic, data): the topic is a non-empty string", 2) end
	return Send(self, topic, data)
end

-- an addon's callback, its errors reported as the client reports any (the debug window, logs) and not thrown at us
local function Safe(fn, ...)
	local args, n = { ... }, select("#", ...)
	local ok, err = pcall(fn, unpack(args, 1, n))
	if not ok then geterrorhandler()(err) end
end

local function Request(self, topic, data, callback, timeout)
	if type(topic) ~= "string" or topic == "" then error("Request(topic, data, callback, timeout): the topic is a non-empty string", 2) end
	if type(callback) ~= "function" then error("Request(topic, data, callback, timeout): callback is a function", 2) end
	timeout = math.max(1, math.min(tonumber(timeout) or 60, 600))
	asked = asked + 1
	local id = ("%d.%d"):format(ns.session or 0, asked)
	if not Send(self, topic, data, (',"r":"%s","w":%d'):format(id, timeout)) then
		C_Timer.After(0, function() Safe(callback, nil, "dropped") end)
		return nil
	end
	pending[id] = { addon = self.addon, topic = topic, callback = callback }
	C_Timer.After(timeout, function()
		local p = pending[id]
		if p then
			pending[id] = nil
			Safe(p.callback, nil, "timeout")
		end
	end)
	return id
end

local function Expose(self, name, fn, doc)
	if type(name) ~= "string" or not name:match("^[%w_.:%-]+$") then
		error("Expose(name, fn, doc): the name is letters, digits and _ . : -", 2)
	end
	if type(fn) ~= "function" then error("Expose(name, fn, doc): fn is a function", 2) end
	local e = Entry(self.addon)
	if not e.exposed[name] then e.order[#e.order + 1] = name end
	e.exposed[name] = { fn = fn, doc = type(doc) == "string" and doc or nil }   -- a hot-load exposes it again: the new fn
	return true
end

local Handle = { Emit = Emit, Expose = Expose, Request = Request }
Handle.__index = Handle

-- the handle of an addon (its name: the first ... of its files)
function WB.Bind(addon)
	if type(addon) ~= "string" or addon == "" then error("WoWBridge.Bind(addonName): the addon's name", 2) end
	return setmetatable({ addon = addon }, Handle)
end

local function Traceback(e)
	local stack = debugstack and debugstack(2) or ""
	stack = stack:match("^(.-)%[C%]: in function 'xpcall'") or stack       -- the function's own frames, not the caller's
	return tostring(e) .. "\n" .. stack:sub(1, 1500)
end

-- `call`: the exposed function run with args; {"ok":true,"result":...} or {"ok":false,"error":...} as JSON
function WB.Call(addon, name, args)
	local e = addons[addon]
	local x = e and e.exposed[name]
	if not x then
		local known = e and #e.order > 0 and ("it exposes " .. table.concat(e.order, ", ")) or "it exposes nothing"
		return Json.Encode({ ok = false, error = ("%s has no %s (%s)"):format(tostring(addon), tostring(name), known) })
	end
	local ok, value = xpcall(function() return x.fn(args) end, Traceback)
	if ok then return Json.Encode({ ok = true, result = value }, 8) end
	return Json.Encode({ ok = false, error = value })
end

-- `respond`: the answer to a request goes to its callback; {"ok":true,"addon":...,"topic":...} or {"ok":false,"error":...}
function WB.Reply(id, data)
	local p = pending[id]
	if not p then
		return Json.Encode({ ok = false, error = ("no request %s is waiting for an answer (it timed out or was answered, or "
			.. "the UI reloaded)"):format(tostring(id)) })
	end
	pending[id] = nil
	Safe(p.callback, data)
	return Json.Encode({ ok = true, addon = p.addon, topic = p.topic })
end

local function Describe(name, e)
	local exposed = {}
	for i, n in ipairs(e.order) do exposed[i] = { name = n, doc = e.exposed[n].doc } end
	local waiting_for = 0
	for _, p in pairs(pending) do
		if p.addon == name then waiting_for = waiting_for + 1 end
	end
	return { addon = name, exposed = exposed, topics = e.topics, requests = waiting_for }
end

-- `addon_api`: what an addon exposed (in the order it did) and the topics of the events it sent, as JSON; without an
-- addon, every addon that took a handle
function WB.Describe(addon)
	if addon then
		local e = addons[addon]
		return Json.Encode(e and Describe(addon, e) or { addon = addon, exposed = {}, topics = {}, unbound = true })
	end
	local all = {}
	for name, e in pairs(addons) do all[#all + 1] = Describe(name, e) end
	table.sort(all, function(a, b) return a.addon < b.addon end)
	return Json.Encode({ addons = all })
end

C_Timer.NewTicker(1, Flush)      -- what waited goes once the link is online
