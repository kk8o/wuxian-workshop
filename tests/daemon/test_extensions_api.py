"""The 扩展 page's API over a real daemon (/api/extensions): the catalog (WUXIAN_EXTENSIONS_FEED: a local one) with what
the game folder holds of each addon; install, then remove, of an addon of the catalog; the refusals."""
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame, request
from tests.installer.test_extensions import make_catalog, make_zip
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server


class ExtensionsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        site = Path(cls.tmp.name) / "site"
        site.mkdir()
        cls.entry = make_catalog(site, make_zip())
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(cls.tmp.name) / "home"),
                                               "WUXIAN_EXTENSIONS_FEED": str(site / "extensions.json")})
        cls.env.start()
        cls.addons = Path(cls.tmp.name) / "client" / "Interface" / "AddOns"
        (cls.addons / "WoWBridge").mkdir(parents=True)
        MB.install(cls.addons, pool=64)
        cls.handle = server.start(worker_factory=lambda on_debug, log: FakeGame(cls.addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "X")

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def refused(self, body):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            request(self.handle, "POST", "/api/extensions", body)
        cm.exception.close()
        return cm.exception.code

    def test_list_install_and_remove(self):
        listing = request(self.handle, "GET", "/api/extensions?refresh=1")
        self.assertEqual(listing["catalog"]["error"], "")
        self.assertEqual(listing["addons_dir"], str(self.addons))
        demo = listing["addons"][0]
        self.assertEqual((demo["id"], demo["state"], demo["there"], demo["installed"]), ("demo", "available", False, None))
        done = request(self.handle, "POST", "/api/extensions", {"action": "install", "id": "demo"})
        self.assertEqual((done["result"]["version"], done["result"]["restart"]), ("1.0.0", True))
        self.assertEqual((done["addon"]["state"], done["addon"]["installed"]), ("current", "1.0.0"))
        self.assertTrue((self.addons / "Demo" / "Core.lua").is_file())
        gone = request(self.handle, "POST", "/api/extensions", {"action": "remove", "id": "demo"})
        self.assertEqual((gone["result"]["removed"], gone["addon"]["state"]), (True, "available"))
        self.assertFalse((self.addons / "Demo").exists())
        self.assertEqual(self.refused({"action": "install", "id": "nope"}), 404)
        self.assertEqual(self.refused({"action": "zap", "id": "demo"}), 400)


if __name__ == "__main__":
    unittest.main()
