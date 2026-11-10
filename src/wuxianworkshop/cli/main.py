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
    wuxian api-scan --out FILE                what the running game has, for the API manual's content pack
    wuxian events [--addon A] [--topic T]... [--follow | --once [--max S]]   what addons sent with WoWBridge's Emit
    wuxian listen <app> [--once] [--since N] [--max S]   join a running WuxianKit app, print its wakes (events app.<app>)
    wuxian call <addon> <name> [json args]    call a function an addon exposed with WoWBridge's Expose
    wuxian addon-api [addon]                  what addons expose (functions) and emit (event topics)
    wuxian respond <request> [json data]      answer an addon's question (an event of `events` with a request id)
    wuxian history [addon] [id] [--against]   kept versions of addons; one version's diff with the files now
    wuxian checkpoint <addon> [note]          keep a version of an addon's files now
    wuxian restore <addon> <id>               put an addon's files back as a kept version had them
    wuxian reload                             ask the player for a UI reload
    wuxian logs [--follow] [--since N] [--tail] [--kinds ERR,OUT]
    wuxian say <text>                         a line in the game's chat
    wuxian doctor                             the self-check
    wuxian addons                             the installed addons, with the errors collected for each
    wuxian errors [addon] [--limit N]         the errors !WuxianWorkshop collected (as of the last logout or /reload)
    wuxian install [--no-clean] [--game DIR]  install the addon
    wuxian update [check|download|apply]      the program's updates (an installed copy; apply restarts it)
    wuxian new <Name> [--title] [--template]  a new addon from a template (with AGENTS.md for the agent)
    wuxian ext list | add <folder|manifest|zip> | remove <id> | load <id> | stop <id> | set <id> [--on|--off]
               [--option k=v] | check <id|path> | new <id> [--title T] [--addon Name] | pack <id> [--to PATH]
               | install <id> | dir | logs <id> [-f]   local extensions (on this computer), their packages, game addons
    wuxian wk <capability> ['<json args>']    call a capability: a local extension's, or WuxianKit's in the game
    wuxian reaction list | set '<json>' | clear [--id X] [--owner A]   reactions: an item heard calls a capability
    wuxian api <words> | --get NAME | --manual [TOPIC]   this client's API manual (built in; no daemon needed)
    wuxian quit                               stop the daemon
    wuxian mcp-config [claude|codex|cursor|trae-cn|trae|workbuddy|workbuddy-ai]   snippets that register the MCP server
                                              with those hosts
    wuxian agents [connect|disconnect|verify] [claude|codex|cursor|trae-cn|trae|workbuddy|workbuddy-ai|all]   register
                  [--server wuxian|wuxianwow] this program (or 无限图鉴, wuxianwow) with the agents
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
import re
import sys
import time
import unicodedata
from pathlib import Path

from .. import __version__
from ..i18n import tr
from ..daemon.api import ApiError, read_daemon_json
from . import mcpconfig
from .client import connect, reconnect

PROGRAMS = {                # sub-command -> (the module whose main() runs it, one line for the help)
    "app": ("wuxianworkshop.ui.shell", "the desktop window with the daemon and the tray icon (--background: hidden)"),
    "serve": ("wuxianworkshop.daemon.server", "run the daemon: the local API on 127.0.0.1, MCP at /mcp"),
    "mcp": ("wuxianworkshop.mcp.server", "the MCP server on stdio, talking to the daemon"),
    "companion": ("wuxianworkshop.daemon.companion", "run the link alone: read frames off the screen, answer through the mailbox"),
    "monitor": ("wuxianworkshop.agent.monitor", "print every new frame on the game screen"),
}


LISTEN_HELP = """\
join a WuxianKit app that runs and print its wakes until it ends. It never opens the app: open it first
(wk_kit_app_open, or the app's card in the game), then wait here.

lines on stdout:
  OPEN <status JSON>   once: the app runs (kit.app.status)
  {"t": ..., "topic": "app.<app>", "data": {app, reason, items, skipped, after, next, ...}}
                       a wake, one JSON line each. reason "catchup": items the app's wakes brought while nothing
                       listened here (the ones after --since, or a gap), read back from the app's last items
  NEXT <item id>       after each batch of wakes: the newest item so far, the --since of the next run
  END <why>            the app does not run (any more): stopped, player, expired, left, extension ... Nothing is
                       tried again: open the app before the next listen
  TIMEOUT              --max seconds are over (NEXT <item id> comes before it): run it again

It exits with code 0 after END and TIMEOUT and, with --once, after the first batch (an app WuxianKit does not know:
END stopped, code 1). While it waits it is the app's heartbeat (kit.app.status every max(30, lease / 3) seconds).

The game away (not running, minimized, the link down) or the daemon gone: it waits and tries again every 5 seconds,
says so once on stderr, and prints nothing on stdout; only --max ends that (TIMEOUT). Once the game answers again it
goes on where it was, or prints END <why> when the app stopped meanwhile."""


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
    p.add_argument("--whole", action="store_true", help="print the whole first value however long (tostring, up to 16 "
                                                       "MB); --timeout is for the run itself")
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
    p = sub.add_parser("api-scan", help="what the running game has, for the API manual's content pack "
                                        "(scripts/build_api_pack.py reads the file)")
    p.add_argument("--out", required=True, help="the file to write (the data repository's api/game_scan.json)")
    p = sub.add_parser("events", help="the events addons sent with WoWBridge's Emit")
    p.add_argument("--addon", help="only this addon's (a name or a glob)")
    p.add_argument("--topic", action="append",
                   help="only these topics (a name or a glob: scan.*); again for more (any one matches, at most 16)")
    p.add_argument("--since", type=int, default=0, help="from this entry id on (the next of the last call; -1: only new ones)")
    p.add_argument("--follow", "-f", action="store_true", help="keep printing new ones (Ctrl+C ends it)")
    p.add_argument("--once", action="store_true",
                   help="wait for the first ones, print them and NEXT <id>, then exit (an agent runs it in the background "
                        "to be woken)")
    p.add_argument("--max", type=float, default=1500,
                   help="with --once: give up after this many seconds (prints NEXT <id> and TIMEOUT)")
    p.add_argument("--json", action="store_true", help="print JSON")
    p = sub.add_parser("listen", help="join a WuxianKit app that runs and print its wakes (the events app.<app>) until it "
                                       "ends", formatter_class=argparse.RawDescriptionHelpFormatter, description=LISTEN_HELP)
    p.add_argument("app", help="the app's id (lowercase letters, digits and _)")
    p.add_argument("--since", type=int, default=None, metavar="N",
                   help="go on after item N: the NEXT of the last run (or the `next` in wk_kit_app_open's answer). What "
                        "the app's wakes brought after it comes first, as a wake with reason \"catchup\". Without it: "
                        "from the app's last wake on")
    p.add_argument("--once", action="store_true", help="exit after the first batch of wakes (it prints NEXT <item id>: "
                                                       "run it again with --since <item id>)")
    p.add_argument("--max", type=float, default=None, metavar="S",
                   help="give up after this many seconds (prints NEXT <item id> and TIMEOUT); default 1500 with --once, "
                        "else none")
    p = sub.add_parser("call", help="call a function an addon exposed with WoWBridge's Expose")
    p.add_argument("addon", help="the addon's folder name")
    p.add_argument("name", help="the name it exposed")
    p.add_argument("args", nargs="?", help="""its arguments as JSON ('{"n": 2}')""")
    p.add_argument("--timeout-ms", type=int, default=10000, help="how long to wait for the game (default 10000)")
    p = sub.add_parser("respond", help="answer an addon's question (WoWBridge's Request: an event with a request id)")
    p.add_argument("request", help="the request id the event carries (5974.3)")
    p.add_argument("data", nargs="?", help="""the answer as JSON ('{"text": "..."}')""")
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
    p.add_argument("reason", nargs="*", help="shown to the player in the game, and in the log")
    p = sub.add_parser("logs", help="the daemon's log: Lua errors, print output, RUN results, notes")
    p.add_argument("--follow", "-f", action="store_true", help="keep printing new entries (Ctrl+C ends it)")
    p.add_argument("--since", type=int, default=None, help="the first entry id (default: the last 50)")
    p.add_argument("--limit", type=int, default=200, help="at most this many entries (default 200)")
    p.add_argument("--tail", action="store_true", help="the newest --limit entries instead of the oldest")
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
    p = sub.add_parser("ext", help="local extensions (WuxianKit's extensions on this computer): list, add, remove, load, "
                                    "stop, wait, set, check, new, pack, install, dir, logs")
    act = p.add_subparsers(dest="action", metavar="<action>", required=True)
    q = act.add_parser("list", help="the registered local extensions and their state")
    q.add_argument("--json", action="store_true", help="print JSON")
    q = act.add_parser("add", help="add an extension: its folder or its wuxian-extension.json (linked: the folder "
                                   "stays where it is), or a package (.zip: unpacked into the extensions folder, the "
                                   "player allows it in the App before it runs)")
    q.add_argument("path", help="a folder, a wuxian-extension.json or a package's .zip")
    q = act.add_parser("remove", help="take an extension off the registry (stopped first): a linked folder stays, one "
                                      "in the extensions folder moves to its .removed; the game addons it installed "
                                      "are deleted")
    q.add_argument("id")
    q = act.add_parser("load", help="check an extension and (re)start it, run its tests; its game addons are installed "
                                    "first when they are missing or differ")
    q.add_argument("id")
    q = act.add_parser("stop", help="stop an extension")
    q.add_argument("id")
    q = act.add_parser("wait", help="wait for what only the player can do before an extension works (approve it, "
                                    "start or restart the game, enable its addon, turn it on)")
    q.add_argument("id")
    q.add_argument("--seconds", type=float, default=120, help="how long at most (default 120, 600 at most)")
    q = act.add_parser("set", help="turn an extension on or off, set its options")
    q.add_argument("id")
    on = q.add_mutually_exclusive_group()
    on.add_argument("--on", dest="enabled", action="store_const", const=True, help="turn it on")
    on.add_argument("--off", dest="enabled", action="store_const", const=False, help="turn it off (it stops)")
    q.add_argument("--option", action="append", default=[], metavar="NAME=VALUE",
                   help="an option (the value as JSON: 0.8, true, \"text\"; anything else is text); again for more")
    q = act.add_parser("check", help="check an extension (an id, or a folder) without touching the running one")
    q.add_argument("id")
    q.add_argument("--json", action="store_true", help="print JSON")
    q = act.add_parser("new", help="a new extension from the template (default folder: <id> in the extensions folder, "
                                   "see `ext dir`)")
    q.add_argument("id")
    q.add_argument("--title", help="the name the player sees (default: the id)")
    q.add_argument("--folder", help="the full path of its folder (new or empty)")
    q.add_argument("--addon", metavar="NAME", help="with a game addon of this folder name (addons\\NAME in its folder, "
                                                   "installed into the game with the extension)")
    q.add_argument("--where", choices=("game", "local", "both"),
                   help="game: in the game only (a WuxianKit extension, no process); local: a process on this computer; "
                        "both (default: both with --addon, else local)")
    q = act.add_parser("pack", help="pack an extension's folder into a package (<id>-<version>.zip) for `ext add`")
    q.add_argument("id")
    q.add_argument("--to", metavar="PATH", help="a folder, or the .zip itself (default: %%WUXIAN_HOME%%\\packages)")
    q.add_argument("--json", action="store_true", help="print JSON")
    q = act.add_parser("install", help="install or update an extension's game addons now (one the player allowed; a "
                                       "new addon needs a game restart)")
    q.add_argument("id")
    act.add_parser("dir", help="print the extensions folder: a package unpacks there, a folder put there is found")
    q = act.add_parser("logs", help="an extension's output (its stderr and the workshop's lines on its starts and ends)")
    q.add_argument("id")
    q.add_argument("--follow", "-f", action="store_true", help="keep printing new lines (Ctrl+C ends it)")
    q.add_argument("--since", type=int, default=0, help="from this line on (default: from the first; the last --limit "
                                                                 "lines when there are more)")
    q.add_argument("--limit", type=int, default=200, help="at most this many lines (default 200)")
    p = sub.add_parser("wk", help="call a capability: a local extension's, or the game's WuxianKit's (see, keep, point)")
    p.add_argument("cap", help="its id, <extension>.<name>: voice.say, sense.bags")
    p.add_argument("args", nargs="?", help="its arguments as a JSON object")
    p.add_argument("--timeout-ms", type=int, default=None, help="how long to wait for its answer (default: the time "
                                                                "the capability declares, 30 seconds without one)")
    p = sub.add_parser("reaction", help="reactions: an item heard calls a capability, without the agent")
    act = p.add_subparsers(dest="action", metavar="<action>", required=True)
    q = act.add_parser("list", help="the reactions set now")
    q.add_argument("--json", action="store_true", help="print JSON")
    q = act.add_parser("set", help="set one: '{\"hear\": \"game:combat\", \"call\": \"voice.play\", \"args\": {...}}'")
    q.add_argument("spec", help="the reaction as a JSON object")
    q = act.add_parser("clear", help="clear reactions: --id, --owner, or (neither) all")
    q.add_argument("--id")
    q.add_argument("--owner", help="an app id: the reactions it owns")
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
    p = sub.add_parser("mcp-config", help="snippets that register the MCP server with Claude Code, Codex, Cursor, Trae or "
                                          "WorkBuddy")
    p.add_argument("host", nargs="?", choices=mcpconfig.HOSTS, help="one host (default: all of them)")
    p = sub.add_parser("agents", help="the agents (Claude Code, Codex, Cursor, Trae, WorkBuddy) and this MCP server: state, "
                                      "connect, disconnect")
    p.add_argument("action", nargs="?", choices=("connect", "disconnect", "verify"), help="nothing: every agent's state")
    p.add_argument("host", nargs="?", default="all", help="claude, codex, cursor, trae-cn, trae, workbuddy, workbuddy-ai or all "
                                                          "(default)")
    p.add_argument("--server", choices=("wuxian", "wuxianwow"), default="wuxian",
                   help="the MCP server: wuxian (this program, the default) or wuxianwow (无限图鉴, https://wuxianwow.com/mcp)")
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
    whole = getattr(args, "whole", False)
    if whole:
        res = client.run(" ".join(args.code), timeout_ms=int(args.timeout * 1000), addon=args.addon, whole=True)
        if res.get("ok") and not res.get("whole"):
            print("the daemon is older than 0.9.13: this is the run's result, not the whole value (restart the program)",
                  file=sys.stderr)
    else:
        res = client.run(" ".join(args.code), timeout_ms=int(args.timeout * 1000), addon=args.addon)
    if res.get("ok") and res.get("whole"):
        sys.stdout.write(res["values"][0])
        if not res["values"][0].endswith("\n"):
            sys.stdout.write("\n")
        return 0
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
          + (f"  ({e['dropped']} dropped before it)" if e.get("dropped") else "")
          + (f"  [request {e['request']}, answer within {e.get('wait')} s]" if e.get("request") else ""))


def cmd_events(client, args):
    since = args.since
    if args.once:
        return events_once(client, args)
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


def events_once(client, args):
    """--once: the first events from --since on (waiting up to --max seconds), then the id to go on from; exit 0 either
    way, the last line says which (NEXT <id> after events, NEXT <id> and TIMEOUT without)"""
    since, deadline = args.since, time.monotonic() + max(1.0, args.max)
    while True:
        left = deadline - time.monotonic()
        res = client.addon_events(args.addon, args.topic, since, 1000, max(0.0, min(25.0, left)))
        since = res["next"]
        if res["events"]:
            for e in res["events"]:
                if args.json:
                    print(json.dumps(e, ensure_ascii=False))
                else:
                    show_event(e)
            print(f"NEXT {since}")
            return 0
        if left <= 0:
            print(f"NEXT {since}")
            print("TIMEOUT")
            return 0


KIT = "WuxianKit"
APP_ID = re.compile(r"^[a-z0-9_]{1,24}$")
RETRY = 5.0                 # seconds between tries after a failure (the game away, the daemon gone, a call that failed)
clock, sleep, wall = time.monotonic, time.sleep, time.time      # tests replace them


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def whole(v):
    """an item id as it is printed and taken by --since: 12, not 12.0"""
    return int(v) if isinstance(v, float) and v.is_integer() else v


class Done(Exception):
    """the listening is over: its exit code"""

    def __init__(self, code=0):
        super().__init__(code)
        self.code = code


CATCHUP = 100       # the items a catch-up asks kit.app.recent for: all an app keeps (WuxianKit's ring)


class Listening:
    """one `wuxian listen <app>`: it joins a WuxianKit app that runs (it never opens one) and prints its wakes.

    The cursor is WuxianKit's item id: --since N, else the `sent` of the app's status (where its last wake ended:
    what it heard since and still gathers comes with its next wake; a WuxianKit without `sent` gives `next`). The ids
    of the daemon's journal (self.at) only say where its events are read from and are never printed, so a daemon that
    starts again (its journal from 1) neither loses nor repeats an item: what may have been missed is read with
    kit.app.recent, only up to where the app's wakes got (the status's `sent`, or the `after` of the wake that showed
    the gap: the rest comes with a wake, when the app sends it), and printed as a wake `catchup`; what a wake repeats
    is left out by its ids.

    stdout: OPEN <status> once; each wake as one JSON line {t, topic, data}; NEXT <item id> after each batch of them;
    END <why> when the app does not run (any more); TIMEOUT after --max seconds (NEXT before it when the cursor is
    known). Failures go to stderr, each once until it changes, and are tried again every RETRY s: the game away (the
    link down) and the daemon gone are waited for and end nothing by themselves"""

    def __init__(self, client, app, since=None, once=False, limit=None):
        self.client, self.app, self.once = client, app, once
        self.topic = f"app.{app}"
        self.topics = [self.topic, "kit.app", "kit.ready"]
        self.deadline = None if limit is None else clock() + max(1.0, limit)
        self.cursor = since                 # the newest item id printed or passed over; None until the status gave it
        self.missed = since is not None     # wakes may have been missed: kit.app.recent from the cursor on
        self.sent = None                    # where the app's wakes got (the status's sent): a catch-up reads up to it
        self.at = None                      # where the daemon's journal is read from (its own ids)
        self.joined = self.opened = self.woke = False
        self.every = self.beat = None       # the heartbeat: every so many seconds, the next one
        self.retry = 0.0                    # when what failed is tried again
        self.held = []                      # wakes that wait for the cursor or behind a catch-up that failed, in order
        self.said, self.wide = None, False

    def out(self, line):
        print(line, flush=True)

    def failed(self, what, e):
        text = f"{what}: {e.message} ({e.code})" if isinstance(e, ApiError) else f"{what}: {e}"
        if text != self.said:
            say_error(f"{text}; waiting, tried again every {RETRY:g} s")
            self.said = text
        self.retry = clock() + RETRY

    def end(self, why, code=0):
        if self.woke:
            self.out(f"NEXT {self.cursor}")
        self.out(f"END {why if isinstance(why, str) and why else 'stopped'}")
        raise Done(code)

    def kit(self, name, **call_args):
        return (self.client.call_exposed(KIT, name, call_args) or {}).get("result")

    def ask(self, since, wait):
        """one ask for the events; a daemon that takes no list of topics (before 0.9.13) is asked for all of
        WuxianKit's, and they are picked in run"""
        if not self.wide:
            try:
                return self.client.addon_events(KIT, self.topics, since, 1000, wait)
            except ApiError as e:
                if e.code != "bad_request" or not str(e.message).startswith("topic:"):
                    raise
                self.wide = True
        return self.client.addon_events(KIT, None, since, 1000, wait)

    def status(self):
        """kit.app.status, which is the heartbeat too: the status of the running app, or None when it cannot be read
        now (the game away: said, tried again). An app that does not run ends the listening (END <why>, the status's
        why); so does one WuxianKit does not know (END stopped, exit code 1)"""
        try:
            status = self.kit("kit.app.status", app=self.app)
        except ApiError as e:
            if e.status == 401:
                raise
            if e.code != "call_failed":
                self.failed("kit.app.status", e)
                return None
            say_error(f"kit.app.status: {e.message} ({e.code}): WuxianKit has no app {self.app}")
            self.end("stopped", 1)
        if not isinstance(status, dict) or not status.get("on"):
            self.end(status.get("why") if isinstance(status, dict) else None)
        lease = status.get("lease")
        self.every = max(30.0, lease / 3) if is_number(lease) and lease > 0 else None
        self.beat = clock() + self.every if self.every else None
        return status

    def join(self):
        """read the status (the first time: OPEN); the cursor is its `sent` (`next` without one) when --since gave
        none"""
        self.joined = False
        status = self.status()
        if status is None:
            return
        if not self.opened:
            self.opened = True
            self.out("OPEN " + json.dumps(status, ensure_ascii=False))
        newest, sent = status.get("next"), status.get("sent")
        self.sent = whole(sent) if is_number(sent) else None
        if self.cursor is None:
            self.cursor = self.sent if self.sent is not None else whole(newest) if is_number(newest) else 0
        elif is_number(newest) and newest < self.cursor:
            # the app's items here end before the cursor (another character, saved data that started again): from
            # the cursor on every wake would count as a repeat
            print(f"note: the app's newest item is {newest}, before {self.cursor}: going on from {newest}",
                  file=sys.stderr, flush=True)
            self.cursor = whole(newest)
        self.joined, self.said = True, None

    def show(self, t, data):
        self.out(json.dumps(dict(t=t, topic=self.topic, data=data), ensure_ascii=False))
        self.woke = True

    def catch_up(self, upto=None):
        """the items after the cursor that the app's wakes brought and that did not get here, read with
        kit.app.recent and printed as one wake `catchup`: up to `upto` (the `after` of the wake that showed the gap,
        which is printed after it), else up to the status's `sent`; what the app heard later comes with a wake. (A
        WuxianKit without `sent`: all it heard.) False: it cannot be read now (the status is read again first, then
        this)"""
        upto = self.sent if upto is None else upto
        if upto is not None and upto <= self.cursor:
            return True
        try:
            res = self.kit("kit.app.recent", app=self.app, since=self.cursor, limit=CATCHUP)
        except ApiError as e:
            if e.status == 401:
                raise
            self.failed("kit.app.recent", e)
            self.joined = False
            return False
        res = res if isinstance(res, dict) else {}
        items = res.get("items")
        items = [i for i in items if isinstance(i, dict) and is_number(i.get("id")) and i["id"] > self.cursor
                 and (upto is None or i["id"] <= upto)] if isinstance(items, list) else []
        if items:
            newest = upto if upto is not None else \
                whole(max(items[-1]["id"], res["next"] if is_number(res.get("next")) else 0))
            self.show(round(wall(), 3),
                      dict(app=self.app, reason="catchup", items=items, skipped=0, after=self.cursor, next=newest))
            self.cursor = newest
        elif upto is not None:
            self.cursor = upto              # the app keeps them no more: its wakes go on from there
        return True

    def wake(self, e):
        """one wake: {..., items, skipped, after, next} covers the items after < id <= next. A gap before it (after is
        past the cursor) is caught up first, up to its after; what the cursor already passed is left out, a wake of
        only that entirely.
        A wake that brings nothing (idle) is printed as it is"""
        if self.cursor is None or self.held:
            self.held.append(e)
            return
        data = e.get("data")
        if not isinstance(data, dict) or not is_number(data.get("next")):
            self.show(e.get("t"), data)                     # not as WuxianKit sends them: as it is
            return
        if is_number(data.get("after")) and data["after"] > self.cursor and not self.catch_up(whole(data["after"])):
            self.held.append(e)
            return
        items = data["items"] if isinstance(data.get("items"), list) else []
        skipped = is_number(data.get("skipped")) and data["skipped"] > 0
        if (items or skipped) and data["next"] <= self.cursor:
            return                                          # a repeat
        fresh = [i for i in items if not (isinstance(i, dict) and is_number(i.get("id")) and i["id"] <= self.cursor)]
        self.cursor = max(self.cursor, whole(data["next"]))
        if items and not fresh and not skipped:
            return
        self.show(e.get("t"), data if len(fresh) == len(items) else dict(data, items=fresh))

    def batch(self):
        """NEXT after what was printed; with --once that ends it"""
        if self.woke:
            self.woke = False
            self.out(f"NEXT {self.cursor}")
            if self.once:
                raise Done(0)

    def lost(self, e):
        """the daemon does not answer or takes this token no more: go on with the one that runs now (after a restart:
        a new port and token, a journal that starts again; what came meanwhile is caught up), else wait for one. It is
        never started from here"""
        self.failed("the daemon", e)
        fresh = reconnect(self.client)
        if fresh is None or fresh is self.client:
            sleep(RETRY)
            return
        self.client, self.at, self.wide = fresh, None, False
        self.joined, self.missed, self.retry = False, True, 0.0

    def step(self):
        now = clock()
        if self.deadline is not None and now >= self.deadline:
            if self.cursor is not None:
                self.out(f"NEXT {self.cursor}")
            self.out("TIMEOUT")
            raise Done(0)
        try:
            if self.at is None:               # the journal from now on, taken before the status: no wake after it is lost
                self.at = self.ask(-1, 0)["next"]
            if now >= self.retry:
                if not self.joined or (self.beat is not None and now >= self.beat):
                    self.join()
                if self.joined and self.missed:
                    self.missed = not self.catch_up()
                if self.joined and self.held:
                    held, self.held = self.held, []
                    for e in held:
                        self.wake(e)
            self.batch()
            wait = 25.0
            for at in (self.deadline, self.beat if self.joined else self.retry):
                if at is not None:
                    wait = min(wait, at - clock())
            res = self.ask(self.at, max(0.0, wait))
        except ApiError as e:
            if e.status == 401:
                self.lost(e)
            else:
                self.failed("events", e)
                sleep(RETRY)
            return
        except ConnectionError as e:
            self.lost(e)
            return
        self.at = res["next"]
        if res.get("truncated"):              # the journal let go of events before they were read: where the app's
            self.missed = True                # wakes got by now is in its status
            self.joined, self.retry = False, 0.0
        for e in res["events"]:
            topic, data = e.get("topic"), e.get("data") if isinstance(e.get("data"), dict) else {}
            if topic == self.topic:
                self.wake(e)
            elif topic == "kit.app" and data.get("app") == self.app:
                if data.get("on") is not False:           # opened again while it ran: its lease may be another
                    self.joined, self.retry = False, 0.0
                elif data.get("why") != "replaced":
                    self.end(data.get("why"))
            elif topic == "kit.ready":                    # a login or a reload: see that it still runs
                self.joined, self.retry = False, 0.0
        self.batch()

    def run(self):
        try:
            while True:
                self.step()
        except Done as done:
            return done.code


def cmd_listen(client, args):
    """join a WuxianKit app that runs and print its wakes until it ends (Listening)"""
    if not APP_ID.match(args.app):
        say_error("app: lowercase letters, digits and _, at most 24 (the id WuxianKit knows it by)")
        return 2
    if args.since is not None and args.since < 0:
        say_error("--since: an item id, 0 or more (the NEXT of the last run)")
        return 2
    limit = args.max if args.max is not None else (1500 if args.once else None)
    return Listening(client, args.app, args.since, args.once, limit).run()


def cmd_call(client, args):
    try:
        call_args = json.loads(args.args) if args.args else None
    except ValueError as e:
        say_error(f"args: not JSON ({e})")
        return 2
    res = client.call_exposed(args.addon, args.name, call_args, args.timeout_ms)
    print(json.dumps(res.get("result"), ensure_ascii=False, indent=2))
    return 0


def cmd_respond(client, args):
    try:
        data = json.loads(args.data) if args.data else None
    except ValueError as e:
        say_error(f"data: not JSON ({e})")
        return 2
    res = client.respond(args.request, data, args.timeout_ms)
    print(f"delivered to {res.get('addon')} ({res.get('topic')})"
          + (f"; its callback raised: {res['callback_error']}" if res.get("callback_error") else ""))
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


EXT_APPROVAL = {"needed": "not approved yet: the player approves it in the 无限工坊 App (it does not start before that)",
                "changed": "changed since the player approved it: the player approves it again in the 无限工坊 App (it "
                           "does not start before that)"}


def ext_since(t, now=None):
    """when a local extension's state began: the time, with the date when that was not today"""
    now = time.time() if now is None else now
    same_day = time.localtime(t)[:3] == time.localtime(now)[:3]
    return time.strftime("%H:%M:%S" if same_day else "%m-%d %H:%M", time.localtime(t))


EXT_ADDON = {"unknown": "the game's folder is not known",
             "broken": "its folder in the extension is missing or has no .toc",
             "missing": "not in the game: `wuxian ext install {id}` installs it (so does `ext load`)",
             "current": "installed",
             "differs": "the game has another version: `wuxian ext install {id}` updates it (so does `ext load`)",
             "foreign": "the game has an addon of this name that is not this extension's: it is left alone"}


def ext_unmet(u):
    """one of a row's unmet ({what, needs, has}): what the extension requires and does not find"""
    what, needs, has = u.get("what"), u.get("needs"), u.get("has")
    if what == "workshop":
        return f"needs 无限工坊 {needs} or newer (this is {has}): it does not start before that"
    if what == "kit":
        return f"needs WuxianKit {needs} or newer (the game has {has})"
    return f"needs {what} {needs} or newer (there is {has})"


def ext_lines(r, now=None):
    """one local extension for `wuxian ext list` and the answers of add / load / stop / set: its line (id, state, since
    when, title and version, capabilities, home (in the extensions folder) or linked, folder), then indented what is
    wrong with it: its approval when the player has to give it (needed, changed; not when the workshop allows it by
    itself), what it requires and does not find (unmet), why it is in this state (exited, backoff, hung), its error (a
    manifest that cannot be read), and its game addons with their states. A row of an older daemon has no version,
    origin, unmet or addons: its lines are as they were"""
    state = r["state"]
    if state == "exited" and r.get("exit_code") is not None:
        state += f" with code {r['exit_code']}"
    if r["state"] in ("starting", "running", "hung", "stopping", "exited", "backoff") and is_number(r.get("since")):
        state += f" since {ext_since(r['since'], now)}"
    if r["state"] in ("starting", "running", "hung", "stopping") and r.get("pid"):
        state += f" (pid {r['pid']})"
    caps = len(r.get("capabilities") or [])
    version = f" {r['version']}" if r.get("version") else ""
    origin = f"{r['origin']}  " if r.get("origin") else ""
    lines = [f"{r['id']:<16} {state:<34} {r['title']}{version}  [{caps} capabilities]  {origin}{r['path']}"]
    if r.get("approval") in EXT_APPROVAL and r.get("allowed") is not True:
        lines.append(f"    {EXT_APPROVAL[r['approval']]}")
    for u in r.get("unmet") or []:
        if isinstance(u, dict) and not (u.get("what") == "workshop" and r.get("error")):   # the error says that one
            lines.append(f"    {ext_unmet(u)}")
    for text in (r.get("why"), r.get("error")):
        if text:
            lines.extend(f"    {line}" for line in str(text).splitlines())
    for g in r.get("addons") or []:
        if isinstance(g, dict):
            name = " ".join(str(x) for x in (g.get("folder"), g.get("version")) if x)
            said = EXT_ADDON.get(g.get("state"), str(g.get("state"))).replace("{id}", str(r["id"]))
            lines.append(f"    game addon {name}: {said}")
    if isinstance(r.get("game"), dict) and r["game"].get("extensions"):
        lines.append(f"    in the game: {', '.join(map(str, r['game']['extensions']))} {r['game'].get('state')}")
    if isinstance(r.get("player"), dict) and r["player"].get("text"):
        lines.append(f"    the player: {r['player']['text']}")
    return lines


def print_ext(r, lead=""):
    lines = ext_lines(r)
    print(lead + lines[0])
    for line in lines[1:]:
        print(line)


def print_game_side(res):
    """what an answer (ext load, ext install) says of the extension's game addons, when it says any: each one with
    what was done ({folder, state: installed | current, was, version}), whether the game has to be restarted, and
    the daemon's hint"""
    for g in res.get("addons") or []:
        if isinstance(g, dict):
            name = " ".join(str(x) for x in (g.get("folder"), g.get("version")) if x)
            was = f" (it was {g['was']})" if g.get("state") == "installed" and g.get("was") else ""
            print(f"game addon {name}: {g.get('state')}{was}")
    if res.get("restart"):
        print("restart the game: it finds new addons and new files only when it starts")
    if res.get("hint") and isinstance(res["hint"], str):
        print(res["hint"])


def size_text(n):
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} bytes"


def option_value(text):
    """NAME=VALUE of --option: the value as JSON, else as text"""
    name, eq, value = text.partition("=")
    if not eq or not name.strip():
        raise ValueError(f"--option {text}: NAME=VALUE")
    try:
        return name.strip(), json.loads(value)
    except ValueError:
        return name.strip(), value


def print_problems(problems):
    for p in problems:
        print(f"{p['level']}: {p['where']}: {p['message']}", file=sys.stderr)
        if p.get("hint"):
            print(f"    {p['hint']}", file=sys.stderr)


def print_check(check):
    print_problems(check.get("problems") or [])
    tests = check.get("tests")
    if tests:
        print(f"tests: {tests['ran']} ran, {tests['failed']} failed", file=sys.stderr)
    print(("ok" if check.get("ok") else "not ok") + f": {len(check.get('capabilities') or [])} capabilities"
          + (f" ({', '.join(check['capabilities'])})" if check.get("capabilities") else ""))


def cmd_ext(client, args):
    a = args.action
    if a == "list":
        res = client.local_extensions()
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0
        for r in res["extensions"]:
            print_ext(r)
        if not res["extensions"]:
            print("no local extensions registered: `wuxian ext new <id>` makes one, `wuxian ext add <folder or .zip>` "
                  "adds one")
        return 0
    if a == "dir":
        home = client.local_extensions().get("home")
        if not home:
            say_error("the daemon does not say where its extensions folder is (an older one: update 无限工坊)")
            return 1
        print(home)
        return 0
    if a == "add":
        res = client.extension_add(str(Path(args.path).resolve()))
        package = res.get("package") if isinstance(res.get("package"), dict) else None
        print_ext(res, "installed: " if package else "registered: ")     # a package's row says the player allows it
        if package and package.get("replaced"):
            print(f"    the folder it replaced is kept: {package['replaced']}")
        return 0
    if a == "remove":
        res = client.extension_remove(args.id)
        kept = f"its folder moved to {res['kept']}" if res.get("kept") else f"{res['path']}; its files stay"
        print(f"{res['id']} is no longer registered ({kept})")
        for g in res.get("addons") or []:
            if isinstance(g, dict) and g.get("removed"):
                history = f" (kept: `wuxian history {g.get('folder')}` {g['kept']})" if g.get("kept") else ""
                print(f"    game addon {g.get('folder')}: deleted from the game's AddOns{history}")
            elif isinstance(g, dict):
                why = "the game holds its files" if g.get("why") == "busy" else "it is not this extension's"
                print(f"    game addon {g.get('folder')}: left in the game's AddOns ({why})")
        if res.get("hint") and isinstance(res["hint"], str):
            print(f"    {res['hint']}")
        return 0
    if a == "load":
        res = client.extension_load(args.id)
        print_check(res["check"])
        print_game_side(res)
        for g in res.get("loaded") or []:
            if isinstance(g, dict):
                print(f"game addon {g.get('folder')}: " + ("ran in the game" if g.get("ok") else
                                                           f"failed in the game: {g.get('error')}"))
        print_ext(res["row"])
        return 0 if res["check"].get("ok") else 1
    if a == "install":
        res = client.extension_install(args.id)
        print_game_side(res)
        if not res.get("addons"):
            print(f"{res.get('id', args.id)}: no game addons to install")
        return 0
    if a == "pack":
        res = client.extension_pack(args.id, str(Path(args.to).resolve()) if args.to else None)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0
        print(f"packed {res.get('id', args.id)} {res.get('version') or ''}".rstrip() + f": {res.get('file')}")
        skipped = res.get("skipped")
        skipped = len(skipped) if isinstance(skipped, (list, tuple, dict)) else skipped
        facts = [size_text(res["size"]) if is_number(res.get("size")) else None,
                 f"{res['files']} files" if is_number(res.get("files")) else None,
                 f"{skipped} left out" if is_number(skipped) and skipped else None,
                 f"sha256 {res['sha256']}" if res.get("sha256") else None]
        if any(facts):
            print("    " + ", ".join(f for f in facts if f))
        return 0
    if a == "stop":
        res = client.extension_stop(args.id)
        print_ext(res)
        if res.get("note") and isinstance(res["note"], str):
            print(f"    {res['note']}")
        return 0
    if a == "wait":
        res = client.extension_wait(args.id, args.seconds)
        print_ext(res["row"])
        was = res.get("was") if isinstance(res.get("was"), dict) else None
        print("nothing to wait for" if was is None else
              f"still waiting: {was.get('text')}" if res.get("timeout") else f"done: {was.get('text')}")
        return 1 if res.get("timeout") else 0
    if a == "set":
        try:
            options = dict(option_value(o) for o in args.option) or None
        except ValueError as e:
            say_error(str(e))
            return 2
        print_ext(client.extension_set(args.id, args.enabled, options))
        return 0
    if a == "check":
        res = client.extension_check(args.id)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            for g in res.get("addons") or []:                    # one in the game only: its addons as `check` reads them
                if isinstance(g, dict):
                    print_findings({level: [dict(f, file=f"{g.get('folder')}/{f.get('file') or ''}")
                                            for f in g.get(level) or [] if isinstance(f, dict)]
                                    for level in ("errors", "warnings")})
            print_check(res)
        return 0 if res.get("ok") else 1
    if a == "new":
        res = client.new_extension(args.id, args.title, args.folder, args.addon,
                                   *((args.where,) if args.where else ()))     # said only when given
        print(f"made {res['row']['path']}: {', '.join(Path(f).name for f in res['files'])}")
        print(res.get("guide", ""))
        return 0
    return ext_logs(client, args)


def ext_logs(client, args):
    """`wuxian ext logs`: the lines; with --follow the new ones as they come"""
    since, limit = max(0, args.since), max(1, min(args.limit, 2000))
    try:
        while True:
            res = client.extension_logs(args.id, since, limit)
            if res.get("cut"):
                print(f"-- its last {len(res['lines'])} of {res['total']} lines (--since 1: from the first)",
                      file=sys.stderr, flush=True)
            if res.get("reset") and since:
                print("-- the output was moved to output.1.log: from the first line of the new one", file=sys.stderr,
                      flush=True)
            for line in res["lines"]:
                print(line["text"], flush=True)
            since = res["next"]
            if not args.follow:
                return 0
            if not res["lines"]:
                time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def cmd_wk(client, args):
    try:
        call_args = json.loads(args.args) if args.args else None
    except ValueError as e:
        say_error(f"args: not JSON ({e})")
        return 2
    res = client.local_call(args.cap, call_args, args.timeout_ms)
    print(json.dumps(res.get("result"), ensure_ascii=False, indent=2))
    return 0


def reaction_line(r):
    what = f"{r.get('id')}: {r.get('hear')} -> {r.get('call')}"
    if r.get("owner"):
        what += f" (owner {r['owner']})"
    what += f", fired {r.get('fired') or 0}"
    if r.get("error"):
        what += f"; error: {r['error']}"
    return what


def cmd_reaction(client, args):
    a = args.action
    if a == "list":
        res = client.reactions()
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0
        for r in res["reactions"]:
            print(reaction_line(r))
        if not res["reactions"]:
            print("no reactions set")
        return 0
    if a == "set":
        try:
            spec = json.loads(args.spec)
        except ValueError as e:
            say_error(f"spec: not JSON ({e})")
            return 2
        print(reaction_line(client.reaction_set(spec)))
        return 0
    print(f"cleared {client.reaction_clear(args.id, args.owner)['cleared']}")
    return 0


def pad(text, width):
    """text padded to width columns of the terminal (a CJK character takes two)"""
    cols = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(1, width - cols)


def cmd_agents(args):
    """the agents' connection to this program's MCP server, or 无限图鉴's (--server wuxianwow) (agents.py), done here
    (no daemon)"""
    from .. import agents
    if args.host != "all" and args.host not in agents.HOSTS:
        say_error(f"host: one of {', '.join(agents.HOSTS)} or all")
        return 2
    out = agents.status(server=args.server)
    rows = [h for h in out["hosts"] if args.host in ("all", h["id"])]
    failed = 0
    if args.action is not None:
        fn = {"connect": agents.connect, "disconnect": agents.disconnect, "verify": agents.verify}[args.action]
        done = []
        for h in rows:
            if args.host == "all" and h["state"] == "missing":
                continue                                  # all: the agents that are not here are left out
            try:
                done.append(fn(h["id"], server=args.server))
            except agents.AgentError as e:
                say_error(f"{h['id']}: {e}")
                failed += 1
        rows = done
    if args.json:
        print(json.dumps(dict(out, hosts=rows), ensure_ascii=False, indent=2))
        return 1 if failed else 0
    mark = {"ok": "[ok]", "other": "[!!]", "absent": "[--]", "missing": "[  ]", "error": "[!!]"}
    for h in rows:
        print(f"{mark.get(h['state'], '[??]')} {pad(h['title'], 13)}{h['detail']}")
        if h.get("verify"):
            print(f"     {'✓' if h['verify']['ok'] else '✕'} {h['verify']['text']}")
        if h["state"] != "missing":
            print(f"     {h['where']}")
    if args.action is None:
        print()
        what = out["program"]
        print(f"the command they start: {what['command']} {' '.join(what['args'])}" if "command" in what
              else f"the server: {what['name']} {what['url']}")
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


FIELDS_AT_ONCE = 300            # tables whose fields one api_scan call brings (an answer stays far under probes.MOST)


def cmd_api_scan(client, args):
    """what the running game has (its C_ namespaces' functions, its global functions, its Enum and Constants tables, the
    fields of the ones the API manual does not document), written for scripts/build_api_pack.py"""
    from .. import apidocs
    scan = dict(scanner=f"wuxian api-scan {__version__}", scanned=time.strftime("%Y-%m-%d"), replaced={})
    for part in ("namespaces", "globals", "tables"):
        print(f"{part} ...", file=sys.stderr, flush=True)
        got = client.api_scan(part)
        if scan.setdefault("client", got["client"]) != got["client"]:
            say_error(f"the game changed during the scan ({scan['client']}, then {got['client']}): run it again")
            return 1
        scan["replaced"].update(got.get("replaced") or {})          # {} comes back as [] (J: an empty table)
        for key in ("namespaces", "addons", "globals", "enums", "constants"):
            if key in got:
                scan[key] = got[key] or ({} if key == "namespaces" else [])
    kinds = {"Enumeration": "Enum", "Constants": "Constants"}
    documented = {f"{kinds[e['item'].get('type')]}.{e['short']}" for e in apidocs.index().entries
                  if e["kind"] == "table" and e["item"].get("type") in kinds and not e.get("undocumented")}
    wanted = [n for n in [f"Enum.{x}" for x in scan["enums"]] + [f"Constants.{x}" for x in scan["constants"]]
              if n not in documented]
    scan["fields"] = {}
    for i in range(0, len(wanted), FIELDS_AT_ONCE):
        print(f"fields {i + 1}-{min(i + FIELDS_AT_ONCE, len(wanted))} of {len(wanted)} ...", file=sys.stderr, flush=True)
        got = client.api_scan("fields", wanted[i:i + FIELDS_AT_ONCE])
        scan["fields"].update({k: v or {} for k, v in (got.get("fields") or {}).items()})
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(scan, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    by = {}
    for addon in scan["replaced"].values():
        by[addon] = by.get(addon, 0) + 1
    print(f"{scan['client']}: {len(scan['namespaces'])} C_ namespaces ({sum(map(len, scan['namespaces'].values()))} "
          f"functions), {len(scan['globals'])} global functions, {len(scan['enums'])} Enum and {len(scan['constants'])} "
          f"Constants tables (the fields of {len(scan['fields'])} the manual does not document) -> {args.out}")
    if by:
        print(f"left out, set by addons: {len(scan['replaced'])} names ("
              + ", ".join(f"{a} {n}" for a, n in sorted(by.items(), key=lambda kv: -kv[1])[:8])
              + "); a scan with only WoWBridge loaded leaves none out")
    return 0


def cmd_logs(client, args):
    since = args.since
    if args.tail:                                       # the newest --limit entries (from --since on), in one answer
        res = client.logs(since or 0, args.limit, args.kinds, True)
        for e in res["entries"]:
            print_entry(e)
        since = res["next"]
    elif since is None:                                 # the tail: the last 50 entries (of these kinds), page by page
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
                       **{"try": cmd_try, "api-scan": cmd_api_scan},
                       restore=cmd_restore,
                       logs=cmd_logs, say=cmd_say, doctor=cmd_doctor, addons=cmd_addons, errors=cmd_errors,
                       install=cmd_install, update=cmd_update, new=cmd_new, events=cmd_events, listen=cmd_listen,
                       ext=cmd_ext, wk=cmd_wk, reaction=cmd_reaction,
                       call=cmd_call, respond=cmd_respond,
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
