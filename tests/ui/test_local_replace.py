"""替换游戏插件 on the 扩展 page: a game addon of a local extension whose folder in the game's AddOns is not the
extension's (state foreign). app.js run under Node (skipped without it) against rows in the shape of the daemon's: a
foreign addon row has same (the same addon, installed by hand) and there (its version), and newer when that is newer
than the extension's (contract3, section 5).

- the 游戏插件 tag says 手动安装的 <there> for the same addon, 保留游戏中的较新版本 <there> for a newer one (it stays),
  同名插件已存在 for another;
- the 批准 card: the summary's replaces is a row (替换游戏插件: what the yes replaces, 原版本存入历史), a game row says
  how the folder stands, the card's line names what the yes does not install;
- once the extension is allowed, 替换 is offered in one place, a row for each such addon: the same addon is replaced at
  a click (the row says 原版本存入历史), another after the 确认 (替换游戏中的 <folder> <there>？…). The request is
  /api/local/install {id, replace: [{folder, there}]}: the folder the player saw, at the version they saw; the answer's
  restart is said (重启游戏后生效); stopping at another folder is not a failure of this one;
- one verb, 替换; every word has its English."""
import json
import re
import subprocess
import unittest

from wuxianworkshop.ui import STATIC_DIR

from tests.ui.test_i18n import HAN, Page as Words, js_strings, norm
from tests.ui.test_local import NODE, STUBS

HARNESS = r"""
const out = {};
const calls = [];
let answer = null, during = null;
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
let rows = [
  gm('same', [foreign({ same: true, there: '1.1.1' })], { player: says('replace_addon') }),
  gm('other', [foreign({ same: false, there: '2.0' })], { player: says('replace_addon') }),
  gm('newer', [foreign({ same: true, there: '1.3.0', newer: true })]),
  gm('link', [foreign()], { player: says('replace_addon') }),
  gm('bare', [foreign({ same: false, there: null })], { player: says('replace_addon') }),
  gm('plain', [foreign({ same: true, there: '1.1.1', version: null })]),
  gm('two', [foreign({ folder: 'WxA', same: true, there: '1.0' }), foreign({ folder: 'WxB', same: false, there: '3.0' })],
     { title: '两件套', player: says('replace_addon') }),
];
const row = (id) => rows.find((r) => r.id === id);
async function api(path, opts) {
  const body = opts && opts.body;
  calls.push([path, body ? copyOf(body) : null]);
  if (path === '/api/local') return { extensions: copyOf(rows), capabilities: {} };
  if (path.startsWith('/api/local/summary')) return {};
  if (path === '/api/local/install') {
    if (during) during();
    const a = answer;
    answer = null;
    if (a instanceof Error) throw a;
    if (a) return a;
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
const cur = (id) => st.lx.list.find((r) => r.id === id);
const last = () => st.said[st.said.length - 1];
const installs = () => calls.filter((c) => c[0] === '/api/local/install');
const sumOf = (s, brief, id) => st.lxSumRows(s, brief).filter((x) => x.id === id).map((x) => [x.k, x.v]);
const game = [
  { folder: 'WuxianCoCreate', version: '1.2.0', needs: [], path: 'G:\\AddOns\\WuxianCoCreate', cannot: 'foreign', same: true, there: '1.1.1', newer: false },
  { folder: 'WuxianCoCreate', version: '1.2.0', needs: [], path: 'G:\\AddOns\\WuxianCoCreate', cannot: 'foreign', same: true, there: '1.3.0', newer: true },
  { folder: 'WuxianCoCreate', version: '1.2.0', needs: [], path: 'G:\\AddOns\\WuxianCoCreate', cannot: 'foreign', same: false, there: '2.0', newer: false },
  { folder: 'WuxianCoCreate', version: null, needs: [], path: 'G:\\AddOns\\WuxianCoCreate', cannot: 'foreign', same: true, there: null, newer: false },
  { folder: 'WuxianCoCreate', version: null, needs: [], path: 'G:\\AddOns\\WuxianCoCreate', cannot: 'foreign' },
];
const replaces = [{ folder: 'WuxianCoCreate', there: '1.1.1', same: true, newer: false }, { folder: 'WxSkin', there: null, same: true, newer: false }];
const asked = (r) => Object.assign({}, r, { approval: 'needed', player: says('approve') });

(async () => {
  out.tags = rows.slice(0, 5).map((r) => [st.lxAddonState(r.addons[0]), st.lxAddonPill(r.addons[0]), st.lxSwappable(r.addons[0])]);
  out.short = [st.lxAddonState(row('newer').addons[0], true), st.lxState(row('newer'), true).text, st.lxState(row('newer')).text,
               st.lxAddonState(foreign({ same: true, newer: true, there: null }))];

  // the 批准 card: what the yes replaces is the summary's replaces; a game row says how the folder stands
  out.card = { replaces: sumOf({ game, replaces }, true, 'replaces'), none: sumOf({ game, replaces: [] }, true, 'replaces'),
               detail: sumOf({ game, replaces }, false, 'replaces'), game: sumOf({ game }, true, 'game').map((x) => x[1]) };
  out.stuck = ['same', 'other', 'newer', 'link'].map((id) => { const s = st.lxStuckOf(asked(row(id))); return s ? [s.text, s.bad] : null; });
  out.stuckBare = st.lxStuck(asked(gm('x', [foreign({ same: true, newer: true, there: null })])));

  // 替换, one place: a row for each such addon of an allowed extension, none before the yes; the player's line leaves it to them
  out.swaps = rows.map((r) => [r.id, st.lxSwaps(r).map((a) => st.lxSwapText(a))]);
  out.before = st.lxSwaps(asked(row('same'))).length;
  out.player = rows.map((r) => st.lxPlayer(r));
  out.why = [lxWhy(apiErr(409, 'addon_exists', 'x', { same: true, newer: true, there: '1.3.0' })),
             lxWhy(apiErr(409, 'addon_exists', 'x', { same: true, newer: true })),
             lxWhy(apiErr(409, 'addon_exists', 'x', { same: true, newer: false }))];

  // the same addon: at once, with the folder and the version the player saw
  answer = { id: 'same', restart: true, hint: 'h',
             addons: [{ folder: 'WuxianCoCreate', state: 'installed', was: 'foreign', version: '1.2.0', kept: '0001' }] };
  calls.length = 0;
  during = () => { out.busy = [st.lx.busy, st.lxWait(cur('same')), st.lxWait(cur('two')), st.lxHeadWhy(cur('two')), st.lxApproveWhy(cur('two'))]; };
  await st.lxSwap(cur('same'), st.lxSwaps(cur('same'))[0]);
  during = null;
  out.same = { posts: installs(), said: last(), restart: st.lxRestart(cur('same')), swap: st.lx.swap, busy: st.lx.busy,
               wait: st.lxWait(cur('same')) };

  // another addon: 确认 first
  calls.length = 0;
  let a = st.lxSwaps(cur('other'))[0];
  await st.lxSwap(cur('other'), a);
  out.ask = { posts: installs().length, swap: st.lx.swap, asked: [st.lxSwapAsked(cur('other'), a), st.lxSwapAsked(cur('bare'), a)],
              line: st.lxSwapLine(a), bare: st.lxSwapLine(cur('bare').addons[0]) };
  answer = { id: 'other', restart: false, hint: null,
             addons: [{ folder: 'WuxianCoCreate', state: 'installed', was: 'foreign', version: '1.2.0', kept: '0002' }] };
  await st.lxSwap(cur('other'), a, true);
  out.other = { posts: installs(), said: last(), restart: st.lxRestart(cur('other')), swap: st.lx.swap };
  calls.length = 0;
  await st.lxSwap(cur('bare'), cur('bare').addons[0], true);    // a version nobody could read: asked for as it was seen
  out.bare = installs();

  // refused: the note says why
  answer = apiErr(409, 'addon_exists', 'WuxianCoCreate: newer', { folder: 'WuxianCoCreate', same: true, there: '1.3.0', newer: true });
  await st.lxSwap(cur('other'), foreign({ same: true, there: '1.1.1' }));
  out.refused = st.lx.note;
  st.lx.note = null;

  // it stopped at another folder: this one went in, the other keeps its row
  calls.length = 0;
  answer = apiErr(409, 'addon_exists', 'WxB: another addon', { folder: 'WxB', same: false, there: '3.0' });
  rows = rows.map((r) => (r.id !== 'two' ? r : Object.assign({}, r, {
    addons: [Object.assign({}, r.addons[0], { state: 'current' }), r.addons[1]] })));
  await st.lxSwap(cur('two'), cur('two').addons[0]);
  out.two = { posts: installs(), said: last(), note: st.lx.note, left: st.lxSwaps(cur('two')).map((x) => x.folder) };

  // nothing of before the contract: no three-way replace, no helpers of the two entries
  out.gone = ['lxSwapOf', 'lxForeignLine'].filter((n) => typeof st[n] === 'function');

  // in English
  globalThis.Alpine = { store: () => ({ v: 'en' }) };
  st.lang = 'en';
  out.english = [].concat(rows.slice(0, 5).map((r) => st.lxAddonState(r.addons[0])), [st.lxAddonState(row('newer').addons[0], true)],
                          sumOf({ game, replaces }, true, 'replaces').map((x) => x.join(' ')), sumOf({ game }, true, 'game').map((x) => x[1]),
                          ['other', 'newer', 'link'].map((id) => st.lxStuck(asked(row(id)))),
                          [row('same'), row('other'), row('plain')].map((r) => st.lxSwapText(r.addons[0])),
                          [st.lxSwapLine(row('other').addons[0]), st.lxSwapLine(row('bare').addons[0])],
                          [st.lxDone('x', { restart: true })]);
  out.english.push(lxWhy(apiErr(409, 'addon_exists', 'x', { same: true, newer: true, there: '1.3.0' })),
                   lxWhy(apiErr(409, 'addon_exists', 'x', { same: false })), st.reasonText('replace'));
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
                                            ["保留游戏中的较新版本 1.3.0", "warn", False],   # newer: it stays, no 替换
                                            ["同名插件已存在", "bad", False],                 # a link or a file: never
                                            ["同名插件已存在", "bad", True]])
        self.assertEqual(self.out["short"], ["保留较新版本", "保留较新版本", "保留游戏中的较新版本 1.3.0",   # the list's tag is short
                                             "保留游戏中的较新版本"])

    def test_the_approval_card(self):
        card = self.out["card"]
        self.assertEqual(card["replaces"], [["替换游戏插件", "WuxianCoCreate 1.1.1、WxSkin · 批准后替换，原版本存入历史"]])
        self.assertEqual((card["none"], card["detail"]), ([], []))          # nothing to replace; and only the card says it
        self.assertEqual(card["game"], [
            "WuxianCoCreate 1.2.0 · 游戏中已有手动安装的 1.1.1",
            "WuxianCoCreate 1.2.0 · 保留游戏中的较新版本 1.3.0",
            "WuxianCoCreate 1.2.0 · 游戏中已有同名插件，批准后可替换",
            "WuxianCoCreate · 游戏中已有手动安装的版本",
            "WuxianCoCreate · 无法安装：游戏中已有同名插件"])               # not a folder: nothing replaces it
        self.assertEqual(self.out["stuck"], [None,                           # the yes replaces it: nothing in its way
                                             ["游戏中已有同名插件 WuxianCoCreate 2.0，批准后可替换", False],
                                             ["WuxianCoCreate：保留游戏中的较新版本 1.3.0", False],
                                             ["游戏插件无法安装：WuxianCoCreate 游戏中已有同名插件", True]])
        self.assertEqual(self.out["stuckBare"], "WuxianCoCreate：保留游戏中的较新版本")

    def test_one_place(self):
        swaps = dict(self.out["swaps"])
        self.assertEqual(swaps["same"], ["游戏中已有手动安装的 WuxianCoCreate 1.1.1。替换为 1.2.0，原版本存入历史。"])
        self.assertEqual(swaps["plain"], ["游戏中已有手动安装的 WuxianCoCreate 1.1.1。替换后原版本存入历史。"])
        self.assertEqual(swaps["other"], ["游戏中已有同名插件 WuxianCoCreate 2.0，替换后生效。"])
        self.assertEqual(swaps["bare"], ["游戏中已有同名插件 WuxianCoCreate，替换后生效。"])
        self.assertEqual((swaps["newer"], swaps["link"]), ([], []))
        self.assertEqual(len(swaps["two"]), 2)                               # a row each
        self.assertEqual(self.out["before"], 0)                              # the card's row says it until the yes
        self.assertEqual(self.out["player"], ["", "", "", "the daemon says: replace_addon", "", "", ""])   # the rows say it

    def test_replacing_the_same_addon(self):
        same = self.out["same"]
        self.assertEqual(same["posts"], [["/api/local/install",
                                          {"id": "same", "replace": [{"folder": "WuxianCoCreate", "there": "1.1.1"}]}]])
        self.assertEqual((same["said"], same["restart"], same["swap"], same["busy"], same["wait"]),
                         ("已替换 WuxianCoCreate · 重启游戏后生效", True, "", "", ""))

    def test_who_is_busy_is_said(self):
        busy, own, other, head, approve = self.out["busy"]
        self.assertEqual(busy, "same:replace")
        self.assertEqual(own, "正在替换游戏插件")
        self.assertEqual((other, head, approve), ("same 正在替换游戏插件",) * 3)   # another extension's buttons: who and what

    def test_another_addon_asks_first(self):
        ask = self.out["ask"]
        self.assertEqual((ask["posts"], ask["swap"], ask["asked"]), (0, "other:WuxianCoCreate", [True, False]))
        self.assertEqual(ask["line"], "替换游戏中的 WuxianCoCreate 2.0？原版本存入历史，可在开发台恢复。")
        self.assertEqual(ask["bare"], "替换游戏中的 WuxianCoCreate？原版本存入历史，可在开发台恢复。")
        other = self.out["other"]
        self.assertEqual(other["posts"], [["/api/local/install",
                                           {"id": "other", "replace": [{"folder": "WuxianCoCreate", "there": "2.0"}]}]])
        self.assertEqual((other["said"], other["restart"], other["swap"]), ("已替换 WuxianCoCreate", False, ""))
        self.assertEqual(self.out["bare"], [["/api/local/install",
                                             {"id": "bare", "replace": [{"folder": "WuxianCoCreate", "there": None}]}]])

    def test_refused(self):
        self.assertEqual(self.out["refused"], {"bad": True, "text": "替换失败：保留游戏中的较新版本 1.3.0"})
        self.assertEqual(self.out["why"], ["保留游戏中的较新版本 1.3.0", "保留游戏中的较新版本", "游戏中已有同名插件，未替换"])

    def test_stopped_at_another_folder(self):
        two = self.out["two"]
        self.assertEqual(two["posts"], [["/api/local/install", {"id": "two", "replace": [{"folder": "WxA", "there": "1.0"}]}]])
        self.assertEqual((two["said"], two["note"], two["left"]), ("已替换 WxA", None, ["WxB"]))

    def test_nothing_of_before(self):
        self.assertEqual(self.out["gone"], [])

    def test_in_english(self):
        for text in self.out["english"]:
            self.assertTrue(text)
            self.assertNotRegex(text, r"[一-鿿]", text)
        self.assertIn("Installed by hand, 1.1.1", self.out["english"])
        self.assertIn("Before a replacement", self.out["english"])


class Page(unittest.TestCase):
    """index.html: 替换 in its rows over the tabs and nowhere else, the 确认 in the row; the words the page no longer says"""

    def setUp(self):
        self.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = self.html.index("<!-- 本机扩展：")
        self.detail = self.html[start:self.html.index("<!-- Agent 会话 -->", start)]
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def test_one_entry(self):
        d = self.detail
        self.assertEqual(d.count('<template x-for="a in lxSwaps(lxCur)" :key="lxCur.id + \':\' + a.folder">'), 1)
        self.assertEqual(len(re.findall(r"lxSwap\(", d)), 2)                 # 替换, and 确认替换 in the same row
        self.assertIn('@click="lxSwap(lxCur, a)"', d)
        self.assertIn('@click="lxSwap(lxCur, a, true)">确认替换</button>', d)
        self.assertIn("x-text=\"lxSwapAsked(lxCur, a) ? lxSwapLine(a) : lxSwapText(a)\"", d)
        self.assertEqual(d.count("t('替换')"), 1)
        addons = d[d.index('<dd x-show="(lxCur.addons || []).length" class="lx-game">'):]
        addons = addons[:addons.index("</dd>")]
        self.assertNotIn("lxSwap", addons)                                   # 概况 has the tag only
        self.assertLess(d.index("lxSwaps(lxCur)"), d.index('<div class="tabs">'))
        self.assertNotIn("lxSwapOf", self.html + self.js)

    def test_the_request(self):
        self.assertIn("replace: [{ folder: a.folder, there: a.there == null ? null : a.there }]", self.js)
        for old in ("replace: a.same ? 'same' : true", "'same'"):
            self.assertNotIn(old, self.js)

    def test_one_verb(self):
        """what a player reads: 替换 (not 换成 / 覆盖 / 保留不换), 游戏中 for where the addon is, 本机端 for the half on
        the computer, 未运行 for a process that is not there"""
        words = Words()
        words.feed(self.html)
        texts = set(words.texts) | {t for t, *_ in words.literals} | {norm(t) for _, _, t in js_strings(self.js) if HAN.search(t)}
        for banned in ("保留不换", "未覆盖", "批准后换成", "存进历史", "游戏里已有", "本机扩展", "没在运行"):
            self.assertEqual([t for t in texts if banned in t], [], banned)
        self.assertIn("批准、替换扩展包与分享时显示相同内容", texts)             # a package, not a game addon
        self.assertIn("替换扩展包 {name}？", texts)


if __name__ == "__main__":
    unittest.main()
