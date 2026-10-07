"""The updater (wuxianworkshop/updater.py) over a fake Velopack UpdateManager, and the real one in a copy that was not
installed by the Setup exe (this test run): no network, nothing applied."""
import threading
import unittest

from wuxianworkshop import updater


class Asset:
    def __init__(self, version, size=12_345_678, notes="## 0.9.0\n- 新功能"):
        self.Version, self.Size, self.NotesMarkdown = version, size, notes


class Info:
    def __init__(self, version):
        self.TargetFullRelease = Asset(version)


class Manager:
    def __init__(self, source, newer=None, fail=None, pending=None):
        self.source, self.newer, self.fail, self.pending = source, newer, fail, pending
        self.checks, self.applied = 0, None

    def get_update_pending_restart(self):
        return Asset(self.pending) if self.pending else None

    def get_current_version(self):
        return "0.8.1"

    def check_for_updates(self):
        self.checks += 1
        if self.fail:
            raise RuntimeError(self.fail)
        return Info(self.newer) if self.newer else None

    def download_updates(self, info, progress):
        for p in (5, 60, 100):
            progress(p)

    def wait_exit_then_apply_updates(self, info, silent=False, restart=True, restart_args=None):
        target = getattr(info, "TargetFullRelease", info)          # an UpdateInfo, or the asset of a pending update
        self.applied = (target.Version, silent, restart) + ((tuple(restart_args),) if restart_args else ())


def make(**kw):
    made = {}

    def factory(source):
        made["m"] = Manager(source, **kw)
        return made["m"]
    notes = []
    u = updater.Updater(source="https://example.invalid/feed", manager=factory, clock=lambda: 1000.0, note=notes.append)
    return u, made["m"], notes


class Updates(unittest.TestCase):
    def test_a_newer_version_is_found_downloaded_and_applied_on_exit(self):
        u, m, notes = make(newer="0.9.0")
        self.assertEqual(u.status()["state"], "idle")
        s = u.check()
        self.assertEqual((s["state"], s["current"], s["latest"], s["size"]), ("available", "0.8.1", "0.9.0", 12_345_678))
        self.assertIn("新功能", s["notes"])
        self.assertEqual(s["checked"], 1000.0)
        self.assertFalse(u.apply_on_exit())                     # not downloaded yet
        s = u.download()
        self.assertEqual((s["state"], s["progress"]), ("ready", 100))
        self.assertEqual(u.check()["state"], "ready")           # a check later leaves the download alone
        self.assertEqual(m.checks, 1)
        self.assertTrue(u.apply_on_exit())
        self.assertEqual(m.applied, ("0.9.0", False, True))
        self.assertTrue(any("0.9.0 is available" in n for n in notes))

    def test_a_download_from_before_the_last_exit_is_ready(self):
        u, m, _ = make(pending="0.9.0")
        s = u.status()
        self.assertEqual((s["state"], s["latest"], s["progress"]), ("ready", "0.9.0", 100))
        self.assertTrue(u.apply_on_exit())
        self.assertEqual(m.applied, ("0.9.0", False, True))

    def test_nothing_newer(self):
        u, m, _ = make()
        self.assertEqual(u.check()["state"], "current")
        self.assertEqual(u.download()["state"], "current")     # nothing to download

    def test_a_failed_check_says_why_and_can_be_retried(self):
        u, m, notes = make(fail="connection refused")
        s = u.check()
        self.assertEqual(s["state"], "error")
        self.assertIn("connection refused", s["error"])
        m.fail, m.newer = None, "0.9.0"
        self.assertEqual(u.check()["state"], "available")

    def test_not_installed(self):
        def factory(source):
            raise RuntimeError("This application is not properly installed: Could not auto-locate app manifest")
        u = updater.Updater(source="x", manager=factory)
        s = u.status()
        self.assertEqual((s["supported"], s["state"], s["current"]), (False, "unsupported", None))
        self.assertIn("不是用安装包装的", s["reason"])
        self.assertEqual(u.check()["state"], "unsupported")
        self.assertFalse(u.apply_on_exit())

    def test_the_real_velopack_in_a_copy_not_installed(self):
        s = updater.Updater(source="https://example.invalid/feed").status()
        self.assertFalse(s["supported"])
        self.assertEqual(s["feed"], "https://example.invalid/feed")

    def test_the_scheduler_checks_and_stops(self):
        u, m, _ = make()
        done = threading.Event()
        real = u.check

        def check():
            real()
            if m.checks >= 2:
                done.set()
        u.check = check
        sch = updater.Scheduler(u.check, first=0.01, every=0.01).start()
        self.assertTrue(done.wait(5))
        sch.stop()

    def test_the_feed_can_be_overridden(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"WUXIAN_UPDATE_FEED": r"C:\releases"}):
            self.assertEqual(updater.feed(), r"C:\releases")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WUXIAN_UPDATE_FEED", None)
            self.assertEqual(updater.feed(), updater.FEED)


if __name__ == "__main__":
    unittest.main()
