"""Kept versions over a real daemon (agent/history.py behind /api/history): load, the start of a watch and a watched save
keep a version first; checkpoint, diff, restore and forget over HTTP, the MCP tools and the command line."""
import asyncio
import contextlib
import io
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame, request
from wuxianworkshop.cli import main as cli
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server


class KeptVersions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(cls.tmp.name) / "home")})
        cls.env.start()
        cls.client = Path(cls.tmp.name) / "client"
        cls.addons = cls.client / "Interface" / "AddOns"
        (cls.addons / "WoWBridge").mkdir(parents=True)
        MB.install(cls.addons, pool=64)
        cls.handle = server.start(worker_factory=lambda on_debug, log: FakeGame(cls.addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "H")

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def addon(self, name):
        folder = self.addons / name
        folder.mkdir()
        (folder / f"{name}.toc").write_text("## Interface: 16001\nCore.lua\n", encoding="utf-8")
        self.edit(name, "print(1)\n")
        return folder

    def edit(self, name, text, later=0):
        p = self.addons / name / "Core.lua"
        p.write_text(text, encoding="utf-8")
        if later:                                  # a time stamp the watch sees as new
            t = time.time_ns() + later
            os.utime(p, ns=(t, t))

    def post(self, body):
        return request(self.handle, "POST", "/api/history", body)

    def get(self, query):
        return request(self.handle, "GET", "/api/history" + query)

    def refused(self, method, path, body=None):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            request(self.handle, method, path, body)
        cm.exception.close()
        return cm.exception.code

    def test_load_watch_and_saves_keep_versions(self):
        self.addon("Kept")
        res = request(self.handle, "POST", "/api/load", {"target": "Kept"})
        self.assertEqual(res["kept"], {"addon": "Kept", "id": 1, "new": True})
        res = request(self.handle, "POST", "/api/load", {"target": "Kept/Core.lua"})
        self.assertEqual(res["kept"], {"addon": "Kept", "id": 1, "new": False})        # nothing changed
        self.edit("Kept", "print(2)\n")
        res = request(self.handle, "POST", "/api/watch", {"action": "start", "target": "Kept"})
        self.assertEqual(res["kept"], {"addon": "Kept", "id": 2, "new": True})
        self.edit("Kept", "print(3)\n", later=10 ** 9)                                # saved: loaded again, kept
        deadline = time.time() + 15
        while time.time() < deadline and len(self.get("?addon=Kept")["versions"]) < 3:
            time.sleep(0.2)
        request(self.handle, "POST", "/api/watch", {"action": "stop"})
        listed = self.get("?addon=Kept")
        self.assertEqual([(v["id"], v["reason"]) for v in listed["versions"]], [(3, "save"), (2, "watch"), (1, "load")])
        self.assertEqual(listed["versions"][0]["note"], "Core.lua")
        self.assertTrue(listed["now"]["same"])
        diff = self.get("?addon=Kept&id=1")
        self.assertEqual((diff["base"], diff["to"]), (1, "now"))
        self.assertIn("-print(1)\n+print(3)", diff["files"][0]["diff"])
        prev = self.get("?addon=Kept&id=3&against=prev")
        self.assertIn("-print(2)\n+print(3)", prev["files"][0]["diff"])
        addons = {a["name"]: a for a in request(self.handle, "GET", f"/api/addons?game_dir={urllib.parse.quote(str(self.client))}")["addons"]}
        self.assertEqual(addons["Kept"]["history"]["versions"], 3)
        self.assertTrue(any("history: Kept #3 kept (save: Core.lua)" in e["text"]
                            for e in request(self.handle, "GET", "/api/logs?limit=1000")["entries"]))

    def test_checkpoint_restore_and_forget(self):
        self.addon("Undo")
        first = self.post({"action": "checkpoint", "addon": "Undo", "note": "the original"})
        self.assertEqual((first["id"], first["new"], first["reason"], first["note"]), (1, True, "manual", "the original"))
        self.assertFalse(self.post({"action": "checkpoint", "addon": "Undo"})["new"])
        self.edit("Undo", "broken(\n")
        (self.addons / "Undo" / "Extra.lua").write_text("x = 1\n", encoding="utf-8")
        res = self.post({"action": "restore", "addon": "Undo", "id": 1})
        self.assertEqual((res["restored"], res["saved"], res["written"], res["removed"]), (1, 2, ["Core.lua"], ["Extra.lua"]))
        self.assertIn("restore #2 undoes this", res["hint"])
        self.assertFalse(res["watched"])
        request(self.handle, "POST", "/api/watch", {"action": "start", "target": "Undo"})
        try:
            again = self.post({"action": "restore", "addon": "Undo", "id": 2})
        finally:
            request(self.handle, "POST", "/api/watch", {"action": "stop"})
        self.assertTrue(again["watched"])
        self.assertIn("loaded into the game again", again["hint"])
        self.post({"action": "restore", "addon": "Undo", "id": 1})
        self.assertEqual((self.addons / "Undo" / "Core.lua").read_text(encoding="utf-8"), "print(1)\n")
        self.assertFalse((self.addons / "Undo" / "Extra.lua").exists())
        self.assertEqual(self.refused("POST", "/api/history", {"action": "restore", "addon": "Undo", "id": 9}), 404)
        self.assertEqual(self.refused("POST", "/api/history", {"action": "restore", "addon": "Undo"}), 400)
        self.assertEqual(self.refused("POST", "/api/history", {"action": "checkpoint", "addon": "WoWBridge"}), 400)
        self.assertEqual(self.refused("POST", "/api/history", {"action": "checkpoint", "addon": "Nowhere"}), 404)
        self.assertEqual(self.refused("POST", "/api/history", {"action": "dance", "addon": "Undo"}), 400)
        self.assertEqual(self.refused("GET", "/api/history?addon=Undo&id=nine"), 400)
        self.assertIn("Undo", [r["addon"] for r in self.get("")["addons"]])
        self.assertTrue(self.post({"action": "forget", "addon": "Undo"})["forgotten"])
        self.assertEqual(self.get("?addon=Undo"), {"addon": "Undo", "versions": [], "now": None})
        self.assertEqual(self.refused("POST", "/api/history", {"action": "forget", "addon": "Undo"}), 404)

    def test_the_mcp_tools(self):
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        self.addon("ViaTools")

        async def go():
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {self.handle.token}"}) as http:
                async with Client(streamable_http_client(f"{self.handle.url.rstrip('/')}/mcp", http_client=http)) as c:
                    tools = {t.name: t for t in (await c.list_tools()).tools}
                    kept = await c.call_tool("checkpoint", {"addon": "ViaTools", "note": "before"})
                    self.edit("ViaTools", "print('new')\n")
                    listed = await c.call_tool("history", {"addon": "ViaTools"})
                    diff = await c.call_tool("history", {"addon": "ViaTools", "id": 1})
                    back = await c.call_tool("restore", {"addon": "ViaTools", "id": 1})
                    missing = await c.call_tool("restore", {"addon": "ViaTools", "id": 7})
                    return tools, kept, listed, diff, back, missing

        tools, kept, listed, diff, back, missing = asyncio.run(go())
        self.assertTrue(tools["history"].annotations.read_only_hint)
        self.assertFalse(tools["checkpoint"].annotations.destructive_hint)
        self.assertTrue(tools["restore"].annotations.destructive_hint)
        self.assertEqual(kept.structured_content["id"], 1)
        self.assertEqual(listed.structured_content["now"]["changed"], ["Core.lua"])
        self.assertIn("+print('new')", diff.structured_content["files"][0]["diff"])
        self.assertEqual(back.structured_content["written"], ["Core.lua"])
        self.assertTrue(missing.is_error)
        self.assertEqual((self.addons / "ViaTools" / "Core.lua").read_text(encoding="utf-8"), "print(1)\n")

    def test_the_command_line(self):
        self.addon("ViaCli")

        def run(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(list(argv))
            return code, out.getvalue()

        self.assertEqual(run("checkpoint", "ViaCli", "the", "original"), (0, "kept ViaCli #1 (2 files)\n"))
        self.assertIn("no change since #1", run("checkpoint", "ViaCli")[1])
        self.edit("ViaCli", "print(2)\n")
        code, out = run("history", "ViaCli")
        self.assertEqual(code, 0)
        self.assertIn("since #1: Core.lua changed", out)
        self.assertIn("#1    ", out)
        self.assertIn("manual", out)
        self.assertIn("+print(2)", run("history", "ViaCli", "1")[1])
        code, out = run("restore", "ViaCli", "1")
        self.assertIn("ViaCli is back to #1: 1 files written, 0 removed", out)
        self.assertIn("wuxian restore ViaCli 2 undoes this", out)
        self.assertIn("ViaCli", run("history")[1])
        self.assertEqual(run("restore", "ViaCli", "42")[0], 1)


if __name__ == "__main__":
    unittest.main()
