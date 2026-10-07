"""The API manual: the "api" content pack (content.py) read for the MCP tools api_search / api_get / api_manual,
`wuxian api` and the API 手册 page. The pack holds this client's own API documentation (Blizzard_APIDocumentationGenerated,
extracted from the client's files: namespaces with their functions, events and tables), the exe's `Usage:` strings, what
the last client builds changed in it, and the manual 无限图鉴 wrote (runtime, taint, secret values, the .toc, load switches,
protected functions, GameRules, the 无限-only API; in Chinese and English); scripts\\build_api_pack.py builds it from
无限图鉴's data (not in this repository).

    index().search("UnitHealth") -> [{kind, name, ns, sig, flags, call, why, since, doc}]   ranked: exact, prefix, substring, text
    index().find("spell", call="ok") -> {results, counts: {ok, limited, protected}, total}    the API 手册 page's search
    index().get("C_Spell.GetSpellInfo") -> the entry in full, with the tables its types name, its namespace's functions and
                                           the exe's Usage strings for it (or {"candidates": [...]} for an ambiguous short name)
    index().manual() -> [{id, title}];  index().manual("taint", lang="en") -> {id, title, md}
    index().systems() -> [{key, title, group, counts, calls}]      the API 手册 page's browser: namespaces (C_…), the global
                                                                   function groups (Unit, Build …), the objects (Frame …)
    index().system("C_Spell") -> {key, title, group, counts, calls, functions, events, tables}

An object's methods (the documentation's ScriptObject systems, SimpleFrameAPI, FrameAPICooldown …) are named Object:Method
(Frame:Hide) and carry obj: they are called on an object, not as globals.

What an addon may do with an entry (`call`, with `why`: the documentation fields that say so):
    ok         it may call / register / use it
    limited    it may, with a catch: usage restrictions (HasRestrictions), secret values in restricted states (SecretReturns,
               SecretWhen..., SecretIn...), a precondition (Requires...: the clubs, the friend list, an active commentator ...);
               an event delivered through callbacks only (CallbackEvent) or with secret payloads
    protected  reserved to Blizzard's secure code: a protected function (IsProtectedFunction, or one of the protected globals
               the manual lists: an addon's call is blocked, ADDON_ACTION_BLOCKED) or a restricted event (HasRestrictions:
               registering it is forbidden, ADDON_ACTION_FORBIDDEN)
An object's method marked IsProtectedFunction (Frame:Hide, Button:Enable …) is limited (why ProtectedMethod): an addon calls it
on its own frames at any time; on a protected (secure) frame it is blocked in combat.
"""
import re

from . import content
from .i18n import tr

KINDS =("function", "event", "table")
CALLS = ("ok", "limited", "protected")
SECRET_TAGS = {"AllowedWhenTainted": ("插件可以传机密值", "an addon may pass secret values"),
               "NotAllowed": ("不接受机密值", "takes no secret values")}


def param(p):
    """name?: type = default (as Lua writes it)"""
    text = f"{p.get('n')}{'?' if p.get('nil') else ''}: {p.get('t')}"
    d = p.get("def")
    if d is None or d == "":
        return text
    return text + " = " + (str(d).lower() if isinstance(d, bool) else str(d))


def function_flags(raw, method=False):
    flags = []
    if raw.get("IsProtectedFunction") and method:
        flags.append(tr("受保护的方法（插件自己的框体随时可用；受保护的安全框体在战斗中不可用）",
                        "protected method (fine on the addon's own frames; not on secure frames in combat)"))
    elif raw.get("IsProtectedFunction"):
        flags.append(tr("受保护（只有安全代码能调用，战斗中对安全框体不可用）",
                        "protected (secure code only; not on secure frames in combat)"))
    if raw.get("SecretArguments") in SECRET_TAGS:
        flags.append(tr(*SECRET_TAGS[raw["SecretArguments"]]))
    secret = sorted(k for k, v in raw.items() if v and (k == "SecretReturns" or k.startswith(("SecretWhen", "SecretIn"))))
    if secret:
        flags.append(tr("可能返回机密值：" + "、".join(secret), "may return secret values: " + ", ".join(secret)))
    if raw.get("HasRestrictions"):
        flags.append(tr("有使用限制", "usage restrictions"))
    return flags


def event_flags(raw):
    flags = []
    if raw.get("HasRestrictions"):
        flags.append(tr("受限：插件注册它会被客户端拦截（ADDON_ACTION_FORBIDDEN）",
                        "restricted: the client blocks an addon that registers it (ADDON_ACTION_FORBIDDEN)"))
    if raw.get("CallbackEvent"):
        flags.append(tr("回调事件（CallbackEvent）：由客户端的回调分发，插件不一定能用 RegisterEvent 收到",
                        "callback event (CallbackEvent): delivered through the client's callbacks, RegisterEvent may not get it"))
    if raw.get("SecretPayloads"):
        flags.append(tr("载荷可能是机密值", "its payload may be secret values"))
    if raw.get("UniqueEvent"):
        flags.append(tr("同一帧只触发一次", "fires at most once a frame"))
    if raw.get("SynchronousEvent"):
        flags.append(tr("同步事件", "synchronous event"))
    return flags


def _secret_or_needs(k):
    return k.startswith(("SecretReturns", "SecretWhen", "SecretIn", "Requires"))


def object_of(system):
    """the object a ScriptObject system's functions are methods of: SimpleFrameAPI -> Frame, FrameAPICooldown -> Cooldown,
    DurationTextBindingObjectAPI -> DurationTextBindingObject; None for a global system (Unit, Build …)"""
    if not system or not (system.endswith("API") or system.startswith("FrameAPI")):
        return None
    name = system[len("FrameAPI"):] if system.startswith("FrameAPI") else system
    name = name[len("Simple"):] if name.startswith("Simple") else name
    return (name[:-3] if name.endswith("API") else name) or system


def callability(kind, raw, protected_global=False, method=False):
    """(call, why): how far an addon may use the entry (CALLS) and the documentation fields that say so"""
    raw = raw or {}
    if kind == "function":
        if method and raw.get("IsProtectedFunction"):  # fine on the addon's own frames; on secure ones blocked in combat
            return "limited", ["ProtectedMethod"] + sorted(k for k, v in raw.items() if v and (k == "HasRestrictions" or _secret_or_needs(k)))
        if raw.get("IsProtectedFunction") or protected_global:
            return "protected", ["IsProtectedFunction" if raw.get("IsProtectedFunction") else "ProtectedGlobal"]
        why = sorted(k for k, v in raw.items() if v and (k == "HasRestrictions" or _secret_or_needs(k)))
        return ("limited" if why else "ok"), why
    if kind == "event":
        if raw.get("HasRestrictions"):
            return "protected", ["HasRestrictions"]
        why = sorted(k for k, v in raw.items() if v and (k in ("CallbackEvent", "SecretPayloads", "RequireNPERestricted") or _secret_or_needs(k)))
        return ("limited" if why else "ok"), why
    return "ok", []


class ApiIndex:
    def __init__(self, pack):
        self.pack = pack or {}
        self.entries = []                       # dicts: kind, name, short, ns, item
        self.by_name, self.by_short, self.tables = {}, {}, {}
        self.protected_globals = set(self.pack.get("protected_globals") or [])
        self.groups = {}                        # the browser's rows: key -> {key, title, group, entries}
        for n in self.pack.get("namespaces", []):
            ns = n.get("ns") or ""
            system = n.get("name") or ""
            obj = None if ns else object_of(system)
            if ns:                              # a namespace: its systems together (C_PartyInfo has two)
                gkey, group = ns, "namespace"
            elif obj:
                gkey, group = obj, "object"
            elif system and (n.get("functions") or n.get("events")):
                gkey, group = system, "global"
            else:                               # a file of types only: its tables are found by search and the kind filter
                gkey, group = None, None
            if gkey:
                row = self.groups.setdefault(gkey, dict(key=gkey, title=gkey, group=group, entries=[]))
            for kind, key in (("function", "functions"), ("event", "events"), ("table", "tables")):
                for item in n.get(key, []):
                    short = item["name"]
                    method = kind == "function" and obj is not None
                    full = f"{ns}.{short}" if kind == "function" and ns else f"{obj}:{short}" if method else short
                    call, why = callability(kind, item.get("raw"), kind == "function" and not ns and not method
                                            and short in self.protected_globals, method)
                    e = dict(kind=kind, name=full, short=short, ns=ns, item=item, low=full.lower(),
                             text=(item.get("doc") or "").lower(), call=call, why=why, obj=obj if method else None, group=gkey)
                    self.entries.append(e)
                    if gkey:
                        row["entries"].append(e)
                    self.by_name.setdefault(full.lower(), []).append(e)
                    if kind == "function":
                        self.by_short.setdefault(short.lower(), []).append(e)
                    elif kind == "table":
                        self.tables.setdefault(short.lower(), item)
        # what the newest client build in the pack changed: {name: {build, what: added | changed, note}}
        self.since = {}
        changes = self.pack.get("changes") or []
        if changes:
            c = changes[0]
            for what, key in (("added", "added"), ("changed", "changed")):
                for x in c.get(key, []):
                    self.since[x["name"]] = dict(build=c.get("to"), what=what, note=x.get("note") or "",
                                                 note_en=x.get("note_en") or x.get("note") or "")

    # --- what the pack is ------------------------------------------------------------------------------------------

    def about(self):
        p = self.pack
        counts = {k: sum(1 for e in self.entries if e["kind"] == k) for k in KINDS}
        calls = {c: sum(1 for e in self.entries if e["kind"] != "table" and e["call"] == c) for c in CALLS}
        changes = p.get("changes") or []
        latest = changes[0] if changes else None
        return dict(version=p.get("version"), client=p.get("client"), interface=p.get("interface"), built=p.get("built"),
                    source=p.get("source"), counts=counts, calls=calls,
                    changes=dict(frm=latest.get("from"), to=latest.get("to"), added=len(latest.get("added", [])),
                                 changed=len(latest.get("changed", [])), removed=len(latest.get("removed", []))) if latest else None,
                    manual=[dict(id=m["id"], title=m["title"], title_en=m.get("title_en") or m["title"]) for m in p.get("manual", [])])

    # --- one entry -------------------------------------------------------------------------------------------------

    def brief(self, e):
        item, kind = e["item"], e["kind"]
        if kind == "function":
            sig = f"{e['name']}({', '.join(param(a) for a in item.get('args', []))})"
            rets = item.get("rets") or []
            if rets:
                sig += " → " + ", ".join(param(r) for r in rets)
            flags = function_flags(item.get("raw") or {}, bool(e.get("obj")))
        elif kind == "event":
            sig = f"{e['name']}: " + (", ".join(param(a) for a in item.get("payload", [])) or tr("（无载荷）", "(no payload)"))
            flags = event_flags(item.get("raw") or {})
        else:
            n = len(item.get("fields", []))
            sig = tr(f"{e['name']}（{item.get('type')}，{n} 项）", f"{e['name']} ({item.get('type')}, {n} entries)")
            flags = []
        out = dict(kind=kind, name=e["name"], ns=e["ns"], sig=sig, flags=flags, call=e["call"], why=e["why"], doc=item.get("doc") or "")
        if e.get("obj"):
            out["obj"] = e["obj"]
        if kind == "table":
            out.update(type=item.get("type"), size=len(item.get("fields", [])))
        if e["name"] in self.since:
            s = self.since[e["name"]]
            out["since"] = dict(build=s["build"], what=s["what"], note=tr(s["note"], s["note_en"]))
        return out

    def full(self, e):
        out = self.brief(e)
        item = e["item"]
        if e["kind"] == "function":
            out.update(args=item.get("args", []), returns=item.get("rets", []))
            typed = item.get("args", []) + item.get("rets", [])
        elif e["kind"] == "event":
            out.update(payload=item.get("payload", []))
            typed = item.get("payload", [])
        else:
            out.update(type=item.get("type"), fields=item.get("fields", []))
            typed = item.get("fields", []) if item.get("type") != "Enumeration" else []
        # the tables its types name (a structure's fields, an enum's values), so that the page shows them in place
        out["types"] = {}
        for p in typed:
            t = self.tables.get(str(p.get("t") or "").lower())
            if t and t["name"] != e["short"]:
                out["types"][t["name"]] = dict(type=t.get("type"), fields=t.get("fields", []))
        row = self.groups.get(e.get("group")) if e.get("group") else None
        if row and e["kind"] in ("function", "event"):   # the rest of its namespace, function group or object, of its kind
            out["siblings"] = [x["short"] for x in row["entries"] if x["kind"] == e["kind"] and x is not e]
            prefix = (e["ns"] + "." if e["ns"] else e["obj"] + ":" if e.get("obj") else "") if e["kind"] == "function" else ""
            out["scope"] = dict(key=row["key"], title=row["title"], group=row["group"], prefix=prefix)
        if e["kind"] == "function":                # its own: TargetUnit( and not SpellTargetUnit(
            needle = re.compile(r"(?<![\w.])" + re.escape(e["name"]) + r"\(")
            out["usage"] = [u for u in self.pack.get("usage", []) if needle.search(u)][:3]
        out["raw"] = item.get("raw") or {}
        return out

    # --- search and lookup -----------------------------------------------------------------------------------------

    def _ranked(self, query, kind=None, call=None):
        """the matching entries, best first (and the query's words)"""
        q = " ".join(str(query or "").lower().split())
        if not q:
            return [], []
        words = q.split()
        scored = []
        for e in self.entries:
            if kind and e["kind"] != kind:
                continue
            if call and not (e["call"] in ("ok", "limited") if call == "usable" else e["call"] == call):
                continue
            low, short = e["low"], e["short"].lower()
            if not all(w in low or w in e["text"] for w in words):
                continue
            if low == q:
                s = 100
            elif short == q:
                s = 90
            elif low.startswith(q):
                s = 70
            elif short.startswith(q):
                s = 60
            elif q in low:
                s = 40
            elif all(w in low for w in words):
                s = 30
            else:
                s = 10                              # found in the description only
            scored.append((-s, len(e["name"]), e["name"], e))
        scored.sort(key=lambda x: x[:3])
        return [e for *_, e in scored], words

    def search(self, query, kind=None, limit=20, call=None):
        found, words = self._ranked(query, kind, call)
        if not words:
            return []
        results = [self.brief(e) for e in found[:max(1, min(int(limit), 200))]]
        if not kind and not call and len(results) < limit:   # the exe's Usage strings fill up an unfiltered search
            results += self._usage(words, limit - len(results))
        return results

    def find(self, query, kind=None, call=None, limit=80, offset=0):
        """the page's search: a page of the results, how many of all the matches each call class has, and how many more
        there are; without a query, a kind or a call filter lists what it keeps (by kind, then name)"""
        found, words = self._ranked(query, kind)
        if not words:
            if not (kind or call):
                return dict(results=[], counts=None, total=0, more=0)
            found = sorted((e for e in self.entries if not kind or e["kind"] == kind), key=lambda e: (KINDS.index(e["kind"]), e["low"]))
        counts = {c: sum(1 for e in found if e["call"] == c) for c in CALLS}
        kept = [e for e in found if not call or (e["call"] in ("ok", "limited") if call == "usable" else e["call"] == call)]
        offset, limit = max(0, int(offset or 0)), max(1, min(int(limit), 200))
        results = [self.brief(e) for e in kept[offset:offset + limit]]
        if words and not kind and not call and not offset and len(results) < limit:   # the exe's Usage strings fill it up
            results += self._usage(words, limit - len(results))
        return dict(results=results, counts=counts, total=len(found), more=max(0, len(kept) - offset - limit))

    def _usage(self, words, n):
        usage = [u for u in self.pack.get("usage", []) if all(w in u.lower() for w in words)]
        return [dict(kind="usage", name=u, ns="", sig=u, flags=[], call="ok", why=[],
                     doc=tr("exe 里的用法字符串（%s 是函数名）", "a Usage string of the exe (%s is the function's name)")) for u in usage[:n]]

    # --- the browser -----------------------------------------------------------------------------------------------

    def _row(self, g):
        es = g["entries"]
        return dict(key=g["key"], title=g["title"], group=g["group"],
                    counts={k: sum(1 for e in es if e["kind"] == k) for k in KINDS},
                    calls={c: sum(1 for e in es if e["kind"] != "table" and e["call"] == c) for c in CALLS})

    def systems(self):
        """the rows to browse: the namespaces, then the global function groups, then the objects, each by name"""
        order = {"namespace": 0, "global": 1, "object": 2}
        return sorted((self._row(g) for g in self.groups.values() if g["entries"]), key=lambda r: (order[r["group"]], r["title"].lower()))

    def system(self, key):
        """one row with its entries (brief): functions, events, tables by name; None for an unknown key"""
        g = self.groups.get(str(key or "")) or next((x for x in self.groups.values() if x["key"].lower() == str(key or "").lower()), None)
        if g is None:
            return None
        out = self._row(g)
        for kind, name in (("function", "functions"), ("event", "events"), ("table", "tables")):
            out[name] = [self.brief(e) for e in sorted((e for e in g["entries"] if e["kind"] == kind), key=lambda e: e["low"])]
        return out

    def get(self, name):
        key = str(name or "").strip().lower()
        found = self.by_name.get(key) or self.by_short.get(key) or []
        if len(found) == 1:
            return self.full(found[0])
        if not found:
            return None
        return dict(candidates=[self.brief(e) for e in found])

    def manual(self, topic=None, lang=None):
        """the topics, or one: in English when lang is "en" and the pack has the English text"""
        en = str(lang or "").lower().startswith("en")
        def pick(m):
            if en and m.get("md_en"):
                return dict(id=m["id"], title=m.get("title_en") or m["title"], md=m["md_en"])
            return dict(id=m["id"], title=m["title"], md=m.get("md", ""))
        topics = self.pack.get("manual", [])
        if not topic:
            return [dict(id=m["id"], title=(m.get("title_en") or m["title"]) if en else m["title"]) for m in topics]
        t = str(topic).strip().lower()
        for m in topics:
            if t in (m["id"].lower(), m["title"].lower(), str(m.get("title_en", "")).lower()):
                return pick(m)
        for m in topics:
            if any(t in str(m.get(k, "")).lower() for k in ("id", "title", "title_en", "md", "md_en")):
                return pick(m)
        return None


_cached = (None, None)


def index():
    """the index of the pack in use (rebuilt when a newer pack was downloaded)"""
    global _cached
    pack = content.PACKS.get("api")
    if _cached[0] is not pack:
        _cached = (pack, ApiIndex(pack))
    return _cached[1]
