"""The SavedVariables reader (core/savedvars.py): the format the client writes, the Lua it must refuse, never running it."""
import tempfile
import unittest
from pathlib import Path

from wuxianworkshop.core import savedvars as sv

# what 1.60.1.70235 wrote for !WuxianWorkshop and WoWBridge (no indentation, CRLF, arrays without "-- [n]" comments)
CLIENT = (b"\r\nWuxianWorkshopDB = {\r\n[\"dropped\"] = 0,\r\n[\"settings\"] = {\r\n},\r\n[\"errors\"] = {\r\n},\r\n}\r\n"
          b"WoWBridgeDB = {\r\n[\"mail\"] = {\r\n[\"next\"] = 242,\r\n[\"proc\"] = \"P54604-1791263681\",\r\n},\r\n"
          b"[\"fonttest\"] = {\r\n[\"bt\"] = 25.8,\r\n[\"j\"] = 0.5,\r\n},\r\n[\"polltest\"] = {\r\n[\"ok\"] = false,\r\n"
          b"[\"done\"] = true,\r\n},\r\n[\"codeDone\"] = {\r\n\"1257497574\",\r\n\"1257497575\",\r\n},\r\n}\r\n")

# the retail style: indented, array entries followed by "-- [n]" comments
RETAIL = b"""
SomeAddonDB = {
	["profiles"] = {
		["Default"] = {
			["scale"] = 1.25,
			["list"] = {
				"a", -- [1]
				"b", -- [2]
			},
		},
	},
	["count"] = -3,
}
"""


class ClientFormat(unittest.TestCase):
    def test_what_the_client_wrote(self):
        got = sv.parse(CLIENT)
        self.assertEqual(got["WuxianWorkshopDB"], {"dropped": 0, "settings": {}, "errors": {}})
        db = got["WoWBridgeDB"]
        self.assertEqual(db["mail"], {"next": 242, "proc": "P54604-1791263681"})
        self.assertEqual((db["fonttest"]["bt"], db["polltest"]["ok"], db["polltest"]["done"]), (25.8, False, True))
        self.assertEqual(sv.array(db["codeDone"]), ["1257497574", "1257497575"])

    def test_retail_style(self):
        db = sv.parse(RETAIL)["SomeAddonDB"]
        profile = db["profiles"]["Default"]
        self.assertEqual((profile["scale"], sv.array(profile["list"]), db["count"]), (1.25, ["a", "b"], -3))

    def test_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "X.lua"
            p.write_bytes(b"\xef\xbb\xbfX = 1\n")               # a byte order mark is taken
            self.assertEqual(sv.load(p), {"X": 1})


class Values(unittest.TestCase):
    def one(self, lua):
        return sv.parse(b"V = " + lua)["V"]

    def test_strings_and_escapes(self):
        self.assertEqual(self.one(rb'"a\"b\\c\nd\te"'), 'a"b\\c\nd\te')
        self.assertEqual(self.one(rb'"\228\184\173\230\150\135"'), "中文")             # decimal escapes: UTF-8 bytes
        self.assertEqual(self.one('"无限 工坊"'.encode()), "无限 工坊")                  # raw UTF-8
        self.assertEqual(self.one(b"'single'"), "single")
        self.assertEqual(self.one(b'"line\\\r\nbreak"'), "line\nbreak")                 # backslash + line break
        self.assertEqual(self.one(rb'"\x41\u{4E2D}\z   x"'), "A中x")
        self.assertEqual(self.one(b"[==[long ]] string]==]"), "long ]] string")
        self.assertEqual(self.one(b"[[\nfirst line\nsecond]]"), "first line\nsecond")   # the first line break is dropped
        self.assertEqual(self.one(b'"\xff"'), "�")                                 # not UTF-8

    def test_numbers(self):
        self.assertEqual([self.one(t) for t in (b"0", b"-12", b"3.5", b"-0.25", b"1e3", b"2.5E-2", b"0x1F", b".5")],
                         [0, -12, 3.5, -0.25, 1000.0, 0.025, 31, 0.5])
        self.assertEqual(self.one(b"inf"), float("inf"))
        self.assertEqual(self.one(b"-inf"), float("-inf"))
        v = self.one(b"-nan(ind)")
        self.assertNotEqual(v, v)                                                        # nan

    def test_tables(self):
        self.assertEqual(self.one(b"{ 1, 2; x = true, [3] = nil, [\"k\"] = {}, [7] = 'seven', }"),
                         {1: 1, 2: 2, "x": True, 3: None, "k": {}, 7: "seven"})
        self.assertEqual(self.one(b"{ [true] = 1, [1.5] = 2 }"), {True: 1, 1.5: 2})
        self.assertEqual(sv.array(self.one(b"{ 'a', 'b', [4] = 'd' }")), ["a", "b"])  # stops at the gap

    def test_comments(self):
        self.assertEqual(sv.parse(b"-- a comment\nA = 1 -- trailing\n--[[ block\n B = 2 ]]\n--[==[ x ]==] C = 3"),
                         {"A": 1, "C": 3})


class Refused(unittest.TestCase):
    """anything beyond literal values is a ParseError: nothing in the file is ever run"""

    def refused(self, lua):
        with self.assertRaises(sv.ParseError):
            sv.parse(lua)

    def test_code(self):
        self.refused(b"A = os.exit()")
        self.refused(b"A = function() end")
        self.refused(b"A = 1 + 2")
        self.refused(b"local A = 1")
        self.refused(b"A = B")
        self.refused(b"print('x')")

    def test_broken(self):
        self.refused(b'A = "unfinished')
        self.refused(b'A = "line\nbreak"')
        self.refused(b"A = { 1, 2")
        self.refused(b"A = { [1] 2 }")
        self.refused(b"A = { 1 2 }")
        self.refused(b"A == 1")
        self.refused(b'A = "\\q"')
        self.refused(b'A = "\\300"')
        self.refused(b"A = --[[ never closed")

    def test_depth(self):
        self.refused(b"A = " + b"{" * (sv.MAX_DEPTH + 1) + b"}" * (sv.MAX_DEPTH + 1))
        self.assertEqual(sv.parse(b"A = {{{}}}"), {"A": {1: {1: {}}}})

    def test_error_names_the_line(self):
        with self.assertRaisesRegex(sv.ParseError, "line 3"):
            sv.parse(b"A = {\n1,\n2 3 }")


if __name__ == "__main__":
    unittest.main()
