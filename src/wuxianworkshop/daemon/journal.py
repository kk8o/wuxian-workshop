"""The daemon's log: the last CAPACITY entries in a ring with monotonic ids, each appended to logs/debug.log as it comes
(the format of agent/debuglog.py) and handed to the subscribers (the SSE connections) through their asyncio loop. The
EVENT entries have a ring of their own (EVENT_CAPACITY): an addon that emits a lot must not push the errors and the run
results out of the log.

An entry is {"id", "t", "kind", "text", "addon", "job"}: kind is one of api.LOG_KINDS (the addon's debug kinds, the
companion's notes as INFO), addon the first "Interface/AddOns/<name>/" in the text, job the id of a RUN result. An EVENT
(what an addon sent with WoWBridge's Emit or Request) also has "topic", "data" and "dropped", its addon being the one
that sent it; a request also "request" and "wait". Its JSON comes from the game, so it is read strictly: no NaN or
Infinity, no lone surrogates, no nesting deeper than Python takes, numbers in their ranges; an event that is not such
JSON stays an EVENT entry with its text only.
add() may be called from any thread; since() is what /api/logs returns.
"""
import heapq
import json
import re
import threading
import time
from collections import deque

from ..agent.debuglog import debug_writer
from .api import parse_run

CAPACITY = 5000
EVENT_CAPACITY = 3000
SHOWN = 300                   # characters of an event's data in its log line (the entry keeps the data whole)
NAME_MAX = 64                 # characters of an addon's name or a topic kept
REQUEST_ID = re.compile(r"^\d{1,5}\.\d{1,9}$")
_ADDON = re.compile(r"Interface[/\\]AddOns[/\\]([^/\\:\s]+)")


def addon_of(text):
    m = _ADDON.search(text)
    return m[1] if m else None


def _no_constant(name):
    raise ValueError(f"{name} is not JSON")


def strict_json(text):
    """JSON from the game as a value the API can send on, or ValueError: NaN / Infinity, lone surrogates and nesting
    past Python's recursion limit are refused"""
    try:
        value = json.loads(text, parse_constant=_no_constant)
        json.dumps(value, ensure_ascii=False).encode("utf-8")       # a lone surrogate fails here
    except RecursionError:
        raise ValueError("nested too deep") from None
    return value


def _whole(v, low, high):
    return v if isinstance(v, int) and not isinstance(v, bool) and low <= v <= high else None


def event_fields(text):
    """an addon's event (WoWBridge API.lua: {"a": addon, "t": topic, "d": data[, "x": dropped][, "r": request id,
    "w": seconds]}) as entry fields: addon, topic, data, dropped (events the addon's rate limit let go before this one),
    for a request its id and how long it waits (request, wait), and text, the topic and the data in one line for the
    log; None when the text is not such JSON"""
    try:
        env = strict_json(text)
    except ValueError:
        return None
    if not isinstance(env, dict) or not isinstance(env.get("t"), str) or not isinstance(env.get("a"), str):
        return None
    data = env.get("d")
    shown = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    shown = shown if len(shown) <= SHOWN else shown[:SHOWN] + "…"
    dropped = _whole(env.get("x"), 0, 2 ** 31) or 0
    addon, topic = env["a"][:NAME_MAX], env["t"][:NAME_MAX]
    fields = dict(addon=addon, topic=topic, data=data, dropped=dropped,
                  text=f"{topic} {shown}" + (f" ({dropped} dropped before it)" if dropped else ""))
    if isinstance(env.get("r"), str) and REQUEST_ID.match(env["r"]):   # a request (Request): answered with `respond`
        wait = _whole(env.get("w"), 1, 600)
        fields.update(request=env["r"], wait=wait)
        fields["text"] += f" (request {env['r']}" + (f", answer within {wait} s)" if wait else ")")
    return fields


class Journal:
    def __init__(self, path=None, capacity=CAPACITY, clock=time.time, event_capacity=EVENT_CAPACITY):
        self.entries = deque(maxlen=capacity)
        self.events = deque(maxlen=event_capacity)
        self.lost = {False: 0, True: 0}       # the latest id each ring (events: True) let go
        self.next_id = 1
        self.lock = threading.Lock()
        self.write = debug_writer(path) if path is not None else None
        self.subscribers = {}                 # key -> (asyncio loop, callback(entry))
        self.clock = clock

    def add(self, kind, text, addon=None, job=None):
        """one entry; returns it. A RUN result's job id is taken from the text; an EVENT's JSON gives the entry its
        addon, topic, data and a readable text"""
        if job is None and kind == "RUN":
            res = parse_run(text)
            job = res["job"] if res else None
        event = event_fields(text) if kind == "EVENT" else None
        if event:
            addon, text = event["addon"], event.pop("text")
        with self.lock:
            entry = dict(id=self.next_id, t=round(self.clock(), 3), kind=kind, text=text, addon=addon or addon_of(text), job=job)
            if event:
                entry.update(event)
            self.next_id += 1
            ring = self.events if kind == "EVENT" else self.entries
            if len(ring) == ring.maxlen:
                self.lost[kind == "EVENT"] = ring[0]["id"]
            ring.append(entry)
            subscribers = list(self.subscribers.values())
        if self.write is not None:
            try:
                self.write(kind, text)
            except (OSError, ValueError):     # a full disk, or text the file cannot take
                pass
        for loop, callback in subscribers:
            try:
                loop.call_soon_threadsafe(callback, entry)
            except RuntimeError:              # the loop is closed: the connection is gone anyway
                pass
        return entry

    def since(self, since=0, limit=200, kinds=None):
        """(entries with id >= since, at most limit, of these kinds; next = the id to ask for next time;
        truncated = entries before the oldest kept one were asked for)"""
        kinds = set(kinds) if kinds else None
        rings = [e for e in (False, True) if kinds is None or (("EVENT" in kinds) if e else bool(kinds - {"EVENT"}))]
        with self.lock:
            lists = [list(self.events if e else self.entries) for e in rings]
            lost = max([self.lost[e] for e in rings] or [0])
            next_id = self.next_id
        entries = lists[0] if len(lists) == 1 else list(heapq.merge(*lists, key=lambda e: e["id"]))
        truncated = bool(lost) and since <= lost
        out = []
        for e in entries:
            if e["id"] < since or (kinds and e["kind"] not in kinds):
                continue
            if len(out) >= limit:
                return out, out[-1]["id"] + 1, truncated      # more to come: resume after the last one returned
            out.append(e)
        return out, next_id, truncated

    def subscribe(self, loop, callback):
        """callback(entry) on that asyncio loop for every new entry; returns the function that ends it"""
        key = object()
        with self.lock:
            self.subscribers[key] = (loop, callback)

        def unsubscribe():
            with self.lock:
                self.subscribers.pop(key, None)
        return unsubscribe
