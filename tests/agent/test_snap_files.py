"""The snaps' files (agent/snap.py): PNG at the fast zlib level, still lossless; JPEG when asked; and the folder kept to
the newest KEEP_FILES snaps within KEEP_BYTES, the one just saved always kept, other files never touched."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from wuxianworkshop.agent import snap
from wuxianworkshop.agent.snap import prune, save_snap


def picture():
    """a UI-like picture: flat panels, a gradient, a little noise"""
    rng = np.random.default_rng(7)
    img = np.zeros((90, 160, 3), np.uint8)
    img[:, :, 0] = np.linspace(0, 255, 160, dtype=np.uint8)
    img[20:60, 30:120] = (90, 90, 90)
    img[70:80] += rng.integers(0, 40, (10, 160, 3), dtype=np.uint8)
    return img


class Formats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_png_is_lossless(self):
        img = picture()
        path, w, h = save_snap(img)
        self.assertEqual((path.suffix, w, h), (".png", 160, 90))
        with Image.open(path) as pic:
            self.assertEqual(pic.format, "PNG")
            self.assertTrue(np.array_equal(np.asarray(pic.convert("RGB")), img))

    def test_jpeg_when_asked(self):
        img = picture()
        path, w, h = save_snap(img, region=[0, 0, 100, 50], format="jpeg")
        self.assertEqual((path.suffix, w, h), (".jpg", 100, 50))
        self.assertEqual(path.read_bytes()[:3], b"\xff\xd8\xff")
        with Image.open(path) as pic:
            self.assertEqual(pic.format, "JPEG")
            got = np.asarray(pic.convert("RGB")).astype(int)
        self.assertLess(np.abs(got - img[:50, :100].astype(int)).mean(), 3)     # quality 90: close to the picture
        with self.assertRaises(ValueError):
            save_snap(img, format="gif")

    def test_each_save_prunes_the_folder(self):
        with mock.patch.object(snap, "KEEP_FILES", 3):
            paths = []
            for i in range(5):
                paths.append(save_snap(picture(), format="jpeg" if i % 2 else "png")[0])
                time.sleep(0.002)                       # another name (the time is in it)
            left = sorted(p.name for p in paths[0].parent.iterdir())
        self.assertEqual(len(left), 3)
        self.assertIn(paths[-1].name, left)


class Prune(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def snaps(self, n, size=100, ext=".png"):
        """n snaps, the first the oldest"""
        out = []
        for i in range(n):
            p = self.dir / f"snap-{i:06d}-000{ext}"
            p.write_bytes(b"x" * size)
            os.utime(p, ns=(10 ** 18 + i * 10 ** 9,) * 2)
            out.append(p)
        return out

    def left(self):
        return sorted(p.name for p in self.dir.iterdir())

    def test_by_count(self):
        made = self.snaps(10)
        self.assertEqual(prune(self.dir, files=4, size=10 ** 9), 6)
        self.assertEqual(self.left(), sorted(p.name for p in made[-4:]))

    def test_by_size(self):
        made = self.snaps(6, size=100)
        self.assertEqual(prune(self.dir, files=100, size=350), 3)
        self.assertEqual(self.left(), sorted(p.name for p in made[-3:]))

    def test_kept_ones_stay_and_others_are_not_touched(self):
        made = self.snaps(5)
        jpg = self.dir / "snap-999999-000.jpg"                       # the newest
        jpg.write_bytes(b"x" * 100)
        os.utime(jpg, ns=(2 * 10 ** 18,) * 2)
        made.append(jpg)
        (self.dir / "notes.txt").write_text("mine")
        (self.dir / "other.png").write_bytes(b"x")
        prune(self.dir, keep=[made[0]], files=2, size=10 ** 9)
        self.assertEqual(self.left(), sorted([made[0].name, made[4].name, made[5].name, "notes.txt", "other.png"]))

    def test_nothing_to_do(self):
        self.snaps(3)
        self.assertEqual(prune(self.dir, files=3, size=300), 0)
        self.assertEqual(prune(self.dir / "missing"), 0)

    def test_the_limits(self):
        self.assertEqual((snap.KEEP_FILES, snap.KEEP_BYTES), (300, 200 * 1024 * 1024))


if __name__ == "__main__":
    unittest.main()
