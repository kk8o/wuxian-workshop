r"""Configuration snippets for the MCP hosts (`wuxian mcp-config [claude|codex|cursor|trae-cn|trae|workbuddy]`): how
Claude Code, Codex, Cursor, Trae and WorkBuddy start `wuxian mcp` on stdio, and the Streamable HTTP alternative at the
running daemon's /mcp (its port and token change with every start, so the stdio form is the one to keep; the web page's
接入 tab fills the live values in)."""
import json
import os
import sys
from pathlib import Path

from ..paths import logs_dir

HOSTS = ("claude", "codex", "cursor", "trae-cn", "trae", "workbuddy")
JSON_FILES = {   # the hosts that read the Cursor layout (mcpServers), and where
    "cursor": "# Cursor: .cursor/mcp.json in the project, or ~/.cursor/mcp.json for every project",
    "trae-cn": "# Trae CN: %APPDATA%\\Trae CN\\User\\mcp.json for every project, or paste it in 设置 → MCP → 添加 → 手动添加",
    "trae": "# Trae (international): %APPDATA%\\Trae\\User\\mcp.json for every project, or Settings → MCP → Add → Add Manually",
    "workbuddy": "# WorkBuddy: %USERPROFILE%\\.workbuddy\\mcp.json for every project, or 插件 → MCP 服务器 → 配置 MCP",
}


def mcp_command():
    """(command, args) that start the stdio server: the frozen exe, the venv's wuxian.exe, or python -m"""
    if getattr(sys, "frozen", False):
        return sys.executable, ["mcp"]
    exe = Path(sys.executable).with_name("wuxian.exe")
    if exe.exists():
        return str(exe), ["mcp"]
    return sys.executable, ["-m", "wuxianworkshop.cli.main", "mcp"]


def environment():
    """the env block of a stdio entry: the log file, and WUXIAN_HOME when this process runs with one"""
    env = {"WUXIAN_LOG": str(logs_dir() / "mcp.log")}
    if os.environ.get("WUXIAN_HOME"):
        env["WUXIAN_HOME"] = os.environ["WUXIAN_HOME"]
    return env


def _toml_string(value):
    return "'" + str(value).replace("'", "''") + "'"        # a literal string: backslashes stay as they are


def snippet(host, info=None, command=None, args=None, env=None):
    """the text for one host; info is daemon.json of the running daemon (its /mcp URL and token), or None"""
    if host not in HOSTS:
        raise ValueError(f"host: one of {', '.join(HOSTS)}")
    if command is None:
        command, args = mcp_command()
    if host in ("trae-cn", "trae", "workbuddy"):          # Trae takes no space in the command: its 8.3 form
        from ..agents import short_path
        command = short_path(str(command))
    env = environment() if env is None else env
    url = info.get("mcp_url") if info else None
    token = info.get("token") if info else None
    stdio = {"command": command, "args": list(args), "env": env}
    lines = []
    if host == "claude":
        entry = {"type": "stdio", **stdio}
        servers = {"wuxian": entry}
        if url:
            servers["wuxian-http"] = {"type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"}}
        lines += ["# Claude Code: .mcp.json in the project (or the mcpServers of ~/.claude.json)",
                  json.dumps({"mcpServers": servers}, indent=2, ensure_ascii=False),
                  "# the same with the command line:",
                  "claude mcp add --transport stdio --scope user wuxian " +
                  " ".join(f"--env {k}={v}" for k, v in env.items()) + f" -- {_quote(command)} " + " ".join(args)]
        if url:
            lines += ["# or straight to the running daemon (port and token change with every start):",
                      f'claude mcp add --transport http --scope user wuxian-http {url} --header "Authorization: Bearer {token}"']
    elif host == "codex":
        lines += ["# Codex: %USERPROFILE%\\.codex\\config.toml", "[mcp_servers.wuxian]", f"command = {_toml_string(command)}",
                  "args = [" + ", ".join(json.dumps(a) for a in args) + "]", "startup_timeout_sec = 30",
                  "tool_timeout_sec = 120", "[mcp_servers.wuxian.env]"]
        lines += [f"{k} = {_toml_string(v)}" for k, v in env.items()]
        if url:
            lines += ["# or straight to the running daemon (port and token change with every start):",
                      "[mcp_servers.wuxian_http]", f'url = "{url}"', "[mcp_servers.wuxian_http.http_headers]",
                      f'Authorization = "Bearer {token}"']
        lines += ["# the same with the command line:",
                  "codex mcp add wuxian " + " ".join(f"--env {k}={v}" for k, v in env.items()) + f" -- {_quote(command)} " + " ".join(args)]
    else:
        servers = {"wuxian": stdio}
        if url:
            servers["wuxian-http"] = {"url": url, "headers": {"Authorization": f"Bearer {token}"}}
        lines += [JSON_FILES[host], json.dumps({"mcpServers": servers}, indent=2, ensure_ascii=False)]
    return "\n".join(lines) + "\n"


def _quote(text):
    return f'"{text}"' if " " in text else text


def all_snippets(info=None, hosts=HOSTS):
    return "\n".join(snippet(h, info) for h in hosts)
