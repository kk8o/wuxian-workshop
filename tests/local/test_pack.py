"""An extension package (local/pack.py): what pack() puts in and leaves out, that the same files give the same zip, and
every member read() and unpack() refuse. Everything lies in a temporary folder."""
import hashlib
import io
import json
import os
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from wuxianworkshop.local import manifest as M
from wuxianworkshop.local import pack as P

TOC = b"## Interface: 16001\n## Title: x\n## Version: 1.2.0\n"


def put(folder, rel, data=b"x"):
    p = Path(folder).joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def extension(folder, eid="voice", **fields):
    """an extension folder: its manifest (fields go into it, None leaves one out), main.py and AGENT.md"""
    data = dict({"id": eid, "title": "声音", "version": "0.2.0", "run": ["python", "main.py"]}, **fields)
    data = {k: v for k, v in data.items() if v is not None}
    put(folder, M.MANIFEST, json.dumps(data, ensure_ascii=False).encode("utf-8"))
    put(folder, "main.py", b"print('hi')\n")
    put(folder, "AGENT.md", b"# voice\n")
    return Path(folder)


def addon(folder, name="WuxianVoice"):
    put(folder, f"addons/{name}/{name}.toc", TOC)
    put(folder, f"addons/{name}/core.lua", b"print('x')\n")


def names(path):
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def tree(folder):
    """{posix path: bytes} of a folder's files"""
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def manifest_bytes(**fields):
    return json.dumps(dict({"id": "voice", "title": "声音", "version": "0.2.0", "run": ["python", "main.py"]}, **fields),
                      ensure_ascii=False).encode("utf-8")


def make_zip(path, members, method=zipfile.ZIP_DEFLATED):
    """a zip of [(name, data)] or [(name, data, tweak)]: the names as given (ZipInfo() would turn a backslash into /),
    tweak(info) changes a member's ZipInfo before it is written"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")                       # "Duplicate name"
        with zipfile.ZipFile(path, "w", method) as z:
            for name, data, *tweaks in members:
                info = zipfile.ZipInfo("x")
                info.filename = name
                info.compress_type = method
                for tweak in tweaks:
                    tweak(info)
                z.writestr(info, data)
    return Path(path)


def patch_central(path, at, value, nth=0):
    """bytes written into the nth entry of a zip's central directory, `at` bytes after its signature"""
    raw = bytearray(Path(path).read_bytes())
    i = -1
    for _ in range(nth + 1):
        i = raw.index(b"PK\x01\x02", i + 1)
    raw[i + at:i + at + len(value)] = value
    Path(path).write_bytes(bytes(raw))


def link(info):
    info.create_system, info.external_attr = 3, 0o120777 << 16


def fifo(info):
    info.create_system, info.external_attr = 3, 0o010644 << 16


GOOD = [("voice/" + M.MANIFEST, manifest_bytes()), ("voice/main.py", b"print('hi')\n")]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.folder = self.root / "src" / "voice"
        self.out = self.root / "out"

    def refused(self, status, code, fn, *args, **kwargs):
        with self.assertRaises(P.PackError) as cm:
            fn(*args, **kwargs)
        self.assertEqual((cm.exception.status, cm.exception.code), (status, code), str(cm.exception))
        return cm.exception


class Pack(Case):
    def test_round_trip(self):
        extension(self.folder, addons=["WuxianVoice"])
        addon(self.folder)
        put(self.folder, "lib/deep/tool.py", b"# tool\n")
        put(self.folder, "data/voices.bin", bytes(range(256)) * 40)
        got = P.pack(self.folder, self.out)
        file = self.out / "voice-0.2.0.zip"
        self.assertEqual(sorted(got), ["file", "files", "id", "sha256", "size", "skipped", "unpacked", "version"])
        self.assertEqual((got["id"], got["version"], got["file"], got["files"], got["skipped"]),
                         ("voice", "0.2.0", str(file), 7, []))
        self.assertEqual(got["size"], file.stat().st_size)
        self.assertEqual(got["sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
        self.assertEqual(got["unpacked"], sum(len(b) for b in tree(self.folder).values()))
        self.assertEqual(names(file), sorted("voice/" + rel for rel in tree(self.folder)))
        self.assertFalse(list(self.out.glob("*.tmp")))

        seen = P.read(file)
        self.assertEqual(sorted(seen), ["files", "id", "manifest", "root", "size", "title", "unpacked", "version"])
        self.assertEqual((seen["id"], seen["version"], seen["title"], seen["root"], seen["files"], seen["size"]),
                         ("voice", "0.2.0", "声音", "voice", 7, got["size"]))
        self.assertEqual(seen["unpacked"], got["unpacked"])
        self.assertEqual(seen["manifest"]["addons"], ["WuxianVoice"])

        into = self.root / "home" / "voice"
        self.assertEqual(P.unpack(file, into), into)
        self.assertEqual(tree(into), tree(self.folder))
        manifest, problems = M.check_manifest(into)
        self.assertEqual([p for p in problems if p["level"] == "error"], [])
        self.assertEqual((manifest["id"], manifest["addons"]), ("voice", ["WuxianVoice"]))

    def test_the_manifest_file_names_the_folder(self):
        extension(self.folder)
        got = P.pack(self.folder / M.MANIFEST, self.out)
        self.assertEqual(names(got["file"]), ["voice/AGENT.md", "voice/main.py", "voice/" + M.MANIFEST])

    def test_same_files_same_zip(self):
        extension(self.folder, addons=["WuxianVoice"])
        addon(self.folder)
        put(self.folder, "b/z.txt", b"z" * 5000)
        put(self.folder, "a/y.txt", b"y" * 5000)
        one = P.pack(self.folder, self.root / "one")
        other = self.root / "elsewhere" / "copy"                  # the same files made in another order, another day
        for rel, data in sorted(tree(self.folder).items(), reverse=True):
            os.utime(put(other, rel, data), (1_000_000_000, 1_000_000_000))
        for p in self.folder.rglob("*"):
            os.utime(p, (1_500_000_000, 1_500_000_000))
        two = P.pack(self.folder, self.root / "two")
        three = P.pack(other, self.root / "three")
        self.assertEqual(one["sha256"], two["sha256"])
        self.assertEqual(one["sha256"], three["sha256"])
        self.assertEqual(Path(one["file"]).read_bytes(), Path(three["file"]).read_bytes())
        with zipfile.ZipFile(one["file"]) as z:
            infos = z.infolist()
        self.assertEqual([i.filename for i in infos], sorted(i.filename for i in infos))
        self.assertEqual({i.date_time for i in infos}, {P.STAMP})
        self.assertEqual({(i.create_system, i.external_attr, i.compress_type) for i in infos},
                         {(0, P.MODE, zipfile.ZIP_DEFLATED)})
        self.assertFalse([i.filename for i in infos if i.is_dir()])

    def test_where_the_zip_goes(self):
        extension(self.folder, version=None)
        self.assertEqual(Path(P.pack(self.folder, self.out)["file"]), self.out / "voice-0.zip")
        named = self.root / "deep" / "er" / "My.Zip"
        got = P.pack(self.folder, named)
        self.assertEqual((Path(got["file"]), got["version"]), (named, None))
        self.assertTrue(named.is_file())
        again = P.pack(self.folder, named)                         # in place of the one there
        self.assertEqual(again["sha256"], got["sha256"])
        self.assertEqual(P._target(self.out, "voice", "1.0 beta/2:x").name, "voice-1.0_beta_2_x.zip")
        self.assertEqual(P._target(self.out, "voice", "..").name, "voice-0.zip")
        self.assertEqual(P._target(self.out, "voice", "二").name, "voice-_.zip")

    def test_what_is_always_left_out(self):
        extension(self.folder)
        for rel in (".git/config", ".hg/x", ".svn/x", "__pycache__/main.cpython-314.pyc", "old.pyc", ".venv/Lib/a.py",
                    ".venv311/Lib/a.py", "venv/Lib/a.py", "node_modules/x/index.js", ".pytest_cache/v", ".mypy_cache/v",
                    ".idea/a.xml", ".vscode/settings.json", "logs/today.txt", "cache/blob", "debug.log",
                    "settings.local.json", ".env", ".env.production", "Thumbs.db", "sub/.DS_Store",
                    "sub/__pycache__/x.pyc", "sub/deep/LOGS/a.txt", "sub/Trace.LOG", "sub/keep.txt", "catalog.txt",
                    "logs.txt", "environment.py"):
            put(self.folder, rel)
        got = P.pack(self.folder, self.out)
        self.assertEqual(names(got["file"]),
                         ["voice/AGENT.md", "voice/catalog.txt", "voice/environment.py", "voice/logs.txt",
                          "voice/main.py", "voice/sub/keep.txt", "voice/" + M.MANIFEST])
        self.assertEqual(got["skipped"], sorted(
            [".git/", ".hg/", ".svn/", "__pycache__/", "old.pyc", ".venv/", ".venv311/", "venv/", "node_modules/",
             ".pytest_cache/", ".mypy_cache/", ".idea/", ".vscode/", "logs/", "cache/", "debug.log",
             "settings.local.json", ".env", ".env.production", "Thumbs.db", "sub/.DS_Store", "sub/__pycache__/",
             "sub/deep/LOGS/", "sub/Trace.LOG"]))

    def test_what_the_manifest_leaves_out(self):
        extension(self.folder, pack={"exclude": ["models/*", "*.wav", "data/big.bin", "Tools/"]})
        for rel in ("models/a.onnx", "models/sub/b.onnx", "sound/a.wav", "sound/a.ogg", "data/big.bin",
                    "data/small.bin", "more/data/big.bin", "tools/x.py", "models.txt"):
            put(self.folder, rel)
        got = P.pack(self.folder, self.out)
        self.assertEqual(names(got["file"]),
                         ["voice/AGENT.md", "voice/data/small.bin", "voice/main.py", "voice/models.txt",
                          "voice/more/data/big.bin", "voice/sound/a.ogg", "voice/" + M.MANIFEST])
        self.assertEqual(got["skipped"], ["data/big.bin", "models/a.onnx", "models/sub/", "sound/a.wav", "tools/"])

    def test_the_argument_in_place_of_the_manifests(self):
        extension(self.folder, pack={"exclude": ["*.wav"]})
        put(self.folder, "a.wav")
        put(self.folder, "a.bin")
        put(self.folder, "debug.log")
        got = P.pack(self.folder, self.out, exclude=("*.bin",))
        self.assertEqual(names(got["file"]), ["voice/AGENT.md", "voice/a.wav", "voice/main.py", "voice/" + M.MANIFEST])
        self.assertEqual(got["skipped"], ["a.bin", "debug.log"])
        self.assertEqual(P.pack(self.folder, self.out, exclude=[])["skipped"], ["debug.log"])
        for bad in ("*.bin", ["../x"], [3], ["a\\b"]):
            e = self.refused(400, "bad_request", P.pack, self.folder, self.out, exclude=bad)
            self.assertTrue(e.problems)

    def test_the_manifest_is_never_left_out(self):
        extension(self.folder)
        os.replace(self.folder / M.MANIFEST, self.folder / "Wuxian-Extension.JSON")
        put(self.folder, "other.json")
        got = P.pack(self.folder, self.out, exclude=["*.json", "wuxian-*"])
        self.assertEqual(names(got["file"]), ["voice/AGENT.md", "voice/main.py", "voice/" + M.MANIFEST])
        self.assertEqual(P.read(got["file"])["id"], "voice")

    def test_a_link_is_not_followed(self):
        import _winapi
        extension(self.folder)
        secret = self.root / "elsewhere"
        put(secret, "secret.txt", b"not yours")
        try:
            _winapi.CreateJunction(str(secret), str(self.folder / "linked"))
        except (OSError, AttributeError) as e:
            self.skipTest(f"no junction here: {e}")
        self.addCleanup(lambda: os.path.lexists(self.folder / "linked") and os.rmdir(self.folder / "linked"))
        got = P.pack(self.folder, self.out)
        self.assertEqual(names(got["file"]), ["voice/AGENT.md", "voice/main.py", "voice/" + M.MANIFEST])
        self.assertEqual(got["skipped"], ["linked"])
        self.assertEqual(P.walk(self.folder)[1], ["linked"])

    def test_a_bad_manifest(self):
        extension(self.folder, eid="Not An Id")
        e = self.refused(400, "bad_manifest", P.pack, self.folder, self.out)
        self.assertEqual([p["level"] for p in e.problems][:1], ["error"])
        self.assertIn("id", str(e))
        self.assertFalse(self.out.exists())
        e = self.refused(400, "bad_manifest", P.pack, self.root / "nowhere", self.out)
        self.assertTrue(e.problems)

    def test_too_much(self):
        extension(self.folder)
        put(self.folder, "big.bin", os.urandom(4000))
        with mock.patch.object(P, "MAX_FILES", 3):
            self.refused(413, "package_too_big", P.pack, self.folder, self.out)
        with mock.patch.object(P, "MAX_UNPACKED", 3000):
            self.refused(413, "package_too_big", P.pack, self.folder, self.out)
        with mock.patch.object(P, "MAX_ZIP", 3000):
            self.refused(413, "package_too_big", P.pack, self.folder, self.out)
        self.assertFalse(list(self.out.glob("*")) if self.out.exists() else [])
        self.assertEqual(P.pack(self.folder, self.out)["files"], 4)

    def test_only_an_addons_kinds_under_addons(self):
        extension(self.folder, addons=["WuxianVoice"])
        addon(self.folder)
        put(self.folder, "addons/WuxianVoice/.gitignore")
        put(self.folder, "addons/WuxianVoice/.hidden/x.exe")
        got = P.pack(self.folder, self.out)
        self.assertEqual(got["skipped"], ["addons/WuxianVoice/.gitignore", "addons/WuxianVoice/.hidden/"])
        self.assertEqual(P.read(got["file"])["files"], 5)
        put(self.folder, "tool.exe")                               # outside addons/ anything goes
        self.assertEqual(P.pack(self.folder, self.out)["files"], 6)
        for rel in ("addons/WuxianVoice/tool.exe", "addons/WuxianVoice/lib/noext", "addons/readme.exe"):
            p = put(self.folder, rel)
            e = self.refused(400, "bad_package", P.pack, self.folder, self.out)
            self.assertIn(rel, str(e))
            p.unlink()

    def test_what_the_manifest_names_must_be_in(self):
        extension(self.folder, python=".venv/Scripts/python.exe")
        put(self.folder, ".venv/Scripts/python.exe")
        e = self.refused(400, "bad_package", P.pack, self.folder, self.out)
        self.assertIn("python", str(e))
        extension(self.folder, python="runtime/python.exe")
        put(self.folder, "runtime/python.exe")
        self.assertIn("voice/runtime/python.exe", names(P.pack(self.folder, self.out)["file"]))
        extension(self.folder, addons=["WuxianVoice"])
        addon(self.folder)
        e = self.refused(400, "bad_package", P.pack, self.folder, self.out, exclude=["*.toc"])
        self.assertIn("WuxianVoice", str(e))

    def test_the_zip_is_not_in_itself(self):
        extension(self.folder)
        one = P.pack(self.folder, self.folder)
        self.assertEqual(Path(one["file"]), self.folder / "voice-0.2.0.zip")
        two = P.pack(self.folder, self.folder)
        self.assertEqual(two["sha256"], one["sha256"])
        self.assertEqual(names(two["file"]), ["voice/AGENT.md", "voice/main.py", "voice/" + M.MANIFEST])

    def test_left_out(self):
        self.assertTrue(P.left_out("a/b/Thumbs.DB", P.EXCLUDE))
        self.assertTrue(P.left_out("a/.venv-old", P.EXCLUDE))
        self.assertFalse(P.left_out("a/venv2", P.EXCLUDE))
        self.assertTrue(P.left_out("Models/x.bin", ["models/*"]))
        self.assertFalse(P.left_out("a/models/x.bin", ["models/*"]))
        self.assertTrue(P.left_out("a/models", ["Models/"]))
        self.assertFalse(P.left_out("a", ["", " "]))
        self.assertTrue(P.hidden("a/.b"))
        self.assertFalse(P.hidden(".a/b"))
        self.assertTrue(P.in_addons("AddOns/X/x.lua"))
        self.assertFalse(P.in_addons("addons.txt"))


class Read(Case):
    def zip(self, members, name="p.zip", **kw):
        return make_zip(self.root / name, members, **kw)

    def refused_both(self, status, code, file):
        """read() and unpack() refuse the package the same way, and unpack() leaves nothing behind"""
        e = self.refused(status, code, P.read, file)
        into = self.root / "into"
        self.refused(status, code, P.unpack, file, into)
        self.assertEqual(list(into.iterdir()), [])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted(["into", Path(file).name]))
        return e

    def test_the_manifest_at_the_top(self):
        file = self.zip([(M.MANIFEST, manifest_bytes(title=None, version=None)), ("main.py", b"1"),
                         ("addons/WuxianVoice/WuxianVoice.toc", TOC)])
        seen = P.read(file)
        self.assertEqual((seen["id"], seen["version"], seen["title"], seen["root"], seen["files"]),
                         ("voice", None, "voice", "", 3))
        into = P.unpack(file, self.root / "into")
        self.assertEqual(sorted(tree(into)), ["addons/WuxianVoice/WuxianVoice.toc", "main.py", M.MANIFEST])

    def test_the_manifest_in_the_one_top_folder(self):
        file = self.zip([("voice-main/", b""), ("voice-main/" + M.MANIFEST, manifest_bytes(version=3)),
                         ("voice-main/main.py", b"1"), ("voice-main/empty/", b""), ("voice-main/sub/a.txt", b"22")])
        seen = P.read(file)
        self.assertEqual((seen["root"], seen["files"], seen["version"], seen["unpacked"]),
                         ("voice-main", 3, None, len(manifest_bytes(version=3)) + 3))
        into = P.unpack(file, self.root / "into")
        self.assertEqual(sorted(tree(into)), ["main.py", "sub/a.txt", M.MANIFEST])
        self.assertFalse((into / "empty").exists())
        self.assertFalse((into / "voice-main").exists())

    def test_a_manifest_at_the_top_wins(self):
        file = self.zip([(M.MANIFEST, manifest_bytes()), ("inner/" + M.MANIFEST, manifest_bytes(id="other"))])
        self.assertEqual((P.read(file)["id"], P.read(file)["root"]), ("voice", ""))

    def test_member_names(self):
        for name in ("/abs.txt", "/voice/abs.txt", "voice/../evil.txt", "../evil.txt", "voice/sub/../../../evil.txt",
                     "C:/evil.txt", "voice/C:evil.txt", "voice/a.txt:stream", "voice\\evil.txt",
                     "voice/..\\..\\evil.txt", "\\\\server\\share\\evil.txt", "voice/NUL", "voice/con.txt",
                     "voice/sub/COM1.lua", "voice/LPT9", "voice/aux.tar.gz", "voice/CONIN$", "voice/com\u00b9",
                     "voice/a./b.txt", "voice/a.txt ", "voice/a.txt.", "voice/ /b.txt", "voice//b.txt",
                     "voice/./b.txt", "voice/a\x01b.txt", "voice/a?.txt", "voice/a*.txt", "voice/a<b>.txt",
                     'voice/a"b.txt', "voice/a|b.txt", "voice/a\0b.txt", "voice/" + "n" * 256,
                     "voice/..", "voice/evil/../", "voice/sub\\"):
            with self.subTest(name=name):
                file = self.zip(GOOD + [(name, b"x")])
                self.refused_both(400, "bad_package", file)
        self.assertFalse((self.root.parent / "evil.txt").exists())

    def test_names_that_are_fine(self):
        file = self.zip(GOOD + [("voice/console.txt", b"1"), ("voice/com10.txt", b"2"), ("voice/a..b/c d.txt", b"3"),
                                ("voice/读我.md", b"4"), ("voice/nul_/x", b"5"), ("voice/.hidden", b"6")])
        self.assertEqual(P.read(file)["files"], 8)
        self.assertEqual(len(tree(P.unpack(file, self.root / "into"))), 8)

    def test_a_link_or_a_special_file(self):
        self.refused_both(400, "bad_package", self.zip(GOOD + [("voice/link", b"C:/Windows", link)]))
        self.refused_both(400, "bad_package", self.zip(GOOD + [("voice/pipe", b"", fifo)]))

    def test_twice(self):
        self.refused_both(400, "bad_package", self.zip(GOOD + [("voice/a.txt", b"1"), ("voice/a.txt", b"2")]))
        self.refused_both(400, "bad_package", self.zip(GOOD + [("voice/a.txt", b"1"), ("voice/A.TXT", b"2")]))
        self.refused_both(400, "bad_package", self.zip(GOOD + [("voice/Sub/a.txt", b"1"), ("voice/sub/a.txt", b"2")]))

    def test_a_file_and_a_folder(self):
        self.refused_both(400, "bad_package", self.zip(GOOD + [("voice/a", b"1"), ("voice/a/b.txt", b"2")]))
        self.refused_both(400, "bad_package", self.zip(GOOD + [("voice/A/", b""), ("voice/a", b"1")]))

    def test_encrypted_or_strangely_compressed(self):
        file = self.zip(GOOD)
        patch_central(file, 8, b"\x01\x00")
        self.refused_both(400, "bad_package", file)
        file = self.zip(GOOD)
        patch_central(file, 10, b"\x63\x00", nth=1)
        self.refused_both(400, "bad_package", file)

    def test_no_manifest_where_it_must_be(self):
        for members in ([("voice/main.py", b"1")],
                        [("a/" + M.MANIFEST, manifest_bytes()), ("b/main.py", b"1")],
                        [("a/" + M.MANIFEST, manifest_bytes()), ("readme.txt", b"1")],
                        [("a/b/" + M.MANIFEST, manifest_bytes()), ("a/b/main.py", b"1")],
                        [("voice/Wuxian-Extension.json", manifest_bytes())],
                        [("voice/", b"")],
                        []):
            with self.subTest(members=[m[0] for m in members]):
                self.refused_both(400, "bad_package", self.zip(members))

    def test_a_manifest_that_cannot_be(self):
        for raw in (b"{not json", b"[1, 2]", b'"voice"', b"\xff\xfe\x00", manifest_bytes(id="Voice"),
                    manifest_bytes(id="voice\n"), manifest_bytes(id=None), manifest_bytes(id="kit"),
                    manifest_bytes(id=7)):
            with self.subTest(raw=raw[:30]):
                self.refused(400, "bad_package", P.read, self.zip([("voice/" + M.MANIFEST, raw)]))
        file = self.zip(GOOD)
        with mock.patch.object(P, "MAX_MANIFEST", 10):
            self.refused(400, "bad_package", P.read, file)
        self.assertEqual(P.read(self.zip([(M.MANIFEST, b"\xef\xbb\xbf" + manifest_bytes())]))["id"], "voice")

    def test_only_an_addons_kinds_under_addons(self):
        for name in ("voice/addons/WuxianVoice/x.exe", "voice/Addons/WuxianVoice/lib/x.dll",
                     "voice/addons/WuxianVoice/noext", "voice/addons/x.bat", "voice/addons/WuxianVoice/a.lua.exe"):
            with self.subTest(name=name):
                self.refused_both(400, "bad_package", self.zip(GOOD + [(name, b"MZ")]))
        self.refused_both(400, "bad_package", self.zip([(M.MANIFEST, manifest_bytes()), ("addons/W/x.exe", b"MZ")]))
        file = self.zip(GOOD + [("voice/addons/W/W.toc", TOC), ("voice/addons/W/Art/a.TGA", b"1"),
                                ("voice/tools/x.exe", b"MZ")])
        self.assertEqual(P.read(file)["files"], 5)
        file = self.zip(GOOD + [("voice/addons", b"a file of that name"), ("voice/addons.exe", b"MZ")])
        self.assertEqual(P.read(file)["files"], 4)

    def test_not_a_zip(self):
        self.refused(400, "bad_package", P.read, put(self.root, "p.zip", b"hello, not a zip"))
        self.refused(400, "bad_package", P.read, put(self.root, "p.zip", b""))
        self.refused(400, "bad_package", P.read, self.root / "nowhere.zip")
        self.refused(400, "bad_package", P.read, self.root)
        self.refused(400, "bad_package", P.unpack, self.root / "nowhere.zip", self.root / "into")
        self.assertEqual(list((self.root / "into").iterdir()), [])

    def test_too_big(self):
        file = self.zip(GOOD + [("voice/a.bin", b"a" * 80), ("voice/b.bin", b"b" * 80)])
        with mock.patch.object(P, "MAX_ZIP", 100):
            self.refused_both(413, "package_too_big", file)
        with mock.patch.object(P, "MAX_FILES", 3):
            self.refused_both(413, "package_too_big", file)
        with mock.patch.object(P, "MAX_UNPACKED", len(GOOD[0][1]) + len(GOOD[1][1]) + 159):
            self.refused_both(413, "package_too_big", file)
        with mock.patch.object(P, "MAX_UNPACKED", len(GOOD[0][1]) + len(GOOD[1][1]) + 160):
            self.assertEqual(P.read(file)["files"], 4)

    def test_a_bomb_by_its_declared_size(self):
        file = self.zip(GOOD + [("voice/zeros.bin", bytes(2_000_000))])
        self.assertLess(file.stat().st_size, 10_000)
        with mock.patch.object(P, "MAX_UNPACKED", 1_000_000):
            self.refused_both(413, "package_too_big", file)


class Unpack(Case):
    def test_into_an_empty_folder_only(self):
        file = make_zip(self.root / "p.zip", GOOD)
        into = self.root / "into"
        put(into, "mine.txt", b"keep me")
        self.refused(409, "not_empty", P.unpack, file, into)
        self.assertEqual(tree(into), {"mine.txt": b"keep me"})
        fresh = self.root / "a" / "b" / "fresh"
        self.assertEqual(sorted(tree(P.unpack(file, fresh))), ["main.py", M.MANIFEST])
        self.refused(409, "not_empty", P.unpack, file, fresh)

    def test_a_size_that_lies(self):
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            for declared in (5, 50, 5000):
                with self.subTest(method=method, declared=declared):
                    file = make_zip(self.root / "p.zip", GOOD + [("voice/a.bin", b"0123456789" * 10)], method=method)
                    patch_central(file, 24, declared.to_bytes(4, "little"), nth=2)
                    into = self.root / f"into-{method}-{declared}"
                    self.refused(400, "bad_package", P.unpack, file, into)
                    self.assertEqual(list(into.iterdir()), [])

    def test_more_bytes_than_declared_stop_the_copy(self):
        with self.assertRaises(P.PackError) as cm:
            P._copy(io.BytesIO(b"x" * 10), io.BytesIO(), 5)
        self.assertEqual((cm.exception.status, cm.exception.code), (413, "package_too_big"))
        out = io.BytesIO()
        self.assertEqual(P._copy(io.BytesIO(b"x" * 10), out, 10), 10)
        self.assertEqual(out.getvalue(), b"x" * 10)

    def test_counted_again_while_unpacking(self):
        file = make_zip(self.root / "p.zip", GOOD + [("voice/a.bin", b"a" * 500), ("voice/b.bin", b"b" * 500)])
        real = P._scan

        def blind(z):                                              # as if the first count had let it through
            with mock.patch.object(P, "MAX_UNPACKED", 1 << 30):
                return real(z)

        into = self.root / "into"
        with mock.patch.object(P, "_scan", blind), mock.patch.object(P, "MAX_UNPACKED", 600):
            self.refused(413, "package_too_big", P.unpack, file, into)
        self.assertEqual(list(into.iterdir()), [])

    def test_broken_content(self):
        payload = b"0123456789abcdef" * 8
        file = make_zip(self.root / "p.zip", GOOD + [("voice/a.bin", payload)], method=zipfile.ZIP_STORED)
        raw = file.read_bytes()
        file.write_bytes(raw.replace(payload, payload[::-1], 1))
        self.assertEqual(P.read(file)["files"], 3)                 # read() does not look into the files
        into = self.root / "into"
        self.refused(400, "bad_package", P.unpack, file, into)
        self.assertEqual(list(into.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
