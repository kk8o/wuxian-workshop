"""Synthetic screens for tests: draw a frame the way the addon does, optionally with the distortions a real screen adds."""
import numpy as np

from wuxianworkshop.core import frame as F


def draw(cells, W, H, cell, origin=(0, 0), size=(640, 360), background=None, transform=None, seed=0):
    """RGB uint8 image of `size` (w, h) with the frame's quiet zone at `origin`.

    cells     {(x, y): (r, g, b)} from frame.build
    cell      cell size in pixels; a float simulates a non-integer UI scale (each pixel takes the cell under its centre)
    background None = random game-like noise
    transform f(img_float) -> img_float applied to the frame area only (gamma, contrast, blur ...)
    """
    rng = np.random.default_rng(seed)
    w, h = size
    img = rng.integers(0, 256, (h, w, 3)).astype(np.float64) if background is None else np.full((h, w, 3), background, np.float64)
    grid = np.zeros((H + 2, W + 2, 3))                       # quiet zone = the border of this grid, black
    for (x, y), rgb in cells.items():
        grid[y + 1, x + 1] = rgb
    ox, oy = origin
    span_w, span_h = (W + 2) * cell, (H + 2) * cell
    x0, y0 = max(0, int(np.floor(ox))), max(0, int(np.floor(oy)))
    x1, y1 = min(w, int(np.ceil(ox + span_w))), min(h, int(np.ceil(oy + span_h)))
    if x1 <= x0 or y1 <= y0:
        return np.clip(np.rint(img), 0, 255).astype(np.uint8)
    ys, xs = np.mgrid[y0:y1, x0:x1]
    gx = np.floor((xs + 0.5 - ox) / cell).astype(int)
    gy = np.floor((ys + 0.5 - oy) / cell).astype(int)
    inside = (gx >= 0) & (gx < W + 2) & (gy >= 0) & (gy < H + 2)
    patch = img[y0:y1, x0:x1].copy()
    patch[inside] = grid[gy[inside], gx[inside]]
    if transform is not None:
        patch = transform(patch)
    img[y0:y1, x0:x1] = patch
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def gamma(g):
    return lambda p: 255.0 * (p / 255.0) ** g


def contrast(c, brightness=0.0):
    return lambda p: (p - 128.0) * c + 128.0 + brightness


def box_blur_edges(p):
    """3x3 box blur: mixes colours across every cell edge"""
    q = np.pad(p, ((1, 1), (1, 1), (0, 0)), mode="edge")
    return sum(q[dy:dy + p.shape[0], dx:dx + p.shape[1]] for dy in range(3) for dx in range(3)) / 9.0


def sample_frame(W=64, H=16, mode=1, ftype=F.TYPE_DATA, payload=b"hello WoWBridge", toggle=1, session=0x1234, msg=7):
    return F.build(W, H, mode, toggle, ftype, session, msg, payload)
