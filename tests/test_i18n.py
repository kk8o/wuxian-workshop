"""i18n.py: the language the program speaks in follows the setting (auto: WUXIAN_LANG, else Windows' display language);
in English the self-check and a new addon (its Lua's words, AGENTS.md) carry no Chinese."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop import i18n, scaffold
from wuxianworkshop.installer import doctor

HAN = re.compile(r"[一-鿿]")


class Language(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("auto")

    def test_the_setting(self):
        i18n.set_language("en")
        self.assertEqual((i18n.language(), i18n.tr("中", "en")), ("en", "en"))
        i18n.set_language("zh-CN")
        self.assertEqual((i18n.language(), i18n.tr("中", "en")), ("zh-CN", "中"))
        with mock.patch.dict(os.environ, {"WUXIAN_LANG": "en"}):
            i18n.set_language("auto")
            self.assertEqual(i18n.language(), "en")              # auto: the environment stands in for Windows
            i18n.set_language("bogus")
            self.assertEqual(i18n.language(), "en")              # anything else counts as auto
            i18n.set_language("zh-CN")
            self.assertEqual(i18n.language(), "zh-CN")           # a chosen language wins

    def test_settings_json_when_not_told(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"WUXIAN_HOME": home}):
            state = Path(home) / "state"
            state.mkdir()
            (state / "settings.json").write_text('{"language": "en"}', encoding="utf-8")
            with mock.patch.object(i18n, "_setting", None):
                self.assertEqual(i18n.language(), "en")


class English(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")

    def tearDown(self):
        i18n.set_language("auto")

    def test_a_new_addon(self):
        with tempfile.TemporaryDirectory() as addons:
            for template in ("basic", "window"):
                res = scaffold.create(addons, f"Hello{template.title()}", title="Hello", template=template, client="1.60.1.70245")
                folder = Path(res["path"])
                for f in folder.iterdir():
                    self.assertIsNone(HAN.search(f.read_text(encoding="utf-8")), f"{template}: {f.name}")
                self.assertIn("notes for the agent", (folder / "AGENTS.md").read_text(encoding="utf-8"))
                self.assertIn('Print("hot-loaded")', (folder / f"{res['name']}.lua").read_text(encoding="utf-8"))
            with self.assertRaises(scaffold.ScaffoldError) as e:
                scaffold.create(addons, "HelloBasic")
            self.assertEqual(e.exception.code, "addon_exists")
            self.assertIn("already", str(e.exception))

    def test_the_self_check(self):
        with tempfile.TemporaryDirectory() as game:
            checks = doctor.run_checks(game)                       # not a client folder: most checks are skipped
        for c in checks:
            self.assertIsNone(HAN.search(c["title"]), c["title"])
            self.assertIsNone(HAN.search(c["detail"]), c["detail"])
        self.assertTrue(any(c["detail"].startswith("skipped: ") for c in checks))
        self.assertIsNone(HAN.search("\n".join(doctor.report(checks))))


if __name__ == "__main__":
    unittest.main()
