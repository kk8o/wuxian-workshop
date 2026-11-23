r"""Version compatibility: one declaration on the package itself, one judge here, one next step for the player.

The declaration (I1) is a .toc field of an addon:

    ## X-Wuxian-Requires: workshop 0.10.0, WuxianKit 0.7.0

comma separated `<name> <version>` items. name: `workshop` (无限工坊, this App) or an addon folder (compared
case-insensitively); version: 1 to 3 dot-separated numbers, missing parts 0. An item without a version, or one that does
not parse, is ignored. It states minimum versions only, judged when the named thing is there: whether it must be there
at all is the game's `## Dependencies`. The "has" side is judged by its leading numbers ("0.10.0.dev3" is 0.10.0); one
that has none ("?", empty, None) is not judged (met). A local extension's manifest says the same in its requires
{workshop?, kit?} (kit: the folder WuxianKit); a catalog entry (extensions.json) in its `needs`, older catalogs only in
`min_app` (needs.workshop).

The floor is the other direction: an extension made for an older WuxianKit than this one runs with. WuxianKit's own .toc
says `## X-WuxianKit-Floor: 0.6.0`, the lowest WuxianKit an extension may have been made for and still run on it (same
version syntax; none, or one that does not parse: nothing is too old). What an extension was made for (built_for): its
X-Wuxian-Requires item for WuxianKit; for a local extension the oldest of its requires.kit and what its addons declare; else
LEGACY (0.7.0: declarations began with WuxianKit 0.10.0, so an undeclared one was made for 0.7.0 or earlier, the best
case assumed). Too old iff built_for < floor. Only extension addons (their .toc says `## X-WuxianKit-Extension`, or the
game's WuxianKit reports them) and local extensions with a game half (addons, or an app they drive: "drives") are
judged (one without is never too old; its requires.kit still counts as a minimum); the `workshop` item plays no part,
WuxianKit itself and its built-in extensions are never too old.

The judge (judge()) is a pure function over a plain snapshot of what the daemon knows (Service.versions_snapshot):

    {"app": {"version": "0.10.1", "latest": "0.10.2" | None,      # this App, and the update feed's newer one
             "update": the updater's state (updater.py: available / downloading / ready / unsupported / idle /
                       checking / current / error) | None},
     "kit_needs": "0.7.0",                                         # what the App needs of WuxianKit (KIT_NEEDS)
     "folders": {"<Folder>": {"there": bool, "installed": .toc Version on disk | None, "running": what the game
                 process runs (WoWBridge's HELLO, WuxianKit's kit.ready, an extension addon's kit row `toc`) | None,
                 "needs": {name: version} of its .toc (or of its kit row), "title": str | None,
                 "extension": its .toc says X-WuxianKit-Extension,
                 "floor": (WuxianKit's only) the floor of the WuxianKit that runs: its manifest's when that manifest is
                          the running one's, else its .toc's X-WuxianKit-Floor | None}},
     "catalog": [catalog entries: {id, folder, version, title, needs?, min_app?}],
     "extensions": ["<Folder>", ...],      # extension addons the game's WuxianKit reports that the catalog does not list
     "local": [{"id", "title", "version", "requires": {workshop?, kit?}, "addons": ["<Folder>", ...],
                "drives": it drives an app in the game (an apps[].driver "local")}],
     "agents": {"seen": n, "stale": n} | None}

and gives status.versions (I2): {next, items}. Items in this order: workshop, bridge (WoWBridge), kit (WuxianKit),
addons (the catalog's that are there, then the extension addons WuxianKit reports), local (a local extension, its
manifest's requires and its addons' X-Wuxian-Requires together), agent (the MCP servers). An item's state, most severe
first: blocked (`unmet`: what it needs is not there in that version, judged against the running version when known,
else the installed one; or, nothing unmet, `old` {built, floor}: an extension too old for the WuxianKit's floor, whose fix
is the catalog's later version of it (update; its own needs first, as an update's chain) when that version itself clears
the floor (its needs.WuxianKit, else LEGACY), else none: its author must update it; text "<title> was made for WuxianKit
0.5.0; WuxianKit 0.10.0 runs extensions made for 0.6.0 or later: update <title>"), pending (installed differs from
running: restart_game; WoWBridge: the game runs an older one than the newer of its .toc and this App's version, as the
App brings its own WoWBridge and puts it in place once the game is closed; an agent's MCP server runs old code:
restart_agent), update (the catalog / update feed has a later one), ok, ahead (installed later than the catalog's: a
developer's, never prompted), unknown. Its `action` and `via` are the first step that fixes it; `next` is the one thing
to do first, the most severe item (on a tie the earlier one) followed down its chain: the fix of an unmet {name, needs}
is update_app for the workshop, restart_game for a folder whose installed version meets it and the running one does
not, update of the catalog entry of that folder whose version meets it unless that entry's own needs are not met (then
those first, DEPTH deep at most), else none. next: {severity, action, id (the item it is for), target (the item or
catalog entry the action acts on; None for none), via (with action update: the catalog entry to install when it is
another item's), text (English: agents read it, error messages end with it; the App renders its own words from the
other fields)}. Every item whose state is blocked, pending or update carries its own `text` too: the sentence next
would have if it were that item's. update_app's sentence says what the player clicks, by the updater's state
(app.update: available, downloading, ready, unsupported; none known: "update 无限工坊" alone). When next is an agent
item's, the report also has agent_next: the next without the agent items, what an agent whose own MCP server is not
stale is told (brief: another agent's server is the App page's business).

What the report does not do: refuse anything for a local extension. A local extension is the player's (or the agent's)
work in progress: its item shows blocked when it needs a newer WuxianKit or is too old for the floor, but 无限工坊
still registers, starts, loads and installs it (local/host.py stops one only for a too-old 无限工坊: requires_unmet);
install_gate is for catalog entries only.
"""
import os
import re
from pathlib import Path

WORKSHOP, KIT, BRIDGE = "workshop", "WuxianKit", "WoWBridge"
APP_TITLE = "无限工坊"
FIELD = "X-Wuxian-Requires"
FLOOR = "X-WuxianKit-Floor"                # WuxianKit's .toc: the lowest WuxianKit an extension may be made for
LEGACY = "0.7.0"                           # what an undeclared extension was made for (declarations began with 0.10.0)
DEPTH = 4                                  # steps a chain is followed at most
SEVERITY = {"blocked": 3, "pending": 2, "update": 1}
NAME = re.compile(r"^[A-Za-z0-9_!][A-Za-z0-9_.!-]{0,63}\Z")
NEEDS = re.compile(r"^\d{1,6}(\.\d{1,6}){0,2}\Z")
HAS = re.compile(r"^\s*v?(\d{1,9}(?:\.\d{1,9})*)")
RESTART = "restart the game fully (not /reload: the game reads .toc files and new files only when it starts)"
CLOSE = (f"close the game fully (not /reload) so {APP_TITLE} puts its new WoWBridge in place, then start the game "
         f"again")
SITE = "https://wuxianwow.com/workshop"
WHERE = "on the App's 设置 (Settings) page, 关于与更新 (About & updates)"


# --- I1: the declaration and the comparison ---------------------------------------------------------------------------

def canon(name):
    """the name as kept: "workshop" and "WuxianKit" in their own spelling (any case matches), other folders as written"""
    low = name.lower()
    return WORKSHOP if low == WORKSHOP else KIT if low == KIT.lower() else name


def needs_version(v):
    """a declared minimum as kept ("0.7" -> "0.7.0"), or None when it is not 1 to 3 dot-separated numbers"""
    if not isinstance(v, str) or not NEEDS.match(v.strip()):
        return None
    parts = [str(int(p)) for p in v.strip().split(".")]
    return ".".join(parts + ["0"] * (3 - len(parts)))


def parse_requires(text):
    """the field's text -> {name: version}; the items that do not parse are left out. A name given twice keeps the
    higher version"""
    out = {}
    for item in str(text or "").split(","):
        words = item.split()
        if len(words) != 2 or not NAME.match(words[0]):
            continue
        version = needs_version(words[1])
        if version is None:
            continue
        name = canon(words[0])
        key = next((k for k in out if k.lower() == name.lower()), name)
        if key not in out or not met(version, out[key]):
            out[key] = version
    return out


def clean_needs(value):
    """a {name: version} read from elsewhere (a catalog entry, a kit row) as parse_requires keeps it; {} when none"""
    if not isinstance(value, dict):
        return {}
    text = ", ".join(f"{k} {v}" for k, v in value.items() if isinstance(k, str) and isinstance(v, str))
    return parse_requires(text)


def need_of(needs, name):
    """what needs says of name (folders case-insensitively), or None"""
    return next((v for k, v in (needs or {}).items() if k.lower() == name.lower()), None)


def has_tuple(v):
    """the leading numbers of a version that is had ("0.10.0.dev3" -> (0, 10, 0)), or None when it has none"""
    m = HAS.match(v) if isinstance(v, str) else None
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _padded(a, b):
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def met(needs, has):
    """whether `has` meets the minimum `needs`; a needs or a has that cannot be judged counts as met"""
    n = needs_version(needs) if isinstance(needs, str) else None
    h = has_tuple(has)
    if n is None or h is None:
        return True
    have, want = _padded(h, tuple(int(p) for p in n.split(".")))
    return have >= want


def compare(a, b):
    """-1, 0 or 1 as version a is older, the same or newer than b (by their leading numbers); None when either cannot
    be judged"""
    x, y = has_tuple(a), has_tuple(b)
    if x is None or y is None:
        return None
    x, y = _padded(x, y)
    return (x > y) - (x < y)


def newest(*versions):
    """the newest of the versions that can be judged, else the first that is not None"""
    judged = [v for v in versions if has_tuple(v) is not None]
    best = None
    for v in judged:
        if best is None or compare(v, best) > 0:
            best = v
    return best if best is not None else next((v for v in versions if v is not None), None)


def gate_has(*versions):
    """the version a gate judges by, of several that are had (the running and the installed WuxianKit): one that
    cannot be judged (a development build, "?", "") when any is given, so the gate does not judge (met); else the
    newest; None when none is given"""
    given = [v for v in versions if v is not None]
    odd = next((v for v in given if has_tuple(v) is None), None)
    return odd if odd is not None else newest(*given)


def needs_of(entry):
    """a catalog entry's needs: its `needs`, with its legacy min_app as needs.workshop when that says nothing of it"""
    needs = clean_needs((entry or {}).get("needs"))
    legacy = needs_version((entry or {}).get("min_app"))
    if legacy and WORKSHOP not in needs:
        needs[WORKSHOP] = legacy
    return needs


_tocs = {}                                 # .toc path -> ((mtime_ns, size), facts): read again when the file changed


def toc_facts(folder):
    """what an addon folder's .toc says for the judge: {version, needs (its X-Wuxian-Requires), deps (its ##
    Dependencies), title, floor (its X-WuxianKit-Floor, kept as needs_version keeps it; None), extension (it says ##
    X-WuxianKit-Extension)}; None when there is no folder or no .toc"""
    from .installer.addons import find_toc, read_toc
    folder = Path(folder)
    try:
        toc = find_toc(folder) if folder.is_dir() else None
        if toc is None:
            return None
        st = os.stat(toc)
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    kept = _tocs.get(str(toc))
    if kept is not None and kept[0] == stamp:
        return dict(kept[1], needs=dict(kept[1]["needs"]), deps=list(kept[1]["deps"]))
    try:
        heads = {k.lower(): v for k, v in (read_toc(toc) or {}).items()}
    except OSError:
        return None
    deps = heads.get("dependencies") or heads.get("requireddeps") or ""
    facts = dict(version=heads.get("version") or None, needs=parse_requires(heads.get(FIELD.lower())),
                 deps=[d.strip() for d in deps.split(",") if d.strip()], title=heads.get("title") or None,
                 floor=needs_version(heads.get(FLOOR.lower())),
                 extension=bool((heads.get("x-wuxiankit-extension") or "").strip()))
    if len(_tocs) > 512:
        _tocs.clear()
    _tocs[str(toc)] = (stamp, facts)
    return dict(facts, needs=dict(facts["needs"]), deps=list(facts["deps"]))


def oldest(versions):
    """the oldest of the versions that can be judged, or None"""
    best = None
    for v in versions:
        if has_tuple(v) is not None and (best is None or compare(v, best) < 0):
            best = v
    return best


def hinted(message, text):
    """an error's message with the judge's text at its end (nothing added without one)"""
    if not text:
        return message
    return f"{str(message).rstrip().rstrip('.')}. {text}"


def title_of(value):
    """a title as the catalog or a manifest writes it (a string, or {zh, en}): the text, or None"""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return next((v for v in (value.get("zh"), value.get("en"), *value.values()) if isinstance(v, str) and v), None)
    return None


# --- the judge -------------------------------------------------------------------------------------------------------

class _Judge:
    """the snapshot with the lookups the items and the chains share"""

    def __init__(self, snap):
        snap = snap or {}
        app = snap.get("app") or {}
        self.app, self.latest, self.update = app.get("version"), app.get("latest"), app.get("update")
        self.kit_needs = snap.get("kit_needs")
        self.folders = {k.lower(): (k, dict(v)) for k, v in (snap.get("folders") or {}).items()
                        if isinstance(k, str) and isinstance(v, dict)}
        self.catalog = [e for e in snap.get("catalog") or [] if isinstance(e, dict) and isinstance(e.get("folder"), str)]
        self.extensions = [f for f in snap.get("extensions") or [] if isinstance(f, str)]
        self.local = [x for x in snap.get("local") or [] if isinstance(x, dict) and isinstance(x.get("id"), str)]
        self.agents = snap.get("agents")
        self.ids = {}                       # folder (lower) -> the id of its item

    def folder(self, name):
        """(the folder's name as kept, its facts) or (name, None)"""
        name_, facts = self.folders.get(name.lower(), (name, None))
        return name_, facts

    def entry(self, folder):
        return next((e for e in self.catalog if e["folder"].lower() == folder.lower()), None)

    def has(self, name):
        """the version of name that a requirement is judged against: the App's for workshop; a folder's running one
        when known, else its installed one; None when it is not there (met: presence is the Dependencies' job)"""
        if name.lower() == WORKSHOP:
            return self.app
        _, f = self.folder(name)
        if not f:
            return None
        if f.get("running") is not None:
            return f["running"]
        return f.get("installed") if f.get("there", f.get("installed") is not None) else None

    def unmet(self, needs):
        """[{name, needs, has}] of needs that are not met now, the workshop's first (its fix comes before the rest)"""
        out = [dict(name=n, needs=v, has=self.has(n)) for n, v in (needs or {}).items() if not met(v, self.has(n))]
        return sorted(out, key=lambda u: u["name"].lower() != WORKSHOP)

    def floor(self):
        """the floor of the WuxianKit judged here (the snapshot's), or None: nothing is too old"""
        _, f = self.folder(KIT)
        return needs_version((f or {}).get("floor"))

    def too_old(self, built):
        """{built, floor} when an extension made for WuxianKit `built` (None: LEGACY) is too old for the floor, else
        None"""
        floor, built = self.floor(), built or LEGACY
        if floor is None or (compare(built, floor) or 0) >= 0:
            return None
        return dict(built=built, floor=floor)

    def is_extension(self, folder, facts):
        """an extension addon: its .toc says X-WuxianKit-Extension, or the game's WuxianKit reports it"""
        return bool((facts or {}).get("extension")) or folder.lower() in {e.lower() for e in self.extensions}

    def old_step(self, title, old, entry, iid):
        """the step of an extension too old for the floor: the catalog's later version of it (entry: one that clears the
        floor), its own needs first as an update's are; else none (its author must update it)"""
        o = dict(old, title=title, kit=self.has(KIT))
        if entry is None:
            return dict(action="none", target=None, links=[], old=o, first=("none", None))
        first = ("update", entry.get("id") or iid)
        further = self.unmet(needs_of(entry))
        if further:
            step = self.resolve(f"{entry['folder']} {entry.get('version')}", further[0])
            return dict(step, head=[self.old_text(o)], first=first)
        return dict(action="update", target=first[1], links=[], entry=entry, old=o, first=first)

    @staticmethod
    def old_text(o):
        kit = f"WuxianKit {o['kit']}" if o.get("kit") else "this WuxianKit"
        return f"{o['title']} was made for WuxianKit {o['built']}; {kit} runs extensions made for {o['floor']} or later"

    @staticmethod
    def shown(name):
        return APP_TITLE if name.lower() == WORKSHOP else name

    def resolve(self, subject, u, depth=1):
        """the fix of one unmet {name, needs} of subject: {action, target, links (the "X needs Y v" on the way), note
        (what a restart is for), first (action, target) of the first step}"""
        link = f"{subject} needs {self.shown(u['name'])} {u['needs']}"
        name = u["name"]
        if name.lower() == WORKSHOP:
            return dict(action="update_app", target="workshop", links=[link], needs=u["needs"],
                        first=("update_app", "workshop"))
        folder, f = self.folder(name)
        if f and f.get("running") is not None and has_tuple(f.get("installed")) is not None \
                and met(u["needs"], f["installed"]) and not met(u["needs"], f["running"]):
            target = self.ids.get(folder.lower(), folder)
            note = f"{folder} {f.get('installed')} is installed but the game still runs {f['running']}"
            return dict(action="restart_game", target=target, links=[link], note=note, first=("restart_game", target))
        entry = next((e for e in self.catalog if e["folder"].lower() == name.lower()
                      and has_tuple(e.get("version")) is not None and met(u["needs"], e["version"])), None)
        if entry is not None:
            first = ("update", entry.get("id"))
            further = self.unmet(needs_of(entry))
            if not further:
                return dict(action="update", target=entry.get("id"), links=[link], entry=entry, first=first)
            if depth >= DEPTH:
                return dict(action="none", target=None, links=[link], deep=True, first=first)
            step = self.resolve(f"{entry['folder']} {entry['version']}", further[0], depth + 1)
            return dict(step, links=[link] + step["links"], first=first)
        return dict(action="none", target=None, links=[link], missing=(self.shown(name), u["needs"]),
                    first=("none", None))

    def text(self, step, head=()):
        """a step as one English sentence: what is needed on the way, then what to do"""
        if step.get("old"):
            return f"{self.old_text(step['old'])}: update {step['old']['title']}"
        links = list(head) + list(step.get("links") or [])
        action = step["action"]
        if action == "update_app":
            tail = f"update {APP_TITLE}" + (" first" if len(links) > 1 else "")
            how = self.app_how(step.get("needs"))
            if how:
                tail += f"; {how}"
        elif action == "update":
            e = step.get("entry") or {}
            tail = f"update {e.get('folder')} to {e.get('version')} on the App's Extensions (扩展) page" if e \
                else "update it on the App's Extensions (扩展) page"
        elif action == "restart_game":
            tail = (step["note"] + ": " if step.get("note") else "") + (CLOSE if step.get("close") else RESTART)
        elif action == "restart_agent":
            tail = step.get("note") or "restart the agent"
        elif step.get("deep"):
            tail = "that chain of required versions is too long to follow: update them on the App's Extensions page"
        elif step.get("missing"):
            name, version = step["missing"]
            tail = f"no {name} {version} or newer is known: update {name} yourself"
        else:
            tail = "nothing to do"
        return "; ".join(links) + (": " if links else "") + tail

    def app_how(self, needs=None):
        """what the player clicks to update this App, by the updater's state (app.update); "" when it is not known.
        needs: the version needed (an unmet one): a newest known that is older does not help"""
        state, v = self.update, self.latest
        if state != "unsupported" and needs and v and not met(needs, v):
            return (f"no {APP_TITLE} {needs} is out yet (the newest is {v}): the player clicks 检查更新 (Check for "
                    f"updates) {WHERE} later")
        if state == "available" and v:
            return (f"the player clicks 下载 {v} (Download {v}) {WHERE}, then 重启并更新 (Restart and update) once it is "
                    f"downloaded")
        if state == "downloading":
            return (f"{APP_TITLE} {v or 'the new version'} is downloading: then the player clicks 重启并更新 (Restart and "
                    f"update) {WHERE}")
        if state == "ready":
            return (f"{APP_TITLE} {v or 'the new version'} is downloaded: the player clicks 重启并更新 (Restart and "
                    f"update) {WHERE}")
        if state == "unsupported":
            return f"this copy of {APP_TITLE} does not update itself: the player downloads the new version from {SITE}"
        if state in ("idle", "checking", "current", "error", "available"):
            return f"the player clicks 检查更新 (Check for updates) {WHERE}"
        return ""

    # the items

    def folder_item(self, kind, iid, folder, title):
        """the item of an addon folder (WuxianKit, a catalog addon, an extension addon WuxianKit reports), with the
        step that fixes it (None: nothing to do)"""
        name, f = self.folder(folder)
        f = f or {}
        entry = self.entry(folder)
        running, installed = f.get("running"), f.get("installed")
        latest = entry.get("version") if entry else None
        own = clean_needs(f.get("needs"))
        unmet = self.unmet(own)
        item = dict(id=iid, kind=kind, title=title or name, folder=name, running=running, installed=installed)
        if entry is not None:
            item["latest"] = latest
        step, needs = None, own
        old = self.too_old(need_of(own, KIT)) if kind == "addon" and not unmet and self.is_extension(name, f) else None
        if unmet:
            state = "blocked"
            step = self.resolve(name, unmet[0])
        elif old:
            state = "blocked"
            have = installed if installed is not None else running
            later = entry is not None and have is not None and (compare(latest, have) or 0) > 0
            clears = later and (compare(need_of(needs_of(entry), KIT) or LEGACY, old["floor"]) or 0) >= 0
            step = self.old_step(item["title"], old, entry if clears else None, iid)
        elif running is not None and installed is not None and compare(running, installed) not in (0, None):
            state = "pending"
            step = dict(action="restart_game", target=iid, links=[],
                        note=f"{name} {installed} is installed but the game still runs {running}",
                        first=("restart_game", iid))
        elif entry is not None and installed is not None and (compare(latest, installed) or 0) > 0:
            state, needs = "update", needs_of(entry)
            head = f"{name} {latest} is available ({installed} is installed)"
            further = self.unmet(needs)
            step = self.resolve(f"{name} {latest}", further[0]) if further else \
                dict(action="update", target=entry.get("id"), links=[], entry=entry)
            step = dict(step, head=[head], first=("update", entry.get("id")))
        elif entry is not None and installed is not None and (compare(latest, installed) or 0) < 0:
            state = "ahead"
        elif installed is not None or running is not None:
            state = "ok"
        else:
            state = "unknown"
        return self.finish(item, state, needs, unmet, step, old)

    def finish(self, item, state, needs, unmet, step, old=None):
        if needs:
            item["needs"] = dict(needs)
        if unmet:
            item["unmet"] = [dict(u) for u in unmet]
        if old:
            item["old"] = dict(old)
        item["state"] = state
        action, target = (step or {}).get("first") or ((step or {}).get("action", "none"), (step or {}).get("target"))
        item["action"] = action if step else "none"
        if step and target and target != item["id"]:
            item["via"] = target
        return item, step

    def workshop_item(self):
        item = dict(id="workshop", kind="workshop", title=APP_TITLE, running=self.app, latest=self.latest)
        needs = {KIT: self.kit_needs} if needs_version(self.kit_needs) else {}
        unmet = self.unmet(needs)
        step = None
        if unmet:
            state = "blocked"
            step = self.resolve(APP_TITLE, unmet[0])
        elif self.latest and (compare(self.latest, self.app) or 0) > 0:
            state = "update"
            step = dict(action="update_app", target="workshop", links=[],
                        head=[f"{APP_TITLE} {self.latest} is available (this is {self.app})"],
                        first=("update_app", "workshop"))
        else:
            state = "ok" if self.app else "unknown"
        return self.finish(item, state, {}, unmet, step)

    def bridge_item(self):
        """WoWBridge: what the game runs (its HELLO) against the newer of the .toc on disk and the App's own version
        (the App always brings its WoWBridge along and puts it in place once the game is closed: sync_addons), so an
        App updated while the game runs is a restart too. Only an older one running counts. The App's version is never
        called installed: when it is the newer one (or the .toc cannot be read), the step says to close the game so the
        App puts its WoWBridge in place"""
        name, f = self.folder(BRIDGE)
        if not f or (not f.get("there") and f.get("installed") is None and f.get("running") is None):
            return None
        running, installed = f.get("running"), f.get("installed")
        item = dict(id="wowbridge", kind="bridge", title=BRIDGE, folder=name, running=running, installed=installed)
        app = ".".join(map(str, has_tuple(self.app))) if has_tuple(self.app) is not None else None
        target = newest(installed, app) if has_tuple(installed) is not None else app
        c = compare(running, target) if running is not None and target is not None else None
        step = None
        if c is not None and c < 0:
            state = "pending"
            if target == installed and (compare(installed, app) is None or compare(installed, app) >= 0):
                note, close = f"{BRIDGE} {installed} is installed but the game still runs {running}", False
            else:
                note, close = f"{APP_TITLE} {app} brings {BRIDGE} {app} but the game still runs {running}", True
            step = dict(action="restart_game", target="wowbridge", links=[], note=note, close=close,
                        first=("restart_game", "wowbridge"))
        else:
            state = "ok" if running is not None or installed is not None else "unknown"
        return self.finish(item, state, {}, [], step)

    def local_item(self, x, iid):
        """a local extension: its manifest's requires (kit as the folder WuxianKit) and its addons' X-Wuxian-Requires
        together; pending while one of its addons runs another version than the one installed. Blocked is what it shows,
        never a refusal: a local extension still starts, loads and installs whatever it needs of WuxianKit or the floor
        says (the module's docstring)"""
        req = x.get("requires") if isinstance(x.get("requires"), dict) else {}
        needs = clean_needs({WORKSHOP: req.get("workshop"), KIT: req.get("kit")})
        built = need_of(needs, KIT)                    # what it was made for: the oldest of requires.kit and its
                                                       # addons' declarations (the game judges each addon's .toc)
        declared = []
        restart = None
        for folder in x.get("addons") or []:
            if not isinstance(folder, str):
                continue
            name, f = self.folder(folder)
            declared.append(need_of(clean_needs((f or {}).get("needs")), KIT))
            for n, v in clean_needs((f or {}).get("needs")).items():
                key = next((k for k in needs if k.lower() == n.lower()), n)
                if key not in needs or not met(v, needs[key]):
                    needs[key] = v
            if f and restart is None and f.get("running") is not None and f.get("installed") is not None \
                    and compare(f["running"], f["installed"]) not in (0, None):
                restart = f"{name} {f['installed']} is installed but the game still runs {f['running']}"
        unmet = self.unmet(needs)
        item = dict(id=iid, kind="local", title=title_of(x.get("title")) or x["id"], installed=x.get("version"))
        game_half = bool(x.get("addons")) or bool(x.get("drives"))   # the floor is only for one with a game half
        old = None if unmet or not game_half else self.too_old(oldest([built] + declared))
        step = None
        if unmet:
            state = "blocked"
            step = self.resolve(item["title"], unmet[0])
        elif old:
            state = "blocked"
            step = self.old_step(item["title"], old, None, iid)
        elif restart:
            state = "pending"
            step = dict(action="restart_game", target=iid, links=[], note=restart, first=("restart_game", iid))
        else:
            state = "ok"
        return self.finish(item, state, needs, unmet, step, old)

    def agent_item(self):
        a = self.agents if isinstance(self.agents, dict) else {}
        item = dict(id="mcp", kind="agent", title="Agent 连接")
        step = None
        if a.get("stale"):
            state = "pending"
            step = dict(action="restart_agent", target="mcp", links=[], first=("restart_agent", "mcp"),
                        note=("an agent's 无限工坊 (wuxian) MCP server runs older code than the App: reconnect it "
                              "(Claude Code: /mcp, choose wuxian, Reconnect) or start a new session of the agent"))
        else:
            state = "ok" if a.get("seen") else "unknown"
        return self.finish(item, state, {}, [], step)

    def items(self):
        """[(item, step)] in the report's order"""
        out = [self.workshop_item()]
        bridge = self.bridge_item()
        if bridge:
            out.append(bridge)
        taken = {"workshop", "wowbridge", "mcp"}
        kit_entry = self.entry(KIT)
        name, f = self.folder(KIT)
        kit_id = kit_entry.get("id") if kit_entry and isinstance(kit_entry.get("id"), str) else "wuxiankit"
        self.ids[KIT.lower()] = kit_id
        rows = []
        for e in self.catalog:
            if e["folder"].lower() != KIT.lower() and isinstance(e.get("id"), str):
                self.ids.setdefault(e["folder"].lower(), e["id"])
        local_folders = {a.lower() for x in self.local for a in x.get("addons") or [] if isinstance(a, str)}
        if f and (f.get("there") or f.get("installed") is not None or f.get("running") is not None):
            rows.append(("kit", kit_id, KIT, title_of((kit_entry or {}).get("title")) or "扩展框架"))
        for e in self.catalog:
            folder = e["folder"]
            if folder.lower() == KIT.lower() or folder.lower() in local_folders:
                continue
            _, ff = self.folder(folder)
            if ff and (ff.get("there") or ff.get("installed") is not None or ff.get("running") is not None):
                rows.append(("addon", e.get("id") or folder, folder, title_of(e.get("title")) or (ff or {}).get("title")))
        listed = {e["folder"].lower() for e in self.catalog}
        for folder in self.extensions:
            low = folder.lower()
            if low in listed or low in local_folders or low in (KIT.lower(), BRIDGE.lower()):
                continue
            listed.add(low)
            _, ff = self.folder(folder)
            iid = folder if folder not in taken and folder not in self.ids.values() else f"addon:{folder}"
            self.ids.setdefault(low, iid)
            rows.append(("addon", iid, folder, (ff or {}).get("title")))
        for kind, iid, folder, title in rows:
            taken.add(iid)
            out.append(self.folder_item(kind, iid, folder, title))
        for x in self.local:
            iid = x["id"] if x["id"] not in taken else f"local:{x['id']}"
            taken.add(iid)
            out.append(self.local_item(x, iid))
        if self.agents is not None:
            out.append(self.agent_item())
        return out


def _next(judge, pairs):
    best = None
    for item, step in pairs:
        rank = SEVERITY.get(item["state"], 0)
        if step is not None and rank and (best is None or rank > SEVERITY[best[0]["state"]]):
            best = (item, step)
    if best is None:
        return None
    item, step = best
    out = dict(severity=item["state"], action=step["action"], id=item["id"], target=step.get("target"),
               text=judge.text(step, step.get("head") or ()))
    if step["action"] == "update" and step.get("target") and step["target"] != item["id"]:
        out["via"] = step["target"]                  # the fix is that catalog entry's update
    return out


def judge(snap, focus=None):
    """the report status.versions is: {next, items} (the module's docstring). focus: the id of one item, whose step
    alone next is (None when that item is fine or not there)"""
    j = _Judge(snap)
    pairs = j.items()
    for item, step in pairs:                         # every item that is not fine says its own sentence
        if step is not None and item["state"] in SEVERITY:
            item["text"] = j.text(step, step.get("head") or ())
    chosen = [p for p in pairs if p[0]["id"] == focus] if focus is not None else pairs
    out = dict(next=_next(j, chosen), items=[item for item, _ in pairs])
    if out["next"] is not None and any(i["kind"] == "agent" and i["id"] == out["next"]["id"] for i, _ in pairs):
        # the next of an agent that is not itself stale (brief): another agent's MCP server is not its business
        out["agent_next"] = _next(j, [p for p in chosen if p[0]["kind"] != "agent"])
    return out


def fix_text(snap, subject, unmet):
    """the judge's sentence for one unmet {name, needs} of subject (an error's end), e.g. "fx needs 无限工坊 0.11.0:
    update 无限工坊" """
    j = _Judge(snap)
    j.items()
    return j.text(j.resolve(subject, unmet))


def gate_text(title, row, kit=None, addon=None):
    """(local/host.py) the sentence for an extension's game addon that WuxianKit did not load for its version, made of
    WuxianKit's row of it (row: {gate or state: outdated / too_old, unmet: [{name, needs, has}], built, floor}) when the
    version report has none: what it needs and what to update, or what it was made for and the floor. kit: the
    WuxianKit known (None: not known); addon: its folder"""
    row = row if isinstance(row, dict) else {}
    gate = row.get("gate") or row.get("state")
    unmet = [u for u in row.get("unmet") or [] if isinstance(u, dict) and isinstance(u.get("name"), str)
             and isinstance(u.get("needs"), str)]
    if gate == "outdated" and unmet:
        said = "; ".join(f"{addon or title} needs {_Judge.shown(u['name'])} {u['needs']}"
                         + (f" (the game has {u['has']})" if isinstance(u.get("has"), str) and u["has"] else "")
                         for u in unmet)
        name = unmet[0]["name"]
        if name.lower() == WORKSHOP:
            todo = f"update {APP_TITLE}"
        elif name.lower() == KIT.lower():
            todo = "update WuxianKit on the App's Extensions (扩展) page, then restart the game fully"
        else:
            todo = f"update {name}, then restart the game fully"
        return f"{said}: {todo}"
    floor = needs_version(row.get("floor")) if isinstance(row.get("floor"), str) else None
    if gate == "too_old" and floor:
        built = row.get("built") if isinstance(row.get("built"), str) and row.get("built") else LEGACY
        return f"{_Judge.old_text(dict(title=title, built=built, floor=floor, kit=kit))}: update {title}"
    return f"WuxianKit did not load {addon or title} for its version: update {title}"


def install_gate(snap, entry):
    """whether the catalog entry may be installed now: None, or {code, text, unmet}. code needs_app: it needs a newer
    无限工坊; needs_kit: only WuxianKit is too old, judged by the newer of the running and the installed one (the files
    of an update in place count: the game loads both at its next start; gate_has: a development build of either is
    not judged, so it is met). Other folders it needs are not judged here: WuxianKit shows such an addon outdated, and
    the report says what to do. Catalog entries only: a local extension is never refused for what it needs of
    WuxianKit or for the floor (the module's docstring)"""
    j = _Judge(snap)
    j.items()
    needs = needs_of(entry)
    subject = f"{entry.get('folder')} {entry.get('version')}"
    want = need_of(needs, WORKSHOP)
    if want and not met(want, j.app):
        u = dict(name=WORKSHOP, needs=want, has=j.app)
        return dict(code="needs_app", unmet=[u], text=j.text(j.resolve(subject, u)))
    want = need_of(needs, KIT)
    if want:
        _, f = j.folder(KIT)
        has = gate_has((f or {}).get("running"), (f or {}).get("installed")) if f else None
        if not met(want, has):
            u = dict(name=KIT, needs=want, has=has)
            return dict(code="needs_kit", unmet=[u], text=j.text(j.resolve(subject, u)))
    return None


def kit_state(entry, kit):
    """(extensions.state_of) "needs_kit" when the entry needs a newer WuxianKit than `kit` (None: not judged)"""
    want = need_of(needs_of(entry), KIT)
    return "needs_kit" if want and kit is not None and not met(want, kit) else None


def agent_stale(versions, note=None):
    """the report with the agent's item pending (an MCP server on stdio that knows itself stale), next set to it when
    nothing more severe is there"""
    if not isinstance(versions, dict) or not isinstance(versions.get("items"), list):
        return versions
    text = note or ("this agent's 无限工坊 (wuxian) MCP server runs old code: reconnect it (Claude Code: /mcp, choose "
                    "wuxian, Reconnect) or start a new session of the agent")
    items = [dict(i) for i in versions["items"] if isinstance(i, dict)]
    mine = next((i for i in items if i.get("id") == "mcp"), None)
    if mine is None:
        mine = dict(id="mcp", kind="agent", title="Agent 连接")
        items.append(mine)
    mine.update(state="pending", action="restart_agent", text=text)
    nxt = versions.get("next")
    if not isinstance(nxt, dict) or nxt.get("id") == "mcp" or SEVERITY.get(nxt.get("severity"), 0) < SEVERITY["pending"]:
        nxt = dict(severity="pending", action="restart_agent", id="mcp", target="mcp", text=text)
    return dict(versions, items=items, next=nxt)


def brief(versions, mine=False):
    """the report as an agent gets it: next, and the items that are not ok / ahead / unknown. The agent items (the MCP
    servers of the agents) only when they concern this very agent (mine: its own server is stale, agent_stale): the
    staleness of another agent's server is the App page's, not this agent's to tell; next is then the report's
    agent_next (judge: the next without the agent items)"""
    if not isinstance(versions, dict):
        return versions
    items = [i for i in versions.get("items") or [] if isinstance(i, dict) and i.get("state") in SEVERITY
             and (mine or i.get("kind") != "agent")]
    nxt = versions.get("next")
    agents = {i.get("id") for i in versions.get("items") or [] if isinstance(i, dict) and i.get("kind") == "agent"}
    if not mine and isinstance(nxt, dict) and nxt.get("id") in agents:
        nxt = versions.get("agent_next")
    return dict(next=nxt, items=items)
