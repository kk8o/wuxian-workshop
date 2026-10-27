"""Where the client is looked for when the game is not running: 《魔兽世界：无限》 moved from the beta's _cn_beta_ to
_forever_ on 2026-10-27, and the launcher left _cn_beta_ behind with Wow.exe and logs only"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop.core import game
from wuxianworkshop.transport import capture


class TheList(unittest.TestCase):
    def test_the_new_client_comes_first(self):
        self.assertTrue(game.DEFAULT_GAME_DIRS[0].endswith("_forever_"))


class DefaultFolders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.beta, self.forever = root / "_cn_beta_", root / "_forever_"
        for d in (self.beta, self.forever):
            d.mkdir()
            (d / "Wow.exe").write_bytes(b"")
        env = {k: v for k, v in os.environ.items() if k != "WUXIAN_MUTEX"}
        for p in (mock.patch.dict(os.environ, env, clear=True), mock.patch.object(capture, "find_window", return_value=None),
                  mock.patch.object(game, "DEFAULT_GAME_DIRS", [str(self.beta), str(self.forever)])):
            p.start()
            self.addCleanup(p.stop)

    def test_an_empty_beta_folder_is_passed_over(self):
        with self.assertRaises(SystemExit):                     # neither holds a client yet
            game.game_dir()
        (self.forever / ".flavor.info").write_text("Product Flavor!STRING:0\nwowf\n", encoding="utf-8")
        self.assertEqual(game.game_dir(), self.forever)          # the beta's folder, though listed first, is a shell
        self.assertEqual(game.read_flavor(self.forever), "wowf")
        (self.beta / "Interface" / "AddOns").mkdir(parents=True)
        self.assertEqual(game.game_dir(), self.beta)             # a client again (the beta installed once more)


if __name__ == "__main__":
    unittest.main()
