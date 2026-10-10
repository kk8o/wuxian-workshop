"""core/squeeze.py: the blocks of a squeezed message (TYPE_ZIP) come out as they went in; what does not shrink is stored;
blocks that do not add up are a ValueError, never a wrong message."""
import os
import random
import unittest

from wuxianworkshop.core import squeeze as Z


class Squeeze(unittest.TestCase):
    def test_round_trips(self):
        rnd = random.Random(5)
        for data in (b"", b"a", b"abcd", b"aaaa" * 1000, os.urandom(9000), ('{"a":1,"b":"two"},' * 900).encode(),
                     "无限工坊 测试。".encode() * 700, bytes(rnd.choice(b"ab") for _ in range(10000))):
            with self.subTest(n=len(data)):
                self.assertEqual(Z.unsqueeze(Z.squeeze(data)), data)

    def test_blocks_and_what_is_stored(self):
        text = ('{"name":"kit.revision","doc":"reads the revision"},' * 200).encode()   # 10,400 bytes: 3 blocks
        z = Z.squeeze(text)
        self.assertLess(len(z), len(text) // 10)
        noise = os.urandom(5000)                                    # nothing to gain: stored, 2 bytes a block more
        z = Z.squeeze(noise)
        self.assertEqual(len(z), len(noise) + 4)
        self.assertTrue(z[0] & 0x80)
        self.assertEqual(Z.unsqueeze(z), noise)

    def test_long_lengths_and_overlapping_matches(self):
        data = b"x" * 70 + b"y" * 300 + bytes(range(40)) * 3              # runs past 15 and 255, a match onto itself
        block = Z.squeeze_block(data)
        self.assertEqual(Z.unsqueeze_block(block), data)
        self.assertLess(len(block), 80)

    def test_what_does_not_add_up(self):
        good = Z.squeeze(("abc" * 500).encode())
        for bad in (good[:1], good[:-3], b"\x00\x05\x10ab", b"\x00\x04\x00\x05\x00\x00"):
            with self.subTest(bad=bad[:8]):
                with self.assertRaises(ValueError):
                    Z.unsqueeze(bad)


if __name__ == "__main__":
    unittest.main()
