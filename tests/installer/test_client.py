"""A game update must not leave the program without its client's ## Interface (installer/addons.py client_info): the
number the game itself reported, else the one this release verified, else the one the version gives; the program's addons
are installed with it (a .toc that keeps the old number makes the game list them as out of date and not load them)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.installer import fake
from wuxianworkshop.installer import addons as A


class Interface(unittest.TestCase):
    def test_from_the_version(self):
        self.assertEqual(A.interface_of_version("1.60.1.70245"), 16001)
        self.assertEqual(A.interface_of_version("1.61.0.71000"), 16100)
        self.assertEqual(A.interface_of_version("11.0.2"), 110002)
        self.assertEqual(A.interface_of_version("1.15.7.60000"), 11507)
        self.assertIsNone(A.interface_of_version("1.160.1.70000"))      # does not read like one
        self.assertIsNone(A.interface_of_version(""))
        self.assertIsNone(A.interface_of_version(None))

    def test_a_toc_with_the_clients_number(self):
        toc = b"## Interface: 16001\r\n## Title: X\r\nX.lua\r\n"
        self.assertEqual(A.toc_with_interface(toc, 16100), b"## Interface: 16100\r\n## Title: X\r\nX.lua\r\n")
        self.assertEqual(A.toc_with_interface(b"##Interface:16001, 11507\n", 16100), b"##Interface: 16100\n")
        self.assertEqual(A.toc_with_interface(b"## Title: X\nX.lua\n", 16100), b"## Interface: 16100\n## Title: X\nX.lua\n")
        self.assertEqual(A.toc_with_interface(b"## Interface-Mainline: 1\n## Interface: 2\n", 3), b"## Interface-Mainline: 1\n## Interface: 3\n")
        self.assertEqual(A.toc_with_interface(toc, None), toc)


class Sources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(self.tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)

    def client(self, version):
        return fake.make_client(Path(self.tmp.name) / version, version=version)

    def test_the_game_then_the_verified_table_then_the_version(self):
        game = self.client("1.61.0.71000")
        self.assertEqual(A.client_info(game), dict(version="1.61.0.71000", interface=16100, source="version"))
        self.assertTrue(A.remember_client("1.61.0.71000", 16105))       # what WoWBridge's HELLO said
        self.assertFalse(A.remember_client("1.61.0.71000", 16105))      # known already
        self.assertFalse(A.remember_client(None, 16105))
        self.assertEqual(A.client_info(game), dict(version="1.61.0.71000", interface=16105, source="game"))
        self.assertEqual(A.client_interface(game), ("1.61.0.71000", 16105))
        old = self.client("1.60.1.70235")
        self.assertEqual(A.client_info(old), dict(version="1.60.1.70235", interface=16001, source="verified"))

    def test_no_client(self):
        self.assertEqual(A.client_info(None), dict(version=None, interface=None, source=None))
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(A.client_interface(empty), (None, None))


if __name__ == "__main__":
    unittest.main()
