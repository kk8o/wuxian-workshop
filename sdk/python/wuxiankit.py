# MIT License
#
# Copyright (c) 2026 kk8o
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
# Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""WuxianKit for Python: one file, standard library only (Python 3.10+). 无限工坊 puts it on the PYTHONPATH of the
local extensions it starts; other projects copy it.

A local extension (a folder with wuxian-extension.json that 无限工坊 starts and talks to over stdin / stdout) is an
Extension: capabilities, sources and options, the same model as a WuxianKit extension in the game, running on this
computer. The agent calls its capabilities as the tools wk_<extension>_<name>:

    from wuxiankit import Extension, ExtensionError

    ext = Extension()                                  # its id: $WUXIAN_EXTENSION, else the manifest's
    volume = ext.option("volume", kind="number", default=80, min=0, max=100, title="Volume")
    said = ext.source("said", title="Said a line", doc="Each line spoken: {text, seconds}.")

    @ext.point("say", title="Say a line", doc="Speaks text aloud; returns {seconds}.", args={"text": "string"})
    def say(text):
        seconds = speak(text, volume.get())
        said.emit({"text": text, "seconds": seconds})
        return {"seconds": seconds}

    ext.run(setup=load_model)                          # answers the workshop until it says shutdown

ExtensionTest runs an Extension the way the workshop would, over the same lines, for unit tests. The protocol (JSON-RPC
2.0, one object per line) is in the class docstring of Extension and in 无限工坊's extension_guide. What a declaration
may be (names, argument specs, options) is checked by the same text the workshop checks it with: the block between
the two "shared" marker lines below, a copy of the workshop's local/validate.py.

An AI app (a local voice companion, a chat helper, ...) reaches the player's game through 无限工坊's daemon, which runs
on this computer, and the WuxianKit addon in the game:

    from wuxiankit import Client

    kit = Client()                                     # finds the daemon (state/daemon.json) and its token
    kit.call("sense.character")                        # a capability: its result
    p = kit.propose([("chat.send", {"channel": "guild", "text": "hi"})], title="Say hi")
    kit.wait(p["id"])                                  # the proposal once it ended (done, declined, ...)
    kit.open("companion", minutes=120)                 # start a declared app (one that runs goes on)
    for wake in kit.app("companion"):
        ...                                            # {app, seq, reason, items, skipped, after, next, ...}

Client.app joins an app that runs; it never opens or declares one (Client.open starts a declared app; an app is
declared by a local extension's manifest, by an addon, or with kit.call("kit.app.declare", {...}); WuxianKit
ARCHITECTURE.md section 10). Its cursor is WuxianKit's item id: `since=N` goes on after item N (what was heard
meanwhile comes first, as one wake with reason "catchup"), without it the stream starts where the app's last wake
ended. The stream sends the heartbeat (kit.app.status) so the app is not taken for gone, long-polls its wakes, reads
the status again after a UI reload (kit.ready) or a restart of the daemon, catches up on what it may have missed
(kit.app.recent), leaves out items it gave already, and ends when the app does not run (any more) for any reason but
"replaced". It never raises while the daemon or the game is away: it waits and tries again (1, 2, 5, then every
10 s); `stream.state` tells why it waits.

Daemon: POST /api/call {addon, name, args, timeout_ms} -> {addon, name, ok, result}; POST /api/addon_events {addon,
topic, since, limit, wait} -> {events: [{id, t, addon, topic, data, dropped}], next, truncated, session}; errors
{"error": {"code", "message"}} with a 4xx / 5xx status. A workshop before 0.9.13 takes one topic (a name or a glob)
only; the client then asks with one glob that covers all of them and keeps the ones asked for.
"""
from __future__ import annotations

import collections
import fnmatch
import inspect
import itertools
import json
import math
import os
import queue
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

__version__ = "0.8.0"
__all__ = ["Client", "AppStream", "WuxianError", "daemon_json_path", "read_daemon_json", "cover_glob", "FINAL",
           "Extension", "ExtensionError", "ExtensionTest", "Option", "Source", "check_args", "parse_spec",
           "check_description"]

ADDON = "WuxianKit"
FINAL = frozenset({"done", "failed", "declined", "expired", "undone"})     # a proposal's end states
BACKOFF = (1.0, 2.0, 5.0, 10.0)        # seconds to wait after the 1st, 2nd, 3rd, 4th and later failure in a row
CALL_TIMEOUT_MS = 10000
POLL_WAIT = 25.0                       # seconds one long-poll waits at most
ASK_EVERY = 15.0                       # wait(): seconds it waits for kit.proposal before it asks the game itself
LEASE = 600                            # an app's lease when its status gives none
CATCHUP = 100                          # the items a catch-up asks kit.app.recent for: all an app keeps


def daemon_json_path(override: Union[str, os.PathLike, None] = None) -> Path:
    """where the daemon says where it listens: %WUXIAN_HOME%\\state\\daemon.json, else
    %LOCALAPPDATA%\\WuxianWorkshop\\state\\daemon.json (the workshop's paths.daemon_file())"""
    if override:
        return Path(override)
    home = os.environ.get("WUXIAN_HOME")
    if home:
        root = Path(home)
    else:
        local = os.environ.get("LOCALAPPDATA")
        root = (Path(local) if local else Path.home() / "AppData" / "Local") / "WuxianWorkshop"
    return root / "state" / "daemon.json"


def read_daemon_json(path: Union[str, os.PathLike]) -> Optional[dict]:
    """{port, token, url?, pid, started}; None when the file is not there, not JSON or without port and token"""
    try:
        info = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and info.get("port") and info.get("token") else None


class WuxianError(Exception):
    """a request that did not work. reason: for people; code: the daemon's error code (link_down, call_failed,
    bad_request, ...) or one of the client's own: no_daemon (not running, not reachable), timeout, bad_answer,
    no_proposal; status: the HTTP status (0 when no answer came)"""

    def __init__(self, reason: str, code: str = "", status: int = 0):
        super().__init__(reason)
        self.reason, self.code, self.status = reason, code, status

    def __repr__(self):
        return f"WuxianError({self.reason!r}, {self.code!r}, {self.status})"


GLOB_CHARS = "*?["
CLASS_UNSAFE = set("]!^-\\[")


def cover_glob(topics: Iterable[str]) -> str:
    """one glob that matches every topic given (names or globs), for a daemon that takes one topic only: a character
    class for each place of the shortest literal start, then *. "app.x", "kit.app", "kit.ready" -> "[ak][ip][pt].[arx]*" """
    topics = list(topics)
    if len(topics) == 1:
        return topics[0]
    heads = [t[:min([t.index(c) for c in GLOB_CHARS if c in t] or [len(t)])] for t in topics]
    out = []
    for i in range(min((len(h) for h in heads), default=0)):
        chars = sorted({h[i] for h in heads})
        if CLASS_UNSAFE & set(chars):
            break
        out.append(chars[0] if len(chars) == 1 else "[" + "".join(chars) + "]")
    return "".join(out) + "*"


def _topic_list(topics: Union[str, Iterable[str]]) -> list[str]:
    out = [topics] if isinstance(topics, str) else [t for t in topics]
    if not out or not all(isinstance(t, str) and t.strip() for t in out):
        raise ValueError("topics: a topic or a list of topics (names or globs)")
    return out


class Client:
    """the daemon of 无限工坊 on this computer. daemon_json: where its daemon.json is (default: daemon_json_path()).
    clock and sleep: time.time and time.sleep, replaced in tests"""

    def __init__(self, daemon_json: Union[str, os.PathLike, None] = None, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, addon: str = ADDON):
        self.path = daemon_json_path(daemon_json)
        self.clock, self.sleep, self.addon = clock, sleep, addon
        self._info: Optional[dict] = None
        self.daemon: Optional[tuple] = None              # (pid, started) of the daemon last read: changes on a restart
        self.topic_mode: Optional[str] = None            # "list" or "glob" once known (reset on a daemon restart)

    # ---- the daemon

    def info(self) -> dict:
        """daemon.json as last read; read again after a failed connection or a 401"""
        if self._info is None:
            info = read_daemon_json(self.path)
            if info is None:
                raise WuxianError(f"无限工坊 is not running (no daemon.json at {self.path})", "no_daemon")
            ident = (info.get("pid"), info.get("started"))
            if self.daemon is not None and ident != self.daemon:
                self.topic_mode = None                   # another daemon: maybe another workshop version
            self.daemon, self._info = ident, info
        return self._info

    def forget(self) -> None:
        """read daemon.json again before the next request"""
        self._info = None

    def request(self, path: str, body: dict, timeout: float) -> dict:
        """one POST to the daemon: its JSON answer, or WuxianError. A 401 reads daemon.json again and tries once more
        (the daemon restarted with another token)"""
        info = self.info()
        try:
            return self._post(info, path, body, timeout)
        except WuxianError as e:
            if e.status != 401:
                raise
            first = e
        try:                                             # _post forgot daemon.json: read it again
            fresh = self.info()
        except WuxianError:
            raise first from None
        if fresh.get("token") == info.get("token"):
            raise first
        return self._post(fresh, path, body, timeout)

    def _post(self, info: dict, path: str, body: dict, timeout: float) -> dict:
        base = (info.get("url") or f"http://127.0.0.1:{info['port']}/").rstrip("/")
        req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Authorization": f"Bearer {info['token']}", "Accept": "application/json",
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            code, message = "http_error", f"{e.code} {e.reason}"
            try:
                err = json.loads(e.read())["error"]
                code, message = str(err.get("code") or code), str(err.get("message") or message)
            except (ValueError, KeyError, TypeError, OSError):
                pass
            finally:
                e.close()
            if e.code == 401:
                self.forget()
            if code == "link_down":
                raise WuxianError("the game is not connected (not running, not in the world, or minimized)", code,
                                  e.code) from None
            raise WuxianError(message[:2000], code, e.code) from None
        except (socket.timeout, TimeoutError):
            raise WuxianError(f"the daemon did not answer in {timeout:.0f} s", "timeout") from None
        except (urllib.error.URLError, OSError) as e:
            self.forget()
            reason = getattr(e, "reason", e)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise WuxianError(f"the daemon did not answer in {timeout:.0f} s", "timeout") from None
            raise WuxianError(f"无限工坊 is not running (nothing answers at {base}: {reason})", "no_daemon") from None
        try:
            answer = json.loads(payload) if payload else {}
        except ValueError:
            raise WuxianError("the daemon's answer is not JSON", "bad_answer") from None
        if not isinstance(answer, dict):
            raise WuxianError("the daemon's answer is not a JSON object", "bad_answer")
        return answer

    # ---- capabilities and proposals

    def call(self, name: str, args: Optional[dict] = None, *, timeout_ms: int = CALL_TIMEOUT_MS) -> Any:
        """a WuxianKit capability (`sense.character`, `chat.send`, ...): its result. A Send or Change answers with its
        proposal {id, state, steps}; _title, _ttl and _after in args set its title, ttl and earliest time. A capability
        that refused (bad args, an error in the game) is WuxianError code call_failed"""
        body = {"addon": self.addon, "name": name, "timeout_ms": int(timeout_ms)}
        if args is not None:
            body["args"] = args
        return self.request("/api/call", body, timeout_ms / 1000 + 30).get("result")

    def propose(self, steps: Iterable, title: Optional[str] = None, ttl: Optional[float] = None,
                after: Optional[float] = None) -> dict:
        """several Send / Change steps in one proposal the player confirms once (kit.propose). steps: {"cap", "args"}
        or (cap, args). ttl: seconds it waits for the player (10-600); after: a Unix time before which it may not run"""
        norm = []
        for s in steps:
            if isinstance(s, dict):
                norm.append({"cap": s.get("cap"), "args": s.get("args") or {}})
            else:
                cap, args = (list(s) + [None])[:2]
                norm.append({"cap": cap, "args": args or {}})
        body = {"steps": norm}
        for k, v in (("title", title), ("ttl", ttl), ("after", after)):
            if v is not None:
                body[k] = v
        return self.call("kit.propose", body)

    def proposal(self, proposal_id: int) -> Optional[dict]:
        """a proposal as the game has it now; None when the game has no such one"""
        try:
            p = self.call("kit.proposal", {"id": proposal_id})
        except WuxianError as e:
            if e.code == "call_failed":
                return None
            raise
        return p if isinstance(p, dict) else None

    def wait(self, proposal_id: int, timeout: float = 120) -> dict:
        """the proposal once it ended (state done, failed, declined, expired or undone). After timeout seconds: the
        proposal as it is then, with timeout = True. Hears kit.proposal; asks the game (kit.proposal) when none comes.
        WuxianError code no_proposal: the game has no such proposal (a UI reload drops the ones that have not run)"""
        mark = self.events("kit.proposal", since=-1, wait=0, limit=1).get("next", -1)
        now = self.proposal(proposal_id)
        if now is None:
            raise WuxianError(f"no proposal #{proposal_id} in the game: it never ran and is gone", "no_proposal")
        deadline = self.clock() + max(0.0, float(timeout))
        while now.get("state") not in FINAL:
            left = deadline - self.clock()
            if left <= 0:
                return dict(now, timeout=True)
            t0 = self.clock()
            ans = self.events("kit.proposal", since=mark, wait=min(left, ASK_EVERY))
            mark = ans.get("next", mark)
            mine = [e["data"] for e in ans["events"] if isinstance(e.get("data"), dict)
                    and e["data"].get("id") == proposal_id]
            if mine:
                now = dict(now, **mine[-1])
                if now.get("state") in FINAL:
                    full = self.proposal(proposal_id)
                    return full if full and full.get("state") in FINAL else now
                continue
            fresh = self.proposal(proposal_id)          # nothing heard: an event may have been lost
            if fresh and fresh.get("state"):
                now = fresh
                if now["state"] in FINAL:
                    return now
            if self.clock() - t0 < 0.5:                  # nothing came, at once: do not spin
                self.sleep(min(1.0, max(0.0, deadline - self.clock())))
        return now

    # ---- events

    def events(self, topics: Union[str, Iterable[str]], since: int = -1, wait: float = POLL_WAIT,
               limit: int = 100) -> dict:
        """WuxianKit's events of these topics (a name or a glob, or a list of them) from id `since` on (-1: only the
        ones still to come), at most `limit`; with `wait` seconds and none there yet, it waits for the first one.
        {events: [{id, t, addon, topic, data, dropped}], next (the since of the next ask), truncated, session}"""
        wanted = _topic_list(topics)
        wait = max(0.0, min(float(wait), 300.0))
        body = {"addon": self.addon, "since": since, "limit": limit, "wait": wait}
        if len(wanted) == 1:
            ans = self.request("/api/addon_events", dict(body, topic=wanted[0]), wait + 30)
        else:
            ans = None
            if self.topic_mode != "glob":
                try:
                    ans = self.request("/api/addon_events", dict(body, topic=wanted), wait + 30)
                    self.topic_mode = "list"
                except WuxianError as e:
                    if not (e.status == 400 and e.code == "bad_request" and "topic" in e.reason):
                        raise
                    self.topic_mode = "glob"             # a workshop before lists: one glob, filtered here
            if ans is None:
                ans = self.request("/api/addon_events", dict(body, topic=cover_glob(wanted)), wait + 30)
        events = [e for e in ans.get("events") or [] if isinstance(e, dict)
                  and any(fnmatch.fnmatchcase(str(e.get("topic") or ""), t) for t in wanted)]
        return dict(ans, events=events)

    # ---- apps

    def open(self, app_id: str, minutes: Optional[float] = None, lease: Optional[float] = None) -> dict:
        """start a declared app (kit.app.open {app, minutes?, lease?}): its status {on, ends, lease, next, ...}. One
        that runs goes on (with the new minutes / lease when given). WuxianError code not_started: it cannot start now
        (the player blocked it, ...); code call_failed: no app of this id is declared"""
        args: dict = {"app": app_id}
        for k, v in (("minutes", minutes), ("lease", lease)):
            if v is not None:
                args[k] = v
        ans = self.call("kit.app.open", args)
        ans = ans if isinstance(ans, dict) else {}
        if ans.get("on") is False:
            raise WuxianError(str(ans.get("blocked") or ans.get("why") or f"the app {app_id} did not start"),
                              "not_started")
        return ans

    def app(self, app_id: str, *, since: Optional[int] = None, wait: Optional[float] = None,
            yield_timeouts: bool = False) -> "AppStream":
        """the wakes of an AI app that runs (ARCHITECTURE.md section 10): {app, seq, reason, items, skipped, after,
        next, state?, pace?, quiet?, notes?}; a wake covers the items after < id <= next. It joins only: an app that
        does not run ends the stream at once (stream.ended tells why); Client.open starts one. since: the id of the
        last item you handled (a wake's next): what the app's wakes brought after it comes first, as one wake with
        reason "catchup"; None: from where its last wake ended (what it heard since and still gathers comes with its
        next wake). wait: seconds one next() blocks at most (None: until a wake); after it, next() gives None with
        yield_timeouts=True, else the iteration stops (the stream stays open: iterate again to go on)"""
        return AppStream(self, app_id, since=since, wait=wait, yield_timeouts=yield_timeouts)


def _id(v) -> Optional[int]:
    """an item id as the game's JSON gives it (12 or 12.0): 12; None for anything else"""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return int(v)


class AppStream:
    """the wakes of one app that runs; see Client.app. Attributes: cursor (the id of the last item given or passed
    over: pass it as since to go on later; None until the app's status was read, unless since was given); state
    ("connected" | "waiting", reason); ended (None while it listens; else why it ended: stopped, player, expired,
    left, extension, forgotten, closed, ...); status (the last kit.app.status answer)"""

    def __init__(self, client: Client, app_id: str, *, since: Optional[int] = None, wait: Optional[float] = None,
                 yield_timeouts: bool = False):
        self.client, self.id = client, app_id
        self.wait, self.yield_timeouts = wait, yield_timeouts
        self.topic = f"app.{app_id}"
        self.topics = [self.topic, "kit.app", "kit.ready"]
        self.state: tuple[str, str] = ("waiting", "not connected yet")
        self.ended: Optional[str] = None
        self.status: dict = {}
        self.cursor: Optional[int] = None if since is None else max(0, int(since))
        self._at = -1                                    # the place in the daemon's events (-1: not marked yet)
        self._daemon: Optional[tuple] = None
        self._joined = False                             # the status was read since the last doubt about the app
        self._missed = since is not None                 # wakes may have passed: catch up before the next poll
        self._sent: Optional[int] = None                 # where the app's wakes got (the status's sent)
        self._held: list = []                            # wakes that wait for a catch-up that failed
        self._beat_at = 0.0                              # when the next heartbeat is due
        self._fails = 0
        self._retry_at = 0.0
        self._pending: collections.deque = collections.deque()

    # ---- iteration

    def __iter__(self):
        return self

    def __next__(self) -> Optional[dict]:
        wake = self.get(self.wait)
        if wake is None and not self._pending:
            if self.ended is not None or not self.yield_timeouts:
                raise StopIteration
        return wake

    def get(self, timeout: Optional[float] = None) -> Optional[dict]:
        """the next wake; None after timeout seconds (None: wait until one comes) or once the app ended (see ended)"""
        clock = self.client.clock
        deadline = None if timeout is None else clock() + max(0.0, float(timeout))
        while True:
            if self._pending:
                return self._pending.popleft()
            if self.ended is not None:
                return None
            left = None if deadline is None else deadline - clock()
            if left is not None and left <= 0:
                return None
            self._step(POLL_WAIT if left is None else min(left, POLL_WAIT))

    def close(self, stop: bool = False) -> None:
        """stop listening; stop=True also stops the app in the game (kit.app.stop)"""
        if stop:
            try:
                self.client.call("kit.app.stop", {"app": self.id})
            except WuxianError:
                pass
        if self.ended is None:
            self.ended = "closed"
        self._pending.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- one round

    def _step(self, budget: float) -> None:
        c = self.client
        now = c.clock()
        if now < self._retry_at:                         # backing off: sleep until then (or the budget is spent)
            c.sleep(max(0.0, min(budget, self._retry_at - now)))
            return
        fails = self._fails
        try:
            c.info()
            self._check_daemon()
            if self._at == -1:                           # mark the place first: no wake between status and poll is lost
                self._at = c.events(self.topics, since=-1, wait=0, limit=1).get("next", -1)
            if not self._joined or c.clock() >= self._beat_at:
                self._join()                             # the heartbeat too
                if self.ended is not None:
                    return
            if self._missed:
                self._catch_up()
                self._missed = False
            if self._held:
                held, self._held = self._held, []
                for data in held:
                    self._wake(data)
                if self._held:                           # the catch-up failed again: _wake set the backoff
                    return
            if self._pending:
                return self._ok()
            ans = c.events(self.topics, since=self._at, wait=max(0.0, min(budget, self._beat_at - c.clock())))
        except WuxianError as e:
            return self._fail(e.reason)
        if self._check_daemon():                         # the answer came from another daemon: from the start
            return
        nxt = ans.get("next")
        if isinstance(nxt, int) and not isinstance(nxt, bool):
            self._at = nxt
        if ans.get("truncated"):                         # the daemon dropped events we did not read: where the app's
            self._missed, self._joined = True, False     # wakes got by now is in its status
        for ev in ans["events"]:
            self._take(ev)
            if self.ended is not None:
                break
        if self._fails == fails:
            self._ok()

    def _check_daemon(self) -> bool:
        """True when the daemon is another one than the last time: its event ids start again, so the stream asks from
        the next one to come, reads the app's status and catches up"""
        ident = self.client.daemon
        if self._daemon is not None and ident != self._daemon:
            self._daemon, self._at, self._joined, self._missed = ident, -1, False, True
            return True
        self._daemon = ident
        return False

    def _join(self) -> None:
        """read the app's status (which is its heartbeat as well): it runs, or the stream ends"""
        c = self.client
        self._joined = False
        try:
            ans = c.call("kit.app.status", {"app": self.id})
        except WuxianError as e:
            if e.code != "call_failed":
                raise
            self.ended = "stopped"                       # the game knows no app of this id
            return
        if not isinstance(ans, dict) or not ans:
            raise WuxianError("kit.app.status gave no status", "bad_answer")
        self.status = ans
        if not ans.get("on"):
            self.ended = str(ans.get("why") or "stopped")
            return
        self._beat_at = c.clock() + self.beat_every
        nxt, self._sent = _id(ans.get("next")), _id(ans.get("sent"))
        if self.cursor is None:                          # a WuxianKit without sent: from its newest item on
            self.cursor = self._sent if self._sent is not None else nxt or 0
        elif nxt is not None and nxt < self.cursor:      # the app's items end before the cursor (another character,
            self.cursor = nxt                            # saved data that started again): every wake would be a repeat
        self._joined = True

    @property
    def beat_every(self) -> float:
        """seconds between heartbeats: a third of the lease, at least 30"""
        lease = self.status.get("lease")
        lease = lease if isinstance(lease, (int, float)) and not isinstance(lease, bool) and lease > 0 else LEASE
        return max(30.0, lease / 3)

    def _catch_up(self, upto: Optional[int] = None) -> None:
        """the items after the cursor that the app's wakes brought and that did not get here (kit.app.recent), as one
        wake with reason "catchup": up to `upto` (the `after` of the wake that showed the gap, which is given after
        it), else up to the status's sent; what the app heard later comes with a wake, when the app sends it. (A
        WuxianKit without sent: all it heard.)"""
        upto = self._sent if upto is None else upto
        if upto is not None and upto <= self.cursor:
            return
        try:
            res = self.client.call("kit.app.recent", {"app": self.id, "since": self.cursor, "limit": CATCHUP})
        except WuxianError:
            self._joined = False                         # the status first, then this again
            raise
        res = res if isinstance(res, dict) else {}
        got = res.get("items") if isinstance(res.get("items"), list) else []
        ids = [(_id(it.get("id")), it) for it in got if isinstance(it, dict)]
        items = [it for n, it in ids if n is not None and n > self.cursor and (upto is None or n <= upto)]
        if items:
            newest = upto if upto is not None else max(_id(items[-1]["id"]), _id(res.get("next")) or 0)
            self._pending.append(dict(app=self.id, reason="catchup", items=items, skipped=0, after=self.cursor,
                                      next=newest))
            self.cursor = newest
        elif upto is not None:
            self.cursor = upto                           # the app keeps them no more: its wakes go on from there

    def _take(self, ev: dict) -> None:
        topic, data = ev.get("topic"), ev.get("data")
        if topic == "kit.ready":                         # a login or a UI reload: does the app still run?
            self._joined = False
            return
        if not isinstance(data, dict):
            return
        if topic == "kit.app" and data.get("app") == self.id:
            if data.get("on") is not False:              # opened again, or changed
                self._joined = False
            elif data.get("why") != "replaced":
                self.ended = str(data.get("why") or "stopped")
            return
        if topic == self.topic:
            self._wake(data)

    def _wake(self, data: dict) -> None:
        """one wake of the app: {..., items, skipped, after, next} covers the items after < id <= next. A gap before
        it (after is past the cursor) is caught up first, up to its after; it comes without the items given already,
        not at all when it only repeats. A wake that brings nothing (idle) comes as it is"""
        if self._held:                                   # in order, behind the ones that wait
            self._held.append(data)
            return
        nxt, after = _id(data.get("next")), _id(data.get("after"))
        if nxt is None or self.cursor is None:           # no cursor in it: as it is
            self._pending.append(data)
            return
        if after is not None and after > self.cursor:
            try:
                self._catch_up(after)
            except WuxianError as e:
                self._held.append(data)
                self._fail(e.reason)
                return
        items = data.get("items") if isinstance(data.get("items"), list) else []
        skipped = (_id(data.get("skipped")) or 0) > 0
        if (items or skipped) and nxt <= self.cursor:
            return                                       # given already
        cursor = self.cursor
        fresh = [it for it in items if not (isinstance(it, dict) and _id(it.get("id")) is not None
                                            and _id(it["id"]) <= cursor)]
        self.cursor = max(cursor, nxt)
        if items and not fresh and not skipped:
            return
        self._pending.append(data if len(fresh) == len(items) else dict(data, items=fresh))

    # ---- state and backoff

    def _ok(self) -> None:
        self._fails, self._retry_at = 0, 0.0
        if self.state[0] != "connected":
            self.state = ("connected", "")

    def _fail(self, reason: str) -> None:
        self._fails += 1
        self._retry_at = self.client.clock() + BACKOFF[min(self._fails, len(BACKOFF)) - 1]
        self.state = ("waiting", reason)


# ---- local extensions ------------------------------------------------------------------------------------------------

MANIFEST = "wuxian-extension.json"       # a local extension's manifest, in its folder
BAD_ARGS, NO_CAPABILITY, FAILED, BUSY, CANCELLED = -32602, -32601, 1, 2, 3     # a call's error codes
REASONS = {BAD_ARGS: "bad_args", NO_CAPABILITY: "no_capability", FAILED: "failed", BUSY: "busy",
           CANCELLED: "cancelled"}       # error.data.reason of each code
PARSE_ERROR, BAD_REQUEST = -32700, -32600
CALL_WAIT = 120.0                        # seconds ext.call waits for the workshop's answer
SHUTDOWN_GRACE = 3.0                     # seconds run() lets running calls finish after shutdown
HELD_EVENTS = 100                        # events kept until initialize is answered

# What a declaration may be and how a call's arguments are checked: the text 无限工坊 checks them with, copied from
# its src/wuxianworkshop/local/validate.py by scripts/sync_sdk_validate.py (a test compares the two). Change it there,
# never here.
# ---- shared: begin ---------------------------------------------------------------------------------------------------
CAP_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*\Z")     # say, pool.list: dotted parts, a letter first each
CAP_NAME_MAX = 48
TOOL_NAME_MAX = 48                     # the agent's tool wk_<extension id>_<name, dots as _>
SOURCE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,23}\Z")
OPTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")
ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,47}\Z")
KINDS = ("see", "keep", "point")       # Read, Store, Guide; say / do (proposals) come later
OPTION_KINDS = ("boolean", "text", "number", "choice")
TYPES = ("string", "number", "boolean", "table", "any")
TIMEOUT_DEFAULT, TIMEOUT_MOST = 30, 600      # seconds the workshop waits for a capability
TEXT_MOST = 2000                       # characters of a text option's value
TITLE_MAX = 48                         # bytes, as WuxianKit takes a title
CAP_NAME_HINT = "lowercase letters, digits, _ and dots, a letter first in every part (say, pool.list)"
SOURCE_NAME_HINT = "lowercase letters, digits and _, a letter first, at most 24 characters (said)"
OPTION_NAME_HINT = "lowercase letters, digits and _, a letter first, at most 32 characters"
ARG_NAME_HINT = "letters, digits and _, no digit first, at most 48 characters"
SPEC_HINT = ("string number boolean table any; a|b for either; words that are no type names are the values it may "
             "take; a trailing ? makes it optional")


def text_of(value, most=None):
    """a one-line text, stripped; None when it is no text (or longer than `most` bytes of UTF-8)"""
    if not isinstance(value, str) or not value.strip():
        return None
    value = " ".join(value.split())
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:             # half of a surrogate pair ("\ud800"): nothing that can be written or shown
        return None
    return value if most is None or size <= most else None


def finite(v):
    try:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    except OverflowError:                  # an integer no float holds
        return False


def tool_name(eid, name):
    """the agent's tool of the capability `name` of the extension `eid`"""
    return f"wk_{eid}_{name.replace('.', '_')}"


def cap_name_problem(name, eid=None):
    """why `name` is no capability name (None: it is one); with the extension's id, also that its tool name fits"""
    if not isinstance(name, str) or not CAP_NAME.match(name) or len(name) > CAP_NAME_MAX:
        return f"{name!r} is no capability name"
    if isinstance(eid, str) and len(tool_name(eid, name)) > TOOL_NAME_MAX:
        return (f"{name!r} is too long a capability name: its tool {tool_name(eid, name)} has more than {TOOL_NAME_MAX} "
                "characters")
    return None


def parse_spec(text):
    """an argument's spec, as WuxianKit writes them: "string", "number", "boolean", "table" (a JSON object or array) or
    "any", several of those ("string|number"), or else the strings it may be ("fast|slow"); a "?" after it makes it
    optional. -> {text, optional, types: [...]} or {text, optional, enum: [...]}; ValueError for a bad spec"""
    if not isinstance(text, str) or not text.strip() or text == "?":
        raise ValueError(f"an argument's spec is a string such as \"string\" or \"number?\", not {text!r}")
    optional = text.endswith("?")
    parts = (text[:-1] if optional else text).split("|")
    if any(not p or p.strip() != p for p in parts):
        raise ValueError(f"{text!r}: the parts between | are names, without spaces, none empty")
    if all(p in TYPES for p in parts):
        return {"text": text, "optional": optional, "types": parts}
    return {"text": text, "optional": optional, "enum": parts}


def arg_problem(spec):
    """why an argument's spec cannot be read (None: it can), and a warning (or None)"""
    try:
        parsed = parse_spec(spec)
    except ValueError as e:
        return str(e), None
    if "enum" in parsed and any(p in TYPES for p in parsed["enum"]):
        return None, (f"{spec!r} mixes types and words: it is read as an enumeration of the strings "
                      f"{', '.join(parsed['enum'])}")
    return None, None


def json_type(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (dict, list)):
        return "table"
    return type(v).__name__


def args_text(specs):
    return ", ".join(f"{n} ({s['text']})" for n, s in sorted(specs.items())) or "no arguments"


def args_problem(cap_id, specs, args):
    """what a call brought, checked against the capability's parsed specs ({name: parse_spec(...)}) the way WuxianKit
    checks them -> (a copy of the declared ones that came, None), or (None, why not): a missing one, a wrong type, a
    string not among the choices, one that is not declared. null counts as left out"""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None, f"{cap_id}: args is a JSON object"
    out = {}
    for name, spec in specs.items():
        v = args.get(name)
        if v is None:
            if not spec["optional"]:
                return None, f"{cap_id}: {name} is missing ({spec['text']})"
            continue
        got = json_type(v)
        if "types" in spec:
            if "any" not in spec["types"] and got not in spec["types"]:
                return None, f"{cap_id}: {name} is a {' or '.join(spec['types'])}, not a {got}"
        elif not isinstance(v, str) or v not in spec["enum"]:
            return None, (f"{cap_id}: {name} is one of {'|'.join(spec['enum'])}, not "
                          f"{json.dumps(v, ensure_ascii=False, default=str)}")
        out[name] = v
    for name, v in args.items():
        if name not in specs and v is not None:
            return None, f"{cap_id}: no argument {name} (it takes {args_text(specs)})"
    return out, None


def timeout_problem(timeout):
    """why `timeout` is not the seconds a capability may take (None: it is)"""
    if not finite(timeout) or not 0 < timeout <= TIMEOUT_MOST:
        return f"timeout is seconds, more than 0 and at most {TIMEOUT_MOST}"
    return None


DEFAULTS = {"boolean": lambda o: False, "text": lambda o: "", "number": lambda o: o.get("min", 0),
            "choice": lambda o: o["choices"][0]["value"] if o.get("choices") else None}


def value_problem(opt, value):
    """why value is not one the option takes (None: it is); opt = {kind, min?, max?, choices?: [{value}]}"""
    kind = opt["kind"]
    if kind == "boolean":
        return None if isinstance(value, bool) else "true or false"
    if kind == "text":
        if not isinstance(value, str):
            return "a text"
        return None if len(value) <= TEXT_MOST else f"at most {TEXT_MOST} characters"
    if kind == "number":
        if not finite(value):
            return "a number"
        if "min" in opt and value < opt["min"] or "max" in opt and value > opt["max"]:
            return f"a number from {opt.get('min', '-inf')} to {opt.get('max', 'inf')}"
        return None
    values = [c["value"] for c in opt.get("choices") or []]
    return None if value in values and not isinstance(value, bool) else "one of " + ", ".join(map(repr, values))


# --- the description: initialize's answer. Every finding is a problem {level: "error" | "warning", where, message, hint}

PROTOCOL = 1                           # the protocol version a description is written in
LATER_KINDS = ("say", "do")            # proposals: a later step


def problem(level, where, message, hint=""):
    return dict(level=level, where=where, message=message, hint=hint)


def errors_in(problems):
    return [p for p in problems if p["level"] == "error"]


def check_description(eid, answer):
    """(description or None, problems) of an extension's answer to initialize: {capabilities: [{id, name, kind,
    title, doc, args, timeout}], sources: [{name, title, doc}], options: [opt]}"""
    out = []
    where = "initialize"
    if not isinstance(answer, dict):
        return None, [problem("error", where, "initialize must answer a JSON object",
                              "{protocol: 1, capabilities: [...], sources: [...], options: [...]}")]
    if answer.get("protocol") != PROTOCOL or isinstance(answer.get("protocol"), bool):
        out.append(problem("error", f"{where}: protocol", f"protocol {answer.get('protocol')!r}: this 无限工坊 speaks 1",
                           "answer protocol 1 (the Python library does)"))
    caps, names = [], set()
    raw_caps = answer.get("capabilities", [])
    if not isinstance(raw_caps, list):
        out.append(problem("error", f"{where}: capabilities", "capabilities is a list", "[{name, kind, title, doc, args}]"))
        raw_caps = []
    for i, c in enumerate(raw_caps):
        cap = check_capability(eid, c, i, names, out)
        if cap is not None:
            caps.append(cap)
            names.add(cap["name"])
    sources, snames = [], set()
    raw_sources = answer.get("sources", [])
    if not isinstance(raw_sources, list):
        out.append(problem("error", f"{where}: sources", "sources is a list", "[{name, title, doc}]"))
        raw_sources = []
    for i, s in enumerate(raw_sources):
        src = check_source(eid, s, i, snames, out)
        if src is not None:
            sources.append(src)
            snames.add(src["name"])
    options, onames = [], set()
    raw_opts = answer.get("options", [])
    if not isinstance(raw_opts, list):
        out.append(problem("error", f"{where}: options", "options is a list", "[{name, kind, title, default}]"))
        raw_opts = []
    for i, o in enumerate(raw_opts):
        opt = check_option(o, i, onames, out)
        if opt is not None:
            options.append(opt)
            onames.add(opt["name"])
    desc = dict(capabilities=caps, sources=sources, options=options)
    return (None if errors_in(out) else desc), out


def check_capability(eid, c, i, names, out):
    at = f"capability {i + 1}"
    if not isinstance(c, dict):
        out.append(problem("error", at, "a capability is a JSON object", "{name, kind, title, doc, args}"))
        return None
    name = c.get("name")
    why = cap_name_problem(name, eid)
    if why:
        out.append(problem("error", at, why, CAP_NAME_HINT + f"; its tool wk_{eid}_<name> has at most {TOOL_NAME_MAX} "
                                                              "characters"))
        return None
    at = f"capability {eid}.{name}"
    if name in names:
        out.append(problem("error", at, "declared twice", "one entry per capability"))
        return None
    same = sorted(n for n in names if tool_name(eid, n) == tool_name(eid, name))
    if same:
        out.append(problem("error", at, f"its tool {tool_name(eid, name)} is also {eid}.{same[0]}'s",
                           "a tool's name has _ for the dots, so a.b and a_b are one tool: rename one of them"))
        return None
    kind = c.get("kind")
    if kind in LATER_KINDS:
        out.append(problem("error", at, f"kind {kind} (bad_capability): say and do are proposals the player confirms, "
                                        "not available to local extensions yet",
                           "see: reads; keep: stores on this computer; point: shows or tells the player something"))
        return None
    if kind not in KINDS:
        out.append(problem("error", at, f"kind {kind!r} (bad_capability)",
                           "see (reads, changes nothing), keep (stores on this computer) or point (shows or tells the "
                           "player something)"))
        return None
    title = text_of(c.get("title") if c.get("title") is not None else name, TITLE_MAX)
    if title is None:
        out.append(problem("error", at, "its title is some text of at most 48 bytes", "\"说一句\""))
        return None
    doc = c.get("doc") if isinstance(c.get("doc"), str) else ""
    if not doc.strip():
        out.append(problem("warning", at, "it has no doc",
                           "the agent picks tools by their docs: say what it does, what it answers, when to use it"))
    args = c.get("args") or {}
    if not isinstance(args, dict):
        out.append(problem("error", f"{at}: args", "args is an object, name -> spec", '{"text": "string", "n": "number?"}'))
        return None
    for aname, spec in args.items():
        if not ARG_NAME.match(aname):
            out.append(problem("error", f"{at}: args.{aname}", "not an argument name", ARG_NAME_HINT))
            return None
        bad, warn = arg_problem(spec)
        if bad:
            out.append(problem("error", f"{at}: args.{aname}", bad, SPEC_HINT))
            return None
        if warn:
            out.append(problem("warning", f"{at}: args.{aname}", warn, "write either types or words"))
    timeout = c.get("timeout", TIMEOUT_DEFAULT)
    if timeout is None:
        timeout = TIMEOUT_DEFAULT
    why = timeout_problem(timeout)
    if why:
        out.append(problem("error", f"{at}: timeout", why,
                           "answer within 30 s; split long work into a capability that starts it and a source that "
                           "tells when it is done"))
        return None
    return dict(id=f"{eid}.{name}", name=name, kind=kind, title=title, doc=doc.strip(), args=dict(args),
                timeout=timeout)


def check_source(eid, s, i, names, out):
    at = f"source {i + 1}"
    if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not SOURCE_NAME.match(s["name"]):
        out.append(problem("error", at, f"{(s or {}).get('name') if isinstance(s, dict) else s!r} is no source name",
                           SOURCE_NAME_HINT))
        return None
    at = f"source {eid}:{s['name']}"
    if s["name"] in names:
        out.append(problem("error", at, "declared twice", "one entry per source"))
        return None
    title = text_of(s.get("title") if s.get("title") is not None else s["name"], TITLE_MAX)
    if title is None:
        out.append(problem("error", at, "its title is some text of at most 48 bytes", "\"说完一句\""))
        return None
    doc = s.get("doc") if isinstance(s.get("doc"), str) else ""
    if not doc.strip():
        out.append(problem("warning", at, "it has no doc", "say what an item of it means and what its data holds"))
    return dict(name=s["name"], title=title, doc=doc.strip())


def check_option(o, i, names, out):
    at = f"option {i + 1}"
    if not isinstance(o, dict) or not isinstance(o.get("name"), str) or not OPTION_NAME.match(o["name"]):
        out.append(problem("error", at, "an option needs a name", OPTION_NAME_HINT))
        return None
    name = o["name"]
    at = f"option {name}"
    if name in names:
        out.append(problem("error", at, "declared twice", "one entry per option"))
        return None
    kind = o.get("kind", "boolean")
    if kind not in OPTION_KINDS:
        out.append(problem("error", at, f"kind {kind!r}", "boolean, text, number or choice"))
        return None
    title = text_of(o.get("title") if o.get("title") is not None else name, TITLE_MAX)
    if title is None:
        out.append(problem("error", at, "its title is some text of at most 48 bytes", "\"音量\""))
        return None
    opt = dict(name=name, kind=kind, title=title)
    if isinstance(o.get("hint"), str) and o["hint"].strip():
        opt["hint"] = o["hint"].strip()
    if kind == "number":
        for k in ("min", "max", "step"):
            if o.get(k) is not None:
                if not finite(o[k]):
                    out.append(problem("error", f"{at}: {k}", f"{k} is a number", "such as 0, 100, 1"))
                    return None
                opt[k] = o[k]
        if "min" in opt and "max" in opt and opt["min"] > opt["max"]:
            out.append(problem("error", at, "min is more than max", "swap them"))
            return None
        if isinstance(o.get("unit"), str) and o["unit"].strip():
            opt["unit"] = o["unit"].strip()
    if kind == "choice":
        choices = o.get("choices")
        if not isinstance(choices, list) or not choices:
            out.append(problem("error", f"{at}: choices", "a choice option lists its choices",
                               '[{"value": "fast", "label": "快"}, {"value": "slow", "label": "慢"}]'))
            return None
        kept = []
        for ch in choices:
            if not isinstance(ch, dict) or not (isinstance(ch.get("value"), str) or finite(ch.get("value"))):
                out.append(problem("error", f"{at}: choices", "each choice is {value, label}", "value a text or a number"))
                return None
            kept.append(dict(value=ch["value"], label=text_of(ch.get("label"), TITLE_MAX) or str(ch["value"])))
        opt["choices"] = kept
    default = o.get("default", DEFAULTS[kind](opt))
    why = value_problem(opt, default)
    if why:
        out.append(problem("error", f"{at}: default", why, "the default must be a value the option takes"))
        return None
    opt["default"] = default
    return opt
# ---- shared: end -----------------------------------------------------------------------------------------------------


class ExtensionError(Exception):
    """raise ExtensionError("why") in a capability: the call fails with code 1 (failed) and the message, which the agent
    reads, so say what went wrong and what to do. ExtensionError(message, BAD_ARGS) says the arguments were wrong.
    ext.call raises it when the capability it called failed: code is that error's code (-32602 bad arguments, -32601
    no such capability, 1 failed, 2 busy, 3 cancelled), reason the workshop's word for it (error.data.reason: timeout,
    down, starting, failed, bad_args, no_capability, busy, paused, cancelled, not_allowed, needs_approval, loop), cap
    the capability's id"""

    def __init__(self, message: str, code: int = FAILED, cap: Optional[str] = None, reason: Optional[str] = None):
        super().__init__(message)
        self.message, self.code, self.cap = str(message), code, cap
        self.reason = reason or REASONS.get(code, "failed")

    def __repr__(self):
        return f"ExtensionError({self.message!r}, {self.code!r}, {self.cap!r}, {self.reason!r})"


def check_args(cap_id: str, specs: dict, args) -> dict:
    """what a call brought, checked against the capability's parsed specs ({name: parse_spec(...)}) the way WuxianKit
    checks them: a copy of the declared ones that came (null counts as left out). ExtensionError code -32602 for a
    missing one, a wrong type, a string not among the choices, or one that is not declared"""
    out, why = args_problem(cap_id, specs, args)
    if why:
        raise ExtensionError(why, BAD_ARGS)
    return out


def _first_paragraph(doc: Optional[str]) -> str:
    return " ".join((inspect.cleandoc(doc or "").split("\n\n") or [""])[0].split())


def _declared(problems: list) -> None:
    """a declaration's problems, as the shared checks found them: ValueError for the first error (warnings pass: the
    workshop's extension_check and ExtensionTest.check_description show them)"""
    for p in errors_in(problems):
        raise ValueError(f"{p['where']}: {p['message']}" + (f" ({p['hint']})" if p["hint"] else ""))


def _line(msg: dict) -> bytes:
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _error_of(code: int, message: str, reason: Optional[str] = None) -> dict:
    return {"code": code, "message": message, "data": {"reason": reason or REASONS.get(code, "failed")}}


def _raise(err, cap: Optional[str] = None):
    """an answer's error {code, message, data: {reason}} as an ExtensionError"""
    if not isinstance(err, dict):
        err = {"message": str(err)}
    code = err.get("code")
    code = code if isinstance(code, int) and not isinstance(code, bool) else FAILED
    data = err.get("data")
    reason = data.get("reason") if isinstance(data, dict) and isinstance(data.get("reason"), str) else None
    raise ExtensionError(str(err.get("message") or "failed"), code, cap, reason)


class Option:
    """a setting the player changes in 无限工坊's App (the extension's row on the Extensions page). get() is its value now:
    the workshop sends the values at initialize and again whenever the player changes one"""

    def __init__(self, ext: "Extension", name: str, desc: dict):
        self.ext, self.name, self.desc = ext, name, desc

    @property
    def default(self):
        return self.desc["default"]

    def get(self):
        with self.ext._lock:
            return self.ext._values.get(self.name, self.desc["default"])

    value = property(get)

    def __repr__(self):
        return f"Option({self.name!r}, {self.get()!r})"


class Source:
    """a stream of items that reactions hear as "<extension>:<name>" (an app's hear takes the game's sources only: a
    reaction, the app's own in the manifest or one the agent set, is what acts on a local source); each item's kind is
    "<extension>.<name>". emit(data) sends one (data: a JSON object), from any thread"""

    def __init__(self, ext: "Extension", name: str, desc: dict):
        self.ext, self.name, self.desc = ext, name, desc

    def emit(self, data: dict) -> None:
        self.ext._emit(self.name, data)

    def __repr__(self):
        return f"Source({self.name!r})"


class Extension:
    """a local extension: declare options, sources and capabilities, then run(). Capabilities run in worker threads, at
    most `workers` at once (a call beyond that is refused with 2 busy), so a slow one does not hold up the others;
    guard shared state with a lock of your own. folder: where wuxian-extension.json is (default: the current folder,
    which is the extension's folder when the workshop starts it). A declaration the workshop would refuse (a bad name,
    spec, timeout, option) raises ValueError at once: the checks are the workshop's own.

    The protocol, version 1 (what run() speaks on stdin / stdout; stderr is the log). JSON-RPC 2.0, UTF-8, one object
    per line.
      workshop -> extension, requests:
        initialize {protocol: {min, max}, workshop, id, options: {name: value}}
          -> {protocol: 1, capabilities: [{name, kind, title, doc, args, timeout?}], sources: [{name, title, doc}],
              options: [{name, kind, title, default, hint?, choices?, min?, max?, step?, unit?}]}
          (1 is not within min..max: an error, and the extension exits with 1)
        call {name, args, caller: {by, id?, app?, chain?}, deadline_ms?} -> any JSON | error
          caller.by: "agent" (a tool call), "extension" (id called it with ext.call), "workshop" (a reaction, or the
          note of a wake of the app `app`), "player" (the player clicked a control of the app `app`); caller.chain: how
          the call came about, oldest first ("reaction:<id>", "wake:<app>", "ext:<id>"; the workshop refuses a fifth
          hop with the reason loop); deadline_ms: how long the workshop still waits
        ping {} -> {} (within 5 s, or the workshop takes the extension for hung; run() answers it by itself)
        shutdown {} -> {}, then the extension exits
      workshop -> extension, notifications:
        options {options: {name: value}} (all of them, after the player changed one)
        cancel {id} (the workshop no longer waits for the call `id`: ext.cancelled() turns True; its answer is dropped)
      extension -> workshop, notification: event {source: name, data: {...}}
      extension -> workshop, request: call {cap: "<capability id>", args, chain?} -> its result | error
        (chain: caller.chain of the call being served, unchanged; ext.call sends it by itself)
      an error: {code, message, data: {reason}}; code -32602 bad_args, -32601 no_capability, 1 failed, 2 busy,
        3 cancelled; from the workshop, reason is one of timeout, down, starting, failed, bad_args, no_capability, busy,
        paused, cancelled, not_allowed, needs_approval, loop"""

    def __init__(self, id: Optional[str] = None, *, folder: Union[str, os.PathLike, None] = None, workers: int = 8):
        self.folder = Path(folder) if folder is not None else Path.cwd()
        try:
            manifest = json.loads((self.folder / MANIFEST).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            manifest = None
        self.manifest: dict = manifest if isinstance(manifest, dict) else {}
        mid = self.manifest.get("id")
        self.id: Optional[str] = id or os.environ.get("WUXIAN_EXTENSION") or (mid if isinstance(mid, str) else None)
        title = self.manifest.get("title")
        self.title: Optional[str] = title if isinstance(title, str) else self.id
        self.workshop: Optional[str] = None              # the workshop's version, from initialize
        self.workers = max(1, int(workers))
        self._caps: dict = {}
        self._sources: dict = {}
        self._options: dict = {}
        self._values: dict = {}
        self._on_options: list = []
        self._lock = threading.RLock()                   # values, pending calls, running jobs
        self._wlock = threading.Lock()                   # one line at a time on stdout
        self._write: Optional[Callable[[bytes], None]] = None
        self._ready = False                              # initialize answered
        self._held: list = []                            # events before that
        self._setup: Optional[Callable[[], Any]] = None
        self._set_up = False
        self._reader: Optional[int] = None               # the thread that reads stdin (ext.call there would hang)
        self._ids = itertools.count(1)
        self._pending: dict = {}                         # ext.call id -> [threading.Event, answer, the job it is for]
        self._slots = threading.BoundedSemaphore(self.workers)
        self._running: dict = {}                         # the workshop's call id -> its job {id, cancel, deadline, caller}
        self._jobs = 0
        self._jobs_cond = threading.Condition()
        self._local = threading.local()

    # ---- declarations

    def option(self, name: str, *, kind: str = "boolean", default=None, title: Optional[str] = None,
               hint: Optional[str] = None, choices=None, min: Optional[float] = None, max: Optional[float] = None,
               step: Optional[float] = None, unit: Optional[str] = None) -> Option:
        """a setting the player changes in 无限工坊: kind boolean, text, number (min, max, step, unit) or choice (choices:
        a list of values, of (value, label) pairs or of {value, label}, or a {value: label} dict). default: False, "",
        min or 0, or the first choice when not given"""
        o: dict = {"name": name, "kind": kind}
        for k, v in (("title", title), ("hint", hint), ("min", min), ("max", max), ("step", step), ("unit", unit),
                     ("default", default)):
            if v is not None:
                o[k] = v
        if choices is not None:
            if isinstance(choices, dict):
                choices = list(choices.items())
            norm = []
            for c in choices:
                if isinstance(c, dict):
                    norm.append({"value": c.get("value"), "label": str(c.get("label", c.get("value")))})
                elif isinstance(c, (tuple, list)) and len(c) == 2:
                    norm.append({"value": c[0], "label": str(c[1])})
                else:
                    norm.append({"value": c, "label": str(c)})
            o["choices"] = norm
        out: list = []
        desc = check_option(o, len(self._options), set(self._options), out)
        _declared(out)
        opt = Option(self, name, desc)
        self._options[name] = opt
        return opt

    def source(self, name: str, *, title: Optional[str] = None, doc: str = "") -> Source:
        """a stream of items reactions hear as "<extension>:<name>". doc: for the agent, what an item means and its
        fields"""
        out: list = []
        desc = check_source(self.id or "?", {"name": name, "title": title, "doc": doc}, len(self._sources),
                            set(self._sources), out)
        _declared(out)
        src = Source(self, name, desc)
        self._sources[name] = src
        return src

    def see(self, name=None, *, title: Optional[str] = None, doc: Optional[str] = None, args: Optional[dict] = None,
            timeout: Optional[float] = None):
        """@ext.see("name", title=..., doc=..., args={...}): a Read capability, which answers at once and changes
        nothing. The function gets the arguments by name (optional ones left out when not given: give them a default)
        and returns any JSON. doc (default: the function's docstring): one English sentence for the agent, starting
        with a verb. timeout: seconds the workshop waits (default 30, at most 600)"""
        return self._decorator("see", name, title, doc, args, timeout)

    def keep(self, name=None, *, title: Optional[str] = None, doc: Optional[str] = None, args: Optional[dict] = None,
             timeout: Optional[float] = None):
        """@ext.keep(...): a Store capability, which keeps or deletes data on this computer (as ext.see)"""
        return self._decorator("keep", name, title, doc, args, timeout)

    def point(self, name=None, *, title: Optional[str] = None, doc: Optional[str] = None, args: Optional[dict] = None,
              timeout: Optional[float] = None):
        """@ext.point(...): a Guide capability, which shows or plays the player something and changes nothing else (as
        ext.see)"""
        return self._decorator("point", name, title, doc, args, timeout)

    def _decorator(self, kind, name, title, doc, args, timeout):
        if callable(name):                               # @ext.see without parentheses
            fn = name
            self._add(kind, fn.__name__, fn, title, doc, args, timeout)
            return fn

        def deco(fn):
            self._add(kind, name or fn.__name__, fn, title, doc, args, timeout)
            return fn
        return deco

    def _add(self, kind, name, fn, title, doc, args, timeout):
        if args is not None and not isinstance(args, dict):
            raise ValueError(f"capability {name!r}: args is a dict, name -> spec")
        args = dict(args or {})
        for a in args:
            if not isinstance(a, str):
                raise ValueError(f"capability {name!r}: the argument name {a!r} is not a string")
        told = {"name": name, "kind": kind, "title": title, "args": args,
                "doc": doc if doc is not None else _first_paragraph(fn.__doc__)}
        if timeout is not None:
            told["timeout"] = timeout
        out: list = []
        cap = check_capability(self.id or "?", told, len(self._caps), set(self._caps), out)
        _declared(out)
        specs = {a: parse_spec(text) for a, text in args.items()}
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = None
        if params is not None:                           # the function and args must agree: found now, not at a call
            named = {p.name: p for p in params.values()
                     if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
            more = any(p.kind == p.VAR_KEYWORD for p in params.values())
            for a, spec in specs.items():
                if a not in named and not more:
                    raise TypeError(f"capability {name}: {fn.__name__}() has no parameter {a}")
                if spec["optional"] and a in named and named[a].default is inspect.Parameter.empty:
                    raise TypeError(f"capability {name}: {a} is optional, so {fn.__name__}() needs a default for it")
            for p in params.values():
                if p.default is inspect.Parameter.empty and p.name not in specs and p.kind in (
                        p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY, p.POSITIONAL_ONLY):
                    raise TypeError(f"capability {name}: {fn.__name__}() needs {p.name}, which args does not declare")
        desc = {"name": name, "kind": kind, "title": cap["title"], "doc": cap["doc"], "args": args}
        if timeout is not None:
            desc["timeout"] = timeout
        self._caps[name] = {"desc": desc, "specs": specs, "fn": fn}

    def on_options(self, fn: Callable[[dict], Any]) -> Callable[[dict], Any]:
        """fn(values) after the player changed the options (values: all of them, by name); usable as a decorator.
        Not called at initialize: read them with opt.get() in setup"""
        self._on_options.append(fn)
        return fn

    def describe(self) -> dict:
        """what initialize answers: {protocol, capabilities, sources, options}"""
        return {"protocol": PROTOCOL,
                "capabilities": [dict(c["desc"], args=dict(c["desc"]["args"])) for c in self._caps.values()],
                "sources": [dict(s.desc) for s in self._sources.values()],
                "options": [dict(o.desc) for o in self._options.values()]}

    @property
    def options(self) -> dict:
        """every option's value now, by name"""
        with self._lock:
            return {n: self._values.get(n, o.desc["default"]) for n, o in self._options.items()}

    @property
    def data(self) -> Path:
        """the folder for what this extension keeps (what it learned, a cache, a database), made when it is asked for.
        The workshop names it in WUXIAN_EXTENSION_DATA: <its home>\\state\\extensions\\<id>\\data, outside the
        extension's folder, so an update of the extension (which replaces that folder) leaves it alone and a package
        of the extension does not carry it. Without the variable (run by hand, a test) it is the same place, worked out
        from WUXIAN_HOME as daemon_json_path does; an extension with no id has none (ValueError). A test that must
        not write there sets WUXIAN_EXTENSION_DATA to a folder of its own"""
        given = os.environ.get("WUXIAN_EXTENSION_DATA")
        if given:
            path = Path(given)
        elif isinstance(self.id, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,23}", self.id):
            path = daemon_json_path().parent / "extensions" / self.id / "data"
        else:
            raise ValueError(f"data: the extension has no id to keep a folder under ({self.id!r}): run it in its "
                             f"folder (wuxian-extension.json), or set WUXIAN_EXTENSION or WUXIAN_EXTENSION_DATA")
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- in a capability (on the thread that runs it)

    @property
    def caller(self) -> Optional[dict]:
        """who called this capability: {by, id?, app?, chain?}; None outside one. by: "agent" (a tool call),
        "extension" (the extension `id`, with ext.call), "workshop" (a reaction, or a wake's note for the app `app`),
        "player" (a control of the app `app` that the player clicked). chain: how the call came about, oldest first"""
        job = getattr(self._local, "job", None)
        return job["caller"] if job else None

    def cancelled(self) -> bool:
        """True once the workshop no longer waits for this call (it took too long there, or whoever asked is gone):
        stop the work and return. Whatever the capability then returns, the call answers 3 cancelled. Ask it between
        the steps of long work; False outside a capability"""
        job = getattr(self._local, "job", None)
        return bool(job and job["cancel"].is_set())

    def remaining(self) -> Optional[float]:
        """seconds the workshop still waits for this call (0.0 once the time is up); None when it named no deadline,
        and outside a capability"""
        job = getattr(self._local, "job", None)
        if not job or job["deadline"] is None:
            return None
        return max(0.0, job["deadline"] - time.monotonic())

    # ---- calling other capabilities

    def call(self, cap: str, args: Optional[dict] = None, *, timeout: float = CALL_WAIT,
             chain: Optional[list] = None) -> Any:
        """another capability, through the workshop: one in the game ("sense.bags") or of a local extension
        ("voice.say"); Read, Store and Guide only. Waits for its result; ExtensionError when it failed: code, message
        and reason as the workshop gave them (see ExtensionError), or the reason "timeout" after `timeout` seconds,
        "cancelled" when the call this capability serves was cancelled meanwhile, "starting" before initialize is
        answered, "down" outside a running extension. Inside a capability it passes on the chain of the call being
        served (caller.chain), which is how the workshop stops loops; from a thread of your own that works for a call,
        pass chain=ext.caller.get("chain") yourself. Call it from a capability, a thread of your own or on_options,
        not from setup"""
        if self._write is None:
            raise ExtensionError("ext.call works while the extension runs", FAILED, cap, "down")
        if not self._ready:
            raise ExtensionError("ext.call works after initialize is answered (not in setup)", FAILED, cap, "starting")
        if threading.get_ident() == self._reader:
            raise ExtensionError("ext.call cannot run on the thread that reads the workshop's messages", FAILED, cap)
        job = getattr(self._local, "job", None)
        if chain is None and job and job["caller"]:
            chain = job["caller"].get("chain")
        params: dict = {"cap": cap, "args": args if args is not None else {}}
        if isinstance(chain, (list, tuple)) and chain:
            params["chain"] = list(chain)
        with self._lock:
            mid = next(self._ids)
            slot = [threading.Event(), None, job]
            self._pending[mid] = slot
        try:
            self._send({"jsonrpc": "2.0", "id": mid, "method": "call", "params": params})
            if not slot[0].wait(timeout):
                raise ExtensionError(f"{cap}: no answer from the workshop in {timeout:g} s", FAILED, cap, "timeout")
        finally:
            with self._lock:
                self._pending.pop(mid, None)
        answer = slot[1] or {}
        if answer.get("error") is not None:
            _raise(answer["error"], cap)
        return answer.get("result")

    # ---- running

    def run(self, setup: Optional[Callable[[], Any]] = None) -> None:
        """answers the workshop on stdin / stdout until it says shutdown or closes stdin, then returns (put clean-up
        after it). setup() runs once, at initialize, before the answer: load models, open files. First thing, run()
        keeps a copy of file descriptor 1 for the protocol and points 1 at stderr (os.dup / os.dup2, Windows too), and
        sys.stdout at sys.stderr: print(), C libraries writing to fd 1 and child processes all go to the log. Output
        before run() (a library imported at the top of the module) cannot be caught: import such libraries in setup.
        A setup that raises, or a workshop that speaks another protocol, fails initialize and the process exits with 1"""
        proto_in, proto_out = _take_stdio()

        def write(line: bytes) -> None:
            proto_out.write(line)
            proto_out.flush()

        self._attach(write, setup)
        if self._serve(proto_in, write):
            sys.exit(1)

    def _attach(self, write: Callable[[bytes], None], setup: Optional[Callable[[], Any]] = None) -> None:
        with self._lock:
            self._write, self._ready, self._held, self._set_up = write, False, [], False
            if setup is not None:
                self._setup = setup

    def _serve(self, lines: Iterable[bytes], write: Callable[[bytes], None], grace: float = SHUTDOWN_GRACE) -> bool:
        """answer the workshop's lines (attached to `write` before) until shutdown or their end; True when initialize
        failed. What still runs then is told to stop (cancel) and given `grace` seconds"""
        self._reader = threading.get_ident()
        failed = False
        try:
            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as e:
                    self._error(None, PARSE_ERROR, f"not a JSON line: {e}")
                    continue
                end = self._receive(msg)
                if end:
                    failed = end == "failed"
                    break
        except KeyboardInterrupt:
            pass
        finally:
            with self._lock:
                running = list(self._running)
            for rid in running:
                self._cancel(rid)
            self._settle(grace)
            self._detach(write)
            self._reader = None
        return failed

    def _detach(self, write: Optional[Callable[[bytes], None]] = None) -> None:
        with self._lock:
            if write is not None and self._write is not write:
                return                                   # attached to another one meanwhile
            self._write, self._ready = None, False
            pending, self._pending = self._pending, {}
        for slot in pending.values():                    # ext.call still waiting: the workshop is gone
            slot[1] = {"error": _error_of(FAILED, "the workshop stopped talking to this extension", "down")}
            slot[0].set()

    def _send(self, msg: dict) -> None:
        line = _line(msg)
        with self._wlock:
            write = self._write
            if write is None:
                return
            try:
                write(line)
            except (OSError, ValueError):                # the workshop closed the pipe
                pass

    def _error(self, mid, code: int, message: str, reason: Optional[str] = None) -> None:
        if code not in REASONS and code not in (PARSE_ERROR, BAD_REQUEST):
            code = FAILED                                # the protocol has these codes only
        self._send({"jsonrpc": "2.0", "id": mid, "error": _error_of(code, message, reason)})

    def _emit(self, source: str, data: dict) -> None:
        if not isinstance(data, dict):
            raise TypeError(f"source {source}: emit takes a dict (a JSON object)")
        msg = {"jsonrpc": "2.0", "method": "event", "params": {"source": source, "data": data}}
        json.dumps(msg, allow_nan=False)                 # not JSON: the emitter hears it now, not the workshop later
        with self._lock:
            if not self._ready:
                if len(self._held) < HELD_EVENTS:
                    self._held.append(msg)
                return
        self._send(msg)

    def _receive(self, msg) -> Optional[str]:
        """one message from the workshop; "stop" after shutdown, "failed" after a failed initialize"""
        if not isinstance(msg, dict):
            self._error(None, BAD_REQUEST, "a message is a JSON object")
            return None
        method, mid = msg.get("method"), msg.get("id")
        if method is None:                               # an answer to ext.call
            with self._lock:
                slot = self._pending.get(mid)
            if slot is not None:
                slot[1] = msg
                slot[0].set()
            return None
        params = msg.get("params")
        params = params if isinstance(params, dict) else {}
        if method == "initialize":
            return self._initialize(mid, params)
        if method == "call":
            self._call(mid, params)
        elif method == "cancel":
            self._cancel(params.get("id"))
        elif method == "ping":
            if "id" in msg:
                self._send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "options":
            self._set_values(params.get("options"))
            values = self.options
            for fn in list(self._on_options):
                self._spawn(self._run_hook, fn, values)
        elif method == "shutdown":
            self._send({"jsonrpc": "2.0", "id": mid, "result": {}})
            return "stop"
        elif "id" in msg:
            self._error(mid, NO_CAPABILITY, f"no method {method}")
        return None

    def _set_values(self, values) -> None:
        with self._lock:
            self._values = {n: v for n, v in (values or {}).items() if n in self._options} \
                if isinstance(values, dict) else {}

    def _initialize(self, mid, params: dict) -> Optional[str]:
        asked = params.get("protocol")
        low, high = (asked.get("min"), asked.get("max")) if isinstance(asked, dict) else (asked, asked)
        if not (finite(low) and finite(high) and low <= PROTOCOL <= high):
            self._error(mid, FAILED, f"this extension speaks protocol {PROTOCOL} (wuxiankit {__version__}), the workshop "
                                     f"{json.dumps(asked, default=str)}: update whichever is older")
            return "failed"
        if isinstance(params.get("id"), str) and params["id"]:
            self.id = params["id"]
        self.workshop = params.get("workshop")
        self._set_values(params.get("options"))
        if self._setup is not None and not self._set_up:
            try:
                self._setup()
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                self._error(mid, FAILED, f"setup failed: {type(e).__name__}: {e}")
                return "failed"
            self._set_up = True
        self._send({"jsonrpc": "2.0", "id": mid, "result": self.describe()})
        with self._lock:
            self._ready, held, self._held = True, self._held, []
        for m in held:
            self._send(m)
        return None

    def _call(self, mid, params: dict) -> None:
        name = params.get("name")
        cap = self._caps.get(name) if isinstance(name, str) else None
        if cap is None and isinstance(name, str) and self.id and name.startswith(self.id + "."):
            cap = self._caps.get(name[len(self.id) + 1:])
        if cap is None:
            self._error(mid, NO_CAPABILITY, f"no capability {self.id}.{name} (it has: "
                                            f"{', '.join(sorted(self._caps)) or 'none'})")
            return
        cid = f"{self.id}.{cap['desc']['name']}"
        args, why = args_problem(cid, cap["specs"], params.get("args"))
        if why:
            self._error(mid, BAD_ARGS, why)
            return
        if not self._slots.acquire(blocking=False):      # never wait in line: the workshop decides what to do
            self._error(mid, BUSY, f"{cid}: {self.id} runs {self.workers} calls already; try again in a moment")
            return
        caller, ms = params.get("caller"), params.get("deadline_ms")
        job = {"id": mid, "cancel": threading.Event(), "caller": caller if isinstance(caller, dict) else None,
               "deadline": time.monotonic() + max(0.0, ms) / 1000 if finite(ms) else None}
        with self._lock:
            self._running[mid] = job
        self._spawn(self._run_cap, job, cid, cap, args)

    def _cancel(self, rid) -> None:
        """the workshop no longer waits for the call `rid`: its capability can see it, an ext.call it waits in ends"""
        try:
            with self._lock:
                job = self._running.get(rid)
                if job is None:
                    return
                job["cancel"].set()
                waiting = [slot for slot in self._pending.values() if slot[2] is job]
        except TypeError:                                # an id that is no id
            return
        for slot in waiting:
            slot[1] = {"error": _error_of(CANCELLED, "the call this one was made for was cancelled")}
            slot[0].set()

    def _run_cap(self, job: dict, cid: str, cap: dict, args: dict) -> None:
        mid, error, result = job["id"], None, None
        self._local.job = job
        try:
            result = cap["fn"](**args)
        except ExtensionError as e:
            if e.cap is not None:                        # from an ext.call inside: this capability failed
                error = (FAILED, f"{e.cap}: {e.message}", None)
            else:
                error = (e.code, e.message, e.reason)
        except Exception as e:
            print(f"{cid} failed:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            error = (FAILED, f"{type(e).__name__}: {e}", None)
        finally:
            self._local.job = None
            with self._lock:
                if self._running.get(mid) is job:
                    del self._running[mid]
            self._slots.release()
        if job["cancel"].is_set():
            self._error(mid, CANCELLED, f"{cid}: cancelled")
        elif error is not None:
            self._error(mid, *error)
        else:
            try:
                self._send({"jsonrpc": "2.0", "id": mid, "result": result})
            except (TypeError, ValueError) as e:
                self._error(mid, FAILED, f"{cid}: the result is not JSON ({e})")

    @staticmethod
    def _run_hook(fn, values) -> None:
        try:
            fn(values)
        except Exception:
            print("on_options failed:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    def _spawn(self, fn, *args) -> None:
        with self._jobs_cond:
            self._jobs += 1

        def job():
            try:
                fn(*args)
            finally:
                with self._jobs_cond:
                    self._jobs -= 1
                    self._jobs_cond.notify_all()
        threading.Thread(target=job, daemon=True, name=f"wuxiankit-{fn.__name__}").start()

    def _settle(self, timeout: Optional[float] = None) -> bool:
        """True once no capability or on_options handler runs (False after timeout seconds)"""
        with self._jobs_cond:
            return self._jobs_cond.wait_for(lambda: self._jobs == 0, timeout)


def _take_stdio():
    """(protocol in, protocol out) as binary files: the process's stdin and stdout, which then point elsewhere (stdin to
    nothing, stdout to stderr) so that nothing else can read the workshop's lines or write into them"""
    try:
        sys.stdout.flush()
        out_fd, in_fd = os.dup(1), os.dup(0)
        os.dup2(2, 1)
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.close(null)
        proto_in, proto_out = os.fdopen(in_fd, "rb"), os.fdopen(out_fd, "wb", buffering=0)
    except (OSError, AttributeError, ValueError):        # no real descriptors (an embedded interpreter, a test)
        proto_in, proto_out = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr
    return proto_in, proto_out


class _Lines:
    """the lines one side writes to the other in memory: put(bytes), iterated until close()"""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()

    def put(self, line: bytes) -> None:
        self._q.put(line)

    def close(self) -> None:
        self._q.put(None)

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        line = self._q.get()
        if line is None:
            raise StopIteration
        return line


class ExtensionTest:
    """an Extension run the way the workshop runs it, for unit tests: no process, but the same peer: every message
    crosses as a line of JSON bytes, read by the loop run() reads stdin with. answers: what the extension's ext.call
    gets, by capability id, a value or fn(args) (raise ExtensionError in it for an error); a capability without one
    fails with -32601.

        t = ExtensionTest(main.ext, setup=main.setup, options={"volume": 50}, answers={"sense.bags": {"free": 3}})
        t.call("say", {"text": "hi"})                  # its result; ExtensionError(message, code) when it failed
        t.call("say", {"text": "hi"}, caller={"by": "player", "app": "companion"}, deadline_ms=3000)
        t.next_event("said")                           # {source, data}; t.events has them all
        t.calls                                        # [{cap, args, chain?}] the extension called
        t.set_options({"volume": 20})                  # waits for its on_options handlers
        rid = t.start("long")                          # a call that goes on: t.cancel(rid), then t.result(rid)
        t.check_description()                          # the problems the workshop would find in its description
        t.close()                                      # or: with ExtensionTest(...) as t:

    exited: None while the extension answers; 0 once its loop ended (shutdown, close), 1 after a failed initialize"""

    def __init__(self, ext: Extension, *, setup: Optional[Callable[[], Any]] = None, options: Optional[dict] = None,
                 answers: Optional[dict] = None, id: Optional[str] = None, timeout: float = 10.0):
        self.ext, self.timeout = ext, timeout
        self.id = id or ext.id or "test"
        self.options = dict(options or {})
        self.answers: dict = dict(answers or {})
        self.events: list = []                           # {source, data}, in order
        self.calls: list = []                            # {cap, args, chain?} the extension called
        self.described: Optional[dict] = None            # the initialize answer
        self.exited: Optional[int] = None
        self._next_event = 0
        self._answers: dict = {}
        self._read_all = False                           # every line the extension wrote was read
        self._cond = threading.Condition()
        self._ids = itertools.count(1)
        self._in = _Lines()                              # workshop -> extension
        self._out = _Lines()                             # extension -> workshop
        self._write = self._out.put
        ext._attach(self._write, setup)
        self._serving = threading.Thread(target=self._serve, daemon=True, name="wuxiankit-test-extension")
        self._thread = threading.Thread(target=self._workshop, daemon=True, name="wuxiankit-test-workshop")
        self._serving.start()
        self._thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- the two ends

    def _serve(self) -> None:
        failed = True
        try:
            failed = self.ext._serve(self._in, self._write, grace=0)
        finally:
            with self._cond:
                self.exited = 1 if failed else 0
                self._cond.notify_all()
            self._out.close()

    def _workshop(self) -> None:
        for line in self._out:
            msg = json.loads(line.decode("utf-8"))
            method = msg.get("method")
            if method is None:
                with self._cond:
                    self._answers[msg.get("id")] = msg
                    self._cond.notify_all()
            elif method == "event":
                with self._cond:
                    self.events.append(msg.get("params"))
                    self._cond.notify_all()
            elif method == "call":
                self._answer(msg)
        with self._cond:
            self._read_all = True
            self._cond.notify_all()

    def _answer(self, msg: dict) -> None:
        p = msg.get("params") or {}
        cap, args = p.get("cap"), p.get("args") or {}
        with self._cond:
            self.calls.append(dict({"cap": cap, "args": args}, **({"chain": p["chain"]} if "chain" in p else {})))
        out = {"jsonrpc": "2.0", "id": msg.get("id")}
        if cap not in self.answers:
            out["error"] = _error_of(NO_CAPABILITY, f"no capability {cap} (ExtensionTest: give its answer in answers= "
                                                    f"or with answer())")
        else:
            a = self.answers[cap]
            try:
                out["result"] = a(args) if callable(a) else a
            except ExtensionError as e:
                out["error"] = _error_of(e.code, e.message, e.reason)
        self._in.put(_line(out))

    def answer(self, cap: str, value) -> None:
        """what the extension's ext.call(cap) gets from now on: a value or fn(args)"""
        self.answers[cap] = value

    def _await(self, mid, what: str, timeout: Optional[float] = None) -> dict:
        wait = self.timeout if timeout is None else timeout
        with self._cond:
            self._cond.wait_for(lambda: mid in self._answers or self._read_all, wait)
            if mid in self._answers:
                return self._answers.pop(mid)
            if self._read_all:
                raise AssertionError(f"{what}: the extension ended (exit code {self.exited}) without an answer")
            raise AssertionError(f"{what}: no answer in {wait:g} s")

    # ---- requests

    def post(self, method: str, params: Optional[dict] = None) -> int:
        """one request as the workshop sends it, not waited for: its id (for result)"""
        mid = next(self._ids)
        self._in.put(_line({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}))
        return mid

    def send(self, method: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        """one request as the workshop sends it; the extension's raw answer {jsonrpc, id, result | error}"""
        return self._await(self.post(method, params), method, timeout)

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        """one notification as the workshop sends it"""
        self._in.put(_line({"jsonrpc": "2.0", "method": method, "params": params or {}}))

    def ping(self, timeout: Optional[float] = None) -> dict:
        """ping, as the workshop asks whether the extension still answers: {} (every line sent before was read by then)"""
        ans = self.send("ping", None, timeout)
        if "error" in ans:
            _raise(ans["error"])
        return ans.get("result")

    def initialize(self, options: Optional[dict] = None, protocol: Optional[dict] = None) -> dict:
        """initialize (call() does it first when it has not run): the extension's description; ExtensionError when
        setup failed or the protocol range (default {min: 1, max: 1}) leaves its version out"""
        if options is not None:
            self.options = dict(options)
        ans = self.send("initialize", {"protocol": protocol or {"min": PROTOCOL, "max": PROTOCOL}, "workshop": "test",
                                       "id": self.id, "options": self.options})
        if "error" in ans:
            _raise(ans["error"])
        self.described = ans["result"]
        return self.described

    def check_description(self) -> list:
        """the problems [{level: "error" | "warning", where, message, hint}] the workshop finds in what initialize
        answered (its extension_check lists the same); [] when there is none"""
        if self.described is None:
            self.initialize()
        return check_description(self.id, self.described)[1]

    def start(self, name: str, args: Optional[dict] = None, *, caller: Optional[dict] = None,
              deadline_ms: Optional[float] = None) -> int:
        """a call that is not waited for: its id, for cancel(id) and result(id)"""
        if self.described is None:
            self.initialize()
        if name.startswith(self.id + "."):
            name = name[len(self.id) + 1:]
        params = {"name": name, "args": args if args is not None else {}, "caller": caller or {"by": "agent"}}
        if deadline_ms is not None:
            params["deadline_ms"] = deadline_ms
        return self.post("call", params)

    def cancel(self, rid: int) -> None:
        """cancel {id}, as the workshop sends it once it no longer waits for that call; back when the extension read
        it. result(rid) then raises ExtensionError code 3 as soon as the capability returned"""
        self.notify("cancel", {"id": rid})
        self.ping()

    def result(self, rid: int, timeout: Optional[float] = None) -> Any:
        """what the call `rid` answered: its result; ExtensionError(message, code) when it failed"""
        ans = self._await(rid, f"call {rid}", timeout)
        if "error" in ans:
            _raise(ans["error"])
        return ans.get("result")

    def call(self, name: str, args: Optional[dict] = None, *, caller: Optional[dict] = None,
             timeout: Optional[float] = None, deadline_ms: Optional[float] = None) -> Any:
        """a capability by its name (or full id): its result; ExtensionError(message, code) when it failed. caller:
        {by: "agent" | "extension" | "workshop" | "player", id?, app?, chain?} (default {by: "agent"})"""
        return self.result(self.start(name, args, caller=caller, deadline_ms=deadline_ms), timeout)

    def set_options(self, options: dict) -> None:
        """the player changed the options (all of them): the options notification; waits for on_options"""
        if self.described is None:
            self.initialize()
        self.options = dict(options)
        self.notify("options", {"options": self.options})
        self.settle()

    def next_event(self, source: Optional[str] = None, timeout: Optional[float] = None) -> dict:
        """the next event (of this source) not given by next_event before, waiting for it; AssertionError after
        timeout seconds"""
        wait = self.timeout if timeout is None else timeout

        def find():
            for i in range(self._next_event, len(self.events)):
                if source is None or self.events[i].get("source") == source:
                    return i
            return None
        with self._cond:
            if not self._cond.wait_for(lambda: find() is not None, wait):
                raise AssertionError(f"no event{' from ' + source if source else ''} in {wait:g} s")
            i = find()
            self._next_event = i + 1
            return self.events[i]

    def settle(self, timeout: Optional[float] = None) -> None:
        """waits until the extension read every line sent so far and no capability or on_options handler runs"""
        wait = self.timeout if timeout is None else timeout
        if self.exited is None:
            self.ping(wait)
        if not self.ext._settle(wait):
            raise AssertionError(f"the extension is still busy after {wait:g} s")

    def shutdown(self) -> dict:
        """shutdown, as the workshop ends it: the answer"""
        ans = self.send("shutdown")
        self.close()
        return ans

    def close(self) -> None:
        self._in.close()                                 # stdin closed: the loop ends and detaches
        if threading.current_thread() is not self._serving:
            self._serving.join(self.timeout)
