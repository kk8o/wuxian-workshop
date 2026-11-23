"""CompanionLoop and the link coming back by itself (0.10.1): the window restored, or the frame's place uncovered after
COVER_MIN s, is the window seen again (Companion.window_back); and for DOORBELL_FRESH s after the window changed the
doorbell looks over the whole window every DOORBELL_SCAN s, also where it knows the frame's place."""
import unittest
from unittest import mock

from tests.daemon import test_standby_daemon as SD
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import companion as C


class Back(unittest.TestCase):
    def setUp(self):
        self.case = SD.Loop("test_the_hello_says_the_place")           # its harness, not its tests
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addons = self.case.addons

    def rounds(self, n):
        self.case.rounds(n)

    def make(self):
        loop = self.case.make()
        self.win, self.screen = self.case.win, self.case.screen
        self.screen.clear()                                          # no frame on the screen
        self.covered = [False]
        p = mock.patch.object(C, "covered", lambda win, x, y: self.covered[0])
        p.start()
        self.addCleanup(p.stop)
        self.back = []
        return loop

    def watch(self, loop):
        real = loop.comp.window_back
        loop.comp.window_back = lambda why: (self.back.append(why), real(why))

    def heres(self):
        out = []
        for k in range(1, MB.POOL + 1):
            pkt = MB.read_slot(self.addons, k)
            if pkt is None:
                break
            out += [r for _, r in pkt["records"] if r.startswith(b"here ")]
        return len(out)

    def test_the_window_restored_says_here(self):
        loop = self.make()
        self.rounds(20)
        self.watch(loop)
        n = self.heres()
        self.win.minimized = True
        self.rounds(40)
        self.win.minimized = False
        self.rounds(2)
        self.assertEqual(self.back, ["the window restored"])
        self.assertEqual(self.heres(), n + 1)

    def test_the_frames_place_uncovered_says_here(self):
        loop = self.make()
        self.rounds(20)
        self.watch(loop)
        n = self.heres()
        self.covered[0] = True
        self.rounds(20)                                              # 1 s: too short to count
        self.covered[0] = False
        self.rounds(40)
        self.assertEqual((self.back, self.heres()), ([], n))
        self.covered[0] = True
        self.rounds(100)                                             # 5 s
        self.covered[0] = False
        self.rounds(40)
        self.assertEqual(len(self.back), 1)
        self.assertIn("uncovered", self.back[0])
        self.assertEqual(self.heres(), n + 1)

    def test_after_the_window_changed_the_doorbell_looks_over_it_often(self):
        loop = self.make()
        loop.bell = (800, 400)                                       # the place is known
        self.rounds(1200)                                            # 60 s after the new process
        early = len([c for c in self.screen.calls if c[0] is None])
        n = len(self.screen.calls)
        self.rounds(1200)
        late = len([c for c in self.screen.calls[n:] if c[0] is None])
        self.assertGreaterEqual(early, 10)                           # every DOORBELL_SCAN s
        self.assertLessEqual(late, 3)                                # every DOORBELL_SCAN_KNOWN s

