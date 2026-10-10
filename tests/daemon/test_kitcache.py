"""What the daemon learned of WuxianKit in a UI session (daemon/kitcache.py): the asks of every view of WuxianKit
(mcp/kit.py, the daemon's own and each stdio server's) go to the game once. Asks at the same time share the game's answer,
later ones get it while it holds: a describe for its UI session, a revision as kit.ready / kit.changed say it or for
REVISION_FRESH seconds, a manifest by its revision in any session; a failure is not kept. The Service answers only the
views' asks so (SHARED, or shared over HTTP), never an agent's own; the client and HttpBackend say shared only for them."""
import asyncio
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from wuxianworkshop.core import frame as F, mailbox as MB
from wuxianworkshop.daemon import kitcache
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.transport import link


class Game:
    """the game's answers, each after a moment (so that asks can meet in flight), counted"""

    def __init__(self, revision="r1"):
        self.revision, self.asked, self.fail = revision, [], None

    async def describe(self):
        return await self.answer("describe", {"addon": "WuxianKit", "exposed": [{"name": "kit.revision"}], "topics": {}})

    def call(self, name):
        async def ask():
            if name == "kit.revision":
                return await self.answer(name, dict(addon="WuxianKit", name=name, ok=True,
                                                    result={"revision": self.revision}))
            return await self.answer(name, dict(addon="WuxianKit", name=name, ok=True,
                                                result={"revision": self.revision, "extensions": []}))
        return ask

    async def answer(self, what, value):
        self.asked.append(what)
        await asyncio.sleep(0.01)
        if self.fail:
            raise ApiError(504, "timeout", self.fail)
        return value


class Cache(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sid, self.t = 5, 100.0
        self.cache = kitcache.KitCache(lambda: self.sid, clock=lambda: self.t)
        self.game = Game()

    async def test_asks_at_the_same_time_share_one(self):
        """five views asking at once after a reload: one describe goes to the game; a later one is answered at once,
        each with its own copy"""
        got = await asyncio.gather(*(self.cache.describe(self.game.describe) for _ in range(5)))
        self.assertEqual((self.game.asked, len(got)), (["describe"], 5))
        again = await self.cache.describe(self.game.describe)
        self.assertEqual((again, self.game.asked), (got[0], ["describe"]))
        again["exposed"].clear()                                          # a copy: what is kept stays as it was
        self.assertEqual(len((await self.cache.describe(self.game.describe))["exposed"]), 1)

    async def test_a_new_session_asks_again(self):
        await self.cache.describe(self.game.describe)
        self.sid = 6                                                      # a reload
        await self.cache.describe(self.game.describe)
        self.cache.forget()                                               # a HELLO (also under an id seen before)
        await self.cache.describe(self.game.describe)
        self.assertEqual(self.game.asked, ["describe"] * 3)

    async def test_events_say_the_revision(self):
        """kit.ready / kit.changed: the revision without asking, for REVISION_FRESH seconds; the describe may be old"""
        await self.cache.describe(self.game.describe)
        self.cache.heard("kit.changed", {"revision": "r2"})
        res = await self.cache.call("kit.revision", self.game.call("kit.revision"))
        self.assertEqual((res["result"], self.game.asked), ({"revision": "r2"}, ["describe"]))
        await self.cache.describe(self.game.describe)
        self.assertEqual(self.game.asked, ["describe", "describe"])
        self.t += kitcache.REVISION_FRESH                                 # an event may have been dropped: ask
        await self.cache.call("kit.revision", self.game.call("kit.revision"))
        await self.cache.call("kit.revision", self.game.call("kit.revision"))
        self.assertEqual(self.game.asked, ["describe", "describe", "kit.revision"])
        self.cache.heard("kit.proposal", {"id": 3})                       # its other events say nothing of it
        await self.cache.describe(self.game.describe)
        self.assertEqual(len(self.game.asked), 3)

    async def test_a_manifest_is_kept_by_its_revision(self):
        """the manifest answers for its revision in any session: a reload that runs the same WuxianKit (kit.ready says
        its revision) costs no manifest; another revision is asked for"""
        m = await self.cache.call("kit.manifest", self.game.call("kit.manifest"))
        self.assertEqual(m["result"]["revision"], "r1")
        await self.cache.call("kit.revision", self.game.call("kit.revision"))   # the manifest said it
        self.sid = 6
        self.cache.heard("kit.ready", {"revision": "r1", "version": "0.4.1"})
        await self.cache.call("kit.manifest", self.game.call("kit.manifest"))
        self.assertEqual(self.game.asked, ["kit.manifest"])
        self.game.revision = "r2"
        self.cache.heard("kit.changed", {"revision": "r2"})
        m = await self.cache.call("kit.manifest", self.game.call("kit.manifest"))
        self.assertEqual((m["result"]["revision"], self.game.asked), ("r2", ["kit.manifest", "kit.manifest"]))
        for r in ("r3", "r4", "r5", "r6"):                               # the last MANIFESTS_KEPT are kept
            self.game.revision = r
            self.cache.heard("kit.changed", {"revision": r})
            await self.cache.call("kit.manifest", self.game.call("kit.manifest"))
        self.assertEqual(list(self.cache.manifests), ["r3", "r4", "r5", "r6"])

    async def test_a_failure_is_not_kept(self):
        self.game.fail = "no answer"
        got = await asyncio.gather(*(self.cache.describe(self.game.describe) for _ in range(3)), return_exceptions=True)
        self.assertTrue(all(isinstance(g, ApiError) for g in got))
        self.assertEqual(self.game.asked, ["describe"])
        self.game.fail = None
        await self.cache.describe(self.game.describe)
        self.assertEqual(self.game.asked, ["describe", "describe"])

    async def test_what_changed_during_an_ask(self):
        """an event while an ask is in flight: its answer goes to whoever waits for it but is not kept, and an ask
        after the event does not join it"""
        first = asyncio.ensure_future(self.cache.describe(self.game.describe))
        await asyncio.sleep(0)
        self.cache.heard("kit.changed", {"revision": "r2"})
        second = asyncio.ensure_future(self.cache.describe(self.game.describe))
        await asyncio.gather(first, second)
        self.assertEqual(self.game.asked, ["describe", "describe"])
        await self.cache.describe(self.game.describe)                     # the second's answer is kept
        self.assertEqual(len(self.game.asked), 2)


class Worker:
    """the worker thread's side for the Service, here and now"""

    def __init__(self, comp):
        self.comp, self.addons, self.win, self.wgc = comp, comp.addons, None, None

    def companion(self):
        return self.comp

    def request(self, fn, *args):
        fut = Future()
        try:
            fut.set_result(fn(*args))
        except BaseException as e:
            fut.set_exception(e)
        return fut


class Views(unittest.IsolatedAsyncioTestCase):
    """the Service: WuxianKit's asks of its views (SHARED, shared) go to the game once; an agent's own, another addon's
    and other calls always go"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        addons = Path(self.tmp.name)
        (addons / "WoWBridge").mkdir()
        MB.install(addons, pool=16)
        self.comp = link.Companion(addons, log=lambda text: None)
        self.comp.new_process("P1-1")
        self.comp.on_frame(F.TYPE_CONTROL, 5, 1, b"HELLO;v=0.9.8;s=5;k=1;m=1;p=P1-1;f=2;j=0")
        self.svc = Service(lambda **kw: Worker(self.comp), journal=Journal())
        self.asked, self.own = [], []

        async def describe(addon, brief):
            self.asked.append(("describe", addon))
            await asyncio.sleep(0.01)
            return {"addon": addon, "exposed": [{"name": "kit.revision"}], "topics": {}}

        async def call_game(addon, name, args, timeout_ms, code, own=False):
            self.asked.append((name, addon))
            self.own.append(own)
            await asyncio.sleep(0.01)
            return dict(addon=addon, name=name, ok=True, result={"revision": "r1"})
        self.svc.describe, self.svc.call_game = describe, call_game

    def tearDown(self):
        self.tmp.cleanup()

    async def test_only_the_views_asks_are_shared(self):
        async def view(fn, *args):
            token = kitcache.SHARED.set(True)                             # as KitTools.ask does
            try:
                return await fn(*args)
            finally:
                kitcache.SHARED.reset(token)
        await asyncio.gather(view(self.svc.addon_api, "WuxianKit"), self.svc.addon_api("WuxianKit", shared=True),
                             view(self.svc.call_exposed, "WuxianKit", "kit.revision"))
        self.assertEqual(sorted(self.asked), [("describe", "WuxianKit"), ("kit.revision", "WuxianKit")])   # once each
        await self.svc.addon_api("WuxianKit")                             # an agent's addon_api: the game's answer
        await self.svc.call_exposed("WuxianKit", "kit.revision")
        await view(self.svc.addon_api, "Foo")                             # another addon
        await view(self.svc.call_exposed, "WuxianKit", "kit.history")     # another call
        self.assertEqual(len(self.asked), 6)
        self.assertEqual(self.own, [True, False, False])                  # only the views' own reads go quiet in the game
        await view(self.svc.addon_api, "WuxianKit")
        self.assertEqual(len(self.asked), 6)

    async def test_events_and_new_sessions(self):
        """WuxianKit's kit.changed (an EVENT the companion hands on) says the revision and makes the describe old; a
        new UI session's HELLO forgets what was known"""
        await self.svc.addon_api("WuxianKit", shared=True)
        self.svc.on_debug("EVENT", '{"a":"WuxianKit","t":"kit.changed","d":{"revision":"r9"}}')
        res = await self.svc.call_exposed("WuxianKit", "kit.revision", shared=True)
        self.assertEqual((res["result"], self.asked), ({"revision": "r9"}, [("describe", "WuxianKit")]))
        self.svc.on_debug("EVENT", '{"a":"Other","t":"kit.changed","d":{"revision":"r0"}}')   # not WuxianKit's
        self.assertEqual((await self.svc.call_exposed("WuxianKit", "kit.revision", shared=True))["result"]["revision"],
                         "r9")
        await self.svc.addon_api("WuxianKit", shared=True)
        self.assertEqual(len(self.asked), 2)
        self.svc.session_started(6, None)
        await self.svc.addon_api("WuxianKit", shared=True)
        await self.svc.call_exposed("WuxianKit", "kit.revision", shared=True)
        self.assertEqual([what for what, _ in self.asked], ["describe", "describe", "describe", "kit.revision"])


class Plumbing(unittest.TestCase):
    """over HTTP: HttpBackend says shared for the views' asks only, the client posts it only then"""

    def test_the_client_says_shared(self):
        from wuxianworkshop.cli import client
        c = client.DaemonClient({"port": 1, "token": "x"})
        posted = []
        with mock.patch.object(c, "post", lambda path, timeout=None, **body: posted.append((path, body))):
            c.addon_api("WuxianKit")
            c.addon_api("WuxianKit", shared=True)
            c.call_exposed("WuxianKit", "kit.revision", None, 15000)
            c.call_exposed("WuxianKit", "kit.revision", None, 15000, True)
        self.assertEqual([("shared" in body, body.get("shared")) for _, body in posted],
                         [(False, None), (True, True), (False, None), (True, True)])

    def test_http_backend_says_it_for_the_views(self):
        from wuxianworkshop.mcp.server import HttpBackend

        class Client:
            def __init__(self):
                self.calls = []

            def addon_api(self, addon=None, brief=False, shared=False):
                self.calls.append(("addon_api", shared))
                return {}

            def call_exposed(self, addon, name, args=None, timeout_ms=10000, shared=False):
                self.calls.append((name, shared))
                return {}
        client = Client()
        backend = HttpBackend(client)

        async def go():
            await backend.addon_api("WuxianKit")
            token = kitcache.SHARED.set(True)
            try:
                await backend.addon_api("WuxianKit")
                await backend.call_exposed("WuxianKit", "kit.revision", None, 15000)
            finally:
                kitcache.SHARED.reset(token)
            await backend.call_exposed("WuxianKit", "kit.revision", None, 15000)
        asyncio.run(go())
        self.assertEqual(client.calls, [("addon_api", False), ("addon_api", True), ("kit.revision", True),
                                        ("kit.revision", False)])


if __name__ == "__main__":
    unittest.main()
