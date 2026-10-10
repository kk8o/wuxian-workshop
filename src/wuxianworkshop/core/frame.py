"""WoWBridge frame format v1: layout, codec and frame builder.

This module is the reference for the format; addon/WoWBridge/Codec.lua and Frame.lua implement the same thing in Lua.

Grid: W x H cells (W >= 24, H >= 8), each cell c x c physical pixels, surrounded by a 1-cell black quiet zone.
Fixed cells (x = column, y = row, both 0-based):
  finders   3x3 at top-left, top-right and bottom-left: white ring, magenta core
  timing    row 0 between the top finders and column 0 between the left finders, black/white alternating, black first
  control   row 1, x = 3..13: 2 mode cells (black = 0, white = 1, high bit first), 1 toggle cell (flips on every new frame),
            8 calibration cells K W R G B C M Y
Every other cell is a data cell, in row-major order (mode 3: in inf_order). The data cells carry one bit stream, most
significant bit first:
  header (15 bytes) | payload | CRC-32 of header + payload (4 bytes, big-endian) | padding (zero bits)
A probe frame (type 1) continues after that stream, from the next whole cell, with literal colours: 256-level ramps of
red, green, blue and grey (1024 cells), then the 8 palette colours repeating.

Cell values in modes 0, 1 and 3 are palette indices with red = bit 2, green = bit 1, blue = bit 0. Mode 3 (0.9.9) is
mode 1 with the data cells in inf_order: nearest first to an ∞ in the middle of the frame, so a short message's cells
gather into an ∞ on the black padding and a long one fills the frame as in mode 1; it carries as much.
"""
import math
import zlib

MAGIC = b"WB"
VERSION = 1
TYPE_DATA, TYPE_PROBE, TYPE_CONTROL = 0, 1, 2     # control: HELLO, HB, PONG (the v0.5 link)
HEADER_LEN = 15
BITS_PER_CELL = {0: 1, 1: 3, 2: 6, 3: 3}

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


# ---------------------------------------------------------------- mode 3: the data cells along ∞
INF_STEPS = 128                    # points on a quarter of the curve
INF_MARGIN = (2.5, 2.0)            # cells between the curve and the frame's sides, and its top and bottom
INF_YMAX = 0.35355339059327373     # the lemniscate's half height for a half width of 1: 1 / (2 sqrt 2)
INF_STRETCH = 1.6                  # how much wider than its own proportions the ∞ may be drawn to fill a wide frame
_INF = {}


def inf_order(W, H):
    """the data cells in mode 3's order, [(x, y)]: nearest first to Bernoulli's lemniscate centred in the frame, as high as
    the frame allows (its half height (H - 1) / 2 - 2: scale ay = that / INF_YMAX) and as wide as the frame allows up to
    INF_STRETCH times its own proportions (scale ax = min((W - 1) / 2 - 2.5, ay * INF_STRETCH)). A quarter of the curve
    is sampled as (ax (s + s^3), ay (s - s^3)) / (1 + s^4) for s = k / 128, k = 0..128 (the rest is its mirror image);
    a cell's distance (squared) is the least to those points, searched outwards from the first point at or right of
    it, and the cells go by floor(distance * 4096) * 65536 + their row-major position (y * W + x), one exact number
    each. Frame.lua does the same + - * / on doubles in the same order, so that both sides lay the cells out alike
    (tests compare them for every size)"""
    got = _INF.get((W, H))
    if got is not None:
        return got
    _, order = layout(W, H)
    cx, cy = (W - 1) / 2, (H - 1) / 2
    ay = (cy - INF_MARGIN[1]) / INF_YMAX
    ax = min(cx - INF_MARGIN[0], ay * INF_STRETCH)
    px, py = [], []
    for k in range(INF_STEPS + 1):
        s = k / INF_STEPS
        s2 = s * s
        s3 = s2 * s
        d = 1 + s2 * s2
        px.append(ax * ((s + s3) / d))
        py.append(ay * ((s - s3) / d))
    scored = []
    for x, y in order:
        X, Y = abs(x - cx), abs(y - cy)
        lo, hi = 0, INF_STEPS + 1                  # the first point at or right of X (Frame.lua searches alike)
        while lo < hi:
            mid = (lo + hi) // 2
            if px[mid] < X:
                lo = mid + 1
            else:
                hi = mid
        best, r, left = float("inf"), lo, lo - 1
        while True:
            moved = False
            if r <= INF_STEPS:
                dx = X - px[r]
                if dx * dx < best:
                    dy = Y - py[r]
                    d2 = dx * dx + dy * dy
                    if d2 < best:
                        best = d2
                    r += 1
                    moved = True
                else:
                    r = INF_STEPS + 1
            if left >= 0:
                dx = X - px[left]
                if dx * dx < best:
                    dy = Y - py[left]
                    d2 = dx * dx + dy * dy
                    if d2 < best:
                        best = d2
                    left -= 1
                    moved = True
                else:
                    left = -1
            if not moved:
                break
        scored.append(math.floor(best * 4096) * 65536 + y * W + x)
    scored.sort()
    out = _INF[(W, H)] = [(v % 65536 % W, v % 65536 // W) for v in scored]
    return out


def cell_order(W, H, mode):
    """the data cells in the order a frame of this mode fills them"""
    return inf_order(W, H) if mode == 3 else layout(W, H)[1]


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
    fixed, _ = layout(W, H)
    order = cell_order(W, H, mode)
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
