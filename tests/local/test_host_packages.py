"""Packages and the game half of a local extension through the host (host.py with pack.py and gameside.py), with real
processes of the fixture: a package installed and one installed over the version there, what a package never replaces,
a bad package that leaves nothing, where pack writes, and the game addons: installed when asked, when the player allows
the extension and when it is loaded, taken out when it is removed, and how long a row's look at them is kept. The
extensions' home folder, the game's AddOns folder and the history (WUXIAN_HOME) all lie in a temporary folder."""
import json
import os
import zipfile
from pathlib import Path
from unittest import mock

from wuxianworkshop import paths
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import gameside as G
from wuxianworkshop.local import home as HOME
from wuxianworkshop.local import host as H
from wuxianworkshop.local import manifest as M
from wuxianworkshop.local import pack as K
from wuxianworkshop.local import process as P

from .util import HostCase, gone, make_extension, until

NAME = "WuxianFx"
INSTALLED = dict(folder=NAME, state="installed", was="missing", version="1.2.0", kept=None)


def put(folder, rel, text="x"):
    p = Path(folder).joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def toc(name, version="1.2.0"):
    return f"## Interface: 16001\n## Title: {name}\n## Version: {version}\n\ncore.lua\n"


class Case(HostCase):
    """a host that knows a game: AddOns is a folder of the test's (self.game None: the game folder is not known).
    The workshop allows commands by itself (auto_now True) unless a test says the player is asked"""

    def setUp(self):
        super().setUp()
        self.assertIn(self.root, paths.history_dir().resolve().parents)     # never the real history
        self.home = self.host.home
        self.addons = self.root / "game" / "Interface" / "AddOns"
        self.addons.mkdir(parents=True)
        self.game = self.addons
        self.host.addons_dir = lambda: self.game
        self.host.interface = lambda: 16002

    def source(self, eid="fx", version="0.1.0", addons=(NAME,), addon_version="1.2.0", where=None, **fields):
        """an extension's folder (root/work/<id> unless `where`) with the game addons `addons`, each a .toc and
        core.lua; written over what is there"""
        folder = Path(where) if where else self.root / "work" / eid
        for name in addons:
            put(G.source(folder, name), f"{name}.toc", toc(name, addon_version))
            put(G.source(folder, name), "core.lua", "print('core')\n")
        if addons:
            fields.setdefault("addons", list(addons))
        return make_extension(folder, eid, version=version, **fields)

    def linked(self, eid="fx", **kw):
        """registers such a folder by its path (root/ext/<id>): (its row, its folder)"""
        folder = self.source(eid, where=self.root / "ext" / eid, **kw)
        return self.host.add(str(folder)), folder

    def package(self, eid="fx", **kw):
        """the package of such a folder, in root/out"""
        return Path(K.pack(self.source(eid, **kw), self.root / "out")["file"])

    def err(self, status, code, fn, *args, **kwargs):
        with self.assertRaises(ApiError) as cm:
            fn(*args, **kwargs)
        self.assertEqual((cm.exception.status, cm.exception.code), (status, code), cm.exception.message)
        return cm.exception

    def ids(self):
        return [e["id"] for e in self.host.load()["extensions"]]

    def said(self, words):
        return [n for n in self.notes if words in n]

    def mark(self, name=NAME):
        return json.loads((self.addons / name / G.MARK).read_text(encoding="utf-8"))

    def stands(self, eid="fx"):
        """where the extension's game addons stand, as its row says"""
        return [a["state"] for a in self.host.get(eid)["addons"]]

    def nothing_left(self):
        self.assertEqual(self.ids(), [])
        self.assertFalse((self.home / HOME.INCOMING).exists())


class Installing(Case):
    def test_a_package_is_installed(self):
        zip_path = self.package()
        told = len(self.revisions)
        row = self.host.add(str(zip_path))
        folder = self.home / "fx"
        self.assertEqual(row.pop("package"), dict(file=str(zip_path), replaced=None))
        self.assertEqual((row["id"], row["version"], row["path"], row["origin"], row["enabled"], row["approval"],
                          row["allowed"]), ("fx", "0.1.0", str(folder), "home", True, "needed", False))
        self.assertEqual(row["addons"], [dict(folder=NAME, version="1.2.0", state="missing")])
        e = self.host.entry("fx")
        self.assertEqual((e["ask"], e.get("approved"), e["described"], e["options"]), (True, None, None, {}))
        self.assertTrue((folder / "main.py").is_file())
        self.assertTrue((G.source(folder, NAME) / "core.lua").is_file())
        self.assertFalse((self.home / HOME.INCOMING).exists())                   # nothing left of the unpacking
        self.assertGreater(len(self.revisions), told)
        self.assertEqual(len(self.said("imported from a package")), 1)
        error = self.host.ensure("fx").exception(5)              # nothing of it runs before the player allows it,
        self.assertEqual(error.code, "needs_approval")           # whatever the setting says
        self.assertFalse((self.addons / NAME).exists())
        self.assertFalse(self.host.discover(force=True))         # the folder there is this extension
        self.assertEqual(self.ids(), ["fx"])
        self.host.approve("fx")
        self.host.call_sync("fx.echo", {"text": "a"})
        self.assertIsNotNone(self.host.get("fx")["pid"])

    def test_a_package_over_the_version_there(self):
        self.host.add(str(self.package()))
        self.host.approve("fx")                                  # (its game addon goes into AddOns with that)
        self.assertTrue(until(lambda: self.host.entry("fx")["described"] is not None))
        self.host.set("fx", options={"volume": 70})
        self.host.call_sync("fx.echo", {"text": "a"})
        pid = self.host.get("fx")["pid"]
        self.assertEqual(self.stands(), ["current"])
        newer = self.package(version="0.2.0", addon_version="1.3.0")
        row = self.host.add(str(newer))
        folder, kept = self.home / "fx", Path(row["package"]["replaced"])
        self.assertEqual(row["package"]["file"], str(newer))
        self.assertTrue(gone(pid))                               # the one that ran was stopped
        self.assertEqual(kept.parent, self.home / HOME.REMOVED)  # and its folder is kept
        self.assertTrue(kept.name.startswith("fx-"), kept)
        self.assertEqual(json.loads((kept / M.MANIFEST).read_text(encoding="utf-8"))["version"], "0.1.0")
        self.assertEqual(json.loads((folder / M.MANIFEST).read_text(encoding="utf-8"))["version"], "0.2.0")
        self.assertEqual((row["version"], row["path"], row["origin"], row["enabled"], row["approval"], row["allowed"]),
                         ("0.2.0", str(folder), "home", True, "needed", False))
        e = self.host.entry("fx")                                # new to the player; enabled and options stay
        self.assertEqual((e["ask"], e.get("approved"), e["described"], e["options"]),
                         (True, None, None, {"volume": 70}))
        self.assertEqual(row["addons"], [dict(folder=NAME, version="1.3.0", state="differs")])   # the game has the old
        self.assertEqual(self.host.ensure("fx").exception(5).code, "needs_approval")
        self.assertFalse((self.home / HOME.INCOMING).exists())
        self.assertEqual(len(self.said("replaced by another version")), 1)
        self.host.approve("fx")                                  # allowed again: it runs with the options kept
        self.assertEqual(self.host.call_sync("fx.opts"), {"volume": 70})
        self.assertEqual(self.host.get("fx")["addons"], [dict(folder=NAME, version="1.3.0", state="current")])
        self.host.set("fx", enabled=False)                       # one that is off stays off
        row = self.host.add(str(self.package(version="0.3.0")))
        self.assertEqual((row["version"], row["enabled"], row["approval"]), ("0.3.0", False, "needed"))
        self.assertEqual(self.host.entry("fx")["options"], {"volume": 70})
        self.assertEqual(len(os.listdir(self.home / HOME.REMOVED)), 2)
        self.assertEqual(self.ids(), ["fx"])

    def test_a_linked_one_is_never_replaced(self):
        _, folder = self.linked()
        zip_path = self.package(version="0.2.0")
        e = self.err(409, "extension_exists", self.host.add, str(zip_path))
        self.assertEqual(e.extra["path"], str(folder))
        self.assertIn("extension_remove fx", e.message)
        self.assertEqual((self.host.get("fx")["version"], self.host.get("fx")["origin"]), ("0.1.0", "linked"))
        self.assertFalse((self.home / "fx").exists())
        self.assertFalse((self.home / HOME.INCOMING).exists())
        self.host.remove("fx")                                   # then the package takes the id
        row = self.host.add(str(zip_path))
        self.assertEqual((row["version"], row["origin"]), ("0.2.0", "home"))
        self.assertTrue((folder / "main.py").is_file())          # the folder elsewhere is as it was

    def test_a_bad_package_leaves_nothing(self):
        out = self.root / "out"
        out.mkdir()
        (out / "junk.zip").write_bytes(b"no zip at all")
        self.err(400, "bad_package", self.host.add, str(out / "junk.zip"))
        self.err(400, "bad_package", self.host.add, str(out / "none.zip"))
        self.nothing_left()
        half = out / "half.zip"                                  # a package as read() takes it: its manifest does not pass
        with zipfile.ZipFile(half, "w") as z:
            z.writestr(f"fx/{M.MANIFEST}", json.dumps({"id": "fx", "title": "half"}))
            z.writestr("fx/main.py", "print(1)\n")
        e = self.err(400, "bad_package", self.host.add, str(half))
        self.assertIn("half.zip", e.message)
        self.assertTrue(M.errors_in(e.extra["problems"]))
        self.assertEqual(e.body()["problems"], e.extra["problems"])
        self.nothing_left()
        self.assertFalse((self.home / "fx").exists())
        good = self.package(addons=())
        self.host.taken = lambda: ("fx",)                        # an id one of WuxianKit's extensions has
        e = self.err(400, "bad_package", self.host.add, str(good))
        self.assertTrue(any(p["where"].endswith("id") for p in e.extra["problems"]), e.extra["problems"])
        self.nothing_left()
        self.host.taken = lambda: ()
        with mock.patch.object(K, "MAX_ZIP", 64):
            self.err(413, "package_too_big", self.host.add, str(good))
        self.nothing_left()
        put(self.home / "fx", "notes.txt", "mine")               # something else has its place
        e = self.err(409, "folder_not_empty", self.host.add, str(good))
        self.assertEqual(e.extra["path"], str(self.home / "fx"))
        self.assertEqual(os.listdir(self.home / "fx"), ["notes.txt"])
        self.nothing_left()
        self.assertEqual(self.said("imported from a package"), [])

    def test_a_folder_that_cannot_be_moved_away(self):
        self.host.add(str(self.package()))
        newer = self.package(version="0.2.0")
        with mock.patch.object(HOME, "trash", side_effect=PermissionError(13, "in use")):
            e = self.err(409, "folder_busy", self.host.add, str(newer))
        self.assertEqual(e.extra["path"], str(self.home / "fx"))
        self.assertEqual((self.ids(), self.host.get("fx")["version"]), (["fx"], "0.1.0"))   # the version there stays
        self.assertFalse((self.home / HOME.INCOMING).exists())
        self.assertEqual(self.host.add(str(newer))["version"], "0.2.0")


class Packing(Case):
    def test_where_a_package_goes(self):
        _, folder = self.linked()
        res = self.host.pack("fx")
        default = self.root / "home" / H.PACKAGES / "fx-0.1.0.zip"               # <WUXIAN_HOME>\packages
        self.assertEqual(sorted(res), ["file", "files", "id", "sha256", "size", "skipped", "unpacked", "version"])
        self.assertEqual((res["id"], res["version"], res["file"]), ("fx", "0.1.0", str(default)))
        self.assertEqual((K.read(default)["id"], default.stat().st_size), ("fx", res["size"]))
        self.assertEqual(len(self.said("packed as")), 1)
        there = self.root / "there"
        self.assertEqual(self.host.pack("fx", str(there))["file"], str(there / "fx-0.1.0.zip"))    # a folder
        self.assertEqual(self.host.pack("fx", f" {there / 'mine.zip'} ")["file"], str(there / "mine.zip"))
        self.assertEqual(self.host.pack("fx", str(there))["sha256"], res["sha256"])    # the same files, the same zip
        for bad in ("packages", "", 7):
            self.err(400, "bad_request", self.host.pack, "fx", bad)
        self.err(404, "not_found", self.host.pack, "nobody")
        (folder / M.MANIFEST).write_text("{", encoding="utf-8")
        e = self.err(400, "bad_manifest", self.host.pack, "fx")
        self.assertTrue(e.extra["problems"])


class GameHalf(Case):
    def test_install(self):
        _, folder = self.linked()
        self.assertEqual(self.stands(), ["missing"])
        res = self.host.install("fx")
        self.assertEqual(res, dict(id="fx", addons=[INSTALLED], restart=True, hint=res["hint"]))
        self.assertEqual(res["hint"], f"{NAME}: new in the game's AddOns, restart the game to load it")
        self.assertIn("## Interface: 16002", (self.addons / NAME / f"{NAME}.toc").read_text(encoding="utf-8"))
        self.assertEqual(self.mark()["extension"], "fx")
        self.assertEqual(self.stands(), ["current"])
        self.assertEqual(len(self.said("game addon installed")), 1)
        again = self.host.install("fx")                          # nothing to do
        self.assertEqual((again["addons"][0]["state"], again["restart"], again["hint"]), ("current", False, None))
        self.assertEqual(len(self.said("game addon installed")), 1)
        put(G.source(folder, NAME), "core.lua", "print('changed')\n")
        res = self.host.install("fx")                            # the game has it: no restart for a changed file
        (row,) = res["addons"]
        self.assertEqual((row["state"], row["was"], res["restart"]), ("installed", "differs", False))
        self.assertIsNotNone(row["kept"])                        # what was there is in the history
        self.assertIn(f"`load {NAME}`", res["hint"])
        self.assertNotIn("restart", res["hint"])

    def test_install_refused(self):
        self.auto_now = False                                    # the player is asked
        _, folder = self.linked()
        self.err(403, "needs_approval", self.host.install, "fx")
        self.assertFalse((self.addons / NAME).exists())
        self.auto_now = True
        self.game = None
        self.err(503, "no_game_folder", self.host.install, "fx")
        self.game = self.addons
        theirs = put(self.addons / NAME, "theirs.lua", "-- not ours\n")          # someone else's addon of the name
        e = self.err(409, "addon_exists", self.host.install, "fx")
        self.assertEqual((e.extra["done"], e.body()["done"]), ([], []))
        self.assertEqual(os.listdir(self.addons / NAME), ["theirs.lua"])
        self.assertTrue(theirs.is_file())
        self.linked("plain", addons=())
        self.err(400, "no_addons", self.host.install, "plain")
        self.linked("newer", addons=("WuxianNewer",), requires={"workshop": "10.0.0"})
        self.err(409, "requires_unmet", self.host.install, "newer")
        self.assertFalse((self.addons / "WuxianNewer").exists())
        self.err(404, "not_found", self.host.install, "nobody")
        (folder / M.MANIFEST).write_text("{", encoding="utf-8")
        self.err(409, "bad_manifest", self.host.install, "fx")

    def test_install_that_fails_half_way(self):
        self.linked("two", addons=("WuxianA", "WuxianB"))
        real = G._put

        def held(folder, name, *args, **kwargs):
            if name == "WuxianB":
                raise G.GameSideError(409, "addon_busy", "WuxianB: the game holds a file of AddOns/WuxianB")
            return real(folder, name, *args, **kwargs)
        with mock.patch.object(G, "_put", side_effect=held):
            e = self.err(409, "addon_busy", self.host.install, "two")
        self.assertEqual(e.extra["done"], [dict(INSTALLED, folder="WuxianA")])   # the one before it is installed
        self.assertEqual(self.stands("two"), ["current", "missing"])
        res = self.host.install("two")
        self.assertEqual([(a["folder"], a["state"]) for a in res["addons"]],
                         [("WuxianA", "current"), ("WuxianB", "installed")])
        self.assertEqual((res["restart"], res["hint"]),
                         (True, "WuxianB: new in the game's AddOns, restart the game to load it"))

    def test_the_players_approval_installs(self):
        self.auto_now = False
        row, _ = self.linked()
        self.assertEqual((row["approval"], row["addons"]),
                         ("needed", [dict(folder=NAME, version="1.2.0", state="missing")]))
        row = self.host.approve("fx")
        self.assertEqual((row["approval"], row["addons"]),
                         ("ok", [dict(folder=NAME, version="1.2.0", state="current")]))
        self.assertEqual(self.mark()["extension"], "fx")
        self.assertEqual(len(self.said("game addon installed")), 1)
        self.linked("off", addons=("WuxianOff",))                # one that is off: allowed, its addon is there too
        self.host.set("off", enabled=False)
        self.host.approve("off")
        self.assertEqual(self.stands("off"), ["current"])
        self.linked("dev", addons=("WuxianDev",))                # the workshop's own approval installs nothing
        self.assertEqual(self.host.approve("dev", by="developer")["approval"], "ok")
        self.assertEqual(self.stands("dev"), ["missing"])

    def test_an_approval_never_fails_on_the_game(self):
        self.auto_now = False
        self.linked()
        theirs = put(self.addons / NAME, "theirs.lua", "-- not ours\n")
        row = self.host.approve("fx")
        self.assertEqual((row["approval"], row["addons"][0]["state"]), ("ok", "foreign"))
        (note,) = self.said("its game addons are not installed")
        self.assertIn("extension_install fx", note)
        self.assertTrue(theirs.is_file())
        self.linked("odd", addons=("WuxianOdd",))                # whatever goes wrong there
        with mock.patch.object(G, "install", side_effect=RuntimeError("no")):
            self.assertEqual(self.host.approve("odd")["approval"], "ok")
        self.assertEqual(len(self.said("its game addons are not installed")), 2)
        self.linked("quiet", addons=("WuxianQuiet",))            # no game folder: nothing to say
        self.game = None
        row = self.host.approve("quiet")
        self.assertEqual((row["approval"], row["addons"][0]["state"]), ("ok", "unknown"))
        self.assertEqual(len(self.said("its game addons are not installed")), 2)

    def test_a_load_installs(self):
        _, folder = self.linked()                                # (the workshop allows its command by itself)
        res = self.host.load_("fx")
        self.assertEqual((res["addons"], res["restart"], res["check"]["ok"]), ([INSTALLED], True, True))
        self.assertIn("restart the game", res["hint"])
        self.assertEqual(res["row"]["addons"], [dict(folder=NAME, version="1.2.0", state="current")])
        res = self.host.load_("fx")
        self.assertEqual((res["addons"][0]["state"], res["restart"], res["hint"]), ("current", False, None))
        put(G.source(folder, NAME), "core.lua", "print('changed')\n")
        res = self.host.load_("fx")
        self.assertEqual((res["addons"][0]["state"], res["addons"][0]["was"], res["restart"], res["check"]["ok"]),
                         ("installed", "differs", False, True))
        self.assertIn(f"`load {NAME}`", res["hint"])
        self.linked("plain", addons=())                          # none: the three are there all the same
        res = self.host.load_("plain")
        self.assertEqual((res["addons"], res["restart"], res["hint"]), ([], False, None))

    def test_a_load_that_cannot_install(self):
        self.linked()
        self.game = None                                         # no game folder: said, and no problem of its own
        res = self.host.load_("fx")
        self.assertEqual((res["addons"], res["restart"], res["check"]["ok"]),
                         ([dict(folder=NAME, version="1.2.0", state="unknown")], False, True))
        self.assertIn("extension_install fx", res["hint"])
        self.game = self.addons
        theirs = put(self.addons / NAME, "theirs.lua", "-- not ours\n")
        res = self.host.load_("fx")                              # someone else's addon of the name: an error of the check
        self.assertEqual((res["addons"], res["restart"], res["hint"], res["check"]["ok"]), ([], False, None, False))
        (problem,) = [p for p in res["check"]["problems"] if p["where"] == "addons"]
        self.assertEqual(problem["level"], "error")
        self.assertIn("extension_install fx", problem["hint"])
        self.assertTrue(theirs.is_file())
        self.assertEqual(res["row"]["state"], "running")         # it runs all the same
        self.assertIn("fx.echo", res["check"]["capabilities"])

    def test_a_removal_takes_them_out(self):
        _, folder = self.linked()
        self.host.install("fx")
        self.host.call_sync("fx.echo", {"text": "a"})
        data = P.data_folder("fx", self.root / "state")
        self.assertTrue(data.is_dir())
        put(data, "kept.json", "{}")
        res = self.host.remove("fx")
        (row,) = res.pop("addons")
        self.assertEqual(res, dict(removed=True, id="fx", path=str(folder), kept=None))
        self.assertEqual((row["folder"], row["removed"], row["why"]), (NAME, True, None))
        self.assertEqual(sorted(row), ["folder", "kept", "removed", "why"])
        self.assertFalse((self.addons / NAME).exists())
        self.assertTrue((folder / "main.py").is_file())
        self.assertTrue((data / "kept.json").is_file())          # what it kept stays
        self.assertEqual(len(self.said("deleted from AddOns")), 1)

    def test_what_a_removal_leaves(self):
        self.linked()                                            # never installed: someone else's addon of the name
        theirs = put(self.addons / NAME, "theirs.lua", "-- not ours\n")
        res = self.host.remove("fx")
        self.assertEqual(res["addons"], [dict(folder=NAME, removed=False, kept=None, why="foreign")])
        self.assertNotIn("hint", res)
        self.assertTrue(theirs.is_file())
        self.linked("b", addons=("WuxianB",))                    # none there: nothing to tell
        self.assertEqual(self.host.remove("b")["addons"], [])
        self.linked("c", addons=("WuxianC",))                    # the game folder is not known: it stays there
        self.host.install("c")
        self.game = None
        self.assertEqual(self.host.remove("c")["addons"], [])
        self.assertTrue((self.addons / "WuxianC" / G.MARK).is_file())
        self.game = self.addons
        _, folder = self.linked("g", addons=("WuxianG",))        # a manifest that does not pass any more
        self.host.install("g")
        manifest = json.loads((folder / M.MANIFEST).read_text(encoding="utf-8"))
        manifest["run"] = "main.py"
        (folder / M.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        self.assertIsNone(M.check_manifest(folder)[0])
        self.assertEqual([(a["folder"], a["removed"]) for a in self.host.remove("g")["addons"]], [("WuxianG", True)])
        self.assertFalse((self.addons / "WuxianG").exists())
        self.linked("d", addons=("WuxianD", "WuxianE"))          # the game holds one
        busy = [dict(folder="WuxianD", removed=True, kept=None, why=None),
                dict(folder="WuxianE", removed=False, kept=None, why="busy")]
        with mock.patch.object(G, "remove", return_value=busy) as asked:
            res = self.host.remove("d")
        self.assertEqual(asked.call_args.args, (["WuxianD", "WuxianE"], self.addons, "d"))
        self.assertEqual(res["addons"], busy)
        self.assertEqual(res["hint"], "WuxianE: still in the game's AddOns, the game holds its files: quit the game, "
                                      "then delete the folder there")

    def test_the_look_is_kept(self):
        _, folder = self.linked()

        def aged():
            when, key, rows = self.host.addon_states["fx"]
            self.host.addon_states["fx"] = (when - H.ADDONS_KEPT - 1, key, rows)
        self.host.forget_addons()                                # (adding it looked already)
        with mock.patch.object(G, "states", wraps=G.states) as looked:
            self.assertEqual(self.stands(), ["missing"])
            self.host.rows()
            self.host.listing()
            self.assertEqual(looked.call_count, 1)               # one look for all of them
            G.install(folder, [NAME], self.addons, "fx")         # behind the host's back: seen at its next look
            self.assertEqual(self.stands(), ["missing"])
            aged()
            self.assertEqual((self.stands(), looked.call_count), (["current"], 2))
            self.game = None                                     # another game folder is another question
            self.assertEqual(self.stands(), ["unknown"])
            self.game = self.addons
            self.assertEqual(self.stands(), ["current"])
            put(G.source(folder, NAME), "core.lua", "print('two')\n")
            self.assertEqual(self.stands(), ["current"])         # (kept)
            aged()
            self.assertEqual(self.stands(), ["differs"])
            self.host.install("fx")                              # what the host does itself is seen at once
            self.assertEqual(self.stands(), ["current"])
            put(G.source(folder, NAME), "core.lua", "print('three')\n")
            self.assertEqual(self.stands(), ["current"])         # (kept)
            self.host.approve("fx", by="developer")
            self.assertEqual(self.stands(), ["differs"])
            self.host.load_("fx")
            self.assertEqual(self.stands(), ["current"])
            put(G.source(folder, NAME), "core.lua", "print('four')\n")
            aged()
            self.assertEqual(self.stands(), ["differs"])
            self.host.remove("fx")                               # taken out of the game: the same folder added again
            self.host.add(str(folder))
            self.assertEqual(self.stands(), ["missing"])


class WholeWay(Case):
    def test_packed_installed_allowed_and_removed(self):
        _, folder = self.linked()                                # the folder it was written in
        packed = self.host.pack("fx")
        self.err(409, "extension_exists", self.host.add, packed["file"])
        self.assertEqual(self.host.remove("fx")["addons"], [])
        row = self.host.add(packed["file"])                      # as another computer gets it
        self.assertEqual((row["origin"], row["approval"], row["package"]),
                         ("home", "needed", dict(file=packed["file"], replaced=None)))
        self.err(403, "needs_approval", self.host.install, "fx")
        self.err(403, "needs_approval", self.host.load_, "fx")
        self.assertFalse((self.addons / NAME).exists())
        row = self.host.approve("fx")                            # the player allows it: its game half goes in
        self.assertEqual((row["approval"], row["addons"]),
                         ("ok", [dict(folder=NAME, version="1.2.0", state="current")]))
        res = self.host.install("fx")
        self.assertEqual((res["id"], res["addons"][0]["state"], res["restart"], res["hint"]),
                         ("fx", "current", False, None))
        self.host.call_sync("fx.echo", {"text": "a"})
        res = self.host.remove("fx")
        self.assertEqual([(a["folder"], a["removed"], a["why"]) for a in res["addons"]], [(NAME, True, None)])
        self.assertEqual(Path(res["kept"]).parent, self.home / HOME.REMOVED)
        self.assertFalse((self.addons / NAME).exists())
        self.assertFalse((self.home / "fx").exists())
        self.assertTrue((folder / "main.py").is_file())
        self.assertEqual(self.ids(), [])
