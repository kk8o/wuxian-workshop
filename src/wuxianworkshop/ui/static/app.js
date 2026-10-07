/* 无限工坊 web UI. Alpine holds the state; htmx is used only where the two forms (settings, run
   Lua) are submitted, through the small "json" extension at the bottom. The page is served by the daemon at / (the fake
   daemon scripts/dev_fake_api.py serves the same files for development) and talks to it like this:

     GET  /api/session                 -> {token, port, url, mcp_url, version}: taken once at load, then every request
                                          carries Authorization: Bearer <token>
     GET  /api/status                  -> daemon / game / link / watch / reload_pending / mcp_url (status and connect pages)
     GET  /api/settings, POST          -> {mode, capture, capture_in_use, game_dir, autostart, language}; the app is the
                                          development environment only: a "player" mode left in old settings is switched
                                          back to "developer" once (migrateMode)
     GET  /api/logs?since=&limit=      -> {entries, next}: the backlog at load; then
     GET  /api/events?token=&since=    -> SSE "log" / "status" events; reconnects itself with since=<last id>
     POST /api/run {code, timeout_ms}  -> {ok, job, values, ms, chunk} or {ok:false, job, error, stack}; 504 = timeout
     POST /api/snap {max_width}        -> {path, width, height, url}; the PNG is fetched from url with the Bearer header
     POST /api/reload {reason}         -> {requested, nonce}
     POST /api/watch {action, target}  -> {watched}
     GET  /api/doctor                  -> {checks:[{id, ok, detail, fix}]};  POST /api/fix {fix} runs one: {fix, install}
                                          for the addon fixes, {fix, started, title, path} for a runtime's installer
     GET  /api/addons                  -> {addons_dir, client:{version, interface}, addons:[{name, title, version, interface,
                                          current, enabled, disabled_in, ours, load_on_demand, errors:{signatures, count}}],
                                          errors:{available, written, report, dropped, signatures}}
     GET  /api/errors?addon=&limit=    -> {available, written, report, entries:[{kind, message, addon, stack, count, first,
                                          last, build}], total}: what !WuxianWorkshop kept as of the last logout / reload
     GET  /api/update, POST {action}   -> {supported, reason, state, current, latest, size, notes, progress, error,
                                          checked}; actions check / download / apply (apply restarts the program); the
                                          state also comes with every status as status.update
     GET  /api/agents                  -> {program:{command, args}, hosts:[{id, title, present, can_connect, state, detail,
                                          where, apply, entry}], manual:{claude, codex, cursor, other}}: the coding agents
                                          and this MCP server (agents.py); POST {action: connect|disconnect|verify, host}
                                          answers that host again, with verify:{ok, text} when checked
     POST /api/check {target, live}    -> {addon, files, ok, errors, warnings, notes:[{file, line, code, message, hint}],
                                          unresolved, libraries, live:{checked} | {error}}: what would fail in the game
     GET  /api/history?addon=[&id=&against=]  -> an addon's kept versions {addon, versions:[{id, time, reason, note,
                                          count, added, changed, removed, counts}], now:{same, since, ...} | {missing}},
                                          or with id one version's diff {base, to, files:[{path, status, binary, plus,
                                          minus, diff}], truncated}; every addon of /api/addons carries history:{versions,
                                          latest, latest_id} | null. POST {action: checkpoint|restore|forget, addon, id,
                                          note}: checkpoint -> {id, new, ...}; restore -> {restored, saved, written,
                                          removed, skipped, hint}

   The 开始 page (start) is the first-run wizard: it opens while settings.onboarded is false and ticks its five steps from
   what the daemon reports (doctor, link, agents, a hot-load); window.pywebview.api.pick_folder (ui/shell.py) is the
   native folder dialog when the page runs in the App's window.

   The page keeps at most MAX_LOGS log entries. */
'use strict';

const PAGES = [                                // the navigation, in its order on the left (Ctrl+1.. go there; Ctrl+8 设置)
  { id: 'status', label: '概览', icon: 'i-home' },
  { id: 'dev', label: '开发台', icon: 'i-console' },
  { id: 'addons', label: '插件', icon: 'i-addons' },
  { id: 'api', label: 'API 手册', icon: 'i-book' },
  { id: 'connect', label: '接入 Agent', icon: 'i-plug' },
  { id: 'doctor', label: '自检与修复', icon: 'i-shield' },
  { id: 'start', label: '开始', icon: 'i-flag' },
];
const NAV = PAGES;
const SITE = 'https://wuxianwow.com/workshop';  // 无限工坊's site: opened in the user's browser (ui/shell.py PageApi.open_url)
const API_KINDS = [{ id: 'function', label: '函数' }, { id: 'event', label: '事件' }, { id: 'table', label: '枚举与结构' }];
const KIND_TEXT = { function: '函数', event: '事件', table: '表', usage: '用法' };
const CALL_FILTERS = [                         // the API manual's filter by what an addon may do (apidocs.py callability); none on: all
  { id: 'usable', label: '插件可用', tip: '插件能调用或注册的：可调用的和有限制的，不含受保护的' },
  { id: 'ok', label: '可调用', tip: '插件可以直接调用或注册' },
  { id: 'limited', label: '有限制', tip: '插件可以调用，但受限状态下返回机密值、有使用限制或前提条件' },
  { id: 'protected', label: '受保护', tip: '只有暴雪的安全代码能用：插件调用会被拦截，受限事件不能注册' },
];
const WHY = {                                  // the documentation fields that limit an entry (apidocs.py), for people
  IsProtectedFunction: '只有暴雪的安全代码能调用：插件调用会被拦截（ADDON_ACTION_BLOCKED），战斗中对安全框体也不可用。',
  ProtectedGlobal: '手册列出的受保护全局函数：只有安全代码或硬件事件触发的安全按钮能调用，插件直接调用会被禁止（ADDON_ACTION_FORBIDDEN）。',
  HasRestrictions: '有使用限制：某些情况下调用会失败或被拦截。',
  HasRestrictionsEvent: '受限事件：插件注册它会被客户端禁止（ADDON_ACTION_FORBIDDEN），游戏里会弹出「插件导致界面行为失效」。',
  SecretReturns: '返回值可能是机密值：在战斗、首领战等受限状态下读不到明文，不能比较、运算、拼接或存表。',
  SecretReturnsForAspect: '相关的方面受限时返回机密值。',
  CallbackEvent: '回调事件：由客户端的回调分发，插件不一定能用 RegisterEvent 收到。',
  SecretPayloads: '载荷可能是机密值。',
  RequireNPERestricted: '只在新手引导受限时触发。',
  RequiresClubsInitialized: '要先初始化社区（Clubs）才能用。',
  RequiresFriendList: '要先加载好友列表才能用。',
  RequiresActiveCommentator: '只有观战解说员能用。',
};
const SECRET_WHEN = {                          // SecretWhen… / SecretIn…: the state that turns the values secret
  SecretWhenInCombat: '战斗中', SecretWhenEncounterEvent: '首领战中', SecretInChatMessagingLockdown: '聊天锁定时',
  SecretInActivePvPMatch: 'PvP 对局中', SecretWhenCooldownsRestricted: '冷却受限时', SecretWhenUnitStatsRestricted: '单位属性受限时',
  SecretWhenUnitAuraRestricted: '光环受限时', SecretWhenUnitIdentityRestricted: '单位身份受限时', SecretWhenUnitNameIdentityRestricted: '单位名字受限时',
  SecretWhenUnitPowerRestricted: '能量受限时', SecretWhenUnitPowerMaxRestricted: '能量上限受限时', SecretWhenUnitHealthMaxRestricted: '生命上限受限时',
  SecretWhenUnitSpellCastRestricted: '施法信息受限时', SecretWhenUnitThreatValuesRestricted: '仇恨值受限时', SecretWhenUnitThreatStateRestricted: '仇恨状态受限时',
  SecretWhenUnitPossessionRestricted: '控制状态受限时', SecretWhenUnitComparisonRestricted: '单位比较受限时', SecretWhenLossOfControlInfoRestricted: '失控信息受限时',
  SecretWhenTotemSlotSecret: '图腾栏是机密时', SecretWhenAnchoringSecret: '锚点是机密时', SecretWhenCurveSecret: '曲线是机密时',
  SecretWhenNumericFormatterSecret: '数字格式是机密时', SecretWhenLuaTableHasSecretKeys: '表里有机密键时',
};
const EXAMPLES = [                             // 试一下: an argument's name (or type) → an example value, the first match wins
  [/^unit(token)?$|UnitToken|unitID/i, '"player"'], [/guid/i, 'UnitGUID("player")'], [/spell(ID|Identifier)?$|spellID/i, '133'],
  [/itemID|itemInfo|itemLink|^item$/i, '6948'], [/questID/i, '7'], [/uiMapID|^mapID$/i, '1429'], [/achievementID/i, '6'],
  [/factionID/i, '72'], [/currency(ID|Type)/i, '1'], [/classID/i, '8'], [/raceID/i, '1'], [/specID/i, '62'], [/mountID/i, '6'],
  [/bag(ID|Index)?$|containerIndex/i, '0'], [/slot|index$/i, '1'], [/cvar/i, '"scriptErrors"'], [/addon(Name|Index)?$/i, '"WoWBridge"'],
  [/^name$|playerName|characterName/i, 'UnitName("player")'],
];
const KINDS = ['ERR', 'OUT', 'WARN', 'BLOCKED', 'RUN', 'RELOAD', 'SNAP', 'WATCH', 'SLOTS', 'INFO'];
const REASON_TEXT = { load: '热加载', watch: '开始监视', save: '保存后热加载', new: '新建', manual: '手动存', restore: '回退前' };
const STATUS_TEXT = { added: '新增', changed: '改动', removed: '删除' };
const CHECK_TEXT = {                           // agent/lint.py's codes, for people (the messages stay the agent's English)
  syntax: '语法错误', 'missing-lib': '客户端没有的库', lua52: 'Lua 5.1 没有', 'event-unknown': '未知事件',
  'toc-missing': 'toc 里的文件不存在', xml: 'XML 格式错误', undefined: '游戏里是 nil', read: '读不了',
  typo: '疑似拼错', moved: '已改到命名空间', 'api-unknown': '手册里没有', 'global-write': '写了全局变量', protected: '受保护函数',
  'event-restricted': '受限事件', encoding: '不是 UTF-8', 'toc-interface': 'Interface 号不对',
};
const MAX_LOGS = 2000;
const RECONNECT_MS = 2000;
const LINK_TEXT = { online: '在线', offline: '离线', waiting: '等待游戏', handshake: '握手中', connecting: '握手中' };

const CLI_HELP = [
  'wuxian                       打开窗口（守护进程 + 托盘）',
  'wuxian serve                 只起守护进程（无窗口）',
  'wuxian status                链路与游戏状态',
  'wuxian run "return 1+1"      在游戏里运行一行 Lua，打印返回值',
  'wuxian load <插件|文件>       热加载一个插件或 .lua 文件',
  'wuxian watch start|stop|list 保存即热加载',
  'wuxian snap                  游戏截图，存到 snaps 文件夹',
  'wuxian reload                请求 /reload（游戏里弹按钮）',
  'wuxian logs [--follow] [--since N]',
  'wuxian say <文本>            把文本发到游戏聊天框',
  'wuxian doctor                自检',
  'wuxian addons                已装插件（版本、Interface、启用、报错数）',
  'wuxian errors [插件]          平台插件收集到的报错（截至上次登出或重载）',
  'wuxian check <插件|文件>       送进游戏前检查：语法、客户端没有的库和 API、拼错的名字、误写的全局变量',
  'wuxian history [插件] [编号]   插件的历史版本；带编号是那一版和现在的差异',
  'wuxian checkpoint <插件> [说明] 给插件存一份',
  'wuxian restore <插件> <编号>   把插件的文件回退到那一版',
  'wuxian install               安装插件（先清后装）',
  'wuxian update [check|download|apply]  程序更新（安装版）：查看、检查、下载、重启更新',
  'wuxian mcp                   stdio 方式的 MCP 服务器（给 Claude Code / Codex / Cursor）',
].join('\n');

function appState() {
  return {
    PAGES, NAV, KINDS, API_KINDS, KIND_TEXT,
    page: 'status',
    devTool: 'run',                            // 开发台's tool on the right: run | snap | watch | reload
    token: null,
    status: null, statusAt: 0, daemonStarted: null,
    session_: null,
    settings: { mode: 'developer', capture: 'wgc', capture_in_use: null, game_dir: '', autostart: false, language: 'auto' },
    form: { capture: 'wgc', game_dir: '', autostart: false, language: 'auto' },
    settingsNote: '', settingsLoaded: false,
    logs: [], lastId: 0,
    kinds: Object.fromEntries(KINDS.map(k => [k, true])),
    filterText: '', follow: true,
    sse: null, sseState: 'closed', sseTimer: null,
    run: { code: '', timeout: 10000, busy: false, result: null },
    snap: { busy: false, url: null, info: null, error: null },
    reload: { busy: false, nonce: null, error: null },
    watchTarget: '',
    doctor: { checks: [], busy: false, fixing: '', result: '', error: '' },
    addons: { list: null, note: '', busy: false, dir: '', client: null, errors: null, characters: 0,
              open: null, entries: [], entriesBusy: false, entriesNote: '',
              sel: null, tab: 'info' },          // the addon shown on the right ('*': the error collection) and its tab
    hist: { open: null, data: null, busy: false, note: '', diff: null, diffKey: '', diffBusy: false, confirm: '',
            working: '', result: null },
    chk: { open: null, busy: false, data: null, error: '' },
    showToken: false,
    CALL_FILTERS,
    docs: { q: '', kind: '', call: '', results: [], counts: null, total: 0, cursor: -1, busy: false, sel: null, topic: null, topics: [],
            about: null, check: null, checking: false, open: {}, back: [] },
    tryit: { code: '', busy: false, result: null, trace: null, seconds: 10 },
    lang: 'zh-CN',                             // the language in force (settings.language; 'auto' follows the system)
    na: { open: false, name: '', title: '', notes: '', template: 'basic', busy: false, error: '', result: null },
    agents: { list: null, program: null, manual: {}, busy: '', note: {} },
    start: { gameInput: '', gameEdit: false, gameNote: '', saving: false, installBusy: false, installNote: '', installRestart: false,
             first: { name: 'MyFirstAddon', title: '我的第一个插件', busy: false, error: '', result: null, loaded: false, fromId: 0 } },
    canPick: false,
    updBusy: false,
    migrating: false,
    timers: false, linked: false, desktop: false,
    toast: '', toastTimer: null,
    daemonError: '',
    now: Date.now(),

    async init() {
      window.wx = { go: (p) => this.go(p), state: this, checkUpdate: () => this.checkUpdate() };   // for the shell (tray)
      const pick = () => { this.canPick = !!(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder); };
      pick();
      window.addEventListener('pywebviewready', pick);
      if (!this.desktop) {                     // once: what a desktop program does with keys and the right button
        this.desktop = true;
        window.addEventListener('keydown', (e) => {
          if (!e.ctrlKey || e.altKey || e.shiftKey || !/^[1-8]$/.test(e.key)) return;
          const n = Number(e.key);
          e.preventDefault();
          this.go(n === 8 ? 'settings' : (NAV[n - 1] || {}).id);
        });
        document.addEventListener('contextmenu', (e) => {   // no browser menu (back, reload, print) on the program's chrome
          if (!e.target.closest('input, textarea, pre, code, .selectable, .md')) e.preventDefault();
        });
      }
      if (!this.timers) {                      // once: init() runs again while the daemon is not up yet
        this.timers = true;
        setInterval(() => { this.now = Date.now(); }, 1000);
        setInterval(() => { if (this.sseState !== 'open') this.refreshStatus(); }, 5000);
      }
      try {
        await this.session();
      } catch (e) {
        this.daemonError = e.message;
        this.sseState = 'closed';
        setTimeout(() => this.init(), RECONNECT_MS);
        return;
      }
      if (location.hash.length > 1 && !this.linked) {
        this.linked = true;
        this.openLink(location.hash.slice(1));
      }
      this.daemonError = '';
      await Promise.all([this.refreshStatus(), this.loadSettings(), this.loadLogs()]);
      if (!this.linked && this.settingsLoaded && !this.settings.onboarded && this.page === 'status') this.go('start');   // the first run
      this.connectEvents();
    },

    // ---- navigation: the page is also in the address (#dev), so that a reload or a link opens it
    openLink(link) {                           // #api/UnitHealth: the manual, looked up; #addons/new: the 新建插件 form
      const [id, arg] = String(link).split('/');
      this.go(id);
      if (id === 'api' && arg) {
        this.docs.q = decodeURIComponent(arg);
        this.docsSearch().then(() => { if (this.docs.results.length) this.docsOpen(this.docs.results[0]); });
      }
      if (id === 'addons' && arg === 'new') this.na.open = true;
    },
    go(id) {
      if (!PAGES.some(p => p.id === id) && id !== 'settings') return;
      this.page = id;
      if (location.hash !== '#' + id) history.replaceState(null, '', '#' + id);
      if (id === 'doctor' && !this.doctor.checks.length) this.runDoctor();
      if (id === 'addons' && this.addons.list === null) this.loadAddons();
      if (id === 'dev') this.scrollLogs(true);
      if (id === 'api') this.docsInit();
      if (id === 'start') this.startInit();
      if (id === 'connect') this.loadAgents();
    },
    openSite() {
      const api = window.pywebview && window.pywebview.api;
      if (api && api.open_url) api.open_url(SITE);
      else window.open(SITE, '_blank', 'noopener');
    },
    get updateChip() {                         // the tool bar's short form of updateBanner
      const u = this.upd;
      if (!u || !u.supported) return '';
      if (u.state === 'available') return '新版本 ' + u.latest;
      if (u.state === 'downloading') return '下载中 ' + u.progress + '%';
      if (u.state === 'ready') return '重启更新到 ' + u.latest;
      return '';
    },
    get addonsUpdateText() {                   // the game folder's addons not yet at the program's version, or (a game
      const a = this.status && this.status.addons_update;   // update) at the client's ## Interface (sync_addons)
      if (!a) return '';
      const list = a.addons || [];
      const names = list.map(x => x.why === 'interface'
        ? x.name + ' 的 Interface ' + (x.interface || '?') + ' → ' + x.want_interface
        : x.name + ' ' + (x.version || '未安装') + ' → ' + a.version).join('、');
      const game = list.some(x => x.why === 'interface') ? '（游戏更新了，不更新的话游戏会把它们当成过期插件）' : '';
      if (a.waiting === 'game') return names + game + '：退出游戏后自动安装（也可以在「自检与修复」里马上装）';
      return names + '：没能自动安装（' + (a.error || '原因不明') + '）';
    },
    get addonsUpdateShort() {
      const a = this.status && this.status.addons_update;
      if (!a) return '';
      const game = (a.addons || []).some(x => x.why === 'interface');
      if (a.waiting === 'game') return game ? '游戏更新了：插件待更新（退出游戏后自动）' : '插件待更新到 ' + a.version + '（退出游戏后自动）';
      return game ? '游戏更新后插件没能更新' : '插件没能更新到 ' + a.version;
    },
    get doctorSummary() {
      const c = this.doctor.checks;
      const bad = c.filter(x => x.ok === false).length, unknown = c.filter(x => x.ok == null).length;
      return c.length + ' 项：' + (bad ? bad + ' 项要处理' : '全部通过') + (unknown ? '，' + unknown + ' 项查不了' : '');
    },
    get banner() {
      if (this.daemonError) return '守护进程未就绪：' + this.daemonError + '。正在重试…';
      if (this.status && this.status.daemon.fake) return '当前连的是开发用的假接口（scripts/dev_fake_api.py），数据都是假的。';
      return '';
    },
    get connText() {
      return { open: '日志流已连接', connecting: '连接中…', reconnecting: '重连中…', closed: '未连接' }[this.sseState] || this.sseState;
    },
    get connTitle() { return 'SSE /api/events · 最近日志 id ' + this.lastId; },

    // ---- api
    async session() {
      const r = await fetch('/api/session', { cache: 'no-store' });
      if (!r.ok) throw new Error('GET /api/session ' + r.status);
      const d = await r.json();
      this.token = d.token;
      this.session_ = d;
      return d;
    },
    async api(path, opts = {}, retried = false) {
      const headers = Object.assign({ 'Authorization': 'Bearer ' + this.token }, opts.headers || {});
      let body = opts.body;
      if (body !== undefined && typeof body !== 'string') {
        body = JSON.stringify(body);
        headers['Content-Type'] = 'application/json';
      }
      let r;
      try {
        r = await fetch(path, { method: opts.method || (body !== undefined ? 'POST' : 'GET'), headers, body, cache: 'no-store' });
      } catch (e) {
        throw new Error('无法连接守护进程（' + e.message + '）');
      }
      const text = await r.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
      if (r.status === 401 && !retried) {               // the daemon restarted with a new token
        await this.session();
        return this.api(path, opts, true);
      }
      if (!r.ok) {
        const err = new Error(apiMessage(data, r));
        err.status = r.status;
        err.data = data;
        throw err;
      }
      return data;
    },
    async fetchBlob(url) {
      const r = await fetch(url, { headers: { 'Authorization': 'Bearer ' + this.token } });
      if (!r.ok) throw new Error('GET ' + url + ' ' + r.status);
      return r.blob();
    },

    // ---- status
    async refreshStatus() {
      if (!this.token) return;
      try {
        this.applyStatus(await this.api('/api/status'));
        this.daemonError = '';
      } catch (e) {
        if (!e.status) this.daemonError = e.message;
      }
    },
    applyStatus(s) {
      if (!s || !s.daemon) return;
      s.game = s.game || {};
      s.link = s.link || {};
      s.watch = s.watch || [];
      if (this.daemonStarted !== null && s.daemon.started && s.daemon.started !== this.daemonStarted) {
        this.logs = [];                                 // a new daemon numbers its entries from 1 again
        this.lastId = 0;
        this.say('守护进程已重启');
        this.connectEvents();
      }
      this.daemonStarted = s.daemon.started || this.daemonStarted;
      this.status = s;
      this.statusAt = Date.now() / 1000;
      if (s.daemon.mode === 'player') this.migrateMode();
    },
    get clientVersion() {
      if (!this.status || !this.status.game.found) return '—';
      const g = this.status.game;
      if (g.version && g.build && !String(g.version).endsWith('.' + g.build)) return g.version + '（' + g.build + '）';
      return g.version || g.build || '—';
    },
    get linkOnline() { return !!(this.status && this.status.link.state === 'online'); },
    get linkText() {
      if (!this.status) return '—';
      if (this.status.link.state !== 'online' && this.status.link.blocked === 'restart') return '等待游戏重启';
      if (this.status.link.state !== 'online' && this.status.link.blocked === 'player') return '未读取';
      const st = this.status.link.state || (this.status.game.found ? 'waiting' : 'offline');
      return LINK_TEXT[st] || st;
    },
    get linkClass() {
      if (!this.status) return 'muted';
      const st = this.status.link.state;
      if (st !== 'online' && this.status.link.blocked === 'restart') return 'warn';
      return st === 'online' ? 'ok' : (st === 'offline' || !st) ? 'muted' : 'warn';
    },
    get linkWhy() {                            // one line under 离线: why, and what to do
      const s = this.status;
      if (!s || s.link.state === 'online') return '';
      if (s.link.blocked === 'restart') return '安装加入了运行中的游戏不认识的新文件：完整退出并重启游戏后才能连接（/reload 不够）。';
      if (s.link.blocked === 'player') return '旧设置里是玩家模式，正在切回开发模式…';
      if (!s.game.found) return '没有找到游戏窗口。';
      if (s.game.minimized) return '游戏窗口最小化了：还原窗口后自动恢复。';
      return '看不到帧码：确认 WoWBridge 已启用、已进入游戏世界（不是读取画面或角色选择）；用 GDI 抓图时帧码不能被遮住。';
    },
    get linkHint() {                           // one line under 在线: what can be done now
      if (!this.linkOnline) return '';
      return 'Agent 现在可以自己写插件、在游戏里试，读报错和截图来调试；你也可以在开发台里自己试。';
    },

    // ---- logs
    async loadLogs() {
      try {
        const d = await this.api('/api/logs?limit=' + MAX_LOGS);
        (d.entries || []).forEach(e => this.pushLog(e, true));
        this.scrollLogs(true);
      } catch (e) { /* the stream brings them */ }
    },
    pushLog(e, quiet) {
      if (!e || typeof e.id !== 'number') return;
      if (e.id <= this.lastId && this.logs.some(x => x.id === e.id)) return;
      this.logs.push(e);
      if (this.logs.length > MAX_LOGS) this.logs.splice(0, this.logs.length - MAX_LOGS);
      if (e.id > this.lastId) this.lastId = e.id;
      if (!quiet) this.scrollLogs();
    },
    clearLogs() { this.logs = []; },
    get visibleLogs() {
      const q = this.filterText.trim().toLowerCase();
      return this.logs.filter(e => this.kinds[e.kind] !== false && (!q || (e.text || '').toLowerCase().includes(q)));
    },
    get recentLogs() {                         // 概览's activity: the newest first, without the link's pings
      const out = [];
      for (let i = this.logs.length - 1; i >= 0 && out.length < 60; i--) {
        const e = this.logs[i];
        if (!(e.kind === 'INFO' && /^ping \d+:/.test(e.text || ''))) out.push(e);
      }
      return out;
    },
    get ovVitals() {                           // 概览's readings of the link: [{label, value, unit, sub, tip}]
      const l = (this.status && this.status.link) || {};
      const left = l.slots_left, hb = l.hb;
      return [
        { label: '延迟', value: l.ping_p50 != null ? Math.round(l.ping_p50 * 1000) : '—', unit: l.ping_p50 != null ? 'ms' : '',
          tip: '一来一回的时间：最近 20 次的中位数' },
        { label: '心跳', value: hb || '—', unit: hb ? t('秒') : '', tip: '开发组件多久报一次平安' },
        { label: '最近帧', value: l.last_frame ? this.ago(l.last_frame) : '—', unit: '', tip: '最近一次从游戏画面读到帧码' },
        { label: '信箱槽位', value: left != null ? left : '—', unit: '',
          sub: left != null && hb ? t('约够 {h} 小时', { h: Math.max(0, left * hb / 3600).toFixed(1) }) : '',
          tip: '这个游戏进程还能收的信件数（心跳也占）；用完要完整重启游戏，/reload 不够' },
        { label: '抓图', value: l.capture ? l.capture.toUpperCase() : '—', unit: '',
          tip: 'WGC：直接读游戏窗口，被挡住也行；GDI：读屏幕，帧码所在的角不能被遮住' },
      ];
    },
    get errCount() { return this.logs.reduce((n, e) => n + (e.kind === 'ERR' ? 1 : 0), 0); },
    scrollLogs(force) {
      if (!(this.follow || force) || this.page !== 'dev') return;
      this.$nextTick(() => {
        const box = this.$refs.logbox;
        if (box) box.scrollTop = box.scrollHeight;
      });
    },
    onLogScroll(ev) {
      const b = ev.target;
      const atBottom = b.scrollHeight - b.scrollTop - b.clientHeight < 12;
      if (atBottom && !this.follow) this.follow = true;        // scrolled back to the end: follow again
      else if (!atBottom && this.follow && b.scrollHeight > b.clientHeight) this.follow = false;
    },

    // ---- SSE
    connectEvents() {
      this.closeEvents();
      if (!this.token) return;
      const url = '/api/events?token=' + encodeURIComponent(this.token) + '&since=' + this.lastId;
      const es = new EventSource(url);
      this.sse = es;
      if (this.sseState !== 'open') this.sseState = 'connecting';
      es.onopen = () => { this.sseState = 'open'; this.daemonError = ''; };
      es.addEventListener('log', ev => { try { this.pushLog(JSON.parse(ev.data)); } catch (e) { /* ignore a bad line */ } });
      es.addEventListener('status', ev => { try { this.applyStatus(JSON.parse(ev.data)); } catch (e) { /* ignore */ } });
      es.onerror = () => {
        es.close();
        if (this.sse !== es) return;
        this.sse = null;
        this.sseState = 'reconnecting';
        this.sseTimer = setTimeout(async () => {
          this.sseTimer = null;
          try { await this.session(); } catch (e) { this.daemonError = e.message; }
          this.connectEvents();                                  // with since=<last id we have>
        }, RECONNECT_MS);
      };
    },
    closeEvents() {
      if (this.sseTimer) { clearTimeout(this.sseTimer); this.sseTimer = null; }
      if (this.sse) { const es = this.sse; this.sse = null; es.close(); }
    },

    // ---- developer page
    onRun(d) {
      this.run.busy = false;
      const j = d.json || {};
      if (d.status === 0) this.run.result = { ok: false, error: d.error || '无法连接守护进程' };
      else if (d.status === 504) this.run.result = { ok: false, job: j.job, error: '超时：游戏没有在 ' + this.run.timeout + ' ms 内回话（job ' + (j.job || '?') + '）' };
      else if (!d.ok) this.run.result = { ok: false, error: apiMessage(j, { status: d.status }) };
      else this.run.result = j;
    },
    async takeSnap() {
      this.snap.busy = true;
      this.snap.error = null;
      try {
        const r = await this.api('/api/snap', { body: { max_width: 1280 } });
        const blob = await this.fetchBlob(r.url);
        if (this.snap.url) URL.revokeObjectURL(this.snap.url);
        this.snap.url = URL.createObjectURL(blob);
        this.snap.info = r;
      } catch (e) {
        this.snap.error = e.message;
      } finally {
        this.snap.busy = false;
      }
    },
    async requestReload() {
      this.reload.busy = true;
      this.reload.error = null;
      try {
        const r = await this.api('/api/reload', { body: { reason: 'ui' } });
        this.reload.nonce = r.nonce;
        if (this.status) this.status.reload_pending = true;
        this.say('已请求重载，去游戏里点「立即重载」');
      } catch (e) {
        this.reload.error = e.message;
      } finally {
        this.reload.busy = false;
      }
    },
    async watch(action, target) {
      if (!target) return;
      try {
        const r = await this.api('/api/watch', { body: { action, target } });
        if (this.status) this.status.watch = r.watched || [];
      } catch (e) {
        this.say('watch 失败：' + e.message);
      }
    },

    // ---- doctor
    async runDoctor() {
      this.doctor.busy = true;
      this.doctor.error = '';
      try {
        const d = await this.api('/api/doctor');
        this.doctor.checks = d.checks || [];
      } catch (e) {
        this.doctor.checks = [];
        this.doctor.error = '自检失败：' + e.message;
      } finally {
        this.doctor.busy = false;
      }
    },
    async fix(c) {
      this.doctor.fixing = c.id;
      this.doctor.result = '';
      try {
        const d = await this.api('/api/fix', { body: { fix: c.fix } });
        const lines = [];
        if (d.started) {                       // a runtime: Microsoft's installer carries on in its own window
          lines.push('已启动微软的「' + d.title + '」安装程序（签名已核对），按它的提示装完后点「重新检查」。');
        } else {
          const r = d.install || {};
          if (r.available === false) lines.push(r.detail || '安装器还没有接入。');
          if (r.installed && r.installed.length) lines.push('已安装：' + r.installed.length + ' 个文件');
          if (r.removed && r.removed.length) lines.push('已清理：' + r.removed.length + ' 项');
          if (r.deferred && r.deferred.length) lines.push('游戏还在运行：' + r.deferred.join('、') + ' 等游戏关闭后再移除。');
          if (r.restart_for_link) lines.push('需要完整重启游戏（客户端只在启动时发现新文件）。');
          else if (r.restart_required) lines.push('下次重启游戏后生效（toc 只在启动时读取），现在照常可用。');
        }
        this.doctor.result = lines.join('\n') || JSON.stringify(d);
        await this.runDoctor();
      } catch (e) {
        this.doctor.result = '修复失败：' + e.message;
      } finally {
        this.doctor.fixing = '';
      }
    },

    // ---- addons
    async loadAddons() {
      this.addons.busy = true;
      try {
        const d = await this.api('/api/addons');
        Object.assign(this.addons, { list: d.addons || [], dir: d.addons_dir || '', client: d.client || null,
                                     errors: d.errors || null, characters: d.characters || 0 });
        this.addons.note = this.addons.list.length ? '' : '游戏目录里没有找到插件。';
        if (this.addons.open && this.addons.open !== '*' && !this.addons.list.some(a => a.name === this.addons.open)) this.addons.open = null;
        const keep = this.addons.sel === '*' || this.addons.list.some(a => a.name === this.addons.sel);
        if (!keep) {
          const first = this.addons.list.find(a => !a.ours) || this.addons.list[0];
          this.addons.sel = null;
          if (first) this.selectAddon(first.name);
        } else if (this.addons.tab === 'errors') await this.loadErrors(this.addons.sel);
      } catch (e) {
        this.addons.list = [];
        this.addons.note = e.status === 404 ? '没有找到游戏目录（' + e.message + '）。' : '读取失败：' + e.message;
      } finally {
        this.addons.busy = false;
      }
    },
    async toggleErrors(name) {
      if (this.addons.open === name) { this.addons.open = null; return; }
      this.addons.open = name;
      await this.loadErrors(name);
    },
    async loadErrors(name) {
      this.addons.entriesBusy = true;
      this.addons.entries = [];
      this.addons.entriesNote = '';
      try {
        const d = await this.api('/api/errors?limit=50' + (name === '*' ? '' : '&addon=' + encodeURIComponent(name)));
        this.addons.entries = d.entries || [];
        if (!d.available) this.addons.entriesNote = d.reason || '还没有收集到报错。';
        else if (!this.addons.entries.length) this.addons.entriesNote = '没有记录。';
      } catch (e) {
        this.addons.entriesNote = '读取失败：' + e.message;
      } finally {
        this.addons.entriesBusy = false;
      }
    },
    // ---- the 插件 page: the list on the left, one addon (or the error collection: '*') on the right in tabs
    get selAddon() { return (this.addons.list || []).find(a => a.name === this.addons.sel) || null; },
    selectAddon(name) {
      this.addons.sel = name;
      const a = this.selAddon;
      let tab = this.addons.tab || 'info';
      if (name === '*') tab = 'errors';
      else if (a && a.ours && (tab === 'check' || tab === 'history')) tab = 'info';
      this.setAddonTab(tab);
    },
    async setAddonTab(tab) {
      const name = this.addons.sel;
      if (!name) return;
      this.addons.tab = tab;
      if (tab === 'errors') { this.addons.open = name; await this.loadErrors(name); }
      if (tab === 'check' && (this.chk.open !== name || (!this.chk.data && !this.chk.busy))) await this.runCheck(name);
      if (tab === 'history' && this.hist.open !== name) {
        Object.assign(this.hist, { open: name, data: null, diff: null, diffKey: '', confirm: '', result: null });
        await this.loadHistory(name);
      }
    },

    // ---- the checks before the game (/api/check): 检查 on the 插件 page
    async toggleCheck(name) {
      if (this.chk.open === name && !this.chk.busy) { this.chk.open = null; return; }
      await this.runCheck(name);
    },
    async runCheck(name) {
      Object.assign(this.chk, { open: name, busy: true, data: null, error: '' });
      try {
        const d = await this.api('/api/check', { body: { target: name, live: true } });
        if (this.chk.open === name) this.chk.data = d;
      } catch (e) {
        this.chk.error = '检查失败：' + e.message;
      } finally {
        this.chk.busy = false;
      }
    },
    checkText(code) { return CHECK_TEXT[code] || code; },
    get checkRows() {                          // errors, then warnings, then notes, each with its level
      const d = this.chk.data;
      if (!d) return [];
      return [].concat((d.errors || []).map(f => Object.assign({ level: 'bad' }, f)),
                       (d.warnings || []).map(f => Object.assign({ level: 'warn' }, f)),
                       (d.notes || []).map(f => Object.assign({ level: '' }, f)));
    },
    get checkSummary() {
      const d = this.chk.data;
      if (!d) return '';
      const live = d.live || {};
      return d.files + ' 个文件：' + (d.errors.length ? d.errors.length + ' 个错误' : '没有错误') + ' · '
           + (d.warnings.length ? d.warnings.length + ' 个警告' : '没有警告')
           + (d.libraries ? ' · 跳过 ' + d.libraries + ' 个库文件' : '')
           + (live.checked ? ' · 问过游戏 ' + live.checked + ' 个名字' : '')
           + (live.error ? ' · 没能问游戏（' + live.error + '）' : '');
    },

    // ---- kept versions (/api/history): 历史 on the 插件 page
    async toggleHistory(name) {
      if (this.hist.open === name) { this.hist.open = null; return; }
      Object.assign(this.hist, { open: name, data: null, diff: null, diffKey: '', confirm: '', result: null });
      await this.loadHistory(name);
    },
    async loadHistory(name) {
      this.hist.busy = true;
      this.hist.note = '';
      try {
        const d = await this.api('/api/history?addon=' + encodeURIComponent(name));
        if (this.hist.open !== name) return;
        this.hist.data = d;
        if (!d.versions.length) this.hist.note = '还没有存过。热加载、开始监视或点「存一份」时会存一份。';
        const a = (this.addons.list || []).find(x => x.name === name);
        if (a) a.history = d.versions.length ? { versions: d.versions.length, latest: d.versions[0].time, latest_id: d.versions[0].id } : null;
      } catch (e) {
        this.hist.data = null;
        this.hist.note = '读取失败：' + e.message;
      } finally {
        this.hist.busy = false;
      }
    },
    async histDiff(name, id, against) {
      const key = id + ':' + against;
      if (this.hist.diffKey === key) { this.hist.diff = null; this.hist.diffKey = ''; return; }
      this.hist.diffBusy = true;
      try {
        this.hist.diff = await this.api('/api/history?addon=' + encodeURIComponent(name) + '&id=' + id + '&against=' + against);
        this.hist.diffKey = key;
      } catch (e) {
        this.say('对比失败：' + e.message);
      } finally {
        this.hist.diffBusy = false;
      }
    },
    armed(key) {                               // a second click within 4 s confirms
      if (this.hist.confirm === key) { this.hist.confirm = ''; return true; }
      this.hist.confirm = key;
      setTimeout(() => { if (this.hist.confirm === key) this.hist.confirm = ''; }, 4000);
      return false;
    },
    async histSave(name) {
      this.hist.working = 'save';
      try {
        const v = await this.api('/api/history', { body: { action: 'checkpoint', addon: name, note: '' } });
        this.say(v.new ? '已存为 #' + v.id : '没有改动：最新一份 #' + v.id + ' 就是现在的文件');
        if (this.hist.open === name) await this.loadHistory(name);
        else {
          const a = (this.addons.list || []).find(x => x.name === name);
          if (a && v.new) a.history = { versions: ((a.history && a.history.versions) || 0) + 1, latest: v.time, latest_id: v.id };
        }
      } catch (e) {
        this.say('存一份失败：' + e.message);
      } finally {
        this.hist.working = '';
      }
    },
    async histRestore(name, id) {
      if (!this.armed('r' + id)) return;
      this.hist.working = 'r' + id;
      try {
        const r = await this.api('/api/history', { body: { action: 'restore', addon: name, id } });
        this.hist.result = r;
        this.hist.diff = null;
        this.hist.diffKey = '';
        await this.loadHistory(name);
      } catch (e) {
        this.say('回退失败：' + e.message);
      } finally {
        this.hist.working = '';
      }
    },
    async histForget(name) {
      if (!this.armed('forget')) return;
      try {
        await this.api('/api/history', { body: { action: 'forget', addon: name } });
        this.say('已清空 ' + name + ' 的历史');
        this.hist.result = null;
        await this.loadHistory(name);
      } catch (e) {
        this.say('清空失败：' + e.message);
      }
    },
    async histLoad(name) {
      try {
        const r = await this.api('/api/load', { body: { target: name } });
        const bad = (r.files || []).filter(x => !x.ok);
        this.say(bad.length ? '热加载有 ' + bad.length + ' 个文件出错，看开发台的日志' : '已热加载 ' + name);
      } catch (e) { this.say('热加载失败：' + e.message); }
    },
    reasonText(r) { return REASON_TEXT[r] || r; },
    statusText(s) { return STATUS_TEXT[s] || s; },
    changeText(v) {                            // "改 Core.lua，新增 2 个文件" for a version or the comparison with now
      if (!v) return '';
      const n = v.counts || {};
      const part = (kind, verb) => {
        const k = n[kind] || 0;
        if (!k) return '';
        return verb + ' ' + (k === 1 ? (v[kind] || [])[0] : (v[kind] || [])[0] + ' 等 ' + k + ' 个文件');
      };
      return [part('changed', '改'), part('added', '新增'), part('removed', '删除')].filter(Boolean).join('，') || '没有改动';
    },
    get histNow() {                            // one line on how the files now compare with the latest version
      const d = this.hist.data;
      const now = d && d.now;
      if (!now) return '';
      if (now.missing) return '插件文件夹不在了：回退可以把它找回来。';
      if (now.error) return now.error;
      return now.same ? '现在的文件和最新一份 #' + now.since + ' 相同。' : '#' + now.since + ' 之后：' + this.changeText(now) + '。';
    },
    diffLines(text) {                          // the lines of a unified diff, the --- / +++ head left out
      return (text || '').split('\n').slice(2).map(line => ({
        t: line || ' ', c: line.startsWith('@@') ? 'hunk' : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : '' }));
    },

    get addonRows() {                          // an addon whose ## Group is listed right after it, indented
      const list = this.addons.list || [];
      const names = new Set(list.map(a => a.name));
      const kids = {}, top = [];
      for (const a of list) {
        if (a.group && a.group !== a.name && names.has(a.group)) (kids[a.group] = kids[a.group] || []).push(a);
        else top.push(a);
      }
      const rows = [];
      for (const a of top) {
        rows.push(Object.assign({}, a, { _child: false }));
        for (const c of kids[a.name] || []) rows.push(Object.assign({}, c, { _child: true }));
      }
      return rows;
    },
    roleText(r) { return { platform: '平台', developer: '开发组件', lab: '实验' }[r] || ''; },
    reportText(r) { return r === true ? '开' : r === false ? '关' : '未设置'; },
    enabledText(a) { return a.enabled === true ? '已启用' : a.enabled === false ? '已禁用' : a.disabled_in + ' 个角色禁用'; },

    // ---- the API manual (apidocs.py through /api/apidocs): the search, with what an addon may do with each result (call: ok /
    // limited / protected) and a filter by it; an entry with that said in full, its types in place, its namespace; 试一下 next to
    // 开发台's 运行 Lua: a function called once (its values by the documented return names, tables opened), an enum read in the
    // game against the manual, an event listened for (the trace)
    async docsInit() {
      if (this.docs.about) return;
      try {
        this.docs.about = await this.api('/api/apidocs');
        this.docs.topics = this.docs.about.manual || [];
      } catch (e) { this.say(t('API 手册：') + e.message); }
    },
    get docsAbout() {
      const a = this.docs.about;
      if (!a) return '';
      const pack = this.status && this.status.content && this.status.content.packs && this.status.content.packs.api;
      const src = pack ? t(pack.source === 'downloaded' ? '已从网站更新' : '程序自带') : '';
      return [a.version && t('版本 {v}', { v: a.version }), a.client && t('资料来自客户端 {c}', { c: a.client }), src,
              a.counts && t('{f} 个函数 · {e} 个事件 · {n} 个表', { f: a.counts.function, e: a.counts.event, n: a.counts.table })].filter(Boolean).join(' · ');
    },
    get docsChanges() {                        // what the newest client build changed in the manual
      const c = this.docs.about && this.docs.about.changes;
      return c ? t('{to} 相对 {frm}：新增 {a}、改动 {c}、删除 {r}', { to: c.to, frm: c.frm, a: c.added, c: c.changed, r: c.removed }) : '';
    },
    topicTitle(m) { return this.lang === 'en' ? (m.title_en || m.title) : m.title; },
    async docsSearch() {
      const q = this.docs.q.trim();
      if (!q) { this.docs.results = []; this.docs.counts = null; this.docs.total = 0; return; }
      this.docs.busy = true;
      try {
        const d = await this.api('/api/apidocs?limit=120&q=' + encodeURIComponent(q) + (this.docs.kind ? '&kind=' + this.docs.kind : '')
                                 + (this.docs.call ? '&call=' + this.docs.call : ''));
        if (this.docs.q.trim() === q) {
          this.docs.results = d.results || []; this.docs.counts = d.counts || null; this.docs.total = d.total || 0; this.docs.cursor = -1;
        }
      } catch (e) { this.say(t('搜索失败：') + e.message); }
      finally { this.docs.busy = false; }
    },
    get docsGroups() {                         // the results by kind, in the manual's order
      return ['function', 'event', 'table', 'usage'].map(k => ({ kind: k, rows: this.docs.results.filter(r => r.kind === k) })).filter(g => g.rows.length);
    },
    get docsFlat() { return this.docsGroups.flatMap(g => g.rows); },
    callCount(id) {                            // a call filter's count over every match of the query
      const c = this.docs.counts;
      if (!c) return '';
      return id === 'usable' ? c.ok + c.limited : c[id];
    },
    get docsCountText() {                      // how many match under the filters, and how many of them are listed
      if (this.docs.busy) return t('查找中…');
      const n = this.docs.call ? this.callCount(this.docs.call) : this.docs.total;
      const shown = this.docs.results.filter(r => r.kind !== 'usage').length;
      return shown < n ? t('{n} 条，列出前 {k} 条', { n, k: shown }) : t('{n} 条', { n });
    },
    docsMove(step) {                           // ↑ ↓ in the search box walk the results; Enter opens the one marked
      const rows = this.docsFlat;
      if (!rows.length) return;
      this.docs.cursor = Math.max(0, Math.min(rows.length - 1, this.docs.cursor + step));
      this.$nextTick(() => { const el = this.$refs.apilist && this.$refs.apilist.querySelector('li.cur'); if (el) el.scrollIntoView({ block: 'nearest' }); });
    },
    docsEnter() {
      const rows = this.docsFlat;
      if (this.docs.cursor >= 0 && rows[this.docs.cursor]) this.docsOpen(rows[this.docs.cursor]);
      else this.docsSearch().then(() => { if (this.docsFlat.length) this.docsOpen(this.docsFlat[0]); });
    },
    async docsOpen(r, back) {                  // a result, or an entry by name (a type, a function of the namespace)
      const name = typeof r === 'string' ? r : r.name;
      if (typeof r === 'object' && r.kind === 'usage') { this.docsShow(Object.assign({ raw: {} }, r), back); return; }
      try {
        const d = await this.api('/api/apidocs?name=' + encodeURIComponent(name));
        this.docsShow(d.candidates ? Object.assign({ raw: {}, name, sig: name, kind: 'function' }, typeof r === 'object' ? r : {},
                                                   { candidates: d.candidates }) : d, back);
      } catch (e) { this.say(e.message); }
    },
    docsShow(entry, back) {
      if (!back && this.docs.sel && this.docs.sel.name !== entry.name) this.docs.back.push(this.docs.sel.name);
      this.docs.sel = entry; this.docs.topic = null; this.docs.check = null;
      this.docs.open = Object.fromEntries(Object.entries(entry.types || {}).map(([k, v]) => [k, (v.fields || []).length <= 12]));
      this.tryReset();
      this.$nextTick(() => { const el = this.$refs.apidetail; if (el) el.scrollTop = 0; });
    },
    docsBack() { const name = this.docs.back.pop(); if (name) this.docsOpen(name, true); },
    async docsTopic(m) {
      try {
        this.docs.topic = await this.api('/api/apidocs?manual=' + encodeURIComponent(m.id) + '&lang=' + this.lang);
        this.docs.sel = null;
      } catch (e) { this.say(e.message); }
    },
    docsToggle(name) {                         // a type of the entry: its fields shown or hidden, scrolled to
      this.docs.open = Object.assign({}, this.docs.open, { [name]: !this.docs.open[name] });
      if (this.docs.open[name]) this.$nextTick(() => { const el = document.getElementById('ty-' + name); if (el) el.scrollIntoView({ block: 'nearest' }); });
    },
    onTypeClick(ev) {                          // a type name in the signature
      const a = ev.target.closest('[data-type]');
      if (!a) return;
      ev.preventDefault();
      if (!this.docs.open[a.dataset.type]) this.docsToggle(a.dataset.type);
      else this.$nextTick(() => { const el = document.getElementById('ty-' + a.dataset.type); if (el) el.scrollIntoView({ block: 'nearest' }); });
    },
    sigHtml(e) {                               // the signature, its type names that the entry carries as links to them
      if (!e) return '';
      const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      const ty = name => (e.types && e.types[name] ? '<a class="ty" href="#" data-type="' + esc(name) + '">' + esc(name) + '</a>' : '<span class="ty0">' + esc(name) + '</span>');
      const p = x => '<span class="pn">' + esc(x.n) + '</span>' + (x.nil ? '<span class="nil">?</span>' : '') + ': ' + ty(x.t)
                     + (x.def != null && x.def !== '' ? ' = ' + esc(x.def) : '');
      if (e.kind === 'function' && !e.candidates) {
        const dot = e.name.lastIndexOf('.');
        return (dot > 0 ? '<span class="ns">' + esc(e.name.slice(0, dot + 1)) + '</span>' : '') + '<b>' + esc(e.name.slice(dot + 1)) + '</b>('
               + (e.args || []).map(p).join(', ') + ')' + ((e.returns || []).length ? ' → ' + e.returns.map(p).join(', ') : '');
      }
      if (e.kind === 'event') return '<b>' + esc(e.name) + '</b>' + ((e.payload || []).length ? ': ' + e.payload.map(p).join(', ') : '');
      return esc(e.sig || e.name);
    },
    callText(e) {                              // what an addon may do with an entry, as a heading
      if (!e || !e.call || e.kind === 'table' || e.kind === 'usage') return '';
      const ev = e.kind === 'event';
      return t({ ok: ev ? '插件可以注册' : '插件可以调用', limited: ev ? '插件可以注册，但有限制' : '插件可以调用，但有限制',
                 protected: ev ? '受限事件：插件不能注册' : '受保护：插件不能调用' }[e.call] || '');
    },
    whyText(code, kind) {                      // one documentation field that limits it, for people
      if (code === 'HasRestrictions') return t(kind === 'event' ? WHY.HasRestrictionsEvent : WHY.HasRestrictions);
      if (WHY[code]) return t(WHY[code]);
      if (SECRET_WHEN[code]) return t('{when}返回机密值：读不到明文，不能比较、运算或存表。', { when: t(SECRET_WHEN[code]) });
      if (/^Secret/.test(code)) return t('在某些受限状态下返回机密值。');
      if (/^Require/.test(code)) return t('有前提条件，条件不满足时调用不起作用。');
      return '';
    },
    sinceText(s) { return s ? t(s.what === 'added' ? '{b} 新增' : '{b} 改动', { b: String(s.build || '').split('.').pop() }) : ''; },
    get docsSections() {                       // the tables under an entry: arguments, returns, payload, fields
      const s = this.docs.sel;
      if (!s) return [];
      const param = p => [p.nil ? t('可以为空') : '', p.def != null && p.def !== '' ? t('默认 {v}', { v: p.def }) : ''].filter(Boolean).join(t('，'));
      const out = [];
      if (s.args && s.args.length) out.push({ title: '参数', rows: s.args, third: '说明', cell: param });
      if (s.returns && s.returns.length) out.push({ title: '返回值', rows: s.returns, third: '说明', cell: param });
      if (s.payload && s.payload.length) out.push({ title: '载荷', rows: s.payload, third: '说明', cell: param });
      if (s.fields && s.fields.length) out.push({ title: s.type === 'Enumeration' ? '取值' : '字段', rows: s.fields, third: '值',
                                                 cell: p => p.v != null ? String(p.v) : (p.nil ? t('可以为空') : '') });
      return out;
    },
    async docsCheck() {                        // is it there in the running game? return type(...)
      const name = this.docs.sel && this.docs.sel.name;
      if (!name) return;
      const dot = name.indexOf('.');
      const expr = dot > 0 ? name.slice(0, dot) + ' and ' + name : name;
      this.docs.checking = true;
      try {
        const r = await this.api('/api/run', { body: { code: 'return type(' + expr + ')', timeout_ms: 8000 } });
        const kind = r.values && r.values[0];
        this.docs.check = kind === '"function"' || kind === 'function' ? { ok: true, text: t('游戏里有它（function）') }
                        : { ok: false, text: t('游戏里是 {v}：这个版本的客户端可能没有它', { v: kind || 'nil' }) };
      } catch (e) {
        this.docs.check = { ok: false, text: t('核对失败：') + e.message };
      } finally { this.docs.checking = false; }
    },

    // ---- 试一下: in the running game, through /api/run (a function, an enum) or /api/trace (an event)
    get tryKind() {
      const s = this.docs.sel;
      if (!s || s.candidates) return '';
      return s.kind === 'function' ? 'call' : s.kind === 'event' ? 'event' : s.kind === 'table' && s.type === 'Enumeration' ? 'enum' : '';
    },
    tryReset() {
      const s = this.docs.sel;
      this.tryit = { code: s ? this.tryTemplate(s) : '', busy: false, result: null, trace: null, seconds: 10 };
    },
    tryTemplate(s) {
      if (!s) return '';
      if (s.kind === 'function') return 'return ' + s.name + '(' + this.tryArgs(s) + ')';
      if (s.kind === 'table' && s.type === 'Enumeration') return 'return Enum.' + s.name;
      return '';
    },
    tryArgs(s) {                               // an example for each argument up to the first optional one: by its name, then its type
      const out = [];
      for (const a of s.args || []) {
        if (a.nil || (a.def != null && a.def !== '')) break;
        const T = s.types && s.types[a.t];
        const ex = EXAMPLES.find(([re]) => re.test(a.n) || re.test(a.t));
        out.push(T && T.type === 'Enumeration' && T.fields.length ? 'Enum.' + a.t + '.' + T.fields[0].n
                 : ex ? ex[1] : /^bool/i.test(a.t) ? 'false' : /^(number|int|uint|fileID|time|luaIndex|double|float)/i.test(a.t) ? '1'
                 : /string$/i.test(a.t) ? '""' : /^table/i.test(a.t) ? '{}' : /function/i.test(a.t) ? 'function() end' : 'nil');
      }
      return out.join(', ');
    },
    get tryButton() {
      if (this.tryit.busy) return t(this.tryKind === 'event' ? '监听中…' : '运行中…');
      return this.tryKind === 'event' ? t('监听 {s} 秒', { s: this.tryit.seconds }) : this.tryKind === 'enum' ? t('读取并对照') : t('运行（Ctrl+Enter）');
    },
    async tryRun() {
      const code = (this.tryit.code || '').trim();
      if (!code || this.tryit.busy || !this.linkOnline) return;
      this.tryit.busy = true; this.tryit.result = null;
      try {
        this.tryit.result = await this.api('/api/run', { body: { code, timeout_ms: 10000 } });
      } catch (e) {
        this.tryit.result = { ok: false, error: e.status === 504 ? t('超时：游戏没有在 10 秒内回话') : e.message };
      } finally { this.tryit.busy = false; }
    },
    async tryTrace() {
      const s = this.docs.sel;
      if (!s || this.tryit.busy || !this.linkOnline) return;
      this.tryit.busy = true; this.tryit.trace = null;
      try {
        this.tryit.trace = await this.api('/api/trace', { body: { seconds: this.tryit.seconds, events: [s.name], max_events: 50, args: 8 } });
      } catch (e) { this.tryit.trace = { error: e.message }; }
      finally { this.tryit.busy = false; }
    },
    tryToConsole() {                           // the snippet into 开发台's 运行 Lua
      this.run.code = this.tryit.code;
      this.devTool = 'run';
      this.go('dev');
    },
    get tryStatus() {
      const r = this.tryit.result;
      if (r && r.ok) return t('{ms} ms · job {job}', { ms: r.ms, job: r.job });
      if (!this.linkOnline) return t('游戏连上后才能运行');
      return '';
    },
    get tryValues() {                          // a run's values by the documented return names, tables opened into their fields
      const r = this.tryit.result, s = this.docs.sel;
      if (!r || !r.ok || !s) return [];
      const vals = r.values || [], rets = s.kind === 'function' ? (s.returns || []) : [];
      const together = vals.length === 1 && rets.length > 1 && /\n/.test(vals[0]);   // several values dumped as one text
      return vals.map((v, i) => {
        const ret = together ? null : rets[i];
        return { name: ret ? ret.n : together ? t('返回值') : '#' + (i + 1), type: ret ? ret.t : '', rows: this.luaRows(v, ret ? ret.t : '') };
      });
    },
    luaRows(text, typeName) {                  // a dumped value as rows {depth, key, text, type, cls}; the fields typed from the manual
      const src = String(text == null ? '' : text);
      if (!/^\s*\{/.test(src))                  // a value other than a table comes as tostring wrote it
        return [{ depth: 0, key: '', text: src, type: typeName || '', cls: /^-?\d/.test(src) ? 'num' : /^(true|false)$/.test(src) ? 'bool' : src === 'nil' ? 'nil' : 'str' }];
      const node = luaParse(src);
      if (!node) return [{ depth: 0, key: '', text: src, type: typeName || '', cls: 'raw' }];
      const types = (this.docs.sel && this.docs.sel.types) || {};
      const fieldType = (tn, key) => { const T = types[tn]; const f = T && (T.fields || []).find(x => x.n === key); return f ? f.t : ''; };
      const out = [];
      const walk = (n, depth, key, tn) => {
        if (n.t !== 'table') { out.push({ depth, key, text: n.text, type: tn || '', cls: n.t }); return; }
        out.push({ depth, key, text: n.entries.length ? '' : '{}', type: tn || '', cls: 'tbl' });
        fields(n, depth + 1, tn);
      };
      const fields = (n, depth, tn) => {
        for (const [k, v] of n.entries) walk(v, depth, k, tn ? fieldType(tn, k) : '');
        if (n.more) out.push({ depth, key: '', text: n.more, type: '', cls: 'more' });
      };
      if (node.t === 'table' && (node.entries.length || node.more)) fields(node, 0, typeName);   // the value's type is in its heading
      else walk(node, 0, '', typeName);
      return out;
    },
    get tryEnum() {                            // an enum read in the game against the manual: [{name, doc, game, same}]
      const r = this.tryit.result, s = this.docs.sel;
      if (!r || !r.ok || !s || this.tryKind !== 'enum') return null;
      const node = luaParse((r.values || [])[0] || '');
      if (!node || node.t !== 'table') return null;
      const game = Object.fromEntries(node.entries.map(([k, v]) => [k, v.text]));
      const rows = (s.fields || []).map(f => ({ name: f.n, doc: f.v == null ? '' : String(f.v), game: f.n in game ? game[f.n] : '—' }));
      for (const k of Object.keys(game)) if (!(s.fields || []).some(f => f.n === k)) rows.push({ name: k, doc: '—', game: game[k] });
      return rows.map(x => Object.assign(x, { same: x.doc === x.game }));
    },
    get tryEnumText() {                        // the comparison's verdict above its table: the values that differ named
      const rows = this.tryEnum;
      if (!rows) return '';
      const diff = rows.filter(x => !x.same);
      if (!diff.length) return t('{n} 项都和手册一致。', { n: rows.length });
      const names = diff.slice(0, 3).map(x => x.name + ' ' + x.doc + ' → ' + x.game).join(t('、')) + (diff.length > 3 ? '…' : '');
      return t('{n} 项和手册不一样（标黄的行）：{names}', { n: diff.length, names });
    },
    md(text) {                                 // the manual's little Markdown: escaped first, then headings, lists, code
      const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
      const out = [];
      let list = false;
      for (const line of String(text || '').split('\n')) {
        if (line.startsWith('- ')) {
          if (!list) { out.push('<ul>'); list = true; }
          out.push('<li>' + inline(line.slice(2)) + '</li>');
          continue;
        }
        if (list) { out.push('</ul>'); list = false; }
        if (line.startsWith('### ')) out.push('<h4>' + inline(line.slice(4)) + '</h4>');
        else if (line.startsWith('## ')) out.push('<h3>' + inline(line.slice(3)) + '</h3>');
        else if (line.trim()) out.push('<p>' + inline(line) + '</p>');
      }
      if (list) out.push('</ul>');
      return out.join('');
    },

    // ---- 新建插件 (scaffold.py through /api/new_addon)
    async newAddon() {
      this.na.busy = true;
      this.na.error = '';
      try {
        this.na.result = await this.api('/api/new_addon', { body: { name: this.na.name.trim(), title: this.na.title.trim() || null,
                                                                   notes: this.na.notes.trim(), template: this.na.template } });
        this.say('已创建 ' + this.na.result.name);
        this.loadAddons();
      } catch (e) {
        this.na.error = e.message;
      } finally {
        this.na.busy = false;
      }
    },
    async naLoad() {
      const name = this.na.result && this.na.result.name;
      try {
        const r = await this.api('/api/load', { body: { target: name } });
        const bad = (r.files || []).filter(x => !x.ok);
        this.say(bad.length ? '热加载有 ' + bad.length + ' 个文件出错，看开发台的日志' : '已热加载 ' + name);
      } catch (e) { this.say('热加载失败：' + e.message); }
    },
    naReveal() { return this.reveal(this.na.result.name); },
    async reveal(name) {
      try { await this.api('/api/reveal', { body: { name } }); }
      catch (e) { this.say('打开文件夹：' + e.message); }
    },
    get naPrompt() { return this.promptFor(this.na.result); },
    promptFor(r) {                             // the first thing to tell the agent about a new addon
      if (!r) return '';
      return '请先读 ' + r.path + '\\AGENTS.md。然后在插件 ' + r.name + ' 里实现：（在这里写你想要的功能）。'
           + '改完用 wuxian 的 load 热加载，用 logs 看报错、snap 截图确认效果；不确定的 API 先用 api_search 查。';
    },

    // ---- the agents (agents.py through /api/agents): one click registers this program with Claude Code, Codex, Cursor
    async loadAgents() {
      try {
        const d = await this.api('/api/agents');
        this.agents.list = d.hosts || [];
        this.agents.program = d.program || null;
        this.agents.manual = d.manual || {};
      } catch (e) {
        if (this.agents.list === null) this.agents.list = [];
        this.say('读取 Agent 失败：' + errText(e));
      }
    },
    async agentAction(h, action) {
      this.agents.busy = h.id + ':' + action;
      this.agents.note = Object.assign({}, this.agents.note, { [h.id]: null });
      try {
        const r = await this.api('/api/agents', { body: { action, host: h.id } });
        this.agents.list = this.agents.list.map(x => (x.id === r.id ? r : x));
        const done = { connect: '已接入', disconnect: '已断开', verify: '已检查' }[action];
        const note = r.verify ? { ok: r.verify.ok, text: r.verify.text } : { ok: true, text: done };
        this.agents.note = Object.assign({}, this.agents.note, { [h.id]: note });
        this.say(h.title + ' ' + done);
      } catch (e) {
        this.agents.note = Object.assign({}, this.agents.note, { [h.id]: { ok: false, text: errText(e) } });
      } finally {
        this.agents.busy = '';
      }
    },
    agentPill(h) {
      return { ok: ['ok', '已接入'], other: ['warn', '指向别的程序'], absent: ['off', '未接入'], missing: ['off', '未安装'],
               error: ['bad', '读不了设置'] }[h.state] || ['off', h.state];
    },
    get agentsShown() { return (this.agents.list || []).filter(h => h.state !== 'missing'); },
    get agentsMissing() { return (this.agents.list || []).filter(h => h.state === 'missing').map(h => h.title); },
    get agentsProgram() {
      const p = this.agents.program;
      return p ? [p.command].concat(p.args || []).join(' ') : '—';
    },

    // ---- 开始: the first-run wizard; every step is ticked from what the daemon reports
    startInit() {
      if (!this.doctor.busy) this.runDoctor();
      this.loadAgents();
      if (!this.start.gameInput) this.start.gameInput = this.settings.game_dir || '';
    },
    docCheck(id) { return this.doctor.checks.find(c => c.id === id) || null; },
    get gamePath() {
      const c = this.docCheck('game_dir');
      if (c && c.ok === true) return c.detail;
      const a = this.status && this.status.addons_dir;
      return a ? a.replace(/[\\/]Interface[\\/]AddOns[\\/]?$/i, '') : '';
    },
    get gameVersion() { const c = this.docCheck('client_version'); return c && c.ok !== null ? '客户端 ' + c.detail : ''; },
    get installChecks() { return this.doctor.checks.filter(c => /^addon:.*:installed$/.test(c.id) || c.id === 'mailbox'); },
    get stepGame() { const c = this.docCheck('game_dir'); return !!(c && c.ok === true) || !!(this.status && this.status.addons_dir); },
    get stepInstall() {                        // the self-check's word; before it answers, a link that is up says enough
      const cs = this.installChecks;
      return cs.length ? cs.every(c => c.ok === true) : this.linkOnline;
    },
    get startSteps() {
      return [['game', this.stepGame], ['install', this.stepInstall], ['enter', this.linkOnline],
              ['agent', !!(this.agents.list && this.agents.list.some(h => h.state === 'ok'))], ['first', this.start.first.loaded]];
    },
    get startDone() { return this.startSteps.filter(s => s[1]).length; },
    stepClass(id) {
      const steps = this.startSteps;
      const s = steps.find(x => x[0] === id);
      if (s && s[1]) return 'done';
      const first = steps.find(x => !x[1]);
      return first && first[0] === id ? 'current' : 'todo';
    },
    stepMark(id) {
      const i = this.startSteps.findIndex(x => x[0] === id);
      return this.startSteps[i] && this.startSteps[i][1] ? '✓' : String(i + 1);
    },
    async pickFolder() {
      try {
        const p = await window.pywebview.api.pick_folder(this.start.gameInput || this.gamePath || '');
        if (p) this.start.gameInput = p;
      } catch (e) { this.say('选择文件夹：' + errText(e)); }
    },
    async saveGameDir() {
      this.start.saving = true;
      this.start.gameNote = '';
      try {
        this.applySettings(await this.api('/api/settings', { body: { game_dir: this.start.gameInput.trim() } }));
        await this.runDoctor();
        if (this.stepGame) { this.start.gameEdit = false; this.say('已保存游戏目录'); }
        else this.start.gameNote = '这个文件夹里没有 Interface\\AddOns：请选《魔兽世界：无限》客户端的文件夹（_cn_beta_）。';
        this.refreshStatus();
      } catch (e) {
        this.start.gameNote = '保存失败：' + errText(e);
      } finally {
        this.start.saving = false;
      }
    },
    async startInstall() {
      this.start.installBusy = true;
      this.start.installNote = '';
      try {
        const r = await this.api('/api/install', { body: { clean: true } });
        this.start.installRestart = !!r.restart_for_link;
        if (r.available === false) this.start.installNote = r.detail || '安装器不可用';
        else this.start.installNote = '已安装 ' + (r.installed || []).length + ' 个文件。'
          + (r.restart_for_link ? '现在请完整退出游戏再启动：游戏只在启动时发现新插件，/reload 不够。' : '');
        await this.runDoctor();
      } catch (e) {
        this.start.installNote = '安装失败：' + errText(e);
      } finally {
        this.start.installBusy = false;
      }
    },
    async startFirst() {                       // 新建 (or the one made before), then a hot-load when the game is there
      const f = this.start.first;
      const name = f.name.trim();
      f.busy = true;
      f.error = '';
      try {
        try {
          f.result = await this.api('/api/new_addon', { body: { name, title: f.title.trim() || null, notes: '', template: 'basic' } });
        } catch (e) {
          if (!(e.status === 400 && /已经有叫/.test(errText(e)))) throw e;
          f.result = { name, path: (this.status && this.status.addons_dir ? this.status.addons_dir + '\\' : '') + name,
                       slash: '/' + name.toLowerCase(), files: [] };
        }
        if (this.linkOnline) {
          f.fromId = this.lastId;
          const r = await this.api('/api/load', { body: { target: name } });
          const bad = (r.files || []).filter(x => !x.ok);
          if (bad.length) throw new Error('热加载时 ' + bad.length + ' 个文件出错：' + (bad[0].error || '看开发台的日志'));
          f.loaded = true;
        }
        this.addons.list = null;                 // the 插件 page reads them again
      } catch (e) {
        f.error = errText(e);
      } finally {
        f.busy = false;
      }
    },
    get firstOutput() {                        // what the new addon printed in the game after the hot-load
      const f = this.start.first;
      if (!f.loaded || !f.result) return '';
      const e = this.logs.find(x => x.id > f.fromId && x.kind === 'OUT' && (x.text || '').includes(f.result.name));
      return e ? e.text.replace(/\|c[0-9a-fA-F]{8}|\|r/g, '').trim() : '';
    },
    async finishStart() {
      try { this.applySettings(await this.api('/api/settings', { body: { onboarded: true } })); }
      catch (e) { /* the page moves on; the next start opens 开始 again */ }
      this.go('status');
    },

    // ---- updates (updater.py through /api/update)
    get upd() { return (this.status && this.status.update) || null; },
    get updateText() {
      const u = this.upd;
      if (!u) return '—';
      if (!u.supported) return '不支持自动更新';
      const checked = u.checked ? '（' + this.fmtStamp(u.checked) + ' 检查）' : '';
      return { idle: '等待第一次检查', checking: '正在检查…', current: '已是最新' + checked, available: '有新版本 ' + u.latest,
               downloading: '正在下载 ' + u.latest + ' · ' + u.progress + '%', ready: u.latest + ' 已下载，重启后生效',
               error: u.error || '检查失败' }[u.state] || u.state;
    },
    get updateBanner() {
      const u = this.upd;
      if (!u || !u.supported) return '';
      if (u.state === 'available') return '无限工坊有新版本 ' + u.latest + '（当前 ' + u.current + '）';
      if (u.state === 'downloading') return '正在下载新版本 ' + u.latest + ' · ' + u.progress + '%';
      if (u.state === 'ready') return '新版本 ' + u.latest + ' 已下载，重启 App 即可更新';
      return '';
    },
    async updateAction(action) {
      this.updBusy = true;
      try {
        const s = await this.api('/api/update', { body: { action } });
        if (this.status) this.status.update = s;
        if (action === 'check') this.say(s.state === 'available' ? '有新版本 ' + s.latest : s.state === 'current' ? '已是最新版本' : (s.error || s.reason || '已检查'));
        if (action === 'apply') this.say('正在重启并更新…');
      } catch (e) {
        this.say('更新：' + e.message);
      } finally {
        this.updBusy = false;
      }
    },
    checkUpdate() { return this.updateAction('check'); },
    downloadUpdate() { return this.updateAction('download'); },
    applyUpdate() { return this.updateAction('apply'); },
    fmtSize(b) { return b ? (b / 1048576).toFixed(1) + ' MB' : '—'; },

    // ---- settings
    applySettings(s) {
      if (!s) return;
      this.settings = Object.assign({}, this.settings, s, { game_dir: s.game_dir || '' });
      this.form = { capture: this.settings.capture, game_dir: this.settings.game_dir,
                    autostart: !!this.settings.autostart, language: this.settings.language || 'auto' };
      this.applyLang(this.settings.language);
    },
    applyLang(setting) {                       // the page's words follow the setting at once
      const v = langOf(setting);
      if (Alpine.store('lang').v !== v) Alpine.store('lang').v = v;
      this.lang = v;
      document.documentElement.lang = v;
      document.title = t('无限工坊');
      if (this.docs.topic) this.docsTopic(this.docs.topic);   // the open chapter in the new language
    },
    async loadSettings() {
      try {
        this.applySettings(await this.api('/api/settings'));
        this.settingsLoaded = true;
      } catch (e) { /* defaults stay */ }
      if (this.settings.mode === 'player') this.migrateMode();
    },
    async migrateMode() {                      // the player mode is gone: old settings that still say so go developer
      if (this.migrating) return;
      this.migrating = true;
      try {
        this.applySettings(await this.api('/api/settings', { body: { mode: 'developer' } }));
        this.say('已切回开发模式');
        this.refreshStatus();
      } catch (e) {
        this.migrating = false;                  // the next status tries again
      }
    },
    onSettingsSaved(d) {
      if (!d.ok) {
        this.settingsNote = '保存失败：' + (d.status ? apiMessage(d.json, { status: d.status }) : (d.error || '无法连接守护进程'));
        return;
      }
      this.applySettings(d.json || Object.assign({}, this.form));
      this.settingsNote = '';
      this.say('设置已保存');
      this.refreshStatus();
    },

    // ---- connect page
    get conn() {
      /* port, token and the MCP URL come from /api/session and /api/status; the command that starts `wuxian mcp` from
         daemon.command ([exe, ...args before "mcp"]) or daemon.exe when the daemon says, else `wuxian` on PATH */
      const s = this.status || {};
      const d = s.daemon || {};
      const ses = this.session_ || {};
      const command = Array.isArray(d.command) && d.command.length ? d.command : [d.exe || ses.exe || 'wuxian'];
      return {
        port: d.port || ses.port || Number(location.port) || 80,
        mcpUrl: d.mcp_url || s.mcp_url || ses.mcp_url || (location.origin + '/mcp'),
        token: d.token || this.token || '<token>',
        exe: command[0],
        args: command.slice(1).concat(['mcp']),
      };
    },
    get snippets() {
      const c = this.conn;
      const exe = c.exe;
      const bearer = 'Bearer ' + c.token;
      const cmdExe = (/\s/.test(exe) ? '"' + exe + '"' : exe) + c.args.slice(0, -1).map(a => ' ' + a).join('');
      return [
        {
          id: 'claude-cmd', title: 'Claude Code · 命令', file: '在终端里运行其一',
          text: 'claude mcp add --transport stdio --scope user wuxian -- ' + cmdExe + ' mcp\n'
              + 'claude mcp add --transport http --scope user wuxian-http ' + c.mcpUrl + ' --header "Authorization: ' + bearer + '"',
        },
        {
          id: 'claude-json', title: 'Claude Code · .mcp.json', file: '项目根目录 .mcp.json',
          text: JSON.stringify({ mcpServers: {
            'wuxian': { type: 'stdio', command: exe, args: c.args },
            'wuxian-http': { type: 'http', url: c.mcpUrl, headers: { Authorization: bearer } },
          } }, null, 2),
        },
        {
          id: 'codex', title: 'Codex · config.toml', file: '%USERPROFILE%\\.codex\\config.toml',
          text: '# 或在终端：codex mcp add wuxian -- ' + cmdExe + ' mcp\n'
              + '[mcp_servers.wuxian]\n'
              + "command = '" + exe + "'\n"          // single quotes: TOML literal string, backslashes stay
              + 'args = ' + JSON.stringify(c.args) + '\n'
              + 'startup_timeout_sec = 30\n\n'
              + '[mcp_servers.wuxian_http]\n'
              + 'url = "' + c.mcpUrl + '"\n'
              + 'http_headers = { Authorization = "' + bearer + '" }',
        },
        {
          id: 'cursor', title: 'Cursor · mcp.json', file: '%USERPROFILE%\\.cursor\\mcp.json，或项目里的 .cursor\\mcp.json',
          text: JSON.stringify({ mcpServers: {
            'wuxian': { command: exe, args: c.args },
            'wuxian-http': { url: c.mcpUrl, headers: { Authorization: bearer } },
          } }, null, 2),
        },
      ];
    },
    cliHelp: CLI_HELP,
    mask(tok) { return tok && tok.length > 8 ? tok.slice(0, 4) + '…' + tok.slice(-4) : '••••'; },
    async copy(text, what) {
      try {
        await navigator.clipboard.writeText(text);
      } catch (e) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
      }
      this.say((what ? what + ' ' : '') + '已复制');
    },

    // ---- helpers
    say(text) {
      this.toast = text;
      if (this.toastTimer) clearTimeout(this.toastTimer);
      this.toastTimer = setTimeout(() => { this.toast = ''; }, 2200);
    },
    val(obj, path) {
      const v = path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
      return v == null || v === '' ? '—' : v;
    },
    show(v) { return typeof v === 'string' ? v : JSON.stringify(v); },
    firstLine(text) { return (text || '').split('\n')[0]; },
    shortPath(p) {                             // the last three parts of a Windows path: _cn_beta_\Interface\AddOns
      if (!p) return '—';
      const parts = String(p).split(/[\\/]/).filter(Boolean);
      return parts.length > 3 ? '…\\' + parts.slice(-3).join('\\') : p;
    },
    fmtTime(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      return [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, '0')).join(':');
    },
    fmtStamp(ts) {                             // Unix seconds from the game (time()): date and time
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      const p = n => String(n).padStart(2, '0');
      return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    },
    fmtDuration(s) {
      if (s == null) return '—';
      s = Math.floor(s);
      if (s < 60) return t('{s} 秒', { s });
      if (s < 3600) return t('{m} 分 {s} 秒', { m: Math.floor(s / 60), s: s % 60 });
      return t('{h} 小时 {m} 分', { h: Math.floor(s / 3600), m: Math.floor((s % 3600) / 60) });
    },
    upFor(ts) {                                // how long since: minutes, or hours and minutes
      const m = Math.floor(Math.max(0, this.now / 1000 - ts) / 60);
      if (m < 1) return t('不到 1 分钟');
      return m < 60 ? t('{m} 分钟', { m }) : t('{h} 小时 {m} 分', { h: Math.floor(m / 60), m: m % 60 });
    },
    ago(ts) {
      const s = Math.max(0, Math.floor(this.now / 1000 - ts));
      return s < 60 ? t('{s} 秒前', { s }) : t('{d}前', { d: this.fmtDuration(s) });
    },
  };
}

/* an error's words for the page: the daemon's message without its code */
function errText(e) {
  return (e && e.data && e.data.error && e.data.error.message) || (e && e.message) || String(e);
}

function apiMessage(data, r) {
  if (data && data.error && typeof data.error === 'object') return (data.error.code ? data.error.code + ': ' : '') + (data.error.message || '');
  if (data && typeof data.error === 'string') return data.error;
  if (data && data.raw) return 'HTTP ' + r.status + ' ' + String(data.raw).slice(0, 200);
  return 'HTTP ' + r.status;
}

function stateOf(elt) {
  const root = document.getElementById('app');
  return root && window.Alpine ? Alpine.$data(root) : null;
}

/* A form's fields as JSON: checkboxes become booleans, number inputs numbers (htmx would send form-encoded data). */
function formJson(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled || el.tagName === 'BUTTON') continue;
    if (el.type === 'checkbox') out[el.name] = el.checked;
    else if (el.type === 'number') out[el.name] = el.value === '' ? null : Number(el.value);
    else out[el.name] = el.value;
  }
  return out;
}

document.addEventListener('alpine:init', () => { Alpine.data('app', appState); });

document.addEventListener('DOMContentLoaded', () => {
  if (!window.htmx) return;
  htmx.defineExtension('json', {
    onEvent(name, evt) {
      if (name === 'htmx:configRequest') evt.detail.headers['Content-Type'] = 'application/json';
      return true;
    },
    encodeParameters(xhr, parameters, elt) {
      xhr.overrideMimeType('text/json');
      return JSON.stringify(formJson(elt));
    },
  });
});

document.addEventListener('htmx:configRequest', evt => {
  const st = stateOf();
  if (st && st.token) evt.detail.headers['Authorization'] = 'Bearer ' + st.token;
});
document.addEventListener('htmx:beforeRequest', evt => {
  evt.detail.elt.dispatchEvent(new CustomEvent('api-start', { bubbles: false }));
});
document.addEventListener('htmx:afterRequest', evt => {       // fires for 2xx, 4xx/5xx and network errors (status 0) alike
  const xhr = evt.detail.xhr;
  let json = null;
  try { json = xhr.responseText ? JSON.parse(xhr.responseText) : null; } catch (e) { json = { raw: xhr.responseText }; }
  const detail = { ok: xhr.status >= 200 && xhr.status < 400, status: xhr.status, json, error: xhr.status ? '' : '无法连接守护进程' };
  evt.detail.elt.dispatchEvent(new CustomEvent('api-done', { detail, bubbles: false }));
});

/* a value as Agent.lua's Dump writes it: "text" (%q), numbers, true / false / nil, <Type name>, <cycle>, {}, {... N entries}, and tables
   {\n  key = value,\n ...} (keys as names, ["text"] or [number]; "... N more" after 50) → {t: 'table', entries: [[key, node]], more} |
   {t: 'str' | 'num' | 'bool' | 'nil' | 'other', text}; null for anything else (a dump cut at the size limit) */
function luaParse(src) {
  const s = String(src == null ? '' : src);
  let i = 0;
  const ws = () => { while (i < s.length && /\s/.test(s[i])) i++; };
  const str = () => {                          // a %q string: \" \\ a backslash before a newline, \r, \0 and \ddd
    let out = '';
    i++;
    while (i < s.length && s[i] !== '"') {
      if (s[i] === '\\') {
        const c = s[i + 1];
        const d = /^\d{1,3}/.exec(s.slice(i + 1));
        if (d) { out += String.fromCharCode(+d[0]); i += 1 + d[0].length; }
        else { out += c === 'n' ? '\n' : c === 'r' ? '\r' : c === 't' ? '\t' : c; i += 2; }
      } else out += s[i++];
    }
    if (s[i] !== '"') throw new Error('string');
    i++;
    return out;
  };
  const value = () => {
    ws();
    if (s[i] === '"') return { t: 'str', text: JSON.stringify(str()) };
    if (s[i] === '{') {
      if (s.startsWith('{}', i)) { i += 2; return { t: 'table', entries: [] }; }
      const many = /^\{\.\.\. \d+ entries\}/.exec(s.slice(i));
      if (many) { i += many[0].length; return { t: 'other', text: many[0] }; }
      i++;
      const entries = [];
      let more = '';
      for (;;) {
        ws();
        if (s[i] === '}') { i++; break; }
        const m = /^\.\.\. \d+ more/.exec(s.slice(i));
        if (m) { more = m[0]; i += m[0].length; continue; }
        let key;
        if (s[i] === '[') {
          i++; ws();
          if (s[i] === '"') key = str();
          else { const n = /^[^\]]+/.exec(s.slice(i)); if (!n) throw new Error('key'); key = n[0].trim(); i += n[0].length; }
          ws();
          if (s[i] !== ']') throw new Error('key');
          i++;
        } else {
          const n = /^[A-Za-z_]\w*/.exec(s.slice(i));
          if (!n) throw new Error('key');
          key = n[0]; i += n[0].length;
        }
        ws();
        if (s[i] !== '=') throw new Error('=');
        i++;
        entries.push([key, value()]);
        ws();
        if (s[i] === ',') i++;
      }
      return { t: 'table', entries, more };
    }
    if (s[i] === '<') { const n = /^<[^>]*>/.exec(s.slice(i)); if (!n) throw new Error('<'); i += n[0].length; return { t: 'other', text: n[0] }; }
    const n = /^[^,\s}]+/.exec(s.slice(i));
    if (!n) throw new Error('value');
    i += n[0].length;
    const w = n[0];
    return { t: w === 'true' || w === 'false' ? 'bool' : w === 'nil' ? 'nil' : /^-?[\d.]/.test(w) ? 'num' : 'other', text: w };
  };
  try { const v = value(); ws(); return i === s.length ? v : null; } catch (e) { return null; }
}
