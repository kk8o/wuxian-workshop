"""The program loader over a real daemon (a FakeGame link, WuxianKit's calls answered here): the HTTP routes and their
errors, SSE's `event: programs`, the MCP tools over /mcp, `wuxian program ...`, and WuxianKit's side: kit.app.declare
when a program is registered and after kit.ready, kit.app.launch (the player's 开始) starting it, a stop that stops its
app first. Two programs made from the template (new_program) run for real: one makes a call, one drives its app."""
import asyncio
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.daemon.test_programs import gone, until, write_program
from tests.daemon.test_server import MUTEX, FakeGame
from wuxianworkshop import programs as P
from wuxianworkshop.cli import main as cli
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server as daemon, service as service_module
from wuxianworkshop.daemon.api import ApiError

ME = {"name": "测试", "level": 60}


class Kit:
    """WuxianKit in the game as the daemon's Service calls it: what it was asked (calls), the answers of a kit 0.6"""

    def __init__(self, svc):
        self.svc, self.calls = svc, []

    def emit(self, topic, data):
        self.svc.on_debug("EVENT", json.dumps({"a": "WuxianKit", "t": topic, "d": data}))

    async def call_exposed(self, addon, name, args=None, timeout_ms=10000, shared=None):
        self.calls.append((name, args))
        if name == "kit.app.declare":
            result = {"app": args["app"], "launch": args.get("launch")}
        elif name in ("kit.app.open", "kit.app.status"):
            result = {"on": True, "lease": 600}
        elif name == "kit.app.stop":
            self.emit("kit.app", {"app": args["app"], "on": False, "why": "stopped"})
            result = {"on": False}
        elif name == "sense.character":
            result = ME
        else:
            raise ApiError(400, "call_failed", f"WuxianKit.{name} is not exposed")
        return {"addon": addon, "name": name, "ok": True, "result": result}

    def asked(self, name):
        return [args for n, args in self.calls if n == name]


class ProgramsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name).resolve()
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(cls.root / "home")})
        cls.env.start()
        cls.patches = [mock.patch.object(service_module, "STOP_GRACE", 5.0),
                       mock.patch.object(service_module, "DECLARE_GRACE", 3600.0)]   # programs_watch: its own test
        for patch in cls.patches:
            patch.start()
        addons = cls.root / "client" / "Interface" / "AddOns"
        (addons / "WoWBridge").mkdir(parents=True)
        MB.install(addons, pool=16)
        cls.handle = daemon.start(worker_factory=lambda on_debug, log: FakeGame(addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "P")
        cls.svc = cls.handle.service
        cls.kit = Kit(cls.svc)
        cls.svc.call_exposed = cls.kit.call_exposed

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        for patch in cls.patches:
            patch.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.kit.calls.clear()

    def tearDown(self):
        for row in self.svc.loader.rows():                   # each test leaves no program behind
            status, _ = self.call("POST", "/api/programs/remove", {"id": row["path"]})
            self.assertEqual(status, 200)

    # --- helpers

    def call(self, method, path, body=None):
        """(status, JSON answer) of one request"""
        req = urllib.request.Request(self.handle.url.rstrip("/") + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.handle.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def ok(self, method, path, body=None):
        status, res = self.call(method, path, body)
        self.assertEqual(status, 200, res)
        return res

    def row(self, pid):
        return next(r for r in self.ok("GET", "/api/programs") if r["id"] == pid)

    def settled(self, pid):
        self.assertTrue(until(lambda: self.row(pid)["state"] != "running", 30), self.row(pid))
        return self.row(pid)

    def output(self, pid):
        return [line["text"] for line in self.ok("GET", f"/api/programs/logs?id={pid}&limit=2000")["lines"]]

    def journal(self, words):
        """whether a line of the daemon's log has these words"""
        since = max(0, self.svc.journal.next_id - 2000)
        return any(words in e["text"] for e in self.svc.journal.since(since, limit=2000)[0])

    def made(self, pid, **more):
        """a program from the template (POST /api/programs/new), run by this interpreter (PATH may have no python)"""
        res = self.ok("POST", "/api/programs/new", dict(path=str(self.root / pid), id=pid, **more))
        file = Path(res["manifest"])
        manifest = json.loads(file.read_text(encoding="utf-8"))
        manifest["run"][0] = sys.executable
        file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return res

    # --- HTTP

    def test_http_routes(self):
        folder = write_program(self.root / "demo", "demo")
        row = self.ok("POST", "/api/programs/add", {"path": str(folder)})
        self.assertEqual((row["id"], row["state"], row["app"], row["declared"], row["stopped"]),
                         ("demo", "stopped", False, None, False))
        rows = self.ok("GET", "/api/programs")
        self.assertIsInstance(rows, list)
        self.assertEqual([r["id"] for r in rows], ["demo"])
        self.assertEqual(set(rows[0]), {"id", "title", "path", "manifest", "app", "state", "pid", "started", "exit_code",
                                        "exited_at", "stopped", "command", "error", "output"})
        row = self.ok("POST", "/api/programs/start", {"id": "demo"})
        self.assertEqual(row["state"], "running")
        row = self.settled("demo")
        self.assertEqual((row["state"], row["exit_code"], row["stopped"]), ("exited", 3, False))
        res = self.ok("GET", "/api/programs/logs?id=demo")
        self.assertEqual((res["total"], res["next"], res["reset"], res["cut"], res["row"]["exit_code"]),
                         (4, 5, False, False, 3))
        self.assertIn("世界", [line["text"] for line in res["lines"]])
        res = self.ok("GET", "/api/programs/logs?id=demo&since=4&limit=1")
        self.assertEqual([line["n"] for line in res["lines"]], [4])
        self.assertEqual(self.ok("POST", "/api/programs/stop", {"id": "demo"})["state"], "exited")   # it does not run

        write_program(folder, "demo", "sleep")
        first = self.ok("POST", "/api/programs/start", {"id": "demo"})
        status, res = self.call("POST", "/api/programs/start", {"id": "demo"})
        self.assertEqual((status, res["error"]["code"]), (409, "running"))
        again = self.ok("POST", "/api/programs/start", {"id": "demo", "restart": True})
        self.assertEqual(again["state"], "running")
        self.assertNotEqual(again["pid"], first["pid"])
        self.assertTrue(gone(first["pid"]))
        row = self.ok("POST", "/api/programs/stop", {"id": "demo"})
        self.assertEqual((row["state"], row["stopped"], row["exit_code"]), ("stopped", True, None))
        self.assertTrue(gone(again["pid"]))

        for method, path, body, want in (
                ("POST", "/api/programs/add", {"path": "relative"}, (400, "bad_request")),
                ("POST", "/api/programs/add", {"path": str(self.root / "nothing")}, (400, "no_manifest")),
                ("POST", "/api/programs/start", {"id": "ghost"}, (404, "not_found")),
                ("POST", "/api/programs/start", {"id": "demo", "restart": "yes"}, (400, "bad_request")),
                ("GET", "/api/programs/logs?id=demo&limit=0", None, (400, "bad_request")),
                ("POST", "/api/programs/new", {"path": str(folder), "id": "other"}, (409, "folder_not_empty")),
                ("POST", "/api/programs/new", {"path": str(self.root / "x"), "id": "Bad"}, (400, "bad_request"))):
            status, res = self.call(method, path, body)
            self.assertEqual((status, res["error"]["code"]), want, (path, body, res))
        res = self.ok("POST", "/api/programs/remove", {"id": "demo"})
        self.assertEqual(res, dict(removed=True, id="demo", path=str(folder)))
        self.assertEqual(self.ok("GET", "/api/programs"), [])

    def test_sse_tells_of_changes(self):
        """`event: programs` (no data) for an add, a start, an end; also when the stream takes no log kinds"""
        req = urllib.request.Request(f"{self.handle.url.rstrip('/')}/api/events?token={self.handle.token}"
                                     f"&since={self.svc.journal.next_id}&kinds=NONE")
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.ok("POST", "/api/programs/add", {"path": str(write_program(self.root / "sse", "sse"))})
            self.ok("POST", "/api/programs/start", {"id": "sse"})
            seen, event, data, deadline = [], None, [], time.time() + 20
            while time.time() < deadline and seen.count("programs") < 3:
                line = resp.readline().decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and data:
                    self.assertEqual(json.loads("".join(data)) if event == "programs" else {}, {})
                    seen.append(event)
                    event, data = None, []
        self.assertGreaterEqual(seen.count("programs"), 3, seen)           # added, started, exited
        self.assertNotIn("log", seen)

    # --- WuxianKit's side

    def test_declare_launch_and_stop(self):
        folder = write_program(self.root / "talk", "talk", "sleep", title="解说", app={"hear": ["chat:guild"]})
        decl = {"hear": ["chat:guild"], "app": "talk", "title": "解说", "driver": "program", "launch": True}
        row = self.ok("POST", "/api/programs/add", {"path": str(folder)})
        self.assertEqual((row["app"], row["declared"]), (True, "ok"))
        self.assertEqual(self.kit.asked("kit.app.declare"), [decl])

        self.kit.calls.clear()
        self.kit.emit("kit.ready", {})                        # a login or a reload: declared again
        self.assertTrue(until(lambda: self.kit.asked("kit.app.declare")), self.kit.calls)
        self.assertEqual(self.kit.asked("kit.app.declare"), [decl])

        self.kit.emit("kit.app.launch", {"app": "talk"})      # the player's 开始
        self.assertTrue(until(lambda: self.row("talk")["state"] == "running"))
        pid = self.row("talk")["pid"]
        self.assertTrue(until(lambda: self.journal("program talk: started (the player's 开始 in the game)")))
        self.kit.emit("kit.app.launch", {"app": "talk"})
        self.assertTrue(until(lambda: self.journal("program talk: the game's 开始, but it runs already")))
        self.kit.emit("kit.app.launch", {"app": "ghost"})
        self.assertTrue(until(lambda: self.journal("kit.app.launch ghost: no program ghost is registered here")))
        self.assertEqual(self.row("talk")["pid"], pid)

        began = time.monotonic()
        row = self.ok("POST", "/api/programs/stop", {"id": "talk"})   # its app first; it does not end: its tree
        self.assertGreaterEqual(time.monotonic() - began, service_module.STOP_GRACE - 0.5)
        self.assertEqual(self.kit.asked("kit.app.stop"), [{"app": "talk"}])
        self.assertEqual((row["state"], row["stopped"], row["exit_code"]), ("stopped", True, None))
        self.assertTrue(gone(pid))

        self.kit.calls.clear()
        self.ok("POST", "/api/programs/remove", {"id": "talk"})
        self.assertEqual(self.kit.asked("kit.app.declare"), [dict(decl, launch=False)])   # no 开始 on its card

    def test_declared_in_a_session_joined_without_kit_ready(self):
        """a daemon that started while the game ran sees no kit.ready: programs_watch declares the programs' apps once
        the UI session has been there DECLARE_GRACE seconds"""
        self.ok("POST", "/api/programs/add", {"path": str(write_program(self.root / "late", "late", "sleep",
                                                                        app={"hear": []}))})
        self.kit.calls.clear()
        self.svc.declared = None
        with mock.patch.object(service_module, "DECLARE_GRACE", 0.0), \
                mock.patch.object(service_module, "DECLARE_EVERY", 0.2):
            self.assertTrue(until(lambda: self.kit.asked("kit.app.declare"), 15))
            self.assertEqual(self.svc.declared, self.svc.ui_session())
            time.sleep(0.6)                                   # once per session
        self.assertEqual([d["app"] for d in self.kit.asked("kit.app.declare")], ["late"])

    def test_a_template_program_that_drives_its_app(self):
        """new_program's main.py (app): Client().app(*program_app()) opens the manifest's app, prints its wakes, and
        ends by itself when the app is stopped: a stop that keeps its exit code"""
        res = self.made("helper", title="助手")
        self.assertEqual((res["id"], res["app"], res["declared"]), ("helper", True, "ok"))
        self.assertEqual(sorted(res["files"]), sorted([P.MANIFEST, "main.py", "wuxiankit.py", "AGENTS.md", "CLAUDE.md"]))
        self.ok("POST", "/api/programs/start", {"id": "helper"})
        self.assertTrue(until(lambda: self.kit.asked("kit.app.open"), 30), self.output("helper"))
        self.assertEqual(self.kit.asked("kit.app.open")[0], {"hear": [], "say": True, "controls": ["stop"], "title": "助手",
                                                             "driver": "program", "app": "helper"})
        self.assertTrue(until(lambda: "helper: waiting for wakes (in the game: /wk helper hello)" in self.output("helper")))
        self.kit.emit("app.helper", {"app": "helper", "reason": "player", "items": [{"id": 1, "source": "player",
                                                                                     "text": "hello"}]})
        self.assertTrue(until(lambda: "player: player hello" in self.output("helper")), self.output("helper"))
        began = time.monotonic()
        row = self.ok("POST", "/api/programs/stop", {"id": "helper"})
        self.assertLess(time.monotonic() - began, service_module.STOP_GRACE)       # it ended by itself
        self.assertEqual((row["state"], row["stopped"], row["exit_code"]), ("stopped", True, 0))
        self.assertIn("the app ended: stopped", self.output("helper"))

    # --- MCP

    def test_mcp_tools(self):
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        async def go():
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {self.handle.token}"}) as http:
                async with Client(streamable_http_client(self.handle.url + "mcp", http_client=http)) as c:
                    tools = {t.name: t for t in (await c.list_tools()).tools}
                    out = {"new": await c.call_tool("new_program", {"path": str(self.root / "plain"), "id": "plain",
                                                                    "app": False})}
                    manifest = Path(out["new"].structured_content["manifest"])
                    m = json.loads(manifest.read_text(encoding="utf-8"))
                    m["run"][0] = sys.executable
                    manifest.write_text(json.dumps(m), encoding="utf-8")
                    out["start"] = await c.call_tool("program_start", {"id": "plain"})
                    for _ in range(300):
                        out["list"] = await c.call_tool("programs", {})
                        if out["list"].structured_content["programs"][0]["state"] != "running":
                            break
                        await asyncio.sleep(0.1)
                    out["logs"] = await c.call_tool("program_logs", {"id": "plain"})
                    out["ghost"] = await c.call_tool("program_start", {"id": "ghost"})
                    out["forget"] = await c.call_tool("program_stop", {"id": "plain", "forget": True})
                    out["add"] = await c.call_tool("program_add", {"path": str(self.root / "plain")})
                    out["stop"] = await c.call_tool("program_stop", {"id": "plain"})
                    return tools, out

        tools, out = asyncio.run(go())
        for name in ("programs", "program_logs"):
            self.assertTrue(tools[name].annotations.read_only_hint, name)
        for name in ("new_program", "program_add", "program_start", "program_stop"):
            self.assertFalse(tools[name].annotations.read_only_hint, name)
        self.assertNotIn("program_remove", tools)                     # program_stop's forget does it
        new = out["new"].structured_content
        self.assertEqual((new["id"], new["app"], new["state"], new["declared"]), ("plain", False, "stopped", None))
        self.assertIn("program_start", new["hint"])
        self.assertEqual(out["start"].structured_content["state"], "running")
        row = out["list"].structured_content["programs"][0]
        self.assertEqual((row["id"], row["state"], row["exit_code"]), ("plain", "exited", 0))
        logs = out["logs"].structured_content
        self.assertEqual(json.loads("\n".join(line["text"] for line in logs["lines"])), ME)   # its call's result
        self.assertEqual(self.kit.asked("sense.character"), [None])
        self.assertTrue(out["ghost"].is_error)
        self.assertIn("not_found", out["ghost"].content[0].text)
        self.assertEqual(out["forget"].structured_content["removed"], True)
        self.assertEqual(out["add"].structured_content["id"], "plain")
        self.assertEqual((out["stop"].structured_content["state"], out["stop"].structured_content["stopped"]),
                         ("stopped", False))                 # registered anew: its runs before are not its row's
        self.assertFalse(out["stop"].is_error)

    # --- the command line

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli(self):
        folder = write_program(self.root / "clidemo", "clidemo")
        code, out, err = self.run_cli("program", "list")
        self.assertEqual(code, 0, err)
        self.assertIn("no programs registered", out)
        code, out, err = self.run_cli("program", "add", str(folder))
        self.assertEqual(code, 0, err)
        self.assertIn("registered: clidemo", out)
        code, out, err = self.run_cli("program", "start", "clidemo")
        self.assertEqual(code, 0, err)
        self.assertIn("started clidemo, pid ", out)
        self.settled("clidemo")
        code, out, err = self.run_cli("program", "list")
        self.assertIn("exited with code 3", out)
        code, out, err = self.run_cli("program", "list", "--json")
        self.assertEqual([r["id"] for r in json.loads(out)], ["clidemo"])
        code, out, err = self.run_cli("program", "logs", "clidemo")
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(out.splitlines()), sorted(["hello", "世界", "an error line", "no newline at the end"]))
        self.assertIn("-- exited with code 3", err)
        code, out, err = self.run_cli("program", "logs", "clidemo", "--limit", "2")
        self.assertEqual(len(out.splitlines()), 2)
        self.assertIn("its last 2 of 4 lines", err)
        code, out, err = self.run_cli("program", "start", "ghost")
        self.assertEqual(code, 1)
        self.assertIn("not_found", err)
        write_program(folder, "clidemo", "sleep")
        self.run_cli("program", "start", "clidemo")
        code, out, err = self.run_cli("program", "start", "clidemo")
        self.assertEqual(code, 1)
        self.assertIn("running", err)
        code, out, err = self.run_cli("program", "start", "clidemo", "--restart")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("program", "stop", "clidemo")
        self.assertEqual(code, 0, err)
        self.assertIn("stopped", out)
        code, out, err = self.run_cli("program", "remove", "clidemo")
        self.assertEqual(code, 0, err)
        self.assertIn("clidemo is no longer registered", out)


if __name__ == "__main__":
    unittest.main()
