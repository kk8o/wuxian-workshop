"""The page's log list at volume: the fake daemon's ?tail=1 (the daemon's shape: the newest N, oldest first, next = the id after
the last), and app.js's log handling run under Node (skipped without it): entries from the stream are queued and land once per
animation frame (a timer while the window is hidden), duplicates go by id, the kept list is trimmed in blocks, the counts follow
pushes and trims, and only the last LOG_ROWS of the filtered list are rendered, on 开发台 only."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from tests.ui.test_fake_api import AUTH, F
from wuxianworkshop.ui import STATIC_DIR

NODE = shutil.which("node")


class FakeTail(unittest.TestCase):
    def setUp(self):
        self.fake = F.Fake(token="t0ken", seed=1)
        self.client = TestClient(F.create_app(self.fake, ticker=False), base_url="http://127.0.0.1")
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def get(self, query):
        r = self.client.get("/api/logs?" + query, headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_tail_is_the_newest_oldest_first(self):
        for i in range(30):
            self.fake.entry("ERR" if i % 3 == 0 else "OUT", f"line {i}")
        last = self.fake.logs[-1]["id"]
        r = self.get("tail=1&limit=5")
        self.assertEqual([e["id"] for e in r["entries"]], list(range(last - 4, last + 1)))
        self.assertEqual(r["next"], last + 1)
        self.assertIs(r["truncated"], False)
        r = self.get("tail=1&limit=4&kinds=ERR")
        ids = [e["id"] for e in r["entries"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), 4)
        self.assertTrue(all(e["kind"] == "ERR" for e in r["entries"]))
        self.assertEqual(ids[-1], max(e["id"] for e in self.fake.logs if e["kind"] == "ERR"))
        self.assertEqual(r["next"], ids[-1] + 1)

    def test_tail_limit_and_empty(self):
        for i in range(1200):
            self.fake.entry("OUT", f"line {i}")
        r = self.get("tail=1&limit=5000")
        self.assertEqual(len(r["entries"]), 1000)                  # the daemon's cap
        self.assertEqual(r["entries"][-1]["id"], self.fake.logs[-1]["id"])
        r = self.get("tail=1&limit=3&kinds=NOPE")
        self.assertEqual(r["entries"], [])
        self.assertEqual(r["next"], self.fake.next_id)

    def test_without_tail_unchanged(self):
        first = self.fake.logs[0]["id"]
        r = self.get("limit=2")
        self.assertEqual([e["id"] for e in r["entries"]], [first, first + 1])
        self.assertEqual(r["next"], first + 1)
        self.assertNotIn("truncated", r)
        self.assertEqual(self.get("tail=0&limit=2")["entries"], r["entries"])

    def test_tail_truncated_when_the_ring_dropped_some(self):
        for i in range(self.fake.logs.maxlen + 10):
            self.fake.entry("OUT", f"line {i}")
        self.assertIs(self.get("tail=1&limit=10")["truncated"], True)


STUBS = r"""
var frames = [], timers = [], tid = 0;
globalThis.window = globalThis;
globalThis.document = { hidden: false, addEventListener() {} };
globalThis.requestAnimationFrame = (f) => { frames.push(f); return frames.length; };
globalThis.cancelAnimationFrame = (n) => { frames[n - 1] = null; };
globalThis.setTimeout = (f, ms) => { timers.push({ id: ++tid, f, ms }); return tid; };
globalThis.clearTimeout = (id) => { const i = timers.findIndex(x => x.id === id); if (i >= 0) timers.splice(i, 1); };
globalThis.setInterval = () => 0;
"""

HARNESS = r"""
const out = {};
function frame() { const fs = frames.splice(0); let n = 0; for (const f of fs) if (f) { f(performance.now()); n++; } return n; }
function fire() { const ts = timers.splice(0); for (const x of ts) x.f(); return ts.length; }
function make() {
  const st = appState();
  st.$nextTick = (f) => f();
  st.$refs = {};
  st.say = () => {};
  st.connectEvents = () => {};
  return st;
}
let id = 0;
const entry = (kind, text) => ({ id: ++id, t: 0, kind: kind || 'OUT', text: text || 'line ' + id, addon: null, job: null });

// the stream: many entries, one frame, one update; duplicates and old ids dropped
let st = make();
st.page = 'dev';
let revs = 0, last = st.logRev;
for (let i = 0; i < 50; i++) st.pushLog(entry(i % 10 ? 'OUT' : 'ERR'));
st.pushLog({ id: 3, t: 0, kind: 'OUT', text: 'again' });
st.pushLog({ nope: 1 });
out.before_frame = { total: st.logTotal, rev: st.logRev === last, frames: frames.filter(Boolean).length };
out.frames_run = frame();
out.after_frame = { total: st.logTotal, errs: st.errCount, lastId: st.lastId, revs: st.logRev - last, timers_left: timers.length };
out.frozen = Object.isFrozen(st.visibleLogs[0]);

// the backlog goes in at once; then a trim in a block, the counts following it
for (let i = 0; i < 2300; i++) st.pushLog(entry(i % 4 ? 'OUT' : 'ERR'));
frame();
out.trimmed = { total: st.logTotal, errs: st.errCount,
                rows: st.visibleLogs.length, last_row: st.visibleLogs[st.visibleLogs.length - 1].id,
                lastId: st.lastId };
let total = st.logTotal, rounds = [];
for (let i = 0; i < 30; i++) { st.pushLog(entry()); frame(); rounds.push(st.logTotal); }
out.block = { min: Math.min(...rounds), max: Math.max(...rounds) };

// filters: kinds and text; only the last LOG_ROWS rendered, all of them counted
st.kinds.OUT = false;
out.err_only = { rows: st.visibleLogs.length, count: st.logView.count, kinds: [...new Set(st.visibleLogs.map(e => e.kind))], errs: st.errCount };
st.kinds.OUT = true;
st.filterText = 'line 4';
out.text = { all_match: st.visibleLogs.every(e => e.text.toLowerCase().includes('line 4')), count: st.logView.count };
st.filterText = '';

// pages: no rows off 开发台 (the data still comes), 概览's feed only on 概览
st.page = 'status';
st.pushLog(entry('INFO', 'ping 7: 12 ms'));
st.pushLog(entry('OUT', 'newest'));
frame();
out.status = { rows: st.visibleLogs.length, feed: st.recentLogs.length, first: st.recentLogs[0].text, lastId: st.lastId };
st.page = 'addons';
out.addons = { rows: st.visibleLogs.length, feed: st.recentLogs.length };

// hidden window: a timer instead of the frame
document.hidden = true;
st.pushLog(entry());
st.pushLog(entry());
out.hidden = { frames: frames.filter(Boolean).length, timers: timers.length, total_before: st.logTotal };
fire();
out.hidden.total_after = st.logTotal;
out.hidden.lastId = st.lastId;
document.hidden = false;

// clear keeps the last id (the stream's replay is not shown again); a new daemon starts over
st.clearLogs();
st.pushLog({ id: st.lastId, t: 0, kind: 'OUT', text: 'replayed' });
frame();
out.cleared = { total: st.logTotal, errs: st.errCount };
st.daemonStarted = 1;
st.applyStatus({ daemon: { started: 2 } });
st.pushLog({ id: 1, t: 0, kind: 'ERR', text: 'fresh' });
frame();
out.restart = { total: st.logTotal, errs: st.errCount, lastId: st.lastId };

// the API manual's cursor: the row's key, not a walk of every row
st = make();
st.docs.results = [{ kind: 'function', name: 'A' }, { kind: 'event', name: 'B' }, { kind: 'function', name: 'C' }];
st.docsMove(1); const k1 = st.docs.curKey;
st.docsMove(1); const k2 = st.docs.curKey;
st.docsMove(5); const k3 = st.docs.curKey;
out.cursor = [k1, k2, k3, st.docs.cursor];
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class PageLogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((Path(STATIC_DIR) / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src +"\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_one_update_a_frame(self):
        o = self.out
        self.assertEqual(o["before_frame"], {"total": 0, "rev": True, "frames": 1})       # queued, one frame asked for
        self.assertEqual(o["frames_run"], 1)
        self.assertEqual(o["after_frame"], {"total": 50, "errs": 5, "lastId": 50, "revs": 1, "timers_left": 0})
        self.assertTrue(o["frozen"])

    def test_trim_in_blocks(self):
        t = self.out["trimmed"]
        self.assertLessEqual(t["total"], 2000 + 200)
        self.assertGreaterEqual(t["total"], 2000)
        self.assertEqual(t["rows"], 300)
        self.assertEqual(t["last_row"], t["lastId"])
        b = self.out["block"]
        self.assertGreaterEqual(b["min"], 2000)
        self.assertLessEqual(b["max"], 2200)

    def test_filters_and_counts(self):
        e = self.out["err_only"]
        self.assertEqual(e["kinds"], ["ERR"])
        self.assertEqual(e["count"], e["errs"])                # the counter matches a count of the kept list
        self.assertEqual(e["rows"], min(300, e["count"]))
        self.assertTrue(self.out["text"]["all_match"])
        self.assertGreater(self.out["text"]["count"], 0)

    def test_pages(self):
        s = self.out["status"]
        self.assertEqual(s["rows"], 0)
        self.assertEqual(s["feed"], 60)
        self.assertEqual(s["first"], "newest")                 # newest first, the ping left out
        self.assertEqual(self.out["addons"], {"rows": 0, "feed": 0})

    def test_hidden_window_uses_a_timer(self):
        h = self.out["hidden"]
        self.assertEqual(h["frames"], 0)
        self.assertEqual(h["timers"], 1)
        self.assertEqual(h["total_after"], h["total_before"] + 2)
        self.assertEqual(h["lastId"], self.out["status"]["lastId"] + 2)

    def test_clear_and_restart(self):
        self.assertEqual(self.out["cleared"], {"total": 0, "errs": 0})
        self.assertEqual(self.out["restart"], {"total": 1, "errs": 1, "lastId": 1})

    def test_api_cursor_key(self):
        self.assertEqual(self.out["cursor"], ["function:A", "function:C", "event:B", 2])


if __name__ == "__main__":
    unittest.main()
