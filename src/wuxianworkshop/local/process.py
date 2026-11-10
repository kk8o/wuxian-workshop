r"""One process of a local extension: what it is started with and how it runs and ends.

Its process gets the daemon's environment, PYTHONUNBUFFERED=1, PYTHONIOENCODING=utf-8, the manifest's env,
WUXIAN_EXTENSION=<id>, WUXIAN_HOME and PYTHONPATH=<the workshop's SDK folder> before what was there (environment); its
command is the manifest's run, its first item found (command: "python" is the manifest's python, else the workshop's
own); its cwd is its folder. Windows: CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, and taskkill /T /F ends a process
tree (kill_tree).

Proc is one run: the Popen, a thread reading its stdout (the protocol: rpc.py; anything else goes to its output), one
reading its stderr (its log: logs.py), one waiting for its end. It ends by shutdown (the request, then its tree ended
after SHUTDOWN_WAIT seconds) or by kill; whoever started it hears of what it asks and tells (on_message) and of its end
(on_end). Which extension runs, and when it starts again, is the host's (host.py). Standard library only.
"""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..daemon.api import ApiError
from ..paths import home
from .logs import LINE_MAX, clock
from .rpc import Wire, frame, parse

SHUTDOWN_WAIT = 5.0           # seconds an extension has to exit after shutdown before its process tree is ended
KILL_WAIT = 5.0               # seconds an ended process tree has to be gone

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS = sys.platform == "win32"


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


# --- what a process is started with ----------------------------------------------------------------------------------

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


def environment(m, sdk):
    """the environment of the process of manifest m; sdk: the folder put first on PYTHONPATH"""
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
    put("PYTHONPATH", str(sdk) + (os.pathsep + before if before else ""))
    return env


def interpreter(m, python=None):
    """the Python of an extension (the manifest's, else python: the host's, else the workshop's), or None"""
    return m["python"] or python or default_python()


def command(m, env, python=None):
    """the argv it starts with: its first item a full path; ApiError bad_command when it is not there. python: the
    host's interpreter for run ["python", ...] without the manifest's own"""
    run = list(m["run"])
    exe = run[0].strip()
    folder = Path(m["folder"])
    if exe.lower() == "python":
        found = interpreter(m, python)
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


def spawn(m, env, argv, output, on_message, on_end):
    """the process of manifest m started with argv (command) and env (environment), its pipe open: its Proc (ApiError
    start_failed)"""
    kw = dict(creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if WINDOWS else dict(start_new_session=True)
    try:
        popen = subprocess.Popen(argv, cwd=m["folder"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, **kw)
    except OSError as e:
        raise ApiError(400, "start_failed", f"{m['id']} could not start: {e}") from None
    output.write(f"-- {clock()} started (pid {popen.pid}): {subprocess.list2cmdline(argv)}")
    return Proc(m["id"], popen, output, on_message, on_end, argv)


# --- one run of an extension -----------------------------------------------------------------------------------------

class Proc(Wire):
    """one process of an extension: its pipe, the requests waiting for their answers, how it ended. on_message(proc,
    msg): a request or a notification of the extension (its reader thread); on_end(proc): it ended (its waiter thread)"""

    def __init__(self, eid, popen, output, on_message, on_end, argv):
        self.id, self.popen, self.output, self.argv = eid, popen, output, argv
        self.on_message, self.on_end = on_message, on_end
        self.pid, self.started = popen.pid, time.time()
        super().__init__()                          # lock, pending, next_id
        self.wlock = threading.Lock()
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
        data = frame(msg)
        with self.wlock:
            self.popen.stdin.write(data)
            self.popen.stdin.flush()

    def gone_error(self):
        return ApiError(503, "down", f"{self.id} exited (code {self.exit_code})")

    def read_out(self):
        for raw in iter(self.popen.stdout.readline, b""):
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            msg = parse(text)
            if msg is None:
                self.output.write(f"stdout: {text[:LINE_MAX]}")       # not the protocol: print() to stdout, say
                continue
            if "method" in msg:
                try:
                    self.on_message(self, msg)
                except Exception as e:                                # a bug of the host: said, the pipe goes on
                    self.output.write(f"-- {clock()} the workshop could not handle {msg.get('method')!r}: {e!r}")
                continue
            self.answer(msg)

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
