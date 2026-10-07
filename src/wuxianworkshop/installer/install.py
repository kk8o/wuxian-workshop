r"""Install the addons into the game's AddOns folder: clean first, then copy.

    wuxian install [--game "<client folder>"] [--mode player|developer] [--no-clean] [--json]

Developer mode (the default) copies addon/!WuxianWorkshop and addon/WoWBridge (.lua, .toc, .xml; see
installer/__init__.py for where the sources are) and creates the font mailbox placeholders that are missing
(mail/00001-04096.ttf, mail/proc.ttf; core/mailbox.py; slots in use are left alone). Player mode copies
!WuxianWorkshop only and, when cleaning, removes the developer components (WoWBridge with its mailbox, WoWBridge_Lab):
a player's client has the error collector and nothing that draws on the screen or runs code it is sent.
Cleaning removes what earlier versions and the experiments left behind: the !WoWBridge addon, the slot addons
WoWBridge_S001..S032 (only a folder that holds nothing but the two files we wrote) and the experiment folders inside
WoWBridge\ (ack fresh never flag poll ctl play font). In developer mode WoWBridge\mail\ and folders that are not ours are
never touched.
The client only reads the .toc files and finds new files when it starts. restart_required: a full restart of the game is
needed to see this install (a folder or file added or removed, or a .toc changed); restart_for_link: the running game
cannot work with the program until then (files were added that it does not know about), so the companion waits for a
game started after the install (state\install.json, paths.py). Changes to existing .lua files only need a /reload.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from . import ADDON_FILE_TYPES, DEVELOPER_ADDON, LAB_ADDON, MODES, addon_source_dir, addons_for
from .. import __version__
from ..core import mailbox
from ..core.game import addons_dir, atomic_write
from .addons import client_interface, toc_with_interface
from ..paths import state_dir

OLD_ADDONS = ("!WoWBridge",)                            # v0.7: replaced by !WuxianWorkshop
SLOT_COUNT = 32                                         # WoWBridge_S001..S032 (the slot addons early experiments wrote)
EXPERIMENT_DIRS = ("ack", "fresh", "never", "flag", "poll", "ctl", "play", "font")   # inside WoWBridge\: what early experiments wrote


def slot_name(i):
    return f"WoWBridge_S{i:03d}"


def is_our_slot(path):
    """a slot folder as we wrote it: nothing in it but its .toc and Inbox.lua (a folder someone added to is not removed)"""
    allowed = {f"{path.name}.toc", "Inbox.lua"}
    return all(c.is_file() and c.name in allowed for c in path.iterdir())


def leftovers(addons):
    """the folders a clean install removes: old addons, our slot addons and the experiment folders"""
    addons = Path(addons)
    out = [addons / name for name in OLD_ADDONS if (addons / name).is_dir()]
    out += [p for i in range(1, SLOT_COUNT + 1) for p in [addons / slot_name(i)] if p.is_dir() and is_our_slot(p)]
    out += [p for d in EXPERIMENT_DIRS for p in [addons / "WoWBridge" / d] if p.is_dir()]
    return out


def developer_components(addons):
    """what player mode removes: WoWBridge (its mailbox included) and the experiments addon"""
    addons = Path(addons)
    return [addons / name for name in (DEVELOPER_ADDON, LAB_ADDON) if (addons / name).is_dir()]


def foreign_slots(addons):
    """slot folders that hold other files: left alone, the doctor only mentions them"""
    addons = Path(addons)
    return [p for i in range(1, SLOT_COUNT + 1) for p in [addons / slot_name(i)] if p.is_dir() and not is_our_slot(p)]


def _rel(addons, path):
    return Path(path).relative_to(addons).as_posix()


def _rmtree(path, errors):
    """remove a folder tree; a file that cannot be removed (still open in the client) is recorded, not raised"""
    failed = []
    shutil.rmtree(path, onexc=lambda func, p, exc: failed.append(f"{p}: {exc}"))
    errors.extend(failed[:3])
    return not path.exists()


def _copy_addon(src_dir, dst_dir, addons, installed, new_dirs, new_files, tocs_changed=None, interface=None):
    """every .lua / .toc / .xml / .tga of the source folder, replaced atomically (the game may be running); a .toc gets the
    client's ## Interface (interface, when known) and is listed in tocs_changed when its content changes (the client reads
    them only when it starts)"""
    if not dst_dir.exists():
        new_dirs.append(_rel(addons, dst_dir))
    for src in sorted(src_dir.rglob("*")):
        if not (src.is_file() and src.suffix.lower() in ADDON_FILE_TYPES):
            continue
        dst = dst_dir / src.relative_to(src_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        if interface and src.suffix.lower() == ".toc":
            data = toc_with_interface(data, interface)
        if not dst.exists():
            new_files.append(_rel(addons, dst))
        elif tocs_changed is not None and dst.suffix.lower() == ".toc" and dst.read_bytes() != data:
            tocs_changed.append(_rel(addons, dst))
        atomic_write(dst, data)
        installed.append(_rel(addons, dst))




def install(game_dir=None, clean=True, source=None, mode="developer", keep_developer=False):
    """dict(installed=[paths written], removed=[folders removed], restart_required, restart_for_link, tocs_changed=[],
    deferred=[folders kept], mode, addons_dir, new_files=count, errors=[]); see the module docstring for the two restart
    flags. game_dir: the client folder (default: the running game's, else a known one); source: the addon folder to copy
    from (default: the program's own, installer.addon_source_dir()); mode: "developer" or "player"; keep_developer: in
    player mode, leave the developer components for an install while the game is not running (listed in deferred).
    Raises FileNotFoundError when either folder is missing, ValueError for a mode that is not one."""
    names = addons_for(mode)
    source = Path(source) if source else addon_source_dir()
    try:
        addons = addons_dir(game_dir)
    except SystemExit as e:                                      # core.game speaks to the command line; callers get an exception
        raise FileNotFoundError(str(e)) from None
    if not addons.is_dir():
        raise FileNotFoundError(f"not an AddOns folder: {addons}")
    for name in names:
        if not (source / name).is_dir():
            raise FileNotFoundError(f"addon source missing: {source / name}")
    removed, errors, deferred = [], [], []
    if clean:
        dev = developer_components(addons) if mode == "player" else []
        if keep_developer:
            deferred, dev = [_rel(addons, p) for p in dev], []
        for p in leftovers(addons) + dev:
            if _rmtree(p, errors):
                removed.append(_rel(addons, p))
    installed, new_dirs, new_files, tocs_changed = [], [], [], []
    interface = client_interface(addons.parent.parent)[1]       # the client's own number in the tocs (a game update
    for name in names:                                          # that changes it is followed: see daemon sync_addons)
        _copy_addon(source / name, addons / name, addons, installed, new_dirs, new_files, tocs_changed, interface)
    if mode == "developer":
        mail = mailbox.mail_dir(addons)
        before = {p.name for p in mail.glob("*.ttf")} if mail.is_dir() else set()
        mailbox.install(addons)
        created = sorted({p.name for p in mail.glob("*.ttf")} - before)
        if created:
            new_files += [f"WoWBridge/mail/{n}" for n in created]
            installed.append("WoWBridge/mail/")
    for_link = bool(new_dirs or new_files)
    result = dict(installed=installed, removed=removed, restart_required=bool(for_link or removed or tocs_changed),
                  restart_for_link=for_link, tocs_changed=tocs_changed, deferred=deferred, mode=mode, addons_dir=str(addons),
                  new_files=len(new_files), errors=errors, interface=interface)
    record = dict(time=time.time(), version=__version__, **result)          # "time": the companion reads it
    (state_dir() / "install.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wuxian install", description=__doc__.split("\n")[0])
    ap.add_argument("--game", help="client folder that holds Interface\\AddOns (default: the running game's, else _cn_beta_)")
    ap.add_argument("--mode", choices=MODES, default="developer",
                    help="player: the error collector only (removes the developer components); developer (default): WoWBridge too")
    ap.add_argument("--no-clean", dest="clean", action="store_false",
                    help="keep what earlier versions and the experiments left in AddOns (default: remove it)")
    ap.add_argument("--source", help="addon folder to install from (default: the program's own copy)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args(argv)
    try:
        r = install(args.game, clean=args.clean, source=args.source, mode=args.mode)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"wuxian install: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(r, indent=1, ensure_ascii=False))
    else:
        files = [p for p in r["installed"] if not p.endswith("/")]
        print(f"installed {len(files)} files into {r['addons_dir']} ({r['new_files']} new)")
        if r["removed"]:
            shown = ", ".join(r["removed"][:8]) + (f", ... ({len(r['removed'])} in all)" if len(r["removed"]) > 8 else "")
            print(f"removed {len(r['removed'])} leftover folders: {shown}")
        for e in r["errors"]:
            print(f"could not remove: {e}", file=sys.stderr)
        if r["restart_for_link"]:
            print("fully exit and restart the game: files were added (the client only finds new files at launch)")
        elif r["restart_required"]:
            print("restart the game when convenient: folders were removed or a .toc changed (read at launch only); "
                  "the running game keeps working")
        else:
            print("no new files: a /reload in game picks up the changed .lua files")
    return 1 if r["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
