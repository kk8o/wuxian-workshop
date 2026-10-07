"""The API manual: the "api" content pack (content.py) read for the MCP tools api_search / api_get / api_manual,
`wuxian api` and the API 手册 page. The pack holds this client's own API documentation (Blizzard_APIDocumentationGenerated,
extracted from the client's files: namespaces with their functions, events and tables), the exe's `Usage:` strings and
the manual 无限图鉴 wrote (runtime, taint, secret values, the .toc, load switches, protected functions, GameRules, the
无限-only API); scripts\build_api_pack.py builds it from 无限图鉴's data (not in this repository).

    index().search("UnitHealth") -> [{kind, name, ns, sig, flags, doc}]     ranked: exact, prefix, substring, text
    index().get("C_Spell.GetSpellInfo") -> the entry in full (or {"candidates": [...]} when a short name is ambiguous)
    index().manual() -> [{id, title}];  index().manual("taint") -> {id, title, md}
"""
from . import content

KINDS = ("function", "event", "table")
SECRET_TAGS = {"AllowedWhenTainted": "插件可以传机密值", "NotAllowed": "不接受机密值"}


def param(p):
    """name?: type = default"""
    text = f"{p.get('n')}{'?' if p.get('nil') else ''}: {p.get('t')}"
    return text + (f" = {p['def']}" if p.get("def") not in (None, "") else "")


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


class ApiIndex:
    def __init__(self, pack):
        self.pack = pack or {}
        self.entries = []                       # dicts: kind, name, short, ns, item
        self.by_name, self.by_short = {}, {}
        for n in self.pack.get("namespaces", []):
            ns = n.get("ns") or ""
            for kind, key in (("function", "functions"), ("event", "events"), ("table", "tables")):
                for item in n.get(key, []):
                    short = item["name"]
                    full = f"{ns}.{short}" if kind == "function" and ns else short
                    e = dict(kind=kind, name=full, short=short, ns=ns, item=item, low=full.lower(),
                             text=(item.get("doc") or "").lower())
                    self.entries.append(e)
                    self.by_name.setdefault(full.lower(), []).append(e)
                    if kind == "function":
                        self.by_short.setdefault(short.lower(), []).append(e)

    # --- what the pack is ------------------------------------------------------------------------------------------

    def about(self):
        p = self.pack
        counts = {k: sum(1 for e in self.entries if e["kind"] == k) for k in KINDS}
        return dict(version=p.get("version"), client=p.get("client"), interface=p.get("interface"), built=p.get("built"),
                    source=p.get("source"), counts=counts, manual=[dict(id=m["id"], title=m["title"]) for m in p.get("manual", [])])

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
        return dict(kind=kind, name=e["name"], ns=e["ns"], sig=sig, flags=flags, doc=item.get("doc") or "")

    def full(self, e):
        out = self.brief(e)
        item = e["item"]
        if e["kind"] == "function":
            out.update(args=item.get("args", []), returns=item.get("rets", []))
        elif e["kind"] == "event":
            out.update(payload=item.get("payload", []))
        else:
            out.update(type=item.get("type"), fields=item.get("fields", []))
        out["raw"] = item.get("raw") or {}
        return out

    # --- search and lookup -----------------------------------------------------------------------------------------

    def search(self, query, kind=None, limit=20):
        q = " ".join(str(query or "").lower().split())
        if not q:
            return []
        words = q.split()
        scored = []
        for e in self.entries:
            if kind and e["kind"] != kind:
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
        results = [self.brief(e) for *_, e in scored[:max(1, min(int(limit), 200))]]
        if not kind and len(results) < limit:            # the exe's Usage strings fill up an unfiltered search
            usage =[u for u in self.pack.get("usage", []) if all(w in u.lower() for w in words)]
            results += [dict(kind="usage", name=u, ns="", sig=u, flags=[], doc="exe 里的用法字符串（%s 是函数名）")
                        for u in usage[:limit - len(results)]]
        return results

    def get(self, name):
        key = str(name or "").strip().lower()
        found = self.by_name.get(key) or self.by_short.get(key) or []
        if len(found) == 1:
            return self.full(found[0])
        if not found:
            return None
        return dict(candidates=[self.brief(e) for e in found])

    def manual(self, topic=None):
        topics = self.pack.get("manual", [])
        if not topic:
            return [dict(id=m["id"], title=m["title"]) for m in topics]
        t = str(topic).strip().lower()
        for m in topics:
            if m["id"].lower() == t or m["title"].lower() == t:
                return m
        for m in topics:
            if t in m["id"].lower() or t in m["title"].lower() or t in m.get("md", "").lower():
                return m
        return None


_cached = (None, None)


def index():
    """the index of the pack in use (rebuilt when a newer pack was downloaded)"""
    global _cached
    pack = content.PACKS.get("api")
    if _cached[0] is not pack:
        _cached = (pack, ApiIndex(pack))
    return _cached[1]
