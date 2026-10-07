"""A mock WoW client for the addon's own Lua, run under Lua 5.1 (lupa): the WoW API the addon uses (MOCK), the file side
of the client (Client: fonts, sounds, load-on-demand addons) and one UI session of the addon (Session).

The mock models what 1.60.1.70235 was measured to do: GetStringWidth measures up to the ink of the last glyph; SetFont on
a font that is not loaded yet returns false and the font takes effect 0.8 s later; a font is cached by path for the life
of the client (/reload included), a failed load is not; an empty wav played for the first time counts as playing for half
a second. Every frame the addon draws is decoded with the Python decoder."""
import shutil
import tempfile
from pathlib import Path

try:
    import lupa.lua51 as lupa
except ImportError:      # pragma: no cover
    lupa = None

from tests.support import render as R
from wuxianworkshop.agent.diag import parse_diag
from wuxianworkshop.core import fontpack, frame as F, mailbox
from wuxianworkshop.core.decode import decode
from wuxianworkshop.core.locate import locate

ADDON = Path(__file__).resolve().parents[2] / "addon"
def toc_files(folder):
    """the Lua files a .toc lists, in its order (what the client loads)"""
    toc = folder / f"{folder.name}.toc"
    return [line.strip() for line in toc.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.startswith("#") and line.strip().endswith(".lua")]


WORKSHOP = ("!WuxianWorkshop", toc_files(ADDON / "!WuxianWorkshop"))                 # the platform addon, loads first
BRIDGE = ("WoWBridge", toc_files(ADDON / "WoWBridge"))
SAVED = {"!WuxianWorkshop": "WuxianWorkshopDB", "WoWBridge": "WoWBridgeDB"}
FONT_DELAY = 0.8

MOCK = r"""
local py = ...
local clock = py.t0
local timers, frames = {}, {}
function GetTime() return clock end
function debugprofilestop() return os.clock() * 1000 end
time = os.time
date = os.date
C_Timer = {}
function C_Timer.After(d, fn) timers[#timers + 1] = { t = clock + d, fn = fn } end
function C_Timer.NewTicker(d, fn)
	local tk = { t = clock + d, fn = fn, every = d }
	timers[#timers + 1] = tk
	return { Cancel = function() tk.dead = true end }
end
function __run(dt)
	local target = clock + dt
	while true do
		local best, bi
		for i, tm in ipairs(timers) do
			if not tm.dead and (best == nil or tm.t < best.t) then best, bi = tm, i end
		end
		if not best or best.t > target then break end
		clock = best.t
		if best.every then best.t = best.t + best.every else table.remove(timers, bi) end
		best.fn()
	end
	clock = target
end
function __clock() return clock end
function __load(src, name) local f, err = loadstring(src, name); return f, err end
function __dump(v)
	if type(v) == "table" then
		local parts = {}
		for k, x in pairs(v) do
			local key = type(k) == "string" and ("[%q]"):format(k) or ("[" .. tostring(k) .. "]")
			parts[#parts + 1] = key .. "=" .. __dump(x)
		end
		return "{" .. table.concat(parts, ",") .. "}"
	elseif type(v) == "string" then
		return ("%q"):format(v)
	end
	return tostring(v)
end

local Region = {}
Region.__index = Region
local function new(kind, parent)
	return setmetatable({ kind = kind, parent = parent, shown = true, scale = 1, alpha = 1, scripts = {}, events = {} }, Region)
end
function Region:SetIgnoreParentScale(v) self.ignoreScale = v end
function Region:SetIgnoreParentAlpha() end
function Region:SetFrameStrata() end
function Region:SetFixedFrameStrata() end
function Region:SetFrameLevel() end
function Region:SetSize(w, h) self.w, self.h = w, h end
function Region:SetPoint(...) self.point = { ... } end
function Region:ClearAllPoints() end
function Region:SetAllPoints() end
function Region:SetAlpha(a) self.alpha = a end
function Region:SetScale(s) self.scale = s end
function Region:GetEffectiveScale()
	if self.ignoreScale or not self.parent then return self.scale end
	return self.scale * self.parent:GetEffectiveScale()
end
function Region:SetParent(p) self.parent = p end
function Region:GetParent() return self.parent end
function Region:Show()                         -- OnShow / OnHide run when the state changes, as in the client
	local was = self.shown
	self.shown = true
	if not was and self.scripts.OnShow then self.scripts.OnShow(self) end
end
function Region:Hide()
	local was = self.shown
	self.shown = false
	if was and self.scripts.OnHide then self.scripts.OnHide(self) end
end
function Region:IsShown() return self.shown end
function Region:IsVisible() return self.shown and (self.parent == nil or self.parent:IsVisible()) end
function Region:RegisterEvent(e) self.events[e] = true end
function Region:SetScript(k, fn) self.scripts[k] = fn end
function Region:CreateTexture() return new("Texture", self) end
function Region:SetColorTexture(r, g, b) self.rgb = { r, g, b } end
function Region:IsSnappingToPixelGrid() return false end
function Region:GetTexelSnappingBias() return 0 end
function Region:SetSnapToPixelGrid() end
function Region:SetTexelSnappingBias() end
function Region:CreateFontString(_, _, template)
	local fs = new("FontString", self)
	if template then fs.font = { stock = true, path = "Fonts\\FRIZQT__.TTF", size = 12 } end   -- a font object
	return fs
end
function Region:EnableMouse() end
-- what the settings panel, the minimap button and the frame's drag handle use: kept simple, enough to build and click
function Region:GetWidth() return self.w or 0 end
function Region:GetHeight() return self.h or 0 end
function Region:SetHeight(h) self.h = h end
function Region:GetLeft() return self.left or 0 end
function Region:GetTop() return self.top or 0 end
function Region:GetCenter() return (self.w or 0) / 2, (self.h or 0) / 2 end
function Region:SetMovable(v) self.movable = v end
function Region:SetClampedToScreen(v) self.clamped = v end
function Region:SetClampRectInsets(l, r, t, b) self.clampInsets = { l, r, t, b } end
function Region:SetDontSavePosition() end
function Region:RegisterForDrag() end
function Region:RegisterForClicks() end
function Region:StartMoving() self.moving = true end
function Region:StopMovingOrSizing() self.moving = false end
function Region:SetShown(v) self.shown = v and true or false end
function Region:SetJustifyH() end
function Region:SetTextColor() end
function Region:SetTexture(t) self.texture = t end
function Region:SetTexCoord() end
function Region:SetDesaturated(v) self.desaturated = v end
function Region:SetHighlightTexture() end
function Region:SetChecked(v) self.checked = v and true or false end
function Region:GetChecked() return self.checked end
function Region:SetEnabled(v) self.enabled = v end
function Region:IsEnabled() return self.enabled ~= false end
-- what Skin.lua's buttons and check boxes use
function Region:SetVertexColor(r, g, b, a) self.vertex = { r, g, b, a } end
function Region:SetFontString(fs) self.fontString = fs end
function Region:GetFontString() return self.fontString end
function Region:SetPushedTextOffset() end
function Region:SetCheckedTexture(path)
	self.checkedTexture = self.checkedTexture or new("Texture", self)
	self.checkedTexture.texture = path
end
function Region:GetCheckedTexture() return self.checkedTexture end
function Region:GetStringHeight() return 14 end
function Region:UnregisterAllEvents() self.events = {} end
function Region:GetObjectType() return self.kind end
function Region:Click(which) if self.scripts.OnClick then self.scripts.OnClick(self, which or "LeftButton") end end
function Region:SetWidth(w) self.w = w end
function Region:SetWordWrap() end
function Region:SetNonSpaceWrap() end
function Region:SetFontObject() self.font = { stock = true, path = "Fonts\\FRIZQT__.TTF", size = 12 } end
-- an edit box (Skin.lua's CopyBox)
function Region:SetAutoFocus() end
function Region:SetTextInsets() end
function Region:GetText() return self.text end
function Region:SetFocus() self.focused = true end
function Region:ClearFocus()
	local was = self.focused
	self.focused = false
	if was and self.scripts.OnEditFocusLost then self.scripts.OnEditFocusLost(self) end
end
function Region:HighlightText() self.highlighted = true end
-- the debug window (Console.lua): a resizable window, a scrolling message frame, a box of many lines in a scroll frame
function Region:SetResizable(v) self.resizable = v end
function Region:SetResizeBounds() end
function Region:StartSizing() end
function Region:GetBottom() return self.bottom or 0 end
function Region:EnableMouseWheel() end
function Region:AddMessage(t) self.messages = self.messages or {}; self.messages[#self.messages + 1] = t end
function Region:Clear() self.messages = {} end
function Region:SetMaxLines() end
function Region:SetFading() end
function Region:GetScrollOffset() return 0 end
function Region:SetScrollOffset() end
function Region:ScrollUp() end
function Region:ScrollDown() end
function Region:SetHyperlinksEnabled() end
function Region:SetMultiLine() end
function Region:SetScrollChild(c) self.child = c end
function Region:GetVerticalScroll() return 0 end
function Region:SetVerticalScroll() end
function Region:GetVerticalScrollRange() return 0 end
-- a font not loaded yet: SetFont says false and the font arrives later; a loaded one applies at once
local function settle(self)
	local p = self.pending
	if p and clock >= p.t then
		self.font, self.pending = p, nil
		py.font_ready(p.key)
	end
end
function Region:SetFont(path, size)
	settle(self)
	local key, ready = py.font_load(path)
	if not key then return false end
	if ready then
		self.font, self.pending = { key = key, path = path, size = size }, nil
		return true
	end
	self.pending = { key = key, path = path, size = size, t = clock + py.font_delay }
	return false
end
function Region:GetFont() settle(self) if self.font then return self.font.path, self.font.size, "" end end
function Region:SetText(t)
	settle(self)
	if self.kind == "FontString" and not self.font then error("FontString:SetText(): Font not set") end
	self.text = t
end
function Region:GetStringWidth()
	settle(self)
	if not self.text then return 0 end
	if self.font.stock then return 10 * #self.text end
	-- the client lays text out in whole physical pixels: round there, report in this region's units
	local _, physH = GetPhysicalScreenSize()
	local px = self:GetEffectiveScale() * physH / 768
	return math.floor(py.font_width(self.font.key, self.font.size, self.text) * px + 0.5) / px
end

UIParent = new("Frame")
UIParent.scale = 0.888889
WorldFrame = new("Frame")
Minimap = new("Minimap")
Minimap.w, Minimap.h = 198, 198
GameTooltip = new("GameTooltip")
function GameTooltip:SetOwner() self.lines = {} end
function GameTooltip:AddLine(t) self.lines = self.lines or {}; self.lines[#self.lines + 1] = t end
function GameTooltip:SetText(t) self.lines = { t } end
UISpecialFrames = {}
-- the options window's addon categories (Options > AddOns)
__categories = {}
Settings = {
	RegisterCanvasLayoutCategory = function(frame, name) return { frame = frame, name = name } end,
	RegisterAddOnCategory = function(category) __categories[#__categories + 1] = category end,
}
tinsert = table.insert
function GetCursorPosition() return 0, 0 end
function HideUIPanel() end
__locale = "enUS"
function GetLocale() return __locale end
function CreateFrame(kind, name, parent)
	local f = new(kind, parent)
	if name then _G[name] = f end
	frames[#frames + 1] = f
	return f
end
function __fire(event, ...)
	for _, f in ipairs(frames) do
		if f.events[event] and f.scripts.OnEvent then f.scripts.OnEvent(f, event, ...) end
	end
end
local physW, physH = 1920, 1080
function GetPhysicalScreenSize() return physW, physH end
function __resize(w, h) physW, physH = w, h end
function GetScreenWidth() return 1536 end
function GetScreenHeight() return 864 end
function GetScreenDPIScale() return 1.25 end
function GetBuildInfo() return "1.60.1", "70235", "Oct 1 2026", 16001 end
local cvars = { Gamma = "1.000000", Brightness = "50.000000", Contrast = "50.000000", uiscale = "1.000000", useUiScale = "0",
	gxMaximize = "1", maxFPSBk = "30", useMaxFPSBk = "1", RenderScale = "1.000000", ResampleSharpness = "0.2",
	Sound_EnableAllSound = "1", Sound_MasterVolume = "1.0" }
function GetCVar(k) return cvars[k] end
function SetCVar(k, v) cvars[k] = tostring(v) end
C_VideoOptions = {
	GetCurrentGameWindowSize = function() return { x = 1920, y = 1080 } end,
	IsLinearEnabledOnStart = function() return true end,
}
function InCombatLockdown() return false end
DEFAULT_CHAT_FRAME = { AddMessage = function(_, msg) py.chat(msg) end }
SlashCmdList = {}
GameFontNormal = {}

local sounds, handles = {}, 0
function PlaySoundFile(path)
	handles = handles + 1
	sounds[handles] = { len = py.sound(path), t = clock }
	return true, handles     -- 1.60.1.70235 reports even an empty file as playable
end
function StopSound(h) sounds[h] = nil end
C_Sound = { IsPlaying = function(h) local s = sounds[h]; return s ~= nil and clock - s.t < s.len end }

local loaded = {}
C_AddOns = {
	IsAddOnLoaded = function(name) return loaded[name] or false end,
	LoadAddOn = function(name)
		local src = py.addon_source(name)
		if not src then return false, "MISSING" end
		assert(loadstring(src, "@" .. name))(name, {})
		loaded[name] = true
		return true
	end,
}
bit = { band = py.band, bor = py.bor, bxor = py.bxor, lshift = py.lshift, rshift = py.rshift }

-- the client's error and print handlers: an addon may wrap them; the originals keep what reached them
__errors, __printed = {}, {}
local errorHandler = function(msg) __errors[#__errors + 1] = msg end
function geterrorhandler() return errorHandler end
function seterrorhandler(f) errorHandler = f end
function __error(msg) return errorHandler(msg) end          -- what the client does with a Lua error
__stack = "[string \"@Interface/AddOns/Foo/Foo.lua\"]:12: in function `Broken'\n[tail call]: ?\n[C]: in function 'xpcall'\n"
	.. "[Interface/AddOns/WoWBridge/Agent.lua]:383: in function <Interface/AddOns/WoWBridge/Agent.lua:365>\n"
function debugstack() return __stack end                    -- the stack of the next error (a test may set __stack)
local printHandler = function(...) __printed[#__printed + 1] = table.concat({ ... }, " ") end
function getprinthandler() return printHandler end
function setprinthandler(f) printHandler = f end
function print(...) return printHandler(...) end
-- a UI reload: the test sees the flag and starts the next session (Session.reload)
__reloads = 0
function ConsoleExec(cmd) if cmd == "reloadui" then __reloads = __reloads + 1 end end
function ReloadUI() __reloads = __reloads + 1 end
"""


def _tobit(x):
    x = int(x) & 0xFFFFFFFF
    return x - (1 << 32) if x & 0x80000000 else x


def _u(x):
    return int(x) & 0xFFFFFFFF


def _reduce(args, op):
    out = _u(args[0])
    for a in args[1:]:
        out = op(out, _u(a))
    return out


class Client:
    """the file side of the game client (it outlives a /reload): what SetFont, PlaySoundFile and LoadAddOn read"""

    def __init__(self, addons, cache_failed_fonts=False):
        self.addons, self.cache_failed_fonts = addons, cache_failed_fonts
        self.fonts, self.ready, self.failed, self.chat, self.played = {}, set(), set(), [], set()

    def path(self, p):
        p = p.decode().replace("\\", "/")
        assert p.lower().startswith("interface/addons/"), p
        return self.addons / p[len("interface/addons/"):]

    def font_load(self, p):
        """(key, already loaded) or None; the first good read of a path is kept for the life of the client"""
        key = p.decode().lower()
        if key in self.fonts:
            return key.encode(), key in self.ready
        if key in self.failed and self.cache_failed_fonts:
            return None
        try:
            self.fonts[key] = fontpack.metrics(self.path(p).read_bytes())
            return key.encode(), False
        except Exception:
            self.failed.add(key)
            return None

    def font_ready(self, key):
        self.ready.add(key.decode())

    def font_width(self, key, size, text):
        glyphs, upem = self.fonts[key.decode()]
        units, chars = 0, text.decode("utf-8")
        for n, ch in enumerate(chars):
            i = ord(ch) - fontpack.BASE
            adv, ink = glyphs[i] if 0 <= i < len(glyphs) else (512, 64)
            units += ink if n == len(chars) - 1 else adv       # the extent ends at the last glyph's ink
        return units * size / upem

    def sound(self, p):
        first = p not in self.played
        self.played.add(p)
        try:
            data = self.path(p).read_bytes()
        except OSError:
            data = b""
        if len(data) >= 44 and data[:4] == b"RIFF":
            return int.from_bytes(data[40:44], "little") / 8000
        return 0.5 if first else 0           # a failed first load still counts as playing for a while

    def addon_source(self, name):
        f = self.addons / name.decode() / "Inbox.lua"
        return f.read_bytes() if f.exists() else None


class Session:
    """one UI session of the addons in the mock client. comp: a wuxianworkshop.transport.link.Companion that reads the
    frames and writes the mailbox."""

    def __init__(self, test, client=None, db=None, t0=1000.0, config=None, comp=None, tmp=None,
                 early=None, bridge=True, saved=None, locale="enUS"):
        """early: load !WuxianWorkshop first (True), or load it and then call early(lua) (what addons loading in between
        do), before WoWBridge; bridge: load WoWBridge; db: WoWBridgeDB as a Lua table literal; saved: {global name: Lua
        table literal} for the other saved variables; config: entries for WoWBridge_Config; locale: what GetLocale() says"""
        self.test = test
        if tmp is None:
            tmp = Path(tempfile.mkdtemp())
            addons = tmp / "AddOns"
            (addons / "WoWBridge").mkdir(parents=True)
            mailbox.install(addons, pool=64)
        self.tmp, self.addons = tmp, tmp / "AddOns"
        self.client = client or Client(self.addons, (config or {}).pop("cache_failed_fonts", False))
        self.comp, self.comp_running = comp, True
        self.drop_part = None      # the first frame showing this part of a multi-part message is lost on its way
        self.lua = lupa.LuaRuntime(encoding=None, unpack_returned_tuples=True)
        c = self.client
        py = self.lua.table_from({k.encode(): v for k, v in dict(
            t0=t0, font_delay=FONT_DELAY, font_load=c.font_load, font_ready=c.font_ready, font_width=c.font_width,
            sound=c.sound, addon_source=c.addon_source,
            chat=lambda m: c.chat.append(m.decode("utf-8", "replace").replace("||", "|")),
            band=lambda *a: _tobit(_reduce(a, lambda x, y: x & y)), bor=lambda *a: _tobit(_reduce(a, lambda x, y: x | y)),
            bxor=lambda *a: _tobit(_reduce(a, lambda x, y: x ^ y)),
            lshift=lambda x, n: _tobit(_u(x) << (int(n) & 31)), rshift=lambda x, n: _tobit(_u(x) >> (int(n) & 31))).items()})
        self.lua.execute(MOCK.encode(), py)
        self.locale = locale
        self.lua.globals()[b"__locale"] = locale.encode()
        if db:
            self.lua.execute(b"WoWBridgeDB = " + db)
        for name, literal in (saved or {}).items():
            self.lua.execute(name.encode() + b" = " + literal)
        self.early, self.bridge = early, bridge
        self.loaded = []                                    # addon names, in load order
        self.ns = self.lua.table()
        if early is not None:
            self.load_addon(*WORKSHOP)
            if callable(early):
                early(self.lua)
        if bridge:
            self.load_addon(*BRIDGE, ns=self.ns)
        g = self.lua.globals()
        conf = g[b"WoWBridge_Config"]
        for k, v in (config or {}).items():
            if conf is not None:
                conf[k.encode()] = v
        self.frames, self.last_key = [], None              # (time, ftype, msg, payload)
        if bridge:
            build = self.ns[b"Frame"][b"Build"]
            session = self

            def traced(W, H, mode, toggle, ftype, sess, msg, payload, part=None, parts=None):
                res = build(W, H, mode, toggle, ftype, sess, msg, payload, part, parts)
                if res[0] is not None:
                    session.drawn(int(W), int(H), int(mode), int(toggle), int(ftype), int(sess), int(msg), payload,
                                  int(part or 0), int(parts or 1), *res)
                return res
            self.ns[b"Frame"][b"Build"] = traced
        if comp is not None:
            comp.clock = self.now

    def load_addon(self, name, files, ns=None):
        """run an addon's Lua files as the client would, with (addon name, its namespace table) as "..." """
        load = self.lua.globals()[b"__load"]
        folder = ADDON / name
        ns = ns if ns is not None else self.lua.table()
        for f in files:
            fn, err = load((folder / f).read_bytes(), f"@{name}/{f}".encode())
            self.test.assertIsNone(err, f"{name}/{f}")
            fn(name.encode(), ns)
        self.loaded.append(name)

    def drawn(self, W, H, mode, toggle, ftype, sess, msg, payload, part, parts, r, g, b):
        cells = {(k % W, k // W): (round(r[k + 1] * 255), round(g[k + 1] * 255), round(b[k + 1] * 255)) for k in range(W * H)}
        img = R.draw(cells, W, H, 4, origin=(0, 0), size=((W + 4) * 4, (H + 4) * 4), background=(30, 30, 30))
        dec = decode(img, locate(img))
        self.test.assertTrue(dec.ok, dec.error)
        self.test.assertEqual(dec.payload, bytes(payload))
        self.test.assertEqual((dec.header["msg"], dec.header["type"], dec.mode, dec.header["part"], dec.header["parts"]),
                              (msg, ftype, mode, part, parts))
        now = self.now()
        self.frames.append((now, ftype, msg, bytes(payload)))
        key = (sess, ftype, msg, part)
        lost = parts > 1 and part == self.drop_part
        if lost:
            self.drop_part = None
        if self.comp is not None and ftype != F.TYPE_PROBE and key != self.last_key and not lost:
            self.comp.on_frame(ftype, sess, msg, bytes(payload), part, parts)
        self.last_key = key

    def now(self):
        return float(self.lua.globals()[b"__clock"]())

    def run(self, seconds, dt=0.05):
        run = self.lua.globals()[b"__run"]
        for _ in range(int(round(seconds / dt))):
            run(dt)
            if self.comp is not None and self.comp_running:
                self.comp.tick()

    def login(self):
        fire = self.lua.globals()[b"__fire"]
        for name in self.loaded:
            fire(b"ADDON_LOADED", name.encode())
        fire(b"PLAYER_LOGIN")                     # the client's order: every ADDON_LOADED, then these two
        fire(b"PLAYER_ENTERING_WORLD")

    def reload(self, **kw):
        """/reload: a new Lua state with the saved variables, in the same client (fonts stay cached)"""
        g = self.lua.globals()
        dump = g[b"__dump"]
        saved = {SAVED[name]: dump(g[SAVED[name].encode()]) for name in self.loaded if g[SAVED[name].encode()] is not None}
        db = saved.pop("WoWBridgeDB", None)
        kw = dict(dict(early=self.early is not None or None, bridge=self.bridge), **kw)
        kw.setdefault("locale", self.locale)                 # the same client: the same language
        return Session(self.test, client=self.client, db=db, t0=self.now() + 5, comp=self.comp, tmp=self.tmp, saved=saved, **kw)

    def slash(self, text, name="WOWBRIDGE"):
        self.lua.globals()[b"SlashCmdList"][name.encode()](text.encode())

    def wxw(self, text):
        """/wxw, the platform addon's command"""
        self.slash(text, "WUXIANWORKSHOP")

    def chat(self):
        return "\n".join(self.client.chat)

    def last_diag(self):
        return parse_diag([f for f in self.frames if f[1] == F.TYPE_PROBE][-1][3])

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
