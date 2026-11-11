r"""A fake daemon for developing the web page: the endpoints of the daemon (daemon/server.py) that the page uses, with made-up
data that moves (log lines every second, a link that drops and comes back, pings that drift), served together with the page
from src/wuxianworkshop/ui/static.

    .venv\Scripts\python.exe scripts\dev_fake_api.py [--port 8765] [--token TEXT] [--tick 1.0] [--quiet]
                                                     [--mode developer|player] [--approval ask|auto] [--rotate 400]
                                                     [--no-game-folder]
        prints the address and the token; open the address in a browser, or open the window on it:
    .venv\Scripts\python.exe -c "from wuxianworkshop.ui.shell import run_app; run_app(['--fake'])"
        (starts this in-process on a free port; or run_app(['--fake', 'http://127.0.0.1:8765/']) for one that is running)

Same rules as the real daemon: /api/* wants Authorization: Bearer <token> (SSE and the snap PNGs also accept ?token=), Host
must be 127.0.0.1 or localhost, and /api/session hands the token to same-origin pages only. Nothing here touches the game:
snaps are drawn with Pillow into a temporary folder, "run" answers canned values, "install" only turns the doctor's red items
green. tests/ui/test_fake_api.py drives create_app() with Starlette's TestClient; the shell calls start_in_thread().

The local extensions (the 扩展 page) come in every state: running, stopped, exited, off, not responding, waiting for its
next try, a manifest that cannot be read, and the ones whose command waits for the player (awaiting approval, command
changed). --approval auto (or 本机扩展批准 set to 自动批准 on the page's 设置) allows every command by itself: the setting
extension_approval, "ask" unless said. The mode (--mode) has no part in it.
--rotate N starts voice's output again at line 1 after N lines, as the real output.log does at 2 MB.

Managing them (the 本机扩展 group's 添加, a row's menu, 游戏插件), in the shapes of extensions-home.md (sections 4 and 7).
A row carries origin (home: its folder is in the extensions' home; linked: it is elsewhere), author, homepage, requires,
unmet and addons ([{folder, version, state}]); the listing carries home. Three more pretend extensions: herald (in the
home, with two game addons: one missing, one that differs, so 安装 has something to do), relay (linked, needs a newer
workshop and a newer WuxianKit: unmet, a load answers 409 requires_unmet) and greeter (came from a package: it waits for
the player whatever the setting, and its card names the game addon it brings). Nothing is unpacked, written or moved:
the names in a path decide what is answered.

Where one runs (contract6). A row carries where ("game", "local" or "both"; null while its manifest cannot be read), game
(null without game addons, else {extensions, state}: unknown, absent, offline, restart, missing, disabled, off, installed
or on, as Host.game_of) and player (null, or {action, text}: the one thing only the player can do now, as
Host.player_of). Two more pretend extensions are in the game only: tips (in the game and on: WuxianKit lists it with two
tools) and marks (its addon is not in the game yet: a load installs it, then the game has to start again, which the
link's next drop stands for). One in the game only has no process and no command: its state is "game", nobody approves
it unless it came in a package, and
    load   checks its Lua first (an id with "syntax": 422 check_failed, the findings in the error's addons, nothing
           installed), installs its addons and answers {row, check, addons, restart, hint, loaded}; loaded: [{folder, ok,
           error, check}], what ran in the running game ([] while it is offline; an id with "fails": ok false)
    check  {ok, problems, capabilities: [], where: "game", addons: [{folder, path, ok, errors, warnings}]} (an id with
           "warn": a warning), without the player's yes
    stop   its row with stopped false and a note
    POST /api/local/wait {id}      {row, was, timeout}: answered at once (nobody does the player's part here), timeout
                                   true while the row has something for the player

    POST /api/local/add {path, replace?}   a folder (or its wuxian-extension.json): a linked row, its id from the
                                   folder's name; a .zip: a row in the home that waits for the player, with package
                                   {file, replaced}. A .zip whose id is in the home already -> 409 needs_replace with
                                   old, new (the two summaries), changes and same (name contains "same": true), and
                                   nothing changed; with replace true it takes the old one's place (replaced: where
                                   the old folder went).
                                     name ends with "exists" -> 409 extension_exists   "bad.zip" -> 400 bad_package
                                     "big.zip" -> 413 package_too_big                  folder "bad" -> 400 bad_manifest
                                     a zip whose id is a linked row's -> 409 extension_exists
                                     name contains "game" -> the row brings a game addon (missing)
                                     name contains "ingame" -> it is in the game only (that addon, no process)
                                     name contains "foreign" -> a game addon another one owns (install: 409 addon_exists)
                                     name contains "needs" -> the row needs a newer WuxianKit (unmet, not refused)
    POST /api/local/new {id, title?, folder?, addon?, where?}   {row, files, guide}; in the home unless folder is
                                   given; with addon the row brings that game addon (missing). where: game, local or
                                   both (not given: both with an addon, else local); game and both without an addon name
                                   it after the id (my_tool -> MyTool); local with an addon, or another where -> 400
                                   bad_request. An id that is taken (or ends with "exists") -> 409 extension_exists; no
                                   id -> 400 bad_request; an addon name that is no folder name, or WuxianKit / WoWBridge
                                   / Blizzard_* -> 400 bad_addon
    POST /api/local/remove {id}    {removed, id, path, kept, addons: [{folder, removed, kept}]}; kept: where the folder
                                   went (the home's .removed) or null for a linked one. One in the home whose id ends
                                   with "busy" -> 409 folder_busy (and it stays)
    POST /api/local/pack {id, to?} {id, version, file, size, files, sha256, skipped, summary, warnings}; to: a folder
                                   or a .zip path, else the packages folder. summary: the package's (below); warnings:
                                   one for stats (its manifest's env sets a value). One whose manifest cannot be read
                                   (draft) -> 400 bad_manifest
    GET  /api/local/summary?id= | ?path=   (POST {id?, path?} as well) what an extension says of itself, as
                                   local/summary.py gives it: {from: folder | package, id, title, version, notes, author,
                                   homepage, package, where, risk: game | local, game: [{folder, title, notes, version,
                                   author, needs: [{addon, installed}], path}], addons_dir, local: {run, python, start,
                                   env, pass} | null, executables, agent: {docs, skills, apps, adds}, files, size}.
                                   path: a row's folder, or a .zip (read by its name as add reads it; nothing is added).
                                   Neither -> 400 bad_request; unknown -> 404 not_found
    POST /api/local/install {id}   {id, addons: [{folder, state, was, version, kept}], restart, hint}. Not allowed yet
                                   -> 403 needs_approval; no game addons -> 400 no_addons; --no-game-folder -> 503
                                   no_game_folder (and every addon's state is "unknown"); a foreign one -> 409
                                   addon_exists. 批准运行 (approve) installs them too, and so does a start
                                   (load answers addons, restart and hint as well when one was missing or differed)
--no-game-folder: the game's AddOns folder is not known.
"""
import argparse
import asyncio
import collections
import contextlib
import json
import os
import random
import re
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:            # when the package is not installed in the interpreter that runs this
    sys.path.insert(0, str(ROOT / "src"))

from wuxianworkshop import __version__           # noqa: E402
from wuxianworkshop.ui import STATIC_DIR         # noqa: E402

GAME_DIR = r"C:\Program Files (x86)\World of Warcraft\_forever_"
OUT_LINES = ["WoWBridge: hb %d (q=0)", "Hello from the addon: print() goes here", "DBM-Core: 已加载 12 个模块",
             "[ElvUI] Profile switched to Default", "WeakAuras: 3 auras loaded", "Details!: segment 4 closed (02:13)"]
INFO_LINES = ["frame {n} ok (64x16 cells, 2 parts)", "mailbox slot {slot:05d} written (ack {n})", "code {job}: =run (38 B, 1 part)"]
WARN_LINES = ["Interface/AddOns/Foo/Core.lua:88: Unknown unit token 'focus'",
              "Interface/AddOns/Bar/Util.lua:12: variable 'tbl' shadows a global"]
BLOCKED_LINES = ["ADDON_ACTION_BLOCKED Foo CastSpellByName()", "ADDON_ACTION_FORBIDDEN Bar TargetUnit()"]
ERRORS = [("Interface/AddOns/Foo/Core.lua:12: attempt to index a nil value (field 'db')",
           "Interface/AddOns/Foo/Core.lua:12: in function <Interface/AddOns/Foo/Core.lua:9>\n[C]: in function `pcall'\n"
           "Interface/AddOns/Foo/Events.lua:44: in function `OnEvent'", "Foo"),
          ("Interface/AddOns/Bar/Frames.lua:201: attempt to call method 'SetPoint' (a nil value)",
           "Interface/AddOns/Bar/Frames.lua:201: in function `Layout'\nInterface/AddOns/Bar/Bar.lua:17: in main chunk", "Bar")]
SLOTS_TEXT = ("mailbox: %d slots left in this game process (about %.1f h at a heartbeat every 15 s); when they are gone, "
              "only a full restart of the game brings new ones")
ARITHMETIC = re.compile(r"^return\s+([\d\s+\-*/%().]+)$")
VOICE_LINES = ["said: 小心，精英怪来了 (1.8 s)", "said: 打完这波歇一下吧 (2.1 s)", "queue: 0 waiting",
               "said: 背包快满了 (1.2 s)"]
EXT_HOME = r"C:\Users\Someone\AppData\Local\WuxianWorkshop\extensions"
PACKAGES = r"C:\Users\Someone\AppData\Local\WuxianWorkshop\packages"       # where 打包 writes unless told
LOCAL_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")             # manifest.ID
ADDON_FOLDER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.!-]{0,63}$")          # manifest.ADDON_FOLDER
LOCAL_COMMAND = [r"C:\Python314\python.exe", "main.py"]      # a pretend local extension's argv ({path}: its folder)
LOCAL_LIVE = ("starting", "running", "hung", "stopping")     # the states with a process


class Refused(Exception):
    """an answer with its own status and code: {error: {code, message, **extra}, **extra} (ApiError.body)"""

    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def player_says(action, title, addon=None, changed=False):
    """{action, text}: one thing only the player can do now, and the sentence for them (Host.player_says: the same
    words, tests/ui/test_fake_local.py holds the two together)"""
    text = {
        "approve": (f"「{title}」要运行的内容变了，请在无限工坊 App 的「扩展」页重新批准" if changed else
                    f"请在无限工坊 App 的「扩展」页批准「{title}」"),
        "start_game": "请启动游戏并进入世界，窗口不要最小化",
        "restart_game": f"请重启游戏：新装的插件「{addon}」重启后才会载入",
        "enable_addon": f"请在游戏的插件列表里启用「{addon}」，然后重载界面",
        "turn_on": f"请在游戏里输入 /wk，打开扩展「{title}」",
    }[action]
    return dict(action=action, text=text)


def addon_name_of(eid):
    """the folder name a new extension's game addon gets when none is given (scaffold.addon_name_of)"""
    return "".join(part[:1].upper() + part[1:] for part in eid.split("_") if part)


def api_version():
    try:
        from wuxianworkshop import content
        return content.PACKS.version("api")
    except Exception:
        return None


class Fake:
    """the pretend daemon's state: logs (a ring of 5,000), subscribers, settings, the link that comes and goes"""

    def __init__(self, token=None, mode="developer", tick=1.0, seed=None, rotate=400, extension_approval="ask",
                 addons_known=True):
        self.token = token or secrets.token_urlsafe(32)
        self.port = None
        self.mode = mode
        self.extension_approval = "auto" if extension_approval == "auto" else "ask"   # anything else is "ask"
        self.tick = tick
        self.rotate = rotate                     # a local extension's output starts again at line 1 after this many
        self.addons_known = bool(addons_known)   # False: the game's AddOns folder is not known (--no-game-folder)
        self.started = time.time()
        self.rng = random.Random(seed)
        self.logs = collections.deque(maxlen=5000)
        self.next_id = 1
        self.last_job = 1700000
        self.subscribers = set()
        self.settings = {"mode": mode, "capture": "wgc", "capture_in_use": "wgc", "game_dir": GAME_DIR, "autostart": False,
                         "language": "auto", "font_scale": 100, "onboarded": False,
                         "extension_approval": self.extension_approval}   # the daemon's shape (daemon/service.py settings())
        # the agents' page (agents.py's shape): Claude Code points at an older copy, Codex, Trae CN and WorkBuddy are not
        # connected, no Cursor and no international Trae
        self.agents = {"claude": "other", "codex": "absent", "cursor": "missing", "trae-cn": "absent", "trae": "missing",
                       "workbuddy": "absent", "workbuddy-ai": "missing"}
        self.kept = self.kept_seed()             # agent/history.py's shapes: addon -> versions oldest first, and "now"
        self.ext_installed = {"wuxiankit": "0.2.0", "wuxiankit-guides": "0.1.0"}   # extensions.py's shapes: the catalog's addons in the game folder
        self.local = self.local_seed()             # local/host.py's shapes: id -> a local extension, in its order
        self.local_revision = 7
        self.local_out = {i: self.local_log_seed(e) for i, e in self.local.items()}   # id -> its output.log lines
        self.watch = []
        self.reload_pending = False
        self.fixed = False
        self.game_found = True
        self.game_start = self.started - 1900
        self.link_state = "online"                 # online | offline | handshake
        self.session = 48879
        self.slots_left = 3987
        self.ping = 0.31
        self.last_frame = self.started
        self.n = 0
        self.snaps = Path(tempfile.mkdtemp(prefix="wuxian-fake-snaps-"))
        self.new_addons = Path(tempfile.mkdtemp(prefix="wuxian-fake-addons-"))     # 新建插件 makes them here
        self.update = dict(supported=True, reason="", state="available", current=__version__, latest="9.9.9",
                           size=46_210_000, progress=0, error="", checked=self.started, feed="(fake)",
                           notes="## 9.9.9\n- （假数据）开发用的更新说明：一行一个改动\n- 第二个改动\n"
                                 "- 第三个改动")
        # the game folder's addons waiting for the program's version while the game runs (daemon/service.py sync_addons)
        self.addons_update = dict(version=__version__, addons=[dict(name="WoWBridge", version="0.9.0")], waiting="game")
        self.entry("INFO", f"fake daemon {__version__} started (pid {os.getpid()})")
        self.entry("INFO", f"HELLO v={__version__} s=48879 k=17 m=1 p=1 (addon WoWBridge {__version__})")
        self.entry("INFO", "link: online, session 48879, heartbeat 15 s")
        self.entry("OUT", f"WoWBridge {__version__} loaded; type /wb for help")

    # ---- logs and events
    def entry(self, kind, text, addon=None, job=None):
        e = {"id": self.next_id, "t": time.time(), "kind": kind, "text": text, "addon": addon, "job": job}
        self.next_id += 1
        self.logs.append(e)
        self.broadcast("log", e)
        return e

    def broadcast(self, event, data):
        """to every SSE connection: a bounded queue each, the oldest item dropped when a slow client lets it fill up"""
        for q in list(self.subscribers):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait((event, data))

    def entries(self, since=0, limit=200, kinds=None):
        out = [e for e in self.logs if e["id"] > since and (not kinds or e["kind"] in kinds)]
        truncated = bool(self.logs) and since and since < self.logs[0]["id"] - 1
        return out[:limit], bool(truncated)

    def tail(self, limit=200, kinds=None):
        """the newest `limit` entries of these kinds, oldest first (the daemon's ?tail=1); truncated = the ring dropped some"""
        out = [e for e in self.logs if not kinds or e["kind"] in kinds][-limit:]
        return out, bool(self.logs) and self.logs[0]["id"] > 1

    # ---- status
    def status(self):
        """the daemon's shape (daemon/service.py status()), plus what only this fake knows: daemon.port / exe / fake and
        link.addon_version, which the page tolerates missing"""
        now = time.time()
        online = self.link_state == "online"
        found = self.game_found
        url = f"http://127.0.0.1:{self.port}/" if self.port else None
        return {
            "daemon": {"version": __version__, "pid": os.getpid(), "uptime": round(now - self.started, 1), "mode": self.mode,
                       "extension_approval": self.extension_approval,
                       "started": round(self.started, 3), "url": url, "port": self.port,
                       "exe": str(ROOT / ".venv" / "Scripts" / "wuxian.exe"), "fake": True},
            "game": {"found": found, "pid": 24816 if found else None, "build": "70235" if found else None,
                     "version": "12.0.0" if found else None, "client": {"w": 2560, "h": 1369} if found else None,
                     "start": self.game_start if found else None, "minimized": False if found else None,
                     "exe": GAME_DIR + r"\Wow.exe" if found else None},
            "link": {"state": self.link_state, "session": self.session if online else None,
                     "slot_next": 4096 - self.slots_left + 1, "slots_left": self.slots_left, "hb": 15,
                     "capture": self.settings["capture_in_use"], "ping_p50": round(self.ping, 3) if online else None,
                     "last_frame": self.last_frame if online else None, "addon_version": __version__ if online else None},
            "watch": list(self.watch),
            "reload_pending": self.reload_pending,
            "mcp_url": url + "mcp" if url else None,
            "addons_dir": GAME_DIR + r"\Interface\AddOns",
            "update": dict(self.update),
            "content": {"packs": {"api": {"version": api_version(), "source": "bundled"}}, "checked": self.started,
                        "error": "", "manifest": "(fake)"},
            "addons_update": self.addons_update,
        }

    def update_action(self, action):
        """the updater's states without Velopack: check finds 9.9.9, download runs in the ticker, apply only logs"""
        u = self.update
        if action == "check":
            u.update(state="available" if u["state"] != "ready" else "ready", checked=time.time())
        elif action == "download" and u["state"] == "available":
            u.update(state="downloading", progress=0)
        elif action == "apply" and u["state"] == "ready":
            self.entry("INFO", "update: (fake) the program would stop now and Velopack would start 9.9.9")
            return dict(u, restarting=True)
        return dict(u)

    def step_update(self):
        u = self.update
        if u["state"] == "downloading":
            u["progress"] = min(100, u["progress"] + 9)
            if u["progress"] >= 100:
                u["state"] = "ready"
                self.entry("INFO", "update: 9.9.9 downloaded; it is applied when the program restarts")

    def step(self):
        """one tick of pretend activity"""
        self.n += 1
        self.step_update()
        n, r = self.n, self.rng.random()
        if self.link_state == "online":
            self.last_frame = time.time()
            self.ping = min(0.9, max(0.12, self.ping + self.rng.uniform(-0.04, 0.04)))
            if n % 15 == 0:
                self.entry("OUT", OUT_LINES[0] % (n // 15))
            if n % 10 == 5:
                self.entry("INFO", f"ping {n // 10 + 1}: {self.ping:.2f} s round trip")
            if n % 60 == 30:
                self.slots_left -= 1
                self.entry("SLOTS", SLOTS_TEXT % (self.slots_left, self.slots_left * 15 / 3600))
            if r < 0.07:
                message, stack, addon = self.rng.choice(ERRORS)
                self.entry("ERR", f"{message}\n{stack}", addon=addon)
            elif r < 0.12:
                self.entry("WARN", self.rng.choice(WARN_LINES))
            elif r < 0.15:
                self.entry("BLOCKED", self.rng.choice(BLOCKED_LINES))
            elif r < 0.40:
                self.entry("OUT", self.rng.choice(OUT_LINES[1:]))
            elif r < 0.50:
                self.entry("INFO", self.rng.choice(INFO_LINES).format(n=1200 + n, slot=17 + n % 4000, job=self.last_job))
            elif r < 0.53 and self.watch:
                target = self.rng.choice(self.watch)
                self.last_job += 1
                self.entry("WATCH", f"{target} changed: load job {self.last_job}", job=self.last_job)
        if n % 120 == 80:                                    # the link drops for a few seconds now and then
            self.link_state = "offline"
            self.entry("INFO", "link: offline (no heartbeat for 6.0 s)")
        elif n % 120 == 88:
            self.link_state = "handshake"
            self.session += 1
            self.entry("INFO", f"HELLO v={__version__} s={self.session} k=18 m=1 p=1")
        elif n % 120 == 90:
            self.link_state = "online"
            self.entry("INFO", f"link: online, session {self.session}, heartbeat 15 s")
            self.local_restarted()
        if n % 5 == 0 or n % 120 in (80, 88, 90):
            self.broadcast("status", self.status())
        voice = self.local.get("voice")
        if voice and voice["state"] == "running" and n % 4 == 2:     # a running extension writes to its stderr now and then
            self.local_write("voice", self.rng.choice(VOICE_LINES))

    async def ticker(self):
        while True:
            await asyncio.sleep(self.tick)
            try:
                self.step()
            except Exception as e:                            # a bug in the pretend data must not stop the stream
                print(f"fake ticker: {e!r}", file=sys.stderr, flush=True)

    # ---- commands
    def run(self, code, timeout_ms):
        """(http status, body) for POST /api/run: canned answers that look like the addon's"""
        self.last_job += 1
        job = self.last_job
        if "sleep" in code or "hang" in code:
            self.entry("RUN", f"RUN {job}: no result within {timeout_ms} ms", job=job)
            return 504, {"error": {"code": "timeout", "message": f"no RUN result for job {job} within {timeout_ms} ms"}, "job": job}
        if "error(" in code or "nil" in code or ".." in code and "nil" in code:
            message = code[code.find("error(") + 6:].strip(")\"' ") if "error(" in code else "attempt to index a nil value"
            error = f'[string "=run"]:1: {message}'
            stack = f'[C]: in function `error\'\n[string "=run"]:1: in main chunk\nInterface/AddOns/WoWBridge/Agent.lua:77: in function `RunJob\''
            self.entry("RUN", f"RUN {job} error =run: {error}\n{stack}", job=job)
            return 200, {"ok": False, "job": job, "error": error, "stack": stack}
        values = self.canned(code)
        ms = self.rng.randint(3, 24)
        shown = ", ".join(v if isinstance(v, str) else json.dumps(v) for v in values)
        self.entry("RUN", f"RUN {job} ok =run ({len(code.encode())} B, {ms} ms)" + (f": {shown}" if shown else ""), job=job)
        return 200, {"ok": True, "job": job, "values": values, "ms": ms, "chunk": "=run"}

    def canned(self, code):
        """values as the addon writes them: tostring for a plain value, Agent.lua's Dump for a table"""
        if "C_Spell.GetSpellInfo" in code:
            return ['{\n  castTime = 1500,\n  iconID = 135812,\n  maxRange = 35,\n  minRange = 0,\n  name = "火球术",\n'
                    '  originalIconID = 135812,\n  spellID = 133,\n}']
        m = re.match(r"^return\s+Enum\.(\w+)\s*$", code.strip())
        if m:                                       # an enum read in the game: the manual's values, the last one off by one
            from wuxianworkshop import apidocs
            entry = apidocs.index().get(m.group(1)) or {}
            fields = [f for f in entry.get("fields") or [] if f.get("v") is not None]
            if not fields:
                return ["nil"]
            vals = {f["n"]: f["v"] for f in fields}
            vals[fields[-1]["n"]] = vals[fields[-1]["n"]] + 1
            return ["{\n" + "".join(f"  {k} = {v},\n" for k, v in sorted(vals.items(), key=lambda kv: kv[1])) + "}"]
        if "GetBuildInfo" in code:
            return ["12.0.0", "70235", "Sep 30 2026", 120000, "12.0.0", "", "12.0.0", 120000]
        if "UnitName" in code:
            return ["冒险者", "nil"]
        if "GetRealmName" in code:
            return ["无限工坊测试服"]
        m = ARITHMETIC.match(code.strip())
        if m:
            try:
                return [eval(m.group(1), {"__builtins__": {}}, {})]            # digits and operators only (ARITHMETIC)
            except Exception as e:
                return [f"<fake: {e}>"]
        if code.strip().startswith("return"):
            return [f"<fake value of {code.strip()[6:].strip()}>"]
        return []

    def snap(self, max_width):
        """a PNG that looks like a game corner, in the temporary snaps folder"""
        from PIL import Image, ImageDraw
        w = min(int(max_width or 1280), 2560)
        h = round(1369 * w / 2560)
        img = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(img)
        for y in range(h):                                   # a dusk sky
            d.line((0, y, w, y), fill=(20 + y * 40 // h, 24 + y * 30 // h, 48 + y * 60 // h))
        cell = max(2, w // 320)
        for i in range(64):                                  # the addon's frame: 64x16 coloured cells, top-left
            for j in range(16):
                colour = [(230, 60, 60), (60, 200, 90), (70, 130, 240), (250, 204, 21)][(i * 7 + j * 3) % 4]
                d.rectangle((i * cell, j * cell, (i + 1) * cell - 1, (j + 1) * cell - 1), fill=colour)
        d.text((w // 2 - 60, h // 2), f"FAKE SNAP {time.strftime('%H:%M:%S')}", fill=(240, 240, 240))
        name = f"snap-{time.strftime('%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.png"
        path = self.snaps / name
        img.save(path)
        self.entry("SNAP", f"{path} {w}x{h}")
        return {"path": str(path), "width": w, "height": h, "url": f"/api/snaps/{name}"}

    def doctor(self):
        """the shape of installer/doctor.py run_checks(): ok is True / False / None (skipped); fix names the action
        (install, install_clean, create_mail) that POST /api/install {clean:true} covers"""
        ok = self.fixed
        checks = [
            {"id": "game_dir", "title": "客户端目录", "ok": True, "detail": self.settings["game_dir"], "fix": None},
            {"id": "client_version", "title": "客户端版本", "ok": True, "detail": "12.0.0（分支 beta，wow_beta）：本版程序已验证",
             "fix": None},
            {"id": "addon:!WuxianWorkshop:installed", "title": "插件 !WuxianWorkshop 已安装", "ok": ok,
             "detail": f"{__version__}，Interface 16001" if ok else "未安装：游戏里的 Lua 报错不会被收集",
             "fix": None if ok else "install"},
            {"id": "addon:WoWBridge:installed", "title": "插件 WoWBridge 已安装", "ok": True,
             "detail": f"{__version__}，Interface 16001", "fix": None},
            {"id": "mailbox", "title": "字体信箱", "ok": True, "detail": "mail/：4096 个信箱槽位和 proc.ttf", "fix": None},
            {"id": "addons_txt", "title": "插件未被禁用（AddOns.txt）", "ok": None, "detail": "跳过：还没有角色登录过", "fix": None},
            {"id": "webview2", "title": "WebView2 运行时", "ok": True, "detail": "版本 154.0.4258.53", "fix": None},
            {"id": "leftovers", "title": "旧版本与实验残留", "ok": ok,
             "detail": "无" if ok else "Interface\\AddOns 里有 32 个 WoWBridge_S*** 实验文件夹",
             "fix": None if ok else "install_clean"},
            {"id": "runtime_dir", "title": "运行时目录可写", "ok": True, "detail": r"%LOCALAPPDATA%\WuxianWorkshop", "fix": None},
        ]
        return {"checks": checks, "ok": all(c["ok"] for c in checks), "available": True}

    ERRORS = [   # what installer/addons.py read_errors() makes of !WuxianWorkshop's SavedVariables
        {"signature": "Interface/AddOns/Bar/Frames.lua:#: attempt to call method 'SetPoint' (a nil value)", "kind": "ERR",
         "message": "Interface/AddOns/Bar/Frames.lua:201: attempt to call method 'SetPoint' (a nil value)", "addon": "Bar",
         "stack": "[string \"@Interface/AddOns/Bar/Frames.lua\"]:201: in function `Layout'\n"
                  "[string \"@Interface/AddOns/Bar/Bar.lua\"]:17: in main chunk", "count": 14},
        {"signature": "Interface/AddOns/Bar/Bar.lua:#: bad argument #1 to 'format'", "kind": "ERR",
         "message": "Interface/AddOns/Bar/Bar.lua:88: bad argument #1 to 'format' (string expected, got nil)", "addon": "Bar",
         "stack": "[string \"@Interface/AddOns/Bar/Bar.lua\"]:88: in function `Refresh'", "count": 2},
        {"signature": "Unknown unit token 'focus'", "kind": "WARN", "message": "Interface/AddOns/Foo/Core.lua:88: Unknown unit token 'focus'",
         "addon": "Foo", "stack": None, "count": 3},
        {"signature": "ADDON_ACTION_BLOCKED UNKNOWN CastSpellByName()", "kind": "BLOCKED", "message": "ADDON_ACTION_BLOCKED: CastSpellByName()",
         "addon": None, "stack": None, "count": 1},
    ]

    def addons(self):
        """the shape of installer/addons.py list_addons()"""
        def addon(name, title, version, interface, current, ours=None, enabled=True, disabled_in=0, lod=False, notes=None,
                  group=None):
            errs = [e for e in self.ERRORS if e["addon"] == name]
            return dict(name=name, title=title, version=version, interface=interface, current=current, notes=notes,
                        author=None, deps=[], optional_deps=[], load_on_demand=lod, saved_variables=[], wuxian_id=None,
                        toc=f"{name}.toc", ours=ours, enabled=enabled, disabled_in=disabled_in, group=group,
                        errors=dict(signatures=len(errs), count=sum(e["count"] for e in errs)), history=self.kept_row(name))
        now = time.time()
        return {"addons_dir": GAME_DIR + r"\Interface\AddOns", "client": {"version": "1.60.1.70235", "interface": 16001},
                "characters": 2,
                "addons": [addon("!WuxianWorkshop", "无限工坊", __version__, [16001], True, "platform",
                                 notes="最先加载，收集所有插件的 Lua 报错，供无限工坊 App 显示（/wxw report on|off|status）。"),
                           addon("Bar", "Bar 动作条", "2.3.1", [16001], True, notes="动作条增强"),
                           addon("Bar_Options", "Bar 设置", "2.3.1", [16001], True, lod=True, group="Bar"),
                           addon("Foo", "Foo", "1.0", [110105, 50500], False, enabled=None, disabled_in=1),
                           addon("OldThing", "Old Thing", None, [], None, enabled=False, disabled_in=2, lod=True),
                           addon("WoWBridge", "无限工坊 · 开发组件 (WoWBridge)", __version__, [16001], True, "developer",
                                 notes="无限工坊的开发组件：画帧码与 App 通信，执行 Agent 发来的 Lua（热加载）、回传报错与截图。/wb 打开设置。",
                                 group="!WuxianWorkshop")],
                "errors": {"available": True, "written": now - 3600, "report": True, "dropped": 0,
                           "signatures": len(self.ERRORS), "unattributed": {"signatures": 1, "count": 1}}}

    # ---- kept versions (agent/history.py's shapes)
    def kept_seed(self):
        now = time.time()

        def v(vid, ago, reason, note, count, added=(), changed=(), removed=()):
            return dict(id=vid, time=round(now - ago, 3), reason=reason, note=note, count=count, bytes=count * 4100,
                        added=list(added), changed=list(changed), removed=list(removed),
                        counts=dict(added=len(added), changed=len(changed), removed=len(removed)), skipped=[])
        bar = [v(1, 86400, "load", "", 4, added=["Bar.lua", "Bar.toc", "Bar.xml", "Frames.lua"]),
               v(2, 7200, "watch", "", 4, changed=["Bar.lua"]),
               v(3, 1800, "save", "Frames.lua", 5, added=["Locale.lua"], changed=["Frames.lua"]),
               v(4, 600, "save", "Bar.lua", 5, changed=["Bar.lua"])]
        foo = [v(1, 3000, "watch", "", 2, added=["Core.lua", "Foo.toc"])]
        return {"Bar": dict(versions=bar, now=dict(same=False, since=4, added=[], changed=["Frames.lua"], removed=[],
                                                    counts=dict(added=0, changed=1, removed=0))),
                "Foo": dict(versions=foo, now=dict(same=True, since=1, added=[], changed=[], removed=[],
                                                    counts=dict(added=0, changed=0, removed=0)))}

    def kept_row(self, name):
        k = self.kept.get(name)
        if not k or not k["versions"]:
            return None
        last = k["versions"][-1]
        return dict(versions=len(k["versions"]), latest=last["time"], latest_id=last["id"])

    EXT_CATALOG = [
        {"id": "wuxiankit", "folder": "WuxianKit", "version": "0.3.0",
         "title": {"zh": "无限工坊 · 扩展框架", "en": "Wuxian Workshop · WuxianKit"},
         "summary": {"zh": "插件的 Agent 接入框架。插件把功能声明为能力，供 AI Agent 经无限工坊调用；更改游戏的操作由玩家确认，可以撤销。",
                     "en": "Connects addons to an AI agent. Addons declare capabilities, which the agent uses through Wuxian Workshop."},
         "file": "WuxianKit-0.3.0.zip", "size": 82675, "sha256": "b" * 64, "min_app": "0.9.7", "protocol": 1,
         "extensions": [{"id": i, "title": {"zh": z, "en": e}, "tools": n, "events": v} for i, z, e, n, v in (
             ("tune", "调校", "Tune", 33, 0), ("chat", "聊天", "Chat", 4, 1), ("gate", "远程", "Remote", 3, 3),
             ("data", "数据", "Data", 4, 0), ("guide", "指引", "Guide", 5, 1), ("sense", "角色信息", "Character info", 8, 0))],
         "skills": [{"name": n, "extension": x, "title": {"zh": z, "en": e}, "description": {"zh": dz, "en": de}}
                    for n, x, z, e, dz, de in (
                        ("kit-extension", "kit", "写一个扩展", "Write an extension", "按扩展框架的架构，把插件接入扩展框架，或新写一个扩展",
                         "Make an addon a WuxianKit extension, or write a new extension"),
                        ("tune-setup", "tune", "调一套设置", "Tune a setup", "按玩家的要求调整一组设置、按键、宏、界面布局或天赋配置，合成一个可以整体撤销的提案",
                         "Change a set of settings in one proposal the player can undo as one"),
                        ("guide-tour", "guide", "分步讲解界面", "Guided UI tour", "用高亮和简短说明，一步步带玩家熟悉游戏界面的某个功能",
                         "Walk the player through a part of the UI step by step"))],
         "notes": {"zh": "0.3.0：新增远程、数据、指引三个扩展；界面用语改写，支持中英文切换。",
                   "en": "0.3.0: Remote, Data and Guide extensions; plainer wording with a Chinese/English switch."},
         "released": "2026-10-10", "homepage": "https://wuxianwow.com/workshop/extensions"},
        {"id": "wuxiankit-guides", "folder": "WuxianKit_Guides", "version": "0.1.0",
         "requires": ["WuxianKit"], "title": {"zh": "无限工坊 · 任务指引适配", "en": "Wuxian Workshop · Guide adapter"},
         "summary": {"zh": "把任务指引插件当前的一步交给 AI Agent。没有装指引插件时不做任何事。",
                     "en": "Hands the current step of a questing guide addon to an AI agent."},
         "file": "WuxianKit_Guides-0.1.0.zip", "size": 9620, "sha256": "d" * 64, "min_app": "0.9.7", "protocol": 1,
         "extensions": [{"id": "guides", "title": {"zh": "任务指引适配", "en": "Guide adapter"}, "tools": 2, "events": 1}],
         "skills": [], "notes": {"zh": "0.1.0：第一版。", "en": ""},
         "released": "2026-10-11", "homepage": "https://wuxianwow.com/workshop/extensions"},
        {"id": "wuxiankit-atlas", "folder": "WuxianKit_Atlas", "version": "1.1.0", "requires": ["WuxianKit", "AtlasData"],
         "title": {"zh": "无限工坊 · 地图标记", "en": "Wuxian Workshop · Map marks"},
         "summary": {"zh": "AI Agent 在世界地图上放标记，玩家在自己的客户端里确认。",
                     "en": "An AI agent puts marks on the world map; the player confirms them."},
         "file": "WuxianKit_Atlas-1.1.0.zip", "size": 12034, "sha256": "c" * 64, "min_app": "0.9.7",
         "extensions": [{"id": "atlas", "title": {"zh": "地图标记", "en": "Map marks"}, "tools": 3, "events": 1}],
         "skills": [], "notes": {"zh": "", "en": ""}, "homepage": "https://wuxianwow.com/workshop/extensions"},
    ]
    ADDONS_THERE = ("EllesmereUI",)              # addons of others in the pretend game's AddOns

    def ext_row(self, e):
        mine = self.ext_installed.get(e["id"])
        state = "available" if mine is None else "update" if mine != e["version"] else "current"
        there = set(self.ADDONS_THERE) | {x["folder"] for x in self.EXT_CATALOG if self.ext_installed.get(x["id"])}
        return dict(e, there=mine is not None, installed=mine, state=state,
                    missing=[f for f in e.get("requires", []) if f not in there])

    def detected(self):
        """what summary.detected finds in the game's AddOns: the addons that give WuxianKit an extension, that are not
        the catalog's and that no local extension put there. One of WuxianKit's own (official: the game lists it with
        builtin false), one the game runs (tips), one it does not run (its needed addon is not there)"""
        addons = GAME_DIR + "\\Interface\\AddOns"

        def one(folder, eid, title, version, notes, official=False, author=None, needs=(), skills=(), files=4):
            row = dict(folder=folder, title=title, notes=notes, version=version, author=author,
                       needs=[dict(addon=n, installed=n in self.ADDONS_THERE) for n in needs], path=addons + "\\" + folder)
            return dict({"from": "game"}, id=eid, title=title, version=version, notes=notes, author=author, homepage=None,
                        package=None, where="game", risk="game", game=[row], addons_dir=addons, local=None, executables=[],
                        agent=dict(docs="AGENT.md" if skills else None, skills=list(skills), apps=[], adds=[]),
                        files=files, size=2048 * files, official=official)
        return [one("WuxianKit_Route", "route", "无限工坊 · 路线", "0.2.0", "把当前路线的下一站交给 Agent。", official=True),
                one("WuxianLayout", "layout", "界面布局助手", "0.4.1", "让 Agent 读取和调整界面插件的布局；每次更改由玩家确认。",
                    author="Someone", needs=("EllesmereUI", "LayoutLib"), skills=("layout",), files=9),
                one("WuxianTips", "tips", "提示", "0.1.0", "在游戏里显示 Agent 给的提示。", author="Someone")]

    def extensions(self):
        return dict(catalog=dict(url="https://wuxianwow.com/workshop/data/extensions.json", updated="2026-10-10T08:00:00+00:00",
                                 checked=time.time(), error=""),
                    addons=[self.ext_row(e) for e in self.EXT_CATALOG], addons_dir=GAME_DIR + "\\Interface\\AddOns", app="0.9.7",
                    detected=self.detected() if self.addons_known else [])

    KIT_TOOLS = {   # the 扩展 page's Agent 接入: a few tools of each extension, as Service.agent_access lists them
        "kit": ("核心", [("history", "see", "Read", "执行记录"), ("undo", "do", "Change", "撤销")]),
        "tune": ("调校", [("cvar.get", "see", "Read", "客户端设置"), ("cvar.set", "do", "Change", "客户端设置"),
                         ("layout.import", "do", "Change", "导入布局"), ("profile.apply", "point", "Proposal", "应用配置档")]),
        "sense": ("角色信息", [("character", "see", "Read", "角色"), ("bags", "see", "Read", "背包")]),
        "data": ("数据", [("put", "keep", "Store", "保存数据"), ("get", "keep", "Store", "读取数据")]),
        "guide": ("指引", [("highlight", "point", "Guide", "高亮界面元素"), ("tour", "point", "Guide", "分步指引")]),
        "chat": ("聊天", [("send", "say", "Send", "发送消息")]),
    }

    def agent_access(self):
        now = time.time()
        exts = [dict(id=i, title=title, version="0.4.0", addon="WuxianKit", builtin=True, state="on",
                     tools=[dict(name=f"wk_{i}_{c.replace('.', '_')}", cap=f"{i}.{c}", kind=k, label=lab, title=t)
                            for c, k, lab, t in tools]) for i, (title, tools) in self.KIT_TOOLS.items()]
        exts.append(dict(id="gate", title="远程", version="0.4.0", addon="WuxianKit", builtin=True, state="off", tools=[]))
        exts.append(dict(id="guides", title="任务指引适配", version="0.1.0", addon="WuxianKit_Guides", builtin=False,
                         state="on", tools=[dict(name="wk_guides_step", cap="guides.step", kind="see", label="Read",
                                                 title="当前一步"),
                                            dict(name="wk_guides_skip", cap="guides.skip", kind="do", label="Change",
                                                 title="跳过这一步")]))
        exts.append(dict(id="route", title="路线", version="0.2.0", addon="WuxianKit_Route", builtin=False, state="on",
                         tools=[dict(name="wk_route_next", cap="route.next", kind="see", label="Read", title="下一站")]))
        exts.append(dict(id="herald", title="播报", version="0.1.4", addon="WuxianHeraldSkin", builtin=False, state="on",
                         tools=[dict(name="wk_herald_show", cap="herald.show", kind="point", label="Guide", title="显示播报")]))
        exts.append(dict(id="tips", title="提示", version="0.1.0", addon="WuxianTips", builtin=False, state="on",
                         tools=[dict(name="wk_tips_show", cap="tips.show", kind="point", label="Guide", title="显示提示"),
                                dict(name="wk_tips_list", cap="tips.list", kind="see", label="Read", title="提示列表")]))
        exts[0]["tools"] += [dict(name=n, cap=None, kind=None, label=lab, title=t) for n, lab, t in (
            ("wk_docs", "Read", "WuxianKit docs"), ("wk_wait", "Read", "Wait for a proposal"),
            ("wk_propose", "Proposal", "One proposal of several steps"))]
        kit = dict(version="0.4.0", protocol=1, revision="7a1c03e5", language="zhCN", source="game", newer=False,
                   tools=sum(len(e["tools"]) for e in exts), extensions=exts)
        row = lambda kind, pid, version, client, protocol, last, tools, revision, state, calls=0: dict(
            kind=kind, pid=pid, version=version, started=now - 3600, client=client, protocol=protocol, first=now - 3000,
            last=now - last, calls=calls, listed=now - 2900 if tools is not None else None, tools=tools, revision=revision,
            state=state)
        return dict(kit=kit, kit_state="running", kit_addon=dict(there=True, version="0.4.0"), program="0.9.7", now=now, servers=[
            row("stdio", 35852, "0.9.7", {"name": "claude-code", "version": "2.1.293"}, "2026-07-28", 12, kit["tools"],
                "7a1c03e5", "current", 41),
            row("stdio", 51324, "0.9.6", {"name": "claude-code", "version": "2.1.293"}, "2026-07-28", 900, None, None, "stale"),
            row("stdio", 61190, "0.9.7", {"name": "Trae CN", "version": "1.9"}, "2025-11-25", 95, 61, "2b9e4410", "behind", 3),
            row("http", 57332, "0.9.7", {"name": "Cursor", "version": "1.7"}, "2025-06-18", 300, kit["tools"], "7a1c03e5",
                "current")])

    def extension(self, action, ext_id):
        e = next((x for x in self.EXT_CATALOG if x["id"] == ext_id), None)
        if e is None:
            raise KeyError(f"{ext_id!r} is not in the extension catalog")
        if action == "install":
            need = self.ext_row(e)["missing"]
            if need:
                raise LookupError(f"{ext_id} needs {', '.join(need)} in the game first")
            was = self.ext_installed.get(ext_id)
            self.ext_installed[ext_id] = e["version"]
            result = dict(id=ext_id, folder=e["folder"], version=e["version"], was=was, kept=7 if was else None,
                          restart=was is None)
        else:
            removed = self.ext_installed.pop(ext_id, None) is not None
            result = dict(id=ext_id, folder=e["folder"], removed=removed, kept=8 if removed else None)
        self.entry("INFO", f"extension {ext_id}: {action}")
        return dict(action=action, result=result, addon=self.ext_row(e))

    # ---- 本机扩展 (local/host.py's shapes): voice runs (started with the workshop), notes waits for its first call and
    #      adds to the guild app, translate crashed, lab is off and has never described itself; facts waits for the
    #      player's 批准运行, stats was allowed with another command, ocr no longer answers, sync failed to start and
    #      waits for its next try, draft has a manifest that cannot be read; herald brings two game addons (one missing,
    #      one that differs), relay is linked (its folder is elsewhere) and needs a newer workshop and WuxianKit, greeter
    #      came from a package (ask). While the setting extension_approval is "auto" every row's approval is "ok" (a
    #      start allows it, by "developer") but greeter's, which the player allows once whatever the setting: "ask", the
    #      default, shows the rest
    def local_seed(self):
        now = self.started

        def cap(name, kind, title, doc, args=None, timeout=30):
            return dict(name=name, kind=kind, title=title, doc=doc, args=args or {}, timeout=timeout)

        def ext(eid, title, state, start="use", enabled=True, caps=(), sources=(), options=(), apps=(), skills=(),
                adds=None, pid=None, started=None, exit_code=None, error=None, broken=None, described=True,
                approval="ok", since=None, why=None, command=LOCAL_COMMAND, path=None, version=None, author=None,
                homepage=None, requires=None, unmet=(), addons=(), ask=False, where="auto", game=(), game_state="on"):
            path = path or EXT_HOME + "\\" + eid
            where = ("both" if addons else "local") if where == "auto" else where
            return dict(id=eid, title=title, version=version or ("0.1.0" if eid != "voice" else "0.3.2"), path=path,
                        author=author, homepage=homepage, requires=dict(requires or {}), unmet=[dict(u) for u in unmet],
                        addons=[dict(folder=f, version=v, state=s) for f, v, s in addons], ask=ask,
                        where=where, game=list(game), game_state=game_state,
                        enabled=enabled, start=start, state=state, pid=pid, started=started, exit_code=exit_code,
                        error=error, caps=list(caps), sources=list(sources), options=list(options), apps=list(apps),
                        skills=list(skills), broken=broken, described=described, approval=approval,
                        adds={app: dict(wake=a.get("wake") and dict(a["wake"]),
                                        skill=a.get("skill") and path + "\\" + a["skill"])
                              for app, a in (adds or {}).items()},
                        since=since if since is not None else started if started is not None else now - 3600, why=why,
                        command=None if command is None else [c.replace("{path}", path) for c in command],
                        notes=f"（假数据）本机扩展 {title}")
        return {e["id"]: e for e in (
            ext("voice", "声音", "running", start="workshop", pid=31412, started=now - 1500,
                caps=[cap("say", "point", "说一句", "Speaks text aloud; returns {seconds}.", {"text": "string",
                                                                                              "speed": "number?"}),
                      cap("play", "point", "播放台词池", "Plays a line from a pool.", {"pool": "string"}),
                      cap("status", "see", "状态", "What it is saying and how many lines wait.")],
                sources=[dict(name="said", title="说完一句", doc="Each line spoken: {text, seconds}.")],
                options=[dict(name="volume", kind="number", title="音量", default=80, min=0, max=100, step=5, unit="%",
                              value=65),
                         dict(name="voice", kind="choice", title="声音", default="serena", hint="换了下一句生效",
                              choices=[dict(value="serena", label="Serena"), dict(value="chelsie", label="Chelsie")],
                              value="serena"),
                         dict(name="combat", kind="boolean", title="战斗中也说", default=False, value=True),
                         dict(name="speed", kind="number", title="语速", default=1.0, min=0.5, max=2, step=0.1,
                              value=1.0)],
                apps=["companion"], skills=["companion"], author="Someone", homepage="https://example.org/voice"),
            ext("notes", "笔记", "stopped",
                caps=[cap("put", "keep", "记一条", "Keeps a note: {text, tags?}.", {"text": "string", "tags": "table?"}),
                      cap("find", "see", "查笔记", "Notes with these words.", {"words": "string?", "wake": "table?"})],
                adds=dict(guild=dict(wake=dict(call="notes.find", timeout=3.0), skill=r"skills\guild-chat.md")),
                options=[dict(name="folder", kind="text", title="保存位置", default="", hint="空着就放在扩展文件夹里",
                              value="")]),
            ext("translate", "翻译", "exited", started=now - 640, since=now - 639, exit_code=1,
                why="exited with code 1 after 0.4 s: ModuleNotFoundError: No module named 'httpx'",
                caps=[cap("text", "see", "翻译一段", "Translates text.", {"text": "string", "to": "zh | en"}, 60)],
                broken="ModuleNotFoundError: No module named 'httpx'"),
            ext("lab", "实验", "off", enabled=False, described=False),
            ext("facts", "图鉴速查", "stopped", start="workshop", described=False, approval="needed", since=now - 90,
                adds=dict(guild=dict(wake=dict(call="facts.lookup", timeout=3.0), skill=r"skills\guild-chat.md"),
                          companion=dict(skill=r"skills\companion.md")),
                command=[r"C:\Program Files\Python314\python.exe", "-m", "facts", "--index", r"{path}\data\index.db"]),
            ext("stats", "统计", "stopped", approval="changed", since=now - 300,
                caps=[cap("today", "see", "今日统计", "Today's numbers: {kills, gold, played}.")],
                command=[r"{path}\.venv\Scripts\python.exe", "main.py", "--upload", "https://stats.example/api"]),
            ext("ocr", "识图", "hung", pid=20480, started=now - 900, since=now - 45,
                why="ocr.read did not answer in 30 s, then no answer to ping in 5 s: the next call restarts it",
                caps=[cap("read", "see", "识别截图", "The text in a picture: {text}.", {"path": "string"})]),
            ext("sync", "同步", "backoff", start="workshop", started=now - 12, since=now - 11, exit_code=1,
                why="3 starts failed (exited with code 1 before it answered initialize): next try in 30 s",
                caps=[cap("push", "keep", "上传存档", "Uploads the kept notes.")],
                broken="ConnectionRefusedError: [WinError 10061] 127.0.0.1:7070"),
            ext("draft", "", "stopped", described=False, approval="needed", since=now - 20, command=None, where=None,
                error="run: a list of strings, the command that starts it (wuxian-extension.json)"),
            ext("herald", "播报", "stopped", version="0.2.0", author="Someone", homepage="https://example.org/herald",
                caps=[cap("announce", "point", "播报一条", "Shows a line in the game: {text}.", {"text": "string"})],
                addons=[("WuxianHerald", "0.2.0", "missing"), ("WuxianHeraldSkin", "0.1.4", "differs")],
                game=["herald"]),
            ext("relay", "转发", "stopped", path=r"D:\Projects\relay", version="0.4.0", author="Another",
                requires=dict(workshop="0.9.14", kit="0.7"),
                unmet=[dict(what="workshop", needs="0.9.14", has=__version__), dict(what="kit", needs="0.7", has="0.6.0")],
                error=f"requires: it needs 无限工坊 0.9.14 or newer (this is {__version__}): update 无限工坊"),
            ext("greeter", "迎新", "stopped", version="1.0.0", author="Another", described=False, approval="needed",
                ask=True, since=now - 30, addons=[("WuxianGreeter", "1.0.0", "missing")], game=["greeter"],
                game_state="restart"),
            # in the game only: no process, no command; WuxianKit lists tips (agent_access), marks is not in the game yet
            ext("tips", "提示", "stopped", described=False, command=None, where="game", author="Someone",
                addons=[("WuxianTips", "0.1.0", "current")], game=["tips"]),
            ext("marks", "标记", "stopped", described=False, command=None, where="game", since=now - 240,
                addons=[("WuxianMarks", "0.1.0", "missing")], game=["marks"], game_state="restart"),
        )}

    def local_log_seed(self, e):
        if e["started"] is None:
            return []
        at = time.strftime("%H:%M:%S", time.localtime(e["started"]))
        lines = [f"-- {at} started (pid {e['pid'] or 28870}): {self.local_argv(e)}"]
        if e["broken"]:
            return lines + ["Traceback (most recent call last):", '  File "main.py", line 3, in <module>',
                            "    import httpx", e["broken"], f"-- {at} exited with code {e['exit_code']} after 0.4 s"]
        lines += [f"loading model ({e['title']})", "model ready in 7.9 s",
                  f"-- {at} ready: {len(e['caps'])} capabilities, {len(e['sources'])} sources, "
                  f"{len(e['options'])} options"] + VOICE_LINES[:2]
        if e["state"] == "hung":
            lines += [f"-- {time.strftime('%H:%M:%S', time.localtime(e['since']))} hung: {e['why']}"]
        return lines

    def local_write(self, eid, text):
        """a line of its output; a full file starts again at line 1 (output.log -> output.1.log)"""
        out = self.local_out.setdefault(eid, [])
        if len(out) >= self.rotate:
            del out[:]
        out.append(text)

    def local_argv(self, e):
        return " ".join(f'"{c}"' if " " in c else c for c in e["command"] or LOCAL_COMMAND)

    def local_approval(self, e):
        """ok, needed or changed as registry.approval answers; with extension_approval "auto" every one is ok but the
        ones that were not written on this computer (ask: from a package, or found in the home), which the player
        allows once"""
        return "ok" if self.local_itself(e) else e["approval"]

    def local_itself(self, e):
        """whether the workshop allows it without the player (Host.by_itself): never one that asks; else with the
        setting "auto", and one in the game only (no process runs on this computer)"""
        return not e.get("ask") and (self.extension_approval == "auto" or e["where"] == "game")

    @staticmethod
    def local_readable(e):
        """whether its manifest can be used: it says what to run, or it is in the game only"""
        return e["command"] is not None or e["where"] == "game"

    def local_game(self, e):
        """the row's game (Host.game_of): None without game addons, else {extensions: the ids its addons give
        WuxianKit, state}; game_state is what WuxianKit says of them once they are in the game and it is online"""
        if not self.local_readable(e) or not e["addons"]:
            return None
        where = [a["state"] for a in self.local_addons(e)]
        if "unknown" in where:
            state = "unknown"
        elif any(s != "current" for s in where):
            state = "absent"
        elif self.link_state != "online":
            state = "offline"
        else:
            state = e["game_state"] if e["game"] else "installed"
        return dict(extensions=list(e["game"]), state=state)

    def local_player(self, e, game):
        """the row's player (Host.player_of): first the approval, then its game half; None: nothing is the player's"""
        if not self.local_readable(e) or not e["enabled"] or any(u["what"] == "workshop" for u in e["unmet"]):
            return None
        title = e["title"] or e["id"]
        if self.local_approval(e) != "ok":
            return player_says("approve", title, changed=e["approval"] == "changed")
        action = {"offline": "start_game", "restart": "restart_game", "disabled": "enable_addon",
                  "off": "turn_on"}.get((game or {}).get("state"))
        return player_says(action, title, addon=e["addons"][0]["folder"]) if action else None

    def local_restarted(self):
        """the game is back (the link's drop stands for a restart): it lists the addons that were new"""
        for e in self.local.values():
            if e["game_state"] == "restart" and e["addons"] and all(a["state"] == "current" for a in e["addons"]):
                e["game_state"] = "on"
        self.local_changed()

    @staticmethod
    def local_home(path):
        """whether a folder is right in the extensions' home (home.inside): the workshop manages it"""
        return path.lower().rsplit("\\", 1)[0] == EXT_HOME.lower()

    def local_addons(self, e):
        """the row's addons: [{folder, version, state}], every state "unknown" while the game's folder is not known"""
        return [dict(a, state=a["state"] if self.addons_known else "unknown") for a in e["addons"]]

    def local_row(self, e):
        game = self.local_game(e)
        in_game = e["where"] == "game" and e["state"] == "stopped" and e["error"] is None
        return dict(id=e["id"], title=e["title"] or e["id"], version=e["version"], path=e["path"], enabled=e["enabled"],
                    start=e["start"], state="game" if in_game else e["state"], pid=e["pid"], started=e["started"],
                    exit_code=e["exit_code"],
                    error=e["error"], capabilities=[f"{e['id']}.{c['name']}" for c in e["caps"]],
                    sources=[s["name"] for s in e["sources"]], options=[dict(o) for o in e["options"]],
                    apps=list(e["apps"]), skills=list(e["skills"]), adds={app: dict(a) for app, a in e["adds"].items()},
                    approval=self.local_approval(e), allowed=self.local_approval(e) == "ok", since=e["since"],
                    why="it is in the game only: WuxianKit runs it there, the workshop starts no process" if in_game
                    else e["why"],
                    command=None if e["command"] is None else list(e["command"]),
                    origin="home" if self.local_home(e["path"]) else "linked", author=e["author"],
                    homepage=e["homepage"], requires=dict(e["requires"]), unmet=[dict(u) for u in e["unmet"]],
                    addons=self.local_addons(e), where=e["where"], game=game, player=self.local_player(e, game))

    def local_listing(self):
        return dict(revision=self.local_revision, extensions=[self.local_row(e) for e in self.local.values()],
                    problem=None, home=EXT_HOME)

    def local_manifest(self):
        """the kit-shaped entries the agents' wk_ tools come from: the enabled ones (one in the game only is not
        among them: the game's WuxianKit lists it itself)"""
        return dict(revision=self.local_revision, extensions=[dict(
            id=e["id"], title=e["title"] or e["id"], version=e["version"], notes=e["notes"], state="on", runtime="local",
            path=e["path"], docs=e["path"] + "\\AGENT.md", skills=e["path"] + "\\skills" if e["skills"] else None,
            capabilities=[dict(id=f"{e['id']}.{c['name']}", kind=c["kind"], title=c["title"], doc=c["doc"],
                               args=dict(c["args"]), timeout=c["timeout"]) for c in e["caps"]],
            sources=[dict(s) for s in e["sources"]], options=[dict(o) for o in e["options"]],
            apps=[dict(app=a, title="陪玩", prompt="companion") for a in e["apps"]],
            adds={app: dict(a) for app, a in e["adds"].items()})
            for e in self.local.values() if e["enabled"] and e["where"] != "game"])

    def local_changed(self):
        """revision + 1, and the stream's `local` event with the rows"""
        self.local_revision += 1
        self.broadcast("local", self.local_listing())

    def local_entry(self, eid):
        e = self.local.get(eid) if isinstance(eid, str) else None
        if e is None:
            raise KeyError(f"no local extension {eid} is registered (local_extensions lists them)")
        return e

    def local_check(self, e):
        """{ok, problems, capabilities, tests?} as Host.check answers"""
        caps = [f"{e['id']}.{c['name']}" for c in e["caps"]]
        if e["broken"]:
            return dict(ok=False, capabilities=[], problems=[
                dict(level="error", where="initialize", message=f"{e['id']} exited before it answered initialize (code 1)",
                     hint="extension_logs shows its output: a traceback says what went wrong"),
                dict(level="error", where="output", message="the end of its output:\n" + e["broken"],
                     hint="a traceback says what went wrong")])
        problems = [] if e["id"] != "notes" else [dict(
            level="warning", where="capability notes.find", message="its doc does not say what it returns",
            hint="say what the agent gets back: {notes: [{text, tags, time}]}")]
        return dict(ok=True, problems=problems, capabilities=caps,
                    tests=dict(ran=4, failed=0, output="....\n----------------------------------------------------------"
                                                       "------------\nRan 4 tests in 0.012s\n\nOK\n"))

    def local_lint(self, e):
        """the Lua of the addons of one in the game only, as `check` checks an addon: [{folder, path, ok, errors,
        warnings}]; what its id says decides what is found"""
        out = []
        for a in e["addons"]:
            file = a["folder"] + ".lua"
            errors = [dict(file=file, line=12, code="syntax", message="unexpected symbol near '}'", hint="")] \
                if "syntax" in e["id"] else []
            warnings = [dict(file=file, line=4, code="undefined", message="C_TooltipInfo.GetOwner is nil in the running game",
                             hint="api_search finds what the client has")] if "warn" in e["id"] else []
            out.append(dict(folder=a["folder"], path=f"{e['path']}\\addons\\{a['folder']}", ok=not errors, errors=errors,
                            warnings=warnings))
        return out

    def local_load_game(self, e):
        """a load of one in the game only (Service.extension_load): its Lua checked before anything is installed, its
        addons put into the game's AddOns, then run in the running game: {row, check, addons, restart, hint, loaded}"""
        found = self.local_lint(e)
        first = next(((a, f) for a in found for f in a["errors"] if f["code"] in ("syntax", "unreadable")), None)
        if first:
            a, f = first
            raise Refused(422, "check_failed", f"extension_load {e['id']}: nothing installed, "
                                               f"{a['folder']}/{f['file']}:{f['line']}: {f['message']}", addons=found)
        self.local_requires(e)
        self.local_allowed(e)
        states = {a["state"] for a in e["addons"]}
        if not self.addons_known:
            game = dict(addons=self.local_addons(e), restart=False,
                        hint="the game's AddOns folder is not known, so its game addons are not installed: start the "
                             "game once, or set its folder on the 开始 page of the 无限工坊 App")
        elif states & {"foreign", "broken"}:
            game = dict(addons=self.local_addons(e), restart=False, hint=None)
        else:
            game = self.local_put(e)
        self.local_stop(e)
        loaded = []
        if self.link_state == "online":
            fails = "fails" in e["id"]
            loaded = [dict(folder=a["folder"], ok=not fails,
                           error=f"{a['folder']}.lua:3: attempt to call a nil value" if fails else None,
                           check=dict(ok=True, errors=[], warnings=[]))
                      for a in e["addons"] if a["state"] == "current"]
        if game["restart"] and loaded and all(x["ok"] for x in loaded):
            game["hint"] = "; ".join(x for x in (game["hint"], "its files ran in the running game just now (loaded): "
                                                 "list the tools again, its wk_ tools may be there; the game itself "
                                                 "takes a new addon or new files only when it starts") if x)
        self.entry("INFO", f"local extension {e['id']}: in the game only, " + (
            ", ".join(f"{x['folder']} {'ran' if x['ok'] else 'failed'}" for x in loaded) or "the game is not online"))
        self.local_changed()
        return dict(row=self.local_row(e), check=dict(ok=True, problems=[], capabilities=[]), loaded=loaded, **game)

    def local_option(self, e, name, value):
        """None, or why value is no value of that option (manifest.value_problem's checks, roughly)"""
        o = next((x for x in e["options"] if x["name"] == name), None)
        if o is None:
            return f"{e['id']} has no option {name!r}"
        if o["kind"] == "boolean" and not isinstance(value, bool):
            return f"option {name}: true or false"
        if o["kind"] == "text" and not isinstance(value, str):
            return f"option {name}: text"
        if o["kind"] == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"option {name}: a number"
            if value < o.get("min", value) or value > o.get("max", value):
                return f"option {name}: {o.get('min')}..{o.get('max')}"
        if o["kind"] == "choice" and value not in [c["value"] for c in o["choices"]]:
            return f"option {name}: one of " + ", ".join(str(c["value"]) for c in o["choices"])
        return None

    def local_to(self, e, state, why=None, **more):
        """it enters a state now (the row's state, since and why)"""
        e.update(state=state, since=time.time(), why=why, **more)

    def local_allowed(self, e):
        """before anything that starts its process (load, check): a manifest that cannot be read is a 400; with
        extension_approval "auto" its command is allowed here; with "ask" the answer is 403 needs_approval until 批准运行"""
        if not self.local_readable(e):
            raise ValueError(e["error"] or f"{e['id']}: its manifest cannot be read")
        if e["approval"] == "ok":
            return
        if not self.local_itself(e):
            what = "its command changed since the player allowed it" if e["approval"] == "changed" else \
                "the player has not allowed its command yet"
            raise Refused(403, "needs_approval", f"{e['id']}: {what}: confirm it on the Extensions (扩展) page of the "
                                                 f"无限工坊 App", player=self.local_player(e, self.local_game(e)))
        e["approval"] = "ok"

    def local_run(self, e, how):
        """a start (load): running; a broken one exits at once (translate), and waits for its next try when it starts
        with the workshop (sync)"""
        e["started"], e["exit_code"] = time.time(), None
        at = time.strftime("%H:%M:%S")
        out = self.local_out.setdefault(e["id"], [])
        if e["broken"]:
            why = f"exited with code 1 before it answered initialize: {e['broken']}"
            if e["start"] == "workshop":
                self.local_to(e, "backoff", why + " (next try in 5 s)", pid=None, exit_code=1)
            else:
                self.local_to(e, "exited", why, pid=None, exit_code=1)
            out += [f"-- {at} started (pid 40112): {self.local_argv(e)}", e["broken"],
                    f"-- {at} exited with code 1 after 0.4 s"]
        else:
            self.local_to(e, "running", pid=self.rng.randrange(20000, 60000), described=True)
            out += [f"-- {at} started (pid {e['pid']}): {self.local_argv(e)}",
                    f"-- {at} ready: {len(e['caps'])} capabilities, {len(e['sources'])} sources, "
                    f"{len(e['options'])} options"]
        self.entry("INFO", f"local extension {e['id']}: {e['state']} ({how})")

    def local_stop(self, e):
        """its process ended (one that did not answer is killed: code 1); one waiting for its next try no longer tries"""
        if e["state"] in LOCAL_LIVE:
            took = time.time() - (e["started"] or time.time())
            code = 1 if e["state"] == "hung" else 0
            self.local_to(e, "stopped", pid=None, exit_code=code)
            self.local_out.setdefault(e["id"], []).append(f"-- {time.strftime('%H:%M:%S')} stopped with code {code} "
                                                          f"after {took:.1f} s")
            self.entry("INFO", f"local extension {e['id']}: stopped")
        elif e["state"] == "backoff":
            self.local_to(e, "stopped")

    def local_requires(self, e):
        """before a start: one that needs a newer workshop does not start (409 requires_unmet); WuxianKit's version only
        shows in the row"""
        for u in e["unmet"]:
            if u["what"] == "workshop":
                raise Refused(409, "requires_unmet", f"{e['id']} needs 无限工坊 {u['needs']} or newer (this is "
                                                     f"{u['has']}): update 无限工坊")

    def local_keep(self):
        """a kept version's id (agent/history.py's), for a game addon replaced or removed"""
        return self.rng.randrange(20, 90)

    def local_fresh(self, eid, title, path, addons=(), where=None, keep=True, **more):
        """a new entry: no capabilities yet, not running, waiting for the player unless the setting allows it. where:
        game (no command; its addon is the WuxianKit extension <id>), local or both (its addon is <id>_game); None: both
        with addons, else local. The game lists what its addons bring after it starts again"""
        seed = self.local_seed()["lab"]
        where = where or ("both" if addons else "local")
        e = dict(seed, id=eid, title=title, path=path, enabled=True, state="stopped", since=time.time(),
                 approval="needed", addons=[dict(folder=f, version=v, state=s) for f, v, s in addons], where=where,
                 game=[eid if where == "game" else eid + "_game"] if addons else [], game_state="restart",
                 command=None if where == "game" else seed["command"], **more)
        if keep:
            self.local[eid] = e
            self.local_out[eid] = []
        return e

    def local_named(self, path):
        """(folder, name, package, eid, addons, more): what the name of a folder or a .zip says it is (the module's
        text); Refused for the ones that cannot be read"""
        if not isinstance(path, str) or not re.match(r"^[A-Za-z]:\\", path.strip()):
            raise ValueError("path: the full path of an extension's folder (or its wuxian-extension.json), or of a "
                             "package (.zip)")
        folder = path.strip().removesuffix("\\wuxian-extension.json").rstrip("\\")
        name = folder.rsplit("\\", 1)[-1]
        package = name.lower().endswith(".zip")
        stem = name[:-4] if package else name
        eid = re.sub(r"[^a-z0-9_]", "_", stem.lower()).lstrip("_0123456789")[:24] or "ext"
        if package and stem.lower().endswith("big"):
            raise Refused(413, "package_too_big", f"{name}: a package is at most 64 MB (and 256 MB unpacked)")
        if package and stem.lower().endswith("bad"):
            raise Refused(400, "bad_package", f"{name}: no wuxian-extension.json in it (at the top, or in its one top "
                                              f"folder)")
        if not package and stem.lower().endswith("bad"):
            raise Refused(400, "bad_manifest", "run: a list of strings, the command that starts it "
                                               "(wuxian-extension.json)")
        addons = [("Wuxian" + eid.title().replace("_", ""), "0.1.0", "foreign" if "foreign" in eid else "missing")] \
            if "game" in eid or "foreign" in eid else []
        more = dict(requires=dict(kit="0.7"), unmet=[dict(what="kit", needs="0.7", has="0.6.0")]) if "needs" in eid else {}
        if "ingame" in eid:
            more["where"] = "game"
        return folder, name, package, eid, addons, more

    def local_summary(self, e, source="folder", package=None):
        """what an extension says of itself (local/summary.py's shape), from a row as it is here"""
        if not self.local_readable(e):
            raise Refused(400, "bad_manifest", e["error"] or f"{e['id']}: its manifest cannot be read")
        addons = GAME_DIR + "\\Interface\\AddOns" if self.addons_known else None
        runs = e["where"] != "game" and e["command"] is not None
        game = [dict(folder=a["folder"], title=a["folder"], notes=None, version=a["version"], author=None,
                     needs=[dict(addon="EllesmereUI", installed=True if addons else None)] if "Skin" in a["folder"] else [],
                     path=addons + "\\" + a["folder"] if addons and a["state"] in ("current", "differs", "foreign") else None)
                for a in e["addons"]]
        local = None
        if runs:
            local = dict(run=list(e["command"]), python=None, start=e["start"],
                         env=["STATS_MODE"] if e["id"] == "stats" else [])
            local["pass"] = ["DEEPSEEK_API_KEY"] if e["id"] in ("voice", "translate") else []
        files = 6 + 2 * len(e["caps"]) + 3 * len(e["addons"])
        rel = lambda p: p[len(e["path"]) + 1:].replace("\\", "/") if p else None
        return dict({"from": source}, id=e["id"], title=e["title"] or None, version=e["version"], notes=e["notes"],
                    author=e["author"], homepage=e["homepage"], package=package or e.get("package"), where=e["where"],
                    risk="local" if runs else "game", game=game, addons_dir=addons, local=local,
                    executables=["bin/tts.exe"] if e["id"] == "voice" else [],
                    agent=dict(docs="AGENT.md", skills=list(e["skills"]), apps=list(e["apps"]),
                               adds=[dict(app=app, wake=(a.get("wake") or {}).get("call"), skill=rel(a.get("skill")))
                                     for app, a in sorted(e["adds"].items())]),
                    files=files, size=4096 + 1830 * files)

    def local_summary_of(self, eid=None, path=None):
        """GET / POST /api/local/summary: of a row (id, or its folder), or of a package that is not here (read by its
        name, as add reads it); nothing is added"""
        if eid is not None:
            return self.local_summary(self.local_entry(eid))
        if path is None:
            raise ValueError("id (an extension here) or path (an extension's folder, or a package's .zip)")
        folder, name, package, eid, addons, more = self.local_named(path)
        here = next((e for e in self.local.values() if e["path"].lower() == folder.lower()), None)
        if here is not None:
            return self.local_summary(here)
        if not package:
            raise KeyError(f"{folder}: no folder of an extension")
        e = self.local_fresh(eid, eid, EXT_HOME + "\\" + eid, addons, keep=False, version="0.1.0", author="Someone", **more)
        return self.local_summary(e, "package", dict(sha256="e" * 64, file=name))

    def local_add(self, path, replace=False):
        """POST /api/local/add: a folder (or its manifest) is linked, a .zip goes into the home; what its name says
        decides the answer (the module's text)"""
        folder, name, package, eid, addons, more = self.local_named(path)
        stem = name[:-4] if package else name
        if stem.lower().endswith("exists"):
            raise Refused(409, "extension_exists", f"the id {eid} is taken by D:\\Projects\\{eid}: extension_remove that "
                                                   f"one first")
        if not package:
            same = next((e for e in self.local.values() if e["path"].lower() == folder.lower()), None)
            if same is not None:
                return self.local_row(same)
            if eid in self.local:
                raise LookupError(f"the id {eid} is taken by {self.local[eid]['path']}: give this one another id, or "
                                  f"extension_remove that one first")
            e = self.local_fresh(eid, eid, folder, addons, **more)
            self.entry("INFO", f"local extension {eid}: registered ({folder})")
            self.local_changed()
            return self.local_row(e)
        old, replaced = self.local.get(eid), None
        if old is not None and not self.local_home(old["path"]):
            raise Refused(409, "extension_exists", f"the id {eid} is linked to {old['path']}: extension_remove that "
                                                   f"one first, then install the package")
        same = old is not None and "same" in eid
        version = "0.1.0" if old is None else old["version"] if same else "0.3.0" if old["version"] == "0.2.0" else "0.2.0"
        came = dict(sha256=(old.get("package") or {}).get("sha256") or "a" * 64 if same else f"{self.rng.getrandbits(256):064x}",
                    file=name)
        if old is not None and replace is not True:      # asked first: nothing of the one here is touched
            try:
                was = self.local_summary(old)
            except Refused:                      # the one there cannot be read: nothing to compare with
                was = None
            base = old if was is not None else self.local_fresh(eid, eid, old["path"], addons, keep=False, **more)
            new = self.local_summary(dict(base, version=version, author=old["author"] or "Someone"), "package", came)
            changes = None if was is None else [dict(field=f, old=was[f], new=new[f]) for f in ("version", "author")
                                                if was[f] != new[f]]
            raise Refused(409, "needs_replace",
                          f"{eid} is here already (version {old['version']}): the package {name} (version {version}) would "
                          f"take its place. Nothing was changed. Tell the player what differs, and add it again with "
                          f"replace true only when the player says so",
                          old=was, new=new, changes=changes, same=None if was is None else same)
        if old is not None:                      # an update: the old folder is kept, enabled and options stay
            self.local_stop(old)
            replaced = EXT_HOME + f"\\.removed\\{eid}-{time.strftime('%Y%m%d-%H%M%S')}"
        e = self.local_fresh(eid, old["title"] if old else eid, EXT_HOME + "\\" + eid, addons, ask=True,
                             version=version, author=(old or {}).get("author") or "Someone", package=came, **more)
        if old is not None:
            e.update(enabled=old["enabled"], options=old["options"], state="stopped" if old["enabled"] else "off")
        self.entry("INFO", f"local extension {eid}: installed from {folder}" + (" (the old one kept)" if old else ""))
        self.local_changed()
        return dict(self.local_row(e), package=dict(file=folder, replaced=replaced))

    def local_new(self, body):
        """POST /api/local/new: {row, files, guide} as Service.new_extension answers; nothing is written"""
        eid, title, folder, addon = body.get("id"), body.get("title"), body.get("folder"), body.get("addon") or None
        where = body.get("where")
        if not isinstance(eid, str) or not LOCAL_ID.match(eid):
            raise ValueError(f"id: {eid!r} is not an extension id: lowercase letters, digits and _, a letter first, at "
                             f"most 24 characters (voice, my_tool)")
        if title is not None and not isinstance(title, str):
            raise ValueError("title: text")
        if folder is not None and not (isinstance(folder, str) and re.match(r"^[A-Za-z]:\\", folder.strip())):
            raise ValueError("folder: the full path of the new extension's folder (new or empty)")
        if eid in self.local or eid.endswith("exists"):
            raise Refused(409, "extension_exists", f"the id {eid} is taken: choose another id, or extension_remove that "
                                                   f"one first")
        if addon is not None and (not isinstance(addon, str) or not ADDON_FOLDER.match(addon)):
            raise Refused(400, "bad_addon", f"addon: {addon!r} is no addon folder's name (letters, digits, _ . ! -)")
        if where is None:                        # (scaffold.extension_where)
            where = "both" if addon else "local"
        if where not in ("game", "local", "both"):
            raise ValueError(f"where: game (in the game only), local (on this computer) or both; {where!r} is not")
        if where == "local" and addon:
            raise ValueError("where local has no game addon: leave addon out, or say where both")
        if where != "local" and not addon:
            addon = addon_name_of(eid)
        if addon and (addon in ("WuxianKit", "WoWBridge") or addon.startswith("Blizzard_")):
            raise Refused(400, "bad_addon", f"addon: {addon} is taken: choose another name")
        path = folder.strip().rstrip("\\") if folder else EXT_HOME + "\\" + eid
        e = self.local_fresh(eid, (title or "").strip() or eid, path, [(addon, "0.1.0", "missing")] if addon else [],
                             where=where)
        self.entry("INFO", f"new local extension {eid} in {path}" + (" (in the game only)" if where == "game" else ""))
        self.local_changed()
        if where == "game":
            files = [path + "\\wuxian-extension.json"] + [f"{path}\\addons\\{addon}\\{f}"
                                                          for f in (addon + ".toc", addon + ".lua", "AGENT.md")]
            return dict(row=self.local_row(e), files=files,
                        guide=f"Read {path}\\addons\\{addon}\\AGENT.md and guide {{\"topic\": \"game-extension\"}}. "
                              f"Edit addons\\{addon}\\{addon}.lua, then extension_load {{\"id\": \"{eid}\"}} (it checks "
                              f"the files, installs the addon into the game's AddOns and runs it in the running game), "
                              f"call its wk_{eid}_ tools (list the tools again), read errors and logs when something "
                              f"fails. The game lists a new addon when it starts: extension_wait {{\"id\": \"{eid}\"}} "
                              f"waits for what the player has to do (the row's player)")
        files = [path + "\\" + f for f in ("wuxian-extension.json", "main.py", "AGENT.md", "tests\\test_main.py")]
        files += [f"{path}\\addons\\{addon}\\{addon}.toc", f"{path}\\addons\\{addon}\\{addon}.lua"] if addon else []
        return dict(row=self.local_row(e), files=files,
                    guide=f"Read {path}\\AGENT.md and guide {{\"topic\": \"local-extension\"}} (the whole chapter). Edit "
                          f"main.py, then extension_load {{\"id\": \"{eid}\"}} (it checks, restarts and runs its tests), "
                          f"call its wk_{eid}_ tools, read extension_logs when something fails"
                          + (f". Its game half is addons\\{addon}: extension_load installs it into the game's AddOns "
                             f"(the game finds a new addon when it starts)" if addon else ""))

    def local_pack(self, e, to=None):
        """POST /api/local/pack: pack.pack's answer; the file's name is <id>-<version or 0>.zip unless `to` names one"""
        if not self.local_readable(e):
            raise Refused(400, "bad_manifest", e["error"] or f"{e['id']}: its manifest cannot be read")
        if to is not None and not (isinstance(to, str) and re.match(r"^[A-Za-z]:\\", to.strip())):
            raise ValueError("to: the full path of a folder, or of the .zip to write")
        to = (to or PACKAGES).strip().rstrip("\\")
        file = to if to.lower().endswith(".zip") else f"{to}\\{e['id']}-{e['version'] or 0}.zip"
        files = 6 + 2 * len(e["caps"]) + 3 * len(e["addons"])
        self.entry("INFO", f"local extension {e['id']}: packed into {file}")
        sha = f"{self.rng.getrandbits(256):064x}"
        summary = self.local_summary(e, "package", dict(sha256=sha, file=file.rsplit("\\", 1)[-1]))
        warnings = [f"wuxian-extension.json: env sets {', '.join(summary['local']['env'])}, and the values go out with "
                    f"the package. Nothing secret belongs there (a key, a token, a path of this computer): name the "
                    f"variable in pass instead and leave its value to whoever runs it"] \
            if summary["local"] and summary["local"]["env"] else []
        return dict(id=e["id"], version=e["version"], file=file, size=4096 + 1830 * files, files=files,
                    sha256=sha, skipped=["__pycache__/", "logs/"], summary=summary, warnings=warnings)

    def local_install(self, e):
        """POST /api/local/install: gameside.install's answer for what this extension brings for the game. Every one is
        looked at before any is touched"""
        if self.local_approval(e) != "ok":
            raise Refused(403, "needs_approval", f"{e['id']}: the player has not allowed it yet: confirm it on the "
                                                 f"Extensions (扩展) page of the 无限工坊 App",
                          player=self.local_player(e, self.local_game(e)))
        if not e["addons"]:
            raise Refused(400, "no_addons", f"{e['id']} brings no game addon (addons in its wuxian-extension.json)")
        if not self.addons_known:
            raise Refused(503, "no_game_folder", "the game's folder is not known: set it on the 开始 page of the 无限工坊 "
                                                 "App")
        for a in e["addons"]:
            if a["state"] == "foreign":
                raise Refused(409, "addon_exists", f"the game has an addon {a['folder']} that {e['id']} did not install: "
                                                   f"it is left as it is")
            if a["state"] == "broken":
                raise Refused(400, "bad_addon", f"addons/{a['folder']} has no {a['folder']}.toc")
        return dict(id=e["id"], **self.local_put(e))

    def local_put(self, e):
        """its game addons go in: {addons, restart, hint} (a start answers these too when one was missing or differed)"""
        out = []
        for a in e["addons"]:
            was = a["state"]
            out.append(dict(folder=a["folder"], state="current" if was == "current" else "installed", was=was,
                            version=a["version"], kept=self.local_keep() if was == "differs" else None))
            a["state"] = "current"
        restart = any(a["was"] == "missing" for a in out)
        new = [a["folder"] for a in out if a["was"] == "missing"]
        self.entry("INFO", f"local extension {e['id']}: game addons " + ", ".join(f"{a['folder']} {a['state']}" for a in out))
        self.local_changed()
        return dict(addons=out, restart=restart,
                    hint=("new in the game's AddOns: " + ", ".join(new) + " (the game loads them after a restart)") if new
                    else "load <Name> hot-loads an addon the game has already")

    def local_action(self, action, body):
        """POST /api/local/<action>: what the daemon answers (KeyError: not_found, LookupError: 409, ValueError: 400,
        Refused: its own status and code)"""
        if action == "add":
            return self.local_add(body.get("path"), body.get("replace"))
        if action == "summary":
            return self.local_summary_of(body.get("id"), body.get("path"))
        if action == "new":
            return self.local_new(body)
        e = self.local_entry(body.get("id"))
        if action == "remove":
            home = self.local_home(e["path"])
            if home and e["id"].endswith("busy"):
                raise Refused(409, "folder_busy", f"{e['path']} could not be moved (a file in it is open?): close what "
                                                  f"uses it and remove {e['id']} again; it is still registered")
            self.local_stop(e)
            del self.local[e["id"]]
            self.local_out.pop(e["id"], None)
            kept = EXT_HOME + f"\\.removed\\{e['id']}-{time.strftime('%Y%m%d-%H%M%S')}" if home else None
            addons = [dict(folder=a["folder"], removed=a["state"] in ("current", "differs"),
                           kept=self.local_keep() if a["state"] in ("current", "differs") else None)
                      for a in e["addons"]] if self.addons_known else []
            self.entry("INFO", f"local extension {e['id']}: no longer registered ({e['path']})")
            self.local_changed()
            return dict(removed=True, id=e["id"], path=e["path"], kept=kept, addons=addons)
        if action == "pack":
            return self.local_pack(e, body.get("to"))
        if action == "install":
            return self.local_install(e)
        if action == "approve":                  # the player's 批准运行: the command as it is now may run
            if not self.local_readable(e):
                raise ValueError(e["error"] or f"{e['id']}: its manifest cannot be read")
            e["approval"] = "ok"
            self.entry("INFO", f"local extension {e['id']}: its command allowed by the player")
            if self.addons_known and not any(a["state"] in ("foreign", "broken") for a in e["addons"]):
                for a in e["addons"]:            # what it brings for the game goes in with the player's yes; one that
                    a["state"] = "current"       # cannot (the row's addons say why) does not fail the approval
            self.local_changed()
            return self.local_row(e)
        if action == "wait":                     # nobody does the player's part here: what is left, at once
            row = self.local_row(e)
            return dict(row=row, was=row["player"], timeout=row["player"] is not None)
        if action == "check":
            if e["where"] == "game":             # nothing of it runs here: its sources, without the player's yes
                found = self.local_lint(e)
                return dict(ok=all(a["ok"] for a in found), problems=[], capabilities=[], where="game", addons=found)
            self.local_allowed(e)
            return self.local_check(e)
        if action == "stop":
            self.local_stop(e)
            self.local_changed()
            if e["where"] == "game":
                return dict(self.local_row(e), stopped=False,
                            note="it is in the game only and has no process to stop: the player turns it off in the "
                                 "game (/wk), extension_set {\"enabled\": false} makes the workshop leave it alone, "
                                 "extension_remove takes its addons out")
            return self.local_row(e)
        if action == "load":
            if not e["enabled"]:
                raise LookupError(f"{e['id']} is off: extension_set {{\"id\": \"{e['id']}\", \"enabled\": true}} first")
            if e["where"] == "game":
                return self.local_load_game(e)
            self.local_allowed(e)
            self.local_requires(e)
            states = {a["state"] for a in e["addons"]} if self.addons_known else set()
            game = self.local_put(e) if states & {"missing", "differs"} and not states & {"foreign", "broken"} else {}
            self.local_stop(e)
            self.local_run(e, "extension_load")
            self.local_changed()
            return dict(row=self.local_row(e), check=self.local_check(e), **game)
        if action == "set":
            enabled, options = body.get("enabled"), body.get("options")
            if enabled not in (None, True, False):
                raise ValueError("enabled: true or false")
            if options is not None and not isinstance(options, dict):
                raise ValueError("options: an object, name -> value")
            for name, value in (options or {}).items():
                why = self.local_option(e, name, value)
                if why:
                    raise ValueError(why)
            for o in e["options"]:
                if o["name"] in (options or {}):
                    o["value"] = options[o["name"]]
            if enabled is False and e["enabled"]:
                self.local_stop(e)
                self.local_to(e, "off", enabled=False)
            elif enabled is True and not e["enabled"]:
                self.local_to(e, "stopped", enabled=True)
                if e["start"] == "workshop" and e["command"] is not None and self.local_approval(e) == "ok":
                    self.local_run(e, "set on")
            if options:
                self.entry("INFO", f"local extension {e['id']}: options " + ", ".join(f"{k}={v}" for k, v in options.items()))
            self.local_changed()
            return self.local_row(e)
        raise ValueError(f"no action {action}")

    def local_logs(self, eid, since=0, limit=200):
        """{lines, next, total, reset, cut, row} as Host.logs answers"""
        e = self.local_entry(eid)
        if since < 0 or not 1 <= limit <= 2000:
            raise ValueError("since: 0 or a line number; limit: 1..2000")
        lines = self.local_out.get(e["id"], [])
        total, reset = len(lines), False
        if since == 0:
            first = max(1, total - limit + 1)
        elif since > total + 1:
            first, reset = 1, True
        else:
            first = since
        last = min(total, first + limit - 1)
        texts = lines[first - 1:last] if last >= first else []
        return dict(lines=[dict(n=first + i, text=t) for i, t in enumerate(texts)], next=first + len(texts), total=total,
                    reset=reset, cut=since == 0 and first > 1, row=self.local_row(e))

    START = "开始无限工坊的应用「{title}」：先调用工具 wk_docs，参数 {{\"skill\": \"{skill}\"}}，照读到的步骤做。"

    def kit_apps(self):
        """/api/kit/apps (Service.kit_apps): the game's WuxianKit answers while the link is up, else the kept manifest
        (on and blocked null)"""
        online = self.link_state == "online"

        def app(aid, title, by, addon, prompt=None, on=None, blocked=None, temp=False, extension=None, needs=(), mcp=()):
            skill = None if prompt is None else dict(name=prompt, title=title, description=f"（假数据）{title}的技能",
                                                     needs=list(needs), mcp=[dict(m) for m in mcp])
            return dict(id=aid, title=title, by=by, addon=addon, temp=temp, prompt=prompt,
                        start=self.START.format(title=title, skill=prompt) if prompt else None,
                        on=on if online else None, blocked=blocked if online else None, extension=extension,
                        skill=skill)
        return dict(online=online, apps=[
            app("guild", "公会聊天", "WuxianKit", "WuxianKit", "guild-chat", on=True, needs=["shell"],
                mcp=[dict(name="wuxianwow", url="https://wuxianwow.com/mcp")]),
            app("companion", "陪玩", "声音（本机扩展）", None, "companion", on=False, needs=["shell"]),
            app("tour", "新手指引", "WuxianKit", "WuxianKit", "tour", on=False, blocked="「指引」扩展关着",
                extension=dict(id="guide", title="指引", on=False if online else None)),
            app("route", "练级路线", "WuxianKit", "WuxianKit", on=False),
            app("reactions", "反射", "无限工坊", None, on=True, temp=True),
        ])

    def history(self, addon=None, vid=None, against="now"):
        if not addon:
            return {"addons": [dict(addon=n, bytes=81_920, **self.kept_row(n)) for n in sorted(self.kept) if self.kept_row(n)]}
        k = self.kept.get(addon)
        if vid is None:
            return dict(addon=addon, versions=list(reversed(k["versions"])) if k else [], now=k["now"] if k else None)
        if not k or not any(x["id"] == int(vid) for x in k["versions"]):
            raise KeyError(f"{addon}: no kept version #{vid}")
        to = "now" if against == "now" else int(vid)
        base = int(vid) if against == "now" else int(vid) - 1
        diff = (f"--- #{base}/Frames.lua\n+++ {to if to == 'now' else '#' + str(to)}/Frames.lua\n@@ -198,7 +198,8 @@\n"
                " local function Layout(self)\n   local w = self:GetWidth()\n-  self.bar:SetPoint(\"LEFT\", 4, 0)\n"
                "+  self.bar:ClearAllPoints()\n+  self.bar:SetPoint(\"LEFT\", self, \"LEFT\", 4, 0)\n"
                "   self.bar:SetWidth(w - 8)\n end\n ")
        return dict(addon=addon, base=base or None, to=to, truncated=False, files=[
            dict(path="Frames.lua", status="changed", binary=False, plus=2, minus=1, diff=diff),
            dict(path="Locale.lua", status="added", binary=False, plus=2, minus=0,
                 diff=f"--- /dev/null\n+++ now/Locale.lua\n@@ -0,0 +1,2 @@\n+local L = {{}}\n+L.TITLE = \"Bar 动作条\""),
            dict(path="media/bar.tga", status="changed", binary=True, old_size=4140, new_size=4268)])

    def history_action(self, action, addon, vid=None, note=""):
        k = self.kept.setdefault(addon, dict(versions=[], now=None))
        vs = k["versions"]
        if action == "forget":
            self.kept.pop(addon, None)
            self.entry("INFO", f"history: the kept versions of {addon} removed")
            return dict(addon=addon, forgotten=True)
        now = k["now"] or dict(same=False, changed=["Core.lua"], added=[], removed=[], counts=dict(added=0, changed=1, removed=0))
        saved = None
        if not vs or not now.get("same"):
            saved = dict(id=(vs[-1]["id"] + 1) if vs else 1, time=round(time.time(), 3),
                         reason="manual" if action == "checkpoint" else "restore",
                         note=note if action == "checkpoint" else f"#{vid}", count=5, bytes=20_500,
                         added=now["added"], changed=now["changed"], removed=now["removed"], counts=now["counts"], skipped=[])
            vs.append(saved)
        latest = vs[-1]
        if action == "checkpoint":
            k["now"] = dict(same=True, since=latest["id"], added=[], changed=[], removed=[], counts=dict(added=0, changed=0, removed=0))
            self.entry("INFO", f"history: {addon} #{latest['id']} kept (manual)")
            return dict(latest, new=saved is not None)
        if not any(x["id"] == int(vid) for x in vs):
            raise KeyError(f"{addon}: no kept version #{vid}")
        k["now"] = dict(same=False, since=latest["id"], added=[], changed=["Frames.lua"], removed=["Locale.lua"],
                        counts=dict(added=0, changed=1, removed=1))
        self.entry("INFO", f"history: {addon} restored to #{vid}: 1 files written, 1 removed")
        return dict(addon=addon, restored=int(vid), saved=saved["id"] if saved else latest["id"], saved_new=saved is not None,
                    written=["Frames.lua"], removed=["Locale.lua"], skipped=[], watched=False,
                    hint=f"the files are as #{vid} had them; `load {addon}` runs them in the game now")

    def errors(self, addon=None, limit=100):
        now = int(time.time())
        entries = [dict(e, first=now - 86400 - 600 * i, last=now - 3600 - 60 * i, build="1.60.1.70235")
                   for i, e in enumerate(self.ERRORS) if addon is None or e["addon"] == addon]
        return {"available": True, "written": now - 3600, "report": True, "dropped": 0, "entries": entries[:limit],
                "total": len(entries)}

    def install(self, clean=True):
        self.addons_update = None
        removed = [] if self.fixed or not clean else [f"WoWBridge_S{i:03d}" for i in range(1, 33)]
        self.fixed = True
        self.entry("INFO", "install: 2 installed, %d removed, the game has to be restarted" % len(removed))
        return {"installed": ["!WuxianWorkshop", "WoWBridge"], "removed": removed, "restart_required": True,
                "addons_dir": GAME_DIR + r"\Interface\AddOns", "available": True}

    def update_settings(self, changes):
        """POST /api/settings as the daemon takes it: mode, capture, game_dir, autostart, language, font_scale,
        extension_approval (unknown keys ignored)"""
        if "capture" in changes and changes["capture"] not in ("gdi", "wgc"):
            raise ValueError('capture: "gdi" or "wgc"')
        if "extension_approval" in changes and changes["extension_approval"] not in ("ask", "auto"):
            raise ValueError('extension_approval: "ask" or "auto"')
        if "mode" in changes and changes["mode"] not in ("player", "developer"):
            raise ValueError('mode: "player" or "developer"')
        if "developer" in changes and "mode" not in changes:               # the page's boolean, for convenience
            changes = dict(changes, mode="developer" if changes["developer"] else "player")
        for key in ("mode", "capture", "game_dir", "language"):
            if key in changes:
                self.settings[key] = changes[key] or (None if key == "game_dir" else self.settings[key])
        if "language" in changes:                       # the daemon's words follow, as in daemon/service.py
            from wuxianworkshop import i18n
            i18n.set_language(self.settings["language"])
        if "font_scale" in changes:
            scale = changes["font_scale"]
            scale = int(scale) if isinstance(scale, str) and scale.isdigit() else scale
            if isinstance(scale, bool) or scale not in (90, 100, 110, 125, 150):
                raise ValueError("font_scale: 90, 100, 110, 125 or 150")
            self.settings["font_scale"] = scale
        if "autostart" in changes:
            self.settings["autostart"] = bool(changes["autostart"])
        if "onboarded" in changes:
            self.settings["onboarded"] = bool(changes["onboarded"])
        if "extension_approval" in changes:
            self.settings["extension_approval"] = changes["extension_approval"]
        self.mode = self.settings["mode"]
        was, self.extension_approval = self.extension_approval, self.settings["extension_approval"]
        self.entry("INFO", "settings: " + ", ".join(f"{k}={v}" for k, v in changes.items()))
        self.broadcast("status", self.status())
        if self.extension_approval != was:              # the rows' approval follows the setting
            self.local_changed()
        return dict(self.settings)

    PROGRAM = dict(command=r"C:\Users\Someone\AppData\Local\WuxianWorkshop.App\current\wuxian.exe", args=["mcp"], env={})

    def agent(self, host):
        """one agent as agents.py's Host.status() says it"""
        from wuxianworkshop import agents, i18n
        title = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor", "trae-cn": "Trae CN", "trae": "Trae 国际版",
                 "workbuddy": "WorkBuddy", "workbuddy-ai": "WorkBuddy 国际版"}[host]
        where = {"claude": r"C:\Users\Someone\.claude.json", "codex": r"C:\Users\Someone\.codex\config.toml",
                 "cursor": r"C:\Users\Someone\.cursor\mcp.json",
                 "trae-cn": r"C:\Users\Someone\AppData\Roaming\Trae CN\User\mcp.json",
                 "trae": r"C:\Users\Someone\AppData\Roaming\Trae\User\mcp.json",
                 "workbuddy": r"C:\Users\Someone\.workbuddy\mcp.json",
                 "workbuddy-ai": r"C:\Users\Someone\.workbuddy-ai\mcp.json"}[host]
        state = self.agents[host]
        cmd = " ".join([self.PROGRAM["command"], *self.PROGRAM["args"]])
        detail = {"ok": "已接入：会启动 " + cmd, "absent": "还没有接入", "missing": f"没有找到 {title}",
                  "other": r"已接入，但启动的是另一个程序：D:\old\wuxian\wuxian.exe（点「接入」改成现在这个）"}[state]
        return dict(id=host, title=title, present=state != "missing", can_connect=state != "missing",
                    can_verify=host in ("claude", "codex"), state=state, detail=detail, where=where,
                    apply=i18n.tr(*agents.CLASSES[host].apply),
                    entry={"command": self.PROGRAM["command"], "args": ["mcp"]} if state == "ok" else None)

    def agents_status(self):
        from wuxianworkshop import agents
        return dict(program=dict(command=self.PROGRAM["command"], args=self.PROGRAM["args"]),
                    hosts=[self.agent(h) for h in agents.HOSTS], manual=agents.manual(self.PROGRAM))

    def agent_action(self, action, host):
        if host not in self.agents or self.agents[host] == "missing":
            raise ValueError(f"没有找到 {host}")
        if action == "connect":
            self.agents[host] = "ok"
        elif action == "disconnect":
            self.agents[host] = "absent"
        elif action != "verify":
            raise ValueError('action: "connect", "disconnect" or "verify"')
        answer = self.agent(host)
        if action in ("connect", "verify") and host in ("claude", "codex"):
            answer["verify"] = {"ok": True, "text": "Claude Code 启动它：✔ Connected" if host == "claude" else "Codex 读到了 wuxian 的设置"}
        self.entry("INFO", f"agents: {action} {host} -> {answer['state']} (fake: nothing written)")
        return answer

    def request_reload(self, loop):
        self.reload_pending = True
        nonce = self.rng.randrange(10 ** 8)
        self.entry("RELOAD", f"RELOAD asked (nonce {nonce}): the addon shows the button")

        def done():
            self.reload_pending = False
            self.session += 1
            self.entry("RELOAD", f"RELOAD done: new session {self.session}")
            self.broadcast("status", self.status())
        loop.call_later(4.0, done)                              # as if the user clicked the button in the game
        return {"requested": True, "nonce": nonce}

    def do_watch(self, action, target):
        if action == "start" and target and target not in self.watch:
            self.watch.append(target)
            self.entry("WATCH", f"watching {target}")
        elif action == "stop" and target in self.watch:
            self.watch.remove(target)
            self.entry("WATCH", f"stopped watching {target}")
        elif action == "stop" and not target:
            self.watch.clear()
        self.broadcast("status", self.status())
        return {"watched": list(self.watch)}

    def close(self):
        shutil.rmtree(self.snaps, ignore_errors=True)


# ---- the ASGI app

class UiFiles(StaticFiles):
    """the page's files, revalidated on every load (their ETag / Last-Modified make that cheap): after an update of the
    program the window must not go on with the previous version's script or styles from the WebView2 cache"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def error(code, message, status, **extra):
    return JSONResponse({"error": {"code": code, "message": message}, **extra}, status_code=status)


def sse(event, data):
    head = f"event: {event}\n" + (f"id: {data['id']}\n" if event == "log" else "")
    return head + "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


class Guard:
    """the daemon's checks: Host must be the loopback name, /api/* needs the token (Authorization: Bearer, or ?token= for
    EventSource and <img>), /api/session is for same-origin pages only"""

    def __init__(self, app, fake):
        self.app = app
        self.fake = fake

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            host = headers.get("host", "")
            hostname, _, port = host.rpartition(":") if host.count(":") == 1 else (host, "", "")
            if hostname not in ("127.0.0.1", "localhost") or (port and self.fake.port and port != str(self.fake.port)):
                return await error("bad_host", f"Host {host!r} is not this daemon", 403)(scope, receive, send)
            path = scope["path"]
            if path == "/api/session":
                origin = headers.get("origin")
                site = headers.get("sec-fetch-site")
                if (origin and origin != f"http://{host}") or (site and site not in ("same-origin", "none")):
                    return await error("forbidden", "the session token is only for the daemon's own page", 403)(scope, receive, send)
            elif path.startswith("/api/"):
                auth = headers.get("authorization", "")
                token = auth[7:] if auth.startswith("Bearer ") else None
                if token is None:
                    query = dict(p.split("=", 1) for p in scope.get("query_string", b"").decode().split("&") if "=" in p)
                    token = query.get("token")
                if not token or not secrets.compare_digest(token, self.fake.token):
                    return await error("unauthorized", "missing or wrong token", 401)(scope, receive, send)
        await self.app(scope, receive, send)


async def json_body(request):
    body = await request.body()
    if not body:
        return {}
    try:
        data = json.loads(body)
    except ValueError as e:
        raise ValueError(f"bad JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("the body must be a JSON object")
    return data


def create_app(fake=None, ticker=True):
    """the Starlette app around a Fake (a new one by default); ticker=False keeps the logs still (tests)"""
    fake = fake or Fake()

    async def session(request):
        return JSONResponse({"token": fake.token, "port": fake.port, "version": __version__, "mode": fake.mode})

    async def status(request):
        return JSONResponse(fake.status())

    async def logs(request):
        q = request.query_params
        kinds = [k for k in q.get("kinds", "").split(",") if k]
        try:
            since, limit = int(q.get("since", 0) or 0), int(q.get("limit", 200) or 200)
        except ValueError:
            return error("bad_request", "since and limit must be integers", 400)
        if q.get("tail") not in (None, "", "0"):     # the newest ones, next = the id after the last (the daemon's shape)
            entries, truncated = fake.tail(max(1, min(limit, 1000)), kinds)
            return JSONResponse({"entries": entries, "next": entries[-1]["id"] + 1 if entries else fake.next_id,
                                 "truncated": truncated})
        entries, truncated = fake.entries(since, max(1, min(limit, 5000)), kinds)
        out = {"entries": entries, "next": entries[-1]["id"] if entries else since}
        if truncated:
            out["truncated"] = True
        return JSONResponse(out)

    async def events(request):
        try:
            since = int(request.query_params.get("since", 0) or 0)
        except ValueError:
            since = 0
        queue = asyncio.Queue(maxsize=512)

        async def stream():
            fake.subscribers.add(queue)
            sent = since
            try:
                yield "retry: 2000\n\n"
                for e in list(fake.logs):
                    if e["id"] > sent:
                        sent = e["id"]
                        yield sse("log", e)
                yield sse("status", fake.status())
                while True:
                    try:
                        event, data = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if event == "log":
                        if data["id"] <= sent:                 # already in the replay above
                            continue
                        sent = data["id"]
                    yield sse(event, data)
            finally:
                fake.subscribers.discard(queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def run(request):
        try:
            body = await json_body(request)
        except ValueError as e:
            return error("bad_request", str(e), 400)
        code = str(body.get("code", "")).strip()
        if not code:
            return error("bad_request", "code is empty", 400)
        timeout_ms = int(body.get("timeout_ms") or 10000)
        await asyncio.sleep(min(0.25, timeout_ms / 1000))
        status_code, out = fake.run(code, timeout_ms)
        return JSONResponse(out, status_code=status_code)

    async def load(request):
        try:
            body = await json_body(request)
        except ValueError as e:
            return error("bad_request", str(e), 400)
        target = str(body.get("target", ""))
        if target.lower().startswith("wowbridge"):
            return error("forbidden", "WoWBridge cannot reload itself", 403)
        fake.last_job += 1
        fake.entry("RUN", f"RUN {fake.last_job} ok {target}/Core.lua (1203 B, 11 ms)", job=fake.last_job)
        return JSONResponse({"files": [{"file": f"{target}/Core.lua", "ok": True, "error": None, "ms": 11}], "addon": target})

    async def watch(request):
        try:
            body = await json_body(request)
        except ValueError as e:
            return error("bad_request", str(e), 400)
        return JSONResponse(fake.do_watch(body.get("action", "list"), str(body.get("target", "") or "")))

    async def snap(request):
        try:
            body = await json_body(request)
        except ValueError as e:
            return error("bad_request", str(e), 400)
        return JSONResponse(fake.snap(body.get("max_width") or 1280))

    async def snap_file(request):
        name = request.path_params["name"]
        path = fake.snaps / name
        if "/" in name or "\\" in name or not path.is_file():
            return error("not_found", f"no snap {name}", 404)
        return FileResponse(path, media_type="image/png")

    async def reload(request):
        return JSONResponse(fake.request_reload(asyncio.get_running_loop()))

    async def say(request):
        try:
            body = await json_body(request)
        except ValueError as e:
            return error("bad_request", str(e), 400)
        fake.entry("INFO", f"say: {body.get('text', '')}")
        return JSONResponse({"queued": True})

    async def doctor(request):
        return JSONResponse(fake.doctor())

    async def addons(request):
        return JSONResponse(fake.addons())

    async def errors(request):
        try:
            limit = int(request.query_params.get("limit") or 100)
        except ValueError:
            return error("bad_request", "limit: an integer", 400)
        return JSONResponse(fake.errors(request.query_params.get("addon") or None, limit))

    async def install(request):
        try:
            body = await json_body(request)
        except ValueError as e:
            return error("bad_request", str(e), 400)
        return JSONResponse(fake.install(bool(body.get("clean", True))))

    async def settings(request):
        if request.method == "GET":
            return JSONResponse(dict(fake.settings))
        try:
            body = await json_body(request)
            return JSONResponse(fake.update_settings(body))
        except ValueError as e:
            return error("bad_request", str(e), 400)

    async def update(request):
        if request.method == "GET":
            return JSONResponse(dict(fake.update))
        body = await json_body(request)
        return JSONResponse(fake.update_action(body.get("action")))

    async def apidocs_(request):
        """the real API manual (the pack the program carries): the same answers as the daemon"""
        from wuxianworkshop import apidocs
        p, ix = request.query_params, apidocs.index()
        if "systems" in p:
            return JSONResponse(dict(systems=ix.systems()))
        if p.get("system"):
            found = ix.system(p["system"])
            return JSONResponse(found) if found else error("not_found", f"{p['system']}: no such namespace", 404)
        if p.get("name"):
            entry = ix.get(p["name"])
            return JSONResponse(entry) if entry else error("not_found", f"{p['name']}: not in the API manual", 404)
        if "manual" in p:
            topic = ix.manual(p["manual"] or None, p.get("lang"))
            if topic is None:
                return error("not_found", "no such topic", 404)
            return JSONResponse(topic if isinstance(topic, dict) else {"topics": topic})
        if p.get("q") or p.get("kind") or p.get("call"):
            return JSONResponse(dict(query=p.get("q") or "", **ix.find(p.get("q"), p.get("kind") or None, p.get("call") or None,
                                                                    int(p.get("limit") or 20), int(p.get("offset") or 0))))
        return JSONResponse(ix.about())

    async def trace(request):
        """POST /api/trace: the events asked for, fired twice within the time (fake: answered after a second)"""
        body = await json_body(request)
        events = body.get("events") or ["PLAYER_TARGET_CHANGED"]
        seconds = body.get("seconds", 10)
        await asyncio.sleep(1)
        fired = [dict(t=round(1.2 + 3.4 * i, 2), event=events[0], args=["player"] if i else [], n=i + 1) for i in range(2)]
        return JSONResponse(dict(seconds=seconds, kept=len(fired), dropped=0, pattern=events, events=fired,
                                 counts=[dict(event=events[0], count=len(fired))], registered=True, unknown=[]))

    async def new_addon(request):
        from wuxianworkshop import scaffold
        body = await json_body(request)
        try:
            res = scaffold.create(fake.new_addons, body.get("name"), body.get("title"), body.get("notes") or "",
                                  body.get("template") or "basic")
        except scaffold.ScaffoldError as e:
            return error(e.code, str(e), 400)
        fake.entry("INFO", f"new addon {res['name']} in {res['path']} (fake: a temporary folder)")
        return JSONResponse(dict(res, hint=f"`load {res['name']}` hot-loads it now; the game lists it after a full restart"))

    async def agents_(request):
        if request.method == "GET":
            return JSONResponse(fake.agents_status())
        body = await json_body(request)
        try:
            return JSONResponse(fake.agent_action(body.get("action"), body.get("host")))
        except ValueError as e:
            return error("agent_failed", str(e), 409)

    async def check(request):
        body = await json_body(request)
        name = str(body.get("target") or "")
        def f(file, line, code, message, hint=""):
            return dict(file=file, line=line, code=code, message=message, hint=hint)
        if name.lower().startswith("wowbridge"):
            return JSONResponse(dict(target=name, addon=name, files=14, ok=True, errors=[], warnings=[], notes=[],
                                     unresolved={}, libraries=0, live=dict(checked=1)))
        return JSONResponse(dict(target=name, addon=name, files=3, ok=False, libraries=1, unresolved={}, live=dict(checked=2),
            errors=[f("Frames.lua", 41, "syntax", "unexpected symbol near '/'", "Lua 5.1 has no integer division (//): use math.floor(a / b)"),
                    f("Bar.lua", 88, "undefined", "GetSpellInfo is nil in the running game", "a typo, or defined nowhere"),
                    f("Bar.lua", 64, "args-missing", "C_Map.GetPlayerMapPosition needs unitToken (argument 2): the call passes 1",
                      "C_Map.GetPlayerMapPosition(uiMapID, unitToken)")],
            warnings=[f("Bar.lua", 12, "global-write", "writes the global counter", "forgot local? Globals are shared by every addon (and taint the Blizzard UI when it reads them)"),
                      f("Bar.lua", 30, "typo", "UnitHelth is neither the client's nor this addon's", "did you mean UnitHealth?"),
                      f("Bar.lua", 52, "secret", "UnitHealth returns secret values: comparing it fails in addon code",
                        "check issecretvalue(x) first, or show it with string.format / SetFormattedText")],
            notes=[f("Bar.lua", 0, "secret-maybe", "3 results are secret only in restricted states (SecretWhen...): plain out of combat",
                     "test them with issecretvalue before arithmetic")]))

    async def agent_access(request):
        return JSONResponse(fake.agent_access())

    async def extensions_(request):
        try:
            if request.method == "GET":
                return JSONResponse(fake.extensions())
            body = await json_body(request)
            if body.get("action") not in ("install", "remove"):
                return error("bad_request", "action: install or remove", 400)
            return JSONResponse(fake.extension(body["action"], body.get("id")))
        except KeyError as e:
            return error("no_such_extension", str(e.args[0]), 404)
        except LookupError as e:
            return error("needs_addons", str(e), 409)
        except ValueError as e:
            return error("bad_request", str(e), 400)

    async def kit_apps(request):
        return JSONResponse(fake.kit_apps())

    async def local_(request):
        return JSONResponse(fake.local_listing())

    async def local_manifest(request):
        return JSONResponse(fake.local_manifest())

    async def local_logs(request):
        q = request.query_params
        try:
            return JSONResponse(fake.local_logs(q.get("id"), int(q.get("since") or 0), int(q.get("limit") or 200)))
        except KeyError as e:
            return error("not_found", str(e.args[0]), 404)
        except ValueError as e:
            return error("bad_request", str(e), 400)

    def local_post(action):
        async def handle(request):
            try:
                body = dict(request.query_params) if request.method == "GET" else await json_body(request)
                if action == "load":
                    await asyncio.sleep(0.6)               # a start takes a moment (initialize, the tests)
                return JSONResponse(fake.local_action(action, body))
            except Refused as e:
                return JSONResponse({"error": {"code": e.code, "message": e.message, **e.extra}, **e.extra},
                                    status_code=e.status)
            except KeyError as e:
                return error("not_found", str(e.args[0]), 404)
            except LookupError as e:
                return error("off" if action == "load" else "extension_exists", str(e), 409)
            except ValueError as e:
                return error("bad_option" if action == "set" else "bad_request", str(e), 400)
        return handle

    async def history_(request):
        try:
            if request.method == "GET":
                p = request.query_params
                return JSONResponse(fake.history(p.get("addon"), p.get("id"), p.get("against") or "now"))
            body = await json_body(request)
            if body.get("action") not in ("checkpoint", "restore", "forget"):
                return error("bad_request", 'action: "checkpoint", "restore" or "forget"', 400)
            return JSONResponse(fake.history_action(body["action"], body.get("addon"), body.get("id"), body.get("note") or ""))
        except KeyError as e:
            return error("not_found", str(e.args[0]), 404)
        except ValueError as e:
            return error("bad_request", str(e), 400)

    async def reveal(request):
        body = await json_body(request)
        fake.entry("INFO", f"reveal {body.get('name')} (fake: nothing opens)")
        return JSONResponse({"opened": False})

    async def show(request):
        fake.entry("INFO", "show: another `wuxian` asked for the window")
        return JSONResponse({"shown": True})

    async def quit_(request):
        fake.entry("INFO", "quit requested")
        return JSONResponse({"bye": True})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(fake.ticker()) if ticker else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            fake.close()

    routes = [
        Route("/api/session", session),
        Route("/api/status", status),
        Route("/api/logs", logs),
        Route("/api/events", events),
        Route("/api/run", run, methods=["POST"]),
        Route("/api/load", load, methods=["POST"]),
        Route("/api/watch", watch, methods=["POST"]),
        Route("/api/snap", snap, methods=["POST"]),
        Route("/api/snaps/{name}", snap_file),
        Route("/api/reload", reload, methods=["POST"]),
        Route("/api/say", say, methods=["POST"]),
        Route("/api/doctor", doctor),
        Route("/api/addons", addons),
        Route("/api/errors", errors),
        Route("/api/install", install, methods=["POST"]),
        Route("/api/settings", settings, methods=["GET", "POST"]),
        Route("/api/show", show, methods=["POST"]),
        Route("/api/quit", quit_, methods=["POST"]),
        Route("/api/update", update, methods=["GET", "POST"]),
        Route("/api/apidocs", apidocs_),
        Route("/api/trace", trace, methods=["POST"]),
        Route("/api/new_addon", new_addon, methods=["POST"]),
        Route("/api/reveal", reveal, methods=["POST"]),
        Route("/api/agents", agents_, methods=["GET", "POST"]),
        Route("/api/history", history_, methods=["GET", "POST"]),
        Route("/api/extensions", extensions_, methods=["GET", "POST"]),
        Route("/api/agent_access", agent_access, methods=["GET"]),
        Route("/api/check", check, methods=["POST"]),
        Route("/api/kit/apps", kit_apps, methods=["GET"]),
        Route("/api/local", local_, methods=["GET"]),
        Route("/api/local/manifest", local_manifest, methods=["GET"]),
        Route("/api/local/logs", local_logs, methods=["GET"]),
        *[Route(f"/api/local/{action}", local_post(action), methods=["POST"])
          for action in ("set", "load", "stop", "check", "approve", "add", "remove", "pack", "install", "new", "wait")],
        Route("/api/local/summary", local_post("summary"), methods=["GET", "POST"]),
        Mount("/", UiFiles(directory=STATIC_DIR, html=True)),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.fake = fake
    return Guard(app, fake)


# ---- running it

class Handle:
    """what the shell expects of daemon.server.start(): port, token, url and stop()"""

    def __init__(self, fake, server, thread):
        self.fake = fake
        self.server = server
        self.thread = thread
        self.port = fake.port
        self.token = fake.token

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


def start_in_thread(port=0, token=None, quiet=True, ticker=True):
    """the fake API on 127.0.0.1:<port> (0 = a free port) in a daemon thread; returns a Handle once it answers"""
    import uvicorn
    fake = Fake(token=token)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    fake.port = sock.getsockname()[1]
    config = uvicorn.Config(create_app(fake, ticker=ticker), host="127.0.0.1", port=fake.port,
                            log_level="warning" if quiet else "info", access_log=not quiet)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="dev-fake-api", daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("the fake API did not start")
    return Handle(fake, server, thread)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default=None, help="a fixed token (default: a random one, printed)")
    ap.add_argument("--tick", type=float, default=1.0, help="seconds between pretend log lines")
    ap.add_argument("--quiet", action="store_true", help="no access log")
    ap.add_argument("--mode", choices=("developer", "player"), default="developer",
                    help="the mode the settings and the status say (the page switches player back to developer)")
    ap.add_argument("--approval", choices=("ask", "auto"), default="ask",
                    help="the setting extension_approval. ask: the local extensions' commands wait for 批准运行; "
                         "auto: every command is allowed by itself")
    ap.add_argument("--rotate", type=int, default=400,
                    help="a local extension's output starts again at line 1 after this many lines")
    ap.add_argument("--no-game-folder", action="store_true",
                    help="the game's AddOns folder is not known: every game addon's state is unknown, 安装 answers 503")
    args = ap.parse_args(argv)
    import uvicorn
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fake = Fake(token=args.token, mode=args.mode, tick=args.tick, rotate=max(2, args.rotate),
                extension_approval=args.approval, addons_known=not args.no_game_folder)
    fake.port = args.port
    print(f"fake daemon: http://127.0.0.1:{args.port}/   token: {fake.token}", flush=True)
    print("open that in a browser, or the window on it:\n"
          f"  .venv\\Scripts\\python.exe -c \"from wuxianworkshop.ui.shell import run_app; run_app(['--fake', 'http://127.0.0.1:{args.port}/'])\"",
          flush=True)
    try:
        uvicorn.run(create_app(fake), host="127.0.0.1", port=args.port, log_level="warning" if args.quiet else "info",
                    access_log=not args.quiet)
    finally:
        fake.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
