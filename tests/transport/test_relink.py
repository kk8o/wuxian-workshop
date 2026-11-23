"""The link coming back by itself (0.10.1), on the Companion alone: the game window seen again says "here" (at most
every BACK_EVERY s) or, with a session always wanted, opens one at once; openings the game did not answer are counted
in a row; and a game process the app saw late goes on after the last one's slots unless fewer than OLD_ROOM are left."""
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop.core import frame as F, mailbox as MB
from wuxianworkshop.transport import link


class WindowBack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addons = Path(self.tmp.name)
        (self.addons / "WoWBridge").mkdir()
        MB.install(self.addons, pool=64)
        self.t = 100.0
        self.failed = []
        self.c = link.Companion(self.addons, clock=lambda: self.t, log=lambda text: None)
        self.c.standby = True
        self.c.on_open_failed = self.failed.append
        self.c.new_process("P1-1")
        self.c.tick()                                   # "here" first

    def tearDown(self):
        self.tmp.cleanup()

    def last(self):
        return MB.read_slot(self.addons, self.c.slot - 1)["records"][0][1]

    def test_the_window_back_says_here_now_and_then(self):
        n = self.c.slot
        self.t += 30
        self.c.window_back("restored")
        self.assertEqual(self.c.slot, n + 1)
        self.assertTrue(self.last().startswith(b"here "))
        self.t += link.BACK_EVERY - 1
        self.c.window_back("restored")
        self.assertEqual(self.c.slot, n + 1)            # not again so soon
        self.t += 2
        self.c.window_back("restored")
        self.assertEqual(self.c.slot, n + 2)

    def test_nothing_while_minimized_or_in_a_session(self):
        n = self.c.slot
        self.c.hidden = True
        self.c.window_back("restored")
        self.c.hidden = False
        self.c.session = True
        self.c.window_back("restored")
        self.assertEqual(self.c.slot, n)

    def test_always_opens_at_once(self):
        self.c.settings(True, 0)
        self.c.tick()
        self.assertIsNotNone(self.c.opening)            # the first opening for always
        self.t += link.OPEN_WAIT + 1
        self.c.tick()                                   # no frames: failed
        self.assertIsNone(self.c.opening)
        self.t += 5
        self.c.tick()
        self.assertIsNone(self.c.opening)               # the next one at ALWAYS_AGAIN ...
        self.c.window_back("restored")
        self.c.tick()
        self.assertIsNotNone(self.c.opening)            # ... or now, the window back

    def test_unanswered_openings_are_counted_until_frames_come(self):
        for i in range(link.UNANSWERED_WARN):
            self.assertTrue(self.c.open("a call"))
            self.t += link.OPEN_WAIT + 1
            self.c.tick()
            self.assertEqual(self.c.unanswered, i + 1)
        self.assertIn("in a row", self.failed[-1])
        self.c.open("a call")
        self.t += 1
        self.c.on_frame(F.TYPE_CONTROL, 5, 0, b"HELLO;v=0.10.1;f=5;k=9;m=1")
        self.c.tick()
        self.assertEqual(self.c.unanswered, 0)
        self.c.new_process("P2-2")
        self.assertEqual(self.c.unanswered, 0)


class LateProcess(unittest.TestCase):
    """the app first sees a game process that ran a while: its addon may have read the last process's stamp"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addons = Path(self.tmp.name)
        (self.addons / "WoWBridge").mkdir()
        MB.install(self.addons, pool=8)
        MB.write_proc(self.addons, "P1-1")

    def tearDown(self):
        self.tmp.cleanup()

    def companion(self, last):
        MB.write_slot(self.addons, last, 0, 0, [(MB.COMMAND, b"here 1 x")])
        c = link.Companion(self.addons, clock=lambda: 100.0, log=lambda text: None)
        c.standby = True
        c.new_process("P2-2", age=60)
        return c

    def test_it_goes_on_after_the_last_processs_slots(self):
        c = self.companion(MB.POOL - link.OLD_ROOM - 10)
        self.assertEqual((c.slot, MB.read_proc(self.addons)), (MB.POOL - link.OLD_ROOM - 9, "P1-1"))
        self.assertLess(link.OLD_ROOM, 512)              # 0.10.0 started over with 512 left (an addon read on above)

    def test_with_hardly_any_room_left_it_starts_over(self):
        c = self.companion(MB.POOL - link.OLD_ROOM + 10)
        self.assertEqual((c.slot, MB.read_proc(self.addons)), (1, "P2-2"))


if __name__ == "__main__":
    unittest.main()
