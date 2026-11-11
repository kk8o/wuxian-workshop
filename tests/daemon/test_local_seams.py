"""The daemon's side of the local extensions' seams over a real daemon (tests/daemon/test_local_api.py's LocalDaemon):
WuxianKit's local apps reconciled (forgotten when no enabled extension declares them, at once or at the next kit.ready),
the reactions an app's manifest declares set while it runs, /api/local/approve, a call's deadline and its cancelling
(the time over, the caller gone, the client hung up), the caller chain and its loop, and the protocol's reasons as the
error codes. The host is the real one (an app's reactions come from its manifest); Host.call, Host.cancel_call and
Host.approve are watched, and patched only for the answers a fixture cannot give (busy, cancelled, a reaction that
names no capability, an extension that cannot run)."""
import asyncio
import contextlib
import http.client
import json
import time
import unittest
import urllib.parse
from concurrent.futures import Future
from unittest import mock

from tests.daemon.test_local_api import LocalDaemon, with_game
from tests.local.util import until
from wuxianworkshop import extensions as X
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local.rpc import RpcError

APP = {"app": "companion", "title": "语音搭子", "prompt": "companion"}
REACTING = dict(APP, reactions=[{"hear": "fx:said", "call": "fx.echo", "args": {"text": "听到"}, "cooldown": 0},
                                {"hear": "game:combat", "call": "fx.echo", "note": "a pull"}])


def failed(error):
    fut = Future()
    fut.set_exception(error)
    return fut


class LocalSeams(LocalDaemon, unittest.TestCase):
    LETTER = "Q"

    def reaction(self, rid):
        return next((r for r in self.ok("GET", "/api/reactions")["reactions"] if r["id"] == rid), None)

    @contextlib.contextmanager
    def watched(self):
        """Host.call and Host.cancel_call as they are, and what they were asked: (calls, cancels)"""
        host, calls, cancels = self.svc.local, [], []
        call, cancel = host.call, host.cancel_call

        def call_(proc, name, args, caller, deadline_ms=None):
            fut = call(proc, name, args, caller, deadline_ms=deadline_ms)
            calls.append(dict(name=name, caller=caller, deadline_ms=deadline_ms, fut=fut))
            return fut

        def cancel_(proc, fut, ping=False):
            cancels.append(dict(fut=fut, ping=ping))
            return cancel(proc, fut, ping=ping)
        with mock.patch.object(host, "call", call_), mock.patch.object(host, "cancel_call", cancel_):
            yield calls, cancels

    # --- the apps of the local extensions in WuxianKit

    def test_a_local_app_nobody_declares_is_forgotten(self):
        self.kit.apps["old"] = {"app": "old", "title": "旧", "local": {"id": "gone", "title": "走了"}}
        self.kit.apps["theirs"] = {"app": "theirs", "title": "别人的"}       # an agent's: not ours to forget
        self.ok("POST", "/api/reactions/set", {"id": "o", "owner": "old", "hear": "game:quest", "call": "sense.bags"})
        self.kit.emit("kit.ready", {})
        self.assertTrue(until(lambda: self.kit.asked("kit.app.forget")), self.kit.calls)
        self.assertEqual(self.kit.asked("kit.app.forget"), [{"app": "old"}])
        self.assertEqual(list(self.kit.apps), ["theirs"])
        self.assertTrue(until(lambda: self.reaction("o") is None))             # its reactions go with it
        self.assertEqual(self.kit.asked("kit.app.declare"), [])

    def test_one_that_needs_a_newer_workshop_declares_nothing(self):
        self.add(apps=[REACTING], requires={"workshop": "99.0.0"})
        self.assertIn("99.0.0", self.fails("POST", "/api/local/load", {"id": "fx"}, 409, "requires_unmet"))
        self.fails("POST", "/api/local/install", {"id": "fx"}, 409, "requires_unmet")
        self.kit.emit("kit.ready", {})
        time.sleep(0.3)
        self.assertEqual((self.kit.asked("kit.app.declare"), self.kit.apps), ([], {}))    # nothing of it is there
        self.assertEqual(self.ok("GET", "/api/reactions")["reactions"], [])

    def test_turned_off_while_the_game_is_away(self):
        self.add(apps=[APP])
        self.assertEqual(list(self.kit.apps), ["companion"])
        self.kit.calls.clear()
        with mock.patch.object(self.svc, "link_is_up", return_value=False):
            self.ok("POST", "/api/local/set", {"id": "fx", "enabled": False})
        self.assertEqual(self.kit.calls, [])                                   # nobody to tell
        self.kit.emit("kit.ready", {})                                         # the game is back
        self.assertTrue(until(lambda: self.kit.asked("kit.app.forget")), self.kit.calls)
        self.assertEqual(self.kit.asked("kit.app.forget"), [{"app": "companion"}])
        self.assertEqual(self.kit.apps, {})

    def test_turned_off_while_the_game_was_covered(self):
        """the link back up (no kit.ready: the same UI session): what changed meanwhile is told then"""
        self.add(apps=[APP])
        self.kit.calls.clear()
        with mock.patch.object(self.svc, "link_is_up", return_value=False):
            self.ok("POST", "/api/local/set", {"id": "fx", "enabled": False})
            self.ok("POST", "/api/reactions/set", {"id": "g", "hear": "game:quest", "call": "sense.bags"})
        self.assertEqual(self.kit.calls, [])
        self.assertTrue(self.svc.reactions_owed)
        self.svc.link_came_up(5)
        self.assertTrue(until(lambda: self.kit.asked("kit.app.forget") and self.kit.asked("kit.app.open")), self.kit.calls)
        self.assertEqual(self.kit.asked("kit.app.forget"), [{"app": "companion"}])
        self.assertEqual(self.kit.asked("kit.app.open")[-1]["temp"]["hear"], ["game:quest"])
        self.assertFalse(self.svc.reactions_owed)
        self.kit.calls.clear()
        self.svc.link_came_up(5)                                               # nothing owed: nothing opened again
        self.assertTrue(until(lambda: self.kit.asked("kit.apps")))
        time.sleep(0.2)
        self.assertEqual(self.kit.asked("kit.app.open"), [])

    def test_a_changed_manifest_is_declared_when_it_is_loaded(self):
        _, folder = self.add(apps=[APP])
        file = folder / "wuxian-extension.json"
        manifest = json.loads(file.read_text(encoding="utf-8"))
        manifest["apps"][0]["title"] = "新名字"
        file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        self.kit.calls.clear()
        self.ok("POST", "/api/local/load", {"id": "fx"})
        self.assertEqual([d["title"] for d in self.kit.asked("kit.app.declare")], ["新名字"])
        self.kit.calls.clear()
        self.ok("POST", "/api/local/load", {"id": "fx"})                       # the same again: compared, not declared
        self.assertEqual(self.kit.asked("kit.app.describe"), [{"app": "companion"}])
        self.assertEqual(self.kit.asked("kit.app.declare"), [])

    # --- the reactions an app declares

    def test_an_apps_reactions_are_there_while_it_runs(self):
        self.add(apps=[REACTING])
        self.kit.emit("kit.app", {"app": "companion", "on": True})
        self.assertTrue(until(lambda: self.reaction("companion.2")), self.ok("GET", "/api/reactions"))
        first, second = self.reaction("companion.1"), self.reaction("companion.2")
        self.assertEqual((first["owner"], first["hear"], first["call"], first["args"]),
                         ("companion", "fx:said", "fx.echo", {"text": "听到"}))
        self.assertEqual((second["owner"], second["hear"], second["note"]), ("companion", "game:combat", "a pull"))
        self.assertEqual(self.ok("GET", "/api/local")["extensions"][0]["state"], "running")   # started to describe
        self.assertTrue(until(lambda: self.kit.asked("kit.app.open")))     # the game source: the temporary app
        self.assertEqual(self.kit.asked("kit.app.open")[-1]["temp"]["hear"], ["game:combat"])
        with self.watched() as (calls, _):
            self.ok("POST", "/api/local/call", {"cap": "fx.emit", "args": {"text": "x"}})
            self.assertTrue(until(lambda: self.reaction("companion.1")["fired"] == 1))
            self.assertTrue(until(lambda: len(calls) == 2))
        self.assertEqual(calls[1]["caller"], {"by": "workshop", "chain": ["reaction:companion.1"]})
        self.kit.emit("kit.app", {"app": "companion", "on": False})
        self.assertTrue(until(lambda: not self.ok("GET", "/api/reactions")["reactions"]))

    def test_an_apps_reactions_follow_kit_ready(self):
        self.add(apps=[REACTING])
        self.ok("POST", "/api/local/call", {"cap": "fx.echo"})
        self.kit.on.add("companion")
        self.kit.emit("kit.ready", {})                                     # it runs in the new session
        self.assertTrue(until(lambda: self.reaction("companion.1")))
        self.ok("POST", "/api/local/set", {"id": "fx", "options": {"volume": 60}})
        self.kit.on.clear()
        self.ok("POST", "/api/local/load", {"id": "fx"})                   # not a new session: nothing is taken
        self.assertIsNotNone(self.reaction("companion.1"))
        self.kit.emit("kit.ready", {})                                     # it stopped while nobody heard
        self.assertTrue(until(lambda: self.reaction("companion.1") is None))

    def test_an_apps_bad_reactions_are_said(self):
        self.add(apps=[APP])
        bad = [{"ext": "fx", "hear": "fx:said", "call": "fx.ghost"}]
        with mock.patch.object(self.svc.local, "app_reactions", return_value=bad):
            self.kit.emit("kit.app", {"app": "companion", "on": True})
            self.assertTrue(until(lambda: [e for e in self.svc.journal.since(0, 100, ["INFO"], tail=True)[0]
                                           if "reaction 1 of app companion" in e["text"]]))
        self.assertEqual(self.ok("GET", "/api/reactions")["reactions"], [])

    # --- approving

    def test_approve(self):
        self.add(start="workshop", apps=[APP])
        self.assertTrue(until(lambda: self.ok("GET", "/api/local")["extensions"][0]["state"] == "running"))
        self.assertEqual(self.ok("POST", "/api/local/stop", {"id": "fx"})["state"], "stopped")
        self.kit.apps.clear()                                                  # as if it was never declared
        self.kit.calls.clear()
        with mock.patch.object(self.svc.local, "approve", wraps=self.svc.local.approve) as approve:
            row = self.ok("POST", "/api/local/approve", {"id": "fx"})
        approve.assert_called_once_with("fx", "player")
        self.assertEqual((row["id"], row["approval"], row["installed"]), ("fx", "ok", None))   # no game addon
        self.assertEqual([d["app"] for d in self.kit.asked("kit.app.declare")], ["companion"])
        self.assertTrue(until(lambda: self.ok("GET", "/api/local")["extensions"][0]["state"] == "running"))
        self.fails("POST", "/api/local/approve", {"id": "ghost"}, 404, "not_found")

    def test_approving_one_that_starts_on_use_starts_it_only_to_learn_what_it_can_do(self):
        self.add()
        self.assertEqual(self.ok("POST", "/api/local/approve", {"id": "fx"})["approval"], "ok")
        row = lambda: self.ok("GET", "/api/local")["extensions"][0]
        self.assertTrue(until(lambda: row()["capabilities"] and row()["state"] == "stopped"))
        self.assertEqual(row()["pid"], None)

    # --- a call's time

    def test_the_deadline_is_told_and_a_timeout_cancels_with_a_ping(self):
        self.add()
        with self.watched() as (calls, cancels):
            self.ok("POST", "/api/local/call", {"cap": "fx.echo"})
            self.ok("POST", "/api/local/call", {"cap": "fx.slow", "args": {"seconds": 0}})
            self.ok("POST", "/api/local/call", {"cap": "fx.echo", "timeout_ms": 45000})
            self.assertEqual([c["deadline_ms"] for c in calls], [30000, 1000, 45000])   # none, its own, the caller's
            self.assertEqual(cancels, [])
            self.fails("POST", "/api/local/call", {"cap": "fx.slow", "args": {"seconds": 1.5}, "timeout_ms": 200},
                       504, "timeout")
            self.assertEqual(calls[-1]["deadline_ms"], 200)
            self.assertEqual(cancels, [dict(fut=calls[-1]["fut"], ping=True)])

    def test_a_caller_that_goes_cancels_without_a_ping(self):
        self.add()
        self.ok("POST", "/api/local/call", {"cap": "fx.echo"})
        with self.watched() as (calls, cancels):
            going = asyncio.run_coroutine_threadsafe(
                self.svc.local_call("fx.slow", {"seconds": 2}, 5000), self.svc.loop)
            self.assertTrue(until(lambda: calls))
            going.cancel()
            self.assertTrue(until(lambda: cancels))
            self.assertEqual(cancels, [dict(fut=calls[0]["fut"], ping=False)])

    def test_a_client_that_hangs_up_cancels_it(self):
        self.add()
        self.ok("POST", "/api/local/call", {"cap": "fx.echo"})
        url = urllib.parse.urlsplit(self.handle.url)
        with self.watched() as (calls, cancels):
            conn = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
            try:
                conn.request("POST", "/api/local/call",
                             body=json.dumps({"cap": "fx.slow", "args": {"seconds": 3}, "timeout_ms": 8000}),
                             headers={"Authorization": f"Bearer {self.handle.token}",
                                      "Content-Type": "application/json"})
                self.assertTrue(until(lambda: calls))
            finally:
                conn.close()                                                   # no answer read: the client went
            self.assertTrue(until(lambda: cancels, 2.5), "the call went on after its client hung up")
            self.assertEqual(cancels, [dict(fut=calls[0]["fut"], ping=False)])

    # --- the caller chain

    def test_a_chain_of_more_than_four_hops_is_a_loop(self):
        self.add()
        hops = ["reaction:r1", "ext:a", "ext:b", "ext:c"]
        res = self.ok("POST", "/api/local/call", {"cap": "fx.echo", "caller": {"by": "workshop", "chain": hops}})
        self.assertEqual(res["result"]["caller"], {"by": "workshop", "chain": hops})
        msg = self.fails("POST", "/api/local/call",
                         {"cap": "fx.echo", "caller": {"by": "workshop", "chain": hops + ["ext:d"]}}, 409, "loop")
        self.assertIn("reaction:r1 > ext:a > ext:b > ext:c > ext:d", msg)
        self.fails("POST", "/api/local/call", {"cap": "fx.echo", "caller": {"chain": "ext:a"}}, 400, "bad_request")
        self.fails("POST", "/api/local/call", {"cap": "sense.bags", "caller": {"chain": hops + ["ext:d"]}},
                   409, "loop")                                                # the game's capabilities too

    def test_an_extensions_own_call_goes_on_the_chain_it_serves(self):
        self.add()
        got = self.svc.extension_calls("fx", "fx.echo", {"text": "x"}, ["reaction:r1", "ext:a"]).result(30)
        self.assertEqual(got["caller"], {"by": "extension", "id": "fx", "chain": ["reaction:r1", "ext:a", "ext:fx"]})
        got = self.svc.extension_calls("fx", "fx.echo", {}).result(30)
        self.assertEqual(got["caller"], {"by": "extension", "id": "fx", "chain": ["ext:fx"]})
        with self.assertRaises(ApiError) as e:                                 # the fifth hop
            self.svc.extension_calls("fx", "fx.echo", {}, ["reaction:r1", "ext:a", "ext:b", "ext:fx"]).result(30)
        self.assertEqual((e.exception.status, e.exception.code), (409, "loop"))

    # --- the reasons

    def test_busy_and_cancelled_answers(self):
        self.add()
        self.ok("POST", "/api/local/call", {"cap": "fx.echo"})
        with mock.patch.object(self.svc.local, "call", return_value=failed(RpcError(2, "one at a time"))):
            self.assertIn("one at a time", self.fails("POST", "/api/local/call", {"cap": "fx.echo"}, 429, "busy"))
        with mock.patch.object(self.svc.local, "call", return_value=failed(RpcError(3, "dropped it"))):
            self.assertIn("dropped it", self.fails("POST", "/api/local/call", {"cap": "fx.echo"}, 499, "cancelled"))
        dropped = Future()
        dropped.cancel()                                                       # somebody else cancelled the call
        with mock.patch.object(self.svc.local, "call", return_value=dropped):
            self.fails("POST", "/api/local/call", {"cap": "fx.echo"}, 499, "cancelled")
        with mock.patch.object(self.svc.local, "call", return_value=failed(RpcError(1, "it went wrong"))):
            self.fails("POST", "/api/local/call", {"cap": "fx.echo"}, 422, "failed")

    def test_an_extension_that_cannot_run(self):
        self.add()
        why = {"needs_approval": (ApiError(403, "needs_approval", "fx: the player has not allowed its command"),
                                  403, "needs_approval"),
               "starting": (ApiError(503, "starting", "fx is starting"), 503, "starting"),
               "start_failed": (ApiError(500, "start_failed", "fx: no such program"), 503, "down"),
               "stopping": (ApiError(503, "stopping", "the daemon is shutting down"), 503, "down")}
        for name, (error, status, code) in why.items():
            with self.subTest(name), mock.patch.object(self.svc.local, "ensure", return_value=failed(error)):
                msg = self.fails("POST", "/api/local/call", {"cap": "fx.echo"}, status, code)
                self.assertEqual(msg, error.message)
        self.ok("POST", "/api/local/call", {"cap": "fx.echo"})                 # it was the start, not the extension


class Asking(LocalDaemon, unittest.TestCase):
    """the setting extension_approval over a daemon in developer mode (a daemon's default): "ask" (nothing saved) asks
    the player once for each command whatever the mode is; "auto" lets the workshop approve by itself"""
    LETTER = "Y"
    APPROVAL = None

    def tearDown(self):
        self.ok("POST", "/api/settings", {"extension_approval": "ask"})
        super().tearDown()

    def row(self, eid="fx"):
        return next(r for r in self.ok("GET", "/api/local")["extensions"] if r["id"] == eid)

    def test_ask_is_the_default_whatever_the_mode(self):
        status = self.ok("GET", "/api/status")["daemon"]
        self.assertEqual((status["mode"], status["extension_approval"]), ("developer", "ask"))
        self.assertEqual(self.ok("GET", "/api/settings")["extension_approval"], "ask")
        row, folder = self.add(apps=[APP])
        self.assertEqual((row["approval"], row["allowed"], row["capabilities"]), ("needed", False, []))
        self.assertEqual(self.kit.asked("kit.app.declare"), [])                # nothing of it is there
        for path, body in (("/api/local/call", {"cap": "fx.echo", "args": {"text": "a"}}),
                           ("/api/local/load", {"id": "fx"}), ("/api/local/check", {"id": "fx"}),
                           ("/api/local/check", {"path": str(folder)})):
            self.assertIn("the player has not confirmed", self.fails("POST", path, body, 403, "needs_approval"))
        self.assertEqual((self.row()["state"], self.row()["pid"]), ("stopped", None))
        self.assertEqual(self.ok("POST", "/api/local/approve", {"id": "fx"})["approval"], "ok")
        self.assertEqual(self.svc.local.reg.entry("fx")["approved"]["by"], "player")
        self.assertEqual([d["app"] for d in self.kit.asked("kit.app.declare")], ["companion"])
        self.assertEqual(self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": "a"}})["result"]["text"], "a")

    def test_the_game_half_waits_for_the_player(self):
        row = self.ok("POST", "/api/local/add", {"path": str(with_game(self.root / "ext" / "ax", "ax", "WuxianAx"))})
        self.assertEqual((row["approval"], row["addons"][0]["state"]), ("needed", "missing"))
        self.fails("POST", "/api/local/install", {"id": "ax"}, 403, "needs_approval")
        self.assertFalse((self.addons / "WuxianAx").exists())
        row = self.ok("POST", "/api/local/approve", {"id": "ax"})              # allowed: its game addon goes in
        self.assertEqual((row["approval"], row["addons"][0]["state"]), ("ok", "current"))
        self.assertEqual((row["installed"]["ok"], row["installed"]["restart"],
                          [(a["folder"], a["state"]) for a in row["installed"]["addons"]]),
                         (True, True, [("WuxianAx", "installed")]))
        self.assertNotIn("installed", self.row("ax"))                          # the answer says it, the row does not
        self.assertTrue((self.addons / "WuxianAx" / "core.lua").is_file())
        self.assertEqual(self.ok("POST", "/api/local/install", {"id": "ax"})["restart"], False)
        # auto: the workshop allows one by itself when it first runs; its game addon goes in when it is loaded
        self.ok("POST", "/api/settings", {"extension_approval": "auto"})
        self.ok("POST", "/api/local/add", {"path": str(with_game(self.root / "ext" / "ay", "ay", "WuxianAy"))})
        self.assertTrue(until(lambda: self.row("ay")["capabilities"] and self.row("ay")["state"] == "stopped"))
        self.assertEqual((self.row("ay")["approval"], self.row("ay")["addons"][0]["state"]), ("ok", "missing"))
        res = self.ok("POST", "/api/local/load", {"id": "ay"})
        self.assertEqual((res["addons"][0]["state"], res["restart"]), ("installed", True))
        self.assertTrue((self.addons / "WuxianAy" / "core.lua").is_file())

    def test_a_game_addon_that_cannot_go_in_is_said_before_and_after(self):
        store = X.catalog_store()                                              # the catalog as the daemon read it
        self.assertFalse(store.exists())
        store.write_text(json.dumps({"addons": [{"folder": "WuxianLx"}]}), encoding="utf-8")
        self.addCleanup(self.svc.local.forget_addons)
        self.addCleanup(store.unlink)
        folder = with_game(self.root / "ext" / "lx", "lx", "WuxianLx")
        row = self.ok("POST", "/api/local/add", {"path": str(folder)})
        self.assertEqual((row["approval"], row["addons"][0]["state"]), ("needed", "listed"))
        game = self.ok("GET", "/api/local/summary?id=lx")["game"]              # before the yes: the summary says it
        self.assertEqual([(g["folder"], g["cannot"], g["path"]) for g in game], [("WuxianLx", "listed", None)])
        self.assertEqual(self.ok("POST", "/api/local/summary", {"path": str(folder)})["game"][0]["cannot"], "listed")
        row = self.ok("POST", "/api/local/approve", {"id": "lx"})              # the yes holds, and says what did not
        self.assertEqual((row["approval"], row["addons"][0]["state"]), ("ok", "listed"))
        said = row["installed"]
        self.assertEqual((said["ok"], said["code"], said["addons"]), (False, "bad_addon", []))
        self.assertIn("WuxianLx", said["message"])
        self.assertIn("an addon of the catalog", said["message"])
        self.assertFalse((self.addons / "WuxianLx").exists())
        self.assertNotIn("installed", self.row("lx"))
        res = self.ok("POST", "/api/local/check", {"id": "lx"})                # and the check, for whoever wrote it
        bad = [p for p in res["problems"] if p["level"] == "error"]
        self.assertEqual((res["ok"], [p["where"] for p in bad]), (False, ["wuxian-extension.json: addons"]))
        self.assertIn("WuxianLx is the folder name of an addon of the catalog", bad[0]["message"])
        self.assertIn("another name", bad[0]["hint"])
        self.assertIn("an addon of the catalog", self.fails("POST", "/api/local/install", {"id": "lx"}, 400, "bad_addon"))
        # one of another name in the way: the same answer, with its own code
        theirs = self.addons / "WuxianFx2"
        theirs.mkdir()
        (theirs / "theirs.lua").write_text("-- theirs\n", encoding="utf-8")
        self.addCleanup(theirs.rmdir)
        self.addCleanup((theirs / "theirs.lua").unlink)
        row = self.ok("POST", "/api/local/add", {"path": str(with_game(self.root / "ext" / "fx2", "fx2", "WuxianFx2"))})
        self.assertEqual(row["addons"][0]["state"], "foreign")
        self.assertEqual(self.ok("GET", "/api/local/summary?id=fx2")["game"][0]["cannot"], "foreign")
        row = self.ok("POST", "/api/local/approve", {"id": "fx2"})
        self.assertEqual((row["approval"], row["installed"]["ok"], row["installed"]["code"]), ("ok", False, "addon_exists"))
        self.assertEqual([p.name for p in theirs.iterdir()], ["theirs.lua"])
        res = self.ok("POST", "/api/local/check", {"id": "fx2"})
        self.assertEqual([(p["level"], p["where"]) for p in res["problems"] if "WuxianFx2" in p["message"]],
                         [("warning", "wuxian-extension.json: addons")])

    def test_auto(self):
        self.add(apps=[APP])
        self.assertEqual(self.kit.asked("kit.app.declare"), [])
        revision = self.ok("GET", "/api/local")["revision"]
        res = self.ok("POST", "/api/settings", {"extension_approval": "auto"})
        self.assertEqual((res["extension_approval"], res["mode"]), ("auto", "developer"))
        self.assertNotIn("install", res)
        self.assertEqual(self.ok("GET", "/api/status")["daemon"]["extension_approval"], "auto")
        self.assertEqual(json.loads((self.root / "home" / "state" / "settings.json").read_text(encoding="utf-8"))
                         ["extension_approval"], "auto")
        # what it adds is there at once (its apps are declared, the rows are read again); its command is approved
        # when it first runs
        self.assertEqual([d["app"] for d in self.kit.asked("kit.app.declare")], ["companion"])
        self.assertGreater(self.ok("GET", "/api/local")["revision"], revision)
        self.assertEqual((self.row()["approval"], self.row()["allowed"]), ("needed", True))
        self.assertEqual(self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": "a"}})["result"]["text"], "a")
        self.assertEqual((self.row()["approval"], self.svc.local.reg.entry("fx")["approved"]["by"]), ("ok", "developer"))
        self.add("fy")                                     # a new one says what it can do at once: nobody is asked
        self.assertTrue(until(lambda: self.row("fy")["capabilities"] and self.row("fy")["state"] == "stopped"))
        self.assertEqual(self.row("fy")["approval"], "ok")
        # asking again: what was approved stays approved, a new one waits for the player
        self.assertEqual(self.ok("POST", "/api/settings", {"extension_approval": "ask"})["extension_approval"], "ask")
        self.assertEqual(self.ok("POST", "/api/local/call", {"cap": "fy.echo", "args": {"text": "b"}})["result"]["text"], "b")
        row, _ = self.add("fz")
        self.assertEqual((row["approval"], row["allowed"]), ("needed", False))
        self.fails("POST", "/api/local/call", {"cap": "fz.echo", "args": {"text": "c"}}, 403, "needs_approval")

    def test_a_value_it_does_not_know(self):
        for value in ("always", "developer", True, None, ""):
            self.assertIn('"ask" or "auto"',
                          self.fails("POST", "/api/settings", {"extension_approval": value}, 400, "bad_request"))
        self.assertEqual(self.ok("GET", "/api/settings")["extension_approval"], "ask")
