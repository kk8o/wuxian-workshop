"""The 扩展 page's Agent 接入: who uses the MCP servers and whether they got WuxianKit's tools. Presence (a server
middleware) keeps one record per agent in both eras of the protocol (on the 2026-07-28 wire every request is a
connection of its own), with what its last tool list gave it; a stdio server reports its record to the daemon
(server.report, Reporter); the daemon lists the game's WuxianKit as tools and a row per agent with its state
(/api/agent_access)."""
import asyncio
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mcp_types import CLIENT_INFO_META_KEY

from tests.daemon.test_server import MUTEX, FakeGame, request
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server as daemon
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.mcp import presence, server

WK = ["wk_docs", "wk_kit_undo", "wk_propose", "wk_tune_cvar_get", "wk_tune_cvar_set", "wk_tune_profile_apply", "wk_wait"]


def manifest(revision="r1"):
    return {"protocol": 1, "kit": "0.4.0", "language": "zhCN", "revision": revision, "extensions": [
        {"id": "kit", "title": "核心", "version": "0.4.0", "addon": "WuxianKit", "builtin": True, "state": "on",
         "capabilities": [{"id": "kit.manifest", "kind": "see", "title": "能力清单", "doc": "", "args": {}},
                          {"id": "kit.revision", "kind": "see", "title": "清单修订号", "doc": "", "args": {}},
                          {"id": "kit.undo", "kind": "do", "title": "撤销", "doc": "undo", "args": {"id": "number"}}]},
        {"id": "tune", "title": "调校", "version": "0.4.0", "addon": "WuxianKit", "builtin": True, "state": "on",
         "capabilities": [{"id": "tune.cvar.get", "kind": "see", "title": "客户端设置", "doc": "read", "args": {"name": "string"}},
                          {"id": "tune.cvar.set", "kind": "do", "title": "客户端设置", "doc": "change",
                           "args": {"name": "string", "value": "string"}},
                          {"id": "tune.profile.apply", "kind": "do", "proposes": True, "title": "应用配置档", "doc": "apply",
                           "args": {"name": "string"}}]},
        {"id": "gate", "title": "远程", "version": "0.4.0", "addon": "WuxianKit", "builtin": True, "state": "off"},
    ]}


def answer(game, name):
    """the game's answer to a call of WuxianKit, as Service.call_exposed gives it"""
    if name == "kit.revision":
        if game.manifest is None:
            raise ApiError(400, "call_failed", "WuxianKit.kit.revision is not exposed")
        return {"ok": True, "result": {"revision": game.manifest["revision"]}}
    if name == "kit.manifest":
        return {"ok": True, "result": game.manifest}
    if name == "kit.capabilities":
        raise ApiError(400, "call_failed", "WuxianKit.kit.capabilities is not exposed")
    game.calls.append(name)
    return {"ok": True, "result": {"value": "1"}}


class Game:
    """the daemon's Service as KitTools calls it (the daemon's own /mcp)"""

    def __init__(self, manifest=None):
        self.manifest, self.calls = manifest, []

    async def status(self):
        return {"addons_dir": None}

    async def call_exposed(self, addon, name, args=None, timeout_ms=10000):
        return answer(self, name)

    async def addon_events(self, addon=None, topic=None, since=0, limit=100, wait=0):
        return {"events": [], "next": 1}


class Daemon(Game):
    """cli.client's DaemonClient as HttpBackend calls it (a stdio server), with what it posted"""

    def __init__(self, manifest=None):
        super().__init__(manifest)
        self.posted = []

    def status(self):
        return {"addons_dir": None}

    def call_exposed(self, addon, name, args=None, timeout_ms=10000):
        return answer(self, name)

    def addon_events(self, addon=None, topic=None, since=0, limit=100, wait=0):
        return {"events": [], "next": 1}

    def post(self, path, timeout=None, **body):
        self.posted.append((path, timeout, body))
        return {"ok": True}


def talk(mcp, *agents):
    """each agent (mode, name, version) lists the tools and calls wk_tune_cvar_get, in process; the names it got"""
    from mcp import Client
    from mcp_types import Implementation

    async def go():
        got = []
        for mode, name, version in agents:
            async with Client(mcp, mode=mode, client_info=Implementation(name=name, version=version), cache=None) as c:
                got.append([t.name for t in (await c.list_tools()).tools])
                await c.call_tool("wk_tune_cvar_get", {"name": "x"})
        return got
    return asyncio.run(go())


class Home(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": self.home.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()


class Agents(Home):
    def test_modern_requests_are_one_agent(self):
        mcp = server.build_server(Game(manifest()))
        self.assertFalse(mcp.presence.single)                                  # the daemon's /mcp: by client
        names = talk(mcp, ("2026-07-28", "claude-code", "2.1.293"))[0]
        self.assertEqual(sorted(n for n in names if n.startswith("wk_")), WK)
        [agent] = mcp.presence.view()
        self.assertEqual((agent["client"], agent["protocol"], agent["calls"], agent["tools"], agent["revision"]),
                         ({"name": "claude-code", "version": "2.1.293"}, "2026-07-28", 1, len(WK), "r1"))
        self.assertLessEqual(agent["first"], agent["listed"])
        self.assertLessEqual(agent["listed"], agent["last"])

    def test_the_handshake_era(self):
        mcp = server.build_server(Game(manifest()))
        talk(mcp, ("legacy", "cursor", "1.7"))
        [agent] = mcp.presence.view()
        self.assertEqual((agent["client"], agent["calls"], agent["tools"]), ({"name": "cursor", "version": "1.7"}, 1, len(WK)))
        self.assertNotEqual(agent["protocol"], "2026-07-28")

    def test_two_clients(self):
        mcp = server.build_server(Game(manifest()))
        talk(mcp, ("2026-07-28", "claude-code", "2.1.293"), ("legacy", "cursor", "1.7"), ("2026-07-28", "claude-code", "2.1.293"))
        agents = mcp.presence.view()
        self.assertEqual([(a["client"]["name"], a["calls"]) for a in agents], [("claude-code", 2), ("cursor", 1)])

    def test_a_tool_list_without_wuxiankit(self):
        mcp = server.build_server(Game(None))
        from mcp import Client
        from mcp_types import Implementation

        async def go():
            async with Client(mcp, mode="2026-07-28", client_info=Implementation(name="codex", version="1"), cache=None) as c:
                await c.list_tools()
        asyncio.run(go())
        [agent] = mcp.presence.view()
        self.assertEqual((agent["tools"], agent["revision"], agent["calls"]), (0, None, 0))
        self.assertIsNotNone(agent["listed"])


class Middleware(unittest.TestCase):
    """Presence by itself, with requests as the SDK hands them over"""

    def ctx(self, method="tools/call", client=None, request_id=1, params=None):
        params = dict(params or {})
        if client:
            params["_meta"] = {CLIENT_INFO_META_KEY: client}
        return SimpleNamespace(request_id=request_id, method=method, params=params, protocol_version="2026-07-28",
                               session=SimpleNamespace(client_params=None))

    def send(self, p, *ctxs):
        async def call_next(ctx):
            return {"tools": [{"name": "wk_a"}, {"name": "wk_b"}, {"name": "run"}]} if ctx.method == "tools/list" else {}

        async def go():
            for ctx in ctxs:
                await p(ctx, call_next)
        asyncio.run(go())
        return p.view()

    def test_a_stdio_server_serves_one_agent(self):
        p = presence.Presence(single=True)
        a, b = {"name": "claude-code", "version": "2"}, {"name": "trae", "version": "1.9"}
        [agent] = self.send(p, self.ctx(client=a), self.ctx("ping"), self.ctx("tools/list", client=b))
        self.assertEqual((agent["client"], agent["calls"], agent["tools"]), (b, 1, 2))

    def test_by_client_over_http(self):
        p = presence.Presence()
        a, b = {"name": "claude-code", "version": "2"}, {"name": "claude-code", "version": "3"}
        agents = self.send(p, self.ctx(client=a), self.ctx(client=b), self.ctx(client=a), self.ctx())
        self.assertEqual([(x["client"], x["calls"]) for x in agents], [(a, 2), (b, 1), (None, 1)])

    def test_notifications_and_odd_clients(self):
        p = presence.Presence()
        self.assertEqual(self.send(p, self.ctx("notifications/initialized", request_id=None), self.ctx("ping")), [])
        [agent] = self.send(p, self.ctx(client={"name": ""}), self.ctx(client={"version": "1"}), self.ctx(client="x"))
        self.assertEqual((agent["client"], agent["calls"]), (None, 3))

    def test_initialize_names_its_client(self):
        p = presence.Presence()
        [agent] = self.send(p, self.ctx("initialize", params={"clientInfo": {"name": "cursor", "version": 7}}))
        self.assertEqual(agent["client"], {"name": "cursor", "version": None})

    def test_a_later_page_adds(self):
        p = presence.Presence(single=True)
        [agent] = self.send(p, self.ctx("tools/list"), self.ctx("tools/list", params={"cursor": "2"}))
        self.assertEqual(agent["tools"], 4)
        [agent] = self.send(p, self.ctx("tools/list"))
        self.assertEqual(agent["tools"], 2)

    def test_idle_clients_drop_out(self):
        p, q = presence.Presence(), presence.Presence(single=True)
        self.send(p, self.ctx(client={"name": "a"}))
        self.send(q, self.ctx(client={"name": "a"}))
        later = time.time() + presence.Presence.IDLE + 1
        with mock.patch.object(presence.time, "time", return_value=later):
            self.assertEqual((p.view(), len(q.view())), ([], 1))              # a stdio agent lasts as its process


class Report(Home):
    def test_what_a_stdio_server_reports(self):
        mcp = server.build_server(server.HttpBackend(Daemon(manifest())))
        self.assertTrue(mcp.presence.single)
        before = server.report(mcp)
        self.assertEqual((before["pid"], before["version"], before["agents"], before["kit"]),
                         (os.getpid(), server.__version__, [], None))
        self.assertIsInstance(before["source_changed"], bool)
        talk(mcp, ("2026-07-28", "claude-code", "2.1.293"))
        after = server.report(mcp)
        self.assertEqual(after["kit"], {"tools": len(WK), "revision": "r1", "protocol": 1})
        [agent] = after["agents"]
        self.assertEqual((agent["client"]["name"], agent["tools"], agent["revision"]), ("claude-code", len(WK), "r1"))

    def test_the_reporter(self):
        client = Daemon(manifest())
        mcp = server.build_server(server.HttpBackend(client))
        with mock.patch.object(server.Reporter, "FIRST", 0.01), mock.patch.object(server.Reporter, "EVERY", 0.01):
            reporter = server.Reporter(server.HttpBackend(client), mcp)
            reporter.start()
            deadline = time.time() + 5
            while len(client.posted) < 2 and time.time() < deadline:
                time.sleep(0.01)
            reporter.stopped.set()
            reporter.join(5)
        path, timeout, body = client.posted[0]
        self.assertEqual((path, timeout, body["pid"], body["version"]), ("/api/mcp/report", 5, os.getpid(), server.__version__))
        self.assertGreaterEqual(len(client.posted), 2)

    def test_a_daemon_away(self):
        class Away(Daemon):
            def post(self, path, timeout=None, **body):
                self.posted.append(path)
                raise ConnectionError("nobody listening")
        client = Away(manifest())
        mcp = server.build_server(server.HttpBackend(client))
        with mock.patch.object(server.Reporter, "FIRST", 0.01), mock.patch.object(server.Reporter, "EVERY", 0.01):
            reporter = server.Reporter(server.HttpBackend(client), mcp)
            reporter.start()
            deadline = time.time() + 5
            while len(client.posted) < 2 and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(reporter.is_alive())                                # it tries again later
            reporter.stopped.set()
            reporter.join(5)

    def test_a_daemon_restarted(self):
        """a report that failed looks for the daemon that runs now (another port and token after an update; never
        starting one), and the next goes there"""
        class Old(Daemon):
            def post(self, path, timeout=None, **body):
                raise ConnectionError("nobody listening")
        new, starts = Daemon(manifest()), []
        backend = server.HttpBackend(Old(manifest()), reconnect=lambda start=True: (starts.append(start), new)[1])
        mcp = server.build_server(backend)
        with mock.patch.object(server.Reporter, "FIRST", 0.01), mock.patch.object(server.Reporter, "EVERY", 0.01):
            reporter = server.Reporter(backend, mcp)
            reporter.start()
            deadline = time.time() + 5
            while not new.posted and time.time() < deadline:
                time.sleep(0.01)
            reporter.stopped.set()
            reporter.join(5)
        self.assertEqual((new.posted[0][0], starts, backend.client), ("/api/mcp/report", [False], new))


class AgentAccessApi(unittest.TestCase):
    """over a real daemon: WuxianKit's tools as the page lists them, the rows of the stdio servers' reports with their
    states, the App's own /mcp agent; a report silent too long goes"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(cls.tmp.name) / "home")})
        cls.env.start()
        cls.addons = Path(cls.tmp.name) / "client" / "Interface" / "AddOns"
        (cls.addons / "WoWBridge").mkdir(parents=True)
        MB.install(cls.addons, pool=64)
        cls.handle = daemon.start(worker_factory=lambda on_debug, log: FakeGame(cls.addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "A")
        cls.game = Game(manifest())
        cls.handle.service.call_exposed = cls.game.call_exposed            # WuxianKit in the game, for both KitTools

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.handle.service.mcp_reports.clear()

    def report(self, pid, version=None, agents=(), kit=None, source_changed=False):
        version = version or self.handle.service.version
        return request(self.handle, "POST", "/api/mcp/report", dict(
            pid=pid, version=version, started=round(time.time()) - 60, source_changed=source_changed, agents=list(agents),
            kit=kit if kit is not None else {"tools": len(WK), "revision": "r1", "protocol": 1}))

    def agent(self, tools=len(WK), revision="r1", name="claude-code", listed=True):
        now = time.time()
        return dict(client={"name": name, "version": "2.1.293"}, protocol="2026-07-28", first=now - 30, last=now, calls=2,
                    listed=now - 20 if listed else None, tools=tools if listed else None, revision=revision if listed else None)

    def stdio_rows(self):
        return {r["pid"]: r for r in request(self.handle, "GET", "/api/agent_access")["servers"] if r["kind"] == "stdio"}

    def test_the_kit_as_tools(self):
        kit = request(self.handle, "GET", "/api/agent_access?refresh=1")["kit"]
        self.assertEqual((kit["version"], kit["revision"], kit["tools"], kit["source"], kit["newer"], kit["language"]),
                         ("0.4.0", "r1", len(WK), "game", False, "zhCN"))
        exts = {e["id"]: e for e in kit["extensions"]}
        self.assertEqual([e["id"] for e in kit["extensions"]], ["kit", "tune", "gate"])
        self.assertEqual([(t["name"], t["label"]) for t in exts["kit"]["tools"]],
                         [("wk_kit_undo", "Change"), ("wk_docs", "Read"), ("wk_wait", "Read"), ("wk_propose", "Proposal")])
        self.assertEqual([(t["name"], t["cap"], t["label"], t["title"]) for t in exts["tune"]["tools"]],
                         [("wk_tune_cvar_get", "tune.cvar.get", "Read", "客户端设置"),
                          ("wk_tune_cvar_set", "tune.cvar.set", "Change", "客户端设置"),
                          ("wk_tune_profile_apply", "tune.profile.apply", "Proposal", "应用配置档")])
        self.assertEqual((exts["gate"]["state"], exts["gate"]["tools"]), ("off", []))
        self.assertEqual(sum(len(e["tools"]) for e in kit["extensions"]), kit["tools"])

    def test_the_states(self):
        older = "0.0.1"
        self.report(101, older, [self.agent()])
        self.report(102, agents=[self.agent()])
        self.report(103, agents=[self.agent(tools=0, revision=None)])
        self.report(104, agents=[self.agent(revision="r0")])
        self.report(105, source_changed=True, agents=[self.agent()])
        self.report(106)                                                       # no agent has talked to it yet
        self.report(107, agents=[self.agent(listed=False)], kit={"tools": len(WK), "revision": "r0"})
        rows = self.stdio_rows()
        self.assertEqual({pid: r["state"] for pid, r in rows.items()},
                         {101: "stale", 102: "current", 103: "no_kit", 104: "behind", 105: "stale", 106: "current",
                          107: "behind"})
        self.assertEqual((rows[102]["client"], rows[102]["calls"], rows[102]["tools"], rows[102]["protocol"]),
                         ({"name": "claude-code", "version": "2.1.293"}, 2, len(WK), "2026-07-28"))
        self.assertEqual((rows[106]["client"], rows[106]["tools"], rows[106]["last"]), (None, len(WK), None))

    def test_a_report_silent_too_long_goes(self):
        self.report(201, agents=[self.agent()])
        self.assertIn(201, self.stdio_rows())
        with mock.patch.object(type(self.handle.service), "MCP_GONE", -1):
            self.assertNotIn(201, self.stdio_rows())
        self.assertNotIn(201, self.stdio_rows())                              # forgotten, not just hidden

    def test_a_bad_report(self):
        for body in ({}, {"pid": "12"}, []):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                request(self.handle, "POST", "/api/mcp/report", body)
            cm.exception.close()
            self.assertEqual(cm.exception.code, 400)

    def test_an_agent_of_the_app_itself(self):
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        from mcp_types import Implementation

        async def go():
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {self.handle.token}"}) as http:
                async with Client(streamable_http_client(f"{self.handle.url.rstrip('/')}/mcp", http_client=http),
                                  client_info=Implementation(name="cursor", version="1.7"), cache=None) as c:
                    names = [t.name for t in (await c.list_tools()).tools]
                    await c.call_tool("wk_tune_cvar_get", {"name": "x"})
                    await c.call_tool("wk_tune_cvar_get", {"name": "y"})
                    return names
        names = asyncio.run(go())
        self.assertEqual(sorted(n for n in names if n.startswith("wk_")), WK)
        rows = [r for r in request(self.handle, "GET", "/api/agent_access")["servers"] if r["kind"] == "http"]
        self.assertEqual(len(rows), 1)                                         # every request a connection, one agent
        row = rows[0]
        self.assertEqual((row["client"], row["calls"], row["tools"], row["state"], row["pid"]),
                         ({"name": "cursor", "version": "1.7"}, 2, len(WK), "current", os.getpid()))


if __name__ == "__main__":
    unittest.main()
