# Per-sub-agent harness and model, chosen per session

**Status:** Implemented on `feature/per-subagent-harness`
**Date:** 2026-09-05
**Touches:** CLI · server · runner · web

## 1. Motivation

A multi-agent bundle's team is fixed at authoring time. `examples/debby` fans
every question out to a Claude head and a GPT head; `examples/polly` pins
`grok-4.5` on its cursor head. Those choices were made when the bundle was
written — debby predates the Antigravity harness by eleven days — and the only
way to change one was to edit the bundle's YAML on disk, which retargets
**every** session of that agent at once.

So someone holding Claude and Gemini subscriptions and no OpenAI account could
not use half of Debby, and nothing on screen explained why. The existing
`harness_override` pins the **brain** — for a bundle, the piece that matters
least to the person configuring it.

This adds two per-session overrides, keyed by the head's declared name:

| Override | Replaces | Example |
|---|---|---|
| `sub_harness_override` | the child spec's `executor.config.harness` | `{"gpt": "antigravity-native"}` |
| `sub_model_override` | the child spec's `executor.model` | `{"gpt": "gemini-3.8-flash-low"}` |

## 2. Where the value lives

In `conversations.session_overrides` — the existing compact-JSON column that
already holds `harness_override`, `model_override`, `reasoning_effort` and
their siblings. Both new keys are **strings** there, holding the
`{"name": "value"}` object as JSON:

```json
{"harness_override":"pi","sub_harness_override":"{\"gpt\":\"antigravity-native\"}"}
```

A string rather than a nested object so the existing encode/decode and the
`String(512)` column need no change, and so an older runner round-trips the
value untouched. The wire shape matches the stored column exactly at every hop.

Three storage locations were rejected:

- **the bundle** — every session of an agent loads the same
  `agent.bundle_location`, so writing there retargets every session at once,
  which is the problem;
- **`session_state`** — policy territory, written by the runtime;
- **`labels`** — guardrails read those.

## 3. The path a pick takes

```
web config dialog / CLI --sub-harness NAME=HARNESS
   → POST /v1/sessions  {"sub_harness_override": {"gpt": "antigravity-native"}}
   → _validated_sub_harness_override()          # server/routes/_sessions/helpers.py
   → conversations.session_overrides            # stores/conversation_store/
   → every message forward + the session-init envelope
   → note_session_sub_agent_overrides()         # runner/app.py, per session
   → _execute_subagent_tool()                   # runner/tool_dispatch.py
   → POST /v1/sessions {"parent_session_id": …, "harness_override": "antigravity-native"}
```

The last step is the load-bearing one. The pick is applied when the **child
session is created**, as that child's own `harness_override` — not resolved
later while its turn runs. Everything downstream then agrees about what the
child runs: the pre-dispatch CLI probe (`missing_harness_cli`), the server's
own create-time validation, the child's later turns, its terminal launch and
its reconnects. An earlier version resolved the pick only at turn time, and
each of those read the harness the bundle declared instead.

### Validation

`_validated_sub_harness_override` rejects, at create, with `invalid_input`:

- a KEY the bound bundle does not declare as a sub-agent — a typo that
  silently ran the old team would surface much later as "why is this still
  answering on GPT";
- a VALUE that does not canonicalize into `OMNIGENT_HARNESSES`;
- an agent whose `executor.type` is not `omnigent`.

`_validated_sub_model_override` checks the KEY the same way and deliberately
**does not** validate the VALUE against a catalog: a model id only means
something next to the harness that will run it, that harness may itself be
overridden in the same request, and each harness resolves its own catalog at
spawn. A wrong id surfaces there, named.

### Precedence

For the harness, most specific first:

1. `sys_session_send`'s `harness` argument — a per-dispatch override, already
   gated by the sub-agent spec's own `executor.config.allowed_harnesses`
   allowlist, so it is a human's opt-in too;
2. **the session's pick** — chosen by a human in the config dialog;
3. what the child spec declares.

For the model: the dispatch's `model` argument, then the session's pick, then
the parent session's model (inherited as a side effect), then the harness's
own default. The session pick sits above the inherited parent model for the
same reason it sits above the spec's harness: it names one head.

Inside `_resolve_harness_config` the per-child pick also wins over the
session's brain `harness_override`, because the brain override is about the
orchestrator and reaches children only as a side effect.

A model pick is **dropped, with a log line**, when the head's harness has no
model-override plumbing. Persisting it would leave the child row claiming a
model the harness never reads. The explicit-dispatch path returns an error
there instead, because its caller is the orchestrator and can act on one; the
chooser here is a human who left the loop at session create.

## 4. Interfaces

### CLI

```bash
omni chat --agent debby --sub-harness gpt=antigravity-native
omni chat --agent debby --sub-harness gpt=antigravity-native --sub-harness claude=pi
```

Repeatable, `NAME=HARNESS`. Applied to the bundle copy the chat session runs
from (`_apply_sub_harness_overrides`), so `omni chat` needs no server.

There is deliberately no `--sub-model` yet: the CLI path rewrites the bundle
copy's YAML, and the model belongs beside the harness there rather than in a
second rewriting pass. The API and the web carry it.

### API

`POST /v1/sessions` takes `sub_harness_override` and `sub_model_override` as
objects. `GET /v1/sessions/{id}` echoes both back as the stored JSON strings —
the response's `harness` field is the BRAIN's, and nothing else in it names a
head, so without these a client cannot tell a session that chose from one that
did not.

### Web

The Configure dialog renders the team under the brain row, titled "Delegates
to", indented, one row per head:

- the row is labelled with the head's **declared name**, because that is the
  identifier the override is keyed by;
- its description says what the bundle **declared** for that head — without
  it, a head named `gpt` retargeted onto Antigravity has a name that lies;
- a harness that is not configured on the selected host carries the same
  amber badge the brain row uses;
- `Auto` is not offered per head: it routes the brain and means nothing for
  one named sub-agent;
- the model dropdown appears only when the host can name models for the
  **picked** harness (the catalog follows the dropdown above it, before Save),
  because an empty select would read as "no models exist" rather than "not
  selectable here";
- its `Default` row names the child spec's declared model when it pins one, so
  the choice is against something visible rather than a blank.

**The Antigravity catalog.** The host answered `model_options` for
`codex-native`, `pi-native`, `claude-native` and the claude-sdk family, and
failed every other harness with "model options are unsupported" — so the head
this feature exists for, a `gpt` head retargeted onto `antigravity-native`, got
a harness dropdown and no model dropdown. agy's own catalog lives behind the
connect-RPC port of a RUNNING session, which a pre-launch picker has none of.

`agy models` answers the same question from the CLI — one tab-separated
`id<TAB>display name` per line — so `omnigent/antigravity_native_catalog.py`
probes that, caches it in the shared `model_catalog_store` keyed by the agy
binary's identity, and the host serves it like any other harness's. A failed
listing answers `None`, never `[]`: persisting an empty catalog would teach
every later reader that agy offers no models, and only a binary change would
clear it.

Worth noting what the resulting list says about effort: agy's ids carry it
(`gemini-3.8-flash-high`, `-medium`, `-low`), so for that harness picking the
model IS picking the effort, and a separate per-head effort control would be a
second name for the same knob.

## 5. What is covered by tests

| Seam | Test |
|---|---|
| create → store → `GET` read-back, alias canonicalization, unknown name / harness rejected, **every message forward carries the picks**, and an un-picked session's body is unchanged | `tests/server/integration/test_sessions_sub_harness_override.py` |
| the session-init envelope carries them to a reconnecting runner; the per-session registry; an absent field means "unchanged", not "cleared"; a malformed blob is ignored rather than raised | `tests/runner/test_session_sub_agent_overrides.py` |
| the dispatch pins the pick on the child's create body; no pick sends nothing; the pre-dispatch CLI probe judges the PICKED harness | `tests/runner/test_subagent_dispatch_session_picks.py` |
| the `agy models` parse, store-then-probe, and the two failures that must not be cached as an empty catalog | `tests/test_antigravity_native_catalog.py` |

Two of those pin bugs that shipped in the first version of this feature: the
message forward dropped both keys (the value persisted and read back, and no
turn ever saw it), and `build_runner_session_init_payload` declared the
snapshot fields without ever populating them.

## 6. Upstream

Kept separate from the fork's other changes so each can be sent on its own.
`a56127f6` (`_build_resume_parts` rebuilding a repeatable option as
`str(tuple)`) and `69a67d15` (an unnamed sub-agent breaking the agents
catalog) are upstream bugs found while building this, and belong in their own
PRs regardless of whether this feature lands.
