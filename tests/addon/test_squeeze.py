"""Squeezed messages (0.9.9), the addon's side: Codec.lua's Squeeze in Lua 5.1 (lupa) makes what core/squeeze.py makes,
byte for byte, block after block; end to end against the real companion, a message that takes two frames or more goes
up squeezed in fewer and comes out whole (a short one squeezed in Send, a longer one a block a step); what does not
shrink, the link tests and a companion that does not say z=1 leave it as it is."""
import random
import unittest

from tests.support.wowmock import Session, lupa
from wuxianworkshop.core import squeeze as Z
from wuxianworkshop.transport import link

DOCS = ('{"name":"tune.cvar.set","doc":"[Change: a proposal.] proposes to change a client setting (CVar) by its '
        'name; the old value is kept, so the change can be undone"},')


def noise(n, seed=1):
    rnd = random.Random(seed)
    return bytes(rnd.choice(b"abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(n))


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class SameAsPython(unittest.TestCase):
    def test_codec_squeeze_is_squeeze_block(self):
        s = Session(self)
        self.addCleanup(s.close)
        squeeze = s.ns[b"Codec"][b"Squeeze"]
        rnd = random.Random(3)
        samples = [b"", b"abc", b"abcd", b"aaaa" * 500, (DOCS * 40).encode()[:4096], ("无限工坊：用 AI 写魔兽插件。" * 120)
                   .encode()[:4096], bytes(rnd.randrange(256) for _ in range(3000)), bytes(range(256)) * 16]
        for data in samples + samples:                    # the second round on the hash array the first one left
            with self.subTest(n=len(data)):
                self.assertEqual(squeeze(data), Z.squeeze_block(data))


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class EndToEnd(unittest.TestCase):
    def start(self, **kw):
        s = Session(self)
        self.addCleanup(s.close)
        self.got = []
        s.comp = link.Companion(s.addons, clock=s.now, log=lambda text: None,
                                on_debug=lambda kind, text: self.got.append((kind, text)), **kw)
        s.comp.new_process("P1-100")
        s.login()
        s.run(8)
        return s

    def sent(self, s, text, kind=None, seconds=40):
        """Link.Send(text, kind) and wait for the companion to have it: (its parts, what Send said of them)"""
        send = s.ns[b"Link"][b"Send"]
        sess = s.comp.sessions[s.comp.current]
        t0 = s.now()
        mid, said = send(text, kind) if kind else send(text)
        while s.now() - t0 < seconds:
            s.run(0.1)
            got = [m for m in sess.messages if m[1] == mid and m[0] >= t0]
            if got:
                self.assertEqual(got[0][4].encode(), text)
                return got[0][3], said
        self.fail(f"message {mid} did not come")

    def test_a_long_message_goes_squeezed(self):
        text = (DOCS * 40).encode()                                   # 6,480 bytes: past SQUEEZE_NOW, a block a step
        plain = self.start(squeeze=False)
        parts_plain, _ = self.sent(plain, text)
        s = self.start()
        self.assertTrue(s.ns[b"Link"][b"P"][b"zip"])
        parts, said = self.sent(s, text)
        self.assertEqual(said, 0)                                     # still being squeezed when Send returned
        self.assertEqual(s.comp.stats["squeezed"], 1)
        self.assertEqual(parts, 1)
        self.assertGreaterEqual(parts_plain, 5)
        short = (DOCS * 15).encode()                                  # 2,430 bytes: squeezed in Send
        parts, said = self.sent(s, short)
        self.assertEqual((parts, said, s.comp.stats["squeezed"]), (1, 1, 2))

    def test_what_does_not_shrink_goes_as_it_is(self):
        s = self.start()
        parts, said = self.sent(s, noise(3000))
        self.assertGreaterEqual(parts, 3)
        self.assertEqual(said, parts)
        self.assertEqual(s.comp.stats["squeezed"], 0)

    def test_the_link_tests_and_an_older_companion(self):
        s = self.start()
        s.slash("long 6000")
        t0 = s.now()
        while s.comp.stats["long_ok"] < 1 and s.now() - t0 < 30:
            s.run(0.1)
        self.assertEqual((s.comp.stats["long_ok"], s.comp.stats["squeezed"]), (1, 0))   # the link as it is
        old = self.start(squeeze=False)                               # no z=1: nothing squeezed
        self.assertFalse(old.ns[b"Link"][b"P"][b"zip"])
        self.sent(old, (DOCS * 30).encode())
        self.assertEqual(old.comp.stats["squeezed"], 0)

    def test_a_run_result(self):
        s = self.start()
        job = s.comp.code(b'return string.rep("[tune.cvar.set] proposes a change. ", 100)', "=run")
        t0 = s.now()
        while not any(k == "RUN" and t.startswith(f"{job} ok") for k, t in self.got) and s.now() - t0 < 20:
            s.run(0.1)
        result = next(t for k, t in self.got if k == "RUN" and t.startswith(f"{job} ok"))
        self.assertIn("[tune.cvar.set] proposes a change. " * 100, result.replace("\\", ""))
        self.assertEqual(s.comp.stats["squeezed"], 1)


if __name__ == "__main__":
    unittest.main()
