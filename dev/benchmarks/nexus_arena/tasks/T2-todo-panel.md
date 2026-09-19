# Task

Work in this repository. It is a checkout of Omnigent (a server and web UI that
runs coding agents) at the commit just before the change below was made.

## What the person asked for, in their own words

These were dictated, so the wording is loose. Read for intent.

> it would be great if i had this to do as a simple artifact formatted here in
> the ui for each conversation to have a more abstract vision of it

> as i see, claude already gives the to do, gemini/summarizer just need to know
> how to handle it

> just the stardard done and pending, i think it's best in the right rail tab and
> the spoken summary doesn't need to mention it everytime, it's more something i
> can visualize you know

## What "the to-do" looks like today

At the end of its turns, the agent in these conversations writes a block like
this one, copied from a real turn:

```markdown
## To-do
### In progress
- [ ] To-do panel: parser + right-rail tab (worker running)

### Waiting on you
- [ ] Backup: push `feat/friendly-layer` (91 local commits) to `fork`?
- [ ] Same "never ask the human" rule in the Antigravity worker config?

### Later
- [ ] Change things without a new conversation (reload orchestrator config, restart runner in place)
- [ ] Enable linger so services start at boot

### Done
- [x] Usage limits stay visible
```

The block's exact headings vary from turn to turn ("## To-do", "## To-do,
updated", ...), and it is usually followed by more of the answer. The server
keeps session state in memory and is restarted often; the person reopens old
conversations after a restart. The spoken
summary is `omnigent/server/spoken_summary.py`.

## Interface the graders bind to

Hidden tests use these names. Names and signatures are fixed; behaviour is
yours to work out.

- `omnigent.server.todo_extract.extract_todos(text: str)` returns an object with
  `.found: bool` and `.todos: list[dict]`; each to-do is
  `{"content": str, "status": "pending" | "completed"}`.
- `omnigent.server.todo_extract.strip_todo_block(text: str) -> str`
- `omnigent.server.routes._sessions.helpers._update_session_todos_from_text(session_id: str, text: str) -> bool`
- `omnigent.server.routes._sessions.orchestration._build_session_response(...)`
  (existing; signature unchanged) — builds what a client gets when it opens a
  conversation.
- `omnigent.server.routes._sessions.helpers._rebuild_session_todos_from_history(session_id: str, items: list[ConversationItem] | None = None, conversation_store: ConversationStore | None = None) -> list[dict]`

The web side (the right-rail tab) is part of the request but is not graded by
these tests.
## Rules

- Stay inside this worktree. Do not deploy, restart services, or touch
  `~/.omnigent`.
- No network calls.
- Verify your own work before you report it done.
- Do not commit.

Report when finished: what you changed and how you verified it.
