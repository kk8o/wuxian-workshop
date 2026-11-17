"""The rest of a long answer in the Service (daemon/service.py) over stand-ins for the game: from a WoWBridge that pours
(0.9.13), one data call "pour" and its segments joined in order, whatever order they come in; a segment that never
comes asked for again alone; a link down in the middle waited for; another UI session, a quiet link, a game that says
another size, an error in the game: errors; a pour whose own result is lost goes on with its segments. An addon that
does not pour cannot bring a long answer back: an error that says its version. run with whole, call and its `most`
(0.10.0: the data call's "m"), what a failed call adds when the game's WoWBridge is older than the program (by the
version of its HELLO), and status's link.pour."""
import asyncio
import json
import re
import unittest
from types import SimpleNamespace
from unittest import mock

from wuxianworkshop import __version__
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
        self.assertEqual(self.asked, [("call", {"a": "Foo", "n": "long", "d": {"n": 1}, "m": probes.WHOLE_MOST}),
                                      ("pour", {"k": "7", "g": False})])

    async def test_a_call_and_its_most(self):
        """call: "m" goes with the data call (16 MB when the caller names none), the rest is poured, and what comes
        back says nothing of it; WuxianKit's own reads that name a most go to the game, not its cache"""
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
        for most, m in ((None, 16 * 1024 * 1024), (4096, 4096), (16 * 1024 * 1024, 16 * 1024 * 1024), (1, 1)):
            self.asked.clear()
            res = await self.svc.call_exposed("Foo", "long", {"n": 1}, most=most)
            self.assertEqual(res, dict(addon="Foo", name="long", ok=True, result="长" * 130))
            self.assertEqual(self.asked, [("call", {"a": "Foo", "n": "long", "d": {"n": 1}, "m": m}),
                                          ("pour", {"k": "7", "g": False})])
        self.asked.clear()
        cached = []

        async def cache(name, ask):
            cached.append(name)
            return await ask()
        self.svc.kit_cache.call = cache
        res = await self.svc.call_exposed("WuxianKit", "kit.manifest", most=1 << 20, shared=True)
        self.assertEqual((res["result"], cached), ("长" * 130, []))
        self.assertEqual(self.asked[0], ("call", {"a": "WuxianKit", "n": "kit.manifest", "d": None, "q": True,
                                                  "m": 1 << 20}))
        await self.svc.call_exposed("WuxianKit", "kit.manifest", shared=True)
        self.assertEqual(cached, ["kit.manifest"])                # no most named: the views' shared answer

    async def test_a_bad_most_and_a_call_the_game_refuses(self):
        """a bad most is a bad request; an answer over most is the game's error as it is; in a game whose WoWBridge is
        older than the program (the version of its HELLO) a failed call says so, whatever it failed with"""
        self.svc.data_calls = lambda: True
        for bad in ("yes", True, 0, -1, 16 * 1024 * 1024 + 1, 2.5, [4096]):
            with self.assertRaises(ApiError) as cm:
                await self.svc.call_exposed("Foo", "long", most=bad)
            self.assertEqual((cm.exception.status, cm.exception.code), (400, "bad_request"))
            self.assertTrue(cm.exception.message.startswith("most: "), cm.exception.message)
        self.assertEqual(self.asked, [])
        said = ["the answer is 17000000 bytes, more than the 16777216 bytes this call takes (its most)"]

        async def data(verb, payload, timeout_ms, what):
            self.asked.append((verb, payload))
            return 7, dict(ok=True, values=["1/1\n" + json.dumps({"error": said[0]})])
        self.svc.data = data

        async def refused():
            with self.assertRaises(ApiError) as cm:
                await self.svc.call_exposed("Foo", "long")
            self.assertEqual((cm.exception.status, cm.exception.code), (400, "call_failed"))
            return cm.exception.message
        self.assertEqual(await refused(), said[0])                # no HELLO seen: nothing to say
        self.comp.sessions = {5: SimpleNamespace(version_text=__version__)}
        self.assertEqual(await refused(), said[0])                # the program's own WoWBridge
        self.comp.sessions = {5: SimpleNamespace(version_text="99.0.0")}
        self.assertEqual(await refused(), said[0])                # a newer one
        note = (f" (the WoWBridge in the game is 0.9.14, older than this 无限工坊 {__version__}: the new one takes effect "
                f"once the game is restarted)")
        self.comp.sessions = {5: SimpleNamespace(version_text="0.9.14")}
        for text in ("the answer is 300 KB, more than the 256 KB a call brings back",     # what 0.9.14 says past 256 KB
                     "Foo has no long (it exposes nothing)"):
            said[0] = text
            self.assertEqual(await refused(), text + note)
        self.comp.sessions = {5: SimpleNamespace(version_text="0.9.14"), 6: SimpleNamespace(version_text=__version__)}
        self.comp.current = 6                                     # the game restarted: the new one's session
        self.assertEqual(await refused(), said[0])

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


class NoPour(unittest.IsolatedAsyncioTestCase):
    """an addon that does not pour (before 0.9.13): what fits in one piece comes, a longer answer is an error that says
    the version of the WoWBridge in the game (nothing is asked for piece by piece any more)"""

    def setUp(self):
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.svc.worker.comp = self.comp = SimpleNamespace(link_up=True, current=5, pours=lambda: False,
                                                           sessions={5: SimpleNamespace(version_text="0.9.12")})
        self.svc.data_calls = lambda: True
        self.pieces, self.asked = 3, []

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            self.asked.append(chunk)
            return dict(ok=True, values=[f"1/{self.pieces}\n" + '"abc"'])

        async def data(verb, payload, timeout_ms, what):
            self.asked.append(verb)
            return 7, dict(ok=True, values=[f"1/{self.pieces}\n" + '{"result":"abc"}'])
        self.svc.run, self.svc.data = run, data

    async def test_a_long_answer_is_refused_by_the_version(self):
        note = (f"(the WoWBridge in the game is 0.9.12, older than this 无限工坊 {__version__}: the new one takes effect "
                f"once the game is restarted)")
        for what, ask in (("run", lambda: self.svc.run_whole("return BIG")),
                          ("call", lambda: self.svc.call_exposed("Foo", "big")),
                          ("probe", lambda: self.svc.probe("return BIG", 10000))):
            with self.assertRaises(ApiError) as cm:
                await ask()
            self.assertEqual((cm.exception.status, cm.exception.code), (409, "addon_outdated"), what)
            self.assertEqual(cm.exception.message, f"{what}: its answer is longer than one piece, which the WoWBridge in "
                                                   f"the game cannot bring back {note}")
        self.assertEqual(self.asked, ["=run", "call", "=probe"])  # each asked once, and nothing after it
        self.assertEqual(self.svc.pours, {})
        self.comp.sessions = {}                                   # a session seen without its HELLO: no version
        with self.assertRaises(ApiError) as cm:
            await self.svc.call_exposed("Foo", "big")
        self.assertTrue(cm.exception.message.endswith("cannot bring back (it is too old)"), cm.exception.message)

    async def test_one_piece_comes(self):
        self.pieces = 1
        self.assertEqual((await self.svc.run_whole("return 'abc'"))["values"], ['"abc"'])
        self.assertEqual((await self.svc.call_exposed("Foo", "small"))["result"], "abc")
        self.assertEqual(await self.svc.probe("return 'abc'", 10000), "abc")


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
