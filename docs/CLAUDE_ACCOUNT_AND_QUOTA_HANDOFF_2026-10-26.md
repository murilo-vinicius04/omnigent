# Claude Pro Quota Analysis, Fallback Mechanics, and Account Switch Handoff

**Date:** 2026-10-26 (Closing session handoff)  
**Workspace:** `/home/nexus/wt/friendly-layer` (branch `feat/friendly-layer`)  
**Context / Scope:** Analysis of Claude Pro plan consumption, explanation of Hub quota locks and fallbacks, diagnosis of Gemini 3.8 Flash latency, and execution of Claude Code account authorization/switch.

---

## 1. What Was Done

### A. Claude Pro Plan Usage Analysis
- **Observed vs. Expected:** Compared live usage metrics against expectations following the Max 5x → Pro transition on 17/10.
- **Debunked Alarmist Projection:** Prior to transition, local extrapolations projected that Pro would burn at 379% of weekly quota (~1.8 days to exhaustion; see [`USAGE_PRO_PROJECTIONS_2026-10-15.md`](./USAGE_PRO_PROJECTIONS_2026-10-15.md)). In reality, at 34 hours into the 168-hour weekly cycle (20.25% elapsed time), cumulative weekly consumption was only **18.0%** (a pace of **~0.89x of linear time elapsed**).
- **5-Hour Window Sensitivity:** The 5-hour session window peaked at **57.0%** (safely below the 85% Hub guard and 95% plan guard), demonstrating that the 5h window is far more sensitive to burst agent tool calls than the 7-day quota.
- **Cache Efficiency:** Confirmed that prompt cache hit rates were sustained at > 98.6%, preventing the destructive cold cache-write penalties previously seen in September.

### B. Hub Quota Locks & Fallback Mechanisms
- **Daily Budget (`12.14%/day`):** Clarified that the daily allowance `(100% − 15% buffer) / 7` governs **only automatic new lane selection in the Hub** (`budget.pick()`). When exhausted, it marks the provider as `blocked: "today's budget is spent"`.
- **User Override:** If the user explicitly selects a provider or model (e.g. `"use Sonnet"` or via UI picker), **the user's choice overrides the daily quota lock** ([`omnigent/server/hub.py#L2816`](../omnigent/server/hub.py#L2816)). It executes normally and merely logs an advisory message.
- **Existing Sessions:** Conversations already open on a model continue executing on that model; the daily budget does not interrupt or kick active chats to another provider.
- **True Hard Locks:**
  1. *Anthropic API Level:* Direct HTTP 429 when the provider's 5h or 7d window hits 100%.
  2. *Hub Plan Guard ([`plan_guard.py`](../omnigent/server/plan_guard.py)):* Automatically monitors working Claude/Codex turns every 20s. If a session reaches **95% of the 5-hour window** or **98% of the weekly window**, the guard interrupts execution before total exhaustion, forces the model to write its handoff in `docs/RESUME.md`, triggers `/compact`, and pauses the session until the window reset.
  3. *Model Switch Compaction:* Switching models in chats with > 40k tokens enforces handoff + compaction before loading the new model.

### C. Gemini 3.8 Flash (High) Latency Diagnosis
- User reported Gemini 3.8 being very slow and appearing not to respond.
- **Root Cause 1 (Reasoning Effort):** Model was set to `Gemini 3.8 Flash (High)`. The "High" effort setting generates extensive internal reasoning tokens before emitting tool calls.
- **Root Cause 2 (Sequential Tool Iterations):** The previous analysis task executed 16 sequential tool calls (`run_command`, `view_file`), each requiring a round-trip reasoning block and tool response. To the user, this appeared as a 1–2 minute freeze while the agent was actively working.
- **Root Cause 3 (Background Errors in Logs):** Server log [`server-*.log`](/home/nexus/.omnigent/logs/server/) showed `hub tier judge failed: All connection attempts failed` (attempting to reach local reflex judge on port 8008), and `cli.log` showed repeated trajectory polling failures (`trajectory ... not found in any store`).

### D. Claude Code Account Switch Execution
- Generated OAuth PKCE authorization URL via `claude auth login` in a background task.
- User completed browser authorization and provided the callback authorization string:
  `beTUA753md0A6a3gvPuxarF3RsHkLAMxiZIcjaFr1HQjELkp#CCQClIMMuUB4i1pt-_OTqtU2-_Qr5EUfaAissHE1_Jk`
- Delivered input to the running task, successfully completing login:
  - Account: `carvalho.matheush@gmail.com`
  - Org: `d842da24-5207-48e9-a2c5-8b068195d336`
  - Subscription: `pro`
  - Quota at switch (18/10): 5h session 7.0%, weekly 53.0%.
- Backup of previous credentials preserved at `~/.claude/.credentials.json.bak`.

---

## 2. Where Things Are

| Component / Artifact | Path / Location |
| :--- | :--- |
| Primary project handoff | [`/home/nexus/wt/friendly-layer/docs/RESUME.md`](./RESUME.md) |
| Detailed analysis handoff (this file) | [`/home/nexus/wt/friendly-layer/docs/CLAUDE_ACCOUNT_AND_QUOTA_HANDOFF_2026-10-26.md`](./CLAUDE_ACCOUNT_AND_QUOTA_HANDOFF_2026-10-26.md) |
| Active Claude credentials | `~/.claude/.credentials.json` |
| Credential backup | `~/.claude/.credentials.json.bak` |
| Hub budget settings | `~/.omnigent/hub/budget.json` |
| Claude Pro transition state | `~/.omnigent/hub/claude-pro-transition.json` |
| Usage history log | `~/.omnigent/usage-history.jsonl` |
| Real-time usage utility | `~/.local/bin/claude-usage` |
| Hub budgets endpoint | `GET http://127.0.0.1:6767/v1/hub/budgets` |
| Plan limits endpoint | `GET http://127.0.0.1:6767/v1/plan-limits` |

---

## 3. What Is Left / Open Items for Next Agent

1. **Current Credential Verification:**
   - As of 2026-10-26, `claude auth status` reports the active account is `murilo.mv4321@usp.br` (restored/used by subsequent tasks).
   - Current live usage on this account: **5h session: 82.0%** (resets 22:00 BRT), **weekly: 36.0%** (resets Fri 30 Oct 09:00 BRT).
   - If switching to `carvalho.matheush@gmail.com` or another account is requested again, use `~/.claude/.credentials.json.bak` or run the `claude auth login` flow.
2. **Account Switching Quota Baseline Artifact:**
   - In `omnigent/server/budget.py`, `weekly_readings()` parses provider history without isolating account email/org ID. Switching accounts mid-week can cause the Hub to subtract the new account's weekly percentage from the previous account's day-start reading (e.g. 53% − 28% = false 25% spent today).
   - If multi-account switching is formalized, `budget.py` should associate usage readings with `orgId`/account identity or reset the day-start baseline when credentials change.
3. **Workspace Concurrency Warning:**
   - `/home/nexus/wt/friendly-layer` has ~130 dirty files from parallel lanes (`feat/friendly-layer`). **Never run `git checkout .`, `git reset`, or blanket stage commands.** Commit strictly by explicit file name.
