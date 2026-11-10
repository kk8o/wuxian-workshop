r"""The registry of local extensions: the file and what it keeps of each extension.

The file is <state>\extensions.json, {revision, extensions: [{id, path, enabled, options: {name: value}, described}]}:
path is the extension's folder, options the values the player set, described the extension's last answer to initialize
(its capabilities, sources and options), so that an extension that does not run still lists them (the agent's tool list
does not change because it stopped). revision goes up by one with every change (the host counts: host.py changed()).

It is written whole each time (a .tmp beside it, then os.replace, tried again for a moment while a reader holds the
file). Registry reads and writes it and looks things up in it; it takes no lock by itself except in update_entry: the
lock is the host's (one RLock for the registry and the processes), and the host holds it around a read that is followed
by a write. Standard library only.
"""
import json
import os
import time
from pathlib import Path

from ..daemon.api import ApiError
from . import manifest as M

REPLACE_WAITS = (0.02, 0.05, 0.1, 0.2, 0.5)


# --- files -----------------------------------------------------------------------------------------------------------

def replace_retrying(src, dst):
    """os.replace, tried again for a moment while another process (a reader) holds one of the files open"""
    for wait in REPLACE_WAITS:
        try:
            return os.replace(src, dst)
        except PermissionError:
            time.sleep(wait)
    return os.replace(src, dst)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        replace_retrying(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# --- an entry's options ----------------------------------------------------------------------------------------------

def option_rows(e):
    """the options an entry's extension described, each with its value now (the player's, else its default)"""
    desc = e.get("described") or {}
    return [dict(o, value=e["options"].get(o["name"], o.get("default"))) for o in desc.get("options") or []]


def check_options(e, options):
    """options (name -> value) against what the entry's extension described: ApiError not_described (it has not
    started yet), bad_option (no option of that name, or a value it does not take)"""
    eid = e["id"]
    described = {o["name"]: o for o in (e.get("described") or {}).get("options") or []}
    if not described and e.get("described") is None:
        raise ApiError(409, "not_described", f"{eid} has not started yet, so its options are not known: "
                                             f"extension_load it first")
    for name, value in options.items():
        opt = described.get(name)
        if opt is None:
            raise ApiError(400, "bad_option", f"{eid} has no option {name!r} (it has: "
                                              f"{', '.join(described) or 'none'})")
        why = M.value_problem(opt, value)
        if why:
            raise ApiError(400, "bad_option", f"option {name}: {why}")


# --- the registry ----------------------------------------------------------------------------------------------------

class Registry:
    """<root>\\extensions.json. lock: the host's lock (update_entry takes it; nothing else here does); on_save(): the
    file was written"""

    def __init__(self, root, lock, on_save=None):
        self.root, self.lock, self.on_save = Path(root), lock, on_save

    @property
    def path(self):
        return self.root / "extensions.json"

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        items = data.get("extensions")
        data["extensions"] = [e for e in items if isinstance(e, dict) and isinstance(e.get("id"), str)
                              and isinstance(e.get("path"), str)] if isinstance(items, list) else []
        for e in data["extensions"]:
            e["enabled"] = e.get("enabled") is not False
            e["options"] = e["options"] if isinstance(e.get("options"), dict) else {}
            e["described"] = e["described"] if isinstance(e.get("described"), dict) else None
        return data

    def save(self, data):
        write_json(self.path, data)
        if self.on_save is not None:
            self.on_save()

    @property
    def revision(self):
        return int(self.load().get("revision") or 0)

    def entry(self, eid, data=None):
        """the registry's entry of an id; ApiError not_found"""
        if not isinstance(eid, str) or not eid.strip():
            raise ApiError(400, "bad_request", "id: a local extension's id (local_extensions lists them)")
        eid = eid.strip()
        for e in (data or self.load())["extensions"]:
            if e["id"] == eid:
                return e
        raise ApiError(404, "not_found", f"no local extension {eid} is registered (local_extensions lists them; "
                                         f"extension_add registers a folder, new_extension makes one)")

    def ids(self):
        return [e["id"] for e in self.load()["extensions"]]

    def update_entry(self, eid, **fields):
        with self.lock:
            data = self.load()
            e = self.entry(eid, data)
            e.update(fields)
            self.save(data)
            return e

    def option_values(self, eid):
        e = self.entry(eid)
        return {o["name"]: o["value"] for o in option_rows(e)}

    def capability(self, cap_id):
        """(entry, the described capability or None) of a capability id whose extension is registered; None when no
        registered extension has its id's first part"""
        if not isinstance(cap_id, str) or "." not in cap_id:
            return None
        eid, _, name = cap_id.partition(".")
        for e in self.load()["extensions"]:
            if e["id"] == eid:
                caps = (e.get("described") or {}).get("capabilities") or []
                return e, next((c for c in caps if c["name"] == name), None)
        return None

    def approval(self, entry):
        """"ok", "needed" or "changed": whether the player allowed this entry's command as it is now (the fingerprint
        of its folder and its manifest's run, python, env and pass against entry["approved"]). (To do: W-B)"""
        return "ok"

    def approve(self, eid, by):
        """the entry's command as it is now is allowed: approved {fingerprint, at, by} ("player" or "developer");
        saves. (To do: W-B)"""
        return None

    def kind_of(self, cap_id):
        """see / keep / point of a capability of a local extension (as it last described itself), else None"""
        found = self.capability(cap_id)
        return found[1]["kind"] if found and found[1] else None
