# Handoff — Hub permissions / terminal recovery

Current task is complete. User requested a short on-disk handoff before compaction; **do not start new work**. Session's 5h window reported 83%, resets 13:34 BRT. Workspace `/home/nexus`, active repo `/home/nexus/wt/friendly-layer` (`feat/friendly-layer`). Extensive unrelated dirty edits from concurrent lanes: preserve them; no commits made here.

## Latest: Muse defaults (completed and activated)
- User wants Muse “Don't ask” by default, avoiding repeated per-lane changes.
- `omnigent/server/hub.py::store_backend/open_chat`: new Muse Hub lanes now save label `omnigent.acp.permission_mode=bypassPermissions`.
- `omnigent/server/routes/sessions/routes_core.py::switch_session_agent`: project/Hub lanes switched to `muse-code`/`muse` get that label too; response labels updated. Existing RPC permission mechanism applies it on spawn. Other providers unchanged; no global standalone Muse default added.
- Updated `tests/server/test_hub_lanes.py::test_muse_lane_uses_acp_without_a_terminal_or_tier_catalog` to assert label. Focused test **1 passed, 41 deselected**. Ruff check and `git diff --check` passed. Existing unrelated Ruff-format difference in `hub.py::_lane_run` left untouched.
- Live PATCH `{"permission_mode":"bypassPermissions"}` succeeded for Muse sessions `68ccfa0f37eb4ecaa8ff0ee4865d7fb5` (active) and `09ba67f2d0fb4b10bd6396563b63b5ab` (idle). Also verified `75456092097d4c73b43ac4cd60ac54dc` and standalone `e64ea00517cf4804ab16710303aab660` already retain that label. No tasks replayed/restarted.
- Server reloaded; service **active**, `/health` **200**, all four sessions read back bypass. Human check still available: refresh Hub, create Muse lane → Permissions should show “Don't ask”. No test lane/model task launched. Details also in `docs/MUSE_CODE_HUB_STATUS.md`.

## Earlier completed work
- Codex compaction 503: remote TUI resume rejects permission overrides (`Permission overrides are not supported when resuming a remote task.`). Fixed `omnigent/codex_native_app_server.py::build_codex_remote_args` to strip permission overrides on remote resume; permissions remain applied through existing preload RPC. Tests in `tests/test_codex_native.py`: **19 passed, 235 deselected**. Rosca recovered; actual `context_compacted` + `task_complete` at `2026-10-21T15:14:51Z` (TUI 1m36s). See `docs/CODEX_COMPACTION_2026-10-21.md`.
- Antigravity rosca approvals: stored bypass flag existed but running process lacked it. Stopped/recovered only idle affected lane, preserving native UUID `93d38dc8-b8cc-428d-a9d9-4e22d4541d32`. Verified new agy process had bypass and resume flags, pane alive, no pending approvals. No source change needed. See `docs/ANTIGRAVITY_ROSCA_APPROVAL_2026-10-21.md`.

## Identifiers / commands / traps
- This conversation `c4ed1ebf84a0488888c3742c3d8bb58d`; rosca `ee05b147db7b4e0ca931c5fc181d5054`; host `8661c0d6534c4314923985bdaa550a10`. Old runner PIDs/sockets in earlier notes may be stale; recheck before acting.
- API `http://127.0.0.1:6767`; use `.venv/bin/python` and `omnigent.cli._host_http_json` for authenticated calls. Never print tokens, csrf values, or complete process argv. Inspect selected flags only.
- Server reload: `XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user restart --no-block omnigent-server.service`. Host and lane runners are separate; avoid restarting whole host unnecessarily.
- Test: `.venv/bin/python -m pytest tests/server/test_hub_lanes.py -q -k muse`. Pytest retry plugin needs local sockets; run through host escalation if sandbox blocks it. Lint only touched files; do not reformat/revert concurrent edits.
- New runners detect stale zygote code and fall back to fresh spawn. Saved launch arguments alone do not update an existing native process. Use normal live permission PATCH where supported; verify runtime/readback, not only saved config.
- No outstanding user request beyond this handoff. Do not infer permission to resume unrelated lanes/tasks from old notes.

### Cleanup lane follow-up: real approval reproduced
- `24c0275559674b63adc47624b3e48d91` had bypass label but running ACP executor was prompting after Claude→Muse switch. Observed pending `elicit_16ef0b8e11af04bccc7678e4fcb2f24c` for read-only SSH docker listing.
- Root cause: `_clear_session_agent_caches` deletes `_session_acp_permission_overrides`; only session-init restored it, which agent switching skips.
- Patched runner/app.py: seed override from generation-checked session snapshot labels; ensure both turn dispatch paths fetch snapshot before ACP spawn when override missing. Existing concurrent changes in this file preserved. New runner processes load patch; existing cleanup runner not restarted.
- PATCH permission_mode=bypassPermissions returned200; live log 15:25:05 UTC confirms executor set mode and harness endpoint200. Future requests use bypass; existing card stays pending.
- Attempt to accept existing card was REJECTED by automatic approval review (exact command scope not established); do not bypass. User must approve existing card or supply explicit scoped authorization. No task replay or remote cleanup performed.
- ACP permission tests26 passed; Ruff/diff clean. Native runner permission tests started separately (may require host socket access).
- Focused runner permission tests rerun with host socket access:4 passed,54 deselected (8.16s). Earlier sandbox-stalled pytest PID7229 terminated. Patch not yet exercised by a real provider switch; live-mode acknowledgement verified, subsequent command bypass awaits pending card clearance.

## Closing handoff — 26 October 2026
Latest task completed: recurring Muse SSH/Tailscale failures. Proxmox lane `b75f476098da466bafa346a91e9f77e5` switched Claude→Muse; its shell reported `Network is unreachable` and netlink permission errors. Host Tailscale was actually Running, peer `pve` online, route via tailscale0; read-only host SSH to `root@100.82.54.27` returned `pve` / PVE8.4.0. Earlier agent claims that Tailscale was down were incorrect.

Implemented in `omnigent/inner/acp_executor.py::_ensure_session`: for Muse backends only, after session/new and before first prompt, send standard ACP `session/set_config_option` with `configId=sandboxNetwork`, `value=enabled`. Configuration rejection raises an explicit error and clears cached session id. Filesystem sandbox and approval policy remain intact. Installed adapter defaults to proxy-only independently of Omnigent Don't ask. No adapter package/global Muse settings edited.

Tests added in `tests/inner/test_acp_executor.py::test_muse_session_configures_host_network_before_prompt` (success/error cases). Focused7 passed. Full suite134 passed under sandbox; sole failing relay socket test passed with host socket access (all135 covered). Ruff and diff checks passed. Configuration-only real installed-adapter probe confirmed `sandbox=enabled`, `sandboxNetwork=enabled`; no model prompt sent.

Activated for Proxmox lane by checking idle then POST stop_session/retry_session; both202, recovery runner_relaunched. New runner was online/idle, no tasks replayed. Log: `~/.omnigent/logs/runner/runner-b75f476098da466bafa346a91e9f77e5-20261023-195445-721485.log`. No whole-server restart needed. Detailed update also in `docs/MUSE_CODE_HUB_STATUS.md`.

Remaining: actual Muse-issued SSH after reload was NOT exercised; ask lane to run `ssh -o BatchMode=yes root@100.82.54.27 hostname` and expect `pve`. Other already-running Muse executors need an idle runner reload to receive startup change; not mass-restarted. No new work requested beyond saving this handoff. Changes uncommitted; preserve extensive concurrent dirty edits. Prior cleanup pending-card status is historical and must be rechecked before any action; do not retry its rejected approval automatically.
