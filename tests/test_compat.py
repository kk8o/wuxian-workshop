"""compat.py: the X-Wuxian-Requires field (I1) with the contract's shared test vectors, the comparison, the .toc read,
and the judge (I2) over plain snapshots: its items and the chain `next` follows (workshop too old, WuxianKit too old
with a catalog fix that itself needs a newer workshop, restarts pending for WoWBridge, WuxianKit and an addon, ahead
never prompted, an old catalog without needs, versions that are no numbers not judged); the floor (an extension made
for an older WuxianKit than WuxianKit's X-WuxianKit-Floor: blocked with `old`, fixed by the catalog's later version or
by nobody here); the install gate (needs_app / needs_kit) and extensions.state_of beside it; what the MCP status gives
an agent."""
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop import compat as C
from wuxianworkshop import extensions as X


def snap(**kw):
    """a snapshot: this App 0.10.0, nothing else unless given"""
    out = dict(app=dict(version="0.10.0", latest=None), kit_needs="0.7.0", folders={}, catalog=[], extensions=[],
               local=[], agents=None)
    out.update(kw)
    return out


def there(installed, running=None, needs=None):
    return dict(there=True, installed=installed, running=running, needs=needs or {}, title=None)


KIT_071 = dict(id="wuxiankit", folder="WuxianKit", version="0.7.1", title={"zh": "扩展框架"}, needs={"workshop": "0.10.1"})
GUIDES = dict(id="wuxiankit_guides", folder="WuxianKit_Guides", version="0.2.0", title={"zh": "任务指引适配"},
              needs={"WuxianKit": "0.7.1"})


def item(report, iid):
    return next(i for i in report["items"] if i["id"] == iid)


class Declaration(unittest.TestCase):
    def test_the_shared_vectors(self):
        for text, parsed in (("workshop 0.10.0, WuxianKit 0.7.0", {"workshop": "0.10.0", "WuxianKit": "0.7.0"}),
                             ("  WuxianKit   0.7 ", {"WuxianKit": "0.7.0"}),
                             ("WuxianKit 0.7.1,EllesmereUI 1.2.3", {"WuxianKit": "0.7.1", "EllesmereUI": "1.2.3"}),
                             ("WuxianKit", {}),
                             ("WuxianKit x.y, workshop 0.10", {"workshop": "0.10.0"}),
                             ("", {}),
                             ("wuxiankit 0.7.0", {"WuxianKit": "0.7.0"})):
            self.assertEqual(C.parse_requires(text), parsed, text)
        self.assertEqual(C.parse_requires(None), {})
        self.assertEqual(C.parse_requires("ellesmereui 1.2"), {"ellesmereui": "1.2.0"})        # kept as written ...
        self.assertEqual(C.need_of(C.parse_requires("ellesmereui 1.2"), "EllesmereUI"), "1.2.0")   # ... any case matches
        self.assertEqual(C.parse_requires("WuxianKit 0.7.0, wuxiankit 0.7.2, WuxianKit 0.7.1"), {"WuxianKit": "0.7.2"})
        for odd in ("WuxianKit 1.2.3.4", "WuxianKit 0.7 extra", "a/b 1.0", "WuxianKit -1", "WuxianKit 1..2"):
            self.assertEqual(C.parse_requires(odd), {}, odd)

    def test_met_vectors(self):
        for needs, has, ok in (("0.7.0", "0.7.0", True), ("0.7.1", "0.7.0", False), ("0.7", "0.7.0", True),
                               ("0.10.0", "0.9.13", False), ("0.7.0", "0.10.0.dev3", True), ("0.7.0", "?", True),
                               ("0.7.0", None, True), ("0.7.0", "", True), ("0.11.0", "0.10.0.dev3", False),
                               ("1.2.1", "1.2.1-beta", True), ("1.2.2", "1.2.1-beta", False), ("x", "0.1", True)):
            self.assertEqual(C.met(needs, has), ok, (needs, has))
        self.assertEqual((C.compare("0.10.0", "0.9.13"), C.compare("0.7", "0.7.0"), C.compare("?", "1")), (1, 0, None))
        self.assertEqual((C.newest("0.7.0", "0.7.1", None), C.newest(None, "?"), C.newest(None, None)), ("0.7.1", "?", None))

    def test_the_toc_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "WuxianKit_Guides"
            folder.mkdir()
            toc = folder / "WuxianKit_Guides.toc"
            toc.write_text("## Interface: 16001\n## Title: Guides\n## Version: 0.2.0\n## Dependencies: WuxianKit, "
                           "EllesmereUI\n## x-wuxian-requires: workshop 0.10.0, WuxianKit 0.7.1\n\nCore.lua\n",
                           encoding="utf-8")
            self.assertEqual(C.toc_facts(folder), dict(version="0.2.0", title="Guides", deps=["WuxianKit", "EllesmereUI"],
                                                       needs={"workshop": "0.10.0", "WuxianKit": "0.7.1"}, floor=None,
                                                       extension=False))
            toc.write_text("## Version: 0.3.0\n## X-WuxianKit-Floor: 0.6\n## X-WuxianKit-Extension: guides\n",
                           encoding="utf-8")                                              # read again once changed
            self.assertEqual(C.toc_facts(folder), dict(version="0.3.0", title=None, deps=[], needs={}, floor="0.6.0",
                                                       extension=True))
            toc.write_text("## Version: 0.3.1\n## X-WuxianKit-Floor: soon\n## X-WuxianKit-Extension: \n",
                           encoding="utf-8")
            self.assertEqual((C.toc_facts(folder)["floor"], C.toc_facts(folder)["extension"]), (None, False))
            self.assertIsNone(C.toc_facts(Path(tmp) / "Nope"))

    def test_catalog_needs(self):
        self.assertEqual(C.needs_of({"needs": {"workshop": "0.10", "WuxianKit": "0.7.1", "bad": 3}}),
                         {"workshop": "0.10.0", "WuxianKit": "0.7.1"})
        self.assertEqual(C.needs_of({"min_app": "0.9.7"}), {"workshop": "0.9.7"})        # an old catalog
        self.assertEqual(C.needs_of({"min_app": "0.9.7", "needs": {"workshop": "0.10.1"}}), {"workshop": "0.10.1"})
        self.assertEqual(C.needs_of({}), {})


class Judge(unittest.TestCase):
    def test_the_contracts_example(self):
        """WuxianKit_Guides needs WuxianKit 0.7.1, whose catalog version needs 无限工坊 0.10.1: update 无限工坊 first"""
        report = C.judge(snap(app=dict(version="0.10.0", latest="0.10.1"),
                              folders={"WoWBridge": there("0.10.1", "0.10.0"), "WuxianKit": there("0.7.0", "0.7.0"),
                                       "WuxianKit_Guides": there("0.2.0", None, {"WuxianKit": "0.7.1"})},
                              catalog=[KIT_071, GUIDES], agents=dict(seen=1, stale=1)))
        self.assertEqual(report["next"], dict(
            severity="blocked", action="update_app", id="wuxiankit_guides", target="workshop",
            text="WuxianKit_Guides needs WuxianKit 0.7.1; WuxianKit 0.7.1 needs 无限工坊 0.10.1: update 无限工坊 first"))
        self.assertEqual([{k: v for k, v in i.items() if k != "text"} for i in report["items"]], [
            dict(id="workshop", kind="workshop", title="无限工坊", running="0.10.0", latest="0.10.1", state="update",
                 action="update_app"),
            dict(id="wowbridge", kind="bridge", title="WoWBridge", folder="WoWBridge", running="0.10.0", installed="0.10.1",
                 state="pending", action="restart_game"),
            dict(id="wuxiankit", kind="kit", title="扩展框架", folder="WuxianKit", running="0.7.0", installed="0.7.0",
                 latest="0.7.1", needs={"workshop": "0.10.1"}, state="update", action="update"),
            dict(id="wuxiankit_guides", kind="addon", title="任务指引适配", folder="WuxianKit_Guides", running=None,
                 installed="0.2.0", latest="0.2.0", needs={"WuxianKit": "0.7.1"},
                 unmet=[dict(name="WuxianKit", needs="0.7.1", has="0.7.0")], state="blocked", action="update",
                 via="wuxiankit"),
            dict(id="mcp", kind="agent", title="Agent 连接", state="pending", action="restart_agent")])
        # every item that is not fine says its own sentence (I-B); next's is the one of its item
        texts = {i["id"]: i["text"] for i in report["items"]}
        self.assertEqual(texts["workshop"], "无限工坊 0.10.1 is available (this is 0.10.0): update 无限工坊")
        self.assertEqual(texts["wowbridge"], "WoWBridge 0.10.1 is installed but the game still runs 0.10.0: " + C.RESTART)
        self.assertEqual(texts["wuxiankit"], "WuxianKit 0.7.1 is available (0.7.0 is installed); WuxianKit 0.7.1 needs "
                                             "无限工坊 0.10.1: update 无限工坊 first")
        self.assertEqual(texts["wuxiankit_guides"], report["next"]["text"])
        self.assertTrue(texts["mcp"].startswith("an agent's 无限工坊 (wuxian) MCP server runs older code"))

    def test_items_without_a_step_say_nothing(self):
        """ok, ahead and unknown items carry no text"""
        report = C.judge(snap(folders={"WuxianKit": there("0.7.1", "0.7.1"), "WuxianKit_Guides": there("0.3.0")},
                              catalog=[KIT_071, GUIDES]))
        self.assertEqual([(i["id"], i["state"], "text" in i) for i in report["items"]],
                         [("workshop", "ok", False), ("wuxiankit", "ok", False), ("wuxiankit_guides", "ahead", False)])

    def test_update_app_says_what_the_player_clicks(self):
        """update_app's sentence follows the updater's state (app.update) and the App's real labels"""
        def said(state, latest="0.10.1", local=()):
            return C.judge(snap(app=dict(version="0.10.0", latest=latest, update=state), local=list(local)))["next"]["text"]
        where = "on the App's 设置 (Settings) page, 关于与更新 (About & updates)"
        self.assertEqual(said("available"), "无限工坊 0.10.1 is available (this is 0.10.0): update 无限工坊; the player "
                                            f"clicks 下载 0.10.1 (Download 0.10.1) {where}, then 重启并更新 (Restart and "
                                            "update) once it is downloaded")
        self.assertTrue(said("downloading").endswith(f"无限工坊 0.10.1 is downloading: then the player clicks 重启并更新 "
                                                     f"(Restart and update) {where}"))
        self.assertTrue(said("ready").endswith(f"无限工坊 0.10.1 is downloaded: the player clicks 重启并更新 (Restart and "
                                               f"update) {where}"))
        self.assertTrue(said("unsupported").endswith("the player downloads the new version from "
                                                     "https://wuxianwow.com/workshop"))
        self.assertEqual(said(None), "无限工坊 0.10.1 is available (this is 0.10.0): update 无限工坊")   # none known
        needy = [dict(id="voice", title="语音", version="1.0", requires={"workshop": "0.11.0"}, addons=[])]
        self.assertEqual(said("current", None, needy), "语音 needs 无限工坊 0.11.0: update 无限工坊; the player clicks "
                                                       f"检查更新 (Check for updates) {where}")
        self.assertEqual(said("available", "0.10.1", needy), "语音 needs 无限工坊 0.11.0: update 无限工坊; no 无限工坊 "
                                                             "0.11.0 is out yet (the newest is 0.10.1): the player clicks "
                                                             f"检查更新 (Check for updates) {where} later")
        self.assertTrue(said("unsupported", "0.10.1", needy).endswith("https://wuxianwow.com/workshop"))
        self.assertEqual(item(C.judge(snap(app=dict(version="0.10.0", latest="0.11.0", update="ready"), local=needy)),
                              "voice")["text"],
                         "语音 needs 无限工坊 0.11.0: update 无限工坊; 无限工坊 0.11.0 is downloaded: the player clicks "
                         f"重启并更新 (Restart and update) {where}")

    def test_the_kit_fix_when_the_workshop_is_new_enough(self):
        report = C.judge(snap(app=dict(version="0.10.1", latest=None),
                              folders={"WuxianKit": there("0.7.0", "0.7.0"),
                                       "WuxianKit_Guides": there("0.2.0", None, {"WuxianKit": "0.7.1"})},
                              catalog=[KIT_071, GUIDES]))
        self.assertEqual(report["next"], dict(
            severity="blocked", action="update", id="wuxiankit_guides", target="wuxiankit", via="wuxiankit",
            text="WuxianKit_Guides needs WuxianKit 0.7.1: update WuxianKit to 0.7.1 on the App's Extensions (扩展) page"))
        self.assertEqual(item(report, "wuxiankit")["state"], "update")

    def test_a_workshop_that_is_too_old(self):
        """an addon or a local extension that needs a newer 无限工坊: update_app"""
        report = C.judge(snap(folders={"Foo": there("1.0", None, {"workshop": "0.11"})}, extensions=["Foo"],
                              local=[dict(id="voice", title="语音", version="1.0", requires={"workshop": "0.12.0"},
                                          addons=[])]))
        self.assertEqual(report["next"], dict(severity="blocked", action="update_app", id="Foo", target="workshop",
                                              text="Foo needs 无限工坊 0.11.0: update 无限工坊"))
        voice = item(report, "voice")
        self.assertEqual((voice["kind"], voice["state"], voice["action"], voice["via"], voice["unmet"]),
                         ("local", "blocked", "update_app", "workshop",
                          [dict(name="workshop", needs="0.12.0", has="0.10.0")]))
        self.assertEqual(C.judge(snap(local=[dict(id="voice", title="语音", version="1.0",
                                                   requires={"workshop": "0.12.0"}, addons=[])]))["next"]["text"],
                         "语音 needs 无限工坊 0.12.0: update 无限工坊")

    def test_a_local_extension_and_its_addons(self):
        """requires.kit (the folder WuxianKit) and the addons' X-Wuxian-Requires together; the stricter one counts; its
        addons are not items of their own; a running addon of another version: a restart"""
        x = dict(id="voice", title="语音", version="1.0", requires={"kit": "0.7.0"}, addons=["WuxianVoice"])
        folders = {"WuxianKit": there("0.7.0", "0.7.0"), "WuxianVoice": there("1.0", "1.0", {"WuxianKit": "0.7.1"})}
        report = C.judge(snap(folders=folders, local=[x], extensions=["WuxianVoice"]))
        self.assertEqual([i["id"] for i in report["items"]], ["workshop", "wuxiankit", "voice"])
        voice = item(report, "voice")
        self.assertEqual((voice["needs"], voice["state"], voice["action"]), ({"WuxianKit": "0.7.1"}, "blocked", "none"))
        self.assertEqual(report["next"]["text"], "语音 needs WuxianKit 0.7.1: no WuxianKit 0.7.1 or newer is known: "
                                                 "update WuxianKit yourself")
        folders["WuxianVoice"] = there("1.1", "1.0")
        voice = item(C.judge(snap(folders=folders, local=[x])), "voice")
        self.assertEqual((voice["state"], voice["action"]), ("pending", "restart_game"))

    def test_restarts_pending(self):
        """WoWBridge, WuxianKit and an extension addon: what the game runs is not what is installed"""
        report = C.judge(snap(folders={"WoWBridge": there("0.10.0", "0.9.14"), "WuxianKit": there("0.7.1", "0.7.0"),
                                       "Foo": there("1.2", "1.1")}, extensions=["Foo"]))
        self.assertEqual([(i["id"], i["state"], i["action"]) for i in report["items"]],
                         [("workshop", "ok", "none"), ("wowbridge", "pending", "restart_game"),
                          ("wuxiankit", "pending", "restart_game"), ("Foo", "pending", "restart_game")])
        self.assertEqual(report["next"], dict(severity="pending", action="restart_game", id="wowbridge", target="wowbridge",
                                              text=f"WoWBridge 0.10.0 is installed but the game still runs 0.9.14: "
                                                   f"{C.RESTART}"))
        self.assertEqual(C.judge(snap(folders={"WuxianKit": there("0.7.1", "0.7.0")}), focus="wowbridge")["next"], None)

    def test_a_restart_is_the_fix(self):
        """the addon needs WuxianKit 0.7.1, which is installed, but the game still runs 0.7.0"""
        report = C.judge(snap(folders={"WuxianKit": there("0.7.1", "0.7.0"),
                                       "WuxianKit_Guides": there("0.2.0", None, {"WuxianKit": "0.7.1"})},
                              catalog=[KIT_071, GUIDES]))
        self.assertEqual(report["next"], dict(
            severity="blocked", action="restart_game", id="wuxiankit_guides", target="wuxiankit",
            text=f"WuxianKit_Guides needs WuxianKit 0.7.1: WuxianKit 0.7.1 is installed but the game still runs 0.7.0: "
                 f"{C.RESTART}"))

    def test_the_bridge_without_its_toc(self):
        """WoWBridge's .toc cannot be read: the App's own version stands for it (never called installed), and only an
        older one counts"""
        def bridge(running):
            return C.judge(snap(folders={"WoWBridge": dict(there=False, installed=None, running=running, needs={})}),
                           focus="wowbridge")["next"]
        self.assertEqual(bridge("0.9.12")["text"], f"无限工坊 0.10.0 brings WoWBridge 0.10.0 but the game still runs "
                                                   f"0.9.12: {C.CLOSE}")
        for fine in ("0.10.0", "0.10.0.dev2", "99.0", "?"):
            self.assertIsNone(bridge(fine), fine)

    def test_the_bridge_after_an_app_update(self):
        """the App was updated while the game runs: the .toc on disk and the HELLO still say 0.10.0 (the App puts its
        WoWBridge in place once the game is closed), the App is 0.10.1: pending, close the game and start it"""
        def report(installed, running, app="0.10.1"):
            return C.judge(snap(app=dict(version=app, latest=None),
                                folders={"WoWBridge": there(installed, running)}))
        r = report("0.10.0", "0.10.0")
        bridge = item(r, "wowbridge")
        self.assertEqual((bridge["state"], bridge["action"], bridge["installed"]), ("pending", "restart_game", "0.10.0"))
        self.assertEqual(r["next"], dict(severity="pending", action="restart_game", id="wowbridge", target="wowbridge",
                                         text=f"无限工坊 0.10.1 brings WoWBridge 0.10.1 but the game still runs 0.10.0: "
                                              f"{C.CLOSE}"))
        self.assertEqual(report("0.10.0", "0.10.0", "0.10.1.dev4")["next"]["text"],     # a development build: its numbers
                         f"无限工坊 0.10.1 brings WoWBridge 0.10.1 but the game still runs 0.10.0: {C.CLOSE}")
        self.assertEqual(report("0.10.1", "0.10.0")["next"]["text"],                     # in place: a restart does it
                         f"WoWBridge 0.10.1 is installed but the game still runs 0.10.0: {C.RESTART}")
        self.assertEqual(report("0.10.2", "0.10.1")["next"]["text"],                     # a developer's newer one
                         f"WoWBridge 0.10.2 is installed but the game still runs 0.10.1: {C.RESTART}")
        for installed, running, app in (("0.10.1", "0.10.1", "0.10.1"), ("0.10.0", "0.10.1", "0.10.1"),
                                        ("0.10.0", "0.10.0", "?"), ("0.10.0", "?", "0.10.1")):
            self.assertEqual(item(report(installed, running, app), "wowbridge")["state"], "ok",
                             (installed, running, app))

    def test_ahead_is_never_prompted(self):
        report = C.judge(snap(folders={"WuxianKit": there("0.8.0", "0.8.0")}, catalog=[KIT_071]))
        self.assertEqual((item(report, "wuxiankit")["state"], report["next"]), ("ahead", None))

    def test_an_old_catalog_without_needs(self):
        """an entry with min_app only: as before (the program's version alone); an update it offers is optional"""
        old = dict(id="wuxiankit", folder="WuxianKit", version="0.7.1", title={"zh": "扩展框架"}, min_app="0.11.0")
        report = C.judge(snap(folders={"WuxianKit": there("0.7.0", "0.7.0")}, catalog=[old]))
        kit = item(report, "wuxiankit")
        self.assertEqual((kit["state"], kit["needs"]), ("update", {"workshop": "0.11.0"}))
        self.assertEqual(report["next"], dict(
            severity="update", action="update_app", id="wuxiankit", target="workshop",
            text="WuxianKit 0.7.1 is available (0.7.0 is installed); WuxianKit 0.7.1 needs 无限工坊 0.11.0: update "
                 "无限工坊 first"))
        plain = dict(old, min_app=None)
        report = C.judge(snap(folders={"WuxianKit": there("0.7.0", "0.7.0")}, catalog=[plain]))
        self.assertEqual(report["next"]["text"], "WuxianKit 0.7.1 is available (0.7.0 is installed): update WuxianKit to "
                                                 "0.7.1 on the App's Extensions (扩展) page")

    def test_unknown_versions_are_not_judged(self):
        report = C.judge(snap(app=dict(version="0.10.0.dev3", latest=None),
                              folders={"WoWBridge": there("?", "0.10.0"), "WuxianKit": there(None, "dev"),
                                       "Foo": there("1.0", None, {"WuxianKit": "0.7.1", "workshop": "0.10.0",
                                                                  "Gone": "2.0"})},
                              extensions=["Foo"]))
        self.assertEqual(report["next"], None)
        self.assertEqual([i["state"] for i in report["items"]], ["ok", "ok", "ok", "ok"])

    def test_the_workshop_needs_wuxiankit(self):
        """KIT_NEEDS: a WuxianKit older than 0.7.0 is the workshop item's unmet; the catalog's is the fix"""
        report = C.judge(snap(folders={"WuxianKit": there("0.6.1", "0.6.1")},
                              catalog=[dict(KIT_071, needs={"workshop": "0.10.0"})]))
        work = item(report, "workshop")
        self.assertEqual((work["state"], work["unmet"], work["action"], work["via"]),
                         ("blocked", [dict(name="WuxianKit", needs="0.7.0", has="0.6.1")], "update", "wuxiankit"))
        self.assertEqual(report["next"]["text"], "无限工坊 needs WuxianKit 0.7.0: update WuxianKit to 0.7.1 on the App's "
                                                 "Extensions (扩展) page")

    def test_the_chain_stops(self):
        """a chain of catalog entries that each need a newer one: followed DEPTH steps, then none"""
        catalog = [dict(id=f"a{n}", folder=f"A{n}", version="2.0", title=None, needs={f"A{n + 1}": "2.0"})
                   for n in range(1, 8)]
        folders = {f"A{n}": there("1.0", "1.0") for n in range(1, 9)}
        folders["Foo"] = there("1.0", None, {"A1": "2.0"})
        report = C.judge(snap(folders=folders, catalog=catalog, extensions=["Foo"]))
        self.assertEqual(report["next"]["action"], "none")
        self.assertEqual(report["next"]["text"].count(" needs "), C.DEPTH)
        self.assertIn("too long to follow", report["next"]["text"])

    def test_agents(self):
        self.assertEqual(item(C.judge(snap(agents=dict(seen=0, stale=0))), "mcp")["state"], "unknown")
        self.assertEqual(item(C.judge(snap(agents=dict(seen=2, stale=0))), "mcp")["state"], "ok")
        self.assertNotIn("mcp", [i["id"] for i in C.judge(snap())["items"]])
        report = C.judge(snap(agents=dict(seen=2, stale=1)))
        self.assertEqual((report["next"]["action"], report["next"]["severity"]), ("restart_agent", "pending"))
        stale = C.agent_stale(C.judge(snap()), "reconnect it")                # an MCP server on stdio knows itself
        self.assertEqual(stale["next"], dict(severity="pending", action="restart_agent", id="mcp", target="mcp",
                                             text="reconnect it"))
        blocked = C.judge(snap(folders={"Foo": there("1.0", None, {"workshop": "1.0"})}, extensions=["Foo"]))
        self.assertEqual(C.agent_stale(blocked)["next"], blocked["next"])    # something more severe stays first
        self.assertEqual(C.brief(stale, mine=True), dict(next=stale["next"], items=[item(stale, "mcp")]))

    def test_an_agent_is_told_of_its_own_server_only(self):
        """another agent's stale MCP server is the App page's: the brief an agent gets leaves the agent items out and
        its next is the next without them (agent_next), unless its own server is stale (mine)"""
        report = C.judge(snap(app=dict(version="0.10.0", latest="0.10.1"), agents=dict(seen=2, stale=1)))
        self.assertEqual((report["next"]["id"], item(report, "mcp")["state"]), ("mcp", "pending"))
        self.assertEqual(report["agent_next"]["action"], "update_app")              # the App page keeps them
        got = C.brief(report)
        self.assertEqual(got, dict(next=report["agent_next"], items=[item(report, "workshop")]))
        quiet = C.judge(snap(agents=dict(seen=2, stale=1)))
        self.assertEqual(C.brief(quiet), dict(next=None, items=[]))
        self.assertNotIn("agent_next", C.judge(snap(agents=dict(seen=2, stale=0))))
        mine = C.agent_stale(report, "reconnect it")
        self.assertEqual(mine["next"]["text"], "reconnect it")                     # this agent's words, not another's
        self.assertEqual([i["id"] for i in C.brief(mine, mine=True)["items"]], ["workshop", "mcp"])


def kit(floor, version="0.7.1"):
    """WuxianKit installed and running at version, with a floor"""
    return dict(there(version, version), floor=floor)


def ext(installed, needs=None, running=None):
    """an extension addon on disk: its .toc says X-WuxianKit-Extension"""
    return dict(there(installed, running, needs), extension=True)


OLD = dict(id="wuxiankit_old", folder="WuxianKit_Old", version="0.2.0", title={"zh": "旧扩展"},
           needs={"WuxianKit": "0.7.1"})


class Floor(unittest.TestCase):
    """an extension made for a WuxianKit older than the floor: blocked with old {built, floor}, no unmet; its fix is the
    catalog's later version of it, else none (its author's); never 无限工坊 or WuxianKit"""

    def test_a_local_extension_judged_by_its_oldest_declaration(self):
        """a local extension's manifest says kit 0.6.0 but its addon's .toc says WuxianKit 0.5.0: the game judges the
        .toc, so the App does too (the oldest of the two)"""
        x = dict(id="chat", title="聊天", version="1.0", requires={"kit": "0.6.0"}, addons=["WuxianChat"])
        report = C.judge(snap(folders={"WuxianKit": kit("0.6.0", "0.10.0"),
                                       "WuxianChat": ext("1.0", {"WuxianKit": "0.5.0"}, "1.0")}, local=[x]))
        item = next(i for i in report["items"] if i["id"] == "chat")
        self.assertEqual((item["state"], item["old"]), ("blocked", dict(built="0.5.0", floor="0.6.0")))
        x["requires"] = {"kit": "0.10.0"}
        report = C.judge(snap(folders={"WuxianKit": kit("0.6.0", "0.10.0"),
                                       "WuxianChat": ext("1.0", {"WuxianKit": "0.6.0"}, "1.0")}, local=[x]))
        self.assertEqual(next(i for i in report["items"] if i["id"] == "chat")["state"], "ok")

    def test_the_floor_only_for_a_game_half(self):
        """(I-D) a local extension without addons and without an app it drives is never too old; its requires.kit
        still counts as a minimum. One that drives an app in the game is judged as one with addons"""
        folders = {"WuxianKit": kit("0.6.0", "0.10.0")}
        local = dict(id="notes", title="笔记", version="1.0", requires={"kit": "0.5.0"}, addons=[])
        self.assertEqual(item(C.judge(snap(folders=folders, local=[local])), "notes")["state"], "ok")
        self.assertEqual(item(C.judge(snap(folders=folders, local=[dict(local, requires={})])), "notes")["state"], "ok")
        driver = item(C.judge(snap(folders=folders, local=[dict(local, drives=True)])), "notes")
        self.assertEqual((driver["state"], driver["old"]), ("blocked", dict(built="0.5.0", floor="0.6.0")))
        self.assertEqual(driver["text"], "笔记 was made for WuxianKit 0.5.0; WuxianKit 0.10.0 runs extensions made for "
                                         "0.6.0 or later: update 笔记")
        needy = item(C.judge(snap(folders=folders, local=[dict(local, requires={"kit": "0.11.0"})])), "notes")
        self.assertEqual((needy["state"], needy["unmet"]), ("blocked", [dict(name="WuxianKit", needs="0.11.0",
                                                                             has="0.10.0")]))

    def test_the_sentence_of_a_game_half_not_loaded(self):
        """gate_text: what WuxianKit's row of an addon it did not load says, when the report has no sentence"""
        row = dict(state="outdated", gate="outdated", unmet=[dict(name="WuxianKit", needs="0.11.0", has="0.10.0")])
        self.assertEqual(C.gate_text("聊天", row, "0.10.0", "WuxianChat"),
                         "WuxianChat needs WuxianKit 0.11.0 (the game has 0.10.0): update WuxianKit on the App's "
                         "Extensions (扩展) page, then restart the game fully")
        self.assertEqual(C.gate_text("聊天", dict(row, unmet=[dict(name="workshop", needs="0.11.0")])),
                         "聊天 needs 无限工坊 0.11.0: update 无限工坊")
        self.assertEqual(C.gate_text("聊天", dict(row, unmet=[dict(name="EllesmereUI", needs="2.0.0", has="1.0")])),
                         "聊天 needs EllesmereUI 2.0.0 (the game has 1.0): update EllesmereUI, then restart the game fully")
        old = dict(state="too_old", gate="too_old", built="0.5.0", floor="0.6.0")
        self.assertEqual(C.gate_text("聊天", old, "0.10.0"),
                         "聊天 was made for WuxianKit 0.5.0; WuxianKit 0.10.0 runs extensions made for 0.6.0 or later: "
                         "update 聊天")
        self.assertEqual(C.gate_text("聊天", dict(old, built=None), None),
                         "聊天 was made for WuxianKit 0.7.0; this WuxianKit runs extensions made for 0.6.0 or later: "
                         "update 聊天")
        self.assertEqual(C.gate_text("聊天", {}, None, "WuxianChat"),
                         "WuxianKit did not load WuxianChat for its version: update 聊天")

    def test_too_old_with_a_catalog_update(self):
        report = C.judge(snap(app=dict(version="0.10.0", latest="0.10.1"),
                              folders={"WuxianKit": kit("0.6.0"), "WuxianKit_Old": ext("0.1.0", {"WuxianKit": "0.5.0"})},
                              catalog=[KIT_071, OLD]))
        self.assertEqual(item(report, "wuxiankit_old"), dict(
            id="wuxiankit_old", kind="addon", title="旧扩展", folder="WuxianKit_Old", running=None, installed="0.1.0",
            latest="0.2.0", needs={"WuxianKit": "0.5.0"}, old={"built": "0.5.0", "floor": "0.6.0"}, state="blocked",
            action="update", text="旧扩展 was made for WuxianKit 0.5.0; WuxianKit 0.7.1 runs extensions made for 0.6.0 or "
                                  "later: update 旧扩展"))
        self.assertEqual(report["next"], dict(
            severity="blocked", action="update", id="wuxiankit_old", target="wuxiankit_old",
            text="旧扩展 was made for WuxianKit 0.5.0; WuxianKit 0.7.1 runs extensions made for 0.6.0 or later: "
                 "update 旧扩展"))

    def test_too_old_without_an_update(self):
        """reported by the game's WuxianKit, not in the catalog; or the catalog has no later version: none"""
        report = C.judge(snap(folders={"WuxianKit": kit("0.6.0"), "Foo": there("1.0", "1.0", {"WuxianKit": "0.5"})},
                              extensions=["Foo"]))
        foo = item(report, "Foo")
        self.assertEqual((foo["state"], foo["old"], foo["action"], "unmet" in foo, "via" in foo),
                         ("blocked", {"built": "0.5.0", "floor": "0.6.0"}, "none", False, False))
        self.assertEqual(report["next"], dict(
            severity="blocked", action="none", id="Foo", target=None,
            text="Foo was made for WuxianKit 0.5.0; WuxianKit 0.7.1 runs extensions made for 0.6.0 or later: update Foo"))
        same = C.judge(snap(folders={"WuxianKit": kit("0.6.0"), "WuxianKit_Old": ext("0.2.0", {"WuxianKit": "0.5.0"})},
                            catalog=[OLD]))
        self.assertEqual((item(same, "wuxiankit_old")["action"], same["next"]["action"]), ("none", "none"))

    def test_the_update_must_clear_the_floor(self):
        """the catalog's later version is offered only when it clears the floor itself (its needs.WuxianKit, else
        LEGACY); one still too old: none"""
        def judged(needs, floor="0.6.0"):
            entry = dict(OLD, needs=needs)
            return C.judge(snap(folders={"WuxianKit": kit(floor), "WuxianKit_Old": ext("0.1.0", {"WuxianKit": "0.5.0"})},
                                catalog=[entry]))
        report = judged({"WuxianKit": "0.5.5"})
        self.assertEqual((item(report, "wuxiankit_old")["action"], report["next"]["action"], report["next"]["target"]),
                         ("none", "none", None))
        self.assertTrue(report["next"]["text"].endswith(": update 旧扩展"))
        report = judged({})                                                   # undeclared: LEGACY 0.7.0 clears 0.6.0
        self.assertEqual((item(report, "wuxiankit_old")["action"], report["next"]["action"]), ("update", "update"))
        report = judged({}, floor="0.7.1")                                    # ... but not 0.7.1
        self.assertEqual(report["next"]["action"], "none")

    def test_the_update_with_needs_of_its_own(self):
        """the fixing version needs a newer 无限工坊 or WuxianKit: the chain of an update, not a bare update that the
        install would refuse"""
        report = C.judge(snap(folders={"WuxianKit": kit("0.6.0"), "WuxianKit_Old": ext("0.1.0", {"WuxianKit": "0.5.0"})},
                              catalog=[dict(OLD, needs={"WuxianKit": "0.7.1", "workshop": "0.10.1"})]))
        old = item(report, "wuxiankit_old")
        self.assertEqual((old["state"], old["action"], "via" in old), ("blocked", "update", False))
        self.assertEqual(report["next"], dict(
            severity="blocked", action="update_app", id="wuxiankit_old", target="workshop",
            text="旧扩展 was made for WuxianKit 0.5.0; WuxianKit 0.7.1 runs extensions made for 0.6.0 or later; "
                 "WuxianKit_Old 0.2.0 needs 无限工坊 0.10.1: update 无限工坊 first"))
        report = C.judge(snap(folders={"WuxianKit": kit("0.6.0"), "WuxianKit_Old": ext("0.1.0", {"WuxianKit": "0.5.0"})},
                              catalog=[dict(KIT_071, version="0.7.2", needs={}),
                                       dict(OLD, needs={"WuxianKit": "0.7.2"})]))
        self.assertEqual(report["next"], dict(
            severity="blocked", action="update", id="wuxiankit_old", target="wuxiankit", via="wuxiankit",
            text="旧扩展 was made for WuxianKit 0.5.0; WuxianKit 0.7.1 runs extensions made for 0.6.0 or later; "
                 "WuxianKit_Old 0.2.0 needs WuxianKit 0.7.2: update WuxianKit to 0.7.2 on the App's Extensions (扩展) page"))

    def test_undeclared_is_legacy(self):
        """no WuxianKit item: made for 0.7.0, not too old at the floor 0.6.0, too old at 0.10.0"""
        def foo(floor):
            report = C.judge(snap(folders={"WuxianKit": kit(floor, "0.10.0"), "WuxianKit_Old": ext("0.2.0")},
                                  catalog=[OLD]))
            return item(report, "wuxiankit_old"), report["next"]
        it, nxt = foo("0.6.0")
        self.assertEqual((it["state"], "old" in it, nxt), ("ok", False, None))
        it, nxt = foo("0.10.0")
        self.assertEqual((it["state"], it["old"], it["action"]), ("blocked", {"built": C.LEGACY, "floor": "0.10.0"},
                                                                  "none"))
        self.assertEqual(nxt["text"], "旧扩展 was made for WuxianKit 0.7.0; WuxianKit 0.10.0 runs extensions made for "
                                      "0.10.0 or later: update 旧扩展")
        it, nxt = foo(None)                                                   # no floor: nothing is too old
        self.assertEqual((it["state"], nxt), ("ok", None))

    def test_outdated_and_too_old_shows_the_unmet(self):
        report = C.judge(snap(folders={"WuxianKit": kit("0.6.0"), "EllesmereUI": there("8.0", "8.0"),
                                       "Foo": there("1.0", None, {"WuxianKit": "0.5.0", "EllesmereUI": "9.0"})},
                              extensions=["Foo"]))
        foo = item(report, "Foo")
        self.assertEqual((foo["state"], foo["unmet"], "old" in foo),
                         ("blocked", [dict(name="EllesmereUI", needs="9.0.0", has="8.0")], False))
        self.assertEqual(report["next"]["text"], "Foo needs EllesmereUI 9.0.0: no EllesmereUI 9.0.0 or newer is known: "
                                                 "update EllesmereUI yourself")

    def test_only_extensions_are_judged(self):
        """a catalog addon whose .toc is no extension's, WuxianKit itself, a pending update in place"""
        report = C.judge(snap(folders={"WuxianKit": kit("0.6.0"),
                                       "WuxianKit_Old": there("0.1.0", None, {"WuxianKit": "0.5.0"})}, catalog=[OLD]))
        self.assertEqual([(i["id"], i["state"]) for i in report["items"]],
                         [("workshop", "ok"), ("wuxiankit", "ok"), ("wuxiankit_old", "update")])
        report = C.judge(snap(folders={"WuxianKit": kit("0.6.0"),
                                       "WuxianKit_Old": ext("0.2.0", {"WuxianKit": "0.7.1"}, running="0.1.0")},
                              catalog=[OLD]))                                 # the new one is installed, not yet loaded
        self.assertEqual((item(report, "wuxiankit_old")["state"], report["next"]["action"]), ("pending", "restart_game"))

    def test_local_extensions(self):
        """requires.kit, else the oldest of its addons' declarations, else LEGACY; the fix is its author's"""
        def voice(requires, addon_needs, floor):
            x = dict(id="voice", title="语音", version="1.0", requires=requires, addons=["WuxianVoice", "WuxianVoice2"])
            folders = {"WuxianKit": kit(floor, "0.10.0"), "WuxianVoice": ext("1.0", addon_needs),
                       "WuxianVoice2": ext("1.0", {"WuxianKit": "0.9.0"} if addon_needs else None)}
            report = C.judge(snap(folders=folders, local=[x]))
            return item(report, "voice"), report["next"]
        it, nxt = voice({"kit": "0.5.0"}, {"WuxianKit": "0.8.0"}, "0.6.0")
        self.assertEqual((it["state"], it["old"], it["action"], "via" in it), ("blocked", {"built": "0.5.0",
                                                                                         "floor": "0.6.0"}, "none", False))
        self.assertEqual(nxt, dict(severity="blocked", action="none", id="voice", target=None,
                                   text="语音 was made for WuxianKit 0.5.0; WuxianKit 0.10.0 runs extensions made for "
                                        "0.6.0 or later: update 语音"))
        it, _ = voice({}, {"WuxianKit": "0.8.0"}, "0.8.5")                    # the oldest of 0.8.0 and 0.9.0
        self.assertEqual(it.get("old"), {"built": "0.8.0", "floor": "0.8.5"})
        self.assertEqual(voice({}, None, "0.6.0")[0]["state"], "ok")
        self.assertEqual(voice({}, None, "0.10.0")[0].get("old"), {"built": "0.7.0", "floor": "0.10.0"})


class Gate(unittest.TestCase):
    def test_install_gate(self):
        base = dict(folders={"WuxianKit": there("0.7.0", "0.7.0")}, catalog=[KIT_071, GUIDES])
        self.assertEqual(C.install_gate(snap(**base), GUIDES), dict(
            code="needs_kit", unmet=[dict(name="WuxianKit", needs="0.7.1", has="0.7.0")],
            text="WuxianKit_Guides 0.2.0 needs WuxianKit 0.7.1; WuxianKit 0.7.1 needs 无限工坊 0.10.1: update 无限工坊 first"))
        newer_app = dict(base, app=dict(version="0.10.1", latest=None))
        self.assertEqual(C.install_gate(snap(**newer_app), GUIDES)["text"],
                         "WuxianKit_Guides 0.2.0 needs WuxianKit 0.7.1: update WuxianKit to 0.7.1 on the App's "
                         "Extensions (扩展) page")
        self.assertEqual(C.install_gate(snap(**base), KIT_071)["code"], "needs_app")
        self.assertEqual(C.install_gate(snap(**base), KIT_071)["text"], "WuxianKit 0.7.1 needs 无限工坊 0.10.1: update 无限工坊")
        self.assertEqual(C.install_gate(snap(**base), dict(GUIDES, needs={"workshop": "0.11", "WuxianKit": "0.7.1"}))
                         ["code"], "needs_app")                                     # the workshop first
        updated = dict(base, folders={"WuxianKit": there("0.7.1", "0.7.0")})  # the update in place counts
        self.assertIsNone(C.install_gate(snap(**updated), GUIDES))
        self.assertIsNone(C.install_gate(snap(), GUIDES))                     # no WuxianKit: Dependencies' job
        self.assertIsNone(C.install_gate(snap(**base), dict(GUIDES, needs={"EllesmereUI": "9.0"})))
        self.assertEqual(C.install_gate(snap(), dict(GUIDES, needs=None, min_app="0.11.0"))["code"], "needs_app")

    def test_a_development_build_is_not_judged(self):
        """the running or the installed WuxianKit is a development build (no numbers): the gate does not judge, an
        older version beside it does not win; the App's own likewise"""
        for running, installed in (("dev", "0.7.0"), ("0.7.0", "@project-version@"), ("", "0.7.0")):
            got = C.install_gate(snap(folders={"WuxianKit": there(installed, running)}), GUIDES)
            self.assertIsNone(got, (running, installed))
        self.assertEqual(C.gate_has("dev", "0.7.0"), "dev")
        self.assertEqual(C.gate_has(None, "0.7.0", "0.7.1"), "0.7.1")
        self.assertIsNone(C.gate_has(None, None))
        self.assertIsNone(C.install_gate(snap(app=dict(version="dev", latest=None)),
                                         dict(GUIDES, needs={"workshop": "9.0"})))

    def test_state_of(self):
        app = X.app_version()
        entry = dict(id="g", folder="G", version="1.0.0", needs={"WuxianKit": "0.7.1"})
        self.assertEqual(X.state_of(entry, False, None), "available")              # no WuxianKit known: not judged
        self.assertEqual(X.state_of(entry, False, None, "0.7.0"), "needs_kit")
        self.assertEqual(X.state_of(entry, True, "0.9", "0.7.1"), "update")
        self.assertEqual(X.state_of(dict(entry, needs={"workshop": "99.0"}), True, "1.0.0", "0.7.1"), "needs_app")
        self.assertEqual(X.state_of(dict(id="g", folder="G", version="1.0.0", min_app=app), True, "1.0.0"), "current")
        self.assertEqual(X.state_of(dict(id="g", folder="G", version="1.0.0", min_app="99.0"), True, "1.0.0", "0.1"),
                         "needs_app")

    def test_entries_keep_needs_clean(self):
        raw = dict(id="g", folder="G", version="1.0.0", file="G-1.0.0.zip", size=10, sha256="0" * 64)
        got = X.entries({"addons": [dict(raw, needs={"workshop": "0.10", "wuxiankit": "0.7.1", "Bad": "x", "n": 3}),
                                    dict(raw, id="h", needs="workshop 0.10"), dict(raw, id="i")]})
        self.assertEqual([e.get("needs") for e in got], [{"workshop": "0.10.0", "WuxianKit": "0.7.1"}, {}, None])


if __name__ == "__main__":
    unittest.main()
