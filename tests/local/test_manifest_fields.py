"""The manifest's fields of a package: author, homepage, requires, addons (the game half), pack; the ids the installed
WuxianKit takes; the extensions' home folder."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop import paths
from wuxianworkshop.local import manifest as M


def where_of(problems, level="error"):
    return [p["where"] for p in problems if p["level"] == level]


class Fields(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name).resolve()
        (self.folder / "AGENT.md").write_text("# x", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, taken=(), **fields):
        data = dict({"id": "voice", "title": "声音", "run": ["python", "main.py"]}, **fields)
        (self.folder / M.MANIFEST).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return M.check_manifest(self.folder, taken)

    def addon(self, name, toc=None):
        d = self.folder / M.ADDONS_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        if toc is not False:
            (d / (toc or f"{name}.toc")).write_text("## Interface: 16001\n## Title: x\n", encoding="utf-8")
        return d

    def test_nothing_given(self):
        m, problems = self.check()
        self.assertEqual(problems, [])
        self.assertEqual((m["author"], m["homepage"], m["requires"], m["addons"], m["pack"]),
                         (None, None, {}, [], {"exclude": []}))

    def test_all_given(self):
        self.addon("WuxianVoice")
        self.addon("WuxianVoice_Data", toc="WuxianVoice_Data_Camelot.toc")
        m, problems = self.check(author="Tim", homepage="https://wuxianwow.com/x",
                                 requires={"workshop": "0.9.13", "kit": " 0.6.0 "},
                                 addons=["WuxianVoice", "WuxianVoice_Data"],
                                 pack={"exclude": ["models/*", " *.wav "]})
        self.assertEqual(problems, [])
        self.assertEqual((m["author"], m["homepage"]), ("Tim", "https://wuxianwow.com/x"))
        self.assertEqual(m["requires"], {"workshop": "0.9.13", "kit": "0.6.0"})
        self.assertEqual(m["addons"], ["WuxianVoice", "WuxianVoice_Data"])
        self.assertEqual(m["pack"], {"exclude": ["models/*", "*.wav"]})

    def test_author_and_homepage_refused(self):
        m, problems = self.check(author=5, homepage="ftp://x")
        self.assertIsNone(m)
        self.assertEqual(where_of(problems), [f"{M.MANIFEST}: author", f"{M.MANIFEST}: homepage"])
        m, problems = self.check(homepage="https://a b")
        self.assertEqual(where_of(problems), [f"{M.MANIFEST}: homepage"])

    def test_requires_refused(self):
        m, problems = self.check(requires=["0.9.13"])
        self.assertEqual(where_of(problems), [f"{M.MANIFEST}: requires"])
        m, problems = self.check(requires={"workshop": "new", "kit": 6, "game": "1.0"})
        self.assertIsNone(m)
        self.assertEqual(where_of(problems), [f"{M.MANIFEST}: requires.kit", f"{M.MANIFEST}: requires.workshop"])
        self.assertEqual(where_of(problems, "warning"), [f"{M.MANIFEST}: requires.game"])

    def test_addons_refused(self):
        self.addon("Good")
        self.addon("NoToc", toc=False)
        m, problems = self.check(addons=["Good", "a/b", "Missing", "NoToc", "good", "WoWBridge", "Blizzard_Foo",
                                         "WuxianKit"])
        self.assertIsNone(m)
        self.assertEqual(where_of(problems), [f"{M.MANIFEST}: addons[{i}]" for i in range(1, 8)])
        m, problems = self.check(addons="Good")
        self.assertEqual(where_of(problems), [f"{M.MANIFEST}: addons"])
        m, problems = self.check(addons=["Good"] * (M.ADDONS_MOST + 1))
        self.assertEqual(where_of(problems), [f"{M.MANIFEST}: addons"])

    def test_pack_refused(self):
        for bad in ("x", {"exclude": "a"}, {"exclude": ["../a"]}, {"exclude": ["a\\b"]}, {"exclude": ["/a"]},
                    {"exclude": [""]}, {"exclude": ["a"] * (M.EXCLUDE_MOST + 1)}):
            m, problems = self.check(pack=bad)
            self.assertIsNone(m, bad)
            self.assertEqual(len(where_of(problems)), 1, bad)
        m, problems = self.check(pack={"exclude": ["a"], "more": 1})
        self.assertEqual(where_of(problems, "warning"), [f"{M.MANIFEST}: pack.more"])
        self.assertEqual(m["pack"], {"exclude": ["a"]})

    def test_ids_the_kit_takes(self):
        m, problems = self.check(taken=["guild", "voice"])
        self.assertIsNone(m)
        self.assertEqual(where_of(problems), [f"{M.MANIFEST}: id"])
        self.assertIn("WuxianKit in this game", problems[0]["hint"])
        m, problems = self.check(taken=["guild"])
        self.assertEqual(problems, [])
        self.assertIsNone(M.id_problem("voice", "id", None))
        self.assertIsNotNone(M.id_problem("chat"))


class Unmet(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(M.version_tuple("0.9.13"), (0, 9, 13))
        self.assertEqual(M.version_tuple(" 1 "), (1,))
        for bad in (None, 9, "", "0.9.x", "v1", "1.2.3.4.5", "1..2"):
            self.assertIsNone(M.version_tuple(bad), bad)

    def test_unmet(self):
        req = {"workshop": "0.9.13", "kit": "0.6"}
        self.assertEqual(M.unmet(req, "0.9.13", "0.6.0"), [])
        self.assertEqual(M.unmet(req, "0.10", "0.6.1"), [])
        self.assertEqual(M.unmet(req, "0.9.12", "0.5.9"),
                         [dict(what="workshop", needs="0.9.13", has="0.9.12"), dict(what="kit", needs="0.6", has="0.5.9")])
        self.assertEqual(M.unmet(req, None, None), [])            # not known: not judged
        self.assertEqual(M.unmet(req, "0.9.12+dev", "0.5"), [dict(what="kit", needs="0.6", has="0.5")])
        self.assertEqual(M.unmet({}, "0.1", "0.1"), [])
        self.assertEqual(M.unmet(None, "0.1", "0.1"), [])


class Home(unittest.TestCase):
    def test_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"WUXIAN_HOME": tmp}):
            home = paths.extensions_home()
            self.assertEqual(home, Path(tmp) / "extensions")
            self.assertTrue(home.is_dir())


if __name__ == "__main__":
    unittest.main()
