"""The tests read the program's Chinese texts: WUXIAN_LANG stands in for "auto" (i18n.py), so they do not follow the machine's
Windows display language. WUXIAN_HOME points the run-time files (paths.py) at a temporary folder for the whole run, so that a
test which does not make its own can never touch the user's: one did not, and every full run deleted the kept WuxianKit
manifest (state\\kit-manifest.json) of the machine it ran on."""
import atexit
import os
import shutil
import tempfile

os.environ.setdefault("WUXIAN_LANG", "zh-CN")
if "WUXIAN_HOME" not in os.environ:
    _home = tempfile.mkdtemp(prefix="wuxian-tests-")
    os.environ["WUXIAN_HOME"] = _home
    atexit.register(shutil.rmtree, _home, True)
