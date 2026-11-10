"""The host of local extensions with real processes (tests/local/fixture_ext.py, which speaks the protocol itself): the
registry and its revision, a start on the first call, initialize (options, environment), calls and their errors, the
extension's events and its own calls, options, a crash and the start after it, stop, check, load (with tests), the
output and its rotation, close."""
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from tests.local.util import gone, make_extension, until
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import host as H


class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(self.root / "home")})
        self.env.start()
        self.events, self.calls, self.revisions, self.notes = [], [], [], []
        self.sdk = self.root / "sdk"
        self.host = H.Host(self.root / "state", version="9.9.9", note=self.notes.append,
                           on_event=lambda eid, item: self.events.append((eid, item)), on_call=self.on_call,
                           on_change=self.revisions.append, python=sys.executable, sdk=self.sdk)

    def tearDown(self):
        self.host.close()
        self.env.stop()
        self.tmp.cleanup()

    def on_call(self, eid, cap, args, chain=None):
        self.calls.append((eid, cap, args))
        fut = Future()
        if cap == "sense.bags":
            fut.set_result({"free": 12})
        elif cap == "sense.nothing":
            fut.set_exception(ApiError(404, "no_capability", "no capability sense.nothing"))
        else:
            fut.set_exception(ApiError(503, "down", "the game is not online"))
        return fut

    def add(self, eid="fx", mode="", **fields):
        folder = make_extension(self.root / "ext" / eid, eid, mode, **fields)
        return self.host.add(str(folder)), folder

    def output(self, eid="fx"):
        return [line["text"] for line in self.host.logs(eid, 0, 2000)["lines"]]

    def test_register_start_call(self):
        row, folder = self.add()
        self.assertEqual((row["id"], row["title"], row["version"], row["enabled"], row["start"], row["state"],
                          row["capabilities"]), ("fx", "测试 fx", "0.1.0", True, "use", "stopped", []))
        self.assertEqual(set(row), {"id", "title", "version", "path", "enabled", "start", "state", "since", "why",
                                    "pid", "started", "exit_code", "error", "command", "capabilities", "sources",
                                    "options", "apps", "skills", "adds", "approval", "allowed"})
        self.assertEqual(row["command"], [os.path.abspath(sys.executable), "main.py"])    # what the workshop runs
        self.assertLessEqual(abs(row["since"] - time.time()), 60)
        self.assertEqual(self.host.add(str(folder / "wuxian-extension.json"))["id"], "fx")   # the same folder: no change
        self.assertEqual(self.host.revision, 1)
        res = self.host.call_sync("fx.echo", {"text": "你好"}, {"by": "agent"})       # its first call starts it
        self.assertEqual((res["text"], res["caller"], res["ext"]), ("你好", {"by": "agent"}, "fx"))
        self.assertEqual(res["init"], {"protocol": {"min": 1, "max": 1}, "workshop": "9.9.9", "id": "fx",
                                       "options": {}})
        self.assertEqual(res["path0"], str(self.sdk))
        self.assertEqual(os.path.normcase(res["cwd"]), os.path.normcase(str(folder)))
        row = self.host.get("fx")
        self.assertEqual((row["state"], row["sources"]), ("running", ["said"]))
        self.assertIn("ready in", row["why"])
        self.assertIn("fx.echo", row["capabilities"])
        self.assertEqual(row["options"], [{"name": "volume", "kind": "number", "title": "音量", "default": 50, "min": 0,
                                           "max": 100, "value": 50}])
        self.assertEqual(self.host.kind_of("fx.emit"), "point")
        self.assertIsNone(self.host.kind_of("fx.ghost"))
        self.assertIsNone(self.host.capability("sense.bags"))
        self.assertGreaterEqual(self.revisions[-1], 3)         # registered, starting, running
        out = self.output()
        self.assertTrue(out[0].startswith("-- ") and "started (pid" in out[0], out)
        self.assertIn("fixture up", out)
        self.assertIn("stdout: not the protocol", out)
        self.assertTrue(any("ready: 8 capabilities, 1 sources, 1 options" in line for line in out), out)

        entry = self.host.manifest_entries()["extensions"][0]
        self.assertEqual((entry["id"], entry["state"], entry["runtime"], entry["docs"], entry["skills"]),
                         ("fx", "on", "local", str(folder / "AGENT.md"), None))
        self.assertEqual(entry["capabilities"][0], {"id": "fx.echo", "kind": "see", "title": "回声",
                                                    "doc": "answers what it got", "args": {"text": "string"},
                                                    "timeout": 30})

    def test_call_errors(self):
        self.add()
        with self.assertRaises(ApiError) as e:
            self.host.call_sync("fx.boom")
        self.assertEqual((e.exception.code, e.exception.message), ("failed", "fx.boom: it went boom"))
        with self.assertRaises(ApiError) as e:
            self.host.call_sync("fx.picky", {"n": "x"})
        self.assertEqual(e.exception.code, "bad_args")
        self.assertEqual(self.host.call_sync("fx.picky", {"n": 4}), 8)
        with self.assertRaises(ApiError) as e:
            self.host.call_sync("fx.ghost")
        self.assertEqual(e.exception.code, "no_capability")
        with self.assertRaises(ApiError) as e:
            self.host.call_sync("fx.slow", {"seconds": 3}, timeout=0.3)
        self.assertEqual(e.exception.code, "timeout")
        self.assertIn("boom is about to fail", self.output())

    def test_events_and_its_own_calls(self):
        self.add()
        self.assertEqual(self.host.call_sync("fx.emit", {"text": "说完了"}), {"emitted": True})
        self.assertTrue(until(lambda: self.events))
        eid, item = self.events[0]
        self.assertEqual((eid, item["source"], item["kind"], item["data"]), ("fx", "fx", "fx.said", {"text": "说完了"}))
        self.assertTrue(until(lambda: any("source 'nope' dropped" in line for line in self.output())))
        self.assertEqual(len(self.events), 1)
        res = self.host.call_sync("fx.ask", {"cap": "sense.bags", "args": {"bag": 0}})
        self.assertEqual(res, {"result": {"free": 12}, "error": None})
        self.assertEqual(self.calls, [("fx", "sense.bags", {"bag": 0})])
        res = self.host.call_sync("fx.ask", {"cap": "sense.nothing"})
        self.assertEqual(res["error"], {"code": -32601, "message": "no capability sense.nothing",
                                        "data": {"reason": "no_capability"}})
        res = self.host.call_sync("fx.ask", {"cap": "voice.say"})
        self.assertEqual((res["error"]["code"], res["error"]["data"]), (1, {"reason": "down"}))

    def test_options(self):
        self.add()
        with self.assertRaises(ApiError) as e:
            self.host.set("fx", options={"volume": 10})
        self.assertEqual(e.exception.code, "not_described")
        self.host.call_sync("fx.opts")
        for bad in ({"volume": 101}, {"pitch": 1}):
            with self.assertRaises(ApiError) as e:
                self.host.set("fx", options=bad)
            self.assertEqual(e.exception.code, "bad_option")
        row = self.host.set("fx", options={"volume": 80})
        self.assertEqual(row["options"][0]["value"], 80)
        self.assertTrue(until(lambda: self.host.call_sync("fx.opts") == {"volume": 80}))   # told at once
        self.host.stop("fx")
        self.assertEqual(self.host.call_sync("fx.echo", {"text": ""})["init"]["options"], {"volume": 80})

    def test_crash_stop_off(self):
        row, _ = self.add()
        self.host.call_sync("fx.echo", {"text": "a"})
        pid = self.host.get("fx")["pid"]
        with self.assertRaises(ApiError) as e:
            self.host.call_sync("fx.crash")
        self.assertEqual(e.exception.code, "down")
        self.assertTrue(until(lambda: self.host.get("fx")["state"] == "exited"))
        row = self.host.get("fx")
        self.assertEqual((row["exit_code"], row["pid"]), (3, None))
        self.assertIn("exited with code 3", row["error"])
        self.assertIn("crashing now", self.output())
        self.host.call_sync("fx.echo", {"text": "b"})                 # started again
        row = self.host.get("fx")
        self.assertNotEqual(row["pid"], pid)
        row = self.host.stop("fx")
        self.assertEqual((row["state"], row["exit_code"]), ("stopped", 0))
        self.assertTrue(any(line.endswith("stopped with code 0 after " + line.rsplit("after ", 1)[-1])
                            for line in self.output()))
        row = self.host.set("fx", enabled=False)
        self.assertEqual(row["state"], "off")
        self.assertEqual(self.host.manifest_entries()["extensions"], [])
        with self.assertRaises(ApiError) as e:
            self.host.ensure("fx").result(5)
        self.assertEqual(e.exception.code, "off")
        self.assertEqual(self.host.set("fx", enabled=True)["state"], "stopped")

    def test_start_workshop_and_close(self):
        row, _ = self.add(start="workshop")
        self.assertTrue(until(lambda: self.host.get("fx")["state"] == "running", 30))
        pid = self.host.get("fx")["pid"]
        self.host.close()
        self.assertTrue(gone(pid))
        self.assertEqual(self.host.get("fx")["state"], "stopped")
        with self.assertRaises(ApiError):
            self.host.ensure("fx").result(1)

    def test_a_tree_that_does_not_stop(self):
        self.add(mode="nostop")
        self.host.call_sync("fx.echo", {"text": "a"})
        pid = self.host.get("fx")["pid"]
        began = time.monotonic()
        with mock.patch.object(H, "SHUTDOWN_WAIT", 0.5):
            row = self.host.stop("fx")
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(row["state"], "stopped")
        self.assertTrue(gone(pid))

    def test_starts_that_fail(self):
        self.add("die", mode="die")
        with self.assertRaises(H.Down) as e:
            self.host.ensure("die").result(30)
        self.assertIn("exited before it answered initialize", e.exception.message)
        self.assertIn("RuntimeError: the fixture cannot start", e.exception.message)    # the end of its output
        row = self.host.get("die")
        self.assertEqual((row["state"], row["pid"]), ("backoff", None))
        self.assertIn("exited before it answered initialize", row["error"])
        self.assertIn("1 time in a row", row["why"])
        self.add("say", mode="say")
        with self.assertRaises(H.Down) as e:
            self.host.ensure("say").result(30)
        self.assertIn("bad_capability", e.exception.message)
        self.assertTrue(until(lambda: self.host.get("say")["pid"] is None))
        self.add("mute", mode="mute")
        with mock.patch.object(H, "INIT_TIMEOUT", 1.0):
            with self.assertRaises(H.Down) as e:
                self.host.ensure("mute").result(30)
        self.assertIn("did not answer initialize within 1 s", e.exception.message)
        # a manifest broken after it was registered: stopped, its error said, and no command
        (self.root / "ext" / "die" / "wuxian-extension.json").write_text("{", encoding="utf-8")
        row = self.host.get("die")
        self.assertEqual((row["state"], row["command"]), ("stopped", None))
        self.assertIn("not valid JSON", row["error"])
        self.assertIn("not valid JSON", row["why"])

    def test_check_and_load(self):
        passing = "import unittest\nclass T(unittest.TestCase):\n    def test_sdk(self):\n        import sys\n" \
                  "        self.assertTrue(sys.path)\n"
        failing = "import unittest\nclass T(unittest.TestCase):\n    def test_no(self):\n        self.fail('no')\n"
        folder = make_extension(self.root / "ext" / "cx", "cx", tests={"test_a.py": passing})
        res = self.host.check(str(folder))                    # not registered: by its path
        self.assertTrue(res["ok"], res)
        self.assertIn("cx.echo", res["capabilities"])
        self.assertEqual((res["tests"]["ran"], res["tests"]["failed"]), (1, 0))
        (folder / "tests" / "test_b.py").write_text(failing, encoding="utf-8")
        res = self.host.check(str(folder))
        self.assertFalse(res["ok"])
        self.assertEqual((res["tests"]["ran"], res["tests"]["failed"]), (2, 1))
        self.assertEqual(res["problems"][-1]["where"], "tests")
        (folder / "tests" / "test_b.py").unlink()
        self.host.add(str(folder))
        self.host.call_sync("cx.echo", {"text": "a"})
        pid = self.host.get("cx")["pid"]
        res = self.host.check("cx")                           # does not touch the running one, and starts no second
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.host.get("cx")["pid"], pid)
        self.assertEqual(res["running"], pid)
        self.assertIn("cx.echo", res["capabilities"])
        self.assertEqual([(p["level"], p["where"]) for p in res["problems"]], [("warning", "process")])
        res = self.host.load_("cx")                           # restarts it
        self.assertTrue(res["check"]["ok"], res)
        self.assertEqual(res["row"]["state"], "running")
        self.assertNotEqual(res["row"]["pid"], pid)
        self.assertTrue(gone(pid))
        bad = make_extension(self.root / "ext" / "bad", "bad", mode="say")
        res = self.host.check(str(bad))
        self.assertFalse(res["ok"])
        self.assertTrue(any("bad_capability" in p["message"] for p in res["problems"]))
        (bad / "wuxian-extension.json").write_text('{"id": "Bad"}', encoding="utf-8")
        res = self.host.check(str(bad))
        self.assertEqual((res["ok"], res["capabilities"]), (False, []))
        with self.assertRaises(ApiError):
            self.host.check("ghost")

    def test_registry_errors_and_remove(self):
        for path, code in (("relative", "bad_request"), (str(self.root / "none"), "bad_manifest")):
            with self.assertRaises(ApiError) as e:
                self.host.add(path)
            self.assertEqual(e.exception.code, code)
        self.assertTrue(e.exception.extra["problems"])
        self.add()
        other = make_extension(self.root / "ext" / "fx2", "fx")
        with self.assertRaises(ApiError) as e:
            self.host.add(str(other))
        self.assertEqual(e.exception.code, "extension_exists")
        self.host.call_sync("fx.echo", {"text": "a"})
        pid = self.host.get("fx")["pid"]
        res = self.host.remove("fx")
        self.assertEqual((res["removed"], res["id"]), (True, "fx"))
        self.assertTrue(gone(pid))
        self.assertEqual(self.host.rows(), [])
        with self.assertRaises(ApiError) as e:
            self.host.get("fx")
        self.assertEqual(e.exception.code, "not_found")

    def test_logs_and_rotation(self):
        self.add()
        out = self.host.output("fx")
        for i in range(30):
            out.write(f"line {i}")
        res = self.host.logs("fx", 0, 10)
        self.assertEqual((res["cut"], res["total"], res["lines"][-1]["text"]), (True, 30, "line 29"))
        res = self.host.logs("fx", 29, 10)
        self.assertEqual([line["text"] for line in res["lines"]], ["line 28", "line 29"])
        self.assertEqual(res["next"], 31)
        with mock.patch.object(H, "LOG_MAX", 200):
            out.write("x" * 50)
        self.assertTrue((self.host.output_path("fx").with_name("output.1.log")).is_file())
        res = self.host.logs("fx", 31, 10)
        self.assertEqual((res["reset"], [line["text"] for line in res["lines"]]), (True, ["x" * 50]))
        for bad in ((-1, 10), (0, 0), (0, 5000)):
            with self.assertRaises(ApiError):
                self.host.logs("fx", *bad)

    def test_subscribers(self):
        import asyncio
        heard = []

        async def go():
            loop = asyncio.get_running_loop()
            stop = self.host.subscribe(loop, lambda: heard.append(1))
            await asyncio.to_thread(self.add)
            await asyncio.sleep(0.1)
            stop()
            await asyncio.to_thread(self.host.set, "fx", False)
            await asyncio.sleep(0.1)
        asyncio.run(go())
        self.assertEqual(heard, [1])


class Small(unittest.TestCase):
    def test_sdk_dir_and_python(self):
        self.assertTrue((H.sdk_dir() / "wuxiankit.py").is_file())
        self.assertEqual(H.default_python(), sys.executable)
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(H.shutil, "which",
                                                                                   side_effect=[None, "C:/py.exe"]):
            self.assertEqual(H.default_python(), "C:/py.exe")

    def test_call_error(self):
        self.assertEqual(H.call_error(H.RpcError(-32602, "n"), "a.b").code, "bad_args")
        self.assertEqual(H.call_error(H.RpcError(-32601, "n"), "a.b").code, "no_capability")
        self.assertEqual(H.call_error(H.RpcError(7, "n"), "a.b").code, "failed")
        self.assertEqual((H.call_error(H.RpcError(2, "n"), "a.b").code, H.call_error(H.RpcError(3, "n"), "a.b").code),
                         ("busy", "cancelled"))
        self.assertEqual(H.call_error(ValueError("x"), "a.b").code, "failed")


if __name__ == "__main__":
    unittest.main()
