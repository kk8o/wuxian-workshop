"""Zero-dependency TrueType writer for the font return channel (replaces fontTools' FontBuilder in fontpack.py).

The font carries bytes in glyph advance widths: 1024 units per em, glyph order `.notdef`, `space`, `d0` .. `dN`, with
d_i advancing (16 + v_i) * 16 units for the value sequence [0, 255] + data (the first two calibrate). U+0020 maps to
`space`, U+E000 + i to d_i. Every d_i and `.notdef` is the same 64 x 512 box, so a glyph's ink is constant and only its
advance differs; FreeType in the game client reports the advance, addon/WoWBridge/FontProbe.lua reads the byte back.

Ten tables: head, hhea, maxp, OS/2 (v4), hmtx, cmap (one (3,1) format 4 subtable), loca, glyf, name, post (v3.0).
Output is byte-reproducible: the head timestamps are constants.
"""
import struct

UPEM = 1024
BASE = 0xE000
ASCENT, DESCENT = 896, -128
BOX = ((0, 0), (64, 0), (64, 512), (0, 512))          # one contour of on-curve points, counter-clockwise like fontTools
MAX_DATA = 0xFFFF - BASE - 2                          # 8189: U+FFFF is the format 4 end marker, code points stop before it
TIMESTAMP = 3850070400                                # 2026-01-01T00:00:00Z as seconds since 1904-01-01 (head.created/modified)
VENDOR = b"WXWS"                                      # OS/2 achVendID

# head, hhea, maxp first, then the rest in the order the OpenType spec recommends for TrueType outlines
_LAYOUT = [b"head", b"hhea", b"maxp", b"OS/2", b"hmtx", b"cmap", b"loca", b"glyf", b"name", b"post"]

# simple glyph flag bits
_ON_CURVE, _X_SHORT, _Y_SHORT, _X_SAME_OR_POSITIVE, _Y_SAME_OR_POSITIVE = 0x01, 0x02, 0x04, 0x10, 0x20


def advance(v):
    return (16 + v) * 16


def build(data, name):
    """TTF bytes with the two calibration glyphs and one glyph per byte of `data`; `name` is the family, full, unique and
    PostScript name (letters and digits)"""
    values = [0, 255] + list(bytes(data))
    if len(values) - 2 > MAX_DATA:
        raise ValueError(f"{len(values) - 2} data bytes need code points past U+FFFE (at most {MAX_DATA})")
    box = _simple_glyph([BOX])
    bbox = _bbox([BOX])
    # glyph order: .notdef, space, d0 .. dN
    glyphs = [box, b""] + [box] * len(values)
    bboxes = [bbox, None] + [bbox] * len(values)
    advances = [512, 256] + [advance(v) for v in values]
    glyf, loca, loca_format = _glyf_loca(glyphs)
    last_char = BASE + len(values) - 1
    tables = {
        b"head": _head(loca_format, bbox),
        b"hhea": _hhea(advances, bboxes),
        b"maxp": _maxp(len(glyphs)),
        b"OS/2": _os2(advances, last_char),
        b"hmtx": _hmtx(advances),
        b"cmap": _cmap([(0x20, 0x20, 1), (BASE, last_char, 2)]),
        b"loca": loca,
        b"glyf": glyf,
        b"name": _name(name),
        b"post": _post(),
    }
    return _sfnt(tables)


# ---------------------------------------------------------------------------------------------------------------- sfnt

def checksum(data):
    """the OpenType table checksum: sum of big-endian uint32 words, the data padded with zeros to a multiple of 4"""
    data = bytes(data) + b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(data) // 4}I", data)) & 0xFFFFFFFF


def _sfnt(tables):
    """the font file: offset table, directory sorted by tag, each table 4-byte aligned, head.checkSumAdjustment patched"""
    tags = sorted(tables)
    order = [t for t in _LAYOUT if t in tables] + [t for t in tags if t not in _LAYOUT]
    n = len(tables)
    entry_selector = n.bit_length() - 1                 # floor(log2(n))
    search_range = 16 << entry_selector                 # (maximum power of 2 <= n) * 16
    header = struct.pack(">IHHHH", 0x00010000, n, search_range, entry_selector, 16 * n - search_range)
    offsets, body, offset = {}, [], 12 + 16 * n
    for tag in order:
        offsets[tag] = offset
        padded = tables[tag] + b"\0" * (-len(tables[tag]) % 4)
        body.append(padded)
        offset += len(padded)
    directory = b"".join(struct.pack(">4sIII", tag, checksum(tables[tag]), offsets[tag], len(tables[tag])) for tag in tags)
    font = bytearray(header + directory + b"".join(body))
    # the head table was packed with checkSumAdjustment = 0, so its directory checksum and the whole-file sum are the
    # ones the spec asks for
    struct.pack_into(">I", font, offsets[b"head"] + 8, (0xB1B0AFBA - checksum(font)) & 0xFFFFFFFF)
    return bytes(font)


# -------------------------------------------------------------------------------------------------------------- glyphs

def _bbox(contours):
    xs = [x for c in contours for x, _ in c]
    ys = [y for c in contours for _, y in c]
    return min(xs), min(ys), max(xs), max(ys)


def _delta(d, short_bit, same_or_positive_bit, out):
    """append one coordinate delta in its shortest TrueType form, return the flag bits describing it"""
    if d == 0:
        return same_or_positive_bit                     # no bytes: same as the previous point
    if -255 <= d <= 255:
        out.append(abs(d))                              # one unsigned byte, the sign in the flag
        return short_bit | (same_or_positive_bit if d > 0 else 0)
    out += struct.pack(">h", d)                         # two signed bytes
    return 0


def _simple_glyph(contours):
    """a TrueType simple glyph of on-curve points without instructions; `contours` is a list of point lists"""
    ends, count = [], 0
    for c in contours:
        count += len(c)
        ends.append(count - 1)
    flags, xs, ys = bytearray(), bytearray(), bytearray()
    px = py = 0
    for x, y in (p for c in contours for p in c):
        f = _ON_CURVE
        f |= _delta(x - px, _X_SHORT, _X_SAME_OR_POSITIVE, xs)
        f |= _delta(y - py, _Y_SHORT, _Y_SAME_OR_POSITIVE, ys)
        flags.append(f)
        px, py = x, y
    header = struct.pack(">h4h", len(contours), *_bbox(contours))
    return header + struct.pack(f">{len(ends)}HH", *ends, 0) + bytes(flags) + bytes(xs) + bytes(ys)


def _glyf_loca(glyphs):
    """glyf and loca tables plus head.indexToLocFormat; glyph records are padded to even lengths so that the short loca
    format (offset / 2) can be used while the table stays under 128 KiB, long offsets otherwise"""
    offsets, data = [], bytearray()
    for g in glyphs:
        offsets.append(len(data))
        data += g
        if len(data) & 1:
            data.append(0)
    offsets.append(len(data))
    if offsets[-1] <= 2 * 0xFFFF:
        return bytes(data), struct.pack(f">{len(offsets)}H", *(o // 2 for o in offsets)), 0
    return bytes(data), struct.pack(f">{len(offsets)}I", *offsets), 1


# -------------------------------------------------------------------------------------------------------------- tables

def _head(loca_format, bbox):
    head = struct.pack(">IIIIHHqq4hHHhhh",
                       0x00010000,                      # version 1.0
                       0x00010000,                      # fontRevision 1.0
                       0,                               # checkSumAdjustment, patched by _sfnt
                       0x5F0F3CF5,                      # magicNumber
                       0x0003,                          # flags: baseline at y = 0, left sidebearing at x = 0 (not bit 3)
                       UPEM,
                       TIMESTAMP, TIMESTAMP,            # created, modified
                       *bbox,                           # xMin, yMin, xMax, yMax over all glyphs
                       0,                               # macStyle
                       3,                               # lowestRecPPEM
                       2,                               # fontDirectionHint (deprecated, 2)
                       loca_format,                     # indexToLocFormat
                       0)                               # glyphDataFormat
    assert len(head) == 54
    return head


def _hhea(advances, bboxes):
    inked = [(a, b) for a, b in zip(advances, bboxes) if b is not None]   # bearings count glyphs with contours only
    hhea = struct.pack(">I hhh H hhh hhh hhhh h H",
                       0x00010000,
                       ASCENT, DESCENT, 0,                                             # ascender, descender, lineGap
                       max(advances),                                                  # advanceWidthMax
                       min(b[0] for _, b in inked),                                    # minLeftSideBearing (= xMin, lsb 0)
                       min(a - b[2] for a, b in inked),                                # minRightSideBearing
                       max(b[2] for _, b in inked),                                    # xMaxExtent (lsb + xMax - xMin)
                       1, 0, 0,                                                        # caretSlopeRise/Run, caretOffset
                       0, 0, 0, 0,                                                     # reserved
                       0,                                                              # metricDataFormat
                       len(advances))                                                  # numberOfHMetrics: one per glyph
    assert len(hhea) == 36
    return hhea


def _maxp(num_glyphs):
    maxp = struct.pack(">I14H",
                       0x00010000, num_glyphs,
                       4, 1,                            # maxPoints, maxContours: the box
                       0, 0,                            # maxCompositePoints, maxCompositeContours
                       2, 0,                            # maxZones, maxTwilightPoints
                       0, 0, 0, 0, 0,                   # maxStorage, maxFunctionDefs, maxInstructionDefs, maxStackElements, maxSizeOfInstructions
                       0, 0)                            # maxComponentElements, maxComponentDepth
    assert len(maxp) == 32
    return maxp


def _os2(advances, last_char):
    widths = [a for a in advances if a]
    os2 = struct.pack(">H h HHH 10h h 10s IIII 4s H HH hhh HH II hh HHH",
                      4,                                              # version
                      (2 * sum(widths) + len(widths)) // (2 * len(widths)),  # xAvgCharWidth: rounded mean of non-zero advances
                      400, 5, 0,                                      # usWeightClass normal, usWidthClass medium, fsType installable
                      665, 716, 0, 143, 665, 716, 0, 491, 51, 266,    # sub/superscript sizes and offsets, strikeout size/position
                      0,                                              # sFamilyClass
                      bytes(10),                                      # panose: any
                      1, 1 << 28, 0, 0,                               # ulUnicodeRange: bit 0 Basic Latin, bit 60 Private Use Area
                      VENDOR,
                      0x0040,                                         # fsSelection: REGULAR
                      0x20, last_char,                                # usFirstCharIndex, usLastCharIndex
                      ASCENT, DESCENT, 0,                             # sTypoAscender, sTypoDescender, sTypoLineGap
                      ASCENT, -DESCENT,                               # usWinAscent, usWinDescent
                      0, 0,                                           # ulCodePageRange1, ulCodePageRange2
                      0, 0,                                           # sxHeight, sCapHeight
                      0, 0x20, 0)                                     # usDefaultChar, usBreakChar, usMaxContext
    assert len(os2) == 96
    return os2


def _hmtx(advances):
    return b"".join(struct.pack(">Hh", a, 0) for a in advances)      # advanceWidth, lsb 0 for every glyph


def _cmap(ranges):
    """one (3,1) format 4 subtable; `ranges` are (first code, last code, glyph id of the first code) in ascending order;
    the 0xFFFF end segment maps to glyph 0"""
    segments = [(start, end, (gid - start) & 0xFFFF) for start, end, gid in ranges] + [(0xFFFF, 0xFFFF, 1)]
    n = len(segments)
    entry_selector = n.bit_length() - 1
    search_range = 2 << entry_selector                  # 2 * (maximum power of 2 <= segCount)
    sub = struct.pack(">7H", 4, 16 + 8 * n, 0, 2 * n, search_range, entry_selector, 2 * n - search_range)
    sub += struct.pack(f">{n}H", *(end for _, end, _ in segments)) + struct.pack(">H", 0)      # endCode[], reservedPad
    sub += struct.pack(f">{n}H", *(start for start, _, _ in segments))                        # startCode[]
    sub += struct.pack(f">{n}H", *(delta for _, _, delta in segments))                        # idDelta[]
    sub += struct.pack(f">{n}H", *([0] * n))                                                   # idRangeOffset[]
    return struct.pack(">HH HHI", 0, 1, 3, 1, 12) + sub   # version, numTables; platform 3, encoding 1, subtable offset


def _name(name):
    strings = {1: name, 2: "Regular", 3: name, 4: name, 5: "Version 1.0", 6: name}
    pool, where, records = bytearray(), {}, []
    for nid in sorted(strings):                        # records sorted by (platform, encoding, language, name id)
        text = strings[nid].encode("utf-16-be")
        if text not in where:
            where[text] = len(pool)
            pool += text
        records.append(struct.pack(">6H", 3, 1, 0x0409, nid, len(text), where[text]))
    return struct.pack(">HHH", 0, len(records), 6 + 12 * len(records)) + b"".join(records) + bytes(pool)


def _post():
    post = struct.pack(">IIhhIIIII", 0x00030000, 0, 0, 0, 0, 0, 0, 0, 0)   # version 3.0: no glyph names
    assert len(post) == 32
    return post
