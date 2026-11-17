"""OnProgress (WoWBridge 0.10.0): an addon hears how far the long answer of a name it exposed is on its way up, end to
end: the real API.lua, Agent.lua and Link.lua under Lua 5.1 (tests/support/wowmock.py), the real companion and the
Service's call_exposed (tests/addon/test_pour.py). fn(info) once as the answer starts to go up and once for every
segment that is in, info = {id, sent, size, seconds}: sent counts the whole answer, only grows (a segment sent again
adds nothing) and equals size at the end; registering again replaces the fn, nil removes it; an fn that raises goes to
the error handler and the answer goes on; an answer that fits in one piece, or a probe's, calls nothing. There is no
OnPour any more."""
import unittest

from tests.addon.test_pour import Pour, text_of

FOO = b"""
FooWB = WoWBridge.Bind("Foo")
FooWB:Expose("big", function() return BIG end)
FooWB:Expose("small", function() return "hi" end)
function Record(into)                          -- an fn for OnProgress that keeps a copy of every info
	return function(info)
		local n = 0
		for _ in pairs(info) do n = n + 1 end
		into[#into + 1] = { id = info.id, sent = info.sent, size = info.size, seconds = info.seconds, fields = n }
	end
end
"""


class OnProgress(Pour):
    def setUp(self):
        super().setUp()
        self.s.lua.execute(FOO)

    def run_lua(self, code):
        self.s.lua.execute(code.encode())

    def seen(self, name):
        t = self.s.lua.globals()[name.encode()]
        return [{k.decode(): (v.decode() if isinstance(v, bytes) else v) for k, v in t[i].items()}
                for i in range(1, len(t) + 1)]

    def call(self, value, name="big", each=None):
        self.s.lua.globals()[b"BIG"] = value.encode()
        before = len(self.segments)
        res = self.drive(self.svc.call_exposed("Foo", name), each=each)
        self.s.run(5)                                             # the last acknowledgements reach the game
        segs = {i: d for _, k, i, _, d in self.segments[before:]}
        return res, segs

    def test_once_as_it_starts_once_a_segment_and_all_of_it_at_the_end(self):
        self.run_lua('Seen, Other = {}, {} FooWB:OnProgress("big", Record(Seen)) FooWB:OnProgress("small", Record(Other))')
        value = text_of(300_000, seed=8)
        res, segs = self.call(value)
        self.assertEqual(res["result"], value)
        m, rest = len(segs), sum(map(len, segs.values()))
        self.assertGreaterEqual(m, 9)
        seen = self.seen("Seen")
        self.assertEqual(len(seen), m + 1)                                    # as it starts, then one a segment
        self.assertEqual({e["fields"] for e in seen}, {4})                    # id, sent, size, seconds: no more
        self.assertEqual({e["id"] for e in seen}, {self.segments[-1][1]})     # this call's: its job, a string
        (size,) = {e["size"] for e in seen}
        first = size - rest                                                   # the piece that came with the result
        self.assertTrue(3897 <= first <= 3900, first)
        self.assertEqual((seen[0]["sent"], seen[0]["seconds"]), (first, 0))   # the app has that one: it asked for more
        self.assertEqual(seen[-1]["sent"], size)                              # sent == size: all of it is in
        for a, b in zip(seen, seen[1:]):
            self.assertLess(a["sent"], b["sent"])
            self.assertLessEqual(a["seconds"], b["seconds"])
        self.assertEqual(sorted(b["sent"] - a["sent"] for a, b in zip(seen, seen[1:])), sorted(map(len, segs.values())))
        self.assertGreater(seen[-1]["seconds"], 0)
        self.assertEqual(self.seen("Other"), [])                              # another name's fn hears nothing

    def test_registering_again_replaces_and_nil_removes(self):
        self.run_lua('A, B = {}, {} FooWB:OnProgress("big", Record(A)) FooWB:OnProgress("big", Record(B))')
        _, segs = self.call(text_of(120_000, seed=2))
        self.assertEqual(self.seen("A"), [])
        self.assertEqual(len(self.seen("B")), len(segs) + 1)
        self.run_lua('FooWB:OnProgress("big", nil)')
        res, segs = self.call(text_of(120_000, seed=3))
        self.assertTrue(segs and res["ok"])
        self.assertEqual(len(self.seen("B")), len(segs) + 1)                 # no more calls
        bad = self.s.lua.eval(b'select(2, pcall(FooWB.OnProgress, FooWB, "big", 5))').decode()
        self.assertIn("OnProgress(name, fn): fn is a function, or nil", bad)
        bad = self.s.lua.eval(b'select(2, pcall(FooWB.OnProgress, FooWB, "a b", print))').decode()
        self.assertIn("OnProgress(name, fn): the name is", bad)
        self.assertTrue(self.s.lua.eval(b"FooWB.OnPour == nil"))              # no alias of the 0.9.15 name

    def test_an_fn_that_raises_goes_to_the_error_handler_and_the_answer_goes_on(self):
        g = self.s.lua.globals()
        errors = len(g[b"__errors"])
        self.run_lua('Calls = 0 FooWB:OnProgress("big", function() Calls = Calls + 1 error("boom") end)')
        value = text_of(200_000, seed=4)
        res, segs = self.call(value)
        self.assertEqual(res["result"], value)
        self.assertEqual(g[b"Calls"], len(segs) + 1)
        raised = [g[b"__errors"][i] for i in range(errors + 1, len(g[b"__errors"]) + 1)]
        self.assertEqual(len(raised), len(segs) + 1)
        self.assertTrue(all(b"boom" in (e if isinstance(e, bytes) else str(e).encode()) for e in raised), raised)

    def test_no_calls_for_one_piece_or_a_probe(self):
        """an answer that fits in one piece is not told; a probe's long answer (run with whole) is no exposed name's"""
        self.run_lua('Seen = {} FooWB:OnProgress("small", Record(Seen)) FooWB:OnProgress("big", Record(Seen))')
        res, segs = self.call("", name="small")
        self.assertEqual((res["result"], segs), ("hi", {}))
        res = self.whole(text_of(120_000, seed=6))
        self.assertTrue(res["ok"])
        self.assertTrue(self.segments)
        self.assertEqual(self.seen("Seen"), [])

    def test_a_segment_sent_again_adds_nothing(self):
        """sent counts each segment once, when it is first in: a segment the daemon missed and asked for again (the
        game had its acknowledgement), and segments asked for once all were in, change nothing and call nothing"""
        self.run_lua('Seen = {} FooWB:OnProgress("big", Record(Seen))')

        def lose():                                               # segment 3 never reaches the daemon's pour
            if self.segments and not self.drop and not hasattr(self, "lost"):
                self.lost = self.segments[0][1]
                self.drop.add((self.lost, 3))
        value = text_of(200_000, seed=5)
        res, segs = self.call(value, each=lose)
        self.assertEqual(res["result"], value)
        self.assertEqual(self.calls("pour")[1:], [{"k": self.lost, "g": False, "only": [3]}])
        self.assertEqual([i for _, _, i, _, _ in self.segments].count(3), 2)
        seen = self.seen("Seen")
        self.assertEqual(len(seen), len(segs) + 1)                # once as it started, once a segment: 3 once
        sent = [e["sent"] for e in seen]
        self.assertEqual(sent, sorted(set(sent)))                 # it only grew
        self.assertEqual(sent[-1], seen[-1]["size"])

        async def ask(payload):
            return await self.svc.data_result("pour", payload, 10000, "test")
        before = len(self.segments)
        self.drive(ask({"k": self.lost, "g": False, "only": [2, 4]}))
        self.s.run(10)
        self.assertEqual(len(self.segments), before + 2)          # they went up again
        self.assertEqual(self.seen("Seen"), seen)                 # and nobody was told: nothing new is in

    def test_a_pour_of_a_few_first(self):
        """the answer's first pour starts it, whichever segments it sends; the ones a later pour adds count on top"""
        self.run_lua('Seen = {} FooWB:OnProgress("big", Record(Seen))')
        s, comp = self.s, self.comp
        s.lua.globals()[b"BIG"] = text_of(150_000, seed=7).encode()

        def result(verb, payload):
            job = comp.data_call(verb, payload)
            t0 = s.now()
            while s.now() - t0 < 20:
                s.run(0.5)
                if self.results(job):
                    return str(job)
            self.fail(f"no result for {verb}")
        key = result("call", {"a": "Foo", "n": "big", "d": None, "m": 1 << 24})
        self.assertEqual(self.seen("Seen"), [])                   # nobody asked for the rest yet
        result("pour", {"k": key, "g": False, "only": [2]})
        s.run(10)
        segs = {i: d for _, _, i, _, d in self.segments}
        start, two = self.seen("Seen")
        self.assertEqual(two["sent"] - start["sent"], len(segs[2]))
        result("pour", {"k": key, "g": False})
        s.run(30)
        segs = {i: d for _, _, i, _, d in self.segments}
        seen = self.seen("Seen")
        self.assertEqual(len(seen), len(segs) + 1)                # 2 went twice and counted once
        self.assertEqual(seen[-1]["sent"], seen[-1]["size"])
        self.assertEqual(seen[-1]["size"] - start["sent"], sum(map(len, segs.values())))
        self.assertGreater(seen[-1]["seconds"], seen[1]["seconds"])   # from when it first started


if __name__ == "__main__":
    unittest.main()
