"""The fake daemon (scripts/dev_fake_api.py): the page loads from it, /api/session hands out the token, the endpoints the
page uses answer in the shapes of the daemon's answers, and the SSE stream replays from `since`."""
import http.client
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def load_module():
    spec = importlib.util.spec_from_file_location("dev_fake_api", ROOT / "scripts" / "dev_fake_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


F = load_module()
AUTH = {"Authorization": "Bearer t0ken"}


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.fake = F.Fake(token="t0ken", seed=1)
        self.client = TestClient(F.create_app(self.fake, ticker=False), base_url="http://127.0.0.1")
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def test_page_and_session(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<title>无限工坊</title>", r.text)
        self.assertIn('x-data="app"', r.text)
        for name in ("i18n.js", "app.js", "app.css", "htmx.min.js", "alpine.min.js", "icon.png", "logo.svg", "wuxian.ico"):
            self.assertEqual(self.client.get("/" + name).status_code, 200, name)
        r = self.client.get("/api/session")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["token"], "t0ken")
        self.assertEqual(self.client.get("/api/session", headers={"Origin": "http://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)

    def test_token_and_host_checks(self):
        self.assertEqual(self.client.get("/api/status").status_code, 401)
        self.assertEqual(self.client.get("/api/status", headers={"Authorization": "Bearer nope"}).status_code, 401)
        self.assertEqual(self.client.get("/api/status", headers=AUTH).status_code, 200)
        self.assertEqual(self.client.get("/api/status?token=t0ken").status_code, 200)       # SSE and <img> style
        self.assertEqual(self.client.get("/api/status", headers={**AUTH, "Host": "evil.example"}).status_code, 403)

    def test_status_shape(self):
        s = self.client.get("/api/status", headers=AUTH).json()
        self.assertTrue({"daemon", "game", "link", "watch", "reload_pending", "mcp_url"} <= set(s))
        self.assertTrue({"version", "pid", "uptime", "mode", "started", "url", "fake"} <= set(s["daemon"]))
        self.assertTrue({"found", "pid", "build", "version", "client", "start"} <= set(s["game"]))
        self.assertTrue({"state", "session", "slot_next", "slots_left", "hb", "capture", "ping_p50", "last_frame"} <= set(s["link"]))

    def test_logs(self):
        r = self.client.get("/api/logs?limit=2", headers=AUTH).json()
        self.assertEqual([e["id"] for e in r["entries"]], [1, 2])
        self.assertEqual(r["next"], 2)
        r = self.client.get("/api/logs?since=2&kinds=OUT", headers=AUTH).json()
        self.assertTrue(r["entries"])
        self.assertTrue(all(e["kind"] == "OUT" and e["id"] > 2 for e in r["entries"]))
        self.assertEqual(set(r["entries"][0]), {"id", "t", "kind", "text", "addon", "job"})

    def test_run(self):
        ok = self.client.post("/api/run", json={"code": "return 1+1"}, headers=AUTH).json()
        self.assertEqual((ok["ok"], ok["values"], ok["chunk"]), (True, [2], "=run"))
        bad = self.client.post("/api/run", json={"code": "error('boom')"}, headers=AUTH)
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(bad.json()["ok"], False)
        self.assertIn("boom", bad.json()["error"])
        self.assertIn("in main chunk", bad.json()["stack"])
        slow = self.client.post("/api/run", json={"code": "sleep(5)", "timeout_ms": 50}, headers=AUTH)
        self.assertEqual(slow.status_code, 504)
        self.assertEqual(slow.json()["error"]["code"], "timeout")
        self.assertIn("job", slow.json())
        self.assertEqual(self.client.post("/api/run", json={"code": ""}, headers=AUTH).status_code, 400)
        self.assertEqual(self.client.post("/api/run", content=b"{", headers=AUTH).status_code, 400)
        kinds = [e["kind"] for e in self.fake.logs]
        self.assertEqual(kinds.count("RUN"), 3)

    def test_snap(self):
        s = self.client.post("/api/snap", json={"max_width": 640}, headers=AUTH).json()
        self.assertEqual((s["width"], s["height"]), (640, 342))
        self.assertTrue(s["url"].startswith("/api/snaps/"))
        png = self.client.get(s["url"], headers=AUTH)
        self.assertEqual((png.status_code, png.headers["content-type"]), (200, "image/png"))
        self.assertTrue(png.content.startswith(b"\x89PNG"))
        self.assertEqual(self.client.get("/api/snaps/nope.png", headers=AUTH).status_code, 404)
        self.assertEqual(self.client.get("/api/snaps/..%5Cx.png", headers=AUTH).status_code, 404)

    def test_doctor_and_install(self):
        d = self.client.get("/api/doctor", headers=AUTH).json()
        self.assertEqual(set(d), {"checks", "ok", "available"})
        bad = [c for c in d["checks"] if c["ok"] is False]
        self.assertTrue(bad)
        self.assertTrue(all(c["fix"] in ("install", "install_clean", "create_mail") for c in bad))
        self.assertTrue(any(c["ok"] is None for c in d["checks"]))                 # a skipped check
        self.assertEqual(set(d["checks"][0]), {"id", "title", "ok", "detail", "fix"})
        r = self.client.post("/api/install", json={"clean": True}, headers=AUTH).json()
        self.assertTrue({"installed", "removed", "restart_required"} <= set(r))
        self.assertFalse([c for c in self.client.get("/api/doctor", headers=AUTH).json()["checks"] if c["ok"] is False])

    def test_addons_and_errors(self):
        """the shapes of installer/addons.py list_addons() / list_errors(), which the 插件 page reads"""
        d = self.client.get("/api/addons", headers=AUTH).json()
        self.assertTrue({"addons_dir", "client", "addons", "errors"} <= set(d))
        a = d["addons"][0]
        self.assertTrue({"name", "title", "version", "interface", "current", "enabled", "disabled_in", "ours",
                         "load_on_demand", "errors"} <= set(a))
        with_errors = [a["name"] for a in d["addons"] if a["errors"]["signatures"]]
        self.assertTrue(with_errors)
        e = self.client.get(f"/api/errors?addon={with_errors[0]}", headers=AUTH).json()
        self.assertTrue(e["entries"] and all(x["addon"] == with_errors[0] for x in e["entries"]))
        self.assertEqual(self.client.get("/api/errors?limit=x", headers=AUTH).status_code, 400)

    def test_settings(self):
        s = self.client.get("/api/settings", headers=AUTH).json()
        self.assertEqual(set(s), {"mode", "capture", "capture_in_use", "game_dir", "autostart", "language", "onboarded"})
        self.assertIs(s["onboarded"], False)                     # the 开始 page opens first
        self.assertIs(self.client.post("/api/settings", json={"onboarded": True}, headers=AUTH).json()["onboarded"], True)
        r = self.client.post("/api/settings", json={"mode": "player", "capture": "gdi"}, headers=AUTH).json()
        self.assertEqual((r["mode"], r["capture"]), ("player", "gdi"))
        self.assertEqual(self.client.get("/api/status", headers=AUTH).json()["daemon"]["mode"], "player")
        self.assertEqual(self.client.post("/api/settings", json={"developer": True}, headers=AUTH).json()["mode"], "developer")
        self.assertEqual(self.client.post("/api/settings", json={"capture": "x"}, headers=AUTH).status_code, 400)
        self.assertEqual(self.client.post("/api/settings", json={"mode": "x"}, headers=AUTH).status_code, 400)

    def test_agents(self):
        """agents.py's shapes, which the 开始 and 接入 Agent pages read"""
        d = self.client.get("/api/agents", headers=AUTH).json()
        self.assertEqual([h["id"] for h in d["hosts"]], ["claude", "codex", "cursor", "trae-cn", "trae", "workbuddy", "workbuddy-ai"])
        self.assertEqual({h["id"]: h["state"] for h in d["hosts"]}, {"claude": "other", "codex": "absent", "cursor": "missing",
                                                                     "trae-cn": "absent", "trae": "missing", "workbuddy": "absent",
                                                                     "workbuddy-ai": "missing"})
        self.assertEqual([h["id"] for h in d["hosts"] if h["can_verify"]], ["claude", "codex"])
        self.assertTrue({"claude", "codex", "cursor", "trae-cn", "trae", "workbuddy", "workbuddy-ai", "other"} <= set(d["manual"]))
        self.assertEqual(d["program"]["args"], ["mcp"])
        r = self.client.post("/api/agents", json={"action": "connect", "host": "codex"}, headers=AUTH).json()
        self.assertEqual((r["state"], r["verify"]["ok"]), ("ok", True))
        r = self.client.post("/api/agents", json={"action": "connect", "host": "trae-cn"}, headers=AUTH).json()
        self.assertEqual((r["state"], "verify" in r), ("ok", False))      # nothing to ask Trae
        self.assertEqual(self.client.post("/api/agents", json={"action": "disconnect", "host": "codex"}, headers=AUTH).json()["state"], "absent")
        self.assertEqual(self.client.post("/api/agents", json={"action": "connect", "host": "cursor"}, headers=AUTH).status_code, 409)

    def test_history(self):
        """agent/history.py's shapes, which the 插件 page's 历史 reads"""
        rows = {a["name"]: a for a in self.client.get("/api/addons", headers=AUTH).json()["addons"]}
        self.assertEqual((rows["Bar"]["history"]["versions"], rows["OldThing"]["history"]), (4, None))
        d = self.client.get("/api/history?addon=Bar", headers=AUTH).json()
        self.assertEqual([v["id"] for v in d["versions"]], [4, 3, 2, 1])
        self.assertEqual((d["now"]["same"], d["now"]["since"]), (False, 4))
        diff = self.client.get("/api/history?addon=Bar&id=3&against=prev", headers=AUTH).json()
        self.assertEqual((diff["base"], diff["to"]), (2, 3))
        self.assertTrue(any(f["binary"] for f in diff["files"]))
        kept = self.client.post("/api/history", json={"action": "checkpoint", "addon": "Bar", "note": "x"}, headers=AUTH).json()
        self.assertEqual((kept["id"], kept["new"]), (5, True))
        r = self.client.post("/api/history", json={"action": "restore", "addon": "Bar", "id": 2}, headers=AUTH).json()
        self.assertEqual((r["restored"], r["written"]), (2, ["Frames.lua"]))
        self.assertEqual(self.client.post("/api/history", json={"action": "restore", "addon": "Bar", "id": 99}, headers=AUTH).status_code, 404)
        self.assertEqual(self.client.post("/api/history", json={"action": "forget", "addon": "Foo"}, headers=AUTH).json()["forgotten"], True)
        self.assertEqual(self.client.get("/api/history?addon=Foo", headers=AUTH).json()["versions"], [])

    def test_extensions(self):
        """extensions.py's shapes, which the 扩展 page reads: the catalog with each addon's state; install, remove"""
        d = self.client.get("/api/extensions", headers=AUTH).json()
        self.assertEqual(d["catalog"]["error"], "")
        rows = {a["id"]: a for a in d["addons"]}
        self.assertEqual((rows["wuxiankit"]["state"], rows["wuxiankit"]["installed"], rows["raidnotes"]["state"]),
                         ("update", "0.2.0", "available"))
        self.assertEqual(rows["wuxiankit"]["extensions"][0]["title"], {"zh": "调校", "en": "Tune"})
        self.assertEqual([s["title"]["zh"] for s in rows["wuxiankit"]["skills"]], ["写一个标准库扩展", "调一套设置", "分步讲解界面"])
        r = self.client.post("/api/extensions", json={"action": "install", "id": "wuxiankit"}, headers=AUTH).json()
        self.assertEqual((r["result"]["restart"], r["addon"]["state"]), (False, "current"))
        r = self.client.post("/api/extensions", json={"action": "install", "id": "raidnotes"}, headers=AUTH).json()
        self.assertTrue(r["result"]["restart"])                                  # new: the game starts again
        r = self.client.post("/api/extensions", json={"action": "remove", "id": "raidnotes"}, headers=AUTH).json()
        self.assertEqual((r["result"]["removed"], r["addon"]["state"]), (True, "available"))
        self.assertEqual(self.client.post("/api/extensions", json={"action": "install", "id": "nope"}, headers=AUTH).status_code, 404)
        self.assertEqual(self.client.post("/api/extensions", json={"action": "zap", "id": "x"}, headers=AUTH).status_code, 400)

    def test_check(self):
        """agent/lint.py's shape, which the 插件 page's 检查 reads"""
        d = self.client.post("/api/check", json={"target": "Bar", "live": True}, headers=AUTH).json()
        self.assertEqual((d["ok"], d["files"], d["live"]), (False, 3, {"checked": 2}))
        self.assertEqual({f["code"] for f in d["errors"]}, {"syntax", "undefined"})
        self.assertTrue(all({"file", "line", "code", "message", "hint"} <= set(f) for f in d["errors"] + d["warnings"]))
        self.assertTrue(self.client.post("/api/check", json={"target": "WoWBridge"}, headers=AUTH).json()["ok"])

    def test_reload_watch_show(self):
        r = self.client.post("/api/reload", json={"reason": "ui"}, headers=AUTH).json()
        self.assertTrue(r["requested"])
        self.assertIn("nonce", r)
        self.assertTrue(self.client.get("/api/status", headers=AUTH).json()["reload_pending"])
        self.assertEqual(self.client.post("/api/watch", json={"action": "start", "target": "Foo"}, headers=AUTH).json(), {"watched": ["Foo"]})
        self.assertEqual(self.client.post("/api/watch", json={"action": "stop", "target": "Foo"}, headers=AUTH).json(), {"watched": []})
        self.assertEqual(self.client.post("/api/show", headers=AUTH).json(), {"shown": True})
        self.assertEqual(self.client.post("/api/load", json={"target": "WoWBridge"}, headers=AUTH).status_code, 403)


class Stream(unittest.TestCase):
    """SSE through a real socket (TestClient cannot leave an endless stream), on the in-thread server the shell uses"""

    def test_events_replay_from_since(self):
        handle = F.start_in_thread(token="t0ken", ticker=False)
        try:
            handle.fake.entry("OUT", "hello")
            conn = http.client.HTTPConnection("127.0.0.1", handle.port, timeout=5)
            conn.request("GET", "/api/events?token=t0ken&since=2")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
            events, lines = [], []
            while len(events) < 2:
                line = resp.readline().decode().rstrip("\n")
                lines.append(line)
                if line.startswith("event: "):
                    events.append(line[7:])
                self.assertLess(len(lines), 60)
            self.assertEqual(events[0], "log")
            data = json.loads(next(l for l in lines if l.startswith("data: "))[6:])
            self.assertEqual(data["id"], 3)                             # since=2: entries 1 and 2 are not sent again
            self.assertIn("id: 3", lines)
            handle.fake.entry("ERR", "live")                            # a new entry reaches the open stream
            for _ in range(60):
                line = resp.readline().decode().rstrip("\n")
                if line.startswith("data: ") and '"live"' in line:
                    break
            else:
                self.fail("the new entry did not arrive on the stream")
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", handle.port, timeout=5)
            conn.request("GET", "/api/events?since=0")                  # no token
            self.assertEqual(conn.getresponse().status, 401)
            conn.close()
        finally:
            handle.stop()
        self.assertFalse(handle.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
