"""Font packets (experiment E0f): the fonts the companion writes and reading them back from glyph widths."""
import unittest
from io import BytesIO

from fontTools.ttLib import TTFont

from wuxianworkshop.core import fontpack as P


class FontPack(unittest.TestCase):
    def test_levels(self):
        ttf = P.build(bytes(range(256)), "WBLevels")
        ws = P.widths(ttf)
        self.assertEqual((ws[0], ws[1]), (16.0, 271.0))       # bytes 0 and 255 at size 64: 16 + b pixels
        data, worst = P.read(ws)
        self.assertEqual(data, bytes(range(256)))
        self.assertEqual(worst, 0.0)

    def test_stamp_roundtrip(self):
        for size in (64, 65):
            ws = P.widths(P.stamp_font("L1-12345", "WBLate"), size)
            self.assertEqual(len(ws), 2 + P.STAMP_GLYPHS)
            self.assertEqual(P.unpacket(P.read(ws)[0]), b"L1-12345")

    def test_packet_checks(self):
        p = P.packet(b"hello")
        self.assertEqual(P.unpacket(p + bytes(10)), b"hello")
        self.assertIsNone(P.unpacket(p[:-1] + bytes([p[-1] ^ 1])))
        self.assertIsNone(P.unpacket(bytes(64)))
        with self.assertRaises(ValueError):
            P.stamp_font("x" * 59, "WBTooLong")

    def test_big(self):
        ttf = P.build(P.pattern(4096), "WBBig")
        self.assertLess(len(ttf), 200_000)
        self.assertEqual(P.read(P.widths(ttf))[0], P.pattern(4096))

    def test_tables_and_names(self):
        f = TTFont(BytesIO(P.build(b"\x01\x02", "WBLateB2")))
        for tag in ("head", "hhea", "maxp", "OS/2", "name", "cmap", "post", "glyf", "loca", "hmtx"):
            self.assertIn(tag, f)
        self.assertEqual(f["name"].getDebugName(1), "WBLateB2")
        self.assertEqual(f["name"].getDebugName(6), "WBLateB2")
        self.assertEqual(f["maxp"].numGlyphs, 2 + 2 + 2)


if __name__ == "__main__":
    unittest.main()
