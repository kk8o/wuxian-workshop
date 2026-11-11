"""An extension's summary (local/summary.py): the facts of a folder with a program, of one in the game only and of an
addon found in the game's AddOns, that a package's count is what pack() packs, what compare() says differs, the addons
detected() finds in the game's AddOns, and a summary and its differences as lines of text. Everything lies in a
temporary folder."""
import json
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop.local import gameside as G
from wuxianworkshop.local import manifest as M
from wuxianworkshop.local import pack as K
from wuxianworkshop.local import summary as S

from .test_pack import extension, put


def toc(title, notes=None, deps=None, gid=None, site=None, author=None, version="1.2.0"):
    lines = ["## Interface: 16001", f"## Title: {title}", f"## Version: {version}"]
    lines += [f"## Notes: {notes}"] if notes else []
    lines += [f"## Author: {author}"] if author else []
    lines += [f"## Dependencies: {deps}"] if deps else []
    lines += [f"## X-WuxianKit-Extension: {gid}"] if gid else []
    lines += [f"## X-Website: {site}"] if site else []
    return ("\n".join(lines) + "\n\ncore.lua\n").encode("utf-8")


def game_addon(folder, name, **kw):
    """addons/<name> of an extension's folder: its .toc and core.lua"""
    put(folder, f"addons/{name}/{name}.toc", toc(name, **kw))
    put(folder, f"addons/{name}/core.lua", b"print('x')\n")


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.folder = self.root / "src" / "voice"
        self.addons = self.root / "game" / "Interface" / "AddOns"
        self.addons.mkdir(parents=True)

    def manifest(self, folder=None):
        m, problems = M.check_manifest(folder or self.folder)
        self.assertIsNotNone(m, problems)
        return m


class OfFolder(Case):
    def both(self):
        extension(self.folder, addons=["WuxianVoice", "WuxianVoice_Options"], author="某人",
                  homepage="https://example.com/voice", notes="把密语读出来", env={"VOICE_RATE": "1.2"},
                  **{"pass": ["DEEPSEEK_API_KEY"]}, start="workshop", pack={"exclude": ["*.wav"]},
                  apps=[{"app": "voice_app", "title": "声音", "prompt": "voice"}])
        game_addon(self.folder, "WuxianVoice", notes="声音的游戏部分", author="某人",
                   deps="WuxianKit, EllesmereUI, WuxianVoice_Options, ellesmereui")
        game_addon(self.folder, "WuxianVoice_Options", deps="WuxianVoice")
        put(self.folder, "skills/speak.md", b"# speak\n")
        put(self.folder, "skills/tune.md", b"# tune\n")
        put(self.folder, "bin/tts.exe", b"MZ")
        put(self.folder, "bin/helper.DLL", b"MZ")
        put(self.folder, "sound/a.wav", b"RIFF")                    # left out of a package
        put(self.folder, ".git/config", b"x")
        return self.manifest()

    def test_one_with_a_program(self):
        m = self.both()
        put(self.folder, "addons/WuxianVoice/lib/odd.dll", b"MZ")   # in the game half (pack() refuses it there): the
        (self.addons / "WuxianVoice").mkdir()                       # game never runs it, it is no program to tell
        s = S.of_folder(m, self.addons)
        self.assertEqual((s["from"], s["id"], s["title"], s["version"], s["notes"], s["author"], s["homepage"]),
                         ("folder", "voice", "声音", "0.2.0", "把密语读出来", "某人", "https://example.com/voice"))
        self.assertEqual((s["package"], s["where"], s["risk"], s["addons_dir"]), (None, "both", "local", str(self.addons)))
        self.assertEqual(s["local"], {"run": ["python", "main.py"], "python": None, "start": "workshop",
                                      "env": ["VOICE_RATE"], "pass": ["DEEPSEEK_API_KEY"]})    # names, never values
        self.assertNotIn("1.2", json.dumps(s["local"]))
        self.assertEqual(s["executables"], ["bin/helper.DLL", "bin/tts.exe"])
        one, two = s["game"]
        self.assertEqual(one, dict(folder="WuxianVoice", title="WuxianVoice", notes="声音的游戏部分", version="1.2.0",
                                   author="某人", needs=[dict(addon="EllesmereUI", installed=False)],
                                   path=str(self.addons / "WuxianVoice"), cannot="broken"))   # (the .dll in it)
        self.assertEqual((two["folder"], two["needs"], two["path"], two["cannot"]),
                         ("WuxianVoice_Options", [], None, None))
        self.assertEqual(s["agent"], dict(docs="AGENT.md", skills=["speak", "tune"], apps=["voice_app"], adds=[]))
        (self.addons / "EllesmereUI").mkdir()
        self.assertEqual(S.of_folder(m, self.addons)["game"][0]["needs"], [dict(addon="EllesmereUI", installed=True)])
        unknown = S.of_folder(m)                                    # the game folder is not known
        self.assertEqual((unknown["addons_dir"], unknown["game"][0]["needs"], unknown["game"][0]["path"]),
                         (None, [dict(addon="EllesmereUI", installed=None)], None))

    def test_what_the_workshop_will_not_install(self):
        m = self.both()

        def cannot(*args, **kw):
            return [g["cannot"] for g in S.of_folder(m, *args, **kw)["game"]]
        self.assertEqual((cannot(), cannot(self.addons), cannot(self.addons, listed=set())), ([None, None],) * 3)
        self.assertEqual(cannot(listed={"wuxianvoice_options"}), [None, "listed"])    # known without a game folder
        (self.addons / "WuxianVoice").mkdir()                       # a folder there that is not this extension's
        self.assertEqual(cannot(self.addons, listed={"WuxianVoice_Options"}), ["foreign", "listed"])
        self.assertEqual(cannot(listed={"Details"}), [None, None])  # (and not known without the game folder)
        G.install(self.folder, ["WuxianVoice_Options"], self.addons, "voice")
        self.assertEqual(cannot(self.addons), ["foreign", None])    # this extension's own: nothing in the way
        (self.folder / "addons" / "WuxianVoice_Options" / "WuxianVoice_Options.toc").unlink()
        self.assertEqual(cannot(self.addons), ["foreign", "broken"])
        with_package = S.of_folder(m, self.addons, "package", dict(sha256="ab" * 32, file="voice.zip"),
                                   listed={"WuxianVoice"})
        self.assertEqual(with_package["game"][0]["cannot"], "listed")

    def test_it_counts_what_a_package_holds(self):
        m = self.both()
        packed = K.pack(self.folder, self.root / "out")
        package = dict(sha256=packed["sha256"], file="voice-0.2.0.zip")
        s = S.of_folder(m, None, "package", package)
        self.assertEqual((s["from"], s["package"], s["files"], s["size"]),
                         ("package", package, packed["files"], packed["unpacked"]))
        self.assertIsNot(s["package"], package)
        into = K.unpack(packed["file"], self.root / "in")           # as whoever gets it reads it: the same facts
        again = S.of_folder(self.manifest(into), None, "package", package)
        self.assertEqual(S.compare(s, again), [])
        self.assertEqual(again, s)
        self.assertIs(S.same(s, again), True)
        self.assertIsNone(S.same(s, S.of_folder(m)))

    def test_one_in_the_game_only(self):
        folder = self.root / "src" / "loot_peek"
        put(folder, M.MANIFEST, json.dumps(dict(id="loot_peek", title="掉落一瞥", version="0.1.0",
                                                addons=["LootPeek"])).encode("utf-8"))
        game_addon(folder, "LootPeek", notes="看一眼掉落", deps="WuxianKit", gid="loot_peek")
        put(folder, "addons/LootPeek/AGENT.md", b"# loot\n")
        put(folder, "addons/LootPeek/skills/peek.md", b"# peek\n")
        put(folder, "tools/build.bat", b"echo\n")
        s = S.of_folder(self.manifest(folder), self.addons)
        self.assertEqual((s["where"], s["risk"], s["local"], s["notes"]), ("game", "game", None, "看一眼掉落"))
        self.assertEqual(s["executables"], ["tools/build.bat"])     # told all the same: the workshop does not run it
        self.assertEqual(s["agent"], dict(docs="addons/LootPeek/AGENT.md", skills=["peek"], apps=[], adds=[]))
        self.assertEqual(s["game"][0]["needs"], [])


class OfAddon(Case):
    def test_an_addon_that_gives_wuxiankit_an_extension(self):
        folder = self.addons / "WuxianKit_EllesmereUI"
        put(folder, "WuxianKit_EllesmereUI.toc",
            toc("EllesmereUI 适配", notes="让 Agent 调整 EllesmereUI 的布局", deps="WuxianKit, EllesmereUI", gid="eui",
                site="https://example.com/eui", author="某人", version="0.3.1"))
        put(folder, "core.lua", b"print('x')\n")
        put(folder, "AGENT.md", b"# eui\n")
        put(folder, "skills/eui-layout.md", b"# layout\n")
        s = S.of_addon(folder, self.addons)
        self.assertEqual((s["from"], s["id"], s["title"], s["version"], s["notes"], s["author"], s["homepage"]),
                         ("game", "eui", "EllesmereUI 适配", "0.3.1", "让 Agent 调整 EllesmereUI 的布局", "某人",
                          "https://example.com/eui"))
        self.assertEqual((s["where"], s["risk"], s["local"], s["package"], s["executables"]),
                         ("game", "game", None, None, []))
        self.assertEqual(s["game"], [dict(folder="WuxianKit_EllesmereUI", title="EllesmereUI 适配",
                                          notes="让 Agent 调整 EllesmereUI 的布局", version="0.3.1", author="某人",
                                          needs=[dict(addon="EllesmereUI", installed=False)], path=str(folder),
                                          cannot=None)])              # (it is in the game: nothing to install)
        self.assertEqual(s["agent"], dict(docs="AGENT.md", skills=["eui-layout"], apps=[], adds=[]))
        self.assertEqual((s["files"], s["addons_dir"]), (4, str(self.addons)))
        (self.addons / "EllesmereUI").mkdir()
        self.assertEqual(S.of_addon(folder)["game"][0]["needs"], [dict(addon="EllesmereUI", installed=True)])

    def test_any_other_addon_is_none(self):
        plain = self.addons / "Plain"
        put(plain, "Plain.toc", toc("Plain"))
        self.assertIsNone(S.of_addon(plain, self.addons))
        odd = self.addons / "Odd"
        put(odd, "Odd.toc", toc("Odd", gid="Not An Id"))
        self.assertIsNone(S.of_addon(odd, self.addons))
        self.assertIsNone(S.of_addon(self.addons / "Nothing", self.addons))
        (self.addons / "Empty").mkdir()
        self.assertIsNone(S.of_addon(self.addons / "Empty", self.addons))


class Compare(Case):
    def test_what_differs(self):
        extension(self.folder, author="某人", env={"A": "1"})
        old = S.of_folder(self.manifest())
        self.assertEqual(S.compare(old, old), [])
        extension(self.folder, version="0.3.0", author="另一个人", run=["python", "main.py", "--fast"],
                  env={"B": "2"}, addons=["WuxianVoice"], **{"pass": ["HOME"]})
        game_addon(self.folder, "WuxianVoice", deps="WuxianKit, Details")
        put(self.folder, "bin/tts.exe", b"MZ")
        new = S.of_folder(self.manifest())
        changes = {c["field"]: c for c in S.compare(old, new)}
        self.assertEqual(list(changes), [f for f in S.FIELDS if f in changes])          # in FIELDS' order
        self.assertEqual(changes["version"], dict(field="version", old="0.2.0", new="0.3.0"))
        self.assertEqual(changes["author"], dict(field="author", old="某人", new="另一个人"))
        self.assertEqual(changes["where"], dict(field="where", old="local", new="both"))
        self.assertEqual(changes["run"], dict(field="run", old="python main.py", new="python main.py --fast"))
        self.assertEqual(changes["env"], dict(field="env", added=["B"], removed=["A"]))
        self.assertEqual(changes["pass"], dict(field="pass", added=["HOME"], removed=[]))
        self.assertEqual(changes["executables"], dict(field="executables", added=["bin/tts.exe"], removed=[]))
        self.assertEqual(changes["addons"], dict(field="addons", added=["WuxianVoice"], removed=[]))
        self.assertEqual(changes["needs"], dict(field="needs", added=["Details"], removed=[]))
        self.assertEqual((changes["files"]["old"], changes["files"]["new"]), (old["files"], new["files"]))
        self.assertNotIn("title", changes)
        back = {c["field"]: c for c in S.compare(new, old)}          # a program that goes: said as much
        self.assertEqual(back["env"], dict(field="env", added=["A"], removed=["B"]))
        self.assertEqual(back["addons"], dict(field="addons", added=[], removed=["WuxianVoice"]))

    def test_the_program_goes(self):
        extension(self.folder, addons=["WuxianVoice"])
        game_addon(self.folder, "WuxianVoice")
        old = S.of_folder(self.manifest())
        extension(self.folder, addons=["WuxianVoice"], run=None)
        new = S.of_folder(self.manifest())
        changes = {c["field"]: c for c in S.compare(old, new)}
        self.assertEqual(changes["where"], dict(field="where", old="both", new="game"))
        self.assertEqual(changes["run"], dict(field="run", old="python main.py", new=None))
        self.assertEqual(changes["start"], dict(field="start", old="use", new=None))
        self.assertEqual((old["risk"], new["risk"]), ("local", "game"))

    def test_same(self):
        a, b = dict(package=dict(sha256="aa", file="x.zip")), dict(package=dict(sha256="bb", file="x.zip"))
        self.assertIs(S.same(a, dict(a)), True)
        self.assertIs(S.same(a, b), False)
        self.assertIsNone(S.same(a, dict(package=None)))
        self.assertIsNone(S.same({}, {}))


class Detected(Case):
    def test_the_addons_that_give_wuxiankit_an_extension(self):
        for name, gid in (("WuxianKit_Guides", "guides"), ("WuxianGuild", "guild"), ("zLast", "last"),
                          ("Demo", "demo"), ("Brought", "brought"), ("Odd", "Not An Id")):
            put(self.addons / name, f"{name}.toc", toc(name, gid=gid, deps="WuxianKit"))
        put(self.addons / "Plain", "Plain.toc", toc("Plain", notes="mentions X-WuxianKit-Extension nowhere"))
        put(self.addons / "Brought", G.MARK, b"{}")                  # a local extension put it there: it has its row
        (self.addons / "Empty").mkdir()
        put(self.addons, "a-file.txt", b"x")
        found = S.detected(self.addons, skip=["demo"], official=M.KIT_EXTENSIONS)
        self.assertEqual([(s["game"][0]["folder"], s["id"], s["official"], s["from"]) for s in found],
                         [("WuxianGuild", "guild", False, "game"), ("WuxianKit_Guides", "guides", True, "game"),
                          ("zLast", "last", False, "game")])
        self.assertEqual(found[0]["game"][0]["path"], str(self.addons / "WuxianGuild"))
        self.assertEqual([s["id"] for s in S.detected(self.addons)], ["demo", "guild", "guides", "last"])
        self.assertFalse(any(s["official"] for s in S.detected(self.addons)))
        self.assertEqual(S.detected(self.root / "nowhere"), [])


class AsText(unittest.TestCase):
    def test_lines(self):
        s = {"from": "package", "id": "voice", "title": "声音", "version": "1.2.0", "author": "某人",
             "homepage": "https://example.com/voice", "package": {"sha256": "ab" * 32, "file": "voice-1.2.0.zip"},
             "where": "both", "risk": "local",
             "game": [dict(folder="WuxianVoice", version="1.2.0", path=None,
                           needs=[dict(addon="EllesmereUI", installed=False), dict(addon="Other", installed=True),
                                  dict(addon="Unknown", installed=None)]),
                      dict(folder="WuxianVoice_Options", version=None, needs=[], path="C:/g/AddOns/WuxianVoice_Options")],
             "local": {"run": ["python", "main.py"], "python": "C:/py/python.exe", "start": "workshop",
                       "env": ["VOICE_RATE"], "pass": ["DEEPSEEK_API_KEY"]},
             "executables": ["bin/tts.exe"],
             "agent": {"docs": "AGENT.md", "skills": ["speak", "tune"], "apps": ["voice_app"],
                       "adds": [dict(app="guild", wake=True, skill=True)]},
             "files": 12, "size": 20480}
        self.assertEqual(S.lines(s), [
            'voice 1.2.0 "声音"',
            "  package: voice-1.2.0.zip, sha256 " + "ab" * 32,
            "  from: author 某人 (as it says itself: not verified), https://example.com/voice",
            "  runs: in the game and on this computer",
            "  game addon: WuxianVoice 1.2.0, needs EllesmereUI (not installed), Other, Unknown, not in the game's AddOns",
            "  game addon: WuxianVoice_Options, at C:/g/AddOns/WuxianVoice_Options",
            "  program: python main.py, started with 无限工坊, its own interpreter C:/py/python.exe",
            "    sets the variables: VOICE_RATE",
            "    gets the player's variables: DEEPSEEK_API_KEY",
            "  executables: bin/tts.exe",
            "  an agent reads: AGENT.md; skills speak, tune; apps voice_app; adds to the app guild: wake, skill",
            "  12 files, 20.0 KB"])
        game = {"from": "game", "id": "eui", "where": "game", "game": [dict(folder="X", needs=[], path="C:/g/X")],
                "local": None, "agent": {"docs": None, "skills": [], "apps": [], "adds": []}, "files": None}
        self.assertEqual(S.lines(game), ["eui", "  runs: in the game only", "  game addon: X, at C:/g/X"])
        local = {"id": "tool", "where": "local", "local": {"run": ["tool.exe"], "start": "use"}, "files": 1, "size": 9}
        self.assertEqual(S.lines(local), ["tool", "  runs: on this computer", "  program: tool.exe, started on use",
                                          "  1 files, 9 bytes"])

    def test_lines_say_what_cannot_be_installed(self):
        s = {"from": "package", "id": "eui", "where": "game", "local": None, "files": None,
             "agent": {"docs": None, "skills": [], "apps": [], "adds": []},
             "game": [dict(folder="A", needs=[], path=None, cannot="listed"),
                      dict(folder="B", needs=[], path="C:/g/B", cannot="foreign"),
                      dict(folder="C", needs=[], path=None, cannot="broken"),
                      dict(folder="D", needs=[], path=None, cannot=None), dict(folder="E", needs=[], path=None)]}
        out = S.lines(s)
        self.assertEqual(out[2:8], ["  game addon: A, not in the game's AddOns", "    " + S.CANNOT_TEXT["listed"],
                                    "  game addon: B, at C:/g/B", "    " + S.CANNOT_TEXT["foreign"],
                                    "  game addon: C, not in the game's AddOns", "    " + S.CANNOT_TEXT["broken"]])
        self.assertEqual(len([line for line in out if "CANNOT BE INSTALLED" in line]), 3)
        self.assertEqual(len(out), 10)

    def test_changed(self):
        self.assertEqual(S.changed([dict(field="version", old="0.1.0", new="0.2.0"),
                                    dict(field="skills", added=["a"], removed=["b", "c"]),
                                    dict(field="size", old=10, new=4096),
                                    dict(field="run", old=None, new="python main.py"),
                                    dict(field="pass", added=[], removed=["K"])]),
                         ["version: 0.1.0 -> 0.2.0", "skills: + a; - b, c", "size: 10 bytes -> 4.0 KB",
                          "run: none -> python main.py", "pass: - K"])
        self.assertEqual((S.changed([]), S.changed(None)), ([], []))

    def test_what_compare_says_reads_as_lines(self):
        old = dict(version="0.1.0", size=10, files=1)
        self.assertEqual(S.changed(S.compare(old, dict(old, version="0.2.0"))), ["version: 0.1.0 -> 0.2.0"])


if __name__ == "__main__":
    unittest.main()
