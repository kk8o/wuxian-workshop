-- WoWBridge's panel (/wb, the minimap button, Options > AddOns) in 无限工坊's look (Skin.lua), in three tabs:
--   概览 (overview): what a player opens it for, top to bottom: whether the app is there (a card: a dot and one sentence,
--        with who keeps the link when it is always on; a second line only when the player has something to do), a notice
--        with its button when something needs doing (the frame unlocked, hot loading off), 正在使用 (the lines addons
--        pushed with handle:Overview, API.lua: what uses 无限工坊 now; not there when nobody pushed any), 本次会话 (quiet
--        when nothing is wrong: one line; else the Lua errors with the way to them and the newest one, the agent's runs
--        that failed, the blocked actions) and, after it, the meter of the app messages left;
--   设置 (settings): 帧码 (the link's switch, the frame's place), Agent (hot loading, what goes to the app, the notice),
--        界面 (the minimap button, the language), the website;
--   诊断 (diagnostics): the link's numbers, the link tests, dropping what waits to go up, the diagnostics in a box to
--        copy (for a bug report), and this session: its counts, the way to the debug window and its latest lines
--        (Console.lua; the mouse wheel goes through them, a line's whole text shows under the mouse).
-- The window is as high as what 概览 shows (HEIGHT at most; the other tabs are HEIGHT), and hangs by its top left corner:
-- its top edge stays where it is whatever the tab and whatever comes and goes on 概览. The first line of 概览 is also the
-- minimap button's (P.Conclusion), as is the meter's warning (P.Usage, P.UsageNote).
-- It opens on 概览, or on the tab it showed last since the last reload. Everything it changes is kept for the account (every character)
-- in WoWBridgeDB.settings, the same as the /wb commands do; the words are Locale.lua's and change with the language.
local _, ns = ...
local L, S = ns.L, ns.Skin
local C = S.C

local P = {}
ns.Panel = P

local WIDTH, HEIGHT, PAD = 400, 470, 20
local INNER = WIDTH - 2 * PAD
local TOP, BOTTOM = 106, 18             -- the pages' area: below the tabs, above the window's edge
local PAGE = HEIGHT - TOP - BOTTOM      -- its height, at most
local GAP = 14                          -- under a box of 概览 (the card, the notice), before what comes next
local LEAD = 10                         -- ... and under a section's last line (a line has some air of its own)
local ROW = 18                          -- a line of small words
local USE_ROW = 20                      -- a line of 正在使用
local HEAD = 22                         -- a section heading and the space under it
local METER = 26                        -- the meter at 概览's end: a line of words over a thin bar
local BAR = 4                           -- ... the bar's height
local LOW = 80                          -- per cent of the app messages used from which the meter warns
local TABS = { "overview", "settings", "diag" }
local TAB_WORDS = { overview = "TAB_OVERVIEW", settings = "TAB_SETTINGS", diag = "TAB_DIAG" }
local TONE = { ok = "ok", warn = "gold", bad = "bad" }     -- a line's tone as a colour of Skin's C (none: grey)
local KV = { "KV_STATE", "KV_SLOTS", "KV_QUEUE", "KV_HEARTBEAT", "KV_FRAME" }
local RECENT = 4                        -- the latest lines on 诊断 at a time
local panel, ticker, options
local labels = {}         -- what Refresh relabels: { object, key }
local headings = {}       -- section headings: { words, key }
local shown = "overview"  -- the tab in front
local scrolled = 0        -- lines of 诊断's latest the mouse wheel went back
local tall = HEIGHT       -- the window's height while 概览 is in front: what it shows needs (Refresh)

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

-- small words that end at the page's right edge (Refresh places them)
local function RightLine(page, color)
	local fs = S.Text(page, "GameFontHighlightSmall", color)
	fs:SetJustifyH("RIGHT")
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

-- 4096 as "4,096"
local function Thousands(n)
	local s = ("%d"):format(n)
	return (s:reverse():gsub("(%d%d%d)", "%1,"):reverse():gsub("^,", ""))
end

-- the app messages left (the mailbox's slots, read once a game process: Link.SlotsLeft) as what is used of them: how
-- many, of how many, the per cent to show (a little used shows as 1, 100 only when none is left) and the tier ("sky":
-- plenty, "gold": LOW per cent and more, "bad": none left)
local function Usage()
	local pool, left = ns.Mailbox.POOL, math.max(0, ns.Link.SlotsLeft())
	local used = math.max(0, pool - left)
	local pct = left == 0 and 100 or used == 0 and 0 or math.max(1, math.min(99, math.floor(used * 100 / pool + 0.5)))
	return used, pool, pct, left == 0 and "bad" or pct >= LOW and "gold" or "sky"
end
P.Usage = Usage

-- the small words that go with a tier: what resets the count, and from LOW per cent on that it runs low or is gone
function P.UsageNote(tier)
	return tier == "bad" and L.METER_GONE or tier == "gold" and L.METER_LOW or L.METER_RESET
end

-- a text's own width (GetStringWidth stops at the width its line was given)
local function Wide(fs)
	return fs.GetUnboundedStringWidth and fs:GetUnboundedStringWidth() or fs:GetStringWidth()
end

-- the meter's tooltip: how many are used and, when the pace says so, how long the rest lasts
local function MeterTip()
	local used, pool = Usage()
	local text = L.METER_TIP:format(Thousands(used), Thousands(pool))
	local hours = ns.Link.SlotsPace()
	if hours and hours >= 1 then
		text = text .. L.METER_HOURS:format(math.floor(hours + 0.5))
	elseif hours then
		text = text .. L.METER_MINUTES:format(math.max(1, math.floor(hours * 60 + 0.5)))
	end
	return text
end

-- a line of 正在使用 (made as they are needed): a dot in the line's tone, the addon's words, its small words at the end;
-- a click runs the addon's function (its error goes to the error handler, not into the click); words cut short for want
-- of room show whole under the mouse
local function UseRow(i)
	local row = panel.rows[i]
	if row then return row end
	row = CreateFrame("Button", nil, panel.pages.overview)
	row:SetSize(INNER, USE_ROW)
	row.dot = S.Dot(row, 7)
	row.dot:SetPoint("LEFT", row, "LEFT", 3, 0)
	row.note = S.Text(row, "GameFontHighlightSmall", C.muted)
	row.note:SetPoint("RIGHT", row, "RIGHT")
	row.note:SetJustifyH("RIGHT")
	row.text = S.Text(row, "GameFontHighlight")
	row.text:SetPoint("LEFT", row, "LEFT", 18, 0)
	row.text:SetPoint("RIGHT", row.note, "LEFT", -8, 0)
	row.text:SetWordWrap(false)
	row:SetScript("OnClick", function(self)
		if not self.click then return end
		local ok, err = pcall(self.click)
		if not ok then geterrorhandler()(err) end
	end)
	row:SetScript("OnEnter", function(self)
		if self.click then S.Ink(self.text, C.goldL) end
		if self.text.IsTruncated and self.text:IsTruncated() then
			GameTooltip:SetOwner(self, "ANCHOR_TOP")
			GameTooltip:SetText(self.text:GetText(), C.fg[1], C.fg[2], C.fg[3], 1, true)
			GameTooltip:Show()
		end
	end)
	row:SetScript("OnLeave", function(self)
		S.Ink(self.text, C.fg)
		GameTooltip:Hide()
	end)
	panel.rows[i] = row
	return row
end

local function BuildOverview()
	local page = Page("overview")
	-- the conclusion: connected or not, and who keeps the link; a button only while the player has something to do
	local card = Block(page, C.field, C.line)
	card:SetWidth(INNER)
	card.dot = S.Dot(card, 9)
	card.dot:SetPoint("TOPLEFT", card, "TOPLEFT", 14, -17)
	card.state = S.Text(card, "GameFontNormalLarge")
	card.state:SetPoint("TOPLEFT", card, "TOPLEFT", 35, -12)
	card.state:SetWidth(INNER - 49)
	card.state:SetWordWrap(false)
	card.act = S.Button(card, 88, 24, "primary")
	card.act:SetPoint("TOPLEFT", card, "TOPLEFT", 35, -38)
	card.act:SetScript("OnClick", function(self)
		if self.what == "on" then WB().SetLink(true) else WB().Reconnect() end
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
	-- what uses 无限工坊 now: the addons' own lines
	panel.using = Heading(page, 0, "SECTION_USING")
	panel.rows = {}
	panel.more = Line(page, PAD, 0, C.muted, INNER)
	-- this session: one quiet line, or what went wrong
	panel.session = Heading(page, 0, "SECTION_SESSION")
	panel.errors = Line(page, PAD, 0, nil, INNER - 150)
	panel.runs = RightLine(page, C.muted)
	panel.view = S.Button(page, 56, 20)
	panel.view:SetScript("OnClick", function() ns.Console.Show("errors") end)
	labels[#labels + 1] = { panel.view, "BTN_VIEW" }
	panel.lastError = Line(page, PAD, 0, C.muted, INNER)
	panel.lastError:SetWordWrap(false)
	panel.forwarded = Line(page, PAD, 0, C.muted, INNER)
	panel.failed = Line(page, PAD, 0, C.gold, INNER)
	panel.blocked = Line(page, PAD, 0, C.gold, INNER)
	-- the app messages left, as a meter of what is used: its name, what resets it, the per cent; under them the bar
	local meter = CreateFrame("Frame", nil, page)
	meter:SetSize(INNER, METER)
	meter:EnableMouse(true)
	meter.label = S.Text(meter, "GameFontHighlightSmall")
	meter.label:SetPoint("TOPLEFT", meter, "TOPLEFT")
	labels[#labels + 1] = { meter.label, "KV_SLOTS" }
	meter.pct = S.Text(meter, "GameFontHighlightSmall")
	meter.pct:SetPoint("TOPRIGHT", meter, "TOPRIGHT")
	meter.pct:SetJustifyH("RIGHT")
	meter.note = S.Text(meter, "GameFontHighlightSmall", C.muted)
	meter.note:SetPoint("RIGHT", meter.pct, "LEFT", -8, 0)
	meter.note:SetJustifyH("RIGHT")
	meter.track = meter:CreateTexture(nil, "BACKGROUND")
	meter.track:SetPoint("BOTTOMLEFT", meter, "BOTTOMLEFT")
	meter.track:SetPoint("BOTTOMRIGHT", meter, "BOTTOMRIGHT")
	meter.track:SetHeight(BAR)
	S.Fill(meter.track, C.line)
	meter.fill = meter:CreateTexture(nil, "ARTWORK")
	meter.fill:SetPoint("BOTTOMLEFT", meter, "BOTTOMLEFT")
	meter.fill:SetHeight(BAR)
	meter:SetScript("OnEnter", function(self)
		GameTooltip:SetOwner(self, "ANCHOR_TOP")
		GameTooltip:SetText(MeterTip(), C.fg[1], C.fg[2], C.fg[3], 1, true)
		GameTooltip:Show()
	end)
	meter:SetScript("OnLeave", function() GameTooltip:Hide() end)
	panel.meter = meter
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
	-- at the page's foot: the website and a button that puts it in a box to copy
	local y = -(PAGE - 24)
	panel.siteLabel = S.Text(page, "GameFontHighlightSmall", C.muted)
	panel.siteLabel:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, y - 6)
	labels[#labels + 1] = { panel.siteLabel, "WEBSITE" }
	panel.site = S.Text(page, "GameFontHighlightSmall", C.goldL)
	panel.site:SetPoint("LEFT", panel.siteLabel, "RIGHT", 8, 0)
	panel.site:SetText((S.WEBSITE:gsub("^https://", "")))
	panel.copyBox = S.CopyBox(page, INNER - 104, function()
		panel.site:Show()
		panel.siteLabel:Show()
	end)
	panel.copyBox:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, y - 1)
	panel.copy = Button(page, WIDTH - PAD - 96, y, 96, "BTN_COPY", function()
		panel.site:Hide()
		panel.siteLabel:Hide()
		panel.copyBox.Open(S.WEBSITE)
	end)
	panel.copy.tip = function() return L.COPY_TIP end
end

local function BuildDiagnostics()
	local page = Page("diag")
	Heading(page, -4, "SECTION_LINK")
	panel.kv = {}
	for i, key in ipairs(KV) do
		local y = -24 - (i - 1) * ROW
		local k = Line(page, PAD, y, C.muted, 96)
		labels[#labels + 1] = { k, key }
		panel.kv[i] = Line(page, PAD + 108, y, nil, INNER - 108)
	end
	Heading(page, -24 - #KV * ROW - 8, "SECTION_TESTS")
	local third = (INNER - 16) / 3
	local y1 = -24 - #KV * ROW - 30
	Button(page, PAD, y1, third, "BTN_SEND", function() WB().Send(L.TEST_TEXT) end)
	Button(page, PAD + third + 8, y1, third, "BTN_BURST", function() WB().Burst(10) end)
	Button(page, PAD + 2 * (third + 8), y1, third, "BTN_LONG", function() WB().Long(6000) end)
	panel.restart = Button(page, PAD, y1 - 28, third, "BTN_RESTART", function()
		WB().Reconnect()
		P.Refresh()
	end)
	Button(page, PAD + third + 8, y1 - 28, third, "BTN_FLUSH", function()
		ns.Print(L.FLUSHED:format(ns.Link.Flush()))
		P.Refresh()
	end)
	panel.copyDiag = Button(page, PAD + 2 * (third + 8), y1 - 28, third, "BTN_COPY_DIAG", function()
		panel.diagNote:Hide()
		panel.diagBox.Open(WB().Diag())
	end)
	panel.copyDiag.tip = function() return L.COPY_TIP end
	panel.diagNote = Line(page, PAD, y1 - 62, C.muted, INNER)
	labels[#labels + 1] = { panel.diagNote, "DIAG_NOTE" }
	panel.diagBox = S.CopyBox(page, INNER, function() panel.diagNote:Show() end)
	panel.diagBox:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, y1 - 58)
	-- this session: its counts, the way to the debug window, and the window's latest lines, newest first (the mouse
	-- wheel goes back through them; a click opens the window on the line's kind, the mouse over one shows its whole text)
	local y2 = y1 - 88
	Heading(page, y2, "SECTION_SESSION", INNER - 100)
	panel.console = TextButton(page, 92, "BTN_CONSOLE", function()
		ns.Console.Show(ns.Debug.stats.errors > 0 and "errors" or nil)
	end)
	panel.console:SetPoint("TOPRIGHT", page, "TOPRIGHT", -PAD, y2 + 1)
	panel.counts = Line(page, PAD, y2 - HEAD, C.muted, INNER)        -- (the whole line: the range gives way to it)
	panel.counts:SetWordWrap(false)
	panel.range = RightLine(page, C.muted)
	panel.range:SetPoint("TOPRIGHT", page, "TOPRIGHT", -PAD, y2 - HEAD)
	local y3 = y2 - HEAD - ROW - 2
	panel.recentNone = Line(page, PAD, y3, C.muted, INNER)
	labels[#labels + 1] = { panel.recentNone, "RECENT_NONE" }
	panel.recent = {}
	for i = 1, RECENT do
		local row = CreateFrame("Button", nil, page)
		row:SetSize(INNER, ROW)
		row:SetPoint("TOPLEFT", page, "TOPLEFT", PAD, y3 - (i - 1) * ROW)
		row.time = S.Text(row, "GameFontHighlightSmall", C.muted)
		row.time:SetPoint("LEFT", row, "LEFT")
		row.tag = S.Text(row, "GameFontHighlightSmall")
		row.tag:SetPoint("LEFT", row, "LEFT", 46, 0)
		row.text = S.Text(row, "GameFontHighlightSmall")
		row.text:SetPoint("LEFT", row, "LEFT", 92, 0)
		row.text:SetPoint("RIGHT", row, "RIGHT")
		row.text:SetWordWrap(false)
		row:SetScript("OnClick", function(self) ns.Console.Show(self.tab) end)
		row:SetScript("OnEnter", function(self)
			S.Ink(self.text, C.goldL)
			GameTooltip:SetOwner(self, "ANCHOR_TOP")
			GameTooltip:SetText(self.full or "", C.fg[1], C.fg[2], C.fg[3], 1, true)
			GameTooltip:Show()
		end)
		row:SetScript("OnLeave", function(self)
			S.Ink(self.text, C.fg)
			GameTooltip:Hide()
		end)
		panel.recent[i] = row
	end
	page:EnableMouseWheel(true)
	page:SetScript("OnMouseWheel", function(_, delta)
		scrolled = math.max(0, scrolled - delta)           -- (Refresh keeps it within the lines there are)
		P.Refresh()
	end)
end

-- the frame is drawn above everything (the app has to see it), so a panel over its room would lose that corner:
-- centred on the screen (as it is at its highest) unless that overlaps the room, else right of the room, or under it
-- when the screen is too narrow. By its top left corner: a height that follows 概览 moves the bottom edge alone
local function Place()
	panel:ClearAllPoints()
	local px = S.Pixel(UIParent)                       -- a pixel in UIParent's units (the frame's place is in pixels)
	local fx, fy = ns.Frame.Offset()
	local fw, fh = ns.Frame.Room()
	local right, bottom = (fx + fw) * px + 12, (fy + fh) * px + 12
	local sw, sh = UIParent:GetWidth(), UIParent:GetHeight()
	local left, top = (sw - WIDTH) / 2, (sh - HEIGHT) / 2 - 40
	if left >= right or top >= bottom then
		panel:SetPoint("TOPLEFT", UIParent, "TOPLEFT", math.max(left, 0), -math.max(top, 0))
	elseif right + WIDTH <= sw then
		panel:SetPoint("TOPLEFT", UIParent, "TOPLEFT", right, -math.max(top, 0))
	else
		panel:SetPoint("TOPLEFT", UIParent, "TOPLEFT", math.max(left, 0), -bottom)
	end
end

-- where the player dragged it (or the client put it back, at whatever corner it kept): by its top left corner again,
-- and still the player's
local function Pin()
	local left, top = panel:GetLeft(), panel:GetTop()
	if not (left and top) then return end
	panel:ClearAllPoints()
	panel:SetPoint("TOPLEFT", UIParent, "BOTTOMLEFT", left, top)
	if panel.SetUserPlaced then panel:SetUserPlaced(true) end
end

-- one tab in front, the window as high as it needs
function P.Select(id)
	if not panel or not panel.pages[id] then return end
	shown = id
	for key, page in pairs(panel.pages) do page:SetShown(key == id) end
	panel.tabs.Select(id)
	panel:SetHeight(id == "overview" and tall or HEIGHT)
end

local function Build()
	panel = S.Window("WoWBridgePanel", WIDTH, HEIGHT)
	panel:Hide()
	panel:SetScript("OnDragStop", function(self)
		self:StopMovingOrSizing()
		Pin()
	end)
	tinsert(UISpecialFrames, "WoWBridgePanel")        -- Escape closes it
	S.Close(panel)

	-- the head: the logo, the name, its version
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
	                                                                -- stays clear of the version below
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

	panel:SetScript("OnShow", function()
		P.Refresh()
		ticker = C_Timer.NewTicker(1, P.Refresh)
	end)
	panel:SetScript("OnHide", function()
		if ticker then ticker:Cancel() ticker = nil end
	end)
end

-- the conclusion, for the card's first line and the minimap button's tooltip: connected or not, and who keeps the link.
-- Gives what it is ("off", "waiting": the frame is up and nobody answers, "away", "on": an app that does not stand by,
-- "auto", "always", "held"), its words and the dot's colour (a name of Skin's C). Kept by apps, the words name the first
-- two, or one when fits(words) says two take too much room, and how many there are when that is more (their titles came
-- from the app: plain text)
function P.Conclusion(fits)
	local link = ns.Link
	local state = link.State()
	if state == "off" then return "off", L.CARD_OFF, "muted" end
	if not link.Reachable() then
		if state == "hello" then return "waiting", L.CARD_AWAY, "sky" end
		return "away", L.CARD_AWAY, "muted"
	end
	if not link.CanStandBy() then return "on", L.CARD_ON, "ok" end
	local always, by, n = link.Kept()
	if not always then return "auto", L.CARD_AUTO, "ok" end
	if not by[1] then return "always", L.CARD_ALWAYS, "ok" end
	local function Words(k)
		local names = {}
		for i = 1, k do names[i] = ns.Plain(by[i]) end
		local who = table.concat(names, L.HELD_SEP)
		return L.CARD_HELD:format(n > k and L.HELD_MORE:format(who, n) or who)
	end
	local words = Words(math.min(#by, 2))
	if by[2] and fits and not fits(words) then words = Words(1) end
	return "held", words, "ok"
end

-- 概览's blocks one under another from the top of the page: the card, the notice, 正在使用 (as many lines as a window of
-- HEIGHT has room for above 本次会话 and the meter; then how many more there are), 本次会话, the meter. s: what 本次会话
-- shows. Gives the height of it all
local function LayOutOverview(acting, todo, rows, s)
	local page, y = panel.pages.overview, 0
	local function At(region, x, dy)
		region:ClearAllPoints()
		region:SetPoint("TOPLEFT", page, "TOPLEFT", x or PAD, -(y + (dy or 0)))
	end
	local card = panel.card
	card:SetHeight(acting and 74 or 42)
	At(card)
	y = y + card:GetHeight() + GAP
	panel.notice:SetShown(todo ~= nil)
	if todo then
		At(panel.notice)
		y = y + 36 + GAP
	end

	local tail = HEAD + USE_ROW + (s.errors > 0 and ((s.last and ROW or 0) + ROW) or 0) + (s.failed > 0 and ROW or 0)
		+ (s.blocked > 0 and ROW or 0)
	local room = math.floor((PAGE - METER - LEAD - tail - LEAD - HEAD - y) / USE_ROW)
	local show = #rows
	if show > room then show = math.max(0, room - 1) end        -- the last line says how many more there are
	panel.using:GetParent():SetShown(#rows > 0)
	panel.more:SetShown(show < #rows)
	if #rows > 0 then
		At(panel.using:GetParent())
		y = y + HEAD
		for i = 1, show do
			local row, r = UseRow(i), rows[i]
			At(row)
			S.Tint(row.dot, C[TONE[r.tone] or "muted"])
			row.text:SetText(r.text)
			row.note:SetText(r.note or "")
			row.click = r.click
			row:Show()
			y = y + USE_ROW
		end
		if show < #rows then
			panel.more:SetText(L.USING_MORE:format(#rows - show))
			At(panel.more, PAD, 2)
			y = y + USE_ROW
		end
		y = y + LEAD
	end
	for i = show + 1, #panel.rows do panel.rows[i]:Hide() end

	At(panel.session:GetParent())
	y = y + HEAD
	At(panel.errors, PAD, 2)
	panel.runs:ClearAllPoints()
	panel.runs:SetPoint("TOPRIGHT", page, "TOPRIGHT", -PAD, -(y + 2))
	panel.view:ClearAllPoints()
	panel.view:SetPoint("TOPRIGHT", page, "TOPRIGHT", -PAD, -(y - 2))
	y = y + USE_ROW
	for _, line in ipairs({ { panel.lastError, s.errors > 0 and s.last ~= nil }, { panel.forwarded, s.errors > 0 },
		{ panel.failed, s.failed > 0 }, { panel.blocked, s.blocked > 0 } }) do
		line[1]:SetShown(line[2])
		if line[2] then
			At(line[1])
			y = y + ROW
		end
	end
	y = y + LEAD
	At(panel.meter)
	return y + METER
end

-- every word and every state, again: on show, every second while shown, after a change of language
function P.Refresh()
	if not panel then return end
	local debug, agent = ns.Debug.stats, ns.Agent.stats
	local runs = math.max(0, agent.runs - ns.Console.beats)       -- the agent's own: not the app's housekeeping calls
	panel.titleText:SetText(L.BRAND)
	panel.version:SetText(WB().VERSION or "?")
	panel.tabs.SetLabels(function(id)                             -- 诊断 with the Lua errors' count in red
		if id ~= "diag" or debug.errors == 0 then return L[TAB_WORDS[id]] end
		return ("%s |cff%s%s|r"):format(L.TAB_DIAG, S.HEX.bad, debug.errors > 99 and "99+" or tostring(debug.errors))
	end)
	for _, h in ipairs(headings) do h[1]:SetText(L[h[2]]) end
	for _, w in ipairs(labels) do w[1]:SetText(L[w[2]]) end
	panel.kit:SetWidth(math.max(64, math.floor(panel.kit.label:GetStringWidth() + 0.5) + 20))   -- its name, either language
	panel.kit:SetShown(ns.Kit.Present())

	-- 概览
	local n = ns.Link.Numbers()
	local card = panel.card
	local what, words, color = P.Conclusion(function(text)       -- (two names only when the line has room for them)
		card.state:SetText(text)
		return Wide(card.state) <= card.state:GetWidth()
	end)
	card.what = what
	S.Tint(card.dot, C[color])
	card.state:SetText(words)
	-- off: on again; the frame up and nobody answering: start over; else nothing to do
	local act = what == "off" and "on" or what == "waiting" and "restart" or nil
	card.act.what = act
	card.act:SetShown(act ~= nil)
	card.act:SetText(act == "on" and L.BTN_TURN_ON or L.BTN_RESTART)
	card.hint:SetShown(act == "restart" and ns.Link.SlotsLeft() > 0)   -- none left: the dialog says what to do
	local unlocked = ns.Frame.IsUnlocked()
	local todo = unlocked and "lock" or (ns.Setting("hotLoad") == false and "hotload") or nil
	local notice = panel.notice
	notice.what = todo
	notice.text:SetText(todo == "lock" and L.NOTICE_UNLOCKED or todo == "hotload" and L.NOTICE_HOTLOAD or "")
	notice.act:SetText(todo == "lock" and L.BTN_LOCK or L.BTN_TURN_ON)
	-- 本次会话: quiet, or the errors (how many, the newest one, whether the agent has them), the failures, the blocked
	local last = debug.errors > 0 and ns.Console.LastError() or nil
	panel.errors:SetText(debug.errors > 0 and L.SESSION_ERRORS:format(debug.errors) or L.SESSION_CLEAN)
	S.Ink(panel.errors, debug.errors > 0 and C.bad or C.fg)
	panel.view:SetShown(debug.errors > 0)
	panel.runs:SetShown(debug.errors == 0 and runs > 0)
	panel.runs:SetText(L.SESSION_RUNS:format(runs))
	if last then
		local whose = last.addon and (ns.Plain(last.addon) .. (last.file and (" · " .. ns.Plain(last.file)) or ""))
			or ns.Plain(last.text)
		panel.lastError:SetText(date("%H:%M", last.t) .. " · " .. whose)
	end
	local waits = n.state ~= "online" and ns.Debug.Waiting() > 0
	panel.forwarded:SetText(ns.Setting("forwardDebug") == false and L.SESSION_KEPT or waits and L.SESSION_HELD or L.SESSION_SENT)
	panel.failed:SetText(L.SESSION_FAILED:format(agent.failed))
	panel.blocked:SetText(L.SESSION_BLOCKED:format(debug.blocked))
	tall = math.min(HEIGHT, TOP + BOTTOM + LayOutOverview(act ~= nil, todo, ns.OverviewRows(),
		{ errors = debug.errors, last = last, failed = agent.failed, blocked = debug.blocked }))
	panel:SetHeight(shown == "overview" and tall or HEIGHT)      -- as high as 概览 needs; the other tabs as ever
	-- the meter
	local used, pool, pct, tier = Usage()
	local meter = panel.meter
	meter.tier = tier
	meter.pct:SetText(pct .. "%")
	S.Ink(meter.pct, tier == "sky" and C.fg or C[tier])
	meter.note:SetText(P.UsageNote(tier))
	S.Ink(meter.note, tier == "sky" and C.muted or C[tier])
	meter.fill:SetShown(used > 0)
	meter.fill:SetWidth(math.max(1, INNER * math.min(1, used / pool)))
	S.Fill(meter.fill, C[tier])

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
	local stateWords = "|cff" .. S.HEX[S.StateColor(n.state)] .. ns.StateName(n.state) .. "|r"
	panel.kv[1]:SetText(ns.session and L.KV_STATE_VALUE:format(stateWords, ns.session) or stateWords)
	panel.kv[2]:SetText(L.KV_SLOTS_VALUE:format(left, left * hb / 3600))
	panel.kv[3]:SetText(("%d / %d"):format(n.waiting, n.acked))
	panel.kv[4]:SetText(L.KV_HEARTBEAT_VALUE:format(hb))
	panel.kv[5]:SetText(L.KV_FRAME_VALUE:format(P_.mode, P_.show))
	local function Count(value, alarm)                       -- a count: red when it is one of the bad ones above 0
		return ("|cff%s%d|r"):format(S.HEX[(alarm and value > 0) and "bad" or (value > 0 and "fg" or "muted")], value)
	end
	panel.counts:SetText(L.COUNTS:format(Count(runs), Count(agent.failed, true), Count(debug.errors, true),
		Count(debug.blocked, true)))
	local _, total = ns.Console.Digest(0)
	scrolled = math.max(0, math.min(scrolled, total - RECENT))
	local lines = ns.Console.Digest(RECENT, scrolled)
	panel.recentNone:SetShown(total == 0)
	panel.range:SetText(L.RANGE:format(scrolled + 1, scrolled + #lines, total))
	-- (the counts come first: the range shows only with 12 px between the two)
	panel.range:SetShown(total > RECENT and Wide(panel.counts) + 12 + Wide(panel.range) <= INNER)
	for i, row in ipairs(panel.recent) do
		local e = lines[i]
		row:SetShown(e ~= nil)
		if e then
			row.time:SetText(date("%H:%M", e.t))
			row.tag:SetText(e.tag)
			S.Ink(row.tag, C[e.color] or C.fg)
			row.text:SetText(e.text)
			row.full, row.tab = e.full, e.tab
		end
	end
end

-- something the panel shows changed (an addon pushed its lines: API.lua): at once when it is open
function P.Changed()
	if panel and panel:IsShown() then P.Refresh() end
end

-- tab: the one to show ("overview", "settings", "diag"); else the one shown last
function P.Show(tab)
	if not panel then Build() end
	if panel.IsUserPlaced and panel:IsUserPlaced() then Pin() else Place() end   -- dragged by the player: theirs
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
