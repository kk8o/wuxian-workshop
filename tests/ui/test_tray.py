"""The tray interface (wuxianworkshop/ui/tray.py) and the icon files (icon.py), without showing an icon."""
import unittest

from wuxianworkshop.ui import icon, tray


class Icon(unittest.TestCase):
    def test_the_icon_files(self):
        img = icon.load()
        self.assertEqual((img.size, img.mode), ((256, 256), "RGBA"))
        self.assertEqual(img.getpixel((0, 0))[3], 0)                   # transparent corner (rounded tile)
        self.assertEqual(img.getpixel((128, 128))[:3] != (0x1b, 0x1c, 0x21), True)   # the mark in the middle
        self.assertEqual(icon.ico_sizes(), list(icon.ICO_SIZES), "static/wuxian.ico: run scripts/make_icons.py")
        for name in ("logo.svg", "logo-small.svg"):
            self.assertIn("<svg", (icon.ICO_PATH.parent / name).read_text(encoding="utf-8"))

    def test_small_icon_is_harmless_without_a_tray(self):
        class Fake:
            _icon_handle = None
        f = Fake()
        tray.small_icon(f)                                            # loads the 16 / 20 px image from the .ico
        if f._icon_handle:
            import ctypes
            ctypes.windll.user32.DestroyIcon(ctypes.c_void_p(f._icon_handle))


class Menu(unittest.TestCase):
    def test_items_become_pystray_items(self):
        import pystray
        calls = []
        t = tray.Tray("无限工坊", [tray.Item("打开", lambda: calls.append("open"), default=True),
                                   tray.Item("检查更新", None, enabled=False), tray.SEPARATOR,
                                   tray.Item("退出", lambda: calls.append("quit"))])
        items = list(t._menu(pystray))
        self.assertEqual(len(items), 4)
        self.assertEqual([str(i) for i in items if i is not pystray.Menu.SEPARATOR], ["打开", "检查更新", "退出"])
        self.assertIs(items[2], pystray.Menu.SEPARATOR)
        self.assertTrue(items[0].default)
        self.assertFalse(items[1].enabled)
        items[0](None)                                                # pystray calls the action with the icon
        items[3](None)
        self.assertEqual(calls, ["open", "quit"])

    def test_actions_never_raise_into_pystray(self):
        def boom():
            raise RuntimeError("x")
        with self.assertLogs(tray.log, level="ERROR"):
            tray.Tray._wrap(boom)()
        tray.Tray._wrap(None)()

    def test_stop_before_start_is_harmless(self):
        t = tray.Tray("x", [])
        t.stop()
        t.notify("nothing")


if __name__ == "__main__":
    unittest.main()
