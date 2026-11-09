"""The 接入 Agent page's 无限图鉴 and 应用 panels: app.js run under Node (skipped without it) against stand-ins for
GET/POST /api/agents with server=wuxianwow and GET /api/kit/apps (the shapes of the launch contract): both servers' agents
load (and the programs, for the program apps), an action on 无限图鉴 says server and one on this program does not, only
the apps with a start prompt are listed, their state and checklist follow the agents of both panels, and the empty states. index.html's two panels call what app.js has, and their 去接入 links name panels that are there."""
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
globalThis.document = { hidden: false, addEventListener() {}, getElementById: (id) => ({ id, scrollIntoView() { scrolled.push(id); } }) };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 0;
globalThis.location = { hash: '' };
globalThis.history = { replaceState() {} };
var scrolled = [];
"""

HARNESS = r"""
const out = {};
const host = (id, title, state, extra) => Object.assign({ id, title, present: state !== 'missing', can_connect: true, can_verify: id === 'claude',
                                                          state, detail: '', where: 'C:\\x', apply: '', entry: null }, extra || {});
const SKILL = { name: 'guild-chat', title: '公会聊天', description: '在公会频道里回答', needs: ['shell'],
                mcp: [{ name: 'wuxianwow', url: 'https://wuxianwow.com/mcp' }, { name: 'other', url: 'https://example.org/mcp' }] };
const APPS = {
  online: true,
  apps: [
    { id: 'guild', title: '公会聊天', by: 'WuxianKit', addon: 'WuxianKit', prompt: 'guild-chat',
      start: '开始无限工坊的应用「公会聊天」：先调用工具 wk_docs，参数 {"skill": "guild-chat"}，照读到的步骤做。',
      on: true, blocked: null, extension: { id: 'guild', title: { zh: '公会', en: 'Guild' }, on: true }, skill: SKILL },
    { id: 'quiet', title: '无启动语', by: 'x', addon: 'X', prompt: null, start: null, on: false, blocked: null, extension: null, skill: null },
    { id: 'shut', title: '关着的', by: 'x', addon: 'X', prompt: 'shut', start: 'go', on: false, blocked: 'no_guild',
      extension: { id: 'x', title: 'X', on: false }, skill: { name: 'shut', title: 'S', description: '', needs: [], mcp: [] } },
  ],
};
let wuxian = [host('claude', 'Claude Code', 'absent'), host('codex', 'Codex', 'ok'), host('cursor', 'Cursor', 'missing')];
let wxw = [host('claude', 'Claude Code', 'absent'), host('codex', 'Codex', 'absent'), host('cursor', 'Cursor', 'missing')];
let apps = APPS;
const calls = [];
function make() {
  const st = appState();
  st.$nextTick = (f) => f();
  st.$refs = {};
  st.said = [];
  st.say = (x) => st.said.push(x);
  st.copied = [];
  st.copy = (text, what) => st.copied.push([text, what]);
  st.api = async (path, opts) => {
    const body = opts && opts.body;
    calls.push([path, body || null]);
    if (path === '/api/kit/apps') { if (apps instanceof Error) throw apps; return apps; }
    if (path === '/api/programs') return [];
    if (path.startsWith('/api/agents')) {
      const web = path.includes('server=wuxianwow') || (body && body.server === 'wuxianwow');
      if (!body) return { program: { command: 'wuxian', args: ['mcp'] }, hosts: web ? wxw : wuxian, manual: { claude: web ? 'claude mcp add --transport http' : 'claude mcp add wuxian' } };
      const r = Object.assign({}, (web ? wxw : wuxian).find(h => h.id === body.host));
      r.state = body.action === 'disconnect' ? 'absent' : 'ok';
      if (body.action === 'verify') r.verify = { ok: true, text: 'fine' };
      return r;
    }
    throw new Error('unexpected ' + path);
  };
  return st;
}
const checks = (st, a) => st.appChecks(a).map(c => [c.id, c.ok, c.go || '']);

(async () => {
  // the page loads both servers' agents and the apps
  let st = make();
  st.go('connect');
  await new Promise(r => setImmediate(r));
  out.loaded = { calls: calls.map(c => c[0]).sort(), wxw: st.wxwShown.map(h => h.id), manual: st.wxw.manual.claude,
                 shown: st.appsShown.map(a => a.id), pills: st.appsShown.map(a => st.appPill(a)), empty: st.appsEmpty };
  const guild = st.appsShown[0], shut = st.appsShown[1];
  out.title = st.appTitle(guild);
  out.before = { guild: checks(st, guild), shut: checks(st, shut),
                 texts: st.appChecks(guild).map(c => c.text) };

  // connecting 无限图鉴 to Claude Code (server in the body), then this program (no server): the checklist follows
  calls.length = 0;
  await st.agentAction(st.wxw.list[0], 'connect', 'wuxianwow');
  await st.agentAction(st.agents.list[0], 'connect');
  out.posts = calls.map(c => c[1]);
  out.after = { guild: checks(st, guild), wxwState: st.wxw.list[0].state, mine: st.agents.list[0].state,
                said: st.said.slice(), busy: st.agentsBusy, pill: st.agentPill(st.wxw.list[0], 'wuxianwow'),
                other: [st.agentPill({ state: 'other' }, 'wuxianwow')[1], st.agentPill({ state: 'other' })[1]] };
  await st.agentAction(st.wxw.list[0], 'verify', 'wuxianwow');
  out.verified = st.wxw.note.claude;
  st.copy(guild.start, t('启动语'));
  out.copied = st.copied;
  st.goPanel('connect-wuxianwow');
  out.scrolled = scrolled;

  // the game away: the kept list (on and blocked not known), or none
  apps = { online: false, apps: [Object.assign({}, APPS.apps[0], { on: null, blocked: null, extension: { id: 'guild', title: '公会', on: null } })] };
  await st.loadApps();
  out.away = { shown: st.appsShown.length, pill: st.appPill(st.appsShown[0]), ext: checks(st, st.appsShown[0])[0], empty: st.appsEmpty };
  apps = { online: false, apps: [] };
  await st.loadApps();
  out.none = st.appsEmpty;
  apps = { online: true, apps: [APPS.apps[1]] };
  await st.loadApps();
  out.nostart = st.appsEmpty;
  apps = new Error('Not Found');
  await st.loadApps();
  out.failed = { error: st.apps.error, data: !!st.apps.data };

  // in English
  globalThis.Alpine = { store: () => ({ v: 'en' }) };
  st.lang = 'en';
  apps = APPS;
  await st.loadApps();
  out.english = st.appChecks(st.appsShown[0]).map(c => c.text).concat([st.appPill(st.appsShown[1])[1]]);
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error(e.stack); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class ConnectPanels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((Path(STATIC_DIR) / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_page_loads_both_servers_and_the_apps(self):
        o = self.out["loaded"]
        self.assertEqual(o["calls"], ["/api/agents", "/api/agents?server=wuxianwow", "/api/kit/apps", "/api/programs"])
        self.assertEqual(o["wxw"], ["claude", "codex"])                                   # missing hosts not listed
        self.assertEqual(o["manual"], "claude mcp add --transport http")
        self.assertEqual(o["shown"], ["guild", "shut"])                                   # only the apps with a start prompt
        self.assertEqual(o["pills"], [["ok", "运行中"], ["bad", "开不了"]])
        self.assertEqual(o["empty"], "")
        self.assertEqual(self.out["title"], "公会聊天")

    def test_checklist_before_connecting(self):
        b = self.out["before"]
        self.assertEqual(b["guild"], [["ext", True, ""], ["wuxian", True, ""], ["shell", True, ""],
                                      ["mcp:wuxianwow", False, "connect-wuxianwow"], ["mcp:other", None, ""]])
        self.assertEqual(b["texts"][1], "已接入无限工坊：Codex")
        self.assertEqual(b["texts"][3], "未接入无限图鉴")
        self.assertIn("https://example.org/mcp", b["texts"][4])
        self.assertEqual(b["shut"], [["ext", False, ""], ["wuxian", True, ""]])          # no needs, no servers

    def test_actions_and_the_checklist_after(self):
        self.assertEqual(self.out["posts"], [{"action": "connect", "host": "claude", "server": "wuxianwow"},
                                             {"action": "connect", "host": "claude"}])
        a = self.out["after"]
        self.assertEqual(a["guild"][3], ["mcp:wuxianwow", True, ""])
        self.assertEqual((a["wxwState"], a["mine"], a["busy"]), ("ok", "ok", False))
        self.assertEqual(a["said"], ["无限图鉴：Claude Code 已接入", "Claude Code 已接入"])
        self.assertEqual(a["pill"], ["ok", "已接入"])
        self.assertEqual(a["other"], ["指向别的地址", "指向别的程序"])
        self.assertEqual(self.out["verified"], {"ok": True, "text": "fine"})
        self.assertEqual(self.out["copied"][0][1], "启动语")
        self.assertIn('{"skill": "guild-chat"}', self.out["copied"][0][0])
        self.assertEqual(self.out["scrolled"], ["connect-wuxianwow"])

    def test_game_away_and_empty(self):
        a = self.out["away"]
        self.assertEqual((a["shown"], a["pill"], a["ext"], a["empty"]), (1, None, ["ext", None, ""], ""))
        self.assertEqual(self.out["none"], "进入游戏后显示")
        self.assertEqual(self.out["nostart"], "没有可交给 AI 的应用")
        self.assertEqual(self.out["failed"]["error"], "Not Found")

    def test_english(self):
        e = self.out["english"]
        self.assertEqual(e[0], 'Extension "Guild" is on')
        self.assertEqual(e[1], "Connected to Wuxian Workshop: Claude Code, Codex")
        self.assertEqual(e[3], "Connected to Wuxian Codex: Claude Code")
        self.assertEqual(e[5], "Blocked")


class Template(unittest.TestCase):
    """the two panels in index.html: what they call is in app.js, their 去接入 targets are panels of the page"""

    def setUp(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = html.index('<div class="panel" id="connect-wuxianwow">')
        self.html, self.part = html, html[start:html.index('<div class="panel">', start)]
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def test_calls_exist(self):
        exprs = " ".join(v for _, v in re.findall(r'\s(x-[\w:.-]+|:[\w-]+|@[\w.:-]+)="([^"]*)"', self.part))
        names = set(re.findall(r"(?<![\w.$'])([A-Za-z_]\w*)\s*\(", exprs)) - {"t"}
        self.assertTrue({"agentAction", "agentPill", "appChecks", "appPill", "appTitle", "goPanel", "copy"} <= names)
        for name in names:
            self.assertRegex(self.js, r"(?m)^\s*(?:async\s+)?%s\s*\(" % name, name)
        for getter in ("agentsBusy", "wxwShown", "appsShown", "appsEmpty"):
            self.assertIn(getter, exprs + self.html)
            self.assertRegex(self.js, r"(?m)^\s*get\s+%s\s*\(" % getter, getter)
        self.assertIn("loadConnect()", self.html)
        self.assertRegex(self.js, r"(?m)^\s*async\s+loadConnect\s*\(")

    def test_actions_name_the_server(self):
        for action in ("connect", "verify", "disconnect"):
            self.assertIn(f"agentAction(h, '{action}', 'wuxianwow')", self.part)
        self.assertIn("copy(a.start, t('启动语'))", self.part)

    def test_go_targets_are_panels(self):
        targets = set(re.findall(r"go: [^?]*\? '' : '([\w-]+)'", self.js))
        self.assertEqual(targets, {"connect-wuxian", "connect-wuxianwow"})
        for target in targets:
            self.assertIn(f'id="{target}"', self.html)


if __name__ == "__main__":
    unittest.main()
