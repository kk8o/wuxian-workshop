"""The Blizzard UI index and what the game answered, as the agent gets them (mcp/server.py, daemon/service.py): ui_search
and ui_get over a fixture pack (tests/agent/test_uidocs.py), api_verify asking the game through the backend (a stand-in
run answers) and keeping its answers per build (seen.py), api_search / api_get / ui_get showing `seen`, api_get pointing
to ui_get, the check settling a kept name offline and keeping what its live step learnt; the three tools read-only, and
a missing pack answered as not installed."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mcp.server.mcpserver.exceptions import ToolError

from tests.agent.test_uidocs import PACK
from wuxianworkshop import seen, uidocs
from wuxianworkshop.agent import lint
from wuxianworkshop.cli import client as cli_client
from wuxianworkshop.daemon import server as daemon
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.mcp import server


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(self.tmp.name) / "home")})
        self.env.start()
        self.ui = mock.patch.object(uidocs, "index", return_value=uidocs.UiIndex(json.loads(json.dumps(PACK))))
        self.ui.start()
        addons = Path(self.tmp.name) / "AddOns"
        (addons / "SeenChk").mkdir(parents=True)
        (addons / "SeenChk" / "SeenChk.toc").write_text("## Interface: 16001\nCore.lua\n", encoding="utf-8")
        (addons / "SeenChk" / "Core.lua").write_text("local a = FooFrameOfMine\nZapIt()\nlocal ej = EncounterJournal\n",
                                                     encoding="utf-8")
        self.game = dict(build=71037, version="1.60.1.71037")
        self.svc = Service(lambda on_debug, log: SimpleNamespace(addons=str(addons), comp=None,
                                                                 game_status=lambda: dict(self.game)), journal=Journal())
        self.asked, self.answer = [], {}

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            self.asked.append(code)
            return dict(ok=True, values=["1/1\n" + json.dumps(self.answer)])
        self.svc.run = run

    def tearDown(self):
        self.ui.stop()
        self.env.stop()
        self.tmp.cleanup()

    def online(self, up=True):
        self.svc.worker.comp = SimpleNamespace(link_up=up, current=5)

    def tool(self, mcp, name, args):
        return asyncio.run(mcp.call_tool(name, args)).structured_content


class Verify(Base):
    def test_verify_keeps_the_answers(self):
        self.online()
        self.answer = {"TooltipDataProcessor": {"type": "table", "members": ["AddTooltipPostCall", "AllTypes"],
                                                "count": 5},
                       "EncounterJournal.encounter.info": {"type": "nil"}}
        got = asyncio.run(self.svc.verify(["TooltipDataProcessor", "EncounterJournal.encounter.info", "Gone:x"], True))
        self.assertIn('"EncounterJournal.encounter.info"', self.asked[0])
        self.assertIn("local MEMBERS, EACH, BYTES = true, 200,", self.asked[0])
        self.assertEqual(got["client"], "1.60.1.71037")
        self.assertEqual(got["build"], "71037")
        self.assertEqual(got["answers"], {
            "TooltipDataProcessor": {"type": "table", "members": ["AddTooltipPostCall", "AllTypes"], "more": 3},
            "EncounterJournal.encounter.info": {"type": "nil"},
            "Gone:x": {"type": None, "error": "no answer for it"}})
        kept = seen.get(71037, "TooltipDataProcessor")
        self.assertEqual((kept["type"], kept["members"], kept["more"]), ("table", ["AddTooltipPostCall", "AllTypes"], 3))
        self.assertEqual(seen.get(71037, "EncounterJournal.encounter.info")["type"], "nil")
        self.assertIsNone(seen.get(71037, "Gone:x"))
        self.answer = {"PLAYER_SWING": {"type": "event", "valid": True}, "NO_SUCH_EVENT": {"type": "event", "valid": False}}
        got = asyncio.run(self.svc.verify(["PLAYER_SWING", "NO_SUCH_EVENT"]))
        self.assertEqual(got["answers"], {"PLAYER_SWING": {"type": "event", "valid": True},
                                          "NO_SUCH_EVENT": {"type": "event", "valid": False}})
        self.assertEqual((seen.get(71037, "PLAYER_SWING")["valid"], seen.get(71037, "NO_SUCH_EVENT")["valid"],
                          seen.get(71037, "PLAYER_SWING")["level"]), (True, False, "presence"))
        self.game["build"] = 71100                                           # another client: its own file
        self.answer = {"X": {"type": "function"}}
        asyncio.run(self.svc.verify(["X"]))
        self.assertIn("local MEMBERS, EACH, BYTES = false,", self.asked[-1])
        self.assertEqual(seen.get(71100, "X")["type"], "function")
        self.assertIsNone(seen.get(71037, "X"))

    def test_bad_names(self):
        for names in ([], ["a b"], ["os.exit()"], "Foo", ["x"] * 101, [1], ["A" * 129]):
            with self.assertRaises(ApiError) as e:
                asyncio.run(self.svc.verify(names))
            self.assertEqual(e.exception.code, "bad_request", names)
        self.assertEqual(self.asked, [])

    def test_the_lua_reads_only(self):
        """the chunk walks the names with rawget (and a metatable's __index table): no __index function runs"""
        rt = lint._runtime()
        if rt is None:
            self.skipTest("no lupa")
        from wuxianworkshop.agent import probes
        rt.execute(b"Methods = {SetOwner = function() end}\n"
                   b"Tip = setmetatable({x = 1, child = {info = {}}}, {__index = Methods})\n"
                   b"Trap = setmetatable({}, {__index = function() error('ran') end})\n"
                   b"Big = {} for i = 1, 300 do Big['k' .. i] = i end\n"
                   b"NUM_BAG_SLOTS = 4\n")
        names = ["Tip", "Tip:SetOwner", "Tip.child.info", "Trap.x", "Nope.x", "Big", "PLAYER_SWING", "NUM_BAG_SLOTS",
                 "NO_SUCH_EVENT"]
        code = probes.verify(names, True, 200, 1000)
        got = json.loads(rt.execute(code.encode()))
        self.assertEqual(got["PLAYER_SWING"], {"type": "nil"})             # no C_EventUtils: a global's answer
        rt.execute(b"C_EventUtils = {IsEventValid = function(e) return e == 'PLAYER_SWING' end}\n")
        events = json.loads(rt.execute(code.encode()))
        rt.execute(b"C_EventUtils = nil\n")
        self.assertEqual(events["PLAYER_SWING"], {"type": "event", "valid": True})
        self.assertEqual(events["NO_SUCH_EVENT"], {"type": "event", "valid": False})
        self.assertEqual(events["NUM_BAG_SLOTS"], {"type": "number"})     # a global by that name: its type
        self.assertEqual(events["Tip"]["type"], "table")
        self.assertEqual(got["Tip"], {"type": "table", "members": ["child", "x"], "count": 2})
        self.assertEqual((got["Tip:SetOwner"]["type"], got["Tip.child.info"]["type"], got["Trap.x"]["type"],
                          got["Nope.x"]["type"]), ("function", "table", "nil", "nil"))
        self.assertEqual(got["Big"]["count"], 300)
        self.assertLess(len(got["Big"]["members"]), 200)                    # the bytes ran out first


class CheckKeeps(Base):
    def test_offline_from_seen_and_the_live_step_keeps(self):
        self.online(False)
        r = asyncio.run(self.svc.check("SeenChk"))
        self.assertEqual(sorted(r["unresolved"]), ["FooFrameOfMine", "ZapIt"])
        self.assertEqual([n["code"] for n in r["notes"] if n["code"] == "blizzard-ui"], ["blizzard-ui"])
        self.assertIn("error", r["live"])
        seen.record(71037, {"ZapIt": "nil"})
        r = asyncio.run(self.svc.check("SeenChk"))
        self.assertEqual(r["unresolved"], {"FooFrameOfMine": ["Core.lua:1"]})
        self.assertEqual([(f["code"], f["line"]) for f in r["errors"]], [("undefined", 2)])
        self.assertEqual(r["seen"], {"settled": 1})
        self.online()
        self.answer = {"FooFrameOfMine": "table"}
        r = asyncio.run(self.svc.check("SeenChk"))
        self.assertIn('"FooFrameOfMine"', self.asked[-1])
        self.assertNotIn('"ZapIt"', self.asked[-1])                          # settled from seen: not asked again
        self.assertNotIn('"EncounterJournal"', self.asked[-1])               # the Blizzard UI defines it
        self.assertEqual((r["unresolved"], r["live"]), ({}, {"checked": 1}))
        self.assertEqual(seen.get(71037, "FooFrameOfMine")["type"], "table")
        pre = self.svc.precheck(Path(self.svc.worker.addons) / "SeenChk", None)   # load's check reads seen too
        self.assertEqual((pre["unresolved"], [f["code"] for f in pre["errors"]]), ([], ["undefined"]))


class Tools(Base):
    def test_the_tools(self):
        mcp = server.build_server(self.svc)
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
        for name in ("ui_search", "ui_get", "api_verify"):
            self.assertTrue(tools[name].annotations.read_only_hint, name)
            self.assertLess(len(tools[name].description), 1200, name)
        self.assertIn("on_demand", tools["ui_search"].description)
        self.assertIn("LoadAddOn", tools["ui_search"].description)
        self.assertIn("seen in the game", tools["api_verify"].description)
        self.assertIn("C_EventUtils.IsEventValid", tools["api_verify"].description)
        self.assertEqual(tools["api_verify"].input_schema["properties"]["names"]["type"], "array")
        self.assertEqual(sorted(tools["ui_search"].input_schema["properties"]), ["kind", "limit", "query"])
        self.assertLessEqual(len(server.INSTRUCTIONS), 2000)

    def test_search_get_and_seen(self):
        mcp = server.build_server(self.svc)
        found = self.tool(mcp, "ui_search", {"query": "TooltipDataProcessor"})
        self.assertEqual((found["index"], found["client"]), ("2026.11.24.1", "1.60.1.71037"))
        self.assertEqual(found["results"][0]["name"], "TooltipDataProcessor")
        self.assertEqual(self.tool(mcp, "ui_search", {"query": "Secure", "kind": "template"})["results"][0]["kind"],
                         "template")
        entry = self.tool(mcp, "ui_get", {"name": "EncounterJournal"})
        self.assertEqual(entry["load"], "on_demand")
        self.assertNotIn("seen", entry)
        with self.assertRaisesRegex(ToolError, "not_found: Nope is not in Blizzard's UI index; ui_search"):
            asyncio.run(mcp.call_tool("ui_get", {"name": "Nope"}))
        with self.assertRaisesRegex(ToolError, r"not_found: StoreUIFrame is not in Blizzard's UI index; "
                                               r"Blizzard_StoreUI is not loaded by this client \(game type standard\)"):
            asyncio.run(mcp.call_tool("ui_get", {"name": "StoreUIFrame"}))
        gone = self.tool(mcp, "ui_search", {"query": "StoreUI"})
        self.assertEqual((gone["results"], gone["not_loaded"]),
                         ([], "Blizzard_StoreUI is not loaded by this client (game type standard)"))
        self.assertNotIn("not_loaded", found)
        self.online()
        self.answer = {"EncounterJournal": {"type": "nil"}, "C_Spell.GetSpellInfo": {"type": "function"},
                       "EncounterJournal.encounter.info": {"type": "table", "members": [], "count": 0}}
        got = self.tool(mcp, "api_verify", {"names": ["EncounterJournal", "C_Spell.GetSpellInfo",
                                                      "EncounterJournal.encounter.info"]})
        self.assertEqual(got["answers"]["EncounterJournal"], {"type": "nil"})
        self.assertEqual(self.tool(mcp, "ui_get", {"name": "EncounterJournal"})["seen"]["type"], "nil")
        self.assertEqual(self.tool(mcp, "ui_get", {"name": "EncounterJournal.encounter.info"})["seen"]["type"], "table")
        api = self.tool(mcp, "api_get", {"name": "C_Spell.GetSpellInfo"})
        self.assertEqual(api["seen"]["type"], "function")
        search = self.tool(mcp, "api_search", {"query": "C_Spell.GetSpellInfo", "limit": 3})["results"]
        self.assertEqual([r.get("seen", {}).get("type") for r in search if r["name"] == "C_Spell.GetSpellInfo"],
                         ["function"])
        with self.assertRaisesRegex(ToolError, "api_verify: bad_request"):
            asyncio.run(mcp.call_tool("api_verify", {"names": []}))

    def test_api_get_points_to_ui_get(self):
        mcp = server.build_server(self.svc)
        with self.assertRaisesRegex(ToolError, r'not_found: TooltipDataProcessor is not in the API manual \(the C API\); '
                                               r'Blizzard\'s UI defines it: ui_get \{"name": "TooltipDataProcessor"\}'):
            asyncio.run(mcp.call_tool("api_get", {"name": "TooltipDataProcessor"}))
        with self.assertRaisesRegex(ToolError, "ui_search Blizzard's UI"):
            asyncio.run(mcp.call_tool("api_get", {"name": "NoSuchApiAnywhere"}))

    def test_not_installed(self):
        mcp = server.build_server(self.svc)
        with mock.patch.object(uidocs, "index", return_value=uidocs.UiIndex(None)):
            for name, args in (("ui_search", {"query": "x"}), ("ui_get", {"name": "x"})):
                with self.assertRaisesRegex(ToolError, "not_installed"):
                    asyncio.run(mcp.call_tool(name, args))
            with self.assertRaisesRegex(ToolError, "not_found: NoSuchApiAnywhere is not in the API manual"):
                asyncio.run(mcp.call_tool("api_get", {"name": "NoSuchApiAnywhere"}))

    def test_over_http(self):
        """the stdio server asks the daemon (POST /api/verify) and reads seen for the newest build"""
        self.assertTrue(hasattr(cli_client.DaemonClient, "verify"))
        posted = []

        class Client:
            def verify(self, names, members=False):
                posted.append((names, members))
                return {"client": None, "build": "71037", "answers": {n: {"type": "nil"} for n in names}}
        mcp = server.build_server(server.HttpBackend(Client()))
        self.assertEqual(self.tool(mcp, "api_verify", {"names": ["A"], "members": True})["answers"], {"A": {"type": "nil"}})
        self.assertEqual(posted, [(["A"], True)])
        seen.record(seen.current(), {"TooltipDataProcessor": "table"})
        self.assertEqual(self.tool(mcp, "ui_get", {"name": "TooltipDataProcessor"})["seen"]["type"], "table")
        self.assertIn('Route("/api/verify", verify, methods=["POST"])',            # the daemon's route for it
                      Path(daemon.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
