---
title: Other addons' data into my agent
zh: 把别的插件的数据接进 Agent
tier: 0 → 1
kind: case
order: 30
summary: "I use a quest-guide addon and a loot addon: can my agent get what they know?" Look at each, then one extension in the game that reads them.
---
# Case: other addons' data into my agent (把别的插件的数据接进 Agent)

The player says something like: 「我装了一个任务指引插件和一个掉落插件，能把它们的数据接进我的 Agent 吗？比如它知道我
下一步该去哪、这个首领掉什么。」

## 1. Is it there already?

`wk_docs` first. A quest guide may be served already: WuxianKit's `route` takes providers, and when the player's
guide has one the agent has the current step as a tool and an app hears `route:step`. Then that half of the idea
is 能做 with no code: show the player by calling it.

## 2. Look at each addon (tier 0)

Follow `guide {"topic": "other-addon"}`, section 1, one addon at a time:

- `addons`: installed, enabled, version.
- Read its `.toc` and the files that hold the data, on the disk.
- `run` to confirm in the running game what the source suggested: the global is there, the table has the shape you
  read, the function answers.

Write down, per addon: **where** the data is (a function, a table, saved variables), **when** it changes (an event
or callback of theirs, or none), and **what is missing** (a loot list that exists only as text in a tooltip, say).

Three outcomes are common:

| What you find | Verdict for that addon |
|---|---|
| a function or table in reach, and a callback when it changes | 能做 |
| data in reach, nothing says when it changes | 有条件能做: the agent asks when it needs it; no event |
| nothing in reach: all local to its files, only frames to read | 有条件能做 from frame texts (fragile), or 不能做 |

## 3. One extension that reads them

`new_extension {"id": "my_addons", "title": "插件数据", "where": "game"}`, then per addon:

- a Read capability per question: `guide_step` ("where next"), `boss_loot` taking `{"boss": "string"}`;
- an Event where they tell you of a change: `step_changed`;
- `## OptionalDeps:` both addons in the `.toc`; every capability answers `{available: false, why}` when its addon
  is missing or changed.

`extension_load {"id": "my_addons"}`, then try each tool while the player has the addon's window open, and
`events {"topic": "my_addons.*", "since": -1, "wait": 60}` while they finish a step.

For the quest guide, prefer a provider for `route` over a capability of your own, if none exists
(`other-addon`, section 3): then every app that knows `route` works with it.

## 4. Into the agent

The tools are there for any conversation with this server. For an app, name the events in its `hear`
(`bus:my_addons.step_changed`) and the tools in its skill.

## 5. The verdict

> **任务指引：能做。** 当前步骤从 `<插件>` 的 `<函数/表>` 读到（版本 x.y），步骤变化有回调，已做成
> `wk_my_addons_guide_step` 和事件 `my_addons.step_changed`。
> **掉落插件：有条件能做。** 掉落表在 `<表>` 里读得到，按首领名查；它没有变化通知，也不需要。插件更新后表的结构
> 可能变，变了工具会答 available: false。
> 新插件要重启一次游戏才会由游戏自己载入；现在已经能用。

Always say which version you read and that the other addon's internals may change with an update.
