"""The apps the workshop keeps open for local extensions' programs, and their reports (local/drive.py; contract3
sections 1 and 2), against a stand-in for the game's WuxianKit and for the extension's program: open and the "open"
wake, the cursor (kept, caught up, repeats left out), one wake at a time, a program that answers an error or goes, the
player's stop and start, other ends, refusals and their waits, a game that is away or has no WuxianKit, the heartbeat,
an app that is driven no more; reports told at once, kept while the game is away and told late."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import drive as D
from wuxianworkshop.local.rpc import RpcError


def item(n, kind="snapshot"):
    return {"id": n, "source": "cocreate", "kind": kind}


class Game:
    """WuxianKit as the drive asks it: one app, cocreate; calls [(name, args)]"""

    def __init__(self):
        self.calls, self.link, self.error = [], "online", None
        self.on, self.why, self.blocked, self.declared = False, None, None, True
        self.lease, self.next, self.sent, self.items = 600, 10, 10, []
        self.refuse_reports = False

    def status(self):
        out = {"app": "cocreate", "on": self.on, "lease": self.lease, "next": self.next}
        if self.on:
            out["sent"] = self.sent
        else:
            out.update(why=self.why, **({"blocked": self.blocked} if self.blocked else {}))
        return out

    async def kit(self, name, args):
        self.calls.append((name, args))
        if self.error is not None:
            raise self.error
        if name == "kit.app.report":
            if self.refuse_reports:
                raise ApiError(400, "call_failed", "kit.app.report: app cocreate is not driven by a local extension")
            return {"kept": True}
        if not self.declared:
            raise ApiError(400, "call_failed", "no app cocreate: kit.app.declare declares one\nstack ...")
        if name == "kit.app.open":
            if not self.blocked:
                self.on, self.why = True, None
            return self.status()
        if name == "kit.app.status":
            return self.status()
        if name == "kit.app.stop":
            self.on, self.why = False, args.get("why") or "stopped"
            return self.status()
        if name == "kit.app.recent":
            return {"items": [i for i in self.items if i["id"] > args["since"]][-args["limit"]:], "next": self.next}
        raise AssertionError(name)

    def asked(self, name):
        return [args for n, args in self.calls if n == name]


class Program:
    """the extension's program: the wakes it was handed; hold: an Event it waits for before it answers"""

    def __init__(self):
        self.wakes, self.hold, self.error = [], None, None

    async def wake(self, eid, app, wake):
        self.wakes.append(wake)
        if self.hold is not None:
            await self.hold.wait()
        if self.error is not None:
            raise self.error

    def reasons(self):
        return [w["reason"] for w in self.wakes]


class Case(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)    # (a write of the file may still be going)
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "state" / D.FILE
        fast = mock.patch.multiple(D, REOPEN=0.05, REFUSED=0.05, REFUSED_MOST=0.2, AWAY=0.05, AWAY_MOST=0.1)
        fast.start()
        self.addCleanup(fast.stop)
        self.game, self.program, self.notes, self.usable = Game(), Program(), [], True
        self.drive = self.make()

    def make(self):
        drive = D.Drive(self.game.kit, self.program.wake, lambda: self.game.link, note=self.notes.append,
                        path=self.path, usable=lambda: self.usable, clock=lambda: 1794938824.5)
        self.addCleanup(drive.close)
        return drive

    async def until(self, fn, seconds=5.0):
        def ok():
            try:
                return fn()
            except (TypeError, KeyError, IndexError):    # (the file not there yet, or not as far)
                return False
        end = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < end:
            if ok():
                return True
            await asyncio.sleep(0.01)
        return bool(ok())

    async def running(self, event=None):
        self.drive.sync({"cocreate": "fx"}, event)
        self.assertTrue(await self.until(lambda: self.state() == "running" and self.program.wakes), self.view())
        return self.drive.apps["cocreate"]

    def view(self):
        return (self.drive.view(), self.game.calls, self.notes)

    def state(self):
        return self.drive.apps["cocreate"].state if "cocreate" in self.drive.apps else None

    def kept(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except OSError:                                  # (being put in place this moment: asked again)
            return {"cursors": None, "reports": None}

    def wake(self, after, nxt, items=None, **more):
        self.game.sent = self.game.next = max(self.game.next, nxt)
        self.drive.heard("app.cocreate", dict({"app": "cocreate", "seq": 1, "reason": "item", "skipped": 0,
                                               "items": [item(n) for n in range(after + 1, nxt + 1)]
                                               if items is None else items, "after": after, "next": nxt}, **more))


class Feeding(Case):
    async def test_open_then_its_wakes(self):
        a = await self.running()
        self.assertEqual([n for n, _ in self.game.calls], ["kit.app.status", "kit.app.open"])   # no minutes, no lease
        self.assertEqual(self.game.asked("kit.app.open"), [{"app": "cocreate"}])
        self.assertEqual(self.program.wakes, [{"app": "cocreate", "seq": 0, "reason": "open", "items": [], "skipped": 0,
                                               "after": 10, "next": 10, "status": self.game.status()}])
        self.wake(10, 12, notes={"fx": {"n": 1}})
        self.assertTrue(await self.until(lambda: len(self.program.wakes) == 2))
        got = self.program.wakes[1]
        self.assertEqual((got["reason"], [i["id"] for i in got["items"]], got["notes"]), ("item", [11, 12], {"fx": {"n": 1}}))
        self.assertTrue(await self.until(lambda: a.cursor == 12 and self.path.exists()))
        self.assertEqual(self.kept(), {"cursors": {"fx": {"cocreate": 12}}, "reports": {}})
        self.assertEqual(self.drive.view(), [dict(app="cocreate", extension="fx", state="running", why=None, cursor=12,
                                                  opened=1)])
        self.assertIn("local extension fx: its app cocreate: open", self.notes)
        self.drive.heard("app.other", {"app": "other", "next": 99, "items": []})      # not one of its: nothing
        self.drive.heard("kit.app", {"app": "other", "on": False, "why": "player"})
        self.drive.heard("app.cocreate", "text")
        await asyncio.sleep(0.05)
        self.assertEqual((len(self.program.wakes), self.state()), (2, "running"))

    async def test_it_goes_on_where_its_program_got(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"cursors": {"fx": {"cocreate": 5}, "gone": {"x": "bad"}}}), encoding="utf-8")
        self.drive = self.make()
        self.game.items = [item(n) for n in range(3, 11)]
        await self.running()
        self.assertTrue(await self.until(lambda: len(self.program.wakes) == 2))
        open_, catchup = self.program.wakes
        self.assertEqual((open_["reason"], open_["after"], open_["next"]), ("open", 5, 5))
        self.assertEqual((catchup["reason"], catchup["seq"], [i["id"] for i in catchup["items"]], catchup["after"],
                          catchup["next"]), ("catchup", 0, [6, 7, 8, 9, 10], 5, 10))
        self.assertEqual(self.game.asked("kit.app.recent"), [{"app": "cocreate", "since": 5, "limit": D.CATCHUP}])
        self.assertTrue(await self.until(lambda: self.path.exists() and self.kept()["cursors"] == {"fx": {"cocreate": 10}}))

    async def test_a_gap_is_caught_up_and_repeats_are_left_out(self):
        a = await self.running()
        self.game.items = [item(n) for n in range(11, 16)]
        self.wake(13, 15)                                            # 11 to 13 never came
        self.assertTrue(await self.until(lambda: len(self.program.wakes) == 3))
        self.assertEqual([(w["reason"], [i["id"] for i in w["items"]], w["after"], w["next"])
                          for w in self.program.wakes[1:]], [("catchup", [11, 12, 13], 10, 13), ("item", [14, 15], 13, 15)])
        self.wake(13, 15)                                            # sent again: given already
        self.wake(14, 16)                                            # one of its two is new
        self.assertTrue(await self.until(lambda: len(self.program.wakes) == 4))
        self.assertEqual(([i["id"] for i in self.program.wakes[3]["items"]], a.cursor), ([16], 16))
        self.drive.heard("app.cocreate", {"app": "cocreate", "seq": 9, "reason": "idle", "items": [], "skipped": 0,
                                          "after": 16, "next": 16})   # one that brings nothing comes as it is
        self.assertTrue(await self.until(lambda: len(self.program.wakes) == 5))
        self.assertEqual(self.program.wakes[4]["reason"], "idle")
        self.game.items = []                                         # a gap the app keeps no more: on from there
        self.wake(20, 21)
        self.assertTrue(await self.until(lambda: len(self.program.wakes) == 6))
        self.assertEqual(([i["id"] for i in self.program.wakes[5]["items"]], a.cursor), ([21], 21))

    async def test_one_wake_at_a_time(self):
        a = await self.running()
        self.program.hold = asyncio.Event()
        self.wake(10, 11)
        self.wake(11, 12)
        self.wake(12, 13)
        await asyncio.sleep(0.1)
        self.assertEqual((len(self.program.wakes), a.cursor), (2, 10))    # the second waits for the first's answer
        self.program.hold.set()
        self.assertTrue(await self.until(lambda: a.cursor == 13))
        self.assertEqual([w["next"] for w in self.program.wakes], [10, 11, 12, 13])

    async def test_a_program_that_reads_too_slowly(self):
        a = await self.running()
        self.program.hold = asyncio.Event()
        with mock.patch.object(D, "WAKES_MOST", 3):
            for n in range(10, 20):
                self.wake(n, n + 1)
        self.game.items = [item(n) for n in range(11, 21)]
        await asyncio.sleep(0.05)
        self.program.hold.set()
        self.assertTrue(await self.until(lambda: a.cursor == 20), self.program.wakes)
        ids = [i["id"] for w in self.program.wakes for i in w["items"]]
        self.assertEqual(ids, list(range(11, 21)))                   # none lost, none twice: the gap was caught up
        self.assertIn("catchup", self.program.reasons())

    async def test_an_error_answer_is_noted_and_it_goes_on(self):
        a = await self.running()
        self.program.error = RpcError(1, "the wake failed")
        self.wake(10, 11)
        self.assertTrue(await self.until(lambda: a.cursor == 11))
        self.assertTrue(any("was answered with an error: the wake failed" in n for n in self.notes), self.notes)
        self.program.error = None
        self.wake(11, 12)
        self.assertTrue(await self.until(lambda: a.cursor == 12))

    async def test_a_process_that_went_gets_the_items_again(self):
        a = await self.running()
        self.program.error = ApiError(503, "down", "fx exited (code 4)")
        self.game.items = [item(11), item(12)]
        self.wake(10, 12)
        self.assertTrue(await self.until(lambda: self.state() == "gone"))
        self.assertEqual((a.cursor, a.task.done(), self.kept()["cursors"]), (10, True, {"fx": {"cocreate": 10}}))
        self.drive.sync({})                                          # its process ended: driven no more, and stopped
        self.assertTrue(await self.until(lambda: self.game.asked("kit.app.stop") == [{"app": "cocreate",
                                                                                     "why": "extension"}]))
        self.program = Program()
        self.drive.wake = self.program.wake
        await self.running()                                         # it runs again: from where it got
        self.assertTrue(await self.until(lambda: len(self.program.wakes) == 2))
        self.assertEqual((self.program.reasons(), [i["id"] for i in self.program.wakes[1]["items"]]),
                         (["open", "catchup"], [11, 12]))

    async def test_a_pipe_that_takes_no_line(self):
        a = await self.running()
        self.program.error = ApiError(429, "busy", "fx is busy")
        self.game.items = [item(11)]
        self.wake(10, 11)
        self.assertTrue(await self.until(lambda: a.missed))
        self.assertEqual((a.cursor, self.state()), (10, "running"))
        self.program.error = None                                    # caught up at its next beat
        self.assertTrue(await self.until(lambda: a.cursor == 11), self.view())
        self.assertEqual(self.program.reasons()[-1], "catchup")


class Ends(Case):
    async def test_the_players_stop_and_start(self):
        await self.running()
        self.game.on, self.game.why = False, "player"
        self.drive.heard("kit.app", {"app": "cocreate", "on": False, "why": "player"})
        self.assertTrue(await self.until(lambda: self.state() == "stopped"))
        opened = len(self.game.asked("kit.app.open"))
        self.drive.sync({"cocreate": "fx"}, "up")                    # the link back, the apps declared anew: theirs
        self.wake(10, 11)                                            # (and no wake is one to open it for)
        await asyncio.sleep(0.15)
        self.assertEqual((self.state(), len(self.game.asked("kit.app.open")), len(self.program.wakes)),
                         ("stopped", opened, 1))
        self.assertTrue(any("the player stopped it" in n for n in self.notes))
        self.game.on = True                                          # the card's 开始: WuxianKit started it itself
        self.drive.heard("kit.app", {"app": "cocreate", "on": True})
        self.assertTrue(await self.until(lambda: self.state() == "running" and len(self.program.wakes) >= 2))
        self.assertEqual(self.program.reasons()[:2], ["open", "open"])
        self.assertEqual(len(self.game.asked("kit.app.open")), opened + 1)

    async def test_the_players_stop_ends_with_kit_ready(self):
        await self.running()
        self.game.on, self.game.why = False, "player"
        self.drive.heard("kit.app", {"app": "cocreate", "on": False, "why": "player"})
        self.assertTrue(await self.until(lambda: self.state() == "stopped"))
        self.drive.sync({"cocreate": "fx"}, "ready")
        self.assertTrue(await self.until(lambda: self.state() == "running" and len(self.program.wakes) == 2))

    async def test_stopped_by_the_player_before_its_program_started(self):
        self.game.why = "player"
        self.drive.sync({"cocreate": "fx"})
        self.assertTrue(await self.until(lambda: self.state() == "stopped"))
        self.assertEqual([n for n, _ in self.game.calls], ["kit.app.status"])
        self.drive.sync({})
        await asyncio.sleep(0.05)
        self.assertEqual(self.game.asked("kit.app.stop"), [])        # it never ran here: nothing to stop
        self.game.calls.clear()
        self.drive.sync({"cocreate": "fx"}, "ready")                 # a task begun by a kit.ready does not ask
        self.assertTrue(await self.until(lambda: self.state() == "running"))
        self.assertEqual([n for n, _ in self.game.calls][:1], ["kit.app.open"])

    async def test_another_end_opens_it_again(self):
        for why in ("expired", "left", "extension", "stopped", None):
            with self.subTest(why=why):
                self.game, self.program, self.notes = Game(), Program(), []
                self.drive.close()
                self.drive = self.make()
                await self.running()
                self.game.on, self.game.why = False, why
                self.drive.heard("kit.app", dict({"app": "cocreate", "on": False}, **({"why": why} if why else {})))
                self.assertTrue(await self.until(lambda: len(self.game.asked("kit.app.open")) == 2), self.view())
                self.assertTrue(await self.until(lambda: self.program.reasons() == ["open", "open"]))
                self.assertTrue(any(f"ended ({why or 'stopped'}): opened again" in n for n in self.notes), self.notes)
        self.drive.heard("kit.app", {"app": "cocreate", "on": False, "why": "replaced"})    # a new declaration: it runs on
        self.drive.heard("kit.app", {"app": "cocreate", "on": True})
        await asyncio.sleep(0.15)
        self.assertEqual((self.state(), len(self.game.asked("kit.app.open"))), ("running", 2))

    async def test_a_refusal_waits_longer_each_time_and_kit_ready_tries_at_once(self):
        self.game.declared = False
        with mock.patch.multiple(D, REFUSED=0.1, REFUSED_MOST=0.4):
            self.drive.sync({"cocreate": "fx"})
            self.assertTrue(await self.until(lambda: self.state() == "refused"))
            t0 = asyncio.get_running_loop().time()
            self.assertTrue(await self.until(lambda: len(self.game.calls) >= 4))     # 0, 0.1, 0.3, 0.7
            self.assertGreater(asyncio.get_running_loop().time() - t0, 0.5)
            self.assertIn("no app cocreate", self.drive.apps["cocreate"].why)
            self.assertNotIn("stack", self.drive.apps["cocreate"].why)
            with mock.patch.multiple(D, REFUSED=30, REFUSED_MOST=60):
                await self.until(lambda: len(self.game.calls) >= 5)
                self.game.declared = True
                await asyncio.sleep(0.05)
                self.drive.sync({"cocreate": "fx"}, "ready")         # declared by now: at once
                self.assertTrue(await self.until(lambda: self.state() == "running", 3), self.view())
        self.assertTrue(any("WuxianKit did not open it (no app cocreate" in n for n in self.notes))

    async def test_blocked_is_a_refusal(self):
        self.game.blocked = "the extension cocreate is off"
        self.drive.sync({"cocreate": "fx"})
        self.assertTrue(await self.until(lambda: self.state() == "refused"))
        self.assertEqual(self.drive.apps["cocreate"].why, "the extension cocreate is off")
        self.assertEqual(self.program.wakes, [])
        self.game.blocked = None
        self.assertTrue(await self.until(lambda: self.state() == "running"))

    async def test_while_the_game_is_away_it_waits(self):
        self.game.link = "offline"
        self.drive.sync({"cocreate": "fx"})
        await asyncio.sleep(0.2)
        self.assertEqual((self.state(), self.game.calls), ("away", []))      # nothing is asked of a game not there
        self.game.link, self.game.error = "standby", ApiError(409, "link_down", "no frames came")
        self.assertTrue(await self.until(lambda: len(self.game.calls) >= 2))   # it stands by: asked, more and more rarely
        self.assertEqual(self.state(), "away")
        self.game.error = None
        self.drive.sync({"cocreate": "fx"}, "up")
        self.assertTrue(await self.until(lambda: self.state() == "running"))
        self.usable = False                                          # a WuxianKit too old: the same as away
        self.game.on, self.game.why = False, "expired"
        self.drive.heard("kit.app", {"app": "cocreate", "on": False, "why": "expired"})
        self.assertTrue(await self.until(lambda: self.state() == "away"))

    async def test_a_game_without_wuxiankit(self):
        self.game.error = D.Absent()
        self.drive.sync({"cocreate": "fx"})
        self.assertTrue(await self.until(lambda: self.state() == "no_kit"))
        asked = len(self.game.calls)
        await asyncio.sleep(0.3)
        self.assertEqual(len(self.game.calls), asked)                # not again by itself
        self.game.error = None
        self.drive.sync({"cocreate": "fx"}, "ready")
        self.assertTrue(await self.until(lambda: self.state() == "running"))

    async def test_driven_no_more(self):
        a = await self.running()
        self.drive.sync({"cocreate": "fy"})                          # another extension's now: the first one's ends
        self.assertTrue(await self.until(lambda: a.task.done() and self.drive.apps["cocreate"].eid == "fy"))
        self.assertTrue(await self.until(lambda: self.state() == "running"), self.view())
        names = [n for n, _ in self.game.calls]
        self.assertLess(names.index("kit.app.stop"), len(names) - 1 - names[::-1].index("kit.app.open"))   # stop first
        self.assertEqual(self.game.asked("kit.app.stop"), [{"app": "cocreate", "why": "extension"}])
        self.game.link = "offline"                                   # the game away: its lease ends it there
        self.drive.sync({})
        await asyncio.sleep(0.1)
        self.assertEqual((self.drive.view(), len(self.game.asked("kit.app.stop"))), ([], 1))


class Beat(Case):
    def test_the_rule(self):
        self.assertEqual(D.beat_rule(600), (200.0, 400.0))
        self.assertEqual(D.beat_rule(90), (30.0, 0.0))
        self.assertEqual(D.beat_rule(None), D.beat_rule(D.LEASE))
        self.assertEqual(D.beat_rule(0), (None, None))               # no lease: no heartbeat

    async def test_the_heartbeat_and_what_it_finds(self):
        with mock.patch.object(D, "beat_rule", lambda lease: (0.05, 0.15)):
            a = await self.running()
            self.assertTrue(await self.until(lambda: len(self.game.asked("kit.app.status")) >= 4))   # online: each beat
            self.game.link = "standby"                               # standing by: only when the lease must be kept
            await asyncio.sleep(0.1)
            n = len(self.game.asked("kit.app.status"))
            await asyncio.sleep(0.4)
            beats = len(self.game.asked("kit.app.status")) - n
            self.assertTrue(1 <= beats <= 4, beats)
            self.game.link = "online"
            self.game.on, self.game.why = False, "left"              # it ended, and the event did not come
            self.assertTrue(await self.until(lambda: self.program.reasons() == ["open", "open"]), self.view())
            self.assertEqual(a.opened, 2)

    async def test_no_lease_no_heartbeat(self):
        self.game.lease = 0
        with mock.patch.object(D, "AWAY", 0.01):
            await self.running()
            await asyncio.sleep(0.2)
        self.assertEqual(len(self.game.asked("kit.app.status")), 1)  # the one before the open

    async def test_kit_ready_and_the_link_back_read_on(self):
        a = await self.running()
        self.game.items, self.game.next, self.game.sent = [item(11), item(12)], 12, 12     # missed meanwhile
        self.drive.sync({"cocreate": "fx"}, "up")
        self.assertTrue(await self.until(lambda: a.cursor == 12))
        self.assertEqual(self.program.reasons(), ["open", "catchup"])
        self.game.on, self.game.why = False, "reload"                # it did not run through the reload: at once
        self.drive.sync({"cocreate": "fx"}, "ready")
        self.assertTrue(await self.until(lambda: self.program.reasons() == ["open", "catchup", "open"]))
        self.assertEqual(self.program.wakes[-1]["after"], 12)


REPORT = {"app": "cocreate", "key": "1794938824:Horde", "state": "run", "step": "submit", "text": "已上传 3 / 4"}


class Reports(Case):
    def told(self):
        return self.game.asked("kit.app.report")

    async def test_told_at_once(self):
        self.drive.report("fx", REPORT)
        self.assertTrue(await self.until(lambda: len(self.told()) == 1))
        self.assertEqual(self.told(), [dict(REPORT, at=1794938824.5)])
        await asyncio.sleep(0.05)
        self.assertFalse(self.path.exists())                         # it got there: nothing kept

    async def test_a_newer_one_of_its_key_takes_the_place_of_one_that_waits(self):
        gate = asyncio.Event()
        real = self.game.kit

        async def slow(name, args):
            await gate.wait()
            return await real(name, args)
        self.drive.kit = slow
        self.drive.report("fx", dict(REPORT, key="a", done=1))
        await asyncio.sleep(0.02)                                    # the first is on its way
        for n in (2, 3, 4):
            self.drive.report("fx", dict(REPORT, key="a", done=n))
        self.drive.report("fx", dict(REPORT, key="b", done=9))
        gate.set()
        self.assertTrue(await self.until(lambda: len(self.told()) == 3))
        self.assertEqual([(r["key"], r["done"]) for r in self.told()], [("a", 1), ("a", 4), ("b", 9)])

    async def test_kept_while_the_game_is_away_and_told_late(self):
        self.game.link = "offline"
        for n in range(20):
            self.drive.report("fx", dict(REPORT, key=f"k{n}"))
        self.drive.report("fx", dict(REPORT, key="k19", state="done"))
        self.assertTrue(await self.until(lambda: self.path.exists() and len(self.kept()["reports"]["fx"]["cocreate"])
                                         == D.REPORT_KEYS and self.kept()["reports"]["fx"]["cocreate"][-1]["state"]
                                         == "done"))
        kept = self.kept()["reports"]["fx"]["cocreate"]
        self.assertEqual(([r["key"] for r in kept], self.told()), ([f"k{n}" for n in range(4, 20)], []))
        again = self.make()                                          # the daemon started again: they are still there
        self.game.link = "online"
        again.sync({}, "ready")
        self.assertTrue(await self.until(lambda: len(self.told()) == 16))
        self.assertEqual(([r["key"] for r in self.told()], {r["late"] for r in self.told()}, self.told()[-1]["state"]),
                         ([f"k{n}" for n in range(4, 20)], {True}, "done"))
        self.assertTrue(await self.until(lambda: self.kept()["reports"] == {}))

    async def test_a_call_that_fails_keeps_it(self):
        self.game.error = ApiError(409, "link_down", "no frames came")
        self.drive.report("fx", REPORT)
        self.assertTrue(await self.until(lambda: self.path.exists() and self.kept()["reports"]))
        self.game.error = None
        self.drive.sync({}, "up")                                    # the link back: late
        self.assertTrue(await self.until(lambda: len(self.told()) == 2))
        self.assertEqual(self.told()[-1], dict(REPORT, at=1794938824.5, late=True))
        self.drive.report("fx", dict(REPORT, state="done"))          # a new one of the key that gets there
        self.assertTrue(await self.until(lambda: len(self.told()) == 3 and self.kept()["reports"] == {}))
        self.assertNotIn("late", self.told()[-1])

    async def test_refused_once_kept_twice_dropped(self):
        self.game.refuse_reports = True                              # not declared yet, perhaps
        self.drive.report("fx", REPORT)
        self.assertTrue(await self.until(lambda: self.path.exists() and self.kept()["reports"]))
        self.assertTrue(any("WuxianKit did not take a report of its app cocreate" in n for n in self.notes))
        self.drive.sync({}, "ready")
        self.assertTrue(await self.until(lambda: len(self.told()) == 2 and self.kept()["reports"] == {}))

    async def test_a_wuxiankit_too_old_is_told_none(self):
        self.usable = False
        self.drive.report("fx", REPORT)
        self.assertTrue(await self.until(lambda: self.path.exists() and self.kept()["reports"]))
        self.assertEqual(self.told(), [])

    async def test_forgotten_with_the_extension(self):
        self.game.link = "offline"
        self.drive.report("fx", REPORT)
        self.drive.kept["cursors"] = {"fx": {"cocreate": 7}, "fy": {"other": 3}}
        self.assertTrue(await self.until(lambda: self.path.exists() and self.kept()["reports"]))
        self.drive.forget("fx")
        self.assertTrue(await self.until(lambda: self.kept() == {"cursors": {"fy": {"other": 3}}, "reports": {}}))


if __name__ == "__main__":
    unittest.main()
