r"""The game half of a local extension: the addon folders its manifest names ("addons": ["WuxianVoice"]), which lie in
<extension folder>/addons/<Name>/ and go into the game's Interface\AddOns when the player says so (the 扩展 page's
button, `wuxian ext install`, extension_install).

A folder in AddOns that this put there holds a mark, the file MARK:

    {"extension": "voice", "digest": "<the source's digest when it was installed>", "version": "<its .toc's version>"}

A folder of that name without the mark, or with another extension's, is someone else's addon: install() refuses it
(409 addon_exists) unless it is told to replace that very one, and remove() leaves it, whatever is in it. What is
replaced is said folder by folder: replace is a list [{folder, there}], and a folder is replaced only when it is in
the list and the version its .toc says is still `there` (what whoever said yes looked at). A folder without any mark
whose .toc says it is the same addon as the extension's (same_addon: the same ## X-WuxianKit-Extension, the same
## X-Website, or the same ## Title and ## Author) is most often one the player installed by hand before (from the web
site): the player's approval replaces those, unless one is newer than the extension's (replaces(): the list the
summary shows them); any other folder of the name only when the player names it (the App's 替换, `wuxian ext install
<id> --replace <folder>`). A link or a file of the name is never replaced. state() says where an addon stands: "current" (the mark's
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
    409 addon_busy, 503 no_game_folder, 500 addon_failed); done: the rows of the addons installed before it failed;
    data: more for the answer (addon_exists: foreign_data)"""

    def __init__(self, status, code, message, done=(), data=None):
        super().__init__(message)
        self.status, self.code, self.done, self.data = status, code, list(done), dict(data or {})


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


def same_addon(a, b):
    """whether two .toc's headers (read_toc's dicts) say they are the same addon: both name the same
    ## X-WuxianKit-Extension, or the same ## X-Website, or both the same ## Title and the same ## Author (none empty)"""
    a, b = a or {}, b or {}

    def of(toc, key):
        return str(toc.get(key) or "").strip()

    for key in ("X-WuxianKit-Extension", "X-Website"):
        if of(a, key) and of(a, key) == of(b, key):
            return True
    return all(of(a, key) and of(a, key) == of(b, key) for key in ("Title", "Author"))


def _numbers(version):
    """a version as a tuple of numbers ("1.2.10" -> (1, 2, 10); a "v" first is no part of it), or None when it is not
    only numbers and dots"""
    text = str(version or "").strip()
    text = text[1:] if text[:1] in ("v", "V") else text
    parts = text.split(".")
    if not text or not all(p.isascii() and p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def newer(there, version):
    """whether the version `there` is newer than `version`, number by number (1.10 is newer than 1.9, 1.2 the same as
    1.2.0); False when either cannot be told so (not only numbers and dots, or none)"""
    a, b = _numbers(there), _numbers(version)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _toc_of(folder):
    """an addon folder's .toc headers ({} when it has none or it cannot be read)"""
    try:
        toc = M.addon_toc(folder)
        return (A.read_toc(toc) or {}) if toc else {}
    except OSError:
        return {}


def _foreign(src, target, version):
    """(state) what a foreign row says more of the folder in AddOns: {} for a link or a file (never replaced), else
    dict(same, there[, newer: True]): same: it has no mark and its .toc says it is the extension's addon (same_addon);
    there: the version its .toc says, or None; newer: it is the same addon and there is newer than the extension's
    version (of another addon's version nothing is said: its numbers do not compare)"""
    if _is_link(target) or not target.is_dir():
        return {}
    theirs = _toc_of(target)
    same = not os.path.lexists(target / MARK) and same_addon(_toc_of(src), theirs)
    there = str(theirs.get("Version") or "").strip() or None
    out = dict(same=same, there=there)
    if same and newer(there, version):
        out["newer"] = True
    return out


def replaceable(row):
    """whether the foreign thing of this state() row can be replaced at all: a folder (a link or a file never)"""
    return row.get("state") == "foreign" and "same" in row


def replace_list(replace):
    """install()'s replace as it is used: [(folder name, casefolded; there: a text or None)]; None when it is not a
    list of {folder, there} (nothing given is the empty list)"""
    if replace is None:
        return []
    if not isinstance(replace, (list, tuple)):
        return None
    out = []
    for item in replace:
        if not isinstance(item, dict) or not isinstance(item.get("folder"), str) or not item["folder"] \
                or not (item.get("there") is None or isinstance(item["there"], str)):
            return None
        out.append((item["folder"].casefold(), item.get("there") or None))
    return out


def takes(row, replace):
    """whether install() with `replace` ([{folder, there}]) puts the extension's addon in the place of the foreign
    folder of this state() row: the list names the folder, and the version it says is still the list's there"""
    if not replaceable(row):
        return False
    return (str(row.get("folder")).casefold(), row.get("there") or None) in (replace_list(replace) or [])


def replaces(rows):
    """what the player's approval replaces, of an extension's state() rows: [{folder, there, same, newer}] of the
    foreign folders that are the same addon (installed by hand) and not newer than the extension's. An extension's
    summary shows the list, and install() takes it as its replace"""
    return [dict(folder=r["folder"], there=r.get("there"), same=True, newer=False)
            for r in rows if replaceable(r) and r.get("same") and not r.get("newer")]


def foreign_data(row):
    """what an addon_exists error says of the folder in AddOns (an App words it with these): {folder, same, there,
    newer}"""
    return dict(folder=row.get("folder"), same=bool(row.get("same")), there=row.get("there"),
                newer=bool(row.get("newer")))


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
    digest), "differs" (this extension's, another digest). A foreign folder (not a link, not a file) has two more:
    same (it has no mark, and its .toc says it is the same addon: same_addon) and there (the version its .toc says, or
    None), and newer: True when it is the same addon and there is newer than the source's version"""
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
    if held == "foreign":
        return dict(out, state=held, **_foreign(src, Path(addons) / name, out["version"]))
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


def _put(folder, name, addons, eid, interface, version, take=None):
    """one addon written beside its place and swapped in: (kept, restart). take: the version the foreign folder there
    says (state()'s there, "" for none) when it is to be replaced, else None. GameSideError when its place was taken
    meanwhile or the game holds the folder; AddOns is then as it was"""
    src, target = source(folder, name), addons / name
    held = _held(target, eid)
    taking = held == "foreign"
    if taking and (take is None or _is_link(target) or not target.is_dir()):    # it was ours or missing a moment ago
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
        if taking:
            kept = X._keep(addons, name, "replace", f"{take or '?'} -> {version or '?'}")
        elif there:
            kept = X._keep(addons, name, "update", f"{held.get('version') or '?'} -> {version or '?'}")
        else:
            kept = None
        X._swap(target, staging, backup)
    except X.NotKept as e:                                          # (a developer's checkout, with its .git)
        _clear(staging)
        raise GameSideError(409, "addon_exists" if taking else "addon_failed", f"{name}: {e}") from None
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


def install(folder, names, addons, eid, interface=None, listed=None, replace=None):
    """the extension's addons `names` put into the AddOns folder `addons`: dict(addons=[dict(folder, state, was,
    version, kept)], restart). An addon that is current stays (state "current"); one that is missing or differs is
    installed (state "installed"; was: its state before; kept: the history's id of the version kept of what was there,
    or None). replace: the foreign folders that are replaced, [{folder, there}] (None: none): one of the name is
    replaced when the list has it and the version it says is still there (takes(); a link or a file never); one
    replaced has was "foreign", the version that was there kept in the history first. interface: the client's Interface number for the .toc files
    (None: as they are). listed: the catalog's addon folders (extensions.listed_folders()), which an extension cannot
    have. restart: the client sees it only when the game starts again (a new folder, or the set of .lua / .toc / .xml
    files changed). GameSideError: 503 no_game_folder; 400 bad_addon (a name that cannot be, one twice, a source that
    is broken, a replace that is no list of {folder, there}) and 409 addon_exists (a folder of the name that is not this extension's, and not one to replace; the
    error's data: foreign_data), both before anything is touched; 409 addon_busy and 500 addon_failed while
    installing: that addon is as it was, the ones before it are installed (the error's done)"""
    if addons is None or not Path(addons).is_dir():
        raise GameSideError(503, "no_game_folder", "the game's AddOns folder is not known: start the game once, or "
                                                   "say where it is in the 无限工坊 App")
    addons = Path(addons)
    if isinstance(names, str) or not isinstance(eid, str) or not eid:
        raise GameSideError(400, "bad_addon", "names: the addon folders' names; eid: the extension's id")
    if replace_list(replace) is None:
        raise GameSideError(400, "bad_addon", "replace: the folders in AddOns to replace, [{folder, there}] (there: "
                                              "the version the folder says now, or null)")
    plan, seen = [], set()
    for name in names:
        why = name_problem(name, listed) or source_problem(folder, name)
        if not why and name.lower() in seen:
            why = "twice in the list"
        if why:
            raise GameSideError(400, "bad_addon", f"{name!r}: {why}")
        seen.add(name.lower())
        row = state(folder, name, addons, eid)
        if row["state"] == "foreign" and not takes(row, replace):
            raise GameSideError(409, "addon_exists", _exists(name, row, eid), data=foreign_data(row))
        if row["state"] == "broken":                                # it cannot be read any more
            raise GameSideError(400, "bad_addon", f"{name!r}: {source_problem(folder, name) or 'it cannot be read'}")
        plan.append(row)
    out, restart = [], False
    for row in plan:
        name = row["folder"]
        if row["state"] == "current":
            out.append(dict(folder=name, state="current", was="current", version=row["version"], kept=None))
            continue
        take = (row.get("there") or "") if row["state"] == "foreign" else None
        try:
            kept, again = _put(folder, name, addons, eid, interface, row["version"], take)
        except GameSideError as e:
            e.done = out
            raise
        restart = restart or again
        out.append(dict(folder=name, state="installed", was=row["state"], version=row["version"], kept=kept))
    return dict(addons=out, restart=restart)


def _exists(name, row, eid):
    """the message of the addon_exists of a foreign folder that is not replaced"""
    there = f" {row['there']}" if row.get("there") else ""
    if "same" not in row:
        return (f"{name}: AddOns holds a link or a file of this name, not this extension's: it is left as it is. "
                f"Move it away yourself, or give the extension's addon another name")
    if row.get("same") and row.get("newer"):
        return (f"{name}: AddOns holds the same addon in a newer version{there}, without this extension's mark "
                f"(installed by hand): it is left as it is")
    if row.get("same"):
        return (f"{name}: AddOns holds the same addon{there}, without this extension's mark (installed by hand): it "
                f"is left as it is. `wuxian ext install {eid} --replace` (the App's 替换) puts this extension's in "
                f"its place, the one there kept in the history")
    return (f"{name}: AddOns holds a folder of this name that is not this extension's{there}: it is left as it is. "
            f"Move it away yourself, give the extension's addon another name, or replace it (`wuxian ext install "
            f"{eid} --replace {name}`, the App's 替换: the one there is kept in the history)")


def marked(addons, eid):
    """the names of the folders in AddOns that carry this extension's mark, sorted ([]: none, or no such folder): what
    it installed there, whatever its manifest names now"""
    if addons is None or not isinstance(eid, str):
        return []
    try:
        names = sorted(p.name for p in Path(addons).iterdir())
    except OSError:
        return []
    return [n for n in names if not n.startswith(".") and not _folder_problem(n)
            and isinstance(_held(Path(addons) / n, eid), dict)]


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
