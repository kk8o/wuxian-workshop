"""What the enabled local extensions add to apps (local/adds.py), through the host and with real processes
(tests/local/fixture_ext.py): the fourth batch's annotations and skill supplements, until the manifest's adds takes
their place."""
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
from wuxianworkshop.local import host as H


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

    def test_stubs(self):
        """the fifth batch's three, before they are written"""
        self.add("fx")
        self.assertEqual((self.host.adds("guild"), self.host.app_reactions("guild"),
                          self.host.control_call("guild", "mute")), ([], [], None))
        self.assertEqual((self.host.get("fx")["adds"], self.host.get("fx")["approval"]), ({}, "ok"))

    def test_annotations_and_supplements(self):
        """annotations(app) and extensions_of(skill): the enabled extensions whose manifest passes, by id; an
        annotation whose call is no see capability of what it described left out; the rows and the manifest entries
        carry both; check and load report the calls against the description"""
        notes = [{"app": "guild", "call": "fx.echo", "wake": "text"}, {"app": "raid", "call": "fx.emit"}]
        _, folder = self.add("fx", annotate=notes, extends={"guild-chat": "AGENT.md"})
        self.add("fa", annotate=[{"app": "guild", "call": "fa.echo", "timeout": 1}], extends={"guild-chat": "AGENT.md"})
        self.assertEqual(self.host.annotations("guild"), [
            {"ext": "fa", "call": "fa.echo", "wake": "wake", "timeout": 1.0},
            {"ext": "fx", "call": "fx.echo", "wake": "text", "timeout": 3.0}])
        self.assertEqual(self.host.annotations("raid"), [{"ext": "fx", "call": "fx.emit", "wake": "wake",
                                                          "timeout": 3.0}])   # not described yet: kept
        self.assertEqual((self.host.annotations("pvp"), self.host.annotations(None)), ([], []))
        self.assertEqual([x["ext"] for x in self.host.extensions_of("guild-chat")], ["fa", "fx"])
        self.assertEqual(self.host.extensions_of("guild-chat")[1], {"ext": "fx", "title": "测试 fx",
                                                                    "path": str(folder / "AGENT.md")})
        self.assertEqual(self.host.extensions_of("companion"), [])
        self.host.set("fa", enabled=False)                     # one switch: off takes both away
        self.assertEqual([a["ext"] for a in self.host.annotations("guild")], ["fx"])
        self.assertEqual([x["ext"] for x in self.host.extensions_of("guild-chat")], ["fx"])

        row = self.host.get("fx")
        self.assertEqual(row["annotate"], [{"app": "guild", "call": "fx.echo", "wake": "text", "timeout": 3.0},
                                           {"app": "raid", "call": "fx.emit", "wake": "wake", "timeout": 3.0}])
        self.assertEqual(row["extends"], ["guild-chat"])
        entry = self.host.manifest_entries()["extensions"][0]
        self.assertEqual((entry["annotate"], entry["extends"]), (row["annotate"], {"guild-chat": str(folder / "AGENT.md")}))

        res = self.host.check("fx")                            # its own process describes it: emit is a point
        self.assertFalse(res["ok"])
        self.assertEqual([(p["level"], p["where"]) for p in res["problems"] if "annotate" in p["where"]],
                         [("error", "wuxian-extension.json: annotate[1].call")])
        self.host.call_sync("fx.echo", {"text": "a"})          # described now
        self.assertEqual(self.host.annotations("raid"), [])
        self.assertEqual([a["call"] for a in self.host.annotations("guild")], ["fx.echo"])
        res = self.host.load_("fx")
        self.assertFalse(res["check"]["ok"])
        self.assertIn("an annotation only reads", next(p["message"] for p in res["check"]["problems"]
                                                         if p["where"].endswith("annotate[1].call")))

        manifest = folder / "wuxian-extension.json"            # a manifest that no longer passes: left out
        manifest.write_text(manifest.read_text(encoding="utf-8").replace('"raid"', '"*"'), encoding="utf-8")
        self.assertEqual((self.host.annotations("guild"), self.host.extensions_of("guild-chat")), ([], []))
        self.assertEqual((self.host.get("fx")["annotate"], self.host.get("fx")["extends"]), ([], []))

    def test_annotations_are_kept(self):
        """annotations() is called for every wake: no manifest is read again until the registry or an enabled
        extension's manifest changed"""
        _, folder = self.add("fx", annotate=[{"app": "guild", "call": "fx.echo", "wake": "text"}])
        self.assertEqual(len(self.host.annotations("guild")), 1)
        real = H.M.check_manifest
        with mock.patch.object(H.M, "check_manifest", side_effect=real) as checked:
            for _ in range(50):
                self.assertEqual(self.host.annotations("guild")[0]["call"], "fx.echo")
            self.assertEqual(self.host.annotations("raid"), [])
            self.assertEqual(checked.call_count, 0)
            self.host.annotations("guild")[0]["call"] = "changed"            # a copy each time
            self.assertEqual(self.host.annotations("guild")[0]["call"], "fx.echo")
            manifest = folder / "wuxian-extension.json"                     # the manifest changed: read again
            manifest.write_text(manifest.read_text(encoding="utf-8").replace('"guild"', '"raid"'), encoding="utf-8")
            os.utime(manifest, ns=(time.time_ns(), time.time_ns() + 10**9))
            self.assertEqual((self.host.annotations("guild"), len(self.host.annotations("raid"))), ([], 1))
            self.assertGreater(checked.call_count, 0)
            self.host.set("fx", enabled=False)                              # the registry changed
            self.assertEqual(self.host.annotations("raid"), [])
            self.host.set("fx", enabled=True)
            self.assertEqual(len(self.host.annotations("raid")), 1)


if __name__ == "__main__":
    unittest.main()
