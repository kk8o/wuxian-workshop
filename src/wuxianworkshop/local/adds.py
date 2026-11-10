r"""What the enabled local extensions add to apps, read from their manifests for the host (Host has a method for each):

    declarations(host)            [(extension id, what kit.app.declare gets)] of their own apps
    adds(host, app)               what they add to an app that may be another's: its wake notes and skill supplements
    app_reactions(host, app)      the reactions of the app's own declaration (installed while the app runs)
    control_call(host, app, cmd)  the capability a control of the app is bound to
    row(manifest)                 the adds of one manifest for the rows of the API

The fifth batch's contract (work/layers/contract5.md, sections 4, 5 and 11). adds() is called for every wake of every
app (the daemon's frame thread) and the others not much less often, so all of them read one table, kept in
host.adds_cache until the registry or an enabled extension's manifest changes (their stamps: a few stats, no reads,
while nothing did; the host sets the cache None whenever the registry is saved, which is also when an extension
described itself anew). Every answer is a copy.

An extension whose command the player has not allowed (registry.approval is not "ok"; where the setting lets the
workshop allow commands by itself, host.auto(), it does so at the first start) adds nothing: no declaration, no wake,
no skill supplement, no reaction, no bound control. All of it comes when the player allows it (the registry is saved
then, so the table is built anew).

Standard library only.
"""
import copy
import os

from . import manifest as M


def adds(host, app):
    """[{"ext": id, "title": title, "wake": {"call": "facts.lookup", "timeout": 3.0} or None, "skill": <absolute path
    of the Markdown> or None}] of the enabled extensions whose manifest passes and adds to app, by extension id; a copy
    each time. Once an extension described itself, a wake that is not a see capability it has is None"""
    if not isinstance(app, str) or not app:
        return []
    return copy.deepcopy(table(host)["adds"].get(app, []))


def app_reactions(host, app):
    """[{"ext": id, "hear", "when", "call", "args", "item", "cooldown", "note"}] of the app's reactions, as the
    manifest of the enabled extension that declares it gives them (apps[].reactions, in its order); [] for an app no
    enabled extension declares"""
    if not isinstance(app, str) or not app:
        return []
    return copy.deepcopy(table(host)["reactions"].get(app, []))


def control_call(host, app, cmd):
    """{"ext": id, "call": "<id>.<capability>"} when the enabled extension that declares app bound its control cmd to
    a capability of its own (apps[].controls[] {cmd, label, call}); None otherwise"""
    if not isinstance(app, str) or not isinstance(cmd, str):
        return None
    found = table(host)["controls"].get((app, cmd))
    return dict(found) if found else None


def row(manifest):
    """a manifest's adds for a row of the API: {app: {"wake": {"call", "timeout"} or None, "skill": <absolute path> or
    None}}; {} without a manifest"""
    value = (manifest or {}).get("adds")
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def declarations(host):
    """[(extension id, what kit.app.declare gets)] of the enabled extensions' apps; each says the extension it comes
    with (local {id, title}: its card in the game names the extension). Without what is the workshop's alone: the
    reactions, and the call of a control"""
    return copy.deepcopy(table(host)["declarations"])


def declaration(app, eid, title):
    """an app of a manifest as kit.app.declare gets it. A control with a call goes as WuxianKit writes controls: its
    own words as strings (it takes no {cmd, label} of them), the app's own as {cmd, label}"""
    out = {k: v for k, v in app.items() if k != "reactions"}
    if isinstance(out.get("controls"), list):
        out["controls"] = [c if isinstance(c, str) else c["cmd"] if c["cmd"] in M.CORE_CONTROLS
                           else {"cmd": c["cmd"], "label": c.get("label")} for c in out["controls"]]
    out["local"] = {"id": eid, "title": title}
    return out


def enabled_manifests(host):
    """[(entry, manifest)] of the enabled extensions whose manifest passes its check (and still has their id) and
    whose command may run (allowed())"""
    out = []
    for e in host.load()["extensions"]:
        if e["enabled"]:
            m, _ = M.check_manifest(e["path"])
            if m is not None and m["id"] == e["id"] and allowed(host, e):
                out.append((e, m))
    return out


def allowed(host, entry):
    """whether the extension's command may run as it is now: the registry says the player allowed it, or the workshop
    allows commands by itself (host.auto(): it does so when the extension starts)"""
    return host.reg.approval(entry) == "ok" or bool(host.auto())


def stamp(path):
    """(mtime_ns, size) of a file, or None when it is not there"""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st.st_mtime_ns, st.st_size


def wake_ok(wake, eid, described):
    """whether a wake's call may be made: before the extension described itself yes (it starts on the call), after
    only when it is a see capability it has"""
    if described is None:
        return True
    name = wake["call"][len(eid) + 1:]
    return any(isinstance(c, dict) and c.get("name") == name and c.get("kind") == "see"
               for c in described.get("capabilities") or [])


def table(host):
    """{"declarations": [(ext, declaration)], "adds": {app: [..]}, "reactions": {app: [..]}, "controls": {(app, cmd):
    {ext, call}}} of the enabled extensions, kept while auto() and the stamps of the registry and of the enabled
    extensions' manifests stay (save() drops it at once). Not to be changed: the callers copy what they give out"""
    with host.lock:
        cache, auto = host.adds_cache, bool(host.auto())
        if cache is not None and cache[2] == auto and all(stamp(path) == was for path, was in cache[0]):
            return cache[1]
        stamps = [(host.registry, stamp(host.registry))]
        stamps += [(path, stamp(path)) for path in (M.locate(e["path"])[1] for e in host.load()["extensions"]
                                                        if e["enabled"])]
        built = {"declarations": [], "adds": {}, "reactions": {}, "controls": {}}
        for e, m in enabled_manifests(host):
            eid, title = e["id"], m.get("title") or e["id"]
            for app, a in m["adds"].items():
                wake = a["wake"] if a["wake"] and wake_ok(a["wake"], eid, e.get("described")) else None
                built["adds"].setdefault(app, []).append(dict(ext=eid, title=title, wake=wake, skill=a["skill"]))
            for a in m["apps"]:
                built["declarations"].append((eid, declaration(a, eid, title)))
                if a["reactions"]:
                    built["reactions"].setdefault(a["app"], []).extend(dict(r, ext=eid) for r in a["reactions"])
                for c in a.get("controls") or []:
                    if isinstance(c, dict) and c.get("call"):
                        built["controls"].setdefault((a["app"], c["cmd"]), dict(ext=eid, call=f"{eid}.{c['call']}"))
        for items in built["adds"].values():
            items.sort(key=lambda a: a["ext"])
        host.adds_cache = (stamps, built, auto)
        return built


