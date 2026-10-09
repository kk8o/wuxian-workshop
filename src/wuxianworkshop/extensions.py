r"""The extension catalog (wuxianwow.com/workshop/data/extensions.json): addons that work with WuxianKit, the
optional extension framework (WuxianKit itself first), which the 扩展 page installs, updates and removes. Its versions are its own,
not the program's.

    {"format": 1, "updated": "...", "addons": [{"id": "wuxiankit", "folder": "WuxianKit", "version": "0.3.0",
      "title": {"zh": ..., "en": ...}, "summary": {"zh", "en"}, "file": "WuxianKit-0.3.0.zip", "size": 82675,
      "sha256": "...", "min_app": "0.9.7", "protocol": 1, "extensions": [{"id", "title": {"zh", "en"}, "tools", "events"}],
      "skills": [{"name", "extension", "description"}], "notes": {"zh", "en"}, "released": "2026-10-10", "homepage": ...}]}

The files sit beside the catalog (a versioned name is never given other contents). An install downloads the zip (its size
and SHA-256 those of the catalog), checks every member (inside the addon's folder, no "..", only the kinds of file an
addon has, MAX_UNPACKED bytes at most) and that its .toc is of the catalog's version, keeps the folder as it was (a version
in agent/history.py, reason "update": it can be put back), writes the new one beside it, swaps the two and makes the
.toc's Interface the client's, as for the bundled addons. A removal keeps a version too ("remove"), then the folder goes.
WUXIAN_EXTENSIONS_FEED overrides the catalog's URL (a URL or a local extensions.json): for tests, and to try a catalog
before it is uploaded.
"""
import hashlib
import io
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path, PurePosixPath

from . import __version__
from .agent import history
from .content import fetch, version_key
from .installer.addons import find_toc, read_toc, toc_with_interface

CATALOG = "https://wuxianwow.com/workshop/data/extensions.json"
MAX_CATALOG = 1 << 20
MAX_ZIP = 32 * 1024 * 1024
MAX_UNPACKED = 64 * 1024 * 1024
MAX_FILES = 4000
KINDS = {".lua", ".toc", ".xml", ".tga", ".blp", ".md", ".txt", ".ttf", ".otf", ".ogg", ".mp3", ".wav", ".json"}
CODE = {".lua", ".toc", ".xml"}                     # the files the client finds only when it starts
ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
FOLDER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.!-]{0,63}$")
SHA = re.compile(r"^[0-9a-f]{64}$")


class CatalogError(Exception):
    pass


def catalog_url():
    return os.environ.get("WUXIAN_EXTENSIONS_FEED") or CATALOG


def app_version():
    return __version__.split(".dev")[0]


def entries(data):
    """the catalog's addons that are whole: id, folder, version, file, size and sha256 as they must be"""
    out = []
    for e in (data or {}).get("addons") or [] if isinstance(data, dict) else []:
        if (isinstance(e, dict) and isinstance(e.get("id"), str) and ID.match(e["id"])
                and isinstance(e.get("folder"), str) and FOLDER.match(e["folder"])
                and isinstance(e.get("version"), str) and e["version"]
                and isinstance(e.get("file"), str) and e["file"] and not e["file"].startswith(("/", "\\"))
                and ".." not in e["file"].replace("\\", "/").split("/")
                and isinstance(e.get("size"), int) and 0 < e["size"] <= MAX_ZIP
                and isinstance(e.get("sha256"), str) and SHA.match(e["sha256"].lower())):
            out.append(e)
    return out


class Catalog:
    """the catalog as last read; refresh() reads it again (blocking: run it in a thread)"""

    def __init__(self, url=None, fetcher=fetch, clock=time.time):
        self.url, self.fetch, self.clock = url or catalog_url(), fetcher, clock
        self.addons, self.updated, self.checked, self.error, self.missing = [], None, None, "", False

    def refresh(self):
        """reads the catalog again; on failure what was read before stands, and error says why in the page's words
        (missing: there is no catalog at its address, which is not a fault)"""
        from .i18n import tr
        self.missing = False
        try:
            data = json.loads(self.fetch(self.url, MAX_CATALOG).decode("utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("addons"), list):
                raise CatalogError("not an extension catalog")
            self.addons, self.updated, self.error = entries(data), data.get("updated"), ""
        except (urllib.error.HTTPError, FileNotFoundError) as e:
            code = getattr(e, "code", 404)
            self.missing = code == 404
            self.error = (tr("网站上还没有扩展目录。", "The website has no extension catalog yet.") if self.missing else
                          tr(f"扩展目录读取失败：网站返回 {code}", f"The extension catalog could not be read: the website answered {code}"))
        except urllib.error.URLError:
            self.error = tr("连不上无限工坊网站，稍后点「刷新目录」再试。",
                            "Cannot reach the Wuxian Workshop website; try Refresh again later.")
        except Exception as e:
            why = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
            self.error = tr(f"扩展目录读取失败：{why}", f"The extension catalog could not be read: {why}")
        finally:
            self.checked = self.clock()
        return self

    def get(self, ext_id):
        return next((e for e in self.addons if e["id"] == ext_id), None)

    def status(self):
        return dict(url=self.url, updated=self.updated, checked=self.checked, error=self.error, missing=self.missing)

    def download(self, entry):
        """the entry's zip, its size and SHA-256 checked"""
        if "://" in self.url:
            where = urllib.parse.urljoin(self.url.rsplit("/", 1)[0] + "/", entry["file"])
        else:
            where = str(Path(self.url).parent / entry["file"])
        raw = self.fetch(where, MAX_ZIP)
        if len(raw) != entry["size"]:
            raise CatalogError(f"{entry['file']}: {len(raw)} bytes, the catalog says {entry['size']}")
        if hashlib.sha256(raw).hexdigest() != entry["sha256"].lower():
            raise CatalogError(f"{entry['file']}: its SHA-256 is not the catalog's")
        return raw


def installed(addons, folder):
    """(there, version) of an addon folder: whether it is there, and the version its .toc says (None: none)"""
    path = Path(addons) / folder
    if not path.is_dir():
        return False, None
    toc = find_toc(path)
    return True, ((read_toc(toc) or {}).get("Version") or None) if toc else None


def state_of(entry, there, version):
    """what the page offers: needs_app (a newer program first), available, update, current, newer (the folder holds a
    later version than the catalog's: a developer's), installed (a version it cannot tell)"""
    if entry.get("min_app") and version_key(entry["min_app"]) > version_key(app_version()):
        return "needs_app"
    if not there:
        return "available"
    if not version:
        return "installed"
    mine, theirs = version_key(version), version_key(entry["version"])
    return "update" if theirs > mine else "current" if theirs == mine else "newer"


def unpack(raw, folder):
    """the zip's files of the folder, {relative path: bytes}, every member checked"""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise CatalogError(f"not a zip: {e}") from None
    with z:
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise CatalogError(f"{len(infos)} entries, more than {MAX_FILES}")
        files, total = {}, 0
        for info in infos:
            name = info.filename
            if info.is_dir():
                continue
            parts = name.split("/")
            if ("\\" in name or name.startswith("/") or ".." in parts or ":" in name or len(parts) < 2
                    or parts[0] != folder or not all(parts)):
                raise CatalogError(f"{name}: not a file of {folder}/")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise CatalogError(f"{name}: a link")
            if PurePosixPath(name).suffix.lower() not in KINDS:
                raise CatalogError(f"{name}: not a kind of file an addon has")
            total += info.file_size
            if total > MAX_UNPACKED:
                raise CatalogError(f"more than {MAX_UNPACKED} bytes unpacked")
            files["/".join(parts[1:])] = z.read(info)
    if f"{folder}.toc" not in files:
        raise CatalogError(f"no {folder}/{folder}.toc in it")
    return files


def toc_version(data):
    m = re.search(rb"^##\s*Version\s*:\s*(.+?)\s*$", data, re.M)
    return m.group(1).decode("utf-8", "replace") if m else None


def _swap(target, staging, backup):
    """the new folder in the old one's place: the old one aside, the new one in, the old one removed. The game may hold a
    file a moment: a refused rename is tried again for about two seconds"""
    for attempt in range(20):
        try:
            if target.exists():
                os.replace(target, backup)
            os.replace(staging, target)
            break
        except PermissionError:
            if attempt == 19:
                if backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise
            time.sleep(0.1)
    shutil.rmtree(backup, ignore_errors=True)


def _keep(addons, folder, reason, note):
    try:
        return history.save(addons, folder, reason, note).get("id")
    except history.HistoryError:                          # too big to keep, say: it goes on without
        return None


def install(addons, entry, raw, interface=None):
    """the catalog's addon from its zip's bytes, in place of what the folder held. {id, folder, version, was, kept,
    restart}: restart = the client finds it only when the game starts again (a new addon, or code files added)"""
    addons, folder = Path(addons), entry["folder"]
    files = unpack(raw, folder)
    found = toc_version(files[f"{folder}.toc"])
    if found != entry["version"]:
        raise CatalogError(f"its .toc says version {found}, the catalog {entry['version']}")
    target = addons / folder
    there, was = installed(addons, folder)
    before = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()} if there else set()
    kept = _keep(addons, folder, "update", f"{was or '?'} -> {entry['version']}") if there else None
    staging, backup = addons / f".{folder}.wuxian-new", addons / f".{folder}.wuxian-old"
    for leftover in (staging, backup):
        shutil.rmtree(leftover, ignore_errors=True)
    for rel, data in files.items():
        dest = staging.joinpath(*rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel.lower().endswith(".toc") and interface:
            data = toc_with_interface(data, interface)
        dest.write_bytes(data)
    _swap(target, staging, backup)
    added = {rel for rel in files if PurePosixPath(rel).suffix.lower() in CODE} - before
    return dict(id=entry["id"], folder=folder, version=entry["version"], was=was if there else None, kept=kept,
                restart=not there or bool(added))


def remove(addons, entry):
    """the addon's folder gone, a version of it kept first. {id, folder, removed, kept}"""
    addons, folder = Path(addons), entry["folder"]
    target = addons / folder
    if not target.is_dir():
        return dict(id=entry["id"], folder=folder, removed=False, kept=None)
    kept = _keep(addons, folder, "remove", installed(addons, folder)[1] or "")
    backup = addons / f".{folder}.wuxian-old"
    shutil.rmtree(backup, ignore_errors=True)
    os.replace(target, backup)
    shutil.rmtree(backup, ignore_errors=True)
    return dict(id=entry["id"], folder=folder, removed=True, kept=kept)
