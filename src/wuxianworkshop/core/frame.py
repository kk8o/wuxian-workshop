"""WoWBridge frame format v1: layout, codec and frame builder.

This module is the reference for the format; addon/WoWBridge/Codec.lua and Frame.lua implement the same thing in Lua.

Grid: W x H cells (W >= 24, H >= 8), each cell c x c physical pixels, surrounded by a 1-cell black quiet zone.
Fixed cells (x = column, y = row, both 0-based):
  finders   3x3 at top-left, top-right and bottom-left: white ring, magenta core
  timing    row 0 between the top finders and column 0 between the left finders, black/white alternating, black first
  control   row 1, x = 3..13: 2 mode cells (black = 0, white = 1, high bit first), 1 toggle cell (flips on every new frame),
            8 calibration cells K W R G B C M Y
Every other cell is a data cell, in row-major order. The data cells carry one bit stream, most significant bit first:
  header (15 bytes) | payload | CRC-32 of header + payload (4 bytes, big-endian) | padding (zero bits)
A probe frame (type 1) continues after that stream, from the next whole cell, with literal colours: 256-level ramps of
red, green, blue and grey (1024 cells), then the 8 palette colours repeating.

Cell values in modes 0 and 1 are palette indices with red = bit 2, green = bit 1, blue = bit 0.
"""
import zlib

MAGIC = b"WB"
VERSION = 1
TYPE_DATA, TYPE_PROBE, TYPE_CONTROL = 0, 1, 2     # control: HELLO, HB, PONG (the v0.5 link)
HEADER_LEN = 15
BITS_PER_CELL = {0: 1, 1: 3, 2: 6}

BLACK, BLUE, GREEN, CYAN, RED, MAGENTA, YELLOW, WHITE = range(8)
CALIBRATION = (BLACK, WHITE, RED, GREEN, BLUE, CYAN, MAGENTA, YELLOW)   # K W R G B C M Y
CONTROL_X0 = 3                     # row 1: mode cells at x = 3, 4; toggle at 5; calibration at 6..13
MIN_W, MIN_H = 24, 8


def palette_rgb(v):
    """RGB (0..255) of palette index v"""
    return (255 if v & 4 else 0, 255 if v & 2 else 0, 255 if v & 1 else 0)


def level_rgb(v):
    """RGB of a mode-2 cell value (6 bits: 2 per channel, 4 levels 0 / 85 / 170 / 255)"""
    return ((v >> 4 & 3) * 85, (v >> 2 & 3) * 85, (v & 3) * 85)


# ---------------------------------------------------------------- layout
_LAYOUTS = {}

def layout(W, H):
    """(fixed, order): fixed maps (x, y) to ('const', palette) | ('mode', bit) | ('toggle',); order lists the data cells"""
    if W < MIN_W or H < MIN_H or W > 255 or H > 255:
        raise ValueError(f"grid {W}x{H} outside {MIN_W}..255 x {MIN_H}..255")
    key = (W, H)
    if key not in _LAYOUTS:
        fixed = {}
        for fx, fy in ((0, 0), (W - 3, 0), (0, H - 3)):
            for dy in range(3):
                for dx in range(3):
                    fixed[(fx + dx, fy + dy)] = ("const", MAGENTA if (dx, dy) == (1, 1) else WHITE)
        for x in range(3, W - 3):
            fixed[(x, 0)] = ("const", BLACK if (x - 3) % 2 == 0 else WHITE)
        for y in range(3, H - 3):
            fixed[(0, y)] = ("const", BLACK if (y - 3) % 2 == 0 else WHITE)
        fixed[(CONTROL_X0, 1)] = ("mode", 1)
        fixed[(CONTROL_X0 + 1, 1)] = ("mode", 0)
        fixed[(CONTROL_X0 + 2, 1)] = ("toggle",)
        for i, v in enumerate(CALIBRATION):
            fixed[(CONTROL_X0 + 3 + i, 1)] = ("const", v)
        order = [(x, y) for y in range(H) for x in range(W) if (x, y) not in fixed]
        _LAYOUTS[key] = (fixed, order)
    return _LAYOUTS[key]


def capacity(W, H, mode):
    """largest payload (bytes) a data frame of this size carries"""
    bits = len(layout(W, H)[1]) * BITS_PER_CELL[mode]
    return max(0, (bits - 8 * (HEADER_LEN + 4)) // 8)


# ---------------------------------------------------------------- codec
def crc16(data):
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)"""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc32(data):
    return zlib.crc32(bytes(data)) & 0xFFFFFFFF


def pack(data, bits):
    """bytes -> cell values of `bits` bits each, most significant bit first; the last cell is padded with zeros"""
    cells, acc, n = [], 0, 0
    for b in data:
        acc = (acc << 8) | b
        n += 8
        while n >= bits:
            n -= bits
            cells.append((acc >> n) & ((1 << bits) - 1))
            acc &= (1 << n) - 1
    if n:
        cells.append((acc << (bits - n)) & ((1 << bits) - 1))
    return cells


def unpack(cells, bits, nbytes=None):
    """cell values -> bytes (whole bytes only; stops after nbytes when given)"""
    out, acc, n = bytearray(), 0, 0
    for v in cells:
        acc = (acc << bits) | v
        n += bits
        while n >= 8:
            n -= 8
            out.append((acc >> n) & 0xFF)
            acc &= (1 << n) - 1
            if nbytes is not None and len(out) >= nbytes:
                return bytes(out)
    return bytes(out)


def header(ftype, W, H, session, msg, part, parts, length):
    h = bytes([MAGIC[0], MAGIC[1], (VERSION << 4) | ftype, W, H, session >> 8 & 0xFF, session & 0xFF,
               msg >> 8 & 0xFF, msg & 0xFF, part, parts, length >> 8 & 0xFF, length & 0xFF])
    c = crc16(h)
    return h + bytes([c >> 8, c & 0xFF])


def parse_header(h):
    """dict of the header fields, or None when the magic, version or CRC-16 is wrong"""
    if len(h) < HEADER_LEN or h[:2] != MAGIC or h[2] >> 4 != VERSION or crc16(h[:13]) != (h[13] << 8 | h[14]):
        return None
    return dict(type=h[2] & 0x0F, W=h[3], H=h[4], session=h[5] << 8 | h[6], msg=h[7] << 8 | h[8], part=h[9], parts=h[10],
                length=h[11] << 8 | h[12])


def stream(ftype, W, H, session, msg, payload, part=0, parts=1):
    """header + payload + CRC-32"""
    body = header(ftype, W, H, session, msg, part, parts, len(payload)) + bytes(payload)
    c = crc32(body)
    return body + c.to_bytes(4, "big")


def ramp_rgb(i):
    """literal colour of the i-th cell after a probe frame's bit stream"""
    if i < 1024:
        ch, lv = divmod(i, 256)
        return ((lv, 0, 0), (0, lv, 0), (0, 0, lv), (lv, lv, lv))[ch]
    return palette_rgb((i - 1024) % 8)


# ---------------------------------------------------------------- build
def build(W, H, mode, toggle, ftype, session, msg, payload, part=0, parts=1):
    """the whole frame as {(x, y): (r, g, b)} with 0..255 channels; raises ValueError when the payload does not fit"""
    fixed, order = layout(W, H)
    bits = BITS_PER_CELL[mode]
    cells = pack(stream(ftype, W, H, session, msg, payload, part, parts), bits)
    if len(cells) > len(order):
        raise ValueError(f"payload of {len(payload)} bytes does not fit a {W}x{H} mode-{mode} frame (max {capacity(W, H, mode)})")
    out = {}
    for xy, role in fixed.items():
        if role[0] == "const":
            out[xy] = palette_rgb(role[1])
        elif role[0] == "mode":
            out[xy] = palette_rgb(WHITE if mode >> role[1] & 1 else BLACK)
        else:
            out[xy] = palette_rgb(WHITE if toggle else BLACK)
    to_rgb = level_rgb if mode == 2 else (lambda v: palette_rgb(WHITE if v else BLACK)) if mode == 0 else palette_rgb
    for i, xy in enumerate(order):
        if i < len(cells):
            out[xy] = to_rgb(cells[i])
        elif ftype == TYPE_PROBE:
            out[xy] = ramp_rgb(i - len(cells))
        else:
            out[xy] = palette_rgb(BLACK)
    return out
