"""trace and inspect in the Service (daemon/service.py) over a stand-in for `run`: the two runs of a trace with the wait
between, one trace at a time, the restricted events refused before anything is sent, inspect's picture of the frame
it found, and the errors the game's answer can carry."""
import asyncio
import json
import unittest

from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service


class Worker:
    def __init__(self, **kw):
        pass


class Probes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.sent, self.chunks, self.snaps, self.answer = [], [], [], {}

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            self.sent.append(code)
            self.chunks.append(chunk)
            if "RegisterEvent" in code:
                return dict(ok=True, values=['{"tracing":2,"unknown":["NOT_AN_EVENT"]}'])
            if "UnregisterAllEvents" in code:
                return dict(ok=True, values=[json.dumps({"seconds": 1.02, "dropped": 0, "events": [[0.5, "BAG_UPDATE", [0], 1]],
                                                         "counts": {"BAG_UPDATE": 3, "LOOT_OPENED": 1}})])
            return self.answer.get("inspect", dict(ok=True, values=[json.dumps(
                {"screen": [1311, 983], "frames": [{"name": "MyFrame", "rect": [5, 900, 200, 100]}]})]))

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

    async def test_errors_from_the_game(self):
        self.answer["inspect"] = dict(ok=True, values=['{"error":"target: the expression gave nil (no such frame)"}'])
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("Nope")
        self.assertEqual((cm.exception.status, cm.exception.code), (400, "probe_failed"))
        self.assertIn("no such frame", cm.exception.message)
        self.answer["inspect"] = dict(ok=False, error='[string "=run"]:3: attempt to index a nil value', stack="...")
        with self.assertRaises(ApiError) as cm:
            await self.svc.inspect("Nope")
        self.assertEqual((cm.exception.status, cm.exception.code), (502, "lua_error"))


if __name__ == "__main__":
    unittest.main()
