"""WgcSource's client-area offset (transport/capture.py) against a stand-in for user32: no window, no GPU. The cases
are frame and client sizes measured on Windows 10 at 125 % (the game window and a test window)."""
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


if __name__ == "__main__":
    unittest.main()
