"""The extension catalog (extensions.py): its entries checked, the zip downloaded and checked (size, SHA-256, every member),
installed in place of what the folder held (a version kept first, the .toc's Interface the client's, whether the game
must start again), removed (a version kept), and what the 扩展 page offers for each state."""
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from wuxianworkshop import extensions as X
from wuxianworkshop.agent import history


def make_zip(folder="Demo", version="1.0.0", extra=None, toc_version=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{folder}/{folder}.toc", f"## Interface: 11000\n## Title: Demo\n## Version: {toc_version or version}\n"
                                             f"\nCore.lua\n")
        z.writestr(f"{folder}/Core.lua", "print('demo')\n")
        z.writestr(f"{folder}/AGENT.md", "# Demo\n")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def make_catalog(where, raw, folder="Demo", version="1.0.0", min_app="0.1.0"):
    name = f"{folder}-{version}.zip"
    (where / name).write_bytes(raw)
    entry = {"id": "demo", "folder": folder, "version": version, "title": {"zh": "示例", "en": "Demo"}, "file": name,
             "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "min_app": min_app}
    (where / "extensions.json").write_text(json.dumps({"format": 1, "updated": "2026-10-10", "addons": [
        entry, {"id": "Bad Id", "folder": "x"}, {"id": "nofile", "folder": "NoFile", "version": "1"}]}), encoding="utf-8")
    return entry


class Catalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_entries_and_the_download(self):
        raw = make_zip()
        entry = make_catalog(self.dir, raw)
        cat = X.Catalog(url=str(self.dir / "extensions.json")).refresh()
        self.assertEqual((cat.addons, cat.updated, cat.error), ([entry], "2026-10-10", ""))      # the broken ones left out
        self.assertEqual(cat.get("demo"), entry)
        self.assertEqual(cat.download(entry), raw)
        with self.assertRaisesRegex(X.CatalogError, "bytes, the catalog says"):
            cat.download(dict(entry, size=entry["size"] + 1))
        with self.assertRaisesRegex(X.CatalogError, "SHA-256"):
            cat.download(dict(entry, sha256="0" * 64))
        (self.dir / "extensions.json").write_text("{not json", encoding="utf-8")
        cat.refresh()
        self.assertTrue(cat.error)
        self.assertEqual(cat.addons, [entry])                                             # what was read stands
        (self.dir / "extensions.json").unlink()                                          # none there yet: not a fault
        self.assertEqual((cat.refresh().missing, cat.error), (True, "网站上还没有扩展目录。"))
        self.assertEqual(cat.status()["missing"], True)
        import urllib.error
        for raised, said in ((urllib.error.HTTPError("u", 500, "x", None, None), "扩展目录读取失败：网站返回 500"),
                             (urllib.error.URLError("offline"), "连不上无限工坊网站，稍后点「刷新目录」再试。")):
            def fetcher(url, limit, raised=raised):
                raise raised
            other = X.Catalog(url="https://example.invalid/extensions.json", fetcher=fetcher).refresh()
            self.assertEqual((other.missing, other.error), (False, said))
        with mock.patch.dict(os.environ, {"WUXIAN_EXTENSIONS_FEED": "C:/x/extensions.json"}):
            self.assertEqual(X.Catalog().url, "C:/x/extensions.json")

    def test_the_catalog_is_kept_for_the_agent(self):
        """a catalog read is kept (its addons' id, folder and version) when the Catalog has a store; a failed read
        leaves what was kept"""
        make_catalog(self.dir, make_zip())
        store = self.dir / "state" / X.KEPT
        cat = X.Catalog(url=str(self.dir / "extensions.json"), store=store).refresh()
        self.assertEqual(json.loads(store.read_text(encoding="utf-8"))["addons"],
                         [{"id": "demo", "folder": "Demo", "version": "1.0.0"}])
        self.assertEqual(X.listed_folders(store), {"Demo"})
        (self.dir / "extensions.json").write_text("{not json", encoding="utf-8")
        cat.refresh()
        self.assertEqual(X.listed_folders(store), {"Demo"})
        self.assertIsNone(X.listed_folders(self.dir / "none.json"))

    def test_every_member_is_checked(self):
        for extra, why in (({"Demo/../evil.lua": "x"}, "not a file of"), ({"Other/x.lua": "x"}, "not a file of"),
                           ({"Demo/run.exe": "x"}, "not a kind of file"), ({"/abs.lua": "x"}, "not a file of")):
            with self.subTest(extra=extra), self.assertRaisesRegex(X.CatalogError, why):
                X.unpack(make_zip(extra=extra), "Demo")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Demo/Core.lua", "x")
        with self.assertRaisesRegex(X.CatalogError, "no Demo/Demo.toc"):
            X.unpack(buf.getvalue(), "Demo")
        link = zipfile.ZipInfo("Demo/link.lua")
        link.external_attr = 0o120777 << 16
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Demo/Demo.toc", "## Version: 1\n")
            z.writestr(link, "elsewhere")
        with self.assertRaisesRegex(X.CatalogError, "a link"):
            X.unpack(buf.getvalue(), "Demo")
        with self.assertRaisesRegex(X.CatalogError, "not a zip"):
            X.unpack(b"PK nothing", "Demo")

    def test_states(self):
        entry = {"id": "demo", "folder": "Demo", "version": "1.2.0", "min_app": "0.1.0"}
        self.assertEqual([X.state_of(entry, *s) for s in ((False, None), (True, None), (True, "1.1.9"), (True, "1.2.0"),
                                                          (True, "1.10.0"))],
                         ["available", "installed", "update", "current", "newer"])
        self.assertEqual(X.state_of(dict(entry, min_app="99.0"), True, "1.1.9"), "needs_app")


class InstallAndRemove(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(self.tmp.name) / "home")})
        self.env.start()
        self.addons = Path(self.tmp.name) / "AddOns"
        self.addons.mkdir()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def entry(self, version):
        return {"id": "demo", "folder": "Demo", "version": version}

    def test_install_update_and_remove(self):
        r = X.install(self.addons, self.entry("1.0.0"), make_zip(), interface=16001)
        self.assertEqual((r["was"], r["kept"], r["restart"]), (None, None, True))        # new: the game starts again
        toc = (self.addons / "Demo" / "Demo.toc").read_text(encoding="utf-8")
        self.assertTrue(toc.startswith("## Interface: 16001\n"))
        self.assertEqual(X.installed(self.addons, "Demo"), (True, "1.0.0"))
        r = X.install(self.addons, self.entry("1.0.1"), make_zip(version="1.0.1"))
        self.assertEqual((r["was"], r["restart"]), ("1.0.0", False))                      # the same files: a reload
        kept = history.versions(self.addons, "Demo")["versions"]
        self.assertEqual((kept[0]["id"], kept[0]["reason"], kept[0]["note"]), (r["kept"], "update", "1.0.0 -> 1.0.1"))
        r = X.install(self.addons, self.entry("1.0.2"), make_zip(version="1.0.2", extra={"Demo/More.lua": "x"}))
        self.assertTrue(r["restart"])                                                     # a code file more
        with self.assertRaisesRegex(X.CatalogError, "says version 9.9"):
            X.install(self.addons, self.entry("1.0.3"), make_zip(toc_version="9.9"))
        self.assertEqual(X.installed(self.addons, "Demo"), (True, "1.0.2"))              # nothing changed
        self.assertFalse([p for p in self.addons.iterdir() if p.name.startswith(".")])    # no staging left over
        r = X.remove(self.addons, self.entry("1.0.2"))
        self.assertTrue(r["removed"])
        self.assertFalse((self.addons / "Demo").exists())
        self.assertEqual(history.versions(self.addons, "Demo")["versions"][0]["reason"], "remove")
        history.restore(self.addons, "Demo", r["kept"])                                  # and back it comes
        self.assertEqual(X.installed(self.addons, "Demo"), (True, "1.0.2"))
        self.assertFalse(X.remove(self.addons, {"id": "x", "folder": "Gone", "version": "1"})["removed"])


if __name__ == "__main__":
    unittest.main()
