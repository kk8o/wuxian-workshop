"""The log of a local extension across a moved output.log (review-0913: the 日志 tab stood still after a rotation).

A full output.log becomes output.1.log and the lines of the new file count from 1 again. The daemon answers a cursor
past the new file's end with `reset` and the lines from the first (logs.window); the page told a line it had from one
it had not by the newest line number shown, which stayed the old file's, so every line of the new file was dropped
while the cursor went on: the log stood still until another extension was selected. Here app.js runs under Node (skipped
without it) against a stand-in that answers as Host.logs does (since: a line number, next: the line after the last one
given, total, reset): after a reset the new lines show under the old ones and the reads go on; a file not there yet
(reset, no lines) is read from its first line once it is; a file that turned out shorter while the page was still
catching up (no reset: the cursor is inside the new file) is read from its first line too. And logs.window itself
across a real move of the file."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wuxianworkshop.local import host as H
from wuxianworkshop.local import logs as L
from wuxianworkshop.ui import STATIC_DIR

from tests.ui.test_local import NODE, STUBS

HARNESS = r"""
const out = {};
const T0 = 1762750000;
const calls = [];
let file = [];                                   // output.log: the texts of its lines
let before = null;                               // run before the next read of the log is answered, once
const row = { id: 'voice', title: '声音', version: '0.1.0', path: 'C:\\ext\\voice', enabled: true, start: 'use', state: 'running',
              pid: 4242, started: T0, since: T0, why: null, exit_code: null, error: null, approval: 'ok', adds: {},
              command: ['python', 'voice.py'], capabilities: [], sources: [], options: [], apps: [], skills: [] };
function answer(since, limit) {                  // Host.logs over logs.window
  const total = file.length;
  let first = since, reset = false;
  if (since === 0) first = Math.max(1, total - limit + 1);
  else if (since > total + 1) { first = 1; reset = true; }
  const last = Math.min(total, first + limit - 1);
  const texts = last >= first ? file.slice(first - 1, last) : [];
  return { lines: texts.map((text, i) => ({ n: first + i, text })), next: first + texts.length, total, reset,
           cut: since === 0 && first > 1, row };
}
async function api(path) {
  calls.push(path);
  if (path === '/api/extensions') return { addons: [], catalog: null, addons_dir: null };
  if (path.startsWith('/api/agent_access')) return { kit: null, kit_state: 'absent', kit_addon: null, servers: [] };
  if (path === '/api/local') return { revision: 1, extensions: [JSON.parse(JSON.stringify(row))] };
  if (path === '/api/local/manifest') return { revision: 1, extensions: [] };
  if (path.startsWith('/api/local/logs?')) {
    const q = new URLSearchParams(path.slice(path.indexOf('?') + 1));
    if (before) { const f = before; before = null; f(); }
    return answer(Number(q.get('since')), Number(q.get('limit')));
  }
  throw new Error('unexpected ' + path);
}
const fill = (word, n, from) => Array.from({ length: n }, (_, i) => word + ((from || 1) + i));
const settle = async () => { for (let i = 0; i < 60; i++) await new Promise((r) => setImmediate(r)); };
const sinces = () => calls.filter((p) => p.startsWith('/api/local/logs')).map((p) => Number(new URLSearchParams(p.slice(p.indexOf('?') + 1)).get('since')));

(async () => {
  const st = appState();
  st.token = 't';
  st.$nextTick = (f) => f();
  st.$refs = {};
  st.say = () => {};
  st.api = api;
  st.now = (T0 + 125) * 1000;
  st.go('extensions');
  await settle();
  st.xpSelect('l:voice');
  const text = () => st.lout.text.split('\n');
  const tick = async () => { calls.length = 0; st.lxTick(); await settle(); };
  const shot = () => ({ sinces: sinces(), count: st.lout.count, next: st.lout.next, tail: text().slice(-4) });

  // the log as it is: the newest lines, then what comes
  file = fill('a', 450);
  calls.length = 0;
  st.lxTab('logs');
  await settle();
  out.first = shot();
  file.push('a451', 'a452');
  await tick();
  out.grown = shot();

  // output.log is full: it becomes output.1.log, the new one has three lines. The cursor is past its end: reset
  file = fill('b', 3);
  await tick();
  out.moved = shot();
  file.push('b4');
  await tick();
  out.after = shot();
  await tick();
  out.idle = shot();                             // nothing new: nothing twice

  // moved again, and read while the new file is not there yet: reset with no lines, then its lines from the first
  file = [];
  await tick();
  out.gap = shot();
  file = fill('c', 2);
  await tick();
  out.filled = shot();

  // the page is behind (more came than a read gives) and the file is moved between two reads: the next answer is from
  // the middle of a shorter file, without reset; the new file is read from its first line
  file = file.concat(fill('c', 698, 3));         // c1..c700: the page has two of them
  let n = 0;
  const hook = () => { n++; if (n === 2) file = fill('d', 430); else before = hook; };
  before = hook;
  await tick();
  out.behind = shot();
  out.behind.has = { c202: text().includes('c202'), c203: text().includes('c203'), d1: text().includes('d1'),
                     d203: text().indexOf('d203') === text().lastIndexOf('d203') && text().includes('d203') };
  out.behind.order = text().indexOf('d1') > text().indexOf('c202');
  out.kept = { most: st.lout.count <= 500, line: st.lxOutLine };
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((STATIC_DIR / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_the_newest_lines_then_what_comes(self):
        self.assertEqual(self.out["first"], {"sinces": [0, 451], "count": 200, "next": 451, "tail": ["a447", "a448", "a449", "a450"]})
        self.assertEqual(self.out["grown"], {"sinces": [451], "count": 202, "next": 453, "tail": ["a449", "a450", "a451", "a452"]})

    def test_a_moved_file_goes_on(self):
        self.assertEqual(self.out["moved"], {"sinces": [453], "count": 205, "next": 4, "tail": ["a452", "b1", "b2", "b3"]})
        self.assertEqual(self.out["after"], {"sinces": [4], "count": 206, "next": 5, "tail": ["b1", "b2", "b3", "b4"]})
        self.assertEqual(self.out["idle"], {"sinces": [5], "count": 206, "next": 5, "tail": ["b1", "b2", "b3", "b4"]})

    def test_a_file_not_there_yet(self):
        self.assertEqual(self.out["gap"], {"sinces": [5], "count": 206, "next": 1, "tail": ["b1", "b2", "b3", "b4"]})
        self.assertEqual(self.out["filled"], {"sinces": [1], "count": 208, "next": 3, "tail": ["b3", "b4", "c1", "c2"]})

    def test_moved_while_catching_up(self):
        o = self.out["behind"]
        # c3..c202, then the file is another: the answer to 203 (d203..d402, no reset) is not taken, d1.. is read
        self.assertEqual(o["sinces"], [3, 203, 1, 201, 401])
        self.assertEqual((o["next"], o["tail"]), (431, ["d427", "d428", "d429", "d430"]))
        self.assertEqual(o["has"], {"c202": True, "c203": False, "d1": True, "d203": True})
        self.assertTrue(o["order"])
        self.assertEqual(self.out["kept"], {"most": True, "line": "最近 500 行"})


class Window(unittest.TestCase):
    """logs.window over a file that Output moved: what the page's cursor meets"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = Path(self.dir) / "extensions" / "fx" / "output.log"
        self.out = L.Output(self.path)
        self.addCleanup(self.out.close)
        self.lines = L.Lines(self.path)

    def test_a_cursor_past_the_new_end(self):
        for i in range(30):
            self.out.write(f"line {i}")
        first, texts, total, reset = L.window(self.lines, 0, 10)
        self.assertEqual((first, texts[-1], total, reset), (21, "line 29", 30, False))
        with mock.patch.object(H, "LOG_MAX", 200):
            self.out.write("x" * 50)
        self.out.write("y")
        self.assertTrue(self.path.with_name("output.1.log").is_file())
        self.assertEqual(L.window(self.lines, 31, 10), (1, ["x" * 50, "y"], 2, True))        # from the first, said so
        self.assertEqual(L.window(self.lines, 3, 10), (3, [], 2, False))                     # the cursor it handed back

    def test_a_cursor_inside_the_new_file(self):
        for i in range(30):
            self.out.write(f"line {i}")
        self.assertEqual(L.window(self.lines, 5, 3)[2:], (30, False))
        with mock.patch.object(H, "LOG_MAX", 200):
            self.out.write("first of the new file")
        for i in range(9):
            self.out.write(f"new {i}")
        first, texts, total, reset = L.window(self.lines, 8, 3)                              # no reset: only total tells
        self.assertEqual((first, texts, total, reset), (8, ["new 6", "new 7", "new 8"], 10, False))

    def test_a_file_that_is_not_there(self):
        self.out.write("one")
        self.assertEqual(L.window(self.lines, 0, 10), (1, ["one"], 1, False))
        self.out.close()
        self.path.unlink()
        self.assertEqual(L.window(self.lines, 2, 10), (1, [], 0, True))                      # past the end of nothing
        self.assertEqual(L.window(self.lines, 1, 10), (1, [], 0, False))                     # the cursor it handed back


if __name__ == "__main__":
    unittest.main()
