r"""The checks of a local extension that need more than its files (manifest.py checks those): check(host, ident) starts
a process of its own, initializes it (its description checked), shuts it down, and runs the extension's tests (python
-m unittest discover -s tests in its folder, with its Python, TESTS_TIMEOUT seconds); the answer is {ok, problems,
capabilities, running?, tests?}, each problem as manifest.problem gives it. The check's process writes
<state>\extensions\<id>\check.log; its events are dropped and its calls refused.

No second process of an extension that runs: while the registered one runs (or is being started) the check starts
none, takes the description it gave at its start and says so (running: its pid, and a warning; extension_load checks
it as its files are now). And nothing of an extension runs before its command is approved (Host.allow): a registered
one that is not approved, and any folder that is not registered, is refused with needs_approval, unless the setting
lets the workshop approve commands by itself (host.auto()).

The functions take the host (host.py Host) for what is the host's: the registry, how a process is started (spawn,
environment, interpreter) and initialize. extension_load (Host.load_) is the same checks around a restart of the
registered one, and uses run_tests and check_description from here. Standard library only.
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..daemon.api import ApiError
from . import manifest as M
from .logs import Output, clock, tail
from .process import CREATE_NO_WINDOW, DATA_ENV, WINDOWS
from .registry import check_folder
from . import rpc as R
from .rpc import INIT_TIMEOUT, Down

TESTS_TIMEOUT = 120.0         # seconds the extension's tests may take (check)
TESTS_OUTPUT = 4000           # characters of the tests' output kept


def check_description(eid, answer):
    """(description, problems) of an extension's answer to initialize, as manifest.check_description gives them; Down
    (with the problems) when the description cannot be used"""
    desc, problems = M.check_description(eid, answer)
    if desc is None:
        first = M.errors_in(problems)[0]
        raise Down(f"{eid}: {first['where']}: {first['message']}"
                   + (f" ({first['hint']})" if first.get("hint") else ""), problems)
    return desc, problems


def resolve(host, ident):
    """(registered entry or None, folder) of an id or a path"""
    if not isinstance(ident, str) or not ident.strip():
        raise ApiError(400, "bad_request", "id: a local extension's id, or the full path of an extension folder")
    ident = ident.strip()
    for e in host.load()["extensions"]:
        if e["id"] == ident:
            return e, Path(e["path"])
    p = Path(ident)
    if not p.is_absolute():
        host.entry(ident)                                  # not_found
    check_folder(p)
    folder, _ = M.locate(p)
    key = os.path.normcase(str(folder.resolve()))
    same = next((e for e in host.load()["extensions"] if os.path.normcase(e["path"]) == key), None)
    return same, folder.resolve()


def gate(host, e, folder):
    """nothing of an extension runs before its command is approved: a registered one as Host.allow says (which
    approves it there where the setting says so); a folder that is not registered only where the workshop approves
    commands by itself (host.auto()). ApiError needs_approval"""
    if e is not None:
        return host.allow(e)
    if not host.auto():
        raise ApiError(403, "needs_approval",
                       f"{folder} is not a registered extension: nothing of it runs before the player has confirmed "
                       f"the command it starts with. extension_add registers it; the player confirms it on the "
                       f"Extensions page of the WuxianWorkshop App", approval="needed")


def running_one(host, e):
    """the process of a registered extension that runs now (ready, not being stopped), else None; a start of it in
    progress is waited for first"""
    if e is None:
        return None
    with host.lock:
        fut = host.starting.get(e["id"])
    if fut is not None:
        try:
            fut.exception(INIT_TIMEOUT + 10)
        except Exception:                                  # not in that time: there is none that runs
            pass
    with host.lock:
        proc = host.procs.get(e["id"])
        return proc if proc is not None and proc.ready and proc.running and not proc.stopping else None


def check(host, ident):
    """an extension checked without touching its running process: {ok, problems, capabilities, running?, tests?}.
    One in the game only: its manifest, and {where: "game", addons: [{folder, path}]}, its addons' sources for the
    caller to check as addons (nothing of it runs here). Else the
    manifest; a process of its own started, initialized (its description checked) and shut down (not while the
    registered one runs: then its description is the one that one gave, and running is its pid); its tests (a tests
    folder) run with its Python. ApiError needs_approval before anything of it runs that the player has not allowed"""
    e, folder = host.resolve(ident)
    m, problems = M.check_manifest(folder, taken=() if e is not None else host.taken_ids())   # (registered: its id is its)
    caps, described, runs = [], None, None
    if m is not None and not m["run"]:                     # in the game only: no process, no tests of a process
        taken_by_another(host, m, folder, problems)
        return dict(ok=not M.errors_in(problems), problems=problems, capabilities=[], where="game",
                    addons=[dict(folder=name, path=str(Path(m["folder"]) / M.ADDONS_DIR / name)) for name in m["addons"]])
    if m is not None:
        gate(host, e, folder)
        runs = running_one(host, e)
    if runs is not None:
        e = next((x for x in host.load()["extensions"] if x["id"] == e["id"]), e)
        problems.append(M.problem("warning", "process", f"{e['id']} is running (pid {runs.pid}), so no second process "
                                                        f"of it was started: its description is the one it gave when "
                                                        f"it started, not checked against its files as they are now",
                                  f"extension_load {e['id']} restarts it and checks it as it is now"))
        if e.get("described") is not None:
            desc, more = M.check_description(e["id"], dict(protocol=M.PROTOCOL, **e["described"]))
            problems += more
            if desc is not None:
                caps, described = [c["id"] for c in desc["capabilities"]], desc
    elif m is not None:
        taken_by_another(host, m, folder, problems)
        out = Output(host.root / "extensions" / m["id"] / "check.log")
        out.write(f"-- {clock()} check")
        proc = None
        try:
            proc = host.spawn(m, out, on_message=host.check_message, on_end=lambda p: None)
            desc, more = host.initialize(proc, m, dict((e or {}).get("options") or {}))
            proc.ready = True                              # it is asked shutdown, not just ended
            problems += more
            caps, described = [c["id"] for c in desc["capabilities"]], desc
        except Down as d:
            problems += d.problems
            lines = tail(out.path)
            if lines:
                problems.append(M.problem("error", "output", "the end of its output:\n" + "\n".join(lines),
                                          "a traceback says what went wrong"))
        except ApiError as a:
            if a.code == "requires_unmet":                 # (Host.require: this workshop is older than it needs)
                problems.append(M.problem("error", f"{M.MANIFEST}: requires", a.message, ""))
            else:
                problems.append(M.problem("error", f"{M.MANIFEST}: run", a.message,
                                          "run: the command that starts it, relative to the extension folder"))
        finally:
            if proc is not None:
                proc.shutdown()                            # (process.SHUTDOWN_WAIT as it is now)
            out.close()
    if m is not None:
        problems += M.adds_problems(m, described if described is not None else (e or {}).get("described"))
    result = dict(ok=False, problems=problems, capabilities=caps)
    if runs is not None:
        result["running"] = runs.pid
    if m is not None:
        tests = host.run_tests(m)
        if tests is not None:
            result["tests"] = tests
            if tests["failed"]:
                problems.append(M.problem("error", "tests", f"{tests['failed']} of {tests['ran']} tests failed"
                                          if tests["ran"] else "the tests did not run",
                                          "tests.output says which and why"))
    result["ok"] = not M.errors_in(problems)
    return result


def taken_by_another(host, m, folder, problems):
    """(check) the error of a manifest whose id another registered folder has"""
    other = next((x for x in host.load()["extensions"] if x["id"] == m["id"]
                  and os.path.normcase(x["path"]) != os.path.normcase(str(folder))), None)
    if other is not None:
        problems.append(M.problem("error", f"{M.MANIFEST}: id", f"the id {m['id']} is taken by {other['path']}",
                                  "give this one another id"))


def check_message(proc, msg):
    """what an extension asks or tells during a check: its events are dropped, its calls refused"""
    if "id" in msg:
        proc.respond(msg["id"], error=R.error("failed", "no calls during a check"))


def run_tests(host, m):
    """{ran, failed, output} of `python -m unittest discover -s tests` in the extension's folder, with its Python;
    None when it has no tests folder or is no Python extension. The tests get a data folder of their own
    (WUXIAN_EXTENSION_DATA: an empty temporary one, gone afterwards), never the one the extension keeps things in"""
    folder = Path(m["folder"])
    if not m["run"] or not (folder / "tests").is_dir():
        return None
    python = m["python"] if m["python"] else (host.interpreter(m) if m["run"][0].strip().lower() == "python" else None)
    if not python:
        return None
    kw = dict(creationflags=CREATE_NO_WINDOW) if WINDOWS else {}
    try:
        with tempfile.TemporaryDirectory(prefix="wuxian-ext-data-", ignore_cleanup_errors=True) as data:
            env = dict(host.environment(m), **{DATA_ENV: data})
            done = subprocess.run([python, "-m", "unittest", "discover", "-s", "tests"], cwd=str(folder),
                                  env=env, stdin=subprocess.DEVNULL, capture_output=True,
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
