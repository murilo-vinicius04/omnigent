# Task

Work in this repository. It is a checkout of Omnigent, a server that runs
coding agents in chat sessions. When the agent finishes a turn, Omnigent writes
a short summary of that turn for the person, which is shown under the reply and
read out loud.

## What the person reported, in their own words

This was dictated, so the wording is loose. Read for intent.

> the summarizer is broken, look at the last conversation, gemini said it was
> looking into it but it has already finished

("gemini" is the summary: the person calls it by the model that writes it.)

## What was handed over

`./bug-report/` is what the person gave you instead of access to their live
chats:

- `items.jsonl` holds the stored items of the conversation they mean.
- `timeline.txt` shows the same items as a table.
- `README.txt` is a short note on both.

The text of that chat has been replaced with neutral filler. Its structure,
order and timestamps are as they were stored.

Find out why this happens and fix it.

## Rules

- Work only inside this directory. Do not look at other checkouts of this
  project, git remotes, the internet, or other agent sessions.
- Do not deploy, restart services, or touch `~/.omnigent`.
- No network calls. Do not start Omnigent sessions or call any model.
- The virtualenv (`.venv`) is already set up and is shared with other work, so
  do not install or sync packages. Run tools with `uv run --no-sync ...` or
  `.venv/bin/python -m ...`.
- Add or update tests for what you change.
- Verify your own work before you report it done.
- Do not commit.

When you finish, report what the cause was, what you changed, and how you
verified it.
