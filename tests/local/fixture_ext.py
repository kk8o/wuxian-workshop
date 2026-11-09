"""A local extension for the tests, without the SDK: it speaks the protocol (JSON-RPC over stdio, one object per line)
itself. FIXTURE_MODE in its environment (the manifest's env) changes what it does: "die" exits at once with a traceback
on stderr, "mute" never answers initialize, "say" declares a say capability (refused), "nostop" ignores shutdown.

Capabilities: echo {text} (see; also what initialize got, its environment's WUXIAN_EXTENSION and PYTHONPATH's first
part), slow {seconds} (see, timeout 1), boom {} (see: an error, code 1), picky {n} (see: -32602 unless n is a number),
ask {cap, args} (see: calls that capability through the workshop and answers its result or its error), emit {text}
(point: an item of its source said), opts {} (see: its options now), crash {} (keep: exits with code 3).
Option volume (number 0..100, default 50); source said.
"""
import json
import os
import sys
import threading
import time

MODE = os.environ.get("FIXTURE_MODE", "")
lock = threading.Lock()
state = {"options": {}, "init": None, "next": 0}
waiting = {}


def send(msg):
    with lock:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def answer(rid, result=None, error=None):
    send({"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})})


def ask(cap, args):
    """a call of another capability through the workshop: (result, error)"""
    with lock:
        state["next"] += 1
        rid = f"x{state['next']}"
        ev = threading.Event()
        waiting[rid] = [ev, None]
    send({"jsonrpc": "2.0", "id": rid, "method": "call", "params": {"cap": cap, "args": args}})
    ev.wait(30)
    msg = waiting.pop(rid)[1] or {}
    return msg.get("result"), msg.get("error")


CAPS = [
    {"name": "echo", "kind": "see", "title": "回声", "doc": "answers what it got", "args": {"text": "string"}},
    {"name": "slow", "kind": "see", "title": "慢", "doc": "sleeps", "args": {"seconds": "number"}, "timeout": 1},
    {"name": "boom", "kind": "see", "title": "炸", "doc": "fails", "args": {}},
    {"name": "picky", "kind": "see", "title": "挑剔", "doc": "a number", "args": {"n": "number"}},
    {"name": "ask", "kind": "see", "title": "转问", "doc": "calls another capability", "args": {"cap": "string",
                                                                                               "args": "table?"}},
    {"name": "emit", "kind": "point", "title": "发", "doc": "an item of said", "args": {"text": "string"}},
    {"name": "opts", "kind": "see", "title": "选项", "doc": "its options", "args": {}},
    {"name": "crash", "kind": "keep", "title": "崩", "doc": "exits", "args": {}},
]
if MODE == "say":
    CAPS.append({"name": "speak", "kind": "say", "title": "说", "doc": "a proposal", "args": {}})


def call(rid, name, args, caller):
    if name == "echo":
        return answer(rid, {"text": args.get("text"), "caller": caller, "init": state["init"],
                            "ext": os.environ.get("WUXIAN_EXTENSION"),
                            "path0": os.environ.get("PYTHONPATH", "").split(os.pathsep)[0],
                            "cwd": os.getcwd()})
    if name == "slow":
        time.sleep(args.get("seconds") or 0)
        return answer(rid, {"slept": args.get("seconds")})
    if name == "boom":
        print("boom is about to fail", file=sys.stderr, flush=True)
        return answer(rid, error={"code": 1, "message": "it went boom"})
    if name == "picky":
        if not isinstance(args.get("n"), (int, float)):
            return answer(rid, error={"code": -32602, "message": "n: a number"})
        return answer(rid, args["n"] * 2)
    if name == "ask":
        result, error = ask(args.get("cap"), args.get("args"))
        return answer(rid, {"result": result, "error": error})
    if name == "emit":
        send({"jsonrpc": "2.0", "method": "event", "params": {"source": "said", "data": {"text": args.get("text")}}})
        send({"jsonrpc": "2.0", "method": "event", "params": {"source": "nope", "data": {}}})
        return answer(rid, {"emitted": True})
    if name == "opts":
        return answer(rid, state["options"])
    if name == "crash":
        print("crashing now", file=sys.stderr, flush=True)
        os._exit(3)
    return answer(rid, error={"code": -32601, "message": f"no capability {name}"})


def main():
    if MODE == "die":
        raise RuntimeError("the fixture cannot start")
    print("fixture up", file=sys.stderr, flush=True)
    print("not the protocol", flush=True)                 # stdout that is no JSON-RPC: the output gets it
    for line in sys.stdin:
        msg = json.loads(line)
        method, params, rid = msg.get("method"), msg.get("params") or {}, msg.get("id")
        if method is None:                                 # an answer to one of its calls
            entry = waiting.get(rid)
            if entry:
                entry[1] = msg
                entry[0].set()
            continue
        if method == "initialize":
            if MODE == "mute":
                continue
            state["init"] = params
            state["options"] = dict({"volume": 50}, **(params.get("options") or {}))
            answer(rid, {"protocol": 1, "capabilities": CAPS,
                         "sources": [{"name": "said", "title": "说完", "doc": "a line was said"}],
                         "options": [{"name": "volume", "kind": "number", "title": "音量", "default": 50, "min": 0,
                                      "max": 100}]})
        elif method == "call":
            threading.Thread(target=call, args=(rid, params.get("name"), params.get("args") or {},
                                                params.get("caller")), daemon=True).start()
        elif method == "options":
            state["options"] = params.get("options") or {}
        elif method == "shutdown":
            if MODE == "nostop":
                continue
            answer(rid, {})
            sys.stdout.flush()
            os._exit(0)


if __name__ == "__main__":
    main()
