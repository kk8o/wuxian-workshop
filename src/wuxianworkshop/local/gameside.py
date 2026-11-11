r"""The game half of a local extension: the addon folders its manifest names ("addons": ["WuxianVoice"]), which lie in
<extension folder>/addons/<Name>/ and go into the game's Interface\AddOns when the player says so (the 扩展 page's
button, `wuxian ext install`, extension_install).

A folder in AddOns that this put there holds a mark, the file MARK:

    {"extension": "voice", "digest": "<the source's digest when it was installed>", "version": "<its .toc's version>"}

A folder of that name without the mark, or with another extension's, is someone else's addon: install() refuses it
(409 addon_exists) and remove() leaves it, whatever is in it. state() says where an addon stands: "current" (the mark's
digest is the source's), "differs" (ours, another digest: the source changed since), "missing", "foreign", "broken" (the
source cannot be installed: no folder, no .toc, a file that is no addon's kind), "listed" (its name is that of an addon
of the catalog, which no extension can bring: it is never installed, whatever AddOns holds) and "unknown" (no game
folder known).

install() checks every name before it touches anything, then writes each addon to a folder beside its place (the .toc
with the client's Interface number, the mark in it) and swaps that in, a version of what was there kept in the history
first (agent/history.py: `wuxian history` gives it back). What a package leaves out is no part of an addon here either
(pack.EXCLUDE and the names that start with a dot), so that an extension installs the same from its folder and from its
package; links are never followed.

The digest is of the source as the extension holds it, so a change of the client's Interface number alone, or a change
someone makes to the installed copy, does not turn "current" into "differs".
"""
import hashlib
import json
import os
import shutil
import time
from pathlib import Path, PurePosixPath

from .. import extensions as X
from ..installer import addons as A
from . import manifest as M
from . import pack as P

MARK = ".wuxian-extension.json"


class GameSideError(Exception):
    """an addon that cannot be installed: status and code as the API answers them (400 bad_addon, 409 addon_exists,
    409 addon_busy, 503 no_game_folder, 500 addon_failed); done: the rows of the addons installed before it failed"""

    def __init__(self, status, code, message, done=()):
        super().__init__(message)
        self.status, self.code, self.done = status, code, list(done)


def source(folder, name):
    """where an extension keeps the addon: <folder>/addons/<name>"""
    return Path(folder) / M.ADDONS_DIR / name


def _files(path):
    """[(posix path relative to the addon folder, Path)] of an addon's files, sorted: without what a package leaves
    out, the names that start with a dot (the mark is one) and the links"""
    return P.walk(path, lambda rel: P.left_out(rel, P.EXCLUDE) or P.hidden(rel))[0]


def _feed(h, rel, data):
    h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())


def digest(path):
    """the SHA-256 (hex) of an addon folder: every file's path relative to it and content, in the order of the paths;
    the mark, the other names that start with a dot and what a package leaves out do not count. OSError when a file
    cannot be read"""
    h = hashlib.sha256()
    for rel, p in _files(path):
        _feed(h, rel, p.read_bytes())
    return h.hexdigest()


def _is_link(path):
    return path.is_symlink() or path.is_junction()


def _folder_problem(name):
    if (not isinstance(name, str) or not M.ADDON_FOLDER.fullmatch(name) or P.part_problem(name)):
        return "not the name of an addon folder (letters, digits, _ . ! - , at most 64, no dot last)"
    return None


def name_problem(name, listed=None):
    """why an extension cannot put an addon folder of this name into AddOns, or None: no folder name, 无限工坊's own
    addons (and what starts like them), WuxianKit, the game's own (Blizzard_*), and the addons of the catalog (listed:
    what extensions.listed_folders() answers)"""
    why = _folder_problem(name)
    if why:
        return why
    low = name.lower()
    if low in {n.lower() for n in A.OURS} or low.startswith("wowbridge"):
        return "无限工坊's own addon"
    if low in M.ADDONS_TAKEN:
        return "WuxianKit's own folder"
    if low.startswith("blizzard_"):
        return "the name of an addon of the game itself"
    if listed and low in {str(n).lower() for n in listed}:
        return "an addon of the catalog: the 扩展 page installs it"
    return None


def source_problem(folder, name):
    """why the extension's addon cannot be installed as it is, or None: no folder name, no such folder in addons/ (a
    link is none), no .toc of its name, a file that is no addon's kind (extensions.KINDS), or it cannot be read"""
    why = _folder_problem(name)
    if why:
        return why
    src = source(folder, name)
    if _is_link(src) or not src.is_dir():
        return f"there is no folder {M.ADDONS_DIR}/{name} in the extension"
    toc = M.addon_toc(src)
    if toc is None:
        return f"{M.ADDONS_DIR}/{name} has no {name}.toc"
    try:
        files = _files(src)
    except OSError as e:
        return f"{M.ADDONS_DIR}/{name} cannot be read: {e}"
    if toc.name.lower() not in {rel.lower() for rel, _ in files}:
        return f"{M.ADDONS_DIR}/{name}/{toc.name} is a name a package leaves out"
    for rel, _ in files:
        if PurePosixPath(rel).suffix.lower() not in X.KINDS:
            return f"{M.ADDONS_DIR}/{name}/{rel} is not a kind of file an addon has ({', '.join(sorted(X.KINDS))})"
    return None


def _version(src):
    """the version the source's .toc says, or None"""
    try:
        toc = M.addon_toc(src) if src.is_dir() else None
        return ((A.read_toc(toc) or {}).get("Version") or None) if toc else None
    except OSError:
        return None


def _held(target, eid):
    """what AddOns holds at an addon's place: "missing", "foreign" (something without this extension's mark: a folder,
    a file, a link), or this extension's mark (a dict)"""
    if not os.path.lexists(target):
        return "missing"
    if _is_link(target) or not target.is_dir():
        return "foreign"
    try:
        mark = json.loads((target / MARK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "foreign"
    if not isinstance(mark, dict) or not isinstance(eid, str) or mark.get("extension") != eid:
        return "foreign"
    return mark


def state(folder, name, addons, eid, listed=None):
    """dict(folder, version, state) of one addon of the extension `eid` in `folder`; addons: the game's AddOns folder,
    or None; listed: the catalog's addon folders (extensions.listed_folders()), or None. version: what the source's
    .toc says (None: nothing). state: "listed" (the name is one of the catalog's: install() refuses it, with or
    without a game folder), "broken" (the source cannot be installed: source_problem), "unknown" (addons is None),
    "missing" (not in AddOns), "foreign" (there, not this extension's), "current" (this extension's, of the source's
    digest), "differs" (this extension's, another digest)"""
    if _folder_problem(name):
        return dict(folder=name, version=None, state="broken")
    src = source(folder, name)
    out = dict(folder=name, version=_version(src), state="broken")
    if listed and name.lower() in {str(n).lower() for n in listed}:
        return dict(out, state="listed")
    if source_problem(folder, name):
        return out
    if addons is None:
        return dict(out, state="unknown")
    held = _held(Path(addons) / name, eid)
    if isinstance(held, str):
        return dict(out, state=held)
    try:
        same = held.get("digest") == digest(src)
    except OSError:
        return out
    return dict(out, state="current" if same else "differs")


def states(folder, names, addons, eid, listed=None):
    """state() of each of the names, in their order"""
    return [state(folder, name, addons, eid, listed) for name in names]


def _clear(path):
    """a leftover of an earlier try (the folder beside an addon's place) gone; whether its place is free now"""
    if os.path.lexists(path) and not _is_link(path):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except OSError:
                pass
    return not os.path.lexists(path)


def _code(files):
    return {rel.lower() for rel in files if PurePosixPath(rel).suffix.lower() in X.CODE}


def _put(folder, name, addons, eid, interface, version):
    """one addon written beside its place and swapped in: (kept, restart). GameSideError when its place was taken
    meanwhile or the game holds the folder; AddOns is then as it was"""
    src, target = source(folder, name), addons / name
    held = _held(target, eid)
    if held == "foreign":                                           # it was ours, or missing, a moment ago
        raise GameSideError(409, "addon_exists", f"{name}: AddOns holds a folder of this name that is not this "
                                                 "extension's; it is left as it is")
    there = held != "missing"
    staging, backup = addons / f".{name}.wuxian-new", addons / f".{name}.wuxian-old"
    if not (_clear(staging) and _clear(backup)):
        raise GameSideError(409, "addon_busy", f"{name}: {staging.name} or {backup.name} in AddOns, left by an earlier "
                                               "try, cannot be removed: close the game and try again")
    try:
        before = _code(rel for rel, _ in P.walk(target)[0]) if there else set()
        h, new = hashlib.sha256(), []
        staging.mkdir()
        for rel, path in _files(src):
            data = path.read_bytes()
            _feed(h, rel, data)
            if rel.lower().endswith(".toc") and interface:
                data = A.toc_with_interface(data, interface)
            dest = staging.joinpath(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            new.append(rel)
        mark = dict(extension=eid, digest=h.hexdigest(), version=version or "")
        (staging / MARK).write_text(json.dumps(mark, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        kept = X._keep(addons, name, "update", f"{held.get('version') or '?'} -> {version or '?'}") if there else None
        X._swap(target, staging, backup)
    except PermissionError:
        _clear(staging)
        raise GameSideError(409, "addon_busy", f"{name}: the game holds a file of AddOns/{name} (or the folder cannot "
                                               "be written): it is left as it was. Close the game, or try again "
                                               "after a /reload") from None
    except OSError as e:
        _clear(staging)
        if os.path.lexists(backup) and not os.path.lexists(target):
            try:
                os.replace(backup, target)
            except OSError:
                pass
        raise GameSideError(500, "addon_failed", f"{name}: it could not be written into AddOns: {e}") from None
    return kept, not there or _code(new) != before


def install(folder, names, addons, eid, interface=None, listed=None):
    """the extension's addons `names` put into the AddOns folder `addons`: dict(addons=[dict(folder, state, was,
    version, kept)], restart). An addon that is current stays (state "current"); one that is missing or differs is
    installed (state "installed"; was: its state before; kept: the history's id of the version kept of what was there,
    or None). interface: the client's Interface number for the .toc files (None: as they are). listed: the catalog's
    addon folders (extensions.listed_folders()), which an extension cannot have. restart: the client sees it only
    when the game starts again (a new folder, or the set of .lua / .toc / .xml files changed). GameSideError: 503
    no_game_folder; 400 bad_addon (a name that cannot be, one twice, a source that is broken) and 409 addon_exists (a
    folder of the name that is not this extension's), both before anything is touched; 409 addon_busy and 500
    addon_failed while installing: that addon is as it was, the ones before it are installed (the error's done)"""
    if addons is None or not Path(addons).is_dir():
        raise GameSideError(503, "no_game_folder", "the game's AddOns folder is not known: start the game once, or "
                                                   "say where it is in the 无限工坊 App")
    addons = Path(addons)
    if isinstance(names, str) or not isinstance(eid, str) or not eid:
        raise GameSideError(400, "bad_addon", "names: the addon folders' names; eid: the extension's id")
    plan, seen = [], set()
    for name in names:
        why = name_problem(name, listed) or source_problem(folder, name)
        if not why and name.lower() in seen:
            why = "twice in the list"
        if why:
            raise GameSideError(400, "bad_addon", f"{name!r}: {why}")
        seen.add(name.lower())
        row = state(folder, name, addons, eid)
        if row["state"] == "foreign":
            raise GameSideError(409, "addon_exists", f"{name}: AddOns holds a folder of this name that is not this "
                                                     "extension's (it has no mark of it): it is left as it is. Move "
                                                     "it away yourself, or give the extension's addon another name")
        if row["state"] == "broken":                                # it cannot be read any more
            raise GameSideError(400, "bad_addon", f"{name!r}: {source_problem(folder, name) or 'it cannot be read'}")
        plan.append(row)
    out, restart = [], False
    for row in plan:
        name = row["folder"]
        if row["state"] == "current":
            out.append(dict(folder=name, state="current", was="current", version=row["version"], kept=None))
            continue
        try:
            kept, again = _put(folder, name, addons, eid, interface, row["version"])
        except GameSideError as e:
            e.done = out
            raise
        restart = restart or again
        out.append(dict(folder=name, state="installed", was=row["state"], version=row["version"], kept=kept))
    return dict(addons=out, restart=restart)


def remove(names, addons, eid):
    """the extension's addon folders taken out of AddOns, a version of each kept in the history first:
    [dict(folder, removed, kept, why)] in the names' order. Only a folder with this extension's mark goes; why says
    what kept another: None (removed), "missing", "foreign" (no mark, or another extension's: left as it is),
    "busy" (the game holds it: left as it is), "bad_name", "no_game_folder". Never raises for one of these"""
    out = []
    for name in ([] if isinstance(names, str) else names):
        row = dict(folder=name, removed=False, kept=None, why=None)
        out.append(row)
        if addons is None or not Path(addons).is_dir():
            row["why"] = "no_game_folder"
            continue
        if _folder_problem(name):
            row["why"] = "bad_name"
            continue
        addons = Path(addons)
        target, backup = addons / name, addons / f".{name}.wuxian-old"
        held = _held(target, eid)
        if isinstance(held, str):
            row["why"] = held
            continue
        if not _clear(backup):
            row["why"] = "busy"
            continue
        try:
            row["kept"] = X._keep(addons, name, "remove", str(held.get("version") or ""))
        except OSError:
            pass
        for _ in range(20):
            try:
                os.replace(target, backup)
                row["removed"] = True
                break
            except PermissionError:
                time.sleep(0.1)
            except OSError:
                break
        if row["removed"]:
            shutil.rmtree(backup, ignore_errors=True)
        else:
            row["why"] = "busy"
    return out
