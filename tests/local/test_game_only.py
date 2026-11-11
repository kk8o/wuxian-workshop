"""An extension in the game only (addons and no run) through the manifest, the host and the check: where it is, the id
it shares with its addon, a row's state "game", its game half as the game lists it and what is left to the player,
who approves it, what loading, checking, stopping and removing do without a process. The game is a folder of the
test's and what the test says the game lists (game_side); no process is ever started."""
import json
from pathlib import Path

from wuxianworkshop.local import adds as A
from wuxianworkshop.local import gameside as G
from wuxianworkshop.local import host as H
from wuxianworkshop.local import manifest as M
from wuxianworkshop.local import pack as K

from .test_host_packages import Case, put

ADDON = "LootPeek"


def game_toc(name, gid=None, version="0.1.0"):
    return (f"## Interface: 16001\n## Title: {name}\n## Version: {version}\n## Dependencies: WuxianKit\n"
            f"## LoadOnDemand: 1\n" + (f"## X-WuxianKit-Extension: {gid}\n" if gid else "") + "\ncore.lua\n")


def write(folder, eid="loot_peek", addons=(ADDON,), gid="", title="掉落一瞥", **fields):
    """a folder of an extension in the game only: wuxian-extension.json without run, addons/<Name> with a .toc (that
    says the WuxianKit extension gid; "": the id, None: none), core.lua and AGENT.md. fields go into the manifest"""
    folder = Path(folder)
    for name in addons:
        put(G.source(folder, name), f"{name}.toc", game_toc(name, eid if gid == "" else gid))
        put(G.source(folder, name), "core.lua", "print('core')\n")
        put(G.source(folder, name), "AGENT.md", f"# {eid}\n")
    manifest = dict(id=eid, title=title, version="0.1.0", addons=list(addons), **fields)
    put(folder, M.MANIFEST, json.dumps(manifest, ensure_ascii=False))
    return folder


class GameCase(Case):
    def setUp(self):
        super().setUp()
        self.online, self.listed = False, {}
        self.host.game_side = lambda: dict(online=self.online, extensions=self.listed)

    def game_only(self, eid="loot_peek", **kw):
        """registers such a folder by its path: (its row, its folder)"""
        folder = write(self.root / "ext" / eid, eid, **kw)
        return self.host.add(str(folder)), folder

    def lists(self, state, eid="loot_peek", addon=ADDON):
        """the game is online and WuxianKit lists the extension so (None: not at all)"""
        self.online = True
        self.listed = {} if state is None else {eid: dict(addon=addon, state=state, version="0.1.0")}

    def game_of(self, eid="loot_peek"):
        row = self.host.get(eid)
        return row["game"]["state"], (row["player"] or {}).get("action")


class Manifest(GameCase):
    def test_where_and_what_it_has(self):
        folder = write(self.root / "a")
        m, problems = M.check_manifest(folder)
        self.assertEqual(problems, [])
        self.assertEqual((m["where"], m["game"], m["run"], m["python"], m["env"], m["pass"], m["start"], m["apps"],
                          m["adds"]), ("game", ["loot_peek"], [], None, {}, [], "use", [], {}))
        self.assertEqual(m["docs"], str(G.source(folder, ADDON) / "AGENT.md"))        # its addon's, the game shows it
        self.assertEqual((M.where_of(m), M.raw_where(folder)), ("game", "game"))
        both = self.source("b")                                                      # run and addons
        local = self.source("c", addons=())
        self.assertEqual([(M.check_manifest(f)[0]["where"], M.raw_where(f)) for f in (both, local)],
                         [("both", "both"), ("local", "local")])
        self.assertEqual(M.check_manifest(both)[0]["game"], [])                      # its .toc names no extension
        self.assertIsNone(M.raw_where(self.root / "none"))

    def test_neither_run_nor_addons(self):
        folder = put(self.root / "n", M.MANIFEST, json.dumps(dict(id="n", title="n", version="1"))).parent
        m, problems = M.check_manifest(folder)
        self.assertIsNone(m)
        (bad,) = [p for p in problems if p["level"] == "error"]
        self.assertIn("neither run nor addons", bad["message"])
        self.assertIsNone(M.raw_where(folder))

    def test_what_means_nothing_and_what_is_missing(self):
        folder = write(self.root / "a", gid=None, env={"A": "1"}, start="workshop", python="py")
        m, problems = M.check_manifest(folder)
        self.assertEqual((m["where"], m["game"], m["start"], m["env"], m["python"]), ("game", [], "use", {}, None))
        said = {p["where"].split(": ")[1]: p for p in problems}
        self.assertEqual(sorted(said), ["env", "id", "python", "start"])
        self.assertTrue(all(p["level"] == "warning" for p in problems))
        self.assertIn("means nothing without run", said["env"]["message"])
        self.assertIn("none of its addons is the WuxianKit extension loot_peek", said["id"]["message"])
        self.assertIn("X-WuxianKit-Extension: loot_peek", said["id"]["hint"])

    def test_the_id_its_own_addon_gives_the_game_is_not_taken(self):
        folder = write(self.root / "a")
        taken = {"loot_peek": ADDON, "guild": "WuxianKit"}
        self.assertEqual(M.check_manifest(folder, taken=taken)[1], [])               # its own addon's: its own
        m, problems = M.check_manifest(folder, taken={"loot_peek": "Another"})        # another addon's: taken
        self.assertIsNone(m)
        self.assertIn("loot_peek", problems[0]["message"])
        m, problems = M.check_manifest(folder, taken=["loot_peek"])                   # nobody says whose: taken
        self.assertIsNone(m)
        with_run = self.source("loot_peek", addons=(ADDON,))                         # one with a process: taken
        self.assertIsNone(M.check_manifest(with_run, taken=taken)[0])
        self.host.taken = lambda: taken
        self.assertEqual(self.host.taken_ids(), taken)
        self.assertEqual(self.host.add(str(folder))["id"], "loot_peek")


class Rows(GameCase):
    def test_a_row(self):
        row, folder = self.game_only()
        self.assertEqual((row["where"], row["state"], row["command"], row["start"], row["error"], row["pid"]),
                         ("game", "game", None, "use", None, None))
        self.assertIn("WuxianKit runs it there", row["why"])
        self.assertEqual((row["approval"], row["allowed"], row["capabilities"], row["apps"]), ("needed", True, [], []))
        self.assertEqual(row["game"], dict(extensions=["loot_peek"], state="absent"))
        self.assertIsNone(row["player"])
        self.assertEqual(self.host.manifest_entries()["extensions"], [])             # the game's WuxianKit lists it
        self.assertEqual(self.host.set("loot_peek", False, None)["state"], "off")
        self.assertIsNone(self.host.get("loot_peek")["player"])
        self.host.set("loot_peek", True, None)
        put(folder, M.MANIFEST, "{")                                                 # a manifest that cannot be read
        row = self.host.get("loot_peek")
        self.assertEqual((row["state"], row["where"], row["game"], row["player"]), ("stopped", None, None, None))
        self.assertTrue(row["error"])

    def test_the_game_half(self):
        self.game_only()
        self.game = None
        self.host.forget_addons("loot_peek")
        self.assertEqual(self.game_of(), ("unknown", None))
        self.game = self.addons
        self.host.forget_addons("loot_peek")
        self.assertEqual(self.game_of(), ("absent", None))                           # the agent's: extension_load
        self.host.install("loot_peek")
        self.assertEqual(self.game_of(), ("offline", "start_game"))
        self.lists(None)
        self.assertEqual(self.game_of(), ("restart", "restart_game"))                # the game has not seen it yet
        for state, action in (("missing", None), ("disabled", "enable_addon"), ("off", "turn_on"), ("on", None)):
            self.lists(state)
            self.assertEqual(self.game_of(), (state, action))
        self.lists("something new")                                                  # a state this workshop knows not
        self.assertEqual(self.game_of(), ("restart", "restart_game"))
        self.lists("off")
        player = self.host.get("loot_peek")["player"]
        self.assertEqual(player, dict(action="turn_on", text="请在游戏里输入 /wk，打开扩展「掉落一瞥」"))
        self.lists("disabled")
        self.assertIn(f"启用「{ADDON}」", self.host.get("loot_peek")["player"]["text"])
        self.lists(None)
        self.assertIn(f"新装的插件「{ADDON}」", self.host.get("loot_peek")["player"]["text"])
        self.assertEqual(set(H.PLAYER_ACTIONS), {"approve", "start_game", "restart_game", "enable_addon", "turn_on"})

    def test_the_worst_of_its_ids_and_none(self):
        self.game_only("two", addons=("TwoA", "TwoB"), gid="")                       # both addons say the id "two"
        self.host.install("two")
        self.lists("on", "two")
        self.assertEqual(self.game_of("two"), ("on", None))
        self.game_only("plain", addons=("Plain",), gid=None)                         # no WuxianKit extension at all
        self.host.install("plain")
        self.assertEqual(self.host.get("plain")["game"], dict(extensions=[], state="installed"))
        self.assertIsNone(self.host.get("plain")["player"])
        self.online = False
        self.assertEqual(self.game_of("plain"), ("offline", "start_game"))

    def test_one_with_a_process_has_a_game_half_too(self):
        row, _ = self.linked("b", addons=("WuxianB",))
        self.assertEqual((row["where"], row["state"], row["game"]), ("both", "stopped", dict(extensions=[], state="absent")))
        self.host.install("b")
        self.assertEqual(self.game_of("b"), ("offline", "start_game"))
        self.online = True
        self.assertEqual(self.game_of("b"), ("installed", None))
        row, _ = self.linked("c", addons=())
        self.assertEqual((row["where"], row["game"], row["player"]), ("local", None, None))

    def test_a_game_side_that_cannot_be_asked(self):
        self.game_only()
        self.host.install("loot_peek")

        def broken():
            raise RuntimeError("no game")
        for side in (broken, lambda: None, lambda: "x", lambda: dict(online=True, extensions=None)):
            self.host.game_side = side
            self.assertIn(self.game_of()[0], ("offline", "restart"))
        self.assertEqual(self.host.side(), dict(online=True, extensions={}))


class Approval(GameCase):
    def test_one_written_here_asks_nobody(self):
        self.auto_now = False                                                        # the player is asked for commands
        row, folder = self.game_only()
        self.assertEqual((row["approval"], row["allowed"], row["player"]), ("needed", True, None))
        self.assertTrue(A.allowed(self.host, self.host.entry("loot_peek")))
        res = self.host.install("loot_peek")
        self.assertEqual([a["state"] for a in res["addons"]], ["installed"])
        self.assertEqual(self.host.get("loot_peek")["approval"], "ok")
        self.assertEqual(self.host.entry("loot_peek")["approved"]["by"], "developer")
        (note,) = self.said("it is in the game only")
        self.assertIn("used without asking the player", note)
        # it gets a process: what it runs changed, and the player is asked as for any other
        manifest = json.loads((folder / M.MANIFEST).read_text(encoding="utf-8"))
        manifest["run"] = ["python", "main.py"]
        put(folder, M.MANIFEST, json.dumps(manifest))
        put(folder, "main.py", "print('x')\n")
        row = self.host.get("loot_peek")
        self.assertEqual((row["where"], row["approval"], row["allowed"], row["state"]), ("both", "changed", False, "stopped"))
        self.assertEqual(row["player"], dict(action="approve",
                                             text="「掉落一瞥」要运行的内容变了，请在无限工坊 App 的「扩展」页重新批准"))
        error = self.err(403, "needs_approval", self.host.load_, "loot_peek")
        self.assertEqual(error.extra["player"], row["player"])

    def test_one_from_a_package_or_found_waits_for_the_player(self):
        zip_path = Path(K.pack(write(self.root / "work" / "loot_peek"), self.root / "out")["file"])
        row = self.host.add(str(zip_path))
        self.assertEqual((row["where"], row["approval"], row["allowed"], row["state"]), ("game", "needed", False, "game"))
        self.assertEqual(row["player"], dict(action="approve", text="请在无限工坊 App 的「扩展」页批准「掉落一瞥」"))
        self.assertEqual(self.stands("loot_peek"), ["missing"])
        for run in (self.host.load_, self.host.install):
            error = self.err(403, "needs_approval", run, "loot_peek")
            self.assertEqual(error.extra["player"]["action"], "approve")
        self.assertEqual(self.stands("loot_peek"), ["missing"])                      # nothing of it went into the game
        row = self.host.approve("loot_peek")                                         # the player: its addons go in
        self.assertEqual((row["approval"], row["allowed"], row["state"]), ("ok", True, "game"))
        installed = row["installed"]                                                 # and the answer says so
        self.assertEqual((installed["ok"], installed["restart"]), (True, True))
        self.assertEqual([(a["folder"], a["state"]) for a in installed["addons"]], [(ADDON, "installed")])
        self.assertIn("restart the game", installed["hint"])
        self.assertNotIn("installed", self.host.get("loot_peek"))                    # (the answer, not the row)
        self.assertEqual(self.stands("loot_peek"), ["current"])
        self.assertEqual(self.host.get("loot_peek")["player"]["action"], "start_game")
        self.assertEqual(self.said("started"), [])                                   # and nothing was started

    def test_a_name_of_the_catalog_is_said_before_and_after(self):
        zip_path = Path(K.pack(write(self.root / "work" / "loot_peek"), self.root / "out")["file"])
        self.host.add(str(zip_path))
        self.host.listed = lambda: {"lootpeek", "Details"}                           # the catalog has an addon so named
        self.host.forget_addons()                                                    # (a row keeps its addons a while)
        self.assertEqual(self.stands("loot_peek"), ["listed"])
        self.assertEqual(self.host.summary("loot_peek")["game"][0]["cannot"], "listed")
        self.assertEqual(self.host.summary(path=str(zip_path))["game"][0]["cannot"], "listed")
        res = self.host.check("loot_peek")
        (problem,) = res["problems"]
        self.assertEqual((res["ok"], problem["level"], problem["where"]), (False, "error", f"{M.MANIFEST}: addons"))
        self.assertIn("LootPeek is the folder name of an addon of the catalog", problem["message"])
        self.assertIn("another name", problem["hint"])
        row = self.host.approve("loot_peek")                                         # allowed all the same,
        installed = row["installed"]                                                 # and the answer says it is not in
        self.assertEqual((row["approval"], installed["ok"], installed["code"], installed["addons"]),
                         ("ok", False, "bad_addon", []))
        self.assertIn("an addon of the catalog", installed["message"])
        self.assertEqual((row["addons"][0]["state"], (self.addons / ADDON).exists()), ("listed", False))
        self.assertEqual(len(self.said("its game addons are not installed")), 1)
        self.game = None                                                             # known without a game folder
        self.assertEqual(self.stands("loot_peek"), ["listed"])
        self.game = self.addons
        self.host.listed = lambda: None                                              # the catalog is not known
        self.host.forget_addons()
        self.assertEqual(self.stands("loot_peek"), ["missing"])
        self.assertTrue(self.host.check("loot_peek")["ok"])
        self.assertEqual(self.host.install("loot_peek")["addons"][0]["state"], "installed")

    def test_the_setting_too(self):
        row, _ = self.game_only()                                                    # auto_now True
        self.host.install("loot_peek")
        self.assertEqual(len(self.said("the setting allows it by itself")), 1)


class Lifecycle(GameCase):
    def test_load_installs_and_starts_nothing(self):
        _, folder = self.game_only()
        res = self.host.load_("loot_peek")
        self.assertEqual(sorted(res), ["addons", "check", "hint", "restart", "row"])
        self.assertEqual(res["check"], dict(ok=True, problems=[], capabilities=[]))
        self.assertEqual([(a["folder"], a["state"], a["was"]) for a in res["addons"]], [(ADDON, "installed", "missing")])
        self.assertTrue(res["restart"])
        self.assertEqual((res["row"]["state"], res["row"]["game"]["state"], res["row"]["pid"]), ("game", "offline", None))
        self.assertTrue((self.addons / ADDON / "core.lua").is_file())
        self.assertEqual(self.host.procs, {})
        put(G.source(folder, ADDON), "core.lua", "print('again')\n")                 # changed: the next load brings it
        res = self.host.load_("loot_peek")
        self.assertEqual([(a["state"], a["was"]) for a in res["addons"]], [("installed", "differs")])
        self.assertEqual((self.addons / ADDON / "core.lua").read_text(encoding="utf-8"), "print('again')\n")
        self.assertEqual([a["state"] for a in self.host.load_("loot_peek")["addons"]], ["current"])

    def test_no_process_to_start_or_call(self):
        self.game_only()
        error = self.host.ensure("loot_peek").exception(5)
        self.assertEqual((error.status, error.code), (409, "no_process"))
        self.assertIn("extension_load puts its addons into the game", error.message)
        self.assertEqual(self.host.describe("loot_peek").exception(5).code, "no_process")
        self.assertEqual(self.host.stop("loot_peek")["state"], "game")
        self.host.autostart()
        self.assertEqual(self.host.procs, {})

    def test_logs_say_where_to_read(self):
        self.game_only()
        res = self.host.logs("loot_peek")
        self.assertEqual((res["lines"], res["total"]), ([], 0))
        self.assertIn("logs with kinds OUT and ERR", res["hint"])
        self.linked("b", addons=())
        self.assertNotIn("hint", self.host.logs("b"))

    def test_check(self):
        _, folder = self.game_only()
        res = self.host.check("loot_peek")
        self.assertEqual(res, dict(ok=True, problems=[], capabilities=[], where="game",
                                   addons=[dict(folder=ADDON, path=str(G.source(folder, ADDON)))]))
        self.assertEqual(self.host.procs, {})
        other = write(self.root / "other")                                           # a folder with the id of one there
        res = self.host.check(str(other))
        self.assertFalse(res["ok"])
        self.assertIn("the id loot_peek is taken by", res["problems"][0]["message"])
        res = self.host.check(str(write(self.root / "w", "fresh", gid=None)))        # not registered: only said
        self.assertTrue(res["ok"])
        self.assertEqual([p["level"] for p in res["problems"]], ["warning"])

    def test_remove_takes_its_addons_out(self):
        _, folder = self.game_only()
        self.host.load_("loot_peek")
        res = self.host.remove("loot_peek")
        self.assertEqual([(a["folder"], a["removed"]) for a in res["addons"]], [(ADDON, True)])
        self.assertFalse((self.addons / ADDON).exists())
        self.assertTrue((folder / M.MANIFEST).is_file())                             # a linked one's folder stays

    def test_off_and_on(self):
        self.game_only()
        self.host.load_("loot_peek")
        row = self.host.set("loot_peek", False, None)
        self.assertEqual((row["state"], row["player"]), ("off", None))
        self.assertTrue((self.addons / ADDON).is_dir())                              # the game's switch is the player's
        self.assertEqual(self.host.set("loot_peek", True, None)["state"], "game")
