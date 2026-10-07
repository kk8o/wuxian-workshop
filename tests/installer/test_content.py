"""Content packs (wuxianworkshop/content.py): the newer of the carried and the downloaded copy is used, and the updater
takes a newer pack from a manifest only when its size, SHA-256 and contents check out. A local folder stands in for
wuxianwow.com/workshop/data."""
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

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
                 dict(raw=b"not gzip"))
        for case in cases:
            raw = case.pop("raw", None)
            self.publish("2026.10.08.1", raw=raw, **case)
            u = self.updater()
            self.assertEqual(u.check(), [], case)
            self.assertIn("内容包更新失败", u.error)
            self.assertEqual(self.packs.version("api"), "2026.10.06.1")
        self.assertFalse((self.cached / "api.json.gz").exists())

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


if __name__ == "__main__":
    unittest.main()
