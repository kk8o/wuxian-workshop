r"""Kept versions of addons, the safety net under an agent's changes: an addon's files are saved before its code reaches
the game, and any saved version can be put back.

    %LOCALAPPDATA%\WuxianWorkshop\history\<addon>\index.json            the versions' summaries, oldest first
    %LOCALAPPDATA%\WuxianWorkshop\history\<addon>\versions\<id>.json    one version: its summary and its files,
                                                                         {path: [sha1, size, mtime_ns]}
    %LOCALAPPDATA%\WuxianWorkshop\history\<addon>\objects\<ab>\<sha1>    a file's content (zlib), kept once

save() makes a version. The daemon does when it hot-loads an addon or one of its files ("load"), when a watch starts
("watch": the files as they are before the agent edits them), when a watched file was saved and is loaded again
("save"), when an addon is made from a template ("new"), when asked (the checkpoint tool, the App's 存一份: "manual")
and before a restore ("restore": a restore is undone the same way). No version is made when the files are those of the
latest one; a file whose size and time stamp are the latest version's is not read again.

A version holds the files of the addon's folder and its sub-folders, except folders whose name starts with "." (.git and
the like) and files over MAX_FILE bytes (listed as skipped); an addon of more than MAX_FILES files or MAX_TOTAL bytes is
refused (TooBig). restore() writes back the version's files that differ and removes the files the version does not have
(only files a version could hold). KEEP versions stay per addon: beyond that the oldest automatic ones ("load", "save")
go first and the addon's first version stays; contents no version uses any more go with them. WoWBridge's own addons
are not kept (an install puts them back).
"""
import difflib
import hashlib
import json
import os
import shutil
import stat
import threading
import time
import zlib
from pathlib import Path

from ..paths import history_dir

KEEP = 100                      # versions per addon
MAX_FILE = 16 * 1024 * 1024     # bytes: a bigger file is not kept (skipped)
MAX_TOTAL = 256 * 1024 * 1024   # bytes of an addon's kept files
MAX_FILES = 20000
LIST_MAX = 50                   # paths a summary lists per kind of change
DIFF_LINES = 400                # lines of diff() text, all files together
REASONS = ("load", "watch", "save", "new", "manual", "restore")
AUTO = ("load", "save")         # what goes first beyond KEEP
OWN = ("wowbridge", "!wuxianworkshop")
TMP = ".wuxian-tmp"             # the suffix of a file being written back


class HistoryError(Exception):
    status, code = 400, "bad_request"


class NotFound(HistoryError):
    status, code = 404, "not_found"


class TooBig(HistoryError):
    status, code = 413, "too_big"


_guard = threading.Lock()
_locks = {}


def _lock(addon):
    with _guard:
        return _locks.setdefault(addon.casefold(), threading.RLock())


def check_name(addon):
    """the name of an addon folder directly in AddOns, or HistoryError"""
    if (not isinstance(addon, str) or not addon or addon != addon.strip() or addon.startswith(".")
            or any(c in addon for c in '/\\:*?"<>|')):
        raise HistoryError(f"{addon!r}: the name of an addon folder in AddOns")
    if addon.casefold() in OWN:
        raise HistoryError(f"{addon}: WoWBridge's own addons are not kept (wuxian install puts them back)")
    return addon


def _home(addon):
    return history_dir() / addon


# --- the files -----------------------------------------------------------------------------------------------------

def scan(folder):
    """({path: (size, mtime_ns)} of the files a version holds, posix paths relative to the folder; [(path, size)] of
    those over MAX_FILE)"""
    found, skipped = {}, []
    stack = [(Path(folder), "")]
    while stack:
        here, prefix = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        for e in entries:
            rel = prefix + e.name
            try:
                if e.is_symlink() or e.is_junction():
                    continue
                if e.is_dir():
                    if not e.name.startswith("."):
                        stack.append((Path(e.path), rel + "/"))
                    continue
                if e.name.endswith(TMP):
                    continue
                st = e.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE:
                skipped.append((rel, st.st_size))
                continue
            found[rel] = (st.st_size, st.st_mtime_ns)
            if len(found) > MAX_FILES:
                raise TooBig(f"{Path(folder).name}: more than {MAX_FILES} files, not kept")
    return found, sorted(skipped)


def _current(folder, base, home=None):
    """({path: [sha1, size, mtime_ns]} of the folder now, its skipped files): a file of the size and time stamp it has
    in `base` (a version's files) is taken to be that one; the others are read, and with `home` their contents kept"""
    found, skipped = scan(folder)
    total = sum(size for size, _ in found.values())
    if total > MAX_TOTAL:
        raise TooBig(f"{Path(folder).name}: {total // (1024 * 1024)} MB of files, more than the "
                     f"{MAX_TOTAL // (1024 * 1024)} MB kept per addon")
    files = {}
    for rel, (size, mtime) in found.items():
        b = base.get(rel)
        if b and b[1] == size and b[2] == mtime:
            files[rel] = list(b)
            continue
        try:
            data = (Path(folder) / rel).read_bytes()
        except OSError:                          # gone or locked meanwhile
            continue
        sha = _put(home, data) if home is not None else hashlib.sha1(data).hexdigest()
        files[rel] = [sha, len(data), mtime]
    return files, skipped


def _inside(folder, rel):
    root = Path(folder).resolve()
    p = (root / rel).resolve()
    if root not in p.parents:
        raise HistoryError(f"{rel}: not inside the addon folder")
    return p


def _writable(path):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _write_file(dest, data):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + TMP)
    try:
        tmp.write_bytes(data)
        try:
            os.replace(tmp, dest)
        except PermissionError:                  # a read-only file
            _writable(dest)
            os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def _remove_file(path):
    try:
        path.unlink()
    except PermissionError:
        _writable(path)
        path.unlink()


# --- the store -----------------------------------------------------------------------------------------------------

def _object(home, sha):
    return home / "objects" / sha[:2] / sha


def _put(home, data):
    sha = hashlib.sha1(data).hexdigest()
    path = _object(home, sha)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(sha + ".tmp")
        tmp.write_bytes(zlib.compress(data, 6))
        os.replace(tmp, path)
    return sha


def _get(home, sha):
    try:
        data = zlib.decompress(_object(home, sha).read_bytes())
    except (OSError, zlib.error) as e:
        raise HistoryError(f"the kept content {sha[:10]} is missing or damaged ({e})") from None
    if hashlib.sha1(data).hexdigest() != sha:
        raise HistoryError(f"the kept content {sha[:10]} is damaged")
    return data


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _manifest_path(home, vid):
    return home / "versions" / f"{vid:06d}.json"


def _summary(manifest):
    return {k: v for k, v in manifest.items() if k != "files"}


def _index(home):
    """{addon, next, versions: summaries oldest first}; made again from the versions' files when index.json is missing
    or does not name the same versions"""
    folder = home / "versions"
    ids = sorted(int(p.stem) for p in folder.glob("*.json") if p.stem.isdigit()) if folder.is_dir() else []
    idx = _read_json(home / "index.json")
    if isinstance(idx, dict) and [v.get("id") for v in idx.get("versions", [])] == ids and isinstance(idx.get("next"), int):
        return idx
    versions = []
    for vid in ids:
        m = _read_json(_manifest_path(home, vid))
        if isinstance(m, dict) and isinstance(m.get("files"), dict):
            versions.append(_summary(m))
    before = idx.get("next", 1) if isinstance(idx, dict) and isinstance(idx.get("next"), int) else 1
    idx = dict(addon=home.name, next=max([before] + [v + 1 for v in ids]), versions=versions)
    if ids:
        _write_json(home / "index.json", idx)
    return idx


def _manifest(home, vid):
    try:
        vid = int(vid)
    except (TypeError, ValueError):
        raise HistoryError(f"{vid!r}: a version number") from None
    m = _read_json(_manifest_path(home, vid))
    if not isinstance(m, dict) or not isinstance(m.get("files"), dict):
        raise NotFound(f"{home.name}: no kept version #{vid}")
    return m


def _changes(old, new):
    """(added, changed, removed): the sorted paths from one {path: [sha1, ...]} to another"""
    added = sorted(p for p in new if p not in old)
    removed = sorted(p for p in old if p not in new)
    changed = sorted(p for p in new if p in old and old[p][0] != new[p][0])
    return added, changed, removed


def _change_fields(added, changed, removed):
    return dict(added=added[:LIST_MAX], changed=changed[:LIST_MAX], removed=removed[:LIST_MAX],
                counts=dict(added=len(added), changed=len(changed), removed=len(removed)))


def _prune(home, idx):
    """beyond KEEP versions: the oldest automatic ones go, then the oldest of any kind (never the first one), and the
    contents no version left uses"""
    versions = idx["versions"]
    extra = len(versions) - KEEP
    if extra <= 0:
        return
    drop = [v["id"] for v in versions[1:] if v.get("reason") in AUTO][:extra]
    if len(drop) < extra:
        drop += [v["id"] for v in versions[1:] if v["id"] not in drop][:extra - len(drop)]
    gone = set(drop)
    idx["versions"] = [v for v in versions if v["id"] not in gone]
    _write_json(home / "index.json", idx)        # first: the index never names a version that is gone
    for vid in gone:
        try:
            _manifest_path(home, vid).unlink()
        except OSError:
            pass
    used = set()
    try:
        for v in idx["versions"]:
            used.update(f[0] for f in _manifest(home, v["id"])["files"].values())
    except HistoryError:                         # a version unreadable: no content is known to be unused
        return
    objects = home / "objects"
    for sub in (objects.iterdir() if objects.is_dir() else ()):
        for obj in (sub.iterdir() if sub.is_dir() else ()):
            if obj.name not in used:
                try:
                    obj.unlink()
                except OSError:
                    pass


# --- the operations ------------------------------------------------------------------------------------------------

def save(addons, addon, reason, note=""):
    """a version of the addon's files as they are now: its summary with new=True, or the latest version's with
    new=False when the files are that one's"""
    check_name(addon)
    if reason not in REASONS:
        raise HistoryError(f"reason: one of {', '.join(REASONS)}")
    folder = Path(addons) / addon
    if not folder.is_dir():
        raise NotFound(f"{addon}: no such addon folder in {addons}")
    with _lock(addon):
        return _save(folder, _home(addon), reason, note)


def _save(folder, home, reason, note):
    idx = _index(home)
    last = idx["versions"][-1] if idx["versions"] else None
    base = _manifest(home, last["id"])["files"] if last else {}
    files, skipped = _current(folder, base, home)
    if last is not None and {p: f[0] for p, f in files.items()} == {p: f[0] for p, f in base.items()}:
        return dict(last, new=False)
    vid = idx["next"]
    m = dict(id=vid, time=round(time.time(), 3), reason=reason, note=str(note or "")[:200], count=len(files),
             bytes=sum(f[1] for f in files.values()), **_change_fields(*_changes(base, files)),
             skipped=[dict(path=p, size=s) for p, s in skipped], files=files)
    _write_json(_manifest_path(home, vid), m)
    idx["versions"].append(_summary(m))
    idx["next"] = vid + 1
    _write_json(home / "index.json", idx)
    _prune(home, idx)
    return dict(_summary(m), new=True)


def versions(addons, addon):
    """{addon, versions: newest first, now}: now compares the addon's files with the latest version ({same, since,
    added, changed, removed, counts}), {missing: true} when its folder is gone, None without versions"""
    check_name(addon)
    home = _home(addon)
    with _lock(addon):
        idx = _index(home)
        out = dict(addon=addon, versions=list(reversed(idx["versions"])), now=None)
        if not idx["versions"] or addons is None:
            return out
        folder = Path(addons) / addon
        if not folder.is_dir():
            out["now"] = dict(missing=True)
            return out
        last = idx["versions"][-1]
        base = _manifest(home, last["id"])["files"]
        try:
            files, _ = _current(folder, base)
        except TooBig as e:
            out["now"] = dict(error=str(e))
            return out
        added, changed, removed = _changes(base, files)
        out["now"] = dict(same=not (added or changed or removed), since=last["id"], **_change_fields(added, changed, removed))
        return out


def summary(sizes=False):
    """[{addon, versions, latest, latest_id[, bytes]}] of every addon with kept versions; bytes = the space its
    contents take"""
    out = []
    root = history_dir()
    for home in sorted(p for p in root.iterdir() if p.is_dir()):
        with _lock(home.name):
            idx = _index(home)
        if not idx["versions"]:
            continue
        last = idx["versions"][-1]
        row = dict(addon=home.name, versions=len(idx["versions"]), latest=last["time"], latest_id=last["id"])
        if sizes:
            row["bytes"] = sum(p.stat().st_size for p in (home / "objects").rglob("*") if p.is_file())
        out.append(row)
    return out


def _text(data):
    """the text of a file's bytes (UTF-8, else GB18030), None for a binary file"""
    if b"\0" in data[:8192]:
        return None
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return None


def diff(addons, addon, vid, against="now", context=3, max_lines=DIFF_LINES):
    """what changed between two states of the addon: from version vid to its files now (against "now"), from the
    version before vid to vid ("prev"), or from vid to another version (its number). {addon, base, to, files: [{path,
    status: added / changed / removed, binary, plus, minus, diff: unified diff text, cut}], truncated}"""
    check_name(addon)
    home = _home(addon)
    with _lock(addon):
        idx = _index(home)
        b_files = None
        if against == "prev":
            target = _manifest(home, vid)
            ids = [v["id"] for v in idx["versions"]]
            older = [i for i in ids if i < target["id"]]
            a_files = _manifest(home, older[-1])["files"] if older else {}
            base, to = (older[-1] if older else None), target["id"]
            b_files = target["files"]
            read_b = lambda p: _get(home, b_files[p][0])            # noqa: E731
        else:
            a = _manifest(home, vid)
            a_files, base = a["files"], a["id"]
            if against in (None, "now"):
                folder = Path(addons) / addon if addons is not None else None
                b_files = _current(folder, a_files)[0] if folder is not None and folder.is_dir() else {}
                to = "now"
                read_b = lambda p: (folder / p).read_bytes()        # noqa: E731
            else:
                b = _manifest(home, against)
                b_files, to = b["files"], b["id"]
                read_b = lambda p: _get(home, b_files[p][0])        # noqa: E731
        added, changed, removed = _changes(a_files, b_files)
        rows = sorted([(p, "added") for p in added] + [(p, "changed") for p in changed] + [(p, "removed") for p in removed])
        files, budget, truncated = [], max_lines, False
        for path, status in rows:
            old = _get(home, a_files[path][0]) if status != "added" else b""
            try:
                new = read_b(path) if status != "removed" else b""
            except OSError:
                new = b""
            entry = dict(path=path, status=status)
            t_old, t_new = _text(old), _text(new)
            if t_old is None or t_new is None:
                entry.update(binary=True, old_size=len(old), new_size=len(new))
                files.append(entry)
                continue
            lines = list(difflib.unified_diff(t_old.splitlines(), t_new.splitlines(),
                                              f"#{base}/{path}" if base is not None else "/dev/null",
                                              f"{to if to == 'now' else '#' + str(to)}/{path}", n=context, lineterm=""))
            body = lines[2:]
            entry.update(binary=False, plus=sum(1 for l in body if l.startswith("+")),
                         minus=sum(1 for l in body if l.startswith("-")))
            shown = lines[:max(budget, 0)]
            entry["diff"] = "\n".join(shown)
            if len(shown) < len(lines):
                entry["cut"] = truncated = True
            budget -= len(shown)
            files.append(entry)
        return dict(addon=addon, base=base, to=to, files=files, truncated=truncated)


def restore(addons, addon, vid):
    """the addon's files back as version vid had them: the files as they are now are kept first (a version with reason
    "restore", or the latest one when they are that one's), then the version's files that differ are written and the
    files it does not have removed. {addon, restored, saved, written, removed, skipped}"""
    check_name(addon)
    home = _home(addon)
    folder = Path(addons) / addon
    with _lock(addon):
        target = _manifest(home, vid)
        for sha, _, _ in target["files"].values():          # every content is there before anything is touched
            if not _object(home, sha).is_file():
                raise HistoryError(f"{addon} #{target['id']}: the kept content {sha[:10]} is missing")
        if folder.is_dir():
            before = _save(folder, home, "restore", f"#{target['id']}")
            now = _manifest(home, before["id"])["files"]
        else:
            before, now = None, {}
            folder.mkdir(parents=True)
        written, removed = [], []
        for path, (sha, _, _) in sorted(target["files"].items()):
            if now.get(path, [None])[0] != sha:
                _write_file(_inside(folder, path), _get(home, sha))
                written.append(path)
        for path in sorted(now):
            if path not in target["files"]:
                p = _inside(folder, path)
                try:
                    _remove_file(p)
                except FileNotFoundError:
                    continue
                removed.append(path)
                d = p.parent
                while d != folder.resolve() and folder.resolve() in d.parents:
                    try:
                        d.rmdir()                         # only when empty
                    except OSError:
                        break
                    d = d.parent
        return dict(addon=addon, restored=target["id"], saved=before["id"] if before else None,
                    saved_new=bool(before and before["new"]), written=written, removed=removed,
                    skipped=[s["path"] for s in target.get("skipped", [])])


def forget(addon):
    """every kept version of the addon removed"""
    check_name(addon)
    home = _home(addon)
    with _lock(addon):
        if not home.is_dir():
            raise NotFound(f"{addon}: no kept versions")
        shutil.rmtree(home)
    return dict(addon=addon, forgotten=True)
