"""新建插件 (wuxianworkshop/scaffold.py): the files it makes, its refusals, and both templates run in the mock client:
loaded, logged in, the slash command, then a hot reload the way WoWBridge's Agent.lua does it (OnUnload, the files
again with the same namespace, OnReload with what OnUnload returned)."""
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
