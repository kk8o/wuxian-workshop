"""The 扩展 page's list, grouped by who brings an extension: 内置 (WuxianKit's own), 已添加 (the custom extensions the game
runs and the local ones, by title) and 扩展目录 (the catalog's packages not in the game folder). A local extension and what
the game runs from its game addons are one row, which says where it runs (游戏 / 本机 / 游戏 · 本机); its detail lists the
game's tools under 能力 too. app.js run under Node (skipped without it)."""
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
st.ext.list = [pkg('wuxiankit', 'WuxianKit', '扩展框架', true, 9), pkg('wuxiankit-eui', 'WuxianKit_EllesmereUI',
  '扩展框架 · EllesmereUI', true, 2), pkg('raidnotes', 'RaidNotes', '团队笔记', false, 3)];
st.access.data = { kit_state: 'running', servers: [], kit: { version: '0.6.0', source: 'game', newer: false, extensions: [
  { id: 'kit', title: '核心', version: '0.6.0', addon: 'WuxianKit', builtin: true, state: 'on', tools: [tool('wk_kit_history', 'Read')] },
  { id: 'eui', title: 'EllesmereUI', version: '0.3.1', addon: 'WuxianKit_EllesmereUI', builtin: false, state: 'on',
    tools: [tool('wk_eui_get', 'Read'), tool('wk_eui_tidy', 'Change')] },
  { id: 'herald', title: '播报', version: '0.1.4', addon: 'WuxianHeraldSkin', builtin: false, state: 'on',
    tools: [tool('wk_herald_show', 'Change')] },
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
out.available = st.xpAvailable.map((c) => c.key);
out.sys = st.xpSys.map((m) => m.id);
const sel = (key) => { st.xp.sel = key; const s = st.xpSel;
  return { kind: s.kind, tag: s.tag, tip: s.tip, count: s.count, tools: (s.tools || []).map((x) => x.name), mod: !!s.mod }; };
out.herald = sel('l:herald');
out.facts = sel('l:facts');
out.sysSel = sel('x:kit');
out.eui = sel('c:wuxiankit-eui');
out.extLine = st.xpExtLine;
out.idea = st.lxIdeaPrompt();
out.originTips = [st.lxOriginTip(st.lx.list[1]), st.lxOriginTip(st.lx.list[0]), st.lxOriginTip(null)];
out.linkedTips = st.xpInstalled.filter((r) => r.linked).map((r) => r.linkedTip);
st.lx.list = [];
out.emptyInstalled = st.xpInstalled.map((r) => r.key);
console.log(JSON.stringify(out));
"""


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

    def test_installed_holds_the_custom_and_the_local_ones(self):
        rows = {r["key"]: r for r in self.out["installed"]}
        self.assertEqual(set(rows), {"l:herald", "l:facts", "c:wuxiankit-eui", "m:lone"})
        self.assertTrue(self.out["sorted"])
        self.assertEqual(self.out["sys"], ["kit"])

    def test_a_local_extension_and_its_game_half_are_one_row(self):
        rows = {r["key"]: r for r in self.out["installed"]}
        self.assertNotIn("m:herald", rows)                       # what WuxianHeraldSkin runs in the game is herald's row
        h = rows["l:herald"]
        self.assertEqual((h["where"], h["count"], h["author"], h["linked"]), ("游戏 · 本机", 2, "Someone", ""))
        self.assertEqual((rows["l:facts"]["where"], rows["l:facts"]["linked"]), ("本机", "链接"))
        self.assertEqual((rows["c:wuxiankit-eui"]["where"], rows["c:wuxiankit-eui"]["title"], rows["c:wuxiankit-eui"]["count"]),
                         ("游戏", "EllesmereUI", 2))
        self.assertEqual((rows["m:lone"]["where"], rows["m:lone"]["pill"]), ("游戏", "未启用"))

    def test_the_catalog_keeps_what_is_not_in_the_game(self):
        self.assertEqual(self.out["available"], ["c:raidnotes"])
        self.assertNotIn("c:raidnotes", [r["key"] for r in self.out["installed"]])
        # without the local herald, what its addon runs in the game has a row of its own
        self.assertEqual(set(self.out["emptyInstalled"]), {"c:wuxiankit-eui", "m:herald", "m:lone"})

    def test_the_detail_says_where_it_runs(self):
        self.assertEqual(self.out["herald"], {"kind": "local", "tag": "游戏 · 本机", "tip": "包含游戏插件与本机程序：游戏插件在游戏中运行，程序在这台电脑上运行", "count": 1,
                                              "tools": ["wk_herald_show"], "mod": True})
        self.assertEqual(self.out["facts"], {"kind": "local", "tag": "本机", "tip": "在这台电脑上运行，不需要游戏", "count": 0, "tools": [],
                                             "mod": False})
        self.assertEqual(self.out["sysSel"]["tip"], "随扩展框架提供，在游戏中运行，不能单独移除")
        self.assertEqual(self.out["eui"]["tip"], "由游戏插件提供，在游戏中运行")
        tips = {r["key"]: r["tip"] for r in self.out["installed"]}
        self.assertEqual((tips["l:herald"], tips["l:facts"], tips["m:lone"]), ("包含游戏插件与本机程序：游戏插件在游戏中运行，程序在这台电脑上运行", "在这台电脑上运行，不需要游戏", "由游戏插件提供，在游戏中运行"))
        self.assertEqual((self.out["sysSel"]["kind"], self.out["sysSel"]["tag"]), ("sys", "内置"))
        self.assertEqual((self.out["eui"]["kind"], self.out["eui"]["tag"]), ("custom", "游戏"))
        self.assertEqual(self.out["extLine"], "1 个内置扩展 · 3 个来自其他插件")

    def test_the_idea_and_the_origins(self):
        idea = self.out["idea"]
        self.assertTrue(idea.startswith("用无限工坊验证一个想法：（在这里写你的想法）。先调用 wuxian 的 guide，"), idea)
        for word in ("从最省事的一档开始", "需要我操作时告诉我", "结论（能做 / 有条件能做 / 不能做）和依据"):
            self.assertIn(word, idea)
        for word in ("extension_guide", "extension_load", "做一个扩展"):       # the route says what to make, if anything
            self.assertNotIn(word, idea)
        self.assertEqual(self.out["originTips"], ["文件夹留在原处，工坊不改动其中的文件；改完重新载入即可，适合开发", "由扩展包导入或放进扩展文件夹；移除时移入回收区", ""])
        self.assertEqual(self.out["linkedTips"], ["文件夹留在原处，工坊不改动其中的文件；改完重新载入即可，适合开发"])


class Template(unittest.TestCase):
    """index.html: the three groups, the page bar's 添加, a row of 已添加, the 能力 tab of a local extension with a game half"""

    def setUp(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        start = html.index("<section class=\"page\" x-t x-show=\"page === 'extensions'\">")
        self.page = html[start:html.index("</section>", start)]
        self.bar = self.page[:self.page.index('<div class="pagebody')]
        self.i18n = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")

    def test_the_groups(self):
        heads = re.findall(r'<div class="mgroup"[^>]*><span>([^<]+)</span>', self.page)
        self.assertEqual(heads, ["内置", "已添加", "扩展目录"])
        tips = re.findall(r'<div class="mgroup" x-t title="([^"]+)"', self.page)   # x-t: its own title is translated
        self.assertEqual(tips, ["随扩展框架提供，在游戏中运行，不能单独移除", "其他插件在游戏中提供的扩展，以及添加到本机的扩展", "无限工坊扩展目录中尚未安装到游戏的扩展"])
        self.assertIn('<span class="xp-where" :title="r.tip" x-text="r.where"></span>', self.page)
        self.assertIn('x-for="r in xpInstalled"', self.page)
        self.assertIn('x-for="c in xpAvailable"', self.page)
        self.assertNotIn("系统扩展", self.page)
        self.assertNotIn("自定义扩展", self.page)
        self.assertNotIn("已安装</span>", self.page)            # 安装 is for addons going into the game

    def test_the_page_bar_adds(self):
        self.assertIn('@click="lxMenu(\'add\')"><svg class="ico"><use href="#i-plus"/></svg>添加</button>', self.bar)
        items = re.findall(r'<button class="menu-item" @click="(\w+)\(\)">([^<]+)</button>', self.bar)
        self.assertEqual(items, [("lxNewOpen", "新建扩展"), ("lxAddFolder", "链接文件夹"), ("lxAddPackage", "导入扩展包")])
        self.assertIn('@click="lxOpenHome()"><svg class="ico"><use href="#i-folder"/></svg>扩展文件夹</button>', self.bar)

    def test_the_title_says_what_extensions_are_for(self):
        self.assertIn('<span class="pg-help" x-t tabindex="0" title="扩展让 Agent 借助无限工坊的框架与通道快速做出原型、验证想法：游戏中的部分经扩展框架接入，本机的部分由工坊运行，能力都以 wk_ 工具提供给 Agent。" aria-label="扩展让 Agent 借助无限工坊的框架与通道快速做出原型、验证想法：游戏中的部分经扩展框架接入，本机的部分由工坊运行，能力都以 wk_ 工具提供给 Agent。">'
                      '<svg class="ico"><use href="#i-info"/></svg></span>', self.bar)
        empty = self.page[self.page.index('<div class="lx-empty"'):]
        empty = empty[:empty.index("</div>\n            </div>")]
        self.assertIn('<button class="btn small primary" @click="lxCopyIdea()">复制给 Agent 的第一句话</button>', empty)
        self.assertIn('<button class="btn tiny" type="button" @click="lxCopyIdea()">复制给 Agent 的第一句话</button>', self.page)
        self.assertNotIn("安装扩展包", self.page)            # a package is imported: 安装 is for addons
        self.assertNotIn("允许运行", self.page)              # the player approves (批准运行, 本机扩展批准)

    def test_the_caps_tab_lists_the_game_half(self):
        caps = self.page[self.page.index("<div x-show=\"xpTab === 'caps'\">"):self.page.index('<div class="toolbar lx-out-bar"')]
        self.assertIn('<h3 class="xp-h" x-show="xpSel.mod && !lxGameOnly(lxCur)">本机', caps)
        self.assertIn('<p class="xp-toolhint" x-show="lxCapsHint(lxCur, xpSel.mod)" x-text="lxCapsHint(lxCur, xpSel.mod)"></p>', caps)
        self.assertIn('<h3 class="xp-h">游戏<span class="muted">由游戏插件经扩展框架提供</span></h3>', caps)
        self.assertIn('x-for="tool in (xpSel.tools || [])"', caps)

    def test_the_words_have_their_english(self):
        for word in ("内置", "已添加", "扩展目录", "可安装到游戏", "本机", "游戏", "还没有扩展。把想法交给 Agent，它会新建扩展、载入并试用。", "在这台电脑上运行，不需要游戏",
                     "由游戏插件经扩展框架提供", "{a} 个内置扩展 · {b} 个来自其他插件", "扩展让 Agent 借助无限工坊的框架与通道快速做出原型、验证想法：游戏中的部分经扩展框架接入，本机的部分由工坊运行，能力都以 wk_ 工具提供给 Agent。", "文件夹留在原处，工坊不改动其中的文件；改完重新载入即可，适合开发",
                     "由扩展包导入或放进扩展文件夹；移除时移入回收区", "导入扩展包", "导入", "已导入 {name}", "批准运行", "已批准 {name}", "自动批准", "交给 Agent", "随扩展框架提供，在游戏中运行，不能单独移除", "其他插件在游戏中提供的扩展，以及添加到本机的扩展",
                     "无限工坊扩展目录中尚未安装到游戏的扩展", "由游戏插件提供，在游戏中运行", "在这台电脑上运行，不需要游戏", "包含游戏插件与本机程序：游戏插件在游戏中运行，程序在这台电脑上运行"):
            self.assertRegex(self.i18n, r"\n\s*'%s':" % re.escape(word), word)


if __name__ == "__main__":
    unittest.main()
