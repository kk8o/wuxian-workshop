r"""The language of what the program tells people: the self-check, the agents, a new addon, the window's menu and dialogs.
It is the setting of the 设置 page (settings.json "language": "auto", "zh-CN" or "en"); "auto" follows Windows' display
language (Chinese: zh-CN, any other: en), and WUXIAN_LANG in the environment stands in for that (the tests run in Chinese).
The daemon sets it when the settings change (set_language); a process that has not been told reads settings.json once.
The page itself translates with ui/static/i18n.js.

    tr("已安装", "installed")   the text for the language in force
    language()                 "zh-CN" or "en"
"""
import ctypes
import json
import locale
import os
import sys

LANGS = ("auto", "zh-CN", "en")
_setting = None                                    # None: not told yet, settings.json is read once


def system_language():
    """WUXIAN_LANG, else Windows' display language: Chinese -> zh-CN, anything else -> en"""
    env = os.environ.get("WUXIAN_LANG")
    if env:
        return "en" if env.lower().startswith("en") else "zh-CN"
    if sys.platform == "win32":
        try:
            return "zh-CN" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x04 else "en"   # LANG_CHINESE
        except (AttributeError, OSError):
            pass
    return "zh-CN" if (locale.getlocale()[0] or "").lower().startswith(("zh", "chinese")) else "en"


def set_language(setting):
    """the setting: "auto", "zh-CN" or "en" (anything else counts as auto)"""
    global _setting
    _setting = setting if setting in LANGS else "auto"


def language():
    global _setting
    if _setting is None:
        try:
            from .paths import settings_file
            stored = json.loads(settings_file().read_text(encoding="utf-8")).get("language")
        except (OSError, ValueError, AttributeError):
            stored = None
        set_language(stored)
    return system_language() if _setting == "auto" else _setting


def tr(zh, en):
    """zh or en, whichever the language in force is"""
    return en if language() == "en" else zh
