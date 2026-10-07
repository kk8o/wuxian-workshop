"""/api/update over a real daemon (daemon/server.py), with a fake Velopack UpdateManager behind updater.Updater: check,
download, the state in /api/status, and apply, which stops the daemon so that Velopack can swap the files."""
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame, request
from tests.installer.test_updater import Manager
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

        def manager(source):
            m = Manager(source, newer="0.9.0")
            self.managers.append(m)
            return m
        made = mock.patch.object(service, "Updater", lambda note=None: updater.Updater(source="x", manager=manager, note=note))
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

    def test_nothing_to_download_or_apply(self):
        for body, status in (({"action": "download"}, 409), ({"action": "apply"}, 409), ({"action": "fly"}, 400)):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post(body)
            self.assertEqual(cm.exception.code, status, body)
            cm.exception.close()


if __name__ == "__main__":
    unittest.main()
