r"""The host of local extensions: the registry, the processes and the protocol (one stdio pipe, JSON-RPC 2.0, UTF-8, one
object per line; stderr is the extension's log).

The registry is <state>\extensions.json, {revision, extensions: [{id, path, enabled, options: {name: value}, described}]}:
described is the extension's last answer to initialize (its capabilities, sources and options), so that an extension
that does not run still lists them (the agent's tool list does not change because it stopped). revision goes up by one
with every change (a registration, a start or a stop, another description) and is told to on_change(revision) and the
subscribers (subscribe).

An extension starts on the first call of one of its capabilities (start "use"), or with the daemon (start "workshop",
autostart()); it runs until it is stopped, set off, removed or the daemon ends (close(): shutdown to each, its process
tree ended after SHUTDOWN_WAIT seconds). One that crashed starts again at its next call. Its process gets the daemon's
environment, PYTHONUNBUFFERED=1, PYTHONIOENCODING=utf-8, the manifest's env, WUXIAN_EXTENSION=<id>, WUXIAN_HOME and
PYTHONPATH=<the workshop's SDK folder> before what was there; its cwd is its folder. Windows: CREATE_NO_WINDOW |
CREATE_NEW_PROCESS_GROUP, and taskkill /T /F ends a process tree.

The workshop asks (requests): initialize {protocol, workshop, id, options} (INIT_TIMEOUT seconds: a model may load),
call {name, args, caller}, shutdown {}; it tells (notifications): options {options} when the player changed them (all of
them). The extension tells event {source, data} (an item {source: <id>, kind: <id>.<source>, data, time} for on_event)
and asks call {cap, args} (on_call: any capability, the game's or this computer's; the daemon allows see, keep and point).

Its output, <state>\extensions\<id>\output.log (output.1.log before it, each at most LOG_MAX bytes), holds its stderr,
whatever it wrote on stdout that is no protocol, and the host's lines on its starts and ends (they begin with "--").
Any thread may call the host; calls answer with concurrent.futures.Future. Standard library only.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from array import array
from concurrent.futures import Future
from pathlib import Path

from .. import __version__
from ..daemon.api import ApiError
from ..paths import home, state_dir
from . import manifest as M

INIT_TIMEOUT = 180.0          # seconds initialize may take (an extension that loads a model first)
SHUTDOWN_WAIT = 5.0           # seconds an extension has to exit after shutdown before its process tree is ended
KILL_WAIT = 5.0               # seconds an ended process tree has to be gone
LOG_MAX = 2 * 1024 * 1024     # bytes of output.log before it becomes output.1.log
LINE_MAX = 4000               # characters of an output line in an answer
LOGS_MOST = 2000              # lines one answer of logs() gives at most
TAIL = 12                     # lines of the output a failed start quotes
TESTS_TIMEOUT = 120.0         # seconds the extension's tests may take (check)
TESTS_OUTPUT = 4000           # characters of the tests' output kept
REPLACE_WAITS = (0.02, 0.05, 0.1, 0.2, 0.5)

# JSON-RPC error codes of a call; an error answered to an extension's own call also has data {reason}, one of REASONS
BAD_ARGS, NO_METHOD, FAILED = -32602, -32601, 1
REASONS = ("no_capability", "bad_args", "failed", "timeout", "down", "not_allowed")

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS = sys.platform == "win32"


# --- small things ----------------------------------------------------------------------------------------------------

def kill_tree(pid):
    """end process pid and every process it started (taskkill /T /F); True when taskkill said it did"""
    if not WINDOWS:
        try:
            os.killpg(pid, 9)
            return True
        except OSError:
            return False
    taskkill = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "taskkill.exe"
    try:
        done = subprocess.run([str(taskkill), "/T", "/F", "/PID", str(pid)], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                              timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


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


def sdk_dir():
    """the folder of the Python SDK (wuxiankit.py) that extensions import: sdk/python in a checkout, _internal/sdk
    (sys._MEIPASS) in a frozen build"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "sdk"
    return Path(__file__).resolve().parents[3] / "sdk" / "python"


def default_python():
    """the workshop's own Python for a manifest whose run starts with "python" and that gives no python: this
    interpreter, or (a frozen build, which has none of its own) the first python on PATH"""
    if not getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("python") or shutil.which("py")


def duration(seconds):
    return f"{seconds:.1f} s" if seconds < 120 else f"{seconds / 60:.1f} min"


def clock():
    return time.strftime("%H:%M:%S")


class RpcError(Exception):
    """an error answer of the extension: {code, message, data?}"""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class Down(ApiError):
    """the extension could not be started (or initialized): why, with the end of its output; problems as check gives
    them"""

    def __init__(self, message, problems=None):
        super().__init__(503, "down", message)
        self.problems = problems or [M.problem("error", "start", message)]


def call_error(e, cap):
    """the ApiError of a call that failed in the extension"""
    if isinstance(e, RpcError):
        if e.code == BAD_ARGS:
            return ApiError(400, "bad_args", f"{cap}: {e.message}")
        if e.code == NO_METHOD:
            return ApiError(404, "no_capability", f"{cap}: {e.message}")
        return ApiError(422, "failed", f"{cap}: {e.message}")
    if isinstance(e, ApiError):
        return e
    return ApiError(422, "failed", f"{cap}: {e}")


# --- output ----------------------------------------------------------------------------------------------------------

class Output:
    """an extension's output.log, written by the host (its stderr lines, the host's own), moved to output.1.log once it
    holds LOG_MAX bytes"""

    def __init__(self, path):
        self.path, self.lock, self.file = Path(path), threading.Lock(), None

    def write(self, text):
        data = (str(text).rstrip("\r\n") + "\n").encode("utf-8", "replace")
        with self.lock:
            try:
                if self.file is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.file = open(self.path, "ab")
                if self.file.tell() and self.file.tell() + len(data) > LOG_MAX:
                    self.file.close()
                    self.file = None
                    replace_retrying(self.path, self.path.with_name("output.1.log"))
                    self.file = open(self.path, "ab")
                self.file.write(data)
                self.file.flush()
            except OSError:
                if self.file is not None:
                    try:
                        self.file.close()
                    except OSError:
                        pass
                self.file = None

    def close(self):
        with self.lock:
            if self.file is not None:
                try:
                    self.file.close()
                except OSError:
                    pass
                self.file = None


class Lines:
    """the lines of an output file, indexed as it grows: starts[i] is where line i + 1 begins, and the last item where
    the line still being written begins. Another file under the name (it was moved to output.1.log) is indexed from
    its start"""

    def __init__(self, path):
        self.path = Path(path)
        self.reset(None)

    def reset(self, ident):
        self.ident, self.starts, self.size = ident, array("q", [0]), 0

    def update(self):
        try:
            st = os.stat(self.path)
        except OSError:
            self.reset(None)
            return 0
        ident = (st.st_dev, st.st_ino)
        if ident != self.ident or st.st_size < self.size:
            self.reset(ident)
        if st.st_size > self.size:
            try:
                with open(self.path, "rb") as f:
                    f.seek(self.size)
                    pos = self.size
                    while pos < st.st_size:
                        block = f.read(min(1 << 20, st.st_size - pos))
                        if not block:
                            break
                        i = block.find(b"\n")
                        while i >= 0:
                            self.starts.append(pos + i + 1)
                            i = block.find(b"\n", i + 1)
                        pos += len(block)
                    self.size = pos
            except OSError:
                pass
        return self.size

    @property
    def complete(self):
        return len(self.starts) - 1

    def read(self, first, last):
        """the texts of lines first..last (1-based)"""
        begin = self.starts[first - 1]
        end = self.starts[last] if last < len(self.starts) else self.size
        try:
            with open(self.path, "rb") as f:
                f.seek(begin)
                data = f.read(end - begin)
        except OSError:
            return []
        parts = data.split(b"\n")
        if data.endswith(b"\n"):
            parts.pop()
        out = []
        for raw in parts:
            text = raw.decode("utf-8", "replace").rstrip("\r")
            out.append(text if len(text) <= LINE_MAX else text[:LINE_MAX] + "…")
        return out


def tail(path, n=TAIL):
    """the last n lines of a file"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            data = f.read()
    except OSError:
        return []
    return [line.decode("utf-8", "replace").rstrip("\r")[:LINE_MAX] for line in data.split(b"\n") if line.strip()][-n:]


# --- one run of an extension -----------------------------------------------------------------------------------------

class Proc:
    """one process of an extension: its pipe, the requests waiting for their answers, how it ended. on_message(proc,
    msg): a request or a notification of the extension (its reader thread); on_end(proc): it ended (its waiter thread)"""

    def __init__(self, eid, popen, output, on_message, on_end, argv):
        self.id, self.popen, self.output, self.argv = eid, popen, output, argv
        self.on_message, self.on_end = on_message, on_end
        self.pid, self.started = popen.pid, time.time()
        self.lock, self.wlock = threading.Lock(), threading.Lock()
        self.pending, self.next_id = {}, 0
        self.ready = False                          # it answered initialize
        self.stopping = False                       # the host stops it (not a crash)
        self.exit_code, self.ended = None, threading.Event()
        self.finished = threading.Event()           # ended, and the host has taken note (on_end returned)
        self.readers = [threading.Thread(target=self.read_out, name=f"wuxian-ext-{eid}-out", daemon=True),
                        threading.Thread(target=self.read_err, name=f"wuxian-ext-{eid}-err", daemon=True)]
        for t in self.readers:
            t.start()
        threading.Thread(target=self.wait, name=f"wuxian-ext-{eid}-wait", daemon=True).start()

    @property
    def running(self):
        return not self.ended.is_set()

    def send(self, msg):
        data = (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self.wlock:
            self.popen.stdin.write(data)
            self.popen.stdin.flush()

    def request(self, method, params):
        """a request: a Future of its result (RpcError for an error answer, ApiError when the process ended)"""
        fut = Future()
        with self.lock:
            if self.ended.is_set():
                fut.set_exception(self.gone_error())
                return fut
            self.next_id += 1
            rid = self.next_id
            self.pending[rid] = fut
        fut.add_done_callback(lambda f: f.cancelled() and self.forget(rid))
        try:
            self.send(dict(jsonrpc="2.0", id=rid, method=method, params=params))
        except (OSError, ValueError) as e:
            self.forget(rid)
            if not fut.done():
                fut.set_exception(ApiError(503, "down", f"{self.id}: its pipe is closed ({e})"))
        return fut

    def forget(self, rid):
        with self.lock:
            self.pending.pop(rid, None)

    def notify(self, method, params):
        try:
            self.send(dict(jsonrpc="2.0", method=method, params=params))
            return True
        except (OSError, ValueError):
            return False

    def respond(self, rid, result=None, error=None):
        msg = dict(jsonrpc="2.0", id=rid)
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        try:
            self.send(msg)
        except (OSError, ValueError):
            pass

    def gone_error(self):
        return ApiError(503, "down", f"{self.id} exited (code {self.exit_code})")

    def read_out(self):
        for raw in iter(self.popen.stdout.readline, b""):
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                msg = None
            if not isinstance(msg, dict) or (msg.get("jsonrpc") != "2.0" and "method" not in msg and "id" not in msg):
                self.output.write(f"stdout: {text[:LINE_MAX]}")       # not the protocol: print() to stdout, say
                continue
            if "method" in msg:
                try:
                    self.on_message(self, msg)
                except Exception as e:                                # a bug of the host: said, the pipe goes on
                    self.output.write(f"-- {clock()} the workshop could not handle {msg.get('method')!r}: {e!r}")
                continue
            with self.lock:
                fut = self.pending.pop(msg.get("id"), None)
            if fut is None or fut.done():
                continue
            err = msg.get("error")
            if err is not None:
                err = err if isinstance(err, dict) else {"message": str(err)}
                fut.set_exception(RpcError(err.get("code") if isinstance(err.get("code"), int) else FAILED,
                                           str(err.get("message") or "failed"), err.get("data")))
            else:
                fut.set_result(msg.get("result"))

    def read_err(self):
        for raw in iter(self.popen.stderr.readline, b""):
            self.output.write(raw.decode("utf-8", "replace").rstrip("\r\n"))

    def wait(self):
        code = self.popen.wait()
        for t in self.readers:                     # what it wrote last, before it is called ended
            t.join(2.0)
        with self.lock:
            self.exit_code = code
            self.ended.set()
            pending, self.pending = list(self.pending.values()), {}
        for fut in pending:
            if not fut.done():
                fut.set_exception(self.gone_error())
        for stream in (self.popen.stdin, self.popen.stdout, self.popen.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        try:
            self.on_end(self)
        finally:
            self.finished.set()

    def kill(self):
        """ends its process tree; True once it has ended (and the host has taken note)"""
        if not self.ended.is_set():
            kill_tree(self.pid)
            if not self.ended.wait(KILL_WAIT):
                try:
                    self.popen.kill()
                except OSError:
                    pass
        return self.finished.wait(KILL_WAIT)

    def shutdown(self, wait=SHUTDOWN_WAIT):
        """shutdown, then its tree ended when it has not exited within wait seconds; True once it has ended"""
        self.stopping = True
        if not self.ended.is_set() and self.ready:
            self.request("shutdown", {})
            self.ended.wait(wait)
        return self.kill()


# --- the host --------------------------------------------------------------------------------------------------------

class Host:
    """the registered local extensions and their processes. root: the state folder (default paths.state_dir());
    note(text): a line for the daemon's log; on_event(id, item): an item of an extension's source; on_call(id, cap,
    args): an extension's call of a capability, a Future of its result (ApiError for a refusal); on_change(revision):
    the extensions changed. python: the interpreter for run ["python", ...] without the manifest's own (default:
    default_python()); sdk: the folder put first on PYTHONPATH (default: sdk_dir())"""

    def __init__(self, root=None, *, version=__version__, note=None, on_event=None, on_call=None, on_change=None,
                 python=None, sdk=None):
        self.root = Path(root) if root is not None else state_dir()
        self.version = version
        self.note = note or (lambda text: None)
        self.on_event, self.on_call, self.on_change = on_event, on_call, on_change
        self.python = python
        self.sdk = Path(sdk) if sdk is not None else sdk_dir()
        self.lock = threading.RLock()
        self.procs = {}               # id -> its Proc (starting or running)
        self.starting = {}            # id -> the Future of its start in progress
        self.runs = {}                # id -> what its latest run left: state, pid, started, exit_code, error
        self.outputs = {}             # id -> Output
        self.lines = {}               # output path -> Lines
        self.listeners = {}
        self.closing = False
        self.annotate_cache = None    # (stamps, {app: [annotation]}): annotations() without reading every manifest

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

    @property
    def registry(self):
        return self.root / "extensions.json"

    def load(self):
        try:
            data = json.loads(self.registry.read_text(encoding="utf-8"))
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
        write_json(self.registry, data)
        self.annotate_cache = None

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

    # --- rows

    def output(self, eid):
        with self.lock:
            out = self.outputs.get(eid)
            if out is None:
                out = self.outputs[eid] = Output(self.output_path(eid))
            return out

    def output_path(self, eid):
        return self.root / "extensions" / eid / "output.log"

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
        desc = e.get("described") or {}
        return [dict(o, value=e["options"].get(o["name"], o.get("default"))) for o in desc.get("options") or []]

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
                    extends=sorted((m or {}).get("extends") or {}))

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
                            extends=dict((m or {}).get("extends") or {})))
        return dict(revision=int(data.get("revision") or 0), extensions=out)

    def declarations(self):
        """[(extension id, what kit.app.declare gets)] of the enabled extensions' apps; each says the extension it comes
        with (local {id, title}: its card in the game has no remove button)"""
        out = []
        for e in self.load()["extensions"]:
            if not e["enabled"]:
                continue
            m, _ = M.check_manifest(e["path"])
            if m is not None and m["id"] == e["id"]:
                here = {"id": e["id"], "title": m.get("title") or e["id"]}
                out += [(e["id"], dict(a, local=dict(here))) for a in m["apps"]]
        return out

    def enabled_manifests(self):
        """[(entry, manifest)] of the enabled extensions whose manifest passes its check (and still has their id)"""
        out = []
        for e in self.load()["extensions"]:
            if e["enabled"]:
                m, _ = M.check_manifest(e["path"])
                if m is not None and m["id"] == e["id"]:
                    out.append((e, m))
        return out

    def annotations(self, app):
        """[{"ext": id, "call": "facts.lookup", "wake": "wake", "timeout": 3.0}] of the enabled extensions that annotate
        app, by extension id; once an extension described itself, only a call that is a see capability it has. Called
        for every wake of every app (the daemon's frame thread), so it is a table kept until the registry or an enabled
        extension's manifest changes: a few stats, no reads, while nothing did"""
        if not isinstance(app, str) or not app:
            return []
        return [dict(a) for a in self.annotation_table().get(app, [])]

    @staticmethod
    def stamp(path):
        """(mtime_ns, size) of a file, or None when it is not there"""
        try:
            st = os.stat(path)
        except (OSError, ValueError):
            return None
        return st.st_mtime_ns, st.st_size

    def annotation_table(self):
        """{app: [annotation]} of the enabled extensions, kept while the stamps of the registry and of the enabled
        extensions' manifests stay (save() drops it at once)"""
        with self.lock:
            cache = self.annotate_cache
            if cache is not None and all(self.stamp(path) == was for path, was in cache[0]):
                return cache[1]
            stamps = [(self.registry, self.stamp(self.registry))]
            stamps += [(path, self.stamp(path)) for path in (M.locate(e["path"])[1] for e in self.load()["extensions"]
                                                            if e["enabled"])]
            table = {}
            for e, m in self.enabled_manifests():
                for a in m["annotate"]:
                    if M.annotation_ok(a, e.get("described")):
                        table.setdefault(a["app"], []).append(dict(ext=e["id"], call=a["call"], wake=a["wake"],
                                                                   timeout=a["timeout"]))
            for items in table.values():
                items.sort(key=lambda a: a["ext"])
            self.annotate_cache = (stamps, table)
            return table

    def extensions_of(self, skill):
        """[{"ext": id, "title": title, "path": <absolute path of the Markdown>}] of the enabled extensions that extend
        skill (a skill file's name), by extension id"""
        if not isinstance(skill, str) or not skill:
            return []
        out = [dict(ext=e["id"], title=m.get("title") or e["id"], path=m["extends"][skill])
               for e, m in self.enabled_manifests() if skill in m["extends"]]
        return sorted(out, key=lambda x: x["ext"])

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

    def kind_of(self, cap_id):
        """see / keep / point of a capability of a local extension (as it last described itself), else None"""
        found = self.capability(cap_id)
        return found[1]["kind"] if found and found[1] else None

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
        e = self.entry(eid)
        return {o["name"]: o["value"] for o in self.option_rows(e)}

    # --- starting

    def environment(self, m):
        env = dict(os.environ)

        def put(name, value):
            if WINDOWS:                                   # names are not case-sensitive there: one of each
                for k in [k for k in env if k.upper() == name.upper()]:
                    del env[k]
            env[name] = value

        def get(name):
            return next((v for k, v in env.items() if k.upper() == name.upper()), None) if WINDOWS else env.get(name)

        put("PYTHONUNBUFFERED", "1")
        put("PYTHONIOENCODING", "utf-8")
        for name, value in m["env"].items():
            put(name, value)
        put("WUXIAN_EXTENSION", m["id"])
        put("WUXIAN_HOME", str(home()))
        before = get("PYTHONPATH")
        put("PYTHONPATH", str(self.sdk) + (os.pathsep + before if before else ""))
        return env

    def interpreter(self, m):
        """the Python of an extension (the manifest's, else the workshop's), or None"""
        return m["python"] or self.python or default_python()

    def command(self, m, env):
        """the argv it starts with: its first item a full path; ApiError bad_command when it is not there"""
        run = list(m["run"])
        exe = run[0].strip()
        folder = Path(m["folder"])
        if exe.lower() == "python":
            found = self.interpreter(m)
            if not found:
                raise ApiError(400, "bad_command", "no Python to run it: give the manifest a python (the path of a "
                                                   "python.exe, a virtual environment's will do)")
        elif "/" in exe or "\\" in exe:
            path = Path(exe) if Path(exe).is_absolute() else folder / exe
            found = shutil.which(str(path)) or (str(path) if path.is_file() else None)
            if not found:
                raise ApiError(400, "bad_command", f"{exe} not found (relative to {folder})")
        else:
            search = next((v for k, v in env.items() if k.upper() == "PATH"), None) if WINDOWS else env.get("PATH")
            found = shutil.which(exe, path=search or os.defpath)
            if not found:
                raise ApiError(400, "bad_command", f"{exe} not found on PATH: write its full path in run")
        return [os.path.abspath(found)] + run[1:]

    def spawn(self, m, output, on_message=None, on_end=None):
        """the extension's process, its pipe open (ApiError bad_command / start_failed)"""
        env = self.environment(m)
        argv = self.command(m, env)
        kw = dict(creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if WINDOWS else dict(start_new_session=True)
        try:
            popen = subprocess.Popen(argv, cwd=m["folder"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, **kw)
        except OSError as e:
            raise ApiError(400, "start_failed", f"{m['id']} could not start: {e}") from None
        output.write(f"-- {clock()} started (pid {popen.pid}): {subprocess.list2cmdline(argv)}")
        return Proc(m["id"], popen, output, on_message or self.on_message, on_end or self.ended, argv)

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
        desc, problems = M.check_description(m["id"], answer)
        if desc is None:
            first = M.errors_in(problems)[0]
            raise Down(f"{m['id']}: {first['where']}: {first['message']}"
                       + (f" ({first['hint']})" if first.get("hint") else ""), problems)
        return desc, problems

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
        try:
            fut = self.on_call(proc.id, cap, args)
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

    def call(self, proc, name, args, caller):
        """a call of a capability of a running Proc: a Future of its result (RpcError / ApiError)"""
        return proc.request("call", dict(name=name, args=args or {}, caller=caller or {"by": "agent"}))

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
        if not isinstance(ident, str) or not ident.strip():
            raise ApiError(400, "bad_request", "id: a local extension's id, or the full path of an extension folder")
        ident = ident.strip()
        for e in self.load()["extensions"]:
            if e["id"] == ident:
                return e, Path(e["path"])
        p = Path(ident)
        if not p.is_absolute():
            self.entry(ident)                                  # not_found
        folder, _ = M.locate(p)
        key = os.path.normcase(str(folder.resolve()))
        same = next((e for e in self.load()["extensions"] if os.path.normcase(e["path"]) == key), None)
        return same, folder.resolve()

    def check(self, ident):
        """an extension checked without touching its running process: {ok, problems, capabilities, tests?}. The
        manifest; a process of its own started, initialized (its description checked) and shut down; its tests (a
        tests folder) run with its Python"""
        e, folder = self.resolve(ident)
        m, problems = M.check_manifest(folder)
        caps, described = [], None
        if m is not None:
            other = next((x for x in self.load()["extensions"] if x["id"] == m["id"]
                          and os.path.normcase(x["path"]) != os.path.normcase(str(folder))), None)
            if other is not None:
                problems.append(M.problem("error", f"{M.MANIFEST}: id", f"the id {m['id']} is taken by {other['path']}",
                                          "give this one another id"))
            out = Output(self.root / "extensions" / m["id"] / "check.log")
            out.write(f"-- {clock()} check")
            proc = None
            try:
                proc = self.spawn(m, out, on_message=self.check_message, on_end=lambda p: None)
                desc, more = self.initialize(proc, m, dict((e or {}).get("options") or {}))
                problems += more
                caps, described = [c["id"] for c in desc["capabilities"]], desc
            except Down as d:
                problems += d.problems
                lines = tail(out.path)
                if lines:
                    problems.append(M.problem("error", "output", "the end of its output:\n" + "\n".join(lines),
                                              "a traceback says what went wrong"))
            except ApiError as a:
                problems.append(M.problem("error", f"{M.MANIFEST}: run", a.message,
                                          "run: the command that starts it, relative to the extension folder"))
            finally:
                if proc is not None:
                    proc.shutdown()
                out.close()
        if m is not None:
            problems += M.annotate_problems(m, described if described is not None else (e or {}).get("described"))
        result = dict(ok=False, problems=problems, capabilities=caps)
        if m is not None:
            tests = self.run_tests(m)
            if tests is not None:
                result["tests"] = tests
                if tests["failed"]:
                    problems.append(M.problem("error", "tests", f"{tests['failed']} of {tests['ran']} tests failed"
                                              if tests["ran"] else "the tests did not run",
                                              "tests.output says which and why"))
        result["ok"] = not M.errors_in(problems)
        return result

    def check_message(self, proc, msg):
        """what an extension asks or tells during a check: its events are dropped, its calls refused"""
        if "id" in msg:
            proc.respond(msg["id"], error=dict(code=FAILED, message="no calls during a check", data=dict(reason="failed")))

    def run_tests(self, m):
        """{ran, failed, output} of `python -m unittest discover -s tests` in the extension's folder, with its Python;
        None when it has no tests folder or is no Python extension"""
        folder = Path(m["folder"])
        if not (folder / "tests").is_dir():
            return None
        python = m["python"] if m["python"] else (self.interpreter(m) if m["run"][0].strip().lower() == "python" else None)
        if not python:
            return None
        kw = dict(creationflags=CREATE_NO_WINDOW) if WINDOWS else {}
        try:
            done = subprocess.run([python, "-m", "unittest", "discover", "-s", "tests"], cwd=str(folder),
                                  env=self.environment(m), stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=TESTS_TIMEOUT, **kw)
        except subprocess.TimeoutExpired as e:
            text = ((e.stderr or b"") + (e.stdout or b"")).decode("utf-8", "replace")
            return dict(ran=0, failed=1, output=f"the tests took more than {TESTS_TIMEOUT:.0f} s\n" + text[-TESTS_OUTPUT:])
        except OSError as e:
            return dict(ran=0, failed=1, output=f"the tests could not run: {e}")
        text = (done.stderr + done.stdout).decode("utf-8", "replace")
        ran = re.search(r"^Ran (\d+) tests?", text, re.M)
        counts = re.search(r"^FAILED \(([^)]*)\)", text, re.M)
        failed = sum(int(n) for k, n in re.findall(r"(failures|errors|unexpected successes)=(\d+)", counts[1])) \
            if counts else (0 if done.returncode == 0 else 1)
        return dict(ran=int(ran[1]) if ran else 0, failed=failed, output=text[-TESTS_OUTPUT:])

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
        if not isinstance(since, int) or isinstance(since, bool) or since < 0:
            raise ApiError(400, "bad_request", "since: 0 (the newest lines) or a line number (the `next` of the last read)")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= LOGS_MOST:
            raise ApiError(400, "bad_request", f"limit: 1..{LOGS_MOST}")
        with self.lock:
            e = self.entry(eid)
            path = self.output_path(e["id"])
            lines = self.lines.get(str(path))
            if lines is None:
                lines = self.lines[str(path)] = Lines(path)
            lines.update()
            total = lines.complete
            reset = False
            if since == 0:
                first = max(1, total - limit + 1)
            elif since > total + 1:
                first, reset = 1, True
            else:
                first = since
            last = min(total, first + limit - 1)
            texts = lines.read(first, last) if last >= first else []
            row = self.row(e)
        return dict(lines=[dict(n=first + i, text=t) for i, t in enumerate(texts)], next=first + len(texts), total=total,
                    reset=reset, cut=since == 0 and first > 1, row=row)
