"""Who runs an app of a local extension's manifest (contract3 section 1): apps[].driver "agent" (the default, left
out of the declaration) or "local" (the extension's own program; the workshop keeps it open). What follows from
"local" is worked out, not written: the extension starts with the workshop (start "use" is an error), requires.kit is
0.7.0 at least, minutes is an error; no prompt needed (one given is a warning); any other driver an error. Through the
host, with the fixture as the program: the apps it names when it answers initialize must be the manifest's (else it
does not start, and check says so), driven() while it runs, a wake handed to it, its reports checked and passed on."""
import json
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from tests.local.util import HostCase, make_extension, until
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import adds as A
from wuxianworkshop.local import host as H
from wuxianworkshop.local import manifest as M
from wuxianworkshop.local.rpc import RpcError

LOCAL = {"app": "cocreate", "title": "数据共创", "driver": "local", "hear": ["cocreate:snapshot"],
         "controls": ["stop", {"cmd": "submit", "label": "立即提交", "call": "echo"}]}


def found(problems, level):
    return [(p["where"], p["message"]) for p in problems if p["level"] == level]


class Driver(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()
        (self.folder / "AGENT.md").write_text("# x", encoding="utf-8")

    def check(self, **fields):
        data = dict({"id": "cocreate", "title": "共创", "run": ["python", "main.py"]}, **fields)
        data = {k: v for k, v in data.items() if v is not None}
        (self.folder / M.MANIFEST).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return M.check_manifest(self.folder)

    def test_local_is_kept_and_declared(self):
        m, problems = self.check(apps=[dict(LOCAL, link="live")])
        self.assertEqual(problems, [])                              # no prompt, no start, no requires: none missed
        app = m["apps"][0]
        self.assertEqual((app["driver"], M.driven(m)), ("local", ["cocreate"]))
        decl = A.declaration(app, "cocreate", "共创")               # the link goes to WuxianKit too
        self.assertEqual(decl, {"app": "cocreate", "title": "数据共创", "driver": "local", "link": "live",
                                "hear": ["cocreate:snapshot"],
                                "controls": ["stop", {"cmd": "submit", "label": "立即提交"}],
                                "local": {"id": "cocreate", "title": "共创"}})

    def test_agent_is_the_default_and_left_out(self):
        m, problems = self.check(apps=[{"app": "talk", "prompt": "talk", "driver": "agent"}, {"app": "chat"}])
        self.assertEqual(problems, [])
        self.assertEqual([("driver" in a) for a in m["apps"]], [False, False])
        self.assertNotIn("driver", A.declaration(m["apps"][0], "cocreate", "共创"))
        self.assertEqual((m["start"], m["requires"], M.driven(m)), ("use", {}, []))   # nothing follows from it

    def test_what_follows_from_local(self):
        for requires, start, kit in ((None, None, "0.7.0"), ({"workshop": "0.10.0"}, "workshop", "0.7.0"),
                                     ({"kit": "0.6.0"}, None, "0.7.0"), ({"kit": "0.7"}, None, "0.7.0"),
                                     ({"kit": "0.7.0"}, None, "0.7.0"), ({"kit": "0.7.2"}, None, "0.7.2"),
                                     ({"kit": "1.0"}, "workshop", "1.0")):
            m, problems = self.check(apps=[LOCAL], requires=requires, start=start)
            self.assertEqual(problems, [], (requires, start))
            self.assertEqual((m["start"], m["requires"].get("kit")), ("workshop", kit), (requires, start))
            self.assertEqual(m["requires"].get("workshop"), (requires or {}).get("workshop"))
        self.assertEqual(M.DRIVER_KIT, "0.7.0")
        m, problems = self.check(apps=[LOCAL], start="use")         # it cannot start at a call: nobody calls it
        self.assertIsNone(m)
        self.assertEqual([w.rpartition(": ")[2] for w, _ in found(problems, "error")], ["start"])
        self.assertIn("cocreate", found(problems, "error")[0][1])
        m, problems = self.check(apps=[dict(LOCAL, minutes=120)])   # it has no end
        self.assertIsNone(m)
        self.assertEqual([w.rpartition(": ")[2] for w, _ in found(problems, "error")], ["apps[0].minutes"])
        m, problems = self.check(apps=[{"app": "talk", "prompt": "talk", "minutes": 120}])   # an agent's may
        self.assertEqual(problems, [])
        m, problems = self.check(apps=[dict(LOCAL, prompt="cocreate")])
        self.assertIsNotNone(m)
        self.assertEqual([w.rpartition(": ")[2] for w, _ in found(problems, "warning")], ["apps[0].prompt"])

    def test_another_driver_is_an_error(self):
        for bad in ("program", "prompt", "", 1, True, ["local"]):
            m, problems = self.check(apps=[dict(LOCAL, driver=bad)])
            self.assertIsNone(m, bad)
            self.assertEqual([w.rpartition(": ")[2] for w, _ in found(problems, "error")], ["apps[0].driver"], bad)
        m, problems = self.check(apps=[dict(LOCAL, temp=True)])     # temp and launch are still not the manifest's
        self.assertIsNone(m)
        self.assertIn("leave out temp", found(problems, "error")[0][1])
        self.assertEqual(M.NOT_IN_APP, ("temp", "launch"))

    def test_the_program_and_the_manifest_name_the_same_apps(self):
        m, _ = self.check(apps=[LOCAL, {"app": "talk", "prompt": "talk"}])
        self.assertIsNone(M.driven_problem(m, {"apps": ["cocreate"]}))
        for described, words in (({"apps": []}, "does not name"), ({}, "does not name"), (None, "does not name"),
                                 ({"apps": ["cocreate", "talk"]}, "its program drives the app talk"),
                                 ({"apps": ["other"]}, "its program drives the app other")):
            p = M.driven_problem(m, described)
            self.assertEqual((p["level"], p["where"]), ("error", "initialize: apps"), described)
            self.assertIn(words, p["message"], described)
            self.assertTrue(p["hint"])
        m, _ = self.check(apps=[{"app": "talk", "prompt": "talk"}])
        self.assertIsNone(M.driven_problem(m, {"apps": []}))
        self.assertIn("its program drives the app talk", M.driven_problem(m, {"apps": ["talk"]})["message"])


class Declared(unittest.TestCase):
    """through the host: the declaration of an enabled extension's local app carries driver local"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(self.root / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.host = H.Host(self.root / "state", version="9.9.9", on_call=self.on_call, python=sys.executable,
                           sdk=self.root / "sdk", auto=lambda: True)
        self.addCleanup(self.host.close)

    def on_call(self, eid, cap, args, chain=None):
        fut = Future()
        fut.set_exception(ApiError(503, "down", "the game is not online"))
        return fut

    def test_declarations(self):
        folder = make_extension(self.root / "ext" / "fx", "fx", apps=[dict(LOCAL), {"app": "talk", "prompt": "talk"}])
        row = self.host.add(str(folder))                             # it starts with the workshop: at once
        self.assertEqual((row["start"], row["requires"]), ("workshop", {"kit": "0.7.0"}))
        decls = dict((d["app"], d) for _, d in self.host.declarations())
        self.assertEqual(decls["cocreate"]["driver"], "local")
        self.assertNotIn("driver", decls["talk"])
        self.assertEqual(self.host.control_call("cocreate", "submit"), {"ext": "fx", "call": "fx.echo", "args": {}})
        self.assertEqual(self.host.drivers(), {"cocreate": "fx"})
        self.assertTrue(until(lambda: self.host.get("fx")["state"] == "running"), self.host.get("fx"))


class Driven(HostCase):
    """a program that drives an app: what the host knows of it, hands it and takes from it"""

    def setUp(self):
        super().setUp()
        self.reports = []
        self.host.on_report = lambda eid, report: self.reports.append((eid, report))

    def running(self, **fields):
        row, folder = self.add(apps=[dict(LOCAL)], **fields)
        self.assertTrue(until(lambda: self.host.get("fx")["state"] == "running"), self.host.get("fx"))
        return folder

    def test_driven_while_it_runs(self):
        self.assertEqual(self.host.driven(), {})
        self.running()
        self.assertEqual((self.host.driven(), self.host.is_running("fx")), ({"cocreate": "fx"}, True))
        self.assertEqual(self.host.entry("fx")["described"]["apps"], ["cocreate"])
        self.assertIn("drives the app cocreate", " ".join(self.output()))
        self.assertTrue(self.host.roster()[0]["running"])
        self.host.stop("fx")
        self.assertEqual((self.host.driven(), self.host.is_running("fx")), ({}, False))
        self.assertEqual(self.host.drivers(), {"cocreate": "fx"})    # it is still its app, with nobody to feed it
        self.assertFalse(self.host.roster()[0]["running"])
        self.host.set("fx", enabled=False)
        self.assertEqual(self.host.drivers(), {})

    def test_a_wake_is_handed_to_it(self):
        self.running()
        wake = {"app": "cocreate", "seq": 3, "reason": "item", "items": [{"id": 7, "kind": "snapshot"}],
                "skipped": 0, "after": 6, "next": 7}
        self.assertEqual(self.host.wake("fx", "cocreate", wake).result(10), {})
        self.assertEqual(self.host.call_sync("fx.wakes"), [{"app": "cocreate", "wake": wake}])
        with self.assertRaises(RpcError) as cm:                      # its program answered an error
            self.host.wake("fx", "cocreate", dict(wake, items=[{"id": 8, "kind": "fail"}])).result(10)
        self.assertIn("the wake failed", cm.exception.message)
        fut = self.host.wake("fx", "cocreate", dict(wake, items=[{"id": 9, "kind": "die"}]))
        with self.assertRaises(ApiError) as cm:                      # it went without an answer
            fut.result(10)
        self.assertEqual(cm.exception.code, "down")
        self.assertTrue(until(lambda: not self.host.is_running("fx")))
        with self.assertRaises(ApiError) as cm:                      # and one that does not run gets none
            self.host.wake("fx", "cocreate", wake).result(10)
        self.assertEqual(cm.exception.code, "down")

    def test_a_program_that_names_other_apps_does_not_start(self):
        for drives, words in (([], "does not name"), (["cocreate", "ghost"], "its program drives the app ghost")):
            eid = "fx" + str(len(drives))
            folder = make_extension(self.root / "ext" / eid, eid, apps=[dict(LOCAL)], drives=drives or ["x"])
            if not drives:                                           # (the fixture names none at all)
                data = json.loads((folder / M.MANIFEST).read_text(encoding="utf-8"))
                del data["env"]["FIXTURE_APPS"]
                (folder / M.MANIFEST).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.host.add(str(folder))
            self.assertTrue(until(lambda: self.host.get(eid)["state"] == "stopped" and self.host.get(eid)["error"]),
                            self.host.get(eid))
            row = self.host.get(eid)
            self.assertIn(words, row["error"])
            self.assertEqual((row["pid"], self.host.driven(), self.host.entry(eid)["described"]), (None, {}, None))
            self.assertTrue(any("it cannot be used" in line for line in self.output(eid)))
            res = self.host.check(eid)                               # and its check says the same
            self.assertFalse(res["ok"])
            self.assertIn(words, [p["message"] for p in res["problems"] if p["where"] == "initialize: apps"][0])
            fut = self.host.ensure(eid, "again")                     # no backoff: the next try says it again
            self.assertIn(words, str(fut.exception(30)))

    def test_its_reports(self):
        self.running()
        good = {"app": "cocreate", "key": "1794938824:Horde", "state": "run", "step": "submit",
                "text": "  已上传 \n 3 / 4 ", "done": 3, "total": 4, "data": {"n": 376000}}
        self.host.call_sync("fx.tell", {"report": good})
        self.assertTrue(until(lambda: self.reports))
        self.assertEqual(self.reports, [("fx", dict(good, text="已上传 3 / 4"))])
        for bad, words in ((dict(good, app="talk"), "it does not drive the app talk"),
                           (dict(good, state="busy"), "state 'busy'"),
                           (dict(good, key="k" * 65), "key"),
                           (dict(good, text="x" * 201), "text"),
                           (dict(good, step="Submit"), "step"),
                           (dict(good, data={"big": "x" * 2100}), "data"),
                           (dict(good, colour="red"), "a report has no colour"),
                           ("text", "a report is a JSON object")):
            self.host.call_sync("fx.tell", {"report": bad} if isinstance(bad, dict) else {"report": {"app": 1}})
        time.sleep(0.3)
        self.assertEqual(len(self.reports), 1)
        out = " | ".join(line for line in self.output() if "report dropped" in line)
        for words in ("it does not drive the app talk", "state 'busy'", "a report has no colour"):
            self.assertIn(words, out)


if __name__ == "__main__":
    unittest.main()
