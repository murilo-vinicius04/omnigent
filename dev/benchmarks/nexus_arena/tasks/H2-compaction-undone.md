# Task

Work in this repository. It is a checkout of Omnigent: a server that runs
coding agents for the person, among them Claude Code sessions they chat with
from Omnigent's web UI. The UI shows how full each chat's context window is
(the context ring, as a percentage of the model's window), and the person
compacts long chats with `/compact`.

## What the person said, in their own words

These were dictated, so the wording is loose. Read for intent. The second one
came six days after the first.

> bro, i compacted, but it's already at 46%, wtf

> context was not compacted in my other session, even with /compact being triggered

## What you have

The person copied what they could see while this was happening: context sizes
request by request, and what Omnigent had stored for those chats. These are
sanitized copies, and they are in `./bug-report/` (start with
`bug-report/NOTES.txt`). Times in them are local (UTC-3).

The person wants the problem fixed so it does not come back.

## Rules

- Work only inside this directory. Do not look at other checkouts, git remotes,
  the internet, or other agent sessions.
- Do not deploy, restart services, or touch `~/.omnigent` or `~/.claude`.
- No network calls.
- The virtualenv (`.venv`) is already set up and is shared with other work: do not
  install or sync packages. Run tools with `uv run --no-sync ...` or
  `.venv/bin/python -m ...`.
- Add or update tests for what you change.
- Verify your own work before you report it done.
- Do not commit.

Report when finished: what caused it, what you changed, and how you verified it.
