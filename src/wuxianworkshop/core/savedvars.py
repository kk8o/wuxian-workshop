r"""Read the SavedVariables files the client writes (WTF\Account\<account>\SavedVariables\<addon>.lua) without running
them: they are Lua assignments of literal values, `Name = { ["key"] = value, ... }`, and this parses only that subset
(tables, strings, numbers, booleans, nil, comments). Anything else is a ParseError.

Tables come back as dicts; their positional entries get the keys 1, 2, ... as in Lua (a table of only those is still a
dict). Strings are bytes in Lua: they come back decoded as UTF-8, with U+FFFD for bytes that are not. The client writes
these files when the player logs out, reloads the UI or exits, so they are as of then; nothing here writes them (the
client would overwrite any change at its next logout).
"""
import re
from pathlib import Path

MAX_DEPTH = 64
_SPACE = re.compile(rb"[ \t\r\n\f\v]*")
_NAME = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(rb"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_SPECIAL = re.compile(rb"-?(?:inf|nan)(?:\(ind\))?", re.I)      # what a C runtime prints for those doubles
_LONG_OPEN = re.compile(rb"\[(=*)\[")
_DECIMAL = re.compile(rb"\d{1,3}")
_HEX2 = re.compile(rb"[0-9a-fA-F]{2}")
_UNICODE = re.compile(rb"\{([0-9a-fA-F]{1,8})\}")
_ESCAPES = {ord("a"): 7, ord("b"): 8, ord("f"): 12, ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("v"): 11,
            ord("\\"): 92, ord('"'): 34, ord("'"): 39, ord("\n"): 10, ord("\r"): 10}


class ParseError(ValueError):
    """the text is not a SavedVariables file (or uses Lua that this reader does not take)"""


class _Reader:
    def __init__(self, data):
        self.s, self.i = data, 0

    def fail(self, what):
        line = self.s.count(b"\n", 0, self.i) + 1
        raise ParseError(f"{what} at line {line}")

    def skip(self):
        """whitespace and comments"""
        s = self.s
        while True:
            self.i = _SPACE.match(s, self.i).end()
            if not s.startswith(b"--", self.i):
                return
            m = _LONG_OPEN.match(s, self.i + 2)
            if m:                                            # --[[ ... ]] / --[==[ ... ]==]
                end = s.find(b"]" + m.group(1) + b"]", m.end())
                if end < 0:
                    self.fail("unfinished comment")
                self.i = end + len(m.group(1)) + 2
            else:
                end = s.find(b"\n", self.i)
                self.i = len(s) if end < 0 else end + 1

    def value(self, depth):
        self.skip()
        s, i = self.s, self.i
        if i >= len(s):
            self.fail("a value expected, end of file found")
        c = s[i]
        if c == 0x7B:                                        # {
            return self.table(depth + 1)
        if c in (0x22, 0x27):                                # " '
            return self.string()
        m = _LONG_OPEN.match(s, i)
        if m:                                                # [[ ... ]]
            end = s.find(b"]" + m.group(1) + b"]", m.end())
            if end < 0:
                self.fail("unfinished long string")
            body = s[m.end():end]
            if body.startswith(b"\r\n"):
                body = body[2:]
            elif body.startswith(b"\n"):
                body = body[1:]
            self.i = end + len(m.group(1)) + 2
            return body.decode("utf-8", "replace")
        for word, val in ((b"true", True), (b"false", False), (b"nil", None)):
            if s.startswith(word, i) and not _NAME.match(s, i + len(word)):
                self.i = i + len(word)
                return val
        m = _SPECIAL.match(s, i)
        if m and not _NAME.match(s, m.end()):
            self.i = m.end()
            text = m.group(0).lower()
            return float("nan") if b"nan" in text else float("-inf" if text.startswith(b"-") else "inf")
        m = _NUMBER.match(s, i)
        if m:
            self.i = m.end()
            text = m.group(0).decode()
            if "x" in text.lower():
                return int(text, 16)
            if any(ch in text for ch in ".eE"):
                return float(text)
            return int(text)
        self.fail(f"unexpected {s[i:i + 12]!r}")

    def string(self):
        s, quote = self.s, self.s[self.i]
        i, out = self.i + 1, bytearray()
        while True:
            if i >= len(s):
                self.fail("unfinished string")
            c = s[i]
            if c == quote:
                self.i = i + 1
                return out.decode("utf-8", "replace")
            if c == 0x0A:
                self.fail("line break in a string")
            if c != 0x5C:                                    # not a backslash
                out.append(c)
                i += 1
                continue
            i += 1
            if i >= len(s):
                self.fail("unfinished string")
            e = s[i]
            if e in _ESCAPES:
                out.append(_ESCAPES[e])
                i += 2 if e == 0x0D and s[i + 1:i + 2] == b"\n" else 1      # backslash + CRLF: one line break
            elif 0x30 <= e <= 0x39:                          # \ddd
                m = _DECIMAL.match(s, i)
                n = int(m.group(0))
                if n > 255:
                    self.fail("escape \\%d out of range" % n)
                out.append(n)
                i = m.end()
            elif e == ord("x"):                              # \xXX
                m = _HEX2.match(s, i + 1)
                if not m:
                    self.fail("bad \\x escape")
                out.append(int(m.group(0), 16))
                i = m.end()
            elif e == ord("z"):                              # \z: skip the whitespace that follows
                i = _SPACE.match(s, i + 1).end()
            elif e == ord("u"):                              # \u{XXX}
                m = _UNICODE.match(s, i + 1)
                if not m:
                    self.fail("bad \\u escape")
                out += chr(int(m.group(1), 16)).encode("utf-8", "surrogatepass")
                i = m.end()
            else:
                self.fail(f"unknown escape \\{chr(e)}")

    def table(self, depth):
        if depth > MAX_DEPTH:
            self.fail("tables nested too deep")
        self.i += 1                                          # {
        out, n = {}, 0
        while True:
            self.skip()
            s, i = self.s, self.i
            if i >= len(s):
                self.fail("unfinished table")
            if s[i] == 0x7D:                                 # }
                self.i = i + 1
                return out
            if s[i] == 0x5B and not _LONG_OPEN.match(s, i):  # [key] = value
                self.i = i + 1
                key = self.value(depth)
                self.skip()
                if not self.s.startswith(b"]", self.i):
                    self.fail("] expected")
                self.i += 1
                self.expect_equals()
                out[key] = self.value(depth)
            else:
                m = _NAME.match(s, i)
                after = _SPACE.match(s, m.end()).end() if m else i
                if m and s.startswith(b"=", after) and not s.startswith(b"==", after) \
                        and m.group(0) not in (b"true", b"false", b"nil"):
                    self.i = after + 1                       # name = value
                    out[m.group(0).decode()] = self.value(depth)
                else:                                        # a positional value
                    n += 1
                    out[n] = self.value(depth)
            self.skip()
            if self.s.startswith((b",", b";"), self.i):
                self.i += 1
            elif not self.s.startswith(b"}", self.i):
                self.fail(", or } expected")

    def expect_equals(self):
        self.skip()
        if not self.s.startswith(b"=", self.i) or self.s.startswith(b"==", self.i):
            self.fail("= expected")
        self.i += 1

    def chunk(self):
        out = {}
        while True:
            self.skip()
            if self.i >= len(self.s):
                return out
            m = _NAME.match(self.s, self.i)
            if not m:
                self.fail(f"a variable name expected, found {self.s[self.i:self.i + 12]!r}")
            self.i = m.end()
            self.expect_equals()
            out[m.group(0).decode()] = self.value(0)


def parse(data):
    """the variables a SavedVariables file assigns, {name: value}; data is bytes (or str)"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return _Reader(data).chunk()


def load(path):
    """parse() of a file"""
    return parse(Path(path).read_bytes())


def array(table):
    """the positional part of a parsed table (keys 1, 2, ... without gaps) as a list"""
    if not isinstance(table, dict):
        return []
    out, k = [], 1
    while k in table:
        out.append(table[k])
        k += 1
    return out
