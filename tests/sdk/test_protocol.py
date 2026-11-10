"""Protocol version 1 in the Python SDK (sdk/python/wuxiankit.py): the version range at initialize, ping, cancel and
ext.cancelled(), deadline_ms and ext.remaining(), busy when every worker is taken, errors {code, message, data: {reason}},
the chain an extension's own call passes on, and the description checked with the workshop's checks; in memory
(ExtensionTest) and with the workshop's own end of the pipe (wuxianworkshop.local.rpc: frame, parse, Wire) on a real
process."""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.sdk.test_sdk import load_sdk
from wuxianworkshop.local import manifest as M
from wuxianworkshop.local import rpc

wk = load_sdk()
SDK_DIR = Path(wk.__file__).resolve().parent


def make(workers=8):
    """wait (goes on until it is cancelled or the gate opens), left (the time it has), bags and far (call back), who"""
    ext = wk.Extension("demo", folder=tempfile.gettempdir(), workers=workers)
    gate, began, seen = threading.Event(), threading.Semaphore(0), []

    @ext.keep("wait", doc="Waits.", timeout=5)
    def wait():
        began.release()
        t0 = time.monotonic()
        while not gate.is_set() and time.monotonic() - t0 < 5:
            if ext.cancelled():
                seen.append("cancelled")
                return {"stopped": True}
            time.sleep(0.01)
        return {"stopped": False}

    @ext.see("left", doc="Tells the time it has.")
    def left():
        return {"left": ext.remaining(), "cancelled": ext.cancelled()}

    @ext.see("bags", doc="Counts.", args={"wait": "number?"})
    def bags(wait=None):
        try:
            return ext.call("sense.bags", {"limit": 1}, **({"timeout": wait} if wait else {}))
        except wk.ExtensionError as e:
            seen.append((e.code, e.reason, e.cap))
            raise

    @ext.see("who", doc="Tells who called.")
    def who():
        return ext.caller

    @ext.see("refuse", doc="Refuses.", args={"code": "number"})
    def refuse(code):
        raise wk.ExtensionError("no", int(code))

    return ext, gate, began, seen


class Version(unittest.TestCase):
    def setUp(self):
        self.ext = make()[0]
        self.t = wk.ExtensionTest(self.ext)
        self.addCleanup(self.t.close)

    def test_the_range(self):
        d = self.t.initialize()
        self.assertEqual(d["protocol"], 1)
        self.assertEqual(self.t.initialize(protocol={"min": 1, "max": 3})["protocol"], 1)
        self.assertIsNone(self.t.exited)

    def test_a_range_without_1(self):
        with self.assertRaises(wk.ExtensionError) as cm:
            self.t.initialize(protocol={"min": 2, "max": 3})
        self.assertEqual((cm.exception.code, cm.exception.reason), (1, "failed"))
        self.assertIn("protocol 1", cm.exception.message)
        self.assertIn('{"min": 2, "max": 3}', cm.exception.message)
        time.sleep(0.05)
        self.assertEqual(self.t.exited, 1)                               # run() exits with 1 then
        with self.assertRaises(AssertionError) as cm:                    # and nothing answers any more
            self.t.send("ping", timeout=1)
        self.assertIn("ended", str(cm.exception))

    def test_no_range_at_all(self):
        for asked in (None, "1", {"min": 1}, {"min": "a", "max": 2}, True):
            t = wk.ExtensionTest(make()[0])
            self.addCleanup(t.close)
            ans = t.send("initialize", {"protocol": asked, "id": "demo", "options": {}})
            self.assertEqual(ans["error"]["data"], {"reason": "failed"}, asked)

    def test_a_single_number_is_a_range_of_one(self):
        ans = self.t.send("initialize", {"protocol": 1, "workshop": "test", "id": "demo", "options": {}})
        self.assertEqual(ans["result"]["protocol"], 1)

    def test_the_description_passes_the_workshop(self):
        self.assertEqual(self.t.check_description(), [])                 # initializes by itself
        desc, problems = M.check_description("demo", self.t.described)
        self.assertEqual(problems, [])
        self.assertEqual([c["id"] for c in desc["capabilities"]],
                         ["demo.wait", "demo.left", "demo.bags", "demo.who", "demo.refuse"])

        @self.ext.see("quiet")                                           # no doc: a warning there, as in extension_check
        def quiet():
            return None
        self.t.initialize()
        self.assertEqual([(p["level"], p["where"]) for p in self.t.check_description()],
                         [("warning", "capability demo.quiet")])


class Calls(unittest.TestCase):
    def setUp(self):
        self.ext, self.gate, self.began, self.seen = make(workers=2)
        self.t = wk.ExtensionTest(self.ext, answers={"sense.bags": {"free": 7}})
        self.addCleanup(self.t.close)
        self.addCleanup(self.gate.set)

    def raises(self, fn, *args, **kw):
        with self.assertRaises(wk.ExtensionError) as cm:
            fn(*args, **kw)
        return cm.exception

    def test_ping(self):
        self.assertEqual(self.t.ping(), {})                              # before initialize too
        rid = self.t.start("wait")
        self.assertTrue(self.began.acquire(timeout=5))
        self.assertEqual(self.t.ping(), {})                              # and while a call runs
        self.assertEqual(self.t.send("ping")["result"], {})
        self.gate.set()
        self.assertEqual(self.t.result(rid), {"stopped": False})

    def test_busy(self):
        a, b = self.t.start("wait"), self.t.start("wait")
        self.assertTrue(self.began.acquire(timeout=5) and self.began.acquire(timeout=5))
        t0 = time.monotonic()
        e = self.raises(self.t.call, "left")
        self.assertLess(time.monotonic() - t0, 1)                        # refused at once, not waited for
        self.assertEqual((e.code, e.reason), (2, "busy"))
        self.assertIn("demo.left", e.message)
        self.assertEqual(self.t.send("call", {"name": "left"})["error"]["data"], {"reason": "busy"})
        seen = []
        self.ext.on_options(seen.append)                                 # a hook takes no worker
        self.t.notify("options", {"options": {}})
        self.t.ping()
        for _ in range(100):
            if seen:
                break
            time.sleep(0.01)
        self.assertEqual(seen, [{}])
        self.gate.set()
        self.assertEqual([self.t.result(a), self.t.result(b)], [{"stopped": False}] * 2)
        self.assertFalse(self.t.call("left")["cancelled"])               # and free again

    def test_cancel(self):
        rid = self.t.start("wait")
        self.assertTrue(self.began.acquire(timeout=5))
        self.t.cancel(rid)
        e = self.raises(self.t.result, rid)                              # whatever it returned
        self.assertEqual((e.code, e.reason, e.message), (3, "cancelled", "demo.wait: cancelled"))
        self.assertEqual(self.seen, ["cancelled"])
        self.assertFalse(self.ext.cancelled())                           # outside a capability
        self.t.cancel(rid)                                               # gone already: nothing
        self.t.cancel(999)
        self.t.notify("cancel", {"id": [1]})
        self.t.notify("cancel", {})
        self.assertEqual(self.t.call("left"), {"left": None, "cancelled": False})

    def test_cancel_ends_the_call_it_waits_in(self):
        asked, go = threading.Event(), threading.Event()

        def slow(args):
            asked.set()
            go.wait(5)
            return {"free": 1}
        self.t.answer("sense.bags", slow)
        rid = self.t.start("bags")
        self.assertTrue(asked.wait(5))
        self.t.notify("cancel", {"id": rid})
        for _ in range(500):
            if self.seen:
                break
            time.sleep(0.01)
        self.assertEqual(self.seen, [(3, "cancelled", "sense.bags")])
        go.set()
        e = self.raises(self.t.result, rid)
        self.assertEqual((e.code, e.reason), (3, "cancelled"))
        self.t.answer("sense.bags", {"free": 2})                         # the late answer was dropped
        self.assertEqual(self.t.call("bags"), {"free": 2})

    def test_deadline(self):
        self.assertEqual(self.t.call("left"), {"left": None, "cancelled": False})
        left = self.t.call("left", deadline_ms=3000)["left"]
        self.assertTrue(2.0 < left <= 3.0, left)
        self.assertEqual(self.t.call("left", deadline_ms=0)["left"], 0.0)
        self.assertEqual(self.t.call("left", deadline_ms=-5)["left"], 0.0)
        ans = self.t.send("call", {"name": "left", "deadline_ms": "soon"})
        self.assertIsNone(ans["result"]["left"])
        self.assertIsNone(self.ext.remaining())                          # outside a capability

    def test_callers(self):
        self.assertEqual(self.t.call("who"), {"by": "agent"})
        for caller in ({"by": "extension", "id": "other", "chain": ["ext:other"]},
                       {"by": "workshop", "app": "guild", "chain": ["wake:guild"]},
                       {"by": "workshop", "chain": ["reaction:companion.elite"]},
                       {"by": "player", "app": "companion"}):
            self.assertEqual(self.t.call("who", caller=caller), caller)
        self.assertIsNone(self.t.send("call", {"name": "who", "caller": "me"})["result"])
        self.assertIsNone(self.ext.caller)

    def test_the_chain_is_passed_on(self):
        self.t.call("bags")
        self.assertEqual(self.t.calls, [{"cap": "sense.bags", "args": {"limit": 1}}])     # none came: none sent
        chain = ["reaction:companion.elite", "ext:other"]
        self.t.call("bags", caller={"by": "extension", "id": "other", "chain": chain})
        self.assertEqual(self.t.calls[-1], {"cap": "sense.bags", "args": {"limit": 1}, "chain": chain})
        self.t.initialize()
        out = []
        th = threading.Thread(target=lambda: out.append(self.ext.call("sense.bags", chain=["wake:guild"])))
        th.start()                                                       # a thread of its own names the chain itself
        th.join(5)
        self.assertEqual(out, [{"free": 7}])
        self.assertEqual(self.t.calls[-1], {"cap": "sense.bags", "args": {}, "chain": ["wake:guild"]})

    def test_reasons(self):
        raw = self.t.send("call", {"name": "nope"})["error"]
        self.assertEqual((raw["code"], raw["data"]), (-32601, {"reason": "no_capability"}))
        raw = self.t.send("call", {"name": "left", "args": {"x": 1}})["error"]
        self.assertEqual((raw["code"], raw["data"]), (-32602, {"reason": "bad_args"}))
        self.assertEqual(self.t.send("frobnicate")["error"]["data"], {"reason": "no_capability"})
        for code, answered, reason in ((1, 1, "failed"), (-32602, -32602, "bad_args"), (2, 2, "busy"),
                                       (77, 1, "failed")):               # the protocol's codes only
            e = self.raises(self.t.call, "refuse", {"code": code})
            self.assertEqual((e.code, e.reason, e.message), (answered, reason, "no"))

        def paused(args):
            raise wk.ExtensionError("sense is paused for 60 s", reason="paused")
        self.t.answer("sense.bags", paused)                              # the workshop's word reaches the capability
        e = self.raises(self.t.call, "bags")
        self.assertEqual(self.seen, [(1, "paused", "sense.bags")])
        self.assertEqual((e.code, e.reason, e.message), (1, "failed", "sense.bags: sense is paused for 60 s"))
        del self.t.answers["sense.bags"]
        self.raises(self.t.call, "bags")
        self.assertEqual(self.seen[-1], (-32601, "no_capability", "sense.bags"))

        def late(args):
            time.sleep(0.4)
            return {}
        self.t.answer("sense.bags", late)
        e = self.raises(self.t.call, "bags", {"wait": 0.1})
        self.assertEqual(self.seen[-1], (1, "timeout", "sense.bags"))
        self.assertIn("no answer from the workshop in 0.1 s", e.message)

    def test_before_initialize_and_after_the_end(self):
        e = self.raises(self.ext.call, "sense.bags")
        self.assertEqual((e.code, e.reason), (1, "starting"))
        self.t.initialize()
        self.t.close()
        self.assertEqual(self.t.exited, 0)
        e = self.raises(self.ext.call, "sense.bags")
        self.assertEqual((e.code, e.reason), (1, "down"))

    def test_close_cancels_what_runs(self):
        self.t.start("wait")
        self.assertTrue(self.began.acquire(timeout=5))
        self.t.close()
        for _ in range(200):
            if self.seen:
                break
            time.sleep(0.01)
        self.assertEqual(self.seen, ["cancelled"])


EXT_SOURCE = r'''
import time

from wuxiankit import Extension, ExtensionError

ext = Extension(workers=1)


@ext.keep("wait", doc="Waits until it is cancelled.", timeout=20)
def wait():
    while not ext.cancelled():
        time.sleep(0.01)
    return {"stopped": True}


@ext.see("left", doc="Tells the time it has.")
def left():
    return {"left": ext.remaining(), "caller": ext.caller}


@ext.see("bags", doc="Counts.")
def bags():
    try:
        return ext.call("sense.bags")
    except ExtensionError as e:
        return {"code": e.code, "reason": e.reason, "message": e.message}


if __name__ == "__main__":
    ext.run()
'''


class Peer(rpc.Wire):
    """the workshop's end of a real extension process, with the workshop's own lines and requests"""

    def __init__(self, folder):
        super().__init__()
        self.id, self.ended = "voice", threading.Event()
        self.asked, self.reply = [], lambda params: dict(result={"free": 3})
        env = dict(os.environ, PYTHONPATH=str(SDK_DIR), WUXIAN_EXTENSION="voice", PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8")
        self.p = subprocess.Popen([sys.executable, "main.py"], cwd=folder, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self.read, daemon=True).start()

    def send(self, msg):
        self.p.stdin.write(rpc.frame(msg))
        self.p.stdin.flush()

    def gone_error(self):
        return RuntimeError("the extension ended")

    def read(self):
        for raw in self.p.stdout:
            msg = rpc.parse(raw.strip())
            if msg is None:
                continue
            if "method" not in msg:
                self.answer(msg)
            elif msg["method"] == "call":
                self.asked.append(msg["params"])
                self.respond(msg["id"], **self.reply(msg["params"]))
        self.ended.set()

    def close(self):
        self.p.kill()
        self.p.wait(5)
        for f in (self.p.stdin, self.p.stdout):
            f.close()


class WithTheWorkshopsWire(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        (Path(self.tmp.name) / "main.py").write_text(EXT_SOURCE, encoding="utf-8")
        self.w = Peer(self.tmp.name)
        self.addCleanup(self.w.close)

    def ask(self, method, params=None, timeout=10):
        return self.w.request(method, params or {}).result(timeout)

    def failure(self, method, params):
        with self.assertRaises(rpc.RpcError) as cm:
            self.ask(method, params)
        e = cm.exception
        return e.code, e.data

    def test_the_protocol(self):
        answer = self.ask("initialize", dict(protocol={"min": 1, "max": 1}, workshop="0.9.13", id="voice", options={}))
        desc, problems = M.check_description("voice", answer)
        self.assertEqual((problems, [c["id"] for c in desc["capabilities"]]),
                         ([], ["voice.wait", "voice.left", "voice.bags"]))
        self.assertEqual(self.ask("ping"), {})

        caller = {"by": "workshop", "app": "guild", "chain": ["wake:guild"]}
        got = self.ask("call", dict(name="left", args={}, caller=caller, deadline_ms=3000))
        self.assertEqual(got["caller"], caller)
        self.assertTrue(2.0 < got["left"] <= 3.0, got)

        fut = self.w.request("call", dict(name="wait", args={}, caller={"by": "agent"}, deadline_ms=20000))
        rid = self.w.next_id
        time.sleep(0.2)
        self.assertEqual(self.ask("ping"), {})                           # it answers while a call runs
        self.assertEqual(self.failure("call", dict(name="left", args={})), (2, {"reason": "busy"}))
        self.assertTrue(self.w.notify("cancel", {"id": rid}))
        with self.assertRaises(rpc.RpcError) as cm:
            fut.result(10)
        self.assertEqual((cm.exception.code, cm.exception.data), (3, {"reason": "cancelled"}))

        self.assertEqual(self.failure("call", dict(name="nope", args={})), (-32601, {"reason": "no_capability"}))
        self.assertEqual(self.failure("call", dict(name="left", args={"x": 1})), (-32602, {"reason": "bad_args"}))

        chain = ["reaction:companion.elite"]                             # its own call carries the chain on
        self.assertEqual(self.ask("call", dict(name="bags", args={}, caller={"by": "workshop", "chain": chain})),
                         {"free": 3})
        self.assertEqual(self.w.asked, [{"cap": "sense.bags", "args": {}, "chain": chain}])
        self.w.reply = lambda params: dict(error={"code": 1, "message": "more than 4 hops", "data": {"reason": "loop"}})
        self.assertEqual(self.ask("call", dict(name="bags", args={}, caller={"by": "agent"})),
                         {"code": 1, "reason": "loop", "message": "more than 4 hops"})
        self.assertEqual(self.w.asked[-1], {"cap": "sense.bags", "args": {}})

        self.assertEqual(self.ask("shutdown"), {})
        self.assertEqual(self.w.p.wait(5), 0)

    def test_another_protocol(self):
        with mock.patch("sys.stderr"):
            code, data = self.failure("initialize", dict(protocol={"min": 2, "max": 2}, workshop="1.0", id="voice",
                                                         options={}))
        self.assertEqual((code, data), (1, {"reason": "failed"}))
        self.assertEqual(self.w.p.wait(5), 1)


if __name__ == "__main__":
    unittest.main()
