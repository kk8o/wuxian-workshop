"""Where a local extension is (host.py: one state, since when and why), with real processes of the fixture: the wait
after starts that failed, a stop while it starts, starts and stops at once, a protocol the workshop does not speak,
cancel and deadline_ms, ping and hung, the events it tells before it has answered initialize, describe."""
import threading
import time
import unittest
from concurrent.futures import Future
from unittest import mock

from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import host as H
from wuxianworkshop.local import rpc as R

from .util import HostCase, gone, make_extension, until


class Backoff(HostCase):
    def test_failed_starts_wait(self):
        self.add(mode="die")
        with mock.patch.object(H, "BACKOFF", (0.6, 30.0)):
            err = self.host.ensure("fx").exception(30)
            self.assertIsInstance(err, R.Down)
            row = self.host.get("fx")
            self.assertEqual((row["state"], row["pid"], self.host.runs["fx"]["fails"]), ("backoff", None, 1))
            self.assertIn("it could not start (1 time in a row)", row["why"])
            self.assertIn("exited before it answered initialize", row["error"])
            self.assertEqual(len(self.pids()), 1)
            # a call that comes now starts no process: it fails at once, and says how long
            fut = self.host.ensure("fx")
            self.assertTrue(fut.done())
            err = fut.exception()
            self.assertEqual((err.status, err.code), (503, "down"))
            self.assertIn("fx could not start 1 time in a row", err.message)
            self.assertIn("extension_load starts it now", err.message)
            self.assertEqual((len(self.pids()), self.host.get("fx")["state"]), (1, "backoff"))
            time.sleep(0.7)
            self.assertIsInstance(self.host.ensure("fx").exception(30), R.Down)     # the wait is over: tried again
            row = self.host.get("fx")
            self.assertEqual((row["state"], self.host.runs["fx"]["fails"], len(self.pids())), ("backoff", 2, 2))
            self.assertIn("(2 times in a row)", row["why"])
            fut = self.host.ensure("fx")                                             # 30 s now
            self.assertTrue(fut.done())
            self.assertIn("could not start 2 times in a row", fut.exception().message)
            self.assertEqual(len(self.pids()), 2)
            # extension_load is an explicit start: at once, and the failures before it are forgotten
            res = self.host.load_("fx")
            self.assertFalse(res["check"]["ok"])
            self.assertEqual((res["row"]["state"], self.host.runs["fx"]["fails"], len(self.pids())), ("backoff", 1, 3))
            # off, and on again: nothing is left of them
            row = self.host.set("fx", enabled=False)
            self.assertEqual((row["state"], row["why"], row["error"]), ("off", "it was set off", None))
            err = self.host.ensure("fx").exception(1)
            self.assertEqual((err.status, err.code), (409, "off"))
            row = self.host.set("fx", enabled=True)
            self.assertEqual((row["state"], self.host.runs["fx"]["fails"], self.host.runs["fx"]["retry_at"]),
                             ("stopped", 0, 0.0))
            for pid in self.pids():
                self.assertTrue(gone(pid))

    def test_an_initialize_that_works_forgets_them(self):
        _, folder = self.add(mode="die")
        with mock.patch.object(H, "BACKOFF", (0.3, 0.3)):
            self.assertIsInstance(self.host.ensure("fx").exception(30), R.Down)
            time.sleep(0.4)
            self.assertIsInstance(self.host.ensure("fx").exception(30), R.Down)
            self.assertEqual((self.host.get("fx")["state"], self.host.runs["fx"]["fails"]), ("backoff", 2))
            make_extension(folder, "fx")                                             # mended
            time.sleep(0.4)
            self.assertEqual(self.host.call_sync("fx.echo", {"text": "a"})["text"], "a")
            row, run = self.host.get("fx"), self.host.runs["fx"]
            self.assertEqual((row["state"], row["error"], run["fails"], run["retry_at"]), ("running", None, 0, 0.0))

    def test_a_protocol_the_workshop_does_not_speak(self):
        self.add(extra="proto9")
        err = self.host.ensure("fx").exception(30)
        self.assertIsInstance(err, R.Down)
        self.assertIn("it speaks protocol 9, this workshop speaks 1", err.message)
        self.assertEqual((err.problems[0]["level"], err.problems[0]["where"]), ("error", "initialize: protocol"))
        row = self.host.get("fx")
        self.assertEqual((row["state"], row["pid"], row["capabilities"]), ("backoff", None, []))
        self.assertIn("speaks protocol 9", row["error"])
        self.assertTrue(any("it cannot be used: it speaks protocol 9" in line for line in self.output()))
        self.assertTrue(gone(self.pids()[0]))                                        # it is stopped
        self.assertEqual(self.host.procs, {})


class StartAndStop(HostCase):
    def test_a_stop_while_it_starts(self):
        self.add(mode="mute")
        with mock.patch.object(H, "INIT_TIMEOUT", 30):
            fut = self.host.ensure("fx")
            self.assertIs(self.host.ensure("fx"), fut)                               # the start in progress
            self.assertTrue(until(self.pids))
            pid = self.pids()[0]
            row = self.host.get("fx")
            self.assertEqual((row["state"], row["pid"]), ("starting", pid))
            t0 = time.monotonic()
            row = self.host.stop("fx")
            self.assertLess(time.monotonic() - t0, 8)
        err = fut.exception(1)
        self.assertEqual((err.status, err.code), (409, "stopped"))
        self.assertEqual((row["state"], row["pid"], row["error"]), ("stopped", None, None))
        self.assertIn("was stopped while it started", row["why"])
        self.assertEqual((self.host.procs, self.host.starting, self.host.runs["fx"]["fails"]), ({}, {}, 0))
        self.assertTrue(gone(pid))
        self.assertFalse(any("failed:" in line for line in self.output()))           # a stop is no failed start

    def test_starts_and_stops_at_once(self):
        self.add()
        answers, codes, odd = [], [], []

        def calls():
            for _ in range(5):
                try:
                    answers.append(self.host.call_sync("fx.echo", {"text": "a"})["text"])
                except ApiError as e:
                    codes.append(e.code)
                except Exception as e:
                    odd.append(repr(e))

        def stops():
            for _ in range(5):
                time.sleep(0.12)
                try:
                    self.host.stop("fx")
                except Exception as e:
                    odd.append(repr(e))

        threads = [threading.Thread(target=calls) for _ in range(3)] + [threading.Thread(target=stops)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        self.assertEqual(odd, [])
        self.assertLessEqual(set(codes), {"stopped", "down"}, codes)   # stopped while it started, or under the call
        self.assertEqual(len(answers) + len(codes), 15)
        row = self.host.stop("fx")
        self.assertEqual((row["state"], row["pid"]), ("stopped", None))
        self.assertEqual((self.host.procs, self.host.starting), ({}, {}))
        for pid in self.pids():
            self.assertTrue(gone(pid), pid)                            # none of the runs is left
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "b"})["text"], "b")
        self.assertEqual(self.host.get("fx")["state"], "running")

    def test_since_and_why(self):
        t0 = time.time()
        row, _ = self.add()
        self.assertEqual((row["state"], row["why"], row["pid"]), ("stopped", None, None))
        seen = [row["since"]]
        self.host.call_sync("fx.echo", {"text": "a"})
        row = self.host.get("fx")
        self.assertEqual((row["state"], row["error"], row["exit_code"]), ("running", None, None))
        self.assertIn("ready in", row["why"])
        self.assertGreaterEqual(row["since"], round(t0, 3) - 0.001)
        seen.append(row["since"])
        row = self.host.stop("fx")
        self.assertEqual((row["state"], row["exit_code"], row["error"], row["pid"]), ("stopped", 0, None, None))
        self.assertIn("it was stopped after", row["why"])
        seen.append(row["since"])
        row = self.host.set("fx", enabled=False)
        self.assertEqual((row["state"], row["why"]), ("off", "it was set off"))
        seen.append(row["since"])
        row = self.host.set("fx", enabled=True)
        self.assertEqual((row["state"], row["why"]), ("stopped", None))
        seen.append(row["since"])
        with self.assertRaises(ApiError) as cm:
            self.host.call_sync("fx.crash")
        self.assertEqual(cm.exception.code, "down")
        self.assertTrue(until(lambda: self.host.get("fx")["state"] == "exited"))
        row = self.host.get("fx")
        self.assertEqual((row["exit_code"], row["pid"]), (3, None))
        self.assertIn("it exited by itself with code 3 after", row["why"])
        self.assertIn("it exited with code 3", row["error"])
        seen.append(row["since"])
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "b"})["text"], "b")     # its next call starts it
        row = self.host.get("fx")
        self.assertEqual((row["state"], row["error"], row["exit_code"]), ("running", None, None))
        seen.append(row["since"])
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(self.pids()), 3)
        self.assertEqual(H.STATES, ("off", "stopped", "starting", "running", "hung", "stopping", "exited", "backoff",
                                    "game"))


class Calls(HostCase):
    def cancelled(self):
        return self.host.call_sync("fx.cancelled")

    def test_cancel_and_deadline(self):
        self.add(extra="caps")
        proc = self.host.ensure("fx").result(30)
        fut = self.host.call(proc, "slow", {"seconds": 1}, None, deadline_ms=1234.9)
        self.assertTrue(until(lambda: fut.rid not in proc.unwritten))
        self.host.cancel_call(proc, fut)
        err = fut.exception(1)
        self.assertEqual((err.status, err.code), (409, "cancelled"))
        self.assertTrue(until(lambda: self.cancelled()["ids"] == [fut.rid]))             # it was told cancel {id}
        self.assertEqual(self.cancelled()["deadlines"]["slow"], 1234)
        time.sleep(1.3)                                                # its answer comes late: dropped, nothing said
        self.assertFalse(any("dropped" in line for line in self.output()))
        self.assertEqual(self.host.get("fx")["state"], "running")
        # a Future that is cancelled is the same
        other = self.host.call(proc, "slow", {"seconds": 0.5}, None)
        self.assertTrue(until(lambda: other.rid not in proc.unwritten))
        self.assertTrue(other.cancel())
        self.assertTrue(until(lambda: self.cancelled()["ids"] == [fut.rid, other.rid]))
        # one that is answered: nothing is told
        done = self.host.call(proc, "echo", {"text": "a"}, None)
        self.assertEqual(done.result(10)["text"], "a")
        self.host.cancel_call(proc, done)
        self.assertEqual(done.result(0)["text"], "a")
        # call_sync tells how long it waits
        self.host.call_sync("fx.echo", {"text": "a"}, timeout=7)
        got = self.cancelled()
        self.assertEqual((got["ids"], got["deadlines"]["echo"], got["deadlines"]["cancelled"]),
                         ([fut.rid, other.rid], 7000, 30000))

    def test_a_call_that_times_out_of_one_that_is_alive(self):
        self.add(extra="caps")
        proc = self.host.ensure("fx").result(30)
        with self.assertRaises(ApiError) as cm:
            self.host.call_sync("fx.slow", {"seconds": 1}, timeout=0.3)
        self.assertEqual((cm.exception.status, cm.exception.code), (504, "timeout"))
        self.assertTrue(until(lambda: not proc.pinging))               # it was asked ping, and answered
        self.assertEqual(len(self.cancelled()["ids"]), 1)              # and told cancel
        row = self.host.get("fx")
        self.assertEqual((row["state"], row["pid"]), ("running", proc.pid))

    def test_hung(self):
        self.add(extra="caps")
        self.assertEqual(self.host.call_sync("fx.hang"), {"deaf": True})
        pid = self.host.get("fx")["pid"]
        with mock.patch.object(R, "PING_TIMEOUT", 0.5):
            with self.assertRaises(ApiError) as cm:
                self.host.call_sync("fx.echo", {"text": "a"}, timeout=0.3)
            self.assertEqual(cm.exception.code, "timeout")
            self.assertTrue(until(lambda: self.host.get("fx")["state"] == "hung", 5))
        row = self.host.get("fx")
        self.assertEqual(row["pid"], pid)                              # it still runs
        self.assertIn("did not answer ping", row["why"])
        self.assertEqual(row["error"], row["why"])
        self.assertTrue(any("hung: it did not answer ping" in line for line in self.output()))
        self.assertTrue(any("fx: hung" in note for note in self.notes))
        # its next call ends its tree and starts it again
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "b"})["text"], "b")
        row = self.host.get("fx")
        self.assertEqual((row["state"], row["error"]), ("running", None))
        self.assertNotEqual(row["pid"], pid)
        self.assertTrue(gone(pid))
        self.assertTrue(any("it is hung: its process tree is ended, and it starts again" in line
                            for line in self.output()))

    def test_ping(self):
        self.add(extra="noping")
        proc = self.host.ensure("fx").result(30)
        with mock.patch.object(R, "PING_TIMEOUT", 0.5):
            asked = self.host.ping(proc)
            self.assertIsInstance(asked, Future)
            self.assertIsNone(self.host.ping(proc))                    # one at a time
            self.assertTrue(until(lambda: self.host.get("fx")["state"] == "hung", 5))
        self.assertEqual(asked.exception(1).code, "cancelled")
        self.assertIsNone(self.host.ping(proc))                        # not of one that is hung already
        other = self.host.ensure("fx").result(30)                      # started again
        self.assertIsNot(other, proc)
        self.assertTrue(gone(proc.pid))
        self.assertIsNone(self.host.ping(proc))                        # nor of a run that is over
        self.assertEqual(self.host.get("fx")["state"], "running")

    def test_what_an_answer_to_ping_says(self):
        self.add()
        proc = self.host.ensure("fx").result(30)
        self.assertEqual(self.host.ping(proc).result(5), {})           # the fixture answers {}
        self.assertTrue(until(lambda: not proc.pinging))
        for error, state in ((R.RpcError(R.NO_METHOD, "no method ping"), "running"),     # an SDK without ping: alive
                             (R.busy_error("fx", "its pipe takes no line"), "hung")):
            answered = Future()
            answered.rid = None
            answered.set_exception(error)
            with mock.patch.object(proc, "request", return_value=answered):
                self.assertIs(self.host.ping(proc), answered)
                self.assertTrue(until(lambda: not proc.pinging))
            self.assertEqual(self.host.get("fx")["state"], state)


class Events(HostCase):
    def test_events_before_it_answered_initialize(self):
        self.add(extra="early")
        self.host.ensure("fx").result(30)
        self.assertTrue(until(lambda: len(self.events) >= 100))
        time.sleep(0.3)
        self.assertEqual([(eid, item["kind"], item["data"]["n"]) for eid, item in self.events],
                         [("fx", "fx.said", n) for n in range(100)])   # the first hundred, in their order
        out = self.output()
        self.assertEqual(sum("it tells events before it has answered initialize: 100 are kept until it has, the rest "
                             "are dropped" in line for line in out), 1)
        self.assertEqual(sum("50 events it told before it answered initialize dropped (100 are kept at most)" in line
                             for line in out), 1)
        self.host.call_sync("fx.emit", {"text": "now"})                # from then on they go on as they come
        self.assertTrue(until(lambda: self.events[-1][1]["data"] == {"text": "now"}))
        self.assertEqual(len(self.events), 101)


class Describe(HostCase):
    def test_describe(self):
        row, _ = self.add()
        self.assertEqual((row["state"], row["capabilities"], row["options"]), ("stopped", [], []))
        self.assertIsNone(self.host.describe("fx").result(30))
        row = self.host.get("fx")                                      # it said what it can do, and runs no more
        self.assertEqual((row["state"], row["pid"], row["error"]), ("stopped", None, None))
        self.assertIn("fx.echo", row["capabilities"])
        self.assertEqual(([o["name"] for o in row["options"]], row["sources"]), (["volume"], ["said"]))
        self.assertTrue(gone(self.pids()[0]))
        self.assertEqual(self.host.procs, {})
        row = self.host.set("fx", options={"volume": 80})              # its options can be set before its first call
        self.assertEqual(row["options"][0]["value"], 80)
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "a"})["init"]["options"], {"volume": 80})
        proc = self.host.procs["fx"]
        self.assertIs(self.host.describe("fx").result(1), proc)        # one that runs is left alone
        self.assertEqual((self.host.get("fx")["state"], len(self.pids())), ("running", 2))

    def test_a_call_that_comes_meanwhile_keeps_it(self):
        self.add()
        real, seen = self.host.initialize, []

        def initialize(proc, m, options, timeout=None):
            answer = real(proc, m, options, timeout)
            seen.append(self.host.ensure("fx"))                        # a call's start, while it is being probed
            return answer

        with mock.patch.object(self.host, "initialize", initialize):
            fut = self.host.describe("fx")
            self.assertTrue(fut.probe)
            proc = fut.result(30)
        self.assertIs(seen[0], fut)
        self.assertIs(proc, self.host.procs["fx"])
        self.assertEqual((self.host.get("fx")["state"], fut.probe), ("running", False))
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "a"})["text"], "a")
        self.assertEqual(len(self.pids()), 1)


if __name__ == "__main__":
    unittest.main()
