r"""The desktop shell: `wuxian` with no sub-command = daemon + tray icon + window.

    wuxian [app] [--background] [--mode developer|player] [--capture wgc|gdi] [--game DIR] [--fake [URL]] [--debug]

run_app() checks first for what the window needs from Windows, the WebView2 Runtime and the .NET Framework (webview2.py:
a missing one is offered for install, Microsoft's own installer, signature checked), hands over to a daemon that is
already running (POST /api/show makes it raise its window), else starts the daemon in this process (daemon.server.start
with window=True: a POST /api/show that comes before the window is up is kept for it, so that a second start meanwhile
opens no second window), opens its page in a pywebview window and puts an icon in the tray (tray.py). Closing the
window hides it (the closing handler returns False); "退出" in the tray menu sets the quit flag, destroys the window, stops
the daemon and then the tray icon, in that order. pywebview owns the main thread; the tray icon has its own thread and its
callbacks only call window.show() / hide() (marshalled onto the UI thread by pywebview) or set the flag.
--background (what the command line's clients start when no daemon answers: cli/client.py) makes the window hidden from
the start; the tray icon shows that the daemon runs, and opening the program shows the window. Without WebView2 it runs
the daemon alone instead of asking. It asks a running daemon only whether it answers (GET /api/status), never to show
its window, which may be hidden on purpose. A start that finds the single-instance mutex taken waits for that daemon to
answer (WINNER_WAIT: it is starting, and writes daemon.json once it listens), then hands over to it; when it does not
answer, or no daemon starts at all, a --background start logs that and exits instead of keeping an error page in a
hidden window. A daemon that runs without a window (`wuxian serve`) answers /api/show with shown=false: the shell then
opens a window on that daemon's page (AttachedBackend); its "退出" stops that daemon too, and the window closes when that
daemon stops. That is one window per daemon: it holds a named mutex, and a later start that finds the mutex taken asks
it to show itself through a named event (WindowSignal) instead of opening another.
--fake [URL] uses scripts/dev_fake_api.py instead of the daemon (started in this process when no URL is given) to develop
the page; the shell also falls back to it while the daemon cannot be imported, and otherwise shows a page that says the
daemon is not ready.
"""
import argparse
import ctypes
import importlib.util
import json
import logging
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .. import __version__, paths
from ..i18n import tr
from . import webview2

ROOT = Path(__file__).resolve().parents[3]       # the repository: scripts/dev_fake_api.py lives there (development only)
BACKGROUND = "#16171d"                                   # the page's --bg: no white flash while it loads
SIZE = (1200, 760)
MIN_SIZE = (960, 600)                                   # the layout: navigation, a list and its detail side by side
EXIT_NO_WEBVIEW2 = 3
WATCH_EVERY = 2.0                                # seconds between looks at an attached daemon
WINNER_WAIT = 10.0                               # seconds a start that lost the mutex waits for the winner to answer
WINNER_EVERY = 0.25                              # seconds between looks at it
WINDOW_NAME = r"Local\WuxianWorkshopWindow"      # + the port: this program's window on a daemon that has none
ERROR_ALREADY_EXISTS = 183
log = logging.getLogger(__name__)

ERROR_HTML = """<!doctype html><html lang="%s"><head><meta charset="utf-8"><meta name="color-scheme" content="light dark">
<title>%s</title><style>
body{font:15px/1.6 "Segoe UI","Microsoft YaHei UI",system-ui,sans-serif;margin:0;display:grid;place-items:center;height:100vh;
background:#f3f4f6;color:#1f2328}@media(prefers-color-scheme:dark){body{background:#0f1216;color:#e5e7eb}}
.box{max-width:560px;padding:32px}h1{font-size:20px;margin:0 0 12px}pre{white-space:pre-wrap;opacity:.75;font-size:12.5px}
</style></head><body><div class="box"><h1>%s</h1>
<p>%s</p>
<pre>%s</pre></div></body></html>"""


def title():
    return tr("无限工坊", "Wuxian Workshop")


def error_html(note):
    """the page the window shows when there is no daemon"""
    from html import escape
    return ERROR_HTML % (tr("zh-CN", "en"), title(), tr("守护进程未就绪", "The daemon is not ready"),
                         tr("无限工坊的守护进程没有启动，所以这里暂时没有内容。可以关掉窗口（它会留在托盘里），稍后从托盘菜单「打开」再试。",
                            "Wuxian Workshop's daemon did not start, so there is nothing here for now. Close the window (it "
                            "stays in the tray) and try again later with Open in the tray menu."), escape(note))


def dark_title_bar(hwnd):
    """asks DWM for the dark title bar (attribute 20; 19 before Windows 10 20H1) and, where Windows 11 takes them, the
    page's colours for the caption, its text and the border; an attribute this Windows does not know is refused, harmlessly"""
    dwm = ctypes.windll.dwmapi
    h, on = ctypes.c_void_p(hwnd), ctypes.c_int(1)
    if dwm.DwmSetWindowAttribute(h, 20, ctypes.byref(on), 4) != 0:
        dwm.DwmSetWindowAttribute(h, 19, ctypes.byref(on), 4)
    for attr, rgb in ((35, 0x16171d), (36, 0xe8e4d8), (34, 0x8a6424)):    # caption, caption text, border (Windows 11)
        colorref = ctypes.c_uint(((rgb & 0xff) << 16) | (rgb & 0xff00) | (rgb >> 16))         # 0x00BBGGRR
        dwm.DwmSetWindowAttribute(h, attr, ctypes.byref(colorref), 4)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="wuxian", description=__doc__.split("\n")[0])
    ap.add_argument("--background", action="store_true",
                    help="keep the window hidden (the tray icon shows); for a daemon started by a command-line client")
    ap.add_argument("--mode", choices=("developer", "player"), default=None,
                    help="the mode the daemon starts in (default: what the 设置 page saved, else developer)")
    ap.add_argument("--capture", choices=("wgc", "gdi"), default=None,
                    help="how the daemon reads the game window (default: what the 设置 page saved, else wgc)")
    ap.add_argument("--game", default=None, help="client folder that holds Interface\\AddOns")
    ap.add_argument("--fake", nargs="?", const="auto", default=None, metavar="URL",
                    help="use scripts/dev_fake_api.py instead of the daemon: its URL, or start it in this process")
    ap.add_argument("--debug", action="store_true", help="open the WebView2 developer tools and log more")
    ap.add_argument("--version", action="version", version=f"wuxian {__version__}")
    return ap.parse_args(sys.argv[1:] if argv is None else list(argv))


class AlreadyRunning(Exception):
    """another daemon holds the single-instance mutex; info is its daemon.json, or None"""

    def __init__(self, info=None):
        super().__init__("the daemon is already running")
        self.info = info


class RemoteBackend:
    """an API that runs in another process (--fake URL): nothing to stop"""

    def __init__(self, url):
        self.url = url if url.endswith("/") else url + "/"
        self.port = urllib.parse.urlparse(self.url).port
        self.token = None

    def stop(self):
        pass


def _request(info, method, path, timeout=2.0):
    """one call to the daemon daemon.json describes: the JSON answer, None when it does not answer"""
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}{path}", data=b"{}" if method == "POST" else None,
                                 method=method, headers={"Authorization": f"Bearer {info['token']}",
                                                         "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}") if 200 <= resp.status < 300 else None
    except (OSError, ValueError):                # nobody listens (a stale daemon.json), or not JSON
        return None


class WindowSignal:
    """this program's one window on a daemon that has none, across processes: the start that opens it holds a named
    mutex; a later start finds the mutex taken and sets a named event instead, which that window waits on to show
    itself. The event is made before the mutex is taken and let go before the mutex is: it is there whenever the mutex
    is (no set is lost on an event nobody holds), and the next window gets a new one (no set is taken by a window that
    has gone). A set before the window listens is kept (auto-reset: one show however many)"""

    def __init__(self, name):
        self.k32 = k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateEventW.restype = k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateEventW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p)
        k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        k32.WaitForMultipleObjects.restype = ctypes.c_uint32
        k32.WaitForMultipleObjects.argtypes = (ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32)
        k32.SetEvent.argtypes = k32.CloseHandle.argtypes = (ctypes.c_void_p,)
        self.name, self.mutex, self.listener, self.closed = name, None, None, False
        self.event = k32.CreateEventW(None, 0, 0, name + ".show")      # the window's, when it has one
        self.stop = k32.CreateEventW(None, 1, 0, None)                 # close() wakes the listener with it

    def take(self):
        """True when this process has the window now; False when another one has it, or is opening it"""
        h = self.k32.CreateMutexW(None, 0, self.name)
        if h and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self.k32.CloseHandle(h)
            return False
        self.mutex = h                           # none could be made: do not keep the window from opening
        return True

    def ask(self):
        """the window that holds the mutex shows itself"""
        if self.event:
            self.k32.SetEvent(self.event)

    def listen(self, callback):
        """callback() on its own thread for every ask, from now until close()"""
        if not (self.event and self.stop) or self.listener is not None:
            return
        handles = (ctypes.c_void_p * 2)(self.stop, self.event)          # the stop first: it wins when both are set

        def run():
            while not self.closed and self.k32.WaitForMultipleObjects(2, handles, 0, 0xFFFFFFFF) == 1:  # an ask
                try:
                    callback()
                except Exception:
                    log.exception("show the window")
        self.listener = threading.Thread(target=run, name="wuxian-window", daemon=True)
        self.listener.start()

    def close(self):
        """the window has gone: the listener stops, the event goes, then the mutex, for a later start's own window. A
        show that hangs past the join does not wait again (closed), so its handles may go"""
        self.closed = True
        if self.stop:
            self.k32.SetEvent(self.stop)
        if self.listener is not None:
            self.listener.join(5)
        for h in (self.event, self.stop, self.mutex):
            if h:
                self.k32.CloseHandle(h)
        self.event = self.stop = self.mutex = None


class AttachedBackend:
    """a daemon in another process that has no window (`wuxian serve`): this window shows its page. 退出 stops it, as
    it stops the daemon of this process; alive() tells the window when it has gone. claim() makes this the one such
    window on that daemon (False when another start's window is: that one is asked to show itself instead); a later
    start's ask calls on_show, as POST /api/show does on the daemon of this process"""

    def __init__(self, info):
        self.info = info
        self.port, self.token = info["port"], info["token"]
        self.url = info.get("url") or f"http://127.0.0.1:{self.port}/"
        self.signal = None
        self._on_show = None

    def claim(self):
        if sys.platform != "win32":                  # no named objects: nothing keeps a second window from opening
            return True
        signal = WindowSignal(f"{WINDOW_NAME}{self.port}")
        if signal.take():
            self.signal = signal
            return True
        signal.ask()
        signal.close()
        return False

    def release(self):
        if self.signal is not None:
            self.signal.close()
            self.signal = None

    @property
    def on_show(self):
        return self._on_show

    @on_show.setter
    def on_show(self, callback):
        """the window is there: asks show it from now on (one that came while it was opening is still set)"""
        self._on_show = callback
        if callback is not None and self.signal is not None:
            self.signal.listen(callback)

    def alive(self):
        return _request(self.info, "GET", "/api/status") is not None

    def stop(self):
        _request(self.info, "POST", "/api/quit")


def load_fake_api():
    """the scripts/dev_fake_api module; it is not part of the package, it lives in the repository"""
    path = ROOT / "scripts" / "dev_fake_api.py"
    if not path.is_file():
        raise ImportError(f"{path} not found")
    spec = importlib.util.spec_from_file_location("dev_fake_api", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def start_fake(url=None):
    """the fake API: a handle for the one at url, else one started in this process on a free port"""
    if url and url != "auto":
        return RemoteBackend(url)
    return load_fake_api().start_in_thread()


def start_backend(args):
    """(handle, note): the daemon (handle.url/port/token/stop()), or the fake API with a note that says why"""
    if args.fake:
        return start_fake(args.fake), ""
    try:
        from ..daemon import server
    except ImportError as e:                     # the daemon is not written yet: develop against the fake API
        log.warning("daemon not importable (%s); using the fake API", e)
        return start_fake(), tr(f"守护进程未就绪（{e}）", f"the daemon is not ready ({e})")
    try:
        handle = server.start(mode=args.mode, capture=args.capture, game_dir=args.game, window=True)
    except getattr(server, "AlreadyRunning", ()) as e:
        raise AlreadyRunning(getattr(e, "info", None)) from e
    return handle, ""


def running_instance(info=None, show=True):
    """(info, shown) of a daemon that is already running, None when none answers: info is its daemon.json (read here
    when not given). show: it is asked to show its window, and shown is True when its window took the request (one
    still coming up takes it too), False when it has no window; else (a --background start) it is only asked whether it
    answers, and shown is False: a window hidden on purpose must stay hidden"""
    if info is None:
        from ..daemon.api import read_daemon_json
        info = read_daemon_json()
    if not info or not info.get("port") or not info.get("token"):
        return None
    if not show:
        return None if _request(info, "GET", "/api/status") is None else (info, False)
    answer = _request(info, "POST", "/api/show")
    return None if answer is None else (info, bool(answer.get("shown")))


def wait_for_winner(info, show=True, wait=WINNER_WAIT, every=WINNER_EVERY):
    """running_instance() of the daemon that took the mutex first (start_backend raised AlreadyRunning), tried until it
    answers; None when it has not within wait seconds. It is normally just starting and writes daemon.json only once it
    listens: info (daemon.json when the mutex was found taken: None, or what a dead daemon left) is tried first, then
    daemon.json is read again for each try"""
    deadline = time.time() + wait
    while True:
        found = running_instance(info, show=show)
        if found is not None or time.time() >= deadline:
            return found
        time.sleep(every)
        info = None


def run_headless(args):
    """the daemon alone in this process (`wuxian serve`): a background start where no window can be made"""
    from ..daemon import server
    argv = [opt for key in ("mode", "capture", "game") if getattr(args, key) for opt in (f"--{key}", getattr(args, key))]
    return server.main(argv) or 0


SITE = "https://wuxianwow.com/"         # the only address the page may open outside the window


class PageApi:
    """what the page may ask of its window (window.pywebview.api): the native folder dialog the 开始 page uses to pick
    the game folder, and wuxianwow.com in the user's browser. pywebview exposes the public methods only; the shell
    stays in a private attribute"""

    def __init__(self, shell):
        self._shell = shell

    def open_url(self, url):
        """opens a page of wuxianwow.com in the default browser (nothing else: the page cannot send the user elsewhere)"""
        import webbrowser
        if not isinstance(url, str) or not url.startswith(SITE):
            return False
        return bool(webbrowser.open(url))

    def pick_folder(self, start=""):
        """the folder the user picked, "" when the dialog was cancelled"""
        import webview
        window = self._shell.window
        if window is None:
            return ""
        start = start if start and os.path.isdir(start) else ""
        picked = window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        if not picked:
            return ""
        return str(picked[0] if isinstance(picked, (list, tuple)) else picked)


class Shell:
    """the window, the tray icon and the quit sequence"""

    def __init__(self, backend, args, note=""):
        self.backend = backend
        self.args = args
        self.note = note
        self.window = None
        self.tray = None
        self.quitting = False

    def run(self):
        import webview
        from . import tray as traymod
        storage = paths.home() / "webview"            # WebView2's user data folder; private_mode would put it in %TEMP%
        storage.mkdir(parents=True, exist_ok=True)
        hidden = bool(getattr(self.args, "background", False))
        if self.backend is not None:
            self.window = webview.create_window(title(), self.backend.url, width=SIZE[0], height=SIZE[1], min_size=MIN_SIZE,
                                                text_select=True, hidden=hidden, background_color=BACKGROUND,
                                                js_api=PageApi(self))
        else:
            self.window = webview.create_window(title(), html=error_html(self.note), width=SIZE[0], height=SIZE[1],
                                                min_size=MIN_SIZE, text_select=True, background_color=BACKGROUND)
        self.window.events.closing += self.on_closing
        self.window.events.before_show += self.on_before_show
        self.tray = traymod.Tray(tr("无限工坊：用 AI 写魔兽插件", "Wuxian Workshop: write WoW addons with AI"), [
            traymod.Item(tr("打开", "Open"), self.show, default=True),
            traymod.Item(tr("开发台", "Console"), self.open_dev),
            traymod.Item(tr("打开日志文件夹", "Open the logs folder"), self.open_logs),
            traymod.Item(tr("检查更新", "Check for updates"), self.check_update),
            traymod.SEPARATOR,
            traymod.Item(tr("退出", "Quit"), self.quit),
        ])
        from . import icon as iconmod
        webview.start(self.after_start, private_mode=False, storage_path=str(storage), gui="edgechromium", debug=self.args.debug,
                      icon=str(iconmod.ICO_PATH) if iconmod.ICO_PATH.is_file() else None)
        self.shutdown()                               # the window is gone: "退出", or the error page was closed
        return 0

    def after_start(self):
        """runs on its own thread once the window is up: the tray icon, and the daemon's hook for /api/show"""
        try:
            self.tray.start()
        except Exception as e:
            log.warning("no tray icon: %s", e)
        if self.backend is not None and hasattr(self.backend, "on_show"):
            self.backend.on_show = self.show         # a second `wuxian` posts /api/show: raise the window
        if self.backend is not None and hasattr(self.backend, "on_quit"):
            self.backend.on_quit = self.quit         # POST /api/quit (or `wuxian quit`) stopped the daemon: close too
        if self.backend is not None and hasattr(self.backend, "alive"):
            threading.Thread(target=self.watch_backend, name="wuxian-attached", daemon=True).start()

    def watch_backend(self, every=WATCH_EVERY):
        """an attached daemon (another process) that stops, from its API or `wuxian quit`, closes this window too"""
        while not self.quitting:
            time.sleep(every)
            if not self.quitting and not self.backend.alive():
                log.info("the daemon this window shows has stopped")
                self.quit()
                return

    def on_before_show(self):
        """the title bar dark like the page (Windows 10 1809 and later; on Windows 11 also its colours), before it shows"""
        try:
            dark_title_bar(self.window.native.Handle.ToInt64())
        except Exception as e:                                  # an older Windows keeps the light one
            log.debug("no dark title bar: %s", e)

    def on_closing(self):
        """closing the window hides it; only the quit flag lets it close (and then this must return True, or destroy()
        hangs waiting for the window)"""
        if self.quitting:
            return True
        self.hide()
        return False

    def show(self):
        if self.window is None:
            return
        try:
            self.window.show()
            self.window.restore()
        except Exception:
            log.exception("show")

    def hide(self):
        if self.window is None:
            return
        try:
            self.window.hide()
        except Exception:
            log.exception("hide")

    def open_page(self, page):
        """the window, on one of its pages (from the tray)"""
        self.show()
        try:
            self.window.evaluate_js(f"window.wx && wx.go('{page}')")
        except Exception:
            log.exception("open page %s", page)

    def open_settings(self):
        self.open_page("settings")

    def open_dev(self):
        self.open_page("dev")

    def check_update(self):
        """the 设置 page, where the check's result shows, and the check itself (through the page: one route for the
        daemon in this process and an attached one)"""
        self.open_page("settings")
        try:
            self.window.evaluate_js("window.wx && wx.checkUpdate && wx.checkUpdate()")
        except Exception:
            log.exception("check update")

    def open_logs(self):
        try:
            os.startfile(paths.logs_dir())
        except OSError:
            log.exception("open logs folder")

    def quit(self):
        """from the tray: flag first, then destroy the window; run() stops the daemon and the icon after that"""
        if self.quitting:
            return
        self.quitting = True
        try:
            self.window.destroy()
        except Exception:
            log.exception("destroy")

    def shutdown(self):
        self.quitting = True
        if self.backend is not None:
            try:
                self.backend.stop()
            except Exception:
                log.exception("backend stop")
        if self.tray is not None:
            self.tray.stop()


def join_running(found, args):
    """what to do about the daemon found running: its window was shown, or there is nothing to start (--background):
    done (0); it has no window: open one on its page, unless another start has one open on it (or is opening it): that
    one is asked to show itself, done (0)"""
    info, shown = found
    if shown or args.background:
        return 0
    backend = AttachedBackend(info)
    if not backend.claim():
        log.info("the daemon at port %s has this program's window already: showing that one", info.get("port"))
        return 0
    log.info("the daemon at port %s runs without a window: showing its page", info.get("port"))
    try:
        return Shell(backend, args).run()
    finally:
        backend.release()


def run_app(argv=None):
    """the `wuxian` command without a sub-command (and `wuxian app`); returns the exit code"""
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    lacking = webview2.missing()
    if lacking:
        if args.background:                          # started for a client: no dialog, the daemon alone
            log.warning("missing for the window: %s; running the daemon without one", ", ".join(lacking))
            return run_headless(args)
        if not webview2.ensure(lacking):             # the user said no, or the install did not take
            return EXIT_NO_WEBVIEW2
    if not args.fake:
        found = running_instance(show=not args.background)
        if found is not None:
            return join_running(found, args)
    try:
        backend, note = start_backend(args)
    except AlreadyRunning as e:                      # lost the race for the mutex: that instance is starting; once it
        found = wait_for_winner(e.info, show=not args.background)    # answers, hand over to it
        if found is not None:
            return join_running(found, args)
        log.error("the daemon is already running but did not answer within %.0f s", WINNER_WAIT)
        backend, note = None, tr("另一个无限工坊已经在运行，但它没有响应；请先从托盘退出它。",
                                 "Another Wuxian Workshop is running but does not answer; quit it from its tray icon first.")
    except Exception as e:                           # neither the daemon nor the fake API: show an error page
        log.exception("no backend")
        backend, note = None, f"{type(e).__name__}: {e}"
    if backend is None and args.background:          # no error page in a window nobody sees, which would stay: the
        log.info("started in the background without a daemon: exiting")   # client that started this one waits for
        return 1                                     # a daemon itself and reports that none came up
    return Shell(backend, args, note).run()


main = run_app                                       # `wuxian app` through cli.main's PROGRAMS


if __name__ == "__main__":
    sys.exit(run_app())
