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
"""WuxianKit for Python programs: one file, standard library only (Python 3.10+). Copy it into your project.

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
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

__version__ = "0.6.0"
__all__ = ["Client", "AppStream", "WuxianError", "daemon_json_path", "read_daemon_json", "cover_glob", "FINAL"]

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

    def app(self, app_id: str, *, wait: Optional[float] = None, yield_timeouts: bool = False, **decl) -> "AppStream":
        """an AI app's wakes (ARCHITECTURE.md section 10): opens it with kit.app.open {app, **decl} and iterates over
        its wakes {app, reason, items, skipped, pace?, quiet?}. decl: title, hear, digest, pace, urgent, controls,
        say, me, lease, minutes, options, prompt (None values are left out; an app opened before needs none).
        wait: seconds one next() blocks at most (None: until a wake); after it, next() gives None with
        yield_timeouts=True, else the iteration stops (the stream stays open: iterate again to go on)"""
        return AppStream(self, app_id, {k: v for k, v in decl.items() if v is not None}, wait=wait,
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
            raise WuxianError(str(ans.get("why") or ans.get("error") or f"the app {self.id} did not start"),
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
