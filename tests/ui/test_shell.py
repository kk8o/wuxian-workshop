"""The shell (wuxianworkshop/ui/shell.py) without opening a window: the WebView2 gate, the arguments, the fake backend,
and the closing / quit sequence against a stand-in window."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import call, patch

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


class Clock:
    """the shell's time in a test: sleep() moves it on at once"""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


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
        found.assert_called_once_with(info, show=True)


class RunningDaemon(unittest.TestCase):
    """a daemon is already running when the program starts"""

    info = {"port": 4321, "token": "t", "url": "http://127.0.0.1:4321/"}

    def run_app(self, argv, found):
        with patch.object(webview2, "missing", return_value=[]), \
                patch.object(shell, "running_instance", return_value=found) as self.look, \
                patch.object(shell, "start_backend") as start, \
                patch.object(shell.Shell, "run", autospec=True, return_value=7) as run:
            code = shell.run_app(argv)
        return code, start, run

    def test_its_window_was_shown(self):
        code, start, run = self.run_app([], (self.info, True))
        self.assertEqual(code, 0)
        start.assert_not_called()
        run.assert_not_called()
        self.look.assert_called_once_with(show=True)

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
            self.look.assert_called_once_with(show=False)            # not asked to show: its window may be hidden

    def test_background_start_does_not_ask_for_the_window(self):
        """a --background start only asks whether the daemon answers (GET /api/status): a POST /api/show would bring up
        the window of a program that a client started with --background a moment earlier"""
        handle = shell.start_fake()
        try:
            info = {"port": handle.port, "token": handle.token}

            def shows():
                return [e for e in list(handle.fake.logs) if e["text"].startswith("show:")]
            self.assertEqual(shell.running_instance(info, show=False), (info, False))
            self.assertEqual(shows(), [])
            self.assertEqual(shell.running_instance(info), (info, True))
            self.assertEqual(len(shows()), 1)
            self.assertIsNone(shell.running_instance({"port": 1, "token": "x"}, show=False))   # nothing listens
        finally:
            handle.stop()

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


class LostTheRace(unittest.TestCase):
    """another start took the mutex a moment earlier and answers only once it listens (2026-10-08: `wuxian status`
    started `app --background` half a second after the program; it asked once, too early, and stayed as a hidden error
    page that kept files in dist open after `wuxian quit`)"""

    info = {"port": 4321, "token": "t", "url": "http://127.0.0.1:4321/"}

    def run_app(self, argv, answers, error=None):
        """answers: running_instance()'s, the first one for the look before start_backend"""
        clock = Clock()
        with patch.object(webview2, "missing", return_value=[]), \
                patch.object(shell, "time", clock), \
                patch.object(shell, "start_backend", side_effect=error or shell.AlreadyRunning(None)), \
                patch.object(shell, "running_instance", side_effect=answers) as found, \
                patch.object(shell.Shell, "run", autospec=True, return_value=7) as run:
            code = shell.run_app(argv)
        return code, found, run, clock

    def test_the_program_waits_for_the_winner(self):
        """daemon.json is written once the winner listens: the one there when the mutex was found taken (here what a
        killed daemon left) is tried once, then daemon.json is read again"""
        stale = {"port": 1, "token": "old"}
        code, found, run, clock = self.run_app([], [None, None, None, (self.info, True)],
                                               error=shell.AlreadyRunning(stale))
        self.assertEqual(code, 0)                                     # its window was shown
        run.assert_not_called()
        self.assertEqual(found.call_args_list, [call(show=True), call(stale, show=True), call(None, show=True),
                                                call(None, show=True)])
        self.assertLess(clock.now, shell.WINNER_WAIT)

    def test_background_start_hands_over_to_the_winner(self):
        code, found, run, clock = self.run_app(["--background"], [None, None, (self.info, False)])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertEqual(found.call_count, 3)
        self.assertEqual({c.kwargs["show"] for c in found.call_args_list}, {False})      # its window stays hidden

    def test_background_start_without_an_answer_exits(self):
        """no hidden error page: the client that started it waits for a daemon and reports that none came up"""
        with self.assertLogs(shell.log, "INFO") as logs:
            code, found, run, clock = self.run_app(["--background"], lambda info=None, show=True: None)
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertGreaterEqual(clock.now, shell.WINNER_WAIT)          # it waited for the winner first
        self.assertIn("ERROR", [r.levelname for r in logs.records])

    def test_the_program_shows_the_note_when_the_winner_hangs(self):
        with self.assertLogs(shell.log, "ERROR"):
            code, found, run, clock = self.run_app([], lambda info=None, show=True: None)
        self.assertEqual(code, 7)
        app = run.call_args[0][0]
        self.assertIsNone(app.backend)
        self.assertTrue(app.note)
        self.assertGreaterEqual(clock.now, shell.WINNER_WAIT)

    def test_a_daemon_that_cannot_start(self):
        """any other failure: the program shows it on its error page, a background start exits"""
        error = RuntimeError("the HTTP server did not start")
        with self.assertLogs(shell.log, "ERROR"):
            code, found, run, clock = self.run_app(["--background"], [None], error=error)
        self.assertEqual(code, 1)
        run.assert_not_called()
        with self.assertLogs(shell.log, "ERROR"):
            code, found, run, clock = self.run_app([], [None], error=error)
        self.assertEqual(code, 7)
        self.assertIn("the HTTP server did not start", run.call_args[0][0].note)


def start_daemon(case, window):
    """a daemon in this process for one test (case): the program's, its window not up yet (window=True, no on_show), or
    one without a window (`wuxian serve`); a test home and mutex, never the user's daemon"""
    from wuxianworkshop.daemon import server
    from wuxianworkshop.daemon.companion import CompanionLoop
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    env = patch.dict(os.environ, {"WUXIAN_HOME": tmp.name, "WUXIAN_MUTEX": rf"Local\WuxianWorkshopShell{os.getpid()}"})
    env.start()
    case.addCleanup(env.stop)
    handle = server.start(worker_factory=lambda on_debug, log: CompanionLoop(
        None, find_window=lambda: None, files=False, on_debug=on_debug, log=log), window=window)
    case.addCleanup(handle.stop)
    return handle


class WindowComingUp(unittest.TestCase):
    """the program's daemon listens (daemon.json is written) seconds before its window is up: pywebview runs after_start,
    which sets on_show, while it makes the window. A second start in that gap (the program opened twice, or opened while
    a client's `app --background` starts) must open no second window and tray icon on that daemon's page; a --background
    start must not bring up a window that is hidden on purpose"""

    def test_the_program_tells_its_daemon_that_a_window_comes(self):
        from wuxianworkshop.daemon import server
        with patch.object(server, "start", return_value="handle") as start:
            self.assertEqual(shell.start_backend(shell.parse_args(["--background"])), ("handle", ""))
        self.assertIs(start.call_args.kwargs["window"], True)

    def start_daemon(self):
        return start_daemon(self, window=True)

    def run_app(self, argv, handle):
        with patch.object(webview2, "missing", return_value=[]), \
                patch.object(handle, "show", wraps=handle.show) as asked, \
                patch.object(shell.Shell, "run", autospec=True, return_value=7) as run:
            code = shell.run_app(argv)
        return code, asked, run

    def test_a_second_start_before_the_window_is_up(self):
        handle = self.start_daemon()
        code, asked, run = self.run_app([], handle)
        self.assertEqual(code, 0)                                    # handed over ...
        run.assert_not_called()                                      # ... no second window
        asked.assert_called()
        shown = threading.Event()
        handle.on_show = shown.set                                   # the window is up: it shows, as asked
        self.assertTrue(shown.wait(5))

    def test_a_background_start_leaves_the_window_hidden(self):
        handle = self.start_daemon()
        shown = threading.Event()
        handle.on_show = shown.set                                   # the window is up, hidden
        code, asked, run = self.run_app(["--background"], handle)
        self.assertEqual(code, 0)
        run.assert_not_called()
        asked.assert_not_called()                                    # only asked whether it answers
        self.assertFalse(shown.is_set())


@unittest.skipUnless(sys.platform == "win32", "named kernel objects")
class OneAttachedWindow(unittest.TestCase):
    """`wuxian serve` runs, without a window of its own: the program's first start opens one on its page; a later start
    shows that one instead of opening another (and another tray icon), also while it is still opening"""

    info = {"port": 4321, "token": "t", "url": "http://127.0.0.1:4321/"}

    def setUp(self):
        name = patch.object(shell, "WINDOW_NAME", rf"Local\WuxianWorkshopWindowTest{os.getpid()}.")    # not the user's
        name.start()
        self.addCleanup(name.stop)

    def start(self):
        """a start that found the daemon running without a window"""
        return shell.join_running((self.info, False), shell.parse_args([]))

    def test_a_later_start_shows_the_window(self):
        shown, later = threading.Event(), []

        def window_up(app):                                          # the first start's window: after_start hooks
            app.backend.on_show = shown.set                          # on_show ...
            later.append(self.start())                               # ... and the program is opened again
            self.assertTrue(shown.wait(5))
            return 7
        with patch.object(shell.Shell, "run", autospec=True, side_effect=window_up) as run:
            self.assertEqual(self.start(), 7)
            self.assertEqual((later, run.call_count), ([0], 1))      # no second window
            run.side_effect, run.return_value = None, 8
            self.assertEqual(self.start(), 8)                        # that window has gone: a start opens its own

    def test_an_ask_while_the_window_opens_is_kept(self):
        shown, later = threading.Event(), []

        def window_up(app):
            later.append(self.start())                               # opened again before the first window is up
            app.backend.on_show = shown.set                          # now it is: it shows, as asked
            self.assertTrue(shown.wait(5))
            return 7
        with patch.object(shell.Shell, "run", autospec=True, side_effect=window_up) as run:
            self.assertEqual(self.start(), 7)
        self.assertEqual((later, run.call_count), ([0], 1))

    def test_on_a_daemon_without_a_window(self):
        """the whole way: POST /api/show answers shown=false, the first start attaches, the second shows its window"""
        start_daemon(self, window=False)
        shown, later = threading.Event(), []

        def window_up(app):
            app.backend.on_show = shown.set
            later.append(shell.run_app([]))
            self.assertTrue(shown.wait(5))
            return 7
        with patch.object(webview2, "missing", return_value=[]), \
                patch.object(shell.Shell, "run", autospec=True, side_effect=window_up) as run:
            self.assertEqual(shell.run_app([]), 7)
        self.assertIsInstance(run.call_args[0][0].backend, shell.AttachedBackend)
        self.assertEqual((later, run.call_count), ([0], 1))


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
