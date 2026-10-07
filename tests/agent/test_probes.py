"""The probes' Lua (agent/probes.py) under Lua 5.1 with stand-ins for the client's frames: the JSON the game answers
with, a trace of fired events (the chattiest counted only, unknown names reported, restricted ones refused before they
reach the game), frames described with their rectangles in client pixels, and the frames under the mouse."""
import json
import unittest

try:
    import lupa.lua51 as lupa
except ImportError:      # pragma: no cover
    lupa = None

from wuxianworkshop.agent import probes as P

STUB = r"""
__now = 100
function GetTime() return __now end
function GetPhysicalScreenSize() return 1920, 1080 end
KNOWN = nil                                    -- nil: every event name is known
local Frame = {}
Frame.__index = Frame
function CreateFrame(kind, name)
	local f = setmetatable({ events = {} }, Frame)
	if name then _G[name] = f end
	return f
end
function Frame:RegisterEvent(e)
	if KNOWN and not KNOWN[e] then error("Attempt to register unknown event \"" .. e .. "\"") end
	self.events[e] = true
end
function Frame:UnregisterAllEvents() self.events = {} end
function Frame:SetScript(k, fn) self[k] = fn end
function fire(e, ...)
	local f = _G.WuxianWorkshopTrace
	if f.events[e] and f.OnEvent then f.OnEvent(f, e, ...) end
end

-- the UI for inspect: frames (strata, children, scripts) and regions (a draw layer, text or texture)
local Base = {}
function Base.GetObjectType(self) return self.otype end
function Base.IsShown(self) return self.shown ~= false end
function Base.IsVisible(self) return self.shown ~= false and (self.parent == nil or self.parent:IsVisible()) end
function Base.GetAlpha(self) return self.alpha or 1 end
function Base.GetWidth(self) return self.w end
function Base.GetHeight(self) return self.h end
function Base.GetRect(self) if not self.l then return nil end return self.l, self.b, self.w, self.h end
function Base.GetEffectiveScale(self) return self.scale or 1 end
function Base.GetParent(self) return self.parent end
function Base.GetName(self) return self.name end
function Base.GetDebugName(self) return self.name or ((self.parent and self.parent:GetDebugName() or "?") .. ".anon") end
function Base.GetNumPoints(self) return #(self.points or {}) end
function Base.GetPoint(self, i) local p = self.points[i] return p[1], p[2], p[3], p[4], p[5] end
function Base.IsForbidden(self) return self.forbidden == true end
local FrameM = { __index = function(t, k) return rawget(FrameMethods, k) or Base[k] end }
FrameMethods = {
	GetFrameStrata = function(self) return "MEDIUM" end,
	GetFrameLevel = function(self) return 5 end,
	IsMouseEnabled = function(self) return true end,
	IsProtected = function(self) return false end,
	HasScript = function(self, h) return h ~= "OnValueChanged" end,
	GetScript = function(self, h) return self.scripts and self.scripts[h] end,
	GetChildren = function(self) return unpack(self.kids or {}) end,
	GetRegions = function(self) return unpack(self.regions or {}) end,
}
local TextM = { __index = function(t, k)
	if k == "GetDrawLayer" then return function() return "ARTWORK" end end
	if k == "GetText" then return function(self) return self.text end end
	return Base[k]
end }
function NewFrame(t) t.otype = t.otype or "Frame" return setmetatable(t, FrameM) end
function NewText(t) t.otype = "FontString" return setmetatable(t, TextM) end
"""


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Probes(unittest.TestCase):
    def setUp(self):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(STUB)

    def run_chunk(self, chunk):
        return P.parse([self.lua.execute(chunk)])

    def test_json_from_lua(self):
        self.lua.execute("SECRET = {} function issecretvalue(v) return v == SECRET end")    # the client's, there first
        got = self.run_chunk(P.LUA_JSON + r"""
			return J({ n = 3, f = 1.5, big = 2^40, s = 'say "hi"\n', b = false, list = { 1, "两", true }, empty = {},
				hidden = SECRET, deep = { { { { { 1 } } } } } })
		""")
        self.assertEqual(got["n"], 3)
        self.assertEqual(got["f"], 1.5)
        self.assertEqual(got["big"], 2 ** 40)                          # past 32 bits: no %d overflow
        self.assertEqual(got["s"], 'say "hi"\n')
        self.assertIs(got["b"], False)
        self.assertEqual(got["list"], [1, "两", True])
        self.assertEqual(got["empty"], [])
        self.assertEqual(got["hidden"], "<secret>")
        self.assertEqual(got["deep"], [[["<table>"]]])                 # 4 levels, then cut

    def test_a_filtered_trace(self):
        self.lua.execute('KNOWN = { BAG_UPDATE = true, LOOT_OPENED = true, UNIT_AURA = true }')
        begun = self.run_chunk(P.trace_start("BAG_UPDATE, loot_opened, NOT_AN_EVENT", max_events=3))
        self.assertEqual((begun["tracing"], begun["unknown"]), (2, ["NOT_AN_EVENT"]))
        fire = self.lua.globals().fire
        self.lua.execute("__now = 100.25")
        fire("BAG_UPDATE", 0)
        fire("UNIT_AURA", "player")                                   # not asked for: not registered at all
        self.lua.execute("__now = 101.5")
        fire("LOOT_OPENED", False, "x" * 300, self.lua.eval("{}"))
        fire("BAG_UPDATE", 1)
        fire("BAG_UPDATE", 2)                                         # the fourth: past max_events
        self.lua.execute("__now = 103")
        res = P.trace_result(self.run_chunk(P.trace_stop()))
        self.assertEqual(res["seconds"], 3)
        self.assertEqual(res["kept"], 3)
        self.assertEqual(res["dropped"], 1)
        self.assertEqual(res["counts"], [dict(event="BAG_UPDATE", count=3), dict(event="LOOT_OPENED", count=1)])
        first, loot = res["events"][0], res["events"][1]
        self.assertEqual((first["t"], first["event"], first["args"], first["n"]), (0.25, "BAG_UPDATE", [0], 1))
        self.assertEqual(loot["args"][0], False)
        self.assertEqual(len(loot["args"][1]), 161)                  # cut to 160 characters and an ellipsis
        self.assertTrue(loot["args"][2].startswith("table: "))        # a table as its tostring
        self.assertEqual(self.lua.eval("next(WuxianWorkshopTrace.events)"), None)   # stopped: nothing registered

    def test_an_unfiltered_trace_counts_the_chatty_events_only(self):
        begun = self.run_chunk(P.trace_start())
        self.assertEqual(begun["tracing"], len(P.select_events(None)[0]))   # every event an addon may register
        fire = self.lua.globals().fire
        for _ in range(5):
            fire("UNIT_AURA", "player")
        fire("PLAYER_TARGET_CHANGED")
        res = P.trace_result(self.run_chunk(P.trace_stop()))
        self.assertEqual([e["event"] for e in res["events"]], ["PLAYER_TARGET_CHANGED"])
        self.assertEqual(res["counts"][0], dict(event="UNIT_AURA", count=5))

    def test_the_restricted_events_never_reach_the_game(self):
        events, quiet = P.select_events(None)
        self.assertNotIn("COMBAT_LOG_EVENT_UNFILTERED", events)
        self.assertNotIn("COMBAT_LOG_EVENT_INTERNAL_UNFILTERED", events)
        self.assertIn("BAG_UPDATE", events)
        self.assertIn("UNIT_AURA", quiet)
        self.assertNotIn("COMBAT_LOG_EVENT_UNFILTERED", P.select_events("COMBAT_*")[0])   # a glob leaves them out
        with self.assertRaises(ValueError) as cm:
            P.select_events("BAG_UPDATE, COMBAT_LOG_EVENT_UNFILTERED")
        self.assertIn("COMBAT_LOG_EVENT_UNFILTERED", str(cm.exception))
        with self.assertRaises(ValueError):
            P.select_events("NOTHING_LIKE_THIS_*")

    def test_inspect_a_frame(self):
        self.lua.execute(r"""
			UIParent = NewFrame({ name = "UIParent", l = 0, b = 0, w = 1920, h = 1080, scale = 768 / 1080 })
			MyAddonFrame = NewFrame({ name = "MyAddonFrame", parent = UIParent, l = 100, b = 600, w = 200, h = 100, scale = 768 / 1080,
				points = { { "CENTER", UIParent, "CENTER", 0, 40 } }, scripts = { OnShow = function() end, OnEvent = function() end } })
			local title = NewText({ parent = MyAddonFrame, text = "我的插件", w = 80, h = 14, l = 160, b = 680, scale = 768 / 1080,
				points = { { "TOP", MyAddonFrame, "TOP", 0, -6 } } })
			local close = NewFrame({ name = "MyAddonFrameClose", otype = "Button", parent = MyAddonFrame, shown = false, w = 20, h = 20 })
			MyAddonFrame.kids, MyAddonFrame.regions = { close }, { title }
		""")
        res = self.run_chunk(P.inspect("MyAddonFrame", depth=1))
        self.assertEqual(res["screen"], [1920, 1080])
        f = res["frames"][0]
        self.assertEqual((f["name"], f["type"], f["shown"], f["visible"]), ("MyAddonFrame", "Frame", True, True))
        self.assertEqual(f["rect"], [100, 380, 200, 100])             # client pixels from the top-left
        self.assertEqual(f["points"], [["CENTER", "UIParent", "CENTER", 0, 40]])
        self.assertEqual((f["strata"], f["level"], f["parent"]), ("MEDIUM", 5, "UIParent"))
        self.assertEqual(sorted(f["scripts"]), ["OnEvent", "OnShow"])
        self.assertEqual((f["children_count"], f["regions_count"]), (1, 1))
        close, title = f["children"][0], f["regions"][0]
        self.assertEqual((close["type"], close["shown"], close["visible"]), ("Button", False, False))
        self.assertIsNone(close.get("rect"))                          # never anchored: no rectangle
        self.assertEqual((title["type"], title["text"], title["layer"]), ("FontString", "我的插件", "ARTWORK"))

    def test_inspect_the_mouse_and_errors(self):
        self.lua.execute(r"""
			UIParent = NewFrame({ name = "UIParent" })
			local panel = NewFrame({ name = "BigPanel", parent = UIParent })
			local button = NewFrame({ otype = "Button", parent = panel, l = 10, b = 10, w = 50, h = 20 })
			local locked = NewFrame({ name = "Locked", forbidden = true })
			function GetMouseFoci() return { button, locked } end
		""")
        res = self.run_chunk(P.inspect(mouse=True))
        button, locked = res["frames"]
        self.assertEqual((button["type"], button["name"], button["parents"]), ("Button", "BigPanel.anon", ["BigPanel", "UIParent"]))
        self.assertEqual(locked["name"], "<forbidden>")
        with self.assertRaises(ValueError) as cm:
            self.run_chunk(P.inspect("NoSuchThing.window"))
        self.assertIn("target:", str(cm.exception))
        with self.assertRaises(ValueError):
            self.run_chunk(P.inspect("NoSuchFrame"))                 # nil
        with self.assertRaises(ValueError):
            P.inspect(None)

    def test_lua_strings_survive_any_text(self):
        for text in ("plain", "a]]b", "x]=]y]==]", "ends with ]", "]", "\nleading newline", "引号 \" 和 ' 都行"):
            self.assertEqual(self.lua.execute("return " + P.lua_str(text)), text)

    def test_a_slash_command_runs_its_handler(self):
        """try's slash: the handler of SLASH_<KEY><n>, case-insensitive, with the rest of the line and the edit box"""
        self.lua.execute('SlashCmdList = { FOO = function(msg, box) return "got [" .. msg .. "]", box end } '
                         'SLASH_FOO1, SLASH_FOO2 = "/foo", "/Fo" DEFAULT_CHAT_FRAME = { editBox = "box" }')
        self.assertEqual(self.lua.execute(P.slash_call("/foo  show all  ")), ("got [show all]", "box"))
        self.assertEqual(self.lua.execute(P.slash_call("/FO")), ("got []", "box"))
        with self.assertRaises(lupa.LuaError) as cm:
            self.lua.execute(P.slash_call("/nothere x"))
        self.assertIn("no slash command /nothere", str(cm.exception))
        with self.assertRaises(ValueError):
            P.slash_call("foo")


if __name__ == "__main__":
    unittest.main()
