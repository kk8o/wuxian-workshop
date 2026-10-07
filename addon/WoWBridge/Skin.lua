-- 无限工坊's look inside the game, the same as the app's and wuxianwow.com's (app.css): a dark panel with a gold-brown
-- hairline and a gold diamond on each corner, the logo at its head, gold section headings with a hairline after them,
-- quiet buttons that turn gold under the mouse (the one to press is gold), check boxes ticked with the diamond, tabs
-- marked with a gold bar (the app's navigation), the ∞ ornament. Plain frames and textures, none of the client's
-- templates: it looks the same whatever the client's own skin.
-- The textures are Media's TGAs (scripts/make_icons.py draws them from the app's SVGs; the white ones are tinted here);
-- the client sees a new texture file only after a full restart of the game.
local _, ns = ...

local S = {}
ns.Skin = S

local MEDIA = "Interface\\AddOns\\WoWBridge\\Media\\"
S.WEBSITE = "https://wuxianwow.com/workshop"     -- 无限工坊's site: the download, the guides, the agents' setup
S.LOGO, S.LOGO_SMALL, S.MARK, S.MARK_WIDE = MEDIA .. "logo", MEDIA .. "logo-small", MEDIA .. "mark", MEDIA .. "mark-wide"
S.STUD, S.INF, S.CROSS = MEDIA .. "stud", MEDIA .. "inf", MEDIA .. "cross"

-- the site's colours (app.css :root; ink: text on gold); C: r, g, b, a in 0..1, HEX: for |cff colour codes
S.HEX = { panel = "23242a", panel2 = "2b2f37", field = "1b1c21", line = "35363d", fg = "e8e4d8", muted = "9c9ea6",
	gold = "d8a85a", goldL = "f1c97a", goldD = "8a6424", goldSoft = "3a3120", sky = "7fb3e0", ok = "6fcf97", bad = "f28b82",
	ink = "1b1c21" }
local C = {}
for k, hex in pairs(S.HEX) do
	C[k] = { tonumber(hex:sub(1, 2), 16) / 255, tonumber(hex:sub(3, 4), 16) / 255, tonumber(hex:sub(5, 6), 16) / 255, 1 }
end
S.C = C

function S.Alpha(c, a) return { c[1], c[2], c[3], a } end
function S.Fill(t, c) t:SetColorTexture(c[1], c[2], c[3], c[4]) end
function S.Tint(t, c) t:SetVertexColor(c[1], c[2], c[3], c[4]) end
function S.Ink(fs, c) fs:SetTextColor(c[1], c[2], c[3], c[4]) end

-- the link's state ("online", "hello", "off") as a colour name of C / HEX: online green, waiting blue, off grey
function S.StateColor(state)
	return state == "online" and "ok" or state == "off" and "muted" or "sky"
end

-- from c1 to c2 ("VERTICAL": bottom to top, "HORIZONTAL": left to right); a client without colour gradients gets the
-- fallback (else c1) flat
function S.Gradient(t, orientation, c1, c2, fallback)
	t:SetColorTexture(1, 1, 1, 1)
	if CreateColor and t.SetGradient and pcall(t.SetGradient, t, orientation, CreateColor(unpack(c1)), CreateColor(unpack(c2))) then
		return
	end
	S.Fill(t, fallback or c1)
end

-- one physical pixel in a frame's units: the hairlines stay one pixel at any UI scale
function S.Pixel(frame)
	local _, physH = GetPhysicalScreenSize()
	return 768 / physH / frame:GetEffectiveScale()
end

-- four hairlines round a region (the owner's own textures: the owner itself, or a texture of it); S.Edges colours them
function S.Border(owner, region, layer)
	region = region or owner
	local p = S.Pixel(owner)
	local e = {}
	for i = 1, 4 do e[i] = owner:CreateTexture(nil, layer or "BORDER") end
	e[1]:SetPoint("TOPLEFT", region, "TOPLEFT")
	e[1]:SetPoint("TOPRIGHT", region, "TOPRIGHT")
	e[1]:SetHeight(p)
	e[2]:SetPoint("BOTTOMLEFT", region, "BOTTOMLEFT")
	e[2]:SetPoint("BOTTOMRIGHT", region, "BOTTOMRIGHT")
	e[2]:SetHeight(p)
	e[3]:SetPoint("TOPLEFT", region, "TOPLEFT")
	e[3]:SetPoint("BOTTOMLEFT", region, "BOTTOMLEFT")
	e[3]:SetWidth(p)
	e[4]:SetPoint("TOPRIGHT", region, "TOPRIGHT")
	e[4]:SetPoint("BOTTOMRIGHT", region, "BOTTOMRIGHT")
	e[4]:SetWidth(p)
	return e
end

function S.Edges(e, c)
	for i = 1, 4 do S.Fill(e[i], c) end
end

-- a gold diamond on each corner, half outside
function S.Studs(frame, size)
	frame.studs = {}
	for i, at in ipairs({ "TOPLEFT", "TOPRIGHT", "BOTTOMLEFT", "BOTTOMRIGHT" }) do
		local t = frame:CreateTexture(nil, "OVERLAY")
		t:SetTexture(S.STUD)
		t:SetSize(size or 9, size or 9)
		t:SetPoint("CENTER", frame, at)
		S.Tint(t, C.gold)
		frame.studs[i] = t
	end
end

-- a text in one of the client's font objects (the typeface follows the client's language) and our colour
function S.Text(parent, font, c, layer)
	local fs = parent:CreateFontString(nil, layer or "ARTWORK", font or "GameFontHighlightSmall")
	fs:SetJustifyH("LEFT")
	S.Ink(fs, c or C.fg)
	return fs
end

-- a window: the panel's gradient, the gold-brown hairline, the studs; dragged by any free spot, kept on the screen
-- (strata: "DIALOG" unless given; the dialogs take "FULLSCREEN_DIALOG", above the panel)
function S.Window(name, w, h, strata)
	local f = CreateFrame("Frame", name, UIParent)
	f:SetSize(w, h)
	f:SetFrameStrata(strata or "DIALOG")
	f:SetClampedToScreen(true)
	f:EnableMouse(true)
	f:SetMovable(true)
	f:RegisterForDrag("LeftButton")
	f:SetScript("OnDragStart", f.StartMoving)
	f:SetScript("OnDragStop", f.StopMovingOrSizing)
	f.bg = f:CreateTexture(nil, "BACKGROUND")
	f.bg:SetAllPoints()
	S.Gradient(f.bg, "VERTICAL", S.Alpha(C.panel, 0.97), S.Alpha(C.panel2, 0.97))
	f.edges = S.Border(f)
	S.Edges(f.edges, C.goldD)
	S.Studs(f)
	return f
end

-- the close button in a window's top-right corner: a quiet cross, gold under the mouse
function S.Close(frame)
	local b = CreateFrame("Button", nil, frame)
	b:SetSize(24, 24)
	b:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -8, -8)
	local x = b:CreateTexture(nil, "ARTWORK")
	x:SetTexture(S.CROSS)
	x:SetSize(14, 14)
	x:SetPoint("CENTER", b, "CENTER")
	S.Tint(x, C.muted)
	b:SetScript("OnEnter", function() S.Tint(x, C.goldL) end)
	b:SetScript("OnLeave", function() S.Tint(x, C.muted) end)
	b:SetScript("OnClick", function() frame:Hide() end)
	frame.close = b
	return b
end

-- a hairline across a frame, y down from its top, inset from both sides
function S.Rule(frame, y, inset)
	local t = frame:CreateTexture(nil, "ARTWORK")
	t:SetPoint("TOPLEFT", frame, "TOPLEFT", inset, y)
	t:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -inset, y)
	t:SetHeight(S.Pixel(frame))
	S.Fill(t, S.Alpha(C.goldD, 0.6))
	return t
end

-- a section heading at x, y of the parent, w wide: gold words, then a hairline to the end (in a row of its own, so both
-- ends of the line sit on the words' middle); returns the words
function S.Heading(parent, x, y, w)
	local row = CreateFrame("Frame", nil, parent)
	row:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
	row:SetSize(w, 16)
	local fs = S.Text(row, "GameFontNormal", C.gold)
	fs:SetPoint("LEFT", row, "LEFT")
	local line = row:CreateTexture(nil, "ARTWORK")
	line:SetPoint("LEFT", fs, "RIGHT", 10, 0)
	line:SetPoint("RIGHT", row, "RIGHT")
	line:SetHeight(S.Pixel(parent))
	S.Fill(line, S.Alpha(C.goldD, 0.55))
	return fs
end

-- the ornament (the app's .orn): hairlines fading out to both sides of a line-art ∞
function S.Orn(parent, x, y, w)
	local row = CreateFrame("Frame", nil, parent)
	row:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
	row:SetSize(w, 14)
	local inf = row:CreateTexture(nil, "ARTWORK")
	inf:SetTexture(S.INF)
	inf:SetSize(28, 14)
	inf:SetPoint("CENTER", row, "CENTER")
	S.Tint(inf, C.goldD)
	local p = S.Pixel(parent)
	local left, right = row:CreateTexture(nil, "ARTWORK"), row:CreateTexture(nil, "ARTWORK")
	left:SetPoint("LEFT", row, "LEFT")
	left:SetPoint("RIGHT", inf, "LEFT", -10, 0)
	left:SetHeight(p)
	right:SetPoint("LEFT", inf, "RIGHT", 10, 0)
	right:SetPoint("RIGHT", row, "RIGHT")
	right:SetHeight(p)
	S.Gradient(left, "HORIZONTAL", S.Alpha(C.goldD, 0), C.goldD, S.Alpha(C.goldD, 0.6))
	S.Gradient(right, "HORIZONTAL", C.goldD, S.Alpha(C.goldD, 0), S.Alpha(C.goldD, 0.6))
	return row
end

-- a small diamond to colour (a state)
function S.Dot(parent, size)
	local t = parent:CreateTexture(nil, "ARTWORK")
	t:SetTexture(S.STUD)
	t:SetSize(size or 8, size or 8)
	return t
end

-- a button's or a check box's colours for its state: the kind ("primary": gold, the one to press), selected (the one in
-- force of a group: marked, not clickable), the mouse over it, disabled
function S.Paint(b)
	local on = b:IsEnabled()
	local hover = b.hover and on
	if b.isCheck then
		S.Edges(b.edges, hover and C.gold or C.line)
		S.Ink(b.label, hover and C.goldL or C.fg)
		return
	end
	local bg, edge, text
	if b.kind == "primary" then
		bg = hover and C.goldL or C.gold
		edge, text = bg, C.ink
	elseif b.selected then
		bg, edge, text = C.goldSoft, C.gold, C.goldL
	else
		bg, edge, text = C.panel2, hover and C.goldD or C.line, hover and C.goldL or C.fg
	end
	S.Fill(b.bg, bg)
	S.Edges(b.edges, edge)
	S.Ink(b.label, text)
	b:SetAlpha((on or b.selected) and 1 or 0.45)
end

-- b.tip(): the words of a tooltip
local function Enter(self)
	self.hover = true
	S.Paint(self)
	local text = self.tip and self.tip()
	if text then
		GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
		GameTooltip:SetText(text, C.fg[1], C.fg[2], C.fg[3], 1, true)
		GameTooltip:Show()
	end
end

local function Leave(self)
	self.hover = false
	S.Paint(self)
	if self.tip then GameTooltip:Hide() end
end

-- a button, w x h; kind nil (quiet) or "primary"; its words with SetText
function S.Button(parent, w, h, kind)
	local b = CreateFrame("Button", nil, parent)
	b:SetSize(w, h or 24)
	b.kind = kind
	b.bg = b:CreateTexture(nil, "BACKGROUND")
	b.bg:SetAllPoints()
	b.edges = S.Border(b)
	b.label = S.Text(b, "GameFontHighlightSmall")
	b.label:SetPoint("CENTER", b, "CENTER", 0, 0)
	b.label:SetJustifyH("CENTER")
	b:SetFontString(b.label)
	b:SetPushedTextOffset(0, -1)
	b:SetScript("OnEnter", Enter)
	b:SetScript("OnLeave", Leave)
	b:SetScript("OnEnable", S.Paint)
	b:SetScript("OnDisable", S.Paint)
	S.Paint(b)
	return b
end

-- a check box and its words (cb.label), w wide: the whole row takes the click; ticked, the gold diamond
function S.Check(parent, w)
	local cb = CreateFrame("CheckButton", nil, parent)
	cb:SetSize(w, 22)
	cb.isCheck = true
	cb.box = cb:CreateTexture(nil, "BACKGROUND")
	cb.box:SetSize(14, 14)
	cb.box:SetPoint("LEFT", cb, "LEFT", 1, 0)
	S.Fill(cb.box, C.field)
	cb.edges = S.Border(cb, cb.box)
	cb:SetCheckedTexture(S.STUD)
	local tick = cb:GetCheckedTexture()
	tick:ClearAllPoints()
	tick:SetPoint("CENTER", cb.box, "CENTER")
	tick:SetSize(8, 8)
	S.Tint(tick, C.gold)
	cb.label = S.Text(cb, "GameFontHighlightSmall")
	cb.label:SetPoint("LEFT", cb.box, "RIGHT", 9, 0)
	cb.label:SetPoint("RIGHT", cb, "RIGHT")
	cb:SetScript("OnEnter", Enter)
	cb:SetScript("OnLeave", Leave)
	S.Paint(cb)
	return cb
end

-- a row of tabs at x, y of the parent, w wide, over a hairline: the selected tab's words light gold with a gold bar
-- under them, the others quiet (light under the mouse). ids: the tabs, left to right; onSelect(id) after a click.
-- bar:SetLabels(text) gives each tab its words (text(id)) and lays the row out again (the widths follow the words);
-- bar:Select(id) marks one
function S.Tabs(parent, x, y, w, ids, onSelect)
	local bar = CreateFrame("Frame", nil, parent)
	bar:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
	bar:SetSize(w, 26)
	local line = bar:CreateTexture(nil, "ARTWORK")
	line:SetPoint("BOTTOMLEFT", bar, "BOTTOMLEFT")
	line:SetPoint("BOTTOMRIGHT", bar, "BOTTOMRIGHT")
	line:SetHeight(S.Pixel(parent))
	S.Fill(line, S.Alpha(C.goldD, 0.55))
	bar.tabs = {}

	function bar.Paint()
		for _, b in ipairs(bar.tabs) do
			local on = b.id == bar.selected
			S.Ink(b.label, on and C.goldL or (b.hover and C.fg or C.muted))
			b.mark:SetShown(on)
		end
	end

	function bar.Select(id)
		bar.selected = id
		bar.Paint()
	end

	function bar.SetLabels(text)
		local at = 0
		for _, b in ipairs(bar.tabs) do
			b.label:SetText(text(b.id))
			local width = math.floor(b.label:GetStringWidth() + 0.5) + 24
			b:SetWidth(width)
			b:ClearAllPoints()
			b:SetPoint("BOTTOMLEFT", bar, "BOTTOMLEFT", at, 0)
			at = at + width + 2
		end
	end

	for i, id in ipairs(ids) do
		local b = CreateFrame("Button", nil, bar)
		b:SetHeight(26)
		b.id = id
		b.label = S.Text(b, "GameFontNormal", C.muted)
		b.label:SetPoint("CENTER", b, "CENTER", 0, 2)
		b.mark = b:CreateTexture(nil, "OVERLAY")
		b.mark:SetPoint("BOTTOMLEFT", b, "BOTTOMLEFT", 8, 0)
		b.mark:SetPoint("BOTTOMRIGHT", b, "BOTTOMRIGHT", -8, 0)
		b.mark:SetHeight(2)
		S.Fill(b.mark, C.gold)
		b:SetScript("OnEnter", function(self) self.hover = true bar.Paint() end)
		b:SetScript("OnLeave", function(self) self.hover = false bar.Paint() end)
		b:SetScript("OnClick", function(self)
			bar.Select(self.id)
			onSelect(self.id)
		end)
		bar.tabs[i] = b
	end
	return bar
end

-- a box that holds a text to copy, w wide: shown with the text selected (the game gives addons no clipboard, so the
-- player presses Ctrl+C); typing cannot change it; Escape, Enter or a click elsewhere hides it again (onHide after)
function S.CopyBox(parent, w, onHide)
	local box = CreateFrame("EditBox", nil, parent)
	box:SetSize(w, 22)
	box:SetAutoFocus(false)
	box:SetFontObject("GameFontHighlightSmall")
	box:SetTextInsets(6, 6, 0, 0)
	box.bg = box:CreateTexture(nil, "BACKGROUND")
	box.bg:SetAllPoints()
	S.Fill(box.bg, C.field)
	box.edges = S.Border(box)
	S.Edges(box.edges, C.gold)
	box:Hide()
	local function close(self)
		self:ClearFocus()
		self:Hide()
		if onHide then onHide() end
	end
	box:SetScript("OnEscapePressed", close)
	box:SetScript("OnEnterPressed", close)
	box:SetScript("OnEditFocusLost", function(self) if self:IsShown() then close(self) end end)
	box:SetScript("OnTextChanged", function(self, user)
		if user and self:GetText() ~= self.value then                 -- read only: put it back, selected
			self:SetText(self.value)
			self:HighlightText()
		end
	end)
	function box.Open(value)
		box.value = value
		box:SetText(value)
		box:Show()
		box:SetFocus()
		box:HighlightText()
	end
	return box
end

-- the chat prefix: the mark (its wide cut, the line's height) and the name in gold
function S.Prefix(name)
	return "|T" .. S.MARK_WIDE .. ":14:28|t |cff" .. S.HEX.gold .. name .. "|r "
end
