"""agent/commands.py: the files load and watch take from an addon folder (toc_files): what the 无限 client (camelot)
loads of it, read as agent/lint.py reads it (the .toc's load conditions, the XML files' <Script> / <Include>), and the
Companion's load / watch of an addon for several game types."""
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop.agent import commands
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.transport import link

FOO = {   # an addon for several game types, laid out the way multi-version addons are
    "Foo.toc": "## Interface: 16001\n"
               "## Title: Foo Retail [AllowLoadGameType standard]\n"
               "## Title: Foo Forever [AllowLoadGameType camelot][ExcludeLoadGameType standard, classic]\n"
               "\n"
               "Libs\\Libs.xml\n"
               "Game\\load_forever.xml [AllowLoadGameType camelot][ExcludeLoadGameType standard, classic]\n"
               "Game\\load_retail.xml [AllowLoadGameType standard]\n"
               "Game\\Retail\\Only.lua [AllowLoadGameType standard, classic]\n"
               "Game\\Shared\\Late.lua [ExcludeLoadGameType classic]\n"
               "Gone.lua\n",
    "Libs/Libs.xml": '<Ui xmlns="http://www.blizzard.com/wow/ui/">\n'
                     '\t<Script file="Stub\\Stub.lua"/>\n'
                     '\t<!--<Include file="Old\\Old.xml"/>-->\n'
                     '\t<Include file="Widgets\\Widgets.xml"/>\n'
                     '</Ui>\n',
    "Libs/Stub/Stub.lua": "-- stub\n",
    "Libs/Old/Old.xml": '<Ui><Script file="Old.lua"/></Ui>\n',
    "Libs/Old/Old.lua": "-- only a commented-out line names it\n",
    "Libs/Widgets/Widgets.xml": '<Ui><Script file="Button.lua"/><!-- <Frame name="FooUnused"/> --></Ui>\n',
    "Libs/Widgets/Button.lua": "-- button\n",
    "Game/load_forever.xml": "<Ui xmlns='http://www.blizzard.com/wow/ui/'>\n"
                             "\t<Script file='Shared\\Init.lua'/>\n"
                             "\t<Script file='..\\Locales\\enUS.lua'/>\n"
                             "\t<Include file ='Shared\\Templates.xml'/> <!-- FooButtonTemplate -->\n"
                             "\t<Include file='Forever\\Fix.lua'/>\n"
                             "\t<Script file='Shared\\Core.lua'/>\n"
                             "</Ui>\n",
    "Game/Shared/Init.lua": "-- init\n",
    "Locales/enUS.lua": "-- enUS\n",
    "Game/Shared/Templates.xml": "<Ui><Button name='FooButtonTemplate' virtual='true'/></Ui>\n",
    "Game/Forever/Fix.lua": "-- fix\n",
    "Game/Shared/Core.lua": "-- core\n",
    "Game/Shared/Late.lua": "-- late\n",
    "Game/load_retail.xml": '<Ui><Script file="Retail\\Retail.lua"/><Frame name="FooRetailFrame"/></Ui>\n',
    "Game/Retail/Retail.lua": "-- retail\n",
    "Game/Retail/Only.lua": "-- retail and classic\n",
}
LOADED = ["Libs/Stub/Stub.lua", "Libs/Widgets/Button.lua", "Game/Shared/Init.lua", "Locales/enUS.lua",
          "Game/Forever/Fix.lua", "Game/Shared/Core.lua", "Game/Shared/Late.lua"]
OTHER = {"Other.toc": "## Interface: 16001\n## AllowLoadGameType: standard, classic\nMain.lua\n", "Main.lua": "-- main\n"}


def make(addons, name, files):
    for rel, text in files.items():
        p = addons / name / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return addons / name


class TocFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addons = Path(self.tmp.name)

    def test_the_files_this_client_loads(self):
        """the .toc's lines for camelot (their load conditions read), the XML files' <Script> and <Include> followed:
        single or double quotes, file ='...', ..\\ paths, an <Include> of a .lua; commented-out lines and missing files
        left out; the XML files that make frames or templates (not in a comment), of those loaded"""
        foo = make(self.addons, "Foo", FOO)
        files, frames = commands.toc_files(foo / "Foo.toc")
        self.assertEqual([p.relative_to(foo).as_posix() for p in files], LOADED)
        self.assertEqual([p.relative_to(foo).as_posix() for p in frames], ["Game/Shared/Templates.xml"])

    def test_an_addon_for_other_game_types(self):
        """## AllowLoadGameType leaves camelot out: the client loads nothing of it"""
        other = make(self.addons, "Other", OTHER)
        self.assertEqual(commands.toc_files(other / "Other.toc"), ([], []))


class LoadAndWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addons = Path(self.tmp.name)
        (self.addons / "WoWBridge").mkdir()
        MB.install(self.addons, pool=16)
        self.c = link.Companion(self.addons, clock=lambda: 100.0, log=lambda text: None)
        self.c.new_process("P1-1")
        self.told = []
        self.c.on_debug = lambda kind, text: self.told.append((kind, text))

    def chunks(self):
        return [d.split(b"\n", 1)[0].decode().split(" ", 3)[3] for k, d in self.c.outbox if k == MB.CODE]

    def test_what_this_client_loads(self):
        """load <addon>: the Lua files camelot loads, in order, OnUnload before the first and OnReload after the last;
        watch <addon>: the same files"""
        foo = make(self.addons, "Foo", FOO)
        self.assertEqual(len(self.c.command("load Foo")), len(LOADED))
        names = [f"@Interface/AddOns/Foo/{rel}" for rel in LOADED]
        self.assertEqual(self.chunks(), [names[0] + " unload"] + names[1:-1] + [names[-1] + " reload"])
        self.assertIn(("RUN", "load Foo: frames and templates in Templates.xml are XML and are not made again; only the "
                              "Lua runs"), self.told)
        self.c.command("watch Foo")
        self.assertEqual(sorted(p.relative_to(foo).as_posix() for p in self.c.watched), sorted(LOADED))
        self.assertIn(("WATCH", f"watching {len(LOADED)} files of Foo: each is loaded again when it is saved"), self.told)

    def test_an_addon_this_client_does_not_load(self):
        """refused with the reason: there is nothing of it to load or watch"""
        make(self.addons, "Other", OTHER)
        self.assertIsNone(self.c.command("load Other"))
        self.assertIsNone(self.c.command("watch Other"))
        why = "the client does not load this addon: its ## AllowLoadGameType leaves out camelot"
        self.assertEqual([t for t in self.told if t[0] == "RUN"], [("RUN", f"load Other: {why}"), ("RUN", f"watch Other: {why}")])
        self.assertEqual((self.chunks(), self.c.watched), ([], {}))


if __name__ == "__main__":
    unittest.main()
