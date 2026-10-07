"""Where the game keeps the addon, and the file and process helpers the companion needs while the game runs."""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import time
from pathlib import Path

DEFAULT_GAME_DIRS = [r"C:\Program Files (x86)\World of Warcraft\_cn_beta_", r"C:\Program Files (x86)\World of Warcraft\_cn_",
                     r"C:\Program Files (x86)\World of Warcraft\_classic_beta_"]


def silent_wav(ms=10):
    """a valid silent WAV (8 kHz, 8-bit mono PCM) of `ms` milliseconds"""
    rate = 8000
    samples = rate * ms // 1000
    head = b"RIFF" + struct.pack("<I", 36 + samples) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate, 1, 8)
    return head + b"data" + struct.pack("<I", samples) + bytes([128]) * samples


def game_dir(explicit=None):
    """the client folder (the one holding Interface/AddOns): explicit, else the running game's, else a default"""
    if explicit:
        return Path(explicit)
    try:
        from ..transport.capture import find_window
        win = find_window()
        if win and win.exe:
            return Path(win.exe).parent
    except Exception:
        pass
    for d in DEFAULT_GAME_DIRS:
        if os.path.isdir(d):
            return Path(d)
    raise SystemExit("game folder not found: pass --game <folder with Interface\\AddOns>")


def addons_dir(explicit=None):
    return game_dir(explicit) / "Interface" / "AddOns"


def read_flavor(game):
    """the product in <client>/.flavor.info (wow_cn_beta), or None"""
    p = Path(game) / ".flavor.info"
    if not p.is_file():
        return None
    lines = [l.strip() for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    return lines[1] if len(lines) > 1 else None


def read_build_info(game):
    """dict(version, branch, product, flavor, path) for this client from the launcher's .build.info (the client folder or
    the one above it; pipe-separated, header `Name!TYPE:len`), the row matched to .flavor.info; None when not found.
    The version here is the real one ("1.60.1.70235"): the exe's version resource holds 16-bit fields, too small for the
    build number."""
    game = Path(game)
    flavor = read_flavor(game)
    for base in (game, game.parent):
        p = base / ".build.info"
        if p.is_file():
            break
    else:
        return None
    lines = [l for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    names = [h.split("!")[0] for h in lines[0].split("|")]
    rows = [dict(zip(names, l.split("|"))) for l in lines[1:]]
    row = (next((r for r in rows if flavor and r.get("Product") == flavor), None)
           or next((r for r in rows if r.get("Active") == "1"), None) or rows[0])
    return dict(version=row.get("Version", ""), branch=row.get("Branch", ""), product=row.get("Product", ""), flavor=flavor, path=str(p))


def atomic_write(path, data):
    """replace the file in one step; the game may hold it open for a moment (sharing violation), so retry for ~1 s"""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    for _ in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, path)


def process_start(pid):
    """process creation time as Unix seconds, or None"""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        c, e, kt, ut = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
            return None
        ticks = (c.dwHighDateTime << 32) | c.dwLowDateTime
        return ticks / 1e7 - 11644473600
    finally:
        k32.CloseHandle(h)
