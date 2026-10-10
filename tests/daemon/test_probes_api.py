"""trace and inspect in the Service (daemon/service.py) over a stand-in for `run`: the two runs of a trace with the wait
between, one trace at a time, the restricted events refused before anything is sent, inspect's picture of the frame
it found, a long answer brought back in pieces, and the errors the game's answer can carry."""
import asyncio
import json
import re
import unittest
from types import SimpleNamespace

from wuxianworkshop.agent import probes
from wuxianworkshop.daemon.api import ApiError, parse_run
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service


class Worker:
    def __init__(self, **kw):
        pass


def whole(text):
    """a probe's run result when its answer is one piece (probes.answer_chunk)"""
    return dict(ok=True, values=["1/1\n" + text])


class Probes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.sent, self.chunks, self.snaps, self.answer = [], [], [], {}

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            self.sent.append(code)
            self.chunks.append(chunk)
            if "RegisterEvent" in code:
                return whole('{"tracing":2,"unknown":["NOT_AN_EVENT"]}')
            if "UnregisterAllEvents" in code:
                return whole(json.dumps({"seconds": 1.02, "dropped": 0, "events": [[0.5, "BAG_UPDATE", [0], 1]],
                                         "counts": {"BAG_UPDATE": 3, "LOOT_OPENED": 1}}))
            return self.answer.get("inspect", whole(json.dumps(
                {"screen": [1311, 983], "frames": [{"name": "MyFrame", "rect": [5, 900, 200, 100]}]})))

        async def snap(region=None, max_width=1280):
            self.snaps.append((region, max_width))
            return dict(path="x.png", width=region[2], height=region[3], url="/api/snaps/x.png")

        self.svc.run, self.svc.snap = run, snap

    async def test_trace(self):
        res = await self.svc.trace(1, "BAG_UPDATE, NOT_AN_EVENT", 50)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.chunks, ["=probe", "=probe"])     # the app's own look: WoWBridge leaves it out of its window
        self.assertIn("BAG_UPDATE", self.sent[0])
        self.assertEqual((res["registered"], res["unknown"], res["kept"]), (2, ["NOT_AN_EVENT"], 1))
        self.assertEqual(res["counts"][0], dict(event="BAG_UPDATE", count=3))
        self.assertEqual(res["events"][0], dict(t=0.5, event="BAG_UPDATE", args=[0], n=1))
        self.assertIn("trace: 1.02 s, 4 events of 2 kinds, 1 kept (BAG_UPDATE, NOT_AN_EVENT)",
                      [e["text"] for e in self.svc.journal.since(0, 100, None)[0]])

    async def test_one_trace_at_a_time_and_bad_requests(self):
        first = asyncio.create_task(self.svc.trace(1))
        await asyncio.sleep(0.05)
        with self.assertRaises(ApiError) as cm:
            await self.svc.trace(1)
        self.assertEqual(cm.exception.status, 409)
        await first
        for kw in (dict(seconds=0), dict(seconds=500), dict(seconds=5, max_events=0), dict(seconds=5, events="COMBAT_LOG_EVENT_UNFILTERED")):
            with self.assertRaises(ApiError) as cm:
                await self.svc.trace(**kw)
            self.assertEqual(cm.exception.status, 400, kw)
        self.assertEqual(len(self.sent), 2)                     # the refused ones sent nothing

    async def test_inspect_and_its_picture(self):
        res = await self.svc.inspect("MyFrame", snap=True)
        self.assertEqual(res["frames"][0]["name"], "MyFrame")
        self.assertEqual(self.snaps, [([0, 888, 224, 95], None)])     # 12 px around it, kept inside 1311 x 983
        self.assertEqual(res["snap"]["path"], "x.png")
        self.assertNotIn("snap", await self.svc.inspect("MyFrame"))
        with self.assertRaises(ApiError):
            await self.svc.inspect(None)
        self.answer["inspect"] = whole('{"screen":[1311,983],"frames":[{"name":"Bar","rect":"<secret>"}]}')
        res = await self.svc.inspect("Bar", snap=True)                 # a secret place: no picture, no error
        self.assertEqual((res["frames"][0]["rect"], "snap" in res, len(self.snaps)), ("<secret>", False, 1))

    async def test_errors_from_the_game(self):
        self.answer["inspect"] = whole('{"error":"target: the expression gave nil (no such frame)"}')
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("Nope")
        self.assertEqual((cm.exception.status, cm.exception.code), (400, "probe_failed"))
        self.assertIn("no such frame", cm.exception.message)
        self.answer["inspect"] = dict(ok=False, error='[string "=run"]:3: attempt to index a nil value', stack="...")
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("Nope")
        self.assertEqual((cm.exception.status, cm.exception.code), (502, "lua_error"))

    async def test_a_long_answer_comes_in_pieces(self):
        """seen in the game: inspect WoWBridgeConsole, depth 2, answered 16821 bytes, and the RUN result stops at 4000
        (the JSON broke at char 3940). Now the first run brings the first piece and how many there are, the others are
        asked for probes.BATCH at a time, each its own RUN result; their ", " and " (" are JSON text (api.parse_run)"""
        frames = [dict(name=f"Row{i}", type="FontString", text=f'第 {i} 行 → a, b (OnUnload ok), "x"\n') for i in range(120)]
        text = json.dumps(dict(screen=[1287, 965], frames=frames), ensure_ascii=False, separators=(",", ":"))
        pieces = [text[i:i + 1000] for i in range(0, len(text), 1000)]
        keys, flight = [], dict(now=0, most=0)

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            self.sent.append(code)
            m = re.match(r'local KEY, I = "(\w+)", (\d+)\n', code)
            if m:
                keys.append(m[1])
            else:
                keys.append(re.search(r'local KEY, PIECE, MOST = "(\w+)"', code)[1])
            i = int(m[2]) if m else 1
            flight["now"] += 1
            flight["most"] = max(flight["most"], flight["now"])
            await asyncio.sleep(0.01)                           # the link brings them back one after another
            flight["now"] -= 1
            return Service.run_result(parse_run(f"{70 + i} ok {chunk} ({len(code)} B, 0.1 ms): "
                                                f"{i}/{len(pieces)}\n{pieces[i - 1]}"))
        self.svc.run = run
        res = await self.svc.inspect("WoWBridgeConsole", depth=2)
        self.assertEqual(res["frames"], frames)
        self.assertEqual(len(self.sent), len(pieces))
        self.assertEqual(len(set(keys)), 1)                       # the same answer, kept under one key
        self.assertIn("local ANSWER = (function(...) ", self.sent[0])
        self.assertEqual(flight["most"], probes.BATCH)          # asked for together: they share mailbox packets

    async def test_where_the_answer_breaks(self):
        """a broken answer is said with its place: what is wrong, at which character of how many, the text around it"""
        self.answer["inspect"] = whole('{"screen":[1287,965],"frames":[{"visible":true,"regions":[{"visi')
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("WoWBridgeConsole", depth=2)
        self.assertEqual((cm.exception.status, cm.exception.code), (400, "probe_failed"))
        self.assertEqual(cm.exception.message, "the game's answer is not JSON: Unterminated string starting at char 59 of "
                                               '64: {"screen":[1287,965],"frames":[{"visible":true,"regions":[{<<HERE>>'
                                               '"visi<<END>>')
        self.answer["inspect"] = whole('{"w":' + "1" * 200 + ',"h":-nan(ind),"s":"' + "x" * 200 + '"}')
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("Odd")
        self.assertIn("Expecting value at char 210 of 427: …" + "1" * 75 + ',"h":<<HERE>>-nan(ind),"s":"' + "x" * 65 + "…",
                      cm.exception.message)

    async def test_a_piece_the_game_no_longer_keeps(self):
        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            if code.startswith("local KEY, I = "):              # a /reload between the pieces
                return dict(ok=False, error="the rest of this answer is no longer kept in the game (the UI reloaded?)",
                            stack="")
            return dict(ok=True, values=['1/3\n{"screen":[1287,965],"frames":['])
        self.svc.run = run
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("WoWBridgeConsole", depth=2)
        self.assertEqual((cm.exception.status, cm.exception.code), (502, "lua_error"))
        self.assertIn("no longer kept", cm.exception.message)


class PiecesAgain(unittest.IsolatedAsyncioTestCase):
    """a piece of a long answer the link lost is asked for again (Service.piece_again): after a timeout, or once the
    link is back when it was down (a minimized window); never after another error, after a reload, or past PIECE_TRIES"""

    def setUp(self):
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.svc.worker.comp = self.comp = SimpleNamespace(link_up=True, current=5)
        text = json.dumps(dict(screen=[1, 2], frames=[dict(name=f"Frame{i}") for i in range(300)]))
        self.pieces = [text[i:i + 1000] for i in range(0, len(text), 1000)]
        self.asked, self.trouble = [], {}

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            m = re.match(r'local KEY, I = "(\w+)", (\d+)\n', code)
            i = int(m[2]) if m else 1
            self.asked.append(i)
            what = self.trouble.get(i)
            if what:
                return what(i)
            return dict(ok=True, values=[f"{i}/{len(self.pieces)}\n{self.pieces[i - 1]}"])
        self.svc.run = run

    def once(self, i, error, then=None):
        def fail(_):
            del self.trouble[i]
            if then:
                then()
            raise error
        self.trouble[i] = fail

    def notes(self):
        return [e["text"] for e in self.svc.journal.since(0, 100, None)[0] if "asked for again" in e["text"]]

    async def test_a_timeout_and_the_link_down(self):
        self.assertGreaterEqual(len(self.pieces), 5)
        self.once(3, ApiError(504, "timeout", "no result for job 7 within 10000 ms"))

        def minimized():
            self.comp.link_up = False                       # back a moment later
            asyncio.get_running_loop().call_later(0.4, setattr, self.comp, "link_up", True)
        self.once(4, ApiError(409, "link_down", "run: no frames come from the game"), minimized)
        t0 = asyncio.get_running_loop().time()
        res = await self.svc.inspect("Somewhere", depth=2)
        self.assertEqual(len(res["frames"]), 300)
        self.assertGreaterEqual(asyncio.get_running_loop().time() - t0, 0.4)    # it waited for the link
        self.assertEqual(sorted(self.asked), sorted([1, 2, 3, 3, 4, 4] + list(range(5, len(self.pieces) + 1))))
        self.assertEqual(self.notes(), ["piece 3 of a long answer asked for again (timeout)",
                                        "piece 4 of a long answer asked for again (link_down)"])

    async def test_what_is_not_asked_for_again(self):
        def gone(i):                                        # a reload took the answer: a Lua error, said at once
            return dict(ok=False, error="the rest of this answer is no longer kept in the game (the UI reloaded?)",
                        stack="")
        self.trouble[2] = gone
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("Somewhere", depth=2)
        self.assertEqual(cm.exception.code, "lua_error")
        self.assertEqual(self.asked.count(2), 1)
        self.asked.clear()
        self.trouble[2] = lambda i: (_ for _ in ()).throw(ApiError(504, "timeout", "no result"))   # never comes
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("Somewhere", depth=2)
        self.assertEqual((cm.exception.code, self.asked.count(2)), ("timeout", 3))      # PIECE_TRIES
        self.asked.clear()

        def reloaded():
            self.comp.link_up, self.comp.current = False, 6  # another UI session: the answer went with the old one
        self.once(2, ApiError(409, "link_down", "run: no frames come from the game"), reloaded)
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("Somewhere", depth=2)
        self.assertEqual((cm.exception.code, self.asked.count(2)), ("link_down", 1))
        self.assertEqual(self.notes(), ["piece 2 of a long answer asked for again (timeout)"] * 2)


if __name__ == "__main__":
    unittest.main()
