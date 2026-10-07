"""The companion's side of the link (v0.8): handshake, heartbeats, cumulative acknowledgements, fragments and pings.

on_frame() takes every new frame read off the screen, tick() runs on the clock and writes mailbox packets. The screen
loop is wuxianworkshop/daemon/companion.py; tests drive this class directly. The addon's side is addon/WoWBridge/Link.lua.
What an agent can have the addon do over the link (commands, run / load / watch) is the AgentCommands mixin in
wuxianworkshop/agent/commands.py, which Companion inherits; the interface of Companion is the union of both.

Control frames (frame type 2) carry "KIND;key=value;...":
    HELLO;v=<addon version>;s=<session>;k=<mailbox slot it reads next>;m=<oldest unacknowledged message id>;p=<proc>
          ...;gv=<the client's version, GetBuildInfo>;i=<its ## Interface> (addon 0.9.3 on: on_client() keeps them)
    HB;n=<counter>;k=..;m=..;a=<last ack it read>;q=<messages waiting>
    PONG;id=<ping id>;k=..
HELLO and HB may add bad=<slot>[,<slot>..]: slots the addon skipped because their packet kept failing its checks; the
texts, commands and code written into them go out again. A data message is one frame or several (the frame header's
part / parts); it counts as received when every part is in. Message ids run 1..65535 and wrap; a session remembers the
last RECEIVED_MAX of them. Records the companion writes besides WELCOME / HEARTBEAT / TEXT / COMMAND: PARTS
"<id>:<hex bitmap>" for a message still missing parts (bit i of byte i // 8 = part i received), so that the addon shows
only the missing parts again.
Messages from addon 0.8 on start with a type byte: TYPE_TEXT (what the user sent with /wb send), TYPE_DEBUG ("ERR
<error>\n<stack>" for a Lua error of any addon, "OUT <lines>" print() output, "WARN <warning>", "BLOCKED <event> <addon>
<function>", "DROPPED <n> ..."; a text the addon reported before comes again as "<text>\n(<n> more times)"), TYPE_RUN
("<job> ok ..." / "<job> error ..." for a code() job), TYPE_RELOAD ("asked: ..." / "later: ..." for the reload button) and
TYPE_TEST (the link tests: "BURST DONE ...", "LONG n=<bytes> crc=<crc32 hex>\n<body>" of /wb long, which is checked
against its body, "S <i> crc=<crc32 hex> <body>" of /wb stream along with the order of i, "STREAM DONE ..."). Debug, RUN
and RELOAD texts go to on_debug(kind, text) without WoW's colour and link escapes, kind being the debug word, "RUN" or
"RELOAD". Addons before 0.8 send the same texts untyped, with the kind as a text prefix ("ERR ...", "RUN ...").
"""
import re
import time
import zlib
from collections import deque

from ..agent.commands import AgentCommands, CODE_CHUNK  # noqa: F401  (CODE_CHUNK stays importable from here)
from ..core import frame as F
from ..core import mailbox as MB

VERSION = "0.9.4"
LEGACY_HB = 5.0                 # heartbeat for addons before 0.7.0: they go offline after 15 s without a packet
SLOT_WARN = (400, 100)          # mailbox slots left: tell the agent (debug.log) and the log
WRAP = 65535                    # message ids run 1..WRAP, then start over at 1
RECEIVED_MAX = 32768            # message ids a session remembers (half the ring: no id can come back that soon)
TYPE_TEXT, TYPE_DEBUG, TYPE_RUN, TYPE_RELOAD, TYPE_TEST = range(5)
TYPE_BYTES = {bytes([t]) for t in range(5)}


def parse_control(payload):
    """b"HB;n=3;k=17" -> ("HB", {"n": "3", "k": "17"})"""
    kind, _, rest = payload.decode("utf-8", "replace").partition(";")
    fields = {}
    for part in rest.split(";"):
        key, eq, value = part.partition("=")
        if eq:
            fields[key] = value
    return kind, fields


def _int(fields, key):
    v = fields.get(key, "")
    return int(v) if v.isdigit() else None


def _ints(fields, key):
    """a comma-separated list of numbers, e.g. bad=12,13"""
    return [int(v) for v in fields.get(key, "").split(",") if v.isdigit()]


def _version(text):
    """"0.8.0" -> (0, 8)"""
    return tuple(int(x) for x in re.findall(r"\d+", text)[:2])


def nxt(i):
    """the message id after i (0 = none yet)"""
    return i % WRAP + 1


DEBUG_KINDS = ("ERR", "OUT", "WARN", "BLOCKED", "DROPPED")
LEGACY_KINDS = DEBUG_KINDS + ("RUN", "RELOAD")   # untyped messages of addons before 0.8: the kind is the first word
DEBUG_LABELS = dict(ERR="lua error", OUT="print", WARN="lua warning", BLOCKED="blocked", DROPPED="dropped", RUN="run",
                    RELOAD="reload", INFO="debug")
RESEND = (MB.TEXT, MB.COMMAND, MB.CODE)   # records sent again when the slot that carried them was lost (not pings)
PING_FORGET = 60.0              # seconds after which an unanswered ping is forgotten
_LINKS = re.compile(r"\|H[^|]*\|h(.*?)\|h")
_ESCAPES = re.compile(r"\|c[0-9a-fA-F]{8}|\|r|\|T[^|]*\|t|\|A[^|]*\|a")
_REPEATS = re.compile(r"\n\((\d+) more times\)$")


def plain(text):
    """chat text without colour codes, textures and hyperlink wrappers ("|Hitem:..|h[Name]|h" -> "[Name]")"""
    return _ESCAPES.sub("", _LINKS.sub(r"\1", text)).replace("||", "|")


def bitmap(parts, got):
    b = bytearray((parts + 7) // 8)
    for i in got:
        b[i // 8] |= 1 << (i % 8)
    return b.hex()


def check_long(data):
    """None if the message is not a /wb long test, else (ok, detail)"""
    head, nl, body = data.partition(b"\n")
    if not head.startswith(b"LONG ") or not nl:
        return None
    f = dict(p.split(b"=", 1) for p in head[5:].split() if b"=" in p)
    want_n, want_crc = int(f.get(b"n", b"-1")), f.get(b"crc", b"").decode()
    crc = f"{zlib.crc32(body) & 0xFFFFFFFF:08x}"
    ok = len(data) == want_n and crc == want_crc
    return ok, f"{len(data)} of {want_n} bytes, crc {crc} {'=' if crc == want_crc else '!='} {want_crc}"


def check_stream(data):
    """None if the message is not a /wb stream one, else (i, ok)"""
    if not data.startswith(b"S "):
        return None
    parts = data.split(b" ", 3)
    if len(parts) < 4 or not parts[1].isdigit() or not parts[2].startswith(b"crc="):
        return None
    return int(parts[1]), f"{zlib.crc32(parts[3]) & 0xFFFFFFFF:08x}" == parts[2][4:].decode("ascii", "replace")


class Session:
    """one UI session of the addon (a new one after every /reload)"""

    def __init__(self, sid, hb=LEGACY_HB):
        self.sid = sid
        self.base = None          # the id before the first message we must see (HELLO / HB "m" - 1)
        self.received = set()     # complete messages, the last RECEIVED_MAX ...
        self.order = deque()      # ... in the order they came
        self.partial = {}         # id -> dict(parts, got={part: bytes}, last=time, reported=hex)
        self.ack = 0              # highest id received without gaps (in the order of the ring); 0 = none
        self.ack_sent = None
        self.welcomed = False
        self.hello_at = None
        self.online_at = None     # the first heartbeat after the WELCOME
        self.beats = 0
        self.dups = 0             # frames of messages already complete
        self.dup_parts = 0        # parts received twice
        self.messages = []        # (time, id, bytes, parts, text)
        self.stream_last = 0      # the highest /wb stream number seen
        self.stream_seen = set()
        self.version = None       # the addon's, from its HELLO; None while this companion has seen none
        self.version_text = None  # ... as the HELLO says it ("0.8.0"); version is (major, minor) for comparisons
        self.typed = None         # its messages start with a type byte (addon 0.8 on); None = unknown, the byte decides
        self.hb = hb              # its heartbeat: the companion's own from addon 0.7.0 on (its HELLO says the version)

    def remember(self, msg):
        self.received.add(msg)
        self.order.append(msg)
        if len(self.order) > RECEIVED_MAX:
            self.received.discard(self.order.popleft())

    def advance(self):
        if self.base is None:
            return
        if self.ack == 0:
            self.ack = self.base
        while nxt(self.ack) in self.received:
            self.ack = nxt(self.ack)


class Companion(AgentCommands):
    def __init__(self, addons, clock=time.time, log=print, hb_every=15.0, ack_every=1.0, send_every=0.5,
                 ping_every=10.0, down_after=6.0, parts_after=1.5, stuck_after=3.0, welcome=None, on_debug=None,
                 reload_wait=600.0, echo_debug=True):
        self.addons, self.clock, self.log, self.on_debug = addons, clock, log, on_debug
        self.echo_debug = echo_debug  # False: what goes to on_debug is not also written to log (the daemon journals it once)
        self.hb_every, self.ack_every, self.send_every = hb_every, ack_every, send_every
        self.ping_every, self.down_after, self.parts_after, self.stuck_after = ping_every, down_after, parts_after, stuck_after
        self.reload_wait = reload_wait
        self.welcome = welcome or dict(mode=1, w=64, h=16, bw=128, bh=32, win=16, show=0.25)   # + hb, per session
        self.slot = None              # the next slot to write: unknown until the addon says where it reads
        self.addon_slot = None        # the slot the addon reads next, as its last HELLO / HB / PONG said
        self.addon_slot_since = None  # ... since when it has said so
        self.addon_slot_seen = None   # ... and when it last said so (no control frames while data frames are up)
        self.written = {}             # slot -> the records written into it (put back in the outbox if the slot is lost)
        self.written_at = {}          # slot -> when
        self.last_rewind = None
        self.sessions = {}
        self.current = None           # the session of the latest frame
        self.last_packet = None
        self.last_frame = None
        self.link_up = False
        self.outbox = []              # records for the next packet
        self.ping_id, self.pings, self.rtts, self.last_ping = 0, {}, [], None
        self.events = []
        self.stats = dict(packets=0, bytes=0, frames=0, data=0, dups=0, parts=0, long_ok=0, long_bad=0, stream_ok=0,
                          stream_bad=0, stream_late=0, stream_missing=None,
                          first_slot=None, last_slot=None, records={name: 0 for name in MB.NAMES.values()},
                          debug=dict(err=0, out=0, warn=0, blocked=0, dropped=0, repeats=0, run=0, reload=0, info=0),
                          reloads=0, skipped=0)
        self.full = False
        self.reload_sent = None       # (when, session) of the last "reload" command, until a new session shows up
        self.last_job = 0             # the AgentCommands state: the last code() job id ...
        self.slots_warned = set()
        self.watched, self.watch_pending, self.watch_checked = {}, {}, 0.0   # ... and path -> (mtime, addon); see watch()
        self.before_load = None       # (addon, path) -> None, before a watched file that was saved is loaded again
        self.on_client = None         # (client version, Interface) -> None, from a HELLO that says them
        self.client = None            # (client version, Interface) as the last HELLO said them

    def note(self, text, echo=True):
        self.events.append((round(self.clock(), 3), text))
        if echo:
            self.log(text)

    def _echo(self):
        """whether a note that also goes to on_debug is written to log as well"""
        return self.echo_debug or not self.on_debug

    def new_process(self, stamp):
        """a game process this companion has not seen: the slots it wrote before are free again"""
        n = MB.reset(self.addons)
        MB.write_proc(self.addons, stamp)
        self.slot, self.addon_slot, self.sessions, self.current, self.full = None, None, {}, None, False
        self.written, self.written_at, self.slots_warned = {}, {}, set()
        self.note(f"game process {stamp}: {n} used mailbox slots emptied, proc.ttf written")

    def say(self, text):
        self.outbox.append((MB.TEXT, text.encode()))

    def _tell(self, kind, text):
        """a note for the log that the agent also finds in debug.log"""
        self.note(text, self._echo())
        if self.on_debug:
            self.on_debug(kind, text)

    def _reload_note(self, text):
        self.note(text, self._echo())
        if self.on_debug:
            self.on_debug("RELOAD", text)

    def on_frame(self, ftype, sid, msg, payload, part=0, parts=1):
        now = self.clock()
        self.stats["frames"] += 1
        self.last_frame = now
        if not self.link_up:
            self.link_up = True
            self.note("link up: frames are visible")
        self.current = sid
        s = self.sessions.get(sid)
        if s is None:
            # a session first seen without its HELLO (this companion started while the addon ran on) is an addon from
            # 0.7 on, older ones are gone: the companion's own heartbeat. A HELLO from an older one lowers it. (With
            # LEGACY_HB here a restarted daemon wrote a packet every 5 s: 12 mailbox slots a minute instead of 4.)
            s = self.sessions[sid] = Session(sid, hb=self.hb_every)
        if ftype == F.TYPE_CONTROL:
            self._control(s, payload, now)
        elif ftype == F.TYPE_DATA:
            self._data(s, msg, bytes(payload), part, parts, now)

    def _control(self, s, payload, now):
        kind, f = parse_control(payload)
        k, m = _int(f, "k"), _int(f, "m")
        if k is not None:
            if k != self.addon_slot:
                self.addon_slot, self.addon_slot_since = k, now
            self.addon_slot_seen = now
            if self.slot is None:
                self.slot = k
            elif k > self.slot:                     # nothing unread waits below the slot the addon reads
                self._skipped(range(self.slot, k), f"it reads slot {k}, the next to write was {self.slot}")
                self.slot = k
            elif k < self.slot and k not in self.written:
                self._start_over(s, k)
        bad = _ints(f, "bad")
        if bad:
            self._skipped(bad, "damaged, as its control frame says")
        if m is not None and s.base is None:
            s.base = m - 1
            s.advance()
        if kind == "HELLO" and s.hello_at is None:
            s.hello_at = now
            s.version = _version(f.get("v", ""))
            s.version_text = f.get("v") or None
            s.hb = self.hb_every if s.version >= (0, 7) else LEGACY_HB
            s.typed = s.version >= (0, 8)
            gv, gi = f.get("gv") or None, _int(f, "i")
            client = f", client {gv} (Interface {gi})" if gv and gi else ""
            self.note(f"HELLO from session {s.sid}: addon {f.get('v')}{client}, reads slot {k}, next message {m}, proc {f.get('p')}")
            if gv and gi:
                self.client = (gv, gi)
                if self.on_client is not None:
                    try:
                        self.on_client(gv, gi)
                    except Exception as e:                # a full disk must not stop the link
                        self.note(f"could not keep client {gv}: {e}")
            if self.reload_sent and s.sid != self.reload_sent[1]:
                self.stats["reloads"] += 1
                self._reload_note(f"reload done: session {self.reload_sent[1]} -> {s.sid}, "
                                  f"{now - self.reload_sent[0]:.1f} s after the command")
                self.reload_sent = None
        if kind in ("HELLO", "HB") and not s.welcomed:
            s.welcomed = True                       # also after a companion restart: the addon may still be online
            params = ";".join(f"{a}={b}" for a, b in dict(self.welcome, hb=int(s.hb)).items())
            self.outbox.insert(0, (MB.WELCOME, f"v={VERSION};s={s.sid};{params}".encode()))
        if kind == "HB":
            s.beats += 1
            if s.online_at is None:
                s.online_at = now                   # pings start; also for a session that was online before we started
                self.note(f"session {s.sid} online, {now - s.hello_at:.1f} s after its HELLO" if s.hello_at is not None
                          else f"session {s.sid} online (it was before this companion started)")
        elif kind == "PONG":
            sent = self.pings.pop(f.get("id"), None)
            if sent is not None:
                self.rtts.append(round(now - sent, 3))
                self.note(f"ping {f.get('id')}: {now - sent:.2f} s round trip")

    def _data(self, s, msg, payload, part, parts, now):
        if msg in s.received:
            s.dups += 1
            self.stats["dups"] += 1
            return
        p = s.partial.setdefault(msg, dict(parts=parts, got={}, last=now, first=now, reported=None))
        if part in p["got"]:
            s.dup_parts += 1
            return
        p["got"][part] = payload
        p["last"] = now
        self.stats["parts"] += 1
        if len(p["got"]) < p["parts"]:
            return
        del s.partial[msg]
        data = b"".join(p["got"][i] for i in range(p["parts"]))
        s.remember(msg)
        self.stats["data"] += 1
        mtype, data = self._split(s, data)
        text = data.decode("utf-8", "replace")
        s.messages.append((round(now, 3), msg, len(data), p["parts"], text))
        s.advance()
        if mtype is None:                                   # an addon before 0.8: the kind is the first word
            kind = text.split(" ", 1)[0]
            if kind in LEGACY_KINDS:
                self._debug(kind, plain(text[len(kind) + 1:]).rstrip())
            else:
                self._test(s, msg, data, text, p, now)
        elif mtype == TYPE_DEBUG:
            kind, _, body = text.partition(" ")
            if kind in DEBUG_KINDS:
                self._debug(kind, plain(body).rstrip())
            else:
                self._debug("INFO", plain(text).rstrip())
        elif mtype == TYPE_RUN:
            self._debug("RUN", plain(text).rstrip())
        elif mtype == TYPE_RELOAD:
            self._debug("RELOAD", plain(text).rstrip())
        elif mtype == TYPE_TEST:
            self._test(s, msg, data, text, p, now)
        else:
            extra = f", {p['parts']} parts" if p["parts"] > 1 else ""
            self.note(f"#{msg} ({len(data)} B{extra}): {text[:80]}")

    def _split(self, s, data):
        """(type, body) of a message: addon 0.8 on puts the type in the first byte, older ones send text (None); for a
        session whose HELLO this companion did not see, a leading control byte decides"""
        typed = s.typed if s.typed is not None else data[:1] in TYPE_BYTES
        if typed and data:
            return data[0], data[1:]
        return None, data

    def _test(self, s, msg, data, text, p, now):
        """the link tests (/wb burst, long, stream) and anything else that is only logged"""
        stream = check_stream(data)
        if stream is not None:
            i, ok = stream
            self.stats["stream_ok" if ok else "stream_bad"] += 1
            if i < s.stream_last:
                self.stats["stream_late"] += 1             # shown again after an outage: late, not lost
            s.stream_last = max(s.stream_last, i)
            s.stream_seen.add(i)
            if not ok:
                self.note(f"stream #{i} (message {msg}) DAMAGED: CRC mismatch, {len(data)} B, {p['parts']} parts")
            elif i % 50 == 0:
                self.note(f"stream #{i}: {self.stats['stream_ok']} ok, {self.stats['stream_bad']} damaged, "
                          f"{self.stats['stream_late']} arrived late")
            return
        if data.startswith(b"STREAM DONE n="):
            n = int(data[14:].split(b" ")[0])
            missing = sorted(set(range(1, n + 1)) - s.stream_seen)
            self.stats["stream_missing"] = len(missing)
            self.note(f"#{msg}: {data.decode()} | companion: {len(s.stream_seen)} of {n} received intact, "
                      f"{self.stats['stream_bad']} damaged, {self.stats['stream_late']} late, missing {missing[:10] or 'none'}")
            return
        long = check_long(data)
        if long is not None:
            self.stats["long_ok" if long[0] else "long_bad"] += 1
            self.note(f"#{msg} LONG {'ok' if long[0] else 'DAMAGED'}: {long[1]}, {p['parts']} parts in {now - p['first']:.1f} s")
        else:
            extra = f", {p['parts']} parts" if p["parts"] > 1 else ""
            self.note(f"#{msg} ({len(data)} B{extra}): {text[:80]}")

    def _debug(self, kind, body):
        repeat = _REPEATS.search(body)
        self.stats["debug"]["repeats" if repeat else kind.lower()] += 1
        label, echo = DEBUG_LABELS[kind], self._echo()
        if kind == "OUT":
            for line in body.split("\n"):
                self.note(f"[{label}] {line}", echo)
        else:
            self.note(f"[{label}] {body}", echo)
        if self.on_debug:
            self.on_debug(kind, body)

    def _parts_records(self, s, now, only_stale):
        """PARTS records for incomplete messages (oldest first); only_stale: just those quiet for parts_after seconds
        whose state was not reported yet"""
        out = []
        for msg in sorted(s.partial)[:4]:
            p = s.partial[msg]
            bits = bitmap(p["parts"], p["got"])
            if only_stale and (now - p["last"] < self.parts_after or bits == p["reported"]):
                continue
            out.append((msg, bits))
        return out

    def _resend(self, records):
        """the records of slots the addon did not read that go out again: texts, commands and code, but not the pings
        (forgotten here): after an outage every heartbeat's ping would come back at once, each with the whole outage
        as its round trip, and the answers would hold up everything else the addon sends"""
        out = []
        for kind, data in records:
            if kind not in RESEND:
                continue
            if kind == MB.COMMAND and data.startswith(b"ping "):
                self.pings.pop(data[5:].decode("ascii", "replace"), None)
                continue
            out.append((kind, data))
        return out

    def _start_over(self, s, k):
        """the addon now reads from slot k, below every slot we wrote. At the first login in a new game process it
        reports the slot saved in the last process until it has read proc.ttf, then starts over at slot 1. Our slots
        from k up are unread: empty them (the addon would read them out of turn later), send their texts and commands
        again, and welcome the session again from slot k"""
        ahead = sorted(i for i in self.written if i >= k)
        lost = self._resend(r for i in ahead for r in self.written[i])
        for i in ahead:
            MB.clear_slot(self.addons, i)
            del self.written[i]
            self.written_at.pop(i, None)
        self.note(f"the addon reads from slot {k} now, below the slots written so far: {len(ahead)} unread slots emptied, "
                  f"writing from slot {k}, {len(lost)} texts / commands sent again")
        self.outbox = lost + self.outbox
        self.slot = k
        s.welcomed = False

    def _skipped(self, slots, why):
        """slots the addon went past without reading them: it names a slot whose packet kept failing its checks in its
        control frames (bad=), and the slot it reads may lie above the next one to write. The texts, commands and code
        those slots carried go out again, once (a slot sent again is forgotten here)"""
        lost, gone = [], []
        for i in slots:
            if i in self.written:
                lost += self._resend(self.written.pop(i))
                self.written_at.pop(i, None)
                gone.append(i)
        if not gone:
            return
        self.stats["skipped"] += len(gone)
        self.note(f"the addon skipped slot{'s' if len(gone) > 1 else ''} {', '.join(map(str, gone))} ({why}): "
                  f"{len(lost)} texts / commands / code parts sent again")
        self.outbox = lost + self.outbox
        self.last_rewind = self.clock()                 # the addon has just moved on: no rewind on top of this

    def _rewind(self, now):
        """the addon still waits on a slot stuck_after seconds after we wrote it (reading takes about one): that slot is
        lost (emptied, damaged, skipped), so write again from there, with the texts and commands it carried. Only on a
        recent report: while data frames are up the addon shows no control frames, and an old slot number means nothing"""
        k = self.addon_slot
        w = self.written_at.get(k)
        if k is None or k >= self.slot or w is None or now - self.addon_slot_seen > 2.5:
            return
        if self.addon_slot_seen - w < self.stuck_after:     # its latest report may predate the slot being readable
            return
        if self.last_rewind is not None and now - self.last_rewind < self.stuck_after:
            return
        lost = self._resend(r for i in range(k, self.slot) for r in self.written.pop(i, []))
        self.note(f"the addon still waits on slot {k} {now - w:.0f} s after it was written: writing again from slot {k} "
                  f"(up to {self.slot - 1}), {len(lost)} texts / commands sent again")
        self.outbox = lost + self.outbox
        self.slot, self.last_rewind = k, now

    def tick(self):
        now = self.clock()
        if self.reload_sent and now - self.reload_sent[0] > self.reload_wait:
            self._reload_note(f"no reload {self.reload_wait:.0f} s after the command (the button was not clicked)")
            self.reload_sent = None
        if self.link_up and now - self.last_frame > self.down_after:
            self.link_up = False
            self.note(f"link down: no frame for {self.down_after:.0f} s (covered, minimized, a loading screen, or the addon is off)")
        self._check_watch(now)
        s = self.sessions.get(self.current)
        if self.slot is None or s is None or not s.welcomed:
            return
        if not self.link_up:        # no frames: the addon is not reading now (the character screen, minimized, a long
            return                  # loading screen); packets written meanwhile would only use slots up and leave it a
                                    # backlog to read through when it is back. What waits goes out once frames come
        self._rewind(now)
        since = None if self.last_packet is None else now - self.last_packet
        stale = self._parts_records(s, now, only_stale=True)
        due = (self.outbox and (since is None or since >= self.send_every)) \
            or ((s.ack != s.ack_sent or stale) and (since is None or since >= self.ack_every)) \
            or since is None or since >= s.hb
        if not due:
            return
        if self.slot > MB.POOL:
            if not self.full:
                self.full = True
                self._tell("SLOTS", f"mailbox full: all {MB.POOL} slots of this game process are used, nothing reaches the "
                                    f"addon any more; only a full restart of the game (not a /reload) makes them usable again")
            return
        if s.online_at is not None and (self.last_ping is None or now - self.last_ping >= self.ping_every):
            self.pings = {i: t for i, t in self.pings.items() if now - t < PING_FORGET}
            self.ping_id += 1                       # rides along with a packet that is going out anyway
            self.last_ping = now
            self.pings[str(self.ping_id)] = now
            self.command(f"ping {self.ping_id}")
        records, size = [], MB.HEAD.size + 4
        for msg, bits in self._parts_records(s, now, only_stale=False):
            rec = (MB.PARTS, f"{msg}:{bits}".encode())
            if size + 3 + len(rec[1]) <= MB.MAX_PACKET:
                records.append(rec)
                size += 3 + len(rec[1])
                s.partial[msg]["reported"] = bits
        while self.outbox and size + 3 + len(self.outbox[0][1]) <= MB.MAX_PACKET:
            size += 3 + len(self.outbox[0][1])
            records.append(self.outbox.pop(0))
        if not records:
            records = [(MB.HEARTBEAT, f"t={int(now * 1000)}".encode())]
        n = MB.write_slot(self.addons, self.slot, s.sid, s.ack, records)
        self.written[self.slot], self.written_at[self.slot] = records, now
        s.ack_sent = s.ack
        self.stats["packets"] += 1
        self.stats["bytes"] += n
        self.stats["first_slot"] = self.stats["first_slot"] or self.slot
        self.stats["last_slot"] = self.slot
        for t, _ in records:
            self.stats["records"][MB.NAMES.get(t, str(t))] += 1
        self.slot += 1
        self.last_packet = now
        left = MB.POOL - self.slot + 1
        crossed = {n for n in SLOT_WARN if left <= n} - self.slots_warned
        if left <= 0:
            self.full = True
            self._tell("SLOTS", f"mailbox full: all {MB.POOL} slots of this game process are used, nothing reaches the "
                                f"addon any more; only a full restart of the game (not a /reload) makes them usable again")
        elif crossed:
            self.slots_warned |= crossed
            self._tell("SLOTS", f"mailbox: {left} slots left in this game process (about {left * s.hb / 3600:.1f} h at a "
                                f"heartbeat every {s.hb:.0f} s); when they are gone, only a full restart of the game brings "
                                f"them back (the addon says so in the chat too)")

    def report(self):
        rtts = sorted(self.rtts)
        return dict(
            stats=self.stats, events=self.events,
            ping=dict(n=len(rtts), min=rtts[0] if rtts else None, avg=round(sum(rtts) / len(rtts), 3) if rtts else None,
                      p50=rtts[len(rtts) // 2] if rtts else None, p95=rtts[int(0.95 * (len(rtts) - 1))] if rtts else None,
                      max=rtts[-1] if rtts else None, all=rtts),
            sessions=[dict(session=s.sid, version=s.version_text, hello_at=s.hello_at,
                           online_at=s.online_at,
                           handshake=round(s.online_at - s.hello_at, 3) if s.online_at and s.hello_at else None,
                           beats=s.beats, messages=len(s.messages), dups=s.dups, dup_parts=s.dup_parts, ack=s.ack,
                           base=s.base, incomplete=sorted(s.partial),
                           last=[dict(id=i, bytes=n, parts=k, text=t[:200]) for _, i, n, k, t in s.messages[-5:]])
                      for s in self.sessions.values()])
