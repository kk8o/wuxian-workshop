"""What addons hand the agent through WoWBridge (addon/WoWBridge/API.lua): events (Emit, the EVENT message, the journal,
`events`) and exposed functions (Expose, `call`, `addon_api`), end to end through the real WoWBridge, link and Service
under Lua 5.1; the journal's EVENT entries; `events` filtering and waiting; the MCP tools; the new_addon templates."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tests.support.wowmock import Session, lupa, toc_files
from wuxianworkshop import scaffold
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon.api import ApiError, parse_run
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.transport import link

ADDON_FOO = b"""
FooWB = WoWBridge.Bind("Foo")
FooWB:Expose("double", function(a) return { n = a.n * 2, s = a.s } end, "doubles n")
FooWB:Expose("boom", function() error("broken on purpose") end)
FooWB:Emit("early", { when = "before the link" })
"""


def event(addon, topic, data, x=None):
    return json.dumps(dict(a=addon, t=topic, d=data, **({"x": x} if x else {})), ensure_ascii=False)


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.got = []
        self.s = Session(self)
        self.journal = Journal()

        def on_debug(kind, text):
            self.got.append((kind, text))
            self.journal.add(kind, text)
        self.comp = link.Companion(self.s.addons, clock=self.s.now, log=lambda text: None, on_debug=on_debug)
        self.s.comp = self.comp
        self.svc = Service(lambda **kw: None, journal=self.journal)
        s, comp, got = self.s, self.comp, self.got

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):        # the daemon's run, over this session
            job = comp.code(code.encode("utf-8"), chunk, addon or "-")
            for _ in range(400):
                s.run(0.25)
                text = next((t for k, t in got if k == "RUN" and t.startswith(f"{job} ")), None)
                if text is not None:
                    return Service.run_result(parse_run(text))
            self.fail(f"no RUN result for job {job}")

        async def caught_up(wait=6.0):                   # the daemon's, over this session: every message up to the marker
            at = comp.mark()
            for _ in range(int(wait / 0.25)):
                if at is None or comp.covered(*at):
                    return True
                s.run(0.25)
            return comp.covered(*at)
        async def data(verb, payload, timeout_ms=10000, what="call"):      # the daemon's data call, over this session
            job = comp.data_call(verb, payload)
            for _ in range(400):
                s.run(0.25)
                text = next((t for k, t in got if k == "RUN" and t.startswith(f"{job} ")), None)
                if text is not None:
                    return job, Service.run_result(parse_run(text))
            self.fail(f"no RUN result for data call {job}")
        self.svc.run, self.svc.caught_up, self.svc.data, self.svc.data_calls = run, caught_up, data, comp.data_calls
        self.addCleanup(s.close)
        comp.new_process("P1-100")
        s.lua.execute(ADDON_FOO)                         # loaded with the UI, before the link is up: its event waits
        s.login()
        s.run(8)

    def events(self, **kw):
        return asyncio.run(self.svc.addon_events(**kw))["events"]

    def test_events_reach_the_journal_whatever_the_link_did(self):
        s = self.s
        s.lua.execute(b'FooWB:Emit("hello", { a = 1, b = "\xe4\xb8\xa4 |cffff0000x|r", nested = { ok = true }, list = { 1, 2, 3 } })')
        s.run(6)
        early, hello = self.events(addon="Foo", topic="early")[0], self.events(addon="F*", topic="hel*")[0]
        self.assertEqual(early["data"], {"when": "before the link"})               # it waited, then went
        self.assertEqual(hello["data"], {"a": 1, "b": "两 |cffff0000x|r", "nested": {"ok": True}, "list": [1, 2, 3]})
        self.assertEqual((hello["addon"], hello["topic"], hello["dropped"]), ("Foo", "hello", 0))
        entry = [e for e in self.journal.since(0, 1000, ["EVENT"])[0] if e["topic"] == "hello"][0]
        self.assertTrue(entry["text"].startswith('hello {"a":1,'))                  # the log line: topic and data
        self.assertEqual(self.events(topic="nothing.*"), [])

    def test_a_flood_is_cut_and_the_next_event_says_so(self):
        """events are best effort: an addon sends BURST (20) at once and RATE (10) a second, and at most QUEUE_BYTES
        (6000) of them wait in the link's queue; what is dropped is counted, and the next event that goes says how
        many. Small ones go up several to a frame"""
        s = self.s
        frames = len(s.frames)
        s.lua.execute(b'for i = 1, 60 do FooWB:Emit("flood", { i = i }) end')
        s.run(15)
        s.lua.execute(b'FooWB:Emit("after", {})')
        s.run(6)
        flood = self.events(topic="flood", limit=1000)
        self.assertEqual([e["data"]["i"] for e in flood], list(range(1, 21)))
        self.assertEqual(self.events(topic="after")[0]["dropped"], 40)
        self.assertLess(len([f for f in s.frames[frames:] if f[1] == 0]), 10)        # 21 events in a few frames
        page = asyncio.run(self.svc.addon_events(topic="flood", limit=3))
        self.assertEqual(len(page["events"]), 3)
        rest = asyncio.run(self.svc.addon_events(topic="flood", since=page["next"], limit=1000))["events"]
        self.assertEqual([e["data"]["i"] for e in rest], list(range(4, 21)))         # the next page picks up there
        s.lua.execute(b'for i = 1, 10 do FooWB:Emit("heavy", { i = i, s = string.rep("x", 2500) }) end')
        s.run(10)
        s.lua.execute(b'FooWB:Emit("after heavy", {})')
        s.run(6)
        self.assertEqual([e["data"]["i"] for e in self.events(topic="heavy")], [1, 2, 3])   # 7,500 bytes wait: no more
        self.assertEqual(self.events(topic="after heavy")[0]["dropped"], 7)
        s.lua.execute(b'FooWB:Emit("big", { s = string.rep("x", 9000) })')         # over MAX: said, not sent
        s.run(6)
        self.assertEqual(self.events(topic="big")[0]["data"]["cut"], True)

    def test_call_and_addon_api(self):
        svc = self.svc
        res = asyncio.run(svc.call_exposed("Foo", "double", {"n": 21, "s": "两 ]] |x"}))
        self.assertEqual(res, dict(addon="Foo", name="double", ok=True, result={"n": 42, "s": "两 ]] |x"}))
        with self.assertRaises(ApiError) as cm:
            asyncio.run(svc.call_exposed("Foo", "boom"))
        self.assertEqual(cm.exception.code, "call_failed")
        self.assertIn("broken on purpose", cm.exception.message)
        with self.assertRaises(ApiError) as cm:
            asyncio.run(svc.call_exposed("Foo", "nope"))
        self.assertIn("Foo has no nope (it exposes double, boom)", cm.exception.message)
        api = asyncio.run(svc.addon_api("Foo"))
        self.assertEqual(api["exposed"], [{"name": "double", "doc": "doubles n"}, {"name": "boom"}])
        self.assertEqual(api["topics"], {"early": 1})
        self.assertEqual([a["addon"] for a in asyncio.run(svc.addon_api())["addons"]], ["Foo"])
        self.assertEqual(asyncio.run(svc.addon_api("Bar")), dict(addon="Bar", exposed=[], topics={}, unbound=True))
        calls = [t for k, t in self.got if k == "RUN" and " =call " in t]
        self.assertTrue(calls)                                                      # under its own chunk name
        for bad in (("", "double"), ("Foo", "a b"), ("../x", "double")):
            with self.assertRaises(ApiError):
                asyncio.run(svc.call_exposed(*bad))

    def test_a_request_and_its_answer(self):
        """Request: an event with a request id; respond hands the answer to the callback once; no answer in time is
        "timeout", a link too busy to take the question "dropped" at once"""
        s, svc = self.s, self.svc
        s.lua.execute(b"""
            Answers = {}
            ReqId = FooWB:Request("ask", { q = "where" }, function(reply, err)
                Answers[#Answers + 1] = { reply = reply, err = err }
            end, 30)""")
        s.run(6)
        asked = self.events(topic="ask")[0]
        self.assertEqual(asked["data"], {"q": "where"})
        self.assertEqual((asked["request"], asked["wait"]), (s.lua.globals()[b"ReqId"].decode(), 30))
        self.assertAlmostEqual(asked["expires"], asked["t"] + 30, places=2)
        self.assertEqual(asyncio.run(svc.addon_api("Foo"))["requests"], 1)
        res = asyncio.run(svc.respond(asked["request"], {"text": "两 ]] |x"}))
        self.assertEqual(res, dict(request=asked["request"], ok=True, addon="Foo", topic="ask"))
        answers = s.lua.globals()[b"Answers"]
        self.assertEqual(answers[1][b"reply"][b"text"].decode(), "两 ]] |x")
        self.assertIsNone(answers[1][b"err"])
        with self.assertRaises(ApiError) as cm:                      # answered: no longer waiting
            asyncio.run(svc.respond(asked["request"], {"text": "again"}))
        self.assertEqual(cm.exception.code, "respond_failed")
        self.assertIn("no request", cm.exception.message)
        s.lua.execute(b'FooWB:Request("slow", {}, function(reply, err) Answers[#Answers + 1] = { err = err } end, 2)')
        s.run(4)
        self.assertEqual(answers[2][b"err"], b"timeout")
        s.lua.execute(b'for i = 1, 20 do FooWB:Emit("fill", {}) end '
                      b'FooWB:Request("crowded", {}, function(reply, err) Answers[#Answers + 1] = { err = err } end)')
        s.run(0.1)
        self.assertEqual(answers[3][b"err"], b"dropped")
        self.assertEqual(asyncio.run(svc.addon_api("Foo"))["requests"], 0)
        for bad in ("", "abc", "1.2.3", "5974"):
            with self.assertRaises(ApiError):
                asyncio.run(svc.respond(bad, {}))

    def test_try_lists_the_events_emitted_in_its_window(self):
        res = asyncio.run(self.svc.try_(code='FooWB:Emit("tried", { ok = true }) return 1', seconds=0))
        self.assertEqual([(e["addon"], e["topic"], e["data"]) for e in res["emitted"]], [("Foo", "tried", {"ok": True})])
        self.assertIn("1 event emitted (tried)", res["summary"])

    def test_calls_go_as_data(self):
        """WoWBridge 0.9.7 takes call / respond / addon_api as data (CALL records): no code goes, so they work with hot
        loading off; big arguments go in parts, a long answer comes in pieces, a record read twice runs once"""
        s, svc, comp = self.s, self.svc, self.comp
        self.assertTrue(comp.data_calls())
        code = comp.stats["records"]["CODE"]
        s.lua.execute(b'FooWB:Expose("echo", function(a) return a end) '
                      b'FooWB:Expose("long", function(a) return string.rep(a.s, a.n) end)')
        text = "两 ]] |x \\ \" é \U0001F600 \t\n"
        res = asyncio.run(svc.call_exposed("Foo", "double", {"n": 21, "s": text}))
        self.assertEqual(res["result"], {"n": 42, "s": text})
        big = {"s": "長" * 3000, "list": list(range(50)), "deep": {"a": [True, False, 1.5, -2, 1e300, 2 ** 53]}}
        self.assertEqual(asyncio.run(svc.call_exposed("Foo", "echo", big))["result"], big)    # 9 KB each way
        long = asyncio.run(svc.call_exposed("Foo", "long", {"s": "无限", "n": 3000}))["result"]
        self.assertEqual(long, "无限" * 3000)                                   # 18 KB: five pieces
        self.assertEqual(comp.stats["records"]["CODE"], code)                   # none of it was code
        self.assertGreater(comp.stats["records"]["CALL"], 6)
        s.slash("set hotLoad off")
        self.assertEqual(asyncio.run(svc.call_exposed("Foo", "double", {"n": 2, "s": ""}))["result"], {"n": 4, "s": ""})
        self.assertEqual(asyncio.run(svc.addon_api("Foo"))["exposed"][0], {"name": "double", "doc": "doubles n"})
        ran = asyncio.run(svc.run("return 1"))
        self.assertFalse(ran["ok"])
        self.assertIn("hot loading is off", ran["error"])
        s.slash("set hotLoad on")
        job = comp.data_call("call", {"a": "Foo", "n": "double", "d": {"n": 1}})   # what a lost slot sends again
        record = comp.outbox[-1]
        s.run(6)
        comp.outbox.append(record)
        s.run(6)
        self.assertEqual(len([t for k, t in self.got if k == "RUN" and t.startswith(f"{job} ")]), 1)

    def test_a_data_call_the_game_cannot_read(self):
        """what WoWBridge says of a CALL record it cannot do: not JSON, an unknown verb, a piece no longer kept"""
        s, comp = self.s, self.comp

        def answer(body, verb="call"):
            comp.last_job = job = max(int(comp.clock() * 1000) % 10 ** 10, comp.last_job + 1)   # data_call's ids
            comp.outbox.append((MB.CALL, f"{job} 1/1 {verb}\n".encode() + body))
            s.run(6)
            return next(t for k, t in self.got if k == "RUN" and t.startswith(f"{job} "))
        self.assertIn("error =call: its data is not JSON: no comma or } at byte", answer(b'{"a":"Foo" "n":1}'))
        self.assertIn("error =call: its data is not a JSON object", answer(b'"Foo"'))
        self.assertIn('error =call: no data call "open"', answer(b'{}', "open"))
        self.assertIn("error =piece: the rest of this answer is no longer kept", answer(b'{"k":"1","i":2}', "piece"))

    def test_json_decode(self):
        """WoWBridge's JSON reader (Json.lua): escapes, surrogate pairs, numbers, nesting, and where a text breaks"""
        lua = self.s.lua
        lua.execute(b"function JD(s) local v, err = WoWBridgeNS.WoWBridge.Json.Decode(s) return v, err end")
        jd = lua.globals()[b"JD"]

        def py(v):
            if lupa.lua_type(v) != "table":
                return v.decode() if isinstance(v, bytes) else v
            items = {(k.decode() if isinstance(k, bytes) else k): py(x) for k, x in v.items()}
            if items and sorted(items, key=str) == sorted(range(1, len(items) + 1), key=str):
                return [items[i] for i in range(1, len(items) + 1)]
            return items

        def dec(text):
            v, err = jd(text.encode())
            return py(v), err and err.decode()
        self.assertEqual(dec('{"a": [1, 2.5, -3e2, 1E-2, 0], "b": {"c": true, "d": false}, "e": ""}'),
                         ({"a": [1, 2.5, -300, 0.01, 0], "b": {"c": True, "d": False}, "e": ""}, None))
        self.assertEqual(dec('"\\u4e24\\ud83d\\ude00\\n\\t\\"\\\\\\/\\b\\f\\r"'),
                         ('两\U0001F600\n\t"\\/\b\f\r', None))
        self.assertEqual(dec('"\\ud800 x \\udc00"'), ("\ufffd x \ufffd", None))          # lone halves of a pair
        self.assertEqual(dec("null"), (None, None))
        self.assertEqual(dec(" [ ] "), ({}, None))
        self.assertEqual(dec("[1, null, 3]"), ({1: 1, 3: 3}, None))                      # a hole where the null was
        for bad, why in (('{"a":}', "not a JSON value at byte 6"), ("[1,]", "not a JSON value at byte 4"),
                         ("{a:1}", "an object key that is not a string at byte 2"), ('"abc', "a string with no end"),
                         ("[1] x", "text after the value at byte 5"), ('"\x01"', "a control byte in a string at byte 2"),
                         ('"\\x"', "an unknown escape at byte 2"), ('"\\u12"', "a \\u escape without four hex digits"),
                         ("[" * 40 + "]" * 40, "more than 32 levels"), ("tru", "not a JSON value at byte 1")):
            v, err = dec(bad)
            self.assertIsNone(v, bad)
            self.assertIn(why, err or "", bad)


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class BeforeDataCalls(unittest.TestCase):
    """a WoWBridge before 0.9.7 (link features 1) takes no CALL records: call / respond / addon_api go to it as code"""

    def test_code_for_an_older_wowbridge(self):
        got, journal = [], Journal()
        s = Session(self)
        self.addCleanup(s.close)
        comp = link.Companion(s.addons, clock=s.now, log=lambda text: None,
                              on_debug=lambda kind, text: (got.append((kind, text)), journal.add(kind, text)))
        s.comp = comp
        svc = Service(lambda **kw: None, journal=journal)

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            job = comp.code(code.encode("utf-8"), chunk, addon or "-")
            for _ in range(400):
                s.run(0.25)
                text = next((t for k, t in got if k == "RUN" and t.startswith(f"{job} ")), None)
                if text is not None:
                    return Service.run_result(parse_run(text))
            self.fail(f"no RUN result for job {job}")
        svc.run, svc.data_calls = run, comp.data_calls
        comp.new_process("P1-100")
        s.ns[b"Link"][b"FEATURES"] = 1                               # what 0.9.6 says in its HELLO
        s.lua.execute(ADDON_FOO)
        s.login()
        s.run(8)
        self.assertFalse(comp.data_calls())
        res = asyncio.run(svc.call_exposed("Foo", "double", {"n": 21, "s": "两"}))
        self.assertEqual(res["result"], {"n": 42, "s": "两"})
        self.assertEqual((comp.stats["records"]["CALL"] > 0, comp.stats["records"]["CODE"] > 0), (False, True))
        s.lua.execute(b"WoWBridge.Call = nil")                                 # a WoWBridge from before 0.9.6
        with self.assertRaises(ApiError) as cm:
            asyncio.run(svc.call_exposed("Foo", "double", {"n": 1}))
        self.assertIn("no addon API", cm.exception.message)


class Events(unittest.TestCase):
    """`events` over the journal alone: filters, pages, waiting, refusals"""

    def setUp(self):
        self.journal = Journal()
        self.svc = Service(lambda **kw: None, journal=self.journal)

    def test_the_journal_reads_an_event(self):
        e = self.journal.add("EVENT", event("Foo", "scan.done", {"items": 120, "name": "两"}, x=3))
        self.assertEqual((e["addon"], e["topic"], e["data"], e["dropped"]), ("Foo", "scan.done", {"items": 120, "name": "两"}, 3))
        self.assertEqual(e["text"], 'scan.done {"items":120,"name":"两"} (3 dropped before it)')
        long = self.journal.add("EVENT", event("Foo", "big", {"s": "x" * 500}))
        self.assertTrue(long["text"].endswith("…"))
        self.assertEqual(long["data"], {"s": "x" * 500})                           # the entry keeps it whole
        asked = self.journal.add("EVENT", json.dumps(dict(a="Foo", t="ask", d={"q": 1}, r="12.3", w=60)))
        self.assertEqual((asked["request"], asked["wait"]), ("12.3", 60))
        self.assertEqual(asked["text"], 'ask {"q":1} (request 12.3, answer within 60 s)')
        odd = self.journal.add("EVENT", "not json")
        self.assertEqual((odd["text"], odd.get("topic")), ("not json", None))

    def test_filters_and_waiting(self):
        j, svc = self.journal, self.svc
        j.add("EVENT", event("Foo", "scan.start", {}))
        j.add("INFO", "something else")
        j.add("EVENT", event("Bar", "scan.done", {"n": 1}))
        j.add("EVENT", event("Foo", "scan.done", {"n": 2}))
        got = asyncio.run(svc.addon_events(addon="Foo", topic="scan.*"))
        self.assertEqual([e["topic"] for e in got["events"]], ["scan.start", "scan.done"])
        self.assertEqual(asyncio.run(svc.addon_events(topic="scan.done"))["events"][0]["addon"], "Bar")

        async def later():                               # nothing yet: it waits, and the next one wakes it
            task = asyncio.create_task(svc.addon_events(addon="Foo", since=got["next"], wait=5))
            await asyncio.sleep(0.1)
            j.add("EVENT", event("Bar", "noise", {}))       # not this filter: still waiting
            await asyncio.sleep(0.1)
            self.assertFalse(task.done())
            j.add("EVENT", event("Foo", "late", {"n": 3}))
            return await asyncio.wait_for(task, 2)
        woke = asyncio.run(later())
        self.assertEqual([e["topic"] for e in woke["events"]], ["late"])
        self.assertEqual(asyncio.run(svc.addon_events(since=woke["next"], wait=0.2))["events"], [])   # gave up
        for bad in (dict(since=-2), dict(limit=0), dict(wait=301), dict(addon=" ")):
            with self.assertRaises(ApiError):
                asyncio.run(svc.addon_events(**bad))
        now = asyncio.run(svc.addon_events(since=-1))                    # -1: none of the old ones, the next id
        self.assertEqual((now["events"], now["next"]), ([], j.next_id))


class McpTools(unittest.TestCase):
    def test_the_tools_and_where_they_go(self):
        from wuxianworkshop.mcp.server import build_server

        class Backend:
            def __init__(self):
                self.calls = []

            def __getattr__(self, name):
                async def method(*args):
                    self.calls.append((name, args))
                    return {"ok": True}
                return method

        backend = Backend()
        mcp = build_server(backend)
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
        self.assertTrue(tools["events"].annotations.read_only_hint)
        self.assertTrue(tools["addon_api"].annotations.read_only_hint)
        self.assertFalse(tools["call"].annotations.read_only_hint)
        asyncio.run(mcp.call_tool("call", {"addon": "Foo", "name": "double", "args": {"n": 2}}))
        asyncio.run(mcp.call_tool("events", {"addon": "Foo", "wait": 1}))
        asyncio.run(mcp.call_tool("addon_api", {}))
        asyncio.run(mcp.call_tool("respond", {"request": "12.3", "data": {"text": "x"}}))
        self.assertFalse(tools["respond"].annotations.read_only_hint)
        self.assertEqual(backend.calls, [("addon_api", ("WuxianKit",)),           # no WuxianKit there: no wk_ tools
                                         ("call_exposed", ("Foo", "double", {"n": 2}, 10000)),
                                         ("addon_events", ("Foo", None, 0, 100, 1)), ("addon_api", (None,)),
                                         ("respond", ("12.3", {"text": "x"}, 10000))])


class KitTools(unittest.TestCase):
    """WuxianKit's capabilities as tools of their own: typed from their declarations, a Say / Do one with the proposal
    options, called through call_exposed; read again after CACHE seconds, the last list kept while the game is away"""

    CAPS = [
        {"id": "sense.character", "kind": "see", "title": "See the character", "doc": "the player's character", "args": {}},
        {"id": "tune.cvar.set", "kind": "do", "title": "Change a setting", "doc": "change a setting",
         "args": {"name": "string", "value": "string|number|boolean"}},
        {"id": "chat.send", "kind": "say", "title": "Send a message", "doc": "say something",
         "args": {"channel": "guild|party", "text": "string", "target": "string?"}},
        {"id": "data.put", "kind": "keep", "title": "Keep a record", "doc": "keep", "args": {"key": "string", "value": "any"}},
        {"id": "gate.propose", "kind": "say", "title": "Hand a proposal", "doc": "hand", "args": {"steps": "table"}},
    ]

    def backend(self, caps):
        class Backend:
            def __init__(self):
                self.calls, self.away = [], False

            def __getattr__(self, name):
                async def method(*args):
                    self.calls.append((name, args))
                    if self.away:
                        raise ApiError(409, "link_down", "no frames come from the game")
                    if name == "addon_api":
                        return {"exposed": [{"name": "kit.capabilities"}, {"name": "tune.cvar.set"}]}
                    if name == "call_exposed" and args[1] == "kit.capabilities":
                        return {"ok": True, "result": caps}
                    return {"addon": args[0], "name": args[1], "ok": True, "result": {"id": 7, "state": "pending"}}
                return method
        return Backend()

    def test_tools_from_the_capabilities(self):
        from wuxianworkshop.mcp.server import build_server

        backend = self.backend(self.CAPS)
        mcp = build_server(backend)
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
        self.assertIn("run", tools)
        self.assertEqual(sorted(n for n in tools if n.startswith("wk_")),
                         ["wk_chat_send", "wk_data_put", "wk_gate_propose", "wk_sense_character", "wk_tune_cvar_set"])
        self.assertTrue(tools["wk_sense_character"].annotations.read_only_hint)
        self.assertFalse(tools["wk_tune_cvar_set"].annotations.read_only_hint)
        cvar = tools["wk_tune_cvar_set"].input_schema
        self.assertEqual((cvar["properties"]["value"], cvar["required"], sorted(cvar["properties"])),
                         ({"type": ["string", "number", "boolean"]}, ["name", "value"],
                          ["_after", "_title", "_ttl", "name", "value"]))
        send = tools["wk_chat_send"].input_schema
        self.assertEqual((send["properties"]["channel"], send["required"]),
                         ({"type": "string", "enum": ["guild", "party"]}, ["channel", "text"]))
        self.assertEqual(tools["wk_data_put"].input_schema["properties"], {"key": {"type": "string"}, "value": {}})
        self.assertEqual(tools["wk_gate_propose"].input_schema["properties"]["steps"], {"type": ["object", "array"]})
        self.assertIn("the player confirms it in the game", tools["wk_tune_cvar_set"].description)
        result = asyncio.run(mcp.call_tool("wk_tune_cvar_set", {"name": "x", "value": 1, "_title": "t"}))
        self.assertEqual(result.structured_content["result"], {"id": 7, "state": "pending"})
        self.assertEqual(backend.calls[-1], ("call_exposed", ("WuxianKit", "tune.cvar.set", {"name": "x", "value": 1, "_title": "t"}, 30000)))
        n = len(backend.calls)
        asyncio.run(mcp.list_tools())                                   # within CACHE seconds: not read again
        self.assertEqual(len(backend.calls), n)
        backend.away = True
        tools_again = asyncio.run(mcp.list_tools())                     # still cached
        self.assertEqual(len([t for t in tools_again if t.name.startswith("wk_")]), 5)
        with self.assertRaises(Exception):
            asyncio.run(mcp.call_tool("wk_nope", {}))                   # read again for it, the game away: none

    def test_no_wuxiankit(self):
        from wuxianworkshop.mcp.server import build_server

        class Backend:
            def __getattr__(self, name):
                async def method(*args):
                    return {"exposed": []} if name == "addon_api" else {"ok": True}
                return method
        tools = asyncio.run(build_server(Backend()).list_tools())
        self.assertFalse([t for t in tools if t.name.startswith("wk_")])


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Templates(unittest.TestCase):
    """a new addon talks with the agent when WoWBridge is there, and runs as it is when it is not"""

    def make(self, template):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        addons = Path(tmp.name)
        scaffold.create(addons, "Talker", template=template)
        return addons / "Talker"

    def run_in(self, s, folder):
        load = s.lua.globals()[b"__load"]
        ns = s.lua.table()
        for f in toc_files(folder):
            fn, err = load((folder / f).read_bytes(), f"@Talker/{f}".encode())
            self.assertIsNone(err, f)
            fn(b"Talker", ns)
        return ns

    def test_with_and_without_wowbridge(self):
        for template, name, answer in (("basic", "hello", "Agent"), ("window", "toggle", "shown")):
            folder = self.make(template)
            s = Session(self)
            self.addCleanup(s.close)
            self.run_in(s, folder)
            g = s.lua.globals()
            api = json.loads(g[b"WoWBridge"][b"Describe"](b"Talker"))
            self.assertEqual(api["exposed"][0]["name"], name)
            out = s.lua.execute(f'return WoWBridge.Call("Talker", "{name}", {{ who = "Agent" }})'.encode()).decode()
            self.assertIn(answer, out)
            bare = Session(self, bridge=False)                      # a player without 无限工坊: the same file runs
            self.addCleanup(bare.close)
            ns = self.run_in(bare, folder)
            self.assertIsNone(bare.lua.globals()[b"WoWBridge"])
            ns[b"WB"][b"Emit"](ns[b"WB"], b"x", None)                # the stub takes the calls and does nothing
            ns[b"WB"][b"Expose"](ns[b"WB"], b"x", bare.lua.eval("function() end"))
            ns[b"WB"][b"Request"](ns[b"WB"], b"q", None, bare.lua.eval("function(r, why) StubAnswer = why end"))
            bare.run(0.1)                                             # ... but a question is called back at once
            self.assertEqual(bare.lua.globals()[b"StubAnswer"], b"unavailable")


class LoadOrder(unittest.TestCase):
    """check: an addon that uses WoWBridge names it in its .toc, or it may load before it and find none"""

    def test_the_toc_must_name_wowbridge(self):
        from wuxianworkshop.agent import lint
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "Aaa"
            folder.mkdir()
            (folder / "Aaa.lua").write_text("local addonName, ns = ...\nlocal WB = WoWBridge and WoWBridge.Bind(addonName)\n"
                                            "ns.WB = WB\n", encoding="utf-8")
            (folder / "Aaa.toc").write_text("## Interface: 16001\nAaa.lua\n", encoding="utf-8")
            codes = [w["code"] for w in lint.check(folder)["warnings"]]
            self.assertIn("load-order", codes)
            (folder / "Aaa.toc").write_text("## Interface: 16001\n## OptionalDeps: WoWBridge\nAaa.lua\n", encoding="utf-8")
            self.assertNotIn("load-order", [w["code"] for w in lint.check(folder)["warnings"]])
            made = Path(d) / "made"
            made.mkdir()
            scaffold.create(made, "Talker")                     # the template names it already
            self.assertIn("## OptionalDeps: WoWBridge", (made / "Talker" / "Talker.toc").read_text(encoding="utf-8"))
            self.assertNotIn("load-order", [w["code"] for w in lint.check(made / "Talker")["warnings"]])


if __name__ == "__main__":
    unittest.main()
