r"""A local extension's manifest, wuxian-extension.json at the root of its folder, and the checks of what the extension
says of itself when it starts (its answer to initialize). Every finding is a problem {level: "error" | "warning", where,
message, hint}: where names the file and field (or the capability), message says what is wrong, hint how to fix it; the
words are for the agent that writes the extension.

    {"id": "voice", "title": "声音", "version": "0.1.0", "notes": "...",
     "run": ["python", "-m", "voice"], "python": "tts/.venv/Scripts/python.exe", "env": {"K": "V"}, "pass": ["HF_HOME"],
     "start": "use" | "workshop", "docs": "AGENT.md", "skills": "skills",
     "adds": {"guild": {"wake": "lookup", "skill": "skills/guild-chat.md"}},
     "apps": [{"app": "companion", "title": "语音搭子", "prompt": "companion",
               "controls": ["stop", {"cmd": "mute", "label": "静音", "call": "mute"}],
               "reactions": [{"hear": "game:combat.start", "call": "voice.say", "args": {"pool": "elite"}}]}]}

id:^[a-z][a-z0-9_]{0,23}$, not a WuxianKit extension's (chat game route sense guide data tune gate codex guides) and not
local, wb or wk. title: at most 48 bytes. run: the argv, relative to the folder; a first item "python" is the manifest's
`python` (relative to the folder), else the workshop's own interpreter. env: variables set for the process; pass: at
most PASS_MOST names of the player's environment variables it also gets (the process starts with a short list of the
system's, not with the whole environment). start: use (the default: started by the first call of one of its
capabilities) or workshop (started with the daemon: a model to warm up). docs: default AGENT.md when there is one;
skills: default skills/ when there is one.

adds: what it adds to apps, its own or others', by app id (at most ADDS_MOST): wake, a see capability of its own that
reads every wake of that app before the agent gets it and answers the facts to note on it (the capability's name without
the extension's id, or {call: name, timeout: seconds, default 3 and at most 10}); skill, a Markdown file in the
extension's folder that is appended to the skill the app's prompt names. That wake is a see capability it has is known
once it described itself (adds_problems).

apps: kit.app.open's fields without temp, driver and launch; the daemon declares them (kit.app.declare) after every
kit.ready. Two fields are the workshop's and do not reach WuxianKit: reactions (at most APP_REACTIONS_MOST; a reaction's
fields without id and owner: the workshop sets them while the app runs and clears them when it stops; call is a whole
capability id, this extension's, another's or the game's) and, in a control {cmd, label}, call: the name of a capability
of this extension's own that the workshop calls ({} as its arguments) when the player gives that control.

The description (initialize's answer): {protocol: 1, capabilities: [cap], sources: [src], options: [opt]};
cap = {name, kind: see | keep | point, title, doc, args: {name: spec}, timeout?: seconds (default 30, at most 600)}; a
name is dotted parts (pool.list), its tool wk_<id>_<name with _ for the dots> at most 48 characters and no other
capability's; an argument's spec as WuxianKit's capabilities write it (mcp/kit.py schema()): string number boolean table
any, a | b for either, words that are no type names an enumeration, a trailing ? optional. src = {name, title, doc};
opt = {name, kind: boolean | text | number | choice, title, default, hint?, choices?: [{value, label}], min?, max?,
step?, unit?}. say and do (proposals the player confirms) come in a later step: refused now (bad_capability).
The checks the SDK makes too (names, specs, option values) are validate.py's. Standard library only.
"""
import copy
import json
import re
from pathlib import Path

from . import reactions as R
from .validate import (ARG_NAME, ARG_NAME_HINT, CAP_NAME, CAP_NAME_HINT, CAP_NAME_MAX, DEFAULTS, KINDS,  # noqa: F401
                       OPTION_KINDS, OPTION_NAME, OPTION_NAME_HINT, SOURCE_NAME, SOURCE_NAME_HINT, SPEC_HINT, TEXT_MOST,
                       TIMEOUT_DEFAULT, TIMEOUT_MOST, TITLE_MAX, TOOL_NAME_MAX, TYPES, arg_problem, cap_name_problem,
                       finite, text_of, timeout_problem, tool_name, value_problem)

MANIFEST = "wuxian-extension.json"
PROTOCOL = 1
ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
APP_ID = ID
KIT_EXTENSIONS = ("chat", "game", "route", "sense", "guide", "data", "tune", "gate", "codex", "guides")
RESERVED = KIT_EXTENSIONS + ("local", "wb", "wk", "kit")
NOTES_MAX = 400
LATER_KINDS = ("say", "do")            # proposals: a later step
NOT_IN_APP = ("temp", "driver", "launch")
STARTS = ("use", "workshop")
KIT_APP_ID = re.compile(r"^[a-z0-9_]{1,24}$")   # an app id as WuxianKit writes it
ADDS_MOST = 8
ADDS_FIELDS = ("wake", "skill")
WAKE_FIELDS = ("call", "timeout")
WAKE_TIMEOUT, WAKE_TIMEOUT_MOST = 3.0, 10.0
APP_REACTIONS_MOST = 16
REACTION_FIELDS = ("hear", "when", "call", "args", "item", "cooldown", "note")
CONTROLS_MOST = 12                     # as WuxianKit takes them: its own words and the app's {cmd, label} together
CORE_CONTROLS = ("mute", "unmute", "less", "more", "test", "pause", "resume", "stop")   # WuxianKit's own controls
CONTROL_FIELDS = ("cmd", "label", "call")
CONTROL_CMD = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
PASS_MOST = 16
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_()]{0,63}$")
FIELDS = ("id", "title", "version", "notes", "run", "python", "env", "pass", "start", "docs", "skills", "apps", "adds")
REMOVED = {"annotate": "adds says it now: {\"<app>\": {\"wake\": \"<capability name>\"}}",
           "extends": "adds says it now: {\"<app>\": {\"skill\": \"skills/<file>.md\"}}, by the app's id"}


class ManifestError(Exception):
    """a manifest that cannot be used: its problems (the errors first)"""

    def __init__(self, problems):
        self.problems = problems
        first = next((p for p in problems if p["level"] == "error"), problems[0])
        super().__init__(f"{first['where']}: {first['message']}" + (f" ({first['hint']})" if first.get("hint") else ""))


def problem(level, where, message, hint=""):
    return dict(level=level, where=where, message=message, hint=hint)


def errors_in(problems):
    return [p for p in problems if p["level"] == "error"]


def locate(path):
    """(folder, manifest file) of a path that names an extension: its folder, or its manifest"""
    p = Path(path)
    if p.name.lower() == MANIFEST and not p.is_dir():
        return p.parent, p
    return p, p / MANIFEST


def id_problem(value, where="id"):
    """the problem with an extension's id, or None"""
    if not isinstance(value, str) or not ID.match(value):
        return problem("error", where, f"{value!r} is not an extension id",
                       "lowercase letters, digits and _, a letter first, at most 24 characters (voice, my_tool)")
    if value in RESERVED:
        return problem("error", where, f"{value!r} is taken",
                       "WuxianKit's own extensions (" + ", ".join(KIT_EXTENSIONS) + ") and local, wb, wk, kit are "
                       "reserved: choose another id")
    return None


def check_manifest(path):
    """(manifest or None, problems) of an extension's folder (or its manifest file). The manifest as the host uses it:
    {id, title, version, notes, run, python, env, pass, start, docs, skills, apps, adds, folder, file}; docs and skills
    absolute paths or None, python an absolute path or None, pass [names], adds {app: {wake: {call: "<id>.<name>",
    timeout} | None, skill: absolute path | None}}, every app with reactions [{hear, when, call, args, item, cooldown,
    note}] (maybe none) and its controls as written (strings, or {cmd, label?, call?} with call a capability's name
    without the id)"""
    folder, file = locate(path)
    where = MANIFEST
    out = []
    try:
        raw = file.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, [problem("error", where, f"there is no {MANIFEST} in {folder}",
                              "new_extension makes an extension folder with one; extension_guide shows its fields")]
    except (OSError, UnicodeDecodeError) as e:
        return None, [problem("error", where, f"it cannot be read: {e}", "save it as UTF-8")]
    try:
        d = json.loads(raw)
    except ValueError as e:
        return None, [problem("error", where, f"it is not valid JSON: {e}",
                              "JSON has no comments and no trailing commas; strings in double quotes")]
    if not isinstance(d, dict):
        return None, [problem("error", where, "it must be a JSON object", '{"id": "...", "title": "...", "run": [...]}')]

    def field(name):
        return f"{where}: {name}"

    for name in sorted(set(d) - set(FIELDS)):
        if name in REMOVED:
            out.append(problem("error", field(name), f"there is no {name} any more", REMOVED[name]))
        else:
            out.append(problem("warning", field(name), "this field means nothing to 无限工坊",
                               "the fields: " + ", ".join(sorted(FIELDS))))
    eid = d.get("id")
    bad = id_problem(eid, field("id"))
    if bad:
        out.append(bad)
    title = d.get("title") if d.get("title") is not None else eid
    title = text_of(title, TITLE_MAX)
    if title is None:
        out.append(problem("error", field("title"), "a title is some text of at most 48 bytes",
                           "the name the player sees, such as \"声音\" (about 16 Chinese characters at most)"))
    version = d.get("version")
    if version is not None and not text_of(version, 32):
        out.append(problem("error", field("version"), "a version is a short text", '"0.1.0"'))
    notes = d.get("notes")
    if notes is not None and text_of(notes, NOTES_MAX) is None:
        out.append(problem("error", field("notes"), "notes are one line of text", "what the extension does, briefly"))
    run = d.get("run")
    if not isinstance(run, list) or not run or not all(isinstance(a, str) for a in run) or not run[0].strip():
        out.append(problem("error", field("run"), "run is the command that starts it, a list of arguments",
                           '["python", "main.py"] or ["python", "-m", "voice"]; relative to the extension folder; '
                           '"python" is the manifest\'s python, else 无限工坊\'s own'))
        run = None
    python = d.get("python")
    python_path = None
    if python is not None:
        if not text_of(python):
            out.append(problem("error", field("python"), "python is the path of a python.exe",
                               "relative to the extension folder, such as \".venv/Scripts/python.exe\""))
        else:
            python_path = Path(python) if Path(python).is_absolute() else folder / python
            if not python_path.is_file():
                out.append(problem("error", field("python"), f"there is no {python_path}",
                                   "make the virtual environment, or leave python out to use 无限工坊's own Python"))
    env = d.get("env") or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and k and "=" not in k and isinstance(v, str)
                                            for k, v in env.items()):
        out.append(problem("error", field("env"), "env is the environment variables, name -> text",
                           '{"MY_SETTING": "1"}; secrets do not belong in the manifest'))
        env = {}
    passed = check_pass(d.get("pass"), field("pass"), out)
    start = d.get("start", "use")
    if start is None:
        start = "use"
    if start not in STARTS:
        out.append(problem("error", field("start"), f"start is \"use\" or \"workshop\", not {start!r}",
                           "use: started by the first call of one of its capabilities; workshop: started with 无限工坊 "
                           "(for a model that takes long to load)"))
        start = "use"
    docs = locate_file(folder, d.get("docs"), "AGENT.md", False, field("docs"), out)
    skills = locate_file(folder, d.get("skills"), "skills", True, field("skills"), out)
    if docs is None and d.get("docs") is None:
        out.append(problem("warning", field("docs"), "there is no AGENT.md",
                           "write one: wk_docs {\"extension\": id} shows it to the agent before it uses the tools"))
    apps = check_apps(d.get("apps"), field("apps"), out)
    adds = check_adds(d.get("adds"), eid, folder, field("adds"), out)
    manifest = dict(id=eid, title=title, version=text_of(version, 32) if version is not None else None,
                    notes=text_of(notes, NOTES_MAX) if notes is not None else None, run=list(run or []),
                    python=str(python_path) if python_path is not None else None, env=dict(env), start=start,
                    docs=str(docs) if docs else None, skills=str(skills) if skills else None, apps=apps, adds=adds,
                    folder=str(folder), file=str(file))
    manifest["pass"] = passed
    return (None if errors_in(out) else manifest), out


def locate_file(folder, given, default, is_dir, where, out):
    """the absolute path a docs / skills field names (default: the default name when it is there), or None"""
    if given is None:
        p = folder / default
        return p if (p.is_dir() if is_dir else p.is_file()) else None
    if not text_of(given):
        out.append(problem("error", where, "a path relative to the extension folder", f'"{default}"'))
        return None
    p = Path(given) if Path(given).is_absolute() else folder / given
    if not (p.is_dir() if is_dir else p.is_file()):
        out.append(problem("warning", where, f"there is no {p}", "make it, or leave the field out"))
        return None
    return p


def check_pass(names, where, out):
    """the manifest's pass as the host uses it: the names of the player's environment variables the process also gets"""
    if names is None:
        return []
    example = '["HF_HOME", "HTTPS_PROXY"]'
    if not isinstance(names, list):
        out.append(problem("error", where, "pass is a list of the names of environment variables", example))
        return []
    if len(names) > PASS_MOST:
        out.append(problem("error", where, f"at most {PASS_MOST} names",
                           "name only the variables it needs; a fixed value goes in env"))
        return []
    kept, seen = [], set()
    for i, name in enumerate(names):
        if not isinstance(name, str) or not ENV_NAME.match(name):
            out.append(problem("error", f"{where}[{i}]", f"{name!r} is not the name of an environment variable",
                               "a name such as \"HF_HOME\": letters, digits and _, no value (a value goes in env)"))
            continue
        if name.upper() not in seen:                 # Windows reads the names without case
            seen.add(name.upper())
            kept.append(name)
    return kept


def check_apps(apps, where, out):
    """the manifest's apps: kit.app.declare's fields ({app, title, ...}) with the workshop's two, reactions (checked,
    [] when none) and the controls' call; the bad ones are problems"""
    if apps is None:
        return []
    if not isinstance(apps, list):
        out.append(problem("error", where, "apps is a list of app declarations",
                           '[{"app": "companion", "title": "语音搭子", "prompt": "companion", "hear": [...]}]'))
        return []
    kept, seen = [], set()
    for i, a in enumerate(apps):
        at = f"{where}[{i}]"
        if not isinstance(a, dict):
            out.append(problem("error", at, "an app is a JSON object", "kit.app.open's fields"))
            continue
        aid = a.get("app")
        if not isinstance(aid, str) or not APP_ID.match(aid):
            out.append(problem("error", f"{at}.app", f"{aid!r} is not an app id",
                               "lowercase letters, digits and _, a letter first, at most 24 characters"))
            continue
        if aid in seen:
            out.append(problem("error", f"{at}.app", f"the app {aid} is declared twice", "one entry per app"))
            continue
        given = [k for k in NOT_IN_APP if k in a]
        if given:
            out.append(problem("error", at, f"leave out {', '.join(given)}",
                               "无限工坊 declares the app for the extension (kit.app.declare); an agent starts it by its "
                               "prompt"))
            continue
        title = text_of(a.get("title") if a.get("title") is not None else aid, TITLE_MAX)
        if title is None:
            out.append(problem("error", f"{at}.title", "a title is some text of at most 48 bytes", "\"语音搭子\""))
            continue
        if a.get("hear") is not None and (not isinstance(a["hear"], list)
                                          or not all(isinstance(h, str) and h.strip() for h in a["hear"])):
            out.append(problem("error", f"{at}.hear", "hear is a list of entries <source>:<what>",
                               '["game:combat", "route:*"]'))
            continue
        before = len(errors_in(out))
        reactions = check_reactions(a.get("reactions"), f"{at}.reactions", out)
        controls = check_controls(a.get("controls"), f"{at}.controls", out)
        if len(errors_in(out)) > before:
            continue
        seen.add(aid)
        app = dict(a, app=aid, title=title, reactions=reactions)
        if controls is not None:
            app["controls"] = controls
        kept.append(app)
    return kept


def check_reactions(reactions, where, out):
    """an app's reactions as the daemon sets them ([{hear, when, call, args, item, cooldown, note}]); a bad one is an
    error. Whether call is a capability there is, and of a kind a reaction may call, is found when it is set"""
    if reactions is None:
        return []
    example = '[{"hear": "game:combat.start", "call": "<id>.say", "args": {"pool": "combat"}, "cooldown": 30}]'
    if not isinstance(reactions, list):
        out.append(problem("error", where, "reactions is a list of what 无限工坊 does by itself while the app runs", example))
        return []
    if len(reactions) > APP_REACTIONS_MOST:
        out.append(problem("error", where, f"at most {APP_REACTIONS_MOST} reactions in an app",
                           "let one capability decide (hear a wider entry and pass the item), or leave the rest to the "
                           "agent"))
        return []
    kept = []
    for i, spec in enumerate(reactions):
        r, why = reaction_of(spec)
        if why:
            out.append(problem("error", f"{where}[{i}]", why, "such as " + example[1:-1]))
        else:
            kept.append(r)
    return kept


def reaction_of(spec):
    """(a reaction of the manifest as reactions.py would set it, None) or (None, why not). reactions.py checks a spec
    in one async step that also asks what kind of capability call is, which a manifest cannot: so its rules are
    followed here field by field, with its own patterns, limits and its check of when"""
    if not isinstance(spec, dict):
        return None, "a reaction is a JSON object"
    extra = sorted(str(k) for k in spec if k not in REACTION_FIELDS)
    if extra:
        mine = [k for k in extra if k in ("id", "owner")]
        if mine:
            return None, (f"leave out {', '.join(mine)}: 无限工坊 names the reaction and the app owns it (it is set while "
                          "the app runs)")
        return None, f"a reaction has no {', '.join(extra)}; its fields are {', '.join(REACTION_FIELDS)}"
    out = {}
    hear = spec.get("hear")
    parts = R.split(hear) if not isinstance(hear, list) else None
    if not parts:
        return None, ("hear is one entry <source>:<what> (a string, not a list), such as \"game:combat.start\", "
                      "\"route:step\" or a source of an extension on this computer (\"voice:said\")")
    out["hear"] = f"{parts[0]}:{parts[1]}"
    try:
        out["when"] = R.Reactions._check_when(spec.get("when"))
    except R.ReactionError as e:
        return None, str(e)
    call = spec.get("call")
    if not isinstance(call, str) or not R.CAP.match(call):
        return None, ("call is a whole capability id <extension>.<name>, this extension's own too (\"voice.say\"), "
                      "or the game's (\"sense.bags\")")
    out["call"] = call
    args = spec.get("args")
    if args is not None and not isinstance(args, dict):
        return None, "args is an object of the capability's arguments, such as {\"pool\": \"combat_start\"}"
    if args and len(repr(args)) > R.MAX_ARGS:
        return None, f"args are at most about {R.MAX_ARGS} bytes: keep big things in the extension and pass a name"
    out["args"] = copy.deepcopy(args) if args else None
    name = spec.get("item")
    if name is not None:
        if not isinstance(name, str) or not R.ARG.match(name):
            return None, ("item is the name of an argument (letters, digits and _): the whole item heard goes as that "
                          "argument")
        if args and name in args:
            return None, f"item names the argument {name}, which args gives too: name another, or leave it out of args"
    out["item"] = name
    cooldown = spec.get("cooldown", R.DEFAULT_COOLDOWN)
    if cooldown is None:
        cooldown = R.DEFAULT_COOLDOWN
    if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) or not 0 <= cooldown <= R.MAX_COOLDOWN:
        return None, (f"cooldown is seconds between two calls of this reaction, 0 to {R.MAX_COOLDOWN} (default "
                      f"{R.DEFAULT_COOLDOWN}; 0: every item that matches calls)")
    out["cooldown"] = cooldown
    note = spec.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > R.MAX_NOTE):
        return None, f"note is a line of text (at most {R.MAX_NOTE} characters) saying what the reaction is for"
    out["note"] = " ".join(note.split()) if note else None
    return out, None


def check_controls(controls, where, out):
    """an app's controls as written (None: it gives none): WuxianKit's own words as strings, the app's own as {cmd,
    label}; either may be an object with call, the name of a capability of this extension. WuxianKit checks the words
    and labels when the app is declared; here only what the workshop reads"""
    if controls is None:
        return None
    example = '["stop", {"cmd": "mute", "call": "mute"}, {"cmd": "again", "label": "再说一遍", "call": "again"}]'
    if not isinstance(controls, list) or len(controls) > CONTROLS_MOST:
        out.append(problem("error", where, f"controls is a list of at most {CONTROLS_MOST}", example))
        return None
    kept, seen = [], set()
    for i, c in enumerate(controls):
        at = f"{where}[{i}]"
        if isinstance(c, str):
            if c not in CORE_CONTROLS:
                out.append(problem("error", at, f"{c!r} is not one of {', '.join(CORE_CONTROLS)}",
                                   "a control of the app's own is an object {\"cmd\", \"label\"}"))
                continue
            cmd, entry = c, c
        elif isinstance(c, dict):
            for name in sorted(str(k) for k in c if k not in CONTROL_FIELDS):
                out.append(problem("warning", f"{at}.{name}", "this field means nothing in a control",
                                   "the fields: " + ", ".join(CONTROL_FIELDS)))
            cmd = c.get("cmd")
            if not isinstance(cmd, str) or not CONTROL_CMD.match(cmd):
                out.append(problem("error", f"{at}.cmd", f"{cmd!r} is not a control's word",
                                   "lowercase letters, digits and _, a letter first, at most 24 characters"))
                continue
            entry = {"cmd": cmd}
            label = c.get("label")
            if cmd in CORE_CONTROLS:
                if label is not None:
                    out.append(problem("warning", f"{at}.label", f"{cmd} is WuxianKit's own control: it keeps its own "
                                                                 "label", "leave label out, or choose another cmd"))
            else:
                label = text_of(label)
                if label is None or " " in label:
                    out.append(problem("error", f"{at}.label", "a control of the app's own needs a label",
                                       "the button's text, one word without spaces (/wk <app> <label> gives it)"))
                    continue
                entry["label"] = label
            call = c.get("call")
            if call is not None:
                if cap_name_problem(call):
                    out.append(problem("error", f"{at}.call", f"{call!r} is no capability name",
                                       "the name of a capability of this extension, without its id (\"mute\"): 无限工坊 "
                                       "calls it when the player gives the control"))
                    continue
                entry["call"] = call
        else:
            out.append(problem("error", at, "a control is a word or an object {cmd, label, call}", example))
            continue
        if cmd in seen:
            out.append(problem("error", at, f"the control {cmd} is there twice", "one entry per control"))
            continue
        seen.add(cmd)
        kept.append(entry)
    return kept


def check_adds(adds, eid, folder, where, out):
    """the manifest's adds as the host uses it ({app: {wake: {call, timeout} | None, skill: absolute path | None}});
    the bad ones are problems. Whether wake is a see capability the extension has waits for its description
    (adds_problems). A skill file that is not there or is no UTF-8 is a warning (kept: the skill then says why it is
    missing); a path that leaves the folder an error"""
    if adds is None:
        return {}
    example = '{"guild": {"wake": "lookup", "skill": "skills/guild-chat.md"}}'
    if not isinstance(adds, dict):
        out.append(problem("error", where, "adds is an object, app id -> what the extension adds to that app", example))
        return {}
    if len(adds) > ADDS_MOST:
        out.append(problem("error", where, f"at most {ADDS_MOST} apps", "one entry per app it adds to"))
        return {}
    own = eid if isinstance(eid, str) and ID.match(eid) else None
    base = Path(folder).resolve()
    kept = {}
    for app, a in sorted(adds.items()):
        at = f"{where}.{app}"
        if not KIT_APP_ID.match(app):
            out.append(problem("error", at, f"{app!r} is not an app id",
                               "one app by its id (lowercase letters, digits and _, at most 24 characters), such as "
                               "\"guild\"; no wildcards"))
            continue
        if not isinstance(a, dict) or not (a.get("wake") is not None or a.get("skill") is not None):
            out.append(problem("error", at, "what it adds to the app is an object with wake, skill or both",
                               example[1:-1].partition(": ")[2]))
            continue
        for name in sorted(str(k) for k in a if k not in ADDS_FIELDS):
            out.append(problem("warning", f"{at}.{name}", "this field means nothing to 无限工坊",
                               "the fields: " + ", ".join(ADDS_FIELDS)))
        before = len(errors_in(out))
        wake = check_wake(a.get("wake"), own, f"{at}.wake", out)
        skill = check_skill(a.get("skill"), base, f"{at}.skill", out)
        if len(errors_in(out)) == before:
            kept[app] = dict(wake=wake, skill=skill)
    return kept


def check_wake(wake, own, where, out):
    """an adds entry's wake as the host uses it: {call: "<id>.<name>", timeout} or None"""
    if wake is None:
        return None
    hint = ("the name of a see capability of this extension, without its id (\"lookup\"), or {\"call\": \"lookup\", "
            "\"timeout\": 5}: it gets {wake} and answers the facts to note on it")
    timeout = WAKE_TIMEOUT
    name = wake
    if isinstance(wake, dict):
        for k in sorted(str(k) for k in wake if k not in WAKE_FIELDS):
            out.append(problem("warning", f"{where}.{k}", "this field means nothing to 无限工坊",
                               "the fields: " + ", ".join(WAKE_FIELDS)))
        name = wake.get("call")
        if wake.get("timeout") is not None:
            timeout = wake["timeout"]
    if cap_name_problem(name):
        out.append(problem("error", where, f"{name!r} is no capability name", hint))
        return None
    if not finite(timeout) or not 0 < timeout <= WAKE_TIMEOUT_MOST:
        out.append(problem("error", f"{where}.timeout", f"timeout is seconds, more than 0 and at most "
                                                        f"{WAKE_TIMEOUT_MOST:g}",
                           "the wake waits that long for the note; leave it out for 3"))
        return None
    return dict(call=f"{own or '?'}.{name}", timeout=float(timeout))


def check_skill(rel, base, where, out):
    """an adds entry's skill as the host uses it: the absolute path of a file in the extension's folder, or None"""
    if rel is None:
        return None
    if not text_of(rel) or Path(rel).is_absolute() or Path(rel).drive:
        out.append(problem("error", where, "the file is a path relative to the extension folder",
                           '"skills/guild-chat.md"'))
        return None
    p = (base / rel).resolve()
    if base not in p.parents:
        out.append(problem("error", where, f"{rel} is not in the extension folder",
                           "keep the supplement in the extension's own folder, such as skills/<app>.md"))
        return None
    if p.is_dir():
        out.append(problem("error", where, f"{p} is a folder", "name a Markdown file"))
        return None
    try:
        p.read_bytes().decode("utf-8-sig")
    except FileNotFoundError:
        out.append(problem("warning", where, f"there is no {p}", "write it, or leave skill out"))
    except UnicodeDecodeError:
        out.append(problem("warning", where, f"{p} is not UTF-8", "save it as UTF-8"))
    except OSError as e:
        out.append(problem("warning", where, f"{p} cannot be read: {e.strerror or e}", "check the file"))
    return str(p)


def named_capabilities(manifest):
    """[(where, capability name, what)] of the capabilities of its own a manifest names: what is "wake" (adds),
    "control" (an app's control) or "reaction" (an app's reaction that calls this extension)"""
    eid = (manifest or {}).get("id") or ""
    out = []
    for app, a in sorted(((manifest or {}).get("adds") or {}).items()):
        if a.get("wake"):
            out.append((f"{MANIFEST}: adds.{app}.wake", a["wake"]["call"][len(eid) + 1:], "wake"))
    for i, a in enumerate((manifest or {}).get("apps") or []):
        for j, c in enumerate(a.get("controls") or []):
            if isinstance(c, dict) and c.get("call"):
                out.append((f"{MANIFEST}: apps[{i}].controls[{j}].call", c["call"], "control"))
        for j, r in enumerate(a.get("reactions") or []):
            if r["call"].startswith(eid + "."):
                out.append((f"{MANIFEST}: apps[{i}].reactions[{j}].call", r["call"][len(eid) + 1:], "reaction"))
    return out


def adds_problems(manifest, described):
    """the problems of the capabilities of its own a manifest names (adds' wake, the controls' call, the reactions that
    call it) against what the extension last said of itself (described: its description {capabilities: [{name,
    kind}]}, or None): each one it has, a wake a see capability. Before it described itself: one warning"""
    named = named_capabilities(manifest)
    if not named:
        return []
    eid = manifest["id"]
    if described is None:
        return [problem("warning", MANIFEST, f"the capabilities it names ({', '.join(sorted({n for _, n, _ in named}))}) "
                                             f"are not checked yet: {eid} has not described itself",
                        "extension_load starts it; its capabilities are checked then")]
    caps = {c.get("name"): c for c in described.get("capabilities") or [] if isinstance(c, dict)}
    have = ", ".join(sorted(n for n in caps if isinstance(n, str))) or "none"
    out = []
    for where, name, what in named:
        cap = caps.get(name)
        if cap is None:
            out.append(problem("error", where, f"{eid} has no capability {eid}.{name} (it has: {have})",
                               "declare it, or fix the name: the capability's name" +
                               (" without the extension's id" if what != "reaction" else "")))
        elif what == "wake" and cap.get("kind") != "see":
            out.append(problem("error", where, f"{eid}.{name} is a {cap.get('kind')} capability: a wake's note only "
                                               "reads", "make it a see capability (it answers facts and changes nothing)"))
        elif what == "wake" and "wake" not in (cap.get("args") or {}):
            out.append(problem("warning", where, f"{eid}.{name} takes no argument wake: the wake goes in it",
                               "declare \"wake\": \"table\" in its args"))
    return out


annotate_problems = adds_problems      # the name host.py and check.py call until they are moved over


# --- the description: initialize's answer ----------------------------------------------------------------------------

def check_description(eid, answer):
    """(description or None, problems) of an extension's answer to initialize: {capabilities: [{id, name, kind,
    title, doc, args, timeout}], sources: [{name, title, doc}], options: [opt]}"""
    out = []
    where = "initialize"
    if not isinstance(answer, dict):
        return None, [problem("error", where, "initialize must answer a JSON object",
                              "{protocol: 1, capabilities: [...], sources: [...], options: [...]}")]
    if answer.get("protocol") != PROTOCOL or isinstance(answer.get("protocol"), bool):
        out.append(problem("error", f"{where}: protocol", f"protocol {answer.get('protocol')!r}: this 无限工坊 speaks 1",
                           "answer protocol 1 (the Python library does)"))
    caps, names = [], set()
    raw_caps = answer.get("capabilities", [])
    if not isinstance(raw_caps, list):
        out.append(problem("error", f"{where}: capabilities", "capabilities is a list", "[{name, kind, title, doc, args}]"))
        raw_caps = []
    for i, c in enumerate(raw_caps):
        cap = check_capability(eid, c, i, names, out)
        if cap is not None:
            caps.append(cap)
            names.add(cap["name"])
    sources, snames = [], set()
    raw_sources = answer.get("sources", [])
    if not isinstance(raw_sources, list):
        out.append(problem("error", f"{where}: sources", "sources is a list", "[{name, title, doc}]"))
        raw_sources = []
    for i, s in enumerate(raw_sources):
        at = f"source {i + 1}"
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not SOURCE_NAME.match(s["name"]):
            out.append(problem("error", at, f"{(s or {}).get('name') if isinstance(s, dict) else s!r} is no source name",
                               SOURCE_NAME_HINT))
            continue
        at = f"source {eid}:{s['name']}"
        if s["name"] in snames:
            out.append(problem("error", at, "declared twice", "one entry per source"))
            continue
        title = text_of(s.get("title") if s.get("title") is not None else s["name"], TITLE_MAX)
        if title is None:
            out.append(problem("error", at, "its title is some text of at most 48 bytes", "\"说完一句\""))
            continue
        doc = s.get("doc") if isinstance(s.get("doc"), str) else ""
        if not doc.strip():
            out.append(problem("warning", at, "it has no doc", "say what an item of it means and what its data holds"))
        snames.add(s["name"])
        sources.append(dict(name=s["name"], title=title, doc=doc.strip()))
    options, onames = [], set()
    raw_opts = answer.get("options", [])
    if not isinstance(raw_opts, list):
        out.append(problem("error", f"{where}: options", "options is a list", "[{name, kind, title, default}]"))
        raw_opts = []
    for i, o in enumerate(raw_opts):
        opt = check_option(o, i, onames, out)
        if opt is not None:
            options.append(opt)
            onames.add(opt["name"])
    desc = dict(capabilities=caps, sources=sources, options=options)
    return (None if errors_in(out) else desc), out


def check_capability(eid, c, i, names, out):
    at = f"capability {i + 1}"
    if not isinstance(c, dict):
        out.append(problem("error", at, "a capability is a JSON object", "{name, kind, title, doc, args}"))
        return None
    name = c.get("name")
    why = cap_name_problem(name, eid)
    if why:
        out.append(problem("error", at, why, CAP_NAME_HINT + f"; its tool wk_{eid}_<name> has at most {TOOL_NAME_MAX} "
                                                              "characters"))
        return None
    at = f"capability {eid}.{name}"
    if name in names:
        out.append(problem("error", at, "declared twice", "one entry per capability"))
        return None
    same = sorted(n for n in names if tool_name(eid, n) == tool_name(eid, name))
    if same:
        out.append(problem("error", at, f"its tool {tool_name(eid, name)} is also {eid}.{same[0]}'s",
                           "a tool's name has _ for the dots, so a.b and a_b are one tool: rename one of them"))
        return None
    kind = c.get("kind")
    if kind in LATER_KINDS:
        out.append(problem("error", at, f"kind {kind} (bad_capability): say and do are proposals the player confirms, "
                                        "not available to local extensions yet",
                           "see: reads; keep: stores on this computer; point: shows or tells the player something"))
        return None
    if kind not in KINDS:
        out.append(problem("error", at, f"kind {kind!r} (bad_capability)",
                           "see (reads, changes nothing), keep (stores on this computer) or point (shows or tells the "
                           "player something)"))
        return None
    title = text_of(c.get("title") if c.get("title") is not None else name, TITLE_MAX)
    if title is None:
        out.append(problem("error", at, "its title is some text of at most 48 bytes", "\"说一句\""))
        return None
    doc = c.get("doc") if isinstance(c.get("doc"), str) else ""
    if not doc.strip():
        out.append(problem("warning", at, "it has no doc",
                           "the agent picks tools by their docs: say what it does, what it answers, when to use it"))
    args = c.get("args") or {}
    if not isinstance(args, dict):
        out.append(problem("error", f"{at}: args", "args is an object, name -> spec", '{"text": "string", "n": "number?"}'))
        return None
    for aname, spec in args.items():
        if not ARG_NAME.match(aname):
            out.append(problem("error", f"{at}: args.{aname}", "not an argument name", ARG_NAME_HINT))
            return None
        bad, warn = arg_problem(spec)
        if bad:
            out.append(problem("error", f"{at}: args.{aname}", bad, SPEC_HINT))
            return None
        if warn:
            out.append(problem("warning", f"{at}: args.{aname}", warn, "write either types or words"))
    timeout = c.get("timeout", TIMEOUT_DEFAULT)
    if timeout is None:
        timeout = TIMEOUT_DEFAULT
    why = timeout_problem(timeout)
    if why:
        out.append(problem("error", f"{at}: timeout", why,
                           "answer within 30 s; split long work into a capability that starts it and a source that "
                           "tells when it is done"))
        return None
    return dict(id=f"{eid}.{name}", name=name, kind=kind, title=title, doc=doc.strip(), args=dict(args),
                timeout=timeout)


def check_option(o, i, names, out):
    at = f"option {i + 1}"
    if not isinstance(o, dict) or not isinstance(o.get("name"), str) or not OPTION_NAME.match(o["name"]):
        out.append(problem("error", at, "an option needs a name", OPTION_NAME_HINT))
        return None
    name = o["name"]
    at = f"option {name}"
    if name in names:
        out.append(problem("error", at, "declared twice", "one entry per option"))
        return None
    kind = o.get("kind", "boolean")
    if kind not in OPTION_KINDS:
        out.append(problem("error", at, f"kind {kind!r}", "boolean, text, number or choice"))
        return None
    title = text_of(o.get("title") if o.get("title") is not None else name, TITLE_MAX)
    if title is None:
        out.append(problem("error", at, "its title is some text of at most 48 bytes", "\"音量\""))
        return None
    opt = dict(name=name, kind=kind, title=title)
    if isinstance(o.get("hint"), str) and o["hint"].strip():
        opt["hint"] = o["hint"].strip()
    if kind == "number":
        for k in ("min", "max", "step"):
            if o.get(k) is not None:
                if not finite(o[k]):
                    out.append(problem("error", f"{at}: {k}", f"{k} is a number", "such as 0, 100, 1"))
                    return None
                opt[k] = o[k]
        if "min" in opt and "max" in opt and opt["min"] > opt["max"]:
            out.append(problem("error", at, "min is more than max", "swap them"))
            return None
        if isinstance(o.get("unit"), str) and o["unit"].strip():
            opt["unit"] = o["unit"].strip()
    if kind == "choice":
        choices = o.get("choices")
        if not isinstance(choices, list) or not choices:
            out.append(problem("error", f"{at}: choices", "a choice option lists its choices",
                               '[{"value": "fast", "label": "快"}, {"value": "slow", "label": "慢"}]'))
            return None
        kept = []
        for ch in choices:
            if not isinstance(ch, dict) or not (isinstance(ch.get("value"), str) or finite(ch.get("value"))):
                out.append(problem("error", f"{at}: choices", "each choice is {value, label}", "value a text or a number"))
                return None
            kept.append(dict(value=ch["value"], label=text_of(ch.get("label"), TITLE_MAX) or str(ch["value"])))
        opt["choices"] = kept
    default = o.get("default", DEFAULTS[kind](opt))
    why = value_problem(opt, default)
    if why:
        out.append(problem("error", f"{at}: default", why, "the default must be a value the option takes"))
        return None
    opt["default"] = default
    return opt
