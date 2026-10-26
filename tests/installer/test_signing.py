"""Signatures of what the program takes from the website (signing.py): a file is taken only with a signature by a key in
KEYS beside it, a local path unsigned; the command line makes a key, signs with a key the program accepts only, and checks
files and URLs."""
import base64
import io
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests.installer import signed
from wuxianworkshop import signing


class Signatures(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        keys = signed.keys()
        keys.start()
        self.addCleanup(keys.stop)

    def test_verify(self):
        f = self.dir / "manifest.json"
        f.write_bytes(b'{"packs": {}}')
        signed.sign(f)
        sig = (self.dir / "manifest.json.sig").read_bytes()
        self.assertEqual(signing.verify(f.read_bytes(), sig), "test")
        for data, bad in ((b'{"packs": {"api": {}}}', sig),           # the file changed after it was signed
                          (f.read_bytes(), b""), (f.read_bytes(), b"not base64!"),
                          (f.read_bytes(), base64.b64encode(b"short")), (f.read_bytes(), b"\xff\xfe")):
            with self.assertRaises(signing.BadSignature):
                signing.verify(data, bad)
        signed.sign(f, signed.OTHER)                                   # by a key the program does not accept
        other = (self.dir / "manifest.json.sig").read_bytes()
        with self.assertRaises(signing.BadSignature):
            signing.verify(f.read_bytes(), other)
        self.assertEqual(signing.verify(f.read_bytes(), other + sig), "test")    # one good line is enough (a new key)

    def test_fetch_signed(self):
        site = self.dir / "site"
        site.mkdir()
        (site / "extensions.json").write_bytes(b'{"addons": []}')
        fetch, url = signed.fetcher(site), signed.BASE + "extensions.json"
        with self.assertRaisesRegex(signing.BadSignature, "no signature beside it"):
            signing.fetch_signed(url, 1000, fetch)
        signed.sign(site / "extensions.json")
        self.assertEqual(signing.fetch_signed(url, 1000, fetch), b'{"addons": []}')
        (site / "extensions.json").write_bytes(b'{"addons": [{}]}')    # replaced on the server, the signature left
        with self.assertRaisesRegex(signing.BadSignature, "not signed by a key the program accepts"):
            signing.fetch_signed(url, 1000, fetch)

        def unavailable(u, limit):                                     # a failure on the way is not a verdict
            if u.endswith(".sig"):
                raise urllib.error.HTTPError(u, 503, "Service Unavailable", None, None)
            return b"{}"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            signing.fetch_signed(url, 1000, unavailable)
        cm.exception.close()
        local = site / "local.json"                                    # a local path (the WUXIAN_*_FEED overrides): unsigned
        local.write_bytes(b"[]")
        self.assertEqual(signing.fetch_signed(str(local), 1000, lambda u, limit: Path(u).read_bytes()), b"[]")

    def test_the_command_line(self):
        key = self.dir / "keys" / "release.pem"
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(signing.main(["keygen", str(key)]), 0)
        public = out.getvalue().strip()
        self.assertEqual(len(base64.b64decode(public)), 32)           # the public key only
        self.assertIn("PRIVATE KEY", key.read_text(encoding="ascii"))
        with self.assertRaises(FileExistsError):                       # a key file is never overwritten
            signing.main(["keygen", str(key)])
        f = self.dir / "releases.win.json"
        f.write_text('{"Assets": []}', encoding="utf-8")
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(signing.main(["sign", str(key), str(f)]), 1)          # not a key in KEYS
        self.assertIn("not in KEYS", err.getvalue())
        self.assertFalse(f.with_name(f.name + ".sig").exists())
        with mock.patch.dict(signing.KEYS, {"new": public}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(signing.main(["sign", str(key), str(f)]), 0)
            self.assertEqual(signing.main(["verify", str(f)]), 0)
            with mock.patch("wuxianworkshop.content.fetch", signed.fetcher(self.dir)):
                self.assertEqual(signing.main(["verify", signed.BASE + "releases.win.json"]), 0)   # what the website gives
        self.assertIn("signed by new", out.getvalue())
        with redirect_stderr(io.StringIO()):
            self.assertEqual(signing.main(["verify", str(f)]), 1)                  # the key no longer accepted


class Keys(unittest.TestCase):
    def test_the_program_accepts_the_release_key_and_a_spare(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        self.assertGreaterEqual(len(signing.KEYS), 2)
        self.assertEqual(len(set(signing.KEYS.values())), len(signing.KEYS))
        for key in signing.KEYS.values():
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True))


if __name__ == "__main__":
    unittest.main()
