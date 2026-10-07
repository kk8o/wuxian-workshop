"""CompanionLoop finds a frame the player moved (WoWBridge's /wb unlock): reads in the top-left corner miss it, a search
of the whole client area finds it, and the reads go on from there. A synthetic client area stands in for the screen."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import render
from wuxianworkshop.core import frame as F, mailbox as MB
from wuxianworkshop.daemon import companion as C
from wuxianworkshop.transport.capture import Window

SIZE = (1280, 720)


class Screen:
    """grab_client over one picture of the client area"""

    def __init__(self, at):
        self.at = at
        self.calls = []
        self.draw()

    def draw(self):
        cells = render.sample_frame(64, 16, ftype=F.TYPE_CONTROL, payload=b"HB;n=1;k=1;m=1;a=0;q=0", session=0x2222, msg=1)
        self.img = render.draw(cells, 64, 16, 4, origin=self.at, size=SIZE, background=60)

    def draw_big(self):
        """a part of a long message: the biggest frame, 128 x 32 cells"""
        cells = render.sample_frame(128, 32, ftype=F.TYPE_DATA, payload=b"x" * 1000, session=0x2222, msg=2)
        self.img = render.draw(cells, 128, 32, 4, origin=self.at, size=SIZE, background=60)

    def grab(self, win, max_w=None, max_h=None, x=0, y=0):
        self.calls.append((max_w, max_h, x, y))
        w = SIZE[0] - x if max_w is None else min(SIZE[0] - x, max_w)
        h = SIZE[1] - y if max_h is None else min(SIZE[1] - y, max_h)
        return self.img[y:y + h, x:x + w]


class MovedFrame(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(self.tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.addons = Path(self.tmp.name) / "AddOns"
        (self.addons / "WoWBridge").mkdir(parents=True)
        MB.install(self.addons, pool=16)
        win = Window(hwnd=1, pid=os.getpid(), exe="", title="魔兽世界", x=0, y=0, w=SIZE[0], h=SIZE[1], dpi=96, minimized=False)
        self.notes_list = []
        self.loop = C.CompanionLoop(self.addons, capture="gdi", wait_restart=False, files=False,
                                    log=self.notes_list.append, find_window=lambda: win)
        self.loop._wait = lambda seconds: None

    def run_rounds(self, screen, n):
        with mock.patch.object(C, "grab_client", screen.grab), mock.patch.object(C.time, "time", side_effect=self.clock()):
            for _ in range(n):
                self.loop.step()

    def clock(self):
        t = [1000.0]

        def tick():
            t[0] += 0.05                       # every round is 50 ms later: a search may come once a second
            return t[0]
        while True:
            yield tick()

    def test_a_frame_away_from_the_corner_is_found(self):
        screen = Screen(at=(700, 400))
        self.run_rounds(screen, 60)
        self.assertEqual(self.loop.roi_at, (700 - C.MARGIN, 400 - C.MARGIN))
        self.assertTrue(any("frame found at (700,400)" in n for n in self.notes_list), self.notes_list)
        self.assertIn((None, None, 0, 0), screen.calls)                     # the whole client area, once lost
        self.assertIn((C.ROI_SMALL[0], C.ROI_SMALL[1], 700 - C.MARGIN, 400 - C.MARGIN), screen.calls)
        self.assertGreater(self.loop.comp.stats["frames"], 0)                # read from there and handed on
        self.assertEqual(self.loop.misses, 0)

    def test_moved_again(self):
        screen = Screen(at=(700, 400))
        self.run_rounds(screen, 60)
        screen.at = (40, 600)                                                # dragged elsewhere
        screen.draw()
        self.run_rounds(screen, 60)
        self.assertEqual(self.loop.roi_at, (40 - C.MARGIN, 600 - C.MARGIN))

    def test_the_region_follows_a_frame_moved_within_it(self):
        screen = Screen(at=(100, 100))
        self.run_rounds(screen, 60)
        self.assertEqual(self.loop.roi_at, (100 - C.MARGIN, 100 - C.MARGIN))
        screen.at = (200, 150)                     # dragged a little: still inside the region, no search needed
        screen.draw()
        self.run_rounds(screen, 3)
        self.assertEqual(self.loop.roi_at, (200 - C.MARGIN, 150 - C.MARGIN))
        before = self.loop.comp.stats["frames"]
        screen.draw_big()                          # 520 px wide: past the region where the frame was first found
        self.run_rounds(screen, 3)
        self.assertEqual(self.loop.comp.stats["frames"], before + 1)

    def test_the_corner_needs_no_search(self):
        screen = Screen(at=(0, 0))
        self.run_rounds(screen, 10)
        self.assertEqual(self.loop.roi_at, (0, 0))
        self.assertNotIn((None, None, 0, 0), screen.calls)
        self.assertGreater(self.loop.comp.stats["frames"], 0)


class FollowsTheRunningGame(unittest.TestCase):
    def test_a_game_from_another_folder(self):
        """the loop started with the old client folder; the game now runs from a new one (a moved or a new client): the
        link has to go through the running game's mailbox"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)
        old, new = (Path(tmp.name) / d / "Interface" / "AddOns" for d in ("_cn_beta_", "_cn_"))
        for addons in (old, new):
            (addons / "WoWBridge").mkdir(parents=True)
            MB.install(addons, pool=16)
        exe = new.parent.parent / "Wow.exe"
        exe.write_bytes(b"")
        win = Window(hwnd=1, pid=os.getpid(), exe=str(exe), title="魔兽世界", x=0, y=0, w=SIZE[0], h=SIZE[1], dpi=96, minimized=False)
        notes = []
        loop = C.CompanionLoop(old, capture="gdi", wait_restart=False, files=False, log=notes.append, find_window=lambda: win)
        loop._wait = lambda seconds: None
        with mock.patch.object(C, "grab_client", Screen((40, 40)).grab):
            loop.step()
        self.assertEqual(loop.addons, new)
        self.assertTrue(any(n.startswith("the game runs from") and n.endswith("following it") for n in notes), notes)
        self.assertTrue(any(n.startswith("game process P") for n in notes), notes)   # the new folder's mailbox in use


if __name__ == "__main__":
    unittest.main()
