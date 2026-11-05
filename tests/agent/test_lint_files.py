"""What made a check slow, done faster with the same answers (agent/lint.py): an addon folder's files listed by os.walk
(addon_files) with WoWBridge's mailbox left out, and the near-name lookups (difflib) remembered by name."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop import apidocs
from wuxianworkshop.agent import lint


class AddonFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addons = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, *names):
        for name in names:
            p = self.addons / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("-- x\n", encoding="utf-8")

    def test_the_files_rglob_finds(self):
        self.make("Foo/Foo.toc", "Foo/Core.lua", "Foo/libs/A/A.lua", "Foo/mail/Note.lua", "Foo/.hidden/x.lua")
        (self.addons / "Foo" / "empty").mkdir()
        folder = self.addons / "Foo"
        self.assertEqual(sorted(lint.addon_files(folder)), sorted(p for p in folder.rglob("*") if p.is_file()))

    def test_wowbridge_mail_is_left_out(self):
        self.make("WoWBridge/WoWBridge.toc", "WoWBridge/Link.lua", "WoWBridge/mail/00001.ttf", "WoWBridge/mail/proc.ttf",
                  "WoWBridge/sub/mail/Keep.lua")
        got = sorted(p.relative_to(self.addons).as_posix() for p in lint.addon_files(self.addons / "WoWBridge"))
        self.assertEqual(got, ["WoWBridge/Link.lua", "WoWBridge/WoWBridge.toc", "WoWBridge/sub/mail/Keep.lua"])

    def test_loaded_files_and_check_as_before(self):
        self.make("WoWBridge/WoWBridge.toc", "WoWBridge/Link.lua", "WoWBridge/mail/00001.ttf",
                  "Foo/Core.lua", "Foo/Extra.lua", "Foo/Unlisted.lua")
        (self.addons / "WoWBridge" / "WoWBridge.toc").write_text("## Interface: 120000\nLink.lua\n", encoding="utf-8")
        (self.addons / "Foo" / "Foo.toc").write_text("## Interface: 120000\n## OptionalDeps: WoWBridge\nCore.lua\n"
                                                     "core.LUA\nExtra.lua\nGone.lua\n", encoding="utf-8")
        loaded = lint.loaded_files(self.addons / "Foo", lint.read_toc(self.addons / "Foo" / "Foo.toc"))
        self.assertEqual([p.name for p in loaded["lua"]], ["Core.lua", "Extra.lua"])    # core.LUA: the same file
        self.assertEqual(loaded["missing"], [(6, "Gone.lua")])
        report = lint.check(self.addons / "Foo", self.addons)
        self.assertIn("Gone.lua is listed but not there", [f["message"] for f in report["errors"]])
        self.assertTrue(any("Unlisted.lua" in n["message"] for n in report["notes"] if n["code"] == "not-loaded"))


class NearNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ix = apidocs.index()

    def test_remembered_with_the_same_answers(self):
        fresh, k = lint.Known(self.ix), lint.Known(self.ix)
        names = ["UnitHelath", "GetTiem", "CreateFrmae", "QuiteUnlikelyName", "UnitHealth"]
        before = [lint.typo_of(n, [g for g in fresh._by_first.get(n[:1].lower(), []) if abs(len(g) - len(n)) <= 2])
                  for n in names]
        self.assertEqual([k.near(n) for n in names], before)
        self.assertEqual(k.near("UnitHelath"), "UnitHealth")
        with mock.patch.object(lint, "typo_of", side_effect=AssertionError("asked again")):
            self.assertEqual([k.near(n) for n in names], before)        # from memory
        field = k.near_field("C_Timer", "Aftr")
        with mock.patch.object(lint, "typo_of", side_effect=AssertionError("asked again")):
            self.assertEqual(k.near_field("C_Timer", "Aftr"), field)
        self.assertEqual(field, lint.typo_of("Aftr", sorted(k.ns.get("C_Timer", ())), loose=True))

    def test_the_memory_is_bounded(self):
        k = lint.Known(self.ix)
        with mock.patch.object(lint.Known, "NEAR_KEPT", 3):
            for n in ("Abcdef", "Bcdefg", "Cdefgh", "Defghi"):
                k.near(n)
        self.assertLessEqual(len(k._near), 3)


if __name__ == "__main__":
    unittest.main()
