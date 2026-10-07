"""The page's files: present in the package, loading nothing from the network, no CDN anywhere under ui/."""
import re
import unittest
from pathlib import Path

from wuxianworkshop.ui import STATIC_DIR

FILES = ("index.html", "app.css", "i18n.js", "app.js", "htmx.min.js", "alpine.min.js", "icon.png", "logo.svg", "wuxian.ico")
TEXT = tuple(f for f in FILES if not f.endswith((".png", ".ico")))
URL = re.compile(r"https?://")
# URLs that may appear outside comments because nothing is fetched from them: the loopback examples of the connect page,
# the text of a console warning inside Alpine (it names the plugin docs when a plugin is missing), the SVG namespace, and
# the website, which the page opens in the user's own browser (ui/shell.py PageApi.open_url)
ALLOWED = ("http://127.0.0.1", "http://localhost", "https://alpinejs.dev/plugins/", "http://www.w3.org/2000/svg",
           "https://wuxianwow.com/")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/|<!--.*?-->", re.S)


def without_comments(name, text):
    """the file's text with block comments (/* */, <!-- -->) and // comment lines taken out"""
    text = BLOCK_COMMENT.sub("", text)
    if name.endswith(".js"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))
    return text


class StaticFiles(unittest.TestCase):
    def test_files_exist(self):
        for name in FILES:
            self.assertTrue((STATIC_DIR / name).is_file(), name)
        self.assertGreater((STATIC_DIR / "htmx.min.js").stat().st_size, 40_000)        # the real libraries, not stubs
        self.assertGreater((STATIC_DIR / "alpine.min.js").stat().st_size, 40_000)

    def test_index_loads_only_local_files(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        refs = re.findall(r'<(?:script|link|img)\b[^>]*?\s(?:src|href)="([^"]+)"', html)      # not :src= (Alpine bindings)
        refs = [r.split("?")[0] for r in refs]                                                 # app.css?v=2: a cache buster
        self.assertTrue(refs)
        for ref in refs:
            self.assertFalse(ref.startswith(("http:", "https:", "//")), ref)
            self.assertTrue((STATIC_DIR / ref).is_file(), f"{ref} is referenced but not in static/")
        for lib in ("htmx.min.js", "alpine.min.js", "i18n.js", "app.js", "app.css"):
            self.assertIn(lib, refs)

    def test_no_external_urls(self):
        for name in TEXT:
            text = without_comments(name, (STATIC_DIR / name).read_text(encoding="utf-8"))
            for i, line in enumerate(text.splitlines(), 1):
                if not URL.search(line):
                    continue
                self.assertTrue(any(a in line for a in ALLOWED), f"{name}:{i}: {line.strip()[:160]}")

    def test_no_cdn_anywhere_under_ui(self):
        """the acceptance check: rg -n "cdn\\.|unpkg|jsdelivr" src/wuxianworkshop/ui finds nothing"""
        pattern = re.compile(r"cdn\.|unpkg|jsdelivr")
        for path in Path(STATIC_DIR).parent.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".js", ".css", ".html", ".md", ".txt", ".json"):
                self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), str(path))


if __name__ == "__main__":
    unittest.main()
