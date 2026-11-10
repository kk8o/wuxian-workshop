---
title: Both sides
zh: 游戏 · 本机
tier: 3
kind: chapter
order: 50
summary: A local extension that also needs something of its own inside the game (its own Read capability, its own event, its own window): new_extension with where both.
---
# Both sides (游戏 · 本机)

A process on this computer that also needs something of its own in the game. Before you go here, check that tier 2
is not enough: a local extension can already call every Read, Store and Guide capability in the game, WuxianKit's
and those of extensions in the game (`ext.call("sense.bags")`), and a reaction can hand it what the game says. A
game half is for what no capability gives yet.

## The loop

1. `new_extension {"id": "zones", "title": "...", "where": "both"}`. It writes the local half (`main.py`, the
   manifest, tests) and the game half in `addons/<Addon>/`: a WuxianKit extension `<id>_game` with a Read capability
   and an event, used by `main.py`. The two halves cannot share an id: their tools share the prefix `wk_`.
2. Read `guide {"topic": "local-extension"}` in full (its section "The game half"), and
   `guide {"topic": "game-extension"}` for the Lua.
3. `extension_load {"id": "zones"}`: it checks, installs the game half when the game has none or other files
   (`addons`, `restart`, `hint` in the answer), restarts the process and runs its tests. `needs_approval`: tell the
   player `row.player.text`, then `extension_wait {"id": "zones"}`.
4. Changed Lua of the game half: `extension_load`, then `load {"target": "<Addon>"}` (a load of a local extension
   does not run Lua in the game by itself). New files and `.toc` changes need a game restart: `row.player` says so.
5. Test each half by itself, then together: `wk_zones_game_<name>` (the game), `wk_zones_<name>` (this computer),
   `extension_logs {"id": "zones"}` for the process, `logs` and `errors` for the Lua.

## How the halves meet

- The local half asks the game half: `ext.call("zones_game.where")`. `no_capability`: the addon is not loaded in
  the game; `down`: the game is not running. Answer something useful in both cases.
- The game half tells the local half: it emits an event, and a reaction in the manifest hears it
  (`"hear": "bus:zones_game.moved"`) and calls one of the local half's capabilities with the item.
- The local half shows the player something: a Guide capability of the game half, or WuxianKit's own
  (`wk_guide_note` and the like), called the same way.

## Split the work

Keep the game half small: read, emit, show. Thinking, memory, files, models and the network belong to the process.
A game half that grows logic is slow to change (new files need a restart) and cannot be tested without the game;
the local half has `ExtensionTest` and runs at every load.

## The verdict

Say which half proved what: the capability in the game answered X; the process did Y with it in Z seconds; what the
player had to do (the approval, one restart).
