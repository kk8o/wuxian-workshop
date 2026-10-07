"""Print every new WoWBridge frame on the game screen (data frames as text). Ctrl+C to stop.

    wuxian monitor [--rate 4]
"""
import argparse
import sys
import time

from ..core import frame as F
from ..core.decode import decode
from ..core.locate import locate
from ..transport.capture import find_window, grab_client
from .diag import parse_diag

if sys.stdout is not None:                                       # None in a detached process
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Chinese text in logs, also when piped (cp936 otherwise)


def main():
    ap = argparse.ArgumentParser(prog="wuxian monitor", description=__doc__.split("\n")[0])
    ap.add_argument("--rate", type=float, default=4, help="captures per second")
    args = ap.parse_args()
    seen, last = set(), None
    while True:
        t = time.time()
        win = find_window()
        if win and not win.minimized:
            img = grab_client(win, 1024, 640)
            lk = locate(img)
            state = "no frame"
            if lk:
                dec = decode(img, lk)
                state = f"frame {lk.W}x{lk.H} pitch {lk.px:.2f}" + ("" if dec.ok else f", not decoded: {dec.error}")
                if dec.ok and (dec.header["session"], dec.header["msg"]) not in seen:
                    seen.add((dec.header["session"], dec.header["msg"]))
                    if dec.header["type"] == F.TYPE_PROBE:
                        d = parse_diag(dec.payload)
                        print(time.strftime("%H:%M:%S"), f"probe #{dec.header['msg']}: physical {d.get('pw')}x{d.get('ph')}, "
                              f"gamma {d.get('gm')}, parent {d.get('par')}", flush=True)
                    else:
                        print(time.strftime("%H:%M:%S"), f"data #{dec.header['msg']} mode {dec.mode}:",
                              dec.payload.decode("utf-8", "replace"), flush=True)
            if state != last:
                print(time.strftime("%H:%M:%S"), state, flush=True)
                last = state
        time.sleep(max(0.0, 1 / args.rate - (time.time() - t)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
