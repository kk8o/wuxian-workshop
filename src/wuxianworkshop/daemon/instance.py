r"""One daemon per user: the named mutex Local\WuxianWorkshop (WUXIAN_MUTEX names another: a test instance beside the
one in use, isolated from the game: core.game.isolated). Light to import: the shell takes it before it opens its window
(ui/shell.py), the daemon when it starts (server.start)."""
import ctypes
import os

MUTEX = r"Local\WuxianWorkshop"
ERROR_ALREADY_EXISTS = 183


def instance_mutex(name=None):
    """the mutex's name: the one given, else WUXIAN_MUTEX's, else MUTEX"""
    return name or os.environ.get("WUXIAN_MUTEX") or MUTEX


class SingleInstance:
    """a named mutex held for the life of the process: the second CreateMutexW of the name says ERROR_ALREADY_EXISTS"""

    def __init__(self, name=MUTEX):
        self.name, self.handle = name, None
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.CreateMutexW.restype = ctypes.c_void_p
        self.k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        self.k32.CloseHandle.argtypes = (ctypes.c_void_p,)

    def acquire(self):
        h = self.k32.CreateMutexW(None, 0, self.name)
        if not h:                                    # cannot tell: do not keep the daemon from running
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self.k32.CloseHandle(h)
            return False
        self.handle = h
        return True

    def release(self):
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None
