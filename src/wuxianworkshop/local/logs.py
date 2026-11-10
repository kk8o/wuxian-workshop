r"""The output of a local extension: <state>\extensions\<id>\output.log (output.1.log before it, each at most LOG_MAX
bytes). It holds the extension's stderr, whatever it wrote on stdout that is no protocol, and the host's lines on its
starts and ends (they begin with "-- " and the time, clock()).

Output writes the file (any thread; one lock per file) and moves it to output.1.log once it is full; Lines indexes a
file by line as it grows, for the reads by line number (window: what the host's logs() answers with); tail gives the
last lines of a file (what a failed start quotes). A check writes its own file, check.log, the same way.
Standard library only.
"""
import os
import threading
import time
from array import array
from pathlib import Path

from ..daemon.api import ApiError
from .registry import replace_retrying

LOG_MAX = 2 * 1024 * 1024     # bytes of output.log before it becomes output.1.log
LINE_MAX = 4000               # characters of an output line in an answer
LOGS_MOST = 2000              # lines one answer of logs() gives at most
TAIL = 12                     # lines of the output a failed start quotes


def clock():
    return time.strftime("%H:%M:%S")


def log_max():
    """LOG_MAX as the host module has it at this moment. Before host.py was split the name was host.LOG_MAX, and that
    is the one set from outside (tests/local/test_host.py sets it for a rotation), so the host module's is the one
    that counts"""
    from . import host
    return host.LOG_MAX


def output_path(root, eid):
    """the output file of an extension under the state folder"""
    return Path(root) / "extensions" / eid / "output.log"


# --- writing ---------------------------------------------------------------------------------------------------------

class Output:
    """an extension's output.log, written by the host (its stderr lines, the host's own), moved to output.1.log once it
    holds LOG_MAX bytes"""

    def __init__(self, path):
        self.path, self.lock, self.file = Path(path), threading.Lock(), None

    def write(self, text):
        data = (str(text).rstrip("\r\n") + "\n").encode("utf-8", "replace")
        with self.lock:
            try:
                if self.file is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.file = open(self.path, "ab")
                if self.file.tell() and self.file.tell() + len(data) > log_max():
                    self.file.close()
                    self.file = None
                    replace_retrying(self.path, self.path.with_name("output.1.log"))
                    self.file = open(self.path, "ab")
                self.file.write(data)
                self.file.flush()
            except OSError:
                if self.file is not None:
                    try:
                        self.file.close()
                    except OSError:
                        pass
                self.file = None

    def close(self):
        with self.lock:
            if self.file is not None:
                try:
                    self.file.close()
                except OSError:
                    pass
                self.file = None


# --- reading ---------------------------------------------------------------------------------------------------------

class Lines:
    """the lines of an output file, indexed as it grows: starts[i] is where line i + 1 begins, and the last item where
    the line still being written begins. Another file under the name (it was moved to output.1.log) is indexed from
    its start"""

    def __init__(self, path):
        self.path = Path(path)
        self.reset(None)

    def reset(self, ident):
        self.ident, self.starts, self.size = ident, array("q", [0]), 0

    def update(self):
        try:
            st = os.stat(self.path)
        except OSError:
            self.reset(None)
            return 0
        ident = (st.st_dev, st.st_ino)
        if ident != self.ident or st.st_size < self.size:
            self.reset(ident)
        if st.st_size > self.size:
            try:
                with open(self.path, "rb") as f:
                    f.seek(self.size)
                    pos = self.size
                    while pos < st.st_size:
                        block = f.read(min(1 << 20, st.st_size - pos))
                        if not block:
                            break
                        i = block.find(b"\n")
                        while i >= 0:
                            self.starts.append(pos + i + 1)
                            i = block.find(b"\n", i + 1)
                        pos += len(block)
                    self.size = pos
            except OSError:
                pass
        return self.size

    @property
    def complete(self):
        return len(self.starts) - 1

    def read(self, first, last):
        """the texts of lines first..last (1-based)"""
        begin = self.starts[first - 1]
        end = self.starts[last] if last < len(self.starts) else self.size
        try:
            with open(self.path, "rb") as f:
                f.seek(begin)
                data = f.read(end - begin)
        except OSError:
            return []
        parts = data.split(b"\n")
        if data.endswith(b"\n"):
            parts.pop()
        out = []
        for raw in parts:
            text = raw.decode("utf-8", "replace").rstrip("\r")
            out.append(text if len(text) <= LINE_MAX else text[:LINE_MAX] + "…")
        return out


def check_window(since, limit):
    """ApiError bad_request unless since and limit are what a read by line takes (window)"""
    if not isinstance(since, int) or isinstance(since, bool) or since < 0:
        raise ApiError(400, "bad_request", "since: 0 (the newest lines) or a line number (the `next` of the last read)")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= LOGS_MOST:
        raise ApiError(400, "bad_request", f"limit: 1..{LOGS_MOST}")


def window(lines, since, limit):
    """(first, texts, total, reset) of a read of Lines, indexed up to now: the texts of at most limit lines from line
    first on, of total complete lines. since 0: its last `limit` lines; since n: from line n on; a since past the end:
    the file was moved to output.1.log, its lines from the first (reset True)"""
    lines.update()
    total = lines.complete
    reset = False
    if since == 0:
        first = max(1, total - limit + 1)
    elif since > total + 1:
        first, reset = 1, True
    else:
        first = since
    last = min(total, first + limit - 1)
    texts = lines.read(first, last) if last >= first else []
    return first, texts, total, reset


def tail(path, n=TAIL):
    """the last n lines of a file"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            data = f.read()
    except OSError:
        return []
    return [line.decode("utf-8", "replace").rstrip("\r")[:LINE_MAX] for line in data.split(b"\n") if line.strip()][-n:]
