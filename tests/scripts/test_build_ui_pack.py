"""scripts/build_ui_pack.py: the index of Blizzard's own UI over small made-up addon folders (invented Lua, XML and .toc,
written here): which .toc and which files this client (camelot, of the mainline family) loads, XML includes, $parent
and parentKey, Lua definitions, events, and that no code, comment or text lands in the pack."""
import gzip
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop import content

ROOT = Path(__file__).resolve().parents[2]


def script():
    spec = importlib.util.spec_from_file_location("build_ui_pack", ROOT / "scripts" / "build_ui_pack.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b = script()

# ---- the made-up client: folders and files lowercase on disk, as extracted

FILES = {
    # an addon loaded at login: [Family] / [Game] paths, lines for other game types, a missing file, locale tags
    "blizzard_alphaui/blizzard_alphaui.toc": """## Title: Blizzard_AlphaUI
## AllowLoadGameType: standard, camelot
## Dep: Blizzard_BetaJournal [AllowLoadGameType mainline]
## SavedVariables: Blizzard_AlphaUI_Saved
Blizzard_AlphaUI.lua
Blizzard_AlphaUI.xml
[Family]\\AlphaFamily.lua [AllowLoadGameType mainline] [ExcludeLoadGameType camelot]
[Game]\\AlphaFamily.lua [AllowLoadGameType camelot]
Classic\\AlphaOld.lua [AllowLoadGameType vanilla, tbc]
AlphaStandard.lua [AllowLoadGameType standard]
AlphaClassicGroup.lua [AllowLoadGameType classic]
AlphaFuture.lua [AllowLoadGameType futuregame]
AlphaGone.lua
[AllowLoadTextLocale: zhCN] AlphaLocale.lua
AlphaRussian.lua [AllowLoadTextLocale ruRU]
AlphaGlue.lua [AllowLoad Glue]
""",
    "blizzard_alphaui/blizzard_alphaui.lua": r'''-- a comment naming function CommentedOut() and AlphaHidden = {}
--[==[ a long comment
function LongCommented(a)
end
]==]
local note = [[ function InLongString() end ]]
local text = "function InQuotes() end -- not a comment"
local function LocalHelper(x) return x + 12345 end
local LocalTable = {}
function LocalTable:Hidden() end

function AlphaUI_Toggle(frame, ...)
    print("AlphaSecretTextOne", 67890)
end

AlphaUI_Make = function(kind, count) return kind end

AlphaPanelMixin = {}
function AlphaPanelMixin:OnLoad()
    self:RegisterEvent("ALPHA_EVENT_ONE")
    self.notAMember = 1
end
function AlphaPanelMixin.Reset(panel) end
AlphaPanelMixin.Refresh = function(panel, force) end
AlphaPanelMixin.Limit = 5

AlphaFancyMixin = CreateFromMixins(AlphaPanelMixin, CallbackRegistryMixin)

AlphaRegistry = {
    Count = 0,
    Add = function(item) end,
    Nested = { Deep = 1 },
}
function AlphaRegistry.Remove(item) end

local ALPHA_EVENTS = { "ALPHA_LIST_ONE", "ALPHA_LIST_TWO" }
function AlphaPanelMixin:OnShow()
    FrameUtil.RegisterFrameForEvents(self, ALPHA_EVENTS)
    FrameUtil.RegisterFrameForEvents(self, { "ALPHA_INLINE" })
end

local made = CreateFrame("Frame", "AlphaMadeFrame", UIParent, "AlphaButtonTemplate")
for i = 1, 3 do
    CreateFrame("Button", "AlphaNumbered" .. i)
end
AlphaDriver = CreateFrame("Frame")
''',
    "blizzard_alphaui/blizzard_alphaui.xml": """<Ui xmlns="http://www.blizzard.com/wow/ui/">
    <Script file="AlphaScript.lua"/>
    <Include file="Shared\\AlphaParts.xml"/>
    <Button name="AlphaButtonTemplate" mixin="AlphaPanelMixin" inherits="UIPanelButtonTemplate, AlphaBaseTemplate" virtual="true">
        <Layers>
            <Layer level="ARTWORK">
                <Texture parentKey="Icon"/>
                <FontString name="$parentLabel" parentKey="Label" text="ALPHA_SECRET_TEXT_TWO"/>
            </Layer>
        </Layers>
    </Button>
    <Frame name="AlphaFrame" parent="UIParent" mixin="AlphaPanelMixin">
        <Frames>
            <Frame name="$parentInset" parentKey="encounter">
                <Frames>
                    <Frame parentKey="info">
                        <Frames>
                            <Button parentKey="bossTab" inherits="AlphaButtonTemplate"/>
                            <Button parentArray="Tabs"/>
                        </Frames>
                    </Frame>
                </Frames>
            </Frame>
            <Frame>
                <Frames>
                    <Button name="$parentDeep" parentKey="Deep"/>
                </Frames>
            </Frame>
        </Frames>
        <Scripts>
            <OnLoad>
                self:RegisterEvent("ALPHA_XML_EVENT")
                print("AlphaSecretTextThree")
            </OnLoad>
        </Scripts>
    </Frame>
</Ui>
""",
    "blizzard_alphaui/alphascript.lua": "function AlphaScript_Run() end\n",
    "blizzard_alphaui/shared/alphaparts.xml": """<Ui>
    <Frame name="AlphaPartsFrame"/>
    <Include file="Interface\\AddOns\\Blizzard_AlphaUI\\AlphaInterfacePath.xml"/>
</Ui>
""",
    "blizzard_alphaui/alphainterfacepath.xml": "<Ui><Frame name=\"AlphaByInterfacePath\"/></Ui>\n",
    "blizzard_alphaui/mainline/alphafamily.lua": "function AlphaFamilyOnly() end\n",
    "blizzard_alphaui/camelot/alphafamily.lua": "function AlphaGameOnly() end\n",
    "blizzard_alphaui/classic/alphaold.lua": "function AlphaOld() end\n",
    "blizzard_alphaui/alphastandard.lua": "function AlphaStandardOnly() end\n",
    "blizzard_alphaui/alphaclassicgroup.lua": "function AlphaClassicGroup() end\n",
    "blizzard_alphaui/alphafuture.lua": "function AlphaFuture() end\n",
    "blizzard_alphaui/alphalocale.lua": "function AlphaLocale() end\n",
    "blizzard_alphaui/alpharussian.lua": "function AlphaRussian() end\n",
    "blizzard_alphaui/alphaglue.lua": "function AlphaGlue() end\n",

    # on demand, with a bootstrap file loaded at login all the same
    "blizzard_betajournal/blizzard_betajournal.toc": """## Title: Blizzard_BetaJournal
## LoadOnDemand: 1
Blizzard_BetaJournal_Bootstrap.lua [Bootstrap]
Blizzard_BetaJournal.lua
""",
    "blizzard_betajournal/blizzard_betajournal_bootstrap.lua": "function BetaJournal_LoadUI() end\n",
    "blizzard_betajournal/blizzard_betajournal.lua": "function BetaJournal_Show() end\n",

    # which .toc: the folder's _mainline one over the plain and the other flavours' ones
    "blizzard_gammamap/blizzard_gammamap.toc": "## Title: Blizzard_GammaMap\nGammaPlain.lua\n",
    "blizzard_gammamap/blizzard_gammamap_mainline.toc": "## Title: Blizzard_GammaMap\nGammaMainline.lua\n",
    "blizzard_gammamap/blizzard_gammamap_vanilla.toc": "## Title: Blizzard_GammaMap\nGammaVanilla.lua\n",
    "blizzard_gammamap/gammaplain.lua": "function GammaPlain() end\n",
    "blizzard_gammamap/gammamainline.lua": "function GammaMainline() end\n",
    "blizzard_gammamap/gammavanilla.lua": "function GammaVanilla() end\n",

    # addons this client does not load
    "blizzard_deltahousing/blizzard_deltahousing.toc": "## Title: Blizzard Delta Housing\n## AllowLoadGameType: standard\nDelta.lua\n",
    "blizzard_deltahousing/delta.lua": "function DeltaHousing() end\n",
    "blizzard_epsilonglue/blizzard_epsilonglue.toc": "## AllowLoad: Glue\nEpsilon.lua\n",
    "blizzard_epsilonglue/epsilon.lua": "function EpsilonGlue() end\n",
    "blizzard_zetagarrison/blizzard_zetagarrison.toc": ("## Title: Blizzard_ZetaGarrison\n## AllowLoadGameType: mainline\n"
                                                        "## ExcludeLoadGameType: camelot\nZeta.lua\n"),
    "blizzard_zetagarrison/zeta.lua": "function ZetaGarrison() end\n",
    "blizzard_etaother/blizzard_etaothername.toc": "Eta.lua\n",
    "blizzard_etaother/eta.lua": "function EtaOther() end\n",

    # the secure environment: nothing of it is _G's, until the file swaps to the global environment
    "blizzard_thetastore/blizzard_thetastore.toc": """## Title: Blizzard_ThetaStore
## UseSecureEnvironment: 1
ThetaSecure.lua
ThetaInbound.lua
ThetaGlobal.lua [LoadIntoEnvironment global]
ThetaSecure.xml
""",
    "blizzard_thetastore/thetasecure.lua": "function ThetaSecret() end\nThetaTable = {}\n",
    "blizzard_thetastore/thetainbound.lua": "local x = 1\nSwapToGlobalEnvironment();\nfunction ThetaInbound_Show() end\n",
    "blizzard_thetastore/thetaglobal.lua": "function ThetaGlobal() end\n",
    "blizzard_thetastore/thetasecure.xml": "<Ui><Frame name=\"ThetaSecureFrame\"/></Ui>\n",
}

# text of the made-up code that must not reach the pack (code, comments, strings that are not names)
NOT_IN_PACK = ["AlphaSecretTextOne", "ALPHA_SECRET_TEXT_TWO", "AlphaSecretTextThree", "12345", "67890", "print",
               "return", "a comment", "a long comment", "not a comment", "CommentedOut", "LongCommented", "InLongString",
               "InQuotes", "LocalHelper", "Hidden", "notAMember", "ARTWORK", "http://", "Deep\":", "AlphaHidden"]


def make_client(root):
    for rel, text in FILES.items():
        path = root / "ui" / "interface" / "addons" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root / "ui"


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.src = make_client(Path(cls.tmp.name))
        cls.builder = b.Builder(cls.src).build()
        cls.pack = cls.builder.pack("2026.11.24.1", "1.60.1.71037")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def entries(self, name, kind=None):
        return [e for e in self.pack["entries"] if e["n"] == name and (kind is None or e["k"] == kind)]

    def one(self, name, kind=None):
        found = self.entries(name, kind)
        self.assertEqual(len(found), 1, (name, kind, found))
        return found[0]

    def file(self, entry):
        return self.pack["files"][entry["f"]]

    def test_the_addons_and_their_load(self):
        self.assertEqual(self.pack["addons"], {
            "Blizzard_AlphaUI": {"load": "always", "files": 8}, "Blizzard_BetaJournal": {"load": "on_demand", "files": 2},
            "Blizzard_GammaMap": {"load": "always", "files": 1}, "Blizzard_ThetaStore": {"load": "always", "files": 4}})
        self.assertEqual(self.pack["left_out"], {
            "Blizzard_DeltaHousing": "game type standard", "blizzard_epsilonglue": "login screen only",
            "blizzard_etaother": "no .toc for this client", "Blizzard_ZetaGarrison": "excluded for camelot"})

    def test_which_toc_and_which_lines(self):
        names = {e["n"] for e in self.pack["entries"] if e["k"] == "function"}
        for taken in ("AlphaGameOnly", "AlphaFuture", "AlphaLocale", "GammaMainline", "BetaJournal_LoadUI",
                      "BetaJournal_Show", "AlphaScript_Run"):
            self.assertIn(taken, names)
        for left in ("AlphaFamilyOnly", "AlphaOld", "AlphaStandardOnly", "AlphaClassicGroup", "AlphaRussian",
                     "AlphaGlue", "GammaPlain", "GammaVanilla", "DeltaHousing", "EpsilonGlue", "ZetaGarrison",
                     "EtaOther"):
            self.assertNotIn(left, names)
        self.assertEqual(self.file(self.one("AlphaGameOnly")), "Interface/AddOns/Blizzard_AlphaUI/Camelot/AlphaFamily.lua")
        self.assertEqual(self.builder.flavor.uncertain, {"futuregame": 1})          # counted as ours, and reported
        self.assertEqual(self.builder.missing, [("Interface/AddOns/Blizzard_AlphaUI/AlphaGone.lua",
                                                 "Interface/AddOns/Blizzard_AlphaUI/blizzard_alphaui.toc")])
        boot = [self.pack["files"][i] for i in self.pack["bootstrap"]]
        self.assertEqual(boot, ["Interface/AddOns/Blizzard_BetaJournal/Blizzard_BetaJournal_Bootstrap.lua"])

    def test_script_and_include(self):
        files = self.pack["files"]
        for path in ("Interface/AddOns/Blizzard_AlphaUI/AlphaScript.lua", "Interface/AddOns/Blizzard_AlphaUI/Shared/AlphaParts.xml",
                     "Interface/AddOns/Blizzard_AlphaUI/AlphaInterfacePath.xml"):
            self.assertIn(path, files)
        self.assertEqual(self.file(self.one("AlphaPartsFrame")), "Interface/AddOns/Blizzard_AlphaUI/Shared/AlphaParts.xml")
        self.one("AlphaByInterfacePath", "frame")

    def test_xml_frames_and_templates(self):
        template = self.one("AlphaButtonTemplate")
        self.assertEqual(template, dict(k="template", n="AlphaButtonTemplate", f=template["f"], l=4, t="Button",
                                        inh=["UIPanelButtonTemplate", "AlphaBaseTemplate"], mix=["AlphaPanelMixin"],
                                        keys={"Icon": "Texture", "Label": "FontString"}, parent=None))
        self.assertEqual(self.entries("$parentLabel") + self.entries("AlphaButtonTemplateLabel"), [])  # not a global
        frame = self.one("AlphaFrame", "frame")
        self.assertEqual(frame["parent"], "UIParent")
        self.assertEqual(frame["mix"], ["AlphaPanelMixin"])
        self.assertEqual(frame["keys"], {"encounter": "Frame", "encounter.info": "Frame", "encounter.info.bossTab": "Button",
                                         "encounter.info.Tabs[]": "Button"})
        inset = self.one("AlphaFrameInset", "frame")                     # $parent resolved
        self.assertEqual((inset["parent"], inset["t"]), ("AlphaFrame", "Frame"))
        deep = self.one("AlphaFrameDeep", "frame")                       # through an unnamed frame: the named one above
        self.assertEqual(deep["parent"], "AlphaFrame")
        self.assertNotIn("Deep", frame["keys"])                           # an unnamed child without a parentKey breaks it

    def test_lua_definitions(self):
        f = self.one("AlphaUI_Toggle", "function")
        self.assertEqual((f["p"], f["l"]), (["frame", "..."], 12))
        self.assertEqual(self.one("AlphaUI_Make", "function")["p"], ["kind", "count"])
        mixin = self.one("AlphaPanelMixin", "mixin")
        self.assertEqual((mixin["m"], mixin["mixins"]), (["Limit", "OnLoad()", "OnShow()", "Refresh()", "Reset()"], []))
        self.assertEqual(self.one("AlphaPanelMixin:OnLoad", "method"),
                         dict(k="method", n="AlphaPanelMixin:OnLoad", f=mixin["f"], l=19, p=[], o="AlphaPanelMixin"))
        self.assertEqual(self.one("AlphaPanelMixin.Reset")["p"], ["panel"])
        self.assertEqual(self.one("AlphaPanelMixin.Refresh")["p"], ["panel", "force"])
        self.assertEqual(self.one("AlphaFancyMixin", "mixin")["mixins"], ["AlphaPanelMixin", "CallbackRegistryMixin"])
        registry = self.one("AlphaRegistry", "table")
        self.assertEqual(registry["m"], ["Add()", "Count", "Nested", "Remove()"])
        self.assertEqual(self.entries("LocalTable") + self.entries("LocalTable:Hidden"), [])
        made = self.one("AlphaMadeFrame", "frame")
        self.assertEqual((made["t"], made["inh"], made["parent"]), ("Frame", ["AlphaButtonTemplate"], "UIParent"))
        self.assertEqual(self.entries("AlphaNumbered"), [])                 # a name made at run time
        self.assertEqual(self.one("AlphaDriver", "frame")["t"], "Frame")

    def test_events(self):
        events = {(e["n"], self.file(e).rsplit("/", 1)[1]) for e in self.pack["entries"] if e["k"] == "event"}
        self.assertEqual(events, {("ALPHA_EVENT_ONE", "Blizzard_AlphaUI.lua"), ("ALPHA_LIST_ONE", "Blizzard_AlphaUI.lua"),
                                  ("ALPHA_LIST_TWO", "Blizzard_AlphaUI.lua"), ("ALPHA_INLINE", "Blizzard_AlphaUI.lua"),
                                  ("ALPHA_XML_EVENT", "Blizzard_AlphaUI.xml")})
        self.assertEqual(self.one("ALPHA_XML_EVENT")["l"], 32)

    def test_the_secure_environment(self):
        names = {e["n"] for e in self.pack["entries"]}
        self.assertTrue({"ThetaInbound_Show", "ThetaGlobal"} <= names)
        self.assertFalse({"ThetaSecret", "ThetaTable", "ThetaSecureFrame"} & names)

    def test_no_code_in_the_pack(self):
        raw = b.fit(json.loads(json.dumps(self.pack)))
        text = gzip.decompress(raw).decode("utf-8")
        for words in NOT_IN_PACK:
            self.assertNotIn(words, text)
        self.assertEqual(content.PACK_HEAD.match(text[:80].encode()).groups(), (b"ui", b"2026.11.24.1"))

    def test_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "out").mkdir()
            (tmp / "out" / "manifest.json").write_text(json.dumps({"packs": {"api": {"version": "1"}}}), encoding="utf-8")
            raw = b.fit(json.loads(json.dumps(self.pack)))
            b.publish(raw, "2026.11.24.1", bundled=tmp / "data" / "ui.json.gz", out=tmp / "out")
            self.assertEqual((tmp / "data" / "ui.json.gz").read_bytes(), raw)
            self.assertEqual((tmp / "out" / "ui-2026.11.24.1.json.gz").read_bytes(), raw)
            manifest = json.loads((tmp / "out" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["packs"]["api"], {"version": "1"})
            self.assertEqual(manifest["packs"]["ui"]["file"], "ui-2026.11.24.1.json.gz")
            self.assertEqual(manifest["packs"]["ui"]["size"], len(raw))

    def test_too_big_drops_the_events(self):
        pack = json.loads(json.dumps(self.pack))
        aim = b.SIZE_AIM
        b.SIZE_AIM = 10
        try:
            raw = b.fit(pack)
        finally:
            b.SIZE_AIM = aim
        got = json.loads(gzip.decompress(raw))
        self.assertTrue(got["events_dropped"])
        self.assertFalse([e for e in got["entries"] if e["k"] == "event"])
        self.assertTrue([e for e in got["entries"] if e["k"] == "function"])


class Tokens(unittest.TestCase):
    def test_strings_comments_and_lines(self):
        src = 'a = "x\\"y" -- c\n--[[ multi\nline ]] b = [==[ s ]] still\n]==] c = \'q\'\nd'
        got = [(k, v, line) for k, v, line in b.tokens(src)]
        self.assertEqual(got, [("name", "a", 1), ("op", "=", 1), ("str", 'x\\"y', 1), ("name", "b", 3), ("op", "=", 3),
                               ("str", " s ]] still\n", 3), ("name", "c", 4), ("op", "=", 4), ("str", "q", 4),
                               ("name", "d", 5)])

    def test_a_definition_only_in_the_pack_by_its_name(self):
        lf = b.LuaFile(__import__("collections").defaultdict(set)).parse(
            'local s = "function Fake() end"\n--[[ function AlsoFake() end ]]\nfunction Real(a, b) end\n')
        self.assertEqual([(e["k"], e["n"], e["l"], e["p"]) for e in lf.entries], [("function", "Real", 3, ["a", "b"])])
        self.assertFalse(re.search("Fake", json.dumps(lf.entries)))


if __name__ == "__main__":
    unittest.main()
