"""The lanes of the apps' wakes (daemon/notes.py): a wake of an app that local extensions add to goes into the journal
with their notes; a timeout or a failure is a note's error and the wake still goes in; one app's wakes and its kit.app
events keep their order, other apps do not wait; a lane that is full lets its oldest wake go without notes (busy); an
extension that keeps failing is paused for that app and probed later; what a control entry is bound to is called before
the wake goes on; an app nobody adds to is journaled at once; a slow start is waited for its timeout only and goes on;
the held wakes go in when the daemon stops. Notes alone (a loop on a thread, the call stubbed), then over a real daemon
(Service.on_debug, local_call to tests/local/fixture_ext.py, /api/addon_events; Host.adds and Host.control_call
stubbed: the manifests are another package's)."""
import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame
from tests.local.util import make_extension, until
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server as daemon
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.daemon.notes import LANE_MAX, PAUSE, Notes, event_of


def wake_text(app, n, **extra):
    return json.dumps({"a": "WuxianKit", "t": f"app.{app}",
                       "d": {"app": app, "reason": "heard", "items": [{"n": n}], "skipped": 0, **extra}})


def app_text(app, on):
    return json.dumps({"a": "WuxianKit", "t": "kit.app", "d": {"app": app, "on": on}})


def add(ext, call, timeout=3):
    """what an extension adds to an app, as Host.adds gives it"""
    return {"ext": ext, "wake": {"call": call, "timeout": timeout}}


def pressed(cmd, n=1):
    """a wake's items with a control entry (the player pressed the app's control cmd) before the item n"""
    return [{"id": 5, "time": 9, "source": "control", "cmd": cmd}, {"n": n}]


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
        self.adds = {}                        # app -> what Host.adds says
        self.bound = {}                       # (app, cmd) -> what Host.control_call says
        self.answers = {}                     # cap -> async fn(args) -> result, or raises
        self.calls, self.cancelled, self.delivered, self.lines = [], [], [], []
        self.state = {}
        self.skip = 0.0                       # seconds the breaker's clock is ahead
        self.notes = Notes(lambda app: self.adds.get(app, []), self.call, self.deliver, self.loop.soon,
                           control=lambda app, cmd: self.bound.get((app, cmd)),
                           state=lambda ext: self.state.get(ext), note=self.lines.append,
                           now=lambda: time.monotonic() + self.skip)

    async def call(self, cap, args, timeout_ms, caller):
        self.calls.append((cap, args, timeout_ms, caller))
        try:
            return {"result": await self.answers[cap](args)}
        except asyncio.CancelledError:
            self.cancelled.append(cap)
            raise

    def deliver(self, text):
        self.delivered.append((time.monotonic(), json.loads(text)))

    def got(self, n, seconds=10):
        self.assertTrue(until(lambda: len(self.delivered) >= n, seconds), self.delivered)
        return [d for _, d in self.delivered]

    def note_of(self, app, n, ext="facts"):
        """the note of ext on a wake of app held now (the count-th event delivered)"""
        count = len(self.delivered) + 1
        self.assertTrue(self.notes.hold(wake_text(app, n)))
        return self.got(count)[count - 1]["d"]["notes"][ext]

    def test_which_events_are_the_lanes(self):
        self.assertEqual(event_of(wake_text("guild", 1))[:2], ("wake", "guild"))
        self.assertEqual(event_of(app_text("guild", False))[:2], ("app", "guild"))
        self.assertEqual(event_of(app_text("guild", False))[2]["d"], {"app": "guild", "on": False})
        self.assertIsNone(event_of(wake_text("reactions", 1)))          # the workshop's own temporary app
        self.assertIsNone(event_of(app_text("reactions", True)))
        self.assertIsNone(event_of(json.dumps({"a": "Other", "t": "app.guild", "d": {}})))
        self.assertIsNone(event_of(json.dumps({"a": "WuxianKit", "t": "kit.app", "d": {"on": True}})))
        self.assertIsNone(event_of(json.dumps({"a": "WuxianKit", "t": "kit.ready", "d": {"app": "guild"}})))
        self.assertIsNone(event_of(json.dumps({"a": "WuxianKit", "t": "app.guild", "d": [1]})))
        self.assertIsNone(event_of('{"a": "WuxianKit", "t": "app.guild", "d": {"x": NaN}}'))
        self.assertIsNone(event_of("plain text app. WuxianKit"))

    def test_notes_attached(self):
        async def lookup(args):
            return {"facts": [it["n"] for it in args["wake"]["items"]]}

        async def other(args):
            return "second"
        self.adds["guild"] = [add("facts", "facts.lookup"), add("more", "more.see", 2),
                              {"ext": "plain", "wake": None},            # adds something else to the app: no note
                              add("facts", "facts.again")]              # one wake capability an extension
        self.answers.update({"facts.lookup": lookup, "more.see": other})
        self.assertTrue(self.notes.hold(wake_text("guild", 7)))
        (data,) = [d["d"] for d in self.got(1)]
        self.assertEqual(data["notes"], {"facts": {"facts": [7]}, "more": "second"})
        self.assertEqual((data["app"], data["items"]), ("guild", [{"n": 7}]))
        self.assertEqual(len(self.calls), 2)
        cap, args, timeout_ms, caller = self.calls[0]
        self.assertEqual((cap, timeout_ms), ("facts.lookup", 3000))
        self.assertEqual(caller, {"by": "workshop", "app": "guild", "chain": ["wake:guild"]})
        self.assertEqual(args.keys(), {"wake"})
        self.assertNotIn("notes", args["wake"])                          # the wake as it came
        self.assertEqual(self.calls[1][2], 2000)                         # the timeout its manifest says
        self.assertEqual(self.lines, [])

    def test_timeout_is_an_error_and_the_wake_still_comes(self):
        async def slow(args):
            await asyncio.sleep(5)
        self.adds["guild"] = [add("facts", "facts.lookup", 0.3)]
        self.answers["facts.lookup"] = slow
        t0 = time.monotonic()
        self.assertTrue(self.notes.hold(wake_text("guild", 1)))
        (event,) = self.got(1)
        self.assertLess(self.delivered[0][0] - t0, 2.0)
        self.assertEqual(event["d"]["notes"]["facts"]["reason"], "timeout")
        self.assertIn("within 0.3 s", event["d"]["notes"]["facts"]["error"])
        self.assertTrue(until(lambda: self.cancelled == ["facts.lookup"], 5))    # the call was told to stop

    def test_failures_are_errors_said_once(self):
        async def boom(args):
            raise ApiError(422, "failed", "facts.lookup: it went boom\nTraceback ...")
        self.adds["guild"] = [add("facts", "facts.lookup", 1)]
        self.answers["facts.lookup"] = boom
        self.assertEqual([self.note_of("guild", n) for n in range(2)],
                         [{"error": "facts.lookup: it went boom", "reason": "failed"}] * 2)
        self.assertEqual(len(self.lines), 1, self.lines)                 # the same reason in a row: said once

        async def down(args):
            raise ApiError(503, "down", "facts is off")
        self.answers["facts.lookup"] = down
        self.assertEqual(self.note_of("guild", 9), {"error": "facts is off", "reason": "down"})
        self.assertEqual(len(self.lines), 3, self.lines)                 # another reason, and the third in a row
        self.assertIn("3 times in a row", self.lines[2])
        self.notes.forgive("facts")

        async def odd(args):
            raise ApiError(409, "something_else", "an error of another kind")
        self.answers["facts.lookup"] = odd
        self.assertEqual(self.note_of("guild", 10)["reason"], "failed")  # a word that is none of the protocol's

        async def bad(args):
            return "\ud800"                   # no JSON text: the journal could not read the wake
        self.answers["facts.lookup"] = bad
        self.assertEqual(self.note_of("guild", 11)["reason"], "failed")

    def test_three_failures_in_a_row_pause_it(self):
        async def boom(args):
            raise ApiError(422, "failed", "boom")

        async def fine(args):
            return "fine"
        self.adds["guild"] = self.adds["party"] = [add("facts", "facts.lookup", 1)]
        self.answers["facts.lookup"] = boom
        self.assertEqual([self.note_of("guild", n)["reason"] for n in range(3)], ["failed"] * 3)
        note = self.note_of("guild", 3)
        self.assertEqual(note["reason"], "paused")
        self.assertIn("3 times in a row", note["error"])
        self.assertEqual(len(self.calls), 3)                             # not called while it is paused
        self.assertEqual(self.note_of("party", 4)["reason"], "failed")   # another app's count is its own
        self.assertEqual(len(self.calls), 4)
        self.skip += PAUSE + 1                                           # the pause is over: one call is let through
        self.assertEqual(self.note_of("guild", 5)["reason"], "failed")
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.note_of("guild", 6)["reason"], "paused")   # it failed: paused again
        self.assertEqual(len(self.calls), 5)
        self.skip += PAUSE + 1
        self.answers["facts.lookup"] = fine
        self.assertEqual(self.note_of("guild", 7), "fine")               # it answered: over
        self.assertEqual(self.note_of("guild", 8), "fine")
        self.answers["facts.lookup"] = boom
        self.assertEqual([self.note_of("guild", n)["reason"] for n in (9, 10)], ["failed"] * 2)   # counted anew
        self.assertEqual(len(self.calls), 9)

    def test_a_timeout_counts_and_a_start_does_not(self):
        async def slow(args):
            await asyncio.sleep(5)

        async def unapproved(args):
            raise ApiError(403, "needs_approval", "the player has not allowed facts yet")
        self.adds["guild"] = [add("facts", "facts.lookup", 0.1)]
        self.answers["facts.lookup"] = unapproved
        self.assertEqual([self.note_of("guild", n)["reason"] for n in range(4)], ["needs_approval"] * 4)
        self.answers["facts.lookup"] = slow
        self.state["facts"] = "starting"
        self.assertEqual([self.note_of("guild", n)["reason"] for n in range(4)], ["starting"] * 4)
        self.assertEqual(len(self.calls), 8)                             # neither is the extension failing
        self.state["facts"] = "running"
        self.assertEqual([self.note_of("guild", n)["reason"] for n in range(4)], ["timeout"] * 3 + ["paused"])
        self.assertEqual(len(self.calls), 11)

    def test_forgiven_when_it_was_loaded_or_turned_on(self):
        async def boom(args):
            raise ApiError(422, "failed", "boom")
        self.adds["guild"] = [add("facts", "facts.lookup", 1)]
        self.answers["facts.lookup"] = boom
        self.assertEqual([self.note_of("guild", n)["reason"] for n in range(4)], ["failed"] * 3 + ["paused"])
        said = len(self.lines)
        self.notes.forgive("facts")
        self.notes.forgive("nobody")
        self.assertEqual(self.note_of("guild", 4)["reason"], "failed")   # called again, and said again
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(len(self.lines), said + 1)

    def test_one_apps_wakes_keep_their_order_and_others_do_not_wait(self):
        async def lookup(args):
            n = args["wake"]["items"][0]["n"]
            await asyncio.sleep(0.6 if n == 1 else 0.01)
            return n

        async def quick(args):
            return "q"
        self.adds["guild"] = [add("facts", "facts.lookup")]
        self.adds["party"] = [add("quick", "quick.see")]
        self.answers.update({"facts.lookup": lookup, "quick.see": quick})
        for n in (1, 2, 3):
            self.assertTrue(self.notes.hold(wake_text("guild", n)))
        self.assertTrue(self.notes.hold(wake_text("party", 4)))
        events = self.got(4)
        self.assertEqual(events[0]["t"], "app.party")                   # it did not wait for guild's slow first
        guild = [e["d"]["notes"]["facts"] for e in events if e["t"] == "app.guild"]
        self.assertEqual(guild, [1, 2, 3])
        self.assertTrue(until(lambda: self.notes.queues == {}, 5))

    def test_the_apps_own_events_keep_their_place(self):
        async def lookup(args):
            n = args["wake"]["items"][0]["n"]
            await asyncio.sleep(0.4 if n == 1 else 0.01)
            return n
        self.adds["guild"] = [add("facts", "facts.lookup")]
        self.answers["facts.lookup"] = lookup
        self.assertFalse(self.notes.hold(app_text("guild", True)))       # nothing of it is held: journaled now
        self.assertTrue(self.notes.hold(wake_text("guild", 1)))
        self.assertTrue(self.notes.hold(app_text("guild", False)))       # it stopped after that wake, not before
        self.assertTrue(self.notes.hold(app_text("guild", True)))
        self.assertTrue(self.notes.hold(wake_text("guild", 2)))
        self.assertFalse(self.notes.hold(app_text("party", True)))       # another app's
        self.assertFalse(self.notes.hold(json.dumps({"a": "WuxianKit", "t": "kit.ready", "d": {}})))
        self.assertEqual(self.delivered, [])
        events = self.got(4)
        self.assertEqual([e["t"] for e in events], ["app.guild", "kit.app", "kit.app", "app.guild"])
        self.assertEqual([e["d"] for e in events[1:3]], [{"app": "guild", "on": False}, {"app": "guild", "on": True}])
        self.assertEqual([events[0]["d"]["notes"]["facts"], events[3]["d"]["notes"]["facts"]], [1, 2])

    def test_a_full_lane_lets_its_oldest_wake_go(self):
        async def lookup(args):
            n = args["wake"]["items"][0]["n"]
            if n == 0:
                await asyncio.sleep(30)
            return n
        self.adds["guild"] = [add("facts", "facts.lookup", 10), add("more", "more.see", 10)]
        self.answers.update({"facts.lookup": lookup, "more.see": lookup})
        t0 = time.monotonic()
        for n in range(LANE_MAX):                                        # the first is worked on, the others wait
            self.assertTrue(self.notes.hold(wake_text("guild", n)))
        time.sleep(0.2)
        self.assertEqual(self.delivered, [])
        self.assertTrue(self.notes.hold(wake_text("guild", LANE_MAX)))   # one more than the lane holds
        events = self.got(LANE_MAX + 1)
        self.assertLess(self.delivered[0][0] - t0, 5.0)                  # not the 10 s of its calls
        first = events[0]["d"]
        self.assertEqual(first["items"], [{"n": 0}])
        self.assertEqual({k: v["reason"] for k, v in first["notes"].items()}, {"facts": "busy", "more": "busy"})
        self.assertIn(f"more than {LANE_MAX}", first["notes"]["facts"]["error"])
        self.assertEqual([e["d"]["notes"] for e in events[1:]],
                         [{"facts": n, "more": n} for n in range(1, LANE_MAX + 1)])      # the others: in order, whole
        self.assertTrue(until(lambda: sorted(self.cancelled) == ["facts.lookup", "more.see"], 5), self.cancelled)
        self.assertEqual(len([line for line in self.lines if "busy" in line]), 1, self.lines)
        self.assertTrue(until(lambda: self.notes.queues == {}, 5))

    def test_a_bound_control_is_called_before_the_wake_goes_on(self):
        done = []

        async def skip(args):
            await asyncio.sleep(0.3)
            done.append(time.monotonic())
        self.bound[("guild", "skip")] = {"ext": "voice", "call": "voice.skip"}
        self.answers["voice.skip"] = skip
        self.assertFalse(self.notes.hold(wake_text("guild", 1, items=pressed("other"))))   # bound to nothing: as before
        self.assertFalse(self.notes.hold(wake_text("party", 1, items=pressed("skip"))))    # another app's command
        self.assertTrue(self.notes.hold(wake_text("guild", 1, items=pressed("skip"))))
        (event,) = self.got(1)
        self.assertEqual(self.calls, [("voice.skip", {}, None, {"by": "player", "app": "guild"})])
        self.assertGreaterEqual(self.delivered[0][0], done[0])           # the capability first, then the wake
        self.assertEqual(event["d"]["items"], pressed("skip"))           # the agent still sees what was pressed
        self.assertNotIn("notes", event["d"])

    def test_a_bound_control_and_notes(self):
        order = []

        async def skip(args):
            await asyncio.sleep(0.1)
            order.append("skip")

        async def lookup(args):
            order.append("lookup")
            return "note"

        async def broken(args):
            raise ApiError(422, "failed", "voice.stop: no player")
        self.bound.update({("guild", "skip"): {"ext": "voice", "call": "voice.skip"},
                           ("guild", "stop"): {"ext": "voice", "call": "voice.stop"}})
        self.adds["guild"] = [add("facts", "facts.lookup")]
        self.answers.update({"voice.skip": skip, "voice.stop": broken, "facts.lookup": lookup})
        items = pressed("stop") + pressed("skip")
        self.assertTrue(self.notes.hold(wake_text("guild", 1, items=items)))
        (event,) = self.got(1)
        self.assertEqual([c[0] for c in self.calls], ["voice.stop", "voice.skip", "facts.lookup"])   # the items' order
        self.assertEqual(order, ["skip", "lookup"])
        self.assertEqual(event["d"]["notes"], {"facts": "note"})
        self.assertEqual(event["d"]["items"], items)
        failed = [line for line in self.lines if "its control stop (voice.stop) failed" in line]
        self.assertEqual(len(failed), 1, self.lines)                     # a failure is said; the wake went on
        self.assertIn("no player", failed[0])

    def test_nothing_added_no_delay(self):
        self.adds["guild"] = []
        self.adds["party"] = [{"ext": "plain", "wake": None}]
        t0 = time.perf_counter()
        self.assertFalse(self.notes.hold(wake_text("guild", 1)))         # the caller journals it now
        self.assertFalse(self.notes.hold(wake_text("party", 1)))
        self.assertFalse(self.notes.hold(wake_text("other", 1)))
        self.assertFalse(self.notes.hold(json.dumps({"a": "WoWBridge", "t": "x", "d": 1})))
        self.assertLess(time.perf_counter() - t0, 0.05)
        self.assertEqual((self.calls, self.delivered), ([], []))

    def test_adds_that_cannot_be_read(self):
        def broken(app):
            raise RuntimeError("the registry is being written")
        self.notes.adds = broken
        self.assertFalse(self.notes.hold(wake_text("guild", 1)))
        self.assertIn("could not be read", self.lines[0])

    def test_a_slow_start_is_waited_for_its_timeout_only(self):
        finished = threading.Event()

        async def starting(args):             # local_call while the extension starts (initialize takes its time)
            try:
                await asyncio.shield(asyncio.sleep(1.0))
            finally:
                finished.set()
            return "late"
        self.adds["guild"] = [add("facts", "facts.lookup", 0.3)]
        self.answers["facts.lookup"] = starting
        self.state["facts"] = "starting"
        t0 = time.monotonic()
        self.assertTrue(self.notes.hold(wake_text("guild", 1)))
        (event,) = self.got(1)
        self.assertLess(self.delivered[0][0] - t0, 0.9)
        self.assertEqual(event["d"]["notes"]["facts"]["reason"], "starting")
        self.assertTrue(finished.wait(5))

    def test_close_delivers_the_held_wakes(self):
        async def never(args):
            await asyncio.sleep(30)
        self.adds["guild"] = [add("facts", "facts.lookup", 10)]
        self.answers["facts.lookup"] = never
        self.assertTrue(self.notes.hold(wake_text("guild", 1)))
        self.assertTrue(self.notes.hold(app_text("guild", False)))
        self.assertTrue(self.notes.hold(wake_text("guild", 2)))
        self.notes.close()
        events = [d for _, d in self.delivered]
        self.assertEqual([e["t"] for e in events], ["app.guild", "kit.app", "app.guild"])
        self.assertEqual([e["d"]["items"][0]["n"] for e in events if e["t"] == "app.guild"], [1, 2])
        self.assertEqual({e["d"]["notes"]["facts"]["reason"] for e in events if e["t"] == "app.guild"}, {"down"})
        self.assertNotIn("notes", events[1]["d"])
        self.assertFalse(self.notes.hold(wake_text("guild", 3)))         # later ones: journaled at once
        time.sleep(0.1)
        self.assertEqual(len(self.delivered), 3)                         # the runner did not deliver them again

    def test_no_loop_no_hold(self):
        notes = Notes(lambda app: [add("facts", "facts.lookup")], self.call, self.deliver, lambda coro: coro.close())
        self.assertFalse(notes.hold(wake_text("guild", 1)))
        self.assertEqual(notes.queues, {})


class NotesDaemon(unittest.TestCase):
    """the lanes over a real daemon: WuxianKit's wake through Service.on_debug, a real local extension's capability"""

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
                                  mutex_name=MUTEX + "N", extension_approval="auto")
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

    def events(self, topic):
        res = self.ok("POST", "/api/addon_events", {"addon": "WuxianKit", "topic": topic, "since": 0})
        return [e["data"] for e in res["events"]]

    def wakes(self, app):
        return self.events(f"app.{app}")

    def added(self, mapping, bound=None):
        """Host.adds and Host.control_call saying this (the seam to the manifests: local/adds.py)"""
        stack = mock.patch.multiple(
            self.svc.local,
            adds=mock.Mock(side_effect=lambda app: mapping.get(app, [])),
            control_call=mock.Mock(side_effect=lambda app, cmd: (bound or {}).get((app, cmd))))
        return stack

    def emit(self, app, n, **extra):
        self.svc.on_debug("EVENT", wake_text(app, n, **extra))

    def test_nothing_added(self):
        """the host's adds as it is (nobody adds to the app): journaled at once, without notes"""
        self.emit("plain", 1)
        self.assertEqual([w["items"] for w in self.wakes("plain")], [[{"n": 1}]])
        self.assertNotIn("notes", self.wakes("plain")[0])

    def test_notes_from_a_local_extension(self):
        self.ok("POST", "/api/local/add", {"path": str(make_extension(self.root / "ext" / "fx", "fx"))})
        with self.added({"guild": [add("fx", "fx.echo", 10)]}):
            self.emit("guild", 1)
            self.svc.on_debug("EVENT", app_text("guild", False))         # behind the wake that is held
            self.assertTrue(until(lambda: self.events("kit.app"), 20))
        (wake,) = self.wakes("guild")
        note = wake["notes"]["fx"]
        self.assertEqual(note["caller"], {"by": "workshop", "app": "guild", "chain": ["wake:guild"]})
        self.assertEqual(wake["items"], [{"n": 1}])
        res = self.ok("POST", "/api/addon_events", {"addon": "WuxianKit", "since": 0})
        mine = [e["topic"] for e in res["events"] if (e["data"] or {}).get("app") == "guild"]
        self.assertEqual(mine, ["app.guild", "kit.app"])

    def test_a_slow_start_and_a_failure(self):
        self.ok("POST", "/api/local/add", {"path": str(make_extension(self.root / "ext" / "fy", "fy"))})
        original = self.svc.local.initialize

        def slow(*args, **kw):
            time.sleep(2.0)
            return original(*args, **kw)
        with self.added({"slow": [add("fy", "fy.echo", 0.5)], "boom": [add("fy", "fy.boom", 3)]}), \
                mock.patch.object(self.svc.local, "initialize", side_effect=slow):
            t0 = time.monotonic()
            self.emit("slow", 1)
            self.assertTrue(until(lambda: self.wakes("slow"), 10))
            self.assertLess(time.monotonic() - t0, 1.8)                  # the wake did not wait for the start
            self.assertEqual(self.wakes("slow")[0]["notes"]["fy"]["reason"], "starting")
            self.assertTrue(until(lambda: self.svc.local.get("fy")["state"] == "running", 20))   # the start went on
            self.emit("slow", 2)
            self.assertTrue(until(lambda: len(self.wakes("slow")) == 2, 10))
            self.assertEqual(self.wakes("slow")[1]["notes"]["fy"]["caller"]["app"], "slow")
            self.emit("boom", 3)              # an error of the capability: the note's
            self.assertTrue(until(lambda: self.wakes("boom"), 10))
        self.assertEqual(self.wakes("boom")[0]["notes"]["fy"], {"error": "fy.boom: it went boom", "reason": "failed"})
        self.assertEqual(self.wakes("boom")[0]["items"], [{"n": 3}])

    def test_a_note_that_takes_too_long_is_cancelled(self):
        """the wake capability's timeout is the call's deadline; at its end the host is told (cancel and ping)"""
        self.ok("POST", "/api/local/add", {"path": str(make_extension(self.root / "ext" / "fz", "fz"))})
        self.ok("POST", "/api/local/call", {"cap": "fz.echo"})           # it runs: the timeout is the call's
        never = Future()                                                 # the extension does not answer
        with self.added({"late": [add("fz", "fz.echo", 0.4)]}), \
                mock.patch.object(self.svc.local, "call", return_value=never) as call, \
                mock.patch.object(self.svc.local, "cancel_call") as cancel:
            self.emit("late", 1)
            self.assertTrue(until(lambda: self.wakes("late"), 10))
        note = self.wakes("late")[0]["notes"]["fz"]
        self.assertEqual(note["reason"], "timeout")
        self.assertIn("within 0.4 s", note["error"])
        (asked,) = call.call_args_list
        self.assertEqual(asked.args[1], "echo")
        self.assertEqual(asked.args[2], {"wake": {"app": "late", "reason": "heard", "items": [{"n": 1}], "skipped": 0}})
        self.assertEqual(asked.args[3], {"by": "workshop", "app": "late", "chain": ["wake:late"]})
        self.assertEqual(asked.kwargs, {"deadline_ms": 400})
        (told,) = cancel.call_args_list
        self.assertIs(told.args[1], never)
        self.assertEqual(told.kwargs, {"ping": True})

    def test_a_bound_control(self):
        self.ok("POST", "/api/local/add", {"path": str(make_extension(self.root / "ext" / "fc", "fc"))})
        with self.added({}, {("music", "skip"): {"ext": "fc", "call": "fc.echo"}}), \
                mock.patch.object(self.svc.local, "call", wraps=self.svc.local.call) as call:
            self.emit("music", 1, items=pressed("skip"))
            self.assertTrue(until(lambda: self.wakes("music"), 20))
            self.emit("music", 2, items=pressed("other"))                # bound to nothing: nothing is called
        self.assertEqual([w["items"] for w in self.wakes("music")], [pressed("skip"), pressed("other", 1)])
        self.assertNotIn("notes", self.wakes("music")[0])
        (asked,) = call.call_args_list
        self.assertEqual(asked.args[1:], ("echo", {}, {"by": "player", "app": "music"}))

    def test_what_a_manifest_adds(self):
        """the seam whole, nothing patched: a manifest's adds (the wake of another's app) and its own app's bound
        control reach the lanes through the real host (local/adds.py)"""
        app = {"app": "tunes", "title": "点歌", "controls": ["stop", {"cmd": "skip", "label": "下一首", "call": "echo"}]}
        folder = make_extension(self.root / "ext" / "fm", "fm", adds={"hall": {"wake": {"call": "echo", "timeout": 10}}},
                                apps=[app])
        self.ok("POST", "/api/local/add", {"path": str(folder)})
        with mock.patch.object(self.svc.local, "call", wraps=self.svc.local.call) as call:
            self.emit("hall", 1)
            self.assertTrue(until(lambda: self.wakes("hall"), 20))
            self.emit("tunes", 2, items=pressed("skip", 2))
            self.assertTrue(until(lambda: self.wakes("tunes"), 20))
            self.assertTrue(until(lambda: len(call.call_args_list) == 2, 20))
        (wake,) = self.wakes("hall")
        self.assertEqual(wake["notes"]["fm"]["caller"], {"by": "workshop", "app": "hall", "chain": ["wake:hall"]})
        self.assertNotIn("notes", self.wakes("tunes")[0])
        noted, bound = call.call_args_list
        self.assertEqual((noted.args[1], noted.args[3], noted.kwargs),
                         ("echo", {"by": "workshop", "app": "hall", "chain": ["wake:hall"]}, {"deadline_ms": 10000}))
        self.assertEqual(bound.args[1:], ("echo", {}, {"by": "player", "app": "tunes"}))


if __name__ == "__main__":
    unittest.main()
