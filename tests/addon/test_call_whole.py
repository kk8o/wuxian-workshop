"""`call` with whole (WoWBridge 0.9.15: the data call's "w") end to end: the real Agent.lua and Link.lua under Lua 5.1
(tests/support/wowmock.py), the real companion and the Service's call_exposed. With whole an answer up to 16 MB comes
back whole, poured (link features 4) or piece by piece (an addon that does not pour); past 16 MB it is refused; without
whole the 256 KB a call brings back stays; a piece asked for keeps the answer from then (a long answer may take longer
than the five minutes from its first piece)."""
import re
import unittest

from tests.addon.test_pour import Pour, text_of
from wuxianworkshop.daemon.api import ApiError

FOO = b"""
FooWB = WoWBridge.Bind("Foo")
FooWB:Expose("big", function() return BIG end)
FooWB:Expose("rep", function(a) return string.rep(a.s, a.n) end)
"""


class Whole(Pour):
    def setUp(self):
        super().setUp()
        self.s.lua.execute(FOO)

    def call(self, value, whole=True, **kw):
        self.s.lua.globals()[b"BIG"] = value.encode()
        return self.drive(self.svc.call_exposed("Foo", "big", whole=whole), **kw)

    def refused(self, coro):
        with self.assertRaises(ApiError) as cm:
            self.drive(coro)
        self.assertEqual(cm.exception.code, "call_failed")
        return cm.exception.message


class Poured(Whole):
    def test_300_kb_and_2_mb_come_whole(self):
        """past the 256 KB of a call: the first piece with the call's result, the rest for one pour"""
        for size, seed in ((300_000, 3), (2_000_000, 4)):
            value = text_of(size, seed=seed)
            before = len(self.calls("pour"))
            res = self.call(value)
            self.assertEqual((res["ok"], res["whole"]), (True, True))
            self.assertEqual(res["result"], value)
            self.assertEqual(self.calls("call")[-1], {"a": "Foo", "n": "big", "d": None, "w": True})
            self.assertEqual(len(self.calls("pour")), before + 1)
        self.assertEqual(self.calls("piece"), [])

    def test_without_whole_256_kb_at_most(self):
        """without whole a call's answer is 256 KB at most, as before; under it, it comes as before (no "w", no
        `whole` in what comes back)"""
        self.s.lua.globals()[b"BIG"] = text_of(300_000).encode()
        message = self.refused(self.svc.call_exposed("Foo", "big"))
        self.assertRegex(message, r"the answer is \d+ KB, more than the 256 KB a call brings back")
        self.assertNotIn("older than 0.9.15", message)
        value = text_of(200_000, seed=2)
        res = self.call(value, whole=False)
        self.assertEqual((res["result"], "whole" in res), (value, False))
        self.assertTrue(all("w" not in c for c in self.calls("call")))

    def test_past_16_mb_is_refused(self):
        message = self.refused(self.svc.call_exposed("Foo", "rep", {"s": "无限\t", "n": 17 * 1024 * 1024 // 7},
                                                     whole=True))
        self.assertRegex(message, r"the answer is \d+ KB, more than the 16384 KB a call brings back")
        self.assertEqual(self.calls("pour"), [])


class Older(Whole):
    features = 3                          # WoWBridge 0.9.9 - 0.9.12 links: no pours, the rest piece by piece

    def test_300_kb_piece_by_piece(self):
        value = text_of(300_000, seed=6)
        res = self.call(value)
        self.assertEqual((res["result"], res["whole"]), (value, True))
        self.assertEqual(self.calls("pour"), [])
        pieces = self.calls("piece")
        self.assertGreater(len(pieces), 70)
        self.assertEqual(sorted(p["i"] for p in pieces), list(range(2, len(pieces) + 2)))

    def test_a_piece_asked_for_keeps_the_answer(self):
        """the game keeps a long answer five minutes from the latest piece asked for, not from its first"""
        s, comp = self.s, self.comp

        def result(verb, payload):
            job = comp.data_call(verb, payload)
            t0 = s.now()
            while s.now() - t0 < 20:
                s.run(0.5)
                got = [text for _, text in self.results(job)]
                if got:
                    return job, got[0]
            self.fail(f"no result for {verb}")
        job, first = result("call", {"a": "Foo", "n": "rep", "d": {"s": "无限", "n": 3000}, "w": True})
        self.assertGreaterEqual(int(re.search(r'"1/(\d+)\n', first)[1]), 4)
        self.assertIn(" ok =piece ", result("piece", {"k": str(job), "i": 2})[1])
        s.run(250)
        self.assertIn(" ok =piece ", result("piece", {"k": str(job), "i": 3})[1])
        s.run(250)                                                   # 500 s after the first piece, 250 after the last
        result("call", {"a": "Foo", "n": "rep", "d": {"s": "又", "n": 3000}})       # its sweep: this one stays
        self.assertIn(" ok =piece ", result("piece", {"k": str(job), "i": 4})[1])


if __name__ == "__main__":
    unittest.main()
