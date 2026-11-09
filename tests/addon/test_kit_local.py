"""The local extensions in KitTools (mcp/kit.py), by the layers contract's third part: backend.local_manifest() gives
extension entries of the kit's shape (runtime "local", docs and skills as absolute paths); their capabilities are wk_
tools too, called through backend.local_call; wk_docs lists them and reads their AGENT.md, tools, sources and options;
their skills folders give skills (an app's first, then a local extension's, then the game's extensions'); the apps they
bring are in the overview's apps. They work without the game and without WuxianKit; a backend without local_manifest has
none; the daemon's local.changed with another revision reads them again and tells the clients."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.server.mcpserver.exceptions import ToolError

from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.mcp import kit


def kit_manifest(apps=None):
    m = {"protocol": 1, "kit": "0.6.0", "language": "zhCN", "revision": "k1", "extensions": [
        {"id": "kit", "title": "Core", "addon": "WuxianKit", "docs": "WuxianKit", "state": "on",
         "capabilities": [{"id": "kit.revision", "kind": "see", "title": "Revision", "doc": "", "args": {}},
                          {"id": "kit.app.open", "kind": "keep", "title": "Open app", "doc": "open", "args": {}}]},
        {"id": "sense", "title": "Sense", "addon": "WuxianKit", "docs": "WuxianKit/Extensions/Sense", "state": "on",
         "capabilities": [{"id": "sense.bags", "kind": "see", "title": "Bags", "doc": "the bags", "args": {}}]},
    ]}
    if apps is not None:
        m["apps"] = apps
    return m


class Backend:
    """the daemon as KitTools reads it: the game's WuxianKit (manifest None: none, away: the game away) and the local
    extensions (local: local_manifest's answer)"""

    def __init__(self, manifest=None, local=None, addons=None):
        self.manifest, self.local, self.addons, self.away = manifest, local, addons, False
        self.calls, self.local_calls, self.events, self.local_fail = [], [], [], None

    async def status(self):
        return {"addons_dir": str(self.addons) if self.addons else None}

    async def call_exposed(self, addon, name, args=None, timeout_ms=10000):
        self.calls.append((name, args))
        if self.away:
            raise ApiError(409, "link_down", "no frames come from the game")
        if name == "kit.revision":
            if self.manifest is None:
                raise ApiError(400, "call_failed", "WuxianKit.kit.revision is not exposed")
            return {"ok": True, "result": {"revision": self.manifest["revision"]}}
        if name == "kit.manifest":
            return {"ok": True, "result": self.manifest}
        if name == "kit.capabilities":
            raise ApiError(400, "call_failed", "WuxianKit.kit.capabilities is not exposed")
        return {"ok": True, "result": {"name": name}}

    async def addon_events(self, addon=None, topic=None, since=0, limit=100, wait=0):
        if addon == "local" and self.events and since != -1:
            out, self.events = self.events, []
            return {"events": out, "next": since + len(out)}
        return {"events": [], "next": 5}

    async def local_manifest(self):
        if self.local_fail:
            raise self.local_fail
        return self.local

    async def local_call(self, cap, args=None, timeout_ms=30000):
        self.local_calls.append((cap, args, timeout_ms))
        if cap == "voice.stop":
            raise ApiError(400, "failed", "nothing is playing")
        if cap == "voice.gone":
            raise ApiError(404, "no_capability", "no capability voice.gone")
        return {"result": {"seconds": 1.5, "cap": cap}}


class LocalExtensions(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": self.home.name})
        self.env.start()
        self.root = Path(self.home.name)

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()

    def voice(self, revision="l1", **more):
        """the local manifest with the extension voice (folder in WUXIAN_HOME: AGENT.md, skills/)"""
        folder = self.root / "extensions" / "voice"
        (folder / "skills").mkdir(parents=True, exist_ok=True)
        (folder / "AGENT.md").write_text("# Voice\n\nSpeaks for the player. Use voice.say, then sense.bags.\n",
                                         encoding="utf-8")
        (folder / "skills" / "companion.md").write_text(
            "---\nname: companion\ndescription: Talk while playing\nneeds: shell\n---\nCall wk_voice_status first.\n",
            encoding="utf-8")
        entry = {"id": "voice", "title": "声音", "version": "0.1.0", "notes": "TTS", "state": "on", "runtime": "local",
                 "path": str(folder), "docs": str(folder / "AGENT.md"), "skills": str(folder / "skills"),
                 "capabilities": [
                     {"id": "voice.say", "kind": "point", "title": "说一句", "doc": "speak a line now; see voice.status",
                      "args": {"text": "string", "mood": "string?", "interrupt": "boolean?"}, "timeout": 60},
                     {"id": "voice.status", "kind": "see", "title": "状态", "doc": "the model, the queue", "args": {}},
                     {"id": "voice.prepare", "kind": "keep", "title": "准备", "doc": "make a pool",
                      "args": {"pool": "string", "lines": "table"}},
                     {"id": "voice.stop", "kind": "point", "title": "停", "doc": "stop", "args": {}},
                     {"id": "voice.gone", "kind": "see", "title": "Gone", "doc": "", "args": {}},
                     {"id": "voice.shout", "kind": "say", "title": "Shout", "doc": "a step two kind", "args": {}},
                     {"id": "other.thing", "kind": "see", "title": "Not its own", "doc": "", "args": {}}],
                 "sources": [{"name": "said", "title": "说完一句", "doc": "{text, pool?, seconds}"}],
                 "options": [{"name": "volume", "kind": "number", "title": "音量", "default": 80, "min": 0, "max": 100,
                              "unit": "%", "value": 65},
                             {"name": "style", "kind": "choice", "title": "风格", "default": "calm",
                              "choices": [{"value": "calm", "label": "平静"}, {"value": "lively", "label": "活泼"}]}],
                 "apps": [{"app": "companion", "title": "无限陪玩", "prompt": "companion", "hear": ["game:combat"],
                           "see": ["me"]}]}
        entry.update(more)
        return {"revision": revision, "extensions": [entry]}

    def tools(self, backend, **kw):
        return kit.KitTools(backend, store=False, **kw)

    def names(self, tools):
        """the tool names once a read the list started (in the background, when something was known) is done"""
        async def go():
            await tools.current(wait=True)
            return [t.name for t in await tools.list()]
        return asyncio.run(go())

    def test_capabilities_are_tools(self):
        """each capability of a kind a local extension may have, with an id of its own, is a wk_ tool: see read-only,
        the others writing; its description says where it runs; say / do and another's id are left out"""
        tools = self.tools(Backend(kit_manifest(), self.voice()))
        listed = {t.name: t for t in asyncio.run(tools.list())}
        local = sorted(n for n in listed if n.startswith("wk_voice_"))
        self.assertEqual(local, ["wk_voice_gone", "wk_voice_prepare", "wk_voice_say", "wk_voice_status", "wk_voice_stop"])
        self.assertIn("wk_sense_bags", listed)
        self.assertNotIn("wk_other_thing", listed)
        self.assertTrue(listed["wk_voice_status"].annotations.read_only_hint)
        self.assertFalse(listed["wk_voice_say"].annotations.read_only_hint)
        self.assertFalse(listed["wk_voice_prepare"].annotations.read_only_hint)
        say = listed["wk_voice_say"]
        self.assertEqual(say.title, "说一句")
        self.assertTrue(say.description.startswith("Guide: shows or plays something for the player at once"))
        self.assertIn("Speak a line now; see wk_voice_status. Runs on this computer and works without the game.",
                      say.description)
        self.assertTrue(say.description.endswith("[local extension voice.say; on this computer: 声音 · 说一句]"))
        self.assertEqual(say.input_schema, {"type": "object", "additionalProperties": False, "required": ["text"],
                                            "properties": {"text": {"type": "string"}, "mood": {"type": "string"},
                                                           "interrupt": {"type": "boolean"}}})
        self.assertTrue(listed["wk_voice_prepare"].description.startswith("Store: saves or deletes data on this computer"))
        self.assertEqual(tools.tools[-3:], kit.META)                    # the game's part as before (the pages count it)
        self.assertNotIn("wk_voice_say", [t.name for t in tools.tools])

    def test_calls_go_to_local_call(self):
        """a local tool's call: backend.local_call with the capability's own timeout (else 30 s); its answer as a game
        call's; a refusal as code: message; one that is gone reads the extensions again"""
        backend = Backend(kit_manifest(), self.voice())
        tools = self.tools(backend)
        asyncio.run(tools.list())
        out = asyncio.run(tools.call("wk_voice_say", {"text": "你好"}))
        self.assertEqual(out.structured_content, {"result": {"seconds": 1.5, "cap": "voice.say"}})
        asyncio.run(tools.call("wk_voice_status", {}))
        self.assertEqual(backend.local_calls, [("voice.say", {"text": "你好"}, 60000), ("voice.status", {}, 30000)])
        self.assertEqual(backend.calls, [("kit.revision", None), ("kit.manifest", None)])    # not the game
        with self.assertRaises(ToolError) as refused:
            asyncio.run(tools.call("wk_voice_stop", {}))
        self.assertEqual(str(refused.exception), "failed: nothing is playing")
        backend.local = self.voice("l2")
        backend.local["extensions"][0]["capabilities"] = backend.local["extensions"][0]["capabilities"][:2]
        with self.assertRaisesRegex(ToolError, "not on this computer now.*List the tools again"):
            asyncio.run(tools.call("wk_voice_gone", {}))
        self.assertNotIn("wk_voice_gone", self.names(tools))

    def test_without_the_game_or_wuxiankit(self):
        """no WuxianKit (or the game away): the local tools are listed and called, with wk_docs; its overview says so
        and lists them"""
        backend = Backend(None, self.voice())
        tools = self.tools(backend)
        self.assertEqual(sorted(self.names(tools)), ["wk_docs", "wk_voice_gone", "wk_voice_prepare", "wk_voice_say",
                                                     "wk_voice_status", "wk_voice_stop"])
        self.assertEqual(asyncio.run(tools.call("wk_voice_status", {})).structured_content["result"]["cap"],
                         "voice.status")
        overview = asyncio.run(tools.call("wk_docs", {})).content[0].text
        self.assertTrue(overview.startswith("# WuxianKit\n\nNo WuxianKit in the game right now"))
        self.assertIn("## Local extensions", overview)
        self.assertIn('- `voice` (声音, 5 tools): before using them, call wk_docs with {"extension": "voice"}', overview)
        self.assertIn("- `companion` (local voice): Talk while playing (needs shell)", overview)
        away = Backend(kit_manifest(), self.voice())
        away.away = True
        self.assertIn("wk_voice_say", self.names(self.tools(away)))
        self.assertEqual(self.names(self.tools(Backend(None, None))), [])          # neither: nothing
        self.assertTrue(asyncio.run(self.tools(Backend(None, None)).overview()).startswith("No WuxianKit"))

    def test_a_backend_without_them(self):
        """no local_manifest (an older daemon over HTTP answers 404; a stand-in has none): none, no error; the daemon
        away: the last ones stand"""
        backend = Backend(kit_manifest(), self.voice())
        backend.local_fail = ApiError(404, "not_found", "no such endpoint")
        tools = self.tools(backend)
        self.assertNotIn("wk_voice_say", self.names(tools))
        self.assertIn("wk_sense_bags", self.names(tools))
        backend.local_fail, tools.local_stale = None, True
        self.assertIn("wk_voice_say", self.names(tools))
        backend.local_fail, tools.local_stale = ConnectionError("daemon gone"), True
        self.assertIn("wk_voice_say", self.names(tools))
        self.assertTrue(tools.local_away)
        bare = Backend(kit_manifest())
        bare.local_manifest = None                                                  # as a stand-in without it
        self.assertFalse(self.tools(bare).has_local())
        odd = Backend(kit_manifest(), {"revision": "x", "extensions": "nope"})
        self.assertNotIn("wk_voice_say", self.names(self.tools(odd)))

    def test_its_docs(self):
        """wk_docs {"extension": "voice"}: its AGENT.md (the absolute path the entry gives), where it is, its tools,
        sources, options (their values now), apps and skills; one that never ran says how its tools come"""
        tools = self.tools(Backend(kit_manifest(), self.voice()))
        doc = asyncio.run(tools.call("wk_docs", {"extension": "voice"})).content[0].text
        self.assertTrue(doc.startswith("# Voice\n\nLocal extension `voice` 0.1.0 (tools wk_voice_*), on this computer in "))
        self.assertIn("the player sees it as 声音. 无限工坊 runs it outside the game, so its tools work without it.", doc)
        self.assertIn("Speaks for the player. Use voice.say, then sense.bags.", doc)    # AGENT.md as it is written
        self.assertIn("| `wk_voice_say` | Guide | 说一句: speak a line now; see wk_voice_status | interrupt: boolean?, "
                      "mood: string?, text: string |", doc)
        self.assertNotIn("wk_voice_shout", doc)
        self.assertIn("| `voice:said` | 说完一句: {text, pool?, seconds} |", doc)
        self.assertIn("| `volume` | number (min 0, max 100) | 65 % | 音量 |", doc)
        self.assertIn('| `style` | choice (calm / lively) | "calm" | 风格 |', doc)
        self.assertIn("- `companion` (无限陪玩): start it with its skill `companion`", doc)
        self.assertIn("## Skills\n\n- `companion`: Talk while playing", doc)
        resources = sorted(str(r.uri) for r in asyncio.run(tools.resources()))
        self.assertIn("wuxian://kit/voice", resources)
        self.assertIn("## Tools", asyncio.run(tools.read("wuxian://kit/voice"))[0].content)
        fresh = self.voice(capabilities=[], docs=None)
        tools = self.tools(Backend(None, fresh))
        doc = asyncio.run(tools.extension_doc("voice"))
        self.assertTrue(doc.startswith("# 声音\n\nLocal extension `voice`"))
        self.assertIn("It has no AGENT.md", doc)
        self.assertIn('Its tools are not known yet: it has not run since it was added or changed. Load it with '
                      'extension_load {"id": "voice"}', doc)
        overview = asyncio.run(tools.overview())
        self.assertIn('- `voice` (声音, its tools come once it has run: extension_load {"id": "voice"}): before', overview)
        self.assertEqual(self.names(tools), ["wk_docs"])
        with self.assertRaisesRegex(ToolError, "there: voice"):
            asyncio.run(tools.extension_doc("nope"))

    def test_skills_and_apps(self):
        """a local extension's skills: after the game's apps', before the game's extensions' (a name taken gets the
        owner's id in front); a prompt says it is a local extension's. The apps it brings are in the overview's apps,
        once, even when WuxianKit has them too (the workshop declared them)"""
        addons = self.root / "AddOns"
        for sub in ("WuxianKit/Apps/Talk/skills", "WuxianKit/Extensions/Sense/skills"):
            (addons / sub).mkdir(parents=True)
        (addons / "WuxianKit/Apps/Talk/skills/companion.md").write_text(
            "---\nname: companion\ndescription: The app's own\n---\nApp.\n", encoding="utf-8")
        (addons / "WuxianKit/Extensions/Sense/skills/companion.md").write_text(
            "---\nname: companion\ndescription: The game extension's\n---\nSense.\n", encoding="utf-8")
        apps = [{"id": "talk", "title": "Talk", "by": "addon", "addon": "WuxianKit", "prompt": "companion",
                 "docs": "WuxianKit/Apps/Talk"},
                {"id": "companion", "title": "无限陪玩", "by": "agent", "prompt": "companion", "hear": ["game:combat"]}]
        tools = self.tools(Backend(kit_manifest(apps), self.voice(), addons=addons))
        skills = asyncio.run(tools.skills())
        self.assertEqual([(s["prompt"], kit.owner(s)) for s in skills],
                         [("companion", "app talk"), ("voice-companion", "local voice"), ("sense-companion", "sense")])
        prompts = {p.name: p for p in asyncio.run(tools.prompts())}
        self.assertEqual(prompts["voice-companion"].description, "Talk while playing (无限工坊 · local voice)")
        text = asyncio.run(tools.prompt("voice-companion", {})).messages[0].content.text
        self.assertTrue(text.startswith("(A skill of the local extension 声音 (voice), on this computer. It describes"))
        self.assertTrue(text.endswith("Call wk_voice_status first."))
        overview = asyncio.run(tools.overview())
        self.assertIn("- `voice-companion` (local voice): Talk while playing (needs shell)", overview)
        self.assertEqual(overview.count("- `companion` (无限陪玩"), 1)
        self.assertIn("- `companion` (无限陪玩, from the local extension voice, topic `app.companion`, hears game:combat): "
                      "start it with its skill `companion`", overview)
        alone = self.tools(Backend(kit_manifest(), self.voice()))                   # WuxianKit without it: still there
        self.assertIn("- `companion` (无限陪玩, from the local extension voice, topic `app.companion`, hears "
                      "game:combat, sees me): start it with its skill `companion`", asyncio.run(alone.overview()))

    def test_a_local_tool_wins_a_name(self):
        """a name both have: the local extension's, as the daemon's local_call routes it"""
        m = kit_manifest()
        m["extensions"].append({"id": "voice", "title": "Game voice", "addon": "WxVoice", "docs": "WxVoice", "state": "on",
                                "capabilities": [{"id": "voice.say", "kind": "see", "title": "x", "doc": "", "args": {}}]})
        backend = Backend(m, self.voice())
        tools = self.tools(backend)
        listed = [t for t in asyncio.run(tools.list()) if t.name == "wk_voice_say"]
        self.assertEqual(len(listed), 1)
        self.assertIn("local extension voice.say", listed[0].description)
        asyncio.run(tools.call("wk_voice_say", {"text": "x"}))
        self.assertEqual(backend.local_calls[-1][0], "voice.say")

    def test_local_changed_reads_them_again(self):
        """the watch on the daemon's local.* events: local.changed with another revision reads the extensions again and
        the clients hear that the lists changed; the same revision changes nothing"""
        backend = Backend(kit_manifest(), self.voice("l1"))
        told = []

        async def notify():
            told.append(1)
        tools = self.tools(backend, notify=notify, wait=0)
        reads = []
        real = backend.local_manifest

        async def counted():
            reads.append(1)
            return await real()
        backend.local_manifest = counted

        async def go():
            await tools.current(wait=True)
            told.clear()
            reads.clear()
            self.assertFalse(tools.local_heard([{"topic": "local.changed", "data": {"revision": "l1"}}]))
            backend.events = [{"topic": "local.changed", "data": {"revision": "l1"}}]
            with mock.patch.object(kit, "POLL", 0.01):
                await asyncio.sleep(0.1)
                same = (len(reads), list(told))
                backend.local = self.voice("l2", capabilities=[])
                backend.events = [{"topic": "local.changed", "data": {"revision": "l2"}}]
                for _ in range(100):
                    if told:
                        break
                    await asyncio.sleep(0.02)
            tools.watching.cancel()
            tools.local_watching.cancel()
            return same
        same = asyncio.run(go())
        self.assertEqual(same, (0, []))
        self.assertEqual(told, [1])
        self.assertEqual([t.name for t in tools.listing() if t.name.startswith("wk_voice")], [])
        self.assertEqual(tools.local["revision"], "l2")


if __name__ == "__main__":
    unittest.main()
