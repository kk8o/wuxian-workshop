r"""Checks an addon's files before they go to the game (写完先检查): what would fail there is found here in milliseconds,
with the file and the line, together with what may be wrong.

    check(path, addons=None) -> report        a file or an addon folder (the files its .toc lists, and the rest)
    syntax(path) -> None or the first syntax error ({file, line, code, message, hint})
    report = {target, addon, files, ok, errors: [finding], warnings: [...], notes: [...], unresolved: {name: [where]},
              libraries}
    finding = {file, line, code, message, hint}   (file relative to the addon folder; hint: what to do, may be "")

Lua files are compiled by Lua 5.1 (lupa's lua51; the client runs "Lua 5.1.4"): a syntax error is an error ("syntax"),
with a hint where newer Lua allows what 5.1 does not (//, goto and ::labels::, the bitwise operators). The compiled
chunk's bytecode (string.dump) gives every global it reads and writes, with its line:
  - "missing-lib" (error): a library or function this client does not have (os, io, utf8, require, dofile, ...)
  - "lua52" (error): a library function newer than 5.1 (table.unpack, table.pack, table.move, math.tointeger, ...)
  - "event-unknown" (error): RegisterEvent / RegisterUnitEvent with a constant name the API manual does not know (the
    client raises an error); "event-restricted" (warning): one the client blocks addons from (HasRestrictions)
  - "typo" (warning): a global read that neither the client (Lua and WoW's libraries, the API manual, the exe's Usage
    strings, the common Blizzard UI globals in UI_GLOBALS) nor the addon (its global writes, SavedVariables, frame names
    from CreateFrame and XML) has, close to a name the client has; the others are notes ("unresolved"), which the
    daemon can ask the running game about
  - "moved" (warning): a global this client does not have whose name a C_ namespace has (GetSpellInfo: use
    C_Spell.GetSpellInfo; the old globals went in 11.0)
  - "api-unknown" (warning): C_Namespace.Name that the manual does not list for that namespace
  - "global-write" (warning): a global the addon writes that is not a SavedVariable, a SLASH_ / BINDING_ name, a frame
    name, a name starting with the addon's own (MyAddon, MyAddonDB, MyAddon_Config) or WoWBridgeNS ("forgot local?")
  - "protected" (warning): a call of a protected function (the manual's list): blocked for addons
  - "xml" (error): an XML file that is not well-formed; "toc-missing" (error): a .toc line naming a missing file;
    "toc-interface" (warning): an Interface number that is not the client's; "encoding" (warning): not UTF-8
Files in a library folder (Libs, Lib, Libraries) are compiled and give their definitions, but only their syntax errors
are reported. Without lupa the Lua checks are skipped (a note says so).
"""
import difflib
import re
import struct
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

INTERFACE = 16001

# Lua 5.1's libraries as WoW has them, and what newer Lua has that 5.1 does not
LIBS = {
    "string": {"byte", "char", "dump", "find", "format", "gfind", "gmatch", "gsub", "len", "lower", "match", "rep",
               "reverse", "sub", "upper", "join", "split", "trim"},
    "table": {"concat", "insert", "maxn", "remove", "sort", "getn", "setn", "foreach", "foreachi", "wipe"},
    "math": {"abs", "acos", "asin", "atan", "atan2", "ceil", "cos", "cosh", "deg", "exp", "floor", "fmod", "frexp", "huge",
             "ldexp", "log", "log10", "max", "min", "mod", "modf", "pi", "pow", "rad", "random", "randomseed", "sin", "sinh",
             "sqrt", "tan", "tanh"},
    "coroutine": {"create", "resume", "running", "status", "wrap", "yield", "isyieldable"},
    "bit": {"band", "bor", "bxor", "bnot", "lshift", "rshift", "arshift", "mod"},
}
NEWER = {   # (library, name) -> what to write in 5.1
    ("table", "unpack"): "unpack(t) (a global in Lua 5.1)",
    ("table", "pack"): "{ ... } and select(\"#\", ...)",
    ("table", "move"): "a loop",
    ("math", "tointeger"): "math.floor(x) (no integer type in 5.1)",
    ("math", "type"): "type(x) == \"number\"",
    ("math", "ult"): "a comparison of numbers",
    ("math", "maxinteger"): "2^53",
    ("math", "mininteger"): "-2^53",
    ("string", "pack"): "string.char / string.byte",
    ("string", "unpack"): "string.byte",
    ("string", "packsize"): "a fixed size",
}
MISSING = {  # globals the client does not have -> what to use
    "os": "the globals time(), date() and difftime() (WoW has no os library)",
    "io": "SavedVariables (addons cannot read or write files)",
    "utf8": "the string library (utf8 is Lua 5.3; WoW has strlenutf8 and strcmputf8i)",
    "require": "the .toc's file list (files run in that order and share the addon's namespace from ...)",
    "dofile": "the .toc's file list",
    "loadfile": "the .toc's file list",
    "package": "the .toc's file list",
    "module": "local tables in the addon's namespace",
    "debug": "debugstack(), debuglocals(), debugprofilestop() (WoW has no debug library)",
    "setfenv": None, "getfenv": None,    # present in 5.1: listed in BASE, not here (None: never reported)
}
BASE = {   # Lua 5.1's base functions and WoW's additions (the API manual's stdlib and globals topics)
    "assert", "collectgarbage", "error", "gcinfo", "getfenv", "getmetatable", "ipairs", "load", "loadstring", "newproxy",
    "next", "pairs", "pcall", "print", "rawequal", "rawget", "rawset", "select", "setfenv", "setmetatable", "tonumber",
    "tostring", "type", "unpack", "xpcall", "_G", "_VERSION", "string", "table", "math", "coroutine", "bit",
    "date", "time", "difftime", "debugstack", "debuglocals", "debugprofilestart", "debugprofilestop", "geterrorhandler",
    "seterrorhandler", "strsplit", "strjoin", "strconcat", "strtrim", "strlenutf8", "strcmputf8i", "tostringall", "wipe",
    "tinsert", "tremove", "getn", "format", "gsub", "strfind", "strmatch", "strsub", "strlen", "strupper", "strlower",
    "strbyte", "strchar", "strrep", "strrev", "gmatch", "floor", "ceil", "abs", "sqrt", "max", "min", "mod", "random",
    "fastrandom", "exp", "log", "log10", "frexp", "ldexp", "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "deg",
    "rad", "PI", "CopyTable", "tContains", "tInvert", "tDeleteItem", "tIndexOf", "sort", "foreach", "foreachi",
    "securecall", "securecallfunction", "secureexecuterange", "issecure", "issecurevariable", "issecretvalue",
    "canaccessvalue", "hooksecurefunc", "forceinsecure", "scrub", "getprinthandler", "setprinthandler",
}
UI_GLOBALS = {   # common globals of the Blizzard UI code (not in the API manual)
    "UIParent", "WorldFrame", "GameTooltip", "ItemRefTooltip", "ShoppingTooltip1", "ShoppingTooltip2", "DEFAULT_CHAT_FRAME",
    "SELECTED_CHAT_FRAME", "SlashCmdList", "hash_SlashCmdList", "UISpecialFrames", "UIPanelWindows", "Minimap",
    "MinimapCluster", "PlayerFrame", "TargetFrame", "FocusFrame", "PartyFrame", "PetFrame", "CompactRaidFrameContainer",
    "MainMenuBar", "MainActionBar", "ObjectiveTrackerFrame", "GameMenuFrame", "CharacterFrame", "SpellBookFrame",
    "ContainerFrameContainer", "ContainerFrameCombinedBags", "StaticPopupDialogs", "StaticPopup_Show", "StaticPopup_Hide",
    "CreateFrame", "CreateFont", "CreateFromMixins", "Mixin", "CreateAndInitFromMixin", "CreateColor", "CreateVector2D",
    "CreateObjectPool", "CreateFramePool", "CreateTexturePool", "CreateFontStringPool", "CreateDataProvider",
    "CreateScrollBoxListLinearView", "ScrollUtil", "Settings", "SettingsPanel", "AddonCompartmentFrame", "EventRegistry",
    "EventUtil", "RAID_CLASS_COLORS", "CUSTOM_CLASS_COLORS", "NORMAL_FONT_COLOR", "HIGHLIGHT_FONT_COLOR", "RED_FONT_COLOR",
    "GREEN_FONT_COLOR", "WHITE_FONT_COLOR", "YELLOW_FONT_COLOR", "GRAY_FONT_COLOR", "DISABLED_FONT_COLOR",
    "ITEM_QUALITY_COLORS", "BackdropTemplateMixin", "BACKDROP_TOOLTIP_16_16_5555", "Enum", "Constants", "SOUNDKIT",
    "NUM_BAG_SLOTS", "NUM_BANKBAGSLOTS", "NUM_CHAT_WINDOWS", "MAX_PARTY_MEMBERS", "MAX_RAID_MEMBERS", "ReloadUI",
    "InCombatLockdown", "GetTime", "GetLocale", "PlaySound", "PlaySoundFile", "GetCursorPosition", "IsShiftKeyDown",
    "IsControlKeyDown", "IsAltKeyDown", "C_Timer", "GetMouseFoci", "EnumerateFrames", "GetBuildInfo", "GetFramerate",
    "ChatFrame_AddMessageEventFilter", "ChatFrame_RemoveMessageEventFilter", "ChatFrame_OpenChat", "ShowUIPanel",
    "HideUIPanel", "ToggleFrame", "GameTooltip_SetDefaultAnchor", "SecondsToTime", "FormatLargeNumber", "GetMoneyString",
    "GetCoinTextureString", "BreakUpLargeNumbers", "AbbreviateLargeNumbers", "WOW_PROJECT_ID", "WOW_PROJECT_MAINLINE",
    "LE_EXPANSION_LEVEL_CURRENT", "UnitPopupSharedUtil", "Menu", "MenuUtil", "WoWBridgeNS", "WoWBridge",
    "GetCVar", "SetCVar", "GetCVarBool", "GetCVarDefault",
}
UI_PATTERNS = re.compile(r"^(ChatFrame\d+\w*|GameFont\w+|SystemFont\w+|NumberFont\w+|ChatFont\w+|Tooltip_\w+|\w+_FONT_COLOR"
                         r"|\w+Mixin|\w+Util|SLASH_\w+|BINDING_\w+|ERR_\w+|LE_\w+|ITEM_\w+|SPELL_\w+|[A-Z][A-Z0-9_]{3,})$")
ALLOWED_WRITES = re.compile(r"^(SLASH_\w+|BINDING_(HEADER|NAME|CATEGORY)_\w+|WoWBridgeNS)$")
LIBRARY_DIRS = {"libs", "lib", "libraries", "library"}
OP_NAMES = ("MOVE", "LOADK", "LOADBOOL", "LOADNIL", "GETUPVAL", "GETGLOBAL", "GETTABLE", "SETGLOBAL", "SETUPVAL",
            "SETTABLE", "NEWTABLE", "SELF", "ADD", "SUB", "MUL", "DIV", "MOD", "POW", "UNM", "NOT", "LEN", "CONCAT",
            "JMP", "EQ", "LT", "LE", "TEST", "TESTSET", "CALL", "TAILCALL", "RETURN", "FORLOOP", "FORPREP", "TFORLOOP",
            "SETLIST", "CLOSE", "CLOSURE", "VARARG")
OP = {name: i for i, name in enumerate(OP_NAMES)}
NO_TARGET = {OP[n] for n in ("SETGLOBAL", "SETUPVAL", "SETTABLE", "EQ", "LT", "LE", "TEST", "JMP", "RETURN", "SETLIST",
                             "CLOSE", "TAILCALL")}            # instructions that do not write R(A)
REGISTERS = ("RegisterEvent", "RegisterUnitEvent")
SYNTAX_HINTS = (    # (a part of 5.1's error, the source text near it) -> hint
    ("'/'", "//", "Lua 5.1 has no integer division (//): use math.floor(a / b)"),
    ("'goto'", "goto", "Lua 5.1 has no goto: use a flag, break, or an early return (a 'continue' can be an if around the "
                       "loop's body)"),
    ("'continue'", "goto", "Lua 5.1 has no goto: use a flag, break, or an early return"),
    ("':'", "::", "Lua 5.1 has no ::labels:: (goto is Lua 5.2)"),
    ("'&'", "&", "Lua 5.1 has no bitwise operators: bit.band(a, b)"),
    ("'|'", "|", "Lua 5.1 has no bitwise operators: bit.bor(a, b)"),
    ("'~'", "~", "Lua 5.1 has no ~ (bitwise not/xor): bit.bnot(a), bit.bxor(a, b); not-equal is ~="),
    ("'<'", "<<", "Lua 5.1 has no shift operators: bit.lshift(a, n)"),
    ("'>'", ">>", "Lua 5.1 has no shift operators: bit.rshift(a, n)"),
)


class LintError(Exception):
    pass


# --- Lua 5.1 bytecode (string.dump) -----------------------------------------------------------------------------

class _Reader:
    def __init__(self, data):
        self.d, self.i = data, 0
        if data[:5] != b"\x1bLua\x51":
            raise LintError("not Lua 5.1 bytecode")
        fmt, little, self.int_size, self.size_size, ins_size, num_size, integral = data[5:12]
        if fmt != 0 or ins_size != 4 or num_size != 8 or integral != 0:
            raise LintError("unexpected Lua 5.1 bytecode format")
        self.e = "<" if little else ">"
        self.i = 12

    def take(self, n):
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def byte(self):
        self.i += 1
        return self.d[self.i - 1]

    def int(self):
        return int.from_bytes(self.take(self.int_size), "little" if self.e == "<" else "big", signed=True)

    def size(self):
        return int.from_bytes(self.take(self.size_size), "little" if self.e == "<" else "big")

    def string(self):
        n = self.size()
        return self.take(n)[:-1].decode("utf-8", "replace") if n else None

    def function(self):
        f = dict(source=self.string(), line=self.int(), last=self.int())
        self.take(4)                                  # nups, numparams, is_vararg, maxstacksize
        n = self.int()
        f["code"] = list(struct.unpack(f"{self.e}{n}I", self.take(4 * n)))
        consts = []
        for _ in range(self.int()):
            t = self.byte()
            if t == 0:
                consts.append(None)
            elif t == 1:
                consts.append(bool(self.byte()))
            elif t == 3:
                consts.append(struct.unpack(f"{self.e}d", self.take(8))[0])
            elif t == 4:
                consts.append(self.string())
            else:
                raise LintError(f"unknown constant type {t}")
        f["consts"] = consts
        f["protos"] = [self.function() for _ in range(self.int())]
        f["lines"] = [self.int() for _ in range(self.int())]
        for _ in range(self.int()):                   # local variables
            self.string()
            self.int()
            self.int()
        for _ in range(self.int()):                   # upvalue names
            self.string()
        return f


def parse_dump(data):
    """the main function of a Lua 5.1 string.dump, with its nested functions"""
    return _Reader(bytes(data)).function()


def scan(fn):
    """what a compiled chunk does with globals: {reads: [(name, line)], writes: [(name, line)], fields: [(global, field,
    line)], events: [(method, event, line)], frames: [(name, line)]} over the function and every nested one"""
    out = dict(reads=[], writes=[], fields=[], events=[], frames=[])

    def walk(f):
        K, lines = f["consts"], f["lines"]
        reg = {}                     # register -> ("g", global name) | ("call", what, base) while it is known
        pending = {}                 # call base register -> (what, first argument register)
        for pc, ins in enumerate(f["code"]):
            op, a = ins & 0x3F, (ins >> 6) & 0xFF
            b, c, bx = (ins >> 23) & 0x1FF, (ins >> 14) & 0x1FF, ins >> 14
            line = lines[pc] if pc < len(lines) else 0
            if op == OP["GETGLOBAL"]:
                name = K[bx]
                out["reads"].append((name, line))
                reg[a] = ("g", name)
                if name == "CreateFrame":
                    pending[a] = ("CreateFrame", a + 2)            # CreateFrame(type, name, ...)
                continue
            if op == OP["SETGLOBAL"]:
                out["writes"].append((K[bx], line))
                continue
            if op == OP["GETTABLE"] and reg.get(b, ("",))[0] == "g" and c & 0x100 and isinstance(K[c & 0xFF], str):
                base, field = reg[b][1], K[c & 0xFF]
                if base == "_G":
                    out["reads"].append((field, line))
                else:
                    out["fields"].append((base, field, line))
                reg[a] = ("g", f"{base}.{field}") if base != "_G" else ("g", field)
                continue
            if op == OP["SETTABLE"] and reg.get(a, ("",))[0] == "g" and reg[a][1] == "_G" and b & 0x100 \
                    and isinstance(K[b & 0xFF], str):
                out["writes"].append((K[b & 0xFF], line))
                continue
            if op == OP["SELF"] and c & 0x100 and K[c & 0xFF] in REGISTERS:
                pending[a] = (K[c & 0xFF], a + 2)                  # obj:RegisterEvent(event): self in a + 1
                reg.pop(a, None)
                reg.pop(a + 1, None)
                continue
            if op == OP["LOADK"] and isinstance(K[bx], str):
                for base, (what, arg) in list(pending.items()):
                    if a == arg:
                        if what == "CreateFrame":
                            out["frames"].append((K[bx], line))
                        else:
                            out["events"].append((what, K[bx], line))
            if op in (OP["CALL"], OP["TAILCALL"]):
                pending.pop(a, None)
                for r in [r for r in reg if r >= a]:
                    del reg[r]
                continue
            if op == OP["CLOSURE"]:
                walk(f["protos"][bx])
            if op not in NO_TARGET:
                reg.pop(a, None)
            if op == OP["JMP"] or op in (OP["FORLOOP"], OP["FORPREP"], OP["TFORLOOP"]):
                reg.clear()                                        # the flow joins: what a register holds is unknown
                pending.clear()
    walk(fn)
    return out


# --- compiling with Lua 5.1 -----------------------------------------------------------------------------------------

_lua_lock = threading.Lock()
_lua = None


def _runtime():
    """a Lua 5.1 state with loadstring and string.dump (one per process, used under _lua_lock); None without lupa"""
    global _lua
    if _lua is None:
        try:
            from lupa.lua51 import LuaRuntime
        except ImportError:
            return None
        _lua = LuaRuntime(encoding=None, register_eval=False)
        _lua.execute(b"function __compile(src, name) local f, err = loadstring(src, name) "
                     b"if not f then return false, err end return string.dump(f), false end")
    return _lua


def compile_lua(source, chunk_name):
    """(bytecode, None) or (None, the error message); (None, None) without lupa"""
    with _lua_lock:
        lua = _runtime()
        if lua is None:
            return None, None
        dump, err = lua.globals()[b"__compile"](source, chunk_name.encode("utf-8"))
    if dump is False:
        return None, err.decode("utf-8", "replace") if isinstance(err, bytes) else str(err)
    return bytes(dump), None


def _syntax_finding(rel, err, text):
    """a finding for a compile error "name:line: message", with a hint for what newer Lua allows"""
    m = re.match(r"^.*?:(\d+): (.*)$", err, re.S)
    line, message = (int(m[1]), m[2]) if m else (0, err)
    hint = ""
    src_line = text.splitlines()[line - 1] if 0 < line <= len(text.splitlines()) else ""
    for near, token, h in SYNTAX_HINTS:
        if near in message and token in src_line:
            hint = h
            break
    return dict(file=rel, line=line, code="syntax", message=message, hint=hint)


_cache = {}                 # str(path) -> (mtime_ns, size, analysis): a file is compiled again only when it changed
_cache_lock = threading.Lock()


def analyze(path, rel):
    """one Lua file: {utf8, syntax: finding (its file: rel) or None, scan: scan() or None, lua: False without lupa};
    OSError when it cannot be read"""
    path = Path(path)
    st = path.stat()
    key = str(path.resolve())
    with _cache_lock:
        hit = _cache.get(key)
    if hit and hit[:2] == (st.st_mtime_ns, st.st_size):
        out = hit[2]
    else:
        data = path.read_bytes()
        out = dict(utf8=True, syntax=None, scan=None, lua=True)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            out["utf8"] = False
        dump, err = compile_lua(data, "@" + path.name)
        if err is not None:
            out["syntax"] = _syntax_finding(path.name, err, data.decode("utf-8", "replace"))
        elif dump is None:
            out["lua"] = False
        else:
            out["scan"] = scan(parse_dump(dump))
        with _cache_lock:
            if len(_cache) > 5000:
                _cache.clear()
            _cache[key] = (st.st_mtime_ns, st.st_size, out)
    return dict(out, syntax=dict(out["syntax"], file=rel)) if out["syntax"] else out


def syntax(path, rel=None):
    """the syntax error of a Lua file ({file, line, code, message, hint}), or None (also without lupa)"""
    path = Path(path)
    return analyze(path, rel or path.name)["syntax"]


# --- what the client has ---------------------------------------------------------------------------------------------

class Known:
    """the names the client has, from the API manual (apidocs.py) and the lists above"""

    def __init__(self, ix=None):
        if ix is None:
            from .. import apidocs
            ix = apidocs.index()
        self.globals = set(BASE) | set(UI_GLOBALS) | set(LIBS)
        self.ns = {}                          # namespace -> its function names (Enum, Constants: their tables)
        self.events, self.restricted = set(), set()
        self.protected = set()
        for e in ix.entries:
            ns, short, item = e.get("ns") or "", e.get("short") or e.get("name"), e.get("item") or {}
            raw = item.get("raw") or {}
            if e["kind"] == "function":
                if ns:
                    self.ns.setdefault(ns, set()).add(short)
                    self.globals.add(ns)
                else:                         # a global function, or a frame method the manual lists without a namespace
                    self.globals.add(short)
            elif e["kind"] == "event":
                self.events.add(short)
                if raw.get("HasRestrictions"):
                    self.restricted.add(short)
            elif e["kind"] == "table":
                if ns:
                    self.globals.add(ns)
                elif item.get("type") == "Enumeration":
                    self.ns.setdefault("Enum", set()).add(short)
                elif item.get("type") == "Constants":
                    self.ns.setdefault("Constants", set()).add(short)
        for u in ix.pack.get("usage", []):
            m = re.match(r"Usage: (?:local [\w, ]+ = )?([A-Za-z_]\w*)\s*[(.:]", u)
            if m:
                self.globals.add(m[1])
        for topic in ("protected",):
            found = ix.manual(topic)
            if isinstance(found, dict):
                self.protected |= set(re.findall(r"`([A-Za-z_]\w*)`", found.get("md", "")))
        self.protected -= {"IsProtectedFunction"}
        self.globals |= self.protected
        for lib, names in LIBS.items():
            self.ns.setdefault(lib, set()).update(names)
        self.moved = {}                       # a function name -> the C_ namespaces that have it ("C_Spell.GetSpellInfo")
        for ns, names in self.ns.items():
            if ns.startswith("C_"):
                for name in names:
                    self.moved.setdefault(name, []).append(f"{ns}.{name}")
        self._by_first = {}
        for name in self.globals:
            self._by_first.setdefault(name[:1].lower(), []).append(name)

    def has(self, name):
        return name in self.globals or bool(UI_PATTERNS.match(name))

    def near(self, name):
        """a known global that name looks like a slip of the keys for, or None"""
        if len(name) < 4:
            return None
        pool = [n for n in self._by_first.get(name[:1].lower(), []) if abs(len(n) - len(name)) <= 2]
        return typo_of(name, pool)

    def near_field(self, ns, field):
        return typo_of(field, sorted(self.ns.get(ns, ())), loose=True)


def edits(a, b):
    """the edit distance of two names, a swap of two neighbouring letters counting one"""
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[len(b)]


def typo_of(name, pool, loose=False):
    """the name of pool that name is a typo of: one slip of the keys (a letter missing, extra, wrong, or two swapped),
    not a name that only adds or drops letters at an end (WoWBridgeDB is not WoWBridge mistyped); loose (the functions
    of one namespace, a short list): within two slips"""
    best = None
    for cand in difflib.get_close_matches(name, pool, n=5, cutoff=0.6 if loose else 0.75):
        if cand == name or cand.startswith(name) or name.startswith(cand) or cand.endswith(name) or name.endswith(cand):
            continue
        d = edits(name.lower(), cand.lower()) if cand.lower() != name.lower() else 1
        limit = 2 if loose else 1
        if d <= limit and (best is None or d < best[0]):
            best = (d, cand)
    return best[1] if best else None


_known = None
_known_lock = threading.Lock()


def known():
    global _known
    with _known_lock:
        if _known is None:
            _known = Known()
        return _known


# --- the .toc and the files -------------------------------------------------------------------------------------------

def read_toc(toc):
    """{interface: [ints], saved: [names], files: [(line, path text)], title}"""
    info = dict(interface=[], saved=[], files=[], title=None)
    text = toc.read_bytes().decode("utf-8-sig", "replace")
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if s.startswith("##"):
            key, _, value = s[2:].partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "interface":
                info["interface"] = [int(v) for v in re.findall(r"\d+", value)]
            elif key in ("savedvariables", "savedvariablespercharacter"):
                info["saved"] += [v.strip() for v in value.split(",") if v.strip()]
            elif key == "title":
                info["title"] = value
            continue
        if s.startswith("#"):
            continue
        info["files"].append((n, s))
    return info


def _in_library(rel):
    return any(part.lower() in LIBRARY_DIRS for part in Path(rel).parts[:-1])


def _xml_names(text):
    return set(re.findall(r'<(?:Frame|Button|CheckButton|EditBox|ScrollFrame|Slider|StatusBar|Cooldown|GameTooltip|'
                          r'MessageFrame|SimpleHTML|Model|PlayerModel|ColorSelect|MovieFrame)\b[^>]*?\bname\s*=\s*"([A-Za-z_]\w*)"',
                          text))


def client_number(addons):
    """the ## Interface of the client whose AddOns folder this is (installer.addons.client_info), else INTERFACE"""
    if addons is None:
        return INTERFACE
    try:
        from ..installer.addons import client_interface
        return client_interface(Path(addons).parent.parent)[1] or INTERFACE
    except Exception:
        return INTERFACE


def check(path, addons=None, ix=None):
    """the report on a file or an addon folder (see the module docstring)"""
    path = Path(path)
    if not path.exists():
        raise LintError(f"{path}: no such file or folder")
    k = known() if ix is None else Known(ix)
    folder = path if path.is_dir() else None
    if folder is None and addons is not None:
        try:
            rel = path.resolve().relative_to(Path(addons).resolve())
            if len(rel.parts) > 1:
                folder = Path(addons) / rel.parts[0]
        except ValueError:
            pass
    if folder is None:
        folder = path.parent if path.parent.glob("*.toc") and any(path.parent.glob("*.toc")) else None
    report = dict(target=str(path), addon=folder.name if folder else None, files=0, errors=[], warnings=[], notes=[],
                  unresolved={}, libraries=0, ok=True)
    findings = []

    def add(rel, line, code, message, hint="", level="warnings"):
        findings.append((level, dict(file=rel, line=line, code=code, message=message, hint=hint)))

    # the files: the addon's (for its definitions), the target's reported
    toc = None
    if folder is not None:
        toc = next((t for t in [folder / f"{folder.name}.toc"] + sorted(folder.glob("*.toc")) if t.is_file()), None)
    tocinfo = read_toc(toc) if toc else dict(interface=[], saved=[], files=[], title=None)
    lua_files = sorted(folder.rglob("*.lua")) if folder is not None else [path]
    if path.is_file() and path.suffix.lower() == ".lua" and path not in lua_files:
        lua_files.append(path)
    targets = set(lua_files) if path.is_dir() else {path}

    def rel_of(p):
        try:
            return p.relative_to(folder).as_posix() if folder else p.name
        except ValueError:
            return p.name

    defined = set(tocinfo["saved"])
    if folder is not None:
        defined.add(folder.name)
    for xml in (sorted(folder.rglob("*.xml")) if folder is not None else []):
        try:
            defined |= _xml_names(xml.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass

    scans = {}
    lupa_missing = False
    for p in lua_files:
        rel = rel_of(p)
        try:
            a = analyze(p, rel)
        except OSError as e:
            if p in targets:
                add(rel, 0, "read", f"cannot read: {e}", level="errors")
            continue
        if p in targets:
            report["files"] += 1
            if not a["utf8"]:
                add(rel, 0, "encoding", "not UTF-8: the client shows its text garbled",
                    "save the file as UTF-8 (without changing the code)")
            if a["syntax"]:
                findings.append(("errors", a["syntax"]))
        if not a["lua"]:
            lupa_missing = True
        s = a["scan"]
        if s is None:
            continue
        scans[p] = s
        defined |= {name for name, _ in s["writes"]}
        defined |= {name for name, _ in s["frames"]}

    if lupa_missing:
        report["notes"].append(dict(file="", line=0, code="no-lua", message="Lua 5.1 (lupa) is not available here: the Lua "
                                    "files were not checked", hint=""))

    for p, s in scans.items():
        if p not in targets:
            continue
        rel = rel_of(p)
        if _in_library(rel):
            report["libraries"] += 1
            continue
        for name, line in s["reads"]:
            if name in MISSING and MISSING[name]:
                add(rel, line, "missing-lib", f"{name} does not exist in this client", f"use {MISSING[name]}", "errors")
            elif name in defined or k.has(name):
                continue
            elif name in k.moved:                    # the global went, its namespace has it (GetSpellInfo -> C_Spell)
                add(rel, line, "moved", f"{name} is not a global of this client",
                    f"use {' or '.join(sorted(k.moved[name])[:3])}")
            else:
                near = k.near(name)
                if near:
                    add(rel, line, "typo", f"{name} is neither the client's nor this addon's", f"did you mean {near}?")
                else:
                    report["unresolved"].setdefault(name, []).append(f"{rel}:{line}")
        for base, field, line in s["fields"]:
            if (base, field) in NEWER:
                add(rel, line, "lua52", f"{base}.{field} is newer than Lua 5.1 (nil in this client)", f"use {NEWER[base, field]}",
                    "errors")
            elif base in LIBS:
                if field not in k.ns.get(base, ()):
                    near = k.near_field(base, field)
                    add(rel, line, "lua52", f"{base}.{field} does not exist in this client's Lua",
                        f"did you mean {base}.{near}?" if near else "", "errors")
            elif (base.startswith("C_") or base in ("Enum", "Constants")) and base in k.ns and field not in k.ns[base]:
                near = k.near_field(base, field)
                add(rel, line, "api-unknown", f"{base}.{field} is not in the API manual",
                    f"did you mean {base}.{near}?" if near else "api_search it, or run `return type(" + f"{base}.{field})`")
        own = re.sub(r"^\W+", "", folder.name) if folder else None      # !MyAddon writes MyAddonDB
        for name, line in s["writes"]:
            if name in tocinfo["saved"] or ALLOWED_WRITES.match(name) or (own and len(own) >= 3 and name.startswith(own)):
                continue                    # the addon's own name in front: the usual way to keep a global apart
            if name in {n for n, _ in s["frames"]}:
                continue
            add(rel, line, "global-write", f"writes the global {name}",
                "forgot local? Globals are shared by every addon (and taint the Blizzard UI when it reads them)")
        for name, line in s["reads"]:
            if name in k.protected:
                add(rel, line, "protected", f"{name} is protected: an addon calling it is blocked (ADDON_ACTION_FORBIDDEN)",
                    "only a secure button the player clicks may do this")
        for what, event, line in s["events"]:
            if event in k.restricted:
                add(rel, line, "event-restricted", f"{event} is restricted: the client blocks an addon that registers it",
                    "find another event (trace shows what fires)")
            elif k.events and event not in k.events:
                add(rel, line, "event-unknown", f"{what}(\"{event}\"): not an event of this client (an error in the game)",
                    "api_search the event's name", "errors")

    # the .toc and the XML (for an addon folder)
    if path.is_dir() and toc is not None:
        trel = rel_of(toc)
        want = client_number(addons)
        if tocinfo["interface"] and want not in tocinfo["interface"]:
            add(trel, 0, "toc-interface", f"Interface {', '.join(map(str, tocinfo['interface']))} is not this client's "
                f"({want}): the game lists the addon as out of date", f"## Interface: {want}")
        for n, name in tocinfo["files"]:
            if not (folder / name.replace("\\", "/")).exists():
                add(trel, n, "toc-missing", f"{name} is listed but not there", "", "errors")
        for xml in sorted(folder.rglob("*.xml")):
            rel = rel_of(xml)
            if _in_library(rel):
                continue
            try:
                ET.fromstring(xml.read_bytes())
            except ET.ParseError as e:
                add(rel, e.position[0] if e.position else 0, "xml", f"not well-formed XML: {e}", "", "errors")
            except OSError:
                pass

    seen = set()
    for level, f in findings:
        key = (f["file"], f["line"], f["code"], f["message"])
        if key in seen:
            continue
        seen.add(key)
        report[level].append(f)
    for level in ("errors", "warnings"):
        report[level].sort(key=lambda f: (f["file"], f["line"]))
    if report["unresolved"]:
        report["notes"].append(dict(file="", line=0, code="unresolved",
                                    message=f"{len(report['unresolved'])} globals are neither in the API manual nor "
                                            f"defined by this addon: {', '.join(sorted(report['unresolved'])[:20])}",
                                    hint="they may come from the Blizzard UI or another addon; check asks the running game"))
    report["ok"] = not report["errors"]
    return report


# --- asking the running game ----------------------------------------------------------------------------------------

def live_names(report):
    """the names the running game can settle: unresolved globals, the typos' and the unknown API names (dotted)"""
    names = set(report["unresolved"])
    for f in report["warnings"]:
        if f["code"] in ("typo", "api-unknown", "moved"):
            names.add(f["message"].split(" ", 1)[0])
    return sorted(names)


def live_chunk(names, json_lua, lua_str):
    """Lua that answers {name: its type in the game} for global names, dotted ones walked from _G"""
    listed = ", ".join(lua_str(n) for n in names)
    return (json_lua + "\nlocal out = {}\nfor _, n in ipairs({" + listed + "}) do\n"
            "\tlocal v = _G\n\tfor part in n:gmatch(\"[^.]+\") do\n"
            "\t\tif type(v) ~= \"table\" then v = nil break end\n\t\tv = v[part]\n\tend\n"
            "\tout[n] = type(v)\nend\nreturn J(out)\n")


def apply_live(report, types):
    """the report with the game's answers ({name: type}): a name the game has is no finding; one it does not have is
    an error ("undefined": nil when the line runs)"""
    def settled(name):
        return types.get(name) not in (None, "nil")
    left = {}
    for name, where_all in report["unresolved"].items():
        if name not in types:                              # not in the answer: still unresolved
            left[name] = where_all
            continue
        if settled(name):
            continue
        for where in where_all:
            file, _, line = where.rpartition(":")
            report["errors"].append(dict(file=file, line=int(line or 0), code="undefined",
                                         message=f"{name} is nil in the running game", hint="a typo, or defined nowhere"))
    report["unresolved"] = left
    keep = []
    for f in report["warnings"]:
        name = f["message"].split(" ", 1)[0]
        if f["code"] in ("typo", "api-unknown", "moved") and name in types:
            if settled(name):
                continue                                   # the game has it: the manual or our lists did not
            report["errors"].append(dict(f, code="undefined", message=f"{name} is nil in the running game"))
            continue
        keep.append(f)
    report["warnings"] = keep
    if not left:
        report["notes"] = [n for n in report["notes"] if n["code"] != "unresolved"]
    report["errors"].sort(key=lambda f: (f["file"], f["line"]))
    report["ok"] = not report["errors"]
    report["live"] = dict(checked=len(types))
    return report
