"""The tray icon behind a thin interface, so that pystray (LGPL, half maintained) can be replaced by a ctypes
Shell_NotifyIconW implementation later without touching the shell.

Tray(title, items).start() runs the icon in its own thread (pystray's win32 backend allows that; pywebview owns the main
thread). Menu callbacks run on that thread, so they must only do thread-safe things: the shell's callbacks call
window.show() / hide() (pywebview marshals them onto the UI thread) or set a flag. stop() ends the thread.
"""
import ctypes
import logging
import sys
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Item:
    label: str
    action: object = None            # a callable taking no arguments; None = does nothing
    enabled: bool = True
    default: bool = False            # the action of a double-click on the icon


SEPARATOR = Item("-")


def small_icon(icon):
    """pystray loads its image at the large-icon size (32 px, 40 px at 125 %) and Windows shrinks that for the tray; the
    .ico has the small sizes drawn for it (logo-small.svg): load the one the tray shows instead (the handle is pystray's
    from then on, it destroys it when the icon goes)"""
    if sys.platform != "win32":
        return
    from . import icon as iconmod
    if not iconmod.ICO_PATH.is_file():
        return
    try:
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        cx, cy = user32.GetSystemMetrics(49), user32.GetSystemMetrics(50)        # SM_CXSMICON, SM_CYSMICON
        handle = user32.LoadImageW(None, str(iconmod.ICO_PATH), 1, cx, cy, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if handle:
            icon._icon_handle = handle
    except Exception as e:                                    # pystray's own conversion stays
        log.debug("small tray icon not loaded: %s", e)


class Tray:
    """the icon, its menu and its thread; menu callbacks are called with no arguments"""

    def __init__(self, title, items, image=None):
        self.title = title
        self.items = list(items)
        self.image = image
        self._icon = None
        self._thread = None

    def start(self):
        import pystray
        if self.image is None:
            from . import icon as iconmod
            self.image = iconmod.load()
        self._icon = pystray.Icon("wuxian", self.image, self.title, pystray.Menu(*self._menu(pystray)))
        small_icon(self._icon)
        self._thread = threading.Thread(target=self._icon.run, name="tray", daemon=True)
        self._thread.start()

    def _menu(self, pystray):
        for item in self.items:
            if item is SEPARATOR or item.label == "-":
                yield pystray.Menu.SEPARATOR
            else:
                yield pystray.MenuItem(item.label, self._wrap(item.action), enabled=item.enabled, default=item.default)

    @staticmethod
    def _wrap(action):
        def call():
            if action is None:
                return
            try:
                action()
            except Exception:                                 # pystray would swallow it silently
                log.exception("tray menu action failed")
        return call

    def stop(self):
        """removes the icon and ends its thread"""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                log.exception("tray icon did not stop cleanly")
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self._icon = self._thread = None

    def notify(self, message, title=None):
        """a balloon / toast from the icon, when the platform has them"""
        if self._icon is not None and getattr(self._icon, "HAS_NOTIFICATION", False):
            try:
                self._icon.notify(message, title or self.title)
            except Exception:
                log.exception("tray notification failed")
