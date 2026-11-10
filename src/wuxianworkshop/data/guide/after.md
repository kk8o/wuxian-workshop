---
title: After the verdict
zh: 验证之后
tier: -
kind: chapter
order: 60
summary: The idea works: keeping it, handing it to another computer, making it an AI app, running it without the coding agent.
---
# After the verdict (验证之后)

The idea works. What comes next is the player's choice; say what the choices are and what each costs. Do not start
any of them unasked.

## 1. Keep it

It stays where it is and runs as it does now. Leave it in order: `AGENT.md` true (what the tools do, what the
events carry, what is unfinished), `extension_check` clean, no keys in the folder. For an addon or a game half: the
player restarts the game once, so that it is loaded by the game itself.

## 2. Hand it to another computer

Set `version` in the manifest, then `extension_pack {"id": "..."}`: one `.zip`. The other player adds it on the
Extensions (扩展) page of their 无限工坊 App, or their agent with `extension_add`; they approve it there before
anything of it runs. Dependencies are not installed for them: pack what it needs. There is no marketplace and no
automatic update: a package is a file one person gives another.

## 3. Make it an AI app

An extension is parts: tools, events. An app is what the player starts: a card in the game with 交给 AI (Hand to
AI), which says what the agent hears, sees and may do, at what pace, and by which skill.
`wk_docs {"skill": "kit-app"}` gives the three ways to declare one; for an extension, `apps` in its manifest (a
local extension) or a declaration in its addon (one in the game). `adds` gives another app what only this computer
has. The cases `ai-app` and `enrich-app` walk through both.

## 4. Change the brain

While you verify, the brain is the coding agent in this conversation: it reads the skill, hears the wakes
(`wuxian listen <app>`) and calls the tools over MCP. Nothing else in the chain depends on that. In the order they
usually come off:

| The brain | How it connects | What changes |
|---|---|---|
| A coding agent (now) | MCP: the tools, the skill, `wuxian listen <app>` | nothing |
| No brain, for the fixed parts | reactions (`reaction_set`, or the app's own in the manifest) and an app's controls | what always follows from an event stops costing a model call |
| A program of your own | the Python SDK (`sdk/python/wuxiankit.py`: `Client`, `kit.app(id)`, `kit.call`, `kit.propose`) | the loop is your code, with any model |

The extensions, the app's declaration, the player's approvals and the proposals they confirm stay exactly as they
are: the brain can be changed, the rest does not move. So verify with the coding agent first, and peel the chain
off only when the idea has earned it.

## 5. A product for many players

Outside what extensions are for. An extension is a prototype on the player's own computer: no signing, no update
channel, no support for other setups. If the idea should reach many players, it becomes an addon they install
(`guide {"topic": "addon"}`), or software of its own that uses the SDK. Say so plainly, and do not dress a prototype
as a release.

## What to tell the player

One paragraph: what exists now and where, which of the five they could pick, and what the next one would take
(their part and yours).
