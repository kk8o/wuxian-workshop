r"""Checks an addon's files before they go to the game (写完先检查): what would fail there is found here in milliseconds,
with the file and the line, together with what may be wrong.

    check(path, addons=None) -> report        a file or an addon folder (the files its .toc loads on this client)
    syntax(path) -> None or the first syntax error ({file, line, code, message, hint})
    report = {target, addon, files, ok, errors: [finding], warnings: [...], notes: [...], unresolved: {name: [where]},
              guarded: {name: [where]}, libraries}
    finding = {file, line, code, message, hint}   (file relative to the addon folder; hint: what to do, may be "")

The files are the ones the client loads: the .toc's lines for this client's game type (camelot: a line's
[AllowLoadGameType ...] and [ExcludeLoadGameType ...] conditions are met; a ## line's too), and what their XML files load
(<Script file> and <Include file>, comments skipped). A file nothing loads here (another game version's) is not checked:
a note ("not-loaded") lists them.
Lua files are compiled by Lua 5.1 (lupa's lua51; the client runs "Lua 5.1.4"; a UTF-8 BOM in front is dropped first, as
the client does): a syntax error is an error ("syntax"), with a hint where newer Lua allows what 5.1 does not (//, goto
and ::labels::, the bitwise operators). The compiled chunk's bytecode (string.dump) gives every global it reads and
writes, with its line:
  - "missing-lib" (error): a library or function this client does not have (os, io, utf8, require, dofile, ...)
  - "lua52" (error): a library function newer than 5.1 (table.unpack, table.pack, table.move, math.tointeger, ...)
  - "event-unknown" (error): RegisterEvent / RegisterUnitEvent with a constant name the API manual does not know (the
    client raises an error); "event-restricted" (error): one the client blocks addons from (HasRestrictions)
  - "typo" (warning): a global read that neither the client (Lua and WoW's libraries, the API manual, the exe's Usage
    strings, the common Blizzard UI globals in UI_GLOBALS) nor the addon has, close to a name the client has; the
    others are notes ("unresolved"), which the daemon can ask the running game about. The addon's names: its global
    writes, SavedVariables, frame names from CreateFrame and XML, the .toc's X-oUF (an embedded oUF's global), and the
    same of the addons its ## Dependencies / RequiredDeps / OptionalDeps name when they are next to it in AddOns, with
    the library fields they set (string.utf8sub = ...)
  - "moved" (warning): a global this client does not have whose name a C_ namespace has (GetSpellInfo: use
    C_Spell.GetSpellInfo; the old globals went in 11.0)
  - "api-unknown" (warning): C_Namespace.Name that the manual does not list for that namespace
  - "global-write" (warning): a global the addon writes that is not a SavedVariable, a SLASH_ / BINDING_ name, a frame
    name, a name starting with the addon's own (MyAddon, MyAddonDB, MyAddon_Config) or WoWBridgeNS ("forgot local?")
  - "protected" (error): a call of a protected function (the manual's list): blocked for addons; a warning where it is
    only read, not called
  - "xml" (error): an XML file that is not well-formed; "toc-missing" (error): a .toc line naming a missing file;
    "toc-interface" (warning): an Interface number that is not the client's; "encoding" (warning): not UTF-8
A name this client does not have (missing-lib, lua52, nil in the running game) is an error where nil surely fails:
called, indexed or computed with where the function always gets (the main chunk: when the file loads), or passed to a
function there; it is a warning where the read keeps it (local X = X, in a table) and nothing so uses it, only tests it
(if X then, X and X.y) or is behind a condition: addons for several game versions keep globals at the top and branch by
version (_flow). report["guarded"] has those places for the names the running game may answer (apply_live).
Names read from a table the addon (or one it depends on) gives functions as their globals with setfenv (oUF's tags:
_TAGS, Hex, _COLORS) are not globals: a note ("setfenv") lists them.
Files in a library folder (Libs, Lib, Libraries) are compiled and give their definitions, but only their syntax errors
are reported. Without lupa the Lua checks are skipped (a note says so).
"""
import difflib
import os
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
GAME_TYPE = "camelot"           # this client's game type in the .toc's load conditions (the API manual's toc topic)
CONDITIONS = re.compile(r"\s*((?:\[\s*(?:Allow|Exclude)Load\w*[^\[\]]*\]\s*)+)$", re.I)   # at the end of a .toc line
CONDITION = re.compile(r"\[\s*((?:Allow|Exclude)Load\w*)\s*([^\[\]]*)\]", re.I)
DEPENDENCIES = {"dependencies", "requireddeps", "optionaldeps"}      # and any other ## Dep... (the client's rule)
ENV_NAME = re.compile(r"env(ironment)?$", re.I)     # a table functions get as their globals: _ENV, Env, env, configEnv
GUARDED_HINT = ("kept in a local (local X = X), tested or behind a condition here: nothing uses it unconditionally; "
                "fine if only the code for other game versions needs it")
RK = 0x100                      # an instruction's B or C operand from 256 up is a constant
OP_NAMES = ("MOVE", "LOADK", "LOADBOOL", "LOADNIL", "GETUPVAL", "GETGLOBAL", "GETTABLE", "SETGLOBAL", "SETUPVAL",
            "SETTABLE", "NEWTABLE", "SELF", "ADD", "SUB", "MUL", "DIV", "MOD", "POW", "UNM", "NOT", "LEN", "CONCAT",
            "JMP", "EQ", "LT", "LE", "TEST", "TESTSET", "CALL", "TAILCALL", "RETURN", "FORLOOP", "FORPREP", "TFORLOOP",
            "SETLIST", "CLOSE", "CLOSURE", "VARARG")
OP = {name: i for i, name in enumerate(OP_NAMES)}
NO_TARGET = {OP[n] for n in ("SETGLOBAL", "SETUPVAL", "SETTABLE", "EQ", "LT", "LE", "TEST", "JMP", "RETURN", "SETLIST",
                             "CLOSE", "TAILCALL")}            # instructions that do not write R(A)
FAILS_ON_NIL = {OP[n] for n in ("ADD", "SUB", "MUL", "DIV", "MOD", "POW", "LT", "LE")}   # nil as RK(B) or RK(C)
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
        f["nups"], _, _, f["maxstack"] = self.take(4)  # nups, numparams, is_vararg, maxstacksize
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
        f["locals"] = [(self.string(), self.int(), self.int()) for _ in range(self.int())]   # (name, startpc, endpc)
        f["upnames"] = [self.string() for _ in range(self.int())]
        return f


def parse_dump(data):
    """the main function of a Lua 5.1 string.dump, with its nested functions"""
    return _Reader(bytes(data)).function()


def scan(fn):
    """what a compiled chunk does with globals: {reads: [(name, line)], writes: [(name, line)], fields: [(global, field,
    line)], events: [(method, event, line)], frames: [(name, line)]} over the function and every nested one, and what
    the values read become (_flow: guarded, called, defines, fdefines, env)"""
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
    out.update(_flow(fn))
    return out


# --- what the values read from globals become ------------------------------------------------------------------------

def _sbx(ins):
    return (ins >> 14) - 131071


def _next_pcs(f):
    """per instruction, where the flow goes next (len(code): the function returns); None for the lines after a CLOSURE
    that name its upvalues (they do not run)"""
    code = f["code"]
    n = len(code)
    out = [None] * n
    pc = 0
    while pc < n:
        ins = code[pc]
        op = ins & 0x3F
        if op == OP["CLOSURE"]:
            ups = f["protos"][ins >> 14]["nups"]
            out[pc] = [min(pc + 1 + ups, n)]
            pc += 1 + ups
            continue
        if op in (OP["JMP"], OP["FORPREP"]):
            to = [pc + 1 + _sbx(ins)]
        elif op == OP["FORLOOP"]:
            to = [pc + 1, pc + 1 + _sbx(ins)]
        elif op in (OP["EQ"], OP["LT"], OP["LE"], OP["TEST"], OP["TESTSET"], OP["TFORLOOP"]):
            to = [pc + 1, pc + 2]                          # these skip the next instruction (a JMP) or not
        elif op == OP["LOADBOOL"] and (ins >> 14) & 0x1FF:
            to = [pc + 2]
        elif op == OP["RETURN"]:
            to = [n]
        else:
            to = [pc + 1]
        out[pc] = [min(max(t, 0), n) for t in to]
        pc += 1
    return out


def _always(nxt):
    """the instructions that run every time the function does: on every way from its start to its end (no condition,
    loop or early return before them can skip them)"""
    n = len(nxt)
    starts = {0}
    for pc, to in enumerate(nxt):
        if to is not None and to != [pc + 1]:
            starts.update(t for t in to if t < n)
            if pc + 1 < n:
                starts.add(pc + 1)
    starts = sorted(starts)
    block = {s: i for i, s in enumerate(starts)}
    m = len(starts)
    ends = starts[1:] + [n]
    succ = [[block[t] if t < n else m for t in nxt[e - 1]] if nxt[e - 1] is not None else [] for e in ends]
    full = (1 << (m + 1)) - 1
    post = [full] * m + [1 << m]              # per block, the blocks on every way from it to the end (m: the end)
    changed = True
    while changed:
        changed = False
        for i in range(m - 1, -1, -1):
            acc = full if succ[i] else 0
            for t in succ[i]:
                acc &= post[t]
            acc |= 1 << i
            if acc != post[i]:
                post[i], changed = acc, True
    return {pc for i in range(m) if post[0] >> i & 1 for pc in range(starts[i], ends[i])}


def _registers(f, nxt):
    """(the local variables [(name, startpc, endpc, register)], register -> the instructions that write it)"""
    locs = []
    for name, start, end in f["locals"]:              # the n-th local active where it starts is register n - 1; one
        locs.append((name or "", start, end,           # declared as a block ends starts where the others end
                     sum(1 for _, s, e, _r in locs if s <= start < e or (start == end and s <= start == e))))
    written = {}
    for pc, ins in enumerate(f["code"]):
        if nxt[pc] is None:
            continue
        op, a = ins & 0x3F, (ins >> 6) & 0xFF
        b, c = (ins >> 23) & 0x1FF, (ins >> 14) & 0x1FF
        if op == OP["LOADNIL"]:
            regs = range(a, b + 1)
        elif op == OP["CALL"]:
            regs = range(a, a + c - 1 if c else f["maxstack"])
        elif op == OP["VARARG"]:
            regs = range(a, a + b - 1 if b else f["maxstack"])
        elif op == OP["TFORLOOP"]:
            regs = range(a + 2, a + 3 + c)
        elif op == OP["FORLOOP"]:
            regs = (a, a + 3)
        elif op == OP["SELF"]:
            regs = (a, a + 1)
        elif op in NO_TARGET:
            regs = ()
        else:
            regs = (a,)
        for r in regs:
            written.setdefault(r, []).append(pc)
    return locs, written


def _upvalues_set(p, memo):
    """the upvalues a function (or one inside it) assigns"""
    if id(p) not in memo:
        memo[id(p)] = out = set()
        code, pc = p["code"], 0
        while pc < len(code):
            ins = code[pc]
            if ins & 0x3F == OP["SETUPVAL"]:
                out.add((ins >> 23) & 0x1FF)
            elif ins & 0x3F == OP["CLOSURE"]:
                q = p["protos"][ins >> 14]
                inner = _upvalues_set(q, memo)
                for j in range(q["nups"]):
                    line = code[pc + 1 + j]
                    if line & 0x3F == OP["GETUPVAL"] and j in inner:
                        out.add((line >> 23) & 0x1FF)
                pc += 1 + q["nups"]
                continue
            pc += 1
    return memo[id(p)]


def _merge(states):
    """where ways join: a register keeps what every way agrees it may hold"""
    if len(states) == 1:
        return dict(states[0])
    keys = set(states[0]).intersection(*states[1:])
    return {r: frozenset().union(*(s[r] for s in states)) for r in keys}


def _flow(main):
    """follows the values a chunk reads from globals through registers, locals and upvalues: {guarded: {(name, line)}:
    reads where a nil value does not surely fail (see below), called: {(name, line)}: reads whose value is called (in
    that function, or in one the local it is kept in is an upvalue of), defines: global names it sets (also through
    local _G = _G, and _G[NAME] with NAME a local constant), fdefines: {(global, field)} it sets (string.utf8sub = ...),
    env: the keys it puts in a table named like a function environment (_ENV, Env: setfenv's tables)}.
    A read of nil surely fails when its value is called, indexed, computed with or used as a key where the function
    always gets (no condition, loop or early return before it; the main chunk: when the file loads), directly or from a
    local of that function; or when the read always runs and its value is passed to a function or returned without
    being kept. Kept in a local (local X = X), a table or a global, only tested (if X then, X and X.y) or behind a
    condition: guarded.
    A register holds a set of origins: ("s", read) a value read in this function, ("u", read) one through an upvalue,
    ("k", text) a string constant, ("t", key) a table built here, ("e",) a table named like an environment"""
    sites = []                   # per read: [name, line, base read, kept, fails, called, always runs, goes on]
    ctor = {}                    # a table built by NEWTABLE -> the keys set on it
    out = dict(defines=set(), fdefines=set(), env=set())
    memo = {}

    def site(name, line, sure, base=None):
        sites.append([name, line, base, False, False, False, sure, False])
        return len(sites) - 1

    def keep(i):
        while i is not None and not sites[i][3]:
            sites[i][3] = True
            i = sites[i][2]

    def use(v, sure, call=False):
        """v is called, indexed or computed with: nil fails there"""
        for o in v or ():
            if call and o[0] in ("s", "u"):
                sites[o[1]][5] = True
        if sure and v and len(v) == 1:
            (o,) = v
            if o[0] == "s":
                sites[o[1]][4] = True

    def goes_on(v):
        """v is passed to a function or returned as it is"""
        for o in v or ():
            if o[0] == "s":
                sites[o[1]][7] = True

    def kept_in(v):
        """v is kept in a table, a global or an upvalue (as in a local: nil leaves it empty)"""
        for o in v or ():
            if o[0] == "s":
                keep(o[1])

    def field(v, key, line, sure):
        """what reading v[key] gives"""
        got = set()
        g = [o for o in v or () if o[0] in ("s", "u")]
        names = {sites[o[1]][0] for o in g}
        if len(names) == 1:
            kind = "s" if all(o[0] == "s" for o in g) else "u"
            name = names.pop()
            got.add((kind, site(key, line, sure) if name == "_G" else site(f"{name}.{key}", line, sure, g[0][1])))
        if ENV_NAME.search(key):
            got.add(("e",))
        return frozenset(got) or None

    def walk(f, upv):
        code, K, lines = f["code"], f["consts"], f["lines"]
        n = len(code)
        nxt = _next_pcs(f)
        always = _always(nxt)
        locs, written = _registers(f, nxt)
        reassigned = {}                          # register -> the closures that capture it and assign it
        for pc, ins in enumerate(code):
            if ins & 0x3F == OP["CLOSURE"] and nxt[pc] is not None:
                q = f["protos"][ins >> 14]
                inner = _upvalues_set(q, memo)
                for j in range(q["nups"]):
                    line = code[pc + 1 + j]
                    if line & 0x3F == OP["MOVE"] and j in inner:
                        reassigned.setdefault((line >> 23) & 0x1FF, []).append(pc)
        stable = [not any(s <= pc < e for pc in written.get(r, []) + reassigned.get(r, [])) for _, s, e, r in locs]
        begins = {}
        for i, (_, s, _e, _r) in enumerate(locs):
            begins.setdefault(s, []).append(i)
        heads = {t for pc, to in enumerate(nxt) if to for t in to if t <= pc}       # loops start here
        env_locals = [(s, e, r) for name, s, e, r in locs if ENV_NAME.search(name)]
        start_value = {}                         # a stable local -> what it holds (from where it starts)
        incoming = {}                            # pc -> the states jumps bring there
        state, falls = {}, True

        def put(st, r, v):
            if v:
                st[r] = v
            else:
                st.pop(r, None)

        def from_up(st, r):                      # the registers from r up (B = 0: up to the top)
            return [st[x] for x in sorted(st) if x >= r]

        for pc in range(n):
            if nxt[pc] is None:
                continue
            ways = incoming.pop(pc, [])
            if falls:
                ways.append(state)
            st = _merge(ways) if ways else {}
            ins = code[pc]
            op, a = ins & 0x3F, (ins >> 6) & 0xFF
            b, c, bx = (ins >> 23) & 0x1FF, (ins >> 14) & 0x1FF, ins >> 14
            line = lines[pc] if pc < len(lines) else 0
            for i in begins.get(pc, ()):         # locals that start here hold what their register holds
                name, _, _, r = locs[i]
                v = st.get(r)
                if stable[i] and v:
                    start_value[i] = v
                if v and not name.startswith("(") and not (op == OP["CLOSURE"] and a == r):
                    for o in v:
                        if o[0] == "s":
                            keep(o[1])
                        elif o[0] == "t" and ENV_NAME.search(name):
                            out["env"] |= ctor[o[1]]
            if pc in heads:                      # a loop comes back here: only the stable locals are known
                st = {locs[i][3]: v for i, v in start_value.items() if locs[i][1] <= pc < locs[i][2]}
            sure = pc in always
            follow = pc + 1
            if op == OP["MOVE"]:
                put(st, a, st.get(b))
            elif op == OP["LOADK"]:
                put(st, a, frozenset([("k", K[bx])]) if isinstance(K[bx], str) else None)
            elif op == OP["LOADNIL"]:
                for r in range(a, b + 1):
                    st.pop(r, None)
            elif op == OP["GETUPVAL"]:
                v = {("u", o[1]) if o[0] == "s" else o for o in (upv[b] if b < len(upv) and upv[b] else ())}
                names = f["upnames"]
                if b < len(names) and names[b] and ENV_NAME.search(names[b]):
                    v.add(("e",))
                put(st, a, frozenset(v))
            elif op == OP["GETGLOBAL"]:
                put(st, a, frozenset([("s", site(K[bx], line, sure))]))
            elif op == OP["GETTABLE"]:
                use(st.get(b), sure)
                key = K[c & 0xFF] if c & RK else None
                put(st, a, field(st.get(b), key, line, sure) if isinstance(key, str) else None)
            elif op in (OP["SETGLOBAL"], OP["SETUPVAL"]):
                kept_in(st.get(a))
                if op == OP["SETGLOBAL"]:
                    out["defines"].add(K[bx])
            elif op == OP["SETTABLE"]:
                v = st.get(a) or frozenset()
                use(v, sure)
                if not b & RK:
                    use(st.get(b), sure)         # t[nil] = x fails
                if not c & RK:
                    kept_in(st.get(c))
                key = K[b & 0xFF] if b & RK else None
                if key is None:                  # _G[NAME] = x with NAME a local holding a constant
                    ks = {o[1] for o in st.get(b, ()) if o[0] == "k"}
                    key = ks.pop() if len(ks) == 1 and len(st[b]) == 1 else None
                if isinstance(key, str):
                    for o in v:
                        if o[0] in ("s", "u"):
                            if sites[o[1]][0] == "_G":
                                out["defines"].add(key)
                            else:
                                out["fdefines"].add((sites[o[1]][0], key))
                        elif o[0] == "t":
                            ctor[o[1]].add(key)
                    if ("e",) in v or any(r == a and s <= pc < e for s, e, r in env_locals):
                        out["env"].add(key)
            elif op == OP["NEWTABLE"]:
                ctor[(id(f), pc)] = set()
                put(st, a, frozenset([("t", (id(f), pc))]))
            elif op == OP["SELF"]:
                v = st.get(b)
                use(v, sure)
                st.pop(a, None)
                put(st, a + 1, v)
            elif op in (OP["CALL"], OP["TAILCALL"]):
                fn = st.get(a)
                use(fn, sure, call=True)
                for v in ([st.get(r) for r in range(a + 1, a + b)] if b else from_up(st, a + 1)):
                    goes_on(v)
                first = st.get(a + 1) if b != 1 else None
                for r in [r for r in st if r >= a]:
                    del st[r]
                if fn and {sites[o[1]][0] for o in fn if o[0] in ("s", "u")} == {"setmetatable"} and c != 1:
                    put(st, a, first)            # setmetatable(t, mt) gives t back
            elif op == OP["RETURN"]:
                for v in ([st.get(r) for r in range(a, a + b - 1)] if b else from_up(st, a)):
                    goes_on(v)
            elif op == OP["SETLIST"]:
                for v in ([st.get(r) for r in range(a + 1, a + b + 1)] if b else from_up(st, a + 1)):
                    kept_in(v)
            elif op in FAILS_ON_NIL:
                for x in (b, c):
                    if not x & RK:
                        use(st.get(x), sure)
                if op not in NO_TARGET:
                    st.pop(a, None)
            elif op in (OP["UNM"], OP["LEN"]):
                use(st.get(b), sure)
                st.pop(a, None)
            elif op == OP["CONCAT"]:
                for r in range(b, c + 1):
                    use(st.get(r), sure)
                st.pop(a, None)
            elif op == OP["FORPREP"]:
                for r in (a, a + 1, a + 2):
                    use(st.get(r), sure)
                st.pop(a, None)
            elif op == OP["TFORLOOP"]:
                use(st.get(a), sure, call=True)
                goes_on(st.get(a + 1))
                goes_on(st.get(a + 2))
                for r in range(a + 2, a + 3 + c):
                    st.pop(r, None)
            elif op == OP["CLOSURE"]:
                q = f["protos"][bx]
                ups = []
                for j in range(q["nups"]):       # what the new function's upvalues hold: stable locals, our upvalues
                    line2 = code[pc + 1 + j]
                    r = (line2 >> 23) & 0x1FF
                    if line2 & 0x3F == OP["MOVE"]:
                        i = next((i for i, (_, s, e, reg) in enumerate(locs) if reg == r and s <= pc < e), None)
                        ups.append(start_value.get(i) if i is not None and stable[i] else None)
                    else:
                        ups.append(upv[r] if r < len(upv) else None)
                walk(q, ups)
                st.pop(a, None)
                follow = pc + 1 + q["nups"]
            elif op == OP["VARARG"]:
                for r in [r for r in st if r >= a and (not b or r < a + b - 1)]:
                    del st[r]
            elif op == OP["FORLOOP"]:
                st.pop(a, None)
                st.pop(a + 3, None)
            elif op == OP["TESTSET"]:            # R(A) := R(B) on the way that does not skip the next instruction
                incoming.setdefault(pc + 2, []).append(dict(st))
                put(st, a, st.get(b))
            elif op not in NO_TARGET:            # LOADBOOL, NOT
                st.pop(a, None)
            for t in nxt[pc]:
                if pc < t < n and t != follow and op != OP["TESTSET"]:
                    incoming.setdefault(t, []).append(dict(st))
            falls = follow in nxt[pc]
            state = st

    walk(main, [])
    verdict = {}
    for name, line, _, kept, fails, _called, sure, on in sites:
        verdict[name, line] = verdict.get((name, line), True) and not fails and (kept or not (sure and on))
    return dict(guarded={k for k, v in verdict.items() if v}, called={(s[0], s[1]) for s in sites if s[5]}, **out)


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
        if data.startswith(b"\xef\xbb\xbf"):          # a UTF-8 BOM: the client loads the file without it
            data = data[3:]
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

def toc_line(text):
    r"""(a .toc line's text without the load conditions at its end, whether it loads on this client): "Game\load.xml
    [AllowLoadGameType camelot][ExcludeLoadGameType standard, classic]" -> ("Game\load.xml", True); a condition this
    check cannot judge (an environment, a text locale) counts as met"""
    m = CONDITIONS.search(text)
    if not m:
        return text.strip(), True
    here = True
    for name, values in CONDITION.findall(m[1]):
        types = {v.lower() for v in re.split(r"[\s,]+", values) if v}
        if name.lower() == "allowloadgametype" and GAME_TYPE not in types:
            here = False
        elif name.lower() == "excludeloadgametype" and GAME_TYPE in types:
            here = False
    return text[:m.start()].strip(), here


def read_toc(toc):
    """{interface: [ints], saved: [names], files: [(line, path text)], title, deps: [addon names], ouf: the global an
    embedded oUF takes (## X-oUF) or None, loads: False when ## AllowLoadGameType leaves this client out}: the lines
    that load on this client (toc_line)"""
    info = dict(interface=[], saved=[], files=[], title=None, deps=[], ouf=None, loads=True)
    text = toc.read_bytes().decode("utf-8-sig", "replace")
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if s.startswith("##"):
            key, _, value = s[2:].partition(":")
            key = key.strip().lower()
            value, here = toc_line(value)
            if not here:
                continue
            if key == "interface":
                info["interface"] = [int(v) for v in re.findall(r"\d+", value)]
            elif key in ("savedvariables", "savedvariablespercharacter"):
                info["saved"] += [v.strip() for v in value.split(",") if v.strip()]
            elif key == "title":
                info["title"] = value
            elif key in DEPENDENCIES or key.startswith("dep"):
                info["deps"] += [v.strip() for v in value.split(",") if v.strip()]
            elif key == "x-ouf":
                info["ouf"] = value or None
            elif key == "allowloadgametype":
                info["loads"] = GAME_TYPE in {v.lower() for v in re.split(r"[\s,]+", value) if v}
            continue
        if s.startswith("#"):
            continue
        name, here = toc_line(s)
        if here and name:
            info["files"].append((n, name))
    return info


def _key(p):
    """a file's identity (the case and the .. of a path as the client takes them)"""
    return os.path.normcase(os.path.normpath(str(p)))


def _xml_refs(path):
    """the files an XML file loads, in order: [(Script | Include, file text)]; comments are not read"""
    data = path.read_bytes()
    try:
        return [(el.tag.rsplit("}", 1)[-1], el.get("file")) for el in ET.fromstring(data).iter()
                if el.tag.rsplit("}", 1)[-1] in ("Script", "Include") and el.get("file")]
    except ET.ParseError:                         # not well-formed (reported): what it names, as far as it can be read
        text = re.sub(r"<!--.*?-->", "", data.decode("utf-8-sig", "replace"), flags=re.S)
        return re.findall(r"<(Script|Include)\b[^>]*?\bfile\s*=\s*[\"']([^\"']+)[\"']", text)


def loaded_files(folder, info):
    """what the client loads of an addon folder: {lua: [paths, in order], xml: [paths], missing: [(line, text)]}: the
    .toc's lines for this client (read_toc), the XML files' <Script file> and <Include file> followed"""
    index = {_key(p): p for p in folder.rglob("*") if p.is_file()}
    out = dict(lua=[], xml=[], missing=[])
    seen = set()

    def find(base, text):
        return index.get(_key(base / text.strip().replace("\\", "/")))

    def visit(p):
        if _key(p) in seen:
            return
        seen.add(_key(p))
        if p.suffix.lower() == ".lua":
            out["lua"].append(p)
        elif p.suffix.lower() == ".xml":
            out["xml"].append(p)
            try:
                refs = _xml_refs(p)
            except OSError:
                return
            for _, text in refs:
                q = find(p.parent, text)
                if q is not None:
                    visit(q)

    if info["loads"]:
        for n, text in info["files"]:
            p = find(folder, text)
            if p is None:
                out["missing"].append((n, text))
            else:
                visit(p)
    return out


def _toc_of(folder):
    return next((t for t in [folder / f"{folder.name}.toc"] + sorted(folder.glob("*.toc")) if t.is_file()), None)


def _in_library(rel):
    return any(part.lower() in LIBRARY_DIRS for part in Path(rel).parts[:-1])


def _xml_names(text):
    return set(re.findall(r'<(?:Frame|Button|CheckButton|EditBox|ScrollFrame|Slider|StatusBar|Cooldown|GameTooltip|'
                          r'MessageFrame|SimpleHTML|Model|PlayerModel|ColorSelect|MovieFrame)\b[^>]*?\bname\s*=\s*'
                          r'["\']([A-Za-z_]\w*)["\']', text))


def _defines(info, xml_files, scans):
    """what an addon makes for others: {names: globals, fields: {(global, field)}, env: names in its setfenv tables,
    setfenv: whether it calls setfenv}"""
    out = dict(names=set(info["saved"]), fields=set(), env=set(), setfenv=False)
    if info.get("ouf"):
        out["names"].add(info["ouf"])                # oUF sets _G[<the .toc's X-oUF>]
    for xml in xml_files:
        try:
            out["names"] |= _xml_names(xml.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    for s in scans:
        out["names"] |= {name for name, _ in s["writes"]} | {name for name, _ in s["frames"]} | s["defines"]
        out["fields"] |= s["fdefines"]
        out["env"] |= s["env"]
        out["setfenv"] = out["setfenv"] or any(name == "setfenv" for name, _ in s["reads"])
    return out


def _dependencies(folder, info, seen):
    """_defines of the addons an addon's ## Dependencies / RequiredDeps / OptionalDeps name (and theirs) that are next
    to it, with their folder names"""
    out = dict(names=set(), fields=set(), env=set(), setfenv=False)
    for name in info["deps"]:
        dep = folder.parent / name
        if _key(dep) in seen or not dep.is_dir():
            continue
        seen.add(_key(dep))
        toc = _toc_of(dep)
        if toc is None:
            continue
        try:
            dinfo = read_toc(toc)
        except OSError:
            continue
        files = loaded_files(dep, dinfo)
        scans = []
        for p in files["lua"]:
            try:
                a = analyze(p, p.name)
            except OSError:
                continue
            if a["scan"] is not None:
                scans.append(a["scan"])
        for got in (_defines(dinfo, files["xml"], scans), _dependencies(dep, dinfo, seen)):
            out["names"] |= got["names"]
            out["fields"] |= got["fields"]
            out["env"] |= got["env"]
            out["setfenv"] = out["setfenv"] or got["setfenv"]
        out["names"].add(dep.name)
    return out


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
                  unresolved={}, guarded={}, libraries=0, ok=True)
    findings = []

    def add(rel, line, code, message, hint="", level="warnings"):
        findings.append((level, dict(file=rel, line=line, code=code, message=message, hint=hint)))

    def note(code, message, hint=""):
        report["notes"].append(dict(file="", line=0, code=code, message=message, hint=hint))

    # the files the client loads (the .toc's lines for this client, the XML they load): their definitions, and the
    # target's findings
    toc = _toc_of(folder) if folder is not None else None
    tocinfo = read_toc(toc) if toc else dict(interface=[], saved=[], files=[], title=None, deps=[], ouf=None, loads=True)
    if toc is not None:
        loaded = loaded_files(folder, tocinfo)
    elif folder is not None:
        loaded = dict(lua=sorted(folder.rglob("*.lua")), xml=sorted(folder.rglob("*.xml")), missing=[])
    else:
        loaded = dict(lua=[path], xml=[], missing=[])
    reached = {_key(p) for p in loaded["lua"] + loaded["xml"]}
    lua_files = list(loaded["lua"])
    if path.is_file() and path.suffix.lower() == ".lua" and _key(path) not in reached:
        lua_files.append(path)
    targets = {_key(p) for p in lua_files} if path.is_dir() else {_key(path)}

    def rel_of(p):
        try:
            return p.relative_to(folder).as_posix() if folder else p.name
        except ValueError:
            return p.name

    scans = {}
    lupa_missing = False
    for p in lua_files:
        rel = rel_of(p)
        try:
            a = analyze(p, rel)
        except OSError as e:
            if _key(p) in targets:
                add(rel, 0, "read", f"cannot read: {e}", level="errors")
            continue
        if _key(p) in targets:
            report["files"] += 1
            if not a["utf8"]:
                add(rel, 0, "encoding", "not UTF-8: the client shows its text garbled",
                    "save the file as UTF-8 (without changing the code)")
            if a["syntax"]:
                findings.append(("errors", a["syntax"]))
        if not a["lua"]:
            lupa_missing = True
        if a["scan"] is not None:
            scans[p] = a["scan"]

    own = _defines(tocinfo, loaded["xml"], scans.values())
    deps = (_dependencies(folder, tocinfo, {_key(folder)}) if folder is not None
            else dict(names=set(), fields=set(), env=set(), setfenv=False))
    defined = own["names"] | deps["names"] | ({folder.name} if folder is not None else set())
    fields_defined = own["fields"] | deps["fields"]
    env_names = own["env"] | deps["env"] if own["setfenv"] or deps["setfenv"] else set()
    env_read = {}

    if lupa_missing:
        note("no-lua", "Lua 5.1 (lupa) is not available here: the Lua files were not checked")

    for p, s in scans.items():
        if _key(p) not in targets:
            continue
        rel = rel_of(p)
        if _in_library(rel):
            report["libraries"] += 1
            continue

        def severity(name, line):             # nil here fails surely (error), or it is guarded (_flow)
            return "warnings" if (name, line) in s["guarded"] else "errors"

        def guarded(name, line):              # for apply_live: where the running game's nil is only a warning
            where = f"{rel}:{line}"
            if (name, line) in s["guarded"] and where not in report["guarded"].get(name, ()):
                report["guarded"].setdefault(name, []).append(where)

        for name, line in s["reads"]:
            if name in MISSING and MISSING[name]:
                add(rel, line, "missing-lib", f"{name} does not exist in this client", f"use {MISSING[name]}",
                    severity(name, line))
            elif name in defined or k.has(name):
                continue
            elif name in env_names:                  # from a setfenv table, not from the globals
                env_read.setdefault(name, []).append(f"{rel}:{line}")
            elif name in k.moved:                    # the global went, its namespace has it (GetSpellInfo -> C_Spell)
                add(rel, line, "moved", f"{name} is not a global of this client",
                    f"use {' or '.join(sorted(k.moved[name])[:3])}")
                guarded(name, line)
            else:
                near = k.near(name)
                if near:
                    add(rel, line, "typo", f"{name} is neither the client's nor this addon's", f"did you mean {near}?")
                else:
                    report["unresolved"].setdefault(name, []).append(f"{rel}:{line}")
                guarded(name, line)
        for base, field, line in s["fields"]:
            full = f"{base}.{field}"
            if (base, field) in fields_defined:      # the addon or one it depends on sets it (string.utf8sub = ...)
                continue
            if (base, field) in NEWER:
                add(rel, line, "lua52", f"{full} is newer than Lua 5.1 (nil in this client)", f"use {NEWER[base, field]}",
                    severity(full, line))
            elif base in LIBS:
                if field not in k.ns.get(base, ()):
                    near = k.near_field(base, field)
                    add(rel, line, "lua52", f"{full} does not exist in this client's Lua",
                        f"did you mean {base}.{near}?" if near else "", severity(full, line))
            elif (base.startswith("C_") or base in ("Enum", "Constants")) and base in k.ns and field not in k.ns[base]:
                near = k.near_field(base, field)
                add(rel, line, "api-unknown", f"{full} is not in the API manual",
                    f"did you mean {base}.{near}?" if near else "api_search it, or run `return type(" + f"{full})`")
                guarded(full, line)
        mine = re.sub(r"^\W+", "", folder.name) if folder else None     # !MyAddon writes MyAddonDB
        for name, line in s["writes"]:
            if name in tocinfo["saved"] or ALLOWED_WRITES.match(name) or (mine and len(mine) >= 3 and name.startswith(mine)):
                continue                    # the addon's own name in front: the usual way to keep a global apart
            if name in {n for n, _ in s["frames"]}:
                continue
            add(rel, line, "global-write", f"writes the global {name}",
                "forgot local? Globals are shared by every addon (and taint the Blizzard UI when it reads them)")
        for name, line in s["reads"]:
            if name in k.protected:                  # an error where it is called (a mere read is a warning)
                add(rel, line, "protected", f"{name} is protected: an addon calling it is blocked (ADDON_ACTION_FORBIDDEN)",
                    "only a secure button the player clicks may do this",
                    "errors" if (name, line) in s["called"] else "warnings")
        for what, event, line in s["events"]:
            if event in k.restricted:
                add(rel, line, "event-restricted", f"{event} is restricted: the client blocks an addon that registers it",
                    "find another event (trace shows what fires)", "errors")
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
        for n, name in loaded["missing"]:
            add(trel, n, "toc-missing", f"{name} is listed but not there", "", "errors")
        bindings = folder / "Bindings.xml"                   # the client loads it by itself
        for xml in loaded["xml"] + ([bindings] if bindings.is_file() and _key(bindings) not in reached else []):
            rel = rel_of(xml)
            if _in_library(rel):
                continue
            try:
                ET.fromstring(xml.read_bytes())
            except ET.ParseError as e:
                add(rel, e.position[0] if e.position else 0, "xml", f"not well-formed XML: {e}", "", "errors")
            except OSError:
                pass
        left = sorted((rel_of(p) for p in folder.rglob("*") if p.suffix.lower() in (".lua", ".xml") and p.is_file()
                       and _key(p) not in reached
                       and not (_key(p.parent) == _key(folder) and p.name.lower().startswith("bindings"))), key=str.lower)
        if not tocinfo["loads"]:
            note("not-loaded", f"the client does not load this addon: its ## AllowLoadGameType leaves out {GAME_TYPE}",
                 "its files were not checked")
        elif left:
            note("not-loaded", f"{len(left)} files are not loaded on this client ({GAME_TYPE}): no .toc line for it "
                               f"reaches them: {', '.join(left[:20])}" + (", ..." if len(left) > 20 else ""),
                 "they were not checked")
    elif path.is_file() and toc is not None and _key(path) not in reached:
        note("not-loaded", f"{rel_of(path)} is not loaded on this client ({GAME_TYPE}): no .toc line for it reaches it",
             "checked anyway")
    if env_read:
        note("setfenv", f"{len(env_read)} names are read from a function environment set with setfenv, not from the "
                        f"globals: {', '.join(sorted(env_read)[:20])}", "not checked")

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
                                            f"defined by this addon (or the addons it depends on): "
                                            f"{', '.join(sorted(report['unresolved'])[:20])}",
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
    an error ("undefined": nil when the line runs), a warning where a nil value is guarded there (report["guarded"])"""
    def settled(name):
        return types.get(name) not in (None, "nil")
    guarded = report.get("guarded", {})
    left, nil_guarded = {}, []
    for name, where_all in report["unresolved"].items():
        if name not in types:                              # not in the answer: still unresolved
            left[name] = where_all
            continue
        if settled(name):
            continue
        for where in where_all:
            file, _, line = where.rpartition(":")
            f = dict(file=file, line=int(line or 0), code="undefined", message=f"{name} is nil in the running game",
                     hint="a typo, or defined nowhere")
            if where in guarded.get(name, ()):
                nil_guarded.append(dict(f, hint=GUARDED_HINT))
            else:
                report["errors"].append(f)
    report["unresolved"] = left
    keep = []
    for f in report["warnings"]:
        name = f["message"].split(" ", 1)[0]
        if f["code"] in ("typo", "api-unknown", "moved") and name in types:
            if settled(name):
                continue                                   # the game has it: the manual or our lists did not
            if f"{f['file']}:{f['line']}" in guarded.get(name, ()):
                keep.append(dict(f, code="undefined", message=f"{name} is nil in the running game", hint=GUARDED_HINT))
            else:
                report["errors"].append(dict(f, code="undefined", message=f"{name} is nil in the running game"))
            continue
        keep.append(f)
    report["warnings"] = keep + nil_guarded
    report["guarded"] = {name: where for name, where in guarded.items() if name not in types}
    if not left:
        report["notes"] = [n for n in report["notes"] if n["code"] != "unresolved"]
    for level in ("errors", "warnings"):
        report[level].sort(key=lambda f: (f["file"], f["line"]))
    report["ok"] = not report["errors"]
    report["live"] = dict(checked=len(types))
    return report
