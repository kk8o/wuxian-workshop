"""The companion's side of a pour (wuxianworkshop/transport/link.py, WoWBridge 0.9.13, link features POUR): a segment
comes as a TYPE_POUR message, alone or squeezed (TYPE_ZIP) in parts, and goes to on_pour as the bytes it carries, never
decoded; what is not a segment is noted and dropped; a segment is acknowledged within POUR_ACK, other messages of a lazy
addon within ack_lazy as before."""
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop.core import frame as F, mailbox as MB, squeeze
from wuxianworkshop.transport import link

HELLO = b"HELLO;v=0.9.13;s=5;k=1;m=1;p=P1-1;f=4;j=0"


def segment(key, i, m, body):
    return bytes([link.TYPE_POUR]) + f"{key} {i}/{m}\n".encode() + body


class Pour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addons = Path(self.tmp.name)
        (self.addons / "WoWBridge").mkdir()
        MB.install(self.addons, pool=64)
        self.t = 100.0
        self.log, self.got, self.poured = [], [], []
        self.c = link.Companion(self.addons, clock=lambda: self.t, log=self.log.append,
                                on_debug=lambda kind, text: self.got.append((kind, text)))
        self.c.on_pour = lambda *a: self.poured.append(a)
        self.c.new_process("P1-1")
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.c.tick()

    def tearDown(self):
        self.tmp.cleanup()

    def frame(self, ftype, msg, payload, sid=5, part=0, parts=1):
        self.c.on_frame(ftype, sid, msg, payload, part, parts)

    def wait(self, seconds, step=0.25):
        end = self.t + seconds
        while self.t < end:
            self.t = round(self.t + step, 3)
            self.frame(F.TYPE_CONTROL, 1000, b"HB;n=1")
            self.c.tick()

    def test_what_the_addon_can_do(self):
        self.assertTrue(self.c.pours() and self.c.data_calls() and self.c.zipping())
        self.assertEqual(self.c.report()["sessions"][0]["pour"], True)
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.9.12;s=6;k=1;m=1;p=P1-1;f=3;j=0", sid=6)
        self.assertFalse(self.c.pours())                          # 0.9.9 - 0.9.12: the rest piece by piece
        self.assertTrue(self.c.data_calls())

    def test_segments_come_as_their_bytes(self):
        """one in a frame, one squeezed in parts; bytes that are not UTF-8 (a segment of a game's answer ends between two
        characters, but what it holds is the game's) come as they are"""
        odd = b"\xff\xfe not text \xe6\x97"
        self.frame(F.TYPE_DATA, 1, segment("ab12", 1, 2, odd))
        long = "".join(f"{i * 7919 % 100003}\t拍卖行 {i % 97}\t1:{i * 31 % 20000}:0:3;\n" for i in range(300)).encode()
        zipped = bytes([link.TYPE_ZIP]) + squeeze.squeeze(segment("ab12", 2, 2, long))
        self.assertLess(len(zipped), len(long))
        parts = [zipped[i:i + 300] for i in range(0, len(zipped), 300)]
        self.assertGreater(len(parts), 1)
        for i, p in enumerate(parts):
            self.frame(F.TYPE_DATA, 2, p, part=i, parts=len(parts))
        self.assertEqual(self.poured, [(5, "ab12", 1, 2, odd), (5, "ab12", 2, 2, long)])
        st = self.c.stats
        self.assertEqual((st["poured"], st["poured_bytes"], st["squeezed"]), (2, len(odd) + len(long), 1))
        self.assertEqual(self.got, [])                            # not a RUN result, not a debug message
        s = self.c.sessions[5]
        self.assertEqual([m[4] for m in s.messages], ["POUR ab12 1/2 (14 B)", f"POUR ab12 2/2 ({len(long)} B)"])
        self.assertEqual(s.ack, 2)

    def test_what_is_not_a_segment(self):
        for msg, data in enumerate((b"ab12 1/2", b"ab12 0/2\nx", b"ab12 3/2\nx", b"a b 1/2\nx", b"ab12 1-2\nx",
                                    b"k" * 65 + b" 1/2\nx"), 1):
            self.frame(F.TYPE_DATA, msg, bytes([link.TYPE_POUR]) + data)
        self.assertEqual(self.poured, [])
        self.assertEqual(sum("not a segment of a pour" in line for line in self.log), 6)
        self.assertEqual(self.c.sessions[5].ack, 6)               # taken all the same: the addon need not send them again

    def test_a_segment_is_acknowledged_soon(self):
        """the addon queues the next segments as the acks come (at most two waiting): they come within POUR_ACK, not
        ack_lazy"""
        self.assertEqual(self.c.slot, 2)                          # the WELCOME
        self.frame(F.TYPE_DATA, 1, segment("ab12", 1, 9, b"x" * 100))
        self.wait(link.POUR_ACK - 0.5)
        self.assertEqual(self.c.slot, 2)
        self.wait(1)
        self.assertEqual((self.c.slot, MB.read_slot(self.addons, 2)["ack"]), (3, 1))
        self.frame(F.TYPE_DATA, 2, b"\x01OUT not urgent")
        self.wait(self.c.ack_lazy - 1)
        self.assertEqual(self.c.slot, 3)                          # a print waits ack_lazy as before
        self.wait(1.5)
        self.assertEqual((self.c.slot, MB.read_slot(self.addons, 3)["ack"]), (4, 2))


if __name__ == "__main__":
    unittest.main()
