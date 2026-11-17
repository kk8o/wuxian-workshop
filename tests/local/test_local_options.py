"""A local extension's options in the game's /wk window (fx2, contract2 section 3): the roster WuxianKit is told
(kit.local.set) gives each extension's options as it described them, with their values; Host.option_from_game sets
one the player changed there (kit.local.option) as /api/local/set does, booleans, numbers and choices only."""
import os
import sys
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from tests.local.util import make_extension
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import adds as A
from wuxianworkshop.local import host as H

OPTIONS = [
    {"name": "auto", "kind": "boolean", "title": "自动提交", "default": True, "hint": "存好快照就提交"},
    {"name": "folder", "kind": "text", "title": "下载位置", "default": ""},
    {"name": "every", "kind": "number", "title": "间隔", "default": 10, "min": 5, "max": 60, "step": 5, "unit": "分钟"},
    {"name": "side", "kind": "choice", "title": "阵营", "default": "both",
     "choices": [{"value": "both", "label": "两边"}, {"value": "horde", "label": "部落"}]},
]
DESCRIBED = {"capabilities": [], "sources": [], "options": OPTIONS}


class LocalOptions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(self.root / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.host = H.Host(self.root / "state", version="9.9.9", on_call=self.on_call, python=sys.executable,
                           sdk=self.root / "sdk", auto=lambda: True)
        self.addCleanup(self.host.close)

    def on_call(self, eid, cap, args, chain=None):
        fut = Future()
        fut.set_exception(ApiError(503, "down", "the game is not online"))
        return fut

    def add(self, eid="fx", described=DESCRIBED, **fields):
        folder = make_extension(self.root / "ext" / eid, eid, **fields)
        self.host.add(str(folder))
        if described is not None:
            self.host.update_entry(eid, described=described)
        return folder

    def roster(self, eid="fx"):
        return next(item for item in self.host.roster() if item["id"] == eid)

    def test_the_roster_gives_the_options(self):
        self.add()
        self.add("bare", described=None)                         # not described yet: no options
        self.add("none", described={"capabilities": [], "sources": [], "options": []})
        self.assertEqual(self.roster()["options"], [
            {"name": "auto", "kind": "boolean", "title": "自动提交", "value": True, "default": True,
             "hint": "存好快照就提交"},
            {"name": "folder", "kind": "text", "title": "下载位置", "value": "", "default": ""},
            {"name": "every", "kind": "number", "title": "间隔", "value": 10, "default": 10, "min": 5, "max": 60,
             "step": 5, "unit": "分钟"},
            {"name": "side", "kind": "choice", "title": "阵营", "value": "both", "default": "both",
             "choices": [{"value": "both", "label": "两边"}, {"value": "horde", "label": "部落"}]}])
        self.assertNotIn("options", self.roster("bare"))
        self.assertNotIn("options", self.roster("none"))
        self.host.set("fx", options={"auto": False, "folder": "D:/cc"})
        self.assertEqual({o["name"]: o["value"] for o in self.roster()["options"]},
                         {"auto": False, "folder": "D:/cc", "every": 10, "side": "both"})
        self.host.set("fx", enabled=False)                       # off: listed, with its options all the same
        self.assertEqual((self.roster()["on"], len(self.roster()["options"])), (False, 4))
        e = self.host.entry("fx")
        A.roster_options(e)[3]["choices"].append("x")            # a copy
        self.assertEqual(len(self.roster()["options"][3]["choices"]), 2)

    def test_what_the_game_may_change(self):
        self.add()
        before = self.host.revision
        for name, value in (("auto", False), ("every", 25), ("side", "horde")):
            row = self.host.option_from_game("fx", name, value)
            self.assertEqual({o["name"]: o["value"] for o in row["options"]}[name], value)
        self.assertGreater(self.host.revision, before)           # each told as a change (the roster again, ...)
        self.assertEqual(self.host.option_values("fx"), {"auto": False, "folder": "", "every": 25, "side": "horde"})

    def test_what_it_refuses(self):
        self.add()
        self.add("bare", described=None)
        for eid, name, value, code, words in (
                ("fx", "folder", "C:/x", "bad_option", "App only"),        # a text: in the App only
                ("fx", "auto", "yes", "bad_option", "option auto"),        # not a boolean
                ("fx", "every", 61, "bad_option", "option every"),         # past max
                ("fx", "side", "alliance", "bad_option", "option side"),   # no such choice
                ("fx", "volume", 3, "bad_option", "no option 'volume'"),
                ("bare", "auto", True, "not_described", "not started yet"),
                ("ghost", "auto", True, "not_found", "ghost"),
                (None, "auto", True, "bad_request", "kit.local.option"),
                ("fx", 3, True, "bad_request", "kit.local.option")):
            with self.assertRaises(ApiError, msg=(eid, name)) as e:
                self.host.option_from_game(eid, name, value)
            self.assertEqual(e.exception.code, code, (eid, name, e.exception.message))
            self.assertIn(words, e.exception.message)
        self.assertEqual(self.host.option_values("fx"), {"auto": True, "folder": "", "every": 10, "side": "both"})


if __name__ == "__main__":
    unittest.main()
