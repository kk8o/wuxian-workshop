""".venv\\Scripts\\python.exe -m unittest discover -s tests -t .   (from the repository root)"""
import random
import unittest

from tests.support.probe_report import analyse
from tests.support import render as R
from wuxianworkshop.core import frame as F
from wuxianworkshop.core.decode import decode
from wuxianworkshop.core.locate import locate


class Codec(unittest.TestCase):
    def test_crc_vectors(self):
        self.assertEqual(F.crc16(b"123456789"), 0x29B1)          # CRC-16/CCITT-FALSE check value
        self.assertEqual(F.crc32(b"123456789"), 0xCBF43926)

    def test_pack_roundtrip(self):
        rnd = random.Random(1)
        for bits in (1, 3, 6):
            for n in (0, 1, 2, 15, 16, 100):
                data = bytes(rnd.randrange(256) for _ in range(n))
                self.assertEqual(F.unpack(F.pack(data, bits), bits, n), data)

    def test_header_roundtrip(self):
        h = F.header(F.TYPE_PROBE, 64, 32, 0xBEEF, 513, 2, 3, 300)
        self.assertEqual(len(h), F.HEADER_LEN)
        self.assertEqual(F.parse_header(h), dict(type=1, W=64, H=32, session=0xBEEF, msg=513, part=2, parts=3, length=300))
        self.assertIsNone(F.parse_header(h[:-1] + bytes([h[-1] ^ 1])))

    def test_capacity_matches_design_doc(self):
        self.assertEqual(len(F.layout(64, 16)[1]), 918)
        self.assertEqual([F.capacity(64, 16, m) for m in (0, 1, 2)], [95, 325, 669])
        self.assertEqual([F.capacity(32, 8, m) for m in (0, 1, 2)], [4, 52, 123])
        self.assertEqual([F.capacity(200, 48, m) for m in (0, 1, 2)], [1146, 3478, 6975])

    def test_payload_too_big(self):
        with self.assertRaises(ValueError):
            F.build(32, 8, 1, 0, F.TYPE_DATA, 1, 1, b"x" * 53)


class EndToEnd(unittest.TestCase):
    def roundtrip(self, W=64, H=16, mode=1, cell=4, origin=(0, 0), transform=None, payload=b"hello WoWBridge", size=(640, 360)):
        cells = F.build(W, H, mode, 1, F.TYPE_DATA, 0x1234, 7, payload)
        img = R.draw(cells, W, H, cell, origin=origin, size=size, transform=transform)
        lk = locate(img)
        self.assertIsNotNone(lk, "frame not found")
        self.assertEqual((lk.W, lk.H), (W, H))
        d = decode(img, lk)
        self.assertTrue(d.ok, d.error)
        self.assertEqual((d.mode, d.toggle, d.payload), (mode, 1, payload))
        return lk, d

    def test_cell_sizes(self):
        for cell in (2, 3, 4, 6, 8):
            with self.subTest(cell=cell):
                lk, _ = self.roundtrip(cell=cell)
                self.assertAlmostEqual(lk.px, cell, places=6)

    def test_offset_and_modes(self):
        for mode in (0, 1, 2):
            with self.subTest(mode=mode):
                self.roundtrip(mode=mode, origin=(37, 15), payload=bytes(range(60)))

    def test_non_integer_pitch(self):
        for cell in (3.6, 4.25, 4.7, 5.5):
            with self.subTest(cell=cell):
                lk, _ = self.roundtrip(cell=cell, origin=(11.3, 7.8))
                self.assertAlmostEqual(lk.px, cell, delta=0.05)

    def test_large_frame(self):
        self.roundtrip(W=200, H=48, payload=bytes(random.Random(2).randrange(256) for _ in range(3478)), size=(900, 260))

    def test_colour_distortions(self):
        for name, t in (("gamma 0.5", R.gamma(0.5)), ("gamma 2.0", R.gamma(2.0)), ("low contrast", R.contrast(0.55, 20)),
                        ("edge blur", R.box_blur_edges)):
            with self.subTest(name):
                self.roundtrip(transform=t, cell=4 if name != "edge blur" else 6)

    def test_probe_frame_report(self):
        W, H = 64, 32
        diag = b"pw=1920;ph=1080;gm=1.0"
        cells = F.build(W, H, 1, 0, F.TYPE_PROBE, 9, 1, diag)
        img = R.draw(cells, W, H, 4, size=(400, 200))
        lk = locate(img)
        d = decode(img, lk)
        rep = analyse(img, lk, d)
        self.assertTrue(d.ok, d.error)
        self.assertEqual(rep["diag"]["pw"], "1920")
        self.assertEqual(rep["purity"]["pure"], rep["purity"]["cells"])
        self.assertTrue(rep["modes"]["bit_exact"])
        self.assertTrue(rep["modes"]["mode2_4levels"])
        g = analyse(R.draw(cells, W, H, 4, size=(400, 200), transform=R.gamma(2.2)), lk, decode(R.draw(cells, W, H, 4, size=(400, 200), transform=R.gamma(2.2)), lk))
        self.assertFalse(g["modes"]["bit_exact"])
        self.assertTrue(g["modes"]["mode1_8colours"])

    def test_busy_background(self):
        """a tall frame on per-pixel noise: thousands of magenta blobs above the bottom-left finder"""
        cells = F.build(64, 40, 1, 1, F.TYPE_PROBE, 0x4242, 1, b"v=0.3.0")
        img = R.draw(cells, 64, 40, 4, origin=(4, 4), size=(1024, 640), seed=1)
        lk = locate(img)
        self.assertIsNotNone(lk)
        self.assertEqual((lk.ox, lk.oy, lk.px, lk.W, lk.H), (8.0, 8.0, 4.0, 64, 40))
        self.assertTrue(decode(img, lk).ok)

    def test_no_frame(self):
        self.assertIsNone(locate(R.draw({}, 64, 16, 4, size=(320, 200), origin=(-10000, -10000))))


if __name__ == "__main__":
    unittest.main()
