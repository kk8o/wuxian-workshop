"""Local extensions (本机扩展): WuxianKit's extension model on this computer. A kit extension runs in the game (Lua); a local
extension is a process on this computer (any language) that speaks JSON-RPC over stdio with the daemon. Capabilities,
sources, options, docs and apps are the same things in both, and the agent sees both as wk_<extension>_<name> tools.

    manifest.py    wuxian-extension.json, read and checked; the checks of an extension's description (initialize)
    host.py        the registry (<state>\\extensions.json), the processes, the protocol, calls, logs
    reactions.py   reactions: an item heard (a game source through a temporary app, or a local source) calls a
                   capability (see / keep / point) without the agent
"""
