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

    def test_what_the_values_read_become(self):
        """kept in a local, only tested or behind a condition (guarded), or surely failing when the file loads; the globals
        and library fields a chunk defines also through local _G = _G and _G[a local constant]"""
        s = scan_of("local MAJOR = \"MyStub\"\n"                                     # 1
                    "local stub = _G[MAJOR]\n"                                       # 2
                    "if not stub then\n"                                             # 3
                    "  stub = {}\n"                                                  # 4
                    "  _G[MAJOR] = stub\n"                                           # 5 _G[a local constant]
                    "end\n"                                                          # 6
                    "local _G = _G\n"                                                # 7
                    "for i = 1, 2 do print(i) end\n"                                 # 8
                    "_G.AfterTheLoop = 1\n"                                          # 9 the local _G, past a loop
                    "local function f() _G.InAFunction = 1 end\n"                    # 10 the local as an upvalue
                    "if not string.mysplit then string.mysplit = f end\n"            # 11 a library field
                    "local Kept = OldThing\n"                                        # 12 kept
                    "local Called = GoneThing\n"                                     # 13 kept, called on 14
                    "Called()\n"                                                     # 14 when the file loads
                    "local Loyal = (C_Old and C_Old.Loyal) or OldLoyal\n"            # 15 a test, a guarded index
                    "print(Passed, { InATable })\n"                                  # 16 passed / kept in a table
                    "local function g() Kept() Direct() end\n"                       # 17 called in a function
                    "if not LaterApi then return end\n"                              # 18 only tested
                    "LaterApi()\n")                                                  # 19 after an early return
        for read in [("OldThing", 12), ("C_Old", 15), ("C_Old.Loyal", 15), ("OldLoyal", 15), ("InATable", 16),
                     ("LaterApi", 18), ("LaterApi", 19)]:
            self.assertIn(read, s["guarded"])
        for read in [("GoneThing", 13), ("Passed", 16), ("Direct", 17), ("string", 11)]:
            self.assertNotIn(read, s["guarded"])
        self.assertTrue({("OldThing", 12), ("GoneThing", 13), ("Direct", 17)} <= s["called"])   # 12: in g, an upvalue
        self.assertNotIn(("Passed", 16), s["called"])
        self.assertTrue({"MyStub", "AfterTheLoop", "InAFunction"} <= s["defines"])
        self.assertIn(("string", "mysplit"), s["fdefines"])
        env = scan_of("local env = { Paint = function() end }\n"
                      "env._CACHE = {}\n"
                      "local lib = { Env = env }\n"
                      "lib.Env.Shade = 1\n"
                      "local function later(p) env._FRAME = p end\n")
        self.assertEqual(env["env"], {"Paint", "_CACHE", "Shade", "_FRAME"})

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
        self.write("Foo.toc", "## Interface: 110200\n## SavedVariables: FooSaved\nCore.lua\nUi.lua\nMissing.lua\nFoo.xml\n"
                              "Libs\\LibThing\\LibThing.lua\n")
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
            ("event-unknown", "Core.lua", 8), ("event-restricted", "Core.lua", 9), ("lua52", "Core.lua", 10),
            ("missing-lib", "Core.lua", 14), ("protected", "Core.lua", 15), ("toc-missing", "Foo.toc", 5)})
        self.assertEqual(self.codes(r, "warnings"), {
            ("global-write", "Core.lua", 5), ("typo", "Core.lua", 11), ("api-unknown", "Core.lua", 12),
            ("api-unknown", "Core.lua", 13), ("global-write", "Ui.lua", 1), ("toc-interface", "Foo.toc", 0),
            ("moved", "Core.lua", 17)})
        hints = {f["code"]: f["hint"] for f in r["warnings"] + r["errors"]}
        self.assertEqual(hints["typo"], "did you mean UnitHealth?")
        self.assertEqual(hints["moved"], "use C_Spell.GetSpellInfo")
        self.assertIn("unpack(t)", hints["lua52"])
        self.assertIn("time()", hints["missing-lib"])
        self.assertEqual(r["unresolved"], {})
        self.assertEqual((r["libraries"], r["ok"], r["addon"], r["files"]), (1, False, "Foo", 3))

    def test_xml_encoding_and_a_file_of_an_addon(self):
        self.write("Foo.toc", "## Interface: 16001\nA.lua\nB.lua\nC.lua\nFoo.xml\n")
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

    def addon(self, name, files):
        """another addon next to Foo"""
        for rel, text in files.items():
            p = self.addons / name / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return self.addons / name

    def test_the_files_this_client_loads(self):
        """the .toc's lines for camelot ([AllowLoadGameType ...][ExcludeLoadGameType ...], on ## lines too) and what their
        XML loads (single quotes, ..\\, a comment, a Lua file by Include): only those are checked, the rest listed once"""
        self.write("Foo.toc", "## Interface: 16001\n"
                              "## Title: Foo Retail [AllowLoadGameType standard]\n"
                              "## Title: Foo Forever [AllowLoadGameType camelot][ExcludeLoadGameType standard, classic]\n"
                              "## SavedVariables: FooRetailDB [AllowLoadGameType standard]\n"
                              "Game\\load_forever.xml [AllowLoadGameType camelot][ExcludeLoadGameType standard, classic]\n"
                              "Game\\load_retail.xml [AllowLoadGameType standard]\n"
                              "Gone.lua [AllowLoadGameType camelot]\n"                       # 7: missing here
                              "Old.lua [AllowLoadGameType vanilla]\n"                         # missing, not for this client
                              "Game\\Retail\\Fix.lua [ExcludeLoadGameType camelot]\n")
        self.write("Game/load_forever.xml", "<Ui xmlns='http://www.blizzard.com/wow/ui/'>\n"
                                            "\t<Script file='Shared\\Core.lua'/>\n"
                                            "\t<!--<Include file=\"Shared\\Old.xml\"/>-->\n"
                                            "\t<Include file ='Forever\\forever.xml'/>\n"
                                            "\t<Script file='..\\Locales\\enUS.lua'/>\n"
                                            "</Ui>\n")
        self.write("Game/Forever/forever.xml", "<Ui><Script file=\"Fix.lua\"/><Include file=\"data.lua\"/>"
                                               "<Frame name='FooXmlFrame'/></Ui>")
        self.write("Game/Shared/Core.lua", "local f = CreateFrame(\"Frame\")\nf:RegisterEvent(\"PLAYER_LOGIN\")\nFooCore = {}\n")
        self.write("Game/Forever/Fix.lua", "FooCore.fix = FooData\nprint(FooXmlFrame, FooLocale)\n")
        self.write("Game/Forever/data.lua", "FooData = {}\n")
        self.write("Locales/enUS.lua", "FooLocale = {}\n")
        self.write("Game/Shared/Old.xml", "<Ui><Script file=\"Old.lua\"/></Ui>")
        self.write("Game/Shared/Old.lua", "local x = = 1\n")                         # nothing loads it here
        self.write("Game/load_retail.xml", "<Ui><Script file=\"Retail\\retail.lua\"/></Ui>")
        self.write("Game/Retail/retail.lua", "local f = CreateFrame(\"Frame\")\nf:RegisterEvent(\"NOT_AN_EVENT_HERE\")\n")
        self.write("Game/Retail/Fix.lua", "x = = 1\n")
        r = lint.check(self.folder)
        self.assertEqual(self.codes(r, "errors"), {("toc-missing", "Foo.toc", 7)})
        self.assertEqual((r["warnings"], r["unresolved"], r["files"]), ([], {}, 4))
        note = next(n for n in r["notes"] if n["code"] == "not-loaded")
        self.assertEqual(note["message"], "5 files are not loaded on this client (camelot): no .toc line for it reaches "
                                          "them: Game/load_retail.xml, Game/Retail/Fix.lua, Game/Retail/retail.lua, "
                                          "Game/Shared/Old.lua, Game/Shared/Old.xml")
        info = lint.read_toc(self.folder / "Foo.toc")
        self.assertEqual((info["title"], info["saved"]), ("Foo Forever", []))
        self.assertEqual(lint.toc_line("[Family]\\Constants.lua"), ("[Family]\\Constants.lua", True))
        self.assertEqual(lint.toc_line("My Addon [Beta]"), ("My Addon [Beta]", True))
        one = lint.check(self.folder / "Game" / "Shared" / "Old.lua", self.addons)    # asked for: checked, and said so
        self.assertEqual([f["code"] for f in one["errors"]], ["syntax"])
        self.assertIn("Game/Shared/Old.lua is not loaded on this client", one["notes"][0]["message"])
        other = self.addon("RetailOnly", {"RetailOnly.toc": "## Interface: 16001\n## AllowLoadGameType: standard, classic\n"
                                                            "Main.lua\n", "Main.lua": "local x = = 1\n"})
        r = lint.check(other)
        self.assertEqual((r["errors"], r["files"]), ([], 0))
        self.assertEqual(r["notes"][0]["message"], "the client does not load this addon: its ## AllowLoadGameType leaves "
                                                   "out camelot")

    def test_a_bom_in_front_is_dropped_as_the_client_does(self):
        self.write("Foo.toc", "## Interface: 16001\nData.lua\nUse.lua\n")
        data = self.write("Data.lua", b"\xef\xbb\xbfFooTable = { [\"a\"] = \"A\" }\n")
        self.write("Use.lua", "print(FooTable.a)\n")
        self.assertIsNone(lint.syntax(data))
        r = lint.check(self.folder)
        self.assertEqual((r["errors"], r["warnings"], r["unresolved"], r["files"]), ([], [], {}, 2))

    def test_the_globals_of_the_addons_it_depends_on(self):
        """what the addons named by ## Dependencies / RequiredDeps / OptionalDeps (and theirs) define when they are next
        to it: their globals (_G[a local constant], local _G = _G), SavedVariables, X-oUF, library fields"""
        self.addon("BaseLib", {
            "BaseLib.toc": "## Interface: 16001\n## X-oUF: BaseUF\n## SavedVariables: BaseLibDB\n"
                           "Base.lua [AllowLoadGameType camelot]\nWrath.lua [AllowLoadGameType wrath]\n",
            "Base.lua": "local MAJOR = \"BaseStub\"\n"
                        "local stub = _G[MAJOR]\n"
                        "if not stub then\n\tstub = {}\n\t_G[MAJOR] = stub\nend\n"
                        "local _G = _G\n"
                        "for i = 1, 2 do stub[i] = i end\n"
                        "_G.BaseLibGlobal = {}\n"
                        "if not string.basesplit then string.basesplit = function() end end\n",
            "Wrath.lua": "BaseWrathOnly = {}\n"})
        self.addon("Mid", {"Mid.toc": "## Interface: 16001\n## RequiredDeps: BaseLib\nMid.lua\n", "Mid.lua": "MidAPI = {}\n"})
        self.addon("Skipped", {"Skipped.toc": "## Interface: 16001\nSkipped.lua\n", "Skipped.lua": "SkippedGlobal = 1\n"})
        self.write("Foo.toc", "## Interface: 16001\n## Dependencies: Mid\n"
                              "## OptionalDeps: NotInstalled, Skipped [AllowLoadGameType classic]\nCore.lua\n")
        self.write("Core.lua", "print(BaseStub, BaseLibGlobal, BaseUF, BaseLibDB, MidAPI, Mid, BaseLib)\n"
                               "print(BaseWrathOnly, SkippedGlobal)\n"
                               "local split = string.basesplit\n")
        r = lint.check(self.folder)
        self.assertEqual((r["errors"], r["warnings"]), ([], []))
        self.assertEqual(r["unresolved"], {"BaseWrathOnly": ["Core.lua:2"], "SkippedGlobal": ["Core.lua:2"]})

    def test_a_global_kept_for_another_game_version_is_a_warning(self):
        """local X = X at the top, tests and branches: nil there (this client's, or the running game's) is a warning;
        called when the file loads, or passed on then, it is an error"""
        self.write("Foo.toc", "## Interface: 16001\nCore.lua\n")
        self.write("Core.lua", "local OldThing = OldFlavorThing\n"                                   # 1 kept
                               "local GoneThing = OldFlavorCall\n"                                   # 2 called on 8
                               "local unpack = table.unpack or unpack\n"                             # 3
                               "local time = os and os.time or time\n"                               # 4
                               "local Loyalty = (C_OldPet and C_OldPet.Loyalty) or GetOldLoyalty\n"  # 5
                               "local frame = CreateFrame(\"Frame\")\n"                               # 6
                               "function frame:Update() return OldThing(), Loyalty() end\n"          # 7 in a function
                               "local n = GoneThing()\n"                                             # 8 when it loads
                               "print(NobodyHasThis)\n"                                              # 9 passed on
                               "local t = table.unpack({1})\n"                                       # 10
                               "if not LaterApi then return end\n"                                   # 11 only tested
                               "LaterApi()\n")                                                       # 12 after a return
        r = lint.check(self.folder)
        self.assertEqual(self.codes(r, "errors"), {("lua52", "Core.lua", 10)})
        self.assertEqual(self.codes(r, "warnings"), {("lua52", "Core.lua", 3), ("missing-lib", "Core.lua", 4)})
        self.assertEqual(r["guarded"], {"OldFlavorThing": ["Core.lua:1"], "C_OldPet": ["Core.lua:5"],
                                        "GetOldLoyalty": ["Core.lua:5"], "LaterApi": ["Core.lua:11", "Core.lua:12"]})
        lint.apply_live(r, {name: "nil" for name in lint.live_names(r)})
        self.assertEqual({(f["code"], f["line"]) for f in r["errors"]},
                         {("undefined", 2), ("undefined", 9), ("lua52", 10)})
        self.assertEqual({(f["code"], f["line"]) for f in r["warnings"]}, {
            ("undefined", 1), ("lua52", 3), ("missing-lib", 4), ("undefined", 5), ("undefined", 11), ("undefined", 12)})
        self.assertEqual(r["guarded"], {})
        self.assertEqual(next(f for f in r["warnings"] if f["line"] == 1)["hint"], lint.GUARDED_HINT)

    def test_what_surely_fails_stays_an_error(self):
        """a restricted event, an unknown one registered in a file this client loads, a protected function called (only
        read: a warning)"""
        self.write("Foo.toc", "## Interface: 16001\nCore.lua\n")
        self.write("Core.lua", "local f = CreateFrame(\"Frame\")\n"                        # 1
                               "f:RegisterEvent(\"COMBAT_LOG_EVENT_UNFILTERED\")\n"         # 2
                               "f:RegisterEvent(\"UNIT_HEALTH_FREQUENT\")\n"                 # 3
                               "local Target = TargetUnit\n"                                # 4 never called
                               "local function fire() CastSpellByName(\"x\") end\n"         # 5
                               "local Pick = PickupAction\n"                                # 6 called through Pick
                               "f:SetScript(\"OnEvent\", function() Pick(1) end)\n")        # 7
        r = lint.check(self.folder)
        self.assertEqual(self.codes(r, "errors"), {("event-restricted", "Core.lua", 2), ("event-unknown", "Core.lua", 3),
                                                   ("protected", "Core.lua", 5), ("protected", "Core.lua", 6)})
        self.assertEqual(self.codes(r, "warnings"), {("protected", "Core.lua", 4)})

    def test_names_from_a_setfenv_environment_are_not_globals(self):
        """oUF's tags run with a table of their own as their globals (_TAGS, Hex, _COLORS): such names are noted, not
        asked of the game; an addon that depends on the library sees them too"""
        lib = self.addon("TagLib", {
            "TagLib.toc": "## Interface: 16001\nTags.lua\n",
            "Tags.lua": "local setfenv, setmetatable = setfenv, setmetatable\n"
                        "local _ENV = { Paint = function(r) return r end }\n"
                        "_ENV._CACHE = {}\n"
                        "local proxy = setmetatable(_ENV, { __index = _G })\n"
                        "TagLib = { Env = _ENV, Methods = {} }\n"
                        "function TagLib:Add(name, fn)\n"
                        "\tsetfenv(fn, proxy)\n"
                        "\tself.Methods[name] = fn\n"
                        "end\n"
                        "TagLib:Add(\"name\", function(u) return Paint(u) .. _CACHE[u] end)\n"
                        "local function frame(p) _ENV._FRAME = p end\n"})
        self.write("Foo.toc", "## Interface: 16001\n## RequiredDeps: TagLib\nCore.lua\n")
        self.write("Core.lua", "local Tags = TagLib\n"
                               "Tags.Env.Shade = function(x) return x end\n"
                               "Tags:Add(\"shaded\", function(u) return Shade(Paint(u)) .. _CACHE[u] .. _FRAME end)\n"
                               "print(NotInTheTable)\n")
        r = lint.check(self.folder)
        self.assertEqual((r["errors"], r["warnings"]), ([], []))
        self.assertEqual(r["unresolved"], {"NotInTheTable": ["Core.lua:4"]})
        note = next(n for n in r["notes"] if n["code"] == "setfenv")
        self.assertEqual(note["message"], "4 names are read from a function environment set with setfenv, not from the "
                                          "globals: Paint, Shade, _CACHE, _FRAME")
        own = lint.check(lib)
        self.assertEqual((own["errors"], own["warnings"], own["unresolved"]), ([], [], {}))
        self.assertIn("Paint, _CACHE", next(n for n in own["notes"] if n["code"] == "setfenv")["message"])

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
