"""The daemon (daemon/server.py) over a real socket, without a game: a CompanionLoop without a screen whose "addon"
reads the mailbox slots the Companion writes and answers every CODE job. Covers the token, Host and Origin checks, the
endpoints, the run / load correlation and timeouts, SSE, daemon.json, the single instance, the MCP endpoint and the
command line as a client."""
import asyncio
import contextlib
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from wuxianworkshop.core import frame as F, mailbox as MB
from wuxianworkshop.daemon import server
from wuxianworkshop.daemon.api import read_daemon_json
from wuxianworkshop.daemon.companion import CompanionLoop
from wuxianworkshop.cli import main as cli

MUTEX = rf"Local\WuxianWorkshopTest{os.getpid()}"


class FakeGame(CompanionLoop):
    """no window: the loop waits for one; meanwhile this "addon" reads the slots the Companion writes and reports the
    CODE jobs as run: ok with two values, an error for code that calls error(), nothing for code that says sleep"""

    def __init__(self, addons, **kw):
        super().__init__(addons, find_window=lambda: None, files=False, **kw)
        self.comp.new_process("P1-1")
        self.comp.on_frame(F.TYPE_CONTROL, 5, 1, b"HELLO;v=0.7.0;s=5;k=1;m=1")    # a session: tick() writes slots
        self.next_slot = 1

    def _wait(self, seconds):
        self.comp.on_frame(F.TYPE_CONTROL, 5, 1, b"HB;n=1")      # its frames keep coming: the link stays up
        self.comp.tick()
        for slot in range(self.next_slot, self.comp.slot or 1):
            pkt = MB.read_slot(self.addons, slot)
            for kind, data in (pkt["records"] if pkt else []):
                if kind == MB.CODE:
                    self.answer(data)
            self.next_slot = slot + 1
        super()._wait(min(seconds, 0.02))

    def answer(self, data):
        head, _, body = data.partition(b"\n")
        parts = head.decode().split(" ")
        if not parts[1].startswith("1/"):
            return
        job, name = int(parts[0]), parts[3]
        if b"sleep" in body:
            return
        for line in body.decode("utf-8", "replace").splitlines():   # "--emit KIND text": what the game reports meanwhile
            if line.startswith("--emit "):
                kind, _, text = line[7:].partition(" ")
                self.comp.on_debug(kind, text.replace("\\n", "\n"))
        if b"no trace is running" in body:                              # probes.trace_stop(): what it kept
            self.comp.on_debug("RUN", f'{job} ok {name} ({len(body)} B, 0.3 ms): {{"seconds":1.0,"events":'
                                      f'[[0.4,"BAG_UPDATE",[0],1]],"counts":{{"BAG_UPDATE":1}},"dropped":0}}')
        elif b"local EVENTS, QUIET" in body:                            # probes.trace_start()
            self.comp.on_debug("RUN", f'{job} ok {name} ({len(body)} B, 0.3 ms): {{"tracing":1,"unknown":[]}}')
        elif b"SlashCmdList" in body:                                   # probes.slash_call(): the handler ran
            self.comp.on_debug("RUN", f"{job} ok {name} ({len(body)} B, 0.3 ms): shown")
        elif b"error(" in body:
            self.comp.on_debug("RUN", f"{job} error {name}: {name}:1: boom\n[string \"{name}\"]:1: in main chunk")
        else:
            self.comp.on_debug("RUN", f"{job} ok {name} ({len(body)} B, 0.3 ms): 42, \"x\"")


def request(handle, method, path, body=None):
    """the JSON answer of one request to a daemon started here"""
    req = urllib.request.Request(handle.url.rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {handle.token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


class ModeSwitch(unittest.TestCase):
    """the 设置 page's 开发者模式: a switch installs what the mode needs; a restarted daemon keeps the saved mode"""

    def test_switch_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(tmp) / "home")}):
            client = Path(tmp) / "client"
            addons = client / "Interface" / "AddOns"
            addons.mkdir(parents=True)

            def factory(on_debug, log):                  # no Companion: the client folder has no mailbox at times
                return CompanionLoop(None, find_window=lambda: None, files=False, on_debug=on_debug, log=log)
            handle = server.start(game_dir=str(client), worker_factory=factory, mutex_name=MUTEX + "M")
            try:
                self.assertEqual(handle.mode, "developer")                           # nothing saved yet
                res = request(handle, "POST", "/api/settings", {"mode": "player"})
                self.assertEqual((res["mode"], res["install"]["mode"]), ("player", "player"))
                self.assertTrue((addons / "!WuxianWorkshop" / "Core.lua").is_file())
                self.assertFalse((addons / "WoWBridge").exists())
                checks = {c["id"] for c in request(handle, "GET", "/api/doctor")["checks"]}
                self.assertIn("developer_components", checks)
                self.assertNotIn("mailbox", checks)
                self.assertNotIn("install", request(handle, "POST", "/api/settings", {"mode": "player"}))   # no change
            finally:
                handle.stop()
            handle = server.start(game_dir=str(client), worker_factory=factory, mutex_name=MUTEX + "M")
            try:
                self.assertEqual(handle.mode, "player")                              # the saved mode, not the default
                res = request(handle, "POST", "/api/settings", {"mode": "developer"})
                self.assertTrue(res["install"]["restart_for_link"])                  # WoWBridge and its mailbox are new
                self.assertTrue((addons / "WoWBridge" / "mail" / "04096.ttf").is_file())
                self.assertEqual(request(handle, "GET", "/api/status")["daemon"]["mode"], "developer")
            finally:
                handle.stop()


class LiveDaemon(unittest.TestCase):
    """one daemon for the class: starting uvicorn takes a moment"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name) / "home"
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(cls.home)})
        cls.env.start()
        cls.client = Path(cls.tmp.name) / "client"
        cls.addons = cls.client / "Interface" / "AddOns"
        (cls.addons / "WoWBridge").mkdir(parents=True)
        MB.install(cls.addons, pool=64)
        foo = cls.addons / "Foo"
        foo.mkdir()
        (foo / "Foo.toc").write_text("## Title: Foo\nCore.lua\nExtra.lua\n", encoding="utf-8")
        (foo / "Core.lua").write_text("-- core\n", encoding="utf-8")
        (foo / "Extra.lua").write_text("error('x')\n", encoding="utf-8")
        cls.fake = None

        def factory(on_debug, log):
            cls.fake = FakeGame(cls.addons, on_debug=on_debug, log=log)
            return cls.fake

        cls.handle = server.start(mode="developer", capture="gdi", game_dir=str(cls.client), worker_factory=factory,
                                  mutex_name=MUTEX)
        cls.base = cls.handle.url.rstrip("/")
        cls.token = cls.handle.token

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, token=None, headers=None, timeout=20):
        """(status, json) with the token unless told otherwise"""
        data = json.dumps(body).encode() if body is not None else None
        h = {"Authorization": f"Bearer {self.token if token is None else token}"}
        if token == "":
            h = {}
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            with e:
                raw = e.read()
            return e.code, (json.loads(raw) if raw else None)

    # --- access ----------------------------------------------------------------------------------------------------

    def test_token_required(self):
        status, body = self.call("GET", "/api/status", token="")
        self.assertEqual((status, body["error"]["code"]), (401, "unauthorized"))
        status, body = self.call("GET", "/api/status", token="wrong")
        self.assertEqual(status, 401)
        status, body = self.call("POST", "/api/say", {"text": "x"}, token="")
        self.assertEqual(status, 401)
        status, _ = self.call("GET", f"/api/status?token={self.token}", token="")     # GET may use the query string
        self.assertEqual(status, 200)
        status, _ = self.call("GET", "/api/status")
        self.assertEqual(status, 200)

    def test_host_and_origin_checks(self):
        port = self.handle.port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.putrequest("GET", "/api/status", skip_host=True)
        conn.putheader("Host", f"evil.example:{port}")
        conn.putheader("Authorization", f"Bearer {self.token}")
        conn.endheaders()
        resp = conn.getresponse()
        self.assertEqual((resp.status, json.loads(resp.read())["error"]["code"]), (403, "bad_host"))
        conn.close()
        status, body = self.call("GET", "/api/status", headers={"Origin": "http://evil.example"})
        self.assertEqual((status, body["error"]["code"]), (403, "bad_origin"))
        status, _ = self.call("GET", "/api/status", headers={"Origin": f"http://127.0.0.1:{port}"})
        self.assertEqual(status, 200)
        status, _ = self.call("GET", "/api/status", headers={"Origin": f"http://localhost:{port}"})
        self.assertEqual(status, 200)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)      # localhost:<port> is a Host too
        conn.putrequest("GET", "/api/status", skip_host=True)
        conn.putheader("Host", f"localhost:{port}")
        conn.putheader("Authorization", f"Bearer {self.token}")
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()

    def test_session_gives_the_token_to_the_page(self):
        status, body = self.call("GET", "/api/session", token="")
        self.assertEqual((status, body["token"], body["port"]), (200, self.token, self.handle.port))
        status, body = self.call("GET", "/api/session", token="", headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_root_serves_the_page_or_says_why_not(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as resp:      # no token needed for the page
            self.assertEqual(resp.status, 200)
            body = resp.read()
        self.assertTrue(b"<html" in body.lower() or b"/api/" in body, body[:200])

    # --- the endpoints ---------------------------------------------------------------------------------------------

    def test_status(self):
        status, s = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(s["daemon"]["pid"], os.getpid())
        self.assertEqual(s["daemon"]["mode"], "developer")
        self.assertFalse(s["game"]["found"])
        self.assertEqual(s["link"]["capture"], "gdi")
        self.assertIn(s["link"]["state"], ("online", "offline"))
        self.assertEqual(s["link"]["session"], 5)
        self.assertIsInstance(s["link"]["slot_next"], int)
        self.assertEqual(s["mcp_url"], f"{self.base}/mcp")
        self.assertEqual(s["addons_dir"], str(self.addons))
        self.assertIsInstance(s["watch"], list)
        self.assertIn("reload_pending", s)

    def test_run_ok_error_and_timeout(self):
        status, res = self.call("POST", "/api/run", {"code": "return 1 + 1"})
        self.assertEqual(status, 200)
        self.assertEqual((res["ok"], res["values"], res["chunk"], res["ms"]), (True, ["42", '"x"'], "=run", 0.3))
        self.assertIsInstance(res["job"], int)
        status, res = self.call("POST", "/api/run", {"code": "error('boom')"})
        self.assertEqual((status, res["ok"]), (200, False))
        self.assertIn("boom", res["error"])
        self.assertIn("in main chunk", res["stack"])
        t0 = time.time()
        status, res = self.call("POST", "/api/run", {"code": "sleep", "timeout_ms": 300})
        self.assertEqual((status, res["error"]["code"]), (504, "timeout"))
        self.assertIsInstance(res["job"], int)
        self.assertLess(time.time() - t0, 5)
        status, res = self.call("POST", "/api/run", {"code": ""})
        self.assertEqual(status, 400)
        status, res = self.call("POST", "/api/run", {"code": "x", "timeout_ms": "soon"})
        self.assertEqual(status, 400)
        status, _ = self.call("POST", "/api/run", None, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, log = self.call("GET", "/api/logs?kinds=RUN")
        self.assertTrue(any(e["job"] is not None and e["kind"] == "RUN" for e in log["entries"]))

    def test_load_addon_file_404_403(self):
        status, res = self.call("POST", "/api/load", {"target": "Foo"})
        self.assertEqual(status, 200)
        self.assertEqual(res["addon"], "Foo")
        self.assertEqual([Path(f["file"]).name for f in res["files"]], ["Core.lua", "Extra.lua"])
        self.assertEqual([f["ok"] for f in res["files"]], [True, False])       # the second file errors, both ran
        self.assertIn("boom", res["files"][1]["error"])
        self.assertFalse(res["ok"])
        status, res = self.call("POST", "/api/load", {"target": "Foo/Core.lua", "mode": "file"})
        self.assertEqual((status, res["ok"], res["addon"]), (200, True, None))
        status, res = self.call("POST", "/api/load", {"target": "Nowhere"})
        self.assertEqual((status, res["error"]["code"]), (404, "not_found"))
        status, res = self.call("POST", "/api/load", {"target": "WoWBridge"})
        self.assertEqual((status, res["error"]["code"]), (403, "forbidden"))
        status, res = self.call("POST", "/api/load", {"target": "Foo", "mode": "file"})
        self.assertEqual(status, 400)

    def test_watch(self):
        status, res = self.call("POST", "/api/watch", {"action": "start", "target": "Foo"})
        self.assertEqual(status, 200)
        self.assertEqual([Path(p).name for p in res["watched"]], ["Core.lua", "Extra.lua"])
        status, res = self.call("POST", "/api/watch", {"action": "list"})
        self.assertEqual(len(res["watched"]), 2)
        status, s = self.call("GET", "/api/status")
        self.assertEqual(len(s["watch"]), 2)
        status, res = self.call("POST", "/api/watch", {"action": "start", "target": "Nowhere"})
        self.assertEqual(status, 404)
        status, res = self.call("POST", "/api/watch", {"action": "stop"})
        self.assertEqual((status, res["watched"]), (200, []))
        status, _ = self.call("POST", "/api/watch", {"action": "begin"})
        self.assertEqual(status, 400)

    def test_reload_say_snap(self):
        status, res = self.call("POST", "/api/reload", {"reason": "test"})
        self.assertEqual((status, res["requested"]), (200, True))
        self.assertIsInstance(res["nonce"], int)
        status, s = self.call("GET", "/api/status")
        self.assertTrue(s["reload_pending"])
        status, res = self.call("POST", "/api/say", {"text": "你好"})
        self.assertEqual((status, res), (200, {"queued": True}))
        status, res = self.call("POST", "/api/say", {})
        self.assertEqual(status, 400)
        status, res = self.call("POST", "/api/snap", {})
        self.assertEqual((status, res["error"]["code"]), (409, "no_game"))
        status, res = self.call("POST", "/api/snap", {"region": [1, 2, 3]})
        self.assertEqual(status, 400)
        status, _ = self.call("GET", "/api/snaps/none.png")
        self.assertEqual(status, 404)
        status, _ = self.call("GET", "/api/snaps/..%5c..%5cdaemon.json")
        self.assertEqual(status, 404)

    def test_logs_cursor(self):
        self.call("POST", "/api/say", {"text": "cursor one"})
        self.call("POST", "/api/say", {"text": "cursor two"})
        status, res = self.call("GET", "/api/logs?since=0&limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(len(res["entries"]), 2)
        self.assertEqual(res["next"], res["entries"][-1]["id"] + 1)
        self.assertFalse(res["truncated"])
        status, more = self.call("GET", f"/api/logs?since={res['next']}&limit=1000")
        self.assertTrue(all(e["id"] >= res["next"] for e in more["entries"]))
        texts = [e["text"] for e in res["entries"] + more["entries"]]
        self.assertIn("say: cursor one", texts)
        self.assertIn("say: cursor two", texts)
        ids = [e["id"] for e in res["entries"] + more["entries"]]
        self.assertEqual(ids, sorted(ids))
        status, info = self.call("GET", "/api/logs?kinds=INFO&limit=5")
        self.assertTrue(all(e["kind"] == "INFO" for e in info["entries"]))
        status, _ = self.call("GET", "/api/logs?since=x")
        self.assertEqual(status, 400)
        debug_log = (self.home / "logs" / "debug.log").read_text(encoding="utf-8")
        self.assertIn("INFO say: cursor one", debug_log)

    def test_events_sse(self):
        status, res = self.call("GET", "/api/logs?since=0&limit=1")
        req = urllib.request.Request(f"{self.base}/api/events?token={self.token}&since={res['next']}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
            self.call("POST", "/api/say", {"text": "over sse"})
            seen, event, data = [], None, []
            deadline = time.time() + 10
            while time.time() < deadline:
                line = resp.readline().decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and data:
                    seen.append((event, json.loads("".join(data))))
                    event, data = None, []
                    if any(ev == "log" and d["text"] == "say: over sse" for ev, d in seen):
                        break
        kinds = [ev for ev, _ in seen]
        self.assertIn("status", kinds)                                   # the first status comes right after the backlog
        self.assertTrue(any(ev == "log" and d["text"] == "say: over sse" and d["kind"] == "INFO" for ev, d in seen), seen)

    def test_doctor_install_settings(self):
        status, res = self.call("GET", "/api/doctor")
        self.assertEqual(status, 200)
        self.assertIsInstance(res["checks"], list)
        for c in res["checks"]:
            self.assertEqual(set(c) >= {"id", "ok", "detail"}, True)
        status, res = self.call("POST", "/api/install", {"game_dir": str(self.client), "clean": True})
        self.assertEqual(status, 200)
        self.assertIn("installed", res)
        self.assertIn("removed", res)
        self.assertIn("restart_required", res)
        status, res = self.call("GET", "/api/settings")
        self.assertEqual((status, res["mode"], res["capture"]), (200, "developer", "gdi"))
        status, res = self.call("POST", "/api/settings", {"capture": "wgc", "autostart": True})
        self.assertEqual((status, res["capture"], res["autostart"]), (200, "wgc", True))
        self.assertEqual(json.loads((self.home / "state" / "settings.json").read_text(encoding="utf-8"))["capture"], "wgc")
        status, res = self.call("POST", "/api/settings", {"capture": "gdi"})
        self.assertEqual(res["capture"], "gdi")
        status, res = self.call("POST", "/api/settings", {"capture": "x11"})
        self.assertEqual(status, 400)
        status, res = self.call("POST", "/api/show")
        self.assertEqual((status, res), (200, {"shown": False}))

    def test_fix(self):
        """POST /api/fix: the addon fixes are an install; a runtime's is Microsoft's installer, started (a stand-in)"""
        from wuxianworkshop.installer import runtimes
        with mock.patch.object(runtimes, "install", return_value=dict(name="webview2", path=r"C:\x\MicrosoftEdgeWebview2Setup.exe",
                                                                      pid=1, exit_code=None)) as install:
            status, res = self.call("POST", "/api/fix", {"fix": "install_webview2"})
        self.assertEqual((status, res["started"], res["title"]), (200, True, "Microsoft Edge WebView2 运行时"))
        install.assert_called_once_with("webview2")
        with mock.patch.object(runtimes, "install", side_effect=runtimes.InstallError("not Microsoft's")):
            status, res = self.call("POST", "/api/fix", {"fix": "install_dotnet"})
        self.assertEqual((status, res["error"]["code"]), (502, "install_failed"))
        status, res = self.call("POST", "/api/fix", {"fix": "format_c"})
        self.assertEqual(status, 400)
        status, res = self.call("POST", "/api/fix", {"fix": "install", "game_dir": str(self.client)})
        self.assertEqual(status, 200)
        self.assertIn("installed", res["install"])

    def test_addons_and_errors(self):
        status, res = self.call("GET", "/api/addons")
        self.assertEqual(status, 200)
        self.assertIn("WoWBridge", [a["name"] for a in res["addons"]])
        self.assertFalse(res["errors"]["available"])                     # no SavedVariables in the test client
        status, res = self.call("GET", "/api/errors?addon=WoWBridge&limit=5")
        self.assertEqual((status, res["entries"], res["total"]), (200, [], 0))
        status, res = self.call("GET", "/api/errors?limit=many")
        self.assertEqual(status, 400)
        nowhere = urllib.parse.quote(str(self.client / "nowhere"))
        status, res = self.call("GET", f"/api/addons?game_dir={nowhere}")
        self.assertEqual((status, res["error"]["code"]), (404, "no_game_folder"))

    def test_daemon_json_describes_this_daemon(self):
        info = read_daemon_json()
        self.assertEqual((info["pid"], info["port"], info["token"], info["mode"]), (os.getpid(), self.handle.port, self.token, "developer"))
        self.assertEqual(info["mcp_url"], f"{self.base}/mcp")
        self.assertIn("started", info)
        self.assertIn("version", info)

    def test_second_instance_is_refused(self):
        with self.assertRaises(server.AlreadyRunning) as cm:
            server.start(worker_factory=lambda on_debug, log: None, mutex_name=MUTEX)
        self.assertEqual(cm.exception.info["port"], self.handle.port)
        self.assertTrue(read_daemon_json())                              # ours is still there

    # --- MCP -------------------------------------------------------------------------------------------------------

    def test_mcp_tools_and_status(self):
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        async def go():
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {self.token}"}) as http:
                async with Client(streamable_http_client(f"{self.base}/mcp", http_client=http)) as c:
                    tools = {t.name: t for t in (await c.list_tools()).tools}
                    result = await c.call_tool("status", {})
                    run = await c.call_tool("run", {"code": "return 7"})
                    bad = await c.call_tool("run", {"code": "error('x')"})
                    return tools, result, run, bad

        tools, result, run, bad = asyncio.run(go())
        self.assertEqual(set(tools) >= {"status", "run", "load", "watch", "snap", "reload", "logs", "say", "doctor", "install"}, True)
        self.assertTrue(tools["status"].annotations.read_only_hint)
        self.assertTrue(tools["logs"].annotations.read_only_hint)
        self.assertTrue(tools["reload"].annotations.destructive_hint)
        self.assertTrue(tools["install"].annotations.destructive_hint)
        self.assertFalse(tools["run"].annotations.read_only_hint)
        self.assertEqual(result.structured_content["daemon"]["pid"], os.getpid())
        self.assertFalse(result.is_error)
        self.assertEqual(run.structured_content["values"], ["42", '"x"'])
        self.assertTrue(bad.is_error)
        self.assertIn("boom", bad.content[0].text)

    def test_mcp_needs_the_token(self):
        req = urllib.request.Request(f"{self.base}/mcp", data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}', method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        with cm.exception:
            self.assertEqual(cm.exception.code, 401)

    # --- the command line as a client ------------------------------------------------------------------------------

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_status_run_logs_watch(self):
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 0, err)
        self.assertIn(f"pid {os.getpid()}", out)
        self.assertIn("game    not running", out)
        code, out, err = self.run_cli("run", "return", "1")
        self.assertEqual((code, out), (0, '42\n"x"\n'))
        code, out, err = self.run_cli("run", "error('x')")
        self.assertEqual(code, 1)
        self.assertIn("boom", err)
        code, out, err = self.run_cli("run", "--timeout", "0.3", "sleep")
        self.assertEqual(code, 1)
        self.assertIn("timeout", err)
        code, out, err = self.run_cli("load", "Foo")
        self.assertEqual(code, 1)                                       # Extra.lua errors
        self.assertIn("ok     ", out)
        self.assertIn("ERROR  ", out)
        code, out, err = self.run_cli("load", "Foo/Core.lua")
        self.assertEqual(code, 0)
        code, out, err = self.run_cli("logs", "--since", "0", "--limit", "3")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.rstrip("\n").split("\n")) >= 3, True)
        code, out, err = self.run_cli("logs")
        self.assertEqual(code, 0)
        code, out, err = self.run_cli("watch", "list")
        self.assertEqual((code, out), (0, "(nothing is watched)\n"))
        code, out, err = self.run_cli("say", "hello", "there")
        self.assertEqual((code, out), (0, "queued\n"))
        code, out, err = self.run_cli("snap")
        self.assertEqual(code, 1)
        self.assertIn("no game window", err)
        code, out, err = self.run_cli("reload")
        self.assertEqual(code, 0)
        self.assertIn("reload requested", out)
        code, out, err = self.run_cli("doctor")
        self.assertIn(code, (0, 1))
        code, out, err = self.run_cli("mcp-config", "claude")
        self.assertIn(f"{self.base}/mcp", out)                          # the running daemon's http entry is in it


class Lifecycle(unittest.TestCase):
    """start, daemon.json, quit through the API, everything gone"""

    def test_start_quit_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"WUXIAN_HOME": tmp}):
            addons = Path(tmp) / "Interface" / "AddOns"
            (addons / "WoWBridge").mkdir(parents=True)
            MB.install(addons, pool=16)
            quit_seen = []
            handle = server.start(worker_factory=lambda on_debug, log: FakeGame(addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "L")
            handle.on_quit = lambda: quit_seen.append(True)
            try:
                self.assertTrue(handle.running)
                self.assertEqual(read_daemon_json()["port"], handle.port)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = cli.main(["quit"])                           # POST /api/quit through the client
                self.assertEqual(code, 0)
                self.assertIn("stopped", out.getvalue())
                self.assertTrue(handle.wait(10))
                self.assertFalse(handle.running)
                self.assertIsNone(read_daemon_json())
                self.assertEqual(quit_seen, [True])
                with self.assertRaises(OSError):
                    urllib.request.urlopen(handle.url, timeout=2)
                again = server.start(worker_factory=lambda on_debug, log: FakeGame(addons, on_debug=on_debug, log=log),
                                     mutex_name=MUTEX + "L")             # the mutex was released
                again.stop()
                self.assertIsNone(read_daemon_json())
            finally:
                handle.stop()


class ShowRequests(unittest.TestCase):
    """POST /api/show (DaemonHandle.show): the shell's window shows. The shell sets on_show while it makes the window,
    seconds after the daemon listens: until then a request is kept for that window (window=True)"""

    def handle(self, window):
        return server.DaemonHandle(None, None, "t", "developer", window=window)

    def test_no_window_comes(self):
        """`wuxian serve`: shown=false, the program opens a window on the daemon's page itself"""
        handle = self.handle(False)
        self.assertFalse(handle.show())
        shown = threading.Event()
        handle.on_show = shown.set
        self.assertFalse(shown.wait(0.2))                                # nothing was kept

    def test_kept_until_the_window_is_up(self):
        handle = self.handle(True)
        self.assertTrue(handle.show())
        self.assertTrue(handle.show())                                   # twice before it is up: it shows once
        calls, shown = [], threading.Event()

        def on_show():
            calls.append(threading.current_thread().name)
            shown.set()
        handle.on_show = on_show
        self.assertTrue(shown.wait(5))
        self.assertEqual(calls, ["wuxian-show"])                         # on its own thread, not the setter's
        shown.clear()
        self.assertTrue(handle.show())                                   # from now on at once
        self.assertTrue(shown.wait(5))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
