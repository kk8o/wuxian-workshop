"""The reactions engine (local/reactions.py): specs checked with messages for the agent, items matched by source and
what (topic, kind, prefixed kind, channel, *) and when, cooldowns, owners cleared, the game's hear union told only when
it changes, failures kept on the reaction, loops (one call of a reaction at a time, MAX_RUNNING of all) and the
reactions an app declares itself."""
import asyncio
import unittest

from wuxianworkshop.local.reactions import MAX_APP_REACTIONS, MAX_REACTIONS, MAX_RUNNING, ReactionError, Reactions

KINDS = {"voice.play": "point", "voice.say": "point", "voice.prepare": "keep", "sense.bags": "see",
         "chat.send": "say", "tune.cvar.set": "do"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Fake:
    """the engine with fake callbacks: calls and hears recorded, a capability may fail"""

    def __init__(self, local=("voice",)):
        self.calls, self.hears, self.notes, self.fail = [], [], [], {}
        self.hear_fail = None
        self.clock = Clock()
        self.local = set(local)
        self.r = Reactions(self.call, self.hear, KINDS.get, now=self.clock, note=self.notes.append,
                           local_sources=lambda: self.local)

    async def call(self, cap, args, caller):
        self.calls.append((cap, args, caller))
        if cap in self.fail:
            raise self.fail[cap]
        return {"ok": True}

    async def hear(self, entries):
        if self.hear_fail:
            raise RuntimeError(self.hear_fail)
        self.hears.append(entries)


def combat(classification="elite", kind="combat.start"):
    return {"source": "game", "topic": "combat", "kind": kind, "time": 1,
            "data": {"unit": {"classification": classification, "name": "X"}}}


class Checks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = Fake()

    async def bad(self, spec, *words):
        with self.assertRaises(ReactionError) as e:
            await self.f.r.set(spec)
        for w in words:
            self.assertIn(w, str(e.exception))
        return str(e.exception)

    async def test_ids_given_and_replaced(self):
        a = await self.f.r.set({"hear": "game:combat", "call": "voice.play"})
        b = await self.f.r.set({"hear": "game:aura", "call": "voice.play"})
        self.assertEqual((a["id"], b["id"]), ("r1", "r2"))
        self.assertEqual(a["cooldown"], 10)
        c = await self.f.r.set({"id": "r1", "hear": "game:quest", "call": "sense.bags", "note": " quests  "})
        self.assertEqual(c["id"], "r1")
        self.assertEqual([x["id"] for x in self.f.r.list()], ["r2", "r1"])
        self.assertEqual(self.f.r.list()[1]["hear"], "game:quest")
        self.assertEqual(self.f.r.list()[1]["note"], "quests")
        await self.f.r.set({"id": "r3", "hear": "game:travel", "call": "voice.play"})
        d = await self.f.r.set({"hear": "game:growth", "call": "voice.play"})
        self.assertEqual(d["id"], "r4")                 # r3 is taken: skipped

    async def test_bad_fields(self):
        await self.bad("game:combat", "object")
        await self.bad({"hear": "game:combat", "call": "voice.play", "action": 1}, "no action", "hear and call")
        await self.bad({"hear": "game:combat", "call": "voice.play", "id": "a b"}, "id")
        await self.bad({"hear": "game:combat", "call": "voice.play", "owner": "Companion"}, "owner", "app id")

    async def test_bad_hear(self):
        await self.bad({"call": "voice.play"}, "hear is one entry")
        await self.bad({"hear": ["game:combat"], "call": "voice.play"}, "not a list")
        await self.bad({"hear": "combat", "call": "voice.play"}, "<source>:<what>")
        await self.bad({"hear": "game:", "call": "voice.play"}, "<source>:<what>")
        await self.bad({"hear": "Game:combat", "call": "voice.play"}, "<source>:<what>")
        await self.bad({"hear": "game:combat start", "call": "voice.play"}, "<source>:<what>")
        for ok in ("game:*", "game:combat.start", "route:step", "bus:demo.*", "voice:said", "chat:guild"):
            await self.f.r.set({"hear": ok, "call": "voice.play"})

    async def test_bad_call(self):
        await self.bad({"hear": "game:combat"}, "call is a capability id")
        await self.bad({"hear": "game:combat", "call": "wk_voice_play"}, "voice.play")
        await self.bad({"hear": "game:combat", "call": "voice.sing"}, "no capability voice.sing", "wk_docs")
        await self.bad({"hear": "game:combat", "call": "chat.send"}, "say capability", "see, keep or point")
        await self.bad({"hear": "game:combat", "call": "tune.cvar.set"}, "do capability", "proposal")

    async def test_kind_of_may_be_async_or_fail(self):
        async def kind_of(cap):
            return "keep" if cap == "voice.prepare" else None

        def broken(cap):
            raise RuntimeError("link down")

        r = Reactions(self.f.call, self.f.hear, kind_of)
        self.assertEqual((await r.set({"hear": "game:combat", "call": "voice.prepare"}))["call"], "voice.prepare")
        r = Reactions(self.f.call, self.f.hear, broken)
        with self.assertRaises(ReactionError) as e:
            await r.set({"hear": "game:combat", "call": "voice.prepare"})
        self.assertIn("link down", str(e.exception))

    async def test_bad_args_item_cooldown_note(self):
        base = {"hear": "game:combat", "call": "voice.play"}
        await self.bad(dict(base, args=["a"]), "args is an object")
        await self.bad(dict(base, item="the item"), "item is the name")
        await self.bad(dict(base, args={"event": 1}, item="event"), "args gives too")
        await self.bad(dict(base, cooldown=-1), "cooldown")
        await self.bad(dict(base, cooldown="10"), "cooldown")
        await self.bad(dict(base, cooldown=True), "cooldown")
        await self.bad(dict(base, note=5), "note")
        await self.bad(dict(base, note="x" * 300), "note")
        row = await self.f.r.set(dict(base, cooldown=0, item="event", args={"pool": "a"}))
        self.assertEqual((row["cooldown"], row["item"], row["args"]), (0, "event", {"pool": "a"}))

    async def test_bad_when(self):
        base = {"hear": "game:combat", "call": "voice.play"}
        await self.bad(dict(base, when=["data.x"]), "when is an object")
        await self.bad(dict(base, when={"data..x": 1}), "no path")
        await self.bad(dict(base, when={"data.x": []}), "1 to 16 values")
        await self.bad(dict(base, when={"data.x": [{"a": 1}]}), "values")
        await self.bad(dict(base, when={"data.unit": {"classification": "elite"}}), "longer path")
        await self.bad(dict(base, when={f"data.k{i}": 1 for i in range(9)}), "at most 8")

    async def test_at_most_32(self):
        for i in range(MAX_REACTIONS):
            await self.f.r.set({"hear": "game:combat", "call": "voice.play"})
        msg = await self.bad({"hear": "game:combat", "call": "voice.play"}, "at most 32", "reaction_clear")
        self.assertTrue(msg)
        await self.bad({"id": "new", "hear": "game:combat", "call": "voice.play"}, "at most 32")
        row = await self.f.r.set({"id": "r5", "hear": "game:aura", "call": "voice.play"})   # replacing is fine
        self.assertEqual(row["hear"], "game:aura")
        self.assertEqual(len(self.f.r.list()), MAX_REACTIONS)


class Matching(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = Fake()

    async def fired(self, hear, item, when=None):
        """whether a reaction hearing hear (and when) calls on item"""
        f = Fake()
        await f.r.set({"hear": hear, "call": "voice.play", "when": when})
        await f.r.item(item)
        await f.r.drain()
        return len(f.calls) == 1

    async def test_game_topic_kind_star(self):
        item = combat()
        self.assertTrue(await self.fired("game:combat", item))
        self.assertTrue(await self.fired("game:combat.start", item))
        self.assertTrue(await self.fired("game:*", item))
        self.assertFalse(await self.fired("game:combat.kill", item))
        self.assertFalse(await self.fired("game:aura", item))
        self.assertFalse(await self.fired("route:*", item))
        self.assertFalse(await self.fired("game:start", item))        # a game kind has no game. prefix to drop

    async def test_route_kind_with_prefix(self):
        item = {"source": "route", "kind": "route.step", "data": {"key": "k"}, "time": 1}
        self.assertTrue(await self.fired("route:step", item))
        self.assertTrue(await self.fired("route:*", item))
        self.assertFalse(await self.fired("route:done", item))
        self.assertFalse(await self.fired("game:step", item))

    async def test_local_source(self):
        item = {"source": "voice", "kind": "voice.said", "data": {"text": "hi", "seconds": 1.2}, "time": 1}
        self.assertTrue(await self.fired("voice:said", item))
        self.assertTrue(await self.fired("voice:*", item))
        self.assertFalse(await self.fired("voice:spoke", item))

    async def test_chat_channel_and_bus_prefix(self):
        line = {"source": "chat", "channel": "guild", "text": "hi", "time": 1}
        self.assertTrue(await self.fired("chat:guild", line))
        self.assertTrue(await self.fired("chat:*", line))
        self.assertFalse(await self.fired("chat:party", line))
        bus = {"source": "bus", "topic": "demo.ping", "data": {}}
        self.assertTrue(await self.fired("bus:demo.*", bus))
        self.assertTrue(await self.fired("bus:demo.ping", bus))
        self.assertFalse(await self.fired("bus:demox.*", bus))

    async def test_when_paths_and_lists(self):
        item = combat("rareelite")
        self.assertTrue(await self.fired("game:combat", item, {"data.unit.classification": ["elite", "rareelite"]}))
        self.assertFalse(await self.fired("game:combat", item, {"data.unit.classification": "elite"}))
        self.assertTrue(await self.fired("game:combat", item, {"data.unit.classification": "rareelite",
                                                                "kind": "combat.start"}))
        self.assertFalse(await self.fired("game:combat", item, {"data.unit.classification": "rareelite",
                                                                 "kind": "combat.end"}))
        self.assertFalse(await self.fired("game:combat", item, {"data.unit.level": 60}))      # missing: no
        self.assertFalse(await self.fired("game:combat", item, {"data.unit.level": None}))   # missing is no null
        listy = {"source": "game", "topic": "quest", "kind": "quest.turnin",
                 "data": {"rewards": [{"n": 1}, {"n": 2}], "elite": True, "count": 1}}
        self.assertTrue(await self.fired("game:quest", listy, {"data.rewards.2.n": 2}))
        self.assertFalse(await self.fired("game:quest", listy, {"data.rewards.3.n": 2}))
        self.assertTrue(await self.fired("game:quest", listy, {"data.elite": True}))
        self.assertFalse(await self.fired("game:quest", listy, {"data.elite": 1}))          # true is no 1
        self.assertFalse(await self.fired("game:quest", listy, {"data.count": True}))

    async def test_junk_items_ignored(self):
        await self.f.r.set({"hear": "game:*", "call": "voice.play"})
        for junk in (None, "x", {"kind": "combat.start"}, {"source": 5}):
            await self.f.r.item(junk)
        await self.f.r.drain()
        self.assertEqual(self.f.calls, [])


class Calling(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = Fake()

    async def test_call_args_item_caller(self):
        row = await self.f.r.set({"hear": "game:combat.start", "call": "voice.play", "args": {"pool": "elite"},
                                  "item": "event"})
        item = combat()
        await self.f.r.item(item)
        await self.f.r.drain()
        cap, args, caller = self.f.calls[0]
        self.assertEqual(cap, "voice.play")
        self.assertEqual(args, {"pool": "elite", "event": item})
        self.assertIsNot(args["event"], item)
        self.assertEqual(caller, {"by": "workshop", "chain": [f"reaction:{row['id']}"]})
        args["pool"] = "changed"                                 # the reaction's own args stay
        self.assertEqual(self.f.r.list()[0]["args"], {"pool": "elite"})
        r = self.f.r.list()[0]
        self.assertEqual((r["fired"], r["failed"], r["last"], r["error"]), (1, 0, 1000.0, None))

    async def test_one_item_many_reactions(self):
        await self.f.r.set({"hear": "game:combat", "call": "voice.play"})
        await self.f.r.set({"hear": "game:combat.start", "call": "sense.bags"})
        await self.f.r.set({"hear": "game:aura", "call": "voice.say"})
        await self.f.r.item(combat())
        await self.f.r.drain()
        self.assertEqual(sorted(c[0] for c in self.f.calls), ["sense.bags", "voice.play"])

    async def test_cooldown(self):
        await self.f.r.set({"hear": "game:combat", "call": "voice.play"})            # 10 s
        await self.f.r.set({"hear": "game:combat", "call": "sense.bags", "cooldown": 0})
        await self.f.r.set({"hear": "game:combat", "call": "voice.say", "cooldown": 2.5})
        for dt in (0, 1, 2, 9.9, 0.1):
            self.f.clock.t += dt
            await self.f.r.item(combat())
            await self.f.r.drain()                       # a call that is not done would hold the next one back
        counts = {cap: sum(1 for c in self.f.calls if c[0] == cap) for cap in KINDS}
        self.assertEqual(counts["voice.play"], 2)        # at 0 and 12.9
        self.assertEqual(counts["sense.bags"], 5)
        self.assertEqual(counts["voice.say"], 3)         # at 0, 3 and 12.9 (13.0 is 0.1 after)
        self.assertEqual(self.f.r.list()[0]["fired"], 2)

    async def test_when_failing_spends_no_cooldown(self):
        await self.f.r.set({"hear": "game:combat", "call": "voice.play", "when": {"data.unit.classification": "elite"}})
        await self.f.r.item(combat("normal"))
        await self.f.r.item(combat("elite"))
        await self.f.r.drain()
        self.assertEqual(len(self.f.calls), 1)

    async def test_failure_kept_not_raised(self):
        class ApiError(Exception):
            def __init__(self, code, message):
                super().__init__(message)
                self.code, self.message = code, message

        self.f.fail["voice.play"] = ApiError("down", "voice did not start:\n  no model")
        await self.f.r.set({"hear": "game:combat", "call": "voice.play", "cooldown": 0})
        await self.f.r.item(combat())
        await self.f.r.drain()
        r = self.f.r.list()[0]
        self.assertEqual((r["fired"], r["failed"]), (1, 1))
        self.assertEqual(r["error"], "down: voice did not start: no model")
        self.assertTrue(any("voice.play failed" in n for n in self.f.notes))
        del self.f.fail["voice.play"]                     # a later success clears it
        await self.f.r.item(combat())
        await self.f.r.drain()
        r = self.f.r.list()[0]
        self.assertEqual((r["fired"], r["failed"], r["error"]), (2, 1, None))

    async def test_calls_run_in_background(self):
        gate = asyncio.Event()

        async def slow(cap, args, caller):
            await gate.wait()
            self.f.calls.append(cap)

        r = Reactions(slow, self.f.hear, KINDS.get)
        await r.set({"hear": "game:combat", "call": "voice.play"})
        await asyncio.wait_for(r.item(combat()), 1)       # item() does not wait for the call
        self.assertEqual(self.f.calls, [])
        gate.set()
        await r.drain()
        self.assertEqual(self.f.calls, ["voice.play"])


class Loops(unittest.IsolatedAsyncioTestCase):
    """a reaction calls once at a time; MAX_RUNNING calls of all reactions at once"""

    async def asyncSetUp(self):
        self.f = Fake()
        self.gate, self.calls = asyncio.Event(), []

        async def slow(cap, args, caller):
            self.calls.append(caller["chain"][0])
            await self.gate.wait()

        self.r = Reactions(slow, self.f.hear, KINDS.get, now=self.f.clock, note=self.f.notes.append)

    async def asyncTearDown(self):
        self.gate.set()
        await self.r.drain()

    async def test_not_again_while_its_call_runs(self):
        await self.r.set({"hear": "game:combat", "call": "voice.play", "cooldown": 0})
        for _ in range(3):
            await self.r.item(combat())
            await asyncio.sleep(0)
        self.assertEqual(self.calls, ["reaction:r1"])
        self.assertEqual(self.r.list()[0]["fired"], 1)   # what it missed is not counted, and spends no cooldown
        self.gate.set()
        await self.r.drain()
        await self.r.item(combat())                      # done: the next item calls again
        await self.r.drain()
        self.assertEqual(self.calls, ["reaction:r1", "reaction:r1"])
        self.assertEqual(self.r.list()[0]["fired"], 2)

    async def test_at_most_16_calls_at_once(self):
        for _ in range(MAX_RUNNING + 2):
            await self.r.set({"hear": "game:combat", "call": "voice.play", "cooldown": 0})
        await self.r.item(combat())
        await asyncio.sleep(0)
        self.assertEqual(len(self.calls), MAX_RUNNING)
        rows = self.r.list()
        self.assertEqual([r["fired"] for r in rows], [1] * MAX_RUNNING + [0, 0])
        await self.r.item(combat())                      # still full: nothing more, and said once
        crowded = [n for n in self.f.notes if "calls run at once" in n]
        self.assertEqual(len(crowded), 1, self.f.notes)
        self.gate.set()
        await self.r.drain()
        await self.r.item(combat())                      # room again: 16 more, and full again is said again
        await self.r.drain()
        self.assertEqual(len(self.calls), 2 * MAX_RUNNING)
        self.assertEqual(len([n for n in self.f.notes if "calls run at once" in n]), 2)


class Declared(unittest.IsolatedAsyncioTestCase):
    """the reactions an app's manifest declares: there while the app runs"""

    SPECS = [{"hear": "game:combat.start", "call": "voice.play", "args": {"pool": "pull"}, "cooldown": 0},
             {"hear": "voice:said", "call": "voice.prepare", "note": "the next line"}]

    async def asyncSetUp(self):
        self.f = Fake()

    def ids(self):
        return [r["id"] for r in self.f.r.list()]

    async def test_set_while_the_app_runs(self):
        self.assertEqual(await self.f.r.app_started("companion", self.SPECS), [])
        rows = self.f.r.list()
        self.assertEqual([(r["id"], r["owner"], r["hear"], r["call"]) for r in rows],
                         [("companion.1", "companion", "game:combat.start", "voice.play"),
                          ("companion.2", "companion", "voice:said", "voice.prepare")])
        self.assertEqual(rows[0]["args"], {"pool": "pull"})
        self.assertEqual(self.f.hears, [["game:combat.start"]])
        await self.f.r.item(combat())
        await self.f.r.drain()
        self.assertEqual(self.f.calls, [("voice.play", {"pool": "pull"},
                                         {"by": "workshop", "chain": ["reaction:companion.1"]})])
        self.assertEqual(await self.f.r.app_started("companion", self.SPECS), [])    # again: nothing changes
        self.assertEqual(self.f.r.list()[0]["fired"], 1)
        self.assertEqual(len(self.f.hears), 1)
        await self.f.r.app_stopped("companion")
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.f.hears[-1], [])
        self.assertEqual(await self.f.r.app_started("companion", self.SPECS), [])    # runs again: back, counts new
        self.assertEqual(self.ids(), ["companion.1", "companion.2"])
        self.assertEqual(self.f.r.list()[0]["fired"], 0)

    async def test_other_specs_replace_them(self):
        await self.f.r.app_started("companion", self.SPECS)
        await self.f.r.set({"hear": "game:aura", "call": "voice.play", "owner": "companion"})      # the agent's
        self.assertEqual(await self.f.r.app_started("companion", self.SPECS[:1]), [])
        self.assertEqual(self.ids(), ["r1", "companion.1"])
        self.assertEqual(await self.f.r.app_started("companion", []), [])
        self.assertEqual(self.ids(), ["r1"])
        await self.f.r.app_started("companion", self.SPECS)
        await self.f.r.app_stopped("companion")                                     # the agent's go with the app too
        self.assertEqual(self.ids(), [])

    async def test_bad_specs_said_and_tried_again(self):
        specs = [self.SPECS[0], {"hear": "game:combat", "call": "later.thing"}, "junk",
                 {"hear": "game:combat", "call": "chat.send"}]
        problems = await self.f.r.app_started("companion", specs)
        self.assertEqual(self.ids(), ["companion.1"])
        self.assertEqual(len(problems), 3)
        self.assertIn("reaction 2 of app companion", problems[0])
        self.assertIn("no capability later.thing", problems[0])
        self.assertIn("reaction 3 of app companion: a reaction is an object", problems[1])
        self.assertIn("say capability", problems[2])
        KINDS["later.thing"] = "point"                    # its extension described itself: the same specs are taken
        try:
            problems = await self.f.r.app_started("companion", specs)
        finally:
            del KINDS["later.thing"]
        self.assertEqual(len(problems), 2)
        self.assertEqual(self.ids(), ["companion.1", "companion.2"])
        self.assertEqual(await self.f.r.app_started("Not An App", self.SPECS), ["'Not An App' is no app id"])

    async def test_only_the_declared_fields(self):
        await self.f.r.app_started("companion", [dict(self.SPECS[0], id="mine", owner="guild", fired=9)])
        row = self.f.r.list()[0]
        self.assertEqual((row["id"], row["owner"], row["fired"]), ("companion.1", "companion", 0))

    async def test_their_ids_are_theirs(self):
        await self.f.r.app_started("companion", self.SPECS)
        with self.assertRaises(ReactionError) as e:
            await self.f.r.set({"id": "companion.1", "hear": "game:aura", "call": "voice.play"})
        self.assertIn("declares itself", str(e.exception))
        self.assertEqual(self.f.r.list()[0]["hear"], "game:combat.start")

    async def test_not_counted_in_the_agents_32(self):
        many = [{"hear": "game:combat", "call": "voice.play"}] * (MAX_APP_REACTIONS + 3)
        self.assertEqual(await self.f.r.app_started("companion", many), [])
        self.assertEqual(len(self.ids()), MAX_APP_REACTIONS)                        # the first 16 of a manifest
        for _ in range(MAX_REACTIONS):
            await self.f.r.set({"hear": "game:combat", "call": "voice.play"})
        with self.assertRaises(ReactionError):
            await self.f.r.set({"hear": "game:combat", "call": "voice.play"})
        self.assertEqual(len(self.ids()), MAX_APP_REACTIONS + MAX_REACTIONS)

    async def test_cleared_ones_come_back_when_it_runs_again(self):
        await self.f.r.app_started("companion", self.SPECS)
        self.assertEqual(await self.f.r.clear(id="companion.2"), 1)
        self.assertEqual(self.ids(), ["companion.1"])
        self.assertEqual(await self.f.r.app_started("companion", self.SPECS), [])
        self.assertEqual(self.ids(), ["companion.1", "companion.2"])


class Owners(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = Fake()
        await self.f.r.set({"hear": "game:combat", "call": "voice.play", "owner": "companion"})
        await self.f.r.set({"hear": "game:aura", "call": "voice.play", "owner": "companion"})
        await self.f.r.set({"hear": "game:quest", "call": "voice.play", "owner": "guild"})
        await self.f.r.set({"hear": "voice:said", "call": "voice.prepare"})

    def ids(self):
        return [r["id"] for r in self.f.r.list()]

    async def test_clear_by_id_owner_all(self):
        self.assertEqual(await self.f.r.clear(id="nope"), 0)
        self.assertEqual(await self.f.r.clear(id="r3", owner="companion"), 0)     # r3 is guild's
        self.assertEqual(await self.f.r.clear(id="r1"), 1)
        self.assertEqual(self.ids(), ["r2", "r3", "r4"])
        self.assertEqual(await self.f.r.clear(owner="companion"), 1)
        self.assertEqual(self.ids(), ["r3", "r4"])
        self.assertEqual(await self.f.r.clear(), 2)
        self.assertEqual(self.ids(), [])

    async def test_app_stopped(self):
        await self.f.r.app_stopped("companion")
        self.assertEqual(self.ids(), ["r3", "r4"])
        await self.f.r.app_stopped("nobody")
        self.assertEqual(self.ids(), ["r3", "r4"])
        await self.f.r.item(combat())
        await self.f.r.drain()
        self.assertEqual(self.f.calls, [])


class Hearing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = Fake()

    async def test_union_told_when_it_changes(self):
        r = self.f.r
        await r.set({"hear": "game:combat", "call": "voice.play", "owner": "companion"})
        await r.set({"hear": "game:combat", "call": "sense.bags", "owner": "companion"})     # same union
        await r.set({"hear": "voice:said", "call": "voice.prepare"})                         # local: not told
        await r.set({"hear": "route:step", "call": "voice.play"})
        self.assertEqual(self.f.hears, [["game:combat"], ["game:combat", "route:step"]])
        await r.clear(id="r2")                                                               # still game:combat
        self.assertEqual(len(self.f.hears), 2)
        await r.app_stopped("companion")
        self.assertEqual(self.f.hears[-1], ["route:step"])
        await r.clear()
        self.assertEqual(self.f.hears[-1], [])
        self.assertEqual(len(self.f.hears), 4)

    async def test_local_sources_change(self):
        self.f.local = set()
        await self.f.r.set({"hear": "voice:said", "call": "voice.play"})
        self.assertEqual(self.f.hears, [["voice:said"]])          # not known as local yet: the game's
        self.f.local = {"voice"}
        await self.f.r.refresh()
        self.assertEqual(self.f.hears[-1], [])
        await self.f.r.refresh()
        self.assertEqual(len(self.f.hears), 2)

    async def test_without_local_sources_all_are_the_game(self):
        hears = []

        async def hear(entries):
            hears.append(entries)

        r = Reactions(self.f.call, hear, KINDS.get)
        await r.set({"hear": "voice:said", "call": "voice.play"})
        self.assertEqual(hears, [["voice:said"]])

    async def test_hear_failure_kept_and_retried(self):
        self.f.hear_fail = "game not running"
        await self.f.r.set({"hear": "game:combat", "call": "voice.play"})
        await self.f.r.set({"hear": "voice:said", "call": "voice.play"})
        rows = self.f.r.list()
        self.assertEqual(rows[0]["error"], "not heard: game not running")
        self.assertIsNone(rows[1]["error"])                       # a local one is heard all the same
        self.f.hear_fail = None
        await self.f.r.refresh()                                  # same union, told again after a failure
        self.assertEqual(self.f.hears, [["game:combat"]])
        self.assertIsNone(self.f.r.list()[0]["error"])

    async def test_sync_callbacks(self):
        seen = []
        r = Reactions(lambda cap, args, caller: seen.append(cap), seen.append, KINDS.get)
        await r.set({"hear": "game:combat", "call": "voice.play"})
        await r.item(combat())
        await r.drain()
        self.assertEqual(seen, [["game:combat"], "voice.play"])


if __name__ == "__main__":
    unittest.main()
