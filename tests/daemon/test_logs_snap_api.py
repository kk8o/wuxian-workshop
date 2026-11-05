"""The log read from its newest end (Journal.since tail, Service.logs, GET /api/logs?tail=1, the MCP logs tool, wuxian
logs --tail), the status of /api/events every STATUS_EVERY seconds while entries keep coming, snaps as JPEG when asked
(the MCP tools ask; /api/snaps serves both kinds), a load's file listing off the event loop, and debug.log let go when
the daemon stops."""
import asyncio
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import Future
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from tests.daemon.test_server import MUTEX, FakeGame
from wuxianworkshop.cli import main as cli
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.mcp import server as mcp_server
from wuxianworkshop.paths import snaps_dir


def journal(n=10, capacity=100):
    """a journal of n entries, every third an ERR"""
    j = Journal(capacity=capacity)
    for i in range(1, n + 1):
        j.add("ERR" if i % 3 == 0 else "OUT", f"entry {i}")
    return j


class JournalTail(unittest.TestCase):
    def test_the_newest_in_id_order(self):
        j = journal()
        entries, next_id, truncated = j.since(0, 3, tail=True)
        self.assertEqual([e["id"] for e in entries], [8, 9, 10])
        self.assertEqual((next_id, truncated), (11, False))
        entries, next_id, _ = j.since(0, 3)                              # without tail: as before, the oldest
        self.assertEqual(([e["id"] for e in entries], next_id), ([1, 2, 3], 4))

    def test_kinds_since_and_a_limit_past_them_all(self):
        j = journal()
        entries, next_id, _ = j.since(0, 2, ["ERR"], tail=True)
        self.assertEqual(([e["id"] for e in entries], next_id), ([6, 9], 11))
        entries, _, _ = j.since(7, 100, tail=True)
        self.assertEqual([e["id"] for e in entries], [7, 8, 9, 10])
        entries, next_id, _ = j.since(11, 5, tail=True)
        self.assertEqual((entries, next_id), ([], 11))

    def test_truncated_as_before(self):
        j = journal(20, capacity=5)
        entries, _, truncated = j.since(0, 2, tail=True)
        self.assertEqual(([e["id"] for e in entries], truncated), ([19, 20], True))
        self.assertFalse(j.since(16, 2, tail=True)[2])

    def test_events_and_entries_merged(self):
        j = journal(4)
        j.add("EVENT", json.dumps({"a": "Foo", "t": "scan.done", "d": 1}))
        j.add("OUT", "after")
        entries, _, _ = j.since(0, 3, tail=True)
        self.assertEqual([e["kind"] for e in entries], ["OUT", "EVENT", "OUT"])


class ServiceLogs(unittest.IsolatedAsyncioTestCase):
    async def test_tail_and_the_limit(self):
        svc = Service(lambda **kw: None, journal=journal(1500, capacity=5000))
        res = await svc.logs(0, 2, "err", tail=True)
        self.assertEqual([e["text"] for e in res["entries"]], ["entry 1497", "entry 1500"])
        self.assertEqual((res["next"], res["truncated"]), (1501, False))
        res = await svc.logs(0, 5000, None, tail=True)                    # at most 1000, the newest
        self.assertEqual((len(res["entries"]), res["entries"][0]["id"], res["entries"][-1]["id"]), (1000, 501, 1500))
        res = await svc.logs(0, 2)
        self.assertEqual([e["id"] for e in res["entries"]], [1, 2])


class Worker:
    """the worker thread's side of a snap (as in test_snap_api)"""

    def __init__(self):
        self.win = SimpleNamespace(w=320, h=200, minimized=False)
        self.wgc = None

    def request(self, fn, *args):
        fut = Future()
        try:
            fut.set_result(fn(*args))
        except BaseException as e:
            fut.set_exception(e)
        return fut


class SnapFormats(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": self.tmp.name})
        self.env.start()
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        img = np.full((200, 320, 3), (40, 44, 52), np.uint8)
        img[50:150, 60:260] = (90, 90, 90)
        self.capture = mock.patch("wuxianworkshop.daemon.service.capture", lambda win, wgc: img.copy())
        self.capture.start()

    def tearDown(self):
        self.capture.stop()
        self.env.stop()
        self.tmp.cleanup()

    async def test_png_by_default_jpeg_when_asked(self):
        res = await self.svc.snap(None, None)
        self.assertEqual((Path(res["path"]).suffix, res["format"], res["url"][-4:]), (".png", "png", ".png"))
        self.assertEqual((await self.svc.snap_bytes(res))[:4], b"\x89PNG")
        res = await self.svc.snap([0, 0, 100, 100], None, "jpeg")
        self.assertEqual((Path(res["path"]).suffix, res["format"], res["width"]), (".jpg", "jpeg", 100))
        self.assertEqual((await self.svc.snap_bytes(res))[:3], b"\xff\xd8\xff")
        with self.assertRaises(ApiError) as e:
            await self.svc.snap(None, None, "gif")
        self.assertEqual(e.exception.status, 400)

    async def test_try_and_inspect_pass_it_on(self):
        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            if "probe" in chunk:
                return dict(ok=True, values=["1/1\n" + json.dumps({"screen": [320, 200], "frames": [
                    {"name": "Win", "rect": [60, 50, 200, 100]}]})])
            return dict(ok=True, values=[])
        self.svc.run = run
        self.svc.caught_up = lambda wait=6.0: asyncio.sleep(0, True)
        res = await self.svc.inspect("Win", snap=True, snap_format="jpeg")
        self.assertTrue(res["snap"]["path"].endswith(".jpg"))
        res = await self.svc.try_(code="x()", seconds=0, snap=True, snap_format="jpeg")
        self.assertTrue(res["snap"]["path"].endswith(".jpg"))
        res = await self.svc.try_(code="x()", seconds=0, snap=True)
        self.assertTrue(res["snap"]["path"].endswith(".png"))
        with self.assertRaises(ApiError):
            await self.svc.try_(code="x()", seconds=0, snap=True, snap_format="webp")


class Backend:
    """what the MCP tools call, recording the arguments; its snaps' bytes are PNG (an older daemon) or JPEG"""

    def __init__(self, data):
        self.data, self.calls = data, []

    def __getattr__(self, name):
        async def method(*args):
            self.calls.append((name, args))
            if name in ("snap", "try_", "inspect"):
                shot = dict(path="C:/x/snap-1.jpg", width=4, height=3, url="/api/snaps/snap-1.jpg")
                return shot if name == "snap" else dict(ok=True, summary="", snap=shot)
            if name == "snap_bytes":
                return self.data
            return dict(entries=[], next=1, truncated=False)
        return method


class McpTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def tool(self, backend, name, args):
        return asyncio.run(mcp_server.build_server(backend).call_tool(name, args))

    def test_the_pictures_are_jpeg(self):
        b = Backend(b"\xff\xd8\xff\xe0jpeg")
        res = self.tool(b, "snap", {})
        self.assertEqual(res.content[0].mime_type, "image/jpeg")
        self.assertEqual(b.calls[0], ("snap", (None, 1280, "jpeg")))
        for name, args in (("try", {"code": "x()", "snap": True}), ("inspect", {"target": "Win", "snap": True})):
            b.calls.clear()
            res = self.tool(b, name, args)
            self.assertEqual(b.calls[0][1][-1], "jpeg")
            images = [c for c in res.content if getattr(c, "type", None) == "image"]
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].mime_type, "image/jpeg")

    def test_an_older_daemon_sends_png(self):
        res = self.tool(Backend(b"\x89PNG\r\n\x1a\nrest"), "snap", {})
        self.assertEqual(res.content[0].mime_type, "image/png")

    def test_logs_tail(self):
        b = Backend(b"")
        self.tool(b, "logs", {"tail": True, "limit": 20, "kinds": "ERR"})
        self.tool(b, "logs", {})
        self.assertEqual(b.calls, [("logs", (0, 20, "ERR", True)), ("logs", (0, 200, None, False))])

    def test_the_instructions_send_load_first(self):
        text = mcp_server.INSTRUCTIONS
        self.assertLessEqual(len(text), 2000)
        self.assertIn("`load` (it checks first", text)
        self.assertNotIn("`check` (fix every error), `load`", text)


class LiveDaemon(unittest.TestCase):
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
        (foo / "Foo.toc").write_text("## Title: Foo\nCore.lua\n", encoding="utf-8")
        (foo / "Core.lua").write_text("-- core\n", encoding="utf-8")
        cls.handle = server.start(mode="developer", capture="gdi", game_dir=str(cls.client),
                                  worker_factory=lambda on_debug, log: FakeGame(cls.addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "Tail")
        cls.base = cls.handle.url.rstrip("/")

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def get(self, path, raw=False):
        req = urllib.request.Request(self.base + path, headers={"Authorization": f"Bearer {self.handle.token}"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return (resp.headers["content-type"], resp.read()) if raw else json.loads(resp.read())

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self.handle.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_logs_tail_over_http_and_the_cli(self):
        for i in range(5):
            self.handle.service.journal.add("OUT", f"tail {i}")
        newest = self.get("/api/logs?tail=1&limit=2")
        self.assertEqual([e["text"] for e in newest["entries"]], ["tail 3", "tail 4"])
        self.assertEqual(newest["next"], newest["entries"][-1]["id"] + 1)
        self.assertEqual(self.get("/api/logs?tail=true&limit=2&kinds=OUT")["entries"], newest["entries"])
        oldest = self.get("/api/logs?tail=0&limit=2")
        self.assertEqual([e["id"] for e in oldest["entries"]], [1, 2])
        out = io.StringIO()
        with mock.patch.object(cli, "connect", return_value=cli_client(self.handle)), redirect_stdout(out):
            cli.main(["logs", "--tail", "--limit", "2", "--kinds", "OUT"])
        self.assertEqual([line.split(" OUT ")[-1] for line in out.getvalue().splitlines()], ["tail 3", "tail 4"])

    def test_status_keeps_coming_while_entries_do(self):
        stop = threading.Event()

        def chatter():                     # an addon that never stops printing: an entry every 50 ms
            while not stop.is_set():
                self.handle.service.journal.add("OUT", "chatter")
                time.sleep(0.05)
        with mock.patch.object(server, "STATUS_EVERY", 0.3):
            req = urllib.request.Request(f"{self.base}/api/events?token={self.handle.token}&since=0&kinds=OUT")
            t = threading.Thread(target=chatter)
            with urllib.request.urlopen(req, timeout=10) as resp:
                t.start()
                try:
                    statuses, logs, event = 0, 0, None
                    deadline = time.time() + 1.6
                    while time.time() < deadline:
                        line = resp.readline().decode("utf-8").rstrip("\r\n")
                        if line.startswith("event:"):
                            event = line[6:].strip()
                            statuses += event == "status"
                            logs += event == "log"
                finally:
                    stop.set()
                    t.join()
        self.assertGreater(logs, 15)
        self.assertGreaterEqual(statuses, 4)               # the first, then one every 0.3 s although entries kept coming

    def test_snaps_of_both_kinds_are_served(self):
        folder = snaps_dir()
        (folder / "snap-000000-001.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
        (folder / "snap-000000-002.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.get("/api/snaps/snap-000000-001.jpg", raw=True), ("image/jpeg", b"\xff\xd8\xff\xe0jpeg"))
        self.assertEqual(self.get("/api/snaps/snap-000000-002.png", raw=True)[0], "image/png")
        status, res = self.post("/api/snap", {"format": "gif"})
        self.assertEqual((status, res["error"]["code"]), (400, "bad_request"))
        status, res = self.post("/api/snap", {"format": "jpeg"})
        self.assertEqual((status, res["error"]["code"]), (409, "no_game"))   # understood; no window to picture

    def test_a_load_lists_its_files_off_the_event_loop(self):
        seen = []
        real = server.Service.load_files

        def load_files(addons, target, mode):
            try:
                asyncio.get_running_loop()
                seen.append("on the loop")
            except RuntimeError:
                seen.append("off the loop")
            return real(addons, target, mode)
        with mock.patch.object(server.Service, "load_files", staticmethod(load_files)):
            status, res = self.post("/api/load", {"target": "Foo"})
            self.assertEqual(status, 200, res)
            status, res = self.post("/api/load", {"target": "NoSuchAddon"})
            self.assertEqual((status, res["error"]["code"]), (404, "not_found"))
        self.assertEqual(seen, ["off the loop", "off the loop"])


def cli_client(handle):
    from wuxianworkshop.cli.client import DaemonClient
    return DaemonClient(dict(port=handle.port, token=handle.token, url=handle.url))


class DebugLogLetGo(unittest.TestCase):
    def test_the_daemon_lets_debug_log_go_when_it_stops(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"WUXIAN_HOME": tmp}):
            addons = Path(tmp) / "AddOns"
            (addons / "WoWBridge").mkdir(parents=True)
            MB.install(addons, pool=8)
            handle = server.start(mode="developer", capture="gdi", game_dir=tmp, worker_factory=lambda on_debug, log:
                                  FakeGame(addons, on_debug=on_debug, log=log), mutex_name=MUTEX + "Let")
            log = Path(tmp) / "logs" / "debug.log"
            try:
                handle.service.journal.add("INFO", "while it runs")
                self.assertIn("INFO while it runs", log.read_text(encoding="utf-8"))
            finally:
                handle.stop()
            os.replace(log, log.with_name("moved.log"))         # refused on Windows while the daemon held it


if __name__ == "__main__":
    unittest.main()
