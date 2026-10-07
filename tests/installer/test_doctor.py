"""installer/doctor.py against the fake client: every check's ok / fix before an install, --fix, and the edge cases
(an AddOns.txt that disables an addon, an unverified client, .build.info with several rows, no client folder)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.installer import fake
from wuxianworkshop import __version__
from wuxianworkshop.core import mailbox
from wuxianworkshop.installer import doctor as D, install as I


def by_id(checks):
    return {c["id"]: c for c in checks}


class Env:
    def __init__(self, **client):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game = fake.make_client(self.root, **client)
        self.addons = self.game / "Interface" / "AddOns"
        self.source = fake.make_source(self.root)
        self.patches = [mock.patch.dict(os.environ, {"WUXIAN_HOME": str(self.root / "home")}),
                        mock.patch.object(D, "webview2_version", lambda: "154.0.4258.53"),
                        mock.patch.object(D, "dotnet_release", lambda: 533325),
                        mock.patch.object(D, "windows_build", lambda: 19045)]
        for p in self.patches:
            p.start()

    def close(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def checks(self):
        return by_id(D.run_checks(self.game, source=self.source))


class Doctor(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)

    def test_before_an_install(self):
        c = self.e.checks()
        self.assertEqual(c["game_dir"]["ok"], True)
        self.assertEqual(c["game_dir"]["detail"], str(self.e.game))
        self.assertEqual(c["client_version"]["ok"], True)
        self.assertIn("1.60.1.70235（分支 cn，wow_cn_beta）", c["client_version"]["detail"])
        self.assertEqual((c["addon:!WuxianWorkshop:installed"]["ok"], c["addon:!WuxianWorkshop:installed"]["fix"]), (False, "install"))
        self.assertIsNone(c["addon:!WuxianWorkshop:interface"]["ok"])                      # not installed: nothing to compare
        self.assertIsNone(c["addon:!WuxianWorkshop:version"]["ok"])
        self.assertEqual((c["addon:WoWBridge:installed"]["ok"], c["addon:WoWBridge:installed"]["fix"]), (False, "install"))
        self.assertIn("与程序自带的副本不同", c["addon:WoWBridge:installed"]["detail"])
        self.assertEqual(c["addon:WoWBridge:interface"]["ok"], True)                        # the old toc says 16001 too
        self.assertEqual((c["addon:WoWBridge:version"]["ok"], c["addon:WoWBridge:version"]["fix"]), (False, "install"))
        self.assertIn("游戏里的是 0.7.0，程序是", c["addon:WoWBridge:version"]["detail"])
        self.assertEqual((c["mailbox"]["ok"], c["mailbox"]["fix"]), (False, "create_mail"))
        self.assertIn("缺少 4086 个信箱槽位（共 4096 个）", c["mailbox"]["detail"])
        self.assertIsNone(c["addons_txt"]["ok"])
        self.assertEqual(c["webview2"]["ok"], True)
        self.assertEqual((c["leftovers"]["ok"], c["leftovers"]["fix"]), (False, "install_clean"))
        self.assertIn("!WoWBridge", c["leftovers"]["detail"])
        self.assertIn(f"WoWBridge_S{fake.FOREIGN_SLOT:03d}", c["leftovers"]["detail"])    # mentioned as left alone
        self.assertEqual(c["runtime_dir"]["ok"], True)
        self.assertEqual(c["runtime_dir"]["detail"], str(self.e.root / "home"))
        for check in c.values():
            self.assertEqual(set(check), {"id", "title", "ok", "detail", "fix"})
            self.assertIn(check["fix"], (None, "install", "install_clean", "create_mail"))

    def test_fix_then_everything_passes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = D.main(["--game", str(self.e.game), "--source", str(self.e.source), "--fix"])
        text = out.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn("已执行：install_clean", text)
        self.assertNotIn("[!!]", text)
        c = self.e.checks()
        self.assertEqual([k for k, v in c.items() if v["ok"] is False], [])
        self.assertEqual([k for k, v in c.items() if v["ok"] is None], ["addons_txt"])
        version = D.read_toc(self.e.source / "WoWBridge" / "WoWBridge.toc")["Version"]
        self.assertEqual(c["addon:WoWBridge:version"]["detail"], f"{version}，与程序一致")
        self.assertEqual(c["addon:WoWBridge:interface"]["detail"], "Interface 16001，与客户端 1.60.1.70235 一致")
        self.assertIn("4096 个信箱槽位和 proc.ttf 齐全", c["mailbox"]["detail"])
        self.assertEqual((self.e.addons / "WoWBridge" / "mail" / "00002.ttf").read_bytes(), b"used-2")
        self.assertFalse((self.e.addons / "!WoWBridge").exists())
        # nothing left to fix: --fix runs nothing
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(D.main(["--game", str(self.e.game), "--source", str(self.e.source), "--fix", "--json"]), 0)
        r = json.loads(out.getvalue())
        self.assertEqual(r["fixed"], [])
        self.assertEqual({c["id"] for c in r["checks"]}, set(c))

    def test_player_mode(self):
        """player mode checks the platform addon only; WoWBridge left in place is a failure the clean install fixes"""
        D.apply_fixes([dict(ok=False, fix="install")], self.e.game, source=self.e.source)          # developer install
        c = by_id(D.run_checks(self.e.game, source=self.e.source, mode="player"))
        self.assertNotIn("addon:WoWBridge:installed", c)
        self.assertNotIn("mailbox", c)
        self.assertEqual(c["addon:!WuxianWorkshop:installed"]["ok"], True)
        self.assertEqual((c["developer_components"]["ok"], c["developer_components"]["fix"]), (False, "install_clean"))
        self.assertIn("WoWBridge", c["developer_components"]["detail"])
        checks = D.run_checks(self.e.game, source=self.e.source, mode="player")
        self.assertEqual(D.apply_fixes(checks, self.e.game, source=self.e.source, mode="player"), ["install_clean"])
        self.assertFalse((self.e.addons / "WoWBridge").exists())
        c = by_id(D.run_checks(self.e.game, source=self.e.source, mode="player"))
        self.assertEqual([k for k, v in c.items() if v["ok"] is False], [])
        self.assertEqual(c["developer_components"]["detail"], "未安装")

    def test_create_mail_is_the_only_fix_when_only_slots_are_missing(self):
        I.install(self.e.game, source=self.e.source)
        for i in (100, 4096):
            mailbox.slot_path(self.e.addons, i).unlink()
        mailbox.proc_path(self.e.addons).unlink()
        checks = D.run_checks(self.e.game, source=self.e.source)
        c = by_id(checks)
        self.assertEqual(c["mailbox"]["detail"], "缺少 2 个信箱槽位（共 4096 个），缺少 proc.ttf")
        self.assertEqual(D.apply_fixes(checks, self.e.game, source=self.e.source), ["create_mail"])
        self.assertEqual(self.e.checks()["mailbox"]["ok"], True)
        self.assertEqual(D.apply_fixes(self.e.checks().values(), self.e.game, source=self.e.source), [])

    def test_outdated_files_ask_for_a_reinstall(self):
        I.install(self.e.game, source=self.e.source)
        (self.e.addons / "WoWBridge" / "Link.lua").write_text("-- edited in place\n", encoding="utf-8")
        c = self.e.checks()
        self.assertEqual((c["addon:WoWBridge:installed"]["ok"], c["addon:WoWBridge:installed"]["fix"]), (False, "install"))
        self.assertIn("7 个文件中有 1 个与程序自带的副本不同：Link.lua", c["addon:WoWBridge:installed"]["detail"])
        self.assertEqual(c["leftovers"]["ok"], True)
        checks = D.run_checks(self.e.game, source=self.e.source)
        self.assertEqual(D.apply_fixes(checks, self.e.game, source=self.e.source), ["install"])
        self.assertEqual(self.e.checks()["addon:WoWBridge:installed"]["ok"], True)

    def test_toc_mismatches_without_a_fix_in_the_bundled_copy(self):
        I.install(self.e.game, source=self.e.source)
        toc = self.e.addons / "WoWBridge" / "WoWBridge.toc"
        toc.write_text(toc.read_text(encoding="utf-8").replace("16001", "16000").replace(D.base_version(__version__), "0.1.0"), encoding="utf-8")
        c = self.e.checks()
        self.assertEqual((c["addon:WoWBridge:interface"]["ok"], c["addon:WoWBridge:interface"]["fix"]), (False, "install"))
        self.assertEqual((c["addon:WoWBridge:version"]["ok"], c["addon:WoWBridge:version"]["fix"]), (False, "install"))
        # the bundled copy is wrong as well: an install still writes the client's Interface; its version only a newer
        # program brings
        src_toc = self.e.source / "WoWBridge" / "WoWBridge.toc"
        src_toc.write_text(toc.read_text(encoding="utf-8"), encoding="utf-8")
        c = self.e.checks()
        self.assertEqual((c["addon:WoWBridge:interface"]["ok"], c["addon:WoWBridge:interface"]["fix"]), (False, "install"))
        self.assertEqual((c["addon:WoWBridge:version"]["ok"], c["addon:WoWBridge:version"]["fix"]), (False, None))
        self.assertIn("需要更新程序", c["addon:WoWBridge:version"]["detail"])    # the bundled copy is not the program's

    def test_addons_txt(self):
        char = self.e.game / "WTF" / "Account" / "123#1" / "95" / "无限-图鉴"
        char.mkdir(parents=True)
        (char / "AddOns.txt").write_text("!WuxianWorkshop: enabled\nWoWBridge: disabled\nSomeOtherAddon: disabled\n", encoding="utf-8")
        c = self.e.checks()["addons_txt"]
        self.assertEqual((c["ok"], c["fix"]), (False, None))
        self.assertIn("WoWBridge 在 Account/123#1/95/无限-图鉴/AddOns.txt 里被禁用", c["detail"])
        self.assertNotIn("SomeOtherAddon", c["detail"])
        (char / "AddOns.txt").write_text("!WuxianWorkshop: enabled\nWoWBridge: enabled\n", encoding="utf-8")
        (self.e.game / "WTF" / "Account" / "123#1" / "AddOns.txt").write_text("WoWBridge: enabled\n", encoding="utf-8")
        c = self.e.checks()["addons_txt"]
        self.assertEqual(c["ok"], True)
        self.assertEqual(c["detail"], "已在 2 个 AddOns.txt 里启用")

    def test_no_client_folder(self):
        checks = D.run_checks(self.e.root / "nowhere", source=self.e.source)
        c = by_id(checks)
        self.assertEqual(c["game_dir"]["ok"], False)
        self.assertIn("里没有 Interface\\AddOns", c["game_dir"]["detail"])
        skipped = {k for k, v in c.items() if v["ok"] is None and v["detail"].startswith("跳过：")}
        self.assertEqual(set(c) - skipped, {"game_dir", "webview2", "dotnet", "windows", "runtime_dir"})  # Windows' own
        self.assertEqual(len(checks), 15)                                              # the list keeps its shape
        self.assertEqual(D.apply_fixes(checks, self.e.root / "nowhere", source=self.e.source), [])


class ClientVersion(unittest.TestCase):
    def test_a_newer_client(self):
        """a client this release has not seen: its Interface comes from its version, the addons are installed with it and
        the self-check passes (a .toc that kept the old number would make the game list them as out of date)"""
        e = Env(version="1.61.0.70500")
        self.addCleanup(e.close)
        I.install(e.game, source=e.source)
        for name in ("!WuxianWorkshop", "WoWBridge"):
            self.assertIn("## Interface: 16100", (e.addons / name / f"{name}.toc").read_text(encoding="utf-8"))
        c = e.checks()
        self.assertEqual(c["client_version"]["ok"], True)
        self.assertIn("1.61.0.70500（分支 cn，wow_cn_beta）：Interface 16100（按版本号推算）", c["client_version"]["detail"])
        self.assertEqual((c["addon:WoWBridge:installed"]["ok"], c["addon:WoWBridge:interface"]["ok"]), (True, True))
        self.assertIn("Interface 16100，与客户端 1.61.0.70500 一致", c["addon:WoWBridge:interface"]["detail"])

    def test_the_game_updated_after_the_install(self):
        """installed for 1.60.1, then the launcher updates the client to 1.61.0: the check says so, a reinstall fixes it"""
        e = Env()
        self.addCleanup(e.close)
        I.install(e.game, source=e.source)
        info = e.game.parent / ".build.info"
        info.write_text(info.read_text(encoding="utf-8").replace("1.60.1.70235", "1.61.0.70500"), encoding="utf-8")
        c = e.checks()
        self.assertEqual((c["addon:WoWBridge:interface"]["ok"], c["addon:WoWBridge:interface"]["fix"]), (False, D.FIX_INSTALL))
        self.assertIn("客户端 1.61.0.70500 是 16100（游戏更新了）", c["addon:WoWBridge:interface"]["detail"])
        result = I.install(e.game, source=e.source)
        self.assertEqual(result["interface"], 16100)
        self.assertIn("WoWBridge/WoWBridge.toc", result["tocs_changed"])
        c = e.checks()
        self.assertEqual((c["addon:WoWBridge:installed"]["ok"], c["addon:WoWBridge:interface"]["ok"]), (True, True))

    def test_build_info_rows_and_places(self):
        rows = [fake.build_info_row("1.15.7.60000", product="wow_classic", branch="cn", active="1"),
                fake.build_info_row("1.60.1.70235", product="wow_cn_beta", branch="cn", active="0")]
        with tempfile.TemporaryDirectory() as tmp:
            game = fake.make_client(tmp, build_info_rows=rows)
            info = D.read_build_info(game)
            self.assertEqual((info["version"], info["product"], info["flavor"]), ("1.60.1.70235", "wow_cn_beta", "wow_cn_beta"))
            self.assertEqual(Path(info["path"]), game.parent / ".build.info")
            (game / ".flavor.info").unlink()                                      # no flavor: the active row
            self.assertEqual(D.read_build_info(game)["version"], "1.15.7.60000")
        with tempfile.TemporaryDirectory() as tmp:
            game = fake.make_client(tmp, build_info_in_client=True)
            self.assertEqual(Path(D.read_build_info(game)["path"]), game / ".build.info")
            (game / ".build.info").unlink()
            self.assertIsNone(D.read_build_info(game))
            with mock.patch.object(D, "webview2_version", lambda: None), mock.patch.dict(os.environ, {"WUXIAN_HOME": tmp}):
                c = by_id(D.run_checks(game, source=fake.make_source(tmp)))
            self.assertIsNone(c["client_version"]["ok"])
            self.assertIn("都没有 .build.info", c["client_version"]["detail"])
            self.assertEqual((c["webview2"]["ok"], c["webview2"]["fix"]), (False, "install_webview2"))   # one click
            self.assertIn("微软的安装程序", c["webview2"]["detail"])


class Helpers(unittest.TestCase):
    def test_base_version_and_toc(self):
        self.assertEqual(D.base_version("0.8.0.dev0"), "0.8.0")
        self.assertEqual(D.base_version("0.8.0"), "0.8.0")
        self.assertEqual(D.base_version("1.0rc1"), "1.0")
        self.assertEqual(D.base_version(None), "")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "X.toc"
            p.write_text("﻿## Interface: 16001\n##Title:  X \n## Notes: a: b\n\nCore.lua\n", encoding="utf-8")
            self.assertEqual(D.read_toc(p), {"Interface": "16001", "Title": "X", "Notes": "a: b"})
            self.assertIsNone(D.read_toc(Path(tmp) / "missing.toc"))

    def test_report(self):
        checks = [dict(id="a", title="甲", ok=True, detail="正常", fix=None),
                  dict(id="b", title="乙", ok=False, detail="出错", fix="install"),
                  dict(id="c", title="丙", ok=None, detail="未知：?", fix=None)]
        lines = D.report(checks)
        self.assertEqual(lines[:3], ["[ok] 甲：正常", "[!!] 乙：出错（修复动作：install）", "[??] 丙：未知：?"])
        self.assertEqual(lines[3], "共 3 项检查，1 项失败，1 项未知；`wuxian doctor --fix` 会执行：install")
        self.assertTrue(D.report(checks, fixed=["install"])[3].endswith(
            "；已执行：install；`wuxian doctor --fix` 会执行：install"))


if __name__ == "__main__":
    unittest.main()
