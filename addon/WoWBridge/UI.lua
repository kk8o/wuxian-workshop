-- WoWBridge dialogs: the button the companion's "reload" command puts up (only a click or key press may reload the UI
-- on this client: ReloadUI from a timer fails with "interface action failed", so the click has to be the user's own),
-- and the mailbox slot warnings. Slots are read once per game process, and only a full restart of the game makes them
-- readable again: a chat warning when 400 and when 100 are left, a dialog when none are. The words are Locale.lua's; a
-- dialog takes the language in force when it first shows. The look is Skin.lua's.
local _, ns = ...
local L, S = ns.L, ns.Skin
local C = S.C

local U = {}
ns.UI = U

local SLOT_WARN = { 400, 100 }
local slotWarned, slotsGone = {}, nil
local ask

-- a small window in the middle of the screen: the tile and the name, a text, and buttons in the bottom-right corner
-- { { label, width, onClick(dialog), kind }, ... }, the first rightmost (the one to press: kind "primary")
local function Dialog(name, text, buttons)
	local d = S.Window(name, 380, 140, "FULLSCREEN_DIALOG")
	d:SetPoint("CENTER", UIParent, "CENTER", 0, 160)
	local tile = d:CreateTexture(nil, "ARTWORK")
	tile:SetTexture(S.LOGO_SMALL)
	tile:SetSize(22, 22)
	tile:SetPoint("TOPLEFT", d, "TOPLEFT", 18, -16)
	local title = S.Text(d, "GameFontNormal", C.goldL)
	title:SetPoint("LEFT", tile, "RIGHT", 9, 0)
	title:SetText(L.BRAND)
	local body = S.Text(d, "GameFontHighlight", C.fg)
	body:SetPoint("TOPLEFT", tile, "BOTTOMLEFT", 0, -14)
	body:SetWidth(344)
	body:SetText(text)
	d:SetHeight(16 + 22 + 14 + math.max(16, body:GetStringHeight()) + 20 + 26 + 16)
	local x = -18
	d.buttons = {}
	for i, b in ipairs(buttons) do
		local button = S.Button(d, b[2], 26, b[4])
		button:SetPoint("BOTTOMRIGHT", d, "BOTTOMRIGHT", x, 16)
		button:SetText(b[1])
		button:SetScript("OnClick", function() b[3](d) end)
		d.buttons[i] = button
		x = x - b[2] - 8
	end
	return d
end

local function Alert(d)
	d:Show()
	pcall(PlaySound, SOUNDKIT and SOUNDKIT.READY_CHECK or 8960)
end

-- the agent wants a UI reload; the answers go back typed as RELOAD messages ("asked: ...", "later: ...")
function U.AskReload()
	if not ask then
		ask = Dialog("WoWBridgeReloadDialog", L.RELOAD_ASK, {
			{ L.RELOAD_NOW, 120, function() ReloadUI() end, "primary" },
			{ L.RELOAD_LATER, 90, function(d)
				d:Hide()
				ns.Link.Send("later: the user put the reload off", "reload")
			end },
		})
		ask.reload, ask.later = ask.buttons[1], ask.buttons[2]
	end
	Alert(ask)
	ns.Print(L.RELOAD_CHAT)
	ns.Link.Send("asked: a button is up; the UI reloads when the user clicks it", "reload")
end

-- runs every 0.5 s (Link.Tick)
function U.CheckSlots()
	local Mailbox, Link = ns.Mailbox, ns.Link
	if Mailbox.Phase() == "full" then
		if not slotsGone then
			ns.Print(L.SLOTS_GONE_CHAT)
			slotsGone = Dialog("WoWBridgeSlotsDialog", L.SLOTS_GONE_DIALOG, { { L.GOT_IT, 100, function(d) d:Hide() end, "primary" } })
			Alert(slotsGone)
		end
		return
	end
	if Link.State() ~= "online" or Mailbox.Phase() ~= "poll" then return end   -- online: the heartbeat is known
	local left = Link.SlotsLeft()
	local hit
	for _, n in ipairs(SLOT_WARN) do
		if left <= n and not slotWarned[n] then slotWarned[n], hit = true, true end
	end
	if hit then
		ns.Print(L.SLOTS_LEFT:format(left, left * Link.P.hb / 3600))
	end
end
