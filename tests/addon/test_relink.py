"""The link coming back by itself (WoWBridge 0.10.1) end to end, the real Link.lua and Mailbox.lua with link.py's
Companion: any packet ends the doorbell's backoff, so the window seen again ("here") lets what waits ring at once; an
addon that stands by and hears nothing for PROBE_AFTER s from an app that should be there shows its HELLO, which says
the mailbox slot it reads, and an app that writes elsewhere learns it; while the app says "here" now and then, or said
"bye", nothing shows."""
import unittest

from tests.addon import test_standby as TS
from tests.support.wowmock import lupa

PROBE_AFTER = 1260


@unittest.skipIf(lupa is None, "lupa not installed")
class Relink(unittest.TestCase):
    def start(self, **kw):
        self.h = TS.StandBy("test_it_stands_by_and_a_call_opens_a_session_in_one_packet")   # its harness
        s, comp = self.h.start(**kw)
        self.addCleanup(s.close)
        return s, comp

    def state(self, s):
        return s.ns[b"Link"][b"State"]().decode()

    def hellos(self, s, since):
        return [p for _, kind, p in TS.controls(s, since) if kind == "HELLO"]

    def reads(self, s):
        return int(s.lua.globals()[b"WoWBridgeDB"][b"mail"][b"next"])

    def test_the_window_back_ends_the_doorbells_backoff(self):
        s, comp = self.start()
        s.comp_running = False                                  # the window covered: frames are not read
        seen = comp.on_frame
        comp.on_frame = lambda *a: None
        s.lua.execute(b"WoWBridge.Bind('Bell'):Emit('rang', { n = 1 })")
        s.run(33)
        self.assertEqual(self.state(s), "standby")              # nobody answered: it rings again in 60 s
        comp.on_frame, s.comp_running = seen, True
        comp.window_back("the frame's place uncovered")         # "here": the backoff is over
        s.run(4)
        self.assertEqual(self.state(s), "online")
        self.assertTrue(any('"rang"' in text for kind, text in self.h.got if kind == "EVENT"), self.h.got)

    def test_an_addon_that_reads_elsewhere_says_where(self):
        s, comp = self.start(idle=3.0)
        for _ in range(6):                                      # sessions: the addon reads on, a dozen slots up
            self.h.call(s, comp, "return 1")
            s.run(6)
        k = self.reads(s)
        self.assertGreater(k, 8)
        comp.slot, comp.written, comp.floor = 2, {}, 2          # the app writes where the addon has read already
        job = self.h.call(s, comp, "return 6 * 7", seconds=14)
        self.assertTrue(self.h.failed)                          # the game did not answer
        self.assertFalse(any(r.startswith(f"{job} ok") for r in self.h.results()))
        t0 = s.now()
        s.run(PROBE_AFTER - 30, dt=0.5)
        self.assertEqual(self.hellos(s, t0), [])                # not yet
        s.run(60, dt=0.25)
        self.assertTrue(self.hellos(s, t0))                     # the HELLO says where it reads ...
        self.assertGreaterEqual(comp.slot, k)                   # ... and the app writes there now
        s.run(6)
        job = self.h.call(s, comp, "return 6 * 7")
        self.assertTrue(any(r.startswith(f"{job} ok") and "42" in r for r in self.h.results()), self.h.got)

    def test_nothing_shows_while_the_app_says_here_or_after_its_bye(self):
        s, comp = self.start()
        t0 = s.now()
        s.run(PROBE_AFTER + 120, dt=0.5)                        # the app says "here" every 600 s
        self.assertEqual(self.hellos(s, t0), [])
        self.assertEqual(self.state(s), "standby")
        comp.farewell()                                         # the app quits
        s.comp_running = False
        t1 = s.now()
        s.run(PROBE_AFTER + 120, dt=0.5)
        self.assertEqual(self.hellos(s, t1), [])


if __name__ == "__main__":
    unittest.main()
