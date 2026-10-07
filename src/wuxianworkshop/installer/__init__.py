"""Putting the addon into the game's AddOns folder (install.py), checking and repairing an installation (doctor.py),
listing what the AddOns folder holds (addons.py) and, later, updating the companion itself.

The addon sources travel with the program: in a checkout they are the repository's addon/ folder; in a PyInstaller onedir
build (scripts/wuxian.spec) they are copied into _internal/addon, which sys._MEIPASS points at.
"""
import sys
from pathlib import Path

PLATFORM_ADDON = "!WuxianWorkshop"                     # everyone: sorts first, loads first, collects the Lua errors
DEVELOPER_ADDON = "WoWBridge"                          # developer mode only: the link to the program, hot loading, the mailbox
PRODUCT_ADDONS = (PLATFORM_ADDON, DEVELOPER_ADDON)     # what developer mode installs; player mode installs the first only
MODES = ("player", "developer")
LAB_ADDON = "WoWBridge_Lab"                            # the experiments of early development builds: removed when found
ADDON_FILE_TYPES = (".lua", ".toc", ".xml", ".tga")    # what an addon folder may contain (.tga: Media's textures); anything else is not copied

# client version -> the ## Interface number its addons declare, for the clients this release was verified with
# (1.60.1.70235: the cn beta the mailbox was measured on; 1.60.1.70245: the update of 2026-10-07, same Interface, link,
# run and checks verified in the game). Other clients are not refused: installer.addons.client_info() takes the number
# the game itself reported (WoWBridge's HELLO), else this table, else the one the version gives (1.60.1 -> 16001).
VERIFIED_CLIENTS = {"1.60.1.70235": 16001, "1.60.1.70245": 16001}


def addons_for(mode):
    """the addon folders a mode installs: player mode only the platform addon, developer mode WoWBridge too"""
    if mode not in MODES:
        raise ValueError(f"mode: one of {', '.join(MODES)}, not {mode!r}")
    return PRODUCT_ADDONS if mode == "developer" else (PLATFORM_ADDON,)


def addon_source_dir():
    """where the addon folders come from: <repo>/addon in a checkout, _internal/addon (sys._MEIPASS) when frozen"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "addon"
    return Path(__file__).resolve().parents[3] / "addon"
