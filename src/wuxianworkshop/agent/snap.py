"""The snap command: a PNG of the game's client area, or part of it, saved in the snaps folder (paths.snaps_dir).

capture() reads the window (WGC when a source is given: the window may be covered; GDI otherwise: the screen, so
anything over the game is in the picture) and save_snap() cuts, scales and writes it; snap_image() does both. The daemon
captures on its screen-reading thread and saves elsewhere, so that the frames the link shows meanwhile are not missed.
take_snap() is the text form the command file uses ("snap [x y w h]" in, one line for the logs out).
WoWBridge's frame is in every picture of the client area: link_frame() finds it, in_picture() says where it shows in a
snap. At rest it is the small heartbeat; a message going up takes a bigger one (a long one's parts 520 x 136 pixels at
4-pixel cells), grown right and down from the same corner over whatever is there, and the frame of an answer the daemon
has just read stays up a while longer (0.2 s at least): the daemon's snap waits for it to be gone (service.py).
"""
import time

from ..core import frame as F
from ..core.decode import decode
from ..core.locate import locate
from ..paths import snaps_dir
from ..transport.capture import grab_client


class SnapError(Exception):
    """why there is no snap: no frame from the window, a region outside the client area, the screen unreadable"""


def save_snap(img, region=None, max_width=None, client=None):
    """an RGB array -> (path, width, height): cut to region [x, y, w, h] if given, scaled down to max_width if wider,
    saved as snap-<time>.png in the snaps folder"""
    from PIL import Image
    if region is not None:
        x, y, w, h = (int(n) for n in region)
        img = img[max(y, 0):max(y, 0) + h, max(x, 0):max(x, 0) + w]
    if img.size == 0:
        raise SnapError(f"region {list(region)} is outside the {client or 'picture'}")
    pic = Image.fromarray(img)
    if max_width and pic.width > int(max_width):
        max_width = int(max_width)
        pic = pic.resize((max_width, max(1, round(pic.height * max_width / pic.width))), Image.LANCZOS)
    path = snaps_dir() / f"snap-{time.strftime('%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.png"
    pic.save(path)
    return path, pic.width, pic.height


def link_frame(img):
    """WoWBridge's frame in a picture of the client area: (its rect [x, y, w, h] with the black border, at rest) where at
    rest = a control frame (HELLO, the heartbeat: no message going up; one that does not decode is not); None when the
    picture shows none"""
    lk = locate(img)
    if lk is None:
        return None
    dec = decode(img, lk)
    rect = [round(lk.ox - lk.px), round(lk.oy - lk.py), round((lk.W + 2) * lk.px), round((lk.H + 2) * lk.py)]
    return rect, dec.ok and dec.header["type"] == F.TYPE_CONTROL


def in_picture(rect, region, size, picture=None):
    """where rect [x, y, w, h] (client pixels) shows in a snap of region (None: the whole client area, `size` (w, h)), cut
    as save_snap cuts it: [x, y, w, h] from the picture's corner, scaled to picture = its (width, height) once saved;
    None when the snap does not show it"""
    rx, ry, rw, rh = region if region is not None else (0, 0, *size)
    x0, y0 = max(rx, 0), max(ry, 0)
    x1, y1 = min(x0 + rw, size[0]), min(y0 + rh, size[1])
    ax, ay = max(rect[0], x0), max(rect[1], y0)
    bx, by = min(rect[0] + rect[2], x1), min(rect[1] + rect[3], y1)
    if bx <= ax or by <= ay:
        return None
    sx, sy = (picture[0] / (x1 - x0), picture[1] / (y1 - y0)) if picture else (1, 1)
    return [round((ax - x0) * sx), round((ay - y0) * sy), round((bx - ax) * sx), round((by - ay) * sy)]


def capture(win, wgc):
    """the game window's client area as an RGB array (the part that needs the window; save_snap does the rest)"""
    if getattr(win, "minimized", False):
        raise SnapError("the game window is minimized: nothing is drawn")
    try:
        img = wgc.snapshot() if wgc is not None else grab_client(win)
    except OSError as e:
        raise SnapError(str(e)) from None
    if img is None:
        raise SnapError("no new frame from the game window within 1 s")
    return img


def snap_image(win, wgc, region=None, max_width=None):
    """a snap of the game window's client area (or the region [x, y, w, h] of it) -> (path, width, height)"""
    return save_snap(capture(win, wgc), region, max_width, client=f"{win.w}x{win.h} client area")


def take_snap(spec, win, wgc):
    """"snap [x y w h]": a PNG of the game's client area, or that part of it, in the snaps folder; returns a line for the
    logs. With WGC the window may be covered; GDI copies the screen, so the game has to be visible"""
    nums = [int(n) for n in spec.split()[1:] if n.isdigit()]
    try:
        path, w, h = snap_image(win, wgc, nums if len(nums) == 4 else None)
    except SnapError as e:
        return f"snap failed: {e}"
    return f"{path} {w}x{h}" + ("" if wgc is not None else " (GDI: anything over the game is in it)")
