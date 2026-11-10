---
title: An addon
zh: 插件
tier: 1
kind: chapter
order: 20
summary: Something the player uses in the game by themselves (a window, a bar, a slash command): new_addon, load, try, errors.
---
# An addon (插件)

When the idea is something the player uses in the game by themselves (a window, a bar, a slash command, a change to
how something looks) and no agent has to be there while it runs.

## An addon, or an extension in the game?

| | Addon | Extension in the game |
|---|---|---|
| For | the player | an agent: tools to call, things to hear |
| Needs | the game | WuxianKit in the game |
| Made with | `new_addon` | `new_extension` with `"where": "game"` |
| Its files | in the game's AddOns | in the extension's folder; the workshop installs its addon |
| Reaches the agent | if you want: a WoWBridge handle (below) | its capabilities are `wk_<id>_<name>` tools |

## The loop

1. `new_addon {"name": "MyAddon", "title": "...", "template": "basic"}` (`"window"` adds a draggable window). It
   writes the `.toc`, the Lua and an `AGENTS.md` in the game's AddOns: read that file and follow it.
2. Edit the files. Look every API up before you use it (`api_search`).
3. `load {"target": "MyAddon"}`: it checks the files, then runs them in the running game. Fix every error in its
   `check`; a syntax error sends nothing.
4. `try {"slash": "/myaddon"}` or `try {"code": "...", "frame": "MyAddonFrame"}`: read `ok`, `errors`, `prints`,
   `blocked`. `snap` and `inspect` show how it looks and where it sits.
5. Change and repeat 3–4. `watch {"action": "start", "target": "MyAddon"}` loads at every save.
6. The game lists a new addon, and reads new files and a changed `.toc`, only when it starts. Until the player has
   restarted it once, the addon is there only as long as the hot load lasts: a UI reload drops it. Say so when a
   restart is due. Only the player reloads (`reload` shows them a button).
7. After the restart: `errors {"addon": "MyAddon"}` (kept from session to session) and `logs` (this session).

## Reaching the agent without WuxianKit

An addon hands the agent data and takes its calls through a WoWBridge handle, bound as the template binds it (with a
stand-in for players without 无限工坊, so the addon works for everyone):

- `WB:Emit(topic, data)`: an event the agent reads with `events`. Use it in place of `print` when you debug.
- `WB:Expose(name, fn, doc)`: a function the agent calls with `call {"addon": "MyAddon", "name": "...", "args": {}}`.
- `WB:Request`: a question to the agent: an event with a `request` id, answered with `respond`.

`addon_api {"addon": "MyAddon"}` lists what an addon offers. The `.toc` says `## OptionalDeps: WoWBridge`.

## An addon that exists

- Changing one you did not make in this session: tell the player, then `checkpoint {"addon": "..."}`. `history` and
  `restore` go back.
- Bringing one over from another version of the game: `check {"target": "..."}` says what this client lacks;
  `api_manual {"topic": "toc"}` for the `.toc`.
- Only reading what one knows, for an agent: `guide {"topic": "other-addon"}`.

## The verdict

As after a probe, and: it loads without errors, the `try` that shows it working, and what still waits for a restart.
