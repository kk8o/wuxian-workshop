"""CompanionLoop's cheap reads: a frame still up (the same pixels in its box) is not located and decoded again, and
while no frame is found the reads slow down and the searches of the whole window come further apart. A synthetic
client area stands in for the screen, a fake clock for time.time()."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.support import render
from wuxianworkshop.core import frame as F, mailbox as MB
from wuxianworkshop.core.decode import Decoded
from wuxianworkshop.daemon import companion as C
from wuxianworkshop.transport.capture import Window

SIZE = (1280, 720)


class Screen:
    """grab_client over one picture of the client area: a heartbeat frame at `at`, or none"""

    def __init__(self, at=(C.MARGIN, C.MARGIN)):   # where the region read stays at (0, 0)
        self.at, self.n = at, 0
        self.calls = []
        self.draw()

    def draw(self):
        """the addon's next frame: the toggle cell flips, the message number grows"""
        self.n += 1
        cells = render.sample_frame(64, 16, ftype=F.TYPE_CONTROL, payload=b"HB;n=%d;k=1;m=1;a=0;q=0" % self.n,
                                    session=0x2222, msg=self.n, toggle=self.n % 2)
        self.img = render.draw(cells, 64, 16, 4, origin=self.at, size=SIZE, background=60)

    def clear(self):
        self.img = render.draw({}, 64, 16, 4, origin=(-10000, -10000), size=SIZE, background=60)

    def grab(self, win, max_w=None, max_h=None, x=0, y=0, reader=None):
        self.calls.append((max_w, max_h, x, y))
        w = SIZE[0] - x if max_w is None else min(SIZE[0] - x, max_w)
        h = SIZE[1] - y if max_h is None else min(SIZE[1] - y, max_h)
        return self.img[y:y + h, x:x + w]


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)
        addons = Path(tmp.name) / "AddOns"
        (addons / "WoWBridge").mkdir(parents=True)
        MB.install(addons, pool=16)
        self.win = Window(hwnd=1, pid=os.getpid(), exe="", title="魔兽世界", x=0, y=0, w=SIZE[0], h=SIZE[1], dpi=96,
                          minimized=False)
        self.notes = []
        self.loop = C.CompanionLoop(addons, capture="gdi", wait_restart=False, files=False, log=self.notes.append,
                                    find_window=lambda: self.win)
        self.clock, self.waits = [1000.0], []

        def wait(seconds):                       # the pause between rounds passes on the fake clock
            self.waits.append(seconds)
            self.clock[0] += max(seconds, 0.001)
        self.loop._wait = wait
        self.screen = Screen()
        self.locates, self.decodes = [], []
        real_locate, real_decode = C.locate, C.decode
        for name, calls, real in (("locate", self.locates, real_locate), ("decode", self.decodes, real_decode)):
            p = mock.patch.object(C, name, side_effect=lambda *a, _c=calls, _r=real: _c.append(1) or _r(*a))
            p.start()
            self.addCleanup(p.stop)
        for p in (mock.patch.object(C, "grab_client", lambda *a, **k: self.screen.grab(*a, **k)),
                  mock.patch.object(C.time, "time", lambda: self.clock[0])):
            p.start()
            self.addCleanup(p.stop)

    def rounds(self, n):
        for _ in range(n):
            self.loop.step()


class UnchangedFrame(Base):
    def test_the_same_frame_is_decoded_once(self):
        self.rounds(40)                                         # the heartbeat stays up 2 s: 40 reads
        self.assertEqual((len(self.locates), len(self.decodes)), (1, 1))
        self.assertEqual((self.loop.loops, self.loop.unchanged, self.loop.misses), (40, 39, 0))
        self.assertEqual(self.loop.comp.stats["frames"], 1)
        self.assertGreater(self.loop.busy, 0)
        self.assertEqual(self.loop.report()["host"]["unchanged"], 39)

    def test_a_new_frame_goes_to_the_companion(self):
        self.rounds(5)
        for i in range(3):
            self.screen.draw()                                  # the toggle flips: the box changes
            self.rounds(5)
        self.assertEqual(len(self.decodes), 4)
        self.assertEqual(self.loop.comp.stats["frames"], 4)
        self.assertEqual(self.loop.unchanged, 16)

    def test_a_change_outside_the_box_is_not_a_new_frame(self):
        self.rounds(2)
        self.screen.img = self.screen.img.copy()
        self.screen.img[400:420, 400:420] = 200                 # the game's picture around the frame changes
        self.rounds(2)
        self.assertEqual(len(self.decodes), 1)

    def test_a_read_that_was_not_decoded_is_not_kept(self):
        self.rounds(1)
        with mock.patch.object(C, "decode", side_effect=lambda img, lk: self.decodes.append(1) or Decoded(lk, ok=False)):
            self.loop.same = None                               # as if the last decode had failed
            self.rounds(3)
            self.assertIsNone(self.loop.same)
        self.assertEqual(len(self.decodes), 4)                  # every read decoded again
        self.assertEqual(self.loop.bad, 3)
        self.rounds(3)
        self.assertEqual(len(self.decodes), 5)

    def test_not_kept_while_the_window_moves_or_waits_for_a_relock(self):
        self.rounds(3)
        self.win = Window(**{**self.win.__dict__, "x": 50})     # dragged: moving until it held still SETTLE
        self.rounds(3)
        self.assertEqual(len(self.decodes), 4)
        self.assertIsNotNone(self.loop.moving_since)
        self.clock[0] += C.SETTLE
        self.rounds(1)                                          # settled: decoded again, the relock logged
        self.assertIsNone(self.loop.relock_from)
        self.assertTrue(any(n.startswith("re-locked") for n in self.notes), self.notes)
        before = len(self.decodes)
        self.rounds(3)
        self.assertEqual(len(self.decodes), before)             # kept again from there
        self.loop.relock_from = self.clock[0]
        self.rounds(1)
        self.assertEqual(len(self.decodes), before + 1)

    def test_another_place_size_capture_or_window(self):
        self.rounds(2)
        region, img = self.loop.roi, self.screen.img[:C.ROI_SMALL[1], :C.ROI_SMALL[0]]

        def decoded(**change):
            n = len(self.decodes)
            saved = {k: getattr(self.loop, k) for k in change if k not in ("win", "img")}
            for k in saved:
                setattr(self.loop, k, change[k])
            self.loop._frame(change.get("win", self.win), img if "img" not in change else change["img"], region,
                             time.perf_counter())
            for k, v in saved.items():
                setattr(self.loop, k, v)
            return len(self.decodes) > n
        self.assertFalse(decoded())
        self.assertTrue(decoded(roi_at=(4, 4)))
        self.loop._frame(self.win, img, region, time.perf_counter())         # kept again
        self.assertTrue(decoded(img=img[:200, :500]))                          # a smaller read (the window shrank)
        self.loop._frame(self.win, img, region, time.perf_counter())
        self.assertTrue(decoded(capture="wgc"))
        self.loop._frame(self.win, img, region, time.perf_counter())
        self.assertTrue(decoded(win=Window(**{**self.win.__dict__, "hwnd": 2})))

    def test_a_region_of_another_size(self):
        self.rounds(2)
        self.loop.roi = (500, 200)
        self.rounds(1)
        self.assertEqual(len(self.decodes), 2)

    def test_the_window_gone_releases_what_was_kept(self):
        self.rounds(2)
        closed = []
        self.loop.gdi.close = lambda: closed.append(1)
        self.win = None
        self.rounds(1)
        self.assertIsNone(self.loop.same)
        self.assertTrue(closed)

    def test_the_reads_go_through_the_kept_gdi_objects(self):
        got = []
        with mock.patch.object(C, "grab_client", lambda *a, **k: got.append(k.get("reader")) or self.screen.grab(*a, **k)):
            self.rounds(1)
        self.assertIs(got[0], self.loop.gdi)

    def test_run_closes_the_gdi_objects(self):
        closed = []
        self.loop.gdi.close = lambda: closed.append(1)
        self.loop.run(stop_when=lambda: True)
        self.assertEqual(closed, [1])


class LostFrame(Base):
    def test_reads_slow_down_and_speed_up_again(self):
        self.screen.clear()
        self.rounds(C.MISSES_BEFORE_FULL)
        self.assertEqual(self.loop.misses, C.MISSES_BEFORE_FULL)
        self.assertTrue(all(w <= self.loop.rate for w in self.waits[:-1]), self.waits)   # the 20th miss: slow already
        self.assertEqual(self.loop._rate(), C.LOST_RATE)
        self.waits.clear()
        self.rounds(5)
        self.assertGreater(max(self.waits), self.loop.rate)                  # 0.2 s less the read's own time
        self.screen.draw()                                                   # the addon draws again, in the region
        self.rounds(1)
        self.assertEqual(self.loop.misses, 0)
        self.assertEqual(self.loop._rate(), self.loop.rate)
        self.assertEqual(len(self.decodes), 1)

    def searches(self, seconds):
        at = []
        real = self.loop._search
        self.loop._search = lambda win: at.append(self.clock[0]) or real(win)
        end = self.clock[0] + seconds
        while self.clock[0] < end:
            self.loop.step()
        return at

    def test_the_searches_come_further_apart(self):
        self.screen.clear()
        at = self.searches(40)
        gaps = [round(b - a) for a, b in zip(at, at[1:])]
        self.assertEqual(gaps[:5], [1, 2, 4, 8, 8], (at, gaps))
        self.assertLess(len(at), 10)

    def test_a_search_that_finds_the_frame_starts_over(self):
        self.screen.clear()
        self.searches(10)
        self.assertGreaterEqual(self.loop.search_fails, 3)
        self.screen.at = (700, 400)                             # the frame is up, away from the region read
        self.screen.draw()
        self.searches(10)
        self.assertEqual(self.loop.search_fails, 0)
        self.assertEqual(self.loop.roi_at, (700 - C.MARGIN, 400 - C.MARGIN))
        self.assertEqual(self.loop.misses, 0)
        self.assertGreater(self.loop.comp.stats["frames"], 0)

    def test_the_window_moved_or_resized_starts_over(self):
        self.screen.clear()
        self.searches(10)
        self.assertGreaterEqual(self.loop.search_fails, 3)
        self.win = Window(**{**self.win.__dict__, "w": SIZE[0] - 100})
        self.rounds(1)                                          # searched again at once, the next one 1 s later
        self.assertLessEqual(self.loop.search_fails, 1)
        self.assertEqual(self.loop._search_every(), C.FULL_SEARCH_EVERY)


if __name__ == "__main__":
    unittest.main()
