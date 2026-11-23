"""The version strip and the rows of the 扩展 page reading the daemon's judge (status.versions, compat.py): the strip says
the next step in one line with one button (the App's updater, the catalog's install, or nothing for a restart) and how many
more there are; each row's pill and 状态 come from its item (blocked, pending, update, ahead) before how it runs; 全部更新
installs the framework first and leaves out what the App cannot install; an extension too old for the framework's floor
(old) says so, its button the catalog's update or none. Without versions (an older daemon) the old notices stand. app.js
run under Node (skipped without it)."""
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
globalThis.LANG = 'zh-CN';
globalThis.Alpine = { store: () => ({ v: globalThis.LANG }) };
"""

HARNESS = r"""
const st = appState();
const calls = [];
st.api = async (path, opts) => { calls.push([path, opts && opts.body]); return { result: { version: 'x' } }; };
st.loadExtensions = async () => { calls.push(['loaded']); };
st.loadAccess = () => {};
st.refreshStatus = () => {};
st.downloadUpdate = () => { calls.push(['download']); };
st.go = (p) => { calls.push(['go', p]); st.page = p; };
const pkg = (id, folder, title, state, more) => Object.assign({ id, folder, title: { zh: title, en: title }, version: '0.7.1',
  there: state !== 'available', installed: '0.7.0', state, extensions: [], missing: [] }, more);
st.ext.list = [pkg('wuxiankit', 'WuxianKit', '扩展框架', 'update'), pkg('wuxiankit-guides', 'WuxianKit_Guides', '扩展框架 · 攻略', 'current'),
  pkg('wuxiankit-atlas', 'WuxianKit_Atlas', '扩展框架 · 地图册', 'update'),
  pkg('wuxiankit-far', 'WuxianKit_Far', '扩展框架 · 远方', 'needs_app', { min_app: '0.11.0' })];
st.access.data = { kit_state: 'running', servers: [], kit: { version: '0.7.0', source: 'game', newer: false, extensions: [] } };
st.lx.list = [{ id: 'herald', title: '播报', version: '0.1.0', enabled: true, state: 'stopped', approval: 'ok', allowed: true,
  origin: 'home', capabilities: [], options: [], addons: [], unmet: [] }];
const items = () => [
  { id: 'workshop', kind: 'workshop', title: '无限工坊', running: '0.10.0', latest: '0.10.1', state: 'update', action: 'update_app' },
  { id: 'wowbridge', kind: 'bridge', title: 'WoWBridge', folder: 'WoWBridge', running: '0.10.0', installed: '0.10.1', state: 'pending', action: 'restart_game' },
  { id: 'wuxiankit-atlas', kind: 'addon', title: '地图册', folder: 'WuxianKit_Atlas', installed: '0.7.0', latest: '0.7.1', state: 'update', action: 'update' },
  { id: 'wuxiankit', kind: 'kit', title: '扩展框架', folder: 'WuxianKit', running: '0.7.0', installed: '0.7.0', latest: '0.7.1',
    state: 'update', action: 'update', needs: { workshop: '0.10.1' } },
  { id: 'wuxiankit-guides', kind: 'addon', title: '任务指引适配', folder: 'wuxiankit_guides', running: null, installed: '0.2.0',
    state: 'blocked', unmet: [{ name: 'WuxianKit', needs: '0.7.1', has: '0.7.0' }], action: 'update', via: 'wuxiankit' },
  { id: 'wuxiankit-far', kind: 'addon', folder: 'WuxianKit_Far', state: 'update', action: 'update', latest: '0.9.0' },
  { id: 'herald', kind: 'local', title: '播报', state: 'blocked', unmet: [{ name: 'workshop', needs: '0.10.1', has: '0.10.0' }], action: 'update_app' },
  { id: 'mcp', kind: 'agent', title: 'Agent 连接', state: 'pending', action: 'restart_agent' },
];
const status = (next, list) => ({ daemon: {}, game: {}, link: {}, update: { supported: true, state: 'available', latest: '0.10.1', current: '0.10.0' },
  versions: next === undefined ? undefined : { next, items: list || items() } });
const strip = () => ({ cls: st.versCls, title: st.versNext ? st.versTitle(st.versNext.it) : null, what: st.versWhat, step: st.versStep,
  btn: st.versBtn && st.versBtn.text, more: st.versMore });
const sel = (key) => { st.xp.sel = key; return { state: st.xpState.text, cls: st.xpState.cls, ver: st.xpVer.pill }; };
const catalogRow = (key) => st.xpCatalog.find((c) => c.key === key);
const badge = (key) => { const b = st.xpBadge(catalogRow(key)); return b && b.text; };
const out = {};
(async () => {
  st.status = status({ severity: 'blocked', action: 'update_app', id: 'wuxiankit-guides', text: 'English for agents' });
  out.blocked = strip();
  out.badges = { kit: st.xpKitBadge && st.xpKitBadge.text, guides: badge('c:wuxiankit-guides'), atlas: badge('c:wuxiankit-atlas'),
    far: badge('c:wuxiankit-far') };
  out.kit = sel('kit');
  out.guides = sel('c:wuxiankit-guides');
  out.atlas = sel('c:wuxiankit-atlas');
  out.local = { pill: st.lxState(st.lx.list[0], true).text, state: st.lxState(st.lx.list[0]).text, blocked: st.lxBlocked(st.lx.list[0]),
    unmet: st.lxUnmet(st.lx.list[0]) };
  out.plan = st.versUpdates.map((p) => p.id);
  await st.versAct();
  out.actApp = calls.splice(0);
  LANG = 'en';
  out.english = strip();
  LANG = 'zh-CN';

  st.status = status({ severity: 'pending', action: 'restart_game', id: 'wowbridge' });
  out.restart = strip();
  st.status = status({ severity: 'pending', action: 'restart_agent', id: 'mcp' });
  out.agent = strip();
  st.status = status({ severity: 'update', action: 'update', id: 'wuxiankit' });
  out.update = strip();
  await st.versAct();
  out.actUpdate = calls.splice(0);
  st.status = status({ severity: 'update', action: 'update', id: 'wuxiankit-guides', via: 'wuxiankit' });
  out.via = strip();
  st.status = status({ severity: 'blocked', action: 'none', id: 'wuxiankit-guides' });
  out.none = strip();

  const pend = items().map((x) => x.id === 'herald' ? Object.assign(x, { state: 'pending', unmet: [], action: 'restart_game' })
    : x.id === 'wuxiankit-guides' ? Object.assign(x, { state: 'pending', unmet: [], installed: '0.2.1', running: '0.2.0', action: 'restart_game' })
    : x.id === 'wuxiankit' ? Object.assign(x, { state: 'ahead', action: 'none' }) : x);
  st.status = status({ severity: 'update', action: 'update', id: 'wuxiankit-atlas' }, pend);
  out.pending = { local: st.lxState(st.lx.list[0], true).text, blocked: st.lxBlocked(st.lx.list[0]), guides: badge('c:wuxiankit-guides'),
    guidesState: sel('c:wuxiankit-guides').state, kit: st.xpKitBadge && st.xpKitBadge.text, kitVer: sel('kit').ver, plan: st.versUpdates.map((p) => p.id) };

  st.status = status({ severity: 'update', action: 'update', id: 'wuxiankit' });
  await st.xpUpdateAll();
  out.all = { installs: calls.filter((c) => c[0] === '/api/extensions').map((c) => c[1].id), note: st.ext.note, busy: st.ext.busy };
  calls.splice(0);
  st.api = async (path, opts) => { if (opts.body.id === 'wuxiankit-atlas') throw new Error('needs_kit'); return { result: {} }; };
  await st.xpUpdateAll();
  out.allFailed = st.ext.note;

  const OLD = { built: '0.5.0', floor: '0.6.0' };
  const olds = () => items().map((x) => x.id === 'wuxiankit-atlas' ? Object.assign(x, { state: 'blocked', old: OLD, action: 'update' })
    : x.id === 'herald' ? Object.assign(x, { state: 'blocked', unmet: [], old: OLD, action: 'none' }) : x);
  st.status = status({ severity: 'blocked', action: 'update', id: 'wuxiankit-atlas', target: 'wuxiankit-atlas' }, olds());
  out.tooOld = { strip: strip(), badge: badge('c:wuxiankit-atlas'), state: sel('c:wuxiankit-atlas').state };
  LANG = 'en';
  out.tooOldEnglish = { strip: strip(), badge: badge('c:wuxiankit-atlas') };
  LANG = 'zh-CN';
  st.status = status({ severity: 'blocked', action: 'none', id: 'herald' }, olds());
  out.tooOldLocal = { strip: strip(), pill: st.lxState(st.lx.list[0], true).text, state: st.lxState(st.lx.list[0]).text,
    unmet: st.lxUnmet(st.lx.list[0]), blocked: st.lxBlocked(st.lx.list[0]) };
  LANG = 'en';
  out.tooOldLocalEnglish = strip().step;
  LANG = 'zh-CN';
  st.status = status({ severity: 'blocked', action: 'none', id: 'herald' },
    olds().map((x) => x.id === 'herald' ? Object.assign(x, { unmet: [{ name: 'workshop', needs: '0.10.1', has: '0.10.0' }] }) : x));
  out.tooOldUnmet = { strip: strip(), pill: st.lxState(st.lx.list[0], true).text, blocked: st.lxBlocked(st.lx.list[0]) };

  st.status = status(null);
  out.nothing = { next: st.versNext, more: st.versMore, kit: st.xpKitBadge && st.xpKitBadge.text };

  st.status = status(undefined);                       // an older daemon: the separate notices
  st.status.kit = { version: '0.6.0', outdated: { needs: '0.7.0', has: '0.6.0' } };
  out.old = { next: st.versNext, vers: st.vers, kit: st.xpKitBadge && st.xpKitBadge.text, kitState: sel('kit').state,
    far: badge('c:wuxiankit-far'), atlas: badge('c:wuxiankit-atlas'), plan: st.versUpdates, local: st.lxState(st.lx.list[0], true).text };
  st.status.versions = { next: { action: 'update' }, items: 'bad' };             // a shape it does not know: nothing
  out.bad = { next: st.versNext, vers: st.vers };

  st.status.versions = { next: null, items: [] };      // the daemon's judge failed: the separate notices, as without it
  out.failed = { vers: st.vers, notice: !!(st.kitOutdated && !st.vers), kit: st.xpKitBadge && st.xpKitBadge.text,
    kitState: sel('kit').state, far: badge('c:wuxiankit-far') };
  st.status.versions = { next: null, items: items().filter((x) => x.kind !== 'workshop') };
  out.noWorkshop = st.vers;

  // a catalog update that needs a newer framework (needs_kit) or App (needs_app) first: no button, the reason beside it
  st.ext.list = st.ext.list.concat([
    pkg('wuxiankit-nk', 'WuxianKit_Nk', '扩展框架 · 近', 'needs_kit', { needs: { WuxianKit: '0.7.1' } }),
    pkg('wuxiankit-hi', 'WuxianKit_Hi', '扩展框架 · 高', 'needs_kit', { needs: { WuxianKit: '0.8.0' } }),
    pkg('wuxiankit-new', 'WuxianKit_New', '扩展框架 · 新', 'needs_kit', { there: false, installed: null, needs: { WuxianKit: '0.7.1' } })]);
  const more = [
    { id: 'wuxiankit-nk', kind: 'addon', folder: 'WuxianKit_Nk', installed: '0.7.0', latest: '0.7.1', state: 'update', action: 'update' },
    { id: 'wuxiankit-hi', kind: 'addon', folder: 'WuxianKit_Hi', installed: '0.7.0', latest: '0.7.1', state: 'update', action: 'update' }];
  st.status = status({ severity: 'update', action: 'update', id: 'wuxiankit' }, items().concat(more));
  const row = (key) => { const v = sel(key); return { badge: badge(key), pill: v.ver, cls: st.xpVer.cls, wait: st.extWait(catalogRow(key).pkg) }; };
  out.needs = { nk: row('c:wuxiankit-nk'), fresh: row('c:wuxiankit-new'), far: row('c:wuxiankit-far'),
    plan: st.versUpdates.map((p) => p.id), more: st.versMore };
  LANG = 'en';
  out.needsEnglish = row('c:wuxiankit-nk');
  LANG = 'zh-CN';
  st.status = status({ severity: 'update', action: 'update', id: 'wuxiankit-nk' },
    items().filter((x) => x.id !== 'wuxiankit' && x.id !== 'wuxiankit-guides').concat(more));
  out.needsNoKit = st.versUpdates.map((p) => p.id);    // no framework update in the plan: the needs_kit ones wait

  // the workshop needs a newer framework than the game runs: the framework's row says so, not 运行中
  const wants = () => items().map((x) => x.id === 'workshop' ? Object.assign(x, { state: 'blocked', action: 'update', via: 'wuxiankit',
    unmet: [{ name: 'WuxianKit', needs: '0.7.1', has: '0.7.0' }] }) : x.id === 'wuxiankit' ? Object.assign(x, { state: 'ok', action: 'none' }) : x);
  st.status = status({ severity: 'blocked', action: 'update', id: 'workshop', via: 'wuxiankit' }, wants());
  out.kitNeeded = { badge: st.xpKitBadge.text, cls: st.xpKitBadge.cls, state: sel('kit').state, stateCls: st.xpState.cls };

  // a local extension short of the framework only: said, its 载入 stays; one listed as local:<id> is found
  const kitShort = (id) => items().map((x) => x.id === 'herald' ? Object.assign(x, { id, unmet: [{ name: 'WuxianKit', needs: '0.7.1', has: '0.7.0' }],
    action: 'update', via: 'wuxiankit' }) : x);
  st.status = status({ severity: 'blocked', action: 'update', id: 'local:herald', via: 'wuxiankit' }, kitShort('local:herald'));
  out.lxKit = { blocked: st.lxBlocked(st.lx.list[0]), unmet: st.lxUnmet(st.lx.list[0]), pill: st.lxState(st.lx.list[0], true).text };
  const pendErr = items().map((x) => x.id === 'herald' ? Object.assign(x, { state: 'pending', unmet: [], action: 'restart_game' }) : x);
  st.status = status({ severity: 'pending', action: 'restart_game', id: 'herald' }, pendErr);
  st.lx.list[0].error = 'manifest.json: bad';
  out.lxErr = st.lxState(st.lx.list[0], true).text;
  st.lx.list[0].error = null;

  // the effect of an update: a new .toc, read when the game starts
  st.api = async () => ({ result: { version: '0.7.1' } });
  await st.extAct(st.ext.list.find((p) => p.id === 'wuxiankit-atlas'), 'install');
  out.updated = { note: st.ext.note, reload: st.ext.reload };
  st.status = status({ severity: 'update', action: 'update', id: 'wuxiankit' });
  st.api = async () => { throw new Error('offline'); };
  await st.xpUpdateAll();
  out.noneUpdated = st.ext.note;
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class Strip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((STATIC_DIR / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;" + HARNESS, capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_blocked_follows_the_chain_to_the_app(self):
        self.assertEqual(self.out["blocked"], {"cls": "bad", "title": "扩展框架 · 攻略", "what": "需要扩展框架 0.7.1 以上",
                                               "step": "请先更新无限工坊", "btn": "更新无限工坊", "more": 4})
        self.assertEqual(self.out["actApp"], [["download"]])          # the App's own updater, its update known
        self.assertEqual(self.out["english"], {"cls": "bad", "title": "扩展框架 · 攻略", "what": "Needs WuxianKit 0.7.1 or later",
                                               "step": "Update Wuxian Workshop first", "btn": "Update Wuxian Workshop", "more": 4})

    def test_restarts_have_no_button(self):
        self.assertEqual(self.out["restart"], {"cls": "warn", "title": "WoWBridge", "what": "已安装 0.10.1，游戏中仍是 0.10.0",
                                               "step": "完整重启游戏后生效", "btn": None, "more": 5})
        self.assertEqual(self.out["agent"], {"cls": "warn", "title": "Agent 连接", "what": "运行的是旧版本",
                                             "step": "请在 Agent 中重启会话", "btn": None, "more": 5})

    def test_an_update_installs_the_catalog_entry(self):
        self.assertEqual(self.out["update"], {"cls": "", "title": "扩展框架", "what": "可更新到 0.7.1，当前 0.7.0", "step": "",
                                              "btn": "更新到 0.7.1", "more": 4})
        self.assertEqual(self.out["actUpdate"][0], ["/api/extensions", {"action": "install", "id": "wuxiankit"}])
        self.assertEqual(self.out["via"]["step"], "请先更新扩展框架")      # another item's update fixes this one
        self.assertEqual(self.out["via"]["btn"], "更新到 0.7.1")
        self.assertEqual((self.out["none"]["step"], self.out["none"]["btn"]), ("暂无满足要求的版本", None))

    def test_rows_read_their_item(self):
        self.assertEqual(self.out["badges"], {"kit": "可更新", "guides": "需更新扩展框架", "atlas": "可更新", "far": "需更新无限工坊"})
        self.assertEqual(self.out["kit"], {"state": "运行中", "cls": "ok", "ver": "可更新到 0.7.1"})
        self.assertEqual((self.out["guides"]["state"], self.out["guides"]["cls"]), ("需要扩展框架 0.7.1 以上", "bad"))
        self.assertEqual(self.out["atlas"]["ver"], "可更新到 0.7.1")
        self.assertEqual(self.out["local"], {"pill": "需更新无限工坊", "state": "需要无限工坊 0.10.1 以上", "blocked": True,
                                             "unmet": "需要无限工坊 0.10.1 以上"})
        p = self.out["pending"]
        self.assertEqual((p["local"], p["blocked"], p["guides"], p["kit"], p["kitVer"]),
                         ("重启游戏后生效", False, "重启游戏后生效", "版本较新", "已安装的版本较新"))
        self.assertEqual(p["guidesState"], "已安装 0.2.1，完整重启游戏后生效")

    def test_too_old_for_the_floor(self):
        """old: its own pill and line; the catalog's update its button, else none and the author's"""
        line = "扩展版本过旧：按扩展框架 0.5.0 制作，当前框架支持 0.6.0 及以上的扩展"
        self.assertEqual(self.out["tooOld"], {"strip": {"cls": "bad", "title": "扩展框架 · 地图册", "what": line, "step": "",
                                                        "btn": "更新到 0.7.1", "more": 4},
                                              "badge": "需更新扩展", "state": line})
        self.assertEqual(self.out["tooOldLocal"], {"strip": {"cls": "bad", "title": "播报", "what": line,
                                                             "step": "需由扩展作者更新", "btn": None, "more": 4},
                                                   "pill": "需更新扩展", "state": line, "unmet": line, "blocked": False})
        self.assertEqual(self.out["tooOldEnglish"], {
            "strip": {"cls": "bad", "title": "扩展框架 · 地图册",
                      "what": "Extension too old: made for WuxianKit 0.5.0; this WuxianKit runs extensions made for 0.6.0 or later",
                      "step": "", "btn": "Update to 0.7.1", "more": 4},
            "badge": "Extension needs an update"})
        self.assertEqual(self.out["tooOldLocalEnglish"], "Its author needs to update it")
        self.assertEqual(self.out["tooOldUnmet"], {"strip": {"cls": "bad", "title": "播报", "what": "需要无限工坊 0.10.1 以上",
                                                             "step": "暂无满足要求的版本", "btn": None, "more": 4},
                                                   "pill": "需更新无限工坊", "blocked": True})

    def test_update_all(self):
        """the framework first though listed after; one entry for the items it fixes; needs_app left out"""
        self.assertEqual(self.out["plan"], ["wuxiankit", "wuxiankit-atlas"])
        self.assertEqual(self.out["pending"]["plan"], ["wuxiankit-atlas"])     # one: no 全部更新
        self.assertEqual(self.out["all"], {"installs": ["wuxiankit", "wuxiankit-atlas"], "note": "已更新 2 项，完整重启游戏后生效",
                                           "busy": ""})
        self.assertEqual(self.out["allFailed"], "已更新 1 项，完整重启游戏后生效；1 项未能更新：扩展框架 · 地图册：needs_kit")

    def test_judge_failed(self):
        """no items (the daemon's judge failed) or no workshop item: as without versions"""
        self.assertEqual(self.out["failed"], {"vers": None, "notice": True, "kit": "需更新", "kitState": "扩展框架需要更新到 0.7.0",
                                              "far": "需更新无限工坊"})
        self.assertIsNone(self.out["noWorkshop"])

    def test_update_needs_a_newer_app_or_framework(self):
        """the judge's update of a catalog entry the App cannot install yet: what comes first, no 可更新, the reason by the
        buttons; 全部更新 takes a needs_kit one only after a framework update that meets it"""
        n = self.out["needs"]
        self.assertEqual(n["nk"], {"badge": "需更新扩展框架", "pill": "需要扩展框架 0.7.1 以上", "cls": "bad",
                                   "wait": "需要扩展框架 0.7.1 以上"})
        self.assertEqual(n["fresh"], {"badge": "需更新扩展框架", "pill": "需要扩展框架 0.7.1 以上", "cls": "bad",
                                      "wait": "需要扩展框架 0.7.1 以上"})              # not installed: the reason, not 未安装
        self.assertEqual(n["far"], {"badge": "需更新无限工坊", "pill": "需要无限工坊 0.11.0 以上", "cls": "bad",
                                    "wait": "需要无限工坊 0.11.0 以上"})
        self.assertEqual(n["plan"], ["wuxiankit", "wuxiankit-atlas", "wuxiankit-nk"])    # -hi needs 0.8.0, far the App
        self.assertEqual(n["more"], 6)                       # the 扩展 page's items only (not the App, WoWBridge, agents)
        self.assertEqual(self.out["needsEnglish"]["badge"], "Needs a newer WuxianKit")
        self.assertEqual(self.out["needsNoKit"], ["wuxiankit-atlas"])

    def test_workshop_needs_a_newer_framework(self):
        self.assertEqual(self.out["kitNeeded"], {"badge": "需更新", "cls": "bad", "state": "扩展框架需要更新到 0.7.1",
                                                 "stateCls": "bad"})

    def test_local_short_of_the_framework_loads(self):
        """only a workshop too old blocks 载入 (what the daemon refuses); the framework is said; local:<id> is its item"""
        self.assertEqual(self.out["lxKit"], {"blocked": False, "unmet": "需要扩展框架 0.7.1 以上", "pill": "需更新扩展框架"})
        self.assertEqual(self.out["lxErr"], "清单有误")       # a manifest error before 重启游戏后生效

    def test_update_takes_a_full_restart(self):
        self.assertEqual(self.out["updated"], {"note": "已更新 扩展框架 · 地图册 到 0.7.1，完整重启游戏后生效", "reload": False})
        self.assertEqual(self.out["noneUpdated"], "未能更新：扩展框架：offline；扩展框架 · 地图册：offline")      # none: no 已更新 0 项

    def test_without_versions_the_old_notices_stand(self):
        self.assertEqual(self.out["nothing"], {"next": None, "more": 0, "kit": "可更新"})
        old = self.out["old"]
        self.assertEqual((old["next"], old["vers"], old["plan"]), (None, None, []))
        self.assertEqual((old["kit"], old["kitState"]), ("需更新", "扩展框架需要更新到 0.7.0"))
        self.assertEqual((old["far"], old["atlas"], old["local"]), ("需更新无限工坊", "可更新", "未运行"))
        self.assertEqual(self.out["bad"], {"next": None, "vers": None})


class Template(unittest.TestCase):
    """index.html: the strip in 概览's band and over 扩展's list, what it calls is in app.js, the old note only without versions"""

    def setUp(self):
        self.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def page(self, name):
        start = self.html.index("<section class=\"page\" x-t x-show=\"page === '%s'\">" % name)
        return self.html[start:self.html.index("</section>", start)]

    def test_the_strips(self):
        ov, ext = self.page("status"), self.page("extensions")
        self.assertLess(ov.index('class="vs-strip"'), ov.index('class="ov-vitals"'))        # inside the band, over its readings
        self.assertIn("go('extensions')", ov[ov.index('class="vs-strip"'):ov.index('class="ov-vitals"')])
        self.assertIn('class="vs-strip vs-page"', ext)
        self.assertIn('@click="xpUpdateAll()">全部更新</button>', ext)
        self.assertIn('x-show="kitOutdated && !vers"', ext)
        for name in ("versNext", "versCls", "versTitle", "versWhat", "versStep", "versMore", "versBtn", "versAct", "versUpdates",
                     "xpUpdateAll", "vers"):
            self.assertRegex(self.js, r"\n    (?:get |async )?%s\b" % name, name)
        self.assertNotIn("next.text", re.sub(r"//.*", "", self.js))                         # the agents' English is not shown


if __name__ == "__main__":
    unittest.main()
