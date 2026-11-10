---
title: Give an existing app what this computer has
zh: 给现有应用加料
tier: 2
kind: case
order: 50
summary: "The guild-chat app should look things up in my local data before it answers": a local extension that adds a wake note and a skill supplement to an app, without changing the app.
---
# Case: give an existing app what this computer has (给现有应用加料)

The player says something like: 「公会聊天助手回答之前，先查一下我本机的资料库。」

The app exists and works (`wk_kit_apps`). The idea is not a new app: it is one more thing the app knows each time
it wakes. A local extension adds that from outside, by the app's id; the app is not edited, and turning the
extension off takes the addition away.

## 1. Check the two ends

- The app: `wk_kit_apps` for its id (`guild`), `wk_kit_app_describe {"app": "guild"}` for what it hears and the
  skill its `prompt` names. Read that skill (`wk_docs {"skill": "<prompt>"}`): your supplement has to fit it.
- The data: can a lookup answer inside the wake's time? A wake note has 3 seconds by default, 10 at most. Slower
  work is a tool the agent calls when it wants to, not a wake note.

## 2. The extension

1. `new_extension {"id": "facts", "title": "资料速查"}`; read `guide {"topic": "local-extension"}`, the section
   "Adding to apps".
2. A `see` capability `lookup` taking `{"wake": "table"}`: from the wake's items it picks what to look up and
   answers the few facts that matter, small and named. Nothing found: an empty list, quickly.
3. `skills/guild-chat.md`: a short text for the agent that follows the app's skill: what `notes.facts` holds, how
   far to trust it, what to do on `notes.facts.error`.
4. The manifest:

   ```json
   {"adds": {"guild": {"wake": "lookup", "skill": "skills/guild-chat.md"}},
    "start": "workshop"}
   ```

   beside the fields `new_extension` wrote. `start: workshop` keeps it warm, so the first wake is not the slow one.
5. `extension_load {"id": "facts"}`: it checks the app id, the file, and that `lookup` exists and is `see`.
   `needs_approval`: `row.player.text`, then `extension_wait {"id": "facts"}`.

## 3. Prove it

- `wk_facts_lookup` by hand with a wake you make up: the answer and its time.
- `wk_docs {"skill": "<prompt>"}` again: your section follows the skill's text, under a heading that names the
  extension.
- A real wake: start the app (`wk_kit_app_open {"app": "guild"}`), have someone speak, read the wake: `notes.facts`
  is there. Stop the app if the player did not ask you to run it.
- The failure: stop the extension (`extension_stop`), make a wake: `notes.facts` is `{error, reason}` and the wake
  still came. A note never loses a wake.

## 4. The verdict

> **能做。** 「资料速查」给公会聊天加了两样：每次唤醒附带一条本机查到的资料（实测 0.2 秒，超时 3 秒不影响唤醒），
> 和一段告诉 AI 怎么用它的说明。公会聊天本身没有改动；在「扩展」页关掉「资料速查」，加的东西就一起撤掉。

Conditions to name: lookups slower than the wake allows become a tool instead; what the data covers and how old it
is; that the agent is told the note is information, not instruction.
