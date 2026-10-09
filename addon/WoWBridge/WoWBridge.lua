-- WoWBridge: draws frames on the screen that the companion program on this PC reads (see src/wuxianworkshop/), and reads
-- what the companion writes back into the font mailbox (Mailbox.lua, Link.lua). Debug output is Debug.lua, hot loading
-- Agent.lua, what addons hand the agent (WoWBridge.Bind: events and exposed functions) API.lua with Json.lua, dialogs
-- UI.lua, the words Locale.lua, the settings panel Panel.lua, the minimap button Minimap.lua; the
-- experiments (probe frame, self-test, sweep, polling and font tests) are the separate addon WoWBridge_Lab (addon/lab).
--   /wb                          the settings panel (status and commands in the chat when it is not there)
--   /wb help                     the commands
--   /wb log [errors|runs|output] the debug window (Console.lua): errors, prints and the agent's code as they happen
--   /wb link                     the link to the companion: state, mailbox slot, message counters
--   /wb on | off                 show the frame and connect, or stop the link and hide the frame (kept: autoLink)
--   /wb unlock | lock | reset    drag the frame elsewhere, fix it there, or put it back in the top-left corner
--   /wb lang auto|zh|en          the language of these messages and the panel (auto: the client's)
--   /wb send <text>              send a message to the companion (acknowledged through the font mailbox)
--   /wb burst [n]                send n test messages; a "BURST DONE ..." message reports delivery and latency
--   /wb long [bytes]             send one long test message (parts of 128x32 frames); "LONG DONE ..." follows
--   /wb stream [seconds]         experiments E2 / E3: a message every 0.3 s, each with a CRC the companion checks
--   /wb set <key> on|off         forwardDebug, hotLoad, autoLink or toasts; kept in WoWBridgeDB.settings (Config.lua has the
--                                defaults)
--   /wb parent ui|world          which frame the bus hangs from (WorldFrame may stay visible with Alt+Z)
--   /wb snap default|on|off      texture pixel snapping
--   /wb mode 0|1|2               colour mode of data frames
--   /wb diag                     print the diagnostics
local addonName, ns = ...
local Frame, L = ns.Frame, ns.L

local VERSION = "0.9.5"
local SWITCHES = { forwardDebug = true, hotLoad = true, autoLink = true, toasts = true }   -- /wb set <key> on|off

local WB = {}
_G.WoWBridge = WB
WB.VERSION = VERSION
WB.Dump = ns.Agent.Dump   -- (value, depth, bytes): a readable dump of a table, for code an agent runs
function WB.ToggleConsole(which) ns.Console.Toggle(which) end   -- the debug window (/wb log, the key binding)

local cfg = {}            -- Config.lua defaults under WoWBridgeDB.settings (see Init)
ns.cfg = cfg
local db
local session = math.random(0, 65535)
local msgId, toggle = 0, 0
local current    -- the frame on screen: { kind, W, H, cell, mode, ftype, payload, msg, toggle, hideAt, Redraw, Stale }
local entered = false
local lastPhys

-- chat text after 无限工坊's prefix (Skin.Prefix: the mark, the name in gold): a bare | starts an escape sequence (|1, |2,
-- |w ...), so plain text doubles it
local function Print(msg)
	DEFAULT_CHAT_FRAME:AddMessage(ns.Skin.Prefix(L.BRAND) .. tostring(msg):gsub("|", "||"))
end
ns.Print = Print

-- a setting: what /wb set or /wb parent / snap / mode kept for this character, else Config.lua
function ns.Setting(key)
	local v = cfg[key]
	if v == nil then v = (WoWBridge_Config or {})[key] end
	return v
end

---------------------------------------------------------------------------
-- Diagnostics: what this client is like (/wb diag; WoWBridge_Lab's probe frame carries them to the companion)
---------------------------------------------------------------------------

local function CVar(name)
	local v = GetCVar(name)
	return v == nil and "" or tostring(v)
end

local function Num(v)
	if type(v) ~= "number" then return tostring(v) end
	return ("%.6g"):format(v)
end

function WB.Diag()
	local pw, ph = GetPhysicalScreenSize()
	local _, build, _, iface = GetBuildInfo()
	local t = {
		"v=" .. VERSION, "b=" .. tostring(build), "if=" .. tostring(iface),
		"pw=" .. Num(pw), "ph=" .. Num(ph), "sw=" .. Num(GetScreenWidth()), "sh=" .. Num(GetScreenHeight()),
		"es=" .. Num(UIParent:GetEffectiveScale()), "bs=" .. Num(Frame.BusScale()),
		"cell=" .. Num(current and current.cell or cfg.cell), "par=" .. tostring(cfg.parent), "snap=" .. tostring(Frame.cfg.snap),
		"vis=" .. ((current and WoWBridgeBus and WoWBridgeBus:IsVisible()) and "1" or "0"),
		"gm=" .. CVar("Gamma"), "br=" .. CVar("Brightness"), "ct=" .. CVar("Contrast"),
		"ui=" .. CVar("uiscale"), "uu=" .. CVar("useUiScale"), "max=" .. CVar("gxMaximize"),
		"fbk=" .. CVar("maxFPSBk"), "ufbk=" .. CVar("useMaxFPSBk"), "rs=" .. CVar("RenderScale"),
		"sharp=" .. CVar("ResampleSharpness"), "snd=" .. CVar("Sound_EnableAllSound"), "vol=" .. CVar("Sound_MasterVolume"),
	}
	if GetScreenDPIScale then t[#t + 1] = "dpi=" .. Num(GetScreenDPIScale()) end
	if C_VideoOptions then
		local ok, sz = pcall(C_VideoOptions.GetCurrentGameWindowSize)
		if ok and type(sz) == "table" then t[#t + 1] = "gw=" .. Num(sz.x) .. "x" .. Num(sz.y) end
		local ok2, lin = pcall(C_VideoOptions.IsLinearEnabledOnStart)
		if ok2 then t[#t + 1] = "lin=" .. tostring(lin) end
	end
	if ns.Link.State() ~= "off" then
		t[#t + 1] = "lk=" .. ns.Link.State() .. ";mk=" .. ns.Mailbox.Next()
	end
	return table.concat(t, ";")
end

---------------------------------------------------------------------------
-- Frames on screen
---------------------------------------------------------------------------

local function Draw(f)
	local R, G, B = Frame.Build(f.W, f.H, f.mode, f.toggle, f.ftype, session, f.msg, f.payload, f.part, f.parts)
	if not R then
		Print(L.FRAME_ERROR:format(tostring(G)))
		return false
	end
	Frame.Show(f.W, f.H, f.cell, R, G, B)
	current = f
	return true
end

-- drawing for Link.lua: f = { kind, W, H, cell, mode, ftype, msg, payload }; the toggle cell flips on every call
function ns.ShowFrame(f)
	toggle = 1 - toggle
	f.toggle = toggle
	return Draw(f)
end

-- a frame with a new message id (1..65535, continued across /reload) for another addon's frames (WoWBridge_Lab's probe):
-- f = { kind, W, H, cell, mode, ftype, payload, hideAt, Redraw(f), Stale(f) }; while its kind is "probe" the link waits.
-- hideAt: hidden at that time; Stale: says when it should be drawn again; Redraw: draws it again (else as it is)
function ns.ShowNew(f)
	msgId = msgId % 65535 + 1
	db.lastMsg = msgId
	toggle = 1 - toggle
	f.msg, f.toggle = msgId, toggle
	return Draw(f)
end

function ns.CurrentFrame()
	return current
end

function WB.Off()
	Frame.Hide()
	current = nil
end

-- the same frame, drawn again: after a size change, or when its content is stale
local function Redraw()
	if not current then return end
	if current.Redraw then
		current.Redraw(current)
	else
		Draw(current)
	end
end

local function Tick()
	local pw, ph = GetPhysicalScreenSize()
	local phys = pw .. "x" .. ph
	local moved = lastPhys ~= nil and phys ~= lastPhys
	lastPhys = phys
	if moved then ns.Font.Rescale() end   -- the size events may not come for every change
	if not current then return end
	if current.hideAt and GetTime() >= current.hideAt then
		WB.Off()
		return
	end
	if moved or (current.Stale and current.Stale(current)) then Redraw() end
end

---------------------------------------------------------------------------
-- Messages to the companion go through the link (Link.lua), acknowledged through the font mailbox
---------------------------------------------------------------------------

function WB.Send(text)
	if not text or text == "" then
		Print(L.USAGE_SEND)
		return
	end
	Print(L.QUEUED:format(ns.Link.Send(text), ns.StateName(ns.Link.State())))
end

function WB.Burst(n)
	Print(L.BURST:format(ns.Link.Burst(n)))
end

function WB.Long(n)
	local id, parts = ns.Link.Long(n)
	Print(L.LONG:format(id, parts))
end

function WB.Stream(seconds)
	Print(L.STREAM:format(ns.Link.Stream(seconds)))
end

function WB.LinkStatus()
	Print(ns.Link.Status())
end

-- the link on or off, kept for this character (autoLink): off stops it and hides the frame
function WB.SetLink(on)
	ns.Set("autoLink", on)
	if on and entered and ns.Link.State() == "off" then
		ns.Link.Start(db, Print)
		Print(L.LINK_STARTED)
	elseif not on and ns.Link.State() ~= "off" then
		ns.Link.Stop()
		current = nil
		Print(L.LINK_STOPPED)
	end
end

function WB.Unlock()
	Frame.SetUnlocked(true, function(x, y)
		ns.Set("frameX", x)
		ns.Set("frameY", y)
	end, L.MOVER)
	Print(L.UNLOCKED)
end

function WB.Lock()
	Frame.SetUnlocked(false)
	Print(L.LOCKED:format(Frame.Offset()))
end

function WB.ResetPosition()
	ns.Set("frameX", 0)
	ns.Set("frameY", 0)
	Frame.Relayout()
	Print(L.RESET)
end

-- "auto", "zhCN" or "enUS", kept for this character
function WB.SetLanguage(setting)
	ns.Set("lang", setting ~= "auto" and setting or nil)
	ns.SetLanguage(setting)
	Print(L.LANG:format(ns.LanguageName(setting)))
end

---------------------------------------------------------------------------
-- Settings, events, slash commands
---------------------------------------------------------------------------

local function Init()
	WoWBridgeDB = WoWBridgeDB or {}
	db = WoWBridgeDB
	ns.db = db
	db.settings = db.settings or {}
	for k, v in pairs(WoWBridge_Config or {}) do cfg[k] = v end
	for k, v in pairs(db.settings) do cfg[k] = v end
	Frame.cfg.parent, Frame.cfg.snap, Frame.cfg.x, Frame.cfg.y = cfg.parent, cfg.snap, cfg.frameX or 0, cfg.frameY or 0
	ns.SetLanguage(cfg.lang)
	msgId = db.lastMsg or 0
	-- never the last session's id: the companion tells a /reload by a new one (and keeps what it knows per id)
	if session == db.lastSession then session = (session + 1 + math.random(0, 65533)) % 65536 end
	db.lastSession = session
	ns.session = session
end

-- a setting kept for this character in WoWBridgeDB.settings (nil: back to Config.lua's)
local function Set(key, value)
	cfg[key] = value
	db.settings[key] = value
	Frame.cfg.parent, Frame.cfg.snap, Frame.cfg.x, Frame.cfg.y = cfg.parent, cfg.snap, cfg.frameX or 0, cfg.frameY or 0
end
ns.Set = Set
WB.Print = Print

local events = CreateFrame("Frame")
events:RegisterEvent("ADDON_LOADED")
events:RegisterEvent("PLAYER_ENTERING_WORLD")
events:RegisterEvent("DISPLAY_SIZE_CHANGED")
pcall(events.RegisterEvent, events, "GX_RESTARTED")
pcall(events.RegisterEvent, events, "UI_SCALE_CHANGED")
events:SetScript("OnEvent", function(_, event, arg1)
	if event == "ADDON_LOADED" then
		if arg1 == addonName then Init() end
	elseif event == "PLAYER_ENTERING_WORLD" then
		if not entered then
			entered = true
			Print(L.LOADED:format(VERSION))
			C_Timer.NewTicker(1, Tick)
			if cfg.autoLink ~= false then ns.Link.Start(db, Print) end
		end
	else
		Frame.Relayout()
		ns.Font.Rescale()
		Redraw()
	end
end)

SLASH_WOWBRIDGE1 = "/wb"
SLASH_WOWBRIDGE2 = "/wowbridge"
SlashCmdList.WOWBRIDGE = function(msg)
	local cmd, rest = (msg or ""):match("^%s*(%S*)%s*(.-)%s*$")
	cmd = (cmd or ""):lower()
	if cmd == "burst" then
		WB.Burst(tonumber(rest))
	elseif cmd == "stream" then
		WB.Stream(tonumber(rest))
	elseif cmd == "long" then
		WB.Long(tonumber(rest))
	elseif cmd == "link" or cmd == "queue" then
		WB.LinkStatus()
	elseif cmd == "send" then
		WB.Send(rest)
	elseif cmd == "set" then
		local key, value = rest:match("^(%S+)%s+(%S+)$")
		if key and SWITCHES[key] and (value == "on" or value == "off") then
			if key == "autoLink" then
				WB.SetLink(value == "on")
			else
				Set(key, value == "on")
			end
			Print(L.SET:format(key, value))
		else
			Print(L.USAGE_SET)
		end
	elseif cmd == "on" or cmd == "off" then
		WB.SetLink(cmd == "on")
	elseif cmd == "unlock" then
		WB.Unlock()
	elseif cmd == "lock" then
		WB.Lock()
	elseif cmd == "reset" then
		WB.ResetPosition()
	elseif cmd == "lang" then
		local want = ({ auto = "auto", zh = "zhCN", zhcn = "zhCN", en = "enUS", enus = "enUS" })[rest:lower()]
		if want then WB.SetLanguage(want) else Print(L.USAGE_LANG) end
	elseif cmd == "parent" and (rest == "ui" or rest == "world") then
		Set("parent", rest == "world" and "WorldFrame" or "UIParent")
		Frame.Relayout()
		Redraw()
		Print(L.PARENT:format(cfg.parent))
	elseif cmd == "snap" and (rest == "default" or rest == "on" or rest == "off") then
		Set("snap", rest)
		Redraw()
		Print(L.SNAP:format(rest))
	elseif cmd == "mode" and (rest == "0" or rest == "1" or rest == "2") then
		Set("mode", tonumber(rest))
		Print(L.MODE:format(rest))
	elseif cmd == "log" or cmd == "console" then
		local which = rest:lower()
		if which == "" or which == "all" or which == "errors" or which == "runs" or which == "output" then
			WB.ToggleConsole(which ~= "" and which or nil)
		else
			Print(L.USAGE_LOG)
		end
	elseif cmd == "diag" then
		local d = WB.Diag()
		for i = 1, #d, 200 do Print(d:sub(i, i + 199)) end
	elseif cmd == "" and ns.Panel then
		ns.Panel.Toggle()
	else
		Print(L.STATUS:format(VERSION, session, current and L.ON_SCREEN:format(current.kind, current.msg) or L.NO_FRAME))
		Print(ns.Link.Status())
		Print(L.HELP)
	end
end
