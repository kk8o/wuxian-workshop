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
        self.svc.run = run
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
        s = self.s
        s.lua.execute(b'for i = 1, 60 do FooWB:Emit("flood", { i = i }) end')  # 40 at once (BURST), 20 a second after
        s.run(30)
        s.lua.execute(b'FooWB:Emit("after", {})')
        s.run(6)
        flood = self.events(topic="flood", limit=1000)
        self.assertEqual(len(flood), 40)
        self.assertEqual(self.events(topic="after")[0]["dropped"], 20)
        page = asyncio.run(self.svc.addon_events(topic="flood", limit=10))
        self.assertEqual(len(page["events"]), 10)
        rest = asyncio.run(self.svc.addon_events(topic="flood", since=page["next"], limit=1000))["events"]
        self.assertEqual(len(rest), 30)                                             # the next page picks up there

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
        self.s.lua.execute(b"WoWBridge.Call = nil")                                 # a WoWBridge from before 0.9.6
        with self.assertRaises(ApiError) as cm:
            asyncio.run(svc.call_exposed("Foo", "double", {"n": 1}))
        self.assertIn("no addon API", cm.exception.message)
        for bad in (("", "double"), ("Foo", "a b"), ("../x", "double")):
            with self.assertRaises(ApiError):
                asyncio.run(svc.call_exposed(*bad))

    def test_try_lists_the_events_emitted_in_its_window(self):
        res = asyncio.run(self.svc.try_(code='FooWB:Emit("tried", { ok = true }) return 1', seconds=0))
        self.assertEqual([(e["addon"], e["topic"], e["data"]) for e in res["emitted"]], [("Foo", "tried", {"ok": True})])
        self.assertIn("1 event emitted (tried)", res["summary"])


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
        for bad in (dict(since=-1), dict(limit=0), dict(wait=301), dict(addon=" ")):
            with self.assertRaises(ApiError):
                asyncio.run(svc.addon_events(**bad))


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
        self.assertEqual(backend.calls, [("call_exposed", ("Foo", "double", {"n": 2}, 10000)),
                                         ("addon_events", ("Foo", None, 0, 100, 1)), ("addon_api", (None,))])


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


if __name__ == "__main__":
    unittest.main()
