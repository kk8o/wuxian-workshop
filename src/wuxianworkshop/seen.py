"""What the running game answered about names (seen in the game), kept per client build so that an answer is not
asked again and a verdict can say "seen in the game": %WUXIAN_HOME%\\state\\seen\\<build>.json,

    {name: {"type": "function" | "table" | "nil" | "event" | ..., "level": "presence", "at": epoch seconds,
            "members": [names]?, "more": n?, "valid": bool?}}

level "presence": what the game answered is that the name is there, of this type (or not there: nil; an event name
C_EventUtils.IsEventValid accepts or not: valid). It says nothing of whether a call works (a try with a valid input)
or is restricted in some state (blocked, secret values).

Fed by the check's live step (daemon/service.py: its answers) and the MCP tool api_verify; read by api_search / api_get
/ ui_get (`seen`) and the check (a kept name settles as the live step would: type nil is undefined).

    record(build, {name: type}, members=None, more=None, valid=None) -> the records written
                                members: {name: [member names]}, more: {name: how many not listed}, valid: {event: bool}
    get(build, name) -> {type, at, members?} or None;  many(build, names) -> {name: record}
    build_key(x) -> "71037"     a build as its number: 71037, "1.60.1.71037"
    current(game_build=None)    the build to read: the running game's when known, else the newest of what is kept and
                                the API manual's client

The file is written whole and atomically; it keeps at most MAX names (the oldest answers go first).
"""
import json
import re
import threading
import time

from .paths import home

MAX = 20000
TYPES = ("nil", "boolean", "number", "string", "function", "table", "userdata", "thread", "event")
LEVEL = "presence"
_lock = threading.Lock()
_cache = {}                                 # path -> (mtime_ns, size, data)


def build_key(build):
    """a build as its number ("71037"), from 71037 or "1.60.1.71037"; None for nothing usable"""
    if build is None:
        return None
    m = re.search(r"(\d+)\s*$", str(build))
    return m.group(1) if m else None


def folder(make=False):
    """state/seen (made only to write in it: reading leaves no folder behind)"""
    path = home() / "state" / "seen"
    if make:
        path.mkdir(parents=True, exist_ok=True)
    return path


def kept_builds():
    """the builds something is kept for"""
    try:
        return sorted((p.stem for p in folder().glob("*.json") if p.stem.isdigit()), key=int)
    except OSError:
        return []


def current(game_build=None):
    """the build whose answers apply: the running game's when it is known, else the newest of the builds kept and the
    API manual's client (a newer client's manual: what an older one answered is not shown for it)"""
    key = build_key(game_build)
    if key:
        return key
    builds = [int(b) for b in kept_builds()]
    try:
        from . import apidocs
        client = build_key(apidocs.index().about().get("client"))
    except Exception:                                    # no manual: what is kept decides
        client = None
    if client:
        builds.append(int(client))
    return str(max(builds)) if builds else None


def _path(build, make=False):
    key = build_key(build)
    if not key:
        raise ValueError(f"not a client build: {build!r}")
    return folder(make) / f"{key}.json"


def _read(path):
    try:
        st = path.stat()
    except OSError:
        return {}
    hit = _cache.get(path)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    _cache[path] = (st.st_mtime_ns, st.st_size, data)
    return data


def record(build, answers, members=None, more=None, valid=None, clock=time.time):
    """keeps the game's answers ({name: type}) for that build (and, for tables, their member names); a name answered
    again is replaced, its members kept when the new answer has none and it is still a table. more: {name: how many
    members were not listed}. Answers {name: record}"""
    from .core.game import atomic_write
    if not build_key(build) or not isinstance(answers, dict):
        return {}
    members = members if isinstance(members, dict) else {}
    now = round(clock(), 3)
    with _lock:
        path = _path(build, True)
        data = dict(_read(path))
        done = {}
        for name, kind in answers.items():
            if not isinstance(name, str) or not name or not isinstance(kind, str) or kind not in TYPES:
                continue
            rec = dict(type=kind, level=LEVEL, at=now)
            if kind == "event" and isinstance((valid or {}).get(name), bool):
                rec["valid"] = valid[name]
            got = members.get(name)
            if isinstance(got, (list, tuple)) and kind == "table":
                rec["members"] = sorted({str(m) for m in got})
                left = (more or {}).get(name)
                if isinstance(left, int) and left > 0:
                    rec["more"] = left
            else:
                old = data.get(name)
                if kind == "table" and isinstance(old, dict) and old.get("type") == "table" and old.get("members"):
                    rec["members"] = old["members"]
                    if old.get("more"):
                        rec["more"] = old["more"]
            data.pop(name, None)                     # to the end: the newest last
            data[name] = done[name] = rec
        if not done:
            return {}
        if len(data) > MAX:
            order = sorted(data, key=lambda n: (data[n].get("at") or 0) if isinstance(data[n], dict) else 0)
            for name in order[:len(data) - MAX]:
                data.pop(name, None)
        atomic_write(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        _cache.pop(path, None)
        return done


def get(build, name):
    """the record of a name for that build, or None"""
    if not build_key(build):
        return None
    rec = _read(_path(build)).get(str(name))
    return dict(rec) if isinstance(rec, dict) else None


def many(build, names):
    """{name: record} of those kept for that build"""
    if not build_key(build):
        return {}
    data = _read(_path(build))
    return {n: dict(data[n]) for n in names if isinstance(data.get(n), dict)}


def brief(rec):
    """a record as tools show it next to an entry: {type, at}, the members' count when there are some"""
    if not rec:
        return None
    out = dict(type=rec.get("type"), level=rec.get("level") or LEVEL, at=rec.get("at"))
    if "valid" in rec:
        out["valid"] = rec["valid"]
    if rec.get("members"):
        out["members"] = len(rec["members"]) + int(rec.get("more") or 0)
    return out
