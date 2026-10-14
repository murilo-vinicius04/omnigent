# Hub: several agents per project, and a model router with a weekly budget

Status: proposal, nothing built (2026-10-14). Each phase ends with something
you can open and try before the next one starts.

## What you asked for

- Run more than one agent in the same project. For example, Opus keeps working
  on the sim Franka in RAL while a second agent plans today's experiment and
  the joystick work, and the Hub knows which agent is which.
- Spread the work across Claude, Codex and Gemini, not only Claude.
- **The router decides on its own**, watching spending, with a weekly ceiling
  per provider so all three end the week with quota left over. On Friday 10-16
  Claude drops from Max to Pro at its weekly reset.
- **Use the best model whenever possible.** Cheaper models only save quota on
  simple tasks.
- **The router is a model** (Laya or Jev), fine-tuned to learn from mistakes:
  when a model's work caused a bug and needed another round, the next similar
  task goes one tier up.
- **Tiers by provider:**

  | Provider | Top | Middle | Economical |
  |---|---|---|---|
  | Claude | Opus 5.5 | Sonnet 5.5 | Haiku 5.5 |
  | OpenAI (Codex) | GPT-6.1 Sol | — | GPT-6 Luna |
  | Google (Gemini) | Gemini 4 Argon | — | Gemini Flash |

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
| Spending ledgers | `~/.omnigent/`: `claude-token-ledger.jsonl`, `openai-token-ledger.json`, `agy-quota-history.csv`, `usage-history.jsonl` | Tokens per call, and meter readings over time |
| Meters at 11:07 today | `plan-limits-*.json` | Claude Max: 43% of the week (resets Fri 10-16 08:59), 9% of the 5h window. Codex Plus: 0% of the week (resets Tue 10-20 12:02). Gemini: last read 10-08 (13% of the week), because it only reads while an agy runs |
| Models each CLI offers today | catalogs and `agy models` | Claude: Opus, Sonnet and Haiku 5.5 (and Fable 5.1). Codex: GPT-6.1 Sol and GPT-6 Luna (also GPT-6 Astra and GPT-6 Sol). agy: Gemini 3.8 Flash (low/medium/high), GPT-OSS 120B, and Claude Opus and Sonnet 5.5 billed to a separate Google pool. **Gemini 4 Argon is not in agy**: announced 09-30, limited access so far. Checked 10-14 11:20: agy 1.3.5 is the latest (`agy update` found nothing newer), the model list comes from Google's server per account, and `--model gemini-4-argon` is refused with the list above |
| Laya | `~/laya-test`, model `convaiinnovations/laya` | Open weights, runs here on the GPU in ~16–20 ms per decision. Untuned AUC on our 62 routing decisions (09-23): 0.50 |
| Jev | OpenRouter Decisions API, `typesafe/jev-1.13` | Hosted; we can call it but I found no way to train it. Untuned AUC 0.66. The account has had no credit since 10-06 |

## Rules the design follows (from the evidence)

1. **One task, one chat, one model**, chosen when the chat starts and kept.
   Each model has its own prompt cache, so switching models on a long
   transcript re-reads all of it. Claude Code's team says not to change models
   mid-session.
2. **One writer per folder.** A second agent that edits code in the same
   repository gets its own git worktree. A planning agent writes only new files.
3. **Unused quota is lost at reset.** While a provider is under its weekly pace,
   its top tier costs nothing extra in the end. Saving matters only when
   spending runs ahead of pace.
4. **Calling a task "simple" will often be wrong, so correct it cheaply.**
   Routers that read only the prompt score AUC 0.55–0.63 on repository tasks;
   ours scored 0.43–0.66 untuned. Every wrong call is caught (a failed check, a
   fix request, a revert), pushes the next similar task up a tier, and becomes
   training data for the router.
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

## Phase 2: the weekly budget and the model router

### 2.1 Tier ladder

| Provider (its own CLI) | Top | Middle | Economical |
|---|---|---|---|
| Claude (Claude Code) | Opus 5.5 | Sonnet 5.5 | Haiku 5.5 |
| OpenAI (Codex) | GPT-6.1 Sol | — | GPT-6 Luna |
| Google (agy) | Gemini 4 Argon, once agy lists it | — | Gemini 3.8 Flash |

- **Gemini until Argon arrives:** Google has no top tier here. Gemini 3.8 Flash
  High is the best Gemini agy offers, so top-tier work goes to Claude or
  Codex, and Flash takes Google's share of the simple tasks. The day
  `agy models` lists Argon, it becomes Google's top tier with no code change.
- **GPT-6 Astra:** the Codex catalog also lists it, as its frontier model. Sol
  stays at the top as you said; tell me if Astra belongs above it.
- **An extra Claude pool:** agy also offers Opus and Sonnet 5.5 billed to a
  separate Google pool ("Claude/GPT week"). It could add Claude-tier capacity
  after Friday. Untested.

### 2.2 Budget governor (plain code, not a model)

- **Reads** the three meters the usage rings show, at each decision, and the
  ledgers for what each lane spent. Gemini is read only while an agy is
  already running: Google has suspended accounts for background quota
  polling, so the Hub never adds any.
- **Weekly pace line** per provider: allowed = (100% − buffer) × the share of
  its week that has passed. Proposed buffer: 15%.
  - **Under pace:** top tier for every task.
  - **Ahead of pace:** simple tasks go to an economical tier. Other tasks keep
    a top tier on the provider furthest under its own pace.
  - **At the ceiling** (100% − buffer): no new lanes on that provider until its
    reset.
  - **5-hour window at 85% or more:** that provider waits, or another takes the
    task.
- **Plan changes.** Claude moves from Max to Pro at Friday's reset (10-16
  08:59). The meter's percentage already counts against the current plan.
  The router's cost estimates are kept in tokens and converted per plan. Our
  ledger ratios say the same task takes about 3.3–5.5 times more of the week
  on Pro. So after Friday Claude's share drops, and Codex and Gemini take
  more.
- **Today, as an example:**
  - Claude Max is at 43% with 73% of its week gone, against a pace line of 62%
    at a 15% buffer. That is 19 points under pace, so Opus is free to use until
    Friday: whatever is left disappears at the reset.
  - Codex is at 0% with its week ending Tuesday, so Sol is underused.
- **You see it:** each provider's pace line sits next to its ring in the Hub
  panel. The sent chip names the model and why, e.g. "Opus 5.5 · Claude under
  pace" or "Luna · simple, Codex ahead of pace".

### 2.3 Choosing the model for a task

1. The Hub's Gemini router, in the call it already makes, also names the
   task's category and whether it is simple. No extra call.
2. The providers whose top tier fits the category are the candidates. These
   defaults are learned over from the outcome log:

   | Category | Fits |
   |---|---|
   | Code | Claude (Opus), Codex (Sol); Gemini once Argon arrives |
   | Plan, docs, research, explain | All three (Gemini Flash High counts here until Argon) |
   | Small edits, renames, short scripts (simple) | Any economical tier: Haiku, Luna, Flash |
   | A quick fact about the machine | The companion's read-only check, no agent |

3. Among the candidates, take the top tier of the provider furthest under its
   pace. The best model goes where there is room, so the load spreads across
   the three.
4. A task marked simple drops to an economical tier only when the chosen
   provider is ahead of pace.
5. The mistake memory (2.4) and later the router model (2.5) can raise the
   tier. Saying "use Opus" or "use Codex" always overrides the choice.

It decides on its own, as you asked; the ceilings in 2.2 are the safety net.
Each choice starts in a fresh lane (Phase 1), never mid-chat.

### 2.4 Learning from mistakes (from the first day)

- **A mistake** is any of these:
  - the lane's check failed;
  - your next message to that lane asks to fix what it just did (the Hub router
    labels it);
  - its change was reverted within 7 days;
  - the task was escalated.
- **Fixing it.** The fix runs one tier above the one that made the mistake.
  At the top tier, it goes to another provider's top.
- **The next 5 tasks** of that project and category also start one tier
  higher. Two clean tasks in a row bring it back down one tier.
- **Every decision and its outcome** go to `~/.omnigent/hub/outcomes.jsonl`:
  - request, project, lane, category, and whether it was marked simple;
  - provider, model, effort and tier;
  - pace state and quota before and after;
  - minutes;
  - check result, fix request, revert, escalation.

### 2.5 The router model: Laya, fine-tuned on our outcomes

- **Why Laya:** its weights are open and run on this machine, so it can be
  trained on our own outcomes. Jev is a hosted API we can only call.
- **What it learns:** for a request, project, category and the lane's recent
  history, the chance that the economical or middle tier needs another round.
  The governor turns that into a tier:
  - **under pace:** top;
  - **ahead of pace:** the cheapest tier whose risk is below a threshold that
    rises with pace pressure.
- **How it takes over:**
  - From the first day of the outcome log, it runs in shadow next to the rules.
  - Each week it is retrained, and scored on outcomes it did not train on.
  - It replaces the rules only when it beats them there.

  Published coding routers trained on about 1,500 or more labelled tasks; at
  our pace that is a few months. Until then the rules and the mistake memory
  decide, and every one of their decisions becomes a label.
- **First step:** confirm the `laya` package can be fine-tuned. If not, use the
  same setup on reflex (Qwen3.5-4B, open, already tried here), or a small
  classifier on Laya's embeddings.

## Architecture

```
 you (typed or voice)
        |
        v
 Hub router (Gemini Flash, warm agy)     project, lane, category, simple?   [exists; gains fields]
        |
        v
 Model router  -------------------------------------------------------------- reads
   tier ladder + category fit (rules)                        <- routing.yaml
   mistake memory (tier bumps per project and category)      <- outcomes.jsonl
   Laya scorer (shadow, then live once it beats the rules)   <- trained weekly
        |   proposal
        v
 Budget governor   pace lines, ceilings, 5h windows,         <- 3 meters + ledgers
                   plan changes (Claude Pro from 10-16)
        |   provider, model, effort
        v
 Lane launcher     POST /v1/sessions: CLI, model, effort, worktree, brief
        |
        v
 Lane runs ... finishes
        |
        +--> report to the Hub (exists)
        +--> Outcome tracker: check, fix request, revert, minutes, quota spent
                    |
                    v
             outcomes.jsonl --> mistake memory (now) --> Laya training (weekly)
```

| Piece | Where | New or existing |
|---|---|---|
| Hub router fields: lane, category, simple | `omnigent/server/hub.py` | Existing call, new fields |
| Lanes | `omnigent/server/hub_lanes.py` | New (Phase 1) |
| Model router, mistake memory, Laya scorer | `omnigent/server/model_router.py` | New |
| Budget governor | `omnigent/server/budget.py`, reading `routes/plan_limits.py` and the ledgers | New |
| Outcome tracker | `omnigent/server/outcomes.py`, fed by the Hub's watches | New |
| Settings: tiers, fit, buffer, plan schedule | `~/.omnigent/hub/routing.yaml` | New |
| Weekly training and evaluation | `tools/train_router.py` | New |

## Order and checkpoints

1. Phase 1 steps 1–3 and 6: lanes visible, and the busy guard. Show you.
2. Phase 1 steps 4–5: the Hub opens lanes, with worktrees. Scratch-server test,
   then live on RAL with your go.
3. Budget governor and pace lines in the panel, read-only. You see each
   provider's pace before anything is decided by it.
4. Model router rules, mistake memory and outcome log in shadow for a week.
   They log what they would have chosen. Show you the log against what you
   used.
5. The router decides on its own, inside the governor's ceilings.
6. Laya: confirm it can be fine-tuned, score it in shadow, promote it when it
   beats the rules.

Building it spends no agent quota, because tests use fakes. Live trials use one
real lane each, and only after you say go.

## Decisions for you

1. **A message for a busy lane:** hold it until the lane is idle (recommended),
   or paste it into the running task.
2. **A second writer in the same repository:** its own worktree (recommended),
   or the same folder.
3. **Buffer left at the end of each week:** 15% per provider (recommended), or
   another number.
4. **Google's top tier until Argon reaches agy:** send top-tier work to Claude
   or Codex (recommended), or try agy's Opus and Sonnet 5.5 on the Google pool.

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
- Gemini 4 Argon announced, limited access:
  https://inc42.com/?p=574374 ·
  https://www.khaleejtimes.com/business/google-announces-gemini-4-flagship-ai-model-after-months-of-delays-2
- Laya: `convaiinnovations/laya` on Hugging Face; our untuned test in
  `~/laya-test`

### Our own data

- `/home/nexus/reports/Coding agent routing and orchestration.md`: the 10-08
  research report. A research agent gathered its paper links; the numbers
  above are quoted from it.
- `~/deepswe/FORENSICS.md`: the benchmark runs behind the Sonnet/Opus/Luna
  numbers.
