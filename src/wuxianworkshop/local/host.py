r"""The host of local extensions: the registered extensions and their processes, as the daemon uses them (Host: start,
stop, calls, the rows the API shows, what it adds to apps, what an extension asks and tells). What it
is made of is beside it:

    registry.py   the registry, <state>\extensions.json: {revision, extensions: [{id, path, enabled, options, described}]}
    home.py       the extensions' home folder, <WUXIAN_HOME>\extensions: the folders the workshop keeps itself
    process.py    one process of an extension (Proc): its environment and command, its threads, the end of its tree
    rpc.py        the protocol on its pipe (JSON-RPC 2.0, UTF-8, one object per line): requests, answers, errors
    logs.py       its output, <state>\extensions\<id>\output.log (stderr, stdout that is no protocol, the host's lines)
    check.py      check: a process of its own started and initialized, the extension's tests

The registry's revision goes up by one with every change (a registration, a start or a stop, another description) and is
told to on_change(revision) and the subscribers (subscribe).

An extension starts on the first call of one of its capabilities (start "use"), or with the daemon (start "workshop",
autostart()); it runs until it is stopped, set off, removed or the daemon ends (close(): shutdown to each, its process
tree ended after SHUTDOWN_WAIT seconds). One that crashed starts again at its next call. Nothing of it runs before its
command is approved (allow(): the registry says whether; else it is refused with needs_approval until the player
confirms it in the App, or, where the player's setting says so (auto()), the workshop approves it by itself; never
one whose entry has ask, which was not written on this computer), and nothing of one that needs a newer workshop
than this one (require(): requires_unmet).

An extension is where its folder is. One whose folder is right in the extensions' home folder (Host.home, home.py) is
kept by the workshop (the row's origin "home"): it is found there without being registered (discover(), before a
listing and before autostart: registered with ask, so the player allows it once in the App), it goes when its folder
went, and removing it moves the folder to the home's .removed. Any other is registered by its path (origin "linked":
extension_add, new_extension), and the workshop never touches its folder.

Where an extension is, is one field, its state (self.runs[id], changed only by to()):

    off        set off: it does not start
    stopped    it does not run (never started, stopped, or its manifest cannot be used: the row's error says so)
    starting   its process is being started and initialized
    running    it answered initialize
    hung       it runs but did not answer ping after a call timed out: its next call ends its tree and starts it again
    stopping   it was asked to stop
    exited     it ended by itself (the row's exit_code and error); its next call starts it again
    backoff    it could not start; after 1, 2, 3 and more failures in a row a call starts it again no sooner than 1, 5,
               30 and 120 s later (BACKOFF). An initialize that worked, extension_load, set on and an approval forget
               the failures

and the row says since when (since, epoch seconds) and why.

The extension tells event {source, data} (an item {source: <id>, kind: <id>.<source>, data, time} for on_event; the
first HELD_EVENTS it tells before it has answered initialize are kept until then, more are dropped)
and asks call {cap, args} (on_call: any capability, the game's or this computer's; the daemon allows see, keep and point).

Any thread may call the host; calls answer with concurrent.futures.Future. Every name this module had before it was
split is still here (the imports below): others import Host and call_error from it, and the tests set INIT_TIMEOUT,
SHUTDOWN_WAIT and LOG_MAX on it, so the code that reads those three reads this module's (Host below; logs.log_max()).
Standard library only.
"""
import os
import shutil                 # noqa: F401  (not used here any more; tests reach it as host.shutil)
import threading
import time
from concurrent.futures import Future
from pathlib import Path

from .. import __version__
from ..daemon.api import ApiError
from ..paths import extensions_home, state_dir
from . import adds as A
from . import check as C
from . import home as H
from . import manifest as M
from . import process as P
from . import rpc as R
from .check import TESTS_OUTPUT, TESTS_TIMEOUT                                             # noqa: F401
from .logs import (LINE_MAX, LOG_MAX, LOGS_MOST, TAIL, Lines, Output, check_window, clock,  # noqa: F401
                   output_path, tail, window)
from .process import (CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, KILL_WAIT, SHUTDOWN_WAIT,  # noqa: F401
                      WINDOWS, Proc, default_python, kill_tree, sdk_dir)
from .registry import (REPLACE_WAITS, Registry, check_folder, check_options, option_rows, replace_retrying,  # noqa: F401
                       write_json)
from .rpc import (BAD_ARGS, BUSY, CANCELLED, FAILED, INIT_TIMEOUT, NO_METHOD, PING_TIMEOUT, REASONS, Down,  # noqa: F401
                  RpcError, call_error)

STATES = ("off", "stopped", "starting", "running", "hung", "stopping", "exited", "backoff")
BACKOFF = (1.0, 5.0, 30.0, 120.0)     # seconds before a call starts it again, after 1, 2, 3 and more failed starts
HELD_EVENTS = 100                     # events of an extension that has not answered initialize yet, kept at most
QUIET_CODES = ("stopping", "stopped", "needs_approval", "not_found", "off")   # a start that ended so did not fail
DISCOVER_EVERY = 2.0                  # seconds between two looks at the extensions' home folder at least (discover)


def duration(seconds):
    return f"{seconds:.1f} s" if seconds < 120 else f"{seconds / 60:.1f} min"


def first_line(text, most=500):
    return (str(text).splitlines() or [""])[0][:most]


def blank_run(state, since):
    return dict(state=state, since=round(since, 3), why=None, started=None, exit_code=None, error=None, fails=0,
                retry_at=0.0)


# --- the host --------------------------------------------------------------------------------------------------------

class Host:
    """the registered local extensions and their processes. root: the state folder (default paths.state_dir());
    note(text): a line for the daemon's log; on_event(id, item): an item of an extension's source; on_call(id, cap,
    args, chain): an extension's call of a capability (chain: the caller chain of the call it is serving, a list
    of strings), a Future of its result (ApiError for a refusal); on_change(revision):
    the extensions changed. python: the interpreter for run ["python", ...] without the manifest's own (default:
    default_python()); sdk: the folder put first on PYTHONPATH (default: sdk_dir()); auto(): whether the workshop
    approves an extension's command by itself now (the daemon's setting extension_approval is "auto"; default: it
    does not, the player is asked). home: the extensions' home folder (default paths.extensions_home()); taken():
    the ids of the extensions the installed WuxianKit lists, which a folder that is not registered yet cannot have
    (default: none); kit_version(): the installed WuxianKit's version or None (a manifest's requires.kit is judged
    by it); addons_dir(): the game's AddOns folder or None while the game folder is not known, and interface(): the
    client's Interface number or None (both only kept here: installing an extension's game addons reads them)"""

    def __init__(self, root=None, *, version=__version__, note=None, on_event=None, on_call=None, on_change=None,
                 python=None, sdk=None, auto=None, home=None, taken=None, kit_version=None, addons_dir=None,
                 interface=None):
        self.root = Path(root) if root is not None else state_dir()
        self.version = version
        self.note = note or (lambda text: None)
        self.on_event, self.on_call, self.on_change = on_event, on_call, on_change
        self.python = python
        self.auto = auto or (lambda: False)         # auto(): the workshop approves a command by itself now
        self.home = Path(home) if home is not None else extensions_home()
        self.taken = taken or (lambda: ())          # taken(): the ids WuxianKit's extensions have (taken_ids)
        self.kit_version = kit_version or (lambda: None)
        self.addons_dir = addons_dir or (lambda: None)
        self.interface = interface or (lambda: None)
        self.looked = None            # when discover() last looked at the home folder (time.monotonic())
        self.passed = {}              # a folder there that was not registered (its path, case folded) -> why, as noted
        self.sdk = Path(sdk) if sdk is not None else sdk_dir()
        self.lock = threading.RLock()
        self.reg = Registry(self.root, self.lock, on_save=self.saved,   # extensions.json (registry.py), under self.lock
                            note=lambda text: self.note(text))         # (self.note may be replaced: asked each time)
        self.born = time.time()
        self.procs = {}               # id -> its Proc (starting, running or being stopped)
        self.starting = {}            # id -> the Future of its start in progress (stopped: stop() came meanwhile;
        #                               probe: nobody waits for the process, only for its description)
        self.runs = {}                # id -> where it is: state, since, why, started, exit_code, error, fails, retry_at
        self.outputs = {}             # id -> Output
        self.lines = {}               # output path -> Lines
        self.listeners = {}
        self.closing = False
        self.adds_cache = None        # adds.py's own: what it keeps until the registry is saved (saved())

    # --- telling of changes

    def subscribe(self, loop, callback):
        """callback() on that asyncio loop whenever the extensions changed; returns the function that ends it"""
        key = object()
        with self.lock:
            self.listeners[key] = (loop, callback)

        def unsubscribe():
            with self.lock:
                self.listeners.pop(key, None)
        return unsubscribe

    def changed(self):
        """revision + 1, told to on_change and the subscribers"""
        with self.lock:
            data = self.load()
            data["revision"] = revision = int(data.get("revision") or 0) + 1
            self.save(data)
            listeners = list(self.listeners.values())
        if self.on_change is not None:
            try:
                self.on_change(revision)
            except Exception as e:
                self.note(f"local extensions: telling of a change failed: {e!r}")
        for loop, callback in listeners:
            try:
                loop.call_soon_threadsafe(callback)
            except RuntimeError:
                pass
        return revision

    # --- the registry

    # (self.reg, registry.py: the file and its entries; these are its methods under the names the host always had)

    @property
    def registry(self):
        return self.reg.path

    def load(self):
        return self.reg.load()

    def save(self, data):
        self.reg.save(data)

    def saved(self):
        """(the registry's on_save) the file was written: what the extensions add to apps is worked out again"""
        self.adds_cache = None

    @property
    def revision(self):
        return self.reg.revision

    def entry(self, eid, data=None):
        """the registry's entry of an id; ApiError not_found"""
        return self.reg.entry(eid, data)

    def ids(self):
        return self.reg.ids()

    def update_entry(self, eid, **fields):
        return self.reg.update_entry(eid, **fields)

    # --- rows

    def output(self, eid):
        with self.lock:
            out = self.outputs.get(eid)
            if out is None:
                out = self.outputs[eid] = Output(self.output_path(eid))
            return out

    def output_path(self, eid):
        return output_path(self.root, eid)

    def run(self, e):
        """(under the lock) where a registry entry's extension is: {state, since, why, started, exit_code, error,
        fails, retry_at}. Made when it is first asked for; off follows the entry's enabled"""
        eid, rest = e["id"], "stopped" if e["enabled"] else "off"
        run = self.runs.get(eid)
        if run is None:
            run = self.runs[eid] = blank_run(rest, self.born)
        elif (run["state"] == "off") == e["enabled"] and eid not in self.procs and eid not in self.starting:
            run.update(state=rest, since=round(time.time(), 3), why=None)    # the registry was changed elsewhere
        return run

    def to(self, eid, state, why=None, **fields):
        """the extension is in that state from now on. why: a sentence for its row; fields: started, exit_code,
        error, fails, retry_at (those not given stay)"""
        with self.lock:
            run = self.runs.get(eid)
            if run is None:
                run = self.runs[eid] = blank_run(state, self.born)
            run.update(fields, state=state, since=round(time.time(), 3), why=why)
            return run

    def state_of(self, e, manifest_error=None):
        """(state, since, why, pid, started, exit_code, error) of an entry now"""
        run = self.run(e)
        proc = self.procs.get(e["id"])
        pid = proc.pid if proc is not None and proc.running else None
        state = run["state"]
        if state in ("starting", "running", "hung", "stopping"):
            return state, run["since"], run["why"], pid, (proc.started if pid else run["started"]), None, run["error"]
        if state == "off":
            return state, run["since"], run["why"], pid, run["started"], run["exit_code"], None
        if manifest_error:                              # nothing of it can run: stopped, and why
            return "stopped", run["since"], manifest_error, pid, run["started"], run["exit_code"], manifest_error
        return state, run["since"], run["why"], pid, run["started"], run["exit_code"], run["error"]

    def command_of(self, m):
        """the command the workshop runs for a manifest (argv, its first item a full path), or None when it has none
        that can be run"""
        if m is None:
            return None
        try:
            return self.command(m, self.environment(m))
        except (ApiError, OSError):
            return None

    @staticmethod
    def option_rows(e):
        return option_rows(e)

    @staticmethod
    def skill_names(folder):
        if not folder:
            return []
        try:
            return sorted(p.stem for p in Path(folder).glob("*.md") if p.is_file())
        except OSError:
            return []

    # what a row says of where the extension is from and what it needs (the manifest's author, homepage, requires and
    # addons; home.py)

    def taken_ids(self):
        """the ids a folder that is not registered yet cannot have: those of the installed WuxianKit's extensions
        (taken(); none when that cannot be asked now). Only add, check and discover use it: a registered extension
        keeps its id whatever WuxianKit lists later"""
        try:
            return tuple(x for x in (self.taken() or ()) if isinstance(x, str))
        except Exception:
            return ()

    def kit(self):
        """the installed WuxianKit's version, or None when it is not known now"""
        try:
            v = self.kit_version()
        except Exception:
            return None
        return v if isinstance(v, str) and v else None

    def origin(self, e):
        """"home": the entry's folder is right in the extensions' home folder, the workshop keeps it; "linked": it is
        registered by its path and the workshop never touches its folder. Worked out each time, never stored"""
        return "home" if H.inside(e["path"], self.home) else "linked"

    def unmet(self, m):
        """what of a manifest's requires this workshop and the installed WuxianKit do not meet (manifest.unmet):
        [{what, needs, has}]"""
        return M.unmet((m or {}).get("requires"), self.version, self.kit())

    def unmet_error(self, m):
        """the ApiError requires_unmet (409) of a manifest that needs a newer workshop than this one, or None. What it
        needs of WuxianKit does not stop it: that is only in its row's unmet"""
        short = next((u for u in self.unmet(m) if u["what"] == "workshop"), None)
        if short is None:
            return None
        return ApiError(409, "requires_unmet",
                        f"{m['id']} needs WuxianWorkshop {short['needs']} or newer, this is {short['has']}: update the "
                        f"workshop (the WuxianWorkshop App does it); nothing of it runs before that", unmet=[short])

    def require(self, m):
        """the gate beside allow(): nothing of an extension runs in a workshop older than its manifest's
        requires.workshop. ApiError requires_unmet (409)"""
        error = self.unmet_error(m)
        if error is not None:
            raise error

    def addon_rows(self, m):
        """the game addons of an extension as its row shows them: [{folder, version, state}], in the manifest's order.
        Here nothing is known of the game's AddOns folder yet: version None, state "unknown" (what reads
        addons_dir() and interface() says missing / outdated / ok / newer / foreign)"""
        return [dict(folder=name, version=None, state="unknown") for name in (m or {}).get("addons") or []]

    def row(self, e):
        """an extension as the API shows it. state, since (epoch seconds) and why: where it is (the module's text);
        command: what the workshop runs for it, None while its manifest cannot be used; adds: what its manifest adds to
        apps, and allowed: whether any of it is added now (adds.allowed: the player allowed its command, or the
        workshop allows commands by itself). origin: "home" (its folder is in the extensions' home folder) or
        "linked"; author, homepage, requires {workshop?, kit?}: the manifest's; unmet: [{what, needs, has}] of
        requires that this computer does not meet (a workshop that is too old is also the row's error: it does not
        start); addons: [{folder, version, state}], its game addons. A manifest that cannot be read: None, None, {},
        [], []"""
        m, problems = M.check_manifest(e["path"])
        error = None
        if m is None:
            first = M.errors_in(problems)[0]
            error = f"{first['where']}: {first['message']}" + (f" ({first['hint']})" if first.get("hint") else "")
        elif m["id"] != e["id"]:
            error = (f"its manifest's id is {m['id']} now, it was registered as {e['id']}: extension_remove "
                     f"{e['id']}, then extension_add the folder again")
            m = None
        short = self.unmet_error(m) if m is not None else None
        state, since, why, pid, started, exit_code, err = self.state_of(e, error or (short and short.message))
        desc = e.get("described") or {}
        return dict(id=e["id"], title=(m or {}).get("title") or e["id"], version=(m or {}).get("version"),
                    path=e["path"], enabled=e["enabled"], start=(m or {}).get("start") or "use", state=state,
                    since=since, why=why, pid=pid, started=started, exit_code=exit_code, error=err,
                    command=self.command_of(m),
                    capabilities=[c["id"] for c in desc.get("capabilities") or []],
                    sources=[s["name"] for s in desc.get("sources") or []], options=self.option_rows(e),
                    apps=[a["app"] for a in (m or {}).get("apps") or []],
                    skills=self.skill_names((m or {}).get("skills")),
                    adds=A.row(m), approval=self.reg.approval(e), allowed=A.allowed(self, e),
                    origin=self.origin(e), author=(m or {}).get("author"), homepage=(m or {}).get("homepage"),
                    requires=dict((m or {}).get("requires") or {}), unmet=self.unmet(m),
                    addons=self.addon_rows(m))

    def rows(self):
        with self.lock:
            return [self.row(e) for e in self.load()["extensions"]]

    def get(self, eid):
        with self.lock:
            return self.row(self.entry(eid))

    def listing(self):
        """{revision, extensions: [row], problem: why extensions.json cannot be read or written now (the registry's
        words; None: it can), home: the extensions' home folder}. What is in the home folder now is looked at first
        (discover)"""
        self.discover()
        with self.lock:
            data = self.load()
            return dict(revision=int(data.get("revision") or 0), extensions=[self.row(e) for e in data["extensions"]],
                        problem=self.reg.problem, home=str(self.home))

    def manifest_entries(self):
        """the enabled extensions as WuxianKit's manifest lists an extension (local_manifest): {id, title, version,
        notes, state "on", runtime "local", path, docs, skills, capabilities: [{id, kind, title, doc, args, timeout}],
        sources: [{name, title, doc}], options: [opt + value], apps: [declaration], adds: {app: {wake, skill}},
        allowed}. One whose manifest cannot be read now keeps what it described"""
        out = []
        with self.lock:
            data = self.load()
        for e in data["extensions"]:
            if not e["enabled"]:
                continue
            m, _ = M.check_manifest(e["path"])
            if m is not None and m["id"] != e["id"]:
                m = None
            desc = e.get("described") or {}
            out.append(dict(id=e["id"], title=(m or {}).get("title") or e["id"], version=(m or {}).get("version"),
                            notes=(m or {}).get("notes"), state="on", runtime="local", path=e["path"],
                            docs=(m or {}).get("docs"), skills=(m or {}).get("skills"),
                            capabilities=[{k: c[k] for k in ("id", "kind", "title", "doc", "args", "timeout")}
                                          for c in desc.get("capabilities") or []],
                            sources=[dict(s) for s in desc.get("sources") or []], options=self.option_rows(e),
                            apps=[dict(a) for a in (m or {}).get("apps") or []],
                            adds=A.row(m), allowed=A.allowed(self, e)))
        return dict(revision=int(data.get("revision") or 0), extensions=out)

    # what the enabled extensions add to apps (adds.py): the declarations of their apps, the wake notes and skill
    # supplements (the manifest's adds), an app's reactions, a control bound to a capability

    def declarations(self):
        return A.declarations(self)

    def enabled_manifests(self):
        return A.enabled_manifests(self)

    def adds(self, app):
        return A.adds(self, app)

    def app_reactions(self, app):
        return A.app_reactions(self, app)

    def control_call(self, app, cmd):
        return A.control_call(self, app, cmd)

    def approve(self, eid, by="player"):
        """the player (by "developer": the workshop by itself) allows this extension's command to run
        (registry.approve); its row. Its
        failed starts are forgotten. The player's approval also starts it: for good when its manifest says start
        "workshop", else, when it never described itself, for as long as it takes to learn what it can do (describe)"""
        with self.lock:
            e = self.entry(eid)
            eid = e["id"]
            self.reg.approve(eid, by)
            self.forgive(e)
        self.changed()
        if by == "player" and e["enabled"]:
            m, _ = M.check_manifest(e["path"])
            if m is not None and m["id"] == eid:
                if m["start"] == "workshop":
                    self.ensure(eid, "approved")
                elif e.get("described") is None:
                    self.describe(eid)
        return self.get(eid)

    def allow(self, e):
        """the gate before anything of a registered extension runs (a start, a check's process, its tests): nothing
        when the registry says its command is approved as it is now. Else, where the setting says so (auto()), the
        workshop approves it here, unless the entry has ask (it came in a package or was found in the extensions'
        home folder: somebody else may have written it, so only the player allows it, whatever the setting; and
        again whenever what it runs changed); otherwise ApiError needs_approval (403)"""
        approval = self.reg.approval(e)
        if approval == "ok":
            return
        ask = bool(e.get("ask"))
        if self.auto() and not ask:
            self.reg.approve(e["id"], "developer")
            self.note(f"local extension {e['id']}: its command is approved (the setting allows it by itself)")
            return
        raise ApiError(403, "needs_approval",
                       f"{e['id']} " + ("changed the command it starts with since the player confirmed it"
                                        if approval == "changed" else "is new: the player has not confirmed the "
                                                                      "command it starts with")
                       + (" (it came in a package or was found in the extensions folder, so the workshop does not "
                          "approve it by itself)" if ask and self.auto() else "")
                       + ". The player confirms it on the Extensions page of the WuxianWorkshop App; nothing of it "
                         "runs before that", approval=approval)

    def forgive(self, e):
        """(under the lock) its failed starts are forgotten: an explicit start does not wait"""
        run = self.run(e)
        run.update(fails=0, retry_at=0.0)
        if run["state"] == "backoff":
            run.update(state="stopped", since=round(time.time(), 3))

    def describe(self, eid):
        """starts an extension only to learn what it can do (an extension with start "use" has no tools and no options
        before its first start): once it has described itself it is stopped again, unless a call came for it
        meanwhile. A Future (of None, or of its Proc when it runs anyway; the error of a start that failed)"""
        return self.ensure(eid, "to learn what it can do", probe=True)

    def capability(self, cap_id):
        """(entry, the described capability or None) of a capability id whose extension is registered; None when no
        registered extension has its id's first part"""
        return self.reg.capability(cap_id)

    def kind_of(self, cap_id):
        """see / keep / point of a capability of a local extension (as it last described itself), else None"""
        return self.reg.kind_of(cap_id)

    # --- registering

    def add(self, path):
        """registers an extension by its folder or its manifest (an absolute path): its row (the one there when the
        folder is registered already, whatever its manifest says now). ApiError bad_manifest (with problems: an id
        one of WuxianKit's extensions has is one, taken_ids), extension_exists (another folder has its id). Started
        at once when its manifest says start "workshop" """
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, "bad_request", "path: the full path of an extension's folder (or its wuxian-extension.json)")
        p = Path(path.strip())
        if not p.is_absolute():
            raise ApiError(400, "bad_request", f"path: a full path ({path} is not)")
        check_folder(p)
        folder, _ = M.locate(p.resolve())
        key = os.path.normcase(str(folder))
        with self.lock:
            same = next((e for e in self.load()["extensions"] if os.path.normcase(e["path"]) == key), None)
            if same is not None:
                return self.row(same)
        m, problems = M.check_manifest(folder, taken=self.taken_ids())
        if m is None:
            raise ApiError(400, "bad_manifest", str(M.ManifestError(problems)), problems=problems)
        e = self.register(folder, m)
        if m["start"] == "workshop" and e["id"] == m["id"]:
            self.ensure(m["id"], "start workshop")
        return self.get(e["id"])

    def register(self, folder, m, ask=False, how="registered"):
        """a new entry for the extension in `folder`: {id, path, enabled true, options {}, described None}, with
        ask true when `ask` (it was not written on this computer: a package, or a folder found in the extensions'
        home folder; allow() then leaves its approval to the player). Saved, noted (`how`: the note's word for what
        happened) and told (changed()); the entry (a copy). Nothing of it is started here.

        folder: its folder as the registry keeps it, an absolute, resolved path (str or Path). m: its manifest as
        manifest.check_manifest(folder, taken=host.taken_ids()) gave it; the caller checked it, here only its id is
        read. A folder that is registered already keeps its entry, which is what comes back (ask is set on it when
        `ask`; nothing else of it changes: reset() is for a folder whose content was replaced). ApiError
        extension_exists (409) when another folder has the id"""
        folder = str(folder)
        key = os.path.normcase(folder)
        with self.lock:
            data = self.load()
            same = next((e for e in data["extensions"] if os.path.normcase(e["path"]) == key), None)
            if same is not None:
                if not ask or same.get("ask"):
                    return same
                same["ask"] = True
                self.save(data)
                e = same
            else:
                other = next((e for e in data["extensions"] if e["id"] == m["id"]), None)
                if other is not None:
                    raise ApiError(409, "extension_exists", f"the id {m['id']} is taken by {other['path']}: give this "
                                                            f"one another id, or extension_remove that one first")
                e = dict(id=m["id"], path=folder, enabled=True, options={}, described=None)
                if ask:
                    e["ask"] = True
                data["extensions"].append(e)
                self.save(data)
                self.note(f"local extension {m['id']}: {how} ({folder})"
                          + (": the player allows it on the Extensions page before anything of it runs" if ask else ""))
        self.changed()
        return self.entry(e["id"])

    def reset(self, eid, folder=None):
        """a registered extension whose folder's content was replaced by another version (a package installed over
        it) is new again: ask true, its approval and its description gone (the player allows the new one before
        anything of it runs, and it describes itself again); enabled and options stay, its failed starts are
        forgotten. folder: where it is now, when that is another path than before (absolute, resolved). Saved, noted
        and told (changed()); the entry (a copy). ApiError not_found.

        It does not stop anything and does not touch the folder. The caller stops the extension first (stop()), then
        holds host.lock from before the old folder is moved away (home.trash) until this returns: discover() takes
        an extension of the home folder whose folder is not there off the registry, and it waits for that lock"""
        with self.lock:
            data = self.load()
            e = self.entry(eid, data)
            e.update(ask=True, approved=None, described=None)
            if folder is not None:
                e["path"] = str(folder)
            self.save(data)
            self.forgive(e)
            self.runs[e["id"]].update(error=None, exit_code=None)
        self.note(f"local extension {e['id']}: replaced by another version ({e['path']}): the player allows it on the "
                  f"Extensions page before anything of it runs")
        self.changed()
        return self.entry(e["id"])

    def drop(self, e, gone=False):
        """(under the lock) takes an entry off the registry, with what the host keeps of its extension (which was
        stopped before): what forgotten() ends, or None when the entry was not there. gone: only if its folder is
        still not there (discover: looked at again under the lock, so a folder that is being replaced is not taken
        for one that went)"""
        data = self.load()
        there = next((x for x in data["extensions"] if x["id"] == e["id"]
                      and os.path.normcase(x["path"]) == os.path.normcase(e["path"])), None)
        if there is None or (gone and os.path.isdir(there["path"])):
            return None
        data["extensions"] = [x for x in data["extensions"] if x is not there]
        self.save(data)
        self.runs.pop(e["id"], None)
        left = self.procs.pop(e["id"], None)           # one that would not end: nothing of it stays here
        return left, self.outputs.pop(e["id"], None)

    def forgotten(self, e, held, why=""):
        """(not under the lock: a process's end takes it) after drop(): what was left of it is ended, noted, told"""
        left, out = held
        if left is not None:
            left.kill()
        if out is not None:
            out.close()
        self.note(f"local extension {e['id']}: no longer registered ({e['path']}{why})")
        self.changed()

    def remove(self, eid):
        """takes an extension off the registry (stopped first): {removed: True, id, path, kept}. A linked one's
        folder stays where it is (kept None). One in the extensions' home folder has its folder moved to the home's
        .removed (home.trash; kept: where it is now, the newest home.KEPT stay there); ApiError folder_busy (409)
        when it cannot be moved now: then it is still registered, and stopped"""
        e = self.entry(eid)
        home = self.origin(e) == "home"
        self.stop(e["id"], quiet=True)
        kept = None
        with self.lock:                                # (held over the move: discover() sees both or neither)
            if home and os.path.lexists(e["path"]):
                try:
                    kept = str(H.trash(e["path"], self.home, e["id"]))
                except FileNotFoundError:              # it went meanwhile: nothing to keep
                    kept = None
                except OSError as ex:
                    raise ApiError(409, "folder_busy",
                                   f"{e['id']} is stopped, but its folder cannot be moved away now ({type(ex).__name__}"
                                   f": {ex}): a program has it or one of its files open (an editor, a terminal in "
                                   f"that folder, a process of it that has not ended). Close that and remove it "
                                   f"again; it is still registered", path=e["path"]) from None
            held = self.drop(e)
        if held is not None:
            self.forgotten(e, held, "" if kept is None else f"; its folder is kept as {kept}")
        return dict(removed=True, id=e["id"], path=e["path"], kept=kept)

    # --- the extensions' home folder (home.py)

    def discover(self, force=False):
        """looks at the extensions' home folder: a folder there with a manifest that is not registered (by its path)
        is registered when its manifest passes its check and nobody has its id ({enabled: true, ask: true}, not
        approved: the player allows it in the App; noted). One that does not pass, or whose id is taken, is left
        out (noted once, and again when the reason is another one). A registered extension of the home folder whose
        folder is not there any more is stopped and taken off the registry (noted). True when the registry changed
        (saved, told: changed()).

        Called before a listing and before autostart; it looks at most once in DISCOVER_EVERY seconds (force: now).
        Nothing it finds is started here, and nothing here fails the caller: what went wrong is noted"""
        now = time.monotonic()
        with self.lock:
            if self.closing or (not force and self.looked is not None and now - self.looked < DISCOVER_EVERY):
                return False
            self.looked = now
        try:
            return self.look()
        except ApiError as e:                          # the registry cannot be read now: its listing says so
            if e.code != "registry_unreadable":
                self.note(f"local extensions: the extensions folder could not be looked at: {first_line(e.message, 300)}")
        except Exception as e:
            self.note(f"local extensions: the extensions folder could not be looked at: {e!r}")
        return False

    def look(self):
        """(discover) what is in the home folder against the registry; True when the registry changed"""
        folders = [(Path(os.path.realpath(f)), f.name) for f in H.found(self.home)]
        taken = self.taken_ids() if folders else ()
        with self.lock:
            entries = self.load()["extensions"]
            known = {os.path.normcase(e["path"]) for e in entries}
            gone = [e for e in entries if not os.path.isdir(e["path"]) and H.inside(e["path"], self.home)] \
                if os.path.isdir(self.home) else []    # (a home folder that is not there says nothing of what was in it)
            for key in set(self.passed) - {os.path.normcase(str(f)) for f, _ in folders}:
                del self.passed[key]                   # it went, or was repaired and registered: said again if it is back
        changed = False
        for e in gone:
            try:
                self.stop(e["id"], quiet=True)
            except ApiError:                           # removed meanwhile
                continue
            with self.lock:
                held = self.drop(e, gone=True)
            if held is not None:
                self.forgotten(e, held, "; its folder in the extensions folder is gone")
                changed = True
        for folder, name in folders:
            key = os.path.normcase(str(folder))
            if key in known:
                continue
            m, problems = M.check_manifest(folder, taken=taken)
            why = None
            if m is None:
                first = M.errors_in(problems)[0]
                why = f"{first['where']}: {first['message']}" + (f" ({first['hint']})" if first.get("hint") else "")
            else:
                try:
                    self.register(folder, m, ask=True, how="found in the extensions folder, registered")
                    changed = True
                except ApiError as refused:            # extension_exists: another folder has its id
                    why = first_line(refused.message, 300)
            with self.lock:
                if why is None:
                    self.passed.pop(key, None)
                elif self.passed.get(key) != why:
                    self.passed[key] = why
                    self.note(f"local extensions: {name} in the extensions folder is not registered: {why}")
        return changed

    def set(self, eid, enabled=None, options=None):
        """enabled false stops it (its capabilities go), true lets it start again (start workshop: now); options: some
        of its options by name (as it described them), checked; a running one is told all of them"""
        if enabled not in (None, True, False):
            raise ApiError(400, "bad_request", "enabled: true or false")
        if options is not None and not isinstance(options, dict):
            raise ApiError(400, "bad_request", "options: an object, name -> value")
        e = self.entry(eid)
        eid = e["id"]
        if options:
            check_options(e, options)
        with self.lock:
            data = self.load()
            e = self.entry(eid, data)
            before = (e["enabled"], dict(e["options"]))
            if options:
                e["options"].update(options)
            if enabled is not None:
                e["enabled"] = enabled
            self.save(data)
            proc = self.procs.get(eid)
        if options and proc is not None and proc.ready:
            proc.notify("options", dict(options=self.option_values(eid)))
        if enabled is False:
            self.stop(eid, quiet=True)
            with self.lock:
                run = self.run(e)                       # off now (run() follows the entry), unless it would not end
                if run["state"] == "off":
                    run.update(why="it was set off", error=None, fails=0, retry_at=0.0)
        elif enabled is True:
            with self.lock:
                self.forgive(e)                         # set on: it may start at once
        if (e["enabled"], e["options"]) != before:
            self.changed()
        if enabled is True and not before[0]:
            m, _ = M.check_manifest(e["path"])
            if m is not None and m["start"] == "workshop":
                self.ensure(eid, "set on")
        return self.get(eid)

    def option_values(self, eid):
        return self.reg.option_values(eid)

    # --- starting (process.py: what a process is started with, and Proc)

    def environment(self, m):
        """what its process is started with (process.environment); WUXIAN_EXTENSION_DATA is its data folder in this
        host's state folder, <state>/extensions/<id>/data, made before the process starts"""
        return P.environment(m, self.sdk, self.root)

    def interpreter(self, m):
        """the Python of an extension (the manifest's, else the workshop's), or None"""
        return P.interpreter(m, self.python)

    def command(self, m, env):
        """the argv it starts with: its first item a full path; ApiError bad_command when it is not there"""
        return P.command(m, env, self.python)

    def spawn(self, m, output, on_message=None, on_end=None):
        """the extension's process, its pipe open (ApiError bad_command / start_failed; requires_unmet: no process
        of one that needs a newer workshop, whoever asks for it)"""
        self.require(m)
        env = self.environment(m)
        argv = self.command(m, env)
        return P.spawn(m, env, argv, output, on_message or self.on_message, on_end or self.ended)

    def initialize(self, proc, m, options, timeout=None):
        """initialize: (description, problems); Down when it did not answer one that can be used"""
        timeout = INIT_TIMEOUT if timeout is None else timeout
        fut = proc.request("initialize", dict(protocol=dict(R.PROTOCOL), workshop=self.version, id=m["id"],
                                              options=options))
        try:
            answer = fut.result(timeout)
        except TimeoutError:
            proc.cancel(fut)
            raise self.down(proc, f"{m['id']} did not answer initialize within {timeout:.0f} s") from None
        except RpcError as e:
            raise self.down(proc, f"{m['id']} answered initialize with an error: {e.message}") from None
        except ApiError as e:
            if e.code != "down":                       # busy: its pipe took no line
                raise self.down(proc, f"{m['id']} could not be asked initialize: {e.message}") from None
            proc.ended.wait(2.0)
            raise self.down(proc, f"{m['id']} exited before it answered initialize (code {proc.exit_code})") from None
        why = R.protocol_problem(answer)
        if why:                                         # no protocol both speak: it is stopped (its starter does)
            proc.output.write(f"-- {clock()} it cannot be used: {why}")
            raise Down(f"{m['id']}: {why}", [M.problem("error", "initialize: protocol", why,
                                                       "the workshop's initialize says which versions it speaks "
                                                       "(protocol {min, max}); the SDK answers one of them")])
        return C.check_description(m["id"], answer)

    def down(self, proc, message):
        lines = tail(proc.output.path)
        text = message + ("\nthe end of its output (extension_logs):\n" + "\n".join(lines) if lines else "")
        return Down(text, [M.problem("error", "initialize", message, "extension_logs shows its output: a traceback "
                                                                     "says what went wrong")])

    def ensure(self, eid, why=None, fresh=False, probe=False):
        """a Future of the extension's Proc once it runs: at once when it runs, the start in progress, or a new start.
        The Future fails at once with ApiError off, requires_unmet (require), needs_approval (allow) or down (it
        failed to start a moment ago and is not tried again yet: backoff), later with Down when it cannot start. A hung one has its tree ended and
        starts again. fresh: an explicit start (extension_load), which forgets its failed starts; probe: nobody
        waits for the process, only for what it describes (describe)"""
        with self.lock:
            fut = Future()
            if self.closing:
                fut.set_exception(ApiError(503, "stopping", "the daemon is shutting down"))
                return fut
            proc = self.procs.get(eid)
            state = (self.runs.get(eid) or {}).get("state")
            if proc is not None and proc.ready and proc.running and not proc.stopping and state != "hung":
                fut.set_result(proc)
                return fut
            if eid in self.starting:
                if not probe:
                    self.starting[eid].probe = False
                return self.starting[eid]
            e = self.entry(eid)
            if not e["enabled"]:
                fut.set_exception(ApiError(409, "off", f"{eid} is off: extension_set {{\"id\": \"{eid}\", "
                                                       f"\"enabled\": true}} turns it on"))
                return fut
            try:
                m, _ = M.check_manifest(e["path"])
                if m is not None and m["id"] == e["id"]:
                    self.require(m)                    # (a manifest that cannot be used: its start says so)
                self.allow(e)
            except ApiError as refused:
                fut.set_exception(refused)
                return fut
            run = self.run(e)
            if fresh:
                self.forgive(e)
            wait = run["retry_at"] - time.time()
            if run["state"] == "backoff" and wait > 0:
                fut.set_exception(Down(f"{eid} could not start {run['fails']} time{'s' if run['fails'] > 1 else ''} "
                                       f"in a row ({run['error']}); the workshop tries again no sooner than in "
                                       f"{wait:.0f} s. extension_load starts it now; extension_logs shows its output"))
                return fut
            if proc is not None:                       # hung, being stopped, or the rest of a start that failed
                del self.procs[eid]                    # (what ends it now says nothing about the next run)
            fut.old, fut.stopped, fut.probe = proc, False, probe
            self.starting[eid] = fut
            self.to(eid, "starting", why or "a call of one of its capabilities", error=None, exit_code=None)
        threading.Thread(target=self.start, args=(eid, fut, why), name=f"wuxian-ext-{eid}-start", daemon=True).start()
        return fut

    def start(self, eid, fut, why):
        """(its thread) starts an extension and initializes it; fut gets its Proc or the error (a probe: None, and it
        is stopped again)"""
        proc, blamed = None, True                      # blamed: a failure counts as a failed start (backoff)
        try:
            old = fut.old
            if old is not None:                        # the run before: gone before this one starts
                if old.stopping or not old.running:
                    old.finished.wait(SHUTDOWN_WAIT + KILL_WAIT)
                else:
                    old.output.write(f"-- {clock()} it is hung: its process tree is ended, and it starts again")
                    old.stopping = True
                old.kill()
            e = self.entry(eid)
            m, problems = M.check_manifest(e["path"])
            if m is None or m["id"] != eid:
                blamed = False                         # nothing was started: its row says what is wrong
                if m is None:
                    raise Down(str(M.ManifestError(problems)), problems)
                raise Down(f"its manifest's id is {m['id']} now: extension_remove {eid}, then extension_add the folder")
            short = self.unmet_error(m)
            if short is not None:                      # nothing was started: its row says what it needs
                blamed = False
                raise short
            out = self.output(eid)
            with self.lock:
                if self.closing:
                    raise ApiError(503, "stopping", "the daemon is shutting down")
                if fut.stopped:
                    raise ApiError(409, "stopped", f"{eid} was stopped while it started")
                self.allow(e)
                proc = self.spawn(m, out)
                self.procs[eid] = proc
                self.runs[eid]["started"] = proc.started
            self.note(f"local extension {eid}: starting" + (f" ({why})" if why else "") + f", pid {proc.pid}")
            self.changed()
            desc, _ = self.initialize(proc, m, dict(e["options"]))
            with self.lock:
                data = self.load()
                entry = next((x for x in data["extensions"] if x["id"] == eid), None)
                described = entry is not None and entry.get("described") != desc
                if described:
                    entry["described"] = desc
                    self.save(data)
                if fut.stopped or proc.stopping:
                    raise ApiError(409, "stopped", f"{eid} was stopped while it started")
                if not proc.running:
                    raise self.down(proc, f"{eid} exited right after it answered initialize (code {proc.exit_code})")
                took = duration(time.time() - proc.started)
                proc.sources = [s["name"] for s in desc["sources"]]
                proc.ready = True
                probe = fut.probe
                self.starting.pop(eid, None)
                if probe:                              # nobody waits for it: it has said what it can do
                    proc.stopping = True
                    self.to(eid, "stopping", "it has described itself; its first call starts it", fails=0,
                            retry_at=0.0, error=None)
                else:
                    self.to(eid, "running", f"ready in {took}" + (f" ({why})" if why else ""), fails=0, retry_at=0.0,
                            error=None)
            out.write(f"-- {clock()} ready: {len(desc['capabilities'])} capabilities, {len(desc['sources'])} sources, "
                      f"{len(desc['options'])} options")
            self.note(f"local extension {eid}: running (pid {proc.pid}, ready in {took})")
        except Exception as ex:
            error = ex if isinstance(ex, ApiError) else ApiError(500, "internal", f"{eid}: {ex!r}")
            said = first_line(error.message)
            with self.lock:
                self.starting.pop(eid, None)
                stopped = fut.stopped or (proc is not None and proc.stopping) or error.code in QUIET_CODES
                started = proc.started if proc is not None else None
                if stopped:
                    if error.code not in QUIET_CODES:  # what ending it looked like from here says nothing
                        error = ApiError(409, "stopped", f"{eid} was stopped while it started")
                    self.to(eid, "stopped", first_line(error.message), started=started,
                            error=said if error.code == "needs_approval" else None)
                elif not blamed:
                    self.to(eid, "stopped", said, started=started, error=said)
                else:
                    run = self.runs[eid]
                    fails = run["fails"] + 1
                    wait = BACKOFF[min(fails, len(BACKOFF)) - 1]
                    self.to(eid, "backoff", f"it could not start ({fails} time{'s' if fails > 1 else ''} in a row): "
                                            f"{said}. A call starts it again no sooner than {wait:.0f} s from now; "
                                            f"extension_load starts it at once",
                            started=started, error=said, fails=fails, retry_at=time.time() + wait)
                if proc is not None:
                    proc.stopping = True               # (under the lock: a start that comes now waits for its end)
            if proc is not None:
                if not stopped:
                    self.output(eid).write(f"-- {clock()} failed: {said}")
                proc.kill()
            self.note(f"local extension {eid}: " + ("stopped while it started" if stopped and error.code == "stopped"
                                                    else f"could not start: {said[:300]}"))
            self.changed()
            fut.set_exception(error)
            return
        self.release(proc)
        if probe:
            proc.shutdown(SHUTDOWN_WAIT)               # ended() says stopped, and tells of the change
            fut.set_result(None)
            return
        self.changed()
        fut.set_result(proc)

    def ended(self, proc):
        """(its waiter thread) a process ended: by a stop, or by itself. One that ended before it was ready is its
        start's to tell of; one a new start took over (hung, stopped late) says nothing about the extension now"""
        took = duration(time.time() - proc.started)
        with self.lock:
            current = self.procs.get(proc.id) is proc
            if current:
                del self.procs[proc.id]
                if proc.ready and proc.stopping:
                    self.to(proc.id, "stopped", f"it was stopped after {took}", started=proc.started,
                            exit_code=proc.exit_code, error=None)
                elif proc.ready:
                    self.to(proc.id, "exited", f"it exited by itself with code {proc.exit_code} after {took}; its "
                                               f"next call starts it again",
                            started=proc.started, exit_code=proc.exit_code,
                            error=None if proc.exit_code == 0 else
                            f"it exited with code {proc.exit_code} (extension_logs shows why)")
        proc.output.write(f"-- {clock()} " + ("stopped" if proc.stopping else "exited")
                          + f" with code {proc.exit_code} after {took}")
        if current and proc.ready:
            if not proc.stopping:
                self.note(f"local extension {proc.id}: exited with code {proc.exit_code} after {took}"
                          + ("" if proc.exit_code == 0 else " (extension_logs shows its output); its next call starts "
                                                            "it again"))
            self.changed()

    # --- stopping

    def stop(self, eid, quiet=False):
        """stops an extension that runs or is being started (shutdown, then its tree): its row. A start in progress
        ends as stopped, whatever it was doing"""
        e = self.entry(eid)
        eid = e["id"]
        with self.lock:
            proc, fut = self.procs.get(eid), self.starting.get(eid)
            if fut is not None:
                fut.stopped = True                     # its start sees this before it spawns, and before it is ready
            if proc is not None:
                proc.stopping = True
                if proc.ready and proc.running:
                    self.to(eid, "stopping", "it was asked to stop")
        if proc is not None:
            proc.shutdown(SHUTDOWN_WAIT)               # this module's SHUTDOWN_WAIT, as it is now
        if fut is not None:                            # its start says stopped; that is waited for
            try:
                fut.exception(SHUTDOWN_WAIT + 2 * KILL_WAIT)
            except Exception:                          # not within that time: its row says where it is
                pass
        if (proc is not None or fut is not None) and not quiet:
            self.note(f"local extension {eid}: stopped")
        return self.get(eid)

    def close(self):
        """the daemon stops: every extension gets shutdown, and those that have not exited within SHUTDOWN_WAIT
        seconds have their trees ended (all at once)"""
        with self.lock:
            self.closing = True
            procs = list(self.procs.values())
            for fut in self.starting.values():
                fut.stopped = True
            for p in procs:
                p.stopping = True
        wait = SHUTDOWN_WAIT
        threads = [threading.Thread(target=p.shutdown, args=(wait,), daemon=True) for p in procs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(wait + 2 * KILL_WAIT + 1)
        with self.lock:
            outputs, self.outputs = list(self.outputs.values()), {}
        for out in outputs:
            out.close()

    def autostart(self):
        """(the daemon's start) the enabled extensions whose manifest says start "workshop" start. What is in the
        extensions' home folder is looked at first (discover)"""
        self.discover(force=True)
        for e in self.load()["extensions"]:
            if not e["enabled"]:
                continue
            m, _ = M.check_manifest(e["path"])
            if m is not None and m["id"] == e["id"] and m["start"] == "workshop":
                fut = self.ensure(e["id"], "start workshop")
                if fut.done() and fut.exception() is not None:
                    self.note(f"local extension {e['id']}: not started with the workshop: "
                              f"{first_line(getattr(fut.exception(), 'message', fut.exception()), 300)}")

    # --- the protocol: what an extension asks and tells

    def on_message(self, proc, msg):
        method, params = msg.get("method"), msg.get("params")
        params = params if isinstance(params, dict) else {}
        if "id" in msg:                                       # a request
            if method != "call":
                proc.respond(msg["id"], error=R.error("no_capability", f"no method {method!r}: the workshop answers "
                                                                       "call {cap, args}"))
                return
            self.extension_call(proc, msg["id"], params)
            return
        if method == "event":
            self.extension_event(proc, params)
        else:
            proc.output.write(f"-- {clock()} a notification the workshop does not know: {method!r}")

    def extension_event(self, proc, params):
        """(its reader thread) an event it tells. Before it has answered initialize nobody knows its sources: the
        first HELD_EVENTS are kept until then (release), more are dropped, and its output says so"""
        with self.lock:
            if proc.held is not None:
                if len(proc.held) < HELD_EVENTS:
                    proc.held.append(params)
                else:
                    proc.dropped += 1
                    if proc.dropped == 1:
                        proc.output.write(f"-- {clock()} it tells events before it has answered initialize: "
                                          f"{HELD_EVENTS} are kept until it has, the rest are dropped")
                return
        self.deliver(proc, params)

    def deliver(self, proc, params):
        """an event of an extension that described itself: an item for on_event, when it is of one of its sources"""
        name, data = params.get("source"), params.get("data")
        sources = proc.sources or []
        if not isinstance(name, str) or name not in sources:
            proc.output.write(f"-- {clock()} event from source {name!r} dropped: declare it (it declares: "
                              f"{', '.join(sources) or 'none'})")
            return
        item = dict(source=proc.id, kind=f"{proc.id}.{name}", data=data, time=round(time.time(), 3))
        if self.on_event is not None:
            self.on_event(proc.id, item)

    def release(self, proc):
        """(its start, once it is ready) the events kept for it go on, in the order it told them; from then on its
        events are not kept"""
        while True:
            with self.lock:
                batch, proc.held = proc.held, []
                if not batch:
                    proc.held, dropped, proc.dropped = None, proc.dropped, 0
                    break
            for params in batch:
                try:
                    self.deliver(proc, params)
                except Exception as e:
                    proc.output.write(f"-- {clock()} the workshop could not pass an event on: {e!r}")
        if dropped:
            proc.output.write(f"-- {clock()} {dropped} event{'s' if dropped > 1 else ''} it told before it answered "
                              f"initialize dropped ({HELD_EVENTS} are kept at most): it should tell events after its "
                              f"start")

    def extension_call(self, proc, rid, params):
        cap, args = params.get("cap"), params.get("args")
        if not isinstance(cap, str) or not cap:
            proc.respond(rid, error=R.error("bad_args", "cap: a capability id, such as sense.bags or voice.say"))
            return
        if args is not None and not isinstance(args, dict):
            proc.respond(rid, error=R.error("bad_args", "args: an object"))
            return
        if self.on_call is None:
            proc.respond(rid, error=R.error("failed", "calls are not available here"))
            return
        chain = params.get("chain")       # the chain of the call the extension is serving, as its SDK passes it on
        chain = [x for x in chain if isinstance(x, str)][:8] if isinstance(chain, list) else []
        try:
            fut = self.on_call(proc.id, cap, args, chain)
        except Exception as e:
            fut = Future()
            fut.set_exception(e)

        def answered(f):
            try:
                result = f.result()
            except BaseException as e:                 # an ApiError's code is its reason; a cancelled Future too
                proc.respond(rid, error=R.error_of(e))
                return
            proc.respond(rid, result=result)
        fut.add_done_callback(answered)

    # --- calls

    def call(self, proc, name, args, caller, deadline_ms=None):
        """a call of a capability of a running Proc: a Future of its result (RpcError for the extension's error answer;
        ApiError down when it ended, busy when its pipe takes no line, cancelled after cancel_call). deadline_ms: how
        long the caller still waits, told to the extension"""
        params = dict(name=name, args=args or {}, caller=caller or {"by": "agent"})
        if deadline_ms is not None:
            params["deadline_ms"] = max(0, int(deadline_ms))
        return proc.request("call", params)

    def cancel_call(self, proc, fut, ping=False):
        """the caller of call()'s Future waits no longer: the extension is told cancel {id}, its answer is dropped
        and the Future ends (ApiError cancelled) unless it is done. ping: the call timed out, so the extension is
        asked ping; one that does not answer within PING_TIMEOUT seconds is hung (its tree is ended and it starts
        again at its next call)"""
        proc.cancel(fut)
        if ping:
            self.ping(proc)

    def ping(self, proc):
        """asks a running extension ping (one at a time): any answer, an error too, says it is alive (an SDK that
        does not know ping answers no_capability); none within rpc.PING_TIMEOUT seconds, or a pipe that takes no
        line: its state is hung. The Future of the ping, or None when none was asked"""
        with self.lock:
            if proc.pinging or self.procs.get(proc.id) is not proc or not proc.ready or not proc.running \
                    or proc.stopping or self.runs[proc.id]["state"] != "running":      # (one that is hung: known)
                return None
            proc.pinging = True
        timeout = R.PING_TIMEOUT
        asked = proc.request("ping", {})

        def judge():
            try:
                error = asked.exception(timeout)
                alive = error is None or isinstance(error, RpcError)
            except Exception:                          # no answer in time
                alive = False
            hung = False
            with self.lock:
                proc.pinging = False
                if not alive and self.procs.get(proc.id) is proc and proc.running and not proc.stopping \
                        and self.runs[proc.id]["state"] == "running":
                    hung = True
                    why = (f"a call of it timed out and it did not answer ping within {timeout:.0f} s: its next call "
                           f"ends its process tree and starts it again")
                    self.to(proc.id, "hung", why, error=why)
            if not asked.done():
                proc.cancel(asked)
            if hung:
                proc.output.write(f"-- {clock()} hung: it did not answer ping within {timeout:.0f} s")
                self.note(f"local extension {proc.id}: hung (no answer to ping within {timeout:.0f} s); its next "
                          f"call starts it again")
                self.changed()
        threading.Thread(target=judge, name=f"wuxian-ext-{proc.id}-ping", daemon=True).start()
        return asked

    def call_sync(self, cap_id, args=None, caller=None, timeout=30.0):
        """(tests, scripts) a capability's result, waiting here; ApiError as local_call raises it"""
        found = self.capability(cap_id)
        if found is None:
            raise ApiError(404, "no_capability", f"no local extension has {cap_id}")
        proc = self.ensure(found[0]["id"]).result(INIT_TIMEOUT + 10)
        fut = self.call(proc, cap_id.partition(".")[2], args, caller, deadline_ms=int(timeout * 1000))
        try:
            return fut.result(timeout)
        except TimeoutError:
            self.cancel_call(proc, fut, ping=True)
            raise ApiError(504, "timeout", f"{cap_id} did not answer within {timeout:.0f} s") from None
        except Exception as e:
            raise call_error(e, cap_id) from None

    # --- checking

    def resolve(self, ident):
        """(registered entry or None, folder) of an id or a path"""
        return C.resolve(self, ident)

    def check(self, ident):
        """an extension checked without touching its running process: {ok, problems, capabilities, tests?}. The
        manifest; a process of its own started, initialized (its description checked) and shut down; its tests (a
        tests folder) run with its Python"""
        return C.check(self, ident)

    def check_message(self, proc, msg):
        """what an extension asks or tells during a check: its events are dropped, its calls refused"""
        C.check_message(proc, msg)

    def run_tests(self, m):
        """{ran, failed, output} of `python -m unittest discover -s tests` in the extension's folder, with its Python;
        None when it has no tests folder or is no Python extension (or needs a newer workshop: nothing of it runs)"""
        if self.unmet_error(m) is not None:
            return None
        return C.run_tests(self, m)

    def load_(self, eid):
        """extension_load: checked, then (re)started: {row, check}. A manifest that cannot be used leaves a running
        one as it is. An explicit start: its failed starts are forgotten. ApiError needs_approval before the player
        allowed its command, requires_unmet when it needs a newer workshop than this one"""
        e = self.entry(eid)
        eid = e["id"]
        if not e["enabled"]:
            raise ApiError(409, "off", f"{eid} is off: extension_set {{\"id\": \"{eid}\", \"enabled\": true}} first")
        m, problems = M.check_manifest(e["path"])
        caps = []
        if m is not None and m["id"] != eid:
            problems.append(M.problem("error", f"{M.MANIFEST}: id", f"the id is {m['id']} now, registered as {eid}",
                                      f"extension_remove {eid}, then extension_add the folder again"))
            m = None
        if m is not None:
            self.require(m)                            # requires_unmet: it cannot run here
            self.allow(e)                              # needs_approval before anything of it runs
            self.stop(eid, quiet=True)
            try:
                proc = self.ensure(eid, "extension_load", fresh=True).result(INIT_TIMEOUT + 10)
                e = self.entry(eid)
                caps = [c["id"] for c in (e.get("described") or {}).get("capabilities") or []]
                _, more = M.check_description(eid, dict(protocol=M.PROTOCOL, **(e.get("described") or {})))
                problems += [p for p in more if p["level"] == "warning"]
                del proc
            except Down as d:
                problems += d.problems
                lines = tail(self.output_path(eid))
                if lines:
                    problems.append(M.problem("error", "output", "the end of its output:\n" + "\n".join(lines),
                                              "a traceback says what went wrong"))
            except ApiError as a:
                problems.append(M.problem("error", "start", a.message, ""))
            problems += M.adds_problems(m, self.entry(eid).get("described"))
        check = dict(ok=False, problems=problems, capabilities=caps)
        if m is not None:
            tests = self.run_tests(m)
            if tests is not None:
                check["tests"] = tests
                if tests["failed"]:
                    problems.append(M.problem("error", "tests", f"{tests['failed']} of {tests['ran']} tests failed"
                                              if tests["ran"] else "the tests did not run",
                                              "tests.output says which and why"))
        check["ok"] = not M.errors_in(problems)
        return dict(row=self.get(eid), check=check)

    # --- output

    def logs(self, eid, since=0, limit=200):
        """the extension's output by line: {lines: [{n, text}], next, total, reset, cut, row}, as program logs were.
        since 0: from its first line, or its last `limit` lines when it has more (cut true); since n: from line n on;
        next: the since of the next read. A since past the end: the file was moved to output.1.log, its lines from the
        first (reset true)"""
        check_window(since, limit)
        with self.lock:
            e = self.entry(eid)
            path = self.output_path(e["id"])
            lines = self.lines.get(str(path))
            if lines is None:
                lines = self.lines[str(path)] = Lines(path)
            first, texts, total, reset = window(lines, since, limit)
            row = self.row(e)
        return dict(lines=[dict(n=first + i, text=t) for i, t in enumerate(texts)], next=first + len(texts), total=total,
                    reset=reset, cut=since == 0 and first > 1, row=row)
