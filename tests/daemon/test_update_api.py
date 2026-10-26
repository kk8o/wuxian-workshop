"""/api/update over a real daemon (daemon/server.py), with a fake Velopack UpdateManager behind updater.Updater and a
signed feed (tests/installer/test_updater.py Site): check, download, the state in /api/status, and apply, which stops the
daemon so that Velopack can swap the files; a package that is not the signed one is not applied."""
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame, request
from tests.installer import signed
from tests.installer.test_updater import FEED, Manager, Site
from wuxianworkshop import updater
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server, service


class UpdateApi(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)
        addons = Path(tmp.name) / "AddOns"
        (addons / "WoWBridge").mkdir(parents=True)
        MB.install(addons, pool=16)
        self.managers = []
        self.site = Site(Path(tmp.name) / "site")
        self.site.publish("0.9.0")
        keys = signed.keys()
        keys.start()
        self.addCleanup(keys.stop)

        def manager(source):
            m = Manager(source, site=self.site, newer="0.9.0")
            self.managers.append(m)
            return m
        made = mock.patch.object(service, "Updater", lambda note=None: updater.Updater(
            source=FEED, manager=manager, note=note, fetcher=self.site.fetch, root=self.site.install,
            store=Path(tmp.name) / "update.json"))
        made.start()
        self.addCleanup(made.stop)
        self.handle = server.start(worker_factory=lambda on_debug, log: FakeGame(addons, on_debug=on_debug, log=log),
                                   mutex_name=MUTEX + "U")
        self.addCleanup(self.handle.stop)

    def post(self, body):
        return request(self.handle, "POST", "/api/update", body)

    def test_check_download_apply(self):
        s = request(self.handle, "GET", "/api/update")
        self.assertEqual((s["supported"], s["state"], s["current"]), (True, "idle", "0.8.1"))
        s = self.post({"action": "check"})
        self.assertEqual((s["state"], s["latest"]), ("available", "0.9.0"))
        self.assertEqual(request(self.handle, "GET", "/api/status")["update"]["state"], "available")
        self.post({"action": "download"})
        deadline = time.time() + 5
        while request(self.handle, "GET", "/api/update")["state"] != "ready" and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(request(self.handle, "GET", "/api/update")["progress"], 100)
        s = self.post({"action": "apply"})
        self.assertTrue(s["restarting"])
        self.assertTrue(self.handle.wait(10), "the daemon stops for Velopack")
        self.assertEqual(self.managers[0].applied, ("0.9.0", False, True))

    def test_a_package_that_is_not_the_signed_one_is_not_applied(self):
        self.post({"action": "check"})
        self.post({"action": "download"})
        deadline = time.time() + 5
        while request(self.handle, "GET", "/api/update")["state"] != "ready" and time.time() < deadline:
            time.sleep(0.05)
        (self.site.install / "packages" / "WuxianWorkshop.App-0.9.0-full.nupkg").write_bytes(b"replaced")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post({"action": "apply"})
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("更新包未通过校验", cm.exception.read().decode("utf-8"))
        cm.exception.close()
        self.assertIsNone(self.managers[0].applied)
        self.assertEqual(request(self.handle, "GET", "/api/update")["state"], "error")
        self.assertFalse(self.handle.wait(0.5), "the daemon keeps running")

    def test_nothing_to_download_or_apply(self):
        for body, status in (({"action": "download"}, 409), ({"action": "apply"}, 409), ({"action": "fly"}, 400)):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post(body)
            self.assertEqual(cm.exception.code, status, body)
            cm.exception.close()


if __name__ == "__main__":
    unittest.main()
