"""What the daemon learned of WuxianKit in the game's UI session, for the views of it (mcp/kit.py): the daemon's own /mcp
and every `wuxian mcp` on stdio each ask the game what WuxianKit runs after every reload or restart: what it exposed (a
describe of some 24 KB, six RUN results of 4 KB on the screen), its revision, and the manifest (some 22 KB) when that is
another one. On 2026-10-10 five of them asked at once after a restart, and their answers held the screen for half a
minute.
Their asks (made under SHARED, or with shared over HTTP) are answered once for all: asks at the same time share the
game's answer, later ones get it from here while it holds. A describe and a revision hold for the UI session they came in
(a new session, a reload or a restart, forgets them); WuxianKit's kit.ready and kit.changed events say its revision and
that a describe may be old; a revision answers for REVISION_FRESH seconds, then the game is asked again (an event may be
dropped). A manifest is kept by its revision, a hash of it: the same in any session. What an agent asks itself (the
addon_api and call tools) never comes from here."""
import asyncio
import contextvars
import copy
import threading
import time

KIT = "WuxianKit"
SHARED = contextvars.ContextVar("wuxian_kit_shared", default=False)   # set by the views' asks (mcp/kit.py)
REVISION_FRESH = 60.0       # seconds a revision answers for after the game or an event said it
MANIFESTS_KEPT = 4


class KitCache:
    """session(): the game's current UI session (None: none). heard() and forget() come on the worker thread, the asks
    on the asyncio loop"""

    def __init__(self, session, clock=time.monotonic):
        self.session, self.clock = session, clock
        self.lock = threading.Lock()
        self.sid, self.era = None, 0  # the session it knows of; era: one more whenever what it knows may have gone old
        self.described = {}           # brief -> WuxianKit's describe answer in session sid (brief: the names only)
        self.revision = None          # (revision, when it was said) in session sid
        self.manifests = {}           # revision -> manifest, the oldest first
        self.flights = {}             # what -> ((session, era), the ask in flight)
        self.asked = 0                # asks that went to the game (tests and the report)

    def _session(self):
        """(under the lock) the current session; what was known of another one goes"""
        sid = self.session()
        if sid != self.sid:
            self.sid, self.described, self.revision = sid, {}, None
            self.era += 1
        return sid

    def forget(self):
        """(any thread) a new UI session began (its HELLO): nothing known holds, also under a session id seen before"""
        with self.lock:
            self.sid, self.described, self.revision = None, {}, None
            self.era += 1

    def heard(self, topic, data):
        """(worker thread) WuxianKit's event of the current session: kit.ready or kit.changed say its revision; what
        it exposed may have changed"""
        if topic not in ("kit.ready", "kit.changed"):
            return
        revision = data.get("revision") if isinstance(data, dict) else None
        with self.lock:
            self._session()
            self.described = {}
            self.revision = (revision, self.clock()) if isinstance(revision, str) else None
            self.era += 1

    def _fresh(self):
        """(under the lock) the revision while it answers for itself, or None"""
        r = self.revision
        return r[0] if r is not None and self.clock() - r[1] < REVISION_FRESH else None

    async def describe(self, ask, brief=False):
        """the describe answer of WuxianKit (brief: the names only); ask(): the game's"""
        def keep(answer, current):
            if current and isinstance(answer, dict):
                self.described[brief] = answer
        return await self._answer(("describe", brief), lambda: self.described.get(brief), ask, keep)

    async def call(self, name, ask):
        """the answer of kit.revision or kit.manifest, as call_exposed gives it; ask(): the game's"""
        def known():
            revision = self._fresh()
            if name == "kit.revision":
                result = {"revision": revision} if revision is not None else None
            else:
                result = self.manifests.get(revision)
            return None if result is None else dict(addon=KIT, name=name, ok=True, result=result)

        def keep(answer, current):
            result = answer.get("result") if isinstance(answer, dict) else None
            revision = result.get("revision") if isinstance(result, dict) else None
            if not isinstance(revision, str):
                return
            if name == "kit.manifest":                   # a hash of it: right in any session
                self.manifests.pop(revision, None)
                self.manifests[revision] = result
                while len(self.manifests) > MANIFESTS_KEPT:
                    del self.manifests[next(iter(self.manifests))]
            if current:
                self.revision = (revision, self.clock())
        return await self._answer(name, known, ask, keep)

    async def _answer(self, what, known, ask, keep):
        """what is known (a copy), or the game's answer: one ask for all that come while it is in flight"""
        with self.lock:
            sid = self._session()
            hit = known()
            if hit is not None:
                return copy.deepcopy(hit)
            flight = self.flights.get(what)
            if flight is None or flight[0] != (sid, self.era):    # none, or one from before what it knew went old
                task = asyncio.ensure_future(self._fly(what, (sid, self.era), ask, keep))
                task.add_done_callback(lambda t: t.cancelled() or t.exception())   # nobody may wait any more
                flight = self.flights[what] = ((sid, self.era), task)
        return copy.deepcopy(await asyncio.shield(flight[1]))

    async def _fly(self, what, mark, ask, keep):
        self.asked += 1
        try:
            answer = await ask()
        finally:
            with self.lock:
                if self.flights.get(what, (None,))[0] == mark:
                    del self.flights[what]
        with self.lock:
            keep(answer, (self._session(), self.era) == mark)
        return answer
