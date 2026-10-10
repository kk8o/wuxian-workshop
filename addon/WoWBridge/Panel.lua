-- WoWBridge's panel (/wb, the minimap button, Options > AddOns) in 无限工坊's look (Skin.lua), in three tabs:
--   概览 (overview): a card with the link's state (and, when the app is not there, the button that reconnects or shows
--        the frame), a notice with its button when something needs doing (the frame unlocked, hot loading off), this
--        session's numbers (the agent's runs and failures, Lua errors, blocked actions) with the way to the debug window,
--        and its latest lines (Console.lua), each opening the debug window on its kind;
--   设置 (settings): 帧码 (the link's switch, the frame's place), Agent (hot loading, what goes to the app, the notice),
--        界面 (the minimap button, the language);
--   诊断 (diagnostics): the link's numbers, the link tests, dropping what waits to go up, and the diagnostics in a box to
--        copy (for a bug report).
-- It opens on 概览, or on the tab it showed last since the last reload. Everything it changes is kept for this character
-- in WoWBridgeDB.settings, the same as the /wb commands do; the words are Locale.lua's and change with the language.
local _, ns = ...
local L, S = ns.L, ns.Skin
local C = S.C

local P = {}
ns.Panel = P

local WIDTH, HEIGHT, PAD = 400, 470, 20
local INNER = WIDTH - 2 * PAD
local TOP, BOTTOM = 106, 64             -- the pages' area: below the tabs, above the ornament and the footer
local GAP = 12                          -- between the overview's blocks
local ROW = 18                          -- a line of small words
local TABS = { "overview", "settings", "diag" }
local TAB_WORDS = { overview = "TAB_OVERVIEW", settings = "TAB_SETTINGS", diag = "TAB_DIAG" }
local STATE_WORDS = { online = "OV_ONLINE", hello = "OV_HELLO", off = "OV_OFF" }
local WHY_WORDS = { online = "WHY_ONLINE", hello = "WHY_HELLO", off = "WHY_OFF" }
local TILES = { "TILE_RUNS", "TILE_FAILED", "TILE_ERRORS", "TILE_BLOCKED" }
local KV = { "KV_STATE", "KV_SLOTS", "KV_QUEUE", "KV_HEARTBEAT", "KV_FRAME" }
local RECENT = 3                        -- the latest lines on 概览, at most
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

local function Heading(page, y, key, width)
	local fs = S.Heading(page, PAD, y, width or INNER)
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

-- a plain block: a fill and hairlines round it
local function Block(parent, fill, edge)
	local f = CreateFrame("Frame", nil, parent)
	f.bg = f:CreateTexture(nil, "BACKGROUND")
	f.bg:SetAllPoints()
	S.Fill(f.bg, fill)
	S.Edges(S.Border(f), edge)
	return f
end

-- light gold words that act as a link, right-aligned in w ("调试输出 →"); lighter still under the mouse
local function TextButton(parent, w, key, onClick)
	local b = CreateFrame("Button", nil, parent)
	b:SetSize(w, ROW)
	b.label = S.Text(b, "GameFontHighlightSmall", C.goldL)
	b.label:SetPoint("RIGHT", b, "RIGHT")
	b.label:SetJustifyH("RIGHT")
	b:SetScript("OnEnter", function() S.Ink(b.label, C.fg) end)
	b:SetScript("OnLeave", function() S.Ink(b.label, C.goldL) end)
	b:SetScript("OnClick", onClick)
	labels[#labels + 1] = { b.label, key }
	return b
end

local function BuildOverview()
	local page = Page("overview")
	-- the state: what the link is doing, and what to do while the app is not there
	local card = Block(page, C.field, C.line)
	card:SetWidth(INNER)
	card.dot = S.Dot(card, 9)
	card.dot:SetPoint("TOPLEFT", card, "TOPLEFT", 14, -16)
	card.state = S.Text(card, "GameFontNormalLarge")
	card.state:SetPoint("TOPLEFT", card, "TOPLEFT", 35, -11)
	card.why = S.Text(card, "GameFontHighlightSmall", C.muted)
	card.why:SetPoint("TOPLEFT", card, "TOPLEFT", 35, -32)
	card.why:SetWidth(INNER - 49)
	card.act = S.Button(card, 88, 24, "primary")
	card.act:SetPoint("TOPLEFT", card, "TOPLEFT", 35, -54)
	card.act:SetScript("OnClick", function()
		if ns.Link.State() == "off" then WB().SetLink(true) else WB().Reconnect() end
		P.Refresh()
	end)
	card.hint = S.Text(card, "GameFontHighlightSmall", C.muted)
	card.hint:SetPoint("LEFT", card.act, "RIGHT", 10, 0)
	labels[#labels + 1] = { card.hint, "HELLO_HINT" }
	panel.card = card
	-- something to do: the words and the button that does it (Refresh decides which, if any)
	local notice = Block(page, C.goldSoft, C.goldD)
	notice:SetSize(INNER, 36)
	notice.text = S.Text(notice, "GameFontHighlightSmall", C.goldL)
	notice.text:SetPoint("LEFT", notice, "LEFT", 12, 0)
	notice.text:SetWidth(INNER - 96)
	notice.act = S.Button(notice, 64, 22, "primary")
	notice.act:SetPoint("RIGHT", notice, "RIGHT", -8, 0)
	notice.act:SetScript("OnClick", function()
		if notice.what == "lock" then WB().Lock() else ns.Set("hotLoad", true) end
		P.Refresh()
	end)
	panel.notice = notice
	-- this session: its numbers, and the way to the debug window
	panel.session = Heading(page, 0, "SECTION_SESSION", INNER - 100)
	panel.console = TextButton(page, 92, "BTN_CONSOLE", function()
		ns.Console.Show(ns.Debug.stats.errors > 0 and "errors" or nil)
	end)
	panel.tiles = {}
	local w = (INNER - 3 * 8) / 4
	for i, key in ipairs(TILES) do
		local tile = Block(page, C.panel2, C.line)
		tile:SetSize(w, 46)
		tile.num = S.Text(tile, "GameFontHighlightLarge")
		tile.num:SetPoint("TOP", tile, "TOP", 0, -7)
		tile.num:SetJustifyH("CENTER")
		tile.label = S.Text(tile, "GameFontHighlightSmall", C.muted)
		tile.label:SetPoint("BOTTOM", tile, "BOTTOM", 0, 6)
		tile.label:SetJustifyH("CENTER")
		labels[#labels + 1] = { tile.label, key }
		panel.tiles[i] = tile
	end
	panel.held = Line(page, PAD, 0, C.muted, INNER)
	labels[#labels + 1] = { panel.held, "HELD" }
	-- the latest lines of the debug window, newest first: a click opens it on their kind
	panel.recentLabel = Line(page, PAD, 0, C.muted, INNER)
	labels[#labels + 1] = { panel.recentLabel, "RECENT" }
	panel.recentNone = Line(page, PAD, 0, C.muted, INNER)
	labels[#labels + 1] = { panel.recentNone, "RECENT_NONE" }
	panel.recent = {}
	for i = 1, RECENT do
		local row = CreateFrame("Button", nil, page)
		row:SetSize(INNER, ROW)
		row.time = S.Text(row, "GameFontHighlightSmall", C.muted)
		row.time:SetPoint("LEFT", row, "LEFT")
		row.tag = S.Text(row, "GameFontHighlightSmall")
		row.tag:SetPoint("LEFT", row, "LEFT", 46, 0)
		row.text = S.Text(row, "GameFontHighlightSmall")
		row.text:SetPoint("LEFT", row, "LEFT", 92, 0)
		row.text:SetPoint("RIGHT", row, "RIGHT")
		row.text:SetWordWrap(false)
		row:SetScript("OnClick", function(self) ns.Console.Show(self.tab) end)
		row:SetScript("OnEnter", function(self) S.Ink(self.text, C.goldL) end)
		row:SetScript("OnLeave", function(self) S.Ink(self.text, C.fg) end)
		panel.recent[i] = row
	end
end

local function BuildSettings()
	local page = Page("settings")
	Heading(page, -4, "SECTION_FRAME")
	panel.link = Check(page, -26, "OPT_LINK", function(on) WB().SetLink(on) P.Refresh() end, "OPT_LINK_TIP")
	panel.position = Line(page, PAD, -58, C.muted, 150)
	panel.reset = Button(page, WIDTH - PAD - 96, -53, 96, "BTN_RESET", function() WB().ResetPosition() P.Refresh() end)
	panel.unlock = Button(page, WIDTH - PAD - 96 - 8 - 64, -53, 64, nil, function()
		if ns.Frame.IsUnlocked() then WB().Lock() else WB().Unlock() end
		P.Refresh()
	end)
	Heading(page, -92, "SECTION_AGENT")
	panel.hot = Check(page, -114, "OPT_HOTLOAD", function(on) ns.Set("hotLoad", on) P.Refresh() end)
	panel.debug = Check(page, -136, "OPT_DEBUG", function(on) ns.Set("forwardDebug", on) P.Refresh() end)
	panel.toasts = Check(page, -158, "OPT_TOASTS", function(on) ns.Set("toasts", on) P.Refresh() end)
	Heading(page, -192, "SECTION_UI")
	panel.minimap = Check(page, -214, "OPT_MINIMAP", function(on)
		ns.Set("minimapHide", not on)
		if ns.MinimapButton then ns.MinimapButton.Update() end
	end)
	panel.langLabel = Line(page, PAD, -248, nil, 100)
	panel.langs = {}
	for i, setting in ipairs(ns.LANGUAGES) do
		local b = S.Button(page, 72, 24)
		b:SetPoint("TOPLEFT", page, "TOPLEFT", WIDTH - PAD - 3 * 72 - 2 * 8 + (i - 1) * 80, -243)
		b:SetScript("OnClick", function() WB().SetLanguage(setting) end)
		b.setting = setting
		panel.langs[i] = b
	end
end

local function BuildDiagnostics()
	local page = Page("diag")
	Heading(page, -4, "SECTION_LINK")
	panel.kv = {}
	for i, key in ipairs(KV) do
		local y = -26 - (i - 1) * ROW
		local k = Line(page, PAD, y, C.muted, 96)
		labels[#labels + 1] = { k, key }
		panel.kv[i] = Line(page, PAD + 108, y, nil, INNER - 108)
	end
	Heading(page, -26 - #KV * ROW - 12, "SECTION_TESTS")
	local third = (INNER - 16) / 3
	local y1 = -26 - #KV * ROW - 36
	Button(page, PAD, y1, third, "BTN_SEND", function() WB().Send(L.TEST_TEXT) end)
	Button(page, PAD + third + 8, y1, third, "BTN_BURST", function() WB().Burst(10) end)
	Button(page, PAD + 2 * (third + 8), y1, third, "BTN_LONG", function() WB().Long(6000) end)
	panel.restart = Button(page, PAD, y1 - 30, third, "BTN_RESTART", function()
		WB().Reconnect()
		P.Refresh()
	end)
	Button(page, PAD + third + 8, y1 - 30, third, "BTN_FLUSH", function()
		ns.Print(L.FLUSHED:format(ns.Link.Flush()))
		P.Refresh()
	end)
	panel.copyDiag = Button(page, PAD + 2 * (third + 8), y1 - 30, third, "BTN_COPY_DIAG", function()
		panel.diagNote:Hide()
		panel.diagBox.Open(WB().Diag())
	end)
	panel.copyDiag.tip = function() return L.COPY_TIP end
	panel.diagNote = Line(page, PAD, y1 - 66, C.muted, INNER)
	labels[#labels + 1] = { panel.diagNote, "DIAG_NOTE" }
	panel.diagBox = S.CopyBox(page, INNER, function() panel.diagNote:Show() end)
	panel.diagBox:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, y1 - 62)
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
	-- WuxianKit's window, while the game runs it (Refresh shows the button then): the panel gives way to it
	panel.kit = S.Button(panel, 64, 22)
	panel.kit:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -40, -18)    -- level with the title: as wide as its name, it
	                                                                -- stays clear of the subtitle below
	panel.kit:SetScript("OnClick", function()
		panel:Hide()
		ns.Kit.Open()
	end)
	panel.kit.tip = function() return L.KIT_TIP end
	labels[#labels + 1] = { panel.kit, "BTN_KIT" }

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

-- 概览's blocks one under another from the top of the page: the card, the notice, this session's numbers, the latest
-- lines (as many as there is room for)
local function LayOutOverview(state, todo)
	local page, y = panel.pages.overview, 0
	local card = panel.card
	local acting = state ~= "online"
	card:SetHeight(acting and 90 or 54)
	card:ClearAllPoints()
	card:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, -y)
	y = y + card:GetHeight() + GAP
	panel.notice:SetShown(todo ~= nil)
	if todo then
		panel.notice:ClearAllPoints()
		panel.notice:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, -y)
		y = y + 36 + GAP
	end
	local head = panel.session:GetParent()          -- the heading's row
	head:ClearAllPoints()
	head:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, -y)
	panel.console:ClearAllPoints()
	panel.console:SetPoint("TOPRIGHT", page, "TOPRIGHT", -PAD, -y + 1)
	y = y + 24
	local w = (INNER - 3 * 8) / 4
	for i, tile in ipairs(panel.tiles) do
		tile:ClearAllPoints()
		tile:SetPoint("TOPLEFT", page, "TOPLEFT", PAD + (i - 1) * (w + 8), -y)
	end
	y = y + 46 + 6
	local held = state ~= "online" and ns.Setting("forwardDebug") ~= false and ns.Debug.Waiting() > 0
	panel.held:SetShown(held)
	if held then
		panel.held:ClearAllPoints()
		panel.held:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, -y)
		y = y + ROW
	end
	y = y + 6
	local room = math.floor((HEIGHT - TOP - BOTTOM - y - ROW - 2) / ROW)     -- lines that fit under the label
	panel.recentLabel:SetShown(room > 0)
	panel.recentLabel:ClearAllPoints()
	panel.recentLabel:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, -y)
	y = y + ROW + 2
	local lines = ns.Console.Recent(math.max(0, math.min(RECENT, room)))
	panel.recentNone:SetShown(room > 0 and #lines == 0)
	panel.recentNone:ClearAllPoints()
	panel.recentNone:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, -y)
	for i, row in ipairs(panel.recent) do
		local e = lines[i]
		row:SetShown(e ~= nil)
		if e then
			row:ClearAllPoints()
			row:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, -y - (i - 1) * ROW)
			row.time:SetText(date("%H:%M", e.t))
			row.tag:SetText(e.tag)
			S.Ink(row.tag, C[e.color] or C.fg)
			row.text:SetText(e.text)
			row.tab = e.tab
		end
	end
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
	panel.kit:SetWidth(math.max(64, math.floor(panel.kit.label:GetStringWidth() + 0.5) + 20))   -- its name, either language
	panel.kit:SetShown(ns.Kit.Present())

	-- 概览
	local n = ns.Link.Numbers()
	local state = STATE_WORDS[n.state] and n.state or "hello"
	local color = S.StateColor(n.state)
	local card = panel.card
	S.Tint(card.dot, C[color])
	card.state:SetText(L[STATE_WORDS[state]])
	S.Ink(card.state, C[color == "muted" and "fg" or color])
	card.why:SetText(L[WHY_WORDS[state]])
	card.act:SetShown(state ~= "online")
	card.act:SetText(state == "off" and L.BTN_SHOW_FRAME or L.BTN_RESTART)
	card.hint:SetShown(state == "hello" and ns.Link.SlotsLeft() > 0)   -- none left: the dialog says what to do
	local unlocked = ns.Frame.IsUnlocked()
	local todo = unlocked and "lock" or (ns.Setting("hotLoad") == false and "hotload") or nil
	local notice = panel.notice
	notice.what = todo
	notice.text:SetText(todo == "lock" and L.NOTICE_UNLOCKED or todo == "hotload" and L.NOTICE_HOTLOAD or "")
	notice.act:SetText(todo == "lock" and L.BTN_LOCK or L.BTN_TURN_ON)
	local runs, errs = ns.Agent.stats, ns.Debug.stats
	local numbers = { runs.runs, runs.failed, errs.errors, errs.blocked }
	local alarm = { false, true, true, true }          -- a count of these above 0 shows red
	for i, tile in ipairs(panel.tiles) do
		tile.num:SetText(tostring(numbers[i]))
		S.Ink(tile.num, (alarm[i] and numbers[i] > 0) and C.bad or (numbers[i] > 0 and C.fg or C.muted))
	end
	LayOutOverview(state, todo)

	-- 设置
	panel.link:SetChecked(n.state ~= "off")
	panel.unlock:SetText(unlocked and L.BTN_LOCK or L.BTN_MOVE)
	panel.unlock.kind = unlocked and "primary" or nil        -- unlocked: locking is the thing to do next
	S.Paint(panel.unlock)
	panel.position:SetText(L.POSITION:format(ns.Frame.Offset()))
	panel.hot:SetChecked(ns.Setting("hotLoad") ~= false)
	panel.debug:SetChecked(ns.Setting("forwardDebug") ~= false)
	panel.toasts:SetChecked(ns.Setting("toasts") ~= false)
	panel.minimap:SetChecked(not ns.Setting("minimapHide"))
	panel.langLabel:SetText(L.LANGUAGE)
	local current = ns.Setting("lang") or "auto"
	for _, b in ipairs(panel.langs) do
		b:SetText(ns.LanguageName(b.setting, true))
		b.selected = b.setting == current                    -- the one in force: marked, not clickable
		b:SetEnabled(not b.selected)
		S.Paint(b)
	end

	-- 诊断
	local P_ = ns.Link.P
	local left, hb = ns.Link.SlotsLeft(), n.hb or 15
	local stateWords = "|cff" .. S.HEX[color] .. ns.StateName(n.state) .. "|r"
	panel.kv[1]:SetText(ns.session and L.KV_STATE_VALUE:format(stateWords, ns.session) or stateWords)
	panel.kv[2]:SetText(L.KV_SLOTS_VALUE:format(left, left * hb / 3600))
	panel.kv[3]:SetText(("%d / %d"):format(n.waiting, n.acked))
	panel.kv[4]:SetText(L.KV_HEARTBEAT_VALUE:format(hb))
	panel.kv[5]:SetText(L.KV_FRAME_VALUE:format(P_.mode, P_.show))
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
