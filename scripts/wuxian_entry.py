"""The script PyInstaller freezes into wuxian.exe (scripts/wuxian.spec): the `wuxian` console script by another route.
cli/main.py uses relative imports, so it cannot be run as a script itself; this stub imports it as part of the package.

Velopack's hooks come first: its Setup exe, Update.exe and the uninstaller start wuxian.exe with --veloapp-* arguments,
and velopack.App().run() handles those and exits; on any other start it returns at once. A downloaded update is not
applied by a start (a `wuxian mcp` started by an agent must not restart into it): the program applies it when the user
says so (updater.Updater.apply_on_exit)."""
import sys
from pathlib import Path


def velopack_layout():
    """installed by the Setup exe or unpacked from the portable zip: <root>\\current\\wuxian.exe with Update.exe in
    <root>; a hook call (--veloapp-install ...) comes with that too. Elsewhere (dist\\wuxian) Velopack would only print
    that it is not installed"""
    exe = Path(sys.executable)
    return exe.parent.name.lower() == "current" and (exe.parent.parent / "Update.exe").is_file() \
        or any(a.startswith("--veloapp") for a in sys.argv[1:])


if getattr(sys, "frozen", False) and velopack_layout():
    try:
        import velopack
        velopack.App().set_auto_apply_on_startup(False).run()
    except Exception:                   # a build without the velopack module: start as before
        pass

from wuxianworkshop.cli.main import main  # noqa: E402  (after the hooks, which may end the process)

if __name__ == "__main__":
    sys.exit(main())
