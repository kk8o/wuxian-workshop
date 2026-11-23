"""The MCP tools an agent takes a version step with: versions_wait (it waits while the player clicks, on status's
versions, also while the App restarts for its own update) and extensions (the catalog as the App's Extensions page
shows it, with why an install would be refused), and what the server's texts say of versions: the instructions,
status, run / call, doctor, new_extension, extension_install, extension_wait, extension_summary. Stand-in backends:
no daemon, no game."""
import asyncio
import unittest
from unittest import mock

from mcp.server.mcpserver.exceptions import ToolError

from wuxianworkshop import compat
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.mcp import kit, server


def report(state, iid="wuxiankit", text="update WuxianKit to 0.10.1 on the App's Extensions (扩展) page"):
    """a status.versions with one item besides the App's (ok)"""
    items = [dict(id="workshop", kind="workshop", state="ok"), dict(id=iid, kind="kit", state=state, text=text)]
    nxt = dict(severity=state, action="update", id=iid, target=iid, text=text) if state in compat.SEVERITY else None
    return dict(next=nxt, items=items)


class Backend:
    """status answers the reports in turn (the last one for ever); an ApiError in the list is raised"""

    def __init__(self, *reports):
        self.reports, self.passive = list(reports), []

    async def status(self):
        self.passive.append(kit.PASSIVE.get())
        got = self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]
        if isinstance(got, Exception):
            raise got
        return dict(daemon=dict(version="0.10.1"), versions=got)

    def __getattr__(self, name):                     # what the kit tools' watch asks: nothing there
        async def method(*args, **kw):
            return {}
        return method


def tools(backend):
    mcp = server.build_server(backend)
    return mcp, {t.name: t for t in asyncio.run(mcp.list_tools())}


def call(mcp, name, args=None):
    return asyncio.run(mcp.call_tool(name, args or {})).structured_content


class VersionsWait(unittest.TestCase):
    def setUp(self):
        every = mock.patch.object(server, "VERSION_EVERY", 0.01)
        every.start()
        self.addCleanup(every.stop)

    def test_the_tool(self):
        _, found = tools(Backend(report("ok")))
        tool = found["versions_wait"]
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertEqual(sorted(tool.input_schema["properties"]), ["id", "timeout"])
        self.assertEqual(tool.input_schema.get("required", []), [])
        said = " ".join(tool.description.split())
        for words in ("tell the player status's versions.next.text", "the player clicks, never you",
                      "{done, item, next}", "600 at most", "restart_agent"):
            self.assertIn(words, said)

    def test_it_waits_for_next(self):
        """no id: next's; it answers once that item is no longer blocked / pending / update, with the next step"""
        b = Backend(report("update"), report("update"), report("ok"))
        mcp, _ = tools(b)
        got = call(mcp, "versions_wait", {"timeout": 5})
        self.assertEqual((got["done"], got["item"]["id"], got["item"]["state"], got["next"]), (True, "wuxiankit", "ok", None))
        self.assertEqual(len(b.passive), 3)
        self.assertTrue(all(b.passive))              # a daemon gone meanwhile is not started from here

    def test_through_the_apps_restart(self):
        """the App restarts for its update: the daemon is away for a while, the wait goes on"""
        gone = ApiError(503, "daemon_unreachable", "the daemon is not running")
        mcp, _ = tools(Backend(report("update", "workshop"), gone, gone, report("ok", "workshop")))
        got = call(mcp, "versions_wait", {"id": "workshop", "timeout": 5})
        self.assertEqual((got["done"], got["item"]["state"]), (True, "ok"))

    def test_a_timeout(self):
        mcp, _ = tools(Backend(report("blocked")))
        got = call(mcp, "versions_wait", {"timeout": 0.05})
        self.assertEqual((got["done"], got["timeout"], got["item"]["state"], got["next"]["severity"]),
                         (False, True, "blocked", "blocked"))

    def test_nothing_to_wait_for(self):
        mcp, _ = tools(Backend(report("ok")))
        self.assertEqual(call(mcp, "versions_wait", {}), dict(done=True, item=None, next=None))

    def test_an_item_that_is_not_there(self):
        mcp, _ = tools(Backend(report("update")))
        with self.assertRaises(ToolError) as cm:
            call(mcp, "versions_wait", {"id": "nope"})
        self.assertIn("not_found", str(cm.exception))
        self.assertIn("wuxiankit", str(cm.exception))

    def test_another_failure_is_said(self):
        mcp, _ = tools(Backend(ApiError(500, "boom", "it broke")))
        with self.assertRaises(ToolError) as cm:
            call(mcp, "versions_wait", {})
        self.assertIn("boom: it broke", str(cm.exception))


LISTING = dict(
    catalog=dict(url="x", updated="2026-11-24", checked=1, error=None), app="0.10.1", addons_dir="C:/g/AddOns",
    addons=[
        dict(id="wuxiankit", folder="WuxianKit", version="0.11.0", title={"zh": "扩展框架", "en": "WuxianKit"},
             summary={"zh": "框架", "en": "the framework"}, file="WuxianKit-0.11.0.zip", size=1, sha256="0" * 64,
             there=True, installed="0.10.0", state="update", missing=[]),
        dict(id="fx", folder="WuxianFx", version="1.0.0", title="特效", needs={"WuxianKit": "0.11.0"}, there=False,
             installed=None, state="needs_kit", missing=[]),
        dict(id="later", folder="WuxianLater", version="2.0.0", title="以后", needs={"workshop": "99.0.0"},
             min_app="99.0.0", there=False, installed=None, state="needs_app", missing=[]),
        dict(id="told", folder="WuxianTold", version="1.0.0", title="说过", needs={"workshop": "99.0.0"}, there=False,
             installed=None, state="needs_app", missing=[], gate=dict(code="needs_app", text="the daemon's own words",
                                                                      unmet=[])),
        dict(id="guide", folder="WuxianGuide", version="1.0.0", title="指引", there=False, installed=None,
             state="available", missing=["EllesmereUI"])],
    detected=[dict(id="eui", title="EllesmereUI 适配", version="0.3.1", official=False,
                   game=[dict(folder="WuxianKit_EllesmereUI")], local=None)])


class Catalog(unittest.TestCase):
    class Lists(Backend):
        def __init__(self):
            super().__init__(report("ok"))
            self.asked = 0

        async def extensions(self):
            self.asked += 1
            return LISTING

    def test_the_tool(self):
        b = self.Lists()
        mcp, found = tools(b)
        tool = found["extensions"]
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertEqual(tool.input_schema.get("properties"), {})
        said = " ".join(tool.description.split())
        for words in ("what can I add to the game", "why can't I add this", "needs_app", "needs_kit", "refused",
                      "安装 / 更新 (Install / Update)", "全部更新 (Update all)", "never do it for them"):
            self.assertIn(words, said)
        got = call(mcp, "extensions")
        rows = {r["id"]: r for r in got["addons"]}
        self.assertEqual(list(rows), ["wuxiankit", "fx", "later", "told", "guide"])
        self.assertEqual(rows["wuxiankit"], dict(id="wuxiankit", title="扩展框架", summary="框架", folder="WuxianKit",
                                                 version="0.11.0", installed="0.10.0", state="update", missing=[]))
        self.assertEqual((rows["fx"]["refused"]["code"], rows["later"]["refused"]["code"]), ("needs_kit", "needs_app"))
        self.assertIn("needs WuxianKit 0.11.0", rows["fx"]["refused"]["text"])       # the judge's sentence
        self.assertIn("WuxianKit", rows["fx"]["refused"]["text"].split(":", 1)[1])   # and what to update
        self.assertIn("needs 无限工坊 99.0.0", rows["later"]["refused"]["text"])
        self.assertEqual(rows["told"]["refused"], dict(code="needs_app", text="the daemon's own words"))
        self.assertNotIn("refused", rows["guide"])
        self.assertEqual(rows["guide"]["missing"], ["EllesmereUI"])
        self.assertEqual(got["detected"], [dict(id="eui", title="EllesmereUI 适配", version="0.3.1",
                                                folder="WuxianKit_EllesmereUI", official=False)])
        self.assertEqual((got["app"], got["catalog"]["updated"]), ("0.10.1", "2026-11-24"))

    def test_the_daemons_own_verdict(self):
        """in the daemon (the /mcp it mounts) the Service's install_gate judges: it knows the WuxianKit that runs"""
        b = self.Lists()
        asked = []

        def install_gate(entry):
            asked.append(entry["id"])
            return dict(code="needs_kit", text=f"{entry['id']}: the Service's", unmet=[])
        b.install_gate = install_gate
        mcp, _ = tools(b)
        rows = {r["id"]: r for r in call(mcp, "extensions")["addons"]}
        self.assertEqual(asked, ["fx", "later"])                  # told: the listing's own gate stands
        self.assertEqual(rows["fx"]["refused"]["text"], "fx: the Service's")

    def test_over_http(self):
        class Client:
            def __init__(self):
                self.asked = []

            def get(self, path, **params):
                self.asked.append(path)
                return LISTING
        client = Client()
        self.assertEqual(asyncio.run(server.HttpBackend(client).extensions()), LISTING)
        self.assertEqual(client.asked, ["/api/extensions"])


class Texts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, found = tools(Backend(report("ok")))
        cls.said = {name: " ".join(t.description.split()) for name, t in found.items()}

    def test_the_instructions(self):
        self.assertLessEqual(len(server.INSTRUCTIONS), 2000)
        self.assertIn("If versions.next is blocked or pending, tell the user its text (mcp.stale: mcp.action).",
                      server.INSTRUCTIONS)
        self.assertNotIn("If mcp.stale is true", server.INSTRUCTIONS)

    def test_what_the_tools_say(self):
        for name, words in {
            "status": ("update: this App's own update", "available", "ready", "unsupported", "addons_update",
                       "kit.outdated", "versions.items lists only what is not ok", "never run `wuxian update apply`"),
            "run": ("addon_outdated", "tell the user that sentence"),
            "call": ("addon_outdated", "tell the user that sentence"),
            "doctor": ("install, install_clean or create_mail: the tool `install`", "修复 (Fix)",
                       "自检与修复 (Check & Repair)", "install_webview2 or install_dotnet: only the player",
                       "null: nothing repairs it from here"),
            "new_extension": ("workshop 0.<generation>.0", "WuxianKit 0.10.0", "## X-Wuxian-Requires",
                              'Kit:Requires("0.10.0")', "raise those together"),
            "extension_install": ("requires_unmet",),
            "extension_wait": ("update (WuxianKit did not load its addon", "outdated (not loaded for its version",
                               "versions_wait"),
            "extension_summary": ("requires ({workshop?, kit?}", "requires (its .toc's X-Wuxian-Requires"),
        }.items():
            for w in words:
                self.assertIn(w, self.said[name], f"{name}: {w}")


if __name__ == "__main__":
    unittest.main()
