"""The Blizzard UI index: the "ui" content pack (content.py) read for the MCP tools ui_search / ui_get and the check
(agent/lint.py). The pack (scripts\\build_ui_pack.py, from the client's own interface files) holds an index of Blizzard's
Lua and XML only: names, kinds, where they are defined (file, line), function parameter names, a table's member names,
an XML frame's structure, which Blizzard addon loads it and when. No code, no comments, no text of Blizzard's.

    pack = {pack: "ui", version, client, built, addons: {name: {load: always | on_demand, files}}, files: [path],
            entries: [{k: kind, n: name, f: file index, l: line, ...}],
            left_out: {addon: why this client does not load it}, bootstrap: [file index]}
    (left_out: Blizzard addons of other game types or the login screen, Blizzard_EncounterJournal among them: their
    names are not in the index; bootstrap: the files of an on_demand addon the client loads at login: "always")
    kinds: function (p: params), table (m: members, mixins), method (n: "Foo:x" / "Foo.x", p, o: owner), mixin (as a
           table), frame and template (t: widget type, inh: inherits, mix: mixins, keys: {parentKey: type}, parent),
           event (one per file that registers it)

    index().search("PlayerFrame", kind=None, limit=20) -> [brief]      ranked: exact, prefix, substring, all words
    index().get("TooltipDataProcessor") -> the entry in full (other definitions in `also`), {candidates} for a member
                                           name, None when the index has nothing by that name
    index().defines("PlayerFrame") -> the global's definition (function, table, mixin, frame) or None
    index().about() -> {version, client, built, counts, addons, files, left_out}
    index().not_loaded("EncounterJournal") -> [{addon, why}]   the left-out addons whose name the query names

A Blizzard addon loaded on demand (load "on_demand") defines its names only after C_AddOns.LoadAddOn("<addon>") or
when the game loads it (opening its panel); the results say so. Without the pack (an older program's data) `missing`
is true and every tool answers that the index is not installed.
"""
import re

from . import content

KINDS = ("function", "table", "mixin", "method", "frame", "template", "event")
GLOBALS = ("function", "table", "mixin", "frame")          # the kinds that make a global name
NOT_INSTALLED = ("not_installed: the Blizzard UI index is not installed in this 无限工坊; the player updates 无限工坊 "
                 "(its content packs come with it)")


def load_note(addon, load):
    """what an agent has to know about when the name exists"""
    if load == "on_demand":
        return (f"on_demand: exists only after C_AddOns.LoadAddOn(\"{addon}\") or when the game loads it (e.g. the "
                f"player opens its panel); test C_AddOns.IsAddOnLoaded(\"{addon}\") first")
    return None


class UiIndex:
    def __init__(self, pack):
        self.pack = pack or {}
        self.missing = not pack
        self.files = list(self.pack.get("files") or [])
        self.addons = {str(k): dict(v) for k, v in (self.pack.get("addons") or {}).items() if isinstance(v, dict)}
        self.left_out = {str(k): str(v) for k, v in (self.pack.get("left_out") or {}).items()}
        self.bootstrap = {f for f in self.pack.get("bootstrap") or [] if isinstance(f, int)}
        addon_low = {k.lower(): k for k in self.addons}
        self.file_addon = []                    # file index -> addon name (as the client spells it) or None
        for path in self.files:
            parts = str(path).replace("\\", "/").split("/")
            name = None
            if len(parts) > 3 and parts[0].lower() == "interface" and parts[1].lower() == "addons":
                name = addon_low.get(parts[2].lower(), parts[2])
            elif len(parts) > 2 and parts[0].lower() == "interface":
                name = addon_low.get(parts[1].lower(), parts[1])
            self.file_addon.append(name)
        self.entries = []
        self.by_name, self.by_member, self.methods = {}, {}, {}
        for raw in self.pack.get("entries") or []:
            if not isinstance(raw, dict) or not raw.get("n") or raw.get("k") not in KINDS:
                continue
            e = dict(raw, low=str(raw["n"]).lower())
            self.entries.append(e)
            self.by_name.setdefault(e["low"], []).append(e)
            if e["k"] == "method":
                short = re.split(r"[.:]", e["n"])[-1].lower()
                self.by_member.setdefault(short, []).append(e)
                if e.get("o"):
                    self.methods.setdefault(e["o"], []).append(e)
            elif e["k"] in ("table", "mixin"):
                for m in e.get("m") or []:
                    self.by_member.setdefault(str(m).rstrip("()").lower(), []).append(e)

    # --- what the pack is ------------------------------------------------------------------------------------------

    def about(self):
        p = self.pack
        return dict(version=p.get("version"), client=p.get("client"), built=p.get("built"),
                    counts={k: sum(1 for e in self.entries if e["k"] == k) for k in KINDS},
                    addons=len(self.addons), files=len(self.files), left_out=len(self.left_out))

    def not_loaded(self, query, strict=False):
        """[{addon, why}]: the Blizzard addons this client does not load (left_out) that the query names: its first
        part holds the addon's name without "Blizzard_" (EncounterJournal_OpenJournal: Blizzard_EncounterJournal), or
        (not strict) is a part of that name of 5 letters or more. The pack has no names of those addons: this is a
        guess by the name, for a hint"""
        head = re.split(r"[\s.:]+", str(query or "").strip().lower())[0]
        if len(head) < 4:
            return []
        rows = []
        for addon, why in sorted(self.left_out.items()):
            short = addon.lower()
            short = short[len("blizzard_"):] if short.startswith("blizzard_") else short
            if (len(short) >= (6 if strict else 4) and short in head.replace("_", "")) or short == head or \
                    (not strict and len(head) >= 5 and head in short):
                rows.append(dict(addon=addon, why=why))
        return rows[:10]

    def not_loaded_text(self, query, strict=False):
        """"<addon> is not loaded by this client (<why>)" for each left-out addon the query names, or "" """
        return "; ".join(f"{r['addon']} is not loaded by this client ({r['why']})"
                         for r in self.not_loaded(query, strict))

    # --- one entry -------------------------------------------------------------------------------------------------

    def where(self, e):
        f = e.get("f")
        path = self.files[f] if isinstance(f, int) and 0 <= f < len(self.files) else "?"
        return f"{path}:{e.get('l') or 0}"

    def addon_of(self, e):
        """(addon, load) of the file the entry is in"""
        f = e.get("f")
        addon = self.file_addon[f] if isinstance(f, int) and 0 <= f < len(self.file_addon) else None
        info = self.addons.get(addon) or {}
        if f in self.bootstrap:                   # loaded at login although its addon loads on demand
            return addon, "always", info
        return addon, info.get("load") or "always", info

    def brief(self, e):
        addon, load, info = self.addon_of(e)
        out = dict(kind=e["k"], name=e["n"], where=self.where(e), addon=addon, load=load)
        if info.get("uncertain"):
            out["uncertain"] = True
        note = load_note(addon, load)
        if note:
            out["note"] = note
        k = e["k"]
        if k in ("function", "method"):
            out["sig"] = f"{e['n']}({', '.join(str(p) for p in e.get('p') or [])})"
            if e.get("o"):
                out["owner"] = e["o"]
        elif k in ("table", "mixin"):
            out["members"] = len(e.get("m") or [])
            if e.get("mixins"):
                out["mixins"] = list(e["mixins"])
        elif k in ("frame", "template"):
            if e.get("t"):
                out["type"] = e["t"]
            if e.get("inh"):
                out["inherits"] = list(e["inh"])
            if e.get("mix"):
                out["mixins"] = list(e["mix"])
            out["keys"] = len(e.get("keys") or {})
        return out

    def full(self, e):
        out = self.brief(e)
        k = e["k"]
        if k in ("function", "method"):
            out["params"] = list(e.get("p") or [])
        elif k in ("table", "mixin"):
            out["members"] = list(e.get("m") or [])
            methods = self.methods.get(e["n"])           # its methods' parameters, where the index has them
            if methods:
                out["methods"] = {x["n"]: list(x.get("p") or []) for x in methods}
        elif k in ("frame", "template"):
            out["keys"] = dict(e.get("keys") or {})
            out["parent"] = e.get("parent")
        if k == "event":
            out["registered_in"] = [self.where(x) for x in self.by_name.get(e["low"], []) if x["k"] == "event"]
        return out

    # --- search and lookup -----------------------------------------------------------------------------------------

    def search(self, query, kind=None, limit=20):
        """the entries whose name holds every word of the query (dots and colons are word breaks too), best first:
        exact, then the name's start, then a part of it; a global before a method, a template or an event"""
        q = " ".join(str(query or "").lower().split())
        if not q:
            return []
        words = q.split()
        parts = [w for w in re.split(r"[\s.:]+", q) if w]
        scored = []
        for e in self.entries:
            if kind and e["k"] != kind:
                continue
            low = e["low"]
            if low == q:
                s = 100
            elif low.startswith(q):
                s = 70
            elif q in low:
                s = 40
            elif all(w in low for w in words) or (parts and all(p in low for p in parts)):
                s = 30
            else:
                continue
            scored.append((-s, e["k"] not in GLOBALS, len(low), low, e))   # globals first, then the shorter
        scored.sort(key=lambda x: x[:4])
        seen, results = set(), []
        limit = max(1, min(int(limit or 20), 100))
        for *_, e in scored:
            key = (e["k"], e["n"]) if e["k"] == "event" else id(e)     # an event once, however many files register it
            if key in seen:
                continue
            seen.add(key)
            results.append(self.brief(e))
            if len(results) >= limit:
                break
        return results

    def defines(self, name):
        """the definition of a global by that exact name (a function, a table, a mixin, a named frame), the one loaded
        always before one loaded on demand; None"""
        found = [e for e in self.by_name.get(str(name).lower(), ()) if e["n"] == name and e["k"] in GLOBALS]
        if not found:
            return None
        found.sort(key=lambda e: (self.addon_of(e)[1] != "always", GLOBALS.index(e["k"])))
        return found[0]

    def get(self, name):
        """one entry in full: by its exact name ("Foo", "Foo:x", "Foo.x" for a method; a frame's child by its parentKeys,
        "PlayerFrame.PlayerFrameContainer.PlayerPortrait"); a member name alone gives the candidates; None"""
        raw = str(name or "").strip()
        low = raw.lower()
        found = self.by_name.get(low) or []
        if not found and ":" in raw:                                 # Foo:x written for Foo.x and the other way round
            found = self.by_name.get(low.replace(":", ".")) or []
        if not found and "." in raw:
            found = self.by_name.get(low[::-1].replace(".", ":", 1)[::-1]) or []
        if found:
            exact = [e for e in found if e["n"] == raw] or found
            exact.sort(key=lambda e: (e["k"] == "event", self.addon_of(e)[1] != "always", KINDS.index(e["k"])))
            first = exact[0]
            out = self.full(first)
            others = [e for e in exact[1:] if not (first["k"] == "event" and e["k"] == "event")]
            if others:
                out["also"] = [self.brief(e) for e in others[:10]]
            return out
        child = self._child(raw)
        if child:
            return child
        members = self.by_member.get(low.rstrip("()")) if not re.search(r"[.:]", raw) else None
        if members:
            rows = []
            for e in members[:20]:
                b = self.brief(e)
                if e["k"] != "method":
                    b["member"] = f"{e['n']}.{raw}"
                rows.append(b)
            return dict(candidates=rows, note=f"{raw} is no global of the Blizzard UI; these define a member by that "
                                              f"name: ui_get one of them by its full name")
        return None

    def _child(self, raw):
        """"Frame.key.key": a named frame's (or template's) child by its parentKeys"""
        head, _, rest = raw.partition(".")
        if not rest:
            return None
        for e in self.by_name.get(head.lower(), ()):
            if e["k"] not in ("frame", "template"):
                continue
            keys = e.get("keys") or {}
            hit = next((k for k in keys if k.lower() == rest.lower()), None)
            if hit is not None:
                out = self.full(e)
                return dict(child=dict(name=f"{e['n']}.{hit}", type=keys[hit],
                                       keys={k[len(hit) + 1:]: t for k, t in keys.items() if k.startswith(hit + ".")}),
                            of=out, note=f"{e['n']}.{hit} is a child of {e['n']} (its parentKey), a {keys[hit]}")
        return None


_cached = (None, None)


def index():
    """the index of the pack in use (rebuilt when a newer pack was downloaded)"""
    global _cached
    pack = content.PACKS.get("ui")
    if _cached[0] is not pack or _cached[1] is None:
        _cached = (pack, UiIndex(pack))
    return _cached[1]
