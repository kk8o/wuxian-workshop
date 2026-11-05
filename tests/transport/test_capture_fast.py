"""The fixed costs of a read (transport/capture.py): GdiReader keeps its DC and bitmap while the size stays and releases
them, BGRA becomes RGB as a view of one contiguous copy, and WgcSource.snapshot() reuses its staging texture. No screen
pixel is read: BitBlt is a stand-in everywhere, and WGC runs against stand-in COM objects."""
import ctypes
import threading
import unittest
from unittest import mock

import numpy as np

from wuxianworkshop.transport import capture

from .test_capture import FakeUser32


def bgra_of(w, h, x=0, y=0):
    """a recognisable BGRA picture of w x h whose pixel (i, j) of the screen is (b, g, r) = (i + x, j + y, 7) mod 256"""
    out = np.empty((h, w, 4), np.uint8)
    out[..., 0] = (np.arange(w)[None, :] + x) % 256
    out[..., 1] = (np.arange(h)[:, None] + y) % 256
    out[..., 2] = 7
    out[..., 3] = 255
    return out


class FakeGdi:
    """gdi32 and the screen's DC: counts the objects alive; BitBlt remembers the rectangle, GetDIBits writes the
    picture bgra_of() of it"""

    def __init__(self):
        self.next, self.alive, self.made, self.rect, self.fail = 100, set(), 0, None, False
        self.selected = {}
        self.dcs_out = 0

    def _new(self):
        self.next += 1
        self.alive.add(self.next)
        return self.next

    def GetDC(self, hwnd):
        self.dcs_out += 1
        return 1

    def ReleaseDC(self, hwnd, dc):
        self.dcs_out -= 1
        return 1

    def CreateCompatibleDC(self, dc):
        self.made += 1
        return self._new()

    def CreateCompatibleBitmap(self, dc, w, h):
        return self._new()

    def SelectObject(self, dc, obj):
        old, self.selected[dc] = self.selected.get(dc, 9), obj
        return old

    def DeleteObject(self, obj):
        assert all(sel != obj for sel in self.selected.values()), "a bitmap deleted while selected"
        self.alive.remove(obj)
        return 1

    def DeleteDC(self, dc):
        self.alive.remove(dc)
        self.selected.pop(dc, None)
        return 1

    def BitBlt(self, mdc, x0, y0, w, h, sdc, x, y, rop):
        self.rect = (x, y, w, h)
        return 0 if self.fail else 1

    def GetDIBits(self, mdc, bmp, start, lines, buf, bmi, usage):
        x, y, w, h = self.rect
        pic = bgra_of(w, h, x, y)
        ctypes.memmove(buf, pic.ctypes.data, pic.nbytes)
        return lines


class Gdi(unittest.TestCase):
    def setUp(self):
        self.gdi = FakeGdi()
        for name in ("gdi32", "user32"):
            p = mock.patch.object(capture, name, self.gdi)
            p.start()
            self.addCleanup(p.stop)

    def test_rgb_in_the_right_order(self):
        for img in (capture.grab(10, 20, 30, 8), capture.GdiReader().grab(10, 20, 30, 8)):
            self.assertEqual(img.shape, (8, 30, 3))
            self.assertTrue(np.array_equal(img, bgra_of(30, 8, 10, 20)[:, :, 2::-1]))
            self.assertTrue(img.base is not None and img.base.flags.c_contiguous)    # a view of one contiguous copy
        self.assertEqual(len(self.gdi.alive), 2)          # the reader's two (not closed here); grab() left none

    def test_kept_while_the_size_stays(self):
        reader = capture.GdiReader()
        for _ in range(5):
            reader.grab(0, 0, 600, 300)
        self.assertEqual((reader.made, self.gdi.made, len(self.gdi.alive)), (1, 1, 2))
        a = reader.grab(0, 0, 600, 300)
        b = reader.grab(0, 0, 600, 300)
        self.assertFalse(np.shares_memory(a, b))            # every read is its own array: a caller may keep one
        self.assertEqual(self.gdi.dcs_out, 0)              # the screen's DC is given back every time

    def test_a_new_size_releases_the_old_objects(self):
        reader = capture.GdiReader()
        reader.grab(0, 0, 600, 300)
        old = set(self.gdi.alive)
        reader.grab(0, 0, 500, 300)
        self.assertEqual(reader.made, 2)
        self.assertEqual(len(self.gdi.alive), 2)
        self.assertFalse(old & self.gdi.alive)

    def test_close_releases_them(self):
        reader = capture.GdiReader()
        reader.grab(0, 0, 600, 300)
        reader.close()
        self.assertEqual(self.gdi.alive, set())
        reader.close()                                      # twice is fine
        self.assertTrue(np.array_equal(reader.grab(0, 0, 4, 4), bgra_of(4, 4)[:, :, 2::-1]))   # made again
        self.assertEqual(reader.made, 2)

    def test_a_failed_read_releases_them(self):
        reader = capture.GdiReader()
        reader.grab(0, 0, 600, 300)
        self.gdi.fail = True                                # a UAC prompt or the lock screen
        with self.assertRaises(OSError):
            reader.grab(0, 0, 600, 300)
        self.assertEqual(self.gdi.alive, set())
        self.assertEqual(self.gdi.dcs_out, 0)
        self.gdi.fail = False
        reader.grab(0, 0, 600, 300)
        self.assertEqual(reader.made, 2)

    def test_another_thread_does_not_use_them(self):
        reader = capture.GdiReader()
        reader.grab(0, 0, 600, 300)
        mine = set(self.gdi.alive)
        got = []
        t = threading.Thread(target=lambda: got.append(reader.grab(0, 0, 600, 300)))
        t.start()
        t.join()
        self.assertEqual(got[0].shape, (300, 600, 3))
        self.assertEqual(reader.made, 1)                    # through grab(): made and released in that read
        self.assertEqual(self.gdi.alive, mine)

    def test_grab_client_through_a_reader(self):
        win = capture.Window(1, 2, "Wow.exe", "", 100, 50, 800, 400, 96, False)
        reader = capture.GdiReader()
        img = capture.grab_client(win, 600, 300, 300, 200, reader=reader)
        self.assertEqual(img.shape, (200, 500, 3))          # cut at the client area's edge
        self.assertEqual(self.gdi.rect, (400, 250, 500, 200))
        self.assertEqual(reader.made, 1)
        self.assertEqual(reader.grab_client(win, 600, 300, 300, 200).shape, (200, 500, 3))
        self.assertEqual(reader.made, 1)


class RealGdiObjects(unittest.TestCase):
    """the real gdi32 with BitBlt stubbed (nothing is read off the screen): this process's GDI object count"""

    def test_no_object_is_left(self):
        user32 = ctypes.WinDLL("user32")
        user32.GetGuiResources.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        count = lambda: user32.GetGuiResources(ctypes.c_void_p(-1), 0)      # GR_GDIOBJECTS of this process
        real = capture.gdi32

        class NoBlit:
            def __getattr__(self, name):
                return getattr(real, name)

            def BitBlt(self, *a):
                return 1
        with mock.patch.object(capture, "gdi32", NoBlit()):
            capture.grab(0, 0, 64, 32)                       # the process's first GDI use makes objects of its own
            before = count()
            for _ in range(20):
                capture.grab(0, 0, 64, 32)
            self.assertEqual(count(), before)
            reader = capture.GdiReader()
            for i in range(200):
                reader.grab(0, 0, 64 + i // 50, 32)          # four sizes
            self.assertEqual(reader.made, 4)
            self.assertEqual(count(), before + 2)            # one DC and one bitmap kept
            reader.close()
            self.assertEqual(count(), before)


class Mapped(unittest.TestCase):
    def test_rows_wider_than_the_picture(self):
        w, h, pitch = 30, 6, 30 * 4 + 64                     # a staging texture's rows are padded
        raw = np.zeros((h, pitch), np.uint8)
        raw[:, :w * 4] = bgra_of(w, h).reshape(h, w * 4)
        m = capture._Mapped(raw.ctypes.data, pitch, 0)
        img = capture._mapped_rgb(m, w, h)
        raw[:] = 0                                           # unmapped: the memory is gone
        self.assertTrue(np.array_equal(img, bgra_of(w, h)[:, :, 2::-1]))


class Com:
    """a stand-in COM object: handlers by ("call" | "out", vtable index); every one made goes into made"""

    def __init__(self, made, name, handlers=None, query=None):
        self.name, self.handlers, self.released = name, dict(handlers or {}), False
        if query is not None:
            self.handlers["query"] = query
        self.ptr = len(made) + 1000
        made.append(self)
        self.made = made

    def call(self, index, argtypes, *args, restype=ctypes.c_long):
        f = self.handlers.get(("call", index))
        return f(*args) if f else 0

    def out(self, index, argtypes, *args):
        return self.handlers[("out", index)](*args)

    def query(self, iid):
        f = self.handlers.get("query")
        return f(iid) if f else Com(self.made, "closable")

    def release(self):
        self.released = True


class Snapshot(unittest.TestCase):
    """snapshot() against a stand-in device: one staging texture while the client size stays"""

    def setUp(self):
        self.made, self.frames, self.textures = [], [], []
        self.user32 = FakeUser32(client=(200, 100))
        p = mock.patch.object(capture, "user32", self.user32)
        p.start()
        self.addCleanup(p.stop)
        src = capture.WgcSource.__new__(capture.WgcSource)
        src.hwnd, src.objs, src.session = 1, [], None
        src.snap_tex = src.snap_size = None
        src.pool_size = (202, 139)
        self.frame_size = (202, 139)                         # the client area with a 1 px border and a 38 px title bar
        self.copies = []

        def next_frame():
            if not self.frames:
                return None
            self.frames.pop()
            size = self.frame_size

            def content_size(p):
                p._obj.w, p._obj.h = size
            tex = Com(self.made, "frame texture")
            access = Com(self.made, "access", {("out", 3): lambda *a: tex})
            surface = Com(self.made, "surface", query=lambda iid: access)
            return Com(self.made, "frame", {("call", 8): content_size, ("out", 6): lambda: surface})
        src.pool = Com(self.made, "pool", {("out", 7): next_frame})

        def texture(pdesc, init):
            d = pdesc._obj
            t = Com(self.made, "staging")
            t.size = (d.w, d.h)
            self.textures.append(t)
            return t
        src.dev = Com(self.made, "device", {("out", 5): texture})
        self.memory = {}

        def copy(dst, i, x, y, z, srcp, j, pbox):
            b = pbox._obj
            self.copies.append((dst, b.left, b.top, b.right, b.bottom))

        def map_(res, sub, kind, flags, pm):
            w, h = next(t.size for t in self.textures if t.ptr == res)
            pitch = w * 4 + 32
            raw = np.zeros((h, pitch), np.uint8)
            raw[:, :w * 4] = bgra_of(w, h).reshape(h, w * 4)
            self.memory[res] = raw                           # alive until unmapped
            pm._obj.data, pm._obj.pitch = raw.ctypes.data, pitch

        def unmap(res, sub):
            self.memory.pop(res)
        src.ctx = Com(self.made, "context", {("call", 46): copy, ("call", 14): map_, ("call", 15): unmap})
        self.src = src

    def snap(self):
        self.frames.append(1)
        return self.src.snapshot(timeout=0.1)

    def test_one_staging_texture_while_the_size_stays(self):
        for _ in range(3):
            img = self.snap()
            self.assertTrue(np.array_equal(img, bgra_of(200, 100)[:, :, 2::-1]))
        self.assertEqual(len(self.textures), 1)
        self.assertFalse(self.textures[0].released)
        self.assertEqual({c[0] for c in self.copies}, {self.textures[0].ptr})
        self.assertEqual(self.copies[0][1:], (1, 38, 201, 138))             # the client area out of the frame
        self.assertEqual(self.memory, {})                                    # unmapped every time
        others = [o for o in self.made if o.name in ("frame texture", "access", "surface", "frame")]
        self.assertTrue(all(o.released for o in others))                    # the frame's objects go back

    def test_a_new_size_makes_a_new_one(self):
        self.snap()
        self.user32.client, self.frame_size = (180, 90), (182, 129)        # the window was made smaller
        img = self.snap()
        self.assertEqual(img.shape, (90, 180, 3))
        self.assertEqual([t.size for t in self.textures], [(200, 100), (180, 90)])
        self.assertTrue(self.textures[0].released)
        self.assertNotIn(self.textures[0], self.src.objs)
        self.assertIn(self.textures[1], self.src.objs)

    def test_stop_releases_it(self):
        self.snap()
        self.src.stop()
        self.assertTrue(self.textures[0].released)
        self.assertIsNone(self.src.snap_tex)
        self.assertEqual(self.src.objs, [])


if __name__ == "__main__":
    unittest.main()
