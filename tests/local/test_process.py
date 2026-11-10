"""One process of a local extension (process.py), with real processes of the fixture: what its environment has, a pipe
it does not read, helpers that hold its pipes and the job object that ends them, bytes that are not UTF-8, lines that are
too long, answers nobody asked for."""
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import host as H
from wuxianworkshop.local import process as P
from wuxianworkshop.local import rpc as R
from wuxianworkshop.paths import home

from .util import HostCase, gone, until

SRC = Path(P.__file__).resolve().parents[2]

# a daemon of its own for test_the_end_of_the_daemon: a child in a job, which starts a child itself once it is told to
DAEMON = '''\
import subprocess, sys, time
from wuxianworkshop.local import process as P
CHILD = ("import subprocess, sys, time; sys.stdin.readline(); "
         "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
         "print(p.pid, flush=True); time.sleep(120)")
child = subprocess.Popen([sys.executable, "-c", CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         creationflags=P.CREATE_NO_WINDOW | P.CREATE_NEW_PROCESS_GROUP)
job = P.job_of(child)
child.stdin.write(b"go\\n")
child.stdin.flush()
print(child.pid, int(child.stdout.readline()), int(bool(job)), flush=True)
time.sleep(120)
'''


class Environment(unittest.TestCase):
    def names(self, env, name):
        return [k for k in env if k.upper() == name]

    def test_environment(self):
        daemon = {"SECRET_KEY_OF_MINE": "s3cret", "WUXIAN_TOKEN": "t0ken", "WUXIAN_HOME": "C:/wx-home",
                  "WUXIAN_MUTEX": "mx", "WUXIAN_PORT": "1", "PYTHONSTARTUP": "C:/startup.py",
                  "PYTHONPATH": "C:/daemon", "PYTHONHOME": "C:/py", "MY_TOOL_HOME": "C:/tool"}
        with mock.patch.dict(os.environ, daemon):
            env = P.environment({"id": "fx", "env": {}}, Path("C:/sdk"))
            for name in ("SECRET_KEY_OF_MINE", "WUXIAN_TOKEN", "WUXIAN_PORT", "PYTHONSTARTUP", "PYTHONHOME",
                         "MY_TOOL_HOME"):
                self.assertEqual(self.names(env, name), [], name)      # none of the daemon's own gets through
            self.assertEqual((env["WUXIAN_EXTENSION"], env["WUXIAN_HOME"], env["WUXIAN_MUTEX"]),
                             ("fx", str(home()), "mx"))
            self.assertEqual((env["PYTHONPATH"], env["PYTHONUNBUFFERED"], env["PYTHONIOENCODING"]),
                             (str(Path("C:/sdk")), "1", "utf-8"))      # not the daemon's PYTHONPATH
            self.assertEqual({k for k in env if k.upper().startswith(("WUXIAN_", "PYTHON"))},
                             {"WUXIAN_EXTENSION", "WUXIAN_EXTENSION_DATA", "WUXIAN_HOME", "WUXIAN_MUTEX", "PYTHONPATH",
                              "PYTHONUNBUFFERED", "PYTHONIOENCODING"})
            # its data folder: in the state folder (the daemon's, or the one given), by its id
            self.assertEqual(env["WUXIAN_EXTENSION_DATA"], str(home() / "state" / "extensions" / "fx" / "data"))
            self.assertEqual(P.environment({"id": "fx", "env": {}}, Path("C:/sdk"), Path("C:/st"))[P.DATA_ENV],
                             str(Path("C:/st") / "extensions" / "fx" / "data"))
            self.assertEqual(P.data_folder("fx", "C:/st"), Path("C:/st") / "extensions" / "fx" / "data")
            for name in ("PATH", "SYSTEMROOT"):                        # what a process needs to run at all
                if name in os.environ:
                    self.assertEqual([env[k] for k in self.names(env, name)], [os.environ[name]], name)
        # who the player is: without it getpass.getuser() goes on to `import pwd`, which Windows has not
        who = "USERNAME" if P.WINDOWS else "LOGNAME"
        with mock.patch.dict(os.environ, {who: "player", "PROGRAMFILES(X86)": "C:/pf86"}):
            env = P.environment({"id": "fx", "env": {}}, Path("C:/sdk"))
            self.assertEqual([env[k] for k in self.names(env, who)], ["player"])
            self.assertEqual([env[k] for k in self.names(env, "PROGRAMFILES(X86)")], ["C:/pf86"] if P.WINDOWS else [])
        with mock.patch.dict(os.environ, daemon):
            # what the manifest's pass names (whatever its case on Windows); a name that is not set is none
            passed = "my_tool_home" if P.WINDOWS else "MY_TOOL_HOME"
            env = P.environment({"id": "fx", "env": {}, "pass": [passed, "PYTHONPATH", "NOT_SET", 7]}, Path("C:/sdk"))
            self.assertEqual([env[k] for k in self.names(env, "MY_TOOL_HOME")], ["C:/tool"])
            self.assertEqual(env["PYTHONPATH"], str(Path("C:/sdk")) + os.pathsep + "C:/daemon")
            self.assertEqual(self.names(env, "NOT_SET") + self.names(env, "SECRET_KEY_OF_MINE"), [])
            # the manifest's env: over what was kept and over the two PYTHON* defaults, under what the workshop gives
            env = P.environment({"id": "fx", "env": {"PYTHONPATH": "C:/mine", "PYTHONUNBUFFERED": "0",
                                                     "WUXIAN_EXTENSION": "other", "WUXIAN_HOME": "C:/else",
                                                     "WUXIAN_MUTEX": "theirs", "FIXTURE_MODE": "say",
                                                     "PATH": "C:/only"}}, Path("C:/sdk"))
            self.assertEqual((env["PYTHONPATH"], env["PYTHONUNBUFFERED"], env["FIXTURE_MODE"]),
                             (str(Path("C:/sdk")) + os.pathsep + "C:/mine", "0", "say"))
            self.assertEqual((env["WUXIAN_EXTENSION"], env["WUXIAN_HOME"], env["WUXIAN_MUTEX"]),
                             ("fx", str(home()), "mx"))
            self.assertEqual([env[k] for k in self.names(env, "PATH")], ["C:/only"])     # one of each name
            env = P.environment({"id": "fx", "env": {"WUXIAN_EXTENSION_DATA": "C:/else"}}, Path("C:/sdk"))
            self.assertEqual(env["WUXIAN_EXTENSION_DATA"], str(home() / "state" / "extensions" / "fx" / "data"))
        with mock.patch.dict(os.environ):
            os.environ.pop("WUXIAN_MUTEX", None)
            env = P.environment({"id": "fx", "env": {}}, Path("C:/sdk"))
            self.assertNotIn("WUXIAN_MUTEX", env)                      # the daemon has none: none is given


class Processes(HostCase):
    def test_what_a_process_gets(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY_OF_MINE": "s3cret", "WUXIAN_TOKEN": "t0ken",
                                          "PYTHONSTARTUP": "", "MY_TOOL_HOME": "C:/tool"}):
            self.add(extra="caps")
            env = self.host.call_sync("fx.env")
        for name in ("SECRET_KEY_OF_MINE", "WUXIAN_TOKEN", "PYTHONSTARTUP", "MY_TOOL_HOME"):
            self.assertNotIn(name, env)
        self.assertNotIn("s3cret", env.values())
        self.assertNotIn("t0ken", env.values())
        self.assertEqual((env["WUXIAN_EXTENSION"], env["WUXIAN_HOME"], env["PYTHONPATH"], env["PYTHONIOENCODING"]),
                         ("fx", str(self.root / "home"), str(self.root / "sdk"), "utf-8"))
        self.assertEqual((env["FIXTURE_MODE"], env["FIXTURE_EXTRA"]), ("", "caps"))      # the manifest's env
        self.assertLessEqual({k for k in env if k.upper().startswith("WUXIAN_")},
                             {"WUXIAN_EXTENSION", "WUXIAN_EXTENSION_DATA", "WUXIAN_HOME", "WUXIAN_MUTEX"})
        data = self.root / "state" / "extensions" / "fx" / "data"      # its data folder: there before it started
        self.assertEqual(env["WUXIAN_EXTENSION_DATA"], str(data))
        self.assertTrue(data.is_dir())
        self.assertEqual({k for k in env if k.upper().startswith("PYTHON")},
                         {"PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONIOENCODING"})

    def test_one_that_does_not_read_its_input(self):
        self.add(extra="caps")
        proc = self.host.ensure("fx").result(30)
        self.assertEqual(self.host.call_sync("fx.hang"), {"deaf": True})
        with mock.patch.object(P, "WRITE_WAIT", 1.0), mock.patch.object(P, "WATCH", 0.1), \
                mock.patch.object(H, "SHUTDOWN_WAIT", 0.5):
            t0 = time.monotonic()
            first = self.host.call(proc, "echo", {"text": "a"}, None)                # its pipe still takes this one
            stuck = self.host.call(proc, "echo", {"text": "x" * 4_000_000}, None)    # not this one
            waits = self.host.call(proc, "echo", {"text": "b"}, None)                # behind it
            self.assertTrue(until(lambda: proc.writing is not None and proc.writing[1] == stuck.rid, 5))
            taken = [proc.notify("options", {"options": {}}) for _ in range(300)]
            self.assertFalse(taken[-1])                                # the queue is full: refused, not waited for
            self.assertTrue(250 <= sum(taken) <= P.WRITE_QUEUE, sum(taken))
            full = self.host.call(proc, "echo", {"text": "c"}, None)
            self.assertTrue(full.done())
            err = full.exception()
            self.assertEqual((err.status, err.code), (429, "busy"))
            self.assertIn("fx is busy: 256 lines wait to be written to it", err.message)
            self.assertEqual(self.host.get("fx")["state"], "running")  # the host's lock waits for no pipe
            self.assertLess(time.monotonic() - t0, 0.9)                # none of that waited for the pipe
            for fut in (stuck, waits):                                 # about a second after they were asked
                err = fut.exception(5)
                self.assertEqual((err.status, err.code), (429, "busy"))
                self.assertIn("its request could not be written within 1 s", err.message)
            self.assertLess(time.monotonic() - t0, 4)
            self.assertTrue(until(lambda: proc.stuck, 3))
            late = self.host.call(proc, "echo", {"text": "d"}, None)   # refused at once from now on
            self.assertTrue(late.done())
            self.assertIn("fx is busy: it has not read its input for 1 s", late.exception().message)
            self.assertTrue(until(lambda: any("it has not read its input for 1 s: calls of it are refused (busy)"
                                              in line for line in self.output()), 3))
            self.assertFalse(first.done())                             # written, and never answered
            # a stop does not wait for the pipe either: shutdown cannot be asked, so its tree is ended
            t0 = time.monotonic()
            row = self.host.stop("fx")
            self.assertLess(time.monotonic() - t0, 6)
        self.assertEqual((row["state"], row["pid"]), ("stopped", None))
        self.assertEqual(first.exception(2).code, "down")
        self.assertTrue(gone(proc.pid))
        self.assertTrue(proc.wclosed)

    def test_a_helper_that_holds_its_pipes(self):
        self.add(extra="caps")
        helper = self.host.call_sync("fx.helper")["pid"]
        proc = self.host.procs["fx"]
        self.assertIsNotNone(proc.job)
        self.assertFalse(any("no job object" in line for line in self.output()))
        with self.assertRaises(ApiError) as cm:
            self.host.call_sync("fx.crash")
        self.assertEqual(cm.exception.code, "down")
        self.assertTrue(until(lambda: self.host.get("fx")["state"] == "exited", 10))     # not kept running by it
        row = self.host.get("fx")
        self.assertEqual((row["exit_code"], row["pid"]), (3, None))
        self.assertTrue(gone(helper))                                  # what it left behind ended with it
        self.assertTrue(gone(proc.pid))
        self.assertTrue(proc.finished.wait(5))
        self.assertTrue(proc.kill())                                   # after its end: nothing to wait for
        self.assertIsNone(proc.job)
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "a"})["text"], "a")
        # a stop ends the helper too
        helper = self.host.call_sync("fx.helper")["pid"]
        pid = self.host.get("fx")["pid"]
        t0 = time.monotonic()
        row = self.host.stop("fx")
        self.assertLess(time.monotonic() - t0, 8)
        self.assertEqual((row["state"], row["exit_code"]), ("stopped", 0))
        self.assertTrue(gone(helper))
        self.assertTrue(gone(pid))

    @unittest.skipUnless(P.WINDOWS, "job objects are Windows'")
    def test_without_a_job(self):
        helpers = []
        self.add(extra="caps")
        try:
            with mock.patch.object(P, "job_of", return_value=None):
                helpers.append(self.host.call_sync("fx.helper")["pid"])
                proc = self.host.procs["fx"]
                self.assertIsNone(proc.job)
                self.assertTrue(any("Windows gave it no job object: what it starts may outlive it and the workshop"
                                    in line for line in self.output()))
                with self.assertRaises(ApiError):
                    self.host.call_sync("fx.crash")
                # its state is not kept by the helper that holds its pipes, though nothing can end that one now
                self.assertTrue(until(lambda: self.host.get("fx")["state"] == "exited", 10))
                self.assertTrue(gone(proc.pid))
                self.assertFalse(gone(helpers[0], 0.3))
                self.assertTrue(proc.kill())
                # one that still runs is ended with its tree (taskkill); one that exits by itself, as after shutdown,
                # leaves its helpers behind without a job
                helpers.append(self.host.call_sync("fx.helper")["pid"])
                proc = self.host.procs["fx"]
                self.assertTrue(proc.kill())
                self.assertTrue(gone(helpers[1]))
                self.assertTrue(gone(proc.pid))
                self.assertTrue(until(lambda: self.host.get("fx")["state"] == "exited"))
        finally:
            for pid in helpers:
                P.kill_tree(pid)
        self.assertTrue(gone(helpers[0]))

    @unittest.skipUnless(P.WINDOWS, "job objects are Windows'")
    def test_the_end_of_the_daemon(self):
        script = self.root / "daemon.py"
        script.write_text(DAEMON, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(SRC))
        daemon = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, creationflags=P.CREATE_NO_WINDOW)
        pids = []
        try:
            line = daemon.stdout.readline().split()
            self.assertEqual(len(line), 3, daemon.stderr.read() if daemon.poll() is not None else line)
            pids = [int(line[0]), int(line[1])]
            self.assertEqual(int(line[2]), 1)                          # it has a job
            self.assertFalse(gone(pids[0], 0.3))
            self.assertFalse(gone(pids[1], 0.3))
            daemon.kill()                                              # no shutdown, no atexit: its handles close
            daemon.wait(10)
            self.assertTrue(gone(pids[0]))                             # the extension
            self.assertTrue(gone(pids[1]))                             # and what it started
        finally:
            daemon.kill()
            for stream in (daemon.stdout, daemon.stderr):
                stream.close()
            for pid in pids:
                P.kill_tree(pid)

    def test_bytes_that_are_not_utf8(self):
        self.add(extra="caps")
        with mock.patch.object(P, "fallback_encoding", return_value="gbk"):
            for _ in range(2):
                self.assertEqual(self.host.call_sync("fx.raw", {"what": "gbk"}), {"wrote": "gbk"})
            self.assertTrue(until(lambda: self.output().count("标准错误的中文") == 2
                                  and self.output().count("stdout: 标准输出的中文") == 2))
        out = self.output()
        for stream in ("stdout", "stderr"):                            # said once for each
            self.assertEqual(sum(f"its {stream} has bytes that are not UTF-8: they are read as this computer's code "
                                 f"page (said once)" in line for line in out), 1, stream)
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "中文"})["text"], "中文")
        self.assertEqual(self.host.get("fx")["state"], "running")

    def test_a_line_that_is_too_long(self):
        self.add(extra="caps")
        with mock.patch.object(R, "LINE_LIMIT", 64 * 1024):
            self.assertEqual(self.host.call_sync("fx.raw", {"what": "long", "size": 300000}), {"wrote": "long"})
            self.assertTrue(until(lambda: any("a line of 300001 bytes on its stdout dropped: a line of the protocol "
                                              "has 65536 bytes at most" in line for line in self.output())))
            self.assertFalse(any("xxxxxxxx" in line for line in self.output()))          # none of it is kept
            self.assertEqual(self.host.call_sync("fx.raw", {"what": "long", "size": 65000}), {"wrote": "long"})
            self.assertEqual(self.host.call_sync("fx.echo", {"text": "y" * 60000})["text"], "y" * 60000)
        self.assertEqual(sum("on its stdout dropped" in line for line in self.output()), 1)
        self.assertEqual(self.host.get("fx")["state"], "running")

    def test_answers_nobody_asked_for(self):
        self.add(extra="caps")
        proc = self.host.ensure("fx").result(30)
        self.assertEqual(self.host.call_sync("fx.raw", {"what": "badid"}), {"wrote": "badid"})
        out = self.output()
        for rid in ("'[]'", "'{}'"):                                   # no key of a dict: the reader lives on
            self.assertEqual(sum(f"an answer with the id {rid} dropped: the workshop asked no request of that id"
                                 in line for line in out), 1, rid)
        self.assertTrue(all(t.is_alive() for t in proc.readers))
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "a"})["text"], "a")
        self.assertEqual((self.host.get("fx")["state"], self.host.procs["fx"]), ("running", proc))

    def test_shutdown_waits_as_long_as_it_is_told_now(self):
        self.add(mode="nostop")
        proc = self.host.ensure("fx").result(30)
        with mock.patch.object(P, "SHUTDOWN_WAIT", 0.3):               # read when it is called, not when defined
            t0 = time.monotonic()
            self.assertTrue(proc.shutdown())
            self.assertLess(time.monotonic() - t0, 4)
        self.assertTrue(gone(proc.pid))
        self.assertTrue(until(lambda: self.host.get("fx")["state"] == "stopped"))


if __name__ == "__main__":
    unittest.main()
