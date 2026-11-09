r"""A local extension's manifest, wuxian-extension.json at the root of its folder, and the checks of what the extension
says of itself when it starts (its answer to initialize). Every finding is a problem {level: "error" | "warning", where,
message, hint}: where names the file and field (or the capability), message says what is wrong, hint how to fix it; the
words are for the agent that writes the extension.

    {"id": "voice", "title": "声音", "version": "0.1.0", "notes": "...",
     "run": ["python", "-m", "voice"], "python": "tts/.venv/Scripts/python.exe", "env": {"K": "V"},
     "start": "use" | "workshop", "docs": "AGENT.md", "skills": "skills", "apps": [{"app": "companion", ...}]}

id: ^[a-z][a-z0-9_]{0,23}$, not a WuxianKit extension's (chat game route sense guide data tune gate codex guides) and not
local, wb or wk. title: at most 48 bytes. run: the argv, relative to the folder; a first item "python" is the manifest's
`python` (relative to the folder), else the workshop's own interpreter. start: use (the default: started by the first
call of one of its capabilities) or workshop (started with the daemon: a model to warm up). docs: default AGENT.md when
there is one; skills: default skills/ when there is one. apps: kit.app.open's fields without temp, driver and launch; the
daemon declares them (kit.app.declare) after every kit.ready.

The description (initialize's answer): {protocol: 1, capabilities: [cap], sources: [src], options: [opt]};
cap = {name, kind: see | keep | point, title, doc, args: {name: spec}, timeout?: seconds (default 30, at most 600)}; an
argument's spec as WuxianKit's capabilities write it (mcp/kit.py schema()): string number boolean table any, a | b for
either, words that are no type names an enumeration, a trailing ? optional. src = {name, title, doc};
opt = {name, kind: boolean | text | number | choice, title, default, hint?, choices?: [{value, label}], min?, max?,
step?, unit?}. say and do (proposals the player confirms) come in a later step: refused now (bad_capability).
Standard library only.
"""
import json
import math
import re
from pathlib import Path

MANIFEST = "wuxian-extension.json"
PROTOCOL = 1
ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
APP_ID = ID
KIT_EXTENSIONS = ("chat", "game", "route", "sense", "guide", "data", "tune", "gate", "codex", "guides")
RESERVED = KIT_EXTENSIONS + ("local", "wb", "wk", "kit")
TITLE_MAX = 48                         # bytes, as WuxianKit takes a title
NOTES_MAX = 400
CAP_NAME = re.compile(r"^[a-z][a-z0-9_.]*$")
CAP_NAME_MAX = 48
SOURCE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
OPTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,47}$")
KINDS = ("see", "keep", "point")
LATER_KINDS = ("say", "do")            # proposals: a later step
OPTION_KINDS = ("boolean", "text", "number", "choice")
TYPES = ("string", "number", "boolean", "table", "any")
TIMEOUT_DEFAULT, TIMEOUT_MOST = 30, 600
TEXT_MOST = 2000                       # characters of a text option's value
NOT_IN_APP = ("temp", "driver", "launch")
STARTS = ("use", "workshop")


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


def text_of(value, most=None):
    """a one-line text, stripped; None when it is no text"""
    if not isinstance(value, str) or not value.strip():
        return None
    value = " ".join(value.split())
    return value if most is None or len(value.encode("utf-8")) <= most else None


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
    {id, title, version, notes, run, python, env, start, docs, skills, apps, folder, file}; docs and skills absolute
    paths or None, python an absolute path or None"""
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

    known = {"id", "title", "version", "notes", "run", "python", "env", "start", "docs", "skills", "apps"}
    for name in sorted(set(d) - known):
        out.append(problem("warning", field(name), "this field means nothing to 无限工坊",
                           "the fields: " + ", ".join(sorted(known))))
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
    manifest = dict(id=eid, title=title, version=text_of(version, 32) if version is not None else None,
                    notes=text_of(notes, NOTES_MAX) if notes is not None else None, run=list(run or []),
                    python=str(python_path) if python_path is not None else None, env=dict(env), start=start,
                    docs=str(docs) if docs else None, skills=str(skills) if skills else None, apps=apps,
                    folder=str(folder), file=str(file))
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


def check_apps(apps, where, out):
    """the manifest's apps as kit.app.declare takes them ({app, title, ...}); the bad ones are problems"""
    if apps is None:
        return []
    if not isinstance(apps, list):
        out.append(problem("error", where, "apps is a list of app declarations",
                           '[{"app": "companion", "title": "无限陪玩", "prompt": "companion", "hear": [...]}]'))
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
            out.append(problem("error", f"{at}.title", "a title is some text of at most 48 bytes", "\"无限陪玩\""))
            continue
        if a.get("hear") is not None and (not isinstance(a["hear"], list)
                                          or not all(isinstance(h, str) and h.strip() for h in a["hear"])):
            out.append(problem("error", f"{at}.hear", "hear is a list of entries <source>:<what>",
                               '["game:combat", "route:*"]'))
            continue
        seen.add(aid)
        kept.append(dict(a, app=aid, title=title))
    return kept


# --- the description: initialize's answer ----------------------------------------------------------------------------

def arg_problem(spec):
    """why an argument's spec cannot be read (None: it can), and a warning (or None)"""
    if not isinstance(spec, str) or not spec.strip():
        return "the spec is a text such as \"string\", \"number?\" or \"fast|slow\"", None
    body = spec[:-1] if spec.endswith("?") else spec
    parts = body.split("|")
    if not all(p and p == p.strip() for p in parts):
        return f"{spec!r}: an empty or padded part", None
    types = [p for p in parts if p in TYPES]
    if types and len(types) != len(parts):
        return None, (f"{spec!r} mixes types and words: it is read as an enumeration of the strings "
                      f"{', '.join(parts)}")
    return None, None


def check_description(eid, answer):
    """(description or None, problems) of an extension's answer to initialize: {capabilities: [{id, name, kind,
    title, doc, args, timeout}], sources: [{name, title, doc}], options: [opt]}"""
    out = []
    where = "initialize"
    if not isinstance(answer, dict):
        return None, [problem("error", where, "initialize must answer a JSON object",
                              "{protocol: 1, capabilities: [...], sources: [...], options: [...]}")]
    if answer.get("protocol") != PROTOCOL:
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
                               "lowercase letters, digits and _, a letter first, at most 24 characters (said)"))
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
    if not isinstance(name, str) or not CAP_NAME.match(name) or len(name) > CAP_NAME_MAX:
        out.append(problem("error", at, f"{name!r} is no capability name",
                           "lowercase letters, digits, _ and dots, a letter first (say, pool.list)"))
        return None
    at = f"capability {eid}.{name}"
    if name in names:
        out.append(problem("error", at, "declared twice", "one entry per capability"))
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
            out.append(problem("error", f"{at}: args.{aname}", "not an argument name", "letters, digits and _"))
            return None
        bad, warn = arg_problem(spec)
        if bad:
            out.append(problem("error", f"{at}: args.{aname}", bad,
                               "string number boolean table any; a|b for either; words that are no type names are the "
                               "values it may take; a trailing ? makes it optional"))
            return None
        if warn:
            out.append(problem("warning", f"{at}: args.{aname}", warn, "write either types or words"))
    timeout = c.get("timeout", TIMEOUT_DEFAULT)
    if timeout is None:
        timeout = TIMEOUT_DEFAULT
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout)
            or not 0 < timeout <= TIMEOUT_MOST):
        out.append(problem("error", f"{at}: timeout", f"timeout is seconds, at most {TIMEOUT_MOST}",
                           "answer within 30 s; split long work into a capability that starts it and a source that "
                           "tells when it is done"))
        return None
    return dict(id=f"{eid}.{name}", name=name, kind=kind, title=title, doc=doc.strip(), args=dict(args),
                timeout=timeout)


def check_option(o, i, names, out):
    at = f"option {i + 1}"
    if not isinstance(o, dict) or not isinstance(o.get("name"), str) or not OPTION_NAME.match(o["name"]):
        out.append(problem("error", at, "an option needs a name",
                           "lowercase letters, digits and _, a letter first, at most 32 characters"))
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


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


DEFAULTS = {"boolean": lambda o: False, "text": lambda o: "", "number": lambda o: o.get("min", 0),
            "choice": lambda o: o["choices"][0]["value"] if o.get("choices") else None}


def value_problem(opt, value):
    """why value is not one the option takes (None: it is)"""
    kind = opt["kind"]
    if kind == "boolean":
        return None if isinstance(value, bool) else "true or false"
    if kind == "text":
        if not isinstance(value, str):
            return "a text"
        return None if len(value) <= TEXT_MOST else f"at most {TEXT_MOST} characters"
    if kind == "number":
        if not finite(value):
            return "a number"
        if "min" in opt and value < opt["min"] or "max" in opt and value > opt["max"]:
            return f"a number from {opt.get('min', '-inf')} to {opt.get('max', 'inf')}"
        return None
    values = [c["value"] for c in opt.get("choices") or []]
    return None if value in values and not isinstance(value, bool) else "one of " + ", ".join(map(repr, values))
