"""agent/history.py: the kept versions of addons, their diffs and restores, in a temporary WUXIAN_HOME."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop.agent import history as H


class History(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(root / "home")})
        self.env.start()
        self.addons = root / "AddOns"
        self.folder = self.addons / "Foo"
        self.write("Foo.toc", "## Interface: 16001\nCore.lua\n")
        self.write("Core.lua", "local a = 1\nprint(a)\n")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, rel, text, mtime=None):
        p = self.folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        if mtime is not None:
            os.utime(p, ns=(mtime, mtime))
        return p

    def save(self, reason="manual", note=""):
        return H.save(self.addons, "Foo", reason, note)

    def test_a_version_only_when_the_files_changed(self):
        v1 = self.save("watch")
        self.assertEqual((v1["id"], v1["new"], v1["count"], v1["counts"]["added"]), (1, True, 2, 2))
        again = self.save("load")
        self.assertEqual((again["id"], again["new"]), (1, False))
        self.write("Core.lua", "local a = 2\nprint(a)\n")
        self.write("New.lua", "x = 1\n")
        (self.folder / "Foo.toc").unlink()
        v2 = self.save("save", "Core.lua")
        self.assertEqual((v2["id"], v2["new"], v2["reason"], v2["note"]), (2, True, "save", "Core.lua"))
        self.assertEqual((v2["added"], v2["changed"], v2["removed"]), (["New.lua"], ["Core.lua"], ["Foo.toc"]))
        listed = H.versions(self.addons, "Foo")
        self.assertEqual([v["id"] for v in listed["versions"]], [2, 1])              # newest first
        self.assertTrue(listed["now"]["same"])
        self.write("New.lua", "x = 2\n")
        now = H.versions(self.addons, "Foo")["now"]
        self.assertEqual((now["same"], now["since"], now["changed"]), (False, 2, ["New.lua"]))

    def test_dot_folders_big_files_and_too_big_addons(self):
        self.write(".git/HEAD", "ref: refs/heads/main\n")
        self.write("media/big.blp", b"x" * 300)
        with mock.patch.object(H, "MAX_FILE", 200):
            v = self.save()
        self.assertEqual(v["count"], 2)
        self.assertEqual(v["skipped"], [dict(path="media/big.blp", size=300)])
        with mock.patch.object(H, "MAX_TOTAL", 10), self.assertRaises(H.TooBig):
            self.save()
        with mock.patch.object(H, "MAX_FILES", 1), self.assertRaises(H.TooBig):
            self.save()

    def test_same_size_and_time_stamp_is_not_read_again(self):
        stamp = time.time_ns() - 10 ** 10
        self.write("Core.lua", "local a = 1\n", stamp)
        self.save()
        self.write("Core.lua", "local b = 1\n", stamp)       # same size, same time stamp: taken to be unchanged
        self.assertFalse(self.save()["new"])
        self.write("Core.lua", "local c = 1\n", stamp + 10 ** 7)     # NTFS keeps 100 ns steps
        self.assertTrue(self.save()["new"])

    def test_restore_writes_back_removes_and_can_be_undone(self):
        self.write("ui/Window.lua", "w = 1\n")
        self.save("watch")
        self.write("Core.lua", "broken(\n")
        self.write("extra/deep/More.lua", "m = 1\n")
        (self.folder / "ui" / "Window.lua").unlink()
        self.write(".git/HEAD", "keep me\n")
        res = H.restore(self.addons, "Foo", 1)
        self.assertEqual((res["restored"], res["saved"], res["saved_new"]), (1, 2, True))
        self.assertEqual(res["written"], ["Core.lua", "ui/Window.lua"])
        self.assertEqual(res["removed"], ["extra/deep/More.lua"])
        self.assertEqual((self.folder / "Core.lua").read_text(), "local a = 1\nprint(a)\n")
        self.assertEqual((self.folder / "ui" / "Window.lua").read_text(), "w = 1\n")
        self.assertFalse((self.folder / "extra").exists())                         # emptied folders go too
        self.assertTrue((self.folder / ".git" / "HEAD").exists())                  # never kept, never touched
        v2 = H.versions(self.addons, "Foo")["versions"][0]
        self.assertEqual((v2["id"], v2["reason"], v2["note"]), (2, "restore", "#1"))
        H.restore(self.addons, "Foo", 2)                                            # the restore undone
        self.assertEqual((self.folder / "Core.lua").read_text(), "broken(\n")
        self.assertEqual((self.folder / "extra" / "deep" / "More.lua").read_text(), "m = 1\n")
        self.assertFalse((self.folder / "ui" / "Window.lua").exists())

    def test_restore_brings_back_a_deleted_addon_and_a_read_only_file(self):
        self.save()
        self.write("Core.lua", "changed\n")
        os.chmod(self.folder / "Core.lua", 0o444)
        H.restore(self.addons, "Foo", 1)
        self.assertEqual((self.folder / "Core.lua").read_text(), "local a = 1\nprint(a)\n")
        os.chmod(self.folder / "Core.lua", 0o666)
        import shutil
        shutil.rmtree(self.folder)
        res = H.restore(self.addons, "Foo", 1)
        self.assertEqual((res["saved"], sorted(res["written"])), (None, ["Core.lua", "Foo.toc"]))
        self.assertTrue((self.folder / "Foo.toc").is_file())
        with self.assertRaises(H.NotFound):
            H.restore(self.addons, "Foo", 9)

    def test_diffs(self):
        self.write("art.tga", b"\0\1\2")
        self.save()
        self.write("Core.lua", "local a = 2\nprint(a)\n")
        self.write("Chinese.lua", "-- 说明\n".encode("gb18030"))
        self.write("art.tga", b"\0\1\2\3")
        d = H.diff(self.addons, "Foo", 1)
        self.assertEqual((d["base"], d["to"], d["truncated"]), (1, "now", False))
        rows = {f["path"]: f for f in d["files"]}
        self.assertEqual({p: f["status"] for p, f in rows.items()},
                         {"Core.lua": "changed", "Chinese.lua": "added", "art.tga": "changed"})
        self.assertIn("-local a = 1\n+local a = 2", rows["Core.lua"]["diff"])
        self.assertEqual((rows["Core.lua"]["plus"], rows["Core.lua"]["minus"]), (1, 1))
        self.assertIn("+-- 说明", rows["Chinese.lua"]["diff"])
        self.assertEqual((rows["art.tga"]["binary"], rows["art.tga"]["new_size"]), (True, 4))
        self.save()
        prev = H.diff(self.addons, "Foo", 2, "prev")
        self.assertEqual((prev["base"], prev["to"], len(prev["files"])), (1, 2, 3))
        first = H.diff(self.addons, "Foo", 1, "prev")
        self.assertEqual((first["base"], {f["status"] for f in first["files"]}), (None, {"added"}))
        back = H.diff(self.addons, "Foo", 2, 1)
        self.assertIn("-local a = 2\n+local a = 1", {f["path"]: f for f in back["files"]}["Core.lua"]["diff"])
        cut = H.diff(self.addons, "Foo", 1, max_lines=4)
        self.assertTrue(cut["truncated"])
        self.assertEqual(sum(len(f.get("diff", "").splitlines()) for f in cut["files"]), 4)

    def test_old_versions_go_automatic_ones_first(self):
        with mock.patch.object(H, "KEEP", 3):
            for i, reason in enumerate(["watch", "load", "manual", "save", "save"]):
                self.write("Core.lua", f"v = {i}\n")
                self.save(reason)
            kept = [(v["id"], v["reason"]) for v in reversed(H.versions(self.addons, "Foo")["versions"])]
        self.assertEqual(kept, [(1, "watch"), (3, "manual"), (5, "save")])
        home = Path(os.environ["WUXIAN_HOME"]) / "history" / "Foo"
        self.assertEqual(sorted(p.name for p in (home / "versions").iterdir()), ["000001.json", "000003.json", "000005.json"])
        objects = [p for p in (home / "objects").rglob("*") if p.is_file()]
        self.assertEqual(len(objects), 4)                    # Foo.toc and Core.lua of 1, 3 and 5
        for vid in (1, 3, 5):
            H.diff(self.addons, "Foo", vid)                   # every kept version still reads
        (home / "index.json").unlink()                        # the index is made again from the versions
        self.assertEqual([v["id"] for v in H.versions(self.addons, "Foo")["versions"]], [5, 3, 1])
        self.assertEqual(self.save("manual")["new"], False)
        self.write("Core.lua", "v = 9\n")
        self.assertEqual(self.save()["id"], 6)

    def test_names_summary_and_forget(self):
        for bad in ("", "../x", "a/b", ".hidden", "WoWBridge", "!WuxianWorkshop", None):
            with self.assertRaises(H.HistoryError):
                H.save(self.addons, bad, "manual")
        with self.assertRaises(H.NotFound):
            H.save(self.addons, "Missing", "manual")
        with self.assertRaises(H.HistoryError):
            self.save("whatever")
        self.save()
        rows = H.summary(sizes=True)
        self.assertEqual([(r["addon"], r["versions"], r["latest_id"]) for r in rows], [("Foo", 1, 1)])
        self.assertGreater(rows[0]["bytes"], 0)
        self.assertEqual(H.versions(self.addons, "Other"), dict(addon="Other", versions=[], now=None))
        H.forget("Foo")
        self.assertEqual(H.summary(), [])
        with self.assertRaises(H.NotFound):
            H.forget("Foo")


if __name__ == "__main__":
    unittest.main()
