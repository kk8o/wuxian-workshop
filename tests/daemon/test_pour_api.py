"""The rest of a long answer in the Service (daemon/service.py) over stand-ins for the game: from a WoWBridge that pours
(0.9.13), one data call "pour" and its segments joined in order, whatever order they come in; a segment that never
comes asked for again alone; a link down in the middle waited for; another UI session, a quiet link, a game that says
another size, an error in the game: errors; a pour whose own result is lost goes on with its segments. From an older
addon, the pieces asked for eight at a time, one more as one comes back. run with whole, call with whole (0.9.15: the
data call's "w"), and status's link.pour."""
import asyncio
import json
import re
import unittest
from types import SimpleNamespace
from unittest import mock

from wuxianworkshop.agent import probes
from wuxianworkshop.daemon import service as service_mod
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service


class Worker:
    def __init__(self, **kw):
        pass


FIRST = "首段 " * 10


class Pour(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name, value in (("POUR_ASK", 0.3), ("POUR_QUIET", 0.9), ("POUR_POLL", 0.02)):
            p = mock.patch.object(service_mod, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.svc.worker.comp = self.comp = SimpleNamespace(link_up=True, current=5, pours=lambda: True)
        self.segments = [("段%d " % i).encode() * 50 + bytes([0xe4, 0xb8]) * (i == 2) for i in range(1, 7)]
        self.segments[2] = bytes([0xad]) + self.segments[2]   # 2 ends between the bytes of a character, 3 goes on with it
        self.rest = b"".join(self.segments)
        self.asked, self.codes = [], []
        self.order = [6, 1, 3, 2, 5, 4]                      # the order they come in (the link's way)
        self.said = None                                     # what the pour's RUN result says, if not the truth
        self.answer = self.pour_answer

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            self.codes.append(code)
            return dict(ok=True, job=1, chunk=chunk, values=[f"1/9\n{FIRST}"], ms=0.1, bytes=None, note=None, cut=None)

        async def data(verb, payload, timeout_ms, what):
            self.asked.append(payload)
            return 7, await self.answer(payload)
        self.svc.run, self.svc.data = run, data

    async def pour_answer(self, payload):
        """the game: the RUN result first, then the segments asked for, one after another"""
        only = payload.get("only") or self.order
        asyncio.get_running_loop().create_task(self.send([i for i in self.order if i in only]))
        m, size = self.said or (len(self.segments), len(self.rest))
        return dict(ok=True, values=[f"{m} {size}"])

    async def send(self, numbers, sid=5, key=None):
        for i in numbers:
            await asyncio.sleep(0.01)
            self.svc.on_pour(sid, key or self.key(), i, len(self.segments), self.segments[i - 1])

    def key(self):
        return re.search(r'local KEY, PIECE, MOST, WHOLE = "(\w+)"', self.codes[0])[1]

    def notes(self):
        return [e["text"] for e in self.svc.journal.since(0, 200, None)[0]]

    async def test_the_segments_joined_in_order(self):
        res = await self.svc.run_whole("return BIG")
        self.assertEqual((res["ok"], res["whole"], res["cut"]), (True, True, None))
        self.assertEqual(res["values"], [FIRST + self.rest.decode("utf-8")])
        self.assertEqual(self.asked, [{"k": self.key(), "g": True}])
        self.assertIn(f", {probes.WHOLE_MOST}, true\n", self.codes[0])    # answer_chunk(whole=True): up to 16 MB
        self.assertTrue(any(re.fullmatch(r"run: its answer poured, 6 segments, \d+ KB in [\d.]+ s", n)
                            for n in self.notes()), self.notes())
        self.assertEqual(self.svc.pours, {})

    async def test_another_ones_segments_are_not_taken(self):
        async def answer(payload):
            loop = asyncio.get_running_loop()
            loop.create_task(self.send([1, 2], sid=6))              # another UI session's
            loop.create_task(self.send([1, 2], key="other"))        # another answer's
            return await self.pour_answer(payload)
        self.answer = answer
        res = await self.svc.run_whole("return BIG")
        self.assertEqual(res["values"], [FIRST + self.rest.decode("utf-8")])

    async def test_a_lost_segment_is_asked_for_again_alone(self):
        lost = {4}

        async def answer(payload):
            only = payload.get("only") or self.order
            asyncio.get_running_loop().create_task(self.send([i for i in self.order if i in only and i not in lost]))
            lost.clear()
            return dict(ok=True, values=[f"{len(self.segments)} {len(self.rest)}"])
        self.answer = answer
        res = await self.svc.run_whole("return BIG")
        self.assertEqual(res["values"], [FIRST + self.rest.decode("utf-8")])
        self.assertEqual(self.asked, [{"k": self.key(), "g": True}, {"k": self.key(), "g": True, "only": [4]}])
        self.assertTrue(any(re.fullmatch(r"run: segments 4 of its answer asked for again \(\d s without a new one\)", n)
                            for n in self.notes()), self.notes())

    async def test_the_link_down_in_the_middle(self):
        """the window minimized after two segments, for longer than POUR_QUIET: the pour waits for the link, then goes
        on; the game asked once, as the game went on with it"""
        async def answer(payload):
            loop = asyncio.get_running_loop()

            async def go():
                await self.send([1, 2])
                self.comp.link_up = False
                await asyncio.sleep(1.2)
                self.comp.link_up = True
                await self.send([3, 4, 5, 6])
            loop.create_task(go())
            return dict(ok=True, values=[f"{len(self.segments)} {len(self.rest)}"])
        self.answer = answer
        t0 = asyncio.get_running_loop().time()
        res = await self.svc.run_whole("return BIG")
        self.assertGreater(asyncio.get_running_loop().time() - t0, 1.2)
        self.assertEqual(res["values"], [FIRST + self.rest.decode("utf-8")])
        self.assertEqual(len(self.asked), 1)

    async def test_the_pour_asked_once_the_link_is_back(self):
        """the data call found the link down: asked again once it is back"""
        async def answer(payload):
            if len(self.asked) == 1:
                self.comp.link_up = False
                asyncio.get_running_loop().call_later(0.3, setattr, self.comp, "link_up", True)
                raise ApiError(409, "link_down", "run: no frames come from the game")
            return await self.pour_answer(payload)
        self.answer = answer
        res = await self.svc.run_whole("return BIG")
        self.assertEqual(res["values"], [FIRST + self.rest.decode("utf-8")])
        self.assertEqual(self.asked, [{"k": self.key(), "g": True}] * 2)
        self.assertIn("run: the pour of its answer had no answer (link_down); going on", self.notes())

    async def test_its_own_result_lost(self):
        """the pour's RUN result timed out (it went up, its result did not come): its segments come all the same, and
        say how many there are"""
        async def answer(payload):
            asyncio.get_running_loop().create_task(self.send(self.order))
            raise ApiError(504, "timeout", "no answer to run (job 7) within 10000 ms")
        self.answer = answer
        res = await self.svc.run_whole("return BIG")
        self.assertEqual(res["values"], [FIRST + self.rest.decode("utf-8")])
        self.assertEqual(len(self.asked), 1)

    async def test_what_ends_a_pour(self):
        cases = []

        async def reloaded(payload):                          # a reload: another UI session
            asyncio.get_running_loop().call_later(0.05, setattr, self.comp, "current", 6)
            return dict(ok=True, values=[f"6 {len(self.rest)}"])
        cases.append((reloaded, 502, "lua_error", "no longer kept in the game (the UI reloaded: session 5 -> 6)"))

        async def quiet(payload):                             # nothing comes with the link up
            return dict(ok=True, values=[f"6 {len(self.rest)}"])
        cases.append((quiet, 504, "timeout", "run: no segment of its answer came for 1 s with the link up (0 of 6 in)"))

        async def gone(payload):                              # the game no longer keeps it
            return dict(ok=False, error="the rest of this answer is no longer kept in the game (the UI reloaded?)",
                        stack="")
        cases.append((gone, 502, "lua_error", "no longer kept in the game (the UI reloaded?)"))

        async def odd(payload):
            return dict(ok=True, values=["six"])
        cases.append((odd, 502, "pour_failed", "the game answered the pour with ['six']"))

        async def other_size(payload):
            self.said = (6, len(self.rest) + 1)
            return await self.pour_answer(payload)
        cases.append((other_size, 502, "pour_failed", f"hold {len(self.rest)} bytes, the game said {len(self.rest) + 1}"))
        for answer, status, code, text in cases:
            self.answer, self.comp.current, self.said = answer, 5, None
            self.asked.clear()
            self.codes.clear()
            with self.assertRaises(ApiError) as cm:
                await self.svc.run_whole("return BIG")
            self.assertEqual((cm.exception.status, cm.exception.code), (status, code), answer.__name__)
            self.assertIn(text, cm.exception.message, answer.__name__)
            self.assertEqual(self.svc.pours, {})
        self.assertEqual(self.asked[:1], [{"k": self.key(), "g": True}])
        quiet_asks = [n for n in self.notes() if "asked for again" in n]
        self.assertEqual(len(quiet_asks), 2)                  # quiet: at POUR_ASK and twice that, then given up
        self.assertTrue(all("segments 1, 2, 3, 4, 5, 6 of its answer" in n for n in quiet_asks), quiet_asks)

    async def test_a_data_calls_answer_pours_from_its_store(self):
        """call: the answer the data call keeps under its job (g false)"""
        self.svc.data_calls = lambda: True
        text = '{"result":"' + "长" * 30
        rest = ('长' * 100 + '"}').encode()
        self.segments, self.rest, self.order = [rest[:150], rest[150:]], rest, [2, 1]
        answers = {"call": dict(ok=True, values=[f"1/3\n{text}"])}

        async def data(verb, payload, timeout_ms, what):
            self.asked.append((verb, payload))
            if verb == "pour":
                asyncio.get_running_loop().create_task(self.send([2, 1], key="7"))
                return 7, dict(ok=True, values=[f"2 {len(rest)}"])
            return 7, answers[verb]
        self.svc.data = data
        res = await self.svc.call_exposed("Foo", "long", {"n": 1})
        self.assertEqual(res["result"], "长" * 130)
        self.assertEqual(self.asked, [("call", {"a": "Foo", "n": "long", "d": {"n": 1}}), ("pour", {"k": "7", "g": False})])

    async def test_a_whole_call(self):
        """call with whole: "w" goes with the data call, the rest is poured, `whole: true` comes back; WuxianKit's own
        reads with whole go to the game, not its cache"""
        self.svc.data_calls = lambda: True
        text = '{"result":"' + "长" * 30
        rest = ('长' * 100 + '"}').encode()
        self.segments, self.rest, self.order = [rest[:150], rest[150:]], rest, [2, 1]

        async def data(verb, payload, timeout_ms, what):
            self.asked.append((verb, payload))
            if verb == "pour":
                asyncio.get_running_loop().create_task(self.send([2, 1], key="7"))
                return 7, dict(ok=True, values=[f"2 {len(rest)}"])
            return 7, dict(ok=True, values=[f"1/3\n{text}"])
        self.svc.data = data
        res = await self.svc.call_exposed("Foo", "long", {"n": 1}, whole=True)
        self.assertEqual(res, dict(addon="Foo", name="long", ok=True, result="长" * 130, whole=True))
        self.assertEqual(self.asked, [("call", {"a": "Foo", "n": "long", "d": {"n": 1}, "w": True}),
                                      ("pour", {"k": "7", "g": False})])
        self.asked.clear()

        async def cached(name, ask):
            raise AssertionError("a whole read went to the cache")
        self.svc.kit_cache.call = cached
        res = await self.svc.call_exposed("WuxianKit", "kit.manifest", whole=True, shared=True)
        self.assertEqual(res["result"], "长" * 130)
        self.assertEqual(self.asked[0], ("call", {"a": "WuxianKit", "n": "kit.manifest", "d": None, "q": True,
                                                  "w": True}))

    async def test_a_whole_call_refused(self):
        """a bad whole is a bad request; an older WoWBridge (it ignores "w") refuses past 256 KB, and with whole the
        error says why; past 16 MB the game's error as it is"""
        self.svc.data_calls = lambda: True
        for bad in ("yes", 1, [True]):
            with self.assertRaises(ApiError) as cm:
                await self.svc.call_exposed("Foo", "long", whole=bad)
            self.assertEqual((cm.exception.status, cm.exception.code), (400, "bad_request"))
        self.assertEqual(self.asked, [])
        said = ["the answer is 300 KB, more than the 256 KB a call brings back"]

        async def data(verb, payload, timeout_ms, what):
            self.asked.append((verb, payload))
            return 7, dict(ok=True, values=["1/1\n" + json.dumps({"error": said[0]})])
        self.svc.data = data
        for whole, hint in ((True, True), (False, False)):
            with self.assertRaises(ApiError) as cm:
                await self.svc.call_exposed("Foo", "long", whole=whole)
            self.assertEqual((cm.exception.status, cm.exception.code), (400, "call_failed"))
            self.assertTrue(cm.exception.message.startswith(said[0]), cm.exception.message)
            self.assertEqual("older than 0.9.15" in cm.exception.message, hint)
        said[0] = "the answer is 17000 KB, more than the 16384 KB a call brings back"
        with self.assertRaises(ApiError) as cm:
            await self.svc.call_exposed("Foo", "long", whole=True)
        self.assertEqual(cm.exception.message, said[0])

    async def test_run_whole_refuses_and_passes_errors_on(self):
        with self.assertRaises(ApiError) as cm:
            await self.svc.run_whole("  ")
        self.assertEqual(cm.exception.status, 400)

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            return dict(ok=False, job=3, chunk=chunk, error='[string "=run"]:1: boom', stack="...")
        self.svc.run = run
        res = await self.svc.run_whole("error('boom')")
        self.assertEqual((res["ok"], res["error"]), (False, '[string "=run"]:1: boom'))

        async def short(code, timeout_ms=10000, addon=None, chunk="=run"):
            return dict(ok=True, job=3, chunk=chunk, values=["1/1\n42"], ms=0.1, bytes=None, note=None, cut=None)
        self.svc.run = short
        res = await self.svc.run_whole("return 42")
        self.assertEqual((res["values"], res["whole"], res["cut"], self.asked), (["42"], True, None, []))


class Window(unittest.IsolatedAsyncioTestCase):
    """an addon that does not pour (before 0.9.13): pieces 2..n asked for probes.WINDOW at a time, one more as one comes
    back"""

    def setUp(self):
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.svc.worker.comp = self.comp = SimpleNamespace(link_up=True, current=5, pours=lambda: False)
        self.pieces = [f"第{i}块;" * 20 for i in range(1, 21)]
        self.flight, self.started, self.slid = set(), [], asyncio.Event()

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            m = re.match(r'local KEY, I = "\w+", (\d+)\n', code)
            i = int(m[1]) if m else 1
            self.started.append((i, set(self.flight)))
            self.flight.add(i)
            if i == 12:
                self.slid.set()
            try:
                if i == 3:          # piece 3 comes back only once 12 was asked for (a batch of 8 would wait for it)
                    await asyncio.wait_for(self.slid.wait(), 5)
                else:
                    await asyncio.sleep(0.002 * (i % 4 + 1))
                return dict(ok=True, values=[f"{i}/{len(self.pieces)}\n{self.pieces[i - 1]}"])
            finally:
                self.flight.discard(i)
        self.svc.run = run

    async def test_eight_at_a_time_sliding(self):
        res = await self.svc.run_whole("return BIG")
        self.assertEqual(res["values"], ["".join(self.pieces)])
        self.assertEqual(max(len(f) for _, f in self.started), probes.WINDOW - 1)    # those out when one more started
        self.assertEqual([i for i, _ in self.started], list(range(1, 21)))
        later = dict(self.started)
        self.assertIn(3, later[12])                         # 12 went while 3 was still out: no batch waits for its slowest


class WholeCallPieces(unittest.IsolatedAsyncioTestCase):
    """call with whole from an addon that does not pour: 2 MB in some 1,600 pieces, eight asked for at a time, the game
    keeping the answer from the latest piece asked for (refresh)"""

    async def test_2_mb_piece_by_piece(self):
        svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        svc.worker.comp = SimpleNamespace(link_up=True, current=5, pours=lambda: False)
        svc.data_calls = lambda: True
        value = "无限\t物品 " * (2_100_000 // 15)
        text = json.dumps({"result": value}, ensure_ascii=False)
        pieces = [text[i:i + 1300] for i in range(0, len(text), 1300)]
        asked, refreshed = [], []

        async def data(verb, payload, timeout_ms, what):
            asked.append((verb, payload))
            i = payload["i"] if verb == "piece" else 1
            await asyncio.sleep(0)
            return 7, dict(ok=True, values=[f"{i}/{len(pieces)}\n{pieces[i - 1]}"])
        window = svc.window

        async def spy(n, more, session, refresh, what):
            refreshed.append(refresh)
            return await window(n, more, session, refresh, what)
        svc.data, svc.window = data, spy
        res = await svc.call_exposed("Foo", "big", whole=True, timeout_ms=3000)
        self.assertEqual((res["result"], res["whole"]), (value, True))
        self.assertGreater(len(text.encode()), 2_000_000)
        self.assertEqual(asked[0], ("call", {"a": "Foo", "n": "big", "d": None, "w": True}))
        self.assertEqual(sorted(p["i"] for v, p in asked[1:]), list(range(2, len(pieces) + 1)))
        self.assertEqual({p["k"] for v, p in asked[1:]}, {"7"})
        self.assertEqual(refreshed, [True])


class Status(unittest.TestCase):
    def test_link_pour(self):
        svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.assertFalse(svc.can_pour())
        svc.worker.comp = SimpleNamespace(pours=lambda: True)
        self.assertTrue(svc.can_pour())
        svc.worker.comp = SimpleNamespace()                 # a companion from before 0.9.13
        self.assertFalse(svc.can_pour())


if __name__ == "__main__":
    unittest.main()
