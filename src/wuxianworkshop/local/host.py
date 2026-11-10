r"""The host of local extensions: the registered extensions and their processes, as the daemon uses them (Host: start,
stop, calls, the rows the API shows, the annotations and skill supplements, what an extension asks and tells). What it
is made of is beside it:

    registry.py   the registry, <state>\extensions.json: {revision, extensions: [{id, path, enabled, options, described}]}
    process.py    one process of an extension (Proc): its environment and command, its threads, the end of its tree
    rpc.py        the protocol on its pipe (JSON-RPC 2.0, UTF-8, one object per line): requests, answers, errors
    logs.py       its output, <state>\extensions\<id>\output.log (stderr, stdout that is no protocol, the host's lines)
    check.py      check: a process of its own started and initialized, the extension's tests

The registry's revision goes up by one with every change (a registration, a start or a stop, another description) and is
told to on_change(revision) and the subscribers (subscribe).

An extension starts on the first call of one of its capabilities (start "use"), or with the daemon (start "workshop",
autostart()); it runs until it is stopped, set off, removed or the daemon ends (close(): shutdown to each, its process
tree ended after SHUTDOWN_WAIT seconds). One that crashed starts again at its next call.

The extension tells event {source, data} (an item {source: <id>, kind: <id>.<source>, data, time} for on_event)
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
from ..paths import state_dir
from . import adds as A
from . import check as C
from . import manifest as M
from . import process as P
from .check import TESTS_OUTPUT, TESTS_TIMEOUT                                             # noqa: F401
from .logs import (LINE_MAX, LOG_MAX, LOGS_MOST, TAIL, Lines, Output, check_window, clock,  # noqa: F401
                   output_path, tail, window)
from .process import (CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, KILL_WAIT, SHUTDOWN_WAIT,  # noqa: F401
                      WINDOWS, Proc, default_python, kill_tree, sdk_dir)
from .registry import (REPLACE_WAITS, Registry, check_folder, check_options, option_rows, replace_retrying,  # noqa: F401
                       write_json)
from .rpc import BAD_ARGS, FAILED, INIT_TIMEOUT, NO_METHOD, REASONS, Down, RpcError, call_error  # noqa: F401


def duration(seconds):
    return f"{seconds:.1f} s" if seconds < 120 else f"{seconds / 60:.1f} min"


# --- the host --------------------------------------------------------------------------------------------------------

class Host:
    """the registered local extensions and their processes. root: the state folder (default paths.state_dir());
    note(text): a line for the daemon's log; on_event(id, item): an item of an extension's source; on_call(id, cap,
    args, chain): an extension's call of a capability (chain: the caller chain of the call it is serving, a list
    of strings), a Future of its result (ApiError for a refusal); on_change(revision):
    the extensions changed. python: the interpreter for run ["python", ...] without the manifest's own (default:
    default_python()); sdk: the folder put first on PYTHONPATH (default: sdk_dir())"""

    def __init__(self, root=None, *, version=__version__, note=None, on_event=None, on_call=None, on_change=None,
                 python=None, sdk=None, mode=None):
        self.root = Path(root) if root is not None else state_dir()
        self.version = version
        self.note = note or (lambda text: None)
        self.on_event, self.on_call, self.on_change = on_event, on_call, on_change
        self.python = python
        self.mode = mode or (lambda: "developer")   # mode(): the daemon's mode now, "player" or "developer"
        self.sdk = Path(sdk) if sdk is not None else sdk_dir()
        self.lock = threading.RLock()
        self.reg = Registry(self.root, self.lock, on_save=self.saved)   # extensions.json (registry.py), under self.lock
        self.procs = {}               # id -> its Proc (starting or running)
        self.starting = {}            # id -> the Future of its start in progress
        self.runs = {}                # id -> what its latest run left: state, pid, started, exit_code, error
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
        """(the registry's on_save) the file was written: the annotations are worked out again"""
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

    def state_of(self, e, manifest_error=None):
        """(state, pid, started, exit_code, error) of an entry now"""
        proc = self.procs.get(e["id"])
        run = self.runs.get(e["id"]) or {}
        if proc is not None and proc.running:
            return ("running" if proc.ready else "starting"), proc.pid, proc.started, None, None
        if e["id"] in self.starting:
            return "starting", None, None, None, None
        if not e["enabled"]:
            return "off", None, run.get("started"), run.get("exit_code"), None
        if manifest_error:
            return "failed", None, run.get("started"), run.get("exit_code"), manifest_error
        return (run.get("state") or "stopped"), None, run.get("started"), run.get("exit_code"), run.get("error")

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

    def row(self, e):
        """an extension as the API shows it"""
        m, problems = M.check_manifest(e["path"])
        error = None
        if m is None:
            first = M.errors_in(problems)[0]
            error = f"{first['where']}: {first['message']}" + (f" ({first['hint']})" if first.get("hint") else "")
        elif m["id"] != e["id"]:
            error = (f"its manifest's id is {m['id']} now, it was registered as {e['id']}: extension_remove "
                     f"{e['id']}, then extension_add the folder again")
            m = None
        state, pid, started, exit_code, err = self.state_of(e, error)
        desc = e.get("described") or {}
        return dict(id=e["id"], title=(m or {}).get("title") or e["id"], version=(m or {}).get("version"),
                    path=e["path"], enabled=e["enabled"], start=(m or {}).get("start") or "use", state=state, pid=pid,
                    started=started, exit_code=exit_code, error=err,
                    capabilities=[c["id"] for c in desc.get("capabilities") or []],
                    sources=[s["name"] for s in desc.get("sources") or []], options=self.option_rows(e),
                    apps=[a["app"] for a in (m or {}).get("apps") or []],
                    skills=self.skill_names((m or {}).get("skills")),
                    annotate=[dict(a) for a in (m or {}).get("annotate") or []],
                    extends=sorted((m or {}).get("extends") or {}), adds=A.row(m),
                    approval=self.reg.approval(e))

    def rows(self):
        with self.lock:
            return [self.row(e) for e in self.load()["extensions"]]

    def get(self, eid):
        with self.lock:
            return self.row(self.entry(eid))

    def listing(self):
        """{revision, extensions: [row]}"""
        with self.lock:
            data = self.load()
            return dict(revision=int(data.get("revision") or 0), extensions=[self.row(e) for e in data["extensions"]])

    def manifest_entries(self):
        """the enabled extensions as WuxianKit's manifest lists an extension (local_manifest): {id, title, version,
        notes, state "on", runtime "local", path, docs, skills, capabilities: [{id, kind, title, doc, args, timeout}],
        sources: [{name, title, doc}], options: [opt + value], apps: [declaration], annotate: [{app, call, wake,
        timeout}], extends: {skill: absolute path}}. One whose manifest cannot be read now keeps what it described"""
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
                            annotate=[dict(a) for a in (m or {}).get("annotate") or []],
                            extends=dict((m or {}).get("extends") or {}), adds=A.row(m)))
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

    def annotations(self, app):           # the fourth batch's; goes with the manifest's annotate
        return A.annotations(self, app)

    def extensions_of(self, skill):       # the fourth batch's; goes with the manifest's extends
        return A.extensions_of(self, skill)

    def approve(self, eid, by="player"):
        """the player (or developer mode) allows this extension's command to run (registry.approve); its row"""
        with self.lock:
            self.reg.approve(eid, by)
        return self.get(eid)

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
        folder is registered already). ApiError bad_manifest (with problems), extension_exists (another folder has its
        id). Started at once when its manifest says start "workshop" """
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, "bad_request", "path: the full path of an extension's folder (or its wuxian-extension.json)")
        p = Path(path.strip())
        if not p.is_absolute():
            raise ApiError(400, "bad_request", f"path: a full path ({path} is not)")
        check_folder(p)
        folder, _ = M.locate(p.resolve())
        m, problems = M.check_manifest(folder)
        if m is None:
            raise ApiError(400, "bad_manifest", str(M.ManifestError(problems)), problems=problems)
        key = os.path.normcase(str(folder))
        with self.lock:
            data = self.load()
            same = next((e for e in data["extensions"] if os.path.normcase(e["path"]) == key), None)
            if same is not None:
                return self.row(same)
            other = next((e for e in data["extensions"] if e["id"] == m["id"]), None)
            if other is not None:
                raise ApiError(409, "extension_exists", f"the id {m['id']} is taken by {other['path']}: give this one "
                                                        f"another id, or extension_remove that one first")
            e = dict(id=m["id"], path=str(folder), enabled=True, options={}, described=None)
            data["extensions"].append(e)
            self.save(data)
        self.note(f"local extension {m['id']}: registered ({folder})")
        self.changed()
        if m["start"] == "workshop":
            self.ensure(m["id"], "start workshop")
        return self.get(m["id"])

    def remove(self, eid):
        """takes an extension off the registry (stopped first; its folder stays)"""
        e = self.entry(eid)
        self.stop(e["id"], quiet=True)
        with self.lock:
            data = self.load()
            data["extensions"] = [x for x in data["extensions"] if x["id"] != e["id"]]
            self.save(data)
            self.runs.pop(e["id"], None)
            out = self.outputs.pop(e["id"], None)
        if out is not None:
            out.close()
        self.note(f"local extension {e['id']}: no longer registered ({e['path']})")
        self.changed()
        return dict(removed=True, id=e["id"], path=e["path"])

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
        return P.environment(m, self.sdk)

    def interpreter(self, m):
        """the Python of an extension (the manifest's, else the workshop's), or None"""
        return P.interpreter(m, self.python)

    def command(self, m, env):
        """the argv it starts with: its first item a full path; ApiError bad_command when it is not there"""
        return P.command(m, env, self.python)

    def spawn(self, m, output, on_message=None, on_end=None):
        """the extension's process, its pipe open (ApiError bad_command / start_failed)"""
        env = self.environment(m)
        argv = self.command(m, env)
        return P.spawn(m, env, argv, output, on_message or self.on_message, on_end or self.ended)

    def initialize(self, proc, m, options, timeout=None):
        """initialize: (description, problems); Down when it did not answer one that can be used"""
        timeout = INIT_TIMEOUT if timeout is None else timeout
        fut = proc.request("initialize", dict(protocol=M.PROTOCOL, workshop=self.version, id=m["id"], options=options))
        try:
            answer = fut.result(timeout)
        except TimeoutError:
            raise self.down(proc, f"{m['id']} did not answer initialize within {timeout:.0f} s") from None
        except RpcError as e:
            raise self.down(proc, f"{m['id']} answered initialize with an error: {e.message}") from None
        except ApiError:
            proc.ended.wait(2.0)
            raise self.down(proc, f"{m['id']} exited before it answered initialize (code {proc.exit_code})") from None
        return C.check_description(m["id"], answer)

    def down(self, proc, message):
        lines = tail(proc.output.path)
        text = message + ("\nthe end of its output (extension_logs):\n" + "\n".join(lines) if lines else "")
        return Down(text, [M.problem("error", "initialize", message, "extension_logs shows its output: a traceback "
                                                                     "says what went wrong")])

    def ensure(self, eid, why=None):
        """a Future of the extension's Proc once it runs: at once when it runs, the start in progress, or a new start
        (Down when it cannot start)"""
        with self.lock:
            fut = Future()
            if self.closing:
                fut.set_exception(ApiError(503, "stopping", "the daemon is shutting down"))
                return fut
            proc = self.procs.get(eid)
            if proc is not None and proc.ready and proc.running:
                fut.set_result(proc)
                return fut
            if eid in self.starting:
                return self.starting[eid]
            e = self.entry(eid)
            if not e["enabled"]:
                fut.set_exception(ApiError(409, "off", f"{eid} is off: extension_set {{\"id\": \"{eid}\", "
                                                       f"\"enabled\": true}} turns it on"))
                return fut
            self.starting[eid] = fut
        threading.Thread(target=self.start, args=(eid, fut, why), name=f"wuxian-ext-{eid}-start", daemon=True).start()
        return fut

    def start(self, eid, fut, why):
        """(its thread) starts an extension and initializes it; fut gets its Proc or the error"""
        proc = None
        try:
            e = self.entry(eid)
            m, problems = M.check_manifest(e["path"])
            if m is None:
                raise Down(str(M.ManifestError(problems)), problems)
            if m["id"] != eid:
                raise Down(f"its manifest's id is {m['id']} now: extension_remove {eid}, then extension_add the folder")
            out = self.output(eid)
            with self.lock:
                if self.closing:
                    raise ApiError(503, "stopping", "the daemon is shutting down")
                proc = self.spawn(m, out)
                self.procs[eid] = proc
                self.runs[eid] = dict(state="starting", started=proc.started)
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
                if proc.stopping or not proc.running:
                    raise ApiError(409, "stopped", f"{eid} was stopped while it started")
                proc.ready = True
                self.runs[eid] = dict(state="running", started=proc.started)
            out.write(f"-- {clock()} ready: {len(desc['capabilities'])} capabilities, {len(desc['sources'])} sources, "
                      f"{len(desc['options'])} options")
            self.note(f"local extension {eid}: running (pid {proc.pid}, ready in {duration(time.time() - proc.started)})")
        except Exception as ex:
            error = ex if isinstance(ex, ApiError) else ApiError(500, "internal", f"{eid}: {ex!r}")
            with self.lock:
                self.starting.pop(eid, None)
                stopped = proc is not None and proc.stopping
                if not stopped:
                    self.runs[eid] = dict(state="failed", error=error.message.splitlines()[0][:500],
                                          started=proc.started if proc else None)
            if proc is not None:
                if not stopped:
                    self.output(eid).write(f"-- {clock()} failed: {error.message.splitlines()[0]}")
                proc.stopping = True
                proc.kill()
            self.note(f"local extension {eid}: could not start: {error.message.splitlines()[0][:300]}")
            self.changed()
            fut.set_exception(error)
            return
        with self.lock:
            self.starting.pop(eid, None)
        self.changed()
        fut.set_result(proc)

    def ended(self, proc):
        """(its waiter thread) a process ended: by a stop, or by itself"""
        took = duration(time.time() - proc.started)
        with self.lock:
            current = self.procs.get(proc.id) is proc
            if current:
                del self.procs[proc.id]
                if proc.ready:
                    if proc.stopping:
                        self.runs[proc.id] = dict(state="stopped", started=proc.started, exit_code=proc.exit_code)
                    else:
                        self.runs[proc.id] = dict(state="exited", started=proc.started, exit_code=proc.exit_code,
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
        """stops a running extension (shutdown, then its tree): its row"""
        e = self.entry(eid)
        with self.lock:
            proc = self.procs.get(e["id"])
        if proc is not None:
            proc.shutdown()
            if not proc.ready:                         # stopped while it started: its start says so
                with self.lock:
                    self.runs[e["id"]] = dict(state="stopped", started=proc.started, exit_code=proc.exit_code)
                self.changed()
            if not quiet:
                self.note(f"local extension {e['id']}: stopped")
        return self.get(e["id"])

    def close(self):
        """the daemon stops: every extension gets shutdown, and those that have not exited within SHUTDOWN_WAIT
        seconds have their trees ended (all at once)"""
        with self.lock:
            self.closing = True
            procs = list(self.procs.values())
        threads = [threading.Thread(target=p.shutdown, daemon=True) for p in procs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(SHUTDOWN_WAIT + 2 * KILL_WAIT + 1)
        with self.lock:
            outputs, self.outputs = list(self.outputs.values()), {}
        for out in outputs:
            out.close()

    def autostart(self):
        """(the daemon's start) the enabled extensions whose manifest says start "workshop" start"""
        for e in self.load()["extensions"]:
            if not e["enabled"]:
                continue
            m, _ = M.check_manifest(e["path"])
            if m is not None and m["id"] == e["id"] and m["start"] == "workshop":
                self.ensure(e["id"], "start workshop")

    # --- the protocol: what an extension asks and tells

    def on_message(self, proc, msg):
        method, params = msg.get("method"), msg.get("params")
        params = params if isinstance(params, dict) else {}
        if "id" in msg:                                       # a request
            if method != "call":
                proc.respond(msg["id"], error=dict(code=NO_METHOD, message=f"no method {method!r}: the workshop "
                                                                           "answers call {cap, args}"))
                return
            self.extension_call(proc, msg["id"], params)
            return
        if method == "event":
            self.extension_event(proc, params)
        else:
            proc.output.write(f"-- {clock()} a notification the workshop does not know: {method!r}")

    def extension_event(self, proc, params):
        name, data = params.get("source"), params.get("data")
        e = next((x for x in self.load()["extensions"] if x["id"] == proc.id), None)
        sources = [s["name"] for s in ((e or {}).get("described") or {}).get("sources") or []]
        if not isinstance(name, str) or (proc.ready and name not in sources):
            proc.output.write(f"-- {clock()} event from source {name!r} dropped: declare it (it declares: "
                              f"{', '.join(sources) or 'none'})")
            return
        item = dict(source=proc.id, kind=f"{proc.id}.{name}", data=data, time=round(time.time(), 3))
        if self.on_event is not None:
            self.on_event(proc.id, item)

    def extension_call(self, proc, rid, params):
        cap, args = params.get("cap"), params.get("args")
        if not isinstance(cap, str) or not cap:
            proc.respond(rid, error=dict(code=BAD_ARGS, message="cap: a capability id, such as sense.bags or voice.say",
                                         data=dict(reason="bad_args")))
            return
        if args is not None and not isinstance(args, dict):
            proc.respond(rid, error=dict(code=BAD_ARGS, message="args: an object", data=dict(reason="bad_args")))
            return
        if self.on_call is None:
            proc.respond(rid, error=dict(code=FAILED, message="calls are not available here", data=dict(reason="failed")))
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
                proc.respond(rid, result=f.result())
            except ApiError as e:
                code = {"no_capability": NO_METHOD, "bad_args": BAD_ARGS}.get(e.code, FAILED)
                reason = e.code if e.code in REASONS else "failed"
                proc.respond(rid, error=dict(code=code, message=e.message, data=dict(reason=reason)))
            except Exception as e:
                proc.respond(rid, error=dict(code=FAILED, message=f"{type(e).__name__}: {e}",
                                             data=dict(reason="failed")))
        fut.add_done_callback(answered)

    # --- calls

    def call(self, proc, name, args, caller, deadline_ms=None):
        """a call of a capability of a running Proc: a Future of its result (RpcError / ApiError). deadline_ms: how
        long the caller still waits, told to the extension (To do: W-A)"""
        return proc.request("call", dict(name=name, args=args or {}, caller=caller or {"by": "agent"}))

    def cancel_call(self, proc, fut, ping=False):
        """the caller of call()'s Future waits no longer: the extension is told cancel {id}, its answer is dropped
        and the Future ends (reason cancelled) unless it is done. ping: the call timed out, so the extension is asked
        ping; one that does not answer within 5 s is hung (killed and started again at its next call). (To do: W-A)"""
        fut.cancel()

    def call_sync(self, cap_id, args=None, caller=None, timeout=30.0):
        """(tests, scripts) a capability's result, waiting here; ApiError as local_call raises it"""
        found = self.capability(cap_id)
        if found is None:
            raise ApiError(404, "no_capability", f"no local extension has {cap_id}")
        proc = self.ensure(found[0]["id"]).result(INIT_TIMEOUT + 10)
        fut = self.call(proc, cap_id.partition(".")[2], args, caller)
        try:
            return fut.result(timeout)
        except TimeoutError:
            fut.cancel()
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
        None when it has no tests folder or is no Python extension"""
        return C.run_tests(self, m)

    def load_(self, eid):
        """extension_load: checked, then (re)started: {row, check}. A manifest that cannot be used leaves a running
        one as it is"""
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
            self.stop(eid, quiet=True)
            try:
                proc = self.ensure(eid, "extension_load").result(INIT_TIMEOUT + 10)
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
            problems += M.annotate_problems(m, self.entry(eid).get("described"))
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
