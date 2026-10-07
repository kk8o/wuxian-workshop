"""The daemon's log ring (daemon/journal.py) and the RUN result parser (daemon/api.py)."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop.daemon.api import parse_run
from wuxianworkshop.daemon.journal import Journal


class ParseRun(unittest.TestCase):
    def test_ok_with_values(self):
        res = parse_run('17 ok =run (12 B, 0.3 ms): 42, "x", nil')
        self.assertEqual((res["ok"], res["job"], res["chunk"], res["bytes"], res["ms"]), (True, 17, "=run", 12, 0.3))
        self.assertEqual(res["values"], ["42", '"x"', "nil"])
        self.assertIsNone(res["note"])

    def test_ok_without_values_and_with_note(self):
        res = parse_run("18 ok @Interface/AddOns/Foo/Core.lua (300 B, 1.0 ms) (no namespace registered for Foo: it got a "
                        "new one, kept for its later loads)")
        self.assertEqual(res["values"], [])
        self.assertTrue(res["note"].startswith("(no namespace registered for Foo"))

    def test_table_dump_is_one_value(self):
        res = parse_run("19 ok =run (5 B, 0.1 ms): {\n  a = 1,\n  b = 2,\n}")
        self.assertEqual(len(res["values"]), 1)
        self.assertTrue(res["values"][0].startswith("{"))

    def test_error_with_stack(self):
        res = parse_run("20 error =run: [string \"=run\"]:1: boom\n[string \"=run\"]:1: in main chunk")
        self.assertEqual((res["ok"], res["job"], res["error"]), (False, 20, '[string "=run"]:1: boom'))
        self.assertEqual(res["stack"], '[string "=run"]:1: in main chunk')

    def test_error_chunk_has_no_colon(self):
        """seen in the game: "<job> error =run: run:1: ..." gave the chunk "=run:" """
        res = parse_run("1276798468 error =run: run:1: attempt to index local 't' (a nil value)\n[=run]:1: in main chunk")
        self.assertEqual((res["chunk"], res["error"]), ("=run", "run:1: attempt to index local 't' (a nil value)"))
        self.assertEqual(res["stack"], "[=run]:1: in main chunk")

    def test_chunk_names_with_spaces(self):
        """a file outside AddOns keeps its absolute path as the chunk name: spaces and parentheses included"""
        chunk = "@C:/Program Files (x86)/My Addons/Foo/Core.lua"
        res = parse_run(f"5 ok {chunk} (154 B, 0.2 ms): Foo, 1 (no namespace registered for Foo: it got a new one, "
                        "kept for its later loads) (OnUnload ok)")
        self.assertEqual((res["chunk"], res["bytes"], res["ms"], res["values"]), (chunk, 154, 0.2, ["Foo", "1"]))
        self.assertEqual(res["note"], "(no namespace registered for Foo: it got a new one, kept for its later loads) "
                                      "(OnUnload ok)")
        res = parse_run(f"6 error {chunk}: C:/Program Files (x86)/My Addons/Foo/Core.lua:3: boom\nstack")
        self.assertEqual((res["chunk"], res["error"], res["stack"]),
                         (chunk, "C:/Program Files (x86)/My Addons/Foo/Core.lua:3: boom", "stack"))

    def test_reset_hook_notes(self):
        res = parse_run("7 ok @Interface/AddOns/Foo/Extra.lua (102 B, 0.0 ms): done (OnReload error: oops)")
        self.assertEqual((res["values"], res["note"]), (["done"], "(OnReload error: oops)"))

    def test_not_a_result(self):
        self.assertIsNone(parse_run("load Foo: no such file or addon folder"))


class JournalRing(unittest.TestCase):
    def test_ids_cursor_kinds_and_truncation(self):
        t = [100.0]
        j = Journal(capacity=5, clock=lambda: t[0])
        for i in range(1, 8):
            t[0] += 1
            j.add("OUT" if i % 2 else "ERR", f"line {i}")
        entries, nxt, truncated = j.since(0)
        self.assertEqual([e["id"] for e in entries], [3, 4, 5, 6, 7])     # 1 and 2 fell out of the ring
        self.assertEqual(nxt, 8)
        self.assertTrue(truncated)
        entries, nxt, truncated = j.since(4, limit=2)
        self.assertEqual(([e["id"] for e in entries], nxt, truncated), ([4, 5], 6, False))
        entries, nxt, _ = j.since(nxt)
        self.assertEqual(([e["id"] for e in entries], nxt), ([6, 7], 8))
        entries, nxt, _ = j.since(0, kinds=["ERR"])
        self.assertEqual([e["text"] for e in entries], ["line 4", "line 6"])
        self.assertEqual(nxt, 8)
        entries, nxt, truncated = j.since(8)
        self.assertEqual((entries, nxt, truncated), ([], 8, False))

    def test_entry_fields(self):
        j = Journal(capacity=10, clock=lambda: 5.0)
        e = j.add("ERR", "Interface/AddOns/Foo/Core.lua:3: oops\nstack")
        self.assertEqual((e["id"], e["t"], e["kind"], e["addon"], e["job"]), (1, 5.0, "ERR", "Foo", None))
        e = j.add("RUN", "77 ok =run (1 B, 0.1 ms): 1")
        self.assertEqual((e["job"], e["addon"]), (77, None))
        e = j.add("RUN", "load Foo: no such file or addon folder")
        self.assertIsNone(e["job"])

    def test_debug_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "debug.log"
            j = Journal(path, capacity=10)
            j.add("ERR", "boom\nline 2")
            j.add("INFO", "note")
            text = path.read_text(encoding="utf-8")
            self.assertRegex(text, r"^\d\d:\d\d:\d\d ERR boom\n    line 2\n\d\d:\d\d:\d\d INFO note\n$")

    def test_subscribers_get_entries_on_their_loop(self):
        j = Journal(capacity=10)
        got = []

        async def main():
            loop = asyncio.get_running_loop()
            unsubscribe = j.subscribe(loop, got.append)
            j.add("OUT", "one")                 # from this thread: the callback is still scheduled on the loop
            await asyncio.sleep(0.01)
            unsubscribe()
            j.add("OUT", "two")
            await asyncio.sleep(0.01)

        asyncio.run(main())
        self.assertEqual([e["text"] for e in got], ["one"])


if __name__ == "__main__":
    unittest.main()
