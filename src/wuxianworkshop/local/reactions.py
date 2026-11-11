"""Reactions (反射): what the workshop does by itself when something is heard, without waking the agent. The agent sets
a reaction (hear one source, maybe only when the item says so, then call one capability), the workshop calls it each
time an item matches, at most once per cooldown. Only see / keep / point capabilities: a reaction never sends anything
in the player's name nor changes the game (say / do are proposals the player approves, not for reactions).

A spec (set):
    {id?        ^[A-Za-z0-9_.-]{1,32}$; none: r1, r2, ...; the same id again replaces that reaction
     owner?     an app id: the reaction goes when that app stops (app_stopped); none: until cleared or the workshop exits
     hear       one entry as an app's hear writes it, "<source>:<what>": game:combat (a topic), game:combat.start (a kind),
                game:*, route:step, chat:guild, bus:demo.*, voice:said (a source of an extension on this computer)
     when?      {"data.unit.classification": "elite" | ["elite", "rareelite"], ...}: dotted paths into the item, each
                must hold (a list: any of its values)
     call       a capability id ("voice.play", "sense.bags"); see, keep or point
     args?      {} the arguments
     item?      an argument name: the whole item goes as that argument
     cooldown?  seconds (default 10; 0: no cooldown) between two calls of this reaction
     note?      what it is for}

Items (item()): an app's item as the game gives it, {source, kind, data, time, topic?, id?} (game: topic combat and kind
combat.start; route: kind route.step, no topic; chat: the message, channel guild; bus: {topic, data}), or an item of a
source on this computer, {source: "voice", kind: "voice.said", data, time}.

Hearing: the game's sources are heard through one app the daemon opens (kit hear entries); hear(entries) is called with
the union of the reactions' game entries whenever it changes ([]: nothing to hear). Sources on this computer (the names
local_sources() gives: the extensions' ids) are not in it: the daemon hands their items to item() itself.

An app's own reactions: what a local extension's manifest declares for its app (apps[].reactions: the same fields
without id and owner) is set while that app runs (app_started: owner the app, ids <app>.1, <app>.2, ...) and goes when
it stops, with the reactions the agent set for that owner.

Loops: a reaction whose call is not done is not fired again meanwhile; at most MAX_RUNNING calls of all reactions run
at once (what is heard then is dropped, no cooldown spent); a reaction calls MAX_PER_MINUTE times a minute at most
(what it hears until that minute is over calls nothing, and its error says so: a cooldown of 0 and a source that tells
without end, or its own call making what it hears); a call's caller is {"by": "workshop", "chain": ["reaction:<id>"]},
so a capability that calls on (and makes an item that is heard again) ends at the chain's limit."""
import asyncio
import copy
import inspect
import json
import re
import time

MAX_REACTIONS = 32                              # the reactions the agent set (an app's own are not counted)
MAX_APP_REACTIONS = 16                          # the reactions one app's manifest declares
MAX_RUNNING = 16                                # calls of all reactions at once
MAX_PER_MINUTE = 240                            # calls of one reaction within a minute
DEFAULT_COOLDOWN = 10
MAX_COOLDOWN = 86400
MAX_WHEN = 8
MAX_VALUES = 16
MAX_NOTE = 200
MAX_ARGS = 4096                                 # bytes of the args as JSON, roughly
MAX_ERROR = 300
KINDS = ("see", "keep", "point")

ID = re.compile(r"^[A-Za-z0-9_.-]{1,32}\Z")
APP = re.compile(r"^[a-z][a-z0-9_]{0,23}\Z")
SOURCE = re.compile(r"^[a-z][a-z0-9_]{0,23}\Z")
WHAT = re.compile(r"^(\*|[a-z0-9_]+(\.[a-z0-9_]+)*(\.\*)?)\Z")
CAP = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+\Z")
ARG = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}\Z")
PATH = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){0,7}\Z")
FIELDS = ("id", "owner", "hear", "when", "call", "args", "item", "cooldown", "note")
DECLARED = ("hear", "when", "call", "args", "item", "cooldown", "note")   # what an app's manifest says of a reaction
EXAMPLE = ('{"hear": "game:combat.start", "when": {"data.unit.classification": ["elite", "rareelite"]}, '
           '"call": "voice.play", "args": {"pool": "combat_start_elite"}, "owner": "companion"}')


class ReactionError(ValueError):
    """a spec the engine does not take; the message tells the agent what is wrong and how to write it"""


async def maybe(value):
    """a callback's answer, awaited when it is awaitable"""
    return await value if inspect.isawaitable(value) else value


def split(entry):
    """a hear entry's (source, what), or None"""
    if not isinstance(entry, str) or ":" not in entry:
        return None
    source, what = entry.split(":", 1)
    return (source, what) if SOURCE.match(source) and what and WHAT.match(what) else None


def matches(what, source, item):
    """whether hear entry <source>:<what> hears item: * hears all of the source; else what is the item's topic, kind,
    kind without the source's prefix (route.step: step; voice.said: said) or chat channel; <prefix>.* hears what starts
    with <prefix>."""
    if not isinstance(item, dict) or item.get("source") != source:
        return False
    if what == "*":
        return True
    names = set()
    for key in ("topic", "kind", "channel"):
        value = item.get(key)
        if isinstance(value, str) and value:
            names.add(value)
            if value.startswith(source + "."):
                names.add(value[len(source) + 1:])
    if what.endswith(".*"):
        prefix = what[:-1]                      # "demo." of "demo.*"
        return any(n.startswith(prefix) for n in names)
    return what in names


MISSING = object()


def pick(item, path):
    """the value at a dotted path of item (a digit steps into a list, 1 the first), or MISSING"""
    value = item
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and 1 <= int(part) <= len(value):
            value = value[int(part) - 1]
        else:
            return MISSING
    return value


def same(a, b):
    """equal as JSON values are (true is no 1)"""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def holds(when, item):
    """whether every condition of when holds for item"""
    for path, want in (when or {}).items():
        value = pick(item, path)
        if value is MISSING:
            return False
        if isinstance(want, list):
            if not any(same(value, w) for w in want):
                return False
        elif not same(value, want):
            return False
    return True


def scalar(value):
    return value is None or isinstance(value, (str, bool, int, float))


def message_of(error):
    """an exception as one line the agent can read"""
    words = getattr(error, "message", None)
    if not isinstance(words, str) or not words:
        words = str(error) or type(error).__name__
    code = getattr(error, "code", None)
    if isinstance(code, str) and code and not words.startswith(code):
        words = f"{code}: {words}"
    words = " ".join(words.split())
    return words if len(words) <= MAX_ERROR else words[:MAX_ERROR - 1] + "…"


class Reactions:
    """the reactions set now, and what an item makes them call.

    call(cap_id, args, caller) -> awaitable result (raises on failure); caller = {"by": "workshop", "chain":
    ["reaction:<id>"]}.
    hear(entries) -> awaitable: the game's hear entries of all reactions (sorted, each once), called when they change.
    kind_of(cap_id) -> "see" | "keep" | "point" | "say" | "do" | None (no such capability); may be async.
    now() -> seconds (time.time). note(text): a line for the daemon's log (a call or hear that failed), optional.
    local_sources() -> the source names on this computer (the local extensions' ids), whose entries hear() does not
    get; optional (none: every source is the game's). When that set changes, refresh() tells hear the new union."""

    def __init__(self, call, hear, kind_of, now=time.time, note=None, local_sources=None):
        self._call, self._hear, self._kind_of, self._now = call, hear, kind_of, now
        self._note, self._local = note, local_sources
        self._rows = {}                          # id -> row, in the order they were set
        self._next = 0
        self._heard = []                         # the union hear() was last told
        self._hear_error = None                  # why the last hear() failed (shown on the game reactions), or None
        self._lock = asyncio.Lock()
        self._tasks = set()                      # the calls that run now
        self._declared = {}                      # app -> the specs of its manifest that are set (as JSON text)
        self._crowded = False                    # MAX_RUNNING calls ran when something was heard (said once)

    # -- what is set ---------------------------------------------------------------------------------------------------

    async def set(self, spec) -> dict:
        """checks spec, sets the reaction (replacing one of the same id) -> its row; ReactionError when it is wrong"""
        clean = await self._check(spec)
        rid = clean["id"]
        there = self._rows.get(rid)
        if there is not None and there["declared"]:
            raise ReactionError(f"{rid} is a reaction the app {there['declared']} declares itself (its extension's "
                                f"manifest): it is there while the app runs; give yours another id")
        if there is None and sum(1 for r in self._rows.values() if not r["declared"]) >= MAX_REACTIONS:
            raise ReactionError(f"at most {MAX_REACTIONS} reactions are set at once; reaction_clear the ones no longer "
                                f"needed (by id, or by owner), or set again an id that is there to replace it")
        if rid is None:
            rid = self._new_id()
        clean["id"] = rid
        self._store(clean)
        await self._rehear()
        return self._row(clean)

    def _store(self, clean, declared=None):
        clean.update(fired=0, failed=0, last=None, error=None, declared=declared, running=False, minute=None, calls=0)
        self._rows.pop(clean["id"], None)        # a replaced one goes to the end, as a new one
        self._rows[clean["id"]] = clean

    def list(self) -> list:
        """the reactions, in the order they were set: rows {id, owner, hear, when, call, args, item, cooldown, note,
        fired (calls made), failed (calls that failed), last (time of the last call), error (the last failure, why
        the game does not hear it, or that it called too often and calls nothing for the rest of a minute)}"""
        return [self._row(r) for r in self._rows.values()]

    async def clear(self, id=None, owner=None) -> int:
        """clears the reaction id, the reactions of owner (both: that one if it is owner's), or all (neither) -> how
        many"""
        gone = [rid for rid, r in self._rows.items()
                if (id is None or rid == id) and (owner is None or r["owner"] == owner)]
        for rid in gone:
            self._declared.pop(self._rows.pop(rid)["declared"], None)    # an app's own come back when it runs again
        if gone:
            await self._rehear()
        return len(gone)

    async def app_started(self, app, specs) -> list:
        """an app runs: the reactions its extension's manifest declares for it (specs: DECLARED fields each, at most
        MAX_APP_REACTIONS) are set, owner the app, ids <app>.1, <app>.2, ...; the ones set for it before go. The same
        specs again change nothing (the reactions keep their counts and cooldowns). -> the specs that were not taken,
        a line each"""
        if not isinstance(app, str) or not APP.match(app):
            return [f"{app!r} is no app id"]
        specs = list(specs or [])[:MAX_APP_REACTIONS]
        key = json.dumps(specs, sort_keys=True, ensure_ascii=False, default=repr)
        if self._declared.get(app) == key:
            return []
        gone = [rid for rid, r in self._rows.items() if r["declared"] == app]
        for rid in gone:
            del self._rows[rid]
        self._declared.pop(app, None)
        problems = []
        for n, spec in enumerate(specs, 1):
            try:
                if not isinstance(spec, dict):
                    raise ReactionError("a reaction is an object")
                clean = await self._check(dict({k: spec[k] for k in DECLARED if spec.get(k) is not None},
                                               id=f"{app}.{n}", owner=app))
            except ReactionError as e:
                problems.append(f"reaction {n} of app {app}: {e}")
                continue
            self._store(clean, declared=app)
        if not problems:                         # one not taken (its capability is not there yet): again next time
            self._declared[app] = key
        if gone or len(problems) < len(specs):
            await self._rehear()
        return problems

    async def app_stopped(self, app) -> None:
        """an app stopped: its reactions go (its own, and what the agent set with it as owner)"""
        if isinstance(app, str) and app:
            self._declared.pop(app, None)
            await self.clear(owner=app)

    async def refresh(self) -> None:
        """the sources on this computer changed (an extension added or removed): hear the new union of game entries"""
        await self._rehear()

    # -- items ---------------------------------------------------------------------------------------------------------

    async def item(self, item) -> None:
        """an item heard: each reaction that hears it, whose when holds and whose cooldown is over, calls its capability
        (in the background: drain() waits for the calls); not while its last call runs, not more than MAX_PER_MINUTE
        times within a minute, and not while MAX_RUNNING calls run"""
        if not isinstance(item, dict) or not isinstance(item.get("source"), str):
            return
        now = self._now()
        if self._crowded and len(self._tasks) < MAX_RUNNING:
            self._crowded = False
        for r in list(self._rows.values()):
            source, what = split(r["hear"])
            if not matches(what, source, item) or not holds(r["when"], item):
                continue
            if r["running"]:                     # its last call is not done: no second one meanwhile
                continue
            if r["last"] is not None and r["cooldown"] and now - r["last"] < r["cooldown"]:
                continue
            if r["minute"] is None or now - r["minute"] >= 60:
                r["minute"], r["calls"] = now, 0
            if r["calls"] >= MAX_PER_MINUTE:     # (no cooldown, and what it hears comes without end): said once
                if not (r["error"] or "").startswith("too often:"):
                    r["error"] = (f"too often: it called {MAX_PER_MINUTE} times within a minute: what it hears until "
                                  f"that minute is over calls nothing (give it a cooldown, or a narrower hear or when)")
                    self._say(f"reaction {r['id']}: {r['error']}")
                continue
            if len(self._tasks) >= MAX_RUNNING:  # too many at once (a loop, or slow capabilities): dropped
                if not self._crowded:
                    self._crowded = True
                    self._say(f"reactions: {MAX_RUNNING} calls run at once: what is heard meanwhile calls nothing")
                continue
            r["last"], r["fired"], r["running"] = now, r["fired"] + 1, True
            r["calls"] += 1
            args = copy.deepcopy(r["args"] or {})
            if r["item"]:
                args[r["item"]] = copy.deepcopy(item)
            task = asyncio.ensure_future(self._fire(r, args))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """waits until the calls started so far are done"""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _fire(self, r, args):
        try:
            await maybe(self._call(r["call"], args, {"by": "workshop", "chain": [f"reaction:{r['id']}"]}))
        except asyncio.CancelledError:
            raise
        except Exception as e:                   # noqa: BLE001 - a reaction's failure is kept on it, never raised
            r["failed"] += 1
            r["error"] = message_of(e)
            self._say(f"reaction {r['id']}: {r['call']} failed: {r['error']}")
        else:
            if r["error"] and not r["error"].startswith("not heard:"):
                r["error"] = None
        finally:
            r["running"] = False

    # -- hearing -------------------------------------------------------------------------------------------------------

    def _locals(self):
        try:
            names = self._local() if self._local else ()
            return {n for n in names or () if isinstance(n, str)}
        except Exception as e:                   # noqa: BLE001
            self._say(f"reactions: local_sources failed: {message_of(e)}")
            return set()

    def _is_game(self, entry, local=None):
        source = split(entry)[0]
        return source not in (self._locals() if local is None else local)

    async def _rehear(self):
        """tells hear() the union of game entries when it changed; a failure is kept on the game reactions"""
        async with self._lock:
            local = self._locals()
            union = sorted({r["hear"] for r in self._rows.values() if self._is_game(r["hear"], local)})
            if union == self._heard and self._hear_error is None:
                return
            try:
                await maybe(self._hear(list(union)))
            except Exception as e:               # noqa: BLE001
                self._hear_error = message_of(e)
                self._say(f"reactions: hear {union} failed: {self._hear_error}")
            else:
                self._heard, self._hear_error = union, None
            for r in self._rows.values():
                game = self._is_game(r["hear"], local)
                if self._hear_error and game:
                    r["error"] = f"not heard: {self._hear_error}"
                elif r["error"] and r["error"].startswith("not heard:"):
                    r["error"] = None

    def _say(self, text):
        if self._note:
            try:
                self._note(text)
            except Exception:                    # noqa: BLE001 - a log line never breaks a reaction
                pass

    # -- checks --------------------------------------------------------------------------------------------------------

    def _new_id(self):
        while True:
            self._next += 1
            rid = f"r{self._next}"
            if rid not in self._rows:
                return rid

    async def _check(self, spec):
        if not isinstance(spec, dict):
            raise ReactionError(f"a reaction is an object such as {EXAMPLE}")
        extra = sorted(k for k in spec if k not in FIELDS)
        if extra:
            raise ReactionError(f"a reaction has no {', '.join(map(str, extra))}; its fields are {', '.join(FIELDS)} "
                                f"(hear and call are needed), such as {EXAMPLE}")
        out = {}

        rid = spec.get("id")
        if rid is not None and (not isinstance(rid, str) or not ID.match(rid)):
            raise ReactionError("id is up to 32 letters, digits, _ . or - (or leave it out: r1, r2, ... are given); "
                                "setting an id that is there replaces that reaction")
        out["id"] = rid

        owner = spec.get("owner")
        if owner is not None and (not isinstance(owner, str) or not APP.match(owner)):
            raise ReactionError("owner is an app id (lowercase letters, digits and _, at most 24, such as \"companion\"): "
                                "the reaction goes when that app stops; leave it out to keep it until reaction_clear")
        out["owner"] = owner

        hear = spec.get("hear")
        if isinstance(hear, list):
            raise ReactionError("hear is one entry (a string), not a list: set one reaction per source, such as "
                                "\"game:combat.start\"")
        parts = split(hear)
        if not parts:
            raise ReactionError("hear is one entry <source>:<what>, as an app's hear writes it: game:combat (a topic), "
                                "game:combat.start (a kind), game:* (all), route:step, chat:guild, bus:demo.* or a "
                                "source of an extension on this computer such as voice:said (what: lowercase letters, "
                                "digits, _ and dots, or *)")
        out["hear"] = f"{parts[0]}:{parts[1]}"

        out["when"] = self._check_when(spec.get("when"))

        cap = spec.get("call")
        if not isinstance(cap, str) or not CAP.match(cap):
            raise ReactionError("call is a capability id <extension>.<name>, such as \"voice.play\" or \"sense.bags\" "
                                "(the tool wk_voice_play is the capability voice.play)")
        try:
            kind = await maybe(self._kind_of(cap))
        except Exception as e:                   # noqa: BLE001
            raise ReactionError(f"could not tell what kind of capability {cap} is: {message_of(e)}") from None
        if kind is None:
            raise ReactionError(f"there is no capability {cap}: wk_docs lists the capabilities (the tool wk_a_b is the "
                                f"capability a.b); an extension on this computer has its capabilities once it was "
                                f"loaded (extension_load)")
        if kind in ("say", "do"):
            raise ReactionError(f"{cap} is a {kind} capability: it is a proposal the player approves, so a reaction "
                                f"cannot call it; a reaction calls see, keep or point capabilities only")
        if kind not in KINDS:
            raise ReactionError(f"{cap} is a {kind} capability; a reaction calls see, keep or point capabilities only")
        out["call"] = cap

        args = spec.get("args")
        if args is not None and not isinstance(args, dict):
            raise ReactionError("args is an object of the capability's arguments, such as {\"pool\": \"combat_start\"}")
        if args and len(repr(args)) > MAX_ARGS:
            raise ReactionError(f"args are at most about {MAX_ARGS} bytes; keep big things in the extension (a keep "
                                f"capability) and pass a name")
        out["args"] = copy.deepcopy(args) if args else None

        name = spec.get("item")
        if name is not None:
            if not isinstance(name, str) or not ARG.match(name):
                raise ReactionError("item is the name of an argument (letters, digits and _): the whole item heard "
                                    "goes as that argument, such as \"event\"")
            if args and name in args:
                raise ReactionError(f"item names the argument {name}, which args gives too: the item would replace it; "
                                    f"name another argument, or leave {name} out of args")
        out["item"] = name

        cooldown = spec.get("cooldown", DEFAULT_COOLDOWN)
        if cooldown is None:
            cooldown = DEFAULT_COOLDOWN
        if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) or not 0 <= cooldown <= MAX_COOLDOWN:
            raise ReactionError(f"cooldown is seconds between two calls of this reaction, 0 to {MAX_COOLDOWN} "
                                f"(default {DEFAULT_COOLDOWN}; 0: every item that matches calls)")
        out["cooldown"] = cooldown

        note = spec.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE):
            raise ReactionError(f"note is a line of text (at most {MAX_NOTE} characters) saying what the reaction is for")
        out["note"] = " ".join(note.split()) if note else None
        return out

    @staticmethod
    def _check_when(when):
        if when is None:
            return None
        how = ("when is an object of conditions on the item heard, each a dotted path into it and the value it must "
               "have, or a list of values (any of them): {\"data.unit.classification\": [\"elite\", \"rareelite\"], "
               "\"kind\": \"combat.start\"}; all must hold")
        if not isinstance(when, dict):
            raise ReactionError(how)
        if len(when) > MAX_WHEN:
            raise ReactionError(f"when has at most {MAX_WHEN} conditions")
        out = {}
        for path, want in when.items():
            if not isinstance(path, str) or not PATH.match(path):
                raise ReactionError(f"{path!r} is no path: {how}. A path is names (and list positions from 1) joined by "
                                    f"dots, starting from the item: data.<field>, kind, topic, source")
            if isinstance(want, list):
                if not want or len(want) > MAX_VALUES or not all(scalar(w) for w in want):
                    raise ReactionError(f"when.{path}: a list is 1 to {MAX_VALUES} values (text, numbers, true / "
                                        f"false or null), any of which holds")
                out[path] = list(want)
            elif scalar(want):
                out[path] = want
            else:
                raise ReactionError(f"when.{path} is a value (text, a number, true / false or null) or a list of "
                                    f"them, not an object: name the field inside with a longer path")
        return out or None

    @staticmethod
    def _row(r):
        return {k: copy.deepcopy(r[k]) for k in ("id", "owner", "hear", "when", "call", "args", "item", "cooldown",
                                                 "note", "fired", "failed", "last", "error")}
