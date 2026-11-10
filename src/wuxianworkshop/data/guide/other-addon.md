---
title: Another addon's data
zh: 接入别的插件
tier: 1
kind: chapter
order: 35
summary: What a quest guide, a loot list or any other installed addon knows, as tools and events for your agent: look first, then an extension in the game that reads it.
---
# Another addon's data (接入别的插件)

The player has a quest-guide addon, a loot addon, a damage meter, and wants what it knows in their agent. Addons
share one Lua state, so what one keeps in reach another can read. You never change the other addon: you read it, and
give what you read a shape of your own.

## 1. Look (tier 0, no files)

- `addons`: is it installed and enabled, which version, what it depends on.
- `addon_api {"addon": "<Name>"}`: what it offers through WoWBridge, if anything.
- How it can be reached, with `run`:
  - a global table: `run {"code": "return type(_G.<Name>)"}`, then its keys;
  - its saved variables (the `.toc` names them: `## SavedVariables`), which are globals once it has loaded;
  - an Ace3 addon: `LibStub("AceAddon-3.0"):GetAddon("<Name>", true)`;
  - messages and callbacks it sends (AceEvent, CallbackHandler): they say when something changed.
- Its source is on the disk, in the game's AddOns: read the `.toc`, then the files that hold what you want. This is
  the fastest way to learn where the data is and when it changes.
- `inspect {"mouse": true}` with the player's mouse on its window: the frame's name and whose it is.

`run` shows tables three levels deep and cuts long output: ask for one thing at a time. What you find is true of
the version installed now. Write that version into the verdict.

Prefer, in this order: a function or event the addon documents for others; its data tables; its saved variables
(often stale until a reload); and last, the text of its frames. Never parse what it prints to chat.

## 2. Make it tools

`new_extension {"id": "...", "where": "game"}`, then, as in `guide {"topic": "game-extension"}`:

- The `.toc` adds `## OptionalDeps: <TheirAddon>`, so that theirs loads first when it is there.
- One Read capability per question the agent will ask ("the current step", "what this boss drops"), each guarded:
  the addon missing, not loaded yet, or of a shape you do not know answers `{available: false, why: "..."}`, never
  an error.
- An Event when their data changes, fed from their callback or message, or from `hooksecurefunc` on a function of
  theirs. Hook after, never replace. Keep the run-twice guard.
- Small, named, structured results: the fields the agent needs, not their whole table. Clean text you hand on
  (`Kit:Clean`).

## 3. An interface WuxianKit already has

Some kinds of data have a place in WuxianKit, and an adapter there serves every app at once. A quest guide is one:
WuxianKit's `route` extension takes providers (`WuxianKit.Route:Provide`; step 8 of
`wk_docs {"skill": "kit-extension"}` shows it, and `WuxianKit_Guides` is the example). Check `wk_docs` for one that
fits before you invent a shape.

## 4. Into your agent

- The agent calls the tools (`wk_<id>_<name>`) and reads the events (`events`).
- An app hears the events as `bus:<id>.<event>` in its `hear` (`guide {"topic": "ai-app"}`).
- A local extension calls the capabilities: `ext.call("<id>.<name>")` (`guide {"topic": "both"}`).

## Cautions

- Do not edit the other addon's files, and do not copy its code or data files into yours: read at run time.
- Its internals are not a contract. Guard every access, and say in `AGENT.md` which version you read.
- A secret value stays secret through any addon. What the game blocks for you it blocks there too.
- Addons that only draw (no data in reach, everything local to their files) give you only frame texts: say
  有条件能做 and name the limit, or 不能做.
