# Task

Work in this repository. It is a checkout of Omnigent: a server that runs
coding agents, plus a host daemon (`omni host`) that runs on the person's
machine and starts the agent processes ("runners") and the terminals they use.
On this machine the host daemon runs as the systemd user service
`omnigent-host.service`.

## What the person said, in their own words

These were dictated, so the wording is loose. Read for intent.

> the host died, i don't know what happened

> go on and fix this problem, after that, we're going to discuss about the team

## What you have

The host was restarted at 22:02 that evening at the person's request, and it
is running again. The person wants the problem fixed so it does not come back.

They captured logs and process listings from their machine around the failure.
These are sanitized copies, and they are in `./bug-report/` (start with
`bug-report/README.txt`). Times in them are local (UTC-3).

## Rules

- Work only inside this directory. Do not look at other checkouts, git remotes,
  the internet, or other agent sessions.
- Do not deploy, restart services, or touch `~/.omnigent`.
- No network calls.
- The virtualenv (`.venv`) is already set up and is shared with other work: do not
  install or sync packages. Run tools with `uv run --no-sync ...` or
  `.venv/bin/python -m ...`.
- Add or update tests for what you change.
- Verify your own work before you report it done.
- Do not commit.

Report when finished: what caused it, what you changed, and how you verified it.
