"""The notes on an app's wakes (daemon/notes.py): a wake that local extensions annotate goes into the journal with their
notes; a timeout or a failure is a note's error and the wake still goes in; one app's wakes keep their order, other apps
do not wait; an app nobody annotates is journaled at once; a slow start is waited for its timeout only and goes on; the
held wakes go in when the daemon stops. Notes alone (a loop on a thread, the call stubbed), then over a real daemon
(Service.on_debug, local_call to tests/local/fixture_ext.py, /api/addon_events)."""
import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame
from tests.local.util import make_extension, until
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server as daemon
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.daemon.notes import Notes, wake_of


def wake_text(app, n, **extra):
    return json.dumps({"a": "WuxianKit", "t": f"app.{app}",
                       "d": {"app": app, "reason": "heard", "items": [{"n": n}], "skipped": 0, **extra}})


class Loop:
    """an asyncio loop on its own thread, as the server's"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def soon(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def close(self):
        async def cancel():                   # the calls a test left running
            for task in asyncio.all_tasks() - {asyncio.current_task()}:
                task.cancel()
        asyncio.run_coroutine_threadsafe(cancel(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


class NotesAlone(unittest.TestCase):
    def setUp(self):
        self.loop = Loop()
        self.addCleanup(self.loop.close)
        self.anns = {}                        # app -> annotations
        self.answers = {}                     # cap -> async fn(args) -> result, or raises
        self.calls, self.delivered, self.lines = [], [], []
        self.state = {}
        self.notes = Notes(lambda app: self.anns.get(app, []), self.call, self.deliver, self.loop.soon,
                           state=lambda ext: self.state.get(ext), note=self.lines.append)

    async def call(self, cap, args, timeout_ms, caller):
        self.calls.append((cap, args, timeout_ms, caller))
        return {"result": await self.answers[cap](args)}

    def deliver(self, text):
        self.delivered.append((time.monotonic(), json.loads(text)))

    def got(self, n, seconds=10):
        self.assertTrue(until(lambda: len(self.delivered) >= n, seconds), self.delivered)
        return [d for _, d in self.delivered]

    def test_which_events_are_wakes(self):
        self.assertEqual(wake_of(wake_text("guild", 1))[0], "guild")
        self.assertIsNone(wake_of(wake_text("reactions", 1)))           # the workshop's own temporary app
        self.assertIsNone(wake_of(json.dumps({"a": "Other", "t": "app.guild", "d": {}})))
        self.assertIsNone(wake_of(json.dumps({"a": "WuxianKit", "t": "kit.app", "d": {"app": "guild"}})))
        self.assertIsNone(wake_of(json.dumps({"a": "WuxianKit", "t": "app.guild", "d": [1]})))
        self.assertIsNone(wake_of('{"a": "WuxianKit", "t": "app.guild", "d": {"x": NaN}}'))
        self.assertIsNone(wake_of("plain text app. WuxianKit"))

    def test_notes_attached(self):
        async def lookup(args):
            return {"facts": [it["n"] for it in args["w"]["items"]]}

        async def other(args):
            return "second"
        self.anns["guild"] = [{"ext": "facts", "call": "facts.lookup", "wake": "w", "timeout": 3},
                              {"ext": "more", "call": "more.see", "timeout": 3}]
        self.answers.update({"facts.lookup": lookup, "more.see": other})
        self.assertTrue(self.notes.hold(wake_text("guild", 7)))
        (data,) = [d["d"] for d in self.got(1)]
        self.assertEqual(data["notes"], {"facts": {"facts": [7]}, "more": "second"})
        self.assertEqual((data["app"], data["items"]), ("guild", [{"n": 7}]))
        cap, args, timeout_ms, caller = self.calls[0]
        self.assertEqual((cap, timeout_ms, caller), ("facts.lookup", 3000, {"by": "workshop", "annotate": "guild"}))
        self.assertNotIn("notes", args["w"])                             # the wake as it came
        self.assertEqual(self.calls[1][1].keys(), {"wake"})              # the default argument name
        self.assertEqual(self.lines, [])

    def test_timeout_is_an_error_and_the_wake_still_comes(self):
        async def slow(args):
            await asyncio.sleep(5)
        self.anns["guild"] = [{"ext": "facts", "call": "facts.lookup", "wake": "wake", "timeout": 0.3}]
        self.answers["facts.lookup"] = slow
        t0 = time.monotonic()
        self.assertTrue(self.notes.hold(wake_text("guild", 1)))
        (event,) = self.got(1)
        self.assertLess(self.delivered[0][0] - t0, 2.0)
        self.assertEqual(event["d"]["notes"]["facts"]["reason"], "timeout")
        self.assertIn("within 0.3 s", event["d"]["notes"]["facts"]["error"])

    def test_failures_are_errors_said_once(self):
        async def boom(args):
            raise ApiError(422, "failed", "facts.lookup: it went boom\nTraceback ...")
        self.anns["guild"] = [{"ext": "facts", "call": "facts.lookup", "timeout": 1}]
        self.answers["facts.lookup"] = boom
        for n in range(3):
            self.assertTrue(self.notes.hold(wake_text("guild", n)))
        events = self.got(3)
        self.assertEqual([e["d"]["notes"]["facts"] for e in events],
                         [{"error": "facts.lookup: it went boom", "reason": "failed"}] * 3)
        self.assertEqual(len(self.lines), 1, self.lines)                 # the same reason in a row: said once

        async def down(args):
            raise ApiError(409, "off", "facts is off")
        self.answers["facts.lookup"] = down
        self.assertTrue(self.notes.hold(wake_text("guild", 9)))
        self.assertEqual(self.got(4)[3]["d"]["notes"]["facts"]["reason"], "down")
        self.assertEqual(len(self.lines), 2)

        async def bad(args):
            return "\ud800"                   # no JSON text: the journal could not read the wake
        self.answers["facts.lookup"] = bad
        self.assertTrue(self.notes.hold(wake_text("guild", 10)))
        self.assertEqual(self.got(5)[4]["d"]["notes"]["facts"]["reason"], "failed")

    def test_one_apps_wakes_keep_their_order_and_others_do_not_wait(self):
        async def lookup(args):
            n = args["wake"]["items"][0]["n"]
            await asyncio.sleep(0.6 if n == 1 else 0.01)
            return n

        async def quick(args):
            return "q"
        self.anns["guild"] = [{"ext": "facts", "call": "facts.lookup", "timeout": 3}]
        self.anns["party"] = [{"ext": "quick", "call": "quick.see", "timeout": 3}]
        self.answers.update({"facts.lookup": lookup, "quick.see": quick})
        for n in (1, 2, 3):
            self.assertTrue(self.notes.hold(wake_text("guild", n)))
        self.assertTrue(self.notes.hold(wake_text("party", 4)))
        events = self.got(4)
        self.assertEqual(events[0]["t"], "app.party")                   # it did not wait for guild's slow first
        guild = [e["d"]["notes"]["facts"] for e in events if e["t"] == "app.guild"]
        self.assertEqual(guild, [1, 2, 3])
        self.assertEqual(self.notes.queues, {})

    def test_no_annotations_no_delay(self):
        self.anns["guild"] = []
        t0 = time.perf_counter()
        self.assertFalse(self.notes.hold(wake_text("guild", 1)))         # the caller journals it now
        self.assertFalse(self.notes.hold(wake_text("other", 1)))
        self.assertFalse(self.notes.hold(json.dumps({"a": "WoWBridge", "t": "x", "d": 1})))
        self.assertLess(time.perf_counter() - t0, 0.05)
        self.assertEqual((self.calls, self.delivered), ([], []))

    def test_a_slow_start_is_waited_for_its_timeout_only(self):
        finished = threading.Event()

        async def starting(args):             # local_call while the extension starts (initialize takes its time)
            await asyncio.sleep(1.0)
            finished.set()
            return "late"
        self.anns["guild"] = [{"ext": "facts", "call": "facts.lookup", "timeout": 0.3}]
        self.answers["facts.lookup"] = starting
        self.state["facts"] = "starting"
        t0 = time.monotonic()
        self.assertTrue(self.notes.hold(wake_text("guild", 1)))
        (event,) = self.got(1)
        self.assertLess(self.delivered[0][0] - t0, 0.9)
        self.assertEqual(event["d"]["notes"]["facts"]["reason"], "starting")
        self.assertTrue(finished.wait(5))     # the start (the call) was not cancelled

    def test_close_delivers_the_held_wakes(self):
        async def never(args):
            await asyncio.sleep(30)
        self.anns["guild"] = [{"ext": "facts", "call": "facts.lookup", "timeout": 10}]
        self.answers["facts.lookup"] = never
        self.assertTrue(self.notes.hold(wake_text("guild", 1)))
        self.assertTrue(self.notes.hold(wake_text("guild", 2)))
        self.notes.close()
        events = [d for _, d in self.delivered]
        self.assertEqual([e["d"]["items"][0]["n"] for e in events], [1, 2])
        self.assertEqual({e["d"]["notes"]["facts"]["reason"] for e in events}, {"down"})
        self.assertFalse(self.notes.hold(wake_text("guild", 3)))         # later ones: journaled at once
        time.sleep(0.1)
        self.assertEqual(len(self.delivered), 2)                         # the runner did not deliver them again

    def test_no_loop_no_hold(self):
        notes = Notes(lambda app: [{"ext": "facts", "call": "facts.lookup"}], self.call, self.deliver,
                      lambda coro: coro.close())
        self.assertFalse(notes.hold(wake_text("guild", 1)))
        self.assertEqual(notes.queues, {})


class NotesDaemon(unittest.TestCase):
    """the notes over a real daemon: WuxianKit's wake through Service.on_debug, a real local extension's capability"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name).resolve()
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(cls.root / "home")})
        cls.env.start()
        addons = cls.root / "client" / "Interface" / "AddOns"
        (addons / "WoWBridge").mkdir(parents=True)
        MB.install(addons, pool=16)
        cls.handle = daemon.start(worker_factory=lambda on_debug, log: FakeGame(addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "N")
        cls.svc = cls.handle.service

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def tearDown(self):
        for row in self.svc.local.rows():
            self.ok("POST", "/api/local/remove", {"id": row["id"]})

    def ok(self, method, path, body=None):
        req = urllib.request.Request(self.handle.url.rstrip("/") + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.handle.token}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.assertEqual(resp.status, 200)
            return json.loads(resp.read())

    def wakes(self, app):
        res = self.ok("POST", "/api/addon_events", {"addon": "WuxianKit", "topic": f"app.{app}", "since": 0})
        return [e["data"] for e in res["events"]]

    def annotate(self, mapping):
        return mock.patch.object(self.svc.local, "annotations", create=True,
                                 side_effect=lambda app: mapping.get(app, []))

    def emit(self, app, n):
        self.svc.on_debug("EVENT", wake_text(app, n))

    def test_the_host_without_annotations(self):
        """a host before Host.annotations: nothing is held"""
        with mock.patch.object(self.svc, "local", mock.Mock(spec=["get"])):
            self.emit("plain", 1)
        self.assertEqual([w["items"] for w in self.wakes("plain")], [[{"n": 1}]])
        self.assertNotIn("notes", self.wakes("plain")[0])

    def test_notes_from_a_local_extension(self):
        self.ok("POST", "/api/local/add", {"path": str(make_extension(self.root / "ext" / "fx", "fx"))})
        with self.annotate({"guild": [{"ext": "fx", "call": "fx.echo", "wake": "text", "timeout": 10}]}):
            self.emit("guild", 1)
            self.assertTrue(until(lambda: self.wakes("guild"), 20))
        (wake,) = self.wakes("guild")
        note = wake["notes"]["fx"]
        self.assertEqual(note["caller"], {"by": "workshop", "annotate": "guild"})
        self.assertEqual(note["text"]["items"], [{"n": 1}])
        self.assertNotIn("notes", note["text"])

    def test_a_slow_start_and_a_failure(self):
        self.ok("POST", "/api/local/add", {"path": str(make_extension(self.root / "ext" / "fy", "fy"))})
        original = self.svc.local.initialize

        def slow(*args, **kw):
            time.sleep(2.0)
            return original(*args, **kw)
        with self.annotate({"slow": [{"ext": "fy", "call": "fy.echo", "wake": "text", "timeout": 0.5}],
                            "boom": [{"ext": "fy", "call": "fy.boom", "timeout": 3}]}), \
                mock.patch.object(self.svc.local, "initialize", side_effect=slow):
            t0 = time.monotonic()
            self.emit("slow", 1)
            self.assertTrue(until(lambda: self.wakes("slow"), 10))
            self.assertLess(time.monotonic() - t0, 1.8)                  # the wake did not wait for the start
            self.assertEqual(self.wakes("slow")[0]["notes"]["fy"]["reason"], "starting")
            self.assertTrue(until(lambda: self.svc.local.get("fy")["state"] == "running", 20))   # the start went on
            self.emit("slow", 2)
            self.assertTrue(until(lambda: len(self.wakes("slow")) == 2, 10))
            self.assertEqual(self.wakes("slow")[1]["notes"]["fy"]["caller"]["annotate"], "slow")
            self.emit("boom", 3)              # an error of the capability: the note's
            self.assertTrue(until(lambda: self.wakes("boom"), 10))
        self.assertEqual(self.wakes("boom")[0]["notes"]["fy"], {"error": "fy.boom: it went boom", "reason": "failed"})
        self.assertEqual(self.wakes("boom")[0]["items"], [{"n": 3}])


if __name__ == "__main__":
    unittest.main()
