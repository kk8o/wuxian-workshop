r"""What the enabled local extensions add to apps, read from their manifests for the host (Host has a method for each):

    declarations(host)            [(extension id, what kit.app.declare gets)] of their own apps
    adds(host, app)               what they add to an app that may be another's: its wake notes and skill supplements
    app_reactions(host, app)      the reactions of the app's own declaration (installed while the app runs)
    drivers(host)                 {app: extension id} of the apps an extension's own program drives
    control_call(host, app, cmd)  the capability a control of the app is bound to, and its arguments
    row(manifest)                 the adds of one manifest for the rows of the API
    roster(host)                  the extensions with a local part, as WuxianKit's window is told of them

The fifth batch's contract (work/layers/contract5.md, sections 4, 5 and 11). adds() is called for every wake of every
app (the daemon's frame thread) and the others not much less often, so all of them read one table, kept in
host.adds_cache until the registry or an enabled extension's manifest changes (their stamps: a few stats, no reads,
while nothing did; the host sets the cache None whenever the registry is saved, which is also when an extension
described itself anew). Every answer is a copy.

An extension whose command the player has not allowed (registry.approval is not "ok"; where the setting lets the
workshop allow commands by itself, host.auto(), it does so at the first start) adds nothing: no declaration, no wake,
no skill supplement, no reaction, no bound control, no app to drive. All of it comes when the player allows it (the
registry is saved then, so the table is built anew). Neither does one that needs a newer workshop than this one (its
manifest's requires.workshop; Host.unmet_error): nothing of it runs here.

Standard library only.
"""
import copy
import os

from . import manifest as M
from .registry import option_rows


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


def drivers(host):
    """{app: extension id} of the apps the enabled extensions' own programs drive (apps[].driver "local"), whether
    or not the program runs now (Host.driven says which do)"""
    return dict(table(host)["drivers"])


def control_call(host, app, cmd):
    """{"ext": id, "call": "<id>.<capability>", "args": {...}} when the enabled extension that declares app bound its
    control cmd to a capability of its own (apps[].controls[] {cmd, label, call, args}; args {} when it gives none);
    None otherwise. A copy each time"""
    if not isinstance(app, str) or not isinstance(cmd, str):
        return None
    found = table(host)["controls"].get((app, cmd))
    return copy.deepcopy(found) if found else None


def row(manifest):
    """a manifest's adds for a row of the API: {app: {"wake": {"call", "timeout"} or None, "skill": <absolute path> or
    None}}; {} without a manifest"""
    value = (manifest or {}).get("adds")
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def declarations(host):
    """[(extension id, what kit.app.declare gets)] of the enabled extensions' apps; each says the extension it comes
    with (local {id, title}: its card in the game names the extension). Without what is the workshop's alone: the
    reactions, and the call and args of a control"""
    return copy.deepcopy(table(host)["declarations"])


def declaration(app, eid, title):
    """an app of a manifest as kit.app.declare gets it. A control with a call goes as WuxianKit writes controls: its
    own words as strings (it takes no {cmd, label} of them), the app's own as {cmd, label}. driver and link go as
    they are (the manifest's check keeps them only when they say "local" and "live")"""
    out = {k: v for k, v in app.items() if k != "reactions"}
    if isinstance(out.get("controls"), list):
        out["controls"] = [c if isinstance(c, str) else c["cmd"] if c["cmd"] in M.CORE_CONTROLS
                           else {"cmd": c["cmd"], "label": c.get("label")} for c in out["controls"]]
    out["local"] = {"id": eid, "title": title}
    return out


def roster(host):
    """what the game's WuxianKit is told of the extensions added to this computer (kit.local.set; its /wk window lists
    them beside the game's own): [{id, title, version?, author?, notes?, where, on, running, addons, adds?,
    options?}] of those with a local part (where "local" or "both": one in the game only is the game's own to show),
    in the registry's order. on: enabled and adding what it adds (as enabled_manifests() takes it); running: its
    process runs now (Host.is_running); addons: the folders of the game addons it brings ([]: none), by which
    WuxianKit knows the game extensions that belong to it (those such an addon registers; and the one of its own id);
    adds: the apps it adds to; options: roster_options(), left out when it has none. One whose manifest
    does not pass is left out. Read anew each time (an extension that is off counts too, and the table keeps only
    those that are on)"""
    out = []
    with host.lock:
        for e in host.load()["extensions"]:
            m, _ = M.check_manifest(e["path"])
            if m is None or m["id"] != e["id"] or m["where"] == "game":
                continue
            item = dict(id=e["id"], title=m.get("title") or e["id"], where=m["where"],
                        on=bool(e["enabled"]) and allowed(host, e) and host.unmet_error(m) is None,
                        running=host.is_running(e["id"]), addons=list(m.get("addons") or []))
            for key in ("version", "author", "notes"):
                if isinstance(m.get(key), str) and m[key]:
                    item[key] = m[key]
            if m.get("adds"):
                item["adds"] = sorted(m["adds"])
            options = roster_options(e)
            if options:
                item["options"] = options
            out.append(item)
    return out


ROSTER_OPTION_KEYS = ("default", "hint", "choices", "min", "max", "step", "unit")   # the optional ones, when given


def roster_options(entry):
    """the options of a registry entry as WuxianKit's /wk window shows them (kit.local.set): [{name, kind, title,
    value, default?, hint?, choices? [{value, label}], min?, max?, step?, unit?}] in the order the extension described
    them, value the player's (else the default); [] before it described itself. kind boolean, number and choice the
    player may change there (kit.local.option), text only here"""
    out = []
    for o in option_rows(entry):
        item = dict(name=o["name"], kind=o["kind"], title=o.get("title") or o["name"], value=o.get("value"))
        for key in ROSTER_OPTION_KEYS:
            if o.get(key) is not None:
                item[key] = copy.deepcopy(o[key])
        out.append(item)
    return out


def enabled_manifests(host):
    """[(entry, manifest)] of the enabled extensions whose manifest passes its check (and still has their id),
    whose command may run (allowed()) and which this workshop is not too old for (host.unmet_error: one that needs
    a newer workshop does not start, so it adds nothing either)"""
    out = []
    for e in host.load()["extensions"]:
        if e["enabled"]:
            m, _ = M.check_manifest(e["path"])
            if m is not None and m["id"] == e["id"] and allowed(host, e) and host.unmet_error(m) is None:
                out.append((e, m))
    return out


def allowed(host, entry):
    """whether the extension's command may run as it is now: the registry says the player allowed it, or the workshop
    allows it by itself (Host.by_itself: where the setting says so, and one in the game only; it does so when the
    extension starts or is loaded; never one whose entry has ask, as Host.allow says)"""
    return host.reg.approval(entry) == "ok" or host.by_itself(entry)


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
    """{"declarations": [(ext, declaration)], "adds": {app: [..]}, "reactions": {app: [..]}, "drivers": {app: ext},
    "controls": {(app, cmd): {ext, call, args}}} of the enabled extensions, kept while auto() and the stamps of the
    registry and of the enabled extensions' manifests stay (save() drops it at once). Not to be changed: the callers
    copy what they give out"""
    with host.lock:
        cache, auto = host.adds_cache, bool(host.auto())
        if cache is not None and cache[2] == auto and all(stamp(path) == was for path, was in cache[0]):
            return cache[1]
        stamps = [(host.registry, stamp(host.registry))]
        stamps += [(path, stamp(path)) for path in (M.locate(e["path"])[1] for e in host.load()["extensions"]
                                                        if e["enabled"])]
        built = {"declarations": [], "adds": {}, "reactions": {}, "drivers": {}, "controls": {}}
        for e, m in enabled_manifests(host):
            eid, title = e["id"], m.get("title") or e["id"]
            for app, a in m["adds"].items():
                wake = a["wake"] if a["wake"] and wake_ok(a["wake"], eid, e.get("described")) else None
                built["adds"].setdefault(app, []).append(dict(ext=eid, title=title, wake=wake, skill=a["skill"]))
            for a in m["apps"]:
                built["declarations"].append((eid, declaration(a, eid, title)))
                if a["reactions"]:
                    built["reactions"].setdefault(a["app"], []).extend(dict(r, ext=eid) for r in a["reactions"])
                if a.get("driver") == "local":
                    built["drivers"].setdefault(a["app"], eid)
                for c in a.get("controls") or []:
                    if isinstance(c, dict) and c.get("call"):
                        built["controls"].setdefault((a["app"], c["cmd"]), dict(ext=eid, call=f"{eid}.{c['call']}",
                                                                               args=copy.deepcopy(c.get("args") or {})))
        for items in built["adds"].values():
            items.sort(key=lambda a: a["ext"])
        host.adds_cache = (stamps, built, auto)
        return built


