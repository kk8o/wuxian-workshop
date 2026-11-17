"""An app a local extension's own program drives, over a real daemon (contract3 sections 1 and 2; a FakeGame link,
WuxianKit answered by test_local_api's Kit, tests/local/fixture_ext.py as the program): the extension starts with the
workshop, its app is declared with driver local and opened by the workshop once the program runs, the wakes go to
the program one after another and where they got is kept, the player's stop and start, the app stopped when the
program stops, its reports passed on to the game, and the list WuxianKit is told says whether the program runs."""
import json
import time
import unittest
from pathlib import Path

from tests.daemon.test_local_api import LocalDaemon
from tests.local.util import until
from wuxianworkshop.local import drive as D

APP = {"app": "cocreate", "title": "数据共创", "driver": "local", "hear": ["cocreate:snapshot"]}
REPORT = {"app": "cocreate", "key": "1794938824:Horde", "state": "run", "step": "submit", "text": "已上传 3 / 4",
          "done": 3, "total": 4}


def wake(after, nxt, reason="item", kind="snapshot"):
    return {"app": "cocreate", "seq": nxt, "reason": reason, "skipped": 0, "after": after, "next": nxt,
            "items": [{"id": n, "source": "cocreate", "kind": kind} for n in range(after + 1, nxt + 1)]}


class Driven(LocalDaemon, unittest.TestCase):
    LETTER = "Drv"

    def wakes(self):
        return [w["wake"] for w in self.ok("POST", "/api/local/call", {"cap": "fx.wakes"})["result"]]

    def reasons(self):
        return [w["reason"] for w in self.wakes()]

    def opened(self):
        return [a for a in self.kit.asked("kit.app.open") if "temp" not in a]

    def kept(self):
        try:
            return json.loads((self.root / "home" / "state" / D.FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def view(self):
        return (self.svc.drive.view(), self.kit.calls)

    def running(self):
        row, _ = self.add(apps=[dict(APP)])
        self.assertEqual((row["start"], row["requires"]), ("workshop", {"kit": "0.7.0"}))
        self.assertTrue(until(lambda: self.opened()), self.view())
        self.assertTrue(until(lambda: self.reasons() == ["open"]), self.view())

    def test_opened_and_fed_by_the_workshop(self):
        self.running()
        self.assertEqual(self.opened(), [{"app": "cocreate"}])                 # no minutes, no lease: as declared
        declared = self.kit.apps["cocreate"]
        self.assertEqual((declared["driver"], declared["local"]["id"]), ("local", "fx"))
        self.assertNotIn("minutes", declared)
        first = self.wakes()[0]
        self.assertEqual((first["seq"], first["items"], first["status"]["on"]), (0, [], True))
        self.assertTrue(until(lambda: self.kit.roster and self.kit.roster[0]["running"] is True), self.kit.roster)
        self.kit.emit("app.cocreate", wake(0, 2))
        self.kit.emit("app.cocreate", wake(2, 3))
        self.assertTrue(until(lambda: len(self.wakes()) == 3), self.view())
        self.assertEqual([[i["id"] for i in w["items"]] for w in self.wakes()[1:]], [[1, 2], [3]])
        self.assertTrue(until(lambda: self.kept().get("cursors") == {"fx": {"cocreate": 3}}), self.kept())
        self.assertEqual(self.svc.drive.view(), [dict(app="cocreate", extension="fx", state="running", why=None,
                                                      cursor=3, opened=1)])
        self.kit.emit("app.cocreate", wake(3, 4, kind="fail"))                 # its program answers an error: noted
        self.assertTrue(until(lambda: self.kept().get("cursors") == {"fx": {"cocreate": 4}}), self.kept())
        notes = [e["text"] for e in list(self.svc.journal.entries) if e.get("kind") == "INFO"]
        self.assertTrue(any("was answered with an error: the wake failed" in n for n in notes))

    def test_the_players_stop_and_start(self):
        self.running()
        self.kit.on.discard("cocreate")
        self.kit.why["cocreate"] = "player"
        self.kit.emit("kit.app", {"app": "cocreate", "on": False, "why": "player"})
        self.assertTrue(until(lambda: self.svc.drive.view()[0]["state"] == "stopped"), self.view())
        self.kit.emit("kit.ready", {"version": "0.6.1"})                       # (a WuxianKit too old ends nothing)
        time.sleep(0.3)
        self.assertEqual((len(self.opened()), self.svc.drive.view()), (1, []))
        self.kit.emit("kit.ready", {"version": "0.7.0"})                       # a login or a reload: opened again
        self.assertTrue(until(lambda: len(self.opened()) == 2 and self.reasons() == ["open", "open"]), self.view())
        self.kit.on.discard("cocreate")
        self.kit.emit("kit.app", {"app": "cocreate", "on": False, "why": "player"})
        self.assertTrue(until(lambda: self.svc.drive.view()[0]["state"] == "stopped"), self.view())
        self.kit.on.add("cocreate")                                            # the card's 开始
        self.kit.emit("kit.app", {"app": "cocreate", "on": True})
        self.assertTrue(until(lambda: self.reasons() == ["open", "open", "open"]), self.view())

    def test_the_program_stops_and_the_app_with_it(self):
        self.running()
        self.assertTrue(until(lambda: self.kit.roster and self.kit.roster[0]["running"] is True))
        self.ok("POST", "/api/local/stop", {"id": "fx"})
        self.assertTrue(until(lambda: self.kit.asked("kit.app.stop") == [{"app": "cocreate", "why": "extension"}]),
                        self.view())
        self.assertTrue(until(lambda: self.kit.roster[0]["running"] is False), self.kit.roster)
        self.assertEqual((self.svc.drive.view(), self.kit.on), ([], set()))
        self.assertIn("cocreate", self.kit.apps)                               # still declared: it is still its app
        self.kit.items["cocreate"] = [{"id": 1, "source": "cocreate", "kind": "snapshot"}]
        self.ok("POST", "/api/local/load", {"id": "fx"})                       # it runs again: opened again
        self.assertTrue(until(lambda: len(self.opened()) == 2), self.view())
        self.assertTrue(until(lambda: self.reasons()[:1] == ["open"]), self.view())

    def test_one_that_died_in_a_wake_gets_it_again(self):
        self.running()
        self.kit.items["cocreate"] = [{"id": 1, "source": "cocreate", "kind": "die"}]
        self.kit.emit("app.cocreate", wake(0, 1, kind="die"))
        self.assertTrue(until(lambda: self.ok("GET", "/api/local")["extensions"][0]["state"] == "exited"))
        self.assertTrue(until(lambda: self.kit.asked("kit.app.stop") == [{"app": "cocreate", "why": "extension"}]),
                        self.view())
        self.assertEqual(self.kept().get("cursors"), {"fx": {"cocreate": 0}})   # not answered: where it got stays
        self.kit.items["cocreate"] = [{"id": 1, "source": "cocreate", "kind": "snapshot"}]
        self.ok("POST", "/api/local/load", {"id": "fx"})
        self.assertTrue(until(lambda: self.reasons() == ["open", "catchup"]), self.view())
        self.assertEqual([i["id"] for i in self.wakes()[1]["items"]], [1])
        self.assertTrue(until(lambda: self.kept().get("cursors") == {"fx": {"cocreate": 1}}), self.kept())

    def test_its_reports_go_to_the_game(self):
        self.running()
        t0 = time.time()
        self.ok("POST", "/api/local/call", {"cap": "fx.tell", "args": {"report": REPORT}})
        self.assertTrue(until(lambda: self.kit.reports), self.kit.calls)
        (told,) = self.kit.reports
        self.assertEqual({k: v for k, v in told.items() if k != "at"}, REPORT)
        self.assertTrue(t0 - 1 <= told["at"] <= time.time() + 1)
        self.ok("POST", "/api/local/call", {"cap": "fx.tell", "args": {"report": dict(REPORT, state="busy")}})
        self.ok("POST", "/api/local/call", {"cap": "fx.tell", "args": {"report": dict(REPORT, app="other")}})
        time.sleep(0.3)
        self.assertEqual(len(self.kit.reports), 1)                             # what is no report is dropped, and said
        out = " ".join(line["text"] for line in self.ok("GET", "/api/local/logs?id=fx")["lines"])
        self.assertIn("report dropped: state 'busy'", out)
        self.assertIn("report dropped: it does not drive the app other", out)
        self.assertFalse(Path(self.root / "home" / "state" / D.FILE).exists() and self.kept().get("reports"))

    def test_status_says_the_kit(self):
        self.kit.emit("kit.ready", {"version": "0.7.0"})
        self.assertTrue(until(lambda: self.ok("GET", "/api/status")["kit"] == {"version": "0.7.0"}))
        self.kit.emit("kit.ready", {"version": "0.6.1"})
        self.assertTrue(until(lambda: self.ok("GET", "/api/status")["kit"] == {
            "version": "0.6.1", "outdated": {"needs": "0.7.0", "has": "0.6.1"}}))
        self.kit.calls.clear()
        self.add(apps=[dict(APP)])
        time.sleep(0.5)
        self.assertEqual(self.kit.calls, [])                                   # no list, no app, nothing opened
        self.kit.emit("kit.ready", {"version": "0.7.0"})
        self.assertTrue(until(lambda: self.opened()), self.view())


if __name__ == "__main__":
    unittest.main()
