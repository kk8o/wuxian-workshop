r"""Content packs: data the program uses that changes more often than the program (first the API manual, the "api" pack;
apidocs.py reads it). Each pack is one gzip'd JSON file with "pack" and "version" at the top. The program carries one
copy of each (wuxianworkshop/data/<pack>.json.gz, made by scripts\build_api_pack.py); a newer one downloaded from the
website goes to %LOCALAPPDATA%\WuxianWorkshop\data\<pack>.json.gz, and whichever is newer is used.

The website lists the newest packs in MANIFEST (wuxianwow.com/workshop/data/manifest.json):
    {"packs": {"api": {"version": "2026.10.06.1", "file": "api-2026.10.06.1.json.gz", "size": 512345,
                       "sha256": "...", "min_app": "0.8.0"}}}
with the files beside it. ContentUpdater.check() reads it, downloads what is newer than what is here (size and SHA-256
checked, the JSON parsed, the pack and version it says matched) and replaces the cached file atomically. A failure keeps
what was there. WUXIAN_CONTENT_FEED overrides the manifest's URL (a URL or a local manifest.json), for tests.
"""
import gzip
import hashlib
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__
from .paths import home

MANIFEST = "https://wuxianwow.com/workshop/data/manifest.json"
BUNDLED = Path(__file__).resolve().parent / "data"
TIMEOUT = 30
MAX_PACK = 64 * 1024 * 1024

log = logging.getLogger(__name__)


def manifest_url():
    return os.environ.get("WUXIAN_CONTENT_FEED") or MANIFEST


def cache_dir():
    return home() / "data"


def version_key(v):
    """sorts "2026.10.06.1" < "2026.10.06.2" < "2026.10.7" (dot-separated numbers; text parts sort after numbers); no
    version at all sorts first"""
    if not v:
        return ()
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in str(v).split("."))


def read_pack(path):
    """the pack in a .json.gz file, or None when it is missing or broken"""
    try:
        with gzip.open(path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, EOFError):
        return None
    return data if isinstance(data, dict) and data.get("pack") and data.get("version") else None


class Packs:
    """the packs in use: get(name) is the newer of the bundled and the downloaded copy, read once and kept"""

    def __init__(self, bundled=BUNDLED, cached=None):
        self.bundled, self.cached = Path(bundled), Path(cached) if cached else None
        self.lock = threading.Lock()
        self.loaded = {}                       # name -> (pack, "bundled" | "downloaded")

    def cache(self):
        return self.cached or cache_dir()

    def get(self, name):
        with self.lock:
            if name not in self.loaded:
                self.loaded[name] = self._newest(name)
            return self.loaded[name][0]

    def source(self, name):
        self.get(name)
        return self.loaded[name][1]

    def _newest(self, name):
        found = []
        for where, folder in (("bundled", self.bundled), ("downloaded", self.cache())):
            pack = read_pack(folder / f"{name}.json.gz")
            if pack is not None and pack.get("pack") == name:
                found.append((version_key(pack["version"]), where, pack))
        if not found:
            return None, None
        _, where, pack = max(found, key=lambda x: x[0])
        return pack, where

    def version(self, name):
        pack = self.get(name)
        return pack["version"] if pack else None

    def replace(self, name, raw):
        """a downloaded pack (gzip bytes, already checked) becomes the cached copy and the one in use when newer"""
        folder = self.cache()
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{name}.json.gz.tmp"
        tmp.write_bytes(raw)
        os.replace(tmp, folder / f"{name}.json.gz")
        with self.lock:
            self.loaded.pop(name, None)

    def status(self):
        return {name: dict(version=self.version(name), source=self.source(name)) for name in ("api",)}


def fetch(url, limit=MAX_PACK):
    """the bytes at a URL (or a local path), at most limit"""
    if not urllib.parse.urlsplit(url).scheme.startswith("http"):
        return Path(url).read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": f"WuxianWorkshop/{__version__}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url}: larger than {limit} bytes")
    return data


class ContentUpdater:
    """checks the website's manifest for newer packs; status() for /api/status"""

    def __init__(self, packs, url=None, note=None, clock=time.time, fetcher=fetch):
        self.packs, self.url, self.clock, self.fetch = packs, url or manifest_url(), clock, fetcher
        self.note = note or (lambda text: log.info(text))
        self.checked, self.error, self.busy = None, "", False

    def status(self):
        return dict(packs=self.packs.status(), checked=self.checked, error=self.error, manifest=self.url)

    def check(self):
        """downloads every pack the manifest has newer than ours; the names of those it replaced"""
        if self.busy:
            return []
        self.busy, self.error, done = True, "", []
        try:
            manifest = json.loads(self.fetch(self.url, 1 << 20).decode("utf-8"))
            for name, entry in (manifest.get("packs") or {}).items():
                if not isinstance(entry, dict) or version_key(entry.get("version")) <= version_key(self.packs.version(name)):
                    continue
                if entry.get("min_app") and version_key(entry["min_app"]) > version_key(__version__.split(".dev")[0]):
                    continue                      # needs a newer program: the program's update brings it
                self._download(name, entry)
                done.append(name)
                self.note(f"content: {name} {entry['version']} downloaded")
        except Exception as e:
            from .i18n import tr
            why = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
            self.error = tr(f"内容包更新失败：{why}", f"the content pack update failed: {why}")
            self.note(f"content check failed: {e}")
        finally:
            self.checked, self.busy = self.clock(), False
        return done

    def _download(self, name, entry):
        base = self.url.rsplit("/", 1)[0] + "/"
        raw = self.fetch(urllib.parse.urljoin(base, entry["file"]) if "://" in base else str(Path(self.url).parent / entry["file"]))
        if entry.get("size") is not None and len(raw) != int(entry["size"]):
            raise ValueError(f"{entry['file']}: {len(raw)} bytes, the manifest says {entry['size']}")
        if hashlib.sha256(raw).hexdigest() != str(entry.get("sha256", "")).lower():
            raise ValueError(f"{entry['file']}: SHA-256 does not match the manifest")
        pack = json.loads(gzip.decompress(raw).decode("utf-8"))
        if pack.get("pack") != name or str(pack.get("version")) != str(entry["version"]):
            raise ValueError(f"{entry['file']}: holds {pack.get('pack')} {pack.get('version')}, not {name} {entry['version']}")
        self.packs.replace(name, raw)


PACKS = Packs()                                  # the program's packs (one per process)
