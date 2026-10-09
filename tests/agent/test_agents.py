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
            name = next(a for a in rest if not a.startswith("-") and a not in ("user", "stdio"))
            if name in servers:
                return 1, f"MCP server {name} already exists in user config\n"
            cmd = rest[rest.index("--") + 1:]
            envs = dict(rest[i + 1].split("=", 1) for i, a in enumerate(rest) if a == "--env")
            servers[name] = {"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": envs}
            path.write_text(json.dumps(data), encoding="utf-8")
            return 0, f"Added stdio MCP server {name}\n"
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


class Claude(Base):
    def setUp(self):
        super().setUp()
        self.cfg = Path(self.tmp.name) / "claudecfg"
        self.cfg.mkdir()
        self.env["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        self.fake = FakeClaude()

    def put_cli(self):
        (self.bin / "claude.exe").write_bytes(b"")

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


class Commands(unittest.TestCase):
    def test_cmd_shims_get_no_special_characters(self):
        with self.assertRaises(A.AgentError):
            A.run_command([r"C:\npm\codex.cmd", "mcp", "add", "wuxian", "--", r"C:\a&b\wuxian.exe"], dict(os.environ))

    def test_unknown_agent(self):
        with self.assertRaises(A.AgentError):
            A.connect("vim")


if __name__ == "__main__":
    unittest.main()
