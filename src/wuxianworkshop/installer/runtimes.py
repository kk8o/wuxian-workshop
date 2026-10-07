r"""What the program needs from Windows that the build does not carry, whether it is there, and a one-click install.

The build carries Python, the VC++ and universal C runtimes, numpy, Pillow, pywebview's WebView2 assemblies and
pythonnet. What it takes from the system:

    webview2  the Microsoft Edge WebView2 Runtime: the window's browser engine (without it pywebview falls back to IE11
              and the page stays blank). Windows 11 has it; not every Windows 10 got it through the updates.
    dotnet    the .NET Framework 4.7.2 or later: pywebview drives WebView2 through WinForms with pythonnet. Windows 10
              1903 and later ship 4.8.
    windows   Windows 10 1903 (build 18362) or later for Windows Graphics Capture; older systems read the screen with
              GDI (the game's top-left corner must stay uncovered). Nothing to install.

install() downloads Microsoft's own small installer from its fwlink, accepts it only when its Authenticode signature
is valid and Microsoft's, and starts it: the installer shows its own progress and asks for administrator rights itself
when it needs them (the .NET installer may want Windows restarted).
"""
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..paths import state_dir

WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
DOTNET_MIN_RELEASE = 461808            # .NET Framework 4.7.2 (the "Release" value under NDP\v4\Full)
WGC_MIN_BUILD = 18362                  # Windows 10 1903: GraphicsCaptureItem for a window
MAX_DOWNLOAD = 32 * 1024 * 1024        # the bootstrappers are 2 MB; anything this big is not one
DOTNET_RELEASES = ((533320, "4.8.1"), (528040, "4.8"), (461808, "4.7.2"), (461308, "4.7.1"), (460798, "4.7"),
                   (394802, "4.6.2"), (394254, "4.6.1"), (393295, "4.6"), (379893, "4.5.2"), (378675, "4.5.1"),
                   (378389, "4.5"))


@dataclass(frozen=True)
class Runtime:
    title: str
    url: str          # Microsoft's fwlink
    file: str         # what it downloads
    args: tuple = ()
    title_en: str = ""

    def label(self):
        """its name in the program's language"""
        from ..i18n import tr
        return tr(self.title, self.title_en or self.title)


RUNTIMES = {
    "webview2": Runtime("Microsoft Edge WebView2 运行时", "https://go.microsoft.com/fwlink/p/?LinkId=2124703",
                        "MicrosoftEdgeWebview2Setup.exe", ("/install",), "Microsoft Edge WebView2 Runtime"),
    "dotnet": Runtime(".NET Framework 4.8", "https://go.microsoft.com/fwlink/?LinkId=2085155", "ndp48-web.exe"),
}


class InstallError(RuntimeError):
    """the installer could not be downloaded, was not signed by Microsoft, or did not start"""


def webview2_version():
    """the WebView2 Runtime's version ("154.0.4258.53"), or None when it is not installed (or this is not Windows):
    the "pv" value per machine (64-bit and 32-bit views), then per user; 0.0.0.0 is what a half-removed install leaves"""
    if sys.platform != "win32":
        return None
    import winreg
    rel = "\\Microsoft\\EdgeUpdate\\Clients\\" + WEBVIEW2_CLIENT
    for hive, path in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node" + rel), (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE" + rel),
                       (winreg.HKEY_CURRENT_USER, "SOFTWARE" + rel)):
        try:
            with winreg.OpenKey(hive, path) as key:
                value = str(winreg.QueryValueEx(key, "pv")[0]).strip()
        except OSError:
            continue
        if value and value != "0.0.0.0":
            return value
    return None


def dotnet_release():
    """the .NET Framework 4.x "Release" number (533325 for 4.8.1 on Windows 10 22H2), or None when there is none"""
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as key:
            return int(winreg.QueryValueEx(key, "Release")[0])
    except (OSError, ValueError):
        return None


def dotnet_name(release):
    """4.x version for a Release number: the highest one it reaches ("4.8.1"), else "4.x" """
    return next((name for least, name in DOTNET_RELEASES if release >= least), "4.x")


def windows_build():
    return sys.getwindowsversion().build if sys.platform == "win32" else 0


def download(name, folder=None, opener=None):
    """Microsoft's installer for a runtime, into state\\downloads (or folder); its path"""
    rt = RUNTIMES[name]
    folder = Path(folder) if folder else state_dir() / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    path, part = folder / rt.file, folder / (rt.file + ".part")
    opener = opener or (lambda url: urllib.request.urlopen(url, timeout=60))
    try:
        with opener(rt.url) as resp, open(part, "wb") as out:
            size = 0
            while chunk := resp.read(65536):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise InstallError(f"{rt.file}: more than {MAX_DOWNLOAD // 1048576} MB, not the installer")
                out.write(chunk)
        os.replace(part, path)
    except OSError as e:
        raise InstallError(f"could not download {rt.file} from {rt.url}: {e}") from None
    finally:
        if part.exists():
            part.unlink()
    return path


def signature(path):
    """(status, signer subject) of a file's Authenticode signature, by PowerShell's Get-AuthenticodeSignature"""
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:WUXIAN_CHECK; "
              "[Console]::Out.Write(($s.Status, $s.SignerCertificate.Subject) -join '|')")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             text=True, timeout=60, env=dict(os.environ, WUXIAN_CHECK=str(path)),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return "Unknown", str(e)
    status, _, subject = out.partition("|")
    return status or "Unknown", subject


def verify(path):
    """refuse anything but a valid signature of Microsoft Corporation"""
    status, subject = signature(path)
    if status != "Valid" or "O=Microsoft Corporation" not in subject:
        raise InstallError(f"{Path(path).name}: the signature is {status} ({subject or 'no signer'}), not Microsoft's; "
                           f"not started")


def install(name, wait=False, folder=None, opener=None):
    """download, verify and start Microsoft's installer for a runtime: dict(name, path, pid, exit_code: None unless
    wait). InstallError when any step fails"""
    path = download(name, folder, opener)
    verify(path)
    try:
        proc = subprocess.Popen([str(path), *RUNTIMES[name].args], close_fds=True)
    except OSError as e:
        raise InstallError(f"{path.name} did not start: {e}") from None
    return dict(name=name, path=str(path), pid=proc.pid, exit_code=proc.wait() if wait else None)
