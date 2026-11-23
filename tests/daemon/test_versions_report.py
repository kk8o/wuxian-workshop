"""status's versions (compat.judge over Service.versions_snapshot): what the game runs against the .toc files in AddOns
(WoWBridge's HELLO, WuxianKit's kit.ready, an extension addon's kit row `toc` of WuxianKit 0.10.0 on), the kit rows' needs
and unmet, the local extensions' requires and their addons' X-Wuxian-Requires, the agents' MCP servers; and the MCP
status tool, which gives an agent next and what is not ok (a stdio server that knows itself stale puts itself in).
A Service with a stand-in loop (no game, no daemon); the AddOns folder is a temporary one."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.daemon.test_standby_daemon import Worker
from wuxianworkshop import __version__, compat
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon import service
from wuxianworkshop.daemon.service import KIT_NEEDS, Service
from wuxianworkshop.mcp import server


def toc(folder, version, requires=None, extra=""):
    folder.mkdir(parents=True, exist_ok=True)
    text = f"## Interface: 16001\n## Title: {folder.name}\n## Version: {version}\n{extra}"
    if requires:
        text += f"## X-Wuxian-Requires: {requires}\n"
    (folder / f"{folder.name}.toc").write_text(text, encoding="utf-8")


class Versions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(Path(tmp.name) / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.addons = Path(tmp.name) / "AddOns"
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.svc.worker.addons = self.addons
        self.hello(__version__)                          # the WoWBridge this App brings

    def hello(self, version):
        self.svc.worker.comp = SimpleNamespace(current=5, sessions={5: SimpleNamespace(version_text=version)})

    def items(self, **kw):
        return {i["id"]: i for i in self.svc.versions(**kw)["items"]}

    def hint(self, item="wowbridge"):
        return asyncio.run(self.svc.version_hint(item))

    def test_running_against_installed(self):
        """WoWBridge and WuxianKit: pending while the game runs another version than the .toc says; the WuxianKit
        this game process runs is judged against what the App needs of it (KIT_NEEDS)"""
        toc(self.addons / "WoWBridge", "99.0.1")                    # newer than any App: the .toc decides
        self.hello("99.0.0")
        toc(self.addons / "WuxianKit", "0.7.1", "workshop 0.10.0")
        self.svc.kit_seen = "0.7.0"
        items = self.items()
        self.assertEqual((items["wowbridge"]["running"], items["wowbridge"]["installed"], items["wowbridge"]["state"]),
                         ("99.0.0", "99.0.1", "pending"))
        self.assertEqual((items["wuxiankit"]["running"], items["wuxiankit"]["installed"], items["wuxiankit"]["state"],
                          items["wuxiankit"]["needs"]), ("0.7.0", "0.7.1", "pending", {"workshop": "0.10.0"}))
        self.assertEqual(self.hint(), f"WoWBridge 99.0.1 is installed but the game still runs 99.0.0: {compat.RESTART}")
        self.svc.kit_seen = "0.6.1"
        self.assertEqual(self.items()["workshop"]["unmet"], [dict(name="WuxianKit", needs=KIT_NEEDS, has="0.6.1")])
        self.hello("99.0.1")
        self.assertIsNone(self.hint())

    def test_an_old_bridge_after_an_app_update(self):
        """the App was updated while the game runs: WoWBridge.toc on disk and the HELLO still say the old version (the
        App puts its own in place once the game is closed): pending, and the hint an error ends with says so"""
        toc(self.addons / "WoWBridge", "0.9.14")
        self.hello("0.9.14")
        bridge = self.items()["wowbridge"]
        self.assertEqual((bridge["installed"], bridge["running"], bridge["state"], bridge["action"]),
                         ("0.9.14", "0.9.14", "pending", "restart_game"))
        app = ".".join(str(n) for n in compat.has_tuple(__version__))
        self.assertEqual(self.hint(), f"无限工坊 {app} brings WoWBridge {app} but the game still runs 0.9.14: "
                                      f"{compat.CLOSE}")
        self.assertEqual(compat.hinted("call failed", self.hint()), f"call failed. {self.hint()}")
        toc(self.addons / "WoWBridge", app)                                 # put in place, the game not restarted yet
        self.assertEqual(self.hint(), f"WoWBridge {app} is installed but the game still runs 0.9.14: {compat.RESTART}")
        self.hello(app)
        self.assertIsNone(self.hint())

    def test_the_kits_rows(self):
        """the extension addons of the manifest of the WuxianKit this game process runs (0.10.0 on: toc, needs, unmet);
        a manifest of another WuxianKit (an older process's, kept on disk) says nothing of what runs"""
        toc(self.addons / "WuxianKit", "0.7.1")
        toc(self.addons / "Foo", "1.1")
        toc(self.addons / "Bar", "2.0", "WuxianKit 0.7.1, EllesmereUI 9.0")
        toc(self.addons / "EllesmereUI", "9.1")
        manifest = {"kit": "0.7.1", "extensions": [
            {"id": "kit", "addon": "WuxianKit", "state": "on"},
            {"id": "foo", "addon": "Foo", "state": "on", "toc": "1.0"},
            {"id": "bar", "addon": "Bar", "state": "outdated", "toc": "2.0", "needs": {"EllesmereUI": "9.0"},
             "unmet": [{"name": "EllesmereUI", "needs": "9.0", "has": "8.0"}]}]}
        self.svc.kit_seen = "0.7.1"
        with mock.patch.object(self.svc, "kit_manifest_known", lambda: manifest):
            items = self.items()
            self.assertEqual((items["Foo"]["kind"], items["Foo"]["running"], items["Foo"]["installed"],
                              items["Foo"]["state"]), ("addon", "1.0", "1.1", "pending"))
            self.assertEqual((items["Bar"]["state"], items["Bar"]["unmet"]),        # the game read EllesmereUI 8.0
                             ("blocked", [dict(name="EllesmereUI", needs="9.0.0", has="8.0")]))
            self.svc.kit_seen = "0.7.0"
            self.assertNotIn("Foo", self.items())

    def test_a_gated_row_that_is_loaded(self):
        """WuxianKit 0.10.0 keeps a loaded extension it gates on (state on, gate outdated / too_old): what the report
        reads is the row's toc, needs and unmet, never its state"""
        toc(self.addons / "WuxianKit", "0.10.0")
        toc(self.addons / "Gated", "1.0", "Other 2.0", extra="## X-WuxianKit-Extension: gated\n")
        toc(self.addons / "Other", "1.0")
        manifest = {"kit": "0.10.0", "extensions": [
            {"id": "gated", "addon": "Gated", "state": "on", "gate": "outdated", "toc": "1.0",
             "needs": {"Other": "2.0"}, "unmet": [{"name": "Other", "needs": "2.0", "has": "1.0"}]}]}
        self.svc.kit_seen = "0.10.0"
        with mock.patch.object(self.svc, "kit_manifest_known", lambda: manifest):
            gated = self.items()["Gated"]
        self.assertEqual((gated["state"], gated["unmet"]), ("blocked", [dict(name="Other", needs="2.0.0", has="1.0")]))

    def test_the_floor(self):
        """WuxianKit's floor: its .toc's, or the running WuxianKit's manifest's; an extension addon made for an older
        WuxianKit is blocked with old"""
        toc(self.addons / "WuxianKit", "0.7.1", extra="## X-WuxianKit-Floor: 0.6.0\n")
        toc(self.addons / "Old", "1.0", "WuxianKit 0.5.0", extra="## X-WuxianKit-Extension: old\n")
        toc(self.addons / "Plain", "1.0", "WuxianKit 0.5.0")              # no extension: not judged
        self.assertEqual(self.svc.versions_snapshot()["folders"]["WuxianKit"]["floor"], "0.6.0")
        with mock.patch.object(self.svc, "versions_local_rows", lambda: [
                dict(id="plain", title="Plain", version="1.0", requires={}, addons=["Plain"])]):
            report = self.svc.versions()
        items = {i["id"]: i for i in report["items"]}
        self.assertNotIn("Old", items)                                    # neither catalog nor reported: no item
        self.assertEqual((items["plain"]["state"], items["plain"]["old"]), ("blocked", {"built": "0.5.0", "floor": "0.6.0"}))
        manifest = {"kit": "0.7.1", "floor": "0.5", "extensions": [{"id": "old", "addon": "Old", "state": "on", "toc": "1.0"}]}
        self.svc.kit_seen = "0.7.1"
        with mock.patch.object(self.svc, "kit_manifest_known", lambda: manifest):
            self.assertEqual(self.svc.versions_snapshot()["folders"]["WuxianKit"]["floor"], "0.5.0")
            self.assertEqual(self.items()["Old"]["state"], "ok")
            manifest["floor"] = "0.6"
            old = self.items()["Old"]
            self.assertEqual((old["state"], old["old"], old["action"]), ("blocked", {"built": "0.5.0", "floor": "0.6.0"},
                                                                         "none"))
            self.assertEqual(self.svc.versions()["next"]["text"], "Old was made for WuxianKit 0.5.0; WuxianKit 0.7.1 runs "
                                                                  "extensions made for 0.6.0 or later: update Old")
            del manifest["floor"]                                        # the running WuxianKit has none
            self.assertIsNone(self.svc.versions_snapshot()["folders"]["WuxianKit"]["floor"])

    def test_local_extensions(self):
        """a local extension: requires.kit not met makes it blocked (it still starts), its addons' X-Wuxian-Requires
        are judged with it and are no items of their own"""
        toc(self.addons / "WuxianKit", "0.7.0")
        toc(self.addons / "WuxianVoice", "1.0", "workshop 99.0")
        rows = [dict(id="voice", title="语音", version="1.0", requires={"kit": "0.7.1"}, addons=["WuxianVoice"])]
        with mock.patch.object(self.svc, "versions_local_rows", lambda: rows):
            report = self.svc.versions()
        items = {i["id"]: i for i in report["items"]}
        self.assertNotIn("WuxianVoice", items)
        self.assertEqual((items["voice"]["kind"], items["voice"]["state"], items["voice"]["needs"]),
                         ("local", "blocked", {"workshop": "99.0.0", "WuxianKit": "0.7.1"}))
        self.assertEqual(report["next"]["text"], "语音 needs 无限工坊 99.0.0: update 无限工坊")

    def test_agents(self):
        """the stdio MCP servers that reported: one that runs an older program is stale (restart_agent)"""
        self.assertEqual(self.items()["mcp"]["state"], "unknown")
        self.svc.mcp_reports[1] = dict(pid=1, version=__version__, agents=[{}], source_changed=False, seen=time.time())
        self.assertEqual(self.items()["mcp"]["state"], "ok")
        self.svc.mcp_reports[2] = dict(pid=2, version="0.0.1", agents=[], source_changed=False, seen=time.time())
        report = self.svc.versions()
        self.assertEqual((report["next"]["action"], report["next"]["id"]), ("restart_agent", "mcp"))
        self.svc.mcp_reports[2]["seen"] = time.time() - 600                    # gone: not counted
        self.assertEqual(self.items()["mcp"]["state"], "ok")

    def test_never_a_failure(self):
        """a failure is an empty report, said in the journal once (status asks every few seconds), again once it
        changed or after a success"""
        def said():
            return [e for e in self.svc.journal.since(0, 1000)[0] if "versions:" in e["text"]]
        with mock.patch.object(compat, "judge", side_effect=ValueError("boom")):
            for _ in range(3):
                self.assertEqual(self.svc.versions(), dict(next=None, items=[]))
        self.assertEqual(len(said()), 1)
        with mock.patch.object(compat, "judge", side_effect=ValueError("bang")):
            self.svc.versions()
        self.assertEqual(len(said()), 2)
        self.svc.versions()
        with mock.patch.object(compat, "judge", side_effect=ValueError("bang")):
            self.svc.versions()
        self.assertEqual(len(said()), 3)

    def test_the_local_rows_are_kept(self):
        """the local extensions' manifests are read lightly (Host.version_rows, not Host.rows), kept till a change of
        theirs (local_changed) or LOCAL_KEPT s"""
        reads = []

        def version_rows():
            reads.append(1)
            return [dict(id="voice", title="语音", version=f"1.{len(reads)}", requires={}, addons=[])]
        with mock.patch.object(self.svc.local, "version_rows", version_rows), \
                mock.patch.object(self.svc.local, "rows", side_effect=AssertionError("the full rows")):
            self.assertEqual(self.items()["voice"]["installed"], "1.1")
            self.assertEqual(self.items()["voice"]["installed"], "1.1")
            self.assertEqual(len(reads), 1)
            with mock.patch.object(self.svc, "soon", lambda coro: coro.close()), \
                    mock.patch.object(self.svc, "roster_again"):
                self.svc.local_changed(7)
            self.assertEqual(self.items()["voice"]["installed"], "1.2")
            self.svc.versions_local = (time.monotonic() - service.LOCAL_KEPT - 1, self.svc.versions_local[1])
            self.assertEqual(self.items()["voice"]["installed"], "1.3")

    def test_the_hosts_version_rows(self):
        """Host.version_rows: id, title, version, requires, addons and drives (an app with driver "local") of each
        manifest; one that cannot be read keeps its id"""
        from tests.local.util import make_extension
        folder = make_extension(Path(self.addons).parent / "ext", "voice")
        self.svc.local.add(str(folder))
        driver = make_extension(Path(self.addons).parent / "drv", "drv", apps=[
            {"app": "cocreate", "title": "数据共创", "driver": "local", "hear": ["cocreate:snapshot"]}])
        self.svc.local.add(str(driver))
        rows = self.svc.local.version_rows()
        self.assertEqual([(r["id"], r["version"], r["addons"], r["drives"]) for r in rows],
                         [("voice", "0.1.0", [], False), ("drv", "0.1.0", [], True)])
        (folder / "wuxian-extension.json").write_text("{", encoding="utf-8")
        self.assertEqual(self.svc.local.version_rows()[0], dict(id="voice", title="voice", version=None, requires={},
                                                                addons=[], drives=False))

    def test_the_catalog_without_the_page(self):
        """(I-A) the catalog kept on disk is known from start(), and the daemon reads the catalog itself in the
        background (catalog_check: not again within half of CATALOG_AGE of a read); status's versions read the catalog
        in memory only, never the website"""
        from wuxianworkshop import extensions
        toc(self.addons / "WuxianKit", "0.10.0")
        self.svc.kit_seen = "0.10.0"
        store = extensions.catalog_store()
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text('{"addons": [{"id": "wuxiankit", "folder": "WuxianKit", "version": "0.10.1"}]}',
                         encoding="utf-8")
        self.assertEqual(self.items()["wuxiankit"]["state"], "ok")                  # nothing read yet

        def start(svc):
            """svc.start() with nothing of it running: [the names of the Schedulers it made]"""
            svc.worker.run = lambda: None
            with mock.patch.object(service, "Scheduler") as scheduler, mock.patch.object(svc.local, "autostart"), \
                    mock.patch.object(service.content, "ContentUpdater"), \
                    mock.patch.object(svc, "updater", SimpleNamespace(supported=False, status=lambda: {})), \
                    mock.patch.object(svc.ext_catalog, "refresh", side_effect=AssertionError("the website")):
                svc.start()
                svc.thread.join(1)
            return {c.kwargs.get("name"): c for c in scheduler.call_args_list}
        self.assertFalse(self.svc.read_catalog)                                     # (not the real daemon)
        self.assertNotIn("wuxian-catalog", start(self.svc))
        items = self.items()
        self.assertEqual((items["wuxiankit"]["state"], items["wuxiankit"]["latest"]), ("update", "0.10.1"))
        self.assertIn("update WuxianKit to 0.10.1", items["wuxiankit"]["text"])
        reads = []
        self.svc.ext_catalog.refresh = lambda: reads.append(1) or setattr(self.svc.ext_catalog, "checked", time.time())
        self.svc.catalog_check()
        self.svc.catalog_check()                                                    # just read: not again
        self.assertEqual(len(reads), 1)
        self.svc.ext_catalog.checked = time.time() - self.svc.CATALOG_AGE
        self.svc.catalog_check()
        self.assertEqual(len(reads), 2)
        self.assertFalse(Service(lambda on_debug, log: Worker(), journal=Journal(), sync_addons=True,
                                 read_catalog=False).read_catalog)
        real = Service(lambda on_debug, log: Worker(), journal=Journal(), sync_addons=True)
        self.assertTrue(real.read_catalog)
        made = start(real)["wuxian-catalog"]
        self.assertEqual((made.args[0], made.kwargs["first"], made.kwargs["every"]),
                         (real.catalog_check, service.CATALOG_FIRST, Service.CATALOG_AGE))

    def test_the_update_state_in_the_snapshot(self):
        """update_app's sentence follows the updater's state (versions_snapshot's app.update)"""
        self.svc.updater = SimpleNamespace(status=lambda: dict(state="ready", latest="99.0.0"))
        report = self.svc.versions()
        self.assertEqual(report["items"][0]["id"], "workshop")
        self.assertIn("重启并更新 (Restart and update)", report["items"][0]["text"])

    def test_a_local_extensions_verdict(self):
        """local_verdict: the text of a local extension's item when it is not fine (the host's player update), else
        None"""
        toc(self.addons / "WuxianKit", "0.10.0")
        self.svc.kit_seen = "0.10.0"
        rows = [dict(id="voice", title="语音", version="1.0", requires={"kit": "0.11.0"}, addons=[])]
        with mock.patch.object(self.svc, "versions_local_rows", lambda: rows):
            self.assertTrue(self.svc.local_verdict("voice").startswith("语音 needs WuxianKit 0.11.0"))
            rows[0]["requires"] = {"kit": "0.10.0"}
            self.assertIsNone(self.svc.local_verdict("voice"))
            self.assertIsNone(self.svc.local_verdict("nobody"))


class McpStatus(unittest.TestCase):
    """the status tool: versions as next and the items that are not ok; on stdio a stale server puts itself in"""

    REPORT = compat.judge(dict(app=dict(version="0.10.0", latest="0.10.1"),
                               folders={"WoWBridge": dict(there=True, installed="0.10.0", running="0.10.0")},
                               agents=dict(seen=1, stale=0)))

    class Backend:
        def __init__(self, version):
            self.version = version

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)

            async def method(*args):
                return {}
            return method

        async def status(self):
            return dict(daemon=dict(version=self.version), versions=McpStatus.REPORT)

    class Client:
        def __init__(self, version):
            self.version = version

        def status(self):
            return dict(daemon=dict(version=self.version), versions=McpStatus.REPORT)

    def call(self, mcp):
        return asyncio.run(mcp.call_tool("status", {})).structured_content

    def test_the_tool(self):
        mcp = server.build_server(self.Backend(__version__))
        tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "status")
        said = " ".join(tool.description.split())
        for words in ('Severity "blocked" or "pending": tell the user versions.next.text', "then wait with versions_wait",
                      "once per session", "versions.items lists only what is not ok", "each with its own `text`",
                      "addons_update", "kit.outdated", "never run `wuxian update apply`", "重启并更新 (Restart and update)"):
            self.assertIn(words, said)
        got = self.call(mcp)["versions"]
        self.assertEqual(got["next"]["action"], "update_app")
        self.assertEqual([i["id"] for i in got["items"]], ["workshop"])            # what is ok is left out
        stdio = self.call(server.build_server(server.HttpBackend(self.Client("999.0.0"))))
        self.assertTrue(stdio["mcp"]["stale"])
        self.assertEqual((stdio["versions"]["next"]["action"], stdio["versions"]["next"]["text"]),
                         ("restart_agent", stdio["mcp"]["action"]))
        self.assertEqual([(i["id"], i["state"]) for i in stdio["versions"]["items"]],
                         [("workshop", "update"), ("mcp", "pending")])

    def test_another_agents_server(self):
        """the report has another agent's MCP server stale: the App page shows it, this agent is not told (neither the
        item nor next) unless its own server is the stale one"""
        report = compat.judge(dict(app=dict(version="0.10.0", latest="0.10.1"), agents=dict(seen=2, stale=1)))
        self.assertEqual(report["next"]["action"], "restart_agent")
        with mock.patch.object(McpStatus, "REPORT", report):
            here = self.call(server.build_server(self.Backend(__version__)))["versions"]
            self.assertEqual((here["next"]["action"], [i["id"] for i in here["items"]]), ("update_app", ["workshop"]))
            fresh = self.call(server.build_server(server.HttpBackend(self.Client(__version__))))
            self.assertEqual([i["id"] for i in fresh["versions"]["items"]], ["workshop"])
            stale = self.call(server.build_server(server.HttpBackend(self.Client("999.0.0"))))
            self.assertEqual((stale["versions"]["next"]["text"], [i["id"] for i in stale["versions"]["items"]]),
                             (stale["mcp"]["action"], ["workshop", "mcp"]))


if __name__ == "__main__":
    unittest.main()
