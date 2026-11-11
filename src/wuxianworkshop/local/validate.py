r"""The checks that the workshop and the SDK share (names, arguments against their specs, option values, and what an
extension says of itself: check_description and its parts, which the SDK runs on every declaration): one module, written
to run alone, because its text is put into the single-file SDK (sdk/python/wuxiankit.py) and a test compares the two.
Standard library only, no imports from this package.

The text between the two marker lines below is the shared part: scripts/sync_sdk_validate.py copies it into the SDK
(between the SDK's own two marker lines), and tests/sdk/test_validate_sync.py fails when the two differ. Change it
here, then run the script. It needs json, math and re, which both files import.
"""
import json
import math
import re

# ---- shared: begin ---------------------------------------------------------------------------------------------------
CAP_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*\Z")     # say, pool.list: dotted parts, a letter first each
CAP_NAME_MAX = 48
TOOL_NAME_MAX = 48                     # the agent's tool wk_<extension id>_<name, dots as _>
SOURCE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,23}\Z")
OPTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")
ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,47}\Z")
KINDS = ("see", "keep", "point")       # Read, Store, Guide; say / do (proposals) come later
OPTION_KINDS = ("boolean", "text", "number", "choice")
TYPES = ("string", "number", "boolean", "table", "any")
TIMEOUT_DEFAULT, TIMEOUT_MOST = 30, 600      # seconds the workshop waits for a capability
TEXT_MOST = 2000                       # characters of a text option's value
TITLE_MAX = 48                         # bytes, as WuxianKit takes a title
CAP_NAME_HINT = "lowercase letters, digits, _ and dots, a letter first in every part (say, pool.list)"
SOURCE_NAME_HINT = "lowercase letters, digits and _, a letter first, at most 24 characters (said)"
OPTION_NAME_HINT = "lowercase letters, digits and _, a letter first, at most 32 characters"
ARG_NAME_HINT = "letters, digits and _, no digit first, at most 48 characters"
SPEC_HINT = ("string number boolean table any; a|b for either; words that are no type names are the values it may "
             "take; a trailing ? makes it optional")


def text_of(value, most=None):
    """a one-line text, stripped; None when it is no text (or longer than `most` bytes of UTF-8)"""
    if not isinstance(value, str) or not value.strip():
        return None
    value = " ".join(value.split())
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:             # half of a surrogate pair ("\ud800"): nothing that can be written or shown
        return None
    return value if most is None or size <= most else None


def finite(v):
    try:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    except OverflowError:                  # an integer no float holds
        return False


def tool_name(eid, name):
    """the agent's tool of the capability `name` of the extension `eid`"""
    return f"wk_{eid}_{name.replace('.', '_')}"


def cap_name_problem(name, eid=None):
    """why `name` is no capability name (None: it is one); with the extension's id, also that its tool name fits"""
    if not isinstance(name, str) or not CAP_NAME.match(name) or len(name) > CAP_NAME_MAX:
        return f"{name!r} is no capability name"
    if isinstance(eid, str) and len(tool_name(eid, name)) > TOOL_NAME_MAX:
        return (f"{name!r} is too long a capability name: its tool {tool_name(eid, name)} has more than {TOOL_NAME_MAX} "
                "characters")
    return None


def parse_spec(text):
    """an argument's spec, as WuxianKit writes them: "string", "number", "boolean", "table" (a JSON object or array) or
    "any", several of those ("string|number"), or else the strings it may be ("fast|slow"); a "?" after it makes it
    optional. -> {text, optional, types: [...]} or {text, optional, enum: [...]}; ValueError for a bad spec"""
    if not isinstance(text, str) or not text.strip() or text == "?":
        raise ValueError(f"an argument's spec is a string such as \"string\" or \"number?\", not {text!r}")
    optional = text.endswith("?")
    parts = (text[:-1] if optional else text).split("|")
    if any(not p or p.strip() != p for p in parts):
        raise ValueError(f"{text!r}: the parts between | are names, without spaces, none empty")
    if all(p in TYPES for p in parts):
        return {"text": text, "optional": optional, "types": parts}
    return {"text": text, "optional": optional, "enum": parts}


def arg_problem(spec):
    """why an argument's spec cannot be read (None: it can), and a warning (or None)"""
    try:
        parsed = parse_spec(spec)
    except ValueError as e:
        return str(e), None
    if "enum" in parsed and any(p in TYPES for p in parsed["enum"]):
        return None, (f"{spec!r} mixes types and words: it is read as an enumeration of the strings "
                      f"{', '.join(parsed['enum'])}")
    return None, None


def json_type(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (dict, list)):
        return "table"
    return type(v).__name__


def args_text(specs):
    return ", ".join(f"{n} ({s['text']})" for n, s in sorted(specs.items())) or "no arguments"


def args_problem(cap_id, specs, args):
    """what a call brought, checked against the capability's parsed specs ({name: parse_spec(...)}) the way WuxianKit
    checks them -> (a copy of the declared ones that came, None), or (None, why not): a missing one, a wrong type, a
    string not among the choices, one that is not declared. null counts as left out"""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None, f"{cap_id}: args is a JSON object"
    out = {}
    for name, spec in specs.items():
        v = args.get(name)
        if v is None:
            if not spec["optional"]:
                return None, f"{cap_id}: {name} is missing ({spec['text']})"
            continue
        got = json_type(v)
        if "types" in spec:
            if "any" not in spec["types"] and got not in spec["types"]:
                return None, f"{cap_id}: {name} is a {' or '.join(spec['types'])}, not a {got}"
        elif not isinstance(v, str) or v not in spec["enum"]:
            return None, (f"{cap_id}: {name} is one of {'|'.join(spec['enum'])}, not "
                          f"{json.dumps(v, ensure_ascii=False, default=str)}")
        out[name] = v
    for name, v in args.items():
        if name not in specs and v is not None:
            return None, f"{cap_id}: no argument {name} (it takes {args_text(specs)})"
    return out, None


def timeout_problem(timeout):
    """why `timeout` is not the seconds a capability may take (None: it is)"""
    if not finite(timeout) or not 0 < timeout <= TIMEOUT_MOST:
        return f"timeout is seconds, more than 0 and at most {TIMEOUT_MOST}"
    return None


DEFAULTS = {"boolean": lambda o: False, "text": lambda o: "", "number": lambda o: o.get("min", 0),
            "choice": lambda o: o["choices"][0]["value"] if o.get("choices") else None}


def value_problem(opt, value):
    """why value is not one the option takes (None: it is); opt = {kind, min?, max?, choices?: [{value}]}"""
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


# --- the description: initialize's answer. Every finding is a problem {level: "error" | "warning", where, message, hint}

PROTOCOL = 1                           # the protocol version a description is written in
LATER_KINDS = ("say", "do")            # proposals: a later step


def problem(level, where, message, hint=""):
    return dict(level=level, where=where, message=message, hint=hint)


def errors_in(problems):
    return [p for p in problems if p["level"] == "error"]


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
        src = check_source(eid, s, i, snames, out)
        if src is not None:
            sources.append(src)
            snames.add(src["name"])
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


def check_source(eid, s, i, names, out):
    at = f"source {i + 1}"
    if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not SOURCE_NAME.match(s["name"]):
        out.append(problem("error", at, f"{(s or {}).get('name') if isinstance(s, dict) else s!r} is no source name",
                           SOURCE_NAME_HINT))
        return None
    at = f"source {eid}:{s['name']}"
    if s["name"] in names:
        out.append(problem("error", at, "declared twice", "one entry per source"))
        return None
    title = text_of(s.get("title") if s.get("title") is not None else s["name"], TITLE_MAX)
    if title is None:
        out.append(problem("error", at, "its title is some text of at most 48 bytes", "\"说完一句\""))
        return None
    doc = s.get("doc") if isinstance(s.get("doc"), str) else ""
    if not doc.strip():
        out.append(problem("warning", at, "it has no doc", "say what an item of it means and what its data holds"))
    return dict(name=s["name"], title=title, doc=doc.strip())


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
# ---- shared: end -----------------------------------------------------------------------------------------------------
