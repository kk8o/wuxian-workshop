"""Inflate.lua (raw DEFLATE decoding in the addon, 0.9.9) against zlib's own output, under Lua 5.1: the addons' real Lua
at every strategy and several levels, JSON, random bytes (stored blocks, one past 64 KB), long runs (copies that overlap
what they copy), a stream that sync-flushes (empty stored blocks between Huffman ones), many random mixes; a stream cut
short or damaged raises an error or comes out as it was, never hangs and never grows past `most` (Agent.lua then checks
the size and the Adler-32 against the job's header); Adler32 is zlib's."""
import random
import unittest
import zlib
from pathlib import Path

try:
    import lupa.lua51 as lupa
except ImportError:                     # pragma: no cover
    lupa = None

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "addon"
STRATEGIES = (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED, zlib.Z_HUFFMAN_ONLY, zlib.Z_RLE, zlib.Z_FIXED)


def deflate(data, level=9, strategy=zlib.Z_DEFAULT_STRATEGY, mem=9):
    c = zlib.compressobj(level, zlib.DEFLATED, -15, mem, strategy)
    return c.compress(data) + c.flush()


def lua_files():
    return sorted(p for folder in ("WoWBridge", "!WuxianWorkshop") for p in (ADDON / folder).glob("*.lua"))


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Inflate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = lupa.LuaRuntime(encoding=None)
        ns = cls.lua.table()
        load = cls.lua.globals()[b"loadstring"]
        fn = load((ADDON / "WoWBridge" / "Inflate.lua").read_bytes(), b"@WoWBridge/Inflate.lua")
        assert fn is not None, "Inflate.lua does not compile"
        fn(b"WoWBridge", ns)
        cls.raw, cls.adler = ns[b"Inflate"][b"Raw"], ns[b"Inflate"][b"Adler32"]

    def inflate(self, stream, most=None):
        return bytes(self.raw(stream, most))

    def same(self, data, **kw):
        self.assertEqual(self.inflate(deflate(data, **kw), len(data)), data)

    def test_addon_code(self):
        files = lua_files()
        self.assertGreater(len(files), 15)
        for p in files:
            with self.subTest(p.name):
                self.same(p.read_bytes())
        sample = (ADDON / "WoWBridge" / "Link.lua").read_bytes()
        for strategy in STRATEGIES:
            for level in (1, 6, 9):
                with self.subTest(strategy=strategy, level=level):
                    self.same(sample, level=level, strategy=strategy)

    def test_json_and_text(self):
        data = ('{"a":"Foo","n":"echo","d":{"s":"' + "两个字" * 900 + '","list":[' + ",".join(str(i) for i in range(2000))
                + "]}}").encode()
        self.same(data)
        self.same("中文 English 混在一起\n".encode() * 500)

    def test_small_and_empty(self):
        for data in (b"", b"a", b"ab", b"\x00", b"\xff" * 3, bytes(range(256))):
            with self.subTest(data=data[:8]):
                self.same(data)

    def test_random_bytes_go_in_stored_blocks(self):
        rnd = random.Random(7)
        for n in (100, 5000, 65535, 70000):
            data = bytes(rnd.getrandbits(8) for _ in range(n))
            with self.subTest(n=n):
                self.same(data)
                self.same(data, level=0)                                 # nothing but stored blocks

    def test_runs_copy_what_they_are_copying(self):
        self.same(b"a" * 100000)                                            # distance 1, lengths of 258 over and over
        self.same(b"ab" * 50000)
        self.same((b"x" * 40000) + b"y" + (b"x" * 40000))                   # distances near 32 KB
        self.same(bytes(range(256)) * 300, strategy=zlib.Z_RLE)

    def test_a_stream_of_many_blocks(self):
        rnd = random.Random(11)
        parts = [lua_files()[0].read_bytes(), bytes(rnd.getrandbits(8) for _ in range(3000)), b"z" * 5000, b"tail"]
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        stream = b""
        for p in parts:
            stream += c.compress(p) + c.flush(zlib.Z_SYNC_FLUSH)           # an empty stored block after each
        stream += c.flush()
        self.assertEqual(self.inflate(stream), b"".join(parts))

    def test_random_mixes(self):
        rnd = random.Random(2026)
        words = [w for p in lua_files()[:4] for w in p.read_bytes().split()][:3000]
        for i in range(120):
            n = rnd.randrange(0, 12000)
            out = bytearray()
            while len(out) < n:
                if rnd.random() < 0.7:
                    out += rnd.choice(words) + b" "
                else:
                    out += bytes(rnd.getrandbits(8) for _ in range(rnd.randrange(1, 40)))
            data = bytes(out[:n])
            with self.subTest(i=i, n=n):
                self.same(data, level=rnd.choice((1, 5, 9)), strategy=rnd.choice(STRATEGIES))

    def test_a_cut_or_damaged_stream_is_caught(self):
        data = (ADDON / "WoWBridge" / "Agent.lua").read_bytes()
        stream = deflate(data)
        rnd = random.Random(5)
        for cut in sorted(rnd.sample(range(0, len(stream) - 1), 40)) + [len(stream) - 2]:
            with self.subTest(cut=cut):
                try:
                    out = self.inflate(stream[:cut], len(data))
                except lupa.LuaError:
                    continue
                self.assertEqual(out, data[:len(out)] if len(out) < len(data) else data)
        damaged = 0
        for i in range(150):
            b = bytearray(stream)
            for _ in range(rnd.randrange(1, 4)):
                b[rnd.randrange(len(b))] ^= 1 << rnd.randrange(8)
            try:
                out = self.inflate(bytes(b), len(data))
            except lupa.LuaError:
                damaged += 1
                continue
            self.assertLessEqual(len(out), len(data))                       # most holds
            if out != data:
                damaged += 1                                                # the size and checksum catch these
        self.assertGreater(damaged, 140)

    def test_most(self):
        with self.assertRaises(lupa.LuaError) as e:
            self.inflate(deflate(b"x" * 1000), 999)
        self.assertIn("longer than said", str(e.exception))
        self.assertEqual(self.inflate(deflate(b"x" * 1000), 1000), b"x" * 1000)

    def test_adler32(self):
        rnd = random.Random(3)
        for n in (0, 1, 2, 3999, 4000, 4001, 5551, 5552, 5553, 100000):
            data = bytes(rnd.getrandbits(8) for _ in range(n))
            with self.subTest(n=n):
                self.assertEqual(int(self.adler(data)), zlib.adler32(data))
        self.assertEqual(int(self.adler(b"\xff" * 100000)), zlib.adler32(b"\xff" * 100000))   # the largest sums


if __name__ == "__main__":
    unittest.main()
