-- !WuxianWorkshop, the platform addon of 无限工坊 (Wuxian Workshop). Its folder name sorts first, so this runs before any
-- other addon loads and hooks the client's error handler (the previous handler still runs), LUA_WARNING and
-- ADDON_ACTION_BLOCKED / FORBIDDEN before anything can go wrong.
-- Player side: every Lua error, warning and blocked action is collected into the saved variable WuxianWorkshopDB, one
-- entry per signature (the message without digits and line numbers) with a count, the addon it came from, the first
-- stack, first / last time and the client build; at most MAX_ENTRIES entries, the least recently seen goes first; more
-- than RATE_LIMIT in a second pauses collecting for a second (the pause is counted in .dropped). The client writes the
-- file at logout or /reload; the Wuxian Workshop app reads it. WuxianWorkshopDB.settings.report: nil = not decided yet
-- (the app asks at its first start), false = collect in memory only, nothing is saved. /wxw report on|off|status,
-- /wxw clear.
-- Developer side: until WoWBridge loads, the errors (with their stack), warnings and print() output of the addons loading
-- in between are kept in WuxianWorkshopEarly (at most EARLY_MAX of each); WoWBridge (Debug.lua) reports them and sets
-- .taken, after which nothing more is kept there.
local addonName = ...

local MAX_ENTRIES = 200
local RATE_LIMIT = 10
local EARLY_MAX = 50
local SIGNATURE_BYTES = 200

local early = { errors = {}, warnings = {}, prints = {}, taken = false }
WuxianWorkshopEarly = early

local db                   -- WuxianWorkshopDB once ADDON_LOADED brought it: { errors, settings = { report }, dropped }
local errors = {}          -- signature -> entry; the same table as db.errors while report is not off
local windowAt, window = 0, 0
local pausedUntil
local dropped = 0
local seq = 0              -- orders entries seen in the same second (time() is whole seconds), for the eviction

-- the words of /wxw, in the language WoWBridge was set to (its panel or /wb lang), else the client's
local WORDS = {
	zhCN = {
		NAME = "无限工坊",
		STATUS = "报错上报：%s；已收集 %d 种报错（共 %d 次），因刷屏跳过 %d 条。/wxw report on|off 切换，/wxw clear 清空",
		UNSET = "未设置（无限工坊首次启动时会询问）",
		ON = "开（写入 SavedVariables，供无限工坊读取）",
		OFF = "关（只留在内存）",
		CLEARED = "已清空收集的报错",
		NOT_LOADED = "还没加载完",
		USAGE = "用法：/wxw report on|off|status（是否把插件报错写入 SavedVariables 供无限工坊读取）、/wxw clear（清空已收集的报错）",
	},
	enUS = {
		NAME = "Wuxian Workshop",
		STATUS = "error reports: %s; %d kinds of errors collected (%d in all), %d skipped as a flood. /wxw report on|off "
			.. "switches, /wxw clear empties",
		UNSET = "not decided yet (the Wuxian Workshop app asks when it first starts)",
		ON = "on (kept in SavedVariables for the Wuxian Workshop app)",
		OFF = "off (kept in memory only)",
		CLEARED = "the collected errors are cleared",
		NOT_LOADED = "not loaded yet",
		USAGE = "usage: /wxw report on|off|status (keep the addons' errors in SavedVariables for the Wuxian Workshop app), "
			.. "/wxw clear (empty what was collected)",
	},
}

local function W(key)
	local set = WoWBridgeDB and WoWBridgeDB.settings and WoWBridgeDB.settings.lang
	local lang = (set == "zhCN" or set == "enUS") and set or nil
	if not lang then
		local loc = GetLocale and GetLocale() or "enUS"
		lang = (loc == "zhCN" or loc == "zhTW") and "zhCN" or "enUS"
	end
	return WORDS[lang][key] or WORDS.enUS[key]
end

-- 无限工坊's chat prefix, as WoWBridge's (Skin.lua): the mark (Media\mark-wide) and the name in the site's gold
local MARK = "|TInterface\\AddOns\\" .. addonName .. "\\Media\\mark-wide:14:28|t "

local function Print(msg)
	DEFAULT_CHAT_FRAME:AddMessage(MARK .. "|cffd8a85a" .. W("NAME") .. "|r " .. tostring(msg):gsub("|", "||"))
end

local function Joined(...)
	local parts = {}
	for i = 1, select("#", ...) do parts[i] = tostring((select(i, ...))) end
	return table.concat(parts, " ")
end

local function Keep(list, item)
	if not early.taken and #list < EARLY_MAX then list[#list + 1] = item end
end

---------------------------------------------------------------------------
-- The player's error collection
---------------------------------------------------------------------------

local function Signature(message)
	return (message:gsub("%d+", "")):sub(1, SIGNATURE_BYTES)
end

-- the addon named first in the stack (Interface/AddOns/<name>/), else in the message
local function AddonOf(stack, message)
	return stack:match("Interface[/\\]AddOns[/\\]([^/\\%]:]+)[/\\]") or message:match("Interface[/\\]AddOns[/\\]([^/\\%]:]+)[/\\]")
end

local function Build()
	local version, build = GetBuildInfo()
	return tostring(version) .. "." .. tostring(build)
end

local function Count()
	local n = 0
	for _ in pairs(errors) do n = n + 1 end
	return n
end

-- the entry seen least recently goes (by its last time, then by the order within that second; a saved entry counts
-- as the oldest of its second)
local function EvictOldest()
	local oldest, at, atSeq
	for sig, e in pairs(errors) do
		local s = e.seq or 0
		if at == nil or e.last < at or (e.last == at and s < atSeq) then oldest, at, atSeq = sig, e.last, s end
	end
	if oldest then errors[oldest] = nil end
end

local function Collect(kind, message, stack, addon)
	local now = GetTime()
	if pausedUntil then
		if now < pausedUntil then
			dropped = dropped + 1
			if db then db.dropped = dropped end
			return
		end
		pausedUntil = nil
	end
	if now - windowAt >= 1 then window, windowAt = 0, now end
	window = window + 1
	if window > RATE_LIMIT then
		pausedUntil = now + 1
		dropped = dropped + 1
		if db then db.dropped = dropped end
		return
	end
	local t = time()
	seq = seq + 1
	local sig = Signature(message)
	local e = errors[sig]
	if e then
		e.count, e.last, e.seq = e.count + 1, t, seq
		return
	end
	while Count() >= MAX_ENTRIES do EvictOldest() end
	errors[sig] = { kind = kind, message = message:sub(1, 1000), addon = addon or AddonOf(stack, message), stack = stack:sub(1, 1500),
		count = 1, first = t, last = t, seq = seq, build = Build() }
end

local function OnError(msg, stack)
	Keep(early.errors, { msg = msg, stack = stack })
	Collect("ERR", msg, stack)
end

local function SetReport(on)
	db.settings.report = on
	if on then
		db.errors = errors           -- what was collected meanwhile is saved too
	else
		db.errors = {}               -- collected in memory only from now on
	end
end

local function Status()
	local report = db.settings.report
	local total = 0
	for _, e in pairs(errors) do total = total + e.count end
	Print(W("STATUS"):format(report == nil and W("UNSET") or (report and W("ON") or W("OFF")), Count(), total, dropped))
end

local function Clear()
	for sig in pairs(errors) do errors[sig] = nil end
	dropped, pausedUntil = 0, nil
	db.dropped = 0
	Print(W("CLEARED"))
end

local function Init()
	WuxianWorkshopDB = WuxianWorkshopDB or {}
	db = WuxianWorkshopDB
	db.settings = db.settings or {}
	dropped = (db.dropped or 0) + dropped
	db.dropped = dropped
	if db.settings.report == false then
		db.errors = {}
		return
	end
	local saved = db.errors or {}           -- errors from before the saved variables loaded join the saved ones
	for sig, e in pairs(errors) do
		local s = saved[sig]
		if s then
			s.count, s.last = s.count + e.count, math.max(s.last, e.last)
		else
			saved[sig] = e
		end
	end
	errors = saved
	db.errors = saved
	while Count() > MAX_ENTRIES do EvictOldest() end
end

SLASH_WUXIANWORKSHOP1 = "/wxw"
SlashCmdList.WUXIANWORKSHOP = function(msg)
	local cmd, rest = (msg or ""):match("^%s*(%S*)%s*(.-)%s*$")
	cmd, rest = (cmd or ""):lower(), (rest or ""):lower()
	if not db then
		Print(W("NOT_LOADED"))
	elseif cmd == "report" and (rest == "on" or rest == "off") then
		SetReport(rest == "on")
		Status()
	elseif cmd == "report" and (rest == "" or rest == "status") then
		Status()
	elseif cmd == "clear" then
		Clear()
	else
		Print(W("USAGE"))
	end
end

---------------------------------------------------------------------------
-- The hooks
---------------------------------------------------------------------------

if seterrorhandler and geterrorhandler then
	local previous = geterrorhandler()
	seterrorhandler(function(msg, ...)
		pcall(OnError, tostring(msg), debugstack and debugstack(3) or "")
		if previous then return previous(msg, ...) end
	end)
end

if setprinthandler and getprinthandler then        -- print() output only matters to WoWBridge: kept until it takes over
	local previous = getprinthandler()
	setprinthandler(function(...)
		if not early.taken then pcall(Keep, early.prints, Joined(...)) end
		return previous(...)
	end)
end

local events = CreateFrame("Frame")
events:RegisterEvent("ADDON_LOADED")
for _, e in ipairs({ "LUA_WARNING", "ADDON_ACTION_BLOCKED", "ADDON_ACTION_FORBIDDEN" }) do
	pcall(events.RegisterEvent, events, e)          -- an event this client does not know is skipped
end
events:SetScript("OnEvent", function(_, event, ...)
	if event == "ADDON_LOADED" then
		if ... == addonName then Init() end
	elseif event == "LUA_WARNING" then
		local text = Joined(...)
		pcall(Keep, early.warnings, text)
		pcall(Collect, "WARN", text, "")
	else
		local addon, func = ...
		pcall(Collect, "BLOCKED", ("%s %s %s"):format(event, tostring(addon), tostring(func)), "", tostring(addon))
	end
end)
