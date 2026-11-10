"""Who uses this MCP server, for the App's 扩展 page (Agent 接入): Presence, a server middleware, keeps a record per
agent: its client (the name and version it gave at initialize or in the 2026-07-28 envelope: may be missing), its
protocol, its first and last request, its tool calls, and what its last tools/list gave it: how many wk_ tools, made from
which WuxianKit manifest. On the 2026-07-28 wire a connection lasts one request (stdio and HTTP alike), so a record is
not a connection: a stdio server serves one agent, its process one record (single); the daemon's /mcp (HTTP) keeps one
per client name and version, until it has been idle IDLE seconds. On stdio, server.Reporter sends the records to the
daemon now and then (POST /api/mcp/report) with what the process is and serves; the daemon's own /mcp server is read in
place (Service.agent_access).

Presence also keeps what each MCP session registered itself: the ids of the local extensions it made or added
(new_extension, extension_add). An agent may set the options of those and of no others (server.extension_set). A session
is the stdio process; over HTTP a handshake-era connection, or on the 2026-07-28 wire (no connection outlives a request)
the client's name and version: two clients of one name and version share theirs there."""
import contextvars
import threading
import time
import weakref

from mcp_types import CLIENT_INFO_META_KEY
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

PREFIX = "wk_"                                         # WuxianKit's tools (mcp/kit.py)
MINE = contextvars.ContextVar("wuxian_mcp_registered", default=None)       # what this request's session registered (a set)


def client_of(ctx):
    """{name, version} of the client that sent this request, or None: the handshake's (the session's), the 2026-07-28
    envelope's, or initialize's own params (the session has them only after it)"""
    params = ctx.params if isinstance(ctx.params, dict) else {}
    info = getattr(getattr(ctx.session, "client_params", None), "client_info", None)
    if info is not None:
        info = {"name": getattr(info, "name", None), "version": getattr(info, "version", None)}
    else:
        meta = params.get("_meta")
        info = meta.get(CLIENT_INFO_META_KEY) if isinstance(meta, dict) else None
        if info is None and ctx.method == "initialize":
            info = params.get("clientInfo")
    if not isinstance(info, dict) or not isinstance(info.get("name"), str) or not info["name"]:
        return None
    version = info.get("version")
    return {"name": info["name"], "version": version if isinstance(version, str) else None}


class Presence:
    """MCPServer(middleware=[..., Presence(single)]): single, the process serves one agent (stdio). kit: the server's
    KitTools (set by build_server), whose manifest revision a tool list is made from"""
    IDLE = 1800                                        # seconds without a request before an HTTP client is no longer listed

    def __init__(self, single=False):
        self.single, self.records, self.kit = single, {}, None
        self.lock = threading.Lock()                   # the reporter reads from its own thread
        self.own = set()                               # single: what the one session registered
        self.by_connection = weakref.WeakKeyDictionary()                   # HTTP, the handshake era: connection -> set
        self.by_client = {}                            # HTTP, the 2026-07-28 wire: (name, version) -> set, while listed

    async def __call__(self, ctx, call_next):
        if ctx.request_id is None:                     # a notification: no envelope says who sent it
            return await call_next(ctx)
        client, now = client_of(ctx), time.time()
        token = MINE.set(self.session(ctx, client))
        try:
            return await self.seen(ctx, call_next, client, now)
        finally:
            MINE.reset(token)

    def session(self, ctx, client):
        """the set of what this request's MCP session registered, or None: nothing says whose the request is"""
        if self.single:
            return self.own
        if ctx.protocol_version not in MODERN_PROTOCOL_VERSIONS:
            connection = getattr(ctx.session, "_connection", None)
            if connection is None:
                return None
            try:
                return self.by_connection.setdefault(connection, set())
            except TypeError:                          # a connection that cannot be a weak key
                return None
        if client is None:
            return None
        with self.lock:
            return self.by_client.setdefault((client["name"], client["version"]), set())

    def mine(self):
        return self.own if self.single else MINE.get()

    def registered(self, eid):
        """this request's session registered the local extension `eid` (new_extension, extension_add)"""
        mine = self.mine()
        if mine is not None and isinstance(eid, str):
            mine.add(eid)

    def removed(self, eid):
        """the local extension `eid` is off the registry: no session's any more (another of that id is not theirs)"""
        with self.lock:
            sets = [self.own, *self.by_client.values(), *self.by_connection.values()]
        for mine in sets:
            mine.discard(eid)

    def registers(self, eid):
        """whether this request's session registered the local extension `eid` itself"""
        mine = self.mine()
        return mine is not None and eid in mine

    async def seen(self, ctx, call_next, client, now):
        if client is None and not self.single and ctx.method not in ("tools/list", "tools/call"):
            return await call_next(ctx)                # a probe or a ping of nobody known: not an agent of its own
        key = "process" if self.single else (client["name"], client["version"]) if client else None
        with self.lock:
            record = self.records.get(key)
            if record is None:
                self.prune(now)                            # a new agent: the idle ones go first (HTTP: one per client)
                record = self.records[key] = {"client": None, "first": now, "calls": 0, "listed": None, "tools": None,
                                              "revision": None}
            if client is not None:
                record["client"] = client
            record["protocol"], record["last"] = ctx.protocol_version, now
            if ctx.method == "tools/call":
                record["calls"] += 1
        result = await call_next(ctx)
        if ctx.method == "tools/list" and isinstance(result, dict) and isinstance(result.get("tools"), list):
            given = sum(1 for t in result["tools"] if isinstance(t, dict) and self.counts(str(t.get("name", ""))))
            more = isinstance(ctx.params, dict) and ctx.params.get("cursor")       # a later page adds to the first
            manifest = getattr(self.kit, "manifest", None) or {}
            with self.lock:
                record["tools"] = (record["tools"] or 0) + given if more else given
                record["revision"], record["listed"] = manifest.get("revision"), time.time()
        return result

    def counts(self, name):
        """whether a listed tool is one of the game's WuxianKit tools: the KitTools' own (a local extension's wk_ tools
        are not: an agent of a game without WuxianKit has them too); without a KitTools, any wk_ tool"""
        kit_tools = getattr(self.kit, "tools", None)
        if kit_tools is None:
            return name.startswith(PREFIX)
        return name in {getattr(t, "name", None) for t in kit_tools}

    def prune(self, now):
        """(under the lock) an HTTP client idle IDLE seconds is no longer listed, and what it registered is nobody's;
        a stdio process's one agent stays"""
        if not self.single:
            for key in [k for k, r in self.records.items() if now - r["last"] > self.IDLE]:
                del self.records[key]
                self.by_client.pop(key, None)

    def view(self):
        """the agents, earliest first: {client {name, version} or None, protocol, first, last, calls, listed (when it
        last listed the tools, None: not seen), tools (the wk_ tools it got), revision (of the manifest they came from)}"""
        with self.lock:
            self.prune(time.time())
            records = [dict(r) for r in self.records.values()]
        return sorted(records, key=lambda r: r["first"])
