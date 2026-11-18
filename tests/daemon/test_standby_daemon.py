"""Standing by on the program's side (WoWBridge 0.9.14, link features 5): with no session CompanionLoop only peeks with
GDI at the frame's place once a second (the doorbell; no WGC capture: no yellow border), finds that place on the screen
or from the HELLO, keeps it, and reads at once when a request opens a session; the installed .toc says whether the
addon stands by; 常开 opens a session by itself. The Service lets a call through to a link that stands by (its records
open the session), fails the waiting calls when no frames come or the player ends the session, and keeps the settings
link_mode; try's window keeps the session (hold)."""
import asyncio
import concurrent.futures
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.daemon.test_companion_fast import SIZE, Screen
from wuxianworkshop.core import frame as F, mailbox as MB
from wuxianworkshop.daemon import companion as C
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.transport import link
from wuxianworkshop.transport.capture import Window


def write_toc(addons, version):
    (addons / "WoWBridge" / "WoWBridge.toc").write_text(f"## Interface: 120000\n## Version: {version}\n", encoding="utf-8")


class Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.addons = Path(tmp.name) / "AddOns"
        (self.addons / "WoWBridge").mkdir(parents=True)
        MB.install(self.addons, pool=16)


class StandsBy(Home):
    def test_the_toc_says_it(self):
        self.assertFalse(C.stands_by(self.addons))                  # no .toc
        for version, yes in (("0.9.13", False), ("0.9.14", True), ("0.10.0", True), ("1.0.0", True), ("0.9", False)):
            write_toc(self.addons, version)
            self.assertEqual(C.stands_by(self.addons), yes, version)


class Loop(Home):
    def make(self, version="0.9.14", at=None):
        write_toc(self.addons, version)
        self.win = Window(hwnd=1, pid=os.getpid(), exe="", title="魔兽世界", x=0, y=0, w=SIZE[0], h=SIZE[1], dpi=96,
                          minimized=False)
        self.notes = []
        self.loop = C.CompanionLoop(self.addons, capture="gdi", wait_restart=False, files=False, log=self.notes.append,
                                    find_window=lambda: self.win)
        self.loop._wait = lambda seconds: None
        self.screen = Screen(at) if at else Screen()
        p = mock.patch.object(C, "grab_client", lambda *a, **k: self.screen.grab(*a, **k))
        p.start()
        self.addCleanup(p.stop)
        self.clock = [1000.0]
        p = mock.patch.object(C.time, "monotonic", lambda: self.clock[0])
        p.start()
        self.addCleanup(p.stop)
        return self.loop

    def rounds(self, n, dt=0.05):
        for _ in range(n):
            self.loop.step()
            self.clock[0] += dt

    def peeks(self):
        return [c for c in self.screen.calls if c[0] is not None]

    def test_standing_by_only_the_doorbell_is_read(self):
        loop = self.make()
        self.screen.clear()
        self.rounds(100)                                             # 5 s
        self.assertTrue(loop.comp.standing_by())
        self.assertEqual(loop.comp.link_state(), "standby")
        self.assertFalse(loop.comp.session)
        here = MB.read_slot(self.addons, 1)["records"]
        self.assertTrue(here[0][1].startswith(b"here "))
        peeks = self.peeks()
        self.assertEqual(len(peeks), 5)                              # once a second
        self.assertEqual(peeks[0], (C.ROI_SMALL[0], C.ROI_SMALL[1], 0, 0))   # at the default place
        scans = [c for c in self.screen.calls if c[0] is None]
        self.assertEqual(len(scans), 1)                              # the place not known: a look over the window
        self.rounds(100)
        self.assertEqual(len([c for c in self.screen.calls if c[0] is None]), 2)   # every 5 s

    def test_the_doorbell_finds_the_frame_and_keeps_its_place(self):
        loop = self.make(at=(800, 400))
        self.rounds(3)
        self.assertTrue(loop.comp.session)                           # a frame while standing by: a session
        self.assertLessEqual(abs(loop.bell[0] - 800) + abs(loop.bell[1] - 400), 16)   # the frame's corner
        kept = json.loads(loop.bell_file.read_text(encoding="utf-8"))
        self.assertEqual((kept["x"], kept["y"]), loop.bell)
        again = C.CompanionLoop(self.addons, capture="gdi", wait_restart=False, files=False, log=self.notes.append,
                                find_window=lambda: self.win)
        self.assertEqual(again.bell, loop.bell)                      # the next start knows it

    def test_a_frame_in_the_corner_is_kept_where_it_is(self):
        loop = self.make(at=(4, 6))                                  # nearer the corner than MARGIN: roi_at clamps at 0
        self.rounds(3)
        self.assertTrue(loop.comp.session)
        self.assertLessEqual(abs(loop.bell[0] - 4) + abs(loop.bell[1] - 6), 4)    # not (MARGIN, MARGIN)

    def test_the_hello_says_the_place(self):
        loop = self.make()
        self.screen.clear()
        self.rounds(1)
        loop.comp.on_place(400, 200, 640, 360)                       # the game's physical pixels: half the client's
        self.assertEqual(loop.bell, (800, 400))
        loop.bell = (1200, 700)                                      # a smaller window: the addon moved the frame in
        self.assertEqual(loop._bell_at(self.win), (SIZE[0] - C.ROOM[0] - C.MARGIN, SIZE[1] - C.ROOM[1] - C.MARGIN))

    def test_an_opening_reads_at_once_where_the_doorbell_is(self):
        loop = self.make()
        self.screen.clear()
        loop.bell = (300, 200)
        self.rounds(2)
        n = len(self.screen.calls)
        loop.comp.code(b"return 1", "=run", "-")
        self.rounds(2)                                               # the tick opens a session; the next round reads
        self.assertIsNotNone(loop.comp.opening)
        opened = MB.read_slot(self.addons, loop.comp.open_slot)["records"]
        self.assertTrue(opened[0][1].startswith(b"at "))
        self.assertGreater(len(self.screen.calls), n)
        self.assertEqual(self.screen.calls[n][2:], (300 - C.MARGIN, 200 - C.MARGIN))
        self.assertEqual(loop._rate(), loop.rate)                    # opening: no slowing down for missed reads

    def test_wgc_stops_with_the_session(self):
        loop = self.make()
        stopped = []
        loop.wgc = SimpleNamespace(stop=lambda: stopped.append(1), hwnd=1, closed=False)
        self.screen.clear()
        self.rounds(1)
        self.assertEqual(stopped, [1])
        self.assertIsNone(loop.wgc)
        self.assertIn("no session: only the doorbell is read (GDI, at the frame's place)", self.notes)

    def test_a_snap_while_standing_by_starts_a_capture_for_it(self):
        """standing by no WGC capture runs (GDI would picture whatever covers the game): a snap gets one of its own,
        which the caller stops; in a session the session's is used, and with GDI chosen nothing starts"""
        loop = self.make()
        self.screen.clear()
        self.rounds(1)
        made = []

        class Fake:
            def __init__(self, hwnd, w, h):
                made.append((hwnd, w, h))

        with mock.patch.object(C, "WgcSource", Fake):
            loop.capture = "wgc"
            wgc, started = loop.snap_source()
            self.assertIsInstance(wgc, Fake)
            self.assertTrue(started)
            self.assertEqual(made, [(self.win.hwnd, *C.ROI_FULL)])
            loop.wgc = session = SimpleNamespace(stop=lambda: None)
            self.assertEqual(loop.snap_source(), (session, False))
            loop.wgc, loop.capture = None, "gdi"
            self.assertEqual(loop.snap_source(), (None, False))
            self.assertEqual(len(made), 1)

    def test_an_older_addon_is_read_as_ever(self):
        loop = self.make("0.9.13")
        self.rounds(3)
        self.assertFalse(loop.comp.standing_by())
        self.assertEqual(len(self.screen.calls), 3)                  # read every round: its frames bring it online
        self.assertEqual(loop.comp.link_state(), "online")

    def test_always_opens_a_session_by_itself(self):
        loop = self.make()
        self.screen.clear()
        loop.link_settings(True, 300.0)
        self.rounds(2)
        self.assertTrue(loop.comp.always)
        self.assertTrue(loop.comp.session)
        self.assertIsNotNone(loop.comp.opening)

    def test_the_settings_reach_the_companion(self):
        loop = self.make()
        loop.link_settings(False, 60.0)
        self.rounds(1)
        self.assertEqual((loop.comp.always, loop.comp.idle, loop.comp.by), (False, 60.0, ()))
        loop.link_settings(True, 60.0, ["语音搭子"])                   # ... and who keeps the session always
        self.rounds(1)
        self.assertEqual((loop.comp.always, loop.comp.by), (True, ("语音搭子",)))
        loop.link_settings(True, 60.0, ["语音搭子", "数据共创"])        # another app with it: the same setting, told too
        self.rounds(1)
        self.assertEqual(loop.comp.by, ("语音搭子", "数据共创"))

    def test_bye_when_the_loop_ends(self):
        loop = self.make()
        self.screen.clear()
        self.rounds(2)
        loop.stopping.set()
        loop.run()
        last = MB.read_slot(self.addons, loop.comp.slot - 1)["records"]
        self.assertTrue(last[0][1].startswith(b"bye "), last)


class Doorbell(Home):
    def comp(self):
        comp = link.Companion(self.addons, log=lambda t: None)
        comp.standby = True
        comp.new_process("P1-100")
        comp.tick()                                                   # "here"
        return comp

    def test_a_frame_while_standing_by_is_a_session(self):
        comp = self.comp()
        self.assertFalse(comp.session)
        comp.on_frame(F.TYPE_CONTROL, 9, 1, b"HELLO;v=0.9.14;s=9;k=2;m=1;p=P1-100;pw=1280;ph=720;f=5;j=0;o=40,50")
        self.assertTrue(comp.session)
        self.assertTrue(comp.link_up)
        self.assertEqual(comp.place, (40, 50, 1280, 720))
        welcome = [r for r in comp.outbox if r[0] == MB.WELCOME]
        self.assertTrue(welcome[0][1].endswith(b";idle=3"))

    def test_a_bye_read_again_opens_nothing(self):
        comp = self.comp()
        comp.on_frame(F.TYPE_CONTROL, 9, 3, b"BYE;why=idle;k=4;m=1")
        self.assertFalse(comp.session)

    def test_the_last_frames_after_a_rest_are_no_doorbell(self):
        comp = self.comp()
        comp.on_frame(F.TYPE_CONTROL, 9, 1, b"HELLO;v=0.9.14;s=9;k=2;m=1;p=P1-100;f=5;j=0")
        self.assertTrue(comp.rest())
        comp.on_frame(F.TYPE_CONTROL, 9, 2, b"HB;n=1;k=3;m=1;l=0;q=0")
        self.assertFalse(comp.session)
        comp.on_frame(F.TYPE_CONTROL, 9, 3, b"BYE;why=rest;k=4;m=1")
        self.assertFalse(comp.session)

    def test_an_idle_bye_ends_the_session_quietly(self):
        comp, byes = self.comp(), []
        comp.on_bye = byes.append
        comp.on_frame(F.TYPE_CONTROL, 9, 1, b"HELLO;v=0.9.14;s=9;k=2;m=1;p=P1-100;f=5;j=0")
        comp.on_frame(F.TYPE_CONTROL, 9, 2, b"BYE;why=idle;k=3;m=1")
        self.assertFalse(comp.session)
        self.assertEqual(byes, [])
        self.assertEqual(comp.rested[1], "the addon's idle time")

    def test_a_session_whose_frames_stopped_is_over(self):
        t = [100.0]
        comp = link.Companion(self.addons, log=lambda t: None, clock=lambda: t[0])
        comp.standby = True
        comp.new_process("P1-100")
        comp.on_frame(F.TYPE_CONTROL, 9, 1, b"HELLO;v=0.9.14;s=9;k=2;m=1;p=P1-100;f=5;j=0")
        t[0] += link.SESSION_LOST - 1
        comp.tick()
        self.assertTrue(comp.session)
        t[0] += 2
        comp.tick()
        self.assertFalse(comp.session)

    def test_an_older_addon_loaded_keeps_the_link_up(self):
        """the files are 0.9.14 but the game still runs 0.9.13 (copied without a /reload): its frames are a session,
        its HELLO says features 4, and from then on the link is up as ever (no rest) until the game process ends"""
        comp = self.comp()
        comp.on_frame(F.TYPE_CONTROL, 9, 2, b"HELLO;v=0.9.13;s=9;k=3;m=1;p=P1-100;f=4;j=0")
        self.assertTrue(comp.legacy)
        self.assertFalse(comp.standing_by())
        self.assertEqual(comp.link_state(), "online")
        self.assertFalse(comp.rest())                                 # nothing to rest: it is always on
        comp.new_process("P2-200")                                    # a restart loads the new files
        self.assertFalse(comp.legacy)
        self.assertTrue(comp.standing_by())


class Worker:
    """the loop as the Service sees it: requests run at once on the caller's thread"""

    def __init__(self):
        self.comp, self.capture, self.addons, self.blocked, self.link = None, "gdi", None, None, True
        self.capture_wanted = "gdi"
        self.settings = []                   # what the Service told it: (always, idle) ...
        self.kept = []                       # ... and the titles of the apps that keep the link, each time

    def request(self, fn, *args):
        fut = concurrent.futures.Future()
        try:
            fut.set_result(fn(*args))
        except BaseException as e:
            fut.set_exception(e)
        return fut

    def companion(self):
        return self.comp

    def link_settings(self, always, idle, by=()):
        self.settings.append((always, idle))
        self.kept.append(tuple(by))

    def game_status(self):
        return dict(found=True)


class Daemon(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.addons = Path(tmp.name) / "AddOns"
        (self.addons / "WoWBridge").mkdir(parents=True)
        MB.install(self.addons, pool=16)
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.comp = link.Companion(self.addons, log=lambda t: None)
        self.comp.standby = True
        self.comp.new_process("P1-100")
        self.svc.worker.comp = self.comp

    async def test_a_call_goes_through_to_a_link_that_stands_by(self):
        self.assertEqual(self.svc.worker.settings, [(False, link.IDLE)])     # the defaults went to the loop
        fut = asyncio.get_running_loop().create_future()

        def start():
            self.svc.ready(self.comp, "run")
            job = self.comp.code(b"return 1", "=run", "-")
            self.svc.pending[job] = (asyncio.get_running_loop(), fut)
            return job
        await self.svc.call(start)
        self.comp.tick()                                                # the link opens a session for it
        self.assertIsNotNone(self.comp.opening)
        self.assertTrue(self.comp.session)
        self.svc.open_failed("the game did not answer")               # what the Companion says after OPEN_WAIT
        with self.assertRaises(ApiError) as e:
            await fut
        self.assertEqual(e.exception.code, "link_down")
        self.assertEqual(self.svc.pending, {})

    async def test_an_older_addon_is_refused_as_before(self):
        self.comp.standby = False
        with self.assertRaises(ApiError) as e:
            self.svc.ready(self.comp, "run")
        self.assertEqual(e.exception.code, "link_down")

    async def test_the_player_ends_the_session(self):
        fut = asyncio.get_running_loop().create_future()
        self.svc.pending[7] = (asyncio.get_running_loop(), fut)
        self.svc.link_bye("hide")
        with self.assertRaises(ApiError) as e:
            await fut
        self.assertIn("/wb hide", e.exception.message)

    async def test_status_and_the_app_buttons(self):
        st = (await self.svc.status())["link"]
        self.assertEqual((st["state"], st["standby"], st["opening"], st["mode"]), ("standby", True, False, "auto"))
        self.assertNotIn("idle_minutes", st)
        res = await self.svc.link_action("open")
        self.assertEqual((res["ok"], res["state"]), (True, "standby"))   # no game here: still opening
        self.assertTrue((await self.svc.status())["link"]["opening"])
        stamp = MB.read_slot(self.addons, self.comp.open_slot)["records"]
        self.assertEqual([k for k, _ in stamp], [MB.COMMAND])          # an empty request: the stamp alone
        res = await self.svc.link_action("rest")
        self.assertTrue(res["ok"])
        self.assertFalse(self.comp.session)
        with self.assertRaises(ApiError):
            await self.svc.link_action("nap")

    async def test_settings(self):
        from wuxianworkshop.paths import settings_file
        settings_file().write_text(json.dumps(dict(idle_minutes=15)), encoding="utf-8")   # a 0.9.14 test build's
        res = await self.svc.settings(dict(link_mode="always"))
        self.assertEqual(res["link_mode"], "always")
        self.assertNotIn("idle_minutes", res)
        self.assertEqual(self.svc.worker.settings[-1], (True, link.IDLE))
        kept = json.loads(settings_file().read_text(encoding="utf-8"))
        self.assertEqual(kept["link_mode"], "always")
        self.assertNotIn("idle_minutes", kept)                       # a session ends when what passes is done
        with self.assertRaises(ApiError):
            await self.svc.settings(dict(link_mode="sometimes"))
        again = Service(lambda on_debug, log: Worker(), journal=Journal())   # the next start takes it
        self.assertEqual(again.worker.settings, [(True, link.IDLE)])

    async def test_try_keeps_the_session_through_its_window(self):
        task = asyncio.ensure_future(self.svc.try_(code="print(1)", seconds=4))
        for _ in range(100):
            if self.svc.pending:
                break
            await asyncio.sleep(0.01)
        self.comp.tick()                                              # the link opens a session for the code
        task.cancel()
        records = MB.read_slot(self.addons, self.comp.open_slot)["records"]
        said = [d for k, d in records if k == MB.COMMAND]
        self.assertTrue(any(d.startswith(b"at ") for d in said))
        self.assertIn(b"hold 9", said)                               # its 4 s and the end marker's
        self.assertEqual([k for k, _ in records][-1], MB.CODE)        # in the code's packet, before the code


if __name__ == "__main__":
    unittest.main()
