r"""Signatures of what the program takes from the website: the update feed (releases.<channel>.json, updater.py), the
content packs' manifest (manifest.json, content.py) and the extension catalog (extensions.json, extensions.py). Each has
its signature beside it, <name>.sig: a line per signature, the base64 of an Ed25519 signature of the file's bytes. What
they list (the update packages, the packs, the addon zips) is checked by the size and SHA-256 they give.

They are signed on the publisher's PC just before the upload (the website's deploy/sync_workshop.py runs `sign`); the
private key never goes to the server, so whoever takes the server or its files still cannot make the program install
anything. KEYS are the public keys the program accepts: the release key, and a spare kept offline that signs a release
naming a new key should the release key be lost.

A local path (WUXIAN_UPDATE_FEED, WUXIAN_CONTENT_FEED, WUXIAN_EXTENSIONS_FEED: tests, and trying a release before it is
uploaded) is taken as it is.

    python -m wuxianworkshop.signing keygen <private key file>          # a new key: its private half into the file
                                                                        # (never overwritten), the public half printed
    python -m wuxianworkshop.signing sign <private key file> <file>...  # writes <file>.sig; only with a key in KEYS
    python -m wuxianworkshop.signing verify <file or URL>...            # checks each against the .sig beside it
"""
import argparse
import base64
import os
import sys
import urllib.error
import urllib.parse
from pathlib import Path

KEYS = {                          # name: the raw Ed25519 public key, base64
    "release-2026-10": "uVOzIrgcT8bzPnyN343VrwWhKvEGde8yubuJ5s9SIMg=",
    "spare-2026-10": "1GadZUUBnyzxyb4v4Q4iBV38bhYqw7iv0LStJ9i3uBU=",
}
SUFFIX = ".sig"
MAX_SIG = 4096


class BadSignature(Exception):
    """a file from the website has no signature beside it, or none by a key in KEYS"""


def remote(url):
    return urllib.parse.urlsplit(str(url)).scheme in ("http", "https")


def verify(data, sig, keys=None):
    """the name of the key in keys (default KEYS) that signed data; BadSignature when none did. sig: the .sig's bytes"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        lines = sig.decode("ascii").split()
    except UnicodeDecodeError:
        lines = []
    for line in lines:
        try:
            raw = base64.b64decode(line, validate=True)
        except ValueError:
            continue
        if len(raw) != 64:
            continue
        for name, key in (KEYS if keys is None else keys).items():
            try:
                Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(raw, data)
                return name
            except InvalidSignature:
                pass
    raise BadSignature("not signed by a key the program accepts")


def fetch_signed(url, limit, fetcher):
    """the bytes at url (fetcher(url, limit), content.fetch) once the signature beside them checks out; a local path's
    as they are"""
    data = fetcher(url, limit)
    if not remote(url):
        return data
    try:
        sig = fetcher(url + SUFFIX, MAX_SIG)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        e.close()
        raise BadSignature(f"{url}: no signature beside it") from None
    try:
        verify(data, sig)
    except BadSignature as e:
        raise BadSignature(f"{url}: {e}") from None
    return data


def public_key(private):
    """the base64 raw public key of a private one (what KEYS holds)"""
    from cryptography.hazmat.primitives import serialization
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def load_private(path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{path}: not an Ed25519 private key")
    return key


def keygen(path):
    """a new key pair: the private key into path as PEM (an existing file is never overwritten); the public key"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as fh:
        fh.write(pem)
    return public_key(key)


def sign_file(path, private, keys=None):
    """writes path + SUFFIX, the signature of the file's bytes; the name of the key in keys (KEYS) it verifies with"""
    path = Path(path)
    data = path.read_bytes()
    line = base64.b64encode(private.sign(data)).decode("ascii") + "\n"
    name = verify(data, line.encode("ascii"), keys)          # BadSignature: a key the program does not accept
    tmp = path.with_name(path.name + SUFFIX + ".tmp")
    tmp.write_text(line, encoding="ascii", newline="\n")
    os.replace(tmp, path.with_name(path.name + SUFFIX))
    return name


def verify_one(where):
    """the name of the key that signed a file or URL (the .sig beside it); BadSignature, OSError"""
    if remote(where):
        from .content import fetch
        data = fetch(where, 1 << 26)
        try:
            sig = fetch(where + SUFFIX, MAX_SIG)
        except urllib.error.HTTPError as e:
            e.close()
            raise BadSignature(f"{where}: no signature beside it ({e.code})") from None
    else:
        data, sig = Path(where).read_bytes(), Path(str(where) + SUFFIX).read_bytes()
    return verify(data, sig)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m wuxianworkshop.signing", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="a new key pair").add_argument("key", help="the private key file to write")
    p = sub.add_parser("sign", help="write <file>.sig for each file")
    p.add_argument("key", help="the private key file")
    p.add_argument("files", nargs="+")
    sub.add_parser("verify", help="check files or URLs against the .sig beside them").add_argument("where", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        print(keygen(a.key))
        return 0
    if a.cmd == "sign":
        private = load_private(a.key)
        for f in a.files:
            try:
                print(f"{f}{SUFFIX}: {sign_file(f, private)}")
            except BadSignature:
                print(f"this key is not in KEYS ({public_key(private)}): the program would refuse what it signs",
                      file=sys.stderr)
                return 1
        return 0
    bad = 0
    for w in a.where:
        try:
            print(f"{w}: signed by {verify_one(w)}")
        except (BadSignature, OSError) as e:
            print(f"{w}: {e}", file=sys.stderr)
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
