"""An isolated test instance (WUXIAN_MUTEX set, core.game.isolated) never looks for the game: find_window does not ask
Windows for the game window, game_dir neither for the running game's folder nor for a default one; a folder given is
still taken. Without WUXIAN_MUTEX both look as before. The daemon's side is tests/daemon/test_server.py IsolatedInstance."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop.core import game
from wuxianworkshop.transport import capture


def environ(mutex):
    """os.environ with WUXIAN_MUTEX set to `mutex`, or without it (None)"""
    env = {k: v for k, v in os.environ.items() if k != "WUXIAN_MUTEX"}
    if mutex:
        env["WUXIAN_MUTEX"] = mutex
    return mock.patch.dict(os.environ, env, clear=True)


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = Path(self.tmp.name) / "client"           # stands for the player's client folder: the default one
        (self.client / "Interface" / "AddOns").mkdir(parents=True)
        self.defaults = mock.patch.object(game, "DEFAULT_GAME_DIRS", [str(self.client)])
        self.defaults.start()

    def tearDown(self):
        self.defaults.stop()
        self.tmp.cleanup()

    def test_game_dir(self):
        with environ(r"Local\WuxianWorkshopIsolated"), mock.patch.object(capture, "find_window", side_effect=AssertionError):
            self.assertTrue(game.isolated())
            with self.assertRaises(SystemExit) as cm:              # neither the running game's folder nor the default
                game.game_dir()
            self.assertIn("WUXIAN_MUTEX", str(cm.exception))
            with self.assertRaises(SystemExit):
                game.addons_dir()
            self.assertEqual(game.game_dir(self.tmp.name), Path(self.tmp.name))      # one given is taken
        with environ(None), mock.patch.object(capture, "find_window", return_value=None):
            self.assertFalse(game.isolated())
            self.assertEqual(game.game_dir(), self.client)         # what isolation keeps a test instance from

    def test_find_window(self):
        with mock.patch.object(capture, "user32") as user32:
            with environ(r"Local\WuxianWorkshopIsolated"):
                self.assertIsNone(capture.find_window())
            user32.EnumWindows.assert_not_called()                 # Windows is not even asked
            user32.EnumWindows.return_value = 1
            with environ(None):
                self.assertIsNone(capture.find_window())          # no window listed: none found
            user32.EnumWindows.assert_called_once()


if __name__ == "__main__":
    unittest.main()
