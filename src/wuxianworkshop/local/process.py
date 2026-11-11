r"""One process of a local extension: what it is started with and how it runs and ends.

Its environment is a short list, not the daemon's own (environment): of the daemon's only the names of ENV_KEEP and
those the manifest's pass names; PYTHONUNBUFFERED=1 and PYTHONIOENCODING=utf-8; then the manifest's env; then what
the workshop gives: WUXIAN_EXTENSION=<id>, WUXIAN_HOME (and the daemon's WUXIAN_MUTEX, when it has one),
WUXIAN_EXTENSION_DATA=<state>\extensions\<id>\data (the folder of its own data, made before it starts: it is not in
the extension's folder, so another version of the extension keeps it) and
PYTHONPATH=<the workshop's SDK folder> (before a PYTHONPATH the manifest passed or gave). Its command is the manifest's
run, its first item found (command: "python" is the manifest's python, else the workshop's own); its cwd is its folder.
Windows: CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, and the process is put in a job object of its own (job_of) whose
processes end when the job's handle is closed, which the end of the daemon does: an extension, and whatever it
started, does not outlive the workshop. (Without a job, when Windows refuses one: a process it started outlives an
extension that exits by itself; taskkill ends the tree only of one that still runs.)

Proc is one run: the Popen and four threads. One reads its stdout (the protocol: rpc.py; anything else goes to its
output), one its stderr (its log: logs.py), one writes its stdin, one waits for its end. Nobody else touches the pipes:
send() only queues a line for the writer (WRITE_QUEUE at most), so no caller, lock or event loop ever waits for an
extension that does not read; a request whose line is not written within WRITE_WAIT seconds, or that finds the queue
full, ends as busy. Each thread closes its own stream, so a helper process that still holds a pipe keeps nothing
waiting: when the extension's own process has exited its tree is ended (the job; elsewhere its process group), its
requests fail, and whoever started it hears of the end (on_end) before anything is closed. It ends by shutdown (the
request, then its tree ended after SHUTDOWN_WAIT seconds) or by kill; whoever started it also hears of what it asks and
tells (on_message). Which extension runs, and when it starts again, is the host's (host.py). Standard library only.
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..daemon.api import ApiError
from ..paths import home
from . import rpc as R
from .logs import LINE_MAX, clock
from .rpc import Busy, Wire, busy_error, frame, parse

SHUTDOWN_WAIT = 5.0           # seconds an extension has to exit after shutdown before its process tree is ended
KILL_WAIT = 5.0               # seconds an ended process tree has to be gone
WRITE_QUEUE = 256             # lines waiting to be written to an extension's stdin at most
WRITE_WAIT = 5.0              # seconds a request's line may wait to be written before the request ends as busy
WATCH = 0.5                   # seconds between two looks at the lines that wait (the waiter thread)
LAST_WORDS = 2.0              # seconds the readers have for what it wrote last, before it is called ended
STDERR_LINE = 64 * 1024       # bytes of stderr read as one line at most (a longer one is cut into several)

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
WINDOWS = sys.platform == "win32"

# the daemon's own environment variables an extension's process gets (Windows: whatever their case)
ENV_KEEP = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP", "USERPROFILE",
            "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "LANG",
            # who and where: without USERNAME getpass.getuser() goes on to `import pwd`, which Windows has not (torch
            # does at import); the rest is what libraries find the system's folders by
            "USERNAME", "USERDOMAIN", "COMPUTERNAME", "OS", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
            "COMMONPROGRAMFILES", "ALLUSERSPROFILE", "PUBLIC")
ENV_KEEP_POSIX = ("HOME", "TMPDIR", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME")
ENV_WORKSHOP = ("WUXIAN_MUTEX",)                      # the daemon's own WUXIAN_* that are handed on as they are
DATA_ENV = "WUXIAN_EXTENSION_DATA"                    # the folder of an extension's own data (data_folder)


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


# --- a job object (Windows): the processes of one extension ----------------------------------------------------------

JOB_EXTENDED_LIMIT_INFORMATION = 9             # JobObjectExtendedLimitInformation
JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x2000           # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
_kernel32 = None


def kernel32():
    """kernel32's job functions with their signatures (ctypes); Windows only"""
    global _kernel32
    if _kernel32 is None:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype, k.CreateJobObjectW.argtypes = wintypes.HANDLE, (ctypes.c_void_p, wintypes.LPCWSTR)
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k.TerminateJobObject.restype, k.TerminateJobObject.argtypes = wintypes.BOOL, (wintypes.HANDLE, wintypes.UINT)
        k.CloseHandle.restype, k.CloseHandle.argtypes = wintypes.BOOL, (wintypes.HANDLE,)
        _kernel32 = k
    return _kernel32


def job_limits():
    """a JOBOBJECT_EXTENDED_LIMIT_INFORMATION that says KILL_ON_JOB_CLOSE"""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    info = Extended()
    info.BasicLimitInformation.LimitFlags = JOB_LIMIT_KILL_ON_JOB_CLOSE
    return info


def job_of(popen):
    """(Windows) a new job object with that process in it: the handle of the job (an int), whose processes, those the
    process starts later among them, end when it is closed (KILL_ON_JOB_CLOSE): job_close, or the end of this process,
    which closes its handles. None elsewhere, or when Windows refuses (the process runs on without a job)"""
    if not WINDOWS:
        return None
    try:
        import ctypes
        k = kernel32()
        job = k.CreateJobObjectW(None, None)
        if not job:
            return None
        info = job_limits()
        if k.SetInformationJobObject(job, JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)) \
                and k.AssignProcessToJobObject(job, int(popen._handle)):
            return job
        k.CloseHandle(job)
    except Exception:                                 # no ctypes, no kernel32 function: no job
        pass
    return None


def job_terminate(job):
    """every process of the job ended now; True when Windows said it did"""
    try:
        return bool(kernel32().TerminateJobObject(job, 1))
    except Exception:
        return False


def job_close(job):
    try:
        kernel32().CloseHandle(job)
    except Exception:
        pass


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


def data_folder(eid, state=None):
    """<state>\\extensions\\<id>\\data: the folder of an extension's own data (WUXIAN_EXTENSION_DATA), which is not in
    its own folder, so another version of the extension finds it as it was. state: the state folder (default: the one
    under WUXIAN_HOME). Not made here: spawn does that"""
    return (Path(state) if state is not None else home() / "state") / "extensions" / eid / "data"


def environment(m, sdk, state=None):
    """the environment of the process of manifest m; sdk: the folder put first on PYTHONPATH; state: the state folder
    its data folder is under (data_folder). Of the daemon's own environment only ENV_KEEP and the names of the
    manifest's pass get through (its secrets, its other WUXIAN_* and its PYTHON* do not); then the manifest's env,
    then what the workshop gives"""
    fold = (lambda name: name.upper()) if WINDOWS else (lambda name: name)   # names are not case-sensitive on Windows
    keep = {fold(n) for n in ENV_KEEP + (() if WINDOWS else ENV_KEEP_POSIX)}
    keep |= {fold(n) for n in m.get("pass") or [] if isinstance(n, str)}
    env = {k: v for k, v in os.environ.items() if fold(k) in keep}

    def put(name, value):
        for k in [k for k in env if fold(k) == fold(name)]:               # one of each
            del env[k]
        env[name] = value

    def get(name):
        return next((v for k, v in env.items() if fold(k) == fold(name)), None)

    put("PYTHONUNBUFFERED", "1")
    put("PYTHONIOENCODING", "utf-8")
    for name, value in m["env"].items():
        put(name, value)
    put("WUXIAN_EXTENSION", m["id"])
    put("WUXIAN_HOME", str(home()))
    put(DATA_ENV, str(data_folder(m["id"], state)))
    for name in ENV_WORKSHOP:                         # which workshop it belongs to (a test instance stays one)
        if os.environ.get(name):
            put(name, os.environ[name])
    before = get("PYTHONPATH")                        # only one the manifest passed or gave
    put("PYTHONPATH", str(sdk) + (os.pathsep + before if before else ""))
    return env


def make_data_folder(env):
    """the data folder an environment names (WUXIAN_EXTENSION_DATA) is there: made before anything is started with
    that environment (OSError when it cannot be made); nothing when it names none"""
    path = next((v for k, v in env.items() if k.upper() == DATA_ENV), None)
    if path:
        os.makedirs(path, exist_ok=True)


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
    """the process of manifest m started with argv (command) and env (environment), its pipe open, in a job of its own
    (Windows): its Proc (ApiError start_failed). Its data folder (env's WUXIAN_EXTENSION_DATA) is made first"""
    kw = dict(creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if WINDOWS else dict(start_new_session=True)
    try:
        make_data_folder(env)
        popen = subprocess.Popen(argv, cwd=m["folder"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, **kw)
    except OSError as e:
        raise ApiError(400, "start_failed", f"{m['id']} could not start: {e}") from None
    job = job_of(popen)
    output.write(f"-- {clock()} started (pid {popen.pid}): {subprocess.list2cmdline(argv)}")
    if WINDOWS and job is None:
        output.write(f"-- {clock()} Windows gave it no job object: what it starts may outlive it and the workshop")
    return Proc(m["id"], popen, output, on_message, on_end, argv, job)


def fallback_encoding():
    """what bytes that are not UTF-8 are read as: Windows' own code page (GBK on a Chinese one); None elsewhere"""
    return "mbcs" if WINDOWS else None


# --- one run of an extension -----------------------------------------------------------------------------------------

class Proc(Wire):
    """one process of an extension: its pipe, the requests waiting for their answers, how it ended. on_message(proc,
    msg): a request or a notification of the extension (its reader thread); on_end(proc): it ended (its waiter thread).
    job: its job object's handle (Windows), or None"""

    def __init__(self, eid, popen, output, on_message, on_end, argv, job=None):
        self.id, self.popen, self.output, self.argv = eid, popen, output, argv
        self.on_message, self.on_end = on_message, on_end
        self.pid, self.started = popen.pid, time.time()
        super().__init__()                          # lock, pending, next_id
        self.job, self.jlock = job, threading.Lock()
        self.outbox = queue.Queue(WRITE_QUEUE)      # (line, the id of the workshop's request it is, or None)
        self.wlock = threading.Lock()               # unwritten, wclosed
        self.unwritten = {}                         # request id -> when its line was queued (monotonic), until written
        self.writing = None                         # (since when, request id or None): the line being written now
        self.stuck = False                          # a write has taken WRITE_WAIT seconds: it does not read its input
        self.wclosed = False                        # the writer is done: nothing more is written
        self.ready = False                          # it answered initialize
        self.stopping = False                       # the host stops it (not a crash)
        self.held, self.dropped = [], 0             # (the host's) its events before it is ready, and how many more
        self.pinging = False                        # (the host's) a ping waits for its answer
        self.sources = None                         # (the host's) the names of the sources it described
        self.events = None                          # (the host's) [the events it may tell now, when that was counted]
        self.lost, self.lost_said = 0, None         # (the host's) its events dropped since its output said so; when
        self.odd = set()                            # the streams that were said not to be UTF-8
        self.exit_code, self.ended = None, threading.Event()
        self.finished = threading.Event()           # ended, and the host has taken note (on_end returned)
        self.readers = [threading.Thread(target=self.read_out, name=f"wuxian-ext-{eid}-out", daemon=True),
                        threading.Thread(target=self.read_err, name=f"wuxian-ext-{eid}-err", daemon=True)]
        for t in self.readers:
            t.start()
        threading.Thread(target=self.write_in, name=f"wuxian-ext-{eid}-in", daemon=True).start()
        threading.Thread(target=self.wait, name=f"wuxian-ext-{eid}-wait", daemon=True).start()

    @property
    def running(self):
        return not self.ended.is_set()

    def gone_error(self):
        return ApiError(503, "down", f"{self.id} exited (code {self.exit_code})")

    # --- writing

    def send(self, msg):
        """(any thread) the message queued for the writer thread; never waits for the pipe. BrokenPipeError: nothing
        is written any more; Busy: the queue is full, or (a request) the extension does not read its input"""
        rid = msg.get("id") if "method" in msg else None       # a request of the workshop's
        if rid is not None and self.stuck:
            raise Busy(f"it has not read its input for {WRITE_WAIT:.0f} s")
        data = frame(msg)
        with self.wlock:
            if self.wclosed or self.ended.is_set():
                raise BrokenPipeError("its input is closed")
            try:
                self.outbox.put_nowait((data, rid))
            except queue.Full:
                raise Busy(f"{WRITE_QUEUE} lines wait to be written to it (it does not read its input)") from None
            if rid is not None:
                self.unwritten[rid] = time.monotonic()

    def write_in(self):
        """(the writer thread, the only one that touches its stdin) the queued lines, one after another; a request
        whose caller gave up before its turn is not written"""
        stdin = self.popen.stdin
        try:
            while not self.ended.is_set():
                try:
                    data, rid = self.outbox.get(timeout=WATCH)
                except queue.Empty:
                    continue
                if rid is not None:
                    with self.lock:
                        waited = rid in self.pending
                    if not waited:
                        with self.wlock:
                            self.unwritten.pop(rid, None)
                        continue
                self.writing = (time.monotonic(), rid)
                try:
                    stdin.write(data)
                    stdin.flush()
                finally:
                    self.writing = None
                with self.wlock:
                    self.unwritten.pop(rid, None)
                if self.stuck:
                    self.stuck = False
                    self.output.write(f"-- {clock()} it reads its input again")
        except (OSError, ValueError):                 # the pipe broke: it exited, or closed its stdin
            pass
        finally:
            with self.wlock:
                self.wclosed = True
                left, self.unwritten = list(self.unwritten), {}
            try:
                stdin.close()
            except (OSError, ValueError):
                pass
            for rid in left:
                self.fail(rid, ApiError(503, "down", f"{self.id}: its input is closed"))

    def watch(self):
        """(the waiter thread, every WATCH seconds) the requests whose line was not written within WRITE_WAIT seconds
        end as busy; the one that is half written is cancelled, for when the extension reads on"""
        now = time.monotonic()
        writing = self.writing
        if writing is not None and now - writing[0] >= WRITE_WAIT and not self.stuck:
            self.stuck = True
            self.output.write(f"-- {clock()} it has not read its input for {WRITE_WAIT:.0f} s: calls of it are "
                              f"refused (busy) until it does")
        with self.wlock:
            late = [rid for rid, since in self.unwritten.items() if now - since >= WRITE_WAIT]
            for rid in late:
                del self.unwritten[rid]
        for rid in late:
            self.fail(rid, busy_error(self.id, f"its request could not be written within {WRITE_WAIT:.0f} s (it does "
                                               f"not read its input)"))
            if writing is not None and writing[1] == rid:
                self.notify("cancel", dict(id=rid))

    # --- reading

    def decode(self, raw, stream):
        """the text of bytes it wrote: UTF-8, as the protocol says; bytes that are not (a program that writes GBK) are
        read as Windows' code page, and its output says so once for each stream"""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        encoding = fallback_encoding()
        if stream not in self.odd:
            self.odd.add(stream)
            self.output.write(f"-- {clock()} its {stream} has bytes that are not UTF-8: they are read as "
                              + ("this computer's code page" if encoding else "U+FFFD")
                              + " (said once). It should write UTF-8: the protocol is UTF-8, and Python does with the "
                                "PYTHONIOENCODING the workshop sets")
        try:
            return raw.decode(encoding or "utf-8", "replace")
        except (LookupError, UnicodeError):
            return raw.decode("utf-8", "replace")

    def read_out(self):
        """(a reader thread) its stdout by line: the protocol, else its output. A line of more than rpc.LINE_LIMIT
        bytes is dropped"""
        stream = self.popen.stdout
        try:
            while True:
                limit = R.LINE_LIMIT
                raw = stream.readline(limit + 1)
                if not raw:
                    break
                if len(raw) > limit and not raw.endswith(b"\n"):
                    size = len(raw)
                    while raw and not raw.endswith(b"\n"):
                        raw = stream.readline(STDERR_LINE)
                        size += len(raw)
                    self.output.write(f"-- {clock()} a line of {size} bytes on its stdout dropped: a line of the "
                                      f"protocol has {limit} bytes at most")
                    continue
                self.line(raw)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def line(self, raw):
        text = self.decode(raw, "stdout").strip()
        if not text:
            return
        msg = parse(text)
        if msg is None:
            self.output.write(f"stdout: {text[:LINE_MAX]}")       # not the protocol: print() to stdout, say
            return
        if "method" in msg:
            try:
                self.on_message(self, msg)
            except Exception as e:                                # a bug of the host: said, the pipe goes on
                self.output.write(f"-- {clock()} the workshop could not handle {msg.get('method')!r}: {e!r}")
            return
        if not self.answer(msg):
            rid = msg.get("id")
            if isinstance(rid, bool) or not isinstance(rid, int):  # none of the workshop's (those are numbers)
                self.output.write(f"-- {clock()} an answer with the id {str(rid)[:80]!r} dropped: the workshop asked "
                                  f"no request of that id")

    def read_err(self):
        stream = self.popen.stderr
        try:
            for raw in iter(lambda: stream.readline(STDERR_LINE), b""):
                self.output.write(self.decode(raw, "stderr").rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    # --- its end

    def end_tree(self, exited=False):
        """every process of its tree ended: its job (Windows; also what it left behind when it has exited itself),
        else taskkill / its process group. exited: its own process is gone already"""
        with self.jlock:
            if self.job is not None and job_terminate(self.job):
                return True
        if exited and WINDOWS:                         # taskkill finds no tree under a process that is gone
            return False
        return kill_tree(self.pid)

    def wait(self):
        """(the waiter thread) until its process has exited; then its tree is ended (helpers it left may hold its
        pipes open), its requests fail, and whoever started it hears of the end. No stream is closed here: each
        thread closes its own, so nothing here waits for a pipe"""
        while True:
            try:
                code = self.popen.wait(WATCH)
                break
            except subprocess.TimeoutExpired:
                try:
                    self.watch()
                except Exception as e:                 # a bug here must not keep its end from being seen
                    self.output.write(f"-- {clock()} the workshop could not look at its waiting lines: {e!r}")
        self.end_tree(exited=True)
        deadline = time.monotonic() + LAST_WORDS
        for t in self.readers:                         # what it wrote last, before it is called ended
            t.join(max(0.0, deadline - time.monotonic()))
        with self.lock:
            self.exit_code = code
            self.ended.set()
            pending, self.pending = list(self.pending.values()), {}
        for fut in pending:
            if not fut.done():
                try:
                    fut.set_exception(self.gone_error())
                except Exception:                      # done by another thread in between
                    pass
        try:
            self.on_end(self)
        except Exception as e:
            self.output.write(f"-- {clock()} the workshop could not take note of its end: {e!r}")
        finally:
            self.finished.set()
            with self.jlock:
                job, self.job = self.job, None
            if job is not None:
                job_close(job)

    def kill(self):
        """ends its process tree; True once it has ended (and the host has taken note)"""
        if not self.finished.is_set():
            self.end_tree(exited=self.ended.is_set())
            if not self.ended.wait(KILL_WAIT):
                try:
                    self.popen.kill()
                except OSError:
                    pass
        return self.finished.wait(KILL_WAIT)

    def shutdown(self, wait=None):
        """shutdown, then its tree ended when it has not exited within wait seconds (default: SHUTDOWN_WAIT as it is
        now); a shutdown that cannot be asked (its pipe takes no line) is not waited for. True once it has ended"""
        wait = SHUTDOWN_WAIT if wait is None else wait
        self.stopping = True
        if not self.ended.is_set() and self.ready:
            refused = threading.Event()
            fut = self.request("shutdown", {})
            fut.add_done_callback(lambda f: (f.cancelled() or f.exception() is not None) and refused.set())
            deadline = time.monotonic() + wait
            while not refused.is_set() and time.monotonic() < deadline:
                if self.ended.wait(0.05):
                    break
        return self.kill()
