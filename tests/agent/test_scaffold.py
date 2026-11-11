"""新建插件 (wuxianworkshop/scaffold.py): the files it makes, its refusals, and both templates run in the mock client:
loaded, logged in, the slash command, then a hot reload the way WoWBridge's Agent.lua does it (OnUnload, the files
again with the same namespace, OnReload with what OnUnload returned). 新建扩展 (new_extension_files): its files, its
refusals, and the extension it makes passes its own tests and answers the workshop's protocol; with addon, its game
half too: the manifest's check and the lint take it, and its Lua runs in the mock client against a stand-in for
WuxianKit (declared once, heard once after a hot load). where game: an extension in the game only (addons/<Addon>
alone, the WuxianKit extension <id> itself, no process), the same way."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support.wowmock import Session, lupa, toc_files
from wuxianworkshop import scaffold
from wuxianworkshop.agent import lint
from wuxianworkshop.local import manifest


def same(s, a, b):
    """the same Lua table (lupa makes a new Python object for each access)"""
    return bool(s.lua.globals()[b"rawequal"](a, b))


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Templates(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addons = Path(tmp.name)

    def run_addon(self, s, name, ns):
        load = s.lua.globals()[b"__load"]
        folder = self.addons / name
        for f in toc_files(folder):
            fn, err = load((folder / f).read_bytes(), f"@{name}/{f}".encode())
            self.assertIsNone(err, f)
            fn(name.encode(), ns)

    @staticmethod
    def printed(s):
        """what reached print(): the client shows it in the chat"""
        out = s.lua.globals()[b"__printed"]
        return "\n".join(out[i].decode("utf-8", "replace") for i in range(1, len(out) + 1))

    def hot_reload(self, s, name, ns):
        state = ns[b"OnUnload"]()
        self.run_addon(s, name, ns)
        ns[b"OnReload"](state)

    def test_the_clients_interface(self):
        """a new addon gets the running client's ## Interface (daemon new_addon passes it), and AGENTS.md says which client"""
        scaffold.create(self.addons, "Later", interface=16100, client="1.61.0.70500")
        self.assertIn("## Interface: 16100", (self.addons / "Later" / "Later.toc").read_text(encoding="utf-8"))
        self.assertIn("1.61.0.70500，`## Interface: 16100`", (self.addons / "Later" / "AGENTS.md").read_text(encoding="utf-8"))

    def test_basic(self):
        res = scaffold.create(self.addons, "HelloWorld", title="你好世界", notes="第一个插件")
        self.assertEqual(sorted(res["files"]), ["AGENTS.md", "CLAUDE.md", "HelloWorld.lua", "HelloWorld.toc"])
        self.assertEqual((res["slash"], res["restart"]), ("/helloworld", True))
        toc = (self.addons / "HelloWorld" / "HelloWorld.toc").read_text(encoding="utf-8")
        self.assertIn("## Interface: 16001", toc)
        self.assertIn("## Title: 你好世界", toc)
        self.assertIn("## SavedVariables: HelloWorldDB", toc)
        self.assertIn("@AGENTS.md", (self.addons / "HelloWorld" / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertIn("load", (self.addons / "HelloWorld" / "AGENTS.md").read_text(encoding="utf-8"))
        s = Session(self, bridge=False, locale="zhCN")
        ns = s.lua.table()
        self.run_addon(s, "HelloWorld", ns)
        fire = s.lua.globals()[b"__fire"]
        fire(b"ADDON_LOADED", b"HelloWorld")
        fire(b"PLAYER_LOGIN")
        self.assertIn("已加载（第 1 次登录）", self.printed(s))
        s.slash("abc", "HELLOWORLD")
        self.assertIn("你好！参数：abc", self.printed(s))
        self.assertTrue(same(s, s.lua.globals()[b"WoWBridgeNS"][b"HelloWorld"], ns))
        self.hot_reload(s, "HelloWorld", ns)
        self.assertIn("已热加载", self.printed(s))
        self.assertEqual(ns[b"db"][b"logins"], 1)                 # the state came across the reload
        events = ns[b"events"]
        self.hot_reload(s, "HelloWorld", ns)
        self.assertTrue(same(s, ns[b"events"], events))                  # one event frame, not one per load

    def test_window(self):
        scaffold.create(self.addons, "MyWindow", template="window")
        s = Session(self, bridge=False)
        ns = s.lua.table()
        self.run_addon(s, "MyWindow", ns)
        win = ns[b"window"]
        self.assertFalse(win[b"shown"])
        s.slash("", "MYWINDOW")
        self.assertTrue(win[b"shown"])
        self.hot_reload(s, "MyWindow", ns)
        self.assertFalse(win[b"shown"])                           # the old one hidden by OnUnload
        self.assertFalse(same(s, ns[b"window"], win))                  # built again from the new code

    def test_refusals(self):
        scaffold.create(self.addons, "Taken")
        for name, template, why in (("1abc", "basic", "插件名"), ("a", "basic", "插件名"), ("has space", "basic", "插件名"),
                                    ("Taken", "basic", "已经有"), ("taken", "basic", "已经有"), ("Fine", "nope", "模板")):
            with self.assertRaises(scaffold.ScaffoldError) as cm:
                scaffold.create(self.addons, name, template=template)
            self.assertIn(why, str(cm.exception), name)
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.create(self.addons / "missing", "Fine")


class Extensions(unittest.TestCase):
    """新建扩展 (new_extension_files): the files, the manifest, the refusals, and the tests it makes pass as made"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_the_files(self):
        folder = self.root / "greeter"
        files = scaffold.new_extension_files(folder, "greeter", '问候 "Hi" \\')
        self.assertEqual(files, ["wuxian-extension.json", "main.py", "AGENT.md", "tests/test_main.py"])
        self.assertEqual(sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()),
                         sorted(files))
        self.assertEqual(json.loads((folder / "wuxian-extension.json").read_text(encoding="utf-8")),
                         {"id": "greeter", "title": '问候 "Hi" \\', "version": "0.1.0",
                          "requires": {"workshop": scaffold.workshop_version()}, "run": ["python", "main.py"],
                          "start": "use"})
        from wuxianworkshop import __version__
        self.assertTrue(__version__.startswith(scaffold.workshop_version()), scaffold.workshop_version())
        self.assertRegex(scaffold.workshop_version(), r"^\d+(\.\d+)+$")
        main = (folder / "main.py").read_text(encoding="utf-8")
        compile(main, "main.py", "exec")
        compile((folder / "tests" / "test_main.py").read_text(encoding="utf-8"), "test_main.py", "exec")
        self.assertIn('@ext.see("hello"', main)
        self.assertIn("wk_greeter_<name>", main)
        agent = (folder / "AGENT.md").read_text(encoding="utf-8")
        self.assertTrue(agent.startswith('# 问候 "Hi" \\ (greeter)'), agent[:40])
        self.assertIn("`wk_greeter_hello`", agent)
        self.assertIn("`greeter:greeted`", agent)
        self.assertNotIn("{{", main + agent)
        self.assertNotIn("game half", main + agent)                             # no addon asked for: none of its words

    def test_the_workshop_takes_its_manifest(self):
        """what new_extension makes passes the manifest's check as it is; the ids it takes are the manifest's (one
        list of reserved ids, the manifest's)"""
        folder = self.root / "greeter"
        scaffold.new_extension_files(folder, "greeter", "问候")
        m, problems = manifest.check_manifest(folder)
        self.assertEqual(problems, [])
        self.assertEqual((m["id"], m["start"], m["apps"], m["adds"]), ("greeter", "use", [], {}))
        self.assertEqual((m["requires"], m["addons"]), ({"workshop": scaffold.workshop_version()}, []))
        self.assertIs(scaffold.EXT_RESERVED, manifest.RESERVED)
        self.assertIs(scaffold.EXT_ID, manifest.ID)
        for eid in ("Bad", "kit", "a" * 25):
            self.assertEqual(scaffold.check_extension_id(eid) is None, manifest.id_problem(eid) is None, eid)
            self.assertIn(manifest.id_problem(eid)["message"], scaffold.check_extension_id(eid))

    def test_ids_taken(self):
        """taken: the ids of the extensions in the game (the installed WuxianKit's), for the id and for the game half's"""
        for kw in (dict(taken=("greeter",)), dict(taken=["other", "greeter_game"], addon="Greeter")):
            with self.assertRaises(scaffold.ScaffoldError) as cm:
                scaffold.new_extension_files(self.root / "t", "greeter", **kw)
            self.assertEqual(cm.exception.code, "bad_request", kw)
            self.assertIn("greeter", str(cm.exception))
        self.assertFalse((self.root / "t").exists())
        self.assertIn("taken", scaffold.check_extension_id("greeter", ("greeter",)))
        self.assertEqual(len(scaffold.new_extension_files(self.root / "t", "greeter", taken=("greeter_game",))), 4)

    def test_the_game_half(self):
        """addon: addons/<Addon> beside the four files, named in the manifest, which the workshop takes as it is"""
        folder = self.root / "zones"
        files = scaffold.new_extension_files(folder, "zones", '地区 "Z" \\', addon="ZoneTrail")
        self.assertEqual(files, ["wuxian-extension.json", "main.py", "AGENT.md", "tests/test_main.py",
                                 "addons/ZoneTrail/ZoneTrail.toc", "addons/ZoneTrail/ZoneTrail.lua"])
        self.assertEqual(sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()),
                         sorted(files))
        self.assertEqual(json.loads((folder / "wuxian-extension.json").read_text(encoding="utf-8")),
                         {"id": "zones", "title": '地区 "Z" \\', "version": "0.1.0",
                          "requires": {"workshop": scaffold.workshop_version(), "kit": "0.6.0"},
                          "run": ["python", "main.py"], "start": "use", "addons": ["ZoneTrail"]})
        m, problems = manifest.check_manifest(folder)
        self.assertEqual(problems, [])
        self.assertEqual((m["addons"], m["requires"]["kit"]), (["ZoneTrail"], "0.6.0"))
        texts = {f: (folder / f).read_text(encoding="utf-8") for f in files}
        self.assertNotIn("{{", "".join(texts.values()))
        compile(texts["main.py"], "main.py", "exec")
        compile(texts["tests/test_main.py"], "test_main.py", "exec")
        self.assertIn('ext.call("zones_game.where")', texts["main.py"])
        self.assertIn('@ext.keep("moved"', texts["main.py"])
        toc = texts["addons/ZoneTrail/ZoneTrail.toc"].splitlines()
        for line in ("## Interface: 16001", '## Title: 地区 "Z" \\', "## Version: 0.1.0", "## Dependencies: WuxianKit",
                     "## OptionalDeps: WoWBridge", "## LoadOnDemand: 1", "## X-WuxianKit-Extension: zones_game",
                     "ZoneTrail.lua"):
            self.assertIn(line, toc)
        lua = texts["addons/ZoneTrail/ZoneTrail.lua"]
        self.assertIn('Kit:Extension("zones_game"', lua)
        self.assertIn('Kit:Requires("0.6.0")', lua)
        self.assertIn('TITLE = "地区 \\"Z\\" \\\\"', lua)                       # the title as a Lua string
        agent = texts["AGENT.md"]
        for said in ("## The game half", "`addons/ZoneTrail`", "`wk_zones_where`", "`wk_zones_game_where`",
                     'extension_install {"id": "zones"}', 'load {"target": "ZoneTrail"}', "restarts the game",
                     'reaction_set {"spec": {"hear": "bus:zones_game.moved", "call": "zones.moved", "item": "event", '
                     '"cooldown": 0}}'):
            self.assertIn(said, agent)

    def test_the_game_half_passes_the_lint(self):
        """the game half's Lua as `check` and `load` see it: nothing wrong; WuxianKit alone is another addon's"""
        folder = self.root / "zones"
        scaffold.new_extension_files(folder, "zones", "地区", addon="ZoneTrail")
        found = lint.check(folder / "addons" / "ZoneTrail")
        self.assertEqual((found["errors"], found["warnings"]), ([], []))
        self.assertEqual(list(found["unresolved"]), ["WuxianKit"])

    def test_the_game_halfs_id(self):
        self.assertEqual(scaffold.game_extension_id("voice"), "voice_game")
        self.assertEqual(scaffold.game_extension_id("a" * 24), "a" * 19 + "_game")
        self.assertEqual(scaffold.game_extension_id("a" * 18 + "_bcdef"), "a" * 18 + "_game")
        for eid in ("ab", "a" * 24, "a" * 18 + "_bcdef", "a_b_c_d_e_f_g_h_i_j_k_l"):
            self.assertIsNone(manifest.id_problem(scaffold.game_extension_id(eid)), eid)
            self.assertNotEqual(scaffold.game_extension_id(eid), eid)
        folder = self.root / "long"
        scaffold.new_extension_files(folder, "a" * 24, addon="Long")
        self.assertIn("## X-WuxianKit-Extension: " + "a" * 19 + "_game",
                      (folder / "addons" / "Long" / "Long.toc").read_text(encoding="utf-8"))
        self.assertEqual(manifest.check_manifest(folder)[1], [])

    def test_where(self):
        """where an extension is and its addon's folder name: an addon alone means both; game and both without one
        name it after the id"""
        for args, out in ((("x",), ("local", None)), (("x", None, "local"), ("local", None)),
                          (("x", "Y"), ("both", "Y")), (("x", "Y", "both"), ("both", "Y")),
                          (("x", "Y", "game"), ("game", "Y")), (("loot_peek", None, "game"), ("game", "LootPeek")),
                          (("a_b9_c", None, "both"), ("both", "AB9C")), (("x", None, "game"), ("game", "X"))):
            self.assertEqual(scaffold.extension_where(*args), out, args)
        for args in (("x", "Y", "local"), ("x", None, "nowhere"), ("x", None, "")):
            with self.assertRaises(scaffold.ScaffoldError) as cm:
                scaffold.extension_where(*args)
            self.assertEqual(cm.exception.code, "bad_request", args)

    def test_in_the_game_only(self):
        """where game: the manifest (no run, no start), addons/<Addon> with its own AGENT.md, and nothing of a process"""
        folder = self.root / "loot_peek"
        files = scaffold.new_extension_files(folder, "loot_peek", "掉落一瞥", where="game")
        self.assertEqual(files, ["wuxian-extension.json", "addons/LootPeek/AGENT.md", "addons/LootPeek/LootPeek.toc",
                                 "addons/LootPeek/LootPeek.lua"])
        self.assertEqual(sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()),
                         sorted(files))
        self.assertEqual(json.loads((folder / "wuxian-extension.json").read_text(encoding="utf-8")),
                         {"id": "loot_peek", "title": "掉落一瞥", "version": "0.1.0",
                          "requires": {"workshop": scaffold.workshop_version(), "kit": "0.6.0"}, "addons": ["LootPeek"]})
        m, problems = manifest.check_manifest(folder, {"loot_peek": "LootPeek", "sense": None})
        self.assertEqual(problems, [])
        self.assertEqual((m["where"], m["game"], m["run"], m["start"], m["addons"]),
                         ("game", ["loot_peek"], [], "use", ["LootPeek"]))
        self.assertEqual(Path(m["docs"]).as_posix().split("/")[-3:], ["addons", "LootPeek", "AGENT.md"])
        texts = {f: (folder / f).read_text(encoding="utf-8") for f in files}
        self.assertNotIn("{{", "".join(texts.values()))
        self.assertNotIn("main.py", "".join(texts.values()))
        toc = texts["addons/LootPeek/LootPeek.toc"].splitlines()
        self.assertEqual(toc, ["## Interface: 16001", "## Title: 掉落一瞥", "## Notes: 游戏内扩展 loot_peek（WuxianKit 扩展）",
                               "## Version: 0.1.0", "## Dependencies: WuxianKit", "## OptionalDeps: WoWBridge",
                               "## LoadOnDemand: 1", "## X-WuxianKit-Extension: loot_peek", "## X-Made-With: 无限工坊", "",
                               "LootPeek.lua"])
        lua = texts["addons/LootPeek/LootPeek.lua"]
        for said in ('Kit:Extension("loot_peek"', 'Kit:Requires("0.6.0")', 'ext:See("where"', 'ext:Event("moved"',
                     "wk_loot_peek_<name>", 'guide {"topic": "game-extension"}'):
            self.assertIn(said, lua)
        agent = texts["addons/LootPeek/AGENT.md"]
        self.assertTrue(agent.startswith("# 掉落一瞥 (loot_peek)"), agent[:40])
        for said in ("`wk_loot_peek_where`", "`loot_peek.moved`", "`bus:loot_peek.moved`", "no process",
                     'extension_load {"id": "loot_peek"}', 'guide {"topic": "game-extension"}',
                     'extension_wait {"id": "loot_peek"}', 'wk_docs {"extension": "loot_peek"}'):
            self.assertIn(said, agent)
        found = lint.check(folder / "addons" / "LootPeek")
        self.assertEqual((found["errors"], found["warnings"], list(found["unresolved"])), ([], [], ["WuxianKit"]))

    def test_in_the_game_only_refused(self):
        """an id the game has already, unless it is this addon's own; a title in English; nothing left behind"""
        for kw in (dict(where="game", taken={"loot_peek": "Other"}), dict(where="game", taken=("loot_peek",)),
                   dict(where="both", taken={"loot_peek_game": "Other"}), dict(where="local", addon="LootPeek"),
                   dict(where="nowhere"), dict(where="game", addon="WuxianKit")):
            with self.assertRaises(scaffold.ScaffoldError) as cm:
                scaffold.new_extension_files(self.root / "t", "loot_peek", **kw)
            self.assertEqual(cm.exception.code, "bad_request", kw)
        self.assertFalse((self.root / "t").exists())
        made = scaffold.new_extension_files(self.root / "t", "loot_peek", where="game", addon="Peek",
                                            taken={"loot_peek": "Peek", "sense": None})    # the game lists it: its own
        self.assertEqual(made[1:], ["addons/Peek/AGENT.md", "addons/Peek/Peek.toc", "addons/Peek/Peek.lua"])
        with mock.patch.object(scaffold, "language", return_value="en"):
            scaffold.new_extension_files(self.root / "en", "en_ext", where="game")
        toc = (self.root / "en" / "addons" / "EnExt" / "EnExt.toc").read_text(encoding="utf-8")
        self.assertIn("## X-WuxianKit-Extension: en_ext", toc.splitlines())
        self.assertIn("## X-Made-With: Wuxian Workshop", toc)
        self.assertNotIn("游戏内扩展", toc)

    def test_addons_refused(self):
        """an addon name that is no folder name, or the workshop's, WuxianKit's or the game's own: nothing is left"""
        (self.root / "empty").mkdir()
        for addon in ("bad name", "a/b", "", "x" * 65, 5, "Blizzard_Zones", "WoWBridge", "WuxianKit"):
            for folder in (self.root / "new", self.root / "empty"):
                with self.assertRaises(scaffold.ScaffoldError) as cm:
                    scaffold.new_extension_files(folder, "zones", addon=addon)
                self.assertEqual(cm.exception.code, "bad_request", addon)
            self.assertFalse((self.root / "new").exists(), addon)
            self.assertEqual(list((self.root / "empty").iterdir()), [], addon)
        self.assertIn("WuxianKit is not an extension's to bring", str(cm.exception))

    def test_its_tests_pass(self):
        folder = self.root / "hello_ext"
        scaffold.new_extension_files(folder, "hello_ext", "你好")
        env = dict(os.environ, PYTHONPATH=str(scaffold.sdk_file().parent), WUXIAN_EXTENSION="hello_ext")
        run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=folder, env=env,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Ran 4 tests", run.stderr)

    def test_its_tests_pass_with_a_game_half(self):
        """the game's answers are given in the tests (ExtensionTest answers=): they need no game"""
        folder = self.root / "zones"
        scaffold.new_extension_files(folder, "zones", "地区", addon="ZoneTrail")
        env = dict(os.environ, PYTHONPATH=str(scaffold.sdk_file().parent), WUXIAN_EXTENSION="zones")
        run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=folder, env=env,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Ran 6 tests", run.stderr)

    def test_it_runs(self):
        """the made extension answers the workshop: initialize, hello, shutdown"""
        folder = self.root / "runner"
        scaffold.new_extension_files(folder, "runner", "Runner")
        env = dict(os.environ, PYTHONPATH=str(scaffold.sdk_file().parent), WUXIAN_EXTENSION="runner",
                   PYTHONIOENCODING="utf-8")
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                     "protocol": {"min": 1, "max": 1}, "workshop": "test", "id": "runner", "options": {}}},
                 {"jsonrpc": "2.0", "id": 2, "method": "call", "params": {
                     "name": "hello", "args": {"name": "Jaina"}, "caller": {"by": "agent"}, "deadline_ms": 30000}},
                 {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}}]
        run = subprocess.run([sys.executable, "main.py"], cwd=folder, env=env, timeout=60, capture_output=True,
                             input="".join(json.dumps(m) + "\n" for m in lines).encode("utf-8"))
        self.assertEqual(run.returncode, 0, run.stderr)
        out = [json.loads(line) for line in run.stdout.decode("utf-8").splitlines()]
        answers = {m["id"]: m for m in out if "id" in m}
        self.assertEqual(answers[1]["result"]["protocol"], 1)
        self.assertEqual([c["name"] for c in answers[1]["result"]["capabilities"]], ["hello"])
        self.assertEqual(answers[2]["result"], {"text": "Hello, Jaina!"})
        self.assertEqual(answers[3]["result"], {})
        self.assertIn({"jsonrpc": "2.0", "method": "event", "params": {"source": "greeted", "data": {
            "name": "Jaina", "text": "Hello, Jaina!"}}}, out)
        self.assertIn(b"runner: ready", run.stderr)

    def test_in_english(self):
        with mock.patch.object(scaffold, "language", return_value="en"):
            scaffold.new_extension_files(self.root / "en", "en_ext")
        main = (self.root / "en" / "main.py").read_text(encoding="utf-8")
        self.assertIn('title="Greeting"', main)
        self.assertIn('"title": "en_ext"', (self.root / "en" / "wuxian-extension.json").read_text(encoding="utf-8"))
        with mock.patch.object(scaffold, "language", return_value="en"):
            scaffold.new_extension_files(self.root / "en2", "en_ext", addon="EnExt")
        self.assertIn('title="Where the player is"', (self.root / "en2" / "main.py").read_text(encoding="utf-8"))
        toc = (self.root / "en2" / "addons" / "EnExt" / "EnExt.toc").read_text(encoding="utf-8")
        self.assertIn("## Notes: The game half of the local extension en_ext (the WuxianKit extension en_ext_game)", toc)
        self.assertIn("## X-Made-With: Wuxian Workshop", toc)

    def test_refusals(self):
        (self.root / "full").mkdir()
        (self.root / "full" / "x.txt").write_text("x", encoding="utf-8")
        (self.root / "empty").mkdir()
        for args, code in (((Path("relative"), "ok"), "bad_request"), ((self.root / "a", "Bad"), "bad_request"),
                           ((self.root / "a", "1abc"), "bad_request"), ((self.root / "a", "a" * 25), "bad_request"),
                           ((self.root / "a", "sense"), "bad_request"), ((self.root / "a", "wk"), "bad_request"),
                           ((self.root / "a", "kit"), "bad_request"),
                           ((self.root / "a", "local"), "bad_request"), ((self.root / "a", "ok", "长" * 17), "bad_request"),
                           ((self.root / "a", "ok", "  "), "bad_request"), ((self.root / "full", "ok"), "folder_not_empty")):
            with self.assertRaises(scaffold.ScaffoldError) as cm:
                scaffold.new_extension_files(*args)
            self.assertEqual(cm.exception.code, code, args)
        self.assertFalse((self.root / "a").exists())                            # nothing made
        self.assertEqual(len(scaffold.new_extension_files(self.root / "empty", "ok", "长" * 16)), 4)
        self.assertIsNone(scaffold.check_extension_id("a" * 24))


# a stand-in for WuxianKit, as much of it as the game half's template uses (Core/Extension.lua): an extension defined
# again is the same object, ext:On adds a handler each time it is called, Emit's topic is <id>.<topic>
KIT = r"""
__zone, __subzone = "Elwynn Forest", "Goldshire"
function GetZoneText() return __zone end
function GetSubZoneText() return __subzone end
__kit = { extensions = {}, handlers = {}, emitted = {}, requires = {} }
local Ext = {}
Ext.__index = Ext
function Ext:See(name, def) self.sees[name] = def end
function Ext:Event(topic, def) self.events[topic] = def end
function Ext:On(event, fn) table.insert(__kit.handlers, { event = event, ext = self, fn = fn }) end
function Ext:Emit(topic, data) table.insert(__kit.emitted, { topic = self.id .. "." .. topic, data = data }) end
WuxianKit = {}
function WuxianKit:Requires(version) table.insert(__kit.requires, version); return true end
function WuxianKit:Words(words)
	local mine = words[GetLocale()] or words.enUS
	return setmetatable({}, { __index = mine, __call = function(_, key) return mine[key] end })
end
function WuxianKit:Clean(text) if type(text) == "string" and text ~= "" then return text end end
function WuxianKit:Extension(id, def)
	local ext = __kit.extensions[id] or setmetatable({ id = id, sees = {}, events = {} }, Ext)
	__kit.extensions[id], ext.def = ext, def
	return ext
end
function __kit.fire(event)
	for _, h in ipairs(__kit.handlers) do if h.event == event then h.fn(h.ext) end end
end
"""


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class GameHalf(unittest.TestCase):
    """the Lua of an extension's game half (new_extension_files with addon) in the mock client"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        scaffold.new_extension_files(Path(tmp.name) / "zones", "zones", '地区 "Z"', addon="ZoneTrail")
        self.folder = Path(tmp.name) / "zones" / "addons" / "ZoneTrail"

    def load(self, s, ns):
        load = s.lua.globals()[b"__load"]
        for f in toc_files(self.folder):
            fn, err = load((self.folder / f).read_bytes(), f"@ZoneTrail/{f}".encode())
            self.assertIsNone(err, f)
            fn(b"ZoneTrail", ns)

    def test_without_wuxiankit(self):
        """WuxianKit is not there (or too old): the file ends at once; the hot load still finds its namespace"""
        s = Session(self, bridge=False)
        ns = s.lua.table()
        self.load(s, ns)
        self.assertTrue(same(s, s.lua.globals()[b"WoWBridgeNS"][b"ZoneTrail"], ns))
        self.assertIsNone(ns[b"Moved"])

    def test_with_wuxiankit(self):
        s = Session(self, bridge=False, locale="zhCN")
        s.lua.execute(KIT.encode("utf-8"))
        kit = s.lua.globals()[b"__kit"]
        ns = s.lua.table()
        self.load(s, ns)
        self.assertEqual(list(kit[b"requires"].values()), [b"0.6.0"])
        self.assertEqual(list(kit[b"extensions"]), [b"zones_game"])
        ext = kit[b"extensions"][b"zones_game"]
        self.assertEqual((ext[b"def"][b"title"].decode("utf-8"), ext[b"def"][b"addon"]), ('地区 "Z"', b"ZoneTrail"))
        self.assertEqual((list(ext[b"sees"]), list(ext[b"events"])), ([b"where"], [b"moved"]))
        self.assertEqual(ext[b"sees"][b"where"][b"title"].decode("utf-8"), "所在地区")
        self.assertEqual(dict(ext[b"events"][b"moved"][b"data"]), {b"zone": b"string", b"subzone": b"string"})
        self.assertEqual(dict(ext[b"sees"][b"where"][b"run"]()), {b"zone": b"Elwynn Forest", b"subzone": b"Goldshire"})
        self.assertEqual(sorted(h[b"event"] for h in kit[b"handlers"].values()),
                         [b"ZONE_CHANGED", b"ZONE_CHANGED_INDOORS", b"ZONE_CHANGED_NEW_AREA"])
        s.lua.execute(b'__zone, __subzone = "Westfall", ""')
        kit[b"fire"](b"ZONE_CHANGED_NEW_AREA")
        self.assertEqual([(e[b"topic"], dict(e[b"data"])) for e in kit[b"emitted"].values()],
                         [(b"zones_game.moved", {b"zone": b"Westfall", b"subzone": b""})])
        self.load(s, ns)                                   # a hot load: the file again with the same namespace
        self.assertEqual(len(kit[b"handlers"]), 3)         # not three more
        kit[b"fire"](b"ZONE_CHANGED")
        self.assertEqual(len(kit[b"emitted"]), 2)
        self.assertEqual(list(kit[b"extensions"]), [b"zones_game"])


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class GameOnlyLua(unittest.TestCase):
    """the Lua of an extension in the game only (new_extension_files where game) in the mock client"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        scaffold.new_extension_files(Path(tmp.name) / "loot_peek", "loot_peek", '掉落 "一瞥"', where="game")
        self.folder = Path(tmp.name) / "loot_peek" / "addons" / "LootPeek"

    def load(self, s, ns):
        load = s.lua.globals()[b"__load"]
        for f in toc_files(self.folder):
            fn, err = load((self.folder / f).read_bytes(), f"@LootPeek/{f}".encode())
            self.assertIsNone(err, f)
            fn(b"LootPeek", ns)

    def test_without_wuxiankit(self):
        s = Session(self, bridge=False)
        ns = s.lua.table()
        self.load(s, ns)
        self.assertTrue(same(s, s.lua.globals()[b"WoWBridgeNS"][b"LootPeek"], ns))
        self.assertIsNone(ns[b"Moved"])

    def test_with_wuxiankit(self):
        """the extension is <id> itself (no _game), declared once, its event <id>.moved; a hot load (extension_load
        runs the files again with the same namespace) adds no handler"""
        s = Session(self, bridge=False, locale="zhCN")
        s.lua.execute(KIT.encode("utf-8"))
        kit = s.lua.globals()[b"__kit"]
        ns = s.lua.table()
        self.load(s, ns)
        self.assertEqual(list(kit[b"requires"].values()), [b"0.6.0"])
        self.assertEqual(list(kit[b"extensions"]), [b"loot_peek"])
        ext = kit[b"extensions"][b"loot_peek"]
        self.assertEqual((ext[b"def"][b"title"].decode("utf-8"), ext[b"def"][b"addon"]), ('掉落 "一瞥"', b"LootPeek"))
        self.assertEqual((list(ext[b"sees"]), list(ext[b"events"])), ([b"where"], [b"moved"]))
        self.assertEqual(ext[b"sees"][b"where"][b"title"].decode("utf-8"), "所在地区")
        self.assertEqual(dict(ext[b"sees"][b"where"][b"run"]()), {b"zone": b"Elwynn Forest", b"subzone": b"Goldshire"})
        self.assertEqual(len(kit[b"handlers"]), 3)
        s.lua.execute(b'__zone, __subzone = "Westfall", ""')
        kit[b"fire"](b"ZONE_CHANGED_NEW_AREA")
        self.assertEqual([(e[b"topic"], dict(e[b"data"])) for e in kit[b"emitted"].values()],
                         [(b"loot_peek.moved", {b"zone": b"Westfall", b"subzone": b""})])
        moved = ns[b"Moved"]
        self.load(s, ns)
        self.assertEqual(len(kit[b"handlers"]), 3)
        self.assertFalse(same(s, ns[b"Moved"], moved))     # the handlers call what the latest run left
        kit[b"fire"](b"ZONE_CHANGED")
        self.assertEqual(len(kit[b"emitted"]), 2)
        self.assertEqual(list(kit[b"extensions"]), [b"loot_peek"])


if __name__ == "__main__":
    unittest.main()
