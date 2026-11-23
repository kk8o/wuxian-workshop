"""A button of the 扩展 page that is off says why beside it, the link's two modes, and the framework that is too old for
this workshop. app.js run under Node (skipped without it) against a stand-in for the daemon in the shapes of contract3
(sections 4 to 6).

- one action of the 本机 part runs at a time (lx.busy): while it does, the buttons of the extension it belongs to read
  正在检查 …, those of another read whose it is (语音搭子 正在检查); 载入 that is off says 启用后可载入 or the workshop
  it needs; 批准 says the same, or that its summary is being read, failed, or is of before;
- an extension selected before its row came (a link into the page) gets its summary once the row is there: 批准 was
  off for good;
- a package of the catalog says who is being installed or removed, or the addons it needs first;
- /api/status: link.held_by is [{id, title}] (常开 · 由 语音搭子 保持), the setting link_mode has 自动 and 常开 only,
  kit.outdated {needs, has} shows 扩展框架需要更新到 <needs> at the page's top with the framework's own 更新;
- an answer's restart is what says 重启游戏后生效, until the game is another process."""
import json
import re
import subprocess
import unittest

from wuxianworkshop.ui import STATIC_DIR

from tests.ui.test_local import NODE, STUBS

HARNESS = r"""
const out = {};
const calls = [];
const held = {};                               // path -> a promise the answer waits for
const sums = {};
function apiErr(status, code, message) {
  const e = new Error(code + ': ' + message);
  e.status = status;
  e.data = { error: { code, message } };
  return e;
}
const copyOf = (r) => JSON.parse(JSON.stringify(r));
const base = { version: '1.0.0', enabled: true, start: 'use', state: 'stopped', pid: null, started: null, exit_code: null,
               error: null, capabilities: [], sources: [], options: [], apps: [], skills: [], adds: {}, approval: 'ok',
               since: null, why: null, origin: 'home', author: null, homepage: null, requires: {}, unmet: [],
               command: ['python', 'main.py'], where: 'local', player: null, addons: [], game: null };
const mk = (id, title, more) => Object.assign({}, base, { id, title, path: 'D:\\ext\\' + id, fingerprint: 'f:' + id }, more);
const rows = [
  mk('voice', '语音搭子', { state: 'running', pid: 7 }),
  mk('idle', '停用的', { enabled: false, state: 'off' }),
  mk('old', '要新版的', { unmet: [{ what: 'workshop', needs: '0.11.0', has: '0.10.0' }] }),
  mk('ask', '待批准的', { approval: 'needed', player: { action: 'approve', text: 'x' } }),
  mk('game', '带插件的', { where: 'both', addons: [{ folder: 'WxGame', version: '1.0.0', state: 'missing' }] }),
];
const row = (id) => rows.find((r) => r.id === id);
const pkgs = [
  { id: 'wuxiankit', folder: 'WuxianKit', title: { zh: '扩展框架', en: 'WuxianKit' }, version: '0.7.0', installed: '0.6.0',
    there: true, state: 'update', extensions: [], skills: [] },
  { id: 'atlas', folder: 'WuxianKit_Atlas', title: { zh: '图集', en: 'Atlas' }, version: '1.1.0', there: false, state: 'available',
    requires: ['WuxianKit', 'AtlasData'], missing: ['AtlasData'], extensions: [] },
];
async function api(path, opts) {
  const body = opts && opts.body;
  calls.push([path, body ? copyOf(body) : null]);
  if (held[path]) await held[path];
  if (path === '/api/local') return { revision: 1, extensions: copyOf(rows) };
  if (path === '/api/local/manifest') return { revision: 1, extensions: [] };
  if (path.startsWith('/api/local/summary')) {
    const id = decodeURIComponent(path.split('=')[1]);
    if (sums[id]) await sums[id];
    return { id, risk: 'local', fingerprint: 'f:' + id, replaces: [] };
  }
  if (path === '/api/local/check') return { ok: true, problems: [], capabilities: [] };
  if (path === '/api/local/set') return copyOf(row(body.id));
  if (path === '/api/local/approve') return Object.assign(copyOf(row(body.id)), { approval: 'ok', player: null, installed: null });
  if (path === '/api/local/install') return { id: body.id, restart: true, hint: 'h',
    addons: [{ folder: 'WxGame', state: 'installed', was: 'missing', version: '1.0.0', kept: null }] };
  if (path.startsWith('/api/extensions')) {
    return body ? { action: body.action, result: { version: '0.7.0', was: '0.6.0', restart: false }, addon: null }
      : { addons: copyOf(pkgs), catalog: null, addons_dir: null };
  }
  if (path.startsWith('/api/agent_access')) return { kit: null, kit_state: 'unknown', kit_addon: null, servers: [] };
  return {};
}
const st = appState();
st.token = 't';
st.$nextTick = (f) => f();
st.$refs = { lxout: { scrollTop: 0, scrollHeight: 0, clientHeight: 0 } };
st.said = [];
st.say = (x) => st.said.push(x);
st.copy = () => {};
st.api = api;
const settle = async () => { for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r)); };
const hold = (path) => { let go; held[path] = new Promise((r) => { go = r; }); return () => { delete held[path]; go(); }; };
const posts = () => calls.filter((c) => c[1]);
const cur = (id) => st.lx.list.find((r) => r.id === id);
const status = (link, kit, pid) => ({ daemon: { started: 1 }, game: { found: true, pid: pid || 100 }, kit,
  link: Object.assign({ state: 'online', in_session: true }, link), watch: [] });

(async () => {
  // selected before its row came (a link into the page): the summary is read once the row is there
  st.xpSelect('l:ask');
  await settle();
  out.early = { sum: st.lx.sum, cur: st.lxCur.id || null, read: calls.length };
  calls.length = 0;
  await st.loadLocal();
  await settle();
  out.late = { read: calls.map((c) => c[0]).filter((p) => p.includes('summary')), state: st.lxSumState(st.lxCur),
               ready: st.lxApproveReady(st.lxCur), why: st.lxApproveWhy(st.lxCur) };
  calls.length = 0;
  await st.loadLocal();                        // … and not again with every list
  await settle();
  out.late.again = calls.map((c) => c[0]).filter((p) => p.includes('summary')).length;

  // 载入 that is off: why
  out.head = ['voice', 'idle', 'old', 'ask'].map((id) => st.lxHeadWhy(cur(id)));
  out.none = [st.lxWait(cur('voice')), st.lxWait(null), st.lxHeadWhy(null), st.lxBusyOf()];

  // one action at a time: who is busy, said at every button it turns off
  let go = hold('/api/local/check');
  const checking = st.lxAct(cur('voice'), 'check');
  await settle();
  calls.length = 0;
  await st.lxApprove(cur('ask'));              // off meanwhile: nothing is posted, and the card says why
  await st.lxInstall(cur('game'));
  out.busy = { slot: st.lx.busy, of: st.lxBusyOf(), own: st.lxWait(cur('voice')), head: st.lxHeadWhy(cur('voice')),
               other: st.lxWait(cur('ask')), idle: st.lxHeadWhy(cur('idle')), approve: st.lxApproveWhy(cur('ask')),
               retry: st.lxSumRetry(cur('ask')), posted: posts() };
  go();
  await checking;
  out.free = [st.lx.busy, st.lxWait(cur('ask')), st.lxApproveWhy(cur('ask'))];

  // a setting is saved at once: nothing to read
  go = hold('/api/local/set');
  const saving = st.lxEnable(cur('voice'), true);
  await settle();
  out.set = [st.lx.busy, st.lxWait(cur('voice')), st.lxWait(cur('ask'))];
  go();
  await saving;

  // 批准: its own run says 进行中… on the button; the summary's states are the line
  go = hold('/api/local/approve');
  const yes = st.lxApprove(cur('ask'));
  await settle();
  out.approving = [st.lx.busy, st.lxApproveWhy(cur('ask')), st.lxWait(cur('ask')), st.lxWait(cur('voice'))];
  go();
  await yes;
  let read;
  sums.voice = new Promise((r) => { read = r; });
  st.xpSelect('l:voice');
  await settle();
  out.reading = [st.lxSumState(st.lxCur), st.lxApproveWhy(st.lxCur), st.lxSumRetry(st.lxCur)];
  read();
  delete sums.voice;
  await settle();
  st.lx.sum = { id: 'voice', n: st.lx.sumN, state: 'error', data: null };
  out.failed = [st.lxApproveWhy(st.lxCur), st.lxSumRetry(st.lxCur)];
  st.lx.sum = { id: 'voice', n: st.lx.sumN, state: 'ok', data: { fingerprint: 'of before' } };
  out.stale = [st.lxApproveWhy(st.lxCur), st.lxSumRetry(st.lxCur)];

  // an answer's restart: 重启游戏后生效, until the game is another process
  st.applyStatus(status({ mode: 'auto', held_by: [] }, null, 100));
  await st.lxInstall(cur('game'));
  out.restart = { said: st.said[st.said.length - 1], mark: st.lxRestart(cur('game')) };
  st.applyStatus(status({ mode: 'auto', held_by: [] }, null, 100));
  out.restart.same = st.lxRestart(cur('game'));
  st.applyStatus(status({ mode: 'auto', held_by: [] }, null, 222));
  out.restart.again = st.lxRestart(cur('game'));
  out.done = [st.lxDone('x', { restart: true }), st.lxDone('x', { restart: false }), st.lxDone('x', null), st.lxDone('x', { restart: 'yes' })];

  // the catalog's packages
  await st.loadExtensions();
  const kit = st.xpPkg, atlas = st.ext.list.find((a) => a.id === 'atlas');
  out.ext = { idle: [st.extWait(kit), st.extWait(atlas), st.extWait(null), st.extRefreshText] };
  st.ext.busy = 'list';
  out.ext.list = [st.extWait(kit), st.extWait(null), st.extRefreshText];
  st.ext.busy = '';
  go = hold('/api/extensions');
  calls.length = 0;
  const installing = st.extAct(kit, 'install');
  await settle();
  await st.extAct(atlas, 'install');           // off meanwhile
  out.ext.install = { slot: [st.ext.busy, st.ext.doing], own: st.extWait(kit), other: st.extWait(atlas), bar: st.extWait(null),
                      refresh: st.extRefreshText, posted: posts() };
  go();
  await installing;
  await settle();
  st.ext.busy = 'atlas';
  st.ext.doing = 'remove';
  out.ext.remove = [st.extWait(atlas), st.extWait(kit)];
  st.ext.busy = '';
  st.ext.doing = '';

  // the framework too old for this workshop: /api/status kit.outdated
  st.access.data = { kit: null, kit_state: 'unknown', servers: [] };
  const kitOf = () => ({ o: st.kitOutdated, line: st.kitOutdatedLine, pkg: st.kitUpdatePkg && st.kitUpdatePkg.id,
                         badge: st.xpKitBadge, state: st.xpKitState });
  st.applyStatus(status({ mode: 'auto', held_by: [] }, { outdated: { needs: '0.7.0', has: '0.6.0' } }));
  out.kit = { old: kitOf() };
  calls.length = 0;
  await st.extAct(st.kitUpdatePkg, 'install');
  await settle();
  out.kit.update = posts();
  st.applyStatus(status({ mode: 'auto', held_by: [] }, { outdated: { needs: '0.7.0', has: null } }));
  out.kit.unread = st.kitOutdated;
  st.applyStatus(status({ mode: 'auto', held_by: [] }, { outdated: null }));
  out.kit.fine = kitOf();
  st.applyStatus(status({ mode: 'auto', held_by: [] }, null));
  out.kit.none = [st.kitOutdated, st.kitOutdatedLine];
  st.applyStatus(status({ mode: 'auto', held_by: [] }, { outdated: { needs: '0.7.0', has: '0.6.0' } }));
  st.ext.list = st.ext.list.map((a) => (a.id === 'wuxiankit' ? Object.assign({}, a, { state: 'needs_app' }) : a));
  out.kit.noEntry = st.kitUpdatePkg;

  // the link: who keeps it, the two modes
  const link = (l) => { st.applyStatus(status(l, null)); return [st.linkKept, st.linkHolders, st.linkHint]; };
  out.link = {
    held: link({ mode: 'auto', held_by: [{ id: 'voice', title: '语音搭子' }] }),
    two: link({ mode: 'auto', held_by: [{ id: 'voice', title: '语音搭子' }, { id: 'cocreate', title: null }] }),
    none: link({ mode: 'auto', held_by: [] }).slice(0, 2),
    always: link({ mode: 'always', held_by: [{ id: 'voice', title: '语音搭子' }] }).slice(0, 2),
  };
  globalThis.Alpine = { store: () => ({ v: 'zh-CN' }) };
  document.documentElement = { style: { setProperty() {} } };
  out.modes = ['auto', 'always', 'demand', undefined, 'x'].map((m) => {
    st.applySettings({ language: 'zh-CN', link_mode: m });
    return st.form.link_mode;
  });

  // in English
  globalThis.Alpine = { store: () => ({ v: 'en' }) };
  st.lang = 'en';
  st.applyStatus(status({ mode: 'auto', held_by: [{ id: 'voice', title: 'Voice' }] }, { outdated: { needs: '0.7.0', has: '0.6.0' } }));
  st.ext.list = copyOf(pkgs);
  st.lx.list = st.lx.list.map((r) => Object.assign({}, r, { title: r.id }));
  out.english = [st.kitOutdatedLine, st.xpKitBadge.text, st.linkHint, st.lxHeadWhy(cur('idle')), st.lxHeadWhy(cur('old')),
                 st.extWait(st.ext.list[1])];
  for (const action of Object.keys(LX_DOING)) {
    st.lx.busy = 'voice:' + action;
    out.english.push(st.lxWait(cur('voice')), st.lxWait(cur('ask')));
  }
  st.lx.busy = 'voice:later';
  out.english.push(st.lxWait(cur('voice')), st.lxWait(cur('ask')));
  st.lx.busy = '';
  for (const [busy, doing] of [['list', ''], ['wuxiankit', 'install'], ['wuxiankit', 'remove']]) {
    Object.assign(st.ext, { busy, doing });
    out.english.push(st.extWait(st.ext.list[0]), st.extWait(st.ext.list[1]), st.extRefreshText);
  }
  out.doing = [Object.keys(LX_DOING).sort(), Object.keys(LX_DOING_BY).sort()];
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class Wait(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((STATIC_DIR / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_selected_before_its_row_came(self):
        self.assertEqual(self.out["early"], {"sum": None, "cur": None, "read": 0})      # no row yet: nothing to read
        late = self.out["late"]
        self.assertEqual(late["read"], ["/api/local/summary?id=ask"])
        self.assertEqual((late["state"], late["ready"], late["why"], late["again"]), ("", True, "", 0))

    def test_why_load_is_off(self):
        self.assertEqual(self.out["head"], ["", "启用后可载入", "需要无限工坊 0.11.0 以上", ""])
        self.assertEqual(self.out["none"], ["", "", "", None])

    def test_who_is_busy(self):
        b = self.out["busy"]
        self.assertEqual((b["slot"], b["of"]), ("voice:check", {"id": "voice", "action": "check"}))
        self.assertEqual((b["own"], b["head"]), ("正在检查", "正在检查"))
        self.assertEqual((b["other"], b["idle"], b["approve"]), ("语音搭子 正在检查",) * 3)
        self.assertFalse(b["retry"])
        self.assertEqual(b["posted"], [])                                    # the click that did nothing has a reason now
        self.assertEqual(self.out["free"], ["", "", ""])
        self.assertEqual(self.out["set"], ["voice:set", "", ""])
        self.assertEqual(self.out["approving"], ["ask:approve", "", "正在批准", "待批准的 正在批准"])

    def test_why_approve_is_off(self):
        self.assertEqual(self.out["reading"], ["loading", "正在读取摘要", False])
        self.assertEqual(self.out["failed"], ["摘要读取失败，暂不能批准", True])
        self.assertEqual(self.out["stale"], ["扩展内容已变化，重新读取后再批准", True])

    def test_restart_is_the_answers(self):
        r = self.out["restart"]
        self.assertEqual((r["said"], r["mark"], r["same"], r["again"]), ("游戏插件已安装 · 重启游戏后生效", True, True, False))
        self.assertEqual(self.out["done"], ["x · 重启游戏后生效", "x", "x", "x"])

    def test_a_package_says_who(self):
        e = self.out["ext"]
        self.assertEqual(e["idle"], ["", "需要先安装 AtlasData", "", "刷新"])
        self.assertEqual(e["list"], ["正在读取扩展目录", "正在读取扩展目录", "读取中…"])
        i = e["install"]
        self.assertEqual(i["slot"], ["wuxiankit", "install"])
        self.assertEqual((i["own"], i["other"], i["bar"], i["refresh"]), ("正在安装", "扩展框架 正在安装", "扩展框架 正在安装", "进行中…"))
        self.assertEqual(i["posted"], [["/api/extensions", {"action": "install", "id": "wuxiankit"}]])
        self.assertEqual(e["remove"], ["正在卸载", "图集 正在卸载"])

    def test_the_framework_too_old(self):
        k = self.out["kit"]
        old = k["old"]
        self.assertEqual(old["o"], {"needs": "0.7.0", "has": "0.6.0"})
        self.assertEqual((old["line"], old["pkg"]), ("扩展框架需要更新到 0.7.0", "wuxiankit"))
        self.assertEqual(old["badge"], {"text": "需更新", "cls": "bad"})
        self.assertEqual(old["state"], {"cls": "bad", "text": "扩展框架需要更新到 0.7.0"})
        self.assertEqual(k["update"], [["/api/extensions", {"action": "install", "id": "wuxiankit"}]])   # the framework's own 更新
        self.assertEqual(k["unread"], {"needs": "0.7.0", "has": ""})
        fine = k["fine"]
        self.assertEqual((fine["o"], fine["line"]), (None, ""))
        self.assertNotEqual(fine["badge"], old["badge"])
        self.assertEqual(k["none"], [None, ""])
        self.assertIsNone(k["noEntry"])                                      # the catalog's needs a newer workshop: no button

    def test_the_link(self):
        link = self.out["link"]
        self.assertEqual(link["held"], [True, "语音搭子", "常开 · 由 语音搭子 保持"])
        self.assertEqual(link["two"][1:], ["语音搭子、cocreate", "常开 · 由 语音搭子、cocreate 保持"])
        self.assertEqual(link["none"], [False, ""])
        self.assertEqual(link["always"], [True, ""])
        self.assertEqual(self.out["modes"], ["auto", "always", "auto", "auto", "auto"])     # two modes: anything else is 自动

    def test_in_english(self):
        for text in self.out["english"]:
            self.assertTrue(text)
            self.assertNotRegex(text, r"[一-鿿]", text)
        self.assertEqual(self.out["doing"][0], self.out["doing"][1])         # each action has both lines
        self.assertEqual(self.out["doing"][0], ["approve", "check", "install", "load", "pack", "remove", "replace", "stop"])


class Page(unittest.TestCase):
    """index.html: a reason beside the buttons that can be off, the banner at the top of 扩展, the link's two modes"""

    def setUp(self):
        self.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.page = self.html[self.html.index('<section class="page" x-t x-show="page === \'extensions\'">'):]
        self.page = self.page[:self.page.index("</section>")]
        start = self.page.index("<!-- 本机扩展：")
        self.detail = self.page[start:self.page.index("<!-- Agent 会话 -->", start)]
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.css = (STATIC_DIR / "app.css").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")

    def test_the_detail_says_why(self):
        d = self.detail
        self.assertIn('<span class="lx-wait" role="status" :class="{ idle: lx.busy === \'\' }" x-show="lxHeadWhy(lxCur)" '
                      'x-text="lxHeadWhy(lxCur)"></span>', d)
        self.assertLess(d.index("lxHeadWhy(lxCur)"), d.index("@click=\"lxAct(lxCur, 'load')\""))    # beside the buttons
        card = d[d.index('<div class="lx-approve"'):]
        card = card[:card.index('<div class="xp-note')]
        self.assertLess(card.index('@click="lxApprove(lxCur)"'), card.index("lxApproveWhy(lxCur)"))
        self.assertLess(card.index("lxApproveWhy(lxCur)"), card.index('<dl class="kv">'))            # right under 批准
        self.assertIn('<button class="btn small" x-show="lxSumRetry(lxCur)" :disabled="lx.loading" @click="lxSumAgain(lxCur)"', card)
        self.assertIn("x-text=\"lx.loading ? t('读取中…') : lxSumState(lxCur) === 'error' ? t('重试') : t('重新读取')\"", card)
        self.assertIn('<p class="lx-wait" role="status" x-show="lxWait(lxCur)" x-text="lxWait(lxCur)"></p>', d)   # 设置
        self.assertIn("lxWait(lxCur) && !lxBusy(lxCur, 'install')", d)       # 游戏插件 · 安装 / 更新
        self.assertIn("lxWait(lxCur) && !lxBusy(lxCur, 'replace')", d)       # 替换

    def test_every_button_that_is_off_while_busy_has_the_line_near(self):
        """whatever lx.busy turns off sits in a block that shows lxWait (or the head's / the card's line of it)"""
        d = self.detail
        blocks = {"head": d[:d.index('<div class="xp-confirm"')],
                  "card": d[d.index('<div class="lx-approve"'):d.index('<div class="xp-note"', d.index('<div class="lx-approve"'))],
                  "swap": d[d.index('<template x-for="a in lxSwaps(lxCur)"'):d.index('<div class="xp-note" x-show="lx.packed')],
                  "game": d[d.index('class="lx-game">'):d.index("</dd>", d.index('class="lx-game">'))],
                  "opts": d[d.index("<div x-show=\"xpTab === 'opts'\">"):d.index("<div x-show=\"xpTab === 'caps'\">")]}
        said = {"head": "lxHeadWhy(lxCur)", "card": "lxApproveWhy(lxCur)", "swap": "lxWait(lxCur)", "game": "lxWait(lxCur)",
                "opts": "lxWait(lxCur)"}
        for name, block in blocks.items():
            self.assertIn("lx.busy !== ''", block, name)
            self.assertIn('x-text="%s"' % said[name], block, name)
        rest = d
        for block in blocks.values():
            rest = rest.replace(block, "")
        left = re.findall(r"<[^>]*:disabled=\"lx\.busy !== ''[^>]*>", rest)
        self.assertEqual(len(left), 1, left)                                 # 确认移除, right under the head's line
        self.assertIn("lxRemove(lxCur)", left[0])

    def test_a_package_says_why(self):
        p = self.page
        self.assertIn('<span class="lx-wait" role="status" x-show="extWait(xpSel.pkg)" x-text="extWait(xpSel.pkg)"></span>', p)
        self.assertIn('<span x-text="extRefreshText"></span>', p)
        self.assertIn("x-text=\"reload.busy ? t('进行中…') : t('重载界面')\"", p)

    def test_the_banner(self):
        p = self.page
        # an older daemon's notice: with status.versions the version strip (test_versions) takes its place
        start = p.index('<div class="xp-note xp-top" role="status" x-show="kitOutdated && !vers">')
        self.assertLess(p.index('<div class="pagebar">'), start)
        self.assertLess(start, p.index('<div class="pagebody fill master-detail xp">'))             # at the page's top
        banner = p[start:p.index("</div>", start)]
        self.assertIn('<b x-text="kitOutdatedLine"></b>', banner)
        self.assertIn("t('当前 {has}', { has: kitOutdated.has })", banner)
        self.assertIn("@click=\"extAct(kitUpdatePkg, 'install')\"", banner)   # the framework's own 更新
        self.assertIn("t('更新到 {v}', { v: kitUpdatePkg ? kitUpdatePkg.version : '' })", banner)
        self.assertIn('x-show="kitUpdatePkg"', banner)
        self.assertIn("\n.xp-note.xp-top {", self.css)
        self.assertIn("\n.lx-wait {", self.css)

    def test_the_two_modes(self):
        start = self.html.index('<select class="input" name="link_mode" x-model="form.link_mode">')
        select = self.html[start:self.html.index("</select>", start)]
        self.assertEqual(re.findall(r'<option value="(\w+)">([^<]+)</option>', select), [("auto", "自动"), ("always", "常开")])
        after = self.html[start:start + 900]
        self.assertIn('<p class="muted small">自动：有需要即时反应的应用运行时保持连接，其余时候传完就收。</p>', after)
        self.assertIn('<p class="muted small">常开：进入游戏世界后一直连接。</p>', after)
        for old in ("'demand'", 'value="demand"', "held_titles", "跟随应用", "按需连接", "link_mode_v"):
            self.assertNotIn(old, self.html, old)
            self.assertNotIn(old, self.js, old)
            self.assertNotIn(old, self.i18n, old)

    def test_the_words_have_their_english(self):
        for word in ("自动", "常开", "自动：有需要即时反应的应用运行时保持连接，其余时候传完就收。", "常开：进入游戏世界后一直连接。",
                     "常开 · 由 {app} 保持", "扩展框架需要更新到 {needs}", "当前 {has}", "需更新", "更新到 {v}",
                     "正在读取摘要", "摘要读取失败，暂不能批准", "扩展内容已变化，重新读取后再批准", "启用后可载入",
                     "正在载入", "{name} 正在载入", "正在安装游戏插件", "{name} 正在安装游戏插件", "{name} 正在处理",
                     "正在读取扩展目录", "{name} 正在安装", "{name} 正在卸载", "需要先安装 {names}", "重启游戏后生效"):
            self.assertIn("\n  '%s': '" % word, self.i18n, word)


if __name__ == "__main__":
    unittest.main()
