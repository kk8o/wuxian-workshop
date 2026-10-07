"""Decode a located frame: calibration -> thresholds -> cell values -> header, payload, CRC."""
from dataclasses import dataclass, field

import numpy as np

from . import frame as F
from .locate import Lock, sample, timing_errors


@dataclass
class Decoded:
    lock: Lock
    ok: bool = False
    error: str = ""
    mode: int = -1
    toggle: int = -1
    header: dict = field(default_factory=dict)
    payload: bytes = b""
    stream_cells: int = 0          # data cells used by header + payload + CRC
    timing_errors: int = 0
    calibration: dict = field(default_factory=dict)   # per channel: on / off means, threshold, margin (0..1)
    calibration_errors: int = 0    # calibration cells that do not classify as their own colour


def _calibrate(rgb_cal):
    """thresholds from the 8 calibration cells (K W R G B C M Y): per channel, the midpoint of the 4 'on' and 4 'off' cells"""
    out = {}
    for ch, bit in (("r", 4), ("g", 2), ("b", 1)):
        idx = "rgb".index(ch)
        on = [rgb_cal[i][idx] for i, v in enumerate(F.CALIBRATION) if v & bit]
        off = [rgb_cal[i][idx] for i, v in enumerate(F.CALIBRATION) if not v & bit]
        hi, lo = float(np.mean(on)), float(np.mean(off))
        out[ch] = dict(on=hi, off=lo, threshold=(hi + lo) / 2, margin=(hi - lo) / 255.0)
    return out


def _palette(rgb, cal):
    thr = np.array([cal["r"]["threshold"], cal["g"]["threshold"], cal["b"]["threshold"]])
    bits = (rgb >= thr).astype(int)
    return bits[:, 0] * 4 + bits[:, 1] * 2 + bits[:, 2]


def _levels(rgb, cal):
    """mode 2: each channel quantised to 4 levels between the calibrated off and on values"""
    lo = np.array([cal[c]["off"] for c in "rgb"])
    hi = np.array([cal[c]["on"] for c in "rgb"])
    q = np.clip(np.rint((rgb - lo) / np.maximum(hi - lo, 1) * 3), 0, 3).astype(int)
    return q[:, 0] * 16 + q[:, 1] * 4 + q[:, 2]


def decode(img, lk):
    d = Decoded(lock=lk)
    fixed, order = F.layout(lk.W, lk.H)
    d.timing_errors = timing_errors(img, lk)
    cal_xy = [(F.CONTROL_X0 + 3 + i, 1) for i in range(8)]
    rgb_cal = sample(img, lk, [x for x, _ in cal_xy], [y for _, y in cal_xy])
    d.calibration = _calibrate(rgb_cal)
    d.calibration_errors = int(np.sum(_palette(rgb_cal, d.calibration) != np.array(F.CALIBRATION)))
    ctl = sample(img, lk, [F.CONTROL_X0, F.CONTROL_X0 + 1, F.CONTROL_X0 + 2], [1, 1, 1])
    white = _palette(ctl, d.calibration) == F.WHITE
    d.mode = int(white[0]) * 2 + int(white[1])
    d.toggle = int(white[2])
    if d.mode not in F.BITS_PER_CELL:
        d.error = f"unknown mode {d.mode}"
        return d
    bits = F.BITS_PER_CELL[d.mode]
    rgb = sample(img, lk, [x for x, _ in order], [y for _, y in order])
    if d.mode == 1:
        values = _palette(rgb, d.calibration)
    elif d.mode == 0:
        values = (_palette(rgb, d.calibration) == F.WHITE).astype(int)
    else:
        values = _levels(rgb, d.calibration)
    head = F.unpack(values[: -(-F.HEADER_LEN * 8 // bits)], bits, F.HEADER_LEN)
    h = F.parse_header(head)
    if h is None:
        d.error = "header magic / version / CRC-16 mismatch"
        return d
    if (h["W"], h["H"]) != (lk.W, lk.H):
        d.error = f"header says {h['W']}x{h['H']}, finders say {lk.W}x{lk.H}"
        return d
    d.header = h
    total = F.HEADER_LEN + h["length"] + 4
    d.stream_cells = -(-total * 8 // bits)
    if d.stream_cells > len(order):
        d.error = f"length {h['length']} does not fit the frame"
        return d
    body = F.unpack(values[: d.stream_cells], bits, total)
    if F.crc32(body[:-4]) != int.from_bytes(body[-4:], "big"):
        d.error = "CRC-32 mismatch"
        return d
    d.payload = body[F.HEADER_LEN:-4]
    d.ok = True
    return d
