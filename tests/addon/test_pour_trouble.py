"""Pours in trouble, end to end over the mock client as tests/addon/test_pour.py drives them: a segment lost whole is
asked for again alone; the link down in the middle (the game minimized) holds the pour back until it is up again; an
addon that does not pour (before 0.9.13) gets the rest piece by piece, eight at a time."""
import re
import unittest

from tests.addon.test_pour import Pour, text_of
from wuxianworkshop.agent import probes


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
    features = 3                          # WoWBridge 0.9.9 - 0.9.12: no pours

    def test_pieces_eight_at_a_time(self):
        """an addon that does not pour gets the rest asked for piece by piece: eight at a time, one more as one comes
        back (not a batch of eight after a batch of eight)"""
        self.assertFalse(self.comp.pours())
        value = text_of(60_000, seed=13)
        flight, most, when_ten = set(), [0], {}
        run = self.svc.run

        async def counted(code, timeout_ms=10000, addon=None, chunk="=run"):
            m = re.match(r'local KEY, I = "\w+", (\d+)\n', code)
            i = int(m[1]) if m else 1
            flight.add(i)
            most[0] = max(most[0], len(flight))
            if i == 10:
                when_ten["flight"] = set(flight)
            try:
                return await run(code, timeout_ms, addon, chunk)
            finally:
                flight.discard(i)
        self.svc.run = counted
        res = self.whole(value, step=0.05)
        self.assertEqual(res["values"], [value])
        self.assertEqual(most[0], probes.WINDOW)
        self.assertTrue(when_ten["flight"] & set(range(2, 10)), when_ten)   # it started while earlier ones were out
        self.assertEqual((self.segments, self.calls("pour")), ([], []))


if __name__ == "__main__":
    unittest.main()
