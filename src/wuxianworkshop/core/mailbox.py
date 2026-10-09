"""The font mailbox (v0.5+): the companion -> addon channel (addon/WoWBridge/Mailbox.lua reads it).

mail/00001.ttf .. mail/04096.ttf are 0-byte placeholders until the companion writes one packet into a slot, as a font
whose glyph widths carry the bytes (fontpack.py). The addon reads the slots in order. Measured on 1.60.1.70235: a slot can
be read once per game process (the client caches a font by path until it exits, /reload included), and an empty slot can
be polled (a failed load is not cached). mail/proc.ttf carries a stamp of the game process, so that the addon knows when
the slots are fresh again.

A packet, inside fontpack's "WF" envelope:
    "WM" | version 1 | flags | slot (4) | session (2) | ack (2) | record count (2) | body length (2) | records | CRC-32 (4)
    record: type (1) | length (2) | data
session = the addon UI session the packet answers, ack = the highest message id received without gaps in that session.
A CODE record carries part of a Lua job for the addon to run: "<job> <i>/<n>[ <addon> <chunk name>]\n<bytes>" (the
addon name and chunk name only in part 1; addon "-" = none). A CALL record (addon 0.9.7 on, link features 2) carries part
of a data call the same way, "<job> <i>/<n>[ <verb>]\n<JSON>": what the agent asks of an addon's API, which WoWBridge reads
without compiling anything (agent/commands.py data_call).
"""
import struct
import zlib

from . import fontpack
from .game import atomic_write

POOL = 4096
MAGIC = b"WM"
VERSION = 1
WELCOME, HEARTBEAT, TEXT, COMMAND, PARTS, CODE, CALL = 1, 2, 3, 4, 5, 6, 7
NAMES = {WELCOME: "WELCOME", HEARTBEAT: "HEARTBEAT", TEXT: "TEXT", COMMAND: "COMMAND", PARTS: "PARTS", CODE: "CODE",
         CALL: "CALL"}
HEAD = struct.Struct(">2sBBIHHHH")
MAX_PACKET = 4096 - 6        # the addon reads at most 4096 bytes of "WF" payload (FontProbe.lua Font.MAX_PACKET)
RECORD_MAX = MAX_PACKET - HEAD.size - 4 - 3   # the most data one record carries: a packet of it alone


def encode(slot, session, ack, records):
    """records: [(type, bytes)]"""
    body = b"".join(struct.pack(">BH", t, len(d)) + d for t, d in records)
    data = HEAD.pack(MAGIC, VERSION, 0, slot, session & 0xFFFF, ack & 0xFFFF, len(records), len(body)) + body
    data += struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)
    if len(data) > MAX_PACKET:
        raise ValueError(f"packet of {len(data)} bytes, the mailbox takes {MAX_PACKET}")
    return data


def decode(data):
    """dict(slot, session, ack, records=[(type, bytes)]) or None"""
    if len(data) < HEAD.size + 4:
        return None
    magic, ver, _, slot, session, ack, n, blen = HEAD.unpack_from(data)
    end = HEAD.size + blen
    if magic != MAGIC or ver != VERSION or len(data) < end + 4:
        return None
    if zlib.crc32(data[:end]) & 0xFFFFFFFF != struct.unpack_from(">I", data, end)[0]:
        return None
    records, i = [], HEAD.size
    for _ in range(n):
        t, ln = struct.unpack_from(">BH", data, i)
        records.append((t, bytes(data[i + 3:i + 3 + ln])))
        i += 3 + ln
    return dict(slot=slot, session=session, ack=ack, records=records)


def mail_dir(addons):
    return addons / "WoWBridge" / "mail"


def slot_path(addons, slot):
    return mail_dir(addons) / f"{slot:05d}.ttf"


def proc_path(addons):
    return mail_dir(addons) / "proc.ttf"


def install(addons, pool=POOL):
    """the placeholders that are missing (new files: the client only finds them when it starts). Slots in use are left
    alone: emptying them under a running companion would lose packets; the companion empties them for a new game process."""
    d = mail_dir(addons)
    d.mkdir(exist_ok=True)
    for i in range(1, pool + 1):
        p = d / f"{i:05d}.ttf"
        if not p.exists():
            p.write_bytes(b"")
    if not proc_path(addons).exists():
        proc_path(addons).write_bytes(b"")


def reset(addons):
    """empty every slot an earlier game process used; returns how many. A slot that cannot be emptied (held open) is
    left as it is rather than stopping the others"""
    n = 0
    for p in mail_dir(addons).glob("[0-9]*.ttf"):
        try:
            if p.stat().st_size:
                atomic_write(p, b"")
                n += 1
        except OSError:
            pass
    return n


def clear_slot(addons, slot):
    atomic_write(slot_path(addons, slot), b"")


def write_proc(addons, stamp):
    atomic_write(proc_path(addons), fontpack.build(fontpack.packet(stamp.encode()), "WBProc"))


def write_slot(addons, slot, session, ack, records):
    """the packet as the font in its slot; returns the packet size"""
    data = encode(slot, session, ack, records)
    atomic_write(slot_path(addons, slot), fontpack.build(fontpack.packet(data), f"WBMail{slot}"))
    return len(data)


def read_slot(addons, slot):
    """what the addon would read from a slot (tests): decode(...) or None"""
    try:
        ws = fontpack.widths(slot_path(addons, slot).read_bytes())
    except Exception:
        return None
    payload = fontpack.unpacket(fontpack.read(ws)[0])
    return decode(payload) if payload else None
