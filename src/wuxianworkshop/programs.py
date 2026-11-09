r"""The program loader (程序): addons run in the game, programs outside it (a Python program with the SDK,
sdk/python/wuxiankit.py, or anything in any language that calls the daemon's HTTP interface). The workshop loads and
debugs both: a program is registered by its folder, started, its output and exit code read, stopped, started again.
Nothing restarts a program by itself (a development tool: a crash must show), nothing watches its files.

A program is a folder with a manifest, wuxian-program.json:

    {"id": "companion", "title": "无限陪玩", "run": ["python", "main.py"], "cwd": ".", "env": {"X": "1"},
     "app": {"hear": ["game:combat"], "me": true, "controls": ["stop"]}}

id: lowercase letters, digits and _, a letter first, at most 24 bytes (a WuxianKit app's id: with `app` it is also the
id of the app the program drives); title: at most 48 bytes (default: the id); run: the argv, its first item relative to
the folder when it holds a / or \, else looked up on PATH; cwd (relative to the folder) and env are optional; app: the
declaration of the WuxianKit app the program drives (kit.app.open's fields but app, title and driver), when it drives
one. The loader starts a program with WUXIAN_PROGRAM (its id), WUXIAN_PROGRAM_MANIFEST (the manifest's path),
WUXIAN_HOME (this daemon's run-time folder: the SDK finds daemon.json there), PYTHONUNBUFFERED=1 and
PYTHONIOENCODING=utf-8 (the manifest's env may give the last two other values) in its environment.

The registry is <state>/programs.json, {"programs": [{"path": "<folder>"}]}: the ids are read from the manifests each
time, and of two folders with the same id the later one is refused. A program runs once at a time; each start keeps
<state>/programs/<id>/run.json (pid, the process's creation time, the command; how it ended) and output.log (stdout
and stderr together, written by the process itself, so that it outlives the daemon; the start before is output.1.log).
A daemon that starts takes back the programs still running from their run.json (adopt): the creation time tells a
reused pid apart. A row's state is stopped (never ran, or the loader stopped it: then stopped is true, until the next
start), running, or exited (it ended by itself; its exit_code: anything but 0 is a failure; None when it ended while no
daemon watched). A run the loader stopped keeps the exit code of a program that exited by itself on its app's stop,
None for a process tree it ended.
Windows first: CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, ctypes (OpenProcess, GetProcessTimes) for the creation time
and the exit code of a process taken back, taskkill /T /F to end a process tree. Standard library only.
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
from pathlib import Path

from .daemon.api import ApiError
from .i18n import tr
from .paths import home, state_dir

MANIFEST = "wuxian-program.json"
ID = re.compile(r"^[a-z][a-z0-9_]*$")
ID_MAX, TITLE_MAX = 24, 48             # bytes: what WuxianKit takes for an app's id and title
NOT_IN_APP = ("app", "title", "driver")   # the loader gives these to WuxianKit itself
LINE_MAX = 4000                        # characters of an output line in an answer
LOGS_MOST = 2000                       # lines one answer gives at most
KILL_WAIT = 5.0                        # seconds a killed process tree has to be gone
REPLACE_WAITS = (0.02, 0.05, 0.1, 0.2, 0.5)   # a file a reader holds open just then is moved aside a moment later

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS = sys.platform == "win32"

if WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    SYNCHRONIZE, QUERY, TERMINATE = 0x00100000, 0x1000, 0x0001
    WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 0x102
    GENERIC_WRITE, SHARE_ALL, CREATE_ALWAYS, NORMAL = 0x40000000, 0x7, 2, 0x80   # read | write | delete sharing
    INVALID = ctypes.c_void_p(-1).value
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)    # an instance of our own: its argtypes are ours
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    _k32.GetProcessTimes.restype = wintypes.BOOL
    _k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _k32.GetExitCodeProcess.restype = wintypes.BOOL
    _k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _k32.TerminateProcess.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.HANDLE)
    _k32.CreateFileW.restype = wintypes.HANDLE


# --- processes (Windows: by handle, which also keeps a pid from being reused while it is open) ----------------------

def open_process(pid, created=None):
    """a handle on process pid; None when there is none, or when created (its creation time) says it is another one
    (the pid was reused). Not Windows: the pid itself while it runs"""
    if not WINDOWS:
        try:
            os.kill(pid, 0)
        except (OSError, TypeError):
            return None
        return pid
    h = _k32.OpenProcess(SYNCHRONIZE | QUERY | TERMINATE, False, pid) or _k32.OpenProcess(SYNCHRONIZE | QUERY, False, pid)
    if not h:
        return None
    if created is not None and creation_time(h) != created:
        close_process(h)
        return None
    return h


def creation_time(handle):
    """when the process was created (a FILETIME as an integer), None when it cannot be read"""
    if not WINDOWS:
        return None
    times = [wintypes.FILETIME() for _ in range(4)]
    if not _k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        return None
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def process_created(pid):
    """the creation time of process pid (None: not known)"""
    h = open_process(pid)
    if h is None:
        return None
    try:
        return creation_time(h)
    finally:
        close_process(h)


def has_ended(handle, wait_ms=0):
    """whether the process ended (waiting up to wait_ms for it)"""
    if not WINDOWS:
        deadline = time.monotonic() + wait_ms / 1000
        while True:
            try:
                os.kill(handle, 0)
            except OSError:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
    return _k32.WaitForSingleObject(handle, wait_ms) == WAIT_OBJECT_0


def exit_code_of(handle):
    """the exit code of an ended process (None: not known)"""
    if not WINDOWS:
        return None
    code = wintypes.DWORD()
    return code.value if _k32.GetExitCodeProcess(handle, ctypes.byref(code)) else None


def close_process(handle):
    if WINDOWS and handle:
        _k32.CloseHandle(handle)


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


def open_output(path):
    """a file descriptor for a run's stdout and stderr, the file made new. On Windows it shares delete too: the next
    start can move it aside while a process this run left behind still writes to it"""
    if not WINDOWS:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    h = _k32.CreateFileW(str(path), GENERIC_WRITE, SHARE_ALL, None, CREATE_ALWAYS, NORMAL, None)
    if not h or h == INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    return msvcrt.open_osfhandle(h, 0)


def replace_retrying(src, dst):
    """os.replace, tried again for a moment while another process holds one of the files open"""
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


# --- manifests -------------------------------------------------------------------------------------------------------

def bad_manifest(file, zh, en):
    return ApiError(400, "bad_manifest", f"{file}: " + tr(zh, en))


def check_id(value, what="id"):
    """value when it is an id a program (and a WuxianKit app) may have, else ApiError bad_request"""
    if not isinstance(value, str) or not ID.match(value) or len(value.encode("utf-8")) > ID_MAX:
        raise ApiError(400, "bad_request", tr(f"{what}：小写字母、数字和 _，字母开头，最多 {ID_MAX} 个字符（{value!r} 不行）",
                                              f"{what}: lowercase letters, digits and _, a letter first, at most "
                                              f"{ID_MAX} characters (not {value!r})"))
    return value


def check_title(value):
    """a title as WuxianKit takes it (at most 48 bytes), stripped; ValueError says why not"""
    if not isinstance(value, str) or not value.strip() or len(value.strip().encode("utf-8")) > TITLE_MAX:
        raise ValueError(tr(f"title：不空的文字，最多 {TITLE_MAX} 字节（约 16 个汉字）",
                            f"title: some text, at most {TITLE_MAX} bytes"))
    return value.strip()


def read_manifest(file):
    """the manifest as the loader uses it: {id, title, run, cwd, env, app}; ApiError no_manifest (none there) or
    bad_manifest (what is wrong with it)"""
    file = Path(file)
    try:
        raw = file.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ApiError(400, "no_manifest", tr(f"{file.parent} 里没有 {MANIFEST}", f"no {MANIFEST} in {file.parent}")) from None
    except (OSError, UnicodeDecodeError) as e:
        raise bad_manifest(file, f"读不了：{e}", f"cannot be read: {e}") from None
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise bad_manifest(file, f"不是合法的 JSON：{e}", f"not valid JSON: {e}") from None
    if not isinstance(d, dict):
        raise bad_manifest(file, "要是一个 JSON 对象", "must be a JSON object")
    pid = d.get("id")
    if not isinstance(pid, str) or not ID.match(pid) or len(pid.encode("utf-8")) > ID_MAX:
        raise bad_manifest(file, f"id：小写字母、数字和 _，字母开头，最多 {ID_MAX} 个字符（有 app 时也是应用的 id）",
                           f"id: lowercase letters, digits and _, a letter first, at most {ID_MAX} characters (with an "
                           f"app, the app's id too)")
    try:
        title = check_title(d.get("title") if d.get("title") is not None else pid)
    except ValueError as e:
        raise bad_manifest(file, str(e), str(e)) from None
    run = d.get("run")
    if not isinstance(run, list) or not run or not all(isinstance(a, str) for a in run) or not run[0].strip():
        raise bad_manifest(file, 'run：启动命令的参数列表，如 ["python", "main.py"]（第一项含 / 或 \\ 时相对这个文件夹，否则在 PATH 里找）',
                           'run: the command as a list of arguments, such as ["python", "main.py"] (the first one '
                           'relative to this folder when it holds a / or \\, else found on PATH)')
    cwd = d.get("cwd", ".")
    if cwd is None:
        cwd = "."
    if not isinstance(cwd, str) or not cwd.strip():
        raise bad_manifest(file, "cwd：工作目录，相对这个文件夹（默认 \".\"）", "cwd: the working folder, relative to this "
                                                                             "folder (default \".\")")
    env = d.get("env") or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and k and "=" not in k and isinstance(v, str)
                                            for k, v in env.items()):
        raise bad_manifest(file, "env：环境变量，名字 → 文字", "env: environment variables, name -> text")
    app = d.get("app")
    if app is not None:
        if not isinstance(app, dict):
            raise bad_manifest(file, "app：这个程序驱动的 WuxianKit 应用的声明（JSON 对象：hear、controls……）",
                               "app: the declaration of the WuxianKit app the program drives (a JSON object: hear, "
                               "controls ...)")
        given = [k for k in NOT_IN_APP if k in app]
        if given:
            raise bad_manifest(file, f"app 里不写 {'、'.join(given)}：id、title 取清单的，driver 由加载器填",
                               f"app: leave out {', '.join(given)} (the id and title are the manifest's, the loader "
                               f"gives the driver)")
    return dict(id=pid, title=title, run=list(run), cwd=cwd, env=dict(env), app=app)


def manifest_of(path):
    """(folder, manifest file) of a path that names a program: its folder, or its manifest"""
    p = Path(path)
    if p.name.lower() == MANIFEST and not p.is_dir():
        return p.parent, p
    return p, p / MANIFEST


def folder_key(folder):
    return os.path.normcase(os.path.normpath(str(folder)))


def fallback_id(folder):
    """an id for a row whose manifest gives none: its folder's name, made one"""
    name = re.sub(r"[^a-z0-9_]+", "_", Path(folder).name.lower()).strip("_")
    if not name or not name[0].isalpha():
        name = "p_" + name
    return name[:ID_MAX].rstrip("_") or "program"


def declaration(manifest, launch=True):
    """what kit.app.declare gets for a program's app: the app with its id, title and driver, and launch"""
    return {**manifest["app"], "app": manifest["id"], "title": manifest["title"], "driver": "program",
            "launch": bool(launch)}


# --- output ----------------------------------------------------------------------------------------------------------

class Lines:
    """the lines of an output file, indexed as it grows: starts[i] is where line i + 1 begins, and the last item where
    the line still being written begins. Another file under the name (the next start moved this one aside) is
    indexed from its start"""

    def __init__(self, path):
        self.path = Path(path)
        self.reset(None)

    def reset(self, ident):
        self.ident, self.starts, self.size = ident, array("q", [0]), 0

    def update(self):
        """reads what was added since; the file's size"""
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

    @property
    def partial(self):
        return self.size > self.starts[-1]

    def read(self, first, last):
        """the texts of lines first..last (1-based); line complete + 1 is the one still being written"""
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


# --- runs ------------------------------------------------------------------------------------------------------------

class Run:
    """one start of a program: its process, its output, how it ended"""

    def __init__(self, pid_, folder, output, command, app):
        self.id, self.folder, self.output, self.command, self.app = pid_, Path(folder), Path(output), command, bool(app)
        self.pid = self.created = self.started = None
        self.exit_code = self.exited_at = None
        self.stop_asked = self.killed = False     # the loader stopped it (not a failure); by ending its tree
        self.proc = self.handle = None            # the Popen of a start of ours; the handle of one taken back
        self.done = threading.Event()             # set once it ended

    @property
    def running(self):
        return not self.done.is_set()

    def record(self):
        """what run.json keeps"""
        return dict(id=self.id, path=str(self.folder), output=str(self.output), command=self.command, app=self.app,
                    pid=self.pid, created=self.created, started=self.started, ended=self.done.is_set(),
                    exit_code=self.exit_code, exited_at=self.exited_at, stopped=self.stop_asked, killed=self.killed)

    @classmethod
    def from_record(cls, rec, output):
        run = cls(str(rec.get("id") or ""), rec["path"], output, rec.get("command"), rec.get("app"))
        run.pid, run.created, run.started = rec.get("pid"), rec.get("created"), rec.get("started")
        run.exit_code, run.exited_at = rec.get("exit_code"), rec.get("exited_at")
        run.stop_asked, run.killed = bool(rec.get("stopped")), bool(rec.get("killed"))
        return run


class Entry:
    """a registered folder as its manifest says now: id (the manifest's, else one made of the folder's name), the
    manifest (None: unreadable or not valid), error (why it cannot be started)"""

    def __init__(self, folder):
        self.folder, self.file = manifest_of(folder)
        self.key = folder_key(self.folder)
        try:
            self.manifest, self.error, self.code = read_manifest(self.file), None, None
        except ApiError as e:
            self.manifest, self.error, self.code = None, e.message, e.code
        self.id = self.manifest["id"] if self.manifest else fallback_id(self.folder)


def duration(seconds):
    return f"{seconds:.1f} s" if seconds < 120 else f"{seconds / 60:.1f} min"


class Programs:
    """the registered programs and their runs. root: the state folder (default paths.state_dir()); note(text): a line
    for the daemon's log. Any thread; changes are told to the subscribers (subscribe) on their asyncio loops"""

    def __init__(self, root=None, note=None):
        self.root = Path(root) if root is not None else state_dir()
        self.note = note or (lambda text: None)
        self.lock = threading.RLock()
        self.runs = {}                 # folder key -> its latest Run
        self.lines = {}                # output path -> Lines
        self.listeners = {}
        self.closing = False

    # --- telling of changes

    def subscribe(self, loop, callback):
        """callback() on that asyncio loop whenever a program is added, removed, started or ends; returns the function
        that ends it"""
        key = object()
        with self.lock:
            self.listeners[key] = (loop, callback)

        def unsubscribe():
            with self.lock:
                self.listeners.pop(key, None)
        return unsubscribe

    def changed(self):
        with self.lock:
            listeners = list(self.listeners.values())
        for loop, callback in listeners:
            try:
                loop.call_soon_threadsafe(callback)
            except RuntimeError:                  # the loop is closed: the connection is gone anyway
                pass

    # --- the registry

    @property
    def registry(self):
        return self.root / "programs.json"

    def folders(self):
        """the registered folders, in the order they were added"""
        try:
            data = json.loads(self.registry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("programs") if isinstance(data, dict) else None
        return [str(p["path"]) for p in items or [] if isinstance(p, dict) and isinstance(p.get("path"), str)]

    def save_folders(self, folders):
        write_json(self.registry, {"programs": [{"path": f} for f in folders]})

    def entries(self):
        """every registered folder as an Entry; of two with the same id the later one is refused (its error)"""
        out, taken = [], {}
        for folder in self.folders():
            e = Entry(folder)
            if e.manifest is not None:
                first = taken.get(e.id)
                if first is not None:
                    e.code = "program_exists"
                    e.error = tr(f"id {e.id} 已经被 {first.folder} 用了：改这个清单的 id",
                                 f"the id {e.id} is taken by {first.folder}: change the id in this manifest")
                else:
                    taken[e.id] = e
            out.append(e)
        return out

    def find(self, ident, entries=None):
        """the Entry an id names (a valid one first), or a folder or manifest path; ApiError not_found"""
        if not isinstance(ident, str) or not ident.strip():
            raise ApiError(400, "bad_request", tr("id：程序的 id（programs 列出来的）", "id: a program's id (as `programs` "
                                                                                      "lists them)"))
        ident = ident.strip()
        entries = self.entries() if entries is None else entries
        for e in sorted(entries, key=lambda e: e.error is not None):
            if e.id == ident:
                return e
        if os.path.isabs(ident):
            key = folder_key(manifest_of(ident)[0])
            for e in entries:
                if e.key == key:
                    return e
        raise ApiError(404, "not_found", tr(f"没有登记 id 为 {ident} 的程序（programs 列出登记过的，program_add 登记）",
                                            f"no program {ident} is registered (`programs` lists them, `program_add` "
                                            f"registers one)"))

    def add(self, path):
        """registers a program by its folder or its manifest: its row (the one there when that folder is registered
        already). ApiError for a path that is not absolute, a manifest missing or not valid, an id another folder has"""
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, "bad_request", tr("path：程序文件夹（或它的 wuxian-program.json）的完整路径",
                                                  "path: the full path of a program's folder (or its wuxian-program.json)"))
        p = Path(path.strip())
        if not p.is_absolute():
            raise ApiError(400, "bad_request", tr(f"path 要写完整路径（{path} 不是）", f"path: a full path ({path} is not)"))
        folder, file = manifest_of(p.resolve())
        manifest = read_manifest(file)
        with self.lock:
            folders = self.folders()
            entries = self.entries()
            key = folder_key(folder)
            if not any(folder_key(f) == key for f in folders):
                for e in entries:
                    if e.id == manifest["id"] and e.error is None:
                        raise ApiError(409, "program_exists", tr(
                            f"id {manifest['id']} 已经被 {e.folder} 用了：换一个 id，或先 program_remove 它",
                            f"the id {manifest['id']} is taken by {e.folder}: give this one another id, or remove that "
                            f"one first"))
                self.save_folders(folders + [str(folder)])
                added = True
            else:
                added = False
            row = self.row(self.find(str(folder)))
        if added:
            self.note(f"program {manifest['id']}: registered ({folder})")
            self.changed()
        return row

    def remove(self, ident):
        """takes a program off the registry (its folder and files stay; a running one is the caller's to stop first)"""
        with self.lock:
            e = self.find(ident)
            self.save_folders([f for f in self.folders() if folder_key(f) != e.key])
            run = self.runs.get(e.key)
            if run is not None and not run.running:
                del self.runs[e.key]
        self.note(f"program {e.id}: no longer registered ({e.folder})")
        self.changed()
        return dict(removed=True, id=e.id, path=str(e.folder))

    def declarations(self, launch=True):
        """[(id, what kit.app.declare gets)] of the programs that drive an app"""
        with self.lock:
            entries = self.entries()
        return [(e.id, declaration(e.manifest, launch)) for e in entries if e.error is None and e.manifest["app"] is not None]

    # --- rows

    def output_of(self, pid_):
        return self.root / "programs" / pid_ / "output.log"

    def row(self, e):
        """a program as the API shows it"""
        run = self.runs.get(e.key)
        m = e.manifest
        if run is None:
            state = "stopped"
        elif run.running:
            state = "running"
        else:
            state = "stopped" if run.stop_asked else "exited"
        ended = run is not None and not run.running
        return dict(id=e.id, title=m["title"] if m else e.folder.name, path=str(e.folder), manifest=str(e.file),
                    app=bool(m and m["app"] is not None), state=state, pid=run.pid if state == "running" else None,
                    started=run.started if run else None, exit_code=run.exit_code if ended else None,
                    exited_at=run.exited_at if ended else None, stopped=bool(ended and run.stop_asked),
                    command=run.command if run is not None else (list(m["run"]) if m else None),
                    error=e.error, output=str(run.output if run is not None else self.output_of(e.id)))

    def rows(self):
        with self.lock:
            return [self.row(e) for e in self.entries()]

    def get(self, ident):
        with self.lock:
            return self.row(self.find(ident))

    def run_of(self, ident):
        """(the Entry, its Run or None)"""
        with self.lock:
            e = self.find(ident)
            return e, self.runs.get(e.key)

    # --- starting

    def environment(self, e):
        """the program's environment: the daemon's, PYTHONUNBUFFERED and PYTHONIOENCODING, the manifest's env, then
        WUXIAN_PROGRAM, WUXIAN_PROGRAM_MANIFEST and WUXIAN_HOME"""
        env = dict(os.environ)

        def put(name, value):
            if WINDOWS:                           # names are not case-sensitive there: one of each
                for k in [k for k in env if k.upper() == name.upper()]:
                    del env[k]
            env[name] = value

        put("PYTHONUNBUFFERED", "1")
        put("PYTHONIOENCODING", "utf-8")
        for name, value in e.manifest["env"].items():
            put(name, value)
        put("WUXIAN_PROGRAM", e.id)
        put("WUXIAN_PROGRAM_MANIFEST", str(e.file))
        put("WUXIAN_HOME", str(home()))
        return env

    @staticmethod
    def command(e, env):
        """the argv the program starts with: its first item a full path; ApiError bad_command when it is not there"""
        run = e.manifest["run"]
        exe = run[0].strip()
        if "/" in exe or "\\" in exe:
            path = Path(exe) if Path(exe).is_absolute() else e.folder / exe
            found = shutil.which(str(path)) or (str(path) if path.is_file() else None)
            where = tr(f"（相对 {e.folder}）", f" (relative to {e.folder})")
        else:
            search = next((v for k, v in env.items() if k.upper() == "PATH"), None) if WINDOWS else env.get("PATH")
            found = shutil.which(exe, path=search or os.defpath)
            where = tr("在 PATH 里", " on PATH")
        if not found:
            hint = tr("：本机要装 Python 3.10 以上并在 PATH 里，或者把 run 的第一项写成 python.exe 的路径（虚拟环境的也行）",
                      ": Python 3.10 or later must be installed and on PATH, or the first item of run must be the path "
                      "of a python.exe (a virtual environment's will do)") if exe.lower() in ("python", "python3",
                                                                                               "py") else ""
            raise ApiError(400, "bad_command", tr(f"找不到 {exe}{where}{hint}", f"{exe} not found{where}{hint}"))
        return [os.path.abspath(found)] + run[1:]

    def start(self, ident, why=None):
        """starts a program that does not run: its row. ApiError for a manifest that is not valid, a program that
        runs already (409 running), a command that is not there, a start that failed"""
        with self.lock:
            e = self.find(ident)
            if e.error:
                raise ApiError(409 if e.code == "program_exists" else 400, e.code or "bad_manifest", e.error)
            run = self.runs.get(e.key)
            if run is not None and run.running:
                raise ApiError(409, "running", tr(f"{e.id} 已经在运行（pid {run.pid}）：restart: true 先停再启动",
                                                  f"{e.id} is running already (pid {run.pid}): restart: true stops it "
                                                  f"and starts it again"))
            m = e.manifest
            env = self.environment(e)
            argv = self.command(e, env)
            cwd = (e.folder / m["cwd"]).resolve()
            if not cwd.is_dir():
                raise bad_manifest(e.file, f"cwd：没有这个文件夹：{cwd}", f"cwd: no such folder: {cwd}")
            out = self.output_of(e.id)
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists():
                try:
                    replace_retrying(out, out.with_name("output.1.log"))
                except OSError as ex:
                    raise ApiError(409, "output_busy", tr(f"上一次的输出 {out} 还被别的进程开着（上次留下的子进程？）：{ex}",
                                                          f"the last output {out} is held open by another process (one "
                                                          f"the last run left behind?): {ex}")) from None
            self.lines.pop(str(out), None)
            fd = open_output(out)
            try:
                kw = dict(creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if WINDOWS else \
                    dict(start_new_session=True)
                proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=fd,
                                        stderr=subprocess.STDOUT, **kw)
            except OSError as ex:
                raise ApiError(400, "start_failed", tr(f"{e.id} 启动不了：{ex}", f"{e.id} could not start: {ex}")) from None
            finally:
                os.close(fd)
            run = Run(e.id, e.folder, out, argv, m["app"] is not None)
            run.proc, run.pid, run.started = proc, proc.pid, time.time()
            run.created = process_created(proc.pid)
            self.runs[e.key] = run
            self.save_run(run)
            threading.Thread(target=self.wait_proc, args=(run,), name=f"wuxian-program-{e.id}", daemon=True).start()
            row = self.row(e)
        self.note(f"program {e.id}: started" + (f" ({why})" if why else "") + f", pid {run.pid}: "
                  f"{subprocess.list2cmdline(argv)}")
        self.changed()
        return row

    def save_run(self, run):
        try:
            write_json(run.output.with_name("run.json"), run.record())
        except OSError as e:
            self.note(f"program {run.id}: run.json not written: {e}")

    def wait_proc(self, run):
        """(its thread) waits for a process this loader started"""
        self.ended(run, run.proc.wait())

    def wait_handle(self, run):
        """(its thread) waits for a process taken back from before the daemon started"""
        while not has_ended(run.handle, 1000):
            if self.closing and not run.stop_asked:
                return
        code = exit_code_of(run.handle)
        close_process(run.handle)
        run.handle = None
        self.ended(run, code)

    def ended(self, run, code):
        with self.lock:
            run.exited_at = time.time()
            run.exit_code = None if run.killed else code
            run.done.set()
            self.save_run(run)
        took = duration(run.exited_at - run.started) if run.started else "an unknown time"
        if run.stop_asked:
            self.note(f"program {run.id}: stopped after {took}" + (" (its process tree was ended)" if run.killed
                                                                   else f" (it exited with code {code})"))
        else:
            self.note(f"program {run.id}: exited with code {code} after {took}"
                      + ("" if code == 0 else " (program_logs shows its output)"))
        self.changed()

    # --- stopping

    def kill(self, run):
        """ends a run's process tree now; True once it has ended"""
        if not run.running:
            return True
        run.stop_asked = run.killed = True
        kill_tree(run.pid)
        if run.done.wait(KILL_WAIT):
            return True
        try:                                      # taskkill could not: the root process at least
            if run.proc is not None:
                run.proc.kill()
            elif WINDOWS and run.handle:
                _k32.TerminateProcess(run.handle, 1)
        except OSError:
            pass
        return run.done.wait(KILL_WAIT)

    def close(self):
        """the daemon stops: every program running under it (started, or taken back) is ended"""
        self.closing = True
        with self.lock:
            running = [r for r in self.runs.values() if r.running]
        for run in running:
            self.kill(run)

    # --- taking back what runs from before

    def adopt(self):
        """(the daemon's start) the runs that run.json records for the registered folders: the processes still
        running (the same pid with the same creation time) are taken back, the others counted as ended"""
        adopted = []
        with self.lock:
            keys = {folder_key(f) for f in self.folders()}
            latest = {}
            for file in sorted((self.root / "programs").glob("*/run.json")):
                try:
                    rec = json.loads(file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(rec, dict) or not isinstance(rec.get("path"), str):
                    continue
                key = folder_key(rec["path"])
                if key in keys and key not in self.runs and (key not in latest or (rec.get("started") or 0) >
                                                              (latest[key][0].get("started") or 0)):
                    latest[key] = (rec, file.with_name("output.log"))
            for key, (rec, output) in latest.items():
                run = Run.from_record(rec, output)
                self.runs[key] = run
                if rec.get("ended"):
                    run.done.set()
                    continue
                h = open_process(run.pid, run.created) if isinstance(run.pid, int) else None
                if h is not None and has_ended(h):
                    run.exit_code, run.exited_at = exit_code_of(h), None
                    close_process(h)
                    h = None
                    run.done.set()
                elif h is None:
                    run.done.set()                # it ended while no daemon watched: how is not known
                if run.done.is_set():
                    self.save_run(run)
                    continue
                run.handle = h
                adopted.append(run)
                threading.Thread(target=self.wait_handle, args=(run,), name=f"wuxian-program-{run.id}",
                                 daemon=True).start()
        for run in adopted:
            self.note(f"program {run.id}: still running from before (pid {run.pid}): taken back")
        if latest:
            self.changed()
        return [r.id for r in adopted]

    # --- output

    def logs(self, ident, since=0, limit=200):
        """the program's output (its latest start's, stdout and stderr together) by line: {lines: [{n, text}], next,
        total, reset, cut, row}. since 0: the output from its first line, or its last `limit` lines when it has more
        (cut true); since n: the lines from n on, at most `limit`, oldest first; next: the since of the next read. A
        since past the end is another start's: its lines from the first (reset true). The line still being written
        comes once the program ended"""
        if not isinstance(since, int) or isinstance(since, bool) or since < 0:
            raise ApiError(400, "bad_request", "since: 0 (the newest lines) or a line number (the `next` of the last read)")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= LOGS_MOST:
            raise ApiError(400, "bad_request", f"limit: 1..{LOGS_MOST}")
        with self.lock:
            e = self.find(ident)
            run = self.runs.get(e.key)
            path = run.output if run is not None else self.output_of(e.id)
            lines = self.lines.get(str(path))
            if lines is None:
                lines = self.lines[str(path)] = Lines(path)
            lines.update()
            running = run is not None and run.running
            total = lines.complete + (1 if lines.partial and not running else 0)
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
        return dict(lines=[dict(n=first + i, text=t) for i, t in enumerate(texts)],
                    next=first + len(texts), total=total, reset=reset, cut=since == 0 and first > 1, row=row)
