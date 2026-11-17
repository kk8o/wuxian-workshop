"""A local extension's options changed in the game's /wk window, over a real daemon (fx2, contract2 section 3; a
FakeGame link, WuxianKit's calls answered by test_local_api's Kit, tests/local/fixture_ext.py as the extension, whose
option is the number volume 0-100): the roster WuxianKit is told carries the options and their values;
kit.local.option from WuxianKit sets one as /api/local/set does (the running extension is told) and the roster goes
again, also when it is refused (only the journal says why); the same topic from another addon does nothing; a change
in the App reaches the window as well."""
import json
import time
import unittest

from tests.daemon.test_local_api import LocalDaemon
from tests.local.util import until

VOLUME = {"name": "volume", "kind": "number", "title": "音量", "value": 50, "default": 50, "min": 0, "max": 100}


class LocalOption(LocalDaemon, unittest.TestCase):
    LETTER = "Opt"

    def told(self):
        return self.kit.asked("kit.local.set")

    def options(self):
        roster = self.kit.roster or []
        return roster[0].get("options") if roster else None

    def notes(self, first):
        return [e["text"] for e in list(self.svc.journal.entries) if e["id"] >= first and e.get("kind") == "INFO"]

    def described(self):
        self.add()
        self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": "hi"}})     # it starts: described
        self.assertTrue(until(lambda: self.options() == [VOLUME] and self.kit.roster[0]["running"] is True),
                        self.kit.roster)                                   # (the list says that it runs, too)

    def test_the_roster_has_the_options(self):
        self.add()
        self.assertTrue(until(lambda: self.kit.roster is not None and len(self.kit.roster) == 1), self.kit.calls)
        self.assertNotIn("options", self.kit.roster[0])                   # not described yet
        self.ok("POST", "/api/local/call", {"cap": "fx.echo", "args": {"text": "hi"}})
        self.assertTrue(until(lambda: self.options() == [VOLUME]), self.kit.roster)
        told = len(self.told())
        self.ok("POST", "/api/local/set", {"id": "fx", "options": {"volume": 30}})     # in the App: to /wk too
        self.assertTrue(until(lambda: self.options() == [dict(VOLUME, value=30)]), self.kit.roster)
        self.assertEqual(len(self.told()), told + 1)
        time.sleep(0.3)
        self.assertEqual(len(self.told()), told + 1)                       # once

    def test_a_change_in_the_game(self):
        self.described()
        told, first = len(self.told()), self.svc.journal.next_id
        self.kit.emit("kit.local.option", {"extension": "fx", "name": "volume", "value": 70})
        self.assertTrue(until(lambda: self.options() == [dict(VOLUME, value=70)]), self.kit.roster)
        self.assertEqual(self.ok("POST", "/api/local/call", {"cap": "fx.opts"})["result"], {"volume": 70})  # told
        self.assertEqual(self.ok("GET", "/api/local")["extensions"][0]["options"][0]["value"], 70)
        time.sleep(0.3)
        self.assertEqual(len(self.told()), told + 1)                       # once: not for the change and again
        self.assertIn("local extension fx: the option volume set from /wk", self.notes(first))

    def test_the_same_value_is_told_back_all_the_same(self):
        self.described()
        told = len(self.told())
        self.kit.emit("kit.local.option", {"extension": "fx", "name": "volume", "value": 50})
        self.assertTrue(until(lambda: len(self.told()) == told + 1), self.kit.calls)
        self.assertEqual(self.options(), [VOLUME])

    def test_refused_and_told_back(self):
        self.described()
        for data, words in (({"extension": "fx", "name": "volume", "value": 500}, "(bad_option)"),
                            ({"extension": "fx", "name": "volume", "value": "loud"}, "(bad_option)"),
                            ({"extension": "fx", "name": "pitch", "value": 1}, "(bad_option)"),
                            ({"extension": "ghost", "name": "volume", "value": 1}, "(not_found)"),
                            ({"extension": "fx", "value": 1}, "(bad_request)")):
            told, first = len(self.told()), self.svc.journal.next_id
            self.kit.emit("kit.local.option", data)
            self.assertTrue(until(lambda: len(self.told()) == told + 1), (data, self.kit.calls))   # the true values
            self.assertEqual(self.options(), [VOLUME], data)
            self.assertTrue(until(lambda: any("from /wk is not taken" in t and words in t
                                              for t in self.notes(first))), (data, self.notes(first)))
        self.assertEqual(self.ok("POST", "/api/local/call", {"cap": "fx.opts"})["result"], {"volume": 50})

    def test_only_wuxiankit_says_it(self):
        self.described()
        told = len(self.told())
        for addon in ("WuxianCoCreate", "local", "WoWBridge"):
            self.svc.on_debug("EVENT", json.dumps({"a": addon, "t": "kit.local.option",
                                                   "d": {"extension": "fx", "name": "volume", "value": 90}}))
        self.kit.emit("kit.local.optional", {"extension": "fx", "name": "volume", "value": 90})   # another topic
        time.sleep(0.5)
        self.assertEqual((len(self.told()), self.options()), (told, [VOLUME]))
        self.assertEqual(self.ok("POST", "/api/local/call", {"cap": "fx.opts"})["result"], {"volume": 50})


if __name__ == "__main__":
    unittest.main()
