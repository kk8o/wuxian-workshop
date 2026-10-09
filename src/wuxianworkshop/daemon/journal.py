"""The daemon's log: the last CAPACITY entries in a ring with monotonic ids, each appended to logs/debug.log as it comes
(the format of agent/debuglog.py) and handed to the subscribers (the SSE connections) through their asyncio loop.

An entry is {"id", "t", "kind", "text", "addon", "job"}: kind is one of api.LOG_KINDS (the addon's debug kinds, the
companion's notes as INFO), addon the first "Interface/AddOns/<name>/" in the text, job the id of a RUN result. An EVENT
(what an addon sent with WoWBridge's Emit) also has "topic", "data" and "dropped", its addon being the one that sent it.
add() may be called from any thread; since() is what /api/logs returns.
"""
import json
import re
import threading
import time
from collections import deque

from ..agent.debuglog import debug_writer
from .api import parse_run

CAPACITY = 5000
SHOWN = 300                   # characters of an event's data in its log line (the entry keeps the data whole)
_ADDON = re.compile(r"Interface[/\\]AddOns[/\\]([^/\\:\s]+)")


def addon_of(text):
    m = _ADDON.search(text)
    return m[1] if m else None


def event_fields(text):
    """an addon's event (WoWBridge API.lua: {"a": addon, "t": topic, "d": data[, "x": dropped]}) as entry fields: addon,
    topic, data, dropped (events the addon's rate limit let go before this one) and text, the topic and the data in one
    line for the log; None when the text is not such JSON"""
    try:
        env = json.loads(text)
    except ValueError:
        return None
    if not isinstance(env, dict) or not isinstance(env.get("t"), str) or not isinstance(env.get("a"), str):
        return None
    data = env.get("d")
    shown = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    shown = shown if len(shown) <= SHOWN else shown[:SHOWN] + "…"
    dropped = env.get("x") if isinstance(env.get("x"), int) else 0
    return dict(addon=env["a"], topic=env["t"], data=data, dropped=dropped,
                text=f"{env['t']} {shown}" + (f" ({dropped} dropped before it)" if dropped else ""))


class Journal:
    def __init__(self, path=None, capacity=CAPACITY, clock=time.time):
        self.entries = deque(maxlen=capacity)
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
            self.entries.append(entry)
            subscribers = list(self.subscribers.values())
        if self.write is not None:
            try:
                self.write(kind, text)
            except OSError:
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
        with self.lock:
            entries = list(self.entries)
            next_id = self.next_id
        oldest = entries[0]["id"] if entries else next_id
        truncated = since < oldest and not (since == 0 and oldest == 1)
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
