"""wuxianwow.com as the update tests see it: a folder served under a fake https:// address (fetcher), its JSON signed
with a throwaway key that keys() makes the only one signing.KEYS holds for the test, as deploy/sync_workshop.py signs
them with the release key (signing.py)."""
import urllib.error
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from wuxianworkshop import signing

KEY = Ed25519PrivateKey.generate()
OTHER = Ed25519PrivateKey.generate()          # a key the program does not accept
BASE = "https://example.invalid/"


def keys():
    """signing.KEYS holding the test key alone (a patch)"""
    return mock.patch.object(signing, "KEYS", {"test": signing.public_key(KEY)})


def sign(path, key=KEY):
    """writes path.sig, the signature of the file by key"""
    signing.sign_file(path, key, {"signer": signing.public_key(key)})


def fetcher(folder, base=BASE):
    """content.fetch for the files of folder under base (a missing one answers 404, as the website does) and for local
    paths"""
    def fetch(url, limit=None):
        if not signing.remote(url):
            return Path(url).read_bytes()
        assert url.startswith(base), url
        path = Path(folder) / url[len(base):]
        if not path.is_file():
            err = urllib.error.HTTPError(url, 404, "Not Found", None, None)
            err.close()                       # no body to read: nothing left open when it is dropped
            raise err
        return path.read_bytes()
    return fetch
