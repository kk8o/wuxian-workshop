"""What the daemon does for its clients, behind the HTTP routes (server.py) and the MCP tools (mcp/server.py): the
operations of the HTTP interface (daemon/server.py) as async methods that return the JSON bodies, or raise api.ApiError.

The Companion lives on the worker thread (companion.CompanionLoop); every operation that touches it is a callable handed
to CompanionLoop.request() and awaited here. run and load remember the job ids the Companion hands out; the RUN results
the addon sends back arrive as on_debug() on the worker thread and resolve the waiting futures through the asyncio loop.
Everything the Companion logs or reports goes into the Journal (INFO for its notes).
Before an addon's code goes to the game (load, the start of a watch, a watched file saved) a version of the addon is kept
(agent/history.py): load and watch keep it before they answer, the saves of a watch on the history thread. Its Lua is
checked first (agent/lint.py): a syntax error keeps it out of the game (load answers 422 check_failed, a watched save is
not loaded), the other findings come with load's answer; check() is the whole report, the running game asked about the
names it could not settle.
"""
import asyncio
import inspect
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import __version__, agents, apidocs, content, i18n, scaffold
from ..agent import history, lint, probes
from ..agent.commands import toc_files
from ..agent.snap import SnapError, snap_image
from ..cli.mcpconfig import mcp_command
from ..core import mailbox as MB
from ..paths import settings_file
from ..updater import Scheduler, Updater
from .api import ApiError, chunk_file, parse_run

WORKER_TIMEOUT = 30.0        # seconds a request may wait for the worker thread (a WGC snapshot may hold it for one)
MAX_TIMEOUT_MS = 600_000


def resolve_target(addons, spec):
    """the file or folder a load / watch target names: absolute, or relative to the AddOns or the client folder"""
    path = Path(spec)
    if not path.is_absolute():
        path = next((base / spec for base in (addons, addons.parent.parent) if (base / spec).exists()), None)
    return path if path is not None and path.exists() else None


def is_own(addons, path):
    """WoWBridge's own files: loading them again would start a second link"""
    p = path.resolve()
    return any(p == d or d in p.parents for d in (addons.resolve() / "WoWBridge", addons.resolve() / "!WuxianWorkshop"))


def call_load(comp, spec, reset):
    """comp.load(spec), with reset once the Companion's load takes it (the v0.8 addon's CODE reset flag)"""
    try:
        takes_reset = "reset" in inspect.signature(comp.load).parameters
    except (TypeError, ValueError):
        takes_reset = False
    return comp.load(spec, reset=reset) if takes_reset else comp.load(spec)


def _resolve(fut, value):
    if not fut.done():
        fut.set_result(value)


def _fail(fut, error):
    if not fut.done():
        fut.set_exception(error)


class Service:
    def __init__(self, worker_factory, *, journal, mode="developer", game_dir=None, version=__version__, updater=None,
                 sync_addons=False):
        self.journal, self.mode, self.game_dir, self.version = journal, mode, game_dir, version
        self.sync_addons_on = sync_addons     # the real daemon: the game folder's addons follow the program (sync_addons)
        self.addon_sync, self.addons_pending = None, None
        self.updater, self.update_checks = updater, None      # updater.Updater; made in start() unless given
        self.content, self.content_checks = None, None        # content.ContentUpdater: newer content packs
        self.started = time.time()
        self.pending = {}            # job id -> (asyncio loop, future) of a run / load waiting for its RUN result
        self.tells = None            # (kind, text) the Companion reported during the worker call in progress
        self.worker = worker_factory(on_debug=self.on_debug, log=self.on_log)
        self.thread = None
        self.url = self.mcp_url = None        # set by the server once the port is known
        self.closed = False
        self.tracing = asyncio.Lock()         # one trace at a time: they share the game-side frame
        self.history_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wuxian-history")
        self.history_big = set()             # addons too big to keep: said once
        if self.worker is not None:
            self.worker.before_load = self.watch_saved   # the saves of a watch keep a version first

    # --- the worker thread -----------------------------------------------------------------------------------------

    def start(self):
        self.thread = threading.Thread(target=self.worker.run, name="wuxian-companion", daemon=True)
        self.thread.start()
        if self.updater is None:
            self.updater = Updater(note=lambda text: self.journal.add("INFO", text))
        if self.updater.supported:
            self.update_checks = Scheduler(self.updater.check).start()
        self.content = content.ContentUpdater(content.PACKS, note=lambda text: self.journal.add("INFO", text))
        self.content_checks = Scheduler(self.content.check, first=40, name="wuxian-content").start()
        if self.sync_addons_on:
            self.addon_sync = Scheduler(self.sync_addons, first=8, every=30, name="wuxian-addons").start()

    def close(self):
        """stop the worker; whoever waits for a job hears that the daemon is going"""
        self.closed = True
        self.history_pool.shutdown(wait=False, cancel_futures=True)
        for checks in (self.update_checks, self.content_checks, self.addon_sync):
            if checks is not None:
                checks.stop()
        self.worker.stop()
        if self.thread is not None:
            self.thread.join(5.0)
        for loop, fut in list(self.pending.values()):
            try:
                loop.call_soon_threadsafe(_fail, fut, ApiError(503, "stopping", "the daemon is shutting down"))
            except RuntimeError:
                pass
        self.pending.clear()

    def on_debug(self, kind, text):
        """the Companion's debug output (worker thread): journaled; a RUN result wakes the request waiting for its job"""
        entry = self.journal.add(kind, text)
        if self.tells is not None:
            self.tells.append((kind, text))
        if kind == "RUN" and entry["job"] is not None:
            waiter = self.pending.pop(entry["job"], None)
            if waiter is not None:
                loop, fut = waiter
                try:
                    loop.call_soon_threadsafe(_resolve, fut, parse_run(text))
                except RuntimeError:          # the loop is gone: nobody waits
                    pass

    def on_log(self, text):
        """the Companion's and the loop's notes (worker thread)"""
        self.journal.add("INFO", text)

    def tracked(self, fn, *args):
        """(fn(*args), what the Companion told meanwhile); runs on the worker thread"""
        self.tells = []
        try:
            return fn(*args), self.tells
        finally:
            self.tells = None

    async def call(self, fn, *args, timeout=WORKER_TIMEOUT):
        """fn(*args) on the worker thread; its result, or its ApiError"""
        if self.closed:
            raise ApiError(503, "stopping", "the daemon is shutting down")
        fut = self.worker.request(fn, *args)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)
        except asyncio.TimeoutError:
            raise ApiError(504, "worker_busy", f"the companion thread did not answer within {timeout:.0f} s") from None
        except RuntimeError as e:              # companion(): the game folder is not known yet
            raise ApiError(503, "no_game_folder", str(e)) from None
        except SystemExit as e:                # core.game.game_dir() found no client folder
            raise ApiError(400, "no_game_folder", str(e)) from None

    @staticmethod
    def tell_error(tells, what):
        """the ApiError behind a None from load / watch: what the Companion said about the target"""
        texts = [t for k, t in tells if k in ("RUN", "WATCH")]
        text = texts[-1] if texts else f"{what} failed"
        if "no such file" in text:
            return ApiError(404, "not_found", text)
        if "refused" in text:
            return ApiError(403, "forbidden", text)
        return ApiError(400, "bad_target", text)

    @staticmethod
    def timeout_seconds(ms, default):
        if ms is None:
            ms = default
        if not isinstance(ms, (int, float)) or isinstance(ms, bool) or not 0 < ms <= MAX_TIMEOUT_MS:
            raise ApiError(400, "bad_request", f"timeout_ms: a number of milliseconds up to {MAX_TIMEOUT_MS}")
        return ms / 1000

    # --- the addons follow the program --------------------------------------------------------------------------------

    def addons_behind(self):
        """the program's addons whose copy in the game folder must be replaced: another version (why "version"), or a
        ## Interface that is not the client's, a game update having changed it (why "interface": the game would list them
        as out of date and not load them): [dict(name, version, why[, interface, want_interface])]; [] while the game
        folder is not known"""
        from ..installer import addons_for
        from ..installer.addons import client_interface, read_toc
        from ..installer.doctor import base_version
        addons = self.worker.addons
        if addons is None:
            return []
        want, behind = base_version(self.version), []
        interface = client_interface(Path(addons).parent.parent)[1]
        for name in addons_for(self.mode):
            toc = read_toc(Path(addons) / name / f"{name}.toc")
            have = toc.get("Version") if toc else None
            if base_version(have) != want:
                behind.append(dict(name=name, version=have, why="version"))
            elif interface and toc.get("Interface") != str(interface):
                behind.append(dict(name=name, version=have, why="interface", interface=toc.get("Interface"),
                                   want_interface=interface))
        return behind

    @staticmethod
    def _behind_text(b, version):
        if b["why"] == "interface":
            return f"{b['name']} Interface {b['interface'] or '?'} -> {b['want_interface']} (the game was updated)"
        return f"{b['name']} {b['version'] or '(missing)'} -> {version}"

    def sync_addons(self):
        """(a background thread, every 30 s) the addons in the game folder carry the program's version: another version
        there is replaced by the program's own copy while the game is not running (the game finds new files only when
        it starts, and a running game would have to be restarted to work with the program again). While it runs,
        status.addons_update says what waits; an install from 自检与修复 does it at once"""
        if self.closed:
            return
        from ..installer.doctor import base_version
        from ..installer.install import install
        behind = self.addons_behind()
        if not behind:
            self.addons_pending = None
            return
        version = base_version(self.version)
        listed = behind
        if getattr(self.worker, "win", None) is not None:
            if (self.addons_pending or {}).get("addons") != listed:
                self.journal.add("INFO", "addons: " + ", ".join(self._behind_text(b, version) for b in behind)
                                 + " once the game is closed (the program brings its addons along)")
            self.addons_pending = dict(version=version, addons=listed, waiting="game")
            return
        game_dir = str(Path(self.worker.addons).parent.parent)

        def do():
            result = install(game_dir=game_dir, clean=True, mode=self.mode)
            if result.get("restart_for_link"):
                self.worker.installed = time.time()        # a game started from now on knows the new files
            return result

        try:
            result = self.worker.request(do).result(timeout=180)
        except Exception as e:                              # no folder, a file in use: try again next time
            if (self.addons_pending or {}).get("error") != str(e):
                self.journal.add("INFO", f"addons: could not bring them to {version}: {e}")
            self.addons_pending = dict(version=version, addons=listed, waiting="error", error=str(e))
            return
        self.addons_pending = None
        self.journal.add("INFO", f"addons: {', '.join(self._behind_text(b, version) for b in behind)}: installed "
                                 f"({len(result.get('installed') or [])} files, Interface {result.get('interface') or '?'})")

    # --- kept versions (agent/history.py) --------------------------------------------------------------------------

    def addon_of(self, path):
        """the addon folder (directly in AddOns) a file or folder is in, or None"""
        try:
            rel = Path(path).resolve().relative_to(Path(self.worker.addons).resolve())
        except (TypeError, ValueError, OSError):
            return None
        return rel.parts[0] if rel.parts else None

    def keep(self, addon, reason, note=""):
        """a version of the addon kept (any thread): its summary, or None when it could not be (journaled; the load
        goes on without it)"""
        addons = self.worker.addons
        if addons is None or not addon:
            return None
        try:
            v = history.save(addons, addon, reason, note)
        except history.TooBig as e:
            if addon.casefold() not in self.history_big:
                self.history_big.add(addon.casefold())
                self.journal.add("INFO", f"history: {e}")
            return None
        except (history.HistoryError, OSError) as e:
            self.journal.add("INFO", f"history: {addon} not kept: {e}")
            return None
        if v["new"]:
            self.journal.add("INFO", f"history: {addon} #{v['id']} kept ({reason}{': ' + note if note else ''})")
        return v

    async def keep_before(self, path, reason):
        """keep the addon a load / watch target is in: {addon, id, new}, or None"""
        addon = self.addon_of(path)
        if addon is None:
            return None
        v = await asyncio.to_thread(self.keep, addon, reason, "" if Path(path).is_dir() else Path(path).name)
        return dict(addon=addon, id=v["id"], new=v["new"]) if v else None

    def rel_name(self, path):
        """a file's name for findings: relative to AddOns when it is in there, else its own name"""
        try:
            return Path(path).resolve().relative_to(Path(self.worker.addons).resolve()).as_posix()
        except (TypeError, ValueError, OSError):
            return Path(path).name

    def watch_saved(self, addon, path):
        """the Companion's hook (worker thread), before a watched file that was saved is loaded again: a syntax error keeps
        it out of the game (WATCH says where; False); else its addon is kept on the history thread and it is loaded"""
        try:
            bad = lint.syntax(path, self.rel_name(path))
        except (OSError, lint.LintError):
            bad = None
        if bad:
            self.journal.add("WATCH", f"{bad['file']}:{bad['line']}: {bad['message']}"
                                      + (f" ({bad['hint']})" if bad["hint"] else "") + ": not loaded, fix it and save again")
            return False
        name = addon or self.addon_of(path)
        if name and not self.closed:
            try:
                self.history_pool.submit(self.keep, name, "save", Path(path).name)
            except RuntimeError:              # shut down meanwhile
                pass
        return True

    async def in_history(self, fn, *args):
        """a history.py operation off the event loop; its HistoryError as an ApiError"""
        try:
            return await asyncio.to_thread(fn, *args)
        except history.HistoryError as e:
            raise ApiError(e.status, e.code, str(e)) from None

    def addons_dir(self):
        addons = self.worker.addons
        if addons is None:
            raise ApiError(503, "no_game_folder", "the game folder is not known yet: start the game, or set it in 设置")
        return Path(addons)

    # --- the operations --------------------------------------------------------------------------------------------

    async def status(self):
        w = self.worker
        comp = w.comp
        # blocked: why the screen is not read ("restart": an install added files the running game does not know, so it
        # has to be restarted; "player": player mode), None while the link is read
        link = dict(state="offline", session=None, slot_next=None, slots_left=None, hb=None, capture=w.capture,
                    ping_p50=None, last_frame=None, addon_version=None, blocked=getattr(w, "blocked", None))
        watch, reload_pending = [], False
        if comp is not None:
            s = comp.sessions.get(comp.current)
            rtts = sorted(comp.rtts[-20:])
            link.update(state="online" if comp.link_up else "offline", session=comp.current, slot_next=comp.slot,
                        slots_left=(MB.POOL - comp.slot + 1) if comp.slot is not None else None, hb=s.hb if s else None,
                        ping_p50=rtts[len(rtts) // 2] if rtts else None, last_frame=comp.last_frame,
                        addon_version=s.version_text if s else None)
            watch = sorted(str(p) for p in comp.watched)
            reload_pending = comp.reload_sent is not None
        exe, args = mcp_command()                     # what starts `wuxian mcp` here; the ui's 接入 page shows it
        return dict(daemon=dict(version=self.version, pid=os.getpid(), uptime=round(time.time() - self.started, 1),
                                mode=self.mode, started=round(self.started, 3), url=self.url,
                                command=[exe, *args[:-1]]),
                    game=w.game_status(), link=link, watch=watch, reload_pending=reload_pending, mcp_url=self.mcp_url,
                    addons_dir=str(w.addons) if w.addons else None,
                    update=self.updater.status() if self.updater is not None else None,
                    content=self.content.status() if self.content is not None else None,
                    addons_update=self.addons_pending)

    async def apidocs(self, q=None, kind=None, limit=20, name=None, manual=None, call=None, lang=None, systems=False,
                      system=None, offset=0):
        """the API manual (apidocs.py): name = one entry; manual = a topic ("" = the list), in English with lang "en"; q = a
        search ({query, results, counts, total, more}; call = ok / limited / protected / usable keeps those; without q, a
        kind or a call lists what they keep; offset pages); systems = the browser's rows, system = one row's entries;
        none = about"""
        def do():
            ix = apidocs.index()
            if systems:
                return dict(systems=ix.systems())
            if system:
                found = ix.system(system)
                if found is None:
                    raise ApiError(404, "not_found", f"{system}: no such namespace, function group or object")
                return found
            if name:
                entry = ix.get(name)
                if entry is None:
                    raise ApiError(404, "not_found", f"{name}: not in the API manual (api_search finds near names)")
                return entry
            if manual is not None:
                topic = ix.manual(manual or None, lang)
                if topic is None:
                    raise ApiError(404, "not_found", f"no manual topic {manual!r}")
                return topic if isinstance(topic, dict) else dict(topics=topic)
            if q or kind or call:
                return dict(query=q or "", **ix.find(q, kind or None, call or None, limit, offset))
            return ix.about()
        if kind not in (None, "", *apidocs.KINDS):
            raise ApiError(400, "bad_request", "kind: function, event or table")
        if call not in (None, "", "usable", *apidocs.CALLS):
            raise ApiError(400, "bad_request", "call: ok, limited, protected or usable")
        return await asyncio.to_thread(do)

    async def reveal(self, name):
        """opens an addon's folder in Explorer (the 插件 page's 打开文件夹): only a folder directly in AddOns"""
        addons = self.worker.addons
        if addons is None:
            raise ApiError(503, "no_game_folder", "the game folder is not known yet")
        folder = Path(addons) / str(name or "")
        if not name or folder.parent != Path(addons) or not folder.is_dir():
            raise ApiError(404, "not_found", f"no addon folder {name!r} in {addons}")
        os.startfile(folder)
        return dict(opened=True, path=str(folder))

    async def new_addon(self, name, title=None, notes="", template="basic"):
        """a new addon from a template (scaffold.py) in the game's AddOns folder"""
        addons = self.worker.addons
        if addons is None:
            raise ApiError(503, "no_game_folder", "the game folder is not known yet: start the game, or set it in 设置")
        try:
            from ..installer.addons import client_interface
            client, interface = client_interface(Path(addons).parent.parent)
            res = await asyncio.to_thread(scaffold.create, addons, name, title, notes or "", template or "basic",
                                          interface or scaffold.INTERFACE, "", client)
        except scaffold.ScaffoldError as e:
            raise ApiError(400, e.code, str(e)) from None          # bad_addon, or addon_exists
        self.journal.add("INFO", f"new addon {name} ({res['template']}) in {res['path']}")
        await asyncio.to_thread(self.keep, name, "new")
        return dict(res, hint=f"`load {name}` hot-loads it now; the game itself lists it after a full restart")

    async def probe(self, code, timeout_ms):
        """a probes.py chunk run in the game; its JSON answer, or the ApiError that says why there is none. The answer
        comes in pieces (probes.answer_chunk): the first with the chunk, the others asked for probes.BATCH at a time"""
        key = secrets.token_hex(4)
        n, first = await self.probe_piece(probes.answer_chunk(code, key), 1, timeout_ms)
        texts = [first]
        for start in range(2, n + 1, probes.BATCH):
            got = await asyncio.gather(*(self.probe_piece(probes.piece_chunk(key, i), i, timeout_ms)
                                         for i in range(start, min(start + probes.BATCH, n + 1))), return_exceptions=True)
            for g in got:
                if isinstance(g, BaseException):
                    raise g
            texts += [text for _, text in got]
        try:
            return probes.parse(["".join(texts)])
        except ValueError as e:
            raise ApiError(400, "probe_failed", str(e)) from None

    async def probe_piece(self, code, i, timeout_ms):
        """(how many pieces the answer has, the text of piece i): one run of a probe's chunk"""
        res = await self.run(code, timeout_ms=timeout_ms, chunk="=probe")
        if not res.get("ok"):
            raise ApiError(502, "lua_error", f"{res.get('error')}\n{res.get('stack') or ''}".strip())
        try:
            return probes.piece(res.get("values"), i)
        except ValueError as e:
            raise ApiError(400, "probe_failed", str(e)) from None

    async def trace(self, seconds=10, events=None, max_events=200, args=6):
        """the events the game fires for `seconds` (probes.py): each kept one with its time, name and first arguments,
        and how often every one fired; events = names or globs (BAG_*), else all an addon may register, the chattiest
        counted only. Registering starts when the chunk arrives (a few seconds for an unfiltered trace)"""
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 1 <= seconds <= 120:
            raise ApiError(400, "bad_request", "seconds: 1..120")
        if not isinstance(max_events, int) or isinstance(max_events, bool) or not 1 <= max_events <= 1000:
            raise ApiError(400, "bad_request", "max_events: 1..1000")
        if not isinstance(args, int) or isinstance(args, bool) or not 0 <= args <= 12:
            raise ApiError(400, "bad_request", "args: 0..12 arguments kept per event")
        try:
            start = probes.trace_start(events, max_events, args)
        except ValueError as e:
            raise ApiError(400, "bad_request", str(e)) from None
        if self.tracing.locked():
            raise ApiError(409, "busy", "a trace is already running")
        async with self.tracing:
            begun = await self.probe(start, 30000)
            await asyncio.sleep(seconds)
            data = await self.probe(probes.trace_stop(), 60000)
        result = probes.trace_result(data, events)
        result.update(registered=begun.get("tracing"), unknown=begun.get("unknown") or [])
        self.journal.add("INFO", f"trace: {result['seconds']} s, {sum(c['count'] for c in result['counts'])} events of "
                                 f"{len(result['counts'])} kinds, {result['kept']} kept" + (f" ({events})" if events else ""))
        return result

    async def inspect(self, target=None, mouse=False, depth=1, snap=False):
        """frames described (probes.py): the one a Lua expression gives, or the ones under the mouse; with snap, a
        picture of the first one's rectangle (and some margin)"""
        if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 3:
            raise ApiError(400, "bad_request", "depth: 0..3 levels of children")
        try:
            code = probes.inspect(target, bool(mouse), depth)
        except ValueError as e:
            raise ApiError(400, "bad_request", str(e)) from None
        data = await self.probe(code, 20000)
        frames = data.get("frames") or []
        result = dict(screen=data.get("screen"), frames=frames)
        rect = frames[0].get("rect") if frames and isinstance(frames[0], dict) else None
        if snap and isinstance(rect, list) and rect[2] > 0 and rect[3] > 0:      # not "<secret>": no place to picture
            sw, sh = (data.get("screen") or [rect[0] + rect[2], rect[1] + rect[3]])[:2]
            x, y = max(0, rect[0] - 12), max(0, rect[1] - 12)
            w, h = min(sw - x, rect[2] + 24), min(sh - y, rect[3] + 24)
            if w > 0 and h > 0:
                result["snap"] = await self.snap([x, y, w, h], None)
        return result

    TRY_KINDS = ("ERR", "WARN", "BLOCKED", "OUT", "RELOAD")

    async def try_(self, code=None, slash=None, seconds=2, addon=None, snap=False, frame=None, events=None,
                   timeout_ms=10000):
        """do something in the game and collect what came of it: the action (Lua `code`, or a `slash` command line run
        through its handler) and its own result, then for `seconds` the Lua errors (with stacks), print output, warnings
        and blocked actions the game reported, the events that fired (`events`: names or globs, "all"), and a picture at
        the end (`snap`: the screen; `frame`: a Lua expression of a frame, pictured with a margin). A last run after the
        wait (the trace's stop, or a marker) is answered only after everything the game sent before it, so the window
        is complete. Everything in it counts: the player's own actions too (`addon` says whose an entry is)"""
        if (code is None) == (slash is None):
            raise ApiError(400, "bad_request", "code (Lua) or slash (a command line such as \"/myaddon show\"), one of them")
        if code is not None and (not isinstance(code, str) or not code.strip()):
            raise ApiError(400, "bad_request", "code: a non-empty string of Lua")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0 <= seconds <= 30:
            raise ApiError(400, "bad_request", "seconds: 0..30, how long to collect after the action")
        if events is not None and (not isinstance(events, str) or not events.strip()):
            raise ApiError(400, "bad_request", 'events: names or globs ("BAG_*, LOOT_OPENED"), or "all"')
        if frame is not None and (not isinstance(frame, str) or not frame.strip()):
            raise ApiError(400, "bad_request", "frame: a Lua expression that gives a frame")
        try:
            lua = probes.slash_call(slash) if slash is not None else code
            start = probes.trace_start(None if events.strip().lower() == "all" else events) if events else None
        except ValueError as e:
            raise ApiError(400, "bad_request", str(e)) from None
        first = self.journal.next_id
        t0 = time.time()
        traced, complete = None, True
        if start is not None:
            if self.tracing.locked():
                raise ApiError(409, "busy", "a trace is already running")
            async with self.tracing:
                begun = await self.probe(start, 30000)
                t0 = time.time()
                action = await self.run(lua, timeout_ms, addon)
                await asyncio.sleep(seconds)
                data = await self.probe(probes.trace_stop(), 60000)       # also the end marker
            traced = probes.trace_result(data, events)
            traced.update(registered=begun.get("tracing"), unknown=begun.get("unknown") or [])
        else:
            action = await self.run(lua, timeout_ms, addon)
            await asyncio.sleep(seconds)
            try:
                await self.run("return true", 15000, chunk="=probe")       # the end marker
            except ApiError:
                complete = False
        entries, _, _ = self.journal.since(first, 1000, list(self.TRY_KINDS))
        out = dict(action=action, seconds=seconds, errors=[], blocked=[], warnings=[], prints=[], notes=[],
                   complete=complete)
        if not action.get("ok"):
            out["errors"].append(dict(t=0.0, message=action.get("error") or "", stack=action.get("stack") or "",
                                      addon=addon, source="action"))
        for e in entries:
            t = round(max(0.0, e["t"] - t0), 2)
            text = e["text"]
            if e["kind"] == "ERR":
                message, _, stack = text.partition("\n")
                out["errors"].append(dict(t=t, message=message, stack=stack, addon=e.get("addon")))
            elif e["kind"] == "BLOCKED":
                out["blocked"].append(dict(t=t, message=text, addon=e.get("addon")))
            elif e["kind"] == "WARN":
                out["warnings"].append(dict(t=t, message=text, addon=e.get("addon")))
            elif e["kind"] == "OUT":
                out["prints"] += [dict(t=t, text=line) for line in text.split("\n")]
            else:
                out["notes"].append(dict(t=t, kind=e["kind"], text=text))
        if traced is not None:
            out["events"] = traced
        if frame:
            try:
                seen = await self.inspect(frame, False, 0, True)
                out["frame"] = (seen.get("frames") or [None])[0]
                if seen.get("snap"):
                    out["snap"] = seen["snap"]
            except ApiError as e:
                out["notes"].append(dict(t=None, kind="SNAP", text=f"frame {frame}: {e.message}"))
        elif snap:
            try:
                out["snap"] = await self.snap(None, 1280)
            except ApiError as e:
                out["notes"].append(dict(t=None, kind="SNAP", text=e.message))
        out["ok"] = bool(action.get("ok")) and not out["errors"] and not out["blocked"]
        out["summary"] = self.try_summary(out, addon)
        self.journal.add("INFO", f"try: {out['summary']}")
        return out

    @staticmethod
    def try_summary(out, addon=None):
        """one line on what came of a try: the action, then the errors, blocked actions and prints of the window"""
        action = out["action"]
        if action.get("ok"):
            values = action.get("values") or []
            head = "the action ran" + (f" (returned {', '.join(map(str, values))[:120]})" if values else "")
        else:
            head = f"the action failed: {(action.get('error') or '').splitlines()[0][:160]}"
        later = [e for e in out["errors"] if e.get("source") != "action"]
        parts = []
        if later:
            mine = [e for e in later if not addon or e.get("addon") in (None, addon)]
            first = (mine or later)[0]["message"][:160]
            parts.append(f"{len(later)} error{'s' if len(later) > 1 else ''} ({first})"
                         + (f", {len(later) - len(mine)} of them from other addons" if addon and len(mine) < len(later) else ""))
        if out["blocked"]:
            parts.append(f"{len(out['blocked'])} blocked ({out['blocked'][0]['message'][:120]})")
        if out["warnings"]:
            parts.append(f"{len(out['warnings'])} warning{'s' if len(out['warnings']) > 1 else ''}")
        parts.append(f"{len(out['prints'])} print line{'s' if len(out['prints']) != 1 else ''}" if out["prints"] else "no prints")
        if not later and not out["blocked"]:
            parts.insert(0, "no errors")
        tail = "" if out["complete"] else " (the end marker got no answer: later output may be missing, see logs)"
        return f"{head}; then in {out['seconds']:g} s: " + ", ".join(parts) + tail

    async def agents(self, action=None, host=None):
        """the coding agents and this MCP server (agents.py): no action = every agent's state; "connect" registers this
        program with one (and checks that the agent can use it), "disconnect" removes it, "verify" checks again"""
        if action in (None, "", "status"):
            return await asyncio.to_thread(agents.status)
        actions = {"connect": agents.connect, "disconnect": agents.disconnect, "verify": agents.verify}
        if action not in actions:
            raise ApiError(400, "bad_request", 'action: "connect", "disconnect" or "verify"')
        if host not in agents.HOSTS:
            raise ApiError(400, "bad_request", f"host: one of {', '.join(agents.HOSTS)}")
        try:
            result = await asyncio.to_thread(actions[action], host)
        except agents.AgentError as e:
            self.journal.add("INFO", f"agents: {action} {host} failed: {e}")
            raise ApiError(409, "agent_failed", str(e)) from None
        check = result.get("verify")
        self.journal.add("INFO", f"agents: {action} {host} -> {result['state']}" + (f" ({check['text']})" if check else ""))
        return result

    async def history(self, addon=None, vid=None, against="now"):
        """kept versions (agent/history.py): no addon = the addons that have some; an addon = its versions, newest
        first, and how its files now compare with the latest one; an addon and a version = what changed from that
        version to the files now (against "now"), from the version before it ("prev"), or to another version"""
        if not addon:
            return dict(addons=await self.in_history(history.summary, True))
        if vid in (None, ""):
            return await self.in_history(history.versions, self.worker.addons, addon)
        if against in (None, "", "now"):
            return await self.in_history(history.diff, self.addons_dir(), addon, vid, "now")
        return await self.in_history(history.diff, self.worker.addons, addon, vid, against)

    def precheck(self, path, files):
        """the findings for a load (worker of asyncio.to_thread): the report of the target (file or addon), without the
        notes; the files that could not be checked (outside an addon folder) only for their syntax"""
        try:
            report = lint.check(path, self.worker.addons)
        except (lint.LintError, OSError) as e:
            return dict(errors=[], warnings=[], unresolved=[], note=str(e))
        return dict(errors=report["errors"], warnings=report["warnings"], unresolved=sorted(report["unresolved"]))

    async def check(self, target, live=True):
        """the whole check of a file or an addon (agent/lint.py): syntax with Lua 5.1, globals and APIs against this
        client, the .toc and the XML; with live and the link online, the names the manual does not settle are asked of
        the running game (nil there: an error)"""
        if not isinstance(target, str) or not target.strip():
            raise ApiError(400, "bad_request", "target: a file, an addon folder or an addon name")
        addons = self.addons_dir()
        path = resolve_target(addons, target)
        if path is None:
            raise ApiError(404, "not_found", f"check {target}: no such file or addon folder")
        try:
            report = await asyncio.to_thread(lint.check, path, addons)
        except lint.LintError as e:
            raise ApiError(400, "bad_target", str(e)) from None
        names = lint.live_names(report)
        if live and names:
            comp = self.worker.comp
            if comp is None or not comp.link_up:
                report["live"] = dict(error="the game is not online: the unresolved names were not asked")
            else:
                try:
                    types = await self.probe(lint.live_chunk(names, probes.LUA_JSON, probes.lua_str), 15000)
                except ApiError as e:
                    report["live"] = dict(error=e.message)
                else:
                    if isinstance(types, dict):
                        lint.apply_live(report, types)
                    else:
                        report["live"] = dict(error=f"the game's answer was not understood: {str(types)[:80]}")
        self.journal.add("INFO", f"check {report['addon'] or target}: {len(report['errors'])} errors, "
                                 f"{len(report['warnings'])} warnings" + (" (asked the game)" if report.get("live", {}).get("checked") else ""))
        return report

    async def checkpoint(self, addon, note=""):
        """a version of the addon's files kept now (the latest one, new false, when nothing changed since)"""
        if note is not None and not isinstance(note, str):
            raise ApiError(400, "bad_request", "note: a string")
        v = await self.in_history(history.save, self.addons_dir(), addon, "manual", note or "")
        if v["new"]:
            self.journal.add("INFO", f"history: {addon} #{v['id']} kept (manual{': ' + note if note else ''})")
        return v

    async def restore(self, addon, vid):
        """the addon's files back as a kept version had them; the files before are kept first (saved)"""
        if vid in (None, ""):
            raise ApiError(400, "bad_request", "id: the number of the version to go back to")
        res = await self.in_history(history.restore, self.addons_dir(), addon, vid)
        self.journal.add("INFO", f"history: {addon} restored to #{res['restored']}: {len(res['written'])} files written, "
                                 f"{len(res['removed'])} removed" + (f"; the files before are #{res['saved']}" if res["saved"] else ""))
        folder = (self.addons_dir() / addon).resolve()
        comp = self.worker.comp
        res["watched"] = comp is not None and any(folder in Path(p).resolve().parents for p in list(comp.watched))
        then = ("the addon is watched, so the files written are loaded into the game again now" if res["watched"] else
                f"`load {addon}` runs them in the game now (frames it made stay), `reload` gives a clean start")
        res["hint"] = (f"the files are as #{res['restored']} had them; {then}"
                       + (f"; restore #{res['saved']} undoes this" if res["saved"] else ""))
        return res

    async def forget(self, addon):
        """every kept version of the addon removed (the App's 清空历史)"""
        res = await self.in_history(history.forget, addon)
        self.journal.add("INFO", f"history: the kept versions of {addon} removed")
        return res

    async def update(self, action=None):
        """the program's updates (updater.py): no action = the state; "check" asks the feed; "download" starts the
        download and answers at once (the state follows in /api/status); "apply" is the server's (it has to stop the
        daemon afterwards)"""
        u = self.updater
        if u is None:
            raise ApiError(503, "starting", "the daemon is still starting")
        if action in (None, "", "status"):
            return u.status()
        if action == "check":
            return await asyncio.to_thread(u.check)
        if action == "download":
            if u.status()["state"] != "available":
                raise ApiError(409, "nothing_to_download", "no update found to download: check first")
            threading.Thread(target=u.download, name="wuxian-update-download", daemon=True).start()
            await asyncio.sleep(0.05)
            return u.status()
        raise ApiError(400, "bad_request", 'action: "check", "download" or "apply"')

    @staticmethod
    def run_result(res):
        if res["ok"]:
            return dict(ok=True, job=res["job"], values=res["values"], ms=res["ms"], chunk=res["chunk"], bytes=res["bytes"],
                        note=res["note"], cut=res["cut"])
        return dict(ok=False, job=res["job"], error=res["error"], stack=res["stack"], chunk=res["chunk"])

    async def run(self, code, timeout_ms=10000, addon=None, chunk="=run"):
        """Lua for the addon to run; waits for the RUN result of that job. chunk: its chunk name, "=run" for the agent's
        and the user's code, "=probe" for this daemon's own (probes, the try's end marker: WoWBridge leaves those out of
        its debug window)"""
        if not isinstance(code, str) or not code.strip():
            raise ApiError(400, "bad_request", "code: a non-empty string of Lua")
        if addon is not None and not isinstance(addon, str):
            raise ApiError(400, "bad_request", "addon: the name of the addon whose namespace the code gets, or null")
        timeout = self.timeout_seconds(timeout_ms, 10000)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        def start():
            comp = self.worker.companion()
            job = comp.code(code.encode("utf-8"), chunk, addon or "-")
            self.pending[job] = (loop, fut)       # on the worker thread, before any RUN result can come
            return job

        job = await self.call(start)
        try:
            res = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(job, None)
            raise ApiError(504, "timeout", f"no result for job {job} within {int(timeout * 1000)} ms: is the game "
                                           f"running with WoWBridge online?", job=job) from None
        return self.run_result(res)

    async def load(self, target, mode=None, reset=True, timeout_ms=30000, check=True):
        """a file, or an addon's Lua files in .toc order, for the addon to run; waits for every job's result. check: the
        files are checked first (agent/lint.py): a syntax error in any of them sends none (422 check_failed), the other
        findings come with the answer (`check`)"""
        if not isinstance(target, str) or not target.strip():
            raise ApiError(400, "bad_request", "target: a file, an addon folder or an addon name")
        if mode not in (None, "file", "addon"):
            raise ApiError(400, "bad_request", 'mode: "file" or "addon"')
        timeout = self.timeout_seconds(timeout_ms, 30000)
        w = self.worker
        if w.comp is None:
            raise ApiError(503, "no_game_folder", "the game folder is not known yet (no game window, no default folder)")
        addons = Path(w.addons)
        path = resolve_target(addons, target)
        if path is None:
            raise ApiError(404, "not_found", f"load {target}: no such file or addon folder")
        if is_own(addons, path):
            raise ApiError(403, "forbidden", f"load {target}: refused, WoWBridge's own files would start a second link "
                                             f"(edit them, wuxian install, then reload)")
        if mode == "addon" and path.is_file():
            raise ApiError(400, "bad_request", f"load {target}: a file, not an addon folder")
        if mode == "file" and path.is_dir():
            raise ApiError(400, "bad_request", f"load {target}: a folder, not a file")
        if path.is_file():
            files, addon = [path], None
        else:
            toc = next((t for t in [path / f"{path.name}.toc"] + sorted(path.glob("*.toc")) if t.is_file()), None)
            if toc is None:
                raise ApiError(400, "no_toc", f"load {target}: no .toc in that folder")
            files, addon = toc_files(toc)[0], path.name
        names = [str(f) for f in files]
        found = None
        if check:
            found = await asyncio.to_thread(self.precheck, path, files)
            if found["errors"] and any(f["code"] == "syntax" for f in found["errors"]):
                first = next(f for f in found["errors"] if f["code"] == "syntax")
                raise ApiError(422, "check_failed", f"load {target}: not sent, {first['file']}:{first['line']}: "
                                                    f"{first['message']}" + (f" ({first['hint']})" if first["hint"] else ""),
                               check=found)
        kept = await self.keep_before(path, "load")
        loop = asyncio.get_running_loop()
        futs = [loop.create_future() for _ in files]

        def start():
            comp = w.companion()
            jobs = call_load(comp, target, reset)
            if jobs is None:
                return None
            while len(futs) < len(jobs):           # the Companion found more files than we did: wait for those too
                futs.append(loop.create_future())
            for job, fut in zip(jobs, futs):
                if job is None:                    # the file could not be read: the Companion told why
                    loop.call_soon_threadsafe(_resolve, fut, dict(ok=False, job=None, chunk=None, ms=None,
                                                                  error="the file could not be read", stack=""))
                else:
                    self.pending[job] = (loop, fut)
            return jobs

        jobs, tells = await self.call(self.tracked, start)
        if jobs is None:
            raise self.tell_error(tells, f"load {target}")
        deadline = loop.time() + timeout
        results, done = [], 0
        for i, (job, fut) in enumerate(zip(jobs, futs)):
            name = names[i] if i < len(names) else None
            try:
                res = await asyncio.wait_for(fut, max(0.0, deadline - loop.time()))
            except asyncio.TimeoutError:
                self.pending.pop(job, None)
                results.append(dict(file=name, job=job, ok=False, error="timeout", stack=None, ms=None))
                continue
            done += 1
            results.append(dict(file=name or chunk_file(res.get("chunk") or ""), job=job, ok=res["ok"],
                                error=res.get("error"), stack=res.get("stack"), ms=res.get("ms"), values=res.get("values")))
        body = dict(files=results, addon=addon, ok=all(r["ok"] for r in results), reset=reset, kept=kept, check=found)
        if results and not done:
            raise ApiError(504, "timeout", f"no result for {len(results)} file(s) within {int(timeout * 1000)} ms: is the "
                                           f"game running with WoWBridge online?", files=results, addon=addon)
        return body

    async def watch(self, action="list", target=None):
        if action not in ("start", "stop", "list"):
            raise ApiError(400, "bad_request", 'action: "start", "stop" or "list"')
        if action == "start" and (not isinstance(target, str) or not target.strip()):
            raise ApiError(400, "bad_request", "target: a file, an addon folder or an addon name to watch")

        def do():
            comp = self.worker.companion()
            if action == "start":
                if comp.watch(target) is None:
                    return None
            elif action == "stop":
                comp.unwatch(target or None)
            return sorted(str(p) for p in comp.watched)

        kept = None
        if action == "start" and self.worker.addons is not None:
            path = resolve_target(Path(self.worker.addons), target)
            if path is not None and not is_own(Path(self.worker.addons), path):
                kept = await self.keep_before(path, "watch")       # the files before the agent edits them
        watched, tells = await self.call(self.tracked, do)
        if watched is None:
            raise self.tell_error(tells, f"watch {target}")
        return dict(watched=watched, kept=kept) if action == "start" else dict(watched=watched)

    async def snap(self, region=None, max_width=1280):
        if region is not None:
            if not (isinstance(region, (list, tuple)) and len(region) == 4
                    and all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in region)):
                raise ApiError(400, "bad_request", "region: [x, y, w, h] in client pixels, or null for the whole client area")
            region = list(region)
        if max_width is not None and not (isinstance(max_width, int) and not isinstance(max_width, bool) and 16 <= max_width <= 8192):
            raise ApiError(400, "bad_request", "max_width: 16..8192 pixels, or null to keep the size")

        def do():
            w = self.worker
            if w.win is None:
                raise ApiError(409, "no_game", "no game window: nothing to snap")
            if w.win.minimized:
                raise ApiError(409, "minimized", "the game window is minimized: nothing is drawn")
            try:
                path, width, height = snap_image(w.win, w.wgc, region, max_width)
            except SnapError as e:
                raise ApiError(409, "snap_failed", str(e)) from None
            self.journal.add("SNAP", f"{path} {width}x{height}" + ("" if w.wgc is not None else " (GDI: anything over the game is in it)"))
            return dict(path=str(path), width=width, height=height, url=f"/api/snaps/{path.name}")

        return await self.call(do)

    async def snap_bytes(self, result):
        """the PNG of a snap() result (the MCP tool returns it as an image block)"""
        return await asyncio.to_thread(Path(result["path"]).read_bytes)

    async def reload(self, reason=""):
        if reason is None:
            reason = ""
        if not isinstance(reason, str):
            raise ApiError(400, "bad_request", "reason: a string")

        def do():
            comp = self.worker.companion()
            n = len(comp.outbox)
            comp.command("reload")                 # a nonce is appended to the command; the addon asks the user once
            nonce = next((int(d[7:]) for k, d in comp.outbox[n:] if k == MB.COMMAND and d.startswith(b"reload ")), None)
            self.journal.add("RELOAD", "requested" + (f": {reason}" if reason else "") +
                             " (the addon shows a button; the UI reloads when the user clicks it)")
            return dict(requested=True, nonce=nonce)

        return await self.call(do)

    async def say(self, text):
        if not isinstance(text, str) or not text:
            raise ApiError(400, "bad_request", "text: what to show in the game's chat")

        def do():
            self.worker.companion().say(text)
            self.journal.add("INFO", f"say: {text}")
            return dict(queued=True)

        return await self.call(do)

    async def logs(self, since=0, limit=200, kinds=None):
        try:
            since, limit = int(since or 0), int(limit or 200)
        except (TypeError, ValueError):
            raise ApiError(400, "bad_request", "since and limit: integers") from None
        if isinstance(kinds, str):
            kinds = [k.strip().upper() for k in kinds.split(",") if k.strip()]
        entries, next_id, truncated = self.journal.since(max(since, 0), max(1, min(limit, 1000)), kinds or None)
        return dict(entries=entries, next=next_id, truncated=truncated)

    async def doctor(self, game_dir=None):
        """the installer's self-check (installer.doctor.run_checks), a placeholder while that module is not there"""
        try:
            from ..installer.doctor import run_checks
        except ImportError as e:
            return dict(checks=[dict(id="doctor", title="self-check", ok=False, fix=None,
                                     detail=f"the self-check module (installer.doctor) is not available yet: {e}")],
                        ok=False, available=False)
        checks = [dict(c) for c in await asyncio.to_thread(run_checks, game_dir=game_dir or self.game_dir, mode=self.mode)]
        # ok is three-valued per check: True, False, or None when it could not be checked (unknown does not fail)
        return dict(checks=checks, ok=not any(c.get("ok") is False for c in checks),
                    unknown=sum(1 for c in checks if c.get("ok") is None), available=True)

    async def fix(self, fix, game_dir=None):
        """run one fix the doctor named: the addon ones are a clean install for this mode; install_webview2 /
        install_dotnet download Microsoft's installer, check its signature and start it (it carries on in its own
        window: check again when it is done)"""
        from ..installer import doctor, runtimes
        if fix in (doctor.FIX_INSTALL, doctor.FIX_INSTALL_CLEAN, doctor.FIX_CREATE_MAIL):
            return dict(fix=fix, install=await self.install(game_dir, clean=True))
        if fix not in doctor.RUNTIME_FIXES:
            raise ApiError(400, "bad_request", f"fix: one of {', '.join([doctor.FIX_INSTALL, doctor.FIX_INSTALL_CLEAN, doctor.FIX_CREATE_MAIL, *doctor.RUNTIME_FIXES])}")
        name = doctor.RUNTIME_FIXES[fix]
        try:
            started = await asyncio.to_thread(runtimes.install, name)
        except runtimes.InstallError as e:
            self.journal.add("INFO", f"{fix}: {e}")
            raise ApiError(502, "install_failed", str(e)) from None
        self.journal.add("INFO", f"{fix}: started {started['path']} (pid {started['pid']})")
        return dict(fix=fix, started=True, title=runtimes.RUNTIMES[name].label(), path=started["path"])

    async def addons(self, game_dir=None):
        """the addons in the client's AddOns folder with their .toc, AddOns.txt state and collected errors
        (installer.addons.list_addons)"""
        from ..installer.addons import list_addons
        try:
            res = await asyncio.to_thread(list_addons, game_dir or self.game_dir)
        except FileNotFoundError as e:
            raise ApiError(404, "no_game_folder", str(e)) from None
        try:
            kept = {r["addon"].casefold(): r for r in await asyncio.to_thread(history.summary)}
        except OSError:
            kept = {}
        for a in res.get("addons") or []:
            r = kept.get(str(a.get("name", "")).casefold())
            a["history"] = dict(versions=r["versions"], latest=r["latest"], latest_id=r["latest_id"]) if r else None
        return res

    async def errors(self, addon=None, limit=100, game_dir=None):
        """the errors !WuxianWorkshop collected (its SavedVariables, as of the last logout or /reload), newest first;
        only those of `addon` when given"""
        from ..installer.addons import list_errors
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise ApiError(400, "bad_request", "limit: an integer") from None
        try:
            return await asyncio.to_thread(list_errors, game_dir or self.game_dir, addon or None, max(1, min(limit, 500)))
        except FileNotFoundError as e:
            raise ApiError(404, "no_game_folder", str(e)) from None

    async def install(self, game_dir=None, clean=True, mode=None):
        """the installer (installer.install.install) for this daemon's mode (or the one given), run on the worker
        thread so that the mailbox is not written meanwhile; a placeholder while that function is not there"""
        try:
            from ..installer.install import install
        except ImportError as e:
            return dict(installed=[], removed=[], restart_required=False, addons_dir=None, available=False,
                        detail=f"the installer (installer.install.install) is not available yet: {e}")
        mode = mode or self.mode
        if mode not in ("player", "developer"):
            raise ApiError(400, "bad_request", 'mode: "player" or "developer"')

        def do():
            # while the game runs, player mode leaves WoWBridge in place: removing it changes nothing in the running
            # game (its Lua is loaded), and putting it back would be files that game does not know, so a switch back
            # to developer mode would need a restart of the game. It goes at an install while the game is closed.
            keep = mode == "player" and getattr(self.worker, "win", None) is not None
            result = dict(install(game_dir=game_dir or self.game_dir, clean=bool(clean), mode=mode, keep_developer=keep))
            if result.get("restart_for_link"):
                self.worker.installed = time.time()   # the loop waits for a game started after the install
            return result

        result = await self.call(do, timeout=180)
        result.setdefault("available", True)
        self.journal.add("INFO", f"install ({mode}): {len(result.get('installed') or [])} installed, "
                                 f"{len(result.get('removed') or [])} removed"
                                 + (", the game has to be restarted" if result.get("restart_for_link") else
                                    ", a restart of the game shows it" if result.get("restart_required") else ""))
        return result

    def stored_settings(self):
        try:
            data = json.loads(settings_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    async def settings(self, update=None):
        """GET: the settings; POST: change some (mode, capture, game_dir, autostart, language, onboarded: the 开始 page's
        steps were done or put off) and keep them. A change of
        mode also installs what that mode needs (developer: WoWBridge and its mailbox; player: removes them); the
        answer then carries that install's result as `install`"""
        stored = self.stored_settings()
        mode_before = self.mode
        if update:
            if not isinstance(update, dict):
                raise ApiError(400, "bad_request", "a JSON object of settings")
            if "capture" in update and update["capture"] not in ("gdi", "wgc"):
                raise ApiError(400, "bad_request", 'capture: "gdi" or "wgc"')
            if "mode" in update and update["mode"] not in ("player", "developer"):
                raise ApiError(400, "bad_request", 'mode: "player" or "developer"')
            if "language" in update and update["language"] not in ("auto", "zh-CN", "en"):
                raise ApiError(400, "bad_request", 'language: "auto", "zh-CN" or "en"')
            if "game_dir" in update and update["game_dir"] is not None and not isinstance(update["game_dir"], str):
                raise ApiError(400, "bad_request", "game_dir: the client folder, or null")
            if "capture" in update:
                self.worker.capture_wanted = update["capture"]
            if "mode" in update:
                self.mode = update["mode"]
                self.worker.link = update["mode"] == "developer"
            if "game_dir" in update:
                self.game_dir = update["game_dir"] or None
            for key in ("autostart", "language", "onboarded"):
                if key in update:
                    stored[key] = update[key]
            if "language" in update:                     # what the program says from now on (i18n.py)
                i18n.set_language(update["language"])
            stored.update(mode=self.mode, capture=self.worker.capture_wanted, game_dir=self.game_dir)
            path = settings_file()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
            self.journal.add("INFO", "settings: " + ", ".join(f"{k}={v}" for k, v in update.items()))
        answer = dict(mode=self.mode, capture=self.worker.capture_wanted, capture_in_use=self.worker.capture,
                      game_dir=self.game_dir, autostart=bool(stored.get("autostart", False)),
                      language=stored.get("language", "auto"), onboarded=bool(stored.get("onboarded", False)))
        if update and self.mode != mode_before:
            try:
                answer["install"] = await self.install()
            except (ApiError, FileNotFoundError, ValueError) as e:     # no client folder yet: the setting still holds
                answer["install"] = dict(available=False, detail=getattr(e, "message", None) or str(e))
        return answer
