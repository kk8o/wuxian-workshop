"""The 本机 part of the 扩展 page and what took the 程序 page's place: app.js run under Node (skipped without it) against a
stand-in for /api/local* (the shapes of the layers contract, third part, section 3): the page reads the rows and the
manifest's capabilities; a row's state reads 运行中 / 启动中 / 清单有误 / 异常退出 / 已退出 / 未运行 / 已停用 (the rest of
the eight states and 批准: tests/ui/test_local_approval.py); the one selected shows its facts, what it adds to other apps
(补充: the row's adds, by app), its options as a form (a change posts that option alone, typed as its kind), its capabilities,
its log (read from the cursor on, paged, a terminal's escapes taken out, every second while it runs and the tab shows,
twice more after it stopped); 启用, 载入, 停止 and 检查 post what the contract says and show the check's problems; the
stream's `local` event carries the rows (the log across a moved output.log: tests/ui/test_local_rotation.py). Nothing of the 程序 page is left in the page's files."""
import json
import re
import shutil
import subprocess
import unittest

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
let rows, logs, revision = 1, failSet = false;
const calls = [];
function apiErr(status, code, message) {
  const e = new Error(code + ': ' + message);
  e.status = status;
  e.data = { error: { code, message } };
  return e;
}
const copyOf = (r) => JSON.parse(JSON.stringify(r));
function reset() {
  rows = [
    { id: 'voice', title: '声音', version: '0.1.0', path: 'C:\\ext\\voice', enabled: true, start: 'use', state: 'running',
      pid: 4242, started: T0, exit_code: null, error: null, capabilities: ['voice.say', 'voice.status'], sources: ['said'],
      apps: ['companion'], skills: ['companion'],
      options: [{ name: 'volume', kind: 'number', title: '音量', default: 80, min: 0, max: 100, unit: '%', value: 65 },
                { name: 'quiet', kind: 'boolean', title: '安静', default: false },
                { name: 'pace', kind: 'choice', title: '语速', default: 1, choices: [{ value: 1, label: '正常' }, { value: 2, label: '快' }] },
                { name: 'name', kind: 'text', title: '称呼', default: '', hint: '怎么称呼玩家' }] },
    { id: 'warm', title: '预热', path: 'C:\\ext\\warm', enabled: true, start: 'workshop', state: 'starting', pid: 5, started: T0,
      capabilities: [], options: [] },
    { id: 'broke', title: '', path: 'C:\\ext\\broke', enabled: true, start: 'use', state: 'stopped', pid: null, started: null,
      exit_code: null, error: 'run: a list of strings', command: null, capabilities: [], options: [] },
    { id: 'crash', title: '崩溃', path: 'C:\\ext\\crash', enabled: true, start: 'use', state: 'exited', exit_code: 3221225477,
      started: T0 - 60, capabilities: ['crash.x'], options: [] },
    { id: 'done', title: '退出', path: 'C:\\ext\\done', enabled: true, start: 'use', state: 'exited', exit_code: 0,
      started: T0 - 60, capabilities: [], options: [] },
    { id: 'idle', title: '没跑', path: 'C:\\ext\\idle', enabled: true, start: 'use', state: 'stopped', capabilities: [], options: [] },
    { id: 'off', title: '停用的', path: 'C:\\ext\\off', enabled: false, start: 'use', state: 'off', capabilities: [], options: [] },
  ];
  logs = {
    voice: Array.from({ length: 449 }, (_, i) => ({ n: i + 1, text: 'line ' + (i + 1) })).concat([{ n: 450, text: '\x1b[32mgreen\x1b[0m\r' }]),
  };
}
async function api(path, opts) {
  const body = opts && opts.body;
  calls.push([path, body || null]);
  const row = (id) => rows.find((r) => r.id === id);
  if (path === '/api/extensions') return { addons: [], catalog: null, addons_dir: null };
  if (path.startsWith('/api/agent_access')) return { kit: null, kit_state: 'absent', kit_addon: null, servers: [] };
  if (path === '/api/local') return { revision, extensions: rows.map(copyOf) };
  if (path === '/api/kit/apps') return { online: true, apps: [{ id: 'guild', title: '公会聊天' }] };
  if (path === '/api/local/manifest') return { revision, extensions: [{ id: 'voice', capabilities: [
    { id: 'voice.say', kind: 'point', title: '说一句', doc: 'speak', args: {} },
    { id: 'voice.status', kind: 'see', title: '状态', doc: '', args: {} }] }] };
  if (path.startsWith('/api/local/logs?')) {
    const q = new URLSearchParams(path.slice(path.indexOf('?') + 1));
    const id = q.get('id'), since = Number(q.get('since')), limit = Number(q.get('limit'));
    if (!logs[id]) throw apiErr(404, 'not_found', 'no log yet');
    const lines = logs[id].filter((l) => l.n > since).slice(0, limit);
    return { lines, next: lines.length ? lines[lines.length - 1].n : since, cut: false };
  }
  if (path === '/api/local/set') {
    if (failSet) throw apiErr(400, 'bad_options', 'volume: at most 100');
    const r = row(body.id);
    if (body.enabled !== undefined) Object.assign(r, { enabled: body.enabled, state: body.enabled ? 'stopped' : 'off' });
    for (const [k, v] of Object.entries(body.options || {})) r.options.find((o) => o.name === k).value = v;
    return copyOf(r);
  }
  if (path === '/api/local/load') {
    const r = row(body.id);
    Object.assign(r, { state: 'running', pid: 6001, started: T0 + 100, exit_code: null });
    logs[r.id] = [{ n: 1, text: 'loaded' }];
    return { row: copyOf(r), check: { ok: false, capabilities: ['voice.say'], problems: [
      { level: 'error', where: 'capabilities[1]', message: 'kind "say" is step two', hint: 'use point' },
      { level: 'warning', where: 'docs', message: 'no AGENT.md', hint: '' }] } };
  }
  if (path === '/api/local/stop') {
    const r = row(body.id);
    Object.assign(r, { state: 'exited', pid: null, exit_code: 0 });
    return { ok: true };                                       // no row: the list is read again
  }
  if (path === '/api/local/check') return { ok: true, problems: [], capabilities: ['voice.say', 'voice.status'],
                                            tests: { ran: 3, failed: 1, output: 'FAIL: test_say' } };
  throw new Error('unexpected ' + path);
}
const box = { scrollTop: 0, scrollHeight: 9999, clientHeight: 300 };
function make() {
  const st = appState();
  st.token = 't';
  st.$nextTick = (f) => f();
  st.$refs = { lxout: box };
  st.said = [];
  st.say = (x) => st.said.push(x);
  st.copied = [];
  st.copy = (text, what) => st.copied.push([text, what]);
  st.api = api;
  return st;
}
const settle = async () => { for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r)); };
const reads = () => calls.filter((c) => c[0].startsWith('/api/local/logs')).map((c) => c[0]);
const posts = () => calls.filter((c) => c[1]).map((c) => [c[0], c[1]]);

(async () => {
  reset();
  const st = make();
  st.now = (T0 + 125) * 1000;

  // the page: the rows and the capabilities of the manifest; the page bar
  st.go('extensions');
  await settle();
  out.load = { calls: calls.map((c) => c[0]).filter((p) => p.startsWith('/api/local')), ids: st.lx.list.map((r) => r.id),
               summary: st.lxSummary, bar: st.extSummary, nav: PAGES.map((p) => p.id) };
  out.states = st.lx.list.map((r) => [r.id, st.lxState(r).text, st.lxPill(r)]);
  st.lxApply({ revision, extensions: rows.map(copyOf), problem: 'extensions.json cannot be read' });
  const problem = st.lx.problem;
  st.lxApply({ revision, extensions: rows.map(copyOf), problem: null });
  out.problem = [problem, st.lx.problem];

  // one selected: its facts, its capabilities, its tabs
  st.xpSelect('l:voice');
  const sel = st.xpSel;
  out.sel = { kind: sel.kind, title: sel.title, tag: sel.tag, line: sel.line, tab: st.xpTab, cur: st.lxCur.id,
              started: st.lxStarted(st.lxCur), start: st.lxStart(st.lxCur), caps: st.lxCaps(st.lxCur),
              kinds: st.lxCaps(st.lxCur).map((c) => st.lxKind(c.kind)), untitled: st.lxTitle(st.lx.list[2]),
              code: st.lxCode(3221225477) };
  st.lxTab('opts');
  out.opts = st.xpTab;
  st.xpSelect('l:warm');
  st.lxTab('opts');
  out.noOpts = st.xpTab;                                        // no options: 概况
  st.xpSelect('kit');
  st.xp.tab = 'logs';
  out.kitTab = [st.xpSel.kind, st.xpTab];

  // the options: each change posts that option alone, typed as its kind; a number that is none is not posted
  st.xpSelect('l:voice');
  calls.length = 0;
  const opt = (n) => st.lxCur.options.find((o) => o.name === n);
  await st.lxSet(st.lxCur, opt('volume'), '70');
  await st.lxSet(st.lxCur, opt('volume'), 'x');
  const badNumber = st.lx.note;
  await st.lxSet(st.lxCur, opt('quiet'), true);
  await st.lxSet(st.lxCur, opt('pace'), '2');
  await st.lxSet(st.lxCur, opt('name'), '老王');
  out.set = { posts: posts(), badNumber, value: st.lxValue(opt('volume')), def: st.lxValue(opt('name')) === '老王',
              said: st.said[st.said.length - 1] };
  const field = { value: '7' };                                 // a field of the form, and what is typed into it
  out.shown = [st.lxShown(opt('volume'), field)];
  document.activeElement = field;
  out.shown.push(st.lxShown(opt('volume'), field));
  document.activeElement = null;
  out.shown.push(st.lxShown({ name: 'none' }, field), st.lxShown(opt('volume')));
  failSet = true;
  calls.length = 0;
  await st.lxSet(st.lxCur, opt('volume'), '120');
  out.setFailed = { note: st.lx.note, reread: calls.map((c) => c[0]).includes('/api/local') };
  failSet = false;

  // 启用 off and on
  calls.length = 0;
  await st.lxEnable(st.lx.list.find((r) => r.id === 'idle'), false);
  out.enable = { posts: posts(), state: st.lxState(st.lx.list.find((r) => r.id === 'idle')).text, said: st.said[st.said.length - 1] };

  // the log: read in pages from the cursor, escapes out; every second while it runs and the tab shows
  calls.length = 0;
  st.lxTab('logs');
  await settle();
  out.logs = { reads: reads(), count: st.lout.count, last: st.lout.text.split('\n').pop(), line: st.lxOutLine, scrolled: box.scrollTop };
  calls.length = 0;
  logs.voice.push({ n: 451, text: 'new' });
  st.lxTick();
  await settle();
  out.tick = { reads: reads(), last: st.lout.text.split('\n').pop() };
  calls.length = 0;
  st.xp.tab = 'info';
  st.lxTick();
  await settle();
  out.tickOff = reads().length;                                 // another tab: no reads

  // 停止: the list read again (no row in the answer), the log read twice more
  st.xp.tab = 'logs';
  calls.length = 0;
  await st.lxAct(st.lxCur, 'stop');
  await settle();
  out.stop = { posts: posts(), reread: calls.map((c) => c[0]).includes('/api/local'), state: st.lxState(st.lxCur).text,
               said: st.said[st.said.length - 1] };
  calls.length = 0;
  for (let i = 0; i < 3; i++) { st.lxTick(); await settle(); }
  out.afterStop = reads().length;

  // 载入: a new run's log read afresh; its check shown, 概况 brought forward
  calls.length = 0;
  await st.lxAct(st.lxCur, 'load');
  await settle();
  const chk = st.lxCheck;
  out.load2 = { posts: posts(), tab: st.xpTab, summary: chk.summary, ok: chk.ok, problems: chk.problems.length,
                said: st.said[st.said.length - 1], state: st.lxState(st.lxCur).text, run: st.lout.run };
  st.lxTab('logs');
  await settle();
  out.load2.text = st.lout.text;

  // 检查
  calls.length = 0;
  await st.lxAct(st.lxCur, 'check');
  const chk2 = st.lxCheck;
  out.check = { posts: posts(), summary: chk2.summary, tests: chk2.tests, output: chk2.output, tab: st.xpTab };
  st.xpSelect('l:idle');
  out.check.other = st.lxCheck;                                 // another extension: not its check

  // never ran: its log answers 404
  st.lxTab('logs');
  await settle();
  out.never = st.lxOutEmpty;

  // the stream's `local`: the rows taken as they come; another revision reads the capabilities again
  calls.length = 0;
  revision = 2;
  st.onLocal(JSON.stringify({ revision: 2, extensions: [rows[0]] }));
  await settle();
  out.sse = { ids: st.lx.list.map((r) => r.id), calls: calls.map((c) => c[0]) };
  calls.length = 0;
  st.onLocal('');
  await settle();
  out.sse.bare = calls.map((c) => c[0]);

  // 补充: what it adds by app (the apps' titles once /api/kit/apps answered, else their ids): the capability that
  // answers a wake, the skill file by its path in the folder; selecting one that adds reads the apps when they are not
  // known yet
  const fx = { id: 'facts', title: '图鉴速查', path: 'C:\\ext\\facts', state: 'stopped', enabled: true, capabilities: [], options: [],
               adds: { raid: { wake: { call: 'facts.lookup', timeout: 2.5 }, skill: null },
                       guild: { wake: { call: 'facts.lookup', timeout: 3 }, skill: 'C:\\ext\\facts\\skills\\guild-chat.md' },
                       trade: { wake: null, skill: 'D:\\shared\\trade.md' }, none: { wake: null, skill: null } } };
  st.apps.data = null;
  out.adds = { bare: st.lxAdds(fx), none: st.lxAdds(st.lx.list[0]), empty: st.lxAdds(st.lxCur.id ? null : st.lxCur) };
  st.lx.list.push(fx);
  calls.length = 0;
  st.xpSelect('l:facts');
  await settle();
  out.adds.read = calls.map((c) => c[0]).filter((p) => p === '/api/kit/apps');
  out.adds.titled = st.lxAdds(st.lxCur);
  calls.length = 0;
  st.xpSelect('l:facts');
  await settle();
  out.adds.again = calls.map((c) => c[0]).filter((p) => p === '/api/kit/apps');

  // in English
  globalThis.Alpine = { store: () => ({ v: 'en' }) };
  st.lang = 'en';
  out.english = ['running', 'starting', 'off', 'stopped'].map((s) => st.lxState({ state: s, enabled: s !== 'off' }).text)
    .concat([st.lxState({ state: 'exited', exit_code: 2 }).text, st.lxStart({ start: 'workshop' }), st.lxKind('keep'),
             st.lxAdds(fx)]);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class LocalPart(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((STATIC_DIR / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_the_page_reads_the_rows(self):
        o = self.out["load"]
        self.assertEqual(o["calls"], ["/api/local", "/api/local/manifest"])
        self.assertEqual(o["ids"], ["voice", "warm", "broke", "crash", "done", "idle", "off"])
        self.assertEqual(o["summary"], "7 个本机扩展 · 1 个运行中")
        self.assertTrue(o["bar"].endswith(" · 7 个本机扩展 · 1 个运行中"))
        self.assertNotIn("programs", o["nav"])
        self.assertEqual(self.out["problem"], ["extensions.json cannot be read", ""])

    def test_states(self):
        self.assertEqual(self.out["states"], [
            ["voice", "运行中", "ok"], ["warm", "启动中", "warn"], ["broke", "清单有误", "bad"], ["crash", "异常退出", "bad"],
            ["done", "已退出", "off"], ["idle", "未运行", "off"], ["off", "已停用", "off"]])

    def test_the_one_selected(self):
        s = self.out["sel"]
        self.assertEqual((s["kind"], s["title"], s["tag"], s["line"], s["tab"], s["cur"]),
                         ("local", "声音", "本机", "voice · 0.1.0", "info", "voice"))
        self.assertRegex(s["started"], r" · 已运行 2 分钟$")
        self.assertEqual(s["start"], "首次调用时启动")
        self.assertEqual(s["caps"], [{"id": "voice.say", "name": "wk_voice_say", "title": "说一句", "kind": "point"},
                                     {"id": "voice.status", "name": "wk_voice_status", "title": "状态", "kind": "see"}])
        self.assertEqual(s["kinds"], ["指引", "读取"])
        self.assertEqual((s["untitled"], s["code"]), ("broke", "3221225477 (0xC0000005)"))
        self.assertEqual((self.out["opts"], self.out["noOpts"], self.out["kitTab"]), ("opts", "info", ["kit", "info"]))

    def test_options(self):
        s = self.out["set"]
        self.assertEqual(s["posts"], [["/api/local/set", {"id": "voice", "options": {"volume": 70}}],
                                      ["/api/local/set", {"id": "voice", "options": {"quiet": True}}],
                                      ["/api/local/set", {"id": "voice", "options": {"pace": 2}}],
                                      ["/api/local/set", {"id": "voice", "options": {"name": "老王"}}]])
        self.assertEqual(s["badNumber"], {"bad": True, "text": "「音量」需要数字"})
        self.assertEqual((s["value"], s["def"], s["said"]), (70, True, "已保存 称呼"))
        self.assertEqual(self.out["shown"], [70, "7", "", 70])        # the value in force; with the focus, what is typed
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r':value="lxShown\(o, \$el\)"', html)), 2)   # the number field and the text
        f = self.out["setFailed"]
        self.assertEqual(f["note"], {"bad": True, "text": "保存设置失败：volume: at most 100"})
        self.assertTrue(f["reread"])                                  # the form shows the values in force again

    def test_enable(self):
        e = self.out["enable"]
        self.assertEqual(e["posts"], [["/api/local/set", {"id": "idle", "enabled": False}]])
        self.assertEqual((e["state"], e["said"]), ("已停用", "已停用 没跑"))

    def test_log(self):
        o = self.out["logs"]
        self.assertEqual(o["reads"], ["/api/local/logs?id=voice&since=0&limit=200", "/api/local/logs?id=voice&since=200&limit=200",
                                      "/api/local/logs?id=voice&since=400&limit=200"])
        self.assertEqual((o["count"], o["last"], o["line"], o["scrolled"]), (450, "green", "450 行", 9999))
        self.assertEqual(self.out["tick"], {"reads": ["/api/local/logs?id=voice&since=450&limit=200"], "last": "new"})
        self.assertEqual(self.out["tickOff"], 0)

    def test_stop_and_load(self):
        s = self.out["stop"]
        self.assertEqual(s["posts"], [["/api/local/stop", {"id": "voice"}]])
        self.assertEqual((s["reread"], s["state"], s["said"]), (True, "已退出", "已停止 声音"))
        self.assertEqual(self.out["afterStop"], 2)
        o = self.out["load2"]
        self.assertEqual(o["posts"], [["/api/local/load", {"id": "voice"}]])
        self.assertEqual((o["tab"], o["summary"], o["ok"], o["problems"]), ("info", "1 个错误 · 1 个警告 · 1 个能力", False, 2))
        self.assertEqual((o["said"], o["state"], o["run"], o["text"]), ("声音 检查未通过", "运行中", 1762750100, "loaded"))

    def test_check(self):
        c = self.out["check"]
        self.assertEqual(c["posts"], [["/api/local/check", {"id": "voice"}]])
        self.assertEqual((c["summary"], c["tests"], c["output"], c["tab"]),
                         ("检查通过 · 2 个能力", "测试 3 个，1 个失败", "FAIL: test_say", "info"))
        self.assertIsNone(c["other"])
        self.assertEqual(self.out["never"], "还没有运行过")

    def test_supplements(self):
        a = self.out["adds"]
        guild = {"app": "guild", "title": "guild", "wake": "facts.lookup · 3 秒", "skill": "skills\\guild-chat.md"}
        raid = {"app": "raid", "title": "raid", "wake": "facts.lookup · 2.5 秒", "skill": ""}
        trade = {"app": "trade", "title": "trade", "wake": "", "skill": "D:\\shared\\trade.md"}   # not in its folder: in full
        self.assertEqual((a["bare"], a["none"], a["empty"]), ([guild, raid, trade], [], []))    # by app; one that adds nothing: left out
        self.assertEqual(a["read"], ["/api/kit/apps"])                   # the apps' titles: read once
        self.assertEqual(a["titled"], [dict(guild, title="公会聊天"), raid, trade])
        self.assertEqual(a["again"], [])

    def test_stream_event(self):
        s = self.out["sse"]
        self.assertEqual(s["ids"], ["voice"])
        self.assertEqual(s["calls"], ["/api/local/manifest"])            # the rows came with it; another revision
        self.assertEqual(s["bare"], ["/api/local"])                      # no rows: read again (the same revision)

    def test_english(self):
        self.assertEqual(self.out["english"][:7], ["Running", "Starting", "Off", "Not running", "Crashed",
                                                   "With Wuxian Workshop", "Store"])
        self.assertEqual([a["wake"] for a in self.out["english"][7]], ["facts.lookup · 3 s", "facts.lookup · 2.5 s", ""])


class Template(unittest.TestCase):
    """the 本机 part of index.html calls what app.js has, the stream's event is listened to, its loaders say they are a
    status; nothing of the 程序 page is left in the page's files"""

    def setUp(self):
        self.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def part(self):
        start = self.html.index("<!-- 本机扩展：")
        return self.html[start:self.html.index("<!-- Agent 会话 -->", start)]

    def test_calls_exist(self):
        start = self.html.index('<span>已添加</span>')
        part = self.part() + self.html[start:self.html.index('<div class="master-foot">', start)]
        exprs = " ; ".join(v for _, v in re.findall(r'\s(x-[\w:.-]+|:[\w-]+|@[\w.:-]+)="([^"]*)"', part))   # one's end, the next's start
        names = set(re.findall(r"(?<![\w.$'])([A-Za-z_]\w*)\s*\(", exprs)) - {"t", "in", "String"}
        self.assertTrue({"lxAct", "lxSet", "lxEnable", "lxTab", "lxState", "lxCaps", "xpSelect", "onLxOutScroll"} <= names)
        for name in names:
            self.assertTrue(re.search(r"(?m)^\s*(?:async\s+)?%s\s*\(" % name, self.js), name)
        for getter in ("lxCur", "lxCheck", "lxOutLine", "lxOutEmpty", "xpTab", "lxSumCur"):
            self.assertIn(getter, exprs)
            self.assertTrue(re.search(r"(?m)^\s*get\s+%s\s*\(" % getter, self.js), getter)
        self.assertIn('x-ref="lxout"', self.part())
        self.assertIn("es.addEventListener('local'", self.js)

    def test_loaders_say_status(self):
        loaders = re.findall(r'<div class="loader"[^>]*>', self.part())
        self.assertEqual(len(loaders), 1)
        for tag in loaders:
            self.assertIn('role="status"', tag)

    def test_no_programs_left(self):
        for name in ("index.html", "app.js", "app.css", "i18n.js"):
            text = (STATIC_DIR / name).read_text(encoding="utf-8")
            for word in ("/api/programs", "'programs'", "\"programs\"", "i-program", "goProgram", "loadPrograms", "appDriver",
                         "appProgram", "progs.", "pout.", "prog-", "new_program", "wuxian-program.json", "程序」驱动", "登记程序"):
                self.assertNotIn(word, text, f"{name}: {word}")


if __name__ == "__main__":
    unittest.main()
