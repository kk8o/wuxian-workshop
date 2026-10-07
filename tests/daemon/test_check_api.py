"""The checks before the game over a real daemon (agent/lint.py behind /api/check, load and watch): a syntax error keeps
the code out of the game, the other findings come with load's answer, a watched save with a syntax error is not loaded;
the MCP tool and `wuxian check`."""
import asyncio
import contextlib
import io
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame, request
from wuxianworkshop.cli import main as cli
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server

TOC = "## Interface: 16001\n## SavedVariables: ChkDB\nCore.lua\n"


class Checks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(cls.tmp.name) / "home")})
        cls.env.start()
        cls.addons = Path(cls.tmp.name) / "AddOns"
        (cls.addons / "WoWBridge").mkdir(parents=True)
        MB.install(cls.addons, pool=64)
        cls.handle = server.start(worker_factory=lambda on_debug, log: FakeGame(cls.addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "C")

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def addon(self, name, core):
        folder = self.addons / name
        folder.mkdir()
        (folder / f"{name}.toc").write_text(TOC.replace("Chk", name), encoding="utf-8")
        return self.edit(name, core)

    def edit(self, name, core, later=0):
        p = self.addons / name / "Core.lua"
        p.write_text(core, encoding="utf-8")
        if later:
            t = time.time_ns() + later
            os.utime(p, ns=(t, t))
        return p

    def refused(self, path, body):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            request(self.handle, "POST", path, body)
        import json
        body = json.loads(cm.exception.read())
        cm.exception.close()
        return cm.exception.code, body["error"]

    def logs(self):
        return [e["text"] for e in request(self.handle, "GET", "/api/logs?limit=1000")["entries"]]

    def test_check_and_load(self):
        self.addon("ChkA", "local t = {}\ncounter = 1\nlocal x = os.time()\nprint(SomethingElse)\n")
        r = request(self.handle, "POST", "/api/check", {"target": "ChkA", "live": False})
        self.assertEqual({(f["code"], f["line"]) for f in r["errors"]}, {("missing-lib", 3)})
        self.assertEqual({(f["code"], f["line"]) for f in r["warnings"]}, {("global-write", 2)})
        self.assertEqual(r["unresolved"], {"SomethingElse": ["Core.lua:4"]})
        self.assertNotIn("live", r)
        live = request(self.handle, "POST", "/api/check", {"target": "ChkA"})   # the fake game does not answer JSON
        self.assertIn("error", live["live"])
        self.assertEqual(live["unresolved"], {"SomethingElse": ["Core.lua:4"]})  # kept: nothing settled it
        res = request(self.handle, "POST", "/api/load", {"target": "ChkA"})     # sent, with what the check found
        self.assertTrue(res["ok"])
        self.assertEqual({f["code"] for f in res["check"]["errors"] + res["check"]["warnings"]}, {"missing-lib", "global-write"})
        self.edit("ChkA", "local t = {}\nlocal x = 7 // 2\n")
        status, err = self.refused("/api/load", {"target": "ChkA"})
        self.assertEqual((status, err["code"]), (422, "check_failed"))
        self.assertIn("load ChkA: not sent, Core.lua:2: unexpected symbol near '/'", err["message"])
        self.assertIn("math.floor(a / b)", err["message"])
        self.assertEqual(err["check"]["errors"][0]["code"], "syntax")
        res = request(self.handle, "POST", "/api/load", {"target": "ChkA", "check": False})   # asked to send it anyway
        self.assertIsNone(res["check"])
        self.assertEqual(self.refused("/api/check", {"target": "Nowhere"})[0], 404)

    def test_a_broken_save_is_not_watched_into_the_game(self):
        core = self.addon("ChkW", "local a = 1\n")
        request(self.handle, "POST", "/api/watch", {"action": "start", "target": "ChkW"})
        try:
            self.edit("ChkW", "local a = = 1\n", later=10 ** 9)
            deadline = time.time() + 15
            while time.time() < deadline and not any("not loaded" in t for t in self.logs()):
                time.sleep(0.2)
            text = next(t for t in self.logs() if "not loaded" in t)
            self.assertEqual(text, "ChkW/Core.lua:1: unexpected symbol near '=': not loaded, fix it and save again")
            self.assertFalse(any("Core.lua was saved: loading it" in t for t in self.logs()))
            self.edit("ChkW", "local a = 2\n", later=2 * 10 ** 9)
            deadline = time.time() + 15
            while time.time() < deadline and not any("Core.lua was saved: loading it" in t for t in self.logs()):
                time.sleep(0.2)
            self.assertTrue(any("Core.lua was saved: loading it" in t for t in self.logs()))
        finally:
            request(self.handle, "POST", "/api/watch", {"action": "stop"})
        self.assertTrue(core.exists())

    def test_the_mcp_tool_and_the_command_line(self):
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        self.addon("ChkM", "local hp = UnitHelth(\"player\")\n")

        async def go():
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {self.handle.token}"}) as http:
                async with Client(streamable_http_client(f"{self.handle.url.rstrip('/')}/mcp", http_client=http)) as c:
                    tools = {t.name: t for t in (await c.list_tools()).tools}
                    found = await c.call_tool("check", {"target": "ChkM", "live": False})
                    return tools, found

        tools, found = asyncio.run(go())
        self.assertTrue(tools["check"].annotations.read_only_hint)
        self.assertIn("check", tools["load"].input_schema["properties"])
        typo = found.structured_content["warnings"][0]
        self.assertEqual((typo["code"], typo["hint"]), ("typo", "did you mean UnitHealth?"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["check", "ChkM", "--no-live"])
        self.assertEqual(code, 0)                                          # warnings only
        self.assertIn("warning Core.lua:1: UnitHelth is neither the client's nor this addon's (did you mean UnitHealth?)",
                      out.getvalue())
        self.assertIn("ChkM: 1 files, 0 errors, 1 warnings", out.getvalue())


if __name__ == "__main__":
    unittest.main()
