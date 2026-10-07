r"""A fake daemon for developing the web page: the endpoints of the daemon (daemon/server.py) that the page uses, with made-up
data that moves (log lines every second, a link that drops and comes back, pings that drift), served together with the page
from src/wuxianworkshop/ui/static.

    .venv\Scripts\python.exe scripts\dev_fake_api.py [--port 8765] [--token TEXT] [--tick 1.0] [--quiet]
        prints the address and the token; open the address in a browser, or open the window on it:
    .venv\Scripts\python.exe -c "from wuxianworkshop.ui.shell import run_app; run_app(['--fake'])"
        (starts this in-process on a free port; or run_app(['--fake', 'http://127.0.0.1:8765/']) for one that is running)

Same rules as the real daemon: /api/* wants Authorization: Bearer <token> (SSE and the snap PNGs also accept ?token=), Host
must be 127.0.0.1 or localhost, and /api/session hands the token to same-origin pages only. Nothing here touches the game:
snaps are drawn with Pillow into a temporary folder, "run" answers canned values, "install" only turns the doctor's red items
green. tests/ui/test_fake_api.py drives create_app() with Starlette's TestClient; the shell calls start_in_thread().
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

GAME_DIR = r"C:\Program Files (x86)\World of Warcraft\_cn_beta_"
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


def api_version():
    try:
        from wuxianworkshop import content
        return content.PACKS.version("api")
    except Exception:
        return None


class Fake:
    """the pretend daemon's state: logs (a ring of 5,000), subscribers, settings, the link that comes and goes"""

    def __init__(self, token=None, mode="developer", tick=1.0, seed=None):
        self.token = token or secrets.token_urlsafe(32)
        self.port = None
        self.mode = mode
        self.tick = tick
        self.started = time.time()
        self.rng = random.Random(seed)
        self.logs = collections.deque(maxlen=5000)
        self.next_id = 1
        self.last_job = 1700000
        self.subscribers = set()
        self.settings = {"mode": mode, "capture": "wgc", "capture_in_use": "wgc", "game_dir": GAME_DIR, "autostart": False,
                         "language": "zh-CN", "onboarded": False}   # the daemon's shape (daemon/service.py settings())
        # the agents' page (agents.py's shape): Claude Code points at an older copy, Codex is not connected, no Cursor
        self.agents = {"claude": "other", "codex": "absent", "cursor": "missing"}
        self.kept = self.kept_seed()             # agent/history.py's shapes: addon -> versions oldest first, and "now"
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
        if n % 5 == 0 or n % 120 in (80, 88, 90):
            self.broadcast("status", self.status())

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
        """POST /api/settings as the daemon takes it: mode, capture, game_dir, autostart, language (unknown keys ignored)"""
        if "capture" in changes and changes["capture"] not in ("gdi", "wgc"):
            raise ValueError('capture: "gdi" or "wgc"')
        if "mode" in changes and changes["mode"] not in ("player", "developer"):
            raise ValueError('mode: "player" or "developer"')
        if "developer" in changes and "mode" not in changes:               # the page's boolean, for convenience
            changes = dict(changes, mode="developer" if changes["developer"] else "player")
        for key in ("mode", "capture", "game_dir", "language"):
            if key in changes:
                self.settings[key] = changes[key] or (None if key == "game_dir" else self.settings[key])
        if "autostart" in changes:
            self.settings["autostart"] = bool(changes["autostart"])
        if "onboarded" in changes:
            self.settings["onboarded"] = bool(changes["onboarded"])
        self.mode = self.settings["mode"]
        self.entry("INFO", "settings: " + ", ".join(f"{k}={v}" for k, v in changes.items()))
        self.broadcast("status", self.status())
        return dict(self.settings)

    PROGRAM = dict(command=r"C:\Users\Someone\AppData\Local\WuxianWorkshop.App\current\wuxian.exe", args=["mcp"], env={})

    def agent(self, host):
        """one agent as agents.py's Host.status() says it"""
        from wuxianworkshop import agents
        title = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor"}[host]
        where = {"claude": r"C:\Users\Someone\.claude.json", "codex": r"C:\Users\Someone\.codex\config.toml",
                 "cursor": r"C:\Users\Someone\.cursor\mcp.json"}[host]
        state = self.agents[host]
        cmd = " ".join([self.PROGRAM["command"], *self.PROGRAM["args"]])
        detail = {"ok": "已接入：会启动 " + cmd, "absent": "还没有接入", "missing": f"没有找到 {title}",
                  "other": r"已接入，但启动的是另一个程序：D:\old\wuxian\wuxian.exe（点「接入」改成现在这个）"}[state]
        return dict(id=host, title=title, present=state != "missing", can_connect=state != "missing", state=state,
                    detail=detail, where=where, apply=agents.CLASSES[host].apply,
                    entry={"command": self.PROGRAM["command"], "args": ["mcp"]} if state == "ok" else None)

    def agents_status(self):
        from wuxianworkshop import agents
        return dict(program=dict(command=self.PROGRAM["command"], args=self.PROGRAM["args"]),
                    hosts=[self.agent(h) for h in ("claude", "codex", "cursor")], manual=agents.manual(self.PROGRAM))

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
        if action in ("connect", "verify") and host != "cursor":
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
        if p.get("name"):
            entry = ix.get(p["name"])
            return JSONResponse(entry) if entry else error("not_found", f"{p['name']}: not in the API manual", 404)
        if "manual" in p:
            topic = ix.manual(p["manual"] or None, p.get("lang"))
            if topic is None:
                return error("not_found", "no such topic", 404)
            return JSONResponse(topic if isinstance(topic, dict) else {"topics": topic})
        if p.get("q"):
            return JSONResponse(dict(query=p["q"], **ix.find(p["q"], p.get("kind") or None, p.get("call") or None, int(p.get("limit") or 20))))
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
            return error("bad_addon", str(e), 400)
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
                    f("Bar.lua", 88, "undefined", "GetSpellInfo is nil in the running game", "a typo, or defined nowhere")],
            warnings=[f("Bar.lua", 12, "global-write", "writes the global counter", "forgot local? Globals are shared by every addon (and taint the Blizzard UI when it reads them)"),
                      f("Bar.lua", 30, "typo", "UnitHelth is neither the client's nor this addon's", "did you mean UnitHealth?")],
            notes=[]))

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
        Route("/api/check", check, methods=["POST"]),
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
    args = ap.parse_args(argv)
    import uvicorn
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fake = Fake(token=args.token, tick=args.tick)
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
