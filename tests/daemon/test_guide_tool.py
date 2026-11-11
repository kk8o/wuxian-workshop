"""The guide (guide.py, data/guide/*.md, the MCP tool `guide`): from an idea to a verdict.

- a chapter is a Markdown file with front matter (title, zh, tier, kind, order, summary); the route (index.md) lists
  them all in two tables it does not write by hand; the standard for local extensions is a chapter that stays where
  it was (data/local-extension.md), the same text as extension_guide's;
- what the chapters tell an agent to call is there: every `guide {"topic": ...}` in them and in the program names a
  chapter, every `tool {json}` in them is a tool of the MCP server with those arguments;
- the tool: read-only, one optional argument, a description that names every topic, not_found with the topics;
- the instructions send every idea to it first, and the package carries the files."""
import asyncio
import json
import re
import unittest
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

from tests.daemon.test_mcp_table import HomeService
from wuxianworkshop import guide
from wuxianworkshop.mcp import server

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "wuxianworkshop"
TOPIC = re.compile(r'guide \{+\\?"topic\\?": \\?"([^"\\]+)\\?"\}+')
CALL = re.compile(r"`([a-z][a-z_]*) (\{.*?\})`", re.S)
CHAPTERS = ["probe", "addon", "game-extension", "other-addon", "local-extension", "both", "after", "review"]
CASES = ["can-the-game", "ai-app", "addon-data", "show-and-compute", "enrich-app", "keep"]


class Chapters(unittest.TestCase):
    def test_the_topics(self):
        rows = guide.topics()
        self.assertEqual([r["topic"] for r in rows if r["kind"] == "chapter"], CHAPTERS)
        self.assertEqual([r["topic"] for r in rows if r["kind"] == "case"], CASES)
        self.assertEqual(guide.names(), CHAPTERS + CASES)
        for r in rows:
            for field in guide.FIELDS:
                self.assertTrue(str(r[field]).strip(), (r["topic"], field))
            self.assertNotIn("\n", r["summary"], r["topic"])
            self.assertNotIn("|", r["summary"], r["topic"])              # it is a table's cell
            self.assertRegex(r["topic"], r"^[a-z][a-z-]*$")
        for kind in guide.KINDS:
            orders = [r["order"] for r in rows if r["kind"] == kind]
            self.assertEqual(len(orders), len(set(orders)), kind)

    def test_the_files(self):
        """every file of the folder is a chapter with all of the front matter, or the route"""
        files = sorted(p.stem for p in guide.FOLDER.glob("*.md"))
        self.assertEqual(files, sorted(["index"] + [t for t in CHAPTERS + CASES if t not in guide.OUTSIDE]))
        for name in files:
            meta, body = guide.parse((guide.FOLDER / f"{name}.md").read_text(encoding="utf-8"))
            self.assertTrue(body.startswith("# "), name)
            if name != guide.INDEX:
                self.assertEqual(sorted(meta), sorted(guide.FIELDS), name)
                self.assertIn(meta["kind"], guide.KINDS, name)
                self.assertTrue(meta["order"].isdigit(), name)

    def test_the_route(self):
        route = guide.text()
        self.assertEqual(route, guide.text(""))
        self.assertEqual(route, guide.text("index"))
        self.assertTrue(route.startswith("# "))
        self.assertNotIn("{{", route)
        self.assertNotIn("---\n", route[:10])
        for r in guide.topics():
            self.assertEqual(route.count(f"| `{r['topic']}` {r['zh']} | {r['tier']} | {r['summary']} |"), 1, r["topic"])
        for word in ("能做", "有条件能做", "不能做", "`extension_wait`", "`new_extension`", "`api_search`", "`wk_docs`",
                     "is data", "Never approve for them"):
            self.assertIn(word, route)
        for tier in ("| 0 探一下 |", "| 1 游戏内 |", "| 2 本机 |", "| 3 游戏 · 本机 |"):
            self.assertIn(tier, route)

    def test_a_topic(self):
        for name in guide.names():
            text = guide.text(name)
            self.assertTrue(text.startswith("# "), name)                   # the front matter is not the agent's
            self.assertNotIn("{{", text, name)
        self.assertEqual(guide.text("Game Extension"), guide.text("game-extension"))
        self.assertEqual(guide.text(" game_extension "), guide.text("game-extension"))
        self.assertEqual(guide.text("local-extension"), server.extension_guide_text())
        with self.assertRaises(guide.NoTopic) as none:
            guide.text("marketplace")
        self.assertEqual(str(none.exception), "no guide topic 'marketplace'; the topics: " + ", ".join(CHAPTERS + CASES))
        self.assertEqual((none.exception.topic, none.exception.known), ("marketplace", CHAPTERS + CASES))

    def test_no_folder(self):
        """a build without the files says so and names what is left; it does not raise"""
        from unittest import mock
        with mock.patch.object(guide, "FOLDER", ROOT / "no-such-folder"), mock.patch.object(guide, "OUTSIDE", {}):
            self.assertEqual(guide.text(), guide.MISSING)
            self.assertEqual(guide.text("probe"), guide.MISSING)
        for word in ("data/guide", "api_search", "wk_docs", "extension_guide"):
            self.assertIn(word, guide.MISSING)


class WhatTheySay(unittest.TestCase):
    """the chapters against the program: the topics and the tools they name"""

    @classmethod
    def setUpClass(cls):
        mcp = server.build_server(HomeService())
        cls.tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
        cls.texts = {name: guide.text(name) for name in [None] + guide.names()}
        cls.ours = {name: text for name, text in cls.texts.items() if name not in guide.OUTSIDE}

    def test_topics_named(self):
        known, seen = set(guide.names()), set()
        for name, text in self.texts.items():
            for topic in TOPIC.findall(text):
                self.assertIn(topic, known, f"{name}: guide topic {topic}")
                seen.add(topic)
        for file in sorted(SRC.rglob("*.py")) + sorted((SRC / "ui" / "static").glob("*.js")):
            for topic in TOPIC.findall(file.read_text(encoding="utf-8")):
                self.assertIn(topic, known, f"{file.name}: guide topic {topic}")
                seen.add(topic)
        for topic in ("probe", "game-extension", "local-extension", "other-addon", "both", "after"):
            self.assertIn(topic, seen)

    def test_calls_named(self):
        """`tool {json}` in a chapter: a tool of this server (or WuxianKit's, wk_), the JSON reads, and each argument
        is one of the tool's (the standard for local extensions writes the protocol's shapes that way too: its own
        tests read it)"""
        count = 0
        for name, text in self.ours.items():
            for tool, args in CALL.findall(text):
                where = f"{name}: `{tool} {args}`"
                given = json.loads(" ".join(args.split()))
                if tool.startswith("wk_"):
                    continue
                self.assertIn(tool, self.tools, where)
                for arg in given:
                    self.assertIn(arg, self.tools[tool].input_schema["properties"], where)
                for arg in self.tools[tool].input_schema.get("required", []):
                    self.assertIn(arg, given, where)
                count += 1
        self.assertGreater(count, 30)

    def test_tools_named(self):
        """a word in backticks that looks like one of this server's tools is one"""
        for name, text in self.ours.items():
            for word in set(re.findall(r"`((?:extension|api|reaction|new)_[a-z_]+|local_extensions|reactions)`", text)):
                self.assertIn(word, self.tools, f"{name}: `{word}`")

    def test_player_actions(self):
        """the game-extension chapter's table of what is left for the player is the host's"""
        from wuxianworkshop.local import host
        text = guide.text("game-extension")
        for action in host.PLAYER_ACTIONS:
            self.assertIn(f"`{action}`", text)


class Tool(unittest.TestCase):
    def setUp(self):
        self.mcp = server.build_server(HomeService())
        self.tools = {t.name: t for t in asyncio.run(self.mcp.list_tools())}

    def read(self, **args):
        return asyncio.run(self.mcp.call_tool("guide", args)).content[0].text

    def test_the_tool(self):
        tool = self.tools["guide"]
        self.assertEqual((tool.annotations.read_only_hint, sorted(tool.input_schema["properties"]),
                          tool.input_schema.get("required", [])), (True, ["topic"], []))
        self.assertLess(len(tool.description), 1600)
        flat = " ".join(tool.description.split())
        for topic in guide.names():
            self.assertRegex(flat, rf" {re.escape(topic)}[,.]", topic)
        for word in ("Start here", "No topic: the route", "verdict", "can, can with\nconditions or cannot"):
            self.assertIn(" ".join(word.split()), " ".join(tool.description.split()))
        self.assertIn('`guide` {"topic": "local-extension"}', " ".join(self.tools["extension_guide"].description.split()))
        self.assertIn("check_failed", self.tools["extension_load"].description)

    def test_reading(self):
        self.assertEqual(self.read(), guide.text())
        self.assertEqual(self.read(topic="probe"), guide.text("probe"))
        self.assertEqual(self.read(topic="Local Extension"), server.extension_guide_text())
        with self.assertRaises(ToolError) as none:
            self.read(topic="marketplace")
        self.assertEqual(str(none.exception), "Error executing tool guide: not_found: no guide topic 'marketplace'; "
                         "the topics: " + ", ".join(CHAPTERS + CASES))

    def test_where_ideas_start(self):
        self.assertIn("call `guide` first", server.INSTRUCTIONS)
        self.assertNotIn("extension_guide", server.INSTRUCTIONS)
        self.assertIn('"data/guide/*.md"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
