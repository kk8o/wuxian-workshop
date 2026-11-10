"""批准 and the eight states of a local extension on the 扩展 page: app.js run under Node (skipped without it) against a
stand-in for /api/local* in the shapes of contract5 (sections 7, 8, 11). A row's state reads 已停用 / 未运行 / 启动中 /
运行中 / 无响应 / 停止中 / 已退出 or 异常退出 / 等待重试 (清单有误 with its error, 待批准 while it waits for the player), with
since when and why; a row whose approval is needed or changed shows its command and folder with 允许运行 (POST
/api/local/approve, the row it answers taken in); a start or a check the daemon refuses with 403 needs_approval shows
the same, not an error; with approval ok (always, in developer mode) nothing of it shows."""
import json
import subprocess
import unittest

from wuxianworkshop.ui import STATIC_DIR

from tests.ui.test_local import NODE, STUBS

HARNESS = r"""
const out = {};
const T0 = 1762750000;
const calls = [];
const refuse = {};
let rows;
function apiErr(status, code, message) {
  const e = new Error(code + ': ' + message);
  e.status = status;
  e.data = { error: { code, message } };
  return e;
}
const copyOf = (r) => JSON.parse(JSON.stringify(r));
const base = { version: '0.1.0', enabled: true, start: 'use', pid: null, started: null, exit_code: null, error: null,
               capabilities: [], sources: [], options: [], apps: [], skills: [], adds: {}, approval: 'ok', since: T0 - 30,
               why: null };
const mk = (id, title, more) => Object.assign({}, base, { id, title, path: 'C:\\ext\\' + id, command: ['python', id + '.py'] }, more);
function reset() {
  rows = [
    mk('voice', '声音', { state: 'running', pid: 4242, started: T0, since: T0 }),
    mk('plain', '普通', { state: 'stopped', since: null }),
    mk('facts', '图鉴速查', { state: 'stopped', start: 'workshop', approval: 'needed',
                              command: ['C:\\Program Files\\Python\\python.exe', '-u', 'main.py', '--name', 'a "b"', ''] }),
    mk('stats', '统计', { state: 'stopped', approval: 'changed' }),
    mk('ocr', '识图', { state: 'hung', pid: 77, started: T0 - 600, since: T0 - 20, why: 'no answer to ping in 20 s' }),
    mk('halt', '收尾', { state: 'stopping', pid: 78, started: T0 - 600 }),
    mk('sync', '同步', { state: 'backoff', start: 'workshop', exit_code: 1, why: 'exit code 1; next start in 30 s' }),
    mk('crash', '崩溃', { state: 'exited', exit_code: 1, why: 'exit code 1\nTraceback (most recent call last):\nKeyError: 1' }),
    mk('done', '退出', { state: 'exited', exit_code: 0, approval: 'changed' }),
    mk('warm', '预热', { state: 'starting', pid: 79, started: T0 }),
    mk('draft', '', { state: 'stopped', approval: 'needed', command: null, error: 'run: a list of strings' }),
    mk('gone', '停用的', { state: 'off', enabled: false, approval: 'needed' }),
    mk('late', '后变', { state: 'stopped' }),
    mk('odd', '只答', { state: 'stopped' }),
    mk('deny', '拒绝', { state: 'stopped' }),
  ];
  Object.assign(refuse, { late: 'changed', odd: 'keep', deny: 'forbidden' });
}
async function api(path, opts) {
  const body = opts && opts.body;
  calls.push([path, body || null]);
  const row = (id) => rows.find((r) => r.id === id);
  if (path === '/api/extensions') return { addons: [], catalog: null, addons_dir: null };
  if (path.startsWith('/api/agent_access')) return { kit: null, kit_state: 'absent', kit_addon: null, servers: [] };
  if (path === '/api/local') return { revision: 1, extensions: rows.map(copyOf) };
  if (path === '/api/local/manifest') return { revision: 1, extensions: [] };
  if (path === '/api/kit/apps') return { online: true, apps: [] };
  if (path.startsWith('/api/local/logs?')) throw apiErr(404, 'not_found', 'no log yet');
  if (path === '/api/local/approve') {
    const r = row(body.id);
    if (!Array.isArray(r.command)) throw apiErr(400, 'bad_manifest', r.error);
    r.approval = 'ok';
    delete refuse[r.id];
    return copyOf(r);
  }
  if (path === '/api/local/load' || path === '/api/local/check') {
    const r = row(body.id);
    if (refuse[r.id] === 'forbidden') throw apiErr(403, 'forbidden', 'not from here');
    if (refuse[r.id] === 'changed') r.approval = 'changed';      // its command changed after the page read the rows
    if (r.approval !== 'ok' || refuse[r.id]) {
      throw apiErr(403, 'needs_approval', r.id + ': the player has not allowed its command yet');
    }
    if (path === '/api/local/check') return { ok: true, problems: [], capabilities: [] };
    Object.assign(r, { state: 'running', pid: 6001, started: T0 + 100, since: T0 + 100, why: null, exit_code: null });
    return { row: copyOf(r), check: { ok: true, problems: [], capabilities: [] } };
  }
  throw new Error('unexpected ' + path);
}
function make() {
  const st = appState();
  st.token = 't';
  st.$nextTick = (f) => f();
  st.$refs = {};
  st.said = [];
  st.say = (x) => st.said.push(x);
  st.api = api;
  return st;
}
const settle = async () => { for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r)); };
const posts = () => calls.filter((c) => c[1]).map((c) => [c[0], c[1]]);
const paths = () => calls.map((c) => c[0]);

(async () => {
  reset();
  const st = make();
  st.now = (T0 + 125) * 1000;
  st.go('extensions');
  await settle();
  const of = (id) => st.lx.list.find((r) => r.id === id);
  const last = () => st.said[st.said.length - 1];

  // the states: the word, its colour, what it is to the page
  out.states = st.lx.list.map((r) => [r.id, st.lxState(r).text, st.lxPill(r), st.lxState(r).key]);
  out.live = { running: st.lx.list.filter((r) => st.lxRunning(r)).map((r) => r.id),
               stoppable: st.lx.list.filter((r) => st.lxStoppable(r)).map((r) => r.id), summary: st.lxSummary };
  out.since = { hung: st.lxSince(of('ocr')), running: st.lxSince(of('voice')), none: st.lxSince(of('plain')),
                why: [of('ocr').why, of('sync').why, of('crash').why, of('voice').why] };

  // 批准: which rows ask, under which title; the command as one line
  out.asks = st.lx.list.filter((r) => st.lxNeeds(r)).map((r) => [r.id, st.lxApprovalTitle(r), st.lxCanApprove(r)]);
  out.command = { quoted: st.lxCommand(of('facts')), plain: st.lxCommand(of('plain')), none: st.lxCommand(of('draft')),
                  empty: st.lxCommand({ command: [] }) };

  // 载入 of one that waits: 403 needs_approval is the same card and one line, not an error; the rows are read again
  st.xpSelect('l:facts');
  calls.length = 0;
  await st.lxAct(st.lxCur, 'load');
  await settle();
  out.refused = { posts: posts(), reread: paths().includes('/api/local'), note: st.lx.note, ask: st.lx.ask,
                  needs: st.lxNeeds(st.lxCur), said: last(), busy: st.lx.busy, state: st.lxState(st.lxCur).text };

  // 允许运行: the row it answers taken in; then 载入 runs it
  calls.length = 0;
  await st.lxApprove(st.lxCur);
  out.approved = { posts: posts(), reread: paths().includes('/api/local'), approval: st.lxCur.approval, ask: st.lx.ask,
                   needs: st.lxNeeds(st.lxCur), said: last(), state: st.lxState(st.lxCur).text };
  await st.lxAct(st.lxCur, 'load');
  out.loaded = { state: st.lxState(st.lxCur).text, said: last(), note: st.lx.note };

  // 检查 of one whose command changed: refused the same way; once allowed it checks
  st.xpSelect('l:stats');
  calls.length = 0;
  await st.lxAct(st.lxCur, 'check');
  out.changed = { posts: posts(), note: st.lx.note, check: st.lxCheck, title: st.lxApprovalTitle(st.lxCur), said: last() };
  await st.lxApprove(st.lxCur);
  await st.lxAct(st.lxCur, 'check');
  out.changed.after = { needs: st.lxNeeds(st.lxCur), check: st.lxCheck && st.lxCheck.ok, state: st.lxState(st.lxCur).text };

  // the row said ok, the daemon refuses (the command changed since): the rows read again say changed
  st.xpSelect('l:late');
  out.late = { before: st.lxNeeds(st.lxCur) };
  await st.lxAct(st.lxCur, 'load');
  Object.assign(out.late, { needs: st.lxNeeds(st.lxCur), title: st.lxApprovalTitle(st.lxCur), approval: st.lxCur.approval,
                            note: st.lx.note });
  await st.lxApprove(st.lxCur);
  Object.assign(out.late, { ask: st.lx.ask, after: st.lxNeeds(st.lxCur) });

  // … and one whose row still says ok: the refusal alone shows the card, until another is selected
  st.xpSelect('l:odd');
  await st.lxAct(st.lxCur, 'load');
  out.odd = { approval: st.lxCur.approval, needs: st.lxNeeds(st.lxCur), title: st.lxApprovalTitle(st.lxCur), state: st.lxState(st.lxCur).text };
  st.xpSelect('l:voice');
  out.odd.left = st.lxNeeds(of('odd'));
  st.xpSelect('l:odd');
  await st.lxAct(st.lxCur, 'load');
  calls.length = 0;
  await st.lxApprove(st.lxCur);
  Object.assign(out.odd, { posts: posts(), ask: st.lx.ask, after: st.lxNeeds(st.lxCur) });

  // any other refusal stays an error; a manifest without a command cannot be allowed
  st.xpSelect('l:deny');
  await st.lxAct(st.lxCur, 'load');
  out.deny = { note: st.lx.note, needs: st.lxNeeds(st.lxCur), ask: st.lx.ask };
  st.xpSelect('l:draft');
  out.draft = { can: st.lxCanApprove(st.lxCur), title: st.lxTitle(st.lxCur) };
  await st.lxApprove(st.lxCur);
  Object.assign(out.draft, { note: st.lx.note, needs: st.lxNeeds(st.lxCur) });

  // 等待重试: its log is read every second like a running one's (the next start writes to it)
  st.xpSelect('l:sync');
  st.lxTab('logs');
  await settle();
  calls.length = 0;
  st.lxTick();
  await settle();
  out.backoffTick = paths().filter((p) => p.startsWith('/api/local/logs')).length;

  // in English
  globalThis.Alpine = { store: () => ({ v: 'en' }) };
  st.lang = 'en';
  out.english = { states: ['ocr', 'halt', 'sync', 'draft', 'gone', 'crash'].map((id) => st.lxState(of(id)).text),
                  waits: st.lxState({ state: 'stopped', approval: 'needed' }).text,
                  titles: [st.lxApprovalTitle({ approval: 'needed' }), st.lxApprovalTitle({ approval: 'changed' })],
                  since: st.lxSince(of('ocr')) };
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class Approval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((STATIC_DIR / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_the_eight_states(self):
        self.assertEqual(self.out["states"], [
            ["voice", "运行中", "ok", "running"], ["plain", "未运行", "off", "stopped"], ["facts", "待批准", "warn", "approval"],
            ["stats", "待批准", "warn", "approval"], ["ocr", "无响应", "bad", "hung"], ["halt", "停止中", "warn", "stopping"],
            ["sync", "等待重试", "warn", "backoff"], ["crash", "异常退出", "bad", "crashed"], ["done", "已退出", "off", "exited"],
            ["warm", "启动中", "warn", "starting"], ["draft", "清单有误", "bad", "invalid"], ["gone", "已停用", "off", "off"],
            ["late", "未运行", "off", "stopped"], ["odd", "未运行", "off", "stopped"], ["deny", "未运行", "off", "stopped"]])

    def test_which_have_a_process(self):
        o = self.out["live"]
        self.assertEqual(o["running"], ["voice", "ocr", "halt", "warm"])
        self.assertEqual(o["stoppable"], ["voice", "ocr", "halt", "sync", "warm"])       # 等待重试: 停止 calls the retry off
        self.assertEqual(o["summary"], "15 个本机扩展 · 1 个运行中")
        self.assertEqual(self.out["backoffTick"], 1)

    def test_since_and_why(self):
        s = self.out["since"]
        self.assertRegex(s["hung"], r"^\S.* 起$")
        self.assertEqual((s["running"], s["none"]), ("", ""))                            # 开始于 says it; no since: nothing
        self.assertEqual(s["why"], ["no answer to ping in 20 s", "exit code 1; next start in 30 s",
                                    "exit code 1\nTraceback (most recent call last):\nKeyError: 1", None])

    def test_which_rows_ask(self):
        self.assertEqual(self.out["asks"], [["facts", "需要批准", True], ["stats", "运行命令已变更", True],
                                            ["done", "运行命令已变更", True], ["draft", "需要批准", False],
                                            ["gone", "需要批准", True]])              # approval ok: nothing of it

    def test_the_command_in_one_line(self):
        c = self.out["command"]
        self.assertEqual(c["quoted"], '"C:\\Program Files\\Python\\python.exe" -u main.py --name "a \\"b\\"" ""')
        self.assertEqual((c["plain"], c["none"], c["empty"]), ("python plain.py", "—", "—"))

    def test_a_refused_start_asks(self):
        r = self.out["refused"]
        self.assertEqual(r["posts"], [["/api/local/load", {"id": "facts"}]])
        self.assertTrue(r["reread"])
        self.assertIsNone(r["note"])                                                     # not an error
        self.assertEqual((r["ask"], r["needs"], r["said"], r["busy"], r["state"]), ("facts", True, "图鉴速查 需要批准", "", "待批准"))

    def test_allow(self):
        a = self.out["approved"]
        self.assertEqual(a["posts"], [["/api/local/approve", {"id": "facts"}]])
        self.assertFalse(a["reread"])                                                    # the row came with the answer
        self.assertEqual((a["approval"], a["ask"], a["needs"], a["said"], a["state"]), ("ok", "", False, "已允许 图鉴速查 运行", "未运行"))
        self.assertEqual(self.out["loaded"], {"state": "运行中", "said": "已载入 图鉴速查", "note": None})

    def test_a_changed_command(self):
        c = self.out["changed"]
        self.assertEqual(c["posts"], [["/api/local/check", {"id": "stats"}]])
        self.assertEqual((c["note"], c["check"], c["title"], c["said"]), (None, None, "运行命令已变更", "统计 需要批准"))
        self.assertEqual(c["after"], {"needs": False, "check": True, "state": "未运行"})

    def test_a_refusal_the_row_did_not_say(self):
        o = self.out["late"]
        self.assertEqual((o["before"], o["needs"], o["title"], o["approval"], o["note"]), (False, True, "运行命令已变更", "changed", None))
        self.assertEqual((o["ask"], o["after"]), ("", False))
        o = self.out["odd"]
        self.assertEqual((o["approval"], o["needs"], o["title"], o["state"]), ("ok", True, "需要批准", "未运行"))
        self.assertFalse(o["left"])                                                      # another selected: asked afresh
        self.assertEqual(o["posts"], [["/api/local/approve", {"id": "odd"}]])
        self.assertEqual((o["ask"], o["after"]), ("", False))

    def test_other_failures_stay_errors(self):
        self.assertEqual(self.out["deny"], {"note": {"bad": True, "text": "载入失败：not from here"}, "needs": False, "ask": ""})
        d = self.out["draft"]
        self.assertEqual((d["can"], d["title"], d["needs"]), (False, "draft", True))
        self.assertEqual(d["note"], {"bad": True, "text": "批准失败：run: a list of strings"})

    def test_english(self):
        e = self.out["english"]
        self.assertEqual(e["states"], ["Not responding", "Stopping", "Waiting to retry", "Manifest error", "Off", "Crashed"])
        self.assertEqual((e["waits"], e["titles"]), ("Awaiting approval", ["Approval needed", "Run command changed"]))
        self.assertRegex(e["since"], r"^since \S")


class Template(unittest.TestCase):
    """the card in index.html: in the 本机 detail, the command in full in a monospace block that wraps, every text of a
    row through x-text"""

    def setUp(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = html.index("<!-- 本机扩展：")
        self.part = html[start:html.index("<!-- Agent 会话 -->", start)]
        self.css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def test_the_card(self):
        start = self.part.index('<div class="lx-approve" x-show="lxNeeds(lxCur)">')
        card = self.part[start:self.part.index('<div class="xp-note', start)]
        self.assertIn('x-text="lxApprovalTitle(lxCur)"', card)
        self.assertIn('<code class="lx-cmd selectable" x-text="lxCommand(lxCur)"></code>', card)
        self.assertIn('x-text="lxCur.path || \'—\'"', card)
        self.assertIn('@click="lxApprove(lxCur)"', card)
        self.assertIn("!lxCanApprove(lxCur)", card)
        self.assertIn("t('允许运行')", card)

    def test_the_command_wraps(self):
        rule = self.css[self.css.index(".lx-cmd"):]
        rule = rule[:rule.index("}")]
        self.assertIn("var(--mono)", rule)
        self.assertIn("pre-wrap", rule)
        self.assertIn("overflow-wrap: anywhere", rule)

    def test_state_and_supplements(self):
        self.assertIn('x-text="lxSince(lxCur)"', self.part)
        self.assertIn('<dd x-show="lxCur.why" class="lx-why selectable" x-text="lxCur.why"></dd>', self.part)
        self.assertIn('x-for="a in lxAdds(lxCur)"', self.part)
        for word in ("唤醒附注", "技能补充"):
            self.assertIn(word, self.part)

    def test_no_markup_from_rows(self):
        self.assertNotIn("x-html", self.part)
        self.assertNotIn("innerHTML", self.js)

    def test_old_fields_are_gone(self):
        for name in ("index.html", "app.js", "i18n.js"):
            text = (STATIC_DIR / name).read_text(encoding="utf-8")
            for word in ("annotate", "extends", "'failed'", "启动失败", "（附注）", "（技能）"):
                self.assertNotIn(word, text, f"{name}: {word}")


if __name__ == "__main__":
    unittest.main()
