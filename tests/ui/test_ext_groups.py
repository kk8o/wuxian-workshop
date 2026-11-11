"""The 扩展 page's list, in two groups by who brings an extension: 内置 (the workshop's: WuxianKit's own, those of its own in
an addon beside it, the catalog's packages whether in the game or not) and 已添加 (what other addons in the game folder
give WuxianKit, running or only found there, and the local ones, by title). A local extension and what the game runs from
its game addons are one row, which says where it runs (游戏 / 本机 / 游戏 · 本机); its detail lists the game's tools under
能力 too. What the game folder holds is read from its .toc, so it shows with the game off. app.js run under Node (skipped
without it)."""
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
const st = appState();
const tool = (name, label) => ({ name, cap: name.slice(3).replace('_', '.'), kind: 'see', label, title: name });
const pkg = (id, folder, title, there, n) => ({ id, folder, title: { zh: title, en: title }, version: '1.0.0', there,
  installed: there ? '1.0.0' : null, state: there ? 'current' : 'available', extensions: [{ id, tools: n }] });
st.ext.list = [pkg('wuxiankit', 'WuxianKit', '扩展框架', true, 9), pkg('wuxiankit-guides', 'WuxianKit_Guides',
  '扩展框架 · 攻略', true, 2), pkg('wuxiankit-atlas', 'WuxianKit_Atlas', '扩展框架 · 地图册', false, 3)];
const DIR = 'D:\\Game\\Interface\\AddOns';
const det = (id, folder, title, more) => Object.assign({ from: 'game', id, title, version: '0.4.1', notes: '', author: '',
  homepage: '', package: null, where: 'game', risk: 'game', official: false, addons_dir: DIR, local: null, executables: [],
  agent: { docs: null, skills: [], apps: [], adds: [] }, files: 3, size: 900,
  game: [Object.assign({ folder, title, notes: '', version: '0.4.1', author: '', needs: [], path: DIR + '\\' + folder },
    (more && more.addon) || {})] }, more && more.top);
st.ext.detected = [
  det('route', 'WuxianKit_Route', '扩展框架 · 路线', { top: { official: true } }),      // one of WuxianKit's own, running
  det('scout', 'WuxianKit_Scout', '扩展框架 · 侦察', { top: { official: true } }),      // one of its own the game does not run
  det('layout', 'WuxianLayout', '界面布局助手', { top: { author: 'Someone', notes: '调整界面布局' },
    addon: { needs: [{ addon: 'EllesmereUI', installed: true }] } }),                   // another addon's, running
  det('tips', 'WuxianTips', '小提示', { top: { author: 'Other', notes: '给新手的提示' },
    addon: { needs: [{ addon: 'EllesmereUI', installed: true }, { addon: 'LayoutLib', installed: false }] } }),
  det('notes', 'WuxianNotes', '笔记', {}),                                              // found, not loaded yet
  det('herald', 'WuxianHeraldSkin', '播报皮肤', {}),                                    // a local extension's: that one's row
];
st.access.data = { kit_state: 'running', servers: [], kit: { version: '0.6.0', source: 'game', newer: false, extensions: [
  { id: 'kit', title: '核心', version: '0.6.0', addon: 'WuxianKit', builtin: true, state: 'on', tools: [tool('wk_kit_history', 'Read')] },
  { id: 'guides', title: '攻略', version: '1.0.0', addon: 'WuxianKit_Guides', builtin: false, state: 'on',
    tools: [tool('wk_guides_get', 'Read'), tool('wk_guides_show', 'Change')] },
  { id: 'route', title: '路线', version: '0.6.0', addon: 'WuxianKit_Route', builtin: false, state: 'on', tools: [tool('wk_route_step', 'Read')] },
  { id: 'herald', title: '播报', version: '0.1.4', addon: 'WuxianHeraldSkin', builtin: false, state: 'on',
    tools: [tool('wk_herald_show', 'Change')] },
  { id: 'layout', title: '界面布局助手', version: '0.4.1', addon: 'WuxianLayout', builtin: false, state: 'on',
    tools: [tool('wk_layout_get', 'Read')] },
  { id: 'lone', title: '独立', version: '2.0', addon: 'Lone', builtin: false, state: 'off', tools: [] }] } };
const row = (id, title, more) => Object.assign({ id, title, version: '0.1.0', enabled: true, state: 'stopped', approval: 'ok',
  allowed: true, origin: 'home', capabilities: [], options: [], addons: [] }, more);
st.lx.list = [
  row('herald', '播报', { author: 'Someone', capabilities: ['herald.announce'],
    addons: [{ folder: 'WuxianHerald', version: '0.2.0', state: 'missing' }, { folder: 'WuxianHeraldSkin', version: '0.1.4', state: 'differs' }] }),
  row('facts', '图鉴速查', { origin: 'linked', state: 'running', capabilities: ['facts.lookup'] }),
];
const out = {};
const lang = 'zh-CN';
out.installed = st.xpInstalled.map((r) => ({ key: r.key, title: r.title, where: r.where, tip: r.tip, version: r.version,
  linked: r.linked, author: r.author, pill: r.pill && r.pill.text, count: r.count }));
out.sorted = st.xpInstalled.every((r, i, a) => !i || a[i - 1].title.localeCompare(r.title, lang) <= 0);
out.catalog = st.xpCatalog.map((c) => ({ key: c.key, title: c.title, version: st.xpVersionOf(c), count: c.count,
  pill: st.xpBadge(c) && st.xpBadge(c).text }));
out.hasAvailable = 'xpAvailable' in st;
out.sys = st.xpSys.map((m) => ({ id: m.id, title: m.title, count: m.count }));
const sel = (key) => { st.xp.sel = key; const s = st.xpSel;
  return { kind: s.kind, tag: s.tag, tip: s.tip, count: s.count, tools: (s.tools || []).map((x) => x.name), mod: !!s.mod,
    line: s.line, author: s.author, needs: s.needs, folder: s.folder, summary: s.summary, state: st.xpState.text,
    ver: st.xpVer.text, toolsLine: st.xpToolsLine }; };
out.herald = sel('l:herald');
out.facts = sel('l:facts');
out.sysSel = sel('x:kit');
out.route = sel('x:route');
out.scout = sel('x:scout');
out.guides = sel('c:wuxiankit-guides');
out.atlas = sel('c:wuxiankit-atlas');
out.layout = sel('m:layout');
out.tips = sel('m:tips');
out.notes = sel('m:notes');
out.extLine = st.xpExtLine;
out.idea = st.lxIdeaPrompt();
out.originTips = [st.lxOriginTip(st.lx.list[1]), st.lxOriginTip(st.lx.list[0]), st.lxOriginTip(null)];
out.linkedTips = st.xpInstalled.filter((r) => r.linked).map((r) => r.linkedTip);
st.lx.list = [];
out.emptyInstalled = st.xpInstalled.map((r) => ({ key: r.key, title: r.title }));
st.access.data = { kit_state: 'unknown', servers: [], kit: null };          // the game is off: the folder still tells
out.off = { sys: st.xpSys.map((m) => m.id), installed: st.xpInstalled.map((r) => r.key), state: (st.xp.sel = 'm:tips', st.xpState.text),
  layout: (st.xp.sel = 'm:layout', st.xpState.text) };
st.ext.detected = [];
out.none = { sys: st.xpSys.map((m) => m.id), installed: st.xpInstalled.map((r) => r.key) };
console.log(JSON.stringify(out));
"""

DIR = "D:\\Game\\Interface\\AddOns\\"


@unittest.skipUnless(NODE, "node is not installed")
class Groups(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join((STATIC_DIR / n).read_text(encoding="utf-8") for n in ("i18n.js", "app.js"))
        r = subprocess.run([NODE, "-"], input=STUBS + src + "\n;(() => {" + HARNESS + "})();", capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout)

    def test_builtin_holds_the_workshops_own(self):
        """WuxianKit's own, then those of its own in an addon beside it (running or only found), then the catalog's"""
        self.assertEqual(self.out["sys"], [{"id": "kit", "title": "核心", "count": 1}, {"id": "route", "title": "路线", "count": 1},
                                           {"id": "scout", "title": "侦察", "count": 0}])
        self.assertEqual(self.out["catalog"], [
            {"key": "c:wuxiankit-guides", "title": "攻略", "version": "1.0.0", "count": 2, "pill": None},
            {"key": "c:wuxiankit-atlas", "title": "地图册", "version": "1.0.0", "count": 3, "pill": "未安装"}])
        self.assertFalse(self.out["hasAvailable"])                       # no third group
        keys = [r["key"] for r in self.out["installed"]]
        for key in ("c:wuxiankit-guides", "c:wuxiankit-atlas", "m:route", "m:scout", "m:guides"):
            self.assertNotIn(key, keys)

    def test_added_holds_the_others_and_the_local_ones(self):
        rows = {r["key"]: r for r in self.out["installed"]}
        self.assertEqual(set(rows), {"l:herald", "l:facts", "m:lone", "m:layout", "m:tips", "m:notes"})
        self.assertTrue(self.out["sorted"])

    def test_a_local_extension_and_its_game_half_are_one_row(self):
        rows = {r["key"]: r for r in self.out["installed"]}
        self.assertNotIn("m:herald", rows)                       # what WuxianHeraldSkin runs in the game is herald's row
        h = rows["l:herald"]
        self.assertEqual((h["where"], h["count"], h["author"], h["linked"]), ("游戏 · 本机", 2, "Someone", ""))
        self.assertEqual((rows["l:facts"]["where"], rows["l:facts"]["linked"]), ("本机", "链接"))
        self.assertEqual((rows["m:lone"]["where"], rows["m:lone"]["pill"]), ("游戏", "未启用"))
        # without the local herald, what its addon runs in the game has a row of its own (one, with what the folder says)
        empty = self.out["emptyInstalled"]
        self.assertEqual(sorted(r["key"] for r in empty), ["m:herald", "m:layout", "m:lone", "m:notes", "m:tips"])

    def test_what_the_game_folder_holds(self):
        """found in AddOns: one row each, running or not, with what its .toc says"""
        rows = {r["key"]: r for r in self.out["installed"]}
        lay, tips, notes = rows["m:layout"], rows["m:tips"], rows["m:notes"]
        self.assertEqual((lay["title"], lay["where"], lay["version"], lay["author"], lay["pill"], lay["count"]),
                         ("界面布局助手", "游戏", "0.4.1", "Someone", None, 1))
        self.assertEqual((tips["title"], tips["author"], tips["pill"], tips["count"]), ("小提示", "Other", "缺少插件", 0))
        self.assertEqual((notes["title"], notes["pill"], notes["version"]), ("笔记", None, "0.4.1"))
        lay, tips, notes = self.out["layout"], self.out["tips"], self.out["notes"]
        self.assertEqual((lay["kind"], lay["tag"], lay["mod"], lay["author"], lay["needs"], lay["folder"], lay["summary"], lay["state"]),
                         ("custom", "游戏", True, "Someone", "EllesmereUI", DIR + "WuxianLayout", "调整界面布局", "运行中"))
        self.assertEqual((tips["mod"], tips["needs"], tips["folder"], tips["summary"], tips["line"], tips["ver"]),
                         (False, "EllesmereUI、LayoutLib（未安装）", DIR + "WuxianTips", "给新手的提示", "WuxianTips · 0.4.1", "0.4.1"))
        self.assertEqual((tips["state"], tips["toolsLine"]), ("缺少需要的插件，游戏不会加载它", "在游戏中加载后可查看"))
        self.assertEqual(notes["state"], "游戏中未加载：新插件需完整重启游戏，已停用的请在插件列表中启用")

    def test_with_the_game_off(self):
        off = self.out["off"]
        self.assertEqual(off["sys"], ["wuxiankit", "route", "scout"])    # the catalog's built-in ones, then its own from the folder
        self.assertEqual(sorted(off["installed"]), ["m:herald", "m:layout", "m:notes", "m:tips"])
        self.assertEqual((off["state"], off["layout"]), ("缺少需要的插件，游戏不会加载它", "游戏未连接"))
        self.assertEqual(self.out["none"], {"sys": ["wuxiankit"], "installed": []})

    def test_the_detail_says_where_it_runs(self):
        self.assertEqual({k: self.out["herald"][k] for k in ("kind", "tag", "tip", "count", "tools", "mod")},
                         {"kind": "local", "tag": "游戏 · 本机", "tip": "包含游戏插件与本机程序：游戏插件在游戏中运行，程序在这台电脑上运行",
                          "count": 1, "tools": ["wk_herald_show"], "mod": True})
        self.assertEqual({k: self.out["facts"][k] for k in ("kind", "tag", "tip", "count", "tools", "mod")},
                         {"kind": "local", "tag": "本机", "tip": "在这台电脑上运行，不需要游戏", "count": 0, "tools": [], "mod": False})
        tips = {r["key"]: r["tip"] for r in self.out["installed"]}
        self.assertEqual((tips["l:herald"], tips["l:facts"], tips["m:lone"]),
                         ("包含游戏插件与本机程序：游戏插件在游戏中运行，程序在这台电脑上运行", "在这台电脑上运行，不需要游戏", "由游戏插件提供，在游戏中运行"))
        self.assertEqual(self.out["layout"]["tip"], "由游戏插件提供，在游戏中运行")

    def test_builtin_says_who_brings_it(self):
        kit, route, scout, guides, atlas = (self.out[k] for k in ("sysSel", "route", "scout", "guides", "atlas"))
        self.assertEqual((kit["kind"], kit["tag"], kit["tip"]), ("sys", "内置", "随扩展框架提供，在游戏中运行，不能单独移除"))
        self.assertEqual((route["kind"], route["tag"], route["tip"], route["tools"]),
                         ("sys", "内置", "由无限工坊提供，在游戏中运行", ["wk_route_step"]))
        self.assertEqual((scout["kind"], scout["tip"], scout["toolsLine"]), ("sys", "由无限工坊提供，在游戏中运行", "在游戏中加载后可查看"))
        self.assertEqual((guides["kind"], guides["tag"], guides["tip"], guides["count"], guides["state"]),
                         ("custom", "内置", "由无限工坊提供，在游戏中运行", 2, "运行中"))
        self.assertEqual((atlas["tag"], atlas["state"], atlas["folder"]), ("内置", "未安装", "WuxianKit_Atlas"))
        self.assertEqual(self.out["extLine"], "4 个内置扩展 · 5 个来自其他插件")

    def test_the_idea_and_the_origins(self):
        idea = self.out["idea"]
        self.assertTrue(idea.startswith("用无限工坊验证一个想法：\n（在这里写你的想法）\n\n先调用 wuxian 的 guide，"), idea)
        for word in ("从最省事的一档开始", "需要我操作时告诉我", "结论（能做 / 有条件能做 / 不能做）和依据"):
            self.assertIn(word, idea)
        for word in ("extension_guide", "extension_load", "做一个扩展"):       # the route says what to make, if anything
            self.assertNotIn(word, idea)
        self.assertEqual(self.out["originTips"], ["文件夹留在原处，工坊不改动其中的文件；改完重新载入即可，适合开发", "由扩展包导入或放进扩展文件夹；移除时移入回收区", ""])
        self.assertEqual(self.out["linkedTips"], ["文件夹留在原处，工坊不改动其中的文件；改完重新载入即可，适合开发"])


class Template(unittest.TestCase):
    """index.html: the two groups, the page bar's 添加, a row of 已添加, the 能力 tab of a local extension with a game half"""

    def setUp(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = html.index("<section class=\"page\" x-t x-show=\"page === 'extensions'\">")
        self.page = html[start:html.index("</section>", start)]
        self.bar = self.page[:self.page.index('<div class="pagebody')]
        self.i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")

    def test_the_groups(self):
        heads = re.findall(r'<div class="mgroup"[^>]*><span>([^<]+)</span>', self.page)
        self.assertEqual(heads, ["内置", "已添加"])
        tips = re.findall(r'<div class="mgroup" x-t title="([^"]+)"', self.page)   # x-t: its own title is translated
        self.assertEqual(tips, ["由无限工坊提供，在游戏中运行", "其他插件在游戏中提供的扩展，以及添加到本机的扩展"])
        self.assertIn('x-show="xpSys.length || xpCatalog.length"><span>内置</span><span>由无限工坊提供</span></div>', self.page)
        self.assertIn('<span class="xp-where" :title="r.tip" x-text="r.where"></span>', self.page)
        self.assertIn('x-for="r in xpInstalled"', self.page)
        builtin = self.page[self.page.index("<span>内置</span>"):self.page.index("<span>已添加</span>")]
        self.assertLess(builtin.index('x-for="m in xpSys"'), builtin.index('x-for="c in xpCatalog"'))
        self.assertEqual(builtin.count('<ul class="mlist">'), 1)      # one list: the catalog's follow WuxianKit's own
        self.assertIn('x-show="xpBadge(c)" :class="xpBadge(c) && xpBadge(c).cls" x-text="xpBadge(c) && xpBadge(c).text"', builtin)
        self.assertNotIn("xpAvailable", self.page)
        self.assertNotIn("扩展目录</span>", self.page)           # no group of its own for the catalog
        self.assertNotIn("可安装到游戏", self.page)
        self.assertNotIn("系统扩展", self.page)
        self.assertNotIn("自定义扩展", self.page)
        self.assertNotIn("已安装</span>", self.page)            # 安装 is for addons going into the game

    def test_what_the_folder_says_is_in_the_detail(self):
        self.assertIn('<dt x-show="xpSel.author">作者</dt><dd x-show="xpSel.author" x-text="xpSel.author"></dd>', self.page)
        self.assertIn('<dt x-show="xpSel.needs">需要插件</dt><dd x-show="xpSel.needs" x-text="xpSel.needs"></dd>', self.page)
        self.assertIn('<p class="notes-line" x-show="xpSel.summary" x-text="xpSel.summary"></p>', self.page)
        self.assertIn('<dt>插件文件夹</dt><dd class="mono" x-text="xpSel.folder || \'—\'"></dd>', self.page)

    def test_the_page_bar_adds(self):
        self.assertIn('@click="lxMenu(\'add\')"><svg class="ico"><use href="#i-plus"/></svg>添加</button>', self.bar)
        items = re.findall(r'<button class="menu-item" @click="(\w+)\(\)">([^<]+)</button>', self.bar)
        self.assertEqual(items, [("lxNewOpen", "新建扩展"), ("lxAddFolder", "链接文件夹"), ("lxAddPackage", "导入扩展包")])
        self.assertIn('@click="lxOpenHome()"><svg class="ico"><use href="#i-folder"/></svg>扩展文件夹</button>', self.bar)

    def test_the_title_says_what_extensions_are_for(self):
        self.assertIn('<span class="pg-help" x-t tabindex="0" title="说出想法，Agent 先查证能否实现，再在运行中的游戏里做成扩展。扩展可在游戏内运行，也可接入本机程序与其他插件的数据。" aria-label="说出想法，Agent 先查证能否实现，再在运行中的游戏里做成扩展。扩展可在游戏内运行，也可接入本机程序与其他插件的数据。">'
                      '<svg class="ico"><use href="#i-info"/></svg></span>', self.bar)
        empty = self.page[self.page.index('<div class="lx-empty"'):]
        empty = empty[:empty.index("</div>\n            </div>")]
        self.assertIn('<button class="btn small primary" @click="lxNewOpen()">新建扩展</button>', empty)   # the idea is typed there
        self.assertNotIn("lxCopyIdea", self.page)
        self.assertIn('<textarea x-t x-ref="lxidea" class="input lx-idea" rows="4" x-model="lxn.idea"', self.page)
        self.assertNotIn("安装扩展包", self.page)            # a package is imported: 安装 is for addons
        self.assertNotIn("允许运行", self.page)              # the player approves (批准运行, 本机扩展批准)

    def test_the_caps_tab_lists_the_game_half(self):
        caps = self.page[self.page.index("<div x-show=\"xpTab === 'caps'\">"):self.page.index('<div class="toolbar lx-out-bar"')]
        self.assertIn('<h3 class="xp-h" x-show="xpSel.mod && !lxGameOnly(lxCur)">本机', caps)
        self.assertIn('<p class="xp-toolhint" x-show="lxCapsHint(lxCur, xpSel.mod)" x-text="lxCapsHint(lxCur, xpSel.mod)"></p>', caps)
        self.assertIn('<h3 class="xp-h">游戏<span class="muted">由游戏插件经扩展框架提供</span></h3>', caps)
        self.assertIn('x-for="tool in (xpSel.tools || [])"', caps)

    def test_the_words_have_their_english(self):
        for word in ("内置", "已添加", "由无限工坊提供", "本机", "游戏", "还没有扩展。说出想法，Agent 先查证能否实现，再在运行中的游戏里做出来。", "在这台电脑上运行，不需要游戏",
                     "由游戏插件经扩展框架提供", "{a} 个内置扩展 · {b} 个来自其他插件", "说出想法，Agent 先查证能否实现，再在运行中的游戏里做成扩展。扩展可在游戏内运行，也可接入本机程序与其他插件的数据。", "文件夹留在原处，工坊不改动其中的文件；改完重新载入即可，适合开发",
                     "由扩展包导入或放进扩展文件夹；移除时移入回收区", "导入扩展包", "导入", "已导入 {name}", "批准运行", "已批准 {name}", "自动批准", "交给 Agent", "随扩展框架提供，在游戏中运行，不能单独移除", "其他插件在游戏中提供的扩展，以及添加到本机的扩展",
                     "由无限工坊提供，在游戏中运行", "由游戏插件提供，在游戏中运行", "在这台电脑上运行，不需要游戏", "包含游戏插件与本机程序：游戏插件在游戏中运行，程序在这台电脑上运行",
                     "需要插件", "缺少插件", "缺少需要的插件，游戏不会加载它", "游戏中未加载：新插件需完整重启游戏，已停用的请在插件列表中启用"):
            self.assertRegex(self.i18n, r"\n\s*'%s':" % re.escape(word), word)
        for word in ("扩展目录", "可安装到游戏", "无限工坊扩展目录中尚未安装到游戏的扩展"):        # the third group is gone
            self.assertNotRegex(self.i18n, r"\n\s*'%s':" % re.escape(word), word)


if __name__ == "__main__":
    unittest.main()
