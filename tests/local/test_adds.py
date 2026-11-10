"""What the enabled local extensions add to apps (local/adds.py), through the host and with real processes
(tests/local/fixture_ext.py): the manifest's adds (wake notes and skill supplements), the declarations of their apps,
an app's reactions, and the controls bound to capabilities."""
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from tests.local.util import make_extension
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import adds as A
from wuxianworkshop.local import host as H

APP = {"app": "companion", "title": "语音搭子", "prompt": "companion", "hear": ["game:combat"],
       "controls": ["stop", {"cmd": "mute", "call": "emit"}, {"cmd": "again", "label": "再说", "call": "echo"},
                    {"cmd": "skip", "label": "跳过"}],
       "reactions": [{"hear": "game:combat.start", "when": {"data.unit.classification": ["elite"]}, "call": "fx.emit",
                      "args": {"text": "elite"}, "cooldown": 30},
                     {"hear": "fx:said", "call": "sense.bags", "item": "event", "note": "what was said"}]}


class AddsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(self.root / "home")})
        self.env.start()
        self.host = H.Host(self.root / "state", version="9.9.9", on_call=self.on_call, python=sys.executable,
                           sdk=self.root / "sdk")

    def tearDown(self):
        self.host.close()
        self.env.stop()
        self.tmp.cleanup()

    def on_call(self, eid, cap, args, chain=None):
        fut = Future()
        fut.set_exception(ApiError(503, "down", "the game is not online"))
        return fut

    def add(self, eid="fx", mode="", **fields):
        folder = make_extension(self.root / "ext" / eid, eid, mode, **fields)
        return self.host.add(str(folder)), folder

    def test_adds(self):
        """adds(app): the enabled extensions whose manifest passes, by id, with the wake's capability and the skill's
        path; a wake that is no see capability of what the extension described is None; off takes both away"""
        _, folder = self.add("fx", adds={"guild": {"wake": "echo", "skill": "AGENT.md"}, "raid": {"wake": "emit"},
                                         "pvp": {"skill": "AGENT.md"}})
        _, other = self.add("fa", adds={"guild": {"wake": {"call": "echo", "timeout": 1}}})
        skill = str(folder / "AGENT.md")
        self.assertEqual(self.host.adds("guild"), [
            {"ext": "fa", "title": "测试 fa", "wake": {"call": "fa.echo", "timeout": 1.0}, "skill": None},
            {"ext": "fx", "title": "测试 fx", "wake": {"call": "fx.echo", "timeout": 3.0}, "skill": skill}])
        self.assertEqual(self.host.adds("raid"), [{"ext": "fx", "title": "测试 fx", "skill": None,
                                                   "wake": {"call": "fx.emit", "timeout": 3.0}}])   # not described yet
        self.assertEqual(self.host.adds("pvp"), [{"ext": "fx", "title": "测试 fx", "wake": None, "skill": skill}])
        self.assertEqual((self.host.adds("arena"), self.host.adds(None), self.host.adds("")), ([], [], []))
        self.assertEqual(self.host.get("fx")["adds"], {
            "guild": {"wake": {"call": "fx.echo", "timeout": 3.0}, "skill": skill},
            "raid": {"wake": {"call": "fx.emit", "timeout": 3.0}, "skill": None},
            "pvp": {"wake": None, "skill": skill}})
        self.assertEqual({x["id"]: x["adds"] for x in self.host.manifest_entries()["extensions"]},
                         {eid: self.host.get(eid)["adds"] for eid in ("fx", "fa")})
        self.assertEqual((A.row(None), A.row({})), ({}, {}))

        self.host.set("fa", enabled=False)                     # one switch
        self.assertEqual([a["ext"] for a in self.host.adds("guild")], ["fx"])

        res = self.host.check("fx")                            # its own process describes it: emit is a point
        self.assertFalse(res["ok"])
        self.assertEqual([(p["level"], p["where"]) for p in res["problems"] if "adds" in p["where"]],
                         [("warning", "wuxian-extension.json: adds.guild.wake"),      # echo takes no wake
                          ("error", "wuxian-extension.json: adds.raid.wake")])
        self.host.call_sync("fx.echo", {"text": "a"})          # described now
        self.assertEqual(self.host.adds("raid"), [{"ext": "fx", "title": "测试 fx", "wake": None, "skill": None}])
        self.assertEqual(self.host.adds("guild")[0]["wake"], {"call": "fx.echo", "timeout": 3.0})
        res = self.host.load_("fx")
        self.assertFalse(res["check"]["ok"])
        self.assertIn("a wake's note only reads", next(p["message"] for p in res["check"]["problems"]
                                                         if p["where"].endswith("adds.raid.wake")))

        manifest = folder / "wuxian-extension.json"            # a manifest that no longer passes: left out
        manifest.write_text(manifest.read_text(encoding="utf-8").replace('"raid"', '"*"'), encoding="utf-8")
        os.utime(manifest, ns=(time.time_ns(), time.time_ns() + 10**9))
        self.assertEqual((self.host.adds("guild"), self.host.adds("pvp"), self.host.get("fx")["adds"]), ([], [], {}))

    def test_apps(self):
        """an app of a manifest: its declaration without what is the workshop's (the reactions, a control's call),
        its reactions with their extension, its bound controls; all gone when the extension is off"""
        self.add("fx", apps=[APP, {"app": "fx_two"}], adds={"companion": {"wake": "echo"}})
        self.add("fa", apps=[{"app": "fa_one", "title": "一", "controls": ["stop"]}])
        self.assertEqual(self.host.declarations(), [
            ("fx", {"app": "companion", "title": "语音搭子", "prompt": "companion", "hear": ["game:combat"],
                    "controls": ["stop", "mute", {"cmd": "again", "label": "再说"}, {"cmd": "skip", "label": "跳过"}],
                    "local": {"id": "fx", "title": "测试 fx"}}),
            ("fx", {"app": "fx_two", "title": "fx_two", "local": {"id": "fx", "title": "测试 fx"}}),
            ("fa", {"app": "fa_one", "title": "一", "controls": ["stop"], "local": {"id": "fa", "title": "测试 fa"}})])
        self.assertEqual(self.host.app_reactions("companion"), [
            {"ext": "fx", "hear": "game:combat.start", "when": {"data.unit.classification": ["elite"]},
             "call": "fx.emit", "args": {"text": "elite"}, "item": None, "cooldown": 30, "note": None},
            {"ext": "fx", "hear": "fx:said", "when": None, "call": "sense.bags", "args": None, "item": "event",
             "cooldown": 10, "note": "what was said"}])
        self.assertEqual((self.host.app_reactions("fx_two"), self.host.app_reactions("guild"),
                          self.host.app_reactions(None)), ([], [], []))
        self.assertEqual(self.host.control_call("companion", "mute"), {"ext": "fx", "call": "fx.emit"})
        self.assertEqual(self.host.control_call("companion", "again"), {"ext": "fx", "call": "fx.echo"})
        self.assertEqual([self.host.control_call(*x) for x in (("companion", "skip"), ("companion", "stop"),
                                                               ("fa_one", "stop"), ("guild", "mute"), (None, "mute"),
                                                               ("companion", None))], [None] * 6)
        self.assertEqual(self.host.adds("companion")[0]["wake"]["call"], "fx.echo")     # its own app: adds names it too

        self.host.declarations()[0][1]["controls"].append("x")                          # copies, all of them
        self.host.app_reactions("companion")[0]["when"]["data.unit.classification"].append("x")
        self.host.control_call("companion", "mute")["call"] = "x"
        self.host.adds("companion")[0]["wake"]["call"] = "x"
        self.assertEqual(len(self.host.declarations()[0][1]["controls"]), 4)
        self.assertEqual(self.host.app_reactions("companion")[0]["when"], {"data.unit.classification": ["elite"]})
        self.assertEqual(self.host.control_call("companion", "mute")["call"], "fx.emit")
        self.assertEqual(self.host.adds("companion")[0]["wake"]["call"], "fx.echo")

        res = self.host.check("fx")                            # against what it described: all three are there
        self.assertEqual([p for p in res["problems"] if p["level"] == "error"], [])
        self.host.set("fx", enabled=False)
        self.assertEqual([eid for eid, _ in self.host.declarations()], ["fa"])
        self.assertEqual((self.host.app_reactions("companion"), self.host.control_call("companion", "mute"),
                          self.host.adds("companion")), ([], None, []))

    def test_the_table_is_kept(self):
        """adds() is called for every wake: no manifest is read again until the registry or an enabled extension's
        manifest changed"""
        _, folder = self.add("fx", adds={"guild": {"wake": "echo"}}, apps=[APP])
        self.assertEqual(len(self.host.adds("guild")), 1)
        real = H.M.check_manifest
        with mock.patch.object(H.M, "check_manifest", side_effect=real) as checked:
            for _ in range(50):
                self.assertEqual(self.host.adds("guild")[0]["wake"]["call"], "fx.echo")
                self.assertEqual(len(self.host.app_reactions("companion")), 2)
                self.assertEqual(self.host.control_call("companion", "mute")["call"], "fx.emit")
                self.assertEqual(len(self.host.declarations()), 1)
            self.assertEqual(self.host.adds("raid"), [])
            self.assertEqual(checked.call_count, 0)
            manifest = folder / "wuxian-extension.json"                     # the manifest changed: read again
            manifest.write_text(manifest.read_text(encoding="utf-8").replace('"guild"', '"raid"'), encoding="utf-8")
            os.utime(manifest, ns=(time.time_ns(), time.time_ns() + 10**9))
            self.assertEqual((self.host.adds("guild"), len(self.host.adds("raid"))), ([], 1))
            self.assertGreater(checked.call_count, 0)
            self.host.set("fx", enabled=False)                              # the registry changed
            self.assertEqual(self.host.adds("raid"), [])
            self.host.set("fx", enabled=True)
            self.assertEqual(len(self.host.adds("raid")), 1)

    def test_the_fourth_batchs_names(self):
        """annotations() and extensions_of() answer nothing now (host.py still forwards to them)"""
        self.add("fx", adds={"guild": {"wake": "echo", "skill": "AGENT.md"}})
        self.assertEqual((self.host.annotations("guild"), self.host.extensions_of("guild-chat")), ([], []))
        self.assertEqual((self.host.get("fx")["annotate"], self.host.get("fx")["extends"]), ([], []))


if __name__ == "__main__":
    unittest.main()
