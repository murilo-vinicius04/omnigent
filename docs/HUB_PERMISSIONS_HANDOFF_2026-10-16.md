# Hub agent permissions — handoff (updated 2026-10-17)

## Latest: Argon quota, stopped lanes, stale model display

**User explicitly demanded STOP twice. Keep the migrated lanes idle; do not replay
their tasks, resume agents, or spend Argon quota to test.** Argon must require an
explicit named-model request, never automatic selection or inheritance from a sibling.
Checkout `/home/nexus/wt/friendly-layer`, branch `feat/friendly-layer`; changes are
uncommitted and mixed with other lanes' edits. Preserve those edits.

- Affected lanes: **Teleop com Meta Quest** `4fe622ccfeb54d87af64cbbbb8ece5b8`
  and **Envio do nvblox para lab** `75456092097d4c73b43ac4cd60ac54dc`.
  Last reported live state: migrated to Claude Code / Sonnet 5.5, idle, history kept;
  stale Argon display repaired in live data. Recheck read-only if needed. No model
  tasks were intentionally restarted. Backups: `/tmp/hub-argon-switch-before.json`
  and `/tmp/hub-stale-argon-display-before.json` (contain conversation data).
- Installed `agy --version`: **1.4.0**; Argon is in its catalog. No confirmed CLI
  update was performed. User reported **37%** of a separate weekly Argon quota spent.
  Automatic selection incorrectly treated Flash's `gemini-weekly`/`gemini-5h` meters
  as capacity for `TIER_MODELS["gemini"]["top"] == "Argon"`.
- Implemented explicit-only policy in `omnigent/server/budget.py` (`offered_tiers`
  removes Gemini top) and `omnigent/server/hub.py` (fallback cannot inherit Argon;
  resolves Flash or refuses). Explicit `use="Argon"` remains available;
  `use="Gemini"` defaults to Flash. Regressions in `tests/server/test_budget.py`
  and `tests/server/test_hub_lanes.py`, notably
  `test_argon_requires_a_named_model_even_beside_an_argon_lane` and
  `test_automatic_top_tier_never_uses_flash_quota_for_argon`.
- **Policy validation/deployment still open:** Ruff reported `ARG001` for unused
  `agy_models` in `budget.py` (around line 458). Focused pytest selection
  `-k 'argon or provider_with_the_most or without_the_decision'` across those two
  files selected six tests, printed three passes then timed out. Investigate missing
  budget mocks/real `fresh_budgets` calls; do not claim full test success. These files
  also contain concurrent accounting changes. Backend reload for this policy has
  not been verified.
- Stale UI cause: `switch_conversation_agent` retained `session_overrides.reported_model`
  from the old runner. `_sessions/orchestration.py` uses that report as `llm_model`;
  `hub_models.run_from_snapshot` then renders Argon plus pending Sonnet override.
  Implemented clearing `reported_model` on **every** agent rebind (same family too)
  in `omnigent/stores/conversation_store/sqlalchemy_store.py`; updated protocol docs
  in that package's `__init__.py`. No frontend change needed for this cause.
- Store regression tests seed stale reports and verify returned/persisted values
  clear while requested model settings retain existing semantics. **2 passed**:
  `.venv/bin/pytest -q -p no:rerunfailures --timeout=25
  tests/stores/test_conversation_store.py -k switch_conversation_agent`.
  Ruff/format passed for the store changes.
- **Still open for UI fix:** extended
  `tests/server/integration/test_sessions_endpoints.py::test_get_session_agent_name_is_spec_name_after_switch`
  to cover Argon/same-family stale reports, native Claude target, Hub model projection,
  and external model-change event. First parametrization timed out at 30 seconds;
  isolate the hang before claiming integration success. Verify/reload the permanent
  backend fix without starting these lanes. Last live display repair was reported
  successful; permanent code deployment is not confirmed.

Next agent: finish the two validation gaps above, fix lint, verify deployment, and
read-only check both lanes show Claude/Sonnet with no pending switch and remain idle.
The older sections below describe completed bypass/TUI work and historical runtime IDs.

## Read first after compaction

**Earlier bypass/TUI requests completed; newer work remains above.** This lane handles
agent bypass and Antigravity delivery, not the sound/chime feature itself (another lane).
All changes remain uncommitted in `/home/nexus/wt/friendly-layer`; preserve concurrent edits.
Latest repair: Hub lane `be446a7bbe404c2c812ed50fd5a7861b` is running and mirroring again.
Current native ID **c87e9690-fae9-4082-a7cb-36f65d8f8b9e**; old `93ce...` is obsolete.
Bridge path `~/.omnigent/antigravity-native/68386cb10412cc5573b336f040430293`;
last native PID **3538874**, tmux socket `/tmp/omnigent-terminal-k6r2u_bi/tmux.sock`,
RPC port **40631** (runtime values: rediscover before using).
Latest test command: `.venv/bin/pytest -q -p no:rerunfailures --timeout=25
tests/test_antigravity_native_bridge.py tests/inner/test_antigravity_native_executor.py`
→ **144 passed**. The stale-ID repair below was one-time data/runtime recovery;
automatic handling of a future resume-ID drift was not implemented or claimed tested.

## Follow-up: Antigravity paste delivery (2026-10-16)

- Same sound/chime lane failed a later status request: `did not render the pasted
  message ... pane_tail: lines ... settings.` Reproduced with its live TUI resized
  briefly to **12×6**: the head and 24-character tail needles scroll away; footer
  truncates to `esc to cance`. `capture-pane -J` does not help (agy hard-wraps).
- Fixed `antigravity_native_bridge.py`: recognize word wrapping, a bounded suffix
  in a clipped composer (requires its footer), and truncated footer markers below
  the composer. Capture mid-turn state before pasting; submit queued steers only once.
- Added real-layout regressions in `tests/test_antigravity_native_bridge.py`.
  **144 bridge/executor tests passed**, Ruff/format/diff checks clean.
- Submitted the existing verified draft once, without re-pasting. Composer cleared;
  TUI displayed `Press up to edit queued messages`. Same Omnigent conversation retained.
  Refreshed only failed harness child PID 3538839 (SIGTERM); native agy stays alive.
  `retry_session` returned `native_terminal_ready`; next delivery spawns fixed code.
- Also repaired a stale reader/resume binding: this TUI's owned RPC port **40631**
  reported active native trajectory **c87e9690-fae9-4082-a7cb-36f65d8f8b9e**, while
  bridge/server still named **93ce6624-0bbe-4a76-80db-8f3a9328ab47**. Verified ownership
  against the terminal PID and original sound/chime request (494 native steps).
  Adopted the live trajectory in place and updated only this metadata row using an
  expected-old-ID transaction. Backup `/tmp/agy-paste-reader-binding-before.json`.
  **Latest live check: status running, error cleared, tool progress mirroring again.**
  Use the NEW native ID for subsequent checks/resumes; old ID below is historical.
- Captures `/tmp/agy-paste-{failure-pane,narrow-plain,narrow-joined,narrow-history}.txt`
  are local diagnostics (may contain account text; do not publish raw captures).

**Historical status (before Argon follow-ups):** bypass/TUI changes implemented and deployed locally.
Working tree `/home/nexus/wt/friendly-layer`, branch `feat/friendly-layer`. Changes are
**uncommitted**. Other lanes have unrelated edits/new docs, including `docs/RESUME.md` and
`omnigent/claude_pro_transition.py`; preserve them.

## Decisions and behavior

- User explicitly wants bypass available in active Claude chats and enabled by default
  for new Hub Claude/Antigravity lanes. Codex defaults were not changed.
- `omnigent/server/hub.py`, `store_backend.open_chat`: Claude receives
  `terminal_launch_args=["--permission-mode", "bypassPermissions"]`; Antigravity
  (provider `gemini`) receives `["--dangerously-skip-permissions"]`.
- These are native agent permission controls. Omnigent policy gates and OS restrictions
  still apply. An authorization written in chat does not itself change launch settings.

## Active Claude switch

- UI: **Hub project → lane Open → Chat → composer gear → Permissions →
  Bypass permissions → Save**. Permissions remains selectable if current mode is unknown.
- `omnigent/claude_native_bridge.py`: reads bypass footer, cycles Shift+Tab, accepts
  only the named bypass consent dialog's explicit acceptance choice. If unavailable,
  runner resumes **the same native UUID** using saved transcript and enabling flags.
- Resume requires idle status and a saved native transcript; fails clearly otherwise.
  Preserves hooks/settings, current model and tracked effort. Confirms the live footer
  before reporting success. `omnigent/inner/terminal.py:TerminalInstance.respawn`
  retains tmux server, original executable/sandbox launcher, environment and cwd.
- Other files: `omnigent/runner/app.py`, server routes `_sessions/{common,helpers}.py`,
  `sessions/routes_core.py`, `_session_create_validation.py`; web
  `src/lib/claudePermissionMode.ts`, `src/pages/ChatPage.tsx`.
- Explicit permission PATCH persists launch args for subsequent restarts; removes the
  standalone dangerous flag when switching back to normal. Initial footer observation
  alone does not pin a session previously using settings defaults.

## Deployment and Antigravity repair

- Frontend source initially was correct but served bundle was old. Rebuilt with Vite to
  `web/.bypass-build`, copied assets to `omnigent/server/static/web-ui`, replaced index
  last, retained old hashed chunks, removed staging directory. Verified over HTTP:
  `/assets/index-B4QrAAjd.js`, `/assets/chatStore-BYh0abDw.js` included live bypass menu.
  Build may change later; inspect actual served index, not just source files.
- Server: user service `omnigent-server.service`, cwd this checkout, port **6767**.
  Reloaded after both default changes; `/health` returned `{"status":"ok"}`.
  Service control needs `XDG_RUNTIME_DIR=/run/user/1000` and
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`.
- **Som de notificação discreto**: Omnigent ID `be446a7bbe404c2c812ed50fd5a7861b`;
  Antigravity native UUID `93ce6624-0bbe-4a76-80db-8f3a9328ab47`.
  Had NULL launch args and approval for `git -C /home/nexus/wt/hub-terminal worktree list`.
  Patched args via API, sent `stop_session`, reloaded backend, POSTed terminal ensure
  (`terminal=antigravity`, `session_key=main`, `ensure_native_terminal=true`), then sent
  continuation of its original sound/chime task. Verified actual agy argv had bypass
  **and the same UUID**; pending approvals = 0. Do not restart/replay again gratuitously.
- Bridge: `~/.omnigent/antigravity-native/68386cb10412cc5573b336f040430293`.
  Repair snapshots `/tmp/antigravity-lane-bypass-{before,resumed}.json`.
  Local API auth exists in `~/.omnigent/auth_tokens.json`; read token in code, never print it.

## Verification and caveats

- Claude: 179 frontend tests, 22 bridge mode tests, 17 runner/resume tests,
  16 API permission tests, 54 terminal tests passed. Real tmux respawn preserved env/cwd/server.
  TypeScript + Ruff passed. Global Pyrefly has 37 unrelated existing errors/dependency gaps.
- Antigravity: 38 focused tests passed (lane creation, CLI launch flags, runner flag
  forwarding, existing-conversation resume). Ruff/format/diff checks passed.
- Broader Antigravity run: 78 passed, one unrelated repo-discovery failure because
  `/tmp/.git` exists and fixture expects no parent repo. `/home/nexus/.git` also exists.
- Relevant tests: `tests/test_claude_permission_resume.py`, `test_claude_native_bridge.py`,
  `runner/test_app_sessions_native_{events_options,session_change_heal,terminals_runtime}.py`,
  `server/test_hub_lanes.py`, `server/integration/test_sessions_endpoints.py`,
  `test_antigravity_native_launch.py`, web `claudePermissionMode.test.ts` and
  `ChatPage.composer.test.tsx`. Use `.venv/bin/pytest -q -p no:rerunfailures --timeout=30 ...`.
- Sandboxed shell lacks host networking/systemd access. Use authorized escalated
  `exec_command` or Omnigent `sys_os_shell`; latter starts in `/home/nexus`, so explicitly
  `cd /home/nexus/wt/friendly-layer`. Browser renderer was unavailable; live JS checked via HTTP.
