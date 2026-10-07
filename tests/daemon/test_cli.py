"""The `wuxian` command line without a daemon: argument parsing, the mcp-config snippets, quit when nothing runs."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from wuxianworkshop.cli import main as cli, mcpconfig


class Parser(unittest.TestCase):
    def parse(self, *argv):
        ns, rest = cli.build_parser().parse_known_args(list(argv))
        return ns, rest

    def test_client_commands(self):
        ns, rest = self.parse("run", "return", "1", "+", "1", "--timeout", "3")
        self.assertEqual((ns.command, ns.code, ns.timeout, rest), ("run", ["return", "1", "+", "1"], 3.0, []))
        ns, _ = self.parse("load", "Foo", "--no-reset")
        self.assertEqual((ns.target, ns.no_reset), ("Foo", True))
        ns, _ = self.parse("watch", "start", "Foo/Core.lua")
        self.assertEqual((ns.action, ns.target), ("start", "Foo/Core.lua"))
        ns, _ = self.parse("watch", "list")
        self.assertEqual((ns.action, ns.target), ("list", None))
        ns, _ = self.parse("snap", "--region", "1", "2", "3", "4")
        self.assertEqual(ns.region, [1, 2, 3, 4])
        ns, _ = self.parse("logs", "--follow", "--since", "12", "--kinds", "ERR,OUT")
        self.assertEqual((ns.follow, ns.since, ns.kinds), (True, 12, "ERR,OUT"))
        ns, _ = self.parse("say", "你好", "世界")
        self.assertEqual(ns.text, ["你好", "世界"])
        ns, _ = self.parse("install", "--no-clean", "--game", r"C:\Games\WoW")
        self.assertEqual((ns.no_clean, ns.game), (True, r"C:\Games\WoW"))
        ns, _ = self.parse("mcp-config", "codex")
        self.assertEqual(ns.host, "codex")

    def test_the_log_tail_pages_to_the_end_with_the_kinds(self):
        """`wuxian logs` without --since: the last 50 of the kinds asked for, however long the journal is"""
        from wuxianworkshop.daemon.journal import Journal
        j = Journal()
        for i in range(2600):
            j.add("ERR" if i % 10 == 0 else "OUT", f"line {i}")

        class Client:
            calls = []

            def logs(self, since=0, limit=200, kinds=None):
                self.calls.append((since, limit, kinds))
                entries, nxt, _ = j.since(since, limit, kinds.split(",") if kinds else None)
                return dict(entries=entries, next=nxt)

        ns, _ = self.parse("logs", "--kinds", "ERR")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_logs(Client(), ns), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 50)
        self.assertTrue(lines[-1].endswith("line 2590"), lines[-1])
        self.assertTrue(all(" ERR " in l for l in lines))
        ns, _ = self.parse("logs")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_logs(Client(), ns)
        self.assertTrue(out.getvalue().splitlines()[-1].endswith("line 2599"))      # past the first 1000

    def test_programs_keep_their_own_arguments(self):
        ns, rest = self.parse("serve", "--mode", "player", "--capture", "gdi")
        self.assertEqual((ns.command, rest), ("serve", ["--mode", "player", "--capture", "gdi"]))
        ns, rest = self.parse("companion", "--say", "hi", "--capture", "wgc")
        self.assertEqual((ns.command, rest), ("companion", ["--say", "hi", "--capture", "wgc"]))

    def test_bad_watch_action(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.parse("watch", "begin")

    def test_app_goes_to_the_shell(self):
        with mock.patch.object(cli, "run_shell", return_value=0) as shell:
            self.assertEqual(cli.main(["app", "--background", "--capture", "gdi"]), 0)
            shell.assert_called_once_with(["--background", "--capture", "gdi"])
            shell.reset_mock()
            self.assertEqual(cli.main([]), 0)
            shell.assert_called_once_with()


class BackgroundStart(unittest.TestCase):
    """what a client starts when no daemon answers: the desktop program with its window hidden, else serve"""

    def test_the_program_when_the_ui_is_there(self):
        from wuxianworkshop.cli import client
        with mock.patch.dict(os.environ, {"WUXIAN_HEADLESS": ""}), \
                mock.patch.object(client.importlib.util, "find_spec", return_value=object()):
            self.assertEqual(client.background_command(), ["app", "--background"])

    def test_serve_without_the_ui_or_when_asked(self):
        from wuxianworkshop.cli import client
        with mock.patch.dict(os.environ, {"WUXIAN_HEADLESS": ""}), \
                mock.patch.object(client.importlib.util, "find_spec", return_value=None):
            self.assertEqual(client.background_command(), ["serve"])
        with mock.patch.dict(os.environ, {"WUXIAN_HEADLESS": "1"}):
            self.assertEqual(client.background_command(), ["serve"])

    def test_spawn_leaves_the_job_when_it_may(self):
        """the daemon asks to leave the caller's job object; a job that forbids it (PermissionError) keeps it"""
        from wuxianworkshop.cli import client
        calls = []

        def popen(command, **kw):
            calls.append(kw["creationflags"])
            if len(calls) == 1 and kw["creationflags"] & client.CREATE_BREAKAWAY_FROM_JOB:
                raise PermissionError(5, "Access is denied")
            return "process"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"WUXIAN_HOME": tmp}), \
                mock.patch.object(client.sys, "platform", "win32"), mock.patch.object(client.subprocess, "Popen", popen):
            self.assertEqual(client.spawn_daemon(), "process")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0] & client.CREATE_BREAKAWAY_FROM_JOB)
        self.assertFalse(calls[1] & client.CREATE_BREAKAWAY_FROM_JOB)

    def test_refused_connection_is_daemon_gone(self):
        from wuxianworkshop.cli import client
        c = client.DaemonClient({"port": 1, "token": "x"}, timeout=10)     # nothing listens on port 1 (Windows says so
                                                                            # after about 2 s of retries)
        with self.assertRaises(client.DaemonGone):
            c.status()


class StdioReconnect(unittest.TestCase):
    """`wuxian mcp` keeps serving when the daemon restarts on a new port with a new token"""

    def make(self, first, second):
        from wuxianworkshop.mcp.server import HttpBackend
        calls = []

        def reconnect():
            calls.append(1)
            return second
        return HttpBackend(first, reconnect=reconnect), calls

    def test_daemon_gone_or_token_refused_is_sent_again(self):
        import asyncio
        from wuxianworkshop.cli.client import DaemonGone
        from wuxianworkshop.daemon.api import ApiError

        class Gone:
            def status(self):
                raise DaemonGone("refused")

        class OldToken:
            def status(self):
                raise ApiError(401, "unauthorized", "bad token")

        class Fresh:
            def status(self):
                return {"daemon": "new"}
        for first in (Gone(), OldToken()):
            backend, calls = self.make(first, Fresh())
            self.assertEqual(asyncio.run(backend.status()), {"daemon": "new"})
            self.assertEqual(len(calls), 1)

    def test_other_failures_are_not_repeated(self):
        import asyncio
        from wuxianworkshop.daemon.api import ApiError

        class Broken:
            def run(self, *args):
                raise ConnectionError("reset while waiting")        # the code may have run: never send it twice
        backend, calls = self.make(Broken(), None)
        with self.assertRaises(ApiError) as e:
            asyncio.run(backend.run("return 1"))
        self.assertEqual((e.exception.code, calls), ("daemon_unreachable", []))


class McpConfig(unittest.TestCase):
    def test_claude_and_cursor_are_json(self):
        info = dict(port=1234, token="tok", mcp_url="http://127.0.0.1:1234/mcp")
        text = mcpconfig.snippet("claude", info, command=r"C:\Tools\wuxian.exe", args=["mcp"], env={"WUXIAN_LOG": r"C:\x\mcp.log"})
        body = text[text.index("{"):text.index("\n# the same")]
        cfg = json.loads(body)
        self.assertEqual(cfg["mcpServers"]["wuxian"], {"type": "stdio", "command": r"C:\Tools\wuxian.exe", "args": ["mcp"],
                                                       "env": {"WUXIAN_LOG": r"C:\x\mcp.log"}})
        self.assertEqual(cfg["mcpServers"]["wuxian-http"]["url"], "http://127.0.0.1:1234/mcp")
        self.assertEqual(cfg["mcpServers"]["wuxian-http"]["headers"]["Authorization"], "Bearer tok")
        self.assertIn("claude mcp add --transport stdio --scope user wuxian", text)
        cursor = mcpconfig.snippet("cursor", None, command="wuxian", args=["mcp"], env={})
        cfg = json.loads(cursor[cursor.index("{"):])
        self.assertEqual(cfg, {"mcpServers": {"wuxian": {"command": "wuxian", "args": ["mcp"], "env": {}}}})

    def test_codex_toml(self):
        text = mcpconfig.snippet("codex", None, command=r"C:\Tools\wuxian.exe", args=["mcp"], env={"WUXIAN_LOG": r"C:\x\mcp.log"})
        self.assertIn("[mcp_servers.wuxian]\ncommand = 'C:\\Tools\\wuxian.exe'\nargs = [\"mcp\"]", text)
        self.assertIn("WUXIAN_LOG = 'C:\\x\\mcp.log'", text)
        self.assertNotIn("wuxian_http", text)                         # no daemon: no http entry

    def test_command_line_output(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"WUXIAN_HOME": tmp}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["mcp-config", "claude"])
            self.assertEqual(code, 0)
            self.assertIn('"mcpServers"', out.getvalue())
            self.assertIn('"mcp"', out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main(["mcp-config"])
            self.assertIn("[mcp_servers.wuxian]", out.getvalue())
            self.assertIn("Cursor", out.getvalue())

    def test_quit_without_a_daemon(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"WUXIAN_HOME": tmp}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["quit"]), 0)
            self.assertIn("not running", out.getvalue())


if __name__ == "__main__":
    unittest.main()
