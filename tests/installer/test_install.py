"""installer/install.py against a fake client in the v0.7 state: clean first, then copy; the mailbox and other people's
folders are never touched; restart_required and state\\install.json."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.installer import fake
from wuxianworkshop import installer
from wuxianworkshop.installer import install as I


class Env:
    """a fake client, a fake addon source and WUXIAN_HOME, all in one temporary folder"""

    def __init__(self, **client):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game = fake.make_client(self.root, **client)
        self.addons = self.game / "Interface" / "AddOns"
        self.source = fake.make_source(self.root)
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(self.root / "home")})
        self.env.start()

    def close(self):
        self.env.stop()
        self.tmp.cleanup()

    def install(self, **kw):
        return I.install(self.game, source=self.source, **kw)


class Install(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)

    def test_clean_install_replaces_the_old_layout(self):
        a = self.e.addons
        r = self.e.install()
        # the two product addons, .lua / .toc / .xml / .tga only
        self.assertEqual((a / "!WuxianWorkshop" / "Core.lua").read_text(encoding="utf-8"), "-- WuxianWorkshop core\n")
        self.assertTrue((a / "!WuxianWorkshop" / "!WuxianWorkshop.toc").is_file())
        self.assertFalse((a / "!WuxianWorkshop" / "README.md").exists())
        self.assertEqual((a / "WoWBridge" / "WoWBridge.lua").read_text(encoding="utf-8"), "-- new WoWBridge.lua\n")
        self.assertTrue((a / "WoWBridge" / "UI.xml").is_file())
        self.assertTrue((a / "WoWBridge" / "Media" / "logo-small.tga").is_file())
        self.assertIn("WoWBridge/Link.lua", r["installed"])
        self.assertIn("!WuxianWorkshop/Core.lua", r["installed"])
        # what was removed: the old early addon, our 31 slot folders, the eight experiment folders
        slots = {f"WoWBridge_S{i:03d}" for i in range(1, 33)} - {f"WoWBridge_S{fake.FOREIGN_SLOT:03d}"}
        self.assertEqual(set(r["removed"]), {"!WoWBridge"} | slots | {f"WoWBridge/{d}" for d in I.EXPERIMENT_DIRS})
        self.assertFalse((a / "!WoWBridge").exists())
        self.assertFalse((a / "WoWBridge" / "ack").exists())
        self.assertFalse((a / "WoWBridge_S001").exists())
        # left alone: the slot folder with someone's file, the other addon
        self.assertTrue((a / f"WoWBridge_S{fake.FOREIGN_SLOT:03d}" / "Notes.txt").is_file())
        self.assertTrue((a / f"WoWBridge_S{fake.FOREIGN_SLOT:03d}" / "Inbox.lua").is_file())
        self.assertEqual((a / "SomeOtherAddon" / "Other.lua").read_text(encoding="utf-8"), "-- other\n")
        # the mailbox: completed to 4,096 slots, the used ones and proc.ttf untouched
        mail = a / "WoWBridge" / "mail"
        self.assertEqual(len(list(mail.glob("*.ttf"))), 4097)
        for i, data in fake.USED_SLOTS.items():
            self.assertEqual((mail / f"{i:05d}.ttf").read_bytes(), data)
        self.assertEqual((mail / "proc.ttf").read_bytes(), b"proc-stamp")
        self.assertEqual((mail / "04096.ttf").read_bytes(), b"")
        self.assertIn("WoWBridge/mail/", r["installed"])
        self.assertTrue(r["restart_required"])
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["addons_dir"], str(a))
        self.assertGreaterEqual(r["new_files"], 4086 + 2 + 1)        # the missing slots, the new addon's files, UI.xml ...
        # the record, under WUXIAN_HOME
        rec = json.loads((self.e.root / "home" / "state" / "install.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["addons_dir"], str(a))
        self.assertEqual(set(rec["removed"]), set(r["removed"]))
        self.assertEqual(rec["installed"], r["installed"])
        self.assertTrue(rec["restart_required"])
        self.assertIsInstance(rec["time"], float)
        self.assertIn("version", rec)

    def test_second_install_needs_no_restart(self):
        self.e.install()
        (self.e.source / "WoWBridge" / "Link.lua").write_text("-- changed\n", encoding="utf-8")
        r = self.e.install()
        self.assertEqual(r["removed"], [])
        self.assertEqual(r["new_files"], 0)
        self.assertFalse(r["restart_required"])                      # a changed .lua only needs a /reload
        self.assertEqual((self.e.addons / "WoWBridge" / "Link.lua").read_text(encoding="utf-8"), "-- changed\n")
        self.assertFalse(json.loads((self.e.root / "home" / "state" / "install.json").read_text(encoding="utf-8"))["restart_required"])

    def test_restart_flags(self):
        """restart_for_link: files the running game does not know; restart_required: also a .toc it read at launch"""
        r = self.e.install()
        self.assertEqual((r["restart_required"], r["restart_for_link"]), (True, True))      # new folders, the mailbox
        toc = self.e.source / "WoWBridge" / "WoWBridge.toc"
        toc.write_text(toc.read_text(encoding="utf-8").replace("## Title: WoWBridge", "## Title: WoWBridge\n## Group: !WuxianWorkshop"),
                       encoding="utf-8")
        r = self.e.install()
        self.assertEqual((r["restart_required"], r["restart_for_link"], r["tocs_changed"]), (True, False, ["WoWBridge/WoWBridge.toc"]))
        rec = json.loads((self.e.root / "home" / "state" / "install.json").read_text(encoding="utf-8"))
        self.assertFalse(rec["restart_for_link"])
        from wuxianworkshop.daemon.companion import installed_at
        self.assertEqual(installed_at(), 0)                      # the running game works on: nothing to wait for
        (self.e.source / "WoWBridge" / "Extra.lua").write_text("-- new\n", encoding="utf-8")
        r = self.e.install()
        self.assertEqual((r["restart_required"], r["restart_for_link"]), (True, True))
        self.assertGreater(installed_at(), 0)

    def test_player_mode(self):
        """player mode: the error collector only; the developer components go (the mailbox with them)"""
        a = self.e.addons
        self.e.install()
        (a / "WoWBridge_Lab").mkdir()                                  # what early development builds left behind
        (a / "WoWBridge_Lab" / "WoWBridge_Lab.toc").write_text("## Title: WoWBridge Lab\n", encoding="utf-8")
        r = self.e.install(mode="player")
        self.assertEqual(r["mode"], "player")
        self.assertTrue((a / "!WuxianWorkshop" / "Core.lua").is_file())
        self.assertFalse((a / "WoWBridge").exists())
        self.assertFalse((a / "WoWBridge_Lab").exists())
        self.assertIn("WoWBridge", r["removed"])
        self.assertIn("WoWBridge_Lab", r["removed"])
        self.assertNotIn("WoWBridge/mail/", r["installed"])
        self.assertTrue((a / "SomeOtherAddon" / "Other.lua").is_file())
        self.assertEqual((r["restart_required"], r["restart_for_link"]), (True, False))  # only removed: nothing new
        r = self.e.install(mode="developer")                          # and back: WoWBridge and a fresh mailbox
        self.assertTrue((a / "WoWBridge" / "mail" / "04096.ttf").is_file())
        self.assertTrue(r["restart_for_link"])
        with self.assertRaises(ValueError):
            self.e.install(mode="tourist")

    def test_player_mode_while_the_game_runs_keeps_wowbridge(self):
        """keep_developer (the game runs): removing WoWBridge changes nothing in the running game, and putting it back
        would be new files to it; the folders wait for an install while the game is closed"""
        self.e.install()
        r = self.e.install(mode="player", keep_developer=True)
        self.assertEqual(r["deferred"], ["WoWBridge"])
        self.assertNotIn("WoWBridge", r["removed"])
        self.assertTrue((self.e.addons / "WoWBridge" / "mail" / "04096.ttf").is_file())
        r = self.e.install(mode="developer")                     # back: nothing new, the link works on
        self.assertEqual((r["restart_for_link"], r["deferred"]), (False, []))

    def test_player_mode_without_clean_keeps_wowbridge(self):
        r = self.e.install(mode="player", clean=False)
        self.assertTrue((self.e.addons / "WoWBridge" / "mail").is_dir())
        self.assertEqual(r["removed"], [])

    def test_no_clean_keeps_the_leftovers(self):
        a = self.e.addons
        r = self.e.install(clean=False)
        self.assertEqual(r["removed"], [])
        self.assertTrue((a / "!WoWBridge" / "Early.lua").is_file())
        self.assertTrue((a / "WoWBridge" / "ack" / "256.wav").is_file())
        self.assertTrue((a / "WoWBridge_S032" / "Inbox.lua").is_file())
        self.assertTrue((a / "!WuxianWorkshop" / "Core.lua").is_file())
        self.assertTrue(r["restart_required"])                       # a new addon folder

    def test_leftovers_and_our_slots(self):
        a = self.e.addons
        left = {p.relative_to(a).as_posix() for p in I.leftovers(a)}
        self.assertIn("!WoWBridge", left)
        self.assertIn("WoWBridge_S001", left)
        self.assertNotIn(f"WoWBridge_S{fake.FOREIGN_SLOT:03d}", left)
        self.assertIn("WoWBridge/mail", {p.relative_to(a).as_posix() for p in (a / "WoWBridge").iterdir()})
        self.assertNotIn("WoWBridge/mail", left)
        self.assertEqual([p.name for p in I.foreign_slots(a)], [f"WoWBridge_S{fake.FOREIGN_SLOT:03d}"])
        empty = a / "WoWBridge_S033"
        empty.mkdir()
        self.assertTrue(I.is_our_slot(empty))
        self.assertNotIn(empty, I.leftovers(a))                      # S033 is outside the range we ever wrote
        (empty / "sub").mkdir()
        self.assertFalse(I.is_our_slot(empty))

    def test_missing_source_or_addons_folder(self):
        with self.assertRaises(FileNotFoundError):
            I.install(self.e.root / "nowhere", source=self.e.source)
        with self.assertRaises(FileNotFoundError):
            I.install(self.e.game, source=self.e.root / "no-addons")

    def test_main(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = I.main(["--game", str(self.e.game), "--source", str(self.e.source)])
        self.assertEqual(code, 0, out.getvalue())
        text = out.getvalue()
        self.assertIn("installed 9 files", text)                     # 2 in !WuxianWorkshop, 7 in WoWBridge (a texture)
        self.assertIn("removed 40 leftover folders", text)
        self.assertIn("restart the game", text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = I.main(["--game", str(self.e.game), "--source", str(self.e.source), "--json"])
        self.assertEqual(code, 0)
        r = json.loads(out.getvalue())
        self.assertFalse(r["restart_required"])
        self.assertEqual(r["removed"], [])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(I.main(["--game", str(self.e.root / "nowhere"), "--source", str(self.e.source)]), 2)
        self.assertIn("not an AddOns folder", err.getvalue())


class SourceDir(unittest.TestCase):
    def test_checkout_and_frozen(self):
        repo = Path(__file__).resolve().parents[2]
        self.assertEqual(installer.addon_source_dir(), repo / "addon")
        self.assertTrue((installer.addon_source_dir() / "WoWBridge" / "WoWBridge.toc").is_file())
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "_MEIPASS", r"C:\app\_internal", create=True):
            self.assertEqual(installer.addon_source_dir(), Path(r"C:\app\_internal") / "addon")


if __name__ == "__main__":
    unittest.main()
