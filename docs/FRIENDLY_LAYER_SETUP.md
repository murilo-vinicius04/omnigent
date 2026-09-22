# Friendly layer: from a fresh clone to the setup we run

This branch (`feat/friendly-layer`) adds a spoken, hands-free layer on top of
Omnigent — spoken summaries, narration, dictation, live voice — plus `nexus`,
an orchestrator where a reviewing brain delegates the building to a cheaper
worker, and a benchmark that measures whether that is worth it.

## 1. Clone and bootstrap

```bash
git clone -b feat/friendly-layer https://github.com/murilo-vinicius04/omnigent.git
cd omnigent
deploy/friendly-layer/bootstrap.sh --services   # omit --services to only prepare
```

Needs `uv`, `node`/`npm`, Python 3.12+, and Linux with systemd for the
services. The script installs dependencies, **builds the web bundle** (the
server serves `omnigent/server/static/web-ui` from disk, so it must be rebuilt
after any `web/src` change) and installs the two user services
(`deploy/systemd/install.sh`, which renders the unit templates for your
checkout). The clone itself is never edited, so `git pull` stays clean: the
host service exports `OMNIGENT_CHECKOUT`, which is how nexus finds its worker
configs on any machine. Re-run it after a pull.

Open http://127.0.0.1:6767.

## 2. Workers (only the ones you want)

| worker | what it needs |
|---|---|
| `claude` | Claude Code CLI, logged in (`claude login`) |
| `gemini` | the Antigravity CLI, logged in. nexus must dispatch it with `bypassPermissions`, or it waits forever for an approval nobody sees |
| `codex` / OpenAI | an API key in `$OMNIGENT_DATA_DIR/openai-key` (default `~/.omnigent/openai-key`) |
| `grok`, `hermes` | their own CLIs |

`nexus` itself is `examples/nexus`; its workers are `examples/nexus/agents/*`.
The server registers it via `--agent`, which `bootstrap.sh` wires up.

**Spending:** every worker burns a paid or free quota. `/v1/plan-limits` and the
Usage page show what is left per provider; the OpenAI daily pools are counted by
`omnigent/openai_token_budget.py`, since OpenAI exposes no reading of them.

## 3. The voice layer

- **Spoken summary** — after each turn a small model rewrites the answer the way
  a person would say it; `omnigent/server/spoken_summary.py`.
- **Narration and dictation** — in the composer. The engine picker chooses
  between GPT Live and Gemini Live.
- **Live voice** needs a Gemini API key (AI Studio) in
  `$OMNIGENT_DATA_DIR/gemini-key`. Preview models go silent sometimes with the
  socket open and no error; that is Google-side and is why the client has a
  stall watchdog instead of a silent auto-reconnect.

## 4. The benchmark

`dev/benchmarks/nexus_arena` — four tasks taken from this branch's own commits,
graded by hidden tests. It answers "is a reviewing brain plus a cheap worker as
good as the big model alone, and what does each cost". See its README; it needs
this branch, because the tasks are its own history.

## 5. Notes that save a day

- **The web bundle is not rebuilt by tests or by a commit.** If a change does
  not show up in the browser, check the served bundle's hash first.
- **Restarting the server interrupts live sessions** and re-caches every open
  conversation, which costs plan quota. Compact long sessions before a restart.
- **Agent bundles are uploaded into the database.** Editing `examples/nexus`
  after the fact does not change an already-uploaded bundle; dispatch by
  `config_path` to pick up an edit immediately.
