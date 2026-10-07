"""The addons follow the program (daemon/service.py sync_addons): another version in the game folder is replaced by the
program's own copy while the game is not running; while it runs, status.addons_update says what waits."""
import asyncio
import os
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from wuxianworkshop import __version__
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.installer.addons import read_toc


class Worker:
    """the loop as the sync sees it: the AddOns folder, the game window (None: not running), request()"""

    def __init__(self, addons, **kw):
        self.addons, self.win, self.comp, self.installed = addons, None, None, 0
        self.capture = self.capture_wanted = "gdi"

    def request(self, fn, *args):
        fut = Future()
        try:
            fut.set_result(fn(*args))
        except BaseException as e:
            fut.set_exception(e)
        return fut

    def game_status(self):
        return dict(found=self.win is not None)

    def stop(self):
        pass


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(self.tmp.name) / "home")})
        self.env.start()
        self.addons = Path(self.tmp.name) / "client" / "Interface" / "AddOns"
        old = self.addons / "WoWBridge"
        old.mkdir(parents=True)
        (old / "WoWBridge.toc").write_text("## Interface: 16001\n## Version: 0.7.0\nWoWBridge.lua\n", encoding="utf-8")
        self.journal = Journal()
        self.service = Service(lambda **kw: Worker(self.addons, **kw), journal=self.journal, sync_addons=True)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def version(self, name):
        toc = read_toc(self.addons / name / f"{name}.toc")
        return toc.get("Version") if toc else None

    def texts(self):
        return [e["text"] for e in self.journal.since(0, 1000)[0]]

    def test_waits_while_the_game_runs_then_installs(self):
        self.assertEqual(self.service.addons_behind(), [dict(name="!WuxianWorkshop", version=None, why="version"),
                                                        dict(name="WoWBridge", version="0.7.0", why="version")])
        self.service.worker.win = object()                                   # the game runs
        self.service.sync_addons()
        self.assertEqual(self.version("WoWBridge"), "0.7.0")                 # left alone
        pending = self.service.addons_pending
        self.assertEqual((pending["version"], pending["waiting"]), (__version__, "game"))
        self.assertEqual([(a["name"], a["version"]) for a in pending["addons"]], [("!WuxianWorkshop", None), ("WoWBridge", "0.7.0")])
        self.service.sync_addons()                                           # said once
        self.assertEqual(sum("once the game is closed" in t for t in self.texts()), 1)
        status = asyncio.run(self.service.status())
        self.assertEqual(status["addons_update"]["waiting"], "game")
        self.service.worker.win = None                                       # closed: installed now
        self.service.sync_addons()
        self.assertEqual((self.version("WoWBridge"), self.version("!WuxianWorkshop")), (__version__, __version__))
        self.assertIsNone(self.service.addons_pending)
        self.assertTrue(any(f"WoWBridge 0.7.0 -> {__version__}: installed" in t for t in self.texts()), self.texts())
        self.assertTrue((self.addons / "WoWBridge" / "mail" / "proc.ttf").exists())
        self.assertGreater(self.service.worker.installed, 0)                 # new files: a game started now knows them
        self.service.sync_addons()                                           # up to date: nothing more
        self.assertEqual(sum(": installed (" in t for t in self.texts()), 1)

    def test_a_game_update_changes_the_interface(self):
        """the program's version is in the game folder, but the launcher updated the client to 1.61.0: the addons get its
        Interface before the game starts again (else the game lists them as out of date and the link is gone)"""
        client = self.addons.parent.parent
        (client / ".build.info").write_text("Branch!STRING:0|Active!DEC:1|Version!STRING:0|Product!STRING:0\n"
                                            "cn|1|1.61.0.70500|wow_cn_beta\n", encoding="utf-8")
        self.service.worker.win = None
        self.service.sync_addons()                                           # 0.7.0 -> the program's, with 16100
        self.assertEqual(read_toc(self.addons / "WoWBridge" / "WoWBridge.toc")["Interface"], "16100")
        toc = self.addons / "WoWBridge" / "WoWBridge.toc"                    # as an older install left it
        toc.write_text(toc.read_text(encoding="utf-8").replace("## Interface: 16100", "## Interface: 16001"), encoding="utf-8")
        self.assertEqual(self.service.addons_behind(), [dict(name="WoWBridge", version=__version__, why="interface",
                                                             interface="16001", want_interface=16100)])
        self.service.worker.win = object()                                   # the updated game runs already
        self.service.sync_addons()
        self.assertEqual(self.service.addons_pending["addons"][0]["why"], "interface")
        self.assertTrue(any("WoWBridge Interface 16001 -> 16100 (the game was updated) once the game is closed" in t
                            for t in self.texts()), self.texts())
        self.service.worker.win = None
        self.service.sync_addons()
        self.assertEqual(read_toc(toc)["Interface"], "16100")
        self.assertEqual(self.service.addons_behind(), [])

    def test_only_the_real_daemon_syncs(self):
        self.assertFalse(Service(lambda **kw: Worker(self.addons, **kw), journal=Journal()).sync_addons_on)


if __name__ == "__main__":
    unittest.main()
