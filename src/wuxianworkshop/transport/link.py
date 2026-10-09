"""The companion's side of the link (v0.9.6): handshake, heartbeats, cumulative acknowledgements, fragments and pings.

on_frame() takes every new frame read off the screen, tick() runs on the clock and writes mailbox packets. The screen
loop is wuxianworkshop/daemon/companion.py; tests drive this class directly. The addon's side is addon/WoWBridge/Link.lua.
What an agent can have the addon do over the link (commands, run / load / watch) is the AgentCommands mixin in
wuxianworkshop/agent/commands.py, which Companion inherits; the interface of Companion is the union of both.

Control frames (frame type 2) carry "KIND;key=value;...":
    HELLO;v=<addon version>;s=<session>;k=<mailbox slot it reads next>;m=<oldest unacknowledged message id>;p=<proc>
          ...;gv=<the client's version, GetBuildInfo>;i=<its ## Interface> (addon 0.9.3 on: on_client() keeps them)
          ...;f=<link features>;j=<the newest agent job it ran> (0.9.6 on)
    HB;n=<counter>;k=..;m=..;l=<the id up to which every message has gone up at least once (0.9.6 on)>;q=<waiting>
    PONG;id=<ping id>;k=..;f=..
HELLO and HB may add bad=<slot>[,<slot>..]: slots the addon skipped because their packet kept failing its checks; the
texts, commands and code written into them go out again. A data message is one frame or several (the frame header's
part / parts); it counts as received when every part is in. Message ids run 1..65535 and wrap; a session remembers the
last RECEIVED_MAX of them. Everything below an addon's oldest message (m=) has left its queue, so the ack moves past it.
Records the companion writes besides WELCOME / HEARTBEAT / TEXT / COMMAND: PARTS "<id>:<hex bitmap>" for a message still
missing parts (bit i of byte i // 8 = part i received; no bits: none of it came), so that the addon shows only the
missing parts again; CODE (agent/commands.py code) and, to an addon with link features DATA (f=2, 0.9.7), CALL: a data
call (agent/commands.py data_call).
Acknowledgements: an addon before 0.9.6 shows a message again 4 s after it went up unless acknowledged, so the ack goes
out within ack_every (1 s) of a message coming. An addon with link features LAZY (f=1, 0.9.6) takes rto, a longer
window and pack=1 from its WELCOME: the companion then acknowledges ack_lazy seconds after the first message it has not
acknowledged came (while messages flow, a packet every ack_lazy seconds instead of every second: a game process has 4,096
mailbox slots), and asks at once (PARTS) for the messages it knows went up but misses, which the addon then shows again
at once: a message that is not a RUN result or RELOAD report goes up after every lower id, and HB l= says up to where
everything went up. A link test (TYPE_TEST) is acknowledged within ack_every: its latency is what it measures.
Messages from addon 0.8 on start with a type byte: TYPE_TEXT (what the user sent with /wb send), TYPE_DEBUG ("ERR
<error>\n<stack>" for a Lua error of any addon, "OUT <lines>" print() output, "WARN <warning>", "BLOCKED <event> <addon>
<function>", "DROPPED <n> ..."; a text the addon reported before comes again as "<text>\n(<n> more times)"), TYPE_RUN
("<job> ok ..." / "<job> error ..." for a code() job), TYPE_RELOAD ("asked: ..." / "later: ..." for the reload button),
TYPE_TEST (the link tests: "BURST DONE ...", "LONG n=<bytes> crc=<crc32 hex>\n<body>" of /wb long, which is checked
against its body, "S <i> crc=<crc32 hex> <body>" of /wb stream along with the order of i, "STREAM DONE ..."),
TYPE_EVENT (an addon's event, WoWBridge API.lua: {"a": addon, "t": topic, "d": data[, "x": dropped]} as JSON) and, from
0.9.6, TYPE_BATCH: several small messages of one kind in one, each as its type (1 byte), length (2) and text. Debug, RUN
and RELOAD texts go to on_debug(kind, text) without WoW's colour and link escapes, kind being the debug word, "RUN" or
"RELOAD"; an event goes to on_debug("EVENT", <its JSON as it came>). Addons before 0.8 send the same texts untyped, with
the kind as a text prefix ("ERR ...", "RUN ...").
"""
import re
import time
import zlib
from collections import deque

from ..agent.commands import AgentCommands, CODE_CHUNK  # noqa: F401  (CODE_CHUNK stays importable from here)
from ..core import frame as F
from ..core import mailbox as MB

VERSION = "0.9.7"
LEGACY_HB = 5.0                 # heartbeat for addons before 0.7.0: they go offline after 15 s without a packet
SLOT_WARN = (400, 100)          # mailbox slots left: tell the agent (debug.log) and the log
WRAP = 65535                    # message ids run 1..WRAP, then start over at 1
RECEIVED_MAX = 32768            # message ids a session remembers (half the ring: no id can come back that soon)
TYPE_TEXT, TYPE_DEBUG, TYPE_RUN, TYPE_RELOAD, TYPE_TEST, TYPE_EVENT, TYPE_BATCH = range(7)
TYPE_BYTES = {bytes([t]) for t in range(7)}
URGENT_TYPES = (TYPE_RUN, TYPE_RELOAD)   # the addon shows these before the others: a later id may come before them
LAZY = 1                        # the link features (f=) from which the acks go lazily and PARTS ask at once (0.9.6)
DATA = 2                        # ... from which the addon takes CALL records: data calls (0.9.7)
NACK_AGAIN = 5.0                # a message asked for is asked for again after this long while it is still missing
NACK_SPAN = 1024                # ids past the ack looked through for missing ones, at most
PARTS_MAX = 8                   # PARTS records in one packet, at most
WELCOME_AGAIN = 5.0             # a HELLO past the slot of its session's WELCOME gets one again, this long after it at most
SESSIONS_KEPT = 8               # sessions remembered (a /reload makes a new one)
NOTES_KEPT = 2000               # notes kept for the report
MESSAGES_KEPT = 50              # messages a session keeps for the report
WRITTEN_KEPT = 512              # written slots remembered (their records go out again when the addon skips one)


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


def prev(i):
    """the message id before i (65535 before 1)"""
    return (i - 2) % WRAP + 1


def ahead(a, b):
    """id b comes after id a on the ring (by less than half of it)"""
    return 0 < (b - a) % WRAP < RECEIVED_MAX


def newer(a, b):
    """the later of two ids on the ring; a may be None"""
    return b if a is None or ahead(a, b) else a


def unbatch(body):
    """the messages of a TYPE_BATCH one: [(type, bytes)], each written as its type (1 byte), length (2) and text"""
    out, i = [], 0
    while i + 3 <= len(body):
        n = int.from_bytes(body[i + 1:i + 3], "big")
        out.append((body[i], body[i + 3:i + 3 + n]))
        i += 3 + n
    return out


DEBUG_KINDS = ("ERR", "OUT", "WARN", "BLOCKED", "DROPPED")
LEGACY_KINDS = DEBUG_KINDS + ("RUN", "RELOAD")   # untyped messages of addons before 0.8: the kind is the first word
DEBUG_LABELS = dict(ERR="lua error", OUT="print", WARN="lua warning", BLOCKED="blocked", DROPPED="dropped", RUN="run",
                    RELOAD="reload", INFO="debug")
RESEND = (MB.TEXT, MB.COMMAND, MB.CODE, MB.CALL, MB.WELCOME)   # sent again when the slot that carried them was lost
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
        self.welcomed_at = None   # when its WELCOME was put in the outbox ...
        self.welcome_slot = None  # ... and the slot that carried it
        self.hello_at = None
        self.online_at = None     # the first heartbeat after the WELCOME
        self.beats = 0
        self.dups = 0             # frames of messages already complete
        self.dup_parts = 0        # parts received twice
        self.messages = deque(maxlen=MESSAGES_KEPT)   # (time, id, bytes, parts, text), the last ones
        self.stream_last = 0      # the highest /wb stream number seen
        self.stream_seen = set()
        self.version = None       # the addon's, from its HELLO; None while this companion has seen none
        self.version_text = None  # ... as the HELLO says it ("0.8.0"); version is (major, minor) for comparisons
        self.typed = None         # its messages start with a type byte (addon 0.8 on); None = unknown, the byte decides
        self.hb = hb              # its heartbeat: the companion's own from addon 0.7.0 on (its HELLO says the version)
        self.features = 0         # its link features (f= of its HELLO or a PONG); 0: before 0.9.6, or not said yet
        self.newest = None        # the latest id received (ring order)
        self.top = None           # the latest id known to have gone up after every lower one (see the module docstring)
        self.nacked = {}          # id -> when a PARTS record last asked for it
        self.unacked_at = None    # when the ack first moved past the one last written
        self.ack_soon = False     # a link test came: acknowledged within ack_every

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

    def skip_to(self, last):
        """the addon's oldest message is the one after `last`: every id up to it has left its queue (acknowledged, or
        dropped from a queue nobody acknowledged), so the ack moves there and on"""
        if self.base is not None and ahead(self.ack, last):
            self.ack = last
            self.advance()

    def tidy(self):
        """partial messages and asks the ack has passed are forgotten"""
        for msg in [m for m in self.partial if not ahead(self.ack, m)]:
            del self.partial[msg]
        for msg in [m for m in self.nacked if not ahead(self.ack, m)]:
            del self.nacked[msg]


class Companion(AgentCommands):
    def __init__(self, addons, clock=time.time, log=print, hb_every=15.0, ack_every=1.0, send_every=0.5,
                 ping_every=10.0, down_after=6.0, parts_after=1.5, stuck_after=3.0, welcome=None, on_debug=None,
                 reload_wait=600.0, echo_debug=True, hb_lazy=30.0, ack_lazy=10.0, rto_lazy=15, win_lazy=64, show_lazy=0.2):
        self.addons, self.clock, self.log, self.on_debug = addons, clock, log, on_debug
        self.echo_debug = echo_debug  # False: what goes to on_debug is not also written to log (the daemon journals it once)
        self.hb_every, self.ack_every, self.send_every = hb_every, ack_every, send_every
        self.ping_every, self.down_after, self.parts_after, self.stuck_after = ping_every, down_after, parts_after, stuck_after
        # what a LAZY addon is told: measured in the game (1.60.1.70245, 30 fps in the background) a frame shown for at
        # least 0.2 s was read every time (0.15 s too, in fewer rounds); a frame missed now and then is asked for at once
        self.hb_lazy, self.ack_lazy, self.rto_lazy, self.win_lazy = hb_lazy, ack_lazy, rto_lazy, win_lazy
        self.show_lazy = show_lazy
        self.reload_wait = reload_wait
        self.welcome = welcome or dict(mode=1, w=64, h=16, bw=128, bh=32, win=16, show=0.25)   # + hb, per session
        self.slot = None              # the next slot to write: unknown until the addon says where it reads
        self.addon_slot = None        # the slot the addon reads next, as its last HELLO / HB / PONG said
        self.addon_slot_since = None  # ... since when it has said so
        self.addon_slot_seen = None   # ... and when it last said so (no control frames while data frames are up)
        self.written = {}             # slot -> the records written into it (put back in the outbox if the slot is lost)
        self.written_at = {}          # slot -> when
        self.written_as = {}          # slot -> (session, ack) it was written with: a rewind writes the same bytes again
        self.last_rewind = None
        self.sessions = {}
        self.current = None           # the session of the latest frame
        self.last_packet = None
        self.last_frame = None
        self.link_up = False
        self.outbox = []              # records for the next packet
        self.ping_id, self.pings, self.rtts, self.last_ping = 0, {}, [], None
        self.events = deque(maxlen=NOTES_KEPT)
        self.packet_times = deque(maxlen=600)   # when the latest packets went out: the slot warnings' rate
        self.stats = dict(packets=0, bytes=0, frames=0, data=0, dups=0, parts=0, long_ok=0, long_bad=0, stream_ok=0,
                          stream_bad=0, stream_late=0, stream_missing=None,
                          first_slot=None, last_slot=None, records={name: 0 for name in MB.NAMES.values()},
                          debug=dict(err=0, out=0, warn=0, blocked=0, dropped=0, repeats=0, run=0, reload=0, info=0), events=0,
                          reloads=0, skipped=0, batches=0, asked=0, bad_parts=0, dropped_records=0)
        self.full = False
        self.reload_sent = None       # (when, session) of the last "reload" command, until a new session shows up
        self.last_job = 0             # the AgentCommands state: the last code() job id ...
        self.slots_warned = set()
        self.watched, self.watch_pending, self.watch_checked = {}, {}, 0.0   # ... and path -> (mtime, addon); see watch()
        self.watch_jobs = {}          # ... path -> the job of its last load (a newer save takes its place while unsent)
        self.before_load = None       # (addon, path) -> None, before a watched file that was saved is loaded again
        self.on_client = None         # (client version, Interface) -> None, from a HELLO that says them
        self.on_session = None        # (session, the newest job it ran or None) -> None, for a new session's HELLO
        self.client = None            # (client version, Interface) as the last HELLO said them
        self.dispatching = None       # (session, message id) while a message is handed on

    def note(self, text, echo=True):
        self.events.append((round(self.clock(), 3), text))
        if echo:
            self.log(text)

    def _echo(self):
        """whether a note that also goes to on_debug is written to log as well"""
        return self.echo_debug or not self.on_debug

    def _forget(self):
        """what this companion knew of a game process that is gone, and the commands and code that waited for it (a
        later process must not run them); returns how many of those"""
        stale = [r for r in self.outbox if r[0] in (MB.CODE, MB.CALL, MB.COMMAND, MB.WELCOME)]
        self.outbox = [r for r in self.outbox if r[0] not in (MB.CODE, MB.CALL, MB.COMMAND, MB.WELCOME)]
        self.slot, self.addon_slot, self.sessions, self.current, self.full = None, None, {}, None, False
        self.written, self.written_at, self.written_as, self.slots_warned = {}, {}, {}, set()
        self.pings, self.reload_sent, self.watch_jobs = {}, None, {}
        return len([r for r in stale if r[0] != MB.WELCOME])

    def new_process(self, stamp):
        """a game process this companion has not seen: the slots it wrote before are free again"""
        n = MB.reset(self.addons)
        MB.write_proc(self.addons, stamp)
        dropped = self._forget()
        self.note(f"game process {stamp}: {n} used mailbox slots emptied, proc.ttf written"
                  + (f", {dropped} commands / code parts for the last one dropped" if dropped else ""))

    def game_gone(self):
        """the game process has exited, and with it every font its client had cached: every used slot is emptied (a game
        started later, while no companion runs, must not read what this one never did) and proc.ttf says "?" (no known
        process: the next game's addon starts at slot 1, and takes the stamp a companion writes later as its own); the
        commands and code that waited for this game are dropped (texts wait for the next one)"""
        n = MB.reset(self.addons)
        MB.write_proc(self.addons, "?")
        dropped = self._forget()
        self.note(f"the game has exited: {n} used mailbox slots emptied"
                  + (f", {dropped} commands / code parts dropped" if dropped else ""))

    def say(self, text):
        """a text for the chat; one longer than a record takes goes in pieces (between UTF-8 characters)"""
        data = text.encode()
        while data:
            cut = min(len(data), MB.RECORD_MAX)
            while cut < len(data) and cut > 0 and (data[cut] & 0xC0) == 0x80:
                cut -= 1
            self.outbox.append((MB.TEXT, data[:cut]))
            data = data[cut:]

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
            while len(self.sessions) > SESSIONS_KEPT:            # one per /reload: the oldest go
                del self.sessions[next(iter(self.sessions))]
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
        if m is not None:
            if s.base is None:
                s.base = m - 1
                s.advance()
            else:
                s.skip_to(prev(m))
            s.tidy()
        shown = _int(f, "l")
        if shown is not None and s.base is not None and 0 < (shown - s.ack) % WRAP <= NACK_SPAN:
            s.top = newer(s.top, shown)
        features = _int(f, "f") or 0
        if kind == "HELLO" and s.hello_at is None:
            s.hello_at = now
            s.version = _version(f.get("v", ""))
            s.version_text = f.get("v") or None
            s.features = features
            s.hb = self.hb_lazy if features >= LAZY else self.hb_every if s.version >= (0, 7) else LEGACY_HB
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
            if self.on_session is not None:
                try:
                    self.on_session(s.sid, _int(f, "j"))
                except Exception as e:
                    self.note(f"new session {s.sid}: {e!r}")
        elif features > s.features:                 # a PONG says what it can do (a session seen without its HELLO)
            s.features = features
            if features >= LAZY:
                s.hb = self.hb_lazy
            if s.welcomed:
                self._welcome(s, now)               # what it can do from now on
        if kind == "HB" and not s.welcomed:
            self._welcome(s, now)                   # also after a companion restart: the addon may still be online
        elif kind == "HELLO" and (not s.welcomed or (k is not None and s.welcome_slot is not None and k > s.welcome_slot
                                                     and now - s.welcomed_at >= WELCOME_AGAIN)):
            self._welcome(s, now)                   # read past its WELCOME and still saying HELLO: offline, or a new
                                                    # UI session under the same id
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
                del self.rtts[:-500]
                self.note(f"ping {f.get('id')}: {now - sent:.2f} s round trip")

    def _welcome(self, s, now):
        """the session's WELCOME at the head of the outbox (one waits at a time): frame sizes, mode, window, display time
        and heartbeat; for an addon with LAZY features also rto and pack, and its own window, display time and heartbeat"""
        s.welcomed, s.welcomed_at = True, now
        params = dict(self.welcome, hb=int(s.hb))
        if s.features >= LAZY:
            params.update(win=self.win_lazy, show=self.show_lazy, rto=self.rto_lazy, pack=1)
        text = f"v={VERSION};s={s.sid};" + ";".join(f"{a}={b}" for a, b in params.items())
        self.outbox = [r for r in self.outbox if r[0] != MB.WELCOME]
        self.outbox.insert(0, (MB.WELCOME, text.encode()))

    def _data(self, s, msg, payload, part, parts, now):
        if msg in s.received:
            s.dups += 1
            self.stats["dups"] += 1
            return
        if not 0 <= part < parts:
            self.stats["bad_parts"] += 1
            return
        p = s.partial.get(msg)
        if p is None or p["parts"] != parts:               # new, or another message under this id (one from before a wrap)
            p = s.partial[msg] = dict(parts=parts, got={}, last=now, first=now, reported=None)
        if part in p["got"]:
            s.dup_parts += 1
            return
        p["got"][part] = payload
        p["last"] = now
        self.stats["parts"] += 1
        if len(p["got"]) < p["parts"]:
            return
        del s.partial[msg]
        s.nacked.pop(msg, None)
        data = b"".join(p["got"][i] for i in range(p["parts"]))
        s.remember(msg)
        s.newest = newer(s.newest, msg)
        s.advance()
        s.tidy()
        if s.ack != s.ack_sent and s.unacked_at is None:
            s.unacked_at = now
        mtype, body = self._split(s, data)
        if mtype is not None and mtype not in URGENT_TYPES:
            s.top = newer(s.top, msg)                      # it went up after every lower id
        self.dispatching = (s.sid, msg)
        try:
            if mtype == TYPE_BATCH:
                self.stats["batches"] += 1
                for t, text in unbatch(body):
                    self._message(s, msg, t, text, p, now)
            else:
                self._message(s, msg, mtype, body, p, now)
        except Exception as e:                             # handed on wrong: said, and the link goes on
            self.note(f"message {msg} of session {s.sid} could not be handed on: {e!r}")
        finally:
            self.dispatching = None

    def _message(self, s, msg, mtype, data, p, now):
        """one message (or one of a batch), handed on by its type"""
        self.stats["data"] += 1
        text = data.decode("utf-8", "replace")
        s.messages.append((round(now, 3), msg, len(data), p["parts"], text))
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
            s.ack_soon = True
            self._test(s, msg, data, text, p, now)
        elif mtype == TYPE_EVENT:
            self.stats["events"] += 1
            if self.on_debug:
                self.on_debug("EVENT", text)
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
        for msg in sorted(s.partial, key=lambda i: (i - s.ack) % WRAP)[:4]:
            p = s.partial[msg]
            bits = bitmap(p["parts"], p["got"])
            if only_stale and (now - p["last"] < self.parts_after or bits == p["reported"]):
                continue
            out.append((msg, bits))
        return out

    def _asks(self, s, now):
        """what a LAZY addon is asked for again (PARTS), at most every NACK_AGAIN seconds each: every id up to the latest
        known to have gone up that is not in (a partial one with the parts it has, one never seen with none), then the
        partial ones quiet for parts_after seconds (their last parts may be the lost ones)"""
        out, seen = [], set()
        span = (s.top - s.ack) % WRAP if s.base is not None and s.top is not None else 0
        if 0 < span <= NACK_SPAN:                   # top behind the ack (or far off): nothing is missing below it
            i = s.ack
            for _ in range(span):
                i = nxt(i)
                if i in s.received or now - s.nacked.get(i, -1e9) < NACK_AGAIN:
                    continue
                p = s.partial.get(i)
                out.append((i, bitmap(p["parts"], p["got"]) if p else ""))
                seen.add(i)
                if len(out) >= PARTS_MAX:
                    return out
        for msg, bits in self._parts_records(s, now, only_stale=True):
            if msg not in seen and now - s.nacked.get(msg, -1e9) >= NACK_AGAIN and len(out) < PARTS_MAX:
                out.append((msg, bits))
        return out

    def _resend(self, records):
        """the records of slots the addon did not read that go out again: texts, commands, code and WELCOMEs, but not the
        pings (forgotten here): after an outage every heartbeat's ping would come back at once, each with the whole
        outage as its round trip, and the answers would hold up everything else the addon sends"""
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
        ahead_ = sorted(i for i in self.written if i >= k)
        lost = self._resend(r for i in ahead_ for r in self.written[i] if r[0] != MB.WELCOME)
        for i in ahead_:
            MB.clear_slot(self.addons, i)
            del self.written[i]
            self.written_at.pop(i, None)
            self.written_as.pop(i, None)
        self.note(f"the addon reads from slot {k} now, below the slots written so far: {len(ahead_)} unread slots emptied, "
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
                self.written_as.pop(i, None)
                gone.append(i)
        if not gone:
            return
        self.stats["skipped"] += len(gone)
        self.note(f"the addon skipped slot{'s' if len(gone) > 1 else ''} {', '.join(map(str, gone))} ({why}): "
                  f"{len(lost)} texts / commands / code parts sent again")
        welcome = [r for r in lost if r[0] == MB.WELCOME][-1:]          # only the latest WELCOME is worth sending again
        lost = [r for r in lost if r[0] != MB.WELCOME]
        if welcome and not any(r[0] == MB.WELCOME for r in self.outbox):
            lost = welcome + lost
        self.outbox = lost + self.outbox
        self.last_rewind = self.clock()                 # the addon has just moved on: no rewind on top of this

    def _rewind(self, now):
        """the addon still waits on a slot stuck_after seconds after it got there and after we wrote it (reading one takes
        about a second): that slot was lost (emptied, say), so the slots from there are written again as they were: the
        same bytes, so that whatever the client has cached of them stays right; what waits goes into new slots after
        them. Only on a recent report: while data frames are up the addon shows few control frames, and an old slot
        number means nothing"""
        k = self.addon_slot
        w = self.written_at.get(k)
        if k is None or k >= self.slot or w is None or now - self.addon_slot_seen > 2.5:
            return
        if self.addon_slot_seen - max(w, self.addon_slot_since or w) < self.stuck_after:
            return                                      # it got there only lately, or its report predates the slot
        if self.last_rewind is not None and now - self.last_rewind < self.stuck_after:
            return
        again = [i for i in range(k, self.slot) if i in self.written and i in self.written_as]
        for i in again:
            sid, ack = self.written_as[i]
            MB.write_slot(self.addons, i, sid, ack, self.written[i])
        self.note(f"the addon still waits on slot {k} {now - w:.0f} s after it was written: writing again from slot {k} "
                  f"(up to {self.slot - 1}) as they were, {len(again)} slots")
        self.last_rewind = now

    def _drop_oversized(self):
        """a record no packet can carry would hold up everything behind it (and the empty packets written meanwhile use
        slots up): dropped and said"""
        for r in [r for r in self.outbox if len(r[1]) > MB.RECORD_MAX]:
            self.outbox.remove(r)
            self.stats["dropped_records"] += 1
            self._tell("RUN", f"a {MB.NAMES.get(r[0], r[0])} record of {len(r[1])} bytes was dropped: a mailbox packet "
                              f"carries {MB.RECORD_MAX} at most")

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
        self._drop_oversized()
        since = None if self.last_packet is None else now - self.last_packet
        if since is not None and since < 0:             # the clock was set back: count from now
            self.last_packet, since = now, 0.0
        free = since is None or since >= self.send_every
        if s.features >= LAZY:
            asks = self._asks(s, now)
            wait = self.ack_every if s.ack_soon else self.ack_lazy
            ack_due = s.ack != s.ack_sent and s.unacked_at is not None and now - s.unacked_at >= wait
            due = (free and (bool(self.outbox) or bool(asks) or ack_due)) or since is None or since >= s.hb
        else:
            asks = self._parts_records(s, now, only_stale=False)
            stale = self._parts_records(s, now, only_stale=True)
            due = (self.outbox and free) \
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
        records, size, asked = [], MB.HEAD.size + 4, []
        for msg, bits in asks:
            rec = (MB.PARTS, f"{msg}:{bits}".encode())
            if size + 3 + len(rec[1]) <= MB.MAX_PACKET:
                records.append(rec)
                size += 3 + len(rec[1])
                asked.append((msg, bits))
        while self.outbox and size + 3 + len(self.outbox[0][1]) <= MB.MAX_PACKET:
            size += 3 + len(self.outbox[0][1])
            records.append(self.outbox.pop(0))
        if not records:
            records = [(MB.HEARTBEAT, f"t={int(now * 1000)}".encode())]
        try:
            n = MB.write_slot(self.addons, self.slot, s.sid, s.ack, records)
        except OSError as e:                        # held open past atomic_write's retries: the records wait for the next
            self.outbox = [r for r in records if r[0] not in (MB.HEARTBEAT, MB.PARTS)] + self.outbox
            self.last_packet = now
            self.note(f"could not write mailbox slot {self.slot} ({e}): its records wait for the next packet")
            return
        for msg, bits in asked:
            if msg in s.partial:
                s.partial[msg]["reported"] = bits
            s.nacked[msg] = now
        self.stats["asked"] += len(asked) if s.features >= LAZY else 0
        self.written[self.slot], self.written_at[self.slot] = records, now
        self.written_as[self.slot] = (s.sid, s.ack)
        if any(kind == MB.WELCOME for kind, _ in records):
            s.welcome_slot = self.slot
        s.ack_sent, s.unacked_at, s.ack_soon = s.ack, None, False
        self.stats["packets"] += 1
        self.stats["bytes"] += n
        self.stats["first_slot"] = self.stats["first_slot"] or self.slot
        self.stats["last_slot"] = self.slot
        for t, _ in records:
            self.stats["records"][MB.NAMES.get(t, str(t))] += 1
        self.slot += 1
        self.last_packet = now
        self.packet_times.append(now)
        self._prune_written()
        left = MB.POOL - self.slot + 1
        crossed = {n for n in SLOT_WARN if left <= n} - self.slots_warned
        if left <= 0:
            self.full = True
            self._tell("SLOTS", f"mailbox full: all {MB.POOL} slots of this game process are used, nothing reaches the "
                                f"addon any more; only a full restart of the game (not a /reload) makes them usable again")
        elif crossed:
            self.slots_warned |= crossed
            rate = self.slot_rate(now, s)
            self._tell("SLOTS", f"mailbox: {left} slots left in this game process (about {left / rate / 3600:.1f} h at "
                                f"{rate * 60:.1f} packets a minute, the rate of the last minutes); when they are gone, "
                                f"only a full restart of the game brings them back (the addon says so in the chat too)")

    def slot_rate(self, now, s=None):
        """mailbox packets a second: the last ten minutes' rate, at least the heartbeat's (the daemon's status asks from
        another thread: the times are copied first)"""
        s = s or self.sessions.get(self.current)
        floor = 1 / s.hb if s is not None else 1 / self.hb_every
        recent = [t for t in tuple(self.packet_times) if now - t <= 600]
        if len(recent) < 2:
            return floor
        return max(len(recent) / max(now - recent[0], 60.0), floor)

    def _prune_written(self):
        """slots the addon read long since: their records will not be needed again"""
        if len(self.written) <= WRITTEN_KEPT or self.addon_slot is None:
            return
        for i in sorted(self.written)[:len(self.written) - WRITTEN_KEPT]:
            if i < self.addon_slot - 64:
                del self.written[i]
                self.written_at.pop(i, None)
                self.written_as.pop(i, None)

    def data_calls(self):
        """whether the addon of the current session takes data calls (CALL records: link features DATA, 0.9.7 on)"""
        s = self.sessions.get(self.current)
        return s is not None and s.features >= DATA

    def mark(self):
        """(the current session, the latest message id received from it), or None: try waits until every message up to
        there is in (covered)"""
        s = self.sessions.get(self.current)
        return (s.sid, s.newest) if s is not None and s.newest is not None else None

    def covered(self, sid, msg):
        """whether every message of session sid up to msg is in"""
        s = self.sessions.get(sid)
        return s is not None and s.base is not None and not ahead(s.ack, msg)

    def report(self):
        rtts = sorted(self.rtts)
        return dict(
            stats=self.stats, events=list(self.events),
            ping=dict(n=len(rtts), min=rtts[0] if rtts else None, avg=round(sum(rtts) / len(rtts), 3) if rtts else None,
                      p50=rtts[len(rtts) // 2] if rtts else None, p95=rtts[int(0.95 * (len(rtts) - 1))] if rtts else None,
                      max=rtts[-1] if rtts else None, all=rtts),
            sessions=[dict(session=s.sid, version=s.version_text, hello_at=s.hello_at,
                           online_at=s.online_at,
                           handshake=round(s.online_at - s.hello_at, 3) if s.online_at and s.hello_at else None,
                           beats=s.beats, messages=len(s.messages), dups=s.dups, dup_parts=s.dup_parts, ack=s.ack,
                           base=s.base, incomplete=sorted(s.partial), features=s.features,
                           last=[dict(id=i, bytes=n, parts=k, text=t[:200]) for _, i, n, k, t in list(s.messages)[-5:]])
                      for s in self.sessions.values()])
