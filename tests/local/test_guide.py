"""The guide to local extensions (data/local-extension.md) against the manifest's check: every manifest it shows is one
the workshop takes as it is, once the files it names are there, and the fields and words it must explain are in it."""
import json
import re
import tempfile
import unittest
from pathlib import Path

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
            for rel in (d.get("python"), "AGENT.md", *(a.get("skill") for a in d.get("adds", {}).values())):
                if rel:
                    (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                    (folder / rel).write_text("# x\n", encoding="utf-8")
            (folder / "skills").mkdir(exist_ok=True)
            (folder / "wuxian-extension.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            m, problems = M.check_manifest(folder)
            self.assertEqual(problems, [], d["id"])
            self.assertEqual(m["id"], d["id"])
            seen |= set(d)
            seen |= {f"apps.{k}" for a in d.get("apps", []) for k in a}
            seen |= {f"adds.{k}" for a in d.get("adds", {}).values() for k in a}
        for field in ("id", "title", "version", "notes", "run", "python", "env", "pass", "start", "docs", "skills",
                      "apps", "adds", "apps.reactions", "apps.controls", "adds.wake", "adds.skill"):
            self.assertIn(field, seen)                                      # each field is shown once at least

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


if __name__ == "__main__":
    unittest.main()
