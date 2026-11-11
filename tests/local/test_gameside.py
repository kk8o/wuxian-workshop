"""The game half of a local extension (local/gameside.py): where an addon stands, installing and removing it, and that a
folder in AddOns without this extension's mark is never written over or removed. The extension, the AddOns folder and
the history (WUXIAN_HOME) all lie in a temporary folder."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop import extensions as X
from wuxianworkshop import paths
from wuxianworkshop.agent import history
from wuxianworkshop.local import gameside as G

NAME = "WuxianVoice"


def put(folder, rel, data=b"x"):
    p = Path(folder).joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def toc(name, version="1.2.0", interface=b"## Interface: 16001\n"):
    return interface + f"## Title: {name}\n## Version: {version}\n\ncore.lua\n".encode("utf-8")


def tree(folder):
    """{posix path: bytes} of a folder's files"""
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def junction(target, at):
    """a junction at `at` to the folder `target`; False when this computer makes none"""
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(at))
    except (ImportError, OSError, AttributeError):
        return False
    return True


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(self.root / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.assertIn(self.root, paths.history_dir().resolve().parents)     # never the real history
        self.folder = self.root / "ext" / "voice"
        self.addons = self.root / "game" / "Interface" / "AddOns"
        self.addons.mkdir(parents=True)

    def addon(self, name=NAME, version="1.2.0"):
        """the extension's addon `name`: its .toc and core.lua; answers its folder"""
        d = G.source(self.folder, name)
        put(d, f"{name}.toc", toc(name, version))
        put(d, "core.lua", b"print('core')\n")
        return d

    def theirs(self, name=NAME, mark=None):
        """someone else's addon folder of that name in AddOns (mark: what its mark file says, None: none)"""
        d = self.addons / name
        put(d, f"{name}.toc", toc(name, "9.9"))
        put(d, "theirs.lua", b"-- not ours\n")
        if mark is not None:
            put(d, G.MARK, mark if isinstance(mark, bytes) else json.dumps(mark).encode("utf-8"))
        return d

    def mark(self, name=NAME):
        return json.loads((self.addons / name / G.MARK).read_text(encoding="utf-8"))

    def install(self, names=(NAME,), **kw):
        return G.install(self.folder, list(names), self.addons, "voice", **kw)

    def state(self, name=NAME, eid="voice"):
        return G.state(self.folder, name, self.addons, eid)["state"]

    def refused(self, status, code, fn, *args, **kwargs):
        with self.assertRaises(G.GameSideError) as cm:
            fn(*args, **kwargs)
        self.assertEqual((cm.exception.status, cm.exception.code), (status, code), str(cm.exception))
        return cm.exception

    def link(self, target, at):
        if not junction(target, at):
            self.skipTest("no junction here")
        self.addCleanup(lambda: os.path.lexists(at) and os.rmdir(at))

    def versions(self, name=NAME):
        return history.versions(self.addons, name)["versions"]


class Basics(Case):
    def test_source(self):
        self.assertEqual(G.source(self.folder, NAME), self.folder / "addons" / NAME)
        self.assertEqual(G.MARK, ".wuxian-extension.json")

    def test_digest(self):
        src = self.addon()
        one = G.digest(src)
        self.assertRegex(one, r"^[0-9a-f]{64}$")
        self.assertEqual(G.digest(src), one)
        copy = self.root / "elsewhere" / "Other"                    # the same files in another place, written later
        for rel, data in sorted(tree(src).items(), reverse=True):
            put(copy, rel, data)
        self.assertEqual(G.digest(copy), one)
        for rel in (G.MARK, ".gitignore", ".git/config", "debug.log", "__pycache__/x.pyc", "Thumbs.db", "sub/.DS_Store",
                    "logs/a.txt"):
            put(src, rel, b"no part of it")
        self.assertEqual(G.digest(src), one)
        put(src, "core.lua", b"print('core2')\n")
        two = G.digest(src)
        self.assertNotEqual(two, one)
        os.replace(src / "core.lua", src / "main.lua")
        three = G.digest(src)
        self.assertNotIn(three, (one, two))
        put(src, "Art/logo.tga", b"")
        self.assertNotIn(G.digest(src), (one, two, three))

    def test_digest_tells_paths_from_content(self):
        a, b = self.root / "a", self.root / "b"
        put(a, "x.lua", b"12")
        put(a, "y.lua", b"")
        put(b, "x.lua", b"1")
        put(b, "y.lua", b"2")
        self.assertNotEqual(G.digest(a), G.digest(b))

    def test_name_problem(self):
        for name in (NAME, "My_Addon-2", "a.b", "_x", "0day", "Details", "Console"):
            self.assertIsNone(G.name_problem(name), name)
        for name in ("!WuxianWorkshop", "WoWBridge", "wowbridge", "WoWBridge_Lab", "WOWBRIDGE_Extra", "WuxianKit",
                     "wuxiankit", "Blizzard_CombatLog", "blizzard_x", "", " ", "a/b", "a\\b", "..", ".", "a b",
                     ".hidden", "Voice.", "NUL", "con.x", "COM1", "x" * 65, "Voice\n", "名字", None, 7, ["a"]):
            self.assertIsInstance(G.name_problem(name), str, repr(name))
        listed = {"Details", "DBM-Core"}
        self.assertIsInstance(G.name_problem("details", listed), str)
        self.assertIsInstance(G.name_problem("DBM-Core", listed), str)
        self.assertIsNone(G.name_problem("Details2", listed))
        self.assertIsNone(G.name_problem("Details", None))
        self.assertIsNone(G.name_problem("Details", set()))


class State(Case):
    def test_unknown_missing_current_differs(self):
        src = self.addon()
        self.assertEqual(G.state(self.folder, NAME, None, "voice"), dict(folder=NAME, version="1.2.0", state="unknown"))
        self.assertEqual(G.state(self.folder, NAME, self.addons, "voice"),
                         dict(folder=NAME, version="1.2.0", state="missing"))
        self.install()
        self.assertEqual(self.state(), "current")
        put(self.addons / NAME, "core.lua", b"-- the player's own change\n")     # the digest is the source's
        self.assertEqual(self.state(), "current")
        put(src, "core.lua", b"print('new')\n")
        self.assertEqual(self.state(), "differs")
        put(src, f"{NAME}.toc", toc(NAME, "1.3.0"))
        self.assertEqual(G.state(self.folder, NAME, self.addons, "voice"),
                         dict(folder=NAME, version="1.3.0", state="differs"))
        self.assertEqual(self.state(eid="other"), "foreign")

    def test_broken(self):
        self.assertEqual(G.state(self.folder, NAME, self.addons, "voice"),
                         dict(folder=NAME, version=None, state="broken"))
        self.assertEqual(G.state(self.folder, NAME, None, "voice")["state"], "broken")
        src = put(self.folder, f"addons/{NAME}/core.lua").parent
        self.assertEqual(self.state(), "broken")                                  # no .toc
        put(src, "Other.toc", toc("Other"))
        self.assertEqual(self.state(), "broken")
        put(src, f"{NAME}_Camelot.toc", toc(NAME, "2.0"))
        self.assertEqual(G.state(self.folder, NAME, self.addons, "voice"),
                         dict(folder=NAME, version="2.0", state="missing"))
        bad = put(src, "lib/helper.exe")
        self.assertEqual(self.state(), "broken")
        self.assertIn("helper.exe", G.source_problem(self.folder, NAME))
        bad.unlink()
        self.assertIsNone(G.source_problem(self.folder, NAME))
        put(self.folder, "addons/File", b"a file, not a folder")
        for name in ("File", "../voice", "a/b", "", None, 3):
            self.assertEqual(G.state(self.folder, name, self.addons, "voice"),
                             dict(folder=name, version=None, state="broken"), repr(name))

    def test_a_linked_source_is_broken(self):
        real = self.root / "elsewhere" / NAME
        put(real, f"{NAME}.toc", toc(NAME))
        (self.folder / "addons").mkdir(parents=True)
        self.link(real, G.source(self.folder, NAME))
        self.assertEqual(self.state(), "broken")
        self.refused(400, "bad_addon", self.install)
        self.assertEqual(list(self.addons.iterdir()), [])

    def test_foreign(self):
        self.addon()
        for mark in (None, {"extension": "other", "digest": "x", "version": "1"}, b"{not json", b"[]", b'"voice"',
                     {"digest": G.digest(G.source(self.folder, NAME))}, {"extension": ["voice"]},
                     {"extension": "Voice"}):
            with self.subTest(mark=mark):
                self.theirs(mark=mark)
                self.assertEqual(self.state(), "foreign")
        for p in (self.addons / NAME).iterdir():
            p.unlink()
        self.assertEqual(self.state(), "foreign")                                 # an empty folder is someone's too
        (self.addons / NAME).rmdir()
        put(self.addons, NAME, b"a file of that name")
        self.assertEqual(self.state(), "foreign")

    def test_a_link_in_addons_is_foreign(self):
        self.addon()
        self.install()
        os.replace(self.addons / NAME, self.root / "moved")                       # ours, mark and all, behind a link
        self.link(self.root / "moved", self.addons / NAME)
        self.assertEqual(self.state(), "foreign")
        self.refused(409, "addon_exists", self.install)
        self.assertEqual(G.remove([NAME], self.addons, "voice"),
                         [dict(folder=NAME, removed=False, kept=None, why="foreign")])
        self.assertTrue((self.root / "moved" / "core.lua").is_file())

    def test_states(self):
        self.addon("One")
        self.addon("Two", version="2.0")
        self.install(["Two"])
        self.theirs("Three")
        self.addon("Three")
        self.assertEqual(G.states(self.folder, ["Two", "Nope", "One", "Three"], self.addons, "voice"),
                         [dict(folder="Two", version="2.0", state="current"),
                          dict(folder="Nope", version=None, state="broken"),
                          dict(folder="One", version="1.2.0", state="missing"),
                          dict(folder="Three", version="1.2.0", state="foreign")])
        self.assertEqual(G.states(self.folder, [], self.addons, "voice"), [])
        self.assertEqual([s["state"] for s in G.states(self.folder, ["One", "Two"], None, "voice")],
                         ["unknown", "unknown"])

    def test_a_name_of_the_catalog(self):
        self.addon()
        listed = {NAME.upper(), "Details"}
        want = dict(folder=NAME, version="1.2.0", state="listed")
        self.assertEqual(G.state(self.folder, NAME, self.addons, "voice", listed), want)
        self.assertEqual(G.state(self.folder, NAME, None, "voice", listed), want)    # known without a game folder
        self.refused(400, "bad_addon", self.install, listed=listed)                  # as install() has it
        for others in ({"Details"}, set(), None):
            self.assertEqual(G.state(self.folder, NAME, self.addons, "voice", others)["state"], "missing")
        self.theirs()                                                                # the catalog's own, in the game
        self.assertEqual(G.state(self.folder, NAME, self.addons, "voice", listed), want)
        self.assertEqual(G.state(self.folder, NAME, self.addons, "voice", {"Details"})["state"], "foreign")
        self.assertEqual(G.state(self.folder, "../x", self.addons, "voice", {"../x"})["state"], "broken")
        self.addon("Two")
        self.assertEqual([s["state"] for s in G.states(self.folder, [NAME, "Two"], None, "voice", listed)],
                         ["listed", "unknown"])


class Install(Case):
    def test_a_new_addon(self):
        src = self.addon()
        put(src, "Art/logo.tga", b"\0\1\2")
        for rel in (".gitignore", ".git/config", "debug.log", "Thumbs.db", "__pycache__/x.pyc"):
            put(src, rel)
        before = tree(src)
        got = self.install(interface=20000)
        self.assertEqual(got, dict(addons=[dict(folder=NAME, state="installed", was="missing", version="1.2.0",
                                                kept=None)], restart=True))
        self.assertEqual(sorted(p.name for p in self.addons.iterdir()), [NAME])   # nothing left beside it
        self.assertEqual(tree(self.addons / NAME),
                         {f"{NAME}.toc": toc(NAME, interface=b"## Interface: 20000\n"), "core.lua": b"print('core')\n",
                          "Art/logo.tga": b"\0\1\2", G.MARK: (self.addons / NAME / G.MARK).read_bytes()})
        self.assertEqual(self.mark(), dict(extension="voice", digest=G.digest(src), version="1.2.0"))
        self.assertEqual(tree(src), before)                                       # the source is only read
        self.assertEqual(self.state(), "current")
        self.assertEqual(self.versions(), [])

    def test_the_toc_as_it_is_without_an_interface(self):
        src = self.addon()
        put(src, f"{NAME}_Camelot.toc", b"## Title: no interface line\n")
        self.install()
        self.assertEqual((self.addons / NAME / f"{NAME}.toc").read_bytes(), toc(NAME))
        self.assertEqual((self.addons / NAME / f"{NAME}_Camelot.toc").read_bytes(), b"## Title: no interface line\n")
        put(src, "more.lua")
        self.install(interface="16002")
        self.assertEqual((self.addons / NAME / f"{NAME}.toc").read_bytes(),
                         toc(NAME, interface=b"## Interface: 16002\n"))
        self.assertEqual((self.addons / NAME / f"{NAME}_Camelot.toc").read_bytes(),
                         b"## Interface: 16002\n## Title: no interface line\n")
        self.assertEqual(self.mark()["digest"], G.digest(src))                    # the source's, not what was written
        self.assertEqual(self.state(), "current")

    def test_no_version(self):
        src = self.addon()
        put(src, f"{NAME}.toc", b"## Interface: 16001\n")
        self.assertEqual(self.install()["addons"][0]["version"], None)
        self.assertEqual(self.mark()["version"], "")

    def test_a_current_addon_stays(self):
        self.addon()
        self.install()
        put(self.addons / NAME, "note.txt", b"written since")
        got = self.install(interface=30000)
        self.assertEqual(got, dict(addons=[dict(folder=NAME, state="current", was="current", version="1.2.0",
                                                kept=None)], restart=False))
        self.assertEqual((self.addons / NAME / "note.txt").read_bytes(), b"written since")
        self.assertEqual((self.addons / NAME / f"{NAME}.toc").read_bytes(), toc(NAME))
        self.assertEqual(self.versions(), [])

    def test_a_changed_source_is_installed_again(self):
        src = self.addon()
        self.install()
        put(self.addons / NAME, "note.txt", b"the player's")
        put(src, "core.lua", b"print('new')\n")
        put(src, f"{NAME}.toc", toc(NAME, "1.3.0"))
        got = self.install()
        row = got["addons"][0]
        self.assertEqual(got, dict(addons=[dict(folder=NAME, state="installed", was="differs", version="1.3.0",
                                                kept=row["kept"])], restart=False))
        self.assertIsNotNone(row["kept"])
        kept = self.versions()
        self.assertEqual([(v["id"], v["reason"], v["note"]) for v in kept], [(row["kept"], "update", "1.2.0 -> 1.3.0")])
        self.assertEqual((self.addons / NAME / "core.lua").read_bytes(), b"print('new')\n")
        self.assertFalse((self.addons / NAME / "note.txt").exists())              # in the history now
        self.assertEqual(self.mark(), dict(extension="voice", digest=G.digest(src), version="1.3.0"))
        self.assertEqual(sorted(p.name for p in self.addons.iterdir()), [NAME])
        self.assertEqual(self.state(), "current")

    def test_restart(self):
        src = self.addon()
        self.assertTrue(self.install()["restart"])
        put(src, "Art/logo.tga")
        put(src, "README.md")
        put(src, "core.lua", b"print('changed')\n")
        self.assertFalse(self.install()["restart"])                               # no code file more or less
        put(src, "extra.lua")
        self.assertTrue(self.install()["restart"])
        self.assertFalse(self.install()["restart"])                               # current
        (src / "extra.lua").unlink()
        self.assertTrue(self.install()["restart"])
        put(src, "frames.xml")
        self.assertTrue(self.install()["restart"])
        os.replace(src / "core.lua", src / "Core.lua")                            # the same file to the client
        put(src, "README.md", b"so that it differs")
        self.assertFalse(self.install()["restart"])

    def test_several(self):
        self.addon("One")
        self.addon("Two", version="2.0")
        self.install(["One"])
        got = self.install(["One", "Two"])
        self.assertEqual(got["addons"], [dict(folder="One", state="current", was="current", version="1.2.0", kept=None),
                                         dict(folder="Two", state="installed", was="missing", version="2.0", kept=None)])
        self.assertTrue(got["restart"])
        self.assertEqual(self.install([]), dict(addons=[], restart=False))

    def test_someone_elses_folder_is_never_written_over(self):
        self.addon("Fine")
        self.addon()
        for mark in (None, {"extension": "other", "digest": "x", "version": "1"}, b"{broken"):
            with self.subTest(mark=mark):
                self.theirs(mark=mark)
                before = tree(self.addons)
                e = self.refused(409, "addon_exists", self.install, ["Fine", NAME])
                self.assertIn(NAME, str(e))
                self.assertEqual(tree(self.addons), before)                       # Fine is not installed either
                self.assertEqual(sorted(p.name for p in self.addons.iterdir()), [NAME])
        self.assertEqual(self.versions(), [])
        self.assertEqual(self.versions("Fine"), [])

    def test_names_and_sources_that_cannot_be(self):
        self.addon("Fine")
        for name in ("WoWBridge", "WuxianKit", "Blizzard_Voice", "!WuxianWorkshop", "WoWBridge_Mine"):
            put(self.folder, f"addons/{name}/{name}.toc", toc(name))
        put(self.folder, "addons/NoToc/core.lua")
        put(self.folder, "addons/WithExe/WithExe.toc", toc("WithExe"))
        put(self.folder, "addons/WithExe/tool.exe")
        for names in (["Fine", "WoWBridge"], ["Fine", "WuxianKit"], ["Fine", "Blizzard_Voice"],
                      ["Fine", "!WuxianWorkshop"], ["Fine", "WoWBridge_Mine"], ["Fine", "NoToc"], ["Fine", "WithExe"],
                      ["Fine", "Nowhere"], ["Fine", "fine"], ["Fine", "../Fine"], ["Fine", ""], ["Fine", None],
                      ["Fine", "Fine."], ["Fine", "NUL"]):
            with self.subTest(names=names):
                self.refused(400, "bad_addon", self.install, names)
                self.assertEqual(list(self.addons.iterdir()), [])
        self.refused(400, "bad_addon", self.install, ["Fine"], listed={"fine", "Details"})
        self.refused(400, "bad_addon", G.install, self.folder, "Fine", self.addons, "voice")
        self.refused(400, "bad_addon", G.install, self.folder, ["Fine"], self.addons, "")
        self.refused(400, "bad_addon", G.install, self.folder, ["Fine"], self.addons, None)
        self.assertEqual(list(self.addons.iterdir()), [])
        self.assertEqual(self.install(["Fine"], listed={"Details"})["addons"][0]["state"], "installed")

    def test_no_game_folder(self):
        self.addon()
        self.refused(503, "no_game_folder", G.install, self.folder, [NAME], None, "voice")
        self.refused(503, "no_game_folder", G.install, self.folder, [NAME], self.root / "nowhere" / "AddOns", "voice")
        self.assertFalse((self.root / "nowhere").exists())

    def test_what_an_earlier_try_left(self):
        src = self.addon()
        put(self.addons, f".{NAME}.wuxian-new/junk.lua")
        put(self.addons, f".{NAME}.wuxian-old/old.lua")
        self.install()
        self.assertEqual(sorted(p.name for p in self.addons.iterdir()), [NAME])
        self.assertEqual(sorted(tree(self.addons / NAME)), sorted([G.MARK, *tree(src)]))

    def test_taken_between_the_check_and_the_writing(self):
        self.addon()
        self.theirs()
        before = tree(self.addons)
        with mock.patch.object(G, "state", return_value=dict(folder=NAME, version="1.2.0", state="missing")):
            self.refused(409, "addon_exists", self.install)
        self.assertEqual(tree(self.addons), before)
        self.assertEqual(sorted(p.name for p in self.addons.iterdir()), [NAME])

    def test_a_failure_leaves_the_folder_as_it_was(self):
        src = self.addon()
        self.install()
        before = tree(self.addons)
        put(src, "core.lua", b"print('new')\n")
        with mock.patch.object(X, "_swap", side_effect=OSError("the disk is full")):
            e = self.refused(500, "addon_failed", self.install)
        self.assertIn("the disk is full", str(e))
        self.assertEqual(tree(self.addons), before)
        self.assertEqual(sorted(p.name for p in self.addons.iterdir()), [NAME])

        def half(target, staging, backup):                                        # the old one aside, then it fails
            os.replace(target, backup)
            raise OSError("gone wrong")

        with mock.patch.object(X, "_swap", side_effect=half):
            self.refused(500, "addon_failed", self.install)
        self.assertEqual(tree(self.addons), before)                               # put back
        self.assertEqual(sorted(p.name for p in self.addons.iterdir()), [NAME])
        self.assertEqual(self.state(), "differs")

    @unittest.skipUnless(os.name == "nt", "the game holds files this way on Windows only")
    def test_the_game_holds_the_folder(self):
        self.addon("First")
        src = self.addon()
        self.install()
        before = tree(self.addons)
        put(src, "core.lua", b"print('new')\n")
        started = time.monotonic()
        with open(self.addons / NAME / "core.lua", "rb"), mock.patch.object(time, "sleep") as sleep:
            e = self.refused(409, "addon_busy", self.install, ["First", NAME])
            self.assertEqual(sleep.call_count, 19)                                # tried again for about two seconds
            self.assertEqual(e.done, [dict(folder="First", state="installed", was="missing", version="1.2.0",
                                           kept=None)])
            self.assertEqual(sorted(p.name for p in self.addons.iterdir()), ["First", NAME])
            self.assertEqual({f"{NAME}/{k}": v for k, v in tree(self.addons / NAME).items()}, before)
            self.assertEqual(self.state(), "differs")
            self.assertEqual(G.remove([NAME], self.addons, "voice")[0]["why"], "busy")
            self.assertTrue((self.addons / NAME / "core.lua").is_file())
        self.assertLess(time.monotonic() - started, 30)
        got = self.install(["First", NAME])                                       # the game let go
        self.assertEqual([(r["state"], r["was"]) for r in got["addons"]],
                         [("current", "current"), ("installed", "differs")])
        self.assertEqual((self.addons / NAME / "core.lua").read_bytes(), b"print('new')\n")


class Remove(Case):
    def test_ours(self):
        self.addon()
        self.addon("Two", version="2.0")
        self.install([NAME, "Two"])
        got = G.remove(["Two"], self.addons, "voice")
        self.assertEqual(got, [dict(folder="Two", removed=True, kept=got[0]["kept"], why=None)])
        self.assertIsNotNone(got[0]["kept"])
        self.assertEqual(sorted(p.name for p in self.addons.iterdir()), [NAME])   # the other stays, nothing beside it
        self.assertEqual([(v["id"], v["reason"], v["note"]) for v in self.versions("Two")],
                         [(got[0]["kept"], "remove", "2.0")])
        self.assertEqual(self.state("Two"), "missing")
        self.assertEqual(G.remove(["Two"], self.addons, "voice"),
                         [dict(folder="Two", removed=False, kept=None, why="missing")])

    def test_only_ours(self):
        self.addon()
        self.install()
        self.theirs("Plain")
        self.theirs("Others", mark={"extension": "other", "digest": "x", "version": "1"})
        self.theirs("Broken", mark=b"{")
        put(self.addons, "AFile", b"a file")
        before = tree(self.addons)
        names = ["Plain", "Others", "Broken", "AFile", "Nowhere", "../AddOns", "", None, "WuxianKit", NAME]
        got = G.remove(names, self.addons, "other2")
        self.assertEqual([(r["folder"], r["removed"], r["kept"], r["why"]) for r in got],
                         [("Plain", False, None, "foreign"), ("Others", False, None, "foreign"),
                          ("Broken", False, None, "foreign"), ("AFile", False, None, "foreign"),
                          ("Nowhere", False, None, "missing"), ("../AddOns", False, None, "bad_name"),
                          ("", False, None, "bad_name"), (None, False, None, "bad_name"),
                          ("WuxianKit", False, None, "missing"), (NAME, False, None, "foreign")])
        self.assertEqual(tree(self.addons), before)
        self.assertEqual(G.remove(["Others"], self.addons, "other")[0]["removed"], True)   # its own extension may
        self.assertEqual(G.remove(["Plain", NAME], self.addons, "voice"),
                         [dict(folder="Plain", removed=False, kept=None, why="foreign"),
                          dict(folder=NAME, removed=True, kept=mock.ANY, why=None)])
        self.assertTrue((self.addons / "Plain" / "theirs.lua").is_file())

    def test_no_game_folder(self):
        self.assertEqual(G.remove([NAME, "Two"], None, "voice"),
                         [dict(folder=NAME, removed=False, kept=None, why="no_game_folder"),
                          dict(folder="Two", removed=False, kept=None, why="no_game_folder")])
        self.assertEqual(G.remove([NAME], self.root / "nowhere", "voice")[0]["why"], "no_game_folder")
        self.assertEqual(G.remove([], self.addons, "voice"), [])
        self.assertEqual(G.remove(NAME, self.addons, "voice"), [])

    @unittest.skipUnless(os.name == "nt", "the game holds files this way on Windows only")
    def test_the_game_holds_the_folder(self):
        self.addon()
        self.install()
        before = tree(self.addons)
        with open(self.addons / NAME / "core.lua", "rb"), mock.patch.object(time, "sleep") as sleep:
            got = G.remove([NAME], self.addons, "voice")
            self.assertEqual(sleep.call_count, 20)
        self.assertEqual((got[0]["removed"], got[0]["why"]), (False, "busy"))
        self.assertEqual(tree(self.addons), before)
        self.assertEqual(sorted(p.name for p in self.addons.iterdir()), [NAME])
        self.assertEqual(G.remove([NAME], self.addons, "voice")[0]["removed"], True)


if __name__ == "__main__":
    unittest.main()
