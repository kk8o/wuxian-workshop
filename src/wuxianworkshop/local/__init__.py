"""Local extensions (本机扩展): WuxianKit's extension model on this computer. A kit extension runs in the game (Lua); a local
extension is a process on this computer (any language) that speaks JSON-RPC over stdio with the daemon. Capabilities,
sources, options, docs and apps are the same things in both, and the agent sees both as wk_<extension>_<name> tools.

    manifest.py    wuxian-extension.json, read and checked; the checks of an extension's description (initialize)
    host.py        the host (Host): the registered extensions and their processes as the daemon uses them, made of
    registry.py      the registry (<state>\\extensions.json, the descriptions in extensions-cache.json) and
                     the approval of each one's command
    process.py       one process of an extension (Proc), its environment and command
    rpc.py           the protocol on its pipe: requests, answers, errors
    logs.py          its output (<state>\\extensions\\<id>\\output.log)
    check.py         check: a process of its own started and initialized, the extension's tests
    adds.py          what the manifests add to apps: their own apps' declarations, reactions and bound controls,
                     wake notes and skill supplements for any app
    reactions.py   reactions: an item heard (a game source through a temporary app, or a local source) calls a
                   capability (see / keep / point) without the agent
"""
