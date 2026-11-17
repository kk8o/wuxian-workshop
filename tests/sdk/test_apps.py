"""The SDK's apps driven by a local extension (fx2, contract2 sections 1 and 2) against the fake daemon of test_sdk.py:
the heartbeat that waits while the link stands by (beat_rule, as `wuxian listen` has it), Client.call with whole,
@ext.app and ExtensionTest.wake, and AppDriver: it opens the app, hands the wakes over one after another (an "open"
wake first, a catch-up from the kept cursor), keeps where they got in ext.data / apps.json, and after the app's end
opens it again as its why says (player: after kit.ready; extension: never; others: a few seconds later), hands a
"ready" wake after a login or UI reload the app ran through, waits while the game or the daemon is away, and stops
with shutdown. The clock is fake; the drivers run in threads."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.sdk.test_sdk import TOKEN, Base, wk


def until(fn, seconds=10.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.01)
    return bool(fn())


class Heartbeat(Base):
    def beats(self, s, seconds):
        t0 = self.fake.call_times("kit.app.status")[0]
        s.get(seconds)
        return [round(t - t0) for t in self.fake.call_times("kit.app.status")]

    def test_the_rule(self):
        self.assertEqual(wk.beat_rule(600), (200.0, 400.0))
        self.assertEqual(wk.beat_rule(90), (30.0, 0.0))
        self.assertEqual(wk.beat_rule(1200), (400.0, 800.0))
        for none in (None, 0, -5, True, "600"):
            self.assertEqual(wk.beat_rule(none), wk.beat_rule(wk.LEASE))

    def test_online_every_third_of_the_lease(self):
        self.fake.link_state = "online"
        s = self.stream()
        self.assertEqual(self.beats(s, 650), [0, 200, 400, 600])

    def test_standing_by_it_waits_until_the_lease_must_be_kept(self):
        self.fake.link_state = "standby"
        s = self.stream()
        self.assertEqual(self.beats(s, 850), [0, 400, 800])
        asked = [p for p, _, auth, _ in self.fake.requests if p == "/api/status"]
        self.assertEqual(len(asked), 2)                  # once each time a beat was due early: at 200 and 600
        self.assertTrue(all(auth == f"Bearer {TOKEN}" for p, _, auth, _ in self.fake.requests
                            if p == "/api/status"))

    def test_the_link_changes_meanwhile(self):
        self.fake.link_state = "standby"
        s = self.stream()
        t0 = self.fake.call_times("kit.app.status")[0]
        s.get(250)                                       # at 200 it stands by: the beat waits
        self.assertEqual(len(self.fake.calls("kit.app.status")), 1)
        self.fake.link_state = "online"                  # a session opened: the beat goes as soon as it is asked
        s.get(200)
        self.assertEqual([round(t - t0) for t in self.fake.call_times("kit.app.status")][:2], [0, 400])

    def test_a_short_lease_has_no_room(self):
        self.fake.lease, self.fake.link_state = 90, "standby"
        s = self.stream()
        self.assertEqual(self.beats(s, 100), [0, 30, 60, 90])
        self.assertEqual([p for p, *_ in self.fake.requests if p == "/api/status"], [])    # nothing to ask

    def test_a_daemon_without_status_beats_as_before(self):
        self.fake.link_state = None                      # 404
        self.assertIsNone(self.kit.link_state())
        s = self.stream()
        self.assertEqual(self.beats(s, 450), [0, 200, 400])


class WholeCall(Base):
    def test_whole_goes_to_the_daemon(self):
        self.assertEqual(self.kit.call("cocreate.payload", {"at": 1}, whole=True),
                         {"name": "cocreate.payload", "args": {"at": 1}})
        self.kit.call("cocreate.info")
        whole, plain = self.fake.calls()
        self.assertEqual((whole["whole"], whole["timeout_ms"]), (True, wk.CALL_TIMEOUT_MS))
        self.assertNotIn("whole", plain)
        seen = []
        real = wk.Client._post

        def post(client, info, path, body, timeout, method="POST"):
            seen.append(timeout)
            return real(client, info, path, body, timeout, method)
        with mock.patch.object(wk.Client, "_post", post):
            self.kit.call("x", whole=True, timeout_ms=2000)
            self.kit.call("x", timeout_ms=2000)
        self.assertEqual(seen, [2 + wk.WHOLE_WAIT, 32])


class Decorator(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def ext(self, manifest=None):
        if manifest is not None:
            (self.folder / "wuxian-extension.json").write_text(json.dumps(manifest), encoding="utf-8")
        return wk.Extension("cc", folder=self.folder)

    def test_what_it_takes(self):
        ext = self.ext()
        for bad in ("Co", "1app", "", "a" * 25, None, "co-create"):
            with self.assertRaises(ValueError):
                ext.app(bad)
        fn = ext.app("cocreate")(lambda w: w)
        self.assertEqual(ext._apps, {"cocreate": fn})
        with self.assertRaises(ValueError):
            ext.app("cocreate")
        with self.assertRaises(TypeError):
            ext.app("other")(None)

    def test_the_manifest_says_who_drives(self):
        local = {"id": "cc", "apps": [{"app": "cocreate", "driver": "local"}, {"app": "talk", "prompt": "talk"}]}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ext = self.ext(local)
            ext.app("cocreate")(lambda w: None)
        self.assertEqual(err.getvalue(), "")
        with contextlib.redirect_stderr(err):
            ext.app("talk")(lambda w: None)              # an agent's: said, still taken
            ext.app("ghost")(lambda w: None)
        self.assertEqual(err.getvalue().count('"driver": "local"'), 2)
        self.assertEqual(sorted(ext._apps), ["cocreate", "ghost", "talk"])

    def test_extension_test_hands_it_a_wake(self):
        ext = self.ext()
        got = []

        @ext.app("cocreate")
        def wake(w):
            got.append(w)
            if w["reason"] == "boom":
                raise RuntimeError("boom")
            return len(w["items"])

        with wk.ExtensionTest(ext) as t:
            self.assertEqual(t.wake("cocreate"), 0)
            self.assertIsNotNone(t.described)            # initialize ran first
            items = [{"id": 7, "source": "cocreate", "kind": "snapshot"}, {"id": 8, "source": "control", "cmd": "x"}]
            self.assertEqual(t.wake("cocreate", items=items), 2)
            self.assertEqual(t.wake("cocreate", {"reason": "catchup", "next": 20}, after=9), 0)
            with self.assertRaises(RuntimeError):
                t.wake("cocreate", reason="boom")
            with self.assertRaises(AssertionError):
                t.wake("other")
        self.assertEqual(got[0], {"app": "cocreate", "seq": 1, "reason": "urgent", "items": [], "skipped": 0,
                                  "after": 0, "next": 0})
        self.assertEqual((got[1]["after"], got[1]["next"], got[1]["seq"]), (6, 8, 2))
        self.assertEqual((got[2]["reason"], got[2]["after"], got[2]["next"]), ("catchup", 9, 20))


class Driver(Base):
    """an AppDriver of the app demo (the fake daemon's), its pauses on the fake clock"""

    def setUp(self):
        super().setUp()
        self.data = self.home / "data"
        env = mock.patch.dict(os.environ, {"WUXIAN_EXTENSION_DATA": str(self.data)})
        env.start()
        self.addCleanup(env.stop)
        self.fake.idle = 0.02                            # a driver polls in a loop: some real time each, not a spin
        self.ext = wk.Extension("cc", folder=self.home)
        self.got = []
        self.fail_on = set()
        self.err = io.StringIO()

    def handler(self, w):
        self.got.append(w)
        if w.get("reason") in self.fail_on or any(i.get("id") in self.fail_on for i in w.get("items") or ()):
            raise RuntimeError("the handler failed")

    def driver(self):
        d = wk.AppDriver(self.ext, "demo", self.handler, self.kit)
        d.pause = lambda s: (self.clock.sleep(s), time.sleep(0.02), d.stop.is_set())[2]
        self.addCleanup(self.finish, d)
        quiet = contextlib.redirect_stderr(self.err)
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        d.start()
        return d

    def finish(self, d):
        d.stop.set()
        d.thread.join(10)
        self.assertFalse(d.thread.is_alive())

    def reasons(self):
        return [w["reason"] for w in list(self.got)]

    def kept(self):
        try:
            return json.loads((self.data / "apps.json").read_text(encoding="utf-8"))
        except OSError:
            return None

    def end(self, why):
        """the app stops in the game: its kit.app event, and its status says so (whichever the stream sees first)"""
        self.fake.app_on, self.fake.app_why = False, why
        self.fake.emit("kit.app", {"app": "demo", "on": False, "why": why})

    def opens(self):
        return len(self.fake.calls("kit.app.open"))

    def test_open_then_the_wakes_one_after_another(self):
        self.fail_on.add(1)                              # the wake with item 1
        d = self.driver()
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.got)
        self.assertEqual(self.fake.calls("kit.app.open")[0]["args"], {"app": "demo"})    # the declared minutes
        self.assertEqual(self.got[0]["status"]["on"], True)
        self.assertEqual((self.got[0]["after"], self.got[0]["next"]), (0, 0))   # where the app's wakes got
        self.assertTrue(until(lambda: self.kept() == {"demo": {"next": 0}}), self.kept())
        self.fake.wake([1, 2])
        self.assertTrue(until(lambda: len(self.got) == 2), self.got)
        self.assertEqual([i["id"] for i in self.got[1]["items"]], [1, 2])
        self.assertTrue(until(lambda: self.kept() == {"demo": {"next": 2}}), self.kept())   # though the handler failed
        self.fake.wake([3], reason="batch")
        self.assertTrue(until(lambda: len(self.got) == 3), self.got)
        self.assertTrue(until(lambda: self.kept() == {"demo": {"next": 3}}), self.kept())
        self.assertIn(f"the wake ({self.got[1]['reason']}) failed", self.err.getvalue())
        self.assertEqual((self.opens(), d.opened, d.state), (1, 1, "listening"))

    def test_it_goes_on_where_the_last_run_got(self):
        self.data.mkdir(parents=True)
        (self.data / "apps.json").write_text(json.dumps({"demo": {"next": 3}, "other": {"next": 9}}), encoding="utf-8")
        self.fake.wake([1, 2, 3], lost=True)
        self.fake.wake([4, 5], lost=True)                # while it did not run
        self.driver()
        self.assertTrue(until(lambda: len(self.got) == 2), self.got)
        opened, caught = self.got
        self.assertEqual((opened["reason"], opened["after"], opened["next"], opened["items"]), ("open", 3, 3, []))
        self.assertEqual((caught["reason"], [i["id"] for i in caught["items"]], caught["next"]), ("catchup", [4, 5], 5))
        self.assertEqual(self.fake.calls("kit.app.recent")[0]["args"], {"app": "demo", "since": 3, "limit": 100})
        self.assertTrue(until(lambda: self.kept() == {"demo": {"next": 5}, "other": {"next": 9}}), self.kept())

    def test_an_end_that_opens_it_again_soon(self):
        self.driver()
        self.assertTrue(until(lambda: self.reasons() == ["open"]))
        t0 = self.clock.t
        self.end("expired")
        self.assertTrue(until(lambda: self.opens() == 2), self.fake.calls())
        self.assertGreaterEqual(self.fake.call_times("kit.app.open")[1] - t0, wk.APP_REOPEN)
        self.assertTrue(until(lambda: self.reasons() == ["open", "open"]), self.got)
        self.assertIn("ended (expired)", self.err.getvalue())

    def test_the_players_stop_waits_for_kit_ready(self):
        d = self.driver()
        self.assertTrue(until(lambda: self.reasons() == ["open"]))
        self.end("player")
        self.assertTrue(until(lambda: "the player stopped it" in self.err.getvalue()))
        polls = len(self.fake.polls())
        self.assertTrue(until(lambda: len(self.fake.polls()) > polls + 5))     # it waits, a while on the clock
        self.assertEqual(self.opens(), 1)
        self.assertEqual(d.thread.is_alive(), True)
        self.fake.emit("kit.ready", {"version": "0.6.1"})                       # a reload
        self.assertTrue(until(lambda: self.opens() == 2), self.fake.calls())
        self.assertTrue(until(lambda: self.reasons() == ["open", "open"]), self.got)

    def test_stopped_by_the_player_before_it_started(self):
        self.fake.app_on, self.fake.app_why = False, "player"   # the workshop restarted; the player had stopped it
        self.driver()
        self.assertTrue(until(lambda: "the player stopped it" in self.err.getvalue()))
        polls = len(self.fake.polls())
        self.assertTrue(until(lambda: len(self.fake.polls()) > polls + 5))
        self.assertEqual(self.opens(), 0)
        self.fake.emit("kit.ready", {"version": "0.6.1"})
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.got)
        self.assertEqual(self.fake.calls()[0]["name"], "kit.app.status")    # read once before the first open

    def test_another_end_before_it_started_opens_it(self):
        self.fake.app_on, self.fake.app_why = False, "expired"
        self.driver()
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.got)

    def test_the_extension_turned_off_ends_it(self):
        d = self.driver()
        self.assertTrue(until(lambda: self.reasons() == ["open"]))
        self.end("extension")
        d.thread.join(10)
        self.assertFalse(d.thread.is_alive())
        self.assertEqual((self.opens(), d.state), (1, "ended"))
        self.assertIn("its extension was turned off", self.err.getvalue())
        self.assertEqual(self.fake.calls("kit.app.stop"), [])

    def test_while_the_game_is_away_it_waits(self):
        self.fake.link_down = True
        d = self.driver()
        self.assertTrue(until(lambda: len(self.fake.call_times("kit.app.status")) >= 3))   # the status first
        times = self.fake.call_times("kit.app.status")
        self.assertTrue(all(b - a >= wk.APP_RETRY for a, b in zip(times, times[1:])), times)
        self.assertEqual((self.got, d.thread.is_alive(), self.opens()), ([], True, 0))
        self.assertIn(d.state, ("opening", "waiting"))
        self.assertEqual(self.err.getvalue().count("waiting for the game"), 1)   # said once
        self.fake.link_down = False
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.got)

    def test_while_the_daemon_is_away_it_waits(self):
        self.json.unlink()
        d = self.driver()
        self.assertTrue(until(lambda: "no_daemon" in self.err.getvalue()))
        self.assertEqual((self.got, self.fake.requests, d.thread.is_alive()), ([], [], True))
        self.write_json(self.fake.port)
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.got)

    def test_a_refusal_waits_for_kit_ready_or_longer_each_time(self):
        self.fake.app_blocked = "「公会」扩展关着"     # also an app the workshop declares a moment after the process started
        self.driver()
        self.assertTrue(until(lambda: len(self.fake.call_times("kit.app.open")) >= 6))
        times = self.fake.call_times("kit.app.open")
        waits = [min(wk.APP_REFUSED, wk.APP_RETRY * 2 ** i) for i in range(5)]       # 5, 10, 20, 40, 60
        self.assertEqual(waits[-1], wk.APP_REFUSED)
        self.assertTrue(all(w <= b - a < w + 1 for w, a, b in zip(waits, times, times[1:])), times)
        self.assertIn("not_started", self.err.getvalue())
        self.assertEqual(self.got, [])
        tries = self.opens()

        turns = []

        def ready_after(body):                           # refused once more, then the player turns it on and reloads
            if turns:
                self.fake.app_blocked = None
            else:
                self.fake.emit("kit.ready", {"version": "0.6.1"})
            turns.append(body)
        self.fake.on_call["kit.app.open"] = ready_after
        self.assertTrue(until(lambda: self.opens() >= tries + 2), self.fake.calls())
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.got)
        refused, opened = self.fake.call_times("kit.app.open")[tries:tries + 2]
        self.assertLess(opened - refused, wk.APP_REFUSED)    # the kit.ready, not the minute

    def test_an_app_that_is_not_declared_yet(self):
        self.fake.app_known = False                      # the workshop declares it at the next kit.ready
        self.driver()
        self.assertTrue(until(lambda: "call_failed" in self.err.getvalue()))
        self.fake.app_known = True
        self.fake.emit("kit.ready", {"version": "0.6.1"})
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.got)

    def test_a_reload_it_ran_through_gives_a_ready_wake(self):
        d = self.driver()
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.got)
        self.fake.wake([1, 2])
        self.assertTrue(until(lambda: len(self.got) == 2), self.got)
        statuses = len(self.fake.calls("kit.app.status"))
        self.fake.emit("kit.ready", {"version": "0.6.1"})                       # a reload; the app goes on
        self.assertTrue(until(lambda: len(self.got) == 3), self.got)
        ready = self.got[2]
        self.assertEqual((ready["reason"], ready["items"], ready["after"], ready["next"]), ("ready", [], 2, 2))
        self.assertEqual(ready["status"]["on"], True)
        self.assertGreater(len(self.fake.calls("kit.app.status")), statuses)  # after the status was read again
        self.assertTrue(until(lambda: self.kept() == {"demo": {"next": 2}}), self.kept())   # the cursor stays
        self.fake.wake([3])
        self.assertTrue(until(lambda: len(self.got) == 4), self.got)
        self.assertEqual([i["id"] for i in self.got[3]["items"]], [3])
        self.fake.emit("kit.ready", {"version": "0.6.1"})                       # once for each
        self.assertTrue(until(lambda: self.reasons()[4:] == ["ready"]), self.got)
        self.assertEqual((self.got[4]["after"], self.got[4]["next"]), (3, 3))
        self.assertEqual((self.opens(), d.opened), (1, 1))

    def test_a_ready_that_ends_the_app_gives_none(self):
        self.driver()
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.got)
        self.fake.app_on, self.fake.app_why = False, "left"                     # a login as another character
        self.fake.emit("kit.ready", {"version": "0.6.1"})
        self.assertTrue(until(lambda: self.opens() == 2), self.fake.calls())
        self.assertTrue(until(lambda: self.reasons() == ["open", "open"]), self.got)
        self.assertNotIn("ready", self.reasons())

    def test_a_ready_wake_that_fails_goes_on(self):
        self.fail_on.add("ready")
        self.driver()
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.got)
        self.fake.emit("kit.ready", {"version": "0.6.1"})
        self.assertTrue(until(lambda: self.reasons() == ["open", "ready"]), self.got)
        self.fake.wake([1])
        self.assertTrue(until(lambda: len(self.got) == 3), self.got)
        self.assertIn("the wake (ready) failed", self.err.getvalue())


class Run(Base):
    """the drivers as ext.run() starts them: after initialize is answered, and stopped by shutdown"""

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"WUXIAN_EXTENSION_DATA": str(self.home / "data")})
        env.start()
        self.addCleanup(env.stop)
        self.fake.idle = 0.02
        self.ext = wk.Extension("cc", folder=self.home)
        self.got = []
        self.ext.app("demo")(self.got.append)

    def test_shutdown_stops_them(self):
        self.ext._drive = True                           # as run() sets it
        self.ext._app_client = lambda: self.kit
        with contextlib.redirect_stderr(io.StringIO()):
            t = wk.ExtensionTest(self.ext)
            self.assertEqual(self.fake.requests, [])     # nothing before initialize
            t.initialize()
            self.assertTrue(until(lambda: [w["reason"] for w in self.got] == ["open"]), self.got)
            driver, = self.ext._drivers
            t.initialize()                               # once
            self.assertEqual(self.ext._drivers, [driver])
            t.shutdown()
            self.assertTrue(driver.stop.is_set())
            driver.thread.join(10)
        self.assertFalse(driver.thread.is_alive())
        self.assertEqual(self.fake.calls("kit.app.stop"), [])    # the workshop sees to the app

    def test_a_test_drives_nothing(self):
        with wk.ExtensionTest(self.ext) as t:
            t.initialize()
            t.settle()
        self.assertEqual((self.ext._drivers, self.fake.requests), ([], []))


if __name__ == "__main__":
    unittest.main()
