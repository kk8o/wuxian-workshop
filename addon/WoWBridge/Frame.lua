-- WoWBridge frame layout, builder and renderer. Mirrors src/wuxianworkshop/core/frame.py (the reference for the format).
-- The frame is drawn pixel-exact: the bus ignores its parent's scale and uses 768 / physical screen height, the same
-- factor as Blizzard's PixelUtil, so one UI unit is one physical pixel and every cell is `cell` pixels wide. It sits
-- cfg.x, cfg.y whole physical pixels from the top-left corner (0, 0 by default), its top-left corner fixed whatever its
-- size, and far enough from the right and bottom edges for the biggest frame the link shows (Room); SetUnlocked puts a
-- handle round that room that drags it elsewhere (the companion finds it anywhere in the window). The handle covers no
-- cell: the link goes on.
local _, ns = ...
local Codec = ns.Codec
local floor = math.floor

local Frame = {}
ns.Frame = Frame

local BLACK, BLUE, GREEN, CYAN, RED, MAGENTA, YELLOW, WHITE = 0, 1, 2, 3, 4, 5, 6, 7
local CALIBRATION = { BLACK, WHITE, RED, GREEN, BLUE, CYAN, MAGENTA, YELLOW }   -- K W R G B C M Y
local CONTROL_X0 = 3
local MODE_HI, MODE_LO, TOGGLE = -1, -2, -3   -- control cells whose colour depends on the frame

Frame.MIN_W, Frame.MIN_H = 24, 8
Frame.cfg = { parent = "UIParent", snap = "default", x = 0, y = 0 }   -- x, y: physical pixels from the top-left

local function Palette(v)
	return floor(v / 4) % 2, floor(v / 2) % 2, v % 2
end

---------------------------------------------------------------------------
-- Layout: fixed cells (finders, timing, control) and the data cells in row-major order
---------------------------------------------------------------------------

local layouts = {}

function Frame.Layout(W, H)
	local key = W * 256 + H
	local L = layouts[key]
	if L then return L end
	local fixed = {}
	local function put(x, y, v) fixed[y * W + x] = v end
	for _, f in ipairs({ { 0, 0 }, { W - 3, 0 }, { 0, H - 3 } }) do
		for dy = 0, 2 do
			for dx = 0, 2 do
				put(f[1] + dx, f[2] + dy, (dx == 1 and dy == 1) and MAGENTA or WHITE)
			end
		end
	end
	for x = 3, W - 4 do put(x, 0, (x - 3) % 2 == 0 and BLACK or WHITE) end
	for y = 3, H - 4 do put(0, y, (y - 3) % 2 == 0 and BLACK or WHITE) end
	put(CONTROL_X0, 1, MODE_HI)
	put(CONTROL_X0 + 1, 1, MODE_LO)
	put(CONTROL_X0 + 2, 1, TOGGLE)
	for i, v in ipairs(CALIBRATION) do put(CONTROL_X0 + 2 + i, 1, v) end
	local order = {}
	for y = 0, H - 1 do
		for x = 0, W - 1 do
			local k = y * W + x
			if fixed[k] == nil then order[#order + 1] = k end
		end
	end
	L = { W = W, H = H, fixed = fixed, order = order }
	layouts[key] = L
	return L
end

-- R, G, B arrays indexed by cell (y * W + x + 1) with channels 0..1, or nil and an error message.
-- A probe frame continues after its bit stream with 256-level ramps of red, green, blue and grey, then the palette.
function Frame.Build(W, H, mode, toggle, ftype, session, msg, payload, part, parts)
	local L = Frame.Layout(W, H)
	local cells = Codec.Pack(Codec.Stream(ftype, W, H, session, msg, part or 0, parts or 1, payload), Codec.BITS[mode])
	if #cells > #L.order then
		return nil, ("%d bytes do not fit a %dx%d mode-%d frame"):format(#payload, W, H, mode)
	end
	local R, G, B = {}, {}, {}
	for k, v in pairs(L.fixed) do
		if v == MODE_HI then
			v = floor(mode / 2) % 2 == 1 and WHITE or BLACK
		elseif v == MODE_LO then
			v = mode % 2 == 1 and WHITE or BLACK
		elseif v == TOGGLE then
			v = toggle == 1 and WHITE or BLACK
		end
		R[k + 1], G[k + 1], B[k + 1] = Palette(v)
	end
	local n = #cells
	for i, k in ipairs(L.order) do
		local r, g, b
		local v = cells[i]
		if v then
			if mode == 1 then
				r, g, b = Palette(v)
			elseif mode == 0 then
				r, g, b = Palette(v == 1 and WHITE or BLACK)
			else
				r, g, b = (floor(v / 16) % 4) / 3, (floor(v / 4) % 4) / 3, (v % 4) / 3
			end
		elseif ftype == Codec.TYPE_PROBE then
			local j = i - n - 1
			if j < 1024 then
				local ch, lv = floor(j / 256), (j % 256) / 255
				if ch == 0 then
					r, g, b = lv, 0, 0
				elseif ch == 1 then
					r, g, b = 0, lv, 0
				elseif ch == 2 then
					r, g, b = 0, 0, lv
				else
					r, g, b = lv, lv, lv
				end
			else
				r, g, b = Palette((j - 1024) % 8)
			end
		else
			r, g, b = 0, 0, 0
		end
		R[k + 1], G[k + 1], B[k + 1] = r, g, b
	end
	return R, G, B
end

---------------------------------------------------------------------------
-- Renderer
---------------------------------------------------------------------------

local bus, back
local mover, room, dragging      -- the drag handle (SetUnlocked), the room of the biggest frame, a drag going on
local pool = {}

local function ParentFrame()
	return Frame.cfg.parent == "WorldFrame" and WorldFrame or UIParent
end

local function ApplySnap(t)
	local mode = Frame.cfg.snap
	if mode == "off" then
		t:SetSnapToPixelGrid(false)
		t:SetTexelSnappingBias(0)
	elseif mode == "on" then
		t:SetSnapToPixelGrid(true)
	else
		t:SetSnapToPixelGrid(t.wbSnap0)
		t:SetTexelSnappingBias(t.wbBias0)
	end
	t.wbSnapMode = mode
end

local function EnsureBus()
	if bus then return bus end
	bus = CreateFrame("Frame", "WoWBridgeBus", ParentFrame())
	bus:SetIgnoreParentScale(true)
	bus:SetIgnoreParentAlpha(true)
	bus:SetFrameStrata("TOOLTIP")
	bus:SetFixedFrameStrata(true)
	bus:SetFrameLevel(9000)
	back = bus:CreateTexture(nil, "BACKGROUND")
	back:SetColorTexture(0, 0, 0, 1)   -- the quiet zone, and everything behind the cells
	back:SetAllPoints(bus)
	bus:Hide()
	return bus
end

-- the room the biggest frame of the link takes (the parts of a long message), in pixels
local function Room()
	local P = ns.Link and ns.Link.P
	if not P then return (128 + 2) * 4, (32 + 2) * 4 end
	return (math.max(P.helloW, P.dataW, P.bigW) + 2) * P.cell, (math.max(P.helloH, P.dataH, P.bigH) + 2) * P.cell
end
Frame.Room = Room

-- Re-anchor and re-scale: on DISPLAY_SIZE_CHANGED, GX_RESTARTED, a parent change, or when the physical size moved.
function Frame.Relayout()
	if not bus then return end
	local parent = ParentFrame()
	if bus:GetParent() ~= parent then
		bus:SetParent(parent)
		bus:SetIgnoreParentScale(true)
		bus:SetIgnoreParentAlpha(true)
		bus:SetFrameStrata("TOOLTIP")
		bus:SetFrameLevel(9000)
	end
	local physW, physH = GetPhysicalScreenSize()
	Frame.physW, Frame.physH = physW, physH
	bus:SetScale(768 / physH)
	if dragging then return end                    -- the mouse has it; the place is taken when the drag ends
	bus:ClearAllPoints()
	local x, y = Frame.Offset()
	bus:SetPoint("TOPLEFT", parent, "TOPLEFT", x, -y)
end

-- where the frame goes: cfg.x, cfg.y in whole pixels, kept on the screen with room for the biggest frame (also when the
-- window got smaller), so that a long message's parts do not move it
function Frame.Offset()
	local x, y = floor((Frame.cfg.x or 0) + 0.5), floor((Frame.cfg.y or 0) + 0.5)
	local w, h = Room()
	if bus then w, h = math.max(w, bus:GetWidth()), math.max(h, bus:GetHeight()) end
	if Frame.physW then
		x = math.max(0, math.min(x, Frame.physW - w))
		y = math.max(0, math.min(y, Frame.physH - h))
	end
	return x, y
end

-- the drag handle, while unlocked: a gold line round the frame and a dashed one round the room of the biggest frame
-- (Room: what a long message's parts take), both 2 pixels wide just outside and drawn in the bus's pixels; the mouse area
-- over that room, and a line of help under it on a dark plate (in the UI's own scale, so it reads at the usual size; the
-- colours are Skin.lua's). While dragging, the room stays on the screen. onMoved(x, y) gets the new place in whole pixels
-- once a drag ends.
local marks, used, drawn = {}, 0, nil     -- the line pieces (textures of room), how many are in use, the sizes drawn

local function Piece(x, y, w, h)          -- bus pixels from its top-left corner, y down
	used = used + 1
	local t = marks[used]
	if not t then
		t = room:CreateTexture(nil, "OVERLAY")
		ns.Skin.Fill(t, ns.Skin.Alpha(ns.Skin.C.gold, 0.95))
		marks[used] = t
	end
	t:ClearAllPoints()
	t:SetPoint("TOPLEFT", bus, "TOPLEFT", x, -y)
	t:SetSize(w, h)
	t:Show()
end

-- the lines round w x h pixels from the bus's top-left corner, a pixel away from it; dashed: 6 pixels drawn, 4 not
local function Box(w, h, dashed)
	local step, len = dashed and 10 or w + h + 6, dashed and 6 or w + h + 6
	local function Line(x, y, length, horizontal)
		for at = 0, length - 1, step do
			local n = math.min(len, length - at)
			if horizontal then Piece(x + at, y, n, 2) else Piece(x, y + at, 2, n) end
		end
	end
	Line(-3, -3, w + 6, true)
	Line(-3, h + 1, w + 6, true)
	Line(-3, -3, h + 6, false)
	Line(w + 1, -3, h + 6, false)
end

-- the lines for the frame's size now, and the clamp: the room, not just the frame, has to stay on the screen
local function DrawMarks()
	local fw, fh = floor(bus:GetWidth() + 0.5), floor(bus:GetHeight() + 0.5)
	local rw, rh = Room()
	rw, rh = math.max(rw, fw), math.max(rh, fh)
	local key = fw .. "x" .. fh .. " " .. rw .. "x" .. rh
	if key == drawn then return end
	drawn, used = key, 0
	room:SetSize(rw, rh)
	Box(rw, rh, true)
	Box(fw, fh, false)
	for i = used + 1, #marks do marks[i]:Hide() end
	if bus.SetClampRectInsets then bus:SetClampRectInsets(0, rw - fw, 0, fh - rh) end   -- grown right and down
end

function Frame.SetUnlocked(on, onMoved, label)
	EnsureBus()
	if not on then
		if mover then mover:Hide() end
		if room then room:Hide() end
		bus:SetMovable(false)
		-- the drag's clamp goes (Offset keeps the frame on the screen): grown by the room around the frame up then, it
		-- would push a bigger frame aside
		bus:SetClampedToScreen(false)
		if bus.SetClampRectInsets then bus:SetClampRectInsets(0, 0, 0, 0) end
		if not Frame.shown then bus:Hide() end
		return
	end
	if not mover then
		mover = CreateFrame("Frame", "WoWBridgeMover", UIParent)
		mover:SetFrameStrata("TOOLTIP")
		mover:SetFrameLevel(9100)
		mover:EnableMouse(true)
		mover:RegisterForDrag("LeftButton")
		local Skin = ns.Skin
		mover.text = Skin.Text(mover, "GameFontHighlightSmall", Skin.C.goldL, "OVERLAY")
		mover.text:SetPoint("TOPLEFT", mover, "BOTTOMLEFT", 8, -10)
		local plate = mover:CreateTexture(nil, "BACKGROUND")
		plate:SetPoint("TOPLEFT", mover.text, "TOPLEFT", -8, 5)
		plate:SetPoint("BOTTOMRIGHT", mover.text, "BOTTOMRIGHT", 8, -5)
		Skin.Fill(plate, Skin.Alpha(Skin.C.panel, 0.92))
		mover:SetScript("OnDragStart", function()
			dragging = true
			bus:StartMoving()
		end)
		mover:SetScript("OnDragStop", function()
			bus:StopMovingOrSizing()
			dragging = false
			local scale = bus:GetEffectiveScale() * Frame.physH / 768            -- bus units: physical pixels
			Frame.cfg.x = floor(bus:GetLeft() * scale + 0.5)
			Frame.cfg.y = floor(Frame.physH - bus:GetTop() * scale + 0.5)
			Frame.Relayout()                                                   -- back on whole pixels, on the screen
			if mover.onMoved then mover.onMoved(Frame.Offset()) end
		end)
		room = CreateFrame("Frame", nil, bus)                                  -- in the bus's units: pixels
		room:SetPoint("TOPLEFT", bus, "TOPLEFT")
	end
	mover.onMoved = onMoved
	mover.text:SetText(label or "")
	mover:ClearAllPoints()
	mover:SetPoint("TOPLEFT", room, "TOPLEFT", -3, 3)
	mover:SetPoint("BOTTOMRIGHT", room, "BOTTOMRIGHT", 3, -3)
	bus:SetMovable(true)
	bus:SetClampedToScreen(true)
	pcall(bus.SetDontSavePosition, bus, true)                              -- the place is ours to keep, not the layout cache's
	if not Frame.shown then                                                 -- the link is off: a black box shows the place
		bus:SetSize(66 * 4, 18 * 4)
		Frame.Relayout()
		bus:Show()
	end
	drawn = nil
	room:Show()
	DrawMarks()
	mover:Show()
end

function Frame.IsUnlocked()
	return mover ~= nil and mover:IsShown()
end

function Frame.Show(W, H, cell, R, G, B)
	EnsureBus()
	bus:SetSize((W + 2) * cell, (H + 2) * cell)
	Frame.Relayout()
	local n = W * H
	for k = 0, n - 1 do
		local t = pool[k + 1]
		if not t then
			t = bus:CreateTexture(nil, "ARTWORK")
			t.wbSnap0, t.wbBias0 = t:IsSnappingToPixelGrid(), t:GetTexelSnappingBias()
			pool[k + 1] = t
		end
		if t.wbW ~= W or t.wbCell ~= cell then
			local x, y = k % W, floor(k / W)
			t:ClearAllPoints()
			t:SetSize(cell, cell)
			t:SetPoint("TOPLEFT", bus, "TOPLEFT", (x + 1) * cell, -(y + 1) * cell)
			t.wbW, t.wbCell = W, cell
		end
		if t.wbSnapMode ~= Frame.cfg.snap then ApplySnap(t) end
		t:SetColorTexture(R[k + 1], G[k + 1], B[k + 1], 1)
		t:Show()
	end
	for k = n + 1, #pool do pool[k]:Hide() end
	Frame.shown = { W = W, H = H, cell = cell }
	bus:Show()
	if Frame.IsUnlocked() then DrawMarks() end
end

function Frame.Hide()
	if bus then bus:Hide() end
	Frame.shown = nil
end

function Frame.IsShown()
	return bus ~= nil and bus:IsShown()
end

-- the bus's effective scale (before the first frame: the scale it will get)
function Frame.BusScale()
	if bus then return bus:GetEffectiveScale() end
	local _, physH = GetPhysicalScreenSize()
	return 768 / physH
end

function Frame.DataCells(W, H)
	return #Frame.Layout(W, H).order
end
