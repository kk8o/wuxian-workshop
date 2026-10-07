r"""Check an installation and repair what can be repaired.

    wuxian doctor [--game "<client folder>"] [--fix] [--json]

run_checks() returns one dict per check: id, title, ok (True / False / None = could not tell), detail, and fix: the id of
the action that repairs it ("install", "install_clean", "create_mail", "install_webview2", "install_dotnet") or None.
--fix runs those actions (apply_fixes) and checks again; the daemon serves the same list as GET /api/doctor and runs a
fix with POST /api/fix.
Checked: the client folder and its Interface\AddOns; the client version (.build.info in the client folder or next to it,
the row matched to .flavor.info) against the builds this release was verified with; each product addon installed and
identical to the bundled copy, its .toc Interface against the client's and its Version against the program's (the
addons always carry the program's version: an update of the program brings them along, and the daemon installs them
when the game is not running; scripts/set_version.py); the font
mailbox (4,096 slots and proc.ttf); an AddOns.txt under WTF that disables an addon; what the build takes from Windows
(runtimes.py: the WebView2 Runtime, the .NET Framework 4.7.2+, a Windows build with Windows Graphics Capture);
leftovers of earlier versions and the experiments; the run-time folder being writable.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from . import ADDON_FILE_TYPES, MODES, addon_source_dir, addons_for
from .addons import addons_txt_states, client_info, read_toc, toc_with_interface
from .. import __version__
from ..i18n import tr
from ..core import mailbox
from ..core.game import game_dir as find_game_dir, read_build_info, read_flavor  # noqa: F401 (read_flavor: tests)
from ..paths import home, state_dir
from . import install as inst
from . import runtimes
from .runtimes import dotnet_release, webview2_version, windows_build      # names the tests replace

FIX_INSTALL, FIX_INSTALL_CLEAN, FIX_CREATE_MAIL = "install", "install_clean", "create_mail"
FIX_WEBVIEW2, FIX_DOTNET = "install_webview2", "install_dotnet"            # Microsoft's installers (runtimes.install)
RUNTIME_FIXES = {FIX_WEBVIEW2: "webview2", FIX_DOTNET: "dotnet"}


def _check(id, title, ok, detail, fix=None):
    return dict(id=id, title=title, ok=ok, detail=detail, fix=fix)


def _skipped(id, title, why=None):
    why = why or tr("没有找到客户端目录", "no client folder found")
    return _check(id, title, None, tr(f"跳过：{why}", f"skipped: {why}"))


def base_version(v):
    """the release part of a version: 0.8.0.dev0 -> 0.8.0 (the .toc says 0.8.0, the package may say .devN)"""
    m = re.match(r"\d+(?:\.\d+)*", v or "")
    return m.group(0) if m else (v or "")



def addon_files(folder):
    """{relative posix path: bytes} of the .lua / .toc / .xml / .tga files of an addon folder"""
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*"))
            if p.is_file() and p.suffix.lower() in ADDON_FILE_TYPES}


def _runtime_checks():
    """what the build takes from Windows (runtimes.py): the WebView2 Runtime, the .NET Framework, the Windows build"""
    pv = webview2_version()
    title = tr("WebView2 运行时", "WebView2 Runtime")
    checks = [_check("webview2", title, True, tr(f"版本 {pv}", f"version {pv}")) if pv else
              _check("webview2", title, False, tr("未安装：程序窗口需要它（修复：运行微软的安装程序，约 2 MB，由它下载运行时）",
                                                  "not installed: the program's window needs it (the fix runs Microsoft's "
                                                  "installer, about 2 MB, which downloads the runtime)"), FIX_WEBVIEW2)]
    release = dotnet_release()
    if release is not None and release >= runtimes.DOTNET_MIN_RELEASE:
        checks.append(_check("dotnet", ".NET Framework", True,
                             tr(f"{runtimes.dotnet_name(release)}（Release 值 {release}）", f"{runtimes.dotnet_name(release)} (Release {release})")))
    else:
        have = (tr(f"{runtimes.dotnet_name(release)}（Release 值 {release}）", f"{runtimes.dotnet_name(release)} (Release {release})")
                if release is not None else tr("未安装 4.x", "no 4.x installed"))
        checks.append(_check("dotnet", ".NET Framework", False,
                             tr(f"{have}：程序窗口需要 4.7.2 或更高版本（修复：运行微软的 .NET Framework 4.8 安装程序，装完可能需要重启 Windows）",
                                f"{have}: the program's window needs 4.7.2 or later (the fix runs Microsoft's .NET Framework 4.8 "
                                f"installer; Windows may need a restart after it)"), FIX_DOTNET))
    build = windows_build()
    if build >= runtimes.WGC_MIN_BUILD:
        checks.append(_check("windows", tr("Windows 版本", "Windows version"), True,
                             tr(f"内部版本 {build}：支持 Windows 图形捕获（WGC）", f"build {build}: Windows Graphics Capture (WGC) available")))
    else:
        checks.append(_check("windows", tr("Windows 版本", "Windows version"), None,
                             tr(f"内部版本 {build}：Windows 图形捕获（WGC）至少需要 Windows 10 1903（{runtimes.WGC_MIN_BUILD}）；"
                                f"改用 GDI 读屏幕，帧码所在的地方不能被遮住",
                                f"build {build}: Windows Graphics Capture (WGC) needs Windows 10 1903 ({runtimes.WGC_MIN_BUILD}) "
                                f"or later; GDI reads the screen instead, and the frame code must not be covered")))
    return checks


def _addon_checks(name, addons, source, expected_interface, client_version):
    src, dst = source / name, addons / name
    cid = f"addon:{name}"
    inst_title, iface_title, ver_title = addon_titles(name)
    if not src.is_dir():
        nocopy = tr("没有程序自带的副本", "the program has no copy of it")
        return [_check(f"{cid}:installed", inst_title, False, tr(f"找不到程序自带的副本：{src}", f"the program's own copy is missing: {src}")),
                _skipped(f"{cid}:interface", iface_title, nocopy),
                _skipped(f"{cid}:version", ver_title, nocopy)]
    want, have = addon_files(src), addon_files(dst) if dst.is_dir() else {}
    if expected_interface:                                   # installed with the client's ## Interface (install.py)
        want = {f: toc_with_interface(d, expected_interface) if f.lower().endswith(".toc") else d for f, d in want.items()}
    toc = read_toc(dst / f"{name}.toc")
    src_toc = read_toc(src / f"{name}.toc") or {}
    checks = []
    if toc is None:
        checks.append(_check(f"{cid}:installed", inst_title, False, tr(f"未安装：{dst}", f"not installed: {dst}"), FIX_INSTALL))
    else:
        differ = sorted(f for f in want if have.get(f) != want[f])
        if differ:
            checks.append(_check(f"{cid}:installed", inst_title, False,
                                 tr(f"{len(want)} 个文件中有 {len(differ)} 个与程序自带的副本不同：{'、'.join(differ[:5])}",
                                    f"{len(differ)} of {len(want)} files differ from the program's own copy: {', '.join(differ[:5])}"),
                                 FIX_INSTALL))
        else:
            checks.append(_check(f"{cid}:installed", inst_title, True,
                                 tr(f"{len(want)} 个文件，与程序自带的副本一致", f"{len(want)} files, the same as the program's own copy")))
    # ## Interface against the client's number; a reinstall only helps when the bundled .toc has the right one
    not_installed = tr("未安装", "not installed")
    if toc is None:
        checks.append(_skipped(f"{cid}:interface", iface_title, not_installed))
    elif expected_interface is None:
        checks.append(_check(f"{cid}:interface", iface_title, None,
                             tr(f"toc 里的 Interface 是 {toc.get('Interface', '?')}；客户端的版本（{client_version or '未知'}）读不出 Interface 号",
                                f"the .toc says Interface {toc.get('Interface', '?')}; the client's version ({client_version or 'unknown'}) "
                                f"gives no Interface number")))
    elif toc.get("Interface") == str(expected_interface):
        checks.append(_check(f"{cid}:interface", iface_title, True,
                             tr(f"Interface {expected_interface}，与客户端 {client_version} 一致",
                                f"Interface {expected_interface}, the same as client {client_version}")))
    else:                                                    # a game update changed it: a reinstall writes the new one
        checks.append(_check(f"{cid}:interface", iface_title, False,
                             tr(f"toc 里的 Interface 是 {toc.get('Interface', '?')}，客户端 {client_version} 是 {expected_interface}"
                                f"（游戏更新了）：重新安装就写入新的号（游戏关着时程序会自己装），游戏才不会把它当成过期插件",
                                f"the .toc says Interface {toc.get('Interface', '?')}, client {client_version} has {expected_interface} "
                                f"(the game was updated): a reinstall writes the new number (the program does it itself while the "
                                f"game is closed), so that the game does not treat it as out of date"),
                             FIX_INSTALL))
    # ## Version against the program's: the addons always carry it (the program brings its addons along)
    program = base_version(__version__)
    if toc is None:
        checks.append(_skipped(f"{cid}:version", ver_title, not_installed))
    elif base_version(toc.get("Version")) == program:
        checks.append(_check(f"{cid}:version", ver_title, True,
                             tr(f"{toc.get('Version')}，与程序一致", f"{toc.get('Version')}, the same as the program")))
    elif base_version(src_toc.get("Version")) == program:
        checks.append(_check(f"{cid}:version", ver_title, False,
                             tr(f"游戏里的是 {toc.get('Version', '?')}，程序是 {program}：程序带来的插件更新还没装进游戏"
                                f"（游戏关着时程序会自己装）",
                                f"the game has {toc.get('Version', '?')}, the program is {program}: the addon update the program "
                                f"brought is not in the game yet (the program installs it itself while the game is closed)"),
                             FIX_INSTALL))
    else:
        checks.append(_check(f"{cid}:version", ver_title, False,
                             tr(f"游戏里的是 {toc.get('Version', '?')}，程序自带的副本是 {src_toc.get('Version', '?')}，"
                                f"都不是程序的 {program}：需要更新程序",
                                f"the game has {toc.get('Version', '?')}, the program's own copy {src_toc.get('Version', '?')}, "
                                f"neither is the program's {program}: the program needs an update")))
    return checks


def addon_titles(name):
    """the three checks of an addon: installed, its Interface number, its version"""
    return (tr(f"插件 {name} 已安装", f"Addon {name} installed"), tr(f"插件 {name} 的 Interface 号", f"Addon {name}'s Interface number"),
            tr(f"插件 {name} 的版本", f"Addon {name}'s version"))


def _mailbox_check(addons):
    wb = addons / "WoWBridge"
    title = tr("字体信箱", "Font mailbox")
    if not wb.is_dir():
        return _check("mailbox", title, False, tr("WoWBridge 未安装，所以没有信箱", "WoWBridge is not installed, so there is no mailbox"), FIX_INSTALL)
    missing = sum(1 for i in range(1, mailbox.POOL + 1) if not mailbox.slot_path(addons, i).exists())
    proc = mailbox.proc_path(addons).exists()
    if not missing and proc:
        return _check("mailbox", title, True, tr(f"{mailbox.POOL} 个信箱槽位和 proc.ttf 齐全（{mailbox.mail_dir(addons)}）",
                                                 f"all {mailbox.POOL} mailbox slots and proc.ttf are there ({mailbox.mail_dir(addons)})"))
    what = (([tr(f"缺少 {missing} 个信箱槽位（共 {mailbox.POOL} 个）", f"{missing} of the {mailbox.POOL} mailbox slots missing")] if missing else [])
            + ([] if proc else [tr("缺少 proc.ttf", "proc.ttf missing")]))
    return _check("mailbox", title, False, tr("，", ", ").join(what), FIX_CREATE_MAIL)


def _developer_components_check(addons):
    """an install of the platform addon alone (`wuxian install --mode player`): WoWBridge draws a frame on the screen and
    runs what the program sends, so it should not be there"""
    present = [p.name for p in inst.developer_components(addons)]
    title = developer_components_title()
    if not present:
        return _check("developer_components", title, True, tr("未安装", "not installed"))
    return _check("developer_components", title, False,
                  tr(f"已安装 {'、'.join(present)}：开发组件会在屏幕上画帧码，并执行程序发来的 Lua",
                     f"{', '.join(present)} installed: the developer addons draw a frame code on the screen and run the Lua "
                     f"the program sends"), FIX_INSTALL_CLEAN)


def developer_components_title():
    return tr("没有开发组件（只装平台插件时）", "No developer addons (with the platform addon alone)")


def addons_txt_title():
    return tr("插件未被禁用（AddOns.txt）", "Addons not disabled (AddOns.txt)")


def leftovers_title():
    return tr("旧版本与实验残留", "Leftovers of old versions and experiments")


def _addons_txt_check(game, names):
    found = addons_txt_states(game, set(names))
    title = addons_txt_title()
    if not found:
        return _check("addons_txt", title, None,
                      tr("未知：WTF\\Account 下没有 AddOns.txt（每个角色登录后，客户端会为它写一份）",
                         "unknown: no AddOns.txt under WTF\\Account (the client writes one for each character that logs in)"))
    disabled = [tr(f"{name} 在 {rel} 里被禁用", f"{name} is disabled in {rel}")
                for rel, states in found for name, st in states.items() if st == "disabled"]
    if disabled:
        return _check("addons_txt", title, False, tr("；", "; ").join(disabled)
                      + tr("（请在游戏的插件列表里启用）", " (enable it in the game's addon list)"))
    return _check("addons_txt", title, True, tr(f"已在 {len(found)} 个 AddOns.txt 里启用", f"enabled in {len(found)} AddOns.txt"))


def _leftovers_check(addons):
    left = inst.leftovers(addons)
    foreign = inst.foreign_slots(addons)
    note = (tr(f"（{'、'.join(p.name for p in foreign)} 里有别的文件，保留不动）",
               f" ({', '.join(p.name for p in foreign)} hold other files and are left alone)") if foreign else "")
    if not left:
        return _check("leftovers", leftovers_title(), True, tr("无", "none") + note)
    names = [p.relative_to(addons).as_posix() for p in left]
    shown = tr("、", ", ").join(names[:6]) + (tr(f" 等 {len(names)} 个文件夹", f" … {len(names)} folders in all") if len(names) > 6 else "")
    return _check("leftovers", leftovers_title(), False, shown + note, FIX_INSTALL_CLEAN)


def _runtime_dir_check():
    try:
        probe = state_dir() / ".write-test"
        probe.write_bytes(b"ok")
        probe.unlink()
        return _check("runtime_dir", tr("运行时目录可写", "Runtime folder writable"), True, str(home()))
    except OSError as e:
        return _check("runtime_dir", tr("运行时目录可写", "Runtime folder writable"), False,
                      tr(f"{home()}：{e}（请把 WUXIAN_HOME 设为可写的文件夹）", f"{home()}: {e} (point WUXIAN_HOME at a folder you can write)"))


def run_checks(game_dir=None, source=None, mode="developer"):
    """[dict(id, title, ok, detail, fix)]; game_dir as for install(), source: the bundled addon folder to compare with,
    mode: what should be installed (player: the platform addon only; developer: WoWBridge and its mailbox too)"""
    names = addons_for(mode)
    source = Path(source) if source else addon_source_dir()
    try:
        game = find_game_dir(game_dir)
    except SystemExit:
        game = None
    checks = []
    addons = game / "Interface" / "AddOns" if game else None
    if addons is not None and addons.is_dir():
        checks.append(_check("game_dir", tr("客户端目录", "Client folder"), True, str(game)))
    else:
        checks.append(_check("game_dir", tr("客户端目录", "Client folder"), False,
                             tr(f"{game} 里没有 Interface\\AddOns", f"{game} has no Interface\\AddOns") if game
                             else tr("没有找到：请用 --game <客户端目录> 指定", "not found: name it with --game <client folder>")))
        addons = game = None                                     # not a client folder: the checks below are skipped
    # client version
    info = read_build_info(game) if game else None
    client = client_info(game) if game else dict(version=None, interface=None, source=None)
    version, expected_interface = client["version"], client["interface"]
    vtitle = tr("客户端版本", "Client version")
    if game is None:
        checks.append(_skipped("client_version", vtitle))
    elif info is None:
        checks.append(_check("client_version", vtitle, None, tr("未知：客户端目录和它的上一级都没有 .build.info",
                                                                "unknown: neither the client folder nor the one above has a .build.info")))
    else:
        product = info['product'] or info['flavor'] or '?'
        who = tr(f"{version}（分支 {info['branch'] or '?'}，{product}）", f"{version} (branch {info['branch'] or '?'}, {product})")
        how = {"game": tr("游戏报告的", "as the game reports"), "verified": tr("本版程序已验证", "verified with this release"),
               "version": tr("按版本号推算", "worked out from the version")}.get(client["source"])
        if expected_interface is not None:
            checks.append(_check("client_version", vtitle, True, tr(f"{who}：Interface {expected_interface}（{how}）",
                                                                    f"{who}: Interface {expected_interface} ({how})")))
        else:
            checks.append(_check("client_version", vtitle, None, tr(f"{who}：读不出它的 Interface 号", f"{who}: no Interface number for it")))
    for name in names:
        if addons is None:
            checks += [_skipped(f"addon:{name}:{k}", t) for k, t in zip(("installed", "interface", "version"), addon_titles(name))]
        else:
            checks += _addon_checks(name, addons, source, expected_interface, version)
    if mode == "developer":
        checks.append(_mailbox_check(addons) if addons is not None else _skipped("mailbox", tr("字体信箱", "Font mailbox")))
    else:
        checks.append(_developer_components_check(addons) if addons is not None
                      else _skipped("developer_components", developer_components_title()))
    checks.append(_addons_txt_check(game, names) if game else _skipped("addons_txt", addons_txt_title()))
    checks += _runtime_checks()
    checks.append(_leftovers_check(addons) if addons is not None else _skipped("leftovers", leftovers_title()))
    checks.append(_runtime_dir_check())
    return checks


def apply_fixes(checks, game_dir=None, source=None, mode="developer"):
    """run the fix actions the failed checks ask for; returns the action ids run. A (clean) install covers the addon
    ones; a runtime's installer is started (it carries on in its own window)"""
    wanted = {c["fix"] for c in checks if c["ok"] is False and c["fix"]}
    ran = []
    for fix in sorted(wanted & set(RUNTIME_FIXES)):
        runtimes.install(RUNTIME_FIXES[fix])
        ran.append(fix)
    if wanted & {FIX_INSTALL, FIX_INSTALL_CLEAN}:
        inst.install(game_dir, clean=True, source=source, mode=mode)
        ran.append(FIX_INSTALL_CLEAN if FIX_INSTALL_CLEAN in wanted else FIX_INSTALL)
    elif FIX_CREATE_MAIL in wanted:
        mailbox.install(inst.addons_dir(game_dir))
        ran.append(FIX_CREATE_MAIL)
    return ran


def report(checks, fixed=()):
    """the checks as text lines, one per check, and a summary"""
    mark = {True: "[ok]", False: "[!!]", None: "[??]"}
    lines = [f"{mark[c['ok']]} {c['title']}{tr('：', ': ')}{c['detail']}"
             + (tr(f"（修复动作：{c['fix']}）", f" (fix: {c['fix']})") if c["ok"] is False and c["fix"] else "") for c in checks]
    failed = [c for c in checks if c["ok"] is False]
    unknown = sum(1 for c in checks if c["ok"] is None)
    fixes = sorted({c["fix"] for c in failed if c["fix"]})
    summary = tr(f"共 {len(checks)} 项检查，{len(failed)} 项失败，{unknown} 项未知",
                 f"{len(checks)} checks, {len(failed)} failed, {unknown} unknown")
    if fixed:
        summary += tr(f"；已执行：{'、'.join(fixed)}", f"; done: {', '.join(fixed)}")
    if fixes:
        summary += tr(f"；`wuxian doctor --fix` 会执行：{'、'.join(fixes)}", f"; `wuxian doctor --fix` would run: {', '.join(fixes)}")
    return lines + [summary]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wuxian doctor", description=__doc__.split("\n")[0])
    ap.add_argument("--game", help="client folder that holds Interface\\AddOns (default: the running game's, else _cn_beta_)")
    ap.add_argument("--fix", action="store_true", help="run the repairs the failed checks ask for, then check again")
    ap.add_argument("--mode", choices=MODES, default="developer", help="what should be installed (default: developer)")
    ap.add_argument("--source", help="addon folder to compare with (default: the program's own copy)")
    ap.add_argument("--json", action="store_true", help="print the checks as JSON")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # Chinese titles, also when piped (cp936 otherwise)
    checks = run_checks(args.game, source=args.source, mode=args.mode)
    fixed = []
    if args.fix:
        try:
            fixed = apply_fixes(checks, args.game, source=args.source, mode=args.mode)
        except (FileNotFoundError, RuntimeError) as e:
            print(tr(f"wuxian doctor: 修复失败：{e}", f"wuxian doctor: the fix failed: {e}"), file=sys.stderr)
            return 2
        if fixed:
            checks = run_checks(args.game, source=args.source, mode=args.mode)
    if args.json:
        print(json.dumps(dict(checks=checks, fixed=fixed), indent=1, ensure_ascii=False))
    else:
        print("\n".join(report(checks, fixed)))
    return 0 if all(c["ok"] is not False for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
