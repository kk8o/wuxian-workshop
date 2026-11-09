"""新建插件 (wuxianworkshop/scaffold.py): the files it makes, its refusals, and both templates run in the mock client:
loaded, logged in, the slash command, then a hot reload the way WoWBridge's Agent.lua does it (OnUnload, the files
again with the same namespace, OnReload with what OnUnload returned). 新建扩展 (new_extension_files): its files, its
refusals, and the extension it makes passes its own tests and answers the workshop's protocol."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support.wowmock import Session, lupa, toc_files
from wuxianworkshop import scaffold


def same(s, a, b):
    """the same Lua table (lupa makes a new Python object for each access)"""
    return bool(s.lua.globals()[b"rawequal"](a, b))


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Templates(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addons = Path(tmp.name)

    def run_addon(self, s, name, ns):
        load = s.lua.globals()[b"__load"]
        folder = self.addons / name
        for f in toc_files(folder):
            fn, err = load((folder / f).read_bytes(), f"@{name}/{f}".encode())
            self.assertIsNone(err, f)
            fn(name.encode(), ns)

    @staticmethod
    def printed(s):
        """what reached print(): the client shows it in the chat"""
        out = s.lua.globals()[b"__printed"]
        return "\n".join(out[i].decode("utf-8", "replace") for i in range(1, len(out) + 1))

    def hot_reload(self, s, name, ns):
        state = ns[b"OnUnload"]()
        self.run_addon(s, name, ns)
        ns[b"OnReload"](state)

    def test_the_clients_interface(self):
        """a new addon gets the running client's ## Interface (daemon new_addon passes it), and AGENTS.md says which client"""
        scaffold.create(self.addons, "Later", interface=16100, client="1.61.0.70500")
        self.assertIn("## Interface: 16100", (self.addons / "Later" / "Later.toc").read_text(encoding="utf-8"))
        self.assertIn("1.61.0.70500，`## Interface: 16100`", (self.addons / "Later" / "AGENTS.md").read_text(encoding="utf-8"))

    def test_basic(self):
        res = scaffold.create(self.addons, "HelloWorld", title="你好世界", notes="第一个插件")
        self.assertEqual(sorted(res["files"]), ["AGENTS.md", "CLAUDE.md", "HelloWorld.lua", "HelloWorld.toc"])
        self.assertEqual((res["slash"], res["restart"]), ("/helloworld", True))
        toc = (self.addons / "HelloWorld" / "HelloWorld.toc").read_text(encoding="utf-8")
        self.assertIn("## Interface: 16001", toc)
        self.assertIn("## Title: 你好世界", toc)
        self.assertIn("## SavedVariables: HelloWorldDB", toc)
        self.assertIn("@AGENTS.md", (self.addons / "HelloWorld" / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertIn("load", (self.addons / "HelloWorld" / "AGENTS.md").read_text(encoding="utf-8"))
        s = Session(self, bridge=False, locale="zhCN")
        ns = s.lua.table()
        self.run_addon(s, "HelloWorld", ns)
        fire = s.lua.globals()[b"__fire"]
        fire(b"ADDON_LOADED", b"HelloWorld")
        fire(b"PLAYER_LOGIN")
        self.assertIn("已加载（第 1 次登录）", self.printed(s))
        s.slash("abc", "HELLOWORLD")
        self.assertIn("你好！参数：abc", self.printed(s))
        self.assertTrue(same(s, s.lua.globals()[b"WoWBridgeNS"][b"HelloWorld"], ns))
        self.hot_reload(s, "HelloWorld", ns)
        self.assertIn("已热加载", self.printed(s))
        self.assertEqual(ns[b"db"][b"logins"], 1)                 # the state came across the reload
        events = ns[b"events"]
        self.hot_reload(s, "HelloWorld", ns)
        self.assertTrue(same(s, ns[b"events"], events))                  # one event frame, not one per load

    def test_window(self):
        scaffold.create(self.addons, "MyWindow", template="window")
        s = Session(self, bridge=False)
        ns = s.lua.table()
        self.run_addon(s, "MyWindow", ns)
        win = ns[b"window"]
        self.assertFalse(win[b"shown"])
        s.slash("", "MYWINDOW")
        self.assertTrue(win[b"shown"])
        self.hot_reload(s, "MyWindow", ns)
        self.assertFalse(win[b"shown"])                           # the old one hidden by OnUnload
        self.assertFalse(same(s, ns[b"window"], win))                  # built again from the new code

    def test_refusals(self):
        scaffold.create(self.addons, "Taken")
        for name, template, why in (("1abc", "basic", "插件名"), ("a", "basic", "插件名"), ("has space", "basic", "插件名"),
                                    ("Taken", "basic", "已经有"), ("taken", "basic", "已经有"), ("Fine", "nope", "模板")):
            with self.assertRaises(scaffold.ScaffoldError) as cm:
                scaffold.create(self.addons, name, template=template)
            self.assertIn(why, str(cm.exception), name)
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.create(self.addons / "missing", "Fine")


class Extensions(unittest.TestCase):
    """新建扩展 (new_extension_files): the files, the manifest, the refusals, and the tests it makes pass as made"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_the_files(self):
        folder = self.root / "greeter"
        files = scaffold.new_extension_files(folder, "greeter", '问候 "Hi" \\')
        self.assertEqual(files, ["wuxian-extension.json", "main.py", "AGENT.md", "tests/test_main.py"])
        self.assertEqual(sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()),
                         sorted(files))
        self.assertEqual(json.loads((folder / "wuxian-extension.json").read_text(encoding="utf-8")),
                         {"id": "greeter", "title": '问候 "Hi" \\', "version": "0.1.0", "run": ["python", "main.py"],
                          "start": "use"})
        main = (folder / "main.py").read_text(encoding="utf-8")
        compile(main, "main.py", "exec")
        compile((folder / "tests" / "test_main.py").read_text(encoding="utf-8"), "test_main.py", "exec")
        self.assertIn('@ext.see("hello"', main)
        self.assertIn("wk_greeter_<name>", main)
        agent = (folder / "AGENT.md").read_text(encoding="utf-8")
        self.assertTrue(agent.startswith('# 问候 "Hi" \\ (greeter)'), agent[:40])
        self.assertIn("`wk_greeter_hello`", agent)
        self.assertIn("`greeter:greeted`", agent)
        self.assertNotIn("{{", main + agent)

    def test_its_tests_pass(self):
        folder = self.root / "hello_ext"
        scaffold.new_extension_files(folder, "hello_ext", "你好")
        env = dict(os.environ, PYTHONPATH=str(scaffold.sdk_file().parent), WUXIAN_EXTENSION="hello_ext")
        run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=folder, env=env,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Ran 4 tests", run.stderr)

    def test_it_runs(self):
        """the made extension answers the workshop: initialize, hello, shutdown"""
        folder = self.root / "runner"
        scaffold.new_extension_files(folder, "runner", "Runner")
        env = dict(os.environ, PYTHONPATH=str(scaffold.sdk_file().parent), WUXIAN_EXTENSION="runner",
                   PYTHONIOENCODING="utf-8")
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocol": 1, "workshop": "test",
                                                                                "id": "runner", "options": {}}},
                 {"jsonrpc": "2.0", "id": 2, "method": "call", "params": {"name": "hello", "args": {"name": "Jaina"},
                                                                          "caller": {"by": "agent"}}},
                 {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}}]
        run = subprocess.run([sys.executable, "main.py"], cwd=folder, env=env, timeout=60, capture_output=True,
                             input="".join(json.dumps(m) + "\n" for m in lines).encode("utf-8"))
        self.assertEqual(run.returncode, 0, run.stderr)
        out = [json.loads(line) for line in run.stdout.decode("utf-8").splitlines()]
        answers = {m["id"]: m for m in out if "id" in m}
        self.assertEqual([c["name"] for c in answers[1]["result"]["capabilities"]], ["hello"])
        self.assertEqual(answers[2]["result"], {"text": "Hello, Jaina!"})
        self.assertEqual(answers[3]["result"], {})
        self.assertIn({"jsonrpc": "2.0", "method": "event", "params": {"source": "greeted", "data": {
            "name": "Jaina", "text": "Hello, Jaina!"}}}, out)
        self.assertIn(b"runner: ready", run.stderr)

    def test_in_english(self):
        with mock.patch.object(scaffold, "language", return_value="en"):
            scaffold.new_extension_files(self.root / "en", "en_ext")
        main = (self.root / "en" / "main.py").read_text(encoding="utf-8")
        self.assertIn('title="Greeting"', main)
        self.assertIn('"title": "en_ext"', (self.root / "en" / "wuxian-extension.json").read_text(encoding="utf-8"))

    def test_refusals(self):
        (self.root / "full").mkdir()
        (self.root / "full" / "x.txt").write_text("x", encoding="utf-8")
        (self.root / "empty").mkdir()
        for args, code in (((Path("relative"), "ok"), "bad_request"), ((self.root / "a", "Bad"), "bad_request"),
                           ((self.root / "a", "1abc"), "bad_request"), ((self.root / "a", "a" * 25), "bad_request"),
                           ((self.root / "a", "sense"), "bad_request"), ((self.root / "a", "wk"), "bad_request"),
                           ((self.root / "a", "local"), "bad_request"), ((self.root / "a", "ok", "长" * 17), "bad_request"),
                           ((self.root / "a", "ok", "  "), "bad_request"), ((self.root / "full", "ok"), "folder_not_empty")):
            with self.assertRaises(scaffold.ScaffoldError) as cm:
                scaffold.new_extension_files(*args)
            self.assertEqual(cm.exception.code, code, args)
        self.assertFalse((self.root / "a").exists())                            # nothing made
        self.assertEqual(len(scaffold.new_extension_files(self.root / "empty", "ok", "长" * 16)), 4)
        self.assertIsNone(scaffold.check_extension_id("a" * 24))


if __name__ == "__main__":
    unittest.main()
