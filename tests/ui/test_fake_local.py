"""The fake daemon's local extensions (scripts/dev_fake_api.py): /api/local* in the shapes of contract5 (sections 5, 7,
8, 11), so the 扩展 page can be looked at in each state. A row carries adds, approval, since, why and command (and no
annotate or extends); the pretend extensions cover the states the page words differently; with the setting
extension_approval "ask" (the default) a start or a check of one that waits answers 403 needs_approval until POST
/api/local/approve, with "auto" every row is ok, whatever the mode; a pretend output.log starts again at line 1 once it
is full (--rotate), and its logs answer `reset` as Host.logs."""
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from tests.ui.test_fake_api import AUTH, F

ROW = {"id", "title", "version", "path", "enabled", "start", "state", "pid", "started", "exit_code", "error", "capabilities",
       "sources", "options", "apps", "skills", "adds", "approval", "allowed", "since", "why", "command"}
STATES = {"off", "stopped", "starting", "running", "hung", "stopping", "exited", "backoff"}


class Local(unittest.TestCase):
    mode, approval = "developer", "ask"          # the mode has no part in it: the one that used to allow everything

    def setUp(self):
        self.fake = F.Fake(token="t0ken", seed=1, mode=self.mode, extension_approval=self.approval, rotate=12)
        self.addCleanup(self.fake.close)
        self.addCleanup(F.shutil.rmtree, self.fake.new_addons, True)
        self.client = TestClient(F.create_app(self.fake, ticker=False), base_url="http://127.0.0.1")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def rows(self):
        r = self.client.get("/api/local", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        return {row["id"]: row for row in r.json()["extensions"]}

    def post(self, action, **body):
        return self.client.post("/api/local/" + action, headers=AUTH, json=body)


class Ask(Local):
    def test_the_rows(self):
        rows = self.rows()
        for row in rows.values():
            self.assertEqual(set(row), ROW, row["id"])
            self.assertIn(row["state"], STATES)
            self.assertIn(row["approval"], ("ok", "needed", "changed"))
            self.assertIsInstance(row["since"], (int, float))
            self.assertTrue(row["why"] is None or isinstance(row["why"], str))
            self.assertTrue(row["command"] is None or all(isinstance(c, str) for c in row["command"]))
        seen = {(row["state"], row["approval"]) for row in rows.values()}
        for pair in (("running", "ok"), ("stopped", "ok"), ("exited", "ok"), ("off", "ok"), ("stopped", "needed"),
                     ("stopped", "changed"), ("hung", "ok"), ("backoff", "ok")):
            self.assertIn(pair, seen)
        self.assertEqual((rows["ocr"]["state"], rows["sync"]["state"]), ("hung", "backoff"))
        for eid in ("ocr", "sync", "translate"):
            self.assertTrue(rows[eid]["why"], eid)
        self.assertEqual((rows["translate"]["state"], rows["translate"]["exit_code"]), ("exited", 1))
        draft = rows["draft"]                                           # a manifest that cannot be read
        self.assertEqual((draft["state"], draft["command"], draft["title"]), ("stopped", None, "draft"))
        self.assertTrue(draft["error"])

    def test_adds_by_app(self):
        rows = self.rows()
        facts = rows["facts"]["adds"]
        self.assertEqual(sorted(facts), ["companion", "guild"])
        self.assertEqual(facts["guild"]["wake"], {"call": "facts.lookup", "timeout": 3.0})
        self.assertTrue(facts["guild"]["skill"].startswith(rows["facts"]["path"] + "\\"))        # an absolute path
        self.assertEqual(facts["companion"]["wake"], None)
        self.assertEqual(rows["voice"]["adds"], {})
        manifest = self.client.get("/api/local/manifest", headers=AUTH).json()["extensions"]
        for e in manifest:
            self.assertNotIn("annotate", e)
            self.assertNotIn("extends", e)
        self.assertEqual({e["id"]: sorted(e["adds"]) for e in manifest if e["adds"]},
                         {"notes": ["guild"], "facts": ["companion", "guild"]})

    def test_a_start_waits_for_the_player(self):
        r = self.post("load", id="facts")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "needs_approval")
        self.assertIn("扩展", r.json()["error"]["message"])
        r = self.post("check", id="stats")                               # its command changed: a check runs it too
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "needs_approval"))
        self.assertEqual(self.rows()["facts"]["state"], "stopped")
        r = self.post("approve", id="facts")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()), ROW)                             # the row, as /api/local/set answers
        self.assertEqual((r.json()["id"], r.json()["approval"], r.json()["state"]), ("facts", "ok", "stopped"))
        r = self.post("load", id="facts")
        self.assertEqual(r.status_code, 200)
        row = r.json()["row"]
        self.assertEqual((row["state"], row["approval"], row["why"]), ("running", "ok", None))
        self.assertGreaterEqual(row["since"], row["started"])
        self.assertEqual(self.rows()["stats"]["approval"], "changed")    # one allowed: the others still wait

    def test_approve_answers(self):
        self.assertEqual(self.post("approve", id="nope").status_code, 404)
        r = self.post("approve", id="draft")                             # no command to allow
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "bad_request"))
        self.assertEqual(self.post("approve", id="voice").json()["approval"], "ok")              # allowed already: the row
        self.assertEqual(self.client.post("/api/local/approve", json={"id": "facts"}).status_code, 401)

    def test_one_added_waits(self):
        r = self.post("add", path="C:\\Users\\me\\mine")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["id"], r.json()["approval"], r.json()["state"]), ("mine", "needed", "stopped"))
        self.assertEqual(self.post("load", id="mine").status_code, 403)

    def test_enabling_starts_only_what_is_allowed(self):
        self.post("set", id="facts", enabled=False)
        self.assertEqual(self.rows()["facts"]["state"], "off")
        r = self.post("set", id="facts", enabled=True)                   # starts with the workshop, not allowed: it waits
        self.assertEqual((r.json()["state"], r.json()["approval"]), ("stopped", "needed"))
        self.post("approve", id="facts")
        self.post("set", id="facts", enabled=False)
        self.assertEqual(self.post("set", id="facts", enabled=True).json()["state"], "running")

    def test_states_move(self):
        r = self.post("load", id="sync")                                 # it fails and starts with the workshop
        self.assertEqual((r.json()["row"]["state"], r.json()["row"]["exit_code"]), ("backoff", 1))
        self.assertIn("next try", r.json()["row"]["why"])
        r = self.post("stop", id="sync")                                 # no longer tried
        self.assertEqual((r.json()["state"], r.json()["why"]), ("stopped", None))
        r = self.post("load", id="translate")                            # it fails and starts on use: no retry
        self.assertEqual(r.json()["row"]["state"], "exited")
        self.assertTrue(r.json()["row"]["why"])
        r = self.post("stop", id="ocr")                                  # one that does not answer is killed
        self.assertEqual((r.json()["state"], r.json()["pid"], r.json()["exit_code"]), ("stopped", None, 1))

    def test_the_setting_decides(self):
        before = self.fake.local_revision
        r = self.client.post("/api/settings", headers=AUTH, json={"extension_approval": "auto"})
        self.assertEqual((r.status_code, r.json()["extension_approval"]), (200, "auto"))
        self.assertGreater(self.fake.local_revision, before)             # the page reads the rows again
        self.assertEqual({row["approval"] for row in self.rows().values()}, {"ok"})
        self.assertEqual(self.client.get("/api/status", headers=AUTH).json()["daemon"]["extension_approval"], "auto")
        self.client.post("/api/settings", headers=AUTH, json={"extension_approval": "ask"})
        self.assertEqual(self.rows()["facts"]["approval"], "needed")
        self.assertEqual(self.post("load", id="facts").status_code, 403)

    def test_the_mode_does_not(self):
        before = self.fake.local_revision
        for mode in ("player", "developer"):
            r = self.client.post("/api/settings", headers=AUTH, json={"mode": mode})
            self.assertEqual((r.status_code, r.json()["mode"], r.json()["extension_approval"]), (200, mode, "ask"))
            self.assertEqual((self.rows()["facts"]["approval"], self.rows()["stats"]["approval"]), ("needed", "changed"))
            self.assertEqual(self.post("load", id="facts").status_code, 403)
        self.assertEqual(self.fake.local_revision, before)               # nothing of the rows changed

    def test_a_value_it_does_not_know(self):
        for bad in ("developer", "", None, True):
            r = self.client.post("/api/settings", headers=AUTH, json={"extension_approval": bad})
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "bad_request"), bad)
        self.assertEqual(self.client.get("/api/settings", headers=AUTH).json()["extension_approval"], "ask")

    def test_a_full_log_starts_again(self):
        def logs(since):
            r = self.client.get(f"/api/local/logs?id=voice&since={since}&limit=200", headers=AUTH)
            self.assertEqual(r.status_code, 200)
            return r.json()

        first = logs(0)
        self.assertEqual((first["reset"], first["next"]), (False, first["total"] + 1))
        self.assertLess(first["total"], 12)
        for i in range(12 - first["total"]):
            self.fake.local_write("voice", f"old {i}")
        full = logs(first["next"])
        self.assertEqual((full["total"], full["next"], full["reset"]), (12, 13, False))
        for i in range(3):
            self.fake.local_write("voice", f"new {i}")                   # the file is full: the first of these opens another
        moved = logs(full["next"])
        self.assertEqual((moved["reset"], moved["total"], moved["next"]), (True, 3, 4))
        self.assertEqual(moved["lines"], [{"n": i + 1, "text": f"new {i}"} for i in range(3)])
        self.assertEqual((logs(4)["lines"], logs(4)["reset"]), ([], False))


class Auto(Local):
    mode, approval = "player", "auto"            # nor does the player's mode ask

    def test_nothing_waits(self):
        rows = self.rows()
        self.assertEqual({row["approval"] for row in rows.values()}, {"ok"})
        self.assertEqual({row["state"] for row in rows.values()}, {"running", "stopped", "exited", "off", "hung", "backoff"})
        r = self.post("load", id="facts")                                # a start allows it by itself
        self.assertEqual((r.status_code, r.json()["row"]["state"]), (200, "running"))
        r = self.post("check", id="stats")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ok", r.json())
        self.assertEqual(self.post("load", id="draft").status_code, 400)                         # still no command to run
        self.assertEqual(self.client.get("/api/settings", headers=AUTH).json()["extension_approval"], "auto")


class CommandLine(unittest.TestCase):
    def test_the_defaults(self):
        fake = F.Fake(token="t0ken", seed=1)
        self.addCleanup(fake.close)
        self.addCleanup(F.shutil.rmtree, fake.new_addons, True)
        self.assertEqual((fake.mode, fake.extension_approval, fake.rotate), ("developer", "ask", 400))
        self.assertEqual(fake.settings["extension_approval"], "ask")
        text = Path(F.__file__).read_text(encoding="utf-8")
        for flag in ('"--mode"', '"--approval"', '"--rotate"', '"--quiet"', '"--token"', '"--port"'):
            self.assertIn(flag, text)

    def test_a_value_it_does_not_know_is_ask(self):
        fake = F.Fake(token="t0ken", seed=1, extension_approval="developer")
        self.addCleanup(fake.close)
        self.addCleanup(F.shutil.rmtree, fake.new_addons, True)
        self.assertEqual((fake.extension_approval, fake.settings["extension_approval"]), ("ask", "ask"))


if __name__ == "__main__":
    unittest.main()
