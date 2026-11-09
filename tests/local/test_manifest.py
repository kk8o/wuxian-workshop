"""wuxian-extension.json and an extension's description (initialize's answer), checked: what is taken, and every
problem with where, message and hint."""
import json
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop.local import manifest as M


def where_of(problems, level="error"):
    return [p["where"] for p in problems if p["level"] == level]


class Manifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data, raw=None):
        (self.folder / M.MANIFEST).write_text(raw if raw is not None else json.dumps(data, ensure_ascii=False),
                                              encoding="utf-8")

    def test_a_full_manifest(self):
        (self.folder / "AGENT.md").write_text("# voice", encoding="utf-8")
        (self.folder / "skills").mkdir()
        (self.folder / "venv").mkdir()
        (self.folder / "venv" / "python.exe").write_bytes(b"")
        self.write({"id": "voice", "title": "声音", "version": "0.1.0", "notes": "说话", "run": ["python", "-m", "voice"],
                    "python": "venv/python.exe", "env": {"K": "V"}, "start": "workshop",
                    "apps": [{"app": "companion", "title": "无限陪玩", "prompt": "companion", "hear": ["game:combat"]}]})
        m, problems = M.check_manifest(self.folder)
        self.assertEqual(problems, [])
        self.assertEqual((m["id"], m["title"], m["version"], m["notes"], m["run"], m["env"], m["start"]),
                         ("voice", "声音", "0.1.0", "说话", ["python", "-m", "voice"], {"K": "V"}, "workshop"))
        self.assertEqual(m["python"], str(self.folder / "venv/python.exe"))
        self.assertEqual((m["docs"], m["skills"]), (str(self.folder / "AGENT.md"), str(self.folder / "skills")))
        self.assertEqual(m["apps"], [{"app": "companion", "title": "无限陪玩", "prompt": "companion",
                                      "hear": ["game:combat"]}])
        self.assertEqual(M.check_manifest(self.folder / M.MANIFEST)[0], m)          # its file names it too

    def test_defaults_and_warnings(self):
        self.write({"id": "tool", "run": ["python", "main.py"], "colour": "red"})
        m, problems = M.check_manifest(self.folder)
        self.assertEqual((m["title"], m["start"], m["docs"], m["skills"], m["apps"], m["python"]),
                         ("tool", "use", None, None, [], None))
        self.assertEqual(where_of(problems, "warning"), ["wuxian-extension.json: colour", "wuxian-extension.json: docs"])
        self.assertTrue(all(p["hint"] for p in problems))

    def test_what_is_refused(self):
        self.assertEqual(M.check_manifest(self.folder)[1][0]["message"], f"there is no {M.MANIFEST} in {self.folder}")
        self.write(None, raw="{nope")
        m, problems = M.check_manifest(self.folder)
        self.assertIsNone(m)
        self.assertIn("not valid JSON", problems[0]["message"])
        self.write([1])
        self.assertIn("JSON object", M.check_manifest(self.folder)[1][0]["message"])
        cases = [({"id": "Voice"}, "id"), ({"id": "sense"}, "id"), ({"id": "wk"}, "id"), ({"title": "长" * 17}, "title"),
                 ({"run": "python main.py"}, "run"), ({"run": []}, "run"), ({"python": "none/python.exe"}, "python"),
                 ({"env": {"A": 1}}, "env"), ({"start": "boot"}, "start"), ({"apps": {}}, "apps"),
                 ({"apps": [{"app": "x", "launch": True}]}, "apps[0]"), ({"apps": [{"app": "X"}]}, "apps[0].app"),
                 ({"apps": [{"app": "a"}, {"app": "a"}]}, "apps[1].app"),
                 ({"apps": [{"app": "a", "hear": "game:combat"}]}, "apps[0].hear")]
        for change, field in cases:
            self.write(dict({"id": "tool", "run": ["python", "main.py"]}, **change))
            m, problems = M.check_manifest(self.folder)
            self.assertIsNone(m, change)
            self.assertIn(f"wuxian-extension.json: {field}", where_of(problems), (change, problems))
        with self.assertRaises(M.ManifestError) as caught:
            raise M.ManifestError(problems)
        self.assertIn("apps[0].hear", str(caught.exception))

    def test_ids(self):
        for good in ("voice", "a", "my_tool2", "a" * 24):
            self.assertIsNone(M.id_problem(good), good)
        for bad in ("", "1a", "A", "a-b", "a" * 25, None, "chat", "guides", "local", "wb"):
            self.assertIsNotNone(M.id_problem(bad), bad)


class Description(unittest.TestCase):
    def good(self, **more):
        return dict({"protocol": 1, "capabilities": [
            {"name": "say", "kind": "point", "title": "说", "doc": "says it", "args": {"text": "string", "mood": "calm|happy?"}},
            {"name": "pool.list", "kind": "see", "title": "组", "doc": "the pools", "args": {}, "timeout": 120}],
            "sources": [{"name": "said", "title": "说完", "doc": "a line"}],
            "options": [{"name": "volume", "kind": "number", "title": "音量", "default": 80, "min": 0, "max": 100},
                        {"name": "speed", "kind": "choice", "title": "语速",
                         "choices": [{"value": "fast", "label": "快"}, {"value": "slow"}]},
                        {"name": "on", "kind": "boolean", "title": "开"}]}, **more)

    def test_a_good_one(self):
        desc, problems = M.check_description("voice", self.good())
        self.assertEqual(problems, [])
        self.assertEqual([(c["id"], c["kind"], c["timeout"]) for c in desc["capabilities"]],
                         [("voice.say", "point", 30), ("voice.pool.list", "see", 120)])
        self.assertEqual(desc["sources"], [{"name": "said", "title": "说完", "doc": "a line"}])
        self.assertEqual([(o["name"], o["default"]) for o in desc["options"]], [("volume", 80), ("speed", "fast"),
                                                                                 ("on", False)])
        self.assertEqual(desc["options"][1]["choices"], [{"value": "fast", "label": "快"}, {"value": "slow",
                                                                                           "label": "slow"}])

    def test_problems(self):
        def first(answer):
            desc, problems = M.check_description("voice", answer)
            return desc, problems
        desc, problems = first(self.good(protocol=2))
        self.assertIsNone(desc)
        self.assertIn("protocol", problems[0]["where"])
        for cap, words in (({"name": "speak", "kind": "say"}, "bad_capability"), ({"name": "x", "kind": "write"}, "kind"),
                           ({"name": "Bad", "kind": "see"}, "no capability name"),
                           ({"name": "x", "kind": "see", "args": {"a": "string|"}}, "empty"),
                           ({"name": "x", "kind": "see", "args": {"a b": "string"}}, "argument name"),
                           ({"name": "x", "kind": "see", "timeout": 601}, "timeout"),
                           ({"name": "say", "kind": "see"}, "declared twice")):
            desc, problems = first(self.good(capabilities=self.good()["capabilities"] + [cap]))
            self.assertIsNone(desc, cap)
            self.assertTrue(any(words in p["message"] for p in problems), (cap, problems))
        for opt, words in (({"name": "v", "kind": "number", "min": 5, "max": 1}, "min"),
                           ({"name": "v", "kind": "number", "default": 200, "max": 100}, "from"),
                           ({"name": "v", "kind": "choice"}, "choices"), ({"name": "v", "kind": "list"}, "kind")):
            desc, problems = first(self.good(options=[opt]))
            self.assertIsNone(desc, opt)
            self.assertTrue(any(words in p["message"] for p in problems), (opt, problems))
        desc, problems = first(self.good(capabilities=[{"name": "x", "kind": "see", "args": {"a": "number|fast"}}],
                                         sources=[{"name": "s"}]))
        self.assertIsNotNone(desc)
        self.assertEqual(len([p for p in problems if p["level"] == "warning"]), 3)   # mixed spec, no docs
        self.assertIsNone(M.check_description("voice", [])[0])

    def test_values(self):
        num = {"kind": "number", "min": 0, "max": 10}
        self.assertIsNone(M.value_problem(num, 5))
        self.assertIsNotNone(M.value_problem(num, 11))
        self.assertIsNotNone(M.value_problem(num, True))
        self.assertIsNone(M.value_problem({"kind": "boolean"}, False))
        self.assertIsNotNone(M.value_problem({"kind": "text"}, 1))
        choice = {"kind": "choice", "choices": [{"value": "a"}, {"value": 1}]}
        self.assertIsNone(M.value_problem(choice, 1))
        self.assertIsNotNone(M.value_problem(choice, True))
        self.assertIsNotNone(M.value_problem(choice, "b"))


if __name__ == "__main__":
    unittest.main()
