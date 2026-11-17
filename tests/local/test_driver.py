"""Who runs an app of a local extension's manifest (fx2, contract2 section 2): apps[].driver "agent" (the default, left
out of the declaration) or "local" (the extension's own program: given to kit.app.declare, which WuxianKit 0.6.1
takes; a warning when requires.kit is older or missing, or start is not workshop; no prompt needed, one given a
warning); any other value an error."""
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from tests.local.util import make_extension
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import adds as A
from wuxianworkshop.local import host as H
from wuxianworkshop.local import manifest as M

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
        data = dict({"id": "cocreate", "title": "共创", "run": ["python", "main.py"], "start": "workshop",
                     "requires": {"workshop": "0.9.15", "kit": "0.6.1"}}, **fields)
        data = {k: v for k, v in data.items() if v is not None}
        (self.folder / M.MANIFEST).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return M.check_manifest(self.folder)

    def test_local_is_kept_and_declared(self):
        m, problems = self.check(apps=[LOCAL])
        self.assertEqual(problems, [])                              # no prompt, and none missed
        app = m["apps"][0]
        self.assertEqual(app["driver"], "local")
        decl = A.declaration(app, "cocreate", "共创")
        self.assertEqual(decl, {"app": "cocreate", "title": "数据共创", "driver": "local", "hear": ["cocreate:snapshot"],
                                "controls": ["stop", {"cmd": "submit", "label": "立即提交"}],
                                "local": {"id": "cocreate", "title": "共创"}})

    def test_agent_is_the_default_and_left_out(self):
        m, problems = self.check(apps=[{"app": "talk", "prompt": "talk", "driver": "agent"}, {"app": "chat"}],
                                 requires=None, start=None)
        self.assertEqual(problems, [])                              # nothing asks for kit 0.6.1 or start workshop
        self.assertEqual([("driver" in a) for a in m["apps"]], [False, False])
        self.assertNotIn("driver", A.declaration(m["apps"][0], "cocreate", "共创"))

    def test_what_a_local_driver_wants(self):
        for requires, start, prompt, wanted in (
                (None, "workshop", None, ["requires"]),
                ({"workshop": "0.9.15"}, "workshop", None, ["requires"]),
                ({"kit": "0.6.0"}, "workshop", None, ["requires"]),
                ({"kit": "0.6"}, "workshop", None, ["requires"]),
                ({"kit": "0.6.1.0"}, "workshop", None, []),
                ({"kit": "0.7"}, "workshop", None, []),
                ({"kit": "0.6.1"}, None, None, ["start"]),
                ({"kit": "0.6.1"}, "use", None, ["start"]),
                ({"kit": "0.6.1"}, "workshop", "cocreate", ["apps[0].prompt"])):
            m, problems = self.check(apps=[dict(LOCAL, prompt=prompt) if prompt else LOCAL], requires=requires,
                                     start=start)
            self.assertIsNotNone(m, problems)                       # warnings only: it is taken
            where = [w.rpartition(": ")[2] for w, _ in found(problems, "warning")]
            self.assertEqual(where, wanted, (requires, start, prompt, problems))
            self.assertEqual(m["apps"][0]["driver"], "local")
        _, problems = self.check(apps=[LOCAL], requires=None)
        message = found(problems, "warning")[0][1]
        self.assertIn("cocreate", message)
        self.assertIn("0.6.1", message)

    def test_another_driver_is_an_error(self):
        for bad in ("program", "prompt", "", 1, True, ["local"]):
            m, problems = self.check(apps=[dict(LOCAL, driver=bad)])
            self.assertIsNone(m, bad)
            self.assertEqual([w.rpartition(": ")[2] for w, _ in found(problems, "error")], ["apps[0].driver"], bad)
        m, problems = self.check(apps=[dict(LOCAL, temp=True)])     # temp and launch are still not the manifest's
        self.assertIsNone(m)
        self.assertIn("leave out temp", found(problems, "error")[0][1])
        self.assertEqual(M.NOT_IN_APP, ("temp", "launch"))


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
        folder = make_extension(self.root / "ext" / "fx", "fx", apps=[dict(LOCAL), {"app": "talk", "prompt": "talk"}],
                                requires={"kit": "0.6.1"}, start="use")      # start use: a warning, still declared
        self.host.add(str(folder))
        decls = dict((d["app"], d) for _, d in self.host.declarations())
        self.assertEqual(decls["cocreate"]["driver"], "local")
        self.assertNotIn("driver", decls["talk"])
        self.assertEqual(self.host.control_call("cocreate", "submit"), {"ext": "fx", "call": "fx.echo", "args": {}})


if __name__ == "__main__":
    unittest.main()
