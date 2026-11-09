"""Presence counts only the game's WuxianKit tools an agent got: a local extension's wk_ tools are not WuxianKit's."""
import unittest
from types import SimpleNamespace

from wuxianworkshop.mcp.presence import Presence


class Counts(unittest.TestCase):
    def test_counts(self):
        p = Presence()
        self.assertTrue(p.counts("wk_voice_say"))              # no KitTools: any wk_ tool
        self.assertFalse(p.counts("status"))
        p.kit = SimpleNamespace(tools=[SimpleNamespace(name="wk_docs"), SimpleNamespace(name="wk_sense_bags")])
        self.assertTrue(p.counts("wk_sense_bags"))
        self.assertFalse(p.counts("wk_voice_say"))             # a local extension's
        p.kit = SimpleNamespace(tools=[])                      # the game has no WuxianKit
        self.assertFalse(p.counts("wk_voice_say"))


if __name__ == "__main__":
    unittest.main()
