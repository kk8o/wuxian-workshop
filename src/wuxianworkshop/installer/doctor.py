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


def _skipped(id, title, why="没有找到客户端目录"):
    return _check(id, title, None, f"跳过：{why}")


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
    checks = [_check("webview2", "WebView2 运行时", True, f"版本 {pv}") if pv else
              _check("webview2", "WebView2 运行时", False, "未安装：程序窗口需要它（修复：运行微软的安装程序，约 2 MB，"
                     "由它下载运行时）", FIX_WEBVIEW2)]
    release = dotnet_release()
    if release is not None and release >= runtimes.DOTNET_MIN_RELEASE:
        checks.append(_check("dotnet", ".NET Framework", True, f"{runtimes.dotnet_name(release)}（Release 值 {release}）"))
    else:
        have = f"{runtimes.dotnet_name(release)}（Release 值 {release}）" if release is not None else "未安装 4.x"
        checks.append(_check("dotnet", ".NET Framework", False, f"{have}：程序窗口需要 4.7.2 或更高版本（修复：运行微软的"
                             f" .NET Framework 4.8 安装程序，装完可能需要重启 Windows）", FIX_DOTNET))
    build = windows_build()
    if build >= runtimes.WGC_MIN_BUILD:
        checks.append(_check("windows", "Windows 版本", True, f"内部版本 {build}：支持 Windows 图形捕获（WGC）"))
    else:
        checks.append(_check("windows", "Windows 版本", None, f"内部版本 {build}：Windows 图形捕获（WGC）至少需要 Windows 10 "
                             f"1903（{runtimes.WGC_MIN_BUILD}）；改用 GDI 读屏幕，帧码所在的地方不能被遮住"))
    return checks


def _addon_checks(name, addons, source, expected_interface, client_version):
    src, dst = source / name, addons / name
    cid = f"addon:{name}"
    if not src.is_dir():
        return [_check(f"{cid}:installed", f"插件 {name} 已安装", False, f"找不到程序自带的副本：{src}"),
                _skipped(f"{cid}:interface", f"插件 {name} 的 Interface 号", "没有程序自带的副本"),
                _skipped(f"{cid}:version", f"插件 {name} 的版本", "没有程序自带的副本")]
    want, have = addon_files(src), addon_files(dst) if dst.is_dir() else {}
    if expected_interface:                                   # installed with the client's ## Interface (install.py)
        want = {f: toc_with_interface(d, expected_interface) if f.lower().endswith(".toc") else d for f, d in want.items()}
    toc = read_toc(dst / f"{name}.toc")
    src_toc = read_toc(src / f"{name}.toc") or {}
    checks = []
    if toc is None:
        checks.append(_check(f"{cid}:installed", f"插件 {name} 已安装", False, f"未安装：{dst}", FIX_INSTALL))
    else:
        differ = sorted(f for f in want if have.get(f) != want[f])
        if differ:
            checks.append(_check(f"{cid}:installed", f"插件 {name} 已安装", False,
                                 f"{len(want)} 个文件中有 {len(differ)} 个与程序自带的副本不同：{'、'.join(differ[:5])}",
                                 FIX_INSTALL))
        else:
            checks.append(_check(f"{cid}:installed", f"插件 {name} 已安装", True, f"{len(want)} 个文件，与程序自带的副本一致"))
    # ## Interface against the client's number; a reinstall only helps when the bundled .toc has the right one
    iface_title = f"插件 {name} 的 Interface 号"
    if toc is None:
        checks.append(_skipped(f"{cid}:interface", iface_title, "未安装"))
    elif expected_interface is None:
        checks.append(_check(f"{cid}:interface", iface_title, None,
                             f"toc 里的 Interface 是 {toc.get('Interface', '?')}；客户端的版本（{client_version or '未知'}）"
                             f"读不出 Interface 号"))
    elif toc.get("Interface") == str(expected_interface):
        checks.append(_check(f"{cid}:interface", iface_title, True,
                             f"Interface {expected_interface}，与客户端 {client_version} 一致"))
    else:                                                    # a game update changed it: a reinstall writes the new one
        checks.append(_check(f"{cid}:interface", iface_title, False,
                             f"toc 里的 Interface 是 {toc.get('Interface', '?')}，客户端 {client_version} 是 {expected_interface}"
                             f"（游戏更新了）：重新安装就写入新的号（游戏关着时程序会自己装），游戏才不会把它当成过期插件",
                             FIX_INSTALL))
    # ## Version against the program's: the addons always carry it (the program brings its addons along)
    ver_title = f"插件 {name} 的版本"
    program = base_version(__version__)
    if toc is None:
        checks.append(_skipped(f"{cid}:version", ver_title, "未安装"))
    elif base_version(toc.get("Version")) == program:
        checks.append(_check(f"{cid}:version", ver_title, True, f"{toc.get('Version')}，与程序一致"))
    elif base_version(src_toc.get("Version")) == program:
        checks.append(_check(f"{cid}:version", ver_title, False,
                             f"游戏里的是 {toc.get('Version', '?')}，程序是 {program}：程序带来的插件更新还没装进游戏"
                             f"（游戏关着时程序会自己装）", FIX_INSTALL))
    else:
        checks.append(_check(f"{cid}:version", ver_title, False,
                             f"游戏里的是 {toc.get('Version', '?')}，程序自带的副本是 {src_toc.get('Version', '?')}，"
                             f"都不是程序的 {program}：需要更新程序"))
    return checks


def _mailbox_check(addons):
    wb = addons / "WoWBridge"
    if not wb.is_dir():
        return _check("mailbox", "字体信箱", False, "WoWBridge 未安装，所以没有信箱", FIX_INSTALL)
    missing = sum(1 for i in range(1, mailbox.POOL + 1) if not mailbox.slot_path(addons, i).exists())
    proc = mailbox.proc_path(addons).exists()
    if not missing and proc:
        return _check("mailbox", "字体信箱", True, f"{mailbox.POOL} 个信箱槽位和 proc.ttf 齐全（{mailbox.mail_dir(addons)}）")
    what = ([f"缺少 {missing} 个信箱槽位（共 {mailbox.POOL} 个）"] if missing else []) + ([] if proc else ["缺少 proc.ttf"])
    return _check("mailbox", "字体信箱", False, "，".join(what), FIX_CREATE_MAIL)


def _developer_components_check(addons):
    """an install of the platform addon alone (`wuxian install --mode player`): WoWBridge draws a frame on the screen and
    runs what the program sends, so it should not be there"""
    present = [p.name for p in inst.developer_components(addons)]
    if not present:
        return _check("developer_components", "没有开发组件（只装平台插件时）", True, "未安装")
    return _check("developer_components", "没有开发组件（只装平台插件时）", False,
                  f"已安装 {'、'.join(present)}：开发组件会在屏幕上画帧码，并执行程序发来的 Lua", FIX_INSTALL_CLEAN)


def _addons_txt_check(game, names):
    found = addons_txt_states(game, set(names))
    if not found:
        return _check("addons_txt", "插件未被禁用（AddOns.txt）", None,
                      "未知：WTF\\Account 下没有 AddOns.txt（每个角色登录后，客户端会为它写一份）")
    disabled = [f"{name} 在 {rel} 里被禁用" for rel, states in found for name, st in states.items() if st == "disabled"]
    if disabled:
        return _check("addons_txt", "插件未被禁用（AddOns.txt）", False, "；".join(disabled) + "（请在游戏的插件列表里启用）")
    return _check("addons_txt", "插件未被禁用（AddOns.txt）", True, f"已在 {len(found)} 个 AddOns.txt 里启用")


def _leftovers_check(addons):
    left = inst.leftovers(addons)
    foreign = inst.foreign_slots(addons)
    note = f"（{'、'.join(p.name for p in foreign)} 里有别的文件，保留不动）" if foreign else ""
    if not left:
        return _check("leftovers", "旧版本与实验残留", True, "无" + note)
    names = [p.relative_to(addons).as_posix() for p in left]
    shown = "、".join(names[:6]) + (f" 等 {len(names)} 个文件夹" if len(names) > 6 else "")
    return _check("leftovers", "旧版本与实验残留", False, shown + note, FIX_INSTALL_CLEAN)


def _runtime_dir_check():
    try:
        probe = state_dir() / ".write-test"
        probe.write_bytes(b"ok")
        probe.unlink()
        return _check("runtime_dir", "运行时目录可写", True, str(home()))
    except OSError as e:
        return _check("runtime_dir", "运行时目录可写", False, f"{home()}：{e}（请把 WUXIAN_HOME 设为可写的文件夹）")


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
        checks.append(_check("game_dir", "客户端目录", True, str(game)))
    else:
        checks.append(_check("game_dir", "客户端目录", False,
                             f"{game} 里没有 Interface\\AddOns" if game else "没有找到：请用 --game <客户端目录> 指定"))
        addons = game = None                                     # not a client folder: the checks below are skipped
    # client version
    info = read_build_info(game) if game else None
    client = client_info(game) if game else dict(version=None, interface=None, source=None)
    version, expected_interface = client["version"], client["interface"]
    if game is None:
        checks.append(_skipped("client_version", "客户端版本"))
    elif info is None:
        checks.append(_check("client_version", "客户端版本", None, "未知：客户端目录和它的上一级都没有 .build.info"))
    else:
        who = f"{version}（分支 {info['branch'] or '?'}，{info['product'] or info['flavor'] or '?'}）"
        how = {"game": "游戏报告的", "verified": "本版程序已验证", "version": "按版本号推算"}.get(client["source"])
        if expected_interface is not None:
            checks.append(_check("client_version", "客户端版本", True, f"{who}：Interface {expected_interface}（{how}）"))
        else:
            checks.append(_check("client_version", "客户端版本", None, f"{who}：读不出它的 Interface 号"))
    for name in names:
        if addons is None:
            checks += [_skipped(f"addon:{name}:{k}", t) for k, t in
                       (("installed", f"插件 {name} 已安装"), ("interface", f"插件 {name} 的 Interface 号"), ("version", f"插件 {name} 的版本"))]
        else:
            checks += _addon_checks(name, addons, source, expected_interface, version)
    if mode == "developer":
        checks.append(_mailbox_check(addons) if addons is not None else _skipped("mailbox", "字体信箱"))
    else:
        checks.append(_developer_components_check(addons) if addons is not None
                      else _skipped("developer_components", "没有开发组件（只装平台插件时）"))
    checks.append(_addons_txt_check(game, names) if game else _skipped("addons_txt", "插件未被禁用（AddOns.txt）"))
    checks += _runtime_checks()
    checks.append(_leftovers_check(addons) if addons is not None else _skipped("leftovers", "旧版本与实验残留"))
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
    lines = [f"{mark[c['ok']]} {c['title']}：{c['detail']}"
             + (f"（修复动作：{c['fix']}）" if c["ok"] is False and c["fix"] else "") for c in checks]
    failed = [c for c in checks if c["ok"] is False]
    unknown = sum(1 for c in checks if c["ok"] is None)
    fixes = sorted({c["fix"] for c in failed if c["fix"]})
    summary = f"共 {len(checks)} 项检查，{len(failed)} 项失败，{unknown} 项未知"
    if fixed:
        summary += f"；已执行：{'、'.join(fixed)}"
    if fixes:
        summary += f"；`wuxian doctor --fix` 会执行：{'、'.join(fixes)}"
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
            print(f"wuxian doctor: 修复失败：{e}", file=sys.stderr)
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
