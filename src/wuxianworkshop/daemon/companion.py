r"""The companion: reads WoWBridge frames off the screen and answers through the font mailbox.

    wuxian companion [--capture gdi|wgc] [--say TEXT ...] [--ping-every 10] [--duration 0] [--rate 0.05]
                     [--no-wait-restart] [--game "<client folder>"]

CompanionLoop is the screen loop around one transport.link.Companion: it finds the game window, reads the frame area,
feeds the frames to the Companion, polls the command files and ticks the link, in rounds (step()). run() repeats the
rounds until stop(); request() hands a callable from another thread to the loop's own thread, which runs it between
rounds (the daemon in server.py drives the loop that way: it owns the thread, the HTTP side only queues requests).
main() is the stand-alone `wuxian companion`, which does the same with the command files only.

Handshake, heartbeats, cumulative acknowledgements and pings are in transport/link.py. --capture gdi (the default) reads
the screen, so the game's top-left corner must stay uncovered; --capture wgc reads the window itself through Windows Graphics
Capture: it works when the window is covered (not when minimized), and Windows 10 draws a yellow border around the window
meanwhile. On every new game process the loop empties the mailbox slots used before and writes proc.ttf.
Its files are under %LOCALAPPDATA%\WuxianWorkshop (paths.py; WUXIAN_HOME replaces that root). --say texts go to the game
after the handshake (shown in the chat); so do lines appended to state\say.txt while it runs. A line appended to
state\command.txt goes to the addon as a command: "run <lua>" and "load <file>" have it run Lua (hot loading, no /reload),
"reload" puts up a button that reloads the UI when the user clicks it (this client lets only a click or key press reload).
"snap [x y w h]" is done here: a PNG of the game's client area (or that part of it) in snaps\. The addon's debug output
(Lua errors of any addon with their stack, print() output, Lua warnings, blocked actions, the results of run / load, the
reload button) and the snaps are appended to logs\debug.log.
Ctrl+C, --duration or creating state\companion.stop ends it and saves logs\link-<time>.json.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from pathlib import Path

from ..agent.debuglog import debug_writer
from ..agent.diag import parse_diag
from ..agent.snap import take_snap
from ..core import frame as F
from ..core.decode import decode
from ..core.game import addons_dir, process_start, read_build_info
from ..core.locate import locate
from ..paths import logs_dir, state_dir
from ..transport import capture
from ..transport.capture import WgcSource, grab_client
from ..transport.link import Companion

if sys.stdout is not None:                                       # None in a detached process
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Chinese text in logs, also when piped (cp936 otherwise)

ROI_SMALL, ROI_FULL = (600, 300), (1024, 640)   # the region read: every frame at 4 px cells fits; the WGC staging size
MISSES_BEFORE_FULL = 20                          # reads in a row without a frame before the whole window is searched
FULL_SEARCH_EVERY = 1.0                          # seconds between searches of the whole window while the frame is lost
MARGIN = 16                                      # pixels read around the frame's top-left corner once it is found
SETTLE = 0.5                                     # the window counts as moved / resized once its geometry held this long
WINDOW_POLL = 2.0                                # seconds between looks for the game window while there is none


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def new_lines(path, pos):
    """lines appended to a text file since byte pos: (lines, new pos)"""
    if not path.exists() or path.stat().st_size <= pos:
        return [], pos
    with path.open("rb") as fh:
        fh.seek(pos)
        new = fh.read()
    return [line.strip() for line in new.decode("utf-8", "replace").splitlines() if line.strip()], pos + len(new)


def exe_version(path):
    """the file version of an exe ("1.60.1.70235"; the game's build is the last number), or None"""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        p, n = ctypes.c_void_p(), wt.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n)) or not p.value:
            return None
        info = (ctypes.c_uint32 * 4).from_address(p.value)      # VS_FIXEDFILEINFO: signature, struct version, MS, LS
        return f"{info[2] >> 16}.{info[2] & 0xFFFF}.{info[3] >> 16}.{info[3] & 0xFFFF}"
    except (OSError, AttributeError, ValueError):
        return None


def installed_at():
    """when `wuxian install` last added files the running game cannot know about (state/install.json): a game started
    before that is waited out; 0 when never, or when that install only changed files or .toc headers (a game running
    then works on; older records without the flag count as such an install)"""
    try:
        record = json.loads((state_dir() / "install.json").read_text(encoding="utf-8"))
        return record["time"] if record.get("restart_for_link", record.get("restart_required", True)) else 0
    except (OSError, ValueError, KeyError, AttributeError):
        return 0


class CompanionLoop:
    """the screen loop around one Companion; see the module docstring. addons may be None when the client folder is
    not known yet (no game window, no default folder): the Companion is made when the window appears"""

    def __init__(self, addons, *, capture="gdi", rate=0.05, ping_every=10.0, wait_restart=True, installed=0,
                 files=True, log=log, on_debug=None, find_window=capture.find_window, link=True, echo_debug=True):
        self.addons, self.rate, self.log, self.on_debug, self.ping_every = addons, rate, log, on_debug, ping_every
        self.echo_debug = echo_debug                # False: the Companion's debug output goes to on_debug only
        self.capture_wanted = self.capture = capture      # asked for / in use: WGC falls back to GDI when it fails
        self.wait_restart, self.installed, self.files, self.link = wait_restart, installed, files, link
        self.find_window = find_window
        self.before_load = None                      # the daemon's hook for its Companion (see AgentCommands)
        self.comp = None
        if addons is not None:
            self._make_companion(Path(addons))
        self.requests = queue.Queue()
        self.wake = threading.Event()               # set by request() and stop(): a waiting round ends early
        self.stopping = threading.Event()
        self.win = None                              # the game window as of the last look, None when there is none
        self.blocked = None                          # why the screen is not read: "restart" (files the running game
                                                     # does not know), "player" (player mode), None
        self.win_start = None                        # when its process started (Unix seconds)
        self.build = self.version = None             # the game's build number and file version, from the exe
        self.pid = None                              # the game process the Companion knows
        self.said = set()
        self.wgc, self.wgc_seq, self.wgc_failed = None, 0, None   # wgc_failed: the window WGC refused (GDI for it)
        self.roi, self.misses = ROI_SMALL, 0
        self.roi_at, self.last_search = (0, 0), 0.0  # where the region read starts (the player may move the frame)
        self.last_key, self.pitch = None, None
        self.geom = self.moving_since = self.last_move = self.relock_from = None
        self.failed_since, self.minimized_since = 0, None
        self.changes = []
        self.loops, self.busy, self.worst, self.bad = 0, 0.0, 0.0, 0
        self.started = time.time()
        state = state_dir()
        self.say_file, self.command_file = state / "say.txt", state / "command.txt"
        self.say_pos = self.say_file.stat().st_size if self.say_file.exists() else 0
        self.command_pos = self.command_file.stat().st_size if self.command_file.exists() else 0

    # --- the Companion ---------------------------------------------------------------------------------------------

    def _make_companion(self, addons):
        self.addons = addons
        self.comp = Companion(addons, log=self.log, ping_every=self.ping_every, on_debug=self.on_debug,
                              echo_debug=self.echo_debug)
        self.comp.before_load = self._before_load
        self.comp.on_client = self._on_client

    def _on_client(self, version, interface):
        """the game's own version and ## Interface (WoWBridge's HELLO): kept, the installer writes it into the tocs"""
        from ..installer.addons import remember_client
        if remember_client(version, interface):
            self.log(f"the game reports client {version} with Interface {interface}")

    def _before_load(self, addon, path):
        if self.before_load is not None:
            return self.before_load(addon, path)
        return None

    def companion(self):
        """the Companion, or a RuntimeError while the client folder is still unknown"""
        if self.comp is None:
            raise RuntimeError("the game folder is not known yet (no game window, no default folder): "
                               "start the game or pass --game")
        return self.comp

    # --- requests from other threads -------------------------------------------------------------------------------

    def request(self, fn, *args):
        """run fn(*args) on the loop's thread between rounds; the Future gets the result or the exception"""
        fut = Future()
        self.requests.put((fn, args, fut))
        self.wake.set()
        return fut

    def _serve_requests(self, limit=64):
        for _ in range(limit):
            try:
                fn, args, fut = self.requests.get_nowait()
            except queue.Empty:
                return
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args))
            except BaseException as e:               # the caller gets it; the loop goes on
                fut.set_exception(e)

    def _wait(self, seconds):
        """the pause between rounds, cut short by a request or stop()"""
        if seconds > 0:
            self.wake.wait(seconds)
        self.wake.clear()

    def stop(self):
        self.stopping.set()
        self.wake.set()

    # --- the rounds ------------------------------------------------------------------------------------------------

    def _once(self, msg):
        if msg not in self.said:
            self.said.add(msg)
            self.log(msg)

    def _tick(self):
        if self.comp is not None and self.link:           # player mode: the link is off, no packet is written
            self.comp.tick()

    def step(self):
        """one round: requests, the window, the command files, one read of the frame area, the link's clock"""
        self._serve_requests()
        win = self.find_window()
        if win is None:
            if self.win is not None:
                self.log("the game window is gone")
                self.said.discard("waiting for the game window")
            self.win = None                             # self.pid stays: the same process back is not a new one
            self._once("waiting for the game window")
            self._tick()
            self._wait(WINDOW_POLL)
            return
        start = process_start(win.pid) or 0
        if win.pid != (self.win.pid if self.win else None):
            # the launcher's .build.info has the real version ("1.60.1.70235"); the exe's version resource holds 16-bit
            # fields, too small for the build number, so it is only the fallback
            info = read_build_info(Path(win.exe).parent) if win.exe else None
            self.version = (info or {}).get("version") or (exe_version(win.exe) if win.exe else None)
            self.build = int(self.version.rsplit(".", 1)[-1]) if self.version else None
            self.win_start = start
        self.win = win
        if self.wait_restart and start < self.installed:
            self.blocked = "restart"
            self._once("the game was started before the install: fully exit and restart it")
            self._tick()
            self._wait(WINDOW_POLL)
            return
        if win.exe:
            running = Path(win.exe).parent / "Interface" / "AddOns"
            if self.comp is None:
                self._make_companion(running)
                self.log(f"game folder: {self.addons.parent.parent}")
            elif os.path.normcase(str(running)) != os.path.normcase(str(self.addons)) and running.is_dir():
                # the game runs from another folder than the one the loop started with (a moved or a new client): the
                # link only works through the running game's mailbox
                self.log(f"the game runs from {running.parent.parent}, not {self.addons.parent.parent}: following it")
                self._make_companion(running)
                self.pid = None
        if not self.link:                             # player mode: the window is followed, the screen is not read
            self.blocked = "player"
            self._wait(WINDOW_POLL)
            return
        self.blocked = None
        comp = self.comp
        if win.pid != self.pid:
            self.pid = win.pid
            comp.new_process(f"P{win.pid}-{int(start)}")
            self.geom = None
        now = time.time()
        if win.minimized:                                   # nothing is drawn: no geometry to follow
            if self.minimized_since is None:
                self.minimized_since = now
                self.log("window minimized")
            comp.tick()
            self._wait(self.rate)
            return
        if self.minimized_since is not None:
            self.log(f"window restored after {now - self.minimized_since:.1f} s minimized")
            self.minimized_since, self.moving_since, self.relock_from, self.failed_since, self.geom = None, None, now, 0, None
            self.changes.append(dict(at=round(now, 3), client=[win.w, win.h], origin=[win.x, win.y], dpi=win.dpi,
                                     changing=0, restored=True, relock=None, failed=0))
        g = (win.x, win.y, win.w, win.h, win.dpi)
        if self.geom is not None and g != self.geom:        # E2: moved, resized, another monitor or mode
            self.moving_since = self.moving_since or now
            self.last_move = now
        self.geom = g
        if self.moving_since is not None and now - self.last_move >= SETTLE:
            self.log(f"window settled: client {win.w}x{win.h} at ({win.x},{win.y}), DPI {win.dpi}, "
                     f"after {now - self.moving_since:.1f} s of changes")
            self.changes.append(dict(at=round(now, 3), client=[win.w, win.h], origin=[win.x, win.y], dpi=win.dpi,
                                     changing=round(now - self.moving_since, 2), relock=None, failed=0))
            self.moving_since, self.relock_from, self.failed_since = None, now, 0
        if self.files:
            self._file_commands(win)
        if self.capture != self.capture_wanted:          # the settings changed, or a new window after a WGC failure
            if self.capture_wanted == "gdi" or self.wgc_failed != win.hwnd:
                self.capture = self.capture_wanted
        if self.capture == "wgc" and (self.wgc is None or self.wgc.hwnd != win.hwnd or self.wgc.closed):
            if self.wgc is not None:
                self.wgc.stop()
            try:
                self.wgc, self.wgc_seq = WgcSource(win.hwnd, *ROI_FULL), 0
                self.log(f"WGC capture of window {win.hwnd} started")
            except Exception as e:                  # an old Windows, no graphics device, or the capture refused
                self.log(f"WGC unavailable ({e}); reading the screen with GDI instead")
                self.capture, self.wgc, self.wgc_failed = "gdi", None, win.hwnd
        elif self.capture == "gdi" and self.wgc is not None:    # switched to GDI (settings)
            self.wgc.stop()
            self.wgc = None
        t0 = time.perf_counter()
        if self.misses >= MISSES_BEFORE_FULL and time.time() - self.last_search >= FULL_SEARCH_EVERY:
            self._search(win)                       # the frame was moved (WoWBridge's /wb unlock), or is not up yet
        region = self.roi
        if self.wgc is not None:
            try:
                seq, img = self.wgc.read(*region, *self.roi_at)
            except OSError as e:                    # the window went away, or the graphics device was reset
                self._once(f"WGC read failed ({e}); starting the capture again")
                self.wgc.stop()
                self.wgc = None
                comp.tick()
                self._wait(0.5)
                return
            if img is None or seq == self.wgc_seq:  # the window has shown nothing new since the last read
                comp.tick()
                self._wait(self.rate)
                return
            self.wgc_seq = seq
        else:
            try:
                img = grab_client(win, *region, *self.roi_at)
            except OSError as e:                    # a UAC prompt, the lock screen or Ctrl+Alt+Del: no screen to read
                self._once(f"screen capture failed ({e}); retrying (a UAC prompt or the lock screen hides the desktop)")
                comp.tick()
                self._wait(0.5)
                return
            self.said.discard(next((m for m in self.said if m.startswith("screen capture failed")), None))
        self._frame(win, img, region, t0)
        comp.tick()
        self._wait(max(0.0, self.rate - (time.perf_counter() - t0)))

    def _file_commands(self, win):
        """lines appended to state/say.txt and state/command.txt"""
        comp = self.comp
        lines, self.say_pos = new_lines(self.say_file, self.say_pos)
        for line in lines:
            comp.say(line)
            self.log(f"say: {line}")
        lines, self.command_pos = new_lines(self.command_file, self.command_pos)
        for line in lines:
            self.log(f"command: {line}")
            if line.split()[0] == "snap":
                text = take_snap(line, win, self.wgc)
                comp.note(f"snap: {text}")
                if comp.on_debug:
                    comp.on_debug("SNAP", text)
            else:
                comp.command(line)

    def _search(self, win):
        """the whole client area, for a frame that is not where it was read: found, the region read starts at its
        corner (less MARGIN); the frame finder takes 15 ms on a 1920 x 1001 window"""
        self.last_search = time.time()
        try:
            full = self.wgc.snapshot(timeout=0.5) if self.wgc is not None else grab_client(win)
        except OSError:
            return
        lk = locate(full) if full is not None else None
        if lk is None:
            return
        at = (max(0, int(lk.ox - lk.px) - MARGIN), max(0, int(lk.oy - lk.py) - MARGIN))   # the quiet zone's corner
        if at != self.roi_at:
            self.log(f"frame found at ({int(lk.ox - lk.px)},{int(lk.oy - lk.py)}) of the client area: reading from there")
            self.roi_at = at
        self.misses = 0

    def _follow(self, lk):
        """the region read starts MARGIN above and left of the frame, wherever in the region it was found: a frame
        dragged within the region would otherwise leave the biggest ones (a long message's parts) partly outside it"""
        at = (max(0, self.roi_at[0] + int(lk.ox - lk.px) - MARGIN), max(0, self.roi_at[1] + int(lk.oy - lk.py) - MARGIN))
        if abs(at[0] - self.roi_at[0]) > MARGIN // 2 or abs(at[1] - self.roi_at[1]) > MARGIN // 2:
            self.roi_at = at

    def _frame(self, win, img, region, t0):
        """one image of the frame area: locate, decode, hand a new frame to the Companion"""
        comp = self.comp
        lk = locate(img)
        dec = decode(img, lk) if lk is not None else None
        dt = time.perf_counter() - t0
        self.loops, self.busy, self.worst = self.loops + 1, self.busy + dt, max(self.worst, dt)
        self.misses = 0 if lk is not None else self.misses + 1
        if lk is not None:
            self._follow(lk)
        if dec is None:
            return
        if not dec.ok:
            self.bad += 1
            self.failed_since += 1
            return
        now = time.time()
        if self.relock_from is not None:
            self.changes[-1].update(relock=round(now - self.relock_from, 3), failed=self.failed_since,
                                    pitch=[round(lk.px, 3), round(lk.py, 3)], frame_origin=[round(lk.ox, 1), round(lk.oy, 1)])
            self.log(f"re-locked {now - self.relock_from:.2f} s after the window settled: pitch {lk.px:.3f}x{lk.py:.3f}, "
                     f"origin ({lk.ox:.1f},{lk.oy:.1f}), {self.failed_since} reads not decoded meanwhile")
            self.relock_from = None
        if self.pitch is not None and abs(lk.px - self.pitch) > 0.01 and self.moving_since is None:
            self.log(f"frame pitch now {lk.px:.3f} px (was {self.pitch:.3f})")   # while settled; mid-drag DWM stretches
        self.pitch = lk.px
        h = dec.header
        key = (h["session"], h["type"], h["msg"], h["part"])
        if key == self.last_key:                  # the same frame stays up for several reads
            return
        self.last_key = key
        if h["type"] == F.TYPE_PROBE:             # a probe frame (/wb diag): one line of diagnostics
            d = parse_diag(dec.payload)
            self.log(f"probe frame: client {win.w}x{win.h}, game reports physical {d.get('pw')}x{d.get('ph')} window "
                     f"{d.get('gw')} UI scale {d.get('es')}; pitch {lk.px:.3f}")
        comp.on_frame(h["type"], h["session"], h["msg"], dec.payload, h["part"], h["parts"])

    def run(self, stop_when=None):
        """rounds until stop() (or stop_when() says so); a round that fails is logged, the next one goes on"""
        self.log("capture: " + ("Windows Graphics Capture of the game window (works when covered; Windows 10 shows a yellow border)"
                                if self.capture == "wgc" else "GDI from the screen (keep the game's top-left corner uncovered)"))
        try:
            while not self.stopping.is_set() and not (stop_when is not None and stop_when()):
                try:
                    self.step()
                except Exception:
                    self.log("loop error: " + traceback.format_exc().rstrip())
                    self._wait(0.5)
        finally:
            if self.wgc is not None:
                self.wgc.stop()
                self.wgc = None
            self._serve_requests()                  # nobody waits forever on a request made at the very end

    # --- what the daemon shows -------------------------------------------------------------------------------------

    def game_status(self):
        """the game window as of the last look, for /api/status"""
        win = self.win
        if win is None:
            return dict(found=False, pid=None, build=None, version=None, client=None, start=None, minimized=None, exe=None)
        return dict(found=True, pid=win.pid, build=self.build, version=self.version, client=dict(w=win.w, h=win.h),
                    start=self.win_start, minimized=win.minimized, exe=win.exe)

    def report(self):
        """the link report (transport.link.Companion.report) with the host's numbers, for logs/link-<time>.json"""
        rep = self.comp.report() if self.comp is not None else dict(stats=dict(packets=0, first_slot=None, last_slot=None,
                                                                             data=0, dups=0), ping=dict(n=0, avg=None, p95=None, max=None))
        rep["window_changes"] = self.changes
        rep["capture"] = self.capture
        rep["host"] = dict(seconds=round(time.time() - self.started, 1), reads=self.loops,
                           read_ms_avg=round(self.busy / max(self.loops, 1) * 1000, 1), read_ms_max=round(self.worst * 1000, 1),
                           not_decoded=self.bad)
        return rep


def main():
    ap = argparse.ArgumentParser(prog="wuxian companion", description=__doc__.split("\n")[0])
    ap.add_argument("--say", action="append", default=[], help="text to send after the handshake (repeatable)")
    ap.add_argument("--ping-every", type=float, default=10.0, help="seconds between pings (round-trip time)")
    ap.add_argument("--duration", type=float, default=0, help="stop after this many seconds (0 = until Ctrl+C)")
    ap.add_argument("--rate", type=float, default=0.05, help="seconds between screen reads")
    ap.add_argument("--capture", choices=("gdi", "wgc"), default="gdi",
                    help="gdi: the screen (the corner must be visible); wgc: the window itself, also when covered")
    ap.add_argument("--no-wait-restart", action="store_true", help="do not wait for the game to restart after the install")
    ap.add_argument("--game", help="client folder that holds Interface\\AddOns")
    args = ap.parse_args()
    state, logs = state_dir(), logs_dir()
    loop = CompanionLoop(addons_dir(args.game), capture=args.capture, rate=args.rate, ping_every=args.ping_every,
                         wait_restart=not args.no_wait_restart, installed=installed_at(),
                         on_debug=debug_writer(logs / "debug.log"))
    for text in args.say:
        loop.comp.say(text)
    stop_file = state / "companion.stop"
    if stop_file.exists():
        stop_file.unlink()
    deadline = time.time() + args.duration if args.duration else None
    try:
        loop.run(stop_when=lambda: (deadline is not None and time.time() >= deadline) or stop_file.exists())
    except KeyboardInterrupt:
        pass
    rep = loop.report()
    path = logs / f"link-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    p = rep["ping"]
    log(f"{rep['stats']['packets']} packets written (slots {rep['stats']['first_slot']}-{rep['stats']['last_slot']}), "
        f"{rep['stats']['data']} messages read, {rep['stats']['dups']} shown again; ping n={p['n']} avg={p['avg']} "
        f"p95={p['p95']} max={p['max']}; saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
