"""The 程序 page and the program apps of 接入 Agent: app.js run under Node (skipped without it) against a stand-in for
/api/programs (the shapes of the layers contract, section 7): the list loads and the first program shows, its output is
read from the cursor on (paged, the newest PROG_KEEP lines kept, a terminal's escapes taken out), every second while it
runs and the page shows, once more after it stopped, afresh for a new run; the end is followed until the user scrolls up;
开始 / 停止 / 重启 / 移除 (移除 after a second click) and 登记程序 post what the contract says; the states read
运行中 / 失败 / 已退出 / 未运行 / 清单有错; the stream's `programs` event reads the list again where it is shown. On
接入 Agent a program app has no 启动语 to copy and links to its program; a temporary app is not listed. index.html's page
calls what app.js has."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from wuxianworkshop.ui import STATIC_DIR

NODE = shutil.which("node")

STUBS = r"""
globalThis.window = globalThis;
globalThis.document = { hidden: false, addEventListener() {}, getElementById: () => null };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 0;
globalThis.location = { hash: '' };
globalThis.history = { replaceState() {} };
"""

HARNESS = r"""
const out = {};
const T0 = 1762750000;
let clock = T0, shape = 'list', logsFail = false, rows, logs, apps;
const calls = [];
function apiErr(status, code, message) {
  const e = new Error(code + ': ' + message);
  e.status = status;
  e.data = { error: { code, message } };
  return e;
}
function reset() {
  rows = [
    { id: 'companion', title: '无限陪玩', path: 'C:\\p\\companion', manifest: 'C:\\p\\companion\\wuxian-program.json', app: true,
      state: 'running', pid: 4242, started: T0, exit_code: null, exited_at: null,
      command: ['C:\\Program Files\\py\\python.exe', '-m', 'companion.main'], error: null },
    { id: 'ah', title: { zh: '拍卖行采集', en: 'AH scan' }, path: 'C:\\p\\ah', manifest: '', app: false, state: 'exited', pid: null,
      started: T0 - 600, exit_code: 3221225477, exited_at: T0 - 540, command: 'python collect.py', error: null },
    { id: 'quiet', title: '', path: 'C:\\p\\quiet', app: false, state: 'exited', pid: null, started: T0 - 100, exit_code: 0,
      exited_at: T0 - 90, command: ['q.exe'], error: null },
    { id: 'fresh', title: '新程序', path: 'C:\\p\\fresh', app: true, state: 'stopped', pid: null, started: null, exit_code: null,
      exited_at: null, command: ['main.py'], error: null },
    { id: 'broken', title: '坏清单', path: 'C:\\p\\broken', app: false, state: 'stopped', pid: null, started: null, exit_code: null,
      exited_at: null, command: null, error: 'run: argv[0] not found' },
  ];
  logs = {
    companion: Array.from({ length: 1199 }, (_, i) => ({ n: i + 1, text: 'line ' + (i + 1) })).concat([{ n: 1200, text: '\x1b[31mred\x1b[0m\r' }]),
    ah: [{ n: 1, text: 'Traceback (most recent call last):' }, { n: 2, text: 'boom' }],
    quiet: [], fresh: [],
  };
}
const copyOf = (r) => JSON.parse(JSON.stringify(r));
async function api(path, opts) {
  const body = opts && opts.body;
  calls.push([path, body || null]);
  const row = (id) => rows.find((r) => r.id === id);
  if (path === '/api/programs') return shape === 'wrap' ? { programs: rows.map(copyOf) } : rows.map(copyOf);
  if (path.startsWith('/api/programs/logs?')) {
    const q = new URLSearchParams(path.slice(path.indexOf('?') + 1));
    const id = q.get('id'), since = Number(q.get('since')), limit = Number(q.get('limit'));
    if (logsFail) throw apiErr(500, 'error', 'disk');
    if (!row(id) || id === 'broken') throw apiErr(404, 'not_found', 'no output yet');
    const lines = logs[id].filter((l) => l.n > since).slice(0, limit);
    return { lines, next: lines.length ? lines[lines.length - 1].n : since, row: copyOf(row(id)) };
  }
  if (path === '/api/programs/start') {
    const r = row(body.id);
    if (r.error) throw apiErr(400, 'bad_manifest', r.error);
    if (r.state === 'running' && !body.restart) throw apiErr(409, 'running', 'already running');
    Object.assign(r, { state: 'running', pid: 5001, started: ++clock, exit_code: null, exited_at: null });
    logs[r.id] = [{ n: 1, text: body.restart ? 'again' : 'hello' }];
    return { row: copyOf(r) };                                   // the row wrapped: taken too
  }
  if (path === '/api/programs/stop') {
    const r = row(body.id);
    Object.assign(r, { state: 'exited', pid: null, exit_code: 1, exited_at: ++clock });
    return copyOf(r);
  }
  if (path === '/api/programs/remove') { rows = rows.filter((r) => r.id !== body.id); return { removed: true }; }
  if (path === '/api/programs/add') {
    if (body.path === 'C:\\nope') throw apiErr(400, 'no_manifest', 'no wuxian-program.json in C:\\nope');
    const r = { id: 'newp', title: '新的', path: body.path, app: false, state: 'stopped', pid: null, started: null, exit_code: null,
                exited_at: null, command: ['main.py'], error: null };
    rows.push(r);
    return copyOf(r);
  }
  if (path === '/api/kit/apps') return apps;
  if (path.startsWith('/api/agents')) return { program: null, hosts: [], manual: {} };
  throw new Error('unexpected ' + path);
}
const box = { scrollTop: 0, scrollHeight: 9999, clientHeight: 300 };
function make() {
  const st = appState();
  st.token = 't';
  st.$nextTick = (f) => f();
  st.$refs = { progout: box };
  st.said = [];
  st.say = (x) => st.said.push(x);
  st.copied = [];
  st.copy = (text, what) => st.copied.push([text, what]);
  st.api = api;
  return st;
}
const settle = async () => { for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r)); };
const reads = () => calls.filter((c) => c[0].startsWith('/api/programs/logs')).map((c) => c[0]);
const posts = () => calls.filter((c) => c[1]).map((c) => [c[0], c[1]]);
const lastLine = (st) => st.pout.text.split('\n').pop();

(async () => {
  reset();
  const st = make();

  // the page: the list, the first program shown, its output read in pages from the cursor, the newest kept
  st.go('programs');
  await settle();
  out.load = { calls: calls.map((c) => c[0]), sel: st.progs.sel, count: st.pout.count, first: st.pout.text.split('\n')[0],
               last: lastLine(st), next: st.pout.next, line: st.progOutLine, summary: st.progSummary, scrolled: box.scrollTop };

  // the states, and what the detail says of a program
  st.now = (T0 + 125) * 1000;
  const L = st.progs.list;
  out.states = L.map((r) => [r.id, st.progState(r).key, st.progState(r).cls, st.progState(r).text, st.progSub(r)]);
  out.facts = { cmd: st.progCommand(L[0]), cmd2: st.progCommand(L[1]), none: st.progCommand(L[4]), code: st.progCode(3221225477),
                code0: st.progCode(0), nocode: st.progCode(null), started: st.progStarted(L[0]), ran: st.progRan(L[1]),
                title: st.progTitle(L[1]), untitled: st.progTitle(L[2]), cur: st.progCur.id, none_cur: appState().progCur.state };

  // every second while it runs: the new lines from the cursor, the end followed
  calls.length = 0;
  box.scrollTop = 0;
  logs.companion.push({ n: 1201, text: 'new 1' }, { n: 1202, text: 'new 2' });
  st.progTick();
  await settle();
  out.tick = { reads: reads(), count: st.pout.count, last: lastLine(st), scrolled: box.scrollTop };

  // scrolled up: the end is not followed; back at the end: followed again
  box.scrollTop = 0;
  st.onProgOutScroll({ target: box });
  const off = st.pout.follow;
  logs.companion.push({ n: 1203, text: 'new 3' });
  st.progTick();
  await settle();
  out.follow = { off, kept: box.scrollTop, last: lastLine(st) };
  box.scrollTop = box.scrollHeight - box.clientHeight;
  st.onProgOutScroll({ target: box });
  out.follow.on = st.pout.follow;

  // another page, or the window hidden: no reads
  calls.length = 0;
  st.page = 'dev';
  st.progTick();
  st.page = 'programs';
  document.hidden = true;
  st.progTick();
  document.hidden = false;
  await settle();
  out.away = calls.length;

  // 停止: its last lines are read twice more, then no more
  calls.length = 0;
  await st.progAct(st.selProg, 'stop');
  out.stop = { posts: posts(), key: st.progState(st.selProg).key, sub: st.progSub(st.selProg), said: st.said[st.said.length - 1] };
  logs.companion.push({ n: 1204, text: 'bye' });
  calls.length = 0;
  for (let i = 0; i < 3; i++) { st.progTick(); await settle(); }
  out.afterStop = { reads: reads().length, last: lastLine(st) };

  // 开始: a new run, its output read afresh from the start
  calls.length = 0;
  await st.progAct(st.selProg, 'start');
  await settle();
  out.start = { posts: posts(), reads: reads(), text: st.pout.text, key: st.progState(st.selProg).key, said: st.said[st.said.length - 1] };

  // 重启
  calls.length = 0;
  await st.progAct(st.selProg, 'restart');
  await settle();
  out.restart = { posts: posts(), text: st.pout.text };

  // a program that never ran (the output answers 404), one with no output, one whose output cannot be read; a failed start
  st.progSelect('broken');
  await settle();
  out.broken = { empty: st.progOutEmpty, key: st.progState(st.selProg).key };
  await st.progAct(st.selProg, 'start');
  out.broken.note = st.progs.note;
  st.progSelect('quiet');
  await settle();
  out.quiet = st.progOutEmpty;
  logsFail = true;
  st.progSelect('ah');
  await settle();
  out.failed = st.progOutEmpty;
  logsFail = false;
  st.progSelect('ah');
  await settle();
  out.ah = { text: st.pout.text, empty: st.progOutEmpty, line: st.progOutLine };

  // 移除: a second click removes; the list is read again and its first program shown
  calls.length = 0;
  const ah = st.selProg;
  await st.progAct(ah, 'remove');
  out.remove1 = { calls: calls.length, confirm: st.progs.confirm };
  await st.progAct(ah, 'remove');
  await settle();
  out.remove2 = { posts: posts(), ids: st.progs.list.map((r) => r.id), sel: st.progs.sel, said: st.said[st.said.length - 1] };

  // 登记程序: a folder without a manifest, then one with
  st.progAddOpen();
  st.progs.add.path = '  C:\\nope  ';
  await st.progAdd();
  out.addBad = { error: st.progs.add.error, open: st.progs.add.open };
  calls.length = 0;
  st.progs.add.path = 'C:\\p\\new';
  await st.progAdd();
  await settle();
  out.add = { posts: posts(), sel: st.progs.sel, open: st.progs.add.open, path: st.progs.add.path, said: st.said[st.said.length - 1] };

  // the stream's `programs` event: the list read again on this page and on 接入 Agent, not elsewhere; the list wrapped
  calls.length = 0;
  st.onPrograms();
  await settle();
  const onPage = calls.map((c) => c[0]);
  st.page = 'dev';
  calls.length = 0;
  st.onPrograms();
  await settle();
  const elsewhere = calls.length;
  st.page = 'connect';
  shape = 'wrap';
  calls.length = 0;
  st.onPrograms();
  await settle();
  out.sse = { onPage, elsewhere, connect: calls.map((c) => c[0]), wrapped: st.progs.list.length };

  // 接入 Agent: a program app links to its program (when registered), has no 启动语 and no agent checklist; temporary
  // apps are not listed; an app without driver is a prompt app when it has a prompt
  apps = { online: true, apps: [
    { id: 'guild', title: '公会聊天', by: 'WuxianKit', addon: 'WuxianKit', prompt: 'guild-chat', driver: 'prompt', start: 'go guild',
      on: false, blocked: null, extension: null, skill: null },
    { id: 'companion', title: '无限陪玩', by: 'x', addon: null, prompt: null, driver: 'program', start: null, on: true, blocked: null,
      extension: { id: 'game', title: '游戏', on: true }, skill: null },
    { id: 'lonely', title: '没登记', prompt: null, driver: 'program', start: null, on: null, blocked: null, extension: null, skill: null },
    { id: 'probe', title: '临时', prompt: 'x', driver: 'prompt', temp: true, start: 'go probe', on: true, blocked: null, extension: null,
      skill: null },
    { id: 'old', title: '旧的', prompt: 'old', start: 'go old', on: false, blocked: null, extension: null, skill: null },
  ] };
  const cp = make();
  cp.go('connect');
  await settle();
  const shown = cp.appsShown;
  out.connect = { shown: shown.map((a) => a.id), drivers: shown.map((a) => cp.appDriver(a)),
                  texts: shown.map((a) => (cp.appDriver(a) === 'program' ? cp.appDriverText(a) : '')),
                  linked: shown.map((a) => !!cp.appProgram(a)), checks: cp.appChecks(shown[1]).map((c) => c.id),
                  promptChecks: cp.appChecks(shown[0]).map((c) => c.id) };
  calls.length = 0;
  cp.goProgram('companion');
  await settle();
  out.goProgram = { page: cp.page, sel: cp.progs.sel, reads: reads().length > 0 };

  // in English
  globalThis.Alpine = { store: () => ({ v: 'en' }) };
  st.lang = 'en';
  cp.lang = 'en';
  out.english = {
    states: [{ state: 'running' }, { state: 'exited', exit_code: 2 }, { state: 'exited', exit_code: 0 }, { state: 'stopped' },
             { state: 'stopped', error: 'x' }].map((r) => st.progState(r).text),
    sub: st.progSub({ id: 'x', state: 'exited', exit_code: 2 }), summary: st.progSummary,
    driven: cp.appDriverText(shown[1]), unlinked: cp.appDriverText(shown[2]), never: (st.progSelect('fresh'), await settle(), st.progOutEmpty),
  };
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class ProgramsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((Path(STATIC_DIR) / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_page_loads_the_list_and_the_first_output(self):
        o = self.out["load"]
        self.assertEqual(o["calls"], ["/api/programs", "/api/programs/logs?id=companion&since=0&limit=500",
                                      "/api/programs/logs?id=companion&since=500&limit=500",
                                      "/api/programs/logs?id=companion&since=1000&limit=500"])
        self.assertEqual(o["sel"], "companion")
        self.assertEqual((o["count"], o["first"], o["last"], o["next"]), (500, "line 701", "red", 1200))   # escapes out
        self.assertEqual(o["line"], "最近 500 行")
        self.assertEqual(o["summary"], "5 个程序 · 1 个运行中")
        self.assertEqual(o["scrolled"], 9999)                                                 # the end followed

    def test_states(self):
        self.assertEqual(self.out["states"], [
            ["companion", "running", "ok", "运行中", "运行中 · 2 分钟"],
            ["ah", "failed", "bad", "失败", "失败 · 退出码 0xC0000005"],                    # the list: its hex
            ["quiet", "exited", "", "已退出", "已退出"],
            ["fresh", "stopped", "", "未运行", "未运行"],
            ["broken", "error", "warn", "清单有错", "清单有错"],
        ])

    def test_facts(self):
        f = self.out["facts"]
        self.assertEqual(f["cmd"], '"C:\\Program Files\\py\\python.exe" -m companion.main')
        self.assertEqual((f["cmd2"], f["none"]), ("python collect.py", "—"))
        self.assertEqual((f["code"], f["code0"], f["nocode"]), ("3221225477 (0xC0000005)", "0", "—"))
        self.assertRegex(f["started"], r" · 已运行 2 分钟$")
        self.assertEqual(f["ran"], "1 分 0 秒")
        self.assertEqual((f["title"], f["untitled"], f["cur"], f["none_cur"]), ("拍卖行采集", "quiet", "companion", "stopped"))

    def test_output_follows_the_cursor_while_it_runs(self):
        t = self.out["tick"]
        self.assertEqual(t["reads"], ["/api/programs/logs?id=companion&since=1200&limit=500"])
        self.assertEqual((t["count"], t["last"], t["scrolled"]), (500, "new 2", 9999))
        f = self.out["follow"]
        self.assertEqual((f["off"], f["kept"], f["last"], f["on"]), (False, 0, "new 3", True))
        self.assertEqual(self.out["away"], 0)

    def test_stop_reads_the_last_lines(self):
        s = self.out["stop"]
        self.assertEqual(s["posts"], [["/api/programs/stop", {"id": "companion"}]])
        self.assertEqual((s["key"], s["sub"], s["said"]), ("failed", "失败 · 退出码 1", "已停止 无限陪玩"))
        self.assertEqual(self.out["afterStop"], {"reads": 2, "last": "bye"})

    def test_start_and_restart_read_a_new_run_afresh(self):
        s = self.out["start"]
        self.assertEqual(s["posts"], [["/api/programs/start", {"id": "companion"}]])
        self.assertEqual(s["reads"], ["/api/programs/logs?id=companion&since=0&limit=500"])
        self.assertEqual((s["text"], s["key"], s["said"]), ("hello", "running", "已开始 无限陪玩"))
        r = self.out["restart"]
        self.assertEqual(r["posts"], [["/api/programs/start", {"id": "companion", "restart": True}]])
        self.assertEqual(r["text"], "again")

    def test_empty_and_failed(self):
        b = self.out["broken"]
        self.assertEqual((b["empty"], b["key"]), ("还没有运行过", "error"))                   # a 404: no output yet
        self.assertEqual(b["note"], {"bad": True, "text": "开始失败：run: argv[0] not found"})
        self.assertEqual(self.out["quiet"], "没有输出")
        self.assertEqual(self.out["failed"], "读取输出失败：disk")
        self.assertEqual(self.out["ah"], {"text": "Traceback (most recent call last):\nboom", "empty": "", "line": "2 行"})

    def test_remove_wants_a_second_click(self):
        self.assertEqual(self.out["remove1"], {"calls": 0, "confirm": "ah"})
        r = self.out["remove2"]
        self.assertEqual(r["posts"], [["/api/programs/remove", {"id": "ah"}]])
        self.assertEqual((r["ids"], r["sel"], r["said"]), (["companion", "quiet", "fresh", "broken"], "companion", "已移除 拍卖行采集"))

    def test_register(self):
        self.assertEqual(self.out["addBad"], {"error": "no wuxian-program.json in C:\\nope", "open": True})
        a = self.out["add"]
        self.assertEqual(a["posts"], [["/api/programs/add", {"path": "C:\\p\\new"}]])
        self.assertEqual((a["sel"], a["open"], a["path"], a["said"]), ("newp", False, "", "已登记 新的"))

    def test_stream_event(self):
        s = self.out["sse"]
        self.assertEqual((s["onPage"], s["elsewhere"], s["connect"], s["wrapped"]), (["/api/programs"], 0, ["/api/programs"], 5))

    def test_connect_program_apps(self):
        c = self.out["connect"]
        self.assertEqual(c["shown"], ["guild", "companion", "lonely", "old"])                # no temporary app
        self.assertEqual(c["drivers"], ["prompt", "program", "program", "prompt"])
        self.assertEqual(c["texts"], ["", "由程序「无限陪玩」驱动", "由程序驱动", ""])
        self.assertEqual(c["linked"], [False, True, False, False])
        self.assertEqual(c["checks"], ["ext"])                                                # no agent to connect
        self.assertEqual(c["promptChecks"], ["wuxian"])
        self.assertEqual(self.out["goProgram"], {"page": "programs", "sel": "companion", "reads": True})

    def test_english(self):
        e = self.out["english"]
        self.assertEqual(e["states"], ["Running", "Failed", "Exited", "Not running", "Manifest error"])
        self.assertEqual(e["sub"], "Failed · exit code 2")
        self.assertEqual(e["summary"], "Programs: 5 · running: 1")
        self.assertEqual((e["driven"], e["unlinked"]), ('Driven by the program "无限陪玩"', "Driven by a program"))
        self.assertEqual(e["never"], "Not run yet")


class Template(unittest.TestCase):
    """the 程序 page in index.html calls what app.js has; the navigation has it right after 插件; the stream's event is
    listened to; its loaders say they are a status; on 接入 Agent a program app has no 复制启动语 and links to its program"""

    def setUp(self):
        self.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = self.html.index('<section class="page" x-t x-show="page === \'programs\'">')
        self.part = self.html[start:self.html.index("</section>", start)]
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def test_calls_exist(self):
        exprs = " ".join(v for _, v in re.findall(r'\s(x-[\w:.-]+|:[\w-]+|@[\w.:-]+)="([^"]*)"', self.part))
        names = set(re.findall(r"(?<![\w.$'])([A-Za-z_]\w*)\s*\(", exprs)) - {"t", "in"}    # x-for="r in (…)"
        self.assertTrue({"progAct", "progSelect", "progState", "progSub", "progAdd", "progPick", "loadPrograms", "onProgOutScroll"} <= names)
        for name in names:
            self.assertTrue(re.search(r"(?m)^\s*(?:async\s+)?%s\s*\(" % name, self.js), name)
        for getter in ("selProg", "progCur", "progSummary", "progOutLine", "progOutEmpty"):
            self.assertIn(getter, exprs)
            self.assertTrue(re.search(r"(?m)^\s*get\s+%s\s*\(" % getter, self.js), getter)
        self.assertIn('x-ref="progout"', self.part)

    def test_navigation(self):
        pages = self.js[self.js.index("const PAGES = ["):self.js.index("];", self.js.index("const PAGES = ["))]
        ids = re.findall(r"id: '(\w+)'", pages)
        self.assertEqual(ids.index("programs"), ids.index("addons") + 1)
        self.assertIn('<symbol id="i-program"', self.html)
        self.assertIn("es.addEventListener('programs'", self.js)

    def test_loaders_say_status(self):
        loaders = re.findall(r'<div class="loader"[^>]*>', self.part)
        self.assertEqual(len(loaders), 2)
        for tag in loaders:
            self.assertIn('role="status"', tag)

    def test_connect_program_rows(self):
        start = self.html.index('<div class="panel" id="connect-apps">')
        part = self.html[start:self.html.index('<div class="panel">', start)]
        self.assertIn('x-show="appDriver(a) !== \'program\'" @click="copy(a.start, t(\'启动语\'))"', part)
        self.assertIn('x-show="appProgram(a)" @click="goProgram(a.id)"', part)
        self.assertIn('x-text="appDriverText(a)"', part)


if __name__ == "__main__":
    unittest.main()
