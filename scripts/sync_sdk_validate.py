r"""Copies the shared checks of a local extension's declarations into the Python SDK.

    .venv\Scripts\python.exe scripts\sync_sdk_validate.py            write sdk\python\wuxiankit.py when it differs
    .venv\Scripts\python.exe scripts\sync_sdk_validate.py --check    change nothing; exit status 1 when it differs

What a capability, a source and an option may be, and how a call's arguments are checked, is written once: the text
between the two marker lines ("# ---- shared: begin" and "# ---- shared: end") of src\wuxianworkshop\local\validate.py.
The SDK is a single file that works without the workshop, so it carries a copy between the same two marker lines. Change
the checks in validate.py, run this, commit both; tests\sdk\test_validate_sync.py fails while the two differ. The block
uses json, math and re only, which both files import.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "wuxianworkshop" / "local" / "validate.py"
TARGET = ROOT / "sdk" / "python" / "wuxiankit.py"
BEGIN, END = "# ---- shared: begin", "# ---- shared: end"


def split(text: str, name: str = "the text") -> tuple:
    """(before, block, after) of a text with "\n" line ends: before ends with the begin marker's line, after starts
    with the end marker's line. ValueError unless each marker starts exactly one line, begin first"""
    lines = text.split("\n")
    begins = [i for i, line in enumerate(lines) if line.startswith(BEGIN)]
    ends = [i for i, line in enumerate(lines) if line.startswith(END)]
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        raise ValueError(f"{name}: one line starting with {BEGIN!r}, then one starting with {END!r}")
    b, e = begins[0], ends[0]
    return "\n".join(lines[:b + 1]) + "\n", "".join(line + "\n" for line in lines[b + 1:e]), "\n".join(lines[e:])


def read(path: Path) -> tuple:
    """(the file's text with "\n" line ends, its line end)"""
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" if "\r\n" in raw else "\n"


def block(path: Path) -> str:
    """the shared block of a file, with "\n" line ends"""
    return split(read(path)[0], path.name)[1]


def synced(source: Path = SOURCE, target: Path = TARGET) -> bool:
    return block(source) == block(target)


def sync(source: Path = SOURCE, target: Path = TARGET) -> bool:
    """put the source's block into the target, keeping the target's line ends; True when the target changed"""
    text, eol = read(target)
    before, old, after = split(text, target.name)
    new = block(source)
    if new == old:
        return False
    target.write_bytes((before + new + after).replace("\n", eol).encode("utf-8"))
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="copy the shared checks of local/validate.py into the Python SDK")
    ap.add_argument("--check", action="store_true", help="change nothing; exit status 1 when the SDK's copy differs")
    args = ap.parse_args(argv)
    if args.check:
        same = synced()
        print("the SDK's copy is the same" if same else
              f"{TARGET.relative_to(ROOT)} differs from {SOURCE.relative_to(ROOT)}: run scripts/sync_sdk_validate.py")
        return 0 if same else 1
    print(f"{TARGET.relative_to(ROOT)}: {'updated' if sync() else 'already the same'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
