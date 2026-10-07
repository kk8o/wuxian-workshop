"""Snaps without a game window (agent/snap.py: cutting, scaling and saving an image) and the probe diagnostics parser."""
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from wuxianworkshop.agent.diag import parse_diag
from wuxianworkshop.agent.snap import SnapError, save_snap, take_snap


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


class Diag(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_diag(b"pw=1920;ph=1080; es = 0.5 ;junk;"), {"pw": "1920", "ph": "1080", "es": "0.5"})


if __name__ == "__main__":
    unittest.main()
