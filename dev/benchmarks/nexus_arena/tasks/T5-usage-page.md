# Task

Work in this repository. It is a checkout of Omnigent (a server and web UI that
runs coding agents on several providers) at the commit just before the change
below was made.

## What the person asked for, in their own words

These were dictated over two days, so the wording is loose ("Brock" is Grok,
"Codex" is OpenAI). Read for intent.

> How much token usage, history, and usage data are we seeing for Brock, Codex,
> Gemini, and Claude from my window?

> can you build the usage ui ring for grok as well and add logs to the history
> for it like you did you the other providers?

> there's nothing about my plan's quota usage? like, there's no limit??

> but claude let's us see the usage, antigravity as well

> Just the Omnigent on usage. That's not what I want. What I want here in this
> ring is my clean quota usage. When you just type. Usage in the Grok build, you
> can see the usage of the week. So this is the fucking thing that I want, okay?
> Yes, I want the charts as well, I want Omnigent as well to measure, but here in
> the ring, I just need to see the usage of my replay plan, okay?

The next day, after moving from Claude Pro to Claude Max:

> and now that we are using max, will we lose the idea of how much quota we
> would be spending on pro? or can we predict it closely?

> it would be good to have a number in tokens, like, pro has X tokens available
> for the week and Y available for the 5 hour

> Do we have a precise... estimate of how much Max is more than Pro, you know?
> How much budget Max has more than Pro because this way we can use it, you
> know? And after that, we can compare at the end of the week and say, oh, okay,
> we would have been able to do everything that we did.

## What was found before this request

**Usage today.** The composer's tray shows each provider's plan usage from
`GET /v1/plan-limits` (`omnigent/server/routes/plan_limits.py`): rows for
Claude, Antigravity (Gemini's quota, read from a local agy) and OpenAI (our own
token counter). The web Usage page shows dollars only, from `GET /v1/usage`.

**The usage log.** `omnigent/usage_history.py` appends one JSON object per line
to `usage-history.jsonl` in the data dir: an `openai_call` line for every
OpenAI call the budget proxy counts, and a `plan_limits` line per provider
every few minutes when the tray polls (formats in its docstring). Codex
workers' OpenAI calls go through that proxy, so those lines are the only
timestamped record of OpenAI tokens so far. Nothing reads the log back. It is
rotated at 8 MB, keeping one previous file.

**Session turns** are stored only as running totals per session in the
database (`omnigent/server/routes/_sessions/orchestration.py`): relay harnesses
report per-turn deltas (`_accumulate_session_usage`), native harnesses report
cumulative totals (`_persist_native_cumulative_usage`). Nothing records when a
turn spent its tokens. Model ids arrive in whatever spelling the harness
reports: `claude-opus-5`, `databricks-gpt-5-6-luna`, `gpt-5.6-luna`,
`Gemini 3.8 Flash (High)`, `grok-4.6-build`. As in the OpenAI counter, a turn's
tokens are all of them: input, output, cache reads and cache writes.

**Grok Build** (the `grok` CLI, now a nexus worker) has no usage API for
tokens, but it writes one file per session at
`~/.grok/sessions/<workspace>/<session id>/usage.json`, rewritten as the
session goes on:

```json
{"sessionId": "0b6c…", "updatedAt": "2026-09-16T22:13:56Z",
 "session": {"totalTokens": 358629, "inputTokens": 353517, "outputTokens": 5112},
 "turns": [{"turnNumber": 1, "endedAt": "2026-09-16T22:13:55.793361213+00:00",
            "inputTokens": 353517, "outputTokens": 5112, "cachedReadTokens": 301184,
            "cacheCreationTokens": 0, "reasoningTokens": 1905, "totalTokens": 358629,
            "modelCalls": 9, "costUsdTicks": 972162000, "primaryModelId": "grok-4.6-build"}]}
```

`inputTokens` already includes `cachedReadTokens`, `totalTokens` is input plus
output, and a cost tick is 1e-10 USD.

**Grok plan usage.** What the CLI's `/usage` screen shows (share of the week's
plan used) comes from
`GET https://cli-chat-proxy.grok.com/v1/billing?format=credits` with headers
`Authorization: Bearer <key>` and `X-XAI-Token-Auth: xai-grok-cli`. The key is
in `~/.grok/auth.json`, shaped `{"<account>": {"key": "…", "expires_at":
"2026-10-01T00:00:00.000000000Z"}}`; an expired key is refused with 401. The
answer looks like:

```json
{"config": {"creditUsagePercent": 42.0,
            "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY",
                              "start": "2026-09-13T20:11:53+00:00", "end": "2026-09-20T20:11:53+00:00"},
            "productUsage": [{"product": "GrokChat", "usagePercent": 35.0},
                             {"product": "GrokBuild"}]}}
```

**Claude plans.** Claude Code's credential file (`CLAUDE_CREDENTIALS_PATH` in
`plan_limits.py`) holds, under `claudeAiOauth`, a `subscriptionType` (`"pro"`,
`"max"`) and a `rateLimitTier` (`"default_claude_max_5x"`,
`"default_claude_max_20x"`). Max 5x is sold as five Pro allowances and Max 20x
as twenty, for both the 5-hour and the weekly window. Measured on 2026-09-18,
in weighted tokens (input x1, output x5, cache read x0.1, cache write x1.25):
Pro holds about 5.5M per 5-hour window and about 40M per week. These are
estimates.

## Interface the graders bind to

Hidden tests use these names. Names, signatures and data shapes are fixed;
behaviour is yours to work out. Extra keys are fine.

New module `omnigent.usage_timeline` (reads the usage history back):

- `provider_for_model(model: str | None) -> str` — the vendor family:
  `"claude"`, `"gemini"`, `"openai"`, `"grok"`, or `"other"`.
- `build_token_usage(*, since: str | None = None, until: str | None = None, path: Path | None = None) -> dict`
  — `since`/`until` are inclusive UTC days (`"YYYY-MM-DD"`); `path` is the
  usage-history log (default: `usage_history.history_path()`). Returns
  `{"since", "until", "providers", "limits", "totals"}`:
  - `providers`: one row per vendor family with tokens in the window,
    `{"id", "label", "tokens", "days": [{"day", "tokens"}, ...], "models": [{"model", "tokens"}, ...]}`,
    days oldest first;
  - `limits`: plan usage over time from the tray's readings,
    `{"provider", "windows": [{"kind", "points": [{"at", "percent"}, ...]}]}`,
    one row per plan-limits provider id, points oldest first;
  - `totals`: `{"tokens"}` across all providers.
- `tokens_today(*, now: datetime | None = None, path: Path | None = None) -> dict[str, dict]`
  — `{family: {"tokens": int, ...}}` for the UTC day of `now`.

`GET /v1/usage/tokens?since=YYYY-MM-DD&until=YYYY-MM-DD`, served by the existing
`omnigent.server.routes.usage.create_usage_router`, answers the
`build_token_usage` report as JSON.

New module `omnigent.grok_usage`:

- `ingest(*, root: Path | None = None, path: Path | None = None, now: datetime | None = None) -> int`
  — scans Grok's session usage files under `root` (default
  `~/.grok/sessions`) and records each turn not recorded before into the usage
  history, so Grok shows up in the report; returns how many turns it recorded.
  `path` is the file where it keeps what it has already counted (tests pass a
  temp file).

New module `omnigent.pro_equivalent`:

- `plan_multiplier(rate_limit_tier: str | None, subscription_type: str | None) -> int | None`
  — how many Pro allowances the plan is, `None` when unknown.
- `pro_equivalent(windows: list[dict], multiplier: int) -> dict | None` — takes
  the Claude row's windows (`{"kind": "session" | "weekly", "percent", ...}`)
  and returns `{"multiplier", "windows": [{"kind", "used_pct", "weighted_tokens_used", "weighted_token_budget"}, ...]}`.

`omnigent.server.routes.plan_limits`:

- `GROK_AUTH_PATH: Path` — Grok's auth file, read on every poll (tests point it
  at a temp file).
- The `collect_plan_limits()` payload gains a `"grok"` row in the same shape as
  the others, with a `"weekly"` window.
- The `"claude"` row carries `"pro_equivalent"` (the dict above) when the
  credential's plan is known.
- Every row carries `"tokens_today"`: the tokens Omnigent recorded today (UTC)
  on that provider's models (the `"antigravity"` row counts Gemini).

The web side (charts on the Usage page, the Grok ring, the Pro readout) is part
of the request but is not graded by these tests.

## Rules

- Stay inside this worktree. Do not deploy, restart services, or touch
  `~/.omnigent`, `~/.grok` or `~/.claude`.
- No network calls.
- The virtualenv (`.venv`) is already set up and is shared with other work: do not
  install or sync packages. Run tools with `uv run --no-sync ...` or
  `.venv/bin/python -m ...`.
- Verify your own work before you report it done.
- Do not commit.

Report when finished: what you changed and how you verified it.
