r"""What the client's AddOns folder holds, for the 插件 page and GET /api/addons: one entry per addon folder from its .toc
(title, version, Interface, notes, dependencies, load on demand), whether the AddOns.txt files under WTF\Account disable
it, and the errors !WuxianWorkshop collected for it.

The errors come from !WuxianWorkshop's SavedVariables (WTF\Account\<account>\SavedVariables\!WuxianWorkshop.lua, read
with core.savedvars, never run): the client writes that file at logout, /reload and exit, so it is as of then. Only the
most recently written account's file is read. The AddOns.txt files are per character; only how many of them disable an
addon is reported, not whose they are.
"""
import json
import re
from pathlib import Path

from . import DEVELOPER_ADDON, LAB_ADDON, PLATFORM_ADDON, VERIFIED_CLIENTS
from ..agent.lint import toc_line
from ..core import savedvars
from ..core.game import game_dir as find_game_dir, read_build_info
from ..paths import state_dir

OURS = {PLATFORM_ADDON: "platform", DEVELOPER_ADDON: "developer", LAB_ADDON: "lab"}
ERRORS_FILE = "!WuxianWorkshop.lua"
_ESCAPES = [(re.compile(r"\|c[0-9a-fA-F]{8}"), ""), (re.compile(r"\|r"), ""), (re.compile(r"\|[TA][^|]*\|[ta]"), ""),
            (re.compile(r"\|n"), " ")]


def read_toc(path):
    """the `## Key: value` headers of a .toc as a dict, or None when the file is missing: the lines for this client's
    game type, without their load conditions (agent/lint.py toc_line: "## Title: Foo [AllowLoadGameType camelot]" ->
    "Foo"; a line for other game types is left out), the last one of a key"""
    path = Path(path)
    if not path.is_file():
        return None
    out = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = re.match(r"##\s*([^:]+?)\s*:\s*(.*?)\s*$", line)
        if m:
            value, here = toc_line(m.group(2))
            if here:
                out[m.group(1)] = value
    return out


def addons_txt_states(game, names):
    """[(AddOns.txt relative to WTF, {addon: state})] for the addons in `names`, from every AddOns.txt under WTF\\Account"""
    wtf = Path(game) / "WTF"
    out = []
    for p in sorted(set(wtf.glob("Account/*/AddOns.txt")) | set(wtf.glob("Account/*/*/*/AddOns.txt"))):
        states = {}
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            name, sep, state = line.partition(":")
            if sep and name.strip() in names:
                states[name.strip()] = state.strip().lower()
        out.append((p.relative_to(wtf).as_posix(), states))
    return out


def plain(text):
    """a .toc text without the client's escapes (colours, textures, atlases)"""
    for pattern, repl in _ESCAPES:
        text = pattern.sub(repl, text)
    return text.strip()


def find_toc(folder):
    """the .toc of an addon folder: <folder>.toc, else one with a flavour suffix (<folder>_Mainline.toc and the like,
    Mainline first); None when there is none"""
    folder = Path(folder)
    exact = folder / f"{folder.name}.toc"
    if exact.is_file():
        return exact
    others = sorted(p for p in folder.glob("*.toc")
                    if p.stem.lower().startswith(folder.name.lower()) and p.stem[len(folder.name):len(folder.name) + 1] in "_-")
    others.sort(key=lambda p: "mainline" not in p.stem.lower())
    return others[0] if others else None


def _list(value):
    return [v.strip() for v in (value or "").split(",") if v.strip()]


VERSION_PARTS = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
TOC_INTERFACE = re.compile(rb"^(##[ \t]*Interface[ \t]*:)[^\r\n]*", re.M)


def interface_of_version(version):
    """the ## Interface number WoW gives a client version: X.Y.Z(.build) -> X*10000 + Y*100 + Z (1.60.1.70245 -> 16001,
    11.0.2 -> 110002, 1.15.7 -> 11507); None when the version does not read like one"""
    m = VERSION_PARTS.match(version or "")
    if not m:
        return None
    x, y, z = map(int, m.groups())
    return x * 10000 + y * 100 + z if y < 100 and z < 100 else None


def learned_clients():
    """{client version: Interface} as the game itself reported them (WoWBridge's HELLO: GetBuildInfo), state/clients.json"""
    try:
        data = json.loads((state_dir() / "clients.json").read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in data.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def remember_client(version, interface):
    """keeps what the game reported; True when it was not known yet"""
    if not version or not isinstance(interface, int) or interface <= 0:
        return False
    known = learned_clients()
    if known.get(version) == interface:
        return False
    known[version] = interface
    (state_dir() / "clients.json").write_text(json.dumps(known, indent=1, sort_keys=True), encoding="utf-8")
    return True


def client_info(game):
    """dict(version, interface, source) of the client in `game`: the version from .build.info; the Interface the game itself
    reported (source "game"), else the one this release was verified with ("verified"), else the one its version gives
    ("version"). A game update therefore never leaves the program without the number its addons need; interface is None
    only when the version is unknown or does not read like one"""
    info = (read_build_info(game) or {}) if game else {}
    version = info.get("version") or None
    if version:
        for source, value in (("game", learned_clients().get(version)), ("verified", VERIFIED_CLIENTS.get(version)),
                              ("version", interface_of_version(version))):
            if value:
                return dict(version=version, interface=value, source=source)
    return dict(version=version, interface=None, source=None)


def client_interface(game):
    """(client version, its Interface number or None): client_info() as a pair"""
    c = client_info(game)
    return c["version"], c["interface"]


def toc_with_interface(data, interface):
    """a .toc (bytes) whose ## Interface is `interface`: the program's addons go into a client with that client's number,
    so that a game update that changes it does not leave them out of date (not loaded, the link gone)"""
    if not interface:
        return data
    num = str(int(interface)).encode()
    if TOC_INTERFACE.search(data):
        return TOC_INTERFACE.sub(lambda m: m.group(1) + b" " + num, data, count=1)
    return b"## Interface: " + num + b"\n" + data


def describe(folder, interface=None):
    """one addon folder as a dict for the list"""
    folder = Path(folder)
    toc_path = find_toc(folder)
    toc = read_toc(toc_path) if toc_path else {}
    nums = [int(n) for n in re.findall(r"\d+", toc.get("Interface", ""))]
    if not nums or interface is None:
        current = None
    else:
        current = interface in nums
    deps = []
    for key, value in toc.items():
        if key.lower() in ("dependencies", "requireddeps") or key.lower().startswith("dep"):
            deps += _list(value)
    return dict(
        name=folder.name,
        title=plain(toc.get("Title-zhCN") or toc.get("Title") or folder.name),
        version=toc.get("Version") or None,
        interface=nums,
        current=current,                                   # its Interface is the client's (None: cannot tell)
        notes=plain(toc.get("Notes-zhCN") or toc.get("Notes") or "") or None,
        author=toc.get("Author") or None,
        deps=deps,
        optional_deps=_list(toc.get("OptionalDeps")),
        load_on_demand=toc.get("LoadOnDemand", "").strip() == "1",
        saved_variables=_list(toc.get("SavedVariables")) + _list(toc.get("SavedVariablesPerCharacter")),
        wuxian_id=toc.get("X-Wuxian-ID") or None,
        toc=toc_path.name if toc_path else None,
        ours=OURS.get(folder.name),
        # the addon it is listed under (## Group); our components belong to the platform addon also where an older
        # install's .toc does not say so
        group=toc.get("Group") or (PLATFORM_ADDON if OURS.get(folder.name) in ("developer", "lab") else None),
    )


def read_errors(game):
    """!WuxianWorkshop's error store from the most recently written account: dict(available, written, report, dropped,
    entries=[{signature, kind, message, addon, stack, count, first, last, build}], newest first); available False with
    a reason when there is none or it cannot be read"""
    files = sorted(Path(game).glob(f"WTF/Account/*/SavedVariables/{ERRORS_FILE}"), key=lambda p: p.stat().st_mtime)
    if not files:
        return dict(available=False, reason="no SavedVariables of !WuxianWorkshop yet (the client writes them at logout "
                                            "or /reload)", entries=[])
    path = files[-1]
    try:
        db = savedvars.load(path).get("WuxianWorkshopDB")
    except (OSError, savedvars.ParseError) as e:
        return dict(available=False, reason=f"{path.name}: {e}", entries=[])
    if not isinstance(db, dict):
        return dict(available=False, reason=f"{path.name} has no WuxianWorkshopDB", entries=[])
    settings = db.get("settings") if isinstance(db.get("settings"), dict) else {}
    entries = []
    for sig, e in (db.get("errors") or {}).items():
        if not isinstance(e, dict):
            continue
        entries.append(dict(signature=str(sig), kind=e.get("kind") or "ERR", message=e.get("message") or str(sig),
                            addon=e.get("addon") or None, stack=e.get("stack") or None, count=e.get("count") or 1,
                            first=e.get("first"), last=e.get("last"), build=e.get("build")))
    entries.sort(key=lambda e: (e["last"] or 0), reverse=True)
    return dict(available=True, written=round(path.stat().st_mtime, 3), report=settings.get("report"),
                dropped=db.get("dropped") or 0, entries=entries)


def list_addons(game_dir=None):
    """everything the 插件 page shows: {addons_dir, client: {version, interface}, characters (AddOns.txt files read),
    addons: [describe() + enabled, disabled_in, errors: {signatures, count}], errors: read_errors() without the entries}.
    FileNotFoundError when the client folder or its AddOns folder is not found"""
    try:
        game = find_game_dir(game_dir)
    except SystemExit as e:                                # core.game says so for the command line
        raise FileNotFoundError(str(e)) from None
    addons = Path(game) / "Interface" / "AddOns"
    if not addons.is_dir():
        raise FileNotFoundError(f"no Interface\\AddOns in {game}")
    version, interface = client_interface(game)
    folders = sorted((p for p in addons.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    states = addons_txt_states(game, {p.name for p in folders})
    errors = read_errors(game)
    by_addon = {}
    for e in errors["entries"]:
        b = by_addon.setdefault(e["addon"], dict(signatures=0, count=0))
        b["signatures"] += 1
        b["count"] += e["count"] if isinstance(e["count"], int) else 1
    out = []
    for folder in folders:
        a = describe(folder, interface)
        said = [st[folder.name] for _, st in states if folder.name in st]
        a["disabled_in"] = sum(1 for s in said if s == "disabled")
        a["enabled"] = True if not a["disabled_in"] else (False if a["disabled_in"] == len(said) else None)
        a["errors"] = by_addon.get(folder.name, dict(signatures=0, count=0))
        out.append(a)
    summary = {k: v for k, v in errors.items() if k != "entries"}
    summary["signatures"] = len(errors["entries"])
    summary["unattributed"] = by_addon.get(None, dict(signatures=0, count=0))
    return dict(addons_dir=str(addons), client=dict(version=version, interface=interface), characters=len(states),
                addons=out, errors=summary)


def list_errors(game_dir=None, addon=None, limit=100):
    """read_errors() of the client folder, only the entries of `addon` (its folder name) when given, at most limit"""
    try:
        game = find_game_dir(game_dir)
    except SystemExit as e:
        raise FileNotFoundError(str(e)) from None
    errors = read_errors(game)
    entries = [e for e in errors["entries"] if addon is None or e["addon"] == addon]
    return dict(errors, entries=entries[:max(1, int(limit))], total=len(entries))
