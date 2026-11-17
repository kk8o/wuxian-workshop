"""Pours in trouble, end to end over the mock client as tests/addon/test_pour.py drives them: a segment lost whole is
asked for again alone; the link down in the middle (the game minimized) holds the pour back until it is up again; an
addon that does not pour (before 0.9.13) cannot bring a long answer back, and the error says its version."""
import re
import unittest

from tests.addon.test_pour import Pour, text_of
from wuxianworkshop import __version__
from wuxianworkshop.daemon.api import ApiError


class Trouble(Pour):
    def test_a_segment_lost_whole_is_asked_for_again(self):
        """a segment that never reaches the pour (the link gave it up) is asked for again, alone, POUR_ASK seconds after
        the last new one came"""
        value = text_of(150_000, seed=9)
        self.s.lua.globals()[b"BIG"] = value.encode()

        def lose():
            if self.segments and not self.drop and not hasattr(self, "lost"):
                self.lost = self.segments[0][1]
                self.drop.add((self.lost, 3))
        res = self.drive(self.svc.run_whole("return BIG"), each=lose)
        self.assertEqual(res["values"], [value])
        self.assertEqual(self.calls("pour")[1:], [{"k": self.lost, "g": True, "only": [3]}])
        notes = [e["text"] for e in self.journal.since(0, 1000, ["INFO"])[0]]
        self.assertTrue(any(re.fullmatch(r"run: segments 3 of its answer asked for again \(2\d s without a new one\)", n)
                            for n in notes), notes)
        self.assertEqual([i for _, _, i, _, _ in self.segments].count(3), 2)

    def test_the_link_down_in_the_middle(self):
        """the game minimized for half a minute in the middle of a pour: no frames reach the companion; the addon stops
        queueing segments POUR_QUIET seconds after the last mailbox packet, the daemon waits for the link, and once it
        is back the pour goes on from where it was and comes whole"""
        value = text_of(170_000, seed=11)
        comp, frame = self.comp, self.comp.on_frame
        out = {}

        def covered(*a, **kw):
            if out.get("from", 1e18) <= self.s.now() < out.get("to", 0):
                return None
            return frame(*a, **kw)
        comp.on_frame = covered

        def each():
            if len(self.segments) >= 2 and not out:
                out["from"], out["to"] = self.s.now(), self.s.now() + 32
        res = self.whole(value, each=each)
        self.assertEqual(res["values"], [value])
        self.assertGreater(self.segments[-1][0], out["to"])            # the last ones came after the link was back
        quiet = [t for t in self.queued() if out["from"] + 24 < t < out["to"]]
        self.assertEqual(quiet, [])                                   # nothing queued while nobody read the screen
        self.assertTrue(any(out["from"] < t < out["from"] + 24 for t in self.queued()))


class Older(Pour):
    features, version = 3, "0.9.12"       # WoWBridge 0.9.9 - 0.9.12: no pours

    def test_a_long_answer_cannot_come_back(self):
        """an addon that does not pour brings back what fits in one piece and no more (the piece-by-piece way is gone):
        a longer answer is an error that says the version its HELLO gave, and that the new WoWBridge takes effect once
        the game is restarted; of a run, a call and a probe alike"""
        self.assertFalse(self.comp.pours())
        self.s.lua.execute(b'FooWB = WoWBridge.Bind("Foo") FooWB:Expose("big", function() return BIG end)')
        self.s.lua.globals()[b"BIG"] = text_of(60_000, seed=13).encode()
        for what, coro in (("run", self.svc.run_whole("return BIG")), ("call", self.svc.call_exposed("Foo", "big")),
                           ("probe", self.svc.probe("return BIG", 10000))):
            with self.assertRaises(ApiError) as cm:
                self.drive(coro)
            self.assertEqual((cm.exception.status, cm.exception.code), (409, "addon_outdated"), what)
            self.assertEqual(cm.exception.message,
                             f"{what}: its answer is longer than one piece, which the WoWBridge in the game cannot bring "
                             f"back (the WoWBridge in the game is 0.9.12, older than this 无限工坊 {__version__}: the new "
                             f"one takes effect once the game is restarted)")
        self.assertEqual((self.segments, self.calls("pour"), self.calls("piece")), ([], [], []))
        self.s.lua.globals()[b"BIG"] = b"short"
        self.assertEqual(self.drive(self.svc.run_whole("return BIG"))["values"], ["short"])
        self.assertEqual(self.drive(self.svc.call_exposed("Foo", "big"))["result"], "short")


if __name__ == "__main__":
    unittest.main()
