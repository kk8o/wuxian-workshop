"""The guide (the MCP tool `guide`): from an idea to a verdict, all of it in one conversation with a coding agent.

One page routes (data/guide/index.md: three questions, the ladder from a probe without files to an extension on both
sides; its two tables are written here from the chapters' front matter), and a topic gives one chapter or one worked
case (data/guide/<topic>.md). The chapter local-extension is data/local-extension.md, the standard the tool
extension_guide gives as well: it stays where it is and has no front matter, so what the route says of it is OUTSIDE.

A chapter's front matter, between two lines `---`, one `name: value` a line: title (English), zh (the player's word
for it), tier (where on the ladder: 0-3, a route for a case), kind (chapter | case), order (a number: the route lists
them by it), summary (one sentence: when to read it)."""
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
FOLDER = DATA / "guide"
LOCAL = DATA / "local-extension.md"
INDEX = "index"
KINDS = ("chapter", "case")
FIELDS = ("title", "zh", "tier", "kind", "order", "summary")
OUTSIDE = {"local-extension": dict(
    path=LOCAL, title="A local extension", zh="本机扩展", tier="2", kind="chapter", order=40,
    summary="What the game cannot do (a voice, a model, files, the network, long work) as a process on this computer "
            "whose capabilities become wk_ tools: the manifest, the protocol, the Python library, the loop, the rules. "
            "The standard, long: read it in full before you write one.")}
MISSING = ("The guide is not installed in this build of 无限工坊 (data/guide is missing); update 无限工坊. "
           "Meanwhile: api_search and api_manual for the game's API, wk_docs for WuxianKit, extension_guide for "
           "local extensions.")


class NoTopic(LookupError):
    """no chapter of that name; `known` are the names there are"""

    def __init__(self, topic, known):
        super().__init__(f"no guide topic {topic!r}; the topics: {', '.join(known)}")
        self.topic, self.known = topic, list(known)


def parse(text):
    """a chapter's text as (front matter: {name: value}, body); no front matter: ({}, text)"""
    if not text.startswith("---"):
        return {}, text
    head, sep, body = text[3:].partition("\n---")
    if not sep:
        return {}, text
    meta = {}
    for line in head.splitlines():
        name, colon, value = line.partition(":")
        if colon and name.strip():
            meta[name.strip()] = value.strip()
    return meta, body.partition("\n")[2].lstrip("\n")


def topics():
    """the chapters and cases there are, as the route lists them: [{topic, title, zh, tier, kind, order, summary,
    path}], chapters first, each kind by its order"""
    found = {name: dict(topic=name, **meta) for name, meta in OUTSIDE.items() if meta["path"].is_file()}
    try:
        files = sorted(FOLDER.glob("*.md"))
    except OSError:
        files = []
    for path in files:
        if path.stem == INDEX:
            continue
        try:
            meta, _ = parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        try:
            order = int(meta.get("order", ""))
        except ValueError:
            order = 999
        kind = meta.get("kind") if meta.get("kind") in KINDS else "chapter"
        found[path.stem] = dict(topic=path.stem, title=meta.get("title") or path.stem, zh=meta.get("zh") or "",
                                tier=meta.get("tier") or "", kind=kind, order=order,
                                summary=meta.get("summary") or "", path=path)
    return sorted(found.values(), key=lambda t: (KINDS.index(t["kind"]), t["order"], t["topic"]))


def names():
    """the topics' names, in the route's order"""
    return [t["topic"] for t in topics()]


def table(rows, first):
    """the route's table of chapters or of cases: how to ask for each, where it is on the ladder, when to read it"""
    lines = [f"| {first} | Tier | When to read it |", "|---|---|---|"]
    for t in rows:
        name = f"`{t['topic']}`" + (f" {t['zh']}" if t["zh"] else "")
        lines.append(f"| {name} | {t['tier'] or '-'} | {t['summary']} |")
    return "\n".join(lines)


def route():
    """the page that routes: index.md with {{chapters}} and {{cases}} written from what is there"""
    try:
        _, body = parse((FOLDER / (INDEX + ".md")).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return MISSING
    every = topics()
    return (body.replace("{{chapters}}", table([t for t in every if t["kind"] == "chapter"], "Chapter"))
                .replace("{{cases}}", table([t for t in every if t["kind"] == "case"], "Case")))


def text(topic=None):
    """the route (no topic, or `index`), or one chapter or case without its front matter. NoTopic when there is none of
    that name (a name is taken as it is written in the route: lowercase, - between its words; _ and spaces pass)"""
    name = "-".join(str(topic or "").strip().lower().replace("_", " ").split())
    if not name or name == INDEX:
        return route()
    for t in topics():
        if t["topic"] == name:
            try:
                return parse(t["path"].read_text(encoding="utf-8"))[1]
            except (OSError, UnicodeDecodeError):
                break
    known = names()
    if not known:
        return MISSING
    raise NoTopic(topic, known)
