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
                                      "hear": ["game:combat"], "reactions": []}])
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

    def test_names_and_tools(self):
        """a capability's name: dotted parts with a letter first each; its tool wk_<id>_<name> at most 48 characters,
        and no two capabilities of one extension with the same tool"""
        for good in ("say", "pool.list", "a1.b_2.c", "a" * 48):
            self.assertIsNone(M.cap_name_problem(good), good)
        for bad in ("", "Say", "1a", "a..b", ".a", "a.", "a.1b", "a._b", "a-b", "a b", "a" * 49, None, 3):
            self.assertIn("no capability name", M.cap_name_problem(bad), bad)
        self.assertEqual(M.tool_name("voice", "pool.list"), "wk_voice_pool_list")
        fits = "a" * (48 - len("wk_voice_"))
        self.assertIsNone(M.cap_name_problem(fits, "voice"))
        self.assertIn("too long", M.cap_name_problem(fits + "a", "voice"))
        desc, problems = M.check_description("voice", self.good(capabilities=[{"name": fits + "a", "kind": "see"}]))
        self.assertIsNone(desc)
        self.assertIn(f"wk_voice_{fits}a", problems[0]["message"])
        desc, problems = M.check_description("voice", self.good(capabilities=[
            {"name": "pool.list", "kind": "see"}, {"name": "pool_list", "kind": "see"}, {"name": "pool", "kind": "see"}]))
        self.assertIsNone(desc)
        self.assertEqual([p["where"] for p in problems if p["level"] == "error"], ["capability voice.pool_list"])
        self.assertIn("wk_voice_pool_list is also voice.pool.list's", M.errors_in(problems)[0]["message"])

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


class Adds(unittest.TestCase):
    """adds, an app's reactions and controls, pass (the fifth batch's contract, sections 5 and 11): what is taken,
    normalized; what is refused; the capabilities named, checked against the description once there is one"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name).resolve() / "facts"
        (self.folder / "skills").mkdir(parents=True)
        (self.folder / "AGENT.md").write_text("# facts", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, **fields):
        d = dict({"id": "facts", "title": "图鉴速查", "run": ["python", "-m", "facts"]}, **fields)
        (self.folder / M.MANIFEST).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return M.check_manifest(self.folder)

    def test_adds_taken(self):
        (self.folder / "skills" / "guild-chat.md").write_text("﻿用 notes.facts", encoding="utf-8")
        skill = str(self.folder / "skills" / "guild-chat.md")
        m, problems = self.check(adds={"guild": {"wake": "lookup", "skill": "skills/guild-chat.md"},
                                       "raid_1": {"wake": {"call": "raid.look", "timeout": 10}},
                                       "companion": {"skill": "skills/guild-chat.md"}})
        self.assertEqual(problems, [])
        self.assertEqual(m["adds"], {"guild": {"wake": {"call": "facts.lookup", "timeout": 3.0}, "skill": skill},
                                     "raid_1": {"wake": {"call": "facts.raid.look", "timeout": 10.0}, "skill": None},
                                     "companion": {"wake": None, "skill": skill}})
        m, problems = self.check()
        self.assertEqual((m["adds"], m["pass"], m["apps"], problems), ({}, [], [], []))
        self.assertNotIn("annotate", m)
        self.assertNotIn("extends", m)

    def test_adds_refused(self):
        outside = Path(self.tmp.name).resolve() / "outside.md"
        outside.write_text("x", encoding="utf-8")
        cases = [
            (["guild"], "adds"),                                                  # no object
            ({f"a{i}": {"wake": "x"} for i in range(9)}, "adds"),                 # more than 8
            ({"*": {"wake": "x"}}, "adds.*"),                                     # no wildcards
            ({"Guild": {"wake": "x"}}, "adds.Guild"),
            ({"g" * 25: {"wake": "x"}}, "adds." + "g" * 25),
            ({"guild": "lookup"}, "adds.guild"),
            ({"guild": {}}, "adds.guild"),                                        # it adds nothing
            ({"guild": {"wake": "Look"}}, "adds.guild.wake"),
            ({"guild": {"wake": "a..b"}}, "adds.guild.wake"),
            ({"guild": {"wake": 3}}, "adds.guild.wake"),
            ({"guild": {"wake": {"timeout": 3}}}, "adds.guild.wake"),             # no call
            ({"guild": {"wake": {"call": "x", "timeout": 11}}}, "adds.guild.wake.timeout"),
            ({"guild": {"wake": {"call": "x", "timeout": 0}}}, "adds.guild.wake.timeout"),
            ({"guild": {"wake": {"call": "x", "timeout": True}}}, "adds.guild.wake.timeout"),
            ({"guild": {"skill": ""}}, "adds.guild.skill"),
            ({"guild": {"skill": 3}}, "adds.guild.skill"),
            ({"guild": {"skill": "../outside.md"}}, "adds.guild.skill"),
            ({"guild": {"skill": str(outside)}}, "adds.guild.skill"),
            ({"guild": {"skill": "skills"}}, "adds.guild.skill"),                 # a folder
        ]
        for value, where in cases:
            m, problems = self.check(adds=value)
            self.assertIsNone(m, value)
            self.assertEqual(where_of(problems), [f"{M.MANIFEST}: {where}"], value)
            self.assertTrue(all(p["hint"] for p in problems), value)
        (self.folder / "skills" / "bad.md").write_bytes(b"\xff\xfe\x00bad")
        m, problems = self.check(adds={"guild": {"skill": "skills/none.md", "colour": 1},
                                       "companion": {"skill": "skills/bad.md", "wake": {"call": "x", "wait": 2}}})
        self.assertEqual(where_of(problems, "warning"),
                         [f"{M.MANIFEST}: adds.companion.wake.wait", f"{M.MANIFEST}: adds.companion.skill",
                          f"{M.MANIFEST}: adds.guild.colour", f"{M.MANIFEST}: adds.guild.skill"])
        self.assertEqual(m["adds"]["guild"]["skill"], str(self.folder / "skills" / "none.md"))   # kept: the skill says why
        self.assertEqual(m["adds"]["companion"]["wake"], {"call": "facts.x", "timeout": 3.0})

    def test_the_old_fields_are_gone(self):
        m, problems = self.check(annotate=[{"app": "guild", "call": "facts.lookup"}],
                                 extends={"guild-chat": "skills/guild-chat.md"})
        self.assertIsNone(m)
        self.assertEqual(where_of(problems), [f"{M.MANIFEST}: annotate", f"{M.MANIFEST}: extends"])
        self.assertTrue(all("adds" in p["hint"] for p in problems))
        self.assertFalse(hasattr(M, "check_annotate") or hasattr(M, "check_extends") or hasattr(M, "annotation_ok"))

    def test_pass(self):
        m, problems = self.check(**{"pass": ["HF_HOME", "ProgramFiles(x86)", "hf_home", "HTTPS_PROXY"]})
        self.assertEqual((m["pass"], problems), (["HF_HOME", "ProgramFiles(x86)", "HTTPS_PROXY"], []))
        for value, where in (("HF_HOME", "pass"), ([f"V{i}" for i in range(17)], "pass"), (["A=1"], "pass[0]"),
                             (["OK", 3], "pass[1]"), ([""], "pass[0]"), (["1A"], "pass[0]"), (["A B"], "pass[0]")):
            m, problems = self.check(**{"pass": value})
            self.assertIsNone(m, value)
            self.assertEqual(where_of(problems), [f"{M.MANIFEST}: {where}"], value)

    def app(self, **fields):
        return self.check(apps=[dict({"app": "companion", "title": "语音搭子", "prompt": "companion"}, **fields)])

    def test_reactions_taken(self):
        m, problems = self.app(reactions=[
            {"hear": "game:combat.start", "when": {"data.unit.classification": ["elite"]}, "call": "voice.say",
             "args": {"pool": "elite"}, "cooldown": 30},
            {"hear": "facts:found", "call": "facts.note", "item": "event", "note": "  what\n it is  for "}])
        self.assertEqual(problems, [])
        self.assertEqual(m["apps"][0]["reactions"], [
            {"hear": "game:combat.start", "when": {"data.unit.classification": ["elite"]}, "call": "voice.say",
             "args": {"pool": "elite"}, "item": None, "cooldown": 30, "note": None},
            {"hear": "facts:found", "when": None, "call": "facts.note", "args": None, "item": "event", "cooldown": 10,
             "note": "what it is for"}])
        self.assertEqual(self.app()[0]["apps"][0]["reactions"], [])
        self.assertNotIn("controls", self.app()[0]["apps"][0])

    def test_reactions_as_the_engine_checks_them(self):
        """what a manifest takes, reactions.py takes too (with an owner, and call a capability there is)"""
        import asyncio
        from wuxianworkshop.local import reactions as R
        specs = [{"hear": "game:combat.start", "when": {"data.unit.classification": ["elite"], "kind": "combat.start"},
                  "call": "voice.say", "args": {"pool": "elite"}, "cooldown": 0},
                 {"hear": "chat:guild", "call": "facts.note", "item": "event", "note": "why"}]
        m, _ = self.app(reactions=specs)

        async def kind_of(cap):
            return "point"
        engine = R.Reactions(lambda *a, **k: None, lambda *a, **k: None, kind_of)
        for spec, mine in zip(specs, m["apps"][0]["reactions"]):
            theirs = asyncio.run(engine._check(dict(spec, owner="companion")))
            self.assertEqual({k: theirs[k] for k in M.REACTION_FIELDS}, mine)

    def test_reactions_refused(self):
        ok = {"hear": "game:combat.start", "call": "voice.say"}
        cases = [
            ({"hear": "game:combat"}, "reactions"),                               # no list
            ([ok] * 17, "reactions"),                                             # more than 16
            (["game:combat"], "reactions[0]"),
            ([dict(ok, id="r1")], "reactions[0]"),                                # the workshop names it
            ([dict(ok, owner="companion")], "reactions[0]"),
            ([dict(ok, colour=1)], "reactions[0]"),
            ([ok, {"call": "voice.say"}], "reactions[1]"),                        # no hear
            ([dict(ok, hear=["game:combat"])], "reactions[0]"),
            ([dict(ok, hear="combat")], "reactions[0]"),
            ([dict(ok, when={"data": {"a": 1}})], "reactions[0]"),
            ([dict(ok, when=["kind"])], "reactions[0]"),
            ([dict(ok, call="say")], "reactions[0]"),                             # a whole capability id
            ([dict(ok, call="Voice.say")], "reactions[0]"),
            ([dict(ok, args=["pool"])], "reactions[0]"),
            ([dict(ok, args={"big": "x" * 5000})], "reactions[0]"),
            ([dict(ok, item="a b")], "reactions[0]"),
            ([dict(ok, item="event", args={"event": 1})], "reactions[0]"),
            ([dict(ok, cooldown=-1)], "reactions[0]"),
            ([dict(ok, cooldown=True)], "reactions[0]"),
            ([dict(ok, note="x" * 201)], "reactions[0]"),
        ]
        for value, where in cases:
            m, problems = self.app(reactions=value)
            self.assertIsNone(m, value)
            self.assertEqual(where_of(problems), [f"{M.MANIFEST}: apps[0].{where}"], value)
            self.assertTrue(all(p["hint"] for p in problems), value)

    def test_controls(self):
        m, problems = self.app(controls=["stop", {"cmd": "mute", "call": "mute"},
                                         {"cmd": "again", "label": "再说一遍", "call": "voice.again"},
                                         {"cmd": "skip", "label": "跳过"}])
        self.assertEqual(problems, [])
        self.assertEqual(m["apps"][0]["controls"], ["stop", {"cmd": "mute", "call": "mute"},
                                                    {"cmd": "again", "label": "再说一遍", "call": "voice.again"},
                                                    {"cmd": "skip", "label": "跳过"}])
        m, problems = self.app(controls=[{"cmd": "mute", "label": "静音", "call": "mute", "colour": 1}])
        self.assertEqual(where_of(problems, "warning"), [f"{M.MANIFEST}: apps[0].controls[0].colour",
                                                         f"{M.MANIFEST}: apps[0].controls[0].label"])
        self.assertEqual(m["apps"][0]["controls"], [{"cmd": "mute", "call": "mute"}])     # WuxianKit's own label
        cases = [
            ("stop", "controls"), (["stop"] * 13, "controls"), ([3], "controls[0]"), (["again"], "controls[0]"),
            ([{"label": "再说"}], "controls[0].cmd"), ([{"cmd": "Again", "label": "再说"}], "controls[0].cmd"),
            ([{"cmd": "again"}], "controls[0].label"), ([{"cmd": "again", "label": "再 说"}], "controls[0].label"),
            ([{"cmd": "again", "label": "再说", "call": "facts.Again"}], "controls[0].call"),
            ([{"cmd": "again", "label": "再说", "call": 3}], "controls[0].call"),
            (["stop", {"cmd": "stop", "call": "bye"}], "controls[1]"),
        ]
        for value, where in cases:
            m, problems = self.app(controls=value)
            self.assertIsNone(m, value)
            self.assertEqual(where_of(problems), [f"{M.MANIFEST}: apps[0].{where}"], value)

    def test_against_the_description(self):
        m, _ = self.check(adds={"guild": {"wake": "lookup"}, "raid": {"wake": "mark"}, "pvp": {"wake": "ghost"},
                                "bg": {"wake": "bare"}, "arena": {"skill": "skills/a.md"}},
                          apps=[{"app": "companion", "controls": ["stop", {"cmd": "mute", "call": "mark"},
                                                                  {"cmd": "again", "label": "再说", "call": "gone"}],
                                 "reactions": [{"hear": "game:combat", "call": "facts.mark"},
                                               {"hear": "game:combat", "call": "facts.missing"},
                                               {"hear": "game:combat", "call": "voice.say"}]}])
        before = M.adds_problems(m, None)
        self.assertEqual([(p["level"], p["where"]) for p in before], [("warning", M.MANIFEST)])
        self.assertIn("has not described itself", before[0]["message"])
        self.assertIn("bare, ghost, gone, lookup, mark, missing", before[0]["message"])
        desc = {"capabilities": [{"name": "lookup", "kind": "see", "args": {"wake": "table", "text": "string?"}},
                                 {"name": "mark", "kind": "keep", "args": {"wake": "table"}},
                                 {"name": "bare", "kind": "see", "args": {}}]}
        after = M.adds_problems(m, desc)
        self.assertEqual([(p["level"], p["where"].partition(": ")[2]) for p in after],
                         [("warning", "adds.bg.wake"), ("error", "adds.pvp.wake"), ("error", "adds.raid.wake"),
                          ("error", "apps[0].controls[2].call"), ("error", "apps[0].reactions[1].call")])
        self.assertIn("takes no argument wake", after[0]["message"])
        self.assertIn("has no capability facts.ghost (it has: bare, lookup, mark)", after[1]["message"])
        self.assertIn("a wake's note only reads", after[2]["message"])
        self.assertFalse(hasattr(M, "annotate_problems"))                 # the fourth batch's name
        self.assertEqual(M.adds_problems(self.check()[0], None), [])       # nothing named: nothing to say
        self.assertEqual(M.adds_problems(None, None), [])


if __name__ == "__main__":
    unittest.main()
