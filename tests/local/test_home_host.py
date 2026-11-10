"""The host and the extensions' home folder (host.py with home.py), with real processes of the fixture: a folder found
there and one that went, who allows an extension that was not written here (ask), a removal that keeps the folder and
one that cannot move it, an extension that needs a newer workshop, the ids WuxianKit has, and the two entries a
package's installation uses (register, reset)."""
import os
import re
import shutil
from unittest import mock

from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import adds as A
from wuxianworkshop.local import home as HOME
from wuxianworkshop.local import host as H
from wuxianworkshop.local import manifest as M

from .util import HostCase, gone, make_extension, until

TESTS = {"test_a.py": "import unittest\nclass T(unittest.TestCase):\n    def test_no(self):\n        self.fail('ran')\n"}


class HomeCase(HostCase):
    def setUp(self):
        super().setUp()
        self.home = self.host.home
        self.home.mkdir(parents=True, exist_ok=True)

    def put(self, name="fx", eid=None, **fields):
        """an extension folder right in the home folder, not registered"""
        return make_extension(self.home / name, eid or name, **fields)

    def look(self):
        return self.host.discover(force=True)

    def ids(self):
        return [e["id"] for e in self.host.load()["extensions"]]

    def said(self, words):
        return [n for n in self.notes if words in n]

    def refused(self, eid="fx"):
        """the ApiError a start of it is refused with now"""
        error = self.host.ensure(eid).exception(5)
        self.assertIsInstance(error, ApiError, error)
        return error


class Discovery(HomeCase):
    def test_the_home_folder(self):
        self.assertEqual(self.home, self.root / "home" / "extensions")           # paths.extensions_home()
        self.assertEqual(self.host.listing()["home"], str(self.home))
        there = self.root / "elsewhere"
        other = H.Host(self.root / "state2", home=there, taken=lambda: ("a", 7), kit_version=lambda: "",
                       addons_dir=lambda: "C:/AddOns", interface=lambda: 120000)
        self.addCleanup(other.close)
        self.assertEqual(other.listing(), dict(revision=0, extensions=[], problem=None, home=str(there)))
        self.assertEqual((other.taken_ids(), other.kit(), other.addons_dir(), other.interface()),
                         (("a",), None, "C:/AddOns", 120000))
        self.assertEqual((self.host.taken_ids(), self.host.kit(), self.host.addons_dir(), self.host.interface()),
                         ((), None, None, None))

        def broken():
            raise RuntimeError("the game is not there")
        other.taken, other.kit_version = broken, broken                          # cannot be asked now: none, not known
        self.assertEqual((other.taken_ids(), other.kit()), ((), None))

    def test_a_folder_found_is_registered_and_waits_for_the_player(self):
        for name in ("WuxianFx", "WuxianFxData"):                                # the game addons it brings
            (self.home / "fx" / M.ADDONS_DIR / name).mkdir(parents=True)
            (self.home / "fx" / M.ADDONS_DIR / name / f"{name}.toc").write_text("## Interface: 120000", encoding="utf-8")
        folder = self.put(author="某人", homepage="https://example.com/fx", addons=["WuxianFx", "WuxianFxData"])
        told = len(self.revisions)
        (row,) = self.host.listing()["extensions"]
        self.assertEqual((row["id"], row["path"], row["origin"], row["enabled"], row["approval"], row["allowed"]),
                         ("fx", str(folder), "home", True, "needed", False))
        self.assertEqual((row["author"], row["homepage"], row["requires"], row["unmet"]),
                         ("某人", "https://example.com/fx", {}, []))
        self.assertEqual(row["addons"], [dict(folder="WuxianFx", version=None, state="unknown"),
                                         dict(folder="WuxianFxData", version=None, state="unknown")])
        self.assertEqual(self.host.entry("fx"), dict(id="fx", path=str(folder), enabled=True, options={}, approved=None,
                                                     described=None, ask=True))
        (note,) = self.said("found in the extensions folder")
        self.assertIn("local extension fx: found in the extensions folder, registered", note)
        self.assertIn("the player approves it", note)
        self.assertGreater(len(self.revisions), told)
        told = len(self.revisions)
        self.assertFalse(self.look())                                            # the same folder again: nothing
        self.assertEqual((len(self.said("found in the extensions folder")), len(self.revisions)), (1, told))
        # the setting lets the workshop approve commands by itself, but not this one's: it was not written here
        self.assertTrue(self.host.auto())
        error = self.refused()
        self.assertEqual((error.status, error.code, error.extra), (403, "needs_approval", dict(
            approval="needed", player=dict(action="approve", text="请在无限工坊 App 的「扩展」页批准「测试 fx」"))))
        self.assertEqual(row["player"], error.extra["player"])
        self.assertIn("extension_wait waits for them", error.message)
        self.assertIn("the player has not confirmed the command it starts with", error.message)
        self.assertIn("it came in a package or was found in the extensions folder", error.message)
        for run in (lambda: self.host.call_sync("fx.echo", {"text": "a"}), lambda: self.host.check("fx"),
                    lambda: self.host.load_("fx")):
            with self.assertRaises(ApiError) as e:
                run()
            self.assertEqual(e.exception.code, "needs_approval")
        self.assertFalse(A.allowed(self.host, self.host.entry("fx")))
        self.assertEqual(self.host.declarations(), [])
        self.assertEqual((self.pids(), self.host.get("fx")["approval"]), ([], "needed"))
        row = self.host.approve("fx")                                            # the player, in the App
        self.assertEqual((row["approval"], row["allowed"]), ("ok", True))
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "a"})["text"], "a")
        self.assertIs(self.host.entry("fx")["ask"], True)                        # it stays one that asks:
        self.host.stop("fx")
        make_extension(folder, "fx", mode="another")                             # what it runs changed
        self.assertEqual(self.host.get("fx")["approval"], "changed")
        error = self.refused()
        self.assertEqual((error.code, error.extra), ("needs_approval", dict(approval="changed", player=dict(
            action="approve", text="「测试 fx」要运行的内容变了，请在无限工坊 App 的「扩展」页重新批准"))))
        self.assertIn("extension_wait", error.message)
        self.assertIn("changed the command it starts with since the player confirmed it", error.message)
        self.assertFalse(self.host.get("fx")["allowed"])

    def test_ask_under_both_settings(self):
        self.put("found")
        self.assertTrue(self.look())
        self.add("mine")                                                         # registered by its path: written here
        self.assertEqual({e["id"]: e.get("ask") for e in self.host.load()["extensions"]}, dict(found=True, mine=None))
        self.assertEqual({r["id"]: r["origin"] for r in self.host.rows()}, dict(found="home", mine="linked"))
        self.auto_now = False                                                    # the player is asked: for both
        self.assertEqual({r["id"]: (r["approval"], r["allowed"]) for r in self.host.rows()},
                         dict(found=("needed", False), mine=("needed", False)))
        for eid in ("found", "mine"):
            error = self.refused(eid)
            self.assertEqual(error.code, "needs_approval")
            self.assertNotIn("it came in a package", error.message)              # nothing special about it there
        self.auto_now = True                                                     # the workshop approves: only its own
        self.assertEqual({r["id"]: (r["approval"], r["allowed"]) for r in self.host.rows()},
                         dict(found=("needed", False), mine=("needed", True)))
        self.assertEqual(self.refused("found").code, "needs_approval")
        self.assertEqual(self.host.call_sync("mine.echo", {"text": "a"})["text"], "a")
        self.assertEqual(self.host.entry("mine")["approved"]["by"], "developer")
        self.assertIsNone(self.host.entry("found")["approved"])
        self.assertEqual(self.pids("found"), [])

    def test_looked_at_no_more_than_every_two_seconds(self):
        self.assertEqual(H.DISCOVER_EVERY, 2.0)
        self.assertEqual(self.host.listing()["extensions"], [])                  # looked
        self.put("a")
        self.assertEqual(self.host.listing()["extensions"], [])                  # a moment later: not again
        self.assertFalse(self.host.discover())
        self.host.looked -= H.DISCOVER_EVERY + 0.1                               # two seconds later
        self.assertEqual([r["id"] for r in self.host.listing()["extensions"]], ["a"])
        self.put("b")
        self.assertFalse(self.host.discover())
        self.assertTrue(self.look())                                             # force: now
        self.assertEqual(self.ids(), ["a", "b"])
        self.assertFalse(self.look())
        self.host.close()
        self.put("c")
        self.assertFalse(self.look())                                            # a host that is closing looks no more
        self.assertEqual(self.ids(), ["a", "b"])

    def test_what_is_not_registered(self):
        self.put(".hidden", eid="hidden")                                        # a dot first: the workshop's own
        (self.home / "plain").mkdir()                                            # no manifest
        (self.home / "plain" / "main.py").write_text("", encoding="utf-8")
        make_extension(self.home / HOME.REMOVED / "old-20260101-000000", "old")
        make_extension(self.home / "deep" / "inner", "inner")                    # not right in the home folder
        bad = self.put("bad")
        (bad / M.MANIFEST).write_text('{"id": "Bad"}', encoding="utf-8")
        self.assertFalse(self.look())
        self.assertEqual((self.ids(), self.revisions), ([], []))
        (note,) = self.said("is not registered")
        self.assertIn("local extensions: bad in the extensions folder is not registered: ", note)
        self.assertIn("'Bad' is not an extension id", note)
        self.assertFalse(self.look())
        self.assertEqual(len(self.said("is not registered")), 1)                 # said once
        (bad / M.MANIFEST).write_text('{"id": 5}', encoding="utf-8")
        self.assertFalse(self.look())
        self.assertEqual(len(self.said("is not registered")), 2)                 # another reason: said again
        self.assertIn("5 is not an extension id", self.notes[-1])
        make_extension(bad, "bad")                                               # repaired
        self.assertTrue(self.look())
        self.assertEqual(self.ids(), ["bad"])
        self.assertEqual((len(self.said("is not registered")), self.host.passed), (2, {}))

    def test_an_id_that_is_taken(self):
        _, mine = self.add("fx")                                                 # registered by its path
        self.put("copy", eid="fx")
        self.host.taken = lambda: ("guild",)                                     # one of WuxianKit's in this game
        self.put("guild")
        self.assertFalse(self.look())
        self.assertFalse(self.look())
        self.assertEqual([(e["id"], e["path"]) for e in self.host.load()["extensions"]], [("fx", str(mine))])
        notes = self.said("is not registered")
        self.assertEqual(len(notes), 2)
        self.assertIn(f"copy in the extensions folder is not registered: the id fx is taken by {mine}", notes[0])
        self.assertIn("guild in the extensions folder is not registered: ", notes[1])
        self.assertIn("'guild' is taken", notes[1])
        self.host.taken = lambda: ()                                             # WuxianKit lost that one
        self.host.remove("fx")
        self.assertTrue(self.look())
        self.assertEqual({e["id"]: e["path"] for e in self.host.load()["extensions"]},
                         dict(fx=str(self.home / "copy"), guild=str(self.home / "guild")))

    def test_a_registered_one_keeps_its_id(self):
        """what WuxianKit lists only counts for a folder that is not registered yet (add, check, discover)"""
        row, folder = self.add("fx")
        found = self.put("seen")
        self.assertTrue(self.look())
        self.host.taken = lambda: ("fx", "seen", "guild")
        for eid in ("fx", "seen"):
            row = self.host.get(eid)
            self.assertEqual((row["error"], row["command"] is not None), (None, True), row)
        self.assertEqual(self.host.add(str(folder))["id"], "fx")                 # registered already: its row
        self.assertFalse(self.look())
        self.assertEqual(self.ids(), ["fx", "seen"])
        res = self.host.check("fx")
        self.assertTrue(res["ok"], res)
        new = make_extension(self.root / "ext" / "guild", "guild")
        with self.assertRaises(ApiError) as e:
            self.host.add(str(new))
        self.assertEqual((e.exception.status, e.exception.code), (400, "bad_manifest"))
        self.assertIn("'guild' is taken", e.exception.message)
        res = self.host.check(str(new))
        self.assertFalse(res["ok"])
        self.assertTrue(any(p["where"].endswith("id") and "'guild' is taken" in p["message"] for p in res["problems"]),
                        res["problems"])
        self.assertEqual((res["capabilities"], self.ids()), ([], ["fx", "seen"]))

    def test_a_folder_that_is_gone(self):
        folder = self.put()
        self.assertTrue(self.look())
        self.host.approve("fx")
        self.host.call_sync("fx.echo", {"text": "a"})
        pid = self.host.get("fx")["pid"]
        self.host.stop("fx")                                                     # (a running one holds its folder)
        self.assertTrue(gone(pid))
        self.assertTrue(until(lambda: self.moved(folder, self.root / "away")))
        told = len(self.revisions)
        with mock.patch.object(self.host, "stop", wraps=self.host.stop) as stop:
            self.assertTrue(self.look())
        stop.assert_called_once_with("fx", quiet=True)                           # stopped before it is forgotten
        self.assertEqual(self.ids(), [])
        self.assertGreater(len(self.revisions), told)
        (note,) = self.said("no longer registered")
        self.assertIn(f"local extension fx: no longer registered ({folder}; its folder in the extensions folder is "
                      f"gone)", note)
        with self.assertRaises(ApiError) as e:
            self.host.get("fx")
        self.assertEqual(e.exception.code, "not_found")
        self.assertFalse(self.look())
        shutil.move(str(self.root / "away"), str(folder))                        # back: found as a new one
        self.assertTrue(self.look())
        e = self.host.entry("fx")
        self.assertEqual((e["ask"], e["approved"], e["described"]), (True, None, None))

    @staticmethod
    def moved(a, b):
        try:
            os.rename(a, b)
        except OSError:
            return False
        return True

    def test_what_is_not_taken_for_gone(self):
        _, linked = self.add("lk")                                               # registered by its path
        self.put("fx")
        self.assertTrue(self.look())
        shutil.move(str(linked), str(self.root / "away"))
        self.assertFalse(self.look())                                            # the workshop never drops a linked one
        self.assertEqual(self.ids(), ["lk", "fx"])
        self.assertIsNotNone(self.host.get("lk")["error"])
        os.rename(self.home, self.root / "home" / "extensions.away")             # the home folder itself is not there
        self.assertFalse(self.look())
        self.assertEqual(self.ids(), ["lk", "fx"])                               # (says nothing of what was in it)
        os.rename(self.root / "home" / "extensions.away", self.home)
        self.assertFalse(self.look())
        self.assertEqual((self.ids(), self.said("no longer registered")), (["lk", "fx"], []))

    def test_autostart_looks_first(self):
        self.put("ws", start="workshop")
        self.host.autostart()
        self.assertEqual(self.ids(), ["ws"])
        (note,) = self.said("not started with the workshop")
        self.assertIn("the player has not confirmed the command it starts with", note)
        row = self.host.get("ws")
        self.assertEqual((row["pid"], row["approval"], row["origin"]), (None, "needed", "home"))
        self.assertNotIn(row["state"], ("starting", "running"))
        self.host.approve("ws")                                                  # allowed: it starts for good
        self.assertTrue(until(lambda: self.host.get("ws")["state"] == "running", 30))


class Removal(HomeCase):
    def test_a_home_folder_is_kept_in_removed(self):
        folder = self.put()
        self.assertTrue(self.look())
        self.host.approve("fx")
        self.host.call_sync("fx.echo", {"text": "a"})
        pid = self.host.get("fx")["pid"]
        res = self.host.remove("fx")                                             # stopped, moved, forgotten
        kept = res.pop("kept")
        self.assertEqual(res, dict(removed=True, id="fx", path=str(folder), addons=[]))
        self.assertTrue(gone(pid))
        self.assertEqual(os.path.dirname(kept), str(self.home / HOME.REMOVED))
        self.assertTrue(re.fullmatch(r"fx-\d{8}-\d{6}", os.path.basename(kept)), kept)
        self.assertTrue(os.path.isfile(os.path.join(kept, "main.py")))
        self.assertTrue(os.path.isfile(os.path.join(kept, M.MANIFEST)))
        self.assertFalse(folder.exists())
        self.assertEqual(self.ids(), [])
        (note,) = self.said("no longer registered")
        self.assertIn(f"its folder is kept as {kept}", note)
        self.assertFalse(self.look())                                            # .removed holds no extension
        self.assertEqual((self.ids(), self.said("gone")), ([], []))
        with self.assertRaises(ApiError) as e:
            self.host.remove("fx")
        self.assertEqual(e.exception.code, "not_found")

    def test_a_linked_folder_stays(self):
        _, folder = self.add("lk")
        self.host.call_sync("lk.echo", {"text": "a"})
        self.assertEqual(self.host.remove("lk"), dict(removed=True, id="lk", path=str(folder), kept=None,
                                                      addons=[]))
        self.assertTrue((folder / "main.py").is_file())
        self.assertFalse((self.home / HOME.REMOVED).exists())
        self.put("went")
        self.assertTrue(self.look())
        shutil.rmtree(self.home / "went")                                        # gone already: nothing to keep
        self.assertEqual(self.host.remove("went"),
                         dict(removed=True, id="went", path=str(self.home / "went"), kept=None, addons=[]))
        self.assertEqual(self.ids(), [])

    def test_only_the_newest_twenty_stay(self):
        removed = self.home / HOME.REMOVED
        for day in range(1, 23):
            (removed / f"old-202601{day:02d}-000000").mkdir(parents=True)
        self.put()
        self.assertTrue(self.look())
        kept = self.host.remove("fx")["kept"]
        names = sorted(p.name for p in removed.iterdir())
        self.assertEqual(names, sorted([f"old-202601{day:02d}-000000" for day in range(4, 23)]
                                       + [os.path.basename(kept)]))
        self.assertEqual(len(names), 20)

    @mock.patch.object(HOME, "MOVE_WAITS", ())
    def test_folder_busy(self):
        folder = self.put()
        self.assertTrue(self.look())
        self.host.approve("fx")
        self.host.call_sync("fx.echo", {"text": "a"})
        pid = self.host.get("fx")["pid"]
        told = len(self.revisions)
        with mock.patch.object(HOME.os, "rename", side_effect=PermissionError(13, "Access is denied", str(folder))):
            with self.assertRaises(ApiError) as e:
                self.host.remove("fx")
        error = e.exception
        self.assertEqual((error.status, error.code, error.extra), (409, "folder_busy", dict(path=str(folder))))
        self.assertIn("fx is stopped, but its folder cannot be moved away now (PermissionError", error.message)
        self.assertIn("it is still registered", error.message)
        self.assertTrue(gone(pid))                                               # stopped it is
        row = self.host.get("fx")                                                # and registered, as it was
        self.assertEqual((row["state"], row["approval"], row["origin"], row["path"]),
                         ("stopped", "ok", "home", str(folder)))
        self.assertTrue((folder / "main.py").is_file())
        self.assertEqual(list((self.home / HOME.REMOVED).iterdir()), [])
        self.assertEqual(self.said("no longer registered"), [])
        self.assertFalse(self.look())
        self.assertEqual(self.ids(), ["fx"])
        if os.name == "nt":                                                      # the real thing: a file of it is open
            with open(folder / "main.py", "rb"):
                with self.assertRaises(ApiError) as e:
                    self.host.remove("fx")
            self.assertEqual(e.exception.code, "folder_busy")
            self.assertEqual(self.ids(), ["fx"])
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "b"})["text"], "b")     # it still works
        res = self.host.remove("fx")                                             # closed: now it goes
        self.assertTrue(os.path.isfile(os.path.join(res["kept"], "main.py")))
        self.assertFalse(folder.exists())
        self.assertGreater(len(self.revisions), told)


class Requires(HomeCase):
    def test_a_workshop_that_is_too_old(self):
        folder = make_extension(self.root / "ext" / "old", "old", tests=TESTS, requires={"workshop": "99.0"})
        row = self.host.add(str(folder))
        short = dict(what="workshop", needs="99.0", has="9.9.9")
        self.assertEqual((row["requires"], row["unmet"], row["state"]), ({"workshop": "99.0"}, [short], "stopped"))
        self.assertIn("old needs WuxianWorkshop 99.0 or newer, this is 9.9.9", row["error"])
        self.assertEqual(row["why"], row["error"])
        for _ in range(3):                                                       # refused each time, never backed off
            error = self.host.ensure("old").exception(5)
            self.assertIsInstance(error, ApiError)
            self.assertEqual((error.status, error.code, error.extra), (409, "requires_unmet", dict(unmet=[short])))
            self.assertIn("update the workshop", error.message)
        for run in (lambda: self.host.call_sync("old.echo", {"text": "a"}), lambda: self.host.load_("old")):
            with self.assertRaises(ApiError) as e:
                run()
            self.assertEqual(e.exception.code, "requires_unmet")
        res = self.host.check("old")
        self.assertFalse(res["ok"])
        self.assertEqual([(p["level"], p["where"]) for p in res["problems"]], [("error", f"{M.MANIFEST}: requires")])
        self.assertIn("needs WuxianWorkshop 99.0", res["problems"][0]["message"])
        self.assertNotIn("tests", res)                                           # not even its tests run
        self.assertEqual((res["capabilities"], self.pids("old")), ([], []))
        row = self.host.get("old")
        self.assertEqual((row["state"], row["pid"]), ("stopped", None))
        self.assertIsNone(self.host.entry("old")["described"])
        make_extension(folder, "old", requires={"workshop": "9.9"})              # this workshop is new enough
        row = self.host.get("old")
        self.assertEqual((row["unmet"], row["error"]), ([], None))
        self.assertEqual(self.host.call_sync("old.echo", {"text": "a"})["text"], "a")

    def test_started_with_the_workshop(self):
        row, _ = self.add("ws", start="workshop", requires={"workshop": "10"})
        self.assertEqual((row["state"], row["pid"]), ("stopped", None))
        self.host.autostart()
        (note,) = self.said("not started with the workshop")
        self.assertIn("ws needs WuxianWorkshop 10 or newer, this is 9.9.9", note)
        self.assertEqual((self.host.get("ws")["state"], self.pids("ws")), ("stopped", []))

    def test_the_kit_it_needs_is_only_told(self):
        self.host.kit_version = lambda: "0.6.0"
        row, _ = self.add("kt", requires={"kit": "9.0", "workshop": "9.9.9"})
        self.assertEqual((row["requires"], row["unmet"], row["error"]),
                         ({"kit": "9.0", "workshop": "9.9.9"}, [dict(what="kit", needs="9.0", has="0.6.0")], None))
        self.assertIsNone(self.host.unmet_error(M.check_manifest(row["path"])[0]))
        self.assertEqual(self.host.call_sync("kt.echo", {"text": "a"})["text"], "a")     # it runs all the same
        self.host.kit_version = lambda: None                                     # not known now: not judged
        self.assertEqual(self.host.get("kt")["unmet"], [])
        self.host.kit_version = lambda: "9.0.1"
        self.assertEqual(self.host.get("kt")["unmet"], [])


class Entries(HomeCase):
    """what installs a package: register (a new folder), reset (a folder replaced by another version)"""

    def test_register(self):
        folder = make_extension(self.root / "ext" / "pk", "pk", start="workshop")
        m, _ = M.check_manifest(folder, taken=self.host.taken_ids())
        e = self.host.register(folder, m, ask=True, how="imported from a package")
        self.assertEqual(e, dict(id="pk", path=str(folder), enabled=True, options={}, approved=None, described=None,
                                 ask=True))
        self.assertEqual(self.host.entry("pk"), e)
        (note,) = self.said("local extension pk: imported from a package")
        self.assertIn(str(folder), note)
        self.assertIn("the player approves it", note)
        self.assertEqual((len(self.revisions), self.pids("pk")), (1, []))        # told; nothing started, start or not
        self.assertEqual(self.host.get("pk")["state"], "stopped")
        self.assertEqual(self.host.register(str(folder), m), e)                  # registered already: as it is
        self.assertEqual(self.host.register(folder, m, ask=True), e)
        self.assertEqual((len(self.said("local extension pk: ")), len(self.revisions)), (1, 1))
        other = make_extension(self.root / "ext" / "pk2", "pk")
        with self.assertRaises(ApiError) as refused:
            self.host.register(other, M.check_manifest(other)[0], ask=True)
        self.assertEqual((refused.exception.status, refused.exception.code), (409, "extension_exists"))
        mine = make_extension(self.root / "ext" / "mine", "mine")
        e = self.host.register(mine, M.check_manifest(mine)[0])
        self.assertNotIn("ask", e)
        (note,) = self.said("local extension mine: registered")
        self.assertNotIn("the player allows it", note)
        self.assertIs(self.host.register(mine, M.check_manifest(mine)[0], ask=True)["ask"], True)    # now it asks
        self.assertIs(self.host.entry("mine")["ask"], True)

    def test_reset(self):
        row, folder = self.add("pk")
        self.host.call_sync("pk.opts")                                           # approved by the workshop, described
        self.host.set("pk", options={"volume": 80})
        self.host.stop("pk")
        self.host.set("pk", enabled=False)
        before = self.host.entry("pk")
        self.assertEqual((before["approved"]["by"], before["described"] is not None, before["options"],
                          before.get("ask")), ("developer", True, {"volume": 80}, None))
        told = len(self.revisions)
        e = self.host.reset("pk")
        self.assertEqual(e, dict(id="pk", path=str(folder), enabled=False, options={"volume": 80}, approved=None,
                                 described=None, ask=True))
        self.assertEqual(self.host.entry("pk"), e)
        self.assertGreater(len(self.revisions), told)
        (note,) = self.said("replaced by another version")
        self.assertIn("local extension pk: ", note)
        row = self.host.set("pk", enabled=True)
        self.assertEqual((row["approval"], row["allowed"], row["capabilities"], row["error"]),
                         ("needed", False, [], None))
        self.assertEqual(self.refused("pk").code, "needs_approval")              # the workshop does not allow it again
        self.host.approve("pk")
        self.assertEqual(self.host.call_sync("pk.opts"), {"volume": 80})         # the player did: as it was set
        self.host.stop("pk")
        elsewhere = make_extension(self.root / "ext" / "pk-new", "pk")
        e = self.host.reset("pk", folder=str(elsewhere))
        self.assertEqual((e["path"], e["approved"], e["described"], e["options"], e["enabled"], e["ask"]),
                         (str(elsewhere), None, None, {"volume": 80}, True, True))
        self.assertEqual(self.host.get("pk")["path"], str(elsewhere))
        with self.assertRaises(ApiError) as refused:
            self.host.reset("ghost")
        self.assertEqual(refused.exception.code, "not_found")

    def test_a_folder_replaced_under_the_lock(self):
        """the order reset()'s text gives a package's installation: stop, then the lock held from before the old folder
        goes until reset returns; a look at the home folder meanwhile waits and takes nothing for gone"""
        import threading
        folder = self.put()
        self.assertTrue(self.look())
        self.host.approve("fx")
        self.host.call_sync("fx.echo", {"text": "a"})
        new = make_extension(HOME.incoming(self.home, "fx") / "fx", "fx", mode="v2")
        self.host.stop("fx", quiet=True)
        looked = []
        with self.host.lock:
            kept = HOME.trash(folder, self.home, "fx")
            looker = threading.Thread(target=lambda: looked.append(self.look()))
            looker.start()                                                       # (waits for the lock)
            looker.join(0.3)
            self.assertTrue(looker.is_alive())
            os.rename(new, folder)
            e = self.host.reset("fx")
        looker.join(10)
        self.assertEqual(looked, [False])
        self.assertEqual(self.ids(), ["fx"])
        self.assertEqual((e["path"], e["ask"], e["approved"], e["described"]), (str(folder), True, None, None))
        self.assertEqual(self.said("no longer registered"), [])
        self.assertTrue((kept / "main.py").is_file())
        self.assertEqual(self.refused().code, "needs_approval")
        self.host.approve("fx")
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "b"})["text"], "b")


if __name__ == "__main__":
    unittest.main()
