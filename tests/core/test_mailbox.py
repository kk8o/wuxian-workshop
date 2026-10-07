"""The font mailbox packets (wuxianworkshop/core/mailbox.py), in memory and as fonts in the slots on disk."""
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop.core import mailbox as MB


class Packets(unittest.TestCase):
    def test_roundtrip(self):
        recs = [(MB.WELCOME, b"v=0.5.0;mode=1"), (MB.TEXT, "你好".encode()), (MB.HEARTBEAT, b"")]
        data = MB.encode(17, 0xBEEF, 4242, recs)
        self.assertEqual(MB.decode(data), dict(slot=17, session=0xBEEF, ack=4242, records=recs))
        self.assertIsNone(MB.decode(data[:-1] + bytes([data[-1] ^ 1])))       # CRC-32
        self.assertIsNone(MB.decode(b"WF" + data[2:]))
        with self.assertRaises(ValueError):
            MB.encode(1, 1, 1, [(MB.TEXT, bytes(MB.MAX_PACKET))])

    def test_slots_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            addons = Path(tmp)
            (addons / "WoWBridge").mkdir()
            MB.install(addons, pool=8)
            self.assertEqual(len(list(MB.mail_dir(addons).glob("*.ttf"))), 9)            # 8 slots + proc.ttf
            self.assertIsNone(MB.read_slot(addons, 3))                                     # empty
            MB.write_slot(addons, 3, 7, 9, [(MB.COMMAND, b"ping 1")])
            self.assertEqual(MB.read_slot(addons, 3), dict(slot=3, session=7, ack=9, records=[(MB.COMMAND, b"ping 1")]))
            self.assertEqual(MB.reset(addons), 1)
            self.assertIsNone(MB.read_slot(addons, 3))


if __name__ == "__main__":
    unittest.main()
