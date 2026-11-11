"""The `wuxian` command line without a daemon: argument parsing, the mcp-config snippets, quit when nothing runs."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop.cli import client as daemon_client, main as cli, mcpconfig
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
        self.assertIsNone(ns.addon)
        ns, _ = self.parse("ext", "new", "voice", "--addon", "VoiceGame")
        self.assertEqual((ns.id, ns.title, ns.folder, ns.addon, ns.where), ("voice", None, None, "VoiceGame", None))
        ns, _ = self.parse("ext", "new", "loot", "--where", "game")
        self.assertEqual((ns.id, ns.addon, ns.where), ("loot", None, "game"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parse("ext", "new", "loot", "--where", "nowhere")
        ns, _ = self.parse("ext", "wait", "loot")
        self.assertEqual((ns.action, ns.id, ns.seconds), ("wait", "loot", 120))
        ns, _ = self.parse("ext", "wait", "loot", "--seconds", "2.5")
        self.assertEqual(ns.seconds, 2.5)
        ns, _ = self.parse("ext", "logs", "voice", "-f", "--since", "5", "--limit", "50")
        self.assertEqual((ns.action, ns.id, ns.follow, ns.since, ns.limit), ("logs", "voice", True, 5, 50))
        ns, _ = self.parse("ext", "add", r"C:\down\voice-1.2.0.zip")                # a package is a path like any
        self.assertEqual((ns.action, ns.path, ns.replace), ("add", r"C:\down\voice-1.2.0.zip", False))
        ns, _ = self.parse("ext", "add", "--replace", "voice-1.2.0.zip")
        self.assertEqual((ns.path, ns.replace), ("voice-1.2.0.zip", True))
        ns, _ = self.parse("ext", "summary", "voice")
        self.assertEqual((ns.action, ns.what, ns.json), ("summary", "voice", False))
        ns, _ = self.parse("ext", "summary", r"C:\down\voice-1.2.0.zip", "--json")
        self.assertEqual((ns.what, ns.json), (r"C:\down\voice-1.2.0.zip", True))
        ns, _ = self.parse("ext", "pack", "voice")
        self.assertEqual((ns.action, ns.id, ns.to, ns.json), ("pack", "voice", None, False))
        ns, _ = self.parse("ext", "pack", "voice", "--to", "out", "--json")
        self.assertEqual((ns.to, ns.json), ("out", True))
        ns, _ = self.parse("ext", "install", "voice")
        self.assertEqual((ns.action, ns.id), ("install", "voice"))
        ns, _ = self.parse("ext", "dir")
        self.assertEqual(ns.action, "dir")
        for argv in (("ext",), ("ext", "begin"), ("ext", "load"), ("ext", "set", "v", "--on", "--off"),
                     ("ext", "pack"), ("ext", "install"), ("ext", "pack", "v", "--to"), ("ext", "summary"),
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

    # --- the extensions' home: version and origin, what it requires, its game addons

    def test_version_and_origin(self):
        self.assertEqual(self.lines("off", version="1.2.0", origin="home"),
                         [f"{'voice':<16} {'off':<34} 声音 1.2.0  [2 capabilities]  home  C:\\e\\voice"])
        self.assertTrue(self.lines("off", origin="linked")[0].endswith("  [2 capabilities]  linked  C:\\e\\voice"))
        self.assertEqual(self.lines("off", version=None, origin=None, author=None, homepage=None, requires={},
                                    unmet=[], addons=[]), self.lines("off"))        # nothing to say: as an older row

    def test_what_it_requires(self):
        workshop = dict(what="workshop", needs="0.9.14", has="0.9.13")
        kit = dict(what="kit", needs="0.7.0", has="0.6.0")
        self.assertEqual(self.lines("stopped", unmet=[workshop, kit])[1:],
                         ["    needs 无限工坊 0.9.14 or newer (this is 0.9.13): it does not start before that",
                          "    needs WuxianKit 0.7.0 or newer (the game has 0.6.0)"])
        said = self.lines("stopped", unmet=[workshop, kit], error="it requires 无限工坊 0.9.14 (this is 0.9.13)")[1:]
        self.assertEqual(said, ["    needs WuxianKit 0.7.0 or newer (the game has 0.6.0)",     # the error says the other
                                "    it requires 无限工坊 0.9.14 (this is 0.9.13)"])
        self.assertEqual(self.lines("stopped", unmet=[dict(what="lua", needs="5.4", has="5.1"), "?"])[1:],
                         ["    needs lua 5.4 or newer (there is 5.1)"])

    def test_its_game_addons(self):
        lines = self.lines("running", pid=1, addons=[dict(folder="VoiceGame", version="1.2.0", state=state) for state in
                                                     ("current", "missing", "differs", "foreign", "broken", "unknown")]
                           + [dict(folder="Odd", version=None, state="later"), "?"])
        self.assertEqual(lines[1:], [
            "    game addon VoiceGame 1.2.0: installed",
            "    game addon VoiceGame 1.2.0: not in the game: `wuxian ext install voice` installs it (so does `ext load`)",
            "    game addon VoiceGame 1.2.0: the game has another version: `wuxian ext install voice` updates it (so does "
            "`ext load`)",
            "    game addon VoiceGame 1.2.0: the game has an addon of this name that is not this extension's: it is left "
            "alone",
            "    game addon VoiceGame 1.2.0: its folder in the extension is missing or has no .toc",
            "    game addon VoiceGame 1.2.0: the game's folder is not known",
            "    game addon Odd: later"])                            # a state this program does not know: as it is
        order = self.lines("stopped", approval="needed", allowed=False, why="it waits", unmet=[
            dict(what="kit", needs="0.7", has="0.6")], addons=[dict(folder="G", version=None, state="unknown")])[1:]
        self.assertEqual([line.split()[0] for line in order], ["not", "needs", "it", "game"])

    def test_one_in_the_game_only(self):
        """where game: its state is game; what the game lists of its WuxianKit extensions, and what only the player
        can do now, in the player's words"""
        game = dict(extensions=["loot_peek"], state="restart")
        player = dict(action="restart_game", text="请重启游戏：新装的插件「LootPeek」重启后才会载入")
        lines = self.lines("game", id="loot_peek", where="game", game=game, player=player, command=None,
                           addons=[dict(folder="LootPeek", version="0.1.0", state="current")])
        self.assertTrue(lines[0].startswith(f"{'loot_peek':<16} game"), lines[0])
        self.assertEqual(lines[1:], ["    game addon LootPeek 0.1.0: installed", "    in the game: loot_peek restart",
                                     "    the player: 请重启游戏：新装的插件「LootPeek」重启后才会载入"])
        self.assertEqual(len(self.lines("game", where="game", game=dict(extensions=[], state="installed"), player=None)),
                         1)                                          # nothing to say: one line
        self.assertEqual(self.lines("stopped", game=None, player=None), self.lines("stopped"))

    def test_wait_stop_check_and_load_of_one_in_the_game(self):
        player = dict(action="restart_game", text="请重启游戏")
        row = self.row("game", id="loot_peek", where="game", player=player)
        asked = []

        class Client:
            def extension_wait(self, eid, seconds):
                asked.append((eid, seconds))
                return dict(self.answer)

            def extension_stop(self, eid):
                return dict(row, stopped=False, note="nothing runs on this computer")

            def extension_check(self, eid):
                return dict(ok=False, problems=[], capabilities=[], addons=[
                    dict(folder="LootPeek", path=r"C:\e\loot\addons\LootPeek", ok=False, warnings=[], errors=[
                        dict(file="LootPeek.lua", line=3, message="unexpected symbol", hint=None)]), "?"])

            def extension_load(self, eid):
                return dict(check=dict(ok=True, capabilities=[]), row=row, addons=[], restart=False, hint=None,
                            loaded=[dict(folder="LootPeek", ok=True, error=None, check={}),
                                    dict(folder="LootMore", ok=False, error="LootMore.lua: boom", check=None), "?"])

        client = Client()
        client.answer = dict(row=row, was=player, timeout=True)
        code, lines, _ = self.run_ext(client, "wait", "loot_peek", "--seconds", "3")
        self.assertEqual((code, lines[-1], asked), (1, "still waiting: 请重启游戏", [("loot_peek", 3.0)]))
        self.assertTrue(lines[0].startswith("loot_peek "), lines)
        client.answer = dict(row=dict(row, player=None), was=player, timeout=False)
        self.assertEqual(self.run_ext(client, "wait", "loot_peek")[::2], (0, ""))
        self.assertEqual(self.run_ext(client, "wait", "loot_peek")[1][-1], "done: 请重启游戏")
        client.answer = dict(row=dict(row, player=None), was=None, timeout=False)
        self.assertEqual(self.run_ext(client, "wait", "loot_peek")[:2][1][-1], "nothing to wait for")
        self.assertEqual(self.run_ext(client, "stop", "loot_peek")[1][-1], "    nothing runs on this computer")
        code, lines, _ = self.run_ext(client, "check", "loot_peek")
        self.assertEqual((code, lines), (1, ["error   LootPeek/LootPeek.lua:3: unexpected symbol",
                                             "not ok: 0 capabilities"]))
        asked = []                                                   # an id as it is, a path made a full one
        with mock.patch.object(Client, "extension_check", lambda self, what: asked.append(what) or dict(ok=True)), \
                mock.patch.object(cli, "print_check"), tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            Path("mine").mkdir()
            for what in ("loot_peek", "mine", ".", "sub/wuxian-extension.json"):
                self.run_ext(client, "check", what)
            self.assertEqual(asked, ["loot_peek", str(Path("mine").resolve()), str(Path(".").resolve()),
                                     str(Path("sub/wuxian-extension.json").resolve())])

        class Broken:                                                # a load its game half's Lua stops: every finding
            def extension_load(self, eid):
                raise ApiError(422, "check_failed", "extension_load loot_peek: nothing installed, LootPeek/LootPeek.lua:3: "
                               "unexpected symbol; 1 more of the kind: extension_check lists them", addons=[
                                   dict(folder="LootPeek", ok=False, warnings=[], errors=[
                                       dict(file="LootPeek.lua", line=3, code="syntax", message="unexpected symbol"),
                                       dict(file="More.lua", line=1, code="syntax", message="unexpected symbol")]), "?"])

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "connect", return_value=Broken()), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = cli.main(["ext", "load", "loot_peek"])
        self.assertEqual((code, out.getvalue().splitlines()), (1, ["error   LootPeek/LootPeek.lua:3: unexpected symbol",
                                                                   "error   LootPeek/More.lua:1: unexpected symbol"]))
        self.assertIn("nothing installed", err.getvalue())
        code, lines, _ = self.run_ext(client, "load", "loot_peek")
        self.assertEqual((code, lines[1:3]), (0, ["game addon LootPeek: ran in the game",
                                                  "game addon LootMore: failed in the game: LootMore.lua: boom"]))

    def test_one_the_workshop_allows_by_itself(self):
        """the setting "auto": the row says allowed, and nothing waits for the player; one from a package (allowed
        false) waits whatever the setting"""
        self.assertEqual(len(self.lines("stopped", approval="needed", allowed=True)), 1)
        self.assertIn("not approved yet", self.lines("stopped", approval="needed", allowed=False)[1])
        self.assertIn("changed since", self.lines("stopped", approval="changed", allowed=False)[1])

    def run_ext(self, client, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_ext(client, cli.build_parser().parse_args(["ext", *argv]))
        return code, out.getvalue().splitlines(), err.getvalue()

    def test_add_a_package(self):
        home = r"C:\w\extensions"
        row = self.row("stopped", approval="needed", allowed=False, version="1.2.0", origin="home",
                       path=home + r"\voice")
        asked = []

        class Client:
            def extension_add(self, path):
                asked.append(path)
                return dict(row, package=dict(file=path, replaced=self.replaced))

        client = Client()
        client.replaced = None
        code, lines, _ = self.run_ext(client, "add", "voice-1.2.0.zip")
        self.assertEqual(asked, [str(Path("voice-1.2.0.zip").resolve())])           # the daemon gets a full path
        self.assertEqual((code, len(lines)), (0, 2))
        self.assertTrue(lines[0].startswith("installed: voice "), lines)
        self.assertIn("  home  " + home + r"\voice", lines[0])
        self.assertIn("not approved yet: the player approves it in the 无限工坊 App", lines[1])
        client.replaced = home + r"\.removed\voice-20261110-120000"
        code, lines, _ = self.run_ext(client, "add", "voice-1.2.0.zip")
        self.assertEqual(lines[2], "    the folder it replaced is kept: " + client.replaced)

    def test_a_package_over_the_version_here_asks_first(self):
        """needs_replace: what differs is printed, nothing is changed, and it says how to go on; --replace sends true"""
        asked = []
        differs = dict(old=dict(id="voice", version="1.1.0"), new=dict(id="voice", version="1.2.0"), same=False,
                       changes=[dict(field="version", old="1.1.0", new="1.2.0"),
                                dict(field="pass", added=["OPENAI_API_KEY"], removed=[])])

        class Client:
            def extension_add(self, path, *replace):
                asked.append(replace)
                if replace != (True,):
                    raise ApiError(409, "needs_replace", "voice is here already", **differs)
                return dict(id="voice", title="声音", path="p", state="stopped", approval="needed",
                            package=dict(file=path, replaced="kept"))

        code, lines, _ = self.run_ext(Client(), "add", "voice-1.2.0.zip")
        self.assertEqual((code, asked), (1, [()]))
        self.assertEqual(lines[:3], ["voice is here already (version 1.1.0): the package is version 1.2.0. Nothing was "
                                     "changed.", "    version: 1.1.0 -> 1.2.0", "    pass: + OPENAI_API_KEY"])
        self.assertTrue(lines[3].startswith("to replace it ("), lines)
        self.assertTrue(lines[3].endswith("wuxian ext add --replace <the .zip>"), lines)
        self.assertEqual(len(lines), 4)
        differs.update(changes=[], same=True)
        self.assertEqual(self.run_ext(Client(), "add", "voice-1.2.0.zip")[1][1],
                         "    it is the very package the one here was imported from")
        differs.update(same=False)
        self.assertEqual(self.run_ext(Client(), "add", "voice-1.2.0.zip")[1][1],
                         "    the two say the same of themselves; their files may differ")
        differs.update(old=None, changes=None, same=None)            # the one here cannot be read
        code, lines, _ = self.run_ext(Client(), "add", "voice-1.2.0.zip")
        self.assertEqual((code, len(lines)), (1, 2))
        self.assertTrue(lines[0].startswith("voice is here already: the package is version 1.2.0."), lines)
        del asked[:]
        code, lines, _ = self.run_ext(Client(), "add", "--replace", "voice-1.2.0.zip")
        self.assertEqual((code, asked), (0, [(True,)]))
        self.assertTrue(lines[0].startswith("installed: voice "), lines)
        self.assertEqual(lines[-1], "    the folder it replaced is kept: kept")

        class Other:
            def extension_add(self, path):
                raise ApiError(409, "extension_exists", "a linked one")

        with self.assertRaises(ApiError):                            # any other refusal goes up as it did
            self.run_ext(Other(), "add", "voice-1.2.0.zip")

    def test_summary(self):
        asked = []
        answer = {"from": "package", "id": "voice", "title": "声音", "version": "1.2.0", "author": "某人",
                  "homepage": None, "package": {"sha256": "ab" * 32, "file": "voice-1.2.0.zip"}, "where": "both",
                  "risk": "local", "game": [dict(folder="WuxianVoice", version="1.2.0", path=None, needs=[
                      dict(addon="EllesmereUI", installed=False), dict(addon="Other", installed=True)])],
                  "local": {"run": ["python", "main.py"], "python": None, "start": "workshop", "env": ["VOICE_RATE"],
                            "pass": ["DEEPSEEK_API_KEY"]}, "executables": ["bin/tts.exe"],
                  "agent": {"docs": "AGENT.md", "skills": ["speak"], "apps": [], "adds": [dict(app="guild", wake=True)]},
                  "files": 12, "size": 20480}

        class Client:
            def extension_summary(self, id=None, path=None):
                asked.append((id, path))
                return answer

        code, lines, _ = self.run_ext(Client(), "summary", "voice")                 # an id: no such path here
        self.assertEqual((code, asked), (0, [("voice", None)]))
        self.assertEqual(lines, [
            'voice 1.2.0 "声音"',
            "  package: voice-1.2.0.zip, sha256 " + "ab" * 32,
            "  from: author 某人 (as it says itself: not verified)",
            "  runs: in the game and on this computer",
            "  game addon: WuxianVoice 1.2.0, needs EllesmereUI (not installed), Other, not in the game's AddOns",
            "  program: python main.py, started with 无限工坊",
            "    sets the variables: VOICE_RATE",
            "    gets the player's variables: DEEPSEEK_API_KEY",
            "  executables: bin/tts.exe",
            "  an agent reads: AGENT.md; skills speak; adds to the app guild: wake",
            "  12 files, 20.0 KB"])
        self.run_ext(Client(), "summary", "voice-1.2.0.zip")         # anything else is a path: the daemon gets it whole
        self.assertEqual(asked[1], (None, str(Path("voice-1.2.0.zip").resolve())))
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            Path("voice").mkdir()                                    # a folder of that name here is meant
            self.run_ext(Client(), "summary", "voice")
            self.assertEqual(asked[2], (None, str(Path("voice").resolve())))
        code, lines, _ = self.run_ext(Client(), "summary", "voice", "--json")
        self.assertEqual(json.loads("\n".join(lines)), answer)

    def test_remove_says_where_it_went(self):
        answers = []

        class Client:
            def extension_remove(self, eid):
                return answers.pop(0)

        answers.append(dict(removed=True, id="voice", path=r"C:\e\voice"))                         # an older daemon
        self.assertEqual(self.run_ext(Client(), "remove", "voice")[:2],
                         (0, ["voice is no longer registered (C:\\e\\voice; its files stay)"]))
        answers.append(dict(removed=True, id="voice", path=r"C:\e\voice", kept=None, addons=[]))    # a linked one
        self.assertEqual(self.run_ext(Client(), "remove", "voice")[1],
                         ["voice is no longer registered (C:\\e\\voice; its files stay)"])
        kept = r"C:\w\extensions\.removed\voice-20261110-120000"
        answers.append(dict(removed=True, id="voice", path=r"C:\w\extensions\voice", kept=kept, addons=[
            dict(folder="VoiceGame", removed=True, kept="20261110-120000"),
            dict(folder="VoiceOld", removed=True, kept=None),
            dict(folder="Theirs", removed=False, kept=None)]))
        self.assertEqual(self.run_ext(Client(), "remove", "voice")[1], [
            "voice is no longer registered (its folder moved to " + kept + ")",
            "    game addon VoiceGame: deleted from the game's AddOns (kept: `wuxian history VoiceGame` 20261110-120000)",
            "    game addon VoiceOld: deleted from the game's AddOns",
            "    game addon Theirs: left in the game's AddOns (it is not this extension's)"])
        hint = "Held: still in the game's AddOns, the game holds its files: quit the game, then delete the folder there"
        answers.append(dict(removed=True, id="voice", path=r"C:\e\voice", kept=None, hint=hint, addons=[
            dict(folder="Theirs", removed=False, kept=None, why="foreign"),
            dict(folder="Held", removed=False, kept=None, why="busy")]))                           # the game runs
        self.assertEqual(self.run_ext(Client(), "remove", "voice")[1], [
            "voice is no longer registered (C:\\e\\voice; its files stay)",
            "    game addon Theirs: left in the game's AddOns (it is not this extension's)",
            "    game addon Held: left in the game's AddOns (the game holds its files)",
            "    " + hint])

    def test_load_and_install_say_the_game_side(self):
        row = self.row("running", pid=7)
        game = dict(addons=[dict(folder="VoiceGame", state="installed", was="missing", version="1.2.0", kept=None),
                            dict(folder="VoiceMore", state="current", was="current", version=None, kept=None)],
                    restart=True, hint="VoiceGame is new: the game lists it after a restart")

        class Client:
            def extension_load(self, eid):
                return dict({"check": {"ok": True, "capabilities": ["voice.say"]}, "row": row}, **self.more)

            def extension_install(self, eid):
                return dict(self.more, id=eid)

        client = Client()
        client.more = game
        said = ["game addon VoiceGame 1.2.0: installed (it was missing)", "game addon VoiceMore: current",
                "restart the game: it finds new addons and new files only when it starts",
                "VoiceGame is new: the game lists it after a restart"]
        code, lines, _ = self.run_ext(client, "load", "voice")
        self.assertEqual((code, lines[0], lines[1:5]), (0, "ok: 1 capabilities (voice.say)", said))
        self.assertTrue(lines[5].startswith("voice "), lines)
        self.assertEqual(self.run_ext(client, "install", "voice")[:2], (0, said))
        client.more = dict(addons=[dict(folder="VoiceGame", state="current", was="current", version="1.2.0")],
                           restart=False, hint=None)
        self.assertEqual(self.run_ext(client, "install", "voice")[1], ["game addon VoiceGame 1.2.0: current"])
        client.more = {}                                             # a daemon that says nothing of the game
        self.assertEqual(len(self.run_ext(client, "load", "voice")[1]), 2)
        self.assertEqual(self.run_ext(client, "install", "voice")[1], ["voice: no game addons to install"])

    def test_pack(self):
        asked = []
        answer = dict(id="voice", version="1.2.0", file=r"D:\out\voice-1.2.0.zip", size=3 * 1048576 + 104858, files=42,
                      sha256="ab" * 32, skipped=["logs/a.log", ".env"])

        class Client:
            def extension_pack(self, eid, to):
                asked.append((eid, to))
                return answer

        code, lines, _ = self.run_ext(Client(), "pack", "voice")
        self.assertEqual((code, asked), (0, [("voice", None)]))      # no --to: the daemon's packages folder
        self.assertEqual(lines, ["packed voice 1.2.0: D:\\out\\voice-1.2.0.zip",
                                 "    3.1 MB, 42 files, 2 left out, sha256 " + "ab" * 32])
        self.run_ext(Client(), "pack", "voice", "--to", "out")
        self.assertEqual(asked[1], ("voice", str(Path("out").resolve())))           # --to is this shell's: a full path
        code, lines, _ = self.run_ext(Client(), "pack", "voice", "--json")
        self.assertEqual(json.loads("\n".join(lines)), answer)
        answer = dict(id="voice", version=None, file="v.zip", size=900, files=1, sha256=None, skipped=0)
        self.assertEqual(self.run_ext(Client(), "pack", "voice")[1], ["packed voice: v.zip", "    900 bytes, 1 files"])
        answer = dict(file="v.zip", size=2048, skipped=3)            # skipped as a number, less said
        self.assertEqual(self.run_ext(Client(), "pack", "voice")[1], ["packed voice: v.zip", "    2.0 KB, 3 left out"])
        answer = dict(file="v.zip", size=2048, skipped=[], summary={}, warnings=["env sets KEY", "another"])
        self.assertEqual(self.run_ext(Client(), "pack", "voice")[1],
                         ["packed voice: v.zip", "    2.0 KB", "    warning: env sets KEY", "    warning: another"])

    def test_dir_and_new(self):
        made = []

        class Client:
            def local_extensions(self):
                return self.listing

            def new_extension(self, eid, title, folder, addon):
                made.append((eid, title, folder, addon))
                return dict(row=dict(path=r"C:\w\extensions\voice"), files=[r"C:\w\extensions\voice\main.py"],
                            guide="extension_load it")

        client = Client()
        client.listing = dict(revision=3, extensions=[], problem=None, home=r"C:\w\extensions")
        self.assertEqual(self.run_ext(client, "dir"), (0, [r"C:\w\extensions"], ""))
        code, lines, _ = self.run_ext(client, "list")
        self.assertTrue(lines[0].startswith("no local extensions registered"), lines)
        self.assertIn(".zip", lines[0])
        client.listing = dict(revision=3, extensions=[])             # an older daemon: said, no traceback
        code, lines, err = self.run_ext(client, "dir")
        self.assertEqual((code, lines), (1, []))
        self.assertIn("does not say where its extensions folder is", err)
        self.run_ext(client, "new", "voice", "--title", "声音", "--addon", "VoiceGame")
        code, lines, _ = self.run_ext(client, "new", "voice")
        self.assertEqual(made, [("voice", "声音", None, "VoiceGame"), ("voice", None, None, None)])
        client.new_extension = lambda *args: made.append(args) or dict(row=dict(path="p"), files=[], guide="")
        self.run_ext(client, "new", "loot", "--where", "game")       # where: a fifth argument, only when given
        self.assertEqual(made[-1], ("loot", None, None, None, "game"))
        self.assertEqual(lines, [r"made C:\w\extensions\voice: main.py", "extension_load it"])


class ExtRoutes(unittest.TestCase):
    """cli.client's requests for the extensions' home: the routes, their bodies and how long each may take"""

    def asked(self, call):
        client = daemon_client.DaemonClient({"port": 1, "token": "t"})
        with mock.patch.object(daemon_client.DaemonClient, "request", return_value={"ok": 1}) as request:
            self.assertEqual(call(client), {"ok": 1})
        (method, path), more = request.call_args
        return method, path, more.get("body"), more.get("timeout")

    def test_pack_and_install(self):
        self.assertEqual(self.asked(lambda c: c.extension_pack("voice")),
                         ("POST", "/api/local/pack", {"id": "voice"}, 180))
        self.assertEqual(self.asked(lambda c: c.extension_pack("voice", r"D:\out")),
                         ("POST", "/api/local/pack", {"id": "voice", "to": r"D:\out"}, 180))
        self.assertEqual(self.asked(lambda c: c.extension_install("voice")),
                         ("POST", "/api/local/install", {"id": "voice"}, 180))

    def test_add_waits_for_a_package(self):
        self.assertEqual(self.asked(lambda c: c.extension_add(r"C:\down\voice-1.2.0.zip")),
                         ("POST", "/api/local/add", {"path": r"C:\down\voice-1.2.0.zip"}, 180))
        self.assertEqual(self.asked(lambda c: c.extension_add("v.zip", True)),      # only when it is to replace
                         ("POST", "/api/local/add", {"path": "v.zip", "replace": True}, 180))
        self.assertEqual(self.asked(lambda c: c.extension_add("v.zip", False))[2], {"path": "v.zip"})

    def test_summary(self):
        self.assertEqual(self.asked(lambda c: c.extension_summary("voice")),
                         ("POST", "/api/local/summary", {"id": "voice"}, 180))
        self.assertEqual(self.asked(lambda c: c.extension_summary(path="v.zip")),
                         ("POST", "/api/local/summary", {"path": "v.zip"}, 180))
        self.assertEqual(self.asked(lambda c: c.extension_summary(None, "v.zip"))[2], {"path": "v.zip"})

    def test_new_with_an_addon(self):
        self.assertEqual(self.asked(lambda c: c.new_extension("voice", "声音")),      # as before: no field it has not
                         ("POST", "/api/local/new", {"id": "voice", "title": "声音", "folder": None}, 60))
        self.assertEqual(self.asked(lambda c: c.new_extension("voice", None, None, "VoiceGame")),
                         ("POST", "/api/local/new", {"id": "voice", "title": None, "folder": None,
                                                     "addon": "VoiceGame"}, 60))
        self.assertEqual(self.asked(lambda c: c.new_extension("loot", None, None, None, "game")),
                         ("POST", "/api/local/new", {"id": "loot", "title": None, "folder": None, "where": "game"}, 60))

    def test_wait(self):
        """extension_wait: the daemon has as long as it was asked to wait (600 s at most) and a minute more"""
        self.assertEqual(self.asked(lambda c: c.extension_wait("loot")),
                         ("POST", "/api/local/wait", {"id": "loot", "seconds": 120}, 180))
        self.assertEqual(self.asked(lambda c: c.extension_wait("loot", 5000)),
                         ("POST", "/api/local/wait", {"id": "loot", "seconds": 5000}, 660))
        self.assertEqual(self.asked(lambda c: c.extension_wait("loot", "long")),      # the daemon refuses it
                         ("POST", "/api/local/wait", {"id": "loot", "seconds": "long"}, 60))


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
