r"""Updates of the program through Velopack. The feed is a folder of static files on wuxianwow.com (FEED: releases.win.json,
the full and delta .nupkg; scripts\release.ps1 makes them). A copy installed by the Setup exe (%LOCALAPPDATA%\
WuxianWorkshop.App\current\wuxian.exe; the program's own files stay in %LOCALAPPDATA%\WuxianWorkshop) checks the feed
FIRST_CHECK seconds after the daemon starts and every CHECK_EVERY after that, downloads when asked, and applies the
update once the program exits: wait_exit_then_apply_updates starts Velopack's Update.exe, the daemon shuts down as on
退出, and Update.exe swaps the files and starts the new version. A copy run from a folder (dist\wuxian, a checkout) is
not installed: supported is False and nothing is checked.

Updater.status() -> {supported, reason, state, current, latest, size, notes, progress, error, checked}; state is one
of "idle", "checking", "current" (no newer version), "available", "downloading", "ready" (downloaded, applied at
the next exit), "error", "unsupported". WUXIAN_UPDATE_FEED overrides the feed (a URL or a local folder), for tests.
"""
import logging
import os
import threading
import time

FEED = "https://wuxianwow.com/workshop/releases"
CHECK_EVERY = 6 * 3600
FIRST_CHECK = 20
TIMEOUT_MS = 30_000

log = logging.getLogger(__name__)


def feed():
    """where the releases are: WUXIAN_UPDATE_FEED or FEED"""
    return os.environ.get("WUXIAN_UPDATE_FEED") or FEED


def velopack_manager(source):
    """Velopack's UpdateManager for the feed; raises when this copy was not installed by its Setup exe"""
    import velopack
    if source.startswith(("http://", "https://")):
        source = velopack.HttpSource(source, velopack.HttpOptions([], TIMEOUT_MS))
    return velopack.UpdateManager(source)


class Updater:
    """the update state of this copy; check / download / apply block (call them off the event loop)"""

    def __init__(self, source=None, manager=velopack_manager, clock=time.time, note=None):
        self.source = source or feed()
        self.note = note or (lambda text: log.info(text))
        self.clock = clock
        self.lock = threading.Lock()
        self.info = None                       # Velopack's UpdateInfo of the newest version found
        self.state, self.latest, self.size, self.notes = "idle", None, 0, ""
        self.progress, self.error, self.checked = 0, "", None
        try:
            self.um = manager(self.source)
            self.current = self.um.get_current_version()
            self.supported, self.reason = True, ""
        except Exception as e:                  # not installed by the Setup exe, or no velopack module
            self.um, self.current = None, None
            self.supported, self.reason = False, "不是用安装包装的（开发版或解压版），不能自动更新：" + _short(e)
            self.state = "unsupported"
            return
        try:
            pending = self.um.get_update_pending_restart()      # downloaded before the last exit, not applied yet
        except Exception:
            pending = None
        if pending is not None:
            self.info, self.state, self.latest = pending, "ready", pending.Version
            self.size, self.notes, self.progress = int(pending.Size or 0), (pending.NotesMarkdown or "").strip(), 100

    def status(self):
        with self.lock:
            return dict(supported=self.supported, reason=self.reason, state=self.state, current=self.current,
                        latest=self.latest, size=self.size, notes=self.notes, progress=self.progress, error=self.error,
                        checked=self.checked, feed=self.source)

    def _set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def check(self):
        """asks the feed for a newer version; the status after it"""
        if not self.supported or self.state in ("checking", "downloading"):
            return self.status()
        if self.state == "ready":              # downloaded already: waits for the restart
            return self.status()
        self._set(state="checking", error="")
        try:
            info = self.um.check_for_updates()
        except Exception as e:
            self._set(state="error", error="检查更新失败：" + _short(e), checked=self.clock())
            self.note(f"update check failed: {_short(e)}")
            return self.status()
        if info is None:
            self._set(state="current", info=None, latest=None, size=0, notes="", checked=self.clock())
            return self.status()
        target = info.TargetFullRelease
        self._set(state="available", info=info, latest=target.Version, size=int(target.Size or 0),
                  notes=(target.NotesMarkdown or "").strip(), checked=self.clock())
        self.note(f"update: {target.Version} is available ({self.size / 1048576:.1f} MB, running {self.current})")
        return self.status()

    def download(self):
        """downloads the version check() found (deltas when the feed has them); the status after it"""
        if not self.supported or self.state != "available" or self.info is None:
            return self.status()
        self._set(state="downloading", progress=0, error="")
        try:
            self.um.download_updates(self.info, lambda p: self._set(progress=int(p)))
        except Exception as e:
            self._set(state="error", error="下载更新失败：" + _short(e))
            self.note(f"update download failed: {_short(e)}")
            return self.status()
        self._set(state="ready", progress=100)
        self.note(f"update: {self.latest} downloaded; it is applied when the program restarts")
        return self.status()

    def apply_on_exit(self, background=False):
        """starts Velopack's Update.exe, which waits for this process to exit, puts the new version in place and starts
        it (background: as `wuxian app --background`, the window hidden; else with its window); the caller then shuts the
        program down. True if it was started"""
        if not self.supported or self.state != "ready" or self.info is None:
            return False
        self.um.wait_exit_then_apply_updates(self.info, silent=False, restart=True,
                                             restart_args=["app", "--background"] if background else None)
        self.note(f"update: restarting into {self.latest}")
        return True


class Scheduler:
    """calls check() in the background: `first` seconds after start, then every `every`; stop() ends it (the program's
    updates: Updater.check; the content packs: content.ContentUpdater.check)"""

    def __init__(self, check, first=FIRST_CHECK, every=CHECK_EVERY, name="wuxian-updates"):
        self.check, self.first, self.every, self.name = check, first, every, name
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self.thread.start()
        return self

    def _run(self):
        wait = self.first
        while not self.stopping.wait(wait):
            try:
                self.check()
            except Exception:
                log.exception("scheduled check")
            wait = self.every

    def stop(self):
        self.stopping.set()


def _short(e):
    text = str(e).strip() or type(e).__name__
    return text.splitlines()[0][:200]
