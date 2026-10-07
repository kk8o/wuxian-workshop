"""The daemon's clients: state/daemon.json says where it listens, DaemonClient talks JSON over HTTP with the token
(standard library only, so `wuxian status` is quick), connect() starts the daemon detached when nothing answers: the
desktop program with its window hidden (`wuxian app --background`: its tray icon shows that it runs, and opening the
program later shows that window), or `wuxian serve` alone when there is no window to make (no pywebview / pystray, or
WUXIAN_HEADLESS=1).
Errors from the daemon come back as daemon.api.ApiError (status, code, message); no daemon at all is a ConnectionError,
DaemonGone when nothing listened at all (the request never reached a daemon).
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from ..daemon.api import ApiError, read_daemon_json
from ..paths import logs_dir

START_TIMEOUT = 30.0         # the desktop program takes longer than `serve` to come up (WebView2, the tray)


class DaemonGone(ConnectionError):
    """the connection was refused: no daemon listens there (any more), so the request did nothing"""


class DaemonClient:
    def __init__(self, info, timeout=30.0):
        self.info, self.timeout = info, timeout
        self.base = info.get("url", f"http://127.0.0.1:{info['port']}/").rstrip("/")
        self.token = info["token"]

    def request(self, method, path, body=None, params=None, timeout=None, raw=False):
        """one request; the JSON answer as a dict (bytes with raw=True), ApiError for an error answer"""
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                err = json.loads(payload)["error"]
                extra = {k: v for k, v in err.items() if k not in ("code", "message")}
                raise ApiError(e.code, err.get("code", "error"), err.get("message", str(e)), **extra) from None
            except (ValueError, KeyError, TypeError):
                raise ApiError(e.code, "http_error", f"{e.code} {e.reason}: {payload[:200]!r}") from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            kind = DaemonGone if isinstance(reason, ConnectionRefusedError) else ConnectionError
            raise kind(f"cannot reach the daemon at {self.base}: {reason}") from None
        if raw:
            return payload
        return json.loads(payload) if payload else {}

    def get(self, path, **params):
        return self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path, timeout=None, **body):
        return self.request("POST", path, body=body, timeout=timeout)

    def get_bytes(self, path):
        return self.request("GET", path, raw=True)

    # the endpoints

    def status(self):
        return self.get("/api/status")

    def run(self, code, timeout_ms=10000, addon=None):
        return self.post("/api/run", code=code, timeout_ms=timeout_ms, addon=addon, timeout=timeout_ms / 1000 + 10)

    def load(self, target, mode=None, reset=True, timeout_ms=30000, check=True):
        return self.post("/api/load", target=target, mode=mode, reset=reset, timeout_ms=timeout_ms, check=check,
                         timeout=timeout_ms / 1000 + 10)

    def try_(self, code=None, slash=None, seconds=2, addon=None, snap=False, frame=None, events=None, timeout_ms=10000):
        return self.post("/api/try", code=code, slash=slash, seconds=seconds, addon=addon, snap=snap, frame=frame,
                         events=events, timeout_ms=timeout_ms, timeout=seconds + timeout_ms / 1000 + 120)

    def check(self, target, live=True):
        return self.post("/api/check", target=target, live=live, timeout=60)

    def watch(self, action="list", target=None):
        return self.post("/api/watch", action=action, target=target)

    def snap(self, region=None, max_width=1280):
        return self.post("/api/snap", region=region, max_width=max_width)

    def trace(self, seconds=10, events=None, max_events=200, args=6):
        return self.post("/api/trace", seconds=seconds, events=events, max_events=max_events, args=args, timeout=seconds + 120)

    def inspect(self, target=None, mouse=False, depth=1, snap=False):
        return self.post("/api/inspect", target=target, mouse=mouse, depth=depth, snap=snap, timeout=60)

    def history(self, addon=None, vid=None, against="now"):
        return self.get("/api/history", addon=addon, id=vid, against=against)

    def checkpoint(self, addon, note=""):
        return self.post("/api/history", action="checkpoint", addon=addon, note=note, timeout=120)

    def restore(self, addon, vid):
        return self.post("/api/history", action="restore", addon=addon, id=vid, timeout=120)

    def reload(self, reason=""):
        return self.post("/api/reload", reason=reason)

    def say(self, text):
        return self.post("/api/say", text=text)

    def logs(self, since=0, limit=200, kinds=None):
        return self.get("/api/logs", since=since, limit=limit, kinds=kinds)

    def doctor(self, game_dir=None):
        return self.get("/api/doctor", game_dir=game_dir)

    def addons(self, game_dir=None):
        return self.get("/api/addons", game_dir=game_dir)

    def errors(self, addon=None, limit=100, game_dir=None):
        return self.get("/api/errors", addon=addon, limit=limit, game_dir=game_dir)

    def install(self, game_dir=None, clean=True, mode=None):
        return self.post("/api/install", game_dir=game_dir, clean=clean, mode=mode, timeout=200)

    def settings(self, update=None):
        return self.post("/api/settings", **update) if update else self.get("/api/settings")

    def quit(self):
        return self.post("/api/quit")

    def new_addon(self, name, title=None, notes="", template="basic"):
        return self.post("/api/new_addon", name=name, title=title, notes=notes, template=template)

    def update(self, action=None, background=False):
        if action is None:
            return self.get("/api/update")
        return self.post("/api/update", action=action, background=background, timeout=60)

    def events(self, since=0, kinds=None, timeout=60.0):
        """the SSE stream /api/events as (event, data) pairs; the daemon sends a status every 2 s, so a silence
        longer than timeout means it is gone"""
        params = dict(token=self.token, since=since)
        if kinds:
            params["kinds"] = kinds
        req = urllib.request.Request(f"{self.base}/api/events?{urllib.parse.urlencode(params)}",
                                     headers={"Accept": "text/event-stream"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                event, data = None, []
                for raw in resp:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if not line:
                        if data:
                            yield event or "message", json.loads("\n".join(data))
                        event, data = None, []
                        continue
                    if line.startswith(":"):
                        continue
                    field, _, value = line.partition(":")
                    value = value[1:] if value.startswith(" ") else value
                    if field == "event":
                        event = value
                    elif field == "data":
                        data.append(value)
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, "http_error", f"{e.code} {e.reason}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ConnectionError(f"the event stream from {self.base} ended: {getattr(e, 'reason', e)}") from None


def self_command():
    """how to start this program again: the frozen exe, or this interpreter with the package"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "wuxianworkshop.cli.main"]


def background_command():
    """the sub-command that runs the daemon in the background: the desktop program with its window hidden, or `serve`
    when it cannot make a window"""
    if os.environ.get("WUXIAN_HEADLESS") != "1" and all(importlib.util.find_spec(m) for m in ("webview", "pystray")):
        return ["app", "--background"]
    return ["serve"]


CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def spawn_daemon(args=()):
    """the daemon as a detached process (no console, survives this one); its output goes to logs/daemon.log.
    An MCP host may run `wuxian mcp` in a job object that kills every process in it when the host lets go of the
    server (the Python MCP SDK does: KILL_ON_JOB_CLOSE): the daemon asks to leave that job, so that it outlives the
    agent's session; a job that does not allow it keeps the daemon (it then stops with the server). Node hosts (Claude
    Code, Cursor) let grandchildren leave their job silently."""
    out = open(logs_dir() / "daemon.log", "ab")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    command = self_command() + background_command() + list(args)
    try:
        if sys.platform == "win32":
            try:
                return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True,
                                        creationflags=flags | CREATE_BREAKAWAY_FROM_JOB)
            except PermissionError:                  # in a job that does not allow breaking away
                pass
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=out, creationflags=flags,
                                close_fds=True)
    finally:
        out.close()


def probe(info, timeout=3.0):
    """a client of the daemon daemon.json describes, if it answers; else None"""
    if not info:
        return None
    client = DaemonClient(info)
    try:
        client.request("GET", "/api/status", timeout=timeout)
    except (ConnectionError, ApiError):
        return None
    return client


def connect(start=True, timeout=START_TIMEOUT, log=None, serve_args=()):
    """a client of the running daemon; with start, the daemon is started (detached) when none answers"""
    log = log or (lambda text: None)
    client = probe(read_daemon_json())
    if client is not None:
        return client
    if not start:
        raise ConnectionError("the daemon is not running (start it with `wuxian serve`, or `wuxian` for the window)")
    command = background_command()
    log(f"the daemon is not running: starting `wuxian {' '.join(command)}`")
    t0 = time.time()
    proc = spawn_daemon(serve_args)
    while time.time() - t0 < timeout:
        info = read_daemon_json()
        if info is not None and info.get("started", 0) >= t0 - 2:     # fresh, not left behind by a dead daemon
            client = probe(info)
            if client is not None:
                log(f"daemon up at {client.base} (pid {info.get('pid')})")
                return client
        if proc.poll() is not None:                                   # it ended: maybe another instance won the race
            client = probe(read_daemon_json())
            if client is not None:
                return client
            raise ConnectionError(f"`wuxian {' '.join(command)}` exited with code {proc.returncode} before it listened "
                                  f"(see {logs_dir() / 'daemon.log'})")
        time.sleep(0.15)
    raise ConnectionError(f"the daemon did not come up within {timeout:.0f} s (see {logs_dir() / 'daemon.log'})")
