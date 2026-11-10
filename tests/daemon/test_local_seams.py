"""The daemon's side of the local extensions' seams over a real daemon (tests/daemon/test_local_api.py's LocalDaemon):
WuxianKit's local apps reconciled (forgotten when no enabled extension declares them, at once or at the next kit.ready),
the reactions an app's manifest declares set while it runs, /api/local/approve, a call's deadline and its cancelling
(the time over, the caller gone, the client hung up), the caller chain and its loop, and the protocol's reasons as the
error codes. What the other packages still stub (Host.app_reactions, Host.approve, Host.call's deadline_ms,
Host.cancel_call, Host.ensure's needs_approval) is patched or watched here."""
import asyncio
import contextlib
import http.client
import json
import time
import unittest
import urllib.parse
from concurrent.futures import Future
from unittest import mock

from tests.daemon.test_local_api import LocalDaemon
from tests.local.util import until
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local.rpc import RpcError

APP = {"app": "companion", "title": "语音搭子", "prompt": "companion"}
SPECS = [{"ext": "fx", "hear": "fx:said", "call": "fx.echo", "args": {"text": "听到"}, "cooldown": 0},
         {"ext": "fx", "hear": "game:combat", "call": "fx.echo", "note": "a pull"}]


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

    def declared(self):
        return mock.patch.object(self.svc.local, "app_reactions",
                                 side_effect=lambda app: [dict(s) for s in SPECS] if app == "companion" else [])

    def test_an_apps_reactions_are_there_while_it_runs(self):
        self.add(apps=[APP])
        with self.declared():
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
        self.add(apps=[APP])
        self.ok("POST", "/api/local/call", {"cap": "fx.echo"})
        with self.declared():
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
        self.assertEqual((row["id"], row["approval"]), ("fx", "ok"))
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
