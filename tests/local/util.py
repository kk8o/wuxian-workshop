"""Helpers of the local extensions' tests: an extension folder with the fixture (fixture_ext.py) as its main.py, and a
wait for a condition."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

FIXTURE = Path(__file__).with_name("fixture_ext.py")


def make_extension(folder, eid, mode="", tests=None, extra=None, **fields):
    """an extension folder: wuxian-extension.json (run ["python", "main.py"], env FIXTURE_MODE), main.py (the fixture),
    AGENT.md; tests: {file name: text} in tests/. fields go into the manifest (None leaves one out). extra: the
    fixture's FIXTURE_EXTRA (words with commas between them), None leaves it out"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, folder / "main.py")
    (folder / "AGENT.md").write_text(f"# {eid}\n", encoding="utf-8")
    manifest = {"id": eid, "title": f"测试 {eid}", "version": "0.1.0", "run": ["python", "main.py"],
                "env": {"FIXTURE_MODE": mode}}
    if extra is not None:
        manifest["env"]["FIXTURE_EXTRA"] = extra
    manifest.update(fields)
    manifest = {k: v for k, v in manifest.items() if v is not None}
    (folder / "wuxian-extension.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    if tests:
        (folder / "tests").mkdir(exist_ok=True)
        for name, text in tests.items():
            (folder / "tests" / name).write_text(text, encoding="utf-8")
    return folder


def until(fn, seconds=15.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.05)
    return bool(fn())


def gone(pid, seconds=10.0):
    """whether process pid has ended"""
    import subprocess
    def ended():
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                             creationflags=0x08000000).stdout
        return str(pid) not in out
    return until(ended, seconds)


class HostCase(unittest.TestCase):
    """a Host on a state folder of its own (WUXIAN_HOME too), with real processes of the fixture. What the host tells
    is kept: events [(id, item)], revisions, notes; mode_now is the daemon's mode it is told ("developer", "player")"""

    def setUp(self):
        from wuxianworkshop.local import host as H
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name).resolve()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(self.root / "home")})
        self.env.start()
        self.events, self.revisions, self.notes = [], [], []
        self.mode_now = "developer"
        self.host = H.Host(self.root / "state", version="9.9.9", note=self.notes.append,
                           on_event=lambda eid, item: self.events.append((eid, item)),
                           on_change=self.revisions.append, python=sys.executable, sdk=self.root / "sdk",
                           mode=lambda: self.mode_now)

    def tearDown(self):
        self.host.close()
        self.env.stop()
        self.tmp.cleanup()

    def add(self, eid="fx", mode="", **fields):
        """registers a new extension folder of the fixture: (its row, its folder)"""
        folder = make_extension(self.root / "ext" / eid, eid, mode, **fields)
        return self.host.add(str(folder)), folder

    def output(self, eid="fx"):
        return [line["text"] for line in self.host.logs(eid, 0, 2000)["lines"]]

    def pids(self, eid="fx"):
        """the pids of every process the host started for it, by its output"""
        import re
        return [int(m[1]) for m in (re.search(r"started \(pid (\d+)\)", line) for line in self.output(eid)) if m]
