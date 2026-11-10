---
title: Bring an AI application in
zh: 接入一个 AI 应用
tier: 2
kind: case
order: 20
summary: "I have an AI application (a knowledge base, a voice, a model of my own): can it work with the game?" What it hears, what it does on this computer, what it does in the game.
---
# Case: bring an AI application in (接入一个 AI 应用)

The player says something like: 「我有一个自己的知识库问答程序，能不能接进游戏，公会里有人提问时帮我起草回答？」

## 1. Split it three ways

| Part | Here | Where it lives |
|---|---|---|
| What it **hears** from the game | guild chat | a WuxianKit source: `chat:guild` |
| What it **knows or does** on this computer | search the knowledge base | a local extension's capability |
| What it **does** in the game | a reply in guild chat | a Send proposal the player confirms (`wk_chat_send`) |

And the brain that connects them: for now, you. `guide {"topic": "after"}` says how that changes later, and why
nothing else has to.

## 2. Check each part cheaply

- **Hears.** `wk_docs` lists the sources. Peek with a temporary app, nothing kept:
  `wk_kit_app_open {"app": "peek", "temp": {"title": "Peek", "hear": ["chat:guild"]}, "minutes": 15}`, then
  `wk_kit_app_recent {"app": "peek", "since": 0}` after someone has spoken, then `wk_kit_app_stop {"app": "peek"}`.
  You see exactly what an item carries. What the game does not give as a source is a question for a probe, and
  perhaps an extension in the game.
- **Knows or does.** Run their program outside the game first: does it answer in the time a chat allows? How is it
  called (a command, an HTTP port, a Python import)? Where are its keys? They stay in a file of the player's that
  you neither read aloud nor copy.
- **Does.** `wk_docs` lists the Send and Change tools. Each answers a proposal: the player clicks 执行, or it runs at
  once in a category they pre-approved. There is no way around that, and it is the point: the app acts under the
  player's eyes.

If all three check out, the verdict is already 能做: the rest is assembly.

## 3. Assemble

1. `new_extension {"id": "kb", "title": "知识库"}` and read `guide {"topic": "local-extension"}` in full.
2. One capability per thing the brain needs, small: `search` (see) taking `{"query": "string"}` and answering the
   three best passages with their sources. Wrap their program; do not rewrite it.
3. `extension_load {"id": "kb"}` until the check is clean. `needs_approval`: tell the player `row.player.text`, then
   `extension_wait {"id": "kb"}`. Then call `wk_kb_search` yourself.
4. Run the chain once by hand: read a real question from the peek app, call `wk_kb_search`, draft a reply, and (only
   because the player asked for replies) `wk_chat_send`, then `wk_wait`.

## 4. Make it an app, when the chain works

Add `apps` to the manifest: the declaration (`hear`, `pace`, `controls`, `prompt`) and its skill in
`skills/<prompt>.md`. `wk_docs {"skill": "kit-app"}` has the fields; design them with the player. The player then
starts it from its card with 交给 AI, in any conversation, without you rebuilding anything.

If an app that does the hearing and replying exists already (`wk_kit_apps`: 公会聊天 is one), do not make a second:
give it your knowledge instead: `guide {"topic": "enrich-app"}`.

## 5. The verdict

> **能做。** 公会聊天能听到（`chat:guild`，实测一条消息带发言人、正文、时间）；知识库查询封装成 `wk_kb_search`，
> 平均 0.8 秒；回复走提案，每条要你点「执行」，或者你预先批准公会聊天这一类。现在是本对话在驱动；要脱离编码
> Agent 长期运行，见「验证之后」。

Typical conditions to name: the program is too slow for chat (answer later, or only on request); it needs a GPU or
a paid key; what it wants to hear is not a source yet.
