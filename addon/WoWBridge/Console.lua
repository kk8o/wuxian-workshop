-- WoWBridge's debug window and its notice.
-- The window (/wb log, the minimap button's right click, a key binding, the panel's 调试输出) lists what happens in this UI
-- session as it happens, the newest at the bottom: Lua errors with their stack (the same error stays one line, counted),
-- warnings, blocked actions, print() output, the agent's code (each file it hot-loads, each snippet it runs: ok or the
-- error, the time, what it returned), reload requests, the link going up and down. MAX lines, kept while it is closed
-- too. Tabs keep one kind in view (全部 / 报错 / 运行 / 输出); a line's [详情] puts its whole text in a box below, selected
-- for Ctrl+C (the game gives addons no clipboard), and 复制 puts every line in view there. Its place and size are kept
-- for this character (consoleX / consoleY / consoleW / consoleH). Errors that come while it is closed show as a count on
-- the minimap button until it opens.
-- The notice: a hot-load (an addon's files together) or the agent's code failing shows for a few seconds at the top of
-- the screen, green or red; a click opens the window. The setting toasts (/wb set toasts off, the panel) turns it off,
-- and the agent's code is then told in the chat as before.
local _, ns = ...
local L, S = ns.L, ns.Skin
local C = S.C

local K = {}
ns.Console = K

local MAX = 300
local W0, H0, WMIN, HMIN = 560, 320, 420, 200
local PANE_H = 120
local TABS = { "all", "errors", "runs", "output" }
local TAB_WORDS = { all = "CON_ALL", errors = "CON_ERRORS", runs = "CON_RUNS", output = "CON_OUTPUT" }
local KEEP = { errors = { ERR = true, WARN = true, BLOCKED = true }, runs = { RUN = true, RELOAD = true, LINK = true },
	output = { OUT = true } }
local KIND = {                                  -- a kind's tag (a word of Locale.lua) and colour (Skin's HEX)
	ERR = { "CON_ERR", "bad" }, WARN = { "CON_WARN", "goldL" }, BLOCKED = { "CON_BLOCKED", "goldL" }, OUT = { "CON_OUT", "ok" },
	RUN = { "CON_RUN", "sky" }, RELOAD = { "CON_RELOAD", "sky" }, LINK = { "CON_LINK", "muted" },
}

local entries, merged, nextId = {}, {}, 0       -- the lines, oldest first; an error's key -> its line; the last id given
local tab = "all"
local window, log, pane, ticker, toast
local full, dirty, drawnId = true, false, 0     -- the log to be drawn again whole; something new; the last id drawn
local batch                                     -- a whole addon's hot-load under way: { addon, files, ms, failed }
K.unseen = 0                                    -- errors since the window was last open: the minimap button's count

local function Shown() return window ~= nil and window:IsShown() end
K.IsShown = Shown

local function FirstLine(s) return (tostring(s or ""):match("^[^\n]*")) end

-- at most n bytes, cut at a character's start (the text is UTF-8)
local function Cut(s, n)
	n = n or 140
	if #s <= n then return s end
	local i = n
	while i > 1 and (s:byte(i + 1) or 0) >= 0x80 and s:byte(i + 1) < 0xC0 do i = i - 1 end
	return s:sub(1, i) .. "…"
end

-- a line's words: the time, the kind in its colour, the text, how many times, the link to its details
local function Text(e)
	local k = KIND[e.kind] or KIND.LINK
	local color = (e.kind == "RUN" and e.failed) and S.HEX.bad or S.HEX[k[2]]
	local s = ("|cff%s%s|r  |cff%s%s|r  %s"):format(S.HEX.muted, date("%H:%M:%S", e.t), color, L[k[1]], e.text)
	if e.n > 1 then s = s .. ("  |cff%s×%d|r"):format(S.HEX.muted, e.n) end
	if e.detail and e.detail ~= "" then s = s .. ("  |Hwbconsole:%d|h|cff%s[%s]|r|h"):format(e.id, S.HEX.sky, L.CON_DETAIL) end
	return s
end

local function Plain(s)                         -- without colours and links: for the copy box
	return (s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|H.-|h(.-)|h", "%1"))
end

local function Kept(e) return tab == "all" or KEEP[tab][e.kind] end

-- a line: kind ERR, WARN, BLOCKED, OUT, RUN, RELOAD or LINK; detail: the whole text behind [详情]; failed: the agent's
-- code failed. An error, a warning or a blocked action seen before counts on its line, which moves to the bottom.
function K.Add(kind, text, detail, failed)
	text = tostring(text or "")
	local key = (kind == "ERR" or kind == "WARN" or kind == "BLOCKED") and (kind .. "\0" .. text) or nil
	local e = key and merged[key]
	if e then
		e.n, e.t = e.n + 1, time()
		if detail then e.detail = detail end
		for i = #entries, 1, -1 do
			if entries[i] == e then
				table.remove(entries, i)
				break
			end
		end
		full = true
	else
		nextId = nextId + 1
		e = { id = nextId, kind = kind, text = text, detail = detail, failed = failed, n = 1, t = time(), key = key }
		if key then merged[key] = e end
	end
	entries[#entries + 1] = e
	while #entries > MAX do
		local old = table.remove(entries, 1)
		if old.key and merged[old.key] == old then merged[old.key] = nil end
		full = true
	end
	dirty = true
	if kind == "ERR" and not Shown() then
		K.unseen = K.unseen + 1
		if ns.MinimapButton and ns.MinimapButton.Badge then ns.MinimapButton.Badge(K.unseen) end
	end
end

-- the lines in view, as plain text (the copy box; tests)
function K.Lines()
	local out = {}
	for _, e in ipairs(entries) do
		if Kept(e) then out[#out + 1] = Plain(Text(e)) end
	end
	return out
end

---------------------------------------------------------------------------
-- The notice
---------------------------------------------------------------------------

local function Toast(text, failed)
	K.lastToast = text
	if ns.Setting("toasts") == false then return end
	if not toast then
		toast = CreateFrame("Button", "WoWBridgeToast", UIParent)
		toast:SetFrameStrata("FULLSCREEN_DIALOG")        -- above the panel, which may be open where it shows
		toast:SetHeight(30)
		toast:SetPoint("TOP", UIParent, "TOP", 0, -64)
		toast.bg = toast:CreateTexture(nil, "BACKGROUND")
		toast.bg:SetAllPoints()
		S.Fill(toast.bg, S.Alpha(C.panel, 0.94))
		toast.edges = S.Border(toast)
		toast.dot = S.Dot(toast, 9)
		toast.dot:SetPoint("LEFT", toast, "LEFT", 12, 0)
		toast.text = S.Text(toast, "GameFontHighlight")
		toast.text:SetPoint("LEFT", toast.dot, "RIGHT", 9, 0)
		toast:SetScript("OnClick", function(self)
			self:Hide()
			K.Show("runs")
		end)
	end
	local c = failed and C.bad or C.ok
	S.Edges(toast.edges, c)
	S.Tint(toast.dot, c)
	toast.text:SetText(text)
	toast:SetWidth(math.min(640, math.floor(toast.text:GetStringWidth() + 0.5) + 42))
	toast:SetAlpha(1)
	toast:Show()
	toast.serial = (toast.serial or 0) + 1
	local serial = toast.serial
	C_Timer.After(failed and 6 or 3, function()
		if toast.serial == serial then toast:Hide() end
	end)
end

-- the agent's code ran (Agent.lua): name its chunk ("@Interface/AddOns/<addon>/<file>" for a file, "=run" for a snippet),
-- flag the load's lifecycle ("reset": one file; "unload" / "reload": an addon's first / last file), values what it
-- returned, failure the error and its stack. A line; the notice for a hot-load (once the addon's last file is in) or a
-- failure.
function K.Job(name, addon, flag, ok, ms, values, failure)
	local file = name:sub(1, 1) == "@"
	local short = name:gsub("^@Interface[/\\]AddOns[/\\]", ""):gsub("^@", "")
	if file then
		K.Add("RUN", ok and L.CON_LOADED:format(short, ms) or L.CON_LOAD_FAILED:format(short, FirstLine(failure)), failure, not ok)
	else
		local got = values and values ~= "" and ("  → " .. Cut(FirstLine(values), 120)) or ""
		K.Add("RUN", ok and L.CON_RAN:format(ms, got) or L.CON_RUN_FAILED:format(FirstLine(failure)),
			ok and values ~= "" and values or failure, not ok)
		if not ok then Toast(L.TOAST_RUN_FAILED:format(Cut(FirstLine(failure))), true) end
		return
	end
	if flag == "unload" then batch = { addon = addon, files = 0, ms = 0 } end
	if batch and batch.addon == addon and flag ~= "reset" then
		batch.files, batch.ms = batch.files + 1, batch.ms + ms
		if not ok and not batch.failed then batch.failed = short .. ": " .. FirstLine(failure) end
		if flag == "reload" then
			local b = batch
			batch = nil
			if b.failed then
				Toast(L.TOAST_FAILED:format(Cut(b.failed)), true)
			else
				Toast(L.TOAST_LOADED:format(addon, b.files, b.ms), false)
			end
		end
	elseif ok then
		Toast(L.TOAST_LOADED_ONE:format(short, ms), false)
	else
		Toast(L.TOAST_FAILED:format(Cut(short .. ": " .. FirstLine(failure))), true)
	end
end

---------------------------------------------------------------------------
-- The window
---------------------------------------------------------------------------

local function Counts()
	local n = { all = #entries, errors = 0, runs = 0, output = 0 }
	for _, e in ipairs(entries) do
		for id, keep in pairs(KEEP) do
			if keep[e.kind] then n[id] = n[id] + 1 end
		end
	end
	return n
end

local function Draw()
	dirty = false
	if not Shown() then return end
	local n = Counts()
	window.tabs.SetLabels(function(id) return n[id] > 0 and ("%s %d"):format(L[TAB_WORDS[id]], n[id]) or L[TAB_WORDS[id]] end)
	window.tabs.Select(tab)
	if full then
		local offset = log:GetScrollOffset()
		log:Clear()
		for _, e in ipairs(entries) do
			if Kept(e) then log:AddMessage(Text(e)) end
		end
		log:SetScrollOffset(offset)            -- where the reader was (0: at the newest)
		full = false
	else
		for _, e in ipairs(entries) do
			if e.id > drawnId and Kept(e) then log:AddMessage(Text(e)) end
		end
	end
	drawnId = nextId
	window.empty:SetShown(#entries == 0)
end

local function Layout()
	log:ClearAllPoints()
	log:SetPoint("TOPLEFT", window, "TOPLEFT", 16, -76)
	log:SetPoint("BOTTOMRIGHT", window, "BOTTOMRIGHT", -16, (pane and pane:IsShown()) and (PANE_H + 22) or 18)
	if pane then pane.edit:SetWidth(math.max(100, window:GetWidth() - 44)) end
end

local function Save()
	ns.Set("consoleX", math.floor(window:GetLeft() + 0.5))
	ns.Set("consoleY", math.floor(window:GetBottom() + 0.5))
	ns.Set("consoleW", math.floor(window:GetWidth() + 0.5))
	ns.Set("consoleH", math.floor(window:GetHeight() + 0.5))
end

-- the box below the lines: a text to read whole and copy (selected; typing does not change it)
local function OpenPane(hint, text)
	pane.hint:SetText(hint)
	pane.edit.value = text
	pane.edit:SetText(text)
	pane:Show()
	Layout()
	pane.edit:SetFocus()
	pane.edit:HighlightText()
end

local function BuildPane()
	pane = CreateFrame("Frame", nil, window)
	pane:SetPoint("BOTTOMLEFT", window, "BOTTOMLEFT", 14, 14)
	pane:SetPoint("BOTTOMRIGHT", window, "BOTTOMRIGHT", -14, 14)
	pane:SetHeight(PANE_H)
	pane.bg = pane:CreateTexture(nil, "BACKGROUND")
	pane.bg:SetAllPoints()
	S.Fill(pane.bg, C.field)
	S.Edges(S.Border(pane), C.goldD)
	pane.hint = S.Text(pane, "GameFontHighlightSmall", C.muted)
	pane.hint:SetPoint("TOPLEFT", pane, "TOPLEFT", 8, -6)
	S.Close(pane)
	pane.close:SetSize(18, 18)
	pane.close:SetPoint("TOPRIGHT", pane, "TOPRIGHT", -3, -2)
	local scroll = CreateFrame("ScrollFrame", nil, pane)
	scroll:SetPoint("TOPLEFT", pane, "TOPLEFT", 8, -24)
	scroll:SetPoint("BOTTOMRIGHT", pane, "BOTTOMRIGHT", -8, 6)
	scroll:EnableMouseWheel(true)
	scroll:SetScript("OnMouseWheel", function(self, delta)
		local y = self:GetVerticalScroll() - delta * 28
		self:SetVerticalScroll(math.max(0, math.min(y, self:GetVerticalScrollRange())))
	end)
	local edit = CreateFrame("EditBox", nil, scroll)
	edit:SetMultiLine(true)
	edit:SetAutoFocus(false)
	edit:SetFontObject("GameFontHighlightSmall")
	edit:SetWidth(W0 - 44)
	scroll:SetScrollChild(edit)
	edit:SetScript("OnEscapePressed", function(self)
		self:ClearFocus()
		pane:Hide()
	end)
	edit:SetScript("OnTextChanged", function(self, user)
		if user and self:GetText() ~= self.value then            -- read only: put it back, selected
			self:SetText(self.value)
			self:HighlightText()
		end
	end)
	pane.edit = edit
	pane:SetScript("OnHide", Layout)
	pane:Hide()
end

local function Relabel()
	window.title:SetText(L.CON_TITLE)
	window.clear:SetText(L.CON_CLEAR)
	window.copy:SetText(L.CON_COPY)
	window.empty:SetText(L.CON_EMPTY)
end

local function Build()
	window = S.Window("WoWBridgeConsole", ns.Setting("consoleW") or W0, ns.Setting("consoleH") or H0)   -- the panel's strata
	window:Hide()
	window:SetDontSavePosition(true)               -- kept by Save, for this character
	window:ClearAllPoints()
	local x, y = ns.Setting("consoleX"), ns.Setting("consoleY")
	if x and y then
		window:SetPoint("BOTTOMLEFT", UIParent, "BOTTOMLEFT", x, y)
	else                                           -- the right of the screen, away from the frame's corner
		window:SetPoint("RIGHT", UIParent, "RIGHT", -40, -40)
	end
	window:SetScript("OnDragStop", function(self)
		self:StopMovingOrSizing()
		Save()
	end)
	window:SetResizable(true)
	if window.SetResizeBounds then window:SetResizeBounds(WMIN, HMIN, 1600, 1200) end
	S.Close(window)

	local tile = window:CreateTexture(nil, "ARTWORK")
	tile:SetTexture(S.LOGO_SMALL)
	tile:SetSize(18, 18)
	tile:SetPoint("TOPLEFT", window, "TOPLEFT", 16, -13)
	window.title = S.Text(window, "GameFontNormal", C.goldL)
	window.title:SetPoint("LEFT", tile, "RIGHT", 8, 0)
	window.clear = S.Button(window, 60, 20)
	window.clear:SetPoint("TOPRIGHT", window, "TOPRIGHT", -40, -10)
	window.clear:SetScript("OnClick", function() K.Clear() end)
	window.copy = S.Button(window, 60, 20)
	window.copy:SetPoint("RIGHT", window.clear, "LEFT", -6, 0)
	window.copy:SetScript("OnClick", function() OpenPane(L.CON_COPY_HINT, table.concat(K.Lines(), "\n")) end)

	window.tabs = S.Tabs(window, 16, -40, W0 - 32, TABS, function(id)
		tab = id
		full = true
		Draw()
	end)
	window.tabs:SetPoint("TOPRIGHT", window, "TOPRIGHT", -16, -40)

	log = CreateFrame("ScrollingMessageFrame", nil, window)
	log:SetFontObject(ChatFontNormal or "GameFontHighlightSmall")
	log:SetJustifyH("LEFT")
	log:SetMaxLines(MAX)
	log:SetFading(false)
	log:EnableMouse(true)                          -- the [详情] links take clicks
	log:EnableMouseWheel(true)
	log:SetScript("OnMouseWheel", function(self, delta)
		if delta > 0 then self:ScrollUp() else self:ScrollDown() end
	end)
	log:SetHyperlinksEnabled(true)
	log:SetScript("OnHyperlinkClick", function(_, link, text, button)
		local id = tonumber(link:match("^wbconsole:(%d+)$") or "")
		if not id then                             -- an item, a spell … in a print: as the chat does
			if SetItemRef then SetItemRef(link, text, button) end
			return
		end
		for _, e in ipairs(entries) do
			if e.id == id then return OpenPane(L.CON_DETAIL_HINT, Plain(Text(e)) .. "\n\n" .. e.detail) end
		end
	end)
	window.empty = S.Text(window, "GameFontHighlightSmall", C.muted)
	window.empty:SetPoint("TOPLEFT", window, "TOPLEFT", 18, -84)
	window.empty:SetPoint("RIGHT", window, "RIGHT", -18, 0)

	local grip = CreateFrame("Button", nil, window)
	grip:SetSize(16, 16)
	grip:SetPoint("BOTTOMRIGHT", window, "BOTTOMRIGHT", -3, 3)
	local mark = grip:CreateTexture(nil, "OVERLAY")
	mark:SetAllPoints()
	mark:SetTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up")
	grip:SetScript("OnMouseDown", function() window:StartSizing("BOTTOMRIGHT") end)
	grip:SetScript("OnMouseUp", function()
		window:StopMovingOrSizing()
		Layout()
		Save()
	end)

	BuildPane()
	Layout()
	Relabel()
	window:SetScript("OnSizeChanged", Layout)
	window:SetScript("OnShow", function()
		full = true
		Draw()
		K.unseen = 0
		if ns.MinimapButton and ns.MinimapButton.Badge then ns.MinimapButton.Badge(0) end
		ticker = C_Timer.NewTicker(0.2, function()
			if dirty then Draw() end
		end)
	end)
	window:SetScript("OnHide", function()
		if ticker then
			ticker:Cancel()
			ticker = nil
		end
	end)
end

-- which: the tab to show ("all", "errors", "runs", "output"), else the one shown last
function K.Show(which)
	if not window then Build() end
	if which and TAB_WORDS[which] then
		tab = which
		full = true
	end
	window:Raise()                                 -- in front of the panel, whose button may have opened it
	if window:IsShown() then Draw() else window:Show() end
end

function K.Toggle(which)
	if Shown() then window:Hide() else K.Show(which) end
end

function K.Clear()
	entries, merged, batch = {}, {}, nil
	full, dirty = true, true
	K.unseen = 0
	if ns.MinimapButton and ns.MinimapButton.Badge then ns.MinimapButton.Badge(0) end
	if Shown() then Draw() end
end

-- the key binding (Bindings.xml) in the language in force
local function BindingNames()
	_G.BINDING_HEADER_WOWBRIDGE = L.BRAND
	_G.BINDING_NAME_WOWBRIDGE_CONSOLE = L.CON_TITLE
end
BindingNames()

ns.OnLanguage(function()
	BindingNames()
	if window then
		Relabel()
		full = true
		if Shown() then Draw() end
	end
end)
