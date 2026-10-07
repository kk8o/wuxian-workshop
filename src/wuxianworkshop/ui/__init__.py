r"""The desktop shell for players and developers: the web page (static/), the pywebview window (shell.py), the tray icon
behind a thin interface (tray.py), the WebView2 Runtime check (webview2.py) and the tray icon image (icon.py).

The page is served by the daemon at / from STATIC_DIR: index.html, app.css, i18n.js (its English), app.js, the two vendored
libraries below and icon.png, nothing loaded from the network (public CDNs are unreliable in mainland China).
scripts/dev_fake_api.py serves the same folder with made-up data for developing the page. Vendored libraries (version, licence, origin; the sha512 of each
tarball was checked against the registry metadata before the file was taken out of it):
    htmx.min.js    htmx 2.0.11, 0BSD: package/dist/htmx.min.js of https://registry.npmmirror.com/htmx.org/-/htmx.org-2.0.11.tgz
    alpine.min.js  Alpine.js 3.17.4, MIT: the browser bundle in package/dist (the file npm publishes for <script src>) of
                   https://registry.npmmirror.com/alpinejs/-/alpinejs-3.17.4.tgz
"""
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"
