"""trace and inspect in the Service (daemon/service.py) over a stand-in for `run`: the two runs of a trace with the wait
between, one trace at a time, the restricted events refused before anything is sent, inspect's picture of the frame
it found, a long answer brought back (its first piece, the rest poured), and the errors the game's answer can
carry."""
import asyncio
import json
import re
import unittest
from types import SimpleNamespace

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

        async def run(code, timeout_ms=10000, addon=None, chunk="=run", hold=0):
            self.sent.append(code)
            self.chunks.append(chunk)
            if "RegisterEvent" in code:
                return whole('{"tracing":2,"unknown":["NOT_AN_EVENT"]}')
            if "UnregisterAllEvents" in code:
                return whole(json.dumps({"seconds": 1.02, "dropped": 0, "events": [[0.5, "BAG_UPDATE", [0], 1]],
                                         "counts": {"BAG_UPDATE": 3, "LOOT_OPENED": 1}}))
            return self.answer.get("inspect", whole(json.dumps(
                {"screen": [1311, 983], "frames": [{"name": "MyFrame", "rect": [5, 900, 200, 100]}]})))

        async def snap(region=None, max_width=1280, format=None):
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

    async def test_a_trace_given_up_is_stopped_in_the_game(self):
        """the client went away during the wait, or the action of a try with events failed: the game is told to stop
        catching events (else it went on, every event an addon may register, until a reload)"""
        task = asyncio.create_task(self.svc.trace(5))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(["RegisterEvent" in c for c in self.sent], [True, False])
        self.assertIn("UnregisterAllEvents", self.sent[1])
        self.sent.clear()
        run = self.svc.run

        async def failing(code, timeout_ms=10000, addon=None, chunk="=run", hold=0):
            if chunk == "=run":
                raise ApiError(504, "timeout", "no answer")
            return await run(code, timeout_ms, addon, chunk)
        self.svc.run = failing
        with self.assertRaises(ApiError):
            await self.svc.try_("DoIt()", seconds=1, events="BAG_UPDATE")
        self.assertEqual(len(self.sent), 2)
        self.assertIn("UnregisterAllEvents", self.sent[1])
        self.assertFalse(self.svc.tracing.locked())

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

    async def test_what_the_game_has(self):
        """api_scan: one part of the game scan (wuxian api-scan) as a probe, the tables' names checked before anything
        is sent"""
        self.answer["inspect"] = whole('{"client":"1.60.1.70338","replaced":[],"fields":{"Enum.GameRule":{"A":1}}}')
        res = await self.svc.api_scan("fields", ["Enum.GameRule"])
        self.assertEqual(res["fields"], {"Enum.GameRule": {"A": 1}})
        self.assertIn('local PART, NAMES = "fields", {"Enum.GameRule"}', self.sent[-1])
        self.assertEqual(self.chunks[-1], "=probe")
        for part, names in (("everything", None), ("fields", None), ("fields", "Enum.GameRule"), ("fields", ["os.exit"]),
                            ("fields", ["Enum.X"] * 1001)):
            with self.assertRaises(ApiError) as cm:
                await self.svc.api_scan(part, names)
            self.assertEqual(cm.exception.status, 400, (part, names))
        self.assertEqual(len(self.sent), 1)                     # the refused ones sent nothing

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

    async def test_a_long_answer_comes_poured(self):
        """seen in the game: inspect WoWBridgeConsole, depth 2, answered 16821 bytes, and the RUN result stops at 4000
        (the JSON broke at char 3940). Now the run brings the first piece and how many pieces the answer makes; the
        rest is poured: one data call, and its segments joined as they are (the first piece's ", " and " (" are JSON
        text: api.parse_run)"""
        frames = [dict(name=f"Row{i}", type="FontString", text=f'第 {i} 行 → a, b (OnUnload ok), "x"\n') for i in range(120)]
        text = json.dumps(dict(screen=[1287, 965], frames=frames), ensure_ascii=False, separators=(",", ":"))
        first, rest = text[:1000], text[1000:].encode("utf-8")
        segments = [rest[i:i + 3001] for i in range(0, len(rest), 3001)]      # cut anywhere, inside a character too
        self.svc.worker.comp = SimpleNamespace(link_up=True, current=5, pours=lambda: True)
        asked = []

        async def run(code, timeout_ms=10000, addon=None, chunk="=run", hold=0):
            self.sent.append(code)
            return Service.run_result(parse_run(f"71 ok {chunk} ({len(code)} B, 0.1 ms): "
                                                f"1/{len(text) // 1000 + 1}\n{first}"))

        async def data(verb, payload, timeout_ms, what):
            asked.append((verb, payload))
            for i, seg in enumerate(segments, 1):                 # the game: every segment, then the pour's result
                self.svc.on_pour(5, payload["k"], i, len(segments), seg)
            return 7, dict(ok=True, values=[f"{len(segments)} {len(rest)}"])
        self.svc.run, self.svc.data = run, data
        res = await self.svc.inspect("WoWBridgeConsole", depth=2)
        self.assertEqual(res["frames"], frames)
        self.assertEqual(len(self.sent), 1)                       # the probe ran once
        self.assertIn("local ANSWER = (function(...) ", self.sent[0])
        key = re.search(r'local KEY, PIECE, MOST, WHOLE = "(\w+)"', self.sent[0])[1]
        self.assertEqual(asked, [("pour", {"k": key, "g": True})])    # the answer the probe kept under its key

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

    async def test_a_rest_the_game_no_longer_keeps(self):
        self.svc.worker.comp = SimpleNamespace(link_up=True, current=5, pours=lambda: True)

        async def run(code, timeout_ms=10000, addon=None, chunk="=run", hold=0):
            return dict(ok=True, values=['1/3\n{"screen":[1287,965],"frames":['])

        async def data(verb, payload, timeout_ms, what):        # a /reload between the first piece and the pour
            return 7, dict(ok=False, error="the rest of this answer is no longer kept in the game (the UI reloaded?)",
                           stack="")
        self.svc.run, self.svc.data = run, data
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("WoWBridgeConsole", depth=2)
        self.assertEqual((cm.exception.status, cm.exception.code), (502, "lua_error"))
        self.assertIn("no longer kept", cm.exception.message)
        self.assertEqual(self.svc.pours, {})


if __name__ == "__main__":
    unittest.main()
