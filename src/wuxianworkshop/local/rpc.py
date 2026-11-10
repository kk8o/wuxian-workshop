r"""The wire between the workshop and a local extension: one stdio pipe, JSON-RPC 2.0, UTF-8, one object per line. No
process here (process.py has the pipe and its threads): the lines (frame, parse), the workshop's requests and the Futures
waiting for their answers (Wire, which process.Proc is), the protocol's numbers and the errors of a call.

Protocol 1. The workshop asks (requests):

    initialize {protocol: {min, max}, workshop, id, options}   -> {protocol: <one of min..max>, capabilities, sources,
                                                                  options}; INIT_TIMEOUT seconds (a model may load)
    call {name, args, caller, deadline_ms?}                    -> the capability's result. caller: {by: "agent" |
                                                                  "extension" | "workshop" | "player", id?, app?,
                                                                  chain?}; deadline_ms: how long the workshop still waits
    ping {}                                                    -> {} within PING_TIMEOUT seconds (asked after a call
                                                                  timed out: one that does not answer is hung)
    shutdown {}                                                -> {} and it exits

and tells (notifications): options {options} when the player changed them (all of them); cancel {id}: the workshop no
longer waits for request id (it timed out, or its caller went away), an answer to it is dropped. The extension tells
event {source, data} and asks call {cap, args, chain?}; what the workshop does with them is the host's (host.py:
on_message, extension_event, extension_call).

An error answer is {code, message, data: {reason}}. code: BAD_ARGS (-32602), NO_METHOD (-32601, no such capability),
FAILED (1), BUSY (2: the extension has too much to do, it may refuse) or CANCELLED (3). reason is one of REASONS, the
one set of words for why a call did not answer (an ApiError's code, the notes of a wake and the errors of a reaction use
them too); error(reason, message) makes such an answer, call_error the ApiError of a call that failed.
Standard library only.
"""
import json
import threading
from concurrent.futures import CancelledError, Future

from ..daemon.api import ApiError
from . import manifest as M

INIT_TIMEOUT = 180.0          # seconds initialize may take (an extension that loads a model first)
PING_TIMEOUT = 5.0            # seconds an extension has to answer ping
PROTOCOL = {"min": 1, "max": 1}   # the protocol versions this workshop speaks (initialize says so)
LINE_LIMIT = 8 * 1024 * 1024  # bytes of one line of the protocol at most (a longer one from the extension is dropped)

# JSON-RPC error codes of a call, and the reason each stands for
BAD_ARGS, NO_METHOD, FAILED, BUSY, CANCELLED = -32602, -32601, 1, 2, 3
REASONS = ("timeout", "down", "starting", "failed", "bad_args", "no_capability", "busy", "paused", "cancelled",
           "not_allowed", "needs_approval", "loop")
CODES = {"bad_args": BAD_ARGS, "no_capability": NO_METHOD, "busy": BUSY, "cancelled": CANCELLED}   # else FAILED
STATUS = {"bad_args": 400, "no_capability": 404, "busy": 429, "cancelled": 409, "failed": 422}     # of an ApiError


# --- errors ----------------------------------------------------------------------------------------------------------

class RpcError(Exception):
    """an error answer of the extension: {code, message, data?}; reason: the word of REASONS its code stands for"""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data

    @property
    def reason(self):
        return next((r for r, c in CODES.items() if c == self.code), "failed")


class Busy(Exception):
    """the pipe takes no line now: too many wait to be written, or the extension has not read its input for a while"""


class Down(ApiError):
    """the extension could not be started (or initialized): why, with the end of its output; problems as check gives
    them"""

    def __init__(self, message, problems=None):
        super().__init__(503, "down", message)
        self.problems = problems or [M.problem("error", "start", message)]


def error(reason, message):
    """an error answer {code, message, data: {reason}}; a reason that is none of REASONS is "failed" """
    reason = reason if reason in REASONS else "failed"
    return dict(code=CODES.get(reason, FAILED), message=str(message), data=dict(reason=reason))


def no_json_error(e):
    return error("failed", f"the result is no JSON ({e})")


def error_of(e):
    """the error answer of an exception: an ApiError's code is its reason"""
    if isinstance(e, ApiError):
        return error(e.code, e.message)
    if isinstance(e, RpcError):
        return error(e.reason, e.message)
    if isinstance(e, CancelledError):
        return error("cancelled", "cancelled")
    return error("failed", f"{type(e).__name__}: {e}")


def busy_error(eid, why):
    return ApiError(STATUS["busy"], "busy", f"{eid} is busy: {why}")


def cancelled_error(eid):
    return ApiError(STATUS["cancelled"], "cancelled", f"{eid}: the call was cancelled")


def call_error(e, cap):
    """the ApiError of a call that failed: an error answer of the extension by its code (bad_args, no_capability, busy,
    cancelled, else failed), an ApiError as it is"""
    if isinstance(e, RpcError):
        return ApiError(STATUS[e.reason], e.reason, f"{cap}: {e.message}")
    if isinstance(e, ApiError):
        return e
    if isinstance(e, CancelledError):
        return ApiError(STATUS["cancelled"], "cancelled", f"{cap}: the call was cancelled")
    return ApiError(422, "failed", f"{cap}: {e}")


def protocol_problem(answer):
    """why the protocol an extension answered initialize with cannot be spoken, or None"""
    got = answer.get("protocol") if isinstance(answer, dict) else None
    if isinstance(got, bool) or not isinstance(got, int):
        return None                                   # no version at all: the description's check says so
    if PROTOCOL["min"] <= got <= PROTOCOL["max"]:
        return None
    spoken = str(PROTOCOL["min"]) if PROTOCOL["min"] == PROTOCOL["max"] else f"{PROTOCOL['min']} to {PROTOCOL['max']}"
    return (f"it speaks protocol {got}, this workshop speaks {spoken}: "
            + ("update the workshop" if got > PROTOCOL["max"] else "update the extension (its SDK)"))


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
    (process.Proc) gives id (the extension's), send(msg) (the line written or queued; OSError or ValueError when the
    pipe is closed, Busy when it takes no line now), ended (an Event: the other end is gone) and gone_error() (what a
    request gets then), and hands the answers it reads to answer(msg)"""

    def __init__(self):
        self.lock = threading.Lock()
        self.pending, self.next_id = {}, 0

    def request(self, method, params):
        """a request: a Future of its result (RpcError for an error answer; ApiError down when the process ended, busy
        when the pipe takes no line, cancelled after cancel()). The Future's rid is the request's id. A Future that
        is cancelled (Future.cancel(): asyncio does that to a wrapped one) counts as cancel()"""
        fut = Future()
        fut.rid = None
        with self.lock:
            if self.ended.is_set():
                fut.set_exception(self.gone_error())
                return fut
            self.next_id += 1
            rid = fut.rid = self.next_id
            self.pending[rid] = fut
        fut.add_done_callback(lambda f: f.cancelled() and self.cancel(f))
        try:
            self.send(dict(jsonrpc="2.0", id=rid, method=method, params=params))
        except (OSError, ValueError) as e:
            self.fail(rid, ApiError(503, "down", f"{self.id}: its pipe is closed ({e})"))
        except Busy as e:
            self.fail(rid, busy_error(self.id, e))
        except TypeError as e:                         # params that are no JSON: nothing was written
            self.fail(rid, ApiError(STATUS["bad_args"], "bad_args", f"{self.id}: what it is asked is no JSON ({e})"))
        return fut

    def forget(self, rid):
        """the Future waiting for request rid (no longer waiting), or None"""
        if isinstance(rid, bool) or not isinstance(rid, int):      # the workshop's ids are whole numbers: a list or an
            return None                                            # object is no key, true and 1.0 would pass for 1
        with self.lock:
            return self.pending.pop(rid, None)

    def fail(self, rid, error):
        """request rid ends with that error, unless it is answered already; True when it did"""
        fut = self.forget(rid)
        if fut is None or fut.done():
            return False
        try:
            fut.set_exception(error)
        except Exception:                              # done by another thread in between
            return False
        return True

    def cancel(self, fut):
        """the caller of a request waits no longer: the other end is told cancel {id}, an answer to it is dropped, and
        the Future ends with ApiError cancelled unless it is done. True when the request was still waiting"""
        rid = getattr(fut, "rid", None)
        waiting = rid is not None and self.forget(rid) is not None
        if waiting:
            self.notify("cancel", dict(id=rid))
        if not fut.done():
            try:
                fut.set_exception(cancelled_error(self.id))
            except Exception:                          # done by another thread in between
                pass
        return waiting

    def notify(self, method, params):
        """a notification; False when the pipe did not take it"""
        try:
            self.send(dict(jsonrpc="2.0", method=method, params=params))
            return True
        except (OSError, ValueError, TypeError, Busy):
            return False

    def respond(self, rid, result=None, error=None):
        """the answer to a request of the other end; False when the pipe did not take it"""
        msg = dict(jsonrpc="2.0", id=rid)
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        try:
            self.send(msg)
            return True
        except TypeError as e:                         # a result that is no JSON: the other end still gets an answer
            return error is None and self.respond(rid, error=no_json_error(e))
        except (OSError, ValueError, Busy):
            return False

    def answer(self, msg):
        """(the reader thread) an answer of the extension: the Future of its request gets the result, or RpcError.
        False when no request waits for it (it was cancelled, or the id is none the workshop gave)"""
        fut = self.forget(msg.get("id"))
        if fut is None or fut.done():
            return False
        err = msg.get("error")
        try:
            if err is not None:
                err = err if isinstance(err, dict) else {"message": str(err)}
                code = err.get("code")
                fut.set_exception(RpcError(code if isinstance(code, int) and not isinstance(code, bool) else FAILED,
                                           str(err.get("message") or "failed"), err.get("data")))
            else:
                fut.set_result(msg.get("result"))
        except Exception:                              # done by another thread in between (cancelled)
            return False
        return True
