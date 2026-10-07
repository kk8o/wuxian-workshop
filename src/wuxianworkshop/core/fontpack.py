"""Font packets: bytes carried by the advance widths of a TrueType font (the font return channel, experiment E0f).

The encoding: 1024 units per em and an advance of (16 + byte) * 16 units,
so at font size 64 on a pixel-exact frame byte b measures 16 + b pixels. Glyphs U+E000 and U+E001 calibrate byte values 0
and 255, data byte k is glyph U+E002 + k. A packet is "WF", the payload length (2 bytes, big-endian), the payload and the
CRC-16/CCITT-FALSE of everything before it. addon/WoWBridge/FontProbe.lua measures the glyphs and reads it back.
"""
from . import ttf
from .frame import crc16

UPEM = 1024
BASE = 0xE000
MAGIC = b"WF"
STAMP_GLYPHS = 64     # data glyphs of a stamp font: a payload of up to 58 bytes


def advance(v):
    return (16 + v) * 16


def build(data, name):
    """TTF bytes with the two calibration glyphs and one glyph per byte of `data`; `name` is the family, full, unique and
    PostScript name (letters and digits). Written by ttf.py (no fontTools); tests/support/fontref.py keeps the fontTools
    version it must match"""
    return ttf.build(data, name)


def packet(payload):
    body = MAGIC + len(payload).to_bytes(2, "big") + payload
    return body + crc16(body).to_bytes(2, "big")


def unpacket(data):
    """payload of a packet at the start of `data`, or None"""
    if data[:2] != MAGIC or len(data) < 6:
        return None
    n = int.from_bytes(data[2:4], "big")
    if len(data) < 6 + n or crc16(data[:4 + n]) != int.from_bytes(data[4 + n:6 + n], "big"):
        return None
    return bytes(data[4:4 + n])


def stamp_font(text, name, glyphs=STAMP_GLYPHS):
    p = packet(text.encode())
    if len(p) > glyphs:
        raise ValueError(f"stamp {text!r} needs {len(p)} glyphs, the font has {glyphs}")
    return build(p + bytes(glyphs - len(p)), name)


def pattern(n):
    """the test bytes of big.ttf (FontProbe.lua checks the same formula)"""
    return bytes((k * 37 + 11) % 256 for k in range(n))


def widths(font, size=64):
    """what GetStringWidth should report for glyph U+E000 + i at `size` on a pixel-exact frame (tests and tools: fontTools)"""
    from io import BytesIO
    from fontTools.ttLib import TTFont
    f = TTFont(BytesIO(font))
    cmap, hmtx, upem = f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm
    out = []
    while BASE + len(out) in cmap:
        out.append(hmtx[cmap[BASE + len(out)]][0] * size / upem)
    return out


def metrics(font):
    """(advance, ink right edge) in font units of glyph U+E000 + i, and units per em (the mock client in tests: fontTools)"""
    from io import BytesIO
    from fontTools.ttLib import TTFont
    f = TTFont(BytesIO(font))
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
