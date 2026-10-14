# Hub: several agents per project, and a model router with a daily budget

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
- **Track it daily**: the week ÷ 7 per day, on each provider's own reset day,
  in Pro plan terms, so no provider runs out mid-week.
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
3. **Unused quota is lost at reset.** While a provider is within its budget,
   its top tier costs nothing extra in the end. Saving matters only when
   spending runs ahead of the budget.
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

## Phase 2: the daily budget and the model router

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

### 2.2 Budget governor: a daily budget per provider (plain code, not a model)

Weekly numbers are hard to follow, so the governor keeps a **daily** budget,
on each provider's own week.

- **Each provider's day starts at its own reset**, because they reset on
  different days:
  - Claude: Friday 08:59;
  - Codex: Tuesday 12:02;
  - Gemini: Tuesday 23:35 at the last reading (10-08); agy's meter keeps it current.

  "Day 3" means three days after that provider's reset, not a calendar day.
- **Pro plan basis.** Budgets are a share of each provider's own plan:
  - Claude Pro, from Friday's reset;
  - ChatGPT Plus;
  - your Google plan.

  The meters already report a percentage of the plan you are on, so the
  budget follows any change Anthropic makes without new numbers.
- **Claude Pro reference values (updated 10-14).**
  - **What changed:** on 09-22 Anthropic raised the 5-hour limits of Pro, Max
    and Team, without publishing figures.
  - **What we measured:** on your Max 5x plan, from our own token ledger and
    meter readings (09-17 to 09-27), 1% of the 5-hour window buys **2.1× more
    tokens** after 09-22 than before. The factor is the same on cache writes,
    output and cache reads. The weekly limit grew too, but there are too few
    weekly points after 09-22 to say by how much.
  - **For Pro:** applying the same factor to our Pro calibration (09-14 to
    09-16), 1% of the Pro 5-hour window is now about 97k cache-write or 10k
    output tokens (it was 46k and 4.9k). The benchmark costs quoted in Pro
    terms (a hard Sonnet task at 14.7% of a window, Opus at 27.2%) date from
    before the change, so they are now roughly halved.
  - **Next step:** we measure Pro directly from Friday's reset. The token
    ledger that does it had stopped on 09-27 and was restarted on 10-14.
- **Daily budget** = (100% − buffer) ÷ 7 of the week. At a 15% buffer that is
  about 12% of the week per day. How many tasks that buys on Claude Pro is
  measured from Friday. Before the 09-22 increase it was roughly 8 hard
  Sonnet tasks or 4–5 hard Opus tasks a day; now it should be more.
- **Unused budget carries forward, capped.** By the end of day d a provider
  may have used (d + 1) × the daily budget. A quiet day leaves room for the
  next, and a heavy day takes it from the next. The carry-over is capped at
  one extra day, so a quiet Monday cannot turn into a binge on Thursday.
- **Today's spend** = the weekly meter now minus its reading at the start of
  this provider-day. The readings are already saved every few minutes in
  `~/.omnigent/usage-history.jsonl`. Gemini is read only while an agy runs, so
  between readings its spend comes from agy's own ledger
  (`agy-quota-history.csv`).
- **Before a lane starts**, its cost is estimated: the average spend of that
  category on that tier in the outcome log, or the benchmark numbers until
  there is a log. If today's remaining budget cannot cover it:
  1. the task moves to the next provider with room, at its top tier;
  2. if none has room, it drops a tier;
  3. if even that does not fit, the Hub tells you and asks.
- **5-hour windows stay a separate guard:** a provider at 85% of its 5-hour
  window waits, or another provider takes the task.
- **Until Friday**, Claude still has Max's leftover (43% of the Max week
  used). That quota is lost at the reset, so the proposal is to let Claude
  spend it freely until Friday and start the Pro daily budget at the reset.
- **You see it:** each provider's ring in the Hub panel shows today's budget,
  what is spent, and the reset day. The sent chip names the model and why,
  e.g. "Opus 5.5 · Claude within today's budget" or "Luna · simple, Codex
  over today's budget".

### 2.3 Choosing the model for a task: the model judges the tier, rules pick the provider

1. **The Hub router** (Gemini Flash, in the call it already makes) reads the
   request: project, lane, category, and whether it fixes earlier work.
2. **The decision model** (2.5) judges how much model the task needs:
   economical, middle or top tier. This is the complexity judgement that code
   cannot make.
3. **Rules pick the provider and apply the budget.** Among the providers whose
   tier fits the category (table below), take the one with the most of
   today's budget left.
   - **While that budget has room:** the top tier anyway, since you want the
     best model whenever possible.
   - **When the budget is tight:** the tier the decision model judged
     sufficient. This is where the saving on simple tasks happens.
   - **When no provider can afford it:** the Hub tells you and asks.

   | Category | Fits |
   |---|---|
   | Code | Claude (Opus, Sonnet), Codex (Sol); Gemini once Argon arrives |
   | Plan, docs, research, explain | All three (Gemini Flash High counts here until Argon) |
   | Economical tier | Haiku, Luna, Flash |
   | A quick fact about the machine | The companion's read-only check, no agent |

4. **A redo always goes one tier up**: a fixed rule, not a judgement.
5. Saying "use Opus" or "use Codex" always overrides the choice.

It decides on its own, as you asked, and each choice starts in a fresh lane
(Phase 1), never mid-chat.

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
  - today's budget state, and quota before and after;
  - minutes;
  - check result, fix request, revert, escalation.

### 2.5 The decision model (Jev style) and how it learns

**What it is asked.** A Jev-style model reads a *state* and answers typed
questions with probabilities, never free text.
- **The state:**
  - the request, as the brief the lane will get;
  - project, lane and category;
  - the files the request concerns;
  - **the outcomes of the most similar past tasks**, e.g. "Luna, RAL, code,
    'add a ROS parameter': needed a redo, wrong QoS".
- **The questions:**
  - `choice` — which tier does this task need to be done right the first time:
    economical, middle or top?
  - `noul` — would an economical model need a redo?
  - `score` — difficulty from 1 to 5, kept for the logs.

**Which model.** The three candidates answer the same typed questions, so
they can be swapped behind one interface:

| Model | Where it runs | On our 62 past routing decisions, untuned (AUC) | Can be trained on our outcomes | Cost |
|---|---|---|---|---|
| **Jev 1.13** (TypeSafe, via OpenRouter) | Hosted | 0.66, the best | No public way. It learns through the state: past outcomes are part of what it reads | Input tokens only: ~$0.042 per million tokens on its model page, a few cents a day at our volume |
| **reflex** (Qwen3.5-4B, open, same API as Jev) | This machine, ~9 GB of GPU | 0.62 | Yes (LoRA) | Free, but the GPU is shared with Isaac Sim and cuRobo |
| **Laya multilingual** (322M, open, same kind of questions) | This machine; light on the GPU | 0.50 | Yes (documented notebook) | Free |

Proposal:
- **Start with Jev:** it is the best untuned, needs no GPU, and starts
  learning on day one through the outcomes in its state.
- **Log every decision and outcome.**
- **Long term:** fine-tune an open Jev-style model (reflex, or Laya if GPU time
  is short) on that log. Switch only when it beats Jev in shadow.

Jev's OpenRouter account has had no credit since 10-06 (decision 7).

**How it learns, in practice:**
1. **Right away, through the state.** Each new task is shown the outcomes of
   its nearest past tasks, so a model that caused a bug on a similar task
   last week weighs on the next decision immediately. No training needed.
2. **Weekly calibration.** The probability thresholds (how sure it must be
   that a cheaper tier suffices) are refitted on the logged outcomes. This is
   TypeSafe's own documented practice.
3. **The fixed rules stay on top:**
   - a redo goes one tier up;
   - a fresh mistake moves the next similar tasks up a tier at once.
4. **Later, fine-tuning** of the open model on the accumulated log, promoted
   only when it beats what is live on outcomes it never saw.

**One catch: it learns only from what it tried.** If every task runs on the
top tier, it never learns which ones a cheaper model would have handled. The
proposal: on at most 1 in 10 tasks it judged economical, outside the project's
core files, the economical tier is actually used even when the budget has
room.

**Prior art.** TypeSafe's own Jev Router (on OpenRouter since 09-25) does this
for API models: Jev "assesses the task, its difficulty, and the potential
benefit of a stronger model", and policy rules and restrictions are applied
on top. It runs the request itself on OpenRouter's paid models, so it cannot
drive our subscription CLIs. The design here is the same split, on our own
lanes.

## Who does what: plain code, LLMs, and the decision model

| Part | Done by | Why |
|---|---|---|
| Lanes, busy guard, opening chats, worktrees | Plain code (Python), with tests | Deterministic plumbing |
| Budget governor: meters, daily budgets, reset days, ceilings, the Friday plan change | Plain code | Money rules must be exact and testable, never guessed |
| Tier ladder, category table, buffer, reset schedule | A settings file you can edit | Your choices, not the model's |
| Provider choice, budget, and the redo-one-tier-up rule | Plain code | Exact rules where exactness matters |
| Outcome log and tracker | Plain code | Records facts |
| Understanding your message: project, lane, category, simple or not, is it a fix request | The Hub's existing Gemini Flash router, in the call it already makes | Language understanding; no extra call |
| Writing the brief a new lane starts from | The Hub's router, or the lane that is handing over | Summarising text |
| The work itself | The lanes: Claude, Codex or Gemini | — |
| Which tier each task needs (its complexity) | The Jev-style decision model of 2.5 | A judgement code cannot make |
| Building all of the above | Me, in this chat, with tests. Once lanes exist, parts can be handed to a Codex lane as a first real test | — |

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
   decision model: Jev (tier needed), past outcomes in its state <- outcomes.jsonl
        |   proposal
        v
 Budget governor   daily budgets per reset day, ceilings,    <- 3 meters + ledgers
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
             outcomes.jsonl --> Jev's state (now) --> calibration (weekly) --> open model fine-tune (later)
```

| Piece | Where | New or existing |
|---|---|---|
| Hub router fields: lane, category, simple | `omnigent/server/hub.py` | Existing call, new fields |
| Lanes | `omnigent/server/hub_lanes.py` | New (Phase 1) |
| Model router: decision model client, provider rules, mistake memory | `omnigent/server/model_router.py` | New |
| Budget governor | `omnigent/server/budget.py`, reading `routes/plan_limits.py` and the ledgers | New |
| Outcome tracker | `omnigent/server/outcomes.py`, fed by the Hub's watches | New |
| Settings: tiers, fit, buffer, plan schedule | `~/.omnigent/hub/routing.yaml` | New |
| Nightly training and evaluation | `tools/train_router.py` | New |

## Order and checkpoints

1. Phase 1 steps 1–3 and 6: lanes visible, and the busy guard. Show you.
2. Phase 1 steps 4–5: the Hub opens lanes, with worktrees. Scratch-server test,
   then live on RAL with your go.
3. Budget governor and daily budgets in the panel, read-only. You see each
   provider's budget for today before anything is decided by it.
4. Model router rules, mistake memory and outcome log in shadow for a week.
   They log what they would have chosen. Show you the log against what you
   used.
5. The router decides on its own, inside the governor's ceilings.
6. The decision model: Jev with past outcomes in its state, in shadow first;
   weekly calibration; later an open Jev-style model fine-tuned on the log,
   promoted only when it beats Jev.

Building it spends no agent quota, because tests use fakes. Live trials use one
real lane each, and only after you say go.

## Decisions for you

1. **A message for a busy lane:** hold it until the lane is idle (recommended),
   or paste it into the running task.
2. **A second writer in the same repository:** its own worktree (recommended),
   or the same folder.
3. **Buffer left at the end of each week:** 15% per provider (recommended), or
   another number.
4. **Unused daily budget:** carry it forward, capped at one extra day
   (recommended), or lose it at the end of each day.
5. **Until Friday's reset:** let Claude spend Max's leftover freely
   (recommended), or hold it to the Pro daily budget from today.
6. **Exploration:** let the router try one tier lower on at most 1 in 10
   simple tasks, so it learns (recommended), or never.
7. **Jev needs OpenRouter credit:** about $5 lasts months at a few cents a day.
   Top it up (recommended), or start on reflex locally, which uses ~9 GB of
   GPU.
8. **Gemini quota sampler:** the `agy-quota-sample` timer stopped on 09-27.
   Leave it off and read Gemini's quota only while an agy is already running
   (recommended), since Google has suspended accounts for background quota
   polling; or turn it back on.

Settled: Gemini 4 Argon is for Ultra only for now, so Google has no top tier.
Top-tier work goes to Claude or Codex, and Argon goes in automatically the day
`agy models` lists it.

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
- Jev on OpenRouter (Decisions API, typed questions, input-only pricing):
  https://openrouter.ai/docs/guides/community/jev · Jev Router, which picks a
  model and effort per request: https://openrouter.ai/docs/guides/routing/routers/jev-router
- Claude limits: Anthropic's 09-22 post raising 5-hour limits on Pro, Max and
  Team: https://x.com/claudeai/status/2102435538120691886 · dated timeline
  (third party): https://explainx.ai/blog/claude-usage-limits-2026-timeline-explained

### Our own data

- `/home/nexus/reports/Coding agent routing and orchestration.md`: the 10-08
  research report. A research agent gathered its paper links; the numbers
  above are quoted from it.
- `~/deepswe/FORENSICS.md`: the benchmark runs behind the Sonnet/Opus/Luna
  numbers.
