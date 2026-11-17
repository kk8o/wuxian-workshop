r"""The apps the workshop keeps open for the programs of local extensions (a manifest's apps with "driver": "local"),
and the reports those programs tell of their work. One place knows how such an app is run: the extension's program
only names the app (the SDK's @ext.app) and gets its wakes; the game's WuxianKit only declares it and shows it.

An app is driven while its extension is enabled, approved and its process runs (the daemon says which: sync()). For
each such app one task here, for as long as that holds:

    open     kit.app.open {app} once the game can be reached (the link is online or stands by). Before the first one
             of a run of this task the app's status is read: one the player stopped (why "player") is theirs, and is
             not opened until they start it (the card's 开始: WuxianKit's kit.app {on: true}) or the next kit.ready.
             WuxianKit's refusal (no such app yet, blocked): again after REFUSED seconds, twice as long each time,
             REFUSED_MOST at most, and at once after a kit.ready. The game away: when it is back
    feed     first a wake with reason "open" (no items; status: the app's status), then what its wakes brought while
             nobody read them (kit.app.recent, reason "catchup"), then each app.<id> event as it comes. A wake is the
             request wake {app, wake} to the extension, one at a time: the next goes when the one before was
             answered, however long that takes. Answered (an error answer too: it is noted), the cursor moves to the
             wake's next and is kept (<state>\local-apps.json, by extension and app; from the first open on, where
             it began); a process that went without an answer gets the same items again at its next start, as a
             catch-up
    beat     kit.app.status by beat_rule(lease), as `wuxian listen` does it; after a kit.ready or when the link came
             back the status is read at once and what was missed is caught up
    end      kit.app {on: false}: why "player" waits as above; any other why (expired, left, extension, stopped, ...)
             opens it again after REOPEN seconds. The app is no longer driven (its process stopped, the extension was
             set off): kit.app.stop {app, why: "extension"}

A wake is WuxianKit's ({app, seq, reason, items, skipped, after, next, state?, pace?, quiet?, notes?}: the daemon's
notes are in it by then) without the items given already; the two made here ("open", "catchup") have seq 0.

Reports. A program tells how a piece of its work stands (the notification report: {app, key, state, step?, text?,
done?, total?, retry?, data?}); the host checks it (validate.check_report) and hands it to report(), which stamps it
(at: Unix seconds) and calls kit.app.report with it while the game can be reached. One that did not get there (the
game away, the call failed) is kept, the newest of each key, REPORT_KEYS keys of an app at most, in the same file, and
goes again after the next kit.ready or when the link is back, with late: true; one that WuxianKit refuses then is
dropped. Reports of one app go one at a time, a newer one of the same key in place of one still waiting.

Everything here runs on the daemon's asyncio loop. Standard library only.
"""
import asyncio
import collections
import json
import math
import time
from pathlib import Path

from ..daemon.api import ApiError
from .registry import write_json
from .rpc import RpcError

LEASE = 600                   # an app's lease when its status gives none
CATCHUP = 100                 # the items a catch-up asks kit.app.recent for: all an app keeps
REOPEN = 5.0                  # seconds before an app that ended (not by the player) is opened again
REFUSED, REFUSED_MOST = 5.0, 60.0     # seconds before an app WuxianKit would not open is tried again: the first wait,
                                      # twice as long each time, and the longest
AWAY, AWAY_MOST = 5.0, 60.0   # ... and while the game does not answer (a link that stands by is asked: each try
                              # costs it a packet); a link that is offline is only looked at, every AWAY seconds
WAKES_MOST = 64               # wakes of one app that wait for its program at most: older ones are dropped, and
                              # caught up from the game once the program reads on
REPORT_KEYS = 16              # keys of one app whose newest report is kept while the game cannot be told
FILE = "local-apps.json"      # in the state folder: {cursors: {ext: {app: next}}, reports: {ext: {app: [report]}}}


class Absent(Exception):
    """(raised by kit()) the game runs no WuxianKit that can be asked: nothing more is asked of it until its next
    kit.ready or until the link is back"""


class Gone(Exception):
    """the extension's process went while a wake waited for its answer: the task of that app ends"""


def beat_rule(lease):
    """(every, slack) of an app's heartbeat for its lease in seconds (LEASE when it gives none), as `wuxian listen`
    and the SDK's Client.app have it: kit.app.status every max(30, lease / 3) s while the link to the game is online;
    while it stands by (a heartbeat would open a session for itself alone) not before lease - max(90, lease / 3) s
    since the last one. (None, None) for a lease of 0: an app without one needs no heartbeat"""
    if lease == 0 and not isinstance(lease, bool):
        return None, None
    lease = lease if isinstance(lease, (int, float)) and not isinstance(lease, bool) and lease > 0 else LEASE
    return max(30.0, lease / 3), lease - max(90.0, lease / 3)


def _id(v):
    """an item id as the game's JSON gives it (12 or 12.0): 12; None for anything else"""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return int(v)


def first_line(text, most=300):
    return (str(text or "").splitlines() or [""])[0][:most]


class App:
    """one app that is driven: who drives it, what waits for its task (inbox: ("wake", data), ("app", data),
    ("ready", None), ("up", None)), and where it stands. state: starting | away (the game cannot be reached) |
    no_kit | opening | refused | stopped (by the player) | running | gone (its process went); why: the words for it"""

    def __init__(self, app, eid):
        self.app, self.eid = app, eid
        self.inbox, self.came = collections.deque(), asyncio.Event()
        self.task = None
        self.state, self.why, self.said = "starting", None, None
        self.held = None              # "player": stopped by the player, not opened until they start it or kit.ready
        self.checked = False          # its status was read before the first open of this task
        self.refused = self.away = 0  # tries in a row that WuxianKit refused; that the game did not answer
        self.running = False          # between an open that worked and the end of that run
        self.opened = 0               # how many times this task opened it
        self.cursor = None            # the id of the last item its program dealt with
        self.status, self.sent = {}, None
        self.beaten, self.beat_at = None, math.inf
        self.missed = False           # a wake could not be handed on, or the status not read: catch up at the next beat

    def put(self, kind, data=None):
        if kind == "wake":
            waiting = [i for i, (k, _) in enumerate(self.inbox) if k == "wake"]
            if len(waiting) >= WAKES_MOST:        # its program reads too slowly: the gap is caught up from the game
                del self.inbox[waiting[0]]
        self.inbox.append((kind, data))
        self.came.set()

    async def get(self, timeout=None):
        """the next thing for the task; None after timeout seconds (None: until one comes)"""
        if not self.inbox:
            self.came.clear()
            try:
                await asyncio.wait_for(self.came.wait(), timeout)
            except asyncio.TimeoutError:
                return None
        return self.inbox.popleft() if self.inbox else None

    def view(self):
        return dict(app=self.app, extension=self.eid, state=self.state, why=self.why, cursor=self.cursor,
                    opened=self.opened)


class Drive:
    """kit(name, args): a coroutine, a call of the game's WuxianKit: its result (ApiError: call_failed is WuxianKit's
    refusal, any other code the game that does not answer; Absent: no WuxianKit to ask). wake(eid, app, wake): a
    coroutine, the wake handed to the extension's program: back once it was answered (RpcError: with an error;
    ApiError down / cancelled: its process went, busy: its pipe takes no line). link(): "online", "standby" or
    "offline". usable(): False while nothing may be asked of the game's WuxianKit (one too old). note(text): a line
    for the journal. path: the state file (FILE in the state folder). clock: Unix seconds (reports' at); tick:
    monotonic seconds (the heartbeat)"""

    def __init__(self, kit, wake, link, note=None, path=None, *, usable=None, clock=time.time, tick=time.monotonic,
                 sleep=asyncio.sleep):
        self.kit, self.wake, self.link = kit, wake, link
        self.note = note or (lambda text: None)
        self.usable = usable or (lambda: True)
        self.path = Path(path) if path is not None else None
        self.clock, self.tick, self.sleep = clock, tick, sleep
        self.apps = {}                # app id -> App: the apps driven now
        self.stopping = {}            # app id -> the task that tells the game it is driven no more
        self.fresh = {}               # app id -> {key: (extension, report)}: reports on their way to the game
        self.senders = {}             # app id -> the task that sends them
        self.flushing = None          # the task that sends the kept ones again
        self.saving = None            # an asyncio.Lock: one write of the file at a time
        self.kept = self.load()       # {"cursors": {ext: {app: next}}, "reports": {ext: {app: [report]}}}

    # --- the file

    def load(self):
        out = dict(cursors={}, reports={})
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path is not None else {}
        except (OSError, ValueError):
            data = {}
        for eid, apps in (data.get("cursors") if isinstance(data, dict) and isinstance(data.get("cursors"), dict)
                          else {}).items():
            if isinstance(apps, dict):
                marks = {app: _id(n) for app, n in apps.items() if isinstance(app, str) and _id(n) is not None}
                if marks:
                    out["cursors"][eid] = marks
        for eid, apps in (data.get("reports") if isinstance(data, dict) and isinstance(data.get("reports"), dict)
                          else {}).items():
            if isinstance(apps, dict):
                for app, reports in apps.items():
                    good = [r for r in reports if isinstance(r, dict) and isinstance(r.get("key"), str)
                            and r.get("app") == app] if isinstance(reports, list) else []
                    if good:
                        out["reports"].setdefault(eid, {})[app] = good[-REPORT_KEYS:]
        return out

    async def save(self):
        """the file written whole (off the loop), one write at a time; what cannot be written is noted once"""
        if self.path is None:
            return
        if self.saving is None:
            self.saving = asyncio.Lock()
        async with self.saving:
            data = json.loads(json.dumps(self.kept))
            try:
                await asyncio.to_thread(self.write, data)
            except OSError as e:
                self.say(None, f"local apps: {self.path.name} cannot be written ({e}): where the wakes got and the "
                               f"reports not sent yet are not kept")

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, data)

    def say(self, a, text):
        """a line for the journal, not the same one twice in a row for an app"""
        if a is None:
            if getattr(self, "said", None) != text:
                self.said = text
                self.note(text)
        elif a.said != text:
            a.said = text
            self.note(f"local extension {a.eid}: its app {a.app}: {text}")

    # --- which apps are driven

    def sync(self, wanted, event=None):
        """the apps to drive now, {app: extension id} (those whose program runs): a task for each new one, the task
        of one that is no longer among them ended (and the game told: stop). event: "ready" (a kit.ready came: the
        game's UI began anew, a player's stop is over, refusals are forgotten) or "up" (the link is back, or the
        apps were declared anew); the tasks that go on hear it, and the reports kept go again"""
        for app, a in list(self.apps.items()):
            if wanted.get(app) != a.eid:
                self.drop(a)
            elif a.task is not None and a.task.done():       # it ended by itself (its process went and came back)
                del self.apps[app]
        for app, eid in wanted.items():
            a = self.apps.get(app)
            if a is None:
                a = self.apps[app] = App(app, eid)
                a.checked = event == "ready"                 # a new UI session: nobody stopped it there yet
                a.cursor = (self.kept["cursors"].get(eid) or {}).get(app)
                a.task = asyncio.ensure_future(self.run(a))
            elif event:
                a.put(event)
        if event and (self.flushing is None or self.flushing.done()):
            self.flushing = asyncio.ensure_future(self.flush())

    def drop(self, a):
        """an app that is driven no more: its task ends; one that ran is stopped in the game (why "extension")"""
        self.apps.pop(a.app, None)
        ran = a.running
        if a.task is not None:
            a.task.cancel()
        if ran:
            self.stopping[a.app] = asyncio.ensure_future(self.stop(a))

    async def stop(self, a):
        try:
            if self.link() != "offline" and self.usable():
                await self.kit("kit.app.stop", {"app": a.app, "why": "extension"})
                self.note(f"local extension {a.eid}: its program does not run any more: its app {a.app} is stopped")
        except (ApiError, Absent) as e:              # the game away: its lease ends it there
            self.note(f"local extension {a.eid}: its app {a.app} could not be stopped in the game "
                      f"({first_line(getattr(e, 'message', e))}): it ends there when its lease is over")
        finally:
            if self.stopping.get(a.app) is asyncio.current_task():
                del self.stopping[a.app]

    def forget(self, eid):
        """an extension was removed: where its apps' wakes got and its reports not sent are kept no longer"""
        had = self.kept["cursors"].pop(eid, None), self.kept["reports"].pop(eid, None)
        if any(had):
            asyncio.ensure_future(self.save())

    def close(self):
        """the daemon stops: every task ends (the apps end in the game when their leases are over)"""
        tasks = [a.task for a in self.apps.values()] + list(self.senders.values()) + list(self.stopping.values()) \
            + [self.flushing]
        self.apps.clear()
        for t in tasks:
            if t is not None:
                t.cancel()

    def view(self):
        """[{app, extension, state, why, cursor, opened}] of the apps driven now, by app id"""
        return [a.view() for _, a in sorted(self.apps.items())]

    def heard(self, topic, data):
        """WuxianKit's event: a wake of an app driven here (app.<id>) or its kit.app go to its task"""
        if not isinstance(topic, str) or not isinstance(data, dict):
            return
        if topic == "kit.app":
            a = self.apps.get(data.get("app"))
            if a is not None and isinstance(data.get("on"), bool):
                a.put("app", data)
        elif topic.startswith("app."):
            a = self.apps.get(topic[4:])
            if a is not None:
                a.put("wake", data)

    # --- one app

    async def run(self, a):
        try:
            stopping = self.stopping.get(a.app)
            if stopping is not None:                     # its last driver's stop first: it must not end this run
                await asyncio.wait({stopping})
            while True:
                status = await self.open(a)
                why, at_once = await self.feed(a, status)
                a.running = False
                if why == "player":
                    a.held = "player"
                elif not at_once:
                    a.state, a.why = "ended", why
                    self.say(a, f"ended ({why}): opened again in {REOPEN:g} s")
                    await self.sleep(REOPEN)
        except Gone:                                     # (running stays: the game still has it open, drop() stops it)
            a.state, a.why = "gone", "its process went while a wake waited for its answer"
        except asyncio.CancelledError:
            raise
        except Exception as e:                           # a bug: said, and the daemon goes on
            a.running, a.state, a.why = False, "failed", repr(e)
            self.note(f"local extension {a.eid}: driving its app {a.app} failed: {e!r}")

    def between(self, a, kind, data):
        """(open) what came for an app that does not run: a kit.ready ends the player's stop and the refusals; the
        player's own start (kit.app on) ends their stop, their stop (why player) begins it; wakes are not for now
        (the open catches up)"""
        if kind == "ready":
            a.held, a.refused, a.away, a.checked = None, 0, 0, True
        elif kind == "up":
            a.away = 0
        elif kind == "app":
            if data.get("on") is True:
                a.held = None
            elif data.get("why") == "player":
                a.held = "player"

    async def open(self, a):
        """until the app runs: its status as kit.app.open answered it"""
        pause = 0                                        # 0: try now; None: until something comes; else seconds
        while True:
            if pause != 0:
                got = await a.get(pause)
                if got is not None:
                    self.between(a, *got)
            while a.inbox:
                self.between(a, *a.inbox.popleft())
            if a.held == "player":
                a.state, a.why, pause = "stopped", "the player stopped it", None
                self.say(a, "the player stopped it: opened again when they start it, or after the next login or UI "
                            "reload")
                continue
            if self.link() == "offline" or not self.usable():
                a.state, a.why, pause = "away", "the game is not connected", AWAY
                continue
            try:
                if not a.checked:                        # the player may have stopped it before this task began
                    was = await self.kit("kit.app.status", {"app": a.app})
                    a.checked = True
                    if isinstance(was, dict) and was.get("on") is False and was.get("why") == "player":
                        a.held = "player"
                        pause = 0
                        continue
                a.state, a.why = "opening", None
                status = await self.kit("kit.app.open", {"app": a.app})
            except Absent:
                a.state, a.why, pause = "no_kit", "the game runs no WuxianKit", None
                continue
            except ApiError as e:
                if e.code == "call_failed":
                    pause = self.refuse(a, first_line(e.message))
                else:
                    a.state, a.why = "away", f"{e.code}: {first_line(e.message)}"
                    pause = min(AWAY_MOST, AWAY * 2 ** a.away)
                    a.away += 1
                continue
            status = status if isinstance(status, dict) else {}
            if status.get("on") is False:                # declared, and it cannot start now: WuxianKit says why
                pause = self.refuse(a, str(status.get("blocked") or status.get("why") or "it did not start"))
                continue
            a.refused = a.away = 0
            a.opened += 1
            return status

    def refuse(self, a, why):
        """WuxianKit would not open it: the seconds until the next try (a kit.ready tries at once)"""
        wait = min(REFUSED_MOST, REFUSED * 2 ** a.refused)
        a.refused += 1
        a.state, a.why = "refused", why
        self.say(a, f"WuxianKit did not open it ({why}): tried again after the next login or UI reload, or in "
                    f"{wait:g} s")
        return wait

    async def feed(self, a, status):
        """the app runs: its wakes to its program until it ends: (why it ended, whether it is opened again at once)"""
        a.state, a.why, a.running, a.missed = "running", None, True, False
        self.say(a, "open")
        first = a.cursor is None
        self.joined(a, status)
        if first:                                        # where it begins is kept from its first open on: a program
            await self.mark(a)                           # that goes inside its first wake gets that one again
        await self.give(a, self.made(a, "open", [], status=status))
        await self.catch_up(a)
        while True:
            got = await a.get(None if a.beat_at == math.inf else max(0.0, a.beat_at - self.tick()))
            if got is None:                              # the heartbeat's time
                if self.due(a):
                    why = await self.rejoin(a)
                    if why is not None:
                        return why, False
                    if a.missed:
                        await self.catch_up(a)
                continue
            kind, data = got
            if kind == "wake":
                await self.take(a, data)
            elif kind == "app":
                if data.get("on") is False and data.get("why") != "replaced":
                    return str(data.get("why") or "stopped"), False
            else:                                        # ready, up: does it still run, and what was missed
                why = await self.rejoin(a)
                if why is not None:
                    return why, kind == "ready"
                await self.catch_up(a)

    def joined(self, a, status):
        """the app's status read: when the next heartbeat is due, and where its wakes are (a cursor past the app's
        newest item, as after another character's login, would make every wake a repeat: it goes back there)"""
        a.status, a.sent = status, _id(status.get("sent"))
        every, _ = beat_rule(status.get("lease"))
        a.beaten = self.tick()
        a.beat_at = math.inf if every is None else a.beaten + every
        nxt = _id(status.get("next"))
        if a.cursor is None:                             # the first time: from where its wakes got as it opened
            a.cursor = a.sent if a.sent is not None else nxt or 0
        elif nxt is not None and nxt < a.cursor:
            a.cursor = nxt

    def due(self, a):
        """whether the heartbeat goes now: not while the link stands by and the lease still has room (beat_rule)"""
        _, slack = beat_rule(a.status.get("lease"))
        now = self.tick()
        if slack is None or a.beaten is None or now - a.beaten >= slack or self.link() != "standby":
            return True
        a.beat_at = a.beaten + slack
        return False

    async def rejoin(self, a):
        """the app's status read again (its heartbeat as well): None while it runs, else why it does not. The game
        away: None, asked again in a while"""
        try:
            status = await self.kit("kit.app.status", {"app": a.app})
        except Absent:
            return "extension"
        except ApiError as e:
            if e.code == "call_failed":                  # the game knows no app of this id any more
                return "forgotten"
            a.missed = True
            a.beat_at = self.tick() + AWAY_MOST
            return None
        status = status if isinstance(status, dict) else {}
        if not status.get("on"):
            return str(status.get("why") or "stopped")
        self.joined(a, status)
        return None

    def made(self, a, reason, items, after=None, nxt=None, **more):
        """a wake the workshop makes itself (open, catchup): seq 0"""
        after = a.cursor if after is None else after
        return dict(app=a.app, seq=0, reason=reason, items=items, skipped=0, after=after,
                    next=after if nxt is None else nxt, **more)

    async def catch_up(self, a, upto=None):
        """the items after the cursor that the app's wakes brought and that its program did not get (kit.app.recent),
        as one wake with reason "catchup": up to `upto` (the after of the wake that showed the gap), else up to
        where its wakes got (the status's sent). True when nothing is left to catch up; False when the game could
        not be asked (missed: again at the next beat)"""
        upto = a.sent if upto is None else upto
        if upto is not None and upto <= a.cursor:
            a.missed = False
            return True
        try:
            res = await self.kit("kit.app.recent", {"app": a.app, "since": a.cursor, "limit": CATCHUP})
        except (ApiError, Absent):
            a.missed = True
            a.beat_at = min(a.beat_at, self.tick() + AWAY_MOST)
            return False
        res = res if isinstance(res, dict) else {}
        got = res.get("items") if isinstance(res.get("items"), list) else []
        ids = [(_id(it.get("id")), it) for it in got if isinstance(it, dict)]
        items = [it for n, it in ids if n is not None and n > a.cursor and (upto is None or n <= upto)]
        if items:
            newest = upto if upto is not None else max(_id(items[-1]["id"]), _id(res.get("next")) or 0)
            await self.give(a, self.made(a, "catchup", items, nxt=newest))
        elif upto is not None:
            await self.moved(a, upto)                    # the app keeps them no more: its wakes go on from there
        a.missed = False
        return True

    async def take(self, a, data):
        """one wake of the app, as its event brought it: a gap before it (its after is past the cursor) is caught up
        first; it goes on without the items given already, not at all when it only repeats"""
        nxt, after = _id(data.get("next")), _id(data.get("after"))
        if nxt is None:                                  # no cursor in it: as it is
            await self.give(a, data)
            return
        if after is not None and after > a.cursor and not await self.catch_up(a, after):
            return                                       # not now: its items come with the catch-up to come
        items = data.get("items") if isinstance(data.get("items"), list) else []
        skipped = (_id(data.get("skipped")) or 0) > 0
        if (items or skipped) and nxt <= a.cursor:
            return                                       # given already
        fresh = [it for it in items if not (isinstance(it, dict) and _id(it.get("id")) is not None
                                            and _id(it["id"]) <= a.cursor)]
        if items and not fresh and not skipped:
            await self.moved(a, nxt)
            return
        await self.give(a, data if len(fresh) == len(items) else dict(data, items=fresh))

    async def give(self, a, wake):
        """a wake to the extension's program; once it is answered the cursor is the wake's next. Gone when its
        process went meanwhile (the cursor stays: the items come again)"""
        try:
            await self.wake(a.eid, a.app, wake)
        except RpcError as e:                            # its program answered an error: noted, and it goes on
            self.note(f"local extension {a.eid}: its app {a.app}: the wake ({wake.get('reason')}) was answered with "
                      f"an error: {first_line(e.message)}")
        except ApiError as e:
            if e.code != "busy":
                raise Gone() from None
            a.missed = True                              # its pipe takes no line now: caught up at the next beat
            a.beat_at = min(a.beat_at, self.tick() + AWAY)
            return
        nxt = _id(wake.get("next"))
        if nxt is not None:
            await self.moved(a, nxt)

    async def moved(self, a, nxt):
        """the cursor is nxt now (never back), and kept"""
        if a.cursor is not None and nxt <= a.cursor:
            return
        a.cursor = nxt
        await self.mark(a)

    async def mark(self, a):
        """the app's cursor into the file"""
        marks = self.kept["cursors"].setdefault(a.eid, {})
        if marks.get(a.app) != a.cursor:
            marks[a.app] = a.cursor
            await self.save()

    # --- reports

    def reachable(self):
        return self.link() != "offline" and bool(self.usable())

    def report(self, eid, report):
        """a report of an extension's program (checked by the host): stamped, and on its way to the game; a newer
        one of the same key takes the place of one that still waits"""
        app = report["app"]
        box = self.fresh.setdefault(app, collections.OrderedDict())
        box.pop(report["key"], None)
        box[report["key"]] = (eid, dict(report, at=round(self.clock(), 3)))
        sender = self.senders.get(app)
        if sender is None or sender.done():
            self.senders[app] = asyncio.ensure_future(self.send(app))

    async def send(self, app):
        box = self.fresh.get(app)
        try:
            while box:
                key, (eid, report) = box.popitem(last=False)
                told = await self.tell(eid, report)
                kept = (self.kept["reports"].get(eid) or {}).get(app) or []
                rest = [r for r in kept if r.get("key") != key]
                if told is None:                         # not there: kept, the newest of each key
                    rest = (rest + [report])[-REPORT_KEYS:]
                if rest != kept:
                    if rest:
                        self.kept["reports"].setdefault(eid, {})[app] = rest
                    else:
                        self.forget_reports(eid, app)
                    await self.save()
        finally:
            if self.senders.get(app) is asyncio.current_task():
                del self.senders[app]

    def forget_reports(self, eid, app):
        apps = self.kept["reports"].get(eid)
        if apps is not None:
            apps.pop(app, None)
            if not apps:
                del self.kept["reports"][eid]

    async def tell(self, eid, report, late=False):
        """kit.app.report: True when the game took it, False when WuxianKit refused it, None when it did not get
        there"""
        if not self.reachable():
            return None
        try:
            await self.kit("kit.app.report", dict(report, late=True) if late else report)
        except Absent:
            return None
        except ApiError as e:
            if e.code != "call_failed":
                return None
            self.note(f"local extension {eid}: WuxianKit did not take a report of its app {report.get('app')} "
                      f"({report.get('key')}): {first_line(e.message)}")
            return None if not late else False           # the first time it may not be declared yet: once more, late
        return True

    async def flush(self):
        """the reports kept go to the game again, the oldest first, with late: true; one that gets there, or that
        WuxianKit refuses, is kept no longer. It ends at the first that does not get there"""
        changed = False
        try:
            for eid in list(self.kept["reports"]):
                for app in list(self.kept["reports"].get(eid) or {}):
                    while True:
                        kept = (self.kept["reports"].get(eid) or {}).get(app) or []
                        if not kept:
                            break
                        report = kept[0]
                        if await self.tell(eid, report, late=True) is None:
                            return
                        now = (self.kept["reports"].get(eid) or {}).get(app) or []
                        rest = [r for r in now if r is not report]
                        if rest:
                            self.kept["reports"][eid][app] = rest
                        else:
                            self.forget_reports(eid, app)
                        changed = True
        finally:
            if changed:
                await self.save()
