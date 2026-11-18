"""The link follows the apps: link_mode auto (the default; anything else that is kept, 0.9.14's "demand" too, is read
as it) or always; in auto the apps that run and want the link live (their kit.app event's or kit.apps row's link
"live": a local extension's app says it there too, its declaration carries it) hold a session always, rebuilt from
kit.apps after kit.ready and when the link comes back, cleared when the game exits; status says who holds it
(held_by: [{id, title}]); the apps' activity goes to the reactions (Reactions.set_activity) from kit.app, kit.option
and kit.apps. The game's WuxianKit older than 0.7.0 is told nothing, and status says it is outdated. A Service with a
stand-in loop (no game, no daemon)."""
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
                             ({"link_mode": "always"}, "always"),
                             ({"link_mode": "auto"}, "auto"),
                             ({"link_mode": "sometimes"}, "auto")):
            svc = self.service(stored)
            self.assertEqual(svc.link_mode, mode, stored)
            self.assertEqual(svc.worker.settings, [(mode == "always", link.IDLE)], stored)

    async def test_two_modes(self):
        svc = self.service({"link_mode": "demand", "language": "en"})
        self.assertEqual((await svc.settings())["link_mode"], "auto")
        res = await svc.settings(dict(link_mode="always"))
        self.assertEqual(res["link_mode"], "always")
        kept = json.loads(settings_file().read_text(encoding="utf-8"))
        self.assertEqual((kept["link_mode"], kept["language"]), ("always", "en"))
        self.assertEqual(self.service().link_mode, "always")                    # the next start keeps it
        await svc.settings(dict(language="zh-CN"))                              # other settings leave it be
        self.assertEqual(self.service().link_mode, "always")
        for bad in ("demand", "sometimes", None, True):
            with self.assertRaises(ApiError) as e:
                await svc.settings(dict(link_mode=bad))
            self.assertIn('"auto" or "always"', e.exception.message)
        res = await svc.settings(dict(link_mode="auto"))
        self.assertEqual(res["link_mode"], "auto")


class Holders(unittest.IsolatedAsyncioTestCase):
    """kit.app events and kit.apps rows as on_debug hears them; the reactor's set_activity a stand-in"""

    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.addCleanup(self.svc.local.close)
        self.svc.loop = asyncio.get_running_loop()
        self.svc.local.app_reactions = lambda app: []
        self.paces = []
        self.svc.reactor.set_activity = lambda app, activity: self.paces.append((app, activity))
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
        return [h["id"] for h in (await self.svc.status())["link"]["held_by"]]

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

    async def test_only_the_game_says_it(self):
        self.emit("kit.app", {"app": "companion", "on": True})              # no link in its event: it holds nothing
        await self.settle()
        self.assertEqual((await self.held(), self.told), ([], [(False, link.IDLE)]))
        self.assertEqual(self.paces, [("companion", None)])                  # an app without a pace: not known

    async def test_the_modes(self):
        self.emit("kit.app", {"app": "companion", "on": True, "link": "live"})
        await self.settle()
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
        self.assertEqual((status["mode"], status["held_by"]), ("auto", [{"id": "companion", "title": "companion"}]))
        self.assertNotIn("held_titles", status)

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
        self.svc.reactor.set_activity = lambda app, activity: order.append(("pace", app, activity))
        self.emit("kit.app", {"app": "companion", "on": False, "why": "replaced"})
        self.emit("kit.app", {"app": "companion", "on": True, "link": "live", "pace": "quiet"})
        self.emit("kit.option", {"app": "companion", "name": "activity", "value": "lively"})
        await self.settle()
        self.assertEqual(order, [("stopped", "companion"), ("started", "companion"), ("pace", "companion", "quiet"),
                                 ("pace", "companion", "lively")])

    async def test_kit_apps_rebuilds_them(self):
        self.emit("kit.app", {"app": "stale", "on": True, "link": "live"})  # one whose stop was missed
        await self.settle()
        self.paces.clear()
        rows = [{"id": "companion", "title": "语音搭子", "on": True, "link": "live", "pace": "quiet"},
                {"id": "local_one", "title": "本机的", "on": True, "link": "live", "local": {"id": "fx"}},
                {"id": "off_one", "title": "停着的", "on": False, "link": "live", "pace": "lively"},
                {"id": "guild", "title": "公会聊天", "on": True, "pace": "normal"}]
        await self.svc.holders_from(rows)
        self.assertEqual(await self.held(), ["companion", "local_one"])
        self.assertEqual(self.paces, [("companion", "quiet"), ("local_one", None), ("guild", "normal")])
        status = (await self.svc.status())["link"]
        self.assertEqual(status["held_by"], [{"id": "companion", "title": "语音搭子"},
                                             {"id": "local_one", "title": "本机的"}])
        await self.svc.holders_from([{"id": "companion", "on": False}])     # it stopped meanwhile
        self.assertEqual((await self.held(), self.told[-1]), ([], (False, link.IDLE)))

    async def test_the_loop_hears_who_keeps_the_link(self):
        """the titles of the apps that hold the link go to the loop with the setting (the addon's panel names them:
        the WELCOME's by=), by id as held_by gives them, again when one comes, goes or its title is learned; none
        when nobody holds it, and none with link_mode always (the settings keep it then)"""
        kept = self.svc.worker.kept
        self.assertEqual(kept, [()])
        self.emit("kit.app", {"app": "voice", "on": True, "link": "live"})   # its title is not known yet: its id
        await self.settle()
        self.assertEqual((self.told[-1], kept[-1]), ((True, link.IDLE), ("voice",)))
        rows = [{"id": "voice", "title": "语音搭子", "on": True, "link": "live"},
                {"id": "cocreate", "title": "数据共创", "on": True, "link": "live"},
                {"id": "guild", "title": "公会聊天", "on": True}]
        await self.svc.holders_from(rows)
        self.assertEqual((self.told[-1], kept[-1]), ((True, link.IDLE), ("数据共创", "语音搭子")))
        n = len(kept)
        await self.svc.holders_from(rows)                                    # read again, nothing new: not told again
        self.assertEqual(len(kept), n)
        rows[0]["title"] = "语音搭子 2"                                       # the same holders, one renamed
        await self.svc.holders_from(rows)
        self.assertEqual(kept[-1], ("数据共创", "语音搭子 2"))
        await self.svc.settings(dict(link_mode="always"))
        self.assertEqual((self.told[-1], kept[-1]), ((True, link.IDLE), ()))
        await self.svc.settings(dict(link_mode="auto"))
        self.assertEqual(kept[-1], ("数据共创", "语音搭子 2"))
        self.emit("kit.app", {"app": "cocreate", "on": False, "why": "stopped"})
        await self.settle()
        self.assertEqual((self.told[-1], kept[-1]), ((True, link.IDLE), ("语音搭子 2",)))
        self.svc.game_gone()
        self.assertEqual((self.told[-1], kept[-1]), ((False, link.IDLE), ()))

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
            self.emit("kit.ready", {"revision": "r1", "version": "0.7.0"})
            await self.settle()
            self.assertEqual(asked, ["kit.apps"])
            self.assertEqual(await self.held(), ["companion"])
            self.assertIn(("companion", "normal"), self.paces)
            self.assertEqual((await self.svc.status())["kit"], {"version": "0.7.0"})
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

    async def test_a_wuxiankit_that_is_too_old_is_told_nothing(self):
        asked, told = [], mock.AsyncMock()

        async def kit_read(name, args=None):
            asked.append(name)
            return []

        async def call_exposed(addon, name, args=None, timeout_ms=10000, shared=None, most=None):
            asked.append(name)
            return {"result": {}}
        self.assertEqual((await self.svc.status())["kit"], {"version": None})    # no game seen: nothing said
        with mock.patch.object(self.svc, "kit_read", kit_read), mock.patch.object(self.svc, "link_is_up", lambda: True), \
                mock.patch.object(self.svc, "ui_session", lambda: 7), \
                mock.patch.object(self.svc, "call_exposed", call_exposed), \
                mock.patch.object(self.svc, "reactions_hear_again", mock.AsyncMock()):
            for version in ("0.6.0", "0.6.1"):
                self.emit("kit.ready", {"revision": "r1", "version": version})
                await self.settle()
                self.assertEqual(asked, [], version)                         # no list, no apps
                self.assertEqual((await self.svc.status())["kit"],
                                 {"version": version, "outdated": {"needs": "0.7.0", "has": version}})
                self.assertFalse(self.svc.drive.reachable())                 # no reports either
            notes = [e["text"] for e in self.svc.journal.since(0, 100, ["INFO"], tail=True)[0]]
            self.assertEqual(len([n for n in notes if "older than the 0.7.0" in n]), 2)
            for version in ("0.7.0", "0.7.1", "1.0", "?"):                   # new enough, or not to be judged
                asked.clear()
                self.emit("kit.ready", {"revision": "r1", "version": version})
                await self.settle()
                self.assertIn("kit.local.set", asked, version)
                self.assertNotIn("outdated", (await self.svc.status())["kit"])
            self.svc.worker.on_game_gone()                                   # another game: not known again
            self.assertEqual((await self.svc.status())["kit"], {"version": None})

    async def test_the_version_is_asked_when_no_kit_ready_said_it(self):
        """this program started while the game ran on: the manifest known when its revision is the game's, else the
        game's own"""
        asked = []
        game = {"revision": "r9", "kit": "0.6.1"}

        async def kit_read(name, args=None):
            asked.append(name)
            return {"kit.revision": {"revision": game["revision"]}, "kit.manifest": game}.get(name, [])
        with mock.patch.object(self.svc, "kit_read", kit_read), mock.patch.object(self.svc, "link_is_up", lambda: True), \
                mock.patch.object(self.svc, "ui_session", lambda: 7), \
                mock.patch.object(self.svc, "tell_roster", mock.AsyncMock()) as told, \
                mock.patch.object(self.svc, "kit_manifest_known", lambda: {"revision": "r1", "kit": "0.7.0"}):
            await self.svc.reconcile_local_apps()                           # the one kept is another's: asked
            self.assertEqual(asked, ["kit.revision", "kit.manifest"])
            self.assertEqual((await self.svc.status())["kit"]["outdated"], {"needs": "0.7.0", "has": "0.6.1"})
            told.assert_not_awaited()
            self.svc.worker.on_game_gone()
            asked.clear()
            game.update(revision="r1")                                       # the one kept is the game's: its version
            await self.svc.reconcile_local_apps()
            self.assertEqual(asked, ["kit.revision", "kit.apps"])
            self.assertEqual((await self.svc.status())["kit"], {"version": "0.7.0"})
            told.assert_awaited()

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
