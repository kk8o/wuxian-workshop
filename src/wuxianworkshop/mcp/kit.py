"""WuxianKit's capabilities as MCP tools of their own (wk_<extension>_<name>), beside the workshop's tools. When the game
runs WuxianKit (无限工坊's standard library, an addon of its own), the tool list carries one tool per capability that is
live there, its arguments typed from the capability's declaration (kit.capabilities: "string", "number?", "a|b",
"string|number"); a Say or Do one takes the proposal options too (_title, _ttl, _after) and answers with the proposal
the player confirms in the game. The list is read from the game when a client asks for the tools (kept CACHE seconds;
while the game is away the last one stands, or none); calling one is `call WuxianKit <capability>` under its own name."""
import json
import time

from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent, Tool, ToolAnnotations

from ..daemon.api import ApiError

ADDON, PREFIX, CACHE = "WuxianKit", "wk_", 30
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False)
TYPES = {"string": ["string"], "number": ["number"], "boolean": ["boolean"], "table": ["object", "array"], "any": None}
KINDS = {"see": "reads the game, at once", "keep": "keeps data on this computer, at once",
         "point": "points at something for the player, changes nothing",
         "say": "a proposal (a message) the player confirms", "do": "a proposal (a change in the game) the player confirms"}
OPTIONS = {
    "_title": {"type": "string", "description": "the proposal's title in the game"},
    "_ttl": {"type": "number", "description": "seconds it waits for the player (10-600, 300 when left out)"},
    "_after": {"type": "number", "description": "a time() before which it may not run"},
}


def tool_name(cap_id):
    return PREFIX + cap_id.replace(".", "_")


def schema(cap):
    """the JSON schema of a capability's arguments, from their spec texts"""
    props, required = {}, []
    for name, spec in sorted((cap.get("args") or {}).items()):
        optional = spec.endswith("?")
        parts = (spec[:-1] if optional else spec).split("|")
        if all(p in TYPES for p in parts):
            kinds = []
            for p in parts:
                if TYPES[p] is None:                               # any
                    kinds = None
                    break
                kinds += TYPES[p]
            props[name] = {} if kinds is None else {"type": kinds[0] if len(kinds) == 1 else kinds}
        else:
            props[name] = {"type": "string", "enum": parts}        # the strings it may be
        if not optional:
            required.append(name)
    if cap.get("kind") in ("say", "do"):
        props.update(OPTIONS)
    out = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


def describe(cap):
    kind = cap.get("kind")
    text = f"WuxianKit {cap['id']} ({cap.get('title') or ''}), {KINDS.get(kind, kind)}: {cap.get('doc') or ''}"
    if kind in ("say", "do"):
        text += (" Answers with the proposal {id, state, steps}: the player confirms it in the game (unless they let its "
                 "scope go without asking); its end is the event kit.proposal (`events`, addon WuxianKit).")
    return text


class KitTools:
    """the capability tools of the WuxianKit the game runs, read through a backend (Service or HttpBackend)"""

    def __init__(self, backend):
        self.backend, self.tools, self.ids, self.at = backend, [], {}, None

    async def read(self, force=False):
        if not force and self.at is not None and time.monotonic() - self.at < CACHE:
            return self.tools
        try:
            api = await self.backend.addon_api(ADDON)
            exposed = {f.get("name") for f in (api or {}).get("exposed") or [] if isinstance(f, dict)}
            caps = []
            if "kit.capabilities" in exposed:
                answer = await self.backend.call_exposed(ADDON, "kit.capabilities", None, 20000)
                caps = (answer or {}).get("result") or []
        except ApiError:
            return self.tools                                      # the game is away: the last list stands
        tools, ids = [], {}
        for cap in caps if isinstance(caps, list) else []:
            if not isinstance(cap, dict) or not isinstance(cap.get("id"), str):
                continue
            name = tool_name(cap["id"])
            ids[name] = cap["id"]
            tools.append(Tool(name=name, title=cap.get("title"), description=describe(cap), input_schema=schema(cap),
                              annotations=READ_ONLY if cap.get("kind") == "see" else WRITES))
        self.tools, self.ids, self.at = tools, ids, time.monotonic()
        return tools

    async def call(self, name, arguments):
        cap = self.ids.get(name)
        if cap is None:
            await self.read(force=True)
            cap = self.ids.get(name)
        if cap is None:
            raise ToolError(f"no WuxianKit capability for {name} now (its extension may be off, or the game away)")
        try:
            result = await self.backend.call_exposed(ADDON, cap, arguments or {}, 30000)
        except ApiError as e:
            raise ToolError(f"{e.code}: {e.message}") from None
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                              structured_content=result if isinstance(result, dict) else {"result": result})


def attach(mcp, backend):
    """mcp lists and calls WuxianKit's capability tools beside its own"""
    kit = KitTools(backend)
    own_list, own_call = mcp.list_tools, mcp.call_tool

    async def list_tools():
        return await own_list() + await kit.read()

    async def call_tool(name, arguments, context=None):
        if name.startswith(PREFIX):
            return await kit.call(name, arguments)
        return await own_call(name, arguments, context)

    mcp.list_tools, mcp.call_tool = list_tools, call_tool
    return kit
