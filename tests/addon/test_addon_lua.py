"""The addons' own Lua, run under Lua 5.1 (lupa) against a mock of the WoW API (tests/support/wowmock.py): the v0.8 link
against the real companion code (wuxianworkshop/transport/link.py): handshake, typed messages, pings, a burst, /reload, a
companion restart, an outage, a skipped mailbox slot, wrapping message ids, hot loading with its lifecycle hooks; and the
platform addon !WuxianWorkshop: the early stash for WoWBridge and the player's error collection (/wxw)."""
import asyncio
import re
import unittest

from tests.support.wowmock import Session, lupa
from wuxianworkshop.core import fontpack, mailbox
from wuxianworkshop.core.game import atomic_write
from wuxianworkshop.daemon.api import parse_run
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.transport import link

def count(table):
    """entries of a Lua table (len() only counts its array part)"""
    return sum(1 for _ in table.keys())


def console_lines(s):
    """the debug window's lines in view (Console.lua), as plain text"""
    t = s.ns[b"Console"][b"Lines"]()
    return [t[i].decode("utf-8") for i in range(1, len(t) + 1)]





@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Link(unittest.TestCase):
    def test_link(self):
        logs = []
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=logs.append, ping_every=4, hb_every=5, hb_lazy=5)
        s.comp = comp
        try:
            comp.new_process("P1-100")
            comp.say("你好 WoWBridge")
            s.login()
            s.run(8)
            chat = s.chat()
            self.assertIn("connected to the companion (mailbox slot 1)", chat)
            self.assertIn(f"companion {link.VERSION}: frames 64x16 (long messages 128x32), mode 1, 64 in flight, 0.20 s a frame, "
                          "a heartbeat every 5 s", chat)
            self.assertIn("> 你好 WoWBridge", chat)
            sess = comp.sessions[comp.current]
            self.assertIsNotNone(sess.online_at, logs)
            self.assertLess(sess.online_at - sess.hello_at, 5)
            s.run(10)
            self.assertGreaterEqual(len(comp.rtts), 2, logs)
            self.assertLess(max(comp.rtts), 3)

            s.slash("burst 10")
            t0 = s.now()
            s.run(30)
            texts = [m[4] for m in sess.messages]
            self.assertEqual(len([t for t in texts if t.startswith("burst ")]), 10)
            self.assertRegex(texts[-1], r"^BURST DONE n=10 acked=10 resent=0 avg=\S+ max=\S+ total=\S+$")
            self.assertEqual(sess.dups, 0)
            self.assertLess(sess.messages[-1][0] - t0, 12)               # 10 + 1 messages at 0.25 s a frame, acks in flight

            # a long message in parts of 128x32 frames, checked end to end (CRC of the body, Chinese included)
            s.slash("long 6000")
            s.run(15)
            self.assertEqual(comp.stats["long_ok"], 1, logs)
            done = sess.messages[-1][4]
            self.assertRegex(done, r"^LONG DONE id=\d+ bytes=6000 parts=5 shown_again=0 time=\S+ draw=\S+$")
            # one part lost on its way: the companion reports the parts it has, the addon shows only the missing one
            s.drop_part = 2
            s.slash("long 6000")
            s.run(20)
            self.assertEqual(comp.stats["long_ok"], 2, logs)
            self.assertRegex(sess.messages[-1][4], r"^LONG DONE id=\d+ bytes=6000 parts=5 shown_again=1 ")
            s.slash("link")
            self.assertRegex(s.chat(), r"link online, mailbox slot (\d+) \(poll, \d+ left\), \d+ packets read; 0 waiting, "
                                       r"15 acknowledged")
            slot, left = map(int, re.search(r"mailbox slot (\d+) \(poll, (\d+) left\)", s.chat()).groups())
            self.assertEqual(slot + left, 4097)
            slot_before = comp.slot

            # /reload: a new session continues with the next mailbox slot (fonts stay cached in the client)
            s2 = s.reload()
            s2.login()
            s2.run(8)
            sess2 = comp.sessions[comp.current]
            self.assertNotEqual(sess2.sid, sess.sid)
            self.assertIsNotNone(sess2.online_at, logs)
            self.assertGreaterEqual(comp.stats["last_slot"], slot_before)       # the slots go on, no reset
            s2.slash("send after reload")
            s2.run(6)
            self.assertEqual(sess2.messages[-1][4], "after reload")

            # the companion restarts in the same game process: it learns the slot from the next heartbeat
            comp2 = link.Companion(s2.addons, clock=s2.now, log=logs.append, ping_every=4, hb_every=5, hb_lazy=5)
            comp2.new_process("P1-100")
            s2.comp = comp2
            s2.run(12)
            self.assertNotIn("offline", s2.chat())
            s2.slash("send after the companion restart")
            s2.run(6)
            self.assertEqual(comp2.sessions[comp2.current].messages[-1][4], "after the companion restart")

            # E2: the window is resized to an odd size; frames and the mailbox keep working through a stream
            s2.lua.globals()[b"__resize"](1599, 901)
            s2.lua.globals()[b"__fire"](b"DISPLAY_SIZE_CHANGED")
            s2.slash("stream 12")
            s2.run(25)
            st = comp2.stats
            self.assertGreater(st["stream_ok"], 20, logs[-5:])
            self.assertEqual((st["stream_bad"], st["stream_missing"]), (0, 0))
            self.assertRegex(comp2.sessions[comp2.current].messages[-1][4], r"^STREAM DONE n=\d+ acked=\d+ resent=0 ")

            # an outage: no packets for 20 s, the addon goes offline, then reconnects
            s2.comp_running = False
            s2.run(20)
            self.assertIn("no packet from the companion for 15 s: offline", s2.chat())
            s2.comp_running = True
            s2.run(10)
            self.assertEqual(s2.chat().count("connected to the companion"), 3)   # before and after /reload, then the reconnect
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class DebugOutput(unittest.TestCase):
    def test_errors_prints_and_warnings_reach_the_companion(self):
        logs, got = [], []
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=logs.append, on_debug=lambda kind, text: got.append((kind, text)))
        s.comp = comp
        try:
            comp.new_process("P1-100")
            g = s.lua.globals()
            g[b"print"](b"before the link")                        # kept until the link starts
            s.login()
            s.run(8)
            self.assertIn("connected to the companion", s.chat())
            g[b"print"](b"hello", 42)
            g[b"print"](b"|cffff0000red|r line")
            for _ in range(3):
                g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:12: attempt to call a nil value")
            g[b"__fire"](b"LUA_WARNING", 0, b"Couldn't open Interface/AddOns/Foo/Missing.lua")
            g[b"__fire"](b"ADDON_ACTION_BLOCKED", b"Foo", b"ReloadUI()")
            s.run(8)
            outs = "\n".join(t for k, t in got if k == "OUT")
            self.assertIn("before the link", outs)
            self.assertIn("hello 42\nred line", outs)                  # gathered into one message, colours removed
            errs = [t for k, t in got if k == "ERR"]
            self.assertEqual(len(errs), 1, got)                         # three times the same error: sent once
            self.assertIn("in function `Broken'", errs[0])              # with its stack
            self.assertIn(("WARN", "0 Couldn't open Interface/AddOns/Foo/Missing.lua"), got)
            self.assertIn(("BLOCKED", "ADDON_ACTION_BLOCKED Foo ReloadUI()"), got)
            s.run(12)                                                   # the repeat count follows within 10 s
            self.assertIn(("ERR", "Interface/AddOns/Foo/Foo.lua:12: attempt to call a nil value\n(2 more times)"), got)
            self.assertEqual(len(g[b"__errors"]), 3)                    # the client's own handlers still ran
            self.assertEqual(len(g[b"__printed"]), 3)
            self.assertIn("[lua error] Interface/AddOns/Foo/Foo.lua:12: attempt to call a nil value", "\n".join(logs))
            d = comp.stats["debug"]
            self.assertEqual((d["err"], d["repeats"], d["warn"], d["blocked"], d["dropped"]), (1, 1, 1, 1, 0))
            counted = s.ns[b"Debug"][b"stats"]                          # the panel's counts: every one, repeats too
            self.assertEqual((counted[b"errors"], counted[b"warnings"], counted[b"blocked"]), (3, 1, 1))
        finally:
            s.close()

    def test_a_flood_is_capped(self):
        """an error that changes every time, many times a second: at most 20 debug messages wait, the rest are counted"""
        logs, got = [], []
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=logs.append, on_debug=lambda kind, text: got.append((kind, text)))
        s.comp = comp
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(8)
            g = s.lua.globals()
            for i in range(200):
                g[b"__error"](f"Interface/AddOns/Foo/Foo.lua:{i}: boom".encode())
            s.run(30)
            errs = [t for k, t in got if k == "ERR"]
            self.assertEqual(len(errs), 20)
            dropped = [t for k, t in got if k == "DROPPED"]
            self.assertEqual(len(dropped), 0)                           # nothing came after the flood to carry the count
            g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:999: later")
            s.run(10)
            self.assertIn(("DROPPED", "180 debug messages (more than 20 were waiting)"), got)
            self.assertIn("Interface/AddOns/Foo/Foo.lua:999: later", [t.split("\n")[0] for k, t in got if k == "ERR"])
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class AgentReload(unittest.TestCase):
    def test_reload_button(self):
        """the companion asks for a reload: a button comes up and nothing reloads by itself; the click reloads, the next
        session connects, and the same command read again (a rewind) asks no more"""
        logs, got = [], []
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=logs.append, on_debug=lambda kind, text: got.append((kind, text)))
        s.comp = comp
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(8)
            comp.command("reload")
            nonce = comp.outbox[-1][1]
            s.run(6)
            g = s.lua.globals()
            dialog = g[b"WoWBridgeReloadDialog"]
            self.assertTrue(dialog[b"shown"])
            self.assertEqual(g[b"__reloads"], 0)                        # not by itself: only a click may
            self.assertIn("the agent asks for a UI reload: click the button", s.chat())
            self.assertIn(("RELOAD", "asked: a button is up; the UI reloads when the user clicks it"), got)
            dialog[b"reload"][b"scripts"][b"OnClick"](dialog[b"reload"])   # the user's click
            self.assertEqual(g[b"__reloads"], 1)
            s2 = s.reload()                                             # the old session ends here
            s2.login()
            s2.run(8)
            self.assertTrue(any(t.startswith("reload done: session") for t in logs), logs)
            self.assertIn(("RELOAD", next(t for t in logs if t.startswith("reload done"))), got)
            comp.outbox.append((mailbox.COMMAND, nonce))                # the same command again
            s2.run(8)
            self.assertIsNone(s2.lua.globals()[b"WoWBridgeReloadDialog"])   # no button this time
        finally:
            s.close()

    def test_reload_later(self):
        """the user puts the reload off: the button goes away, the agent hears so, the session goes on"""
        logs, got = [], []
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=logs.append, on_debug=lambda kind, text: got.append((kind, text)))
        s.comp = comp
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(8)
            comp.command("reload")
            s.run(6)
            dialog = s.lua.globals()[b"WoWBridgeReloadDialog"]
            dialog[b"later"][b"scripts"][b"OnClick"](dialog[b"later"])
            s.run(4)
            self.assertFalse(dialog[b"shown"])
            self.assertIn(("RELOAD", "later: the user put the reload off"), got)
            self.assertEqual(s.lua.globals()[b"__reloads"], 0)
            self.assertNotIn("offline", s.chat())
        finally:
            s.close()

    def test_dump(self):
        """returned tables come back readable: sorted keys, nesting down to 3 levels, cycles, functions, a byte limit"""
        s = Session(self)
        try:
            s.login()
            dump = s.lua.eval(b"WoWBridge.Dump")
            t = s.lua.eval(b'(function() local t = { 10, "a", name = "x", ["two words"] = true, deep = { a = { b = { c = 1 } } },'
                           b' f = print } t.self = t return t end)()')
            text = dump(t).decode()
            self.assertEqual(text, '{\n  [1] = 10,\n  [2] = "a",\n  deep = {\n    a = {\n      b = {... 1 entries},\n    },\n  },\n'
                                   '  f = ' + str(s.lua.eval(b"tostring(print)").decode()) + ',\n  name = "x",\n'
                                   '  self = <cycle>,\n  ["two words"] = true,\n}')
            self.assertEqual(dump(s.lua.eval(b"{}")).decode(), "{}")
            big = dump(s.lua.eval(b"(function() local t = {} for i = 1, 80 do t[i] = i end return t end)()")).decode()
            self.assertIn("  ... 30 more\n", big)
            cut = dump(s.lua.eval(b'(function() local t = {} for i = 1, 40 do t[i] = ("x"):rep(50) end return t end)()'),
                       3, 300).decode()
            self.assertTrue(cut.endswith(" ...(cut at 300 bytes)"), cut)
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class HotLoad(unittest.TestCase):
    def test_run_and_load(self):
        """run: a line of Lua, its print() output and returned values come back; load: a file of another addon in three
        parts runs once as that file, with the namespace the addon registered; errors come back with the file's name"""
        logs, got = [], []
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=logs.append, on_debug=lambda kind, text: got.append((kind, text)))
        s.comp = comp

        def runs():
            return [t for k, t in got if k == "RUN"]
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(8)
            g = s.lua.globals()
            comp.command('run print("hi", 1 + 1) return "done", nil, 3')
            s.run(6)
            self.assertIn(("OUT", "hi 2"), got)
            self.assertRegex(runs()[-1], r'^\d+ ok =run \(\d+ B, [\d.]+ ms, 3 values\): "done", "nil", "3"$')
            self.assertRegex(console_lines(s)[-1], r"agent  ran a Lua snippet · [\d.]+ ms  → done, nil, 3  \[details\]$")
            self.assertNotIn("agent code =run (", s.chat())             # the notice says it, not the chat

            foo = s.addons / "Foo"
            foo.mkdir()
            (foo / "Core.lua").write_text("-- " + "x" * 8000 + "\nlocal name, ns = ...\nns.value = ns.value + 1\n"
                                          "FooLoads = (FooLoads or 0) + 1\nreturn name, ns.value\n", encoding="utf-8")
            s.lua.execute(b"WoWBridgeNS.Foo = { value = 41 }")
            comp.command("load Foo/Core.lua")
            sent = [r for r in comp.outbox if r[0] == mailbox.CODE]
            self.assertEqual(len(sent), 3)
            s.run(15)
            self.assertRegex(runs()[-1], r'^\d+ ok @Interface/AddOns/Foo/Core\.lua \(\d+ B, [\d.]+ ms, 2 values\): '
                                         r'"Foo", "42"$')
            self.assertEqual(g[b"FooLoads"], 1)
            self.assertRegex(s.ns[b"Console"][b"lastToast"].decode(), r"^Hot-loaded Foo/Core\.lua · \d+\.\d ms$")
            comp.outbox.extend(sent)                                    # the same parts again (a lost slot, say)
            s.run(10)
            self.assertEqual(g[b"FooLoads"], 1)                         # not run twice

            comp.command("run x = = 1")
            (foo / "Bad.lua").write_text("local t = nil\nreturn t.x\n", encoding="utf-8")
            comp.command("load Foo/Bad.lua")
            (s.addons / "Baz").mkdir()
            (s.addons / "Baz" / "B.lua").write_text("local name, ns = ...\nns.x = 1\nreturn name, ns.x\n", encoding="utf-8")
            comp.command("load Baz/B.lua")
            comp.command("load Bar/Missing.lua")
            s.run(12)
            r = runs()
            self.assertTrue(any(re.match(r"^\d+ error =run: run:1: unexpected symbol near '='$", t) for t in r), r)
            self.assertTrue(any(re.match(r"^\d+ error @Interface/AddOns/Foo/Bad\.lua: Interface/AddOns/Foo/Bad\.lua:2: "
                                         r"attempt to index local 't' \(a nil value\)\n.*in function `Broken'$", t)
                                for t in r), r)                         # the loader's own frames cut off
            self.assertTrue(any(t.endswith(': "Baz", "1" (no namespace registered for Baz: it got a new one, kept for its '
                                           "later loads)") for t in r), r)
            self.assertTrue(any(t.startswith("load Bar/Missing.lua: ") for t in r), r)
            stats = s.ns[b"Agent"][b"stats"]                            # for the panel: 5 jobs ran, 2 failed
            self.assertEqual((stats[b"runs"], stats[b"failed"]), (5, 2))
            self.assertEqual((stats[b"last"][b"name"], stats[b"last"][b"ok"]), (b"@Interface/AddOns/Baz/B.lua", True))
        finally:
            s.close()

    def test_returned_values_come_back_whole(self):
        """seen in the game (0.9.4): `return "a, b (OnUnload ok)", 2` came back as the values "a" and "b" and the note
        "(OnUnload ok), 2". Through the real Agent.lua and link each value comes back as it was, whatever it holds: the
        "|" of a colour code or a link too, which the link strips from the rest of the text. Past 4000 bytes the result
        says which value was cut, where, and how many after it were not sent"""
        got = []
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=lambda text: None, on_debug=lambda kind, text: got.append((kind, text)))
        s.comp = comp

        def run(code):
            """(the RUN text, the result of /api/run) of a snippet"""
            job = comp.code(code.encode("utf-8"), "=run", "-")
            for _ in range(200):
                s.run(0.25)
                text = next((t for k, t in got if k == "RUN" and t.startswith(f"{job} ")), None)
                if text is not None:
                    return text, Service.run_result(parse_run(text))
            self.fail(f"no RUN result for job {job}")
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(8)
            text, res = run('return "a, b (OnUnload ok)", 2')
            self.assertRegex(text, r'^\d+ ok =run \(\d+ B, [\d.]+ ms, 2 values\): "a, b \(OnUnload ok\)", "2"$')
            self.assertEqual((res["values"], res["note"], res["cut"]), (["a, b (OnUnload ok)", "2"], None, None))
            self.assertTrue(console_lines(s)[-1].endswith("  → a, b (OnUnload ok), 2  [details]"))   # the window: as it was

            text, res = run(r'''return "|cffff0000red|r |Hitem:19019|h[Thunderfury, Blessed Blade]|h", 'say "hi" \\ \0\r',
                "", nil, "nil", true, "中文, 逗号 (OnReload ok)", { a = "x, y" }, "ends with\n\tspaces  ", 1.5''')
            self.assertEqual(res["values"], ["|cffff0000red|r |Hitem:19019|h[Thunderfury, Blessed Blade]|h",
                                             'say "hi" \\ \x00\r', "", "nil", "nil", "true", "中文, 逗号 (OnReload ok)",
                                             '{\n  a = "x, y",\n}', "ends with\n\tspaces  ", "1.5"])
            self.assertNotIn("|", text)                                 # \124: nothing for the link to strip
            self.assertEqual((res["note"], res["cut"]), (None, None))

            text, res = run('return ("x"):rep(3000), ("中"):rep(1000), "after", 4')
            self.assertEqual(res["values"], ["x" * 3000, "中" * 333])  # 1000 bytes left: 333 characters, never half of one
            self.assertEqual(res["cut"], dict(value=2, kept=999, bytes=3000, not_sent=2))
            self.assertTrue(text.endswith('中" (value 2 cut at 999 of 3000 bytes, 2 more not sent)'))
            text, res = run('return ("y"):rep(4000), "z"')
            self.assertEqual((res["values"], res["cut"]), (["y" * 4000, ""], dict(value=2, kept=0, bytes=1, not_sent=0)))

            foo = s.addons / "Foo"                                      # a load with its lifecycle notes after the values
            foo.mkdir()
            (foo / "Core.lua").write_text('return "x, y (OnReload ok)", "(OnUnload error: z)"\n', encoding="utf-8")
            s.lua.execute(b"WoWBridgeNS.Foo = { OnUnload = function() return 1 end, OnReload = function() end }")
            comp.command("load Foo/Core.lua")
            s.run(8)
            res = parse_run([t for k, t in got if k == "RUN"][-1])
            self.assertEqual((res["chunk"], res["values"]), ("@Interface/AddOns/Foo/Core.lua",
                                                             ["x, y (OnReload ok)", "(OnUnload error: z)"]))
            self.assertEqual((res["note"], res["cut"]), ("(OnUnload ok) (OnReload ok)", None))
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class DebugWindow(unittest.TestCase):
    def test_debug_window_and_notice(self):
        """the debug window (Console.lua) keeps what happens: an error seen again stays one line with its count, which
        shows on the minimap button until the window opens; prints; the agent's code. A hot-load of a whole addon is one
        notice; a failing snippet is a red one; with the notice off the chat says it, as before"""
        logs = []
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=logs.append)
        s.comp = comp
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(8)
            g, con = s.lua.globals(), s.ns[b"Console"]
            g[b"__error"](b"Interface/AddOns/Foo/Core.lua:3: boom")
            g[b"__error"](b"Interface/AddOns/Foo/Core.lua:3: boom")
            g[b"print"](b"hello", 2)
            boom = [line for line in console_lines(s) if "boom" in line]
            self.assertEqual(len(boom), 1)
            self.assertRegex(boom[0], r"^\d\d:\d\d:\d\d  error  Interface/AddOns/Foo/Core\.lua:3: boom  ×2  \[details\]$")
            self.assertTrue(any(line.endswith("  print  hello 2") for line in console_lines(s)))
            button = g[b"WoWBridgeMinimapButton"]
            self.assertEqual((con[b"unseen"], button[b"count"][b"text"], button[b"count"][b"shown"]), (2, b"2", True))
            s.slash("log errors")                                       # opens on the errors: the count is seen
            self.assertTrue(g[b"WoWBridgeConsole"][b"shown"])
            self.assertEqual((con[b"unseen"], button[b"count"][b"shown"]), (0, False))
            self.assertEqual(len(console_lines(s)), 1)
            s.slash("log")                                              # and closed again
            self.assertFalse(g[b"WoWBridgeConsole"][b"shown"])
            s.slash("")                                                 # the panel, and its 调试输出 button: the window
            panel, console = g[b"WoWBridgePanel"], g[b"WoWBridgeConsole"]   # comes up in front of the panel
            panel[b"console"].Click(panel[b"console"])
            self.assertTrue(console[b"shown"])
            self.assertEqual((console[b"strata"], console[b"toplevel"], panel[b"toplevel"]), (panel[b"strata"], True, True))
            self.assertGreater(console[b"raised"], panel[b"raised"])
            s.slash("log")
            s.slash("")
            self.assertFalse(console[b"shown"] or panel[b"shown"])

            foo = s.addons / "Foo"
            foo.mkdir()
            (foo / "Foo.toc").write_text("## Interface: 16001\nA.lua\nB.lua\n", encoding="utf-8")
            (foo / "A.lua").write_text("return 1\n", encoding="utf-8")
            (foo / "B.lua").write_text("return 2\n", encoding="utf-8")
            comp.command("load Foo")
            for _ in range(60):                                         # until the notice comes up
                s.run(0.25)
                if con[b"lastToast"] is not None:
                    break
            self.assertRegex(con[b"lastToast"].decode(), r"^Hot-loaded Foo · 2 files · \d+\.\d ms$")
            self.assertTrue(g[b"WoWBridgeToast"][b"shown"])
            s.run(4)
            self.assertFalse(g[b"WoWBridgeToast"][b"shown"])            # gone after a few seconds
            comp.command("run error('no')")
            s.run(6)
            self.assertRegex(con[b"lastToast"].decode(), r"^The agent's code failed: .*no$")
            s.slash("set toasts off")
            comp.command("run return 1")
            s.run(6)
            self.assertIn("agent code =run (", s.chat())                # no notice: the chat says it

            stats, before = s.ns[b"Agent"][b"stats"], len(console_lines(s))
            runs = stats[b"runs"]
            comp.code(b"return true", "=probe")                         # the app's own look (a try's end marker)
            s.run(6)
            self.assertEqual((len(console_lines(s)), stats[b"runs"]), (before, runs))
            self.assertNotIn("agent code =probe", s.chat())
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class ProbeAnswers(unittest.TestCase):
    def test_inspect_the_debug_window_with_its_details_open(self):
        """seen in the game (0.9.4): inspect WoWBridgeConsole failed. Its answer, 16821 bytes at depth 2, was cut at the
        4000 a RUN result carries; with a [详情] box open, the ", " and " (OnUnload " of the box's text were taken for
        value separators and a note. Through the real Agent.lua and link the answer (agent/probes.py) comes back whole,
        in pieces"""
        got = []
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=lambda text: None, on_debug=lambda kind, text: got.append((kind, text)))
        s.comp = comp
        svc = Service(lambda **kw: None, journal=Journal())

        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):     # the daemon's run, over this session
            job = comp.code(code.encode("utf-8"), chunk, addon or "-")
            for _ in range(200):
                s.run(0.25)
                text = next((t for k, t in got if k == "RUN" and t.startswith(f"{job} ")), None)
                if text is not None:
                    return Service.run_result(parse_run(text))
            self.fail(f"no RUN result for job {job}")
        svc.run = run

        def boxes(frame):
            """the edit boxes in an answer's frame and below"""
            return ([frame] if frame.get("type") == "EditBox" else []) + [b for kid in frame.get("children") or []
                                                                          for b in boxes(kid)]
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(8)
            comp.command('run return ("第一行, a, b (OnUnload ok) \\"引号\\" \\\\ 反斜杠\\t"):rep(60), 2')
            s.run(8)
            s.slash("log")
            shown = s.lua.execute(b"""
                local log, pane
                for _, kid in ipairs(WoWBridgeConsole.__kids) do
                    if kid.kind == "ScrollingMessageFrame" then log = kid elseif kid.edit then pane = kid end
                end
                for id = 50, 1, -1 do                                   -- the newest line with a [details] link
                    pcall(log.scripts.OnHyperlinkClick, log, "wbconsole:" .. id, "", "LeftButton")
                    if pane:IsShown() then return pane.edit:GetText() end
                end""").decode("utf-8")
            self.assertIn(", 2", shown)
            self.assertIn("(OnUnload ok) \"引号\" \\ 反斜杠\t", shown)
            for depth in (2, 3):
                before = len(got)
                res = asyncio.run(svc.inspect("WoWBridgeConsole", depth=depth))
                window = res["frames"][0]
                self.assertEqual((window["name"], window["type"], window["shown"]), ("WoWBridgeConsole", "Frame", True))
                pieces = [t for k, t in got[before:] if k == "RUN" and " ok =probe " in t]
                self.assertGreater(len(pieces), 1)
                self.assertTrue(all(parse_run(t)["cut"] is None for t in pieces))                # never cut
            self.assertEqual([b["text"] for b in boxes(window)], [shown])                 # at depth 3: the box's text
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class EarlyAndSlots(unittest.TestCase):
    def test_errors_before_wowbridge_loads(self):
        """!WuxianWorkshop loads first: errors, prints and warnings of addons loading before WoWBridge reach the companion;
        later ones go the usual way, and the client's own handler still sees them all"""
        logs, got = [], []

        def between(lua):                                               # an addon between !WuxianWorkshop and WoWBridge
            g = lua.globals()
            g[b"__error"](b"Interface/AddOns/Aaa/Aaa.lua:3: attempt to call a nil value")
            g[b"print"](b"Aaa loaded")
            g[b"__fire"](b"LUA_WARNING", 0, b"Couldn't open Interface/AddOns/Aaa/Missing.lua")
        s = Session(self, early=between)
        comp = link.Companion(s.addons, clock=s.now, log=logs.append, on_debug=lambda kind, text: got.append((kind, text)))
        s.comp = comp
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(10)
            errs = [t for k, t in got if k == "ERR"]
            self.assertEqual(len(errs), 1, got)
            self.assertTrue(errs[0].startswith("Interface/AddOns/Aaa/Aaa.lua:3: attempt to call a nil value\n"), errs)
            self.assertTrue(errs[0].endswith("(before WoWBridge loaded)"), errs)
            self.assertIn(("WARN", "0 Couldn't open Interface/AddOns/Aaa/Missing.lua"), got)
            self.assertIn("Aaa loaded", "\n".join(t for k, t in got if k == "OUT"))
            g = s.lua.globals()
            self.assertTrue(g[b"WuxianWorkshopEarly"][b"taken"])
            g[b"__error"](b"Interface/AddOns/Zzz/Zzz.lua:1: later")
            s.run(5)
            self.assertEqual(len([t for k, t in got if k == "ERR"]), 2)
            self.assertEqual(len(g[b"WuxianWorkshopEarly"][b"errors"]), 1)   # not kept any more
            self.assertEqual(len(g[b"__errors"]), 2)                    # the client's own handler saw both
            errors = g[b"WuxianWorkshopDB"][b"errors"]                  # ... and the player's collection has them all
            self.assertEqual(sorted(e[b"message"].decode() for e in errors.values()),
                             ["0 Couldn't open Interface/AddOns/Aaa/Missing.lua", "Interface/AddOns/Aaa/Aaa.lua:3: attempt to call a nil value",
                              "Interface/AddOns/Zzz/Zzz.lua:1: later"])
        finally:
            s.close()

    def test_slot_warning_and_full_dialog(self):
        """a few hundred slots left: a chat warning with what to do; none left: a dialog"""
        logs = []
        s = Session(self, db=b'{ mail = { proc = "P1-100", next = 3698 } }', locale="zhCN")    # a Chinese client
        s.comp = link.Companion(s.addons, clock=s.now, log=logs.append)
        try:
            s.comp.new_process("P1-100")
            s.login()
            s.run(10)
            self.assertRegex(s.chat(), r"信箱槽位还剩 39\d 个（空闲时约 3\.\d 小时）。用完后 App 的消息就进不来了，要完整退出游戏再启动才能恢复")
            self.assertEqual(s.chat().count("信箱槽位还剩"), 1)
            self.assertIsNone(s.lua.globals()[b"WoWBridgeSlotsDialog"])
        finally:
            s.close()
        s = Session(self, db=b'{ mail = { proc = "P1-100", next = 4096 } }', locale="zhCN")
        s.comp = link.Companion(s.addons, clock=s.now, log=logs.append)
        try:
            s.comp.new_process("P1-100")
            s.login()
            s.run(10)
            self.assertIn("信箱槽位已用完", s.chat())
            self.assertTrue(s.lua.globals()[b"WoWBridgeSlotsDialog"][b"shown"])
            self.assertTrue(any(t.startswith("mailbox full: all 4096 slots") for t in logs), logs)
            self.assertFalse(any("writing again" in t for t in logs), logs)   # all read is not a lost last slot
        finally:
            s.close()

    def test_offline_after_three_heartbeats(self):
        """a heartbeat every 30 s (15 s for an addon before 0.9.6): 60 s without a packet is not offline yet, 90 s is"""
        logs = []
        s = Session(self)
        s.comp = link.Companion(s.addons, clock=s.now, log=logs.append)
        try:
            s.comp.new_process("P1-100")
            s.login()
            s.run(8)
            self.assertIn("a heartbeat every 30 s", s.chat())
            s.comp_running = False
            s.run(65)
            self.assertNotIn("offline", s.chat())
            s.run(30)
            self.assertIn("no packet from the companion for 90 s: offline", s.chat())
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class ProtocolV08(unittest.TestCase):
    """the v0.8 revisions: typed messages, a skipped slot's records sent again, wrapping ids, the reset flag, /wb set"""

    def start(self, **kw):
        logs, got = [], []
        s = Session(self, **kw)
        comp = link.Companion(s.addons, clock=s.now, log=logs.append, on_debug=lambda kind, text: got.append((kind, text)))
        s.comp = comp
        comp.new_process("P1-100")
        s.login()
        s.run(8)
        self.assertIn("connected to the companion", s.chat())
        return s, comp, logs, got

    def test_user_text_is_not_parsed(self):
        """a message typed as user text is only logged, whatever it says; results and reports have their own types"""
        s, comp, logs, got = self.start()
        try:
            s.slash("send RUN 1 ok")
            s.slash("send ERR not an error")
            comp.command("run return 1")
            s.run(6)
            sess = comp.sessions[comp.current]
            texts = [m[4] for m in sess.messages]
            self.assertIn("RUN 1 ok", texts)
            self.assertIn("ERR not an error", texts)
            self.assertEqual([k for k, _ in got if k == "ERR"], [])
            runs = [t for k, t in got if k == "RUN"]
            self.assertEqual(len(runs), 1, got)
            self.assertRegex(runs[0], r'^\d+ ok =run \(\d+ B, [\d.]+ ms, 1 value\): "1"$')
            self.assertIn("#" + str([m[1] for m in sess.messages if m[4] == "RUN 1 ok"][0]) + " (8 B): RUN 1 ok", logs)
            self.assertGreaterEqual(sess.version, (0, 8))              # the typed uplink came with 0.8
            self.assertTrue(sess.typed)
        finally:
            s.close()

    def test_skipped_slot_is_sent_again(self):
        """a slot whose packet is damaged: the addon tries it 8 times, skips it and says so in its heartbeat (bad=); the
        companion sends the slot's text again in the next one, and nothing is lost or doubled"""
        s, comp, logs, got = self.start()
        try:
            s.comp_running = False
            s.run(1)
            comp.say("the text in the damaged slot")
            comp.tick()                                                 # written now, before the addon polls it
            slot = comp.stats["last_slot"]
            self.assertIn((mailbox.TEXT, b"the text in the damaged slot"), mailbox.read_slot(s.addons, slot)["records"])
            path = mailbox.slot_path(s.addons, slot)
            packet = bytearray(fontpack.read(fontpack.widths(path.read_bytes()))[0])
            packet[14] ^= 1                                             # the slot number stays readable, the CRC fails
            atomic_write(path, fontpack.build(bytes(packet), f"WBMail{slot}"))
            s.comp_running = True
            s.run(12)
            self.assertEqual(s.chat().count("> the text in the damaged slot"), 1, s.chat())
            self.assertTrue(any(t.startswith(f"the addon skipped slot {slot} (damaged") for t in logs), logs)
            self.assertEqual(comp.stats["skipped"], 1)
            self.assertNotIn(slot, comp.written)
            self.assertEqual(s.lua.globals()[b"WoWBridgeDB"][b"mail"][b"skipped"][1], slot)
            s.run(10)                                                   # later heartbeats name it again: nothing more
            self.assertEqual(s.chat().count("> the text in the damaged slot"), 1)
            self.assertEqual(comp.stats["skipped"], 1)
            s.slash("send still fine")                                  # the link goes on
            s.run(6)
            self.assertEqual(comp.sessions[comp.current].messages[-1][4], "still fine")
        finally:
            s.close()

    def test_message_ids_wrap(self):
        """the addon's next id is 65530: ten messages run 65530..65535, 1..4 and every one is acknowledged"""
        s, comp, logs, got = self.start(db=b"{ nextData = 65530 }")
        try:
            for i in range(10):
                s.slash(f"send wrap {i}")
                s.run(0.4)                                           # up before the next: no batch
            s.run(15)
            sess = comp.sessions[comp.current]
            self.assertEqual([m[1] for m in sess.messages][-10:], [65530, 65531, 65532, 65533, 65534, 65535, 1, 2, 3, 4])
            self.assertEqual(sess.ack, 4)
            s.slash("link")
            self.assertRegex(s.chat(), r"0 waiting, 10 acknowledged")
            self.assertEqual(s.lua.globals()[b"WoWBridgeDB"][b"nextData"], 5)
        finally:
            s.close()

    def test_reset_calls_the_lifecycle_hooks(self):
        """load with reset: OnUnload runs before the file, OnReload after it with what OnUnload returned; a whole addon:
        once around all of its files; without reset neither runs; a failing hook is reported, the code still runs"""
        s, comp, logs, got = self.start()

        def runs():
            return [t for k, t in got if k == "RUN"]
        try:
            g = s.lua.globals()
            foo = s.addons / "Foo"
            foo.mkdir()
            (foo / "Foo.toc").write_text("Core.lua\nMore.lua\n", encoding="utf-8")
            (foo / "Core.lua").write_text("FooCore = (FooCore or 0) + 1\n", encoding="utf-8")
            (foo / "More.lua").write_text("FooMore = (FooMore or 0) + 1\nreturn FooMore\n", encoding="utf-8")
            s.lua.execute(b"""WoWBridgeNS.Foo = {
                OnUnload = function() FooUnloads = (FooUnloads or 0) + 1 FooOrder = (FooOrder or "") .. "u" .. tostring(FooCore) return { kept = 7 } end,
                OnReload = function(v) FooReloads = (FooReloads or 0) + 1 FooOrder = FooOrder .. "r" .. tostring(FooCore) FooKept = v and v.kept end }""")
            comp.command("load Foo/Core.lua")
            s.run(6)
            self.assertRegex(runs()[-1], r"^\d+ ok @Interface/AddOns/Foo/Core\.lua \(\d+ B, [\d.]+ ms, 0 values\) \(OnUnload ok\) "
                                          r"\(OnReload ok\)$")
            self.assertEqual((g[b"FooUnloads"], g[b"FooReloads"], g[b"FooKept"], g[b"FooOrder"].decode()), (1, 1, 7, "unilr1"))
            comp.load("Foo/Core.lua", reset=False)
            s.run(6)
            self.assertRegex(runs()[-1], r"^\d+ ok @Interface/AddOns/Foo/Core\.lua \(\d+ B, [\d.]+ ms, 0 values\)$")
            self.assertEqual((g[b"FooUnloads"], g[b"FooReloads"], g[b"FooCore"]), (1, 1, 2))
            comp.command("load Foo")                                    # two files: unload before the first, reload after the last
            s.run(8)
            r = runs()[-2:]
            self.assertRegex(r[0], r"Core\.lua \(\d+ B, [\d.]+ ms, 0 values\) \(OnUnload ok\)$")
            self.assertRegex(r[1], r'More\.lua \(\d+ B, [\d.]+ ms, 1 value\): "1" \(OnReload ok\)$')
            self.assertEqual((g[b"FooUnloads"], g[b"FooReloads"], g[b"FooOrder"].decode()), (2, 2, "unilr1u2r3"))
            s.lua.execute(b"WoWBridgeNS.Foo.OnUnload = function() error('no way') end")
            comp.command("load Foo/Core.lua")
            s.run(6)
            self.assertRegex(runs()[-1], r"\(OnUnload error: .*no way\) \(OnReload ok\)$")
            self.assertEqual(g[b"FooCore"], 4)                          # the code ran all the same
            self.assertIsNone(g[b"FooKept"])                            # OnReload got nil: OnUnload returned nothing
        finally:
            s.close()

    def test_set_switches(self):
        """/wb set: forwardDebug off keeps errors from the companion, hotLoad off refuses code; both are kept per
        character and survive a /reload; autoLink off keeps the link from starting"""
        s, comp, logs, got = self.start()
        try:
            g = s.lua.globals()
            s.slash("set forwardDebug off")
            self.assertIn("forwardDebug = off (kept for this character)", s.chat())
            g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:1: quiet")
            g[b"print"](b"quiet too")
            s.run(6)
            self.assertEqual([t for k, t in got if k in ("ERR", "OUT")], [])
            self.assertEqual(len(g[b"__errors"]), 1)                    # the client's own handler still ran
            s.slash("set hotLoad off")
            comp.command("run return 1")
            s.run(6)
            self.assertRegex([t for k, t in got if k == "RUN"][-1], r"^\d+ error =run: hot loading is off \(/wb set hotLoad on\)$")
            s.slash("set nothing on")
            self.assertIn("usage: /wb set forwardDebug|hotLoad|autoLink|toasts on|off", s.chat())
            s2 = s.reload()
            s2.login()
            s2.run(8)
            settings = s2.lua.globals()[b"WoWBridgeDB"][b"settings"]
            self.assertEqual((settings[b"forwardDebug"], settings[b"hotLoad"]), (False, False))
            s2.lua.globals()[b"__error"](b"Interface/AddOns/Foo/Foo.lua:2: still quiet")
            s2.run(6)
            self.assertEqual([t for k, t in got if k == "ERR"], [])
            s2.slash("set forwardDebug on")
            s2.lua.globals()[b"__error"](b"Interface/AddOns/Foo/Foo.lua:3: loud")
            s2.run(6)
            self.assertEqual(len([t for k, t in got if k == "ERR"]), 1)
            s2.slash("set autoLink off")
            before = s2.chat().count("connected to the companion")     # the chat log is the client's, across reloads
            s3 = s2.reload()
            s3.login()
            s3.run(5)
            self.assertEqual(s3.chat().count("connected to the companion"), before)
            s3.slash("set autoLink on")                                 # starts the link right away
            s3.run(8)
            self.assertEqual(s3.chat().count("connected to the companion"), before + 1)
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class PanelAndLanguage(unittest.TestCase):
    """the settings panel, the minimap button, the frame's place, the link on and off, and the two languages"""

    def start(self, **kw):
        logs = []
        s = Session(self, **kw)
        s.comp = link.Companion(s.addons, clock=s.now, log=logs.append)
        s.comp.new_process("P1-100")
        s.login()
        s.run(8)
        return s

    def test_language_follows_the_client_then_the_setting(self):
        s = self.start(locale="zhCN")
        try:
            # 无限工坊's prefix: the mark and the name in gold
            self.assertIn(f"Media\\mark-wide:14:28|t |cffd8a85a无限工坊|r 开发组件 {link.VERSION} 已加载：/wb 打开面板", s.chat())
            self.assertIn("已连接无限工坊 App", s.chat())
            s.slash("lang en")
            self.assertIn("language: English", s.chat())
            s.slash("link")
            self.assertRegex(s.chat().splitlines()[-1], r"link online, mailbox slot \d+")
            self.assertEqual(s.lua.globals()[b"WoWBridgeDB"][b"settings"][b"lang"], b"enUS")
            s.slash("lang auto")                                    # back to the client's: Chinese, nothing kept
            self.assertIn("语言：跟随客户端", s.chat())
            s.slash("link")
            self.assertRegex(s.chat().splitlines()[-1], r"链路在线，信箱槽位 \d+")    # the state in words too
            self.assertIsNone(s.lua.globals()[b"WoWBridgeDB"][b"settings"][b"lang"])
            s.slash("lang klingon")
            self.assertIn("用法：/wb lang auto|zh|en", s.chat())
        finally:
            s.close()

    def test_link_off_and_on(self):
        s = self.start()
        try:
            ns = s.ns
            s.slash("off")
            self.assertEqual(ns[b"Link"][b"State"](), b"off")
            self.assertFalse(ns[b"Frame"][b"IsShown"]())
            self.assertIs(s.lua.globals()[b"WoWBridgeDB"][b"settings"][b"autoLink"], False)
            self.assertIn("link stopped: the frame is hidden", s.chat())
            s.run(3)
            self.assertFalse(ns[b"Frame"][b"IsShown"]())             # nothing draws it again while off
            s.slash("on")
            s.run(1)
            self.assertNotEqual(ns[b"Link"][b"State"](), b"off")
            self.assertTrue(ns[b"Frame"][b"IsShown"]())
            self.assertIn("link started", s.chat())
        finally:
            s.close()

    def test_frame_place(self):
        s = self.start()
        try:
            ns, g = s.ns, s.lua.globals()
            s.slash("unlock")
            mover = g[b"WoWBridgeMover"]
            self.assertTrue(mover[b"shown"])
            self.assertTrue(ns[b"Frame"][b"IsUnlocked"]())
            bus = g[b"WoWBridgeBus"]
            bus[b"left"], bus[b"top"] = 300.4, 1080 - 200.6           # where a drag left it (bus units: pixels)
            mover[b"scripts"][b"OnDragStop"](mover)
            settings = g[b"WoWBridgeDB"][b"settings"]
            self.assertEqual((settings[b"frameX"], settings[b"frameY"]), (300, 201))   # whole pixels, kept
            self.assertEqual(tuple(ns[b"Frame"][b"Offset"]()), (300, 201))
            s.slash("lock")
            self.assertFalse(mover[b"shown"])
            self.assertIn("frame locked at 300, 201", s.chat())
            s.slash("reset")
            self.assertEqual(tuple(ns[b"Frame"][b"Offset"]()), (0, 0))
            self.assertEqual((settings[b"frameX"], settings[b"frameY"]), (0, 0))
        finally:
            s.close()

    def test_room_for_the_biggest_frame(self):
        s = self.start()
        try:
            ns, g = s.ns, s.lua.globals()
            self.assertEqual(tuple(ns[b"Frame"][b"Room"]()), (130 * 4, 34 * 4))      # a long message's parts, 128 x 32
            s.slash("unlock")
            bus, mover = g[b"WoWBridgeBus"], g[b"WoWBridgeMover"]
            fw, fh = bus[b"w"], bus[b"h"]
            self.assertLess(fw, 130 * 4)                                              # a smaller frame is up now
            self.assertEqual(tuple(bus[b"clampInsets"].values()), (0, 130 * 4 - fw, 0, fh - 34 * 4))   # the room stays on screen
            self.assertTrue(bus[b"clamped"])
            room = mover[b"point"][2]                                                 # the mouse area covers the room
            self.assertEqual((room[b"w"], room[b"h"]), (130 * 4, 34 * 4))
            bus[b"left"], bus[b"top"] = 1900, 1080 - 1070                             # dropped in the bottom-right corner
            mover[b"scripts"][b"OnDragStop"](mover)
            settings = g[b"WoWBridgeDB"][b"settings"]
            self.assertEqual((settings[b"frameX"], settings[b"frameY"]), (1920 - 130 * 4, 1080 - 34 * 4))
            self.assertEqual(tuple(ns[b"Frame"][b"Offset"]()), (1920 - 130 * 4, 1080 - 34 * 4))
            s.run(2)                                                                  # frames of other sizes: the place holds
            self.assertEqual(tuple(ns[b"Frame"][b"Offset"]()), (1920 - 130 * 4, 1080 - 34 * 4))
            s.slash("lock")                                       # the drag's clamp goes: grown for a small frame, it
            self.assertFalse(bus[b"clamped"])                     # pushed a long message's parts aside
            self.assertEqual(tuple(bus[b"clampInsets"].values()), (0, 0, 0, 0))
        finally:
            s.close()

    def test_wuxiankit_while_the_game_runs_it(self):
        """WuxianKit (无限工坊's standard library, an addon of its own): without it nothing changes (no button in the
        panel's head, the minimap button's tooltip and /wb's help as they were, /wb kit says it is not there); with it the
        head has a button to its window (the panel gives way), Shift-click on the minimap button and /wb kit [page] open
        it, and an error of its own goes to the error handler"""
        s = self.start()
        try:
            g = s.lua.globals()
            button = g[b"WoWBridgeMinimapButton"]
            enter = lambda: (button[b"scripts"][b"OnEnter"](button), list(g[b"GameTooltip"][b"lines"].values()))[1]
            button.Click(button, "LeftButton")
            panel = g[b"WoWBridgePanel"]
            self.assertTrue(panel[b"shown"])
            self.assertFalse(panel[b"kit"][b"shown"])
            self.assertEqual(enter()[-3:], [b"Left-click: the panel", b"Right-click: the debug output",
                                            b"Drag: move this button"])
            g[b"__shift"] = True
            button.Click(button, "LeftButton")                       # Shift or not: the panel, as before
            self.assertFalse(panel[b"shown"])
            s.slash("kit")
            self.assertIn("WuxianKit (the workshop's standard library) is not running", s.chat())
            s.slash("help")
            self.assertNotIn("kit", s.chat().splitlines()[-1])
            g[b"__shift"] = False

            s.lua.execute(b"__opened = {} WuxianKit = { Window = function(self, page) "
                          b"__opened[#__opened + 1] = page or 'last' return true end }")
            button.Click(button, "LeftButton")
            s.run(1.1)                                                # the panel's next refresh
            self.assertTrue(panel[b"kit"][b"shown"])
            self.assertEqual(panel[b"kit"][b"text"], b"Kit")
            panel[b"kit"].Click(panel[b"kit"])
            self.assertFalse(panel[b"shown"])                         # the panel gives way to it
            s.slash("kit Tune")
            g[b"__shift"] = True
            button.Click(button, "LeftButton")
            self.assertFalse(panel[b"shown"])
            g[b"__shift"] = False
            self.assertEqual(list(g[b"__opened"].values()), [b"last", b"tune", b"last"])
            self.assertEqual(enter()[-4:], [b"Left-click: the panel", b"Right-click: the debug output",
                                            b"Shift-click: WuxianKit's window", b"Drag: move this button"])
            s.slash("help")
            self.assertIn("| kit [page] (WuxianKit's window)", s.chat().splitlines()[-1])
            s.slash("lang zh")
            button.Click(button, "LeftButton")
            self.assertEqual(panel[b"kit"][b"text"].decode(), "标准库")
            s.lua.execute(b"WuxianKit.Window = function() error('kit broke') end")
            n = len(g[b"__errors"])
            s.slash("kit")                                            # its error: to the handler, not into /wb
            self.assertEqual(len(g[b"__errors"]), n + 1)
            self.assertIn(b"kit broke", g[b"__errors"][n + 1])
        finally:
            s.close()

    def test_panel_and_minimap_button(self):
        s = self.start()
        try:
            g = s.lua.globals()
            button = g[b"WoWBridgeMinimapButton"]
            self.assertTrue(button[b"shown"])
            button.Click(button, "LeftButton")                       # left-click: the panel, on 概览
            panel = g[b"WoWBridgePanel"]
            self.assertTrue(panel[b"shown"])
            pages = panel[b"pages"]
            tabs = list(panel[b"tabs"][b"tabs"].values())
            self.assertEqual([t[b"id"] for t in tabs], [b"overview", b"settings", b"diag"])
            self.assertEqual([t[b"label"][b"text"] for t in tabs], [b"Overview", b"Settings", b"Diagnostics"])
            self.assertEqual([pages[t[b"id"]][b"shown"] for t in tabs], [True, False, False])
            self.assertEqual([t[b"mark"][b"shown"] for t in tabs], [True, False, False])   # the gold bar under 概览
            self.assertEqual(panel[b"titleText"][b"text"], b"Wuxian Workshop")
            self.assertEqual(panel[b"version"][b"text"].decode(), f"Developer addon · WoWBridge {link.VERSION}")
            self.assertEqual(panel[b"state"][b"text"], b"Connected to the app")
            self.assertEqual(tuple(panel[b"dot"][b"vertex"].values())[:3], (0x6f / 255, 0xcf / 255, 0x97 / 255))   # green
            self.assertTrue(panel[b"link"][b"checked"])
            self.assertTrue(panel[b"link"][b"checkedTexture"][b"texture"].endswith(b"Media\\stud"))   # ticked: the diamond
            self.assertEqual(panel[b"runs"][b"text"], b"Agent code: nothing run since the last reload")
            self.assertEqual(panel[b"errors"][b"text"], b"Lua errors: 0 \xc2\xb7 warnings: 0 \xc2\xb7 blocked: 0")
            self.assertFalse(panel[b"act"][b"shown"])                 # nothing to do
            s.ns[b"Agent"][b"stats"][b"runs"] = 3
            s.ns[b"Agent"][b"stats"][b"last"] = s.lua.table_from({b"name": b"=run", b"ok": False, b"at": g[b"GetTime"]() - 75})
            s.run(1.1)
            self.assertEqual(panel[b"runs"][b"text"], b"Agent code ran 3 times, 0 failed")
            self.assertEqual(panel[b"last"][b"text"].decode(), "Last: a Lua snippet \xb7 |cfff28b82error|r \xb7 1 min ago")
            tabs[2].Click(tabs[2])                                    # 诊断: the link's numbers
            self.assertEqual([pages[t[b"id"]][b"shown"] for t in tabs], [False, False, True])
            self.assertRegex(panel[b"st1"][b"text"].decode(), r"^Link: \|cff6fcf97online\|r")   # the state in its colour
            self.assertRegex(panel[b"st2"][b"text"].decode(), r"^Mailbox slots left: \d+ \(about [\d.]+ h when idle\)$")
            self.assertRegex(panel[b"st3"][b"text"].decode(), r"^Waiting \d+ \xb7 acknowledged \d+ \xb7 heartbeat 30 s$")
            tabs[1].Click(tabs[1])                                    # 设置
            self.assertTrue(pages[b"settings"][b"shown"])
            zh = [b for b in panel[b"langs"].values() if b[b"setting"] == b"zhCN"][0]
            auto = [b for b in panel[b"langs"].values() if b[b"setting"] == b"auto"][0]
            self.assertEqual(auto[b"text"], b"Auto")                 # the short name on the button
            self.assertTrue(auto[b"selected"])
            zh.Click(zh)                                              # the language buttons
            self.assertEqual(panel[b"titleText"][b"text"].decode(), "无限工坊")
            self.assertEqual(panel[b"version"][b"text"].decode(), f"开发组件 · WoWBridge {link.VERSION}")
            self.assertEqual([t[b"label"][b"text"].decode() for t in tabs], ["概览", "设置", "诊断"])
            self.assertEqual(panel[b"state"][b"text"].decode(), "已连接 App")
            self.assertEqual(auto[b"text"].decode(), "自动")
            self.assertFalse(zh[b"enabled"])                          # the one in force: marked, not clickable
            self.assertTrue(zh[b"selected"])
            self.assertFalse(auto[b"selected"])
            s.slash("unlock")
            s.run(1.1)                                                # the panel's next refresh
            self.assertEqual(panel[b"unlock"][b"kind"], b"primary")   # unlocked: locking is the button to press
            self.assertTrue(panel[b"act"][b"shown"])                  # and 概览 says so, with the button that does it
            self.assertEqual(panel[b"act"][b"text"].decode(), "锁定位置")
            panel[b"act"].Click(panel[b"act"])
            self.assertFalse(s.ns[b"Frame"][b"IsUnlocked"]())
            self.assertIsNone(panel[b"unlock"][b"kind"])
            self.assertFalse(panel[b"act"][b"shown"])
            panel[b"hot"][b"checked"] = False
            panel[b"hot"].Click(panel[b"hot"])                        # hot loading off: 概览 offers to turn it on
            self.assertEqual((panel[b"act"][b"what"], panel[b"act"][b"text"].decode()), (b"hotload", "打开"))
            self.assertEqual(panel[b"notice"][b"text"].decode(), "热加载已关闭：Agent 发来的代码不会运行。")
            panel[b"act"].Click(panel[b"act"])
            self.assertIs(g[b"WoWBridgeDB"][b"settings"][b"hotLoad"], True)
            self.assertTrue(panel[b"hot"][b"checked"])
            panel[b"minimap"][b"checked"] = False
            panel[b"minimap"].Click(panel[b"minimap"])                # hide the minimap button
            self.assertFalse(button[b"shown"])
            self.assertIs(g[b"WoWBridgeDB"][b"settings"][b"minimapHide"], True)
            panel[b"link"][b"checked"] = False
            panel[b"link"].Click(panel[b"link"])                      # the link off from the panel
            self.assertEqual(s.ns[b"Link"][b"State"](), b"off")
            s.slash("")                                               # /wb alone: the panel again (closes it)
            self.assertFalse(panel[b"shown"])
            s.slash("")                                               # and opens it on the tab shown last
            self.assertTrue(panel[b"shown"])
            self.assertTrue(pages[b"settings"][b"shown"])
            self.assertEqual((panel[b"siteLabel"][b"text"].decode(), panel[b"site"][b"text"]),
                             ("官网", b"wuxianwow.com/workshop"))            # the website, under every tab
            box = panel[b"copyBox"]
            panel[b"copy"].Click(panel[b"copy"])                      # 复制网址: the address in a box, selected
            self.assertEqual((box[b"shown"], box[b"text"], box[b"highlighted"], box[b"focused"]),
                             (True, b"https://wuxianwow.com/workshop", True, True))
            self.assertFalse(panel[b"site"][b"shown"])
            box[b"text"] = b"typed over"
            box[b"scripts"][b"OnTextChanged"](box, True)              # read only: it comes back
            self.assertEqual(box[b"text"], b"https://wuxianwow.com/workshop")
            box[b"scripts"][b"OnEscapePressed"](box)                  # Esc: the line again
            self.assertEqual((box[b"shown"], panel[b"site"][b"shown"]), (False, True))
            categories = list(g[b"__categories"].values())            # Options > AddOns has its page
            self.assertEqual([c[b"name"].decode() for c in categories], ["Wuxian Workshop · WoWBridge"])
            self.assertEqual(list(g[b"__errors"].values()), [])       # and nothing failed on the way
        finally:
            s.close()


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class PlayerErrors(unittest.TestCase):
    """!WuxianWorkshop on a player's client (no WoWBridge): the error collection in WuxianWorkshopDB and /wxw"""

    def test_signature_dedup_addon_and_stack(self):
        s = Session(self, early=True, bridge=False, locale="zhCN")
        try:
            g = s.lua.globals()
            g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:12: attempt to call a nil value")   # before the saved variables
            s.login()
            s.run(1)
            g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:12: attempt to call a nil value")
            g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:99: attempt to call a nil value")   # another line: the same signature
            g[b"__stack"] = b"[string \"@Interface/AddOns/Bar/Bar.lua\"]:1: in function `Boom'\n"
            g[b"__error"](b"Interface/AddOns/Bar/Bar.lua:1: boom 42")
            g[b"__fire"](b"LUA_WARNING", 0, b"Couldn't open Interface/AddOns/Baz/Missing.lua")
            g[b"__fire"](b"ADDON_ACTION_BLOCKED", b"Qux", b"ReloadUI()")
            s.run(1)
            errors = {sig.decode(): e for sig, e in g[b"WuxianWorkshopDB"][b"errors"].items()}
            self.assertEqual(sorted(errors), [" Couldn't open Interface/AddOns/Baz/Missing.lua",   # the warning type (0) removed too
                                              "ADDON_ACTION_BLOCKED Qux ReloadUI()", "Interface/AddOns/Bar/Bar.lua:: boom ",
                                              "Interface/AddOns/Foo/Foo.lua:: attempt to call a nil value"])
            foo = errors["Interface/AddOns/Foo/Foo.lua:: attempt to call a nil value"]
            self.assertEqual((foo[b"count"], foo[b"addon"], foo[b"kind"], foo[b"build"]), (3, b"Foo", b"ERR", b"1.60.1.70235"))
            self.assertEqual(foo[b"message"], b"Interface/AddOns/Foo/Foo.lua:12: attempt to call a nil value")   # the first one
            self.assertIn(b"in function `Broken'", foo[b"stack"])
            self.assertLessEqual(foo[b"first"], foo[b"last"])
            bar = errors["Interface/AddOns/Bar/Bar.lua:: boom "]
            self.assertEqual((bar[b"count"], bar[b"addon"]), (1, b"Bar"))
            self.assertIn(b"in function `Boom'", bar[b"stack"])
            self.assertEqual(errors[" Couldn't open Interface/AddOns/Baz/Missing.lua"][b"kind"], b"WARN")
            self.assertEqual(errors[" Couldn't open Interface/AddOns/Baz/Missing.lua"][b"addon"], b"Baz")
            self.assertEqual(errors["ADDON_ACTION_BLOCKED Qux ReloadUI()"][b"addon"], b"Qux")
            self.assertEqual(len(g[b"__errors"]), 4)                    # the client's own handler saw every error
            self.assertEqual(g[b"WuxianWorkshopDB"][b"settings"][b"report"], None)   # not decided yet
        finally:
            s.close()

    def test_cap_and_rate_limit(self):
        """more than 10 errors in a second: collecting pauses for a second and counts what it missed; more than 200
        signatures: the least recently seen goes"""
        s = Session(self, early=True, bridge=False, locale="zhCN")
        try:
            g = s.lua.globals()
            s.login()
            for i in range(15):
                g[b"__error"](f"Interface/AddOns/Foo/Foo.lua:1: flood {'x' * i}".encode())
            db = g[b"WuxianWorkshopDB"]
            self.assertEqual(count(db[b"errors"]), 10)
            self.assertEqual(db[b"dropped"], 5)
            s.run(0.5)
            g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:1: still paused")
            self.assertEqual((count(db[b"errors"]), db[b"dropped"]), (10, 6))
            s.run(0.6)
            g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:1: collected again")
            self.assertEqual((count(db[b"errors"]), db[b"dropped"]), (11, 6))
            def code(i):                                                # letters: digits do not count in a signature
                return chr(97 + i // 26) + chr(97 + i % 26)
            for i in range(250):                                        # 2 a second, well under the limit
                g[b"__error"](f"Interface/AddOns/Foo/Foo.lua:1: distinct {code(i)}".encode())
                s.run(0.5)
            errors = {sig.decode() for sig in db[b"errors"].keys()}
            self.assertEqual(len(errors), 200)
            self.assertIn("Interface/AddOns/Foo/Foo.lua:: distinct " + code(249), errors)
            self.assertIn("Interface/AddOns/Foo/Foo.lua:: distinct " + code(50), errors)
            self.assertNotIn("Interface/AddOns/Foo/Foo.lua:: distinct " + code(49), errors)   # the oldest went first
            self.assertNotIn("Interface/AddOns/Foo/Foo.lua:: collected again", errors)
            s.wxw("report status")
            self.assertIn("已收集 200 种报错（共 200 次），因刷屏跳过 6 条", s.chat())
            long = "Interface/AddOns/Foo/Foo.lua:1: " + "z" * 300                     # a signature is at most 200 bytes
            g[b"__error"](long.encode())
            g[b"__error"]((long + "tail").encode())
            self.assertEqual([e[b"count"] for e in db[b"errors"].values() if e[b"message"].startswith(b"Interface/AddOns/Foo/Foo.lua:1: zzz")], [2])
        finally:
            s.close()

    def test_report_switch_and_clear(self):
        """report off: collected in memory, nothing in the saved variables; on again: saved, with what came meanwhile;
        the setting survives a /reload; /wxw clear empties it all"""
        s = Session(self, early=True, bridge=False, locale="zhCN")
        try:
            g = s.lua.globals()
            s.login()
            s.wxw("report")
            self.assertIn("报错上报：未设置（无限工坊首次启动时会询问）；已收集 0 种报错", s.chat())
            s.wxw("report off")
            db = g[b"WuxianWorkshopDB"]
            self.assertEqual(db[b"settings"][b"report"], False)
            g[b"__error"](b"Interface/AddOns/Foo/Foo.lua:1: unsaved")
            self.assertEqual(count(db[b"errors"]), 0)
            self.assertIn("报错上报：关（只留在内存）", s.chat())
            s.wxw("report status")
            self.assertIn("已收集 1 种报错（共 1 次）", s.chat())              # in memory all the same
            s2 = s.reload()
            s2.login()
            db2 = s2.lua.globals()[b"WuxianWorkshopDB"]
            self.assertEqual((db2[b"settings"][b"report"], count(db2[b"errors"])), (False, 0))
            s2.lua.globals()[b"__error"](b"Interface/AddOns/Foo/Foo.lua:2: unsaved too")
            s2.wxw("report on")
            self.assertEqual(db2[b"settings"][b"report"], True)
            self.assertEqual([e[b"message"] for e in db2[b"errors"].values()], [b"Interface/AddOns/Foo/Foo.lua:2: unsaved too"])
            s2.lua.globals()[b"__error"](b"Interface/AddOns/Foo/Foo.lua:3: saved")
            self.assertEqual(count(db2[b"errors"]), 2)
            s3 = s2.reload()
            s3.login()
            db3 = s3.lua.globals()[b"WuxianWorkshopDB"]
            self.assertEqual((db3[b"settings"][b"report"], count(db3[b"errors"])), (True, 2))
            s3.lua.globals()[b"__error"](b"Interface/AddOns/Foo/Foo.lua:3: saved")   # counted onto the saved entry
            self.assertEqual([e[b"count"] for e in db3[b"errors"].values() if e[b"message"].endswith(b"saved")], [2])
            s3.wxw("clear")
            self.assertEqual((count(db3[b"errors"]), db3[b"dropped"]), (0, 0))
            self.assertIn("已清空收集的报错", s3.chat())
            s3.wxw("help")
            self.assertIn("用法：/wxw report on|off|status", s3.chat())
        finally:
            s.close()


class ClientInHello(unittest.TestCase):
    def test_the_hello_says_the_client_and_fits_its_frame(self):
        s = Session(self)
        comp = link.Companion(s.addons, clock=s.now, log=lambda *a: None, ping_every=4)
        s.comp = comp
        got = []
        comp.on_client = lambda version, interface: got.append((version, interface))
        try:
            comp.new_process("P1-100")
            s.login()
            s.run(6)
            self.assertEqual(got[:1], [("1.60.1.70235", 16001)])           # the mock client's GetBuildInfo
            longest = (b"HELLO;v=10.10.10;s=65535;k=4096;m=65535;p=P4294967295-1791331220;pw=7680;ph=4320;es=0.533333;"
                       b"gv=10.160.10.1234567;i=1016010;bad=" + b",".join(str(4096 - i).encode() for i in range(8)))
            ns = s.ns
            built = ns[b"Frame"][b"Build"](64, 16, 1, 0, ns[b"Codec"][b"TYPE_CONTROL"], 65535, 65535, longest)
            first = built[0] if isinstance(built, tuple) else built
            self.assertIsNotNone(first, built)
        finally:
            s.close()


class NewGameProcess(unittest.TestCase):
    def test_first_login_starts_the_mailbox_over(self):
        """the saved variables still hold the last game process: the first HELLO says slot 40 until proc.ttf has been
        read, then slot 1. The companion has to follow the addon down to slot 1"""
        logs = []
        s = Session(self, db=b'{ mail = { proc = "P0-50", next = 40 } }')
        comp = link.Companion(s.addons, clock=s.now, log=logs.append, ping_every=4)
        s.comp = comp
        try:
            comp.new_process("P1-100")
            comp.say("你好")
            s.login()
            s.run(10)
            self.assertIn("connected to the companion (mailbox slot 1)", s.chat(), logs)
            self.assertIn("> 你好", s.chat())
            self.assertIn("reads from slot 1 now", " ".join(logs))
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
