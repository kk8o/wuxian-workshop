---
title: Compute here, show there
zh: 本机计算，游戏里显示
tier: 2 → 3
kind: case
order: 40
summary: "Work something out on this computer and show it in the game" (a route, a plan, a spoken line with a mark on the map): usually tier 2 with WuxianKit's guide tools, tier 3 only for a window of your own.
---
# Case: compute here, show there (本机计算，游戏里显示)

The player says something like: 「根据我背包和任务，在本机算一条跑图路线，然后在游戏里一步步标出来。」

## 1. Split it

- **Read from the game**: the quests, the position, the bags.
- **Compute on this computer**: the route (a solver, a model, a data file too big for the game).
- **Show in the game**: a mark on the map, a note on the screen, step by step.

The question that decides the tier is the third: does a tool that shows it exist?

## 2. Tier 2 first

`wk_docs` lists what is there. Reading: `wk_sense_quests`, `wk_sense_position`, `wk_sense_bags`. Showing:
WuxianKit's guide tools (`wk_guide_waypoint`, `wk_guide_note`, `wk_guide_highlight`, `wk_guide_tour`,
`wk_guide_clear`). Try the showing by hand before you write anything: call `wk_guide_waypoint` once and `snap`.

If those are enough, the idea is a local extension and no Lua:

1. `new_extension {"id": "router", "title": "路线"}`, and `guide {"topic": "local-extension"}`.
2. `plan` (see): reads the game through `ext.call("sense.quests")` and `ext.call("sense.position")`, computes,
   answers the steps.
3. `show` (point): calls the guide capability for the step it is given. A local extension may call Read, Store and
   Guide capabilities; anything that changes the game stays a proposal the agent makes.
4. `extension_load`, the approval if `row.player` says so, then `wk_router_plan` and `wk_router_show`.

## 3. Tier 3 only when the showing is yours

A window of your own (a list of the steps with buttons, a bar that follows the player) is not something a guide
tool draws. Then: `guide {"topic": "both"}`. `new_extension` with `"where": "both"`; the game half gets one Guide
capability (`ext:Point`) that draws what it is handed, and perhaps an event when the player clicks a step; the
process keeps all the computing.

Signs that you need the game half: the player must interact with what is shown; it must update many times a second
(position, a timer); it must look a certain way.

## 4. The verdict

> **能做，不用写游戏内代码。** 任务和位置由现成的工具读到；路线在本机算（12 个任务 0.3 秒）；显示用扩展框架
> 自带的路径点和提示，已实测在地图上标出第一步。需要你在无限工坊 App 的「扩展」页批准一次。
> 如果想要一个自己的步骤窗口，要加游戏内的一半：多一次重启游戏，之后改动窗口也要重载。

Conditions to name when they apply: what cannot be read in combat or in an instance; that the route is as good as
the data file behind it; that walking it is always the player's (no addon may move the character).
