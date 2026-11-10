"""The lane of an app's wakes: a WuxianKit app's wake (its event app.<id> from the game) goes into the journal with what
the local extensions that add to that app said of it (their manifest's adds.<app>.wake: local/adds.py, Host.adds).

A wake of an app nobody adds to goes into the journal at once (the caller journals it: hold() says False). One that is
added to is held: each extension's wake capability is called with {"wake": <the wake's data, without notes>} (local_call,
caller {"by": "workshop", "app": <app>, "chain": ["wake:<app>"]}), all at once, each for the timeout its manifest says
(the call gets it as its deadline and is cancelled at its end); then the wake goes into the journal with
data["notes"][<extension>] = the capability's result, or {"error": <one line>, "reason": <a word of REASONS>} when it
failed. An extension that does not run is started by the call; the wake waits only for the timeout, the start goes on
(the next wake is quicker: reason starting).
The lane: while a wake of an app is held, that app's later wakes and its kit.app events (it stopped, it runs again) wait
behind it and go into the journal in the order they came; other apps and other events do not wait. At most LANE_MAX wait
in one lane: one more, and the oldest wake goes on without its notes (each {"error", "reason": "busy"}).
The breaker: an extension that failed (or timed out) TRIP times in a row for one app is not called for that app for
PAUSE seconds (its note is {"error", "reason": "paused"}); then one call is let through: its success ends it, its
failure pauses it again.
Controls: a control entry of a wake ({source: "control", cmd}) whose command the app's extension bound to a capability
(the manifest's controls[].call: Host.control_call) has that capability called first (args {}, caller {"by": "player",
"app"}), in this lane, then the wake goes on (the entry stays in it: the agent sees what the player pressed).
A note never costs a wake: a failure is a note's error, and when the daemon stops what is still held goes in, in order.
"""
import asyncio
import json
import threading
import time
from collections import deque

from .api import ApiError
from .journal import strict_json

KIT = "WuxianKit"
SKIP = ("reactions",)            # the workshop's own temporary app (the reactions): no lane
EVENT = "kit.app"                # WuxianKit's event of an app's run: {app, on, why?}
TIMEOUT = 3.0                    # seconds a wake capability is waited for, unless its manifest says
TIMEOUT_MAX = 10.0
GRACE = 0.25                     # seconds more the lane waits than the call may take (the call's own timeout comes first)
NOTE_MAX = 64 * 1024             # bytes of one note's JSON: a bigger result is an error
LANE_MAX = 32                    # events that wait in one app's lane
TRIP = 3                         # failures in a row (one extension, one app) that pause it
PAUSE = 60.0                     # seconds it is not called then
CONTROL_WAIT = 10.0              # seconds a wake waits for the capabilities its control entries are bound to
REASONS = ("timeout", "down", "starting", "failed", "bad_args", "no_capability", "busy", "paused", "cancelled",
           "not_allowed", "needs_approval", "loop")      # the words of a failure (contract §7): ApiError codes too
UNCOUNTED = ("starting", "needs_approval")               # failures that are not the extension's: the breaker skips them


def event_of(text):
    """("wake", app, envelope) of WuxianKit's event app.<id>, ("app", app, envelope) of its kit.app event of an app,
    from the journal text (the data an object); None for everything else"""
    if KIT not in text or ('"app.' not in text and '"kit.app"' not in text):      # most events: no parse
        return None
    try:
        env = strict_json(text)
    except ValueError:
        return None
    if not isinstance(env, dict) or env.get("a") != KIT or not isinstance(env.get("d"), dict):
        return None
    topic = env.get("t")
    if topic == EVENT:
        app = env["d"].get("app")
        return ("app", app, env) if isinstance(app, str) and app and app not in SKIP else None
    if not isinstance(topic, str) or not topic.startswith("app.") or len(topic) < 5 or topic[4:] in SKIP:
        return None
    return "wake", topic[4:], env


def plain(add):
    """what an extension adds to an app, as the host gives it ({ext, wake: {call, timeout} | None, ...}), made safe:
    {ext, call, timeout}; None when it has no wake"""
    if not isinstance(add, dict) or not isinstance(add.get("wake"), dict):
        return None
    ext, call = add.get("ext"), add["wake"].get("call")
    if not isinstance(ext, str) or not ext or not isinstance(call, str) or not call:
        return None
    try:
        timeout = float(add["wake"].get("timeout") or TIMEOUT)
    except (TypeError, ValueError):
        timeout = TIMEOUT
    timeout = TIMEOUT if timeout != timeout or timeout <= 0 else min(timeout, TIMEOUT_MAX)
    return dict(ext=ext, call=call, timeout=timeout)


def error(text, reason):
    return {"error": text, "reason": reason}


def first_line(e):
    """one line of an ApiError"""
    text = str(e.message or e.code or "")
    return text.splitlines()[0][:300] if text.strip() else str(e.code)


def _quiet(task):
    """a call nobody waits for any more: its result or error is nobody's"""
    if not task.cancelled():
        task.exception()


class Notes:
    """the lanes. adds(app) -> [{ext, wake: {call, timeout} | None, ...}] (any thread: Host.adds); call(cap, args,
    timeout_ms, caller) -> {result} or ApiError (a coroutine: Service.local_call; cancelling it tells the extension);
    deliver(text) journals an event and tells who hears WuxianKit's events; soon(coro) puts a coroutine on the server's
    loop (None when there is none); control(app, cmd) -> {ext, call} or None (any thread: Host.control_call); state(ext)
    -> the extension's state (starting, running, ...; any thread); note(text) an INFO line; now() seconds, monotonic"""

    def __init__(self, adds, call, deliver, soon, control=None, state=None, note=None, now=time.monotonic):
        self.adds, self.call, self.deliver, self.soon = adds, call, deliver, soon
        self.control, self.state, self.note, self.now = control, state, note or (lambda text: None), now
        self.lock = threading.Lock()      # the queues; an event is delivered under it (the order of a lane)
        self.queues = {}                  # app -> deque of what its lane holds (items); there while its runner runs
        self.failed = {}                  # ext -> the reason of its last note when it failed (said once in a row)
        self.breakers = {}                # (ext, app) -> [failures in a row, paused until (now()) or None]
        self.full = set()                 # apps whose lane overflowed (said once until it empties)
        self.closed = False

    # --- what comes in (the worker thread) ---------------------------------------------------------------------------

    def hold(self, text):
        """(the worker thread) whether this event waits in a lane: then it goes into the journal later (deliver);
        False: the caller journals it now"""
        if self.closed:
            return False
        found = event_of(text)
        if found is None:
            return False
        kind, app, env = found
        item = dict(kind=kind, env=env, text=text, wakes=None, controls=None, controlled=False, task=None, done=False)
        with self.lock:
            queue = self.queues.get(app)
            if queue is not None:         # an earlier wake of this app is still held: this one waits behind it
                queue.append(item)
                self.overflow(app, queue)
                return True
        if kind != "wake":                # the app's kit.app with nothing held before it
            return False
        wakes, controls = self.wakes_of(app), self.controls_of(app, env["d"])
        if not wakes and not controls:
            return False
        item.update(wakes=wakes, controls=controls)
        with self.lock:
            if self.closed:
                return False
            self.queues[app] = deque([item])
            if self.soon(self.run(app)) is None:     # no loop (a Service without a server, or it stops)
                del self.queues[app]
                return False
        return True

    def overflow(self, app, queue):
        """(under the lock) more than LANE_MAX wait: the oldest wake goes on without its notes (and what waited before
        it), its calls are let go"""
        while len(queue) > LANE_MAX:
            item = queue.popleft()
            if item["kind"] != "wake":
                self.put(item, None)
                continue
            wakes = item["wakes"] if item["wakes"] is not None else self.wakes_of(app)
            text = f"more than {LANE_MAX} events of app {app} waited for notes: this wake went on without them"
            self.put(item, {w["ext"]: error(text, "busy") for w in wakes} or None)
            self.soon(self.let_go(app, item))
            if app not in self.full:
                self.full.add(app)
                self.note(f"notes of app {app}: more than {LANE_MAX} events waited: the oldest wakes go on without "
                          f"their notes (busy)")

    def wakes_of(self, app):
        """the wake capabilities added to the app now, made safe: [{ext, call, timeout}], one per extension; [] when
        the host has none (or failed to say)"""
        try:
            adds = self.adds(app) or []
        except Exception as e:
            self.note(f"notes of app {app}: what is added to it could not be read: {e!r}")
            return []
        seen, out = set(), []
        for w in map(plain, adds):
            if w is not None and w["ext"] not in seen:
                seen.add(w["ext"])
                out.append(w)
        return out

    def controls_of(self, app, data):
        """the wake's control entries whose command is bound to a capability: [{cmd, ext, call}], in the items' order"""
        if self.control is None or not isinstance(data.get("items"), list):
            return []
        out = []
        for entry in data["items"]:
            if not isinstance(entry, dict) or entry.get("source") != "control" or not isinstance(entry.get("cmd"), str):
                continue
            try:
                bound = self.control(app, entry["cmd"])
            except Exception as e:
                self.note(f"app {app}: what its control {entry['cmd']} is bound to could not be read: {e!r}")
                continue
            if isinstance(bound, dict) and isinstance(bound.get("call"), str) and bound["call"]:
                out.append(dict(cmd=entry["cmd"], ext=bound.get("ext"), call=bound["call"]))
        return out

    def resolve(self, app, data):
        return self.wakes_of(app), self.controls_of(app, data)

    # --- the lane (the loop) -----------------------------------------------------------------------------------------

    async def run(self, app):
        """(the loop) what one app's lane holds, in order, until nothing is left"""
        while True:
            with self.lock:
                queue = self.queues.get(app)
                while queue and queue[0]["kind"] != "wake":      # nothing is held before them any more
                    self.put(queue.popleft(), None)
                if not queue:
                    self.queues.pop(app, None)
                    self.full.discard(app)
                    return
                item = queue[0]
                item["task"] = task = asyncio.ensure_future(self.work(app, item))
            await asyncio.wait({task})
            if task.cancelled():          # let go (overflow), or the loop ends
                notes = {w["ext"]: error("the notes were not waited for", "cancelled") for w in item["wakes"] or []}
            else:
                try:
                    notes = task.result()
                except Exception as e:    # a bug: the wake still goes in
                    notes = {w["ext"]: error(f"the notes failed: {e!r}"[:300], "failed") for w in item["wakes"] or []}
            with self.lock:
                if self.queues.get(app) is not queue:            # close() delivered everything
                    return
                if item["done"]:          # overflow delivered it: the next one
                    continue
                queue.popleft()
                self.put(item, notes or None)

    def put(self, item, notes):
        """(under the lock) one event into the journal: a wake with its notes, anything else as it came"""
        item["done"] = True
        env = item["env"]
        try:
            if notes is None:
                self.deliver(item["text"])
            else:
                env["d"]["notes"] = notes
                self.deliver(json.dumps(env, ensure_ascii=False))
        except Exception as e:            # the journal or a listener failed: the others go on
            self.note(f"an event {env.get('t')} could not be journaled: {e!r}")

    async def work(self, app, item):
        """(the loop) what one held wake waits for: the capabilities its control entries are bound to, then its notes:
        {ext: note}"""
        data = item["env"]["d"]
        if item["wakes"] is None:         # it waited behind another: what is added to the app is read now
            wakes, controls = await asyncio.to_thread(self.resolve, app, data)
            item.update(wakes=wakes, controls=controls)
        if item["controls"] and not item["controlled"]:
            bound = asyncio.ensure_future(self.controls(app, item))
            bound.add_done_callback(_quiet)
            await asyncio.wait({bound}, timeout=CONTROL_WAIT)    # not cancelled with the wake, or when they take longer
        if not item["wakes"]:
            return None
        wake = {k: v for k, v in data.items() if k != "notes"}
        results = await asyncio.gather(*(self.one(app, w, wake) for w in item["wakes"]))
        return {w["ext"]: r for w, r in zip(item["wakes"], results)}

    async def controls(self, app, item):
        """(the loop) the capabilities bound to a wake's control entries, called in order, once; a failure is journaled
        (the entry is in the wake: the agent knows what the player pressed)"""
        if item["controlled"]:
            return
        item["controlled"] = True
        for c in item["controls"] or []:
            try:
                await self.call(c["call"], {}, None, {"by": "player", "app": app})
            except ApiError as e:
                self.note(f"app {app}: its control {c['cmd']} ({c['call']}) failed: {e.code}: {first_line(e)}")
            except Exception as e:
                self.note(f"app {app}: its control {c['cmd']} ({c['call']}) failed: {e!r}"[:400])

    async def let_go(self, app, item):
        """(the loop) a wake that went on without its notes: its calls are not waited for; what its control entries are
        bound to is still called"""
        task = item["task"]
        if task is not None and not task.done():
            task.cancel()
        if item["controlled"]:
            return
        if item["controls"] is None:
            item["controls"] = await asyncio.to_thread(self.controls_of, app, item["env"]["d"])
        await self.controls(app, item)

    async def one(self, app, w, wake):
        """one extension's note: its wake capability's result, or {error, reason}. Waits w["timeout"] (the call's own
        timeout) and a little; then the call is cancelled, a start of the extension goes on"""
        ext, cap, timeout = w["ext"], w["call"], w["timeout"]
        if self.paused(ext, app):
            return error(f"{ext} failed {TRIP} times in a row for {app}: it is not called for {PAUSE:.0f} s", "paused")
        caller = {"by": "workshop", "app": app, "chain": [f"wake:{app}"]}
        task = asyncio.ensure_future(self.call(cap, {"wake": wake}, int(timeout * 1000), caller))
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout + GRACE)
        except asyncio.CancelledError:    # the wake went on without its notes
            task.add_done_callback(_quiet)
            task.cancel()
            raise
        if not done:
            task.add_done_callback(_quiet)
            task.cancel()
            if await self.starting(ext):
                return self.failure(app, w, error(f"{ext} is still starting (it goes on: the next wake is quicker)",
                                                  "starting"))
            return self.failure(app, w, error(f"{cap} did not answer within {timeout:g} s", "timeout"))
        if task.cancelled():
            return self.failure(app, w, error(f"{cap}: the call was cancelled", "cancelled"))
        try:
            answer = task.result()
        except ApiError as e:
            return self.failure(app, w, error(first_line(e), e.code if e.code in REASONS else "failed"))
        except Exception as e:
            return self.failure(app, w, error(f"{cap}: {e!r}"[:300], "failed"))
        result = (answer or {}).get("result") if isinstance(answer, dict) else answer
        try:
            size = len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError):   # not JSON text (a lone surrogate, NaN): the journal would not read it
            return self.failure(app, w, error(f"{cap} answered what is not JSON text", "failed"))
        if size > NOTE_MAX:
            return self.failure(app, w, error(f"{cap} answered {size:,} bytes (a note is at most {NOTE_MAX:,})",
                                              "failed"))
        self.failed.pop(ext, None)
        self.breakers.pop((ext, app), None)
        return result

    async def starting(self, ext):
        """whether the extension is still starting (a timeout that is its start, not its call)"""
        if self.state is None:
            return False
        try:
            return await asyncio.to_thread(self.state, ext) == "starting"
        except Exception:
            return False

    # --- failures and the breaker ------------------------------------------------------------------------------------

    def paused(self, ext, app):
        """whether the extension is not called for this app now; a pause that is over lets this call through (the
        probe: one more failure pauses it again)"""
        breaker = self.breakers.get((ext, app))
        if breaker is None or breaker[1] is None:
            return False
        if self.now() < breaker[1]:
            return True
        breaker[1] = None
        return False

    def failure(self, app, w, note):
        """a failed note: counted for the breaker, said once while the extension keeps failing the same way"""
        ext = w["ext"]
        if self.failed.get(ext) != note["reason"]:
            self.failed[ext] = note["reason"]
            self.note(f"notes of app {app}: {ext} ({w['call']}): {note['reason']}: {note['error']}")
        if note["reason"] not in UNCOUNTED:
            breaker = self.breakers.setdefault((ext, app), [0, None])
            breaker[0] = min(breaker[0] + 1, TRIP)
            if breaker[0] >= TRIP:
                breaker[1] = self.now() + PAUSE
                self.note(f"notes of app {app}: {ext} failed {TRIP} times in a row: it is not called for {PAUSE:.0f} s")
        return note

    def forgive(self, ext):
        """(any thread) the extension was loaded, turned on or approved: its pauses end, its next failure is said"""
        for key in [k for k in list(self.breakers) if k[0] == ext]:
            self.breakers.pop(key, None)
        self.failed.pop(ext, None)

    def close(self):
        """(the daemon stops) what the lanes still hold goes into the journal now, in order, each note an error; later
        events are not held"""
        with self.lock:
            self.closed = True
            queues, self.queues = self.queues, {}
            for app, queue in queues.items():
                wakes = None
                for item in queue:
                    if item["done"]:
                        continue
                    if item["kind"] != "wake":
                        self.put(item, None)
                        continue
                    wakes = item["wakes"] if item["wakes"] is not None else wakes
                    if wakes is None:
                        wakes = self.wakes_of(app)
                    self.put(item, {w["ext"]: error("the daemon stopped before it answered", "down")
                                    for w in wakes} or None)
