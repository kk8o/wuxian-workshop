"""Notes on an app's wakes: a WuxianKit 0.6 app's wake (its event app.<id> from the game) goes into the journal with
what the local extensions that annotate that app (their manifest's annotate: local/host.py Host.annotations) said of it.

A wake of an app that nobody annotates goes into the journal at once, as before (the caller journals it: hold() says
False). One that is annotated is held: each annotation's capability is called with the whole wake as one argument
(local_call, caller {"by": "workshop", "annotate": <app>}), all at once, each waited for its own timeout at most; then
the wake goes into the journal with data["notes"][<extension>] = the capability's result, or {"error", "reason"} when it
failed (reason timeout | down | failed | no_capability | bad_args | starting). An extension that does not run is started
by the call; the wake waits only for the timeout, the start goes on (the next wake is quicker).
The wakes of one app go into the journal in the order they came (a later one waits behind an earlier one); the apps do
not wait for each other. A note never costs a wake: a failure is a note's error, and when the daemon stops the wakes
still held go in with an error each.
"""
import asyncio
import json
import threading
from collections import deque

from .api import ApiError
from .journal import strict_json

KIT = "WuxianKit"
SKIP = ("app.reactions",)        # the workshop's own temporary app (the reactions): no notes
TIMEOUT = 3.0                    # seconds an annotation is waited for, unless it says
TIMEOUT_MAX = 10.0
NOTE_MAX = 64 * 1024             # bytes of one note's JSON: a bigger result is an error
REASONS = ("timeout", "down", "failed", "no_capability", "bad_args")
DOWN_CODES = ("off", "stopping", "stopped", "not_found")   # local_call's other refusals: the extension is not there


def wake_of(text):
    """(app, envelope) of WuxianKit's event app.<id> whose data is an object, from its journal text; None otherwise"""
    if '"app.' not in text or KIT not in text:         # most events: no parse
        return None
    try:
        env = strict_json(text)
    except ValueError:
        return None
    if not isinstance(env, dict) or env.get("a") != KIT or not isinstance(env.get("d"), dict):
        return None
    topic = env.get("t")
    if not isinstance(topic, str) or not topic.startswith("app.") or topic in SKIP or len(topic) < 5:
        return None
    return topic[4:], env


def plain(annotation):
    """an annotation as the host gives it, made safe: {ext, call, wake, timeout}; None when it cannot be one"""
    if not isinstance(annotation, dict):
        return None
    ext, call, wake = annotation.get("ext"), annotation.get("call"), annotation.get("wake") or "wake"
    if not isinstance(ext, str) or not ext or not isinstance(call, str) or not call or not isinstance(wake, str):
        return None
    try:
        timeout = float(annotation.get("timeout") or TIMEOUT)
    except (TypeError, ValueError):
        timeout = TIMEOUT
    timeout = TIMEOUT if timeout != timeout or timeout <= 0 else min(timeout, TIMEOUT_MAX)
    return dict(ext=ext, call=call, wake=wake, timeout=timeout)


def error(text, reason):
    return {"error": text, "reason": reason}


def _quiet(task):
    """a call left behind after its timeout: its result or error is nobody's"""
    if not task.cancelled():
        task.exception()


class Notes:
    """the held wakes. annotations(app) -> [{ext, call, wake, timeout}] (any thread); call(cap, args, timeout_ms,
    caller) -> {result} or ApiError (a coroutine: Service.local_call); deliver(text) journals a wake and tells who hears
    WuxianKit's events; soon(coro) puts a coroutine on the server's loop (None when there is none); state(ext) -> the
    extension's state (starting, running, ...; any thread); note(text) an INFO line"""

    def __init__(self, annotations, call, deliver, soon, state=None, note=None):
        self.annotations, self.call, self.deliver, self.soon = annotations, call, deliver, soon
        self.state, self.note = state, note or (lambda text: None)
        self.lock = threading.Lock()      # the queues; a wake is delivered under it (the order of an app's wakes)
        self.queues = {}                  # app -> deque of held wakes ({env, anns, done}); there while its runner runs
        self.failed = {}                  # ext -> the reason of its last note when it failed (said once in a row)
        self.closed = False

    def hold(self, text):
        """(the worker thread) whether this event is a wake held for its notes: then it goes into the journal later
        (deliver); False: the caller journals it now"""
        if self.closed:
            return False
        found = wake_of(text)
        if found is None:
            return False
        app, env = found
        with self.lock:
            queue = self.queues.get(app)
            if queue is not None:         # an earlier wake of this app is still held: this one waits behind it
                queue.append(dict(env=env, anns=None, done=False))
                return True
        anns = self.annotations_of(app)
        if not anns:
            return False
        with self.lock:
            if self.closed:
                return False
            self.queues[app] = deque([dict(env=env, anns=anns, done=False)])
            if self.soon(self.run(app)) is None:     # no loop (a Service without a server, or it stops)
                del self.queues[app]
                return False
        return True

    def annotations_of(self, app):
        """the app's annotations now, made safe; [] when the host has none (or failed to say)"""
        try:
            anns = self.annotations(app) or []
        except Exception as e:
            self.note(f"notes of app {app}: the annotations could not be read: {e!r}")
            return []
        anns = [a for a in map(plain, anns) if a is not None]
        seen, out = set(), []
        for a in anns:                    # one note per extension
            if a["ext"] not in seen:
                seen.add(a["ext"])
                out.append(a)
        return out

    async def run(self, app):
        """(the loop) the held wakes of one app, in order, until none is left"""
        while True:
            with self.lock:
                queue = self.queues.get(app)
                if not queue:
                    self.queues.pop(app, None)
                    return
                item = queue[0]
            anns = item["anns"] if item["anns"] is not None else await asyncio.to_thread(self.annotations_of, app)
            try:
                notes = await self.gather(app, item["env"]["d"], anns) if anns else None
            except Exception as e:        # a bug: the wake still goes in
                notes = {a["ext"]: error(f"the notes failed: {e!r}", "failed") for a in anns}
            with self.lock:
                if item["done"] or self.queues.get(app) is not queue:   # close() delivered it
                    return
                queue.popleft()
                self.put(item, notes)

    def put(self, item, notes):
        """(under the lock) one wake into the journal, with its notes"""
        item["done"] = True
        env = item["env"]
        if notes is not None:
            env["d"]["notes"] = notes
        try:
            self.deliver(json.dumps(env, ensure_ascii=False))
        except Exception as e:            # the journal or a listener failed: the others go on
            self.note(f"a wake of {env.get('t')} could not be journaled: {e!r}")

    async def gather(self, app, data, anns):
        """{ext: note} of every annotation, called at once"""
        wake = {k: v for k, v in data.items() if k != "notes"}
        results = await asyncio.gather(*(self.one(app, a, wake) for a in anns))
        return {a["ext"]: r for a, r in zip(anns, results)}

    async def one(self, app, a, wake):
        """one annotation's note: the result, or {error, reason}; waits a["timeout"] at most, leaving the call (and a
        start of the extension) to go on"""
        caller = {"by": "workshop", "annotate": app}
        task = asyncio.ensure_future(self.call(a["call"], {a["wake"]: wake}, int(a["timeout"] * 1000), caller))
        done, _ = await asyncio.wait({task}, timeout=a["timeout"])    # not cancelled when it takes longer
        if not done:
            task.add_done_callback(_quiet)
            if await self.starting(a["ext"]):
                return self.failure(app, a, error(f"{a['ext']} is still starting (it goes on: the next wake is "
                                                  f"quicker)", "starting"))
            return self.failure(app, a, error(f"{a['call']} did not answer within {a['timeout']:g} s", "timeout"))
        try:
            answer = task.result()
        except ApiError as e:
            reason = e.code if e.code in REASONS else "down" if e.code in DOWN_CODES else "failed"
            first = str(e.message or e.code).splitlines()[0][:300] if (e.message or e.code) else reason
            return self.failure(app, a, error(first, reason))
        except Exception as e:
            return self.failure(app, a, error(f"{a['call']}: {e!r}"[:300], "failed"))
        result = (answer or {}).get("result") if isinstance(answer, dict) else answer
        try:
            size = len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError):   # not JSON text (a lone surrogate, NaN): the journal would not read it
            return self.failure(app, a, error(f"{a['call']} answered what is not JSON text", "failed"))
        if size > NOTE_MAX:
            return self.failure(app, a, error(f"{a['call']} answered {size:,} bytes (a note is at most {NOTE_MAX:,})",
                                              "failed"))
        self.failed.pop(a["ext"], None)
        return result

    async def starting(self, ext):
        """whether the extension is still starting (a timeout that is its start, not its call)"""
        if self.state is None:
            return False
        try:
            return await asyncio.to_thread(self.state, ext) == "starting"
        except Exception:
            return False

    def failure(self, app, a, note):
        """a failed note, said once while the extension keeps failing the same way"""
        if self.failed.get(a["ext"]) != note["reason"]:
            self.failed[a["ext"]] = note["reason"]
            self.note(f"notes of app {app}: {a['ext']} ({a['call']}): {note['reason']}: {note['error']}")
        return note

    def close(self):
        """(the daemon stops) the wakes still held go into the journal now, in order, each note an error; later events
        are not held"""
        with self.lock:
            self.closed = True
            queues, self.queues = self.queues, {}
            for app, queue in queues.items():
                anns = None
                for item in queue:
                    if item["done"]:
                        continue
                    anns = item["anns"] if item["anns"] is not None else anns
                    if anns is None:
                        anns = self.annotations_of(app)
                    notes = {a["ext"]: error("the daemon stopped before it answered", "down") for a in anns}
                    self.put(item, notes or None)
