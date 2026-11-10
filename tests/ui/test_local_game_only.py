"""An extension in the game only on the 扩展 page (a row with where game: game addons and no process). app.js run under
Node (skipped without it) against rows in the shape of the daemon's (state game, command null, game {extensions,
state}, player {action, text}):

- its state is how the game has it (未安装 / 待重启游戏 / 插件未启用 / 未启用 / 已安装 / 运行中 …), after 已停用, 清单有误 and 待批准;
- what is left for the player is one line in the App's words (a program's game half says nothing while the game is
  only not running; the 批准 card says the approval);
- no command, no 启动方式, no 日志: the card and 概况 name its game addons, 能力 are the game's tools once it is loaded;
- 载入 says what the game refused (loaded [{folder, ok, error}]) and a Lua syntax error that kept it out (422
  check_failed with the addons' findings); 检查 lists the Lua findings as its problems;
- the page hides and shows those parts by lxGameOnly, and every word has its English."""
import json
import re
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
const base = { version: '0.1.0', enabled: true, start: 'use', state: 'game', pid: null, started: null, exit_code: null,
               error: null, capabilities: [], sources: [], options: [], apps: [], skills: [], adds: {}, approval: 'ok',
               since: null, why: null, origin: 'linked', author: null, homepage: null, requires: {}, unmet: [],
               command: null, where: 'game', player: null };
const addon = (folder, state) => ({ folder, version: '0.1.0', state: state || 'current' });
const gm = (id, gstate, more) => Object.assign({}, base, { id, title: id, path: 'D:\\ext\\' + id,
  addons: [addon('Wx' + id[0].toUpperCase() + id.slice(1))], game: { extensions: [id], state: gstate } }, more);
const player = (action) => ({ action, text: 'the daemon says: ' + action });
let rows = [
  gm('tips', 'on'),
  gm('fresh', 'restart', { player: player('restart_game') }),
  gm('asked', 'absent', { approval: 'needed', origin: 'home', addons: [addon('WxAsked', 'missing')], player: player('approve') }),
  gm('edited', 'on', { approval: 'changed', origin: 'home' }),
  Object.assign({}, base, { id: 'duo', title: 'duo', path: 'D:\\ext\\duo', state: 'stopped', where: 'both',
    command: ['python', 'main.py'], capabilities: ['duo.say'], addons: [addon('WxDuo')],
    game: { extensions: ['duo_game'], state: 'offline' }, player: player('start_game') }),
  Object.assign({}, base, { id: 'solo', title: 'solo', path: 'D:\\ext\\solo', state: 'stopped', where: 'local',
    command: ['python', 'main.py'], addons: [], game: null }),
  Object.assign({}, base, { id: 'old', title: 'old', path: 'D:\\ext\\old', state: 'stopped', where: undefined,
    command: ['python', 'main.py'], addons: [addon('WxOld')], game: undefined, player: undefined }),
];
const row = (id) => rows.find((r) => r.id === id);
async function api(path, opts) {
  const body = opts && opts.body;
  calls.push([path, body ? copyOf(body) : null]);
  if (path === '/api/local') return { extensions: copyOf(rows), capabilities: {} };
  const action = path.startsWith('/api/local/') ? path.slice(11).split('?')[0] : '';
  if ((action === 'load' || action === 'check') && answer) {
    const a = answer;
    answer = null;
    if (a instanceof Error) throw a;
    return a;
  }
  if (action === 'load') return { row: copyOf(row(body.id)), check: { ok: true, problems: [], capabilities: [] }, loaded: [] };
  if (action === 'check') return { ok: true, problems: [], capabilities: [], where: 'game', addons: [] };
  return {};
}
const tool = (name, label) => ({ name, cap: name.slice(3).replace('_', '.'), kind: 'see', label, title: name });
const st = appState();
st.token = 't';
st.$nextTick = (f) => f();
st.$refs = { lxout: { scrollTop: 0, scrollHeight: 0, clientHeight: 0 } };
st.said = [];
st.say = (x) => st.said.push(x);
st.copy = () => {};
st.api = api;
st.lx.list = copyOf(rows);
st.access.data = { kit_state: 'running', servers: [], kit: { version: '0.6.0', source: 'game', newer: false, extensions: [
  { id: 'kit', title: '核心', version: '0.6.0', addon: 'WuxianKit', builtin: true, state: 'on', tools: [tool('wk_kit_history', 'Read')] },
  { id: 'tips', title: 'tips', version: '0.1.0', addon: 'WxTips', builtin: false, state: 'on',
    tools: [tool('wk_tips_show', 'Change'), tool('wk_tips_list', 'Read')] }] } };
const settle = async () => { for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r)); };
const last = () => st.said[st.said.length - 1];
const cur = (id) => { st.xpSelect('l:' + id); return st.lxCur; };
const pill = (r) => { const s = st.lxState(r); return [s.key, s.cls, s.text]; };

(async () => {
  // the state: how the game has it
  out.states = {};
  for (const s of ['unknown', 'offline', 'restart', 'missing', 'disabled', 'off', 'installed', 'on', 'later']) {
    out.states[s] = pill(gm('x', s));
  }
  out.absent = [['missing'], ['differs'], ['foreign'], ['current', 'broken'], ['current'], []].map((states) =>
    pill(gm('x', 'absent', { addons: states.map((s, i) => addon('A' + i, s)) })));
  out.noGame = pill(gm('x', 'on', { game: null }));
  out.first = [pill(gm('x', 'on', { enabled: false })), pill(gm('x', 'on', { error: 'no addons folder' })),
               pill(gm('x', 'on', { approval: 'needed' })), pill(gm('x', 'on', { approval: 'needed', allowed: true })),
               pill(gm('x', 'on', { unmet: [{ what: 'workshop', needs: '9.0', has: '0.9.13' }] }))];
  out.pills = [st.lxPill(gm('x', 'on')), st.lxPill(gm('x', 'offline')), st.lxPill(gm('x', 'restart'))];

  // what is left for the player
  out.player = {};
  for (const a of ['approve', 'start_game', 'restart_game', 'enable_addon', 'turn_on', 'something_new']) {
    out.player[a] = st.lxPlayer(gm('tips', 'off', { player: player(a) }));
  }
  out.playerNone = [st.lxPlayer(gm('tips', 'on')), st.lxPlayer(null), st.lxPlayer(row('duo')),
                    st.lxPlayer(Object.assign({}, row('duo'), { player: player('restart_game') }))];

  // where it runs
  out.where = rows.map((r) => [r.id, st.lxGameOnly(r), st.lxWhere(r), st.lxWhereTip(r)]);

  // the list and the detail: one row with what the game runs from its addon
  out.list = st.xpInstalled.filter((r) => r.key.startsWith('l:')).map((r) => [r.key, r.where, r.pill && r.pill.text, r.count]);
  out.noRowOfItsOwn = st.xpInstalled.some((r) => r.key === 'm:tips');
  const sel = (id) => { cur(id); const s = st.xpSel;
    return { kind: s.kind, tag: s.tag, mod: !!s.mod, tools: (s.tools || []).map((x) => x.name),
             caps: st.lxCapsLine(st.lxCur, s.mod), hint: st.lxCapsHint(st.lxCur, s.mod) }; };
  out.tips = sel('tips');
  out.fresh = sel('fresh');
  out.duo = sel('duo');
  out.solo = sel('solo');
  out.soloCaps = [st.lxCapsLine(Object.assign({}, row('solo'), { capabilities: ['solo.a', 'solo.b'] }), null),
                  st.lxCapsHint(Object.assign({}, row('solo'), { capabilities: ['solo.a'] }), null)];

  // no log: the tab falls back
  cur('tips');
  st.xp.tab = 'logs';
  out.tabs = [st.xpTab];
  st.xp.tab = 'caps';
  out.tabs.push(st.xpTab);
  cur('duo');
  st.xp.tab = 'logs';
  out.tabs.push(st.xpTab);
  st.xp.tab = 'info';

  // the approval: what the yes allows
  out.approval = ['asked', 'edited', 'duo'].map((id) => { const r = row(id);
    return [st.lxUnapproved(r), st.lxApprovalTitle(r), st.lxApproveHint(r), st.lxCanApprove(r), st.lxCommand(r)]; });
  out.approvalChanged = st.lxApprovalTitle(Object.assign({}, row('duo'), { approval: 'changed' }));
  out.askedPill = pill(row('asked'));
  out.askedPlayer = st.lxPlayer(row('asked'));

  // 载入: in the game at once
  cur('tips');
  calls.length = 0;
  await st.lxAct(st.lxCur, 'load');
  out.loaded = { posts: calls.filter((c) => c[1]), said: last(), note: st.lx.note, check: st.lxCheck && st.lxCheck.summary };
  answer = { row: copyOf(row('tips')), check: { ok: true, problems: [], capabilities: [] },
             loaded: [{ folder: 'WxTips', ok: false, error: 'Tips.lua:3: attempt to call a nil value', check: null },
                      { folder: 'WxTipsMore', ok: true, error: null, check: null },
                      { folder: 'WxTipsSkin', ok: false, error: null, check: null }] };
  st.xp.tab = 'caps';
  await st.lxAct(st.lxCur, 'load');
  out.loadRefused = { said: last(), note: copyOf(st.lx.note), tab: st.xp.tab };
  answer = apiErr(422, 'check_failed', 'extension_load tips: nothing installed, WxTips/Tips.lua:12: unexpected symbol near }', {
    addons: [{ folder: 'WxTips', errors: [{ code: 'global', file: 'Core.lua', line: 2, message: 'a global', hint: '' },
                                           { code: 'syntax', file: 'Tips.lua', line: 12, message: "unexpected symbol near '}'", hint: 'fix it' }],
               warnings: [] }] });
  const saidBefore = st.said.length;
  await st.lxAct(st.lxCur, 'load');
  out.loadSyntax = { note: copyOf(st.lx.note), said: st.said.length - saidBefore };
  answer = apiErr(422, 'check_failed', 'extension_load tips: nothing installed', {});
  await st.lxAct(st.lxCur, 'load');
  out.loadSyntaxBare = st.lx.note.text;
  answer = apiErr(403, 'needs_approval', 'tips: has to be allowed first', { player: player('approve') });
  await st.lxAct(st.lxCur, 'load');
  out.loadAsks = [last(), st.lx.ask, st.lxNeeds(st.lxCur), st.lx.note];
  st.lx.ask = '';

  // 检查: the Lua findings are its problems
  answer = { ok: false, problems: [{ level: 'warning', where: 'extension.json', message: 'no title', hint: 'add one' }],
             capabilities: [], where: 'game',
             addons: [{ folder: 'WxTips', path: 'D:\\ext\\tips\\addons\\WxTips', ok: false,
                        errors: [{ file: 'Tips.lua', line: 12, code: 'syntax', message: "unexpected symbol near '}'", hint: 'fix it' }],
                        warnings: [{ file: 'Core.lua', line: 2, code: 'global', message: 'a global', hint: '' },
                                   { file: '', line: 0, code: 'toc', message: '', hint: '' }] },
                      { folder: 'WxTipsMore', path: 'D:\\ext\\tips\\addons\\WxTipsMore', ok: true, errors: [], warnings: [] }] };
  st.xp.tab = 'caps';
  await st.lxAct(st.lxCur, 'check');
  out.checked = { summary: st.lxCheck.summary, ok: st.lxCheck.ok, problems: copyOf(st.lxCheck.problems), tab: st.xp.tab };
  answer = { ok: true, problems: [], capabilities: [], where: 'game', addons: [{ folder: 'WxTips', ok: true, errors: [], warnings: [] }] };
  await st.lxAct(st.lxCur, 'check');
  out.checkedFine = st.lxCheck.summary;
  cur('duo');                                 // an ordinary one counts its capabilities still, and has no addons rows
  answer = { ok: true, problems: [], capabilities: ['duo.say'] };
  await st.lxAct(st.lxCur, 'check');
  out.checkedDuo = st.lxCheck.summary;

  // 原因: a row in the game only at rest has none (its why only says where it runs)
  out.reasons = [st.lxReason({ state: 'game', why: 'it is in the game only' }), st.lxReason({ state: 'exited', why: 'exit code 1' }),
                 st.lxReason({ state: 'stopped', why: null }), st.lxReason(null)];

  // the first line for the agent
  out.prompts = [st.lxPrompt(row('tips')), st.lxPrompt(row('duo')), st.lxPrompt(row('solo'))];

  // in English
  globalThis.Alpine = { store: () => ({ v: 'en' }) };
  st.lang = 'en';
  out.english = {
    states: ['unknown', 'offline', 'restart', 'missing', 'disabled', 'off', 'installed', 'on'].map((s) => st.lxState(gm('x', s)).text),
    player: ['start_game', 'restart_game', 'enable_addon', 'turn_on'].map((a) => st.lxPlayer(gm('tips', 'off', { player: player(a) }))),
    where: [st.lxWhere(row('tips')), st.lxWhere(row('duo')), st.lxWhere(row('solo')), st.lxWhereTip(row('tips'))],
    approval: [st.lxApprovalTitle(row('edited')), st.lxApproveHint(row('asked'))],
    caps: st.lxCapsLine(row('fresh'), null),
    prompt: st.lxPrompt(row('tips')),
    idea: st.lxIdeaPrompt(),
  };
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class GameOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((STATIC_DIR / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_the_state_is_the_games(self):
        self.assertEqual(self.out["states"], {
            "unknown": ["game-unknown", "warn", "游戏目录未知"], "offline": ["game-offline", "", "游戏未连接"],
            "restart": ["game-restart", "warn", "待重启游戏"], "missing": ["game-missing", "warn", "游戏中未加载"],
            "disabled": ["game-disabled", "warn", "插件未启用"], "off": ["game-off", "", "未启用"],
            "installed": ["game-installed", "ok", "已安装"], "on": ["game-on", "ok", "运行中"],
            "later": ["game-later", "", "—"]})                               # a state of a later daemon: no words, no colour
        self.assertEqual(self.out["noGame"], ["game-unknown", "warn", "游戏目录未知"])
        self.assertEqual(self.out["pills"], ["ok", "off", "warn"])

    def test_not_in_the_game_yet(self):
        """absent: the first of its addons that is not in the game as it is here says why"""
        self.assertEqual(self.out["absent"], [
            ["game-absent", "warn", "未安装"], ["game-absent", "warn", "有更新"], ["game-absent", "bad", "同名插件已存在"],
            ["game-absent", "bad", "文件缺失"], ["game-absent", "warn", "未安装"], ["game-absent", "warn", "未安装"]])

    def test_what_comes_before_the_game(self):
        off, invalid, asks, allowed, unmet = self.out["first"]
        self.assertEqual(off, ["off", "", "已停用"])
        self.assertEqual(invalid, ["invalid", "bad", "清单有误"])
        self.assertEqual(asks, ["approval", "warn", "待批准"])
        self.assertEqual(allowed, ["game-on", "ok", "运行中"])
        self.assertEqual(unmet, ["unmet", "bad", "版本不足"])

    def test_what_is_left_for_the_player(self):
        p = self.out["player"]
        self.assertEqual(p["approve"], "")                                # the card asks
        self.assertEqual(p["start_game"], "请启动游戏并进入世界")
        self.assertEqual(p["restart_game"], "请重启游戏：新插件 WxTips 重启后才会载入")
        self.assertEqual(p["enable_addon"], "请在游戏的插件列表中启用 WxTips，然后重载界面")
        self.assertEqual(p["turn_on"], "请在游戏中输入 /wk，开启此扩展")
        self.assertEqual(p["something_new"], "the daemon says: something_new")   # a later daemon's: its own words
        none, nobody, waiting, restart = self.out["playerNone"]
        self.assertEqual((none, nobody, waiting), ("", "", ""))            # a program's game half only waits for the game
        self.assertEqual(restart, "请重启游戏：新插件 WxDuo 重启后才会载入")

    def test_where_it_runs(self):
        rows = {r[0]: r[1:] for r in self.out["where"]}
        self.assertEqual(rows["tips"], [True, "游戏", "只含游戏插件，在游戏中运行，没有本机程序"])
        self.assertEqual(rows["duo"], [False, "游戏 · 本机", "包含游戏插件与本机程序：游戏插件在游戏中运行，程序在这台电脑上运行"])
        self.assertEqual(rows["solo"], [False, "本机", "在这台电脑上运行，不需要游戏"])
        self.assertEqual(rows["old"][:2], [False, "游戏 · 本机"])           # a row that does not say: by what it brings

    def test_one_row_with_what_the_game_runs(self):
        rows = {r[0]: r[1:] for r in self.out["list"]}
        self.assertEqual(rows["l:tips"], ["游戏", "运行中", 2])
        self.assertEqual(rows["l:fresh"][:2], ["游戏", "待重启游戏"])
        self.assertEqual(rows["l:asked"][:2], ["游戏", "待批准"])
        self.assertFalse(self.out["noRowOfItsOwn"])
        self.assertEqual(self.out["tips"], {"kind": "local", "tag": "游戏", "mod": True, "tools": ["wk_tips_show", "wk_tips_list"],
                                            "caps": "2 个", "hint": ""})
        self.assertEqual(self.out["fresh"], {"kind": "local", "tag": "游戏", "mod": False, "tools": [],
                                             "caps": "游戏中载入后可查看", "hint": "游戏中载入后可查看"})
        self.assertEqual((self.out["duo"]["caps"], self.out["duo"]["hint"]),
                         ("1 个", "每个能力是一个 Agent 工具，在这台电脑上运行，不需要游戏"))
        self.assertEqual((self.out["solo"]["caps"], self.out["solo"]["hint"]), ("运行后可查看", "运行后可查看"))
        self.assertEqual(self.out["soloCaps"], ["2 个", "每个能力是一个 Agent 工具，在这台电脑上运行，不需要游戏"])

    def test_no_log(self):
        self.assertEqual(self.out["tabs"], ["info", "caps", "logs"])

    def test_the_approval(self):
        asked, edited, duo = self.out["approval"]
        self.assertEqual(asked, [True, "需要批准", "确认后，无限工坊将把此扩展的游戏插件安装到游戏。", True, "—"])
        self.assertEqual(edited[:3], [True, "扩展内容已变更", "确认后，无限工坊将把此扩展的游戏插件安装到游戏。"])
        self.assertEqual(duo[:3], [False, "需要批准", "确认后，无限工坊将在本机运行此命令。"])
        self.assertEqual(self.out["approvalChanged"], "运行命令已变更")
        self.assertEqual(self.out["askedPill"], ["approval", "warn", "待批准"])
        self.assertEqual(self.out["askedPlayer"], "")

    def test_load(self):
        o = self.out["loaded"]
        self.assertEqual(o["posts"], [["/api/local/load", {"id": "tips"}]])
        self.assertEqual((o["said"], o["note"], o["check"]), ("已载入 tips", None, "检查通过"))      # no 个能力: they are the game's

    def test_load_the_game_refused(self):
        o = self.out["loadRefused"]
        self.assertEqual(o["said"], "tips 在游戏中加载失败")
        self.assertEqual(o["note"], {"bad": True, "text": "游戏中加载失败：WxTips Tips.lua:3: attempt to call a nil value；WxTipsSkin"})
        self.assertEqual(o["tab"], "info")

    def test_load_with_a_syntax_error(self):
        o = self.out["loadSyntax"]
        self.assertEqual(o["note"], {"bad": True, "text": "载入失败：Lua 有语法错误，未安装到游戏：WxTips/Tips.lua:12 unexpected symbol near '}'"})
        self.assertEqual(o["said"], 0)
        self.assertEqual(self.out["loadSyntaxBare"], "载入失败：Lua 有语法错误，未安装到游戏")
        self.assertEqual(self.out["loadAsks"], ["tips 需要批准", "tips", True, None])

    def test_check(self):
        o = self.out["checked"]
        self.assertEqual((o["summary"], o["ok"], o["tab"]), ("1 个错误 · 3 个警告", False, "info"))
        self.assertEqual(o["problems"], [
            {"level": "warning", "where": "extension.json", "message": "no title", "hint": "add one"},
            {"level": "error", "where": "WxTips/Tips.lua:12", "message": "unexpected symbol near '}'", "hint": "fix it"},
            {"level": "warning", "where": "WxTips/Core.lua:2", "message": "a global", "hint": ""},
            {"level": "warning", "where": "WxTips", "message": "toc", "hint": ""}])
        self.assertEqual(self.out["checkedFine"], "检查通过")
        self.assertEqual(self.out["checkedDuo"], "检查通过 · 1 个能力")

    def test_no_reason_at_rest(self):
        self.assertEqual(self.out["reasons"], ["", "exit code 1", "", ""])

    def test_the_first_line(self):
        game, both, local = self.out["prompts"]
        self.assertTrue(game.startswith("请先读 D:\\ext\\tips\\addons\\WxTips\\AGENT.md 和 guide {\"topic\": \"game-extension\"}。"
                                        "然后在扩展 tips 里实现："), game)
        self.assertIn("用 extension_wait 等我完成", game)
        self.assertTrue(both.startswith("请先读 D:\\ext\\duo\\AGENT.md 和 guide {\"topic\": \"local-extension\"}。然后在本机扩展 duo 里实现："), both)
        self.assertIn("游戏插件在 addons\\WxDuo：", both)
        self.assertNotIn("游戏插件在", local)

    def test_in_english(self):
        e = self.out["english"]
        self.assertEqual(e["states"], ["Game folder unknown", "Game not linked", "Restart the game", "Not loaded in the game",
                                       "Addon disabled", "Off", "Installed", "Running"])
        self.assertEqual(e["where"][:3], ["Game", "Game · Local", "Local"])
        for text in e["player"] + e["approval"] + [e["where"][3], e["caps"], e["prompt"], e["idea"]]:
            self.assertTrue(text, e)
            self.assertNotRegex(text, r"[\u4e00-\u9fff]", text)
        self.assertIn("WxTips", e["player"][1])
        self.assertIn('guide {"topic": "game-extension"}', e["prompt"])
        self.assertIn("extension_wait", e["prompt"])
        self.assertIn("guide", e["idea"])


class Page(unittest.TestCase):
    """index.html: the parts of the detail an extension in the game only has not got are hidden by lxGameOnly, the
    card and 概况 name its game addons, the player's line is under the notes"""

    def setUp(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = html.index("<!-- 本机扩展：")
        self.detail = html[start:html.index("<!-- Agent 会话 -->", start)]
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
        self.css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")

    def test_the_approval_card(self):
        d = self.detail
        card = d[d.index('<div class="lx-approve"'):]
        card = card[:card.index('<div class="xp-note')]
        self.assertIn('<span class="muted" x-text="lxApproveHint(lxCur)"></span>', card)
        self.assertIn("x-text=\"lxBusy(lxCur, 'approve') ? t('进行中…') : lxGameOnly(lxCur) ? t('批准') : t('批准运行')\"", card)
        self.assertIn('<dt x-show="!lxGameOnly(lxCur)">命令</dt><dd x-show="!lxGameOnly(lxCur)">'
                      '<code class="lx-cmd selectable" x-text="lxCommand(lxCur)"></code></dd>', card)
        self.assertIn('<dt x-show="lxGameOnly(lxCur)">游戏插件</dt><dd x-show="lxGameOnly(lxCur)" class="mono selectable" '
                      'x-text="(lxCur.addons || []).map((a) => a.folder).join(\'  \')"></dd>', card)

    def test_the_players_line(self):
        d = self.detail
        line = '<div class="xp-note" x-show="lxPlayer(lxCur) && !lxNeeds(lxCur)" x-text="lxPlayer(lxCur)"></div>'
        self.assertEqual(d.count(line), 1)
        self.assertLess(d.index('x-text="lx.note.text"') if 'x-text="lx.note.text"' in d else 0, d.index(line))

    def test_no_process(self):
        d = self.detail
        self.assertIn("<button class=\"tab\" x-show=\"!lxGameOnly(lxCur)\" :class=\"{ on: xpTab === 'logs' }\" "
                      "@click=\"lxTab('logs')\">日志</button>", d)
        self.assertIn('<dt x-show="!lxGameOnly(lxCur)">启动方式</dt><dd x-show="!lxGameOnly(lxCur)" x-text="lxStart(lxCur)"></dd>', d)
        self.assertIn('<dt>能力</dt><dd x-text="lxCapsLine(lxCur, xpSel.mod)"></dd>', d)
        self.assertIn('<h3 class="xp-h" x-show="xpSel.mod && !lxGameOnly(lxCur)">本机<span class="muted">在这台电脑上运行，不需要游戏</span></h3>', d)
        self.assertIn('<p class="xp-toolhint" x-show="lxCapsHint(lxCur, xpSel.mod)" x-text="lxCapsHint(lxCur, xpSel.mod)"></p>', d)

    def test_no_reason_at_rest(self):
        self.assertIn('<dt x-show="lxReason(lxCur)">原因</dt>', self.detail)

    def test_calls_exist(self):
        for name in ("lxReason", "lxGameOnly", "lxWhereOf", "lxWhereName", "lxWhereHint", "lxWhere", "lxWhereTip", "lxGameState", "lxPlayer",
                     "lxCapsLine", "lxCapsHint", "lxApprovalTitle", "lxApproveHint"):
            self.assertRegex(self.js, r"(?m)^\s*%s\s*\(" % name, name)
        for name in ("lxLint", "lxSyntax"):
            self.assertRegex(self.js, r"(?m)^function %s\(" % name, name)
        self.assertIn("\n.lx-where-hint {", self.css)

    def test_the_words_have_their_english(self):
        for word in ("待重启游戏", "插件未启用", "游戏中载入后可查看", "请启动游戏并进入世界", "请重启游戏：新插件 {addon} 重启后才会载入",
                     "请在游戏的插件列表中启用 {addon}，然后重载界面", "请在游戏中输入 /wk，开启此扩展", "批准", "扩展内容已变更",
                     "确认后，无限工坊将把此扩展的游戏插件安装到游戏。", "Lua 有语法错误，未安装到游戏", "游戏中加载失败",
                     "{name} 在游戏中加载失败", "只含游戏插件，在游戏中运行，没有本机程序", "游戏未连接", "游戏中未加载", "未启用", "已安装"):
            self.assertRegex(self.i18n, r"\n\s*'%s':" % re.escape(word), word)


if __name__ == "__main__":
    unittest.main()
