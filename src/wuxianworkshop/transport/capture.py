"""Find the game window and read its client area: GDI from the screen, or Windows Graphics Capture of the window itself
(WgcSource, optional). Neither needs extra packages."""
import ctypes
import ctypes.wintypes as wt
import os
import time
from dataclasses import dataclass

import numpy as np

user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))     # per-monitor DPI aware v2: every coordinate below is in physical pixels

GAME_EXES = ("wow.exe", "wowt.exe", "wowb.exe", "wowclassic.exe")
GAME_CLASSES = ("waApplication Window", "GxWindowClass")    # the client's main window (12.x; older clients)
CLIENT_MARKS = (".flavor.info", "Interface", "WTF")          # what sits next to the exe in a client folder
SRCCOPY = 0x00CC0020        # without CAPTUREBLT: layered windows and the cursor are not copied


@dataclass
class Window:
    hwnd: int
    pid: int
    exe: str
    title: str
    x: int            # client area, screen coordinates (physical pixels)
    y: int
    w: int
    h: int
    dpi: int
    minimized: bool


def _exe(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        return buf.value if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(h)


def is_game(exe, cls, exes=GAME_EXES):
    """a game window: a known exe, or (an exe renamed in a game update) the client's window class with the exe in a client
    folder"""
    if os.path.basename(exe or "").lower() in exes:
        return True
    if cls in GAME_CLASSES and exe:
        folder = os.path.dirname(exe)
        return any(os.path.exists(os.path.join(folder, m)) for m in CLIENT_MARKS)
    return False


def _class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_window(exes=GAME_EXES):
    """the visible top-level window of the game, or None"""
    found = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):     # GW_OWNER: skip owned popups
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = _exe(pid.value)
            if is_game(exe, _class(hwnd), exes):
                found.append((hwnd, pid.value, exe))
        return True

    user32.EnumWindows(proc(cb), 0)
    if not found:
        return None
    hwnd, pid, exe = found[0]
    n = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, title, n + 1)
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return Window(hwnd, pid, exe, title.value, pt.x, pt.y, r.right, r.bottom, user32.GetDpiForWindow(hwnd), bool(user32.IsIconic(hwnd)))


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def grab(x, y, w, h):
    """RGB uint8 array (h, w, 3) of the screen rectangle, as composed by DWM"""
    sdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(sdc)
    bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not gdi32.BitBlt(mdc, 0, 0, w, h, sdc, x, y, SRCCOPY):
            raise OSError("BitBlt failed")
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = (ctypes.c_ubyte * (w * h * 4))()
        if gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0) != h:
            raise OSError("GetDIBits failed")
        return np.frombuffer(buf, np.uint8).reshape(h, w, 4)[:, :, 2::-1].copy()
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, sdc)


def grab_client(win, max_w=None, max_h=None, x=0, y=0):
    """a part of the window's client area: max_w x max_h from (x, y) of it (all of it from there without them)"""
    w = win.w - x if max_w is None else min(win.w - x, max_w)
    h = win.h - y if max_h is None else min(win.h - y, max_h)
    return grab(win.x + x, win.y + y, w, h)


class ComError(OSError):
    def __init__(self, what, hr):
        super().__init__(f"{what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
        self.hr = hr & 0xFFFFFFFF


class _Com:
    """an owned COM interface pointer; call() goes through the vtable by index"""
    def __init__(self, ptr):
        self.ptr = ptr if isinstance(ptr, ctypes.c_void_p) else ctypes.c_void_p(ptr)

    def call(self, index, argtypes, *args, restype=ctypes.c_long):
        fn = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0][index]
        r = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(fn)(self.ptr, *args)
        if restype is ctypes.c_long and r < 0:
            raise ComError(f"COM call {index}", r)
        return r

    def out(self, index, argtypes, *args):
        """a method whose last argument returns an interface"""
        p = ctypes.c_void_p()
        self.call(index, (*argtypes, ctypes.POINTER(ctypes.c_void_p)), *args, ctypes.byref(p))
        return _Com(p) if p else None

    def query(self, iid):
        return self.out(0, (ctypes.POINTER(_Guid),), ctypes.byref(iid))

    def release(self):
        if self.ptr:
            self.call(2, (), restype=ctypes.c_ulong)
            self.ptr = ctypes.c_void_p()


class _Guid(ctypes.Structure):
    _fields_ = [("b", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text):
        import uuid
        return cls((ctypes.c_ubyte * 16)(*uuid.UUID(text).bytes_le))


class _Size(ctypes.Structure):
    _fields_ = [("w", ctypes.c_int32), ("h", ctypes.c_int32)]


class _Box(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint) for n in ("left", "top", "front", "right", "bottom", "back")]


class _Mapped(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("pitch", ctypes.c_uint), ("depth_pitch", ctypes.c_uint)]


class _TexDesc(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint) for n in ("w", "h", "mips", "array", "format", "samples", "quality", "usage", "bind",
                                             "cpu", "misc")]


# interface ids and vtable slots from the Windows SDK headers (windows.graphics.capture.h, d3d11.h, ...interop.h)
IID_DXGI_DEVICE = _Guid.of("54ec77fa-1377-44e6-8c32-88fd5f44c84c")
IID_D3D_DEVICE = _Guid.of("a37624ab-8d5f-4650-9d3e-9eae3d9bc670")          # Windows.Graphics.DirectX.Direct3D11
IID_ITEM_INTEROP = _Guid.of("3628e81b-3cac-4c60-b7f4-23ce0e0c3356")
IID_ITEM = _Guid.of("79c3f95b-31f7-4ec2-a464-632ef5d30760")
IID_POOL_STATICS2 = _Guid.of("589b103f-6bbc-5df5-a991-02e28b3b66d5")
IID_SESSION2 = _Guid.of("2c39ae40-7d2e-5044-804e-8b6799d4cf9e")
IID_CLOSABLE = _Guid.of("30d5a829-7fa4-4026-83bb-d75bae4ea99e")
IID_DXGI_ACCESS = _Guid.of("a9b3d012-3df2-4ee3-b8d1-8695f457d3c1")
IID_TEXTURE2D = _Guid.of("6f15aaf2-d208-4e89-9ab4-489535d34f9c")
BGRA = 87                       # DXGI_FORMAT_B8G8R8A8_UNORM = DirectXPixelFormat.B8G8R8A8UIntNormalized
STILL_DRAWING = 0x887A000A      # DXGI_ERROR_WAS_STILL_DRAWING: Map with DO_NOT_WAIT before the GPU is done
MAP_ARGS = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_Mapped))
COPY_ARGS = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint,
             ctypes.POINTER(_Box))


def _factory(name, iid):
    combase = ctypes.windll.combase
    hs = ctypes.c_void_p()
    if combase.WindowsCreateString(ctypes.c_wchar_p(name), len(name), ctypes.byref(hs)) < 0:
        raise OSError("WindowsCreateString failed")
    try:
        f = ctypes.c_void_p()
        hr = combase.RoGetActivationFactory(hs, ctypes.byref(iid), ctypes.byref(f))
        if hr < 0:
            raise OSError(f"no activation factory for {name}: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
        return _Com(f)
    finally:
        combase.WindowsDeleteString(hs)


def _close(obj):
    """IClosable.Close (a frame goes back to the pool), then release"""
    if obj is None:
        return
    try:
        c = obj.query(IID_CLOSABLE)
        c.call(6, ())
        c.release()
    except OSError:
        pass
    obj.release()


class WgcSource:
    """Windows Graphics Capture of one window, read on demand. It reads the window's own content, so a covered window
    still works (a minimized one does not), and Windows 10 draws a yellow border around the window while it is captured.
    The system keeps the window's latest frame in a one-buffer pool. read() cuts the top-left part of the client area
    out of it on the GPU (never the whole frame) into one of RING staging textures, and returns the newest cut-out the
    GPU has finished, as RGB, with a sequence number that grows with every one. It does not wait for the GPU: with a
    busy game the copies can take 40-250 ms, and the caller's loop keeps going meanwhile. Plain ctypes over the WinRT
    ABI, no extra packages. Use it from one thread."""

    RING = 4
    SPIN = 0.004                # read() waits this long at most for the copy it just started (a fast GPU: no lag)

    def __init__(self, hwnd, max_w, max_h):
        self.hwnd, self.max_w, self.max_h = hwnd, max_w, max_h
        self.seq, self.latest, self.objs = 0, None, []
        self.pending, self.copies, self.skipped = [], 0, 0      # (staging index, w, h) in the order the copies were issued
        ctypes.windll.combase.RoInitialize(1)                # multithreaded; fine if COM is already set up here
        d3d11 = ctypes.windll.d3d11
        dev, ctx, level = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_uint()
        hr = d3d11.D3D11CreateDevice(None, 1, None, 0x20, None, 0, 7, ctypes.byref(dev), ctypes.byref(level),
                                     ctypes.byref(ctx))      # hardware, BGRA support, D3D11_SDK_VERSION
        if hr < 0:
            raise OSError(f"D3D11CreateDevice failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
        self.dev, self.ctx = self._keep(_Com(dev)), self._keep(_Com(ctx))
        try:
            dxgi = self.dev.query(IID_DXGI_DEVICE)
            insp = ctypes.c_void_p()
            hr = d3d11.CreateDirect3D11DeviceFromDXGIDevice(dxgi.ptr, ctypes.byref(insp))
            dxgi.release()
            if hr < 0:
                raise OSError(f"CreateDirect3D11DeviceFromDXGIDevice failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
            insp = _Com(insp)
            self.wdev = self._keep(insp.query(IID_D3D_DEVICE))
            insp.release()
            interop = _factory("Windows.Graphics.Capture.GraphicsCaptureItem", IID_ITEM_INTEROP)
            try:                                             # CreateForWindow
                self.item = self._keep(interop.out(3, (wt.HWND, ctypes.POINTER(_Guid)), wt.HWND(hwnd), ctypes.byref(IID_ITEM)))
            finally:
                interop.release()
            size = _Size()
            self.item.call(7, (ctypes.POINTER(_Size),), ctypes.byref(size))            # Size
            statics = _factory("Windows.Graphics.Capture.Direct3D11CaptureFramePool", IID_POOL_STATICS2)
            try:                                             # CreateFreeThreaded: no dispatcher queue, no events
                self.pool = self._keep(statics.out(6, (ctypes.c_void_p, ctypes.c_int, ctypes.c_int32, _Size),
                                                   self.wdev.ptr, BGRA, 1, size))
            finally:
                statics.release()
            self.pool_size = (size.w, size.h)
            self.session = self._keep(self.pool.out(10, (ctypes.c_void_p,), self.item.ptr))   # CreateCaptureSession
            try:
                s2 = self.session.query(IID_SESSION2)
                s2.call(7, (ctypes.c_ubyte,), 0)             # IsCursorCaptureEnabled = false: no cursor over the cells
                s2.release()
            except OSError:
                pass
            desc = _TexDesc(max_w, max_h, 1, 1, BGRA, 1, 0, 3, 0, 0x20000, 0)    # staging, CPU read
            self.ring = [self._keep(self.dev.out(5, (ctypes.POINTER(_TexDesc), ctypes.c_void_p), ctypes.byref(desc), None))
                         for _ in range(self.RING)]
            self.free = list(range(self.RING))
            self.session.call(6, ())                         # StartCapture
        except Exception:
            self.stop()
            raise

    def _keep(self, obj):
        self.objs.append(obj)
        return obj

    @property
    def closed(self):
        return not user32.IsWindow(self.hwnd)

    def _offset(self, size):
        """(dx, dy) of the client area in a frame of this size; None while the window is minimized, or for a frame that
        is smaller than the client area (a resize in progress). A frame holds the client area with the window's frame
        around it: the same border left, right and below (none for a borderless window), the title bar above, so
        dx = (frame width - client width) / 2 and dy = frame height - client height - dx. Measured on every frame from
        the client size, not matched against GetWindowRect / the DWM frame bounds: a capture started before the window
        was maximized keeps the restored frame's layout (1922 x 1040 around a 1920 x 1001 client: 1, 38), which is
        neither rectangle (the link was lost after a maximize), and while a window comes back from minimized those
        rectangles disagree for a moment (snaps were cut at the title bar)"""
        if user32.IsIconic(self.hwnd):
            return None
        rc = wt.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rc))
        dx = (size[0] - rc.right) // 2
        dy = size[1] - rc.bottom - dx
        if rc.right <= 0 or rc.bottom <= 0 or dx < 0 or dy < 0:
            return None
        return dx, dy

    def read(self, w=None, h=None, x=0, y=0):
        """(sequence number, RGB array of the w x h of the client area from (x, y), or None before the first one); the
        same pair again when nothing new is ready"""
        started = self._issue(w, h, x, y)
        until = time.perf_counter() + (self.SPIN if started else 0)
        while not self._collect() and self.pending and time.perf_counter() < until:
            time.sleep(0.001)
        return self.seq, self.latest

    def snapshot(self, timeout=1.0):
        """the whole client area of the next frame, as RGB, or None if none came within timeout. Waits for the GPU (a
        one-off picture, not the frame reading loop)"""
        end, frame = time.perf_counter() + timeout, None
        while frame is None and time.perf_counter() < end:
            while True:
                f = self.pool.out(7, ())                     # TryGetNextFrame
                if f is None:
                    break
                _close(frame)
                frame = f
            if frame is None:
                time.sleep(0.01)
        if frame is None:
            return None
        surface = access = tex = staging = None
        try:
            size = _Size()
            frame.call(8, (ctypes.POINTER(_Size),), ctypes.byref(size))        # ContentSize
            off = self._offset((size.w, size.h))
            if off is None:                                  # minimized, or a frame of a size the window does not have
                return None
            dx, dy = off
            rc = wt.RECT()
            user32.GetClientRect(self.hwnd, ctypes.byref(rc))
            w = min(rc.right, size.w - dx, self.pool_size[0] - dx)
            h = min(rc.bottom, size.h - dy, self.pool_size[1] - dy)
            if w <= 0 or h <= 0:
                return None
            surface = frame.out(6, ())
            access = surface.query(IID_DXGI_ACCESS)
            tex = access.out(3, (ctypes.POINTER(_Guid),), ctypes.byref(IID_TEXTURE2D))
            desc = _TexDesc(w, h, 1, 1, BGRA, 1, 0, 3, 0, 0x20000, 0)
            staging = self.dev.out(5, (ctypes.POINTER(_TexDesc), ctypes.c_void_p), ctypes.byref(desc), None)
            box = _Box(dx, dy, 0, dx + w, dy + h, 1)
            self.ctx.call(46, COPY_ARGS, staging.ptr, 0, 0, 0, 0, tex.ptr, 0, ctypes.byref(box), restype=None)
            m = _Mapped()
            self.ctx.call(14, MAP_ARGS, staging.ptr, 0, 1, 0, ctypes.byref(m))     # Map: read, waiting for the GPU
            try:
                buf = (ctypes.c_ubyte * (m.pitch * h)).from_address(m.data)
                return np.frombuffer(buf, np.uint8).reshape(h, m.pitch)[:, :w * 4].reshape(h, w, 4)[:, :, 2::-1].copy()
            finally:
                self.ctx.call(15, (ctypes.c_void_p, ctypes.c_uint), staging.ptr, 0, restype=None)
        finally:
            for o in (staging, tex, access, surface):
                if o is not None:
                    o.release()
            _close(frame)

    def _issue(self, w, h, x=0, y=0):
        """a part of the newest frame (w x h from (x, y) of the client area) into a free staging texture; False when
        there is no new frame or no free texture"""
        frame = None
        while True:                                          # the newest frame waiting, if any
            f = self.pool.out(7, ())                         # TryGetNextFrame
            if f is None:
                break
            _close(frame)
            frame = f
        if frame is None:
            return False
        surface = access = tex = None
        try:
            size = _Size()
            frame.call(8, (ctypes.POINTER(_Size),), ctypes.byref(size))        # ContentSize
            if (size.w, size.h) != self.pool_size:          # the window was resized: buffers of the new size from now on
                self.pool.call(6, (ctypes.c_void_p, ctypes.c_int, ctypes.c_int32, _Size), self.wdev.ptr, BGRA, 1, size)
                self.pool_size = (size.w, size.h)
                return False
            if not self.free:                                # the GPU is behind by RING copies: skip this frame
                self.skipped += 1
                return False
            off = self._offset((size.w, size.h))
            if off is None:
                return False
            dx, dy = off[0] + x, off[1] + y
            w = min(w or self.max_w, self.max_w, size.w - dx)
            h = min(h or self.max_h, self.max_h, size.h - dy)
            if w <= 0 or h <= 0:
                return False
            surface = frame.out(6, ())                                          # Surface
            access = surface.query(IID_DXGI_ACCESS)
            tex = access.out(3, (ctypes.POINTER(_Guid),), ctypes.byref(IID_TEXTURE2D))
            i = self.free.pop(0)
            box = _Box(dx, dy, 0, dx + w, dy + h, 1)
            self.ctx.call(46, COPY_ARGS, self.ring[i].ptr, 0, 0, 0, 0, tex.ptr, 0, ctypes.byref(box),
                          restype=None)                      # CopySubresourceRegion
            self.ctx.call(111, (), restype=None)             # Flush: the GPU starts on it now, nobody waits
            self.pending.append((i, w, h))
            self.copies += 1
            return True
        finally:
            for o in (tex, access, surface):
                if o is not None:
                    o.release()
            _close(frame)                                    # the frame goes back to the pool; the copy is queued

    def _collect(self):
        """every finished copy, oldest first (the GPU does them in order); the newest becomes latest. True if any"""
        got = False
        while self.pending:
            i, w, h = self.pending[0]
            m = _Mapped()
            try:
                self.ctx.call(14, MAP_ARGS, self.ring[i].ptr, 0, 1, 0x100000, ctypes.byref(m))   # Map: read, do not wait
            except ComError as e:
                if e.hr == STILL_DRAWING:
                    break
                raise
            try:
                buf = (ctypes.c_ubyte * (m.pitch * h)).from_address(m.data)
                img = np.frombuffer(buf, np.uint8).reshape(h, m.pitch)[:, :w * 4].reshape(h, w, 4)[:, :, 2::-1].copy()
            finally:
                self.ctx.call(15, (ctypes.c_void_p, ctypes.c_uint), self.ring[i].ptr, 0, restype=None)   # Unmap
            self.pending.pop(0)
            self.free.append(i)
            self.seq += 1
            self.latest = img
            got = True
        return got

    def stop(self):
        for name in ("session", "pool"):
            o = getattr(self, name, None)
            if o is not None and o.ptr:
                try:
                    c = o.query(IID_CLOSABLE)
                    c.call(6, ())
                    c.release()
                except OSError:
                    pass
        for o in reversed(self.objs):
            try:
                o.release()
            except OSError:
                pass
        self.objs = []
