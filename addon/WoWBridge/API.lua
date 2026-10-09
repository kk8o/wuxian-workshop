-- WoWBridge for addons: WoWBridge.Bind(<the addon's name>) gives a handle with the calls an addon makes for the agent that
-- works on it through 无限工坊 (MCP `events`, `call`, `respond`, `addon_api`):
--   handle:Emit(topic, data)       an event: what happened, as data (no chat line, no print); the agent reads or waits for
--                                  it with `events`
--   handle:Expose(name, fn, doc)   a function the agent may call with `call`: fn gets the call's arguments (a table made
--                                  from JSON) and its first return value goes back as JSON; `doc` says what it does
--   handle:Request(topic, data, callback, timeout)   a question for the agent: an event that carries a request id, which
--                                  the agent answers with `respond`; callback(reply) then, or callback(nil, "timeout") when
--                                  no answer came in `timeout` seconds (default 60), or callback(nil, "dropped") when it
--                                  could not go (the link busy); returns the request id
-- An event goes out as an EVENT message {"a":addon,"t":topic,"d":data[,"x":events dropped before it][,"r":request id,
-- "w":seconds the request still waits]} (Json.lua), at most RATE a second for an addon (BURST at once), its data at most
-- MAX bytes; while the link is not online the last KEEP wait (a request among them that has to go is called back
-- "dropped", and one whose time ran out meanwhile is not asked at all). Events are best effort: at most QUEUE of them, or
-- QUEUE_BYTES of their text, wait in the link's queue (more are dropped and counted); small ones go up several to a
-- frame, and a run's result goes up before them. Topics and addon names are at most NAME_MAX bytes.
-- An addon that uses this names WoWBridge in its .toc (## OptionalDeps: WoWBridge): addons load in name order, and one
-- that loads before WoWBridge finds no WoWBridge to bind to.
-- A call reaches WoWBridge.Call(addon, name, args) as data (a CALL record that Agent.lua reads, from 0.9.7) or, for a
-- WoWBridge before that, as a chunk the daemon sends (agent/probes.py, call_chunk); its answer is JSON either way, and
-- WoWBridge.Describe(addon) is what `addon_api` shows. Only data crosses: the agent calls what an addon exposed and nothing
-- else. A data call compiles nothing: it works with hot loading off, and the addon's function runs as any addon code does,
-- without the taint this client gives code loaded at run time (ForceTaint_Strong); a chunk is code the agent sent, which
-- this client taints (as `respond` was before 0.9.7, too). An
-- addon's callback that raises is reported as any error of the game is (with its stack), and `respond` says so. A name
-- stays exposed until the UI reloads: exposing it again replaces the function. Without 无限工坊 there is no WoWBridge:
-- the new_addon template takes a stub then, on which the same calls do nothing, so the addon runs for players as it is.
local _, ns = ...
local WB = _G.WoWBridge
local Json = ns.Json

local RATE, BURST, MAX, KEEP, QUEUE, QUEUE_BYTES, NAME_MAX = 10, 20, 8000, 20, 40, 6000, 64

local addons = {}     -- addon -> { exposed = { name -> { fn, doc } }, order = { names }, topics = { topic -> n }, tokens, at, dropped }
local waiting = {}    -- events from before the link was online, oldest first: { text, entry, request, deadline }
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

-- whether the link's queue has room for another event
local function Room()
	local n, bytes = ns.Link.Waiting("event")
	return n < QUEUE and bytes < QUEUE_BYTES
end

-- an addon's callback: its error is reported as the client reports any (the debug window, logs, with the callback's
-- stack) and not thrown at the caller; returns the error, or nil
local function Safe(fn, ...)
	local args, n, failed = { ... }, select("#", ...), nil
	xpcall(function() return fn(unpack(args, 1, n)) end, function(e)
		failed = tostring(e)
		geterrorhandler()(e)
	end)
	return failed
end

-- the event's text as it goes now: a request says how long it still waits
local function Out(item)
	if not item.deadline then return item.text .. "}" end
	return item.text .. (',"w":%d}'):format(math.max(1, math.floor(item.deadline - GetTime())))
end

-- the oldest event waiting goes to make room: counted for its addon (its next event says so); a request is called back
local function Evict()
	local old = table.remove(waiting, 1)
	old.entry.dropped = old.entry.dropped + 1
	local p = old.request and pending[old.request]
	if p then
		pending[old.request] = nil
		C_Timer.After(0, function() Safe(p.callback, nil, "dropped") end)
	end
end

-- what waited from before the link was online, as far as the link's queue has room (the ticker below comes back for
-- more); a request whose time ran out while it waited is not asked any more (its timer has called back "timeout")
local function Flush()
	if #waiting == 0 or not Online() then return end
	while #waiting > 0 and Room() do
		local item = table.remove(waiting, 1)
		if not item.deadline or item.deadline - GetTime() >= 1 then ns.Link.Send(Out(item), "event") end
	end
end

-- an event out (or waiting for the link), a request with its id and the seconds it waits; false when it was dropped
local function Send(self, topic, data, request, timeout)
	local e = Entry(self.addon)
	local now = GetTime()
	e.tokens, e.at = math.min(BURST, e.tokens + (now - e.at) * RATE), now
	local online = Online()
	if e.tokens < 1 or (online and not Room()) then   -- too many, or the link is busy: counted, and the next one says so
		e.dropped = e.dropped + 1
		return false
	end
	e.tokens = e.tokens - 1
	e.topics[topic] = (e.topics[topic] or 0) + 1
	local body = Json.Encode(data)
	if #body > MAX then body = ('{"cut":true,"bytes":%d}'):format(#body) end
	local item = { entry = e, request = request, deadline = request and now + timeout,
		text = '{"a":' .. Json.Encode(self.addon) .. ',"t":' .. Json.Encode(topic) .. ',"d":' .. body
			.. (e.dropped > 0 and (',"x":' .. e.dropped) or "") .. (request and (',"r":"%s"'):format(request) or "") }
	e.dropped = 0
	if online and #waiting == 0 then
		ns.Link.Send(Out(item), "event")
	else                                                    -- behind the ones still waiting, in order
		if #waiting >= KEEP then Evict() end
		waiting[#waiting + 1] = item
		Flush()
	end
	return true
end

local function Topic(topic, call)
	if type(topic) ~= "string" or topic == "" or #topic > NAME_MAX then
		error(("%s: the topic is a non-empty string of at most %d bytes"):format(call, NAME_MAX), 3)
	end
end

local function Emit(self, topic, data)
	Topic(topic, "Emit(topic, data)")
	return Send(self, topic, data)
end

local function Request(self, topic, data, callback, timeout)
	Topic(topic, "Request(topic, data, callback, timeout)")
	if type(callback) ~= "function" then error("Request(topic, data, callback, timeout): callback is a function", 2) end
	timeout = math.max(1, math.min(tonumber(timeout) or 60, 600))
	asked = asked + 1
	local id = ("%d.%d"):format(ns.session or 0, asked)
	pending[id] = { addon = self.addon, topic = topic, callback = callback }
	if not Send(self, topic, data, id, timeout) then
		pending[id] = nil
		C_Timer.After(0, function() Safe(callback, nil, "dropped") end)
		return nil
	end
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
	if type(name) ~= "string" or #name > NAME_MAX or not name:match("^[%w_.:%-]+$") then
		error(("Expose(name, fn, doc): the name is letters, digits and _ . : - (at most %d)"):format(NAME_MAX), 2)
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
	if type(addon) ~= "string" or addon == "" or #addon > NAME_MAX then
		error(("WoWBridge.Bind(addonName): the addon's name (at most %d bytes)"):format(NAME_MAX), 2)
	end
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
	if not e then
		return Json.Encode({ ok = false, error = ("%s has no WoWBridge handle: it did not call WoWBridge.Bind(%q) (not "
			.. "loaded, or loaded before WoWBridge: its .toc needs ## OptionalDeps: WoWBridge)"):format(tostring(addon), tostring(addon)) })
	end
	local x = e.exposed[name]
	if not x then
		local known = #e.order > 0 and ("it exposes " .. table.concat(e.order, ", ")) or "it exposes nothing"
		return Json.Encode({ ok = false, error = ("%s has no %s (%s)"):format(tostring(addon), tostring(name), known) })
	end
	local ok, value = xpcall(function() return x.fn(args) end, Traceback)
	if ok then return Json.Encode({ ok = true, result = value }, 8) end
	return Json.Encode({ ok = false, error = value })
end

-- `respond`: the answer to a request goes to its callback; {"ok":true,"addon":...,"topic":...[,"callback_error":...]}
-- or {"ok":false,"error":...}
function WB.Reply(id, data)
	local p = pending[id]
	if not p then
		return Json.Encode({ ok = false, error = ("no request %s is waiting for an answer (it timed out or was answered, or "
			.. "the UI reloaded)"):format(tostring(id)) })
	end
	pending[id] = nil
	local failed = Safe(p.callback, data)
	return Json.Encode({ ok = true, addon = p.addon, topic = p.topic, callback_error = failed })
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
