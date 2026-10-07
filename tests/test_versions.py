"""One version everywhere: the program's (pyproject, __init__, the link) and the addons it carries (their .toc Version and
WoWBridge's Lua constants) say the same; scripts/set_version.py sets them together."""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop import __version__

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("set_version", ROOT / "scripts" / "set_version.py")
set_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(set_version)


class Versions(unittest.TestCase):
    def test_every_place_says_the_programs_version(self):
        self.assertEqual(set_version.found(), {rel: __version__ for rel, _ in set_version.PLACES})

    def test_set_version_changes_only_the_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, _ in set_version.PLACES:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / rel, root / rel)
            before = {rel: (root / rel).read_bytes() for rel, _ in set_version.PLACES}
            changed = set_version.set_version("9.8.7", root)
            self.assertEqual(sorted(changed), sorted(rel for rel, _ in set_version.PLACES))
            self.assertEqual(set(set_version.found(root).values()), {"9.8.7"})
            for rel, data in before.items():
                after = (root / rel).read_bytes()
                self.assertEqual(after.replace(b"9.8.7", __version__.encode()), data, rel)   # nothing else moved
            self.assertEqual(set_version.set_version("9.8.7", root), [])
            with self.assertRaises(ValueError):
                set_version.set_version("latest", root)


if __name__ == "__main__":
    unittest.main()
