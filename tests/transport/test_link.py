"""The companion's bookkeeping (wuxianworkshop/transport/link.py with the AgentCommands mixin of agent/commands.py):
handshake, cumulative acknowledgements, fragments, pings, reload, hot loading and the mailbox slots on disk; the v0.8
revisions: typed messages, a skipped slot's records sent again, wrapping message ids and the reset flag."""
import os
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop.core import frame as F, mailbox as MB
from wuxianworkshop.transport import link


class Companion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addons = Path(self.tmp.name)
        (self.addons / "WoWBridge").mkdir()
        MB.install(self.addons, pool=16)
        self.t = 100.0
        self.log = []
        self.c = link.Companion(self.addons, clock=lambda: self.t, log=self.log.append)
        self.c.new_process("P1-1")

    def tearDown(self):
        self.tmp.cleanup()

    def frame(self, ftype, msg, payload, sid=5, part=0, parts=1):
        self.c.on_frame(ftype, sid, msg, payload, part, parts)

    def test_the_hello_says_the_client(self):
        got = []
        self.c.on_client = lambda version, interface: got.append((version, interface))
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.9.3;s=5;k=1;m=1;p=P1-1;gv=1.61.0.70500;i=16100")
        self.assertEqual((got, self.c.client), ([("1.61.0.70500", 16100)], ("1.61.0.70500", 16100)))
        self.assertIn("addon 0.9.3, client 1.61.0.70500 (Interface 16100)", " ".join(self.log))
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.9.2;s=6;k=1;m=1", sid=6)  # an older addon: nothing said
        self.assertEqual(len(got), 1)

    def test_handshake_and_cumulative_ack(self):
        self.c.tick()
        self.assertIsNone(MB.read_slot(self.addons, 1))                 # nothing written before a HELLO
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.5.0;s=5;k=3;m=10")
        self.c.tick()
        p = MB.read_slot(self.addons, 3)                                  # written where the addon reads
        self.assertEqual((p["session"], p["ack"], p["records"][0][0]), (5, 9, MB.WELCOME))
        for msg in (10, 12):                                              # 11 is missing: ack stops at 10
            self.frame(F.TYPE_DATA, msg, b"x%d" % msg)
        self.t += 1.0
        self.c.tick()
        self.assertEqual(MB.read_slot(self.addons, 4)["ack"], 10)
        self.frame(F.TYPE_DATA, 11, b"x11")
        self.frame(F.TYPE_DATA, 11, b"x11")                               # shown again: a duplicate
        self.t += 1.0
        self.c.tick()
        self.assertEqual(MB.read_slot(self.addons, 5)["ack"], 12)
        self.assertEqual(self.c.sessions[5].dups, 1)
        self.t += 0.5
        self.c.tick()
        self.assertIsNone(MB.read_slot(self.addons, 6))                   # nothing new: no packet until the heartbeat
        self.t += 4.0
        self.frame(F.TYPE_DATA, 12, b"x12")                               # frames keep coming (no packets without)
        self.t += 1.0
        self.c.tick()
        self.assertEqual(MB.read_slot(self.addons, 6)["records"][0][0], MB.HEARTBEAT)

    def test_ping_and_link_down(self):
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.5.0;s=5;k=1;m=1")
        self.c.tick()
        self.t += 1
        self.frame(F.TYPE_CONTROL, 2, b"HB;n=1;k=2;m=1;a=0;q=0")       # online: pings start
        self.c.tick()
        self.assertIsNone(MB.read_slot(self.addons, 2))                   # a ping rides with the next packet
        self.t += 4
        self.c.tick()
        cmd = MB.read_slot(self.addons, 2)["records"][0]
        self.assertEqual(cmd, (MB.COMMAND, b"ping 1"))
        self.t += 1.5
        self.frame(F.TYPE_CONTROL, 3, b"PONG;id=1;k=3")
        self.assertEqual(self.c.rtts, [1.5])
        self.t += 7
        self.c.tick()
        self.assertFalse(self.c.link_up)
        self.assertIn("link down", self.log[-1] if "link down" in self.log[-1] else " ".join(self.log))

    def test_restart_learns_the_slot_from_a_heartbeat(self):
        """a new companion in the same game process: no HELLO comes, the heartbeat says where the addon reads"""
        self.frame(F.TYPE_CONTROL, 9, b"HB;n=40;k=57;m=300;a=299;q=0")
        self.c.tick()
        p = MB.read_slot(self.addons, 57)
        self.assertEqual((p["records"][0][0], p["ack"]), (MB.WELCOME, 299))   # welcomed again, ack from "m"
        self.assertEqual(self.c.slot, 58)
        self.assertIn("session 5 online (it was before this companion started)", self.log)
        self.assertIn((MB.COMMAND, b"ping 1"), p["records"])                # pings ride along from the start
        self.t += 10
        self.c.tick()
        self.assertIsNone(MB.read_slot(self.addons, 58))                      # the 15 s heartbeat, not the 5 s one
        self.t += 4
        self.frame(F.TYPE_CONTROL, 10, b"HB;n=41;k=58;m=300;a=299;q=0")     # frames keep coming: the link is up
        self.t += 1
        self.c.tick()
        self.assertIn((MB.COMMAND, b"ping 2"), MB.read_slot(self.addons, 58)["records"])

    def test_nothing_is_written_while_no_frames_come(self):
        """the addon stops reading (the character screen, minimized, a long loading screen): no packets while no frames
        come (a heartbeat every 15 s used up 720 mailbox slots in a three-hour outage, each with a ping the addon then
        answered at once, which held up the agent's code for minutes); what waits goes out when frames come again"""
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.9.2;s=5;k=1;m=1")
        self.c.tick()                                                         # slot 1: the WELCOME
        self.t += 1
        self.frame(F.TYPE_CONTROL, 2, b"HB;n=1;k=2;m=1;a=0;q=0")
        self.t += 13
        self.frame(F.TYPE_CONTROL, 3, b"HB;n=2;k=2;m=1;a=0;q=0")
        self.t += 1
        self.c.tick()                                                         # slot 2: the heartbeat and ping 1
        self.assertIn((MB.COMMAND, b"ping 1"), MB.read_slot(self.addons, 2)["records"])
        for _ in range(720):                                                  # three hours without a frame
            self.t += 15
            self.c.tick()
        self.assertFalse(self.c.link_up)
        self.assertEqual(self.c.slot, 3)                                      # not one slot used meanwhile
        self.c.say("after the outage")                                        # waits for the frames
        self.t += 1
        self.c.tick()
        self.assertIsNone(MB.read_slot(self.addons, 3))
        self.c.pings["99"] = self.t - 3600                                    # an old one that never came back
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.9.2;s=6;k=3;m=1", sid=6)    # back (logged in again)
        self.c.tick()
        recs = MB.read_slot(self.addons, 3)["records"]
        self.assertIn(MB.WELCOME, [k for k, _ in recs])
        self.assertIn((MB.TEXT, b"after the outage"), recs)
        self.t += 1
        self.frame(F.TYPE_CONTROL, 2, b"HB;n=1;k=4;m=1;a=0;q=0", sid=6)
        self.t += 14
        self.frame(F.TYPE_CONTROL, 3, b"HB;n=2;k=4;m=1;a=0;q=0", sid=6)
        self.t += 1
        self.c.tick()
        recs = MB.read_slot(self.addons, 4)["records"]
        self.assertEqual([r for r in recs if r[0] == MB.COMMAND], [(MB.COMMAND, b"ping 2")])   # one fresh ping
        self.assertEqual(sorted(self.c.pings), ["2"])                        # the unanswered old ones forgotten

    def test_addon_starts_over_at_slot_1(self):
        """first login in a new game process: the HELLO shows the slot saved in the last process until the addon has
        read proc.ttf, then slot 1. The companion follows it down and empties what it wrote up there"""
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=5;k=668;m=1;p=?")
        self.c.say("hello")
        self.c.tick()                                                     # WELCOME and the text into slot 668
        self.assertIsNotNone(MB.read_slot(self.addons, 668))
        self.t += 1
        self.frame(F.TYPE_CONTROL, 2, b"HELLO;v=0.6.0;s=5;k=1;m=1;p=P1-1")
        self.c.tick()
        self.assertIsNone(MB.read_slot(self.addons, 668))                # emptied: never read out of turn
        recs = MB.read_slot(self.addons, 1)["records"]
        self.assertEqual(recs[0][0], MB.WELCOME)
        self.assertIn((MB.TEXT, b"hello"), recs)
        self.assertEqual(self.c.slot, 2)
        self.t += 1
        self.frame(F.TYPE_CONTROL, 3, b"HB;n=1;k=2;m=1;a=0;q=0")       # read; 2 is just next, nothing to undo
        self.t += 5
        self.c.tick()
        self.assertEqual(self.c.slot, 3)
        self.assertEqual(" ".join(self.log).count("reads from slot"), 1)

    def test_debug_messages(self):
        """the addon's debug output: errors with their stack, print() lines, warnings, repeats; escapes removed"""
        got = []
        self.c.on_debug = lambda kind, text: got.append((kind, text))
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=5;k=1;m=1")
        self.frame(F.TYPE_DATA, 1, b"ERR Interface/AddOns/Foo/Foo.lua:3: oops\n[string]:3: in main chunk")
        self.frame(F.TYPE_DATA, 2, b"OUT hello |cffff0000red|r\nsecond line")
        self.frame(F.TYPE_DATA, 3, b"ERR Interface/AddOns/Foo/Foo.lua:3: oops\n(4 more times)")
        self.frame(F.TYPE_DATA, 4, b"WARN 0 Couldn't open Interface/AddOns/Foo/Bar.lua")
        self.frame(F.TYPE_DATA, 5, b"just a message")
        self.assertEqual(got[0], ("ERR", "Interface/AddOns/Foo/Foo.lua:3: oops\n[string]:3: in main chunk"))
        self.assertEqual(got[1], ("OUT", "hello red\nsecond line"))
        self.assertEqual(len(got), 4)
        d = self.c.stats["debug"]
        self.assertEqual((d["err"], d["repeats"], d["out"], d["warn"]), (1, 1, 1, 1))
        log = "\n".join(self.log)
        self.assertIn("[lua error] Interface/AddOns/Foo/Foo.lua:3: oops", log)
        self.assertIn("[print] second line", log)
        self.assertEqual(self.c.sessions[5].ack, 5)                       # debug messages are acknowledged like any

    def test_reload_command(self):
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=5;k=1;m=1")
        self.c.tick()                                                     # WELCOME into slot 1
        self.c.command("reload")
        self.t += 1
        self.c.tick()
        cmds = [r for r in MB.read_slot(self.addons, 2)["records"] if r[0] == MB.COMMAND]
        self.assertRegex(cmds[0][1], rb"^reload \d+$")                    # with a nonce
        self.t += 4
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=5;k=3;m=1", sid=6)   # a new session: the UI reloaded
        self.assertIn("reload done: session 5 -> 6, 5.0 s after the command", self.log)
        self.assertEqual(self.c.stats["reloads"], 1)
        self.c.command("reload")                                          # this time nobody clicks the button
        self.t += 601
        self.c.tick()
        self.assertIn("no reload 600 s after the command (the button was not clicked)", self.log)

    def test_heartbeat_by_addon_version(self):
        """addon 0.7.0 on: a heartbeat every 15 s (it goes offline after three missed); an older one: every 5 s"""
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.7.0;s=5;k=1;m=1")
        self.c.tick()
        welcome = MB.read_slot(self.addons, 1)["records"][0]
        self.assertEqual(welcome[0], MB.WELCOME)
        self.assertIn(b";hb=15", welcome[1])
        for i in range(28):                                               # 14 s: nothing due
            self.t += 0.5
            if i % 4 == 3:
                self.frame(F.TYPE_CONTROL, 2, b"HB;n=1;k=2;m=1;a=0;q=0")   # the addon's frames keep coming
            self.c.tick()
        self.assertEqual(self.c.slot, 2)
        self.t += 1.5
        self.c.tick()                                                     # 15.5 s: the heartbeat
        self.assertEqual(self.c.slot, 3)
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=6;k=3;m=1", sid=6)   # an older addon
        self.t += 1
        self.c.tick()
        welcome = [r for r in MB.read_slot(self.addons, 3)["records"] if r[0] == MB.WELCOME][0]
        self.assertIn(b";hb=5", welcome[1])

    def test_slot_warnings(self):
        """400 and 100 slots left: a note for the agent; none left: mailbox full, restart the game"""
        got = []
        self.c.on_debug = lambda kind, text: got.append((kind, text))
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.7.0;s=5;k=3696;m=1")
        self.c.tick()                                                     # into 3696: 400 left after it
        slots = [t for k, t in got if k == "SLOTS"]
        self.assertEqual(len(slots), 1)
        self.assertTrue(slots[0].startswith("mailbox: 400 slots left in this game process (about 1.7 h at a heartbeat "
                                            "every 15 s); when they are gone, only a full restart"), slots)
        self.c.slot = 3996
        for text in ("a", "b", "c"):                                      # 3996, 3997 (100 left after it), 3998
            self.c.say(text)
            self.t += 1
            self.c.tick()
        slots = [t for k, t in got if k == "SLOTS"]
        self.assertEqual(len(slots), 2)
        self.assertTrue(slots[1].startswith("mailbox: 100 slots left"), slots)
        self.c.slot = 4096
        for text in ("d", "e"):                                           # the last slot, then none
            self.c.say(text)
            self.t += 1
            self.c.tick()
        self.assertTrue(got[-1][1].startswith("mailbox full: all 4096 slots of this game process are used"), got)

    def test_load_addon_and_watch(self):
        """load <addon>: its Lua in .toc order, XML <Script> / <Include> followed; WoWBridge's own refused;
        watch: a saved file is loaded again once its time stamp held for a check"""
        got = []
        self.c.on_debug = lambda kind, text: got.append((kind, text))
        foo = self.addons / "Foo"
        (foo / "Libs" / "Inner").mkdir(parents=True)
        (foo / "Foo.toc").write_text("## Title: Foo\n## SavedVariables: FooDB\n\nLibs\\Lib.xml\nCore.lua\nUI.xml\n",
                                     encoding="utf-8")
        (foo / "Libs" / "Lib.xml").write_text('<Ui>\n  <Script file="LibStub.lua"/>\n  <Include file="Inner\\Inner.xml"/>\n</Ui>\n')
        (foo / "Libs" / "LibStub.lua").write_text("-- lib\n")
        (foo / "Libs" / "Inner" / "Inner.xml").write_text('<Ui><Script file="Deep.lua"/></Ui>')
        (foo / "Libs" / "Inner" / "Deep.lua").write_text("-- deep\n")
        (foo / "Core.lua").write_text("-- core\n")
        (foo / "UI.xml").write_text('<Ui><Script file="UI.lua"/><Frame name="FooFrame"/></Ui>')
        (foo / "UI.lua").write_text("-- ui\n")
        self.assertEqual(len(self.c.command("load Foo")), 4)
        heads = [d.split(b"\n", 1)[0].decode().split(" ", 3) for k, d in self.c.outbox if k == MB.CODE]
        self.assertEqual([h[3] for h in heads], ["@Interface/AddOns/Foo/Libs/LibStub.lua unload",     # OnUnload before the first
                                                 "@Interface/AddOns/Foo/Libs/Inner/Deep.lua",
                                                 "@Interface/AddOns/Foo/Core.lua", "@Interface/AddOns/Foo/UI.lua reload"])
        self.assertEqual({h[2] for h in heads}, {"Foo"})                  # one addon, one namespace
        self.assertIn(("RUN", "load Foo: frames and templates in UI.xml are XML and are not made again; only the Lua "
                              "runs"), got)
        self.assertIsNone(self.c.command("load WoWBridge"))
        self.assertTrue(got[-1][1].startswith("load WoWBridge: refused"), got)
        self.c.outbox.clear()
        self.c.command("watch Foo")
        self.assertIn(("WATCH", "watching 4 files of Foo: each is loaded again when it is saved"), got)
        core = foo / "Core.lua"
        core.write_text("-- core, edited\n")
        os.utime(core, ns=(core.stat().st_atime_ns, core.stat().st_mtime_ns + 10 ** 9))
        self.t += 1
        self.c.tick()                                                     # changed: wait for one more check
        self.assertEqual(self.c.outbox, [])
        self.t += 1
        self.c.tick()                                                     # unchanged since: loaded
        heads = [d.split(b"\n", 1)[0].decode() for k, d in self.c.outbox if k == MB.CODE]
        self.assertEqual(len(heads), 1)
        self.assertTrue(heads[0].endswith(" Foo @Interface/AddOns/Foo/Core.lua"), heads)
        self.assertIn(("WATCH", "Core.lua was saved: loading it"), got)
        self.c.outbox.clear()
        asked = []
        self.c.before_load = lambda addon, path: asked.append((addon, path.name)) or path.name != "Core.lua"
        for _ in range(2):                                                # saved again, and the hook says no
            os.utime(core, ns=(core.stat().st_atime_ns, core.stat().st_mtime_ns + 10 ** 9))
            self.t += 1
            self.c.tick()
            self.t += 1
            self.c.tick()
        self.assertEqual(asked, [("Foo", "Core.lua"), ("Foo", "Core.lua")])
        self.assertEqual([r for r in self.c.outbox if r[0] == MB.CODE], [])   # kept out of the game both times
        self.assertEqual([t for k, t in got if k == "WATCH"].count("Core.lua was saved: loading it"), 1)
        self.c.command("unwatch")
        self.assertIn(("WATCH", "stopped watching 4 files"), got)

    def test_code_jobs(self):
        """run / load: Lua in CODE records of at most CODE_CHUNK bytes, the first part naming the addon and the chunk"""
        got = []
        self.c.on_debug = lambda kind, text: got.append((kind, text))
        self.c.command("run print(1)")
        kind, data = self.c.outbox[-1]
        self.assertEqual(kind, MB.CODE)
        self.assertRegex(data, rb"^\d+ 1/1 - =run\nprint\(1\)$")
        foo = self.addons / "Foo"
        foo.mkdir()
        body = b"-- " + b"x" * 8000 + b"\nreturn 1\n"
        (foo / "Core.lua").write_bytes(body)
        [job] = self.c.command("load Foo/Core.lua")
        parts = [d for k, d in self.c.outbox if k == MB.CODE and d.startswith(b"%d " % job)]
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[0].startswith(b"%d 1/3 Foo @Interface/AddOns/Foo/Core.lua reset\n" % job))   # load: reset by default
        self.assertTrue(parts[1].startswith(b"%d 2/3\n" % job))
        self.assertEqual(b"".join(p.split(b"\n", 1)[1] for p in parts), body)
        self.assertIsNone(self.c.command("load Foo/Missing.lua"))
        self.assertEqual(got[-1][0], "RUN")
        self.assertTrue(got[-1][1].startswith("load Foo/Missing.lua: "), got)
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=5;k=1;m=1")
        for _ in range(5):
            self.c.tick()
            self.t += 1
        codes = [d for i in range(1, self.c.slot) for k, d in MB.read_slot(self.addons, i)["records"] if k == MB.CODE]
        self.assertEqual(len(codes), 4)                                   # every part went out, each in a packet

    def test_parts_and_long_messages(self):
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=5;k=1;m=1")
        self.c.tick()
        body = "长消息 WoWBridge ".encode() * 40
        head = b"LONG n=%d crc=%08x\n" % (len(body) + 24, link.zlib.crc32(body))
        data = head + body
        self.assertEqual(len(head), 24)
        parts = [data[i:i + 300] for i in range(0, len(data), 300)]
        for i, p in enumerate(parts):
            if i != 1:                                                    # part 1 is lost
                self.frame(F.TYPE_DATA, 1, p, part=i, parts=len(parts))
        self.t += 2.0                                                     # quiet for parts_after: report what is in
        self.c.tick()
        pkt = MB.read_slot(self.addons, 2)
        self.assertEqual(pkt["ack"], 0)
        bits = int.from_bytes(bytes.fromhex(pkt["records"][0][1].decode().split(":")[1]), "little")
        self.assertEqual((pkt["records"][0][0], bits), (MB.PARTS, (1 << len(parts)) - 1 - 2))
        self.frame(F.TYPE_DATA, 1, parts[1], part=1, parts=len(parts))
        self.assertEqual(self.c.stats["long_ok"], 1)
        self.assertEqual(self.c.sessions[5].ack, 1)
        self.frame(F.TYPE_DATA, 1, parts[2], part=2, parts=len(parts))  # a late copy: a duplicate, not a new message
        self.assertEqual(self.c.sessions[5].dups, 1)

    def test_stream_checks(self):
        self.assertEqual(link.check_stream(b"S 7 crc=%08x hello" % link.zlib.crc32(b"hello")), (7, True))
        self.assertEqual(link.check_stream(b"S 7 crc=00000000 hello"), (7, False))
        self.assertIsNone(link.check_stream(b"burst 1/2 x"))
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=5;k=1;m=1")
        for i, msg in ((1, 1), (2, 2), (4, 3), (5, 4), (3, 5)):         # stream number 3 comes late (shown again)
            body = b"x" * i
            self.frame(F.TYPE_DATA, msg, b"S %d crc=%08x %s" % (i, link.zlib.crc32(body), body))
        self.frame(F.TYPE_DATA, 6, b"STREAM DONE n=6 acked=6 resent=1 avg=1.00s max=2.00s total=3.0s")
        st = self.c.stats
        self.assertEqual((st["stream_ok"], st["stream_bad"], st["stream_late"], st["stream_missing"]), (5, 0, 1, 1))

    def test_rewind_when_a_slot_is_lost(self):
        """a slot emptied before the addon read it (an install under a running companion, say): the addon keeps saying
        it waits on that slot, and after stuck_after seconds the companion writes from there again, texts included"""
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=5;k=1;m=1")
        self.c.say("hello")
        self.c.command("run x = 1")
        self.c.tick()
        self.assertEqual(self.c.slot, 2)
        MB.reset(self.addons)                                             # slot 1 is gone
        self.t += 2
        self.frame(F.TYPE_CONTROL, 2, b"HELLO;v=0.6.0;s=5;k=1;m=1")
        self.c.tick()
        self.assertEqual(self.c.slot, 2)                                  # 2 s: not yet
        self.t += 1.5
        self.frame(F.TYPE_CONTROL, 3, b"HELLO;v=0.6.0;s=5;k=1;m=1")    # still waiting 3.5 s after slot 1 was written
        self.c.tick()
        self.assertEqual(self.c.slot, 2)                                  # rewound to 1 and written again
        self.assertIn((MB.TEXT, b"hello"), MB.read_slot(self.addons, 1)["records"])
        self.assertIn(MB.CODE, [k for k, _ in MB.read_slot(self.addons, 1)["records"]])   # code is sent again too
        self.assertIn("writing again from slot 1", " ".join(self.log))
        self.t += 0.5
        self.frame(F.TYPE_CONTROL, 4, b"HB;n=1;k=2;m=1;a=0;q=0")       # read: no more rewinds
        self.t += 5
        self.c.tick()
        self.assertEqual(self.c.slot, 3)

    def test_no_rewind_for_a_slot_just_written(self):
        """the addon reports the slot it waits on before the companion writes it; that is not being stuck"""
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=5;k=1;m=1")
        self.c.tick()                                                     # slot 1
        self.frame(F.TYPE_CONTROL, 2, b"HB;n=1;k=2;m=1;a=0;q=0")       # read; waits on 2, not written yet
        for _ in range(4):                                                # 2 s heartbeats while waiting
            self.t += 2
            self.frame(F.TYPE_CONTROL, 3, b"HB;n=2;k=2;m=1;a=0;q=0")
            self.c.tick()                                                 # slot 2 is written at the 5 s heartbeat
        self.assertNotIn("writing again", " ".join(self.log))
        self.assertEqual(self.c.slot, 3)

    def test_text_splits_across_packets(self):
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.5.0;s=5;k=1;m=1")
        for i in range(3):
            self.c.say(("%d" % i) * 2000)
        self.c.tick()
        self.t += 0.5
        self.c.tick()
        texts = [r for r in MB.read_slot(self.addons, 1)["records"] + MB.read_slot(self.addons, 2)["records"]
                 if r[0] == MB.TEXT]
        self.assertEqual(len(texts), 3)

    def test_typed_messages(self):
        """addon 0.8: the first byte says what a message is; user text is never parsed, results and reports have their
        own types, the link tests are checked; a session seen without its HELLO is told apart by that byte"""
        got = []
        self.c.on_debug = lambda kind, text: got.append((kind, text))
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.8.0;s=5;k=1;m=1")
        s = self.c.sessions[5]
        self.assertEqual((s.version, s.typed, s.hb), ((0, 8), True, 15.0))
        self.frame(F.TYPE_DATA, 1, b"\x00RUN 1 ok")                     # what a user typed: logged, nothing more
        self.frame(F.TYPE_DATA, 2, b"\x00ERR not an error")
        self.frame(F.TYPE_DATA, 3, b"\x01ERR Interface/AddOns/Foo/Foo.lua:3: oops\n[string]:3: in main chunk")
        self.frame(F.TYPE_DATA, 4, b"\x01OUT hello |cffff0000red|r\nsecond line")
        self.frame(F.TYPE_DATA, 5, b"\x01DROPPED 3 debug messages (more than 20 were waiting)")
        self.frame(F.TYPE_DATA, 6, b"\x01ODD something new")
        self.frame(F.TYPE_DATA, 7, b"\x0212345 ok =run (9 B, 0.1 ms): 1")
        self.frame(F.TYPE_DATA, 8, b"\x03asked: a button is up; the UI reloads when the user clicks it")
        body = b"x" * 7
        self.frame(F.TYPE_DATA, 9, b"\x04S 1 crc=%08x %s" % (link.zlib.crc32(body), body))
        long_body = "长消息 WoWBridge ".encode() * 10
        self.frame(F.TYPE_DATA, 10, b"\x04LONG n=%d crc=%08x\n" % (len(long_body) + 24, link.zlib.crc32(long_body)) + long_body)
        self.frame(F.TYPE_DATA, 11, b"\x04BURST DONE n=10 acked=10 resent=0 avg=0.50s max=1.00s total=5.0s")
        self.frame(F.TYPE_DATA, 12, b"\x04STREAM DONE n=1 acked=1 resent=0 avg=1.00s max=1.00s total=3.0s")
        self.assertEqual(got, [("ERR", "Interface/AddOns/Foo/Foo.lua:3: oops\n[string]:3: in main chunk"),
                               ("OUT", "hello red\nsecond line"),
                               ("DROPPED", "3 debug messages (more than 20 were waiting)"),
                               ("INFO", "ODD something new"),
                               ("RUN", "12345 ok =run (9 B, 0.1 ms): 1"),
                               ("RELOAD", "asked: a button is up; the UI reloads when the user clicks it")])
        st = self.c.stats
        self.assertEqual((st["stream_ok"], st["long_ok"], st["stream_missing"]), (1, 1, 0))
        self.assertEqual(st["debug"], dict(err=1, out=1, warn=0, blocked=0, dropped=1, repeats=0, run=1, reload=1, info=1))
        self.assertEqual([m[4] for m in s.messages][:2], ["RUN 1 ok", "ERR not an error"])
        self.assertEqual(s.messages[0][2], 8)                             # bytes without the type byte
        self.assertIn("#1 (8 B): RUN 1 ok", self.log)
        self.assertEqual(s.ack, 12)
        # a session this companion never saw a HELLO from: the first byte decides
        self.frame(F.TYPE_CONTROL, 1, b"HB;n=3;k=2;m=1;a=0;q=0", sid=6)
        self.assertIsNone(self.c.sessions[6].typed)
        self.frame(F.TYPE_DATA, 1, b"\x0299 ok =run (1 B, 0.1 ms)", sid=6)
        self.frame(F.TYPE_DATA, 2, b"RUN 98 ok =run (1 B, 0.1 ms)", sid=6)   # an older addon: the kind is the first word
        self.assertEqual(got[-2:], [("RUN", "99 ok =run (1 B, 0.1 ms)"), ("RUN", "98 ok =run (1 B, 0.1 ms)")])

    def test_session_without_hello_gets_the_slow_heartbeat(self):
        """a daemon restarted while the addon runs on sees heartbeats, never the HELLO: it must not fall back to the
        5 s heartbeat of addons before 0.7 (seen in the game: 12 mailbox slots a minute instead of 4)"""
        self.frame(F.TYPE_CONTROL, 1, b"HB;n=3;k=2;m=1;a=0;q=0", sid=9)
        s = self.c.sessions[9]
        self.assertEqual((s.hello_at, s.hb), (None, 15.0))
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.6.0;s=10;k=2;m=1", sid=10)  # an old addon still says so in its HELLO
        self.assertEqual(self.c.sessions[10].hb, link.LEGACY_HB)

    def test_debug_output_once_for_the_daemon(self):
        """echo_debug=False (the daemon): what goes to on_debug is not written to log as well, so the journal has each
        message once; the link report still lists it, and the HELLO's version is kept as written"""
        got, log = [], []
        c = link.Companion(self.addons, clock=lambda: self.t, log=log.append, on_debug=lambda k, t: got.append((k, t)),
                           echo_debug=False)
        c.new_process("P1-2")
        c.on_frame(F.TYPE_CONTROL, 5, 1, b"HELLO;v=0.8.0;s=5;k=1;m=1")
        c.on_frame(F.TYPE_DATA, 5, 1, b"\x0212345 ok =run (9 B, 0.1 ms): 1")
        c.on_frame(F.TYPE_DATA, 5, 2, b"\x01OUT hello")
        self.assertEqual(got, [("RUN", "12345 ok =run (9 B, 0.1 ms): 1"), ("OUT", "hello")])
        self.assertFalse([line for line in log if line.startswith(("[run]", "[print]"))], log)
        self.assertTrue(any(text.startswith("[run] 12345 ok") for _, text in c.events))
        self.assertEqual(c.sessions[5].version_text, "0.8.0")
        self.assertEqual(c.report()["sessions"][0]["version"], "0.8.0")
        # the stand-alone companion (echo_debug=True, the default) still prints it
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.8.0;s=5;k=1;m=1")
        self.c.on_debug = lambda k, t: None
        self.frame(F.TYPE_DATA, 1, b"\x0212345 ok =run (9 B, 0.1 ms): 1")
        self.assertIn("[run] 12345 ok =run (9 B, 0.1 ms): 1", self.log)

    def test_skipped_slot_records_are_sent_again(self):
        """the addon skipped a slot whose packet kept failing its checks and says so (bad=): the slot's text, command
        and code go into the next packet, once; the same goes for slots below the one it reads when that lies above
        the next slot to write (after a rewind)"""
        got = []
        self.c.on_debug = lambda kind, text: got.append((kind, text))
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.8.0;s=5;k=1;m=1")
        self.c.tick()                                                     # WELCOME into slot 1
        self.c.say("hello")
        self.c.command("run x = 1")
        self.c.command("diag")
        self.c.command("ping 7")                                          # a ping is not sent again
        self.t += 1
        self.c.tick()                                                     # text, code and command into slot 2
        self.assertEqual(self.c.slot, 3)
        self.frame(F.TYPE_CONTROL, 2, b"HB;n=1;k=3;m=1;a=0;q=0;bad=2")
        self.assertNotIn(2, self.c.written)
        self.assertIn("the addon skipped slot 2 (damaged, as its control frame says): 3 texts / commands / code parts sent again",
                      self.log)
        self.t += 1
        self.c.tick()
        recs = MB.read_slot(self.addons, 3)["records"]
        self.assertIn((MB.TEXT, b"hello"), recs)
        self.assertIn((MB.COMMAND, b"diag"), recs)
        self.assertNotIn((MB.COMMAND, b"ping 7"), recs)
        self.assertEqual([k for k, _ in recs].count(MB.CODE), 1)
        self.assertEqual(self.c.stats["skipped"], 1)
        self.frame(F.TYPE_CONTROL, 3, b"HB;n=2;k=4;m=1;a=0;q=0;bad=2")   # said again: nothing more to send
        self.t += 1
        self.c.tick()                                                     # nothing due: no packet
        self.assertEqual((self.c.stats["skipped"], self.c.outbox), (1, []))
        self.assertEqual(self.c.slot, 4)
        # a rewind put the next slot to write below slots the addon then went past
        self.c.say("after the rewind")
        self.t += 1
        self.c.tick()                                                     # slot 4
        self.assertEqual(self.c.slot, 5)
        self.c.slot = 4                                                   # as a rewind would, with slot 4 still on record
        self.frame(F.TYPE_CONTROL, 4, b"HB;n=3;k=5;m=1;a=0;q=0")
        self.assertEqual(self.c.slot, 5)
        self.assertIn("the addon skipped slot 4 (it reads slot 5, the next to write was 4): 1 texts / commands / code parts sent again",
                      self.log)
        self.assertEqual(self.c.outbox, [(MB.TEXT, b"after the rewind")])
        self.frame(F.TYPE_CONTROL, 5, b"HB;n=4;k=5;m=1;a=0;q=0;bad=2,9")   # not on record (sent again / never written): nothing
        self.assertEqual(self.c.stats["skipped"], 2)

    def test_message_ids_wrap(self):
        """ids run 1..65535 and start over: the ack follows them around the ring, and a session remembers the last
        32,768 of them (an id that comes back after that is a new message)"""
        self.frame(F.TYPE_CONTROL, 1, b"HELLO;v=0.8.0;s=5;k=1;m=65534")
        s = self.c.sessions[5]
        self.assertEqual((s.base, s.ack), (65533, 65533))
        for msg in (65534, 1, 2):                                         # 65535 is missing: the ack stops before it
            self.frame(F.TYPE_DATA, msg, b"\x00x")
        self.assertEqual(s.ack, 65534)
        self.c.tick()
        self.assertEqual(MB.read_slot(self.addons, 1)["ack"], 65534)
        self.frame(F.TYPE_DATA, 65535, b"\x00x")
        self.assertEqual(s.ack, 2)
        self.t += 1
        self.c.tick()
        self.assertEqual(MB.read_slot(self.addons, 2)["ack"], 2)
        self.frame(F.TYPE_DATA, 1, b"\x00x")                              # shown again: still a duplicate
        self.assertEqual(s.dups, 1)
        for msg in range(3, 3 + link.RECEIVED_MAX):                       # as many as a session remembers
            self.frame(F.TYPE_DATA, msg, b"\x00y")
        self.assertEqual(len(s.received), link.RECEIVED_MAX)
        self.assertEqual(len(s.order), link.RECEIVED_MAX)
        self.assertNotIn(1, s.received)                                   # forgotten: the ring has moved on
        self.assertNotIn(2, s.received)
        self.assertEqual(s.ack, (2 + link.RECEIVED_MAX) % link.WRAP)
        self.assertEqual(s.dups, 1)
        self.frame(F.TYPE_DATA, 1, b"\x00z")                              # id 1 comes round again: a new message
        self.assertEqual(s.dups, 1)
        self.assertEqual(s.messages[-1][4], "z")

    def test_reset_flag(self):
        """code() flags the first part when asked; load() flags one file "reset", a folder's first and last file
        "unload" / "reload", nothing with reset=False; watch reloads carry no flag"""
        self.c.code(b"return 1", "=run", reset=True)
        self.assertRegex(self.c.outbox[-1][1], rb"^\d+ 1/1 - =run reset\nreturn 1$")
        self.c.code(b"return 1", "=run")
        self.assertRegex(self.c.outbox[-1][1], rb"^\d+ 1/1 - =run\nreturn 1$")
        foo = self.addons / "Foo"
        foo.mkdir()
        (foo / "Foo.toc").write_text("A.lua\nB.lua\nC.lua\n", encoding="utf-8")
        for name in ("A", "B", "C"):
            (foo / f"{name}.lua").write_bytes(f"-- {name}\n".encode())
        self.c.outbox.clear()
        self.c.load("Foo/A.lua")
        self.c.load("Foo/B.lua", reset=False)
        self.c.load("Foo")
        heads = [d.split(b"\n", 1)[0].decode().split(" ", 3)[3] for k, d in self.c.outbox if k == MB.CODE]
        self.assertEqual(heads, ["@Interface/AddOns/Foo/A.lua reset", "@Interface/AddOns/Foo/B.lua",
                                 "@Interface/AddOns/Foo/A.lua unload", "@Interface/AddOns/Foo/B.lua", "@Interface/AddOns/Foo/C.lua reload"])
        self.assertIn("code %d: @Interface/AddOns/Foo/A.lua (5 B, 1 parts, reset)" % (self.c.last_job - 4), self.log)
        self.c.outbox.clear()
        self.c.command("watch Foo/C.lua")
        c = foo / "C.lua"
        c.write_text("-- C, edited\n", encoding="utf-8")
        os.utime(c, ns=(c.stat().st_atime_ns, c.stat().st_mtime_ns + 10 ** 9))
        for _ in range(2):
            self.t += 1
            self.c.tick()
        self.assertTrue(self.c.outbox[-1][1].startswith(b"%d 1/1 Foo @Interface/AddOns/Foo/C.lua\n" % self.c.last_job), self.c.outbox)


if __name__ == "__main__":
    unittest.main()
