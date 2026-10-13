r"""What the agent learns from the running game through `run` alone, so that any WoWBridge 0.9 serves it (no addon update,
no reload): `trace` records the events the game fires for a while (names, arguments, how often each fired); `inspect`
describes frames — place, size, anchors, visibility, strata and level, scripts, children and regions — named by a Lua
expression, or the ones under the mouse; `api_scan` lists what the game has for the API manual (its C_ namespaces'
functions, its global functions, its Enum and Constants tables). Each is a Lua chunk sent with `run`; the game answers
with JSON made by the small encoder J below (secret values come back as "<secret>"), parsed here.

A RUN result brings back at most 4000 bytes of what the code returned (WoWBridge's Agent.lua cuts it there), so the
answer comes in pieces (answer_chunk): the chunk keeps it in the game and returns the first piece and how many there are,
piece_chunk brings each of the others. A piece ends between two UTF-8 characters, and the daemon takes it as it came:
api.parse_run neither splits the "=probe" chunk's result at ", " nor reads notes into it.

The chunks are plain Lua 5.1 with this client's API (RegisterAllEvents, GetMouseFoci, issecretvalue, GetPhysicalScreenSize:
all in the API manual). Rectangles are in the game window's client pixels, from its top-left corner, as `snap` takes them.
"""
import json
import re

# J(value, depth): JSON text of a Lua value; tables as arrays when they have a sequence (or are empty), else objects;
# a number that is not finite as null, a string with the quote, the backslash and every byte below 32 escaped
LUA_JSON = r"""
local issecret = issecretvalue
local function J(v, depth)
	depth = depth or 4
	if issecret and issecret(v) then return '"<secret>"' end
	local t = type(v)
	if t == "nil" then return "null" end
	if t == "boolean" then return v and "true" or "false" end
	if t == "number" then
		local s = string.format("%.4f", v)        -- told by its text: on this client NaN equals every number, math.huge too
		if not s:find("^%-?%d+%.%d+$") then return "null" end          -- inf, -inf, nan, -nan(ind)
		if v == math.floor(v) and math.abs(v) < 2147483648 then return string.format("%d", v) end   -- %d: 32 bits
		return s
	end
	if t == "string" then
		return '"' .. (v:gsub('[%z\1-\31"\\]', function(c) return string.format("\\u%04x", c:byte()) end)) .. '"'
	end
	if t == "table" then
		if depth <= 0 then return '"<table>"' end
		local out, n = {}, #v
		if n > 0 or next(v) == nil then
			for i = 1, n do out[i] = J(v[i], depth - 1) end
			return "[" .. table.concat(out, ",") .. "]"
		end
		for k, x in pairs(v) do out[#out + 1] = J(tostring(k), 0) .. ":" .. J(x, depth - 1) end
		return "{" .. table.concat(out, ",") .. "}"
	end
	local ok, s = pcall(tostring, v)
	return J(ok and s or ("<" .. t .. ">"), 0)
end
"""

# the chattiest events: counted, but kept only when the trace's pattern asks for them
QUIET = ("COMBAT_LOG_EVENT_UNFILTERED", "COMBAT_LOG_EVENT", "UNIT_AURA", "UNIT_POWER_FREQUENT", "UNIT_POWER_UPDATE",
         "UNIT_HEALTH", "UNIT_HEALTH_FREQUENT", "UNIT_ABSORB_AMOUNT_CHANGED", "UNIT_THREAT_LIST_UPDATE",
         "UNIT_THREAT_SITUATION_UPDATE", "UNIT_COMBAT", "UNIT_FLAGS", "UNIT_MODEL_CHANGED", "UNIT_PORTRAIT_UPDATE",
         "UPDATE_MOUSEOVER_UNIT", "CURSOR_CHANGED", "CURSOR_UPDATE", "MODIFIER_STATE_CHANGED", "GLOBAL_MOUSE_DOWN",
         "GLOBAL_MOUSE_UP", "WORLD_CURSOR_TOOLTIP_UPDATE", "NAME_PLATE_UNIT_ADDED", "NAME_PLATE_UNIT_REMOVED",
         "ACTIONBAR_UPDATE_COOLDOWN", "ACTIONBAR_UPDATE_USABLE", "ACTIONBAR_UPDATE_STATE", "SPELL_UPDATE_COOLDOWN",
         "SPELL_UPDATE_USABLE", "BAG_UPDATE_COOLDOWN", "UPDATE_UI_WIDGET", "CHAT_MSG_ADDON", "CHAT_MSG_ADDON_LOGGED")

TRACE_BODY = r"""
local T = _G.WuxianWorkshopTrace or CreateFrame("Frame")
_G.WuxianWorkshopTrace = T
T:UnregisterAllEvents()
T.log, T.counts, T.dropped, T.t0 = {}, {}, 0, GetTime()
local unknown = {}
for _, e in ipairs(EVENTS) do
	if not pcall(T.RegisterEvent, T, e) then unknown[#unknown + 1] = e end
end
T:SetScript("OnEvent", function(self, event, ...)
	self.counts[event] = (self.counts[event] or 0) + 1
	if QUIET[event] then return end
	if #self.log >= MAX then self.dropped = self.dropped + 1 return end
	local n = select("#", ...)
	local len = math.min(n, ARGS)
	local args = {}
	for i = 1, len do
		local v = select(i, ...)
		if issecretvalue and issecretvalue(v) then
			v = "<secret>"
		elseif type(v) == "string" then
			if #v > 160 then v = v:sub(1, 160) .. "…" end
		elseif type(v) ~= "number" and type(v) ~= "boolean" and v ~= nil then
			local ok, s = pcall(tostring, v)
			v = ok and s or "?"
		end
		args[i] = v
	end
	self.log[#self.log + 1] = { t = GetTime() - self.t0, e = event, a = args, len = len, n = n }
end)
return '{"tracing":' .. (#EVENTS - #unknown) .. ',"unknown":' .. J(unknown) .. '}'
"""

TRACE_STOP = r"""
local T = _G.WuxianWorkshopTrace
if not T or not T.log then return '{"error":"no trace is running"}' end
T:UnregisterAllEvents()
T:SetScript("OnEvent", nil)
local events = {}
for i, x in ipairs(T.log) do
	local a = {}
	for j = 1, x.len do a[j] = J(x.a[j], 1) end
	events[i] = "[" .. string.format("%.3f", x.t) .. "," .. J(x.e) .. ",[" .. table.concat(a, ",") .. "]," .. x.n .. "]"
end
local counts = {}
for e, c in pairs(T.counts) do counts[#counts + 1] = J(e) .. ":" .. c end
local out = '{"seconds":' .. string.format("%.2f", GetTime() - T.t0) .. ',"dropped":' .. T.dropped .. ',"events":['
	.. table.concat(events, ",") .. '],"counts":{' .. table.concat(counts, ",") .. '}}'
T.log, T.counts = nil, nil
return out
"""

INSPECT_BODY = r"""
local physW, physH = GetPhysicalScreenSize()
local SCRIPTS = { "OnShow", "OnHide", "OnEvent", "OnUpdate", "OnClick", "OnEnter", "OnLeave", "OnMouseDown", "OnMouseUp",
	"OnDragStart", "OnValueChanged", "OnTextChanged", "OnKeyDown" }
-- f:m(...), or nothing when f has no m or the client refuses m on this kind of object: a Line has GetNumPoints and
-- GetPoint (the region base's) and raises "Wrong object type for function" on them, which failed the whole inspect
local function results(ok, ...) if ok then return ... end end
local function call(f, m, ...) if f[m] then return results(pcall(f[m], f, ...)) end end
local function forbidden(f) return f.IsForbidden and f:IsForbidden() end
local function nm(f)
	if f == nil then return nil end
	if type(f) ~= "table" or not f.GetObjectType then return tostring(f) end
	if forbidden(f) then return "<forbidden>" end
	local ok, n = pcall(function() return (f.GetDebugName and f:GetDebugName()) or f:GetName() end)
	return ok and n or tostring(f)
end
local function px(f)
	local l, b, w, h = call(f, "GetRect")
	-- a bar set from a secret value (on this client the player's own health too) has a secret place: no arithmetic on it
	if issecret and (issecret(l) or issecret(b) or issecret(w) or issecret(h)) then return "<secret>" end
	local scale = call(f, "GetEffectiveScale")
	if not (l and scale) then return nil end
	local s = scale * physH / 768
	return { math.floor(l * s + 0.5), math.floor(physH - (b + h) * s + 0.5), math.floor(w * s + 0.5), math.floor(h * s + 0.5) }
end
local function info(f, depth)
	if type(f) ~= "table" or not f.GetObjectType then return { value = tostring(f), type = type(f) } end
	if forbidden(f) then return { name = "<forbidden>" } end
	local o = { name = nm(f), type = f:GetObjectType(), shown = call(f, "IsShown"), visible = call(f, "IsVisible"),
		alpha = call(f, "GetAlpha"), size = { call(f, "GetWidth"), call(f, "GetHeight") }, rect = px(f),
		parent = nm(call(f, "GetParent")), strata = call(f, "GetFrameStrata"), level = call(f, "GetFrameLevel"),
		layer = call(f, "GetDrawLayer"), text = call(f, "GetText"), texture = call(f, "GetTexture"),
		protected = call(f, "IsProtected") or nil, mouse = call(f, "IsMouseEnabled") }
	local points = {}
	if f.GetStartPoint then                        -- a Line: placed by its two ends, as its points "start" and "end"
		for _, e in ipairs({ "Start", "End" }) do
			local rp, rel, x, y = call(f, "Get" .. e .. "Point")
			if rp then points[#points + 1] = { e:lower(), nm(rel), rp, x, y } end
		end
	else
		for i = 1, call(f, "GetNumPoints") or 0 do
			local ok, p, rel, rp, x, y = pcall(f.GetPoint, f, i)
			if ok then points[#points + 1] = { p, nm(rel), rp, x, y } end
		end
	end
	o.points = points
	if f.HasScript then
		local s = {}
		for _, h in ipairs(SCRIPTS) do
			if call(f, "HasScript", h) and call(f, "GetScript", h) then s[#s + 1] = h end
		end
		o.scripts = s
	end
	if f.GetChildren then
		local kids, regions = { call(f, "GetChildren") }, { call(f, "GetRegions") }
		o.children_count, o.regions_count = #kids, #regions
		if depth > 0 then
			o.children, o.regions = {}, {}
			for i = 1, math.min(#kids, LIMIT) do o.children[i] = info(kids[i], depth - 1) end
			for i = 1, math.min(#regions, LIMIT) do o.regions[i] = info(regions[i], 0) end
		end
	end
	return o
end
local frames = {}
if MOUSE then
	for i, f in ipairs(GetMouseFoci and GetMouseFoci() or {}) do
		local o = info(f, 0)
		local chain, p = {}, call(f, "GetParent")
		while p and #chain < 12 do chain[#chain + 1] = nm(p) p = call(p, "GetParent") end
		o.parents = chain
		frames[i] = o
	end
else
	local ok, target = pcall(TARGET)
	if not ok then return '{"error":' .. J("target: " .. tostring(target)) .. '}' end
	if target == nil then return '{"error":"target: the expression gave nil (no such frame)"}' end
	frames[1] = info(target, DEPTH)
end
return '{"screen":' .. J({ physW, physH }) .. ',"frames":' .. J(frames, 2 + DEPTH * 3 + 4) .. '}'
"""

# an answer's pieces: at most PIECE bytes each, the others asked for BATCH at a time (their requests share mailbox
# packets, the link brings the pieces back one after another); an answer over MOST bytes is refused (over a minute to
# bring)
PIECE, BATCH, MOST = 3900, 4, 256 * 1024

# after the chunk, run as a function (ANSWER: what it returned): the first piece, the others kept under KEY
ANSWER_PIECES = r"""
local s = tostring(ANSWER)
if #s > MOST then
	s = ('{"error":"the answer is %d KB, more than the %d KB a probe brings back: ask for less (inspect: a lower depth or a '
		.. 'frame further down; trace: fewer events or arguments)"}'):format(math.ceil(#s / 1024), MOST / 1024)
end
local cuts, from = {}, 1
repeat
	local to = math.min(#s, from + PIECE - 1)
	for _ = 1, 3 do                                -- between two UTF-8 characters: the next byte starts one
		local b = s:byte(to + 1)
		if not b or b < 128 or b >= 192 then break end
		to = to - 1
	end
	cuts[#cuts + 1] = to
	from = to + 1
until from > #s
if #cuts > 1 then
	local kept = _G.WuxianWorkshopAnswers or {}
	_G.WuxianWorkshopAnswers = kept
	for k, a in pairs(kept) do
		if GetTime() - a.at > 300 then kept[k] = nil end   -- left by a daemon that never asked for the rest
	end
	kept[KEY] = { s = s, cuts = cuts, left = #cuts - 1, at = GetTime(), served = {} }
end
return "1/" .. #cuts .. "\n" .. s:sub(1, cuts[1])
"""

PIECE_BODY = r"""
local kept = _G.WuxianWorkshopAnswers
local a = kept and kept[KEY]
if not a then error("the rest of this answer is no longer kept in the game (the UI reloaded?)", 0) end
a.served = a.served or {}
if not a.served[I] then a.served[I], a.left = true, a.left - 1 end
if a.left <= 0 then a.at = math.min(a.at, GetTime() - 240) end  -- every piece went: a minute more, for one asked again
return I .. "/" .. #a.cuts .. "\n" .. a.s:sub(a.cuts[I - 1] + 1, a.cuts[I])
"""


# a slash command as the chat would run it: the handler an addon registered (SLASH_<KEY><n> = "/cmd", SlashCmdList.KEY)
# called with the rest of the line; LINE is set before it. On this client SlashCmdList is a front table over another (its
# metatable's __index): the client moves the commands registered in the front one to the one behind (seen in the game:
# all of them, soon after login), where pairs does not look. So every table of the __index chain is searched, from the
# front (a handler registered since, say by a hot-loaded file, before the one it replaced), then the chat's cache,
# hash_SlashCmdList
SLASH_BODY = r"""
local cmd, msg = LINE:match("^(/%S+)%s*(.-)%s*$")
if not cmd then error("not a slash command: " .. LINE, 0) end
local want, box = cmd:upper(), DEFAULT_CHAT_FRAME and DEFAULT_CHAT_FRAME.editBox
local seen, list = {}, SlashCmdList
while type(list) == "table" and not seen[list] do
	seen[list] = true
	for key, handler in pairs(list) do
		local i = 1
		while true do
			local s = _G["SLASH_" .. key .. i]
			if type(s) ~= "string" then break end
			if s:upper() == want then
				return handler(msg, box)
			end
			i = i + 1
		end
	end
	local meta = getmetatable(list)
	list = type(meta) == "table" and rawget(meta, "__index") or nil
end
local hashed = type(hash_SlashCmdList) == "table" and hash_SlashCmdList[want]
if hashed then return hashed(msg, box) end
error("no slash command " .. cmd .. " (an addon registers one with SLASH_NAME1 = \"/cmd\" and SlashCmdList.NAME)", 0)
"""


def slash_call(line):
    """the chunk that runs a slash command line ("/myaddon show") through its handler"""
    if not isinstance(line, str) or not line.strip().startswith("/") or len(line.strip()) < 2:
        raise ValueError('slash: a command line that starts with "/" ("/myaddon show")')
    return f"local LINE = {lua_str(line.strip())}\n" + SLASH_BODY


_LUA_ESCAPES = {i: f"\\{i:03d}" for i in [*range(32), 127]}
_LUA_ESCAPES.update({ord('"'): '\\"', ord("\\"): "\\\\"})


def lua_str(s):
    """a Lua string literal for any text: in double quotes, the quote, the backslash and every control character
    escaped (\\ddd), the rest as it is (UTF-8 goes through untouched). Long brackets were not enough: Lua 5.1 refuses a
    "[[" inside them and turns a CR into a newline. ValueError for text that is not valid Unicode (a lone surrogate)"""
    s = str(s)
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("not valid Unicode text (a lone surrogate)") from None
    return '"' + s.translate(_LUA_ESCAPES) + '"'


def safe_events():
    """this client's events an addon may register, from the API manual: all but the restricted ones (HasRestrictions,
    e.g. COMBAT_LOG_EVENT_UNFILTERED: registering it got WoWBridge blocked — ADDON_ACTION_FORBIDDEN, ForceTaint_Strong)
    and the internal callback events (CallbackEvent)"""
    from .. import apidocs
    names = set()
    for e in apidocs.index().entries:
        raw = e["item"].get("raw") or {} if e["kind"] == "event" else None
        if raw is not None and not raw.get("HasRestrictions") and not raw.get("CallbackEvent"):
            names.add(e["short"])
    return sorted(names)


def restricted_events():
    from .. import apidocs
    return sorted(e["short"] for e in apidocs.index().entries
                  if e["kind"] == "event" and ((e["item"].get("raw") or {}).get("HasRestrictions")
                                               or (e["item"].get("raw") or {}).get("CallbackEvent")))


def select_events(spec=None):
    """(the events to register, the chatty ones among them to count only): spec = names or globs (BAG_*, LOOT_*),
    comma or space separated, case-insensitive; none = every event an addon may register, the QUIET ones counted only.
    A restricted event named or matched is a ValueError (the client would block the addon)"""
    import fnmatch
    import re
    safe = safe_events()
    if not spec or not str(spec).strip():
        return safe, set(QUIET)
    wanted = [w.upper() for w in re.split(r"[\s,]+", str(spec).strip()) if w]
    blocked = set(restricted_events())
    chosen, refused = [], []
    for w in wanted:
        if any(c in w for c in "*?["):
            chosen += [e for e in safe if fnmatch.fnmatchcase(e, w)]   # a glob leaves the restricted ones out
        elif w in blocked:
            refused.append(w)
        else:
            chosen.append(w)                       # also a name the manual lacks: the game says whether it knows it
    if refused:
        from ..i18n import tr
        raise ValueError(tr("这些事件插件不能注册（客户端会拦截并提示禁用插件）：", "addons may not register these events (the client "
                            "blocks the addon and asks to disable it): ") + tr("、", ", ").join(sorted(set(refused))))
    chosen = sorted(set(chosen))
    if not chosen:
        raise ValueError(f"events: nothing matches {spec!r} (names or globs like BAG_*, LOOT_*)")
    return chosen, set()


def trace_start(spec=None, max_events=200, args=6):
    """the chunk that starts a trace of the events select_events(spec) gives: every one counted, the chatty ones of an
    unfiltered trace not kept, the rest kept with their first `args` arguments, at most `max_events`"""
    events, quiet = select_events(spec)
    head = ("local EVENTS, QUIET = {}, {}\n"                         # the names as one string each: half the bytes
            f"for e in ({lua_str(' '.join(events))}):gmatch('%S+') do EVENTS[#EVENTS + 1] = e end\n"
            f"for e in ({lua_str(' '.join(sorted(quiet)))}):gmatch('%S+') do QUIET[e] = true end\n"
            f"local MAX, ARGS = {int(max_events)}, {int(args)}\n")
    return LUA_JSON + head + TRACE_BODY


def trace_stop():
    """the chunk that stops the trace and answers with what it kept"""
    return LUA_JSON + TRACE_STOP


def inspect(target=None, mouse=False, depth=1, limit=30):
    """the chunk that describes the frame a Lua expression gives (target), or the frames under the mouse"""
    if not mouse and not (isinstance(target, str) and target.strip()):
        raise ValueError("target: a Lua expression that gives a frame (e.g. PlayerFrame, MyAddon.window), or mouse=true")
    depth = max(0, min(int(depth), 3))
    head = (f"local MOUSE = {'true' if mouse else 'false'}\n"
            f"local DEPTH, LIMIT = {depth}, {int(limit)}\n"
            f"local TARGET = function() return ({target if not mouse else 'nil'}) end\n")
    return LUA_JSON + head + INSPECT_BODY


# an addon's own API (WoWBridge API.lua: the functions it exposed, the topics it emitted): WoWBridge answers in JSON itself.
# A WoWBridge from before it came says so instead of failing on a nil field
API_GUARD = ("if not (WoWBridge and WoWBridge.Call and WoWBridge.Describe) then return '{\"error\":\"WoWBridge in the game has "
             "no addon API (Emit / Expose / call): it came with 无限工坊 0.9.6; update the program, then restart the "
             "game\"}' end\n")


def lua_value(v, depth=0):
    """a Lua literal for a JSON value (the arguments of a call): objects and arrays as tables, strings quoted (lua_str),
    finite numbers (whole ones up to 2^53, past which a Lua number is not exact), booleans, nil; ValueError for anything
    else"""
    if depth > 20:
        raise ValueError("args: nested deeper than 20 levels")
    if v is None or isinstance(v, bool):
        return {None: "nil", True: "true", False: "false"}[v]
    if isinstance(v, int):
        if abs(v) > 2 ** 53:
            raise ValueError("args: whole numbers up to 2^53 (a Lua number holds no more exactly)")
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("args: numbers are finite")
        return repr(v)
    try:
        if isinstance(v, str):
            return lua_str(v)
        if isinstance(v, (list, tuple)):
            return "{" + ", ".join(lua_value(x, depth + 1) for x in v) + "}"
        if isinstance(v, dict):
            return "{" + ", ".join(f"[{lua_str(str(k))}] = {lua_value(x, depth + 1)}" for k, x in v.items()) + "}"
    except ValueError as e:
        raise ValueError(e.args[0] if str(e).startswith("args:") else f"args: {e}") from None
    raise ValueError(f"args: a {type(v).__name__} is not JSON")


def call_chunk(addon, name, args=None):
    """the chunk that calls a function an addon exposed (WoWBridge.Call) with args and answers with its JSON"""
    return API_GUARD + f"return WoWBridge.Call({lua_str(addon)}, {lua_str(name)}, {lua_value(args)})"


def describe_chunk(addon=None, brief=False):
    """the chunk that answers with an addon's API in JSON (every addon's without one); brief: the names without their
    docs (a WoWBridge before 0.9.9 gives them all the same)"""
    return API_GUARD + f"return WoWBridge.Describe({lua_str(addon) if addon else 'nil'}{', true' if brief else ''})"


REPLY_GUARD = ("if not (WoWBridge and WoWBridge.Reply) then return '{\"error\":\"WoWBridge in the game takes no answers "
               "(Request / respond): it came with 无限工坊 0.9.6; update the program, then restart the game\"}' end\n")


def reply_chunk(request, data=None):
    """the chunk that hands the answer to an addon's request (WoWBridge.Reply) to its callback, and says whether it was
    still waiting"""
    return REPLY_GUARD + f"return WoWBridge.Reply({lua_str(request)}, {lua_value(data)})"


# what the game has, for the API manual (wuxian api-scan; scripts/build_api_pack.py): its C_ namespaces with their
# functions, its global functions, its Enum and Constants tables, the fields of the tables asked for. A name an addon set
# (not secure: issecurevariable names the addon) is listed apart in `replaced`: it is that addon's own, or the client's
# replaced
SCAN_PARTS = ("namespaces", "globals", "tables", "fields")
SCAN_BODY = r"""
local secure = issecurevariable
local version, build = GetBuildInfo()
local out = { client = version .. "." .. build, replaced = {} }
local function own(t, k)                       -- set by the client or Blizzard's code; else the addon that set it
	local ok, by = secure(t, k)
	if ok then return true end
	return false, by or "?"
end
local function sorted(t) table.sort(t) return t end
if PART == "namespaces" then
	local ns, addons = {}, {}
	for k, v in pairs(_G) do
		if type(k) == "string" and k:find("^C_") and type(v) == "table" then
			local ok, by = own(_G, k)
			if ok then
				local fns = {}
				for f, fv in pairs(v) do
					if type(f) == "string" and type(fv) == "function" then
						local fok, fby = own(v, f)
						if fok then fns[#fns + 1] = f else out.replaced[k .. "." .. f] = fby end
					end
				end
				ns[k] = sorted(fns)
			else
				out.replaced[k] = by
			end
		end
	end
	for i = 1, C_AddOns.GetNumAddOns() do
		if C_AddOns.IsAddOnLoaded(i) then addons[#addons + 1] = (C_AddOns.GetAddOnInfo(i)) end
	end
	out.namespaces, out.addons = ns, sorted(addons)
elseif PART == "globals" then
	local fns = {}
	for k, v in pairs(_G) do
		if type(k) == "string" and type(v) == "function" then
			local ok, by = own(_G, k)
			if ok then fns[#fns + 1] = k else out.replaced[k] = by end
		end
	end
	out.globals = sorted(fns)
elseif PART == "tables" then
	for _, base in ipairs({ "Enum", "Constants" }) do
		local names, t = {}, _G[base]
		for k, v in pairs(type(t) == "table" and t or {}) do
			if type(k) == "string" and type(v) == "table" then
				local ok, by = own(t, k)
				if ok then names[#names + 1] = k else out.replaced[base .. "." .. k] = by end
			end
		end
		out[base == "Enum" and "enums" or "constants"] = sorted(names)
	end
else
	local fields = {}
	for _, n in ipairs(NAMES) do
		local base, key = n:match("^(%a+)%.(.+)$")
		local t = type(_G[base]) == "table" and _G[base][key]
		if type(t) == "table" then
			local f = {}
			for k, v in pairs(t) do
				local vt = type(v)
				if type(k) == "string" and (vt == "number" or vt == "string" or vt == "boolean") then f[k] = v end
			end
			fields[n] = f
		end
	end
	out.fields = fields
end
return J(out)
"""


def api_scan(part, names=None):
    """the chunk that answers one part of what the game has (SCAN_PARTS): {client: "1.60.1.70338", replaced: {name: the
    addon that set it}, namespaces: {C_X: [functions]} and addons: [the loaded ones] | globals: [functions] | enums,
    constants: [table names] | fields: {"Enum.X": {field: value}} for names, the Enum.X / Constants.X tables asked for}"""
    if part not in SCAN_PARTS:
        raise ValueError(f"part: one of {', '.join(SCAN_PARTS)}")
    names = list(names or ()) if part == "fields" else []
    if part == "fields" and not (names and all(isinstance(n, str) and re.fullmatch(r"(Enum|Constants)\.\w+", n)
                                               for n in names)):
        raise ValueError("names: the tables whose fields to bring, as Enum.X or Constants.X")
    head = f"local PART, NAMES = {lua_str(part)}, {{{', '.join(lua_str(n) for n in names)}}}\n"
    return LUA_JSON + head + SCAN_BODY


def answer_chunk(chunk, key):
    """a probe chunk run so that its answer comes back in pieces: it returns the first piece after a line "1/<pieces>",
    and keeps the answer in the game under `key` for piece_chunk when there are more (the chunk's lines keep their
    numbers)"""
    return ("local ANSWER = (function(...) " + chunk + "\nend)(...)\n"
            f"local KEY, PIECE, MOST = {lua_str(key)}, {PIECE}, {MOST}\n" + ANSWER_PIECES)


def piece_chunk(key, i):
    """the chunk that returns piece i (2 and up) of the answer kept under `key`, after a line "<i>/<pieces>" """
    return f"local KEY, I = {lua_str(key)}, {int(i)}\n" + PIECE_BODY


def piece(values, i):
    """(how many pieces the answer has, the text of piece i) from what answer_chunk / piece_chunk returned; ValueError
    when it is not that piece"""
    text = values[0] if values and isinstance(values[0], str) else ""
    head, sep, rest = text.partition("\n")
    m = re.fullmatch(r"(\d+)/(\d+)", head)
    if not sep or not m or int(m[1]) != i or i > int(m[2]):
        raise ValueError(f"the game answered {str(values)[:200]}, not piece {i} of a probe's answer")
    return int(m[2]), rest


def parse(values):
    """the JSON a chunk answered with (the first returned value); ValueError when there is none, or it is not JSON
    (saying where it breaks)"""
    text = values[0] if values else None
    if not isinstance(text, str):
        raise ValueError(f"the game answered {values!r}, not JSON")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"the game's answer is not JSON: {where(text, e)}") from None
    if isinstance(data, dict) and data.get("error"):
        raise ValueError(data["error"])
    return data


def where(text, e, around=80):
    """a JSON error with its place: what is wrong, at which character of how many, the text on either side of it"""
    a, b = max(0, e.pos - around), min(len(text), e.pos + around)
    return (f"{e.msg.removesuffix(' at')} at char {e.pos} of {len(text)}: {'…' if a else ''}{text[a:e.pos]}<<HERE>>"
            f"{text[e.pos:b]}" + ("…" if b < len(text) else "<<END>>"))


def trace_result(data, wanted=None):
    """the trace as the agent gets it: events as {t, event, args, n} and counts from the most frequent down"""
    events = [dict(t=e[0], event=e[1], args=e[2], n=e[3]) for e in data.get("events", [])]
    counts = sorted(data.get("counts", {}).items(), key=lambda kv: (-kv[1], kv[0]))
    return dict(seconds=data.get("seconds"), kept=len(events), dropped=data.get("dropped", 0), pattern=wanted,
                events=events, counts=[dict(event=e, count=c) for e, c in counts])
