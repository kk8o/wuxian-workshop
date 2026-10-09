"""wuxian: the WuxianWorkshop (无限工坊) command line.

    wuxian                                    the desktop window (daemon + tray + web view); `serve` when the ui is missing
    wuxian app [--background] [...]           the same; --background keeps the window hidden (the tray icon shows)
    wuxian serve [--mode] [--capture] [--game]  the daemon alone: the local API on 127.0.0.1, MCP at /mcp
    wuxian status                             the daemon, the game window and the link
    wuxian run <lua>                          run Lua in the game, print the values (exit 1 on a Lua error)
    wuxian load <target> [--no-reset]         hot-load a file or an addon (its .toc order), checked first
    wuxian check <target> [--no-live]         what would fail in the game, found before it gets there
    wuxian try <lua> | --slash "/cmd args"    do it in the game and see what came of it (errors, prints, events, a snap)
    wuxian watch start|stop|list [target]     reload files whenever they are saved
    wuxian snap [--region x y w h]            a PNG of the game window
    wuxian trace [seconds] [--events BAG_*]   the events the game fires meanwhile (names, arguments, counts)
    wuxian inspect <lua expr> | --mouse       a frame of the running UI (place, size, anchors, children)
    wuxian events [--addon A] [--topic T] [--follow]   what addons sent with WoWBridge's Emit (their events)
    wuxian call <addon> <name> [json args]    call a function an addon exposed with WoWBridge's Expose
    wuxian addon-api [addon]                  what addons expose (functions) and emit (event topics)
    wuxian history [addon] [id] [--against]   kept versions of addons; one version's diff with the files now
    wuxian checkpoint <addon> [note]          keep a version of an addon's files now
    wuxian restore <addon> <id>               put an addon's files back as a kept version had them
    wuxian reload                             ask the player for a UI reload
    wuxian logs [--follow] [--since N] [--kinds ERR,OUT]
    wuxian say <text>                         a line in the game's chat
    wuxian doctor                             the self-check
    wuxian addons                             the installed addons, with the errors collected for each
    wuxian errors [addon] [--limit N]         the errors !WuxianWorkshop collected (as of the last logout or /reload)
    wuxian install [--no-clean] [--game DIR]  install the addon
    wuxian update [check|download|apply]      the program's updates (an installed copy; apply restarts it)
    wuxian new <Name> [--title] [--template]  a new addon from a template (with AGENTS.md for the agent)
    wuxian api <words> | --get NAME | --manual [TOPIC]   this client's API manual (built in; no daemon needed)
    wuxian quit                               stop the daemon
    wuxian mcp-config [claude|codex|cursor]   snippets that register the MCP server with those hosts
    wuxian agents [connect|disconnect|verify] [claude|codex|cursor|all]   register this program with the agents
    wuxian mcp                                the MCP server on stdio (for hosts that start it themselves)
    wuxian companion [...]                    the link alone, without the daemon;  wuxian monitor: print every frame

status .. quit are clients of the daemon: they read state/daemon.json and, when nothing answers there, start it
detached (`wuxian app --background`, or `wuxian serve` without the ui) and wait for it. app, serve, mcp, companion and
monitor hand their arguments to their own programs (ui.shell, daemon.server, mcp.server, daemon.companion,
agent.monitor): `wuxian companion --help` shows those options.
"""
import argparse
import importlib
import json
import sys
import time

from .. import __version__
from ..i18n import tr
from ..daemon.api import ApiError, read_daemon_json
from . import mcpconfig
from .client import connect

PROGRAMS = {                # sub-command -> (the module whose main() runs it, one line for the help)
    "app": ("wuxianworkshop.ui.shell", "the desktop window with the daemon and the tray icon (--background: hidden)"),
    "serve": ("wuxianworkshop.daemon.server", "run the daemon: the local API on 127.0.0.1, MCP at /mcp"),
    "mcp": ("wuxianworkshop.mcp.server", "the MCP server on stdio, talking to the daemon"),
    "companion": ("wuxianworkshop.daemon.companion", "run the link alone: read frames off the screen, answer through the mailbox"),
    "monitor": ("wuxianworkshop.agent.monitor", "print every new frame on the game screen"),
}


def build_parser():
    ap = argparse.ArgumentParser(prog="wuxian", description="wuxian: the WuxianWorkshop command line",
                                 epilog="`wuxian <command> --help` shows that command's own options")
    ap.add_argument("--version", action="version", version=f"wuxian {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="<command>")
    for name, (_, text) in PROGRAMS.items():
        sub.add_parser(name, help=text, add_help=False)       # the program parses its own options, --help included
    sub.add_parser("status", help="the daemon, the game window and the link")
    p = sub.add_parser("run", help="run Lua in the game and print the returned values")
    p.add_argument("code", nargs="+", help="the Lua (several words are joined with spaces)")
    p.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for the result (default 10)")
    p.add_argument("--addon", help="the addon whose name and namespace the code gets as ...")
    p = sub.add_parser("load", help="hot-load a file or an addon into the game")
    p.add_argument("target", help="a file, an addon folder or an addon name (relative to Interface/AddOns)")
    p.add_argument("--no-reset", action="store_true", help="do not run the addon's OnUnload / OnReload hooks")
    p.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for all results (default 30)")
    p.add_argument("--no-check", action="store_true", help="send it without checking it first")
    p = sub.add_parser("try", help="do something in the game and see what came of it: errors, prints, blocked, events")
    p.add_argument("code", nargs="*", help="the Lua (several words are joined with spaces)")
    p.add_argument("--slash", help='a slash command line instead ("/myaddon show"; the / may be left out: Git Bash turns '
                                   'an argument that starts with / into a path)')
    p.add_argument("--seconds", type=float, default=2, help="how long to collect afterwards (0-30, default 2)")
    p.add_argument("--addon", help="the addon whose name and namespace the code gets as ...")
    p.add_argument("--snap", action="store_true", help="a screenshot at the end")
    p.add_argument("--frame", help="a Lua expression of a frame to picture at the end instead of the screen")
    p.add_argument("--events", help='events to record meanwhile: names or globs (BAG_*), or "all"')
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("check", help="check an addon or a file: what would fail in the game, before it gets there")
    p.add_argument("target", help="a file, an addon folder or an addon name (relative to Interface/AddOns)")
    p.add_argument("--no-live", action="store_true", help="do not ask the running game about the unresolved names")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("watch", help="reload files into the game whenever they are saved")
    p.add_argument("action", choices=("start", "stop", "list"))
    p.add_argument("target", nargs="?", help="a file, an addon folder or an addon name")
    p = sub.add_parser("snap", help="a PNG of the game window's client area")
    p.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"), help="a part of the client area")
    p.add_argument("--max-width", type=int, default=1280, help="scale down to this width (default 1280; 0 = keep)")
    p = sub.add_parser("trace", help="the events the game fires for a while (names, arguments, how often)")
    p.add_argument("seconds", nargs="?", type=float, default=10, help="how long (1-120 s, default 10)")
    p.add_argument("--events", help="names or globs, comma-separated (BAG_*, LOOT_OPENED); default: all an addon may register")
    p.add_argument("--max", type=int, default=200, help="events kept (default 200; all are counted)")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("inspect", help="a frame of the running UI, or the frames under the mouse")
    p.add_argument("target", nargs="?", help="a Lua expression that gives a frame (PlayerFrame, MyAddon.window)")
    p.add_argument("--mouse", action="store_true", help="the frames under the mouse cursor instead")
    p.add_argument("--depth", type=int, default=1, help="levels of children (0-3, default 1)")
    p.add_argument("--snap", action="store_true", help="also a picture of the first frame")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("events", help="the events addons sent with WoWBridge's Emit")
    p.add_argument("--addon", help="only this addon's (a name or a glob)")
    p.add_argument("--topic", help="only these topics (a name or a glob: scan.*)")
    p.add_argument("--since", type=int, default=0, help="from this entry id on (the next of the last call)")
    p.add_argument("--follow", "-f", action="store_true", help="keep printing new ones (Ctrl+C ends it)")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("call", help="call a function an addon exposed with WoWBridge's Expose")
    p.add_argument("addon", help="the addon's folder name")
    p.add_argument("name", help="the name it exposed")
    p.add_argument("args", nargs="?", help="""its arguments as JSON ('{"n": 2}')""")
    p.add_argument("--timeout-ms", type=int, default=10000, help="how long to wait for the game (default 10000)")
    p = sub.add_parser("addon-api", help="what addons expose (functions to call) and emit (event topics)")
    p.add_argument("addon", nargs="?", help="the addon (nothing: every addon with a WoWBridge handle)")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("history", help="kept versions of addons; what changed since one of them")
    p.add_argument("addon", nargs="?", help="the addon (nothing: the addons with kept versions)")
    p.add_argument("id", nargs="?", type=int, help="a version: its diff with the files now")
    p.add_argument("--against", default="now", help="now (default), prev (what that version changed) or another id")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("checkpoint", help="keep a version of an addon's files now")
    p.add_argument("addon")
    p.add_argument("note", nargs="*", help="what this version is")
    p = sub.add_parser("restore", help="put an addon's files back as a kept version had them")
    p.add_argument("addon")
    p.add_argument("id", type=int, help="the version (wuxian history <addon> lists them)")
    p = sub.add_parser("reload", help="ask the player for a UI reload (a button in the game)")
    p.add_argument("reason", nargs="*", help="shown in the log")
    p = sub.add_parser("logs", help="the daemon's log: Lua errors, print output, RUN results, notes")
    p.add_argument("--follow", "-f", action="store_true", help="keep printing new entries (Ctrl+C ends it)")
    p.add_argument("--since", type=int, default=None, help="the first entry id (default: the last 50)")
    p.add_argument("--limit", type=int, default=200, help="at most this many entries (default 200)")
    p.add_argument("--kinds", help="only these kinds, comma-separated: ERR,OUT,WARN,BLOCKED,RUN,RELOAD,EVENT,SNAP,WATCH,SLOTS,INFO")
    p = sub.add_parser("say", help="show a line of text in the game's chat")
    p.add_argument("text", nargs="+")
    p = sub.add_parser("doctor", help="the self-check (exit 1 when a check fails)")
    p.add_argument("--game", help="client folder to check")
    p = sub.add_parser("addons", help="the addons in the game's AddOns folder, with the errors collected for each")
    p.add_argument("--game", help="client folder that holds Interface\\AddOns")
    p = sub.add_parser("errors", help="the errors !WuxianWorkshop collected (as of the last logout or /reload)")
    p.add_argument("addon", nargs="?", help="only this addon's (its folder name)")
    p.add_argument("--limit", type=int, default=20, help="at most this many (default 20)")
    p.add_argument("--game", help="client folder that holds Interface\\AddOns")
    p = sub.add_parser("install", help="install the addons into the game's AddOns folder")
    p.add_argument("--game", help="client folder that holds Interface\\AddOns")
    p.add_argument("--mode", choices=("player", "developer"),
                   help="player: the error collector only; developer: WoWBridge too (default: the daemon's mode)")
    p.add_argument("--no-clean", action="store_true", help="leave files of older versions in place")
    p = sub.add_parser("new", help="a new addon in the game's AddOns folder, from a template")
    p.add_argument("name", help="the folder name: letters, digits and _, a letter first")
    p.add_argument("--title", help="the name shown in the game (default: the folder name)")
    p.add_argument("--notes", default="", help="one line about it")
    p.add_argument("--template", choices=("basic", "window"), default="basic")
    p = sub.add_parser("api", help="this client's API manual: search, one entry, the manual's topics")
    p.add_argument("words", nargs="*", help="what to search for")
    p.add_argument("--get", metavar="NAME", help="one function, event or table in full")
    p.add_argument("--manual", nargs="?", const="", metavar="TOPIC", help="the manual's topics, or one of them")
    p.add_argument("--kind", choices=("function", "event", "table"))
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("update", help="the program's updates: the state, or check / download / apply (restarts it)")
    p.add_argument("action", nargs="?", choices=("check", "download", "apply"), help="nothing: the state")
    p.add_argument("--background", action="store_true", help="apply: start the new version without its window")
    sub.add_parser("quit", help="stop the running daemon")
    p = sub.add_parser("mcp-config", help="snippets that register the MCP server with Claude Code, Codex or Cursor")
    p.add_argument("host", nargs="?", choices=mcpconfig.HOSTS, help="one host (default: all three)")
    p = sub.add_parser("agents", help="the agents (Claude Code, Codex, Cursor) and this MCP server: state, connect, disconnect")
    p.add_argument("action", nargs="?", choices=("connect", "disconnect", "verify"), help="nothing: every agent's state")
    p.add_argument("host", nargs="?", default="all", help="claude, codex, cursor or all (default)")
    p.add_argument("--json", action="store_true", help="print JSON")
    return ap


def say_error(text):
    print(f"error: {text}", file=sys.stderr, flush=True)


def fmt_time(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def print_entry(e):
    lines = e["text"].split("\n")
    print(f"{fmt_time(e['t'])} {e['kind']} {lines[0]}")
    for line in lines[1:]:
        print(f"    {line}")


def cmd_status(client, args):
    s = client.status()
    d, g, k = s["daemon"], s["game"], s["link"]
    print(f"daemon  {d['version']}  pid {d['pid']}  up {d['uptime']:.0f} s  mode {d['mode']}  {d.get('url') or ''}")
    if g["found"]:
        size = f"{g['client']['w']}x{g['client']['h']}" if g.get("client") else "?"
        print(f"game    pid {g['pid']}  build {g.get('build') or '?'}  client {size}" + ("  (minimized)" if g.get("minimized") else ""))
    else:
        print("game    not running (no game window)")
    if k["state"] == "online":
        print(f"link    online  session {k['session']}  next slot {k['slot_next']} ({k['slots_left']} left)  hb {k['hb']} s  "
              f"ping {k['ping_p50']} s  capture {k['capture']}")
    else:
        print(f"link    offline  capture {k['capture']}" + (f"  last frame {fmt_time(k['last_frame'])}" if k.get("last_frame") else ""))
    print("watch   " + (", ".join(s["watch"]) if s["watch"] else "(nothing)"))
    print(f"reload  {'pending' if s['reload_pending'] else 'none pending'}")
    print(f"mcp     {s.get('mcp_url') or 'off'}")
    return 0


def cmd_run(client, args):
    res = client.run(" ".join(args.code), timeout_ms=int(args.timeout * 1000), addon=args.addon)
    if res.get("ok"):
        for value in res.get("values") or []:
            print(value)
        if not res.get("values"):
            print(f"ok ({res.get('ms')} ms, no values)", file=sys.stderr)
        cut = res.get("cut")
        if cut:
            print(f"(value {cut['value']} cut at {cut['kept']} of {cut['bytes']} bytes"
                  + (f", {cut['not_sent']} more not sent)" if cut["not_sent"] else ")"), file=sys.stderr)
        return 0
    print(f"error: {res.get('error')}", file=sys.stderr)
    if res.get("stack"):
        print(res["stack"], file=sys.stderr)
    return 1


def print_findings(found, limit=None):
    """errors and warnings, one line each: level file:line: message (hint)"""
    rows = [("error", f) for f in found.get("errors") or []] + [("warning", f) for f in found.get("warnings") or []]
    for level, f in rows[:limit]:
        where = f"{f['file']}:{f['line']}" if f.get("line") else (f.get("file") or "")
        print(f"{level:<8}{where}: {f['message']}" + (f" ({f['hint']})" if f.get("hint") else ""))
    if limit is not None and len(rows) > limit:
        print(f"... {len(rows) - limit} more (wuxian check)")


def cmd_try(client, args):
    if bool(args.code) == bool(args.slash):
        say_error("the Lua to run, or --slash \"/cmd args\" (one of them)")
        return 2
    slash = args.slash
    if slash is not None and not slash.lstrip().startswith("/"):
        slash = "/" + slash.lstrip()                     # "myaddon show": Git Bash would have made "/myaddon" a path
    res = client.try_(" ".join(args.code) or None, slash, args.seconds, args.addon, args.snap, args.frame, args.events)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1
    a = res["action"]
    if a.get("ok"):
        print(f"action ok ({a.get('ms')} ms)" + (f": {', '.join(map(str, a['values']))}" if a.get("values") else ""))
    rows = [(e["t"], "ERR", e) for e in res["errors"]] + [(e["t"], "BLOCKED", e) for e in res["blocked"]] \
        + [(e["t"], "WARN", e) for e in res["warnings"]] + [(e["t"], "OUT", e) for e in res["prints"]]
    for t, kind, e in sorted(rows, key=lambda r: (r[0] or 0)):
        who = f"[{e['addon']}] " if e.get("addon") else ""
        text = e.get("message", e.get("text", ""))
        print(f"+{t:.1f} s {kind:<7} {who}{text}")
        if e.get("stack"):
            print("        " + e["stack"].strip().replace("\n", "\n        "))
    if res.get("events"):
        ev = res["events"]
        print(f"events: {sum(c['count'] for c in ev['counts'])} of {len(ev['counts'])} kinds: "
              + ", ".join(f"{c['event']} x{c['count']}" for c in ev["counts"][:8]))
    if res.get("snap"):
        print(f"snap: {res['snap']['path']}")
    print(("ok: " if res["ok"] else "not ok: ") + res["summary"])
    return 0 if res["ok"] else 1


def cmd_check(client, args):
    res = client.check(args.target, live=not args.no_live)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1
    print_findings(res)
    for n in res.get("notes") or []:
        print(f"note    {n['message']}" + (f" ({n['hint']})" if n.get("hint") else ""))
    live = res.get("live") or {}
    print(f"{res.get('addon') or args.target}: {res['files']} files, {len(res['errors'])} errors, "
          f"{len(res['warnings'])} warnings" + (f", {res['libraries']} library files skipped" if res.get("libraries") else "")
          + (f"; asked the game about {live['checked']} names" if live.get("checked") else "")
          + (f"; {live['error']}" if live.get("error") else ""))
    return 0 if res["ok"] else 1


def cmd_load(client, args):
    res = client.load(args.target, reset=not args.no_reset, timeout_ms=int(args.timeout * 1000), check=not args.no_check)
    for f in res["files"]:
        if f["ok"]:
            print(f"ok     {f['file']} ({f.get('ms')} ms)")
        else:
            print(f"ERROR  {f['file']}: {f.get('error')}")
            if f.get("stack"):
                print("    " + f["stack"].replace("\n", "\n    "))
    kept = res.get("kept")
    if kept and kept.get("new"):
        print(f"(kept as {kept['addon']} #{kept['id']}: wuxian history {kept['addon']})")
    if res.get("check"):
        print_findings(res["check"], limit=10)
    return 0 if res.get("ok") else 1


def cmd_watch(client, args):
    res = client.watch(args.action, args.target)
    print("\n".join(res["watched"]) if res["watched"] else "(nothing is watched)")
    return 0


def cmd_snap(client, args):
    res = client.snap(args.region, args.max_width or None)
    print(f"{res['path']} {res['width']}x{res['height']}")
    return 0


def cmd_trace(client, args):
    res = client.trace(args.seconds, args.events, args.max)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    total = sum(c["count"] for c in res["counts"])
    print(f"{res['seconds']} s: {total} events of {len(res['counts'])} kinds; {res['kept']} kept"
          + (f", {res['dropped']} more not kept" if res["dropped"] else "")
          + (f"; unknown to the game: {', '.join(res['unknown'])}" if res.get("unknown") else ""))
    for c in res["counts"][:20]:
        print(f"  {c['count']:>6}  {c['event']}")
    for e in res["events"]:
        args_text = ", ".join(json.dumps(a, ensure_ascii=False) for a in e["args"])
        print(f"{e['t']:8.3f}  {e['event']}({args_text}{', ...' if e['n'] > len(e['args']) else ''})")
    return 0


def cmd_inspect(client, args):
    if not args.mouse and not args.target:
        say_error("a target (a Lua expression that gives a frame) or --mouse")
        return 2
    res = client.inspect(args.target, args.mouse, args.depth, args.snap)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    def show(f, indent=""):
        state = "shown" if f.get("shown") else "hidden"
        if f.get("shown") and not f.get("visible"):
            state = "shown, parent hidden"
        size = "x".join(str(round(v)) for v in (f.get("size") or []))
        print(f"{indent}{f.get('type', '?')} {f.get('name')}  {state}  size {size}  rect {f.get('rect')}"
              + (f"  {f.get('strata')}/{f.get('level')}" if f.get("strata") else "")
              + (f"  text {json.dumps(f['text'], ensure_ascii=False)}" if f.get("text") else ""))
        for p in f.get("points") or []:
            print(f"{indent}    {p[0]} -> {p[1]}:{p[2]} ({p[3]}, {p[4]})")
        if f.get("parents"):
            print(f"{indent}    in {' < '.join(str(p) for p in f['parents'])}")
        for c in (f.get("children") or []) + (f.get("regions") or []):
            show(c, indent + "  ")

    for f in res["frames"]:
        show(f)
    if not res["frames"]:
        print("nothing there")
    if res.get("snap"):
        print(f"snap: {res['snap']['path']}")
    return 0


def show_event(e):
    data = json.dumps(e.get("data"), ensure_ascii=False)
    print(f"{fmt_time(e['t'])}  {e.get('addon')}  {e.get('topic')}  {data}"
          + (f"  ({e['dropped']} dropped before it)" if e.get("dropped") else ""))


def cmd_events(client, args):
    since = args.since
    while True:
        res = client.addon_events(args.addon, args.topic, since, 1000, 25 if args.follow else 0)
        for e in res["events"]:
            if args.json:
                print(json.dumps(e, ensure_ascii=False))
            else:
                show_event(e)
        since = res["next"]
        if not args.follow:
            if not res["events"] and not args.json:
                print("no events (an addon sends them with WB:Emit(topic, data), WB = WoWBridge.Bind(addonName))")
            return 0


def cmd_call(client, args):
    try:
        call_args = json.loads(args.args) if args.args else None
    except ValueError as e:
        say_error(f"args: not JSON ({e})")
        return 2
    res = client.call_exposed(args.addon, args.name, call_args, args.timeout_ms)
    print(json.dumps(res.get("result"), ensure_ascii=False, indent=2))
    return 0


def cmd_addon_api(client, args):
    res = client.addon_api(args.addon)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    for a in res.get("addons") or [res]:
        print(a.get("addon"))
        for x in a.get("exposed") or []:
            print(f"  call {x['name']}" + (f"  {x['doc']}" if x.get("doc") else ""))
        for topic, n in sorted((a.get("topics") or {}).items()):
            print(f"  event {topic}  ({n} sent)")
        if not a.get("exposed") and not a.get("topics"):
            print("  nothing yet (WB:Expose / WB:Emit, WB = WoWBridge.Bind(addonName))")
    return 0


def fmt_day(t):
    return time.strftime("%m-%d %H:%M:%S", time.localtime(t))


def change_text(v):
    """"Core.lua changed, 1 added" for a version summary or a comparison"""
    n = v.get("counts") or {}
    parts = []
    for kind in ("changed", "added", "removed"):
        names = v.get(kind) or []
        if names:
            more = n.get(kind, len(names)) - 1
            parts.append(f"{names[0]}{f' (+{more})' if more > 0 else ''} {kind}")
    return ", ".join(parts) or "no change"


def cmd_history(client, args):
    res = client.history(args.addon, args.id, args.against)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if not args.addon:
        for r in res["addons"]:
            print(f"{r['addon']:<28} {r['versions']:>3} versions, the latest #{r['latest_id']} {fmt_day(r['latest'])}, "
                  f"{r.get('bytes', 0) / 1048576:.1f} MB")
        if not res["addons"]:
            print("no kept versions yet: they are kept when an addon is loaded or watched (or wuxian checkpoint)")
        return 0
    if args.id is None:
        now = res.get("now")
        state = ("" if now is None else "its folder is gone" if now.get("missing") else now["error"] if now.get("error")
                 else f"the same as #{now['since']}" if now["same"] else f"since #{now['since']}: {change_text(now)}")
        print(f"{res['addon']}: {len(res['versions'])} kept versions" + (f"; now {state}" if state else ""))
        for v in res["versions"]:
            print(f"  #{v['id']:<4} {fmt_day(v['time'])}  {v['reason']:<8} {v.get('note') or '':<24} {change_text(v)}")
        return 0
    print(f"{res['addon']}: #{res['base']} -> {res['to'] if res['to'] == 'now' else '#' + str(res['to'])}, "
          f"{len(res['files'])} files")
    for f in res["files"]:
        if f.get("binary"):
            print(f"{f['status']}: {f['path']} (binary, {f.get('old_size')} -> {f.get('new_size')} bytes)")
        elif f.get("diff"):
            print(f["diff"])
        else:
            print(f"{f['status']}: {f['path']} (+{f.get('plus')} -{f.get('minus')})")
    if res.get("truncated"):
        print("(the diffs are cut short there; each file's + and - counts are in --json)")
    return 0


def cmd_checkpoint(client, args):
    res = client.checkpoint(args.addon, " ".join(args.note))
    if res["new"]:
        print(f"kept {args.addon} #{res['id']} ({res['count']} files)")
    else:
        print(f"{args.addon}: no change since #{res['id']}, which stays the latest")
    return 0


def cmd_restore(client, args):
    res = client.restore(args.addon, args.id)
    print(f"{args.addon} is back to #{res['restored']}: {len(res['written'])} files written, {len(res['removed'])} removed")
    if res.get("saved"):
        print(f"the files before are #{res['saved']} (wuxian restore {args.addon} {res['saved']} undoes this)")
    if res.get("skipped"):
        print(f"not kept, so not written: {', '.join(res['skipped'])}")
    if res.get("watched"):
        print("it is watched: the files written are loaded into the game again")
    else:
        print(f"the game still runs the code it has: wuxian load {args.addon}, or wuxian reload")
    return 0


def cmd_reload(client, args):
    res = client.reload(" ".join(args.reason))
    print(f"reload requested (nonce {res.get('nonce')}): the player has to click the button in the game")
    return 0


def update_line(s):
    """one line for an update state (updater.Updater.status())"""
    if not s.get("supported"):
        return f"updates: not supported here ({s.get('reason')})"
    text = {"idle": "not checked yet", "checking": "checking", "current": "up to date",
            "available": f"{s.get('latest')} available ({(s.get('size') or 0) / 1048576:.1f} MB)",
            "downloading": f"downloading {s.get('latest')}: {s.get('progress')}%",
            "ready": f"{s.get('latest')} downloaded: `wuxian update apply` restarts into it",
            "error": s.get("error") or "error"}.get(s.get("state"), s.get("state"))
    return f"version {s.get('current')}: {text}"


def cmd_update(client, args):
    if args.action is None:
        print(update_line(client.update()))
        return 0
    if args.action == "apply":
        client.update("apply", background=args.background)
        print("the program stops now; Velopack puts the new version in place and starts it")
        return 0
    s = client.update(args.action)
    while s.get("state") in ("checking", "downloading"):
        print(update_line(s), flush=True)
        time.sleep(1.0)
        s = client.update()
    print(update_line(s))
    return 1 if s.get("state") == "error" else 0


def cmd_new(client, args):
    res = client.new_addon(args.name, args.title, args.notes, args.template)
    print(f"made {res['path']}: {', '.join(res['files'])}; {res['slash']} in the game")
    print(res.get("hint", ""))
    return 0


def cmd_agents(args):
    """the agents' connection to this program's MCP server (agents.py), done here (no daemon)"""
    from .. import agents
    if args.host != "all" and args.host not in agents.HOSTS:
        say_error(f"host: one of {', '.join(agents.HOSTS)} or all")
        return 2
    out = agents.status()
    rows = [h for h in out["hosts"] if args.host in ("all", h["id"])]
    failed = 0
    if args.action is not None:
        fn = {"connect": agents.connect, "disconnect": agents.disconnect, "verify": agents.verify}[args.action]
        done = []
        for h in rows:
            if args.host == "all" and h["state"] == "missing":
                continue                                  # all: the agents that are not here are left out
            try:
                done.append(fn(h["id"]))
            except agents.AgentError as e:
                say_error(f"{h['id']}: {e}")
                failed += 1
        rows = done
    if args.json:
        print(json.dumps(dict(out, hosts=rows), ensure_ascii=False, indent=2))
        return 1 if failed else 0
    mark = {"ok": "[ok]", "other": "[!!]", "absent": "[--]", "missing": "[  ]", "error": "[!!]"}
    for h in rows:
        print(f"{mark.get(h['state'], '[??]')} {h['title']:<12} {h['detail']}")
        if h.get("verify"):
            print(f"     {'✓' if h['verify']['ok'] else '✕'} {h['verify']['text']}")
        if h["state"] != "missing":
            print(f"     {h['where']}")
    if args.action is None:
        print()
        print(f"the command they start: {out['program']['command']} {' '.join(out['program']['args'])}")
    return 1 if failed else 0


def cmd_api(args):
    """the API manual, read here (no daemon)"""
    from .. import apidocs
    ix = apidocs.index()
    if args.get:
        out = ix.get(args.get)
        if out is None:
            say_error(f"{args.get}: not in the API manual")
            return 1
    elif args.manual is not None:
        out = ix.manual(args.manual or None)
        if out is None:
            say_error(f"no manual topic {args.manual!r}")
            return 1
    elif args.words:
        out = ix.search(" ".join(args.words), args.kind, args.limit)
    else:
        out = ix.about()
    if args.json or not isinstance(out, list) and not (isinstance(out, dict) and "md" in out):
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif isinstance(out, dict):
        print(f"# {out['title']}\n\n{out['md']}")
    else:
        for r in out:
            flags = f"  [{'; '.join(r['flags'])}]" if r["flags"] else ""
            print(f"{r['kind']:<8} {r['sig']}{flags}")
    return 0


def cmd_logs(client, args):
    since = args.since
    if since is None:                                   # the tail: the last 50 entries (of these kinds), page by page
        since, tail = 0, []
        while True:
            res = client.logs(since, 1000, args.kinds)
            tail = (tail + res["entries"])[-50:]
            since = res["next"]
            if len(res["entries"]) < 1000:
                break
        for e in tail:
            print_entry(e)
    else:
        res = client.logs(since, args.limit, args.kinds)
        for e in res["entries"]:
            print_entry(e)
        since = res["next"]
    if not args.follow:
        return 0
    try:
        for event, data in client.events(since, args.kinds):
            if event == "log":
                print_entry(data)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_say(client, args):
    client.say(" ".join(args.text))
    print("queued")
    return 0


def cmd_doctor(client, args):
    res = client.doctor(args.game)
    marks = {True: "ok", False: "!!", None: "??"}       # ok, failed, unknown (could not be checked here)
    for c in res["checks"]:
        print(f"[{marks.get(c.get('ok'), '!!')}] {c.get('title') or c.get('id')}{tr('：', ': ')}{c.get('detail') or ''}"
              + (tr(f"\n     修复动作：{c['fix']}（wuxian doctor --fix）", f"\n     fix: {c['fix']} (wuxian doctor --fix)")
                 if c.get("fix") and c.get("ok") is False else ""))
    if not res.get("available", True):
        print("(the self-check module is not available yet)", file=sys.stderr)
    return 1 if any(c.get("ok") is False for c in res["checks"]) else 0


def cmd_addons(client, args):
    res = client.addons(args.game)
    c = res["client"]
    print(f"{res['addons_dir']}  (client {c.get('version') or '?'}, Interface {c.get('interface') or '?'})")
    for a in res["addons"]:
        iface = ",".join(map(str, a["interface"])) or "?"
        flags = [a["ours"]] if a.get("ours") else []
        if a["current"] is False:
            flags.append("Interface out of date")
        if a["enabled"] is False:
            flags.append("disabled")
        elif a["enabled"] is None:
            flags.append(f"disabled for {a['disabled_in']} characters")
        if a["load_on_demand"]:
            flags.append("load on demand")
        err = a["errors"]
        if err["signatures"]:
            flags.append(f"errors: {err['signatures']} signatures, {err['count']} times")
        print(f"  {a['name']:<28} {a.get('version') or '-':<12} {iface:<8} {', '.join(flags)}")
    e = res["errors"]
    if e.get("available"):
        state = {None: "not decided yet", True: "on", False: "off"}.get(e.get("report"), str(e.get("report")))
        print(f"error collection: {state}; {e['signatures']} signatures as of {fmt_time(e['written'])} "
              f"(the client writes them at logout or /reload)")
    else:
        print(f"error collection: {e.get('reason')}")
    return 0


def cmd_errors(client, args):
    res = client.errors(args.addon, args.limit, args.game)
    if not res.get("available"):
        print(res.get("reason") or "no errors collected", file=sys.stderr)
        return 0
    for e in res["entries"]:
        when = fmt_time(e["last"]) if isinstance(e.get("last"), (int, float)) else "?"
        print(f"{when} {e['kind']} x{e['count']} [{e.get('addon') or '?'}] {e['message']}")
        if e.get("stack"):
            print("    " + e["stack"].strip().replace("\n", "\n    "))
    print(f"({len(res['entries'])} of {res['total']})", file=sys.stderr)
    return 0


def cmd_install(client, args):
    res = client.install(args.game, clean=not args.no_clean, mode=args.mode)
    if not res.get("available", True):
        print(res.get("detail") or "the installer is not available yet", file=sys.stderr)
        return 1
    print(f"installed ({res.get('mode') or '?'} mode) {len(res.get('installed') or [])} files into {res.get('addons_dir')}, "
          f"removed {len(res.get('removed') or [])}")
    if res.get("restart_for_link"):
        print("new files: fully exit and restart the game (new files are only discovered at launch)")
    elif res.get("restart_required"):
        print("a .toc changed or folders were removed: restart the game when convenient to see it (it keeps working)")
    return 0


def cmd_quit(args):
    info = read_daemon_json()
    if info is None:
        print("the daemon is not running")
        return 0
    try:
        client = connect(start=False)
    except ConnectionError:
        print("the daemon is not running (a stale daemon.json)")
        return 0
    client.quit()
    for _ in range(50):
        if read_daemon_json() is None:
            print("stopped")
            return 0
        time.sleep(0.1)
    print("quit sent; the daemon has not removed daemon.json yet")
    return 0


def cmd_mcp_config(args):
    info = read_daemon_json()
    print(mcpconfig.snippet(args.host, info) if args.host else mcpconfig.all_snippets(info), end="")
    return 0


CLIENT_COMMANDS = dict(status=cmd_status, run=cmd_run, load=cmd_load, watch=cmd_watch, snap=cmd_snap, reload=cmd_reload,
                       trace=cmd_trace, inspect=cmd_inspect, check=cmd_check, history=cmd_history, checkpoint=cmd_checkpoint,
                       **{"try": cmd_try},
                       restore=cmd_restore,
                       logs=cmd_logs, say=cmd_say, doctor=cmd_doctor, addons=cmd_addons, errors=cmd_errors,
                       install=cmd_install, update=cmd_update, new=cmd_new, events=cmd_events, call=cmd_call,
                       **{"addon-api": cmd_addon_api})


def run_program(name, rest):
    module = importlib.import_module(PROGRAMS[name][0])
    sys.argv = [f"wuxian {name}", *rest]                # the program's argparse reads its options from here
    try:
        return module.main() or 0
    except KeyboardInterrupt:                           # companion / monitor run until Ctrl+C
        return 130


def run_shell(argv=()):
    """`wuxian` without arguments, or `wuxian app ...`: the window (ui.shell.run_app); the daemon alone while the ui is
    not there"""
    try:
        from ..ui.shell import run_app
    except ImportError as e:
        print(f"the desktop window is not available ({e}); running the daemon alone (wuxian serve)", file=sys.stderr)
        return run_program("serve", [])
    return run_app(list(argv)) or 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        return run_shell()
    ap = build_parser()
    ns, rest = ap.parse_known_args(argv)
    if ns.command == "app":
        return run_shell(rest)
    if ns.command in PROGRAMS:
        return run_program(ns.command, rest)
    if rest:
        ap.error(f"unrecognized arguments: {' '.join(rest)}")
    for stream in (sys.stdout, sys.stderr):      # the checks' titles are Chinese; a pipe would otherwise get GBK
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if ns.command == "quit":
        return cmd_quit(ns)
    if ns.command == "mcp-config":
        return cmd_mcp_config(ns)
    if ns.command == "api":
        return cmd_api(ns)
    if ns.command == "agents":
        return cmd_agents(ns)
    try:
        client = connect(log=lambda text: print(text, file=sys.stderr, flush=True))
    except ConnectionError as e:
        say_error(str(e))
        return 2
    try:
        return CLIENT_COMMANDS[ns.command](client, ns)
    except ApiError as e:
        say_error(f"{e.message} ({e.status} {e.code})")
        if e.code == "check_failed" and isinstance(e.extra.get("check"), dict):
            print_findings(e.extra["check"], limit=10)
        if e.code == "timeout" and e.extra.get("job") is not None:
            print(f"job {e.extra['job']}: its result, if it comes later, is in `wuxian logs --kinds RUN`", file=sys.stderr)
        return 1
    except ConnectionError as e:
        say_error(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
