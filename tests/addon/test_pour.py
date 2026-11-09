"""Pours (WoWBridge 0.9.13, link features 4) end to end: the real Agent.lua and Link.lua under Lua 5.1
(tests/support/wowmock.py), the real companion (transport/link.py) and the Service's pour (daemon/service.py). The rest
of a long answer goes up in whole segments for one data call "pour": they are cut by the text alone (asked again, they
are numbered and cut the same), at most two wait in the queue unshown, a RUN result goes before them, both stores (a
probe's, a data call's) pour, `only` sends the ones asked for; `run` with whole brings a long value back whole. Pour
here is what tests/addon/test_pour_trouble.py drives too (a lost segment, the link down, an addon that does not pour)."""
import asyncio
import re
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.support.wowmock import Session, lupa
from wuxianworkshop.daemon import service as service_mod
from wuxianworkshop.daemon.api import ApiError, parse_run
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.transport import link

SEGMENT = 32 * 1024
COUNT_SENDS = b"""
local ns = ...
local send = ns.Link.Send
Queued = {}                                    -- when each segment went into the link's queue
ns.Link.Send = function(text, kind, pour)
	if kind == "pour" then Queued[#Queued + 1] = GetTime() end
	return send(text, kind, pour)
end
"""


def text_of(n, seed=7):
    """about n bytes of text that squeezes as the game's answers do: ASCII, Chinese, tabs, newlines, numbers"""
    out, x, size = [], seed, 0
    words = ["物品", "15347", "\t", "拍卖师", "1:20000:0:3;", "Horde", "\n", "无限", "é", "x"]
    while size < n:
        x = (x * 1103515245 + 12345) % 2 ** 31
        w = words[x % len(words)] if x % 7 else str(x % 100000)
        out.append(w)
        size += len(w.encode())
    return "".join(out)


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Pour(unittest.TestCase):
    features = None                       # what the addon says it can do (None: as it is)

    def setUp(self):
        self.timeline, self.segments, self.drop = [], [], set()
        self.s = s = Session(self)
        self.addCleanup(s.close)
        if self.features is not None:
            s.ns[b"Link"][b"FEATURES"] = self.features
        self.journal = Journal()

        def on_debug(kind, text):
            self.timeline.append((s.now(), kind, text))
            self.journal.add(kind, text)
        self.comp = comp = link.Companion(s.addons, clock=s.now, log=lambda text: None, on_debug=on_debug)
        s.comp = comp
        self.svc = svc = Service(lambda **kw: None, journal=self.journal)
        svc.worker = SimpleNamespace(comp=comp)

        def on_pour(sid, key, i, m, data):
            self.segments.append((s.now(), key, i, m, data))
            if (key, i) in self.drop:                         # lost whole on its way to the daemon: once
                self.drop.discard((key, i))
                return
            svc.on_pour(sid, key, i, m, data)
        comp.on_pour = on_pour
        self.asked = []                                           # (verb, payload) of every data call
        data_call = comp.data_call

        def recorded(verb, payload):
            self.asked.append((verb, payload))
            return data_call(verb, payload)
        comp.data_call = recorded
        svc.run, svc.data, svc.data_calls = self.run_stub, self.data_stub, comp.data_calls
        for name, value in (("_now", s.now), ("POUR_POLL", 0)):   # a pour's waits in the game's time
            p = mock.patch.object(service_mod, name, value)
            p.start()
            self.addCleanup(p.stop)
        s.lua.execute(COUNT_SENDS, s.ns)
        comp.new_process("P1-100")
        s.login()
        s.run(8)

    def results(self, job):
        return [(t, text) for t, k, text in self.timeline if k == "RUN" and text.startswith(f"{job} ")]

    async def result(self, job, timeout_ms):
        t0 = self.s.now()
        while self.s.now() - t0 < timeout_ms / 1000:
            got = self.results(job)
            if got:
                return Service.run_result(parse_run(got[0][1]))
            await asyncio.sleep(0)
        raise ApiError(504, "timeout", f"no result for job {job}")

    async def run_stub(self, code, timeout_ms=10000, addon=None, chunk="=run"):     # the daemon's run, over the mock
        return await self.result(self.comp.code(code.encode("utf-8"), chunk, addon or "-"), timeout_ms)

    async def data_stub(self, verb, payload, timeout_ms=10000, what="call"):
        job = self.comp.data_call(verb, payload)
        return job, await self.result(job, timeout_ms)

    def drive(self, coro, each=None, step=0.25, limit=900):
        """coro on an asyncio loop while the game runs `step` seconds at a time (each(): after every step), at most
        `limit` seconds of the game"""
        async def go():
            task = asyncio.ensure_future(coro)
            t0 = self.s.now()
            while not task.done():
                if self.s.now() - t0 > limit:
                    task.cancel()
                    self.fail(f"not done in {limit} s of the game")
                self.s.run(step)
                if each:
                    each()
                await asyncio.sleep(0)
            return task.result()
        return asyncio.run(go())

    def whole(self, value, **kw):
        self.s.lua.globals()[b"BIG"] = value.encode()
        return self.drive(self.svc.run_whole("return BIG"), **kw)

    def calls(self, verb):
        """what the data calls of a verb asked for"""
        return [payload for v, payload in self.asked if v == verb]

    def queued(self):
        return list(self.s.lua.globals()[b"Queued"].values())


class Poured(Pour):
    def test_a_long_value_comes_whole(self):
        """200 KB: the first piece with the run's result, the rest in six segments of at most 32 KB that end between
        two UTF-8 characters, for one data call; the journal says how it went"""
        value = text_of(200_000)
        res = self.whole(value)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["whole"], res["cut"], res["values"]), (True, None, [value]))
        segs = sorted({i: d for _, _, i, _, d in self.segments}.items())
        self.assertEqual([i for i, _ in segs], list(range(1, 7)))
        self.assertTrue(all(len(d) <= SEGMENT for _, d in segs))
        self.assertTrue(all(len(d) >= SEGMENT - 3 for _, d in segs[:-1]))
        for _, d in segs:
            d.decode("utf-8")                                     # whole characters: decodes strictly
        self.assertEqual(len(self.calls("pour")), 1)
        self.assertEqual(self.calls("pour")[0], {"k": self.segments[0][1], "g": True})
        notes = [e["text"] for e in self.journal.since(0, 1000, ["INFO"])[0]]
        self.assertTrue(any(re.match(r"run: its answer poured, 6 segments, \d+ KB in [\d.]+ s$", n) for n in notes), notes)
        self.assertTrue(self.comp.stats["squeezed"] >= 6)             # they went squeezed (TYPE_ZIP)

    def test_the_panel_shows_how_far_a_pour_is(self):
        """a pour is no RUN result, so it has a line of its own in the debug log (the panel's 最近 shows it): the
        segments in so far while it goes, then how many, how big and how long"""
        console = self.s.ns[b"Console"]
        seen = []

        def look():
            got = console.Recent(1)[1]
            if got and got[b"text"] not in seen:
                seen.append(got[b"text"])
        res = self.whole(text_of(200_000, seed=5), each=look)
        self.assertTrue(res["ok"], res)
        texts = [t.decode() for t in seen]
        self.assertTrue(any(re.fullmatch(r"long answer going up: [1-5]/6 segments", t) for t in texts), texts)
        self.s.run(5)                                             # the last acknowledgements reach the game
        last = console.Recent(1)[1][b"text"].decode()
        self.assertRegex(last, r"^long answer sent: 6 segments · \d+ KB · \d+ s$")

    def test_two_wait_at_most_and_a_run_result_goes_first(self):
        """no more than two segments wait in the queue unshown (each 32 KB to squeeze, and the queue holds 256 messages
        at most); a run sent meanwhile has its result back within a few frames, before the rest of the pour"""
        value = text_of(200_000, seed=3)
        L = self.s.ns[b"Link"]
        most, sent = [0], {}

        def each():
            most[0] = max(most[0], L[b"Waiting"](b"pour")[0])
            if len(self.segments) == 1 and not sent:
                sent["job"], sent["at"] = self.comp.code(b"return 'quick'", "=run"), self.s.now()
        res = self.whole(value, each=each, step=0.05)
        self.assertEqual(res["values"], [value])
        self.assertEqual(most[0], 2)
        (at, text), = self.results(sent["job"])
        self.assertIn('"quick"', text)
        self.assertLess(at - sent["at"], 3.0)
        self.assertLess(at, self.segments[-1][0] - 4)                 # long before the last segment

    def test_segments_are_cut_by_the_text_alone_and_only_sends_those_asked_for(self):
        """a pour asked again cuts and numbers the segments the same; `only` sends those alone; a pour of an answer
        that is not kept says so"""
        value = text_of(110_000, seed=5)
        self.whole(value)
        key = self.segments[0][1]
        first = {i: d for _, k, i, _, d in self.segments if k == key}

        async def ask(payload):
            return (await self.svc.data_result("pour", payload, 10000, "test"))
        before = len(self.segments)
        res = self.drive(ask({"k": key, "g": True}))
        self.assertEqual(res["values"], [f"{len(first)} {sum(map(len, first.values()))}"])
        self.drive(self.until(lambda: len(self.segments) >= before + len(first)))
        again = {i: d for _, k, i, _, d in self.segments[before:]}
        self.assertEqual(again, first)                                # the same cuts, the same numbers
        before = len(self.segments)
        self.drive(ask({"k": key, "g": True, "only": [2, 4, 99, "x"]}))
        self.drive(self.until(lambda: len(self.segments) >= before + 2))
        self.s.run(10)
        self.assertEqual([(i, d) for _, _, i, _, d in self.segments[before:]], [(2, first[2]), (4, first[4])])
        res = self.drive(ask({"k": "nothing", "g": True}))
        self.assertFalse(res["ok"])
        self.assertIn("no longer kept", res["error"])

    def until(self, done):
        async def wait():
            while not done():
                await asyncio.sleep(0)
        return wait()

    def test_both_stores_pour(self):
        """a data call's long answer (Agent.lua keeps it under its job) pours as a probe's does"""
        s = self.s
        s.lua.execute(b'FooWB = WoWBridge.Bind("Foo") FooWB:Expose("long", function(a) return string.rep(a.s, a.n) end)')
        res = self.drive(self.svc.call_exposed("Foo", "long", {"s": "无限\t", "n": 20000}))
        self.assertEqual(res["result"], "无限\t" * 20000)
        keys = {k for _, k, *_ in self.segments}
        (key,) = keys
        self.assertTrue(key.isdigit())                                 # the data call's job
        self.assertEqual(self.calls("pour"), [{"k": key, "g": False}])
        self.assertEqual(self.calls("piece"), [])


if __name__ == "__main__":
    unittest.main()
