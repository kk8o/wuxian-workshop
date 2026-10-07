r"""Before the window opens: what it needs from Windows (installer/runtimes.py) is the WebView2 Runtime (without it
pywebview falls back to IE11 and the page stays blank) and the .NET Framework 4.7.2 or later (pywebview drives WebView2
through WinForms with pythonnet). For each one that is missing a system message box offers to install it now:
Microsoft's own installer is downloaded, its signature checked and run, and the window opens once it is done. A "no",
or an install that does not take, ends the program with a word on what is missing.
"""
import ctypes

from ..installer import runtimes

MB_OK, MB_YESNO, MB_ICONWARNING, MB_ICONINFORMATION = 0x0, 0x4, 0x30, 0x40
MB_SETFOREGROUND, MB_TOPMOST = 0x10000, 0x40000
IDYES = 6
TITLE = "无限工坊"
ASK = ("无限工坊需要「{title}」来显示窗口，这台电脑上没有。\n\n"
       "点「是」现在安装：从微软下载官方安装程序（约 2 MB），核对数字签名后运行，按它的提示完成{extra}。\n"
       "点「否」退出。")
EXTRA = {"dotnet": "（装完可能需要重启电脑）"}


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
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags | MB_SETFOREGROUND | MB_TOPMOST)


def ensure(names):
    """offer to install each missing runtime; True when none is missing any more (the window can open)"""
    for name in names:
        rt = runtimes.RUNTIMES[name]
        if _message_box(ASK.format(title=rt.title, extra=EXTRA.get(name, "")), MB_YESNO | MB_ICONWARNING) != IDYES:
            return False
        try:
            runtimes.install(name, wait=True)
        except runtimes.InstallError as e:
            _message_box(f"「{rt.title}」没有装上：{e}\n\n可以稍后在「自检与修复」里再试，或从微软官网手动安装。",
                         MB_OK | MB_ICONWARNING)
            return False
    left = missing()
    if left:
        titles = "、".join(runtimes.RUNTIMES[n].title for n in left)
        _message_box(f"安装程序已结束，但还检测不到「{titles}」。如果它要求重启电脑，请重启后再打开无限工坊。",
                     MB_OK | MB_ICONINFORMATION)
        return False
    return True
