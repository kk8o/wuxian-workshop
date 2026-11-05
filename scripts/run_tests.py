r"""Runs the tests in parallel: one test module at a time in each of several worker processes, the slowest first.

    .venv\Scripts\python.exe scripts\run_tests.py                        every module, min(CPUs, 8) workers
    .venv\Scripts\python.exe scripts\run_tests.py -j 4 tests.addon       the modules under tests.addon, 4 workers
    .venv\Scripts\python.exe scripts\run_tests.py tests\core\test_frame.py tests.daemon.test_cli.Cli.test_help
    .venv\Scripts\python.exe scripts\run_tests.py --pixels all           every mock-client frame through the pixels

It runs the same tests as `python -m unittest discover -s tests -t .` (the same discovery: test*.py in packages under
tests/) and counts them the same way. Each worker is `python scripts\run_tests.py --worker`, started in the checkout with
its src/ first on PYTHONPATH, so a checkout other than the one the package was installed from (a worktree) tests its own
code; a worker that imports wuxianworkshop from elsewhere stops the run. Each worker gets its own WUXIAN_HOME
(tests/__init__.py makes one per process, so the variable is not passed on). Workers write their output (prints, logs of
the servers the tests start) to a file each; the run keeps the files when something failed and names them.

The order comes from the time each module took the last time (build\test-times.json, written after every run that ran
the whole suite or part of it), the size of the module's file when it has no time yet. The exit status is 0 when every
test passed (unittest's wasSuccessful), 1 when one did not, 2 when a worker died.

Worker protocol: one module (or test name) per line on stdin; one JSON line per name on the original stdout:
{"name", "run", "seconds", "failures": [[test, traceback]], "errors": [...], "skipped": [[test, reason]],
"expected_failures", "unexpected_successes": [test]}; an empty line or EOF ends the worker.
"""
import argparse
import itertools
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMES = ROOT / "build" / "test-times.json"


# ---- worker side

def _worker():
    """run the names read from stdin, one at a time, answering on the original stdout; test output goes to the log"""
    sys.path[0] = str(ROOT)                          # as discover -t . has it: the checkout, not scripts/
    import unittest
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    commands = os.fdopen(os.dup(0), "r", encoding="utf-8")
    log = open(os.environ.get("WUXIAN_TEST_LOG") or os.devnull, "ab")
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(log.fileno(), 1)                         # prints, and processes the tests start, write to the log ...
    os.dup2(log.fileno(), 2)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)                                 # ... and nothing reads the commands meant for the worker
    import wuxianworkshop
    proto.write(json.dumps(dict(package=wuxianworkshop.__file__)) + "\n")
    for line in commands:
        name = line.strip()
        if not name:
            break
        result = unittest.TestResult()
        start = time.perf_counter()
        try:
            suite = unittest.defaultTestLoader.loadTestsFromName(name)
            suite.run(result)
        except BaseException:                         # a module that does not import is an error of its own
            result.errors.append((name, traceback.format_exc()))
        seconds = time.perf_counter() - start
        sys.stdout.flush()
        sys.stderr.flush()
        proto.write(json.dumps(dict(
            name=name, run=result.testsRun, seconds=seconds,
            failures=[[_id(t), tb] for t, tb in result.failures], errors=[[_id(t), tb] for t, tb in result.errors],
            skipped=[[_id(t), why] for t, why in result.skipped], expected_failures=len(result.expectedFailures),
            unexpected_successes=[_id(t) for t in result.unexpectedSuccesses])) + "\n")
    return 0


def _id(test):
    return test if isinstance(test, str) else test.id()


# ---- what to run

def modules(root=ROOT):
    """the dotted names of the test modules `unittest discover -s tests -t .` finds: test*.py in packages"""
    found = []

    def walk(folder, dotted):
        for p in sorted(folder.iterdir()):
            if p.is_dir() and (p / "__init__.py").is_file():
                walk(p, f"{dotted}.{p.name}")
            elif p.is_file() and p.suffix == ".py" and p.name.startswith("test") and p.stem.isidentifier():
                found.append(f"{dotted}.{p.stem}")

    walk(root / "tests", "tests")
    return found


def select(names, wanted):
    """the modules a name picks: a package or module (dotted, or a path), or a test class or method run as it is"""
    if not wanted:
        return list(names)
    out = []
    for w in wanted:
        p = Path(w)
        if w.endswith(".py") or os.sep in w or "/" in w:
            p = (Path.cwd() / p).resolve()
            try:
                w = ".".join(p.relative_to(ROOT).with_suffix("").parts) if p.suffix == ".py" else ".".join(p.relative_to(ROOT).parts)
            except ValueError:
                raise SystemExit(f"not in {ROOT}: {p}")
        hits = [n for n in names if n == w or n.startswith(w + ".")]
        if not hits and any(w.startswith(n + ".") for n in names):
            hits = [w]                                # a class or a test inside a module
        if not hits:
            raise SystemExit(f"no test module matches {w!r}")
        out += [h for h in hits if h not in out]
    return out


def estimates(names, times):
    """the seconds each name is expected to take: last time's, else the module file's size at about 25 kB a second"""
    out = {}
    for n in names:
        if n in times:
            out[n] = times[n]
        else:
            f = ROOT.joinpath(*n.split(".")).with_suffix(".py")
            out[n] = f.stat().st_size / 25000 if f.is_file() else 1.0
    return out


def read_times():
    try:
        return {k: float(v) for k, v in json.loads(TIMES.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def write_times(times):
    try:
        TIMES.parent.mkdir(exist_ok=True)
        TIMES.write_text(json.dumps(dict(sorted(times.items())), indent=1), encoding="utf-8")
    except OSError:
        pass


# ---- parent side

class Worker:
    """one worker process and the thread that reads its answers into the shared queue"""

    def __init__(self, index, env, logs, answers):
        self.index, self.log = index, logs / f"worker-{index}.log"
        env = dict(env, WUXIAN_TEST_LOG=str(self.log))
        self.proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker"], cwd=ROOT, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        self.name = None
        self.thread = threading.Thread(target=self._read, args=(answers,), daemon=True)
        self.thread.start()

    def _read(self, answers):
        for line in self.proc.stdout:
            answers.put((self, json.loads(line)))
        answers.put((self, None))                     # it ended

    def send(self, name):
        self.name = name
        self.proc.stdin.write(name + "\n")
        self.proc.stdin.flush()

    def stop(self):
        try:
            self.proc.stdin.write("\n")
            self.proc.stdin.close()
        except OSError:
            pass

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()


def run(names, jobs, pixels=None, quiet=False):
    """run the names on `jobs` workers; returns (exit status, totals dict)"""
    times, known = read_times(), set(modules())
    guess = estimates(names, times)
    todo = sorted(names, key=lambda n: -guess[n])
    env = {k: v for k, v in os.environ.items() if k != "WUXIAN_HOME"}
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.setdefault("WUXIAN_HEADLESS", "1")
    env["PYTHONIOENCODING"] = "utf-8"
    if pixels:
        env["WUXIAN_MOCK_PIXELS"] = pixels
    logs = Path(tempfile.mkdtemp(prefix="wuxian-test-logs-"))
    answers = queue.Queue()
    jobs = max(1, min(jobs, len(todo)))
    numbers = itertools.count(1)
    workers = [Worker(next(numbers), env, logs, answers) for _ in range(jobs)]
    results, broken, done, start = [], [], 0, time.perf_counter()
    try:
        while workers:
            w, msg = answers.get()
            if msg is None:                           # the worker ended: a crash if it still had a name
                if w.name is not None:
                    broken.append((w.name, w.log))
                    results.append(dict(name=w.name, run=0, seconds=0.0, failures=[], skipped=[], expected_failures=0,
                                        unexpected_successes=[], errors=[[w.name, f"the worker died (exit {w.proc.wait()}); "
                                                                                  f"its output: {w.log}"]]))
                    done += 1
                    if todo:                          # carry on with a new worker
                        workers.append(Worker(next(numbers), env, logs, answers))
                workers.remove(w)
                continue
            if "package" in msg:
                src = (ROOT / "src").resolve()
                if not Path(msg["package"]).resolve().is_relative_to(src):
                    for x in workers:
                        x.kill()
                    raise SystemExit(f"the workers import wuxianworkshop from {msg['package']}, not from {src}")
            else:
                results.append(msg)
                if msg["name"] in known:              # a class or a single test says nothing of its module
                    times[msg["name"]] = round(msg["seconds"], 2)
                done += 1
                if not quiet:
                    bad = len(msg["failures"]) + len(msg["errors"]) + len(msg["unexpected_successes"])
                    print(f"[{done}/{len(names)}] {msg['name']}: {msg['run']} tests, {msg['seconds']:.1f} s"
                          + (f", {bad} FAILED" if bad else ""), flush=True)
            w.name = None
            if todo:
                w.send(todo.pop(0))
            else:
                w.stop()
    except KeyboardInterrupt:
        for x in workers:
            x.kill()
        raise
    wall = time.perf_counter() - start
    write_times(times)
    totals = dict(run=sum(r["run"] for r in results), wall=wall, seconds=sum(r["seconds"] for r in results),
                  failures=[f for r in results for f in r["failures"]], errors=[e for r in results for e in r["errors"]],
                  skipped=sum(len(r["skipped"]) for r in results),
                  expected_failures=sum(r["expected_failures"] for r in results),
                  unexpected_successes=[u for r in results for u in r["unexpected_successes"]])
    ok = not (totals["failures"] or totals["errors"] or totals["unexpected_successes"])
    for kind in ("errors", "failures"):
        for test, tb in totals[kind]:
            print("=" * 70 + f"\n{kind[:-1].upper()}: {test}\n" + "-" * 70 + f"\n{tb}")
    for test in totals["unexpected_successes"]:
        print(f"UNEXPECTED SUCCESS: {test}")
    print("-" * 70)
    print(f"Ran {totals['run']} tests in {wall:.1f} s on {jobs} workers ({totals['seconds']:.1f} s of module time)")
    extra = [f"{k}={v}" for k, v in (("failures", len(totals["failures"])), ("errors", len(totals["errors"])),
                                      ("skipped", totals["skipped"]), ("expected failures", totals["expected_failures"]),
                                      ("unexpected successes", len(totals["unexpected_successes"]))) if v]
    print(("OK" if ok else "FAILED") + (f" ({', '.join(extra)})" if extra else ""))
    if ok and not broken:
        _rmtree(logs)
    else:
        print(f"worker output: {logs}")
    return (2 if broken else 0 if ok else 1), totals


def _rmtree(folder):
    import shutil
    shutil.rmtree(folder, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the tests in parallel worker processes.")
    ap.add_argument("names", nargs="*", help="test packages, modules (dotted or paths), classes or tests; default: all")
    ap.add_argument("-j", "--jobs", type=int, default=min(os.cpu_count() or 1, 8), help="worker processes (default: %(default)s)")
    ap.add_argument("--pixels", metavar="all|N", help="WUXIAN_MOCK_PIXELS for the workers: every mock-client frame "
                                                      "through the pixels (all), or every N-th")
    ap.add_argument("-q", "--quiet", action="store_true", help="no line per module")
    ap.add_argument("--list", action="store_true", help="print the modules it would run, slowest first, and stop")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.worker:
        return _worker()
    names = select(modules(), args.names)
    if args.list:
        guess = estimates(names, read_times())
        for n in sorted(names, key=lambda n: -guess[n]):
            print(f"{guess[n]:7.1f}  {n}")
        return 0
    return run(names, args.jobs, pixels=args.pixels, quiet=args.quiet)[0]


if __name__ == "__main__":
    sys.exit(main())
