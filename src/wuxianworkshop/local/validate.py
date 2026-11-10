r"""The checks of values that the workshop and the SDK share (names, arguments against their specs, option values):
one module, written to run alone, because its text is put into the single-file SDK (sdk/python/wuxiankit.py) and a test
compares the two. Standard library only, no imports from this package.

The text between the two marker lines below is the shared part: scripts/sync_sdk_validate.py copies it into the SDK
(between the SDK's own two marker lines), and tests/sdk/test_validate_sync.py fails when the two differ. Change it
here, then run the script. It needs json, math and re, which both files import.
"""
import json
import math
import re

# ---- shared: begin ---------------------------------------------------------------------------------------------------
CAP_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")     # say, pool.list: dotted parts, a letter first each
CAP_NAME_MAX = 48
TOOL_NAME_MAX = 48                     # the agent's tool wk_<extension id>_<name, dots as _>
SOURCE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
OPTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,47}$")
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
    return value if most is None or len(value.encode("utf-8")) <= most else None


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


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
# ---- shared: end -----------------------------------------------------------------------------------------------------
