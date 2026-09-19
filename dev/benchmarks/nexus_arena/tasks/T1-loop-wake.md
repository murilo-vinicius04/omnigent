# Task

Work in this repository. It is a checkout of Omnigent (a server that runs
coding agents and lets one agent delegate to sub-agents) at the commit just
before the change below was made.

## What the person asked for, in their own words

These were dictated, so the wording is loose. Read for intent.

> okay, i just killed it, now what's the problem? why is it looping?

> you know what, the orchestrator is a busy man, time is money, it doesn't make
> sense for it to keep monitoring the intern, the worker, if the worker has some
> major doubt, it should be able to ask quickly the experienced ceo, you know
> what i'm saying??

## What was found before this request

An orchestrator agent (the parent conversation) delegated a coding task to a
worker sub-agent (a child conversation). The worker got stuck calling the same
tool with the same arguments over and over, for a long time, until the person
killed it by hand. The parent only wakes up when a child finishes, so it never
noticed. Nobody wants the parent polling the child.

Every event a session publishes to its live stream passes through
`omnigent.runtime.inflight_text.record_publish(conversation_id, event)`. A tool
call made by a sub-agent shows up there in one of two shapes:

```python
{"type": "external_conversation_item",
 "data": {"item_type": "function_call",
          "item_data": {"name": "Read", "arguments": "{\"path\":\"a.py\"}", "call_id": "c1"}}}

{"type": "response.output_item.done",
 "item": {"type": "function_call", "name": "Read",
          "arguments": "{\"path\":\"a.py\"}", "call_id": "c1"}}
```

`call_id` may be absent. An event is sometimes published more than once; a
re-published event carries the same `call_id` as the original, and events
without a `call_id` are never re-published. Tool arguments can be very large
(whole file contents).

Healthy workers do repeat a call a few times in a row (retrying a flaky read,
polling a build). The stuck worker repeated the same call hundreds of times.

## Interface the graders bind to

Hidden tests use these names. Names and signatures are fixed; behaviour is
yours to work out.

- `omnigent.runtime.inflight_text.set_publish_observer(observer: Callable[[str, dict], None] | None) -> None`
  — registers one callable that `record_publish` calls with
  `(conversation_id, event)`; `None` unregisters it.
- `omnigent.runtime.subagent_loop_notifier.SubagentLoopNotifier(conversation_store, wake_dispatch, loop)`
  (positional or keyword, in that order)
  - `conversation_store.get_conversation(conversation_id)` returns an object
    with `.id`, `.parent_conversation_id` (may be `None`) and `.title`.
  - `wake_dispatch(parent_id: str, child_id: str, notice: str) -> Awaitable[bool]`
    delivers a text notice to the parent conversation and wakes it.
  - `loop` is the asyncio event loop to schedule work on.
  - `.observe(conversation_id: str, event: dict) -> None` is called with every
    published event; it must return quickly and never raise.
  - `.close() -> None` stops it.

Wire it into the running server as well.
## Rules

- Stay inside this worktree. Do not deploy, restart services, or touch
  `~/.omnigent`.
- No network calls.
- Verify your own work before you report it done.
- Do not commit.

Report when finished: what you changed and how you verified it.
