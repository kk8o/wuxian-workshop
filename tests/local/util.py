"""Helpers of the local extensions' tests: an extension folder with the fixture (fixture_ext.py) as its main.py, and a
wait for a condition."""
import json
import shutil
import time
from pathlib import Path

FIXTURE = Path(__file__).with_name("fixture_ext.py")


def make_extension(folder, eid, mode="", tests=None, **fields):
    """an extension folder: wuxian-extension.json (run ["python", "main.py"], env FIXTURE_MODE), main.py (the fixture),
    AGENT.md; tests: {file name: text} in tests/. fields go into the manifest (None leaves one out)"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, folder / "main.py")
    (folder / "AGENT.md").write_text(f"# {eid}\n", encoding="utf-8")
    manifest = {"id": eid, "title": f"测试 {eid}", "version": "0.1.0", "run": ["python", "main.py"],
                "env": {"FIXTURE_MODE": mode}}
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
