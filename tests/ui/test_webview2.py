"""What the window needs from Windows, at startup (wuxianworkshop/ui/webview2.py): the WebView2 Runtime and the .NET
Framework, each offered for install when missing."""
import sys
import unittest
from unittest.mock import patch

from wuxianworkshop.installer import runtimes
from wuxianworkshop.ui import webview2


class Detection(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "reads the Windows registry")
    def test_this_machine_has_both(self):
        version = webview2.installed_version()
        self.assertIsNotNone(version, "no WebView2 Runtime in the registry")
        self.assertRegex(version, r"^\d+(\.\d+){1,3}$")
        self.assertEqual(webview2.missing(), [])

    def test_missing(self):
        with patch.object(runtimes, "webview2_version", return_value=None), \
                patch.object(runtimes, "dotnet_release", return_value=394802):          # 4.6.2: too old
            self.assertEqual(webview2.missing(), ["dotnet", "webview2"])
        with patch.object(runtimes, "webview2_version", return_value="154.0"), \
                patch.object(runtimes, "dotnet_release", return_value=None):
            self.assertEqual(webview2.missing(), ["dotnet"])

    def test_not_windows(self):
        with patch.object(runtimes.sys, "platform", "linux"):
            self.assertIsNone(webview2.installed_version())


class Ensure(unittest.TestCase):
    def test_yes_installs_and_waits(self):
        state = {"pv": None}
        with patch.object(runtimes, "webview2_version", lambda: state["pv"]), \
                patch.object(runtimes, "dotnet_release", return_value=533325), \
                patch.object(webview2, "_message_box", return_value=webview2.IDYES) as box, \
                patch.object(runtimes, "install", side_effect=lambda name, wait: state.update(pv="154.0")) as install:
            self.assertTrue(webview2.ensure(["webview2"]))
        install.assert_called_once_with("webview2", wait=True)
        text, flags = box.call_args_list[0][0]
        self.assertIn("WebView2", text)
        self.assertIn("数字签名", text)
        self.assertTrue(flags & webview2.MB_YESNO)

    def test_no_installs_nothing(self):
        with patch.object(webview2, "_message_box", return_value=7), patch.object(runtimes, "install") as install:
            self.assertFalse(webview2.ensure(["webview2"]))
        install.assert_not_called()

    def test_failed_install(self):
        answers = iter([webview2.IDYES, 1])
        with patch.object(webview2, "_message_box", side_effect=lambda *a: next(answers)) as box, \
                patch.object(runtimes, "install", side_effect=runtimes.InstallError("not Microsoft's")):
            self.assertFalse(webview2.ensure(["dotnet"]))
        self.assertIn("not Microsoft's", box.call_args_list[1][0][0])

    def test_still_missing_after_the_installer(self):
        """the .NET installer may want Windows restarted first"""
        with patch.object(runtimes, "webview2_version", return_value="154.0"), \
                patch.object(runtimes, "dotnet_release", return_value=None), \
                patch.object(webview2, "_message_box", return_value=webview2.IDYES) as box, \
                patch.object(runtimes, "install"):
            self.assertFalse(webview2.ensure(["dotnet"]))
        self.assertIn("重启电脑", box.call_args_list[-1][0][0])


if __name__ == "__main__":
    unittest.main()
