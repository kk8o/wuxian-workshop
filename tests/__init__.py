"""The tests read the program's Chinese texts: WUXIAN_LANG stands in for "auto" (i18n.py), so they do not follow the machine's
Windows display language."""
import os

os.environ.setdefault("WUXIAN_LANG", "zh-CN")
