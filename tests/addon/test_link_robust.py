"""The link of 0.9.6 end to end: WoWBridge's Lua under Lua 5.1 (tests/support/wowmock.py) against the real companion
(wuxianworkshop/transport/link.py). A backlog no longer keeps the link from coming up, acknowledgements are lazy (a few
mailbox slots a minute while messages flow), small messages share frames, a run's result goes up first, a lost frame is
asked for at once, a queue nobody acknowledges is capped without stalling the ack; and on the addon's side the agent's
jobs: values that cannot be put into words, stale and repeated jobs, a reload asked long ago, the session id."""
import unittest

from tests.support.wowmock import Session, lupa
from wuxianworkshop.core import frame as F, mailbox
from wuxianworkshop.transport import link


def lose(s, test):
    """the next data frame whose payload passes test(payload) is lost on its way to the companion (once)"""
    deliver, state = s.comp.on_frame, {"lost": False}

    def on_frame(ftype, sid, msg, payload, part=0, parts=1):
        if not state["lost"] and ftype == F.TYPE_DATA and test(bytes(payload)):
            state["lost"] = True
            return
        deliver(ftype, sid, msg, payload, part, parts)
    s.comp.on_frame = on_frame
    return state


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Link(unittest.TestCase):
    def start(self, login=True, **kw):
        s = Session(self, **kw.pop("session", {}))
        self.addCleanup(s.close)
        self.got, self.logs = [], []
        s.comp = link.Companion(s.addons, clock=s.now, log=self.logs.append,
                                on_debug=lambda kind, text: self.got.append((s.now(), kind, text)), **kw)
        s.comp.new_process("P1-100")
        if login:
            s.login()
            s.run(8)
        return s

    def error(self, s, text):
        s.lua.globals()[b"__error"](text.encode())

    def texts(self, kind):
        return [text for _, k, text in self.got if k == kind]

    def until(self, s, done, seconds):
        """run the session until done() or `seconds`; the time it took"""
        t0 = s.now()
        while not done() and s.now() - t0 < seconds:
            s.run(0.05)
        return s.now() - t0

    def test_a_backlog_does_not_keep_the_link_down(self):
        """20 errors before the link started: a HELLO goes up between them (it once waited for an empty queue that a
        resend cycle never left, and the companion welcomes only after a control frame)"""
        s = self.start(login=False)
        for i in range(20):
            self.error(s, f"Interface/AddOns/Foo/Foo.lua:{i}: early {i}")
        s.login()
        s.run(12)
        sess = s.comp.sessions[s.comp.current]
        self.assertTrue(sess.welcomed and sess.online_at is not None, self.logs)
        self.assertEqual(len(self.texts("ERR")), 20)
        self.assertIn("connected to the companion", s.chat())

    def test_acknowledgements_are_lazy(self):
        """a print every half second for five minutes: a packet every ack_lazy seconds or so instead of every second
        (a game process has 4,096 mailbox slots), every line arrives, nothing is shown twice"""
        s = self.start()
        slot = s.comp.slot
        for i in range(600):
            s.lua.execute(b'print("tick %d")' % i)
            s.run(0.5)
        s.run(12)
        lines = [line for text in self.texts("OUT") for line in text.split("\n")]
        self.assertEqual(lines, [f"tick {i}" for i in range(600)])
        self.assertLess(s.comp.slot - slot, 40)
        self.assertEqual(s.comp.stats["dups"], 0)
        self.assertRegex(s.ns[b"Link"][b"Status"]().decode(), r"; 0 waiting, \d+ acknowledged .* 0 parts shown again")

    def test_small_messages_share_frames(self):
        """a burst of errors goes up a few to a frame (the WELCOME said pack=1): 20 errors in a handful of frames, in
        order, within a second or two"""
        s = self.start()
        frames = len(s.frames)
        for i in range(20):
            self.error(s, f"Interface/AddOns/Foo/Foo.lua:{i}: burst number {i} of a burst of errors")
        took = self.until(s, lambda: len(self.texts("ERR")) == 20, 10)
        self.assertLess(took, 2.5)
        self.assertEqual([t.split("\n")[0] for t in self.texts("ERR")],
                         [f"Interface/AddOns/Foo/Foo.lua:{i}: burst number {i} of a burst of errors" for i in range(20)])
        self.assertLessEqual(len([f for f in s.frames[frames:] if f[1] == F.TYPE_DATA]), 4)
        self.assertGreaterEqual(s.comp.stats["batches"], 1)

    def test_a_run_result_goes_first(self):
        """a run while 20 errors wait: its result goes up before them; the companion knows when every message from
        before it is in too (try waits for that: covered)"""
        s = self.start()
        for i in range(20):
            self.error(s, f"Interface/AddOns/Foo/Foo.lua:{i}: queued {i} " + "x" * 300)   # too big to share many frames
        job = s.comp.code(b"return 1", "=run")
        took = self.until(s, lambda: any(t.startswith(f"{job} ok") for t in self.texts("RUN")), 10)
        self.assertLess(took, 2.5)
        self.assertLess(len(self.texts("ERR")), 20)              # it overtook them
        mark = s.comp.mark()
        self.assertFalse(s.comp.covered(*mark))
        self.until(s, lambda: s.comp.covered(*mark), 15)
        self.assertTrue(s.comp.covered(*mark))
        self.assertEqual(len(self.texts("ERR")), 20)

    def test_a_lost_frame_is_asked_for_at_once(self):
        """a print lost on its way: the next one shows the gap, the companion asks for it (PARTS) and the addon shows it
        again at once, not rto (15 s) later"""
        s = self.start()
        lost = lose(s, lambda p: b"second" in p)
        for word in ("first", "second", "third"):
            s.lua.execute(b'print("%s")' % word.encode())
            s.run(1.2)
        self.assertTrue(lost["lost"])
        took = self.until(s, lambda: any("second" in t for t in self.texts("OUT")), 10)
        self.assertLess(took, 3)
        self.assertGreaterEqual(s.comp.stats["asked"], 1)
        self.assertEqual([t for t in self.texts("OUT")], ["first", "third", "second"])   # handed on as they came

    def test_a_lost_last_message_is_asked_for_too(self):
        """nothing comes after the lost one: the heartbeat frame says up to where every message went up (l=)"""
        s = self.start()
        lose(s, lambda p: p[:1] == bytes([link.TYPE_RUN]))
        job = s.comp.code(b"return 7", "=run")
        took = self.until(s, lambda: any(t.startswith(f"{job} ok") for t in self.texts("RUN")), 10)
        self.assertLess(took, 5)
        self.assertGreaterEqual(s.comp.stats["asked"], 1)

    def test_a_queue_nobody_acknowledges(self):
        """no companion for a long while: the addon keeps at most 256 messages (the oldest go) and counts no more of
        them as waiting than it shows; a companion that comes later moves its ack past the ones that went (HB m=)"""
        s = self.start(login=False)
        s.comp_running = False
        s.login()
        for i in range(300):
            s.lua.execute(b'print("lonely %d")' % i)
            s.run(0.6)
        link_ns = s.ns[b"Link"]
        self.assertLessEqual(link_ns[b"Numbers"]()[b"waiting"], 256)
        s.comp_running = True
        s.lua.execute(b'print("after")')
        self.until(s, lambda: any("after" in t for t in self.texts("OUT")), 30)
        s.run(20)
        sess = s.comp.sessions[s.comp.current]
        self.assertEqual(link_ns[b"Numbers"]()[b"waiting"], 0)
        self.assertEqual(sess.ack, sess.newest)                  # the ack went past the gap of the dropped ones

    def test_a_restarted_companion_learns_what_the_addon_does(self):
        """a companion started while the addon runs sees no HELLO: it welcomes the session as an older one, the addon's
        PONG says f=1, and the next WELCOME sets the lazy acks and packing again"""
        s = self.start()
        comp2 = link.Companion(s.addons, clock=s.now, log=self.logs.append,
                               on_debug=lambda kind, text: self.got.append((s.now(), kind, text)))
        comp2.new_process("P1-100")
        s.comp = comp2
        s.run(30)
        sess = comp2.sessions[comp2.current]
        self.assertEqual(sess.features, link.LAZY)
        P = s.ns[b"Link"][b"P"]
        self.assertEqual((P[b"pack"], P[b"rto"], P[b"win"]), (True, 15, 64))
        for i in range(10):
            self.error(s, f"Interface/AddOns/Foo/Foo.lua:{i}: after the restart {i}")
        self.until(s, lambda: len(self.texts("ERR")) == 10, 10)
        self.assertEqual(len(self.texts("ERR")), 10)


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Jobs(unittest.TestCase):
    def start(self, **kw):
        s = Session(self, **kw)
        self.addCleanup(s.close)
        self.got = []
        s.comp = link.Companion(s.addons, clock=s.now, log=lambda text: None,
                                on_debug=lambda kind, text: self.got.append((kind, text)))
        s.comp.new_process("P1-100")
        s.login()
        s.run(8)
        return s

    def result(self, s, job, seconds=8):
        for _ in range(int(seconds / 0.25)):
            s.run(0.25)
            text = next((t for k, t in self.got if k == "RUN" and t.startswith(f"{job} ")), None)
            if text is not None:
                return text
        return None

    def test_values_that_cannot_be_put_into_words(self):
        """a value whose tostring raises, a secret value: the result still comes, each said for what it is"""
        s = self.start()
        s.lua.execute(b"SECRET = {} function issecretvalue(v) return v == SECRET end")
        job = s.comp.code(b'local u = newproxy(true) getmetatable(u).__tostring = function() error("no words") end '
                          b'return u, SECRET, { SECRET }, "fine"', "=run")
        text = self.result(s, job)
        self.assertRegex(text, rf'^{job} ok =run \(\d+ B, [\d.]+ ms, 4 values\): "<userdata: tostring failed>", '
                               r'"<secret>", "\{\n  \[1\] = <secret>,\n\}", "fine"$')

    def test_old_and_repeated_jobs_do_not_run(self):
        """a job older than 15 minutes by the PC's clock waited in the mailbox (a game restart): not run; one that ran
        is never run twice, also past the 50 the list once kept"""
        s = self.start()
        g = s.lua.globals()
        old = int((s.now() - 1000) * 1000) % 10 ** 10
        s.ns[b"Agent"][b"Code"](b"%d 1/1 - =run\nOldJobRan = true" % old)
        self.assertIsNone(g[b"OldJobRan"])
        jobs = []
        for i in range(60):
            jobs.append(s.comp.code(b"Runs = (Runs or 0) + 1", "=run"))
        s.run(30)
        self.assertEqual(g[b"Runs"], 60)
        for job in jobs:                                          # every part read again: none runs twice
            s.ns[b"Agent"][b"Code"](b"%d 1/1 - =run\nRuns = (Runs or 0) + 1" % job)
        self.assertEqual(g[b"Runs"], 60)

    def test_a_stamp_read_late_is_taken_as_it_is(self):
        """the first read of proc.ttf failed (no companion had written it): the saved stamp is "?", and the stamp read
        at the next /reload is taken without starting the slots over (they sit in the client's font cache: read from 1
        again, every job in them would come back)"""
        s = Session(self, db=b'{ mail = { proc = "?", next = 7 } }')
        self.addCleanup(s.close)
        s.comp = None
        mailbox.write_proc(s.addons, "P1-100")
        s.login()
        s.run(8)
        mail = s.lua.globals()[b"WoWBridgeDB"][b"mail"]
        self.assertEqual((mail[b"proc"], mail[b"next"]), (b"P1-100", 7))

    def test_a_reload_asked_long_ago_asks_nothing(self):
        s = self.start()
        g = s.lua.globals()
        old = int((s.now() - 3600) * 1000) % 100000000
        s.ns[b"Link"][b"Command"](b"reload %d" % old)
        self.assertIsNone(g[b"WoWBridgeReloadDialog"])            # no button asks for it
        s.ns[b"Link"][b"Command"](b"reload %d" % (int(s.now() * 1000) % 100000000))
        self.assertTrue(g[b"WoWBridgeReloadDialog"][b"shown"])

    def test_the_session_id_is_never_the_last_one(self):
        """the companion tells a /reload by a new session id: a random one that equals the last is moved"""
        s = Session(self, db=b"{ lastSession = 42 }", early=lambda lua: lua.execute(b"math.random = function() return 42 end"))
        self.addCleanup(s.close)
        s.login()
        self.assertNotEqual(s.ns[b"session"], 42)
        self.assertEqual(s.lua.globals()[b"WoWBridgeDB"][b"lastSession"], s.ns[b"session"])


if __name__ == "__main__":
    unittest.main()
