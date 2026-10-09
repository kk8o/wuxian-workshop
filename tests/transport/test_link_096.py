"""The companion's side of the 0.9.6 link (wuxianworkshop/transport/link.py), driven frame by frame: the lazy acks and
asks of an addon with link features (f=1), batches, the ack moving past what the addon dropped, reassembly checks,
records no packet can carry, a failed write, the game exiting, rewinds that write the same bytes, WELCOMEs again, the
bounded memory; the daemon's journal reading events strictly; and the Service failing fast when the link cannot take
work, telling a reload from a timeout and taking back a job nobody waits for."""
import asyncio
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from wuxianworkshop.core import frame as F, mailbox as MB
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.transport import link

HELLO = b"HELLO;v=0.9.6;s=5;k=1;m=1;p=P1-1;f=1;j=0"


class Lazy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addons = Path(self.tmp.name)
        (self.addons / "WoWBridge").mkdir()
        MB.install(self.addons, pool=64)
        self.t = 100.0
        self.log, self.got = [], []
        self.c = link.Companion(self.addons, clock=lambda: self.t, log=self.log.append,
                                on_debug=lambda kind, text: self.got.append((kind, text)))
        self.c.new_process("P1-1")

    def tearDown(self):
        self.tmp.cleanup()

    def frame(self, ftype, msg, payload, sid=5, part=0, parts=1):
        self.c.on_frame(ftype, sid, msg, payload, part, parts)

    def wait(self, seconds, step=0.25, hb=True):
        """time passes: the addon's frames keep coming (a heartbeat now and then), the companion ticks"""
        end = self.t + seconds
        while self.t < end:
            self.t = round(self.t + step, 3)
            if hb:
                self.frame(F.TYPE_CONTROL, 1000, b"HB;n=1")         # no k / m / l: says nothing but that frames come
            self.c.tick()

    def packets(self, first=1):
        return [MB.read_slot(self.addons, i) for i in range(first, self.c.slot)]

    def test_the_welcome_and_lazy_acks(self):
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.c.tick()
        welcome = MB.read_slot(self.addons, 1)["records"][0]
        self.assertEqual(welcome[0], MB.WELCOME)
        for field in (b";win=64", b";show=0.2", b";hb=30", b";rto=15", b";pack=1"):
            self.assertIn(field, welcome[1])
        for msg in (1, 2, 3):
            self.frame(F.TYPE_DATA, msg, b"\x01OUT line %d" % msg)
            self.wait(1)
        self.assertEqual(self.c.slot, 2)                                  # 3 s: no ack yet
        self.wait(8)
        self.assertEqual(self.c.slot, 3)                                  # ack_lazy (10 s) after the first: one packet
        self.assertEqual(MB.read_slot(self.addons, 2)["ack"], 3)
        self.frame(F.TYPE_DATA, 4, b"\x04burst 1/1 x")                    # a link test: acknowledged within a second
        self.wait(1.5)
        self.assertEqual((self.c.slot, MB.read_slot(self.addons, 3)["ack"]), (4, 4))

    def test_what_went_missing_is_asked_for_at_once(self):
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.c.tick()
        self.frame(F.TYPE_DATA, 1, b"\x01OUT one")
        self.frame(F.TYPE_DATA, 3, b"\x01OUT three")                     # 2 went up before it: lost
        self.wait(0.5)
        parts = [r for p in self.packets(2) for r in p["records"] if r[0] == MB.PARTS]
        self.assertEqual(parts, [(MB.PARTS, b"2:")])
        self.wait(3)                                                      # asked again only after NACK_AGAIN
        self.assertEqual(len([r for p in self.packets(2) for r in p["records"] if r[0] == MB.PARTS]), 1)
        self.wait(2.5)
        self.assertEqual(len([r for p in self.packets(2) for r in p["records"] if r[0] == MB.PARTS]), 2)
        self.frame(F.TYPE_DATA, 2, b"\x01OUT two")
        self.assertEqual(self.c.sessions[5].ack, 3)
        self.assertEqual([t for k, t in self.got if k == "OUT"], ["one", "three", "two"])
        # a RUN result goes up first: one coming before a lower id says nothing went missing
        self.frame(F.TYPE_DATA, 6, b"\x02123 ok =run (1 B, 0.1 ms)")
        self.wait(1)
        self.assertNotIn((MB.PARTS, b"4:"), [r for p in self.packets(2) for r in p["records"]])
        # the heartbeat says every message up to 5 went up: 4 and 5 are missing, 5 with the part it has
        self.frame(F.TYPE_DATA, 5, b"\x01part zero", part=0, parts=2)
        self.frame(F.TYPE_CONTROL, 9, b"HB;n=2;k=%d;m=1;l=5;q=3" % self.c.slot)
        self.wait(0.5)
        asked = [r[1] for p in self.packets(2) for r in p["records"] if r[0] == MB.PARTS]
        self.assertIn(b"4:", asked)
        self.assertIn(b"5:01", asked)

    def test_a_batch_is_handed_on_message_by_message(self):
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        body = b"".join(bytes([t]) + len(x).to_bytes(2, "big") + x for t, x in
                        ((link.TYPE_DEBUG, b"OUT first"), (link.TYPE_DEBUG, b"ERR Interface/AddOns/Foo/Foo.lua:1: x\nstack"),
                         (link.TYPE_EVENT, b'{"a":"Foo","t":"x","d":1}')))
        self.frame(F.TYPE_DATA, 1, bytes([link.TYPE_BATCH]) + body)
        self.assertEqual(self.got, [("OUT", "first"), ("ERR", "Interface/AddOns/Foo/Foo.lua:1: x\nstack"),
                                    ("EVENT", '{"a":"Foo","t":"x","d":1}')])
        self.assertEqual((self.c.stats["batches"], self.c.stats["data"], self.c.sessions[5].ack), (1, 3, 1))
        self.assertEqual(link.unbatch(b"\x01\x00\x05ab"), [(1, b"ab")])     # cut short: what is there

    def test_the_ack_moves_past_what_the_addon_dropped(self):
        """a queue nobody acknowledged dropped its oldest: the addon's oldest id (m=) is past a gap"""
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.frame(F.TYPE_DATA, 1, b"\x01OUT a")
        self.frame(F.TYPE_DATA, 5, b"\x01OUT e")
        self.assertEqual(self.c.sessions[5].ack, 1)
        self.frame(F.TYPE_CONTROL, 2, b"HB;n=1;k=1;m=5;l=5;q=1")
        self.assertEqual(self.c.sessions[5].ack, 5)
        self.frame(F.TYPE_CONTROL, 3, b"HB;n=2;k=1;m=3;l=5;q=1")        # an older report moves nothing back
        self.assertEqual(self.c.sessions[5].ack, 5)

    def test_reassembly_checks(self):
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.frame(F.TYPE_DATA, 1, b"\x01OUT x", part=3, parts=2)        # a part past the parts: ignored
        self.assertEqual((self.c.stats["bad_parts"], self.c.sessions[5].partial), (1, {}))
        self.frame(F.TYPE_DATA, 2, b"\x01OUT ", part=0, parts=3)
        self.frame(F.TYPE_DATA, 2, b"\x01OUT whole", part=0, parts=1)    # another message under that id: it wins
        self.assertEqual(self.got[-1], ("OUT", "whole"))
        self.c.on_debug = lambda kind, text: 1 / 0                        # handing on fails: said, the link goes on
        self.frame(F.TYPE_DATA, 3, b"\x01OUT boom")
        self.assertIn("message 3 of session 5 could not be handed on", " ".join(self.log))
        self.assertIn(3, self.c.sessions[5].received)                     # received all the same: acknowledged

    def test_records_no_packet_carries(self):
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.c.say("长" * 3000)                                           # 9,000 bytes: in pieces, whole characters
        texts = [d for k, d in self.c.outbox if k == MB.TEXT]
        self.assertEqual((len(texts), b"".join(texts).decode()), (3, "长" * 3000))
        self.assertTrue(all(len(t) <= MB.RECORD_MAX for t in texts))
        self.c.outbox.append((MB.COMMAND, b"x" * (MB.RECORD_MAX + 1)))
        job = self.c.code(b"-- " + b"y" * 10000, "@" + "C:/a/very/long/path/" * 40 + "Core.lua", "Foo", True)
        self.assertTrue(all(len(d) <= MB.RECORD_MAX for k, d in self.c.outbox if k == MB.CODE))
        self.wait(6)
        self.assertEqual(self.c.stats["dropped_records"], 1)
        self.assertIn(("RUN", f"a COMMAND record of {MB.RECORD_MAX + 1} bytes was dropped: a mailbox packet carries "
                              f"{MB.RECORD_MAX} at most"), self.got)
        sent = b"".join(d.split(b"\n", 1)[1] for p in self.packets() for k, d in p["records"]
                        if k == MB.CODE and d.startswith(b"%d " % job))
        self.assertEqual(sent, b"-- " + b"y" * 10000)

    def test_a_failed_write_keeps_its_records(self):
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.c.say("kept")
        with mock.patch.object(MB, "write_slot", side_effect=PermissionError("held open")):
            self.c.tick()
        self.assertEqual(self.c.slot, 1)
        self.assertIn((MB.TEXT, b"kept"), self.c.outbox)
        self.assertIn("could not write mailbox slot 1", " ".join(self.log))
        self.wait(1)
        recs = MB.read_slot(self.addons, 1)["records"]
        self.assertIn((MB.TEXT, b"kept"), recs)
        self.assertEqual(recs[0][0], MB.WELCOME)

    def test_the_game_exits(self):
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.c.tick()
        self.c.say("unread")
        self.c.command("run x = 1")
        self.c.tick()
        self.c.command("reload")
        self.c.game_gone()
        self.assertIsNone(MB.read_slot(self.addons, 1))                   # every used slot emptied
        self.assertEqual(self.c.outbox, [(MB.TEXT, b"unread")])           # its commands and code dropped, texts wait
        self.assertEqual(MB.fontpack.unpacket(MB.fontpack.read(MB.fontpack.widths(MB.proc_path(self.addons).read_bytes()))[0]),
                         b"?")
        self.assertEqual((self.c.slot, self.c.sessions), (None, {}))
        self.c.command("run y = 2")
        self.c.say("for the next game")
        self.c.new_process("P2-2")                                        # a new game: code for the old one is dropped
        self.assertEqual(self.c.outbox, [(MB.TEXT, b"unread"), (MB.TEXT, b"for the next game")])

    def test_a_rewind_writes_the_same_bytes(self):
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.c.say("hello")
        self.c.tick()
        before = MB.slot_path(self.addons, 1).read_bytes()
        MB.clear_slot(self.addons, 1)                                     # lost before the addon read it
        self.c.say("after")
        for i in range(8):
            self.t += 0.5
            self.frame(F.TYPE_CONTROL, 2 + i, b"HB;n=1;k=1;m=1;l=0;q=0")
            self.c.tick()
        self.assertEqual(MB.slot_path(self.addons, 1).read_bytes(), before)
        self.assertIn("writing again from slot 1", " ".join(self.log))
        self.assertIn((MB.TEXT, b"after"), MB.read_slot(self.addons, 2)["records"])   # new records: new slots

    def test_no_rewind_while_the_addon_catches_up(self):
        """the addon reads about a slot a second: one written long ago that it got to only now is not lost"""
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        for i in range(6):
            self.c.say(f"line {i}")
            self.t += 0.5
            self.c.tick()
        written = self.c.slot
        for k in range(2, written):
            self.t += 1
            self.frame(F.TYPE_CONTROL, 10 + k, b"HB;n=1;k=%d;m=1;l=0;q=0" % k)
            self.c.tick()
        self.assertNotIn("writing again", " ".join(self.log))

    def test_welcomed_again(self):
        """a HELLO from a session that read past its WELCOME (offline, or a new UI session under its id): again; a
        session met without its HELLO is welcomed as an older one until its PONG says f=1"""
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.c.tick()
        self.wait(6)
        self.frame(F.TYPE_CONTROL, 2, b"HELLO;v=0.9.6;s=5;k=1;m=1;f=1")   # not read yet: no new one
        self.assertNotIn(MB.WELCOME, [k for k, _ in self.c.outbox])
        self.frame(F.TYPE_CONTROL, 3, b"HELLO;v=0.9.6;s=5;k=2;m=1;f=1")   # read past it and still saying HELLO
        self.assertEqual(self.c.outbox[0][0], MB.WELCOME)
        self.frame(F.TYPE_CONTROL, 1, b"HB;n=1;k=2;m=1;l=0;q=0", sid=8)    # a session met without its HELLO
        self.assertNotIn(b"rto=", self.c.outbox[0][1])
        self.assertEqual(self.c.sessions[8].hb, 15.0)
        self.frame(F.TYPE_CONTROL, 2, b"PONG;id=1;k=2;f=1", sid=8)
        self.assertIn(b";rto=15;pack=1", self.c.outbox[0][1])
        self.assertEqual((self.c.sessions[8].features, self.c.sessions[8].hb), (1, 30.0))

    def test_memory_is_bounded(self):
        for sid in range(20):
            self.frame(F.TYPE_CONTROL, 1, b"HB;n=1;k=1;m=1", sid=sid)
        self.assertEqual(len(self.c.sessions), link.SESSIONS_KEPT)
        self.assertIn(19, self.c.sessions)
        for i in range(link.NOTES_KEPT + 10):
            self.c.note(f"note {i}", echo=False)
        self.assertEqual(len(self.c.events), link.NOTES_KEPT)
        s = self.c.sessions[19]
        for msg in range(1, 200):
            self.frame(F.TYPE_DATA, msg, b"\x00x", sid=19)
        self.assertEqual(len(s.messages), link.MESSAGES_KEPT)

    def test_a_job_taken_back(self):
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        job = self.c.code(b"x" * 9000, "=run")
        self.assertEqual(self.c.withdraw(job), (3, False))
        self.assertEqual(self.c.outbox, [r for r in self.c.outbox if r[0] != MB.CODE])
        job = self.c.code(b"return 1", "=run")
        self.c.tick()
        self.assertEqual(self.c.withdraw(job), (0, True))                 # it went out: it may still run

    def test_the_clock_set_back(self):
        self.frame(F.TYPE_CONTROL, 1, HELLO)
        self.c.tick()
        self.t -= 3600
        self.c.say("after the clock went back")
        self.wait(1)
        self.assertIn((MB.TEXT, b"after the clock went back"), MB.read_slot(self.addons, 2)["records"])


class StrictEvents(unittest.TestCase):
    """an event's JSON comes from the game: what the API could not send on stays a plain EVENT entry"""

    def test_what_is_not_json_this_daemon_passes_on(self):
        j = Journal()
        for text in ('{"a":"Foo","t":"x","d":NaN}', '{"a":"Foo","t":"x","d":Infinity}', '{"a":"Foo","t":"x","d":"\\ud800"}',
                     '{"a":"Foo","t":"x","d":' + "[" * 100000 + "]" * 100000 + "}"):
            e = j.add("EVENT", text)
            self.assertNotIn("topic", e)
            self.assertEqual(e["text"], text)
        e = j.add("EVENT", '{"a":"Foo","t":"q","d":1,"x":-5,"r":"1.2","w":99999}')
        self.assertEqual((e["dropped"], e["request"], e["wait"]), (0, "1.2", None))
        e = j.add("EVENT", '{"a":"Foo","t":"q","d":1,"r":"not an id","w":5}')
        self.assertNotIn("request", e)

    def test_events_do_not_push_the_rest_out(self):
        j = Journal(capacity=10, event_capacity=5)
        j.add("ERR", "an error")
        for i in range(50):
            j.add("EVENT", '{"a":"Foo","t":"x","d":%d}' % i)
        entries, _, truncated = j.since(0, 100)
        self.assertEqual(entries[0]["text"], "an error")
        self.assertEqual([e["data"] for e in entries[1:]], [45, 46, 47, 48, 49])
        self.assertTrue(truncated)
        errors, _, truncated = j.since(0, 100, ["ERR"])
        self.assertEqual((len(errors), truncated), (1, False))


class Worker:
    """the worker thread's side for the Service, here and now"""

    def __init__(self, comp):
        self.comp, self.addons, self.win, self.wgc = comp, comp.addons, None, None

    def companion(self):
        return self.comp

    def request(self, fn, *args):
        fut = Future()
        try:
            fut.set_result(fn(*args))
        except BaseException as e:
            fut.set_exception(e)
        return fut


class FailFast(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        addons = Path(self.tmp.name)
        (addons / "WoWBridge").mkdir()
        MB.install(addons, pool=16)
        self.comp = link.Companion(addons, log=lambda text: None)
        self.comp.new_process("P1-1")
        self.comp.on_frame(F.TYPE_CONTROL, 5, 1, HELLO)
        self.svc = Service(lambda **kw: Worker(self.comp), journal=Journal())

    def tearDown(self):
        self.tmp.cleanup()

    async def test_the_link_cannot_take_it(self):
        self.comp.link_up = False
        with self.assertRaises(ApiError) as cm:
            await self.svc.run("return 1")
        self.assertEqual((cm.exception.status, cm.exception.code), (409, "link_down"))
        self.comp.link_up, self.comp.full = True, True
        with self.assertRaises(ApiError) as cm:
            await self.svc.say("hi")
        self.assertEqual(cm.exception.code, "mailbox_full")

    async def test_a_job_nobody_waits_for_is_taken_back(self):
        with self.assertRaises(ApiError) as cm:
            await self.svc.run("return 1", timeout_ms=200)
        self.assertIn("it never reached the game and will not run (taken back)", cm.exception.message)
        self.assertEqual([r for r in self.comp.outbox if r[0] == MB.CODE], [])
        self.assertEqual(self.svc.pending, {})

    async def test_a_reload_while_a_job_ran(self):
        async def reload_soon():
            await asyncio.sleep(0.05)
            job = max(self.svc.pending)
            self.svc.session_started(9, job)                    # the new session's HELLO: it ran up to that job
        res, _ = await asyncio.gather(self.svc.run("return 1", timeout_ms=5000), reload_soon())
        self.assertFalse(res["ok"])
        self.assertIn("the UI reloaded (now session 9) before its result came back", res["error"])


if __name__ == "__main__":
    unittest.main()
