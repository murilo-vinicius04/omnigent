# nexus arena — four open coding tasks, graded by hidden tests

A benchmark for comparing ways of doing the same work: one model alone, or the
`nexus` orchestrator (a reviewing brain plus a cheaper worker). It is built
from this repository's own commits, so no model can have seen the solutions.

## Why not just reuse a public benchmark

Public sets are in every training corpus, and our earlier attempt
(`f7da4a3dc`, one task, 20 tests) could not separate models: 13 of its 20
tests bound to names the prompt never disclosed, and 3 more encoded the
original author's private choices, so the real ceiling was 4/7 and every arm
scored 2-4. The lesson: **a task must be open, and only behaviour a competent
engineer would arrive at may be graded.**

## The four tasks

| task | gold commit | graded tests | shape |
|---|---|---|---|
| T1 | `722d842da` | 8 | wake the parent when a sub-agent repeats one tool call |
| T2 | `ad763b2db` | 12 | parse the agent's to-do block, show it, keep it after a restart |
| T3 | `02628e770` | 5 | keep Claude's plan limits on screen through Anthropic's 429 |
| T4 | `46a0c437a` | 14 | count OpenAI tokens ourselves against the free daily pools |

Each `tasks/T*.md` is the person's **own dictated request**, verbatim, plus the
facts known at the time and the names the hidden tests bind to. Behaviour is
never stated: working it out is the task.

## Fairness

Every graded test went through a blind audit: an agent that saw the task, the
starting tree and the tests — never the solution — labelled each assertion
FAIR (follows from the request or the code's own conventions) or PRIVATE (the
author's arbitrary choice). PRIVATE assertions were dropped or rewritten, and
every such edit is marked `[fairness]` in `graded/`. Examples: exact log
strings, an undisclosed default cooldown, a threshold the prompt never gives
(T1's tests now use a 200-call streak, so any threshold from 3 to 200 passes).

## Grading (`bin/grade.py <task> <tree>`)

- Copies the tree first: hidden tests are written at their real paths and
  would overwrite an arm's own test files.
- Runs **every graded test in its own process**: module-level state is named by
  the arm, not by us.
- **Import guard** (`bin/benchguard.py`, loaded as a pytest plugin): this repo
  is usually installed editable, so a module missing from the tree under test
  silently resolves to the developer's checkout — i.e. to the solution. The
  guard removes that finder, so `omnigent.*` resolves only inside the tree.
- A regression counts only if its test file existed at the start commit AND it
  fails again when rerun alone (the suite has load-sensitive timing tests).
- Lint runs on the arm's own changed files, before the hidden tests land.
- Arms are graded **exactly as delivered** — never fixed, tidied or reformatted.

## Running an arm

    export ARENA_REPO=/path/to/checkout ARENA_WORK=/tmp/nexus-arena
    bin/run_plain.sh T1 fable          # claude -p, one model alone
    bin/run_nexus.py T1 <host_id>      # nexus: opus brain reviews, gemini worker
    bin/grade.py T1 $ARENA_WORK/runs/T1-fable/tree --json out.json
    bin/measure2.py $ARENA_WORK/runs/T1-fable        # time, cost, tokens
    bin/measure_nexus.py $ARENA_WORK/runs/T1-gemini  # brain cost + gemini points

`mktree.sh` builds each arm's tree with `git archive` + a fresh `git init`: a
`git worktree` would share history and put the solution one `git show` away.

## Calibration

`bin/calibrate.sh <gold>` re-derives the fail-to-pass list. Current state, with
the guard and every fairness edit applied: **gold 8/8, 12/12, 5/5, 9/9; the
bare start commit 0 on all four; no regressions.** Re-run it after any edit to
`graded/` — a test that passes on the start tree is not measuring anything.
