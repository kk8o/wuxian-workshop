"""WgcSource's client-area offset (transport/capture.py) against a stand-in for user32: no window, no GPU. The cases
are frame and client sizes measured on Windows 10 at 125 % (the game window and a test window). Then the window's
rectangles in physical pixels whatever the thread's DPI awareness: a stand-in, and a real window."""
import ctypes
import ctypes.wintypes as wt
import unittest
from unittest import mock

from wuxianworkshop.transport import capture


class FakeUser32:
    """IsIconic and GetClientRect of one window whose state the test sets"""

    def __init__(self, client=(1287, 983), iconic=False):
        self.client, self.iconic = client, iconic

    def IsIconic(self, hwnd):
        return self.iconic

    def GetClientRect(self, hwnd, prc):
        prc._obj.left = prc._obj.top = 0
        prc._obj.right, prc._obj.bottom = self.client
        return 1


class GameWindow(unittest.TestCase):
    def test_a_known_exe_or_the_class_in_a_client_folder(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            client, other = Path(tmp) / "_cn_", Path(tmp) / "Tools"
            (client / "Interface").mkdir(parents=True)
            other.mkdir()
            self.assertTrue(capture.is_game(r"C:\Games\Wow.exe", "Whatever"))
            self.assertTrue(capture.is_game(str(client / "WowCN.exe"), "waApplication Window"))   # renamed in an update
            self.assertTrue(capture.is_game(str(client / "WowCN.exe"), "GxWindowClass"))
            self.assertFalse(capture.is_game(str(other / "WowCN.exe"), "waApplication Window"))   # not a client folder
            self.assertFalse(capture.is_game(str(client / "Launcher.exe"), "Chrome_WidgetWin_1"))
            self.assertFalse(capture.is_game("", "waApplication Window"))


class Offset(unittest.TestCase):
    def offset(self, frame, client, iconic=False):
        src = capture.WgcSource.__new__(capture.WgcSource)           # no capture session: only the geometry
        src.hwnd = 1
        with mock.patch.object(capture, "user32", FakeUser32(client, iconic)):
            return src._offset(frame)

    def test_measured_frames(self):
        # the game window, restored: the frame is the window rectangle with its invisible resize borders
        self.assertEqual(self.offset((1305, 1030), (1287, 983)), (9, 38))
        # a window maximized before the capture started: the frame is the visible frame (no borders, a lower title bar)
        self.assertEqual(self.offset((1920, 1030), (1920, 1001)), (0, 29))
        # a capture started while the window was restored, after a maximize: the restored layout stays; this size is
        # neither GetWindowRect (1938 x 1048) nor the DWM frame bounds (1920 x 1030), and matching against those lost the
        # link after the player maximized the game
        self.assertEqual(self.offset((1922, 1040), (1920, 1001)), (1, 38))
        # a test window, restored: the visible frame with its 1-pixel border
        self.assertEqual(self.offset((1202, 939), (1200, 900)), (1, 38))
        # borderless full screen
        self.assertEqual(self.offset((1920, 1080), (1920, 1080)), (0, 0))

    def test_no_offset(self):
        self.assertIsNone(self.offset((1305, 1030), (1287, 983), iconic=True))     # minimized: nothing is drawn
        self.assertIsNone(self.offset((199, 34), (0, 0)))                           # the minimized window's own frames
        self.assertIsNone(self.offset((1200, 900), (1920, 1001)))                   # a frame from before a resize


class SecondMonitor(FakeUser32):
    """the game full screen on a second monitor at 100 %, right of a primary one at 125 %, seen from a system DPI aware
    thread (the App's): Windows gives its rectangles in the primary monitor's scaling unless the thread is per-monitor
    aware"""

    def __init__(self):
        super().__init__((1920, 1080))
        self.context = "system"

    def set_context(self, context):
        old, self.context = self.context, context
        return old

    def scaled(self, *values):
        return [n if self.context is capture.PER_MONITOR_V2 else n * 5 // 4 for n in values]

    def GetClientRect(self, hwnd, prc):
        prc._obj.left = prc._obj.top = 0
        prc._obj.right, prc._obj.bottom = self.scaled(*self.client)
        return 1

    def ClientToScreen(self, hwnd, ppt):
        ppt._obj.x, ppt._obj.y = self.scaled(1920, 0)
        return 1

    def GetWindowTextLengthW(self, hwnd):
        return 0

    def GetWindowTextW(self, hwnd, buf, n):
        return 0

    def GetDpiForWindow(self, hwnd):
        return 96


class Physical(unittest.TestCase):
    def test_a_window_on_a_monitor_of_another_scaling(self):
        fake = SecondMonitor()
        src = capture.WgcSource.__new__(capture.WgcSource)
        src.hwnd = 1
        with mock.patch.object(capture, "user32", fake), mock.patch.object(capture, "_set_thread_dpi", fake.set_context):
            win = capture._window(1, 2, "Wow.exe")
            self.assertEqual((win.x, win.y, win.w, win.h), (1920, 0, 1920, 1080))
            self.assertEqual(capture.client_size(1), (1920, 1080))
            # scaled, the client area was bigger than the frame: no offset, nothing read, the link never came up
            self.assertEqual(src._offset((1920, 1080)), (0, 0))
        self.assertEqual(fake.context, "system")                    # the thread's own awareness is back

    @staticmethod
    def on_thread(context, fn, *args):
        """fn(*args) with this thread at that DPI awareness"""
        old = capture._set_thread_dpi(context)
        try:
            return fn(*args)
        finally:
            capture._set_thread_dpi(old)

    def test_a_real_window_from_a_thread_that_is_not_aware(self):
        # a per-monitor aware window, as the game's, measured from a DPI unaware thread: on a monitor that is not at
        # 100 % Windows scales its rectangles for such a thread (at 100 % they agree anyway)
        create = ctypes.WINFUNCTYPE(wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)(
            ("CreateWindowExW", ctypes.windll.user32))
        hwnd = self.on_thread(capture.PER_MONITOR_V2, create, 0, "STATIC", "wuxian test", 0x00CF0000, 200, 150, 1000,
                              700, None, None, None, None)              # never shown
        self.assertTrue(hwnd)
        self.addCleanup(ctypes.windll.user32.DestroyWindow, hwnd)
        if ctypes.windll.user32.GetDpiForWindow(hwnd) == 96:            # GitHub's runners: nothing would be scaled
            self.skipTest("the monitor is at 100 %: an unaware thread measures the same anyway (the stand-in test "
                          "test_a_window_on_a_monitor_of_another_scaling covers it)")

        def measure():
            win = capture._window(hwnd, 0, "")
            return win.x, win.y, win.w, win.h, *capture.client_size(hwnd)

        want = self.on_thread(capture.PER_MONITOR_V2, measure)
        self.assertEqual(want[2:4], want[4:])
        self.assertEqual(self.on_thread(ctypes.c_void_p(-1), measure), want)     # DPI_AWARENESS_CONTEXT_UNAWARE


if __name__ == "__main__":
    unittest.main()
