# Task

Work in this repository. It is a checkout of Omnigent, a server that runs
coding agents in chats. Agents write files into their workspace, videos
included, and the person opens those files in a file viewer in the web UI.

## What the person said, in their own words

This was dictated, so the wording is loose. Read for intent.

> take a look at why i can't see the video from the other conversation, it's
> displaying this message: Unable to play this video here. Download it to watch
> it.

## What they handed over

`./bug-report/` holds what the viewer showed, the requests the browser made
while opening the video, and the server's log lines for that chat. Ids, file
names and folder names in it were anonymized.

Find out why the video doesn't play and fix it.

## Rules

- Work only inside this directory. Do not look at other checkouts, git
  remotes, the internet, or other agent sessions.
- Do not deploy, restart services, or touch `~/.omnigent`.
- No network calls.
- The virtualenv (`.venv`) is already set up and is shared with other work: do not
  install or sync packages. Run tools with `uv run --no-sync ...` or
  `.venv/bin/python -m ...`.
- Add or update tests for what you change.
- Verify your own work before you report it done.
- Do not commit.

Report when finished: what was wrong, what you changed, and how you verified it.
