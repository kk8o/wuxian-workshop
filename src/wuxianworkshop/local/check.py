r"""The checks of a local extension that need more than its files (manifest.py checks those): check(host, ident) starts
a process of its own beside the running one, initializes it (its description checked), shuts it down, and runs the
extension's tests (python -m unittest discover -s tests in its folder, with its Python, TESTS_TIMEOUT seconds); the
answer is {ok, problems, capabilities, tests?}, each problem as manifest.problem gives it. The check's process writes
<state>\extensions\<id>\check.log; its events are dropped and its calls refused.

The functions take the host (host.py Host) for what is the host's: the registry, how a process is started (spawn,
environment, interpreter) and initialize. extension_load (Host.load_) is the same checks around a restart of the
registered one, and uses run_tests and check_description from here. Standard library only.
"""
import os
import re
import subprocess
from pathlib import Path

from ..daemon.api import ApiError
from . import manifest as M
from .logs import Output, clock, tail
from .process import CREATE_NO_WINDOW, WINDOWS
from .registry import check_folder
from .rpc import FAILED, Down

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


def check(host, ident):
    """an extension checked without touching its running process: {ok, problems, capabilities, tests?}. The
    manifest; a process of its own started, initialized (its description checked) and shut down; its tests (a
    tests folder) run with its Python"""
    e, folder = host.resolve(ident)
    m, problems = M.check_manifest(folder)
    caps, described = [], None
    if m is not None:
        other = next((x for x in host.load()["extensions"] if x["id"] == m["id"]
                      and os.path.normcase(x["path"]) != os.path.normcase(str(folder))), None)
        if other is not None:
            problems.append(M.problem("error", f"{M.MANIFEST}: id", f"the id {m['id']} is taken by {other['path']}",
                                      "give this one another id"))
        out = Output(host.root / "extensions" / m["id"] / "check.log")
        out.write(f"-- {clock()} check")
        proc = None
        try:
            proc = host.spawn(m, out, on_message=host.check_message, on_end=lambda p: None)
            desc, more = host.initialize(proc, m, dict((e or {}).get("options") or {}))
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
        tests = host.run_tests(m)
        if tests is not None:
            result["tests"] = tests
            if tests["failed"]:
                problems.append(M.problem("error", "tests", f"{tests['failed']} of {tests['ran']} tests failed"
                                          if tests["ran"] else "the tests did not run",
                                          "tests.output says which and why"))
    result["ok"] = not M.errors_in(problems)
    return result


def check_message(proc, msg):
    """what an extension asks or tells during a check: its events are dropped, its calls refused"""
    if "id" in msg:
        proc.respond(msg["id"], error=dict(code=FAILED, message="no calls during a check", data=dict(reason="failed")))


def run_tests(host, m):
    """{ran, failed, output} of `python -m unittest discover -s tests` in the extension's folder, with its Python;
    None when it has no tests folder or is no Python extension"""
    folder = Path(m["folder"])
    if not (folder / "tests").is_dir():
        return None
    python = m["python"] if m["python"] else (host.interpreter(m) if m["run"][0].strip().lower() == "python" else None)
    if not python:
        return None
    kw = dict(creationflags=CREATE_NO_WINDOW) if WINDOWS else {}
    try:
        done = subprocess.run([python, "-m", "unittest", "discover", "-s", "tests"], cwd=str(folder),
                              env=host.environment(m), stdin=subprocess.DEVNULL, capture_output=True,
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
