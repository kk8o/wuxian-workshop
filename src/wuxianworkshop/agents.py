r"""One-click connection of the coding agents to 无限工坊's MCP server (the wizard's 接上 Agent step, the 接入 Agent page,
`wuxian agents`): each agent starts `wuxian.exe mcp` on stdio, registered at the user level so that every project has it.

    claude   Claude Code (its command line, the desktop app's Code tab and the IDE extensions share one user config):
             through its own command line, `claude mcp add --scope user` / `claude mcp remove -s user`, which keeps
             ~/.claude.json the way Claude Code wants it (every running Claude Code rewrites that file); read from the
             file's top-level mcpServers. CLAUDE_CONFIG_DIR moves the file.
    codex    Codex (command line, desktop app and IDE extension share ~/.codex/config.toml; CODEX_HOME moves it): the
             [mcp_servers.wuxian] table, written here with the start-up and tool timeouts its command line cannot set
    cursor   Cursor: mcpServers.wuxian in ~/.cursor/mcp.json

status() tells for each agent whether it is there and whether wuxian is registered with this program: state "ok" (this
program), "other" (another command, e.g. a copy that moved), "absent", "missing" (the agent is not installed) or "error"
(its config could not be read); with where the entry lives and how a change takes effect. connect() registers this
program, replacing an entry of the same name; disconnect() removes it. A file is backed up first (<file>.wuxian-backup),
and the new text is parsed again and compared with the old one (only wuxian's entry may differ) before it replaces the
original. verify() asks Claude Code or Codex whether it can use the entry (`mcp get`; Claude Code starts the server to
tell). Every message is meant for the page, in Chinese.
"""
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from .cli.mcpconfig import mcp_command

NAME = "wuxian"
HOSTS = ("claude", "codex", "cursor")
TIMEOUT = 90                 # seconds for an agent's command line (`claude mcp get` starts the server to check it)
BACKUP = ".wuxian-backup"
CMD_UNSAFE = '%^&|<>"'      # what cmd.exe would read itself in an argument handed to a .cmd / .bat (npm's shims)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class AgentError(Exception):
    """a change that could not be made; the message says why, for the page"""


def program(env=None):
    """the entry every agent gets: {command, args, env}; env carries WUXIAN_HOME only, when this process runs with one"""
    env = os.environ if env is None else env
    command, args = mcp_command()
    return dict(command=str(command), args=[str(a) for a in args],
                env={"WUXIAN_HOME": env["WUXIAN_HOME"]} if env.get("WUXIAN_HOME") else {})


def same(entry, prog):
    """whether an agent's entry starts this program the same way (paths compared as Windows does)"""
    if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
        return False
    norm = lambda p: os.path.normcase(os.path.normpath(p))              # noqa: E731
    env = entry.get("env") if isinstance(entry.get("env"), dict) else {}
    return (norm(entry["command"]) == norm(prog["command"]) and list(entry.get("args") or []) == prog["args"]
            and ({"WUXIAN_HOME": env["WUXIAN_HOME"]} if env.get("WUXIAN_HOME") else {}) == prog["env"])


def run_command(argv, env, timeout=TIMEOUT):
    """(exit code, what it printed) of an agent's command line, run without a console window"""
    if Path(argv[0]).suffix.lower() in (".cmd", ".bat"):
        bad = next((a for a in argv[1:] if any(c in a for c in CMD_UNSAFE)), None)
        if bad is not None:
            raise AgentError(f"参数里有命令行的特殊字符，不能经 {Path(argv[0]).name} 传过去：{bad}")
    try:
        r = subprocess.run(argv, env=env, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise AgentError(f"运行不了 {argv[0]}") from None
    except subprocess.TimeoutExpired:
        raise AgentError(f"{Path(argv[0]).stem} {' '.join(argv[1:3])} 超过 {timeout} 秒没有结束") from None
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode, ANSI.sub("", out)


def last_line(text):
    lines = [l.strip() for l in str(text).splitlines() if l.strip()]
    return lines[-1] if lines else "（没有输出）"


def write_file(path, text):
    """text into path atomically, the old file kept beside it as <name>.wuxian-backup; line endings as given"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_name(path.name + BACKUP))
    tmp = path.with_name(path.name + ".wuxian-tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


class Host:
    """one agent; home and env stand in for the user's (tests), run for run_command"""
    id = title = apply = ""

    def __init__(self, home=None, env=None, run=None):
        self.env = dict(os.environ) if env is None else dict(env)
        self.home = Path(home) if home else Path(self.env.get("USERPROFILE") or Path.home())
        self.run = run or run_command

    def where(self):
        return str(self.config())

    def can_connect(self):
        return True, ""

    def status(self, prog):
        base = dict(id=self.id, title=self.title, where=self.where(), apply=self.apply, entry=None, can_connect=False)
        present, why = self.present()
        if not present:
            return dict(base, present=False, state="missing", detail=why)
        try:
            entry = self.entry()
        except AgentError as e:
            return dict(base, present=True, state="error", detail=str(e))
        can, cant = self.can_connect()
        if entry is None:
            state, detail = "absent", "还没有接入"
        elif same(entry, prog):
            state, detail = "ok", "已接入：会启动 " + " ".join([entry["command"], *entry.get("args", [])])
        else:
            shown = entry.get("command") or entry.get("url") or "?"
            state, detail = "other", f"已接入，但启动的是另一个程序：{shown}（点「接入」改成现在这个）"
        if not can:
            detail += "；" + cant
        return dict(base, present=True, can_connect=can, state=state, detail=detail,
                    entry={k: entry.get(k) for k in ("command", "args", "url") if k in entry} if entry else None)

    def verify(self):
        return None


class Claude(Host):
    id, title = "claude", "Claude Code"
    apply = "新开一个 Claude Code 会话就能用（已经开着的会话要重开）；终端、桌面版和 IDE 插件里的 Claude Code 共用这份设置。"

    def config(self):
        base = self.env.get("CLAUDE_CONFIG_DIR")
        return (Path(base) if base else self.home) / ".claude.json"

    def cli(self):
        found = shutil.which("claude", path=self.env.get("PATH"))
        if found:
            return found
        for p in (self.home / ".local" / "bin" / "claude.exe", Path(self.env.get("APPDATA") or self.home) / "npm" / "claude.cmd"):
            if p.is_file():
                return str(p)
        return None

    def present(self):
        if self.cli() or self.config().is_file():
            return True, ""
        return False, "没有找到 Claude Code（没有 claude 命令，也没有 ~/.claude.json）"

    def can_connect(self):
        if self.cli():
            return True, ""
        return False, "找到了 Claude Code 的设置，但没有找到 claude 命令：请在终端里运行下面的命令"

    def entry(self):
        path = self.config()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise AgentError(f"读不了 {path}：{e}") from None
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        entry = servers.get(NAME) if isinstance(servers, dict) else None
        return entry if isinstance(entry, dict) else None

    def _cli(self):
        cli = self.cli()
        if not cli:
            raise AgentError("没有找到 claude 命令：请先装好 Claude Code，或在终端里运行接入命令")
        return cli

    def connect(self, prog):
        cli = self._cli()
        if self.entry() is not None:                       # replace: the add refuses a name that is taken
            self.run([cli, "mcp", "remove", NAME, "-s", "user"], self.env)
        argv = [cli, "mcp", "add", "--scope", "user", "--transport", "stdio", NAME]
        for key, value in prog["env"].items():             # after the name: --env takes several values
            argv += ["--env", f"{key}={value}"]
        rc, out = self.run([*argv, "--", prog["command"], *prog["args"]], self.env)
        if rc != 0:
            raise AgentError("claude mcp add 失败：" + last_line(out))
        if not same(self.entry(), prog):
            raise AgentError("claude mcp add 说成功了，但设置里没有看到新的 wuxian：" + last_line(out))

    def disconnect(self):
        if self.entry() is None:
            return
        rc, out = self.run([self._cli(), "mcp", "remove", NAME, "-s", "user"], self.env)
        if rc != 0 and self.entry() is not None:
            raise AgentError("claude mcp remove 失败：" + last_line(out))

    def verify(self):
        """`claude mcp get wuxian`: Claude Code starts the server and says whether it answered"""
        rc, out = self.run([self._cli(), "mcp", "get", NAME], self.env)
        m = re.search(r"Status:\s*(.+)", out)
        state = m.group(1).strip() if m else ""
        ok = rc == 0 and ("Connected" in state or "✔" in state)
        return dict(ok=ok, text=f"Claude Code 启动它：{state}" if state else last_line(out))


TABLE = re.compile(r"^[ \t]*\[\[?[ \t]*([^\]\r\n]+?)[ \t]*\]\]?[ \t]*(?:#.*)?$")


def _ours(key):
    """whether a TOML table header names mcp_servers.wuxian or one of its sub-tables"""
    parts = [p.strip().strip("\"'") for p in key.split(".")]
    return len(parts) >= 2 and parts[0] == "mcp_servers" and parts[1] == NAME


def _toml_str(s):
    return "'" + s + "'" if "'" not in s and "\n" not in s else json.dumps(s)


def _without(data):
    """a parsed config without mcp_servers.wuxian (to compare everything else)"""
    data = dict(data)
    servers = data.get("mcp_servers")
    if isinstance(servers, dict) and NAME in servers:
        servers = {k: v for k, v in servers.items() if k != NAME}
        if servers:
            data["mcp_servers"] = servers
        else:
            data.pop("mcp_servers")
    return data


class Codex(Host):
    id, title = "codex", "Codex"
    apply = "新开一个 Codex 会话就能用；命令行、桌面版和 IDE 插件里的 Codex 共用这份设置。"

    def folder(self):
        return Path(self.env.get("CODEX_HOME") or self.home / ".codex")

    def config(self):
        return self.folder() / "config.toml"

    def cli(self):
        return shutil.which("codex", path=self.env.get("PATH"))

    def present(self):
        if self.cli() or self.folder().is_dir():
            return True, ""
        return False, "没有找到 Codex（没有 codex 命令，也没有 ~/.codex 文件夹）"

    def load(self, text=None):
        path = self.config()
        if text is None:
            if not path.is_file():
                return {}
            text = path.read_bytes().decode("utf-8-sig")
        try:
            return tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError) as e:
            raise AgentError(f"{path} 不是合法的 TOML（{e}）：请照下面的配置手动修改") from None

    def entry(self):
        servers = self.load().get("mcp_servers")
        entry = servers.get(NAME) if isinstance(servers, dict) else None
        return entry if isinstance(entry, dict) else None

    @staticmethod
    def block(prog):
        lines = [f"[mcp_servers.{NAME}]", f"command = {_toml_str(prog['command'])}",
                 "args = [" + ", ".join(_toml_str(a) for a in prog["args"]) + "]",
                 "startup_timeout_sec = 30", "tool_timeout_sec = 120"]
        if prog["env"]:
            lines += [f"[mcp_servers.{NAME}.env]"] + [f"{k} = {_toml_str(v)}" for k, v in prog["env"].items()]
        return lines

    def rewrite(self, prog=None):
        """config.toml without our tables, then (prog given) our table at the end; checked before it is written"""
        path = self.config()
        old = path.read_bytes().decode("utf-8-sig") if path.is_file() else ""      # as it is: \r\n stays \r\n
        before = self.load(old) if old else {}
        nl = "\r\n" if "\r\n" in old else "\n"
        kept, skip = [], False
        for line in old.splitlines(keepends=True):
            m = TABLE.match(line.rstrip("\r\n"))
            if m:
                skip = _ours(m.group(1))
            if not skip:
                kept.append(line)
        text = "".join(kept).rstrip("\r\n")
        text = text + nl if text.strip() else ""
        if prog is not None:
            text += (nl if text else "") + nl.join(self.block(prog)) + nl
        try:
            after = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise AgentError(f"改写后的 {path.name} 解析不了（{e}），没有写入：请手动修改") from None
        if _without(after) != _without(before):
            raise AgentError(f"{path} 里 wuxian 的写法认不出来，改写会动到别的设置，没有写入：请手动修改")
        servers = after.get("mcp_servers") or {}
        if prog is not None and not same(servers.get(NAME), prog):
            raise AgentError(f"{path} 里还有别处定义了 wuxian，没有写入：请手动修改")
        if prog is None and NAME in servers:
            raise AgentError(f"{path} 里 wuxian 的写法认不出来，没有删掉：请手动修改")
        write_file(path, text)

    def connect(self, prog):
        self.rewrite(prog)

    def disconnect(self):
        if self.entry() is not None:
            self.rewrite(None)

    def verify(self):
        """`codex mcp get wuxian`: whether Codex reads the entry (it does not start the server)"""
        cli = self.cli()
        if not cli:
            return None
        rc, out = self.run([cli, "mcp", "get", NAME], self.env)
        return dict(ok=rc == 0, text="Codex 读到了 wuxian 的设置" if rc == 0 else "Codex 读设置出错：" + last_line(out))


class Cursor(Host):
    id, title = "cursor", "Cursor"
    apply = "在 Cursor 的 设置 → MCP 里能看到 wuxian；第一次可能要手动打开它的开关。"

    def folder(self):
        return self.home / ".cursor"

    def config(self):
        return self.folder() / "mcp.json"

    def app(self):
        local = self.env.get("LOCALAPPDATA")
        exe = Path(local) / "Programs" / "cursor" / "Cursor.exe" if local else None
        return exe if exe is not None and exe.is_file() else None

    def present(self):
        if self.folder().is_dir() or self.app():
            return True, ""
        return False, "没有找到 Cursor（没有 ~/.cursor 文件夹）"

    def load(self):
        path = self.config()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise AgentError(f"{path} 不是合法的 JSON（{e}；也许有注释）：请照下面的配置手动修改") from None
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
            raise AgentError(f"{path} 的格式认不出来：请照下面的配置手动修改")
        return data

    def entry(self):
        entry = self.load().get("mcpServers", {}).get(NAME)
        return entry if isinstance(entry, dict) else None

    def connect(self, prog):
        data = self.load()
        entry = {"command": prog["command"], "args": prog["args"]}
        if prog["env"]:
            entry["env"] = prog["env"]
        data.setdefault("mcpServers", {})[NAME] = entry
        write_file(self.config(), json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def disconnect(self):
        data = self.load()
        if NAME in data.get("mcpServers", {}):
            del data["mcpServers"][NAME]
            write_file(self.config(), json.dumps(data, indent=2, ensure_ascii=False) + "\n")


CLASSES = {"claude": Claude, "codex": Codex, "cursor": Cursor}


def host(name, home=None, env=None, run=None):
    if name not in CLASSES:
        raise AgentError(f"不认识的 Agent：{name}（可选 {'、'.join(HOSTS)}）")
    return CLASSES[name](home, env, run)


def manual(prog):
    """what to do by hand, per agent and for any other MCP host: {id: text}"""
    cmd = prog["command"]
    quoted = f'"{cmd}"' if " " in cmd else cmd
    args = " ".join(prog["args"])
    env = "".join(f" --env {k}={v}" for k, v in prog["env"].items())
    stdio = {"command": cmd, "args": prog["args"], **({"env": prog["env"]} if prog["env"] else {})}
    return dict(
        claude=f"claude mcp add --scope user --transport stdio {NAME}{env} -- {quoted} {args}",
        codex="\n".join(["# %USERPROFILE%\\.codex\\config.toml 末尾加上：", *Codex.block(prog)]),
        cursor=json.dumps({"mcpServers": {NAME: stdio}}, indent=2, ensure_ascii=False),
        other=json.dumps({"mcpServers": {NAME: stdio}}, indent=2, ensure_ascii=False))


def status(home=None, env=None, run=None):
    """every agent's state, the program they get, and the manual way for each"""
    prog = program(env)
    return dict(program=dict(command=prog["command"], args=prog["args"]),
                hosts=[host(h, home, env, run).status(prog) for h in HOSTS], manual=manual(prog))


def connect(name, home=None, env=None, run=None, verify=True):
    """register this program with one agent; its new state, with what verify() found when asked"""
    h, prog = host(name, home, env, run), program(env)
    present, why = h.present()
    if not present:
        raise AgentError(why)
    h.connect(prog)
    answer = h.status(prog)
    if verify:
        try:
            answer["verify"] = h.verify()
        except AgentError as e:
            answer["verify"] = dict(ok=False, text=str(e))
    return answer


def disconnect(name, home=None, env=None, run=None):
    h = host(name, home, env, run)
    h.disconnect()
    return h.status(program(env))


def verify(name, home=None, env=None, run=None):
    h = host(name, home, env, run)
    answer = h.status(program(env))
    answer["verify"] = h.verify()
    return answer
