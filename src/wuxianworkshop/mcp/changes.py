"""Telling MCP clients that the lists of tools, resources and prompts changed (WuxianKit's wk_ tools come and go with the
game: mcp/kit.py), in both eras of the protocol.

- 2026-07-28, the modern wire (Claude Code): the client opens a `subscriptions/listen` stream and hears the events
  published on the MCPServer's subscription bus; MCPServer serves that method, and the modern initialize result derives its
  listChanged flags from it.
- The handshake era (2025-11-25 and before: the SDK's own client, agents not on the new wire yet): the SDK takes the
  listChanged flags from NotificationOptions (all off by default) and never hands the bus to these connections, so such a
  client kept the wk_ tools of its connect for good. ListChanges, a server middleware, keeps a session of every
  handshake-era connection it sees and sends them notifications/tools|resources|prompts/list_changed; advertise() makes
  their initialize result say listChanged for the three lists.
"""
import logging
import weakref

from mcp.server.lowlevel.server import NotificationOptions
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

logger = logging.getLogger("wuxian.mcp")


class ListChanges:
    """MCPServer(middleware=[ListChanges()]): one session per handshake-era connection, the latest request's. Sent without a
    request id, its notifications ride the connection's own channel (stdio's pipe, streamable HTTP's GET stream), so they
    reach the client after that request has ended. The connection is the SDK's (a private attribute of the session); it
    is forgotten when it closes (its exit stack unwinds; the session holds the connection, so weak keys alone would keep
    it), or on its first failed send."""

    def __init__(self):
        self.sessions = weakref.WeakKeyDictionary()

    async def __call__(self, ctx, call_next):
        if ctx.protocol_version not in MODERN_PROTOCOL_VERSIONS:
            connection = getattr(ctx.session, "_connection", None)
            if connection is not None:
                if connection not in self.sessions:
                    stack = getattr(connection, "exit_stack", None)
                    if stack is not None:
                        stack.callback(self.sessions.pop, connection, None)
                self.sessions[connection] = ctx.session
        return await call_next(ctx)

    async def tell(self, mcp):
        """every client hears that the tools, resources and prompts changed: the modern ones on the subscription bus, the
        handshake-era ones by notification"""
        bus = getattr(mcp, "_subscriptions", None)
        if bus is not None:
            from mcp.server.subscriptions import PromptsListChanged, ResourcesListChanged, ToolsListChanged
            for event in (ToolsListChanged(), ResourcesListChanged(), PromptsListChanged()):
                try:
                    await bus.publish(event)
                except Exception:                          # nobody listening, or a client gone
                    pass
        for connection, session in list(self.sessions.items()):
            try:
                await session.send_tool_list_changed()
                await session.send_resource_list_changed()
                await session.send_prompt_list_changed()
            except Exception as e:                         # the client went away
                logger.debug(f"list_changed not sent: {e!r}")
                self.sessions.pop(connection, None)


def advertise(mcp):
    """the handshake-era initialize result says listChanged for the tools, resources and prompts (stdio's run and the
    streamable HTTP runner both ask the lowlevel server for its initialization options when a client connects)"""
    low = mcp._lowlevel_server
    make = low.create_initialization_options

    def create_initialization_options(notification_options=None, *args, **kwargs):
        every = NotificationOptions(prompts_changed=True, resources_changed=True, tools_changed=True)
        return make(notification_options or every, *args, **kwargs)

    low.create_initialization_options = create_initialization_options
