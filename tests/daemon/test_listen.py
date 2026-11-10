"""`wuxian listen <app>` without a daemon or a game: it joins an app that runs and never opens one, its cursor is
WuxianKit's item id (the journal's ids are not printed), what was missed is caught up with kit.app.recent, what a wake
repeats is left out, and the game away or the daemon gone are waited for."""
import contextlib
import fnmatch
import io
import json
import unittest
from unittest import mock

from wuxianworkshop.cli import main as cli
from wuxianworkshop.cli.client import DaemonGone
from wuxianworkshop.daemon.api import ApiError


def item(n):
    return {"id": n, "time": 1700000000 + n, "source": "chat", "text": f"line {n}"}


def wake(after, *ids, reason="item", skipped=0, next=None, **more):
    """a wake as WuxianKit sends it: it covers the items after < id <= next"""
    return dict(app="guild", seq=1, reason=reason, items=[item(n) for n in ids], skipped=skipped, after=after,
                next=next if next is not None else (ids[-1] if ids else after), **more)


class Game:
    """the game with WuxianKit's app `guild`: a clock that moves by what is waited for, the items the app heard (each
    from its time on), whether it runs, when the link is down"""

    def __init__(self, heard=(), lease=90, on=True, why=None, known=True):
        self.now, self.lease, self.on, self.why, self.known = 1000.0, lease, on, why, known
        self.ring = [(1000.0, item(n)) for n in heard]
        self.stops, self.down, self.calls = [], [], []

    def hear(self, at, *ids):
        self.ring += [(1000.0 + at, item(n)) for n in ids]

    def stop(self, at, why):
        self.stops.append((1000.0 + at, why))

    def away(self, start, end):
        self.down.append((1000.0 + start, 1000.0 + end))

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def names(self):
        return [name for name, _, _ in self.calls]

    def call(self, name, args):
        self.calls.append((name, args, round(self.now - 1000.0, 3)))
        if any(a <= self.now < b for a, b in self.down):
            raise ApiError(409, "link_down", "no frames come from the game")
        if not self.known:
            raise ApiError(400, "call_failed", "no app guild")
        heard = [i for at, i in self.ring if at <= self.now]
        if name == "kit.app.status":
            why = next((why for at, why in self.stops if at <= self.now), None if self.on else self.why or "")
            out = {"app": "guild", "title": "公会聊天", "on": why is None, "lease": self.lease,
                   "next": heard[-1]["id"] if heard else 0}
            if why:
                out["why"] = why
            return {"result": out}
        assert name == "kit.app.recent", name               # never kit.app.open
        since = args.get("since") or 0
        return {"result": {"items": [i for i in heard if i["id"] > since][-50:],
                           "next": heard[-1]["id"] if heard else since}}


class Daemon:
    """a daemon client: its journal holds the timeline's events (at, topic, data), ids from `first` on; from `gone`
    on nothing listens there"""

    def __init__(self, game, timeline=(), first=100, old=False, gone=None, lost=0):
        self.game, self.first, self.old, self.lost = game, first, old, lost
        self.gone = None if gone is None else 1000.0 + gone
        self.events = [dict(id=first + i, t=1000.0 + at, at=1000.0 + at, addon="WuxianKit", topic=topic, data=data)
                       for i, (at, topic, data) in enumerate(timeline)]
        self.asks = []

    def alive(self):
        if self.gone is not None and self.game.now >= self.gone:
            raise DaemonGone("cannot reach the daemon at http://127.0.0.1:1: refused")

    def call_exposed(self, addon, name, args=None, timeout_ms=10000):
        self.alive()
        assert addon == "WuxianKit"
        return self.game.call(name, args)

    def addon_events(self, addon, topic, since, limit, wait):
        g = self.game
        self.asks.append((topic, since, wait))
        assert len(self.asks) < 2000, "it does not get anywhere"
        self.alive()
        if self.old and isinstance(topic, list):
            raise ApiError(400, "bad_request", "topic: a name or a glob")

        def journal_next():
            return self.first + sum(1 for e in self.events if e["at"] <= g.now)

        if since == -1:
            return {"events": [], "next": journal_next(), "truncated": False}
        pick = [e for e in self.events if e["id"] >= since
                and (topic is None or any(fnmatch.fnmatchcase(e["topic"], t) for t in topic))]
        if not any(e["at"] <= g.now for e in pick):
            g.now = min(t for t in (g.now + wait, pick[0]["at"] if pick else None, self.gone) if t is not None)
            self.alive()
        come = [{k: v for k, v in e.items() if k != "at"} for e in pick if e["at"] <= g.now]
        return {"events": come, "next": journal_next(), "truncated": since < self.first + self.lost}


def listen(daemon, *argv, reconnect=None):
    """`wuxian listen guild <argv>` against the daemon: (exit code, the lines on stdout, stderr)"""
    game = daemon.game
    ns = cli.build_parser().parse_args(["listen", "guild", *argv])
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(cli, "clock", game.clock), mock.patch.object(cli, "sleep", game.sleep), \
            mock.patch.object(cli, "wall", game.clock), \
            mock.patch.object(cli, "reconnect", reconnect or (lambda client: None)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.cmd_listen(daemon, ns)
    return code, out.getvalue().splitlines(), err.getvalue()


def wakes(lines):
    return [json.loads(line) for line in lines if line.startswith("{")]


def ids(w):
    return [i["id"] for i in w["data"]["items"]]


class Parser(unittest.TestCase):
    def test_arguments(self):
        ns = cli.build_parser().parse_args(["listen", "guild"])
        self.assertEqual((ns.command, ns.app, ns.since, ns.once, ns.max), ("listen", "guild", None, False, None))
        ns = cli.build_parser().parse_args(["listen", "guild", "--once", "--since", "42", "--max", "60"])
        self.assertEqual((ns.since, ns.once, ns.max), (42, True, 60.0))

    def test_it_cannot_open(self):
        for gone in (["--open", "x.json"], ["--minutes", "30"], ["--replace"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.build_parser().parse_args(["listen", "guild", *gone])

    def test_the_help_says_how(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(out):
            cli.build_parser().parse_args(["listen", "--help"])
        text = out.getvalue()
        for word in ("never opens", "wk_kit_app_open", "NEXT <item id>", "END <why>", "TIMEOUT", "catchup",
                     "The game away", "tries again every 5 seconds"):
            self.assertIn(word, text)

    def test_bad_arguments(self):
        daemon = Daemon(Game())
        for argv in (["listen", "Guild!"], ["listen", "guild", "--since", "-1"]):
            ns = cli.build_parser().parse_args(argv)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.cmd_listen(daemon, ns), 2)
        self.assertEqual((daemon.game.calls, daemon.asks), ([], []))


class Joining(unittest.TestCase):
    def test_an_app_that_does_not_run(self):
        game = Game(on=False, why="expired")
        code, out, err = listen(Daemon(game), "--since", "40")
        self.assertEqual((code, out, err), (0, ["END expired"], ""))
        self.assertEqual(game.names(), ["kit.app.status"])               # once: not tried again, not opened, not read

        code, out, _ = listen(Daemon(Game(on=False)))
        self.assertEqual((code, out), (0, ["END stopped"]))

    def test_an_app_wuxiankit_does_not_know(self):
        game = Game(known=False)
        code, out, err = listen(Daemon(game))
        self.assertEqual((code, out), (1, ["END stopped"]))
        self.assertIn("no app guild", err)
        self.assertEqual(game.names(), ["kit.app.status"])

    def test_from_now_on(self):
        game = Game(heard=[40])
        game.hear(10, 41, 42)
        daemon = Daemon(game, [(10, "app.guild", wake(40, 41, 42, pace="normal", quiet=False)),
                               (20, "kit.app", {"app": "other", "on": False, "why": "stopped"}),
                               (30, "kit.app", {"app": "guild", "on": False, "why": "replaced"}),
                               (40, "kit.app", {"app": "guild", "on": False, "why": "player"})])
        code, out, err = listen(daemon)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out[0][5:]), {"app": "guild", "title": "公会聊天", "on": True, "lease": 90,
                                                  "next": 40})
        self.assertEqual(wakes(out), [{"t": 1010.0, "topic": "app.guild",          # no id of the journal's
                                      "data": wake(40, 41, 42, pace="normal", quiet=False)}])
        self.assertEqual([out[0][:5]] + out[2:], ["OPEN ", "NEXT 42", "END player"])
        self.assertEqual(game.names(), ["kit.app.status", "kit.app.status"])        # the second: the heartbeat at 30 s
        self.assertEqual(daemon.asks[0][1], -1)                                     # the journal from now on, first
        self.assertEqual(daemon.asks[1][0], ["app.guild", "kit.app", "kit.ready"])

    def test_once_exits_after_the_first_batch(self):
        game = Game(heard=[40])
        game.hear(10, 41, 42, 43)
        daemon = Daemon(game, [(10, "app.guild", wake(40, 41)), (10, "app.guild", wake(41, 42, 43)),
                               (50, "app.guild", wake(43, 44))])
        code, out, _ = listen(daemon, "--once")
        self.assertEqual((code, [ids(w) for w in wakes(out)], out[-1]), (0, [[41], [42, 43]], "NEXT 43"))

    def test_max(self):
        game = Game(heard=[40], lease=0)
        code, out, _ = listen(Daemon(game), "--max", "100")
        self.assertEqual((code, out[1:], game.now), (0, ["NEXT 40", "TIMEOUT"], 1100.0))
        self.assertEqual(game.names(), ["kit.app.status"])                          # no lease: no heartbeat

        game = Game(heard=[40], lease=600)
        code, out, _ = listen(Daemon(game), "--once")                               # --once: 1500 s
        self.assertEqual((out[1:], game.now), (["NEXT 40", "TIMEOUT"], 2500.0))
        self.assertEqual([at for _, _, at in game.calls], [0.0] + [200.0 * n for n in range(1, 8)])   # lease / 3

    def test_the_heartbeat_sees_that_it_stopped(self):
        game = Game(heard=[40])
        game.stop(45, "expired")                                                    # no kit.app came of it
        code, out, _ = listen(Daemon(game))
        self.assertEqual((code, out[1:], game.now), (0, ["END expired"], 1060.0))

    def test_a_login_or_a_reload(self):
        game = Game(heard=[40])
        game.hear(30, 41)
        daemon = Daemon(game, [(20, "kit.ready", {}), (30, "app.guild", wake(40, 41)), (50, "kit.ready", {})])
        game.stop(45, "left")
        code, out, _ = listen(daemon)
        self.assertEqual((code, [line.split()[0] for line in out]), (0, ["OPEN", "{\"t\":", "NEXT", "END"]))
        self.assertEqual(out[-2:], ["NEXT 41", "END left"])                         # OPEN once; the status read again
        self.assertEqual([at for name, _, at in game.calls], [0.0, 20.0, 50.0])

    def test_opened_again_while_it_runs(self):
        game = Game(heard=[40], lease=600)
        daemon = Daemon(game, [(100, "kit.app", {"app": "guild", "on": True, "ends": 9})])
        code, out, _ = listen(daemon, "--max", "150")
        self.assertEqual(out[1:], ["NEXT 40", "TIMEOUT"])
        self.assertEqual([at for _, _, at in game.calls], [0.0, 100.0])             # its lease may be another now

    def test_a_daemon_without_lists_of_topics(self):
        game = Game(heard=[40])
        game.hear(10, 41)
        daemon = Daemon(game, [(5, "chat.line", {"text": "x"}), (10, "app.guild", wake(40, 41)),
                               (12, "app.other", wake(0, 7))], old=True)
        code, out, _ = listen(daemon, "--once")
        self.assertEqual((code, [ids(w) for w in wakes(out)], out[-1]), (0, [[41]], "NEXT 41"))
        self.assertEqual([topic for topic, _, _ in daemon.asks],            # all of WuxianKit's, picked here
                         [["app.guild", "kit.app", "kit.ready"], None, None, None])


class Cursor(unittest.TestCase):
    def test_since_catches_up_first(self):
        game = Game(heard=[40, 41, 43, 44])
        code, out, _ = listen(Daemon(game), "--once", "--since", "41")
        self.assertEqual(code, 0)
        self.assertEqual(wakes(out), [{"t": 1000.0, "topic": "app.guild", "data": {
            "app": "guild", "reason": "catchup", "items": [item(43), item(44)], "skipped": 0, "after": 41, "next": 44}}])
        self.assertEqual([out[0][:5]] + out[2:], ["OPEN ", "NEXT 44"])
        self.assertEqual([(name, args) for name, args, _ in game.calls],
                         [("kit.app.status", {"app": "guild"}), ("kit.app.recent", {"app": "guild", "since": 41})])

    def test_since_with_nothing_missed(self):
        game = Game(heard=[40, 44])
        game.hear(10, 45)
        code, out, _ = listen(Daemon(game, [(10, "app.guild", wake(44, 45))]), "--once", "--since", "44")
        self.assertEqual(([ids(w) for w in wakes(out)], wakes(out)[0]["data"]["reason"], out[-1]),
                         ([[45]], "item", "NEXT 45"))

    def test_a_gap_is_caught_up(self):
        game = Game(heard=[40])
        game.hear(10, 41, 42)                       # their wake did not get here
        game.hear(20, 43)
        note = [{"from": "codex", "text": "a note"}]
        daemon = Daemon(game, [(20, "app.guild", wake(42, 43, pace="lively", quiet=False, state={"zone": "x"},
                                                      notes=note))])
        code, out, _ = listen(daemon, "--once")
        self.assertEqual(wakes(out), [{"t": 1020.0, "topic": "app.guild", "data": {
            "app": "guild", "reason": "catchup", "items": [item(41), item(42), item(43)], "skipped": 0, "after": 40,
            "next": 43, "pace": "lively", "quiet": False, "state": {"zone": "x"}, "notes": note}}])
        self.assertEqual(out[-1], "NEXT 43")        # the wake that showed the gap: all of it is in the catch-up
        self.assertEqual(game.calls[-1][:2], ("kit.app.recent", {"app": "guild", "since": 40}))

    def test_repeats_are_left_out(self):
        game = Game(heard=[40, 41, 42, 43])
        game.hear(30, 44)
        daemon = Daemon(game, [(10, "app.guild", wake(40, 41, 42)),          # all of it before the cursor
                               (20, "app.guild", wake(41, 42, 43)),
                               (30, "app.guild", wake(41, 42, 43, 44)),      # one of it is new
                               (40, "app.guild", wake(43, 44))])
        code, out, _ = listen(daemon, "--since", "43", "--max", "60")
        self.assertEqual([(w["t"], ids(w), w["data"]["after"], w["data"]["next"]) for w in wakes(out)],
                         [(1030.0, [44], 41, 44)])
        self.assertEqual(out[1:], [out[1], "NEXT 44", "NEXT 44", "TIMEOUT"])
        self.assertEqual(game.names().count("kit.app.recent"), 1)             # the one of --since

    def test_a_wake_that_brings_nothing(self):
        game = Game(heard=[40])
        daemon = Daemon(game, [(300, "app.guild", wake(40, reason="idle"))])
        code, out, _ = listen(daemon, "--once")
        self.assertEqual(wakes(out), [{"t": 1300.0, "topic": "app.guild", "data": wake(40, reason="idle")}])
        self.assertEqual(out[-1], "NEXT 40")

    def test_items_too_big_for_their_wake(self):
        game = Game(heard=[40])
        game.hear(10, 41, 42, 43)
        daemon = Daemon(game, [(10, "app.guild", wake(40, 43, skipped=2))])
        code, out, _ = listen(daemon, "--once")
        self.assertEqual([(ids(w), w["data"]["skipped"]) for w in wakes(out)], [([43], 2)])
        self.assertEqual(out[-1], "NEXT 43")

    def test_a_cursor_past_the_apps_items(self):
        game = Game(heard=[40])
        game.hear(10, 41)
        code, out, err = listen(Daemon(game, [(10, "app.guild", wake(40, 41))]), "--once", "--since", "500")
        self.assertIn("going on from 40", err)
        self.assertEqual(([ids(w) for w in wakes(out)], out[-1]), ([[41]], "NEXT 41"))

    def test_item_ids_print_whole(self):
        game = Game(heard=[40])
        game.hear(10, 41)
        data = dict(wake(40.0, 41), next=41.0)
        code, out, _ = listen(Daemon(game, [(10, "app.guild", data)]), "--once")
        self.assertEqual(out[-1], "NEXT 41")

    def test_what_is_no_wake_of_wuxiankits(self):
        game = Game(heard=[40])
        code, out, _ = listen(Daemon(game, [(10, "app.guild", {"hello": 1})]), "--once")
        self.assertEqual((wakes(out), out[-1]), ([{"t": 1010.0, "topic": "app.guild", "data": {"hello": 1}}], "NEXT 40"))


class Away(unittest.TestCase):
    def test_the_game_away_at_the_start(self):
        game = Game(heard=[40])
        game.away(0, 12)
        game.hear(20, 41)
        code, out, err = listen(Daemon(game, [(20, "app.guild", wake(40, 41))]), "--once")
        self.assertEqual((code, [line.split()[0] for line in out], out[-1]), (0, ["OPEN", "{\"t\":", "NEXT"], "NEXT 41"))
        self.assertEqual(err.count("link_down"), 1)                                 # said once
        self.assertIn("tried again every 5 s", err)
        self.assertEqual([at for _, _, at in game.calls], [0.0, 5.0, 10.0, 15.0])

    def test_the_game_away_until_max(self):
        game = Game(heard=[40])
        game.away(0, 9999)
        code, out, err = listen(Daemon(game), "--max", "60")
        self.assertEqual((code, out), (0, ["TIMEOUT"]))                             # no cursor yet: no NEXT
        code, out, err = listen(Daemon(game), "--max", "60", "--since", "7")
        self.assertEqual((code, out), (0, ["NEXT 7", "TIMEOUT"]))

    def test_the_game_away_while_it_waits(self):
        game = Game(heard=[40])
        game.away(20, 70)
        game.stop(60, "left")                                                       # its lease ran out meanwhile
        code, out, err = listen(Daemon(game))
        self.assertEqual((code, out[1:]), (0, ["END left"]))
        self.assertEqual(err.count("link_down"), 1)
        self.assertEqual([at for _, _, at in game.calls], [0.0] + [30.0 + 5 * n for n in range(9)])

    def test_a_catch_up_that_fails(self):
        game = Game(heard=[40])
        game.hear(10, 41, 42)
        game.away(10, 18)
        daemon = Daemon(game, [(10, "app.guild", wake(41, 42)), (12, "app.guild", wake(42, reason="idle"))])
        code, out, err = listen(daemon, "--max", "40")
        self.assertEqual([(w["data"]["reason"], ids(w)) for w in wakes(out)], [("catchup", [41, 42]), ("idle", [])])
        self.assertEqual(out[-3:], ["NEXT 42", "NEXT 42", "TIMEOUT"])
        self.assertEqual(err.count("kit.app.recent"), 1)

    def test_the_daemon_starts_again(self):
        game = Game(heard=[40])
        game.hear(10, 41)
        game.hear(60, 42, 43)                                # heard while no daemon ran: their wake is lost
        game.hear(90, 44)
        first = Daemon(game, [(10, "app.guild", wake(40, 41))], first=500, gone=50)
        second = Daemon(game, [(80, "app.guild", wake(41, 42, 43)),       # sent again: nothing of it is new
                               (90, "app.guild", wake(43, 44)),
                               (95, "kit.app", {"app": "guild", "on": False, "why": "stopped"})], first=1)
        tries = []

        def reconnect(client):
            tries.append(round(game.now - 1000.0, 3))
            return second if game.now >= 1070.0 else None

        code, out, err = listen(first, reconnect=reconnect)
        self.assertEqual(code, 0)
        self.assertEqual([(w["data"]["reason"], ids(w)) for w in wakes(out)],
                         [("item", [41]), ("catchup", [42, 43]), ("item", [44])])    # none lost, none twice
        self.assertEqual([line for line in out if not line.startswith(("{", "OPEN"))],
                         ["NEXT 41", "NEXT 43", "NEXT 44", "END stopped"])
        self.assertEqual(len([line for line in out if line.startswith("OPEN")]), 1)
        self.assertEqual(tries, [50.0, 55.0, 60.0, 65.0, 70.0])
        self.assertEqual((err.count("error: the daemon: "), len(err.splitlines())), (1, 1))      # said once
        self.assertEqual(second.asks[0][1], -1)            # its journal from now on: the old ids mean nothing there

    def test_a_token_the_daemon_takes_no_more(self):
        game = Game(heard=[40, 41])

        class Refusing(Daemon):
            def alive(self):
                raise ApiError(401, "unauthorized", "Authorization: Bearer <token> is needed")

        second = Daemon(game, [(20, "kit.app", {"app": "guild", "on": False, "why": "player"})], first=1)
        code, out, _ = listen(Refusing(game), "--since", "40", reconnect=lambda client: second)
        self.assertEqual(([ids(w) for w in wakes(out)], out[-2:]), ([[41]], ["NEXT 41", "END player"]))

    def test_the_journal_let_go_of_events(self):
        game = Game(heard=[40])
        game.hear(10, 41)
        daemon = Daemon(game, [(10, "chat.line", {}), (10, "chat.line", {})], lost=2)
        code, out, _ = listen(daemon, "--once")
        self.assertEqual(([(w["data"]["reason"], ids(w)) for w in wakes(out)], out[-1]), ([("catchup", [41])], "NEXT 41"))


if __name__ == "__main__":
    unittest.main()
