"""The wire of local extensions without a process (rpc.py): lines, requests and their answers, cancel, the ids and the
pipes that refuse, the error answers and the one set of reasons, the protocol version an extension answers with."""
import json
import threading
import unittest
from concurrent.futures import CancelledError
from unittest import mock

from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import rpc as R


class FakeWire(R.Wire):
    """a Wire whose pipe is a list (sent); refuse: what send raises"""

    def __init__(self):
        super().__init__()
        self.id, self.ended, self.sent, self.refuse = "fx", threading.Event(), [], None

    def gone_error(self):
        return ApiError(503, "down", "fx exited (code 3)")

    def send(self, msg):
        if self.refuse is not None:
            raise self.refuse
        self.sent.append(json.loads(R.frame(msg)))


class Lines(unittest.TestCase):
    def test_frame_and_parse(self):
        line = R.frame({"jsonrpc": "2.0", "id": 1, "method": "call", "params": {"text": "你好"}})
        self.assertEqual(line, '{"jsonrpc":"2.0","id":1,"method":"call","params":{"text":"你好"}}\n'.encode("utf-8"))
        self.assertEqual(R.parse(line.decode("utf-8").strip())["params"], {"text": "你好"})
        self.assertEqual(R.parse('{"id": 1, "result": 2}'), {"id": 1, "result": 2})       # jsonrpc left out: still one
        self.assertEqual(R.parse('{"method": "event"}'), {"method": "event"})
        for text in ("not the protocol", "[1, 2]", '"text"', "12", '{"level": "info"}', "{"):
            self.assertIsNone(R.parse(text), text)
        with self.assertRaises(TypeError):
            R.frame({"params": {1, 2}})


class Requests(unittest.TestCase):
    def test_request_and_answer(self):
        w = FakeWire()
        fut = w.request("call", {"name": "echo"})
        self.assertEqual(w.sent, [{"jsonrpc": "2.0", "id": 1, "method": "call", "params": {"name": "echo"}}])
        self.assertEqual((fut.rid, fut.done()), (1, False))
        self.assertTrue(w.answer({"jsonrpc": "2.0", "id": 1, "result": {"a": 1}}))
        self.assertEqual(fut.result(1), {"a": 1})
        self.assertFalse(w.answer({"jsonrpc": "2.0", "id": 1, "result": {"a": 2}}))      # a second answer: dropped
        self.assertEqual(w.request("ping", {}).rid, 2)
        self.assertEqual(w.pending.keys(), {2})

    def test_error_answers(self):
        w = FakeWire()
        for code, reason in ((-32602, "bad_args"), (-32601, "no_capability"), (1, "failed"), (2, "busy"),
                             (3, "cancelled"), (99, "failed"), (-32000, "failed")):
            fut = w.request("call", {})
            w.answer({"id": fut.rid, "error": {"code": code, "message": "no", "data": {"reason": reason}}})
            err = fut.exception(1)
            self.assertIsInstance(err, R.RpcError)
            self.assertEqual((err.code, err.reason, err.message, err.data), (code, reason, "no", {"reason": reason}))
        for code in ("x", True, None, 1.5):                        # no number: failed
            fut = w.request("call", {})
            w.answer({"id": fut.rid, "error": {"code": code}})
            self.assertEqual((fut.exception(1).code, fut.exception(1).message), (R.FAILED, "failed"))
        fut = w.request("call", {})
        w.answer({"id": fut.rid, "error": "it broke"})             # no object
        self.assertEqual((fut.exception(1).code, fut.exception(1).message), (R.FAILED, "it broke"))

    def test_cancel(self):
        w = FakeWire()
        fut = w.request("call", {"name": "slow"})
        self.assertTrue(w.cancel(fut))
        self.assertEqual(w.sent[-1], {"jsonrpc": "2.0", "method": "cancel", "params": {"id": 1}})
        err = fut.exception(1)
        self.assertEqual((err.status, err.code), (409, "cancelled"))
        self.assertFalse(w.answer({"id": 1, "result": "late"}))    # its answer is dropped
        self.assertFalse(w.cancel(fut))                            # once
        self.assertEqual(len(w.sent), 2)
        # a Future that is cancelled (asyncio does that to one it wraps) is the same
        fut = w.request("call", {"name": "slow"})
        self.assertTrue(fut.cancel())
        self.assertEqual(w.sent[-1], {"jsonrpc": "2.0", "method": "cancel", "params": {"id": 2}})
        self.assertFalse(w.answer({"id": 2, "result": "late"}))
        self.assertEqual(w.pending, {})
        # one that is answered already: nothing is told
        fut = w.request("call", {})
        w.answer({"id": 3, "result": 1})
        self.assertFalse(w.cancel(fut))
        self.assertEqual((fut.result(1), len(w.sent)), (1, 5))

    def test_ids_that_are_none_of_the_workshops(self):
        w = FakeWire()
        fut = w.request("call", {})
        for rid in ([], {}, [1], None, "1", 1.5, 1.0, 7, True):    # a list or an object is no key: no exception
            self.assertFalse(w.answer({"id": rid, "result": 1}), rid)
        self.assertFalse(w.answer({"result": 1}))
        self.assertIsNone(w.forget([]))
        self.assertFalse(w.fail({}, ApiError(503, "down", "x")))
        self.assertFalse(fut.done())
        self.assertTrue(w.answer({"id": 1, "result": 2}))

    def test_a_pipe_that_refuses(self):
        w = FakeWire()
        w.refuse = R.Busy("256 lines wait to be written to it")
        err = w.request("call", {}).exception(1)
        self.assertEqual((err.status, err.code), (429, "busy"))
        self.assertIn("fx is busy: 256 lines wait", err.message)
        self.assertFalse(w.notify("options", {}))
        self.assertFalse(w.respond("x1", result=1))
        w.refuse = BrokenPipeError("its input is closed")
        err = w.request("call", {}).exception(1)
        self.assertEqual((err.status, err.code), (503, "down"))
        self.assertFalse(w.notify("options", {}))
        self.assertFalse(w.respond("x1", result=1))
        self.assertEqual(w.pending, {})                            # none of them waits
        w.refuse = None
        err = w.request("call", {"args": {1, 2}}).exception(1)     # what it is asked is no JSON
        self.assertEqual((err.status, err.code), (400, "bad_args"))
        self.assertFalse(w.notify("options", {"x": object()}))
        w.ended.set()
        err = w.request("call", {}).exception(1)
        self.assertEqual((err.code, err.message), ("down", "fx exited (code 3)"))
        self.assertEqual((w.pending, w.sent), ({}, []))

    def test_respond(self):
        w = FakeWire()
        self.assertTrue(w.respond("x1", result={"free": 12}))
        self.assertTrue(w.respond("x2", error=R.error("down", "the game is not online")))
        self.assertTrue(w.respond("x3", result={1, 2}))            # no JSON: the other end still gets an answer
        self.assertEqual(w.sent[0], {"jsonrpc": "2.0", "id": "x1", "result": {"free": 12}})
        self.assertEqual(w.sent[1], {"jsonrpc": "2.0", "id": "x2",
                                     "error": {"code": 1, "message": "the game is not online",
                                               "data": {"reason": "down"}}})
        self.assertEqual((w.sent[2]["id"], w.sent[2]["error"]["code"], w.sent[2]["error"]["data"]),
                         ("x3", 1, {"reason": "failed"}))
        self.assertIn("no JSON", w.sent[2]["error"]["message"])
        self.assertTrue(w.respond("x4"))                           # a result of nothing
        self.assertEqual(w.sent[3], {"jsonrpc": "2.0", "id": "x4", "result": None})


class Errors(unittest.TestCase):
    def test_reasons_and_codes(self):
        self.assertEqual(R.REASONS, ("timeout", "down", "starting", "failed", "bad_args", "no_capability", "busy",
                                     "paused", "cancelled", "not_allowed", "needs_approval", "loop"))
        self.assertEqual((R.BAD_ARGS, R.NO_METHOD, R.FAILED, R.BUSY, R.CANCELLED), (-32602, -32601, 1, 2, 3))
        codes = {"bad_args": -32602, "no_capability": -32601, "busy": 2, "cancelled": 3}
        for reason in R.REASONS:
            self.assertEqual(R.error(reason, "m"), {"code": codes.get(reason, 1), "message": "m",
                                                    "data": {"reason": reason}})
        self.assertEqual(R.error("bad_option", "m")["data"], {"reason": "failed"})       # none of the words: failed
        self.assertEqual(R.error("failed", ValueError("x"))["message"], "x")
        self.assertEqual(R.PROTOCOL, {"min": 1, "max": 1})
        self.assertEqual(R.PING_TIMEOUT, 5.0)

    def test_error_of(self):
        self.assertEqual(R.error_of(ApiError(503, "down", "the game is not online")),
                         {"code": 1, "message": "the game is not online", "data": {"reason": "down"}})
        self.assertEqual(R.error_of(ApiError(403, "not_allowed", "no")), {"code": 1, "message": "no",
                                                                          "data": {"reason": "not_allowed"}})
        self.assertEqual(R.error_of(ApiError(409, "loop", "a loop"))["data"], {"reason": "loop"})
        self.assertEqual(R.error_of(ApiError(404, "not_found", "none"))["data"], {"reason": "failed"})
        self.assertEqual(R.error_of(R.RpcError(-32602, "n: a number")),
                         {"code": -32602, "message": "n: a number", "data": {"reason": "bad_args"}})
        self.assertEqual(R.error_of(R.RpcError(2, "later")), {"code": 2, "message": "later",
                                                              "data": {"reason": "busy"}})
        self.assertEqual(R.error_of(CancelledError()), {"code": 3, "message": "cancelled",
                                                        "data": {"reason": "cancelled"}})
        self.assertEqual(R.error_of(ValueError("x")), {"code": 1, "message": "ValueError: x",
                                                       "data": {"reason": "failed"}})
        self.assertEqual(R.no_json_error(TypeError("a set"))["data"], {"reason": "failed"})

    def test_call_error(self):
        for code, status, reason in ((-32602, 400, "bad_args"), (-32601, 404, "no_capability"), (1, 422, "failed"),
                                     (2, 429, "busy"), (3, 409, "cancelled"), (42, 422, "failed")):
            err = R.call_error(R.RpcError(code, "why"), "fx.echo")
            self.assertEqual((err.status, err.code, err.message), (status, reason, "fx.echo: why"))
        down = ApiError(503, "down", "fx exited (code 3)")
        self.assertIs(R.call_error(down, "fx.echo"), down)         # an ApiError as it is
        err = R.call_error(CancelledError(), "fx.echo")
        self.assertEqual((err.status, err.code), (409, "cancelled"))
        err = R.call_error(ValueError("x"), "fx.echo")
        self.assertEqual((err.status, err.code, err.message), (422, "failed", "fx.echo: x"))
        self.assertEqual((R.busy_error("fx", "why").code, R.cancelled_error("fx").code), ("busy", "cancelled"))
        err = R.Down("it could not start")
        self.assertEqual((err.status, err.code, err.problems[0]["level"]), (503, "down", "error"))

    def test_protocol_problem(self):
        self.assertIsNone(R.protocol_problem({"protocol": 1}))
        for none in ({}, {"protocol": "1"}, {"protocol": True}, {"protocol": None}, None, [1]):
            self.assertIsNone(R.protocol_problem(none), none)      # no version: the description's check says so
        why = R.protocol_problem({"protocol": 9})
        self.assertIn("it speaks protocol 9, this workshop speaks 1", why)
        self.assertIn("update the workshop", why)
        self.assertIn("update the extension", R.protocol_problem({"protocol": 0}))
        with mock.patch.dict(R.PROTOCOL, {"min": 1, "max": 2}):
            self.assertIsNone(R.protocol_problem({"protocol": 2}))
            self.assertIn("this workshop speaks 1 to 2", R.protocol_problem({"protocol": 3}))


if __name__ == "__main__":
    unittest.main()
