"""The MCP server: the daemon's operations as tools for coding agents (Claude Code, Codex, Cursor), one tool per endpoint
of the HTTP interface (daemon/server.py), snake_case; the read-only ones carry readOnlyHint, reload and install destructiveHint.

build_server(backend) makes it over any object with the async methods of daemon.service.Service (status, run, load,
watch, snap, snap_bytes, trace, inspect, try_, check, history, checkpoint, restore, reload, logs, say, doctor, addons,
errors, new_addon, install). The daemon mounts it at /mcp (Streamable HTTP) over the
Service itself; `wuxian mcp` (main) serves it on stdio for hosts that start the server themselves, over HttpBackend,
which calls the running daemon through cli.client (and starts it when it is not running). On stdio, stdout carries the
protocol: logging goes to stderr and, with WUXIAN_LOG (or --log), to that file.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .. import __version__, apidocs
from ..daemon.api import ApiError

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)

INSTRUCTIONS = """WuxianWorkshop (无限工坊): a live link to a running World of Warcraft client with the WoWBridge addon.
`run` executes Lua inside the game and returns the values; `check` finds what would fail in the game before it gets
there (Lua 5.1 syntax, globals and APIs this client does not have, typos, accidental globals, restricted events, the
.toc and XML), and `load` / `watch` check the syntax themselves and send nothing broken; `load` hot-loads a file or an
addon (its .toc order) without a /reload; `watch` reloads files whenever they are saved; `logs` has the addon's Lua errors (with stacks), print output
and the RUN results; `snap` is a screenshot of the game window; `reload` asks the player to click a reload button (only
a click may reload on this client). Check `status` first: the game must be running with the link online.
`try` does something in the game (Lua or a slash command) and brings back what came of it in one call: its result,
the errors with stacks, prints and blocked actions of the next seconds, the events that fired, a picture: use it to test
a feature and to see whether a fix worked. `trace` records the events the game fires for a while (which event to
handle, what its payload is); `inspect` describes a frame, or the frames under the mouse (layout bugs). Test without mouse and keyboard: call the slash handler
(`SlashCmdList.NAME("args")`), your button's `:Click()`, your handler with made-up arguments. Never register restricted
events (COMBAT_LOG_EVENT_UNFILTERED and the like): the client blocks the addon with a dialog. When something seems
blocked or does nothing, read `logs` (ERR, BLOCKED) and `snap` the screen: the game shows many warnings as dialogs.
Each `load`, the start of a `watch` and every save a watch loads first keep a version of the addon (`history` lists them,
`restore` goes back); `checkpoint` an addon you did not make before you change it, so the player's original is kept.
`api_search` / `api_get` / `api_manual` read this client's own API manual (built in, no game needed): look an API up
there before using it, and `run` it in the game when in doubt. `new_addon` makes a new addon from a template (with an
AGENTS.md on how to work on it)."""


class HttpBackend:
    """the Service's methods over HTTP, for the stdio server (cli.client.DaemonClient underneath). The daemon may
    restart while this server runs, on a new port with a new token: a request that found nobody listening, or that
    was turned away for its token, did nothing, so it is sent once more to the daemon that runs now (reconnect():
    cli.client.connect, which also starts one). Other failures are reported, never repeated (a run may have run)."""

    def __init__(self, client, reconnect=None):
        self.client, self.reconnect = client, reconnect

    async def _call(self, name, *args):
        from ..cli.client import DaemonGone
        for attempt in range(2):
            try:
                return await asyncio.to_thread(getattr(self.client, name), *args)
            except DaemonGone as e:
                failure = ApiError(503, "daemon_unreachable", str(e))
            except ConnectionError as e:
                raise ApiError(503, "daemon_unreachable", str(e)) from None
            except ApiError as e:
                if e.status != 401:
                    raise
                failure = e
            if attempt or self.reconnect is None:
                raise failure
            try:
                self.client = await asyncio.to_thread(self.reconnect)
            except ConnectionError as e:
                raise ApiError(503, "daemon_unreachable", str(e)) from None

    async def status(self):
        return await self._call("status")

    async def run(self, code, timeout_ms=10000, addon=None):
        return await self._call("run", code, timeout_ms, addon)

    async def load(self, target, mode=None, reset=True, timeout_ms=30000, check=True):
        return await self._call("load", target, mode, reset, timeout_ms, check)

    async def check(self, target, live=True):
        return await self._call("check", target, live)

    async def try_(self, code=None, slash=None, seconds=2, addon=None, snap=False, frame=None, events=None, timeout_ms=10000):
        return await self._call("try_", code, slash, seconds, addon, snap, frame, events, timeout_ms)

    async def watch(self, action="list", target=None):
        return await self._call("watch", action, target)

    async def snap(self, region=None, max_width=1280):
        return await self._call("snap", region, max_width)

    async def snap_bytes(self, result):
        return await self._call("get_bytes", result["url"])

    async def trace(self, seconds=10, events=None, max_events=200, args=6):
        return await self._call("trace", seconds, events, max_events, args)

    async def inspect(self, target=None, mouse=False, depth=1, snap=False):
        return await self._call("inspect", target, mouse, depth, snap)

    async def history(self, addon=None, vid=None, against="now"):
        return await self._call("history", addon, vid, against)

    async def checkpoint(self, addon, note=""):
        return await self._call("checkpoint", addon, note)

    async def restore(self, addon, vid):
        return await self._call("restore", addon, vid)

    async def reload(self, reason=""):
        return await self._call("reload", reason)

    async def logs(self, since=0, limit=200, kinds=None):
        return await self._call("logs", since, limit, kinds)

    async def say(self, text):
        return await self._call("say", text)

    async def doctor(self, game_dir=None):
        return await self._call("doctor", game_dir)

    async def addons(self, game_dir=None):
        return await self._call("addons", game_dir)

    async def errors(self, addon=None, limit=100, game_dir=None):
        return await self._call("errors", addon, limit, game_dir)

    async def new_addon(self, name, title=None, notes="", template="basic"):
        return await self._call("new_addon", name, title, notes, template)

    async def install(self, game_dir=None, clean=True):
        return await self._call("install", game_dir, clean)


def build_server(backend, name="wuxian"):
    """the MCPServer with the tools, over a Service or an HttpBackend"""
    mcp = MCPServer(name, instructions=INSTRUCTIONS, version=__version__, log_level="WARNING")

    async def call(coro):
        try:
            return await coro
        except ApiError as e:
            raise ToolError(f"{e.code}: {e.message}") from None

    @mcp.tool(annotations=READ_ONLY)
    async def status() -> dict[str, Any]:
        """The daemon, the game window and the link: game.found / game.build, link.state (online = frames are coming
        in, so run / load work), link.slots_left (mailbox slots left in this game process), watch (files reloaded on
        save), reload_pending. Call this first when a run or load times out."""
        return await call(backend.status())

    @mcp.tool(annotations=WRITES)
    async def run(code: str, timeout_ms: int = 10000, addon: str | None = None) -> dict[str, Any]:
        """Run Lua inside the game (hot: no /reload) and wait for the result. `return` values come back as strings
        (tables are dumped, 3 levels deep); a Lua error raises with its message and stack. `addon` gives the code that
        addon's name and namespace as `...` (what its files get when loaded). Times out when the link is offline."""
        res = await call(backend.run(code, timeout_ms, addon))
        if not res.get("ok"):
            raise ToolError(f"Lua error in job {res.get('job')}: {res.get('error')}\n{res.get('stack') or ''}".rstrip())
        return res

    @mcp.tool(annotations=WRITES)
    async def load(target: str, reset: bool = True, timeout_ms: int = 30000, check: bool = True) -> dict[str, Any]:
        """Hot-load a Lua file or a whole addon (every Lua file its .toc lists, in order; XML <Script> files
        followed) into the running game. `target`: a path, or a name relative to Interface/AddOns or the client folder.
        Returns one result per file (ok, error, stack, ms); a file that fails does not stop the next ones. WoWBridge's
        own files are refused. The files are checked first (`check`: errors and warnings, see the check tool): a Lua
        5.1 syntax error in any of them sends none (check_failed, with the file and line). A version of the addon is
        kept first (`kept`: its id; `history`, `restore`)."""
        return await call(backend.load(target, None, reset, timeout_ms, check))

    @mcp.tool(annotations=READ_ONLY)
    async def check(target: str, live: bool = True) -> dict[str, Any]:
        """Check an addon (or one file) before it goes to the game, in milliseconds, without running it: `errors` (would
        fail in the game: syntax with Lua 5.1, the client's own version, with a hint for what newer Lua allows and 5.1
        does not; libraries and functions this client lacks: os, io, utf8, require, table.unpack...; unknown event names
        in RegisterEvent; a .toc line naming a missing file; XML that is not well-formed; with `live`, globals that are
        nil in the running game), `warnings` (probable bugs: a name one slip from a real one, C_ / Enum names the API
        manual does not have, globals written by accident, protected functions, restricted events, not UTF-8, an
        Interface number not this client's) and `notes`. Each finding has file, line, code, message and hint. With
        `live` (and the game online) the names neither the manual nor the addon settle are asked of the running game:
        nil there is an error, present means fine (the Blizzard UI or another addon has it). Run it after every
        change, before load or reload."""
        return await call(backend.check(target, live))

    @mcp.tool(annotations=WRITES)
    async def watch(action: Literal["start", "stop", "list"] = "list", target: str | None = None) -> dict[str, Any]:
        """Reload files into the game whenever they are saved: start watching a file or an addon folder, stop (all,
        or the target), or list what is watched. The results arrive in `logs` as WATCH and RUN entries. Starting keeps
        a version of the addon (the files before you edit them), and so does every save it loads."""
        return await call(backend.watch(action, target))

    @mcp.tool(annotations=WRITES)
    async def snap(region: list[int] | None = None, max_width: int = 1280) -> list[Image | str]:
        """A screenshot of the game window's client area (PNG; the image block plus its path). `region`: [x, y, w, h]
        in client pixels for a part of it. The picture is scaled down to `max_width` pixels wide."""
        res = await call(backend.snap(region, max_width))
        png = await call(backend.snap_bytes(res))
        return [Image(data=png, format="png"), f"{res['path']} ({res['width']}x{res['height']})"]

    @mcp.tool(annotations=WRITES)
    async def trace(seconds: float = 10, events: str | None = None, max_events: int = 200, args: int = 6) -> dict[str, Any]:
        """Record the game's events for `seconds` (1-120) while the player does something, or after you `run` code:
        each kept event with its time (s from the start), name and first `args` arguments, and how often every event
        fired (`counts`, the most frequent first). `events`: names or globs, comma-separated ("BAG_*, LOOT_OPENED",
        "UNIT_SPELLCAST_*"); without it every event an addon may register is counted and all but the chattiest are
        kept (UNIT_AURA, cursor and power updates are only counted), and the start takes a few seconds longer. The
        restricted events (COMBAT_LOG_EVENT_UNFILTERED and the like) are never registered: the client blocks an addon
        that registers them. Use it to find the event to handle and what its payload looks like before writing the
        handler, or to see whether your code fired what it should."""
        return await call(backend.trace(seconds, events, max_events, args))

    @mcp.tool(name="try", annotations=WRITES)
    async def try_(code: str | None = None, slash: str | None = None, seconds: float = 2, addon: str | None = None,
                   snap: bool = False, frame: str | None = None, events: str | None = None) -> list[Image | str]:
        """Do something in the game and see what came of it, in one call. The action: `code` (Lua; `addon` gives it
        that addon's namespace as `...`) or `slash`, a slash command line ("/myaddon show": the handler the addon
        registered is called with "show"). Then for `seconds` (0-30, default 2) everything the game reports is
        collected: `action` (the returned values, or its error and stack), `errors` (Lua errors with their stacks and
        the addon they came from: timers, events and OnUpdate code run later too), `prints`, `warnings`, `blocked`
        (ADDON_ACTION_BLOCKED / FORBIDDEN), `events` (with `events`: names or globs to record, "BAG_*, LOOT_OPENED", or
        "all"), and a picture at the end (`snap`: the screen; `frame`: a Lua expression of a frame, e.g.
        "MyAddonFrame", pictured with a margin, with its rect). `ok`: the action ran and nothing errored or was
        blocked; `summary` says it in one line. The window holds everything the game sent, the player's own actions
        too: look at each entry's addon. Use it to test a feature without mouse and keyboard, and after a fix to see
        whether the error is gone."""
        res = await call(backend.try_(code, slash, seconds, addon, snap, frame, events))
        shot = res.pop("snap", None)
        out = [json.dumps(res, ensure_ascii=False)]
        if shot:
            out.append(Image(data=await call(backend.snap_bytes(shot)), format="png"))
        return out

    @mcp.tool(annotations=READ_ONLY)
    async def inspect(target: str | None = None, mouse: bool = False, depth: int = 1, snap: bool = False) -> list[Image | str]:
        """Describe frames of the running UI. `target`: a Lua expression that gives a frame or a region (PlayerFrame,
        MyAddonFrame, MyAddon.window, _G["Name"]): its type and name, shown / visible, alpha, size, `rect` ([x, y, w, h]
        in client pixels from the top-left, as `snap` takes it), anchors (point, relative frame, relative point, x, y),
        strata, level, draw layer, text or texture, protected, mouse-enabled, which scripts are set, and with `depth`
        (0-3) its children and regions. `mouse=true` instead: the frames under the mouse cursor, each with its parent
        chain (ask the player to hover the thing in question). `snap=true` adds a picture of the first frame. For
        layout bugs: is it shown and visible, is its rect on the screen, which anchor puts it there."""
        res = await call(backend.inspect(target, mouse, depth, snap))
        shot = res.pop("snap", None)
        out = [json.dumps(res, ensure_ascii=False)]
        if shot:
            out.append(Image(data=await call(backend.snap_bytes(shot)), format="png"))
        return out

    @mcp.tool(annotations=READ_ONLY)
    async def history(addon: str | None = None, id: int | None = None, against: str | int = "now") -> dict[str, Any]:
        """The kept versions of an addon. One is kept before its code goes to the game (each `load`, the start of a
        `watch`, every save a watch loads, `new_addon`) and when asked (`checkpoint`). No addon: the addons that have
        some. An addon: its versions, newest first (id, time, reason, note, the files added / changed / removed), and
        `now`: how its files differ from the latest one. With `id`: what changed from that version to the files now,
        as unified diffs (`against`="prev": what that version itself changed; or another version's id)."""
        return await call(backend.history(addon, id, against))

    @mcp.tool(annotations=WRITES)
    async def checkpoint(addon: str, note: str = "") -> dict[str, Any]:
        """Keep a version of an addon's files as they are now, with a note ("before the bag rewrite"). Do it before
        you change an addon you did not make, so the player can go back to the original: loads and watches keep
        versions by themselves, but only once you load or watch. Nothing new is kept when the files are the latest
        version's (new: false)."""
        return await call(backend.checkpoint(addon, note))

    @mcp.tool(annotations=DESTRUCTIVE)
    async def restore(addon: str, id: int) -> dict[str, Any]:
        """Put an addon's files back as a kept version had them (`history` lists them): the files that differ are
        written, those the version did not have are removed. The files as they are now are kept first (`saved`:
        restoring that one undoes this). The game still runs the code it has: `load` the addon, or `reload` (a
        watched addon, `watched`: true, gets the files written loaded again by its watch)."""
        return await call(backend.restore(addon, id))

    @mcp.tool(annotations=DESTRUCTIVE)
    async def reload(reason: str = "") -> dict[str, Any]:
        """Ask for a UI reload (/reload): the addon shows a button in the game, the UI reloads when the player clicks
        it (this client lets only a click reload). `status.reload_pending` stays true until then; the RELOAD entry in
        `logs` reports done / later. Use `load` instead whenever hot-loading is enough."""
        return await call(backend.reload(reason))

    @mcp.tool(annotations=READ_ONLY)
    async def logs(since: int = 0, limit: int = 200, kinds: str | None = None) -> dict[str, Any]:
        """The daemon's log: Lua errors of every addon with their stacks (ERR), print output (OUT), warnings (WARN),
        blocked actions (BLOCKED), RUN results, RELOAD / WATCH / SNAP / SLOTS reports, the companion's notes (INFO).
        Entries have increasing ids: pass `since` = the `next` of the last call to read on. `kinds`: a comma-separated
        filter, e.g. "ERR,OUT"."""
        return await call(backend.logs(since, limit, kinds))

    @mcp.tool(annotations=WRITES)
    async def say(text: str) -> dict[str, Any]:
        """Show a line of text in the game's chat window (the player sees it; nothing runs)."""
        return await call(backend.say(text))

    @mcp.tool(annotations=READ_ONLY)
    async def doctor() -> dict[str, Any]:
        """The self-check: is the game folder found, the addon installed and current, the daemon able to read the
        window, and so on; each check has ok, detail and a fix."""
        return await call(backend.doctor())

    @mcp.tool(annotations=READ_ONLY)
    async def addons() -> dict[str, Any]:
        """The addons installed in the game's Interface/AddOns: each one's .toc title, version, Interface numbers and
        whether they are the client's (current), dependencies, load on demand, whether AddOns.txt disables it, and how
        many errors the player-side collector (!WuxianWorkshop) recorded for it as of the last logout or /reload."""
        return await call(backend.addons())

    @mcp.tool(annotations=READ_ONLY)
    async def errors(addon: str | None = None, limit: int = 50) -> dict[str, Any]:
        """The Lua errors the player-side collector (!WuxianWorkshop) kept in its SavedVariables, newest first: one
        entry per error signature with its message, addon, first stack, count, first / last time and client build.
        `addon`: only that addon's (its folder name). These are as of the last logout or /reload; the live ones of the
        current session are in `logs` (ERR)."""
        return await call(backend.errors(addon, limit))

    @mcp.tool(annotations=DESTRUCTIVE)
    async def install(game_dir: str | None = None, clean: bool = True) -> dict[str, Any]:
        """(Re)install the WoWBridge addon into the game's Interface/AddOns (the running game's folder, or
        `game_dir`); `clean` removes files of older versions. New files need a full restart of the game."""
        return await call(backend.install(game_dir, clean))

    @mcp.tool(annotations=WRITES)
    async def new_addon(name: str, title: str | None = None, notes: str = "",
                        template: Literal["basic", "window"] = "basic") -> dict[str, Any]:
        """Make a new addon in the game's AddOns folder from a template: <name>/<name>.toc (Interface 16001,
        SavedVariables <name>DB), <name>.lua (a slash command /<name lowercase>, the hot-reload hooks OnUnload /
        OnReload already in place; "window" adds a draggable window) and AGENTS.md (how to work on it: load, logs,
        run, snap, this client's rules). `name` is the folder name: letters, digits, _ (2-40, a letter first); `title`
        the name shown in the game. `load <name>` hot-loads it at once; the game lists it after a full restart."""
        return await call(backend.new_addon(name, title, notes, template))

    @mcp.tool(annotations=READ_ONLY)
    async def api_search(query: str, kind: Literal["function", "event", "table"] | None = None,
                         limit: int = 20, call: Literal["usable", "ok", "limited", "protected"] | None = None) -> dict[str, Any]:
        """Search this client's API manual (1.60.1 无限, Lua 5.1, the Mainline 12.x UI code; built into 无限工坊,
        no game needed): functions (C_ namespaces and globals), events and tables (enums, structures) by name or by
        words of their description, exact names first. Each result has its signature and flags (protected: secure
        code only; may return secret values: in combat, encounters, PvP ...). `api_get` gives one entry in full,
        `api_manual` the rules (taint, secret values, the .toc, protected functions, GameRules, the 无限-only API). The
        manual is the client's own documentation; to be sure in the running game, `run` `return type(C_X.Y)`.
        Every result says how far an addon may use it (`call`, `why`: the documentation fields): ok; limited (usage
        restrictions, secret values in restricted states, a precondition, a callback-only event); protected (secure code
        only: an addon's call is blocked, a restricted event cannot be registered). call="usable" leaves the protected out."""
        ix = await asyncio.to_thread(apidocs.index)
        return dict(results=ix.search(query, kind, limit, call), manual=ix.about()["version"])

    @mcp.tool(annotations=READ_ONLY)
    async def api_get(name: str) -> dict[str, Any]:
        """One entry of the API manual in full: a function ("C_Spell.GetSpellInfo", or a global like "UnitHealth":
        arguments, returns with types and nilability, flags and the raw documentation fields), an event (payload) or
        a table (an enum's values, a structure's fields). A short name shared by several namespaces returns the
        candidates."""
        ix = await asyncio.to_thread(apidocs.index)
        entry = ix.get(name)
        if entry is None:
            raise ToolError(f"not_found: {name} is not in the API manual; api_search finds near names")
        return entry

    @mcp.tool(annotations=READ_ONLY)
    async def api_manual(topic: str | None = None) -> dict[str, Any]:
        """The manual's topics (no topic: the list) or one topic as Markdown: runtime, stdlib, globals, taint, secret,
        quotas, cvars, toc, load-switches, protected, secure-templates, gamerules, forever-api (an id, a title or a
        word in it)."""
        ix = await asyncio.to_thread(apidocs.index)
        found = ix.manual(topic)
        if found is None:
            raise ToolError(f"not_found: no manual topic {topic!r}; api_manual() lists them")
        return found if isinstance(found, dict) else dict(topics=found, about=ix.about())

    return mcp


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wuxian mcp", description="the MCP server on stdio, talking to the daemon over HTTP")
    ap.add_argument("--no-start", action="store_true", help="do not start the daemon when it is not running")
    ap.add_argument("--log", default=os.environ.get("WUXIAN_LOG"), help="also log to this file (default: $WUXIAN_LOG)")
    args = ap.parse_args(argv)
    handlers = [logging.StreamHandler(sys.stderr)]
    if args.log:
        handlers.append(logging.FileHandler(args.log, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("wuxian.mcp")
    from ..cli.client import connect                   # stdout stays clean: connect() reports through the logger
    try:
        client = connect(start=not args.no_start, log=logger.info)
    except ConnectionError as e:
        logger.error(str(e))
        return 2
    logger.info(f"serving the tools on stdio for the daemon at {client.base}")
    backend = HttpBackend(client, reconnect=lambda: connect(start=not args.no_start, log=logger.info))
    build_server(backend).run()                       # stdio
    return 0


if __name__ == "__main__":
    sys.exit(main())
