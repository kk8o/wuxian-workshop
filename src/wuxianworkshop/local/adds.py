r"""What the enabled local extensions add to apps, read from their manifests for the host (Host has a method for each):

    declarations(host)            [(extension id, what kit.app.declare gets)] of their own apps
    adds(host, app)               what they add to an app that may be another's: its wake notes and skill supplements
    app_reactions(host, app)      the reactions of the app's own declaration (installed while the app runs)
    control_call(host, app, cmd)  the capability a control of the app is bound to
    row(manifest)                 the adds of one manifest for the rows of the API

The fifth batch's contract (work/layers/contract5.md, sections 4 and 5). annotations() and extensions_of() are the
fourth batch's (the manifest's annotate and extends) and go when the manifest has adds.

host.adds_cache is this module's to keep things in: the host sets it None whenever the registry is saved.
Standard library only.
"""
import os

from . import manifest as M


def adds(host, app):
    """[{"ext": id, "title": title, "wake": {"call": "facts.lookup", "timeout": 3.0} or None, "skill": <absolute path
    of the Markdown> or None}] of the enabled extensions whose manifest passes and adds to app, by extension id; a copy
    each time. Called for every wake of every app, so it reads no manifest while neither the registry nor an enabled
    extension's manifest changed. (To do: W-F)"""
    return []


def app_reactions(host, app):
    """[{"ext": id, "hear", "when", "call", "args", "item", "cooldown", "note"}] of the app's reactions, as the
    manifest of the enabled extension that declares it gives them (apps[].reactions, in its order); [] for an app no
    enabled extension declares. (To do: W-F)"""
    return []


def control_call(host, app, cmd):
    """{"ext": id, "call": "<id>.<capability>"} when the enabled extension that declares app bound its control cmd to
    a capability of its own (apps[].controls[] {cmd, label, call}); None otherwise. (To do: W-F)"""
    return None


def row(manifest):
    """a manifest's adds for a row of the API: {app: {"wake": {"call", "timeout"} or None, "skill": <absolute path> or
    None}}; {} without a manifest"""
    value = (manifest or {}).get("adds")
    return {app: dict(v) for app, v in value.items()} if isinstance(value, dict) else {}


def declarations(host):
    """[(extension id, what kit.app.declare gets)] of the enabled extensions' apps; each says the extension it comes
    with (local {id, title}: its card in the game has no remove button)"""
    out = []
    for e in host.load()["extensions"]:
        if not e["enabled"]:
            continue
        m, _ = M.check_manifest(e["path"])
        if m is not None and m["id"] == e["id"]:
            here = {"id": e["id"], "title": m.get("title") or e["id"]}
            out += [(e["id"], dict(a, local=dict(here))) for a in m["apps"]]
    return out


def enabled_manifests(host):
    """[(entry, manifest)] of the enabled extensions whose manifest passes its check (and still has their id)"""
    out = []
    for e in host.load()["extensions"]:
        if e["enabled"]:
            m, _ = M.check_manifest(e["path"])
            if m is not None and m["id"] == e["id"]:
                out.append((e, m))
    return out


def annotations(host, app):
    """[{"ext": id, "call": "facts.lookup", "wake": "wake", "timeout": 3.0}] of the enabled extensions that annotate
    app, by extension id; once an extension described itself, only a call that is a see capability it has. Called
    for every wake of every app (the daemon's frame thread), so it is a table kept until the registry or an enabled
    extension's manifest changes: a few stats, no reads, while nothing did"""
    if not isinstance(app, str) or not app:
        return []
    return [dict(a) for a in annotation_table(host).get(app, [])]


def stamp(path):
    """(mtime_ns, size) of a file, or None when it is not there"""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st.st_mtime_ns, st.st_size


def annotation_table(host):
    """{app: [annotation]} of the enabled extensions, kept while the stamps of the registry and of the enabled
    extensions' manifests stay (save() drops it at once)"""
    with host.lock:
        cache = host.adds_cache
        if cache is not None and all(stamp(path) == was for path, was in cache[0]):
            return cache[1]
        stamps = [(host.registry, stamp(host.registry))]
        stamps += [(path, stamp(path)) for path in (M.locate(e["path"])[1] for e in host.load()["extensions"]
                                                        if e["enabled"])]
        table = {}
        for e, m in enabled_manifests(host):
            for a in m["annotate"]:
                if M.annotation_ok(a, e.get("described")):
                    table.setdefault(a["app"], []).append(dict(ext=e["id"], call=a["call"], wake=a["wake"],
                                                               timeout=a["timeout"]))
        for items in table.values():
            items.sort(key=lambda a: a["ext"])
        host.adds_cache = (stamps, table)
        return table


def extensions_of(host, skill):
    """[{"ext": id, "title": title, "path": <absolute path of the Markdown>}] of the enabled extensions that extend
    skill (a skill file's name), by extension id"""
    if not isinstance(skill, str) or not skill:
        return []
    out = [dict(ext=e["id"], title=m.get("title") or e["id"], path=m["extends"][skill])
           for e, m in enabled_manifests(host) if skill in m["extends"]]
    return sorted(out, key=lambda x: x["ext"])
