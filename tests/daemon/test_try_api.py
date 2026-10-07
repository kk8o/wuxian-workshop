"""try over a real daemon: do something in the game and collect what came of it — the action's result, the errors with
stacks, prints, warnings and blocked actions of the window, the events that fired; the MCP tool and `wuxian try`. The
fake game reports what the code's "--emit KIND text" lines say."""
import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame, request
from wuxianworkshop.cli import main as cli
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server

ERROR = ("--emit ERR Interface/AddOns/Foo/Foo.lua:3: attempt to index a nil value\\n"
         "[string \"@Interface/AddOns/Foo/Foo.lua\"]:3: in function `Refresh'\n")


class Try(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(cls.tmp.name) / "home")})
        cls.env.start()
        cls.addons = Path(cls.tmp.name) / "AddOns"
        (cls.addons / "WoWBridge").mkdir(parents=True)
        MB.install(cls.addons, pool=64)
        cls.handle = server.start(worker_factory=lambda on_debug, log: FakeGame(cls.addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "T")

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def tryit(self, **body):
        return request(self.handle, "POST", "/api/try", body)

    def refused(self, body):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            request(self.handle, "POST", "/api/try", body)
        err = json.loads(cm.exception.read())["error"]
        cm.exception.close()
        return cm.exception.code, err

    def test_what_came_of_it(self):
        res = self.tryit(code=ERROR + "--emit OUT refreshed 3 bags\\nall done\n--emit BLOCKED ADDON_ACTION_BLOCKED Foo "
                                       "CastSpellByName()\n--emit WARN something odd\nFoo.Refresh()", seconds=0.5,
                         addon="Foo")
        self.assertEqual((res["ok"], res["complete"], res["action"]["ok"]), (False, True, True))
        err = res["errors"][0]
        self.assertEqual((err["message"], err["addon"]), ("Interface/AddOns/Foo/Foo.lua:3: attempt to index a nil value", "Foo"))
        self.assertIn("in function `Refresh'", err["stack"])
        self.assertEqual([p["text"] for p in res["prints"]], ["refreshed 3 bags", "all done"])
        self.assertEqual(res["blocked"][0]["message"], "ADDON_ACTION_BLOCKED Foo CastSpellByName()")
        self.assertEqual(res["warnings"][0]["message"], "something odd")
        self.assertTrue(res["summary"].startswith("the action ran (returned 42"), res["summary"])
        self.assertIn("1 error (Interface/AddOns/Foo/Foo.lua:3: attempt to index a nil value)", res["summary"])
        self.assertIn("1 blocked", res["summary"])
        self.assertIn("2 print lines", res["summary"])
        quiet = self.tryit(code="return 1", seconds=0)
        self.assertEqual((quiet["ok"], quiet["errors"], quiet["prints"]), (True, [], []))   # the window is the try's own
        self.assertIn("no errors", quiet["summary"])

    def test_a_failing_action_a_slash_command_and_events(self):
        res = self.tryit(code="error('boom')", seconds=0)
        self.assertFalse(res["ok"])
        self.assertEqual((res["errors"][0]["source"], res["action"]["ok"]), ("action", False))
        self.assertIn("the action failed", res["summary"])
        res = self.tryit(slash="/foo show", seconds=0)
        self.assertEqual((res["ok"], res["action"]["values"]), (True, ["shown"]))
        res = self.tryit(code="Foo.Open()", seconds=0.2, events="BAG_*")
        self.assertEqual(res["events"]["counts"], [{"event": "BAG_UPDATE", "count": 1}])
        self.assertEqual(res["events"]["registered"], 1)
        for body, code in (({"code": "x", "slash": "/x"}, 400), ({}, 400), ({"code": "x", "seconds": 31}, 400),
                           ({"slash": "foo"}, 400), ({"code": "x", "events": "COMBAT_LOG_EVENT_UNFILTERED"}, 400)):
            self.assertEqual(self.refused(body)[0], code, body)

    def test_the_mcp_tool_and_the_command_line(self):
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        async def go():
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {self.handle.token}"}) as http:
                async with Client(streamable_http_client(f"{self.handle.url.rstrip('/')}/mcp", http_client=http)) as c:
                    tools = {t.name: t for t in (await c.list_tools()).tools}
                    got = await c.call_tool("try", {"code": ERROR + "Foo.Refresh()", "seconds": 0.2})
                    return tools, got

        tools, got = asyncio.run(go())
        self.assertFalse(tools["try"].annotations.read_only_hint)
        self.assertEqual(set(tools["try"].input_schema["properties"]),
                         {"code", "slash", "seconds", "addon", "snap", "frame", "events"})
        data = json.loads(got.content[0].text)
        self.assertEqual((data["ok"], data["errors"][0]["addon"]), (False, "Foo"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["try", "--slash", "foo show", "--seconds", "0"])     # the / may be left out
        self.assertEqual(code, 0)
        self.assertIn("action ok (0.3 ms): shown", out.getvalue())
        self.assertIn("ok: the action ran (returned shown); then in 0 s: no errors, no prints", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["try", ERROR + "Foo.Refresh()", "--seconds", "0.2"])
        self.assertEqual(code, 1)
        self.assertIn("ERR     [Foo] Interface/AddOns/Foo/Foo.lua:3: attempt to index a nil value", out.getvalue())
        self.assertIn("        [string \"@Interface/AddOns/Foo/Foo.lua\"]:3: in function `Refresh'", out.getvalue())


if __name__ == "__main__":
    unittest.main()
