"""The development tools over a real daemon: the API manual (/api/apidocs and the MCP tools api_search / api_get /
api_manual), 新建插件 (/api/new_addon, MCP new_addon), /api/reveal's refusals, and `wuxian api` without a daemon."""
import asyncio
import contextlib
import io
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame, request
from wuxianworkshop.cli import main as cli
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server


class DevTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(cls.tmp.name) / "home")})
        cls.env.start()
        cls.addons = Path(cls.tmp.name) / "AddOns"
        (cls.addons / "WoWBridge").mkdir(parents=True)
        MB.install(cls.addons, pool=16)
        cls.handle = server.start(worker_factory=lambda on_debug, log: FakeGame(cls.addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "D")

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def get(self, path):
        return request(self.handle, "GET", path)

    def refused(self, method, path, body=None):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            request(self.handle, method, path, body)
        cm.exception.close()
        return cm.exception.code

    def test_the_manual_over_http(self):
        about = self.get("/api/apidocs")
        self.assertGreater(about["counts"]["function"], 5000)
        self.assertTrue(about["manual"])
        found = self.get("/api/apidocs?q=UnitHealth&limit=5")["results"]
        self.assertEqual(found[0]["name"], "UnitHealth")
        self.assertEqual(len(found), 5)
        self.assertTrue(all(r["kind"] == "event" for r in self.get("/api/apidocs?q=cooldown&kind=event")["results"]))
        entry = self.get("/api/apidocs?name=C_Spell.GetSpellInfo")
        self.assertEqual(entry["args"][0]["n"], "spellIdentifier")
        self.assertIn("md", self.get("/api/apidocs?manual=taint"))
        self.assertTrue(self.get("/api/apidocs?manual=")["topics"])
        self.assertEqual(self.refused("GET", "/api/apidocs?name=NoSuchApi"), 404)
        self.assertEqual(self.refused("GET", "/api/apidocs?q=x&kind=bogus"), 400)
        self.assertEqual(self.refused("GET", "/api/apidocs?q=x&call=bogus"), 400)
        found = self.get("/api/apidocs?q=Target&kind=function&call=protected")    # the page's search: a call class, the counts
        self.assertTrue(found["results"] and all(r["call"] == "protected" for r in found["results"]))
        self.assertEqual(set(found["counts"]), {"ok", "limited", "protected"})
        self.assertGreater(found["total"], len(found["results"]) - 1)
        self.assertIn(self.get("/api/apidocs?name=C_Spell.GetSpellInfo")["call"], ("ok", "limited"))
        self.assertIn("md", self.get("/api/apidocs?manual=taint&lang=en"))
        self.assertEqual(self.get("/api/status")["content"]["packs"]["api"]["source"], "bundled")
        # the browser: the rows, one namespace, a call class listed without a query, paging
        rows = self.get("/api/apidocs?systems=1")["systems"]
        self.assertEqual({r["group"] for r in rows}, {"namespace", "global", "object"})
        spell = self.get("/api/apidocs?system=C_Spell")
        self.assertTrue(spell["functions"] and all(f["name"].startswith("C_Spell.") for f in spell["functions"]))
        self.assertEqual(self.refused("GET", "/api/apidocs?system=C_Nothing"), 404)
        self.assertEqual(self.get("/api/apidocs?name=Frame:Hide")["call"], "limited")       # an object's method
        listed = self.get("/api/apidocs?call=protected&limit=5")
        self.assertTrue(listed["results"] and all(r["call"] == "protected" for r in listed["results"]))
        more = self.get("/api/apidocs?kind=function&limit=5&offset=5")
        self.assertEqual(len(more["results"]), 5)
        self.assertGreater(more["more"], 5000)

    def test_new_addon_and_reveal(self):
        res = request(self.handle, "POST", "/api/new_addon", {"name": "ViaApi", "title": "接口建的", "template": "window"})
        self.assertEqual(Path(res["path"]), self.addons / "ViaApi")
        self.assertTrue((self.addons / "ViaApi" / "AGENTS.md").is_file())
        self.assertIn("load ViaApi", res["hint"])
        self.assertEqual(self.refused("POST", "/api/new_addon", {"name": "ViaApi"}), 400)          # taken
        self.assertEqual(self.refused("POST", "/api/new_addon", {"name": "bad name"}), 400)
        self.assertEqual(self.refused("POST", "/api/reveal", {"name": "NotThere"}), 404)
        self.assertEqual(self.refused("POST", "/api/reveal", {"name": "../.."}), 404)            # only folders in AddOns
        with mock.patch.object(os, "startfile", create=True) as opened:
            self.assertTrue(request(self.handle, "POST", "/api/reveal", {"name": "ViaApi"})["opened"])
        opened.assert_called_once_with(self.addons / "ViaApi")

    def test_the_mcp_tools(self):
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        async def go():
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {self.handle.token}"}) as http:
                async with Client(streamable_http_client(f"{self.handle.url.rstrip('/')}/mcp", http_client=http)) as c:
                    tools = {t.name: t for t in (await c.list_tools()).tools}
                    search = await c.call_tool("api_search", {"query": "GetSpellInfo", "limit": 3})
                    entry = await c.call_tool("api_get", {"name": "C_Spell.GetSpellInfo"})
                    missing = await c.call_tool("api_get", {"name": "NoSuchApi"})
                    manual = await c.call_tool("api_manual", {"topic": "secret"})
                    made = await c.call_tool("new_addon", {"name": "ViaMcp"})
                    return tools, search, entry, missing, manual, made

        tools, search, entry, missing, manual, made = asyncio.run(go())
        for name in ("api_search", "api_get", "api_manual"):
            self.assertTrue(tools[name].annotations.read_only_hint, name)
        self.assertFalse(tools["new_addon"].annotations.read_only_hint)
        self.assertEqual(search.structured_content["results"][0]["name"], "C_Spell.GetSpellInfo")
        self.assertEqual(entry.structured_content["kind"], "function")
        self.assertTrue(missing.is_error)
        self.assertIn("机密值", manual.structured_content["title"])
        self.assertTrue((self.addons / "ViaMcp" / "ViaMcp.toc").is_file())
        self.assertFalse(made.is_error)

    def test_cli_api_needs_no_daemon(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["api", "UnitHealth", "--limit", "2"]), 0)
            self.assertEqual(cli.main(["api", "--manual", "toc"]), 0)
        text = out.getvalue()
        self.assertIn("UnitHealth(unit:", text)
        self.assertIn("# TOC 文件", text)

    def test_agents_over_http_and_the_command_line(self):
        """/api/agents and `wuxian agents` in a home of their own: Codex connected, Claude Code without its command line"""
        root = Path(self.tmp.name) / "agents"
        home, empty = root / "home", root / "bin"
        for d in (home / ".codex", empty):
            d.mkdir(parents=True)
        (root / "claude").mkdir()
        (root / "claude" / ".claude.json").write_text("{}", encoding="utf-8")
        env = {"USERPROFILE": str(home), "PATH": str(empty), "CLAUDE_CONFIG_DIR": str(root / "claude"),
               "APPDATA": str(home / "AppData" / "Roaming"), "LOCALAPPDATA": str(home / "AppData" / "Local"),
               "PROGRAMFILES": str(root / "Program Files")}
        with mock.patch.dict(os.environ, env):
            states = {h["id"]: h["state"] for h in self.get("/api/agents")["hosts"]}
            self.assertEqual(states, {"claude": "absent", "codex": "absent", "cursor": "missing", "trae-cn": "missing",
                                      "trae": "missing", "workbuddy": "missing", "workbuddy-ai": "missing"})
            made = request(self.handle, "POST", "/api/agents", {"action": "connect", "host": "codex"})
            self.assertEqual(made["state"], "ok")
            self.assertIn("[mcp_servers.wuxian]", (home / ".codex" / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual(self.refused("POST", "/api/agents", {"action": "connect", "host": "claude"}), 409)   # no claude command
            self.assertEqual(self.refused("POST", "/api/agents", {"action": "connect", "host": "vim"}), 400)
            self.assertEqual(self.refused("POST", "/api/agents", {"action": "dance", "host": "codex"}), 400)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["agents", "disconnect", "codex"]), 0)
            self.assertIn("还没有接入", out.getvalue())
            self.assertNotIn("wuxian", (home / ".codex" / "config.toml").read_text(encoding="utf-8"))
        self.assertTrue(any("agents: connect codex -> ok" in e["text"] for e in self.get("/api/logs")["entries"]))


if __name__ == "__main__":
    unittest.main()
