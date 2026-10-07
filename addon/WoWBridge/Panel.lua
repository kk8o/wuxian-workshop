-- WoWBridge's panel (/wb, the minimap button, Options > AddOns) in 无限工坊's look (Skin.lua), in three tabs:
--   概览 (overview): is the app connected (and what to do when not), the switch that connects, what the agent ran and the
--        Lua errors since the last reload (调试输出 opens the debug window, Console.lua), and a notice with its button when
--        something needs doing (the frame is unlocked, hot loading is off);
--   设置 (settings): the frame's place (unlock to drag it, lock, back to the corner), the switches, the language;
--   诊断 (diagnostics): the link's numbers and tests, for finding out why it does not connect.
-- It opens on 概览, or on the tab it showed last since the last reload. Everything it changes is kept for this character
-- in WoWBridgeDB.settings, the same as the /wb commands do; the words are Locale.lua's and change with the language.
local _, ns = ...
local L, S = ns.L, ns.Skin
local C = S.C

local P = {}
ns.Panel = P

local WIDTH, HEIGHT, PAD = 400, 440, 20
local INNER = WIDTH - 2 * PAD
local TOP, BOTTOM = 106, 64             -- the pages' area: below the tabs, above the ornament and the footer
local TABS = { "overview", "settings", "diag" }
local TAB_WORDS = { overview = "TAB_OVERVIEW", settings = "TAB_SETTINGS", diag = "TAB_DIAG" }
local STATE_WORDS = { online = "OV_ONLINE", hello = "OV_HELLO", off = "OV_OFF" }
local WHY_WORDS = { online = "WHY_ONLINE", hello = "WHY_HELLO", off = "WHY_OFF" }
local panel, ticker, options
local labels = {}         -- what Refresh relabels: { object, key }
local headings = {}       -- section headings: { words, key }
local shown = "overview"  -- the tab in front

local function WB() return _G.WoWBridge end

local function Page(id)
	local page = CreateFrame("Frame", nil, panel)
	page:SetPoint("TOPLEFT", panel, "TOPLEFT", 0, -TOP)
	page:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", 0, BOTTOM)
	page:Hide()
	panel.pages[id] = page
	return page
end

local function Heading(page, y, key)
	local fs = S.Heading(page, PAD, y, INNER)
	headings[#headings + 1] = { fs, key }
	return fs
end

local function Line(page, x, y, color, width)
	local fs = S.Text(page, "GameFontHighlightSmall", color)
	fs:SetPoint("TOPLEFT", page, "TOPLEFT", x, y)
	fs:SetWidth(width or (WIDTH - PAD - x))
	return fs
end

local function Check(page, y, key, onClick, tipKey)
	local cb = S.Check(page, INNER)
	cb:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, y)
	cb:SetScript("OnClick", function(self) onClick(self:GetChecked() and true or false) end)
	if tipKey then cb.tip = function() return L[tipKey] end end
	labels[#labels + 1] = { cb.label, key }
	return cb
end

local function Button(page, x, y, width, key, onClick, kind)
	local b = S.Button(page, width, 24, kind)
	b:SetPoint("TOPLEFT", page, "TOPLEFT", x, y)
	b:SetScript("OnClick", onClick)
	if key then labels[#labels + 1] = { b, key } end
	return b
end

-- how long ago a GetTime() was, in words
local function Ago(at)
	local s = math.max(0, GetTime() - at)
	if s < 60 then return L.AGO_S:format(math.floor(s)) end
	if s < 3600 then return L.AGO_M:format(math.floor(s / 60)) end
	return L.AGO_H:format(math.floor(s / 3600))
end

local function BuildOverview()
	local page = Page("overview")
	panel.dot = S.Dot(page, 12)
	panel.dot:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, -6)
	panel.state = S.Text(page, "GameFontNormalLarge")
	panel.state:SetPoint("LEFT", panel.dot, "RIGHT", 10, 0)
	panel.why = Line(page, PAD, -28, C.muted)
	panel.link = Check(page, -64, "OPT_LINK", function(on) WB().SetLink(on) P.Refresh() end, "OPT_LINK_TIP")
	headings[#headings + 1] = { S.Heading(page, PAD, -102, INNER - 108), "SECTION_SESSION" }   -- its line up to the button
	panel.console = Button(page, WIDTH - PAD - 96, -98, 96, "BTN_CONSOLE", function()
		ns.Console.Show(ns.Debug.stats.errors > 0 and "errors" or nil)
	end)
	panel.runs = Line(page, PAD, -126)
	panel.last = Line(page, PAD, -144, C.muted)
	panel.errors = Line(page, PAD, -162)
	-- something to do: the words and the button that does it (Refresh decides which, if any)
	panel.notice = Line(page, PAD, -196, C.goldL, INNER - 108)
	panel.act = Button(page, WIDTH - PAD - 96, -190, 96, nil, function()
		if panel.act.what == "lock" then WB().Lock() else ns.Set("hotLoad", true) end
		P.Refresh()
	end, "primary")
	panel.hint = Line(page, PAD, -236, C.muted)
	labels[#labels + 1] = { panel.hint, "FOOTER" }
end

local function BuildSettings()
	local page = Page("settings")
	Heading(page, -4, "SECTION_POSITION")
	panel.unlock = Button(page, PAD, -26, 140, nil, function()
		if ns.Frame.IsUnlocked() then WB().Lock() else WB().Unlock() end
		P.Refresh()
	end)
	panel.reset = Button(page, PAD + 148, -26, 120, "BTN_RESET", function() WB().ResetPosition() P.Refresh() end)
	panel.position = Line(page, PAD, -58, C.muted)
	Heading(page, -90, "SECTION_OPTIONS")
	panel.debug = Check(page, -112, "OPT_DEBUG", function(on) ns.Set("forwardDebug", on) P.Refresh() end)
	panel.hot = Check(page, -134, "OPT_HOTLOAD", function(on) ns.Set("hotLoad", on) P.Refresh() end)
	panel.minimap = Check(page, -156, "OPT_MINIMAP", function(on)
		ns.Set("minimapHide", not on)
		if ns.MinimapButton then ns.MinimapButton.Update() end
	end)
	panel.toasts = Check(page, -178, "OPT_TOASTS", function(on) ns.Set("toasts", on) P.Refresh() end)
	panel.langLabel = Line(page, PAD, -220, nil, 84)
	panel.langs = {}
	for i, setting in ipairs(ns.LANGUAGES) do
		local b = S.Button(page, 80, 24)
		b:SetPoint("TOPLEFT", page, "TOPLEFT", PAD + 88 + (i - 1) * 86, -214)
		b:SetScript("OnClick", function() WB().SetLanguage(setting) end)
		b.setting = setting
		panel.langs[i] = b
	end
end

local function BuildDiagnostics()
	local page = Page("diag")
	local third, half = (INNER - 16) / 3, (INNER - 8) / 2
	Heading(page, -4, "SECTION_LINK")
	panel.st1 = Line(page, PAD, -26)
	panel.st2 = Line(page, PAD, -44, C.muted)
	panel.st3 = Line(page, PAD, -62, C.muted)
	Heading(page, -94, "SECTION_TESTS")
	Button(page, PAD, -116, third, "BTN_SEND", function() WB().Send(L.TEST_TEXT) end)
	Button(page, PAD + third + 8, -116, third, "BTN_BURST", function() WB().Burst(10) end)
	Button(page, PAD + 2 * (third + 8), -116, third, "BTN_LONG", function() WB().Long(6000) end)
	Button(page, PAD, -144, half, "BTN_RESTART", function()
		WB().SetLink(false)
		WB().SetLink(true)
		P.Refresh()
	end)
	Button(page, PAD + half + 8, -144, half, "BTN_STATUS", function() WB().LinkStatus() end)
	panel.diagNote = Line(page, PAD, -184, C.muted)
	labels[#labels + 1] = { panel.diagNote, "DIAG_NOTE" }
end

-- the frame is drawn above everything (the app has to see it), so a panel over its room would lose that corner:
-- centred on the screen unless that overlaps the room, else right of the room, or under it when the screen is too narrow
local function Place()
	panel:ClearAllPoints()
	local px = S.Pixel(UIParent)                       -- a pixel in UIParent's units (the frame's place is in pixels)
	local fx, fy = ns.Frame.Offset()
	local fw, fh = ns.Frame.Room()
	local right, bottom = (fx + fw) * px + 12, (fy + fh) * px + 12
	local sw, sh = UIParent:GetWidth(), UIParent:GetHeight()
	local left, top = (sw - WIDTH) / 2, (sh - HEIGHT) / 2 - 40
	if left >= right or top >= bottom then
		panel:SetPoint("CENTER", UIParent, "CENTER", 0, 40)
	elseif right + WIDTH <= sw then
		panel:SetPoint("TOPLEFT", UIParent, "TOPLEFT", right, -math.max(top, 0))
	else
		panel:SetPoint("TOPLEFT", UIParent, "TOPLEFT", math.max(left, 0), -bottom)
	end
end

-- one tab in front
function P.Select(id)
	if not panel or not panel.pages[id] then return end
	shown = id
	for key, page in pairs(panel.pages) do page:SetShown(key == id) end
	panel.tabs.Select(id)
end

local function Build()
	panel = S.Window("WoWBridgePanel", WIDTH, HEIGHT)
	panel:Hide()
	tinsert(UISpecialFrames, "WoWBridgePanel")        -- Escape closes it
	S.Close(panel)

	-- the head: the logo, the name, the part and its version
	local logo = panel:CreateTexture(nil, "ARTWORK")
	logo:SetTexture(S.LOGO)
	logo:SetSize(40, 40)
	logo:SetPoint("TOPLEFT", panel, "TOPLEFT", PAD, -16)
	panel.titleText = S.Text(panel, "GameFontNormalLarge", C.goldL)
	panel.titleText:SetPoint("TOPLEFT", logo, "TOPRIGHT", 12, -4)
	panel.version = S.Text(panel, "GameFontHighlightSmall", C.muted)
	panel.version:SetPoint("TOPLEFT", panel.titleText, "BOTTOMLEFT", 0, -5)

	panel.tabs = S.Tabs(panel, PAD, -68, INNER, TABS, P.Select)
	panel.pages = {}
	BuildOverview()
	BuildSettings()                  -- (not named Settings: that is the client's settings API, used below)
	BuildDiagnostics()
	S.Orn(panel, PAD, -(HEIGHT - 58), INNER)

	-- the footer, under every tab: the website and a button that puts it in a box to copy
	panel.siteLabel = S.Text(panel, "GameFontHighlightSmall", C.muted)
	panel.siteLabel:SetPoint("BOTTOMLEFT", panel, "BOTTOMLEFT", PAD, 20)
	panel.site = S.Text(panel, "GameFontHighlightSmall", C.goldL)
	panel.site:SetPoint("LEFT", panel.siteLabel, "RIGHT", 8, 0)
	panel.site:SetText((S.WEBSITE:gsub("^https://", "")))
	panel.copy = S.Button(panel, 96, 22)
	panel.copy:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -PAD, 15)
	labels[#labels + 1] = { panel.copy, "BTN_COPY" }
	panel.copyBox = S.CopyBox(panel, INNER - 104, function()
		panel.site:Show()
		panel.siteLabel:Show()
	end)
	panel.copyBox:SetPoint("BOTTOMLEFT", panel, "BOTTOMLEFT", PAD, 15)
	panel.copy:SetScript("OnClick", function()
		panel.site:Hide()
		panel.siteLabel:Hide()
		panel.copyBox.Open(S.WEBSITE)
	end)
	panel.copy.tip = function() return L.COPY_TIP end

	panel:SetScript("OnShow", function()
		P.Refresh()
		ticker = C_Timer.NewTicker(1, P.Refresh)
	end)
	panel:SetScript("OnHide", function()
		if ticker then ticker:Cancel() ticker = nil end
	end)
end

-- every word and every state, again: on show, every second while shown, after a change of language
function P.Refresh()
	if not panel then return end
	panel.titleText:SetText(L.BRAND)
	panel.version:SetText(L.SUBTITLE:format(WB().VERSION or "?"))
	panel.siteLabel:SetText(L.WEBSITE)
	panel.tabs.SetLabels(function(id) return L[TAB_WORDS[id]] end)
	for _, h in ipairs(headings) do h[1]:SetText(L[h[2]]) end
	for _, w in ipairs(labels) do w[1]:SetText(L[w[2]]) end

	-- 概览
	local n = ns.Link.Numbers()
	local state = STATE_WORDS[n.state] and n.state or "hello"
	local color = S.StateColor(n.state)
	S.Tint(panel.dot, C[color])
	panel.state:SetText(L[STATE_WORDS[state]])
	S.Ink(panel.state, C[color == "muted" and "fg" or color])
	panel.why:SetText(L[WHY_WORDS[state]])
	panel.link:SetChecked(n.state ~= "off")
	local runs = ns.Agent.stats
	panel.last:SetShown(runs.runs > 0)
	panel.errors:ClearAllPoints()                          -- right under the runs while there is no last one
	panel.errors:SetPoint("TOPLEFT", panel.pages.overview, "TOPLEFT", PAD, runs.runs > 0 and -162 or -144)
	if runs.runs == 0 then
		panel.runs:SetText(L.RUNS_NONE)
	else
		panel.runs:SetText(L.RUNS:format(runs.runs, runs.failed))
		local last = runs.last
		local what = last.name:sub(1, 1) == "=" and L.RUN_SNIPPET or last.name:gsub("^@Interface[/\\]AddOns[/\\]", "")
		local result = last.ok and ("|cff" .. S.HEX.ok .. L.AGENT_OK .. "|r") or ("|cff" .. S.HEX.bad .. L.AGENT_ERROR .. "|r")
		panel.last:SetText(L.LAST_RUN:format(what, result, Ago(last.at)))
	end
	local errs = ns.Debug.stats
	panel.errors:SetText(L.ERRORS:format(errs.errors, errs.warnings, errs.blocked)
		.. (ns.Setting("forwardDebug") == false and L.NOT_SENT or ""))
	local unlocked = ns.Frame.IsUnlocked()
	local todo = unlocked and "lock" or (ns.Setting("hotLoad") == false and "hotload") or nil
	panel.notice:SetText(todo == "lock" and L.NOTICE_UNLOCKED or todo == "hotload" and L.NOTICE_HOTLOAD or "")
	panel.act.what = todo
	panel.act:SetText(todo == "lock" and L.BTN_LOCK or L.BTN_TURN_ON)
	panel.act:SetShown(todo ~= nil)

	-- 设置
	panel.unlock:SetText(unlocked and L.BTN_LOCK or L.BTN_UNLOCK)
	panel.unlock.kind = unlocked and "primary" or nil        -- unlocked: locking is the thing to do next
	S.Paint(panel.unlock)
	panel.position:SetText(L.POSITION:format(ns.Frame.Offset()))
	panel.debug:SetChecked(ns.Setting("forwardDebug") ~= false)
	panel.hot:SetChecked(ns.Setting("hotLoad") ~= false)
	panel.minimap:SetChecked(not ns.Setting("minimapHide"))
	panel.toasts:SetChecked(ns.Setting("toasts") ~= false)
	panel.langLabel:SetText(L.LANGUAGE)
	local current = ns.Setting("lang") or "auto"
	for _, b in ipairs(panel.langs) do
		b:SetText(ns.LanguageName(b.setting, true))
		b.selected = b.setting == current                    -- the one in force: marked, not clickable
		b:SetEnabled(not b.selected)
		S.Paint(b)
	end

	-- 诊断
	local left, hb = ns.Link.SlotsLeft(), n.hb or 15
	panel.st1:SetText(L.ST_LINK:format("|cff" .. S.HEX[color] .. ns.StateName(n.state) .. "|r")
		.. (ns.session and ("  ·  " .. L.ST_SESSION:format(ns.session)) or ""))
	panel.st2:SetText(L.ST_SLOTS:format(left, left * hb / 3600))
	panel.st3:SetText(L.ST_QUEUE:format(n.waiting, n.acked, hb))
end

-- tab: the one to show ("overview", "settings", "diag"); else the one shown last
function P.Show(tab)
	if not panel then Build() end
	if not (panel.IsUserPlaced and panel:IsUserPlaced()) then Place() end   -- dragged by the player: theirs
	P.Select(tab or shown)
	panel:Raise()                                  -- in front of the debug window, if that is open there
	panel:Show()
end

function P.Toggle()
	if panel and panel:IsShown() then panel:Hide() else P.Show() end
end

-- Options > AddOns: the logo, the name, a line on what it is and a button to the panel (which lives outside the options
-- window)
local function RelabelOptions()
	if not options then return end
	options.title:SetText(L.BRAND)
	options.version:SetText(L.SUBTITLE:format(WB().VERSION or "?"))
	options.text:SetText(L.OPTIONS_TEXT)
	options.open:SetText(L.OPTIONS_OPEN)
	options.siteLabel:SetText(L.WEBSITE)
	options.copy:SetText(L.BTN_COPY)
end

local function RegisterOptions()
	if not (Settings and Settings.RegisterCanvasLayoutCategory and Settings.RegisterAddOnCategory) then return end
	local canvas = CreateFrame("Frame")
	local logo = canvas:CreateTexture(nil, "ARTWORK")
	logo:SetTexture(S.LOGO)
	logo:SetSize(56, 56)
	logo:SetPoint("TOPLEFT", canvas, "TOPLEFT", 20, -20)
	options = {}
	options.title = S.Text(canvas, "GameFontNormalLarge", C.goldL)
	options.title:SetPoint("TOPLEFT", logo, "TOPRIGHT", 14, -10)
	options.version = S.Text(canvas, "GameFontHighlightSmall", C.muted)
	options.version:SetPoint("TOPLEFT", options.title, "BOTTOMLEFT", 0, -6)
	options.text = S.Text(canvas, "GameFontHighlight", C.fg)
	options.text:SetPoint("TOPLEFT", logo, "BOTTOMLEFT", 0, -18)
	options.text:SetWidth(520)
	options.open = S.Button(canvas, 180, 28, "primary")
	options.open:SetPoint("TOPLEFT", options.text, "BOTTOMLEFT", 0, -16)
	options.open:SetScript("OnClick", function()
		if SettingsPanel and SettingsPanel:IsShown() then HideUIPanel(SettingsPanel) end
		P.Show()
	end)
	options.siteLabel = S.Text(canvas, "GameFontHighlight", C.muted)
	options.siteLabel:SetPoint("TOPLEFT", options.open, "BOTTOMLEFT", 0, -22)
	options.site = S.Text(canvas, "GameFontHighlight", C.goldL)
	options.site:SetPoint("LEFT", options.siteLabel, "RIGHT", 10, 0)
	options.site:SetText(S.WEBSITE)
	options.copy = S.Button(canvas, 110, 24)
	options.copy:SetPoint("LEFT", options.site, "RIGHT", 14, 0)
	options.copyBox = S.CopyBox(canvas, 360)
	options.copyBox:SetPoint("TOPLEFT", options.siteLabel, "BOTTOMLEFT", 0, -10)
	options.copy:SetScript("OnClick", function() options.copyBox.Open(S.WEBSITE) end)
	RelabelOptions()
	local ok, category = pcall(Settings.RegisterCanvasLayoutCategory, canvas, L.PANEL_TITLE)
	if ok and category then pcall(Settings.RegisterAddOnCategory, category) end
end

ns.OnLanguage(function()
	P.Refresh()
	RelabelOptions()
end)

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_LOGIN")
events:SetScript("OnEvent", function()
	RegisterOptions()
	events:UnregisterAllEvents()
end)
