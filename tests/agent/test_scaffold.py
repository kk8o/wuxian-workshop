"""新建插件 (wuxianworkshop/scaffold.py): the files it makes, its refusals, and both templates run in the mock client:
loaded, logged in, the slash command, then a hot reload the way WoWBridge's Agent.lua does it (OnUnload, the files
again with the same namespace, OnReload with what OnUnload returned). 新建程序 (create_program): its files (the
manifest the loader reads, main.py, a copy of the SDK) and its refusals; tests/daemon/test_programs_api.py runs both
main.py templates."""
import json
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


class Programs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_with_an_app(self):
        from wuxianworkshop.programs import read_manifest
        res = scaffold.create_program(self.root / "talk", "talk", title="解说")
        folder = self.root / "talk"
        self.assertEqual(res, dict(id="talk", path=str(folder), manifest=str(folder / "wuxian-program.json"),
                                   files=["wuxian-program.json", "main.py", "wuxiankit.py", "AGENTS.md", "CLAUDE.md"]))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(res["files"]))
        self.assertEqual(read_manifest(folder / "wuxian-program.json"),        # what the loader takes
                         dict(id="talk", title="解说", run=["python", "main.py"], cwd=".", env={},
                              app={"hear": [], "say": True, "controls": ["stop"]}))
        main = (folder / "main.py").read_text(encoding="utf-8")
        compile(main, "main.py", "exec")
        self.assertIn("Client().app(*program_app())", main)
        self.assertEqual((folder / "wuxiankit.py").read_bytes(), scaffold.sdk_file().read_bytes())
        self.assertIn("talk", (folder / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertEqual((folder / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n")

    def test_without_an_app_and_in_english(self):
        with mock.patch.object(scaffold, "language", return_value="en"):
            scaffold.create_program(self.root / "plain", "plain", app=False)
        folder = self.root / "plain"
        manifest = json.loads((folder / "wuxian-program.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest, {"id": "plain", "title": "plain", "run": ["python", "main.py"], "cwd": ".", "env": {}})
        main = (folder / "main.py").read_text(encoding="utf-8")
        compile(main, "main.py", "exec")
        self.assertIn('kit.call("sense.character")', main)
        self.assertNotIn("program_app", main)
        agents = (folder / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(agents.startswith("# plain (plain) · notes for the agent"), agents[:80])

    def test_refusals(self):
        (self.root / "full").mkdir()
        (self.root / "full" / "x.txt").write_text("x", encoding="utf-8")
        for args, code in (((Path("relative"), "ok"), "bad_request"), ((self.root / "a", "Bad"), "bad_request"),
                           ((self.root / "a", "1abc"), "bad_request"), ((self.root / "a", "a" * 25), "bad_request"),
                           ((self.root / "full", "ok"), "folder_not_empty")):
            with self.assertRaises(scaffold.ScaffoldError) as cm:
                scaffold.create_program(*args)
            self.assertEqual(cm.exception.code, code, args)
        with self.assertRaises(scaffold.ScaffoldError) as cm:
            scaffold.create_program(self.root / "a", "ok", title="长" * 17)
        self.assertEqual(cm.exception.code, "bad_request")
        with mock.patch.object(scaffold, "sdk_file", return_value=self.root / "none.py"):
            with self.assertRaises(scaffold.ScaffoldError) as cm:
                scaffold.create_program(self.root / "a", "ok")
        self.assertEqual(cm.exception.code, "no_sdk")
        self.assertFalse((self.root / "a").exists())                            # nothing made


if __name__ == "__main__":
    unittest.main()
