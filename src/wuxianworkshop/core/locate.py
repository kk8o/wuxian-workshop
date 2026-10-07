"""Find a WoWBridge frame in a screenshot: finder patterns -> origin, grid size and cell pitch. No configuration needed."""
from dataclasses import dataclass

import numpy as np

from . import frame as F

THRESHOLD = 128   # fixed per-channel threshold, only for finding the finders; decoding uses the frame's own calibration cells


@dataclass
class Lock:
    ox: float          # pixel position of the top-left corner of cell (0, 0)
    oy: float
    px: float          # cell pitch in pixels
    py: float
    W: int
    H: int

    def center(self, x, y):
        return self.ox + (x + 0.5) * self.px, self.oy + (y + 0.5) * self.py

    def shifted(self, dx, dy):
        return Lock(self.ox + dx, self.oy + dy, self.px, self.py, self.W, self.H)


def components(mask):
    """4-connected components of a boolean mask as (x0, y0, x1, y1, pixels), x1 / y1 exclusive (run-length union-find)"""
    parent = []

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    runs, prev = [], []
    for y in np.flatnonzero(mask.any(axis=1)):
        d = np.diff(np.concatenate(([0], mask[y].astype(np.int8), [0])))
        cur, j = [], 0
        if prev and prev[0][3] != y - 1:
            prev = []
        for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
            lab = None
            while j < len(prev) and prev[j][1] <= s:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] < e:
                r = find(prev[k][2])
                if lab is None:
                    lab = r
                elif r != lab:
                    parent[r] = lab
                k += 1
            if lab is None:
                lab = len(parent)
                parent.append(lab)
            cur.append((s, e, lab, y))
            runs.append((y, s, e, lab))
        prev = cur
    boxes = {}
    for y, s, e, lab in runs:
        r = find(lab)
        b = boxes.get(r)
        if b is None:
            boxes[r] = [s, y, e, y + 1, e - s]
        else:
            b[0], b[1], b[2], b[3], b[4] = min(b[0], s), min(b[1], y), max(b[2], e), max(b[3], y + 1), b[4] + e - s
    return [tuple(int(v) for v in b) for b in boxes.values()]


def _px(img, x, y):
    h, w = img.shape[:2]
    xi, yi = int(np.floor(x)), int(np.floor(y))
    if 0 <= xi < w and 0 <= yi < h:
        return img[yi, xi]
    return None


def _is(img, x, y, want):
    p = _px(img, x, y)
    if p is None:
        return False
    on = p >= THRESHOLD
    return (bool(on[0]), bool(on[1]), bool(on[2])) == want


WHITE, BLACK = (True, True, True), (False, False, False)


def find_finders(img, min_size=2, max_candidates=5000):
    """candidate finder centres (cx, cy, size): a square magenta core whose 8 neighbours one core-width away are white.
    The shape test runs on every magenta blob (a busy background above the frame must not push the finders out of the
    budget); only the ring test is capped. Cores smaller than `min_size` pixels (1 px cells) are not looked for."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    mask = (r >= THRESHOLD) & (g < THRESHOLD) & (b >= THRESHOLD)
    out, checked = [], 0
    for x0, y0, x1, y1, n in components(mask):
        w, h = x1 - x0, y1 - y0
        if w < min_size or h < min_size or abs(w - h) > max(1, 0.2 * max(w, h)) or n < 0.7 * w * h:
            continue
        checked += 1
        if checked > max_candidates:
            break
        s = (w + h) / 2
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if all(_is(img, cx + dx * s, cy + dy * s, WHITE) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy):
            out.append((cx, cy, s))
    return out


def _count_cells(values, s):
    """number of alternating black / white cells along a line of pixels (True = white); runs shorter than half a cell are
    edge noise and are merged into their neighbour"""
    runs = []
    for v in values:
        if runs and runs[-1][0] == v:
            runs[-1][1] += 1
        else:
            runs.append([v, 1])
    out = []
    for v, n in runs:
        if out and (n < s / 2 or out[-1][0] == v):
            out[-1][1] += n
        else:
            out.append([v, n])
    if len(out) > 1 and out[0][1] < s / 2:
        out[1][1] += out.pop(0)[1]
    return len(out)


def _luma_line(img, x0, x1, y0, y1):
    """mean-of-channels >= THRESHOLD along a horizontal (y0 == y1) or vertical (x0 == x1) pixel line"""
    h, w = img.shape[:2]
    if y0 == y1:
        y = int(np.floor(y0))
        xa, xb = max(0, int(round(x0))), min(w, int(round(x1)))
        seg = img[y, xa:xb] if 0 <= y < h else np.zeros((0, 3))
    else:
        x = int(np.floor(x0))
        ya, yb = max(0, int(round(y0))), min(h, int(round(y1)))
        seg = img[ya:yb, x] if 0 <= x < w else np.zeros((0, 3))
    return list(seg.astype(np.float64).mean(axis=1) >= THRESHOLD)


def locate(img, finders=None):
    """Lock on the frame whose three finders agree (top-left, top-right, bottom-left), or None.
    The grid size comes from counting the cells of the timing row and column, the pitch from the distance between finders."""
    finders = find_finders(img) if finders is None else finders
    for cx, cy, s in sorted(finders, key=lambda f: f[0] + f[1]):
        # the quiet zone above-left of the top-left finder is black
        if not _is(img, cx - 2 * s, cy - 2 * s, BLACK):
            continue
        same = lambda f: abs(f[2] - s) <= max(1.0, 0.25 * s)
        tr = [f for f in finders if same(f) and abs(f[1] - cy) <= s / 2 and f[0] > cx + 10 * s]
        bl = [f for f in finders if same(f) and abs(f[0] - cx) <= s / 2 and f[1] > cy + 4 * s]
        for t in sorted(tr, key=lambda f: f[0]):
            W = _count_cells(_luma_line(img, cx + 1.5 * s, t[0] - 1.5 * s, cy - s, cy - s), s) + 6
            if not F.MIN_W <= W <= 255:
                continue
            for b in sorted(bl, key=lambda f: f[1]):
                H = _count_cells(_luma_line(img, cx - s, cx - s, cy + 1.5 * s, b[1] - 1.5 * s), s) + 6
                if not F.MIN_H <= H <= 255:
                    continue
                px, py = (t[0] - cx) / (W - 3), (b[1] - cy) / (H - 3)
                lk = Lock(cx - 1.5 * px, cy - 1.5 * py, px, py, W, H)
                if timing_errors(img, lk) <= max(1, (W + H) // 20):
                    return lk
    return None


def sample(img, lk, xs, ys):
    """mean RGB (float) of the cells (xs[i], ys[i]): 2x2 pixels around each centre when the pitch is >= 3.5, else 1"""
    h, w = img.shape[:2]
    cx = lk.ox + (np.asarray(xs) + 0.5) * lk.px
    cy = lk.oy + (np.asarray(ys) + 0.5) * lk.py
    xi, yi = np.floor(cx).astype(int), np.floor(cy).astype(int)
    pts = [(xi, yi)]
    if lk.px >= 3.5 and lk.py >= 3.5:
        pts += [(xi - 1, yi), (xi, yi - 1), (xi - 1, yi - 1)]
    acc = np.zeros((len(xi), 3))
    for qx, qy in pts:
        acc += img[np.clip(qy, 0, h - 1), np.clip(qx, 0, w - 1)].astype(np.float64)
    return acc / len(pts)


def timing_errors(img, lk):
    """cells of the two timing lines that do not read as the expected black / white"""
    xs = list(range(3, lk.W - 3)) + [0] * (lk.H - 6)
    ys = [0] * (lk.W - 6) + list(range(3, lk.H - 3))
    want = [(x - 3) % 2 for x in range(3, lk.W - 3)] + [(y - 3) % 2 for y in range(3, lk.H - 3)]
    rgb = sample(img, lk, xs, ys)
    got = (rgb.mean(axis=1) >= THRESHOLD).astype(int)
    return int(np.sum(got != np.asarray(want)))
