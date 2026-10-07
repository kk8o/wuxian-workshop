"""The zero-dependency TrueType writer (wuxianworkshop.core.ttf) against tests/support/fontref.py, the fontTools
version it replaced; fontTools reads both, Pillow's FreeType measures the glyphs when it is there."""
import struct
import unittest
from io import BytesIO

from fontTools.ttLib import TTFont

from tests.support import fontref as reference
from wuxianworkshop.core import ttf

try:
    from PIL import ImageFont, features
    HAVE_FREETYPE = features.check("freetype2")
except ImportError:  # pragma: no cover
    HAVE_FREETYPE = False

TABLES = {"head", "hhea", "maxp", "OS/2", "hmtx", "cmap", "loca", "glyf", "name", "post"}
BASE = 0xE000

# (font name, data) per case: empty, one byte, every byte value, the big.ttf pattern, a stamp packet padded to 64 glyphs
CASES = {
    "empty": ("WFEmpty", b""),
    "one": ("WF1", b"\x7f"),
    "all256": ("WFAll", bytes(range(256))),
    "pattern4096": ("WFBig", reference.pattern(4096)),
    "stamp": ("Stamp12", reference.packet(b"P1-100").ljust(64, b"\0")),
}


def checksum(data):
    """independent re-implementation of the table checksum for the tests"""
    data = bytes(data) + b"\0" * (-len(data) % 4)
    total = 0
    for i in range(0, len(data), 4):
        total = (total + int.from_bytes(data[i:i + 4], "big")) & 0xFFFFFFFF
    return total


def directory(font):
    """[(tag, checksum, offset, length)] from the offset table, parsed with struct only"""
    num_tables = struct.unpack_from(">H", font, 4)[0]
    return [struct.unpack_from(">4sIII", font, 12 + 16 * i) for i in range(num_tables)]


def open_font(data):
    return TTFont(BytesIO(data))


class EquivalenceTests(unittest.TestCase):
    """what the addon measures must be identical for both writers"""

    def test_widths_and_metrics_match_fonttools(self):
        for case, (name, data) in CASES.items():
            with self.subTest(case=case):
                ours, theirs = ttf.build(data, name), reference.build(data, name)
                self.assertEqual(reference.widths(ours), reference.widths(theirs))
                self.assertEqual(reference.metrics(ours), reference.metrics(theirs))
                self.assertEqual(len(reference.widths(ours)), 2 + len(data))

    def test_widths_read_back_to_the_data(self):
        for case, (name, data) in CASES.items():
            with self.subTest(case=case):
                decoded, worst = reference.read(reference.widths(ttf.build(data, name)))
                self.assertEqual(decoded, data)
                self.assertEqual(worst, 0.0)

    def test_accepts_bytearray_and_int_lists(self):
        name, data = CASES["all256"]
        self.assertEqual(ttf.build(bytearray(data), name), ttf.build(data, name))
        self.assertEqual(ttf.build(list(data), name), ttf.build(data, name))


class StructureTests(unittest.TestCase):
    """the font as fontTools reads it"""

    def test_tables_present(self):
        for case, (name, data) in CASES.items():
            with self.subTest(case=case):
                font = open_font(ttf.build(data, name))
                self.assertTrue(TABLES <= set(font.keys()))
                self.assertEqual(set(font.reader.tables), TABLES)

    def test_cmap(self):
        for case, (name, data) in CASES.items():
            with self.subTest(case=case):
                font = open_font(ttf.build(data, name))
                cmap = font.getBestCmap()
                expected = {0x20: 1, **{BASE + i: 2 + i for i in range(2 + len(data))}}
                self.assertEqual({code: font.getGlyphID(glyph) for code, glyph in cmap.items()}, expected)
                subtables = [(t.platformID, t.platEncID, t.format) for t in font["cmap"].tables]
                self.assertEqual(subtables, [(3, 1, 4)])
                raw = font.reader["cmap"]
                self.assertEqual(len(raw), 52)
                seg_count_x2 = struct.unpack_from(">H", raw, 12 + 6)[0]
                self.assertEqual(seg_count_x2, 6)
                end_codes = struct.unpack_from(">3H", raw, 12 + 14)
                self.assertEqual(end_codes, (0x20, BASE + 1 + len(data), 0xFFFF))

    def test_names(self):
        for case, (name, data) in CASES.items():
            with self.subTest(case=case):
                table = open_font(ttf.build(data, name))["name"]
                self.assertEqual(table.getDebugName(1), name)
                self.assertEqual(table.getDebugName(2), "Regular")
                self.assertEqual(table.getDebugName(3), name)
                self.assertEqual(table.getDebugName(4), name)
                self.assertEqual(table.getDebugName(5), "Version 1.0")
                self.assertEqual(table.getDebugName(6), name)
                self.assertEqual(sorted((n.platformID, n.platEncID, n.langID, n.nameID) for n in table.names),
                                 [(3, 1, 0x409, i) for i in range(1, 7)])

    def test_glyphs_and_metrics(self):
        name, data = CASES["all256"]
        font = open_font(ttf.build(data, name))
        order = font.getGlyphOrder()
        self.assertEqual(len(order), 4 + len(data))
        glyf, hmtx = font["glyf"], font["hmtx"]
        box = [(0, 0), (64, 0), (64, 512), (0, 512)]
        for gid, glyph_name in enumerate(order):
            glyph = glyf[glyph_name]
            if gid == 1:  # space
                self.assertEqual(glyph.numberOfContours, 0)
                self.assertEqual(hmtx[glyph_name], (256, 0))
                continue
            self.assertEqual(glyph.numberOfContours, 1)
            self.assertEqual(list(glyph.coordinates), box)
            self.assertEqual(list(glyph.flags), [1, 1, 1, 1])
            self.assertEqual(list(glyph.endPtsOfContours), [3])
            self.assertEqual((glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax), (0, 0, 64, 512))
            if gid == 0:
                self.assertEqual(hmtx[glyph_name], (512, 0))
            else:
                value = [0, 255, *data][gid - 2]
                self.assertEqual(hmtx[glyph_name], ((16 + value) * 16, 0))

    def test_header_fields(self):
        name, data = CASES["pattern4096"]
        ours, theirs = open_font(ttf.build(data, name)), open_font(reference.build(data, name))
        head, hhea, os2, maxp, post = ours["head"], ours["hhea"], ours["OS/2"], ours["maxp"], ours["post"]
        self.assertEqual(head.unitsPerEm, 1024)
        self.assertEqual(head.flags, 0x0003)
        self.assertEqual(head.magicNumber, 0x5F0F3CF5)
        self.assertEqual((head.xMin, head.yMin, head.xMax, head.yMax), (0, 0, 64, 512))
        self.assertEqual(head.indexToLocFormat, 0)
        self.assertEqual((hhea.ascent, hhea.descent, hhea.lineGap), (896, -128, 0))
        self.assertEqual(hhea.numberOfHMetrics, maxp.numGlyphs)
        self.assertEqual(maxp.numGlyphs, 4 + len(data))
        self.assertEqual((maxp.tableVersion, maxp.maxPoints, maxp.maxContours), (0x10000, 4, 1))
        self.assertEqual(os2.version, 4)
        self.assertEqual((os2.sTypoAscender, os2.sTypoDescender, os2.usWinAscent, os2.usWinDescent), (896, -128, 896, 128))
        self.assertEqual((os2.usFirstCharIndex, os2.usLastCharIndex), (0x20, BASE + 1 + len(data)))
        self.assertEqual(post.formatType, 3.0)
        # the computed hhea extremes match what fontTools derives from the same glyphs
        for field in ("advanceWidthMax", "minLeftSideBearing", "minRightSideBearing", "xMaxExtent"):
            self.assertEqual(getattr(hhea, field), getattr(theirs["hhea"], field), field)
        self.assertEqual(os2.xAvgCharWidth, theirs["OS/2"].xAvgCharWidth)
        self.assertEqual((os2.ulUnicodeRange1, os2.ulUnicodeRange2), (1, 1 << 28))

    def test_checksums_alignment_and_order(self):
        for case, (name, data) in CASES.items():
            with self.subTest(case=case):
                font = ttf.build(data, name)
                reader = open_font(font).reader
                entries = directory(font)
                self.assertEqual(struct.unpack_from(">IHHHH", font, 0), (0x00010000, 10, 128, 3, 32))
                self.assertEqual([e[0] for e in entries], sorted(e[0] for e in entries))
                self.assertEqual(entries[0][0], b"OS/2")
                end = 12 + 16 * len(entries)
                head_offset = None
                for tag, stored, offset, length in sorted(entries, key=lambda e: e[2]):
                    self.assertEqual(offset % 4, 0, tag)
                    self.assertEqual(offset, end, tag)                      # tables are contiguous with padding only
                    table = font[offset:offset + length]
                    if tag == b"head":
                        head_offset = offset
                        table = table[:8] + bytes(4) + table[12:]            # checkSumAdjustment counts as 0
                    self.assertEqual(stored, checksum(table), tag)
                    self.assertEqual(stored, reader.tables[tag.decode()].checkSum, tag)
                    end = offset + length + (-length % 4)
                    self.assertEqual(font[offset + length:end], bytes(-length % 4), tag)   # zero padding
                self.assertEqual(end, len(font))
                adjustment = struct.unpack_from(">I", font, head_offset + 8)[0]
                zeroed = font[:head_offset + 8] + bytes(4) + font[head_offset + 12:]
                self.assertEqual(adjustment, (0xB1B0AFBA - checksum(zeroed)) & 0xFFFFFFFF)
                self.assertEqual(checksum(font), 0xB1B0AFBA)
                self.assertEqual(open_font(font)["head"].checkSumAdjustment, adjustment)


class LocaTests(unittest.TestCase):
    def test_long_loca_when_glyf_exceeds_short_range(self):
        data = bytes(k & 0xFF for k in range(6000))                        # 6003 box glyphs * 22 bytes > 0x1FFFE
        ours = ttf.build(data, "WFLong")
        font = open_font(ours)
        self.assertEqual(font["head"].indexToLocFormat, 1)
        self.assertEqual(reference.widths(ours), reference.widths(reference.build(data, "WFLong")))
        self.assertEqual(reference.read(reference.widths(ours))[0], data)

    def test_loca_format_switches_at_the_limit(self):
        # box glyphs (.notdef + 2 + len(data)) are 22 bytes each; short offsets (offset / 2 in 16 bits) reach 0x1FFFE
        # bytes, i.e. 5957 box glyphs = 5954 data bytes
        font = ttf.build(bytes(5954), "WFEdge")
        short = open_font(font)
        self.assertEqual((short["head"].indexToLocFormat, len(short.reader["glyf"])), (0, 5957 * 22))
        self.assertEqual(reference.read(reference.widths(font))[0], bytes(5954))
        long = open_font(ttf.build(bytes(5955), "WFEdge"))
        self.assertEqual((long["head"].indexToLocFormat, len(long.reader["glyf"])), (1, 5958 * 22))

    def test_too_many_glyphs_rejected(self):
        ttf.build(bytes(ttf.MAX_DATA), "WFMax")
        with self.assertRaises(ValueError):
            ttf.build(bytes(ttf.MAX_DATA + 1), "WFMax")


class ReproducibilityTests(unittest.TestCase):
    def test_deterministic(self):
        for case, (name, data) in CASES.items():
            with self.subTest(case=case):
                self.assertEqual(ttf.build(data, name), ttf.build(data, name))

    def test_size_against_fonttools(self):
        name, data = CASES["pattern4096"]
        ours, theirs = len(ttf.build(data, name)), len(reference.build(data, name))
        self.assertLessEqual(ours, 1.5 * theirs, f"hand-written {ours} bytes, fontTools {theirs} bytes")


@unittest.skipUnless(HAVE_FREETYPE, "Pillow with FreeType is not installed")
class FreeTypeTests(unittest.TestCase):
    """the real renderer: Pillow's bundled FreeType loads the font and reports the scaled advances"""

    def test_advances_at_64px(self):
        for case, (name, data) in CASES.items():
            with self.subTest(case=case):
                font = ImageFont.truetype(BytesIO(ttf.build(data, name)), 64)
                values = [0, 255, *data]
                self.assertEqual([font.getlength(chr(BASE + i)) for i in range(len(values))], [16 + v for v in values])
                self.assertEqual(font.getname(), (name, "Regular"))

    def test_glyph_with_tail_like_fontprobe(self):
        name, data = CASES["stamp"]
        font = ImageFont.truetype(BytesIO(ttf.build(data, name)), 64)
        tail = chr(BASE)
        rights = [float(font.getbbox(chr(BASE + i) + tail)[2]) for i in range(2 + len(data))]
        self.assertEqual(reference.read(rights), (data, 0.0))

    def test_long_loca_loads(self):
        data = bytes(k & 0xFF for k in range(6000))
        font = ImageFont.truetype(BytesIO(ttf.build(data, "WFLong")), 64)
        self.assertEqual([font.getlength(chr(BASE + i)) for i in range(6002)], [16 + v for v in [0, 255, *data]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
