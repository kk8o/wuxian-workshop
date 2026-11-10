---
title: Probe
zh: 探一下
tier: 0
kind: chapter
order: 10
summary: Whether the game allows it at all: look it up in the manual, try it in the running game, read what came back. No files, nothing for the player to do.
---
# Probe (探一下): an answer without files

For questions of the kind "does the game allow X", "can Lua read Y", "which event fires when Z". Nothing is
installed, and the player only plays.

## Steps

1. **Look it up.** `api_search {"query": "...", "call": "usable"}` works without the game. Read each result's `call`:
   - `ok`: use it;
   - `limited`: read `why`. A secret value in a restricted state, a precondition, a protected method that is fine on
     your own frames, `Undocumented` (the game has it and nothing describes it: try it), `NotInClient` (this client
     lacks it: do not use it);
   - `protected`: secure code only. An addon cannot call it, so that part of the idea is a "cannot".

   `api_get {"name": "..."}` gives one entry in full: arguments, returns, an event's payload, an enum's values.
2. **Read the rules that decide it.** `api_manual` lists the topics. The ones that settle most verdicts: `protected`
   and `secure-templates` (what only a click of the player's may do), `secret` (values an addon may show and not
   read), `taint`, `gamerules`, `forever-api` (what only this client has), `toc`.
3. **Try it in the running game.** `status` first: the link must be online. Then:
   - `run {"code": "return type(C_X.Y)"}`: is it there at all;
   - `try {"code": "...", "events": "BAG_*"}`: do it once and read `ok`, `action`, `errors`, `blocked`, `events`;
   - `trace {"seconds": 15, "events": "LOOT_*"}` while the player does the thing: which events fire, with what;
   - `inspect {"target": "SomeFrame"}`, or `{"mouse": true}` with the player's mouse on it: what a frame is, where,
     and whose;
   - `snap`: what the screen shows.
4. **Read the answer as evidence.** A value that comes back secret, an entry in `blocked`, an error that says a
   function is protected: these are findings, not failures. They are the conditions of the verdict.

## What a probe cannot tell

- `run` and `try` run tainted. Whatever needs an untainted path (a secure button, a click that casts) cannot be
  judged from them: it takes an addon's own files (`guide {"topic": "addon"}`).
- A state that only comes in combat, in an instance or in a group needs the player there: ask them, then `trace` or
  `try`.
- A probe shows that a call works once. Whether the thing holds over a session (events over time, saved variables, a
  reload) is tier 1.
- Another addon's data: `guide {"topic": "other-addon"}` starts with a probe of its own.

## The verdict from a probe

Name the API entries with their `call`, the snippet you ran and what it returned, and the condition if there is one
("works out of combat; in combat the value is secret: it can be shown, not compared"). Offer the next rung only when
the player wants the thing built.
