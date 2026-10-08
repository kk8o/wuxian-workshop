"""The AddOns listing (installer/addons.py): the .toc fields, AddOns.txt states and !WuxianWorkshop's collected errors,
on a fake client folder."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from tests.installer import fake
from wuxianworkshop.installer import addons as A

ERRORS_LUA = """
WuxianWorkshopDB = {
["dropped"] = 2,
["settings"] = {
["report"] = true,
},
["errors"] = {
["Interface/AddOns/Bar/Frames.lua:#: attempt to call method 'SetPoint' (a nil value)"] = {
["kind"] = "ERR",
["message"] = "Interface/AddOns/Bar/Frames.lua:201: attempt to call method 'SetPoint' (a nil value)",
["addon"] = "Bar",
["stack"] = "[string \\"@Interface/AddOns/Bar/Frames.lua\\"]:201: in function `Layout'",
["count"] = 14,
["first"] = 1791200000,
["last"] = 1791270000,
["seq"] = 3,
["build"] = "1.60.1.70235",
},
["Unknown unit token 'focus'"] = {
["kind"] = "WARN",
["message"] = "Interface/AddOns/Foo/Core.lua:88: Unknown unit token 'focus'",
["addon"] = "Foo",
["count"] = 3,
["first"] = 1791200100,
["last"] = 1791280000,
["seq"] = 9,
},
["ADDON_ACTION_BLOCKED UNKNOWN CastSpellByName()"] = {
["kind"] = "BLOCKED",
["message"] = "ADDON_ACTION_BLOCKED: CastSpellByName()",
["count"] = 1,
["first"] = 1791100000,
["last"] = 1791100000,
},
},
}
"""


class Listing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.game = fake.make_client(self.tmp.name)
        self.addons = self.game / "Interface" / "AddOns"
        self.toc("Bar", "## Interface: 16001\n## Title: |cff00ff00Bar|r Bars\n## Title-zhCN: Bar 动作条\n## Version: 2.3.1\n"
                        "## Notes: Action |TInterface\\Icons\\x:0|t bars|nmore\n## Author: Someone\n## Dependencies: Lib, Base\n"
                        "## OptionalDeps: Masque\n## SavedVariables: BarDB\n## SavedVariablesPerCharacter: BarCharDB\n"
                        "## X-Wuxian-ID: bar-1\n\nBar.lua\n")
        self.toc("Foo", "## Interface: 110105, 50500\n## Title: Foo\n\nFoo.lua\n")
        self.toc("Flavoured", "## Interface: 16001\n## Title: Flavoured\n", name="Flavoured_Vanilla.toc")
        self.toc("Flavoured", "## Interface: 16001\n## Title: Flavoured (Mainline)\n", name="Flavoured_Mainline.toc")
        self.toc("Lazy", "## Interface: 16001\n## LoadOnDemand: 1\n")
        self.toc("Bar_Options", "## Interface: 16001\n## Title: Bar Options\n## Group: Bar\n## LoadOnDemand: 1\n")
        (self.addons / "NoToc").mkdir()
        (self.addons / "NoToc" / "x.lua").write_text("-- no toc\n", encoding="utf-8")
        wtf = self.game / "WTF" / "Account" / "123#1"
        for realm, char, text in (("R", "Alice", "Foo: disabled\nBar: enabled\n"),
                                  ("R", "Bob", "Foo: enabled\nLazy: disabled\nNoToc: disabled\n"),
                                  ("S", "Carl", "Lazy: disabled\n")):
            (wtf / realm / char).mkdir(parents=True)
            (wtf / realm / char / "AddOns.txt").write_text(text, encoding="utf-8")

    def toc(self, folder, text, name=None):
        (self.addons / folder).mkdir(exist_ok=True)
        (self.addons / folder / (name or f"{folder}.toc")).write_text(text, encoding="utf-8")

    def by_name(self, res):
        return {a["name"]: a for a in res["addons"]}

    def test_toc_fields(self):
        res = A.list_addons(self.game)
        self.assertEqual(res["client"], {"version": fake.CLIENT_VERSION, "interface": 16001})
        self.assertEqual(res["characters"], 3)
        bar = self.by_name(res)["Bar"]
        self.assertEqual((bar["title"], bar["version"], bar["interface"], bar["current"]), ("Bar 动作条", "2.3.1", [16001], True))
        self.assertEqual((bar["notes"], bar["author"], bar["deps"], bar["optional_deps"]),
                         ("Action  bars more", "Someone", ["Lib", "Base"], ["Masque"]))
        self.assertEqual((bar["saved_variables"], bar["wuxian_id"], bar["toc"]), (["BarDB", "BarCharDB"], "bar-1", "Bar.toc"))
        foo = self.by_name(res)["Foo"]
        self.assertEqual((foo["interface"], foo["current"]), ([110105, 50500], False))     # not this client's
        flav = self.by_name(res)["Flavoured"]
        self.assertEqual((flav["toc"], flav["title"]), ("Flavoured_Mainline.toc", "Flavoured (Mainline)"))
        self.assertTrue(self.by_name(res)["Lazy"]["load_on_demand"])
        none = self.by_name(res)["NoToc"]
        self.assertEqual((none["toc"], none["title"], none["interface"], none["current"]), (None, "NoToc", [], None))
        self.assertEqual(self.by_name(res)["WoWBridge"]["ours"], "developer")
        self.assertEqual(self.by_name(res)["Bar_Options"]["group"], "Bar")                # ## Group
        self.assertIsNone(bar["group"])
        self.assertEqual(self.by_name(res)["WoWBridge"]["group"], "!WuxianWorkshop")      # a v0.7 toc without it
        names = [a["name"] for a in res["addons"]]
        self.assertEqual(names, sorted(names, key=str.lower))

    def test_lines_for_other_game_types(self):
        """a .toc for several game types: its ## lines for this client (camelot) without their load conditions; a line
        for other game types is not read (the last Title is another game's)"""
        self.toc("Multi", "## Interface: 11508, 16001, 120001\n"
                          "## Title: |cff00ccffMulti|r Classic [AllowLoadGameType vanilla]\n"
                          "## Title: |cff00ccffMulti|r Forever [AllowLoadGameType camelot][ExcludeLoadGameType standard, classic]\n"
                          "## Title: |cff00ccffMulti|r Retail [AllowLoadGameType standard]\n"
                          "## Notes: Bars for every game [Beta]\n"
                          "## OptionalDeps: LibClassicOnly [AllowLoadGameType classic]\n"
                          "## SavedVariables: MultiDB\n"
                          "## SavedVariables: MultiRetailDB [ExcludeLoadGameType camelot]\n\n"
                          "Game\\load_forever.xml [AllowLoadGameType camelot]\n")
        multi = self.by_name(A.list_addons(self.game))["Multi"]
        self.assertEqual((multi["title"], multi["notes"], multi["optional_deps"], multi["saved_variables"], multi["current"]),
                         ("Multi Forever", "Bars for every game [Beta]", [], ["MultiDB"], True))

    def test_addons_txt(self):
        got = {n: (a["enabled"], a["disabled_in"]) for n, a in self.by_name(A.list_addons(self.game)).items()}
        self.assertEqual(got["Bar"], (True, 0))
        self.assertEqual(got["Foo"], (None, 1))            # one character disables it, another enables it
        self.assertEqual(got["Lazy"], (False, 2))          # every character that names it disables it
        self.assertEqual(got["NoToc"], (False, 1))
        self.assertEqual(got["SomeOtherAddon"], (True, 0))  # named nowhere: enabled

    def test_a_client_this_release_has_not_seen(self):
        """its Interface comes from its version (9.9.9 -> 90909): an addon still at 16001 is out of date there"""
        (self.game.parent / ".build.info").write_text("\n".join([fake.BUILD_INFO_HEADER, fake.build_info_row("9.9.9.1")]) + "\n",
                                                      encoding="utf-8")
        res = A.list_addons(self.game)
        self.assertEqual(res["client"], {"version": "9.9.9.1", "interface": 90909})
        self.assertIs(self.by_name(res)["Bar"]["current"], False)

    def test_no_errors_yet(self):
        res = A.list_addons(self.game)
        self.assertFalse(res["errors"]["available"])
        self.assertIn("SavedVariables", res["errors"]["reason"])
        self.assertEqual(self.by_name(res)["Bar"]["errors"], {"signatures": 0, "count": 0})

    def write_errors(self, account="123#1", text=ERRORS_LUA, mtime=None):
        p = self.game / "WTF" / "Account" / account / "SavedVariables" / "!WuxianWorkshop.lua"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if mtime:
            os.utime(p, (mtime, mtime))
        return p

    def test_errors_per_addon(self):
        self.write_errors()
        res = A.list_addons(self.game)
        by = self.by_name(res)
        self.assertEqual(by["Bar"]["errors"], {"signatures": 1, "count": 14})
        self.assertEqual(by["Foo"]["errors"], {"signatures": 1, "count": 3})
        e = res["errors"]
        self.assertEqual((e["available"], e["report"], e["dropped"], e["signatures"], e["unattributed"]),
                         (True, True, 2, 3, {"signatures": 1, "count": 1}))
        self.assertNotIn("entries", e)

    def test_error_entries(self):
        self.write_errors()
        res = A.list_errors(self.game)
        self.assertEqual([x["kind"] for x in res["entries"]], ["WARN", "ERR", "BLOCKED"])      # newest first
        self.assertEqual(res["total"], 3)
        bar = A.list_errors(self.game, addon="Bar")["entries"]
        self.assertEqual(len(bar), 1)
        self.assertEqual((bar[0]["count"], bar[0]["build"], bar[0]["stack"]),
                         (14, "1.60.1.70235", "[string \"@Interface/AddOns/Bar/Frames.lua\"]:201: in function `Layout'"))
        self.assertEqual(len(A.list_errors(self.game, limit=1)["entries"]), 1)

    def test_newest_account_wins_and_broken_file(self):
        self.write_errors("111#1", ERRORS_LUA.replace('["report"] = true', '["report"] = false'), mtime=time.time() - 3600)
        self.write_errors("123#1")
        self.assertIs(A.read_errors(self.game)["report"], True)
        self.write_errors("123#1", "WuxianWorkshopDB = { broken", mtime=time.time() + 10)
        res = A.read_errors(self.game)
        self.assertFalse(res["available"])
        self.assertIn("!WuxianWorkshop.lua", res["reason"])

    def test_no_game_folder(self):
        with self.assertRaises(FileNotFoundError):
            A.list_addons(Path(self.tmp.name) / "nowhere")


class Helpers(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(A.plain("|cffff0000Red|r and |A:atlas:16:16|a icon|nnext"), "Red and  icon next")

    def test_read_toc_takes_this_clients_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "X.toc"
            p.write_text("## Title: |cff00ccffX|r\n"
                         "## Title-zhCN: 某插件 [AllowLoadGameType camelot]\n"
                         "## Title-zhCN: 某插件（正式服） [AllowLoadGameType standard]\n", encoding="utf-8")
            self.assertEqual(A.read_toc(p), {"Title": "|cff00ccffX|r", "Title-zhCN": "某插件"})


if __name__ == "__main__":
    unittest.main()
