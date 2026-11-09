"""One-click connection of the agents (agents.py) in a temporary home: Codex's config.toml and Cursor's mcp.json written
and read back without touching the rest, Claude Code through a stand-in for its command line, the states the page shows."""
import json
import os
import tempfile
import tomllib
import unittest
import unittest.mock
from pathlib import Path

from wuxianworkshop import agents as A

PROG = dict(command=r"C:\Users\Someone\AppData\Local\WuxianWorkshop.App\current\wuxian.exe", args=["mcp"], env={})

CODEX_CONFIG = """model = "gpt-5"   # a comment that stays
conversationDetailMode = "STEPS_COMMANDS"

[features]
js_repl = false

[mcp_servers.node_repl]
args = []
command = 'C:\\Tools\\node_repl.exe'
startup_timeout_sec = 120

[mcp_servers.node_repl.env]
CODEX_HOME = 'C:\\Users\\Someone\\.codex'
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.bin = Path(self.tmp.name) / "bin"                 # PATH: nothing there unless a test puts a command in
        self.home.mkdir()
        self.bin.mkdir()
        self.env = {"PATH": str(self.bin), "USERPROFILE": str(self.home), "APPDATA": str(self.home / "AppData" / "Roaming"),
                    "LOCALAPPDATA": str(self.home / "AppData" / "Local")}
        self.patch_program = unittest.mock.patch.object(A, "program", lambda env=None: dict(PROG, env=dict(PROG["env"])))
        self.patch_program.start()
        self.addCleanup(self.patch_program.stop)

    def status(self, host):
        return next(h for h in A.status(home=self.home, env=self.env)["hosts"] if h["id"] == host)


class Same(unittest.TestCase):
    def test_paths_compare_as_windows_does(self):
        self.assertTrue(A.same({"command": PROG["command"].upper(), "args": ["mcp"]}, PROG))
        self.assertFalse(A.same({"command": PROG["command"], "args": []}, PROG))
        self.assertFalse(A.same({"command": r"C:\elsewhere\wuxian.exe", "args": ["mcp"]}, PROG))
        self.assertFalse(A.same({"url": "http://127.0.0.1:1/mcp"}, PROG))
        self.assertTrue(A.same({"command": PROG["command"], "args": ["mcp"], "env": {"WUXIAN_LOG": "x"}}, PROG))   # other variables
        with_home = dict(PROG, env={"WUXIAN_HOME": r"C:\t\home"})
        self.assertFalse(A.same({"command": PROG["command"], "args": ["mcp"]}, with_home))
        self.assertTrue(A.same({"command": PROG["command"], "args": ["mcp"], "env": {"WUXIAN_HOME": r"C:\t\home"}}, with_home))

    def test_program_carries_wuxian_home_only_when_set(self):
        self.assertEqual(A.program({})["env"], {})
        self.assertEqual(A.program({"WUXIAN_HOME": r"C:\t"})["env"], {"WUXIAN_HOME": r"C:\t"})
        self.assertEqual(A.program({})["args"][-1], "mcp")


class Codex(Base):
    def config(self):
        return self.home / ".codex" / "config.toml"

    def test_missing_until_the_folder_or_the_command_is_there(self):
        self.assertEqual(self.status("codex")["state"], "missing")
        (self.home / ".codex").mkdir()
        st = self.status("codex")
        self.assertEqual((st["state"], st["can_connect"]), ("absent", True))
        self.assertEqual(st["where"], str(self.config()))

    def test_connect_appends_and_keeps_everything_else(self):
        self.config().parent.mkdir()
        self.config().write_text(CODEX_CONFIG, encoding="utf-8")
        st = A.connect("codex", home=self.home, env=self.env, verify=False)
        self.assertEqual(st["state"], "ok")
        text = self.config().read_text(encoding="utf-8")
        self.assertTrue(text.startswith(CODEX_CONFIG), text)                     # the user's text untouched, comments too
        data = tomllib.loads(text)
        self.assertEqual(data["mcp_servers"]["wuxian"], dict(command=PROG["command"], args=["mcp"], startup_timeout_sec=30,
                                                             tool_timeout_sec=120))
        self.assertEqual(data["mcp_servers"]["node_repl"]["env"]["CODEX_HOME"], r"C:\Users\Someone\.codex")
        self.assertEqual((self.config().parent / "config.toml.wuxian-backup").read_text(encoding="utf-8"), CODEX_CONFIG)
        again = A.connect("codex", home=self.home, env=self.env, verify=False)    # once more: the same file
        self.assertEqual(again["state"], "ok")
        self.assertEqual(self.config().read_text(encoding="utf-8"), text)

    def test_an_old_entry_in_the_middle_is_replaced(self):
        old = CODEX_CONFIG.replace("[mcp_servers.node_repl]", "[mcp_servers.wuxian]\ncommand = 'D:\\old\\wuxian.exe'\nargs = [\"mcp\"]\n"
                                   "[mcp_servers.wuxian.env]\nWUXIAN_LOG = 'x'\n\n[mcp_servers.node_repl]")
        self.config().parent.mkdir()
        self.config().write_text(old, encoding="utf-8")
        st = self.status("codex")
        self.assertEqual(st["state"], "other")
        self.assertIn(r"D:\old\wuxian.exe", st["detail"])
        A.connect("codex", home=self.home, env=self.env, verify=False)
        data = tomllib.loads(self.config().read_text(encoding="utf-8"))
        self.assertEqual(data["mcp_servers"]["wuxian"]["command"], PROG["command"])
        self.assertNotIn("env", data["mcp_servers"]["wuxian"])                    # the old sub-table went with it
        self.assertEqual(data["mcp_servers"]["node_repl"]["startup_timeout_sec"], 120)
        A.disconnect("codex", home=self.home, env=self.env)
        data = tomllib.loads(self.config().read_text(encoding="utf-8"))
        self.assertNotIn("wuxian", data["mcp_servers"])
        self.assertIn("node_repl", data["mcp_servers"])
        self.assertEqual(self.status("codex")["state"], "absent")

    def test_crlf_files_stay_crlf(self):
        self.config().parent.mkdir()
        self.config().write_bytes(CODEX_CONFIG.replace("\n", "\r\n").encode("utf-8"))
        A.connect("codex", home=self.home, env=self.env, verify=False)
        raw = self.config().read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertIn(b"[mcp_servers.wuxian]\r\n", raw)

    def test_an_entry_written_another_way_is_left_alone(self):
        odd = '[mcp_servers]\nwuxian = { command = "D:/x/wuxian.exe", args = ["mcp"] }\n'
        self.config().parent.mkdir()
        self.config().write_text(odd, encoding="utf-8")
        with self.assertRaises(A.AgentError) as cm:
            A.connect("codex", home=self.home, env=self.env, verify=False)
        self.assertIn("手动", str(cm.exception))
        self.assertEqual(self.config().read_text(encoding="utf-8"), odd)          # nothing written

    def test_a_broken_file_is_an_error_not_a_rewrite(self):
        self.config().parent.mkdir()
        self.config().write_text("[mcp_servers.wuxian\ncommand = 1\n", encoding="utf-8")
        st = self.status("codex")
        self.assertEqual(st["state"], "error")
        self.assertIn("TOML", st["detail"])
        with self.assertRaises(A.AgentError):
            A.connect("codex", home=self.home, env=self.env, verify=False)

    def test_wuxian_home_goes_into_an_env_table(self):
        self.config().parent.mkdir()
        with unittest.mock.patch.object(A, "program", lambda env=None: dict(PROG, env={"WUXIAN_HOME": r"C:\it's\home"})):
            st = A.connect("codex", home=self.home, env=self.env, verify=False)
        self.assertEqual(st["state"], "ok")
        data = tomllib.loads(self.config().read_text(encoding="utf-8"))
        self.assertEqual(data["mcp_servers"]["wuxian"]["env"], {"WUXIAN_HOME": r"C:\it's\home"})     # a quote: a basic string

    def test_codex_home_moves_the_file(self):
        moved = Path(self.tmp.name) / "codexhome"
        moved.mkdir()
        env = dict(self.env, CODEX_HOME=str(moved))
        A.connect("codex", home=self.home, env=env, verify=False)
        self.assertTrue((moved / "config.toml").is_file())
        self.assertFalse(self.config().exists())


class Cursor(Base):
    def config(self):
        return self.home / ".cursor" / "mcp.json"

    def test_connect_keeps_other_servers(self):
        self.assertEqual(self.status("cursor")["state"], "missing")
        self.config().parent.mkdir()
        self.config().write_text(json.dumps({"mcpServers": {"other": {"command": "node", "args": ["x.js"]}}}), encoding="utf-8")
        st = A.connect("cursor", home=self.home, env=self.env, verify=False)
        self.assertEqual((st["state"], st["can_verify"]), ("ok", False))
        data = json.loads(self.config().read_text(encoding="utf-8"))
        self.assertEqual(data["mcpServers"]["wuxian"], {"command": PROG["command"], "args": ["mcp"]})
        self.assertEqual(data["mcpServers"]["other"], {"command": "node", "args": ["x.js"]})
        A.disconnect("cursor", home=self.home, env=self.env)
        self.assertEqual(json.loads(self.config().read_text(encoding="utf-8")), {"mcpServers": {"other": {"command": "node", "args": ["x.js"]}}})

    def test_a_file_with_comments_is_not_touched(self):
        self.config().parent.mkdir()
        text = '{\n  // mine\n  "mcpServers": {}\n}\n'
        self.config().write_text(text, encoding="utf-8")
        self.assertEqual(self.status("cursor")["state"], "error")
        with self.assertRaises(A.AgentError):
            A.connect("cursor", home=self.home, env=self.env, verify=False)
        self.assertEqual(self.config().read_text(encoding="utf-8"), text)

    def test_present_with_the_program_installed_only(self):
        exe = self.home / "AppData" / "Local" / "Programs" / "cursor" / "Cursor.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        self.assertEqual(self.status("cursor")["state"], "absent")
        A.connect("cursor", home=self.home, env=self.env, verify=False)
        self.assertTrue(self.config().is_file())


class Trae(Base):
    def config(self, folder="Trae CN"):
        return self.home / "AppData" / "Roaming" / folder / "User" / "mcp.json"

    def test_trae_cn_is_there_once_installed_or_started(self):
        self.assertEqual(self.status("trae-cn")["state"], "missing")
        exe = self.home / "AppData" / "Local" / "Programs" / "Trae CN" / "Trae CN.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        st = self.status("trae-cn")
        self.assertEqual((st["state"], st["title"], st["can_connect"], st["can_verify"]), ("absent", "Trae CN", True, False))
        self.assertEqual(st["where"], str(self.config()))
        self.assertEqual(self.status("trae")["state"], "missing")      # the international version is another app

    def test_connect_writes_the_mcp_json_beside_the_user_settings(self):
        user = self.config().parent
        user.mkdir(parents=True)
        (user / "settings.json").write_text("{}", encoding="utf-8")   # what a Trae CN that has started has
        st = A.connect("trae-cn", home=self.home, env=self.env, verify=False)
        self.assertEqual(st["state"], "ok")
        self.assertEqual(json.loads(self.config().read_text(encoding="utf-8")),
                         {"mcpServers": {"wuxian": {"command": PROG["command"], "args": ["mcp"]}}})
        self.assertEqual((user / "settings.json").read_text(encoding="utf-8"), "{}")
        A.disconnect("trae-cn", home=self.home, env=self.env)
        self.assertEqual(json.loads(self.config().read_text(encoding="utf-8")), {"mcpServers": {}})
        self.assertEqual(self.status("trae-cn")["state"], "absent")

    def test_the_international_version(self):
        (self.home / "AppData" / "Roaming" / "Trae").mkdir(parents=True)
        st = A.connect("trae", home=self.home, env=self.env, verify=False)
        self.assertEqual(st["state"], "ok")
        self.assertIn(st["title"], ("Trae 国际版", "Trae (international)"))
        self.assertTrue(self.config("Trae").is_file())
        self.assertFalse(self.config().exists())

    def test_a_command_with_a_space_goes_in_short(self):
        exe = Path(self.tmp.name) / "With Space" / "wuxian.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"")
        if A.short_path(str(exe)) == str(exe):
            self.skipTest("no 8.3 short names on this volume")
        self.config().parent.mkdir(parents=True)
        with unittest.mock.patch.object(A, "program", lambda env=None: dict(PROG, command=str(exe), env={})):
            st = A.connect("trae-cn", home=self.home, env=self.env, verify=False)
            written = json.loads(self.config().read_text(encoding="utf-8"))["mcpServers"]["wuxian"]["command"]
            self.assertNotIn(" ", written)
            self.assertEqual(st["state"], "ok")                          # the short form is this program too
            self.assertNotIn(" ", json.loads(A.status(home=self.home, env=self.env)["manual"]["trae-cn"])["mcpServers"]["wuxian"]["command"])


class WorkBuddy(Base):
    def config(self):
        return self.home / ".workbuddy" / "mcp.json"

    def test_connect_into_its_user_level_file(self):
        self.assertEqual(self.status("workbuddy")["state"], "missing")
        self.config().parent.mkdir()
        self.config().write_text('{\n  "mcpServers": {}\n}', encoding="utf-8")       # as WorkBuddy leaves it at first start
        self.assertEqual(self.status("workbuddy")["state"], "absent")
        st = A.connect("workbuddy", home=self.home, env=self.env, verify=False)
        self.assertEqual((st["state"], st["can_verify"]), ("ok", False))
        self.assertEqual(json.loads(self.config().read_text(encoding="utf-8")),
                         {"mcpServers": {"wuxian": {"command": PROG["command"], "args": ["mcp"]}}})
        self.assertTrue(self.config().with_name("mcp.json" + A.BACKUP).is_file())
        A.disconnect("workbuddy", home=self.home, env=self.env)
        self.assertEqual(json.loads(self.config().read_text(encoding="utf-8")), {"mcpServers": {}})

    def test_present_with_the_program_installed_only(self):
        exe = self.home / "AppData" / "Local" / "Programs" / "WorkBuddy" / "WorkBuddy.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        self.assertEqual(self.status("workbuddy")["state"], "absent")
        A.connect("workbuddy", home=self.home, env=self.env, verify=False)
        self.assertTrue(self.config().is_file())

    def test_the_international_version_has_its_own_folder(self):
        """WorkBuddy 国际版 (WorkBuddy AI): ~/.workbuddy-ai, installed for every user under Program Files"""
        self.assertEqual(self.status("workbuddy-ai")["state"], "missing")
        exe = Path(self.tmp.name) / "Program Files" / "WorkBuddyAI" / "WorkBuddyAI.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        self.env["PROGRAMFILES"] = str(exe.parent.parent)
        st = self.status("workbuddy-ai")
        self.assertEqual((st["state"], st["where"]), ("absent", str(self.home / ".workbuddy-ai" / "mcp.json")))
        self.assertIn(st["title"], ("WorkBuddy 国际版", "WorkBuddy (international)"))
        self.assertEqual(self.status("workbuddy")["state"], "missing")      # the Chinese version is another app
        A.connect("workbuddy-ai", home=self.home, env=self.env, verify=False)
        self.assertEqual(json.loads((self.home / ".workbuddy-ai" / "mcp.json").read_text(encoding="utf-8")),
                         {"mcpServers": {"wuxian": {"command": PROG["command"], "args": ["mcp"]}}})
        self.assertFalse(self.config().exists())


class FakeClaude:
    """claude mcp add / remove / get against $CLAUDE_CONFIG_DIR/.claude.json, as the real command line does"""

    def __init__(self, healthy=True):
        self.calls, self.healthy = [], healthy

    def __call__(self, argv, env, timeout=None):
        self.calls.append(list(argv[1:]))
        path = Path(env["CLAUDE_CONFIG_DIR"]) / ".claude.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        servers = data.setdefault("mcpServers", {})
        sub, rest = argv[2], argv[3:]
        if sub == "add":
            flags, words, i = {}, [], 0
            head = rest[:rest.index("--")] if "--" in rest else rest
            while i < len(head):                                    # --scope, --transport and --env take a value
                if head[i].startswith("-"):
                    flags.setdefault(head[i], []).append(head[i + 1])
                    i += 2
                else:
                    words.append(head[i])
                    i += 1
            name = words[0]
            if name in servers:
                return 1, f"MCP server {name} already exists in user config\n"
            if flags.get("--transport") == ["http"]:
                servers[name] = {"type": "http", "url": words[1]}
            else:
                cmd = rest[rest.index("--") + 1:]
                envs = dict(e.split("=", 1) for e in flags.get("--env", []))
                servers[name] = {"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": envs}
            path.write_text(json.dumps(data), encoding="utf-8")
            return 0, f"Added {servers[name]['type']} MCP server {name}\n"
        if sub == "remove":
            if rest[0] not in servers:
                return 1, f'No MCP server named "{rest[0]}" in user scope\n'
            del servers[rest[0]]
            path.write_text(json.dumps(data), encoding="utf-8")
            return 0, f"Removed MCP server {rest[0]} from user config\n"
        if sub == "get":
            if rest[0] not in servers:
                return 1, f'No MCP server named "{rest[0]}". Configured servers: \n'
            state = "✔ Connected" if self.healthy else "✗ Failed to connect"
            return 0, f"{rest[0]}:\n  Scope: User config\n  Status: {state}\n  Type: stdio\n"
        return 2, "unknown\n"


class ClaudeHome(Base):
    """a home with Claude Code's config folder and the stand-in for its command line"""

    def setUp(self):
        super().setUp()
        self.cfg = Path(self.tmp.name) / "claudecfg"
        self.cfg.mkdir()
        self.env["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        self.fake = FakeClaude()

    def put_cli(self):
        (self.bin / "claude.exe").write_bytes(b"")


class Claude(ClaudeHome):

    def test_states_and_connect_through_the_command_line(self):
        self.assertEqual(self.status("claude")["state"], "missing")
        self.put_cli()
        st = self.status("claude")
        self.assertEqual((st["state"], st["can_connect"], st["where"]), ("absent", True, str(self.cfg / ".claude.json")))
        self.assertTrue(st["can_verify"])                              # its command line can tell (mcp get)
        st = A.connect("claude", home=self.home, env=self.env, run=self.fake)
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["verify"], {"ok": True, "text": "Claude Code 启动它：✔ Connected"})
        self.assertEqual(self.fake.calls[0], ["mcp", "add", "--scope", "user", "--transport", "stdio", "wuxian", "--", PROG["command"], "mcp"])

    def test_another_entry_is_replaced(self):
        self.put_cli()
        (self.cfg / ".claude.json").write_text(json.dumps({"numStartups": 3, "mcpServers": {
            "wuxian": {"type": "stdio", "command": r"D:\dev\wuxian\.venv\Scripts\wuxian.exe", "args": ["mcp"]},
            "other": {"type": "stdio", "command": "node", "args": []}}}), encoding="utf-8")
        st = self.status("claude")
        self.assertEqual(st["state"], "other")
        A.connect("claude", home=self.home, env=self.env, run=self.fake, verify=False)
        self.assertEqual([c[:2] for c in self.fake.calls], [["mcp", "remove"], ["mcp", "add"]])
        data = json.loads((self.cfg / ".claude.json").read_text(encoding="utf-8"))
        self.assertEqual(data["mcpServers"]["wuxian"]["command"], PROG["command"])
        self.assertIn("other", data["mcpServers"])
        A.disconnect("claude", home=self.home, env=self.env, run=self.fake)
        self.assertEqual(self.status("claude")["state"], "absent")

    def test_env_goes_after_the_name(self):
        self.put_cli()
        with unittest.mock.patch.object(A, "program", lambda env=None: dict(PROG, env={"WUXIAN_HOME": r"C:\t\home"})):
            st = A.connect("claude", home=self.home, env=self.env, run=self.fake, verify=False)
        self.assertEqual(st["state"], "ok")
        add = self.fake.calls[-1]
        self.assertLess(add.index("wuxian"), add.index("--env"))
        self.assertEqual(add[add.index("--env") + 1], r"WUXIAN_HOME=C:\t\home")

    def test_without_the_command_line_the_page_shows_the_command(self):
        (self.cfg / ".claude.json").write_text("{}", encoding="utf-8")
        st = self.status("claude")
        self.assertEqual((st["state"], st["can_connect"]), ("absent", False))
        self.assertIn("claude 命令", st["detail"])
        with self.assertRaises(A.AgentError):
            A.connect("claude", home=self.home, env=self.env, run=self.fake)
        manual = A.status(home=self.home, env=self.env)["manual"]
        self.assertIn(f'claude mcp add --scope user --transport stdio wuxian -- {PROG["command"]} mcp', manual["claude"])

    def test_a_failed_check_is_reported(self):
        self.put_cli()
        st = A.connect("claude", home=self.home, env=self.env, run=FakeClaude(healthy=False))
        self.assertFalse(st["verify"]["ok"])
        self.assertIn("Failed", st["verify"]["text"])


URL = "https://wuxianwow.com/mcp"


class Web(ClaudeHome):
    """the second server, 无限图鉴 (wuxianwow) by its URL: each agent's own way (the website's aiconnect.js), an entry of
    any name that reaches it counts, disconnect removes only the one written here, wuxian's entry is never touched"""

    def web(self, host):
        return next(h for h in A.status(home=self.home, env=self.env, server="wuxianwow")["hosts"] if h["id"] == host)

    def connect(self, host):
        return A.connect(host, home=self.home, env=self.env, run=self.fake, verify=False, server="wuxianwow")

    def disconnect(self, host):
        return A.disconnect(host, home=self.home, env=self.env, run=self.fake, server="wuxianwow")

    def test_the_mark(self):
        mark = A.target("wuxianwow")["mark"]
        for yes in ({"url": URL}, {"url": "https://wuxianwow.com/mcp/"}, {"serverUrl": "https://www.WuxianWow.com/mcp?x=1"},
                    {"command": "npx", "args": ["-y", "mcp-remote", URL]}, {"type": "streamableHttp", "baseUrl": URL}):
            self.assertTrue(A.serves(yes, mark), yes)
        for no in ({"url": "https://notwuxianwow.com/mcp"}, {"url": "https://wuxianwow.com/mcpx"}, {"url": "https://wuxianwow.com/ai"},
                   {"command": URL.replace("/mcp", "")}, "wuxianwow.com/mcp", None):
            self.assertFalse(A.serves(no, mark), no)
        with self.assertRaises(A.AgentError):
            A.target("elsewhere")
        self.assertEqual(A.target(None)["name"], "wuxian")

    def test_the_status_shape(self):
        out = A.status(home=self.home, env=self.env, server="wuxianwow")
        self.assertEqual((out["server"], out["program"]), ("wuxianwow", {"name": "wuxianwow", "url": URL}))
        self.assertEqual([h["id"] for h in out["hosts"]], list(A.HOSTS))
        self.assertTrue(all(h["server"] == "wuxianwow" and h["state"] == "missing" for h in out["hosts"]))
        m = out["manual"]
        self.assertEqual(m["claude"], f"claude mcp add --transport http --scope user wuxianwow {URL}")
        self.assertEqual(tomllib.loads(m["codex"].split("\n", 1)[1]), {"mcp_servers": {"wuxianwow": {"url": URL}}})
        for h in ("cursor", "trae-cn", "trae", "other"):
            self.assertEqual(json.loads(m[h]), {"mcpServers": {"wuxianwow": {"url": URL}}}, h)
        for h in ("workbuddy", "workbuddy-ai"):
            self.assertEqual(json.loads(m[h]), {"mcpServers": {"wuxianwow": {"type": "http", "url": URL}}}, h)
        self.assertEqual(json.loads(m["remote"])["mcpServers"]["wuxianwow"]["args"], ["-y", "mcp-remote", URL])
        self.assertEqual(A.status(home=self.home, env=self.env)["server"], "wuxian")         # the default, as before

    def test_json_agents(self):
        cases = {"cursor": (self.home / ".cursor", ".", {"url": URL}),
                 "trae-cn": (self.home / "AppData" / "Roaming" / "Trae CN", "User", {"url": URL}),
                 "trae": (self.home / "AppData" / "Roaming" / "Trae", "User", {"url": URL}),
                 "workbuddy": (self.home / ".workbuddy", ".", {"type": "http", "url": URL}),
                 "workbuddy-ai": (self.home / ".workbuddy-ai", ".", {"type": "http", "url": URL})}
        for host, (folder, sub, entry) in cases.items():
            with self.subTest(host):
                path = folder / sub / "mcp.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                mine = {"wuxian": {"command": PROG["command"], "args": ["mcp"]}, "other": {"command": "node", "args": []}}
                path.write_text(json.dumps({"mcpServers": mine}), encoding="utf-8")
                st = self.web(host)
                self.assertEqual((st["state"], st["can_connect"], st["name"], st["entry"]), ("absent", True, None, None))
                if "wuxian" in A.tr(*A.CLASSES[host].apply):
                    self.assertIn("wuxianwow", st["apply"])                              # its own name in the words
                st = self.connect(host)
                self.assertEqual((st["state"], st["name"], st["entry"], st["ours"]), ("ok", "wuxianwow", "wuxianwow", True))
                self.assertIn(URL, st["detail"])
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["mcpServers"], dict(mine, wuxianwow=entry))
                self.assertTrue(path.with_name("mcp.json" + A.BACKUP).is_file())
                self.assertEqual(self.status(host)["state"], "ok")                       # wuxian's own entry as it was
                st = self.disconnect(host)
                self.assertEqual(st["state"], "absent")
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["mcpServers"], mine)

    def test_an_entry_of_another_name_counts(self):
        """added by hand from the website (another name, the URL with a slash) or through mcp-remote: connected, not
        added again, and disconnect leaves it"""
        path = self.home / ".cursor" / "mcp.json"
        path.parent.mkdir()
        for entry in ({"url": URL + "/"}, {"command": "npx", "args": ["-y", "mcp-remote", URL]}):
            with self.subTest(entry):
                text = json.dumps({"mcpServers": {"wuxian-db": entry}})
                path.write_text(text, encoding="utf-8")
                st = self.web("cursor")
                self.assertEqual((st["state"], st["name"], st["entry"], st["ours"]), ("ok", "wuxian-db", "wuxian-db", False))
                self.assertIn("断开不会删它", st["detail"])
                self.assertEqual(self.connect("cursor")["state"], "ok")
                self.assertEqual(path.read_text(encoding="utf-8"), text)                  # not added again
                self.assertEqual(self.disconnect("cursor")["state"], "ok")
                self.assertEqual(path.read_text(encoding="utf-8"), text)                  # not ours: left

    def test_its_name_written_another_way(self):
        """an entry named wuxianwow through mcp-remote is connected but not ours (disconnect leaves it); one that reaches
        elsewhere is other, and connect replaces it"""
        path = self.home / ".workbuddy" / "mcp.json"
        path.parent.mkdir()
        remote = {"command": "npx", "args": ["-y", "mcp-remote", URL]}
        path.write_text(json.dumps({"mcpServers": {"wuxianwow": remote}}), encoding="utf-8")
        self.assertEqual((self.web("workbuddy")["state"], self.web("workbuddy")["ours"]), ("ok", False))
        self.disconnect("workbuddy")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["wuxianwow"], remote)
        path.write_text(json.dumps({"mcpServers": {"wuxianwow": {"url": "https://elsewhere.example/mcp"}}}), encoding="utf-8")
        st = self.web("workbuddy")
        self.assertEqual((st["state"], st["ours"]), ("other", False))
        self.assertIn("elsewhere.example", st["detail"])
        self.assertEqual(self.connect("workbuddy")["state"], "ok")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["wuxianwow"], {"type": "http", "url": URL})

    def test_codex(self):
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir()
        config.write_text(CODEX_CONFIG, encoding="utf-8")
        A.connect("codex", home=self.home, env=self.env, verify=False)                   # wuxian first
        with_wuxian = config.read_text(encoding="utf-8")
        st = self.connect("codex")
        self.assertEqual((st["state"], st["name"], st["ours"]), ("ok", "wuxianwow", True))
        text = config.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(with_wuxian), text)
        self.assertTrue(text.endswith("[mcp_servers.wuxianwow]\nurl = 'https://wuxianwow.com/mcp'\n"), text)
        data = tomllib.loads(text)
        self.assertEqual(data["mcp_servers"]["wuxianwow"], {"url": URL})
        self.assertEqual(self.status("codex")["state"], "ok")
        self.connect("codex")                                                            # once more: the same file
        self.assertEqual(config.read_text(encoding="utf-8"), text)
        self.disconnect("codex")
        self.assertEqual(config.read_text(encoding="utf-8"), with_wuxian)
        self.assertEqual(self.web("codex")["state"], "absent")
        aliased = with_wuxian + "\n[mcp_servers.db]\nurl = \"https://wuxianwow.com/mcp\"\n"     # the website's way, another name
        config.write_text(aliased, encoding="utf-8")
        self.assertEqual((self.web("codex")["state"], self.web("codex")["name"]), ("ok", "db"))
        self.connect("codex")
        self.disconnect("codex")
        self.assertEqual(config.read_text(encoding="utf-8"), aliased)

    def test_claude_code_through_its_command_line(self):
        self.put_cli()
        (self.cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"wuxian": {"type": "stdio", "command": PROG["command"],
                                                                                       "args": ["mcp"]}}}), encoding="utf-8")
        self.assertEqual(self.web("claude")["state"], "absent")
        st = A.connect("claude", home=self.home, env=self.env, run=self.fake, server="wuxianwow")
        self.assertEqual(self.fake.calls[0], ["mcp", "add", "--transport", "http", "--scope", "user", "wuxianwow", URL])
        self.assertEqual((st["state"], st["ours"]), ("ok", True))
        self.assertEqual(st["verify"], {"ok": True, "text": "Claude Code 连它：✔ Connected"})
        self.assertEqual(self.fake.calls[1], ["mcp", "get", "wuxianwow"])
        self.assertEqual(self.status("claude")["state"], "ok")                           # wuxian's still there
        self.fake.calls.clear()
        self.connect("claude")                                                           # there already: nothing run
        self.assertEqual(self.fake.calls, [])
        self.disconnect("claude")
        self.assertEqual(self.fake.calls, [["mcp", "remove", "wuxianwow", "-s", "user"]])
        self.assertEqual((self.web("claude")["state"], self.status("claude")["state"]), ("absent", "ok"))

    def test_claude_code_with_an_entry_of_its_own(self):
        """one added through claude.ai's connector-like name, or another URL under our name"""
        self.put_cli()
        cfg = self.cfg / ".claude.json"
        cfg.write_text(json.dumps({"mcpServers": {"无限图鉴": {"type": "http", "url": URL}}}), encoding="utf-8")
        st = A.verify("claude", home=self.home, env=self.env, run=self.fake, server="wuxianwow")
        self.assertEqual((st["state"], st["name"]), ("ok", "无限图鉴"))
        self.assertEqual(self.fake.calls[-1], ["mcp", "get", "无限图鉴"])                # asked by its own name
        self.disconnect("claude")
        self.assertIn("无限图鉴", json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"])
        cfg.write_text(json.dumps({"mcpServers": {"wuxianwow": {"type": "http", "url": "http://127.0.0.1:9/mcp"}}}), encoding="utf-8")
        self.assertEqual(self.web("claude")["state"], "other")
        self.fake.calls.clear()
        self.assertEqual(self.connect("claude")["state"], "ok")
        self.assertEqual([c[:2] for c in self.fake.calls], [["mcp", "remove"], ["mcp", "add"]])

    def test_claude_code_without_its_command_line(self):
        (self.cfg / ".claude.json").write_text("{}", encoding="utf-8")
        st = self.web("claude")
        self.assertEqual((st["state"], st["can_connect"]), ("absent", False))
        with self.assertRaises(A.AgentError):
            self.connect("claude")

    def test_an_agent_without_http(self):
        """an agent that cannot take a server by URL: no one-click connect, the manual way through mcp-remote"""
        (self.home / ".cursor").mkdir()
        with unittest.mock.patch.object(A.Cursor, "http", False):
            st = self.web("cursor")
            self.assertEqual((st["state"], st["can_connect"]), ("absent", False))
            self.assertIn("mcp-remote", st["detail"])
            with self.assertRaises(A.AgentError):
                self.connect("cursor")
            self.assertFalse((self.home / ".cursor" / "mcp.json").exists())
            manual = A.status(home=self.home, env=self.env, server="wuxianwow")["manual"]
            self.assertEqual(json.loads(manual["cursor"])["mcpServers"]["wuxianwow"]["command"], "npx")
            self.assertEqual(self.status("cursor")["can_connect"], True)                   # wuxian on stdio: as before


class Commands(unittest.TestCase):
    def test_cmd_shims_get_no_special_characters(self):
        with self.assertRaises(A.AgentError):
            A.run_command([r"C:\npm\codex.cmd", "mcp", "add", "wuxian", "--", r"C:\a&b\wuxian.exe"], dict(os.environ))

    def test_unknown_agent(self):
        with self.assertRaises(A.AgentError):
            A.connect("vim")


if __name__ == "__main__":
    unittest.main()
