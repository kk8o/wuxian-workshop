r"""Before the window opens: what it needs from Windows (installer/runtimes.py) is the WebView2 Runtime (without it
pywebview falls back to IE11 and the page stays blank) and the .NET Framework 4.7.2 or later (pywebview drives WebView2
through WinForms with pythonnet). For each one that is missing a system message box offers to install it now:
Microsoft's own installer is downloaded, its signature checked and run, and the window opens once it is done. A "no",
or an install that does not take, ends the program with a word on what is missing.
"""
import ctypes

from ..i18n import tr
from ..installer import runtimes

MB_OK, MB_YESNO, MB_ICONWARNING, MB_ICONINFORMATION = 0x0, 0x4, 0x30, 0x40
MB_SETFOREGROUND, MB_TOPMOST = 0x10000, 0x40000
IDYES = 6


def ask(title, name):
    extra = tr("（装完可能需要重启电脑）", " (the computer may need a restart afterwards)") if name == "dotnet" else ""
    return tr(f"无限工坊需要「{title}」来显示窗口，这台电脑上没有。\n\n"
              f"点「是」现在安装：从微软下载官方安装程序（约 2 MB），核对数字签名后运行，按它的提示完成{extra}。\n"
              f"点「否」退出。",
              f"Wuxian Workshop needs {title} to show its window, and this computer does not have it.\n\n"
              f"Yes installs it now: Microsoft's own installer is downloaded (about 2 MB), its digital signature checked and run; "
              f"follow it to the end{extra}.\nNo quits.")


def installed_version():
    """the WebView2 Runtime's version, or None"""
    return runtimes.webview2_version()


def missing():
    """the runtimes the window needs that are not there, in the order to install them"""
    out = []
    release = runtimes.dotnet_release()
    if release is None or release < runtimes.DOTNET_MIN_RELEASE:
        out.append("dotnet")
    if not installed_version():
        out.append("webview2")
    return out


def _message_box(text, flags):
    return ctypes.windll.user32.MessageBoxW(None, text, tr("无限工坊", "Wuxian Workshop"), flags | MB_SETFOREGROUND | MB_TOPMOST)


def ensure(names):
    """offer to install each missing runtime; True when none is missing any more (the window can open)"""
    for name in names:
        rt = runtimes.RUNTIMES[name]
        if _message_box(ask(rt.label(), name), MB_YESNO | MB_ICONWARNING) != IDYES:
            return False
        try:
            runtimes.install(name, wait=True)
        except runtimes.InstallError as e:
            _message_box(tr(f"「{rt.label()}」没有装上：{e}\n\n可以稍后在「自检与修复」里再试，或从微软官网手动安装。",
                            f"{rt.label()} did not install: {e}\n\nTry again later under Check & Repair, or install it by hand "
                            f"from Microsoft's website."), MB_OK | MB_ICONWARNING)
            return False
    left = missing()
    if left:
        titles = tr("、", ", ").join(runtimes.RUNTIMES[n].label() for n in left)
        _message_box(tr(f"安装程序已结束，但还检测不到「{titles}」。如果它要求重启电脑，请重启后再打开无限工坊。",
                        f"The installer has finished, but {titles} is still not there. If it asked for a restart, restart the "
                        f"computer and open Wuxian Workshop again."), MB_OK | MB_ICONINFORMATION)
        return False
    return True
