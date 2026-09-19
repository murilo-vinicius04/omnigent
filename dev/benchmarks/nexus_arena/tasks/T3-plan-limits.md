# Task

Work in this repository. It is a checkout of Omnigent (a server and web UI that
runs coding agents) at the commit just before the change below was made.

## What the person asked for, in their own words

These were dictated, so the wording is loose. Read for intent.

> i'm not able to see claude's usage limits here in the ui, what's happening?

> now the usage is showing, i have no idea why sometimes it works and sometimes
> it doesn't

> but five minutes to update the ui is a lot, don't you think? and now it's
> working and appearing without any problems

> yeah, do this smaller fix, make sure it's not a silent fail and respect the
> cooldown, but keep the number as fresh as possible

## What was found before this request

The composer shows each provider's plan usage (`/v1/plan-limits`, built in
`omnigent/server/routes/plan_limits.py`). The Claude row reads Anthropic's
usage endpoint with the local OAuth token. Anthropic sometimes answers
**429 Too Many Requests**, with or without a `Retry-After` header (seconds, or
an HTTP date). When that happens the Claude row disappears, and nothing is
logged. The page asks for plan limits often, and several requests can arrive at
the same moment.

## Interface the graders bind to

Hidden tests call `plan_limits.collect_plan_limits()` and read the `"claude"`
provider row, whose existing fields you can see in the code. They replace the
HTTP client, the clock (`plan_limits.time.time` and `plan_limits.time.monotonic`)
and `plan_limits._read_claude_token`. They also expect:

- `plan_limits.CLAUDE_CACHE_PATH: pathlib.Path` — where the Claude row keeps
  anything it stores on disk, so tests can point it at a temp file.

The web side is not graded by these tests.
## Rules

- Stay inside this worktree. Do not deploy, restart services, or touch
  `~/.omnigent`.
- No network calls.
- Verify your own work before you report it done.
- Do not commit.

Report when finished: what you changed and how you verified it.
