---
title: It works: keep it, share it, let it run
zh: 验证过了，留下来
tier: after
kind: case
order: 60
summary: "The prototype works. I want to keep it, give it to a friend, and not need this conversation for it": tidy, pack, declare an app, change the brain.
---
# Case: it works: keep it, share it, let it run (验证过了，留下来)

The player says something like: 「这个做出来挺好用，我想留着，也发给公会的朋友，最好不用每次都开着编码对话。」

Three wishes, three different amounts of work. Take them apart and answer each
(`guide {"topic": "after"}` has the whole picture).

## 1. Keep it

Already true: an extension stays registered, an addon stays in AddOns. Leave it in a state the next conversation can
pick up without this one:

- `extension_check {"id": "..."}` clean; an addon: `check {"target": "..."}`.
- `AGENT.md` says what is true now: each tool and event, the options, what is unfinished, which versions of other
  addons it was read against.
- No keys in the folder. A key lives in a file of the player's outside it, or in their environment.
- A new addon or game half: the player restarts the game once (`row.player`), so it no longer depends on a hot load.
- `local_extensions`: the row is as you describe it to the player.

## 2. Give it to a friend

- An extension: set `version` in the manifest, `extension_pack {"id": "..."}`; the answer names the file and what
  was left out. Tell the player what the friend needs: 无限工坊, WuxianKit for a game half, and whatever the process
  uses (Python packages, a model, a key of their own). The friend adds the file on the Extensions (扩展) page and
  approves it there.
- An addon: its folder in the game's AddOns is the whole thing; it works without 无限工坊 if you kept the
  WoWBridge stand-in from the template.
- Say what a package is not: no update channel, no support. A newer version is a new file.

## 3. Without this conversation

What needs the coding agent now? Sort the pieces:

| Piece | Without a brain? |
|---|---|
| Tools an agent calls when asked | needs a brain, any: every conversation with this server has them |
| "When X happens, do Y", always the same | a reaction: `reaction_set`, or the app's own in the manifest. No model |
| A button the player presses | an app's control bound to a capability. No model |
| Judging, wording, deciding | a brain: this conversation, another agent with the app's skill, or a program with the SDK |

So first declare the app (`wk_docs {"skill": "kit-app"}`), which makes it startable from its card by any AI
conversation; then move what is mechanical into reactions; and only if the player wants it running with no agent
at all, write the loop as a program with the Python SDK. Each step leaves the extensions and the player's approvals
as they are.

## 4. What to tell the player

> 现在的状态：扩展「…」已登记，随无限工坊启动；游戏里的一半重启后由游戏自己载入。
> 发给朋友：我可以打一个包（一个 .zip），对方在「扩展」页添加并批准；对方要自己装 … 。
> 不开编码对话：固定的反应（听到 … 就 …）可以交给工坊自己做，不用 AI；需要判断和措辞的部分，可以做成应用，
> 从游戏里的卡片交给任意 AI；要完全独立运行，得写一个小程序，这一步工作量最大，建议用一段时间再决定。
