r"""Updates of the program through Velopack. The feed is a folder of static files on wuxianwow.com (FEED: releases.win.json,
the full and delta .nupkg; scripts\pack.ps1 makes them). A copy installed by the Setup exe (%LOCALAPPDATA%\
WuxianWorkshop.App\current\wuxian.exe; the program's own files stay in %LOCALAPPDATA%\WuxianWorkshop) checks the feed
FIRST_CHECK seconds after the daemon starts and every CHECK_EVERY after that, downloads when asked, and applies the
update once the program exits: wait_exit_then_apply_updates starts Velopack's Update.exe, the daemon shuts down as on
退出, and Update.exe swaps the files and starts the new version. A copy run from a folder (dist\wuxian, a checkout) is
not installed: supported is False and nothing is checked.

Only a signed release is taken (signing.py). check() reads the feed's releases.<channel>.json with its signature beside
Velopack and offers the version Velopack found only when it is the signed entry: the same file name, size and SHA-256.
Velopack checks the download by these, and the package (packages\<file name> in the install folder) is hashed again
after the download and before Update.exe applies it. The signed entry of a downloaded version is kept (state\update.json):
a download from before the last exit is ready only when it is the one that was checked. A local feed is read unsigned.

Updater.status() -> {supported, reason, state, current, latest, size, notes, progress, error, checked}; state is one
of "idle", "checking", "current" (no newer version), "available", "downloading", "ready" (downloaded, applied at
the next exit), "error", "unsupported". WUXIAN_UPDATE_FEED overrides the feed (a URL or a local folder), for tests.
"""
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

from .content import fetch
from .i18n import tr
from .paths import state_dir
from .signing import BadSignature, fetch_signed, remote

FEED = "https://wuxianwow.com/workshop/releases"
CHECK_EVERY = 6 * 3600
FIRST_CHECK = 20
TIMEOUT_MS = 30_000
MAX_FEED = 4 * 1024 * 1024             # releases.<channel>.json: every version's notes, some tens of KB
KEPT = "update.json"                   # in the state folder: the signed entry of the version downloaded
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.nupkg$")
SHA256 = re.compile(r"^[0-9A-F]{64}$")

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


def install_root():
    r"""the folder Velopack installed this copy into (…\WuxianWorkshop.App: Update.exe, current\wuxian.exe, packages\)"""
    return Path(sys.executable).resolve().parent.parent


def channel(root):
    r"""the channel this copy was packed for (scripts\pack.ps1 -Channel) as its manifest says; "win" when it says none"""
    try:
        m = re.search(r"<channel>\s*([A-Za-z0-9._-]+)\s*</channel>",
                      (Path(root) / "current" / "sq.version").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        m = None
    return m.group(1) if m else "win"


def signed_entry(source, version, chan, fetcher=fetch):
    """the full package of `version` in the feed's releases.<chan>.json, read with its signature (a local folder's
    unsigned): {Version, FileName, Size, SHA256}. BadSignature; ValueError when the feed does not list it whole"""
    name = f"releases.{chan}.json"
    where = source.rstrip("/") + "/" + name if remote(source) else str(Path(source) / name)
    data = json.loads(fetch_signed(where, MAX_FEED, fetcher).decode("utf-8-sig"))
    for a in (data.get("Assets") or []) if isinstance(data, dict) else []:
        if isinstance(a, dict) and a.get("Type") == "Full" and a.get("Version") == version:
            entry = dict(Version=version, FileName=a.get("FileName"), Size=a.get("Size"), SHA256=str(a.get("SHA256") or "").upper())
            if (isinstance(entry["FileName"], str) and PACKAGE.match(entry["FileName"]) and isinstance(entry["Size"], int)
                    and entry["Size"] > 0 and SHA256.match(entry["SHA256"])):
                return entry
            raise ValueError(f"{name}: {version} has no proper file name, size or SHA-256")
    raise ValueError(f"{name} does not list {version}")


def is_entry(asset, entry):
    """whether a Velopack asset is the signed entry: the same version, file, size and SHA-256"""
    return (asset.Version == entry["Version"] and asset.FileName == entry["FileName"]
            and int(asset.Size or 0) == entry["Size"] and str(asset.SHA256 or "").upper() == entry["SHA256"])


def check_package(path, entry):
    """ValueError unless the file at path is the signed entry's package (its size and SHA-256); OSError when unread"""
    h, size = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
            size += len(chunk)
    if size != entry["Size"] or h.hexdigest().upper() != entry["SHA256"]:
        raise ValueError(f"{Path(path).name} is not the package the signed feed lists")


class Updater:
    """the update state of this copy; check / download / apply block (call them off the event loop). root: the install
    folder (install_root()); store: the signed entry of a download (state\\update.json)"""

    def __init__(self, source=None, manager=velopack_manager, clock=time.time, note=None, fetcher=fetch, root=None,
                 store=None):
        self.source = source or feed()
        self.note = note or (lambda text: log.info(text))
        self.clock, self.fetch = clock, fetcher
        self.lock = threading.Lock()
        self.info = None                       # Velopack's UpdateInfo of the newest version found
        self.signed = None                     # the signed entry of that version (signed_entry)
        self.state, self.latest, self.size, self.notes = "idle", None, 0, ""
        self.progress, self.error, self.checked = 0, "", None
        try:
            self.um = manager(self.source)
            self.current = self.um.get_current_version()
            self.supported, self.reason = True, ""
        except Exception as e:                  # not installed by the Setup exe, or no velopack module
            self.um, self.current = None, None
            self.supported, self.reason = False, tr("不是用安装包装的（开发版或解压版），不能自动更新：",
                                                    "not installed by the setup program (a development or portable copy), so no "
                                                    "automatic updates: ") + _short(e)
            self.state = "unsupported"
            return
        self.root = Path(root) if root else install_root()
        self.store = Path(store) if store else state_dir() / KEPT
        self.channel = channel(self.root)
        try:
            pending = self.um.get_update_pending_restart()      # downloaded before the last exit, not applied yet
        except Exception:
            pending = None
        kept = self.kept() if pending is not None else None
        if kept and (pending.Version, pending.FileName) == (kept["Version"], kept["FileName"]):
            self.info, self.signed, self.state, self.latest = pending, kept, "ready", pending.Version
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
            self._set(state="error", error=tr("检查更新失败：", "the update check failed: ") + _short(e), checked=self.clock())
            self.note(f"update check failed: {_short(e)}")
            return self.status()
        if info is None:
            self._set(state="current", info=None, signed=None, latest=None, size=0, notes="", checked=self.clock())
            return self.status()
        target = info.TargetFullRelease
        try:
            entry = signed_entry(self.source, target.Version, self.channel, self.fetch)
            if not is_entry(target, entry):
                raise ValueError(f"{target.FileName} is not the package the signed feed lists for {target.Version}")
        except Exception as e:
            unsigned = isinstance(e, (BadSignature, ValueError))
            self._set(state="error", info=None, signed=None, checked=self.clock(),
                      error=(tr("更新未通过签名校验：", "the update failed its signature check: ") if unsigned else
                             tr("检查更新失败：", "the update check failed: ")) + _short(e))
            self.note(f"update check: {target.Version} not taken: {_short(e)}")
            return self.status()
        self._set(state="available", info=info, signed=entry, latest=target.Version, size=int(target.Size or 0),
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
            self._set(state="error", error=tr("下载更新失败：", "the update download failed: ") + _short(e))
            self.note(f"update download failed: {_short(e)}")
            return self.status()
        try:
            self.check_package()
        except Exception as e:
            self._set(state="error", info=None, signed=None,
                      error=tr("更新包未通过校验：", "the update package failed its check: ") + _short(e))
            self.note(f"update: the download of {self.latest} is not taken: {_short(e)}")
            return self.status()
        self.keep()
        self._set(state="ready", progress=100)
        self.note(f"update: {self.latest} downloaded; it is applied when the program restarts")
        return self.status()

    def apply_on_exit(self, background=False):
        """starts Velopack's Update.exe, which waits for this process to exit, puts the new version in place and starts
        it (background: as `wuxian app --background`, the window hidden; else with its window); the caller then shuts the
        program down. True if it was started; False also when the package is not the signed one (state "error")"""
        if not self.supported or self.state != "ready" or self.info is None:
            return False
        try:
            self.check_package()
        except Exception as e:
            self._set(state="error", info=None, signed=None,
                      error=tr("更新包未通过校验：", "the update package failed its check: ") + _short(e))
            self.note(f"update: {self.latest} not applied: {_short(e)}")
            return False
        self.um.wait_exit_then_apply_updates(self.info, silent=False, restart=True,
                                             restart_args=["app", "--background"] if background else None)
        self.note(f"update: restarting into {self.latest}")
        return True

    def check_package(self):
        """the downloaded package (packages\\<file name> in the install folder) is the signed one; ValueError when it
        is not, and it is removed (Velopack, finding it there, would not download it again), OSError when unread"""
        path = self.root / "packages" / self.signed["FileName"]
        try:
            check_package(path, self.signed)
        except ValueError:
            for gone in (path, self.store):
                try:
                    gone.unlink()
                except OSError:
                    pass
            raise

    def keep(self):
        """the signed entry of the version downloaded, for a start before it is applied; unwritten, that start checks again"""
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store.with_name(self.store.name + ".tmp")
            tmp.write_text(json.dumps(self.signed), encoding="utf-8")
            os.replace(tmp, self.store)
        except OSError as e:
            self.note(f"update: the checked entry was not kept: {_short(e)}")

    def kept(self):
        """the entry keep() wrote, None when there is none whole"""
        try:
            e = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (isinstance(e, dict) and isinstance(e.get("Version"), str) and isinstance(e.get("FileName"), str)
                and PACKAGE.match(e["FileName"]) and isinstance(e.get("Size"), int) and SHA256.match(str(e.get("SHA256")))):
            return e
        return None


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
