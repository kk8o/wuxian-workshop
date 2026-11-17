"""A Service's pours over the mock client (tests/support/wowmock.py), for the end-to-end tests whose Service has no
worker: its `run` and `data` are the test's own stand-ins, which run the game until the RUN result is there. The rest
of a long answer comes after the pour's result, so the game has to go on while Service.pour waits: wire() hands the
segments to the Service and runs the game a step at a time for as long as a pour waits for them."""
import asyncio
from unittest import mock

from wuxianworkshop.daemon import service as service_mod


def wire(test, svc, comp, session, step=0.25):
    """svc takes the rest of its long answers from the mock game `session` through the companion comp"""
    comp.on_pour = svc.on_pour
    svc.can_pour, svc.link_is_up, svc.ui_session = comp.pours, lambda: comp.link_up, lambda: comp.current
    for name, value in (("_now", session.now), ("POUR_POLL", 0)):       # a pour's waits in the game's time
        p = mock.patch.object(service_mod, name, value)
        p.start()
        test.addCleanup(p.stop)
    ask = svc.ask_pour
    going = set()

    async def game(key):
        try:
            while key in svc.pours:
                session.run(step)
                await asyncio.sleep(0)
        finally:
            going.discard(key)

    async def ask_pour(p, key, kept, only, timeout_ms, what):
        res = await ask(p, key, kept, only, timeout_ms, what)
        if key not in going:
            going.add(key)
            asyncio.ensure_future(game(key))
        return res
    svc.ask_pour = ask_pour
