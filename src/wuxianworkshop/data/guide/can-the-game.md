---
title: Can the game do this?
zh: 插件点子能不能做
tier: 0 → 1
kind: case
order: 10
summary: "I have an idea for an addon: does the API allow it, can Lua do it?" A probe, a verdict, then the smallest addon that shows it.
---
# Case: can the game do this? (插件点子能不能做)

The player says something like: 「背包快满的时候，在屏幕中间提醒我一下，能做吗？」

## 1. Split the idea into what it needs

Write the parts down before you look anything up. Here:

- **read**: how many free bag slots there are;
- **know when**: an event when the bags change;
- **show**: a text in the middle of the screen;
- **limits**: does any of it stop in combat, in an instance, for a secret value?

Every idea splits this way: what it reads, when it learns of a change, what it shows or does, and where the game
might say no. A part that *does* something for the player (casts, targets, moves, uses an item) is the one most
likely to be protected: look at it first.

## 2. Look each part up

- `api_search {"query": "bag free slots", "call": "usable"}`, then `api_get` on the entry you will use.
- `api_search {"query": "BAG_UPDATE"}` for the event; `api_get` gives its payload.
- For the text: your own frame with a font string is always yours to show. `api_search` the widget methods you
  will call.
- The limits: `api_manual {"topic": "protected"}`, `{"topic": "secret"}`.

Read `call` on each: `ok`, `limited` (read `why`), `protected`. One `protected` part does not end the idea: it ends
that part. Say what is left without it.

## 3. Try each part in the running game

`status`, then, with the names the manual gave you:

- the read: `run {"code": "return <the call>"}`: a number, as expected?
- the event: `trace {"seconds": 20, "events": "BAG_*"}` and ask the player to move an item: which events came, how
  often, with what?
- the show: `try {"code": "<make a frame, set its text, show it>"}`, then `snap`: is it there? Read `blocked` and
  `errors`.

## 4. The verdict

For this idea it would read like:

> **能做。** 读空格数用 `<函数>`（手册标 ok，实测返回 12）；背包变化有事件 `<事件>`（移动一件物品触发 2 次，
> 需要合并）；屏幕中间的文字是插件自己的框体，战斗中也能显示。要我做成插件吗？

Other ideas end differently, and those answers are as useful:

> **有条件能做。** 血量在副本战斗中是机密值：可以显示成条和百分比，不能拿来比较大小，所以「低于 30% 时喊话」做不了，
> 「显示一个更大的血条」可以。

> **不能做。** 自动选目标并施法要调用受保护函数，插件只能在玩家自己按键时做。最接近的做法：一个安全按钮，玩家按一下。

## 5. If they want it built

Tier 1, an addon: `guide {"topic": "addon"}`. `new_addon`, the three parts in one file, `load`, `try`, and the
restart note. If instead an agent should know when the bags are full, it is an extension in the game:
`guide {"topic": "game-extension"}`, though for this one `wk_docs` would show that `wk_sense_bags` exists already.
