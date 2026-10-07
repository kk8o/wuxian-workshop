"""The fontTools version of the font packets, copied from host/wowbridge/fontpack.py (with crc16 from frame.py) so that
the tests can compare the hand-written writer against it. Test-only: fontTools is not a dependency of ttf.py."""
"""The fontTools version of the mailbox font builder, kept for tests: wuxianworkshop.core.ttf must build fonts that
read back the same way (tests/core/test_ttf.py), and the mock client in wowmock.py reads fonts through fontTools."""
from io import BytesIO

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

UPEM = 1024
BASE = 0xE000
MAGIC = b"WF"


def crc16(data):
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)"""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def advance(v):
    return (16 + v) * 16


def _box():
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((64, 0))
    pen.lineTo((64, 512))
    pen.lineTo((0, 512))
    pen.closePath()
    return pen.glyph()


def build(data, name):
    """TTF bytes with the two calibration glyphs and one glyph per byte of `data`; `name` is the family, full, unique and
    PostScript name (letters and digits)"""
    values = [0, 255] + list(data)
    names = [f"d{i}" for i in range(len(values))]
    fb = FontBuilder(UPEM, isTTF=True)
    fb.setupGlyphOrder([".notdef", "space"] + names)
    fb.setupCharacterMap({0x20: "space", **{BASE + i: n for i, n in enumerate(names)}})
    fb.setupGlyf({".notdef": _box(), "space": TTGlyphPen(None).glyph(), **{n: _box() for n in names}})
    fb.setupHorizontalMetrics({".notdef": (512, 0), "space": (256, 0), **{n: (advance(v), 0) for n, v in zip(names, values)}})
    fb.setupHorizontalHeader(ascent=896, descent=-128)
    fb.setupNameTable(dict(familyName=name, styleName="Regular", uniqueFontIdentifier=name, fullName=name, psName=name,
                           version="Version 1.0"))
    fb.setupOS2(sTypoAscender=896, sTypoDescender=-128, usWinAscent=896, usWinDescent=128)
    fb.setupPost()
    buf = BytesIO()
    fb.save(buf)
    return buf.getvalue()


def packet(payload):
    body = MAGIC + len(payload).to_bytes(2, "big") + payload
    return body + crc16(body).to_bytes(2, "big")


def pattern(n):
    """the test bytes of big.ttf (FontProbe.lua checks the same formula)"""
    return bytes((k * 37 + 11) % 256 for k in range(n))


def widths(ttf, size=64):
    """what GetStringWidth should report for glyph U+E000 + i at `size` on a pixel-exact frame (for tests)"""
    f = TTFont(BytesIO(ttf))
    cmap, hmtx, upem = f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm
    out = []
    while BASE + len(out) in cmap:
        out.append(hmtx[cmap[BASE + len(out)]][0] * size / upem)
    return out


def metrics(ttf):
    """(advance, ink right edge) in font units of glyph U+E000 + i, and units per em (for the mock client in tests)"""
    f = TTFont(BytesIO(ttf))
    cmap, hmtx, glyf = f.getBestCmap(), f["hmtx"], f["glyf"]
    out = []
    while BASE + len(out) in cmap:
        name = cmap[BASE + len(out)]
        out.append((hmtx[name][0], getattr(glyf[name], "xMax", 0)))
    return out, f["head"].unitsPerEm


def read(ws):
    """inverse of build: measured widths -> (data bytes, worst distance from a whole step)"""
    step = (ws[1] - ws[0]) / 255
    vals = [(w - ws[0]) / step for w in ws[2:]]
    return bytes(int(round(v)) for v in vals), max((abs(v - round(v)) for v in vals), default=0.0)
