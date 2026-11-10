"""The Python SDK's local extensions (sdk/python/wuxiankit.py: Extension, ExtensionTest): argument specs checked the way
WuxianKit checks them, the declarations and what they refuse, the protocol in memory (ExtensionTest) and in a real
process over stdin / stdout (initialize, calls side by side, error codes, events, calls back to the workshop, the options
notification, stdout kept clean, shutdown), and the Python example of the agent's guide (data/local-extension.md)."""
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.sdk.test_sdk import load_sdk

wk = load_sdk()
SDK_DIR = Path(wk.__file__).resolve().parent
GUIDE = Path(__file__).resolve().parents[2] / "src" / "wuxianworkshop" / "data" / "local-extension.md"


def make(eid="demo"):
    """a small extension: hello (see), boom (fails), slow (waits on a gate), bags (calls sense.bags), a source"""
    ext = wk.Extension(eid, folder=tempfile.gettempdir())
    volume = ext.option("volume", kind="number", default=80, min=0, max=100, title="音量")
    said = ext.source("said", title="说完", doc="Each line: {text}.")
    gate = threading.Event()

    @ext.see("hello", title="打招呼", args={"name": "string?", "mood": "calm|happy?"})
    def hello(name="you", mood="calm"):
        """Returns a greeting.

        More words that stay out of the doc."""
        said.emit({"text": name})
        return {"text": f"hi {name}", "mood": mood, "volume": volume.get(), "caller": ext.caller}

    @ext.point
    def boom():
        raise ValueError("it broke")

    @ext.keep("slow", timeout=5)
    def slow():
        if not gate.wait(5):
            raise wk.ExtensionError("the gate never opened")
        return {"ok": True}

    @ext.see("bags", args={"want": "number?"})
    def bags(want=None):
        return {"free": ext.call("sense.bags", {"limit": 1})["free"]}

    return ext, gate, said


class Specs(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(wk.parse_spec("string"), {"text": "string", "optional": False, "types": ["string"]})
        self.assertEqual(wk.parse_spec("number|string?"), {"text": "number|string?", "optional": True,
                                                            "types": ["number", "string"]})
        self.assertEqual(wk.parse_spec("calm|happy"), {"text": "calm|happy", "optional": False,
                                                        "enum": ["calm", "happy"]})
        self.assertEqual(wk.parse_spec("string|loud")["enum"], ["string", "loud"])     # not all types: the strings
        for bad in ("", "?", "a||b", "a| b", 3, None):
            with self.assertRaises(ValueError, msg=bad):
                wk.parse_spec(bad)

    def test_check_like_the_kit(self):
        specs = {n: wk.parse_spec(s) for n, s in {"text": "string", "n": "number?", "on": "boolean?", "t": "table?",
                                                  "x": "any?", "mood": "calm|happy?", "v": "string|number?"}.items()}
        ok = {"text": "a", "n": 1.5, "on": False, "t": [1], "x": None, "mood": "calm", "v": 2, "extra": None}
        self.assertEqual(wk.check_args("e.c", specs, ok), {"text": "a", "n": 1.5, "on": False, "t": [1], "mood": "calm",
                                                           "v": 2})          # null counts as left out
        for args, want in (({}, "e.c: text is missing (string)"),
                           ({"text": None}, "e.c: text is missing (string)"),
                           ({"text": 1}, "e.c: text is a string, not a number"),
                           ({"text": "a", "n": True}, "e.c: n is a number, not a boolean"),
                           ({"text": "a", "t": "x"}, "e.c: t is a table, not a string"),
                           ({"text": "a", "v": []}, "e.c: v is a string or number, not a table"),
                           ({"text": "a", "mood": "sad"}, 'e.c: mood is one of calm|happy, not "sad"'),
                           ({"text": "a", "mood": 1}, "e.c: mood is one of calm|happy, not 1"),
                           ({"text": "a", "zz": 1}, "e.c: no argument zz (it takes mood (calm|happy?), n (number?), "
                                                    "on (boolean?), t (table?), text (string), v (string|number?), "
                                                    "x (any?))"),
                           ([1], "e.c: args is a JSON object")):
            with self.assertRaises(wk.ExtensionError, msg=args) as cm:
                wk.check_args("e.c", specs, args)
            self.assertEqual((cm.exception.code, cm.exception.message), (-32602, want))
        self.assertEqual(wk.check_args("e.c", {}, None), {})


class Declarations(unittest.TestCase):
    def setUp(self):
        self.ext = wk.Extension("demo", folder=tempfile.gettempdir())

    def test_describe(self):
        ext, _, _ = make()
        d = ext.describe()
        self.assertEqual(d["protocol"], 1)
        self.assertEqual(d["capabilities"][0], {"name": "hello", "kind": "see", "title": "打招呼",
                                                "doc": "Returns a greeting.",
                                                "args": {"name": "string?", "mood": "calm|happy?"}})
        self.assertEqual([(c["name"], c["kind"], c.get("timeout")) for c in d["capabilities"]],
                         [("hello", "see", None), ("boom", "point", None), ("slow", "keep", 5), ("bags", "see", None)])
        self.assertEqual(d["sources"], [{"name": "said", "title": "说完", "doc": "Each line: {text}."}])
        self.assertEqual(d["options"], [{"name": "volume", "kind": "number", "title": "音量", "default": 80, "min": 0,
                                         "max": 100}])
        json.dumps(d)

    def test_options(self):
        e = self.ext
        self.assertEqual(e.option("on").desc, {"name": "on", "kind": "boolean", "title": "on", "default": False})
        self.assertEqual(e.option("words", kind="text", hint="h").desc["default"], "")
        self.assertEqual(e.option("speed", kind="number", min=0.5).desc["default"], 0.5)
        c = e.option("voice", kind="choice", choices=["a", ("b", "Bee"), {"value": "c", "label": "See"}])
        self.assertEqual(c.desc["choices"], [{"value": "a", "label": "a"}, {"value": "b", "label": "Bee"},
                                             {"value": "c", "label": "See"}])
        self.assertEqual(c.get(), "a")
        self.assertEqual(e.option("size", kind="choice", choices={"s": "Small", "l": "Large"}, default="l").get(), "l")
        for kw in (dict(name="Bad"), dict(name="on"), dict(name="x", kind="list"),
                   dict(name="x", kind="choice"), dict(name="x", kind="choice", choices=["a"], default="b"),
                   dict(name="x", kind="text", default=3), dict(name="x", kind="number", default="3"),
                   dict(name="x", kind="number", default=True)):
            name = kw.pop("name")
            with self.assertRaises(ValueError, msg=(name, kw)):
                e.option(name, **kw)

    def test_capabilities_refused(self):
        e = self.ext
        with self.assertRaises(ValueError):
            e.see("Bad")(lambda: 1)
        with self.assertRaises(ValueError):
            e.see("x", args={"a": "strnig||"})(lambda a: 1)
        with self.assertRaises(ValueError):
            e.see("x", args={"a-b": "string"})(lambda **kw: 1)
        for t in (0, 601, True, "5"):
            with self.assertRaises(ValueError, msg=t):
                e.see("x", timeout=t)(lambda: 1)
        with self.assertRaises(TypeError):                          # declared, but the function has no such parameter
            e.see("x", args={"a": "string"})(lambda: 1)
        with self.assertRaises(TypeError):                          # optional without a default
            e.see("x", args={"a": "string?"})(lambda a: 1)
        with self.assertRaises(TypeError):                          # the function needs one args does not declare
            e.see("x")(lambda a: 1)
        e.see("x", args={"a": "string", "b": "number?"})(lambda **kw: kw)       # **kw takes any
        with self.assertRaises(ValueError):
            e.see("x")(lambda: 1)                                   # twice
        with self.assertRaises(ValueError):
            e.source("Said")
        e.source("said")
        with self.assertRaises(ValueError):
            e.source("said")

    def test_the_id_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / wk.MANIFEST).write_text(json.dumps({"id": "voice", "title": "声音"}), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "WUXIAN_EXTENSION"}
            with mock.patch.dict(os.environ, env, clear=True):
                ext = wk.Extension(folder=d)
                self.assertEqual((ext.id, ext.title, ext.manifest["id"]), ("voice", "声音", "voice"))
            with mock.patch.dict(os.environ, {"WUXIAN_EXTENSION": "other"}):
                self.assertEqual(wk.Extension(folder=d).id, "other")
            self.assertEqual(wk.Extension("mine", folder=d).id, "mine")
        self.assertEqual(wk.Extension("x", folder=Path(d) / "gone").manifest, {})

    def test_the_data_folder(self):
        """ext.data: the folder the workshop names (WUXIAN_EXTENSION_DATA), else the same place under WUXIAN_HOME (or
        %LOCALAPPDATA%\\WuxianWorkshop); made when asked for, not before"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("WUXIAN_EXTENSION", "WUXIAN_EXTENSION_DATA", "WUXIAN_HOME")}
            with mock.patch.dict(os.environ, dict(env, WUXIAN_EXTENSION_DATA=str(root / "given" / "data")), clear=True):
                ext = wk.Extension("voice", folder=d)
                self.assertFalse((root / "given").exists())                 # not made by the declaration
                self.assertEqual(ext.data, root / "given" / "data")
                self.assertTrue((root / "given" / "data").is_dir())
                (ext.data / "kept.json").write_text("{}", encoding="utf-8")
                self.assertEqual([p.name for p in ext.data.iterdir()], ["kept.json"])     # asked again: as it was
                self.assertEqual(wk.Extension(folder=root / "gone").data, root / "given" / "data")    # the id is not needed
            with mock.patch.dict(os.environ, dict(env, WUXIAN_HOME=str(root / "home")), clear=True):
                self.assertEqual(wk.Extension("voice", folder=d).data,
                                 root / "home" / "state" / "extensions" / "voice" / "data")
                self.assertTrue((root / "home" / "state" / "extensions" / "voice" / "data").is_dir())
                self.assertEqual(wk.daemon_json_path().parent, root / "home" / "state")   # beside daemon.json's folder
                for eid in (None, "Bad", "../up", "a" * 25):
                    with self.assertRaises(ValueError, msg=eid):
                        wk.Extension(eid, folder=root / "gone").data
                self.assertEqual(sorted(p.name for p in (root / "home" / "state" / "extensions").iterdir()), ["voice"])
            with mock.patch.dict(os.environ, dict(env, LOCALAPPDATA=str(root / "local")), clear=True):
                self.assertEqual(wk.Extension("voice", folder=d).data,
                                 root / "local" / "WuxianWorkshop" / "state" / "extensions" / "voice" / "data")

    def test_call_outside_a_run(self):
        with self.assertRaises(wk.ExtensionError) as cm:
            self.ext.call("sense.bags")
        self.assertEqual((cm.exception.code, cm.exception.reason, cm.exception.cap), (1, "down", "sense.bags"))


class InMemory(unittest.TestCase):
    def setUp(self):
        self.ext, self.gate, self.said = make()
        self.t = wk.ExtensionTest(self.ext, options={"volume": 30}, answers={"sense.bags": {"free": 7}})
        self.addCleanup(self.t.close)
        self.addCleanup(self.gate.set)

    def test_initialize_and_call(self):
        d = self.t.initialize()
        self.assertEqual([c["name"] for c in d["capabilities"]], ["hello", "boom", "slow", "bags"])
        self.assertEqual(self.t.call("hello", {"name": "Thrall"}),
                         {"text": "hi Thrall", "mood": "calm", "volume": 30, "caller": {"by": "agent"}})
        who = {"by": "workshop", "chain": ["reaction:r1"]}
        self.assertEqual(self.t.call("demo.hello", caller=who)["caller"], who)     # the full id works too
        self.assertEqual(self.t.next_event("said"), {"source": "said", "data": {"text": "Thrall"}})
        self.assertEqual(self.t.next_event()["data"], {"text": "you"})
        self.assertIsNone(self.ext.caller)                               # only inside a capability

    def test_error_codes(self):
        cases = ((("hello", {"name": 1}), -32602, "demo.hello: name is a string, not a number"),
                 (("nope", {}), -32601, "no capability demo.nope (it has: bags, boom, hello, slow)"),
                 (("boom", {}), 1, "ValueError: it broke"))
        with mock.patch("sys.stderr"):                                   # boom's traceback
            for (name, args), code, message in cases:
                with self.assertRaises(wk.ExtensionError) as cm:
                    self.t.call(name, args)
                self.assertEqual((cm.exception.code, cm.exception.message), (code, message))

    def test_extension_error_and_non_json(self):
        @self.ext.see("refuse", args={"why": "string"})
        def refuse(why):
            raise wk.ExtensionError(why, -32602 if why == "args" else 1)

        @self.ext.see("weird")
        def weird():
            return {1, 2}
        for why, code in (("args", -32602), ("plain", 1)):
            with self.assertRaises(wk.ExtensionError) as cm:
                self.t.call("refuse", {"why": why})
            self.assertEqual((cm.exception.code, cm.exception.message), (code, why))
        with self.assertRaises(wk.ExtensionError) as cm:
            self.t.call("weird")
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("not JSON", cm.exception.message)
        with self.assertRaises(TypeError):
            self.said.emit("not a dict")

    def test_calls_side_by_side(self):
        out = []
        th = threading.Thread(target=lambda: out.append(self.t.call("slow")))
        self.t.initialize()
        th.start()
        time.sleep(0.1)
        self.assertEqual(self.t.call("hello", {"name": "x"})["text"], "hi x")   # not held up by slow
        self.assertEqual(out, [])
        self.gate.set()
        th.join(5)
        self.assertEqual(out, [{"ok": True}])

    def test_calling_back(self):
        self.assertEqual(self.t.call("bags"), {"free": 7})
        self.assertEqual(self.t.calls, [{"cap": "sense.bags", "args": {"limit": 1}}])
        self.t.answer("sense.bags", lambda args: {"free": args["limit"] + 1})
        self.assertEqual(self.t.call("bags"), {"free": 2})

        def down(args):
            raise wk.ExtensionError("the game is not connected", reason="down")
        self.t.answer("sense.bags", down)
        with self.assertRaises(wk.ExtensionError) as cm:                # its error: this capability failed
            self.t.call("bags")
        self.assertEqual((cm.exception.code, cm.exception.message), (1, "sense.bags: the game is not connected"))
        del self.t.answers["sense.bags"]
        with self.assertRaises(wk.ExtensionError) as cm:
            self.t.call("bags")
        self.assertTrue(cm.exception.message.startswith("sense.bags: no capability sense.bags"), cm.exception.message)

    def test_options(self):
        seen = []
        self.ext.on_options(seen.append)
        self.t.initialize()
        self.assertEqual(self.ext.options, {"volume": 30})
        self.assertEqual(seen, [])                                       # not at initialize
        self.t.set_options({"volume": 55, "unknown": 1})
        self.assertEqual(seen, [{"volume": 55}])
        self.assertEqual(self.t.call("hello")["volume"], 55)
        self.t.set_options({})                                           # left out: the default
        self.assertEqual(self.t.call("hello")["volume"], 80)

    def test_events_wait_for_initialize(self):
        self.said.emit({"text": "early"})
        self.assertEqual(self.t.events, [])
        self.t.initialize()
        self.assertEqual(self.t.next_event("said")["data"], {"text": "early"})

    def test_setup_runs_once_and_a_failed_one(self):
        runs = []
        t = wk.ExtensionTest(self.ext, setup=lambda: runs.append(1))
        t.initialize()
        t.initialize()
        self.assertEqual(runs, [1])
        t.close()

        def bad():
            raise RuntimeError("no model")
        t = wk.ExtensionTest(self.ext, setup=bad)
        self.addCleanup(t.close)
        with mock.patch("sys.stderr"), self.assertRaises(wk.ExtensionError) as cm:
            t.initialize()
        self.assertEqual((cm.exception.code, cm.exception.message), (1, "setup failed: RuntimeError: no model"))

    def test_raw_messages_and_shutdown(self):
        self.assertEqual(self.t.send("frobnicate")["error"]["code"], -32601)
        self.assertEqual(self.t.send("call", {"name": "hello", "args": [1]})["error"],
                         {"code": -32602, "message": "demo.hello: args is a JSON object",
                          "data": {"reason": "bad_args"}})
        self.assertEqual(self.t.shutdown()["result"], {})


EXT_SOURCE = r'''
import subprocess
import sys
import threading
import time

from wuxiankit import Extension, ExtensionError

ext = Extension()
volume = ext.option("volume", kind="number", default=80, min=0, max=100)
said = ext.source("said", title="Said", doc="Each line: {text}.")


@ext.on_options
def changed(values):
    said.emit({"text": f"volume {values['volume']}"})


@ext.point("say", args={"text": "string", "mood": "calm|happy?"})
def say(text, mood="calm"):
    print("saying", text)                                   # to stderr, not the protocol
    said.emit({"text": text})
    return {"seconds": len(text) / 10, "mood": mood, "volume": volume.get(), "by": ext.caller}


@ext.see("slow", args={"s": "number"})
def slow(s):
    time.sleep(s)
    return {"slept": s}


@ext.see("fail")
def fail():
    raise ExtensionError("cannot")


@ext.see("bags")
def bags():
    return {"free": ext.call("sense.bags")["free"]}


@ext.see("child")
def child():
    subprocess.run([sys.executable, "-c", "print('from a child process')"], check=True)
    import os
    os.write(1, b"raw write to fd 1\n")
    return {"ok": True}


def setup():
    if "--fail" in sys.argv:
        raise RuntimeError("no model")
    import os
    os.write(1, b"a C library writes to fd 1 while the model loads\n")    # CUDA, a TTS library ...
    sys.__stdout__.write("sys.__stdout__ too\n")
    sys.__stdout__.flush()
    print("ready")


if __name__ == "__main__":
    ext.run(setup)
    print("after run")
'''


class Process(unittest.TestCase):
    """a real extension process, spoken to over its stdin / stdout as the workshop does"""

    def start(self, *argv):
        d = Path(self.tmp.name)
        (d / "main.py").write_text(EXT_SOURCE, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(SDK_DIR), WUXIAN_EXTENSION="voice", PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8")
        p = subprocess.Popen([sys.executable, "main.py", *argv], cwd=d, env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.wait(5), p.stdin.close(), p.stdout.close(), p.stderr.close()))
        self.lines, self.raw, self.err = queue.Queue(), [], []

        def read_out():
            for line in p.stdout:
                self.raw.append(line)
                self.lines.put(json.loads(line))                    # anything but JSON fails here
        threading.Thread(target=read_out, daemon=True).start()
        threading.Thread(target=lambda: self.err.extend(p.stderr), daemon=True).start()
        self.p = p
        return p

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.next_id = 0

    def send(self, msg):
        self.p.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def request(self, method, params=None):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        return self.next_id

    def get(self, timeout=10):
        return self.lines.get(timeout=timeout)

    def answer_of(self, mid, others=None, timeout=10):
        """the answer to request mid; other messages meanwhile go to others"""
        while True:
            m = self.get(timeout)
            if m.get("id") == mid and "method" not in m:
                return m
            if others is not None:
                others.append(m)

    def initialize(self, options=None):
        mid = self.request("initialize", {"protocol": {"min": 1, "max": 1}, "workshop": "0.9.13", "id": "voice",
                                          "options": options or {}})
        return self.answer_of(mid)

    def test_the_protocol(self):
        self.start()
        init = self.initialize({"volume": 40})
        self.assertEqual(init["jsonrpc"], "2.0")
        self.assertEqual([c["name"] for c in init["result"]["capabilities"]], ["say", "slow", "fail", "bags", "child"])
        self.assertEqual(init["result"]["capabilities"][0]["args"], {"text": "string", "mood": "calm|happy?"})

        others = []                                                 # a call, its event
        ans = self.answer_of(self.request("call", {"name": "say", "args": {"text": "你好"},
                                                   "caller": {"by": "player", "app": "companion"}}), others)
        self.assertEqual(ans["result"], {"seconds": 0.2, "mood": "calm", "volume": 40,
                                         "by": {"by": "player", "app": "companion"}})
        if not others:
            others.append(self.get())
        self.assertEqual(others, [{"jsonrpc": "2.0", "method": "event", "params": {"source": "said",
                                                                                   "data": {"text": "你好"}}}])

        for params, code in (({"name": "say", "args": {}}, -32602), ({"name": "say", "args": {"text": "a", "mood": "x"}},
                             -32602), ({"name": "nope"}, -32601), ({"name": "fail"}, 1)):
            self.assertEqual(self.answer_of(self.request("call", params))["error"]["code"], code, params)
        self.send("a string")                                         # JSON, not an object
        self.assertEqual(self.get()["error"]["code"], -32600)
        self.p.stdin.write(b"{not json\n")
        self.p.stdin.flush()
        self.assertEqual(self.get()["error"]["code"], -32700)

        t0 = time.monotonic()                                        # side by side
        a, b = self.request("call", {"name": "slow", "args": {"s": 1}}), self.request("call", {"name": "slow",
                                                                                               "args": {"s": 1}})
        got = {self.get()["id"], self.get()["id"]}
        self.assertEqual(got, {a, b})
        self.assertLess(time.monotonic() - t0, 1.8)

        mid = self.request("call", {"name": "bags"})                 # it calls back
        req = self.get()
        self.assertEqual((req["method"], req["params"]), ("call", {"cap": "sense.bags", "args": {}}))
        self.send({"jsonrpc": "2.0", "id": req["id"], "result": {"free": 12}})
        self.assertEqual(self.answer_of(mid)["result"], {"free": 12})
        mid = self.request("call", {"name": "bags"})
        req = self.get()
        self.send({"jsonrpc": "2.0", "id": req["id"], "error": {"code": 1, "message": "no game",
                                                                "data": {"reason": "down"}}})
        self.assertEqual(self.answer_of(mid)["error"], {"code": 1, "message": "sense.bags: no game",
                                                        "data": {"reason": "failed"}})

        self.send({"jsonrpc": "2.0", "method": "options", "params": {"options": {"volume": 70}}})
        self.assertEqual(self.get()["params"], {"source": "said", "data": {"text": "volume 70"}})

        self.assertEqual(self.answer_of(self.request("call", {"name": "child"}))["result"], {"ok": True})

        mid = self.request("shutdown")
        self.assertEqual(self.answer_of(mid)["result"], {})
        self.assertEqual(self.p.wait(5), 0)
        time.sleep(0.2)
        err = b"".join(self.err).decode("utf-8")
        for words in ("ready", "saying 你好", "from a child process", "raw write to fd 1", "after run",
                      "a C library writes to fd 1 while the model loads", "sys.__stdout__ too"):
            self.assertIn(words, err)
        self.assertTrue(all(line.startswith(b"{") for line in self.raw))

    def test_a_failed_setup_and_stdin_closed(self):
        self.start("--fail")
        ans = self.initialize()
        self.assertEqual(ans["error"], {"code": 1, "message": "setup failed: RuntimeError: no model",
                                        "data": {"reason": "failed"}})
        self.assertEqual(self.p.wait(5), 1)
        self.start()
        self.assertIn("result", self.initialize())
        self.p.stdin.close()                                         # the workshop went away
        self.assertEqual(self.p.wait(5), 0)


class Guide(unittest.TestCase):
    """data/local-extension.md, the standard: its Python example runs, and it names the tools of the loop"""

    def test_the_example_runs(self):
        text = GUIDE.read_text(encoding="utf-8")
        code = next(b for b in re.findall(r"```python\n(.*?)```", text, re.S) if "ext.run(setup)" in b)
        module = type(sys)("guide_example")
        with mock.patch.dict(sys.modules, {"wuxiankit": wk}):
            exec(compile(code, "local-extension.md", "exec"), module.__dict__)
        t = wk.ExtensionTest(module.ext, setup=module.setup, answers={"sense.bags": {"free": 9}})
        self.addCleanup(t.close)
        with mock.patch("sys.stdout"):
            caps = [c["name"] for c in t.initialize()["capabilities"]]
        self.assertEqual(caps, ["set", "list", "bags"])
        at = t.call("set", {"name": "tea", "text": "Tea is ready", "after": 0.5})["at"]
        self.assertEqual([r["name"] for r in t.call("list")], ["tea"])
        self.assertEqual(t.next_event("rang", timeout=5)["data"], {"text": "Tea is ready", "at": at})
        self.assertEqual(t.call("bags"), {"free": 9})
        with self.assertRaises(wk.ExtensionError):
            t.call("set", {"name": "x", "text": "y", "after": -1})

    def test_it_names_the_loop(self):
        text = GUIDE.read_text(encoding="utf-8")
        for word in ("new_extension", "extension_load", "extension_logs", "extension_check", "wk_<id>_<name>",
                     "wuxian-extension.json", "-32602", "-32601", "shutdown", "ExtensionTest", "stderr"):
            self.assertIn(word, text)
        for block in re.findall(r"```json\n(.*?)```", text, re.S):
            json.loads(block)
        for line in re.findall(r"^[<>-]+ (\{.*\})$", text, re.M):
            json.loads(line)


if __name__ == "__main__":
    unittest.main()
