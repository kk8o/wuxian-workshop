"""The SDK and the apps (contract3 sections 1 to 3). Against the fake daemon of test_sdk.py: the heartbeat of Client.app
that waits while the link stands by (beat_rule, as `wuxian listen` has it), Client.call with most. In memory
(ExtensionTest): @ext.app only names the function of an app the extension's program drives (initialize answers the
apps; the workshop opens the app and sends the request wake), one wake of an app at a time on the app's own thread,
ExtensionTest.wake, and ext.report with ExtensionTest.reports."""
import contextlib
import io
import json
import os
import tempfile
import threading
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


class MostCall(Base):
    def test_most_goes_to_the_daemon(self):
        self.assertEqual(self.kit.call("cocreate.payload", {"at": 1}), {"name": "cocreate.payload", "args": {"at": 1}})
        self.kit.call("cocreate.info", most=4096)
        plain, small = self.fake.calls()
        self.assertEqual((small["most"], small["timeout_ms"]), (4096, wk.CALL_TIMEOUT_MS))
        self.assertNotIn("most", plain)                  # not given: the daemon's own (16 MB)
        self.assertNotIn("whole", plain)
        with self.assertRaises(TypeError):
            self.kit.call("x", whole=True)               # there is no such thing any more
        seen = []
        real = wk.Client._post

        def post(client, info, path, body, timeout, method="POST"):
            seen.append(timeout)
            return real(client, info, path, body, timeout, method)
        with mock.patch.object(wk.Client, "_post", post):
            self.kit.call("x", timeout_ms=2000)          # as long as a long answer keeps coming
            self.kit.call("x", most=1 << 20, timeout_ms=2000)
            self.kit.call("x", most=256 * 1024, timeout_ms=2000)     # one that comes within seconds
            self.kit.proposal(7)                         # the client's own asks are such
        self.assertEqual(seen, [2 + wk.ANSWER_WAIT, 2 + wk.ANSWER_WAIT, 32, wk.CALL_TIMEOUT_MS / 1000 + 30])


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
        self.assertEqual(ext.describe()["apps"], ["cocreate"])       # what initialize says it drives
        self.assertFalse(hasattr(wk, "AppDriver"))                   # the workshop runs the app, not the program

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
            self.assertEqual(t.described["apps"], ["cocreate"])
            items = [{"id": 7, "source": "cocreate", "kind": "snapshot"}, {"id": 8, "source": "control", "cmd": "x"}]
            self.assertEqual(t.wake("cocreate", items=items), 2)
            self.assertEqual(t.wake("cocreate", {"reason": "catchup", "next": 20}, after=9), 0)
            self.assertEqual(t.wake("cocreate", reason="open", status={"on": True}), 0)
            with self.assertRaises(RuntimeError):
                t.wake("cocreate", reason="boom")
            with self.assertRaises(AssertionError):
                t.wake("other")
        self.assertEqual(got[0], {"app": "cocreate", "seq": 1, "reason": "urgent", "items": [], "skipped": 0,
                                  "after": 0, "next": 0})
        self.assertEqual((got[1]["after"], got[1]["next"], got[1]["seq"]), (6, 8, 2))
        self.assertEqual((got[2]["reason"], got[2]["after"], got[2]["next"]), ("catchup", 9, 20))
        self.assertEqual((got[3]["reason"], got[3]["status"]), ("open", {"on": True}))


class Wakes(unittest.TestCase):
    """the request wake {app, wake}, as the workshop sends it: one wake of an app at a time, in a thread of the app's
    own, answered {} once its function returned, whatever it did"""

    def setUp(self):
        self.ext = wk.Extension("cc", folder=tempfile.gettempdir())
        self.got, self.gate, self.inside = [], threading.Event(), threading.Event()
        self.threads = set()

        @self.ext.app("cocreate")
        def cocreate(w):
            self.threads.add(threading.current_thread().name)
            self.got.append(("cocreate", w["next"]))
            if w["reason"] == "slow":
                self.inside.set()
                self.gate.wait(10)
            if w["reason"] == "boom":
                raise RuntimeError("the handler failed")

        @self.ext.app("other")
        def other(w):
            self.threads.add(threading.current_thread().name)
            self.got.append(("other", w["next"]))

        @self.ext.see("hello", doc="Says hello.")
        def hello():
            return "hi"

        self.t = wk.ExtensionTest(self.ext)
        self.addCleanup(self.t.close)
        self.addCleanup(self.gate.set)
        self.t.initialize()

    def wake(self, app, nxt, reason="item"):
        return {"app": app, "wake": {"app": app, "seq": nxt, "reason": reason, "items": [], "skipped": 0,
                                     "after": nxt - 1, "next": nxt}}

    def test_answered_once_the_function_returned(self):
        self.assertEqual(self.t.send("wake", self.wake("cocreate", 1))["result"], {})
        self.assertEqual(self.got, [("cocreate", 1)])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):            # what it raises goes to the log: answered all the same
            self.assertEqual(self.t.send("wake", self.wake("cocreate", 2, "boom"))["result"], {})
        self.assertIn("the wake (boom) failed", err.getvalue())
        self.assertIn("RuntimeError: the handler failed", err.getvalue())
        self.assertEqual(self.t.send("wake", self.wake("cocreate", 3))["result"], {})    # and the next one comes

    def test_one_at_a_time_each_app_on_its_own_thread(self):
        slow = self.t.post("wake", self.wake("cocreate", 1, "slow"))
        self.assertTrue(self.inside.wait(10))
        nxt = self.t.post("wake", self.wake("cocreate", 2))          # behind the one that runs
        self.assertEqual(self.t.send("wake", self.wake("other", 9))["result"], {})   # another app's goes on
        self.assertEqual(self.t.call("hello"), "hi")                 # and so do its capabilities, and ping
        self.assertEqual(self.t.ping(), {})
        self.assertEqual(self.got, [("cocreate", 1), ("other", 9)])
        self.gate.set()
        self.assertEqual(self.t.result(slow), {})
        self.assertEqual(self.t.result(nxt), {})
        self.assertEqual(self.got, [("cocreate", 1), ("other", 9), ("cocreate", 2)])
        self.assertEqual(self.threads, {"wuxiankit-app-cocreate", "wuxiankit-app-other"})

    def test_what_is_no_wake(self):
        for params, code in (({"app": "ghost", "wake": {}}, -32601), ({"wake": {}}, -32601),
                             ({"app": "cocreate", "wake": [1]}, -32602), ({"app": "cocreate"}, -32602)):
            ans = self.t.send("wake", params)
            self.assertEqual(ans["error"]["code"], code, params)
        self.assertIn("it drives: cocreate, other", self.t.send("wake", {"app": "ghost", "wake": {}})["error"]["message"])
        self.assertEqual(self.got, [])

    def test_shutdown_leaves_the_waiting_ones_unanswered(self):
        slow = self.t.post("wake", self.wake("cocreate", 1, "slow"))
        self.assertTrue(self.inside.wait(10))
        waiting = self.t.post("wake", self.wake("cocreate", 2))
        self.t.shutdown()                                # (close: the loop ended)
        self.gate.set()
        self.assertEqual(self.t.exited, 0)
        time.sleep(0.1)
        self.assertEqual(self.got, [("cocreate", 1)])    # the one behind never ran: the workshop gives it again
        del slow, waiting


class Reports(unittest.TestCase):
    """ext.report: checked as the workshop checks it, told as the notification report; ExtensionTest keeps them"""

    def setUp(self):
        self.ext = wk.Extension("cc", folder=tempfile.gettempdir())

        @self.ext.app("cocreate")
        def cocreate(w):
            self.ext.report("cocreate", "k1", "run", step="fetch", text="取回快照", done=1, total=4)
            self.ext.report("cocreate", "k1", "done", data={"n": 3})

        self.t = wk.ExtensionTest(self.ext)
        self.addCleanup(self.t.close)

    def test_told_in_order(self):
        self.ext.report("cocreate", "early", "run")      # before initialize: held, as events are
        self.assertEqual(self.t.reports, [])
        self.t.wake("cocreate")                          # (initialize first; back when what it told was read)
        self.assertEqual(self.t.reports, [
            {"app": "cocreate", "key": "early", "state": "run"},
            {"app": "cocreate", "key": "k1", "state": "run", "step": "fetch", "text": "取回快照", "done": 1, "total": 4},
            {"app": "cocreate", "key": "k1", "state": "done", "data": {"n": 3}}])
        self.ext.report("cocreate", "1794938824:Horde", "retry", retry=1794939000, text="  网络 \n 不通  ")
        self.t.ping()
        self.assertEqual(self.t.reports[-1], {"app": "cocreate", "key": "1794938824:Horde", "state": "retry",
                                              "retry": 1794939000, "text": "网络 不通"})

    def test_what_is_no_report(self):
        ok = dict(app="cocreate", key="k", state="run")
        for bad, words in ((dict(ok, app="talk"), "drives no app 'talk'"), (dict(ok, app="Bad"), "not an app id"),
                           (dict(ok, key=""), "key"), (dict(ok, key="k" * 65), "key"), (dict(ok, key=" k"), "key"),
                           (dict(ok, key="a\nb"), "key"), (dict(ok, key=7), "key"),
                           (dict(ok, state="busy"), "state"), (dict(ok, state=None), "state"),
                           (dict(ok, step="Fetch"), "step"), (dict(ok, step="x" * 25), "step"),
                           (dict(ok, text="x" * 201), "text"), (dict(ok, text=7), "text"),
                           (dict(ok, text="a\x07b"), "text"),
                           (dict(ok, done="3"), "done"), (dict(ok, total=float("nan")), "total"),
                           (dict(ok, retry=True), "retry"),
                           (dict(ok, data=[1]), "data"), (dict(ok, data={"big": "x" * 2100}), "data"),
                           (dict(ok, data={"x": object()}), "data")):
            with self.assertRaises(ValueError, msg=bad) as cm:
                self.ext.report(**bad)
            self.assertIn(words, str(cm.exception), bad)
        with self.assertRaises(TypeError):
            self.ext.report("cocreate", "k", "run", colour="red")
        self.t.initialize()
        self.t.ping()
        self.assertEqual(self.t.reports, [])
        self.ext.report("cocreate", "k" * 64, "failed", text="中" * 66, data={"a": "x" * 2030}, step="a_1")
        self.t.ping()                                    # at the limits: taken
        self.assertEqual(len(self.t.reports), 1)
        self.assertEqual(wk.check_report({"app": "a", "key": "k", "state": "run", "text": "  ", "step": None}),
                         ({"app": "a", "key": "k", "state": "run"}, None))


if __name__ == "__main__":
    unittest.main()
