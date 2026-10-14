# Hub: several agents per project, and automatic model choice

Status: proposal, nothing built (2026-10-14). Each phase ends with something
you can open and try before the next one starts.

## What you asked for

- Run more than one agent in the same project. For example, Opus keeps working
  on the sim Franka in RAL while a second agent plans today's experiment and
  the joystick work, and the Hub knows which agent is which.
- Have the Hub pick the agent for each task, spreading the work across
  Claude, Codex and Gemini, not only Claude.

## What exists today (checked in the code)

| Piece | Where | State |
|---|---|---|
| A project holds many chats | `projects` table, `project_id` on each chat | Works; the Hub reads up to 50 per project |
| Which chat the Hub sends to | `hub.py` `_chat_ids`, sorted by `updated_at` | Only the most recently active one |
| A send to a busy Claude Code chat | `docs/QUEUE_STEER_DESIGN.md` (claude-native row) | Pasted into the live turn, which steers it off its task |
| The Hub starting a chat | `new_project` decision | It cannot: it posts a link and you start the first task |
| Creating a chat by API | `POST /v1/sessions` (`schemas.py`) | Takes `project_id`, `workspace`, `git` (create or bind a worktree), `model_override`, `reasoning_effort`, `harness_override`, `labels` |
| Changing a chat's model from the Hub | `hub_models.py`, `set_model` | Works (10-10) |
| Plan meters | `routes/plan_limits.py` | Claude 5h/7d, Codex Plus 5h/week, and Gemini from agy's own usage screen (readable while an agy runs; the last reading is kept). The same numbers as the usage rings |
| The three vendors' CLIs | `hub_models.py` `PROVIDERS` | Omnigent already runs Claude Code, Codex and Antigravity (agy, Gemini) chats |
| Upstream Smart Routing | `smart_routing.py`, `routing_backend.py` | Picks the harness and model when a chat is created, using an LLM judge that rates each request fast/balanced/powerful |
| Reports back to the Hub | watches per chat id | Work, and survive restarts |

## Rules the design follows (from the evidence)

1. **One task, one chat, one model**, chosen when the chat starts and kept.
   Each model has its own prompt cache, so switching models on a long
   transcript re-reads all of it. Claude Code's team says not to change models
   mid-session.
2. **One writer per folder.** A second agent that edits code in the same
   repository gets its own git worktree. A planning agent writes only new files.
3. **Choose by task category and remaining quota, not by a guess at
   difficulty.** Routers that read only the prompt score AUC 0.55–0.63 on
   repository tasks. LLMs used as routers score below a simple lookup table.
   Our own routers scored 0.43–0.66. That rules out upstream's fast/balanced/
   powerful judge; its plumbing can still be reused.
4. **Escalate on evidence, not prediction.** Opus comes in when a check shows
   the cheaper model failed, and the Hub asks you first.
5. **Start new agents from a short brief**: the task plus the project's handoff
   file, never the busy chat's transcript.

## Phase 1: lanes (several agents per project)

What you would see: under RAL in the Projects panel, "RAL · sim (Opus 5.5,
working)" and "RAL · joystick (Sonnet 5.5, idle)". Hub chips name the lane.

1. **A lane is a chat in the project** with the label `omnigent.hub.lane=<topic>`.
   Existing chats become their project's first lane, named from their title.
2. **The router sees lanes.** Each project line lists its lanes with topic,
   model, status and last request. `send` gains a `"lane"` field, and a new
   decision `{"new_lane": project, "topic", "message"}` opens one.
3. **Busy guard.** The Hub never pastes into a working chat by default:
   - Same topic: the Hub holds the message, says "RAL · sim is busy; I'll send
     it when it finishes", and sends it when the chat goes idle. Saying "tell it
     now" still steers the busy chat.
   - New topic: the Hub opens a new lane.
4. **The Hub opens chats itself** with `POST /v1/sessions`: `project_id`, the
   project's folder, Claude Code by default, the model and effort from Phase 2,
   the lane label, and a first message made of the brief plus the request.
   First check: does a native Claude Code chat start from the API alone,
   without the page? Test on a scratch server.
5. **Isolation.** A lane that will write code in a git repository where another
   lane is writing is created with `git: {branch_name: "hub/<topic>"}`, so it
   gets its own worktree. Planning lanes stay in the main folder and are told
   which paths the busy lane is using.
6. **Panel.** Lanes appear under each project, with New lane, rename and close.
   Before a lane is archived, it writes its handoff note.
7. **Tests**, all with fakes and no agent quota:
   - router decisions with two lanes, one busy and one idle;
   - the busy guard;
   - the create request the Hub sends;
   - the panel.
   Then a scratch-server run, then live on RAL once you say go.

## Phase 2: choosing the agent for each lane (Claude, Codex or Gemini)

Each lane runs one whole task on one vendor's own CLI:
- Claude Code: Sonnet 5.5 or Opus 5.5;
- Codex: GPT-6 Luna or Sol;
- Antigravity (agy): Gemini 3.8.

The Hub spreads the work across the three quota pools lane by lane. A single
task is not split between vendors. In our runs, a Claude brain directing a
cheaper worker shipped real bugs in 8 of 34 runs, against 0 of 27 for one
agent doing the whole task.

1. **Category table**, editable in `~/.omnigent/hub/routing.yaml`. The Hub's
   Gemini router already decides once per message; it would also name a
   category from this list in that same decision, so no extra call. This
   follows Arch-Router: matching a request to categories you wrote is the part
   routers do well (93%).

   | Category | First choice | Next, if that pool is low | What we know |
   |---|---|---|---|
   | Plan, write docs, research, explain | Gemini 3.8 (agy) | Sonnet 5.5 high | Text work on a separate pool. No quality data yet; the shadow week measures it |
   | Code the lane can test (the project has tests, or a check is written first) | Codex GPT-6 Luna high | Sonnet 5.5, then Gemini | As a worker under a Claude brain: 12/17 solved, 2 runs with a real bug, about 0.3% of the Plus week per run. On its own it is untested (report test 1) |
   | Code with nothing to test it, or the project's core | Sonnet 5.5 high | Codex Luna high | Plain Sonnet: 0 runs with a real bug in 17 |
   | Code after a failed check, or when you ask for the best | Opus 5.5 high, in a fresh lane with the brief | — | Asks you first |
   | A quick fact about the machine | The companion's read-only check | — | No agent at all |

2. **A check before code goes to Codex or Gemini.** When a code task has no
   tests, a short Sonnet lane first writes acceptance tests from your request.
   It reads only the request, not any code. Then the Codex or Gemini lane
   builds, and the Hub runs the check on the result. Claude's part is the
   check, not the code: in our runs, writing acceptance tests first turned two
   misses into solves (kombu-sac at 9.5% Pro, langchain at 20.2%). The report
   found the strong model most useful as the author of the check.
3. **Quota rule across the three pools.** When a lane starts, the Hub reads the
   three meters the rings already show. It takes the first choice unless that
   pool is at or above 80% of its 5-hour window or 85% of its week; then it
   moves to the next choice. When all are low, it tells you and asks. Prior
   art:
   - GPT-5's fallback to a smaller model at usage limits;
   - LiteLLM `budget_fallbacks`;
   - PILOT, a budgeted routing policy.

   Gemini runs only through agy, and its meter is read only while an agy is
   already running. Google has suspended accounts for background quota
   polling, so the Hub never adds any.
4. **It tells you what it picked.** The sent chip shows, for example, "Codex ·
   GPT-6 Luna high · code" and a one-line reason. Saying "use Claude" or "use
   Gemini" overrides it.
5. **Shadow week first.** For a week the Hub only logs what it would have
   chosen next to what you actually used. Nothing switches.
6. **Outcome log** per task:
   - category;
   - vendor, model and effort;
   - quota used in each pool;
   - minutes;
   - check result;
   - whether the change was kept.

   After a few weeks, this shows which vendor does which category best on your
   work. CodeRouterBench found that a plain lookup or nearest-neighbour router
   over such outcomes matches a fine-tuned one.
7. **Escalation.** When a lane finishes and its check fails, the Hub proposes
   the next step: another vendor, or Opus in a fresh lane started from the
   brief. It waits for your yes.

## Later (not planned now)

- A learned router, once about 1,500 outcomes are logged.
- Best-of-2 across quota pools, with the winner picked by differential tests.

## Order and checkpoints

1. Phase 1 steps 1–3 and 6: lanes visible, and the busy guard. Show you.
2. Phase 1 steps 4–5: the Hub opens lanes, with worktrees. Scratch-server test,
   then live on RAL with your go.
3. Phase 2 steps 1–4 in shadow mode for a week. Show you the log.
4. Turn the chooser on.

Building it spends no agent quota, because tests use fakes. Live trials use one
real lane each, and only after you say go.

## Decisions for you

1. **A message for a busy lane:** hold it until the lane is idle (recommended),
   or paste it into the running task.
2. **A second writer in the same repository:** its own worktree (recommended),
   or the same folder.
3. **When the Hub picks the agent for a new lane, may it start it on its own?**
   Recommended: yes for Codex, Gemini and Sonnet; ask you first only before
   Opus, the most expensive.
4. **The default split:**
   - Gemini for planning, docs and research;
   - Codex for code that can be tested;
   - Sonnet for the rest;
   - Opus only on escalation.

   Change any line if you see the split differently.

## References

### Several agents per project

- Claude Code agent view, read 10-14:
  - each background session is its own conversation, drawing on the same quota;
  - a session moves into `.claude/worktrees/<id>` before it edits;
  - rows are grouped as needs input, working and completed;
  - each session can run its own model.

  https://code.claude.com/docs/en/agent-view
- claude-squad (smtg-ai/claude-squad, AGPL-3.0): one tmux session and one git
  worktree per agent, with a terminal UI to switch between them. Seen through
  search summaries only. https://github.com/smtg-ai/claude-squad
- Vibe Kanban (BloopAI/vibe-kanban, Apache-2.0): each kanban card is a worktree
  plus an agent; it supports 10+ CLIs. Recent repository activity suggests it
  is being wound down. https://github.com/BloopAI/vibe-kanban
- Anthropic, effective harnesses for long-running agents: one feature per
  session, ending with a commit and a progress note.
  https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Cognition, "Don't build multi-agents", and Cursor, "Scaling agents": keep
  writes single-threaded; Cursor dropped its integrator role.
  https://cognition.com/blog/dont-build-multi-agents ·
  https://cursor.com/blog/scaling-agents
- Kim et al.: every multi-agent layout tested scored at or below a single agent
  on SWE-bench Verified. https://arxiv.org/html/2512.08296v3

### Model routing

- Arch-Router: routes to categories the user writes; the template for Phase 2
  step 1. Paper https://arxiv.org/abs/2506.16655 · code in katanemo/archgw ·
  model katanemo/Arch-Router-1.5B
- RouteLLM (lm-sys/RouteLLM): strong/weak routers; big savings on chat
  traffic. https://lmsys.org/blog/2024-07-01-routellm/
- SWE-Router: prompt-only AUC 0.55–0.63, and 0.78 after the cheap agent's first
  two turns. https://arxiv.org/html/2607.00053v1
- CodeRouterBench: LLM routers score below a lookup table; a verifier plus a
  memory of past outcomes beats always using the strong model.
  https://arxiv.org/html/2606.22902v1
- TRACE-Router: route once per task and keep that model.
  https://arxiv.org/html/2607.22465v2
- TwinRouterBench: https://arxiv.org/html/2605.18859v1
- The Routing Plateau: https://arxiv.org/html/2606.07587
- PILOT, routing under a budget: https://arxiv.org/html/2508.21141v1
- LiteLLM budget fallbacks: https://docs.litellm.ai/docs/proxy/budget_fallbacks
- LiteLLM auto-router benchmark:
  https://docs.litellm.ai/blog/auto-router-capability-benchmark
- FrugalGPT https://arxiv.org/abs/2305.05176 · AutoMix https://arxiv.org/abs/2310.12963
- Claude Code model config (`opusplan`):
  https://code.claude.com/docs/en/model-config
- Claude Code prompt caching: https://code.claude.com/docs/en/prompt-caching
- Omnigent's own Smart Routing: `omnigent/server/smart_routing.py`,
  `omnigent/server/routing_backend.py`

### Our own data

- `/home/nexus/reports/Coding agent routing and orchestration.md`: the 10-08
  research report. A research agent gathered its paper links; the numbers
  above are quoted from it.
- `~/deepswe/FORENSICS.md`: the benchmark runs behind the Sonnet/Opus/Luna
  numbers.
