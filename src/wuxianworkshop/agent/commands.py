"""What an agent can have the addon do over the link: commands, Lua to run (run / load) and files to load again whenever
they are saved (watch). AgentCommands is mixed into transport.link.Companion, which owns the link state it works on:
clock, addons (the AddOns folder), outbox, current (the session of the latest frame), reload_sent, note() and _tell(),
and the bookkeeping the Companion's __init__ sets up for it (last_job, watched, watch_pending, watch_checked), and
before_load: None, or what is called with (addon or None, path) before a watched file that was saved is loaded again;
False from it keeps the file out of the game (the daemon checks its syntax then, agent/lint.py, and keeps a version of
the addon, agent/history.py).

Commands: "reload" (the addon shows a button: only a click may reload the UI on this client, and the addon reports
RELOAD "asked: ..." / "later: ..."); "run <lua>" and "load <file>" send Lua for the addon to run (CODE records, see code()),
which answers RUN "<job> ok <chunk name> (<bytes> B, <ms> ms, <n> values)[: <returned values, framed>]" or "<job> error
<chunk name>: <error>" (daemon/api.py parse_run reads them); "watch <file | addon>" and "unwatch" are handled here.
data_call() sends what the agent asks of an addon's API (call, respond, addon_api) as data (CALL records), not code.
Anything else goes to the addon as a COMMAND record.
A load may flag its first CODE part "reset" (one file) or "unload" / "reload" (the first / last file of an addon): the
addon then calls the addon's OnUnload() before the code runs and OnReload(<what OnUnload returned>) after it.
"""
import json
import re
from pathlib import Path

from ..core import mailbox as MB
from . import lint

CODE_CHUNK = 3700               # Lua bytes per CODE record for a short header: one record and the small ones (PARTS, a
                                # ping, a WELCOME) still fit a 4,090-byte packet; a longer header takes its room from it
CODE_ROOM = MB.RECORD_MAX - CODE_CHUNK - 64   # what a CODE record's header may take before the Lua gets less
RESET_FLAGS = {True: " reset", "reset": " reset", "unload": " unload", "reload": " reload"}   # code()'s reset -> header
FRAMES = re.compile(r"<(Frame|Button|CheckButton|EditBox|ScrollFrame|Slider|StatusBar|Texture|FontString)\b")


def _makes_frames(xml):
    """whether an XML file makes frames or templates (its comments are not read)"""
    try:
        text = xml.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    return FRAMES.search(re.sub(r"<!--.*?-->", "", text, flags=re.S)) is not None


def toc_files(toc):
    """(the Lua files an addon loads, in order; its XML files that also make frames or templates): what the client
    loads of the .toc's folder, as agent/lint.py reads it (read_toc, loaded_files): the .toc's lines for this client's
    game type, the XML files' <Script file> and <Include file> followed (comments skipped), a missing file left out;
    nothing when the .toc's ## AllowLoadGameType leaves this client out"""
    loaded = lint.loaded_files(toc.parent, lint.read_toc(toc))
    return loaded["lua"], [p for p in loaded["xml"] if _makes_frames(p)]


class AgentCommands:
    """the agent's side of the Companion (see the module docstring for what the Companion provides)"""

    def command(self, text):
        """a command for the addon; "reload" gets a nonce (the addon saves it, so it asks once even if the packet is
        read again) and is followed up: a new session = done (the user clicked), none within reload_wait seconds = not"""
        if text == "reload":
            now = self.clock()
            text = f"reload {int(now * 1000) % 100000000}"
            self.reload_sent = (now, self.current)
        elif text.startswith("run "):
            return self.code(text[4:].encode(), "=run")
        elif text.startswith("load "):
            return self.load(text[5:].strip())
        elif text.startswith("watch "):
            return self.watch(text[6:].strip())
        elif text == "unwatch" or text.startswith("unwatch "):
            return self.unwatch(text[8:].strip() or None)
        self.outbox.append((MB.COMMAND, text.encode()))

    def code(self, data, name, addon="-", reset=False):
        """Lua for the addon to run (loadstring under chunk name `name`, with (addon, its namespace) as "..."), in parts
        of CODE_CHUNK bytes. reset: True flags the first part "reset" (the addon calls WoWBridgeNS[addon].OnUnload()
        before the code and OnReload(<its result>) after it); "unload" or "reload" flags one half only. Returns the job
        id; the addon reports the result as a RUN message"""
        self.last_job = job = max(int(self.clock() * 1000) % 10 ** 10, self.last_job + 1)   # unique across restarts
        flag = RESET_FLAGS.get(reset, "")
        # the longest header any part gets (the first names the addon and the chunk), as if the parts ran to 99999
        head_max = len(f"{job} 99999/99999 {addon} {name}{flag}\n".encode())
        chunk = CODE_CHUNK - max(0, head_max - CODE_ROOM)
        if chunk < 512:
            raise ValueError(f"the chunk name is too long for a mailbox record: {name[:80]}...")
        parts = [data[i:i + chunk] for i in range(0, max(len(data), 1), chunk)]
        for i, piece in enumerate(parts, 1):
            head = f"{job} {i}/{len(parts)}" + (f" {addon} {name}{flag}" if i == 1 else "")
            self.outbox.append((MB.CODE, head.encode() + b"\n" + piece))
        self.note(f"code {job}: {name} ({len(data)} B, {len(parts)} parts{flag.replace(' ', ', ') if flag else ''})")
        return job

    def data_call(self, verb, payload):
        """what the agent asks of an addon's API (WoWBridge API.lua) as data, not code: CALL records "<job> <i>/<n>[
        <verb>]\n<JSON>" in parts of CODE_CHUNK bytes. verb: "call" {a: addon, n: name, d: args}, "reply" {r: request id,
        d: data}, "describe" {a: addon or None}, "piece" {k: job, i: piece}. WoWBridge (0.9.7 on, link features DATA)
        compiles nothing for it, so it works with hot loading off; it answers with a RUN message, as for code(). Returns
        the job id"""
        self.last_job = job = max(int(self.clock() * 1000) % 10 ** 10, self.last_job + 1)
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        parts = [data[i:i + CODE_CHUNK] for i in range(0, max(len(data), 1), CODE_CHUNK)]
        for i, piece in enumerate(parts, 1):
            head = f"{job} {i}/{len(parts)}" + (f" {verb}" if i == 1 else "")
            self.outbox.append((MB.CALL, head.encode() + b"\n" + piece))
        self.note(f"data call {job}: {verb} ({len(data)} B, {len(parts)} parts)")
        return job

    def withdraw(self, job):
        """the CODE / CALL parts of a job still in the outbox taken out (nobody waits for its result any more): (how
        many, whether a part had gone out already, so that it may still run)"""
        prefix = f"{job} ".encode()

        def its(kind, data):
            return kind in (MB.CODE, MB.CALL) and data.startswith(prefix)
        mine = [r for r in self.outbox if its(*r)]
        self.outbox = [r for r in self.outbox if not its(*r)]
        sent = any(its(k, d) for recs in self.written.values() for k, d in recs)
        return len(mine), sent

    def _resolve(self, spec):
        """a file or folder: absolute, or relative to the AddOns folder or the client folder; None if it is not there"""
        path = Path(spec)
        if not path.is_absolute():
            path = next((base / spec for base in (self.addons, self.addons.parent.parent) if (base / spec).exists()), None)
        return path if path is not None and path.exists() else None

    def _own(self, path):
        """WoWBridge's own files: running them again would start a second link"""
        p = path.resolve()
        return any(p == d or d in p.parents for d in (self.addons.resolve() / "WoWBridge", self.addons.resolve() / "!WuxianWorkshop"))

    def _targets(self, verb, spec):
        """(files, addon) for load / watch: a file, or the Lua files the client loads of an addon folder, in order
        (toc_files); None after telling the agent why not"""
        path = self._resolve(spec)
        if path is None:
            self._tell("RUN", f"{verb} {spec}: no such file or addon folder")
            return None
        if self._own(path):
            self._tell("RUN", f"{verb} {spec}: refused, WoWBridge's own files would start a second link "
                              f"(edit them, wuxian install, then reload)")
            return None
        if path.is_file():
            return [path], None
        toc = next((t for t in [path / f"{path.name}.toc"] + sorted(path.glob("*.toc")) if t.is_file()), None)
        if toc is None:
            self._tell("RUN", f"{verb} {spec}: no .toc in that folder")
            return None
        if not lint.read_toc(toc)["loads"]:
            self._tell("RUN", f"{verb} {path.name}: the client does not load this addon: its ## AllowLoadGameType "
                              f"leaves out {lint.GAME_TYPE}")
            return None
        files, frames = toc_files(toc)
        if frames:
            self._tell("RUN", f"{verb} {path.name}: frames and templates in {', '.join(f.name for f in frames)} are XML "
                              f"and are not made again; only the Lua runs")
        return files, path.name

    def load(self, spec, reset=True):
        """load <file | addon folder | addon name>: Lua for the addon to run as that file (errors name it). A file of an
        addon gets the addon's name and namespace as "..." (WoWBridgeNS[name], which the addon registers or the first
        load makes); a folder loads the Lua files the client loads of it, in order (toc_files: the .toc's lines for this
        client's game type, XML <Script> / <Include> lines followed).
        reset: the addon's OnUnload / OnReload hooks run around the load (one file: both around its job; a folder:
        OnUnload before the first file, OnReload after the last). Returns the job ids"""
        found = self._targets("load", spec)
        if found is None:
            return None
        files, addon = found
        flags = [None] * len(files)
        if reset and files:
            if len(files) == 1:
                flags[0] = "reset"
            else:
                flags[0], flags[-1] = "unload", "reload"
        return [self._load_file(f, addon, flag) for f, flag in zip(files, flags)]

    def _load_file(self, path, addon=None, reset=None):
        try:
            data = path.read_bytes()
        except OSError as e:
            self._tell("RUN", f"load {path}: {e}")
            return None
        try:
            rel = path.resolve().relative_to(self.addons.resolve())
            return self.code(data, "@Interface/AddOns/" + rel.as_posix(), addon or rel.parts[0], reset or False)
        except ValueError:
            return self.code(data, "@" + path.as_posix(), addon or "-", reset or False)

    def watch(self, spec):
        """watch <file | addon folder | addon name>: load a file again whenever it is saved (checked every 0.5 s,
        loaded once its time stamp has held for a check)"""
        found = self._targets("watch", spec)
        if found is None:
            return None
        files, addon = found
        for f in files:
            self.watched[f] = (f.stat().st_mtime_ns, addon)
        self._tell("WATCH", f"watching {len(files)} files of {addon or spec}: each is loaded again when it is saved")
        return files

    def unwatch(self, spec=None):
        root = self._resolve(spec) if spec else None
        gone = [f for f in self.watched if root is None or f == root or root in f.parents]
        for f in gone:
            self.watched.pop(f, None)
            self.watch_pending.pop(f, None)
        self._tell("WATCH", f"stopped watching {len(gone)} files")

    def _check_watch(self, now):
        # not while the link is down (the game away, a loading screen): a save meanwhile is loaded once it is back,
        # instead of a load queued for every save
        if not self.watched or now - self.watch_checked < 0.5 or not self.link_up:
            return
        self.watch_checked = now
        for f, (mtime, addon) in list(self.watched.items()):
            try:
                m = f.stat().st_mtime_ns
            except OSError:
                continue
            if m == mtime:
                continue
            if self.watch_pending.get(f) == m:          # unchanged since the last check: the save is complete
                self.watched[f] = (m, addon)
                del self.watch_pending[f]
                if self.before_load is not None:
                    try:
                        if self.before_load(addon, f) is False:
                            continue                    # not loaded: the hook said why
                    except Exception as e:              # the load goes on without it
                        self.note(f"before load of {f.name}: {e}")
                self._tell("WATCH", f"{f.name} was saved: loading it")
                before = self.watch_jobs.get(f)
                if before is not None:                  # the last save's load, not gone out yet: this one replaces it
                    self.withdraw(before)
                self.watch_jobs[f] = self._load_file(f, addon)
            else:
                self.watch_pending[f] = m
