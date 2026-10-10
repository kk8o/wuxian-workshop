"""The link must not get stuck (0.9.9), end to end: WoWBridge's Lua under Lua 5.1 (tests/support/wowmock.py) against the
real companion (wuxianworkshop/transport/link.py).

What happened with 0.9.8 after a /reload: a burst of RUN results (WuxianKit's answers, in pieces) went up first, a print
queued behind them waited, and the companion's cumulative ack could not pass it. The results past it were then shown
again every rto seconds, before it, for good: the print never went up and the frame never stopped changing. Now the
addon never shows a part again on its timer before a message that has not gone up, and those passes come further apart
each time; the companion tells the addon which messages past the gap came in whole (it then shows them no more, also an
addon before 0.9.9); a message the companion asked for many times with nothing more of it coming is given up; and
/wb flush (or the companion's "flush") drops what waits to go up."""
import re
import unittest

from tests.support.wowmock import Session, lupa
from wuxianworkshop.core import frame as F
from wuxianworkshop.transport import link

# Link.lua's Pick and the wait of PassAgain as they were up to 0.9.8: an addon from before this release
OLD_PICK = b"""local function Pick(now)
	local fresh
	for n, m in ipairs(queue) do
		if m.urgent then
			if PassPart(m, now) or (m.shown and PassAgain(m, now)) then return m end
		elseif n <= P.win and not fresh and not m.shown and PassPart(m, now) then
			fresh = m
		end
	end
	if fresh then return fresh end
	for n, m in ipairs(queue) do
		if n > P.win then break end
		if not m.urgent and m.shown and (PassPart(m, now) or PassAgain(m, now)) then return m end
	end
end
"""
NEW_WAIT = b"math.min(P.rto * 2 ^ m.again, AGAIN_MAX)"


def as_098(source):
    source = source.replace(b"\r\n", b"\n")
    start = source.index(b"local function Pick(now)")
    end = source.index(b"\nend\n", start) + 5
    assert source.count(NEW_WAIT) == 1
    return (source[:start] + OLD_PICK + source[end:]).replace(NEW_WAIT, b"P.rto").replace(b'n("mode") <= 3', b'n("mode") <= 2')


@unittest.skipIf(lupa is None, "lupa (Lua 5.1 for Python) is not installed")
class Stuck(unittest.TestCase):
    def start(self, old_addon=False, **kw):
        s = Session(self, patch={"Link.lua": as_098} if old_addon else None)
        self.addCleanup(s.close)
        self.got, self.logs = [], []
        s.comp = link.Companion(s.addons, clock=s.now, log=self.logs.append,
                                on_debug=lambda kind, text: self.got.append((s.now(), kind, text)), **kw)
        s.comp.new_process("P1-100")
        s.login()
        s.run(8)
        return s

    def outs(self):
        return [t for _, k, t in self.got if k == "OUT"]

    def until(self, s, done, seconds):
        t0 = s.now()
        while not done() and s.now() - t0 < seconds:
            s.run(0.05)
        return s.now() - t0

    def burst(self, s, n=10):
        """a print queued, and right after it n RUN results (three big frames each), which go up before it: the print
        is the gap the cumulative ack waits at (WuxianKit's answers after a reload)"""
        send = s.ns[b"Link"][b"Send"]
        send(b"OUT held up", b"debug")
        for i in range(n):
            send(b'%d ok =run (1 B, 0.0 ms): 1 values "%s"' % (9000 + i, b"x" * 4000), b"run")

    def data_frames(self, s, since):
        return [f for f in s.frames if f[0] >= since and f[1] == F.TYPE_DATA]

    def test_the_old_way_got_stuck(self):
        """the case itself: an addon before 0.9.9 and a companion that says nothing of what came in whole never get the
        print up, and the frame keeps changing"""
        s = self.start(old_addon=True, rto_lazy=2)
        s.comp._sacks = lambda sess, now: []
        self.burst(s)
        s.run(60)
        self.assertNotIn("held up", self.outs())
        self.assertGreater(len(self.data_frames(s, s.now() - 20)), 50)

    def test_passes_again_never_hold_up_a_message(self):
        """this addon, and a companion that says nothing of what came in whole: the print goes up once the results'
        first passes are done, and then the frame goes quiet"""
        s = self.start(rto_lazy=2)
        s.comp._sacks = lambda sess, now: []
        self.burst(s)
        took = self.until(s, lambda: "held up" in self.outs(), 30)
        self.assertLess(took, 15)                       # the results' first passes: 10 x 6 breathing parts
        s.run(40)
        self.assertEqual(self.data_frames(s, s.now() - 20), [])
        self.assertEqual(len([t for _, k, t in self.got if k == "RUN"]), 10)

    def test_an_older_addon_is_told_what_came_in_whole(self):
        """an addon before 0.9.9 and this companion: it learns which results came in whole (PARTS with every bit),
        stops showing them again, and the print goes up"""
        s = self.start(old_addon=True, rto_lazy=2)
        self.burst(s)
        took = self.until(s, lambda: "held up" in self.outs(), 40)
        self.assertLess(took, link.SACK_AFTER + 15)
        self.assertGreater(s.comp.stats["sacked"], 0)
        s.run(40)
        self.assertEqual(self.data_frames(s, s.now() - 20), [])

    def test_passes_nobody_asked_for_come_further_apart(self):
        """a message whose frames never get through, and a companion that does not ask for it: shown again rto, 2 rto,
        4 rto ... after the last pass; the count starts over when the link comes back"""
        s = self.start(rto_lazy=2)
        s.comp._asks = lambda sess, now: []
        deliver = s.comp.on_frame
        s.comp.on_frame = lambda ftype, sid, msg, payload, part=0, parts=1: (
            None if ftype == F.TYPE_DATA and b"never through" in bytes(payload) else deliver(ftype, sid, msg, payload, part, parts))
        s.lua.execute(b'print("never through")')
        s.run(40)
        times = [f[0] for f in s.frames if f[1] == F.TYPE_DATA and b"never through" in f[3]]
        gaps = [round(b - a) for a, b in zip(times, times[1:])]
        self.assertEqual(gaps[:4], [2, 4, 8, 16])
        s.comp_running = False                       # no packets: the addon goes offline, then back online
        s.run(100)
        s.comp_running = True
        t0 = s.now()
        s.run(20)
        again = [f[0] for f in s.frames if f[0] >= t0 and f[1] == F.TYPE_DATA and b"never through" in f[3]]
        self.assertTrue(again)
        self.assertLess(again[0] - t0, 6)

    def test_a_message_that_never_comes_is_given_up(self):
        """the companion asks for a message ASK_MAX times and nothing of it comes: it goes on without it (a note says
        so), the addon drops it, and what comes after it is handed on"""
        s = self.start()
        deliver = s.comp.on_frame
        s.comp.on_frame = lambda ftype, sid, msg, payload, part=0, parts=1: (
            None if ftype == F.TYPE_DATA and b"never through" in bytes(payload) else deliver(ftype, sid, msg, payload, part, parts))
        s.lua.execute(b'print("never through")')
        s.run(1)
        s.lua.execute(b'print("after it")')
        took = self.until(s, lambda: s.comp.stats["given_up"] > 0, 150)
        self.assertGreater(took, link.ASK_MAX * link.NACK_AGAIN - 10)
        self.assertTrue(any(k == "INFO" and "given up after 12 asks" in t for _, k, t in self.got))
        self.until(s, lambda: s.ns[b"Link"][b"Numbers"]()[b"waiting"] == 0 and s.comp.sessions[s.comp.current].ack
                   == s.comp.sessions[s.comp.current].newest, 30)
        sess = s.comp.sessions[s.comp.current]
        self.assertEqual(sess.ack, sess.newest)
        self.assertEqual(self.outs(), ["after it"])
        s.run(30)
        self.assertEqual([f for f in self.data_frames(s, s.now() - 15) if b"never through" in f[3]], [])

    def test_flush(self):
        """/wb flush drops what waits to go up and says how much; the companion moves its ack past it, and what comes
        next goes up as ever. The companion's "flush" does the same"""
        s = self.start()
        deliver, cut = s.comp.on_frame, {"on": True}     # the companion reads nothing: everything waits
        s.comp.on_frame = lambda *a: None if cut["on"] else deliver(*a)
        for i in range(20):
            s.lua.execute(b'print("waiting %d")' % i)
            s.run(0.3)
        s.run(1)                                         # Debug.lua has handed the last one to the link
        s.slash("flush")
        said = re.findall(r"(\d+) messages waiting to go up dropped", s.chat())
        self.assertEqual(len(said), 1)
        self.assertGreaterEqual(int(said[0]), 1)
        self.assertEqual(s.ns[b"Link"][b"Numbers"]()[b"waiting"], 0)
        cut["on"] = False
        s.lua.execute(b'print("next")')
        self.until(s, lambda: "next" in self.outs(), 15)
        self.assertEqual(self.outs(), ["next"])
        sess = s.comp.sessions[s.comp.current]
        self.until(s, lambda: sess.ack == sess.newest, 15)
        self.assertEqual(sess.ack, sess.newest)
        cut["on"] = True
        for i in range(5):
            s.lua.execute(b'print("again %d")' % i)
            s.run(0.3)
        s.run(1)
        s.comp.command("flush")
        self.until(s, lambda: len(re.findall("waiting to go up dropped", s.chat())) == 2, 10)
        self.assertEqual(len(re.findall("waiting to go up dropped", s.chat())), 2)
        cut["on"] = False
        s.run(10)
        self.assertEqual(self.outs(), ["next"])


if __name__ == "__main__":
    unittest.main()
