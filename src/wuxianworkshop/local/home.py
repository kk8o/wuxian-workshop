r"""The extensions' home folder, paths.extensions_home() (<WUXIAN_HOME>\extensions): the local extensions the workshop
keeps itself, one folder each.

    <home>\<name>\wuxian-extension.json     an extension: found there without being registered (found; host.py discover)
    <home>\.removed\<id>-<YYYYMMDD-HHMMSS>  the folders taken away (trash: an extension removed, or replaced by another
                                            version), the newest KEPT of them
    <home>\.incoming\<id>-<YYYYMMDD-HHMMSS> where a package is unpacked before it takes its place (incoming)

A folder whose name starts with a dot is no extension (the two above are the workshop's own). An extension whose folder
is right in the home folder is one the workshop keeps ("home": it finds, replaces and removes the folder); any other is
registered by its path ("linked": the workshop never touches its folder). A link or a junction in the home folder that
leads elsewhere is neither found nor inside: what it leads to is registered by its path. Every function takes the home
folder (root), so nothing here reads where it is. Standard library only.
"""
import os
import re
import shutil
import time
from pathlib import Path

from .manifest import MANIFEST

REMOVED = ".removed"          # the folders taken away
INCOMING = ".incoming"        # packages being unpacked
KEPT = 20                     # folders kept in .removed at most (the newest)
MOVE_WAITS = (0.05, 0.1, 0.2, 0.4)     # seconds between the tries of a move the system refused (a process that just
#                                        ended may hold a file of the folder for a moment)
STAMPED = re.compile(r"-(\d{8}-\d{6})(?:-(\d+))?$")


def real(path):
    """a path as it is compared: links followed as far as they are there, the case folded (Windows)"""
    return os.path.normcase(os.path.realpath(str(path)))


def inside(folder, root):
    """whether folder is right in root: a folder of its own there, not deeper, not root itself, and not a link in it
    that leads elsewhere (resolved, then compared; the case does not count on Windows). It need not be there any more"""
    try:
        return os.path.dirname(real(folder)) == real(root) and bool(os.path.basename(real(folder)))
    except (OSError, ValueError, TypeError):
        return False


def found(root):
    """the folders right in root that have a manifest (wuxian-extension.json), by name: [Path]. Not those whose name
    starts with a dot, and not a link or junction that leads elsewhere"""
    root = Path(root)
    try:
        names = sorted(os.listdir(root), key=lambda n: (n.lower(), n))
    except OSError:
        return []
    out = []
    for name in names:
        folder = root / name
        if name.startswith(".") or not (folder / MANIFEST).is_file() or not inside(folder, root):
            continue
        out.append(folder)
    return out


def name_of(eid):
    """an id as the first part of a folder's name here: nothing that leads out of the folder it is made in"""
    return re.sub(r"[^A-Za-z0-9_]", "_", str(eid or ""))[:40] or "extension"


def stamped(parent, eid):
    """a name that is not taken in parent: <id>-<YYYYMMDD-HHMMSS>, then <same>-2, -3, ..."""
    base = f"{name_of(eid)}-{time.strftime('%Y%m%d-%H%M%S')}"
    path, n = parent / base, 1
    while os.path.lexists(path):
        n += 1
        path = parent / f"{base}-{n}"
    return path


def order(path):
    """what the folders of .removed are sorted by: when they were taken away, by their names"""
    m = STAMPED.search(path.name)
    return (m[1], int(m[2] or 1), path.name) if m else ("", 0, path.name)


def prune(removed, keep=KEPT):
    """only the newest `keep` folders of .removed stay (those the workshop named; anything else there is left alone).
    One that cannot be deleted now stays until the next time"""
    try:
        ours = sorted((p for p in Path(removed).iterdir() if STAMPED.search(p.name)), key=order)
    except OSError:
        return
    for old in ours[:max(0, len(ours) - keep)]:
        shutil.rmtree(old, ignore_errors=True)
        if os.path.lexists(old):                       # a link, or a file: not a folder's tree
            try:
                os.rmdir(old) if os.path.isdir(old) else os.unlink(old)
            except OSError:
                pass


def trash(folder, root, eid):
    """moves an extension's folder to root/.removed/<eid>-<YYYYMMDD-HHMMSS>[-n] and gives that path; after that only
    the newest KEPT folders stay there. OSError when it cannot be moved (a program holds one of its files): the folder
    is where it was, whole"""
    folder, removed = Path(folder), Path(root) / REMOVED
    removed.mkdir(parents=True, exist_ok=True)
    to = stamped(removed, eid)
    for wait in MOVE_WAITS + (None,):
        try:
            os.rename(folder, to)
            break
        except FileNotFoundError:
            raise
        except OSError:
            if wait is None:
                raise
            time.sleep(wait)
    prune(removed)
    return to


def incoming(root, eid):
    """a new, empty folder root/.incoming/<eid>-<YYYYMMDD-HHMMSS>[-n]: where a package is unpacked and checked before
    its folder takes its place in root. Whoever asked for it removes it"""
    parent = Path(root) / INCOMING
    parent.mkdir(parents=True, exist_ok=True)
    while True:
        path = stamped(parent, eid)
        try:
            path.mkdir()
            return path
        except FileExistsError:                        # another thread took the name meanwhile
            continue
