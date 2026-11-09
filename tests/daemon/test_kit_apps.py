"""The 接入 Agent page's apps (/api/kit/apps) over a real daemon: WuxianKit's kit.apps while the game answers (with
why an app that is off cannot start, never kit.app.status: the agent's heartbeat), the apps of the kept manifest while it does not; each app's
driver (prompt, program), launch and temp, its start line as WuxianKit gives it, its own extension and the skill its
prompt names in its docs folder (with needs and mcp)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame, request
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server as daemon
from wuxianworkshop.daemon.api import ApiError

START = '开始无限工坊的应用「公会聊天」：先调用工具 wk_docs，参数 {"skill": "guild-chat"}，照读到的步骤做。'


def manifest():
    return {"protocol": 1, "kit": "0.6.0", "language": "zhCN", "revision": "r6", "extensions": [
        {"id": "kit", "title": "核心", "addon": "WuxianKit", "docs": "WuxianKit", "builtin": True, "state": "on",
         "capabilities": [{"id": "kit.apps", "kind": "see", "title": "应用", "doc": "", "args": {}}]},
        {"id": "chat", "title": "消息", "addon": "WuxianKit", "docs": "WuxianKit/Extensions/Chat", "state": "on",
         "capabilities": [], "sources": [{"name": "chat", "title": "聊天", "doc": "chat:<channel>"}]},
        {"id": "guide", "title": "指引", "addon": "WuxianKit", "docs": "WuxianKit/Extensions/Guide", "state": "off"}],
        "apps": [{"id": "guild", "title": "公会聊天", "by": "addon", "addon": "WuxianKit", "topic": "app.guild",
                  "hear": ["chat:guild"], "driver": "prompt", "prompt": "guild-chat", "docs": "WuxianKit/Apps/Guild",
                  "start": START, "launch": False, "temp": False},
                 {"id": "tour", "title": "新手引导", "by": "addon", "addon": "WuxianKit", "topic": "app.tour",
                  "hear": [], "driver": "prompt", "prompt": "guide-tour", "extension": "guide"},
                 {"id": "quiz", "title": "问答", "by": "addon", "addon": "WuxianKit", "topic": "app.quiz",
                  "hear": ["chat:guild"], "driver": "prompt", "prompt": "chat-quiz"},
                 {"id": "companion", "title": "无限陪玩", "by": "agent", "topic": "app.companion", "hear": ["game:combat"],
                  "driver": "program", "launch": True, "temp": False},
                 {"id": "raid_talk", "title": "副本解说", "by": "agent", "topic": "app.raid_talk", "hear": [],
                  "temp": True}]}


class Game:
    """WuxianKit in the game as the daemon's Service calls it; away: every call finds the link down"""

    def __init__(self):
        self.manifest, self.away, self.calls = manifest(), False, []
        self.apps = [{"id": "guild", "title": "公会聊天", "by": "addon", "addon": "WuxianKit", "on": True, "present": True,
                      "driver": "prompt", "prompt": "guild-chat", "start": START, "launch": False, "temp": False},
                     {"id": "tour", "title": "新手引导", "by": "addon", "addon": "WuxianKit", "on": False, "present": False,
                      "driver": "prompt", "prompt": "guide-tour", "extension": "guide", "blocked": "「指引」扩展关着",
                      "launch": False, "temp": False},
                     {"id": "quiz", "title": "问答", "by": "addon", "addon": "WuxianKit", "on": False, "present": False,
                      "driver": "prompt", "prompt": "chat-quiz", "launch": False, "temp": False},
                     {"id": "companion", "title": "无限陪玩", "by": "agent", "on": False, "present": False,
                      "driver": "program", "launch": True, "temp": False},
                     {"id": "raid_talk", "title": "副本解说", "by": "agent", "on": True, "present": True,
                      "launch": False, "temp": True}]

    async def call_exposed(self, addon, name, args=None, timeout_ms=10000, shared=None):
        self.calls.append((name, args, shared))
        if self.away:
            raise ApiError(409, "link_down", "no frames come from the game")
        if name == "kit.revision":
            return {"ok": True, "result": {"revision": self.manifest["revision"]}}
        if name == "kit.manifest":
            return {"ok": True, "result": self.manifest}
        if name == "kit.apps":
            return {"ok": True, "result": self.apps}
        raise ApiError(400, "call_failed", f"WuxianKit.{name} is not exposed")

    async def addon_api(self, addon=None, brief=False, shared=None):
        if self.away:
            raise ApiError(409, "link_down", "no frames come from the game")
        return {"addon": "WuxianKit", "exposed": [{"name": n} for n in ("kit.revision", "kit.manifest", "kit.apps")],
                "topics": {}}


class KitAppsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(cls.tmp.name) / "home")})
        cls.env.start()
        cls.addons = Path(cls.tmp.name) / "client" / "Interface" / "AddOns"
        (cls.addons / "WoWBridge").mkdir(parents=True)
        MB.install(cls.addons, pool=16)
        kit = cls.addons / "WuxianKit"
        for sub in ("Apps/Guild/skills", "Extensions/Chat/skills", "Extensions/Guide/skills"):
            (kit / sub).mkdir(parents=True)
        (kit / "WuxianKit.toc").write_text("## Interface: 16001\n## Version: 0.6.0\n", encoding="utf-8")
        (kit / "Apps/Guild/skills/guild-chat.md").write_text(
            "---\nname: guild-chat\ntitle: Guild chat\ndescription: Take part in guild chat\narguments: hours?\n"
            "needs: shell\nmcp: wuxianwow https://wuxianwow.com/mcp\n---\nSteps.\n", encoding="utf-8")
        (kit / "Extensions/Chat/skills/guild-chat.md").write_text(        # the same name: the app's own wins
            "---\nname: guild-chat\ntitle: Not the app's\n---\nOther.\n", encoding="utf-8")
        (kit / "Extensions/Chat/skills/chat-quiz.md").write_text(
            "---\nname: chat-quiz\ndescription: A quiz\n---\nSteps.\n", encoding="utf-8")
        (kit / "Extensions/Guide/skills/guide-tour.md").write_text(
            "---\nname: guide-tour\ndescription: A tour\n---\nSteps.\n", encoding="utf-8")
        cls.handle = daemon.start(worker_factory=lambda on_debug, log: FakeGame(cls.addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + "K")
        cls.game = Game()
        cls.handle.service.call_exposed, cls.handle.service.addon_api = cls.game.call_exposed, cls.game.addon_api

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.game.away, self.game.manifest = False, manifest()
        self.game.calls.clear()

    def apps(self):
        out = request(self.handle, "GET", "/api/kit/apps?refresh=1")
        return out["online"], {a["id"]: a for a in out["apps"]}

    def test_while_the_game_answers(self):
        online, apps = self.apps()
        self.assertTrue(online)
        self.assertEqual(list(apps), ["guild", "tour", "quiz", "companion", "raid_talk"])
        guild = apps["guild"]
        keys = ("title", "by", "addon", "driver", "launch", "temp", "prompt", "start", "on", "blocked")
        self.assertEqual({k: guild[k] for k in keys},
                         dict(title="公会聊天", by="addon", addon="WuxianKit", driver="prompt", launch=False, temp=False,
                              prompt="guild-chat", start=START, on=True, blocked=None))
        self.assertIsNone(guild["extension"])     # an app of its own: chat is only what it hears
        self.assertEqual(guild["skill"], {"name": "guild-chat", "title": "Guild chat", "description": "Take part in guild chat",
                                          "needs": ["shell"], "mcp": [{"name": "wuxianwow", "url": "https://wuxianwow.com/mcp"}]})
        tour = apps["tour"]                       # its extension is off: no skill, why it cannot start from kit.apps
        self.assertEqual((tour["on"], tour["blocked"], tour["start"], tour["skill"]), (False, "「指引」扩展关着", None, None))
        self.assertEqual(tour["extension"], {"id": "guide", "title": "指引", "on": False})
        quiz = apps["quiz"]                       # no docs: the skill wk_docs gives by its name; no extension guessed
        self.assertEqual((quiz["skill"]["name"], quiz["skill"]["description"], quiz["extension"]),
                         ("chat-quiz", "A quiz", None))
        companion = apps["companion"]             # driven by a program the game's 无限工坊 loads
        self.assertEqual((companion["driver"], companion["launch"], companion["temp"], companion["prompt"],
                          companion["skill"]), ("program", True, False, None, None))
        talk = apps["raid_talk"]                  # a temporary app
        self.assertEqual((talk["on"], talk["blocked"], talk["driver"], talk["temp"], talk["prompt"], talk["start"],
                          talk["extension"], talk["skill"]), (True, None, None, True, None, None, None, None))
        reads = [(n, a, s) for n, a, s in self.game.calls if n in ("kit.apps", "kit.app.status")]
        self.assertEqual(reads, [("kit.apps", None, True)])   # the page's own read; no kit.app.status (a heartbeat)

    def test_a_wuxiankit_before_start(self):
        """kit.apps without start (a WuxianKit before it): the kept manifest's start, else None"""
        self.game.apps = [dict(a) for a in self.game.apps]
        for a in self.game.apps:
            a.pop("start", None)
        try:
            online, apps = self.apps()
            self.assertTrue(online)
            self.assertEqual((apps["guild"]["start"], apps["tour"]["start"]), (START, None))
        finally:
            self.game.apps = Game().apps

    def test_while_the_game_is_away(self):
        """the kept manifest's apps: on, blocked and launch not known; driver, temp, the skill and extension as
        before"""
        self.apps()                                                    # the manifest read and kept
        self.game.away = True
        self.game.calls.clear()
        online, apps = self.apps()
        self.assertFalse(online)
        self.assertEqual(list(apps), ["guild", "tour", "quiz", "companion", "raid_talk"])
        self.assertEqual((apps["guild"]["on"], apps["guild"]["blocked"], apps["guild"]["start"]), (None, None, START))
        self.assertEqual(apps["guild"]["skill"]["needs"], ["shell"])
        self.assertEqual((apps["companion"]["driver"], apps["companion"]["launch"], apps["raid_talk"]["temp"]),
                         ("program", None, True))                   # launch: only the game in this UI session knows
        self.assertEqual((apps["tour"]["on"], apps["tour"]["blocked"], apps["tour"]["extension"]["on"]), (None, None, False))
        self.assertFalse([n for n, _, _ in self.game.calls if n in ("kit.apps", "kit.app.status")])   # not asked

    def test_none_known(self):
        """the game away and no manifest kept: no apps"""
        from wuxianworkshop.mcp import kit
        service = self.handle.service
        self.apps()
        self.game.away = True
        view = getattr(service.local_mcp, "kit_tools", None) or service.kit_view
        kit.forget_cached()
        view.use(None)
        try:
            self.assertEqual(request(self.handle, "GET", "/api/kit/apps?refresh=1"), {"online": False, "apps": []})
        finally:
            self.game.away = False
            self.apps()


if __name__ == "__main__":
    unittest.main()
