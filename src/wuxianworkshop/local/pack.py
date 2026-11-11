r"""An extension package: a local extension's folder as one zip, which `wuxian ext pack` makes and extension_add (or the
扩展 page) installs into the extensions' home folder (local/home.py).

    voice-0.2.0.zip
      voice/wuxian-extension.json     the manifest (local/manifest.py)
      voice/main.py, voice/AGENT.md ...
      voice/addons/WuxianVoice/...    its game half (local/gameside.py)

pack() writes one: the folder's files under one top folder named after the extension's id, paths with /, sorted by path,
every time stamp the same, so that the same files give the same zip (the same SHA-256). It never follows a link (a
symbolic link or a junction is left out) and leaves out what EXCLUDE names (version control, caches, virtual
environments, logs, *.local.* and .env* files: what belongs to one computer) and what the manifest's pack.exclude names;
a pattern without / is matched against the name of every file and folder on the way, one with / against the path
relative to the folder (fnmatch, without case). The manifest itself is always in. Nothing is installed with a package:
an extension uses the standard library or brings what it needs.

read() looks at a package without unpacking it, unpack() unpacks it; both check every member first, as a package comes
from someone else: no absolute path, no "..", no drive or stream (":"), no backslash, no link or other special file, no
name Windows reads as a device (CON, NUL, COM1 ...) or cuts (a dot or space last), no path twice (without case), no path
that is a file and a folder, nothing encrypted; at most MAX_FILES files, MAX_ZIP bytes of zip and MAX_UNPACKED bytes
unpacked, by the sizes the zip declares, counted again while unpacking. The manifest is at the top, or in the one folder
the top holds (whatever its name: unpack() takes that level away). Under addons/ only the kinds of file an addon has
(extensions.KINDS) are taken; pack() leaves out the names there that start with a dot and refuses the other kinds, so
that it never makes a package read() refuses.

Standard library and this package's manifest checks only.
"""
import fnmatch
import hashlib
import json
import os
import shutil
import stat
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from .. import extensions as X
from . import manifest as M

MAX_ZIP = 64 * 1024 * 1024
MAX_UNPACKED = 256 * 1024 * 1024
MAX_FILES = 8000
MAX_MANIFEST = 1 << 20              # bytes of a manifest read from a package
EXCLUDE = (".git", ".hg", ".svn", "__pycache__", "*.pyc", ".venv*", "venv", "node_modules", ".pytest_cache",
           ".mypy_cache", ".idea", ".vscode", "logs", "cache", "*.log", "*.local.*", ".env*", "Thumbs.db", ".DS_Store")
STAMP = (1980, 1, 1, 0, 0, 0)       # every member's time: the same files give the same zip
DEVICES = ({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
           | {f"{d}{n}" for d in ("COM", "LPT") for n in "0123456789¹²³"})
METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA)
MODE = 0o600 << 16                  # every member's attributes: what zipfile gives a member that says nothing
CHUNK = 1 << 20


class PackError(Exception):
    """a package that cannot be made or taken: status and code as the API answers them (400 bad_manifest with the
    manifest's problems, 400 bad_package, 400 bad_request for an exclude that is no list of patterns, 413
    package_too_big, 409 not_empty), problems when there are some"""

    def __init__(self, status, code, message, problems=None):
        super().__init__(message)
        self.status, self.code, self.problems = status, code, problems


def _bad(message):
    return PackError(400, "bad_package", message)


def _too_big(message):
    return PackError(413, "package_too_big", message)


# --- what goes in ----------------------------------------------------------------------------------------------------

def left_out(rel, patterns):
    """whether the patterns leave out a path relative to the folder (posix): a pattern without / is matched against the
    path's last name (the walk asks for every folder on the way), one with / against the whole path; without case"""
    low = rel.lower()
    name = low.rsplit("/", 1)[-1]
    for pattern in patterns:
        p = pattern.strip().lower().rstrip("/")
        if p and fnmatch.fnmatchcase(low if "/" in p else name, p):
            return True
    return False


def hidden(rel):
    """whether a path's last name starts with a dot"""
    return rel.rsplit("/", 1)[-1].startswith(".")


def in_addons(rel):
    """whether a path relative to an extension's folder is under its addons/ (the game half)"""
    return rel.lower().startswith(M.ADDONS_DIR + "/")


def walk(folder, leave=None):
    """([(posix path relative to the folder, Path)] of its files sorted by path, [what was left out], sorted too: the
    paths leave(path) is true for, a folder's with / last and nothing below it looked at, and the links, which are
    never followed). OSError when a folder cannot be read"""
    files, skipped = [], []
    stack = [(Path(folder), "")]
    while stack:
        here, prefix = stack.pop()
        with os.scandir(here) as it:
            entries = list(it)
        for e in entries:
            rel = prefix + e.name
            if e.is_symlink() or e.is_junction():
                skipped.append(rel)
                continue
            is_dir = e.is_dir(follow_symlinks=False)
            if leave is not None and leave(rel):
                skipped.append(rel + "/" if is_dir else rel)
            elif is_dir:
                stack.append((Path(e.path), rel + "/"))
            elif e.is_file(follow_symlinks=False):
                files.append((rel, Path(e.path)))
            else:
                skipped.append(rel)
    return sorted(files), sorted(skipped)


def kept(folder, exclude=()):
    """(files, skipped) of an extension's folder as a package takes them: walk()'s, with what EXCLUDE and the patterns
    in `exclude` (the manifest's pack.exclude) name left out, and the names under addons/ that start with a dot; the
    manifest itself is always in. What pack() packs and summary.py counts"""
    patterns = EXCLUDE + tuple(exclude or ())

    def leave(rel):
        if rel.lower() == M.MANIFEST:
            return False
        return left_out(rel, patterns) or (in_addons(rel) and hidden(rel))

    return walk(folder, leave)


def part_problem(part):
    """why a name cannot be one part of a path in a package, or None"""
    if part in ("", ".", ".."):
        return "an empty, . or .. part"
    if len(part) > 255:
        return "a name of more than 255 characters"
    if any(ord(c) < 32 or c in '<>:"|?*\\\x7f' for c in part):
        return "a character Windows takes in no file name"
    if part[-1] in ". ":
        return "a name that ends with a dot or a space"
    if part.split(".")[0].rstrip(" ").upper() in DEVICES:
        return "a device's name on Windows"
    return None


def _target(to, eid, version):
    """the zip to write: `to` itself when it names a .zip, else <id>-<version or 0>.zip in the folder `to`"""
    to = Path(to)
    if to.suffix.lower() == ".zip" and not to.is_dir():
        return to
    safe = "".join(c if c.isascii() and (c.isalnum() or c in "._+-") else "_" for c in version or "").strip(".")
    return to / f"{eid}-{safe or '0'}.zip"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def pack(folder, to, exclude=None):
    """a package of the extension in `folder` (or its manifest file), written to `to`: a folder (the file is then
    <id>-<version or 0>.zip in it; made when missing) or the path of a .zip (replaced when there). exclude: patterns
    in place of the manifest's pack.exclude; EXCLUDE counts always. dict(id, version, file, size, files, unpacked,
    sha256, skipped): the zip's path, its bytes, how many files it holds and their bytes, its SHA-256 and the paths
    left out (a folder's with / last). PackError: 400 bad_manifest (problems: the manifest's), 400 bad_package (a file
    under addons/ that is no addon's kind; something the manifest names is left out), 413 package_too_big"""
    folder, _ = M.locate(folder)
    manifest, problems = M.check_manifest(folder)
    if manifest is None:
        raise PackError(400, "bad_manifest", str(M.ManifestError(problems)), problems)
    eid = manifest["id"]
    if exclude is None:
        mine = manifest["pack"]["exclude"]
    else:
        found = []
        mine = M.check_pack({"exclude": list(exclude) if isinstance(exclude, (list, tuple)) else exclude}, "exclude",
                            found)["exclude"]
        if found:
            p = found[0]
            raise PackError(400, "bad_request", f"{p['where']}: {p['message']} ({p['hint']})", found)
    dest = _target(to, eid, manifest["version"])
    tmp = dest.with_name(dest.name + ".tmp")
    own = {str(p.resolve()).lower() for p in (dest, tmp)}         # the zip itself, when it is written into the folder
    try:
        files, skipped = kept(folder, mine)
    except OSError as e:
        raise _bad(f"{folder} cannot be read: {e}") from None
    files = [(rel, path) for rel, path in files if str(path.resolve()).lower() not in own]
    if len(files) > MAX_FILES:
        raise _too_big(f"{len(files)} files, more than the {MAX_FILES} a package holds: leave some out with "
                       f"pack.exclude in {M.MANIFEST}")
    names = {rel.lower() for rel, _ in files}
    for rel, _ in files:
        why = next((w for w in map(part_problem, rel.split("/")) if w), None)
        if why:
            raise _bad(f"{rel}: {why}")
        if in_addons(rel) and PurePosixPath(rel).suffix.lower() not in X.KINDS:
            raise _bad(f"{rel}: not a kind of file an addon has ({', '.join(sorted(X.KINDS))}): move it out of "
                       f"{M.ADDONS_DIR}/, or leave it out with pack.exclude in {M.MANIFEST}")
    base = folder.resolve()
    if manifest["python"]:
        python = Path(manifest["python"]).resolve()
        if base in python.parents and python.relative_to(base).as_posix().lower() not in names:
            raise _bad(f"{M.MANIFEST}: python is {python.relative_to(base).as_posix()}, which a package leaves out: an "
                       "extension in a package runs on 无限工坊's own Python (leave python out) or brings an interpreter "
                       "that is not in a folder EXCLUDE names")
    for name in manifest["addons"]:
        at = f"{M.ADDONS_DIR}/{name}/".lower()
        tocs = [n[len(at):] for n in names if n.startswith(at) and "/" not in n[len(at):] and n.endswith(".toc")]
        if not any(t == f"{name}.toc".lower() or t.startswith(f"{name}_".lower()) for t in tocs):
            raise _bad(f"{M.MANIFEST}: the addon {name} would be in the package without its .toc: do not leave it out")
    try:
        total = sum(path.stat().st_size for _, path in files)
        if total > MAX_UNPACKED:
            raise _too_big(f"{total // (1024 * 1024)} MB of files, more than the {MAX_UNPACKED // (1024 * 1024)} MB a "
                           f"package holds: leave some out with pack.exclude in {M.MANIFEST}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name, path in sorted((M.MANIFEST if rel.lower() == M.MANIFEST else rel, path) for rel, path in files):
                info = zipfile.ZipInfo(f"{eid}/{name}", date_time=STAMP)
                info.compress_type, info.create_system, info.external_attr = zipfile.ZIP_DEFLATED, 0, MODE
                with open(path, "rb") as src, z.open(info, "w") as out:
                    total += _copy(src, out, MAX_UNPACKED - total)
        size = tmp.stat().st_size
        if size > MAX_ZIP:
            raise _too_big(f"the zip is {size // (1024 * 1024)} MB, more than the {MAX_ZIP // (1024 * 1024)} MB of a "
                           f"package: leave some out with pack.exclude in {M.MANIFEST}")
        sha = _sha256(tmp)
        os.replace(tmp, dest)
    except OSError as e:
        raise _bad(f"the package could not be written: {e}") from None
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return dict(id=eid, version=manifest["version"], file=str(dest), size=size, files=len(files), unpacked=total,
                sha256=sha, skipped=skipped)


# --- what comes in ---------------------------------------------------------------------------------------------------

def _copy(src, out, most):
    """copies src to out and answers the bytes copied; PackError (413) as soon as they are more than `most`"""
    n = 0
    while True:
        block = src.read(CHUNK)
        if not block:
            return n
        n += len(block)
        if n > most:
            raise _too_big("more bytes than it says, or than a package may hold")
        out.write(block)


def _member(info):
    """(the parts of a member's path, whether it is a folder), its name and kind checked"""
    raw = info.orig_filename                     # as the zip says it: zipfile itself cuts at a NUL and turns \ into /
    if "\\" in raw or "\0" in raw:
        raise _bad(f"{raw!r}: a backslash or a NUL in a name")
    if raw.startswith("/"):
        raise _bad(f"{raw}: an absolute path")
    is_dir = raw.endswith("/")
    parts = raw[:-1].split("/") if is_dir else raw.split("/")
    for part in parts:
        why = part_problem(part)
        if why:
            raise _bad(f"{raw!r}: {why}")
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind == stat.S_IFLNK:
        raise _bad(f"{raw}: a link")
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise _bad(f"{raw}: not a file")
    if not is_dir:
        if info.flag_bits & 0x1:
            raise _bad(f"{raw}: encrypted")
        if info.compress_type not in METHODS:
            raise _bad(f"{raw}: compressed in a way this cannot read")
    return parts, is_dir


def _scan(z):
    """(root, [(ZipInfo, the parts of its path under the root)], bytes the files unpack to as declared) of an open
    package, every member checked; root: the name of its one top folder, "" when the manifest is at the top"""
    infos = z.infolist()
    if len(infos) > MAX_FILES:
        raise _too_big(f"{len(infos)} entries, more than the {MAX_FILES} a package holds")
    files, seen, above, total = [], set(), set(), 0
    for info in infos:
        parts, is_dir = _member(info)
        keys = ["/".join(parts[:i]).casefold() for i in range(1, len(parts) + 1)]
        above.update(keys if is_dir else keys[:-1])
        if is_dir:
            continue
        if keys[-1] in seen:
            raise _bad(f"{info.orig_filename}: twice in it")
        seen.add(keys[-1])
        total += info.file_size
        if total > MAX_UNPACKED:
            raise _too_big(f"more than {MAX_UNPACKED // (1024 * 1024)} MB unpacked")
        files.append((info, parts))
    clash = sorted(seen & above)
    if clash:
        raise _bad(f"{clash[0]}: a file and a folder")
    if any(parts == [M.MANIFEST] for _, parts in files):
        root = ""
    else:
        tops = {parts[0] for _, parts in files}
        root = next(iter(tops)) if len(tops) == 1 else None
        if root is None or not any(parts == [root, M.MANIFEST] for _, parts in files):
            raise _bad(f"no {M.MANIFEST} at its top or in the one folder there: not an extension package")
        files = [(info, parts[1:]) for info, parts in files]
    for info, parts in files:
        if (len(parts) > 1 and parts[0].casefold() == M.ADDONS_DIR
                and PurePosixPath(parts[-1]).suffix.lower() not in X.KINDS):
            raise _bad(f"{info.orig_filename}: not a kind of file an addon has")
    return root, files, total


def _open(zip_path):
    """the package opened, its size checked"""
    path = Path(zip_path)
    try:
        size = path.stat().st_size
        if not path.is_file():
            raise _bad(f"{path} is not a file")
        if size > MAX_ZIP:
            raise _too_big(f"{path.name} is {size // (1024 * 1024)} MB, more than the {MAX_ZIP // (1024 * 1024)} MB "
                           "of a package")
        return zipfile.ZipFile(path), size
    except FileNotFoundError:
        raise _bad(f"there is no {path}") from None
    except (zipfile.BadZipFile, OSError) as e:
        raise _bad(f"{path.name} is not a zip: {e}") from None


READ_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, OSError, ValueError)


def read(zip_path):
    """what a package holds, nothing unpacked: dict(id, version, title, manifest, root, files, size, unpacked);
    manifest: its wuxian-extension.json as written (only the id is checked: check_manifest wants the folder, after
    unpack()), root: the name of its top folder ("" when the manifest is at the top), files: how many, size: the zip's
    bytes, unpacked: the files' bytes as declared. PackError: 400 bad_package (not a zip, a member that is not taken,
    no manifest or one without a good id), 413 package_too_big"""
    z, size = _open(zip_path)
    with z:
        try:
            root, files, total = _scan(z)
            info = next(i for i, parts in files if parts == [M.MANIFEST])
            if info.file_size > MAX_MANIFEST:
                raise _bad(f"{M.MANIFEST}: {info.file_size} bytes is no manifest")
            d = json.loads(z.read(info).decode("utf-8-sig"))
        except PackError:
            raise
        except READ_ERRORS as e:
            raise _bad(f"{Path(zip_path).name}: {M.MANIFEST} cannot be read: {e}") from None
    if not isinstance(d, dict):
        raise _bad(f"{M.MANIFEST}: it must be a JSON object")
    p = M.id_problem(d.get("id"), f"{M.MANIFEST}: id")
    if p is None and not M.ID.fullmatch(d["id"]):                # ID's $ lets a line end through
        p = M.id_problem(None, f"{M.MANIFEST}: id")
    if p:
        raise PackError(400, "bad_package", f"{p['where']}: {p['message']} ({p['hint']})", [p])
    version = d.get("version")
    return dict(id=d["id"], version=M.text_of(version, 32) if version is not None else None,
                title=M.text_of(d.get("title"), M.TITLE_MAX) or d["id"], manifest=d, root=root, files=len(files),
                size=size, unpacked=total)


def _empty(folder):
    for p in Path(folder).iterdir():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
            except OSError:
                pass


def unpack(zip_path, into):
    """the package's files written into the folder `into` (empty; made when missing) without its top folder; answers
    `into` as a Path. Every member is checked first as read() does, and the bytes are counted again as they come.
    Folders without files are not made. PackError: read()'s, 409 not_empty; when it fails `into` is left empty. The
    caller checks the manifest there next (check_manifest)"""
    into = Path(into)
    try:
        into.mkdir(parents=True, exist_ok=True)
        full = any(into.iterdir())
    except OSError as e:
        raise _bad(f"{into} cannot be used: {e}") from None
    if full:
        raise PackError(409, "not_empty", f"{into} is not empty: a package is unpacked into an empty folder")
    z, _ = _open(zip_path)
    with z:
        try:
            _, files, _ = _scan(z)
            total = 0
            for info, parts in files:
                dest = into.joinpath(*parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dest, "xb") as out:
                    n = _copy(src, out, min(info.file_size, MAX_UNPACKED - total))
                if n != info.file_size:
                    raise _bad(f"{info.orig_filename}: {n} bytes, it says {info.file_size}")
                total += n
        except PackError:
            _empty(into)
            raise
        except READ_ERRORS as e:
            _empty(into)
            raise _bad(f"{Path(zip_path).name} cannot be unpacked: {e}") from None
    return into
