"""debug.log (paths.logs_dir): the addon's debug output and the companion's notes for the agent, one entry per message.

The file stays open between entries (they come from the asyncio loop and the screen-reading thread: a lock keeps them
whole) and each entry is written through at once, so a reader sees it as soon as it is logged. Past MAX_BYTES the file
becomes debug.log.1 (the one before goes) and a new debug.log begins: a reader that follows the file should open
debug.log again when it shrinks, and read debug.log.1 first for the older entries. close() lets the file go; an entry
written after it opens the file for that entry only.
"""
import os
import threading
import time

MAX_BYTES = 10 * 1024 * 1024      # debug.log's size before it is rotated to debug.log.1
RETRY_ROTATE = 1024 * 1024        # bytes to write before trying again when the rename failed (another process has it)


def rotated(path):
    """the older file of a debug.log: debug.log.1"""
    return path.with_name(path.name + ".1")


class DebugLog:
    """on_debug for the companion and the journal's writer: write(kind, text) appends one entry, continuation lines
    indented; the text is UTF-8 with the platform's line ends, as a text file opened for appending writes it"""

    def __init__(self, path, max_bytes=MAX_BYTES):
        self.path, self.max_bytes = path, max_bytes
        self.lock = threading.Lock()
        self.fh, self.size, self.closed = None, 0, False
        self.rotate_at = max_bytes

    def __call__(self, kind, text):
        lines = text.split("\n")
        entry = f"{time.strftime('%H:%M:%S')} {kind} {lines[0]}\n" + "".join(f"    {line}\n" for line in lines[1:])
        data = entry.replace("\n", os.linesep).encode("utf-8")      # a text that UTF-8 cannot take fails before writing
        with self.lock:
            if self.closed:
                with self.path.open("ab") as fh:
                    fh.write(data)
                return
            if self.fh is None:
                self.fh = open(self.path, "ab", buffering=0)          # unbuffered: every write reaches the file
                self.size = self.fh.seek(0, os.SEEK_END)
                self.rotate_at = max(self.max_bytes, 1)
            self.fh.write(data)
            self.size += len(data)
            if self.size >= self.rotate_at:
                self._rotate()

    def _rotate(self):
        self.fh.close()
        self.fh = None
        try:
            os.replace(self.path, rotated(self.path))
        except OSError:                 # held open elsewhere (Windows): go on in this file, try again later
            self.fh = open(self.path, "ab", buffering=0)
            self.size = self.fh.seek(0, os.SEEK_END)
            self.rotate_at = self.size + RETRY_ROTATE

    def close(self):
        """let the file go (the daemon stops; a test removes the folder)"""
        with self.lock:
            self.closed = True
            if self.fh is not None:
                self.fh.close()
                self.fh = None


def debug_writer(path, max_bytes=MAX_BYTES):
    """on_debug for the companion: one entry per message, continuation lines indented (a DebugLog)"""
    return DebugLog(path, max_bytes)
