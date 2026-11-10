"""The guide to local extensions (data/local-extension.md) against the manifest's check: every manifest it shows is one
the workshop takes as it is, once the files it names are there, and the fields and words it must explain are in it."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop import extensions as X
from wuxianworkshop.local import manifest as M

GUIDE = Path(__file__).resolve().parents[2] / "src" / "wuxianworkshop" / "data" / "local-extension.md"


class Guide(unittest.TestCase):
    def setUp(self):
        self.text = GUIDE.read_text(encoding="utf-8")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def manifests(self):
        return [json.loads(b) for b in re.findall(r"```json\n(.*?)```", self.text, re.S)]

    def test_its_manifests_pass(self):
        seen = set()
        for i, d in enumerate(self.manifests()):
            folder = self.root / f"m{i}"
            for rel in (d.get("python"), "AGENT.md", *(a.get("skill") for a in d.get("adds", {}).values()),
                        *(f"{M.ADDONS_DIR}/{name}/{name}.toc" for name in d.get("addons", []))):
                if rel:
                    (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                    (folder / rel).write_text("# x\n", encoding="utf-8")
            (folder / "skills").mkdir(exist_ok=True)
            (folder / "wuxian-extension.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            m, problems = M.check_manifest(folder)
            self.assertEqual(problems, [], d["id"])
            self.assertEqual(m["id"], d["id"])
            self.assertEqual(m["addons"], d.get("addons", []))              # the game half, where the tree has it
            seen |= set(d)
            seen |= {f"apps.{k}" for a in d.get("apps", []) for k in a}
            seen |= {f"adds.{k}" for a in d.get("adds", {}).values() for k in a}
        for field in ("id", "title", "version", "notes", "run", "python", "env", "pass", "start", "docs", "skills",
                      "apps", "adds", "apps.reactions", "apps.controls", "adds.wake", "adds.skill"):
            self.assertIn(field, seen)                                      # each field is shown once at least
        fields = self.text.split("### The environment")[0].split("```")[-1]     # the list under the first manifest
        for field in M.FIELDS:                                              # every field of the manifest is shown,
            self.assertIn(field, seen)                                      # and the list says what it is
            self.assertIn(f"`{field}`", fields)

    def test_what_the_examples_come_to(self):
        """the two examples as the workshop reads them: an app with reactions and bound controls, and what one
        extension adds to another's app"""
        voice, facts = self.manifests()[1:3]
        for d in (voice, facts):
            (self.root / d["id"] / "skills").mkdir(parents=True)
            (self.root / d["id"] / "AGENT.md").write_text("# x\n", encoding="utf-8")
            (self.root / d["id"] / "wuxian-extension.json").write_text(json.dumps(d), encoding="utf-8")
        (self.root / "facts" / "skills" / "guild-chat.md").write_text("# x\n", encoding="utf-8")
        m, _ = M.check_manifest(self.root / "voice")
        app = m["apps"][0]
        self.assertEqual([r["call"] for r in app["reactions"]], ["voice.say", "voice.count"])
        self.assertEqual([c if isinstance(c, str) else (c["cmd"], c.get("call")) for c in app["controls"]],
                         ["stop", ("mute", "mute"), ("again", "again")])
        m, _ = M.check_manifest(self.root / "facts")
        skill = (self.root / "facts" / "skills" / "guild-chat.md").resolve()
        self.assertEqual(m["adds"], {"guild": {"wake": {"call": "facts.lookup", "timeout": 3.0}, "skill": str(skill)}})

    def test_what_it_explains(self):
        for word in ("needs_approval", "not_allowed", "允许运行", "`pass`", "`adds`", "`reactions`", "reaction_set",
                     "wk_kit_app_open", "wuxian listen", "deadline_ms", "`cancel", "`ping", "`loop`", '"player"',
                     '"workshop"', "extension_set", "forgotten"):
            self.assertIn(word, self.text)
        for word in ("annotate", "extends", "`driver`", "`launch`"):         # what the fifth batch took away
            self.assertNotIn(word, self.text)
        for word in M.RESERVED:
            self.assertRegex(self.text, rf"\b{word}\b")

    def test_the_home_and_the_packages(self):
        """what the extensions' home brought: where an extension lives, who is asked, packages, requires, the game
        half, removing; with the numbers and names the code has"""
        for word in ("`home`", "`linked`", "`origin`", "wuxian ext dir", "extension_pack", "wuxian ext pack",
                     "extension_install", "wuxian ext install", "extension_remove", "`pack.exclude`", "`skipped`",
                     "`sha256`", "bad_package", "package_too_big", "extension_exists", "`requires`", "`unmet`",
                     "requires_unmet", "`addons`", "addons/<Name>/", "<id>_game", "bus:<id>_game.", "addon_exists",
                     "needs_approval", "no_addons", "no_game_folder", "`foreign`", "`restart`", '`load {"target":',
                     "folder_busy", ".removed", "WUXIAN_EXTENSION_DATA", "`ext.data`", "## X-WuxianKit-Extension",
                     "kit-extension", "at every install and every update"):
            self.assertIn(word, self.text)
        kinds = re.search(r"an addon's kinds of file only: `([^`]+)`", self.text).group(1).split()
        self.assertEqual(sorted(kinds), sorted(X.KINDS))
        self.assertIn(f"at most {M.ADDONS_MOST} names", self.text)
        self.assertIn(f"at most {M.AUTHOR_MAX} bytes", self.text)
        self.assertIn(f'{{"workshop": "0.9.13", "kit": "{self.manifests()[0]["requires"]["kit"]}"}}', self.text)
        self.assertEqual(sorted(self.manifests()[0]["requires"]), sorted(M.REQUIRES))

    def test_what_a_package_leaves_out(self):
        """the guide's list and limits are the packer's, and the mark is the game side's"""
        try:
            from wuxianworkshop.local import gameside, pack
        except ImportError:                                 # a checkout before local/pack.py and local/gameside.py
            self.skipTest("local/pack.py and local/gameside.py are not here yet")
        flat = " ".join(self.text.replace("`", " ").split())
        self.assertIn(" " + " ".join(pack.EXCLUDE) + " ", flat)             # all of it, in the packer's order
        self.assertEqual((pack.MAX_ZIP, pack.MAX_UNPACKED, pack.MAX_FILES), (64 << 20, 256 << 20, 8000))
        for word in ("64 MB", "256 MB", "8,000", f"`{gameside.MARK}`"):
            self.assertIn(word, self.text)


if __name__ == "__main__":
    unittest.main()
