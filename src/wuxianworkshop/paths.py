r"""Where the companion keeps its run-time files, outside the repository:

    %LOCALAPPDATA%\WuxianWorkshop\logs    debug.log, link-<time>.json, mcp.log
    %LOCALAPPDATA%\WuxianWorkshop\snaps   PNGs of the snap command
    %LOCALAPPDATA%\WuxianWorkshop\state   install.json, command.txt, say.txt, companion.stop, daemon.json, settings.json
    %LOCALAPPDATA%\WuxianWorkshop\history kept versions of addons (agent/history.py)

WUXIAN_HOME in the environment replaces the %LOCALAPPDATA%\WuxianWorkshop root (tests point it at a temporary folder).
Each function makes its folder when it is missing.
"""
import os
from pathlib import Path

APP_FOLDER = "WuxianWorkshop"


def home():
    """the root of the run-time files: $WUXIAN_HOME, else %LOCALAPPDATA%\\WuxianWorkshop"""
    override = os.environ.get("WUXIAN_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / APP_FOLDER


def _folder(name):
    path = home() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir():
    return _folder("logs")


def snaps_dir():
    return _folder("snaps")


def state_dir():
    return _folder("state")


def history_dir():
    """the kept versions of addons (agent/history.py)"""
    return _folder("history")


def daemon_file():
    """state/daemon.json: the running daemon's pid, port and token (written on start, removed on exit)"""
    return state_dir() / "daemon.json"


def settings_file():
    """state/settings.json: what the settings page keeps (mode, capture, game folder, ...)"""
    return state_dir() / "settings.json"


def ui_static_dir():
    """the web page the daemon serves at / (written by the ui package; may not exist yet)"""
    return Path(__file__).resolve().parent / "ui" / "static"
