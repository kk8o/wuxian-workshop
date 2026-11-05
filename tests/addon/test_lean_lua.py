"""The addons doing the same work with fewer client calls, in the mock client (tests/support/wowmock.py): the frame is
drawn again only where it changed (Frame.Show) from arrays reused per size (Frame.Build, Codec.Stream / Pack), the
mailbox looks at one glyph before it reads a slot's head (Font.Peek), and an error seen before takes no stack (both error
hooks) while the minimap button's count is drawn at most twice a second. Each checks the result is what it was, and
counts the calls."""
import re
import unittest

from tests.support.wowmock import ADDON, Session, lupa
from wuxianworkshop.core import frame as F
from wuxianworkshop.transport import link

# every call of these client methods counted (__calls), and those on the frame's bus apart (__bus)
COUNT = rb"""
local R = getmetatable(UIParent)
__calls, __bus = {}, {}
for _, name in ipairs({ "SetColorTexture", "Show", "Hide", "SetText", "GetStringWidth", "SetFont", "SetSize", "SetPoint",
	"ClearAllPoints", "SetScale" }) do
	local f = R[name]
	R[name] = function(self, ...)
		__calls[name] = (__calls[name] or 0) + 1
		if self == WoWBridgeBus then __bus[name] = (__bus[name] or 0) + 1 end
		return f(self, ...)
	end
end
function __reset()
	for k in pairs(__calls) do __calls[k] = 0 end
	for k in pairs(__bus) do __bus[k] = 0 end
end
"""


def counted(s, which=b"__calls"):
    return {k.decode(): int(v) for k, v in s.lua.globals()[which].items() if v}


def reset(s):
    s.lua.globals()[b"__reset"]()


def reference(W, H, mode, toggle, ftype, sess, msg, payload, part=0, parts=1):
    """frame.py's frame as a list of (r, g, b) 0..255 by cell index (y * W + x)"""
    cells = F.build(W, H, mode, toggle, ftype, sess, msg, payload, part, parts)
    return [cells[(k % W, k // W)] for k in range(W * H)]


def payload(n, seed=37):
    return bytes((i * seed + 11) % 256 for i in range(n))


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class FrameDraw(unittest.TestCase):
    def setUp(self):
        self.s = Session(self)
        self.s.lua.execute(COUNT)
        self.Frame = self.s.ns[b"Frame"]

    def tearDown(self):
        self.s.close()

    def build(self, W, H, mode, toggle, ftype, msg, data, part=0, parts=1):
        res = self.Frame[b"Build"](W, H, mode, toggle, ftype, 77, msg, data, part, parts)   # decoded by the mock too
        self.assertIsNotNone(res[0], res)
        return res

    def screen(self):
        """the pool's textures (the bus's regions after its backdrop): (shown, (r, g, b) 0..255) each"""
        regs = self.s.lua.globals()[b"WoWBridgeBus"][b"__regions"]
        out = []
        for i in range(2, len(regs) + 1):
            t = regs[i]
            rgb = t[b"rgb"]
            out.append((bool(t[b"shown"]), tuple(round(rgb[j] * 255) for j in (1, 2, 3)) if rgb else None))
        return out

    def show(self, W, H, mode, toggle, ftype, msg, data, part=0, parts=1, cell=4):
        """build and show a frame; the pool must show exactly its cells, in frame.py's colours"""
        R, G, B = self.build(W, H, mode, toggle, ftype, msg, data, part, parts)
        reset(self.s)
        self.Frame[b"Show"](W, H, cell, R, G, B)
        got = self.screen()
        want = reference(W, H, mode, toggle, ftype, 77, msg, data, part, parts)
        self.assertEqual([c for shown, c in got[:W * H]], want)
        self.assertTrue(all(shown for shown, _ in got[:W * H]))
        self.assertFalse(any(shown for shown, _ in got[W * H:]))
        return counted(self.s), counted(self.s, b"__bus")

    def test_build_reuses_its_arrays_and_matches_frame_py(self):
        """one set of arrays per size, written over: a shorter message after a longer one, the other order (mode 1 after
        mode 3), a probe frame's ramps and then data, modes 0 and 2: every cell as frame.py has it; little garbage"""
        cap = F.capacity(128, 32, 3)
        seq = [(128, 32, 3, F.TYPE_DATA, payload(cap)), (128, 32, 3, F.TYPE_DATA, payload(20, 91)),
               (128, 32, 1, F.TYPE_DATA, payload(300)), (128, 32, 3, F.TYPE_DATA, payload(40)),
               (64, 16, 1, F.TYPE_PROBE, b"v=0.9.11"), (64, 16, 1, F.TYPE_DATA, payload(10)),
               (64, 16, 3, F.TYPE_PROBE, payload(5)), (64, 16, 3, F.TYPE_CONTROL, payload(30)),
               (32, 12, 0, F.TYPE_DATA, payload(8)), (32, 12, 2, F.TYPE_DATA, payload(F.capacity(32, 12, 2))),
               (32, 12, 2, F.TYPE_DATA, b""), (32, 12, 3, F.TYPE_CONTROL, payload(40))]
        first = {}
        for i, (W, H, mode, ftype, data) in enumerate(seq):
            R, G, B = self.build(W, H, mode, i % 2, ftype, i + 1, data)
            got = [(round(R[k + 1] * 255), round(G[k + 1] * 255), round(B[k + 1] * 255)) for k in range(W * H)]
            self.assertEqual(got, reference(W, H, mode, i % 2, ftype, 77, i + 1, data), (W, H, mode, ftype, len(data)))
            same = self.s.lua.eval(b"function(a, b) return rawequal(a, b) end")
            self.assertTrue(same(first.setdefault((W, H), R), R))            # the same table for the size
        garbage = self.s.lua.eval(rb"""function(Build, p)
			Build(56, 16, 3, 0, 0, 1, 1, p)
			collectgarbage("collect")
			collectgarbage("stop")
			local k0 = collectgarbage("count")
			for i = 1, 20 do Build(56, 16, 3, i % 2, 0, 1, i, p) end
			local kb = (collectgarbage("count") - k0) / 20
			collectgarbage("restart")
			return kb
		end""")
        kb = garbage(self.untraced(), payload(200))
        self.assertLess(kb, 4, "KB of garbage a 56x16 frame (a new set of arrays was about 70)")

    def untraced(self):
        """Frame.Build as the addon has it (the mock wraps it to decode every frame), in a namespace of its own"""
        ns = self.s.lua.table()
        load = self.s.lua.globals()[b"__load"]
        for f in ("Codec.lua", "Frame.lua"):
            fn, err = load((ADDON / "WoWBridge" / f).read_bytes(), f"@WoWBridge/{f}".encode())
            self.assertIsNone(err)
            fn(b"WoWBridge", ns)
        return ns[b"Frame"][b"Build"]

    def test_show_draws_only_what_changed(self):
        """the same frame again: no texture call; a smaller one: only the cells it no longer has are hidden; the next
        heartbeat: only its changed cells coloured, the bus neither sized nor laid out again"""
        cap = F.capacity(128, 32, 3)
        calls, bus = self.show(128, 32, 3, 0, F.TYPE_DATA, 1, payload(cap))
        self.assertEqual((calls["SetColorTexture"], calls["Show"]), (4096 + 1, 4096 + 1))     # and the backdrop, the bus
        calls, bus = self.show(128, 32, 3, 0, F.TYPE_DATA, 1, payload(cap))
        self.assertEqual(calls, {"Show": 1})                                  # the bus's Show only
        calls, bus = self.show(32, 12, 3, 1, F.TYPE_CONTROL, 2, payload(40))
        self.assertEqual(calls["Hide"], 4096 - 384)
        self.assertEqual((bus["SetSize"], bus["SetScale"]), (1, 1))
        before = self.screen()[:384]
        calls, bus = self.show(32, 12, 3, 0, F.TYPE_CONTROL, 3, payload(40, 91))
        changed = sum(1 for a, b in zip(before, self.screen()[:384]) if a != b)
        self.assertGreater(changed, 0)
        self.assertEqual(calls.get("SetColorTexture", 0), changed)
        self.assertEqual((calls.get("Hide", 0), calls["Show"]), (0, 1))
        self.assertEqual(bus, {"Show": 1})                                   # not sized, scaled nor placed again
        calls, _ = self.show(56, 16, 3, 1, F.TYPE_DATA, 4, payload(60))     # bigger again: the new cells shown
        self.assertEqual((calls.get("Hide", 0), calls["Show"]), (0, 896 - 384 + 1))

    def test_what_makes_it_draw_everything_again(self):
        """Relayout from outside (the size events), Frame.Hide, a snap setting, the drag handle: the next frame is drawn
        whole (every cell coloured and shown, the rest of the pool hidden); a new physical size lays the bus out again"""
        cap = F.capacity(128, 32, 3)
        self.show(128, 32, 3, 0, F.TYPE_DATA, 1, payload(cap))
        self.show(32, 12, 3, 1, F.TYPE_CONTROL, 2, payload(40))
        g = self.s.lua.globals()

        def whole(calls):
            self.assertEqual((calls["SetColorTexture"], calls["Show"], calls["Hide"]), (384, 384 + 1, 4096 - 384))

        self.Frame[b"Relayout"]()
        whole(self.show(32, 12, 3, 0, F.TYPE_CONTROL, 3, payload(40))[0])
        self.Frame[b"Hide"]()
        whole(self.show(32, 12, 3, 1, F.TYPE_CONTROL, 4, payload(40))[0])
        self.Frame[b"cfg"][b"snap"] = b"on"
        whole(self.show(32, 12, 3, 0, F.TYPE_CONTROL, 5, payload(40))[0])
        self.Frame[b"SetUnlocked"](True, None, b"drag")
        whole(self.show(32, 12, 3, 1, F.TYPE_CONTROL, 6, payload(40))[0])
        self.Frame[b"SetUnlocked"](False)
        whole(self.show(32, 12, 3, 0, F.TYPE_CONTROL, 7, payload(40))[0])
        calls, bus = self.show(32, 12, 3, 1, F.TYPE_CONTROL, 8, payload(40))
        self.assertNotIn("Hide", calls)
        g[b"__resize"](1599, 901)                                           # no event yet: the frame itself sees it
        calls, bus = self.show(32, 12, 3, 0, F.TYPE_CONTROL, 9, payload(40))
        self.assertEqual((bus["SetSize"], bus["SetScale"]), (1, 1))
        self.assertAlmostEqual(g[b"WoWBridgeBus"][b"scale"], 768 / 901)
        self.Frame[b"cfg"][b"x"] = 40                                       # a new place (the panel's reset, a drag)
        calls, bus = self.show(32, 12, 3, 1, F.TYPE_CONTROL, 10, payload(40))
        self.assertEqual(bus["SetPoint"], 1)
        self.assertEqual(tuple(g[b"WoWBridgeBus"][b"point"][i] for i in (4, 5)), (40, 0))

    def test_idle_link_hides_nothing(self):
        """online and idle after a long message: the heartbeats hide no texture and colour only a few cells a second
        (were 2000 hides and 200 colours a second); every frame still decodes (the mock decodes each Build)"""
        s = self.s
        comp = link.Companion(s.addons, clock=s.now, log=lambda *_: None)
        s.comp = comp
        comp.new_process("P1-100")
        s.login()
        s.run(8)
        s.slash("long 4000")
        s.run(20)
        self.assertEqual(comp.stats["long_ok"], 1)
        reset(s)
        frames = len(s.frames)
        s.run(30)
        calls = counted(s)
        self.assertGreater(len(s.frames) - frames, 10)
        self.assertEqual(calls.get("Hide", 0), 0)
        self.assertLess(calls.get("SetColorTexture", 0) / 30, 40)


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class MailboxPeek(unittest.TestCase):
    OLD = b"local head = Font.Head(meter, 12)"
    NEW = b"local head = Font.Peek(meter, 11) == low and Font.Head(meter, 12)"

    def session(self, old):
        """a link session; old: the mailbox as it was (every meter's whole head read each tick)"""
        def patch(src):
            self.assertIn(self.NEW, src)
            return src.replace(self.NEW, self.OLD)
        s = Session(self, patch={"Mailbox.lua": patch} if old else None)
        s.lua.execute(COUNT + b"math.randomseed(7)")                        # the same session id in both
        s.lua.globals()[b"NS"] = s.ns
        s.lua.execute(rb"""
			local M = NS.Mailbox
			local start = M.Start
			__got = {}
			M.Start = function(db, onPacket)
				return start(db, function(pkt)
					local parts = { ("%.2f %d"):format(GetTime(), pkt.slot) }
					for _, r in ipairs(pkt.records) do parts[#parts + 1] = r.kind .. "=" .. r.data end
					__got[#__got + 1] = table.concat(parts, "|")
					return onPacket(pkt)
				end)
			end
		""")
        return s

    def scenario(self, s):
        comp = link.Companion(s.addons, clock=s.now, log=lambda *_: None, ping_every=4, hb_every=5, hb_lazy=5)
        s.comp = comp
        comp.new_process("P1-100")
        comp.say("你好 WoWBridge")
        s.login()
        s.run(8)
        s.slash("burst 10")
        s.run(10)
        for i in range(5):
            comp.say(f"第 {i} 句")
            s.run(0.7)
        g = s.lua.globals()
        g[b"__resize"](1599, 901)                                           # a new scale: the calibration again
        g[b"__fire"](b"DISPLAY_SIZE_CHANGED")
        s.slash("stream 6")
        s.run(12)
        comp.say("after the resize")
        s.run(20)
        busy = counted(s)
        reset(s)
        s.run(20)                                                           # idle: heartbeats only
        got = g[b"__got"]
        return [got[i] for i in range(1, len(got) + 1)], busy, counted(s)

    def test_the_same_packets_at_the_same_times(self):
        """the mailbox reads the same packets, byte for byte, at the same moments as when it read every meter's whole
        head four times a second; with about a tenth of the measuring"""
        old, new = self.session(True), self.session(False)
        try:
            got_old, busy_old, idle_old = self.scenario(old)
            got_new, busy_new, idle_new = self.scenario(new)
            self.assertGreater(len(got_old), 20)
            self.assertTrue(any(b"after the resize" in p for p in got_old))
            self.assertEqual(got_new, got_old)
            self.assertLess(busy_new["GetStringWidth"] * 3, busy_old["GetStringWidth"])
            self.assertLess(idle_new["GetStringWidth"] * 4, idle_old["GetStringWidth"])
            self.assertEqual((busy_new["SetFont"], idle_new["SetFont"]), (busy_old["SetFont"], idle_old["SetFont"]))
        finally:
            old.close()
            new.close()

    def test_peek_reads_what_head_reads(self):
        """Font.Peek(fs, 11) is the head's byte 12 whichever font the FontString shows: the stock one, a slot that is
        loading (the last font still in), a loaded slot, after a new scale; with a kept calibration it measures once"""
        s = self.session(False)
        try:
            comp = link.Companion(s.addons, clock=s.now, log=lambda *_: None)
            s.comp = comp
            comp.new_process("P1-100")
            s.login()
            s.run(8)
            for i in range(8):
                comp.say(f"say {i}")
                s.run(1)
            s.comp_running = False
            slots = list(range(1, int(s.ns[b"Mailbox"][b"Next"]())))         # read: in the client's font cache
            self.assertGreater(len(slots), 5)
            cmp = s.lua.eval(rb"""function(Font, m)
				local p = Font.Peek(m, 11)
				local h = Font.Head(m, 12)
				return p, h and h[12]
			end""")
            Font, g = s.ns[b"Font"], s.lua.globals()
            m = Font[b"Meter"]()
            self.assertEqual(cmp(Font, m), (None, None))                    # the stock font
            seen = []
            for k in slots + [4000]:                                        # the last: no font there
                Font[b"Load"](m, f"Interface\\AddOns\\WoWBridge\\mail\\{k:05d}.ttf".encode())
                p, h = cmp(Font, m)
                self.assertEqual(p, h)
                s.run(1)
                p, h = cmp(Font, m)
                self.assertEqual(p, h)
                seen.append(p)
                if k == slots[len(slots) // 2]:
                    g[b"__resize"](1366, 767)
                    p, h = cmp(Font, m)                                     # the old scale's calibration still kept
                    Font[b"Rescale"]()
                    p, h = cmp(Font, m)
                    self.assertEqual(p, h)
            self.assertEqual(seen[:-1], [k % 256 for k in slots])
            self.assertEqual(seen[-1], slots[-1] % 256)                     # the empty slot: the last font stays
            reset(s)
            self.assertEqual(Font[b"Peek"](m, 11), slots[-1] % 256)
            self.assertEqual(counted(s)["GetStringWidth"], 1)
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class ErrorRepeats(unittest.TestCase):
    STACK = rb"""
		__stacks = {}
		local orig = debugstack
		function debugstack(...)
			local src = debug.getinfo(2, "S").source
			__stacks[src] = (__stacks[src] or 0) + 1
			return orig(...)
		end
	"""

    def test_a_repeated_error_takes_its_stack_once(self):
        """the same error 40 times: one stack in each hook (were 40), the count in both; the player's entry, the debug
        window's [详情] and what goes to the app keep the first stack; a new error takes its own"""
        got = []
        s = Session(self, early=True)
        comp = link.Companion(s.addons, clock=s.now, log=lambda *_: None, on_debug=lambda kind, text: got.append((kind, text)))
        s.comp = comp
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(8)
            g = s.lua.globals()
            s.lua.execute(self.STACK)
            g[b"__stack"] = b"[string \"@Interface/AddOns/Foo/Foo.lua\"]:12: in function `FirstStack'\n"
            msg = b"Interface/AddOns/Foo/Foo.lua:12: attempt to call a nil value"
            for i in range(40):
                g[b"__error"](msg)
                if i == 0:
                    g[b"__stack"] = b"[string \"@Interface/AddOns/Foo/Foo.lua\"]:12: in function `LaterStack'\n"
                s.run(0.2)                                                  # 5 a second: under the rate limit
            stacks = {k.decode(): int(v) for k, v in g[b"__stacks"].items()}
            self.assertEqual(stacks, {"@!WuxianWorkshop/Core.lua": 1, "@WoWBridge/Debug.lua": 1})
            entry = next(iter(g[b"WuxianWorkshopDB"][b"errors"].values()))
            self.assertEqual(entry[b"count"], 40)
            self.assertIn(b"FirstStack", entry[b"stack"])
            self.assertEqual(s.ns[b"Debug"][b"stats"][b"errors"], 40)
            line = [ln for ln in self.console(s) if "attempt to call" in ln]
            self.assertEqual(len(line), 1)
            self.assertIn("×40", line[0])
            s.run(12)
            errs = [t for k, t in got if k == "ERR"]
            self.assertIn("FirstStack", errs[0])
            self.assertTrue(any(t.endswith("more times)") for t in errs[1:]), errs)
            detail = self.detail(s, "attempt to call")
            self.assertIn("FirstStack", detail)
            self.assertNotIn("LaterStack", detail)
            g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:30: something else")
            stacks = {k.decode(): int(v) for k, v in g[b"__stacks"].items()}
            self.assertEqual(stacks, {"@!WuxianWorkshop/Core.lua": 2, "@WoWBridge/Debug.lua": 2})
        finally:
            s.close()

    def test_a_flood_takes_no_stack_while_paused(self):
        """15 new errors at once: the player's collection takes the stacks of the 10 it keeps, none for the 5 over its
        rate limit; the debug window takes each new one's"""
        s = Session(self, early=True)
        try:
            s.login()
            s.run(1)
            g = s.lua.globals()
            s.lua.execute(self.STACK)
            for i in range(15):
                g[b"__error"](f"Interface/AddOns/Foo/Foo.lua:1: flood {'x' * i}".encode())
            stacks = {k.decode(): int(v) for k, v in g[b"__stacks"].items()}
            self.assertEqual(stacks, {"@!WuxianWorkshop/Core.lua": 10, "@WoWBridge/Debug.lua": 15})
            self.assertEqual(g[b"WuxianWorkshopDB"][b"dropped"], 5)
        finally:
            s.close()

    def test_the_minimap_count_at_most_twice_a_second(self):
        """30 errors in a tenth of a second: the button's count is drawn at once and once more half a second later,
        with all 30"""
        s = Session(self)
        try:
            s.login()
            s.run(1)
            g, mm = s.lua.globals(), s.ns[b"MinimapButton"]
            draws = []
            badge = mm[b"Badge"]
            mm[b"Badge"] = lambda n: (draws.append(int(n)), badge(n))[1]
            for i in range(30):
                g[b"__error"](f"Interface/AddOns/Foo/Foo.lua:1: boom {i % 3}".encode())
                if i % 10 == 9:
                    s.run(0.05)
            self.assertEqual(draws, [1])
            s.run(0.6)
            self.assertEqual(draws, [1, 30])
            button = g[b"WoWBridgeMinimapButton"]
            self.assertEqual(button[b"count"][b"text"], b"30")
            s.run(2)
            g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:1: later")       # quiet for a while: at once again
            self.assertEqual(draws, [1, 30, 31])
        finally:
            s.close()

    @staticmethod
    def console(s):
        t = s.ns[b"Console"][b"Lines"]()
        return [t[i].decode("utf-8") for i in range(1, len(t) + 1)]

    def detail(self, s, needle):
        """the text the debug window's [详情] of the line with `needle` puts in its box"""
        s.slash("log")
        win = s.lua.globals()[b"WoWBridgeConsole"]

        def walk(f):
            yield f
            for kid in (f[b"__kids"] or {}).values():
                yield from walk(kid)
        log = next(f for f in walk(win) if f[b"scripts"][b"OnHyperlinkClick"])
        msgs = [m.decode("utf-8") for m in log[b"messages"].values()]
        line = next(m for m in msgs if needle in m)
        ident = re.search(r"\|H(wbconsole:\d+)\|h", line).group(1)
        log[b"scripts"][b"OnHyperlinkClick"](log, ident.encode(), b"", b"LeftButton")
        texts = [f[b"text"].decode("utf-8") for f in walk(win) if f[b"text"] and b"\n\n" in f[b"text"]]
        self.assertTrue(texts)
        return texts[-1]


if __name__ == "__main__":
    unittest.main()
