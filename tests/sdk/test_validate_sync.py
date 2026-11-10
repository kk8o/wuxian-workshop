"""The checks of a local extension's declarations are written once, in local/validate.py; the single-file SDK carries a
copy. The two are the same text (scripts/sync_sdk_validate.py copies it), and the SDK refuses what the workshop refuses."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests.sdk.test_sdk import load_sdk
from wuxianworkshop.local import manifest as M
from wuxianworkshop.local import validate as V

ROOT = Path(__file__).resolve().parents[2]
wk = load_sdk()


def load_script():
    spec = importlib.util.spec_from_file_location("sync_sdk_validate", ROOT / "scripts" / "sync_sdk_validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = load_script()


class Same(unittest.TestCase):
    def test_the_two_blocks_are_the_same_text(self):
        self.assertTrue(S.block(S.SOURCE).strip())
        self.assertEqual(S.block(S.TARGET), S.block(S.SOURCE),
                         "sdk/python/wuxiankit.py's shared block differs from local/validate.py's: "
                         "run scripts/sync_sdk_validate.py")
        self.assertTrue(S.synced())
        self.assertEqual(S.main(["--check"]), 0)

    def test_the_names_are_the_same_objects_text(self):
        for name in ("CAP_NAME", "SOURCE_NAME", "OPTION_NAME", "ARG_NAME"):
            self.assertEqual(getattr(wk, name).pattern, getattr(V, name).pattern, name)
        for name in ("CAP_NAME_MAX", "TOOL_NAME_MAX", "KINDS", "OPTION_KINDS", "TYPES", "TIMEOUT_DEFAULT", "TIMEOUT_MOST",
                     "TEXT_MOST", "TITLE_MAX", "PROTOCOL", "LATER_KINDS"):
            self.assertEqual(getattr(wk, name), getattr(V, name), name)
        self.assertIs(M.check_description, V.check_description)        # the manifest module gives the same checks

    def test_the_block_stands_alone(self):
        """it uses json, math and re only: it runs with nothing else around it"""
        scope: dict = {}
        exec("import json\nimport math\nimport re\n" + S.block(S.SOURCE), scope)
        self.assertEqual(scope["args_problem"]("x.y", {"n": scope["parse_spec"]("number")}, {"n": 1}), ({"n": 1}, None))


class Script(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.source, self.target = d / "validate.py", d / "wuxiankit.py"
        self.source.write_bytes(b"import re\r\n# ---- shared: begin ---\r\nA = 1\r\nB = 2\r\n# ---- shared: end ---\r\n")

    def test_it_copies_and_keeps_line_ends(self):
        self.target.write_bytes(b"head\r\n# ---- shared: begin ---\r\nA = 0\r\n# ---- shared: end ---\r\ntail\r\n")
        self.assertFalse(S.synced(self.source, self.target))
        self.assertTrue(S.sync(self.source, self.target))
        self.assertEqual(self.target.read_bytes(),
                         b"head\r\n# ---- shared: begin ---\r\nA = 1\r\nB = 2\r\n# ---- shared: end ---\r\ntail\r\n")
        self.assertTrue(S.synced(self.source, self.target))
        self.assertFalse(S.sync(self.source, self.target))            # nothing to do: the file is not written again
        self.target.write_bytes(b"head\n# ---- shared: begin\n# ---- shared: end\ntail\n")
        self.assertTrue(S.sync(self.source, self.target))
        self.assertEqual(self.target.read_bytes(), b"head\n# ---- shared: begin\nA = 1\nB = 2\n# ---- shared: end\ntail\n")

    def test_missing_markers(self):
        for text in (b"no markers\n", b"# ---- shared: end\n# ---- shared: begin\n",
                     b"# ---- shared: begin\n# ---- shared: begin\n# ---- shared: end\n"):
            self.target.write_bytes(text)
            with self.assertRaises(ValueError):
                S.sync(self.source, self.target)


class OneRule(unittest.TestCase):
    """the SDK refuses at declaration what the workshop refuses in the description, with the workshop's words"""

    def workshop(self, **described):
        desc, problems = M.check_description("demo", dict({"protocol": 1}, **described))
        return desc, [p["message"] for p in M.errors_in(problems)]

    def test_source_names(self):
        ext = wk.Extension("demo")
        for name in ("Said", "voice.said", "a" * 25, ""):
            self.assertIsNone(self.workshop(sources=[{"name": name, "title": "t", "doc": "d"}])[0], name)
            with self.assertRaises(ValueError, msg=name):
                ext.source(name, doc="d")
        ext.source("a" * 24, doc="d")
        self.assertIsNotNone(self.workshop(sources=[{"name": "a" * 24, "title": "t", "doc": "d"}])[0])

    def test_capability_names_and_lengths(self):
        ext = wk.Extension("demo")
        fine = "a" * (wk.TOOL_NAME_MAX - len("wk_demo_"))
        for name in ("Bad", "a..b", "say.", fine + "a", "a" * 49):
            cap = {"name": name, "kind": "see", "title": "t", "doc": "d", "args": {}}
            _desc, errors = self.workshop(capabilities=[cap])
            self.assertTrue(errors, name)
            with self.assertRaises(ValueError, msg=name) as cm:
                ext.see(name, doc="d")(lambda: None)
            self.assertIn(errors[0], str(cm.exception))
        ext.see(fine, doc="d")(lambda: None)
        self.assertEqual(wk.tool_name("demo", fine), "wk_demo_" + fine)

    def test_what_a_described_extension_gets_back(self):
        """an extension written with the SDK passes the workshop's check as it is"""
        ext = wk.Extension("demo")
        ext.option("volume", kind="number", default=80, min=0, max=100, step=5, unit="%", title="音量", hint="how loud")
        ext.option("voice", kind="choice", choices={"a": "甲", "b": "乙"})
        ext.option("on")
        ext.source("said", title="说完一句", doc="Each line: {text}.")

        @ext.point("pool.say", title="说一句", doc="Says a line.", args={"text": "string", "mood": "calm|happy?"},
                   timeout=60)
        def say(text, mood="calm"):
            return {}
        desc, problems = M.check_description("demo", ext.describe())
        self.assertEqual(problems, [])
        self.assertEqual(desc["capabilities"][0]["id"], "demo.pool.say")
        self.assertEqual([o["default"] for o in desc["options"]], [80, "a", False])
        self.assertEqual(desc["options"], [dict(o) for o in ext.describe()["options"]])


if __name__ == "__main__":
    unittest.main()
