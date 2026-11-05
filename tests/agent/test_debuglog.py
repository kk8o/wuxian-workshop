"""debug.log (agent/debuglog.py): the file stays open and every entry is written through, entries from several threads
stay whole, past its size the file becomes debug.log.1 (one old file only), a rename that fails does not lose entries,
and close() lets the file go."""
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop.agent import debuglog
from wuxianworkshop.agent.debuglog import DebugLog, debug_writer, rotated

LINE = re.compile(r"^(\d\d:\d\d:\d\d \w+ .*|    .*)$")


def lines(path):
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


class DebugLogFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "debug.log"
        self.logs = []

    def tearDown(self):
        for log in self.logs:
            log.close()
        self.tmp.cleanup()

    def log(self, **kw):
        log = debug_writer(self.path, **kw)
        self.logs.append(log)
        return log

    def test_the_format_and_written_through(self):
        log = self.log()
        log("ERR", "boom\nline 2")
        self.assertRegex(self.path.read_text(encoding="utf-8"), r"^\d\d:\d\d:\d\d ERR boom\n    line 2\n$")
        log("INFO", "note")                                            # readable at once, the file still open
        self.assertEqual(lines(self.path)[-1][9:], "INFO note")
        self.assertEqual(self.path.read_bytes().count(os.linesep.encode()), 3)   # the platform's line ends, as before

    def test_text_utf8_cannot_take_writes_nothing(self):
        log = self.log()
        with self.assertRaises(ValueError):
            log("OUT", "half a pair \ud800")
        log("OUT", "你好")
        self.assertEqual([line[9:] for line in lines(self.path)], ["OUT 你好"])

    def test_rotates_to_one_older_file(self):
        log = self.log(max_bytes=300)
        for i in range(40):
            log("OUT", f"entry {i:02d} " + "x" * 20)
        old = rotated(self.path)
        self.assertEqual(old.name, "debug.log.1")
        self.assertTrue(old.exists())
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["debug.log", "debug.log.1"])
        self.assertLess(self.path.stat().st_size, 300)
        self.assertLessEqual(old.stat().st_size, 300 + 60)            # rotated after the entry that went past
        both = lines(old) + lines(self.path)
        self.assertTrue(all(LINE.match(line) for line in both))      # no entry cut in two
        self.assertEqual(both[-1][9:], "OUT entry 39 " + "x" * 20)   # the newest last, in order
        numbers = [int(line[19:21]) for line in both]
        self.assertEqual(numbers, list(range(numbers[0], 40)))

    def test_a_file_already_past_the_size_rotates_on_the_first_entry(self):
        self.path.write_bytes(b"x" * 500)
        self.log(max_bytes=300)("INFO", "new")
        self.assertEqual(rotated(self.path).stat().st_size, 500 + len(f"00:00:00 INFO new{os.linesep}"))
        self.assertFalse(self.path.exists())                         # the next entry starts it
        self.logs[-1]("INFO", "next")
        self.assertEqual(len(lines(self.path)), 1)

    def test_a_rename_that_fails_loses_nothing(self):
        log = self.log(max_bytes=200)
        real = os.replace
        with mock.patch.object(debuglog, "RETRY_ROTATE", 100), \
                mock.patch.object(debuglog.os, "replace", side_effect=PermissionError("in use")) as replace:
            for i in range(20):
                log("OUT", f"entry {i:02d} " + "y" * 20)
        self.assertGreater(replace.call_count, 1)                     # tried again, not on every entry
        self.assertLess(replace.call_count, 20)
        self.assertEqual(len(lines(self.path)), 20)
        self.assertFalse(rotated(self.path).exists())
        with mock.patch.object(debuglog.os, "replace", side_effect=real):
            for i in range(20, 30):
                log("OUT", f"entry {i:02d} " + "y" * 20)
        self.assertTrue(rotated(self.path).exists())                 # once the rename works, it rotates
        numbers = [int(line[19:21]) for line in lines(rotated(self.path)) + lines(self.path)]
        self.assertEqual(numbers, list(range(numbers[0], 30)))

    def test_threads_keep_entries_whole(self):
        log = self.log(max_bytes=1 << 30)

        def write(n):
            for i in range(200):
                log("OUT", f"thread {n} entry {i}\nsecond line {n} {i}")
        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        got = lines(self.path)
        self.assertEqual(len(got), 8 * 200 * 2)
        for first, second in zip(got[::2], got[1::2]):
            n, i = re.search(r"thread (\d) entry (\d+)$", first).groups()
            self.assertEqual(second, f"    second line {n} {i}")

    def test_close_lets_the_file_go(self):
        log = self.log()
        log("INFO", "one")
        log.close()
        moved = self.path.with_name("moved.log")
        os.replace(self.path, moved)                                  # Windows refuses this while a handle is open
        log("INFO", "after close")                                    # still written, the file opened for it alone
        os.replace(self.path, self.path.with_name("again.log"))
        self.assertEqual(lines(self.path.with_name("again.log"))[0][9:], "INFO after close")

    def test_a_debuglog_is_what_debug_writer_gives(self):
        self.assertIsInstance(self.log(), DebugLog)
        self.assertEqual(self.logs[-1].max_bytes, debuglog.MAX_BYTES)
        self.assertEqual(debuglog.MAX_BYTES, 10 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
