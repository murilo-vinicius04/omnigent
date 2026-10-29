# Hub notification chime — lane notes

Lane "Som de notificação discreto": a per-viewer Hub sound (no narration) for
"task finished" and "needs your answer". Worktree `/home/nexus/wt/hub-som-discreto`,
branch `hub/som-de-notifica-o-discreto`, commit `13e3818fa` (this note is uncommitted).

## Done
- `web/src/lib/hubChime.ts`: Web Audio chime (finished D5→A5, attention A5→E5→G5,
  preview), zustand store + localStorage (`omnigent:hub-chime-enabled`,
  `omnigent:hub-chime-volume`, default 0.4), long-poll listener on `/v1/hub/updates`.
  No backend change.
- UI: `HubChimeButton` in the Hub panel header (`shell/HubProjectsPanel.tsx`) and in
  the Hub chat composer toolbar (`pages/ChatPage.tsx`, only when label
  `omnigent.hub == "1"`); `HubChimeSettingsControl` in Settings → General → Hub;
  `useHubChimeListener()` mounted in `shell/AppShell.tsx`.
- Tests pass: hubChime (9), HubChimeButton (2), HubChimeSettingsControl (3),
  HubProjectsPanel (17), SettingsPage (60), ChatPage.composer (170),
  tests/server/test_hub.py (98).
- Deployed 2026-10-18: live server now serves `assets/index-DWynMeO7.js`
  (contains `hub-chime`). Verified through `http://127.0.0.1:6767`, not by file.

## Open / not verified
- Real audio never heard in a browser; no browser end-to-end. User must hit
  "Test sound" and confirm.
- Known gaps: possible double sound with an open voice call (`followHubNews`) or
  narration; no suppression when the Hub chat is focused. Not implemented.
- `tsc -b` and `oxlint` were not run on the changed files.
- Commit message rule: no Claude co-author / attribution trailers.

## How the frontend is deployed (the trap)
- `omni server` (pid from `pgrep -af 'omni server'`, port 6767) runs from the
  friendly-layer venv and serves `/home/nexus/wt/friendly-layer/omnigent/server/static/web-ui`
  (gitignored). Vite's `outDir` is `<worktree>/omnigent/server/static/web-ui`, so a
  build in this worktree does NOT reach the server by itself: copy it.
- Build (a bare `vite build` got OOM-killed by systemd-oomd; this one works, ~4 s
  once deps are warm): run `systemd-run --user` with `XDG_RUNTIME_DIR=/run/user/1000`
  and `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`, `-p MemoryMax=10G
  -p CPUQuota=400% -E RAYON_NUM_THREADS=4 -E NODE_OPTIONS=--max-old-space-size=6144`,
  working dir `web/`, command `node_modules/.bin/vite build`, log to the scratchpad.
- Publish: `cp -a <worktree>/.../web-ui/assets/. <friendly-layer>/.../web-ui/assets/`,
  then write `index.html` last (via a temp name + `mv`). Old assets stay, so open
  tabs do not break. No server or runner restart. Check with
  `curl -s :6767/ | grep -o 'assets/index-[^"]*\.js'`.
- Never kill the `omni server` or runner processes to deploy; sessions live there.
- `node_modules` in this worktree are real dirs now (untracked; never commit them,
  nor `.venv`).

## Measured
- Runner WebSocket closes (code 1006) were logged at 19:06, 19:16, 19:20; a new
  runner connected 19:22. Cause not identified (no OOM/kernel kill found).
