r"""The registry of local extensions: what is registered, kept in memory and in two files.

    <state>\extensions.json         the configuration, {revision, extensions: [{id, path, enabled, options, approved}]}:
                                    path the extension's folder, options the values the player set (name -> value),
                                    approved {fingerprint, at, by} or null (whether its command may run, below);
                                    ask: true (only written when it is) on one that was not written on this computer:
                                    imported from a package, or found in the extensions' home folder (below)
    <state>\extensions.json.bak     the configuration as it was before its last change
    <state>\extensions-cache.json   the descriptions, {extensions: {id: {path, described}}}: described is the
                                    extension's last answer to initialize (its capabilities, sources and options), so
                                    that one that does not run still lists them (the agent's tool list does not change
                                    because it stopped). It may be lost or damaged: an extension without a description
                                    has not described itself yet, and does at its next start

An entry as load() and entry() give it is both together, {id, path, enabled, options, approved, described} and, on
those that have it, ask: True. revision goes up by one with every change (the host counts: host.py changed()).

Memory is what counts. The configuration is read at the first use and again only when the file changed under the
registry (a player's edit, by its time and size); every save() replaces what memory holds and writes the files that
differ (a .tmp beside each, flushed, then os.replace, tried again for a moment while a reader holds the file). Only a
file that is not there is an empty registry. Any other failed read (the system refused, no JSON, not a registry's
shape) leaves memory as it is and the file alone: Registry.problem says why until a read works, save() keeps its
change in memory without writing over the file, and a registry that has never been read refuses save() (ApiError
registry_unreadable), so that a list nobody saw is not replaced by an empty one. A write that fails also stays in
memory (problem says so) and is written with the next save. A registry file of before the two were split (described
in its entries) is taken apart at its first read.

Approval: an extension's command runs only once it was allowed as it is now. The fingerprint is the sha256 of the
canonical JSON (sorted keys) of {path: the folder's real path, run, python, env, pass: as the manifest file has them},
read from wuxian-extension.json itself; a manifest that names game addons (addons, not empty) has one more key there,
addons: their names, sorted (what the workshop installs into the game with it), and one without has exactly the keys
above, so what was approved before addons were known stays approved. approval(entry) compares it with
entry["approved"], approve(id, by) stores it. Whether the workshop may approve by itself is the host's to say (host.py
allow): never an entry with ask, which the player allows in the App each time its command is another one.

Registry takes the host's lock (one RLock for the registry and the processes) in every method; the host holds it
around a read that is followed by a write. Standard library only.
"""
import collections
import copy
import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

from ..daemon.api import ApiError
from . import manifest as M

REPLACE_WAITS = (0.02, 0.05, 0.1, 0.2, 0.5)
READ_AGAIN = 1.0              # seconds before a read the system refused is tried again
NOTES_KEPT = 50               # the last lines for the log a registry keeps itself (Registry.notes)
DRIVE_REMOTE = 4              # GetDriveTypeW: a network drive
APPROVERS = ("player", "developer")
FINGERPRINTED = ("run", "python", "env", "pass")     # the manifest's fields that decide what runs


# --- files -----------------------------------------------------------------------------------------------------------

def replace_retrying(src, dst):
    """os.replace, tried again for a moment while another process (a reader) holds one of the files open"""
    for wait in REPLACE_WAITS:
        try:
            return os.replace(src, dst)
        except PermissionError:
            time.sleep(wait)
    return os.replace(src, dst)


def read_retrying(path):
    """a file's text (UTF-8, a byte order mark dropped), tried again for a moment while the system refuses it (a
    writer is replacing it); FileNotFoundError at once"""
    for wait in REPLACE_WAITS:
        try:
            return Path(path).read_text(encoding="utf-8-sig")
        except PermissionError:
            time.sleep(wait)
    return Path(path).read_text(encoding="utf-8-sig")


def write_json(path, data):
    """data as the file's JSON, whole or not at all: written to a .tmp beside it and flushed, then moved over it"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=1))
            f.flush()
            os.fsync(f.fileno())
        replace_retrying(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def stamp(path):
    """(mtime_ns, size) of a file, or None when it is not there"""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st.st_mtime_ns, st.st_size


# --- folders ---------------------------------------------------------------------------------------------------------

def drive_type(root):
    """GetDriveTypeW of a drive's root ("Z:\\"); None where there are no drives"""
    if os.name != "nt":
        return None
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))


def check_folder(path):
    """refuses a folder that is not on this computer: a path that starts with two backslashes (UNC; two slashes are
    the same) or is on a network drive (GetDriveType DRIVE_REMOTE): ApiError(400, "not_allowed", ...). Called with
    the path as given, before anything of it is read (Host.add, check.resolve); it reads nothing itself"""
    text = os.fspath(path).strip()
    remote = len(text) >= 2 and text[0] in "\\/" and text[1] in "\\/"
    if not remote and os.name == "nt":
        drive = os.path.splitdrive(os.path.abspath(text))[0]
        remote = drive.startswith("\\\\") or (bool(drive) and drive_type(drive + "\\") == DRIVE_REMOTE)
    if remote:
        raise ApiError(400, "not_allowed", f"{text} is a network path: a local extension runs from a folder on this "
                                           f"computer's own disk (copy the folder there, then give that path)")
    return None


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


def stale_options(e):
    """{name: why} of an entry's option values that its description does not take (any more)"""
    described = {o["name"]: o for o in (e.get("described") or {}).get("options") or []}
    out = {}
    for name, value in e["options"].items():
        opt = described.get(name)
        if opt is None:
            out[name] = "it has no such option any more"
            continue
        try:
            why = M.value_problem(opt, value)
        except Exception as ex:                    # an option this version cannot weigh: its default is the safe value
            why = f"it could not be checked ({type(ex).__name__})"
        if why:
            out[name] = f"its value is no longer one it takes ({why})"
    return out


# --- an entry's shape ------------------------------------------------------------------------------------------------

def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def texts(d, *keys):
    return all(isinstance(d.get(k), str) for k in keys)


def described_ok(d):
    """whether d has the shape of a description as the host stores it ({capabilities: [{id, name, kind, title, doc,
    args, timeout}], sources: [{name, title, doc}], options: [{name, kind, title, ...}]}): every key the code that
    reads a description takes for granted is there, with its type. One that fails counts as no description"""
    if not isinstance(d, dict) or not all(isinstance(d.get(k), list) for k in ("capabilities", "sources", "options")):
        return False
    for c in d["capabilities"]:
        if not (isinstance(c, dict) and texts(c, "id", "name", "kind", "title", "doc")
                and isinstance(c.get("args"), dict) and number(c.get("timeout"))):
            return False
    for s in d["sources"]:
        if not (isinstance(s, dict) and texts(s, "name", "title", "doc")):
            return False
    for o in d["options"]:
        if not (isinstance(o, dict) and texts(o, "name", "kind", "title")):
            return False
        choices = o.get("choices")
        if choices is not None and not (isinstance(choices, list)
                                        and all(isinstance(c, dict) and "value" in c for c in choices)):
            return False
    return True


def approved_ok(a):
    return isinstance(a, dict) and isinstance(a.get("fingerprint"), str) and bool(a["fingerprint"])


def normal_entry(e):
    """an entry with every key the rest reads: {id, path, enabled, options, approved, described} (in place). ask is
    there only as True (anything else is taken out: an entry without it is as it always was, in memory and written)"""
    e["enabled"] = e.get("enabled") is not False
    e["options"] = e["options"] if isinstance(e.get("options"), dict) else {}
    e["approved"] = e["approved"] if approved_ok(e.get("approved")) else None
    e["described"] = e["described"] if described_ok(e.get("described")) else None
    if e.get("ask") is True:
        e["ask"] = True
    else:
        e.pop("ask", None)
    return e


def addon_names(raw):
    """the game addons a manifest file names, as the fingerprint has them: its addons' texts, sorted, each once; []
    when it names none (or addons is no list)"""
    names = raw.get("addons") if isinstance(raw, dict) else None
    if not isinstance(names, list):
        return []
    return sorted({n for n in names if isinstance(n, str) and n})


def same_folder(a, b):
    return isinstance(a, str) and isinstance(b, str) and os.path.normcase(a) == os.path.normcase(b)


def parse_config(text):
    """extensions.json's text as {revision, extensions: [entry as it is written]}; ValueError says what is wrong
    with it. Anything that is not plainly a registry is an error, not an empty one"""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("it is not a JSON object")
    items = data.get("extensions")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ValueError("its extensions is not a list")
    seen = set()
    for i, e in enumerate(items):
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not isinstance(e.get("path"), str):
            raise ValueError(f"its extension {i + 1} has no id or no path")
        if e["id"] in seen:
            raise ValueError(f"it lists {e['id']} twice")
        seen.add(e["id"])
    revision = data.get("revision")
    data["revision"] = revision if isinstance(revision, int) and not isinstance(revision, bool) else 0
    data["extensions"] = items
    return data


def config_of(data):
    """what extensions.json holds of the registry: all but the descriptions"""
    out = {k: v for k, v in data.items() if k != "extensions"}
    out["extensions"] = [{k: v for k, v in e.items() if k != "described"} for e in data["extensions"]]
    return out


def cache_of(data):
    """what extensions-cache.json holds of the registry: the descriptions, each with the folder it is of"""
    return dict(extensions={e["id"]: dict(path=e["path"], described=e["described"])
                            for e in data["extensions"] if e["described"] is not None})


# --- the registry ----------------------------------------------------------------------------------------------------

class Registry:
    """the registered local extensions: memory, <root>\\extensions.json and <root>\\extensions-cache.json (the
    module's text says how they go together). lock: the host's RLock, taken by every method; on_save(): save() took
    a change (the host drops what it worked out from the registry); note(text): a line for the daemon's log (a file
    that cannot be read or written, an option value dropped; notes keeps the last of them with their times, note or
    not). problem: None, or why extensions.json cannot be read or written just now, for the host to show"""

    def __init__(self, root, lock=None, on_save=None, note=None):
        self.root = Path(root)
        self.lock = lock if lock is not None else threading.RLock()
        self.on_save, self.note = on_save, note
        self.problem = None
        self.notes = collections.deque(maxlen=NOTES_KEPT)     # (time, text) of the last lines said, note or not
        self.data = None              # memory: {revision, extensions: [entry]}; None until the file was read. Replaced
                                      # whole (adopt, save), never changed in place: disk_config and disk_cache share
                                      # its values
        self.file_stamp = None        # extensions.json's stamp as this registry last read or wrote it (None: no file)
        self.unread = None            # the stamp of an extensions.json that is no registry: read again once it changes
        self.again = 0.0              # (monotonic) a read the system refused is not tried again before this
        self.disk_config = None       # what extensions.json holds, as far as this registry knows (None: write it)
        self.disk_cache = None        # the same of extensions-cache.json
        self.legacy = False           # extensions.json still has the descriptions in its entries (not taken apart yet)
        self.prints = {}              # real folder -> (its manifest's stamp, fingerprint or None)

    @property
    def path(self):
        return self.root / "extensions.json"

    @property
    def cache_path(self):
        return self.root / "extensions-cache.json"

    @property
    def backup_path(self):
        return self.root / "extensions.json.bak"

    # --- memory and the files

    def say(self, text):
        self.notes.append((time.time(), text))
        if self.note is not None:
            try:
                self.note(f"local extensions: {text}")
            except Exception:
                pass

    def trouble(self, text):
        """problem is text now (None: none any more); told once"""
        if text != self.problem:
            self.problem = text
            self.say(text or f"{self.path} is read and written again")

    def memory(self):
        """(under the lock) what memory holds, the file read first where it must be; an empty registry while one that
        was never read cannot be. Not a copy: for reading only"""
        self.fresh()
        return self.data if self.data is not None else dict(revision=0, extensions=[])

    def fresh(self):
        """(under the lock) reads extensions.json when memory has nothing yet or the file is not the one this registry
        last read or wrote"""
        now = stamp(self.path)
        if self.data is not None and now == self.file_stamp:
            return
        if now is not None and now == self.unread:
            return
        if self.again and time.monotonic() < self.again:
            return
        self.again = 0.0
        try:
            data = parse_config(read_retrying(self.path))
        except FileNotFoundError:
            # no file: an empty registry at the first read; later memory stays, and the next save writes the file anew
            if self.data is None:
                self.data, self.disk_cache = dict(revision=0, extensions=[]), self.read_cache()[1]
            self.file_stamp, self.unread, self.disk_config, self.legacy = None, None, None, False
            self.trouble(None)
        except OSError as e:
            self.again = time.monotonic() + READ_AGAIN
            self.unreadable(f"{type(e).__name__}: {e}")
        except ValueError as e:                    # no JSON (or no UTF-8), or not a registry's shape
            self.unread = now
            self.unreadable(str(e))
        else:
            self.adopt(data, now)

    def unreadable(self, why):
        kept = ("the local extensions stay as they were, and their changes are not saved until the file is put right"
                if self.data is not None else
                "no local extension is listed, and none can be registered or changed until the file is put right "
                "(or removed, to start with none)")
        self.trouble(f"{self.path} cannot be read ({why}): {kept}. {self.backup_path.name} beside it is the file as "
                     f"it was before its last change")

    def read_cache(self):
        """({id: {path, described}}, what the file holds) of extensions-cache.json; nothing ({}) when it is not there
        (no file is a cache of nothing), cannot be read or is no cache (holds None: written anew)"""
        try:
            raw = json.loads(read_retrying(self.cache_path))
            items = raw["extensions"]
            if not isinstance(items, dict) or not all(isinstance(v, dict) for v in items.values()):
                raise ValueError("its extensions is not an object of objects")
        except FileNotFoundError:
            return {}, dict(extensions={})
        except (OSError, ValueError, TypeError, KeyError) as e:
            self.say(f"{self.cache_path} cannot be used ({type(e).__name__}: {e}): the extensions describe themselves "
                     f"again when they start")
            return {}, None
        return items, raw

    def adopt(self, data, now):
        """(under the lock) memory is what extensions.json holds (data, parsed; now: the file's stamp). An entry's
        description: the one memory has of that folder (the first time: the cache file's), else the one in the entry
        itself (a file of before the two were split, written as two files here)"""
        first = self.data is None
        legacy = any("described" in e for e in data["extensions"])
        on_disk = None if legacy else config_of(copy.deepcopy(data))
        if first:
            known, self.disk_cache = self.read_cache()
            revision = data["revision"]
        else:
            known = {e["id"]: e for e in self.data["extensions"]}
            revision = max(data["revision"], self.data["revision"])
        dropped = []
        for e in data["extensions"]:
            own = e.get("described")
            normal_entry(e)
            was = known.get(e["id"])
            if was is not None and same_folder(was.get("path"), e["path"]) and was.get("described") is not None:
                if described_ok(was["described"]):
                    e["described"] = copy.deepcopy(was["described"])
                else:
                    dropped.append(e["id"])
            elif own is not None and e["described"] is None:
                dropped.append(e["id"])
            self.drop_stale(e)
        if dropped:
            self.say(f"the stored description of {', '.join(dropped)} has not the shape of one: taken as not described "
                     f"yet (the next start describes it again)")
        data["revision"] = revision
        self.data, self.file_stamp, self.unread, self.disk_config, self.legacy = data, now, None, on_disk, legacy
        self.trouble(None)
        if legacy:
            self.say(f"{self.path}: the descriptions move to {self.cache_path.name}")
            self.write()

    def write(self):
        """(under the lock) memory to the files that differ from it. The cache first: a registry file that still
        holds the descriptions itself is not rewritten before they are in the cache"""
        cache, cached = cache_of(self.data), True
        if cache != self.disk_cache:
            try:
                write_json(self.cache_path, cache)
                self.disk_cache = cache
            except OSError as e:
                cached = False
                self.disk_cache = None
                self.say(f"{self.cache_path} could not be written ({type(e).__name__}: {e}): tried again with the next "
                         f"change")
        config = config_of(self.data)
        if config == self.disk_config or (self.legacy and not cached):
            return
        if stamp(self.path) != self.file_stamp:         # not the file this registry read or wrote: left alone (fresh())
            return
        self.backup()
        try:
            write_json(self.path, config)
        except OSError as e:
            self.trouble(f"{self.path} could not be written ({type(e).__name__}: {e}): the change holds until the "
                         f"daemon stops, and is written with the next one")
            return
        self.disk_config, self.file_stamp, self.legacy = config, stamp(self.path), False
        self.trouble(None)

    def backup(self):
        """(under the lock) extensions.json, about to be replaced, copied to extensions.json.bak"""
        tmp = self.backup_path.with_name(self.backup_path.name + ".tmp")
        try:
            shutil.copyfile(self.path, tmp)
            replace_retrying(tmp, self.backup_path)
        except FileNotFoundError:
            pass
        except OSError as e:
            self.say(f"{self.backup_path} could not be written ({type(e).__name__}: {e})")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def drop_stale(self, e):
        """an entry's option values that its description does not take (any more) are dropped: back to their
        defaults. Told by name only: a value may be a secret"""
        if e["described"] is None or not e["options"]:
            return
        for name, why in stale_options(e).items():
            del e["options"][name]
            self.say(f"{e['id']}: its option {name} is back at its default: {why}")

    def load(self):
        """{revision, extensions: [entry]}: a copy, the caller's to change and to save()"""
        with self.lock:
            return copy.deepcopy(self.memory())

    def save(self, data):
        """memory is data now (as load() gave it, changed), and the files follow. An entry whose description is
        another one than before has its option values checked against it: those it does not take are dropped (in
        data too), back to their defaults. ApiError registry_unreadable while the file has never been read"""
        with self.lock:
            self.fresh()
            if self.data is None:
                raise ApiError(500, "registry_unreadable", f"{self.problem}. Nothing was changed")
            before = {e["id"]: e for e in self.data["extensions"]}
            for e in data["extensions"]:
                normal_entry(e)
                was = before.get(e["id"])
                if was is None or was["described"] != e["described"]:
                    self.drop_stale(e)
            revision = data.get("revision")
            data["revision"] = revision if isinstance(revision, int) and not isinstance(revision, bool) else 0
            self.data = copy.deepcopy(data)
            self.write()
        if self.on_save is not None:
            self.on_save()

    @property
    def revision(self):
        with self.lock:
            return self.memory()["revision"]

    def entry(self, eid, data=None):
        """the registry's entry of an id (data's own when data is given, else a copy); ApiError not_found"""
        if not isinstance(eid, str) or not eid.strip():
            raise ApiError(400, "bad_request", "id: a local extension's id (local_extensions lists them)")
        eid = eid.strip()
        with self.lock:
            for e in (data or self.memory())["extensions"]:
                if e["id"] == eid:
                    return e if data else copy.deepcopy(e)
        raise ApiError(404, "not_found", f"no local extension {eid} is registered (local_extensions lists them; "
                                         f"extension_add registers a folder, new_extension makes one)")

    def ids(self):
        with self.lock:
            return [e["id"] for e in self.memory()["extensions"]]

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
        with self.lock:
            for e in self.memory()["extensions"]:
                if e["id"] == eid:
                    e = copy.deepcopy(e)
                    caps = (e.get("described") or {}).get("capabilities") or []
                    return e, next((c for c in caps if c["name"] == name), None)
        return None

    def kind_of(self, cap_id):
        """see / keep / point of a capability of a local extension (as it last described itself), else None"""
        found = self.capability(cap_id)
        return found[1]["kind"] if found and found[1] else None

    # --- approval

    def fingerprint(self, folder, kept=True):
        """the fingerprint of what an extension's folder would run as it is now: the sha256 (hex) of the canonical
        JSON of {path: its real path, run, python, env, pass: as its manifest file has them (null: not there)}, and
        addons: the sorted names, only when the manifest file has a list of them that is not empty (addon_names); None
        when the manifest cannot be read. Kept by the manifest's time and size, a stat while nothing changed
        (kept=False: read anyway)"""
        try:
            real = os.path.normcase(os.path.realpath(folder))
            file = os.path.join(real, M.MANIFEST)
            st = os.stat(file)
        except (OSError, ValueError, TypeError):
            return None
        seen = (st.st_mtime_ns, st.st_size)
        had = self.prints.get(real) if kept else None
        if had is not None and had[0] == seen:
            return had[1]
        try:
            with open(file, encoding="utf-8-sig") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("not an object")
            what = dict({k: raw.get(k) for k in FINGERPRINTED}, path=real)
            addons = addon_names(raw)
            if addons:                                  # (none: the very text it always was)
                what["addons"] = addons
            text = json.dumps(what, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            value = hashlib.sha256(text.encode("utf-8")).hexdigest()
        except (OSError, ValueError):
            value = None
        self.prints[real] = (seen, value)
        return value

    def approval(self, entry):
        """"ok", "needed" or "changed": whether this entry's command was allowed as it is now (the fingerprint of its
        folder and its manifest's run, python, env and pass against entry["approved"]). needed: never allowed, or its
        manifest cannot be read; changed: allowed, but it is another folder or another command now"""
        approved = entry.get("approved")
        if not approved_ok(approved):
            return "needed"
        now = self.fingerprint(entry.get("path"))
        if now is None:
            return "needed"
        return "ok" if now == approved["fingerprint"] else "changed"

    def approve(self, eid, by):
        """the entry's command as it is now is allowed: approved {fingerprint, at, by} ("player": in the App;
        "developer": the workshop by itself, where the setting says so, the name is kept for what is stored);
        saves, and gives the entry. One that is allowed as it is stays as it was. ApiError bad_manifest when its
        manifest cannot be read (there is no command to allow)"""
        if by not in APPROVERS:
            raise ApiError(400, "bad_request", 'by: "player" or "developer"')
        with self.lock:
            data = self.load()
            e = self.entry(eid, data)
            now = self.fingerprint(e["path"], kept=False)
            if now is None:
                raise ApiError(409, "bad_manifest", f"{e['id']}: its {M.MANIFEST} cannot be read, so there is no "
                                                    f"command to allow (extension_check says what is wrong with it)")
            if not approved_ok(e["approved"]) or e["approved"]["fingerprint"] != now:
                e["approved"] = dict(fingerprint=now, at=round(time.time(), 3), by=by)
                self.save(data)
            return e
