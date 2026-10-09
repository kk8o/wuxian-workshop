"""The 扩展 page's API over a real daemon (/api/extensions): the catalog (WUXIAN_EXTENSIONS_FEED: a local one) with what
the game folder holds of each addon; install, then remove, of an addon of the catalog; the refusals."""
import hashlib
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame, request
from tests.installer.test_extensions import make_catalog, make_zip
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop import extensions
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
        self.assertEqual(extensions.listed_folders(), {"Demo"})        # kept for the agent's tools
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


    def test_removing_wuxiankit_forgets_its_manifest(self):
        """the agents' wk_ tools go with WuxianKit: its kept manifest is forgotten when the App removes it"""
        from wuxianworkshop.mcp import kit

        site = Path(os.environ["WUXIAN_EXTENSIONS_FEED"]).parent
        before = (site / "extensions.json").read_text(encoding="utf-8")
        raw = make_zip(folder="WuxianKit", version="0.4.0")
        (site / "WuxianKit-0.4.0.zip").write_bytes(raw)
        (site / "extensions.json").write_text(json.dumps({"format": 1, "addons": [{
            "id": "wuxiankit", "folder": "WuxianKit", "version": "0.4.0", "file": "WuxianKit-0.4.0.zip",
            "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "min_app": "0.1.0"}]}), encoding="utf-8")
        try:
            request(self.handle, "GET", "/api/extensions?refresh=1")
            request(self.handle, "POST", "/api/extensions", {"action": "install", "id": "wuxiankit"})
            self.assertTrue(kit.save_cached(kit.cache_path(), {"revision": "r1", "extensions": []}))
            request(self.handle, "POST", "/api/extensions", {"action": "remove", "id": "wuxiankit"})
            self.assertFalse(kit.cache_path().exists())
        finally:
            (site / "extensions.json").write_text(before, encoding="utf-8")
            request(self.handle, "GET", "/api/extensions?refresh=1")


if __name__ == "__main__":
    unittest.main()
