"""Local extensions over a real daemon (a FakeGame link; WuxianKit's calls answered here, tests/local/fixture_ext.py as
the extension): the HTTP routes and their errors, SSE's `event: local`, local_call to a local extension and to the
game's WuxianKit (see / keep / point only), an extension's own calls, its source's items as journal events, its apps
declared after kit.ready, the reactions (a game source through the temporary app, a local source directly),
new_extension, the MCP tools over /mcp and the command line."""
import asyncio
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame
from tests.local.util import make_extension, until
from wuxianworkshop import scaffold
from wuxianworkshop.cli import main as cli
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server as daemon
from wuxianworkshop.daemon.api import ApiError

KIT_MANIFEST = {"protocol": 1, "revision": "r1", "extensions": [
    {"id": "sense", "state": "on", "capabilities": [{"id": "sense.bags", "kind": "see", "title": "背包", "args": {}}]},
    {"id": "tune", "state": "on", "capabilities": [{"id": "tune.cvar.set", "kind": "do", "title": "设置", "args": {}}]},
    {"id": "guide", "state": "off", "capabilities": [{"id": "guide.note", "kind": "point", "title": "提示"}]}]}


class Kit:
    """WuxianKit in the game as the daemon's Service calls it: what it was asked (calls), the answers of a kit 0.6"""

    def __init__(self, svc):
        self.svc, self.calls = svc, []

    def emit(self, topic, data):
        self.svc.on_debug("EVENT", json.dumps({"a": "WuxianKit", "t": topic, "d": data}))

    async def call_exposed(self, addon, name, args=None, timeout_ms=10000, shared=None):
        self.calls.append((name, args))
        if name == "kit.app.declare":
            result = {"app": args["app"]}
        elif name == "kit.app.open":
            if "game:nonsense" in args.get("hear", []):
                raise ApiError(400, "call_failed", "no source game:nonsense\nstack ...")
            result = {"on": True}
        elif name == "kit.app.stop":
            result = {"on": False}
        elif name == "sense.bags":
            result = {"free": 12, "args": args}
        else:
            raise ApiError(400, "call_failed", f"WuxianKit.{name} is not exposed")
        return {"addon": addon, "name": name, "ok": True, "result": result}

    def asked(self, name):
        return [args for n, args in self.calls if n == name]


class LocalApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name).resolve()
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(cls.root / "home")})
        cls.env.start()
        state = cls.root / "home" / "state"
        state.mkdir(parents=True)
        (state / "kit-manifest.json").write_text(json.dumps(KIT_MANIFEST), encoding="utf-8")
        addons = cls.root / "client" / "Interface" / "AddOns"
        (addons / "WoWBridge").mkdir(parents=True)
        MB.install(addons, pool=16)
        cls.handle = daemon.start(worker_factory=lambda on_debug, log: FakeGame(addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "L")
        cls.svc = cls.handle.service
        cls.kit = Kit(cls.svc)
        cls.svc.call_exposed = cls.kit.call_exposed
        cls.svc.kit_manifest_known = lambda: KIT_MANIFEST

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.kit.calls.clear()

    def tearDown(self):
        self.ok("POST", "/api/reactions/clear", {})
        for row in self.svc.local.rows():                  # each test leaves no extension behind
            status, _ = self.call("POST", "/api/local/remove", {"id": row["id"]})
            self.assertEqual(status, 200)

    # --- helpers

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.handle.url.rstrip("/") + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.handle.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def ok(self, method, path, body=None):
        status, res = self.call(method, path, body)
        self.assertEqual(status, 200, res)
        return res

    def fails(self, method, path, body, status, code):
        got, res = self.call(method, path, body)
        self.assertEqual((got, res["error"]["code"]), (status, code), res)
        return res["error"]["message"]

    def add(self, eid="fx", **fields):
        folder = make_extension(self.root / "ext" / eid, eid, **fields)
        return self.ok("POST", "/api/local/add", {"path": str(folder)}), folder

    def events(self, topic):
        res = self.ok("POST", "/api/addon_events", {"addon": "local", "topic": topic, "since": 0})
        return [e["data"] for e in res["events"]]

    # --- HTTP

    def test_http_routes(self):
        row, folder = self.add()
        self.assertEqual((row["id"], row["state"]), ("fx", "stopped"))
        listing = self.ok("GET", "/api/local")
        self.assertEqual([r["id"] for r in listing["extensions"]], ["fx"])
        self.assertGreaterEqual(listing["revision"], 1)
        res = self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": "你好"}})
        self.assertEqual((res["result"]["text"], res["result"]["caller"]), ("你好", {"by": "agent"}))
        self.assertEqual(self.ok("GET", "/api/local")["extensions"][0]["state"], "running")
        manifest = self.ok("GET", "/api/local/manifest")
        ext = manifest["extensions"][0]
        self.assertEqual((ext["id"], ext["runtime"], ext["state"]), ("fx", "local", "on"))
        self.assertIn("fx.emit", [c["id"] for c in ext["capabilities"]])
        row = self.ok("POST", "/api/local/set", {"id": "fx", "options": {"volume": 70}})
        self.assertEqual(row["options"][0]["value"], 70)
        logs = self.ok("GET", "/api/local/logs?id=fx")
        self.assertIn("fixture up", [line["text"] for line in logs["lines"]])
        self.assertEqual(self.ok("POST", "/api/local/check", {"id": "fx"})["ok"], True)
        res = self.ok("POST", "/api/local/load", {"id": "fx"})
        self.assertEqual((res["row"]["state"], res["check"]["ok"]), ("running", True))
        self.assertEqual(self.ok("POST", "/api/local/stop", {"id": "fx"})["state"], "stopped")
        self.assertEqual(self.ok("POST", "/api/local/set", {"id": "fx", "enabled": False})["state"], "off")
        self.fails("POST", "/api/local/call", {"cap": "fx.echo", "args": {}}, 404, "no_capability")
        self.ok("POST", "/api/local/set", {"id": "fx", "enabled": True})
        for method, path, body, status, code in (
                ("POST", "/api/local/add", {"path": "relative"}, 400, "bad_request"),
                ("POST", "/api/local/add", {"path": str(self.root / "nothing")}, 400, "bad_manifest"),
                ("POST", "/api/local/load", {"id": "ghost"}, 404, "not_found"),
                ("POST", "/api/local/set", {"id": "fx", "options": {"volume": 500}}, 400, "bad_option"),
                ("GET", "/api/local/logs?id=fx&limit=0", None, 400, "bad_request"),
                ("POST", "/api/local/call", {"cap": "fx.picky", "args": {"n": "x"}}, 400, "bad_args"),
                ("POST", "/api/local/call", {"cap": "fx.boom"}, 422, "failed"),
                ("POST", "/api/local/call", {"cap": "fx.ghost"}, 404, "no_capability"),
                ("POST", "/api/local/call", {"cap": "fx.echo", "args": [1]}, 400, "bad_args"),
                ("POST", "/api/local/call", {"cap": "nodot"}, 400, "bad_request"),
                ("POST", "/api/local/new", {"id": "chat"}, 400, "bad_request"),
                ("POST", "/api/local/new", {"id": "fy", "folder": str(folder)}, 409, "folder_not_empty"),
                ("POST", "/api/reactions/set", {"hear": "nothing"}, 400, "bad_args")):
            self.fails(method, path, body, status, code)
        res = self.ok("POST", "/api/local/remove", {"id": "fx"})
        self.assertEqual(res, dict(removed=True, id="fx", path=str(folder)))
        self.assertEqual(self.ok("GET", "/api/local")["extensions"], [])

    def test_a_slow_start_is_not_the_calls_time(self):
        """the first call waits for the start (initialize) on top of the capability's own time"""
        self.add()
        original = self.svc.local.initialize

        def slow(*args, **kw):
            time.sleep(1.5)
            return original(*args, **kw)
        with mock.patch.object(self.svc.local, "initialize", side_effect=slow):
            res = self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": "a"}, "timeout_ms": 1000})
        self.assertEqual(res["result"]["text"], "a")
        msg = self.fails("POST", "/api/local/call", {"cap": "fx.slow", "args": {"seconds": 3}, "timeout_ms": 100},
                         504, "timeout")
        self.assertIn("within 1 s", msg)                     # its own timeout (1 s) is the longer one

    def test_sse_tells_of_changes(self):
        req = urllib.request.Request(f"{self.handle.url.rstrip('/')}/api/events?token={self.handle.token}"
                                     f"&since={self.svc.journal.next_id}&kinds=NONE")
        with urllib.request.urlopen(req, timeout=20) as resp:
            self.add("sse")
            seen, event, data, deadline = [], None, [], time.time() + 20
            while time.time() < deadline and not seen:
                line = resp.readline().decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and data:
                    if event == "local":
                        seen.append(json.loads("".join(data)))
                    event, data = None, []
        self.assertTrue(seen)
        self.assertEqual([r["id"] for r in seen[0]["extensions"]], ["sse"])
        self.assertGreaterEqual(seen[0]["revision"], 1)

    # --- calls of the game's WuxianKit, an extension's own calls, its items

    def test_kit_capabilities_and_own_calls(self):
        res = self.ok("POST", "/api/local/call", {"cap": "sense.bags", "args": {"bag": 1}})
        self.assertEqual(res["result"], {"free": 12, "args": {"bag": 1}})
        self.assertIn("proposal", self.fails("POST", "/api/local/call", {"cap": "tune.cvar.set"}, 403, "not_allowed"))
        self.fails("POST", "/api/local/call", {"cap": "guide.note"}, 404, "no_capability")   # its extension is off
        self.fails("POST", "/api/local/call", {"cap": "ghost.thing"}, 404, "no_capability")
        self.add()
        res = self.ok("POST", "/api/local/call", {"cap": "fx.ask", "args": {"cap": "sense.bags", "args": {"bag": 2}}})
        self.assertEqual(res["result"], {"result": {"free": 12, "args": {"bag": 2}}, "error": None})
        res = self.ok("POST", "/api/local/call", {"cap": "fx.ask", "args": {"cap": "tune.cvar.set"}})
        self.assertEqual((res["result"]["error"]["code"], res["result"]["error"]["data"]), (1, {"reason": "not_allowed"}))
        res = self.ok("POST", "/api/local/call", {"cap": "fx.ask", "args": {"cap": "fx.echo", "args": {"text": "x"}}})
        self.assertEqual(res["result"]["result"]["caller"], {"by": "extension", "id": "fx"})
        self.ok("POST", "/api/local/call", {"cap": "fx.emit", "args": {"text": "说完"}})
        self.assertTrue(until(lambda: self.events("fx.said")))
        item = self.events("fx.said")[0]
        self.assertEqual((item["source"], item["kind"], item["data"]), ("fx", "fx.said", {"text": "说完"}))
        self.assertTrue(self.events("local.changed"))

    def test_apps_declared(self):
        app = {"app": "companion", "title": "语音搭子", "prompt": "companion", "hear": ["game:combat"]}
        _, folder = self.add(apps=[app])
        title = json.loads((folder / "wuxian-extension.json").read_text(encoding="utf-8"))["title"]
        app = dict(app, local={"id": "fx", "title": title})          # the extension it comes with
        self.assertTrue(until(lambda: self.kit.asked("kit.app.declare")))
        self.assertEqual(self.kit.asked("kit.app.declare"), [app])
        self.kit.calls.clear()
        self.kit.emit("kit.ready", {})                     # a login or a reload: declared again
        self.assertTrue(until(lambda: self.kit.asked("kit.app.declare")), self.kit.calls)
        self.assertEqual(self.kit.asked("kit.app.declare"), [app])
        self.assertNotIn("launch", self.kit.asked("kit.app.declare")[0])

    # --- reactions

    def test_reactions(self):
        self.add()
        self.fails("POST", "/api/reactions/set", {"hear": "game:combat", "call": "fx.echo"}, 400, "bad_args")
        self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": ""}})   # described: its kinds known
        game = self.ok("POST", "/api/reactions/set", {"id": "g", "owner": "companion", "hear": "game:combat",
                                                      "call": "fx.echo", "args": {"text": "战斗"}, "cooldown": 0})
        self.assertEqual(game["id"], "g")
        self.assertTrue(until(lambda: self.kit.asked("kit.app.open")))
        opened = self.kit.asked("kit.app.open")[-1]
        self.assertEqual(opened, {"app": "reactions", "title": "反射", "hear": ["game:combat"], "temp": True,
                                  "hidden": True, "digest": False, "lease": 0, "minutes": 720})
        self.ok("POST", "/api/reactions/set", {"id": "l", "hear": "fx:said", "call": "fx.echo", "args": {"text": "本机"},
                                               "cooldown": 0})
        self.assertEqual(self.kit.asked("kit.app.open")[-1]["hear"], ["game:combat"])   # a local source: not the game's
        self.kit.emit("app.reactions", {"app": "reactions", "reason": "item",
                                        "items": [{"id": 1, "source": "game", "kind": "combat", "data": {}}]})
        self.assertTrue(until(lambda: self.reaction("g")["fired"] == 1), self.reaction("g"))
        self.ok("POST", "/api/local/call", {"cap": "fx.emit", "args": {"text": "x"}})
        self.assertTrue(until(lambda: self.reaction("l")["fired"] == 1), self.reaction("l"))
        self.kit.emit("kit.app", {"app": "companion", "on": False})       # its owner stopped: its reactions go
        self.assertTrue(until(lambda: self.reaction("g") is None))
        self.assertTrue(until(lambda: self.kit.asked("kit.app.stop")))    # no game source left
        self.assertEqual(self.kit.asked("kit.app.stop")[-1], {"app": "reactions"})
        self.assertEqual(self.ok("POST", "/api/reactions/clear", {"id": "l"}), {"cleared": 1})

    def test_reactions_after_kit_ready(self):
        self.ok("POST", "/api/reactions/set", {"hear": "game:quest", "call": "sense.bags"})
        self.kit.calls.clear()
        self.kit.emit("kit.ready", {})
        self.assertTrue(until(lambda: self.kit.asked("kit.app.open")))
        self.assertEqual(self.kit.asked("kit.app.open")[-1]["hear"], ["game:quest"])

    def reaction(self, rid):
        return next((r for r in self.ok("GET", "/api/reactions")["reactions"] if r["id"] == rid), None)

    # --- new_extension

    def test_new_extension(self):
        def files(folder, eid, title):
            make_extension(folder, eid, title=title or eid)
            return [str(Path(folder) / "wuxian-extension.json"), str(Path(folder) / "main.py")]
        with mock.patch.object(scaffold, "new_extension_files", side_effect=files, create=True) as made:
            res = self.ok("POST", "/api/local/new", {"id": "fresh", "title": "新的"})
        folder = self.root / "home" / "extensions" / "fresh"
        made.assert_called_once_with(str(folder), "fresh", "新的")
        self.assertEqual((res["row"]["id"], res["row"]["title"], res["row"]["path"]), ("fresh", "新的", str(folder)))
        self.assertEqual(len(res["files"]), 2)
        self.assertIn("extension_load", res["guide"])
        with mock.patch.object(scaffold, "new_extension_files", None, create=True):
            self.fails("POST", "/api/local/new", {"id": "other"}, 501, "not_available")

    # --- MCP

    def test_mcp_tools(self):
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        self.add()
        self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": ""}})

        async def go():
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {self.handle.token}"}) as http:
                async with Client(streamable_http_client(self.handle.url + "mcp", http_client=http)) as c:
                    tools = {t.name: t for t in (await c.list_tools()).tools}
                    out = {"list": await c.call_tool("local_extensions", {}),
                           "guide": await c.call_tool("extension_guide", {}),
                           "set": await c.call_tool("reaction_set", {"spec": {"hear": "fx:said", "call": "fx.echo"}}),
                           "reactions": await c.call_tool("reactions", {}),
                           "clear": await c.call_tool("reaction_clear", {}),
                           "logs": await c.call_tool("extension_logs", {"id": "ghost"})}
                    return tools, out

        tools, out = asyncio.run(go())
        for name in ("local_extensions", "extension_logs", "extension_guide", "reactions"):
            self.assertTrue(tools[name].annotations.read_only_hint, name)
        for name in ("new_extension", "extension_add", "extension_remove", "extension_load", "extension_stop",
                     "extension_set", "extension_check", "reaction_set", "reaction_clear"):   # a check starts a process
            self.assertFalse(tools[name].annotations.read_only_hint, name)
        self.assertFalse([n for n in tools if n.startswith("program") or n == "new_program"])
        self.assertEqual(out["list"].structured_content["extensions"][0]["id"], "fx")
        self.assertTrue(out["guide"].content[0].text)
        self.assertEqual(out["set"].structured_content["hear"], "fx:said")
        self.assertEqual(len(out["reactions"].structured_content["reactions"]), 1)
        self.assertEqual(out["clear"].structured_content, {"cleared": 1})
        self.assertTrue(out["logs"].is_error)
        self.assertIn("not_found", out["logs"].content[0].text)

    # --- the command line

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli(self):
        folder = make_extension(self.root / "ext" / "clix", "clix")
        code, out, err = self.run_cli("ext", "list")
        self.assertEqual(code, 0, err)
        self.assertIn("no local extensions registered", out)
        code, out, err = self.run_cli("ext", "add", str(folder))
        self.assertEqual(code, 0, err)
        self.assertIn("registered: clix", out)
        code, out, err = self.run_cli("wk", "clix.echo", '{"text": "命令行"}')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["text"], "命令行")
        code, out, err = self.run_cli("ext", "set", "clix", "--option", "volume=30")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("ext", "list", "--json")
        self.assertEqual(json.loads(out)["extensions"][0]["options"][0]["value"], 30)
        code, out, err = self.run_cli("ext", "load", "clix")
        self.assertEqual(code, 0, err)
        self.assertIn("ok: 8 capabilities", out)
        code, out, err = self.run_cli("ext", "check", str(folder))
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("ext", "logs", "clix")
        self.assertIn("fixture up", out)
        code, out, err = self.run_cli("reaction", "set", '{"id": "c", "hear": "clix:said", "call": "clix.echo"}')
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("reaction", "list")
        self.assertIn("c: clix:said -> clix.echo", out)
        code, out, err = self.run_cli("reaction", "clear", "--id", "c")
        self.assertIn("cleared 1", out)
        code, out, err = self.run_cli("wk", "clix.boom")
        self.assertEqual(code, 1)
        self.assertIn("failed", err)
        code, out, err = self.run_cli("ext", "stop", "clix")
        self.assertIn("stopped", out)
        code, out, err = self.run_cli("ext", "remove", "clix")
        self.assertIn("clix is no longer registered", out)


if __name__ == "__main__":
    unittest.main()
