-- WoWBridge's minimap button and its entry in the addon compartment (the minimap's addon list): left-click opens the
-- settings panel, right-click puts the link's state in the chat, dragging moves the button round the minimap. Its place
-- (minimapAngle, degrees) and whether it shows (minimapHide) are kept for this character. The icon is 无限工坊's mark
-- (Skin.lua), grey while the link is off; the tooltip says the link's state in its colour.
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

local function Tooltip(owner)
	local C, state = S.C, ns.Link.State()
	local c = C[S.StateColor(state)]
	GameTooltip:SetOwner(owner, "ANCHOR_LEFT")
	GameTooltip:AddLine(L.PANEL_TITLE, C.goldL[1], C.goldL[2], C.goldL[3])
	GameTooltip:AddLine(L.ST_LINK:format(ns.StateName(state)), c[1], c[2], c[3])
	GameTooltip:AddLine(" ")
	for _, key in ipairs({ "MM_LEFT", "MM_RIGHT", "MM_DRAG" }) do
		GameTooltip:AddLine(L[key], C.muted[1], C.muted[2], C.muted[3])
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
	button:SetScript("OnClick", function(_, which)
		if which == "RightButton" then _G.WoWBridge.LinkStatus() else ns.Panel.Toggle() end
	end)
	button:SetScript("OnDragStart", function(self)
		GameTooltip:Hide()
		self:SetScript("OnUpdate", Dragging)
	end)
	button:SetScript("OnDragStop", function(self) self:SetScript("OnUpdate", nil) end)
	button:SetScript("OnEnter", Tooltip)
	button:SetScript("OnLeave", function() GameTooltip:Hide() end)
	C_Timer.NewTicker(1, function() icon:SetDesaturated(ns.Link.State() == "off") end)
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
