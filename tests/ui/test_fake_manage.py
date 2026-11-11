"""The fake daemon's managing of local extensions (scripts/dev_fake_api.py), in the shapes of extensions-home.md (sections
4 and 7): the rows' new keys and the three pretend extensions that show them (herald brings game addons, relay is linked
and needs newer versions, greeter came from a package), and POST /api/local/add, new, remove, pack and install with the
refusals a name in the path (or an id) asks for. Nothing is unpacked, written or moved."""
import re

from tests.ui.test_fake_api import AUTH, F
from tests.ui.test_fake_local import ROW, Local

HOME = F.EXT_HOME


class Rows(Local):
    def test_the_listing_names_the_home(self):
        d = self.client.get("/api/local", headers=AUTH).json()
        self.assertEqual(set(d), {"revision", "extensions", "problem", "home"})
        self.assertEqual((d["home"], d["problem"]), (HOME, None))

    def test_the_new_keys(self):
        rows = self.rows()
        for row in rows.values():
            self.assertIn(row["origin"], ("home", "linked"), row["id"])
            self.assertEqual(row["origin"] == "home", row["path"] == HOME + "\\" + row["id"], row["id"])
            self.assertTrue(row["author"] is None or isinstance(row["author"], str))
            self.assertTrue(row["homepage"] is None or isinstance(row["homepage"], str))
            self.assertLessEqual(set(row["requires"]), {"workshop", "kit"})
            for u in row["unmet"]:
                self.assertEqual(set(u), {"what", "needs", "has"})
                self.assertIn(u["what"], ("workshop", "kit"))
            for a in row["addons"]:
                self.assertEqual(set(a), {"folder", "version", "state"})
                self.assertIn(a["state"], ("unknown", "broken", "missing", "current", "differs", "foreign"))
        self.assertEqual((rows["voice"]["author"], rows["voice"]["homepage"]), ("Someone", "https://example.org/voice"))
        self.assertEqual((rows["notes"]["author"], rows["notes"]["homepage"], rows["notes"]["addons"]), (None, None, []))

    def test_the_three_that_show_them(self):
        rows = self.rows()
        herald = rows["herald"]                                          # in the home, one to install, one to update
        self.assertEqual((herald["origin"], herald["approval"]), ("home", "ok"))
        self.assertEqual([(a["folder"], a["state"]) for a in herald["addons"]],
                         [("WuxianHerald", "missing"), ("WuxianHeraldSkin", "differs")])
        relay = rows["relay"]                                            # linked, needs newer versions
        self.assertEqual((relay["origin"], relay["path"]), ("linked", "D:\\Projects\\relay"))
        self.assertEqual(relay["requires"], {"workshop": "0.9.14", "kit": "0.7"})
        self.assertEqual([u["what"] for u in relay["unmet"]], ["workshop", "kit"])
        self.assertIn("0.9.14", relay["error"])
        greeter = rows["greeter"]                                        # from a package: it waits
        self.assertEqual((greeter["origin"], greeter["approval"], greeter["allowed"]), ("home", "needed", False))
        self.assertEqual([a["folder"] for a in greeter["addons"]], ["WuxianGreeter"])

    def test_one_that_needs_a_newer_workshop_does_not_start(self):
        r = self.post("load", id="relay")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "requires_unmet"))
        self.assertIn("0.9.14", r.json()["error"]["message"])
        self.assertEqual(self.rows()["relay"]["state"], "stopped")


class Add(Local):
    def test_a_folder_is_linked(self):
        r = self.post("add", path="D:\\Work\\mine\\wuxian-extension.json")
        self.assertEqual(r.status_code, 200)
        row = r.json()
        self.assertEqual(set(row), ROW)                                  # no package: it was not one
        self.assertEqual((row["id"], row["origin"], row["path"], row["approval"]), ("mine", "linked", "D:\\Work\\mine", "needed"))
        self.assertEqual(self.post("add", path="D:\\Work\\mine").json()["id"], "mine")           # the same folder: the row
        r = self.post("add", path="E:\\Other\\mine")                     # the id is taken
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "extension_exists"))

    def test_a_package_goes_into_the_home_and_waits(self):
        self.client.post("/api/settings", headers=AUTH, json={"extension_approval": "auto"})
        r = self.post("add", path="D:\\Downloads\\gamehelper-1.zip")
        self.assertEqual(r.status_code, 200)
        row = r.json()
        self.assertEqual(set(row), ROW | {"package"})
        self.assertEqual(row["package"], {"file": "D:\\Downloads\\gamehelper-1.zip", "replaced": None})
        self.assertEqual((row["origin"], row["path"]), ("home", HOME + "\\" + row["id"]))
        self.assertEqual((row["approval"], row["allowed"]), ("needed", False))                    # whatever the setting
        self.assertEqual([a["state"] for a in row["addons"]], ["missing"])                        # "game" in its name
        self.assertEqual(self.post("load", id=row["id"]).status_code, 403)
        self.assertEqual(self.post("approve", id=row["id"]).json()["approval"], "ok")
        self.assertEqual([a["state"] for a in self.rows()[row["id"]]["addons"]], ["current"])     # the yes installs them

    def test_a_package_again_asks_first(self):
        """the id is in the home already: 409 needs_replace with the two summaries and what differs, nothing changed;
        with replace true it takes the old one's place and waits for the player"""
        self.post("set", id="herald", options={})
        before = self.rows()["herald"]
        r = self.post("add", path="D:\\Downloads\\herald.zip")
        d = r.json()
        self.assertEqual((r.status_code, d["error"]["code"]), (409, "needs_replace"))
        self.assertEqual((d["old"]["from"], d["old"]["version"], d["new"]["from"], d["new"]["version"], d["same"]),
                         ("folder", before["version"], "package", "0.3.0", False))
        self.assertEqual(d["new"]["package"]["file"], "herald.zip")
        self.assertIn({"field": "version", "old": before["version"], "new": "0.3.0"}, d["changes"])
        self.assertEqual(d["error"]["changes"], d["changes"])                # as ApiError.body gives it
        self.assertEqual(self.rows()["herald"], before)                      # nothing was touched
        self.assertEqual(self.post("add", path="D:\\Downloads\\herald.zip", replace=False).status_code, 409)
        r = self.post("add", path="D:\\Downloads\\herald.zip", replace=True)
        self.assertEqual(r.status_code, 200)
        row = r.json()
        self.assertEqual((row["id"], row["version"], row["approval"]), ("herald", "0.3.0", "needed"))
        self.assertTrue(row["package"]["replaced"].startswith(HOME + "\\.removed\\herald-"))
        self.assertEqual(sum(1 for i in self.rows() if i == "herald"), 1)
        self.assertEqual(set(self.rows()["herald"]), ROW)                    # where it came from is the summary's to say
        self.assertEqual(self.post("summary", id="herald").json()["package"]["file"], "herald.zip")

    def test_the_very_package_again(self):
        self.assertEqual(self.post("add", path="D:\\Downloads\\same.zip").status_code, 200)
        d = self.post("add", path="D:\\Downloads\\same.zip").json()
        self.assertEqual((d["error"]["code"], d["changes"], d["same"]), ("needs_replace", [], True))
        self.assertEqual(d["old"]["package"]["sha256"], d["new"]["package"]["sha256"])

    def test_over_one_that_cannot_be_read(self):
        d = self.post("add", path="D:\\Downloads\\draft.zip").json()
        self.assertEqual((d["error"]["code"], d["old"], d["changes"], d["same"]), ("needs_replace", None, None, None))
        self.assertEqual(d["new"]["id"], "draft")
        r = self.post("add", path="D:\\Downloads\\relay.zip")           # a linked one has that id
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "extension_exists"))

    def test_what_a_name_asks_for(self):
        for path, status, code in (("D:\\Downloads\\bad.zip", 400, "bad_package"),
                                   ("D:\\Downloads\\big.zip", 413, "package_too_big"),
                                   ("D:\\Work\\bad", 400, "bad_manifest"),
                                   ("D:\\Work\\exists", 409, "extension_exists"),
                                   ("D:\\Downloads\\exists.zip", 409, "extension_exists"),
                                   ("mine", 400, "bad_request"), ("", 400, "bad_request")):
            r = self.post("add", path=path)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (status, code), path)
            self.assertTrue(r.json()["error"]["message"], path)
        row = self.post("add", path="D:\\Work\\needs").json()            # a newer WuxianKit: it shows, nothing refuses
        self.assertEqual(row["unmet"], [{"what": "kit", "needs": "0.7", "has": "0.6.0"}])
        row = self.post("add", path="D:\\Work\\foreign").json()
        self.assertEqual([a["state"] for a in row["addons"]], ["foreign"])


class New(Local):
    def test_in_the_home(self):
        r = self.post("new", id="mine", title="我的")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(set(d), {"row", "files", "guide"})              # as Service.new_extension answers
        row = d["row"]
        self.assertEqual(set(row), ROW)
        self.assertEqual((row["id"], row["title"], row["origin"], row["path"], row["addons"]),
                         ("mine", "我的", "home", HOME + "\\mine", []))
        self.assertIn(HOME + "\\mine\\wuxian-extension.json", d["files"])
        self.assertIn("mine", self.rows())

    def test_with_a_game_addon(self):
        d = self.post("new", id="mine", addon="WuxianMine").json()
        self.assertEqual(d["row"]["addons"], [{"folder": "WuxianMine", "version": "0.1.0", "state": "missing"}])
        self.assertIn(HOME + "\\mine\\addons\\WuxianMine\\WuxianMine.toc", d["files"])
        self.assertEqual(d["row"]["title"], "mine")                      # no title: the id

    def test_elsewhere(self):
        row = self.post("new", id="mine", folder="D:\\Work\\mine").json()["row"]
        self.assertEqual((row["origin"], row["path"]), ("linked", "D:\\Work\\mine"))

    def test_refusals(self):
        for body, status, code in ((dict(), 400, "bad_request"), (dict(id="My Tool"), 400, "bad_request"),
                                   (dict(id="mine", folder="mine"), 400, "bad_request"),
                                   (dict(id="voice"), 409, "extension_exists"),
                                   (dict(id="it_exists"), 409, "extension_exists"),
                                   (dict(id="mine", addon="My Addon"), 400, "bad_addon"),
                                   (dict(id="mine", addon="WuxianKit"), 400, "bad_addon"),
                                   (dict(id="mine", addon="Blizzard_Chat"), 400, "bad_addon")):
            r = self.post("new", **body)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (status, code), body)
        self.assertNotIn("mine", self.rows())


class Remove(Local):
    def test_one_in_the_home_is_kept(self):
        self.post("install", id="herald")
        r = self.post("remove", id="herald")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(set(d), {"removed", "id", "path", "kept", "addons"})
        self.assertEqual((d["removed"], d["id"], d["path"]), (True, "herald", HOME + "\\herald"))
        self.assertTrue(re.fullmatch(re.escape(HOME + "\\.removed\\herald-") + r"\d{8}-\d{6}", d["kept"]))
        self.assertEqual([(a["folder"], a["removed"]) for a in d["addons"]],
                         [("WuxianHerald", True), ("WuxianHeraldSkin", True)])
        for a in d["addons"]:
            self.assertIsInstance(a["kept"], int)
        self.assertNotIn("herald", self.rows())
        self.assertEqual(self.post("remove", id="herald").status_code, 404)

    def test_an_addon_never_installed_is_not_removed(self):
        d = self.post("remove", id="greeter").json()
        self.assertEqual(d["addons"], [{"folder": "WuxianGreeter", "removed": False, "kept": None}])

    def test_a_linked_one_keeps_its_folder(self):
        d = self.post("remove", id="relay").json()
        self.assertEqual((d["path"], d["kept"], d["addons"]), ("D:\\Projects\\relay", None, []))

    def test_a_folder_in_use(self):
        self.post("new", id="so_busy")
        r = self.post("remove", id="so_busy")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "folder_busy"))
        self.assertIn("so_busy", self.rows())                            # it stays
        self.post("new", id="link_busy", folder="D:\\Work\\link_busy")
        self.assertEqual(self.post("remove", id="link_busy").status_code, 200)                   # nothing to move


class Pack(Local):
    def test_into_the_packages_folder(self):
        r = self.post("pack", id="herald")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(set(d), {"id", "version", "file", "size", "files", "sha256", "skipped", "summary", "warnings"})
        s = d["summary"]                                                 # what whoever gets it will see
        self.assertEqual((s["from"], s["id"], s["package"]), ("package", "herald", {"sha256": d["sha256"],
                                                                                    "file": "herald-0.2.0.zip"}))
        self.assertEqual((d["warnings"], d["files"], d["size"]), ([], s["files"], s["size"]))
        w = self.post("pack", id="stats").json()["warnings"]             # its manifest's env sets a value
        self.assertEqual(len(w), 1)
        self.assertIn("env sets STATS_MODE", w[0])
        self.assertEqual((d["id"], d["version"], d["file"]), ("herald", "0.2.0", F.PACKAGES + "\\herald-0.2.0.zip"))
        self.assertIsInstance(d["files"], int)
        self.assertIsInstance(d["size"], int)
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", d["sha256"]))
        self.assertTrue(all(isinstance(s, str) for s in d["skipped"]))
        self.assertIn("herald", self.rows())                             # nothing of the rows changed

    def test_to(self):
        self.assertEqual(self.post("pack", id="voice", to="D:\\Out\\").json()["file"], "D:\\Out\\voice-0.3.2.zip")
        self.assertEqual(self.post("pack", id="voice", to="D:\\Out\\v.zip").json()["file"], "D:\\Out\\v.zip")
        r = self.post("pack", id="voice", to="Out")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "bad_request"))

    def test_refusals(self):
        r = self.post("pack", id="draft")                                # its manifest cannot be read
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "bad_manifest"))
        self.assertEqual(self.post("pack", id="nope").status_code, 404)


class Install(Local):
    def test_the_answer(self):
        before = self.fake.local_revision
        r = self.post("install", id="herald")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(set(d), {"id", "addons", "restart", "hint"})
        self.assertEqual([(a["folder"], a["state"], a["was"]) for a in d["addons"]],
                         [("WuxianHerald", "installed", "missing"), ("WuxianHeraldSkin", "installed", "differs")])
        for a in d["addons"]:
            self.assertEqual(set(a), {"folder", "state", "was", "version", "kept"})
        self.assertEqual((d["addons"][0]["kept"], d["restart"]), (None, True))                    # a new one: a restart
        self.assertIsInstance(d["addons"][1]["kept"], int)
        self.assertIn("WuxianHerald", d["hint"])
        self.assertGreater(self.fake.local_revision, before)             # the page reads the rows again
        self.assertEqual({a["state"] for a in self.rows()["herald"]["addons"]}, {"current"})
        d = self.post("install", id="herald").json()                     # again: nothing to do
        self.assertEqual(({a["state"] for a in d["addons"]}, d["restart"]), ({"current"}, False))

    def test_refusals(self):
        for eid, status, code in (("greeter", 403, "needs_approval"), ("voice", 400, "no_addons"), ("nope", 404, "not_found")):
            r = self.post("install", id=eid)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (status, code), eid)
        row = self.post("add", path="D:\\Work\\foreign").json()
        self.post("approve", id=row["id"])
        self.assertEqual([a["state"] for a in self.rows()[row["id"]]["addons"]], ["foreign"])     # the yes did not fail
        r = self.post("install", id=row["id"])
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "addon_exists"))

    def test_a_start_installs_them_first(self):
        r = self.post("load", id="herald")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(set(d), {"row", "check", "addons", "restart", "hint"})
        self.assertEqual((d["restart"], {a["state"] for a in d["row"]["addons"]}), (True, {"current"}))
        self.assertEqual(set(self.post("load", id="herald").json()), {"row", "check"})            # nothing left to do

    def test_the_yes_installs_them(self):
        row = self.post("approve", id="greeter").json()
        self.assertEqual((row["approval"], [a["state"] for a in row["addons"]]), ("ok", ["current"]))


class NoGameFolder(Local):
    def setUp(self):
        super().setUp()
        self.fake.addons_known = False

    def test_nothing_is_known_of_the_game(self):
        self.assertEqual({a["state"] for a in self.rows()["herald"]["addons"]}, {"unknown"})
        r = self.post("install", id="herald")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (503, "no_game_folder"))
        self.assertEqual(set(self.post("load", id="herald").json()), {"row", "check"})            # it starts all the same
        self.assertEqual(self.post("remove", id="herald").json()["addons"], [])


class Summary(Local):
    KEYS = {"from", "id", "title", "version", "notes", "author", "homepage", "package", "where", "risk", "game",
            "addons_dir", "local", "executables", "agent", "files", "size"}

    def get(self, **q):
        return self.client.get("/api/local/summary", headers=AUTH, params=q)

    def test_of_a_row(self):
        s = self.get(id="voice").json()
        self.assertEqual(set(s), self.KEYS)
        self.assertEqual((s["from"], s["id"], s["risk"], s["package"]), ("folder", "voice", "local", None))
        self.assertEqual(set(s["local"]), {"run", "python", "start", "env", "pass"})
        self.assertEqual((s["local"]["pass"], s["executables"]), (["DEEPSEEK_API_KEY"], ["bin/tts.exe"]))
        self.assertEqual(set(s["agent"]), {"docs", "skills", "apps", "adds"})
        self.assertEqual(self.post("summary", id="voice").json(), s)                             # by POST as well
        self.assertEqual(self.get(path=self.rows()["voice"]["path"]).json(), s)                  # by its folder

    def test_one_with_game_addons(self):
        s = self.get(id="herald").json()
        self.assertEqual([(g["folder"], g["path"] is not None) for g in s["game"]],
                         [("WuxianHerald", False), ("WuxianHeraldSkin", True)])                  # one is not there yet
        self.assertEqual(s["game"][1]["needs"], [{"addon": "EllesmereUI", "installed": True}])
        for g in s["game"]:
            self.assertEqual(set(g), {"folder", "title", "notes", "version", "author", "needs", "path"})
        for app in s["agent"]["adds"]:
            self.assertEqual(set(app), {"app", "wake", "skill"})

    def test_one_in_the_game_only(self):
        row = self.post("add", path="D:\\Downloads\\ingame.zip").json()
        s = self.get(id=row["id"]).json()
        self.assertEqual((s["where"], s["risk"], s["local"]), ("game", "game", None))

    def test_of_a_package_that_is_not_here(self):
        before = self.rows()
        s = self.get(path="D:\\Downloads\\gamehelper-1.zip").json()
        self.assertEqual((s["from"], s["package"]["file"], s["version"]), ("package", "gamehelper-1.zip", "0.1.0"))
        self.assertEqual(self.rows(), before)                                                    # nothing is added

    def test_refusals(self):
        for q, status, code in (({}, 400, "bad_request"), ({"id": "nope"}, 404, "not_found"),
                                ({"id": "draft"}, 400, "bad_manifest"), ({"path": "D:\\Work\\nothing"}, 404, "not_found"),
                                ({"path": "D:\\Downloads\\bad.zip"}, 400, "bad_package"), ({"path": "x"}, 400, "bad_request")):
            r = self.get(**q)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (status, code), q)


del Local                                                            # its tests run in test_fake_local


if __name__ == "__main__":
    import unittest
    unittest.main()
