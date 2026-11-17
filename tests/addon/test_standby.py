"""Standing by (WoWBridge 0.9.14, link features 5) end to end: the real Link.lua and Mailbox.lua under Lua 5.1 with
transport/link.py's Companion. No frame until something has to pass: a call's records open a session in one stamped
packet; the addon's own data rings the doorbell (its HELLO, which says where the frame is); the addon ends a session
after the WELCOME's idle time with a BYE, as do the player and the app's rest; /wb off drops what comes (and what came
never runs, also later), a /reload keeps standing by, and a new program ("here" from another run) ends a session."""
import unittest
from unittest import mock

from tests.support.wowmock import Session, lupa
from wuxianworkshop.core import frame as F
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.transport import link


def controls(s, since=0.0):
    """the control frames the addon drew since `since` (mock seconds), as (time, kind, payload)"""
    out = []
    for t, ftype, _, payload in s.frames:
        if ftype == F.TYPE_CONTROL and t >= since:
            out.append((t, payload.split(b";", 1)[0].decode(), payload))
    return out


def fields(payload):
    return dict(kv.split(b"=", 1) for kv in payload.split(b";")[1:] if b"=" in kv)


@unittest.skipIf(lupa is None, "lupa not installed")
class StandBy(unittest.TestCase):
    def start(self, idle=300.0, always=False, db=b'{ appNew = true, link = "standby" }'):
        """a standing-by companion and the addon logged in, as it is from its second login on: standing by"""
        self.got, self.logs, self.failed, self.byes, self.places = [], [], [], [], []
        s = Session(self, db=db)
        comp = link.Companion(s.addons, clock=s.now, log=self.logs.append, ping_every=4, hb_every=5, hb_lazy=5,
                              on_debug=lambda kind, text: self.got.append((kind, text)))
        comp.standby = True
        comp.settings(always, idle)
        comp.on_open_failed = self.failed.append
        comp.on_bye = self.byes.append
        comp.on_place = lambda *p: self.places.append(p)
        s.comp = comp
        comp.clock = s.now
        comp.new_process("P1-100")
        s.login()
        s.run(6)
        return s, comp

    def state(self, s):
        return s.ns[b"Link"][b"State"]().decode()

    def results(self):
        return [text for kind, text in self.got if kind == "RUN"]

    def call(self, s, comp, code, seconds=4):
        """an agent's call as the daemon makes it: its records in the outbox; the link opens a session for them"""
        job = comp.code(code.encode(), "=run", "-")
        s.run(seconds)
        return job

    def test_it_stands_by_and_a_call_opens_a_session_in_one_packet(self):
        s, comp = self.start()
        try:
            self.assertEqual(self.state(s), "standby")
            self.assertFalse(s.ns[b"Frame"][b"IsShown"]())
            self.assertTrue(s.lua.globals()[b"WoWBridgeDB"][b"appNew"])
            self.assertEqual(comp.link_state(), "standby")
            self.assertFalse(comp.session)
            first = MB.read_slot(s.addons, 1)
            self.assertTrue(first["records"][0][1].startswith(b"here "))
            t0 = s.now()
            job = self.call(s, comp, "return 6 * 7")
            self.assertTrue(any(r.startswith(f"{job} ok") and "42" in r for r in self.results()), self.got)
            opened = MB.read_slot(s.addons, comp.open_slot)
            self.assertTrue(opened["records"][0][1].startswith(b"at "))   # the stamp, then the code: one packet
            self.assertIn(MB.CODE, [k for k, _ in opened["records"]])
            self.assertEqual(self.state(s), "online")
            self.assertEqual(comp.link_state(), "online")
            self.assertIsNone(comp.opening)
            self.assertTrue(s.ns[b"Frame"][b"IsShown"]())
            hello = [p for _, kind, p in controls(s, t0) if kind == "HELLO"]
            self.assertTrue(hello)
            self.assertEqual(fields(hello[0])[b"o"], b"0,0")             # where the frame is: the default place
            self.assertEqual(comp.place[:2], (0, 0))
            self.assertTrue(self.places)
            # the result came about one mailbox read after the call (the mock's font load: 0.8 s), not after a handshake
            result_at = next(t for t, ftype, _, p in s.frames if ftype == F.TYPE_DATA and t >= t0)
            self.assertLess(result_at - t0, 1.5)
            self.assertNotIn("connected", s.chat().split("connected to the app")[-1])   # said once at most
        finally:
            s.close()

    def test_the_first_login_shows_hello_until_the_idle_end(self):
        """an addon that never heard an app that stands by shows its HELLO, as for any app: this one takes it as a
        session, says "here", and the addon ends the session once idle"""
        s, comp = self.start(idle=5.0, db=None)
        try:
            self.assertEqual(MB.read_slot(s.addons, 1)["records"][0][1][:5], b"here ")
            self.assertTrue(s.lua.globals()[b"WoWBridgeDB"][b"appNew"])
            s.run(10)
            self.assertEqual(self.state(s), "standby")
            self.assertFalse(comp.session)
        finally:
            s.close()

    def test_the_addon_ends_an_idle_session_and_a_later_call_opens_one(self):
        s, comp = self.start(idle=5.0)
        try:
            self.call(s, comp, "return 1")
            self.assertEqual(self.state(s), "online")
            self.assertEqual(s.ns[b"Link"][b"P"][b"idle"], 5)            # the WELCOME said so
            t0 = s.now()
            s.run(12)
            self.assertEqual(self.state(s), "standby")
            bye = [p for _, kind, p in controls(s, t0) if kind == "BYE"]
            self.assertEqual(fields(bye[0])[b"why"], b"idle")
            self.assertFalse(s.ns[b"Frame"][b"IsShown"]())
            self.assertEqual(comp.link_state(), "standby")
            self.assertFalse(comp.session)
            self.assertEqual(self.byes, [])                         # nobody to tell: nothing waited
            n = len(self.results())
            job = self.call(s, comp, "return 2 + 3")
            self.assertEqual(len(self.results()), n + 1)
            self.assertTrue(self.results()[-1].startswith(f"{job} ok"))
            self.assertEqual(self.state(s), "online")
        finally:
            s.close()

    def test_heartbeats_do_not_keep_a_session_but_requests_do(self):
        s, comp = self.start(idle=6.0)
        try:
            self.call(s, comp, "return 1")
            for _ in range(4):                                      # a call every 4 s: never 6 s with nothing passing
                self.call(s, comp, "return 2")
                self.assertEqual(self.state(s), "online")
            s.run(12)                                               # the app's heartbeats go on: not use
            self.assertEqual(self.state(s), "standby")
        finally:
            s.close()

    def test_always_opens_a_session_by_itself_and_keeps_it(self):
        s, comp = self.start(always=True)
        try:
            s.run(4)
            self.assertEqual(self.state(s), "online")
            self.assertEqual(s.ns[b"Link"][b"P"][b"idle"], 0)
            s.run(30)
            self.assertEqual(self.state(s), "online")
            comp.settings(False, 5.0)                               # on demand again: a new WELCOME says idle=5
            s.run(14)
            self.assertEqual(self.state(s), "standby")
        finally:
            s.close()

    def test_the_addons_data_rings_the_doorbell(self):
        s, comp = self.start()
        try:
            s.lua.execute(b"print('a print waits for the next session')")
            s.run(4)
            self.assertEqual(self.state(s), "standby")              # a print does not ring
            self.assertFalse(comp.session)
            t0 = s.now()
            s.lua.execute(b"WoWBridge.Bind('Bell'):Emit('rang', { n = 1 })")
            s.run(4)
            self.assertTrue(comp.session)
            self.assertEqual(self.state(s), "online")
            self.assertTrue(any(kind == "HELLO" for _, kind, _ in controls(s, t0)))
            events = [text for kind, text in self.got if kind == "EVENT"]
            self.assertTrue(any('"rang"' in e for e in events), self.got)
            self.assertTrue(any(kind == "OUT" or "a print waits" in text for kind, text in self.got), self.got)
        finally:
            s.close()

    def test_a_doorbell_nobody_answers_rings_again_later(self):
        s, comp = self.start()
        try:
            s.comp_running = False                                  # the app does not read the screen
            comp_frames = comp.on_frame
            comp.on_frame = lambda *a: None
            s.lua.execute(b"WoWBridge.Bind('Bell'):Emit('rang', { n = 1 })")
            s.run(2)
            self.assertEqual(self.state(s), "hello")
            s.run(31)
            self.assertEqual(self.state(s), "standby")              # given up after 30 s
            s.run(50)
            self.assertEqual(self.state(s), "standby")              # not at once again
            s.run(15)
            self.assertEqual(self.state(s), "hello")                # 60 s later
            comp.on_frame, s.comp_running = comp_frames, True
            s.run(6)
            self.assertEqual(self.state(s), "online")
        finally:
            s.close()

    def test_the_player_ends_the_session(self):
        s, comp = self.start()
        try:
            self.call(s, comp, "return 1")
            t0 = s.now()
            s.slash("hide")
            self.assertIn("session ended", s.chat())
            s.run(2)
            bye = [p for _, kind, p in controls(s, t0) if kind == "BYE"]
            self.assertEqual(fields(bye[0])[b"why"], b"hide")
            self.assertEqual(self.byes, ["hide"])
            self.assertFalse(comp.session)
            self.assertEqual(self.state(s), "standby")
            self.assertFalse(s.ns[b"Frame"][b"IsShown"]())
            job = self.call(s, comp, "return 9")                  # the next call opens one again
            self.assertTrue(self.results()[-1].startswith(f"{job} ok"))
        finally:
            s.close()

    def test_the_apps_rest(self):
        s, comp = self.start()
        try:
            self.call(s, comp, "return 1")
            t0 = s.now()
            self.assertTrue(comp.rest())
            self.assertFalse(comp.session)
            s.run(3)
            bye = [p for _, kind, p in controls(s, t0) if kind == "BYE"]
            self.assertEqual(fields(bye[0])[b"why"], b"rest")
            self.assertEqual(self.state(s), "standby")
            self.assertFalse(comp.session)                          # its last frames are no doorbell
            self.assertEqual(self.byes, [])
        finally:
            s.close()

    def test_a_request_as_the_addon_leaves_opens_the_next_session(self):
        s, comp = self.start(idle=5.0)
        try:
            self.call(s, comp, "return 1")
            for _ in range(200):                                    # up to the BYE of the idle end
                s.run(0.05)
                if self.state(s) == "leaving":
                    break
            self.assertEqual(self.state(s), "leaving")
            job = self.call(s, comp, "return 'again'")
            self.assertTrue(self.results()[-1].startswith(f"{job} ok"), self.got)
            self.assertEqual(self.state(s), "online")
        finally:
            s.close()

    def test_off_drops_requests_and_their_code_never_runs(self):
        s, comp = self.start()
        try:
            s.slash("off")
            self.assertEqual(self.state(s), "off")               # standing by: no frame, so the app does not know
            runs = s.ns[b"Agent"][b"stats"][b"runs"]
            self.call(s, comp, "return 'never'", seconds=14)
            self.assertEqual(len(self.failed), 1, self.logs[-5:])
            self.assertIn("WoWBridge may be off", self.failed[0])
            self.assertEqual(comp.link_state(), "standby")
            self.assertFalse(comp.outbox)
            self.assertFalse(s.ns[b"Frame"][b"IsShown"]())
            s.slash("on")
            self.assertEqual(self.state(s), "standby")
            s.run(25)                                            # the stamp is old now: nothing of it runs
            self.assertEqual(s.ns[b"Agent"][b"stats"][b"runs"], runs)
            self.assertEqual(self.results(), [])
            job = self.call(s, comp, "return 'now'")
            self.assertTrue(self.results()[-1].startswith(f"{job} ok"))
        finally:
            s.close()

    def test_a_stamped_packet_that_waited_does_not_run_its_code(self):
        """a packet that waited in the mailbox (the character screen: no addon reads it) opens nothing, and its code
        does not run when the addon gets to it"""
        failed, got = [], []
        s = Session(self, db=b'{ appNew = true, link = "standby" }')
        comp = link.Companion(s.addons, clock=s.now, log=lambda t: None, ping_every=4, hb_every=5, hb_lazy=5,
                              on_debug=lambda kind, text: got.append((kind, text)))
        comp.standby, comp.on_open_failed = True, failed.append
        s.comp = comp
        try:
            comp.new_process("P1-100")
            s.run(1)                                             # "here" in slot 1
            comp.code(b"return 'late'", "=run", "-")
            s.run(30)                                            # nobody reads: the opening fails
            self.assertEqual(len(failed), 1)
            s.login()
            s.run(8)
            self.assertEqual(s.ns[b"Link"][b"State"](), b"standby")
            self.assertEqual([t for k, t in got if k == "RUN"], [])
            self.assertEqual(s.ns[b"Agent"][b"stats"][b"runs"], 0)
            self.assertFalse(s.ns[b"Frame"][b"IsShown"]())
        finally:
            s.close()

    def test_a_packet_the_app_gave_up_on_never_runs(self):
        """the addon reads the stamped packet after the app gave up on it (a long loading screen): its code does not
        run, so the caller's link_down holds (the addon's STAMP_TTL is shorter than the app's OPEN_WAIT)"""
        failed, got = [], []
        s = Session(self, db=b'{ appNew = true, appId = "%s", link = "standby" }' % link.APP_ID.encode())   # it stood by before
        comp = link.Companion(s.addons, clock=s.now, log=lambda t: None, ping_every=4, hb_every=5, hb_lazy=5,
                              on_debug=lambda kind, text: got.append((kind, text)))
        comp.standby, comp.on_open_failed = True, failed.append
        s.comp = comp
        try:
            comp.new_process("P1-100")
            s.run(1)
            comp.code(b"return 'gave up'", "=run", "-")
            s.run(9.5)                                           # past the addon's STAMP_TTL, not yet the app's OPEN_WAIT
            self.assertEqual(failed, [])
            s.login()
            s.run(4)
            self.assertEqual(len(failed), 1)                     # 12 s after the packet
            self.assertEqual(s.ns[b"Agent"][b"stats"][b"runs"], 0)
            self.assertEqual([t for k, t in got if k == "RUN"], [])
            self.assertEqual(s.ns[b"Link"][b"State"](), b"standby")
            self.assertFalse(s.ns[b"Frame"][b"IsShown"]())
        finally:
            s.close()

    def test_reload_keeps_standing_by_and_a_session_across_it(self):
        s, comp = self.start()
        try:
            s2 = s.reload()
            s2.login()
            s2.run(4)
            self.assertEqual(self.state(s2), "standby")
            self.assertFalse(s2.ns[b"Frame"][b"IsShown"]())
            self.assertEqual([k for _, k, _ in controls(s2)], [])          # not a frame: no flash at the reload
            job = self.call(s2, comp, "return 'after reload'")
            self.assertTrue(self.results()[-1].startswith(f"{job} ok"), self.got)
            self.assertEqual(comp.current, s2.lua.eval(b"WoWBridgeDB.lastSession"))
            hello = next(p for _, kind, p in controls(s2) if kind == "HELLO")
            self.assertLess(int(fields(hello)[b"j"]), job)        # the opening's own job is not "ran in the session before"
            s3 = s2.reload()                              # a /reload in a session: back in it at once
            s3.login()
            s3.run(4)
            self.assertEqual(self.state(s3), "online")
        finally:
            s.close()

    def test_a_session_ends_when_what_passes_is_done(self):
        """the app's own idle (link.IDLE): a few seconds after the result the frame goes; the chat says nothing"""
        s, comp = self.start(idle=link.IDLE)
        try:
            said = s.chat()
            t0 = s.now()
            self.call(s, comp, "return 1", seconds=2)
            self.assertEqual(self.state(s), "online")
            s.run(5)
            self.assertEqual(self.state(s), "standby")
            bye = [t for t, kind, _ in controls(s, t0) if kind == "BYE"]
            self.assertLess(bye[0] - t0, 6)
            self.assertEqual(s.chat(), said)                       # sessions come and go unsaid
        finally:
            s.close()

    def test_hold_keeps_the_session_through_trys_window(self):
        """prints do not ring: the app's "hold <s>" (try's window) keeps the session that long"""
        s, comp = self.start(idle=link.IDLE)
        try:
            comp.hold(10)
            self.call(s, comp, "C_Timer.After(3, function() print('late') end) return 1", seconds=2)
            s.run(6)
            self.assertEqual(self.state(s), "online")              # 8 s after: held
            self.assertIn(("OUT", "late"), self.got)               # the print in the window came up
            s.run(8)
            self.assertEqual(self.state(s), "standby")             # past the hold and the idle time
        finally:
            s.close()

    def test_prints_neither_ring_nor_keep_a_session(self):
        """an addon printing every half second: the session still ends a few seconds after the result, the prints do
        not open another, and what is left of them comes up in the next session"""
        s, comp = self.start(idle=link.IDLE)
        try:
            self.call(s, comp, "C_Timer.NewTicker(0.5, function() print('tick') end) return 1", seconds=2)
            s.run(6)
            self.assertEqual(self.state(s), "standby")
            t0 = s.now()
            s.run(20)
            self.assertEqual(self.state(s), "standby")             # the prints did not ring
            self.assertFalse([c for c in controls(s, t0) if c[1] == "HELLO"])
            ticks = lambda: sum(1 for kind, text in self.got if kind == "OUT" and "tick" in text)   # (batched)
            before = ticks()
            self.call(s, comp, "return 2", seconds=3)
            self.assertGreater(ticks(), before)
            s.run(6)
            self.assertEqual(self.state(s), "standby")
        finally:
            s.close()

    def test_a_failed_opening_drops_its_hold(self):
        """the hold that rode with a request whose opening failed does not keep a later session"""
        s, comp = self.start(idle=link.IDLE)
        try:
            s.slash("off")
            comp.hold(30)
            self.call(s, comp, "return 'never'", seconds=14)
            self.assertEqual(len(self.failed), 1)
            self.assertFalse(comp.outbox)                          # neither the code nor its hold waits
            s.slash("on")
            self.call(s, comp, "return 'now'", seconds=2)
            s.run(6)
            self.assertEqual(self.state(s), "standby")
        finally:
            s.close()

    def test_the_first_app_that_stands_by_is_said_once(self):
        s, comp = self.start(idle=link.IDLE, db=None)
        try:
            s.run(6)
            chat = s.chat()
            self.assertEqual(chat.count("the Wuxian Workshop app is here"), 1, chat)
            self.assertNotIn("connected to the app", chat)
            s2 = s.reload()
            s2.login()
            s2.run(4)
            self.call(s2, comp, "return 1", seconds=2)
            s2.run(6)
            self.assertEqual(s2.chat().count("the Wuxian Workshop app is here"), 1)   # (the client's chat): once
            self.assertNotIn("connected to the app", s2.chat())
        finally:
            s.close()

    def test_unlocking_the_frame_is_a_session(self):
        s, comp = self.start(idle=3.0)
        try:
            s.slash("unlock")
            s.run(3)
            self.assertEqual(self.state(s), "online")
            self.assertTrue(comp.session)
            s.run(10)
            self.assertEqual(self.state(s), "online")              # unlocked: no idle end
            s.slash("lock")
            s.run(8)
            self.assertEqual(self.state(s), "standby")
        finally:
            s.close()

    def test_a_new_program_takes_the_session_over(self):
        s, comp = self.start()
        try:
            self.call(s, comp, "return 1")
            self.assertEqual(self.state(s), "online")
            got = []
            again = link.Companion(s.addons, clock=s.now, log=self.logs.append, ping_every=4, hb_every=5, hb_lazy=5,
                                   on_debug=lambda kind, text: got.append((kind, text)))
            again.standby = True
            again.settings(False, 300.0)                # (a long idle time: the takeover, not the end, is looked at)
            with mock.patch.object(link, "APP_ID", "another"):
                again.new_process("P1-100")             # the same game process: the slots go on
                self.assertEqual(again.slot, comp.slot)
                s.comp = again
                s.run(6)
                self.assertTrue(again.session)          # its frames: a session, welcomed
                job = again.code(b"return 'taken over'", "=run", "-")
                s.run(4)
            self.assertEqual(self.state(s), "online")
            self.assertTrue(any(k == "RUN" and t.startswith(f"{job} ok") for k, t in got), got)
            self.assertEqual(s.lua.globals()[b"WoWBridgeDB"][b"appId"], b"another")
        finally:
            s.close()

    def test_bye_ends_the_session_and_says_the_app_is_away(self):
        s, comp = self.start()
        try:
            self.call(s, comp, "return 1")
            comp.farewell()
            s.run(2)
            self.assertEqual(self.state(s), "standby")
            self.assertTrue(s.ns[b"Link"][b"AppAway"]())
            s.lua.execute(b"WoWBridge.Bind('Bell'):Emit('rang', {})")
            s.run(3)
            self.assertEqual(self.state(s), "standby")             # nobody to ring for
            comp.present()
            s.run(2)
            self.assertFalse(s.ns[b"Link"][b"AppAway"]())
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa not installed")
class OlderAddonLoaded(unittest.TestCase):
    def test_an_older_addon_still_loaded_gets_nothing_of_standing_by(self):
        """the files are 0.9.14 but the game still runs an older Link.lua (no /reload yet; its HELLO said f<5): a
        request goes through only while frames come, and no "hold" goes (the older addon prints commands it does not
        know in the chat)"""
        import tempfile
        from pathlib import Path
        from wuxianworkshop.daemon.api import ApiError
        from wuxianworkshop.daemon.service import Service
        with tempfile.TemporaryDirectory() as d:
            comp = link.Companion(Path(d), log=lambda t: None)
            comp.standby, comp.legacy, comp.session, comp.slot, comp.link_up = True, True, True, 1, False
            self.assertFalse(comp.standing_by())
            self.assertFalse(comp.takes_requests())
            with self.assertRaises(ApiError) as e:
                Service.ready(Service, comp, "run")
            self.assertEqual(e.exception.code, "link_down")
            comp.hold(10)
            self.assertFalse(comp.outbox)
            comp.link_up = True
            self.assertTrue(comp.takes_requests())
            Service.ready(Service, comp, "run")


class OlderApp(unittest.TestCase):
    def test_an_app_that_never_stamps_sees_the_addon_as_ever(self):
        """a program before 0.9.14 says neither "here" nor stamps a packet: the addon shows its HELLO and goes online
        as ever, and never ends the session itself; /wb hide says it cannot, /wb off and on work as before"""
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=lambda t: None, ping_every=4, hb_every=5, hb_lazy=5)
        s.comp = comp
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(6)
            self.assertEqual(s.ns[b"Link"][b"State"](), b"online")
            s.run(20)
            self.assertEqual(s.ns[b"Link"][b"State"](), b"online")
            s.slash("hide")
            self.assertIn("nothing to end", s.chat())
            self.assertEqual(s.ns[b"Link"][b"State"](), b"online")
            s.slash("off")
            self.assertEqual(s.ns[b"Link"][b"State"](), b"off")
            s.slash("on")
            s.run(3)
            self.assertEqual(s.ns[b"Link"][b"State"](), b"online")
            self.assertTrue(s.ns[b"Frame"][b"IsShown"]())
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
