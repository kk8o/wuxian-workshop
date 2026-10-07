"""The shell (wuxianworkshop/ui/shell.py) without opening a window: the WebView2 gate, the arguments, the fake backend,
and the closing / quit sequence against a stand-in window."""
import json
import sys
import unittest
import urllib.request
from unittest.mock import patch

from wuxianworkshop.ui import shell, webview2


class StandInWindow:
    def __init__(self):
        self.calls = []

    def hide(self):
        self.calls.append("hide")

    def show(self):
        self.calls.append("show")

    def restore(self):
        self.calls.append("restore")

    def destroy(self):
        self.calls.append("destroy")

    def evaluate_js(self, script):
        self.calls.append(("js", script))


class StandInBackend:
    def __init__(self):
        self.stopped = False
        self.url = "http://127.0.0.1:1/"

    def stop(self):
        self.stopped = True


class WithoutWebView2(unittest.TestCase):
    def test_declined_install_exits(self):
        with patch.object(webview2, "missing", return_value=["webview2"]), \
                patch.object(webview2, "ensure", return_value=False) as ensure, \
                patch.object(shell, "start_backend") as backend:
            self.assertEqual(shell.run_app([]), shell.EXIT_NO_WEBVIEW2)
        ensure.assert_called_once_with(["webview2"])
        backend.assert_not_called()

    def test_installed_then_the_window_opens(self):
        with patch.object(webview2, "missing", return_value=["dotnet", "webview2"]), \
                patch.object(webview2, "ensure", return_value=True), \
                patch.object(shell, "running_instance", return_value=None), \
                patch.object(shell, "start_backend", return_value=(StandInBackend(), "")), \
                patch.object(shell.Shell, "run", autospec=True, return_value=0) as run:
            self.assertEqual(shell.run_app([]), 0)
        run.assert_called_once()


class Arguments(unittest.TestCase):
    def test_defaults(self):
        ns = shell.parse_args([])
        self.assertEqual((ns.mode, ns.capture, ns.game, ns.fake, ns.debug, ns.background),
                         (None, None, None, None, False, False))          # None: what the 设置 page saved
        self.assertTrue(shell.parse_args(["--background"]).background)

    def test_fake(self):
        self.assertEqual(shell.parse_args(["--fake"]).fake, "auto")
        self.assertEqual(shell.parse_args(["--fake", "http://127.0.0.1:8765/"]).fake, "http://127.0.0.1:8765/")
        self.assertEqual(shell.parse_args(["--mode", "player", "--capture", "gdi"]).mode, "player")

    def test_remote_backend(self):
        remote = shell.RemoteBackend("http://127.0.0.1:8765")
        self.assertEqual((remote.url, remote.port), ("http://127.0.0.1:8765/", 8765))
        remote.stop()


class FakeBackend(unittest.TestCase):
    def test_fake_api_in_this_process(self):
        handle = shell.start_fake()
        try:
            self.assertTrue(handle.url.startswith("http://127.0.0.1:"))
            with urllib.request.urlopen(handle.url + "api/session", timeout=5) as resp:
                self.assertEqual(json.load(resp)["token"], handle.token)
        finally:
            handle.stop()

    def test_fake_url_is_remote(self):
        self.assertIsInstance(shell.start_fake("http://127.0.0.1:8765/"), shell.RemoteBackend)

    def test_no_daemon_json_means_no_running_instance(self):
        with patch("wuxianworkshop.daemon.api.read_daemon_json", return_value=None):
            self.assertIsNone(shell.running_instance())
        self.assertIsNone(shell.running_instance({"port": 1, "token": "x"}))      # nothing listens on port 1

    def test_already_running_instance_is_shown(self):
        info = {"port": 4321, "token": "t"}
        with patch.object(webview2, "missing", return_value=[]), \
                patch.object(shell, "start_backend", side_effect=shell.AlreadyRunning(info)), \
                patch.object(shell, "running_instance", return_value=(info, True)) as found:
            self.assertEqual(shell.run_app(["--fake"]), 0)
        found.assert_called_once_with(info)


class RunningDaemon(unittest.TestCase):
    """a daemon is already running when the program starts"""

    info = {"port": 4321, "token": "t", "url": "http://127.0.0.1:4321/"}

    def run_app(self, argv, found):
        with patch.object(webview2, "missing", return_value=[]), \
                patch.object(shell, "running_instance", return_value=found), \
                patch.object(shell, "start_backend") as start, \
                patch.object(shell.Shell, "run", autospec=True, return_value=7) as run:
            code = shell.run_app(argv)
        return code, start, run

    def test_its_window_was_shown(self):
        code, start, run = self.run_app([], (self.info, True))
        self.assertEqual(code, 0)
        start.assert_not_called()
        run.assert_not_called()

    def test_without_a_window_this_one_shows_its_page(self):
        """`wuxian serve` runs: double-clicking the program must still bring up a window, on that daemon's page"""
        code, start, run = self.run_app([], (self.info, False))
        self.assertEqual(code, 7)
        start.assert_not_called()
        backend = run.call_args[0][0].backend
        self.assertIsInstance(backend, shell.AttachedBackend)
        self.assertEqual((backend.url, backend.port, backend.token), ("http://127.0.0.1:4321/", 4321, "t"))

    def test_background_start_has_nothing_to_do(self):
        for shown in (True, False):
            code, start, run = self.run_app(["--background"], (self.info, shown))
            self.assertEqual(code, 0)
            run.assert_not_called()

    def test_background_start_without_webview2_runs_the_daemon_alone(self):
        with patch.object(webview2, "missing", return_value=["webview2"]), \
                patch.object(webview2, "ensure") as ensure, \
                patch.object(shell, "run_headless", return_value=0) as headless:
            self.assertEqual(shell.run_app(["--background", "--capture", "gdi"]), 0)
        ensure.assert_not_called()                                   # no dialog for a client's start
        self.assertEqual(headless.call_args[0][0].capture, "gdi")

    def test_attached_backend_against_the_fake_api(self):
        handle = shell.start_fake()
        try:
            info = {"port": handle.port, "token": handle.token}
            self.assertEqual(shell.running_instance(info), (info, True))         # the fake says its window showed
            attached = shell.AttachedBackend(info)
            self.assertTrue(attached.alive())
            attached.stop()                                                     # 退出: POST /api/quit
            self.assertTrue(any(e["text"] == "quit requested" for e in list(handle.fake.logs)))
            self.assertFalse(shell.AttachedBackend({"port": 1, "token": "x"}).alive())
        finally:
            handle.stop()

    def test_window_closes_when_the_attached_daemon_stops(self):
        app = shell.Shell(StandInBackend(), shell.parse_args([]))
        app.window = StandInWindow()
        app.backend.alive = lambda: False
        app.watch_backend(every=0.01)
        self.assertTrue(app.quitting)
        self.assertEqual(app.window.calls, ["destroy"])


class ClosingAndQuitting(unittest.TestCase):
    def setUp(self):
        self.app = shell.Shell(StandInBackend(), shell.parse_args([]))
        self.app.window = StandInWindow()

    def test_closing_hides_until_quit(self):
        self.assertIs(self.app.on_closing(), False)                    # cancel the close ...
        self.assertEqual(self.app.window.calls, ["hide"])              # ... and hide instead
        self.app.quit()                                                # the tray's 退出: flag, then destroy
        self.assertTrue(self.app.quitting)
        self.assertEqual(self.app.window.calls[-1], "destroy")
        self.assertIs(self.app.on_closing(), True)                     # now the window may really close
        self.app.quit()                                                # a second 退出 does nothing more
        self.assertEqual(self.app.window.calls.count("destroy"), 1)

    def test_shutdown_stops_backend(self):
        self.app.shutdown()
        self.assertTrue(self.app.backend.stopped)
        self.assertTrue(self.app.quitting)

    def test_tray_callbacks(self):
        self.app.show()
        self.app.open_settings()
        calls = self.app.window.calls
        self.assertEqual(calls[:2], ["show", "restore"])
        self.assertEqual(calls[-1], ("js", "window.wx && wx.go('settings')"))


if __name__ == "__main__":
    unittest.main()
