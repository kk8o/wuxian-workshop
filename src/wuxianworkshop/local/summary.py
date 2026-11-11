r"""An extension's summary: the facts a player (and an agent) wants before the extension is approved, shared, replaced
or packed, worked out one way and shown everywhere (the App's detail page and approval panel, the comparison before a
package replaces a version, a pack's result, extension_summary). It says what is there, never that it is safe.

    {"from": "folder" | "package" | "game",       where it was read: an extension's folder, a package (unpacked to look),
                                                 an addon found in the game's AddOns that no extension folder brings
     "id", "title", "version", "notes",          the manifest's (notes: else the first addon's .toc Notes)
     "author", "homepage",                       as the manifest says them: nobody checked who wrote it
     "package": {"sha256", "file"} | None,       the package it is, or was imported from (its SHA-256)
     "where": "game" | "local" | "both",
     "risk": "game" | "local",                   local: a program runs on this computer with the player's rights;
                                                 game: only addons, which run in the game under its limits
     "game": [{"folder", "title", "notes", "version", "author",
               "needs": [{"addon", "installed"}],    the other addons its .toc depends on (not WuxianKit, not its own);
                                                     installed None when the game's AddOns folder is not known
               "path"}],                             where it is in the game's AddOns now, None when it is not there
     "addons_dir": the game's AddOns folder or None,
     "local": None | {"run": [argv as written], "python": the manifest's own interpreter (relative) or None,
                      "start": "use" | "workshop", "env": [names the manifest sets], "pass": [names of the player's
                      environment variables it gets]},
     "executables": [paths],                     the files with a program's suffix (EXECUTABLES) outside addons/
     "agent": {"docs": path or None, "skills": [names], "apps": [ids],
               "adds": [{"app", "wake": the capability or None, "skill": path or None}]},   the texts an agent reads
     "files": n, "size": bytes}                  what a package of it holds (pack.kept)

Paths are relative to the extension's folder, with /. compare() says what differs between two summaries, field by field.
detected() finds the addons in the game's AddOns that give WuxianKit an extension and that no extension folder put
there; lines() and changed() say a summary and a comparison as text (the command line, an agent's answer).
Standard library, the manifest and the .toc reader only.
"""
from pathlib import Path, PurePosixPath

from . import manifest as M
from . import pack as K

EXECUTABLES = (".exe", ".dll", ".pyd", ".com", ".scr", ".msi", ".bat", ".cmd", ".ps1", ".vbs")
OWN_ADDONS = ("wuxiankit",)             # what every game half depends on: not a need to tell
SKILLS_MOST = 64


def _rel(path, folder):
    """a path of the manifest's as the summary says it: relative to the folder with /, as it is when it lies elsewhere"""
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(Path(folder).resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _skills(folder):
    try:
        return sorted(p.stem for p in Path(folder).glob("*.md") if p.is_file())[:SKILLS_MOST] if folder else []
    except OSError:
        return []


def _there(addons_dir, name):
    """the folder of an addon in the game's AddOns, or None (also when the AddOns folder is not known)"""
    if not addons_dir:
        return None
    try:
        p = Path(addons_dir) / name
        return p if p.is_dir() else None
    except OSError:
        return None


def addon(folder, addons_dir=None, own=()):
    """one addon folder (an extension's addons/<Name>, or a folder in the game's AddOns) as the summary's game lists
    it; the second answer: its .toc's headers ({} when it has none)"""
    from ..installer.addons import describe, read_toc
    folder = Path(folder)
    try:
        d = describe(folder)
        toc = (read_toc(folder / d["toc"]) if d["toc"] else None) or {}
    except OSError:
        d, toc = dict(title=folder.name, notes=None, version=None, author=None, deps=[]), {}
    mine = {n.lower() for n in own} | set(OWN_ADDONS) | {folder.name.lower()}
    needs, seen = [], set()
    for dep in d["deps"]:
        if dep.lower() in mine or dep.lower() in seen:
            continue
        seen.add(dep.lower())
        needs.append(dict(addon=dep, installed=(_there(addons_dir, dep) is not None) if addons_dir else None))
    at = _there(addons_dir, folder.name)
    return dict(folder=folder.name, title=d["title"], notes=d["notes"], version=d["version"], author=d["author"],
                needs=needs, path=str(at) if at else None), toc


def of_folder(m, addons_dir=None, source="folder", package=None):
    """the summary of an extension by its manifest (check_manifest's, not None). source: "folder" or "package";
    package: {sha256, file} of the package it is or came from"""
    folder = Path(m["folder"])
    try:
        files, _ = K.kept(folder, m["pack"]["exclude"])
    except OSError:
        files = []
    size = 0
    for _, path in files:
        try:
            size += path.stat().st_size
        except OSError:
            pass
    game = [addon(folder / M.ADDONS_DIR / name, addons_dir, m["addons"])[0] for name in m["addons"]]
    local = None
    if m["run"]:
        local = dict(run=list(m["run"]), python=_rel(m["python"], folder), start=m["start"], env=sorted(m["env"]))
        local["pass"] = list(m["pass"])
    adds = [dict(app=app, wake=(a.get("wake") or {}).get("call"), skill=_rel(a.get("skill"), folder))
            for app, a in sorted((m.get("adds") or {}).items())]
    notes = m["notes"] or next((g["notes"] for g in game if g["notes"]), None)
    return dict({"from": source}, id=m["id"], title=m["title"], version=m["version"], notes=notes, author=m["author"],
                homepage=m["homepage"], package=dict(package) if package else None, where=M.where_of(m),
                risk="local" if m["run"] else "game", game=game, addons_dir=str(addons_dir) if addons_dir else None,
                local=local,
                executables=[rel for rel, _ in files
                             if not K.in_addons(rel) and PurePosixPath(rel).suffix.lower() in EXECUTABLES],
                agent=dict(docs=_rel(m["docs"], folder), skills=_skills(m["skills"]),
                           apps=[a["app"] for a in m["apps"]], adds=adds),
                files=len(files), size=size)


def of_addon(folder, addons_dir=None):
    """the summary of an addon in the game's AddOns that gives WuxianKit an extension (its .toc's
    ## X-WuxianKit-Extension) and that no extension folder brings; None when its .toc names no extension"""
    folder = Path(folder)
    row, toc = addon(folder, addons_dir if addons_dir else folder.parent)
    eid = (toc.get("X-WuxianKit-Extension") or "").strip()
    if not M.ID.fullmatch(eid):
        return None
    files = size = 0
    try:
        for p in folder.rglob("*"):
            if p.is_file():
                files, size = files + 1, size + p.stat().st_size
    except OSError:
        pass
    site = (toc.get("X-Website") or "").strip()
    docs = folder / "AGENT.md"
    row["path"] = str(folder)
    return dict({"from": "game"}, id=eid, title=row["title"], version=row["version"], notes=row["notes"],
                author=row["author"], homepage=site if site.startswith(("http://", "https://")) else None, package=None,
                where="game", risk="game", game=[row], addons_dir=str(addons_dir if addons_dir else folder.parent),
                local=None, executables=[],
                agent=dict(docs="AGENT.md" if docs.is_file() else None, skills=_skills(folder / "skills"), apps=[],
                           adds=[]),
                files=files, size=size)


def detected(addons_dir, skip=(), official=()):
    """the addons in the game's AddOns that give WuxianKit an extension (of_addon) and that no local extension put there
    (those hold gameside's mark): [the summary, with official: its extension's id is one of `official` (WuxianKit's
    own)], by folder. skip: the folder names not to list (the catalog's: they have their rows)"""
    from ..installer.addons import find_toc
    from .gameside import MARK
    skip = {name.lower() for name in skip}
    try:
        folders = sorted((p for p in Path(addons_dir).iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return []
    out = []
    for folder in folders:
        try:
            if folder.name.lower() in skip or (folder / MARK).exists():
                continue
            toc = find_toc(folder)
            if toc is None or b"X-WuxianKit-Extension" not in toc.read_bytes():
                continue
        except OSError:
            continue
        s = of_addon(folder, addons_dir)
        if s is not None:
            out.append(dict(s, official=s["id"] in official))
    return out


# --- what differs ----------------------------------------------------------------------------------------------------

def _facts(s):
    """a summary as the flat facts compare() goes through: {field: a text, a number, None, or a list of texts}"""
    local, agent = s.get("local") or {}, s.get("agent") or {}
    needs = []
    for g in s.get("game") or []:
        needs += [n["addon"] for n in g.get("needs") or [] if n["addon"] not in needs]
    return dict(
        version=s.get("version"), title=s.get("title"), author=s.get("author"), homepage=s.get("homepage"),
        where=s.get("where"), run=" ".join(local.get("run") or []) or None, python=local.get("python"),
        start=local.get("start") if local else None, env=list(local.get("env") or []),
        **{"pass": list(local.get("pass") or [])},
        executables=list(s.get("executables") or []), addons=[g["folder"] for g in s.get("game") or []], needs=needs,
        docs=agent.get("docs"), skills=list(agent.get("skills") or []), apps=list(agent.get("apps") or []),
        adds=[f"{a['app']}: " + ", ".join(k for k in ("wake", "skill") if a.get(k)) for a in agent.get("adds") or []],
        files=s.get("files"), size=s.get("size"))


FIELDS = ("version", "title", "author", "homepage", "where", "run", "python", "start", "env", "pass", "executables",
          "addons", "needs", "docs", "skills", "apps", "adds", "files", "size")


def compare(old, new):
    """what differs between two summaries, in FIELDS' order: [{field, old, new}] for a text or a number,
    [{field, added, removed}] for a list. [] when the summaries say the same (the files may still differ: the summary
    counts them, it does not read them; `same`() tells two packages apart)"""
    a, b = _facts(old), _facts(new)
    out = []
    for field in FIELDS:
        x, y = a[field], b[field]
        if isinstance(x, list) or isinstance(y, list):
            x, y = x or [], y or []
            added, removed = [v for v in y if v not in x], [v for v in x if v not in y]
            if added or removed:
                out.append(dict(field=field, added=added, removed=removed))
        elif x != y:
            out.append(dict(field=field, old=x, new=y))
    return out


def same(old, new):
    """True when both summaries name a package's SHA-256 and it is the same one; None when one names none"""
    a, b = ((s.get("package") or {}).get("sha256") for s in (old, new))
    return (a == b) if a and b else None


# --- as text ---------------------------------------------------------------------------------------------------------

WHERE = dict(game="in the game only", local="on this computer", both="in the game and on this computer")


def _size(n):
    return f"{n} bytes" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def lines(s):
    """a summary as lines of text: the facts, in the order a reader wants them. Nothing here says it is safe"""
    out = [" ".join(x for x in (s.get("id"), s.get("version"), f"\"{s['title']}\"" if s.get("title") else None) if x)]
    package = s.get("package")
    if package:
        out.append(f"  package: {package.get('file')}, sha256 {package.get('sha256')}")
    who = [f"author {s['author']} (as it says itself: not verified)" if s.get("author") else None, s.get("homepage")]
    if any(who):
        out.append("  from: " + ", ".join(w for w in who if w))
    out.append(f"  runs: {WHERE.get(s.get('where'), s.get('where'))}")
    for g in s.get("game") or []:
        needs = ", ".join(n["addon"] + {True: "", False: " (not installed)", None: ""}[n.get("installed")]
                          for n in g.get("needs") or [])
        out.append(f"  game addon: {g['folder']}" + (f" {g['version']}" if g.get("version") else "")
                   + (f", needs {needs}" if needs else "")
                   + (f", at {g['path']}" if g.get("path") else ", not in the game's AddOns"))
    local = s.get("local")
    if local:
        out.append("  program: " + " ".join(local.get("run") or [])
                   + (", started with 无限工坊" if local.get("start") == "workshop" else ", started on use")
                   + (f", its own interpreter {local['python']}" if local.get("python") else ""))
        if local.get("env"):
            out.append("    sets the variables: " + ", ".join(local["env"]))
        if local.get("pass"):
            out.append("    gets the player's variables: " + ", ".join(local["pass"]))
    if s.get("executables"):
        out.append("  executables: " + ", ".join(s["executables"]))
    agent = s.get("agent") or {}
    reads = [agent.get("docs"), ("skills " + ", ".join(agent["skills"])) if agent.get("skills") else None,
             ("apps " + ", ".join(agent["apps"])) if agent.get("apps") else None]
    reads += [f"adds to the app {a['app']}: " + ", ".join(k for k in ("wake", "skill") if a.get(k))
              for a in agent.get("adds") or []]
    if any(reads):
        out.append("  an agent reads: " + "; ".join(r for r in reads if r))
    if s.get("files") is not None:
        out.append(f"  {s['files']} files, {_size(s.get('size') or 0)}")
    return out


def changed(changes):
    """compare()'s answer as lines of text"""
    out = []
    for c in changes or []:
        if "added" in c:
            parts = [("+ " + ", ".join(c["added"])) if c["added"] else None,
                     ("- " + ", ".join(c["removed"])) if c["removed"] else None]
            out.append(f"{c['field']}: " + "; ".join(p for p in parts if p))
        else:
            old, new = (("none" if v is None else _size(v) if c["field"] == "size" else str(v)) for v in (c["old"], c["new"]))
            out.append(f"{c['field']}: {old} -> {new}")
    return out
