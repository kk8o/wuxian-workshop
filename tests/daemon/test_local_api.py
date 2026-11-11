"""Local extensions over a real daemon (a FakeGame link; WuxianKit's calls answered here, tests/local/fixture_ext.py as
the extension): the HTTP routes and their errors, SSE's `event: local`, local_call to a local extension and to the
game's WuxianKit (see / keep / point only), an extension's own calls, its source's items as journal events, its apps
declared (again after kit.ready only when they changed) and forgotten, the reactions (a game source through the
temporary app, a local source directly), new_extension, the MCP tools over /mcp and the command line; and packages and
the game half of an extension (/api/local/pack, /api/local/install, a .zip given to /api/local/add), whose game addons
go into the FakeGame's AddOns folder."""
import asyncio
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.daemon.test_server import MUTEX, FakeGame
from tests.local.util import make_extension, until
from wuxianworkshop import scaffold
from wuxianworkshop.cli import main as cli
from wuxianworkshop.core import mailbox as MB
from wuxianworkshop.daemon import server as daemon
from wuxianworkshop.daemon.api import ApiError
from wuxianworkshop.local import pack as K

KIT_MANIFEST = {"protocol": 1, "revision": "r1", "extensions": [
    {"id": "sense", "state": "on", "capabilities": [{"id": "sense.bags", "kind": "see", "title": "背包", "args": {}}]},
    {"id": "tune", "state": "on", "capabilities": [{"id": "tune.cvar.set", "kind": "do", "title": "设置", "args": {}}]},
    {"id": "guide", "state": "off", "capabilities": [{"id": "guide.note", "kind": "point", "title": "提示"}]}]}
ZED_MANIFEST = {"protocol": 1, "revision": "r2", "extensions": [{"id": "zed", "state": "on", "capabilities": []}]}


def with_game(folder, eid, name, addon_version="1.2.0", **fields):
    """an extension's folder with a game addon (addons/<name>: a .toc and core.lua), written over what is there"""
    side = Path(folder) / "addons" / name
    side.mkdir(parents=True, exist_ok=True)
    (side / f"{name}.toc").write_text(f"## Interface: 16001\n## Title: {name}\n## Version: {addon_version}\n\ncore.lua\n",
                                      encoding="utf-8", newline="\n")
    (side / "core.lua").write_text("print('core')\n", encoding="utf-8", newline="\n")
    return make_extension(folder, eid, addons=[name], **fields)


class Kit:
    """WuxianKit in the game as the daemon's Service calls it: what it was asked (calls), the answers of a kit 0.7:
    the apps it was told of (apps: id -> declaration), on: the ids that run"""

    def __init__(self, svc):
        self.svc, self.calls, self.apps, self.on = svc, [], {}, set()

    def emit(self, topic, data):
        self.svc.on_debug("EVENT", json.dumps({"a": "WuxianKit", "t": topic, "d": data}))

    async def call_exposed(self, addon, name, args=None, timeout_ms=10000, shared=None):
        self.calls.append((name, args))
        if name == "kit.app.declare":
            result = {"app": args["app"], "changed": self.apps.get(args["app"]) != args}
            self.apps[args["app"]] = json.loads(json.dumps(args))
        elif name == "kit.apps":
            result = [dict(id=app, on=app in self.on, local=decl.get("local")) for app, decl in self.apps.items()]
        elif name == "kit.app.describe":
            if args["app"] not in self.apps:
                raise ApiError(400, "call_failed", f"no app {args['app']}\nstack ...")
            result = {"app": args["app"], "by": "agent", "temp": False, "decl": dict(self.apps[args["app"]], lease=120)}
        elif name == "kit.app.forget":
            if self.apps.pop(args["app"], None) is None:
                raise ApiError(400, "call_failed", f"no app {args['app']}\nstack ...")
            self.on.discard(args["app"])
            result = {"app": args["app"]}
        elif name == "kit.app.open":
            if "game:nonsense" in (args.get("temp") or {}).get("hear", []):
                raise ApiError(400, "call_failed", "no source game:nonsense\nstack ...")
            result = {"on": True}
        elif name == "kit.app.stop":
            result = {"on": False}
        elif name == "sense.bags":
            result = {"free": 12, "args": args}
        else:
            raise ApiError(400, "call_failed", f"WuxianKit.{name} is not exposed")
        return {"addon": addon, "name": name, "ok": True, "result": result}

    def asked(self, name):
        return [args for n, args in self.calls if n == name]


class LocalDaemon:
    """a daemon of its own for a TestCase class (LETTER: its mutex), WuxianKit's calls answered by a Kit, and the
    helpers; each test leaves no extension and no reaction behind. APPROVAL: its extension_approval ("auto": nobody
    is asked before an extension runs, as most tests want; None: as a daemon is started, which asks)"""
    LETTER = "L"
    APPROVAL = "auto"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name).resolve()
        cls.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": str(cls.root / "home")})
        cls.env.start()
        state = cls.root / "home" / "state"
        state.mkdir(parents=True)
        (state / "kit-manifest.json").write_text(json.dumps(KIT_MANIFEST), encoding="utf-8")
        addons = cls.root / "client" / "Interface" / "AddOns"
        (addons / "WoWBridge").mkdir(parents=True)
        MB.install(addons, pool=16)
        cls.handle = daemon.start(worker_factory=lambda on_debug, log: FakeGame(addons, on_debug=on_debug, log=log),
                                  mutex_name=MUTEX + cls.LETTER, extension_approval=cls.APPROVAL)
        cls.svc = cls.handle.service
        cls.kit = Kit(cls.svc)
        cls.svc.call_exposed = cls.kit.call_exposed
        cls.svc.kit_manifest_known = lambda: KIT_MANIFEST
        cls.addons = addons

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.kit.calls.clear()
        self.kit.apps.clear()
        self.kit.on.clear()

    def tearDown(self):
        self.ok("POST", "/api/reactions/clear", {})
        for row in self.svc.local.rows():                  # each test leaves no extension behind
            status, _ = self.call("POST", "/api/local/remove", {"id": row["id"]})
            self.assertEqual(status, 200)

    # --- helpers

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.handle.url.rstrip("/") + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.handle.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def ok(self, method, path, body=None):
        status, res = self.call(method, path, body)
        self.assertEqual(status, 200, res)
        return res

    def fails(self, method, path, body, status, code):
        got, res = self.call(method, path, body)
        self.assertEqual((got, res["error"]["code"]), (status, code), res)
        return res["error"]["message"]

    def add(self, eid="fx", **fields):
        folder = make_extension(self.root / "ext" / eid, eid, **fields)
        return self.ok("POST", "/api/local/add", {"path": str(folder)}), folder

    def events(self, topic):
        res = self.ok("POST", "/api/addon_events", {"addon": "local", "topic": topic, "since": 0})
        return [e["data"] for e in res["events"]]


class LocalApi(LocalDaemon, unittest.TestCase):
    # --- HTTP

    def test_http_routes(self):
        row, folder = self.add()
        self.assertEqual((row["id"], row["state"]), ("fx", "stopped"))
        listing = self.ok("GET", "/api/local")
        self.assertEqual([r["id"] for r in listing["extensions"]], ["fx"])
        self.assertGreaterEqual(listing["revision"], 1)
        res = self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": "你好"}})
        self.assertEqual((res["result"]["text"], res["result"]["caller"]), ("你好", {"by": "agent"}))
        self.assertEqual(self.ok("GET", "/api/local")["extensions"][0]["state"], "running")
        manifest = self.ok("GET", "/api/local/manifest")
        ext = manifest["extensions"][0]
        self.assertEqual((ext["id"], ext["runtime"], ext["state"]), ("fx", "local", "on"))
        self.assertIn("fx.emit", [c["id"] for c in ext["capabilities"]])
        row = self.ok("POST", "/api/local/set", {"id": "fx", "options": {"volume": 70}})
        self.assertEqual(row["options"][0]["value"], 70)
        logs = self.ok("GET", "/api/local/logs?id=fx")
        self.assertIn("fixture up", [line["text"] for line in logs["lines"]])
        self.assertEqual(self.ok("POST", "/api/local/check", {"id": "fx"})["ok"], True)
        res = self.ok("POST", "/api/local/load", {"id": "fx"})
        self.assertEqual((res["row"]["state"], res["check"]["ok"]), ("running", True))
        self.assertEqual(self.ok("POST", "/api/local/stop", {"id": "fx"})["state"], "stopped")
        self.assertEqual(self.ok("POST", "/api/local/set", {"id": "fx", "enabled": False})["state"], "off")
        self.fails("POST", "/api/local/call", {"cap": "fx.echo", "args": {}}, 404, "no_capability")
        self.ok("POST", "/api/local/set", {"id": "fx", "enabled": True})
        for method, path, body, status, code in (
                ("POST", "/api/local/add", {"path": "relative"}, 400, "bad_request"),
                ("POST", "/api/local/add", {"path": str(self.root / "nothing")}, 400, "bad_manifest"),
                ("POST", "/api/local/load", {"id": "ghost"}, 404, "not_found"),
                ("POST", "/api/local/set", {"id": "fx", "options": {"volume": 500}}, 400, "bad_option"),
                ("GET", "/api/local/logs?id=fx&limit=0", None, 400, "bad_request"),
                ("POST", "/api/local/call", {"cap": "fx.picky", "args": {"n": "x"}}, 400, "bad_args"),
                ("POST", "/api/local/call", {"cap": "fx.boom"}, 422, "failed"),
                ("POST", "/api/local/call", {"cap": "fx.ghost"}, 404, "no_capability"),
                ("POST", "/api/local/call", {"cap": "fx.echo", "args": [1]}, 400, "bad_args"),
                ("POST", "/api/local/call", {"cap": "nodot"}, 400, "bad_request"),
                ("POST", "/api/local/new", {"id": "chat"}, 400, "bad_request"),
                ("POST", "/api/local/new", {"id": "fy", "folder": str(folder)}, 409, "folder_not_empty"),
                ("POST", "/api/reactions/set", {"hear": "nothing"}, 400, "bad_args")):
            self.fails(method, path, body, status, code)
        res = self.ok("POST", "/api/local/remove", {"id": "fx"})
        self.assertEqual(res, dict(removed=True, id="fx", path=str(folder), kept=None, addons=[]))
        self.assertEqual(self.ok("GET", "/api/local")["extensions"], [])

    # --- packages and the game half

    def row(self, eid):
        return next(r for r in self.ok("GET", "/api/local")["extensions"] if r["id"] == eid)

    def test_what_the_host_knows_of_the_game(self):
        host = self.svc.local
        self.assertEqual(host.taken_ids(), {"sense": None, "tune": None, "guide": None})   # WuxianKit's own, and
                                                                               # the addon each is of (not said here)
        self.assertEqual(Path(host.game_addons()), self.addons)
        self.assertIsNone(host.kit())                                          # (this manifest does not say)
        host.client_interface()                                                # never an error
        folder = make_extension(self.root / "ext" / "zed", "zed")
        with mock.patch.object(self.svc, "kit_manifest_known", return_value=dict(ZED_MANIFEST, kit="0.7.1")):
            self.assertEqual((host.taken_ids(), host.kit()), ({"zed": None}, "0.7.1"))
            self.assertIn("zed", self.fails("POST", "/api/local/add", {"path": str(folder)}, 400, "bad_manifest"))
        self.ok("POST", "/api/local/add", {"path": str(folder)})               # free again: it is added

    def test_pack_and_install(self):
        self.add()
        folder = with_game(self.root / "ext" / "gx", "gx", "WuxianGx")
        row = self.ok("POST", "/api/local/add", {"path": str(folder)})
        self.assertEqual(row["addons"], [dict(folder="WuxianGx", version="1.2.0", state="missing")])
        res = self.ok("POST", "/api/local/install", {"id": "gx"})
        self.assertEqual(res, dict(id="gx", restart=True, addons=[dict(folder="WuxianGx", state="installed",
                                                                       was="missing", version="1.2.0", kept=None)],
                                   hint="WuxianGx: new in the game's AddOns, restart the game to load it"))
        self.assertTrue((self.addons / "WuxianGx" / "core.lua").is_file())
        self.assertEqual(self.row("gx")["addons"], [dict(folder="WuxianGx", version="1.2.0", state="current")])
        res = self.ok("POST", "/api/local/install", {"id": "gx"})
        self.assertEqual((res["addons"][0]["state"], res["restart"], res["hint"]), ("current", False, None))
        res = self.ok("POST", "/api/local/load", {"id": "gx"})                 # a load says the same three
        self.assertEqual((res["addons"][0]["state"], res["restart"], res["hint"], res["check"]["ok"]),
                         ("current", False, None, True))
        res = self.ok("POST", "/api/local/load", {"id": "fx"})
        self.assertEqual((res["addons"], res["restart"], res["hint"]), ([], False, None))
        packed = self.ok("POST", "/api/local/pack", {"id": "gx"})
        self.assertEqual(sorted(packed), ["file", "files", "id", "sha256", "size", "skipped", "summary", "unpacked",
                                          "version", "warnings"])
        self.assertEqual((packed["summary"]["from"], packed["summary"]["package"]["sha256"], len(packed["warnings"])),
                         ("package", packed["sha256"], 1))
        self.assertIn("env sets FIXTURE_MODE", packed["warnings"][0])
        mine = self.ok("POST", "/api/local/summary", {"id": "gx"})             # the same facts, of the folder
        self.assertEqual((mine["from"], mine["where"], mine["risk"], mine["package"], mine["local"]),
                         ("folder", "both", "local", None, packed["summary"]["local"]))
        self.assertEqual(mine["game"], [dict(folder="WuxianGx", title="WuxianGx", notes=None, version="1.2.0",
                                             author=None, needs=[], path=str(self.addons / "WuxianGx"),
                                             cannot=None)])
        self.assertEqual(self.ok("POST", "/api/local/summary", {"path": packed["file"]}), packed["summary"])
        self.assertEqual(self.ok("POST", "/api/local/summary", {"path": str(folder)}), mine)
        self.assertEqual(self.ok("GET", "/api/local/summary?id=gx"), mine)     # the App reads it so
        self.fails("GET", "/api/local/summary", None, 400, "bad_request")
        self.fails("GET", "/api/local/summary?id=ghost", None, 404, "not_found")
        for body, status, code in (({}, 400, "bad_request"), ({"id": "ghost"}, 404, "not_found"),
                                   ({"path": "relative.zip"}, 400, "bad_request"),
                                   ({"path": str(self.root / "nothing")}, 404, "not_found")):
            self.fails("POST", "/api/local/summary", body, status, code)
        self.assertEqual((packed["id"], packed["version"], packed["file"]),
                         ("gx", "0.1.0", str(self.root / "home" / "packages" / "gx-0.1.0.zip")))
        self.assertEqual(K.read(packed["file"])["id"], "gx")
        there = self.root / "there"
        self.assertEqual(self.ok("POST", "/api/local/pack", {"id": "gx", "to": str(there)})["file"],
                         str(there / "gx-0.1.0.zip"))
        self.assertEqual(self.ok("POST", "/api/local/pack", {"id": "gx", "to": str(there / "mine.zip")})["file"],
                         str(there / "mine.zip"))
        for path, body, status, code in (
                ("/api/local/pack", {"id": "ghost"}, 404, "not_found"),
                ("/api/local/pack", {}, 400, "bad_request"),
                ("/api/local/pack", {"id": "gx", "to": "relative"}, 400, "bad_request"),
                ("/api/local/pack", {"id": "gx", "to": 7}, 400, "bad_request"),
                ("/api/local/install", {"id": "ghost"}, 404, "not_found"),
                ("/api/local/install", {}, 400, "bad_request"),
                ("/api/local/install", {"id": "fx"}, 400, "no_addons")):
            self.fails("POST", path, body, status, code)
        with mock.patch.object(self.svc.local, "addons_dir", return_value=None):
            self.fails("POST", "/api/local/install", {"id": "gx"}, 503, "no_game_folder")
            self.assertEqual(self.row("gx")["addons"][0]["state"], "unknown")
            res = self.ok("POST", "/api/local/load", {"id": "gx"})
            self.assertEqual((res["addons"][0]["state"], res["restart"], res["check"]["ok"]), ("unknown", False, True))
            self.assertIn("extension_install gx", res["hint"])
        res = self.ok("POST", "/api/local/remove", {"id": "gx"})
        (gone,) = res.pop("addons")
        self.assertEqual(res, dict(removed=True, id="gx", path=str(folder), kept=None))
        self.assertEqual((gone.pop("kept") is not None, gone),                 # (kept: where the history has it)
                         (True, dict(folder="WuxianGx", removed=True, why=None)))
        self.assertFalse((self.addons / "WuxianGx").exists())
        self.assertTrue((folder / "main.py").is_file())

    def test_an_addon_that_is_someone_elses(self):
        theirs = self.addons / "WuxianHx"
        theirs.mkdir()
        self.addCleanup(shutil.rmtree, theirs, True)
        (theirs / "theirs.lua").write_text("-- not ours\n", encoding="utf-8")
        self.ok("POST", "/api/local/add", {"path": str(with_game(self.root / "ext" / "hx", "hx", "WuxianHx"))})
        self.assertEqual(self.row("hx")["addons"], [dict(folder="WuxianHx", version="1.2.0", state="foreign")])
        status, res = self.call("POST", "/api/local/install", {"id": "hx"})
        self.assertEqual((status, res["error"]["code"]), (409, "addon_exists"), res)
        self.assertEqual((res["done"], res["error"]["done"]), ([], []))        # what it installed before that
        res = self.ok("POST", "/api/local/load", {"id": "hx"})                 # it runs all the same, and says so
        self.assertEqual((res["row"]["state"], res["addons"], res["check"]["ok"]), ("running", [], False))
        self.assertEqual([p["level"] for p in res["check"]["problems"] if p["where"] == "addons"], ["error"])
        res = self.ok("POST", "/api/local/remove", {"id": "hx"})
        self.assertEqual(res["addons"], [dict(folder="WuxianHx", removed=False, kept=None, why="foreign")])
        self.assertNotIn("hint", res)
        self.assertEqual(os.listdir(theirs), ["theirs.lua"])

    def test_a_package_from_the_folder_to_another_computer(self):
        folder = with_game(self.root / "ext" / "px", "px", "WuxianPx")
        home, sent = self.root / "home" / "extensions", self.root / "sent"
        self.ok("POST", "/api/local/add", {"path": str(folder)})
        packed = self.ok("POST", "/api/local/pack", {"id": "px", "to": str(sent)})
        status, res = self.call("POST", "/api/local/add", {"path": packed["file"]})    # never over a linked one
        self.assertEqual((status, res["error"]["code"], res["path"]), (409, "extension_exists", str(folder)), res)
        self.assertIn("extension_remove px", res["error"]["message"])
        self.assertEqual(self.ok("POST", "/api/local/remove", {"id": "px"})["addons"], [])
        row = self.ok("POST", "/api/local/add", {"path": packed["file"]})      # as another computer gets it
        self.assertEqual((row["id"], row["origin"], row["path"], row["approval"], row["allowed"], row["package"]),
                         ("px", "home", str(home / "px"), "needed", False, dict(file=packed["file"], replaced=None)))
        self.assertEqual(row["addons"], [dict(folder="WuxianPx", version="1.2.0", state="missing")])
        self.assertEqual(row["capabilities"], [])                              # nothing of it ran (auto or not)
        for path, body in (("/api/local/install", {"id": "px"}), ("/api/local/load", {"id": "px"}),
                           ("/api/local/call", {"cap": "px.echo", "args": {"text": "a"}})):
            self.fails("POST", path, body, 403, "needs_approval")
        self.assertFalse((self.addons / "WuxianPx").exists())
        row = self.ok("POST", "/api/local/approve", {"id": "px"})              # the player allows it: its game half too
        self.assertEqual((row["approval"], row["addons"]),
                         ("ok", [dict(folder="WuxianPx", version="1.2.0", state="current")]))
        self.assertEqual((row["installed"]["ok"], row["installed"]["restart"]), (True, True))
        self.assertTrue((self.addons / "WuxianPx" / "core.lua").is_file())
        res = self.ok("POST", "/api/local/install", {"id": "px"})
        self.assertEqual((res["addons"][0]["state"], res["restart"], res["hint"]), ("current", False, None))
        res = self.ok("POST", "/api/local/call", {"cap": "px.echo", "args": {"text": "到了"}})
        self.assertEqual(res["result"]["text"], "到了")
        self.ok("POST", "/api/local/set", {"id": "px", "options": {"volume": 70}})
        newer = K.pack(with_game(folder, "px", "WuxianPx", addon_version="1.3.0", version="0.2.0"), sent)["file"]
        status, res = self.call("POST", "/api/local/add", {"path": newer})     # an update: asked first
        self.assertEqual((status, res["error"]["code"], res["old"]["version"], res["new"]["version"], res["same"]),
                         (409, "needs_replace", "0.1.0", "0.2.0", False), res)
        self.assertEqual(res["changes"], [dict(field="version", old="0.1.0", new="0.2.0")])
        self.assertEqual((res["old"]["package"]["file"], res["new"]["package"]["file"]), ("px-0.1.0.zip", "px-0.2.0.zip"))
        self.fails("POST", "/api/local/add", {"path": newer, "replace": "yes"}, 409, "needs_replace")
        self.assertEqual(self.ok("POST", "/api/local/call", {"cap": "px.opts"})["result"], {"volume": 70})   # untouched
        row = self.ok("POST", "/api/local/add", {"path": newer, "replace": True})   # the player is asked again
        kept = Path(row["package"]["replaced"])
        self.assertEqual((row["version"], row["path"], row["approval"], row["enabled"], kept.parent),
                         ("0.2.0", str(home / "px"), "needed", True, home / ".removed"))
        self.assertEqual(row["addons"], [dict(folder="WuxianPx", version="1.3.0", state="differs")])
        self.assertEqual(self.svc.local.reg.entry("px")["options"], {"volume": 70})
        self.fails("POST", "/api/local/call", {"cap": "px.echo", "args": {"text": "a"}}, 403, "needs_approval")
        row = self.ok("POST", "/api/local/approve", {"id": "px"})
        self.assertEqual(row["addons"], [dict(folder="WuxianPx", version="1.3.0", state="current")])
        self.assertEqual([(a["folder"], a["state"], a["was"]) for a in row["installed"]["addons"]],
                         [("WuxianPx", "installed", "differs")])
        self.assertEqual(self.ok("POST", "/api/local/call", {"cap": "px.opts"})["result"], {"volume": 70})
        res = self.ok("POST", "/api/local/remove", {"id": "px"})
        self.assertEqual((Path(res["kept"]).parent, [(a["folder"], a["removed"]) for a in res["addons"]]),
                         (home / ".removed", [("WuxianPx", True)]))
        self.assertFalse((self.addons / "WuxianPx").exists())
        self.assertFalse((home / "px").exists())
        self.assertEqual(self.ok("GET", "/api/local")["extensions"], [])

    def test_a_package_that_is_refused(self):
        home, out = self.root / "home" / "extensions", self.root / "refused"
        out.mkdir()
        (out / "junk.zip").write_bytes(b"no zip at all")
        self.fails("POST", "/api/local/add", {"path": str(out / "junk.zip")}, 400, "bad_package")
        self.fails("POST", "/api/local/add", {"path": str(out / "none.zip")}, 400, "bad_package")
        taken = K.pack(make_extension(self.root / "ext" / "zed2", "zed"), out)["file"]
        with mock.patch.object(self.svc, "kit_manifest_known", return_value=ZED_MANIFEST):   # an id WuxianKit has
            status, res = self.call("POST", "/api/local/add", {"path": taken})
        self.assertEqual((status, res["error"]["code"]), (400, "bad_package"), res)
        self.assertTrue(res["problems"])
        self.assertEqual(res["problems"], res["error"]["problems"])
        good = K.pack(make_extension(self.root / "ext" / "big", "big"), out)["file"]
        with mock.patch.object(K, "MAX_ZIP", 64):
            self.fails("POST", "/api/local/add", {"path": good}, 413, "package_too_big")
        self.assertEqual(self.ok("GET", "/api/local")["extensions"], [])
        self.assertFalse((home / ".incoming").exists())
        self.assertFalse((home / "zed").exists() or (home / "big").exists())

    def test_a_slow_start_is_not_the_calls_time(self):
        """the first call waits for the start (initialize) on top of the call's time: the capability's own timeout, or
        timeout_ms when the caller gives one (shorter or longer)"""
        self.add()
        original = self.svc.local.initialize

        def slow(*args, **kw):
            time.sleep(1.5)
            return original(*args, **kw)
        with mock.patch.object(self.svc.local, "initialize", side_effect=slow):
            res = self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": "a"}, "timeout_ms": 1000})
        self.assertEqual(res["result"]["text"], "a")
        msg = self.fails("POST", "/api/local/call", {"cap": "fx.slow", "args": {"seconds": 3}, "timeout_ms": 100},
                         504, "timeout")
        self.assertIn("within 0.1 s", msg)                   # what the caller gave, not the longer of the two
        msg = self.fails("POST", "/api/local/call", {"cap": "fx.slow", "args": {"seconds": 3}}, 504, "timeout")
        self.assertIn("within 1 s", msg)                     # none given: its own timeout (1 s), not 30 s
        res = self.ok("POST", "/api/local/call", {"cap": "fx.slow", "args": {"seconds": 1.3}, "timeout_ms": 5000})
        self.assertEqual(res["result"], {"slept": 1.3})      # given longer than its own: the caller's

    def test_sse_tells_of_changes(self):
        req = urllib.request.Request(f"{self.handle.url.rstrip('/')}/api/events?token={self.handle.token}"
                                     f"&since={self.svc.journal.next_id}&kinds=NONE")
        with urllib.request.urlopen(req, timeout=20) as resp:
            self.add("sse")
            seen, event, data, deadline = [], None, [], time.time() + 20
            while time.time() < deadline and not seen:
                line = resp.readline().decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and data:
                    if event == "local":
                        seen.append(json.loads("".join(data)))
                    event, data = None, []
        self.assertTrue(seen)
        self.assertEqual([r["id"] for r in seen[0]["extensions"]], ["sse"])
        self.assertGreaterEqual(seen[0]["revision"], 1)

    # --- calls of the game's WuxianKit, an extension's own calls, its items

    def test_kit_capabilities_and_own_calls(self):
        res = self.ok("POST", "/api/local/call", {"cap": "sense.bags", "args": {"bag": 1}})
        self.assertEqual(res["result"], {"free": 12, "args": {"bag": 1}})
        self.assertIn("proposal", self.fails("POST", "/api/local/call", {"cap": "tune.cvar.set"}, 403, "not_allowed"))
        self.fails("POST", "/api/local/call", {"cap": "guide.note"}, 404, "no_capability")   # its extension is off
        self.fails("POST", "/api/local/call", {"cap": "ghost.thing"}, 404, "no_capability")
        self.add()
        res = self.ok("POST", "/api/local/call", {"cap": "fx.ask", "args": {"cap": "sense.bags", "args": {"bag": 2}}})
        self.assertEqual(res["result"], {"result": {"free": 12, "args": {"bag": 2}}, "error": None})
        res = self.ok("POST", "/api/local/call", {"cap": "fx.ask", "args": {"cap": "tune.cvar.set"}})
        self.assertEqual((res["result"]["error"]["code"], res["result"]["error"]["data"]), (1, {"reason": "not_allowed"}))
        res = self.ok("POST", "/api/local/call", {"cap": "fx.ask", "args": {"cap": "fx.echo", "args": {"text": "x"}}})
        self.assertEqual(res["result"]["result"]["caller"], {"by": "extension", "id": "fx", "chain": ["ext:fx"]})
        self.ok("POST", "/api/local/call", {"cap": "fx.emit", "args": {"text": "说完"}})
        self.assertTrue(until(lambda: self.events("fx.said")))
        item = self.events("fx.said")[0]
        self.assertEqual((item["source"], item["kind"], item["data"]), ("fx", "fx.said", {"text": "说完"}))
        self.assertTrue(self.events("local.changed"))

    def test_apps_declared(self):
        app = {"app": "companion", "title": "语音搭子", "prompt": "companion", "hear": ["game:combat"]}
        _, folder = self.add(apps=[app])
        title = json.loads((folder / "wuxian-extension.json").read_text(encoding="utf-8"))["title"]
        app = dict(app, local={"id": "fx", "title": title})          # the extension it comes with
        self.assertTrue(until(lambda: self.kit.asked("kit.app.declare")))
        self.assertEqual(self.kit.asked("kit.app.declare"), [app])
        self.assertNotIn("launch", self.kit.asked("kit.app.declare")[0])
        self.kit.calls.clear()
        self.kit.emit("kit.ready", {})                     # a login or a reload: compared, the same, not declared
        self.assertTrue(until(lambda: self.kit.asked("kit.app.describe")), self.kit.calls)
        time.sleep(0.3)
        self.assertEqual(self.kit.asked("kit.app.declare"), [])
        self.assertEqual(self.kit.asked("kit.app.describe"), [{"app": "companion"}])
        self.kit.apps["companion"]["title"] = "别的"        # the game's is not what it took from us: declared again
        self.kit.calls.clear()
        self.kit.emit("kit.ready", {})
        self.assertTrue(until(lambda: self.kit.asked("kit.app.declare")), self.kit.calls)
        self.assertEqual(self.kit.asked("kit.app.declare"), [app])
        self.kit.calls.clear()
        self.ok("POST", "/api/local/set", {"id": "fx", "enabled": False})     # off with the game online: forgotten now
        self.assertEqual(self.kit.asked("kit.app.forget"), [{"app": "companion"}])
        self.assertEqual(self.kit.apps, {})
        self.ok("POST", "/api/local/set", {"id": "fx", "enabled": True})
        self.assertEqual(self.kit.asked("kit.app.declare"), [app])
        self.kit.calls.clear()
        self.ok("POST", "/api/local/remove", {"id": "fx"})
        self.assertEqual(self.kit.asked("kit.app.forget"), [{"app": "companion"}])

    def test_apps_of_a_kit_before_describe(self):
        """a WuxianKit without kit.apps and kit.app.describe: the apps are declared at every kit.ready, as before"""
        app = {"app": "companion", "title": "语音搭子", "prompt": "companion"}
        answer = self.kit.call_exposed

        async def old(addon, name, args=None, timeout_ms=10000, shared=None):
            if name in ("kit.apps", "kit.app.describe", "kit.app.forget"):
                self.kit.calls.append((name, args))
                raise ApiError(400, "call_failed", f"WuxianKit has no {name}")
            return await answer(addon, name, args, timeout_ms, shared)
        with mock.patch.object(self.svc, "call_exposed", old):
            self.add(apps=[app])
            self.assertEqual(len(self.kit.asked("kit.app.declare")), 1)
            self.kit.emit("kit.ready", {})
            self.assertTrue(until(lambda: len(self.kit.asked("kit.app.declare")) == 2), self.kit.calls)

    # --- reactions

    def test_reactions(self):
        self.add()
        self.fails("POST", "/api/reactions/set", {"hear": "game:combat", "call": "fx.echo"}, 400, "bad_args")
        self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": ""}})   # described: its kinds known
        game = self.ok("POST", "/api/reactions/set", {"id": "g", "owner": "companion", "hear": "game:combat",
                                                      "call": "fx.echo", "args": {"text": "战斗"}, "cooldown": 0})
        self.assertEqual(game["id"], "g")
        self.assertTrue(until(lambda: self.kit.asked("kit.app.open")))
        opened = self.kit.asked("kit.app.open")[-1]
        self.assertEqual(opened, {"app": "reactions", "lease": 0, "minutes": 720,      # a temporary app: its declaration
                                  "temp": {"title": "反射", "hear": ["game:combat"], "hidden": True, "digest": False}})
        self.ok("POST", "/api/reactions/set", {"id": "l", "hear": "fx:said", "call": "fx.echo", "args": {"text": "本机"},
                                               "cooldown": 0})
        self.assertEqual(self.kit.asked("kit.app.open")[-1]["temp"]["hear"], ["game:combat"])   # a local source: not the game's
        self.kit.emit("app.reactions", {"app": "reactions", "reason": "item",
                                        "items": [{"id": 1, "source": "game", "kind": "combat", "data": {}}]})
        self.assertTrue(until(lambda: self.reaction("g")["fired"] == 1), self.reaction("g"))
        self.ok("POST", "/api/local/call", {"cap": "fx.emit", "args": {"text": "x"}})
        self.assertTrue(until(lambda: self.reaction("l")["fired"] == 1), self.reaction("l"))
        self.kit.emit("kit.app", {"app": "companion", "on": False})       # its owner stopped: its reactions go
        self.assertTrue(until(lambda: self.reaction("g") is None))
        self.assertTrue(until(lambda: self.kit.asked("kit.app.stop")))    # no game source left
        self.assertEqual(self.kit.asked("kit.app.stop")[-1], {"app": "reactions"})
        self.assertEqual(self.ok("POST", "/api/reactions/clear", {"id": "l"}), {"cleared": 1})

    def test_reactions_after_kit_ready(self):
        self.ok("POST", "/api/reactions/set", {"hear": "game:quest", "call": "sense.bags"})
        self.kit.calls.clear()
        self.kit.emit("kit.ready", {})
        self.assertTrue(until(lambda: self.kit.asked("kit.app.open")))
        self.assertEqual(self.kit.asked("kit.app.open")[-1]["temp"]["hear"], ["game:quest"])

    def reaction(self, rid):
        return next((r for r in self.ok("GET", "/api/reactions")["reactions"] if r["id"] == rid), None)

    # --- new_extension

    def test_new_extension(self):
        def files(folder, eid, title, addon=None, taken=(), where=None):
            make_extension(folder, eid, title=title or eid)
            return [str(Path(folder) / "wuxian-extension.json"), str(Path(folder) / "main.py")]
        with mock.patch.object(scaffold, "new_extension_files", side_effect=files, create=True) as made:
            res = self.ok("POST", "/api/local/new", {"id": "fresh", "title": "新的"})
        folder = self.root / "home" / "extensions" / "fresh"
        made.assert_called_once_with(str(folder), "fresh", "新的", addon=None, where="local",
                                     taken={"sense": None, "tune": None, "guide": None})
        self.assertEqual((res["row"]["id"], res["row"]["title"], res["row"]["path"]), ("fresh", "新的", str(folder)))
        self.assertEqual(len(res["files"]), 2)
        self.assertIn("extension_load", res["guide"])
        with mock.patch.object(scaffold, "new_extension_files", None, create=True):
            self.fails("POST", "/api/local/new", {"id": "other"}, 501, "not_available")

    def test_new_extension_with_a_game_half(self):
        home = self.root / "home" / "extensions"
        res = self.ok("POST", "/api/local/new", {"id": "pair", "title": "一对", "addon": " WuxianPair "})
        folder = home / "pair"
        self.assertEqual((res["row"]["id"], res["row"]["path"]), ("pair", str(folder)))
        self.assertEqual([(a["folder"], a["state"]) for a in res["row"]["addons"]], [("WuxianPair", "missing")])
        self.assertTrue((folder / "addons" / "WuxianPair" / "WuxianPair.toc").is_file())
        self.assertIn("addons/WuxianPair/WuxianPair.toc", [f.replace("\\", "/") for f in res["files"]])
        self.assertIn("addons\\WuxianPair", res["guide"])
        manifest = json.loads((folder / "wuxian-extension.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["addons"], ["WuxianPair"])
        res = self.ok("POST", "/api/local/load", {"id": "pair"})               # as made it passes, and its game half
        self.assertEqual(res["check"]["ok"], True, res["check"])               # goes into the game's AddOns
        self.assertEqual(([(a["folder"], a["state"], a["was"]) for a in res["addons"]], res["restart"]),
                         ([("WuxianPair", "installed", "missing")], True))
        self.assertIn("restart the game", res["hint"])
        self.assertTrue((self.addons / "WuxianPair" / "WuxianPair.toc").is_file())
        for body in ({"id": "odd", "addon": 7}, {"id": "odd", "addon": " "}, {"id": "odd", "addon": "no good"}):
            self.fails("POST", "/api/local/new", body, 400, "bad_request")
        with mock.patch.object(self.svc.local, "taken", return_value=["odd_game"]):    # its game half's id is taken
            self.fails("POST", "/api/local/new", {"id": "odd", "addon": "WuxianOdd"}, 400, "bad_request")
        self.assertFalse((home / "odd").exists())
        self.assertEqual([r["id"] for r in self.ok("GET", "/api/local")["extensions"]], ["pair"])
        res = self.ok("POST", "/api/local/remove", {"id": "pair"})
        self.assertEqual([(a["folder"], a["removed"]) for a in res["addons"]], [("WuxianPair", True)])
        self.assertFalse((self.addons / "WuxianPair").exists())

    # --- MCP

    def test_mcp_tools(self):
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        self.add()
        self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": ""}})

        async def go():
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {self.handle.token}"}) as http:
                async with Client(streamable_http_client(self.handle.url + "mcp", http_client=http)) as c:
                    tools = {t.name: t for t in (await c.list_tools()).tools}
                    out = {"list": await c.call_tool("local_extensions", {}),
                           "guide": await c.call_tool("extension_guide", {}),
                           "set": await c.call_tool("reaction_set", {"spec": {"hear": "fx:said", "call": "fx.echo"}}),
                           "reactions": await c.call_tool("reactions", {}),
                           "clear": await c.call_tool("reaction_clear", {}),
                           "pack": await c.call_tool("extension_pack", {"id": "fx", "to": str(self.root / "by-mcp")}),
                           "summary": await c.call_tool("extension_summary", {"id": "fx"}),
                           "no summary": await c.call_tool("extension_summary", {}),
                           "install": await c.call_tool("extension_install", {"id": "fx"}),
                           "logs": await c.call_tool("extension_logs", {"id": "ghost"})}
                    return tools, out

        tools, out = asyncio.run(go())
        for name in ("local_extensions", "extension_logs", "extension_guide", "reactions", "extension_summary"):
            self.assertTrue(tools[name].annotations.read_only_hint, name)
        for name in ("new_extension", "extension_add", "extension_remove", "extension_load", "extension_stop",
                     "extension_set", "extension_check", "extension_pack", "extension_install", "reaction_set",
                     "reaction_clear"):                                        # a check starts a process
            self.assertFalse(tools[name].annotations.read_only_hint, name)
        self.assertFalse([n for n in tools if n.startswith("program") or n == "new_program"])
        self.assertEqual(out["list"].structured_content["extensions"][0]["id"], "fx")
        self.assertTrue(out["guide"].content[0].text)
        self.assertEqual(out["set"].structured_content["hear"], "fx:said")
        self.assertEqual(len(out["reactions"].structured_content["reactions"]), 1)
        self.assertEqual(out["clear"].structured_content, {"cleared": 1})
        self.assertEqual(out["pack"].structured_content["file"], str(self.root / "by-mcp" / "fx-0.1.0.zip"))
        self.assertEqual(out["pack"].structured_content["summary"]["package"]["file"], "fx-0.1.0.zip")
        s = out["summary"].structured_content
        self.assertEqual((s["from"], s["id"], s["risk"], s["local"]["run"], s["package"]),
                         ("folder", "fx", "local", ["python", "main.py"], None))
        self.assertTrue(out["no summary"].is_error)
        self.assertIn("bad_request", out["no summary"].content[0].text)
        self.assertTrue(out["install"].is_error)
        self.assertIn("no_addons", out["install"].content[0].text)
        self.assertTrue(out["logs"].is_error)
        self.assertIn("not_found", out["logs"].content[0].text)

    # --- the command line

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli(self):
        folder = make_extension(self.root / "ext" / "clix", "clix")
        code, out, err = self.run_cli("ext", "list")
        self.assertEqual(code, 0, err)
        self.assertIn("no local extensions registered", out)
        code, out, err = self.run_cli("ext", "add", str(folder))
        self.assertEqual(code, 0, err)
        self.assertIn("registered: clix", out)
        code, out, err = self.run_cli("wk", "clix.echo", '{"text": "命令行"}')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["text"], "命令行")
        code, out, err = self.run_cli("ext", "set", "clix", "--option", "volume=30")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("ext", "list", "--json")
        self.assertEqual(json.loads(out)["extensions"][0]["options"][0]["value"], 30)
        code, out, err = self.run_cli("ext", "load", "clix")
        self.assertEqual(code, 0, err)
        self.assertIn("ok: 8 capabilities", out)
        code, out, err = self.run_cli("ext", "check", str(folder))
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("ext", "logs", "clix")
        self.assertIn("fixture up", out)
        code, out, err = self.run_cli("ext", "dir")
        self.assertEqual((code, out.strip()), (0, str(self.root / "home" / "extensions")), err)
        code, out, err = self.run_cli("ext", "pack", "clix", "--to", str(self.root / "by-cli"))
        self.assertEqual(code, 0, err)
        self.assertIn(f"packed clix 0.1.0: {self.root / 'by-cli' / 'clix-0.1.0.zip'}", out)
        code, out, err = self.run_cli("ext", "install", "clix")
        self.assertEqual(code, 1)
        self.assertIn("no_addons", err)
        code, out, err = self.run_cli("reaction", "set", '{"id": "c", "hear": "clix:said", "call": "clix.echo"}')
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("reaction", "list")
        self.assertIn("c: clix:said -> clix.echo", out)
        code, out, err = self.run_cli("reaction", "clear", "--id", "c")
        self.assertIn("cleared 1", out)
        code, out, err = self.run_cli("wk", "clix.boom")
        self.assertEqual(code, 1)
        self.assertIn("failed", err)
        code, out, err = self.run_cli("ext", "stop", "clix")
        self.assertIn("stopped", out)
        code, out, err = self.run_cli("ext", "remove", "clix")
        self.assertIn("clix is no longer registered", out)


if __name__ == "__main__":
    unittest.main()
