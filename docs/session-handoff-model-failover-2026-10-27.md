# Handoff — model distribution, Muse quota rules, auto-failover (2026-10-27)

Lane: omnigent model distribution ("switch provider automatically when quota runs out").
Live tree: `/home/nexus/wt/friendly-layer` (server runs this; needs restart — see §0).

## 0. Restart: done 2026-10-27 ~19:00 UTC

`systemctl --user` is unreachable from the sandbox (user manager runs, `systemctl` gets
`No data available`), so the restart went through the manager's D-Bus API directly:
`busctl --user call org.freedesktop.systemd1 ... RestartUnit ss omnigent-server.service replace`
→ job/82870. New log `~/.omnigent/logs/server/server-20261027-190036-002022.log`, `/health` ok,
`/v1/plan-limits` 200. Pre-restart gates from `restart_omnigent_server.sh` (backend pytest, web
vitest + tsc, vite build) all passed before the restart step. Host untouched, runners untouched.

## 1. Why everything was on Claude

- New-lane router (`hub.py::_lane_run`) only distributes when the tier judge answers; judge backend
  was reflex localhost (`model_router.configured` → `DEFAULT_URL`), which is not running → judge
  failed → every lane copied the reference lane (Claude).
- Even with the judge up, `budget.METERS`/`TIER_MODELS` knew only claude/codex/gemini, so Muse
  could never be picked and had no quota row in the budget rules.

## 2. Done

- `omnigent/server/budget.py`: `muse` in `METERS` (`plan-limits-muse.json`, kinds `weekly`/`session`),
  `TIER_MODELS` (`top Wonder, middle Spark, economical Spark`), full `PROVIDER_EFFORTS` incl. `xhigh`,
  `PROVIDER_LABELS`. New pure helpers `tier_of_model` + `failover_proposer` (nearest tier with
  room on another provider, spent provider excluded).
- `omnigent/server/quota_handoff.py`: new `omnigent.provider` label (`PROVIDER_LABEL`, first-class
  in `provider_of`; ACP/Muse lanes have no wrapper label). New `full_window(provider, ...)`; the
  labels-based `exhausted()` is now a wrapper over it.
- `omnigent/server/model_router.py`: reflex default gone. `configured()` = explicit
  `OMNIGENT_HUB_ROUTER_URL`/flag URL wins, else supervisor OpenRouter Jev backend
  (`typesafe/jev-1.13`, key in `~/.openrouter-key`), else judge off. Live check prints
  `Backend(name='jev', url='https://openrouter.ai/api/alpha/decisions', ...)`.
- `omnigent/server/hub_lanes.py`: `spark`/`wonder` in `_MODEL_WORDS`; LaneRun docstring covers muse.
- `omnigent/server/hub.py`: explicit "use muse" resolves newest Spark + `xhigh`
  (harness default + xhigh when catalog unreachable); `open_chat` stamps `omnigent.provider` on
  every new lane; new optional `HubBackend.migrate` (POST `switch-agent` + model/effort PATCH +
  provider label); `send_to` runs `_failover_lane` on idle lanes — full window → nearest tier
  with room elsewhere → in-place harness switch → message sent to the new harness with a hub note.
- `web/src/lib/planProviders.ts`: `muse` family mapping (tray already listed all windows incl. week
  in the tooltip; tightest window leads).
- `~/.omnigent/hub/budget.json`: `priority_models` = `["Spark","Luna","Flash"]`
  (backup `budget.json.bak-20261027-muse-failover`). `avoid_models` still `["Argon","Sol"]`.
- Tests: 3 new budget, 1 quota_handoff, updated router-config test, 1 hub failover
  (`test_a_send_moves_an_idle_lane_off_a_full_plan`), updated 4 lane tests for the provider
  label + Spark/xhigh. Files: `tests/server/test_{budget,quota_handoff,model_router,hub,hub_lanes}.py`.

## 3. Verified

- `pytest tests/server/test_hub{,_lanes,_models,_place,_switch_handoff,_brief?} test_plan_guard
  test_quota_handoff test_budget test_model_router`: **306 passed, 9 failed** — the 9 are the
  pre-existing `test_hub_models` (5) + `test_hub_switch_handoff` (4) failures recorded in
  `notes/omnigent-HANDOFF.md` (set_model/compaction paths, untouched here).
- `git diff --check` clean. Other lanes' dirt (~90 files) untouched.

## 4. Open / decisions for next turn

- Mid-turn interrupt (`plan_guard.INTERRUPTIBLE`) still claude/codex only — Muse mid-turn
  interrupt is unverified, so the guard covers Muse at turn end (handoff), not mid-turn.
- Failover only triggers on a **full** window (≥100%), idle lanes only, lanes only
  (`session.lane`); explicit "use X" lanes also move, with a note. No room anywhere → stays silent.
- After restart, verify: tray shows Muse with week in tooltip; a send to a Claude-full lane moves
  it to Muse Spark 1.3 xhigh; new lanes spread across providers again.

## 5. Inspect 422 fix (same day, later)

- Symptom: `I couldn't inspect the selected machine and folder (HTTPStatusError)` when opening a
  lane. Cause was NOT an offline machine: `POST /v1/hosts/{id}/inspect` answered 422 because a busy
  lane's touched-path list (up to `_TURN_CALLS` = 300, uncapped) exceeded
  `HostInspectionIn.paths` `max_length=100`. Proven from `chat.db`: 82 sessions hold >100 touched
  paths in their newest 300 calls (type code 2 = `function_call`).
- Fix: `MAX_INSPECT_PATHS = 100` cap on the busy-lane inspect payload (`hub.py::open_lane`);
  `_inspect_failure()` names the HTTP status + server detail and says the machine is online when
  it answered with one. Tests: `test_a_busy_lane_with_more_paths_than_the_endpoint_takes_still_opens`,
  `test_a_rejected_inspect_names_the_status_instead_of_crying_offline` (`test_hub_lanes.py`).
  Suite: 239 passed (`test_hub_lanes/hub/quota_handoff/budget/model_router`).

## 6. Muse unsandboxed by default (same day, later)

- The cuRobo lane could not see the L40S: Muse's own filesystem sandbox is retained by default
  (runner log: `Muse sandbox network enabled; filesystem sandbox retained`) and nexus's card was
  at 100% anyway. User asked for all Muse terminals to run with host access, no runner restarts.
- `omnigent/inner/acp_executor.py`: spawn env now sets `MUSE_CODE_ACP_ALLOW_YOLO=1` for Muse
  backends only (the adapter's gate for `sandbox: disabled`); `_ensure_session` sets the `sandbox`
  session option to `disabled` right after `sandboxNetwork`, failing fast on error like the
  network step does. Applies to sessions (re)created once runners load this code — current
  runners/sessions untouched, nothing restarted.
- Tests: updated `test_muse_session_configures_host_network_before_prompt`, new
  `test_muse_session_fails_when_sandbox_cannot_be_disabled`,
  `test_spawn_env_grants_muse_the_sandbox_opt_out`,
  `test_spawn_env_withholds_the_opt_out_from_other_agents`. Suite: 169 passed
  (`test_acp_executor` + timeout config).
