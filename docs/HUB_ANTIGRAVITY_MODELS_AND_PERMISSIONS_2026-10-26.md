# Hub Antigravity Model Picker & Autonomous Permissions Handoff (2026-10-26)

## Overview & Context

This task addressed two key issues with Antigravity (`antigravity-native-ui`) within Omnigent Hub lanes:
1. **Model Selection in Hub Lanes**:
   - In the Hub Projects view (`/`), lanes running Antigravity displayed static text (`"Its default"`) instead of the model selector dropdown, because Antigravity was omitted from the model picker registry.
   - In the "Open lane" modal (`NewLaneDialog`), only `Gemini Flash (agy)` was offered under Antigravity, while `Gemini Argon (agy)` was missing.
2. **Interactive Permission Prompt Blocking Hub Lanes**:
   - When switching an existing lane (such as `rosca`, `ee05b147db7b4e0ca931c5fc181d5054`) to Antigravity, the lane repeatedly stopped and entered `Waiting for your approval` on every bash command (`nvidia-smi`, `ps`, `tmux`).
   - Hub lanes are autonomous background lanes and have no interactive approval affordances directly in the Hub dashboard.

---

## Changes Made

### 1. Hub Model Picker Registry & Open Lane Choices
- **[`omnigent/server/hub_models.py`](file:///home/nexus/wt/friendly-layer/omnigent/server/hub_models.py)**:
  - Added `"antigravity-native-ui": "antigravity"` to `_PICKER_WRAPPERS` so that `SessionRun.as_dict()` populates `kind="antigravity"` and populates the available models catalog (`Gemini 4 Argon (Medium)`, `Gemini 3.8 Flash (Medium)`, `Claude Opus/Sonnet`, `GPT-OSS`).
  - Added `"antigravity": "antigravity-native"` to `HOST_CATALOG_HARNESS`.
  - Updated `run_from_snapshot` so `kind in ("claude", "codex", "antigravity")` sets `model = reported or override` and properly reports pending switches when `override != reported`.
- **[`web/src/shell/HubProjectsPanel.tsx`](file:///home/nexus/wt/friendly-layer/web/src/shell/HubProjectsPanel.tsx)**:
  - Added `{ value: "Argon", label: "Gemini Argon (agy)" }` to `LANE_MODELS` in `NewLaneDialog` alongside `Flash`.
- **Rebuilt web bundle**:
  - Ran `npm --prefix web run build` to update assets in `omnigent/server/static/web-ui/`.

### 2. Autonomous Permissions for Hub Lanes on Agent Switch
- **[`omnigent/server/routes/sessions/routes_core.py`](file:///home/nexus/wt/friendly-layer/omnigent/server/routes/sessions/routes_core.py#L3083-L3100)**:
  - Updated `switch_session_agent` (`POST /v1/sessions/{session_id}/switch-agent`):
    - When switching an agent on a Hub lane (`session.project_id` or `LANE_LABEL in session.labels`), the target wrapper now automatically sets appropriate bypass/auto-approval launch flags:
      - `antigravity-native-ui`: `["--dangerously-skip-permissions"]`
      - `claude-code-native-ui`: `["--permission-mode", "bypassPermissions"]`
      - `codex-native-ui`: `["--dangerously-bypass-approvals-and-sandbox"]`
    - This ensures background worker lanes do not stall on interactive tool approval cards.

### 3. Lane Recovery & Verification
- Resolved the pending elicitations on lane `ee05b147db7b4e0ca931c5fc181d5054` via `POST /v1/sessions/{id}/elicitations/{eid}/resolve` (`{"action": "accept"}`).
- Patched its `terminal_launch_args = ["--dangerously-skip-permissions"]`.
- Verified the lane finished its turn and settled to `idle` with `pending_approvals: 0`.
- Verified `GET /v1/hub/projects` returns populated `run.models` for Antigravity lanes.

---

## Verification & Test Results

- **Python Tests**:
  - `tests/server/test_hub_lanes.py` and `tests/test_antigravity_native_bridge.py`: 154 passed (`.venv/bin/pytest`).
- **Web Component Tests**:
  - `web/src/shell/HubProjectsPanel.test.tsx`: 17 passed (`npx vitest run src/shell/HubProjectsPanel.test.tsx`).
- **Service Restart**:
  - Cleanly restarted `omnigent-server.service` via `systemctl --user restart omnigent-server.service`. (Host and runner services untouched).

---

## Current Status & Open Items

- **Worktree**: `/home/nexus/wt/friendly-layer` on branch `feat/friendly-layer`.
- **Uncommitted Files Modified in this Task**:
  - `omnigent/server/hub_models.py`
  - `omnigent/server/routes/sessions/routes_core.py`
  - `web/src/shell/HubProjectsPanel.tsx`
  - `omnigent/server/static/web-ui/*` (built assets)
- **Note for Next Agent**:
  - Do NOT stage all dirty files across the worktree (`feat/friendly-layer` is shared and has uncommitted files from other active lanes). Commit or branch only by specific file names.
  - When inspecting running processes for Antigravity sessions, remember that updating database metadata alone does not modify already running child processes until restarted or next launched with the new `terminal_launch_args`.
