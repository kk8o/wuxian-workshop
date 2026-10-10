"""The MCP server keeps its clients current: a stdio process says in `status` when it runs older code than the program
(server.freshness: reconnect it), and clients of both protocol eras hear when WuxianKit's tools, resources and prompts
change (mcp/changes.py: the subscription bus for the 2026-07-28 wire, notifications for the handshake era)."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

import anyio

from wuxianworkshop.mcp import server


class Daemon:
    """cli.client's DaemonClient as HttpBackend calls it, for status"""

    def __init__(self, version):
        self.version = version

    def status(self):
        return {"daemon": {"version": self.version}, "link": {"state": "online"}}


class Freshness(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": self.home.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()

    def status(self, backend):
        mcp = server.build_server(backend)
        return asyncio.run(mcp.call_tool("status", {})).structured_content

    def test_current(self):
        res = self.status(server.HttpBackend(Daemon(server.__version__)))
        self.assertEqual(res["link"], {"state": "online"})
        self.assertEqual((res["mcp"]["version"], res["mcp"]["stale"]), (server.__version__, False))
        self.assertNotIn("action", res["mcp"])

    def test_a_newer_program(self):
        major, minor, patch = server.version_key(server.__version__)
        mcp = self.status(server.HttpBackend(Daemon(f"{major}.{minor}.{patch + 1}")))["mcp"]
        self.assertTrue(mcp["stale"])
        self.assertIn(f"still runs {server.__version__}", mcp["why"])
        self.assertIn("/mcp", mcp["action"])
        older = self.status(server.HttpBackend(Daemon("0.0.1")))["mcp"]          # an older program is not this one's fault
        self.assertFalse(older["stale"])

    def test_the_source_changed(self):
        with mock.patch.object(server, "LOADED", (1, 1)), mock.patch.object(server, "code_stamp", return_value=(1, 2)):
            mcp = self.status(server.HttpBackend(Daemon(server.__version__)))["mcp"]
        self.assertTrue(mcp["stale"])
        self.assertIn("source files changed", mcp["why"])
        with mock.patch.object(server, "LOADED", None):                         # frozen: only the version tells
            self.assertFalse(self.status(server.HttpBackend(Daemon(server.__version__)))["mcp"]["stale"])

    def test_not_in_the_daemon(self):
        class Service:                                                          # the daemon's own /mcp is the program itself
            async def status(self):
                return {"daemon": {"version": "99.0.0"}}

            def __getattr__(self, name):
                async def method(*args):
                    return {}
                return method
        self.assertNotIn("mcp", self.status(Service()))

    def test_the_code_stamp(self):
        stamp = server.code_stamp()
        self.assertEqual(stamp, server.code_stamp())
        self.assertGreater(stamp[0], 20)                                        # the package's .py files


class ListChanged(unittest.TestCase):
    """over memory streams, the way stdio serves: a client of the handshake era (the SDK's own client settles on
    2025-11-25) is told listChanged at initialize and hears the three notifications when the lists change"""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": self.home.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()

    def test_handshake_era(self):
        from mcp import ClientSession
        from mcp.shared.memory import create_client_server_memory_streams

        class Backend:
            def __getattr__(self, name):
                async def method(*args):
                    return {}
                return method

        mcp = server.build_server(Backend())
        heard, seen = [], {}

        async def handler(message):
            method = getattr(message, "method", None)
            if method:
                heard.append(method)

        async def main():
            low = mcp._lowlevel_server
            async with create_client_server_memory_streams() as (client, served):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(low.run, served[0], served[1], low.create_initialization_options())
                    async with ClientSession(client[0], client[1], message_handler=handler) as session:
                        init = await session.initialize()
                        seen["version"] = init.protocol_version
                        caps = init.capabilities
                        seen["listChanged"] = (caps.tools.list_changed, caps.resources.list_changed, caps.prompts.list_changed)
                        await session.list_tools()                             # the middleware meets the connection
                        await mcp.list_changes.tell(mcp)
                        with anyio.fail_after(5):
                            while len(heard) < 3:
                                await anyio.sleep(0.01)
                    tg.cancel_scope.cancel()

        asyncio.run(main())
        self.assertNotEqual(seen["version"], "2026-07-28")
        self.assertEqual(seen["listChanged"], (True, True, True))
        self.assertEqual(sorted(heard), ["notifications/prompts/list_changed", "notifications/resources/list_changed",
                                         "notifications/tools/list_changed"])

    def test_a_closed_connection_is_forgotten(self):
        """a handshake-era connection that closed leaves no session behind (the session holds its connection: weak keys
        alone would keep it for the life of the daemon)"""
        from mcp import Client
        from mcp_types import Implementation

        class Backend:
            def __getattr__(self, name):
                async def method(*args):
                    return {}
                return method

        mcp = server.build_server(Backend())
        seen = {}

        async def main():
            async with Client(mcp, mode="legacy", client_info=Implementation(name="old", version="1"), cache=None) as c:
                await c.list_tools()
                seen["open"] = len(mcp.list_changes.sessions)
        asyncio.run(main())
        self.assertEqual((seen["open"], len(mcp.list_changes.sessions)), (1, 0))

    def test_nobody_to_tell(self):
        mcp = server.build_server(server.HttpBackend(Daemon(server.__version__)))
        asyncio.run(mcp.list_changes.tell(mcp))                                 # no session, no listener: nothing breaks
        self.assertEqual(len(mcp.list_changes.sessions), 0)


if __name__ == "__main__":
    unittest.main()
