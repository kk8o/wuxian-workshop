"""The link of 0.9.9 end to end: WoWBridge's Lua under Lua 5.1 (tests/support/wowmock.py) against the real companion
(wuxianworkshop/transport/link.py). Jobs worth it go compressed to an addon that inflates (link features ZIP): code and
data calls run as sent, small jobs and older addons get them as they are, and a job the game could not unpack turns it off
for the session. Long messages go up at the shorter display time (show_lazy), and the session goes back to show_safe
for good once something it showed had to be asked for again, unless the link had only just come up."""
import json
import math
import unittest

from tests.addon.test_link_robust import lose
from tests.support.wowmock import Session, lupa
from wuxianworkshop.core import frame as F, mailbox
from wuxianworkshop.transport import link

ROWS = 400
BIG = ("local T = {}\n" + "\n".join(f'T[{i}] = "row {i} of a table that is here to make the code long"' for i in range(1, ROWS + 1))
       + "\nlocal n = 0\nfor _, v in ipairs(T) do n = n + #v end\nreturn n, #T\n").encode()
BIG_SUM = sum(len(f"row {i} of a table that is here to make the code long") for i in range(1, ROWS + 1))


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Link099(unittest.TestCase):
    def start(self, features=None, patch=None, **kw):
        s = Session(self, patch=patch)
        self.addCleanup(s.close)
        self.got, self.logs = [], []
        s.comp = link.Companion(s.addons, clock=s.now, log=self.logs.append,
                                on_debug=lambda kind, text: self.got.append((s.now(), kind, text)), **kw)
        s.comp.new_process("P1-100")
        if features is not None:                                  # an older WoWBridge says less in its HELLO
            s.ns[b"Link"][b"FEATURES"] = features
        s.login()
        s.run(8)
        return s

    def run_result(self, s, job, seconds=20):
        t0 = s.now()
        while s.now() - t0 < seconds:
            text = next((t for _, k, t in self.got if k == "RUN" and t.startswith(f"{job} ")), None)
            if text is not None:
                return text
            s.run(0.05)
        self.fail(f"no result for job {job} in {seconds} s")

    def heads(self, s, kind=mailbox.CODE):
        return [d.split(b"\n", 1)[0].decode() for k, d in s.comp.outbox if k == kind]

    def session(self, s):
        return s.comp.sessions[s.comp.current]

    def test_a_big_job_goes_compressed(self):
        s = self.start()
        self.assertEqual(self.session(s).features, link.ZIP)
        self.assertTrue(s.comp.zipping())
        job = s.comp.code(BIG, "=run")
        heads = self.heads(s)
        self.assertRegex(heads[0], rf"^{job} 1/{len(heads)} z:{len(BIG)}:[0-9a-f]{{8}} - =run$")
        self.assertLess(len(heads), -(-len(BIG) // 3700))                 # fewer parts than as it is
        text = self.run_result(s, job)
        self.assertTrue(text.startswith(f"{job} ok =run ({len(BIG)} B"), text)   # it ran what was sent, unpacked
        self.assertIn(f'"{BIG_SUM}", "{ROWS}"', text)

    def test_small_jobs_go_as_they_are(self):
        s = self.start()
        job = s.comp.code(b"return 1 + 1", "=run")
        self.assertEqual(self.heads(s), [f"{job} 1/1 - =run"])
        self.assertIn('"2"', self.run_result(s, job))

    def test_an_older_addon_gets_them_as_they_are(self):
        s = self.start(features=link.DATA)
        self.assertEqual(self.session(s).features, link.DATA)
        self.assertFalse(s.comp.zipping())
        job = s.comp.code(BIG, "=run")
        self.assertNotIn(" z:", " ".join(self.heads(s)))
        self.assertIn(f'"{BIG_SUM}", "{ROWS}"', self.run_result(s, job, 40))

    def test_a_data_call_goes_compressed(self):
        s = self.start()
        s.lua.execute(b'local WB = WoWBridge.Bind("Foo") WB:Expose("size", function(a) return #a.s + #a.list end, "sizes")')
        payload = {"a": "Foo", "n": "size", "d": {"s": "两个字" * 1500, "list": list(range(800))}}
        job = s.comp.data_call("call", payload)
        heads = self.heads(s, mailbox.CALL)
        self.assertRegex(heads[0], rf"^{job} 1/{len(heads)} z:\d+:[0-9a-f]{{8}} call$")
        text = self.run_result(s, job)
        self.assertTrue(text.startswith(f"{job} ok =call"), text)
        self.assertIn('\\"result\\":14300', text.replace(" ", ""))         # 4,500 characters of 3 bytes, and 800

    def test_a_job_that_cannot_be_unpacked_turns_compression_off(self):
        s = self.start()
        s.lua.execute(b'local ns = ... ns.Inflate.Raw = function() error("broken on purpose", 0) end', s.ns)
        job = s.comp.code(BIG, "=run")
        text = self.run_result(s, job)
        self.assertIn(link.UNPACK_FAILED, text)
        self.assertIn("broken on purpose", text)
        self.assertTrue(self.session(s).zip_off)
        self.assertFalse(s.comp.zipping())
        again = s.comp.code(BIG, "=run")                                  # the agent tries again: it goes as it is
        self.assertNotIn(" z:", " ".join(self.heads(s)))
        self.assertIn(f'"{BIG_SUM}", "{ROWS}"', self.run_result(s, again, 40))

    def test_a_client_that_did_not_load_inflate(self):
        """installed while the game ran, then a /reload: the client loads the files of the .toc it read at launch, so no
        Inflate.lua. The addon says f=2 and gets its jobs as they are; one that comes compressed all the same is
        refused with the reason"""
        s = self.start(patch={"Inflate.lua": lambda source: b""})
        self.assertEqual(self.session(s).features, link.DATA)
        self.assertFalse(s.comp.zipping())
        job = s.comp.code(BIG, "=run")
        self.assertNotIn(" z:", " ".join(self.heads(s)))
        self.assertIn(f'"{BIG_SUM}", "{ROWS}"', self.run_result(s, job, 40))
        s.comp.zipping = lambda: True
        again = s.comp.code(BIG, "=run")
        text = self.run_result(s, again)
        self.assertIn("could not be unpacked (Inflate.lua is not loaded)", text)

    def test_frames_go_up_faster_and_slow_down_once_one_is_missed(self):
        s = self.start()
        P = s.ns[b"Link"][b"P"]
        self.assertEqual((self.session(s).show, float(P[b"show"])), (0.15, 0.15))
        s.run(link.STEADY + 2)                                             # the link has been up a while
        lost = lose(s, lambda p: b"second" in p)
        for word in ("first", "second", "third"):
            s.lua.execute(b'print("%s")' % word.encode())
            s.run(1.2)
        self.assertTrue(lost["lost"])
        t0 = s.now()
        while float(P[b"show"]) != 0.2 and s.now() - t0 < 10:
            s.run(0.1)
        self.assertEqual((self.session(s).show, float(P[b"show"])), (0.2, 0.2))   # told in a new WELCOME, for good
        self.assertTrue(any("frames stay up 0.2 s" in line for line in self.logs))
        self.assertIn("second", [t for _, k, t in self.got if k == "OUT"])

    def test_a_miss_right_after_the_link_came_up_does_not_count(self):
        s = self.start()
        lost = lose(s, lambda p: b"second" in p)
        for word in ("first", "second", "third"):
            s.lua.execute(b'print("%s")' % word.encode())
            s.run(1.2)
        self.assertTrue(lost["lost"])
        self.until_out(s, "second")
        self.assertGreaterEqual(s.comp.stats["asked"], 1)
        self.assertEqual(self.session(s).show, 0.15)

    def until_out(self, s, word, seconds=10):
        t0 = s.now()
        while word not in [t for _, k, t in self.got if k == "OUT"] and s.now() - t0 < seconds:
            s.run(0.05)

    def shapes(self, s, since, ftype):
        """(W, H, mode) of the frames of a type drawn since then"""
        return {(W, H, mode) for t, ft, W, H, mode, *_ in s.shapes if t >= since and ft == ftype}

    def part_sizes(self, s, head):
        """the sizes of the parts of the message whose first part starts with head, by part, as they were drawn"""
        msg = next(m for (_, _, m, payload), sh in zip(s.frames, s.shapes) if sh[6] == 0 and payload.startswith(head))
        sizes = {}
        for (_, _, m, payload), sh in zip(s.frames, s.shapes):
            if m == msg and sh[1] == F.TYPE_DATA:
                sizes[sh[6]] = len(payload)
        return [sizes[i] for i in range(len(sizes))]

    def test_a_long_message_breathes(self):
        """mode 3: a long message's parts go from 45 % of a big frame's room up to 70 % and back, a cosine over 12 parts
        (its ∞ swells and shrinks as it goes up, never a blob); it is read whole"""
        s = self.start()
        cap = F.capacity(128, 32, 3)
        s.slash("long 12000")
        t0 = s.now()
        while s.comp.stats["long_ok"] < 1 and s.now() - t0 < 30:
            s.run(0.05)
        self.assertEqual((s.comp.stats["long_ok"], s.comp.stats["long_bad"]), (1, 0))
        sizes = self.part_sizes(s, b"LONG n=12000")
        want = [int(cap * (0.45 + 0.25 * (1 - math.cos(2 * math.pi * i / 12)) / 2)) for i in range(len(sizes) - 1)]
        self.assertEqual(sizes[:-1], want)
        self.assertEqual((min(want), max(want)), (int(cap * 0.45), int(cap * 0.70)))
        self.assertLessEqual(sizes[-1], int(cap * 0.70))
        self.assertEqual(sum(sizes), 12000 + 1)                          # the type byte too

    def test_an_older_companion_gets_full_parts(self):
        """mode 1 (a companion before 0.9.9): the parts fill their frames, as before"""
        s = self.start(welcome=dict(mode=1, w=64, h=16, bw=128, bh=32, win=16, show=0.25))
        s.slash("long 5000")
        t0 = s.now()
        while s.comp.stats["long_ok"] < 1 and s.now() - t0 < 20:
            s.run(0.05)
        self.assertEqual(s.comp.stats["long_ok"], 1)
        sizes = self.part_sizes(s, b"LONG n=5000")
        self.assertEqual(sizes[:-1], [F.capacity(128, 32, 1)] * (len(sizes) - 1))

    def test_frames_along_infinity(self):
        """the WELCOME says mode 3: heartbeats come 32 x 12 and data frames in mode 3, their cells laid along ∞; the
        HELLO before it was in mode 1; everything is read as before (the mock decodes every frame with frame.py)"""
        s = self.start()
        self.assertEqual({sh for sh in self.shapes(s, 0, F.TYPE_CONTROL) if sh[:2] == (64, 16)}, {(64, 16, 1)})   # HELLO
        t0 = s.now()
        s.lua.execute(b'print("along the curve")')
        s.run(6)
        self.assertIn((32, 12, 3), self.shapes(s, t0, F.TYPE_CONTROL))
        self.assertEqual(self.shapes(s, t0, F.TYPE_DATA), {(56, 16, 3)})              # mode 3's narrower data frame
        self.assertIn("along the curve", [t for _, k, t in self.got if k == "OUT"])

    def test_the_addon_lays_the_cells_out_as_frame_py_does(self):
        """Frame.InfOrder (Lua 5.1) and frame.py inf_order: the same order, cell by cell, for the link's frame sizes and
        others"""
        s = Session(self)
        self.addCleanup(s.close)
        inf = s.ns[b"Frame"][b"InfOrder"]
        for W, H in ((24, 8), (32, 12), (56, 16), (128, 32), (32, 16), (64, 16), (64, 32), (37, 11), (200, 48), (101, 77)):
            with self.subTest(W=W, H=H):
                keys = inf(W, H)
                self.assertEqual([int(keys[i + 1]) for i in range(len(keys))], [y * W + x for x, y in F.inf_order(W, H)])

    def test_an_older_addon_stays_in_mode_1(self):
        """an addon before 0.9.9 takes modes 0..2 only: the WELCOME's mode 3 leaves it in mode 1, heartbeats 32 x 8"""
        s = self.start(patch={"Link.lua": lambda b: b.replace(b'n("mode") <= 3', b'n("mode") <= 2')})
        t0 = s.now()
        s.lua.execute(b'print("rows as before")')
        s.run(6)
        self.assertEqual(self.shapes(s, t0, F.TYPE_DATA), {(64, 16, 1)})
        self.assertIn((32, 8, 1), self.shapes(s, t0, F.TYPE_CONTROL))
        self.assertIn("rows as before", [t for _, k, t in self.got if k == "OUT"])

    def test_an_older_companion_keeps_mode_1(self):
        """a companion before 0.9.9 says mode 1: so it stays"""
        s = self.start(welcome=dict(mode=1, w=64, h=16, bw=128, bh=32, win=16, show=0.25))
        t0 = s.now()
        s.lua.execute(b'print("rows as before")')
        s.run(6)
        self.assertEqual(self.shapes(s, t0, F.TYPE_DATA), {(64, 16, 1)})
        self.assertEqual(self.shapes(s, t0, F.TYPE_CONTROL) - {(64, 16, 1)}, {(32, 8, 1)})

    def test_a_long_message_at_the_shorter_display_time(self):
        """8,000 bytes (big frames, breathing) go up whole, nothing asked for again, each part shown about 0.15 s"""
        s = self.start()
        s.slash("long 8000")
        t0 = s.now()
        while s.comp.stats["long_ok"] < 1 and s.now() - t0 < 10:
            s.run(0.05)
        self.assertEqual((s.comp.stats["long_ok"], s.comp.stats["long_bad"], s.comp.stats["asked"]), (1, 0, 0))
        times = [t for t, ftype, msg, payload in s.frames if ftype == 0 and len(payload) > 500]
        gaps = [b - a for a, b in zip(times, times[1:]) if b - a < 1]
        self.assertTrue(gaps)
        self.assertLess(max(gaps), 0.2)


if __name__ == "__main__":
    unittest.main()
