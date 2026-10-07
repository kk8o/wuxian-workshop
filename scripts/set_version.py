r"""Sets the one version of 无限工坊 everywhere it is written. The addons always carry the program's version: the program
brings its addons along (the daemon installs them when the game folder has another version), so an update of the
program is an update of the addons too.

    .venv\Scripts\python.exe scripts\set_version.py 0.9.2      sets it in every place below
    .venv\Scripts\python.exe scripts\set_version.py             lists what each place says

tests/test_versions.py fails when the places disagree.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (file, pattern whose group 1 is the version)
PLACES = [
    ("pyproject.toml", r'(?m)^version = "([^"]+)"'),
    ("src/wuxianworkshop/__init__.py", r'(?m)^__version__ = "([^"]+)"'),
    ("src/wuxianworkshop/transport/link.py", r'(?m)^VERSION = "([^"]+)"'),
    ("addon/WoWBridge/WoWBridge.toc", r"(?m)^## Version: (\S+)"),
    ("addon/WoWBridge/WoWBridge.lua", r'(?m)^local VERSION = "([^"]+)"'),
    ("addon/WoWBridge/Link.lua", r'(?m)^L\.VERSION = "([^"]+)"'),
    ("addon/!WuxianWorkshop/!WuxianWorkshop.toc", r"(?m)^## Version: (\S+)"),
]


def found(root=ROOT):
    """{file: the version it says, or None when the pattern is not there}"""
    out = {}
    for rel, pattern in PLACES:
        m = re.search(pattern, (root / rel).read_text(encoding="utf-8"))
        out[rel] = m.group(1) if m else None
    return out


def set_version(version, root=ROOT):
    """writes version into every place; returns the files that changed"""
    if not re.fullmatch(r"\d+\.\d+\.\d+([.-]?[0-9A-Za-z.]+)?", version):
        raise ValueError(f"{version!r}: a version like 0.9.2")
    changed = []
    for rel, pattern in PLACES:
        path = root / rel
        data = path.read_bytes()
        text = data.decode("utf-8")
        m = re.search(pattern, text)
        if not m:
            raise ValueError(f"{rel}: no version found ({pattern})")
        if m.group(1) == version:
            continue
        text = text[:m.start(1)] + version + text[m.end(1):]
        path.write_bytes(text.encode("utf-8"))              # line endings and all the rest as they were
        changed.append(rel)
    return changed


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        for rel, v in found().items():
            print(f"{v or '?':<12} {rel}")
        return 0
    changed = set_version(argv[0])
    for rel in changed:
        print(f"{argv[0]}  {rel}")
    print(f"{len(changed)} files changed; every place says {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
