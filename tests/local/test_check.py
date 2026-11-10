"""The gate before anything of a local extension runs, and its check (host.py allow / approve, check.py), with real
processes of the fixture and the real registry. approved: who the registry was told approved what, [(id, by)];
change(id): its manifest starts it with something else now (what the player confirmed is not what would run)."""
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import process as P

from .util import HostCase, gone, make_extension, until

DATA_TEST = ("import os\nimport unittest\nfrom pathlib import Path\n\n\nclass T(unittest.TestCase):\n"
             "    def test_it(self):\n        d = Path(os.environ['WUXIAN_EXTENSION_DATA'])\n"
             "        self.assertEqual((d.is_dir(), list(d.iterdir())), (True, []))\n"
             "        (d / 'written.txt').write_text('x', encoding='utf-8')\n")
A_TEST = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_it(self):\n        self.assertTrue(True)\n"


class GateCase(HostCase):
    def setUp(self):
        super().setUp()
        self.changes = 0
        self.approving = mock.patch.object(self.host.reg, "approve", wraps=self.host.reg.approve).start()
        self.spawn = mock.patch.object(self.host, "spawn", wraps=self.host.spawn).start()
        self.tests = mock.patch.object(self.host, "run_tests", wraps=self.host.run_tests).start()
        self.addCleanup(mock.patch.stopall)

    @property
    def approved(self):
        return [tuple(c.args) for c in self.approving.call_args_list]

    def change(self, eid="fx"):
        """its manifest's env has one more variable: another command than the one that was confirmed"""
        file = self.root / "ext" / eid / "wuxian-extension.json"
        manifest = json.loads(file.read_text(encoding="utf-8"))
        self.changes += 1
        manifest["env"]["FIXTURE_CHANGED"] = "x" * self.changes
        file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def refused(self, fn, *args):
        with self.assertRaises(ApiError) as cm:
            fn(*args)
        err = cm.exception
        self.assertEqual((err.status, err.code), (403, "needs_approval"))
        self.assertIn("The player confirms it on the Extensions page of the WuxianWorkshop App; nothing of it runs "
                      "before that", err.message)
        return err

    def check_pids(self, eid="fx"):
        """the pids of the processes a check started, by its check.log"""
        path = self.host.root / "extensions" / eid / "check.log"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        return [int(n) for n in re.findall(r"started \(pid (\d+)\)", text)]


class Approval(GateCase):
    def test_nothing_runs_that_is_not_approved(self):
        self.auto_now = False
        row, folder = self.add(tests={"test_ok.py": A_TEST})
        self.assertEqual((row["state"], row["approval"], row["pid"]), ("stopped", "needed", None))
        fut = self.host.ensure("fx")
        self.assertTrue(fut.done())                                    # refused at once
        err = fut.exception()
        self.assertEqual((err.status, err.code, err.extra["approval"]), (403, "needs_approval", "needed"))
        self.assertIn("fx is new: the player has not confirmed the command it starts with", err.message)
        for fn, args in ((self.host.check, ("fx",)), (self.host.check, (str(folder),)), (self.host.load_, ("fx",)),
                         (self.host.call_sync, ("fx.echo", {"text": "a"}))):
            err = self.refused(fn, *args)
            self.assertEqual(err.extra["approval"], "needed")
        self.assertIsNotNone(self.host.describe("fx").exception(1))    # not even to learn what it can do
        # the command changed since the player confirmed it: the same, said so
        self.host.reg.approve("fx", "player")                          # confirmed (the registry only: no start)
        self.change()
        err = self.refused(self.host.check, "fx")
        self.assertEqual(err.extra["approval"], "changed")
        self.assertIn("fx changed the command it starts with since the player confirmed it", err.message)
        self.assertEqual(self.host.ensure("fx").exception(1).extra["approval"], "changed")
        row = self.host.get("fx")
        self.assertEqual((row["state"], row["approval"], row["capabilities"]), ("stopped", "changed", []))
        # nothing of it ran, and nothing approved it but the test
        self.assertEqual((self.spawn.call_count, self.tests.call_count, self.approved), (0, 0, [("fx", "player")]))
        self.assertEqual((self.pids(), self.check_pids(), self.host.procs, self.host.starting), ([], [], {}, {}))

    def test_one_that_starts_with_the_workshop(self):
        self.auto_now = False
        row, _ = self.add(start="workshop")                            # registered, not started
        self.assertEqual((row["state"], row["approval"]), ("stopped", "needed"))
        self.host.autostart()
        time.sleep(0.3)
        self.assertEqual((self.spawn.call_count, self.host.get("fx")["state"]), (0, "stopped"))
        row = self.host.approve("fx")                                  # the player's yes starts it for good
        self.assertEqual((self.approved, row["approval"]), ([("fx", "player")], "ok"))
        self.assertTrue(until(lambda: self.host.get("fx")["state"] == "running"))
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "a"})["text"], "a")
        self.assertEqual(len(self.pids()), 1)

    def test_the_player_approves(self):
        self.auto_now = False
        self.add()
        self.refused(self.host.call_sync, "fx.echo", {"text": "a"})
        row = self.host.approve("fx")
        self.assertEqual((self.approved, row["approval"]), ([("fx", "player")], "ok"))
        # start "use": it is started only to say what it can do, so its tools and options are there before a call
        self.assertTrue(until(lambda: self.host.get("fx")["capabilities"] and self.host.get("fx")["state"] == "stopped"))
        row = self.host.get("fx")
        self.assertIn("fx.echo", row["capabilities"])
        self.assertEqual(([o["name"] for o in row["options"]], row["pid"], row["error"]), (["volume"], None, None))
        self.assertTrue(gone(self.pids()[0]))
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "a"})["text"], "a")
        self.assertTrue(self.host.check("fx")["ok"])
        # approved again (its command changed): it has described itself, so nothing is started for that
        self.host.stop("fx")
        self.change()
        self.refused(self.host.call_sync, "fx.echo", {"text": "a"})
        self.host.approve("fx")
        time.sleep(0.3)
        self.assertEqual((self.host.get("fx")["state"], len(self.pids()), self.approved[-1]),
                         ("stopped", 2, ("fx", "player")))
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "b"})["text"], "b")
        with self.assertRaises(ApiError) as cm:
            self.host.approve("nope")
        self.assertEqual(cm.exception.code, "not_found")

    def test_the_workshop_approves_as_it_goes(self):
        self.add()
        self.assertEqual(self.host.get("fx")["approval"], "needed")
        self.assertEqual(self.host.call_sync("fx.echo", {"text": "a"})["text"], "a")
        self.assertEqual(self.approved, [("fx", "developer")])
        self.assertTrue(any("local extension fx: its command is approved (the setting allows it by itself)" in n for n in self.notes))
        self.assertEqual(self.host.get("fx")["approval"], "ok")
        self.host.stop("fx")
        self.change()                                                  # its check and its reload too
        self.assertTrue(self.host.check("fx")["ok"])
        self.change()
        self.assertTrue(self.host.load_("fx")["check"]["ok"])
        self.assertEqual(self.approved, [("fx", "developer")] * 3)
        self.assertEqual(self.host.get("fx")["state"], "running")

    def test_a_folder_that_is_not_registered(self):
        folder = make_extension(self.root / "ext" / "loose", "loose", tests={"test_ok.py": A_TEST})
        broken = make_extension(self.root / "ext" / "broken", "broken")
        (broken / "wuxian-extension.json").write_text("{", encoding="utf-8")
        self.auto_now = False
        with self.assertRaises(ApiError) as cm:
            self.host.check(str(folder))
        err = cm.exception
        self.assertEqual((err.status, err.code, err.extra["approval"]), (403, "needs_approval", "needed"))
        self.assertIn("is not a registered extension: nothing of it runs before the player has confirmed", err.message)
        self.assertIn("extension_add registers it", err.message)
        self.assertEqual((self.spawn.call_count, self.tests.call_count, self.check_pids("loose")), (0, 0, []))
        res = self.host.check(str(broken))                             # a manifest is only read: its problems
        self.assertFalse(res["ok"])
        self.assertTrue(res["problems"])
        self.assertEqual(self.spawn.call_count, 0)
        self.auto_now = True                                           # the workshop approves by itself: checked
        res = self.host.check(str(folder))
        self.assertTrue(res["ok"], res["problems"])
        self.assertIn("loose.echo", res["capabilities"])
        self.assertEqual((self.spawn.call_count, self.tests.call_count, res["tests"]["failed"]), (1, 1, 0))
        self.assertEqual((self.host.rows(), self.approved), ([], []))  # and nothing is registered or approved by it
        self.assertTrue(gone(self.check_pids("loose")[0]))


class Check(GateCase):
    def test_no_second_process_of_one_that_runs(self):
        self.add()
        self.host.call_sync("fx.echo", {"text": "a"})
        pid = self.host.get("fx")["pid"]
        self.assertEqual(self.spawn.call_count, 1)
        res = self.host.check("fx")
        self.assertEqual((self.spawn.call_count, self.check_pids()), (1, []))            # none of its own
        self.assertEqual((res["ok"], res["running"]), (True, pid))
        self.assertIn("fx.echo", res["capabilities"])
        said = [p for p in res["problems"] if p["where"] == "process"]
        self.assertEqual([p["level"] for p in said], ["warning"])
        self.assertIn(f"fx is running (pid {pid}), so no second process of it was started", said[0]["message"])
        self.assertIn("extension_load fx", said[0]["hint"])
        row = self.host.get("fx")
        self.assertEqual((row["state"], row["pid"]), ("running", pid))                   # untouched
        # stopped: the check starts its own, and leaves the extension stopped
        self.host.stop("fx")
        res = self.host.check("fx")
        self.assertTrue(res["ok"], res["problems"])
        self.assertNotIn("running", res)
        self.assertFalse(any(p["where"] == "process" for p in res["problems"]))
        self.assertEqual((self.spawn.call_count, len(self.check_pids()), len(self.pids())), (2, 1, 1))
        self.assertTrue(gone(self.check_pids()[0]))
        row = self.host.get("fx")
        self.assertEqual((row["state"], row["pid"]), ("stopped", None))

    def test_a_check_while_it_starts(self):
        self.add()
        fut = self.host.ensure("fx")
        res = self.host.check("fx")                                    # waits for the start, then starts none
        proc = fut.result(1)
        self.assertEqual((res["ok"], res["running"], self.spawn.call_count, self.check_pids()),
                         (True, proc.pid, 1, []))
        self.assertEqual(self.host.get("fx")["pid"], proc.pid)

    def test_what_it_tells_and_asks_during_a_check(self):
        self.add(extra="early")
        res = self.host.check("fx")
        self.assertTrue(res["ok"], res["problems"])
        time.sleep(0.2)
        self.assertEqual(self.events, [])                              # its events go nowhere
        self.assertEqual(self.host.get("fx")["state"], "stopped")
        self.assertEqual(self.pids(), [])

    def test_the_check_ends_its_own_process(self):
        self.add(mode="nostop")
        with mock.patch.object(P, "SHUTDOWN_WAIT", 0.3):               # read when the check asks shutdown
            t0 = time.monotonic()
            res = self.host.check("fx")
            self.assertLess(time.monotonic() - t0, 8)
        self.assertTrue(res["ok"], res["problems"])
        self.assertTrue(gone(self.check_pids()[0]))

    def test_its_tests_get_a_data_folder_of_their_own(self):
        self.add(tests={"test_data.py": DATA_TEST})
        kept = P.data_folder("fx", self.host.root)
        res = self.host.check("fx")
        self.assertEqual((res["tests"]["ran"], res["tests"]["failed"]), (1, 0), res["tests"]["output"])
        self.assertFalse((kept / "written.txt").exists())              # not in the folder the extension keeps
        self.assertEqual([p for p in Path(tempfile.gettempdir()).glob("wuxian-ext-data-*") if p.is_dir()
                          and (p / "written.txt").exists()], [])       # and the temporary one is gone


if __name__ == "__main__":
    unittest.main()
