r"""The wire between the workshop and a local extension: one stdio pipe, JSON-RPC 2.0, UTF-8, one object per line. No
process here (process.py has the pipe and its threads): the lines (frame, parse), the workshop's requests and the Futures
waiting for their answers (Wire, which process.Proc is), the protocol's numbers and the errors of a call.

The workshop asks (requests): initialize {protocol, workshop, id, options} (INIT_TIMEOUT seconds: a model may load),
call {name, args, caller}, shutdown {}; it tells (notifications): options {options} when the player changed them (all of
them). The extension tells event {source, data} and asks call {cap, args}; what the workshop does with them is the
host's (host.py: on_message, extension_event, extension_call).

An error answer is {code, message, data?}: BAD_ARGS, NO_METHOD or FAILED; one answered to an extension's own call also
has data {reason}, one of REASONS. Standard library only.
"""
import json
import threading
from concurrent.futures import Future

from ..daemon.api import ApiError
from . import manifest as M

INIT_TIMEOUT = 180.0          # seconds initialize may take (an extension that loads a model first)

# JSON-RPC error codes of a call; an error answered to an extension's own call also has data {reason}, one of REASONS
BAD_ARGS, NO_METHOD, FAILED = -32602, -32601, 1
REASONS = ("no_capability", "bad_args", "failed", "timeout", "down", "not_allowed")


# --- errors ----------------------------------------------------------------------------------------------------------

class RpcError(Exception):
    """an error answer of the extension: {code, message, data?}"""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class Down(ApiError):
    """the extension could not be started (or initialized): why, with the end of its output; problems as check gives
    them"""

    def __init__(self, message, problems=None):
        super().__init__(503, "down", message)
        self.problems = problems or [M.problem("error", "start", message)]


def call_error(e, cap):
    """the ApiError of a call that failed in the extension"""
    if isinstance(e, RpcError):
        if e.code == BAD_ARGS:
            return ApiError(400, "bad_args", f"{cap}: {e.message}")
        if e.code == NO_METHOD:
            return ApiError(404, "no_capability", f"{cap}: {e.message}")
        return ApiError(422, "failed", f"{cap}: {e.message}")
    if isinstance(e, ApiError):
        return e
    return ApiError(422, "failed", f"{cap}: {e}")


# --- lines -----------------------------------------------------------------------------------------------------------

def frame(msg):
    """a message as its line on the pipe"""
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def parse(text):
    """the message of a line read from the pipe (stripped, not empty), or None when the line is no protocol"""
    try:
        msg = json.loads(text)
    except ValueError:
        msg = None
    if not isinstance(msg, dict) or (msg.get("jsonrpc") != "2.0" and "method" not in msg and "id" not in msg):
        return None
    return msg


# --- requests and their answers --------------------------------------------------------------------------------------

class Wire:
    """the workshop's end of the pipe: the requests waiting for their answers, by id. Whoever has the pipe
    (process.Proc) gives id (the extension's), send(msg) (the line written; OSError or ValueError when the pipe is
    closed), ended (an Event: the other end is gone) and gone_error() (what a request gets then), and hands the
    answers it reads to answer(msg)"""

    def __init__(self):
        self.lock = threading.Lock()
        self.pending, self.next_id = {}, 0

    def request(self, method, params):
        """a request: a Future of its result (RpcError for an error answer, ApiError when the process ended)"""
        fut = Future()
        with self.lock:
            if self.ended.is_set():
                fut.set_exception(self.gone_error())
                return fut
            self.next_id += 1
            rid = self.next_id
            self.pending[rid] = fut
        fut.add_done_callback(lambda f: f.cancelled() and self.forget(rid))
        try:
            self.send(dict(jsonrpc="2.0", id=rid, method=method, params=params))
        except (OSError, ValueError) as e:
            self.forget(rid)
            if not fut.done():
                fut.set_exception(ApiError(503, "down", f"{self.id}: its pipe is closed ({e})"))
        return fut

    def forget(self, rid):
        with self.lock:
            self.pending.pop(rid, None)

    def notify(self, method, params):
        try:
            self.send(dict(jsonrpc="2.0", method=method, params=params))
            return True
        except (OSError, ValueError):
            return False

    def respond(self, rid, result=None, error=None):
        msg = dict(jsonrpc="2.0", id=rid)
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        try:
            self.send(msg)
        except (OSError, ValueError):
            pass

    def answer(self, msg):
        """(the reader thread) an answer of the extension: the Future of its request gets the result, or RpcError"""
        with self.lock:
            fut = self.pending.pop(msg.get("id"), None)
        if fut is None or fut.done():
            return
        err = msg.get("error")
        if err is not None:
            err = err if isinstance(err, dict) else {"message": str(err)}
            fut.set_exception(RpcError(err.get("code") if isinstance(err.get("code"), int) else FAILED,
                                       str(err.get("message") or "failed"), err.get("data")))
        else:
            fut.set_result(msg.get("result"))
