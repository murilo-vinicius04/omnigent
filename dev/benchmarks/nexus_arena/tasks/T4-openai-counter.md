# Task

Work in this repository. It is a checkout of Omnigent (a server and web UI that
runs coding agents on several providers) at the commit just before the change
below was made.

## What the person asked for, in their own words

These were dictated, so the wording is loose. Read for intent.

> bro, a lot of things worked, but now, i spent all of my gemini quota hahaha so,
> can you help plug another provider? i was thinking of using gpt api, i have
> the voice api key and the same project has free quota because i allowed them to
> use my data for training, so, can you check that? like how much of it can i
> use, i do not want to spend beyond the free quota

> this api key has literally all of the permissions, it's important that we know
> how much it costs, so we don't spend past it, we have gpt 5.6 luna and terra
> that may be more token efficient, but we need to measure that

> we need to know exactly how much tokens are being spend, because openai
> doesn't gives us this, so make your own counter, terra and luna have a 2.5M
> tokens that can be spend daily, as for sol, is just 250k tokens, we need to
> make sure every token is controlled in our own usage, just like we have the
> usage for claude and gemini

## What was found before this request

OpenAI answers 403 on its usage and cost endpoints for this project key, so
nothing tells us how much of the complimentary daily allowance is left. The
allowance resets daily at 00:00 UTC. Omnigent already records each session's token usage
(`omnigent/server/routes/_sessions/orchestration.py`): relay harnesses report
per-turn deltas, native harnesses report cumulative totals. The composer shows
each provider's plan usage from `/v1/plan-limits`
(`omnigent/server/routes/plan_limits.py`); Claude and Gemini already have rows
there. Some calls to OpenAI are made outside Omnigent and would need to be
entered by hand.

## Interface the graders bind to

Hidden tests use these names. Names, signatures and data shapes are fixed;
behaviour is yours to work out.

New module `omnigent.openai_token_budget`:

- `ledger_path() -> pathlib.Path` — where the count is kept (tests point it at
  a temp file).
- `UNLISTED: str` — pool id for OpenAI models that are in no free pool.
- `pool_for(model: str) -> str | None` — `"small"` (Luna/Terra, 2.5M/day),
  `"large"` (Sol, 250k/day), `UNLISTED`, or `None` for a model that is not
  OpenAI's.
- `record(model: str, usage: Mapping[str, object], *, source: str = "session", now: datetime | None = None, path: Path | None = None) -> int`
  — `usage` uses the same token field names as session usage; returns the
  tokens counted.
- `record_usage_delta(delta: Mapping[str, object], *, source: str = "session") -> int`
  — takes a session usage delta as the session-usage code builds it.
- `pool_usage(now: datetime | None = None, *, path: Path | None = None) -> list[dict]`
  — one row per pool: `{"id", "label", "daily_tokens", "tokens", "models"}`,
  where `models` maps model id to tokens; `daily_tokens` is `None` for `UNLISTED`.
- `read_day(day: str | None = None, *, path: Path | None = None) -> dict[str, dict]`
  — per model: `{"tokens": int, "by_source": {source: int}, ...}`.
- `next_reset(now: datetime | None = None) -> datetime`
- `_main(argv: list[str] | None = None) -> int` — command line entry,
  `python -m omnigent.openai_token_budget add <model> [--input N] [--cached N] [--output N]`,
  recorded with source `"manual"`.

And `omnigent.server.routes.plan_limits._openai_provider() -> dict` — the
`"openai"` provider row, in the same shape as the existing rows, with one
window per free pool whose `kind` is `"daily-<pool id>"`.
## Rules

- Stay inside this worktree. Do not deploy, restart services, or touch
  `~/.omnigent`.
- No network calls.
- Verify your own work before you report it done.
- Do not commit.

Report when finished: what you changed and how you verified it.
