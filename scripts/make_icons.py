r"""Renders the logo into the app's icons with the system's Edge (headless Chromium, so the SVG is drawn the way the page
draws it): static\wuxian.ico (16, 20 and 24 px from logo-small.svg, the mark simplified for small sizes; 32 to 256 px
from logo.svg) and static\icon.png (256 px); and the addons' textures (TGA, power-of-two sizes; Skin.lua uses them):
WoWBridge\Media\logo (128 px, the panel's head), logo-small (32 px, the AddOns list and the addon compartment), mark
(64 px, the ‹∞› alone from mark.svg: the minimap button), mark-wide (64 x 32, the same cut to its width: the chat
prefix), and white ones the addon tints: stud (32 px, the diamond of the corners and the check boxes), inf (64 x 32, the
line-art ∞ of the ornament, as the app's #inf-line), cross (32 px, the close button); !WuxianWorkshop\Media\logo-small
and mark-wide. Run it after changing an SVG (from PowerShell: Edge does not start under the Bash tool); the build takes
the files as they are.

    .venv\Scripts\python.exe scripts\make_icons.py [--preview <png>]   (--preview: every size side by side, to look at)
"""
import argparse
import io
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "wuxianworkshop" / "ui" / "static"
SMALL = (16, 20, 24)                        # the tray (16 / 20 / 24 px at 100 / 125 / 150 % scaling) and small lists
LARGE = (32, 40, 48, 64, 128, 256)
EDGES = (Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
         Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"))


def edge():
    for p in EDGES:
        if p.is_file():
            return p
    sys.exit("Microsoft Edge not found: it draws the SVG")


def render(jobs):
    """[RGBA image] for [(svg, size)], in that order (svg: a file or the markup itself; size: a side or (width, height)): one
    page with the icons side by side on a transparent background, one screenshot, cut apart (the window at least 200 x 100:
    headless Edge hangs on a tiny one)"""
    with tempfile.TemporaryDirectory() as tmp:
        places, x = [], 0
        for i, (svg, size) in enumerate(jobs):
            w, h = size if isinstance(size, tuple) else (size, size)
            if isinstance(svg, str):                               # markup: a file next to the page
                path = Path(tmp) / f"inline{i}.svg"
                path.write_text(svg, encoding="utf-8")
                svg = path
            places.append((svg, w, h, x))
            x += w + 8
        width, height = max(x, 200), max(100, max(h for _, _, h, _ in places))
        imgs = "".join(f'<img src="{svg.as_uri()}" style="left:{left}px;width:{w}px;height:{h}px">' for svg, w, h, left in places)
        html = ("<!doctype html><html><head><style>html,body{margin:0;background:transparent;overflow:hidden}"
                f"img{{position:absolute;top:0}}</style></head><body>{imgs}</body></html>")
        page, shot = Path(tmp) / "icons.html", Path(tmp) / "shot.png"
        page.write_text(html, encoding="utf-8")
        subprocess.run([str(edge()), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--default-background-color=00000000", f"--user-data-dir={Path(tmp) / 'profile'}",   # not the user's
                        f"--window-size={width},{height}", f"--screenshot={shot}", page.as_uri()],         # running Edge
                       check=True, capture_output=True, timeout=120)
        with Image.open(shot) as sheet:
            sheet = sheet.convert("RGBA")
    return [sheet.crop((left, 0, left + w, h)) for _, w, h, left in places]


def ico_bytes(images):
    """an .ico of PNG-compressed images (Windows Vista and later read those at every size), smallest first"""
    entries = sorted(images.items())
    head = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    table, data = b"", b""
    for size, img in entries:
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        png = buf.getvalue()
        side = 0 if size >= 256 else size                 # 0 means 256 in the directory entry
        table += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return head + table + data


def stud(size=32, scale=8):
    """the diamond stud of the panel's corners, white (the addon tints it): drawn large and scaled down for smooth edges"""
    from PIL import ImageDraw
    big = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    s = size * scale
    ImageDraw.Draw(big).polygon([(s / 2, 0), (s, s / 2), (s / 2, s), (0, s / 2)], fill=(255, 255, 255, 255))
    return big.resize((size, size), Image.LANCZOS)


# white, for the addon to tint: the app's #inf-line (index.html) cut to 2 : 1, and the close button's cross
INF = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="8 8 48 24"><path d="M32,20 C40,8 54,8 54,20 C54,32 40,32 32,20 '
       'C24,8 10,8 10,20 C10,32 24,32 32,20" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/></svg>')
CROSS = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4.5,4.5 L11.5,11.5 M11.5,4.5 L4.5,11.5" '
         'fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/></svg>')


def addon_textures():
    """the addons' textures (TGA, which the client reads; power-of-two sizes): the panel's logo, the small tile for the
    AddOns list and the addon compartment, the mark alone for the minimap button and, cut to its width, the chat prefix;
    the white stud, ornament and cross"""
    mark = (STATIC / "mark.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 64 64"' in mark, "mark.svg: the viewBox changed; cut mark-wide anew"
    wide = mark.replace('viewBox="0 0 64 64"', 'viewBox="-4 14 72 36"')       # the brackets' tips included, 2 : 1
    logo, small, mark, wide, inf, cross = render([(STATIC / "logo.svg", 128), (STATIC / "logo-small.svg", 32),
                                                  (STATIC / "mark.svg", 64), (wide, (64, 32)), (INF, (64, 32)), (CROSS, 32)])
    out = {ROOT / "addon" / "WoWBridge" / "Media": {"logo": logo, "logo-small": small, "mark": mark, "mark-wide": wide,
                                                   "stud": stud(), "inf": inf, "cross": cross},
           ROOT / "addon" / "!WuxianWorkshop" / "Media": {"logo-small": small, "mark-wide": wide}}
    for folder, files in out.items():
        folder.mkdir(parents=True, exist_ok=True)
        for name, img in files.items():
            img.save(folder / f"{name}.tga", "TGA")
    return [folder / f"{name}.tga" for folder, files in out.items() for name in files]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--preview", help="also write every size side by side on the app's background into this PNG")
    args = ap.parse_args()
    jobs = [(STATIC / "logo-small.svg", s) for s in SMALL] + [(STATIC / "logo.svg", s) for s in LARGE]
    images = dict(zip((size for _, size in jobs), render(jobs)))
    (STATIC / "wuxian.ico").write_bytes(ico_bytes(images))
    images[256].save(STATIC / "icon.png", "PNG", optimize=True)
    print(f"wrote {STATIC / 'wuxian.ico'} ({', '.join(str(s) for s in sorted(images))} px) and {STATIC / 'icon.png'}")
    textures = addon_textures()
    print(f"wrote {len(textures)} addon textures: {', '.join(str(p.relative_to(ROOT)) for p in textures)}")
    if args.preview:
        sizes = sorted(images)
        sheet = Image.new("RGBA", (sum(sizes) + 12 * (len(sizes) + 1), 256 + 24), "#16171d")
        x = 12
        for s in sizes:
            sheet.alpha_composite(images[s], (x, 12))
            x += s + 12
        sheet.save(args.preview)
        print(f"preview: {args.preview}")


if __name__ == "__main__":
    main()
