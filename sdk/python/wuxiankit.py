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

ExtensionTest runs an Extension in memory, the way the workshop would, for unit tests. The protocol (JSON-RPC 2.0, one
object per line) is in the class docstring of Extension and in 无限工坊's extension_guide.

An AI app (a local voice companion, a chat helper, ...) reaches the player's game through 无限工坊's daemon, which runs
on this computer, and the WuxianKit addon in the game:

    from wuxiankit import Client

    kit = Client()                                     # finds the daemon (state/daemon.json) and its token
    kit.call("sense.character")                        # a capability: its result
    p = kit.propose([("chat.send", {"channel": "guild", "text": "hi"})], title="Say hi")
    kit.wait(p["id"])                                  # the proposal once it ended (done, declined, ...)
    for wake in kit.app("companion", title="Voice companion", hear=["game:*"], me=True,
                        controls=["mute", "unmute", "less", "more"]):
        ...                                            # {app, reason, items, skipped, pace?, quiet?}

Client.app opens the app (kit.app.open with the declaration, WuxianKit ARCHITECTURE.md section 10), sends the
heartbeat (kit.app.status) so the app is not taken for gone, long-polls its wakes, opens it again after a UI reload
(kit.ready), a restart of the daemon or a failed open, drops items it gave already, and ends when the app stops for
any reason but "replaced". It never raises while the daemon or the game is away: it waits and tries again (1, 2, 5,
then every 10 s); `stream.state` tells why it waits.

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

__version__ = "0.7.0"
__all__ = ["Client", "AppStream", "WuxianError", "daemon_json_path", "read_daemon_json", "cover_glob", "FINAL",
           "Extension", "ExtensionError", "ExtensionTest", "Option", "Source", "check_args", "parse_spec"]

ADDON = "WuxianKit"
FINAL = frozenset({"done", "failed", "declined", "expired", "undone"})     # a proposal's end states
BACKOFF = (1.0, 2.0, 5.0, 10.0)        # seconds to wait after the 1st, 2nd, 3rd, 4th and later failure in a row
CALL_TIMEOUT_MS = 10000
POLL_WAIT = 25.0                       # seconds one long-poll waits at most
ASK_EVERY = 15.0                       # wait(): seconds it waits for kit.proposal before it asks the game itself
LEASE = 600                            # an app's lease when the open answer gives none
SEEN_KEPT = 1000                       # item ids remembered to drop repeats


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

    def app(self, app_id: str, decl: Optional[dict] = None, *, wait: Optional[float] = None,
            yield_timeouts: bool = False, **more) -> "AppStream":
        """an AI app's wakes (ARCHITECTURE.md section 10): opens it with kit.app.open {app, **decl} and iterates over
        its wakes {app, reason, items, skipped, pace?, quiet?}. decl (a dict, and the keywords, which win over it):
        title, hear, see, digest, pace, urgent, controls, say, me, lease, minutes, options, prompt (None values are
        left out; an app opened before needs none). wait: seconds one next() blocks at most (None: until a wake); after it, next()
        gives None with yield_timeouts=True, else the iteration stops (the stream stays open: iterate again to go on)"""
        if decl is not None and not isinstance(decl, dict):
            raise TypeError("decl: the app's declaration as a dict (or keywords)")
        merged = dict(decl or {}, **more)
        merged.pop("app", None)
        return AppStream(self, app_id, {k: v for k, v in merged.items() if v is not None}, wait=wait,
                         yield_timeouts=yield_timeouts)


class AppStream:
    """the wakes of one app; see Client.app. Attributes: state ("connected" | "waiting", reason); ended (None while it
    runs; why it stopped: stopped, player, expired, left, extension, closed, ...); status (the last kit.app.open or
    kit.app.status answer); opens (how many times it was opened)"""

    def __init__(self, client: Client, app_id: str, decl: dict, *, wait: Optional[float] = None,
                 yield_timeouts: bool = False):
        self.client, self.id, self.decl = client, app_id, decl
        self.wait, self.yield_timeouts = wait, yield_timeouts
        self.topic = f"app.{app_id}"
        self.topics = [self.topic, "kit.app", "kit.ready"]
        self.state: tuple[str, str] = ("waiting", "not connected yet")
        self.ended: Optional[str] = None
        self.status: dict = {}
        self.opens = 0
        self.since = -1
        self._daemon: Optional[tuple] = None
        self._need_open = True
        self._beat_at = 0.0                              # when the next heartbeat is due
        self._fails = 0
        self._retry_at = 0.0
        self._pending: collections.deque = collections.deque()
        self._seen: collections.deque = collections.deque()
        self._seen_set: set = set()

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
        try:
            c.info()
        except WuxianError as e:
            return self._fail(e.reason)
        self._check_daemon()
        if self._need_open:
            try:
                self._open()
            except WuxianError as e:
                return self._fail(e.reason)
        if c.clock() >= self._beat_at:
            try:
                self._heartbeat()
            except WuxianError as e:
                return self._fail(e.reason)
            if self.ended is not None:
                return
        w = max(0.0, min(budget, self._beat_at - c.clock()))
        try:
            ans = c.events(self.topics, since=self.since, wait=w)
        except WuxianError as e:
            return self._fail(e.reason)
        if self._check_daemon():                          # the answer came from another daemon: from the start
            return
        nxt = ans.get("next")
        if isinstance(nxt, int) and not isinstance(nxt, bool):
            self.since = nxt
        for ev in ans["events"]:
            self._take(ev)
            if self.ended is not None:
                break
        self._ok()

    def _check_daemon(self) -> bool:
        """True when the daemon is another one than the last time: its event ids start again, so the stream asks from
        the next one to come and opens the app again"""
        ident = self.client.daemon
        if self._daemon is not None and ident != self._daemon:
            self._daemon, self.since, self._need_open = ident, -1, True
            return True
        self._daemon = ident
        return False

    def _open(self) -> None:
        c = self.client
        if self.since == -1:                              # mark the place first: no wake between open and poll is lost
            self.since = c.events(self.topics, since=-1, wait=0, limit=1).get("next", -1)
        ans = c.call("kit.app.open", dict(self.decl, app=self.id))
        ans = ans if isinstance(ans, dict) else {}
        if ans.get("on") is False:
            raise WuxianError(str(ans.get("blocked") or ans.get("why") or ans.get("error")         # blocked: it cannot start now
                                  or f"the app {self.id} did not start"),
                              "not_started")
        self.status = ans
        self._need_open = False
        self.opens += 1
        self._beat_at = c.clock() + self.beat_every

    def _heartbeat(self) -> None:
        c = self.client
        ans = c.call("kit.app.status", {"app": self.id})
        ans = ans if isinstance(ans, dict) else {}
        self._beat_at = c.clock() + self.beat_every
        if ans:
            self.status = ans
        if ans.get("on") is False:                        # it stopped and the kit.app event did not reach us
            self.ended = str(ans.get("why") or "stopped")

    @property
    def beat_every(self) -> float:
        """seconds between heartbeats: a third of the lease, at least 30"""
        lease = self.status.get("lease")
        lease = lease if isinstance(lease, (int, float)) and not isinstance(lease, bool) and lease > 0 else LEASE
        return max(30.0, lease / 3)

    def _take(self, ev: dict) -> None:
        topic, data = ev.get("topic"), ev.get("data")
        if topic == "kit.ready":                          # a login or a UI reload: open it again
            self._need_open = True
            return
        if not isinstance(data, dict):
            return
        if topic == "kit.app" and data.get("app") == self.id:
            if data.get("on") is False and data.get("why") != "replaced":
                self.ended = str(data.get("why") or "stopped")
            elif data.get("on") is True and data.get("ends") is not None:
                self.status = dict(self.status, on=True, ends=data["ends"])
            return
        if topic != self.topic:
            return
        items = data.get("items")
        if isinstance(items, list):
            fresh = [it for it in items if not self._repeat(it)]
            if items and not fresh:
                return                                    # every item given already
            data = dict(data, items=fresh)
        self._pending.append(data)

    def _repeat(self, item) -> bool:
        key = item.get("id") if isinstance(item, dict) else None
        if key is None or isinstance(key, (dict, list)):
            return False
        if key in self._seen_set:
            return True
        self._seen.append(key)
        self._seen_set.add(key)
        while len(self._seen) > SEEN_KEPT:
            self._seen_set.discard(self._seen.popleft())
        return False

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

PROTOCOL = 1
MANIFEST = "wuxian-extension.json"       # a local extension's manifest, in its folder
KINDS = ("see", "keep", "point")         # Read, Store, Guide; say / do (proposals) come later
OPTION_KINDS = ("boolean", "text", "number", "choice")
ARG_TYPES = {"string": "string", "number": "number", "boolean": "boolean", "table": "table", "any": "any"}
CAP_NAME = re.compile(r"^[a-z][a-z0-9_.]*$")
OPTION_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BAD_ARGS, NO_CAPABILITY, FAILED = -32602, -32601, 1      # a call's error codes
PARSE_ERROR, BAD_REQUEST = -32700, -32600
TIMEOUT_DEFAULT, TIMEOUT_MAX = 30, 600   # seconds the workshop waits for a capability
CALL_WAIT = 120.0                        # seconds ext.call waits for the workshop's answer
SHUTDOWN_GRACE = 3.0                     # seconds run() lets running calls finish after shutdown
HELD_EVENTS = 100                        # events kept until initialize is answered


class ExtensionError(Exception):
    """raise ExtensionError("why") in a capability: the call fails with code 1 (failed) and the message, which the agent
    reads, so say what went wrong and what to do. ExtensionError(message, -32602) says the arguments were wrong.
    ext.call raises it when the capability it called failed: code is that error's code (-32602 bad arguments, -32601
    no such capability, 1 failed, or the workshop's own such as "timeout", "down"), cap the capability's id"""

    def __init__(self, message: str, code: Union[int, str] = FAILED, cap: Optional[str] = None):
        super().__init__(message)
        self.message, self.code, self.cap = str(message), code, cap

    def __repr__(self):
        return f"ExtensionError({self.message!r}, {self.code!r}, {self.cap!r})"


def parse_spec(text: str) -> dict:
    """an argument's spec, as WuxianKit writes them: "string", "number", "boolean", "table" (a JSON object or array) or
    "any", several of those ("string|number"), or else the strings it may be ("fast|slow"); a "?" after it makes it
    optional. -> {text, optional, types: [...]} or {text, optional, enum: [...]}; ValueError for a bad spec"""
    if not isinstance(text, str) or not text or text == "?":
        raise ValueError(f"an argument's spec is a string such as \"string\" or \"number?\", not {text!r}")
    optional = text.endswith("?")
    parts = (text[:-1] if optional else text).split("|")
    if any(not p or p.strip() != p for p in parts):
        raise ValueError(f"{text!r}: the parts between | are names, without spaces, none empty")
    if all(p in ARG_TYPES for p in parts):
        return {"text": text, "optional": optional, "types": parts}
    return {"text": text, "optional": optional, "enum": parts}


def _json_type(v) -> str:
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


def _arg_text(specs: dict) -> str:
    return ", ".join(f"{n} ({s['text']})" for n, s in sorted(specs.items())) or "no arguments"


def check_args(cap_id: str, specs: dict, args) -> dict:
    """what a call brought, checked against the capability's parsed specs ({name: parse_spec(...)}) the way WuxianKit
    checks them: a copy of the declared ones that came (null counts as left out). ExtensionError code -32602 for a
    missing one, a wrong type, a string not among the choices, or one that is not declared"""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ExtensionError(f"{cap_id}: args is a JSON object", BAD_ARGS)
    out = {}
    for name, spec in specs.items():
        v = args.get(name)
        if v is None:
            if not spec["optional"]:
                raise ExtensionError(f"{cap_id}: {name} is missing ({spec['text']})", BAD_ARGS)
            continue
        got = _json_type(v)
        if "types" in spec:
            if "any" not in spec["types"] and got not in spec["types"]:
                raise ExtensionError(f"{cap_id}: {name} is a {' or '.join(spec['types'])}, not a {got}", BAD_ARGS)
        elif not isinstance(v, str) or v not in spec["enum"]:
            raise ExtensionError(f"{cap_id}: {name} is one of {'|'.join(spec['enum'])}, not "
                                 f"{json.dumps(v, ensure_ascii=False, default=str)}", BAD_ARGS)
        out[name] = v
    for name, v in args.items():
        if name not in specs and v is not None:
            raise ExtensionError(f"{cap_id}: no argument {name} (it takes {_arg_text(specs)})", BAD_ARGS)
    return out


def _first_paragraph(doc: Optional[str]) -> str:
    return " ".join((inspect.cleandoc(doc or "").split("\n\n") or [""])[0].split())


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
    """a stream of items apps and reactions hear as "<extension>:<name>"; each item's kind is "<extension>.<name>".
    emit(data) sends one (data: a JSON object), from any thread"""

    def __init__(self, ext: "Extension", name: str, desc: dict):
        self.ext, self.name, self.desc = ext, name, desc

    def emit(self, data: dict) -> None:
        self.ext._emit(self.name, data)

    def __repr__(self):
        return f"Source({self.name!r})"


class Extension:
    """a local extension: declare options, sources and capabilities, then run(). Capabilities run in worker threads
    (at most `workers` at once, the rest wait), so a slow one does not hold up the others; guard shared state with a
    lock of your own. folder: where wuxian-extension.json is (default: the current folder, which is the extension's
    folder when the workshop starts it).

    The protocol (what run() speaks on stdin / stdout; stderr is the log). JSON-RPC 2.0, UTF-8, one object per line.
      workshop -> extension, requests:
        initialize {protocol: 1, workshop, id, options: {name: value}}
          -> {protocol: 1, capabilities: [{name, kind, title, doc, args, timeout?}], sources: [{name, title, doc}],
              options: [{name, kind, title, default, hint?, choices?, min?, max?, step?, unit?}]}
        call {name, args, caller: {by: "agent" | "reaction" | "extension", id?}} -> any JSON
          | error {code: -32602 bad arguments, -32601 no such capability, 1 failed; message}
        shutdown {} -> {}, then the extension exits
      workshop -> extension, notification: options {options: {name: value}} (all of them, after the player changed one)
      extension -> workshop, notification: event {source: name, data: {...}}
      extension -> workshop, request: call {cap: "<capability id>", args} -> its result | error {code, message}"""

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
        self._caps: dict = {}
        self._sources: dict = {}
        self._options: dict = {}
        self._values: dict = {}
        self._on_options: list = []
        self._lock = threading.RLock()                   # values, pending calls
        self._wlock = threading.Lock()                   # one line at a time on stdout
        self._write: Optional[Callable[[str], None]] = None
        self._ready = False                              # initialize answered
        self._held: list = []                            # events before that
        self._setup: Optional[Callable[[], Any]] = None
        self._set_up = False
        self._reader: Optional[int] = None               # the thread that reads stdin (ext.call there would hang)
        self._ids = itertools.count(1)
        self._pending: dict = {}                         # ext.call id -> [threading.Event, answer]
        self._slots = threading.BoundedSemaphore(max(1, int(workers)))
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
        if not isinstance(name, str) or not OPTION_NAME.match(name):
            raise ValueError(f"option {name!r}: lowercase letters, digits and _, a letter first")
        if name in self._options:
            raise ValueError(f"option {name}: declared twice")
        if kind not in OPTION_KINDS:
            raise ValueError(f"option {name}: kind is one of {', '.join(OPTION_KINDS)}, not {kind!r}")
        desc: dict = {"name": name, "kind": kind, "title": title or name}
        if kind == "choice":
            if isinstance(choices, dict):
                choices = [{"value": v, "label": str(lab)} for v, lab in choices.items()]
            norm = []
            for c in choices or []:
                if isinstance(c, dict):
                    norm.append({"value": c.get("value"), "label": str(c.get("label", c.get("value")))})
                elif isinstance(c, (tuple, list)) and len(c) == 2:
                    norm.append({"value": c[0], "label": str(c[1])})
                else:
                    norm.append({"value": c, "label": str(c)})
            if not norm or not all(isinstance(c["value"], (str, int, float)) for c in norm):
                raise ValueError(f"option {name}: a choice option needs choices (strings or numbers)")
            desc["choices"] = norm
            values = [c["value"] for c in norm]
            if default is None:
                default = values[0]
            elif default not in values:
                raise ValueError(f"option {name}: the default {default!r} is not one of the choices")
        elif kind == "boolean":
            default = bool(default) if default is not None else False
        elif kind == "text":
            default = "" if default is None else default
            if not isinstance(default, str):
                raise ValueError(f"option {name}: a text option's default is a string")
        else:
            if default is None:
                default = min if min is not None else 0
            if isinstance(default, bool) or not isinstance(default, (int, float)):
                raise ValueError(f"option {name}: a number option's default is a number")
        desc["default"] = default
        for k, v in (("hint", hint), ("min", min), ("max", max), ("step", step), ("unit", unit)):
            if v is not None:
                desc[k] = v
        opt = Option(self, name, desc)
        self._options[name] = opt
        return opt

    def source(self, name: str, *, title: Optional[str] = None, doc: str = "") -> Source:
        """a stream of items others hear as "<extension>:<name>". doc: for the agent, what an item means and its fields"""
        if not isinstance(name, str) or not CAP_NAME.match(name):
            raise ValueError(f"source {name!r}: lowercase letters, digits, _ and ., a letter first")
        if name in self._sources:
            raise ValueError(f"source {name}: declared twice")
        src = Source(self, name, {"name": name, "title": title or name, "doc": doc or ""})
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
        if not isinstance(name, str) or not CAP_NAME.match(name):
            raise ValueError(f"capability {name!r}: lowercase letters, digits, _ and ., a letter first")
        if name in self._caps:
            raise ValueError(f"capability {name}: declared twice")
        args = dict(args or {})
        specs = {}
        for a, text in args.items():
            if not isinstance(a, str) or not ARG_NAME.match(a):
                raise ValueError(f"capability {name}: the argument name {a!r} is not a Python name")
            try:
                specs[a] = parse_spec(text)
            except ValueError as e:
                raise ValueError(f"capability {name}, argument {a}: {e}") from None
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                    or not 0 < timeout <= TIMEOUT_MAX):
            raise ValueError(f"capability {name}: timeout is seconds, more than 0 and at most {TIMEOUT_MAX}")
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
        desc = {"name": name, "kind": kind, "title": title or name, "doc": doc if doc is not None
                else _first_paragraph(fn.__doc__), "args": args}
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
    def caller(self) -> Optional[dict]:
        """in a capability: who called it, {by: "agent" | "reaction" | "extension", id?}; None elsewhere"""
        return getattr(self._local, "caller", None)

    # ---- calling other capabilities

    def call(self, cap: str, args: Optional[dict] = None, *, timeout: float = CALL_WAIT) -> Any:
        """another capability, through the workshop: one in the game ("sense.bags") or of a local extension
        ("voice.say"); Read, Store and Guide only. Waits for its result; ExtensionError when it failed (code and message
        as the workshop gave them; "timeout" after `timeout` seconds; "not_running" outside a running extension).
        Call it from a capability, a thread of your own or on_options, not from setup"""
        if self._write is None or not self._ready:
            raise ExtensionError("ext.call works while the extension runs, after initialize (not in setup)",
                                 "not_running", cap)
        if threading.get_ident() == self._reader:
            raise ExtensionError("ext.call cannot run on the thread that reads the workshop's messages", "not_running",
                                 cap)
        with self._lock:
            mid = next(self._ids)
            slot = [threading.Event(), None]
            self._pending[mid] = slot
        try:
            self._send({"jsonrpc": "2.0", "id": mid, "method": "call",
                        "params": {"cap": cap, "args": args if args is not None else {}}})
            if not slot[0].wait(timeout):
                raise ExtensionError(f"{cap}: no answer from the workshop in {timeout:g} s", "timeout", cap)
        finally:
            with self._lock:
                self._pending.pop(mid, None)
        answer = slot[1] or {}
        err = answer.get("error")
        if err is not None:
            if not isinstance(err, dict):
                err = {"message": str(err)}
            raise ExtensionError(str(err.get("message") or "failed"), err.get("code", FAILED), cap)
        return answer.get("result")

    # ---- running

    def run(self, setup: Optional[Callable[[], Any]] = None) -> None:
        """answers the workshop on stdin / stdout until it says shutdown or closes stdin, then returns (put clean-up
        after it). setup() runs once, at initialize, before the answer: load models, open files. Once it runs, print()
        and anything else written to stdout (by libraries, by child processes) goes to stderr, the log. A setup that
        raises fails initialize and the process exits with 1"""
        self._setup = setup
        proto_in, proto_out = _take_stdio()

        def write(line: str) -> None:
            proto_out.write(line.encode("utf-8"))
            proto_out.flush()

        self._attach(write)
        self._reader = threading.get_ident()
        failed = False
        try:
            for raw in proto_in:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as e:
                    self._error(None, PARSE_ERROR, f"not a JSON line: {e}")
                    continue
                end = self._receive(msg)
                if end == "failed":
                    failed = True
                if end:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self._settle(SHUTDOWN_GRACE)
            self._detach()
            self._reader = None
        if failed:
            sys.exit(1)

    def _attach(self, write: Callable[[str], None], setup: Optional[Callable[[], Any]] = None) -> None:
        with self._lock:
            self._write, self._ready, self._held, self._set_up = write, False, [], False
            if setup is not None:
                self._setup = setup

    def _detach(self) -> None:
        with self._lock:
            self._write, self._ready = None, False
            pending, self._pending = self._pending, {}
        for slot in pending.values():                    # ext.call still waiting: the workshop is gone
            slot[1] = {"error": {"code": "down", "message": "the workshop stopped talking to this extension"}}
            slot[0].set()

    def _send(self, msg: dict) -> None:
        line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        with self._wlock:
            write = self._write
            if write is None:
                return
            try:
                write(line)
            except (OSError, ValueError):                # the workshop closed the pipe
                pass

    def _error(self, mid, code, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})

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
        """one message from the workshop; "stop" after shutdown, "failed" after a failed setup"""
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
        try:
            args = check_args(cid, cap["specs"], params.get("args"))
        except ExtensionError as e:
            self._error(mid, BAD_ARGS, e.message)
            return
        caller = params.get("caller")
        self._spawn(self._run_cap, mid, cid, cap, args, caller if isinstance(caller, dict) else None)

    def _run_cap(self, mid, cid: str, cap: dict, args: dict, caller: Optional[dict]) -> None:
        self._local.caller = caller
        try:
            result = cap["fn"](**args)
        except ExtensionError as e:
            if e.cap is not None:                        # from an ext.call inside: this capability failed
                self._error(mid, FAILED, f"{e.cap}: {e.message}")
            else:
                self._error(mid, e.code, e.message)
            return
        except Exception as e:
            print(f"{cid} failed:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            self._error(mid, FAILED, f"{type(e).__name__}: {e}")
            return
        finally:
            self._local.caller = None
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
                with self._slots:
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


class ExtensionTest:
    """an Extension run in memory the way the workshop runs it, for unit tests: no process, the same messages (each one
    through JSON). answers: what the extension's ext.call gets, by capability id, a value or fn(args) (raise
    ExtensionError in it for an error); a capability without one fails with -32601.

        t = ExtensionTest(main.ext, setup=main.setup, options={"volume": 50}, answers={"sense.bags": {"free": 3}})
        t.call("say", {"text": "hi"})                  # its result; ExtensionError(message, code) when it failed
        t.next_event("said")                           # {source, data}; t.events has them all
        t.calls                                        # [{cap, args}] the extension called
        t.set_options({"volume": 20})                  # waits for its on_options handlers
        t.close()                                      # or: with ExtensionTest(...) as t:"""

    def __init__(self, ext: Extension, *, setup: Optional[Callable[[], Any]] = None, options: Optional[dict] = None,
                 answers: Optional[dict] = None, id: Optional[str] = None, timeout: float = 10.0):
        self.ext, self.timeout = ext, timeout
        self.id = id or ext.id or "test"
        self.options = dict(options or {})
        self.answers: dict = dict(answers or {})
        self.events: list = []                           # {source, data}, in order
        self.calls: list = []                            # {cap, args} the extension called
        self.described: Optional[dict] = None            # the initialize answer
        self._next_event = 0
        self._answers: dict = {}
        self._cond = threading.Condition()
        self._ids = itertools.count(1)
        self._q: queue.Queue = queue.Queue()
        ext._attach(self._q.put, setup=setup)
        self._thread = threading.Thread(target=self._workshop, daemon=True, name="wuxiankit-test")
        self._thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- the workshop's side

    def _workshop(self) -> None:
        while True:
            line = self._q.get()
            if line is None:
                return
            msg = json.loads(line)
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

    def _answer(self, msg: dict) -> None:
        p = msg.get("params") or {}
        cap, args = p.get("cap"), p.get("args") or {}
        with self._cond:
            self.calls.append({"cap": cap, "args": args})
        out = {"jsonrpc": "2.0", "id": msg.get("id")}
        if cap not in self.answers:
            out["error"] = {"code": NO_CAPABILITY, "message": f"no capability {cap} (ExtensionTest: give its answer "
                                                              f"in answers= or with answer())"}
        else:
            a = self.answers[cap]
            try:
                out["result"] = a(args) if callable(a) else a
            except ExtensionError as e:
                out["error"] = {"code": e.code, "message": e.message}
        self.ext._receive(json.loads(json.dumps(out)))

    def answer(self, cap: str, value) -> None:
        """what the extension's ext.call(cap) gets from now on: a value or fn(args)"""
        self.answers[cap] = value

    # ---- requests

    def send(self, method: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        """one request as the workshop sends it; the extension's raw answer {jsonrpc, id, result | error}"""
        mid = next(self._ids)
        msg = json.loads(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}))
        self.ext._receive(msg)
        wait = self.timeout if timeout is None else timeout
        with self._cond:
            if not self._cond.wait_for(lambda: mid in self._answers, wait):
                raise AssertionError(f"{method}: no answer in {wait:g} s")
            return self._answers.pop(mid)

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        """one notification as the workshop sends it"""
        self.ext._receive(json.loads(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}})))

    def initialize(self, options: Optional[dict] = None) -> dict:
        """initialize (call() does it first when it has not run): the extension's description; ExtensionError when
        setup failed"""
        if options is not None:
            self.options = dict(options)
        ans = self.send("initialize", {"protocol": PROTOCOL, "workshop": "test", "id": self.id, "options": self.options})
        if "error" in ans:
            raise ExtensionError(ans["error"].get("message", ""), ans["error"].get("code", FAILED))
        self.described = ans["result"]
        return self.described

    def call(self, name: str, args: Optional[dict] = None, *, caller: Optional[dict] = None,
             timeout: Optional[float] = None) -> Any:
        """a capability by its name (or full id): its result; ExtensionError(message, code) when it failed"""
        if self.described is None:
            self.initialize()
        if name.startswith(self.id + "."):
            name = name[len(self.id) + 1:]
        ans = self.send("call", {"name": name, "args": args if args is not None else {},
                                 "caller": caller or {"by": "agent"}}, timeout)
        if "error" in ans:
            raise ExtensionError(ans["error"].get("message", ""), ans["error"].get("code", FAILED))
        return ans.get("result")

    def set_options(self, options: dict) -> None:
        """the player changed the options (all of them): the options notification; waits for on_options"""
        if self.described is None:
            self.initialize()
        self.options = dict(options)
        self.notify("options", {"options": self.options})
        if not self.ext._settle(self.timeout):
            raise AssertionError(f"on_options still running after {self.timeout:g} s")

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
        """waits until no capability or on_options handler runs"""
        if not self.ext._settle(self.timeout if timeout is None else timeout):
            raise AssertionError("the extension is still busy")

    def shutdown(self) -> dict:
        """shutdown, as the workshop ends it: the answer"""
        ans = self.send("shutdown")
        self.close()
        return ans

    def close(self) -> None:
        if self.ext._write == self._q.put:
            self.ext._detach()
        self._q.put(None)
