r"""Builds the "ui" content pack (content.py, uidocs.py): an index of Blizzard's own Lua and XML in the installed client,
for what the API manual does not have (TooltipDataProcessor, EncounterJournal, a template to inherit). It holds names
only: kinds, where each is defined (file, line), function parameter names, table and mixin member names, the structure of
XML frames and templates (type, name, parentKey, inherits, mixin, the keys of their children), the events a file
registers, and which Blizzard addon loads it and when. Never a line of code, a comment or a text (string literals only
as names: events, frame and template names). The sources (the client's Interface\ files, extracted from CASC by
无限图鉴's tools) are read where they lie and never copied anywhere.

Writes the program's own copy (src\wuxianworkshop\data\ui.json.gz) and the copy for the website
(dist\content\ui-<version>.json.gz with manifest.json beside it, as build_api_pack.py does for "api").

    .venv\Scripts\python.exe scripts\build_ui_pack.py --src <...\uisrc\1.60.1.71037> [--version 2026.11.24.1] [--client 1.60.1.71037]

--src is the folder holding interface\ (or the one above it, holding ui\interface\).

What this client loads, as found in the files of 1.60.1.71037 (and checked against `wuxian api-scan` of the running
game: the global functions of the files taken are there, those of the files left out are not):
- Every Blizzard addon is a folder of Interface\AddOns (FrameXML, SharedXML and the rest alike) with one .toc in it
  (lowercase on disk). The client reads <folder>_<game>.toc, else <folder>_<family>.toc, else <folder>.toc:
  blizzard_worldmap and blizzard_sharedmapdataproviders ship only their _mainline.toc; a .toc named after something
  other than its folder (blizzard_endofmatchui\blizzard_endmatchui.toc) is not read.
- Game types: 无限 is "camelot", a game type of the "mainline" family (the retail client) beside "standard" (retail
  itself), "plunderstorm", "wowlabs" and "wowhack"; "classic" is vanilla, tbc, wrath, cata and mists. The evidence:
  addons with `## AllowLoadGameType: mainline` and `## ExcludeLoadGameType: camelot` together (blizzard_garrisonui),
  files `[Family]\x.lua [ExcludeLoadGameType camelot]` next to `[Game]\x.lua [AllowLoadGameType camelot]`, tag lists
  `standard, camelot` and `classic, camelot` (camelot is in neither group); in the game, blizzard_garrisonbase
  (mainline) is loaded, the standard-only and classic-only addons are not.
- In a path, `[Family]` is Mainline and `[Game]` is Camelot (the folders mainline\ and camelot\).
- A line (a file, or a `##` directive) tagged [AllowLoadGameType a, b] counts when a or b is camelot or mainline;
  [ExcludeLoadGameType ...] drops it when it names one of them; [AllowLoad Glue] is the login screen only;
  [AllowLoadTextLocale x] only that locale (this client is zhCN). A name in a tag that is none of the game types or
  groups above is counted as ours (it may be) and reported as uncertain.
- The addon as a whole: the same with `## AllowLoadGameType` / `## ExcludeLoadGameType`; `## AllowLoad: Glue` (login
  screen only), `## DisableInCNBuild: 1` and `## OnlyBetaAndPTR: 1` are left out; `## LoadOnDemand: 1` or `## LoadWith`
  is on_demand (it exists after C_AddOns.LoadAddOn or when the game loads it), anything else always. A [Bootstrap] file
  of an on_demand addon is loaded at login all the same: the pack lists those files in "bootstrap".
- XML <Include file=> and <Script file=> are relative to the XML file's folder (or start at Interface\).

The pack (gzip JSON, "pack" and "version" first, as content.py's PACK_HEAD expects):
    {"pack": "ui", "version", "client", "built", "addons": {name: {"load": "always" | "on_demand", "files": n}},
     "left_out": {name: why: the Blizzard addons this client does not load}, "files": [path as the .toc names it],
     "bootstrap": [file index], "entries": [{"k", "n", "f", "l", ...}]}
kinds: function (p), method (n "Foo:x" / "Foo.x", o, p), table and mixin (m, mixins), frame and template (t, inh, mix,
keys, parent), event (one per event and file).
"""
import argparse
import gzip
import hashlib
import json
import posixpath
import re
import sys
import time
import xml.parsers.expat
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ROOT / "src" / "wuxianworkshop" / "data" / "ui.json.gz"
OUT = ROOT / "dist" / "content"
MIN_APP = "0.10.2"
SIZE_AIM = 6 * 1024 * 1024                       # gzip: over this, the event entries go first

GAME, FAMILY = "camelot", "mainline"
PATH_SUBST = {"[family]": "Mainline", "[game]": "Camelot"}
GROUPS = {"mainline": {"standard", "camelot", "plunderstorm", "wowlabs", "wowhack"},
          "classic": {"vanilla", "tbc", "wrath", "cata", "mists"}}
TYPES = set().union(*GROUPS.values())
OURS = {GAME} | {g for g, members in GROUPS.items() if GAME in members}
LOCALE = "zhcn"

TAG = re.compile(r"\[([A-Za-z]+):?(?:[ \t]+([^\]]*))?\]")   # [AllowLoadGameType a, b], [AllowLoadTextLocale: zhCN]
DEP_KEYS = ("dep", "deps", "dependencies", "requireddep", "requireddeps", "optionaldep", "optionaldeps", "loadwith")


# ---- .toc: which addons, which files

class Flavor:
    """the tag rules for this client; names it could not place are kept in `uncertain`"""

    def __init__(self):
        self.uncertain = Counter()

    def _names(self, text):
        return [n for n in re.split(r"[\s,]+", (text or "").strip().lower()) if n]

    def ours(self, text):
        """does a game type list name this client (or a group holding it)? unknown names: yes, and noted"""
        names = self._names(text)
        if OURS & set(names):
            return True
        unknown = [n for n in names if n not in TYPES and n not in GROUPS]
        for n in unknown:
            self.uncertain[n] += 1
        return bool(unknown)

    def excluded(self, text):
        names = self._names(text)
        for n in names:
            if n not in TYPES and n not in GROUPS:
                self.uncertain[n] += 1
        return bool(OURS & set(names))

    def line_ok(self, tags):
        """(counts for this client in the game world, [Bootstrap], [LoadIntoEnvironment x]: "global" / "secure" / None)"""
        ok, boot, env = True, False, None
        for key, value in tags:
            k = key.lower()
            if k == "allowloadgametype":
                ok = ok and self.ours(value)
            elif k == "excludeloadgametype":
                ok = ok and not self.excluded(value)
            elif k == "allowload":
                ok = ok and (value or "").strip().lower() != "glue"
            elif k == "allowloadtextlocale":
                ok = ok and LOCALE in self._names(value)
            elif k == "bootstrap":
                boot = True
            elif k == "loadintoenvironment":
                env = (value or "").strip().lower() or None
        return ok, boot, env


def read_toc(path):
    """{"directives": [(key, value, tags)], "files": [(path text, tags)]}; tags: [(name, value)]"""
    directives, files = [], []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("##"):
            m = re.match(r"##\s*([^:]+?)\s*:\s*(.*)$", s)
            if m:
                value = m.group(2)
                directives.append((m.group(1), TAG.sub("", value).strip(), TAG.findall(value)))
        elif s.startswith("#"):
            continue
        else:
            lead = []                                # tags before the path: [AllowLoadTextLocale: zhCN] x.lua
            while (m := re.match(r"(\[[^\]]*\])\s+", s)) and m.group(1).lower() not in PATH_SUBST:
                lead += TAG.findall(m.group(1))
                s = s[m.end():]
            m = re.match(r"(\S+)(.*)$", s)
            if m:
                files.append((m.group(1), lead + TAG.findall(m.group(2))))
    return dict(directives=directives, files=files)


def toc_path(text):
    """a file named in a .toc, as the client takes it: [Family] / [Game] filled in, forward slashes"""
    out = text.replace("\\", "/")
    for token, folder in PATH_SUBST.items():
        out = re.sub(re.escape(token), folder, out, flags=re.I)
    return out


def choose_toc(folder):
    """the .toc this client reads in an addon folder (see the module docstring), or None"""
    tocs = {p.name.lower(): p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".toc"}
    base = folder.name.lower()
    for name in (f"{base}_{GAME}.toc", f"{base}_{FAMILY}.toc", f"{base}.toc"):
        if name in tocs:
            return tocs[name]
    return None


def addon_names(addons_dir, tocs):
    """folder (lowercase) -> the addon's name as the client spells it: the spelling other .toc files (dependencies,
    titles, the names of its own files and saved variables) and the UI's own files (paths, LoadAddOn) use most, a
    lowercase one only when there is no other"""
    seen = defaultdict(Counter)
    folders = {p.name.lower() for p in addons_dir.iterdir() if p.is_dir()}

    def credit(token, weight=1):
        """the token, or the part of it before an underscore, spelling a folder's name"""
        cuts = [len(token)] + [i for i, c in enumerate(token) if c == "_" or (c.isupper() and i)]
        for cut in cuts:
            name = token[:cut]
            if name.lower() in folders and name != name.lower():
                seen[name.lower()][name] += weight

    for toc in tocs.values():
        for key, value, _tags in toc["directives"]:
            k = key.lower().replace(" ", "")
            if k in DEP_KEYS or k == "title" or k.startswith("savedvariables"):
                for name in re.split(r"[,\s]+", value):
                    credit(name, 3 if k == "title" and name.lower() in folders else 1)
            if k == "title" and " " in value.strip():  # "Blizzard Legacy System": Blizzard_LegacySystem
                words = value.split()
                credit(words[0] + "_" + "".join(words[1:]))
                credit("".join(words))
        for text, _tags in toc["files"]:
            credit(posixpath.basename(text.replace("\\", "/")).rsplit(".", 1)[0])
    for path in addons_dir.rglob("*"):
        if path.suffix.lower() in (".lua", ".xml") and path.is_file():
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            for m in re.finditer(r"(?i)\b(?:blizzard|deprecated)_[a-z0-9_]+", text):
                credit(m.group(0))
    out = {}
    for folder in folders:
        spellings = seen.get(folder)
        cased = [(n, c) for n, c in spellings.items() if n != n.lower()] if spellings else []
        out[folder] = max(cased, key=lambda x: (x[0] != x[0].upper(), x[1], x[0]))[0] if cased else folder
    return out


# ---- Lua: a tokenizer and a shallow parser

KEYWORDS = frozenset("and break do else elseif end false for function goto if in local nil not or repeat return then "
                     "true until while".split())
LUA_TOKEN = re.compile(r"""
  (?P<ws>[ \t\r\f\v]+)
| (?P<nl>\n)
| (?P<lcom>--\[=*\[)
| (?P<com>--[^\n]*)
| (?P<lstr>\[=*\[)
| (?P<str>"(?:[^"\\\n]|\\(?:\r\n|.|\n))*"|'(?:[^'\\\n]|\\(?:\r\n|.|\n))*')
| (?P<num>0[xX][0-9a-fA-F]*(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?\d+)?|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
| (?P<name>[A-Za-z_][A-Za-z0-9_]*)
| (?P<op>\.\.\.|\.\.|==|~=|<=|>=|::|<<|>>|//|[-+*/%^\#&~|<>=(){}\[\];:,.])
""", re.X | re.S)
NAME, KW, STR, NUM, OP = "name", "kw", "str", "num", "op"


def tokens(src, line=1):
    """[(kind, value, line)] of a Lua chunk: strings (value: their text between the quotes or brackets), names,
    keywords, numbers, operators; comments and whitespace dropped; lines 1-based (from `line`)"""
    out, pos, n = [], 0, len(src)
    match = LUA_TOKEN.match
    while pos < n:
        m = match(src, pos)
        if not m:                                    # a stray character (or an unclosed quote): skip it
            if src[pos] == "\n":
                line += 1
            pos += 1
            continue
        kind, text = m.lastgroup, m.group()
        end = m.end()
        if kind == "nl":
            line += 1
        elif kind == "ws" or kind == "com":
            pass
        elif kind in ("lcom", "lstr"):
            level = text.count("=")
            close = src.find("]" + "=" * level + "]", end)
            stop = n if close < 0 else close + level + 2
            if kind == "lstr":
                out.append((STR, src[end:close if close >= 0 else n], line))
            line += src.count("\n", pos, stop)
            end = stop
        elif kind == "str":
            out.append((STR, text[1:-1], line))
            line += text.count("\n")
        elif kind == "name":
            out.append((KW if text in KEYWORDS else NAME, text, line))
        else:
            out.append((OP if kind == "op" else NUM, text, line))
        pos = end
    return out


EVENT = re.compile(r"[A-Z][A-Z0-9_]+$")
REGISTER_ONE = frozenset(("RegisterEvent", "RegisterUnitEvent", "RegisterFrameEventAndCallback",
                          "RegisterFrameEventAndCallbackWithHandle"))
REGISTER_LIST = frozenset(("RegisterFrameForEvents", "RegisterFrameForUnitEvents"))
MIXIN_MAKERS = frozenset(("CreateFromMixins", "CreateAndInitFromMixin", "Mixin", "CreateFromSecureMixins",
                          "SecureMixin", "CreateSecureMixinCopy"))
OPENERS = frozenset(("do", "if", "repeat"))
GLOBAL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


class Scope:
    __slots__ = ("locals", "depth", "ctors")

    def __init__(self, names=()):
        self.locals = set(names)
        self.depth = 0                               # ( [ { open in this function's body
        self.ctors = []                              # [(depth, table name)] table constructors of a global table


class LuaFile:
    """what one Lua chunk defines and registers: entries (in order) and the members it gives global tables"""

    def __init__(self, members, secure=False):
        self.members = members                       # owner -> set(member), shared by all files
        self.secure = secure                         # in the secure environment: nothing is _G's (until the file
                                                     # calls SwapToGlobalEnvironment() at its top level)
        self.entries = []
        self.events = {}                             # event -> first line
        self.lists = {}                              # a name -> [strings] (local or global tables of strings)
        self.list_refs = []                          # (name, line, secure): RegisterFrameForEvents(f, name)

    # -- helpers over the token list
    def _is(self, i, kind, value=None):
        t = self.t[i] if 0 <= i < len(self.t) else None
        return t is not None and t[0] == kind and (value is None or t[1] == value)

    def _local(self, name):
        return any(name in s.locals for s in self.scopes)

    def _chain(self, i):
        """Name {. Name} from i: ([names], index after)"""
        names = [self.t[i][1]]
        j = i + 1
        while self._is(j, OP, ".") and self._is(j + 1, NAME):
            names.append(self.t[j + 1][1])
            j += 2
        return names, j

    def _match(self, i):
        """the index of the bracket closing the one at i"""
        depth = 0
        for j in range(i, len(self.t)):
            kind, value, _ = self.t[j]
            if kind == OP and value in "([{":
                depth += 1
            elif kind == OP and value in ")]}":
                depth -= 1
                if depth == 0:
                    return j
        return len(self.t) - 1

    def _strings(self, i):
        """the strings of a constructor { "A", "B" } at i, or None when it holds anything else"""
        j, out = i + 1, []
        while j < len(self.t):
            kind, value, _ = self.t[j]
            if kind == STR:
                out.append(value)
            elif kind == OP and value in (",", ";"):
                pass
            elif kind == OP and value == "}":
                return out or None
            else:
                return None
            j += 1
        return None

    def _args(self, i):
        """the dotted names passed alone as arguments of the call whose ( is at i"""
        out, end, j = [], self._match(i), i + 1
        while j < end:
            if self._is(j, NAME) and (self._is(j - 1, OP, "(") or self._is(j - 1, OP, ",")) and j - 1 >= i:
                names, k = self._chain(j)
                if self._is(k, OP, ",") or k == end:
                    out.append(".".join(names))
                j = k
                continue
            if self._is(j, OP) and self.t[j][1] in "([{":
                j = self._match(j) + 1
                continue
            j += 1
        return out

    def _params(self, i):
        """the parameter names of a function whose ( is at i: ([names], index after the ))"""
        out, j = [], i + 1
        while j < len(self.t) and not self._is(j, OP, ")"):
            kind, value, _ = self.t[j]
            if kind == NAME or (kind == OP and value == "..."):
                out.append(value)
            j += 1
        return out, j + 1

    def _add(self, entry):
        if not self.secure:
            self.entries.append(entry)

    def _member(self, owner, name, func):
        if not self.secure:
            self.members[owner].add(name + "()" if func else name)

    def _event(self, name, line):
        if not self.secure and EVENT.match(name):
            self.events.setdefault(name, line)

    # -- the parse
    def parse(self, src, line=1, events_only=False):
        self.t = tokens(src, line)
        self.scopes = [Scope()]
        blocks = []
        pending = None                               # a definition waiting for the function at the next token
        t, n = self.t, len(self.t)
        i = 0
        while i < n:
            kind, value, line = t[i]
            scope = self.scopes[-1]
            prev = t[i - 1] if i else (None, None, 0)
            if kind == KW:
                if value == "function":
                    i = self._function(i, prev, pending, blocks, events_only)
                    pending = None
                    continue
                if value in OPENERS:
                    blocks.append(value)
                elif value in ("end", "until"):
                    if blocks and blocks.pop() == "function" and len(self.scopes) > 1:
                        self.scopes.pop()
                elif value == "local" and not self._is(i + 1, KW, "function"):
                    names, j = [], i + 1
                    while self._is(j, NAME):
                        names.append(t[j][1])
                        j += 1
                        if not self._is(j, OP, ","):
                            break
                        j += 1
                    scope.locals.update(names)
                    if len(names) == 1 and self._is(j, OP, "=") and self._is(j + 1, OP, "{"):
                        strings = self._strings(j + 1)
                        if strings:
                            self.lists[names[0]] = strings
                    i = j
                    continue
                elif value == "for":
                    j = i + 1
                    while self._is(j, NAME):
                        scope.locals.add(t[j][1])
                        j += 1
                        if not self._is(j, OP, ","):
                            break
                        j += 1
                i += 1
                continue
            if kind == OP:
                if value in "([{":
                    scope.depth += 1
                    if value == "{" and pending and pending[0] == "ctor":
                        scope.ctors.append((scope.depth, pending[1]))
                        pending = None
                elif value in ")]}":
                    if value == "}" and scope.ctors and scope.ctors[-1][0] == scope.depth:
                        scope.ctors.pop()
                    scope.depth = max(0, scope.depth - 1)
                i += 1
                continue
            if kind != NAME:
                i += 1
                continue
            # a field of a global table's constructor: Foo = { a = 1, b = function() end }
            if (scope.ctors and scope.ctors[-1][0] == scope.depth and prev[0] == OP and prev[1] in ("{", ",", ";")
                    and self._is(i + 1, OP, "=") and not events_only):
                self._member(scope.ctors[-1][1], value, self._is(i + 2, KW, "function"))
                i += 2
                continue
            # events and frames made in Lua, anywhere
            if self._is(i + 1, OP, "(") and not (prev[0] == KW and prev[1] == "function"):
                if value == "SwapToGlobalEnvironment" and len(self.scopes) == 1:
                    self.secure = False
                elif value in REGISTER_ONE and self._is(i + 2, STR):
                    self._event(t[i + 2][1], line)
                elif value in REGISTER_LIST:
                    self._register_list(i + 1)
                elif value == "CreateFrame" and not events_only:
                    self._create_frame(i + 1)
            # an assignment statement: Name {. Name} = ...
            if (scope.depth == 0 and not (prev[0] == OP and prev[1] in (".", ":", ",", "::"))
                    and not (prev[0] == KW and prev[1] in ("local", "function", "for", "goto"))):
                names, j = self._chain(i)
                if self._is(j, OP, "="):
                    if not events_only:
                        pending = self._assign(names, j + 1, line)
                    else:
                        self._list_assign(names, j + 1)
                    i = j + 1
                    continue
            i += 1
        for name, line, secure in self.list_refs:
            for event in self.lists.get(name, ()):
                if not secure and EVENT.match(event):
                    self.events.setdefault(event, line)
        return self

    def _function(self, i, prev, pending, blocks, events_only):
        """a function statement or expression at i: its entry, then its body as a new scope; the index after ( )"""
        t = self.t
        j, names, method = i + 1, None, False
        if self._is(j, NAME):
            names, j = self._chain(j)
            if self._is(j, OP, ":") and self._is(j + 1, NAME):
                names.append(t[j + 1][1])
                method = True
                j += 2
        params, after = self._params(j) if self._is(j, OP, "(") else ([], j)
        line = t[i][2]
        top = len(self.scopes) == 1
        if prev[0] == KW and prev[1] == "local" and names:
            self.scopes[-1].locals.add(names[0])
        elif names and not events_only:
            if names[0] == "_G" and len(names) > 1:
                names = names[1:]
            if not self._local(names[0]):
                if len(names) == 1:
                    if top:
                        self._add(dict(k="function", n=names[0], l=line, p=params))
                else:
                    owner, member = ".".join(names[:-1]), names[-1]
                    self._member(owner, member, True)
                    if top:
                        self._add(dict(k="method", n=owner + (":" if method else ".") + member, l=line, p=params,
                                       o=owner))
        elif pending and pending[0] == "func" and prev[0] == OP and prev[1] == "=":
            _, kind, name, owner, pline = pending
            entry = dict(k=kind, n=name, l=pline, p=params)
            if owner:
                entry["o"] = owner
            self._add(entry)
        blocks.append("function")
        self.scopes.append(Scope(params + (["self"] if method else [])))
        return after

    def _assign(self, names, k, line):
        """Name {. Name} = <t[k] ...>: an entry or a member; what the next tokens owe it (a constructor's fields, a
        function's parameters) as `pending`"""
        t = self.t
        if names[0] == "_G" and len(names) > 1:
            names = names[1:]
        if self._local(names[0]):
            self._list_assign(names, k)
            return None
        top = len(self.scopes) == 1
        func = self._is(k, KW, "function")
        if len(names) > 1:
            owner, member = ".".join(names[:-1]), names[-1]
            self._member(owner, member, func)
            self._list_assign(names, k)
            if func and top:
                return ("func", "method", owner + "." + member, owner, line)
            return None
        name = names[0]
        if self._is(k, OP, "{"):
            strings = self._strings(k)
            if strings:
                self.lists[name] = strings
            if top:
                self._table(name, line, [])
                return ("ctor", name)
            return None
        if not top:
            return None
        if func:
            return ("func", "function", name, None, line)
        if self._is(k, NAME):
            callee, after = self._chain(k)
            callee = ".".join(callee)
            if callee in MIXIN_MAKERS and self._is(after, OP, "("):
                self._table(name, line, self._args(after))
            elif callee == "CreateFrame" and self._is(after, OP, "("):
                if not (self._is(after + 1, STR) and self._is(after + 2, OP, ",") and self._is(after + 3, STR)
                        and self.t[after + 3][1] == name):
                    self._add(dict(k="frame", n=name, l=line, t=t[after + 1][1] if self._is(after + 1, STR) else "Frame",
                                   inh=[], mix=[], keys={}, parent=None))
            elif callee == name and self._is(after, KW, "or") and self._is(after + 1, OP, "{"):
                self._table(name, line, [])
                return ("ctor", name)
            elif callee == "setmetatable" and self._is(after, OP, "(") and self._is(after + 1, OP, "{"):
                self._table(name, line, [])
            elif name.endswith("Mixin") and callee.endswith("Mixin") and not (
                    self._is(after, OP) and t[after][1] in ("(", ":", "[")):
                self._table(name, line, [callee])
        return None

    def _list_assign(self, names, k):
        if self._is(k, OP, "{"):
            strings = self._strings(k)
            if strings:
                self.lists[".".join(names)] = strings

    def _table(self, name, line, mixins):
        self._add(dict(k="mixin" if name.endswith("Mixin") else "table", n=name, l=line, mixins=mixins))

    def _register_list(self, i):
        """FrameUtil.RegisterFrameForEvents(frame, { "A", "B" } or a name of such a table)"""
        end, j, depth = self._match(i), i + 1, 0
        while j < end:                               # the second argument
            kind, value, _ = self.t[j]
            if kind == OP and value in "([{":
                depth += 1
            elif kind == OP and value in ")]}":
                depth -= 1
            elif kind == OP and value == "," and depth == 0:
                break
            j += 1
        j += 1
        if self._is(j, OP, "{"):
            for event in self._strings(j) or ():
                self._event(event, self.t[j][2])
        elif self._is(j, NAME):
            names, _ = self._chain(j)
            self.list_refs.append((".".join(names), self.t[j][2], self.secure))

    def _create_frame(self, i):
        """CreateFrame("Type", "Name", parent, "Template, Template") with a literal global name"""
        t = self.t
        if not (self._is(i + 1, STR) and self._is(i + 2, OP, ",") and self._is(i + 3, STR)
                and (self._is(i + 4, OP, ",") or self._is(i + 4, OP, ")"))):
            return                                   # no name, or one made at run time ("ChatFrame" .. i)
        name = t[i + 3][1]
        if not GLOBAL_NAME.match(name):
            return
        parent = inh = None
        j = i + 4
        if self._is(j, OP, ",") and self._is(j + 1, NAME):
            names, j = self._chain(j + 1)
            parent = ".".join(names) if self._is(j, OP, ",") or self._is(j, OP, ")") else None
        elif self._is(j, OP, ","):
            j += 2
        if self._is(j, OP, ",") and self._is(j + 1, STR) and (self._is(j + 2, OP, ",") or self._is(j + 2, OP, ")")):
            inh = [x.strip() for x in t[j + 1][1].split(",") if x.strip()]
        self._add(dict(k="frame", n=name, l=t[i + 3][2], t=t[i + 1][1], inh=inh or [], mix=[], keys={},
                       parent=parent))


# ---- XML

STRUCTURE = frozenset("""Ui Include Script Scripts Layers Layer Frames Anchors Anchor Size AbsDimension RelDimension Offset
AbsInset RelInset AbsValue Color Shadow TexCoords KeyValues KeyValue KeyValye Attributes Attribute Animations Member Mixin
Mixins Origin NormalFont HighlightFont DisabledFont HitRectInsets BarColor ScrollChild PushedTextOffset TextInsets
ViewInsets Rect MinColor MaxColor Gradient ResizeBounds ControlPoint ControlPoints HighlightColor FontHeight FogColor
Binding Bindings ModifiedClick ScopedModifier ForbiddenAspect ForbiddenAspects Insert MaskedTextures MaskedTexture
Backdrop BackgroundInsets EdgeSize TileSize""".split())
PARENT = re.compile(r"\$parent", re.I)


class XmlFile:
    """the objects of one XML file (frames, templates), its includes and the inline handlers' code (for events)"""

    def __init__(self):
        self.entries = []
        self.includes = []                           # (file attribute, line)
        self.code = []                               # (line, inline Lua text)
        self.error = None

    def parse(self, data):
        p = xml.parsers.expat.ParserCreate()
        stack = []                                   # [tag, obj or None, in_scripts, text parts, text line]

        def start(tag, attrs):
            line = p.CurrentLineNumber
            in_scripts = bool(stack) and (stack[-1][0] == "Scripts" or stack[-1][2])
            if tag in ("Include", "Script") and attrs.get("file"):
                self.includes.append((attrs["file"], line))
            obj = None
            if tag not in STRUCTURE and not in_scripts:
                parent = next((s[1] for s in reversed(stack) if s[1] is not None), None)
                obj = self._object(tag, attrs, line, parent)
            stack.append([tag, obj, in_scripts or tag == "Scripts", [], None])

        def text(data):
            top = stack[-1] if stack else None
            if top and (top[2] or top[0] == "Script"):
                if top[4] is None:
                    top[4] = p.CurrentLineNumber
                top[3].append(data)

        def end(tag):
            top = stack.pop()
            if top[3] and "".join(top[3]).strip():
                self.code.append((top[4], "".join(top[3])))
            obj = top[1]
            if obj is not None:
                self._close(obj)

        p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, text
        try:
            p.Parse(data, True)
        except xml.parsers.expat.ExpatError as e:
            self.error = str(e)
        return self

    def _object(self, tag, attrs, line, parent):
        virtual = (attrs.get("virtual") or "").lower() == "true" or (attrs.get("intrinsic") or "").lower() == "true"
        in_template = parent is not None and (parent["virtual"] or parent["in_template"])
        raw = attrs.get("name")
        name = raw
        if raw and PARENT.search(raw):
            up, base = parent, None
            while up is not None:
                if up["virtual"] or up["in_template"]:
                    break
                if up["name"]:
                    base = up["name"]
                    break
                up = up["parent"]
            name = PARENT.sub(base, raw) if base else None
        named_parent = parent
        while named_parent is not None and not named_parent["name"]:
            named_parent = named_parent["parent"]
        return dict(tag=tag, name=name, raw=raw, line=line, parent=parent, virtual=virtual, in_template=in_template,
                    key=attrs.get("parentKey"), array=attrs.get("parentArray"),
                    inh=[x.strip() for x in (attrs.get("inherits") or "").split(",") if x.strip()],
                    mix=[x.strip() for x in ((attrs.get("mixin") or "") + "," + (attrs.get("secureMixin") or "")).split(",")
                         if x.strip()],
                    parent_name=(named_parent["name"] if named_parent is not None and not in_template
                                 else (attrs.get("parent") if attrs.get("parent") and "$" not in attrs["parent"] else None)),
                    keys={})

    def _close(self, obj):
        parent = obj["parent"]
        if parent is not None:
            if obj["key"]:
                parent["keys"][obj["key"]] = obj["tag"]
                for k, v in obj["keys"].items():
                    parent["keys"][obj["key"] + "." + k] = v
            if obj["array"]:
                parent["keys"].setdefault(obj["array"] + "[]", obj["tag"])
        if obj["virtual"] and obj["raw"] and not obj["in_template"]:
            kind = "template"
        elif obj["name"] and not obj["virtual"] and not obj["in_template"] and not re.search(r"[\s$]", obj["name"]):
            kind = "frame"
        else:
            return
        self.entries.append(dict(k=kind, n=obj["name"] or obj["raw"], l=obj["line"], t=obj["tag"], inh=obj["inh"],
                                 mix=obj["mix"], keys=dict(sorted(obj["keys"].items())),
                                 parent=obj["parent_name"] if kind == "frame" else None))


# ---- the build

def find_interface(src):
    """the folder holding addons\\ (src\\interface, or src\\ui\\interface)"""
    src = Path(src)
    for cand in (src, src / "interface", src / "ui" / "interface"):
        if (cand / "addons").is_dir() and cand.name.lower() == "interface":
            return cand
    raise SystemExit(f"{src}: no interface\\addons in it (give the folder holding interface\\)")


class Builder:
    def __init__(self, src):
        self.interface = find_interface(src)
        self.addons_dir = self.interface / "addons"
        self.flavor = Flavor()
        self.files, self.index = [], {}              # pack paths; their index by lowercase path
        self.file_addon = []                         # the addon (folder, lowercase) of each file
        self.bootstrap = []
        self.entries = []
        self.members = defaultdict(set)
        self.missing = []                            # (pack path, named by)
        self.left_out = {}                           # addon folder -> why
        self.addons = {}
        self.xml_errors = []
        self.names = {}

    def disk(self, pack_path):
        """Interface/AddOns/X/y.lua -> the file on disk (lowercase)"""
        parts = pack_path.split("/")[1:]
        return self.interface.joinpath(*[p.lower() for p in parts])

    def pack_path(self, rel, base):
        """a path as a .toc or an XML file names it (base: the pack folder it is relative to) -> the pack's spelling"""
        rel = rel.replace("\\", "/").strip()
        if rel.lower().startswith("interface/"):
            parts = rel.split("/")
            if len(parts) > 2 and parts[1].lower() == "addons":
                parts[0], parts[1] = "Interface", "AddOns"
                if len(parts) > 3:
                    parts[2] = self.names.get(parts[2].lower(), parts[2])
            return posixpath.normpath("/".join(parts))
        return posixpath.normpath(posixpath.join(base, rel))

    def build(self):
        tocs = {}
        chosen = {}
        for folder in sorted(p for p in self.addons_dir.iterdir() if p.is_dir()):
            toc = choose_toc(folder)
            if toc is None:
                self.left_out[folder.name.lower()] = "no .toc for this client"
                continue
            chosen[folder.name.lower()] = toc
            tocs[folder.name.lower()] = read_toc(toc)
        self.names = addon_names(self.addons_dir, tocs)
        for folder, toc in tocs.items():
            self.addon(folder, toc)
        for e in self.entries:                       # members defined anywhere
            if e["k"] in ("table", "mixin"):
                got = self.members.get(e["n"], set())
                e["m"] = sorted(m for m in got if not (not m.endswith("()") and m + "()" in got))
        return self

    def addon(self, folder, toc):
        d = {}
        for key, value, tags in toc["directives"]:
            if self.flavor.line_ok(tags)[0]:
                d.setdefault(key.lower().replace(" ", ""), value)
        why = None
        if "allowloadgametype" in d and not self.flavor.ours(d["allowloadgametype"]):
            why = "game type " + d["allowloadgametype"]
        elif "excludeloadgametype" in d and self.flavor.excluded(d["excludeloadgametype"]):
            why = "excluded for " + d["excludeloadgametype"]
        elif d.get("allowload", "game").lower() == "glue":
            why = "login screen only"
        elif d.get("disableincnbuild") == "1":
            why = "DisableInCNBuild"
        elif d.get("onlybetaandptr") == "1":
            why = "OnlyBetaAndPTR"
        if why:
            self.left_out[folder] = why
            return
        name = self.names[folder]
        load = "on_demand" if d.get("loadondemand") == "1" or "loadwith" in d else "always"
        secure_env = d.get("usesecureenvironment") == "1"
        self.addons[name] = dict(load=load, files=0)
        base = f"Interface/AddOns/{name}"
        for text, tags in toc["files"]:
            ok, boot, env = self.flavor.line_ok(tags)
            if ok:
                secure = (env or ("secure" if secure_env else "global")) == "secure"
                self.take(self.pack_path(toc_path(text), base), f"{base}/{folder}.toc", boot and load == "on_demand",
                          folder, secure)

    def take(self, path, by, boot, folder, secure=False, depth=0):
        """index one file the client loads (and the files an XML file includes); secure: it is loaded into the
        secure environment (its globals are not _G's: nothing of it is indexed, see LuaFile)"""
        key = path.lower()
        if key in self.index or depth > 20:
            return
        disk = self.disk(path)
        if not disk.is_file():
            self.missing.append((path, by))
            return
        f = len(self.files)
        self.index[key] = f
        self.files.append(path)
        parts = path.split("/")
        owner = parts[2] if len(parts) > 3 else self.names.get(folder, folder)
        if owner in self.addons:
            self.addons[owner]["files"] += 1
        if boot:
            self.bootstrap.append(f)
        data = disk.read_bytes()
        if key.endswith(".lua"):
            self.lua(data.decode("utf-8-sig", errors="replace"), f, secure)
        elif key.endswith(".xml"):
            x = XmlFile().parse(data)
            if x.error:
                self.xml_errors.append((path, x.error))
            if secure:
                x.entries, x.code = [], []
            for e in x.entries:
                e["f"] = f
                self.entries.append(e)
            events = {}
            for line, code in x.code:
                for event, l in LuaFile(self.members).parse(code, line, events_only=True).events.items():
                    events.setdefault(event, l)
            self.events(events, f)
            folder_path = posixpath.dirname(path)
            for rel, line in x.includes:
                self.take(self.pack_path(rel, folder_path), f"{path}:{line}", boot, folder, secure, depth + 1)

    def lua(self, src, f, secure=False):
        lf = LuaFile(self.members, secure).parse(src)
        for e in lf.entries:
            e["f"] = f
            self.entries.append(e)
        self.events(lf.events, f)

    def events(self, events, f):
        for event, line in sorted(events.items(), key=lambda x: x[1]):
            self.entries.append(dict(k="event", n=event, f=f, l=line))

    def pack(self, version, client):
        order = ("k", "n", "f", "l")
        entries = []
        seen = set()
        for e in self.entries:
            ident = (e["k"], e["n"], e["f"], e["l"])
            if ident in seen:
                continue
            seen.add(ident)
            entries.append({**{k: e[k] for k in order}, **{k: v for k, v in e.items() if k not in order}})
        return dict(pack="ui", version=version, client=client, built=time.strftime("%Y-%m-%d"),
                    addons=dict(sorted(self.addons.items(), key=lambda x: x[0].lower())),
                    left_out={self.names.get(f, f): why for f, why in sorted(self.left_out.items())},
                    files=self.files, bootstrap=self.bootstrap, entries=entries)


def compress(pack):
    return gzip.compress(json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9, mtime=0)


def fit(pack):
    """the gzip bytes, under SIZE_AIM: the event entries dropped when over"""
    raw = compress(pack)
    if len(raw) > SIZE_AIM:
        pack["entries"] = [e for e in pack["entries"] if e["k"] != "event"]
        pack["events_dropped"] = True
        raw = compress(pack)
    return raw


def publish(raw, version, bundled=BUNDLED, out=OUT):
    """the program's copy, the website's copy, and the "ui" entry of the website's manifest.json"""
    bundled.parent.mkdir(parents=True, exist_ok=True)
    bundled.write_bytes(raw)
    out.mkdir(parents=True, exist_ok=True)
    name = f"ui-{version}.json.gz"
    (out / name).write_bytes(raw)
    mpath = out / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {"packs": {}}
    manifest.setdefault("packs", {})["ui"] = dict(version=version, file=name, size=len(raw),
                                                   sha256=hashlib.sha256(raw).hexdigest(), min_app=MIN_APP)
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out / name


def client_of(src):
    m = re.search(r"\d+\.\d+\.\d+\.\d+", str(Path(src).resolve()))
    return m.group(0) if m else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, help="the folder holding interface\\ (the client's UI files, extracted)")
    ap.add_argument("--version", default=time.strftime("%Y.%m.%d") + ".1", help="the pack's version (default: today.1)")
    ap.add_argument("--client", help="the client build the files are of (default: the 1.60.1.x in --src)")
    args = ap.parse_args(argv)
    client = args.client or client_of(args.src)
    if not client:
        raise SystemExit("--client: the client build (no 1.60.1.x in --src)")
    started = time.time()
    b = Builder(args.src).build()
    pack = b.pack(args.version, client)
    raw = fit(pack)
    where = publish(raw, args.version)
    kinds = Counter(e["k"] for e in pack["entries"])
    loads = Counter(a["load"] for a in pack["addons"].values())
    print(f"ui {args.version}: client {client}, {len(pack['addons'])} addons ({loads['always']} always, "
          f"{loads['on_demand']} on_demand), {len(b.left_out)} left out, {len(pack['files'])} files "
          f"({len(pack['bootstrap'])} bootstrap), {len(pack['entries'])} entries: "
          + ", ".join(f"{k} {n}" for k, n in kinds.most_common())
          + f"; {len(raw) / 1024:.0f} KB -> {BUNDLED} and {where} ({time.time() - started:.0f} s)")
    if pack.get("events_dropped"):
        print(f"over {SIZE_AIM // 1024 // 1024} MB: the event entries were left out")
    if b.missing:
        print(f"{len(b.missing)} files named but not extracted:")
        for path, by in b.missing:
            print(f"  {path}  (named by {by})")
    if b.xml_errors:
        print(f"{len(b.xml_errors)} XML files that do not parse to the end:")
        for path, error in b.xml_errors:
            print(f"  {path}: {error}")
    if b.flavor.uncertain:
        print("game type names not placed (counted as this client's): "
              + ", ".join(f"{n} ({c})" for n, c in b.flavor.uncertain.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
