"""What a probe frame (core/frame.py TYPE_PROBE: the addon's diagnostics, then colour ramps) says about the screen
path: lock, decode, calibration, cell purity and the ramps. The frame-format tests use it (tests/core/test_frame.py)."""
import numpy as np

from wuxianworkshop.agent.diag import parse_diag
from wuxianworkshop.core import frame as F
from wuxianworkshop.core.locate import sample


def purity(img, lk, cells):
    """per cell: the largest deviation (0..255) of any pixel inside the cell from the cell's median colour.
    Only meaningful when the pitch is a whole number of pixels (otherwise cells straddle pixels by design)."""
    h, w = img.shape[:2]
    devs = {}
    for x, y in cells:
        x0, x1 = int(round(lk.ox + x * lk.px)), int(round(lk.ox + (x + 1) * lk.px))
        y0, y1 = int(round(lk.oy + y * lk.py)), int(round(lk.oy + (y + 1) * lk.py))
        if x0 < 0 or y0 < 0 or x1 > w or y1 > h or x1 <= x0 or y1 <= y0:
            continue
        block = img[y0:y1, x0:x1].reshape(-1, 3).astype(int)
        med = np.median(block, axis=0)
        devs[(x, y)] = int(np.max(np.abs(block - med)))
    return devs


def ramps(img, lk, dec):
    """measured vs expected colour of the 1024 ramp cells that follow the probe frame's bit stream"""
    _, order = F.layout(lk.W, lk.H)
    tail = order[dec.stream_cells:dec.stream_cells + 1024]
    if len(tail) < 1024:
        return None
    rgb = sample(img, lk, [x for x, _ in tail], [y for _, y in tail])
    out = {}
    for ch, name in enumerate(("r", "g", "b", "gray")):
        seg = rgb[ch * 256:(ch + 1) * 256]
        lv = np.arange(256)
        active = seg.mean(axis=1) if name == "gray" else seg[:, ch]
        others = np.delete(seg, ch, axis=1) if name != "gray" else seg - seg.mean(axis=1, keepdims=True)
        err = active - lv
        out[name] = dict(
            exact=int(np.sum(np.rint(active) == lv)),
            max_abs_error=float(np.max(np.abs(err))),
            mean_error=float(np.mean(err)),
            crosstalk=float(np.max(np.abs(others))),
            monotonic=bool(np.all(np.diff(active) >= -0.5)),
            distinct=int(len(np.unique(np.rint(active)))),
            at_0_85_170_255=[round(float(active[i]), 1) for i in (0, 85, 170, 255)],
            curve=[round(float(v), 1) for v in active],
        )
    return out


def analyse(img, lk, dec):
    """a report on one probe frame"""
    rep = dict(lock=dict(origin=[round(lk.ox, 2), round(lk.oy, 2)], pitch=[round(lk.px, 4), round(lk.py, 4)], grid=[lk.W, lk.H]),
               decode=dict(ok=dec.ok, error=dec.error, mode=dec.mode, toggle=dec.toggle, header=dec.header,
                           timing_errors=dec.timing_errors, calibration_errors=dec.calibration_errors),
               calibration=dec.calibration)
    rep["diag"] = parse_diag(dec.payload) if dec.ok else {}
    integer_pitch = abs(lk.px - round(lk.px)) < 0.02 and abs(lk.py - round(lk.py)) < 0.02
    if integer_pitch:
        devs = purity(img, lk, [(x, y) for y in range(lk.H) for x in range(lk.W)])
        vals = np.array(list(devs.values())) if devs else np.array([0])
        rep["purity"] = dict(cells=len(devs), pure=int(np.sum(vals <= 2)), worst=int(vals.max()),
                             worst_cells=[list(k) for k, v in sorted(devs.items(), key=lambda kv: -kv[1])[:8] if v > 2])
    else:
        rep["purity"] = dict(skipped="pitch is not a whole number of pixels")
    rr = ramps(img, lk, dec) if dec.ok else None
    rep["ramps"] = rr
    rep["modes"] = modes(dec, rr)
    return rep


def modes(dec, rr):
    """which colour modes this screen path supports, judged from the calibration cells and the ramps"""
    m = {}
    margins = [dec.calibration[c]["margin"] for c in "rgb"] if dec.calibration else [0]
    m["mode0_bw"] = dec.ok
    m["mode1_8colours"] = dec.ok and dec.calibration_errors == 0 and min(margins) >= 0.5
    if rr:
        gaps = []
        for c in ("r", "g", "b"):
            pts = rr[c]["at_0_85_170_255"]
            gaps += [pts[i + 1] - pts[i] for i in range(3)]
        m["mode2_4levels"] = m["mode1_8colours"] and min(gaps) >= 40 and max(rr[c]["crosstalk"] for c in "rgb") <= 20
        m["min_gap_4levels"] = round(min(gaps), 1)
        m["bit_exact"] = all(rr[c]["exact"] == 256 for c in ("r", "g", "b", "gray"))
    return m
