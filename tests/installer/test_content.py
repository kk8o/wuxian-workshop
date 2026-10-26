"""Content packs (wuxianworkshop/content.py): the newer of the carried and the downloaded copy is used, and the updater
takes a newer pack from a manifest only when its size, SHA-256 and contents check out, and from the website only when the
manifest is signed. A local folder stands in for wuxianwow.com/workshop/data."""
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.installer import signed
from wuxianworkshop import content


def pack_bytes(version, name="api", **extra):
    return gzip.compress(json.dumps(dict(pack=name, version=version, **extra)).encode("utf-8"))


class Packs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bundled, self.cached, self.site = self.root / "bundled", self.root / "cached", self.root / "site"
        for d in (self.bundled, self.cached, self.site):
            d.mkdir()
        (self.bundled / "api.json.gz").write_bytes(pack_bytes("2026.10.06.1"))
        self.packs = content.Packs(self.bundled, self.cached)
        self.notes = []

    def publish(self, version, raw=None, **entry):
        raw = raw if raw is not None else pack_bytes(version)
        name = f"api-{version}.json.gz"
        (self.site / name).write_bytes(raw)
        info = dict(version=version, file=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), min_app="0.8.0")
        info.update(entry)
        (self.site / "manifest.json").write_text(json.dumps({"packs": {"api": info}}), encoding="utf-8")

    def updater(self):
        return content.ContentUpdater(self.packs, url=str(self.site / "manifest.json"), note=self.notes.append, clock=lambda: 5.0)

    def test_carried_then_downloaded(self):
        self.assertEqual((self.packs.version("api"), self.packs.source("api")), ("2026.10.06.1", "bundled"))
        self.publish("2026.10.07.1")
        self.assertEqual(self.updater().check(), ["api"])
        self.assertEqual((self.packs.version("api"), self.packs.source("api")), ("2026.10.07.1", "downloaded"))
        self.assertEqual(self.updater().check(), [])                              # nothing newer now
        self.assertTrue(any("api 2026.10.07.1 downloaded" in n for n in self.notes))

    def test_a_carried_copy_newer_than_the_download_wins(self):
        (self.cached / "api.json.gz").write_bytes(pack_bytes("2026.10.05.1"))
        self.assertEqual((self.packs.version("api"), self.packs.source("api")), ("2026.10.06.1", "bundled"))

    def test_bad_downloads_change_nothing(self):
        cases = (dict(sha256="0" * 64), dict(size=1), dict(raw=pack_bytes("2026.10.08.9")),     # another version inside
                 dict(raw=b"not gzip"),
                 dict(raw=gzip.compress(json.dumps({"version": "2026.10.08.1", "pack": "api"}).encode("utf-8"))))   # not led by them
        for case in cases:
            raw = case.pop("raw", None)
            self.publish("2026.10.08.1", raw=raw, **case)
            u = self.updater()
            self.assertEqual(u.check(), [], case)
            self.assertIn("内容包更新失败", u.error)
            self.assertEqual(self.packs.version("api"), "2026.10.06.1")
        self.assertFalse((self.cached / "api.json.gz").exists())

    def test_from_the_website_the_manifest_counts_only_signed(self):
        self.publish("2026.10.07.1")
        with signed.keys():
            u = content.ContentUpdater(self.packs, url=signed.BASE + "site/manifest.json", note=self.notes.append,
                                       clock=lambda: 5.0, fetcher=signed.fetcher(self.root))
            for sign in (None, signed.OTHER):                         # no signature; one by a key the program does not accept
                if sign:
                    signed.sign(self.site / "manifest.json", sign)
                self.assertEqual(u.check(), [])
                self.assertEqual(u.error, "内容包清单未通过签名校验，没有更新。")
                self.assertEqual(self.packs.version("api"), "2026.10.06.1")
            signed.sign(self.site / "manifest.json")
            self.assertEqual(u.check(), ["api"])
            self.assertEqual(u.error, "")
        self.assertEqual((self.packs.version("api"), self.packs.source("api")), ("2026.10.07.1", "downloaded"))

    def test_a_pack_for_a_newer_program_waits(self):
        self.publish("2026.10.09.1", min_app="99.0")
        self.assertEqual(self.updater().check(), [])
        self.assertEqual(self.packs.version("api"), "2026.10.06.1")

    def test_versions_sort_as_numbers(self):
        self.assertLess(content.version_key("2026.10.6.9"), content.version_key("2026.10.06.10"))
        self.assertLess(content.version_key("2026.9.30.1"), content.version_key("2026.10.1.1"))
        self.assertLess(content.version_key(None), content.version_key("0.1"))

    def test_a_broken_cached_copy_is_ignored(self):
        (self.cached / "api.json.gz").write_bytes(b"\x1f\x8b broken")
        self.assertEqual(self.packs.source("api"), "bundled")

    def test_a_newer_copy_that_does_not_read_whole_gives_way(self):
        whole = pack_bytes("2026.10.07.1", entries=list(range(5000)))
        (self.cached / "api.json.gz").write_bytes(whole[:len(whole) // 2])      # its first bytes read, the rest not
        self.assertEqual((self.packs.version("api"), self.packs.source("api")), ("2026.10.06.1", "bundled"))

    def test_status_reads_no_pack_whole(self):
        """/api/status asks first: the versions come from the files' first bytes, and only the newest is read whole"""
        (self.cached / "api.json.gz").write_bytes(pack_bytes("2026.10.07.1"))
        with mock.patch.object(content, "read_pack") as whole:
            self.assertEqual(self.packs.status(), {"api": {"version": "2026.10.07.1", "source": "downloaded"}})
            whole.assert_not_called()
        self.assertEqual(self.packs.version("api"), "2026.10.07.1")
        with mock.patch.object(content, "read_pack", wraps=content.read_pack) as whole:
            self.packs.replace("api", pack_bytes("2026.10.08.1"))
            self.assertEqual(self.packs.version("api"), "2026.10.08.1")
            self.assertEqual(whole.call_count, 1)                             # the newer copy only
        self.assertEqual(content.pack_head(self.cached / "api.json.gz"), ("api", "2026.10.08.1"))
        self.assertIsNone(content.pack_head(self.root / "missing.json.gz"))
        (self.root / "bytes.json.gz").write_bytes(gzip.compress(b'{"pack":"api","version":"\xff\xfe"}'))   # not UTF-8
        self.assertIsNone(content.pack_head(self.root / "bytes.json.gz"))


if __name__ == "__main__":
    unittest.main()
