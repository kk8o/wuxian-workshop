"""agent/lint.py: the checks before the game — Lua 5.1 syntax, the globals of the compiled bytecode against this client,
the .toc and the XML, the cache, and what the running game's answers change."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from wuxianworkshop import scaffold
from wuxianworkshop.agent import lint, probes

ROOT = Path(__file__).resolve().parents[2]


def scan_of(src):
    dump, err = lint.compile_lua(src.encode("utf-8"), "@test.lua")
    assert err is None, err
    return lint.scan(lint.parse_dump(dump))


class Bytecode(unittest.TestCase):
    def test_reads_writes_fields_events_and_frames_with_their_lines(self):
        s = scan_of("local ns = {}\n"                                        # 1
                    "counter = (counter or 0) + 1\n"                        # 2
                    "local f = CreateFrame(\"Frame\", \"MyFrame\", UIParent)\n"  # 3
                    "f:RegisterEvent(\"BAG_UPDATE\")\n"                       # 4
                    "local function g()\n"                                    # 5
                    "  return C_Spell.GetSpellInfo(1), table.unpack({})\n"    # 6
                    "end\n"                                                   # 7
                    "_G.Leak = 1\n"                                           # 8
                    "f:RegisterUnitEvent(\"UNIT_HEALTH\", \"player\")\n")     # 9
        self.assertIn(("counter", 2), s["reads"])
        self.assertIn(("counter", 2), s["writes"])
        self.assertIn(("CreateFrame", 3), s["reads"])
        self.assertIn(("MyFrame", 3), s["frames"])
        self.assertIn(("RegisterEvent", "BAG_UPDATE", 4), s["events"])
        self.assertIn(("RegisterUnitEvent", "UNIT_HEALTH", 9), s["events"])
        self.assertIn(("C_Spell", "GetSpellInfo", 6), s["fields"])          # inside a nested function
        self.assertIn(("table", "unpack", 6), s["fields"])
        self.assertIn(("Leak", 8), s["writes"])                              # _G.Leak = ...
        self.assertNotIn("ns", [n for n, _ in s["reads"]])                   # locals are not globals

    def test_syntax_errors_with_the_5_1_way(self):
        cases = {
            "local x = 7 // 2\n": ("integer division", 1),
            "for i = 1, 3 do\n  if i == 2 then goto continue end\nend\n": ("no goto", 2),
            "local a = 1\nlocal b = a & 3\n": ("bit.band", 2),
            "local a = 1 << 2\n": ("bit.lshift", 1),
            "x = = 1\n": ("", 1),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for i, (src, (hint, line)) in enumerate(cases.items()):
                p = Path(tmp) / f"f{i}.lua"
                p.write_text(src, encoding="utf-8")
                found = lint.syntax(p)
                self.assertEqual((found["code"], found["line"]), ("syntax", line), src)
                self.assertIn(hint, found["hint"], src)
                if not hint:
                    self.assertEqual(found["hint"], "")
            ok = Path(tmp) / "ok.lua"
            ok.write_text("local t = {}\nreturn t\n", encoding="utf-8")
            self.assertIsNone(lint.syntax(ok))

    def test_typos_are_one_slip(self):
        self.assertEqual(lint.edits("UnitHelth", "UnitHealth"), 1)
        self.assertEqual(lint.edits("UnitHaelth", "UnitHealth"), 1)                  # two letters swapped
        self.assertEqual(lint.typo_of("UnitHelth", ["UnitHealth", "UnitHealthMax"]), "UnitHealth")
        self.assertIsNone(lint.typo_of("WoWBridgeDB", ["WoWBridge", "WoWBridgeNS"]))  # another name, not a slip
        self.assertIsNone(lint.typo_of("MyThing", ["MyThingy"]))                     # only longer at an end
        self.assertEqual(lint.typo_of("GetSpelInfo", ["GetSpellInfo", "GetSpellName"], loose=True), "GetSpellInfo")


class Addons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addons = Path(self.tmp.name) / "AddOns"
        self.folder = self.addons / "Foo"
        self.folder.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        return p

    def codes(self, report, level):
        return {(f["code"], f["file"], f["line"]) for f in report[level]}

    def test_what_would_fail_and_what_may_be_wrong(self):
        self.write("Foo.toc", "## Interface: 110200\n## SavedVariables: FooSaved\nCore.lua\nUi.lua\nMissing.lua\nFoo.xml\n")
        self.write("Core.lua", "local addonName, ns = ...\n"                              # 1
                               "FooSaved = FooSaved or {}\n"                              # 2 a SavedVariable
                               "FooDB = {}\n"                                             # 3 the addon's own prefix
                               "SLASH_FOO1 = \"/foo\"\n"                                  # 4
                               "counter = 0\n"                                            # 5 forgot local
                               "local f = CreateFrame(\"Frame\", \"FooMain\", UIParent)\n"  # 6
                               "f:RegisterEvent(\"BAG_UPDATE\")\n"                        # 7
                               "f:RegisterEvent(\"BAG_UPDATED_TYPO\")\n"                  # 8
                               "f:RegisterEvent(\"COMBAT_LOG_EVENT_UNFILTERED\")\n"       # 9
                               "local t = table.unpack({1})\n"                            # 10
                               "local hp = UnitHelth(\"player\")\n"                       # 11
                               "local i = C_Spell.GetSpelInfo(1)\n"                      # 12
                               "local q = Enum.ItemQualty\n"                              # 13
                               "print(os.time(), FooMain, FooFromXml, SharedThing)\n"     # 14
                               "CastSpellByName(\"x\")\n"                                 # 15
                               "local s = string.trim(\" x \")\n"                         # 16 WoW's own
                               "local info = GetSpellInfo(1)\n")                          # 17 moved to C_Spell
        self.write("Ui.lua", "SharedThing = FooDB\n")                                     # defined in another file
        self.write("Foo.xml", '<Ui><Frame name="FooFromXml"/></Ui>')
        self.write("Libs/LibThing/LibThing.lua", "LibThing = {}\nleaky = os.time()\n")   # a library: not reported
        r = lint.check(self.folder)
        self.assertEqual(self.codes(r, "errors"), {
            ("event-unknown", "Core.lua", 8), ("lua52", "Core.lua", 10), ("missing-lib", "Core.lua", 14),
            ("toc-missing", "Foo.toc", 5)})
        self.assertEqual(self.codes(r, "warnings"), {
            ("global-write", "Core.lua", 5), ("event-restricted", "Core.lua", 9), ("typo", "Core.lua", 11),
            ("api-unknown", "Core.lua", 12), ("api-unknown", "Core.lua", 13), ("protected", "Core.lua", 15),
            ("global-write", "Ui.lua", 1), ("toc-interface", "Foo.toc", 0), ("moved", "Core.lua", 17)})
        hints = {f["code"]: f["hint"] for f in r["warnings"] + r["errors"]}
        self.assertEqual(hints["typo"], "did you mean UnitHealth?")
        self.assertEqual(hints["moved"], "use C_Spell.GetSpellInfo")
        self.assertIn("unpack(t)", hints["lua52"])
        self.assertIn("time()", hints["missing-lib"])
        self.assertEqual(r["unresolved"], {})
        self.assertEqual((r["libraries"], r["ok"], r["addon"], r["files"]), (1, False, "Foo", 3))

    def test_xml_encoding_and_a_file_of_an_addon(self):
        self.write("Foo.toc", "## Interface: 16001\nA.lua\nB.lua\nFoo.xml\n")
        self.write("A.lua", "FooShared = 1\n")
        self.write("B.lua", "print(FooShared, NobodyHasThis)\n")
        self.write("C.lua", "-- 中文\n".encode("gb18030"))
        self.write("Foo.xml", "<Ui><Frame name=\"X\"></Ui>")
        r = lint.check(self.folder / "B.lua", self.addons)          # one file: the addon's other files define names
        self.assertEqual((r["files"], r["errors"], r["warnings"]), (1, [], []))
        self.assertEqual(r["unresolved"], {"NobodyHasThis": ["B.lua:1"]})
        whole = lint.check(self.folder)
        self.assertIn(("xml", "Foo.xml", 1), self.codes(whole, "errors"))
        self.assertIn(("encoding", "C.lua", 0), self.codes(whole, "warnings"))

    def test_out_of_date_against_the_running_client(self):
        """the toc check compares with the Interface of the client whose AddOns folder this is (after a game update too)"""
        client = Path(self.tmp.name) / "client"                    # a client folder of its own (not the temp folder's parent)
        addons = client / "Interface" / "AddOns"
        (addons / "Foo").mkdir(parents=True)
        (client / ".build.info").write_text("Branch!STRING:0|Active!DEC:1|Version!STRING:0|Product!STRING:0\n"
                                            "cn|1|1.61.0.70500|wow_cn_beta\n", encoding="utf-8")
        (addons / "Foo" / "Foo.toc").write_text("## Interface: 16001\nA.lua\n", encoding="utf-8")
        (addons / "Foo" / "A.lua").write_text("local x = 1\n", encoding="utf-8")
        r = lint.check(addons / "Foo", addons)
        found = [f for f in r["warnings"] if f["code"] == "toc-interface"]
        self.assertEqual([(f["message"], f["hint"]) for f in found], [(
            "Interface 16001 is not this client's (16100): the game lists the addon as out of date", "## Interface: 16100")])

    def test_a_changed_file_is_compiled_again(self):
        p = self.write("A.lua", "local x = 1\n")
        self.assertIsNone(lint.syntax(p))
        p.write_text("local x = = 1\n", encoding="utf-8")
        t = time.time_ns() + 10 ** 9
        os.utime(p, ns=(t, t))
        self.assertEqual(lint.syntax(p)["line"], 1)

    def test_the_game_settles_what_the_manual_does_not(self):
        self.write("Foo.toc", "## Interface: 16001\nA.lua\n")
        self.write("A.lua", "print(FromAnotherAddon, NobodyHasThis, UnitHelth, C_Spell.GetSpelInfo)\n")
        r = lint.check(self.folder)
        self.assertEqual(lint.live_names(r), ["C_Spell.GetSpelInfo", "FromAnotherAddon", "NobodyHasThis", "UnitHelth"])
        chunk = lint.live_chunk(lint.live_names(r), probes.LUA_JSON, probes.lua_str)
        self.assertIn("[[C_Spell.GetSpelInfo]]", chunk)
        self.assertIsNone(lint.compile_lua(chunk.encode(), "=chunk")[1])          # it compiles under Lua 5.1
        lint.apply_live(r, {"FromAnotherAddon": "table", "NobodyHasThis": "nil", "UnitHelth": "function",
                            "C_Spell.GetSpelInfo": "nil"})
        self.assertEqual({(f["code"], f["message"]) for f in r["errors"]}, {
            ("undefined", "NobodyHasThis is nil in the running game"),
            ("undefined", "C_Spell.GetSpelInfo is nil in the running game")})
        self.assertEqual(r["warnings"], [])                                    # UnitHelth exists there after all
        self.assertEqual((r["unresolved"], r["live"], r["ok"]), ({}, {"checked": 4}, False))

    def test_the_templates_and_our_own_addons_are_clean(self):
        for template in ("basic", "window"):
            name = "Tpl" + template.title()
            scaffold.create(self.addons, name, None, "", template)
            r = lint.check(self.addons / name)
            self.assertEqual((r["errors"], r["warnings"], r["unresolved"]), ([], [], {}), template)
        for name in ("WoWBridge", "!WuxianWorkshop"):
            r = lint.check(ROOT / "addon" / name)
            self.assertEqual((r["errors"], r["warnings"]), ([], []), name)


if __name__ == "__main__":
    unittest.main()
