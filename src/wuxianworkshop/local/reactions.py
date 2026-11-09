"""A STAND-IN for the reactions engine (work package W2 writes the real one; at the merge W2's file wins). It has the
interface the daemon wires (contract3.md section 4, with W2's additions: local_sources, refresh, drain, ReactionError)
and the least behaviour that makes the wiring testable: a reaction hears one entry "<source>:<what>", an item of that
source whose kind is <what> (or <source>.<what>, or starts with <what>.) calls its capability with its args.

    Reactions(call, hear, kind_of, now=time.time, note=None, local_sources=None)
        call(cap_id, args, caller) -> result (or awaitable); hear(entries) (or awaitable): the game sources to hear;
        kind_of(cap_id) -> "see" | "keep" | "point" | "say" | "do" | None; local_sources() -> the local extensions' ids
    await set(spec) -> dict; list() -> [dict]; await clear(id=None, owner=None) -> int (neither: all)
    await item(item); await app_stopped(app); await refresh(); await drain()
"""
import asyncio
import inspect
import time

MOST = 32
ALLOWED = ("see", "keep", "point")


class ReactionError(ValueError):
    """a spec that cannot be used: the message is for the agent"""


async def _maybe(value):
    return await value if inspect.isawaitable(value) else value


class Reactions:
    def __init__(self, call, hear, kind_of, now=time.time, note=None, local_sources=None):
        self.call, self.hear, self.kind_of, self.now = call, hear, kind_of, now
        self.note = note or (lambda text: None)
        self.local_sources = local_sources or (lambda: set())
        self.items, self.n, self.heard, self.tasks = {}, 0, None, set()

    def game_entries(self):
        local = set(self.local_sources())
        return sorted({r["hear"] for r in self.items.values() if r["hear"].partition(":")[0] not in local})

    async def refresh(self):
        entries = self.game_entries()
        if entries != self.heard:
            self.heard = entries
            try:
                await _maybe(self.hear(entries))
            except Exception as e:
                for r in self.items.values():
                    if r["hear"] in entries:
                        r["error"] = f"not heard: {e}"

    async def set(self, spec):
        if not isinstance(spec, dict):
            raise ReactionError("a reaction is an object {hear, call, args?, ...}")
        hear, cap = spec.get("hear"), spec.get("call")
        if not isinstance(hear, str) or ":" not in hear:
            raise ReactionError("hear: one entry <source>:<what>, such as game:combat")
        if not isinstance(cap, str) or self.kind_of(cap) not in ALLOWED:
            raise ReactionError(f"call: a see, keep or point capability ({cap!r} is not)")
        rid = spec.get("id")
        if rid is None:
            self.n += 1
            rid = f"r{self.n}"
        if rid not in self.items and len(self.items) >= MOST:
            raise ReactionError(f"at most {MOST} reactions")
        r = dict(id=rid, owner=spec.get("owner"), hear=hear, when=spec.get("when") or {}, call=cap,
                 args=spec.get("args") or {}, item=spec.get("item"), cooldown=spec.get("cooldown", 10),
                 note=spec.get("note"), fired=0, last=None, error=None)
        self.items[rid] = r
        await self.refresh()
        return dict(r)

    def list(self):
        return [dict(r) for r in self.items.values()]

    async def clear(self, id=None, owner=None):
        gone = [k for k, r in self.items.items()
                if (id is None and owner is None) or (id is not None and k == id) or (owner is not None and
                                                                                     r["owner"] == owner)]
        for k in gone:
            del self.items[k]
        await self.refresh()
        return len(gone)

    def matches(self, r, item):
        source, _, what = r["hear"].partition(":")
        kind = item.get("kind") or ""
        if item.get("source") != source:
            return False
        return what in ("*", kind, f"{source}.{kind}") or kind == f"{source}.{what}" or kind.startswith(what + ".")

    async def item(self, item):
        if not isinstance(item, dict):
            return
        for r in list(self.items.values()):
            if not self.matches(r, item):
                continue
            if r["cooldown"] and r["last"] is not None and self.now() - r["last"] < r["cooldown"]:
                continue
            r["fired"] += 1
            r["last"] = self.now()
            args = dict(r["args"])
            if r["item"]:
                args[r["item"]] = item
            task = asyncio.ensure_future(self.fire(r, args))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def fire(self, r, args):
        try:
            await _maybe(self.call(r["call"], args, {"by": "reaction", "id": r["id"]}))
            r["error"] = None
        except Exception as e:
            r["error"] = str(getattr(e, "message", e))

    async def app_stopped(self, app):
        await self.clear(owner=app) if any(r["owner"] == app for r in self.items.values()) else None

    async def drain(self):
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
