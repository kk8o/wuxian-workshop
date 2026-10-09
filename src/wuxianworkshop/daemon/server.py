r"""The daemon: the local HTTP interface (the routes below) on 127.0.0.1, one instance per user.

    wuxian serve [--mode developer|player] [--capture gdi|wgc] [--game "<client folder>"] [--rate 0.05] [--ping-every 10]

start() runs it inside another program (the shell, `wuxian` without arguments) and returns a DaemonHandle; main() is
`wuxian serve`. One instance per user: the named mutex Local\WuxianWorkshop (a second start() raises AlreadyRunning).
The running one's address and token are in state/daemon.json (read_daemon_json), written once the port is bound and
removed on exit.
Threads: uvicorn with its asyncio loop on one thread (the Starlette app: the routes here, the MCP server at /mcp), the
Companion on the worker thread (companion.CompanionLoop); service.Service hands the requests across and holds the
Journal (the log ring, logs/debug.log).
Every /api/* request and /mcp needs `Authorization: Bearer <token>` (GET requests, SSE among them, may use ?token=), a
Host of 127.0.0.1:<port> or localhost:<port>, and no Origin header or the same origin; /api/session gives the token to
the page served at / (same checks, no token). Errors are {"error": {"code", "message"}} with a 4xx / 5xx status.
"""
import argparse
import asyncio
import contextlib
import ctypes
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs

import uvicorn
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import __version__
from ..core.game import addons_dir
from ..paths import daemon_file, logs_dir, settings_file, snaps_dir, ui_static_dir
from .api import ApiError, read_daemon_json  # noqa: F401  (read_daemon_json is this module's interface too)
from .companion import CompanionLoop, installed_at
from .journal import Journal
from .service import Service

MUTEX = r"Local\WuxianWorkshop"
ERROR_ALREADY_EXISTS = 183
SNAP_NAME = re.compile(r"^[\w.-]+\.png$")
STARTUP_WAIT = 15.0


class AlreadyRunning(RuntimeError):
    """another daemon holds the mutex; info is its daemon.json (None when it has not written one yet)"""

    def __init__(self, info):
        super().__init__("the daemon is already running" + (f" at {info.get('url')} (pid {info.get('pid')})" if info else ""))
        self.info = info


class SingleInstance:
    """a named mutex held for the life of the process: the second CreateMutexW of the name says ERROR_ALREADY_EXISTS"""

    def __init__(self, name=MUTEX):
        self.name, self.handle = name, None
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.CreateMutexW.restype = ctypes.c_void_p
        self.k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        self.k32.CloseHandle.argtypes = (ctypes.c_void_p,)

    def acquire(self):
        h = self.k32.CreateMutexW(None, 0, self.name)
        if not h:                                    # cannot tell: do not keep the daemon from running
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self.k32.CloseHandle(h)
            return False
        self.handle = h
        return True

    def release(self):
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None


class UiFiles(StaticFiles):
    """the page's files, revalidated on every load (their ETag / Last-Modified make that cheap): after an update of the
    program the window must not go on with the previous version's script or styles from the WebView2 cache"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def write_daemon_json(info):
    path = daemon_file()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(info, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def remove_daemon_json(pid):
    """our daemon.json, not one a later instance wrote"""
    info = read_daemon_json()
    if info is not None and info.get("pid") == pid:
        try:
            daemon_file().unlink()
        except OSError:
            pass


class Access:
    """the checks on /api/* and /mcp: Host, Origin, the token"""

    def __init__(self, token, host="127.0.0.1"):
        self.token, self.host, self.port = token, host, None
        self.hosts = self.origins = frozenset()

    def bind(self, port):
        self.port = port
        self.hosts = frozenset({f"{self.host}:{port}", f"localhost:{port}"})
        self.origins = frozenset(f"http://{h}" for h in self.hosts)

    def check(self, scope):
        """None when the request may pass, else (status, code, message)"""
        headers = Headers(scope=scope)
        if headers.get("host", "") not in self.hosts:
            return 403, "bad_host", f"the Host header must be {self.host}:{self.port} or localhost:{self.port}"
        origin = headers.get("origin")
        if origin and origin.rstrip("/") not in self.origins:
            return 403, "bad_origin", "requests from another origin are not accepted"
        if scope["path"] == "/api/session":
            return None
        token = None
        auth = headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            token = auth[7:].strip()
        elif scope["type"] == "websocket" or scope.get("method") == "GET":
            token = (parse_qs(scope.get("query_string", b"").decode("latin-1")).get("token") or [None])[0]
        if not token or not secrets.compare_digest(token, self.token):
            return 401, "unauthorized", "Authorization: Bearer <token> is needed (the token is in state/daemon.json)"
        return None


class Guard:
    """ASGI middleware: Access.check on /api/* and /mcp; the page at / and its files pass"""

    def __init__(self, app, access):
        self.app, self.access = app, access

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] not in ("http", "websocket") or not (path.startswith("/api/") or path == "/mcp"):
            return await self.app(scope, receive, send)
        denied = self.access.check(scope)
        if denied is None:
            return await self.app(scope, receive, send)
        status, code, message = denied
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        await JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)(scope, receive, send)


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def mcp_route(service):
    """(the /mcp route, the MCPServer) or (None, None) with the reason when the mcp package is not installed"""
    try:
        from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
        from mcp.server.transport_security import TransportSecuritySettings
        from ..mcp.server import build_server
    except ImportError as e:
        return None, None, f"MCP is off: {e}"
    mcp = build_server(service)
    mcp.streamable_http_app(streamable_http_path="/mcp",           # makes the session manager; Guard does Host / Origin
                            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return Route("/mcp", endpoint=StreamableHTTPASGIApp(mcp.session_manager)), mcp, None


def make_app(service, access, handle, mcp=None, mcp_endpoint=None):
    """the Starlette application: the routes of the API, SSE, the MCP endpoint (a Route), the page at /"""

    def api(fn):
        async def endpoint(request):
            try:
                result = await fn(request)
                # made here: a value JSON cannot carry (NaN, a lone surrogate) is an error said like any other
                return result if isinstance(result, Response) else JSONResponse(result)
            except ApiError as e:
                return JSONResponse(e.body(), status_code=e.status)
            except Exception as e:                   # a bug: say so instead of a bare 500
                service.journal.add("INFO", f"daemon error on {request.method} {request.url.path}: {e!r}")
                return JSONResponse({"error": {"code": "internal", "message": f"{type(e).__name__}: {e!r}"[:2000]}},
                                    status_code=500)
        return endpoint

    async def body_of(request):
        raw = await request.body()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise ApiError(400, "bad_json", "the request body must be JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "bad_json", "the request body must be a JSON object")
        return data

    def int_param(request, name, default):
        value = request.query_params.get(name)
        if value in (None, ""):
            return default
        try:
            return int(value)
        except ValueError:
            raise ApiError(400, "bad_request", f"{name}: an integer") from None

    @api
    async def status(request):
        return await service.status()

    @api
    async def run(request):
        b = await body_of(request)
        return await service.run(b.get("code"), b.get("timeout_ms", 10000), b.get("addon"))

    @api
    async def load(request):
        b = await body_of(request)
        return await service.load(b.get("target"), b.get("mode"), b.get("reset", True), b.get("timeout_ms", 30000),
                                  b.get("check", True) is not False)

    @api
    async def watch(request):
        b = await body_of(request)
        return await service.watch(b.get("action", "list"), b.get("target"))

    @api
    async def snap(request):
        b = await body_of(request)
        return await service.snap(b.get("region"), b.get("max_width", 1280))

    @api
    async def snaps(request):
        name = request.path_params["name"]
        path = snaps_dir() / name
        if not SNAP_NAME.match(name) or not path.is_file():
            raise ApiError(404, "not_found", f"no snap named {name}")
        return FileResponse(str(path), media_type="image/png")

    @api
    async def reload(request):
        b = await body_of(request)
        return await service.reload(b.get("reason", ""))

    @api
    async def say(request):
        b = await body_of(request)
        return await service.say(b.get("text"))

    @api
    async def logs(request):
        return await service.logs(int_param(request, "since", 0), int_param(request, "limit", 200),
                                  request.query_params.get("kinds"))

    @api
    async def events(request):
        """SSE: the log entries from ?since= on as `event: log`, the status every 2 s as `event: status`"""
        since = int_param(request, "since", 0)
        kinds = request.query_params.get("kinds")
        kinds = {k.strip().upper() for k in kinds.split(",") if k.strip()} if kinds else None
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=1000)

        def push(entry):
            if kinds and entry["kind"] not in kinds:
                return
            if queue.full():                       # a slow reader loses its oldest entries, nobody else waits
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(entry)

        unsubscribe = service.journal.subscribe(loop, push)

        async def stream():
            last = since - 1
            try:
                yield "retry: 2000\n\n"
                entries, _, truncated = service.journal.since(since, limit=2000, kinds=kinds)
                if truncated:
                    yield sse("truncated", {"since": since})
                for e in entries:
                    last = e["id"]
                    yield sse("log", e)
                yield sse("status", await service.status())
                while True:
                    try:
                        entry = await asyncio.wait_for(queue.get(), 2.0)
                    except asyncio.TimeoutError:
                        yield sse("status", await service.status())
                        continue
                    while True:
                        if entry["id"] > last:         # not one the backlog above already carried
                            last = entry["id"]
                            yield sse("log", entry)
                        try:
                            entry = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
            finally:
                unsubscribe()

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @api
    async def doctor(request):
        return await service.doctor(request.query_params.get("game_dir"))

    @api
    async def fix(request):
        b = await body_of(request)
        return await service.fix(b.get("fix"), b.get("game_dir"))

    @api
    async def addons(request):
        return await service.addons(request.query_params.get("game_dir") or None)

    @api
    async def errors(request):
        return await service.errors(request.query_params.get("addon") or None, int_param(request, "limit", 100),
                                    request.query_params.get("game_dir") or None)

    @api
    async def install(request):
        b = await body_of(request)
        return await service.install(b.get("game_dir"), b.get("clean", True), b.get("mode"))

    @api
    async def apidocs_(request):
        p = request.query_params
        return await service.apidocs(p.get("q"), p.get("kind"), int_param(request, "limit", 20), p.get("name"),
                                     p.get("manual") if "manual" in p else None, p.get("call"), p.get("lang"),
                                     "systems" in p, p.get("system"), int_param(request, "offset", 0))

    @api
    async def reveal(request):
        return await service.reveal((await body_of(request)).get("name"))

    @api
    async def new_addon(request):
        b = await body_of(request)
        return await service.new_addon(b.get("name"), b.get("title"), b.get("notes") or "", b.get("template") or "basic")

    @api
    async def update(request):
        if request.method != "POST":
            return await service.update()
        body = await body_of(request)
        action = body.get("action")
        if action != "apply":
            return await service.update(action)
        u = service.updater
        if u is None or not u.apply_on_exit(bool(body.get("background"))):   # Update.exe waits for this process to end
            raise ApiError(409, "not_ready", "no downloaded update to apply")
        service.journal.add("INFO", "update: the program stops now; Velopack puts the new version in place and starts it")
        handle.request_stop(delay=0.5)
        return dict(u.status(), restarting=True)

    @api
    async def quit_(request):
        service.journal.add("INFO", "quit requested through the API")
        handle.request_stop()
        return {"bye": True}

    @api
    async def session(request):
        return {"token": access.token, "port": access.port, "url": handle.url, "mcp_url": handle.mcp_url,
                "version": __version__}

    @api
    async def show(request):
        return {"shown": handle.show()}

    @api
    async def settings(request):
        return await service.settings(await body_of(request) if request.method == "POST" else None)

    @api
    async def trace(request):
        b = await body_of(request)
        return await service.trace(b.get("seconds", 10), b.get("events"), b.get("max_events", 200), b.get("args", 6))

    @api
    async def inspect(request):
        b = await body_of(request)
        return await service.inspect(b.get("target"), bool(b.get("mouse")), b.get("depth", 1), bool(b.get("snap")))

    @api
    async def addon_events(request):
        b = await body_of(request)
        return await service.addon_events(b.get("addon"), b.get("topic"), b.get("since", 0), b.get("limit", 100),
                                          b.get("wait", 0))

    @api
    async def call_(request):
        b = await body_of(request)
        return await service.call_exposed(b.get("addon"), b.get("name"), b.get("args"), b.get("timeout_ms", 10000))

    @api
    async def respond(request):
        b = await body_of(request)
        return await service.respond(b.get("request"), b.get("data"), b.get("timeout_ms", 10000))

    @api
    async def addon_api(request):
        b = await body_of(request)
        return await service.addon_api(b.get("addon"))

    @api
    async def agents_(request):
        if request.method != "POST":
            return await service.agents()
        b = await body_of(request)
        return await service.agents(b.get("action"), b.get("host"))

    @api
    async def try_(request):
        b = await body_of(request)
        return await service.try_(b.get("code"), b.get("slash"), b.get("seconds", 2), b.get("addon"), bool(b.get("snap")),
                                  b.get("frame"), b.get("events"), b.get("timeout_ms", 10000))

    @api
    async def check(request):
        b = await body_of(request)
        return await service.check(b.get("target"), b.get("live", True) is not False)

    @api
    async def extensions_(request):
        if request.method != "POST":
            return await service.extensions(request.query_params.get("refresh") in ("1", "true"))
        b = await body_of(request)
        return await service.extension(b.get("action"), b.get("id"))

    @api
    async def history_(request):
        if request.method != "POST":
            p = request.query_params
            return await service.history(p.get("addon") or None, p.get("id") or None, p.get("against") or "now")
        b = await body_of(request)
        action = b.get("action")
        if action == "checkpoint":
            return await service.checkpoint(b.get("addon"), b.get("note") or "")
        if action == "restore":
            return await service.restore(b.get("addon"), b.get("id"))
        if action == "forget":
            return await service.forget(b.get("addon"))
        raise ApiError(400, "bad_request", 'action: "checkpoint", "restore" or "forget"')

    routes = [
        Route("/api/status", status, methods=["GET"]),
        Route("/api/run", run, methods=["POST"]),
        Route("/api/load", load, methods=["POST"]),
        Route("/api/watch", watch, methods=["POST"]),
        Route("/api/snap", snap, methods=["POST"]),
        Route("/api/snaps/{name}", snaps, methods=["GET"]),
        Route("/api/reload", reload, methods=["POST"]),
        Route("/api/say", say, methods=["POST"]),
        Route("/api/logs", logs, methods=["GET"]),
        Route("/api/events", events, methods=["GET"]),
        Route("/api/doctor", doctor, methods=["GET"]),
        Route("/api/fix", fix, methods=["POST"]),
        Route("/api/addons", addons, methods=["GET"]),
        Route("/api/errors", errors, methods=["GET"]),
        Route("/api/install", install, methods=["POST"]),
        Route("/api/quit", quit_, methods=["POST"]),
        Route("/api/session", session, methods=["GET"]),
        Route("/api/show", show, methods=["POST"]),
        Route("/api/settings", settings, methods=["GET", "POST"]),
        Route("/api/update", update, methods=["GET", "POST"]),
        Route("/api/apidocs", apidocs_, methods=["GET"]),
        Route("/api/new_addon", new_addon, methods=["POST"]),
        Route("/api/reveal", reveal, methods=["POST"]),
        Route("/api/agents", agents_, methods=["GET", "POST"]),
        Route("/api/trace", trace, methods=["POST"]),
        Route("/api/inspect", inspect, methods=["POST"]),
        Route("/api/addon_events", addon_events, methods=["POST"]),
        Route("/api/call", call_, methods=["POST"]),
        Route("/api/addon_api", addon_api, methods=["POST"]),
        Route("/api/respond", respond, methods=["POST"]),
        Route("/api/history", history_, methods=["GET", "POST"]),
        Route("/api/extensions", extensions_, methods=["GET", "POST"]),
        Route("/api/check", check, methods=["POST"]),
        Route("/api/try", try_, methods=["POST"]),
    ]
    if mcp_endpoint is not None:
        routes.append(mcp_endpoint)
    static = ui_static_dir()
    if (static / "index.html").is_file():
        routes.append(Mount("/", app=UiFiles(directory=str(static), html=True), name="ui"))
    else:
        async def placeholder(request):
            return PlainTextResponse("无限工坊守护进程在运行。网页界面（ui/static/index.html）还没有打进包里；接口在 /api/*，MCP 在 /mcp。\n"
                                     "The WuxianWorkshop daemon is running; the web page is not bundled yet. "
                                     "API: /api/*, MCP: /mcp.\n")
        routes.append(Route("/", placeholder, methods=["GET"]))

    @contextlib.asynccontextmanager
    async def lifespan(app):
        handle.loop = asyncio.get_running_loop()
        if mcp is not None:
            async with mcp.session_manager.run():
                yield
        else:
            yield

    app = Starlette(routes=routes, lifespan=lifespan)
    return Guard(app, access)


class DaemonHandle:
    """the running daemon, for the program that started it (the shell): where it listens, how to stop it.
    on_show: called (on its own thread) for POST /api/show, the shell shows its window; window: that window is coming
    (the shell sets on_show while it makes the window, seconds after the daemon listens), so a request before then is
    kept for it; on_quit: called once the daemon has stopped, from the API or stop()."""

    def __init__(self, service, lock, token, mode, host="127.0.0.1", port=0, window=False):
        self.service, self.lock, self.token, self.mode = service, lock, token, mode
        self.host, self.port, self.pid = host, port, os.getpid()
        self.window = window
        self._on_show = None
        self._show_kept = False
        self._show_lock = threading.Lock()
        self.on_quit = None
        self.access = Access(token, host)
        self.server = self.thread = self.loop = self.mcp = None
        self.error = None
        self.started = time.time()
        self._finished = threading.Event()
        self._cleanup_lock = threading.Lock()
        self._cleaned = False

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/"

    @property
    def mcp_url(self):
        return f"http://{self.host}:{self.port}/mcp" if self.mcp is not None else None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def info(self):
        """what daemon.json holds"""
        return dict(pid=self.pid, port=self.port, token=self.token, version=__version__, started=round(self.started, 3),
                    mode=self.mode, url=self.url, mcp_url=self.mcp_url)

    def _start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((self.host, self.port))
        self.port = sock.getsockname()[1]
        self.access.bind(self.port)
        route, self.mcp, off = mcp_route(self.service)
        if off:
            self.service.journal.add("INFO", off)
        self.service.url, self.service.mcp_url = self.url, self.mcp_url
        app = make_app(self.service, self.access, self, mcp=self.mcp, mcp_endpoint=route)
        config = uvicorn.Config(app, host=self.host, port=self.port, log_config=None, log_level="warning", access_log=False,
                                lifespan="on", loop="asyncio", timeout_graceful_shutdown=3)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._serve, args=(sock,), name="wuxian-http", daemon=True)
        self.thread.start()
        deadline = time.time() + STARTUP_WAIT
        while not self.server.started and self.thread.is_alive() and time.time() < deadline:
            time.sleep(0.02)
        if not self.server.started:
            self.server.should_exit = True
            self._cleanup()
            raise RuntimeError(f"the HTTP server did not start: {self.error!r}")
        write_daemon_json(self.info())
        self.service.start()

    def _serve(self, sock):
        try:
            self.server.run(sockets=[sock])      # uvicorn's own asyncio loop lives on this thread
        except BaseException as e:               # SystemExit on a lifespan failure, among others
            self.error = e
        finally:
            self._cleanup()

    def _cleanup(self):
        with self._cleanup_lock:
            if self._cleaned:
                return
            self._cleaned = True
        self.service.close()
        remove_daemon_json(self.pid)
        self.lock.release()
        self._finished.set()
        if self.on_quit is not None:
            try:
                self.on_quit()
            except Exception:
                pass

    def request_stop(self, delay=0.2):
        """stop a moment from now (the quit response goes out first); from the server's own thread"""
        def exit_():
            self.server.should_exit = True
        if self.loop is not None:
            self.loop.call_later(delay, exit_)
        else:
            exit_()

    def stop(self, timeout=10.0):
        """stop the server and the companion; True when everything has ended within timeout"""
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout)
        self._cleanup()
        return not self.running

    def wait(self, timeout=None):
        """block until the daemon has stopped (Ctrl+C in the main thread gets through); True once it has"""
        return self._finished.wait(timeout)

    @property
    def on_show(self):
        return self._on_show

    @on_show.setter
    def on_show(self, callback):
        """the shell hooks on its window: a show asked for before is made now (pywebview's show() waits for the window)"""
        with self._show_lock:
            self._on_show, kept = callback, self._show_kept
            self._show_kept = kept and callback is None
        if kept and callback is not None:
            threading.Thread(target=callback, name="wuxian-show", daemon=True).start()

    def show(self):
        """POST /api/show: the shell's on_show on its own thread; while the shell's window is still coming (window, no
        on_show yet) the request is kept for it; False when no window comes (`wuxian serve`)"""
        with self._show_lock:
            callback = self._on_show
            if callback is None:
                self._show_kept = self.window
                return self.window
        threading.Thread(target=callback, name="wuxian-show", daemon=True).start()
        return True


def saved_settings():
    """state/settings.json as the 设置 page left it ({} when there is none)"""
    try:
        data = json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def start(mode=None, capture=None, game_dir=None, *, rate=0.05, ping_every=10.0, wait_restart=True,
          worker_factory=None, mutex_name=None, host="127.0.0.1", port=0, window=False):
    """the daemon in this process: the HTTP server on its thread, the companion on another; returns a DaemonHandle
    (port, token, url, mcp_url; stop(), wait()). AlreadyRunning when another instance holds the mutex.
    mode / capture / game_dir: None takes what the 设置 page saved (state/settings.json), else developer / wgc.
    worker_factory(on_debug=, log=) replaces the CompanionLoop (tests: a loop without a screen).
    window: the program opens a window on the daemon's page once it listens (the shell): POST /api/show before that
    window has set on_show is kept for it (DaemonHandle.show)."""
    saved = saved_settings()
    mode = mode or (saved.get("mode") if saved.get("mode") in ("developer", "player") else "developer")
    capture = capture or (saved.get("capture") if saved.get("capture") in ("gdi", "wgc") else "wgc")
    saved_dir, gone = saved.get("game_dir") or None, None
    if saved_dir and not game_dir and not Path(saved_dir).is_dir():
        gone, saved_dir = saved_dir, None                  # moved or removed (a new client folder): the running game's
    game_dir = game_dir or saved_dir
    lock = SingleInstance(mutex_name or os.environ.get("WUXIAN_MUTEX") or MUTEX)   # WUXIAN_MUTEX: a test instance
                                                                                    # beside the one in use
    if not lock.acquire():
        raise AlreadyRunning(read_daemon_json())
    try:
        journal = Journal(logs_dir() / "debug.log")
        if gone:
            journal.add("INFO", f"the saved game folder {gone} is not there any more: the running game's is taken")
        real = worker_factory is None                    # the game's own loop: its addons follow the program
        if worker_factory is None:
            try:
                addons = addons_dir(game_dir)
            except SystemExit as e:                      # no window, no default folder: the loop learns it later
                addons = None
                journal.add("INFO", f"game folder not known yet ({e}); it is taken from the game window when it appears")
            installed = installed_at()

            def worker_factory(on_debug, log):
                return CompanionLoop(addons, capture=capture, rate=rate, ping_every=ping_every, wait_restart=wait_restart,
                                     installed=installed, log=log, on_debug=on_debug, link=(mode == "developer"),
                                     echo_debug=False)      # the journal gets each debug message once, with its kind
        service = Service(worker_factory, journal=journal, mode=mode, game_dir=game_dir, sync_addons=real)
        handle = DaemonHandle(service, lock, secrets.token_urlsafe(32), mode, host=host, port=port, window=window)
        handle._start()
    except BaseException:
        lock.release()
        raise
    journal.add("INFO", f"daemon {__version__} listening on {handle.url} (pid {os.getpid()}, mode {mode}, "
                        f"MCP {handle.mcp_url or 'off'})")
    return handle


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wuxian serve", description="run the daemon: the local API on 127.0.0.1, MCP at /mcp")
    ap.add_argument("--mode", choices=("developer", "player"), default=None,
                    help="developer: the WoWBridge link runs; player: only the game window is followed "
                         "(default: what the 设置 page saved, else developer)")
    ap.add_argument("--capture", choices=("gdi", "wgc"), default=None,
                    help="wgc: the window itself, also when covered; gdi: the screen (the corner must be visible) "
                         "(default: what the 设置 page saved, else wgc)")
    ap.add_argument("--game", help="client folder that holds Interface\\AddOns (default: the running game's, else _cn_beta_)")
    ap.add_argument("--rate", type=float, default=0.05, help="seconds between screen reads")
    ap.add_argument("--ping-every", type=float, default=10.0, help="seconds between pings")
    ap.add_argument("--no-wait-restart", action="store_true", help="do not wait for the game to restart after an install")
    args = ap.parse_args(argv)
    try:
        handle = start(args.mode, args.capture, args.game, rate=args.rate, ping_every=args.ping_every,
                       wait_restart=not args.no_wait_restart)
    except AlreadyRunning as e:
        print(e, flush=True)
        return 0
    print(f"wuxian daemon {__version__}: {handle.url} (MCP: {handle.mcp_url or 'off'}), pid {os.getpid()}; "
          f"daemon.json: {daemon_file()}; Ctrl+C stops it", flush=True)
    try:
        while not handle.wait(0.5):
            pass
    except KeyboardInterrupt:
        print("stopping", flush=True)
    handle.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
