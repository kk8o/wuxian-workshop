"""Standing by across game starts (WoWBridge 0.9.14): the game started before the app (the addon read the last process's
stamp and waits after its slots: an app that sees the process late goes on after them; one that starts it over hears
where the addon reads from its login HELLO), what WoWBridge.Online() tells other addons while standing by, and what the
app does with an older addon's PONG, a hold that goes without its request, a minimized window and /wb off."""
import unittest

from tests.support.wowmock import Session, lupa
from wuxianworkshop.core import frame as F
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.transport import link

OLD_DB = b'{ appNew = true, link = "standby", mail = { proc = "P0-1", next = 5 } }'


def last_process(addons):
    """the mailbox as the last game process left it: its stamp, slots 1-4 read"""
    MB.write_proc(addons, "P0-1")
    for slot in range(1, 5):
        MB.write_slot(addons, slot, 0, 0, [(MB.COMMAND, b"here 1 old")])


@unittest.skipIf(lupa is None, "lupa not installed")
class GameFirst(unittest.TestCase):
    def setUp(self):
        self.got, self.failed = [], []

    def companion(self, s):
        comp = link.Companion(s.addons, clock=s.now, log=lambda t: None, ping_every=4, hb_every=5, hb_lazy=5,
                              on_debug=lambda kind, text: self.got.append((kind, text)))
        comp.standby, comp.on_open_failed = True, self.failed.append
        comp.settings(False, 300.0)
        return comp

    def state(self, s):
        return s.ns[b"Link"][b"State"]().decode()

    def ok(self, s, comp, code=b"return 6 * 7"):
        job = comp.code(code, "=run", "-")
        s.run(4)
        return any(kind == "RUN" and text.startswith(f"{job} ok") for kind, text in self.got)

    def logged_in_alone(self):
        """the game started with no app: the addon read the last process's stamp and waits on slot 5"""
        s = Session(self, db=OLD_DB)
        self.addCleanup(s.close)
        last_process(s.addons)
        s.comp = None
        s.login()
        s.run(8)
        mail = s.lua.globals()[b"WoWBridgeDB"][b"mail"]
        self.assertEqual((mail[b"proc"], mail[b"next"]), (b"P0-1", 5))
        return s

    def test_an_app_that_sees_the_process_late_goes_on_after_the_last_ones_slots(self):
        s = self.logged_in_alone()
        comp = self.companion(s)
        comp.new_process("P1-100", age=60.0)
        self.assertEqual(MB.read_proc(s.addons), "P0-1")            # kept: the addon read it
        self.assertEqual(comp.slot, 5)
        s.comp = comp
        s.run(4)
        self.assertTrue(s.ns[b"Link"][b"Reachable"]())               # its "here" came
        self.assertTrue(self.ok(s, comp))
        s.run(30)                                                     # no login HELLO: the app was heard
        self.assertNotIn(b"o=", b"".join(p for t, ft, _, p in s.frames if ft == F.TYPE_CONTROL and t > s.now() - 25))

    def test_an_app_that_starts_the_mailbox_over_hears_where_the_addon_reads_from_its_login_hello(self):
        s = self.logged_in_alone()
        comp = self.companion(s)
        comp.new_process("P1-100")                                   # seen young (or an older app): from slot 1
        self.assertEqual(comp.slot, 1)
        s.comp = comp
        s.run(4)
        self.assertEqual(self.state(s), "standby")                  # slot 1 is not where it reads: nothing heard
        s.run(12)                                                     # LOGIN_QUIET after the login: the HELLO
        self.assertIn(self.state(s), ("hello", "online"))
        s.run(6)
        self.assertGreaterEqual(comp.slot, 5)                        # its k= put the app where the addon reads
        self.assertTrue(self.ok(s, comp))

    def test_no_app_at_all_shows_the_hello_a_few_seconds(self):
        s = self.logged_in_alone()
        s.run(13)                                                     # 21 s after the login
        self.assertEqual(self.state(s), "hello")
        s.run(12)
        self.assertEqual(self.state(s), "standby")
        self.assertFalse(s.ns[b"Frame"][b"IsShown"]())
        s.run(60)
        self.assertEqual(self.state(s), "standby")                  # once

    def test_data_at_login_waits_for_the_login_hello(self):
        """an extension's event right after the login, no app: the doorbell waits for LOGIN_QUIET, then 10 s of HELLO"""
        s = Session(self, db=OLD_DB)
        self.addCleanup(s.close)
        last_process(s.addons)
        s.comp = None
        s.login()
        s.run(1)
        s.lua.execute(b"WoWBridge.Bind('Bell'):Emit('rang', { n = 1 })")
        s.run(14)
        self.assertEqual(self.state(s), "standby")
        s.run(6)
        self.assertEqual(self.state(s), "hello")
        s.run(11)
        self.assertEqual(self.state(s), "standby")

    def test_data_at_login_rings_at_once_when_the_app_was_heard(self):
        s = Session(self, db=b'{ appNew = true, link = "standby" }')
        self.addCleanup(s.close)
        comp = self.companion(s)
        comp.new_process("P1-100")
        s.comp = comp
        s.login()
        s.run(3)
        s.lua.execute(b"WoWBridge.Bind('Bell'):Emit('rang', { n = 1 })")
        s.run(3)
        self.assertTrue(comp.session)
        self.assertTrue(any(kind == "EVENT" and '"rang"' in text for kind, text in self.got), self.got)

    def test_off_in_a_session_puts_the_app_back_to_standby(self):
        s = Session(self, db=b'{ appNew = true, link = "standby" }')
        self.addCleanup(s.close)
        comp = self.companion(s)
        comp.new_process("P1-100")
        s.comp = comp
        s.login()
        s.run(5)
        self.ok(s, comp)                                              # a session: its BYE (why=off) is read
        self.assertEqual(self.state(s), "online")
        s.slash("off")
        s.run(2)
        self.assertEqual(self.state(s), "off")
        self.assertFalse(s.ns[b"Frame"][b"IsShown"]())
        self.assertFalse(comp.session)
        self.assertEqual(comp.link_state(), "standby")
        s.slash("on")
        s.run(2)
        self.assertEqual(self.state(s), "standby")                  # no frame: nothing passes
        self.assertTrue(self.ok(s, comp))

    def test_a_reload_shows_no_login_hello(self):
        s = Session(self, db=OLD_DB)
        self.addCleanup(s.close)
        last_process(s.addons)
        s.comp = None
        s.login(initial=False)
        s.run(30)
        self.assertEqual(self.state(s), "standby")


@unittest.skipIf(lupa is None, "lupa not installed")
class Reachable(unittest.TestCase):
    def test_wowbridge_online_while_standing_by_with_the_app_there(self):
        s = Session(self, db=b'{ appNew = true, link = "standby" }')
        self.addCleanup(s.close)
        comp = link.Companion(s.addons, clock=s.now, log=lambda t: None, ping_every=4, hb_every=5, hb_lazy=5)
        comp.standby = True
        comp.settings(False, 300.0)
        comp.new_process("P1-100")
        s.comp = comp
        s.login()
        s.run(6)
        online = s.lua.globals()[b"WoWBridge"][b"Online"]
        self.assertEqual(s.ns[b"Link"][b"State"](), b"standby")
        self.assertTrue(online())                                     # an extension's call opens a session
        s.run(link.PRESENCE_EVERY + 5)                                 # "here" again meanwhile: still there
        self.assertTrue(online())
        comp.farewell()                                                # the app quits
        s.run(3)
        self.assertFalse(online())
        s.ns[b"Link"][b"Command"](b"here %d x" % int(s.now() * 1000))
        s.slash("off")
        self.assertFalse(online())

    def test_an_app_not_heard_for_long_is_not_there(self):
        s = Session(self, db=b'{ appNew = true, link = "standby", heardAt = 1 }')
        self.addCleanup(s.close)
        s.comp = None
        s.login(initial=False)
        s.run(3)
        self.assertFalse(s.lua.globals()[b"WoWBridge"][b"Online"]())


class AppSide(unittest.TestCase):
    """transport/link.py alone"""

    def comp(self):
        import tempfile
        from pathlib import Path
        addons = Path(tempfile.mkdtemp()) / "AddOns"
        (addons / "WoWBridge").mkdir(parents=True)
        MB.install(addons, pool=64)
        self.now = 1000.0
        comp = link.Companion(addons, clock=lambda: self.now, log=lambda t: None)
        comp.standby = True
        comp.new_process("P1-100")
        return comp

    def test_an_older_addons_pong_says_it_does_not_stand_by(self):
        comp = self.comp()
        comp.session, comp.link_up = True, True
        comp.on_frame(F.TYPE_CONTROL, 7, 0, b"PONG;id=1;k=3")       # before 0.9.9: no f=, no v=
        self.assertTrue(comp.legacy)
        self.assertFalse(comp.standing_by())

    def test_a_new_addons_pong_does_not(self):
        comp = self.comp()
        comp.on_frame(F.TYPE_CONTROL, 7, 0, b"PONG;id=1;k=3;f=%d;v=0.9.14" % link.STANDBY)
        self.assertFalse(comp.legacy)

    def test_a_hold_goes_stamped(self):
        self.assertTrue(link.Companion._stamps((MB.COMMAND, b"hold 5")))
        self.assertTrue(link.Companion._stamps((MB.CODE, b"x")))
        self.assertFalse(link.Companion._stamps((MB.COMMAND, b"here 1 x")))

    def test_minimized_opens_nothing(self):
        comp = self.comp()
        self.assertTrue(comp.can_open())
        comp.hidden = True
        self.assertFalse(comp.can_open())
        self.assertEqual(comp.link_state(), "offline")
        self.assertFalse(comp.open())

    def test_a_call_while_minimized_fails_at_once_and_is_not_kept(self):
        comp = self.comp()
        failed = []
        comp.on_open_failed = failed.append
        comp.hidden = True
        comp.code(b"return 1", "=run", "-")
        comp.tick()
        self.assertEqual(len(failed), 1)
        self.assertIn("minimized", failed[0])
        self.assertFalse([r for r in comp.outbox if link.Companion._request(r)])
        comp.hidden = False
        self.assertTrue(comp.can_open())

    def test_a_process_seen_young_starts_the_mailbox_over(self):
        comp = self.comp()
        MB.write_slot(comp.addons, 1, 0, 0, [(MB.COMMAND, b"here 1 x")])
        comp.new_process("P2-200", age=3.0)
        self.assertEqual((MB.read_proc(comp.addons), comp.slot), ("P2-200", 1))

    def test_a_process_seen_old_keeps_the_stamp(self):
        comp = self.comp()
        MB.write_slot(comp.addons, 1, 0, 0, [(MB.COMMAND, b"here 1 x")])
        comp.new_process("P2-200", age=60.0)
        self.assertEqual((MB.read_proc(comp.addons), comp.slot, comp.floor), ("P1-100", 2, 1))

    def test_an_older_addon_starts_the_mailbox_over_whatever_the_age(self):
        comp = self.comp()
        comp.standby = False
        MB.write_slot(comp.addons, 1, 0, 0, [(MB.COMMAND, b"here 1 x")])
        comp.new_process("P2-200", age=60.0)
        self.assertEqual(MB.read_proc(comp.addons), "P2-200")


if __name__ == "__main__":
    unittest.main()
