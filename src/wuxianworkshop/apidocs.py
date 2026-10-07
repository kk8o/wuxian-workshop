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

What an addon may do with an entry (`call`, with `why`: the documentation fields that say so):
    ok         it may call / register / use it
    limited    it may, with a catch: usage restrictions (HasRestrictions), secret values in restricted states (SecretReturns,
               SecretWhen..., SecretIn...), a precondition (Requires...: the clubs, the friend list, an active commentator ...);
               an event delivered through callbacks only (CallbackEvent) or with secret payloads
    protected  reserved to Blizzard's secure code: a protected function (IsProtectedFunction, or one of the protected globals
               the manual lists: an addon's call is blocked, ADDON_ACTION_BLOCKED) or a restricted event (HasRestrictions:
               registering it is forbidden, ADDON_ACTION_FORBIDDEN)
"""
import re

from . import content

KINDS =("function", "event", "table")
CALLS = ("ok", "limited", "protected")
SECRET_TAGS = {"AllowedWhenTainted": "插件可以传机密值", "NotAllowed": "不接受机密值"}


def param(p):
    """name?: type = default (as Lua writes it)"""
    text = f"{p.get('n')}{'?' if p.get('nil') else ''}: {p.get('t')}"
    d = p.get("def")
    if d is None or d == "":
        return text
    return text + " = " + (str(d).lower() if isinstance(d, bool) else str(d))


def function_flags(raw):
    flags = []
    if raw.get("IsProtectedFunction"):
        flags.append("受保护（只有安全代码能调用，战斗中对安全框体不可用）")
    if raw.get("SecretArguments") in SECRET_TAGS:
        flags.append(SECRET_TAGS[raw["SecretArguments"]])
    secret = sorted(k for k, v in raw.items() if v and (k == "SecretReturns" or k.startswith(("SecretWhen", "SecretIn"))))
    if secret:
        flags.append("可能返回机密值：" + "、".join(secret))
    if raw.get("HasRestrictions"):
        flags.append("有使用限制")
    return flags


def event_flags(raw):
    flags = []
    if raw.get("HasRestrictions"):
        flags.append("受限：插件注册它会被客户端拦截（ADDON_ACTION_FORBIDDEN）")
    if raw.get("CallbackEvent"):
        flags.append("回调事件（CallbackEvent）：由客户端的回调分发，插件不一定能用 RegisterEvent 收到")
    if raw.get("SecretPayloads"):
        flags.append("载荷可能是机密值")
    if raw.get("UniqueEvent"):
        flags.append("同一帧只触发一次")
    if raw.get("SynchronousEvent"):
        flags.append("同步事件")
    return flags


def _secret_or_needs(k):
    return k.startswith(("SecretReturns", "SecretWhen", "SecretIn", "Requires"))


def callability(kind, raw, protected_global=False):
    """(call, why): how far an addon may use the entry (CALLS) and the documentation fields that say so"""
    raw = raw or {}
    if kind == "function":
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
        for n in self.pack.get("namespaces", []):
            ns = n.get("ns") or ""
            for kind, key in (("function", "functions"), ("event", "events"), ("table", "tables")):
                for item in n.get(key, []):
                    short = item["name"]
                    full = f"{ns}.{short}" if kind == "function" and ns else short
                    call, why = callability(kind, item.get("raw"), kind == "function" and not ns and short in self.protected_globals)
                    e = dict(kind=kind, name=full, short=short, ns=ns, item=item, low=full.lower(),
                             text=(item.get("doc") or "").lower(), call=call, why=why)
                    self.entries.append(e)
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
                    self.since[x["name"]] = dict(build=c.get("to"), what=what, note=x.get("note") or "")

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
            flags = function_flags(item.get("raw") or {})
        elif kind == "event":
            sig = f"{e['name']}: " + (", ".join(param(a) for a in item.get("payload", [])) or "（无载荷）")
            flags = event_flags(item.get("raw") or {})
        else:
            sig = f"{e['name']}（{item.get('type')}，{len(item.get('fields', []))} 项）"
            flags = []
        out = dict(kind=kind, name=e["name"], ns=e["ns"], sig=sig, flags=flags, call=e["call"], why=e["why"], doc=item.get("doc") or "")
        if kind == "table":
            out.update(type=item.get("type"), size=len(item.get("fields", [])))
        if e["name"] in self.since:
            out["since"] = self.since[e["name"]]
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
        if e["ns"]:                              # the rest of its namespace: functions, or events of an event's
            key = "functions" if e["kind"] == "function" else "events" if e["kind"] == "event" else None
            if key:
                ns = next((n for n in self.pack.get("namespaces", []) if (n.get("ns") or "") == e["ns"]), None)
                out["siblings"] = [x["name"] for x in (ns or {}).get(key, []) if x["name"] != e["short"]]
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
            usage = [u for u in self.pack.get("usage", []) if all(w in u.lower() for w in words)]
            results += [dict(kind="usage", name=u, ns="", sig=u, flags=[], call="ok", why=[], doc="exe 里的用法字符串（%s 是函数名）")
                        for u in usage[:limit - len(results)]]
        return results

    def find(self, query, kind=None, call=None, limit=80):
        """the page's search: the results (search) and how many of all the matches each call class has"""
        found, _ = self._ranked(query, kind)
        counts = {c: sum(1 for e in found if e["call"] == c) for c in CALLS}
        return dict(results=self.search(query, kind, limit, call), counts=counts, total=len(found))

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
