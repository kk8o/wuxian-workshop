"""A game addon of a local extension whose folder in the game's AddOns is not the extension's (state foreign) on the 扩展
page. app.js run under Node (skipped without it) against rows in the shape of the daemon's: a foreign addon row has
same (the same addon, installed by hand) and there (its version), and newer when that is newer than the extension's.

- the 游戏插件 tag says 手动安装的 <there> for the same addon, 同名插件已存在 for another;
- the 批准 card's summary row says the yes replaces the same addon (原来的存进历史), keeps a newer one, and that another
  cannot be installed; the card's stuck line leaves out the one the yes replaces;
- after the yes, the player's line (replace_addon) and the tag have 替换: the same addon at once (replace "same"),
  another after a 确认 (replace true); the answer's restart is said as for an install;
- every word has its English."""
import json
import subprocess
import unittest

from wuxianworkshop.ui import STATIC_DIR

from tests.ui.test_local import NODE, STUBS

HARNESS = r"""
const out = {};
const calls = [];
let answer = null;
function apiErr(status, code, message, extra) {
  const e = new Error(code + ': ' + message);
  e.status = status;
  e.data = Object.assign({ error: Object.assign({ code, message }, extra || {}) }, extra || {});
  return e;
}
const copyOf = (r) => JSON.parse(JSON.stringify(r));
const base = { version: '1.2.0', enabled: true, start: 'use', state: 'game', pid: null, started: null, exit_code: null,
               error: null, capabilities: [], sources: [], options: [], apps: [], skills: [], adds: {}, approval: 'ok',
               since: null, why: null, origin: 'home', author: null, homepage: null, requires: {}, unmet: [],
               command: null, where: 'game', player: null };
const foreign = (more) => Object.assign({ folder: 'WuxianCoCreate', version: '1.2.0', state: 'foreign' }, more);
const gm = (id, addons, more) => Object.assign({}, base, { id, title: id, path: 'D:\\ext\\' + id, fingerprint: 'f:' + id,
  addons, game: { extensions: [id], state: 'absent' } }, more);
const says = (action) => ({ action, text: 'the daemon says: ' + action });
const rows = [
  gm('same', [foreign({ same: true, there: '1.1.1' })], { player: says('replace_addon') }),
  gm('other', [foreign({ same: false, there: '2.0' })], { player: says('replace_addon') }),
  gm('newer', [foreign({ same: true, there: '1.3.0', newer: true })]),
  gm('link', [foreign()]),
  gm('bare', [foreign({ same: false, there: null })], { player: says('replace_addon') }),
];
const row = (id) => rows.find((r) => r.id === id);
async function api(path, opts) {
  const body = opts && opts.body;
  calls.push([path, body ? copyOf(body) : null]);
  if (path === '/api/local') return { extensions: copyOf(rows), capabilities: {} };
  if (path.startsWith('/api/local/summary')) return {};
  if (path === '/api/local/install' && answer) {
    const a = answer;
    answer = null;
    if (a instanceof Error) throw a;
    return a;
  }
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
st.lx.list = copyOf(rows);
const last = () => st.said[st.said.length - 1];
const sumOf = (game) => st.lxSumRows({ game }).filter((x) => x.id === 'game').map((x) => x.v);
const sum = [
  { folder: 'WuxianCoCreate', version: '1.2.0', needs: [], path: 'G:\\AddOns\\WuxianCoCreate', cannot: 'foreign', same: true, there: '1.1.1', newer: false },
  { folder: 'WuxianCoCreate', version: '1.2.0', needs: [], path: 'G:\\AddOns\\WuxianCoCreate', cannot: 'foreign', same: true, there: '1.3.0', newer: true },
  { folder: 'WuxianCoCreate', version: '1.2.0', needs: [], path: 'G:\\AddOns\\WuxianCoCreate', cannot: 'foreign', same: false, there: '2.0', newer: false },
  { folder: 'WuxianCoCreate', version: null, needs: [], path: 'G:\\AddOns\\WuxianCoCreate', cannot: 'foreign', same: true, there: null, newer: false },
];
const asked = (r) => Object.assign({}, r, { approval: 'needed', player: says('approve') });

(async () => {
  out.tags = rows.map((r) => [st.lxAddonState(r.addons[0]), st.lxAddonPill(r.addons[0]), st.lxSwappable(r.addons[0])]);
  out.sum = sumOf(sum);
  out.stuck = ['same', 'other', 'newer', 'link'].map((id) => st.lxStuck(asked(row(id))));
  out.player = rows.map((r) => st.lxPlayer(r));
  out.why = ['', lxWhy(apiErr(409, 'addon_exists', 'x', { same: true, newer: true })),
             lxWhy(apiErr(409, 'addon_exists', 'x', { same: true, newer: false }))];

  // 替换: the same addon at once
  answer = { id: 'same', restart: true, hint: 'h',
             addons: [{ folder: 'WuxianCoCreate', state: 'installed', was: 'foreign', version: '1.2.0', kept: '0001' }] };
  calls.length = 0;
  await st.lxSwap(row('same'), st.lxSwapOf(row('same')));
  out.same = { posts: calls.filter((c) => c[0] === '/api/local/install'), said: last(), restart: st.lxRestart(row('same')),
               swap: st.lx.swap, busy: st.lx.busy };

  // another addon: 确认 first
  calls.length = 0;
  await st.lxSwap(row('other'), st.lxSwapOf(row('other')));
  out.ask = { posts: calls.filter((c) => c[0] === '/api/local/install').length, swap: st.lx.swap, line: st.lxSwapLine(row('other')),
              bare: st.lxSwapLine(Object.assign({}, row('bare'), { id: 'other' })) };
  answer = { id: 'other', restart: false, hint: null,
             addons: [{ folder: 'WuxianCoCreate', state: 'installed', was: 'foreign', version: '1.2.0', kept: '0002' }] };
  await st.lxSwap(row('other'), st.lxSwapOf(row('other')), true);
  out.other = { posts: calls.filter((c) => c[0] === '/api/local/install'), said: last(), restart: st.lxRestart(row('other')),
                swap: st.lx.swap, line: st.lxSwapLine(row('other')) };

  // refused: the note says why
  answer = apiErr(409, 'addon_exists', 'WuxianCoCreate: newer', { same: true, there: '1.3.0', newer: true });
  await st.lxSwap(row('other'), foreign({ same: true, there: '1.1.1' }));
  out.refused = st.lx.note;
  st.lx.note = null;

  // in English
  globalThis.Alpine = { store: () => ({ v: 'en' }) };
  st.lang = 'en';
  out.english = [].concat(rows.map((r) => st.lxAddonState(r.addons[0])), sumOf(sum), rows.map((r) => st.lxPlayer(r)),
                          [st.lxStuck(asked(row('newer')))]);
  st.lx.swap = 'other:WuxianCoCreate';
  out.english.push(st.lxSwapLine(row('other')), st.lxSwapLine(Object.assign({}, row('bare'), { id: 'other' })));
  out.english.push(lxWhy(apiErr(409, 'addon_exists', 'x', { same: true, newer: true })), st.reasonText('replace'));
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class Replace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((STATIC_DIR / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_the_tag(self):
        self.assertEqual(self.out["tags"], [["手动安装的 1.1.1", "warn", True], ["同名插件已存在", "bad", True],
                                            ["手动安装的 1.3.0", "warn", False],     # newer: kept, no 替换
                                            ["同名插件已存在", "bad", False],          # a link or a file: never
                                            ["同名插件已存在", "bad", True]])

    def test_the_approval_card(self):
        self.assertEqual(self.out["sum"], [
            "WuxianCoCreate 1.2.0 · 游戏里已有手动安装的 WuxianCoCreate 1.1.1，批准后换成 1.2.0；原来的存进历史，可以找回",
            "WuxianCoCreate 1.2.0 · 游戏里的 WuxianCoCreate 1.3.0 比扩展带的新，保留不换",
            "WuxianCoCreate 1.2.0 · 无法安装：游戏里已有同名插件",
            "WuxianCoCreate · 游戏里已有手动安装的 WuxianCoCreate，批准后换成扩展带的版本；原来的存进历史，可以找回"])
        self.assertEqual(self.out["stuck"], ["",                                   # the yes replaces it: nothing stuck
                                             "WuxianCoCreate 游戏里已有同名插件",
                                             "游戏里的 WuxianCoCreate 1.3.0 比扩展带的新，保留不换",
                                             "WuxianCoCreate 游戏里已有同名插件"])

    def test_the_players_line(self):
        self.assertEqual(self.out["player"], [
            "游戏里已有手动安装的 WuxianCoCreate 1.1.1，替换为扩展带的 1.2.0 后生效",
            "游戏里已有同名插件 WuxianCoCreate 2.0，替换后生效",
            "", "",                                                                # no replace_addon: nothing to say
            "游戏里已有同名插件 WuxianCoCreate，替换后生效"])

    def test_replacing_the_same_addon(self):
        same = self.out["same"]
        self.assertEqual(same["posts"], [["/api/local/install", {"id": "same", "replace": "same"}]])
        self.assertEqual((same["said"], same["restart"], same["swap"], same["busy"]), ("已替换 WuxianCoCreate", True, "", ""))

    def test_another_addon_asks_first(self):
        ask = self.out["ask"]
        self.assertEqual((ask["posts"], ask["swap"]), (0, "other:WuxianCoCreate"))
        self.assertEqual(ask["line"], "游戏里的 WuxianCoCreate 不是这个扩展带的插件（2.0）。替换后原来的存进历史，可以在开发台找回。替换？")
        self.assertEqual(ask["bare"], "游戏里的 WuxianCoCreate 不是这个扩展带的插件（未知版本）。替换后原来的存进历史，可以在开发台找回。替换？")
        other = self.out["other"]
        self.assertEqual(other["posts"], [["/api/local/install", {"id": "other", "replace": True}]])
        self.assertEqual((other["said"], other["restart"], other["swap"], other["line"]),
                         ("已替换 WuxianCoCreate", False, "", ""))
        self.assertEqual(self.out["refused"], {"bad": True, "text": "替换失败：游戏里的插件版本更新，保留不换"})
        self.assertEqual(self.out["why"][1:], ["游戏里的插件版本更新，保留不换", "游戏里已有同名插件，未覆盖"])

    def test_in_english(self):
        for text in self.out["english"]:
            self.assertNotRegex(text, r"[一-鿿]", text)
        self.assertIn("Installed by hand, 1.1.1", self.out["english"])
        self.assertIn("Before a replacement", self.out["english"])


class Page(unittest.TestCase):
    """index.html: 替换 in the player's line and beside the tag, and the 确认 before another addon is replaced"""

    def setUp(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = html.index("<!-- 本机扩展：")
        self.detail = html[start:html.index("<!-- Agent 会话 -->", start)]

    def test_the_buttons(self):
        d = self.detail
        self.assertIn('@click="lxSwap(lxCur, lxSwapOf(lxCur))"', d)
        self.assertIn('x-show="lxSwappable(a) && !lxUnapproved(lxCur)"', d)
        self.assertIn('<div class="xp-confirm" x-show="lxSwapLine(lxCur)">', d)
        self.assertIn("t('确认替换')", d)
        self.assertEqual(d.count("t('替换')"), 2)
