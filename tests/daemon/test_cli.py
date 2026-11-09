"""The `wuxian` command line without a daemon: argument parsing, the mcp-config snippets, quit when nothing runs."""
import contextlib
import fnmatch
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from wuxianworkshop.cli import main as cli, mcpconfig
from wuxianworkshop.daemon.api import ApiError


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
        self.assertEqual(c.asked, [("voice.say", {"text": "hi"}, 30000), ("clear", None, None)])
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


class Game:
    """a daemon client with WuxianKit's apps in the game: a clock that moves by what is waited for, events that come
    at their times (ids from 100 on), kit.app.status / kit.app.open answering for one app"""

    def __init__(self, timeline=(), running=False, lease=90, down=0, old=False):
        self.now, self.running, self.lease, self.down, self.old = 1000.0, running, lease, down, old
        self.timeline = [dict(id=100 + i, t=1000.0 + at, at=1000.0 + at, addon="WuxianKit", topic=topic, data=data)
                         for i, (at, topic, data) in enumerate(timeline)]
        self.calls, self.asks = [], []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def call_exposed(self, addon, name, args=None, timeout_ms=10000):
        self.calls.append((name, args, self.now))
        if self.down > 0:
            self.down -= 1
            raise ApiError(409, "link_down", "no frames come from the game")
        if name == "kit.app.status":
            if not self.running:
                raise ApiError(400, "call_failed", "no app guild")
            return {"result": {"app": "guild", "on": True, "lease": self.lease}}
        assert name == "kit.app.open", name
        self.running = True
        return {"result": {"app": args["app"], "title": "公会聊天", "on": True, "lease": self.lease}}

    def addon_events(self, addon, topic, since, limit, wait):
        self.asks.append((topic, since, wait))
        if self.old and isinstance(topic, list):
            raise ApiError(400, "bad_request", "topic: a name or a glob")
        if since == -1:
            return {"events": [], "next": 100}
        pick = [e for e in self.timeline if e["id"] >= since
                and (topic is None or any(fnmatch.fnmatchcase(e["topic"], t) for t in topic))]
        come = [e for e in pick if e["at"] <= self.now]
        if not come:
            if pick and pick[0]["at"] <= self.now + wait:
                self.now = pick[0]["at"]
                come = [e for e in pick if e["at"] <= self.now]
            else:
                self.now += wait
                return {"events": [], "next": since}
        return {"events": [{k: v for k, v in e.items() if k != "at"} for e in come], "next": come[-1]["id"] + 1}


class Listen(unittest.TestCase):
    """`wuxian listen <app>`: joins the app when it runs, else opens it (kit.app.open, tried again every 5 s while the
    game is away), prints OPEN <status>, each event app.<id> as a JSON line and NEXT <id> after each batch, keeps it
    alive with kit.app.status every max(30, lease / 3) s, joins or opens it again after kit.ready, ends with END <why>
    (not for a replaced one), TIMEOUT after --max; --once ends after the first wake"""

    def listen(self, game, *argv):
        ns, rest = cli.build_parser().parse_known_args(["listen", *argv])
        self.assertEqual(rest, [])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "clock", game.clock), mock.patch.object(cli, "sleep", game.sleep), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_listen(game, ns)
        return code, out.getvalue().splitlines(), err.getvalue()

    def test_a_whole_run(self):
        game = Game([(10, "app.guild", {"app": "guild", "reason": "urgent", "items": [{"id": 1}], "skipped": 0}),
                     (10, "kit.app", {"app": "guild", "on": True, "ends": 9999}),
                     (12, "chat.message", {"text": "not this"}),
                     (40, "app.guild", {"app": "guild", "reason": "batch", "items": [], "skipped": 0}),
                     (50, "kit.ready", {}),
                     (60, "kit.app", {"app": "other", "on": False, "why": "player"}),
                     (61, "kit.app", {"app": "guild", "on": False, "why": "replaced"}),
                     (70, "app.guild", {"app": "guild", "reason": "idle", "items": [], "skipped": 0}),
                     (80, "kit.app", {"app": "guild", "on": False, "why": "player"})], down=1)
        code, lines, err = self.listen(game, "guild")
        self.assertEqual(code, 0)
        self.assertEqual(err.count("kit.app.status: no frames come from the game (link_down)"), 1)
        self.assertTrue(lines[0].startswith("OPEN {"))
        self.assertEqual(json.loads(lines[0][5:])["title"], "公会聊天")
        wake = json.loads(lines[1])
        self.assertEqual((sorted(wake), wake["id"], wake["topic"], wake["data"]["reason"]),
                         (["data", "id", "t", "topic"], 100, "app.guild", "urgent"))
        self.assertEqual(lines[2:4], ["NEXT 102", json.dumps(dict(id=103, t=1040.0, topic="app.guild", data={
            "app": "guild", "reason": "batch", "items": [], "skipped": 0}), ensure_ascii=False)])
        self.assertEqual(lines[4], "NEXT 104")
        self.assertTrue(lines[5].startswith("OPEN {"))                       # after the reload: joined
        self.assertEqual((json.loads(lines[6])["id"], lines[7:]), (107, ["NEXT 108", "END player"]))
        names = [(n, round(at)) for n, _, at in game.calls]
        self.assertEqual(names[:3], [("kit.app.status", 1000), ("kit.app.status", 1005), ("kit.app.open", 1005)])
        self.assertIn(("kit.app.status", 1035), names)                       # the heartbeat: lease 90 -> every 30 s
        self.assertIn(("kit.app.status", 1050), names)                       # after kit.ready: running, so not opened
        self.assertEqual([n for n, _ in names].count("kit.app.open"), 1)
        self.assertEqual(game.calls[2][1], {"app": "guild"})
        self.assertTrue(all(t == ["app.guild", "kit.app", "kit.ready"] and 0 <= w <= 25 for t, _, w in game.asks))

    def test_once_with_a_declaration(self):
        decl = {"app": "not this", "title": "公会聊天", "hear": ["chat:guild"], "pace": True, "lease": 0}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "guild.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(decl, f, ensure_ascii=False)
            game = Game([(3, "app.guild", {"reason": "item", "items": []}), (4, "app.guild", {"reason": "item"})],
                        running=True, lease=0)
            code, lines, _ = self.listen(game, "guild", "--open", path, "--minutes", "30", "--replace", "--once")
            self.assertEqual((code, len(lines), lines[-1]), (0, 3, "NEXT 101"))
            self.assertEqual(game.calls, [("kit.app.open", dict(decl, app="guild", minutes=30), 1000.0)])  # no status
            game = Game(running=True)                        # running: joined as it is, the declaration not sent
            code, lines, _ = self.listen(game, "guild", "--open", path, "--once", "--max", "0")
            self.assertEqual(([c[0] for c in game.calls], lines[-2:]), (["kit.app.status"], ["NEXT 100", "TIMEOUT"]))
            with open(path, "w", encoding="utf-8") as f:
                f.write("[1]")
            self.assertEqual(self.listen(Game(), "guild", "--open", path)[0], 2)
        self.assertEqual(self.listen(Game(), "guild", "--open", os.path.join(tmp, "gone.json"))[0], 2)
        code, _, err = self.listen(Game(), "Guild!")
        self.assertEqual(code, 2)
        self.assertIn("app: lowercase letters", err)

    def test_max_and_no_heartbeat_without_a_lease(self):
        game = Game(running=True, lease=0)
        code, lines, _ = self.listen(game, "guild", "--max", "120", "--since", "42")
        self.assertEqual((code, lines[1:]), (0, ["NEXT 42", "TIMEOUT"]))
        self.assertEqual([c[0] for c in game.calls], ["kit.app.status"])
        self.assertEqual(game.now, 1120.0)
        self.assertEqual(game.asks[0][1], 42)                          # from --since on, not from now

    def test_since_reads_first(self):
        """--since N: what came after N is read before anything is opened, so that an app the player stopped between
        two `listen --once` ends (END) instead of being opened again"""
        game = Game([(0, "app.guild", {"reason": "item", "items": []}),
                     (0, "kit.app", {"app": "guild", "on": False, "why": "player"})])
        code, lines, _ = self.listen(game, "guild", "--since", "100", "--once")
        self.assertEqual((code, lines[1:]), (0, ["NEXT 102", "END player"]))
        self.assertEqual(game.calls, [])
        self.assertEqual(game.asks[0][1:], (100, 0.0))
        game = Game([(0, "app.guild", {"reason": "item", "items": []})], running=True)   # running: its wake, then join
        code, lines, _ = self.listen(game, "guild", "--since", "100", "--max", "10")
        self.assertEqual((code, lines[0].startswith("{"), lines[1]), (0, True, "NEXT 101"))
        self.assertTrue(lines[2].startswith("OPEN {"))
        self.assertEqual([c[0] for c in game.calls], ["kit.app.status"])

    def test_the_game_away_while_it_waits(self):
        """the open is tried every 5 s until the game answers (the error said once); --max may end it first"""
        game = Game(down=1000)
        code, lines, err = self.listen(game, "guild", "--max", "60")
        self.assertEqual((code, lines), (0, ["NEXT 100", "TIMEOUT"]))
        self.assertEqual(len(game.calls), 12)                          # at 1000, 1005 ... 1055
        self.assertEqual(err.count("\n"), 1)
        game = Game()                                                  # it did not start (no why): no OPEN, tried again
        game.call_exposed = lambda addon, name, args=None, timeout_ms=10000: (
            game.calls.append(name) or {"result": {"app": "guild", "on": False}})
        code, lines, err = self.listen(game, "guild", "--max", "130")
        self.assertEqual((code, lines, game.calls.count("kit.app.open")), (0, ["NEXT 100", "TIMEOUT"], 3))
        self.assertIn("kit.app.open: it did not start:", err)

    def test_it_cannot_start(self):
        """WuxianKit says it cannot start (blocked: not in a guild): tried again every 60 s, not 5 (an older WuxianKit's
        refusal is an error the player sees in red each time), and at once after a login or a reload (kit.ready)"""
        for refuse in ("status", "error"):
            game = Game([(90, "kit.ready", {})])
            def call(addon, name, args=None, timeout_ms=10000, game=game, refuse=refuse):
                game.calls.append((name, args, game.now))
                if name == "kit.app.status":
                    raise ApiError(400, "call_failed", "no app guild")
                if refuse == "error":                                    # WuxianKit 0.6.0
                    raise ApiError(400, "call_failed", "app guild cannot start: 你还没有加入公会")
                return {"result": {"app": "guild", "on": False, "blocked": "你还没有加入公会"}}
            game.call_exposed = call
            code, lines, err = self.listen(game, "guild", "--max", "130")
            self.assertEqual((code, lines), (0, ["NEXT 101", "TIMEOUT"]), refuse)
            opens = [t - 1000 for name, _, t in game.calls if name == "kit.app.open"]
            self.assertEqual(opens, [0, 60, 90], refuse)                 # 90: the reload
            self.assertEqual(len(err.splitlines()), 1, refuse)           # said once
            self.assertIn("你还没有加入公会", err)

    def test_a_daemon_without_lists_of_topics(self):
        """a daemon before 0.9.13 refuses a list (400 bad_request "topic: ..."): all of WuxianKit's events, picked here"""
        game = Game([(2, "chat.message", {}), (3, "app.guild", {"reason": "item"})], running=True, old=True)
        code, lines, _ = self.listen(game, "guild", "--once")
        self.assertEqual((code, [json.loads(x)["id"] for x in lines[1:-1]], lines[-1]), (0, [101], "NEXT 102"))
        self.assertEqual([t for t, _, _ in game.asks][:2], [["app.guild", "kit.app", "kit.ready"], None])


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
