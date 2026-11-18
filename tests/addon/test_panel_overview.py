"""The panel's 概览 as a player reads it (WoWBridge 0.10.0): the real Panel.lua, API.lua, Console.lua and Link.lua under
Lua 5.1 (tests/support/wowmock.py) with the real companion (transport/link.py). Its first line is the conclusion:
connected (auto), always on by the settings, always on and kept by the apps the WELCOME names (by=, byn=), or not
connected; 正在使用 lists what addons pushed with handle:Overview (checked at the caller, shown as plain text, in the
order the addons bound, a click through pcall); 本次会话 is one quiet line until something is wrong; at the foot the
meter of the app messages used, in three tiers. 诊断 has the counts and the latest lines, the app's housekeeping calls
as one line, and its tab carries the errors' count."""
import unittest

from tests.addon.test_pour import Pour
from tests.support.wowmock import BRIDGE, Session, lupa
from wuxianworkshop.transport import link

SKY, GOLD, BAD, OK, MUTED = "7fb3e0", "d8a85a", "f28b82", "6fcf97", "9c9ea6"


def rgb(hexed):
    return tuple(int(hexed[i:i + 2], 16) / 255 for i in (0, 2, 4))


def colour(region, key=b"textColor"):
    return tuple(region[key].values())[:3]


class Panel:
    """the panel of a session, open, as a test reads it"""

    def __init__(self, s):
        self.s, self.g = s, s.lua.globals()
        if not (self.g[b"WoWBridgePanel"] and self.g[b"WoWBridgePanel"][b"shown"]):
            s.slash("")
        self.p = self.g[b"WoWBridgePanel"]

    def refresh(self):
        self.s.ns[b"Panel"][b"Refresh"]()

    def text(self, name):
        return self.p[name.encode()][b"text"].decode()

    def shown(self, name):
        return bool(self.p[name.encode()][b"shown"])

    def card(self):
        return self.p[b"card"][b"state"][b"text"].decode()

    def rows(self):
        """the lines of 正在使用 in view: (text, note, the dot's colour as hex)"""
        if not self.p[b"using"][b"parent"][b"shown"]:
            return []
        out = []
        for row in self.p[b"rows"].values():
            if row[b"shown"]:
                r, g, b = colour(row[b"dot"], b"vertex")
                out.append((row[b"text"][b"text"].decode(), (row[b"note"][b"text"] or b"").decode(),
                            "%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))))
        return out

    def recent(self):
        """诊断's latest lines in view: (tag, text)"""
        return [(r[b"tag"][b"text"].decode(), r[b"text"][b"text"].decode()) for r in self.p[b"recent"].values()
                if r[b"shown"]]


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Conclusion(unittest.TestCase):
    """the card's one line, with an app that stands by"""

    def start(self, always=False, by=(), locale="zhCN"):
        s = Session(self, db=b'{ appNew = true, link = "standby" }', locale=locale)
        self.addCleanup(s.close)
        comp = link.Companion(s.addons, clock=s.now, log=lambda text: None, ping_every=4, hb_every=5, hb_lazy=5)
        comp.standby = True
        comp.settings(always, 300.0, by)
        s.comp = comp
        comp.new_process("P1-100")
        s.login()
        s.run(8)
        return s, comp

    def test_connected_and_the_app_gone(self):
        s, comp = self.start()
        self.assertEqual(s.ns[b"Link"][b"State"](), b"standby")       # no frame, and the app is there
        panel = Panel(s)
        card = panel.p[b"card"]
        self.assertEqual(panel.card(), "已连接 · 自动")
        self.assertEqual(colour(card[b"dot"], b"vertex"), rgb(OK))
        self.assertEqual((card[b"what"], card[b"act"][b"shown"], card[b"hint"][b"shown"], card[b"h"]),
                         (b"auto", False, False, 42))                 # one line: nothing for the player to do
        comp.code(b"return 1", "=run", "-")                           # a call opens a session: still that line
        s.run(3)
        self.assertEqual((s.ns[b"Link"][b"State"](), panel.card()), (b"online", "已连接 · 自动"))
        self.assertIsNone(s.ns[b"Link"][b"Kept"]()[1][1])             # (no app holds it)
        comp.farewell()                                               # the app quits
        s.run(2)
        self.assertEqual(panel.card(), "未连接 · 请打开无限工坊 App")
        self.assertEqual(colour(card[b"dot"], b"vertex"), rgb(MUTED))
        self.assertEqual((card[b"what"], card[b"act"][b"shown"], card[b"h"]), (b"away", False, 42))
        comp.present()                                                # and is back
        s.run(2)
        self.assertEqual(panel.card(), "已连接 · 自动")
        s.slash("lang en")
        self.assertEqual(panel.card(), "Connected · auto")
        comp.farewell()
        s.run(2)
        self.assertEqual(panel.card(), "Not connected · open the Wuxian Workshop app")

    def test_always_on_by_the_settings(self):
        s, comp = self.start(always=True)
        self.assertEqual(s.ns[b"Link"][b"State"](), b"online")
        panel = Panel(s)
        self.assertEqual((panel.card(), panel.p[b"card"][b"what"]), ("常开", b"always"))
        self.assertEqual(panel.p[b"card"][b"act"][b"shown"], False)
        s.slash("lang en")
        self.assertEqual(panel.card(), "Always on")
        comp.settings(False, 300.0)                                   # auto again: the session's new WELCOME says so
        s.run(3)
        self.assertEqual(panel.card(), "Connected · auto")

    def test_kept_by_an_app(self):
        s, comp = self.start(always=True, by=("语音搭子",))
        panel = Panel(s)
        self.assertEqual((panel.card(), panel.p[b"card"][b"what"]), ("常开 · 由 语音搭子 保持", b"held"))
        self.assertEqual(colour(panel.p[b"card"][b"dot"], b"vertex"), rgb(OK))
        always, by, n = s.ns[b"Link"][b"Kept"]()
        self.assertEqual((always, [t.decode() for t in by.values()], n), (True, ["语音搭子"], 1))
        s.slash("lang en")
        self.assertEqual(panel.card(), "Always on · kept by 语音搭子")
        comp.settings(False, 300.0)                                   # the app stopped: nobody keeps it
        s.run(3)
        self.assertEqual(panel.card(), "Connected · auto")
        self.assertEqual(s.ns[b"Link"][b"Kept"]()[0], False)

    def test_kept_by_several(self):
        s, comp = self.start(always=True, by=("Voice", "Guild", "公会聊天"), locale="enUS")
        panel = Panel(s)
        always, by, n = s.ns[b"Link"][b"Kept"]()                      # the first two by name, and how many in all
        self.assertEqual(([t.decode() for t in by.values()], n), (["Voice", "Guild"], 3))
        self.assertEqual(panel.card(), "Always on · kept by Voice, 3 in all")   # (the mock's line is narrow: one name)
        panel.p[b"card"][b"state"][b"w"] = 2000                       # room for two
        panel.refresh()
        self.assertEqual(panel.card(), "Always on · kept by Voice, Guild, 3 in all")
        s.slash("lang zh")
        self.assertEqual(panel.card(), "常开 · 由 Voice、Guild 等 3 个 保持")
        comp.settings(True, 300.0, ("Voice", "Guild"))                # one let go: both named, no count
        s.run(3)
        self.assertEqual(panel.card(), "常开 · 由 Voice、Guild 保持")
        comp.settings(True, 300.0)                                    # the settings now: nobody to name
        s.run(3)
        self.assertEqual(panel.card(), "常开")

    def test_titles_are_data(self):
        """what the app's WELCOME carries of a title: one plain line, cut at a character; the addon shows a | as itself"""
        self.assertEqual(link.by_title("语音搭子"), "语音搭子")
        self.assertEqual(link.by_title(" a;idle=9,b|cffff0000c\nd\t "), "a idle=9 b cffff0000c d")
        self.assertEqual(link.by_title("无限工坊的语音搭子应用"), "无限工坊的语音搭")            # 24 bytes: eight of them
        self.assertEqual(link.by_title("abcdefghijklmnopqrstuvwxyz"), "abcdefghijklmnopqrstuvwx")
        self.assertEqual(link.by_title(";|,"), "")
        s, comp = self.start(always=True, by=("a;idle=9,b|c", ";", "last"))
        self.assertEqual(s.ns[b"Link"][b"P"][b"idle"], 0)             # (the title's idle=9 set nothing)
        always, by, n = s.ns[b"Link"][b"Kept"]()                      # the one with no text left is counted, not named
        self.assertEqual((always, [t.decode() for t in by.values()], n), (True, ["a idle=9 b c", "last"], 3))
        s.ns[b"Link"][b"Welcome"](b"v=0.10.0;s=1;idle=0;by=x|cffff0000y|r;byn=1")   # (an app that cleaned nothing)
        panel = Panel(s)
        self.assertEqual(panel.card(), "常开 · 由 x||cffff0000y||r 保持")
        s.ns[b"Link"][b"Welcome"](b"v=0.9.13;s=1;mode=3")             # an app that says no idle=: nothing known
        self.assertIsNone(s.ns[b"Link"][b"Kept"]())


FOO = b"""
A = WoWBridge.Bind("Alpha")
B = WoWBridge.Bind("Beta")
Clicked = {}
function Bad(rows)                                 -- the error of a push that must fail (where it points, and its text)
	local ok, err = pcall(function() local done = A:Overview(rows) return done end)   -- (no tail call: it has a line)
	assert(not ok, "it took a wrong shape")
	return err
end
"""


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Overview(unittest.TestCase):
    """handle:Overview(rows) and 正在使用"""

    def setUp(self):
        self.s = s = Session(self)
        self.addCleanup(s.close)
        s.comp = link.Companion(s.addons, clock=s.now, log=lambda text: None)
        s.comp.new_process("P1-100")
        s.login()
        s.run(8)
        s.lua.execute(FOO)

    def lua(self, code):
        return self.s.lua.execute(code.encode())

    def test_rows_show_in_the_order_the_addons_bound(self):
        panel = Panel(self.s)
        self.assertEqual(panel.rows(), [])                            # nobody pushed: no section
        self.assertFalse(panel.p[b"using"][b"parent"][b"shown"])
        self.assertTrue(self.s.lua.eval(b"type(A.Overview) == 'function'"))   # (an addon checks it is there)
        self.lua('B:Overview({ { text = "Beta one", note = "Agent", tone = "warn" } })')
        self.assertEqual(panel.rows(), [("Beta one", "Agent", GOLD)])   # the open panel shows it at once
        self.assertEqual(panel.p[b"using"][b"text"], b"In use")
        self.lua('A:Overview({ { text = "语音搭子", note = "本机端", tone = "ok" }, { text = "数据共创" }, '
                 '{ text = "broken", tone = "bad" } })')
        self.assertEqual(panel.rows(), [("语音搭子", "本机端", OK), ("数据共创", "", MUTED), ("broken", "", BAD),
                                        ("Beta one", "Agent", GOLD)])   # Alpha bound first: its lines first, as given
        self.lua('A:Overview({ { text = "only this" } })')              # pushed again: all of its lines replaced
        self.assertEqual(panel.rows(), [("only this", "", MUTED), ("Beta one", "Agent", GOLD)])
        self.lua("B:Overview({})")                                      # none: its lines go
        self.assertEqual(panel.rows(), [("only this", "", MUTED)])
        self.lua("A:Overview(nil)")
        self.assertEqual(panel.rows(), [])
        self.assertFalse(panel.p[b"using"][b"parent"][b"shown"])
        self.s.slash("lang zh")
        self.lua('WoWBridge.Bind("Late"):Overview({ { text = "x" } }) B:Overview({ { text = "y" } })')
        self.assertEqual([r[0] for r in panel.rows()], ["y", "x"])      # one that binds later comes later
        self.assertEqual(panel.p[b"using"][b"text"].decode(), "正在使用")

    def test_the_shape_is_checked_at_the_caller(self):
        panel = Panel(self.s)
        self.lua('A:Overview({ { text = "kept" } })')
        for rows, says in (
                ('"text"', "rows is an array of rows, or nil for none"),
                ("{ {text='1'}, {text='2'}, {text='3'}, {text='4'}, {text='5'}, {text='6'}, {text='7'} }",
                 "rows is an array of at most 6 rows"),
                ("{ a = { text = 'x' } }", "rows is an array of at most 6 rows"),
                ("{ 'x' }", "row 1 is a table"),
                ("{ { text = 'ok' }, { note = 'no text' } }", "row 2: text is a string of 1 to 60 bytes"),
                ("{ { text = '' } }", "row 1: text is a string of 1 to 60 bytes"),
                ("{ { text = '  ' } }", "row 1: text is a string of 1 to 60 bytes"),
                ("{ { text = 5 } }", "row 1: text is a string of 1 to 60 bytes"),
                ("{ { text = ('x'):rep(61) } }", "row 1: text is a string of 1 to 60 bytes"),
                ("{ { text = 'x', note = ('n'):rep(31) } }", "row 1: note is a string of at most 30 bytes, or nil"),
                ("{ { text = 'x', note = 3 } }", "row 1: note is a string of at most 30 bytes, or nil"),
                ("{ { text = 'x', tone = 'good' } }", 'row 1: tone is "ok", "warn", "bad" or nil'),
                ("{ { text = 'x', click = 'open' } }", "row 1: click is a function, or nil")):
            err = self.s.lua.eval(f"Bad({rows})".encode()).decode()
            self.assertIn("Overview(rows): " + says, err, rows)
            self.assertRegex(err, r'^\[string "<python>"\]:\d+: Overview', rows)   # the error is the caller's line
        self.assertEqual(panel.rows(), [("kept", "", MUTED)])           # a push that failed changed nothing
        self.lua("A:Overview({ { text = ('x'):rep(60), note = ('n'):rep(30) }, {text='2'}, {text='3'}, {text='4'}, "
                 "{text='5'}, {text='6'} })")                           # the most there is
        self.assertEqual(len(panel.rows()), 6)

    def test_words_are_plain_text(self):
        panel = Panel(self.s)
        self.lua(r'A:Overview({ { text = "|cffff0000red|r |Hitem:1|h[x]|h a\nb", note = "|TInterface\\Icons\\x:16|t" } })')
        self.assertEqual(panel.rows(), [("||cffff0000red||r ||Hitem:1||h[x]||h a b", "||TInterface\\Icons\\x:16||t", MUTED)])

    def test_a_click_runs_the_addons_function(self):
        panel = Panel(self.s)
        self.lua('A:Overview({ { text = "opens", click = function() Clicked[#Clicked + 1] = "a" end }, { text = "plain" }, '
                 '{ text = "breaks", click = function() error("click broke") end } })')
        rows = list(panel.p[b"rows"].values())
        rows[0].Click(rows[0])
        rows[1].Click(rows[1])                                          # no function: nothing
        self.assertEqual(list(self.s.lua.globals()[b"Clicked"].values()), [b"a"])
        errors = self.s.lua.globals()[b"__errors"]
        n = len(errors)
        rows[2].Click(rows[2])                                          # its error: to the handler, not into the click
        self.assertEqual(len(errors), n + 1)
        self.assertIn(b"click broke", errors[n + 1])
        self.assertTrue(panel.p[b"shown"])
        rows[0][b"scripts"][b"OnEnter"](rows[0])                        # one with a function lights up under the mouse
        rows[1][b"scripts"][b"OnEnter"](rows[1])
        self.assertNotEqual(colour(rows[0][b"text"]), colour(rows[1][b"text"]))

    def test_more_lines_than_there_is_room_for(self):
        panel = Panel(self.s)
        six = ", ".join("{ text = 'r%d' }" % i for i in range(1, 7))
        for name in ("A", "B", "C", "D"):
            self.lua(f'{name} = {name} or WoWBridge.Bind("{name}") {name}:Overview({{ {six} }})')
        rows = panel.rows()
        self.assertEqual(len(rows), 7)                                  # what fits above 本次会话 and the meter ...
        self.assertEqual((panel.shown("more"), panel.text("more")), (True, "17 more"))   # ... and how many more
        self.s.slash("lang zh")
        self.assertEqual(panel.text("more"), "另有 17 行")
        self.lua("C:Overview() D:Overview() B:Overview()")
        self.assertEqual((len(panel.rows()), panel.shown("more")), (6, False))

    def test_a_hot_load_of_wowbridge_keeps_the_rows(self):
        self.lua('A:Overview({ { text = "before", click = function() Clicked[#Clicked + 1] = "old" end } })')
        s = self.s
        s.load_addon("WoWBridge", ["API.lua", "Panel.lua"], ns=s.ns)    # the files run again, as `load WoWBridge` does
        panel = Panel(s)
        self.assertEqual([r[0] for r in panel.rows()], ["before"])
        self.lua('A:Overview({ { text = "after" } }) WoWBridge.Bind("Zed"):Overview({ { text = "new" } }) '
                 'B:Overview({ { text = "second" } })')                 # the handles from before still push, in their turn
        self.assertEqual([r[0] for r in panel.rows()], ["after", "second", "new"])


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class ThisSession(Pour):
    """本次会话 on 概览, and 诊断's counts and lines"""

    KIT = b"""
    Kit = WoWBridge.Bind("WuxianKit")
    Kit:Expose("kit.app.status", function(args) if args and args.app == "gone" then error("no such app") end return { on = true } end)
    Kit:Expose("kit.revision", function() return 7 end)
    Kit:Expose("kit.tune.get", function() return "x" end)
    """

    def call(self, name, args=None):
        try:
            return self.drive(self.svc.call_exposed("WuxianKit", name, args or {}))
        except Exception as e:                                          # a call that fails: the game has its line
            return e

    def test_quiet_then_only_what_is_wrong(self):
        s, g = self.s, self.s.lua.globals()
        panel = Panel(s)
        hidden = ("view", "runs", "lastError", "forwarded", "failed", "blocked")
        self.assertEqual(panel.text("errors"), "No errors")
        self.assertEqual([panel.shown(n) for n in hidden], [False] * 6)
        s.lua.execute(self.KIT)
        self.call("kit.tune.get")
        s.run(1.1)
        self.assertEqual((panel.text("errors"), panel.shown("runs"), panel.text("runs")), ("No errors", True, "Agent runs: 1"))
        g[b"__fire"](b"ADDON_ACTION_BLOCKED", b"Foo", b"CastSpellByName()")   # a blocked action: its own line
        s.run(1.1)
        self.assertEqual((panel.shown("blocked"), panel.text("blocked"), panel.shown("failed")),
                         (True, "Blocked actions: 1", False))
        self.assertEqual(colour(panel.p[b"blocked"]), rgb(GOLD))
        self.assertEqual(panel.text("errors"), "No errors")
        g[b"__error"](b'[string "=run"]:1: attempt to index global \'Nope\' (a nil value)')
        s.run(1.1)                                                       # an error of no addon's file: its text
        self.assertEqual(panel.text("errors"), "Lua errors: 1")
        self.assertRegex(panel.text("lastError"), r'^\d\d:\d\d · \[string "=run"\]:1: attempt to index global')
        g[b"__error"](b"...ace/AddOns/Foo/Sub\\Core.lua:3: bad ||cffff0000thing")
        s.run(1.1)
        self.assertEqual(panel.text("errors"), "Lua errors: 2")          # the newest one: whose file
        self.assertRegex(panel.text("lastError"), r"^\d\d:\d\d · Foo · Core\.lua$")
        self.assertEqual(panel.text("forwarded"), "Passed on to the agent")
        s.slash("set forwardDebug off")
        s.run(1.1)
        self.assertEqual(panel.text("forwarded"), "Forwarding is off")
        s.slash("lang zh")
        self.assertEqual((panel.text("errors"), panel.text("forwarded"), panel.text("blocked")),
                         ("2 个 Lua 报错", "转发已关闭", "被拦截 1 次"))
        tabs = list(panel.p[b"tabs"][b"tabs"].values())
        self.assertEqual(tabs[2][b"label"][b"text"].decode(), "诊断 |cfff28b822|r")
        s.ns[b"Debug"][b"stats"][b"errors"] = 250
        panel.refresh()
        self.assertEqual(tabs[2][b"label"][b"text"].decode(), "诊断 |cfff28b8299+|r")
        self.assertEqual(panel.text("errors"), "250 个 Lua 报错")

    def test_heartbeats_are_one_line_and_no_agent_runs(self):
        s = self.s
        s.lua.execute(self.KIT)
        panel = Panel(s)
        for _ in range(3):
            self.call("kit.app.status", {"app": "voice"})
        self.call("kit.tune.get")
        self.call("kit.revision")
        self.call("kit.app.status", {"app": "gone"})                    # one that failed is news: its own line
        s.run(1.1)
        stats, console = s.ns[b"Agent"][b"stats"], s.ns[b"Console"]
        self.assertEqual((stats[b"runs"], stats[b"failed"], console[b"beats"]), (6, 1, 4))
        self.assertEqual(panel.text("runs"), "Agent runs: 2")           # the agent's own: without the housekeeping
        self.assertEqual(panel.text("failed"), "Failed runs: 1")
        self.assertEqual(panel.text("counts"), "Runs |cffe8e4d82|r · Failed |cfff28b821|r · Errors |cff9c9ea60|r · "
                                               "Blocked |cff9c9ea60|r")
        lines = panel.recent()
        self.assertEqual(len(lines), 4)
        self.assertRegex(lines[0][1], r"^calling WuxianKit\.kit\.app\.status failed: .*no such app")
        self.assertEqual(lines[1], ("agent", "heartbeats ×4"))          # where the newest of them is, with their count
        self.assertRegex(lines[2][1], r"^called WuxianKit\.kit\.tune\.get · ")
        self.assertEqual(lines[3], ("link", "connected to the app"))
        self.assertFalse(panel.shown("range"))
        s.slash("lang zh")
        self.assertEqual(panel.recent()[1], ("运行", "心跳 ×4"))
        self.assertEqual(panel.text("runs"), "Agent 运行 2 次")
        all_lines = [t.decode() for t in console[b"Lines"]().values()]  # the debug window still has each of them
        self.assertEqual(sum("kit.app.status" in t for t in all_lines), 4)
        console[b"Clear"]()
        self.call("kit.revision")
        s.run(1.1)
        self.assertEqual(panel.recent(), [("运行", "心跳 ×1")])          # counted from when the lines were cleared
        self.assertEqual(panel.text("runs"), "Agent 运行 2 次")

    def test_the_wheel_goes_through_the_lines(self):
        s, g = self.s, self.s.lua.globals()
        panel = Panel(s)
        for i in range(1, 8):
            g[b"print"](f"line {i}".encode())
        s.run(1.1)
        self.assertEqual([t for _, t in panel.recent()], ["line 7", "line 6", "line 5", "line 4"])
        self.assertEqual((panel.shown("range"), panel.text("range")), (True, "1-4 / 8"))
        page = panel.p[b"pages"][b"diag"]
        wheel = page[b"scripts"][b"OnMouseWheel"]
        wheel(page, -1)                                                 # down: older lines
        wheel(page, -1)
        self.assertEqual([t for _, t in panel.recent()], ["line 5", "line 4", "line 3", "line 2"])
        self.assertEqual(panel.text("range"), "3-6 / 8")
        for _ in range(9):
            wheel(page, -1)                                             # no further than the oldest
        self.assertEqual([t for _, t in panel.recent()][-1], "connected to the app")
        self.assertEqual(panel.text("range"), "5-8 / 8")
        for _ in range(9):
            wheel(page, 1)
        self.assertEqual(panel.text("range"), "1-4 / 8")
        s.ns[b"Console"][b"Clear"]()
        panel.refresh()
        self.assertEqual((panel.recent(), panel.shown("recentNone"), panel.shown("range")), ([], True, False))


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Meter(unittest.TestCase):
    """the app messages used, at 概览's foot"""

    def setUp(self):
        self.s = s = Session(self)
        self.addCleanup(s.close)
        s.comp = link.Companion(s.addons, clock=s.now, log=lambda text: None)
        s.comp.new_process("P1-100")
        s.login()
        s.run(8)
        s.lua.execute(b"local ns = ... __left = nil local real = ns.Link.SlotsLeft "
                      b"ns.Link.SlotsLeft = function() return __left or real() end", s.ns)
        self.panel = Panel(s)

    def meter(self, left):
        self.s.lua.globals()[b"__left"] = left
        self.panel.refresh()
        m = self.panel.p[b"meter"]
        return (m[b"pct"][b"text"].decode(), m[b"note"][b"text"].decode(), m[b"tier"].decode(),
                bool(m[b"fill"][b"shown"]), round(m[b"fill"][b"w"], 2))

    def tip(self):
        m, g = self.panel.p[b"meter"], self.s.lua.globals()
        m[b"scripts"][b"OnEnter"](m)
        (line,) = g[b"GameTooltip"][b"lines"].values()
        m[b"scripts"][b"OnLeave"](m)
        return line.decode()

    def test_three_tiers(self):
        m = self.panel.p[b"meter"]
        self.assertEqual(m[b"label"][b"text"], b"App messages left")
        self.assertEqual((m[b"w"], m[b"track"][b"h"], m[b"fill"][b"h"]), (360, 4, 4))   # as wide as the page's content
        quiet = "resets when the game exits"
        self.assertEqual(self.meter(4096), ("0%", quiet, "sky", False, 1))      # nothing used: no bar
        self.assertEqual(self.meter(4095), ("1%", quiet, "sky", True, 1))       # a little: 1 at least, a pixel of bar
        self.assertEqual(self.meter(4096 - 696), ("17%", quiet, "sky", True, round(360 * 696 / 4096, 2)))
        self.assertEqual(tuple(m[b"fill"][b"rgb"].values()), rgb(SKY))
        self.assertEqual(colour(m[b"note"]), rgb(MUTED))
        self.assertEqual(self.meter(4096 - 3256)[0], "79%")                     # 79.49: still plenty
        self.assertEqual(self.meter(4096 - 3256)[2], "sky")
        low = "running low · resets when the game exits"
        self.assertEqual(self.meter(4096 - 3257), ("80%", low, "gold", True, round(360 * 3257 / 4096, 2)))   # 79.52
        self.assertEqual(tuple(m[b"fill"][b"rgb"].values()), rgb(GOLD))
        self.assertEqual((colour(m[b"note"]), colour(m[b"pct"])), (rgb(GOLD), rgb(GOLD)))
        self.assertEqual(self.meter(1), ("99%", low, "gold", True, round(360 * 4095 / 4096, 2)))   # never 100 with one left
        gone = "none left · back after the game exits"
        self.assertEqual(self.meter(0), ("100%", gone, "bad", True, 360))
        self.assertEqual(tuple(m[b"fill"][b"rgb"].values()), rgb(BAD))
        self.assertEqual((colour(m[b"note"]), colour(m[b"pct"])), (rgb(BAD), rgb(BAD)))
        self.s.slash("lang zh")
        self.assertEqual(m[b"label"][b"text"].decode(), "App 消息余量")
        self.assertEqual(self.meter(0)[1], "已用完 · 退出游戏后恢复")
        self.assertEqual(self.meter(500)[1], "余量不足 · 退出游戏后重置")
        self.assertEqual(self.meter(3400)[1], "退出游戏后重置")

    def test_the_tooltip_says_how_many_and_how_long(self):
        self.meter(4096 - 696)
        self.assertEqual(self.tip(), "696 / 4,096 used")                # too early to say how fast they go
        self.s.run(130)
        self.meter(4096 - 696)
        self.assertRegex(self.tip(), r"^696 / 4,096 used · about \d+ min left at this pace$")
        self.meter(4090)                                                # a slow pace: hours
        self.assertRegex(self.tip(), r"^6 / 4,096 used · about \d+ h left at this pace$")
        self.s.slash("lang zh")
        self.assertRegex(self.tip(), r"^已用 6 / 4,096 · 按当前速度约 \d+ 小时用完$")
        self.meter(0)                                                   # none left: nothing to estimate
        self.assertEqual(self.tip(), "已用 4,096 / 4,096")

    def test_none_left_keeps_the_dialog_and_adds_no_button(self):
        g = self.s.lua.globals()
        self.meter(0)
        self.s.run(1)                                                   # the link's own check: the chat and the dialog
        self.assertTrue(g[b"WoWBridgeSlotsDialog"][b"shown"])
        self.assertIn("no app messages left", self.s.chat())
        kids = self.panel.p[b"meter"][b"__kids"]                        # the meter is words and a bar: nothing to press
        self.assertIsNone(kids)
        self.assertEqual(self.panel.p[b"card"][b"hint"][b"shown"], False)
