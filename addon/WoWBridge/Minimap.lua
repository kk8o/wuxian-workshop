-- WoWBridge's minimap button and its entry in the addon compartment (the minimap's addon list): left-click opens the
-- settings panel, right-click the debug window (Console.lua), Shift-click WuxianKit's window (while the game runs it),
-- dragging moves the button round the minimap. Lua errors
-- that came while the debug window was closed show as a red count on the button (M.Badge). Its place
-- (minimapAngle, degrees) and whether it shows (minimapHide) are kept for this character. The icon is 无限工坊's mark
-- (Skin.lua), grey while the link is off, with the link's state as a diamond in its colour at its foot; the tooltip says
-- the state in words and lists what the mouse does, in two columns.
local _, ns = ...
local L, S = ns.L, ns.Skin

local M = {}
ns.MinimapButton = M

local DEFAULT_ANGLE = 200
local atan2 = math.atan2 or math.atan        -- Lua 5.1 (the client) has atan2; 5.3 and later take two arguments to atan
local button

local function Place()
	local angle = math.rad(ns.Setting("minimapAngle") or DEFAULT_ANGLE)
	local radius = Minimap:GetWidth() / 2 + 5                      -- just outside the round minimap's edge
	button:ClearAllPoints()
	button:SetPoint("CENTER", Minimap, "CENTER", math.cos(angle) * radius, math.sin(angle) * radius)
end

local function Dragging()
	local mx, my = Minimap:GetCenter()
	local px, py = GetCursorPosition()
	local scale = Minimap:GetEffectiveScale()
	local angle = math.deg(atan2(py / scale - my, px / scale - mx)) % 360
	ns.Set("minimapAngle", math.floor(angle + 0.5))
	Place()
end

local STATE_WORDS = { online = "OV_ONLINE", hello = "OV_HELLO", off = "OV_OFF" }

local function Tooltip(owner)
	local C, state = S.C, ns.Link.State()
	local c = C[S.StateColor(state)]
	GameTooltip:SetOwner(owner, "ANCHOR_LEFT")
	GameTooltip:AddLine(L.PANEL_TITLE, C.goldL[1], C.goldL[2], C.goldL[3])
	GameTooltip:AddLine(S.InlineDot(c) .. " " .. L[STATE_WORDS[state] or "OV_HELLO"], c[1], c[2], c[3])
	if ns.Console.unseen > 0 then GameTooltip:AddLine(L.MM_ERRORS:format(ns.Console.unseen), C.bad[1], C.bad[2], C.bad[3]) end
	GameTooltip:AddLine(" ")
	local keys = { "LEFT", "RIGHT", "DRAG" }
	if ns.Kit.Present() then table.insert(keys, 3, "SHIFT") end
	for _, key in ipairs(keys) do
		GameTooltip:AddDoubleLine(L["MM_KEY_" .. key], L["MM_DO_" .. key], C.muted[1], C.muted[2], C.muted[3],
			C.fg[1], C.fg[2], C.fg[3])
	end
	GameTooltip:Show()
end

local function Build()
	button = CreateFrame("Button", "WoWBridgeMinimapButton", Minimap)
	button:SetSize(31, 31)
	button:SetFrameStrata("MEDIUM")
	button:SetFrameLevel(8)
	button:RegisterForClicks("LeftButtonUp", "RightButtonUp")
	button:RegisterForDrag("LeftButton")
	button:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")
	local back = button:CreateTexture(nil, "BACKGROUND")
	back:SetSize(20, 20)
	back:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
	back:SetPoint("TOPLEFT", button, "TOPLEFT", 7, -5)
	local icon = button:CreateTexture(nil, "ARTWORK")
	icon:SetSize(20, 20)
	icon:SetTexture(S.MARK)
	icon:SetPoint("CENTER", back, "CENTER")
	button.icon = icon
	local border = button:CreateTexture(nil, "OVERLAY")
	border:SetSize(53, 53)
	border:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
	border:SetPoint("TOPLEFT", button, "TOPLEFT")
	button.badge = button:CreateTexture(nil, "OVERLAY", nil, 6)
	button.badge:SetSize(15, 12)
	button.badge:SetPoint("TOPRIGHT", button, "TOPRIGHT", -1, -3)
	S.Fill(button.badge, S.C.bad)
	button.count = S.Text(button, "GameFontHighlightSmall", S.C.ink, "OVERLAY")
	button.count:SetPoint("CENTER", button.badge, "CENTER", 0, 0)
	button.count:SetJustifyH("CENTER")
	M.Badge(ns.Console.unseen)
	-- the link's state at the icon's foot: a diamond in its colour, edged dark to stand off the ring
	local edge = button:CreateTexture(nil, "OVERLAY", nil, 4)
	edge:SetTexture(S.STUD)
	edge:SetSize(11, 11)
	edge:SetPoint("CENTER", back, "BOTTOMRIGHT", -1, 1)
	S.Tint(edge, S.C.ink)
	button.dot = button:CreateTexture(nil, "OVERLAY", nil, 5)
	button.dot:SetTexture(S.STUD)
	button.dot:SetSize(7, 7)
	button.dot:SetPoint("CENTER", edge, "CENTER")
	button:SetScript("OnClick", function(_, which)
		if which == "RightButton" then
			ns.Console.Toggle()
		elseif IsShiftKeyDown() and ns.Kit.Present() then
			ns.Kit.Open()
		else
			ns.Panel.Toggle()
		end
	end)
	button:SetScript("OnDragStart", function(self)
		GameTooltip:Hide()
		self:SetScript("OnUpdate", Dragging)
	end)
	button:SetScript("OnDragStop", function(self) self:SetScript("OnUpdate", nil) end)
	button:SetScript("OnEnter", Tooltip)
	button:SetScript("OnLeave", function() GameTooltip:Hide() end)
	local function Paint()
		local state = ns.Link.State()
		icon:SetDesaturated(state == "off")
		S.Tint(button.dot, S.C[S.StateColor(state)])
	end
	Paint()
	C_Timer.NewTicker(1, Paint)
end

-- the count of Lua errors the debug window has not shown yet (0: none, no badge)
function M.Badge(n)
	if not button then return end
	button.badge:SetShown(n > 0)
	button.count:SetShown(n > 0)
	button.count:SetText(n > 99 and "99+" or tostring(n))
	button.badge:SetWidth(n > 9 and 20 or 15)
end

-- shown or hidden as the setting says, in its place
function M.Update()
	if not Minimap then return end
	if not button then Build() end
	Place()
	button:SetShown(not ns.Setting("minimapHide"))
end

local function RegisterCompartment()
	if not (AddonCompartmentFrame and AddonCompartmentFrame.RegisterAddon) then return end
	pcall(AddonCompartmentFrame.RegisterAddon, AddonCompartmentFrame, {
		text = L.PANEL_TITLE,
		icon = S.LOGO_SMALL,
		notCheckable = true,
		func = function() ns.Panel.Toggle() end,
		funcOnEnter = function(owner) Tooltip(owner) end,
		funcOnLeave = function() GameTooltip:Hide() end,
	})
end

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_ENTERING_WORLD")
events:SetScript("OnEvent", function()
	events:UnregisterAllEvents()
	M.Update()
	RegisterCompartment()
end)
