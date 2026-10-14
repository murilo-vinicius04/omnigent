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

  The meters already report a percentage of the plan you are on. Before
  Friday, Claude's Max reading is converted to Pro terms at the measured ratio
  (one Max point is about 4–5 Pro points). Our numbers on Pro:
  - a Pro week is about 10 Pro 5-hour windows;
  - a hard coding task cost 14.7% of a window on plain Sonnet and 27.2% on
    Opus, so about 1.5% and 2.7% of the week.
- **Daily budget** = (100% − buffer) ÷ 7 of the week. At a 15% buffer that is
  about 12% of the week per day. For Claude Pro that is roughly 8 hard Sonnet
  tasks or 4–5 hard Opus tasks a day.
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

3. Among the candidates, take the top tier of the provider with the most of
   today's budget left. The best model goes where there is room, so the load
   spreads across the three.
4. A task marked simple drops to an economical tier only when the chosen
   provider is over today's budget.
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
  - today's budget state, and quota before and after;
  - minutes;
  - check result, fix request, revert, escalation.

### 2.5 The router model and how it learns

**What it decides.** Not what the task is: the Hub router already says that.
It decides one thing: for this request and this tier, the chance that the work
needs another round (a mistake as defined in 2.4). The governor then chooses:
- **Inside today's budget:** the top tier.
- **Budget tight:** the cheapest tier whose chance of a mistake is below a bar
  that rises as today's budget runs out. Roughly, it minimises expected cost:
  the tier's price, plus the chance of a redo times the price of the redo.

**Which model.**

| Candidate | Size | Runs on | Labels it needs | Verdict |
|---|---|---|---|---|
| Multilingual sentence embeddings (e.g. `multilingual-e5-small`, 118M) plus logistic regression or nearest neighbours over past outcomes | ~0.5 GB | CPU, a few ms; retrains in seconds | Tens | **Start here** |
| Laya multilingual (mmBERT-base, 322M, Apache 2.0), fine-tuned with a yes/no head | ~650 MB | GPU to train; GPU or a slower CPU to run | Hundreds | Second stage, if it beats the first in shadow |
| Arch-Router (1.5B) | ~3 GB | GPU | — | Not needed: it matches requests to categories, which the Hub router already does |
| reflex (Qwen3.5-4B) | ~9 GB of GPU | GPU | — | Too heavy for this job |

Why embeddings first:
- With few labels, a simple classifier over embeddings matches fine-tuned
  small models on routing (CodeRouterBench). Nearest neighbours degrade least
  when the work changes.
- It runs on the CPU, so it never competes with Isaac Sim, cuRobo and the ZED
  for the GPU in RA-L experiments.
- It retrains in seconds every night.

Laya is the upgrade once there are a few hundred labelled outcomes. Its
authors say fine-tuning is where its accuracy comes from. Its base model is
near chance, which matches our untuned AUC of 0.50.

**How it learns, in practice:**
1. Every task leaves one row in `outcomes.jsonl`:
   - the request text;
   - project, category, simple or not;
   - provider and tier;
   - spend;
   - whether it needed another round.
2. Every night a script embeds the new requests and refits the classifier on
   all rows, one per tier.
3. It is scored on the last two weeks' rows it did not train on.
4. It runs in shadow until it predicts mistakes better than the plain rules of
   2.4. Only then does its choice count.
5. The mistake memory of 2.4 keeps working on top: a fresh mistake moves the
   next similar tasks up a tier at once, without waiting for the night's
   retraining.

**One catch: it learns only from what it tried.** If every task runs on the
top tier, it never learns which ones a cheaper model would have handled. So
it needs a little exploration. The proposal: on at most 1 in 10 tasks it marks
simple, outside the project's core files, it tries one tier lower. A mistake
there costs one redo and teaches it the most.

## Who does what: plain code, LLMs, and the small learned model

| Part | Done by | Why |
|---|---|---|
| Lanes, busy guard, opening chats, worktrees | Plain code (Python), with tests | Deterministic plumbing |
| Budget governor: meters, daily budgets, reset days, ceilings, the Friday plan change | Plain code | Money rules must be exact and testable, never guessed |
| Tier ladder, category table, buffer, reset schedule | A settings file you can edit | Your choices, not the model's |
| Mistake memory: tier bumps after a mistake | Plain code | A simple, predictable rule |
| Outcome log and tracker | Plain code | Records facts |
| Understanding your message: project, lane, category, simple or not, is it a fix request | The Hub's existing Gemini Flash router, in the call it already makes | Language understanding; no extra call |
| Writing the brief a new lane starts from | The Hub's router, or the lane that is handing over | Summarising text |
| The work itself | The lanes: Claude, Codex or Gemini | — |
| The chance that a tier needs a redo | The small local model of 2.5, trained on our outcomes | Learned from your own history |
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
   learned scorer (shadow, then live once it beats the rules) <- retrained nightly
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
             outcomes.jsonl --> mistake memory (now) --> scorer retraining (nightly)
```

| Piece | Where | New or existing |
|---|---|---|
| Hub router fields: lane, category, simple | `omnigent/server/hub.py` | Existing call, new fields |
| Lanes | `omnigent/server/hub_lanes.py` | New (Phase 1) |
| Model router, mistake memory, learned scorer | `omnigent/server/model_router.py` | New |
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
6. The learned model: embeddings + classifier from the first labels, in shadow;
   Laya fine-tuned once there are a few hundred; each promoted only when it
   beats what is live.

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

### Our own data

- `/home/nexus/reports/Coding agent routing and orchestration.md`: the 10-08
  research report. A research agent gathered its paper links; the numbers
  above are quoted from it.
- `~/deepswe/FORENSICS.md`: the benchmark runs behind the Sonnet/Opus/Luna
  numbers.
