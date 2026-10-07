"""What the daemon's clients and the daemon share without the daemon's dependencies: the error type that maps to the
JSON error body, the RUN result parser, the kinds of a log entry, state/daemon.json. Standard library only, so the CLI
can import it without the HTTP stack."""
import json
import re

from ..paths import daemon_file

LOG_KINDS = ("ERR", "OUT", "WARN", "BLOCKED", "DROPPED", "RUN", "RELOAD", "SNAP", "WATCH", "SLOTS", "INFO")


def read_daemon_json():
    """state/daemon.json as a dict ({"pid", "port", "token", "version", "started", "mode", "url", "mcp_url"}), or None
    when there is none (or it is not JSON): the daemon writes it when it listens and removes it when it exits"""
    try:
        info = json.loads(daemon_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and "port" in info and "token" in info else None


class ApiError(Exception):
    """an error answer: HTTP status, a short code, a message, extra fields for the body (job, for one)"""

    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def body(self):
        return {"error": dict(code=self.code, message=self.message, **self.extra), **self.extra}


_RUN = re.compile(r"^(\d+) (ok|error) (.*)$", re.S)
_OK = re.compile(r"^(.*?) \((\d+) B, ([\d.]+) ms\)(.*)$", re.S)       # the chunk name may hold spaces: C:/Program Files
_NOTES = (" (no namespace registered for ", " (OnUnload ", " (OnReload ")   # what Agent.lua appends after the values


def parse_run(text):
    """the body of a "RUN ..." debug message from the addon (the "RUN " removed), as the result of /api/run:
    "<job> ok <chunk> (<bytes> B, <ms> ms)[: <values>][ (<note>)...]" -> {ok, job, chunk, values, ms, bytes, note}
    "<job> error <chunk>: <error>\\n<stack>" -> {ok: False, job, chunk, error, stack}; None for anything else.
    The daemon's own chunk "=probe" (agent/probes.py) returns one string and gets no notes: it is the one value, whole"""
    m = _RUN.match(text)
    if not m:
        return None
    job, state, rest = int(m[1]), m[2], m[3]
    if state == "error":
        chunk, sep, err = rest.partition(": ")      # a chunk name has no ": " (a path has ":/"), the message does
        if not sep:
            chunk, err = rest.strip(), ""
        error, _, stack = err.partition("\n")
        return dict(ok=False, job=job, chunk=chunk, error=error, stack=stack)
    m2 = _OK.match(rest)
    if not m2:                                      # not what Agent.lua writes: keep what can be told
        return dict(ok=True, job=job, chunk=rest.strip(), values=[], ms=None, bytes=None, note=None)
    chunk, size, ms, tail = m2[1], int(m2[2]), float(m2[3]), m2[4]
    if chunk == "=probe":                           # JSON: its ", " and " (" are text, not separators or notes
        return dict(ok=True, job=job, chunk=chunk, values=[tail[2:]] if tail.startswith(": ") else [], ms=ms,
                    bytes=size, note=None)
    note = None
    found = [i for i in (tail.find(n) for n in _NOTES) if i >= 0]
    if found:
        i = min(found)
        note, tail = tail[i + 1:].rstrip(), tail[:i]
    values = []
    if tail.startswith(": "):
        v = tail[2:]
        values = [v] if "\n" in v else v.split(", ")     # a table dump is one value; plain values are comma-separated
    return dict(ok=True, job=job, chunk=chunk, values=values, ms=ms, bytes=size, note=note)


def chunk_file(chunk):
    """the file a chunk name stands for ("@Interface/AddOns/Foo/Core.lua" -> "Interface/AddOns/Foo/Core.lua"), else None"""
    return chunk[1:] if chunk.startswith("@") else None
