"""The extensions' home folder (home.py) on a temporary folder: what is inside it and what is found there, a folder
taken away (its name, the newest that are kept, one that cannot be moved), the folder a package is unpacked in."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop.local import home as H
from wuxianworkshop.local.manifest import MANIFEST


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.top = Path(self.tmp.name).resolve()
        self.root = self.top / "extensions"
        self.root.mkdir()

    def ext(self, name, manifest=True, parent=None):
        folder = (parent or self.root) / name
        folder.mkdir(parents=True)
        if manifest:
            (folder / MANIFEST).write_text("{}", encoding="utf-8")
        (folder / "main.py").write_text(f"# {name}\n", encoding="utf-8")
        return folder

    def removed(self):
        return sorted(p.name for p in (self.root / H.REMOVED).iterdir())

    def test_inside(self):
        self.assertTrue(H.inside(self.ext("a"), self.root))
        self.assertTrue(H.inside(str(self.root / "a"), str(self.root)))
        self.assertTrue(H.inside(self.root / "not_there_any_more", self.root))
        self.assertFalse(H.inside(self.root / "a" / "deeper", self.root))
        self.assertFalse(H.inside(self.root, self.root))
        self.assertFalse(H.inside(self.top / "elsewhere", self.root))
        self.assertFalse(H.inside(self.top, self.root))
        self.assertFalse(H.inside(None, self.root))
        if os.name == "nt":                                # the case does not count there
            self.assertTrue(H.inside(str(self.root).upper() + "\\A", str(self.root).lower()))

    def test_a_junction_that_leads_elsewhere(self):
        try:
            import _winapi
            other = self.ext("other", parent=self.top / "mine")
            _winapi.CreateJunction(str(other), str(self.root / "other"))
        except (ImportError, AttributeError, OSError) as e:
            self.skipTest(f"no junction here: {e!r}")
        self.assertTrue((self.root / "other" / MANIFEST).is_file())
        self.assertFalse(H.inside(self.root / "other", self.root))      # what it leads to is not the workshop's
        self.assertEqual(H.found(self.root), [])

    def test_found(self):
        self.assertEqual(H.found(self.root), [])
        self.assertEqual(H.found(self.root / "none"), [])
        for name in ("beta", "Alpha", "gamma"):
            self.ext(name)
        self.ext("plain", manifest=False)                  # no manifest: no extension
        self.ext(".hidden")                                # a dot first: not one either
        self.ext("fx-20261110-120000", parent=self.root / H.REMOVED)
        self.ext("fx-20261110-120001", parent=self.root / H.INCOMING)
        (self.root / "file.txt").write_text("x", encoding="utf-8")
        self.assertEqual(H.found(self.root), [self.root / "Alpha", self.root / "beta", self.root / "gamma"])

    def test_names(self):
        self.assertEqual(H.name_of("voice"), "voice")
        self.assertEqual(H.name_of("../x y"), "___x_y")    # nothing that leads out of the folder
        self.assertEqual(H.name_of(""), "extension")
        self.assertEqual(H.name_of(None), "extension")
        self.assertEqual(len(H.name_of("a" * 99)), 40)
        names = ["fx-20261110-120000-10", "b-20261110-120000", "fx-20261109-235959", "fx-20261110-120000-2", "odd"]
        self.assertEqual([p.name for p in sorted((Path(n) for n in names), key=H.order)],
                         ["odd", "fx-20261109-235959", "b-20261110-120000", "fx-20261110-120000-2",
                          "fx-20261110-120000-10"])

    def test_trash(self):
        folder = self.ext("fx")
        to = H.trash(folder, self.root, "fx")
        self.assertEqual(to.parent, self.root / H.REMOVED)
        self.assertTrue(re.fullmatch(r"fx-\d{8}-\d{6}", to.name), to.name)
        self.assertFalse(folder.exists())
        self.assertEqual((to / "main.py").read_text(encoding="utf-8"), "# fx\n")
        self.assertTrue((to / MANIFEST).is_file())
        with mock.patch.object(H.time, "strftime", return_value="20261110-120000"):       # the same second: -2, -3
            got = [H.trash(self.ext("fx"), self.root, "fx").name for _ in range(3)]
            self.assertEqual(got, ["fx-20261110-120000", "fx-20261110-120000-2", "fx-20261110-120000-3"])
            self.assertEqual(H.trash(self.ext("named"), self.root, "../up").name, "___up-20261110-120000")
        self.assertEqual(H.found(self.root), [])           # nothing of .removed is an extension
        with self.assertRaises(FileNotFoundError):
            H.trash(self.root / "ghost", self.root, "ghost")

    def test_only_the_newest_are_kept(self):
        removed = self.root / H.REMOVED
        for day in range(1, 26):
            self.ext(f"old-202601{day:02d}-000000", parent=removed)
        self.ext("notes", parent=removed)                  # not one the workshop named: left alone
        (removed / "readme.txt").write_text("x", encoding="utf-8")
        to = H.trash(self.ext("fx"), self.root, "fx")
        ours = [n for n in self.removed() if H.STAMPED.search(n)]
        self.assertEqual(len(ours), H.KEPT)
        self.assertEqual(H.KEPT, 20)
        self.assertEqual(ours, sorted([f"old-202601{day:02d}-000000" for day in range(7, 26)] + [to.name]))
        self.assertTrue((removed / "notes" / "main.py").is_file())
        self.assertTrue((removed / "readme.txt").is_file())
        H.prune(removed, keep=2)
        self.assertEqual([n for n in self.removed() if H.STAMPED.search(n)], [to.name, "old-20260125-000000"])
        H.prune(self.root / "none")                        # no such folder: nothing

    def test_a_folder_that_cannot_be_moved(self):
        folder = self.ext("fx")
        self.ext("old-20260101-000000", parent=self.root / H.REMOVED)
        with mock.patch.object(H, "MOVE_WAITS", ()), \
                mock.patch.object(H.os, "rename", side_effect=PermissionError(13, "in use")) as rename:
            with self.assertRaises(PermissionError):
                H.trash(folder, self.root, "fx")
            self.assertEqual(rename.call_count, 1)
        self.assertEqual((folder / "main.py").read_text(encoding="utf-8"), "# fx\n")     # where it was, whole
        self.assertEqual(self.removed(), ["old-20260101-000000"])       # and nothing new is in .removed
        real, sleeps, tries = os.rename, [], []

        def twice(a, b):                                   # held for a moment (a process that just ended)
            tries.append(a)
            if len(tries) < 3:
                raise PermissionError(13, "in use")
            return real(a, b)

        with mock.patch.object(H.os, "rename", side_effect=twice), mock.patch.object(H.time, "sleep", sleeps.append):
            to = H.trash(folder, self.root, "fx")
        self.assertEqual((len(tries), sleeps), (3, list(H.MOVE_WAITS[:2])))
        self.assertTrue((to / "main.py").is_file())
        self.assertFalse(folder.exists())

    @unittest.skipUnless(os.name == "nt", "an open file holds its folder on Windows")
    def test_a_file_of_it_open(self):
        folder = self.ext("fx")
        with mock.patch.object(H, "MOVE_WAITS", ()), open(folder / "main.py", "rb"):
            with self.assertRaises(OSError):
                H.trash(folder, self.root, "fx")
            self.assertTrue((folder / MANIFEST).is_file())
        self.assertEqual(self.removed(), [])
        self.assertTrue((H.trash(folder, self.root, "fx") / "main.py").is_file())

    def test_incoming(self):
        a = H.incoming(self.root, "fx")
        self.assertEqual(a.parent, self.root / H.INCOMING)
        self.assertTrue(re.fullmatch(r"fx-\d{8}-\d{6}", a.name), a.name)
        self.assertEqual(list(a.iterdir()), [])
        with mock.patch.object(H.time, "strftime", return_value="20261110-120000"):
            b, c = H.incoming(self.root, "fx"), H.incoming(self.root, "fx")
        self.assertEqual((b.name, c.name), ("fx-20261110-120000", "fx-20261110-120000-2"))
        self.assertTrue(b.is_dir() and c.is_dir())
        self.assertEqual(H.found(self.root), [])
        self.assertFalse(H.inside(a, self.root))           # in .incoming, not in the home folder itself


if __name__ == "__main__":
    unittest.main()
