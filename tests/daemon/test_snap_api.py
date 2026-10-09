"""snap in the Service (daemon/service.py) over a stand-in for the worker thread whose pictures come in a given order:
while WoWBridge's frame shows a message going up over the part pictured, the picture is taken again (inspect's picture
of a window next to the frame, as seen in the game), not when the message is elsewhere, and not for longer than
SNAP_WAIT; the result says where the frame shows in the picture (link_frame), and the MCP tools say it in words."""
import json
import os
import tempfile
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PIL import Image

from tests.support import render as R
from wuxianworkshop.core import frame as F
from wuxianworkshop.daemon.journal import Journal
from wuxianworkshop.daemon.service import Service
from wuxianworkshop.mcp.server import link_note

SIZE = (800, 400)                       # the client area
CORNER = (12, 120)                      # where the player put WoWBridge's frame
WINDOW = [283, 142, 400, 200]           # an addon's window next to it (the inspect's rect)
GROUND, PANEL = (40, 44, 52), (90, 90, 90)


def screen(W=None, H=None, ftype=F.TYPE_DATA, payload=b""):
    """the client area: the window on the game's ground, and WoWBridge's frame of W x H cells (None: none) at CORNER"""
    img = np.full((SIZE[1], SIZE[0], 3), GROUND, np.uint8)
    x, y, w, h = WINDOW
    img[y:y + h, x:x + w] = PANEL
    if W:
        cells = F.build(W, H, 1, 1, ftype, 0x1234, 7, payload)
        drawn = R.draw(cells, W, H, 4, origin=CORNER, size=SIZE, background=GROUND)
        fx, fy = CORNER
        img[fy:fy + (H + 2) * 4, fx:fx + (W + 2) * 4] = drawn[fy:fy + (H + 2) * 4, fx:fx + (W + 2) * 4]
    return img


ANSWER = screen(128, 32, payload=b"\x02" + b'77 ok =probe (5814 B, 0.3 ms, 1 value): "1/1\\n{...}" ' * 8)   # 520 x 136
SMALL = screen(64, 16, payload=b"\x0277 ok =probe (11 B, 0.0 ms, 1 value): \"true\"")                     # 264 x 72
RESTING = screen(32, 8, F.TYPE_CONTROL, b"HB;n=100;k=86;m=3778;l=3777;q=0")                             # 136 x 40


class Worker:
    """the worker thread's side of a snap: the game window, and request() running the call at once"""

    def __init__(self):
        self.win = SimpleNamespace(w=SIZE[0], h=SIZE[1], minimized=False)
        self.wgc = None

    def request(self, fn, *args):
        fut = Future()
        try:
            fut.set_result(fn(*args))
        except BaseException as e:
            fut.set_exception(e)
        return fut


class Snap(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"WUXIAN_HOME": self.tmp.name})
        self.env.start()
        self.svc = Service(lambda on_debug, log: Worker(), journal=Journal())
        self.svc.SNAP_AGAIN = 0.01
        self.shown, self.taken = [], 0

        def capture(win, wgc):                # the next picture; the last one stays
            self.taken += 1
            return (self.shown.pop(0) if len(self.shown) > 1 else self.shown[0]).copy()
        self.capture = mock.patch("wuxianworkshop.daemon.service.capture", capture)
        self.capture.start()

    def tearDown(self):
        self.capture.stop()
        self.env.stop()
        self.tmp.cleanup()

    @staticmethod
    def picture(res):
        with Image.open(res["path"]) as pic:
            return np.asarray(pic.convert("RGB"))

    async def test_inspect_pictures_the_window_once_its_answer_is_gone(self):
        """the answer's 128 x 32 frame stays up after the daemon read it: the picture waits for the heartbeat"""
        async def run(code, timeout_ms=10000, addon=None, chunk="=run"):
            return dict(ok=True, values=["1/1\n" + json.dumps({"screen": list(SIZE), "frames": [
                {"name": "MyAddonWindow", "rect": WINDOW}]})])
        self.svc.run = run
        self.shown = [ANSWER, ANSWER, RESTING]
        res = await self.svc.inspect("MyAddonWindow", snap=True)
        self.assertEqual(self.taken, 3)
        pic = self.picture(res["snap"])
        self.assertEqual(pic.shape[:2], (224, 424))                      # 12 px round the window
        self.assertEqual(tuple(pic[0, 0]), GROUND)                       # the answer's frame covered [0, 0, 261, 126]
        self.assertEqual(tuple(pic[12, 12]), PANEL)                      # the window's corner
        self.assertNotIn("link_frame", res["snap"])                      # the heartbeat is outside the picture

    async def test_the_whole_screen_and_where_the_frame_is(self):
        self.shown = [SMALL, RESTING]
        res = await self.svc.snap(None, 400)
        self.assertEqual((self.taken, res["width"], res["height"]), (2, 400, 200))
        self.assertEqual(res["link_frame"], dict(rect=[6, 60, 68, 20], busy=False))   # 136 x 40 at 12, 120, halved
        self.shown = [RESTING]
        res = await self.svc.snap([0, 0, 300, 300], None)
        self.assertEqual(res["link_frame"], dict(rect=[12, 120, 136, 40], busy=False))
        self.assertEqual(self.taken, 3)

    async def test_a_message_elsewhere_is_not_waited_for(self):
        self.shown = [ANSWER, RESTING]
        res = await self.svc.snap([600, 300, 150, 80], None)            # nowhere near the frame
        self.assertEqual(self.taken, 1)
        self.assertNotIn("link_frame", res)
        self.shown = [screen(), RESTING]                                 # no frame at all (the link is off): at once
        res = await self.svc.snap(None, None)
        self.assertEqual(self.taken, 2)
        self.assertNotIn("link_frame", res)

    async def test_not_longer_than_snap_wait(self):
        self.svc.SNAP_WAIT, self.svc.SNAP_AGAIN = 0.2, 0.05
        self.shown = [ANSWER]                                            # a long message that keeps going up
        res = await self.svc.snap(WINDOW, None)
        self.assertGreater(self.taken, 2)
        self.assertLess(self.taken, 10)
        self.assertEqual(res["link_frame"], dict(rect=[0, 0, 249, 114], busy=True))   # 532 x 256 is its corner
        self.assertIn("still showed a message after 0.2 s",
                      [e["text"] for e in self.svc.journal.since(0, 10, ["SNAP"])[0]][-1])

    def test_what_the_mcp_tools_say(self):
        self.assertIsNone(link_note(None))
        self.assertIsNone(link_note(dict(path="x.png", width=10, height=10)))
        self.assertEqual(link_note(dict(link_frame=dict(rect=[6, 60, 68, 20], busy=False))),
                         "WoWBridge's link frame (its block of coloured cells, not part of the UI) covers [6, 60, 68, 20] "
                         "of this picture")
        self.assertTrue(link_note(dict(link_frame=dict(rect=[0, 0, 249, 114], busy=True)))
                        .endswith("; a message was still going up, so it is bigger than at rest"))


if __name__ == "__main__":
    unittest.main()
