"""The updater (wuxianworkshop/updater.py) over a fake Velopack UpdateManager and a stand-in for the website whose feed is
signed (tests/installer/signed.py); then the real Velopack in a copy that was not installed by the Setup exe (this test
run), and in a made-up install folder against a feed served over HTTP on 127.0.0.1. Nothing leaves the machine, nothing
is applied."""
import contextlib
import functools
import hashlib
import http.server
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests.installer import signed
from wuxianworkshop import updater

FEED = signed.BASE + "feed"


class Asset:
    def __init__(self, version, size=12_345_678, notes="## 0.9.0\n- 新功能", file=None, sha256=""):
        self.Version, self.Size, self.NotesMarkdown, self.SHA256 = version, size, notes, sha256
        self.FileName = file or f"WuxianWorkshop.App-{version}-full.nupkg"


class Info:
    def __init__(self, asset):
        self.TargetFullRelease = asset


class Site:
    """the feed (a folder served as FEED: releases.win.json, signed, with the packages it lists) and the install folder
    whose packages\\ the fake Manager downloads into"""

    def __init__(self, root):
        self.feed, self.install = Path(root) / "feed", Path(root) / "App"
        self.feed.mkdir(parents=True)
        (self.install / "packages").mkdir(parents=True)
        self.assets = []
        self.fetch = signed.fetcher(Path(root))

    def publish(self, version, data=None, sign=True):
        data = data if data is not None else f"package {version}\n".encode("ascii") * 1000
        name = f"WuxianWorkshop.App-{version}-full.nupkg"
        (self.feed / name).write_bytes(data)
        self.assets.append(dict(PackageId="WuxianWorkshop.App", Version=version, Type="Full", FileName=name,
                                SHA1=hashlib.sha1(data).hexdigest().upper(), SHA256=hashlib.sha256(data).hexdigest().upper(),
                                Size=len(data), NotesMarkdown=f"## {version}\n- 新功能", NotesHtml=""))
        (self.feed / "releases.win.json").write_text(json.dumps({"Assets": self.assets}), encoding="utf-8")
        if sign:
            signed.sign(self.feed / "releases.win.json")

    def asset(self, version):
        """the feed's entry as Velopack's asset"""
        a = next(a for a in self.assets if a["Version"] == version)
        return Asset(version, a["Size"], a["NotesMarkdown"], a["FileName"], a["SHA256"])


class Manager:
    """Velopack's UpdateManager over a Site: it finds `newer` as the site lists it (or `found`: the server gave Velopack
    another feed than the program), downloads the package into the install folder (`delivered`: other bytes, as if
    Velopack's own check let them through) and has `pending` there from before the last exit"""

    def __init__(self, source, site=None, newer=None, fail=None, pending=None, found=None, delivered=None):
        self.source, self.site, self.newer, self.fail, self.pending = source, site, newer, fail, pending
        self.found, self.delivered = found, delivered
        self.checks, self.applied = 0, None

    def get_update_pending_restart(self):
        if not self.pending:
            return None
        a = self.site.asset(self.pending)
        return Asset(a.Version, a.Size, a.NotesMarkdown, a.FileName)        # as Velopack reads it back: no SHA-256

    def get_current_version(self):
        return "0.8.1"

    def check_for_updates(self):
        self.checks += 1
        if self.fail:
            raise RuntimeError(self.fail)
        return Info(self.found or self.site.asset(self.newer)) if self.newer else None

    def download_updates(self, info, progress):
        name = info.TargetFullRelease.FileName
        data = self.delivered if self.delivered is not None else (self.site.feed / name).read_bytes()
        (self.site.install / "packages" / name).write_bytes(data)
        for p in (5, 60, 100):
            progress(p)

    def wait_exit_then_apply_updates(self, info, silent=False, restart=True, restart_args=None):
        target = getattr(info, "TargetFullRelease", info)          # an UpdateInfo, or the asset of a pending update
        self.applied = (target.Version, silent, restart) + ((tuple(restart_args),) if restart_args else ())


class Updates(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.site = Site(tmp.name)
        self.store = Path(tmp.name) / "state" / updater.KEPT
        keys = signed.keys()
        keys.start()
        self.addCleanup(keys.stop)

    def make(self, source=FEED, **kw):
        made = {}

        def factory(src):
            made["m"] = Manager(src, site=self.site, **kw)
            return made["m"]
        notes = []
        u = updater.Updater(source=source, manager=factory, clock=lambda: 1000.0, note=notes.append, fetcher=self.site.fetch,
                            root=self.site.install, store=self.store)
        return u, made["m"], notes

    def package(self, version):
        return self.site.install / "packages" / f"WuxianWorkshop.App-{version}-full.nupkg"

    def test_a_signed_version_is_found_downloaded_and_applied_on_exit(self):
        self.site.publish("0.9.0")
        u, m, notes = self.make(newer="0.9.0")
        self.assertEqual(u.status()["state"], "idle")
        s = u.check()
        self.assertEqual((s["state"], s["current"], s["latest"], s["size"]), ("available", "0.8.1", "0.9.0", 14_000))
        self.assertIn("新功能", s["notes"])
        self.assertEqual(s["checked"], 1000.0)
        self.assertFalse(u.apply_on_exit())                     # not downloaded yet
        s = u.download()
        self.assertEqual((s["state"], s["progress"]), ("ready", 100))
        self.assertEqual(json.loads(self.store.read_text(encoding="utf-8"))["Version"], "0.9.0")   # kept for a restart
        self.assertEqual(u.check()["state"], "ready")           # a check later leaves the download alone
        self.assertEqual(m.checks, 1)
        self.assertTrue(u.apply_on_exit())
        self.assertEqual(m.applied, ("0.9.0", False, True))
        self.assertTrue(any("0.9.0 is available" in n for n in notes))

    def test_a_download_from_before_the_last_exit_is_ready_when_it_was_checked(self):
        self.site.publish("0.9.0")
        u, _, _ = self.make(newer="0.9.0")
        u.check()
        u.download()                                            # then the program exits without applying it
        u, m, _ = self.make(pending="0.9.0")
        s = u.status()
        self.assertEqual((s["state"], s["latest"], s["progress"]), ("ready", "0.9.0", 100))
        self.assertTrue(u.apply_on_exit())
        self.assertEqual(m.applied, ("0.9.0", False, True))
        self.store.unlink()                                     # none kept: it was never checked
        u, m, _ = self.make(pending="0.9.0", newer="0.9.0")
        self.assertEqual(u.status()["state"], "idle")
        self.assertFalse(u.apply_on_exit())
        self.assertEqual(u.check()["state"], "available")      # the next check takes it the usual way

    def test_a_release_without_a_good_signature_is_not_offered(self):
        cases = {"unsigned": lambda: self.site.publish("0.9.0", sign=False),
                 "another key": lambda: (self.site.publish("0.9.0", sign=False),
                                         signed.sign(self.site.feed / "releases.win.json", signed.OTHER)),
                 "changed after signing": lambda: (self.site.publish("0.9.0"),
                                                   (self.site.feed / "releases.win.json").write_text(
                                                       json.dumps({"Assets": [dict(self.site.assets[0], Size=1)]}),
                                                       encoding="utf-8"))}
        for case, publish in cases.items():
            self.site.assets.clear()
            for f in self.site.feed.iterdir():
                f.unlink()
            publish()
            u, m, notes = self.make(newer="0.9.0")
            s = u.check()
            self.assertEqual(s["state"], "error", case)
            self.assertIn("更新未通过签名校验", s["error"], case)
            self.assertEqual(u.download()["state"], "error", case)              # nothing to download
            self.assertFalse(any(self.site.install.joinpath("packages").iterdir()), case)
            self.assertTrue(any("0.9.0 not taken" in n for n in notes), case)

    def test_velopack_must_find_the_signed_package(self):
        """the server gives Velopack another feed than the program: another package, size or hash, or a version the
        signed feed does not list"""
        self.site.publish("0.9.0")
        good = self.site.asset("0.9.0")
        for found in (Asset("0.9.0", good.Size, file="Evil-0.9.0-full.nupkg", sha256=good.SHA256),
                      Asset("0.9.0", good.Size + 1, file=good.FileName, sha256=good.SHA256),
                      Asset("0.9.0", good.Size, file=good.FileName, sha256="0" * 64),
                      Asset("0.9.1", good.Size, sha256=good.SHA256)):
            u, _, _ = self.make(newer="0.9.0", found=found)
            s = u.check()
            self.assertEqual(s["state"], "error", vars(found))
            self.assertIn("更新未通过签名校验", s["error"])

    def test_a_package_that_is_not_the_signed_one_is_neither_kept_nor_applied(self):
        self.site.publish("0.9.0")
        u, m, _ = self.make(newer="0.9.0", delivered=b"something else")
        u.check()
        s = u.download()
        self.assertEqual(s["state"], "error")
        self.assertIn("更新包未通过校验", s["error"])
        self.assertFalse(self.package("0.9.0").exists())        # removed: Velopack would not fetch it again
        self.assertFalse(self.store.exists())
        m.delivered = None                                      # the next round downloads the real one
        u.check()
        self.assertEqual(u.download()["state"], "ready")
        self.package("0.9.0").write_bytes(b"replaced before the exit")
        self.assertFalse(u.apply_on_exit())
        self.assertIsNone(m.applied)
        s = u.status()
        self.assertEqual(s["state"], "error")
        self.assertIn("更新包未通过校验", s["error"])
        self.assertFalse(self.package("0.9.0").exists())
        self.assertFalse(self.store.exists())

    def test_a_local_feed_is_read_unsigned(self):
        self.site.publish("0.9.0", sign=False)
        u, m, _ = self.make(source=str(self.site.feed), newer="0.9.0")
        self.assertEqual(u.check()["state"], "available")
        self.assertEqual(u.download()["state"], "ready")
        self.assertTrue(u.apply_on_exit())

    def test_the_channel_names_the_feed(self):
        self.assertEqual(updater.channel(self.site.install), "win")          # no manifest: the default
        (self.site.install / "current").mkdir()
        (self.site.install / "current" / "sq.version").write_text("<package><metadata><version>0.8.1</version>"
                                                                  "<channel>beta</channel></metadata></package>",
                                                                  encoding="utf-8")
        self.assertEqual(updater.channel(self.site.install), "beta")
        self.site.publish("0.9.0")
        u, _, _ = self.make(newer="0.9.0")
        self.assertEqual(u.check()["state"], "error")                     # the site has no releases.beta.json
        os.replace(self.site.feed / "releases.win.json", self.site.feed / "releases.beta.json")
        os.replace(self.site.feed / "releases.win.json.sig", self.site.feed / "releases.beta.json.sig")
        self.assertEqual(u.check()["state"], "available")

    def test_nothing_newer(self):
        u, m, _ = self.make()
        self.assertEqual(u.check()["state"], "current")
        self.assertEqual(u.download()["state"], "current")     # nothing to download

    def test_a_failed_check_says_why_and_can_be_retried(self):
        self.site.publish("0.9.0")
        u, m, notes = self.make(fail="connection refused")
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
        u, m, _ = self.make()
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
        with mock.patch.dict(os.environ, {"WUXIAN_UPDATE_FEED": r"C:\releases"}):
            self.assertEqual(updater.feed(), r"C:\releases")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WUXIAN_UPDATE_FEED", None)
            self.assertEqual(updater.feed(), updater.FEED)


def nuspec(version):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://schemas.microsoft.com/packaging/2010/07/nuspec.xsd">'
            "<metadata><id>WuxianWorkshop.App</id><title>t</title><description>t</description><authors>a</authors>"
            f"<version>{version}</version><channel>win</channel><mainExe>wuxian.exe</mainExe><os>win</os><rid>win</rid>"
            f"<releaseNotes>## {version}</releaseNotes></metadata></package>")


class Feed(http.server.SimpleHTTPRequestHandler):
    """the site's files; Velopack's request for the feed (it adds ?localVersion=…) gets `velopack_feed` when set: a
    server that gives Velopack another feed than the program"""
    velopack_feed = None

    def do_GET(self):
        if self.velopack_feed is not None and self.path.startswith("/feed/releases.win.json?"):
            body = self.velopack_feed
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        pass


@contextlib.contextmanager
def velopack_log():
    """Velopack logs to the process's stderr from Rust ("Using explicit locator configuration…"): into a file meanwhile,
    out of the test output"""
    sys.stderr.flush()
    saved = os.dup(2)
    with tempfile.TemporaryFile() as sink:
        os.dup2(sink.fileno(), 2)
        try:
            yield
        finally:
            os.dup2(saved, 2)
            os.close(saved)


@unittest.skipUnless(importlib.util.find_spec("velopack"), "no velopack module")
class RealVelopack(unittest.TestCase):
    """the real Velopack in a made-up install folder (0.9.10) against a site on 127.0.0.1 that has 0.9.11: its feed
    signed, the package a zip with a .nuspec as Velopack reads it back. Velopack finds and downloads it, the updater
    takes it only as signed, and a start after it has it ready. Nothing is applied: Update.exe is a stub"""

    def setUp(self):
        import velopack
        self.velopack = velopack
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root, self.site = Path(tmp.name) / "App", Path(tmp.name) / "site"
        (self.root / "current").mkdir(parents=True)
        (self.root / "packages").mkdir()
        (self.site / "feed").mkdir(parents=True)
        (self.root / "Update.exe").write_bytes(b"MZ")
        (self.root / "current" / "wuxian.exe").write_bytes(b"MZ")
        (self.root / "current" / "sq.version").write_text(nuspec("0.9.10"), encoding="utf-8")
        self.store = Path(tmp.name) / "state" / updater.KEPT
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("WuxianWorkshop.App.nuspec", nuspec("0.9.11"))
            z.writestr("lib/app/wuxian.exe", b"MZ")
        self.pkg = buf.getvalue()
        self.name = "WuxianWorkshop.App-0.9.11-full.nupkg"
        (self.site / "feed" / self.name).write_bytes(self.pkg)
        self.feed = self.write_feed(self.pkg)
        signed.sign(self.site / "feed" / "releases.win.json")
        Feed.velopack_feed = None
        self.addCleanup(setattr, Feed, "velopack_feed", None)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Feed, directory=str(self.site)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.url = f"http://127.0.0.1:{srv.server_address[1]}/feed"
        keys = signed.keys()
        keys.start()
        self.addCleanup(keys.stop)
        quiet = velopack_log()
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def write_feed(self, pkg, to=None):
        asset = dict(PackageId="WuxianWorkshop.App", Version="0.9.11", Type="Full", FileName=self.name,
                     SHA1=hashlib.sha1(pkg).hexdigest(), SHA256=hashlib.sha256(pkg).hexdigest(), Size=len(pkg),
                     NotesMarkdown="## 0.9.11", NotesHtml="")
        data = json.dumps({"Assets": [asset]}).encode("utf-8")
        if to is None:
            (self.site / "feed" / "releases.win.json").write_bytes(data)
        return data

    def updater(self):
        loc = self.velopack.VelopackLocatorConfig(RootAppDir=self.root, UpdateExePath=self.root / "Update.exe",
                                                  PackagesDir=self.root / "packages",
                                                  ManifestPath=self.root / "current" / "sq.version",
                                                  CurrentBinaryDir=self.root / "current", IsPortable=False)

        def manager(source):
            return self.velopack.UpdateManager(self.velopack.HttpSource(source, self.velopack.HttpOptions([], 10_000)),
                                               None, loc)
        return updater.Updater(source=self.url, manager=manager, root=self.root, store=self.store)

    def test_a_signed_release(self):
        u = self.updater()
        s = u.check()
        self.assertEqual((s["state"], s["current"], s["latest"]), ("available", "0.9.10", "0.9.11"), s["error"])
        self.assertEqual(u.download()["state"], "ready")
        self.assertEqual((self.root / "packages" / self.name).read_bytes(), self.pkg)
        s = self.updater().status()                              # the next start, before the update is applied
        self.assertEqual((s["state"], s["latest"]), ("ready", "0.9.11"))

    def test_velopack_given_another_package(self):
        Feed.velopack_feed = self.write_feed(self.pkg + b"\0", to="velopack")   # the same file, another size and SHA-256
        s = self.updater().check()
        self.assertEqual(s["state"], "error")
        self.assertIn("更新未通过签名校验", s["error"])
        self.assertFalse((self.root / "packages" / self.name).exists())


if __name__ == "__main__":
    unittest.main()
