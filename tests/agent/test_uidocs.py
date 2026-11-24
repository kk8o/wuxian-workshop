"""The Blizzard UI index (uidocs.py, the "ui" content pack) and what the game answered (seen.py), with a fixture pack of
the pack's schema (scripts\\build_ui_pack.py makes the real one): search and get by name, a method's two spellings, a
frame's child by its keys, a member name's candidates, on_demand addons, a missing pack; seen kept per build, the newest
answer last, at most MAX names; the check (agent/lint.py) takes a global the Blizzard UI defines for a note, not an
unresolved name, and settles a name kept in seen as the live step would."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop import apidocs, content, seen, uidocs
from wuxianworkshop.agent import lint

PACK = {
    "pack": "ui", "version": "2026.11.24.1", "client": "1.60.1.71037", "built": "2026-11-24",
    "addons": {"Blizzard_SharedXMLBase": {"load": "always", "files": 2},
               "Blizzard_EncounterJournal": {"load": "on_demand", "files": 2},
               "Blizzard_GameTooltip": {"load": "always", "files": 1}},
    "files": ["Interface/AddOns/Blizzard_SharedXMLBase/Mixin.lua",
              "Interface/AddOns/Blizzard_SharedXMLBase/TooltipDataProcessor.lua",
              "Interface/AddOns/Blizzard_EncounterJournal/Blizzard_EncounterJournal.lua",
              "Interface/AddOns/Blizzard_EncounterJournal/Blizzard_EncounterJournal.xml",
              "Interface/AddOns/Blizzard_GameTooltip/GameTooltip.xml"],
    "entries": [
        {"k": "function", "n": "CreateFromMixins", "f": 0, "l": 10, "p": ["..."]},
        {"k": "table", "n": "TooltipDataProcessor", "f": 1, "l": 3, "m": ["AddTooltipPostCall()", "AllTypes"]},
        {"k": "method", "n": "TooltipDataProcessor.AddTooltipPostCall", "f": 1, "l": 20, "p": ["tooltipType", "func"],
         "o": "TooltipDataProcessor"},
        {"k": "mixin", "n": "EncounterJournalMixin", "f": 2, "l": 5, "m": ["OnLoad()"], "mixins": []},
        {"k": "method", "n": "EncounterJournalMixin:OnLoad", "f": 2, "l": 9, "p": [], "o": "EncounterJournalMixin"},
        {"k": "frame", "n": "EncounterJournal", "f": 3, "l": 2, "t": "Frame", "inh": ["PortraitFrameTemplate"],
         "mix": ["EncounterJournalMixin"], "keys": {"encounter": "Frame", "encounter.info": "Frame",
                                                   "encounter.info.bossesScroll": "ScrollFrame"}, "parent": "UIParent"},
        {"k": "template", "n": "SecureActionButtonTemplate", "f": 4, "l": 1, "t": "Button", "inh": [], "mix": [],
         "keys": {}, "parent": None},
        {"k": "frame", "n": "GameTooltip", "f": 4, "l": 8, "t": "GameTooltip", "inh": ["SharedTooltipTemplate"],
         "mix": [], "keys": {}, "parent": None},
        {"k": "event", "n": "EJ_LOOT_DATA_RECIEVED", "f": 2, "l": 30},
        {"k": "event", "n": "EJ_LOOT_DATA_RECIEVED", "f": 1, "l": 44},
        {"k": "table", "n": "CreateFromMixins", "f": 2, "l": 99, "m": []},   # a second definition
    ],
    "left_out": {"Blizzard_StoreUI": "game type standard", "Blizzard_GlueParent": "login screen only"},
    "bootstrap": [2]}                       # loaded at login although Blizzard_EncounterJournal loads on demand


def index():
    return uidocs.UiIndex(json.loads(json.dumps(PACK)))


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


class Index(unittest.TestCase):
    def test_about(self):
        about = index().about()
        self.assertEqual((about["version"], about["client"], about["addons"], about["files"]),
                         ("2026.11.24.1", "1.60.1.71037", 3, 5))
        self.assertEqual(about["counts"]["event"], 2)
        self.assertEqual(about["counts"]["frame"], 2)

    def test_search(self):
        ui = index()
        first = ui.search("TooltipDataProcessor")[0]
        self.assertEqual((first["kind"], first["name"], first["where"], first["addon"], first["load"], first["members"]),
                         ("table", "TooltipDataProcessor", "Interface/AddOns/Blizzard_SharedXMLBase/"
                          "TooltipDataProcessor.lua:3", "Blizzard_SharedXMLBase", "always", 2))
        self.assertNotIn("note", first)
        names = [r["name"] for r in ui.search("encounter journal")]          # words, in any order of the name's parts
        self.assertEqual(names[:2], ["EncounterJournal", "EncounterJournalMixin"])
        ej = ui.search("EncounterJournal", kind="frame")[0]
        self.assertEqual((ej["load"], ej["type"], ej["mixins"], ej["keys"]), ("on_demand", "Frame",
                                                                              ["EncounterJournalMixin"], 3))
        self.assertIn('C_AddOns.LoadAddOn("Blizzard_EncounterJournal")', ej["note"])
        self.assertEqual([r["name"] for r in ui.search("EJ_LOOT")], ["EJ_LOOT_DATA_RECIEVED"])   # an event once
        self.assertEqual([r["kind"] for r in ui.search("ActionButton", kind="template")], ["template"])
        self.assertEqual(ui.search("TooltipDataProcessor.AddTooltip")[0]["sig"],
                         "TooltipDataProcessor.AddTooltipPostCall(tooltipType, func)")
        self.assertEqual(ui.search(""), [])
        self.assertEqual(len(ui.search("o", limit=2)), 2)

    def test_get(self):
        ui = index()
        tdp = ui.get("TooltipDataProcessor")
        self.assertEqual(tdp["members"], ["AddTooltipPostCall()", "AllTypes"])
        self.assertEqual(tdp["methods"], {"TooltipDataProcessor.AddTooltipPostCall": ["tooltipType", "func"]})
        self.assertEqual(ui.get("TooltipDataProcessor:AddTooltipPostCall")["params"], ["tooltipType", "func"])
        self.assertEqual(ui.get("EncounterJournalMixin.OnLoad")["name"], "EncounterJournalMixin:OnLoad")
        ej = ui.get("EncounterJournal")
        self.assertEqual((ej["keys"]["encounter.info"], ej["parent"], ej["inherits"]),
                         ("Frame", "UIParent", ["PortraitFrameTemplate"]))
        info = ui.get("EncounterJournal.encounter.info")
        self.assertEqual(info["child"], {"name": "EncounterJournal.encounter.info", "type": "Frame",
                                         "keys": {"bossesScroll": "ScrollFrame"}})
        self.assertEqual(info["of"]["name"], "EncounterJournal")
        both = ui.get("CreateFromMixins")                 # the always-loaded definition first, the other in `also`
        self.assertEqual((both["kind"], both["params"], [a["where"] for a in both["also"]]),
                         ("function", ["..."], ["Interface/AddOns/Blizzard_EncounterJournal/Blizzard_EncounterJournal.lua:99"]))
        event = ui.get("EJ_LOOT_DATA_RECIEVED")
        self.assertEqual(len(event["registered_in"]), 2)
        self.assertNotIn("also", event)
        member = ui.get("AddTooltipPostCall")
        self.assertIn("candidates", member)
        self.assertEqual({c["name"] for c in member["candidates"]},
                         {"TooltipDataProcessor.AddTooltipPostCall", "TooltipDataProcessor"})
        self.assertIsNone(ui.get("NoSuchThing"))
        self.assertIsNone(ui.get("EncounterJournal.nothere"))
        # a member the table lists without an entry of its own (review, 0.10.2: not_found, and the candidates sent the
        # agent back to the same name)
        types = ui.get("TooltipDataProcessor.AllTypes")
        self.assertEqual((types["member"], types["of"]["name"]),
                         ({"name": "TooltipDataProcessor.AllTypes", "kind": "value"}, "TooltipDataProcessor"))
        self.assertEqual(ui.get("TooltipDataProcessor:AllTypes")["member"]["name"], "TooltipDataProcessor.AllTypes")
        self.assertEqual(ui.get("AllTypes")["candidates"][0]["member"], "TooltipDataProcessor.AllTypes")
        self.assertIsNone(ui.get("TooltipDataProcessor.Nope"))

    def test_left_out_and_bootstrap(self):
        ui = index()
        self.assertEqual(ui.get("EncounterJournalMixin")["load"], "always")          # a bootstrap file
        self.assertNotIn("note", ui.get("EncounterJournalMixin"))
        self.assertEqual(ui.get("EncounterJournal")["load"], "on_demand")
        self.assertEqual(ui.not_loaded("StoreUIFrame.Title"), [{"addon": "Blizzard_StoreUI", "why": "game type standard"}])
        self.assertEqual(ui.not_loaded_text("StoreUI_Open"), "Blizzard_StoreUI is not loaded by this client (game type "
                                                             "standard)")
        self.assertEqual([r["addon"] for r in ui.not_loaded("Glue")], [])           # too short
        self.assertEqual([r["addon"] for r in ui.not_loaded("GlueP")], ["Blizzard_GlueParent"])
        self.assertEqual(ui.not_loaded("GlueP", strict=True), [])                    # strict: the addon's name in it
        self.assertEqual(ui.about()["left_out"], 2)

    def test_defines(self):
        ui = index()
        self.assertEqual(ui.defines("EncounterJournal")["k"], "frame")
        self.assertEqual(ui.addon_of(ui.defines("CreateFromMixins"))[1], "always")
        self.assertIsNone(ui.defines("SecureActionButtonTemplate"))     # a template makes no global
        self.assertIsNone(ui.defines("EJ_LOOT_DATA_RECIEVED"))
        self.assertIsNone(ui.defines("encounterjournal"))               # Lua's names are case-sensitive

    def test_no_code_in_what_it_answers(self):
        """what the tools give is the index's fields: names, places, structure"""
        out = json.dumps([index().get("TooltipDataProcessor"), index().search("Encounter")])
        for word in ("function(", "end", "local "):
            self.assertNotIn(word, out.replace("function\"", ""))

    def test_missing_pack(self):
        ui = uidocs.UiIndex(None)
        self.assertTrue(ui.missing)
        self.assertEqual((ui.search("x"), ui.get("x"), ui.defines("x")), ([], None, None))
        self.assertIn("not_installed", uidocs.NOT_INSTALLED)

    def test_the_pack_in_use(self):
        """content.py reads "ui" as it reads "api": the newer of the bundled and the downloaded copy"""
        self.assertIn("ui", content.NAMES)
        with tempfile.TemporaryDirectory() as tmp:
            import gzip
            bundled, cached = Path(tmp) / "b", Path(tmp) / "c"
            bundled.mkdir(), cached.mkdir()
            (bundled / "ui.json.gz").write_bytes(gzip.compress(json.dumps(PACK).encode()))
            (cached / "ui.json.gz").write_bytes(gzip.compress(json.dumps(dict(PACK, version="2026.11.25.1")).encode()))
            packs = content.Packs(bundled, cached)
            with mock.patch.object(content, "PACKS", packs), mock.patch.object(uidocs, "_cached", (None, None)):
                self.assertEqual(uidocs.index().about()["version"], "2026.11.25.1")
                self.assertIs(uidocs.index(), uidocs.index())
            self.assertEqual(packs.status()["ui"], {"version": "2026.11.25.1", "source": "downloaded"})


class Seen(Home):
    def test_record_and_read(self):
        self.assertIsNone(seen.get(71037, "Foo"))
        self.assertFalse((Path(self.tmp.name) / "state" / "seen").exists())     # reading leaves no folder behind
        done = seen.record("1.60.1.71037", {"Foo": "table", "Bar": "nil", "Bad": "weird", 3: "table"},
                           members={"Foo": ["b", "a"], "Bar": ["x"]}, more={"Foo": 5}, clock=lambda: 100.0)
        self.assertEqual(sorted(done), ["Bar", "Foo"])
        self.assertEqual(seen.get(71037, "Foo"), {"type": "table", "level": "presence", "at": 100.0,
                                                  "members": ["a", "b"], "more": 5})
        self.assertEqual(seen.get("71037", "Bar"), {"type": "nil", "level": "presence", "at": 100.0})
        self.assertEqual(seen.many(71037, ["Foo", "Nope"]).keys(), {"Foo"})
        self.assertIsNone(seen.get(71038, "Foo"))                       # per build
        seen.record(71037, {"Foo": "table"}, clock=lambda: 200.0)       # answered again without members: kept
        self.assertEqual(seen.get(71037, "Foo")["members"], ["a", "b"])
        self.assertEqual(seen.get(71037, "Foo")["at"], 200.0)
        seen.record(71037, {"Foo": "function"}, clock=lambda: 300.0)
        self.assertNotIn("members", seen.get(71037, "Foo"))
        self.assertEqual(seen.brief({"type": "table", "at": 1, "members": ["a"], "more": 2}),
                         {"type": "table", "level": "presence", "at": 1, "members": 3})
        seen.record(71037, {"PLAYER_SWING": "event", "NOT_AN_EVENT": "event"},
                    valid={"PLAYER_SWING": True, "NOT_AN_EVENT": False}, clock=lambda: 400.0)
        self.assertEqual(seen.get(71037, "PLAYER_SWING"), {"type": "event", "level": "presence", "at": 400.0,
                                                           "valid": True})
        self.assertEqual(seen.brief(seen.get(71037, "NOT_AN_EVENT")),
                         {"type": "event", "level": "presence", "at": 400.0, "valid": False})
        self.assertEqual(seen.record(None, {"Foo": "table"}), {})

    def test_at_most(self):
        with mock.patch.object(seen, "MAX", 3):
            for i in range(5):
                seen.record(71037, {f"N{i}": "number"}, clock=lambda i=i: float(i))
        data = json.loads((Path(self.tmp.name) / "state" / "seen" / "71037.json").read_text(encoding="utf-8"))
        self.assertEqual(list(data), ["N2", "N3", "N4"])                 # the oldest went

    def test_the_build(self):
        self.assertEqual(seen.build_key("1.60.1.71037"), "71037")
        self.assertEqual(seen.build_key(71037), "71037")
        self.assertIsNone(seen.build_key("abc"))
        self.assertEqual(seen.current(71100), "71100")                  # the running game's
        client = seen.build_key(apidocs.index().about().get("client"))
        self.assertEqual(seen.current(), client)                         # else the API manual's client
        seen.record(99999999, {"X": "nil"})
        self.assertEqual(seen.current(), "99999999")                     # or a newer one kept


class Check(Home):
    """lint.check with the fixture index: the Blizzard UI's globals are notes; seen settles the rest"""

    def addon(self, code):
        folder = Path(self.tmp.name) / "AddOns" / "UiChk"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "UiChk.toc").write_text("## Interface: 16001\nCore.lua\n", encoding="utf-8")
        (folder / "Core.lua").write_text(code, encoding="utf-8")
        return folder

    def check(self, code):
        return lint.check(self.addon(code), ix=apidocs.ApiIndex(dict(namespaces=[])), ui=index())

    def test_blizzard_names_are_notes(self):
        r = self.check("TooltipDataProcessor.AddTooltipPostCall(1, print)\n"
                       "local ej = EncounterJournal\n"
                       "local x = SomethingNobodyHas\n")
        self.assertEqual(r["unresolved"], {"SomethingNobodyHas": ["Core.lua:3"]})
        self.assertNotIn("TooltipDataProcessor", lint.live_names(r))       # not asked of the game
        notes = {n["message"].split(" ", 1)[0]: n for n in r["notes"] if n["code"] == "blizzard-ui"}
        self.assertEqual(sorted(notes), ["EncounterJournal", "TooltipDataProcessor"])
        tdp, ej = notes["TooltipDataProcessor"], notes["EncounterJournal"]
        self.assertEqual((tdp["file"], tdp["line"]), ("Core.lua", 1))
        self.assertEqual(tdp["message"], "TooltipDataProcessor is defined by the Blizzard UI: Interface/AddOns/"
                                         "Blizzard_SharedXMLBase/TooltipDataProcessor.lua:3 (Blizzard_SharedXMLBase, "
                                         "always loaded)")
        self.assertIn('(Blizzard_EncounterJournal, on_demand: needs C_AddOns.LoadAddOn("Blizzard_EncounterJournal"))',
                      ej["message"])
        self.assertIn("ADDON_LOADED", ej["hint"])
        self.assertTrue(r["ok"])

    def test_a_left_out_addons_name(self):
        r = self.check("local f = StoreUIFrame\n")
        self.assertEqual(r["unresolved"], {"StoreUIFrame": ["Core.lua:1"]})          # still for the game to settle
        [n] = [n for n in r["notes"] if n["code"] == "blizzard-left-out"]
        self.assertEqual(n["message"], "StoreUIFrame: Blizzard_StoreUI is not loaded by this client (game type standard)")

    def test_many_notes_are_one(self):
        names = [f"Blz{i:02d}" for i in range(lint.BLIZZARD_NOTES + 3)]
        pack = dict(PACK, entries=[{"k": "function", "n": n, "f": 0, "l": 1, "p": []} for n in names])
        r = lint.check(self.addon("".join(f"{n}()\n" for n in names)), ix=apidocs.ApiIndex(dict(namespaces=[])),
                       ui=uidocs.UiIndex(pack))
        notes = [n for n in r["notes"] if n["code"] == "blizzard-ui"]
        self.assertEqual(len(notes), lint.BLIZZARD_NOTES + 1)
        self.assertTrue(notes[-1]["message"].startswith("3 more globals are defined by the Blizzard UI: Blz20"))

    def test_seen_settles(self):
        code = "local a = KeptThere\nKeptNil()\nlocal b = NotKept\n"
        r = self.check(code)
        self.assertEqual(sorted(r["unresolved"]), ["KeptNil", "KeptThere", "NotKept"])
        seen.record(71037, {"KeptThere": "function", "KeptNil": "nil"})
        lint.apply_seen(r, seen.many(71037, lint.live_names(r)))
        self.assertEqual(r["unresolved"], {"NotKept": ["Core.lua:3"]})      # left for the live step
        self.assertEqual(r["seen"], {"settled": 2})
        self.assertEqual([(f["code"], f["line"], f["message"]) for f in r["errors"]],
                         [("undefined", 2, "KeptNil was nil in the game (kept from an earlier answer: api_verify asks "
                                           "again)")])
        self.assertNotIn("live", r)
        lint.apply_live(r, {"NotKept": "table"})                           # the live step after it, for the rest
        self.assertEqual((r["unresolved"], r["live"]), ({}, {"checked": 1}))
        self.assertFalse(r["ok"])
        empty = self.check(code)
        self.assertNotIn("seen", lint.apply_seen(empty, {}))

    def test_a_name_in_capitals_the_game_lacks_stays_an_error(self):
        """api_verify answers a nil name in capitals as an event the game does not know: kept so, it is still nil for
        the check (review, 0.10.2: a missing name went quiet once api_verify had asked about it)"""
        r = self.check("X_Y()\n")                          # (from four letters on, capitals pass as the UI's own)
        self.assertEqual(r["unresolved"], {"X_Y": ["Core.lua:1"]})
        lint.apply_seen(r, {"X_Y": {"type": "event", "valid": False}})
        self.assertEqual([f["code"] for f in r["errors"]], ["undefined"])
        self.assertFalse(r["ok"])
        r = self.check("X_Y()\n")
        lint.apply_seen(r, {"X_Y": {"type": "event", "valid": True}})
        self.assertEqual((r["errors"], r["unresolved"]), ([], {}))


if __name__ == "__main__":
    unittest.main()
