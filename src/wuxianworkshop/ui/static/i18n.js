/* 无限工坊's page in two languages. The Chinese text of index.html and app.js is both the source and the key; EN holds the English
   for each (keys with their runs of whitespace made one space, without the whitespace around them). t(text, vars) answers in the
   language in force (the Alpine store "lang": the settings' language, 'auto' following the system's) and fills {name} from vars;
   a text without English stays Chinese. x-t on an element translates its static words: its text nodes and those of descendants
   without Alpine bindings of their own, and its title / placeholder / aria-label / alt (unless bound). Loaded before app.js;
   tests/ui/test_i18n.py checks that every text the page translates has its English here. */
'use strict';

const EN = {};

/** the language a setting stands for: 'auto' follows the system (the window's language), else zh-CN or en */
function langOf(setting) {
  const s = String(setting || 'auto');
  if (s === 'auto') return /^zh/i.test(navigator.language || '') ? 'zh-CN' : 'en';
  return /^en/i.test(s) ? 'en' : 'zh-CN';
}

function curLang() { return window.Alpine && Alpine.store('lang') ? Alpine.store('lang').v : 'zh-CN'; }

/** the text in the language in force, {name} filled from vars */
function t(text, vars) {
  let s = text == null ? '' : String(text);
  if (curLang() === 'en') {
    const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(s);
    const e = EN[m[2].replace(/\s+/g, ' ')];
    if (e !== undefined) s = m[1] + e + m[3];
  }
  return vars ? s.replace(/\{(\w+)\}/g, (all, k) => (k in vars && vars[k] != null ? String(vars[k]) : all)) : s;
}

document.addEventListener('alpine:init', () => {
  Alpine.store('lang', { v: 'zh-CN' });
  Alpine.directive('t', (el, _, { effect }) => {
    const texts = [];
    const walk = (node) => {
      for (const c of node.childNodes) {
        if (c.nodeType === 3) { if (/\S/.test(c.nodeValue)) texts.push([c, c.nodeValue]); }
        else if (c.nodeType === 1 && c.tagName !== 'TEMPLATE' && !c.hasAttribute('x-text') && !c.hasAttribute('x-html') && !c.hasAttribute('x-t')) walk(c);
      }
    };
    walk(el);
    const attrs = ['title', 'placeholder', 'aria-label', 'alt']
      .filter((a) => el.getAttribute(a) && !el.hasAttribute(':' + a) && !el.hasAttribute('x-bind:' + a)).map((a) => [a, el.getAttribute(a)]);
    effect(() => {
      Alpine.store('lang').v;                    // runs again when the language changes
      for (const [n, src] of texts) n.nodeValue = t(src);
      for (const [a, src] of attrs) el.setAttribute(a, t(src));
    });
  });
});
