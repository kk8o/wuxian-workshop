"""The Python SDK (sdk/python/wuxiankit.py) against a fake 无限工坊 daemon (http.server in a thread): finding the daemon,
the token, backoff, calls, proposals and wait, events with a list of topics (and a workshop that takes one topic
only), and an app's stream: open, wakes, repeats dropped, heartbeat, kit.ready, a daemon restart, the end.
Standard library only; the clock is fake, so nothing really waits. WUXIANKIT_SDK may name the SDK file; else it is
looked for as sdk/python/wuxiankit.py above this file."""
import fnmatch
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


def load_sdk():
    named = os.environ.get("WUXIANKIT_SDK")
    candidates = [Path(named)] if named else [p / "sdk" / "python" / "wuxiankit.py"
                                              for p in Path(__file__).resolve().parents]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise ImportError("sdk/python/wuxiankit.py not found above " + __file__)
    spec = importlib.util.spec_from_file_location("wuxiankit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wk = load_sdk()
TOKEN = "t0ken"


class Clock:
    def __init__(self, t=10_000.0):
        self.t, self.slept = t, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


class FakeDaemon:
    """/api/call (kit.app.*, kit.propose, kit.proposal, anything else echoed) and /api/addon_events as the daemon
    answers them. An empty long-poll moves the fake clock on by its wait. lists=False: a workshop before list topics"""

    def __init__(self, clock, lists=True):
        self.clock, self.lists = clock, lists
        self.events, self.next_id = [], 1
        self.requests = []                    # (path, body, auth, clock time)
        self.link_down = False
        self.lease = 600
        self.app_on = True
        self.proposals = {}
        self.on_call = {}                     # name -> fn(body) run before the answer (scripted turns)
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                auth = self.headers.get("Authorization") or ""
                fake.requests.append((self.path, body, auth, fake.clock.t))
                if auth != f"Bearer {TOKEN}":
                    return self.answer(401, {"error": {"code": "unauthorized", "message": "bad token"}})
                if self.path == "/api/call":
                    return self.answer(*fake.call(body))
                if self.path == "/api/addon_events":
                    return self.answer(*fake.pick(body))
                self.answer(404, {"error": {"code": "not_found", "message": self.path}})

            def answer(self, status, obj):
                data = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # ---- the game's side

    def emit(self, topic, data, addon="WuxianKit"):
        with self.lock:
            self.events.append({"id": self.next_id, "t": 0, "addon": addon, "topic": topic, "data": data, "dropped": 0})
            self.next_id += 1

    def wake(self, ids, reason="urgent", app="demo"):
        self.emit(f"app.{app}", {"app": app, "reason": reason, "skipped": 0,
                                 "items": [{"id": i, "time": 1, "source": "game", "kind": "rest"} for i in ids]})

    def status(self, app):
        return {"app": app, "title": app, "on": self.app_on, "ends": 99999, "lease": self.lease, "seen": 1}

    def call(self, body):
        name, args = body.get("name"), body.get("args") or {}
        if name in self.on_call:
            self.on_call[name](body)
        if self.link_down:
            return 409, {"error": {"code": "link_down", "message": "call: no frames come from the game"}}
        if name == "kit.app.open":
            self.app_on = True
            return 200, {"addon": "WuxianKit", "name": name, "ok": True, "result": self.status(args["app"])}
        if name == "kit.app.status":
            return 200, {"ok": True, "result": self.status(args["app"])}
        if name == "kit.app.stop":
            self.app_on = False
            self.emit("kit.app", {"app": args["app"], "on": False, "why": "stopped"})
            return 200, {"ok": True, "result": self.status(args["app"])}
        if name == "kit.propose":
            pid = len(self.proposals) + 1
            self.proposals[pid] = {"id": pid, "state": "pending", "title": args.get("title"), "steps": args["steps"]}
            return 200, {"ok": True, "result": dict(self.proposals[pid])}
        if name == "kit.proposal":
            p = self.proposals.get(args.get("id"))
            if p is None:
                return 400, {"error": {"code": "call_failed", "message": f"no proposal {args.get('id')}"}}
            return 200, {"ok": True, "result": dict(p)}
        if name == "boom":
            return 400, {"error": {"code": "call_failed", "message": "bad args"}}
        return 200, {"ok": True, "result": {"name": name, "args": body.get("args")}}

    def pick(self, body):
        topic = body.get("topic")
        if isinstance(topic, list) and not self.lists:
            return 400, {"error": {"code": "bad_request", "message": "topic: a name or a glob"}}
        patterns = topic if isinstance(topic, list) else [topic] if topic else ["*"]
        since, limit, wait = body["since"], body.get("limit", 100), float(body.get("wait") or 0)
        with self.lock:
            if since == -1:
                since = self.next_id
            hits = [e for e in self.events if e["id"] >= since and fnmatch.fnmatchcase(e["addon"], body["addon"])
                    and any(fnmatch.fnmatchcase(e["topic"], p) for p in patterns)]
            nxt = self.next_id
            if len(hits) > limit:
                hits, nxt = hits[:limit], hits[limit]["id"]
        if not hits and wait:
            self.clock.t += wait                 # the long-poll took its time
        return 200, {"events": hits, "next": nxt, "truncated": False, "session": 7}

    # ---- what came

    def calls(self, name=None):
        return [b for p, b, _, _ in self.requests if p == "/api/call" and (name is None or b["name"] == name)]

    def call_times(self, name):
        return [t for p, b, _, t in self.requests if p == "/api/call" and b["name"] == name]

    def polls(self):
        return [b for p, b, _, _ in self.requests if p == "/api/addon_events"]


def refuse_port(port):
    """urlopen that refuses at once at that port (Windows takes 2 s to refuse a closed one) and works elsewhere"""
    real = wk.urllib.request.urlopen

    def fake(req, timeout=None):
        if f":{port}/" in req.full_url:
            raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        return real(req, timeout=timeout)
    return mock.patch.object(wk.urllib.request, "urlopen", fake)


class Base(unittest.TestCase):
    lists = True

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.json = self.home / "state" / "daemon.json"
        self.json.parent.mkdir(parents=True)
        self.clock = Clock()
        self.fake = self.daemon()
        self.write_json(self.fake.port)
        self.kit = wk.Client(self.json, clock=self.clock, sleep=self.clock.sleep)

    def daemon(self):
        fake = FakeDaemon(self.clock, lists=self.lists)
        self.addCleanup(fake.close)
        return fake

    def write_json(self, port, token=TOKEN, pid=1):
        self.json.write_text(json.dumps({"pid": pid, "port": port, "token": token, "started": pid * 1000,
                                         "url": f"http://127.0.0.1:{port}/"}), encoding="utf-8")

    def stream(self, **decl):
        decl.setdefault("title", "Demo")
        decl.setdefault("hear", ["game:*"])
        s = self.kit.app("demo", **decl)
        self.assertIsNone(s.get(1))                     # opens it and finds nothing yet
        return s


class Discovery(Base):
    def test_where_daemon_json_is(self):
        with mock.patch.dict(os.environ, {"WUXIAN_HOME": r"D:\w"}):
            self.assertEqual(wk.daemon_json_path(), Path(r"D:\w") / "state" / "daemon.json")
        env = {k: v for k, v in os.environ.items() if k != "WUXIAN_HOME"}
        env["LOCALAPPDATA"] = r"C:\L"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(wk.daemon_json_path(), Path(r"C:\L") / "WuxianWorkshop" / "state" / "daemon.json")
        self.assertEqual(wk.daemon_json_path(r"E:\x.json"), Path(r"E:\x.json"))
        self.assertIsNone(wk.read_daemon_json(self.home / "nope.json"))

    def test_the_client_finds_the_daemon_by_wuxian_home(self):
        with mock.patch.dict(os.environ, {"WUXIAN_HOME": str(self.home)}):
            kit = wk.Client()
        self.assertEqual(kit.call("sense.character"), {"name": "sense.character", "args": None})
        path, body, auth, _ = self.fake.requests[0]
        self.assertEqual((path, auth), ("/api/call", f"Bearer {TOKEN}"))
        self.assertEqual((body["addon"], body["name"], body["timeout_ms"]), ("WuxianKit", "sense.character", 10000))
        self.assertEqual(kit.daemon, (1, 1000))

    def test_no_daemon_json(self):
        self.json.unlink()
        with self.assertRaises(wk.WuxianError) as e:
            self.kit.call("x")
        self.assertEqual(e.exception.code, "no_daemon")

    def test_a_refused_connection_and_daemon_json_read_again(self):
        with refuse_port(self.fake.port):
            with self.assertRaises(wk.WuxianError) as e:
                self.kit.call("x")
        self.assertEqual((e.exception.code, e.exception.status), ("no_daemon", 0))
        self.write_json(self.fake.port, pid=2)
        self.assertEqual(self.kit.call("x", {"a": 1})["args"], {"a": 1})
        self.assertEqual(self.kit.daemon, (2, 2000))

    def test_a_401_reads_daemon_json_again(self):
        self.write_json(self.fake.port, token="old")
        self.kit.info()                                  # holds the old token
        self.write_json(self.fake.port)                  # the daemon restarted with another
        self.assertEqual(self.kit.call("x")["name"], "x")
        self.assertEqual([a for _, _, a, _ in self.fake.requests], ["Bearer old", f"Bearer {TOKEN}"])

    def test_a_401_with_the_same_token_is_an_error(self):
        self.write_json(self.fake.port, token="old")
        with self.assertRaises(wk.WuxianError) as e:
            self.kit.call("x")
        self.assertEqual((e.exception.code, e.exception.status), ("unauthorized", 401))
        self.assertEqual(len(self.fake.requests), 1)

    def test_errors(self):
        with self.assertRaises(wk.WuxianError) as e:
            self.kit.call("boom")
        self.assertEqual((e.exception.reason, e.exception.code, e.exception.status), ("bad args", "call_failed", 400))
        self.fake.link_down = True
        with self.assertRaises(wk.WuxianError) as e:
            self.kit.call("x")
        self.assertEqual((e.exception.code, e.exception.status), ("link_down", 409))
        self.assertIn("the game is not connected", e.exception.reason)


class Proposals(Base):
    def test_propose_sends_the_steps_and_options(self):
        p = self.kit.propose([("chat.send", {"channel": "guild", "text": "hi"}),
                              {"cap": "tune.cvar.set", "args": {"name": "a", "value": "1"}}],
                             title="Two", ttl=60, after=123)
        self.assertEqual((p["id"], p["state"]), (1, "pending"))
        body = self.fake.calls("kit.propose")[0]
        self.assertEqual(body["args"], {"title": "Two", "ttl": 60, "after": 123, "steps": [
            {"cap": "chat.send", "args": {"channel": "guild", "text": "hi"}},
            {"cap": "tune.cvar.set", "args": {"name": "a", "value": "1"}}]})
        self.kit.propose([("chat.send", {})])
        self.assertEqual(self.fake.calls("kit.propose")[1]["args"], {"steps": [{"cap": "chat.send", "args": {}}]})

    def test_wait_hears_kit_proposal(self):
        pid = self.kit.propose([("chat.send", {"channel": "guild", "text": "hi"})])["id"]

        def decide(body):                                 # the player clicks Run once wait() looked
            if self.fake.proposals[pid]["state"] == "pending":
                self.fake.proposals[pid]["state"] = "done"
                self.fake.emit("kit.proposal", {"id": pid + 5, "state": "declined", "source": "agent"})
                self.fake.emit("kit.proposal", {"id": pid, "state": "done", "source": "agent"})
                self.fake.proposals[pid]["state"] = "pending"   # the event comes before the game says so
                self.fake.on_call["kit.proposal"] = lambda b: self.fake.proposals[pid].update(state="done")
        self.fake.on_call["kit.proposal"] = decide
        p = self.kit.wait(pid)
        self.assertEqual(p["state"], "done")
        self.assertNotIn("timeout", p)
        self.assertEqual(self.fake.polls()[0]["topic"], "kit.proposal")

    def test_wait_asks_the_game_when_no_event_comes(self):
        pid = self.kit.propose([("chat.send", {})])["id"]
        n = {"asks": 0}

        def later(body):
            n["asks"] += 1
            if n["asks"] == 3:
                self.fake.proposals[pid]["state"] = "declined"   # and the event was lost
        self.fake.on_call["kit.proposal"] = later
        self.assertEqual(self.kit.wait(pid)["state"], "declined")

    def test_wait_times_out_and_unknown_proposals(self):
        pid = self.kit.propose([("chat.send", {})])["id"]
        t0 = self.clock.t
        p = self.kit.wait(pid, timeout=40)
        self.assertEqual((p["state"], p["timeout"]), ("pending", True))
        self.assertGreaterEqual(self.clock.t - t0, 40)
        with self.assertRaises(wk.WuxianError) as e:
            self.kit.wait(77)
        self.assertEqual(e.exception.code, "no_proposal")
        self.assertIsNone(self.kit.proposal(77))


class Events(Base):
    def test_a_list_of_topics_goes_as_a_list(self):
        self.fake.emit("kit.ready", {})
        self.fake.emit("chat.message", {})
        ans = self.kit.events(["app.demo", "kit.ready"], since=0, wait=0)
        self.assertEqual([e["topic"] for e in ans["events"]], ["kit.ready"])
        self.assertEqual(ans["next"], 3)
        self.assertEqual(self.fake.polls()[0]["topic"], ["app.demo", "kit.ready"])
        self.assertEqual(self.kit.topic_mode, "list")
        self.kit.events("kit.*", since=0, wait=0)
        self.assertEqual(self.fake.polls()[1]["topic"], "kit.*")       # one topic: as it is

    def test_cover_glob(self):
        self.assertEqual(wk.cover_glob(["app.x", "kit.app", "kit.ready"]), "[ak][ip][pt].[arx]*")
        self.assertEqual(wk.cover_glob(["kit.app", "kit.ready"]), "kit.[ar][ep][ap]*")
        self.assertEqual(wk.cover_glob(["gate.*", "guide.tour"]), "g[au][it][de][.e]*")
        self.assertEqual(wk.cover_glob(["*", "kit.ready"]), "*")
        self.assertEqual(wk.cover_glob(["kit.ready"]), "kit.ready")
        for t in ("app.x", "kit.app", "kit.ready"):
            self.assertTrue(fnmatch.fnmatchcase(t, wk.cover_glob(["app.x", "kit.app", "kit.ready"])))


class OldWorkshop(Base):
    lists = False

    def test_a_workshop_without_lists_gets_one_glob_and_the_client_filters(self):
        self.fake.emit("kit.feed", {})                   # the glob takes it too: dropped here
        self.fake.emit("kit.ready", {})
        self.fake.emit("app.demo", {"app": "demo", "items": []})
        ans = self.kit.events(["app.demo", "kit.app", "kit.ready"], since=0, wait=0)
        self.assertEqual([e["topic"] for e in ans["events"]], ["kit.ready", "app.demo"])
        self.kit.events(["app.demo", "kit.app", "kit.ready"], since=0, wait=0)
        self.assertEqual([p["topic"] for p in self.fake.polls()],
                         [["app.demo", "kit.app", "kit.ready"], "[ak][ip][pt].[adr][ep][amp]*", "[ak][ip][pt].[adr][ep][amp]*"])
        self.assertEqual(self.kit.topic_mode, "glob")

    def test_an_app_runs_on_it(self):
        s = self.stream()
        self.fake.wake([1])
        self.assertEqual([i["id"] for i in s.get(1)["items"]], [1])

    def test_a_new_daemon_is_asked_with_a_list_again(self):
        self.kit.events(["a.b", "c.d"], since=0, wait=0)
        self.assertEqual(self.kit.topic_mode, "glob")
        self.write_json(self.fake.port, pid=2)
        self.kit.forget()
        self.kit.info()
        self.assertIsNone(self.kit.topic_mode)


class Apps(Base):
    def test_open_sends_the_declaration(self):
        s = self.stream(me=True, controls=["mute", "less", "more"], prompt=None)
        body = self.fake.calls("kit.app.open")[0]
        self.assertEqual(body["args"], {"app": "demo", "title": "Demo", "hear": ["game:*"], "me": True,
                                        "controls": ["mute", "less", "more"]})
        self.assertEqual(s.state, ("connected", ""))
        self.assertEqual(s.opens, 1)
        self.assertEqual(s.status["lease"], 600)
        polls = self.fake.polls()
        self.assertEqual(polls[0]["since"], -1)          # the place is marked before the open
        self.assertEqual(polls[1]["topic"], ["app.demo", "kit.app", "kit.ready"])
        self.assertNotEqual(polls[1]["since"], -1)

    def test_wakes_in_order_and_repeats_dropped(self):
        s = self.stream()
        self.fake.wake([1, 2])
        self.fake.wake([2, 3], reason="batch")
        self.fake.wake([3])                              # nothing new: no wake
        self.fake.wake([], reason="idle")                # an idle wake has no items and still comes
        self.fake.emit("app.other", {"app": "other", "items": [{"id": 9}]})
        got = [s.get(1) for _ in range(3)]
        self.assertEqual([(w["reason"], [i["id"] for i in w["items"]]) for w in got],
                         [("urgent", [1, 2]), ("batch", [3]), ("idle", [])])
        self.assertEqual(got[0]["app"], "demo")
        self.assertIsNone(s.get(1))

    def test_only_the_last_1000_ids_are_kept(self):
        s = self.stream()
        self.fake.wake(range(1, 1002))
        self.assertEqual(len(s.get(1)["items"]), 1001)
        self.fake.wake([1, 1001])                        # 1 was forgotten, 1001 not
        self.assertEqual([i["id"] for i in s.get(1)["items"]], [1])

    def test_the_iterator(self):
        s = self.kit.app("demo", title="Demo", wait=10)
        self.fake.on_call["kit.app.open"] = lambda b: self.fake.wake([1])
        self.assertEqual([[i["id"] for i in w["items"]] for w in s], [[1]])     # then 10 s of nothing: it stops
        self.assertIsNone(s.ended)
        self.fake.wake([2])
        self.assertEqual([i["id"] for i in next(s)["items"]], [2])            # the stream goes on
        del self.fake.on_call["kit.app.open"]
        t = self.kit.app("demo", wait=10, yield_timeouts=True)
        self.assertIsNone(next(t))
        self.assertIsNone(t.ended)

    def test_the_heartbeat(self):
        self.fake.lease = 90                              # every 30 s (a third, at least 30)
        s = self.stream()
        t0 = self.fake.call_times("kit.app.open")[0]
        s.get(100)
        beats = [round(t - t0) for t in self.fake.call_times("kit.app.status")]
        self.assertEqual(beats, [30, 60, 90])
        self.assertEqual(self.fake.calls("kit.app.status")[0]["args"], {"app": "demo"})
        self.assertTrue(all(p["wait"] <= 25 for p in self.fake.polls()))

    def test_the_heartbeat_of_a_long_lease(self):
        s = self.stream()                                 # lease 600: every 200 s
        t0 = self.fake.call_times("kit.app.open")[0]
        s.get(450)
        self.assertEqual([round(t - t0) for t in self.fake.call_times("kit.app.status")], [200, 400])

    def test_kit_ready_opens_it_again(self):
        s = self.stream()
        self.fake.emit("kit.ready", {"version": "0.6.0"})
        self.fake.wake([5])
        self.assertEqual([i["id"] for i in s.get(1)["items"]], [5])
        self.assertIsNone(s.get(1))
        self.assertEqual(s.opens, 2)
        self.assertEqual(len(self.fake.calls("kit.app.open")), 2)

    def test_replaced_does_not_end_it(self):
        s = self.stream()
        self.fake.emit("kit.app", {"app": "demo", "on": False, "why": "replaced"})
        self.fake.emit("kit.app", {"app": "demo", "on": True, "ends": 123})
        self.fake.wake([1])
        self.assertEqual([i["id"] for i in s.get(1)["items"]], [1])
        self.assertIsNone(s.ended)

    def test_it_ends_when_the_app_stops(self):
        s = self.stream()
        self.fake.wake([1])
        self.fake.emit("kit.app", {"app": "other", "on": False, "why": "player"})    # another app: nothing
        self.fake.emit("kit.app", {"app": "demo", "on": False, "why": "player"})
        self.fake.wake([2])
        self.assertEqual([[i["id"] for i in w["items"]] for w in s], [[1]])          # the wake before still comes
        self.assertEqual(s.ended, "player")
        self.assertIsNone(s.get(1))
        with self.assertRaises(StopIteration):
            next(s)

    def test_a_heartbeat_that_finds_it_stopped_ends_it(self):
        self.fake.lease = 60
        s = self.stream()
        self.fake.app_on = False
        self.assertIsNone(s.get(100))
        self.assertEqual(s.ended, "stopped")

    def test_close(self):
        s = self.stream()
        s.close(stop=True)
        self.assertEqual(self.fake.calls("kit.app.stop")[0]["args"], {"app": "demo"})
        self.assertEqual(s.ended, "closed")
        self.assertEqual(list(s), [])
        t = self.stream()
        with t:
            pass
        self.assertEqual(t.ended, "closed")
        self.assertEqual(len(self.fake.calls("kit.app.stop")), 1)     # close() alone leaves the app running

    def test_link_down_waits_and_opens_once_it_is_back(self):
        self.fake.link_down = True
        s = self.kit.app("demo", title="Demo")
        self.assertIsNone(s.get(20))                      # never raises
        self.assertEqual(s.state[0], "waiting")
        self.assertIn("the game is not connected", s.state[1])
        self.assertEqual(self.clock.slept[:3], [1, 2, 5])
        self.fake.link_down = False
        self.fake.on_call["kit.app.open"] = lambda b: self.fake.wake([1])
        self.assertEqual([i["id"] for i in s.get(30)["items"]], [1])
        self.assertEqual(s.state, ("connected", ""))
        self.assertEqual(s.opens, 1)

    def test_backoff_while_the_daemon_is_away(self):
        with refuse_port(self.fake.port):
            s = self.kit.app("demo", title="Demo")
            self.assertIsNone(s.get(30))
        self.assertEqual(self.clock.slept, [1, 2, 5, 10, 10, 2])
        self.assertEqual(s.state[0], "waiting")
        self.assertIn("not running", s.state[1])
        self.assertEqual(self.fake.requests, [])
        self.fake.on_call["kit.app.open"] = lambda b: self.fake.wake([1])
        self.assertEqual([i["id"] for i in s.get(30)["items"]], [1])
        self.assertEqual(s.state, ("connected", ""))

    def test_no_daemon_json_backs_off_too(self):
        self.json.unlink()
        s = self.kit.app("demo")
        self.assertIsNone(s.get(20))
        self.assertEqual(self.clock.slept[:4], [1, 2, 5, 10])
        self.assertIn("daemon.json", s.state[1])

    def test_a_daemon_restart_starts_from_its_next_event_and_opens_again(self):
        s = self.stream()
        self.fake.wake([1])
        self.assertEqual([i["id"] for i in s.get(1)["items"]], [1])
        new = self.daemon()                              # another daemon, its event ids from 1 again
        for _ in range(5):
            new.emit("kit.feed", {})                     # older than the stream: not given
        new.wake([99])
        with refuse_port(self.fake.port):
            self.write_json(new.port, pid=2)
            self.assertIsNone(s.get(5))
            new.wake([1, 2])
            self.assertEqual([i["id"] for i in s.get(5)["items"]], [2])        # 1 was given already
        self.assertEqual(len(new.calls("kit.app.open")), 1)
        self.assertEqual(new.polls()[0]["since"], -1)
        self.assertEqual(s.opens, 2)

    def test_a_restart_found_by_the_token(self):
        s = self.stream()
        new = self.daemon()
        self.write_json(new.port, token=TOKEN, pid=2)
        self.kit._info = dict(self.kit._info, token="stale")  # the old one's token: 401, read again, a new daemon
        new.on_call["kit.app.open"] = lambda b: new.wake([7])
        self.assertIsNone(s.get(1))
        self.assertEqual([i["id"] for i in s.get(1)["items"]], [7])
        self.assertEqual(len(new.calls("kit.app.open")), 1)


if __name__ == "__main__":
    unittest.main()
