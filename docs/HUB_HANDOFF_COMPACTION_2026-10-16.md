# Write the context down and compact: on model switches and before a plan window runs out

2026-10-16. Live on :6767 (server restarted; runners untouched). Uncommitted.

## Why

- A model switch starts with an empty prompt cache. Today's first Sonnet call in a
  870k-token chat wrote 842k tokens at once, about 1.05M weighted tokens, roughly 9% of
  a Pro 5-hour window by the local estimator.
- A session blocked at 100% of a 5-hour window sits idle for hours. The cache lasts
  1 hour (all writes are the 1h tier), so the first message after the reset rewrites the
  whole context. Compacting before the block makes that rewrite small. It does not save
  quota now.

## What it does

1. **Model switch through the Hub** (`Hub.set_model`, `omnigent/server/hub_handoff.py`):
   when a chat on Claude or Codex changes model and its last turn carried at least
   40,000 tokens, the Hub says it will write down where it is and compact first, then
   runs this in the background: wait for the chat to be idle, ask it for the write-up
   (`auto_compact.SWITCH_DOCUMENTATION_PROMPT`), wait for that turn to end, compact
   (`POST /v1/sessions/{id}/events {"type":"compact"}`), wait for a `compaction` item to
   land, then apply the model. Every wait is bounded (30 min idle, 15 min write-up,
   10 min compaction). If any step fails the switch still happens and the reply says
   what was not done.
2. **Plan window ceiling** (`omnigent/server/quota_handoff.py`, wired into
   `_run_auto_compaction`): at a turn end, if the session's provider window is at or past
   its ceiling and below 100%, the same two-step routine runs (write-up turn, then
   compact on the next turn end) with a prompt that names the window and its reset time.
   Once per window per session; a request that could not be delivered is asked again.
   Needs at least 40,000 tokens of context.

Defaults: 5h ceiling 80%, weekly ceiling 95%. Set them in `~/.omnigent/hub/budget.json`:

```json
{"handoff": {"five_hour_pct": 80, "weekly_pct": 95}}
```

The real plan meter is read, not the Max-as-Pro mock, because the block that matters is
the one the provider enforces. A reading older than 15 minutes, or from a window that
already reset, is ignored.

## When the plan is already full (added 16/10 after the question)

A full window turns the chat's requests away, so it can neither write up nor compact.
Asking would only sit until a timeout. The first version guarded the quota path
(it ignores readings at 100%) but **not the model-switch path**; that is fixed:

- `quota_handoff.exhausted` reads the provider's real meter: a window at 100% that has
  not reset yet (reading up to 1 h old, since a full window stays full) is "full".
- Hub switch: if the chat's window is full, no write-up and no compaction is attempted.
  The model is switched at once and the reply says "its plan window is full, so it could
  not write up or compact first".
- The same check runs again inside the sequence: after waiting for a busy chat, and after
  the write-up turn (which may itself use the last of the window). Either one ends the
  sequence with the switch applied as it was.
- A write-up turn that ends `failed` (the plan turned it away) ends the sequence at once
  instead of sitting out the 15-minute wait, and no compaction follows.
- Quota path: before the compaction step, a full window skips it (logged), so nothing is
  typed into a pane that would only error.
- If there is no fresh reading, the chat is not known to be full and the bounded waits
  (30/15/10 min) are what protect the switch.
- Not handled: after the reset the chat is not compacted on its own unless the context or
  a later window crosses its threshold.

## Stop before 100% (added 16/10 after "don't let it reach 100%")

`omnigent/server/plan_guard.py`, started with the server (`OMNIGENT_PLAN_GUARD=0` turns it
off). Every 20 s, only while some chat has a turn running, it refreshes the plan meters
(`collect_plan_limits`, cached 60 s; the meter files are otherwise only refreshed when a
page polls, or every 5 min by the meter keeper, far too slow) and checks each working
Claude or Codex chat with at least 40,000 tokens of context. At the interrupt ceiling it:

1. interrupts the turn (the same path as the Stop button, so the runner presses Esc /
   calls `turn/interrupt`),
2. asks the chat to write its handoff (what it was in the middle of and the exact next
   step, decided / done / open, files and ids, in the repo notes),
3. compacts it, and
4. leaves it **idle**: it does not resume the task, because that would spend the last of
   the window. The Hub says which chat was paused, at what reading, and when the window
   resets.

Ceilings (`~/.omnigent/hub/budget.json`, `handoff`): `interrupt_five_hour_pct` 95,
`interrupt_weekly_pct` 98, `interrupt` true. They sit above the turn-end ceilings (80 / 95),
which still run first when a turn ends in between. Once per chat per window; a chat the
turn-end flow already wrote up this window is not stopped again. If the runner does not
take the interrupt, nothing is asked and the next tick tries again.

Chosen defaults and why: the meter moves in whole points and is seen up to ~1 minute late;
Codex was moving about 2 points per minute at its fastest today, and the write-up plus the
compaction need roughly 2-3 points. 95% leaves room; 99% would not. A higher number can
be set, at the risk of the handoff itself hitting 100%.

Not interruptible: Antigravity (Gemini) chats, whose native harness has no interrupt wired
yet, so the guard skips them. Switching provider is a new lane, which receives only the
task text, not this chat's context; the handoff note in the repo is what carries over.

## What it does not do

- **Switches from the chat's own model picker (the PATCH route) are not covered.** That
  route lives in `routes_core.py`, which another lane has uncommitted edits in. Only
  switches made through the Hub (typed, voice, `set_model`) compact first.
- **Effort-only changes are not compacted.** I did not measure whether an effort change
  invalidates the cache.
- **Mid-turn, the 20 s poll and the ~60 s meter cache are the resolution.** A burst faster
  than ~3 points per minute can still cross 95% and reach 100% before the handoff ends.
- **Not exercised live.** Nothing here has been run against a real lane, because that
  interrupts your work and spends worker quota. The least certain piece is what status a
  chat reports right after an interrupt, so the sequence asks it anyway once a 45 s wait
  for idle has passed. The tests drive the full sequence with fakes, including
  `_run_auto_compaction` for the quota path.
- The write-up and compaction turns themselves read the whole context (cached), so they
  cost something; that is the price of the smaller rewrite.

## Tests

`tests/server/test_auto_compact.py` (22), `test_quota_handoff.py` (9),
`test_hub_handoff.py` (10), `test_hub_switch_handoff.py` (7), plus the existing Hub suites
(251 passed together).

## How to check it yourself

- Switch a chat with a large context from the Hub: "put <project> on Sonnet". Expected: an
  immediate reply saying it will write down where it is and compact first; the chat then
  runs a write-up turn, compacts, and the Hub says "Switched ..." with "Its context was
  written up and compacted first."
- Lower a ceiling to see the quota path without waiting: set `five_hour_pct` below the
  current reading in `budget.json`; at the next turn end of a chat with 40k+ tokens it is
  asked for the write-up, then compacted. Log line: `auto-compaction: asked session=...
  (quota)`.
