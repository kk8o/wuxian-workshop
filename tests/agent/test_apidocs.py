"""The API manual's index (wuxianworkshop/apidocs.py) over a small made-up pack, and over the pack the program carries;
the game scan in it (scripts/build_api_pack.py)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop import apidocs, content

ROOT = Path(__file__).resolve().parents[2]


def arg(n, t, nil=False, default=None):
    return {"n": n, "t": t, "nil": nil, "def": default}


PACK = {
    "pack": "api", "version": "2026.10.06.1", "client": "1.60.1.70094", "interface": 16001,
    "namespaces": [
        {"ns": "C_Spell", "name": "Spell", "functions": [
            {"name": "GetSpellInfo", "args": [arg("spellIdentifier", "SpellIdentifier")],
             "rets": [arg("spellInfo", "SpellInfo", nil=True)], "doc": "Returns nil if spell is not found",
             "raw": {"SecretArguments": "AllowedWhenTainted"}},
            {"name": "GetSpellCooldown", "args": [arg("spellIdentifier", "SpellIdentifier")],
             "rets": [arg("info", "SpellCooldownInfo")], "doc": "", "raw": {"SecretWhenCooldownsRestricted": True}}],
         "events": [{"name": "SPELL_UPDATE_COOLDOWN", "payload": [], "doc": "", "raw": {}},
                    {"name": "COMBAT_LOG_EVENT", "payload": [], "doc": "", "raw": {"HasRestrictions": True}}],
         "tables": [{"name": "SpellInfo", "type": "Structure", "fields": [{"n": "name", "t": "cstring", "v": None, "nil": False}]}]},
        {"ns": "", "name": "Unit", "functions": [
            {"name": "UnitHealth", "args": [arg("unit", "UnitToken"), arg("usePredicted", "bool", default=True)],
             "rets": [arg("result", "number")], "doc": "", "raw": {"SecretReturns": True}},
            {"name": "CastSpellByName", "args": [arg("name", "cstring")], "rets": [], "doc": "",
             "raw": {"IsProtectedFunction": True}},
            {"name": "TargetUnit", "args": [arg("unit", "UnitToken")], "rets": [], "doc": "", "raw": {}}],
         "events": [], "tables": []},
        {"ns": "C_Other", "name": "Other", "functions": [
            {"name": "GetSpellInfo", "args": [], "rets": [], "doc": "another one", "raw": {}}], "events": [], "tables": []},
    ],
    "usage": ["Usage: %s:AddAtlas(\"atlas\")", "Usage: local spellInfo = C_Spell.GetSpellInfo(spellIdentifier)",
              "Usage: SpellTargetUnit(\"unit\")", "Usage: TargetUnit([name, exactMatch])"],
    "protected_globals": ["TargetUnit", "CastSpellByName"],
    "changes": [{"from": "1.60.1.70094", "to": "1.60.1.70245", "date": "2026-10-08",
                 "added": [{"what": "新增函数", "name": "C_Spell.GetSpellCooldown", "note": "(spellIdentifier: SpellIdentifier) → info: SpellCooldownInfo"}],
                 "changed": [{"what": "改动函数", "name": "UnitHealth", "note": "新增标记 SecretReturns"}], "removed": []}],
    "manual": [{"id": "taint", "title": "安全与污染（taint）", "md": "插件代码默认是受污染的。",
                "title_en": "Security and taint", "md_en": "Addon code is tainted by default."},
               {"id": "secret", "title": "机密值（secret values）", "md": "战斗中部分 API 返回机密值。"}],
}


class Index(unittest.TestCase):
    def setUp(self):
        self.ix = apidocs.ApiIndex(PACK)

    def test_ranking_and_kinds(self):
        r = self.ix.search("UnitHealth")
        self.assertEqual(r[0]["name"], "UnitHealth")
        self.assertEqual(r[0]["sig"], "UnitHealth(unit: UnitToken, usePredicted: bool = true) → result: number")
        self.assertIn("可能返回机密值：SecretReturns", r[0]["flags"])
        names = [x["name"] for x in self.ix.search("spell", kind="function")]
        self.assertEqual(set(names), {"C_Spell.GetSpellInfo", "C_Spell.GetSpellCooldown", "C_Other.GetSpellInfo",
                                      "CastSpellByName"})
        self.assertEqual([x["name"] for x in self.ix.search("cooldown", kind="event")], ["SPELL_UPDATE_COOLDOWN"])
        self.assertEqual(self.ix.search("c_spell.getspellinfo")[0]["name"], "C_Spell.GetSpellInfo")   # exact first
        self.assertEqual(self.ix.search("not found")[0]["name"], "C_Spell.GetSpellInfo")             # the description
        self.assertEqual(self.ix.search("AddAtlas")[0]["kind"], "usage")
        self.assertEqual(self.ix.search(""), [])

    def test_flags(self):
        self.assertIn("受保护", self.ix.get("CastSpellByName")["flags"][0])
        self.assertIn("插件可以传机密值", self.ix.get("C_Spell.GetSpellInfo")["flags"])
        self.assertIn("可能返回机密值：SecretWhenCooldownsRestricted", self.ix.get("C_Spell.GetSpellCooldown")["flags"])

    def test_get(self):
        e = self.ix.get("c_spell.getspellinfo")
        self.assertEqual((e["kind"], e["args"][0]["n"], e["returns"][0]["nil"]), ("function", "spellIdentifier", True))
        self.assertEqual(len(self.ix.get("GetSpellInfo")["candidates"]), 2)        # a short name in two namespaces
        self.assertEqual(self.ix.get("SpellInfo")["fields"][0]["n"], "name")
        self.assertIsNone(self.ix.get("NoSuchThing"))

    def test_manual(self):
        self.assertEqual([t["id"] for t in self.ix.manual()], ["taint", "secret"])
        self.assertEqual(self.ix.manual("secret")["title"], "机密值（secret values）")
        self.assertEqual(self.ix.manual("机密")["id"], "secret")
        self.assertEqual(self.ix.manual("受污染")["id"], "taint")                     # a word in the text
        self.assertIsNone(self.ix.manual("nothing"))
        about = self.ix.about()
        self.assertEqual((about["version"], about["counts"]["function"]), ("2026.10.06.1", 6))
        self.assertEqual(about["calls"], {"ok": 3, "limited": 2, "protected": 3})      # functions and events
        self.assertEqual(about["changes"], {"frm": "1.60.1.70094", "to": "1.60.1.70245", "added": 1, "changed": 1, "removed": 0})
        # English topics where the pack has them, the Chinese text otherwise
        self.assertEqual(self.ix.manual("taint", lang="en"), {"id": "taint", "title": "Security and taint", "md": "Addon code is tainted by default."})
        self.assertEqual(self.ix.manual("secret", lang="en")["md"], "战斗中部分 API 返回机密值。")
        self.assertEqual([t["title"] for t in self.ix.manual(lang="en")], ["Security and taint", "机密值（secret values）"])
        self.assertEqual(self.ix.manual("security and taint")["id"], "taint")

    def test_callability(self):
        """what an addon may do: protected = secure code only (a protected function or one of the protected globals, a
        restricted event), limited = callable with a catch (secret values in restricted states), ok"""
        call = lambda name: (self.ix.get(name)["call"], self.ix.get(name)["why"])
        self.assertEqual(call("CastSpellByName"), ("protected", ["IsProtectedFunction"]))
        self.assertEqual(call("TargetUnit"), ("protected", ["ProtectedGlobal"]))
        self.assertEqual(call("C_Spell.GetSpellCooldown"), ("limited", ["SecretWhenCooldownsRestricted"]))
        self.assertEqual(call("UnitHealth"), ("limited", ["SecretReturns"]))
        self.assertEqual(call("C_Spell.GetSpellInfo"), ("ok", []))               # SecretArguments: no catch for a plain call
        self.assertEqual(call("COMBAT_LOG_EVENT"), ("protected", ["HasRestrictions"]))
        self.assertEqual(call("SPELL_UPDATE_COOLDOWN"), ("ok", []))
        self.assertEqual(call("SpellInfo"), ("ok", []))
        f = self.ix.find("spell", kind="function")
        self.assertEqual((f["counts"], f["total"]), ({"ok": 2, "limited": 1, "protected": 1}, 4))
        usable = self.ix.find("spell", kind="function", call="usable")["results"]
        self.assertEqual({x["name"] for x in usable}, {"C_Spell.GetSpellInfo", "C_Spell.GetSpellCooldown", "C_Other.GetSpellInfo"})
        self.assertEqual([x["name"] for x in self.ix.search("spell", call="protected")], ["CastSpellByName"])

    def test_entry_in_place(self):
        """an entry carries the tables its types name, the rest of its namespace, its Usage strings and what the newest build changed"""
        e = self.ix.get("C_Spell.GetSpellInfo")
        self.assertEqual(e["types"], {"SpellInfo": {"type": "Structure", "fields": [{"n": "name", "t": "cstring", "v": None, "nil": False}]}})
        self.assertEqual(e["siblings"], ["GetSpellCooldown"])
        self.assertEqual(e["usage"], ["Usage: local spellInfo = C_Spell.GetSpellInfo(spellIdentifier)"])
        self.assertEqual(self.ix.get("TargetUnit")["usage"], ["Usage: TargetUnit([name, exactMatch])"])   # not SpellTargetUnit's
        self.assertNotIn("since", e)
        self.assertEqual(self.ix.get("C_Spell.GetSpellCooldown")["since"], {"build": "1.60.1.70245", "what": "added",
                         "note": "(spellIdentifier: SpellIdentifier) → info: SpellCooldownInfo"})
        self.assertEqual(self.ix.search("UnitHealth")[0]["since"]["what"], "changed")
        self.assertEqual(self.ix.get("SPELL_UPDATE_COOLDOWN")["siblings"], ["COMBAT_LOG_EVENT"])
        unit = self.ix.get("UnitHealth")                                          # a global function: the rest of its group
        self.assertEqual((unit["siblings"], unit["scope"]), (["CastSpellByName", "TargetUnit"],
                         {"key": "Unit", "title": "Unit", "group": "global", "prefix": ""}))
        self.assertEqual(e["scope"]["prefix"], "C_Spell.")


# an object's methods (a ScriptObject system: SimpleFrameAPI) and a file of types only, beside the made-up pack
OBJECTS = dict(PACK, namespaces=PACK["namespaces"] + [
    {"ns": "", "name": "SimpleFrameAPI", "functions": [
        {"name": "Hide", "args": [], "rets": [], "doc": "", "raw": {"IsProtectedFunction": True}},
        {"name": "GetWidth", "args": [], "rets": [arg("width", "number")], "doc": "", "raw": {}}], "events": [], "tables": []},
    {"ns": "", "name": "SimpleEditBoxAPI", "functions": [
        {"name": "ClearFocus", "args": [], "rets": [], "doc": "", "raw": {}}], "events": [], "tables": []},
    {"ns": "", "name": "", "functions": [], "events": [],
     "tables": [{"name": "SomeConstants", "type": "Constants", "fields": []}]}],
    protected_globals=PACK["protected_globals"] + ["ClearFocus"])


class Browse(unittest.TestCase):
    """the API 手册 page's browser: namespaces, function groups and objects; an object's methods; a listing without a query"""

    def setUp(self):
        self.ix = apidocs.ApiIndex(OBJECTS)

    def test_an_objects_methods(self):
        hide = self.ix.get("Frame:Hide")
        self.assertEqual((hide["name"], hide["obj"], hide["sig"]), ("Frame:Hide", "Frame", "Frame:Hide()"))
        self.assertEqual((hide["call"], hide["why"]), ("limited", ["ProtectedMethod"]))   # fine on the addon's own frames
        self.assertIn("受保护的方法", hide["flags"][0])
        self.assertEqual(self.ix.get("EditBox:ClearFocus")["call"], "ok")       # the protected global ClearFocus is another
        self.assertEqual(self.ix.get("Hide")["name"], "Frame:Hide")              # by its short name
        self.assertEqual(self.ix.get("Frame:GetWidth")["scope"], {"key": "Frame", "title": "Frame", "group": "object", "prefix": "Frame:"})
        self.assertEqual(apidocs.object_of("FrameAPICooldown"), "Cooldown")
        self.assertEqual(apidocs.object_of("DurationTextBindingObjectAPI"), "DurationTextBindingObject")
        self.assertIsNone(apidocs.object_of("Unit"))

    def test_the_rows(self):
        rows = self.ix.systems()
        self.assertEqual([(r["group"], r["key"]) for r in rows], [("namespace", "C_Other"), ("namespace", "C_Spell"),
                         ("global", "Unit"), ("object", "EditBox"), ("object", "Frame")])   # no row for a file of types only
        spell = next(r for r in rows if r["key"] == "C_Spell")
        self.assertEqual((spell["counts"], spell["calls"]), ({"function": 2, "event": 2, "table": 1},
                                                             {"ok": 2, "limited": 1, "protected": 1}))
        one = self.ix.system("c_spell")
        self.assertEqual([f["name"] for f in one["functions"]], ["C_Spell.GetSpellCooldown", "C_Spell.GetSpellInfo"])
        self.assertEqual([x["name"] for x in one["events"]], ["COMBAT_LOG_EVENT", "SPELL_UPDATE_COOLDOWN"])
        self.assertEqual([x["name"] for x in one["tables"]], ["SpellInfo"])
        self.assertIsNone(self.ix.system("C_Nothing"))

    def test_a_listing_without_a_query(self):
        protected = self.ix.find("", call="protected")
        self.assertEqual([r["name"] for r in protected["results"]], ["CastSpellByName", "TargetUnit", "COMBAT_LOG_EVENT"])
        self.assertEqual(protected["more"], 0)
        page = self.ix.find("", kind="function", limit=3)
        self.assertEqual((len(page["results"]), page["more"], page["total"]), (3, 6, 9))
        rest = self.ix.find("", kind="function", limit=3, offset=6)
        self.assertEqual((len(rest["results"]), rest["more"]), (3, 0))
        self.assertEqual(self.ix.find(""), {"results": [], "counts": None, "total": 0, "more": 0})
        self.assertEqual([r["name"] for r in self.ix.find("", kind="table")["results"]], ["SomeConstants", "SpellInfo"])


# an enum a namespace's file documents and a file of constants, beside the made-up pack
RULES = dict(PACK, namespaces=PACK["namespaces"] + [
    {"ns": "C_GameRules", "name": "GameRules", "functions": [], "events": [],
     "tables": [{"name": "ForeverExperiencePreset", "type": "Enumeration",
                 "fields": [{"n": "Classic", "t": "ForeverExperiencePreset", "v": 0, "nil": False},
                            {"n": "Modern", "t": "ForeverExperiencePreset", "v": 1, "nil": False}]}]},
    {"ns": "", "name": "LegacyConstants", "functions": [], "events": [],
     "tables": [{"name": "LegacyConsts", "type": "Constants", "fields": []}]}])


class GameNames(unittest.TestCase):
    def test_an_enum_and_constants_by_the_names_the_game_gives_them(self):
        """Enum.X and Constants.X, wherever the documentation has them"""
        ix = apidocs.ApiIndex(RULES)
        e = ix.get("Enum.ForeverExperiencePreset")
        self.assertEqual((e["kind"], e["name"], e["type"]), ("table", "ForeverExperiencePreset", "Enumeration"))
        self.assertEqual([(f["n"], f["v"]) for f in e["fields"]], [("Classic", 0), ("Modern", 1)])
        self.assertEqual(ix.get("enum.foreverexperiencepreset")["name"], "ForeverExperiencePreset")
        self.assertEqual(ix.get("ForeverExperiencePreset")["name"], "ForeverExperiencePreset")    # as before
        self.assertEqual(ix.get("Constants.LegacyConsts")["type"], "Constants")
        self.assertIsNone(ix.get("Enum.LegacyConsts"))                       # constants, not an enum
        self.assertIsNone(ix.get("Constants.ForeverExperiencePreset"))
        self.assertIsNone(ix.get("Enum.SpellInfo"))                          # a structure is neither


# the made-up pack with a scan of the running game (scripts/build_api_pack.py runtime): C_Spell has a function the
# documentation leaves out (its Usage string names it without the namespace), C_Garrison is not documented at all, C_Other
# and the global TargetUnit are not in the game, GameRule and LegacyConsts are tables the documentation does not have
SCANNED = dict(PACK, usage=PACK["usage"] + ["Usage: GetSpellBookInfo(index)"], namespaces=PACK["namespaces"] + [
    {"ns": "C_GameRules", "name": "GameRules", "events": [], "tables": [],
     "functions": [{"name": "IsGameRuleActive", "args": [arg("gameRule", "GameRule")], "rets": [arg("isActive", "bool")],
                    "doc": "", "raw": {}}]}],
    runtime=dict(client="1.60.1.70338", scanned="2026-10-11", globals=["CastSpellByName", "UnitHealth"],
                 namespaces={"C_Spell": ["GetSpellBookInfo", "GetSpellCooldown", "GetSpellInfo"],
                             "C_Garrison": ["GetGarrisonInfo"], "C_GameRules": ["IsGameRuleActive"]},
                 enums=["GameRule"], constants=["LegacyConsts"],
                 fields={"Enum.GameRule": {"MacrosDisabled": 2, "UserAddonsDisabled": 1},
                         "Constants.LegacyConsts": {"LEGACY_TREE_PROFESSIONS_ID": 1187}}))


class Scanned(unittest.TestCase):
    """what the game scan adds: the functions and tables the documentation leaves out (undocumented), the documented ones
    the game lacks marked (absent)"""

    def setUp(self):
        self.ix = apidocs.ApiIndex(SCANNED)

    def test_what_the_documentation_leaves_out(self):
        e = self.ix.get("C_Spell.GetSpellBookInfo")
        self.assertEqual((e["sig"], e["call"], e["why"], e["undocumented"]),
                         ("C_Spell.GetSpellBookInfo()", "limited", ["Undocumented"], True))
        self.assertEqual(e["flags"][0], "文档里没有：游戏里有（1.60.1.70338 实测），没有参数与返回值的说明")
        self.assertEqual(e["usage"], ["Usage: GetSpellBookInfo(index)"])          # named without its namespace
        self.assertIn("GetSpellBookInfo", self.ix.get("C_Spell.GetSpellInfo")["siblings"])
        self.assertEqual(self.ix.system("C_Garrison")["functions"][0]["name"], "C_Garrison.GetGarrisonInfo")
        rule = self.ix.get("Enum.GameRule")
        self.assertEqual((rule["type"], [(f["n"], f["v"]) for f in rule["fields"]]),
                         ("Enumeration", [("UserAddonsDisabled", 1), ("MacrosDisabled", 2)]))   # by value
        self.assertEqual(rule["flags"], ["文档里没有：游戏里有（1.60.1.70338 实测），取值是游戏里读到的"])
        self.assertEqual(self.ix.get("C_GameRules.IsGameRuleActive")["types"]["GameRule"]["fields"][0]["n"],
                         "UserAddonsDisabled")                                   # an argument's type, from the scan
        self.assertEqual(self.ix.get("Constants.LegacyConsts")["fields"][0]["v"], 1187)
        self.assertEqual([r["name"] for r in self.ix.search("GameRule", kind="table")], ["GameRule"])

    def test_what_the_game_lacks(self):
        other = self.ix.get("C_Other.GetSpellInfo")
        self.assertEqual((other["absent"], other["call"], other["why"]), (True, "limited", ["NotInClient"]))
        self.assertEqual(other["flags"][0], "这个客户端里没有（1.60.1.70338 实测）")
        target = self.ix.get("TargetUnit")                                      # protected stays protected
        self.assertEqual((target["absent"], target["call"], target["why"]), (True, "protected", ["NotInClient", "ProtectedGlobal"]))
        for name in ("UnitHealth", "C_Spell.GetSpellInfo", "SpellInfo", "SPELL_UPDATE_COOLDOWN"):
            self.assertNotIn("absent", self.ix.get(name), name)
        self.assertEqual(self.ix.about()["scan"], {"client": "1.60.1.70338", "scanned": "2026-10-11", "carried": None, "undocumented": 4,
                                                   "absent": 2})
        self.assertIsNone(apidocs.ApiIndex(PACK).about()["scan"])               # a pack without a scan


def build_script():
    spec = importlib.util.spec_from_file_location("build_api_pack", ROOT / "scripts" / "build_api_pack.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Build(unittest.TestCase):
    """scripts/build_api_pack.py: the game scan beside the documentation, as the pack's runtime"""

    def test_the_scan_and_the_documentation(self):
        """the scan's names; a documented one an addon had set in the scanned game counts (the client's all the same),
        an addon's own does not; the fields of the tables the documentation does not have"""
        docs = {"namespaces": [
            {"ns": "C_Spell", "name": "Spell", "functions": [{"name": "GetSpellInfo"}, {"name": "Hooked"}], "events": [],
             "tables": [{"name": "Doc", "type": "Enumeration", "fields": []}]},
            {"ns": "", "name": "Unit", "functions": [{"name": "UnitHealth"}, {"name": "Replaced"}], "events": [], "tables": []},
            {"ns": "", "name": "SimpleFrameAPI", "functions": [{"name": "Hide"}], "events": [], "tables": []},
            {"ns": "", "name": "", "functions": [], "events": [],
             "tables": [{"name": "LegacyConsts", "type": "Constants", "fields": []}]}]}
        scan = dict(client="1.60.1.70338", scanned="2026-10-11", namespaces={"C_Spell": ["Extra", "GetSpellInfo"]},
                    globals=["UnitHealth"], enums=["Doc", "New"], constants=[],
                    fields={"Enum.Doc": {"A": 1}, "Enum.New": {"B": 2}},
                    replaced={"C_Spell.Hooked": "X", "Replaced": "X", "Hide": "X", "Constants.LegacyConsts": "X",
                              "AddonOwn": "X"})
        got = build_script().runtime(scan, docs)
        self.assertEqual(got, dict(client="1.60.1.70338", scanned="2026-10-11", globals=["Replaced", "UnitHealth"],
                                   namespaces={"C_Spell": ["Extra", "GetSpellInfo", "Hooked"]}, enums=["Doc", "New"],
                                   constants=["LegacyConsts"], fields={"Enum.New": {"B": 2}}))

    def test_a_scan_of_the_documentations_build_only(self):
        script = build_script()
        with tempfile.TemporaryDirectory() as tmp:
            codex = Path(tmp)
            (codex / "api").mkdir()
            with self.assertRaises(SystemExit) as cm:
                script.game_scan(codex, "1.60.1.70338")
            self.assertIn("no game scan", str(cm.exception))
            (codex / "api" / script.SCAN).write_text(json.dumps({"client": "1.60.1.70245"}), encoding="utf-8")
            with self.assertRaises(SystemExit) as cm:
                script.game_scan(codex, "1.60.1.70338")
            self.assertIn("a scan of 1.60.1.70245, the documentation is of 1.60.1.70338", str(cm.exception))
            self.assertEqual(script.game_scan(codex, "1.60.1.70245"), {"client": "1.60.1.70245"})


class Carry(unittest.TestCase):
    """--carry-scan: the scan of an earlier build, with what the documentation says changed since"""

    def test_the_documentations_changes_applied(self):
        docs = {"namespaces": [{"ns": "", "name": "", "functions": [], "events": [],
                                "tables": [{"name": "Rule", "type": "Enumeration"}, {"name": "Consts", "type": "Constants"}]}]}
        scan = dict(client="1.60.1.70450", scanned="2026-10-25", namespaces={"C_GameRules": ["Old", "Kept"], "C_Gone": ["X"]},
                    globals=["Pet", "Kept"], enums=["Rule", "Gone"], constants=[], fields={})
        changes = [dict(added=[dict(what="新增函数", name="C_GameRules.New"), dict(what="新增函数", name="GetPetCrit"),
                               dict(what="新增事件", name="EVENT"), dict(what="新增表", name="Consts")],
                        removed=[dict(what="删除函数", name="C_GameRules.Old"), dict(what="删除函数", name="C_Gone.X"),
                                 dict(what="删除枚举", name="Gone")], **{"from": "1.60.1.70450", "to": "1.60.1.70630"}),
                   dict(added=[], removed=[], **{"from": "1.60.1.70374", "to": "1.60.1.70450"})]
        script = build_script()
        got = script.carry(scan, changes, docs, "1.60.1.70630")
        self.assertEqual(got["namespaces"], {"C_GameRules": ["Kept", "New"]})
        self.assertEqual(got["globals"], ["Pet", "Kept", "GetPetCrit"])
        self.assertEqual((got["enums"], got["constants"]), (["Rule"], ["Consts"]))
        self.assertEqual((got["client"], got["carried"]), ("1.60.1.70450", "1.60.1.70630"))   # scanned there, carried here
        self.assertEqual(scan["namespaces"]["C_GameRules"], ["Old", "Kept"])                # the scan itself untouched
        with self.assertRaises(SystemExit):
            script.carry(scan, changes, docs, "1.60.1.70700")                                # no records lead there


class Carried(unittest.TestCase):
    """the pack in src/wuxianworkshop/data (scripts/build_api_pack.py)"""

    def test_the_program_carries_the_manual(self):
        pack = content.read_pack(content.BUNDLED / "api.json.gz")
        self.assertIsNotNone(pack, "src/wuxianworkshop/data/api.json.gz: run scripts/build_api_pack.py")
        ix = apidocs.ApiIndex(pack)
        self.assertGreater(ix.about()["counts"]["function"], 5000)
        self.assertEqual(ix.search("UnitHealth")[0]["name"], "UnitHealth")
        for topic in ("runtime", "taint", "secret", "toc", "protected", "forever-api"):
            self.assertTrue(ix.manual(topic)["md"], topic)
        self.assertNotIn("__", ix.manual("protected")["md"])                       # every placeholder filled in
        preset = ix.get("Enum.ForeverExperiencePreset")                            # in C_GameRules' file (forever-api)
        self.assertEqual([f["n"] for f in preset["fields"]], ["Classic", "Enhanced"])  # 1.60.1.70630: Modern -> Enhanced
        scan = ix.about()["scan"]                                                   # a game scan of the same build, or
        self.assertEqual(scan["carried"] or scan["client"], ix.about()["client"])   # one carried to it (--carry-scan)
        self.assertTrue(ix.get("C_Garrison.GetGarrisonInfo")["undocumented"])     # the documentation leaves it out
        self.assertEqual(ix.get("Constants.LegacyConsts")["fields"][2]["n"], "LEGACY_TREE_PROFESSIONS_ID")


if __name__ == "__main__":
    unittest.main()
