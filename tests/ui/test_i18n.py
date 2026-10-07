"""The page in two languages (ui/static/i18n.js): every Chinese text of index.html and app.js has its English in EN, with the
same {placeholders}; index.html's expressions put their Chinese through t(); app.js's component has no Chinese outside
t() (its constants at the top are keys, translated where they are shown)."""
import re
import unittest
from html.parser import HTMLParser

from wuxianworkshop.ui import STATIC_DIR

HAN = re.compile(r"[　-〿一-鿿＀-￯]")
ATTRS = ("title", "placeholder", "aria-label", "alt")


def js_strings(src):
    """the string literals of a piece of JavaScript: [(start, end, text)], comments and regular expressions skipped"""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == "/":
            k = i - 1
            while k >= 0 and src[k] in " \t":
                k -= 1
            if k < 0 or src[k] in "(,=:[!&|?{};\n" or src[max(0, k - 5):k + 1] == "return":
                j, cls = i + 1, False                       # a regular expression
                while j < n and (src[j] != "/" or cls) and src[j] != "\n":
                    if src[j] == "\\":
                        j += 1
                    elif src[j] in "[]":
                        cls = src[j] == "["
                    j += 1
                i = j + 1
            else:
                i += 1
        elif c in "'\"`":
            j, buf = i + 1, []
            while j < n and src[j] != c:
                if src[j] == "\\":
                    buf.append({"n": "\n", "t": "\t"}.get(src[j + 1], src[j + 1]))
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            out.append((i, j + 1, "".join(buf)))
            i = j + 1
        else:
            i += 1
    return out


def in_t_call(src, pos, start=0):
    """whether the literal at pos sits inside a t(...) call (brackets inside strings blanked out first)"""
    depth = 0
    for k in range(pos - 1, start - 1, -1):
        c = src[k]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            if depth:
                depth -= 1
            elif c == "(" and k >= 1 and src[k - 1] == "t" and (k < 2 or not (src[k - 2].isalnum() or src[k - 2] in "_$.")):
                return True
    return False


def blank_strings(src, lits):
    chars = list(src)
    for a, b, _ in lits:
        for k in range(a + 1, b - 1):
            if chars[k] != "\n":
                chars[k] = "x"
    return "".join(chars)


def norm(text):
    return " ".join(text.split())


class Page(HTMLParser):
    """index.html's Chinese: static text (not under an element with a lang of its own), static attributes, and the
    literals of the Alpine expressions with whether a t() holds each"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts, self.literals, self.stack = [], [], []

    def handle_starttag(self, tag, attrs):
        if not self.get_starttag_text().endswith("/>") and tag not in ("input", "img", "br", "meta", "link"):
            self.stack.append((tag, any(k == "lang" for k, _ in attrs) and tag != "html"))
        for k, v in attrs:
            if not v or not HAN.search(v):
                continue
            if k in ATTRS:
                self.texts.append(norm(v))
            elif k.startswith((":", "x-", "@")):
                lits = js_strings(v)
                masked = blank_strings(v, lits)
                self.literals += [(norm(t), in_t_call(masked, a), k, v) for a, b, t in lits if HAN.search(t)]

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if HAN.search(data) and self.stack and self.stack[-1][0] not in ("title", "script", "style") \
                and not any(own for _, own in self.stack):
            self.texts.append(norm(data))


def english():
    """EN of i18n.js as {key: english}"""
    src = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    body = src[src.index("const EN = {"):src.index("\n};", src.index("const EN = {"))]
    lits = [t for _, _, t in js_strings(body)]
    return dict(zip(lits[0::2], lits[1::2]))


class Translations(unittest.TestCase):
    def setUp(self):
        self.en = english()
        self.page = Page()
        self.page.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
        self.js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.js_lits = js_strings(self.js)

    def test_every_text_has_english(self):
        keys = set(self.page.texts) | {t for t, *_ in self.page.literals}
        keys |= {norm(t) for _, _, t in self.js_lits if HAN.search(t)}
        missing = sorted(k for k in keys if k not in self.en)
        self.assertEqual(missing, [], "texts without English in i18n.js EN")
        self.assertGreater(len(keys), 400)

    def test_placeholders_match(self):
        for key, value in self.en.items():
            self.assertEqual(set(re.findall(r"\{(\w+)\}", key)), set(re.findall(r"\{(\w+)\}", value)), key)
            self.assertIsNone(HAN.search(value), f"{key}: the English has Chinese in it")

    def test_keys_as_t_looks_them_up(self):
        for key in self.en:
            self.assertEqual(key, norm(key), "a key with its whitespace made one space, trimmed")

    def test_expressions_go_through_t(self):
        bare = [(k, t) for t, held, k, v in self.page.literals if not held]
        self.assertEqual(bare, [], "Chinese in an Alpine expression without t()")

    def test_component_goes_through_t(self):
        top = self.js.index("function appState() {")
        masked = blank_strings(self.js, self.js_lits)
        keep = {"我的第一个插件"}                      # the first addon's default name, compared in applyLang
        bare = [t for a, b, t in self.js_lits
                if a > top and HAN.search(t) and t not in keep and not in_t_call(masked, a, top)]
        self.assertEqual(bare, [], "Chinese in app.js's component without t()")

    def test_static_words_are_walked(self):
        """each page, the header, the navigation and the status bar carry x-t; so does an element with a static Chinese
        title / placeholder / aria-label / alt"""
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'<section class="page" x-t', html)), len(re.findall(r'<section class="page"', html)))
        for tag in ('<header class="topbar" x-t>', '<nav class="sidebar" x-t>', '<footer class="statusbar" x-t>'):
            self.assertIn(tag, html)
        for m in re.finditer(r"<(\w+)\s[^>]*>", html):
            tag = m.group(0)
            if re.search(r'\s(?:%s)="[^"]*[一-鿿]' % "|".join(ATTRS), tag):
                self.assertRegex(tag, r"\sx-t[\s>]", tag[:120])

    def test_language_menu(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        for value in ("auto", "zh-CN", "en"):
            self.assertIn(f'<option value="{value}"', html)
        self.assertIn('<option value="zh-CN" lang="zh-CN">简体中文</option>', html)     # in its own language


if __name__ == "__main__":
    unittest.main()
