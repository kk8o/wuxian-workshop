"""load's restart line: an addon's .toc written after the running game started (Sonnet in the game, 0.10.2: an agent
changed ## Interface and told the player a hot load was enough)"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from wuxianworkshop.daemon.service import Service


def service(start):
    return SimpleNamespace(worker=SimpleNamespace(game_status=lambda: dict(start=start)))


class TocAfterStart(unittest.TestCase):
    def test_written_before_and_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            addons = Path(tmp)
            for name in ("Foo", "Foo_Guides"):
                (addons / name).mkdir()
                (addons / name / f"{name}.toc").write_text("## Interface: 16001\n", encoding="utf-8")
            now = time.time()
            for name in ("Foo", "Foo_Guides"):
                os.utime(addons / name / f"{name}.toc", (now - 100, now - 100))
            check = Service.toc_after_start
            self.assertIsNone(check(service(now - 50), addons, "Foo"))                 # older than the game
            os.utime(addons / "Foo_Guides" / "Foo_Guides.toc", (now, now))
            self.assertIsNone(check(service(now - 50), addons, "Foo"))                 # another addon's
            self.assertEqual(check(service(now - 50), addons, "Foo_Guides"), "Foo_Guides.toc")
            self.assertIsNone(check(service(None), addons, "Foo_Guides"))              # no game: nothing to say
            self.assertIsNone(check(service(now - 50), addons, "Nope"))


if __name__ == "__main__":
    unittest.main()
