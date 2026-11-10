"""The fake daemon and where an extension runs (scripts/dev_fake_api.py; contract6): a row carries where, game and
player as Host.row answers them, two pretend extensions are in the game only (tips is on, marks is not in the game yet),
and of one in the game only a load checks its Lua, installs its addons and says what ran (loaded), a check reads its
sources without the player's yes, a stop has nothing to stop; new takes where; wait answers what is left for the player.
The sentences for the player are Host.player_says's, word for word."""
import re
from pathlib import Path

from wuxianworkshop import guide, scaffold
from wuxianworkshop.local.host import GAME_STATES, Host

from tests.ui.test_fake_api import AUTH, F
from tests.ui.test_fake_local import ROW, Local

HOME = F.EXT_HOME
IN_GAME = "it is in the game only: WuxianKit runs it there, the workshop starts no process"


class Rows(Local):
    def test_where_each_runs(self):
        rows = self.rows()
        for row in rows.values():
            self.assertIn(row["where"], ("game", "local", "both", None), row["id"])
            self.assertEqual(row["game"] is None, not row["addons"] or row["where"] is None, row["id"])
            if row["game"]:
                self.assertEqual(set(row["game"]), {"extensions", "state"}, row["id"])
                self.assertIn(row["game"]["state"], GAME_STATES, row["id"])
            self.assertTrue(row["player"] is None or set(row["player"]) == {"action", "text"}, row["id"])
            self.assertEqual(row["state"] == "game", row["where"] == "game" and row["enabled"], row["id"])
        self.assertEqual({eid: rows[eid]["where"] for eid in ("voice", "herald", "greeter", "tips", "marks", "draft")},
                         {"voice": "local", "herald": "both", "greeter": "both", "tips": "game", "marks": "game", "draft": None})
        self.assertEqual(rows["herald"]["game"], {"extensions": ["herald"], "state": "absent"})

    def test_the_two_in_the_game_only(self):
        rows = self.rows()
        tips, marks = rows["tips"], rows["marks"]
        self.assertEqual((tips["state"], tips["command"], tips["approval"], tips["allowed"], tips["pid"], tips["why"]),
                         ("game", None, "ok", True, None, IN_GAME))
        self.assertEqual((tips["game"], tips["player"]), ({"extensions": ["tips"], "state": "on"}, None))
        self.assertEqual([a["state"] for a in tips["addons"]], ["current"])
        self.assertEqual((marks["state"], marks["approval"], marks["game"], marks["player"]),
                         ("game", "ok", {"extensions": ["marks"], "state": "absent"}, None))      # a load is the agent's
        self.assertEqual((tips["capabilities"], tips["error"]), ([], None))

    def test_the_kit_lists_the_one_that_is_on(self):
        kit = self.client.get("/api/agent_access", headers=AUTH).json()["kit"]["extensions"]
        tips = next(e for e in kit if e["id"] == "tips")
        self.assertEqual((tips["addon"], tips["state"], [t["name"] for t in tips["tools"]]),
                         ("WuxianTips", "on", ["wk_tips_show", "wk_tips_list"]))
        manifest = self.client.get("/api/local/manifest", headers=AUTH).json()["extensions"]      # the workshop's own
        self.assertFalse({"tips", "marks"} & {e["id"] for e in manifest})

    def test_what_the_player_has_to_do(self):
        rows = self.rows()
        self.assertEqual(rows["greeter"]["player"], Host.player_says("approve", "迎新"))
        self.assertEqual(rows["facts"]["player"], Host.player_says("approve", rows["facts"]["title"]))
        self.assertEqual(rows["stats"]["player"], Host.player_says("approve", rows["stats"]["title"], changed=True))
        self.assertEqual((rows["voice"]["player"], rows["draft"]["player"]), (None, None))
        self.fake.link_state = "offline"                                 # the game is not there
        rows = self.rows()
        self.assertEqual(rows["tips"]["game"]["state"], "offline")
        self.assertEqual(rows["tips"]["player"], Host.player_says("start_game", "提示", addon="WuxianTips"))
        self.assertEqual(rows["marks"]["game"]["state"], "absent")       # nothing of it is in the game: a load first
        for state, action in (("disabled", "enable_addon"), ("off", "turn_on"), ("restart", "restart_game")):
            self.fake.link_state = "online"
            self.fake.local["tips"]["game_state"] = state
            row = self.rows()["tips"]
            self.assertEqual((row["game"]["state"], row["player"]), (state, Host.player_says(action, "提示", addon="WuxianTips")))
        self.post("set", id="tips", enabled=False)                       # off: nothing is asked of the player
        row = self.rows()["tips"]
        self.assertEqual((row["state"], row["player"]), ("off", None))
        self.assertEqual(self.post("set", id="tips", enabled=True).json()["state"], "game")

    def test_the_words_are_the_daemons(self):
        for action in ("approve", "start_game", "restart_game", "enable_addon", "turn_on"):
            for changed in (False, True):
                self.assertEqual(F.player_says(action, "标记", addon="WuxianMarks", changed=changed),
                                 Host.player_says(action, "标记", addon="WuxianMarks", changed=changed))
        for eid in ("pure", "my_tool", "a_b_c", "x9"):
            self.assertEqual(F.addon_name_of(eid), scaffold.addon_name_of(eid))

    def test_the_folder_is_not_known(self):
        self.fake.addons_known = False
        row = self.rows()["tips"]
        self.assertEqual((row["game"]["state"], row["player"]), ("unknown", None))
        d = self.post("load", id="marks").json()
        self.assertEqual((d["restart"], d["loaded"], [a["state"] for a in d["addons"]]), (False, [], ["unknown"]))
        self.assertIn("not known", d["hint"])


class Load(Local):
    def test_a_new_one_goes_in_and_runs(self):
        r = self.post("load", id="marks")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(set(d), {"row", "check", "addons", "restart", "hint", "loaded"})
        self.assertEqual(d["check"], {"ok": True, "problems": [], "capabilities": []})
        self.assertEqual([(a["folder"], a["state"], a["was"]) for a in d["addons"]], [("WuxianMarks", "installed", "missing")])
        self.assertEqual(d["loaded"], [{"folder": "WuxianMarks", "ok": True, "error": None,
                                        "check": {"ok": True, "errors": [], "warnings": []}}])
        self.assertTrue(d["restart"])
        self.assertIn("its files ran in the running game just now (loaded)", d["hint"])
        row = d["row"]
        self.assertEqual(set(row), ROW)
        self.assertEqual((row["state"], row["pid"], row["game"]["state"]), ("game", None, "restart"))
        self.assertEqual(row["player"], Host.player_says("restart_game", "标记", addon="WuxianMarks"))
        w = self.post("wait", id="marks").json()                         # nobody restarts the game here
        self.assertEqual((set(w), w["was"], w["timeout"]), ({"row", "was", "timeout"}, row["player"], True))
        self.fake.local_restarted()                                      # the game came back
        row = self.rows()["marks"]
        self.assertEqual((row["game"]["state"], row["player"]), ("on", None))
        w = self.post("wait", id="marks").json()
        self.assertEqual((w["was"], w["timeout"], w["row"]["id"]), (None, False, "marks"))

    def test_one_that_is_there_runs_again(self):
        d = self.post("load", id="tips").json()
        self.assertEqual((d["restart"], [x["ok"] for x in d["loaded"]], d["row"]["game"]["state"], d["row"]["player"]),
                         (False, [True], "on", None))
        self.assertNotIn("ran in the running game", d["hint"] or "")

    def test_while_the_game_is_away(self):
        self.fake.link_state = "offline"
        d = self.post("load", id="marks").json()
        self.assertEqual((d["loaded"], d["restart"], d["row"]["game"]["state"]), ([], True, "offline"))
        self.assertEqual(d["row"]["player"]["action"], "start_game")
        self.assertNotIn("ran in the running game", d["hint"])

    def test_a_syntax_error_installs_nothing(self):
        row = self.post("add", path="D:\\Work\\ingame_syntax").json()
        self.assertEqual((row["where"], row["state"], row["approval"], row["command"]), ("game", "game", "ok", None))
        self.assertEqual(row["game"], {"extensions": ["ingame_syntax"], "state": "absent"})
        r = self.post("load", id="ingame_syntax")
        self.assertEqual(r.status_code, 422)
        d = r.json()
        self.assertEqual(d["error"]["code"], "check_failed")
        self.assertEqual(d["error"]["message"], "extension_load ingame_syntax: nothing installed, "
                                                "WuxianIngameSyntax/WuxianIngameSyntax.lua:12: unexpected symbol near '}'")
        self.assertEqual(d["addons"], d["error"]["addons"])              # as ApiError.body: in the error and beside it
        a, = d["addons"]
        self.assertEqual((a["folder"], a["ok"], [f["code"] for f in a["errors"]]), ("WuxianIngameSyntax", False, ["syntax"]))
        self.assertEqual(set(a["errors"][0]), {"file", "line", "code", "message", "hint"})
        self.assertEqual([x["state"] for x in self.rows()["ingame_syntax"]["addons"]], ["missing"])

    def test_one_that_fails_in_the_game(self):
        self.post("add", path="D:\\Work\\ingame_fails")
        d = self.post("load", id="ingame_fails").json()
        self.assertEqual([(x["folder"], x["ok"]) for x in d["loaded"]], [("WuxianIngameFails", False)])
        self.assertIn("attempt to call a nil value", d["loaded"][0]["error"])
        self.assertTrue(d["restart"])
        self.assertNotIn("ran in the running game", d["hint"])

    def test_one_from_a_package_waits(self):
        row = self.post("add", path="C:\\Users\\me\\Downloads\\ingame_pack.zip").json()
        self.assertEqual((row["where"], row["approval"], row["state"]), ("game", "needed", "game"))
        self.assertEqual(row["player"], Host.player_says("approve", row["title"]))
        r = self.post("load", id="ingame_pack")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "needs_approval"))
        self.assertEqual(r.json()["player"], row["player"])
        self.assertEqual(r.json()["error"]["player"], row["player"])
        self.assertEqual(self.post("check", id="ingame_pack").status_code, 200)                 # reading it runs nothing
        self.assertEqual(self.post("approve", id="ingame_pack").json()["approval"], "ok")
        self.assertEqual(self.post("load", id="ingame_pack").status_code, 200)

    def test_off(self):
        self.post("set", id="marks", enabled=False)
        r = self.post("load", id="marks")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "off"))

    def test_the_other_kinds_answer_as_before(self):
        d = self.post("load", id="herald").json()                        # its addons go in, its process starts
        self.assertEqual(set(d), {"row", "check", "addons", "restart", "hint"})                   # no loaded: a process runs it
        self.assertEqual(d["row"]["game"], {"extensions": ["herald"], "state": "on"})
        r = self.post("load", id="facts")                                # the refusal says what the player has to do
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["player"], Host.player_says("approve", self.rows()["facts"]["title"]))


class CheckStopWait(Local):
    def test_a_check_reads_the_lua(self):
        d = self.post("check", id="tips").json()
        self.assertEqual(set(d), {"ok", "problems", "capabilities", "where", "addons"})
        self.assertEqual((d["ok"], d["problems"], d["capabilities"], d["where"]), (True, [], [], "game"))
        a, = d["addons"]
        self.assertEqual(a, {"folder": "WuxianTips", "path": HOME + "\\tips\\addons\\WuxianTips", "ok": True, "errors": [],
                             "warnings": []})
        self.post("add", path="D:\\Work\\ingame_warn")
        d = self.post("check", id="ingame_warn").json()
        self.assertEqual((d["ok"], len(d["addons"][0]["warnings"]), d["addons"][0]["errors"]), (True, 1, []))
        self.assertEqual(d["addons"][0]["path"], "D:\\Work\\ingame_warn\\addons\\WuxianIngameWarn")
        self.post("add", path="D:\\Work\\ingame_syntax")
        d = self.post("check", id="ingame_syntax").json()
        self.assertEqual((d["ok"], d["addons"][0]["ok"]), (False, False))

    def test_nothing_to_stop(self):
        d = self.post("stop", id="tips").json()
        self.assertEqual(set(d), ROW | {"stopped", "note"})
        self.assertEqual((d["stopped"], d["state"]), (False, "game"))
        self.assertIn("no process to stop", d["note"])
        self.assertEqual(set(self.post("stop", id="voice").json()), ROW)

    def test_wait(self):
        w = self.post("wait", id="facts").json()                         # waits for the player's yes
        self.assertEqual((w["was"]["action"], w["timeout"], w["row"]["id"]), ("approve", True, "facts"))
        w = self.post("wait", id="voice").json()
        self.assertEqual((w["was"], w["timeout"]), (None, False))
        self.assertEqual(self.post("wait", id="nope").status_code, 404)
        self.assertEqual(self.client.post("/api/local/wait", json={"id": "voice"}).status_code, 401)


class New(Local):
    def test_in_the_game_only(self):
        r = self.post("new", id="pure", title="纯游戏", where="game")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(set(d), {"row", "files", "guide"})
        row = d["row"]
        self.assertEqual((row["where"], row["state"], row["command"], row["approval"], row["player"]),
                         ("game", "game", None, "ok", None))
        self.assertEqual(row["addons"], [{"folder": "Pure", "version": "0.1.0", "state": "missing"}])
        self.assertEqual(row["game"], {"extensions": ["pure"], "state": "absent"})
        base = HOME + "\\pure"
        self.assertEqual(d["files"], [base + "\\wuxian-extension.json", base + "\\addons\\Pure\\Pure.toc",
                                      base + "\\addons\\Pure\\Pure.lua", base + "\\addons\\Pure\\AGENT.md"])
        self.assertTrue(d["guide"].startswith("Read " + base + "\\addons\\Pure\\AGENT.md and guide "
                                              "{\"topic\": \"game-extension\"}. Edit addons\\Pure\\Pure.lua, then "
                                              "extension_load {\"id\": \"pure\"}"), d["guide"])
        for word in ("wk_pure_", "errors and logs", "extension_wait {\"id\": \"pure\"}"):
            self.assertIn(word, d["guide"])
        loaded = self.post("load", id="pure").json()
        self.assertEqual((loaded["restart"], loaded["row"]["player"]["action"]), (True, "restart_game"))

    def test_the_addon_is_named(self):
        row = self.post("new", id="my_tool", where="game").json()["row"]
        self.assertEqual(([a["folder"] for a in row["addons"]], row["game"]["extensions"]), (["MyTool"], ["my_tool"]))
        row = self.post("new", id="other", where="game", addon="WuxianOther").json()["row"]
        self.assertEqual([a["folder"] for a in row["addons"]], ["WuxianOther"])

    def test_both(self):
        d = self.post("new", id="my_tool", where="both").json()
        row = d["row"]
        self.assertEqual((row["where"], [a["folder"] for a in row["addons"]], row["game"]["extensions"], row["state"]),
                         ("both", ["MyTool"], ["my_tool_game"], "stopped"))
        self.assertTrue(row["command"])
        self.assertEqual(row["approval"], "needed")                      # a process: the player's yes, as before
        self.assertIn(HOME + "\\my_tool\\main.py", d["files"])
        self.assertIn("guide {\"topic\": \"local-extension\"}", d["guide"])
        self.assertIn("Its game half is addons\\MyTool", d["guide"])

    def test_where_is_read_from_the_addon(self):
        self.assertEqual(self.post("new", id="one").json()["row"]["where"], "local")
        self.assertEqual(self.post("new", id="two", addon="WuxianTwo").json()["row"]["where"], "both")
        d = self.post("new", id="three", where="local").json()
        self.assertEqual((d["row"]["where"], d["row"]["addons"], d["row"]["game"]), ("local", [], None))
        self.assertNotIn("game half", d["guide"])

    def test_refusals(self):
        for body, code in ((dict(id="mine", where="local", addon="WuxianMine"), "bad_request"),
                           (dict(id="mine", where="moon"), "bad_request"),
                           (dict(id="mine", where="game", addon="WuxianKit"), "bad_addon"),
                           (dict(id="mine", where="game", addon="no good"), "bad_addon")):
            r = self.post("new", **body)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, code), body)
        self.assertNotIn("mine", self.rows())

    def test_the_guides_name_real_topics(self):
        text = Path(F.__file__).read_text(encoding="utf-8")
        named = set(re.findall(r'guide \{\{\\"topic\\": \\"([a-z-]+)\\"\}\}', text))
        self.assertEqual(named, {"game-extension", "local-extension"})
        self.assertLessEqual(named, set(guide.names()))


class Auto(Local):
    approval = "auto"

    def test_a_package_still_asks(self):
        rows = self.rows()
        self.assertEqual(rows["greeter"]["player"], Host.player_says("approve", "迎新"))
        self.assertEqual({row["id"] for row in rows.values() if row["player"]}, {"greeter"})


if __name__ == "__main__":
    import unittest
    unittest.main()
