"""The `wuxian` command line without a daemon: argument parsing, the mcp-config snippets, quit when nothing runs."""
import contextlib
import io
import json
import os
import tempfile
import time
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

    def test_a_run_whole(self):
        """run --whole: the first value whole on stdout (a newline after it), asked for with whole; from a daemon before
        0.9.13 (no whole in its answer) the run's values, and a word on stderr"""
        ns, _ = self.parse("run", "--whole", "return", "BIG")
        self.assertEqual((ns.whole, ns.code), (True, ["return", "BIG"]))
        self.assertFalse(self.parse("run", "return 1")[0].whole)
        asked = []

        class Client:
            def __init__(self, res):
                self.res = res

            def run(self, code, timeout_ms=10000, addon=None, whole=False):
                asked.append((code, timeout_ms, whole))
                return self.res
        for res, out, err in ((dict(ok=True, whole=True, values=["长\n" * 3], cut=None), "长\n" * 3, ""),
                              (dict(ok=True, whole=True, values=["x"], cut=None), "x\n", ""),
                              (dict(ok=True, values=["a", "b"], cut=None), "a\nb\n", "older than 0.9.13")):
            o, e = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
                code = cli.cmd_run(Client(res), ns)
            self.assertEqual((code, o.getvalue()), (0, out))
            self.assertIn(err, e.getvalue())
        self.assertEqual(asked[0], ("return BIG", 10000, True))
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = cli.cmd_run(Client(dict(ok=False, error="the value is 17000 KB, more than the 16384 KB a whole run "
                                                         "brings back", stack="")), ns)
        self.assertEqual((code, o.getvalue()), (1, ""))
        self.assertIn("more than the 16384 KB", e.getvalue())

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

    def test_events_once_wakes_on_the_first_and_says_where_to_go_on(self):
        """`wuxian events --once`: waits (25 s a request) until events come, prints them and NEXT <id>, exits; without any
        until --max it prints NEXT and TIMEOUT"""
        class Client:
            def __init__(self, pages):
                self.pages, self.calls = pages, []

            def addon_events(self, addon, topic, since, limit, wait):
                self.calls.append((addon, topic, since, wait))
                return self.pages.pop(0) if self.pages else {"events": [], "next": since}

        e = {"t": 0, "addon": "WuxianKit", "topic": "guild.chat", "data": {"reason": "urgent", "items": []}}
        c = Client([{"events": [], "next": 7}, {"events": [e], "next": 9}])
        ns, _ = self.parse("events", "--addon", "WuxianKit", "--topic", "guild.chat", "--since", "-1", "--once", "--json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_events(c, ns), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual((json.loads(lines[0])["topic"], lines[-1]), ("guild.chat", "NEXT 9"))
        self.assertEqual([x[2] for x in c.calls], [-1, 7])
        self.assertTrue(all(0 < x[3] <= 25 for x in c.calls))
        ns, _ = self.parse("events", "--since", "3", "--once", "--max", "0")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_events(Client([]), ns), 0)
        self.assertEqual(out.getvalue().splitlines()[-2:], ["NEXT 3", "TIMEOUT"])

    def test_programs_keep_their_own_arguments(self):
        ns, rest = self.parse("serve", "--mode", "player", "--capture", "gdi")
        self.assertEqual((ns.command, rest), ("serve", ["--mode", "player", "--capture", "gdi"]))
        ns, rest = self.parse("companion", "--say", "hi", "--capture", "wgc")
        self.assertEqual((ns.command, rest), ("companion", ["--say", "hi", "--capture", "wgc"]))

    def test_bad_watch_action(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.parse("watch", "begin")

    def test_ext(self):
        ns, _ = self.parse("ext", "list", "--json")
        self.assertEqual((ns.command, ns.action, ns.json), ("ext", "list", True))
        ns, _ = self.parse("ext", "add", r"C:\e\voice")
        self.assertEqual((ns.action, ns.path), ("add", r"C:\e\voice"))
        for action in ("remove", "load", "stop", "check"):
            ns, _ = self.parse("ext", action, "voice")
            self.assertEqual((ns.action, ns.id), (action, "voice"))
        ns, _ = self.parse("ext", "set", "voice", "--off", "--option", "volume=80", "--option", "name=小明")
        self.assertEqual((ns.enabled, ns.option), (False, ["volume=80", "name=小明"]))
        self.assertEqual(dict(map(cli.option_value, ns.option)), {"volume": 80, "name": "小明"})
        ns, _ = self.parse("ext", "set", "voice", "--on")
        self.assertIs(ns.enabled, True)
        ns, _ = self.parse("ext", "set", "voice")
        self.assertIsNone(ns.enabled)
        ns, _ = self.parse("ext", "new", "voice", "--title", "声音", "--folder", r"C:\e\v")
        self.assertEqual((ns.id, ns.title, ns.folder), ("voice", "声音", r"C:\e\v"))
        ns, _ = self.parse("ext", "logs", "voice", "-f", "--since", "5", "--limit", "50")
        self.assertEqual((ns.action, ns.id, ns.follow, ns.since, ns.limit), ("logs", "voice", True, 5, 50))
        for argv in (("ext",), ("ext", "begin"), ("ext", "load"), ("ext", "set", "v", "--on", "--off"),
                     ("program", "list")):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.parse(*argv)
        with self.assertRaises(ValueError):
            cli.option_value("novalue")

    def test_wk_and_reaction(self):
        ns, _ = self.parse("wk", "voice.say", '{"text": "hi"}', "--timeout-ms", "5000")
        self.assertEqual((ns.command, ns.cap, ns.args, ns.timeout_ms), ("wk", "voice.say", '{"text": "hi"}', 5000))
        ns, _ = self.parse("reaction", "set", '{"hear": "game:combat", "call": "voice.play"}')
        self.assertEqual((ns.action, json.loads(ns.spec)["call"]), ("set", "voice.play"))
        ns, _ = self.parse("reaction", "clear", "--owner", "companion")
        self.assertEqual((ns.id, ns.owner), (None, "companion"))

        class Client:
            def __init__(self):
                self.asked = []

            def local_call(self, cap, args, timeout_ms):
                self.asked.append((cap, args, timeout_ms))
                return {"result": {"seconds": 1.5}}

            def reactions(self):
                return {"reactions": [dict(id="r1", hear="game:combat", call="voice.play", owner="companion",
                                           fired=2, error="not heard: away")]}

            def reaction_clear(self, id, owner):
                self.asked.append(("clear", id, owner))
                return {"cleared": 3}

        c, out = Client(), io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_wk(c, self.parse("wk", "voice.say", '{"text": "hi"}')[0]), 0)
            self.assertEqual(cli.cmd_reaction(c, self.parse("reaction", "list")[0]), 0)
            self.assertEqual(cli.cmd_reaction(c, self.parse("reaction", "clear")[0]), 0)
        self.assertEqual(c.asked, [("voice.say", {"text": "hi"}, None), ("clear", None, None)])
        lines = out.getvalue().splitlines()
        self.assertEqual(json.loads("\n".join(lines[:3])), {"seconds": 1.5})
        self.assertEqual(lines[3:], ["r1: game:combat -> voice.play (owner companion), fired 2; error: not heard: away",
                                     "cleared 3"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_wk(c, self.parse("wk", "voice.say", "{bad")[0]), 2)

    def test_ext_logs_follow(self):
        """`wuxian ext logs -f`: the new lines as they come; a file moved aside said once, its new lines from the
        first"""
        row = dict(id="voice", state="running")
        answers = [
            dict(lines=[dict(n=1, text="a"), dict(n=2, text="b")], next=3, total=2, reset=False, cut=False, row=row),
            dict(lines=[], next=3, total=2, reset=False, cut=False, row=row),
            dict(lines=[dict(n=1, text="new")], next=2, total=1, reset=True, cut=False, row=row),
        ]
        asked = []

        class Client:
            def extension_logs(self, eid, since, limit):
                asked.append((eid, since, limit))
                if not answers:
                    raise KeyboardInterrupt                   # Ctrl+C ends it
                return answers.pop(0)

        ns, _ = self.parse("ext", "logs", "voice", "-f")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(cli.time, "sleep"):
            self.assertEqual(cli.cmd_ext(Client(), ns), 0)
        self.assertEqual(out.getvalue().splitlines(), ["a", "b", "new"])
        self.assertIn("moved to output.1.log", err.getvalue())
        self.assertEqual([since for _, since, _ in asked], [0, 3, 3, 2])

    def test_app_goes_to_the_shell(self):
        with mock.patch.object(cli, "run_shell", return_value=0) as shell:
            self.assertEqual(cli.main(["app", "--background", "--capture", "gdi"]), 0)
            shell.assert_called_once_with(["--background", "--capture", "gdi"])
            shell.reset_mock()
            self.assertEqual(cli.main([]), 0)
            shell.assert_called_once_with()


class ExtLines(unittest.TestCase):
    """how `wuxian ext` prints a local extension: its state (off, stopped, starting, running, hung, stopping, exited,
    backoff) with since when, then what the player or the Agent has to know: its approval, why, its error"""

    NOW = 1762750000.0                                       # some noon, far from a midnight in any time zone

    def row(self, state, **more):
        return dict(dict(id="voice", title="声音", path=r"C:\e\voice", enabled=state != "off", state=state, pid=None,
                         exit_code=None, error=None, capabilities=["voice.say", "voice.stop"], approval="ok",
                         since=None, why=None), **more)

    def lines(self, state, **more):
        return cli.ext_lines(self.row(state, **more), self.NOW)

    def test_states(self):
        at = self.NOW - 600
        clock = time.strftime("%H:%M:%S", time.localtime(at))
        self.assertEqual(self.lines("off", since=at), [f"{'voice':<16} {'off':<34} 声音  [2 capabilities]  C:\\e\\voice"])
        self.assertEqual(self.lines("stopped", since=at)[0].split()[:2], ["voice", "stopped"])
        for state in ("starting", "running", "hung", "stopping"):
            self.assertIn(f" {state} since {clock} (pid 4242) ", self.lines(state, since=at, pid=4242)[0])
        self.assertIn(f" running (pid 4242) ", self.lines("running", pid=4242)[0])         # a row without since
        self.assertIn(f" exited with code 3 since {clock} ", self.lines("exited", since=at, exit_code=3, pid=4242)[0])
        self.assertIn(f" exited since {clock} ", self.lines("exited", since=at)[0])
        self.assertIn(f" backoff since {clock} ", self.lines("backoff", since=at)[0])

    def test_another_day(self):
        at = self.NOW - 3 * 86400
        self.assertIn(" running since " + time.strftime("%m-%d %H:%M", time.localtime(at)) + " ",
                      self.lines("running", since=at)[0])
        self.assertEqual(cli.ext_since(at, self.NOW), time.strftime("%m-%d %H:%M", time.localtime(at)))

    def test_what_is_wrong_with_it(self):
        self.assertEqual(len(self.lines("running", pid=1)), 1)
        needed = self.lines("stopped", approval="needed")
        self.assertEqual(len(needed), 2)
        self.assertIn("not approved yet: the player approves it in the 无限工坊 App", needed[1])
        changed = self.lines("stopped", approval="changed", why="it waits for the player's approval")
        self.assertIn("changed since the player approved it", changed[1])
        self.assertEqual(changed[2], "    it waits for the player's approval")
        self.assertEqual(self.lines("backoff", why="it exited 3 times in a minute\nstarted again at 12:00:05")[1:],
                         ["    it exited 3 times in a minute", "    started again at 12:00:05"])
        self.assertEqual(self.lines("stopped", error="extension.json: no id")[1:], ["    extension.json: no id"])
        self.assertEqual(cli.ext_lines(dict(id="v", title="V", path="p", state="stopped"))[0].split()[:2],
                         ["v", "stopped"])                   # a row of a daemon that says less

    def test_the_commands_print_them(self):
        row = self.row("stopped", approval="needed")

        class Client:
            def local_extensions(self):
                return {"extensions": [row]}

            def extension_add(self, path):
                return row

            def extension_load(self, eid):
                return {"check": {"ok": True}, "row": row}

            def extension_stop(self, eid):
                return row

            def extension_set(self, eid, enabled, options):
                return row

        parse = cli.build_parser().parse_args
        for argv, lead in ((["list"], "voice"), (["add", r"C:\e\voice"], "registered: voice"), (["load", "voice"], None),
                           (["stop", "voice"], "voice"), (["set", "voice", "--on"], "voice")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch.object(cli, "print_check"):
                self.assertEqual(cli.cmd_ext(Client(), parse(["ext", *argv])), 0)
            lines = out.getvalue().splitlines()
            self.assertTrue(lead is None or lines[0].startswith(lead), lines)
            self.assertIn("not approved yet", lines[-1])

    def test_no_command_approves(self):
        for action in ("approve", "trust", "allow"):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.build_parser().parse_args(["ext", action, "voice"])


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

        def reconnect(start=True):
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

    def test_trae_and_workbuddy_take_the_cursor_layout(self):
        for host, where in (("trae-cn", "%APPDATA%\\Trae CN\\User\\mcp.json"), ("trae", "%APPDATA%\\Trae\\User\\mcp.json"),
                            ("workbuddy", "%USERPROFILE%\\.workbuddy\\mcp.json"),
                            ("workbuddy-ai", "%USERPROFILE%\\.workbuddy-ai\\mcp.json")):
            text = mcpconfig.snippet(host, None, command="wuxian", args=["mcp"], env={})
            self.assertIn(where, text.splitlines()[0])
            self.assertEqual(json.loads(text[text.index("{"):]), {"mcpServers": {"wuxian": {"command": "wuxian", "args": ["mcp"], "env": {}}}})

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


class ApiScan(unittest.TestCase):
    """`wuxian api-scan`: the game scan a part at a time, the fields only of the tables the API manual does not
    document, written for scripts/build_api_pack.py"""

    class Game:
        def __init__(self, client="1.60.1.70338"):
            self.client, self.asked = client, []

        def api_scan(self, part, names=None):
            self.asked.append((part, names))
            parts = dict(namespaces=dict(namespaces={"C_Garrison": ["GetGarrisonInfo"], "C_Empty": []}, addons=["WoWBridge"],
                                         replaced={"C_Shim": "Shims"}),
                         globals=dict(globals=["print"], replaced={"ShimGlobal": "Shims"}),
                         tables=dict(enums=["Documented", "GameRule"], constants=["LegacyConsts"], replaced=[]),
                         fields=dict(fields={n: {"A": 1} if n == "Enum.GameRule" else [] for n in names or ()}))
            client = self.client if part != "globals" else "1.60.1.70338"
            return dict(parts[part], client=client)

    def test_the_file_it_writes(self):
        from wuxianworkshop import apidocs
        pack = {"namespaces": [{"ns": "", "name": "", "functions": [], "events": [],
                                "tables": [{"name": "Documented", "type": "Enumeration", "fields": []}]}]}
        game = self.Game()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(apidocs, "index", return_value=apidocs.ApiIndex(pack)):
            out, path = io.StringIO(), os.path.join(tmp, "game_scan.json")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.cmd_api_scan(game, mock.Mock(out=path)), 0)
            with open(path, encoding="utf-8") as f:
                scan = json.load(f)
            self.assertEqual(game.asked[-1], ("fields", ["Enum.GameRule", "Constants.LegacyConsts"]))   # not Documented
            self.assertEqual((scan["client"], scan["namespaces"], scan["globals"], scan["enums"], scan["addons"]),
                             ("1.60.1.70338", {"C_Garrison": ["GetGarrisonInfo"], "C_Empty": []}, ["print"],
                              ["Documented", "GameRule"], ["WoWBridge"]))
            self.assertEqual(scan["fields"], {"Enum.GameRule": {"A": 1}, "Constants.LegacyConsts": {}})
            self.assertEqual(scan["replaced"], {"C_Shim": "Shims", "ShimGlobal": "Shims"})
            self.assertIn("1.60.1.70338: 2 C_ namespaces (1 functions), 1 global functions", out.getvalue())
            self.assertIn("left out, set by addons: 2 names (Shims 2)", out.getvalue())
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):     # the game restarted
                self.assertEqual(cli.cmd_api_scan(self.Game("1.60.1.70400"), mock.Mock(out=path)), 1)
            self.assertIn("the game changed during the scan", err.getvalue())


if __name__ == "__main__":
    unittest.main()
