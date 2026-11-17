"""OnPour (WoWBridge 0.9.15): an addon hears how far the long answer of a name it exposed is on its way up, end to end:
the real API.lua, Agent.lua and Link.lua under Lua 5.1 (tests/support/wowmock.py), the real companion and the Service's
call_exposed with whole (tests/addon/test_pour.py, test_call_whole.py). fn(info) once as the pour starts (done 0), once
for every segment acknowledged, done == total at the end; registering again replaces it, nil removes it; an fn that
raises goes to the error handler and the pour goes on; an answer that fits in one piece, or a probe's, calls nothing;
a pour of a few segments asked again (`only`) counts those."""
import unittest

from tests.addon.test_pour import Pour, text_of

FOO = b"""
FooWB = WoWBridge.Bind("Foo")
FooWB:Expose("big", function() return BIG end)
FooWB:Expose("small", function() return "hi" end)
function Record(into)                          -- an fn for OnPour that keeps a copy of every info
	return function(info)
		into[#into + 1] = { done = info.done, total = info.total, bytes = info.bytes, seconds = info.seconds,
			answer = info.answer }
	end
end
"""


class OnPour(Pour):
    def setUp(self):
        super().setUp()
        self.s.lua.execute(FOO)

    def run_lua(self, code):
        self.s.lua.execute(code.encode())

    def seen(self, name):
        t = self.s.lua.globals()[name.encode()]
        return [{k.decode(): v for k, v in t[i].items()} for i in range(1, len(t) + 1)]

    def call(self, value, name="big"):
        self.s.lua.globals()[b"BIG"] = value.encode()
        before = len(self.segments)
        res = self.drive(self.svc.call_exposed("Foo", name, whole=True))
        self.s.run(5)                                             # the last acknowledgements reach the game
        segs = {i: d for _, k, i, _, d in self.segments[before:]}
        return res, segs

    def test_once_as_it_starts_once_a_segment_and_done_at_the_end(self):
        self.run_lua('Seen, Other = {}, {} FooWB:OnPour("big", Record(Seen)) FooWB:OnPour("small", Record(Other))')
        value = text_of(300_000, seed=8)
        res, segs = self.call(value)
        self.assertEqual(res["result"], value)
        m, rest = len(segs), sum(map(len, segs.values()))
        self.assertGreaterEqual(m, 9)
        seen = self.seen("Seen")
        self.assertEqual([e["done"] for e in seen], list(range(m + 1)))      # 0 as it starts, then one a segment
        self.assertEqual({e["total"] for e in seen}, {m})
        self.assertEqual({e["answer"] for e in seen}, {rest})
        self.assertEqual((seen[0]["bytes"], seen[0]["seconds"]), (0, 0))
        self.assertEqual(seen[-1]["bytes"], rest)                             # done == total: all of it is in
        for a, b in zip(seen, seen[1:]):
            self.assertLessEqual(a["bytes"], b["bytes"])
            self.assertLessEqual(a["seconds"], b["seconds"])
        self.assertGreater(seen[-1]["seconds"], 0)
        self.assertEqual(self.seen("Other"), [])                              # another name's fn hears nothing

    def test_registering_again_replaces_and_nil_removes(self):
        self.run_lua('A, B = {}, {} FooWB:OnPour("big", Record(A)) FooWB:OnPour("big", Record(B))')
        _, segs = self.call(text_of(120_000, seed=2))
        self.assertEqual(self.seen("A"), [])
        self.assertEqual(len(self.seen("B")), len(segs) + 1)
        self.run_lua('FooWB:OnPour("big", nil)')
        res, segs = self.call(text_of(120_000, seed=3))
        self.assertTrue(segs and res["whole"])
        self.assertEqual(len(self.seen("B")), len(segs) + 1)                 # no more calls
        bad = self.s.lua.eval(b'select(2, pcall(FooWB.OnPour, FooWB, "big", 5))').decode()
        self.assertIn("fn is a function, or nil", bad)

    def test_an_fn_that_raises_goes_to_the_error_handler_and_the_pour_goes_on(self):
        g = self.s.lua.globals()
        errors = len(g[b"__errors"])
        self.run_lua('Calls = 0 FooWB:OnPour("big", function() Calls = Calls + 1 error("boom") end)')
        value = text_of(200_000, seed=4)
        res, segs = self.call(value)
        self.assertEqual(res["result"], value)
        self.assertEqual(g[b"Calls"], len(segs) + 1)
        raised = [g[b"__errors"][i] for i in range(errors + 1, len(g[b"__errors"]) + 1)]
        self.assertEqual(len(raised), len(segs) + 1)
        self.assertTrue(all(b"boom" in (e if isinstance(e, bytes) else str(e).encode()) for e in raised), raised)

    def test_no_calls_without_a_pour(self):
        """an answer that fits in one piece does not pour; a probe's pour (run with whole) is no exposed name's"""
        self.run_lua('Seen = {} FooWB:OnPour("small", Record(Seen)) FooWB:OnPour("big", Record(Seen))')
        res, segs = self.call("", name="small")
        self.assertEqual((res["result"], segs), ("hi", {}))
        res = self.whole(text_of(120_000, seed=6))
        self.assertTrue(res["ok"])
        self.assertTrue(self.segments)
        self.assertEqual(self.seen("Seen"), [])

    def test_a_pour_of_a_few_counts_those(self):
        """`only` (the daemon asks for the segments it misses): total is how many this pour sends"""
        self.run_lua('Seen = {} FooWB:OnPour("big", Record(Seen))')
        _, segs = self.call(text_of(200_000, seed=5))
        key = self.segments[-1][1]
        first = len(self.seen("Seen"))

        async def ask(payload):
            return await self.svc.data_result("pour", payload, 10000, "test")
        self.drive(ask({"k": key, "g": False, "only": [2, 4]}))
        self.s.run(10)
        again = self.seen("Seen")[first:]
        self.assertEqual([(e["done"], e["total"]) for e in again], [(0, 2), (1, 2), (2, 2)])
        self.assertEqual(again[-1]["bytes"], len(segs[2]) + len(segs[4]))
        self.assertEqual({e["answer"] for e in again}, {sum(map(len, segs.values()))})


if __name__ == "__main__":
    unittest.main()
