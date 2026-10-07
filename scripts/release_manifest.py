r"""Writes latest.json beside a Velopack release (scripts\pack.ps1 runs it after `vpk pack`): what the website's download
page shows and links. It takes the newest full package from releases.<channel>.json (version, notes, size) and renames
the Setup exe and the portable zip vpk made to the names visitors download (WuxianWorkshop-<version>-Setup.exe,
WuxianWorkshop-<version>-Portable.zip; the feed does not name them) with their size and SHA-256. The feed itself
(releases.<channel>.json, the .nupkg files) is what the installed program reads; latest.json is only for people.

    .venv\Scripts\python.exe scripts\release_manifest.py [--channel win] [--dir dist\releases]
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK_ID = "WuxianWorkshop.App"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def semver_key(version):
    """sorts 0.9.0-dev.1 < 0.9.0 < 0.10.0 (enough of SemVer for our versions)"""
    core, _, pre = version.partition("-")
    nums = tuple(int(x) for x in core.split(".") if x.isdigit())
    return nums, (0, tuple(int(p) if p.isdigit() else p for p in pre.split("."))) if pre else (1, ())


def manifest(folder, channel):
    feed = json.loads((folder / f"releases.{channel}.json").read_text(encoding="utf-8-sig"))
    full = [a for a in feed.get("Assets", []) if a.get("Type") == "Full"]
    if not full:
        sys.exit(f"no full package in releases.{channel}.json")
    newest = max(full, key=lambda a: semver_key(a["Version"]))
    version = newest["Version"]
    out = dict(product="无限工坊", id=PACK_ID, version=version, channel=channel, date=time.strftime("%Y-%m-%d"),
               notes=(newest.get("NotesMarkdown") or "").strip(), package=dict(file=newest["FileName"], size=newest["Size"]),
               requires=dict(windows="64 位 Windows 10（1809 或更新）或 Windows 11",
                             webview2="WebView2 运行时（Windows 11 自带，缺了程序会提示安装）"))
    for key, made, name in (("setup", f"{PACK_ID}-{channel}-Setup.exe", f"WuxianWorkshop-{version}-Setup.exe"),
                            ("portable", f"{PACK_ID}-{channel}-Portable.zip", f"WuxianWorkshop-{version}-Portable.zip")):
        src, dst = folder / made, folder / name
        if src.is_file():
            shutil.move(src, dst)                     # replaces one of the same version made earlier
        if dst.is_file():
            out[key] = dict(file=name, size=dst.stat().st_size, sha256=sha256(dst))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--channel", default="win")
    ap.add_argument("--dir", default=str(ROOT / "dist" / "releases"))
    args = ap.parse_args()
    folder = Path(args.dir)
    data = manifest(folder, args.channel)
    (folder / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    setup = data.get("setup", {})
    print(f"latest.json: {data['version']} ({data['channel']}), setup {setup.get('file')} {setup.get('size', 0) / 1048576:.1f} MB")


if __name__ == "__main__":
    main()
