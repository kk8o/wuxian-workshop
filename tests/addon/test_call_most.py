"""`call` and how long its answer may be (0.10.0: the data call's "m") end to end: the real Agent.lua and Link.lua under
Lua 5.1 (tests/support/wowmock.py), the real companion and the Service's call_exposed. An answer comes back up to
`most` bytes (its JSON), 16 MB when the caller names none, which is also the most there is: its first piece with the
call's result, the rest poured. A longer one is refused in the game and none of it is sent. A call that fails in a game
whose WoWBridge is older than the program says so, by the version of its HELLO."""
import unittest

from tests.addon.test_pour import Pour, text_of
from wuxianworkshop import __version__, compat
from wuxianworkshop.agent import probes
from wuxianworkshop.daemon.api import ApiError

FOO = b"""
FooWB = WoWBridge.Bind("Foo")
FooWB:Expose("big", function() return BIG end)
FooWB:Expose("rep", function(a) return string.rep(a.s, a.n) end)
FooWB:Expose("boom", function() error("broken on purpose") end)
"""
MB16 = 16 * 1024 * 1024


class Most(Pour):
    def setUp(self):
        super().setUp()
        self.s.lua.execute(FOO)

    def call_coro(self, value, **kw):
        self.s.lua.globals()[b"BIG"] = value.encode()
        return self.svc.call_exposed("Foo", "big", **kw)

    def call(self, value, **kw):
        return self.drive(self.call_coro(value, **kw))

    def refused(self, coro, code="call_failed"):
        with self.assertRaises(ApiError) as cm:
            self.drive(coro)
        self.assertEqual(cm.exception.code, code)
        return cm.exception.message


class Poured(Most):
    def test_300_kb_and_2_mb_come_back(self):
        """no most named: 16 MB. The first piece with the call's result, the rest for one pour; nothing else in what
        comes back"""
        self.assertEqual(probes.WHOLE_MOST, MB16)
        for size, seed in ((300_000, 3), (2_000_000, 4)):
            value = text_of(size, seed=seed)
            before = len(self.calls("pour"))
            res = self.call(value)
            self.assertEqual(res, dict(addon="Foo", name="big", ok=True, result=value))
            self.assertEqual(self.calls("call")[-1], {"a": "Foo", "n": "big", "d": None, "m": MB16})
            self.assertEqual(len(self.calls("pour")), before + 1)

    def test_most_is_the_callers(self):
        """most: the bytes the answer (its JSON) may be. One byte more is refused in the game, which says both numbers,
        and none of it is sent; at most, it comes"""
        value = text_of(300_000)
        message = self.refused(self.call_coro(value, most=256 * 1024))
        self.assertRegex(message, r"^the answer is \d+ bytes, more than the 262144 bytes this call takes \(its most\)$")
        self.assertNotIn("older than", message)                       # this WoWBridge is the program's
        self.assertEqual(self.calls("call")[-1]["m"], 256 * 1024)
        self.assertEqual((self.calls("pour"), self.segments), ([], []))
        size = int(message.split()[3])                                # the bytes of its JSON
        self.assertGreater(size, 300_000)
        self.refused(self.call_coro(value, most=size - 1))
        self.assertEqual(self.segments, [])
        res = self.call(value, most=size)
        self.assertEqual(res["result"], value)
        rest = sum(len(d) for *_, d in self.segments)                 # all but the first piece (3,900 bytes, or up to
        self.assertTrue(size - probes.PIECE <= rest <= size - probes.PIECE + 3, (size, rest))   # 3 less)

    def test_past_16_mb_is_refused(self):
        message = self.refused(self.svc.call_exposed("Foo", "rep", {"s": "无限\t", "n": 17 * 1024 * 1024 // 7}))
        self.assertRegex(message, r"^the answer is \d+ bytes, more than the 16777216 bytes this call takes \(its most\)$")
        self.assertEqual(self.calls("pour"), [])

    def test_a_bad_most(self):
        for bad in (0, -5, MB16 + 1, "4096", 1.5, True, [1]):
            message = self.refused(self.svc.call_exposed("Foo", "big", most=bad), "bad_request")
            self.assertTrue(message.startswith("most: "), message)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.call("x", most=1 << 10)["result"], "x")
        self.assertEqual(self.call("y", most=MB16)["result"], "y")


class Record(Most):
    """what Agent.lua makes of a CALL record's "m", whoever wrote it"""

    def said(self, verb, payload):
        """the RUN result of a data call"""
        job = self.comp.data_call(verb, payload)
        t0 = self.s.now()
        while self.s.now() - t0 < 30:
            self.s.run(0.5)
            got = [text for _, text in self.results(job)]
            if got:
                return got[0]
        self.fail(f"no result for {verb}")

    def test_m_is_whole_bytes_from_1_to_16_mb_and_only_a_calls(self):
        self.s.lua.globals()[b"BIG"] = text_of(300_000).encode()
        call = {"a": "Foo", "n": "big", "d": None}
        for m in ("none", "262144", 0, -3, 0.5, None, True):          # no "m", or not a number of bytes: 256 KB
            got = self.said("call", call if m == "none" else dict(call, m=m))
            self.assertRegex(got, r"the answer is \d+ bytes, more than the 262144 bytes an answer may be", m)
        self.assertIn("more than the 4096 bytes this call takes (its most)", self.said("call", dict(call, m=4096.9)))
        self.assertIn(' ok =call (', self.said("call", dict(call, m=10 ** 12)))       # 16 MB at most: 300 KB fits
        self.assertRegex(self.said("call", dict(call, m=10 ** 12)), r'"1/\d\d+\n')
        self.s.lua.execute(b'for i = 1, 400 do FooWB:Expose(("name%03d"):format(i), print, ("x"):rep(700)) end')
        got = self.said("describe", {"a": "Foo", "m": 1 << 24})       # not a call: its "m" is not read
        self.assertRegex(got, r"the answer is \d+ bytes, more than the 262144 bytes an answer may be")
        self.assertEqual(self.segments, [])


class Behind(Most):
    version = "0.9.14"                    # the game still runs the WoWBridge from before the program was updated

    def test_a_failed_call_says_the_game_runs_an_older_wowbridge(self):
        """whatever failed (that WoWBridge may know less: before 0.10.0 it read no "m" and stopped at 256 KB), the
        error ends with the judge's step: the version its HELLO gave and that the new one needs a restart of the
        game; a call that works says nothing"""
        note = f". 无限工坊 {__version__} brings WoWBridge {__version__} but the game still runs 0.9.14: {compat.CLOSE}"
        self.assertEqual(self.svc.bridge_running(), "0.9.14")
        message = self.refused(self.call_coro(text_of(20_000), most=4096))
        self.assertTrue(message.startswith("the answer is ") and message.endswith(note), message)
        message = self.refused(self.svc.call_exposed("Foo", "boom"))
        self.assertIn("broken on purpose", message)
        self.assertTrue(message.endswith(note), message)
        value = text_of(50_000, seed=2)
        self.assertEqual(self.call(value), dict(addon="Foo", name="big", ok=True, result=value))


if __name__ == "__main__":
    unittest.main()
