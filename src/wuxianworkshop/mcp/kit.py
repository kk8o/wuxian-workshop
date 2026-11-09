r"""WuxianKit (the extension framework, an optional addon of its own) for the agent, by its Agent access spec: the game's
WuxianKit gives a manifest (kit.manifest: protocol, its version, the language, a revision, every extension with its state
and its docs folder under Interface\AddOns, and for those that are on their capabilities and the events they emit). This
module turns it into MCP:
  - a tool per capability, wk_<extension>_<name> (dots to _), its arguments typed from the declaration ("string",
    "number?", "a|b"); Read ones are read-only, a Send or Change one takes the proposal options (_title, _ttl, _after)
    and answers with the proposal the player confirms in the game;
  - wk_docs (an extension's AGENT.md, its tools, its events and its sources, what an app hears from it; without one,
    the overview: the extensions, the local extensions, the skills, and the apps, an app with a prompt started by its
    skill, with how to wait for their wakes, `wuxian listen <id>`, and how to listen for a while through a temporary
    app), wk_wait (a proposal's outcome) and wk_propose (several steps in one proposal);
  - the resources wuxian://kit and wuxian://kit/<extension> (the same docs), and a prompt per skill (skills/*.md of a
    docs folder: the manifest's apps', then the local extensions', then those of the extensions that are on, an app's
    skills its own; a skill's front matter also says what the client needs, `needs: shell`, and the other MCP servers it
    uses, `mcp: <name> <https URL>`), which wk_docs {skill, arguments} gives too, its arguments filled in the same way.
The local extensions (local/, the same model with another runtime: a process on this computer that 无限工坊 runs) come
from backend.local_manifest() ({revision, extensions: [an extension entry as the kit's, runtime "local", its docs and
skills as absolute paths]}): their capabilities are wk_<extension>_<name> tools too, called through backend.local_call,
documented, listed and given skills by the same code as the kit's. They run without the game, so they stay listed and
callable while it is away or has no WuxianKit; a backend without local_manifest (an older daemon, a test's stand-in) has
none. They are read again when the daemon's events say local.changed with another revision. An entry's extends ({skill
name: absolute path}) supplements a skill of that name, whoever's it is: wk_docs {skill} and the prompt append each
file as a section of its own (by extension id), and the overview says which skills have one; its annotate ([{app,
call, wake, timeout}]: the daemon adds the call's answer to that app's wakes as notes.<extension>) is told in its line.
Docs and skills are read from the addon folders, not over the game's link. The manifest is kept on disk with its revision.
It is read again when the game tells of another revision (kit.changed, kit.ready: a watch on the events, which then tells
the clients that the lists changed), when the game's UI session changed (a reload or a restart may run another WuxianKit,
or none), or when kit.revision answers another one (asked at most every RECHECK seconds, every AWAY seconds while it does
not answer). One ask at a time: a list gives what is known at once and the clients hear when the ask changed it (only
with nothing known yet does it wait, at most FIRST_WAIT seconds). Every view asks the same after a reload (the daemon's
own and each stdio server's), so its asks are SHARED: the daemon asks the game once for all (daemon/kitcache.py), and
answers the others with what that UI session told it. The game is asked only while the AddOns folder holds
WuxianKit, and then first what it exposed (addon_api: an addon that is not running fails no call, so nothing fails in the
log and no run counts in the game). WuxianKit is optional: whatever goes wrong with it (the game away, an answer this
module cannot read) leaves the App's own tools alone, keeps the last manifest and asks again AWAY seconds later. While
the game is away the last manifest stands and calling its tools fails. A WuxianKit from before the spec still gets its
tools, from kit.capabilities; one of a later protocol than this module knows gets wk_docs only (and no prompts), which
says to update 无限工坊. The kept manifest goes when the game answers without WuxianKit, the AddOns folder has none, or
the App removes it (forget_cached; a session that kept it sees the file gone). Another addon's extension says where it
comes from: verified when the extension catalog, as the App last read it, lists its addon."""
import asyncio
import contextvars
import json
import logging
import os
import re
import time
from pathlib import Path

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp_types import (CallToolResult, GetPromptResult, Prompt, PromptArgument, PromptMessage, Resource, TextContent,
                       Tool, ToolAnnotations)

from .changes import ListChanges
from ..daemon.api import ApiError
from ..daemon.kitcache import SHARED  # noqa: F401  (backends read it as kit.SHARED)
from ..extensions import installed as folder_holds, listed_folders
from ..paths import state_dir

logger = logging.getLogger("wuxian.mcp")

ADDON, PREFIX, PROTOCOL, URI = "WuxianKit", "wk_", 1, "wuxian://kit"
RECHECK, AWAY = 600, 30                     # seconds: ask the game for the revision again after; while it is away
FIRST_WAIT = 8                              # seconds a list waits for the first answer when nothing is known yet
GONE = re.compile(r"WuxianKit has no ")     # a call's failure that says WuxianKit (or that capability) is not running
ASK_EVERY = 15                              # seconds wk_wait waits for an event before it asks the game itself
POLL = 10                                   # seconds between the watch's asks when it may not wait for events
# the calls of the watch: in the background, so they never start the daemon (a daemon the player quit stays quit; a
# client's own call starts one, as before). HttpBackend reads it
PASSIVE = contextvars.ContextVar("wuxian_kit_passive", default=False)
INTERNAL = {"kit.manifest", "kit.revision", "kit.capabilities"}             # read by this module, not tools
FINAL = {"done", "failed", "declined", "expired", "undone"}
MAX_DOC, MAX_SKILLS = 64 * 1024, 20
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False)
TYPES = {"string": ["string"], "number": ["number"], "boolean": ["boolean"], "table": ["object", "array"], "any": None}
KINDS = {"see": ("Read", "returns data at once; changes nothing"),
         "keep": ("Store", "saves or deletes WuxianKit records on this computer at once; the game itself is not changed"),
         "point": ("Guide", "shows the player something (a highlight, a note, a tour, a map pin) at once; changes no "
                            "setting"),
         "say": ("Send", "a proposal: the message goes out when the player clicks Run (执行), or at once if they "
                         "pre-approved its category"),
         "do": ("Change", "a proposal: the game changes when the player clicks Run (执行), or at once if they "
                          "pre-approved its category")}
PROPOSES = ("Proposal", "makes a proposal of saved steps at once; nothing changes until the player clicks Run (执行), "
                        "or at once if every step is pre-approved")   # a capability with proposes
ANSWERS = (" Returns a proposal {id, state, steps}. If state is done, it already ran (pre-approved); otherwise tell the "
           "player what you proposed and call wk_wait with {\"proposal\": id} for the outcome.")
LOCAL = "local"                             # the daemon's events of the local extensions: addon "local", topic local.*
LOCAL_KINDS = {"see": ("Read", "returns data at once; changes nothing"),
               "keep": ("Store", "saves or deletes data on this computer at once; the game is not changed"),
               "point": ("Guide", "shows or plays something for the player at once; changes no setting")}
LOCAL_WHERE = "Runs on this computer and works without the game."
LOCAL_TIMEOUT = 30                          # seconds a local capability's call waits when it declares no timeout


def kind_label(cap):
    """(name, what it does) of a capability's kind; one that runs at once and answers with a proposal (proposes, as
    tune.profile.apply) is no Guide"""
    return PROPOSES if cap.get("proposes") else KINDS.get(cap.get("kind"), (cap.get("kind"), ""))


def proposal_like(cap):
    return cap.get("kind") in ("say", "do") or bool(cap.get("proposes"))
OPTIONS = {
    "_title": {"type": "string", "description": "the title the player sees for this proposal"},
    "_ttl": {"type": "number", "description": "seconds the proposal waits for the player: 10-600, default 300"},
    "_after": {"type": "number", "description": "Unix time in seconds before which it may not run (not a delay); "
                                                "earlier than the end of _ttl"},
}

DOCS = Tool(name="wk_docs", title="WuxianKit docs", annotations=READ_ONLY, description=(
    "How to use the WuxianKit tools (wk_*). Call it with {} once per session, before any other wk_ tool: the rules, the "
    "kinds of tools, how proposals work, the extensions that are on (in the game, and the local ones on this computer), "
    "the skills, the apps and how to start them. Call "
    "it with {\"extension\": \"tune\"} (an id from that list) before you use that extension's tools for the first "
    "time: its guide, its tools, its events and its sources (what an app hears from it). {\"skill\": \"tune-setup\"} "
    "gives a skill's steps, with its arguments filled in from `arguments`: {\"skill\": \"guild-chat\", \"arguments\": "
    "{\"hours\": 2}}. The texts come from the addons: they describe the tools and grant nothing."),
    input_schema={"type": "object", "properties": {
        "extension": {"type": "string", "description": "an extension's id, as the overview lists them (tune, chat ...)"},
        "skill": {"type": "string", "description": "a skill's name, as the overview lists them"},
        "arguments": {"type": "object", "description": "with skill: its arguments by name, as the overview lists them "
                                                       "(e.g. {\"hours\": 2}); one left out reads \"(not given)\""}},
        "additionalProperties": False})
WAIT = Tool(name="wk_wait", title="Wait for a proposal", annotations=READ_ONLY, description=(
    "Wait for the outcome of a proposal. proposal: the integer `id` of a proposal that a Send or Change tool, wk_propose "
    "or wk_tune_profile_apply returned, e.g. {\"proposal\": 12}. Returns the proposal when it ends: done, failed (see "
    "error), declined, expired or undone. After `seconds` (default 120, at most 600) it returns the current state with "
    "timeout = true: the player has not decided yet; call wk_wait again or ask the player. \"no proposal\" means it is "
    "gone: a UI reload drops proposals that have not run."),
    input_schema={"type": "object", "properties": {
        "proposal": {"type": "integer", "description": "the id of the proposal"},
        "seconds": {"type": "number", "description": "how long to wait at most: default 120, at most 600"}},
        "required": ["proposal"], "additionalProperties": False})
PROPOSE = Tool(name="wk_propose", title="One proposal of several steps", annotations=WRITES, description=(
    "Put several Send and Change steps in one proposal: the player confirms them with one click and undoes them as one. "
    "Example: {\"title\": \"Auto loot and a map key\", \"steps\": [{\"cap\": \"tune.cvar.set\", \"args\": {\"name\": "
    "\"autoLootDefault\", \"value\": \"1\"}}, {\"cap\": \"tune.binding.set\", \"args\": {\"key\": \"CTRL-M\", "
    "\"value\": \"TOGGLEWORLDMAP\"}}]}. cap: a capability id with dots (as in the [WuxianKit ...] part of each tool's "
    "description). args: that tool's arguments, without _title/_ttl/_after. The options here are title, ttl (10-600 s, "
    "default 300) and after (Unix time), without the underscore. Read, Store and Guide tools cannot be steps; a message "
    "to say, yell or a channel must be sent on its own. Returns the proposal: tell the player, then call wk_wait."),
    input_schema={"type": "object", "additionalProperties": False, "required": ["steps"], "properties": {
        "steps": {"type": "array", "items": {"type": "object", "required": ["cap"], "properties": {
            "cap": {"type": "string", "description": "a capability id, e.g. tune.cvar.set"},
            "args": {"type": "object", "description": "that capability's arguments"}}}},
        "title": {"type": "string", "description": "the title the player sees"},
        "ttl": {"type": "number", "description": "seconds it waits for the player: 10-600, default 300"},
        "after": {"type": "number", "description": "Unix time before which it may not run (not a delay)"}}})
META = [DOCS, WAIT, PROPOSE]


def newer(manifest):
    """the manifest's protocol when it is a later one than this module knows (a newer WuxianKit), else None"""
    p = (manifest or {}).get("protocol")
    return p if isinstance(p, int) and not isinstance(p, bool) and p > PROTOCOL else None


def update_note(protocol):
    return (f"The game's WuxianKit speaks protocol {protocol}, newer than this 无限工坊 knows ({PROTOCOL}): update "
            "无限工坊 to use its tools. wk_docs still gives its guides.")


def docs_for(protocol):
    """wk_docs while the game's WuxianKit is of a later protocol: the only tool, saying so"""
    return Tool(name=DOCS.name, title=DOCS.title, annotations=READ_ONLY, input_schema=DOCS.input_schema,
                description=f"{DOCS.description} {update_note(protocol)}")


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
    if proposal_like(cap):
        props.update(OPTIONS)
    out = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


POSITION = re.compile(r"(\.lua\]?|\[string [^\]]*\]):\d+:")


def brief_error(message):
    """an extension's own refusal (error(msg, 0): no place in the code) without the Lua stack WoWBridge adds to every
    failed call, which tells the agent nothing; an error with a place (a bug in the addon) keeps it"""
    first, _, rest = str(message or "").partition("\n")
    return message if not rest or POSITION.search(first) else first


def foreign(ext):
    """an extension another addon brings (not one of WuxianKit's own)"""
    return bool(ext) and bool(ext.get("addon")) and ext["addon"] != ADDON


def source(ext, listed=None):
    """where another addon's extension comes from, for the agent: the addon, and whether the extension catalog as the
    App last read it (listed: its addon folders) has it; no verdict without one"""
    if listed is None:
        return f"the addon {ext['addon']}"
    if ext["addon"] in listed:
        return f"the addon {ext['addon']} (verified: listed in the 无限工坊 extension catalog)"
    return f"the addon {ext['addon']} (unverified: not in the 无限工坊 extension catalog)"


CAP_ID = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+\b")


def topics_of(manifest):
    """every event topic the manifest declares (its extensions' events, its apps' app.<id>): a doc's word that is one
    of them names the event, even where a capability has the same id (guild.chat, the tool and the event)"""
    out = set()
    for ext in (manifest or {}).get("extensions") or []:
        if isinstance(ext, dict):
            out.update(v["topic"] for v in ext.get("events") or [] if isinstance(v, dict) and isinstance(v.get("topic"), str))
    out.update(a["topic"] for a in apps_of(manifest) if a["topic"])
    return out


APP_ID = re.compile(r"^[a-z0-9_]{1,24}$")
DRIVERS = ("prompt", "program")             # an older WuxianKit's driver field, passed on as it is; the docs never use it


def apps_of(manifest):
    """the manifest's apps (WuxianKit 0.6: kit.app.open, the events app.<id>) that this module can read, their texts
    on one line; also kit.apps' rows, and a local extension's apps (kit.app.open's fields: `app` for the id). docs: the
    app's docs folder under Interface\\AddOns (an addon's declaration only; kit.apps does not give it); temp: a temporary
    app (an agent's, not remembered). driver and launch: what an older WuxianKit said of a program driving the app (the
    pages may still read them; nothing here does)"""
    apps = (manifest or {}).get("apps")
    out = []
    for a in apps if isinstance(apps, list) else []:
        if isinstance(a, dict) and "id" not in a and isinstance(a.get("app"), str):
            a = dict(a, id=a["app"])                       # a declaration: kit.app.open's fields
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not APP_ID.match(a["id"]):
            continue
        hear = a.get("hear") if isinstance(a.get("hear"), list) else []
        out.append(dict(id=a["id"], title=one_line(text(a.get("title")))[:48] or a["id"], by=a.get("by"),
                        addon=a["addon"] if isinstance(a.get("addon"), str) else None,
                        topic=a["topic"] if isinstance(a.get("topic"), str) else f"app.{a['id']}",
                        hear=[one_line(h) for h in hear if isinstance(h, str)],
                        prompt=a["prompt"] if isinstance(a.get("prompt"), str) else None,
                        start=one_line(a["start"]) if isinstance(a.get("start"), str) and a["start"].strip() else None,
                        extension=a["extension"] if isinstance(a.get("extension"), str) else None,
                        driver=a["driver"] if a.get("driver") in DRIVERS else None,
                        docs=a["docs"] if isinstance(a.get("docs"), str) and a["docs"] else None,
                        temp=a.get("temp") is True, launch=a.get("launch") is True,
                        see=[one_line(f) for f in a["see"] if isinstance(f, str)] if isinstance(a.get("see"), list)
                        else []))
    return out


SOURCE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
MAX_VALUES = 40                             # an extension's docs show at most this many known values of a source


def sources_of(ext):
    """an extension's sources (what an app hears from it, `<name>:<what>` in hear) that this module can read: [{name,
    title, doc, values [str]}], texts on one line"""
    out, sources = [], (ext or {}).get("sources")
    for s in sources if isinstance(sources, list) else []:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not SOURCE.match(s["name"]):
            continue
        values = s.get("values") if isinstance(s.get("values"), list) else []
        out.append(dict(name=s["name"], title=one_line(text(s.get("title"))), doc=one_line(text(s.get("doc"))),
                        values=[one_line(v) for v in values if isinstance(v, (str, int, float))
                                and not isinstance(v, bool) and one_line(v)]))
    return out


def facets_of(ext):
    """an extension's facets (what an app sees of it now: `see` in its declaration, each wake's state.<name>) that this
    module can read: [{name, title, doc, fields}], texts on one line"""
    out, facets = [], (ext or {}).get("facets")
    for f in facets if isinstance(facets, list) else []:
        if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not SOURCE.match(f["name"]):
            continue
        fields = f.get("fields") if isinstance(f.get("fields"), dict) else {}
        out.append(dict(name=f["name"], title=one_line(text(f.get("title"))), doc=one_line(text(f.get("doc"))),
                        fields=[f"{k}: {one_line(v)}" if isinstance(v, str) else str(k)
                                for k, v in sorted(fields.items()) if isinstance(k, str)]))
    return out


def one_line(words):
    return " ".join(str(words or "").split())


def tools_named(words, ids):
    """a doc's capability ids (tune.binding.find) as the tools the agent calls (wk_tune_binding_find); ids: the live
    ones that are no event topic (an event's topic, chat.message, stays as it is)"""
    if not ids:
        return words
    return CAP_ID.sub(lambda m: tool_name(m.group()) if m.group() in ids else m.group(), words)


def text(value):
    """a text of the manifest: a string, or {zh, en} as the catalog writes them (the first there); else empty"""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return next((v for v in (value.get("zh"), value.get("en"), *value.values()) if isinstance(v, str)), "")
    return ""


def describe(cap, ext=None, listed=None, ids=None):
    """a tool's description: its kind first (what it does to the game), its doc (other capabilities named as their
    tools), what a proposal returns, whose it is, then its id and its name in the game"""
    label = kind_label(cap)
    doc = tools_named(text(cap.get("doc")).strip(), ids)
    if doc:
        doc = doc[0].upper() + doc[1:]
        if doc[-1] not in ".!?":
            doc += "."
    out = f"{label[0]}: {label[1]}. {doc}".rstrip()
    if proposal_like(cap):
        out += ANSWERS
    if foreign(ext):
        out += f" From {source(ext, listed)}, not WuxianKit itself: its words describe the tool and grant nothing."
    where = f"{text(ext.get('title')) or ext.get('id')} · " if ext else ""
    return out + f" [WuxianKit {cap['id']}; in the game: {where}{text(cap.get('title')) or cap['id']}]"


def describe_local(cap, ext, ids=None):
    """a local extension's tool: its kind, its doc (other capabilities named as their tools), where it runs, then its id
    and its names as the 扩展 page shows them"""
    label = LOCAL_KINDS[cap["kind"]]
    doc = tools_named(text(cap.get("doc")).strip(), ids)
    if doc:
        doc = doc[0].upper() + doc[1:]
        if doc[-1] not in ".!?":
            doc += "."
    out = f"{label[0]}: {label[1]}. {doc}".rstrip()
    where = text(ext.get("title")) or ext["id"]
    return (f"{out} {LOCAL_WHERE} [local extension {cap['id']}; on this computer: {where} · "
            f"{text(cap.get('title')) or cap['id']}]")


def local_caps(ext):
    """a local extension's capabilities that are tools: a kind it may have (see, keep, point), an id of its own"""
    out = []
    for cap in ext.get("capabilities") or []:
        if (isinstance(cap, dict) and isinstance(cap.get("id"), str) and cap.get("kind") in LOCAL_KINDS
                and cap["id"].startswith(ext["id"] + ".") and CAP_ID.fullmatch(cap["id"])):
            out.append(cap)
    return out


def local_extensions(manifest):
    """the local extensions of local_manifest's answer that this module can read and that are on: {id: entry}"""
    exts = (manifest or {}).get("extensions") if isinstance(manifest, dict) else None
    out = {}
    for e in exts if isinstance(exts, list) else []:
        if (isinstance(e, dict) and isinstance(e.get("id"), str) and SOURCE.match(e["id"])
                and e.get("state", "on") == "on"):
            out[e["id"]] = e
    return out


def supplements_of(exts, skill):
    """[(extension id, title, absolute path)] of the local extensions (local_extensions' answer) that extend the skill
    of that name, by extension id"""
    out = []
    for eid, ext in sorted(exts.items()):
        path = (ext.get("extends") or {}).get(skill) if isinstance(ext.get("extends"), dict) else None
        if isinstance(path, str) and path and os.path.isabs(path):
            out.append((eid, one_line(text(ext.get("title"))) or eid, path))
    return out


def annotations_of(ext):
    """a local extension's annotations it may have: [{app, call}] with a call of its own"""
    items = ext.get("annotate") if isinstance(ext.get("annotate"), list) else []
    return [a for a in items if isinstance(a, dict) and isinstance(a.get("app"), str) and APP_ID.match(a["app"])
            and isinstance(a.get("call"), str) and a["call"].startswith(ext["id"] + ".")]


def read_supplement(path):
    """(text, None) of a supplement file (at most MAX_DOC bytes), or (None, why it cannot be read)"""
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_DOC)
    except OSError as e:
        return None, e.strerror or str(e)
    try:
        words = data.decode("utf-8-sig").replace("\r\n", "\n").strip()
    except UnicodeDecodeError:
        return None, "it is not UTF-8"
    return (words, None) if words else (None, "it is empty")


def supplement_section(eid, title, words, why):
    """a skill's section that a local extension adds"""
    head = f"## 本机扩展「{title}」的补充（On this machine: the local extension {eid}）"
    return f"{head}\n\n" + (words if why is None else f"(This supplement could not be read: {why}.)")


def supplemented(exts, skill):
    """what the overview adds to a skill that local extensions supplement: （本机补充：<ids>）"""
    ids = [eid for eid, _, _ in supplements_of(exts, skill["name"])]
    return f"（本机补充：{'、'.join(ids)}）" if ids else ""


def additions(ext):
    """what a local extension adds to apps and skills, as its lines say it: [text]"""
    out = [f"annotates the wakes of the app `{a['app']}` with {a['call']}" for a in annotations_of(ext)]
    extends = ext.get("extends") if isinstance(ext.get("extends"), dict) else {}
    out += [f"supplements the skill `{name}`" for name in sorted(extends) if isinstance(name, str)]
    return out


def option_line(o):
    """an option of a local extension as its docs list it: [name, kind, value, what]"""
    kind = o.get("kind") if isinstance(o.get("kind"), str) else "?"
    if kind == "choice":
        values = [str(c.get("value")) for c in o.get("choices") or [] if isinstance(c, dict)]
        kind += f" ({' / '.join(values)})" if values else ""
    elif kind == "number":
        bounds = [f"{k} {o[k]}" for k in ("min", "max", "step") if isinstance(o.get(k), (int, float))]
        kind += f" ({', '.join(bounds)})" if bounds else ""
    value = o.get("value", o.get("default"))
    shown = json.dumps(value, ensure_ascii=False) if value is not None else "—"
    if isinstance(o.get("unit"), str) and o["unit"] and value is not None:
        shown += f" {o['unit']}"
    what = "; ".join(x for x in (one_line(text(o.get("title"))), one_line(text(o.get("hint")))) if x)
    return [str(o.get("name")), kind, shown, what]


def from_capabilities(caps):
    """a manifest made of kit.capabilities (a WuxianKit from before the spec): an extension per id prefix"""
    exts = {}
    for cap in caps:
        if isinstance(cap, dict) and isinstance(cap.get("id"), str):
            eid = cap["id"].split(".", 1)[0]
            exts.setdefault(eid, {"id": eid, "state": "on", "capabilities": [], "events": []})["capabilities"].append(cap)
    return {"protocol": 0, "revision": None, "extensions": list(exts.values())}


def cache_path():
    return state_dir() / "kit-manifest.json"


def load_cached(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("extensions"), list) else None


def save_cached(path, manifest):
    """whether it was kept"""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def forget_cached(path=None):
    """the kept manifest gone (the game runs no WuxianKit, or the App removed it): no tools from it until the game
    gives one again; a session that kept it sees the file gone"""
    try:
        Path(path or cache_path()).unlink()
    except OSError:
        pass


# --- the docs and skills in the addon folders ---------------------------------------------------------------------

def inside(addons, rel, *names):
    """a path in a docs folder (rel: under Interface\\AddOns, as the manifest gives it), or None when it leaves it"""
    if not addons or not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
        return None
    base = Path(addons).resolve()
    p = base.joinpath(*rel.split("/"), *names).resolve()
    return p if base in p.parents else None


def read_doc(path):
    """a text file of an addon (at most MAX_DOC bytes), or None"""
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_DOC)
    except (OSError, TypeError):
        return None
    return data.decode("utf-8", "replace").replace("\r\n", "\n")


def front_matter(text):
    """the --- key: value --- lines at the top of a skill, and the rest"""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip().lower()] = value.strip()
    return meta, m.group(2)


NEED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,47}$")
SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SERVER_URL = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[^\s]*)?$")
MAX_NEEDS = 16


def needs_of(value):
    """a skill's needs: what the client must be able to do (shell: run a command in the background), comma-separated;
    words this module does not know kept as they are, what is no word left out"""
    out = []
    for word in str(value or "").split(","):
        word = word.strip()
        if NEED.match(word) and word not in out:
            out.append(word)
    return out[:MAX_NEEDS]


def servers_of(value):
    """a skill's mcp: the MCP servers it uses that are not 无限工坊's, comma-separated `<name> <https URL>`; an item of
    another shape (no name, another scheme, more words) left out: [{name, url}]"""
    out = []
    for item in str(value or "").split(","):
        parts = item.split()
        if (len(parts) == 2 and SERVER_NAME.match(parts[0]) and SERVER_URL.match(parts[1])
                and all(s["name"] != parts[0] for s in out)):
            out.append(dict(name=parts[0], url=parts[1]))
    return out[:MAX_NEEDS]


def skills_in(addons, rel):
    """the skills of a docs folder (skills/*.md, by file name): [{name, title, description, arguments [(name, required)],
    needs [str], mcp [{name, url}], body}]"""
    return skills_from(inside(addons, rel, "skills"))


def skills_from(folder):
    """the skills of a skills folder (a docs folder's skills/, a local extension's skills folder): as skills_in"""
    try:
        if folder is None or not Path(folder).is_dir():
            return []
    except (OSError, TypeError, ValueError):
        return []
    folder, out = Path(folder), []
    for p in sorted(folder.glob("*.md"))[:MAX_SKILLS]:
        text = read_doc(p)
        if text is None:
            continue
        meta, body = front_matter(text)
        name = (meta.get("name") or p.stem).strip().lower()
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,47}$", name):
            continue
        args = []
        for a in (meta.get("arguments") or "").split(","):
            a = a.strip()
            if re.match(r"^[a-z_][a-z0-9_]*\??$", a):
                args.append((a.rstrip("?"), not a.endswith("?")))
        out.append(dict(name=name, title=meta.get("title") or name, description=meta.get("description") or "",
                        arguments=args, needs=needs_of(meta.get("needs")), mcp=servers_of(meta.get("mcp")),
                        body=body.strip()))
    return out


def skill_facts(skill):
    """what the overview adds to a skill's line: its arguments, what it needs, the other MCP servers it uses"""
    facts = []
    if skill["arguments"]:
        facts.append("arguments " + ", ".join(n + ("" if required else "?") for n, required in skill["arguments"]))
    if skill.get("needs"):
        facts.append("needs " + ", ".join(skill["needs"]))
    if skill.get("mcp"):
        facts.append("uses the MCP server" + ("s " if len(skill["mcp"]) > 1 else " ")
                     + ", ".join(f"{m['name']} ({m['url']})" for m in skill["mcp"]))
    return f" ({'; '.join(facts)})" if facts else ""


def owner(skill):
    """whose a skill is, as the overview and the prompts say it: an extension's id, `local <id>` (a local extension's),
    or `app <id>`"""
    if skill.get("app"):
        return f"app {skill['app']['id']}"
    return f"local {skill['ext']['id']}" if skill.get("local") else skill["ext"]["id"]


def filled(value):
    """an argument's value as it goes into a skill's text"""
    if value is None or value == "":
        return "(not given)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def cell(text):
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def specs(d):
    return ", ".join(f"{k}: {v}" for k, v in sorted((d or {}).items())) if isinstance(d, dict) else ""


# --- the tools ------------------------------------------------------------------------------------------------------

class KitTools:
    """WuxianKit in the game as MCP tools, docs and prompts, read through a backend (Service or HttpBackend). store: the
    file of the kept manifest (None: state\\kit-manifest.json; False: none). notify: a coroutine function that tells the
    clients the lists changed. wait: how long the watch waits for an event in one ask (0: it asks every POLL seconds
    instead; over HTTP a long wait holds a thread the process would wait for when it ends)"""

    def __init__(self, backend, store=None, notify=None, wait=300):
        self.backend, self.store, self.notify, self.wait_events = backend, store, notify, wait
        self.manifest, self.loaded, self.stale, self.away, self.checked = None, False, True, False, None
        self.tools, self.ids, self.exts, self.kept, self.listed, self.topics = [], {}, {}, False, None, set()
        self.addons, self.addons_at, self.watching, self.asking, self.session = None, None, None, None, None
        # the local extensions (backend.local_manifest): tools, their capability ids by tool name, the entries by id.
        # self.tools / self.ids stay the game's WuxianKit's (the pages count them as its); listing() gives both
        self.local, self.local_tools, self.local_ids, self.local_exts = None, [], {}, {}
        self.local_stale, self.local_away, self.local_checked = True, False, None
        self.local_asking, self.local_watching = None, None

    def path(self):
        return cache_path() if self.store is None else Path(self.store)

    def use(self, manifest, save=False, kept=False):
        """the manifest in force (None: no WuxianKit), and the tools it makes: each capability apart (one this module
        cannot read is left out, never the others), all of them in force at once (the reporter thread reads them).
        save: the game's answer, kept on disk (without one, what was kept goes); kept: it came from the disk"""
        if not isinstance(manifest, dict) or not isinstance(manifest.get("extensions"), list):
            manifest = None
        tools, ids, exts, listed, later = [], {}, {}, listed_folders(), newer(manifest)
        topics = topics_of(manifest)
        live = {c["id"] for e in (manifest or {}).get("extensions") or [] if isinstance(e, dict) and e.get("state") == "on"
                for c in e.get("capabilities") or [] if isinstance(c, dict) and isinstance(c.get("id"), str)
                and c["id"] not in INTERNAL} - topics
        for ext in (manifest or {}).get("extensions") or []:
            if not isinstance(ext, dict) or not isinstance(ext.get("id"), str):
                continue
            exts[ext["id"]] = ext
            if ext.get("state") != "on" or later:          # a later protocol: its capabilities are no tools here
                continue
            for cap in ext.get("capabilities") or []:
                if (not isinstance(cap, dict) or not isinstance(cap.get("id"), str) or cap["id"] in INTERNAL
                        or cap.get("kind") not in KINDS):           # a kind of a later protocol: left out
                    continue
                try:
                    name = tool_name(cap["id"])
                    tool = Tool(name=name, title=text(cap.get("title")) or None, description=describe(cap, ext, listed, live),
                                input_schema=schema(cap), annotations=READ_ONLY if cap["kind"] == "see" else WRITES)
                except Exception as e:                       # a declaration this module cannot read
                    logger.debug(f"WuxianKit's {cap.get('id')} left out: {e!r}")
                    continue
                ids[name] = cap["id"]
                tools.append(tool)
        if manifest is not None:
            tools += [docs_for(later)] if later else META
        self.manifest, self.tools, self.ids, self.exts, self.listed = manifest, tools, ids, exts, listed
        self.topics = topics
        self.kept = kept and manifest is not None
        if save and self.store is not False:
            if manifest is not None and manifest.get("revision"):
                self.kept = save_cached(self.path(), manifest)
            else:
                forget_cached(self.path())
        if self.local_exts:                                # their docs name the game's tools that are there now
            self.use_local(self.local)

    def use_local(self, manifest):
        """the local extensions in force (local_manifest's answer; one this module cannot read: none) and the tools
        they make, each capability apart, all in force at once"""
        exts = local_extensions(manifest)
        live = (set(self.ids.values()) - self.topics) | {c["id"] for e in exts.values() for c in local_caps(e)}
        tools, ids = [], {}
        for ext in exts.values():
            for cap in local_caps(ext):
                try:
                    name = tool_name(cap["id"])
                    tool = Tool(name=name, title=text(cap.get("title")) or None, description=describe_local(cap, ext, live),
                                input_schema=schema(dict(cap, proposes=False)),
                                annotations=READ_ONLY if cap["kind"] == "see" else WRITES)
                except Exception as e:                       # a declaration this module cannot read
                    logger.debug(f"local {cap.get('id')} left out: {e!r}")
                    continue
                if name not in ids:
                    ids[name] = cap["id"]
                    tools.append(tool)
        self.local = manifest if isinstance(manifest, dict) else None
        self.local_tools, self.local_ids, self.local_exts = tools, ids, exts

    def listing(self):
        """the wk_ tools: the game's WuxianKit's and the local extensions' (a local one wins a name both have, as the
        daemon's local_call does), wk_docs also when only local extensions are there"""
        local = self.local_tools
        if not local and not self.local_exts:
            return self.tools
        names = {t.name for t in local}
        out = [t for t in self.tools if t.name not in names] + local
        if self.manifest is None:
            out.append(DOCS)
        return out

    async def kit(self, name, args=None, timeout_ms=20000):
        answer = await self.backend.call_exposed(ADDON, name, args, timeout_ms)
        return (answer or {}).get("result")

    async def installed(self):
        """whether the AddOns folder holds WuxianKit (None: no folder known; the game decides)"""
        addons = await self.addons_dir()
        if not addons:
            return None
        return (await asyncio.to_thread(folder_holds, addons, ADDON))[0]

    async def exposed(self):
        """the names WuxianKit exposed in the game (none when it took no handle: not running), or None when the backend
        cannot tell (it has no addon_api)"""
        describe_ = getattr(self.backend, "addon_api", None)
        if describe_ is None:
            return None
        answer = await describe_(ADDON, True)                        # brief, the names: the docs come to tens of KB
        if not isinstance(answer, dict):
            return None
        return {e.get("name") for e in answer.get("exposed") or [] if isinstance(e, dict)}

    async def ask(self):
        """asks the game what runs (ask_game). Whatever goes wrong (the game away, the link down, an answer this module
        cannot read) keeps the last manifest and asks again AWAY seconds later. Its asks are SHARED: every view (the
        daemon's own, each stdio server's) asks the same after a reload, and the daemon asks the game once for all"""
        shared = SHARED.set(True)
        try:
            await self.ask_game()
        except Exception as e:
            logger.debug(f"WuxianKit not read now: {e!r}")
            self.away, self.stale, self.checked = True, False, time.monotonic()
            return
        finally:
            SHARED.reset(shared)
        self.away, self.stale, self.checked = False, False, time.monotonic()

    async def ask_game(self):
        """none when the AddOns folder has no WuxianKit (the game is not asked); else what WuxianKit exposed, then its
        revision, and the manifest when that is another; a WuxianKit from before the spec by kit.capabilities"""
        if await self.installed() is False:
            return self.use(None, save=True)
        exposed = await self.exposed()                     # None: this backend cannot tell; kit.revision decides
        if exposed is not None and "kit.revision" not in exposed:
            if "kit.capabilities" in exposed:              # a WuxianKit from before the spec
                caps = await self.kit("kit.capabilities")
                return self.use(from_capabilities(caps) if isinstance(caps, list) else None, save=True)
            return self.use(None, save=True)               # not running: disabled, or a restart away
        try:
            answer = await self.kit("kit.revision", None, 15000)
        except ApiError as e:
            if e.code != "call_failed" or exposed is not None:
                raise
            try:                                           # no kit.revision: none, or a WuxianKit before the spec
                caps = await self.kit("kit.capabilities")
            except ApiError as e2:
                if e2.code != "call_failed":
                    raise
                caps = None
            return self.use(from_capabilities(caps) if isinstance(caps, list) else None, save=True)
        revision = answer.get("revision") if isinstance(answer, dict) else None
        if not isinstance(revision, str):
            raise ValueError(f"kit.revision answered {answer!r:.100}")
        if not self.manifest or revision != self.manifest.get("revision"):
            manifest = await self.kit("kit.manifest", None, 30000)       # failing: as the game away, not an older one
            if not isinstance(manifest, dict) or not isinstance(manifest.get("extensions"), list):
                raise ValueError("kit.manifest answered no manifest")
            self.use(manifest, save=True)

    def due(self):
        return self.stale or self.checked is None or time.monotonic() - self.checked > (AWAY if self.away else RECHECK)

    def signature(self):
        return ((self.manifest or {}).get("revision"), (self.local or {}).get("revision"),
                tuple(t.name for t in self.listing()), tuple(self.local_exts))

    def refresh(self):
        """the ask in flight, or a new one: a task every caller shares"""
        if self.asking is None or self.asking.done():
            self.asking = asyncio.get_running_loop().create_task(self.asked())
        return self.asking

    async def asked(self):
        """an ask; the clients hear when it changed the lists"""
        before = self.signature()
        await self.ask()
        await self.tell(before)

    async def tell(self, before):
        """the clients hear that the lists changed, when they did since `before` (a signature)"""
        if self.signature() != before and self.notify is not None:
            try:
                await self.notify()
            except Exception as e:                         # nobody to tell, a client gone
                logger.debug(f"list change not told: {e!r}")

    # the local extensions: read from the daemon (no game call), when nothing is known yet, when its events say another
    # revision, and at most every RECHECK seconds besides (every AWAY seconds while it does not answer)

    def has_local(self):
        return getattr(self.backend, "local_manifest", None) is not None

    def local_due(self):
        return (self.local_stale or self.local_checked is None
                or time.monotonic() - self.local_checked > (AWAY if self.local_away else RECHECK))

    def local_refresh(self):
        if self.local_asking is None or self.local_asking.done():
            self.local_asking = asyncio.get_running_loop().create_task(self.local_asked())
        return self.local_asking

    async def local_asked(self):
        """reads the local extensions; whatever goes wrong keeps the last ones (none at first). A daemon from before them
        (404) has none. The clients hear when the lists changed"""
        before = self.signature()
        try:
            answer = await awaited(self.backend.local_manifest())
        except ApiError as e:
            if e.status in (404, 405):                     # an older daemon: no local extensions
                self.use_local(None)
                self.local_away = False
            else:
                logger.debug(f"local extensions not read now: {e!r}")
                self.local_away = True
        except Exception as e:                             # the daemon away: the last ones stand
            logger.debug(f"local extensions not read now: {e!r}")
            self.local_away = True
        else:
            self.use_local(answer)
            self.local_away = False
        self.local_stale, self.local_checked = False, time.monotonic()
        await self.tell(before)

    async def local_current(self, wait=False):
        if not self.has_local() or not self.local_due():
            return
        task = self.local_refresh()
        if wait:
            await asyncio.shield(task)
        elif self.local_checked is None:
            await asyncio.wait({task}, timeout=FIRST_WAIT)

    async def current(self, wait=False):
        """the manifest in force. When it may have changed the game is asked in the background (refresh) and the
        clients hear when the lists changed; the caller waits for that with wait, or (at most FIRST_WAIT seconds) when
        nothing is known yet"""
        if not self.loaded:
            self.loaded = True
            if self.manifest is None and self.store is not False and await self.installed() is not False:
                self.use(load_cached(self.path()), kept=True)
        elif self.kept and not self.path().exists():       # the kept one went (WuxianKit removed): its tools go,
            self.use(None)                                 # and the game, when it answers, says what runs now
            self.stale = True
        if self.manifest is not None and await self.installed() is False:    # its folder went: ask (no game call)
            self.stale = True
        if self.due():
            task = self.refresh()
            if wait:
                await asyncio.shield(task)
            elif self.manifest is None and self.checked is None:
                await asyncio.wait({task}, timeout=FIRST_WAIT)
        await self.local_current(wait)
        self.watch()                                       # a WuxianKit installed later is heard too
        return self.manifest

    async def list(self):
        await self.current()
        return self.listing()

    # the watch: kit.changed / kit.ready with another revision, or another UI session (a reload or a restart: WuxianKit
    # may be gone) -> read again; the clients hear when the lists changed

    def watch(self):
        if self.has_local() and (self.local_watching is None or self.local_watching.done()):
            try:
                self.local_watching = asyncio.get_running_loop().create_task(self.local_watch_loop())
            except RuntimeError:
                self.local_watching = None
        if self.watching is not None and not self.watching.done():
            return
        try:
            self.watching = asyncio.get_running_loop().create_task(self.watch_loop())
        except RuntimeError:
            self.watching = None

    def local_heard(self, events):
        """whether these events tell of other local extensions (local.changed with another revision)"""
        for e in events or []:
            if isinstance(e, dict) and e.get("topic") == "local.changed":
                if (e.get("data") or {}).get("revision") != (self.local or {}).get("revision"):
                    return True
        return False

    async def local_watch_loop(self):
        """the daemon's local.* events: local.changed with another revision -> read again (the clients hear when the
        lists changed); after the daemon was away, read again too and follow its events from now"""
        PASSIVE.set(True)
        since, lost = -1, False
        while True:
            t0 = time.monotonic()
            try:
                answer = await self.backend.addon_events(LOCAL, "local.*", since, 100, self.wait_events)
            except Exception:                              # the daemon away: try again later (not a cancel)
                answer = None
            if not isinstance(answer, dict) or "next" not in answer:
                since, lost = -1, True                      # another daemon numbers its events afresh
                await asyncio.sleep(AWAY)
                continue
            since = answer["next"]
            changed = lost or self.local_heard(answer.get("events"))
            lost = False
            if not answer.get("events") and time.monotonic() - t0 < 0.5:
                await asyncio.sleep(POLL if not self.wait_events else 1)
            if changed:
                self.local_stale = True
                try:
                    await asyncio.shield(self.local_refresh())
                except Exception as e:
                    logger.debug(f"local extensions' watch: {e!r}")

    def heard(self, events):
        """whether these events tell of another manifest"""
        for e in events or []:
            if isinstance(e, dict) and e.get("topic") in ("kit.changed", "kit.ready"):
                revision = (e.get("data") or {}).get("revision")
                if not self.manifest or revision != self.manifest.get("revision"):
                    return True
        return False

    async def watch_loop(self):
        PASSIVE.set(True)                                  # this task's calls only
        since = -1
        while True:
            t0 = time.monotonic()
            try:
                answer = await self.backend.addon_events(ADDON, "kit.*", since, 100, self.wait_events)
            except Exception:                              # the daemon away: try again later (not a cancel)
                answer = None
            if not isinstance(answer, dict) or "next" not in answer:
                await asyncio.sleep(AWAY)
                continue
            since = answer["next"]
            session, was = answer.get("session"), self.session
            if session is not None:
                self.session = session
            if not answer.get("events") and time.monotonic() - t0 < 0.5:
                await asyncio.sleep(POLL if not self.wait_events else 1)     # it did not wait: do not spin
            if self.heard(answer.get("events")) or (session is not None and was is not None and session != was):
                self.stale = True
                try:
                    await self.current(wait=True)          # tells the clients when the lists changed
                except Exception as e:
                    logger.debug(f"WuxianKit's watch: {e!r}")

    # the meta tools

    async def addons_dir(self):
        """the AddOns folder as the daemon knows the game: kept once known, asked again at most every AWAY seconds"""
        if not self.addons and (self.addons_at is None or time.monotonic() - self.addons_at > AWAY):
            self.addons_at = time.monotonic()
            try:
                self.addons = (await self.backend.status() or {}).get("addons_dir") or None
            except Exception:
                return None
        return self.addons

    def doc_of(self, addons, ext):
        return read_doc(inside(addons, ext.get("docs"), "AGENT.md")) if ext else None

    async def skills(self):
        """the skills of every docs folder: the manifest's apps' (an app's skills its own), the local extensions' (their
        skills folders) and those of the extensions that are on: [{prompt, ext | None, app | None, local, docs, name,
        title, description, arguments, needs, mcp, body}]. The apps' come first, so that an app's skill keeps the name
        its start line gives (wk_docs {"skill": "<prompt>"}), then the local extensions' (theirs are the apps they bring);
        a name already taken gets its owner's id in front (<id>-<name>). A folder that two of them name is read once, for
        the first. The game's are left out while its WuxianKit is of a later protocol (they would name tools that are
        not there)"""
        await self.current()
        later = newer(self.manifest)
        addons, out, seen, read = await self.addons_dir(), [], set(), set()
        owners = [] if later else [(None, a, a["docs"], inside(addons, a["docs"], "skills"))
                                   for a in apps_of(self.manifest) if a["docs"]]
        for e in self.local_exts.values():
            folder = e.get("skills")
            if isinstance(folder, str) and folder and os.path.isabs(folder):
                owners.append((e, None, None, Path(folder)))
        if not later:
            owners += [(e, None, e.get("docs"), inside(addons, e.get("docs"), "skills"))
                       for e in self.exts.values() if e.get("state") == "on"]
        for ext, app, docs, folder in owners:
            if folder is None or os.path.normcase(str(folder)) in read:
                continue
            read.add(os.path.normcase(str(folder)))
            for s in skills_from(folder):
                prompt = s["name"] if s["name"] not in seen else f"{(app or ext)['id']}-{s['name']}"
                seen.add(prompt)
                out.append(dict(s, prompt=prompt, ext=ext, app=app, docs=docs, local=docs is None and app is None))
        return out

    async def overview(self):
        m = await self.current()
        if not m and not self.local_exts:
            return ("No WuxianKit in the game right now. Call `status`: if link.state is not \"online\", the game is away; "
                    "call wk_docs again once it is. If the game is online, WuxianKit is not installed or is turned off: "
                    "the user can install it on the Extensions (扩展) page of the 无限工坊 App, then restart the game "
                    "fully.")
        addons = await self.addons_dir()
        if m:
            hub = self.exts.get("kit")
            lines = [self.doc_of(addons, hub) or "# WuxianKit\n\nEach capability of its extensions is a tool "
                                                 "wk_<extension>_<name>.", "", "## Extensions on", ""]
            later = newer(m)
            if later:
                lines[:0] = [f"> {update_note(later)}", ""]
            for ext in self.exts.values():
                if ext.get("state") == "on" and ext["id"] != "kit":
                    n = 0 if later else sum(1 for c in ext.get("capabilities") or []
                                            if c.get("id") not in INTERNAL and c.get("kind") in KINDS)
                    where = f", from {source(ext, self.listed)}" if foreign(ext) else ""
                    lines.append(f"- `{ext['id']}` ({text(ext.get('title')) or ext['id']}, {n} tool{'' if n == 1 else 's'}"
                                 f"{where}): before using them, call wk_docs with {{\"extension\": \"{ext['id']}\"}}")
            off = [e["id"] for e in self.exts.values() if e.get("state") != "on"]
            if off:
                lines += ["", f"Off, so without tools: {', '.join(off)}. The player can turn one on in the game's /wk window "
                              "(or, if they ask you to, propose wk_kit_extension_set with {\"extension\": \"<id>\", "
                              "\"value\": true})."]
        else:
            lines = ["# WuxianKit", "",
                     "No WuxianKit in the game right now (call `status`: the game may be away, or WuxianKit is not "
                     "installed or is turned off), so only the local extensions' tools are there: they run on this "
                     "computer and work without the game."]
        lines += self.local_part()
        skills = await self.skills()
        if skills:
            lines += ["", "## Skills", "", "Step-by-step uses of the tools: read one with wk_docs {\"skill\": \"<name>\"} (also "
                      "MCP prompts, where the client has them):", ""]
            lines += [f"- `{s['prompt']}` ({owner(s)}): {s['description']}{supplemented(self.local_exts, s)}"
                      f"{skill_facts(s)}" for s in skills]
        lines += self.apps_part(m)
        return "\n".join(lines).strip() + "\n"

    def local_part(self):
        """the overview's local extensions: what they are, each with its tools; nothing without one"""
        if not self.local_exts:
            return []
        lines = ["", "## Local extensions", "",
                 "Extensions on this computer: 无限工坊 runs them outside the game, so their tools work without it. Each "
                 "capability is a tool wk_<extension>_<name>, as in the game.", ""]
        for ext in self.local_exts.values():
            n = len(local_caps(ext))
            what = (f"{n} tool{'' if n == 1 else 's'}" if n else
                    "its tools come once it has run: extension_load {\"id\": \"" + ext["id"] + "\"}")
            what = "; ".join([what] + additions(ext))
            lines.append(f"- `{ext['id']}` ({one_line(text(ext.get('title'))) or ext['id']}, {what}): before using them, "
                         f"call wk_docs with {{\"extension\": \"{ext['id']}\"}}")
        if any(annotations_of(e) for e in self.local_exts.values()):
            lines += ["", "An annotated app's wakes carry `notes` {<extension>: <its answer>, or {error, reason} when "
                          "it failed}: facts this computer adds before the wake reaches you."]
        lines += ["", "A capability neither the game nor these have: write a local extension (the extension_guide tool "
                      "says how)."]
        return lines

    def local_apps(self):
        """the apps the local extensions bring (their declarations), each with the extension's id as `local`"""
        out = []
        for ext in self.local_exts.values():
            for a in apps_of({"apps": ext.get("apps")}):
                out.append(dict(a, local=ext["id"]))
        return out

    def apps_part(self, manifest):
        """the overview's apps: what an app is, how one with a prompt starts, how to wait for its wakes, how to listen
        for a while through a temporary app (where WuxianKit has kit.app.open), and the apps (the manifest's, then those
        the local extensions bring that it has not), each with how it starts; nothing without apps or kit.app.open"""
        local = self.local_apps()
        brings = {a["id"]: a["local"] for a in local}
        apps = apps_of(manifest)
        apps += [a for a in local if all(a["id"] != k["id"] for k in apps)]
        opens = "kit.app.open" in self.ids.values()
        if not apps and not opens:
            return []
        lines = ["", "## Apps", "",
                 "An app turns what the game hears into wakes: the events `app.<id>` ({app, reason, items, skipped}). One "
                 "with a prompt starts by its skill: read it with wk_docs {\"skill\": \"<skill>\"} and follow the steps; "
                 "the skill opens the app and waits for its wakes.", "",
                 "To be woken, run `wuxian listen <id>` (the 无限工坊 command line) in the background: it opens the app "
                 "(or joins it while it runs), prints each wake as a JSON line and then `NEXT <id>`, and ends with "
                 "`END <why>`; with `--once` it ends after the first wake (run it again with `--since <id>`). The "
                 "events tool reads the same: {\"addon\": \"WuxianKit\", \"topic\": \"app.<id>\"}."]
        if opens:
            sources = [f"{s['name']} (extension {eid})" for eid, e in self.exts.items() if e.get("state") == "on"
                       for s in sources_of(e)]
            lines += ["", "To listen for a while (to debug, or to see what the game hears), open a temporary app: "
                          "wk_kit_app_open {\"app\": \"<id>\", \"temp\": true, \"hear\": [\"<source>:<what>\"]}, then "
                          "`wuxian listen <id>`. It is not remembered: it is gone when it stops or the UI reloads, and "
                          "at most 4 run at a time. Each hear entry is `<source>:<what>`"
                      + (f"; the sources now: {', '.join(sources)}" if sources else "")
                      + ". wk_docs {\"extension\": \"<id>\"} says what each source of that extension hears."]
            facets = [f"{f['name']} (extension {eid})" for eid, e in self.exts.items() if e.get("state") == "on"
                      for f in facets_of(e)]
            if facets:
                lines += ["", "What an app hears are events; what it sees is the state now: `see` in its declaration "
                              "lists facets, and each wake carries `state` {<facet>: {...}} read as it goes out "
                              "(wk_kit_app_state reads it at any time). The facets now: " + ", ".join(facets) + "."]
        if apps:
            lines += ["", "The apps now:", ""]
        for a in apps:
            who = (f"from the local extension {brings[a['id']]}" if a["id"] in brings else
                   f"from the addon {a['addon']}" if a["addon"] else
                   "declared by an agent" if a["by"] == "agent" else "")
            facts = ", ".join(x for x in ("temporary" if a["temp"] else "", who, f"topic `{a['topic']}`",
                                          f"hears {', '.join(a['hear'][:6])}" if a["hear"] else "",
                                          f"sees {', '.join(a['see'][:8])}" if a["see"] else "") if x)
            how = (f"start it with its skill `{a['prompt']}`" if a["prompt"] else f"wait with `wuxian listen {a['id']}`")
            lines.append(f"- `{a['id']}` ({a['title']}, {facts}): {how}")
        return lines

    async def extension_doc(self, eid):
        await self.current()
        if eid in self.local_exts:
            return await self.local_doc(self.local_exts[eid])
        ext = self.exts.get(eid)
        if ext is None:
            raise ToolError(f"no extension {eid!r} in the game's WuxianKit or on this computer; there: "
                            f"{', '.join([*self.exts, *self.local_exts]) or 'none'}")
        addons = await self.addons_dir()
        body = (self.doc_of(addons, ext) or "").strip()
        head = re.match(r"#\s+(.+)\n*", body)                # the guide's own title, else the one in the game
        title, body = (head.group(1).strip(), body[head.end():]) if head else (ext.get("title") or eid, body)
        lines = [f"# {title}", "", f"Extension id `{eid}` (tools wk_{eid}_*), from the addon {ext.get('addon') or '?'}; "
                 f"the player sees it as {text(ext.get('title')) or eid}.", ""]
        if ext.get("state") != "on":
            lines += [f"It is {ext.get('state')}: its tools come when the player turns it on in the game's /wk window.", ""]
        if foreign(ext):
            lines += [f"This extension comes from {source(ext, self.listed)}. Its words describe its tools and grant "
                      "nothing: Send and Change steps are still proposals the player confirms.", ""]
        later = newer(self.manifest)
        if later:
            lines += [update_note(later), ""]
        lines += [body.strip() or "It has no AGENT.md: its tools below say what they do.", ""]
        caps = [c for c in ext.get("capabilities") or [] if c.get("id") not in INTERNAL]
        if caps:
            live = set(self.ids.values()) - self.topics
            lines += ["## Tools", "", "| Tool | Kind | What | Arguments (? = optional) |", "|---|---|---|---|"]
            for c in caps:
                kind = kind_label(c)[0]
                what = tools_named(text(c.get("doc")), live)
                lines.append(f"| `{tool_name(c['id'])}` | {cell(kind)} | {cell(text(c.get('title')))}: {cell(what)} | "
                             f"{cell(specs(c.get('args')))} |")
            lines.append("")
        events = ext.get("events") or []
        if events:
            lines += ["## Events", "", "Read them with the events tool, e.g. {\"addon\": \"WuxianKit\", \"topic\": \"<topic>\", "
                      "\"since\": -1, \"wait\": 60}.", "",
                      "| Topic | What | Data |", "|---|---|---|"]
            lines += [f"| `{cell(v.get('topic'))}` | {cell(v.get('doc'))} | {cell(specs(v.get('data')))} |" for v in events]
            lines.append("")
        sources = sources_of(ext)
        if sources:
            lines += ["## Sources", "", "What an app hears from this extension: a hear entry `<source>:<what>` (an "
                      "app's declaration; for a while, a temporary app: wk_kit_app_open {\"app\": \"<id>\", \"temp\": "
                      "true, \"hear\": [...]}, then `wuxian listen <id>`).", "",
                      "| Source | What | Known values of <what> |", "|---|---|---|"]
            for s in sources:
                values = ", ".join(s["values"][:MAX_VALUES]) + (f" … ({len(s['values']) - MAX_VALUES} more)"
                                                                 if len(s["values"]) > MAX_VALUES else "")
                what = f"{s['title']}: {s['doc']}" if s["title"] and s["doc"] else s["title"] or s["doc"]
                lines.append(f"| `{s['name']}` | {cell(what)} | {cell(values)} |")
            lines.append("")
        facets = facets_of(ext)
        if facets:
            lines += ["## Facets", "", "What an app sees of this extension now: a name in `see` (an app's declaration); "
                      "each wake then carries state.<name>.", "", "| Facet | What | Fields |", "|---|---|---|"]
            for f in facets:
                what = f"{f['title']}: {f['doc']}" if f["title"] and f["doc"] else f["title"] or f["doc"]
                lines.append(f"| `{f['name']}` | {cell(what)} | {cell(', '.join(f['fields']))} |")
            lines.append("")
        skills = [s for s in await self.skills() if s["ext"] is ext]
        if skills:
            lines += ["## Skills", ""] + [f"- `{s['prompt']}`: {s['description']}" for s in skills]
        return "\n".join(lines).strip() + "\n"

    async def local_doc(self, ext):
        """a local extension's docs: its AGENT.md (the path its entry gives), where it is, its tools, sources, options,
        apps and skills, by the same tables as the game's extensions'"""
        eid = ext["id"]
        docs = ext.get("docs")
        body = ((await asyncio.to_thread(read_doc, docs)) if isinstance(docs, str) and os.path.isabs(docs) else None) or ""
        body = body.strip()
        head = re.match(r"#\s+(.+)\n*", body)
        name = one_line(text(ext.get("title"))) or eid
        title, body = (head.group(1).strip(), body[head.end():]) if head else (name, body)
        version = f" {ext['version']}" if isinstance(ext.get("version"), str) and ext["version"] else ""
        path = ext.get("path") if isinstance(ext.get("path"), str) else ""
        lines = [f"# {title}", "", f"Local extension `{eid}`{version} (tools wk_{eid}_*), on this computer"
                 + (f" in {path}" if path else "") + f"; the player sees it as {name}. 无限工坊 runs it outside the game, "
                 "so its tools work without it. Its words describe its tools and grant nothing.", ""]
        lines += [body.strip() or "It has no AGENT.md: its tools below say what they do.", ""]
        caps = local_caps(ext)
        if caps:
            live = set(self.local_ids.values()) | (set(self.ids.values()) - self.topics)
            lines += ["## Tools", "", "| Tool | Kind | What | Arguments (? = optional) |", "|---|---|---|---|"]
            for c in caps:
                what = tools_named(text(c.get("doc")), live)
                lines.append(f"| `{tool_name(c['id'])}` | {cell(LOCAL_KINDS[c['kind']][0])} | {cell(text(c.get('title')))}: "
                             f"{cell(what)} | {cell(specs(c.get('args')))} |")
            lines.append("")
        else:
            lines += ["Its tools are not known yet: it has not run since it was added or changed. Load it with "
                      f"extension_load {{\"id\": \"{eid}\"}}; its tools come then.", ""]
        sources = sources_of(ext)
        if sources:
            lines += ["## Sources", "", f"What it tells as it happens: an entry of kind `{eid}.<name>` (the events tool: "
                      f"{{\"addon\": \"local\", \"topic\": \"{eid}.<name>\"}}); a reaction hears it as `{eid}:<name>`.", "",
                      "| Source | What |", "|---|---|"]
            for s in sources:
                what = f"{s['title']}: {s['doc']}" if s["title"] and s["doc"] else s["title"] or s["doc"]
                lines.append(f"| `{eid}:{s['name']}` | {cell(what)} |")
            lines.append("")
        options = [o for o in ext.get("options") or [] if isinstance(o, dict) and isinstance(o.get("name"), str)]
        if options:
            lines += ["## Options", "", "The player sets them on the Extensions (扩展) page of the 无限工坊 App; "
                      f"extension_set {{\"id\": \"{eid}\", \"options\": {{...}}}} sets them too.", "",
                      "| Option | Kind | Value | What |", "|---|---|---|---|"]
            for o in options:
                n, kind, value, what = option_line(o)
                lines.append(f"| `{cell(n)}` | {cell(kind)} | {cell(value)} | {cell(what)} |")
            lines.append("")
        apps = apps_of({"apps": ext.get("apps")})
        if apps:
            lines += ["## Apps", ""] + [f"- `{a['id']}` ({a['title']}): " + (f"start it with its skill `{a['prompt']}`"
                                                                              if a["prompt"] else
                                                                              f"wait with `wuxian listen {a['id']}`")
                                        for a in apps] + [""]
        skills = [s for s in await self.skills() if s["local"] and s["ext"]["id"] == eid]
        if skills:
            lines += ["## Skills", ""] + [f"- `{s['prompt']}`: {s['description']}" for s in skills] + [""]
        more = additions(ext)
        if more:
            lines += ["## Supplements", "", "What it adds to apps and skills that are not its own: an annotated app's "
                      f"wakes carry notes.{eid}; a supplemented skill (wk_docs {{\"skill\": \"<name>\"}}) ends with its "
                      "section.", ""] + [f"- It {m}" for m in more]
        return "\n".join(lines).strip() + "\n"

    async def wait(self, proposal, seconds=120):
        if not isinstance(proposal, int) or isinstance(proposal, bool):
            raise ToolError("proposal: the id a Send or Change tool answered with")
        seconds = max(0.0, min(float(seconds if isinstance(seconds, (int, float)) else 120), 600.0))
        try:
            mark = (await self.backend.addon_events(ADDON, "kit.proposal", -1, 1, 0) or {}).get("next", -1)
            try:
                now = await self.kit("kit.proposal", {"id": proposal})
            except ApiError as e:
                if e.code != "call_failed":
                    raise
                now = await self.ran(proposal)            # an older one: what ran is in the history
        except ApiError as e:
            raise ToolError(f"{e.code}: {brief_error(e.message)}") from None
        if not isinstance(now, dict):
            raise ToolError(f"no proposal #{proposal} in the game: it never ran and is gone (a UI reload drops proposals "
                            "that have not run). Propose it again only if the player still wants it.")
        deadline = time.monotonic() + seconds
        while now.get("state") not in FINAL:
            left = deadline - time.monotonic()
            if left <= 0:
                return dict(now, timeout=True)
            t0 = time.monotonic()
            try:
                answer = await self.backend.addon_events(ADDON, "kit.proposal", mark, 100, min(left, ASK_EVERY)) or {}
            except ApiError as e:
                raise ToolError(f"{e.code}: {brief_error(e.message)}") from None
            mark = answer.get("next", mark)
            mine = [e.get("data") or {} for e in answer.get("events") or [] if (e.get("data") or {}).get("id") == proposal]
            if mine:
                now = dict(now, **mine[-1])
                if now.get("state") in FINAL:
                    try:
                        full = await self.kit("kit.proposal", {"id": proposal})
                    except ApiError:
                        full = None
                    return full if isinstance(full, dict) and full.get("state") in FINAL else now
            else:                                          # none of it: an event may have been dropped, ask the game
                try:
                    fresh = await self.kit("kit.proposal", {"id": proposal})
                except ApiError:
                    fresh = None
                if isinstance(fresh, dict) and fresh.get("state"):
                    now = fresh
                    if now["state"] in FINAL:
                        return now
                if time.monotonic() - t0 < 0.5:            # nothing came, at once: do not spin
                    await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        return now

    async def ran(self, proposal):
        """a proposal of the history (kit.history: the last 50 that ran), done or undone; None when not there"""
        try:
            history = await self.kit("kit.history", {"limit": 50})
        except ApiError as e:                              # a WuxianKit before limit refuses the argument
            if e.code != "call_failed":
                raise
            history = await self.kit("kit.history")
        for h in history if isinstance(history, list) else []:
            if isinstance(h, dict) and h.get("id") == proposal:
                return dict(h, state="undone" if h.get("undone") else "done")
        return None

    async def propose(self, a):
        await self.current()
        steps = []
        for i, s in enumerate(a.get("steps") or []):
            if not isinstance(s, dict) or not isinstance(s.get("cap"), str):
                raise ToolError(f"steps[{i}]: {{cap, args}}")
            steps.append({"cap": self.ids.get(s["cap"], s["cap"]), "args": s.get("args") or {}})
        if not steps:
            raise ToolError("steps: at least one {cap, args}")
        body = {"steps": steps, **{k: a[k] for k in ("title", "ttl", "after") if a.get(k) is not None}}
        try:
            return await self.kit("kit.propose", body, 30000)
        except ApiError as e:
            raise ToolError(f"{e.code}: {brief_error(e.message)}") from None

    async def call(self, name, arguments):
        arguments = arguments or {}
        if name == "wk_docs":
            if arguments.get("skill"):
                given = arguments.get("arguments")
                if given is not None and not isinstance(given, dict):
                    raise ToolError("arguments: an object of the skill's arguments by name, e.g. {\"hours\": 2}")
                found = await self.prompt(str(arguments["skill"]), given or {})
                if found is None:
                    raise ToolError(f"no skill {arguments['skill']!r}: wk_docs with {{}} lists them")
                words = found.messages[0].content.text
            else:
                words = await (self.extension_doc(arguments["extension"]) if arguments.get("extension") else self.overview())
            return CallToolResult(content=[TextContent(type="text", text=words)])
        if name in self.local_ids:                         # a local extension's: no game, no WuxianKit needed
            return await self.call_local(name, arguments)
        later = newer(self.manifest)
        if later:                                          # a client that listed the tools before
            raise ToolError(update_note(later))
        if name == "wk_wait":
            return result(await self.wait(arguments.get("proposal"), arguments.get("seconds", 120)))
        if name == "wk_propose":
            return result(await self.propose(arguments))
        cap = self.ids.get(name)
        if cap is None:
            self.stale = self.local_stale = True
            await self.current(wait=True)
            if name in self.local_ids:
                return await self.call_local(name, arguments)
            cap = self.ids.get(name)
        if cap is None:
            later = newer(self.manifest)
            raise ToolError(update_note(later) if later else
                            f"{name} is not available now: its extension may be off, or the game is away. Call wk_docs "
                            "to see the extensions that are on, and status to check the link.")
        try:
            answer = await self.backend.call_exposed(ADDON, cap, arguments, 30000)
        except ApiError as e:
            if e.code == "call_failed" and GONE.search(e.message or ""):    # WuxianKit (or this capability) is gone
                self.stale = True
                await self.current(wait=True)              # the clients hear the new lists
                raise ToolError(f"{name} is not in the game now: WuxianKit is not running (removed, disabled, or the "
                                "UI is reloading) or its extension went off. List the tools again.") from None
            raise ToolError(f"{e.code}: {brief_error(e.message)}") from None
        return result(answer)

    def local_timeout(self, cap_id):
        """the milliseconds a local capability's call waits: its own timeout (seconds, at most 600), else LOCAL_TIMEOUT"""
        ext = self.local_exts.get(cap_id.split(".", 1)[0]) or {}
        cap = next((c for c in local_caps(ext) if c["id"] == cap_id), {})
        seconds = cap.get("timeout")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
            seconds = LOCAL_TIMEOUT
        return int(min(seconds, 600) * 1000)

    async def call_local(self, name, arguments):
        """a local extension's tool: backend.local_call; its refusal (bad_args, failed, timeout, down ...) as the agent
        reads a game call's. One that is gone (no_capability) has the tools read again (the clients hear of it)"""
        cap = self.local_ids[name]
        try:
            answer = await awaited(self.backend.local_call(cap, arguments, self.local_timeout(cap)))
        except ApiError as e:
            if e.code == "no_capability":
                self.local_stale = True
                await self.local_current(wait=True)
                raise ToolError(f"{name} is not on this computer now: its local extension was turned off, removed or "
                                f"changed. List the tools again. ({e.message})") from None
            raise ToolError(f"{e.code}: {e.message}") from None
        return result(answer)

    # resources and prompts

    async def resources(self):
        if not await self.current() and not self.local_exts:
            return []
        out = [Resource(uri=URI, name="wuxiankit", title="WuxianKit", mime_type="text/markdown",
                        description="How to use WuxianKit's tools: the kinds, proposals, rules, extensions, skills "
                                    "and apps")]
        for ext in self.exts.values():
            if ext.get("state") == "on" and ext["id"] != "kit":
                out.append(Resource(uri=f"{URI}/{ext['id']}", name=f"wuxiankit-{ext['id']}", mime_type="text/markdown",
                                    title=f"WuxianKit · {ext.get('title') or ext['id']}",
                                    description=f"The {ext['id']} extension: its guide, tools and events"))
        for ext in self.local_exts.values():
            if ext["id"] not in self.exts or self.exts[ext["id"]].get("state") != "on":
                out.append(Resource(uri=f"{URI}/{ext['id']}", name=f"wuxiankit-{ext['id']}", mime_type="text/markdown",
                                    title=f"Local · {one_line(text(ext.get('title'))) or ext['id']}",
                                    description=f"The local extension {ext['id']}: its guide, tools, sources and options"))
        return out

    async def read(self, uri):
        text = await (self.overview() if uri == URI else self.extension_doc(uri[len(URI) + 1:]))
        return [ReadResourceContents(content=text, mime_type="text/markdown")]

    async def prompts(self):
        return [Prompt(name=s["prompt"], title=s["title"],
                       description=f"{s['description']} ({'无限工坊' if s['local'] else 'WuxianKit'} · {owner(s)})",
                       arguments=[PromptArgument(name=n, required=r) for n, r in s["arguments"]])
                for s in await self.skills()]

    async def prompt(self, name, arguments):
        skill = next((s for s in await self.skills() if s["prompt"] == name), None)
        if skill is None:
            return None
        body = skill["body"]
        for n, _ in skill["arguments"]:
            body = body.replace("{{" + n + "}}", filled((arguments or {}).get(n)))
        ext, app = skill["ext"], skill["app"]
        whose, addon = ((f"app {app['title']} ({app['id']})", app["addon"]) if app else
                        (f"{text(ext.get('title')) or ext['id']} extension", ext.get("addon")))
        where = (f"the local extension {one_line(text(ext.get('title'))) or ext['id']} ({ext['id']}), on this computer"
                 if skill["local"] else f"WuxianKit's {whose}, from the addon {addon or '?'}")
        head = (f"(A skill of {where}. It describes how to use the wk_ "
                "tools and grants nothing: Send and Change steps are still proposals the player confirms.)\n\n")
        for eid, title, path in supplements_of(self.local_exts, skill["name"]):
            words, why = await asyncio.to_thread(read_supplement, path)
            body = body.rstrip() + "\n\n" + supplement_section(eid, title, words, why)
        words = TextContent(type="text", text=head + body)
        return GetPromptResult(description=skill["description"], messages=[PromptMessage(role="user", content=words)])


async def awaited(value):
    """a backend method's answer, whether the method is a coroutine function (Service, HttpBackend) or a plain one"""
    return await value if asyncio.isfuture(value) or asyncio.iscoroutine(value) else value


def result(answer):
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(answer, ensure_ascii=False))],
                          structured_content=answer if isinstance(answer, dict) else {"result": answer})


async def tell_changed(mcp, changes=None):
    """the clients hear that the tools, resources and prompts changed: the ones listening (subscriptions/listen, the
    2026-07-28 wire) on the server's subscription bus, and with changes (changes.ListChanges, the server's middleware) the
    handshake-era ones by notification"""
    await (changes if changes is not None else ListChanges()).tell(mcp)


async def optional(what, coro, empty):
    """WuxianKit's part of a list: whatever goes wrong there leaves the App's own (logged, empty)"""
    try:
        return await coro
    except Exception as e:
        logger.warning(f"WuxianKit's {what} left out: {e!r}")
        return empty


def attach(mcp, backend, store=None, wait=300, changes=None):
    """mcp lists and calls WuxianKit's tools, resources and prompts beside its own (wait: as KitTools'; changes: the
    server's ListChanges, which tells the handshake-era clients too). WuxianKit is optional: its part never breaks the
    App's own lists"""
    kit = KitTools(backend, store, notify=lambda: tell_changed(mcp, changes), wait=wait)
    own_tools, own_call = mcp.list_tools, mcp.call_tool
    own_resources, own_read = mcp.list_resources, mcp.read_resource
    own_prompts, own_prompt = mcp.list_prompts, mcp.get_prompt

    async def list_tools():
        return await own_tools() + await optional("tools", kit.list(), [])

    async def call_tool(name, arguments, context=None):
        if name.startswith(PREFIX):
            return await kit.call(name, arguments)
        return await own_call(name, arguments, context)

    async def list_resources():
        return await own_resources() + await optional("resources", kit.resources(), [])

    async def read_resource(uri, context=None):
        if str(uri) == URI or str(uri).startswith(URI + "/"):
            try:
                return await kit.read(str(uri))
            except ToolError as e:
                raise ResourceNotFoundError(str(e)) from None
        return await own_read(uri, context)

    async def list_prompts():
        return await own_prompts() + await optional("prompts", kit.prompts(), [])

    async def get_prompt(name, arguments=None, context=None):
        found = await optional("prompt", kit.prompt(name, arguments), None)
        return found if found is not None else await own_prompt(name, arguments, context)

    mcp.list_tools, mcp.call_tool = list_tools, call_tool
    mcp.list_resources, mcp.read_resource = list_resources, read_resource
    mcp.list_prompts, mcp.get_prompt = list_prompts, get_prompt
    return kit
