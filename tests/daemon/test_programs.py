"""The program loader (programs.py) on its own, with real processes: a small Python script run by this interpreter
stands for a program. Manifests and their errors, the registry (ids from the manifests, a taken id refused), starting
(the environment, the command, output.log and output.1.log), exit codes, stopping a process tree, taking back what
runs after a daemon restart (and not a reused pid), reading the output by line, the end of the daemon."""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop import programs as P
from wuxianworkshop.daemon.api import ApiError

SCRIPT = r'''
import json, os, subprocess, sys, time
mode = sys.argv[1] if len(sys.argv) > 1 else "hello"
if mode == "hello":
    print("hello")
    print("世界")
    sys.stderr.write("an error line\n")
    sys.stdout.write("no newline at the end")
    sys.exit(3)
elif mode == "env":
    names = ("WUXIAN_PROGRAM", "WUXIAN_PROGRAM_MANIFEST", "WUXIAN_HOME", "PYTHONUNBUFFERED", "PYTHONIOENCODING", "EXTRA")
    print(json.dumps({k: os.environ.get(k) for k in names}))
    print(json.dumps({"cwd": os.getcwd()}))
elif mode == "sleep":
    print("sleeping", *sys.argv[2:], flush=True)
    time.sleep(120)
elif mode == "tree":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print("child", child.pid, flush=True)
    time.sleep(120)
elif mode == "lines":
    for i in range(1, int(sys.argv[2]) + 1):
        print(f"line {i}")
elif mode == "partial":
    sys.stdout.write("half a line")
    sys.stdout.flush()
    time.sleep(120)
'''


def until(test, seconds=15.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return True
        time.sleep(0.05)
    return False


def gone(pid, seconds=10.0):
    """whether process pid has ended (within seconds)"""
    h = P.open_process(pid)
    if h is None:
        return True
    try:
        return P.has_ended(h, int(seconds * 1000))
    finally:
        P.close_process(h)


def write_program(folder, pid, mode="hello", *more, **fields):
    """a program folder: prog.py and its manifest (run: this interpreter, prog.py, mode)"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "prog.py").write_text(SCRIPT, encoding="utf-8")
    manifest = dict(id=pid, run=[sys.executable, "prog.py", mode, *more], **fields)
    (folder / P.MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return folder


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(self.root / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.state = self.root / "home" / "state"
        self.notes = []
        self.loader = self.make()

    def make(self):
        loader = P.Programs(self.state, note=self.notes.append)
        self.addCleanup(loader.close)                # whatever a test leaves running ends
        return loader

    def program(self, name, mode="hello", *more, pid=None, **fields):
        return write_program(self.root / name, pid or name, mode, *more, **fields)

    def ended(self, ident, loader=None):
        loader = loader or self.loader
        self.assertTrue(until(lambda: loader.get(ident)["state"] != "running"), loader.get(ident))
        return loader.get(ident)

    def texts(self, ident, since=0, limit=200, loader=None):
        return [line["text"] for line in (loader or self.loader).logs(ident, since, limit)["lines"]]

    def stop_recording(self, loader):
        """this loader stands for a daemon that is gone: how its processes end is not recorded (run.json). Before a
        start: its waiting thread takes the method as it starts"""
        loader.ended = lambda run, code: run.done.set()


class Manifests(Base):
    def write(self, data, raw=None):
        folder = self.root / "m"
        folder.mkdir(exist_ok=True)
        file = folder / P.MANIFEST
        file.write_text(raw if raw is not None else json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return file

    def bad(self, data, words, raw=None):
        with self.assertRaises(ApiError) as cm:
            P.read_manifest(self.write(data, raw))
        self.assertEqual(cm.exception.code, "bad_manifest")
        self.assertIn(words, cm.exception.message)

    def test_a_good_one_with_its_defaults(self):
        m = P.read_manifest(self.write({"id": "demo_1", "run": ["python", "main.py"]}))
        self.assertEqual(m, dict(id="demo_1", title="demo_1", run=["python", "main.py"], cwd=".", env={}, app=None))
        m = P.read_manifest(self.write(None, raw='\ufeff{"id": "x", "title": " 陪玩 ", "run": ["a"], "env": {"A": "1"}, '
                                                '"app": {"hear": ["game:*"]}}'))
        self.assertEqual((m["title"], m["env"], m["app"]), ("陪玩", {"A": "1"}, {"hear": ["game:*"]}))

    def test_what_is_wrong_is_said(self):
        self.bad(None, "JSON", raw="{nope")
        self.bad([1], "JSON")
        self.bad({"run": ["a"]}, "id")
        self.bad({"id": "Bad", "run": ["a"]}, "id")
        self.bad({"id": "1abc", "run": ["a"]}, "id")
        self.bad({"id": "a" * 25, "run": ["a"]}, "id")
        self.bad({"id": "x", "run": "python main.py"}, "run")
        self.bad({"id": "x", "run": []}, "run")
        self.bad({"id": "x", "run": ["python", 1]}, "run")
        self.bad({"id": "x", "title": "长" * 17, "run": ["a"]}, "title")
        self.bad({"id": "x", "run": ["a"], "env": {"A": 1}}, "env")
        self.bad({"id": "x", "run": ["a"], "app": ["game:*"]}, "app")
        self.bad({"id": "x", "run": ["a"], "app": {"title": "t", "driver": "prompt"}}, "driver")
        with self.assertRaises(ApiError) as cm:
            P.read_manifest(self.root / "none" / P.MANIFEST)
        self.assertEqual(cm.exception.code, "no_manifest")

    def test_the_declaration_of_its_app(self):
        m = P.read_manifest(self.write({"id": "talk", "title": "解说", "run": ["a"], "app": {"hear": ["game:*"], "me": True}}))
        self.assertEqual(P.declaration(m), {"hear": ["game:*"], "me": True, "app": "talk", "title": "解说",
                                            "driver": "program", "launch": True})
        self.assertFalse(P.declaration(m, launch=False)["launch"])


class Registry(Base):
    def test_add_list_and_remove(self):
        folder = self.program("demo", app={"hear": []})
        row = self.loader.add(str(folder))
        self.assertEqual({k: row[k] for k in ("id", "title", "path", "app", "state", "pid", "exit_code", "stopped",
                                              "error")},
                         dict(id="demo", title="demo", path=str(folder), app=True, state="stopped", pid=None,
                              exit_code=None, stopped=False, error=None))
        self.assertEqual(row["manifest"], str(folder / P.MANIFEST))
        self.assertEqual(json.loads((self.state / "programs.json").read_text(encoding="utf-8")),
                         {"programs": [{"path": str(folder)}]})
        self.assertEqual(self.loader.add(str(folder / P.MANIFEST))["id"], "demo")         # the same folder: no second
        self.assertEqual(len(self.loader.rows()), 1)
        self.assertEqual(self.notes, [f"program demo: registered ({folder})"])
        self.assertEqual(self.loader.remove("demo"), dict(removed=True, id="demo", path=str(folder)))
        self.assertEqual(self.loader.rows(), [])
        self.assertTrue((folder / P.MANIFEST).is_file())                                   # its files stay
        with self.assertRaises(ApiError) as cm:
            self.loader.remove("demo")
        self.assertEqual((cm.exception.status, cm.exception.code), (404, "not_found"))

    def test_what_add_refuses(self):
        for path, code in (("relative/x", "bad_request"), ("", "bad_request"), (str(self.root / "none"), "no_manifest")):
            with self.assertRaises(ApiError) as cm:
                self.loader.add(path)
            self.assertEqual(cm.exception.code, code, path)
        self.loader.add(str(self.program("one", pid="same")))
        with self.assertRaises(ApiError) as cm:
            self.loader.add(str(self.program("two", pid="same")))
        self.assertEqual((cm.exception.status, cm.exception.code), (409, "program_exists"))
        self.assertIn(str(self.root / "one"), cm.exception.message)

    def test_an_id_taken_later_and_a_broken_manifest(self):
        """the ids are the manifests' each time: of two that say the same, the later one is refused; a manifest that
        cannot be read leaves a row with its error, under an id made of the folder's name"""
        a, b = self.program("alpha"), self.program("beta")
        self.loader.add(str(a))
        self.loader.add(str(b))
        self.program("beta", pid="alpha")                     # beta's manifest now says alpha
        rows = self.loader.rows()
        self.assertEqual([(r["id"], r["error"] is None) for r in rows], [("alpha", True), ("alpha", False)])
        self.assertIn(str(a), rows[1]["error"])
        self.assertEqual(self.loader.get("alpha")["path"], str(a))          # the id names the valid one
        with self.assertRaises(ApiError) as cm:
            self.loader.start(str(b))                         # the other by its folder
        self.assertEqual((cm.exception.status, cm.exception.code), (409, "program_exists"))
        (b / P.MANIFEST).write_text("{broken", encoding="utf-8")
        row = self.loader.rows()[1]
        self.assertEqual((row["id"], row["title"], row["app"], row["state"]), ("beta", "beta", False, "stopped"))
        self.assertIn("JSON", row["error"])
        with self.assertRaises(ApiError) as cm:
            self.loader.start("beta")
        self.assertEqual(cm.exception.code, "bad_manifest")
        self.assertEqual(self.loader.remove("beta")["path"], str(b))
        self.assertEqual(P.fallback_id(Path("C:/x/My Program-2")), "my_program_2")
        self.assertEqual(P.fallback_id(Path("C:/x/2go")), "p_2go")

    def test_declarations(self):
        self.loader.add(str(self.program("plain")))
        self.loader.add(str(self.program("talk", title="解说", app={"hear": ["chat:guild"]})))
        self.assertEqual(self.loader.declarations(), [("talk", {"hear": ["chat:guild"], "app": "talk", "title": "解说",
                                                                "driver": "program", "launch": True})])

    def test_changes_are_told(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        told = []
        stop = self.loader.subscribe(loop, lambda: told.append(1))
        self.loader.add(str(self.program("demo")))
        loop.run_until_complete(asyncio.sleep(0.05))
        self.assertEqual(told, [1])
        stop()
        self.loader.remove("demo")
        loop.run_until_complete(asyncio.sleep(0.05))
        self.assertEqual(told, [1])


class Running(Base):
    def test_output_and_exit_code(self):
        self.loader.add(str(self.program("demo")))
        row = self.loader.start("demo")
        self.assertEqual((row["state"], row["command"][0], row["command"][1:]),
                         ("running", os.path.abspath(sys.executable), ["prog.py", "hello"]))
        self.assertIsInstance(row["pid"], int)
        row = self.ended("demo")
        self.assertEqual((row["state"], row["exit_code"], row["stopped"], row["pid"]), ("exited", 3, False, None))
        self.assertGreaterEqual(row["exited_at"], row["started"])
        self.assertEqual(sorted(self.texts("demo")), sorted(["hello", "世界", "an error line", "no newline at the end"]))
        self.assertTrue(any(n.startswith("program demo: started, pid ") for n in self.notes), self.notes)
        self.assertTrue(until(lambda: any("program demo: exited with code 3 after" in n for n in self.notes)), self.notes)
        record = json.loads((self.state / "programs" / "demo" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((record["exit_code"], record["ended"], record["path"]), (3, True, row["path"]))

    def test_the_environment_and_cwd(self):
        folder = self.program("envy", "env", cwd="sub", env={"PYTHONIOENCODING": "utf-8:replace", "EXTRA": "x",
                                                            "WUXIAN_PROGRAM": "not this"})
        (folder / "sub").mkdir()
        (folder / "sub" / "prog.py").write_text(SCRIPT, encoding="utf-8")       # run's prog.py is relative to cwd
        self.loader.add(str(folder))
        self.loader.start("envy")
        self.assertEqual(self.ended("envy")["exit_code"], 0)
        lines = self.texts("envy")
        env, cwd = json.loads(lines[0]), json.loads(lines[1])
        self.assertEqual(env, dict(WUXIAN_PROGRAM="envy", WUXIAN_PROGRAM_MANIFEST=str(folder / P.MANIFEST),
                                   WUXIAN_HOME=str(self.root / "home"), PYTHONUNBUFFERED="1",
                                   PYTHONIOENCODING="utf-8:replace", EXTRA="x"))
        self.assertEqual(os.path.normcase(cwd["cwd"]), os.path.normcase(str(folder / "sub")))

    def test_commands_that_are_not_there(self):
        folder = self.program("nope")
        self.loader.add(str(folder))

        def refused(manifest, code):
            (folder / P.MANIFEST).write_text(json.dumps(dict(id="nope", **manifest)), encoding="utf-8")
            with self.assertRaises(ApiError) as cm:
                self.loader.start("nope")
            self.assertEqual(cm.exception.code, code)
            return cm.exception.message

        self.assertIn("PATH", refused({"run": ["no-such-program-xyz", "a"]}, "bad_command"))
        self.assertIn(str(folder), refused({"run": ["bin/missing.exe"]}, "bad_command"))
        with mock.patch.object(shutil, "which", return_value=None):
            self.assertIn("Python 3.10", refused({"run": ["python", "main.py"]}, "bad_command"))
        self.assertIn("gone", refused({"run": [sys.executable], "cwd": "gone"}, "bad_manifest"))
        self.assertEqual(self.loader.get("nope")["state"], "stopped")
        self.assertFalse((self.state / "programs" / "nope" / "run.json").exists())

    def test_once_at_a_time_and_a_new_start_keeps_the_last_output(self):
        self.loader.add(str(self.program("sleepy", "sleep", "first")))
        first = self.loader.start("sleepy")
        with self.assertRaises(ApiError) as cm:
            self.loader.start("sleepy")
        self.assertEqual((cm.exception.status, cm.exception.code), (409, "running"))
        self.assertTrue(until(lambda: self.texts("sleepy") == ["sleeping first"]))
        self.assertTrue(self.loader.kill(self.loader.run_of("sleepy")[1]))
        row = self.loader.get("sleepy")
        self.assertEqual((row["state"], row["stopped"], row["exit_code"]), ("stopped", True, None))   # no failure
        self.assertTrue(gone(first["pid"]))
        self.assertTrue(until(lambda: any("program sleepy: stopped after" in n and "process tree" in n
                                          for n in self.notes)), self.notes)
        self.program("sleepy", "sleep", "second")             # the manifest is read at each start
        second = self.loader.start("sleepy")
        self.assertEqual((second["state"], second["stopped"]), ("running", False))
        self.assertNotEqual(second["pid"], first["pid"])
        self.assertTrue(until(lambda: self.texts("sleepy") == ["sleeping second"]))
        out = self.state / "programs" / "sleepy"
        self.assertEqual((out / "output.1.log").read_text(encoding="utf-8").splitlines(), ["sleeping first"])
        self.assertTrue(self.loader.kill(self.loader.run_of("sleepy")[1]))

    def test_a_process_tree_ends_whole(self):
        self.loader.add(str(self.program("tree", "tree")))
        row = self.loader.start("tree")
        self.assertTrue(until(lambda: any(t.startswith("child ") for t in self.texts("tree"))))
        child = int(next(t for t in self.texts("tree") if t.startswith("child ")).split()[1])
        self.assertFalse(gone(child, 0))
        self.assertTrue(self.loader.kill(self.loader.run_of("tree")[1]))
        self.assertTrue(gone(row["pid"]))
        self.assertTrue(gone(child))

    def test_close_ends_what_runs(self):
        self.loader.add(str(self.program("sleepy", "sleep")))
        pid = self.loader.start("sleepy")["pid"]
        self.loader.close()
        self.assertTrue(gone(pid))
        row = self.loader.get("sleepy")
        self.assertEqual((row["state"], row["stopped"]), ("stopped", True))


class TakenBack(Base):
    def test_a_daemon_restart_takes_back_what_runs(self):
        self.loader.add(str(self.program("sleepy", "sleep")))
        self.stop_recording(self.loader)
        pid = self.loader.start("sleepy")["pid"]
        self.assertTrue(until(lambda: self.texts("sleepy")))
        later = self.make()
        self.assertEqual(later.adopt(), ["sleepy"])
        row = later.get("sleepy")
        self.assertEqual((row["state"], row["pid"]), ("running", pid))
        self.assertEqual(self.texts("sleepy", loader=later), ["sleeping"])
        self.assertIn(f"program sleepy: still running from before (pid {pid}): taken back", self.notes)
        with self.assertRaises(ApiError):
            later.start("sleepy")
        self.assertTrue(later.kill(later.run_of("sleepy")[1]))
        self.assertTrue(gone(pid))
        self.assertEqual((later.get("sleepy")["state"], later.get("sleepy")["stopped"]), ("stopped", True))
        again = self.make()                                  # once more: it ended, as run.json says
        self.assertEqual(again.adopt(), [])
        self.assertEqual((again.get("sleepy")["state"], again.get("sleepy")["stopped"]), ("stopped", True))

    def test_one_that_ended_while_no_daemon_watched(self):
        self.loader.add(str(self.program("demo")))
        self.stop_recording(self.loader)
        self.loader.start("demo")
        e, run = self.loader.run_of("demo")
        self.assertTrue(run.done.wait(15))
        file = self.state / "programs" / "demo" / "run.json"
        record = file.read_text(encoding="utf-8")
        later = self.make()                                  # its process is still there (a handle on it is open)
        self.assertEqual(later.adopt(), [])
        row = later.get("demo")
        self.assertEqual((row["state"], row["exit_code"], row["stopped"]), ("exited", 3, False))
        self.assertTrue(json.loads(file.read_text(encoding="utf-8"))["ended"])
        file.write_text(record, encoding="utf-8")
        with mock.patch.object(P, "open_process", return_value=None):      # gone with its exit code
            again = self.make()
            self.assertEqual(again.adopt(), [])
        row = again.get("demo")
        self.assertEqual((row["state"], row["exit_code"], row["stopped"]), ("exited", None, False))

    @unittest.skipUnless(P.WINDOWS, "the creation time is read on Windows")
    def test_a_reused_pid_is_not_taken_back(self):
        self.loader.add(str(self.program("sleepy", "sleep")))
        self.stop_recording(self.loader)
        pid = self.loader.start("sleepy")["pid"]
        file = self.state / "programs" / "sleepy" / "run.json"
        record = json.loads(file.read_text(encoding="utf-8"))
        self.assertIsInstance(record["created"], int)
        record["created"] += 1                               # another process under that pid
        file.write_text(json.dumps(record), encoding="utf-8")
        later = self.make()
        self.assertEqual(later.adopt(), [])
        self.assertEqual(later.get("sleepy")["state"], "exited")
        P.kill_tree(pid)
        self.assertTrue(gone(pid))

    def test_only_registered_folders(self):
        self.loader.add(str(self.program("sleepy", "sleep")))
        self.stop_recording(self.loader)
        self.loader.start("sleepy")
        self.loader.save_folders([])                         # taken off the registry by hand
        self.assertEqual(self.make().adopt(), [])


class Output(Base):
    def test_by_line_with_a_cursor(self):
        self.loader.add(str(self.program("many", "lines", "450")))
        self.loader.start("many")
        self.ended("many")
        res = self.loader.logs("many")
        self.assertEqual((res["total"], res["next"], res["reset"], res["cut"]), (450, 451, False, True))
        self.assertEqual([res["lines"][0], res["lines"][-1]], [dict(n=251, text="line 251"), dict(n=450, text="line 450")])
        res = self.loader.logs("many", 0, 450)
        self.assertEqual((len(res["lines"]), res["lines"][0]["n"], res["cut"]), (450, 1, False))
        res = self.loader.logs("many", 1, 200)
        self.assertEqual((res["lines"][0]["n"], res["lines"][-1]["n"], res["next"], res["cut"]), (1, 200, 201, False))
        res = self.loader.logs("many", 401, 200)
        self.assertEqual((len(res["lines"]), res["next"]), (50, 451))
        res = self.loader.logs("many", 451)
        self.assertEqual((res["lines"], res["next"]), ([], 451))
        res = self.loader.logs("many", 9999, 5)                # another start's place: from the first line
        self.assertEqual((res["reset"], [l["n"] for l in res["lines"]], res["next"]), (True, [1, 2, 3, 4, 5], 6))
        self.assertEqual(res["row"]["exit_code"], 0)
        for since, limit in ((-1, 10), (0, 0), (0, 2001), ("1", 5), (True, 5)):
            with self.assertRaises(ApiError):
                self.loader.logs("many", since, limit)

    def test_the_line_being_written_comes_once_it_ended(self):
        self.loader.add(str(self.program("half", "partial")))
        out = Path(self.loader.start("half")["output"])
        self.assertTrue(until(lambda: out.stat().st_size == len("half a line")))
        res = self.loader.logs("half")
        self.assertEqual((res["lines"], res["total"]), ([], 0))
        self.loader.kill(self.loader.run_of("half")[1])
        res = self.loader.logs("half")
        self.assertEqual((res["lines"], res["total"]), ([dict(n=1, text="half a line")], 1))

    def test_a_program_that_never_ran(self):
        self.loader.add(str(self.program("idle")))
        res = self.loader.logs("idle")
        self.assertEqual((res["lines"], res["next"], res["total"], res["cut"], res["row"]["state"]),
                         ([], 1, 0, False, "stopped"))

    def test_lines_cut_and_bad_bytes(self):
        path = self.root / "out.log"
        path.write_bytes(b"a\r\n" + b"x" * (P.LINE_MAX + 10) + b"\n\xff\xfe ok\nrest")
        lines = P.Lines(path)
        lines.update()
        self.assertEqual((lines.complete, lines.partial), (3, True))
        texts = lines.read(1, 4)
        self.assertEqual(texts[0], "a")
        self.assertEqual(len(texts[1]), P.LINE_MAX + 1)
        self.assertTrue(texts[2].endswith(" ok"))
        self.assertEqual(texts[3], "rest")
        with open(path, "ab") as f:
            f.write(b" more\n")
        lines.update()
        self.assertEqual((lines.complete, lines.partial, lines.read(4, 4)), (4, False, ["rest more"]))


if __name__ == "__main__":
    unittest.main()
