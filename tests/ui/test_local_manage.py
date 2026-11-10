"""The 本机扩展 group as a manager on the 扩展 page: app.js run under Node (skipped without it) against a stand-in for
/api/local* in the shapes of extensions-home (sections 4, 7, 10). A row says where its folder is (目录 / 链接), what it
requires and is not there (版本不足 when this workshop is too old for it), what it brings for the game with each addon's
state and 安装 / 更新 where they can go in (重启游戏后生效 once new ones did, by the button, a load or the player's yes);
添加 links a folder or installs a package picked through the shell, or typed where there is none, a refused path back
in the dialog with why; 新建扩展 posts the id with the title and the game addon given; 打包 shows the package's file;
移除 asks once more in words that say what happens to the folder and the game addons; a folder opens through the shell,
and where it cannot its path is shown. The page has the menus, the dialogs and the words, in both languages; the shell's
open_folder and pick_file: tests/ui/test_shell.py; the stand-in's routes: tests/ui/test_fake_manage.py."""
import json
import re
import subprocess
import unittest

from wuxianworkshop.ui import STATIC_DIR

from tests.ui.test_local import NODE, STUBS

HARNESS = r"""
const out = {};
const T0 = 1762750000;
const HOME = 'C:\\home\\extensions';
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
const base = { version: '0.1.0', enabled: true, start: 'use', state: 'stopped', pid: null, started: null, exit_code: null,
               error: null, capabilities: [], sources: [], options: [], apps: [], skills: [], adds: {}, approval: 'ok',
               since: null, why: null, origin: 'home', author: null, homepage: null, requires: {}, unmet: [], addons: [] };
const mk = (id, title, more) => Object.assign({}, base, { id, title, path: HOME + '\\' + id, command: ['python', 'main.py'] }, more);
const game = (folder, state) => ({ folder, version: '1.0.0', state });
function reset() {
  rows = [
    mk('herald', '传令', { version: '0.2.0', author: 'Tim', homepage: 'https://example.com/herald',
                           addons: [game('WuxianHerald', 'missing'), game('WuxianHeraldSkin', 'differs')] }),
    mk('relay', '中继', { origin: 'linked', path: 'D:\\Projects\\relay', requires: { workshop: '0.9.14', kit: '0.7' },
                          unmet: [{ what: 'workshop', needs: '0.9.14', has: '0.9.13' }, { what: 'kit', needs: '0.7', has: '0.6.0' }],
                          error: 'relay requires workshop 0.9.14 (this is 0.9.13)' }),
    mk('kitty', '只缺框架', { unmet: [{ what: 'kit', needs: '0.7', has: null }] }),
    mk('greeter', '迎宾', { approval: 'needed', addons: [game('WuxianGreeter', 'missing')] }),
    mk('plain', '普通', {}),
    mk('skin', '皮肤', { origin: 'linked', path: 'D:\\Projects\\skin', addons: [game('WuxianSkin', 'differs')] }),
    mk('clash', '撞名', { addons: [game('Clash', 'foreign'), game('ClashMore', 'missing')] }),
    mk('lost', '不知道', { addons: [game('Lost', 'unknown')] }),
  ];
}
const row = (id) => rows.find((r) => r.id === id);
async function api(path, opts) {
  const body = opts && opts.body;
  calls.push([path, body || null]);
  const action = path.startsWith('/api/local/') ? path.slice('/api/local/'.length) : '';
  if (refuse[action]) throw apiErr(...refuse[action]);
  if (path === '/api/extensions') return { addons: [], catalog: null, addons_dir: null };
  if (path.startsWith('/api/agent_access')) return { kit: null, kit_state: 'absent', kit_addon: null, servers: [] };
  if (path === '/api/kit/apps') return { online: true, apps: [] };
  if (path === '/api/local') return { revision: 1, extensions: rows.map(copyOf), problem: null, home: HOME };
  if (path === '/api/local/manifest') return { revision: 1, extensions: [] };
  if (action === 'add') {
    const p = body.path, zip = /\.zip$/i.test(p);
    if (/bad/.test(p)) throw apiErr(400, zip ? 'bad_package' : 'bad_manifest', 'no extension.json in it');
    const id = p.split(/[\\/]/).pop().replace(/[-.].*$/, '');
    const had = row(id);
    rows = rows.filter((r) => r.id !== id);
    const r = zip ? mk(id, '', { approval: 'needed' }) : mk(id, '', { origin: 'linked', path: p, approval: 'needed' });
    rows.push(r);
    return zip ? Object.assign(copyOf(r), { package: { file: p, replaced: had ? HOME + '\\.trash\\' + id : null } }) : copyOf(r);
  }
  if (action === 'new') {
    if (row(body.id)) throw apiErr(409, 'extension_exists', body.id + ' is registered already');
    const where = body.where || (body.addon ? 'both' : 'local');     // as the daemon: the addon's name is the id's when none is given
    const addon = body.addon || (where === 'local' ? '' : body.id.replace(/(^|_)([a-z0-9])/g, (m, a, c) => c.toUpperCase()));
    const more = { where, approval: 'needed', addons: addon ? [game(addon, 'missing')] : [] };
    if (where === 'game') {                    // no process: nothing to approve, the game has not got it yet
      Object.assign(more, { approval: 'ok', command: null, state: 'game', game: { extensions: [body.id], state: 'absent' }, player: null });
    }
    const r = mk(body.id, body.title || '', more);
    rows.push(r);
    return { row: copyOf(r), files: where === 'game' ? ['extension.json', 'addons'] : ['extension.json', 'main.py'], guide: 'read AGENT.md' };
  }
  if (action === 'remove') {
    const r = row(body.id);
    rows = rows.filter((x) => x.id !== body.id);
    return { removed: true, id: r.id, path: r.path, kept: r.origin === 'linked' ? null : HOME + '\\.trash\\' + r.id,
             addons: r.addons.map((a) => ({ folder: a.folder, removed: true, kept: 3 })) };
  }
  if (action === 'pack') {
    const r = row(body.id);
    return { id: r.id, version: r.version, file: 'C:\\home\\packages\\' + r.id + '-' + r.version + '.zip',
             size: body.id === 'plain' ? 3 * 1048576 : 20480, files: 7, sha256: 'ab'.repeat(32), skipped: ['logs'] };
  }
  const put = (r) => {
    const was = r.addons.map((a) => a.state);
    r.addons.forEach((a) => { a.state = 'current'; });
    return { addons: r.addons.map((a, i) => ({ folder: a.folder, state: was[i] === 'current' ? 'current' : 'installed',
                                               was: was[i], version: a.version, kept: null })),
             restart: was.includes('missing'), hint: 'restart the game for the new ones' };
  };
  if (action === 'install') {
    const r = row(body.id);
    if (r.approval !== 'ok') throw apiErr(403, 'needs_approval', r.id + ': its command has to be allowed first');
    return Object.assign({ id: r.id }, put(r));
  }
  if (action === 'load') {
    const r = row(body.id);
    const more = r.addons.some((a) => a.state === 'missing' || a.state === 'differs') ? put(r) : {};
    Object.assign(r, { state: 'running', pid: 7, started: T0 });
    return Object.assign({ row: copyOf(r), check: { ok: true, problems: [], capabilities: [] } }, more);
  }
  if (action === 'approve') {
    const r = row(body.id);
    r.approval = 'ok';
    r.addons.forEach((a) => { a.state = 'current'; });
    return copyOf(r);
  }
  throw new Error('unexpected ' + path);
}
const picks = { folder: '', file: '', asked: [] };
const opened = [];
let opens = true;
const shell = {
  pick_folder: async (start) => { picks.asked.push(['folder', start]); return picks.folder; },
  pick_file: async (kind) => { picks.asked.push(['file', kind]); return picks.file; },
  open_folder: async (p) => { opened.push(p); return opens; },
};
function withShell(st, on) {
  if (on) globalThis.pywebview = { api: shell }; else delete globalThis.pywebview;
  Object.assign(st, { canPick: !!on, canPickFile: !!on, canOpen: !!on });
}
function make() {
  const st = appState();
  st.token = 't';
  st.$nextTick = (f) => f();
  st.$refs = { lxout: { scrollTop: 0, scrollHeight: 0, clientHeight: 0 } };
  st.said = [];
  st.say = (x) => st.said.push(x);
  st.copy = () => {};
  st.api = api;
  return st;
}
const settle = async () => { for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r)); };
const posts = () => calls.filter((c) => c[1]).map((c) => [c[0], c[1]]);
const last = (st) => st.said[st.said.length - 1];
const cur = (st, id) => { st.xpSelect('l:' + id); return st.lxCur; };

(async () => {
  reset();
  const st = make();
  st.now = (T0 + 100) * 1000;
  withShell(st, true);
  st.go('extensions');
  await settle();

  // the rows: where the folder is, what is required and not there, the state
  out.home = st.lx.home;
  st.onLocal({ revision: 1, extensions: rows.map(copyOf) });   // the stream's rows come without the folder: it stays
  out.homeKept = st.lx.home;
  out.origin = st.lx.list.map((r) => [r.id, st.lxOrigin(r)]).concat([['', st.lxOrigin(st.lxCur)]]);
  out.unmet = ['relay', 'kitty', 'plain'].map((id) => [id, st.lxUnmet(row(id)), st.lxBlocked(row(id)),
                                                      st.lxState(row(id)).key, st.lxState(row(id)).text, st.lxPill(row(id))]);
  out.line = st.lxLine(row('herald'));

  // the game addons: each one's state, what the button does, what the player's yes installs as well
  out.addonStates = ['missing', 'current', 'differs', 'foreign', 'unknown', 'broken', 'odd']
    .map((s) => [s, st.lxAddonState({ state: s }), st.lxAddonPill({ state: s })]);
  out.gameAct = st.lx.list.map((r) => [r.id, st.lxGameAct(r), st.lxWillInstall(r)]);
  out.gameActCurrent = st.lxGameAct({ id: 'x', addons: [game('A', 'current')] });
  out.gameActBroken = st.lxGameAct({ id: 'x', addons: [game('A', 'broken'), game('B', 'missing')] });

  // 安装: new ones need the game restarted; 更新 alone does not
  let r = cur(st, 'herald');
  calls.length = 0;
  await st.lxInstall(r);
  out.install = { posts: posts(), reread: calls.map((c) => c[0]).includes('/api/local'), said: last(st),
                  restart: st.lxRestart(st.lxCur), states: st.lxCur.addons.map((a) => a.state), act: st.lxGameAct(st.lxCur),
                  note: st.lx.note, busy: st.lx.busy };
  r = cur(st, 'skin');
  await st.lxInstall(r);
  out.update = { said: last(st), restart: st.lxRestart(st.lxCur) };
  out.restartKept = st.lxRestart(cur(st, 'herald'));           // it stays said while the page lives
  // … not before its command is allowed: the same 批准运行, no error
  r = cur(st, 'greeter');
  await st.lxInstall(r);
  out.installAsk = { ask: st.lx.ask, needs: st.lxNeeds(st.lxCur), said: last(st), note: st.lx.note };
  // the player's yes installs them as well
  await st.lxApprove(st.lxCur);
  out.approved = { restart: st.lxRestart(st.lxCur), states: st.lxCur.addons.map((a) => a.state), said: last(st) };
  // a failure is said
  refuse.install = [503, 'no_game_folder', 'the game folder is not known'];
  await st.lxInstall(cur(st, 'herald'));
  out.installFailed = st.lx.note;
  delete refuse.install;
  // a load that installed them says so too
  reset();
  await st.loadLocal();
  st.lx.game = {};
  await st.lxAct(cur(st, 'herald'), 'load');
  out.loaded = { restart: st.lxRestart(st.lxCur), said: last(st) };
  await st.lxAct(cur(st, 'plain'), 'load');
  out.loadedPlain = st.lxRestart(st.lxCur);

  // 打包: the file, its folder, how much
  reset();
  await st.loadLocal();
  r = cur(st, 'herald');
  st.lx.menu = 'more';
  calls.length = 0;
  await st.lxPack(r);
  out.pack = { posts: posts(), packed: st.lx.packed, menu: st.lx.menu, busy: st.lx.busy };
  await st.lxPack(cur(st, 'plain'));
  out.packBig = st.lx.packed.line;
  out.packGone = (cur(st, 'herald'), st.lx.packed);             // another one selected: not shown any more
  refuse.pack = [400, 'bad_manifest', 'extension.json: version is missing'];
  await st.lxPack(cur(st, 'plain'));
  out.packFailed = [st.lx.note, st.lx.packed];
  delete refuse.pack;

  // 移除: asked once more, in words that say what happens
  out.dropLines = ['plain', 'herald', 'relay', 'skin'].map((id) => [id, st.lxDropLine(row(id))]);
  r = cur(st, 'herald');
  st.lx.menu = 'more';
  st.lxDrop(r);
  out.drop = [st.lx.drop, st.lx.menu];
  calls.length = 0;
  await st.lxRemove(r);
  out.remove = { posts: posts(), ids: st.lx.list.map((x) => x.id), sel: st.xp.sel, said: last(st), drop: st.lx.drop };
  r = cur(st, 'skin');
  st.lxDrop(r);
  await st.lxRemove(r);
  out.unlink = { ids: st.lx.list.map((x) => x.id), said: last(st) };
  refuse.remove = [409, 'folder_busy', 'the folder is in use'];
  r = cur(st, 'plain');
  st.lxDrop(r);
  await st.lxRemove(r);
  out.removeFailed = { note: st.lx.note, drop: st.lx.drop, sel: st.xp.sel, there: !!st.selLx };
  delete refuse.remove;
  st.lxDrop(st.lxCur);
  const asked = st.lx.drop;
  cur(st, 'relay');
  out.dropForgotten = [asked, st.lx.drop];                      // another one selected: not asked any more

  // a folder: opened by the shell; where it cannot (not one the page lists, no shell), its path is shown
  st.lx.menu = 'more';
  await st.lxOpen('D:\\Projects\\relay');
  out.open = { opened: opened.slice(), shown: st.lxp.open, menu: st.lx.menu };
  await st.lxOpenHome();
  out.openHome = opened[opened.length - 1];
  opens = false;
  await st.lxOpen('C:\\elsewhere');
  out.openRefused = copyOf(st.lxp);
  st.lxp.open = false;
  withShell(st, false);
  opened.length = 0;
  await st.lxOpenHome();
  out.openNoShell = [copyOf(st.lxp), opened.length];
  st.lxp.open = false;
  await st.lxOpen('');
  out.openNothing = st.lxp.open;
  withShell(st, true);
  opens = true;

  // 添加 · 链接文件夹: picked through the shell
  reset();
  await st.loadLocal();
  st.xpSelect('kit');
  st.lx.menu = 'add';
  picks.folder = 'D:\\Work\\mapper';
  calls.length = 0;
  await st.lxAddFolder();
  out.addFolder = { asked: picks.asked.slice(), posts: posts(), sel: st.xp.sel, origin: st.lxOrigin(st.lxCur), said: last(st),
                    menu: st.lx.menu, dialog: st.lxa.open };
  picks.folder = '';                                            // the dialog cancelled: nothing
  calls.length = 0;
  await st.lxAddFolder();
  out.addCancelled = posts().length;
  picks.folder = 'D:\\Work\\bad';                               // refused: the dialog, with the path and why
  await st.lxAddFolder();
  out.addRefused = copyOf(st.lxa);
  st.lxa.path = ' D:\\Work\\notes ';                            // … changed and tried again
  calls.length = 0;
  await st.lxAddSubmit();
  out.addRetried = { posts: posts(), dialog: copyOf(st.lxa), sel: st.xp.sel };
  // 添加 · 导入扩展包
  picks.asked.length = 0;
  picks.file = 'D:\\Downloads\\scout-1.0.0.zip';
  calls.length = 0;
  await st.lxAddPackage();
  out.addPackage = { asked: picks.asked.slice(), posts: posts(), sel: st.xp.sel, origin: st.lxOrigin(st.lxCur), said: last(st) };
  await st.lxAddPackage();
  out.addPackageAgain = last(st);                               // the same id once more: it replaced the one there
  picks.file = 'D:\\Downloads\\bad-1.0.0.zip';
  await st.lxAddPackage();
  out.addPackageRefused = copyOf(st.lxa);
  st.lxa.open = false;
  // … without a shell: the path is typed
  withShell(st, false);
  picks.asked.length = 0;
  await st.lxAddFolder();
  const typedFolder = copyOf(st.lxa);
  st.lxa.open = false;
  await st.lxAddPackage();
  const typedZip = copyOf(st.lxa);
  calls.length = 0;
  await st.lxAddSubmit();                                       // nothing typed: nothing posted
  const none = posts().length;
  st.lxa.path = 'D:\\Downloads\\ferry-0.3.0.zip';
  await st.lxAddSubmit();
  out.addTyped = { folder: typedFolder, zip: typedZip, none, asked: picks.asked.length, posts: posts(), sel: st.xp.sel,
                   dialog: st.lxa.open };
  withShell(st, true);

  // 新建扩展: the id, with the title when given; where it runs (本机 unless chosen) and the game addon's name with it
  st.lx.menu = 'add';
  st.lxn.error = 'old';
  st.lxNewOpen();
  out.newOpen = [copyOf(st.lxn), st.lx.menu];
  Object.assign(st.lxn, { id: ' my_tool ', title: '', where: 'both', addon: ' MyTool ' });
  calls.length = 0;
  await st.lxNew();
  out.created = { posts: posts(), sel: st.xp.sel, said: last(st), dialog: st.lxn.open, result: copyOf(st.lxn.result),
                  prompt: st.lxPrompt(st.lxn.result), detailPrompt: st.lxPrompt(st.lxCur),
                  addons: st.lxCur.addons.map((a) => [a.folder, st.lxAddonState(a)]), will: st.lxWillInstall(st.lxCur) };
  st.lxNewOpen();
  Object.assign(st.lxn, { id: 'named', title: ' 有名字 ', addon: 'Typed' });   // 本机: the name typed before is not sent
  calls.length = 0;
  await st.lxNew();
  out.createdNamed = [posts(), st.lxTitle(st.lxCur), st.lxn.result.where, st.lxn.result.addon];
  st.lxNewOpen();
  Object.assign(st.lxn, { id: 'pure', where: 'game', addon: '' });
  calls.length = 0;
  await st.lxNew();
  out.createdGame = { posts: posts(), result: copyOf(st.lxn.result), prompt: st.lxPrompt(st.lxn.result),
                      detailPrompt: st.lxPrompt(st.lxCur), where: st.lxWhere(st.lxCur), pill: st.lxState(st.lxCur).text,
                      after: st.lxn.where };
  st.lxNewOpen();
  Object.assign(st.lxn, { id: 'odd', where: 'elsewhere', addon: 'Odd' });      // not a place: 本机
  calls.length = 0;
  await st.lxNew();
  out.createdOdd = posts();
  out.whereHints = ['game', 'local', 'both'].map((w) => st.lxWhereHint(w));
  st.lxNewOpen();
  st.lxn.id = 'my_tool';
  await st.lxNew();
  out.createFailed = copyOf(st.lxn);
  st.lxn.id = '  ';
  calls.length = 0;
  await st.lxNew();
  out.createNothing = posts().length;
  st.lxn.open = false;

  // the menus: one at a time
  st.lx.menu = '';
  st.lxMenu('add');
  const m1 = st.lx.menu;
  st.lxMenu('more');
  const m2 = st.lx.menu;
  st.lxMenuClose('add');                                        // a click outside the other one
  const m3 = st.lx.menu;
  st.lxMenu('more');
  out.menus = [m1, m2, m3, st.lx.menu];
  st.lx.menu = 'more';
  cur(st, 'plain');
  out.menuForgotten = st.lx.menu;

  // in English
  globalThis.Alpine = { store: () => ({ v: 'en' }) };
  st.lang = 'en';
  reset();
  out.english = [st.lxOrigin(row('herald')), st.lxOrigin(row('relay')), st.lxUnmet(row('relay')), st.lxState(row('relay')).text,
                 st.lxDropLine(row('herald')), st.lxDropLine(row('relay')), st.lxWillInstall(row('herald'))]
    .concat(['missing', 'current', 'differs', 'foreign', 'unknown', 'broken'].map((s) => st.lxAddonState({ state: s })));
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class Manage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((STATIC_DIR / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_where_the_folder_is(self):
        o = self.out
        self.assertEqual(o["home"], "C:\\home\\extensions")
        self.assertEqual(o["homeKept"], "C:\\home\\extensions")
        self.assertEqual(o["origin"], [["herald", "扩展文件夹"], ["relay", "链接"], ["kitty", "扩展文件夹"],
                                       ["greeter", "扩展文件夹"], ["plain", "扩展文件夹"], ["skin", "链接"],
                                       ["clash", "扩展文件夹"], ["lost", "扩展文件夹"], ["", ""]])
        self.assertEqual(o["line"], "herald · 0.2.0")

    def test_what_it_requires(self):
        self.assertEqual(self.out["unmet"], [
            ["relay", "需要无限工坊 0.9.14 以上；需要 WuxianKit 0.7 以上", True, "unmet", "版本不足", "bad"],
            ["kitty", "需要 WuxianKit 0.7 以上", False, "stopped", "未运行", "off"],
            ["plain", "", False, "stopped", "未运行", "off"]])

    def test_the_game_addons(self):
        o = self.out
        self.assertEqual(o["addonStates"], [
            ["missing", "未安装", "warn"], ["current", "已安装", "ok"], ["differs", "有更新", "warn"],
            ["foreign", "同名插件已存在", "bad"], ["unknown", "游戏目录未知", "off"], ["broken", "文件缺失", "bad"],
            ["odd", "—", "off"]])
        self.assertEqual(o["gameAct"], [
            ["herald", "install", "WuxianHerald、WuxianHeraldSkin"], ["relay", "", ""], ["kitty", "", ""],
            ["greeter", "install", "WuxianGreeter"], ["plain", "", ""], ["skin", "update", "WuxianSkin"],
            ["clash", "", ""], ["lost", "", ""]])
        self.assertEqual([o["gameActCurrent"], o["gameActBroken"]], ["", ""])

    def test_install(self):
        o = self.out["install"]
        self.assertEqual(o["posts"], [["/api/local/install", {"id": "herald"}]])
        self.assertTrue(o["reread"])
        self.assertEqual((o["said"], o["restart"], o["states"], o["act"]), ("游戏插件已安装", True, ["current", "current"], ""))
        self.assertEqual((o["note"], o["busy"]), (None, ""))
        self.assertEqual(self.out["update"], {"said": "游戏插件已更新", "restart": False})
        self.assertTrue(self.out["restartKept"])

    def test_install_waits_for_the_approval(self):
        o = self.out["installAsk"]
        self.assertEqual((o["ask"], o["needs"], o["said"], o["note"]), ("greeter", True, "迎宾 需要批准", None))
        self.assertEqual(self.out["approved"], {"restart": True, "states": ["current"], "said": "已批准 迎宾"})

    def test_install_failed(self):
        self.assertEqual(self.out["installFailed"], {"bad": True, "text": "安装失败：未找到游戏目录"})

    def test_a_load_installs_them(self):
        self.assertEqual(self.out["loaded"], {"restart": True, "said": "已载入 传令"})
        self.assertFalse(self.out["loadedPlain"])

    def test_pack(self):
        o = self.out["pack"]
        self.assertEqual(o["posts"], [["/api/local/pack", {"id": "herald"}]])
        self.assertEqual(o["packed"], {"id": "herald", "file": "C:\\home\\packages\\herald-0.2.0.zip",
                                       "folder": "C:\\home\\packages", "line": "7 个文件 · 20 KB"})
        self.assertEqual((o["menu"], o["busy"]), ("", ""))
        self.assertEqual(self.out["packBig"], "7 个文件 · 3.0 MB")
        self.assertIsNone(self.out["packGone"])
        self.assertEqual(self.out["packFailed"], [{"bad": True, "text": "打包失败：清单有误：extension.json: version is missing"}, None])

    def test_remove_says_what_happens(self):
        self.assertEqual(self.out["dropLines"], [
            ["plain", "文件夹移入回收区。"], ["herald", "文件夹移入回收区，游戏插件一并移除。"],
            ["relay", "仅解除链接，文件夹保留。"], ["skin", "仅解除链接，文件夹保留，游戏插件一并移除。"]])

    def test_remove(self):
        o = self.out
        self.assertEqual(o["drop"], ["herald", ""])
        self.assertEqual(o["remove"]["posts"], [["/api/local/remove", {"id": "herald"}]])
        self.assertNotIn("herald", o["remove"]["ids"])
        self.assertEqual((o["remove"]["sel"], o["remove"]["said"], o["remove"]["drop"]),
                         ("kit", "已移除 传令，文件夹已移入回收区", ""))
        self.assertNotIn("skin", o["unlink"]["ids"])
        self.assertEqual(o["unlink"]["said"], "已解除链接 皮肤")
        self.assertEqual(o["removeFailed"], {"note": {"bad": True, "text": "移除失败：文件夹被占用，请关闭占用它的程序后重试"}, "drop": "",
                                             "sel": "l:plain", "there": True})
        self.assertEqual(o["dropForgotten"], ["plain", ""])

    def test_open_a_folder(self):
        o = self.out
        self.assertEqual(o["open"], {"opened": ["D:\\Projects\\relay"], "shown": False, "menu": ""})
        self.assertEqual(o["openHome"], "C:\\home\\extensions")
        self.assertEqual(o["openRefused"], {"open": True, "path": "C:\\elsewhere"})
        self.assertEqual(o["openNoShell"], [{"open": True, "path": "C:\\home\\extensions"}, 0])
        self.assertFalse(o["openNothing"])

    def test_link_a_folder(self):
        o = self.out["addFolder"]
        self.assertEqual(o["asked"], [["folder", ""]])
        self.assertEqual(o["posts"], [["/api/local/add", {"path": "D:\\Work\\mapper"}]])
        self.assertEqual((o["sel"], o["origin"], o["said"], o["menu"], o["dialog"]),
                         ("l:mapper", "链接", "已链接 mapper", "", False))
        self.assertEqual(self.out["addCancelled"], 0)
        self.assertEqual(self.out["addRefused"], {"open": True, "kind": "folder", "path": "D:\\Work\\bad", "busy": False,
                                                  "error": "清单有误：no extension.json in it"})
        o = self.out["addRetried"]
        self.assertEqual(o["posts"], [["/api/local/add", {"path": "D:\\Work\\notes"}]])
        self.assertEqual((o["dialog"]["open"], o["dialog"]["error"], o["sel"]), (False, "", "l:notes"))

    def test_install_a_package(self):
        o = self.out["addPackage"]
        self.assertEqual(o["asked"], [["file", "zip"]])
        self.assertEqual(o["posts"], [["/api/local/add", {"path": "D:\\Downloads\\scout-1.0.0.zip"}]])
        self.assertEqual((o["sel"], o["origin"], o["said"]), ("l:scout", "扩展文件夹", "已导入 scout"))
        self.assertEqual(self.out["addPackageAgain"], "已更新 scout")
        self.assertEqual(self.out["addPackageRefused"], {"open": True, "kind": "zip", "path": "D:\\Downloads\\bad-1.0.0.zip",
                                                         "busy": False, "error": "扩展包无效：no extension.json in it"})

    def test_a_path_typed_without_a_shell(self):
        o = self.out["addTyped"]
        self.assertEqual(o["folder"], {"open": True, "kind": "folder", "path": "", "busy": False, "error": ""})
        self.assertEqual(o["zip"], {"open": True, "kind": "zip", "path": "", "busy": False, "error": ""})
        self.assertEqual((o["none"], o["asked"]), (0, 0))
        self.assertEqual(o["posts"], [["/api/local/add", {"path": "D:\\Downloads\\ferry-0.3.0.zip"}]])
        self.assertEqual((o["sel"], o["dialog"]), ("l:ferry", False))

    def test_new(self):
        o = self.out
        self.assertEqual(o["newOpen"], [{"open": True, "id": "", "title": "", "addon": "", "where": "local", "busy": False,
                                         "error": "", "result": None}, ""])
        c = o["created"]
        self.assertEqual(c["posts"], [["/api/local/new", {"id": "my_tool", "where": "both", "addon": "MyTool"}]])
        self.assertEqual((c["sel"], c["said"], c["dialog"]), ("l:my_tool", "已创建 my_tool", True))   # it shows what next
        r = c["result"]
        self.assertEqual((r["id"], r["addon"], r["where"], r["needs"]), ("my_tool", "MyTool", "both", True))
        self.assertTrue(r["path"])
        self.assertTrue(c["prompt"].startswith("请先读 " + r["path"] + "\\AGENT.md 和 guide {\"topic\": \"local-extension\"}。"),
                        c["prompt"])
        self.assertIn("在本机扩展 my_tool 里实现：（在这里写你想要的功能）", c["prompt"])
        self.assertIn("wk_my_tool_", c["prompt"])
        self.assertIn("游戏插件在 addons\\MyTool：", c["prompt"])
        self.assertEqual(c["detailPrompt"], c["prompt"])        # 更多 · 复制给 Agent 的第一句话 says the same of its row
        self.assertEqual((c["addons"], c["will"]), ([["MyTool", "未安装"]], "MyTool"))
        self.assertEqual(o["createdNamed"], [[["/api/local/new", {"id": "named", "title": "有名字"}]], "有名字", "local", ""])
        self.assertEqual(o["createdOdd"], [["/api/local/new", {"id": "odd"}]])
        self.assertEqual(o["createFailed"], {"open": True, "id": "my_tool", "title": "", "addon": "", "where": "local",
                                             "busy": False, "result": None, "error": "标识已被占用，请先移除同名扩展"})
        self.assertEqual(o["createNothing"], 0)

    def test_new_in_the_game_only(self):
        g = self.out["createdGame"]
        self.assertEqual(g["posts"], [["/api/local/new", {"id": "pure", "where": "game"}]])      # the name is the daemon's
        r = g["result"]
        self.assertEqual((r["id"], r["addon"], r["where"], r["needs"]), ("pure", "Pure", "game", False))
        self.assertTrue(g["prompt"].startswith("请先读 " + r["path"] + "\\addons\\Pure\\AGENT.md 和 guide "
                                               "{\"topic\": \"game-extension\"}。然后在扩展 pure 里实现："), g["prompt"])
        for word in ("extension_load", "wk_pure_", "errors 和 logs", "extension_wait"):
            self.assertIn(word, g["prompt"])
        for word in ("main.py", "extension_logs", "本机扩展"):
            self.assertNotIn(word, g["prompt"])
        self.assertEqual(g["detailPrompt"], g["prompt"])
        self.assertEqual((g["where"], g["pill"], g["after"]), ("游戏", "未安装", "local"))
        self.assertEqual(self.out["whereHints"], ["只含游戏插件，在游戏中运行，没有本机程序",
                                                  "在这台电脑上运行，不需要游戏",
                                                  "包含游戏插件与本机程序：游戏插件在游戏中运行，程序在这台电脑上运行"])

    def test_the_menus(self):
        self.assertEqual(self.out["menus"], ["add", "more", "more", ""])
        self.assertEqual(self.out["menuForgotten"], "")

    def test_in_english(self):
        self.assertEqual(self.out["english"], [
            "Extensions folder", "Linked", "Needs Wuxian Workshop 0.9.14 or later; Needs WuxianKit 0.7 or later", "Needs an update",
            "The folder is moved to the workshop's trash, and its game addons are removed too.",
            "Only the link is removed; the folder stays.", "WuxianHerald, WuxianHeraldSkin",
            "Not installed", "Installed", "Update available", "Another addon has this name", "Game folder unknown",
            "Files missing"])


class Page(unittest.TestCase):
    """index.html: the group's head with 添加 (its menu) and 扩展文件夹, a row's second line, nothing yet with the same three
    ways in, the detail's 更多 with 移除 asked once more, the approval's line for the game, the notes, 概况's new facts,
    the three dialogs; every word has its English; the shell's three calls are looked for"""

    def setUp(self):
        self.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
        self.i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
        start = self.html.index('<span>已添加</span>')
        self.master = self.html[start:self.html.index('<div class="master-foot">', start)]
        start = self.html.index("<section class=\"page\" x-t x-show=\"page === 'extensions'\">")
        self.bar = self.html[start:self.html.index('<div class="pagebody', start)]
        start = self.html.index("<!-- 本机扩展：")
        self.detail = self.html[start:self.html.index("<!-- Agent 会话 -->", start)]
        start = self.html.index("<!-- 本机扩展 · 添加（对话框）")
        self.dialogs = self.html[start:self.html.index("</section>", start)]

    def test_the_page_bar(self):                # 添加 and the folder are the page's, in its bar
        head = self.bar
        self.assertIn('<span class="menu-wrap" @click.outside="lxMenuClose(\'add\')">', head)
        self.assertIn('@click="lxMenu(\'add\')"><svg class="ico"><use href="#i-plus"/></svg>添加</button>', head)
        self.assertIn('<div class="menu right" x-show="lx.menu === \'add\'"', head)
        items = re.findall(r'<button class="menu-item" @click="(\w+)\(\)">([^<]+)</button>', head)
        self.assertEqual(items, [("lxNewOpen", "新建扩展"), ("lxAddFolder", "链接文件夹"), ("lxAddPackage", "导入扩展包")])
        self.assertIn('x-show="lx.home" :title="t(\'扩展文件夹\') + t(\'：\') + lx.home" '
                      '@click="lxOpenHome()"><svg class="ico"><use href="#i-folder"/></svg>扩展文件夹</button>', head)
        self.assertNotIn("扩展目录", head)                           # 扩展目录 is the catalog on this page: the folder has its own word
        self.assertNotIn("lxAdd", self.master[:self.master.index('<ul class="mlist">')])
        self.assertIn(".mgroup > span:first-child { flex: none; white-space: nowrap;", self.css)

    def test_a_row(self):
        row = self.master[self.master.index('<li class="mitem line"'):self.master.index("</li>")]
        sub = row[row.index('<div class="mitem-sub">'):]
        for piece in ('x-show="r.version" x-text="r.version"', '<span class="pill tiny off" x-show="r.linked" :title="r.linkedTip" x-text="r.linked"></span>',
                      'x-show="r.author" x-text="r.author"', '<span class="xp-where" :title="r.tip" x-text="r.where"></span>'):
            self.assertIn(piece, sub)
        self.assertNotRegex(row, r">[^<]*[\u4e00-\u9fff][^<]*<")   # a row's words come through x-text (no x-t on it)

    def test_nothing_yet(self):
        empty = self.master[self.master.index('<div class="lx-empty"'):]
        self.assertIn('x-show="lx.list && !xpInstalled.length && !lx.error"', empty)
        self.assertIn("还没有扩展。把想法交给 Agent，它会新建扩展、载入并试用。", empty)
        self.assertEqual(re.findall(r'<button class="btn small" @click="(\w+)\(\)">([^<]+)</button>', empty),
                         [("lxNewOpen", "新建扩展"), ("lxAddFolder", "链接文件夹"), ("lxAddPackage", "导入扩展包")])
        self.assertNotIn("new_extension", self.master)

    def test_the_detail_menu(self):
        d = self.detail
        menu = d[d.index('<span class="menu-wrap" @click.outside="lxMenuClose(\'more\')">'):]
        menu = menu[:menu.index("</span>", menu.index('<div class="menu right"'))]
        self.assertIn("t('更多')", menu)
        self.assertIn('@click="lxOpen(lxCur.path)" x-text="canOpen ? t(\'打开文件夹\') : t(\'文件夹路径\')"', menu)
        self.assertIn('@click="lxPack(lxCur)">打包</button>', menu)
        self.assertIn('<button class="menu-item" @click="lxCopyPrompt(lxCur)">复制给 Agent 的第一句话</button>', menu)
        self.assertIn('<button class="menu-item bad" :disabled="lx.busy !== \'\'" @click="lxDrop(lxCur)">移除</button>', menu)
        confirm = d[d.index('<div class="xp-confirm" x-show="lx.drop && lx.drop === lxCur.id">'):]
        confirm = confirm[:confirm.index("</div>")]
        self.assertIn('x-text="lxDropLine(lxCur)"', confirm)
        self.assertIn('@click="lxRemove(lxCur)"', confirm)
        self.assertIn("t('确认移除')", confirm)
        self.assertIn('@click="lx.drop = \'\'">取消</button>', confirm)
        self.assertLess(d.index("lxDrop(lxCur)"), d.index("lxRemove(lxCur)"))   # 移除 itself only after it was asked

    def test_the_approval_names_the_game_addons(self):
        d = self.detail
        card = d[d.index('<div class="lx-approve"'):]
        card = card[:card.index('<div class="xp-note')]
        self.assertIn('x-show="lxWillInstall(lxCur) && !lxGameOnly(lxCur)" '
                      'x-text="t(\'同时安装游戏插件：{names}\', { names: lxWillInstall(lxCur) })"', card)

    def test_the_notes(self):
        d = self.detail
        self.assertIn('<div class="xp-note" :class="{ bad: lxBlocked(lxCur) }" x-show="lxUnmet(lxCur)" x-text="lxUnmet(lxCur)"></div>', d)
        self.assertIn('<div class="xp-note bad" x-show="lxCur.error && !lxBlocked(lxCur)" x-text="lxCur.error"></div>', d)
        packed = d[d.index('<div class="xp-note" x-show="lx.packed && lx.packed.id === lxCur.id">'):]
        packed = packed[:packed.index("</div>")]
        self.assertIn("已打包", packed)
        self.assertIn('<span class="mono selectable grow" x-text="lx.packed ? lx.packed.file : \'\'"></span>', packed)
        self.assertIn('x-show="canOpen" @click="lxOpen(lx.packed.folder)">打开所在文件夹</button>', packed)

    def test_the_facts(self):
        d = self.detail
        kv = d[d.index("<dt>状态</dt>"):]
        kv = kv[:kv.index("</dl>")]
        self.assertIn('<dt>来源</dt><dd :title="lxOriginTip(lxCur)" x-text="lxOrigin(lxCur) || \'—\'"></dd>', kv)
        self.assertIn('<dd x-show="lxCur.origin === \'linked\'"><button class="btn tiny" @click="lxCopyPrompt(lxCur)">'
                      '复制给 Agent 的第一句话</button></dd>', kv)       # the one being developed: its first message at hand
        self.assertIn('<dt x-show="lxCur.author">作者</dt><dd x-show="lxCur.author" class="selectable" x-text="lxCur.author"></dd>', kv)
        self.assertIn('<dt x-show="lxCur.homepage">主页</dt><dd x-show="lxCur.homepage" class="mono selectable" x-text="lxCur.homepage"></dd>', kv)
        self.assertNotIn("openOut", d)                             # the homepage is words: the page opens nothing of it
        self.assertNotIn("href=", kv)
        game = kv[kv.index('<dt x-show="(lxCur.addons || []).length">游戏插件</dt>'):]
        game = game[:game.index("</dd>")]
        self.assertIn('x-for="a in (lxCur.addons || [])" :key="a.folder"', game)
        self.assertIn('<span class="pill tiny" :class="lxAddonPill(a)" x-text="lxAddonState(a)"></span>', game)
        self.assertIn('x-show="lxGameAct(lxCur) && !lxUnapproved(lxCur)" :disabled="lx.busy !== \'\'" @click="lxInstall(lxCur)"',
                      game)                                        # not before it is approved: approval installs them
        self.assertIn("lxGameAct(lxCur) === 'update' ? t('更新') : t('安装')", game)
        self.assertIn('<span class="pill tiny warn" x-show="lxRestart(lxCur)" x-text="t(\'重启游戏后生效\')"></span>', game)
        self.assertNotIn("x-html", d)

    def test_the_dialogs(self):
        d = self.dialogs
        self.assertEqual(re.findall(r'<div class="modal-back" x-show="(\w+)\.open"', d), ["lxa", "lxn", "lxp"])
        self.assertIn('@submit.prevent="lxAddSubmit()"', d)
        self.assertIn('x-model="lxa.path"', d)
        self.assertIn("lxa.kind === 'zip' ? t('导入扩展包') : t('链接文件夹')", d)
        self.assertIn('<p class="small bad-text selectable lx-why" x-show="lxa.error" x-text="lxa.error"></p>', d)
        self.assertIn('@submit.prevent="lxNew()"', d)
        fields = re.findall(r'<label class="field"[^>]*><span>([^<]+)</span><input x-t class="[^"]+" x-model="lxn\.(\w+)"', d)
        self.assertEqual(fields, [("标识", "id"), ("名称", "title"), ("游戏插件", "addon")])
        self.assertIn('<div class="field"><span>运行位置</span>', d)             # where it runs: one of three, 本机 unless chosen
        self.assertEqual(re.findall(r'<label class="check-inline"><input type="radio" value="(\w+)" x-model="lxn\.where"> ([^<]+)</label>', d),
                         [("game", "游戏"), ("local", "本机"), ("both", "游戏 · 本机")])
        self.assertIn('<p class="small muted lx-where-hint" x-text="lxWhereHint(lxn.where)"></p>', d)
        self.assertIn('<label class="field" x-show="lxn.where !== \'local\'"><span>游戏插件</span>'
                      '<input x-t class="input mono" x-model="lxn.addon" placeholder="插件文件夹名，留空则按标识生成"></label>', d)
        self.assertIn('<li x-show="lxn.result.where === \'game\'">载入时安装到游戏；新插件在游戏重启后由游戏载入。</li>', d)
        self.assertIn(':disabled="lxn.busy || !lxn.id.trim()"', d)
        self.assertIn('<p class="small bad-text selectable lx-why" x-show="lxn.error" x-text="lxn.error"></p>', d)
        self.assertIn('<p class="mono selectable lx-path" x-text="lxp.path"></p>', d)
        self.assertIn('@click="copy(lxp.path, t(\'路径\'))">复制</button>', d)

    def test_calls_exist(self):
        exprs = " ".join(v for _, v in re.findall(r'\s(x-[\w:.-]+|:[\w-]+|@[\w.:-]+)="([^"]*)"', self.dialogs))
        names = set(re.findall(r"(?<![\w.$'])([A-Za-z_]\w*)\s*\(", exprs)) - {"t"}
        self.assertEqual(names, {"lxAddSubmit", "lxNew", "copy", "lxPrompt", "lxOpen", "lxNewOpen", "lxCopyIdea", "lxWhereHint"})
        for name in names - {"copy"}:
            self.assertRegex(self.js, r"(?m)^\s*(?:async\s+)?%s\s*\(" % name, name)
        for name in names | {"lxMenu", "lxMenuClose", "lxAddFolder", "lxAddPackage", "lxNewOpen", "lxOpenHome", "lxOpen",
                             "lxPack", "lxDrop", "lxDropLine", "lxRemove", "lxInstall", "lxOrigin", "lxUnmet", "lxBlocked",
                             "lxAddonState", "lxAddonPill", "lxGameAct", "lxWillInstall", "lxRestart"}:
            self.assertTrue(re.search(r"(?m)^\s*(?:async\s+)?%s\s*\(" % name, self.js), name)

    def test_the_shell_is_looked_for(self):
        for line in ("this.canPick = !!shell.pick_folder;", "this.canPickFile = !!shell.pick_file;",
                     "this.canOpen = !!shell.open_folder;", "window.addEventListener('pywebviewready', pick);"):
            self.assertIn(line, self.js)
        for call in ("pick_folder('')", "pick_file('zip')", "shell.open_folder(path)"):
            self.assertIn(call, self.js)

    def test_the_menu_has_its_style(self):
        for rule in (".menu-wrap {", ".menu {", ".menu.right {", ".menu-item {", ".menu-item.bad {", ".mgroup-note {",
                     ".lx-empty {", ".lx-addon {", ".lx-path {"):
            self.assertIn("\n" + rule, self.css)

    def test_the_words_have_their_english(self):
        for word in ("添加", "链接文件夹", "导入扩展包", "新建扩展", "扩展文件夹", "还没有扩展。把想法交给 Agent，它会新建扩展、载入并试用。", "再建一个",
                     "首次运行前需要批准：关闭此窗口后，在详情中点「批准运行」。", "链接", "主页", "游戏插件",
                     "有更新", "同名插件已存在", "游戏目录未知", "重启游戏后生效", "同时安装游戏插件：{names}",
                     "需要无限工坊 {v} 以上", "需要 WuxianKit {v} 以上", "版本不足", "更多", "打包", "移除", "确认移除",
                     "文件夹移入回收区。", "仅解除链接，文件夹保留。", "已打包", "打开所在文件夹", "文件夹路径", "标识", "在本机运行",
                     "标识已被占用，请先移除同名扩展", "文件夹被占用，请关闭占用它的程序后重试", "未找到游戏目录",
                     "游戏里已有同名插件，未覆盖", "没有游戏插件", "扩展包过大", "扩展包无效", "游戏插件无效", "清单有误",
                     "目标文件夹已有其他内容", "游戏插件被占用，请退出游戏后重试", "游戏插件写入失败",
                     "运行位置", "游戏 · 本机", "只含游戏插件，在游戏中运行，没有本机程序", "插件文件夹名，留空则按标识生成",
                     "载入时安装到游戏；新插件在游戏重启后由游戏载入。"):
            self.assertIn("\n  '%s': '" % word, self.i18n, word)
        self.assertNotIn("new_extension 新建", self.i18n)


if __name__ == "__main__":
    unittest.main()
