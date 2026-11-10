"""The registry of local extensions (registry.py) on files in a temporary folder: memory and its two files, a read that
fails (only a missing file is an empty registry), the copy kept before a change, the registry file of before the two
were split, a description cache that is lost or damaged, option values after another description, approval and its
fingerprint, folders that are not on this computer."""
import copy
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.local.util import make_extension
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import registry as R

DESC = dict(
    capabilities=[dict(id="demo.look", name="look", kind="see", title="Look", doc="looks around", args={}, timeout=10)],
    sources=[dict(name="ticks", title="Ticks", doc="one a second")],
    options=[dict(name="loud", kind="boolean", title="Loud", default=False),
             dict(name="size", kind="number", title="Size", default=3, min=1, max=10),
             dict(name="mode", kind="choice", title="Mode", default="a",
                  choices=[dict(value="a", label="A"), dict(value="b", label="B")]),
             dict(name="token", kind="text", title="Token", default="")])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "state"
        self.notes, self.saves = [], []
        self.reg = self.registry()

    def registry(self):
        return R.Registry(self.state, on_save=lambda: self.saves.append(1), note=self.notes.append)

    def add(self, eid="demo", reg=None, **fields):
        """registers a new extension folder the way the host does; gives the folder"""
        reg = reg or self.reg
        folder = make_extension(self.root / "ext" / eid, eid)
        data = reg.load()
        data["extensions"].append(dict(dict(id=eid, path=str(folder), enabled=True, options={}, described=None),
                                       **fields))
        data["revision"] += 1
        reg.save(data)
        return folder

    def config(self):
        return json.loads(self.reg.path.read_text(encoding="utf-8"))

    def cache(self):
        return json.loads(self.reg.cache_path.read_text(encoding="utf-8"))

    def put(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def noted(self, *words):
        return [n for n in self.notes if all(w in n for w in words)]


class FilesTests(Base):
    def test_no_file_is_an_empty_registry(self):
        self.assertEqual(self.reg.load(), dict(revision=0, extensions=[]))
        self.assertEqual((self.reg.ids(), self.reg.revision, self.reg.problem), ([], 0, None))
        self.assertFalse(self.state.exists())          # reading writes nothing
        with self.assertRaises(ApiError) as c:
            self.reg.entry("demo")
        self.assertEqual((c.exception.status, c.exception.code), (404, "not_found"))

    def test_configuration_and_descriptions_are_two_files(self):
        folder = self.add()
        self.reg.update_entry("demo", described=DESC, options=dict(size=5))
        entry = self.config()["extensions"][0]
        self.assertEqual(entry, dict(id="demo", path=str(folder), enabled=True, options=dict(size=5), approved=None))
        self.assertEqual(self.cache(), dict(extensions=dict(demo=dict(path=str(folder), described=DESC))))
        again = self.registry()
        self.assertEqual(again.entry("demo"), dict(entry, described=DESC))
        self.assertEqual(again.load(), self.reg.load())
        self.assertEqual(again.option_values("demo"), dict(loud=False, size=5, mode="a", token=""))
        self.assertEqual(again.kind_of("demo.look"), "see")
        self.assertEqual(again.capability("demo.nope"), (again.entry("demo"), None))
        self.assertIsNone(again.capability("other.look"))
        self.assertIsNone(again.problem)

    def test_load_is_a_copy_and_save_takes_it(self):
        self.add()
        data = self.reg.load()
        entry = self.reg.entry("demo", data)
        self.assertIs(entry, data["extensions"][0])
        entry["enabled"] = False
        self.assertTrue(self.reg.entry("demo")["enabled"])
        self.reg.entry("demo")["options"]["x"] = 1
        self.assertEqual(self.reg.entry("demo")["options"], {})
        saves = len(self.saves)
        self.reg.save(data)
        self.assertFalse(self.reg.entry("demo")["enabled"])
        self.assertFalse(self.config()["extensions"][0]["enabled"])
        self.assertEqual(len(self.saves), saves + 1)
        data["extensions"][0]["enabled"] = True        # what was saved is no longer the caller's
        self.assertFalse(self.reg.entry("demo")["enabled"])

    def test_memory_is_read_from_the_file_once(self):
        self.add()
        again = self.registry()
        self.assertEqual(again.ids(), ["demo"])
        with mock.patch.object(R, "read_retrying", side_effect=AssertionError("read again")):
            self.assertEqual(again.ids(), ["demo"])
            self.assertEqual(again.load()["revision"], 1)
            again.update_entry("demo", enabled=False)
        self.assertFalse(self.registry().entry("demo")["enabled"])

    def test_a_save_that_changes_nothing_writes_nothing(self):
        self.add()
        self.reg.update_entry("demo", described=DESC)
        before = [(p.name, p.stat().st_mtime_ns, p.read_bytes()) for p in sorted(self.state.iterdir())]
        saves = len(self.saves)
        self.reg.save(self.reg.load())
        self.assertEqual([(p.name, p.stat().st_mtime_ns, p.read_bytes()) for p in sorted(self.state.iterdir())], before)
        self.assertEqual(len(self.saves), saves + 1)

    def test_the_file_as_it_was_is_kept_beside_it(self):
        self.add()
        first = self.reg.path.read_bytes()
        self.assertFalse(self.reg.backup_path.exists())
        self.reg.update_entry("demo", enabled=False)
        self.assertEqual(self.reg.backup_path.read_bytes(), first)
        second = self.reg.path.read_bytes()
        self.reg.update_entry("demo", options=dict(x=1))
        self.assertEqual(self.reg.backup_path.read_bytes(), second)
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["extensions.json", "extensions.json.bak"])

    def test_a_players_edit_of_the_file_is_read(self):
        self.add()
        self.reg.update_entry("demo", described=DESC)
        config = self.config()
        config["extensions"][0]["enabled"] = False
        config["revision"] = 0
        self.put(self.reg.path, config)
        entry = self.reg.entry("demo")
        self.assertFalse(entry["enabled"])
        self.assertEqual(entry["described"], DESC)     # the description is memory's
        self.assertEqual(self.reg.revision, 1)         # and the revision never goes back

    def test_a_file_that_is_no_json_keeps_memory_and_is_not_written_over(self):
        folder = self.add()
        self.put(self.reg.path, '{"revision": 3, "extensions": [')
        self.assertEqual(self.reg.ids(), ["demo"])
        self.assertIn("cannot be read", self.reg.problem)
        self.assertIn(str(self.reg.path), self.reg.problem)
        self.assertEqual(len(self.noted("cannot be read")), 1)
        self.reg.update_entry("demo", enabled=False)   # taken, in memory
        self.assertFalse(self.reg.entry("demo")["enabled"])
        self.assertEqual(self.reg.path.read_text(encoding="utf-8"), '{"revision": 3, "extensions": [')
        self.assertIsNotNone(self.reg.problem)
        self.assertEqual(len(self.noted("cannot be read")), 1)       # told once
        # put right by hand: the file counts again
        self.put(self.reg.path, dict(revision=1, extensions=[dict(id="demo", path=str(folder), enabled=True)]))
        self.assertEqual(self.reg.entry("demo"), dict(id="demo", path=str(folder), enabled=True, options={},
                                                      approved=None, described=None))
        self.assertIsNone(self.reg.problem)
        self.reg.update_entry("demo", options=dict(x=1))
        self.assertEqual(self.config()["extensions"][0]["options"], dict(x=1))

    def test_a_file_that_is_no_registry_is_not_an_empty_one(self):
        self.add()
        for text in ("[]", '"extensions"', '{"extensions": {}}', '{"extensions": [{"id": "a"}]}',
                     '{"extensions": [{"id": 1, "path": "x"}]}', '{"extensions": ["demo"]}',
                     '{"extensions": [{"id": "a", "path": "x"}, {"id": "a", "path": "y"}]}', "\xff\xfe"):
            with self.subTest(text=text):
                self.put(self.reg.path, text)
                self.assertEqual(self.reg.ids(), ["demo"])
                self.assertIn("cannot be read", self.reg.problem)
        self.reg.path.write_bytes(b"\xff\xfe\x00{")       # no UTF-8 either
        self.assertEqual(self.reg.ids(), ["demo"])
        self.assertIn("cannot be read", self.reg.problem)

    def test_a_registry_never_read_refuses_changes(self):
        self.put(self.reg.path, "{not json")
        self.assertEqual(self.reg.load(), dict(revision=0, extensions=[]))
        self.assertIn("cannot be read", self.reg.problem)
        data = self.reg.load()
        data["extensions"].append(dict(id="demo", path=str(self.root), enabled=True, options={}, described=None))
        with self.assertRaises(ApiError) as c:
            self.reg.save(data)
        self.assertEqual((c.exception.status, c.exception.code), (500, "registry_unreadable"))
        with self.assertRaises(ApiError):
            self.reg.update_entry("demo", enabled=False)
        self.assertEqual(self.reg.path.read_text(encoding="utf-8"), "{not json")
        self.assertEqual((self.saves, self.reg.ids()), ([], []))
        self.assertFalse(self.reg.cache_path.exists())
        self.reg.path.unlink()                         # removed: starts with none
        self.add()
        self.assertEqual((self.reg.ids(), self.reg.problem), (["demo"], None))

    def test_a_read_the_system_refuses_keeps_memory(self):
        self.add()
        config = self.config()
        config["extensions"][0]["enabled"] = False
        self.put(self.reg.path, config)                # another file than the one memory is of
        with mock.patch.object(R, "read_retrying", side_effect=PermissionError(13, "Permission denied")) as read:
            self.assertTrue(self.reg.entry("demo")["enabled"])
            self.assertIn("PermissionError", self.reg.problem)
            self.assertEqual(self.reg.ids(), ["demo"])
            self.assertEqual(read.call_count, 1)       # not tried at every call
            self.reg.update_entry("demo", options=dict(x=1))
            self.assertEqual(self.reg.entry("demo")["options"], dict(x=1))
        self.assertEqual(self.config(), config)        # not written over
        self.reg.again = 0.0
        self.assertFalse(self.reg.entry("demo")["enabled"])
        self.assertIsNone(self.reg.problem)

    def test_a_never_read_registry_the_system_refuses(self):
        self.add()
        again = self.registry()
        with mock.patch.object(R, "read_retrying", side_effect=PermissionError(13, "Permission denied")):
            self.assertEqual(again.ids(), [])
            with self.assertRaises(ApiError) as c:
                again.save(again.load())
            self.assertEqual(c.exception.code, "registry_unreadable")
        again.again = 0.0
        self.assertEqual((again.ids(), again.problem), (["demo"], None))
        self.assertEqual(self.registry().ids(), ["demo"])

    def test_a_file_that_went_away_is_written_again(self):
        self.add()
        self.reg.path.unlink()
        self.assertEqual((self.reg.ids(), self.reg.problem), (["demo"], None))
        self.reg.save(self.reg.load())
        self.assertEqual(self.registry().ids(), ["demo"])

    def test_a_write_that_fails_stays_in_memory_and_is_written_later(self):
        self.add()
        with mock.patch.object(R, "write_json", side_effect=PermissionError(13, "Permission denied")):
            self.reg.update_entry("demo", enabled=False, described=DESC)
        self.assertFalse(self.reg.entry("demo")["enabled"])
        self.assertIn("could not be written", self.reg.problem)
        self.assertTrue(self.config()["extensions"][0]["enabled"])
        self.assertEqual(len(self.saves), 2)
        self.reg.update_entry("demo", options=dict(size=2))
        self.assertIsNone(self.reg.problem)
        self.assertEqual(self.registry().entry("demo"), self.reg.entry("demo"))
        self.assertEqual(self.registry().entry("demo")["described"], DESC)

    def test_write_json_is_whole_or_not_at_all(self):
        target = self.state / "x.json"
        R.write_json(target, dict(a=1))
        with mock.patch.object(R, "replace_retrying", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(PermissionError):
                R.write_json(target, dict(a=2))
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), dict(a=1))
        self.assertEqual([p.name for p in self.state.iterdir()], ["x.json"])

    def test_a_read_waits_for_a_writer(self):
        self.put(self.reg.path, dict(revision=4, extensions=[]))
        real, calls = Path.read_text, []

        def read_text(path, *a, **kw):
            if path.name != "extensions.json":
                return real(path, *a, **kw)
            calls.append(path)
            if len(calls) < 3:
                raise PermissionError(13, "Permission denied")
            return real(path, *a, **kw)

        with mock.patch.object(R, "REPLACE_WAITS", (0, 0, 0)), mock.patch.object(Path, "read_text", read_text):
            self.assertEqual(self.reg.revision, 4)
        self.assertEqual((len(calls), self.reg.problem), (3, None))

    def test_readers_and_a_writer_at_once(self):
        """one registry writes while its own threads read, and another registry (as another process would) reads the
        files: nobody ever sees a registry without the extension"""
        self.add()
        other, seen, errors, stop = self.registry(), set(), [], threading.Event()

        def read(reg):
            try:
                while not stop.is_set():
                    seen.add(tuple(reg.ids()))
                    seen.add(tuple(e["id"] for e in reg.load()["extensions"]))
                    time.sleep(0.001)
            except Exception as e:                     # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=read, args=(reg,)) for reg in (self.reg, self.reg, other)]
        for t in threads:
            t.start()
        try:
            for i in range(150):
                self.reg.update_entry("demo", options=dict(n=i))
        finally:
            stop.set()
            for t in threads:
                t.join(10)
        self.reg.update_entry("demo", options=dict(n="last"))
        self.assertEqual((errors, seen), ([], {("demo",)}))
        self.assertIsNone(self.reg.problem)
        self.assertEqual(other.entry("demo")["options"], dict(n="last"))
        self.assertIsNone(other.problem)
        self.assertEqual(self.config()["extensions"][0]["options"], dict(n="last"))


class SplitTests(Base):
    """the registry file of before the descriptions had their own, and a cache that is lost or damaged"""

    def old_file(self):
        a = make_extension(self.root / "ext" / "demo", "demo")
        b = make_extension(self.root / "ext" / "other", "other")
        old = dict(revision=7, extensions=[
            dict(id="demo", path=str(a), enabled=True, options=dict(loud=True), described=DESC),
            dict(id="other", path=str(b), enabled=False, options={}, described=None)])
        self.put(self.reg.path, old)
        return old

    def test_the_old_single_file_is_taken_apart(self):
        old = self.old_file()
        raw = self.reg.path.read_bytes()
        data = self.reg.load()
        self.assertEqual(data["revision"], 7)
        self.assertEqual(data["extensions"], [dict(e, approved=None) for e in old["extensions"]])
        self.assertEqual(self.config(), dict(revision=7, extensions=[
            {k: v for k, v in dict(e, approved=None).items() if k != "described"} for e in old["extensions"]]))
        self.assertEqual(self.cache(), dict(extensions=dict(demo=dict(path=old["extensions"][0]["path"],
                                                                      described=DESC))))
        self.assertEqual(self.reg.backup_path.read_bytes(), raw)
        self.assertEqual((self.saves, self.reg.problem), ([], None))
        self.assertTrue(self.noted("extensions-cache.json"))
        again = self.registry()
        self.assertEqual(again.load(), data)
        self.assertEqual(again.option_values("demo")["loud"], True)

    def test_the_old_file_stays_until_the_descriptions_are_written(self):
        old = self.old_file()
        real = R.write_json

        def write_json(path, data):
            if Path(path).name == "extensions-cache.json":
                raise PermissionError(13, "Permission denied")
            return real(path, data)

        with mock.patch.object(R, "write_json", write_json):
            self.assertEqual(self.reg.entry("demo")["described"], DESC)
            self.assertEqual(self.config(), old)
            self.reg.update_entry("other", enabled=True)
            self.assertEqual(self.config(), old)
        self.assertTrue(self.noted("extensions-cache.json", "could not be written"))
        self.reg.update_entry("demo", enabled=False)
        self.assertNotIn("described", self.config()["extensions"][0])
        again = self.registry()
        self.assertEqual(again.entry("demo")["described"], DESC)
        self.assertEqual((again.entry("demo")["enabled"], again.entry("other")["enabled"]), (False, True))

    def test_an_old_description_of_another_shape_is_none(self):
        old = self.old_file()
        old["extensions"][0]["described"] = dict(capabilities=[dict(id="demo.look")])
        self.put(self.reg.path, old)
        self.assertIsNone(self.reg.entry("demo")["described"])
        self.assertEqual(self.reg.entry("demo")["options"], dict(loud=True))
        self.assertTrue(self.noted("demo", "not the shape"))
        self.assertFalse(self.noted("other", "not the shape"))

    def described(self):
        folder = self.add()
        self.reg.update_entry("demo", described=DESC, options=dict(size=5))
        return folder

    def test_a_lost_cache_is_no_description(self):
        self.described()
        self.reg.cache_path.unlink()
        again = self.registry()
        entry = again.entry("demo")
        self.assertIsNone(entry["described"])
        self.assertEqual((entry["options"], entry["enabled"], again.problem), (dict(size=5), True, None))
        self.assertEqual(R.option_rows(entry), [])
        self.assertIsNone(again.kind_of("demo.look"))
        with self.assertRaises(ApiError) as c:
            R.check_options(entry, dict(size=1))
        self.assertEqual(c.exception.code, "not_described")
        again.update_entry("demo", described=DESC)
        self.assertEqual(self.cache()["extensions"]["demo"]["described"], DESC)
        self.assertEqual(again.entry("demo")["options"], dict(size=5))

    def test_a_damaged_cache_is_no_description(self):
        folder = self.described()
        for text in ("{half", "[]", '{"extensions": []}', '{"extensions": {"demo": 5}}', "{}",
                     json.dumps(dict(extensions=dict(demo=dict(described=DESC)))),
                     json.dumps(dict(extensions=dict(demo=dict(path=str(folder), described="yes"))))):
            with self.subTest(text=text):
                self.put(self.reg.cache_path, text)
                again = self.registry()
                self.assertIsNone(again.entry("demo")["described"])
                self.assertEqual((again.entry("demo")["options"], again.problem), (dict(size=5), None))
        self.assertTrue(self.noted("extensions-cache.json", "cannot be used"))
        self.assertEqual(self.config()["extensions"][0]["options"], dict(size=5))

    def test_a_cached_description_of_another_shape_is_none(self):
        folder = self.described()

        def without(part, i, key):
            d = copy.deepcopy(DESC)
            del d[part][i][key]
            return d

        bad = [dict(DESC, options=None), {k: v for k, v in DESC.items() if k != "sources"},
               dict(DESC, capabilities=["look"]), dict(DESC, capabilities=dict(look={})),
               without("capabilities", 0, "id"), without("capabilities", 0, "name"), without("capabilities", 0, "kind"),
               without("capabilities", 0, "args"), without("capabilities", 0, "timeout"),
               without("sources", 0, "name"), without("sources", 0, "doc"),
               without("options", 1, "name"), without("options", 1, "kind"), without("options", 2, "title"),
               dict(DESC, options=[dict(name="mode", kind="choice", title="Mode", choices=["a", "b"])]),
               dict(DESC, options=[dict(name="mode", kind="choice", title="Mode", choices=[dict(label="A")])]),
               dict(DESC, capabilities=[dict(DESC["capabilities"][0], timeout="soon")])]
        for d in bad:
            with self.subTest(described=d):
                self.assertFalse(R.described_ok(d))
                self.put(self.reg.cache_path, dict(extensions=dict(demo=dict(path=str(folder), described=d))))
                again = self.registry()
                entry = again.entry("demo")
                self.assertIsNone(entry["described"])
                self.assertEqual(R.option_rows(entry), [])
                self.assertIsNone(again.kind_of("demo.look"))
                self.assertEqual(again.capability("demo.look"), (entry, None))
                self.assertEqual(again.option_values("demo"), {})
        self.assertTrue(self.noted("demo", "not the shape"))
        self.assertTrue(R.described_ok(DESC))
        self.assertTrue(R.described_ok(dict(capabilities=[], sources=[], options=[], more="is fine")))

    def test_a_description_of_another_folder_is_not_this_ones(self):
        self.described()
        cache = self.cache()
        cache["extensions"]["demo"]["path"] = str(self.root / "ext" / "elsewhere")
        self.put(self.reg.cache_path, cache)
        self.assertIsNone(self.registry().entry("demo")["described"])

    def test_a_saved_description_of_another_shape_is_none(self):
        self.add()
        e = self.reg.update_entry("demo", described=dict(capabilities=[]))
        self.assertIsNone(e["described"])
        self.assertIsNone(self.reg.entry("demo")["described"])
        self.assertFalse(self.reg.cache_path.exists())


class OptionsTests(Base):
    NEW = dict(DESC, options=[dict(name="size", kind="number", title="Size", default=3, min=1, max=5),
                              dict(name="mode", kind="choice", title="Mode", default="a",
                                   choices=[dict(value="a", label="A"), dict(value="c", label="C")]),
                              dict(name="name", kind="text", title="Name", default="")])

    def test_another_description_checks_the_values_again(self):
        self.add()
        self.reg.update_entry("demo", described=DESC)
        self.reg.update_entry("demo", options=dict(loud=True, size=4, mode="b", token="s3cret-value"))
        self.notes.clear()
        e = self.reg.update_entry("demo", described=self.NEW)
        self.assertEqual(e["options"], dict(size=4))
        self.assertEqual(self.reg.entry("demo")["options"], dict(size=4))
        self.assertEqual(self.config()["extensions"][0]["options"], dict(size=4))
        self.assertEqual(self.reg.option_values("demo"), dict(size=4, mode="a", name=""))
        for name in ("loud", "mode", "token"):
            self.assertEqual(len(self.noted("demo", f"option {name} ")), 1, self.notes)
        self.assertFalse(self.noted("option size"))
        self.assertFalse(self.noted("s3cret-value"))   # a value may be a secret: names only
        self.assertEqual([text for _, text in self.reg.notes][-3:], [n.partition(": ")[2] for n in self.notes[-3:]])

    def test_the_hosts_way_drops_them_in_its_data_too(self):
        self.add()
        self.reg.update_entry("demo", described=DESC, options=dict(size=9, mode="a"))
        data = self.reg.load()
        entry = self.reg.entry("demo", data)
        entry["described"] = self.NEW
        self.reg.save(data)
        self.assertEqual(entry["options"], dict(mode="a"))
        self.assertEqual(self.registry().entry("demo")["options"], dict(mode="a"))

    def test_the_same_description_keeps_the_values(self):
        self.add()
        self.reg.update_entry("demo", described=DESC, options=dict(size=9, mode="b"))
        self.notes.clear()
        self.reg.update_entry("demo", described=copy.deepcopy(DESC))
        self.assertEqual(self.reg.entry("demo")["options"], dict(size=9, mode="b"))
        self.reg.update_entry("demo", described=None)  # not described any more: nothing to check against
        self.assertEqual(self.reg.entry("demo")["options"], dict(size=9, mode="b"))
        self.assertEqual(self.notes, [])

    def test_values_in_the_file_are_checked_when_it_is_read(self):
        self.add()
        self.reg.update_entry("demo", described=DESC, options=dict(size=9))
        config = self.config()
        config["extensions"][0]["options"] = dict(size=99, mode="b", gone=1)
        self.put(self.reg.path, config)
        self.assertEqual(self.registry().entry("demo")["options"], dict(mode="b"))
        self.assertEqual(self.reg.entry("demo")["options"], dict(mode="b"))

    def test_check_options(self):
        self.add()
        with self.assertRaises(ApiError) as c:
            R.check_options(self.reg.entry("demo"), dict(size=1))
        self.assertEqual(c.exception.code, "not_described")
        entry = self.reg.update_entry("demo", described=DESC)
        self.assertIsNone(R.check_options(entry, dict(size=1, mode="b", loud=True)))
        for options in (dict(size=99), dict(mode="z"), dict(nope=1)):
            with self.assertRaises(ApiError) as c:
                R.check_options(entry, options)
            self.assertEqual(c.exception.code, "bad_option")
        self.assertEqual([(o["name"], o["value"]) for o in R.option_rows(entry)],
                         [("loud", False), ("size", 3), ("mode", "a"), ("token", "")])


class ApprovalTests(Base):
    def manifest(self, folder, **fields):
        """rewrites the folder's manifest with fields changed (None: left out)"""
        file = Path(folder) / "wuxian-extension.json"
        m = json.loads(file.read_text(encoding="utf-8-sig"))
        m.update(fields)
        file.write_text(json.dumps({k: v for k, v in m.items() if v is not None}, ensure_ascii=False), encoding="utf-8")

    def test_needed_until_allowed(self):
        self.add()
        self.assertEqual(self.reg.approval(self.reg.entry("demo")), "needed")
        before = time.time()
        entry = self.reg.approve("demo", "player")
        approved = entry["approved"]
        self.assertEqual(sorted(approved), ["at", "by", "fingerprint"])
        self.assertRegex(approved["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(approved["by"], "player")
        self.assertTrue(before - 1 <= approved["at"] <= time.time() + 1)
        self.assertEqual(self.reg.approval(entry), "ok")
        self.assertEqual(self.reg.approval(self.reg.entry("demo")), "ok")
        self.assertEqual(self.config()["extensions"][0]["approved"], approved)
        again = self.registry()
        self.assertEqual(again.approval(again.entry("demo")), "ok")

    def test_the_fingerprint_is_the_folder_and_what_runs(self):
        folder = self.add()
        self.manifest(folder, python=">=3.11", **{"pass": ["HTTP_PROXY"]})
        what = dict(path=os.path.normcase(os.path.realpath(folder)), run=["python", "main.py"], python=">=3.11",
                    env=dict(FIXTURE_MODE=""), **{"pass": ["HTTP_PROXY"]})
        text = json.dumps(what, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(self.reg.fingerprint(folder), hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(self.reg.fingerprint(str(folder).upper() if os.name == "nt" else folder),
                         self.reg.fingerprint(folder))
        self.assertEqual(self.reg.approve("demo", "player")["approved"]["fingerprint"], self.reg.fingerprint(folder))

    def test_a_manifest_without_addons_has_the_fingerprint_it_always_had(self):
        """the fingerprint's input is byte for byte what it was before addons counted, for every manifest that names
        none: an approval stored by an older workshop still holds"""
        folder = self.add()
        real = os.path.normcase(os.path.realpath(folder))
        old = ('{"env":{"FIXTURE_MODE":""},"pass":null,"path":' + json.dumps(real, ensure_ascii=False)
               + ',"python":null,"run":["python","main.py"]}')
        before = hashlib.sha256(old.encode("utf-8")).hexdigest()
        self.assertEqual(self.reg.fingerprint(folder), before)
        self.reg.approve("demo", "player")
        for none in ([], None, "WuxianDemo", {"WuxianDemo": 1}, [7, ""]):   # no list of names: as if not there
            with self.subTest(addons=none):
                self.manifest(folder, addons=none)
                self.assertEqual(self.reg.fingerprint(folder), before)
                self.assertEqual(self.reg.approval(self.reg.entry("demo")), "ok")

    def test_the_addons_it_brings_are_in_the_fingerprint(self):
        folder = self.add()
        without = self.reg.fingerprint(folder)
        self.reg.approve("demo", "player")
        self.manifest(folder, addons=["WuxianB", "WuxianA"])
        what = dict(path=os.path.normcase(os.path.realpath(folder)), run=["python", "main.py"], python=None,
                    env=dict(FIXTURE_MODE=""), addons=["WuxianA", "WuxianB"], **{"pass": None})
        text = json.dumps(what, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        both = self.reg.fingerprint(folder)
        self.assertEqual(both, hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertNotEqual(both, without)
        self.assertEqual(self.reg.approval(self.reg.entry("demo")), "changed")     # an addon more: asked again
        self.reg.approve("demo", "player")
        self.manifest(folder, addons=["WuxianA", "WuxianB", "WuxianA"])            # another order, one twice: the same
        self.assertEqual(self.reg.fingerprint(folder), both)
        self.assertEqual(self.reg.approval(self.reg.entry("demo")), "ok")
        self.manifest(folder, addons=["WuxianA"])
        self.assertEqual(self.reg.approval(self.reg.entry("demo")), "changed")
        self.manifest(folder, addons=None)
        self.assertEqual(self.reg.fingerprint(folder), without)
        self.assertEqual(R.addon_names(dict(addons=["b", "a", "b", "", 3])), ["a", "b"])
        self.assertEqual((R.addon_names({}), R.addon_names(dict(addons="a")), R.addon_names(None)), ([], [], []))

    def test_ask_is_kept_only_as_true(self):
        """an entry's ask (it was not written on this computer: only the player allows it) is written and read back;
        an entry without it is what it always was, in memory and in the file"""
        self.add("plain")
        self.add("asked", ask=True)
        self.add("odd", ask="yes")
        self.add("no", ask=False)
        entries = {e["id"]: e for e in self.reg.load()["extensions"]}
        self.assertEqual({k: e.get("ask", "-") for k, e in entries.items()},
                         dict(plain="-", asked=True, odd="-", no="-"))
        self.assertEqual(set(entries["plain"]), {"id", "path", "enabled", "options", "approved", "described"})
        written = {e["id"]: e for e in self.config()["extensions"]}
        self.assertEqual({k: e.get("ask", "-") for k, e in written.items()},
                         dict(plain="-", asked=True, odd="-", no="-"))
        again = self.registry()
        self.assertIs(again.entry("asked")["ask"], True)
        self.assertNotIn("ask", again.entry("plain"))
        self.assertIs(self.reg.approve("asked", "player")["ask"], True)            # an approval leaves it
        self.assertIs(self.registry().entry("asked")["ask"], True)
        self.reg.update_entry("asked", ask=False)
        self.assertNotIn("ask", self.reg.entry("asked"))
        self.assertNotIn("ask", {e["id"]: e for e in self.config()["extensions"]}["asked"])
        self.assertEqual(R.normal_entry(dict(id="x", path="p", ask=1)).get("ask", "-"), "-")

    def test_what_runs_changed(self):
        folder = self.add()
        changes = [dict(run=["python", "other.py"]), dict(python=">=3.12"), dict(env=dict(FIXTURE_MODE="x")),
                   {"pass": ["PATH"]}, dict(run=None), dict(env=dict(FIXTURE_MODE="x", MORE="1"))]
        for i, change in enumerate(changes):
            with self.subTest(change=change):
                by = ("player", "developer")[i % 2]
                self.assertEqual(self.reg.approve("demo", by)["approved"]["by"], by)
                self.assertEqual(self.reg.approval(self.reg.entry("demo")), "ok")
                self.manifest(folder, **change)
                self.assertEqual(self.reg.approval(self.reg.entry("demo")), "changed")

    def test_what_does_not_run_does_not_matter(self):
        folder = self.add()
        self.reg.approve("demo", "player")
        self.manifest(folder, title="Another title", version="2.0.0", start="workshop", requires=["sense"])
        self.assertEqual(self.reg.approval(self.reg.entry("demo")), "ok")
        (Path(folder) / "wuxian-extension.json").write_bytes(      # a byte order mark and another order
            b"\xef\xbb\xbf" + json.dumps(dict(env=dict(FIXTURE_MODE=""), run=["python", "main.py"], id="demo")).encode())
        self.assertEqual(self.reg.approval(self.reg.entry("demo")), "ok")

    def test_another_folder_is_changed(self):
        folder = self.add()
        self.reg.approve("demo", "player")
        moved = self.root / "ext" / "moved"
        shutil.copytree(folder, moved)
        entry = self.reg.update_entry("demo", path=str(moved))
        self.assertEqual(self.reg.approval(entry), "changed")

    def test_a_manifest_that_cannot_be_read_is_needed(self):
        folder = self.add()
        file = Path(folder) / "wuxian-extension.json"
        good = file.read_bytes()
        self.reg.approve("demo", "player")
        for bad in (b"{half", b"[1, 2]", b"\xff\xfe\x00", None):
            with self.subTest(bad=bad):
                file.unlink() if bad is None else file.write_bytes(bad)
                self.assertEqual(self.reg.approval(self.reg.entry("demo")), "needed")
                with self.assertRaises(ApiError) as c:
                    self.reg.approve("demo", "developer")
                self.assertEqual((c.exception.status, c.exception.code), (409, "bad_manifest"))
        self.assertEqual(self.reg.entry("demo")["approved"]["by"], "player")
        file.write_bytes(good)
        self.assertEqual(self.reg.approval(self.reg.entry("demo")), "ok")
        for path in (str(self.root / "nowhere"), None):
            self.assertEqual(self.reg.approval(dict(id="x", path=path, approved=dict(fingerprint="f"))), "needed")

    def test_an_approval_of_another_shape_is_needed(self):
        folder = self.add()
        fingerprint = self.reg.fingerprint(folder)
        for approved in (None, True, "yes", {}, dict(fingerprint=""), dict(fingerprint=5), [fingerprint]):
            entry = dict(id="demo", path=str(folder), approved=approved)
            self.assertEqual(self.reg.approval(entry), "needed", approved)
        entry = dict(id="demo", path=str(folder), approved=dict(fingerprint=fingerprint))
        self.assertEqual(self.reg.approval(entry), "ok")
        self.assertEqual(self.reg.approval(dict(id="demo", path=str(folder))), "needed")
        config = self.config()
        config["extensions"][0]["approved"] = "yes"
        self.put(self.reg.path, config)
        self.assertIsNone(self.reg.entry("demo")["approved"])

    def test_approve_checks_and_saves_once(self):
        self.add()
        with self.assertRaises(ApiError) as c:
            self.reg.approve("demo", "agent")
        self.assertEqual((c.exception.status, c.exception.code), (400, "bad_request"))
        with self.assertRaises(ApiError) as c:
            self.reg.approve("nobody", "player")
        self.assertEqual(c.exception.code, "not_found")
        first = self.reg.approve("demo", "player")["approved"]
        saves = len(self.saves)
        self.assertEqual(self.reg.approve("demo", "developer")["approved"], first)       # allowed as it is: stays
        self.assertEqual(len(self.saves), saves)

    def test_the_fingerprint_is_read_again_only_when_the_manifest_changed(self):
        folder = self.add()
        entry = self.reg.approve("demo", "player")
        with mock.patch.object(R.json, "load", side_effect=AssertionError("read again")):
            for _ in range(3):
                self.assertEqual(self.reg.approval(entry), "ok")
        self.manifest(folder, run=["python", "main.py", "--more"])
        self.assertEqual(self.reg.approval(entry), "changed")
        with mock.patch.object(R.json, "load", side_effect=AssertionError("read again")):
            self.assertEqual(self.reg.approval(entry), "changed")
            with self.assertRaises(AssertionError):    # approve reads the manifest whatever is kept
                self.reg.approve("demo", "player")


class FolderTests(unittest.TestCase):
    def refused(self, path):
        with self.assertRaises(ApiError) as c:
            R.check_folder(path)
        self.assertEqual((c.exception.status, c.exception.code), (400, "not_allowed"))

    def test_unc_paths_are_refused(self):
        with mock.patch.object(R, "drive_type", side_effect=AssertionError("asked the system")):
            for path in (r"\\server\share\ext", "//server/share/ext", r"\\server\share", r"\/server\share",
                         r"  \\server\share\ext", r"\\?\UNC\server\share\ext", r"\\?\C:\ext", r"\\.\pipe\x"):
                with self.subTest(path=path):
                    self.refused(path)
            self.refused(Path(r"\\server\share\ext") if os.name == "nt" else Path("//server/share/ext"))

    @unittest.skipUnless(os.name == "nt", "drives are a Windows thing")
    def test_network_drives_are_refused(self):
        with mock.patch.object(R, "drive_type", return_value=R.DRIVE_REMOTE) as kind:
            self.refused(r"Z:\ext\demo")
            self.refused(Path("z:/ext/demo/wuxian-extension.json"))
        self.assertEqual([c.args[0].upper() for c in kind.call_args_list], ["Z:\\", "Z:\\"])
        for local in (2, 3, 5, 6, 1, 0):               # removable, fixed, CD, RAM disk, no root, unknown
            with mock.patch.object(R, "drive_type", return_value=local):
                self.assertIsNone(R.check_folder(r"Z:\ext\demo"))

    def test_a_folder_on_this_computer_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(R.check_folder(tmp))
            self.assertIsNone(R.check_folder(Path(tmp) / "not there" / "wuxian-extension.json"))
        if os.name == "nt":
            self.assertIn(R.drive_type(os.path.splitdrive(os.path.abspath(tempfile.gettempdir()))[0] + "\\"), (2, 3, 6))


if __name__ == "__main__":
    unittest.main()
