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
Local extensions (local/host.py) are WuxianKit's extensions on this computer: processes that speak JSON-RPC over stdio,
registered, started on their first call, checked, stopped and their output read here, over the same HTTP and MCP; their
capabilities are called with local_call (which also calls WuxianKit's see / keep / point capabilities in the game), their
sources' items go into the journal as events of the addon "local" (topic <extension>.<source>; local.changed when the
extensions changed). Their apps are kept the same in WuxianKit as their manifests say (reconcile_local_apps: after every
kit.ready and whenever the extensions change, kit.app.declare for what is new or different, kit.app.forget for what is
gone; before that WuxianKit is told which extensions with a local part are added here, kit.local.set, for its window
to list them: tell_roster). Reactions (local/reactions.py) call a capability when an item is heard: a game source through the temporary app
"reactions" (kit.app.open), a local source directly; an app's own (its manifest's reactions) are there while it runs.
The wakes of an app that local extensions add to (their manifest's adds.<app>.wake) go into the journal with those
extensions' notes, in the order they came with the app's kit.app events (daemon/notes.py).
"""
import asyncio
import fnmatch
import inspect
import itertools
import json
import math
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import __version__, agents, apidocs, content, extensions, i18n, scaffold
from ..local import manifest as local_manifest
from ..local import summary as local_summary
from ..local.host import Host as LocalHost, call_error
from ..local.reactions import ReactionError, Reactions
from ..agent import history, lint, probes
from ..agent.commands import toc_files
from ..agent.snap import SnapError, capture, in_picture, link_frame, save_snap
from ..cli.mcpconfig import mcp_command
from ..core import mailbox as MB
from ..core.game import isolated
from ..paths import settings_file
from ..updater import Scheduler, Updater
from .api import ApiError, chunk_file, parse_run
from .journal import REQUEST_ID
from .kitcache import KIT, SHARED, KitCache
from .notes import REASONS, Notes

OWN_READS = ("kit.revision", "kit.manifest", "kit.capabilities", "kit.apps")   # what WuxianKit's views read of it for themselves

WORKER_TIMEOUT = 30.0        # seconds a request may wait for the worker thread (a WGC snapshot may hold it for one)
MAX_TIMEOUT_MS = 600_000
FONT_SCALES = (90, 100, 110, 125, 150)   # the settings' font_scale: the page's size in per cent (app.js applyScale)
# the settings' extension_approval, who approves a local extension's command before its first run: "ask" the player
# (once for each command, in the App), "auto" the workshop by itself (for developing extensions)
EXTENSION_APPROVALS = ("ask", "auto")
MAX_TOPICS = 16              # topics one ask of addon_events may name (any one matches)
PIECE_TRIES = 3              # times a piece of a long answer is asked for when the link lost it (a timeout, link_down)
PIECE_WAIT = 90.0            # seconds a piece to ask for again waits for the link to come back
PIECE_KEPT = 280.0           # seconds after its first piece that the game still keeps an answer (it keeps 300)
POUR_ASK = 20.0              # seconds with the link up and no new segment of a pour before the missing ones are asked
                             # for again (the link brings a lost frame again by itself within seconds: this is for a
                             # segment lost whole), then again after twice that ...
POUR_QUIET = 60.0            # ... and before the answer is given up
POUR_NOTE = 10.0             # seconds between the journal's notes on how far a long answer has come
POUR_POLL = 0.25             # seconds between a pour's looks at how far it is (a segment that comes wakes it at once)
_now = time.monotonic        # the clock of a pour's waits (a test's mock client puts the game's, and POUR_POLL 0)
ABSENT = "absent"            # declare(): the game's WuxianKit has no kit.app.declare
LOCAL = "local"              # the addon name of the local extensions' events in the journal
REACTIONS_APP = "reactions"  # the temporary app through which the reactions hear the game
REACTIONS_MINUTES = 720
ALLOWED_KINDS = ("see", "keep", "point")   # what local_call calls (say and do are proposals: a later step)
MAX_CHAIN = 4                # hops of a call's caller.chain: one more is a loop (capabilities calling each other)
CALL_TIMEOUT = 30.0          # seconds a capability has that declares no timeout (and a call of the game's)
WAIT_MAX = 600.0             # seconds extension_wait waits at most
WAIT_STEP = 2.0              # seconds between its looks at the extension's row


def short_list(numbers, most=12):
    """a list of numbers for a note: the first `most`, and how many more"""
    text = ", ".join(map(str, numbers[:most]))
    return text + (f" and {len(numbers) - most} more" if len(numbers) > most else "")


class Pouring:
    """the segments of one pour as they come: the worker thread hands each to add() through the loop"""

    def __init__(self, session):
        self.session, self.loop = session, asyncio.get_running_loop()
        self.got = {}                    # segment number -> its bytes
        self.m = self.size = None        # how many segments there are, and their bytes together (the game's answer)
        self.last = _now()               # when the latest new one came (or the link came back)
        self.came = asyncio.Event()

    def add(self, sid, i, m, data):
        if sid != self.session or (self.m is not None and m != self.m) or i in self.got:
            return
        self.m, self.got[i], self.last = m, data, _now()
        self.came.set()

    def complete(self):
        return self.m is not None and len(self.got) >= self.m

    def missing(self):
        """the numbers of the segments not in yet; None while it is not known how many there are"""
        return None if self.m is None else [i for i in range(1, self.m + 1) if i not in self.got]

    def bytes(self):
        return sum(map(len, self.got.values()))


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


def _seen(fut):
    """(a done callback) a Future nobody may await any more: its exception counts as seen"""
    if not fut.cancelled():
        fut.exception()


class Service:
    def __init__(self, worker_factory, *, journal, mode="developer", game_dir=None, version=__version__, updater=None,
                 sync_addons=False, extension_approval="ask"):
        self.journal, self.mode, self.game_dir, self.version = journal, mode, game_dir, version
        # who approves a local extension's command (EXTENSION_APPROVALS): "ask" the player, once for each command, or
        # "auto", the workshop by itself (for developing extensions). The mode has no say in it
        self.extension_approval = extension_approval if extension_approval in EXTENSION_APPROVALS else "ask"
        self.sync_addons_on = sync_addons     # the real daemon: the game folder's addons follow the program (sync_addons)
        self.addon_sync, self.addons_pending = None, None
        self.updater, self.update_checks = updater, None      # updater.Updater; made in start() unless given
        self.content, self.content_checks = None, None        # content.ContentUpdater: newer content packs
        self.ext_catalog = extensions.Catalog(store=extensions.catalog_store())   # the 扩展 page's, read when it looks
        self.mcp_reports = {}        # pid -> what a stdio MCP server last reported (mcp_report): the 扩展 page's Agent 接入
        self.local_mcp = None        # the daemon's own /mcp server (set by the server): its connections, its WuxianKit tools
        self.mcp_lazy = None         # the server.LazyMcp that builds it after the daemon listens (None: no MCP)
        self.kit_view = None         # mcp.kit.KitTools of the page, made when it first looks
        self.kit_cache = KitCache(self.ui_session)   # what every view of WuxianKit asks, asked of the game once
        self.started = time.time()
        self.pending = {}            # job id -> (asyncio loop, future) of a run / load waiting for its RUN result
        self.describing = {}         # (UI session, addon, brief) -> the addon_api answer on its way: callers share it
        self.pours = {}              # key -> the Pouring of a long answer whose segments come (pour)
        self.tells = None            # (kind, text) the Companion reported during the worker call in progress
        self.worker = worker_factory(on_debug=self.on_debug, log=self.on_log)
        self.thread = None
        self.url = self.mcp_url = None        # set by the server once the port is known
        self.closed = False
        self.tracing = asyncio.Lock()         # one trace at a time: they share the game-side frame
        self.history_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wuxian-history")
        self.history_big = set()             # addons too big to keep: said once
        self.local = LocalHost(note=lambda text: self.journal.add("INFO", text), auto=lambda: self.extension_approval == "auto",
                               on_event=self.local_event,
                               on_call=self.extension_calls, on_change=self.local_changed,
                               taken=self.kit_extension_ids, kit_version=self.kit_version_known,
                               addons_dir=self.game_addons_known,
                               interface=self.client_interface_known,
                               game_side=self.game_side_known)   # local/host.py
        self.reactor = Reactions(self.reaction_call, self.reactions_hear, self.cap_kind,
                                   note=lambda text: self.journal.add("INFO", text),
                                   local_sources=lambda: set(self.local.ids()))
        self.notes = Notes(lambda app: self.local.adds(app), self.local_call, self.wake_heard, self.soon,
                           control=lambda app, cmd: self.local.control_call(app, cmd), state=self.extension_state,
                           note=lambda text: self.journal.add("INFO", text))   # the notes on the apps' wakes
        self.reaction_hears = []             # the game sources the reactions hear (their temporary app's hear)
        self.reactions_open = False          # that app was opened in this UI session
        self.reactions_owed = False          # what they hear changed while the game could not be told
        self.loop = None                     # the server's asyncio loop (set before start()): what kit events start
        self.declaring = None                # an asyncio.Lock: one round of reconciling the local apps at a time
        self.app_memory = {}                 # app -> (our declaration, WuxianKit's of it) as JSON when they last agreed
        self.roster_told = None              # (UI session, the roster as JSON) WuxianKit was last told (tell_roster)
        self.reacting = None                 # an asyncio.Lock: the apps' own reactions are put and taken one at a time
        if self.worker is not None:
            self.worker.before_load = self.watch_saved   # the saves of a watch keep a version first
            self.worker.on_session = self.session_started
            self.worker.on_up = self.link_came_up
            self.worker.on_pour = self.on_pour      # the segments of a pour (worker thread)

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
        self.local.autostart()               # the local extensions that start with 无限工坊 (start "workshop")

    def close(self):
        """stop the worker; whoever waits for a job hears that the daemon is going"""
        self.closed = True
        self.notes.close()                   # the wakes still held for their notes go into the journal
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
        self.local.close()                   # the local extensions get shutdown; their trees end after 5 s
        self.journal.close()

    def on_debug(self, kind, text):
        """the Companion's debug output (worker thread): journaled; a RUN result wakes the request waiting for its job;
        WuxianKit's events tell what is known of it (kitcache); the wakes of an app that local extensions add to wait
        for their notes, and its kit.app events behind them (daemon/notes.py: then wake_heard)"""
        if self.tells is not None:
            self.tells.append((kind, text))
        if kind == "EVENT" and self.notes.hold(text):
            return
        entry = self.journal.add(kind, text)
        if kind == "EVENT" and entry.get("addon") == KIT:
            self.kit_cache.heard(entry.get("topic"), entry.get("data"))
            self.kit_event(entry.get("topic"), entry.get("data"))
        if kind == "RUN" and entry["job"] is not None:
            waiter = self.pending.pop(entry["job"], None)
            if waiter is not None:
                loop, fut = waiter
                try:
                    loop.call_soon_threadsafe(_resolve, fut, parse_run(text))
                except RuntimeError:          # the loop is gone: nobody waits
                    pass

    def wake_heard(self, text):
        """(any thread) an event the notes held (an app's wake with its notes, its kit.app event): journaled, and
        heard as on_debug hears WuxianKit's events"""
        entry = self.journal.add("EVENT", text)
        if entry.get("addon") == KIT:
            self.kit_cache.heard(entry.get("topic"), entry.get("data"))
            self.kit_event(entry.get("topic"), entry.get("data"))

    def extension_state(self, eid):
        """(any thread) a local extension's state now (off, stopped, starting, running, ...), None when not there"""
        try:
            return self.local.get(eid)["state"]
        except ApiError:
            return None

    def on_log(self, text):
        """the Companion's and the loop's notes (worker thread)"""
        self.journal.add("INFO", text)

    def ui_session(self):
        """the game's UI session now (the session of the latest frame), or None"""
        return getattr(getattr(self.worker, "comp", None), "current", None)

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
        # has to be restarted; "missing": the running game's folder has no WoWBridge yet, installed once the game is
        # closed; "player": player mode), None while the link is read
        link = dict(state="offline", session=None, slot_next=None, slots_left=None, hb=None, capture=w.capture,
                    ping_p50=None, last_frame=None, addon_version=None, blocked=getattr(w, "blocked", None), pour=False)
        watch, reload_pending = [], False
        if comp is not None:
            s = comp.sessions.get(comp.current)
            rtts = sorted(comp.rtts[-20:])
            link.update(state="online" if comp.link_up else "offline", session=comp.current, slot_next=comp.slot,
                        slots_left=(MB.POOL - comp.slot + 1) if comp.slot is not None else None, hb=s.hb if s else None,
                        ping_p50=rtts[len(rtts) // 2] if rtts else None, last_frame=comp.last_frame,
                        addon_version=s.version_text if s else None, pour=self.can_pour(),
                        slot_rate=round(comp.slot_rate(time.time(), s) * 60, 2) if comp.slot is not None else None)
            watch = sorted(str(p) for p in list(comp.watched))      # the worker thread may change it meanwhile
            reload_pending = comp.reload_pending()
        exe, args = mcp_command()                     # what starts `wuxian mcp` here; the ui's 接入 page shows it
        return dict(daemon=dict(version=self.version, pid=os.getpid(), uptime=round(time.time() - self.started, 1),
                                mode=self.mode, extension_approval=self.extension_approval,
                                started=round(self.started, 3), url=self.url,
                                command=[exe, *args[:-1]], isolated=isolated()),
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

    async def probe(self, code, timeout_ms, chunk="=probe"):
        """a probes.py chunk run in the game; its JSON answer, or the ApiError that says why there is none. The answer
        comes in pieces (probes.answer_chunk): the first with the chunk (under that chunk name: "=call" shows in the game's
        debug window, "=probe" does not), the rest as whole_text brings it"""
        key = secrets.token_hex(4)
        res = await self.run(probes.answer_chunk(code, key), timeout_ms=timeout_ms, chunk=chunk)
        return self.answer_json(await self.whole_text(
            res, lambda i: self.run(probes.piece_chunk(key, i), timeout_ms=timeout_ms, chunk="=probe"),
            lambda: (key, True), timeout_ms, "probe", refresh=True))

    async def data_probe(self, verb, payload, timeout_ms, what):
        """a data call (agent/commands.py data_call: what the agent asks of an addon's API, sent as data) answered in
        pieces as a probe is; its JSON answer, or the ApiError that says why there is none"""
        job, res = await self.data(verb, payload, timeout_ms, what)
        return self.answer_json(await self.whole_text(
            res, lambda i: self.data_result("piece", {"k": str(job), "i": i}, timeout_ms, what),
            lambda: (str(job), False), timeout_ms, what))

    async def run_whole(self, code, timeout_ms=10000, addon=None):
        """run, with the whole of what the code returned first (as tostring makes it, up to probes.WHOLE_MOST bytes)
        brought back however long it is: {ok, job, values: [the text], ms, chunk, bytes, note, cut: None, whole: True},
        or run's error result. timeout_ms is for the run's own result; after it the rest comes as long as segments (or
        pieces) keep coming (whole_text)"""
        if not isinstance(code, str) or not code.strip():
            raise ApiError(400, "bad_request", "code: a non-empty string of Lua")
        key = secrets.token_hex(4)
        res = await self.run(probes.answer_chunk(code, key, whole=True), timeout_ms, addon, "=run")
        if not res["ok"]:
            return res
        text = await self.whole_text(
            res, lambda i: self.run(probes.piece_chunk(key, i), timeout_ms=timeout_ms, chunk="=probe"),
            lambda: (key, True), timeout_ms, "run", refresh=True)
        return dict(res, values=[text], cut=None, whole=True)

    @staticmethod
    def answer_json(text):
        """the JSON a probe's or data call's whole answer is; ApiError probe_failed (saying where it breaks) when not"""
        try:
            return probes.parse([text])
        except ValueError as e:
            raise ApiError(400, "probe_failed", str(e)) from None

    async def whole_text(self, res, more, pour, timeout_ms, what, refresh=False):
        """the whole text of an answer that comes in pieces: res is the call's own RUN result, piece 1 of n (the call
        itself, never asked for again: a call may change something). The rest is poured (pour(): its key and whether a
        probe keeps it) from a WoWBridge that pours (link features 4), else asked for piece by piece (more(i): piece i,
        window). refresh: the game keeps the answer 5 minutes from the latest piece asked for (probes.PIECE_BODY), not
        from the first"""
        n, text = self.piece(res, 1)
        if n == 1:
            return text
        session = self.ui_session()
        if self.can_pour():
            key, kept = pour()
            return text + (await self.pour(key, kept, session, timeout_ms, what)).decode("utf-8", "replace")
        return text + await self.window(n, more, session, refresh, what)

    def can_pour(self):
        """whether the addon in the game pours the rest of a long answer (link features POUR, WoWBridge 0.9.13 on)"""
        comp = getattr(self.worker, "comp", None)
        try:
            return comp is not None and comp.pours()
        except AttributeError:
            return False

    def link_is_up(self):
        return getattr(getattr(self.worker, "comp", None), "link_up", False)

    def on_pour(self, sid, key, i, m, data):
        """(worker thread) a segment of a pour: to the request that waits for it, if one does"""
        p = self.pours.get(key)
        if p is not None:
            try:
                p.loop.call_soon_threadsafe(p.add, sid, i, m, data)
            except RuntimeError:              # the loop is gone: nobody waits
                pass

    async def pour(self, key, kept, session, timeout_ms, what):
        """the rest of a long answer, all that follows its first piece, from a WoWBridge that pours it: one data call
        "pour" and the game sends every segment up (TYPE_POUR, transport/link.py on_pour), joined here in order; their
        bytes. The link brings a frame it lost again by itself; a segment that does not come all the same (lost whole)
        is asked for again (only) after POUR_ASK seconds with the link up and nothing new, once more after twice that.
        With the link down (minimized, a loading screen) it waits for the link, PIECE_WAIT seconds at most, and goes on
        when it is back; another UI session (a reload took the answer), or POUR_QUIET seconds with the link up and no new
        segment, is an error"""
        p = self.pours[key] = Pouring(session)       # before the call: a segment may come before its RUN result
        t0 = noted = _now()
        only, due, asked, last = None, True, 0, p.last   # what to ask for next (None: all), whether now, asks since last
        try:
            while not p.complete():
                if self.ui_session() != session:
                    raise ApiError(502, "lua_error", f"the rest of this answer is no longer kept in the game (the UI "
                                                     f"reloaded: session {session} -> {self.ui_session()})")
                if not self.link_is_up():
                    if not await self.link_back(math.inf, session):
                        if self.ui_session() != session:
                            continue
                        raise ApiError(409, "link_down", f"{what}: no frames came from the game for {PIECE_WAIT:.0f} s "
                                                         f"with {len(p.got)} of {p.m or '?'} segments of its answer in")
                    p.last = _now()            # back: the game goes on with it, quiet counts from now
                if due:
                    due = False
                    if await self.ask_pour(p, key, kept, only, timeout_ms, what) == "link_down":
                        due = True                       # asked once the link is back
                    continue
                if p.last != last:
                    asked, last = 0, p.last              # something new came: the asks start over
                quiet = _now() - p.last
                if quiet >= POUR_QUIET:
                    raise ApiError(504, "timeout", f"{what}: no segment of its answer came for {POUR_QUIET:.0f} s "
                                                   f"with the link up ({len(p.got)} of {p.m or '?'} in)")
                if quiet >= POUR_ASK * (asked + 1):
                    asked, only, due = asked + 1, p.missing(), True
                    self.journal.add("INFO", f"{what}: " + (f"segments {short_list(only)} of its answer asked for again"
                                                            if only else "its answer asked for again")
                                     + f" ({quiet:.0f} s without a new one)")
                    continue
                if _now() - noted >= POUR_NOTE:
                    noted = _now()
                    self.journal.add("INFO", f"{what}: segment {len(p.got)} of {p.m or '?'} of its answer, "
                                             f"{p.bytes() / 1024:,.0f} KB in {noted - t0:.1f} s")
                try:
                    await asyncio.wait_for(p.came.wait(), POUR_POLL)
                except asyncio.TimeoutError:
                    pass
                p.came.clear()
        finally:
            self.pours.pop(key, None)
        data = b"".join(p.got[i] for i in range(1, p.m + 1))
        if p.size is not None and len(data) != p.size:
            raise ApiError(502, "pour_failed", f"{what}: the {p.m} segments of its answer hold {len(data)} bytes, the "
                                               f"game said {p.size}")
        self.journal.add("INFO", f"{what}: its answer poured, {p.m} segments, {len(data) / 1024:,.0f} KB in "
                                 f"{_now() - t0:.1f} s")
        return data

    async def ask_pour(self, p, key, kept, only, timeout_ms, what):
        """the data call "pour" (only: those segments alone): None once the game took it, "timeout" or "link_down" when
        that is not known (the pour goes on: its segments may come all the same), an ApiError for what the game said"""
        payload = dict(k=key, g=kept, **({} if only is None else {"only": only}))
        try:
            res = await self.data_result("pour", payload, timeout_ms, what)
        except ApiError as e:
            if e.code not in ("timeout", "link_down"):
                raise
            self.journal.add("INFO", f"{what}: the pour of its answer had no answer ({e.code}); going on")
            return e.code
        if not res.get("ok"):
            raise ApiError(502, "lua_error", f"{res.get('error')}\n{res.get('stack') or ''}".strip())
        said = re.fullmatch(r"(\d+) (\d+)", (res.get("values") or [""])[0])
        if not said:
            raise ApiError(502, "pour_failed", f"the game answered the pour with {str(res.get('values'))[:200]}")
        if p.m is not None and p.m != int(said[1]):
            raise ApiError(502, "pour_failed", f"the game said {said[1]} segments, its segments say {p.m}")
        p.m, p.size = int(said[1]), int(said[2])
        return None

    async def window(self, n, more, session, refresh, what):
        """pieces 2..n of a long answer from a WoWBridge that does not pour, one call each (more(i)): WINDOW asked for at
        a time, one more as one comes back; each one the link lost is asked for again (piece_again) while the game
        keeps the answer (PIECE_KEPT from the first piece, or from the latest one with refresh)"""
        kept = [time.monotonic() + PIECE_KEPT]
        texts, waiting, numbers = {}, {}, iter(range(2, n + 1))
        t0 = noted = time.monotonic()

        def ask(i):
            waiting[asyncio.ensure_future(self.piece_again(more, i, lambda: kept[0], session))] = i
        for i in itertools.islice(numbers, probes.WINDOW):
            ask(i)
        try:
            while waiting:
                done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    i = waiting.pop(task)
                    texts[i] = self.piece(task.result(), i)[1]
                    if refresh:
                        kept[0] = time.monotonic() + PIECE_KEPT
                    nxt = next(numbers, None)
                    if nxt is not None:
                        ask(nxt)
                if time.monotonic() - noted >= POUR_NOTE:
                    noted = time.monotonic()
                    self.journal.add("INFO", f"{what}: piece {len(texts) + 1} of {n} of its answer in "
                                             f"{noted - t0:.1f} s")
        finally:
            for task in waiting:
                task.cancel()
            if waiting:
                await asyncio.gather(*waiting, return_exceptions=True)
        return "".join(texts[i] for i in range(2, n + 1))

    async def piece_again(self, more, i, until, session):
        """more(i); when it times out or finds the link down (a minimized window, a loading screen), asked for again once
        the link is back, PIECE_TRIES times in all, while the game still keeps the answer (until(): a monotonic time) in
        the same UI session (a reload takes the answer with it). Another error is the answer's"""
        for attempt in range(1, PIECE_TRIES + 1):
            try:
                return await more(i)
            except ApiError as e:
                if e.code not in ("timeout", "link_down") or attempt == PIECE_TRIES or not await self.link_back(until,
                                                                                                                session):
                    raise
                self.journal.add("INFO", f"piece {i} of a long answer asked for again ({e.code})")

    async def link_back(self, until, session):
        """whether the link is up in UI session `session`, waiting for it PIECE_WAIT seconds at most and not past until
        (a monotonic time, or what a callable says)"""
        stop = min(until() if callable(until) else until, time.monotonic() + PIECE_WAIT)
        while True:
            if self.ui_session() != session:
                return False
            if self.link_is_up():
                return True
            if time.monotonic() >= stop:
                return False
            await asyncio.sleep(0.25)

    @staticmethod
    def piece(res, i):
        """(how many pieces the answer has, the text of piece i) from a RUN result"""
        if not res.get("ok"):
            raise ApiError(502, "lua_error", f"{res.get('error')}\n{res.get('stack') or ''}".strip())
        try:
            return probes.piece(res.get("values"), i)
        except ValueError as e:
            raise ApiError(400, "probe_failed", str(e)) from None

    def data_calls(self):
        """whether the addon in the game takes data calls (CALL records: link features DATA, WoWBridge 0.9.7 on); before
        that, call / respond / addon_api go as code (probes.py chunks)"""
        comp = self.worker.comp if self.worker is not None else None
        try:
            return comp is not None and comp.data_calls()
        except AttributeError:
            return False

    async def data(self, verb, payload, timeout_ms, what):
        """a data call for WoWBridge; waits for its RUN result: (the job id, the result as run() gives it)"""
        timeout = self.timeout_seconds(timeout_ms, 10000)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        def start():
            comp = self.worker.companion()
            self.ready(comp, what)
            job = comp.data_call(verb, payload)
            self.pending[job] = (loop, fut)       # on the worker thread, before any RUN result can come
            return job

        job = await self.call(start)
        try:
            res = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise ApiError(504, "timeout", f"no answer to {what} (job {job}) within {int(timeout * 1000)} ms: "
                                           f"{await self.give_up(job)}", job=job) from None
        finally:
            self.pending.pop(job, None)           # also when the caller went away
        return job, self.run_result(res)

    async def data_result(self, verb, payload, timeout_ms, what):
        """data() without the job id"""
        return (await self.data(verb, payload, timeout_ms, what))[1]

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

    async def inspect(self, target=None, mouse=False, depth=1, snap=False, snap_format=None):
        """frames described (probes.py): the one a Lua expression gives, or the ones under the mouse; with snap, a
        picture of the first one's rectangle (and some margin), a PNG or (snap_format "jpeg") a JPEG"""
        if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 3:
            raise ApiError(400, "bad_request", "depth: 0..3 levels of children")
        self.snap_format(snap_format)
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
                result["snap"] = await self.snap([x, y, w, h], None, snap_format)
        return result

    async def api_scan(self, part, names=None):
        """one part of what the running game has, for the API manual (wuxian api-scan; probes.api_scan): its C_
        namespaces' functions, its global functions, its Enum and Constants tables, the fields of the tables named"""
        if names is not None and not (isinstance(names, list) and len(names) <= 1000):
            raise ApiError(400, "bad_request", "names: a list of at most 1000 tables (Enum.X, Constants.X)")
        try:
            code = probes.api_scan(part, names)
        except ValueError as e:
            raise ApiError(400, "bad_request", str(e)) from None
        data = await self.probe(code, 30000)
        if not isinstance(data, dict):
            raise ApiError(400, "probe_failed", f"the game's answer was not understood: {str(data)[:80]}")
        return data

    # an addon's API (WoWBridge API.lua): the events it emits, the functions it exposes. A name is passed to the game
    # quoted (probes.lua_str), so any text without control characters will do
    ADDON_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,64}\Z")
    CALL_NAME = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}\Z")

    @staticmethod
    def event_view(e):
        """an EVENT entry as `events` and `try` show it; a request (Request) also with its id, how long it waits and
        until when (t + wait), for `respond`"""
        out = dict(id=e["id"], t=e["t"], addon=e.get("addon"), topic=e.get("topic"), data=e.get("data"),
                   dropped=e.get("dropped") or 0)
        if e.get("request"):
            out.update(request=e["request"], wait=e.get("wait"),
                       expires=round(e["t"] + e["wait"], 3) if e.get("wait") else None)
        return out

    async def addon_events(self, addon=None, topic=None, since=0, limit=100, wait=0):
        """the events addons sent with WoWBridge's Emit (journal entries of kind EVENT) from id `since` on (-1: only
        the ones still to come), of that addon (a name or a glob: "MyAddon") and topic (a name or a glob, "scan.*", or a
        list of up to MAX_TOPICS of them: any one matches), at most `limit`; with `wait` seconds and none there yet, it
        waits for the first one. next = the id to ask from next time"""
        if not isinstance(since, int) or isinstance(since, bool) or since < -1:
            raise ApiError(400, "bad_request", "since: an entry id, 0 or more (the `next` of the last call), or -1 for "
                                               "the events still to come")
        if since == -1:
            since = self.journal.next_id
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ApiError(400, "bad_request", "limit: 1..1000")
        if not isinstance(wait, (int, float)) or isinstance(wait, bool) or not 0 <= wait <= 300:
            raise ApiError(400, "bad_request", "wait: 0..300 seconds")
        if addon is not None and (not isinstance(addon, str) or not addon.strip()):
            raise ApiError(400, "bad_request", "addon: a name or a glob")
        topics = [topic] if isinstance(topic, str) else topic
        if topic is not None and (not isinstance(topics, list) or not 1 <= len(topics) <= MAX_TOPICS
                                  or not all(isinstance(t, str) and t.strip() for t in topics)):
            raise ApiError(400, "bad_request", f"topic: a name or a glob, or a list of 1-{MAX_TOPICS} of them")

        def matches(e):
            return (e["kind"] == "EVENT" and (not addon or fnmatch.fnmatchcase(e.get("addon") or "", addon))
                    and (not topics or any(fnmatch.fnmatchcase(e.get("topic") or "", t) for t in topics)))

        def pick():
            entries, next_id, truncated = self.journal.since(since, self.journal.events.maxlen, ["EVENT"])
            hits = [e for e in entries if matches(e)]
            if len(hits) > limit:
                hits, next_id = hits[:limit], hits[limit]["id"]
            return [self.event_view(e) for e in hits], next_id, truncated

        loop, arrived = asyncio.get_running_loop(), asyncio.Event()
        stop = self.journal.subscribe(loop, lambda e: arrived.set() if matches(e) else None)   # before the first look
        try:
            events, next_id, truncated = pick()
            if not events and wait:
                try:
                    await asyncio.wait_for(arrived.wait(), wait)
                except asyncio.TimeoutError:
                    pass
                events, next_id, truncated = pick()
        finally:
            stop()
        comp = getattr(self.worker, "comp", None)          # the UI session: another one may run another WuxianKit
        return dict(events=events, next=next_id, truncated=truncated, session=getattr(comp, "current", None))

    async def call_exposed(self, addon, name, args=None, timeout_ms=10000, shared=None):
        """call a function an addon exposed with WoWBridge's Expose: args (JSON) go to it as a Lua table, its first
        return value comes back as `result`. A function that is not there, or that raised, is an ApiError call_failed
        with the message (and the stack). shared: an ask of WuxianKit's views (kitcache.SHARED; shared over HTTP), for
        which what the daemon knows of WuxianKit answers kit.revision and kit.manifest (daemon/kitcache.py)"""
        if not isinstance(addon, str) or not self.ADDON_NAME.match(addon):
            raise ApiError(400, "bad_request", "addon: the addon's folder name")
        if not isinstance(name, str) or not self.CALL_NAME.match(name):
            raise ApiError(400, "bad_request", "name: the name the addon exposed (letters, digits and _ . : -)")
        try:
            code = probes.call_chunk(addon, name, args)       # also what checks args (JSON a Lua table can hold)
        except ValueError as e:
            raise ApiError(400, "bad_request", str(e)) from None
        own = addon == KIT and name in OWN_READS and args is None and (SHARED.get() if shared is None else shared)
        if own and name in ("kit.revision", "kit.manifest"):
            return await self.kit_cache.call(name, lambda: self.call_game(addon, name, args, timeout_ms, code, own))
        return await self.call_game(addon, name, args, timeout_ms, code, own)

    async def call_game(self, addon, name, args, timeout_ms, code, own=False):
        """call_exposed's call in the game: as data (CALL records), or as the chunk `code` for a WoWBridge before 0.9.7.
        own: a read of WuxianKit's views, not the agent's call: the game neither counts it nor lists it (q; 0.9.9)"""
        t0 = time.time()
        try:
            if self.data_calls():
                req = {"a": addon, "n": name, "d": args, **({"q": True} if own else {})}
                data = await self.data_probe("call", req, timeout_ms, "call")
            else:
                data = await self.probe(code, timeout_ms, chunk="=call")
        except ApiError as e:
            if e.code != "probe_failed":
                raise
            self.journal.add("INFO", f"call {addon}.{name}: failed: {e.message.splitlines()[0][:200]}", addon=addon)
            raise ApiError(400, "call_failed", e.message) from None
        self.journal.add("INFO", f"call {addon}.{name}: ok in {time.time() - t0:.1f} s", addon=addon)
        return dict(addon=addon, name=name, ok=True, result=data.get("result") if isinstance(data, dict) else None)

    REQUEST_ID = REQUEST_ID

    async def respond(self, request, data=None, timeout_ms=10000):
        """answer an addon's request (WoWBridge's Request, an event with `request` in `events`): data (JSON) goes to
        its callback as a Lua table. A request that is no longer waiting (timed out, answered, the UI reloaded) is an
        ApiError respond_failed"""
        if not isinstance(request, str) or not self.REQUEST_ID.match(request):
            raise ApiError(400, "bad_request", "request: the id an event of `events` carries (\"5974.3\")")
        try:
            code = probes.reply_chunk(request, data)
        except ValueError as e:
            raise ApiError(400, "bad_request", str(e).replace("args:", "data:")) from None
        try:
            if self.data_calls():
                answer = await self.data_probe("reply", {"r": request, "d": data}, timeout_ms, "respond")
            else:
                answer = await self.probe(code, timeout_ms, chunk="=respond")
        except ApiError as e:
            if e.code != "probe_failed":
                raise
            self.journal.add("INFO", f"respond {request}: failed: {e.message.splitlines()[0][:200]}")
            raise ApiError(400, "respond_failed", e.message) from None
        answer = answer if isinstance(answer, dict) else {}
        addon, topic, failed = answer.get("addon"), answer.get("topic"), answer.get("callback_error")
        self.journal.add("INFO", f"respond {request} ({addon} {topic}): delivered"
                                 + (f", its callback raised: {str(failed)[:200]}" if failed else ""), addon=addon)
        return dict(request=request, ok=True, addon=addon, topic=topic, **({"callback_error": failed} if failed else {}))

    async def addon_api(self, addon=None, brief=False, shared=None):
        """what an addon exposed with WoWBridge's Expose (name and doc, in the order it did) and the topics it emitted
        (with how often); without addon, every addon that took a handle. brief: the names without the docs (WuxianKit's
        tools ask so what runs: its docs come to tens of KB; a WoWBridge before 0.9.9 sends them all the same). shared:
        an ask of WuxianKit's views (kitcache.SHARED; shared over HTTP): WuxianKit's answer is then the one this UI
        session gave already, if any"""
        if addon is not None and (not isinstance(addon, str) or not self.ADDON_NAME.match(addon)):
            raise ApiError(400, "bad_request", "addon: the addon's folder name")
        if addon == KIT and (SHARED.get() if shared is None else shared):
            return await self.kit_cache.describe(lambda: self.describe_once(addon, brief), bool(brief))
        return await self.describe_once(addon, brief)

    async def describe_once(self, addon, brief):
        """addon_api's answer from the game. The same question asked while it is on its way, in the same UI session,
        shares its answer: after a reload every agent's WuxianKit tools ask at once, and each answer goes up the game's
        link"""
        key = (getattr(getattr(self.worker, "comp", None), "current", None), addon, bool(brief))
        task = self.describing.get(key)
        if task is None:
            task = self.describing[key] = asyncio.ensure_future(self.describe(addon, brief))
            task.add_done_callback(lambda t: self.described(key, t))
        return await asyncio.shield(task)

    def described(self, key, task):
        if self.describing.get(key) is task:
            del self.describing[key]
        if not task.cancelled():
            task.exception()             # retrieved: callers that went away leave no "never retrieved" warning

    async def describe(self, addon, brief):
        if self.data_calls():
            data = await self.data_probe("describe", dict(a=addon, **({"b": True} if brief else {})), 15000, "addon_api")
        else:
            data = await self.probe(probes.describe_chunk(addon, brief), 15000)

        def tidy(a):                     # an empty Lua table comes as []: topics are an object
            return dict(a, topics=a.get("topics") or {}, exposed=a.get("exposed") or [])
        if isinstance(data, dict) and isinstance(data.get("addons"), list):
            return dict(addons=[tidy(a) for a in data["addons"] if isinstance(a, dict)])
        return tidy(data) if isinstance(data, dict) else dict(addon=addon, exposed=[], topics={})

    TRY_KINDS = ("ERR", "WARN", "BLOCKED", "OUT", "RELOAD", "EVENT", "SLOTS")

    async def try_(self, code=None, slash=None, seconds=2, addon=None, snap=False, frame=None, events=None,
                   timeout_ms=10000, snap_format=None):
        """do something in the game and collect what came of it: the action (Lua `code`, or a `slash` command line run
        through its handler) and its own result, then for `seconds` the Lua errors (with stacks), print output, warnings
        and blocked actions the game reported, the events that fired (`events`: names or globs, "all"), and a picture at
        the end (`snap`: the screen; `frame`: a Lua expression of a frame, pictured with a margin). A last run after the
        wait (the trace's stop, or a marker) is answered only after everything the game sent before it, so the window
        is complete. Everything in it counts: the player's own actions too (`addon` says whose an entry is).
        snap_format: the picture's, as snap() takes it"""
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
        self.snap_format(snap_format)
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
            complete = await self.caught_up()
        else:
            action = await self.run(lua, timeout_ms, addon)
            await asyncio.sleep(seconds)
            try:
                await self.run("return true", 15000, chunk="=probe")       # the end marker
                complete = await self.caught_up()
            except ApiError:
                complete = False
        entries, _, _ = self.journal.since(first, 1000, list(self.TRY_KINDS))
        out = dict(action=action, seconds=seconds, errors=[], blocked=[], warnings=[], prints=[], emitted=[], notes=[],
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
            elif e["kind"] == "EVENT":
                seen = self.event_view(e)
                out["emitted"].append(dict(t=t, addon=seen["addon"], topic=seen["topic"], data=seen["data"],
                                           **({"request": seen["request"]} if seen.get("request") else {})))
            else:
                out["notes"].append(dict(t=t, kind=e["kind"], text=text))
        if traced is not None:
            out["events"] = traced
        if frame:
            try:
                seen = await self.inspect(frame, False, 0, True, snap_format)
                out["frame"] = (seen.get("frames") or [None])[0]
                if seen.get("snap"):
                    out["snap"] = seen["snap"]
            except ApiError as e:
                out["notes"].append(dict(t=None, kind="SNAP", text=f"frame {frame}: {e.message}"))
        elif snap:
            try:
                out["snap"] = await self.snap(None, 1280, snap_format)
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
        if out.get("emitted"):
            topics = sorted({e["topic"] or "?" for e in out["emitted"]})
            parts.append(f"{len(out['emitted'])} event{'s' if len(out['emitted']) > 1 else ''} emitted ({', '.join(topics)[:120]})")
        if not later and not out["blocked"]:
            parts.insert(0, "no errors")
        tail = "" if out["complete"] else " (not all of the game's output came in time: some may be missing, see logs)"
        return f"{head}; then in {out['seconds']:g} s: " + ", ".join(parts) + tail

    async def agents(self, action=None, host=None, server=None):
        """the coding agents and an MCP server (agents.py; server: "wuxian", this program, the default, or "wuxianwow",
        无限图鉴): no action = every agent's state; "connect" registers it with one (and checks that the agent can use
        it), "disconnect" removes it, "verify" checks again"""
        server = server or agents.NAME
        if server not in agents.SERVERS:
            raise ApiError(400, "bad_request", f"server: one of {', '.join(agents.SERVERS)}")
        if action in (None, "", "status"):
            return await asyncio.to_thread(agents.status, server=server)
        actions = {"connect": agents.connect, "disconnect": agents.disconnect, "verify": agents.verify}
        if action not in actions:
            raise ApiError(400, "bad_request", 'action: "connect", "disconnect" or "verify"')
        if host not in agents.HOSTS:
            raise ApiError(400, "bad_request", f"host: one of {', '.join(agents.HOSTS)}")
        what = host if server == agents.NAME else f"{host} {server}"
        try:
            result = await asyncio.to_thread(actions[action], host, server=server)
        except agents.AgentError as e:
            self.journal.add("INFO", f"agents: {action} {what} failed: {e}")
            raise ApiError(409, "agent_failed", str(e)) from None
        check = result.get("verify")
        self.journal.add("INFO", f"agents: {action} {what} -> {result['state']}" + (f" ({check['text']})" if check else ""))
        return result

    # --- the extension catalog (extensions.py): the 扩展 page --------------------------------------------------------

    CATALOG_AGE = 600                         # seconds a catalog read stands before the page's next look reads it again

    async def extensions(self, refresh=False):
        """the extension catalog's addons and what the game folder holds of each: {catalog: {url, updated, checked,
        error}, addons: [the catalog's entry with there, installed (its .toc's version), state and missing (the folders
        it needs that are not there)], addons_dir, app, detected}. detected: the addons in the game's AddOns that give
        WuxianKit an extension and that neither the catalog lists nor a local extension put there, read from the
        folder (the game need not run): [their summary (local/summary.py of_addon) with official: the extension is
        one of WuxianKit's own]"""
        cat = self.ext_catalog
        if refresh or cat.checked is None or time.time() - cat.checked > self.CATALOG_AGE:
            await asyncio.to_thread(cat.refresh)
        try:
            addons = self.addons_dir()
        except ApiError:
            addons = None

        def look():
            out = []
            for e in cat.addons:
                there, version = extensions.installed(addons, e["folder"]) if addons else (False, None)
                out.append(dict(e, there=there, installed=version, state=extensions.state_of(e, there, version),
                                missing=extensions.missing(addons, e) if addons else []))
            return out

        def found():
            if not addons:
                return []
            return local_summary.detected(addons, skip=[e["folder"] for e in cat.addons],
                                          official=local_manifest.KIT_EXTENSIONS)
        return dict(catalog=cat.status(), addons=await asyncio.to_thread(look), addons_dir=str(addons) if addons else None,
                    app=extensions.app_version(), detected=await asyncio.to_thread(found))

    async def extension(self, action, ext_id):
        """install (an update too) or remove an addon of the catalog: {action, result, addon (as extensions() lists it)}.
        An install downloads the catalog's zip and checks it before anything in the game folder changes"""
        if action not in ("install", "remove"):
            raise ApiError(400, "bad_request", "action: install or remove")
        if not isinstance(ext_id, str) or not ext_id:
            raise ApiError(400, "bad_request", "id: an addon of the extension catalog")
        cat = self.ext_catalog
        entry = cat.get(ext_id)
        if entry is None:
            await asyncio.to_thread(cat.refresh)
            entry = cat.get(ext_id)
        if entry is None:
            raise ApiError(404, "no_such_extension", f"{ext_id!r} is not in the extension catalog")
        addons = self.addons_dir()
        if action == "install":
            if extensions.state_of(entry, False, None) == "needs_app":
                raise ApiError(409, "needs_app", f"{entry['id']} {entry['version']} needs 无限工坊 {entry['min_app']} or later")
            need = extensions.missing(addons, entry)
            if need:
                raise ApiError(409, "needs_addons", f"{entry['id']} needs {', '.join(need)} in the game first")
            try:
                raw = await asyncio.to_thread(cat.download, entry)
            except (OSError, ValueError, extensions.CatalogError) as e:
                raise ApiError(502, "download_failed", str(e) or type(e).__name__) from None
            from ..installer.addons import client_interface
            interface = client_interface(addons.parent.parent)[1]
            work = (extensions.install, addons, entry, raw, interface)
        else:
            work = (extensions.remove, addons, entry)
        try:
            result = await asyncio.to_thread(*work)
        except extensions.CatalogError as e:
            raise ApiError(422, "bad_package", str(e)) from None
        except OSError as e:
            raise ApiError(500, "extension_failed", f"{type(e).__name__}: {e}") from None
        if action == "remove" and result.get("removed") and entry["folder"] == "WuxianKit":
            from ..mcp.kit import forget_cached
            forget_cached()                        # its tools go with it, in every agent's session
        if entry["folder"] == "WuxianKit":
            self.kit_changed()
        self.journal.add("INFO", f"extension {entry['id']}: {action} {entry['version'] if action == 'install' else ''}"
                                 f" ({entry['folder']}, kept #{result.get('kept')})")
        listing = await self.extensions()
        return dict(action=action, result=result, addon=next((a for a in listing["addons"] if a["id"] == ext_id), None))

    # --- Agent 接入 (the 扩展 page): WuxianKit's tools as agents get them, and the MCP servers that serve agents ----------

    MCP_GONE = 75                             # seconds without a report before a stdio server counts as gone (it reports every 20)

    @staticmethod
    def number(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    @staticmethod
    def word(v):
        return v if isinstance(v, str) else None

    def report_agent(self, a):
        """one agent of a report, as far as it is what Presence makes (anything else left out)"""
        c = a.get("client") if isinstance(a.get("client"), dict) else None
        client = {"name": c["name"], "version": self.word(c.get("version"))} if c and self.word(c.get("name")) else None
        calls, tools = self.number(a.get("calls")), self.number(a.get("tools"))
        return dict(client=client, protocol=self.word(a.get("protocol")), first=self.number(a.get("first")),
                    last=self.number(a.get("last")), listed=self.number(a.get("listed")), calls=int(calls or 0),
                    tools=int(tools) if tools is not None else None, revision=self.word(a.get("revision")))

    def mcp_report(self, body):
        """a stdio MCP server's report (mcp.server.report): kept by its pid until it has been silent MCP_GONE seconds;
        what is not of the report's shape is left out"""
        if not isinstance(body, dict) or not isinstance(body.get("pid"), int) or isinstance(body.get("pid"), bool):
            raise ApiError(400, "bad_request", "pid: the MCP server's process id")
        agents = body.get("agents")
        if agents is not None and not isinstance(agents, list):
            raise ApiError(400, "bad_request", "agents: a list")
        kit = body.get("kit") if isinstance(body.get("kit"), dict) else None
        tools = self.number((kit or {}).get("tools"))
        record = dict(pid=body["pid"], version=self.word(body.get("version")), started=self.number(body.get("started")),
                      source_changed=body.get("source_changed") is True,
                      agents=[self.report_agent(a) for a in agents or [] if isinstance(a, dict)][:20],
                      kit=None if kit is None else dict(tools=int(tools or 0), revision=self.word(kit.get("revision"))),
                      seen=time.time())
        self.mcp_reports[record["pid"]] = record
        self.forget_silent()
        return dict(ok=True)

    def forget_silent(self):
        now = time.time()
        for pid, record in list(self.mcp_reports.items()):
            if now - record["seen"] > self.MCP_GONE:
                self.mcp_reports.pop(pid, None)

    def kit_views(self):
        """the views of the game's WuxianKit the daemon keeps: its own /mcp's (shared with the 扩展 page)"""
        kit = getattr(self.local_mcp, "kit_tools", None)
        return [k for k in (kit, self.kit_view) if k is not None]

    def kit_changed(self):
        """(any thread) WuxianKit may be another now (a new UI session, an install or a removal): the views ask the
        game again at their next look"""
        for kit in self.kit_views():
            kit.stale = True

    async def agent_access(self, refresh=False):
        """{kit: the game's WuxianKit as the agents' tools come from it (None: none, or never seen), kit_state (running;
        cached: the game away, the last one kept; absent: not in the AddOns folder; stopped: installed but not running;
        unknown: the game away and none seen), kit_addon: what the game folder holds of it ({there, version}; None: no game
        folder), servers: the MCP servers that serve agents now, a row per agent, each with its state against this program
        and that kit}. The kit is /mcp's own view of it (the page's own when there is no /mcp): read from the game when it
        is online, else the manifest kept on disk. An extension's tools are its capabilities'; wk_docs, wk_wait and
        wk_propose go with WuxianKit's own (kit). Asked before /mcp is built, it has it built now and waits: its view of the
        kit is the one to use, and the mcp package is not imported on this loop"""
        from ..mcp import kit as kit_tools
        view = await self.page_kit()
        if refresh:
            view.stale = True
        await view.current(wait=True)
        try:
            addons = self.addons_dir()
        except ApiError:
            addons = None
        kit_addon = None
        if addons is not None:                               # installed but not running: disabled, or a restart away
            there, version = await asyncio.to_thread(extensions.installed, addons, kit_tools.ADDON)
            kit_addon = dict(there=there, version=version)
        manifest, kit = view.manifest, None
        if manifest:
            exts, names = [], set(view.ids.values())
            for eid, ext in view.exts.items():               # the view's: what it could read of the manifest
                caps = [c for c in ext.get("capabilities") or [] if isinstance(c, dict) and c.get("id") in names]
                exts.append(dict(id=eid, title=kit_tools.text(ext.get("title")) or None, version=self.word(ext.get("version")),
                                 addon=self.word(ext.get("addon")), builtin=bool(ext.get("builtin")),
                                 state=self.word(ext.get("state")),
                                 tools=[dict(name=kit_tools.tool_name(c["id"]), cap=c["id"], kind=c.get("kind"),
                                             label=kit_tools.kind_label(c)[0], title=kit_tools.text(c.get("title")) or None)
                                        for c in caps]))
            general = [dict(name=t.name, cap=None, kind=None, label="Proposal" if t.name == kit_tools.PROPOSE.name else "Read",
                            title=t.title) for t in view.tools if t.name not in view.ids]
            own = next((e for e in exts if e["id"] == "kit"), None)
            if own is None:
                own = dict(id="kit", title=None, version=self.word(manifest.get("kit")), addon=None, builtin=True,
                           state="on", tools=[])
                exts.insert(0, own)
            own["tools"] += general
            kit = dict(version=self.word(manifest.get("kit")), protocol=manifest.get("protocol"),
                       revision=self.word(manifest.get("revision")), language=self.word(manifest.get("language")),
                       tools=len(view.tools), extensions=exts, source="cache" if view.away or view.checked is None else "game",
                       newer=bool(kit_tools.newer(manifest)))
        if kit is not None:
            kit_state = "cached" if kit["source"] == "cache" else "running"
        elif view.away:
            kit_state = "unknown"
        else:
            kit_state = "stopped" if kit_addon and kit_addon["there"] else "absent"
        self.forget_silent()
        rows = []
        for record in list(self.mcp_reports.values()):
            for agent in record["agents"] or [None]:          # a process no agent has talked to yet: one row
                rows.append(self.access_row("stdio", record, agent, kit))
        local = self.local_mcp
        if local is not None and getattr(local, "presence", None) is not None:
            record = dict(pid=os.getpid(), version=self.version, started=round(self.started), source_changed=False)
            for agent in local.presence.view():
                rows.append(self.access_row("http", record, agent, kit))
        rows.sort(key=lambda r: -(r["last"] or 0))
        return dict(kit=kit, kit_state=kit_state, kit_addon=kit_addon, servers=rows, program=self.version, now=time.time())

    async def page_kit(self):
        """the view of the game's WuxianKit the pages read: /mcp's own (had built now when it is not yet: its view is the
        one to use, and the mcp package is not imported on this loop), else one of the pages' own"""
        lazy = self.mcp_lazy
        if lazy is not None and not lazy.built.is_set():
            lazy.wanted.set()
            await lazy.built.wait()
        from ..mcp import kit as kit_tools
        view = getattr(self.local_mcp, "kit_tools", None)
        if view is None:
            if self.kit_view is None:
                self.kit_view = kit_tools.KitTools(self)
            view = self.kit_view
        return view

    async def kit_read(self, name, args=None):
        """a read of WuxianKit for the pages (neither counted nor listed in the game): its result"""
        answer = await self.call_exposed(KIT, name, args, 10000, shared=True)
        return (answer or {}).get("result")

    async def kit_apps(self, refresh=False):
        """WuxianKit's apps for the 接入 Agent page: {online, apps: [{id, title, by, addon, temp (a temporary app: an
        agent's, not remembered), prompt, start (the line that hands the app to an agent; None: it has no prompt, or a
        WuxianKit before it), on, blocked (why it cannot start while off), extension {id, title, on} | None, skill
        {name, title, description, needs, mcp} | None}]}. online: the game's WuxianKit answered kit.apps; else the apps
        of the manifest kept on disk (none without one), on and blocked None. blocked comes from kit.apps (WuxianKit
        0.6), never from kit.app.status: that is the agent's
        heartbeat and would keep the app alive. The skill is the one the app's prompt names in the app's docs folder
        (the manifest says it, kit.apps does not), else the one wk_docs gives by that name; the extension, the app's
        own as kit.apps and the manifest say it"""
        from ..mcp import kit as kit_tools
        view = await self.page_kit()
        if refresh:
            view.stale = True
        manifest = await view.current(wait=True)
        live = None
        if manifest and not view.away:
            try:
                live = await self.kit_read("kit.apps")
            except ApiError:                                 # the game away, or a WuxianKit before its apps
                live = None
            if not isinstance(live, list):
                live = None
        known = {a["id"]: a for a in kit_tools.apps_of(manifest)}
        rows = kit_tools.apps_of({"apps": live}) if live is not None else list(known.values())
        states = {a.get("id"): a for a in live or [] if isinstance(a, dict)}
        skills = await view.skills() if manifest else []
        out = []
        for a in rows:
            kept = known.get(a["id"]) or {}
            state = states.get(a["id"]) or {}
            on = state.get("on") if isinstance(state.get("on"), bool) else None
            blocked = state.get("blocked") if isinstance(state.get("blocked"), str) else None
            eid, docs = a["extension"] or kept.get("extension"), a["docs"] or kept.get("docs")
            skill = None
            if a["prompt"]:
                skill = (next((s for s in skills if docs and s["docs"] == docs and s["name"] == a["prompt"]), None)
                         or next((s for s in skills if s["prompt"] == a["prompt"]), None))
            ext = view.exts.get(eid) if eid else None
            start = a["start"] or kept.get("start")
            out.append(dict(id=a["id"], title=a["title"], by=self.word(a["by"]), addon=a["addon"], temp=a["temp"], prompt=a["prompt"], start=start, on=on, blocked=blocked,
                            extension=None if ext is None else dict(
                                id=ext.get("id"), title=kit_tools.text(ext.get("title")) or ext.get("id"),
                                on=ext.get("state") == "on"),
                            skill=None if skill is None else dict(
                                name=skill["prompt"], title=skill["title"], description=skill["description"],
                                needs=list(skill["needs"]), mcp=[dict(m) for m in skill["mcp"]])))
        return dict(online=live is not None, apps=out)

    def access_row(self, kind, record, agent, kit):
        """one agent of an MCP server (a Presence record; None: a server no agent has talked to yet), and its state: stale
        (the server runs older code than this program: restart the agent's session), unlisted (it has not asked for the
        tools yet), no_kit (the tools it got have no wk_ ones though the game has WuxianKit), behind (they came from
        another manifest, or the game runs no WuxianKit any more: an agent that hears of list changes lists them again on
        its own, others need a restart), current. What it got is its last tool list"""
        from ..mcp.server import version_key
        agent = agent or {}
        got = dict(tools=agent.get("tools"), revision=agent.get("revision")) if agent.get("listed") else None
        if version_key(record.get("version")) < version_key(self.version) or record.get("source_changed"):
            state = "stale"
        elif got is None:
            state = "unlisted"
        elif kit is not None and not kit["newer"] and not got.get("tools"):
            state = "no_kit"
        elif got.get("revision") != kit["revision"] if kit is not None else got.get("tools"):
            state = "behind"
        else:
            state = "current"
        client = agent.get("client") if isinstance(agent.get("client"), dict) else None
        return dict(kind=kind, pid=record.get("pid"), version=record.get("version"), started=record.get("started"),
                    client=client, protocol=agent.get("protocol"), first=agent.get("first"), last=agent.get("last"),
                    calls=agent.get("calls") or 0, listed=agent.get("listed"), tools=(got or {}).get("tools"),
                    revision=(got or {}).get("revision"), state=state)

    # --- local extensions (local/host.py) and reactions (local/reactions.py) -----------------------------------------

    def soon(self, coro):
        """(any thread) a coroutine on the server's loop; dropped when there is none (a Service without a server)"""
        loop = self.loop
        if loop is None or self.closed:
            coro.close()
            return None
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:                  # the loop has closed: the daemon stops
            coro.close()
            return None

    def link_came_up(self, sid):
        """(worker thread) the game can be told things again and no kit.ready says so: this program started while
        the game ran on, or the game was covered, minimized or behind a loading screen. The local extensions' apps
        are reconciled (what was added, removed or turned off meanwhile), and the reactions' temporary app made
        what was asked of it meanwhile. After a login or a reload this comes first and kit.ready does it again"""
        self.soon(self.reconcile_local_apps())
        self.soon(self.reactions_hear_again(owed=True))

    def kit_event(self, topic, data):
        """(any thread) WuxianKit's events for the local side: kit.ready (a login or a reload: the extensions' apps
        are reconciled, the reactions' temporary app opened again), app.reactions (what that app heard: each item to
        the reactions), kit.app (an app started: the reactions its manifest declares are set; stopped: the reactions
        it owns go)"""
        if topic == "kit.ready":
            self.reactions_open = False
            self.soon(self.reconcile_local_apps(ready=True))
            self.soon(self.reactions_hear_again())
        elif topic == f"app.{REACTIONS_APP}" and isinstance(data, dict) and isinstance(data.get("items"), list):
            for item in data["items"]:
                if isinstance(item, dict):
                    self.soon(self.reactor.item(item))
        elif topic == "kit.app" and isinstance(data, dict) and isinstance(data.get("app"), str) \
                and isinstance(data.get("on"), bool):
            if data["app"] != REACTIONS_APP:
                self.soon(self.app_runs(data["app"], data["on"]))
            elif not data["on"]:
                self.reactions_open = False

    async def declare(self, eid, decl):
        """kit.app.declare of one app of a local extension: "ok"; None when not now (the game away); ABSENT when the
        game's WuxianKit has no kit.app.declare (or no WuxianKit runs); else WuxianKit's refusal, journaled"""
        if not self.link_is_up():
            return None
        try:
            await self.call_exposed(KIT, "kit.app.declare", decl, 10000)
        except ApiError as e:
            text = str(e.message)
            if e.code != "call_failed":
                return None
            if "has no kit.app.declare" in text or "has no WoWBridge handle" in text:
                return ABSENT
            first = text.splitlines()[0][:300] if text else "refused"
            self.journal.add("INFO", f"local extension {eid}: WuxianKit did not take its app {decl.get('app')}: {first}")
            return first
        return "ok"

    async def app_described(self, app):
        """WuxianKit's declaration of an app now as JSON (kit.app.describe: normalized); None when it has none, or
        cannot say (the game away, a WuxianKit before describe)"""
        try:
            answer = await self.call_exposed(KIT, "kit.app.describe", {"app": app}, 10000)
        except ApiError:
            return None
        result = (answer or {}).get("result")
        if not isinstance(result, dict) or not isinstance(result.get("decl"), dict):
            return None
        return json.dumps(result["decl"], sort_keys=True, ensure_ascii=False)

    async def tell_roster(self, ready=False):
        """WuxianKit told the extensions added to this computer that have a local part (kit.local.set, the whole list:
        its /wk window lists them beside the game's own, as the App's 扩展 page does). It keeps the list until its UI
        reloads, so: once in each UI session (ready: its kit.ready came, it has none), and again when the list is
        another. A WuxianKit without kit.local.set (or one that refuses the list) is not asked again until then; the
        game away: at the next round"""
        if ready:
            self.roster_told = None
        session = self.ui_session()
        roster = await asyncio.to_thread(self.local.roster)
        told = (session, json.dumps(roster, sort_keys=True, ensure_ascii=False))
        if self.roster_told == told:
            return
        try:
            await self.call_exposed(KIT, "kit.local.set", {"extensions": roster}, 10000)
        except ApiError as e:
            if e.code != "call_failed":
                return                        # the game went away: at the next round
            text = str(e.message or "")
            if "kit.local.set" not in text and "has no WoWBridge handle" not in text:
                self.journal.add("INFO", "local extensions: WuxianKit did not take their list: "
                                         + text.partition("\n")[0][:300])
        self.roster_told = told

    async def reconcile_local_apps(self, ready=False):
        """WuxianKit's apps of the local extensions made what the enabled extensions' manifests say, while the game is
        online (after every kit.ready, each time the link comes up, and when an extension was added, removed, turned
        on or off, loaded or approved; the game away: when it is back). An app WuxianKit has as a local one (kit.apps: its row's
        local) that no enabled extension declares any more is forgotten (kit.app.forget: it stops, its declaration
        goes, the player's options of it stay). The others are declared (kit.app.declare) when they are new or not
        what WuxianKit has (kit.app.describe, against what it answered when it last took ours). First of all WuxianKit
        is told the extensions themselves (tell_roster: the rows its window has for them). The reactions of
        the apps that run are set; ready (a new UI session): those of the apps that do not run go, as when they
        stopped (their kit.app event may have been missed)"""
        if self.declaring is None:
            self.declaring = asyncio.Lock()
        try:
            async with self.declaring:
                if self.ui_session() is None or not self.link_is_up():
                    return
                await self.tell_roster(ready)
                want = {}
                for eid, decl in await asyncio.to_thread(self.local.declarations):
                    if isinstance(decl, dict) and isinstance(decl.get("app"), str):
                        want.setdefault(decl["app"], (eid, decl))
                try:
                    rows = await self.kit_read("kit.apps")
                except ApiError:              # the game went away, or a WuxianKit before its apps
                    rows = None
                rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)] \
                    if isinstance(rows, list) else []
                for row in rows:
                    if not isinstance(row.get("local"), dict) or row["id"] in want:
                        continue
                    try:
                        await self.call_exposed(KIT, "kit.app.forget", {"app": row["id"]}, 10000)
                    except ApiError as e:
                        first = str(e.message or "").partition("\n")[0][:300]
                        self.journal.add("INFO", f"local extensions: WuxianKit did not forget the app {row['id']} "
                                                 f"({e.code}): {first}")
                    else:
                        self.journal.add("INFO", f"local extension {row['local'].get('id')}: its app {row['id']} is "
                                                 f"no longer declared: WuxianKit forgot it")
                    self.app_memory.pop(row["id"], None)
                    await self.app_runs(row["id"], False)
                for app in [a for a in self.app_memory if a not in want]:
                    del self.app_memory[app]
                for app, (eid, decl) in want.items():
                    ours = json.dumps(decl, sort_keys=True, ensure_ascii=False, default=repr)
                    theirs = await self.app_described(app)
                    if theirs is not None and self.app_memory.get(app) == (ours, theirs):
                        continue
                    result = await self.declare(eid, decl)
                    if result == ABSENT:
                        return                # a WuxianKit before declare: none of them
                    if result is None:
                        return                # the game went away: at the next kit.ready
                    self.app_memory.pop(app, None)
                    if result == "ok":
                        theirs = await self.app_described(app)
                        if theirs is not None:
                            self.app_memory[app] = (ours, theirs)
                for row in rows:              # the apps' own reactions: there while the app runs
                    if row["id"] in want and (row.get("on") is True or ready and row.get("on") is False):
                        await self.app_runs(row["id"], row["on"])
        except Exception as e:                # a bug: said, and the daemon goes on
            self.journal.add("INFO", f"local extensions: reconciling their apps failed: {e!r}")

    async def extension_running(self, eid, why):
        """the Proc of a local extension once it runs (Host.ensure). The caller may go while it starts: the start goes
        on (the Future is the host's, others wait for it too)"""
        inner = asyncio.wrap_future(self.local.ensure(eid, why))
        inner.add_done_callback(_seen)
        return await asyncio.shield(inner)

    async def app_runs(self, app, on):
        """an app started or stopped (its kit.app event, reconcile_local_apps): while it runs the reactions its
        extension's manifest declares for it are set (Host.app_reactions; local/reactions.py app_started), owner the
        app; when it stops they go, with what an agent set with it as owner. An extension whose capability a reaction
        calls and that never described itself is started first"""
        if self.reacting is None:
            self.reacting = asyncio.Lock()
        try:
            async with self.reacting:
                if not on:
                    await self.reactor.app_stopped(app)
                    return
                specs = [s for s in await asyncio.to_thread(self.local.app_reactions, app) or [] if isinstance(s, dict)]
                for cap in {s.get("call") for s in specs}:
                    found = await asyncio.to_thread(self.local.capability, cap)
                    if found is not None and found[1] is None and found[0]["enabled"] \
                            and found[0].get("described") is None:
                        try:
                            await self.extension_running(found[0]["id"], f"a reaction of the app {app}")
                        except ApiError:      # it cannot start: the reaction is not taken, and said below
                            pass
                problems = await self.reactor.app_started(app, specs)
                for line in problems if specs else []:
                    self.journal.add("INFO", f"local extensions: {line}")
        except Exception as e:                # a bug: said, and the daemon goes on
            self.journal.add("INFO", f"local extensions: the reactions of the app {app} failed: {e!r}")

    def local_event(self, eid, item):
        """(an extension's reader thread) an item of one of its sources: an event of the addon "local" in the journal
        (topic <extension>.<source>), and the reactions hear it"""
        self.journal.add("EVENT", json.dumps({"a": LOCAL, "t": item["kind"], "d": item}, ensure_ascii=False))
        self.soon(self.reactor.item(item))

    def local_changed(self, revision):
        """(any thread) the local extensions changed: local.changed in the journal; the reactions look again at which
        of their sources are the game's"""
        self.journal.add("EVENT", json.dumps({"a": LOCAL, "t": "local.changed", "d": {"revision": revision}}))
        self.soon(self.reactor.refresh())

    def extension_calls(self, eid, cap, args, chain=None):
        """(an extension's reader thread) its call {cap, args}: a Future of the result (ApiError as local_call's).
        chain: the caller chain of the call it is serving, which this call goes on (one hop longer: local_call ends
        a chain of more than MAX_CHAIN hops as a loop)"""
        caller = {"by": "extension", "id": eid, "chain": [x for x in chain or [] if isinstance(x, str)] + [f"ext:{eid}"]}

        async def value():
            return (await self.local_call(cap, args, caller=caller))["result"]
        fut = self.soon(value())
        if fut is None:
            raise ApiError(503, "stopping", "the daemon is shutting down")
        return fut

    def kit_extension_ids(self):
        """(the local host's taken) the ids of the extensions WuxianKit lists in the manifest known now, each with
        the addon that brings it ({id: addon}): a local extension that is not registered yet cannot have one of them,
        but for one in the game only whose own addon it is; {} when no manifest is known"""
        manifest = self.kit_manifest_known() or {}
        return {x["id"]: x.get("addon") for x in manifest.get("extensions") or []
                if isinstance(x, dict) and isinstance(x.get("id"), str)}

    def game_side_known(self):
        """(the local host's game_side) what the game says of the extensions' game halves now: {online: the link is
        up, extensions: {id: {addon, state, version}} as the WuxianKit manifest known now lists them}"""
        manifest = self.kit_manifest_known() or {}
        return dict(online=bool(self.link_is_up()),
                    extensions={x["id"]: dict(addon=x.get("addon"), state=x.get("state"), version=x.get("version"))
                                for x in manifest.get("extensions") or []
                                if isinstance(x, dict) and isinstance(x.get("id"), str)})

    def kit_version_known(self):
        """(the local host's kit_version) the version of WuxianKit in the manifest known now (its kit), or None"""
        version = (self.kit_manifest_known() or {}).get("kit")
        return version if isinstance(version, str) and version else None

    def game_addons_known(self):
        """(the local host's addons_dir) the game's AddOns folder, or None while the game folder is not known. Never
        an error, unlike addons_dir()"""
        return getattr(self.worker, "addons", None) or None

    def client_interface_known(self):
        """(the local host's interface) the client's Interface number for the .toc files of an extension's game
        addons, as extension_install gives the catalog's; None when it cannot be read"""
        addons = self.game_addons_known()
        if addons is None:
            return None
        from ..installer.addons import client_interface
        try:
            return client_interface(Path(addons).parent.parent)[1]
        except Exception:
            return None

    def kit_manifest_known(self):
        """the WuxianKit manifest known now without asking the game: a view's, the kit cache's newest, the one kept on
        disk; None"""
        view = getattr(self.local_mcp, "kit_tools", None) or self.kit_view
        manifest = getattr(view, "manifest", None)
        if isinstance(manifest, dict):
            return manifest
        kept = list(self.kit_cache.manifests.values())
        if kept:
            return kept[-1]
        from ..paths import state_dir
        try:
            manifest = json.loads((state_dir() / "kit-manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return manifest if isinstance(manifest, dict) else None

    @staticmethod
    def kit_kind(manifest, cap):
        """the kind of a capability of the game's WuxianKit as its manifest says (one that answers with a proposal,
        proposes, counts as do); None when no extension that is on has it"""
        for ext in (manifest or {}).get("extensions") or []:
            if not isinstance(ext, dict) or ext.get("state") != "on":
                continue
            for c in ext.get("capabilities") or []:
                if isinstance(c, dict) and c.get("id") == cap:
                    return "do" if c.get("proposes") else c.get("kind")
        return None

    def cap_kind(self, cap):
        """see / keep / point / say / do of a capability, a local extension's or the game's WuxianKit's; None"""
        kind = self.local.kind_of(cap)
        if kind is not None:
            return kind
        return self.kit_kind(self.kit_manifest_known(), cap)

    @staticmethod
    def local_error(e, cap):
        """the ApiError of a local capability's call that failed (or whose extension did not start), its code one of
        the protocol's reasons (notes.REASONS): the extension's busy and cancelled answers, call_error's, the host's
        needs_approval as it is; whatever else keeps the extension from running (off, stopped, a bad command, the
        daemon stopping, ...) is down"""
        if not isinstance(e, ApiError):
            code = getattr(e, "code", None)
            if code == 2:
                return ApiError(429, "busy", f"{cap}: {e.message}")
            if code == 3:
                return ApiError(499, "cancelled", f"{cap}: {e.message}")
            e = call_error(e, cap)
        if e.code in REASONS:
            return e
        return ApiError(503, "down", str(e.message), **e.extra)

    async def local_call(self, cap, args=None, timeout_ms=None, caller=None):
        """a capability called: a local extension's (started when it does not run: the start is not the call's time),
        else the game's WuxianKit's (see, keep or point only: say and do are proposals, a later step). It has
        timeout_ms when given, else the timeout it declared (CALL_TIMEOUT without one, and for the game's): a local
        extension is told that deadline, told when the time is over (and asked whether it still lives) and when the
        caller went (this coroutine cancelled). caller: who calls ({by, app?, id?, chain?}; default by "agent");
        its chain is the hops the call came by, more than MAX_CHAIN is a loop.
        {result}; ApiError with one of the protocol's reasons as its code (notes.REASONS): no_capability, bad_args,
        failed, timeout, busy, cancelled, down (it cannot start: why, with the end of its output; for the game's: the
        game is not online), needs_approval (the player has not allowed its command yet), not_allowed (a say or do),
        loop; bad_request when the request itself is wrong"""
        if not isinstance(cap, str) or not re.match(r"^[a-z][a-z0-9_]*\.[a-z0-9_.]+\Z", cap):
            raise ApiError(400, "bad_request", "cap: a capability id, <extension>.<name> (voice.say, sense.bags)")
        if args is not None and not isinstance(args, dict):
            raise ApiError(400, "bad_args", f"{cap}: args is an object of its arguments by name")
        seconds = None if timeout_ms is None else self.timeout_seconds(timeout_ms, None)
        caller = dict(caller) if isinstance(caller, dict) else {"by": "agent"}
        chain = caller.get("chain")
        if chain is not None:
            if not isinstance(chain, list) or not all(isinstance(x, str) for x in chain):
                raise ApiError(400, "bad_request", "caller.chain: a list of texts (the hops the call came by)")
            if len(chain) > MAX_CHAIN:
                raise ApiError(409, "loop", f"{cap}: the call came by more than {MAX_CHAIN} hops "
                                            f"({' > '.join(chain)}): capabilities that call each other in a circle")
        found = await asyncio.to_thread(self.local.capability, cap)
        if found is not None:
            entry, described = found
            eid = entry["id"]
            if not entry["enabled"]:
                raise ApiError(404, "no_capability", f"{cap}: the local extension {eid} is off (extension_set turns it "
                                                     f"on)")
            if described is None and entry.get("described") is not None:
                raise ApiError(404, "no_capability", self.no_such(eid, entry))
            try:
                proc = await self.extension_running(eid, f"a call of {cap}")
            except ApiError as e:
                raise self.local_error(e, cap) from None
            if described is None:                          # it had not described itself before this start
                found = await asyncio.to_thread(self.local.capability, cap)
                described = found[1] if found else None
                if described is None:
                    raise ApiError(404, "no_capability", self.no_such(eid, found[0] if found else entry))
            limit = seconds if seconds is not None else float(described.get("timeout") or CALL_TIMEOUT)
            fut = self.local.call(proc, described["name"], args, caller, deadline_ms=int(limit * 1000))
            waiter = asyncio.wrap_future(fut)
            waiter.add_done_callback(_seen)
            try:
                done, _ = await asyncio.wait({waiter}, timeout=limit)
            except asyncio.CancelledError:                 # the caller went: the extension is told
                self.local.cancel_call(proc, fut)
                raise
            if not done:
                self.local.cancel_call(proc, fut, ping=True)
                raise ApiError(504, "timeout", f"{cap} did not answer within {limit:g} s (extension_logs shows what "
                                               f"it did)")
            if waiter.cancelled():
                raise ApiError(499, "cancelled", f"{cap}: the call was cancelled")
            error = waiter.exception()
            if error is not None:
                raise self.local_error(error, cap) from None
            return dict(result=waiter.result())
        manifest = self.kit_manifest_known()
        kind = self.kit_kind(manifest, cap)
        if kind is None and self.link_is_up():
            try:
                view = await self.page_kit()
                manifest = await view.current(wait=True)
            except Exception:
                manifest = None
            kind = self.kit_kind(manifest, cap)
        if kind is None:
            raise ApiError(404, "no_capability", f"no capability {cap}: no local extension has it (local_extensions "
                                                 f"lists them), and the game's WuxianKit has no extension that is on "
                                                 f"with it (or the game is not online)")
        if kind not in ALLOWED_KINDS:
            raise ApiError(403, "not_allowed", f"{cap} is a {kind} capability: a proposal the player confirms; only see, "
                                               f"keep and point capabilities can be called this way")
        try:
            answer = await self.call_exposed(KIT, cap, args, int((seconds or CALL_TIMEOUT) * 1000))
        except ApiError as e:
            if e.code == "call_failed":
                first, _, rest = str(e.message or "").partition("\n")
                raise ApiError(422, "failed", f"{cap}: {first}") from None
            if e.code == "timeout":
                raise ApiError(504, "timeout", f"{cap}: {e.message}") from None
            raise ApiError(503, "down", f"{cap}: the game is not online ({e.code}: {e.message})") from None
        return dict(result=(answer or {}).get("result"))

    @staticmethod
    def no_such(eid, entry):
        names = [c["id"] for c in ((entry or {}).get("described") or {}).get("capabilities") or []]
        return f"the local extension {eid} has no such capability (it has: {', '.join(names) or 'none'})"

    async def local_extensions(self):
        """{revision, extensions: [row]} (local/host.py Host.row)"""
        return await asyncio.to_thread(self.local.listing)

    async def local_manifest(self):
        """{revision, extensions}: the enabled local extensions as WuxianKit's manifest lists an extension (mcp/kit.py
        renders them as it renders the game's)"""
        return await asyncio.to_thread(self.local.manifest_entries)

    async def extension_add(self, path, replace=False, sha256=None):
        """registers a local extension by its folder (or its wuxian-extension.json): its row; its apps are told to
        WuxianKit now while the game is online (reconcile_local_apps). Where the setting asks nobody
        (extension_approval "auto"), one that starts on use is started here once, only to learn what it can do
        (Host.describe): its tools and options are there before its first call. Else that is done when the player
        allows its command (extension_approve). path may be a package (.zip) instead: it is installed into the
        extensions folder: its row with package {file, replaced}; that one always waits for the player. Over the
        version there only with replace true (the player said so): without it needs_replace (409) answers old, new,
        changes and same, and nothing is touched (Host.add_package). sha256 (with replace): the package that was
        compared, new.package.sha256 of that answer; another file there by now answers changed (409)"""
        row = await asyncio.to_thread(self.local.add, path, replace, sha256)
        self.notes.forgive(row["id"])                      # (a package over the version there: its notes are tried anew)
        if self.extension_approval == "auto" and row.get("enabled") and row.get("start") == "use" \
                and row.get("allowed") and not row.get("error") and row.get("where") != "game":
            self.local.describe(row["id"]).add_done_callback(_seen)   # in the background: its row says how it went
        await self.reconcile_local_apps()
        return row

    async def extension_remove(self, id):
        """stops a local extension and takes it off the registry: {removed, id, path, kept, addons}. A linked one's
        folder stays, one in the extensions folder goes to its .removed (kept), its game addons go out of the game's
        AddOns (addons); WuxianKit forgets its apps, now while the game is online, else at its next kit.ready"""
        res = await asyncio.to_thread(self.local.remove, id)
        self.notes.forgive(id)
        await self.reconcile_local_apps()
        return res

    async def extension_load(self, id):
        """checks a local extension and (re)starts it: {row, check, addons, restart, hint} (its game addons are
        installed first where the game has none or another version); its apps are made what its manifest says now.
        One in the game only has no process: its addons are installed and, while the game is online, run there now
        (loaded: [{folder, ok, error, check}], as `load` does it), and its row says how the game lists it then. Its
        Lua is checked before anything is installed: a syntax error leaves the game's AddOns as it was (422
        check_failed, as `load` answers it; the findings in the error's `addons`)"""
        before = await asyncio.to_thread(self.local.get, id)
        if before.get("where") == "game" and before.get("enabled"):
            found = (await self.extension_check(before["id"])).get("addons") or []
            bad = [(a, f) for a in found for f in a["errors"] if f.get("code") in ("syntax", "unreadable")]
            if bad:
                (a, f), more = bad[0], len(bad) - 1
                at = f"{a['folder']}/{f['file']}:{f['line']}: " if f.get("file") else f"{a['folder']}: "
                raise ApiError(422, "check_failed", f"extension_load {before['id']}: nothing installed, {at}{f['message']}"
                                                    + (f" ({f['hint']})" if f.get("hint") else "")
                                                    + (f"; {more} more of the kind: extension_check lists them"
                                                       if more else ""), addons=found)
        res = await asyncio.to_thread(self.local.load_, id)
        row = res["row"]
        self.notes.forgive(row["id"])         # loaded again: its notes are tried at once
        await self.reconcile_local_apps()
        if row.get("where") == "game":
            res["loaded"] = await self.game_loaded(res.get("addons") or [])
            res["row"] = await self.extension_waiting(row["id"], fresh=bool(res["loaded"]))
            if res.get("restart") and res["loaded"] and all(x["ok"] for x in res["loaded"]):
                res["hint"] = "; ".join(x for x in (res.get("hint"), "its files ran in the running game just now "
                                                    "(loaded): list the tools again, its wk_ tools may be there; the "
                                                    "game itself takes a new addon or new files only when it starts")
                                        if x)
        return res

    async def game_loaded(self, addons):
        """(extension_load) the addons of an extension in the game only run in the running game now, each as `load
        <folder>` does it: [{folder, ok, error: the first one, or None, check: the findings of its check}]; [] while
        the game is not online. Only those that are in AddOns as the extension has them"""
        if not self.link_is_up():
            return []
        out = []
        for a in addons:
            if a.get("state") not in ("installed", "current"):
                continue
            try:
                done = await self.load(a["folder"], mode="addon")
            except ApiError as e:
                out.append(dict(folder=a["folder"], ok=False, error=e.message, check=e.extra.get("check")))
                continue
            bad = next((f for f in done["files"] if not f["ok"]), None)
            out.append(dict(folder=a["folder"], ok=bool(done["ok"]),
                            error=f"{bad['file']}: {bad['error']}" if bad else None, check=done.get("check")))
        return out

    async def extension_waiting(self, id, fresh=False):
        """(extension_wait, extension_load) a local extension's row with its game half as the game lists it now: when
        it has one and the link is up, the view of WuxianKit is brought up to date first (what kit.changed told it
        is read; fresh: the game is asked whatever was heard)"""
        row = await asyncio.to_thread(self.local.get, id)
        if row.get("game") is None or not self.link_is_up():
            return row
        try:
            view = await self.page_kit()
            if fresh:
                view.stale = True
            await asyncio.wait_for(view.current(wait=True), 15)
        except Exception:                     # the game went away meanwhile: the row says what is known
            pass
        return await asyncio.to_thread(self.local.get, id)

    async def extension_wait(self, id, seconds=120):
        """waits for what only the player can do before a local extension works (its row's player): {row, was,
        timeout}. was: the row's player when the wait began (None: nothing to wait for, answered at once); it ends
        when that is done (the row's player is then the next thing, or None), or after seconds (WAIT_MAX at most):
        timeout true. A game that is restarted goes away first: after restart_game, start_game is the same wait"""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds != seconds or seconds < 0:
            raise ApiError(400, "bad_request", f"seconds: how long to wait, 0 to {WAIT_MAX:g}")
        end = time.monotonic() + min(float(seconds), WAIT_MAX)
        row = await self.extension_waiting(id)
        was = row.get("player")
        if was is None:
            return dict(row=row, was=None, timeout=False)
        same = {was["action"]} | ({"start_game"} if was["action"] == "restart_game" else set())
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return dict(row=row, was=was, timeout=True)
            await asyncio.sleep(min(WAIT_STEP, left))
            row = await self.extension_waiting(id)
            if (row.get("player") or {}).get("action") not in same:
                return dict(row=row, was=was, timeout=False)

    async def extension_approve(self, id, fingerprint=None):
        """the player allows a local extension's command to run (Host.approve; as it is now: a changed command asks
        again). fingerprint: the row's and the summary's, as the player saw them: when the extension is something
        else by now the answer is changed (409) and nothing is approved. Its row, with installed: what became of
        its game addons, which go into the game's AddOns with the
        yes (None: nothing was tried; {ok: true, addons, restart, hint}; {ok: false, code, message, addons}). The
        host starts it then: for good when it starts with 无限工坊 (start "workshop"), else,
        when it never said what it can do, only to learn that; its apps are declared"""
        row = await asyncio.to_thread(self.local.approve, id, "player", fingerprint)
        eid = row["id"]
        self.notes.forgive(eid)
        await self.reconcile_local_apps()
        return dict(await asyncio.to_thread(self.local.get, eid), installed=row.get("installed"))

    async def extension_stop(self, id):
        """stops a local extension (shutdown, then its process tree): its row. One in the game only has no process:
        its row with stopped false and a note"""
        row = await asyncio.to_thread(self.local.stop, id)
        if row.get("where") == "game":
            row = dict(row, stopped=False, note="it is in the game only and has no process to stop: the player turns "
                                                "it off in the game (/wk), extension_set {\"enabled\": false} makes "
                                                "the workshop leave it alone, extension_remove takes its addons out")
        return row

    async def extension_set(self, id, enabled=None, options=None):
        """turns a local extension on or off, and sets some of its options: its row. On: its apps are declared; off:
        WuxianKit forgets them (now while the game is online, else at its next kit.ready)"""
        row = await asyncio.to_thread(self.local.set, id, enabled, options)
        if enabled is not None:
            self.notes.forgive(row["id"])
            await self.reconcile_local_apps()
        return row

    async def extension_check(self, id):
        """a local extension (an id, or the full path of a folder) checked without touching its running process:
        {ok, problems, capabilities, tests?}. One in the game only: its manifest and each of its addons' sources
        checked as `check` checks an addon (agent/lint.py; nothing is asked of the game): addons [{folder, path,
        ok, errors, warnings}], ok false when one has errors"""
        res = await asyncio.to_thread(self.local.check, id)
        if res.get("where") == "game":
            for a in res.get("addons") or []:
                try:
                    report = await asyncio.to_thread(lint.check, a["path"], self.game_addons_known())
                    a.update(ok=not report["errors"], errors=report["errors"], warnings=report["warnings"])
                except (lint.LintError, OSError) as e:
                    a.update(ok=False, errors=[dict(file="", line=0, code="unreadable", message=str(e), hint="")],
                             warnings=[])
            res["ok"] = bool(res["ok"]) and all(a["ok"] for a in res.get("addons") or [])
        return res

    async def extension_logs(self, id, since=0, limit=200):
        """a local extension's output by line: {lines: [{n, text}], next, total, reset, cut, row}"""
        return await asyncio.to_thread(self.local.logs, id, since, limit)

    async def extension_pack(self, id, to=None):
        """a local extension's folder as a package, one .zip another computer's extension_add takes (Host.pack):
        {id, version, file, size, files, unpacked, sha256, skipped, summary, warnings}; summary: what whoever gets
        it will see of it, warnings: what to look at before it is sent. to: a folder or the .zip's path (a full
        path); not given: %WUXIAN_HOME%\\packages"""
        return await asyncio.to_thread(self.local.pack, id, to)

    async def extension_summary(self, id=None, path=None):
        """the facts of an extension before it is approved, shared or replaced (Host.summary, local/summary.py): of a
        registered one (id), or of what lies at a full path: a package (.zip; nothing is installed), an extension's
        folder, an addon folder in the game's AddOns that gives WuxianKit an extension"""
        if id is None and path is None:
            raise ApiError(400, "bad_request", "id: a registered extension, or path: the full path of a package, an "
                                               "extension's folder or a game addon's folder")
        return await asyncio.to_thread(self.local.summary, id, path)

    async def extension_install(self, id):
        """a local extension's game addons put into the game's AddOns now (Host.install): {id, addons, restart,
        hint}"""
        return await asyncio.to_thread(self.local.install, id)

    async def new_extension(self, id, title=None, folder=None, addon=None, where=None):
        """a new local extension from the template (scaffold.new_extension_files) in folder (default
        %WUXIAN_HOME%\\extensions\\<id>; new or empty), registered: {row, files, guide}. addon (a folder name): it
        brings a game half too, addons/<addon> (a WuxianKit extension <id>_game), which extension_load installs.
        where (scaffold.extension_where): game (in the game only: the WuxianKit extension <id> in addons/<addon>, no
        process; it is used at once, as an addon new_addon makes), local or both; None: both with an addon, else
        local"""
        if addon is not None and (not isinstance(addon, str) or not addon.strip()):
            raise ApiError(400, "bad_request", "addon: the folder name of the extension's game addon (WuxianVoice)")
        addon = addon.strip() if addon else None
        if where is not None and not isinstance(where, str):
            raise ApiError(400, "bad_request", "where: game, local or both")
        try:
            where, addon = scaffold.extension_where(id, addon, where)
        except scaffold.ScaffoldError as e:
            raise ApiError(400, getattr(e, "code", "bad_request"), str(e)) from None
        taken = self.local.taken_ids()
        bad = local_manifest.id_problem(id, taken=local_manifest.not_own(taken, [addon]) if where == "game" else taken)
        if bad:
            raise ApiError(400, "bad_request", f"id: {bad['message']}: {bad['hint']}")
        if title is not None and not isinstance(title, str):
            raise ApiError(400, "bad_request", "title: text")
        if folder is None or (isinstance(folder, str) and not folder.strip()):
            from ..paths import home
            path = home() / "extensions" / id
        elif not isinstance(folder, str) or not Path(folder.strip()).is_absolute():
            raise ApiError(400, "bad_request", "folder: the full path of the new extension's folder (new or empty)")
        else:
            path = Path(folder.strip())
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise ApiError(409, "folder_not_empty", f"{path} is not empty: give another folder, or extension_add an "
                                                    f"extension that is there")
        make = getattr(scaffold, "new_extension_files", None)
        if make is None:
            raise ApiError(501, "not_available", "this build of 无限工坊 has no extension template "
                                                 "(scaffold.new_extension_files)")
        try:
            files = await asyncio.to_thread(make, str(path), id, title, addon=addon, taken=taken, where=where)
        except scaffold.ScaffoldError as e:
            raise ApiError(400, getattr(e, "code", "bad_request"), str(e)) from None
        self.journal.add("INFO", f"new local extension {id} in {path}" + (" (in the game only)" if where == "game"
                                                                          else ""))
        row = await self.extension_add(str(path))
        if where == "game":
            return dict(row=row, files=[str(f) for f in files or []],
                        guide=f"Read {path}\\addons\\{addon}\\AGENT.md and guide {{\"topic\": \"game-extension\"}}. "
                              f"Edit addons\\{addon}\\{addon}.lua, then extension_load {{\"id\": \"{id}\"}} (it checks "
                              f"the files, installs the addon into the game's AddOns and runs it in the running game), "
                              f"call its wk_{id}_ tools (list the tools again), read errors and logs when something "
                              f"fails. The game lists a new addon when it starts: extension_wait {{\"id\": \"{id}\"}} "
                              f"waits for what the player has to do (the row's player)")
        return dict(row=row, files=[str(f) for f in files or []],
                    guide=f"Read {path}\\AGENT.md and guide {{\"topic\": \"local-extension\"}} (the whole "
                          f"chapter). Edit main.py, then "
                          f"extension_load {{\"id\": \"{id}\"}} (it checks, restarts and runs its tests), call its "
                          f"wk_{id}_ tools, read extension_logs when something fails"
                          + (f". Its game half is addons\\{addon}: extension_load installs it into the game's AddOns "
                             f"(the game finds a new addon when it starts)" if addon else ""))

    async def reaction_call(self, cap, args, caller):
        """(the reactions) a capability called for a reaction: its result"""
        return (await self.local_call(cap, args, None, caller))["result"]

    async def reactions_hear(self, entries):
        """(the reactions) the game sources they hear changed: the temporary app "reactions" opened with them as its
        hear (kit.app.open; again at each kit.ready), stopped when there are none. The game away: owed, and done
        when the link is back (link_came_up) or at its kit.ready. WuxianKit's refusal is an ApiError not_heard with
        its words"""
        self.reaction_hears = [e for e in entries or [] if isinstance(e, str)]
        if not self.link_is_up():
            self.reactions_owed = True
            return
        self.reactions_owed = False
        if not self.reaction_hears:
            if self.reactions_open:
                try:
                    await self.call_exposed(KIT, "kit.app.stop", {"app": REACTIONS_APP}, 5000)
                except ApiError:
                    pass
            self.reactions_open = False
            return
        decl = dict(app=REACTIONS_APP, lease=0, minutes=REACTIONS_MINUTES,     # plumbing: a temporary app, not shown
                    temp=dict(title="反射", hear=list(self.reaction_hears), hidden=True, digest=False))
        try:
            await self.call_exposed(KIT, "kit.app.open", decl, 10000)
        except ApiError as e:
            if e.code == "call_failed":
                raise ApiError(400, "not_heard", str(e.message or "").partition("\n")[0]) from None
            return
        self.reactions_open = True

    async def reactions_hear_again(self, owed=False):
        """kit.ready: the reactions' app opened again in the new UI session. owed (the link is back): only when
        what they hear changed while the game could not be told"""
        if not (self.reactions_owed if owed else self.reaction_hears):
            return
        try:
            await self.reactions_hear(list(self.reaction_hears))
        except ApiError as e:
            self.journal.add("INFO", f"reactions: the game did not take what they hear: {e.message}")

    async def reactions(self):
        """{reactions: [...]}"""
        return dict(reactions=self.reactor.list())

    async def reaction_set(self, spec):
        """a reaction set (one with its id replaced): that reaction. ApiError bad_args with the engine's words"""
        try:
            return await self.reactor.set(spec)
        except ReactionError as e:
            raise ApiError(400, "bad_args", str(e)) from None

    async def reaction_clear(self, id=None, owner=None):
        """{cleared: n}: the reaction id, the reactions of owner (an app id), or (neither) all"""
        if id is not None and not isinstance(id, str) or owner is not None and not isinstance(owner, str):
            raise ApiError(400, "bad_request", "id, owner: texts")
        return dict(cleared=await self.reactor.clear(id=id, owner=owner))

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
                if set(names) - set(report["unconfirmed"]):   # the Usage strings' names alone: nothing to say offline
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
        try:
            data = code.encode("utf-8")
        except UnicodeEncodeError:
            raise ApiError(400, "bad_request", "code: not valid text (a lone surrogate)") from None
        timeout = self.timeout_seconds(timeout_ms, 10000)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        def start():
            comp = self.worker.companion()
            self.ready(comp, "run")
            job = comp.code(data, chunk, addon or "-")
            self.pending[job] = (loop, fut)       # on the worker thread, before any RUN result can come
            return job

        job = await self.call(start)
        try:
            res = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise ApiError(504, "timeout", f"no result for job {job} within {int(timeout * 1000)} ms: "
                                           f"{await self.give_up(job)}", job=job) from None
        finally:
            self.pending.pop(job, None)           # also when the caller went away
        return self.run_result(res)

    def ready(self, comp, what):
        """(worker thread) the link takes `what` now, or an ApiError says why not: the mailbox of this game process is
        full, or no frames come (the game is not running, is minimized, at the character screen, on a loading screen)"""
        if getattr(comp, "full", False):
            raise ApiError(409, "mailbox_full", f"{what}: the mailbox of this game process is full (all {MB.POOL} slots "
                                                f"used), nothing reaches the game until it is fully restarted (a /reload "
                                                f"does not help)")
        if not getattr(comp, "link_up", True):
            raise ApiError(409, "link_down", f"{what}: no frames come from the game (not running, minimized, at the "
                                             f"character screen or on a loading screen); try again once `status` says "
                                             f"the link is online")

    async def give_up(self, job):
        """a job nobody waits for any more: its parts still in the outbox are taken out; what that means, in words"""
        try:
            gone, sent = await self.call(lambda: self.worker.companion().withdraw(job))
        except ApiError:
            return "is the game running with WoWBridge online?"
        if sent:
            return "it went to the game and may still run there (see logs)"
        return "it never reached the game and will not run" + (" (taken back)" if gone else "") + \
            "; is the game running with WoWBridge online?"

    def session_started(self, sid, last_job):
        """(worker thread) a new UI session's HELLO: the jobs up to the newest one it says ran (last_job) ran in the
        session before, whose results went with it; whoever waits for one hears so now rather than at its timeout. The
        new session may run another WuxianKit, or none: the views of it ask again"""
        self.kit_cache.forget()
        self.kit_changed()
        if not last_job:
            return
        for job in [j for j in list(self.pending) if j <= last_job]:      # a copy: the asyncio side pops too
            waiter = self.pending.pop(job, None)
            if waiter is None:
                continue
            loop, fut = waiter
            res = dict(ok=False, job=job, chunk=None, stack="",
                       error=f"it ran in the game, but the UI reloaded (now session {sid}) before its result came back")
            try:
                loop.call_soon_threadsafe(_resolve, fut, res)
            except RuntimeError:
                pass

    async def caught_up(self, wait=6.0):
        """after a try's end marker came back: whether every message the game sent before it is in, waiting up to `wait`
        seconds (the marker, a RUN result, goes up before the game's other output; a lost frame comes again later)"""
        def mark():
            comp = self.worker.comp
            return comp.mark() if comp is not None and hasattr(comp, "mark") else None

        def covered(sid, msg):
            comp = self.worker.comp
            return comp is None or comp.covered(sid, msg)

        if self.worker is None:
            return True
        at = await self.call(mark)
        if at is None:
            return True
        deadline = time.time() + wait
        while not await self.call(covered, *at):
            if time.time() >= deadline:
                return False
            await asyncio.sleep(0.1)
        return True

    @staticmethod
    def load_files(addons, target, mode):
        """what a load sends (worker of asyncio.to_thread: reading the .toc and listing the folder is disk work):
        (the target's path, its Lua files in .toc order, the addon or None for a file, their bytes); ApiError when the
        target cannot be loaded"""
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
        return path, files, addon, sum(f.stat().st_size for f in files if f.is_file())

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
        path, files, addon, size = await asyncio.to_thread(self.load_files, Path(w.addons), target, mode)
        names = [str(f) for f in files]
        # about 3.7 KB of Lua go into the game a second: a big addon gets the time it takes, whatever timeout_ms says
        timeout = min(max(timeout, 5 + size / 2500), MAX_TIMEOUT_MS / 1000)
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
            self.ready(comp, f"load {target}")
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
        try:
            for i, (job, fut) in enumerate(zip(jobs, futs)):
                name = names[i] if i < len(names) else None
                try:
                    res = await asyncio.wait_for(fut, max(0.0, deadline - loop.time()))
                except asyncio.TimeoutError:
                    results.append(dict(file=name, job=job, ok=False, error=f"timeout: {await self.give_up(job)}",
                                        stack=None, ms=None))
                    continue
                done += 1
                results.append(dict(file=name or chunk_file(res.get("chunk") or ""), job=job, ok=res["ok"],
                                    error=res.get("error"), stack=res.get("stack"), ms=res.get("ms"),
                                    values=res.get("values")))
        finally:
            for job in jobs:                   # also when the caller went away
                self.pending.pop(job, None)
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

    SNAP_WAIT, SNAP_AGAIN = 3.0, 0.1   # a snap waits this long at most for WoWBridge's frame to rest, looking this often

    @staticmethod
    def snap_format(format):
        """a snap's format as asked ("png", the default, or "jpeg"), or 400"""
        if format in (None, ""):
            return "png"
        if format not in ("png", "jpeg"):
            raise ApiError(400, "bad_request", 'format: "png" (the default) or "jpeg"')
        return format

    async def snap(self, region=None, max_width=1280, format=None):
        """a picture of the client area (or the region of it) in the snaps folder: a PNG, or with format "jpeg" a JPEG
        (agent/snap.py: a third of the bytes, encoded ten times faster; what the MCP tools send). WoWBridge's frame is
        in it where the player put it; one that shows a message going up is bigger than at rest, over the UI next to
        it, and the answer the daemon has just read stays up 0.2 s at least (after an inspect or a try nearly always):
        while the region shows such a frame the picture is taken again, SNAP_WAIT seconds at most. link_frame: where the
        frame shows in the picture, busy when it still showed a message"""
        format = self.snap_format(format)
        if region is not None:
            if not (isinstance(region, (list, tuple)) and len(region) == 4
                    and all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in region)):
                raise ApiError(400, "bad_request", "region: [x, y, w, h] in client pixels, or null for the whole client area")
            region = list(region)
        if max_width is not None and not (isinstance(max_width, int) and not isinstance(max_width, bool) and 16 <= max_width <= 8192):
            raise ApiError(400, "bad_request", "max_width: 16..8192 pixels, or null to keep the size")

        def grab():                       # on the worker thread: the window's picture, nothing more (it reads the link too)
            w = self.worker
            if w.win is None:
                raise ApiError(409, "no_game", "no game window: nothing to snap")
            if w.win.minimized:
                raise ApiError(409, "minimized", "the game window is minimized: nothing is drawn")
            try:
                return capture(w.win, w.wgc), w.wgc is not None, f"{w.win.w}x{w.win.h} client area"
            except SnapError as e:
                raise ApiError(409, "snap_failed", str(e)) from None

        loop = asyncio.get_running_loop()
        until = loop.time() + self.SNAP_WAIT
        while True:
            img, wgc, client = await self.call(grab)
            found = await asyncio.to_thread(link_frame, img)
            size = (img.shape[1], img.shape[0])
            busy = found is not None and not found[1] and in_picture(found[0], region, size) is not None
            if not busy or loop.time() >= until:
                break
            await asyncio.sleep(self.SNAP_AGAIN)
        try:
            path, width, height = await asyncio.to_thread(save_snap, img, region, max_width, client, format)
        except SnapError as e:
            raise ApiError(409, "snap_failed", str(e)) from None
        self.journal.add("SNAP", f"{path} {width}x{height}" + ("" if wgc else " (GDI: anything over the game is in it)")
                         + (f" (WoWBridge's frame still showed a message after {self.SNAP_WAIT:g} s)" if busy else ""))
        out = dict(path=str(path), width=width, height=height, url=f"/api/snaps/{path.name}", format=format)
        at = in_picture(found[0], region, size, (width, height)) if found is not None else None
        if at is not None:
            out["link_frame"] = dict(rect=at, busy=busy)
        return out

    async def snap_bytes(self, result):
        """the file of a snap() result, PNG or JPEG (the MCP tool returns it as an image block)"""
        return await asyncio.to_thread(Path(result["path"]).read_bytes)

    async def reload(self, reason=""):
        if reason is None:
            reason = ""
        if not isinstance(reason, str):
            raise ApiError(400, "bad_request", "reason: a string")

        def do():
            comp = self.worker.companion()
            self.ready(comp, "reload")
            n = len(comp.outbox)
            comp.command("reload", reason)         # a nonce (and the reason) after it; the addon asks the user once
            nonce = next((int(d[7:].split()[0]) for k, d in comp.outbox[n:] if k == MB.COMMAND and d.startswith(b"reload ")),
                         None)
            self.journal.add("RELOAD", "requested" + (f": {reason}" if reason else "") +
                             " (the addon shows a button; the UI reloads when the user clicks it)")
            return dict(requested=True, nonce=nonce)

        return await self.call(do)

    async def say(self, text):
        if not isinstance(text, str) or not text:
            raise ApiError(400, "bad_request", "text: what to show in the game's chat")

        if len(text) > 4000:
            raise ApiError(400, "bad_request", "text: at most 4,000 characters for the chat")

        def do():
            comp = self.worker.companion()
            self.ready(comp, "say")
            comp.say(text)
            self.journal.add("INFO", f"say: {text}")
            return dict(queued=True)

        return await self.call(do)

    async def logs(self, since=0, limit=200, kinds=None, tail=False):
        """the journal's entries from id `since` on, oldest first, at most `limit` (1..1000); tail: the newest `limit`
        of them (still oldest first), for what happened lately. next: the `since` of the next read"""
        try:
            since, limit = int(since or 0), int(limit or 200)
        except (TypeError, ValueError):
            raise ApiError(400, "bad_request", "since and limit: integers") from None
        if isinstance(kinds, str):
            kinds = [k.strip().upper() for k in kinds.split(",") if k.strip()]
        entries, next_id, truncated = self.journal.since(max(since, 0), max(1, min(limit, 1000)), kinds or None,
                                                         tail=bool(tail))
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
        """GET: the settings; POST: change some (mode, capture, game_dir, language, font_scale: the page's size
        in per cent, onboarded: the 开始 page's steps were done or put off, extension_approval: "ask" or "auto", who
        approves a local extension's command) and keep them. A change of
        mode also installs what that mode needs (developer: WoWBridge and its mailbox; player: removes them); the
        answer then carries that install's result as `install`"""
        stored = self.stored_settings()
        mode_before, approval_before = self.mode, self.extension_approval
        if update:
            if not isinstance(update, dict):
                raise ApiError(400, "bad_request", "a JSON object of settings")
            if "capture" in update and update["capture"] not in ("gdi", "wgc"):
                raise ApiError(400, "bad_request", 'capture: "gdi" or "wgc"')
            if "mode" in update and update["mode"] not in ("player", "developer"):
                raise ApiError(400, "bad_request", 'mode: "player" or "developer"')
            if "extension_approval" in update and update["extension_approval"] not in EXTENSION_APPROVALS:
                raise ApiError(400, "bad_request", 'extension_approval: "ask" or "auto"')
            if "language" in update and update["language"] not in ("auto", "zh-CN", "en"):
                raise ApiError(400, "bad_request", 'language: "auto", "zh-CN" or "en"')
            if "font_scale" in update:                   # the page sends a select's value: "125" or 125
                scale = update["font_scale"]
                if isinstance(scale, str) and scale.isdigit():
                    scale = int(scale)
                if isinstance(scale, bool) or scale not in FONT_SCALES:
                    raise ApiError(400, "bad_request", "font_scale: " + ", ".join(map(str, FONT_SCALES)))
                update = dict(update, font_scale=scale)
            if "game_dir" in update and update["game_dir"] is not None and not isinstance(update["game_dir"], str):
                raise ApiError(400, "bad_request", "game_dir: the client folder, or null")
            if "capture" in update:
                self.worker.capture_wanted = update["capture"]
            if "mode" in update:
                self.mode = update["mode"]
                self.worker.link = update["mode"] == "developer"
            if "game_dir" in update:
                self.game_dir = update["game_dir"] or None
            if "extension_approval" in update:
                self.extension_approval = update["extension_approval"]
            for key in ("language", "font_scale", "onboarded"):
                if key in update:
                    stored[key] = update[key]
            stored.pop("autostart", None)                # 0.9.13 and before kept a 开机自启 that started nothing
            if "language" in update:                     # what the program says from now on (i18n.py)
                i18n.set_language(update["language"])
            stored.update(mode=self.mode, capture=self.worker.capture_wanted, game_dir=self.game_dir,
                          extension_approval=self.extension_approval)
            path = settings_file()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
            self.journal.add("INFO", "settings: " + ", ".join(f"{k}={v}" for k, v in update.items()))
            if self.extension_approval != approval_before:   # what is allowed changed: the rows (the page reads them
                await asyncio.to_thread(self.local.changed)  # again) and the apps the extensions declare
                await self.reconcile_local_apps()
        answer = dict(mode=self.mode, capture=self.worker.capture_wanted, capture_in_use=self.worker.capture,
                      game_dir=self.game_dir, extension_approval=self.extension_approval,
                      language=stored.get("language", "auto"), onboarded=bool(stored.get("onboarded", False)),
                      font_scale=stored["font_scale"] if stored.get("font_scale") in FONT_SCALES else 100)
        if update and self.mode != mode_before:
            try:
                answer["install"] = await self.install()
            except (ApiError, FileNotFoundError, ValueError) as e:     # no client folder yet: the setting still holds
                answer["install"] = dict(available=False, detail=getattr(e, "message", None) or str(e))
        return answer
