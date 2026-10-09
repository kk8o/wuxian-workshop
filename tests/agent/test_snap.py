"""Snaps without a game window (agent/snap.py: cutting, scaling and saving an image, WoWBridge's frame in a picture and
where it shows in a snap) and the probe diagnostics parser."""
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from tests.support import render as R
from wuxianworkshop.agent.diag import parse_diag
from wuxianworkshop.agent.snap import SnapError, in_picture, link_frame, save_snap, take_snap
from wuxianworkshop.core import frame as F


class SaveSnap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_region_and_scale(self):
        img = np.zeros((40, 60, 3), np.uint8)
        img[10:20, 10:30] = (200, 30, 30)
        path, w, h = save_snap(img, region=[10, 10, 20, 10], max_width=10)
        self.assertEqual((w, h), (10, 5))
        self.assertTrue(path.exists())
        self.assertEqual(path.parent.name, "snaps")
        self.assertTrue(str(path.parent.parent).startswith(self.tmp.name))
        from PIL import Image
        with Image.open(path) as pic:
            self.assertEqual(pic.size, (10, 5))
            self.assertGreater(pic.getpixel((5, 2))[0], 150)         # the red block, scaled

    def test_whole_picture_kept_when_narrow_enough(self):
        path, w, h = save_snap(np.zeros((8, 16, 3), np.uint8), max_width=1280)
        self.assertEqual((w, h), (16, 8))

    def test_region_outside(self):
        with self.assertRaises(SnapError):
            save_snap(np.zeros((8, 16, 3), np.uint8), region=[100, 100, 4, 4])

    def test_take_snap_text(self):
        win = mock.Mock(w=16, h=8, minimized=False)
        with mock.patch("wuxianworkshop.agent.snap.grab_client", return_value=np.zeros((8, 16, 3), np.uint8)):
            text = take_snap("snap 0 0 4 4", win, None)
        self.assertRegex(text, r"snap-\d{6}-\d{3}\.png 4x4 \(GDI: anything over the game is in it\)$")
        with mock.patch("wuxianworkshop.agent.snap.grab_client", side_effect=OSError("BitBlt failed")):
            self.assertEqual(take_snap("snap", win, None), "snap failed: BitBlt failed")
        win.minimized = True                                       # nothing is drawn: say so instead of a stale picture
        self.assertEqual(take_snap("snap", win, None), "snap failed: the game window is minimized: nothing is drawn")


class LinkFrame(unittest.TestCase):
    """seen in the game (1.60.1.70245, the frame moved to 10, 119): inspect of an addon's window with snap pictured
    [271, 130, 825, 612] of the client area, and its top-left 259 x 125 pixels were the black part of a 128 x 32 frame,
    the RUN result of the inspect itself, still up when the picture was taken"""

    def screen(self, W, H, ftype, payload):
        cells = F.build(W, H, 1, 1, ftype, 0x1234, 7, payload)
        return R.draw(cells, W, H, 4, origin=(10, 119), size=(700, 400), background=(40, 44, 52))

    def test_found_with_its_place_and_whether_it_rests(self):
        self.assertEqual(link_frame(self.screen(128, 32, F.TYPE_DATA, b"\x02" + b"1546762771 ok =probe" * 20)),
                         ([10, 119, 520, 136], False))                     # a long message's part: 520 x 136
        self.assertEqual(link_frame(self.screen(64, 16, F.TYPE_DATA, b"\x02" + b"42 ok =run")),
                         ([10, 119, 264, 72], False))
        self.assertEqual(link_frame(self.screen(32, 8, F.TYPE_CONTROL, b"HB;n=9;k=181;m=3799;l=3798;q=0")),
                         ([10, 119, 136, 40], True))                       # the heartbeat: at rest
        self.assertIsNone(link_frame(np.full((400, 700, 3), 40, np.uint8)))

    def test_where_it_shows_in_a_snap(self):
        client = (1920, 1001)
        self.assertEqual(in_picture([10, 119, 520, 136], [271, 130, 825, 612], client), [0, 0, 259, 125])   # the one seen
        self.assertIsNone(in_picture([10, 119, 136, 40], [271, 130, 825, 612], client))   # the heartbeat is not in it
        self.assertEqual(in_picture([10, 119, 136, 40], None, client), [10, 119, 136, 40])
        self.assertEqual(in_picture([10, 119, 136, 40], None, client, (1280, 667)), [7, 79, 91, 27])  # scaled as saved
        self.assertEqual(in_picture([1900, 990, 40, 40], [1800, 900, 400, 400], client), [100, 90, 20, 11])  # the edge


class Diag(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_diag(b"pw=1920;ph=1080; es = 0.5 ;junk;"), {"pw": "1920", "ph": "1080", "es": "0.5"})


if __name__ == "__main__":
    unittest.main()
