"""The link follows the apps (0.9.15): link_mode auto (the default; 0.9.14's kept "demand" read as it), always or
demand; in auto the apps that run and want the link live (their kit.app event's or kit.apps row's link "live", or a
local manifest's: Host.app_link) hold a session always, rebuilt from kit.apps after kit.ready and when the link comes
back, cleared when the game exits; status says who holds it (held_by); the apps' activity goes to the reactions
(Reactions.set_pace) from kit.app, kit.option and kit.apps. A Service with a stand-in loop (no game, no daemon)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.daemon.test_standby_daemon import Worker
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.paths import settings_file
from wuxianworkshop.transport import link


class Settings(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)
        settings_file().parent.mkdir(parents=True, exist_ok=True)

    def service(self, stored=None):
        if stored is not None:
            settings_file().write_text(json.dumps(stored), encoding="utf-8")
        return Service(lambda on_debug, log: Worker(), journal=Journal())

    def test_the_kept_mode(self):
        for stored, mode in (({}, "auto"),
                             ({"link_mode": "demand"}, "auto"),                       # 0.9.14's default
                             ({"link_mode": "demand", "link_mode_v": 2}, "demand"),   # the player chose it
                             ({"link_mode": "always"}, "always"),
                             ({"link_mode": "auto"}, "auto"),
                             ({"link_mode": "sometimes"}, "auto")):
            svc = self.service(stored)
            self.assertEqual(svc.link_mode, mode, stored)
            self.assertEqual(svc.worker.settings, [(mode == "always", link.IDLE)], stored)

    async def test_a_choice_is_kept_as_the_players(self):
        svc = self.service({"link_mode": "demand", "language": "en"})
        self.assertEqual((await svc.settings())["link_mode"], "auto")
        res = await svc.settings(dict(link_mode="demand"))
        self.assertEqual(res["link_mode"], "demand")
        kept = json.loads(settings_file().read_text(encoding="utf-8"))
        self.assertEqual((kept["link_mode"], kept["link_mode_v"], kept["language"]), ("demand", 2, "en"))
        self.assertEqual(self.service().link_mode, "demand")                    # the next start keeps it
        await svc.settings(dict(language="zh-CN"))                              # other settings leave it be
        self.assertEqual(self.service().link_mode, "demand")
        with self.assertRaises(ApiError) as e:
            await svc.settings(dict(link_mode="sometimes"))
        self.assertIn('"auto", "always" or "demand"', e.exception.message)
        res = await svc.settings(dict(link_mode="auto"))
        self.assertEqual(res["link_mode"], "auto")


class Holders(unittest.IsolatedAsyncioTestCase):
    """kit.app events and kit.apps rows as on_debug hears them; the local host's app_link and the reactor's set_pace
    stand-ins (a host or a reactor before them is tolerated)"""

    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.addCleanup(self.svc.local.close)
        self.svc.loop = asyncio.get_running_loop()
        self.live = set()                                    # the apps a local manifest declares live
        self.svc.local.app_link = lambda app: "live" if app in self.live else None
        self.svc.local.app_reactions = lambda app: []
        self.paces = []
        self.svc.reactor.set_pace = lambda app, activity: self.paces.append((app, activity))
        self.told = self.svc.worker.settings                 # what the loop was told: (always, idle)

    def emit(self, topic, data):
        self.svc.on_debug("EVENT", json.dumps({"a": "WuxianKit", "t": topic, "d": data}))

    async def settle(self):
        for _ in range(20):
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)                            # what went to a thread (app_link, app_reactions)
        for _ in range(20):
            await asyncio.sleep(0)

    async def held(self):
        return (await self.svc.status())["link"]["held_by"]

    async def test_an_app_that_wants_the_link_live_holds_it(self):
        self.emit("kit.app", {"app": "companion", "on": True, "link": "live", "pace": "normal"})
        await self.settle()
        self.assertEqual(self.told[-1], (True, link.IDLE))
        self.assertEqual(await self.held(), ["companion"])
        self.assertEqual(self.paces, [("companion", "normal")])
        self.paces.clear()
        self.emit("kit.app", {"app": "guild", "on": True})                   # standby: it holds nothing
        await self.settle()
        self.assertEqual(await self.held(), ["companion"])
        self.assertEqual(self.paces, [("guild", None)])                      # no pace: not known
        self.emit("kit.app", {"app": "companion", "on": False, "why": "stopped"})
        await self.settle()
        self.assertEqual(self.told[-1], (False, link.IDLE))
        self.assertEqual(await self.held(), [])
        self.assertEqual(self.told, [(False, link.IDLE), (True, link.IDLE), (False, link.IDLE)])   # only changes
        notes = [e["text"] for e in self.svc.journal.since(0, 100, ["INFO"], tail=True)[0]]
        self.assertIn("link: a session always, kept by the app companion", notes)
        self.assertIn("link: a session while things pass", notes)

    async def test_a_local_manifest_says_it(self):
        self.live.add("companion")
        self.emit("kit.app", {"app": "companion", "on": True})              # a kit 0.6.0: no link in its event
        await self.settle()
        self.assertEqual(await self.held(), ["companion"])
        self.assertEqual(self.paces, [("companion", None)])                  # a kit 0.6.0: no pace, not known

    async def test_a_host_before_app_link(self):
        del self.svc.local.app_link
        with mock.patch.object(type(self.svc.local), "app_link", None, create=True):
            self.emit("kit.app", {"app": "companion", "on": True})
            await self.settle()
        self.assertEqual((await self.held(), self.told), ([], [(False, link.IDLE)]))

    async def test_the_modes(self):
        self.emit("kit.app", {"app": "companion", "on": True, "link": "live"})
        await self.settle()
        await self.svc.settings(dict(link_mode="demand"))                   # never for an app
        self.assertEqual((self.told[-1], await self.held()), ((False, link.IDLE), []))
        await self.svc.settings(dict(link_mode="always"))
        self.assertEqual((self.told[-1], await self.held()), ((True, link.IDLE), []))
        self.emit("kit.app", {"app": "companion", "on": False, "why": "stopped"})
        await self.settle()
        self.assertEqual(self.told[-1], (True, link.IDLE))                   # always, whatever runs
        self.emit("kit.app", {"app": "companion", "on": True, "link": "live"})
        await self.settle()
        await self.svc.settings(dict(link_mode="auto"))                     # the holder is still known
        self.assertEqual((self.told[-1], await self.held()), ((True, link.IDLE), ["companion"]))
        status = (await self.svc.status())["link"]
        self.assertEqual((status["mode"], status["held_titles"]), ("auto", {"companion": "companion"}))

    async def test_the_activity_changes(self):
        self.emit("kit.option", {"app": "companion", "name": "activity", "value": "lively"})
        self.emit("kit.option", {"app": "companion", "name": "volume", "value": 3})       # not the activity
        self.emit("kit.option", {"extension": "voice", "name": "activity", "value": "quiet"})   # an extension's
        self.emit("kit.option", {"app": "companion", "name": "activity", "value": None})        # reset
        await self.settle()
        self.assertEqual(self.paces, [("companion", "lively"), ("companion", None)])

    async def test_the_pace_comes_after_the_start(self):
        """a stop and a start in a row: app_stopped (which forgets the pace) and app_started come first, the start's
        pace after them"""
        order = []
        stopped, started = self.svc.reactor.app_stopped, self.svc.reactor.app_started

        async def app_stopped(app):
            order.append(("stopped", app))
            return await stopped(app)

        async def app_started(app, specs):
            order.append(("started", app))
            return await started(app, specs)
        self.svc.reactor.app_stopped, self.svc.reactor.app_started = app_stopped, app_started
        self.svc.reactor.set_pace = lambda app, activity: order.append(("pace", app, activity))
        self.emit("kit.app", {"app": "companion", "on": False, "why": "replaced"})
        self.emit("kit.app", {"app": "companion", "on": True, "link": "live", "pace": "quiet"})
        self.emit("kit.option", {"app": "companion", "name": "activity", "value": "lively"})
        await self.settle()
        self.assertEqual(order, [("stopped", "companion"), ("started", "companion"), ("pace", "companion", "quiet"),
                                 ("pace", "companion", "lively")])

    async def test_a_reactor_before_set_pace(self):
        del self.svc.reactor.set_pace
        with mock.patch.object(type(self.svc.reactor), "set_pace", None, create=True):
            self.emit("kit.app", {"app": "companion", "on": True, "link": "live", "pace": "quiet"})
            self.emit("kit.option", {"app": "companion", "name": "activity", "value": "lively"})
            await self.settle()
        self.assertEqual(await self.held(), ["companion"])

    async def test_kit_apps_rebuilds_them(self):
        self.emit("kit.app", {"app": "stale", "on": True, "link": "live"})  # one whose stop was missed
        await self.settle()
        self.paces.clear()
        self.live.add("local_one")
        rows = [{"id": "companion", "title": "语音搭子", "on": True, "link": "live", "pace": "quiet"},
                {"id": "local_one", "title": "本机的", "on": True},
                {"id": "off_one", "title": "停着的", "on": False, "link": "live", "pace": "lively"},
                {"id": "guild", "title": "公会聊天", "on": True, "pace": "normal"}]
        await self.svc.holders_from(rows)
        self.assertEqual(await self.held(), ["companion", "local_one"])
        self.assertEqual(self.paces, [("companion", "quiet"), ("local_one", None), ("guild", "normal")])
        status = (await self.svc.status())["link"]
        self.assertEqual(status["held_titles"], {"companion": "语音搭子", "local_one": "本机的"})
        await self.svc.holders_from([{"id": "companion", "on": False}])     # it stopped meanwhile
        self.assertEqual((await self.held(), self.told[-1]), ([], (False, link.IDLE)))

    async def test_kit_ready_reads_kit_apps(self):
        rows = [{"id": "companion", "title": "语音搭子", "on": True, "link": "live", "pace": "normal"}]
        asked = []

        async def kit_read(name, args=None):
            asked.append(name)
            return rows
        with mock.patch.object(self.svc, "kit_read", kit_read), mock.patch.object(self.svc, "link_is_up", lambda: True), \
                mock.patch.object(self.svc, "ui_session", lambda: 7), \
                mock.patch.object(self.svc, "tell_roster", mock.AsyncMock()), \
                mock.patch.object(self.svc, "reactions_hear_again", mock.AsyncMock()):
            self.emit("kit.ready", {"revision": "r1"})
            await self.settle()
            self.assertEqual(asked, ["kit.apps"])
            self.assertEqual(await self.held(), ["companion"])
            self.assertIn(("companion", "normal"), self.paces)
            rows = []                                                        # the link back: it runs no more
            self.svc.link_came_up(7)
            await self.settle()
            self.assertEqual(await self.held(), [])

        async def gone(name, args=None):
            raise ApiError(409, "link_down", "no frames")
        self.svc.hold("companion", True)
        with mock.patch.object(self.svc, "kit_read", gone), mock.patch.object(self.svc, "link_is_up", lambda: True), \
                mock.patch.object(self.svc, "ui_session", lambda: 7), \
                mock.patch.object(self.svc, "tell_roster", mock.AsyncMock()):
            await self.svc.reconcile_local_apps()
        self.assertEqual(await self.held(), ["companion"])                  # not read: kept as it was

    async def test_the_game_exits(self):
        self.emit("kit.app", {"app": "companion", "on": True, "link": "live"})
        await self.settle()
        self.assertEqual(self.svc.worker.on_game_gone, self.svc.game_gone)
        self.svc.worker.on_game_gone()
        self.assertEqual((await self.held(), self.told[-1]), ([], (False, link.IDLE)))


class LoopSaysTheGameIsGone(unittest.TestCase):
    """CompanionLoop calls on_game_gone when the game process it knew exited, and when another one runs"""

    def test_it(self):
        from tests.daemon.test_standby_daemon import Loop
        case = Loop("test_the_hello_says_the_place")
        case.setUp()
        try:
            loop = case.make()
            gone = []
            loop.on_game_gone = lambda: gone.append(True)
            case.rounds(1)
            self.assertEqual(len(gone), 1)                                   # a process it had not seen
            case.rounds(3)
            self.assertEqual(len(gone), 1)
            from wuxianworkshop.daemon import companion as C
            case.win = None                                                  # the window and the process gone
            with mock.patch.object(C, "process_start", lambda pid: None):
                case.rounds(1)
            self.assertEqual(len(gone), 2)
        finally:
            case.doCleanups()


if __name__ == "__main__":
    unittest.main()
