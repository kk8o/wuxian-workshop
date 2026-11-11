"""An extension in the game only over a real daemon (tests/daemon/test_local_api.py's: a FakeGame link, an
extension_approval as a daemon is started, which asks): new_extension where game, its row (where, game, player), check
(its addons through the lint), load (installed into the FakeGame's AddOns and run in the running game), stop and logs
of one without a process, and /api/local/wait: what only the player can do, waited for."""
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.daemon.test_local_api import KIT_MANIFEST, LocalDaemon
from tests.local.util import make_extension
from wuxianworkshop.daemon import service as service_module

RESTART = {"action": "restart_game", "text": "请重启游戏：新装的插件「LootPeek」重启后才会载入"}


def listed(state="on", **more):
    """WuxianKit's manifest with loot_peek in it, as the game lists it after a restart"""
    return dict(KIT_MANIFEST, extensions=KIT_MANIFEST["extensions"] + [
        dict(id="loot_peek", addon="LootPeek", state=state, version="0.1.0", capabilities=[], **more)])


class GameOnly(LocalDaemon, unittest.TestCase):
    LETTER = "Game"
    APPROVAL = None

    def setUp(self):
        super().setUp()
        step = mock.patch.object(service_module, "WAIT_STEP", 0.05)
        step.start()
        self.addCleanup(step.stop)

    def new(self, **more):
        return self.ok("POST", "/api/local/new", {"id": "loot_peek", "title": "掉落一瞥", "where": "game", **more})

    def wait(self, seconds=0, eid="loot_peek"):
        return self.ok("POST", "/api/local/wait", {"id": eid, "seconds": seconds})

    def test_new(self):
        """made and registered: no process (state game, no command), nobody is asked (allowed, though not approved
        yet), nothing of it in the game so far and nothing for the player to do"""
        res = self.new()
        row = res["row"]
        self.assertEqual(res["files"], ["wuxian-extension.json", "addons/LootPeek/AGENT.md",
                                        "addons/LootPeek/LootPeek.toc", "addons/LootPeek/LootPeek.lua"])
        self.assertEqual((row["id"], row["where"], row["state"], row["command"], row["pid"], row["start"]),
                         ("loot_peek", "game", "game", None, None, "use"))
        self.assertEqual((row["approval"], row["allowed"], row["origin"], row["unmet"]), ("needed", True, "home", []))
        self.assertEqual(row["addons"], [{"folder": "LootPeek", "version": "0.1.0", "state": "missing"}])
        self.assertEqual((row["game"], row["player"]), ({"extensions": ["loot_peek"], "state": "absent"}, None))
        self.assertIn("no process", row["why"])
        folder = Path(row["path"])
        self.assertEqual(folder, self.root / "home" / "extensions" / "loot_peek")
        for said in (str(folder / "addons" / "LootPeek" / "AGENT.md"), 'guide {"topic": "game-extension"}',
                     'extension_load {"id": "loot_peek"}', "wk_loot_peek_", 'extension_wait {"id": "loot_peek"}'):
            self.assertIn(said, res["guide"])
        self.assertEqual(self.wait(), {"row": row, "was": None, "timeout": False})
        self.assertEqual(self.ok("GET", "/api/local")["extensions"], [row])

    def test_new_where(self):
        """where both: as addon alone (a process and a game half); local with an addon, an unknown where and an id
        the game's WuxianKit has are refused and leave nothing"""
        both = self.ok("POST", "/api/local/new", {"id": "pair", "where": "both"})
        self.assertEqual((both["row"]["where"], both["row"]["state"], both["row"]["game"]["extensions"]),
                         ("both", "stopped", ["pair_game"]))
        self.assertIn("addons/Pair/Pair.toc", both["files"])
        self.assertEqual(both["row"]["command"][-1], "main.py")
        for body in ({"id": "odd", "where": "local", "addon": "Odd"}, {"id": "odd", "where": "nowhere"},
                     {"id": "odd", "where": 5}, {"id": "sense", "where": "game"}):
            self.fails("POST", "/api/local/new", body, 400, "bad_request")
        self.assertFalse((self.root / "home" / "extensions" / "odd").exists())
        self.assertEqual([r["id"] for r in self.ok("GET", "/api/local")["extensions"]], ["pair"])

    def test_check(self):
        """its addons through the lint, each with its findings; a broken file makes the whole not ok"""
        folder = Path(self.new()["row"]["path"]) / "addons" / "LootPeek"
        res = self.ok("POST", "/api/local/check", {"id": "loot_peek"})
        self.assertEqual((res["ok"], res["problems"], res["where"]), (True, [], "game"))
        self.assertEqual(res["addons"], [{"folder": "LootPeek", "path": str(folder), "ok": True, "errors": [],
                                         "warnings": []}])
        lua = folder / "LootPeek.lua"
        lua.write_text(lua.read_text(encoding="utf-8") + "\nlocal = = 5\n", encoding="utf-8")
        res = self.ok("POST", "/api/local/check", {"id": "loot_peek"})
        self.assertEqual((res["ok"], res["addons"][0]["ok"]), (False, False))
        self.assertEqual([e["file"] for e in res["addons"][0]["errors"]], ["LootPeek.lua"])
        message = self.fails("POST", "/api/local/load", {"id": "loot_peek"}, 422, "check_failed")
        self.assertIn("extension_load loot_peek: nothing installed, LootPeek/LootPeek.lua:", message)
        self.assertNotIn("more of the kind", message)
        self.assertFalse((self.addons / "LootPeek").exists())                  # nothing reached the game
        toc = folder / "LootPeek.toc"                                          # a second file that does not parse
        toc.write_text(toc.read_text(encoding="utf-8") + "\nMore.lua\n", encoding="utf-8")
        (folder / "More.lua").write_text("local = = 6\n", encoding="utf-8")
        message = self.fails("POST", "/api/local/load", {"id": "loot_peek"}, 422, "check_failed")
        self.assertIn("; 1 more of the kind: extension_check lists them", message)
        self.assertFalse((self.addons / "LootPeek").exists())

    def test_load_stop_and_logs(self):
        """load: approved by the workshop itself, the addon in the game's AddOns and run in the running game; the
        game lists it only after a restart, which is the player's to do. Nothing to stop, no output of its own"""
        self.new()
        res = self.ok("POST", "/api/local/load", {"id": "loot_peek"})
        self.assertEqual(res["addons"], [{"folder": "LootPeek", "state": "installed", "was": "missing",
                                         "version": "0.1.0", "kept": None}])
        self.assertTrue((self.addons / "LootPeek" / "LootPeek.lua").is_file())
        self.assertEqual([(a["folder"], a["ok"], a["error"]) for a in res["loaded"]], [("LootPeek", True, None)])
        self.assertEqual(res["loaded"][0]["check"]["errors"], [])
        self.assertTrue(res["restart"])
        for said in ("LootPeek: new in the game's AddOns", "ran in the running game", "list the tools again"):
            self.assertIn(said, res["hint"])
        row = res["row"]
        self.assertEqual((row["approval"], row["allowed"], row["state"]), ("ok", True, "game"))
        self.assertEqual(row["addons"], [{"folder": "LootPeek", "version": "0.1.0", "state": "current"}])
        self.assertEqual((row["game"], row["player"]), ({"extensions": ["loot_peek"], "state": "restart"}, RESTART))
        again = self.ok("POST", "/api/local/load", {"id": "loot_peek"})           # nothing changed: nothing to install
        self.assertEqual(([a["state"] for a in again["addons"]], again["loaded"][0]["ok"]), (["current"], True))
        stopped = self.ok("POST", "/api/local/stop", {"id": "loot_peek"})
        self.assertEqual((stopped["id"], stopped["state"], stopped["stopped"]), ("loot_peek", "game", False))
        self.assertIn("no process to stop", stopped["note"])
        logs = self.ok("GET", "/api/local/logs?id=loot_peek")
        self.assertEqual((logs["lines"], logs["row"]["id"]), ([], "loot_peek"))
        self.assertIn("logs with kinds OUT and ERR", logs["hint"])

    def test_load_without_the_game(self):
        """the link is down: installed all the same, nothing run (loaded empty), and the player starts the game"""
        self.new()
        with mock.patch.object(self.svc, "link_is_up", return_value=False):
            res = self.ok("POST", "/api/local/load", {"id": "loot_peek"})
        self.assertEqual(([a["state"] for a in res["addons"]], res["loaded"]), (["installed"], []))
        self.assertNotIn("ran in the running game", res["hint"])

    def test_wait(self):
        """seconds: a number from 0; what the player has to do is still there when the time is up (timeout), and the
        answer comes as soon as it is done"""
        self.new()
        for seconds in (-1, "long", True, None, float("nan")):
            self.fails("POST", "/api/local/wait", {"id": "loot_peek", "seconds": seconds}, 400, "bad_request")
        self.fails("POST", "/api/local/wait", {"id": "nobody"}, 404, "not_found")
        self.ok("POST", "/api/local/load", {"id": "loot_peek"})
        t0 = time.monotonic()
        res = self.wait(0.3)
        self.assertEqual((res["was"], res["timeout"], res["row"]["player"]), (RESTART, True, RESTART))
        self.assertGreaterEqual(time.monotonic() - t0, 0.25)
        self.assertEqual(self.wait(0)["timeout"], True)                         # 0: only a look
        t0 = time.monotonic()
        with mock.patch.object(self.svc, "kit_manifest_known",
                               lambda: listed() if time.monotonic() - t0 > 0.3 else KIT_MANIFEST):
            res = self.wait(30)
        self.assertEqual((res["was"], res["timeout"]), (RESTART, False))
        self.assertEqual((res["row"]["player"], res["row"]["game"]), (None, {"extensions": ["loot_peek"], "state": "on"}))
        self.assertLess(time.monotonic() - t0, 10)

    def test_wait_goes_on_to_the_next_thing(self):
        """it answers when what the player had to do changed, with what is next in the row: the game restarted, the
        extension is off in it"""
        self.new()
        self.ok("POST", "/api/local/load", {"id": "loot_peek"})
        t0 = time.monotonic()
        with mock.patch.object(self.svc, "kit_manifest_known",
                               lambda: listed("off") if time.monotonic() - t0 > 0.2 else KIT_MANIFEST):
            res = self.wait(30)
        self.assertEqual((res["was"], res["timeout"], res["row"]["game"]["state"]), (RESTART, False, "off"))
        self.assertEqual(res["row"]["player"], {"action": "turn_on", "text": "请在游戏里输入 /wk，打开扩展「掉落一瞥」"})
        with mock.patch.object(self.svc, "kit_manifest_known", lambda: listed("disabled")):
            self.assertEqual(self.wait()["row"]["player"],
                             {"action": "enable_addon", "text": "请在游戏的插件列表里启用「LootPeek」，然后重载界面"})

    def test_wait_for_the_game(self):
        """the game went away while the player was to restart it: still the same wait; it started again with the
        extension in it: done"""
        self.new()
        self.ok("POST", "/api/local/load", {"id": "loot_peek"})
        with mock.patch.object(self.svc, "link_is_up", return_value=False):
            row = self.wait()["row"]
            self.assertEqual((row["game"]["state"], row["player"]["action"]), ("offline", "start_game"))
            t0 = time.monotonic()
            with mock.patch.object(self.svc, "extension_waiting", wraps=self.svc.extension_waiting) as looked:
                res = self.wait(0.3)
            self.assertEqual((res["was"]["action"], res["timeout"]), ("start_game", True))
            self.assertGreaterEqual(looked.call_count, 3)                      # it looked again and again

    def test_wait_for_approval(self):
        """one with a process that the agent added: the player approves it in the App, and the wait is over"""
        folder = make_extension(self.root / "ext" / "fx", "fx")
        row = self.ok("POST", "/api/local/add", {"path": str(folder)})
        self.assertEqual((row["where"], row["game"], row["allowed"]), ("local", None, False))
        approve = {"action": "approve", "text": "请在无限工坊 App 的「扩展」页批准「测试 fx」"}
        self.assertEqual(row["player"], approve)
        message = self.fails("POST", "/api/local/load", {"id": "fx"}, 403, "needs_approval")
        self.assertIn("extension_wait", message)
        self.assertEqual(self.wait(0.1, "fx")["timeout"], True)
        t0 = time.monotonic()
        waited = self.svc.extension_waiting

        async def then_approved(eid, **kw):
            if time.monotonic() - t0 > 0.2 and self.svc.local.rows()[0]["approval"] != "ok":
                await self.svc.extension_approve("fx")
            return await waited(eid, **kw)

        with mock.patch.object(self.svc, "extension_waiting", then_approved):
            res = self.wait(30, "fx")
        self.assertEqual((res["was"], res["timeout"], res["row"]["player"], res["row"]["approval"]),
                         (approve, False, None, "ok"))


if __name__ == "__main__":
    unittest.main()
