"""The Python SDK (sdk/python/wuxiankit.py) against a fake 无限工坊 daemon (http.server in a thread): finding the daemon,
the token, backoff, calls, proposals and wait, events with a list of topics (and a workshop that takes one topic
only), Client.open, and an app's stream: it joins an app that runs (never opens one), the cursor (WuxianKit's item
ids), catching up, wakes without what was given, heartbeat, kit.ready, a daemon restart, the end.
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
        self.app_on = True                    # the app demo runs
        self.app_why = None                   # why it does not
        self.app_known = True                 # it is declared at all
        self.app_blocked = None               # kit.app.open: the player blocked it (the words)
        self.app_items = []                   # what the app heard, oldest first
        self.app_next = 0                     # the id of the newest of them
        self.app_sent = 0                     # where the app's wakes got: the next of its last one
        self.old_kit = False                  # a WuxianKit whose status has no sent
        self.seq = 0
        self.fail = {}                        # name -> how many of its next calls fail (link_down)
        self.truncated = False                # the next poll says events were dropped
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

    def hear(self, *ids):
        """the app hears items (kit.app.recent has them) and no wake about them reaches the stream"""
        for i in ids:
            if all(it["id"] != i for it in self.app_items):
                self.app_items.append({"id": i, "time": 1, "source": "game", "kind": "rest"})
        self.app_items.sort(key=lambda it: it["id"])
        self.app_next = max([self.app_next, *ids])

    def wake(self, ids, reason="urgent", app="demo", after=None, lost=False, **more):
        """the app sends a wake of the items `ids` (heard now, when they were not before): it covers
        after < id <= next. lost=True: it does not reach the stream"""
        ids = list(ids)
        if after is None:
            after = min(ids) - 1 if ids else self.app_sent
        self.hear(*ids)
        self.seq += 1
        nxt = max(ids) if ids else self.app_sent
        self.app_sent = max(self.app_sent, nxt)
        if not lost:
            self.emit(f"app.{app}", dict({"app": app, "seq": self.seq, "reason": reason, "skipped": 0, "after": after,
                                          "next": nxt, "items": [it for it in self.app_items if it["id"] in ids]},
                                         **more))

    def status(self, app):
        out = {"app": app, "title": app, "on": self.app_on, "ends": 99999, "lease": self.lease, "seen": 1,
               "next": self.app_next}
        if not self.old_kit:
            out["sent"] = self.app_sent
        if not self.app_on and self.app_why:
            out["why"] = self.app_why
        return out

    def call(self, body):
        name, args = body.get("name"), body.get("args") or {}
        if name in self.on_call:
            self.on_call[name](body)
        if self.link_down:
            return 409, {"error": {"code": "link_down", "message": "call: no frames come from the game"}}
        if self.fail.get(name):
            self.fail[name] -= 1
            return 409, {"error": {"code": "link_down", "message": "call: no frames come from the game"}}
        if name in ("kit.app.open", "kit.app.status", "kit.app.recent") and not self.app_known:
            return 400, {"error": {"code": "call_failed", "message": f"no app {args.get('app')}"}}
        if name == "kit.app.open":
            if self.app_blocked:
                return 200, {"ok": True, "result": {"app": args["app"], "on": False, "blocked": self.app_blocked}}
            self.app_on = True
            return 200, {"addon": "WuxianKit", "name": name, "ok": True, "result": self.status(args["app"])}
        if name == "kit.app.status":
            return 200, {"ok": True, "result": self.status(args["app"])}
        if name == "kit.app.recent":
            items = [it for it in self.app_items if it["id"] > args.get("since", -1)][-args.get("limit", 50):]
            return 200, {"ok": True, "result": {"items": items, "next": self.app_next}}
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
        truncated, self.truncated = self.truncated, False
        return 200, {"events": hits, "next": nxt, "truncated": truncated, "session": 7}

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

    def stream(self, **how):
        s = self.kit.app("demo", **how)
        self.assertIsNone(s.get(1))                     # joins it and finds nothing yet
        return s

    def ids(self, wake):
        return [i["id"] for i in wake["items"]]


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
        self.assertEqual(self.ids(s.get(1)), [1])

    def test_a_new_daemon_is_asked_with_a_list_again(self):
        self.kit.events(["a.b", "c.d"], since=0, wait=0)
        self.assertEqual(self.kit.topic_mode, "glob")
        self.write_json(self.fake.port, pid=2)
        self.kit.forget()
        self.kit.info()
        self.assertIsNone(self.kit.topic_mode)


class Open(Base):
    def test_open_starts_a_declared_app(self):
        self.fake.app_on = False
        st = self.kit.open("demo")
        self.assertEqual((st["on"], st["lease"]), (True, 600))
        self.kit.open("demo", minutes=120, lease=900)
        self.assertEqual([b["args"] for b in self.fake.calls("kit.app.open")],
                         [{"app": "demo"}, {"app": "demo", "minutes": 120, "lease": 900}])

    def test_one_that_cannot_start(self):
        self.fake.app_blocked = "the player turned demo off"
        with self.assertRaises(wk.WuxianError) as cm:
            self.kit.open("demo")
        self.assertEqual((cm.exception.code, cm.exception.reason), ("not_started", "the player turned demo off"))
        self.fake.app_known = False
        with self.assertRaises(wk.WuxianError) as cm:
            self.kit.open("demo")
        self.assertEqual(cm.exception.code, "call_failed")


class Apps(Base):
    def test_it_joins_and_never_opens(self):
        self.fake.wake(range(1, 41), lost=True)          # the app's wakes got to 40 before
        self.fake.hear(41)                               # heard since, still gathered: its next wake brings it
        s = self.stream()
        self.assertEqual(s.state, ("connected", ""))
        self.assertEqual((s.cursor, s.status["lease"]), (40, 600))      # from where its last wake ended
        polls = self.fake.polls()
        self.assertEqual(polls[0]["since"], -1)          # the place is marked before the status is read
        self.assertEqual(polls[1]["topic"], ["app.demo", "kit.app", "kit.ready"])
        self.assertNotEqual(polls[1]["since"], -1)
        self.fake.wake([41, 42])
        self.assertEqual(self.ids(s.get(1)), [41, 42])
        self.assertEqual(s.cursor, 42)
        self.assertEqual([b["name"] for b in self.fake.calls()], ["kit.app.status"])     # no open, declare or recent
        self.assertEqual(self.fake.calls()[0]["args"], {"app": "demo"})

    def test_a_kit_without_sent(self):
        self.fake.old_kit = True
        self.fake.hear(1, 2, 3)
        s = self.stream()
        self.assertEqual(s.cursor, 3)                    # from its newest item on
        self.fake.wake([4])
        self.assertEqual(self.ids(s.get(1)), [4])
        t = self.kit.app("demo", since=1)                # a catch-up reads all it heard
        w = t.get(1)
        self.assertEqual((w["reason"], self.ids(w), w["after"], w["next"]), ("catchup", [2, 3, 4], 1, 4))

    def test_an_app_that_does_not_run_ends_it_at_once(self):
        self.fake.app_on = False
        for why, ended in (("expired", "expired"), (None, "stopped")):
            self.fake.app_why = why
            s = self.kit.app("demo")
            self.assertIsNone(s.get(100))
            self.assertEqual(s.ended, ended)
            self.assertEqual(list(s), [])
        self.fake.app_known = False                      # no app of this id at all
        s = self.kit.app("demo")
        self.assertIsNone(s.get(100))
        self.assertEqual(s.ended, "stopped")
        self.assertEqual(len(self.fake.calls("kit.app.status")), 3)     # asked once each: no retry
        self.assertEqual(self.fake.calls("kit.app.open"), [])
        self.assertEqual(self.clock.slept, [])

    def test_wakes_in_order_and_repeats_dropped(self):
        s = self.stream()
        self.fake.wake([1, 2])
        self.fake.wake([2, 3], reason="batch")           # 2 was given
        self.fake.wake([3])                              # nothing new: no wake
        self.fake.wake([], reason="idle")                # an idle wake has no items and still comes
        self.fake.emit("app.other", {"app": "other", "items": [{"id": 9}]})
        got = [s.get(1) for _ in range(3)]
        self.assertEqual([(w["reason"], self.ids(w)) for w in got], [("urgent", [1, 2]), ("batch", [3]), ("idle", [])])
        self.assertEqual((got[0]["app"], got[0]["after"], got[0]["next"], got[1]["next"]), ("demo", 0, 2, 3))
        self.assertIsNone(s.get(1))
        self.assertEqual(s.cursor, 3)

    def test_ids_as_the_game_writes_them(self):
        s = self.stream()
        self.fake.emit("app.demo", {"app": "demo", "reason": "item", "skipped": 0, "after": 0.0, "next": 2.0,
                                    "items": [{"id": 1.0}, {"id": 2.0}]})
        self.fake.emit("app.demo", {"app": "demo", "reason": "full", "skipped": 3, "after": 2.0, "next": 9.0,
                                    "items": [{"id": 9.0}]})
        self.fake.emit("app.demo", {"app": "demo", "reason": "old kit", "items": [{"id": 1}]})    # no cursor: as it is
        self.assertEqual([(w["reason"], self.ids(w)) for w in (s.get(1), s.get(1), s.get(1))],
                         [("item", [1, 2]), ("full", [9]), ("old kit", [1])])
        self.assertEqual(s.cursor, 9)

    def test_since_catches_up_first(self):
        self.fake.wake([1, 2, 3, 4, 5], lost=True)
        self.fake.hear(6)                                # still gathered: not read ahead of the app's wake
        s = self.kit.app("demo", since=2)
        self.assertEqual(s.cursor, 2)
        self.assertEqual(s.get(1), {"app": "demo", "reason": "catchup", "items": self.fake.app_items[2:5],
                                    "skipped": 0, "after": 2, "next": 5})
        self.assertEqual(self.fake.calls("kit.app.recent")[0]["args"], {"app": "demo", "since": 2, "limit": 100})
        self.assertEqual(s.cursor, 5)
        self.fake.wake([6])
        self.assertEqual(self.ids(s.get(1)), [6])
        self.assertIsNone(s.get(1))
        self.assertEqual(len(self.fake.calls("kit.app.recent")), 1)
        t = self.kit.app("demo", since=6)                # the app's wakes got no further: nothing is asked
        self.assertIsNone(t.get(1))
        self.assertEqual((t.cursor, t.ended), (6, None))
        self.assertEqual(len(self.fake.calls("kit.app.recent")), 1)

    def test_since_and_what_the_app_keeps_no_more(self):
        self.fake.wake([1, 2, 3, 4, 5], lost=True)
        self.fake.app_items = []                         # its ring let go of them
        s = self.kit.app("demo", since=2)
        self.assertIsNone(s.get(1))
        self.assertEqual(s.cursor, 5)                    # its wakes go on from there
        self.fake.wake([6])
        self.assertEqual((self.ids(s.get(1)), len(self.fake.calls("kit.app.recent"))), ([6], 1))
        t = self.kit.app("demo", since=99)               # past its newest item (another character): from that one on
        self.assertIsNone(t.get(1))
        self.assertEqual(t.cursor, 6)
        self.fake.wake([7])
        self.assertEqual(self.ids(t.get(1)), [7])

    def test_a_gap_is_caught_up_before_the_wake(self):
        s = self.stream()
        self.fake.wake([1])
        self.assertEqual(self.ids(s.get(1)), [1])
        self.fake.wake([2, 3], lost=True)                # this one never came
        self.fake.wake([4, 5], reason="batch", state={"mood": "calm"}, pace="low", notes={"facts": {"n": 1}})
        self.fake.hear(6)
        w = s.get(1)
        self.assertEqual(w, {"app": "demo", "reason": "catchup", "items": self.fake.app_items[1:3], "skipped": 0,
                             "after": 1, "next": 3})
        w = s.get(1)                                     # the wake itself, as it is
        self.assertEqual((w["reason"], self.ids(w), w["after"], w["next"]), ("batch", [4, 5], 3, 5))
        self.assertEqual((w["state"], w["pace"], w["notes"]), ({"mood": "calm"}, "low", {"facts": {"n": 1}}))
        self.assertIsNone(s.get(1))
        self.assertEqual([b["args"] for b in self.fake.calls("kit.app.recent")],
                         [{"app": "demo", "since": 1, "limit": 100}])
        self.fake.wake([6])
        self.assertEqual(self.ids(s.get(1)), [6])

    def test_a_catch_up_that_fails_keeps_the_wakes(self):
        s = self.stream()
        self.fake.wake([1])
        self.assertEqual(self.ids(s.get(1)), [1])
        self.fake.wake([2, 3], lost=True)
        self.fake.fail["kit.app.recent"] = 2
        self.fake.wake([4])
        self.fake.wake([5])
        self.assertIsNone(s.get(0.5))                    # never raises
        self.assertEqual(s.state[0], "waiting")
        got = [s.get(30), s.get(1), s.get(1)]
        self.assertEqual([(w["reason"], self.ids(w)) for w in got],
                         [("catchup", [2, 3]), ("urgent", [4]), ("urgent", [5])])       # nothing lost, nothing twice
        self.assertIsNone(s.get(1))
        self.assertEqual(s.state, ("connected", ""))
        self.assertEqual(len(self.fake.calls("kit.app.recent")), 3)
        self.assertEqual(self.clock.slept[:3], [0.5, 0.5, 2])

    def test_events_the_daemon_dropped(self):
        s = self.stream()
        self.fake.wake([1])
        self.assertEqual(self.ids(s.get(1)), [1])
        self.fake.wake([2, 3], lost=True)                # the daemon's journal let go of it before it was read
        self.fake.hear(4)
        self.fake.truncated = True
        self.fake.emit("kit.app", {"app": "other", "on": True})
        w = s.get(1)
        self.assertEqual((w["reason"], self.ids(w), w["after"], w["next"]), ("catchup", [2, 3], 1, 3))
        self.assertEqual(len(self.fake.calls("kit.app.status")), 2)     # where its wakes got is in the status
        self.fake.wake([4])
        self.assertEqual(self.ids(s.get(1)), [4])

    def test_the_iterator(self):
        s = self.kit.app("demo", wait=10)
        self.assertEqual(list(s), [])                    # 10 s of nothing: it stops
        self.assertIsNone(s.ended)
        self.fake.wake([1])
        self.assertEqual([self.ids(w) for w in s], [[1]])               # the stream goes on, then 10 s of nothing
        self.fake.wake([2])
        self.assertEqual(self.ids(next(s)), [2])
        t = self.kit.app("demo", wait=10, yield_timeouts=True)
        self.assertIsNone(next(t))
        self.assertIsNone(t.ended)

    def test_the_heartbeat(self):
        self.fake.lease = 90                              # every 30 s (a third, at least 30)
        s = self.stream()
        t0 = self.fake.call_times("kit.app.status")[0]
        s.get(100)
        beats = [round(t - t0) for t in self.fake.call_times("kit.app.status")]
        self.assertEqual(beats, [0, 30, 60, 90])
        self.assertEqual(self.fake.calls("kit.app.status")[1]["args"], {"app": "demo"})
        self.assertTrue(all(p["wait"] <= 25 for p in self.fake.polls()))

    def test_the_heartbeat_of_a_long_lease(self):
        s = self.stream()                                 # lease 600: every 200 s
        t0 = self.fake.call_times("kit.app.status")[0]
        s.get(450)
        self.assertEqual([round(t - t0) for t in self.fake.call_times("kit.app.status")], [0, 200, 400])

    def test_kit_ready_reads_the_status_again(self):
        s = self.stream()
        self.fake.emit("kit.ready", {"version": "0.8.0"})
        self.assertIsNone(s.get(1))
        self.assertEqual(len(self.fake.calls("kit.app.status")), 2)
        self.fake.wake([1])
        self.assertEqual(self.ids(s.get(1)), [1])
        self.fake.app_on, self.fake.app_why = False, "left"             # a login as another character
        self.fake.emit("kit.ready", {"version": "0.8.0"})
        self.assertIsNone(s.get(1))
        self.assertEqual(s.ended, "left")
        self.assertEqual(self.fake.calls("kit.app.open"), [])

    def test_items_that_start_again(self):
        s = self.stream()
        self.fake.wake([1, 2, 3])
        self.assertEqual(self.ids(s.get(1)), [1, 2, 3])
        self.fake.app_items, self.fake.app_next, self.fake.app_sent = [], 0, 0     # the saved data started again
        self.fake.emit("kit.ready", {"version": "0.8.0"})
        self.assertIsNone(s.get(1))
        self.assertEqual(s.cursor, 0)
        self.fake.wake([1])
        self.assertEqual(self.ids(s.get(1)), [1])

    def test_replaced_does_not_end_it(self):
        s = self.stream()
        self.fake.emit("kit.app", {"app": "demo", "on": False, "why": "replaced"})
        self.fake.emit("kit.app", {"app": "demo", "on": True, "ends": 123})
        self.fake.wake([1])
        self.assertEqual(self.ids(s.get(1)), [1])
        self.assertIsNone(s.get(1))
        self.assertIsNone(s.ended)
        self.assertEqual(len(self.fake.calls("kit.app.status")), 2)     # opened again: its lease may be another

    def test_it_ends_when_the_app_stops(self):
        s = self.stream()
        self.fake.wake([1])
        self.fake.emit("kit.app", {"app": "other", "on": False, "why": "player"})    # another app: nothing
        self.fake.emit("kit.app", {"app": "demo", "on": False, "why": "player"})
        self.fake.wake([2])
        self.assertEqual([self.ids(w) for w in s], [[1]])               # the wake before still comes
        self.assertEqual(s.ended, "player")
        self.assertIsNone(s.get(1))
        with self.assertRaises(StopIteration):
            next(s)
        t = self.stream()
        self.fake.emit("kit.app", {"app": "demo", "on": False, "why": "forgotten"})  # its extension was turned off
        self.assertIsNone(t.get(1))
        self.assertEqual(t.ended, "forgotten")

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
        self.fake.app_on = True
        t = self.stream()
        with t:
            pass
        self.assertEqual(t.ended, "closed")
        self.assertEqual(len(self.fake.calls("kit.app.stop")), 1)     # close() alone leaves the app running

    def test_link_down_waits_and_joins_once_it_is_back(self):
        self.fake.link_down = True
        s = self.kit.app("demo")
        self.assertIsNone(s.get(20))                      # never raises
        self.assertEqual(s.state[0], "waiting")
        self.assertIn("the game is not connected", s.state[1])
        self.assertEqual(self.clock.slept[:3], [1, 2, 5])
        self.assertIsNone(s.ended)
        self.fake.link_down = False
        self.assertIsNone(s.get(30))
        self.assertEqual(s.state, ("connected", ""))
        self.fake.wake([1])
        self.assertEqual(self.ids(s.get(1)), [1])
        self.assertEqual(self.fake.calls("kit.app.open"), [])

    def test_backoff_while_the_daemon_is_away(self):
        with refuse_port(self.fake.port):
            s = self.kit.app("demo")
            self.assertIsNone(s.get(30))
        self.assertEqual(self.clock.slept, [1, 2, 5, 10, 10, 2])
        self.assertEqual(s.state[0], "waiting")
        self.assertIn("not running", s.state[1])
        self.assertEqual(self.fake.requests, [])
        self.assertIsNone(s.get(30))
        self.assertEqual(s.state, ("connected", ""))
        self.fake.wake([1])
        self.assertEqual(self.ids(s.get(1)), [1])

    def test_no_daemon_json_backs_off_too(self):
        self.json.unlink()
        s = self.kit.app("demo")
        self.assertIsNone(s.get(20))
        self.assertEqual(self.clock.slept[:4], [1, 2, 5, 10])
        self.assertIn("daemon.json", s.state[1])

    def test_a_daemon_restart_catches_up(self):
        s = self.stream()
        self.fake.wake([1])
        self.assertEqual(self.ids(s.get(1)), [1])
        new = self.daemon()                              # another daemon, its event ids from 1 again; the same game
        new.wake([1], lost=True)
        for _ in range(5):
            new.emit("kit.feed", {})                     # older than the stream: not given
        new.wake([2, 3], lost=True)                      # the app's wakes while no daemon was there
        new.hear(4)
        with refuse_port(self.fake.port):
            self.write_json(new.port, pid=2)
            w = s.get(5)
            self.assertEqual((w["reason"], self.ids(w), w["after"], w["next"]), ("catchup", [2, 3], 1, 3))
            self.assertIsNone(s.get(5))
            new.wake([3, 4])
            self.assertEqual(self.ids(s.get(5)), [4])    # 3 was given already
        self.assertEqual(new.polls()[0]["since"], -1)    # its events from the next one on
        self.assertEqual([b["name"] for b in new.calls()], ["kit.app.status", "kit.app.recent"])     # it is not opened
        self.assertEqual(new.calls("kit.app.recent")[0]["args"], {"app": "demo", "since": 1, "limit": 100})

    def test_a_restart_found_by_the_token(self):
        s = self.stream()
        new = self.daemon()
        new.wake([1, 2], lost=True)
        new.emit("kit.app", {"app": "other", "on": True})
        self.write_json(new.port, token=TOKEN, pid=2)
        self.kit._info = dict(self.kit._info, token="stale")  # the old one's token: 401, read again, a new daemon
        self.assertEqual(self.ids(s.get(1)), [1, 2])
        self.assertEqual(new.calls("kit.app.open"), [])
        self.assertEqual(len(new.calls("kit.app.status")), 1)


if __name__ == "__main__":
    unittest.main()
