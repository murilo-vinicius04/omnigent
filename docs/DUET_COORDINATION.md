# Agents coordinating across people (the duet)

## Handoff — 2026-11-01

Goal: Murilo and Hipólito's agents should share relevant work and coordinate
reliably across users, with activity visible in the UI, without interrupting
their conversations. The latest tested adapter and outstanding work are below;
older timestamped sections are historical and may be superseded.

- **Status:** server routing and limited proxied-shell delivery are live; the
  native Claude hook adapter is implemented and tested locally, but **not
  installed on Hipólito's Tower**. No real two-way exchange has been verified.
  At the last check his 11:06 reply was still queued. Recheck rather than assume.
- **Next:** user was asked to arrange SSH access on the Tower `100.88.134.127`
  to the Linux account running Hipólito's host and provide its username. A
  dedicated key is at `~/.ssh/id_ed25519_duet_host` (public `.pub`; never
  disclose the private key); its public key was supplied to the user. SSH as
  `murilo` previously failed. No access confirmation has been received yet.
  Once available, verify the existing hook and relay, install the tested adapter
  without restarting host or runner, and verify an actual round trip. Do not
  claim compatibility, live delivery or agent understanding without evidence.
- **Files/tests:** `scripts/duet_hook_proxy.py`,
  `omnigent/server/duet_hook_proxy.py`, `server/routes/sessions/routes_agent.py`,
  `tests/test_duet_hook_proxy.py`, `tests/server/test_duet_mcp_delivery.py`.
  The 39-test focused suite and server trial boot passed; details below. Server
  restarted at 18:25 Oct 31 after UI notice, health OK, host PID unchanged.
- **Caveats:** `runner_online=false` from another user's session is deliberately
  filtered and does not prove disconnection; the Tower's runner connected at
  18:24:58 Oct 31. Stale notes (>30 min) are not automatically injected; ack
  means handed off, not understood. Cross-user session access stays read-only.
  Host updater ends runners, so do not use it during active work. The robot
  resource board is advisory; no physical motion without explicit authorization.
- Worktree `/home/nexus/wt/friendly-layer` has many unrelated, uncommitted and
  untracked changes from other lanes. Preserve them; nothing here was committed.

## Native delivery adapter — 2026-10-31, 18:25 BRT

Implemented `scripts/duet_hook_proxy.py` (stdlib-only) and server MCP transport
`omnigent/server/duet_hook_proxy.py`, wired in `routes/sessions/routes_agent.py`.
An existing Claude policy hook can read an adapted `tool_relay.env` and receive
recent notes after native tool calls without restarting the runner or host. The
adapter forwards policy output unchanged, authenticates on loopback, uses the
existing runner MCP credentials with edit ACL to peek/ack by conversation ID,
acks after writing, and has a watchdog that restores the original env on crash.
It stops when another relay replaces its env. The existing inbox tool name is
used for this private transport so updated relays do not consume notes twice.
Delivery is at-least-once, does not wake idle agents, and requires a compatible
running hook and an installed adapter. Notes older than 30 minutes remain queued.

The real native relay → MCP manager → server route → queue test passes, as do
policy-output preservation, crash restoration, read-only ACL and cross-session
isolation: 39 focused tests; Ruff and `git diff --check` pass; server trial boot
passed. **Not yet installed on Hipólito's machine, no live peer exchange proved.**
SSH `murilo@100.88.134.127` was refused (publickey/password); no credentials were
changed. His replay lane reported idle via `sys_session_get_info`; that tool's
`runner_online=false` is **not reliable cross-user**: the runner status route
deliberately hides another user's online runner. Server logs show this runner
connected and its session stream attached at 18:24:58. Recheck current status
with an owner-authorized method; do not infer the runner is offline from that flag.
Both Hipólito host tunnels also connected: mhc-lenovo from 100.118.129.74 and
hipolito-Precision-5860-Tower from 100.88.134.127. Do not bypass cross-user permissions,
restart his host, or promise that this adapter is active there. Confirm the active
runner/hook and an authorized host-side deployment route, then install and verify
two-way delivery without interrupting his work. Server-only restart completed at
18:25 through the notice script: health OK, server PID `2578715`, local host PID
`1456750` unchanged. A live MCP probe on this Codex lane returned `unavailable`
as intended for a non-Claude session; this proves the new route is loaded, not
live delivery to Hipólito. No host/runner restart or live adapter installation.

## Delivery investigation — 2026-10-31, 18:18 BRT

Hipólito's `66f62b197c034509bcc5ae071a852e31` replay lane last used the
server-proxied shell at 11:43; its 16:39 work used native Bash. Its recorded
calls on Oct 31 were 309 Read, 175 Bash, 69 proxied shell, and one inbox check.
The latest recorded assistant answer was 16:40; the connected host/runner alone
does not prove an active turn. The 11:06 reply and 11:42 restart notice remain
queued; no new cross-user response is recorded. At 20:48–20:52 UTC this lane
received three notes from another morys5 lane; that establishes same-user delivery
to this session, not delivery to Hipólito.

The existing Claude relay transforms server policy verdicts locally. The old
`evaluation_response_to_hook_output` discards extra ALLOW response fields, so
adding notes to the server's policy response cannot deliver them to native tools.
The catch-all policy hook re-reads `tool_relay.env` for each invocation, suggesting
a possible local sidecar/proxy without restarting Claude or the host; this is
**unproven**, requires a secure way to fetch/ack notes and must preserve the
policy relay, hook latency, lifecycle and failure behavior. Do not hijack policy
verdicts, consume the real inbox as a probe, or alter a live remote relay before
isolated tests and a safe deployment/rollback design. The runner-side hook already
implemented can deliver at native tool boundaries once its code is actually loaded.
No hosts or runners were restarted for this investigation.

## Current handoff — 2026-10-31, 17:40 BRT

Older timestamped sections below are historical and contain superseded claims. Murilo wants
his and Hipólito's agents to collaborate on related work, negotiate shared resources according
to readiness, and show their messages and Hipólito's shared work in the UI. Keep cross-user
access read-only; the activity view must respect existing access to **both** message endpoints.

**Start here:** The server-side router, activity UI, and a **limited automatic delivery path**
are live. New notes (≤30 minutes old) can reach an already-running Claude/Codex agent when it
next calls the server-proxied Omnigent `sys_os_shell`; the server attaches them to the tool's
JSON result. This needs no host restart, but does not deliver while an agent only uses native
tools or is idle. The independent runner-side hook supports additional tool boundaries, but an
old in-memory Claude relay needs updated code and relaunch before it can use that hook. Do not
claim live two-way communication until verified. `docs/HOST_UPDATE.md` describes the host
updater, which ends its runners; avoid it while work is active. Do not restart a host from its
own lane or command the real robot. The resource board is advisory, not a hardware interlock.
Older sections below contain superseded deployment claims.

**17:39 finding:** A server-only alternative works with already-running native runners: the
existing MCP proxy forwards `sys_os_shell` through the server. `server/duet_mcp_delivery.py`
attaches recent (≤30 min) queued notes to a successful JSON shell result as
`_omnigent_duet_notes`, preserving existing fields; acknowledges only after sending the
response. It runs only for Claude/Codex native session wrappers after the existing edit access
check, fails open, and leaves older notes queued. Tests exercise the real proxy manager and
route, existing JSON, stale notes, failures and read-only access (`.venv/bin/pytest
tests/server/test_duet_mcp_delivery.py tests/server/test_duet_router.py
tests/server/test_duet_route.py tests/server/integration/test_mcp_proxy.py
tests/server/routes/test_sessions_mcp_proxy.py tests/test_duet_delivery.py -q`: 33 passed,
plus a subsequent 3-test delivery suite). A live `sys_os_shell` call on this lane returned
exit 0 after deployment, without a live cross-user message. It needs a server restart,
not host/runner restarts. It does not inject into native Bash or wake idle sessions, and it
depends on the model making a proxied Omnigent shell call; real peer delivery is unverified.
Server-only restart completed at 17:39 after the 30-second notice; server PID 2526698,
health OK, all four host records online, local host PID unchanged (1456750).
This limited server-side path supersedes the absolute claim above that all auto-delivery to
the active Claude lane requires a host update.

**17:32 notice correction:** The restart banner now warns on an open page *before* the
restart, including “refresh (F5) once the server is back if the page freezes.” A freshly
loaded page ignores the completed notice, so an ordinary F5 no longer requests another F5.
`scripts/restart_server.py` publishes the warning and waits 30 seconds before restarting.
The web build was deployed without a server or host restart; already-open pages must refresh
once to load this fix. It does not prove or fix the underlying page-freeze cause. Direct
`systemctl restart` and crashes cannot produce this advance notice; use the script for planned
restarts. UI tests (4), restart-script tests (5), type-check, build and lint passed.

- **Observed failure:** Hipólito's lane `66f62b197c034509bcc5ae071a852e31` sent a warning
  at 11:05 that Murilo's `c4c49d11d44e4835adf2248c21ab5c54` read; its 11:06 reply and the
  11:42 restart note remained queued at the last check. At 12:25 and 13:34 Murilo's lane
  attempted relevant sends, but the router rejected both with “no lane matches”; failures
  never appear in `notes.json`. Hipólito's lane checked its inbox only at 10:48 in the recorded
  transcript, although it worked into the afternoon. Do not infer it was idle all day.
- **Delivery implementation:** `omnigent/duet_delivery.py`, `claude_native_bridge.py` and
  `codex_native_hook.py` attach bounded, timestamped notes at a tool boundary and acknowledge
  after the hook response. Notes can be delivered more than once on failure; `read` indicates
  handoff to a runner, not understanding or action. Idle agents are not woken, and agents
  still need to author relevant messages. API access follows existing session permissions.
- **Live checks:** `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus python3
  scripts/restart_server.py` performs a spare-port boot, UI warning and server-only restart.
  At 17:39 server PID `2526698` was healthy, local host PID `1456750` unchanged, four host
  records online. Recheck before relying on these details. `notes.json` and `resources.json`
  are under `~/.omnigent/duet/`; links share RAL/franka_teleop sessions read-only.
- **Freela:** Hipólito's projects are `frelaa` (`3b450cb076bf457d881ec9d6e432a4b3`) and
  `amarras` (`97732f2958704768a2fcc8e1c109ddd0`); both project briefs were populated for his
  review. Freela repo is private `github.com/murilo-vinicius04/explora-pipeline`; at the user's
  explicit request `MHC-CodeSmith` was invited with **write** access (invitation `340779393`,
  pending at the last check; personal GitHub repos cannot grant read-only collaborator access).
  The detailed Portuguese handoff is untracked locally at
  `/home/nexus/explora-pipeline-worktrees/hub-organiza-o-do-reposit-rio-do-freela/docs/HANDOFF_HIPOLITO.md`;
  it was not pushed. Local `dev` `/home/nexus/explora-pipeline` is diverged and contains an
  uncommitted updated `HANDOFF.md`; preserve it and ignored client data. Push only when told.
- **Worktree:** `/home/nexus/wt/friendly-layer` has extensive unrelated uncommitted changes;
  preserve them. No commits or pushes were made for this work. Useful focused checks:
  `.venv/bin/pytest tests/server/test_duet_router.py tests/server/test_duet_route.py
  tests/test_duet_delivery.py tests/test_codex_native_hook.py -q` and
  `.venv/bin/pytest tests/test_claude_native_bridge.py -q -k 'duet_note_flows or
  post_tool_hook_delivers'` (60 + 2 passed); touched-file Ruff passed.

**17:27 update:** Fixed two afternoon routing failures: old titles can resolve through a
previously recorded exchange to the same still-accessible conversation ID; ambiguous matches
are rejected. Untargeted follow-ups can reach one recent relevant partner on one keyword;
task summaries are also searchable, and failed sends return accessible candidate IDs. Added
bounded pending/ack endpoints and tool-boundary delivery for updated Claude relays and Codex
hooks: notes are informational, timestamped, retried if acknowledgement fails, and never
interrupt the turn or wake an idle agent. Existing live Claude relays keep their old in-memory
code and do **not** acquire this delivery behavior by restarting only the server. Remote host
code must be updated and the relevant runner relaunched when safe; host update ends runners.
Do not restart an active host or claim delivery to Hipólito's current lane until verified.
This does not force agents to author relevant notes, provide immediate delivery while an
agent performs no tools, or enforce physical hardware exclusion. Tests: 60 router/routes,
delivery and Codex-hook cases; focused Claude relay cases plus an end-to-end synthetic
router→relay delivery passed. The server-only restart completed at 17:28; health, pending
endpoint and all four host records passed; local host PID remained 1456750. The server-side
routing changes are live. The active Hipólito Claude relay remains old and delivery to it is
not yet verified.

**11:47 update:** Deployed the current web build, including `AgentActivity` and a new
`ServerRestartNotice`, without restarting the server or removing existing static assets.
`scripts/restart_server.py` now publishes a planned notice 20 seconds before restarting,
then publishes complete or failed state; loaded pages poll the public non-secret JSON file
every five seconds. The notice offers a refresh button. Previously open pages must refresh
once to load this new code; no notice was delivered to Hipólito's already-open page. Direct
`systemctl restart` bypasses this script and its notice. Backend script tests (5), notice UI
tests (2), type check, touched-file lint and web build passed. Browser interaction as each
account and the shared-activity permissions still need end-to-end verification.

**11:43 update:** At Murilo's request, restarted only `omnigent-server.service` using
`scripts/restart_server.py`; isolated boot and post-restart health, login and host checks passed.
New server PID 2181678; local host PID 1456750 stayed the same, all four host records came
back online, and Hipólito's replay lane continued issuing commands after the restart. This
loads the latest server-side changes described below; the activity UI build is still **not
deployed** and active runners may not have the new tool schema. A restart warning note
`03eb4db36b37` was queued to Hipólito's replay lane but had not been read at the last check;
there was no confirmed direct notification to Hipólito. Historical references below to these
server-side changes being undeployed are superseded by this update.

- **Observed:** `66f62b19` (Hipólito, Replay robô real) warned `c4c49d11` (morys5,
  Calibração de câmeras) about two concurrent FR3 publishers at 11:05. The calibration lane
  took that note and replied at 11:06; its reply was still queued at the last check. A later
  update reported the camera sweep finished at 11:14. Re-check current state before any action.
- **Why the resource board didn't prevent it:** the replay lane reported no resource tools;
  the user independently launched the calibration sweep; the recorded robot claim was made
  after the overlap. `ResourceBoard` is voluntary, permits expiring leases and cannot block
  shell commands, other publishers or human launchers. It is not a hardware safety interlock.
- **Already live:** router pagination and explicit subagent addressing, sender ID in inbox,
  and rejection of ambiguous/stale explicit targets. Server restarted safely at 09:56:26 BRT,
  PID 2080778, using `python3 scripts/restart_server.py`; health and host reconnections passed.
  The older statement below that this router fix has not been restarted is obsolete.
- **Implemented, not deployed:** optional `yield_to_next=true` on `sys_resource_release`:
  only the holder yields to the next waiter and rejoins at the tail; with no waiter it retains
  its hold. Holder must stop using the resource first. See `server/resource_board.py`,
  `server/routes/resources.py`, `runner/resource_tool.py`, `tools/builtins/resources.py`.
  This is voluntary handoff, not automatic readiness detection or physical exclusion.
- **Implemented, not deployed:** `GET /v1/duet/activity` and sidebar `AgentActivity` panel.
  It lists up to 100 recent notes only when the viewer can read both endpoints; shared lane
  titles, owner, status and available task summaries come from accessible sessions. Viewing
  does not consume inbox notes; `read` only means taken from inbox. Files: `server/routes/duet.py`,
  `server/duet_router.py`, `web/src/shell/AgentActivity.tsx`, `SidebarHeaderActions.tsx`.
- **Also implemented, not deployed:** new Hub lane brief asks agents to share findings, ask and
  answer questions, and not command physical hardware when resource tools are unavailable;
  descriptions clarify that the board is advisory. Existing lanes may not receive new guidance.
- **Validation:** 33 focused backend tests passed (duet router/routes, resource board/routes,
  resource runner); activity UI test passed; TypeScript and touched-file lint passed. Updated
  server passed `scripts/restart_server.py --smoke-only`. A full Vite build succeeded into
  `/tmp/omnigent-agent-activity-build` (CSS highlight and large-chunk warnings). Nothing from
  this latest work was deployed: robot replay lane was active when checked. No commits/pushes.
- **Next:** confirm physical operation is idle before deployment. The user's earlier direction
  authorizes a safe server restart, avoiding host restarts: `python3 scripts/restart_server.py`
  tests a spare-port boot and checks health/hosts. Web build normally empties
  `omnigent/server/static/web-ui` and can erase `usage`/`voice-ab`; follow
  `docs/FRIENDLY_LAYER_SESSION.md` for safe deployment. Hosts/runners need updated code and
  sessions exposing the new release schema before an agent can use yielding. Verify the UI as
  both accounts, including denied private notes, and verify a real two-way exchange.
- **Still open:** auto-delivery and automatic relevant context/help discovery; more expressive
  readiness/negotiation; a physical shared command gateway or controller-side arbiter covering
  every agent and user launcher. Do not claim FIFO leases or prompt instructions enforce safety.
  The shared working tree contains many unrelated uncommitted changes; preserve them.

Two people on one server, each with their own hosts and provider quota. Their agents share
information and take turns on shared things (the robot, a camera, a GPU). Nobody has to relay
anything by hand.

## Who can see and change what

- Sessions of one person are shared with the other **read-only** (permission level 1). Nobody
  types into the other's lane. `omnigent.server.duet_merge.limit_grants` caps existing grants,
  and `share_project` now defaults to read-only.
- Agents talk through the server, never lane to lane: a note goes to the other lane's duet queue,
  not into its conversation, so no edit permission is needed. Waking a lane in someone else's
  session (`urgent`) therefore only queues; it needs edit access, which is deliberately absent.
- Lanes of linked projects are shared automatically, read-only, within a minute of being created.
  The links are in `<data dir>/duet/links.json`, one entry per direction and project name:
  `{"shares": [{"owner": "morys5", "reader": "hipolito", "projects": ["RAL"]}]}`. The server
  re-applies them every minute (`omnigent.server.duet_links`); it only adds read grants and never
  raises or removes one. To stop sharing a project, remove it from the file and revoke the
  existing grants by hand.

## Notes (`sys_duet_say`, `sys_duet_inbox`)

An agent volunteers a short note; the router (`omnigent.server.duet_router`) picks the lanes
that match by keywords, drops repeats and caps how many notes a lane or a pair of lanes may send
per hour. The receiving agent reads them with `sys_duet_inbox`, which it is told to do on its own
at the start of a task (tool description, and the brief of lanes the Hub opens).

## Shared resources (`sys_resource_acquire`, `_release`, `_status`)

`omnigent.server.resource_board` keeps, per resource, one holder and a first-come queue.
The board is advisory: neither shell commands nor external operators and publishers are
enforced by it. A lease expiring does not establish that physical motion has stopped.

- `acquire` claims it, renews your own claim, or queues you. The holder is told someone waits.
- A claim is a lease (default 30 minutes, at most 240). When it ends or is released, the next
  lane is promoted and told through its inbox. A promoted lane has 10 minutes to confirm by
  calling `acquire` again, or the turn passes on, so an absent lane cannot block the others.
- Names are normalised (`Franka Robot` and `franka-robot` are the same). `status` lists them all.
- A turn is **never** permission to move real hardware. That still needs the person's explicit go.
- A holder that is not ready may call `sys_resource_release` with `yield_to_next=true`.
  The next waiter is promoted and the previous holder rejoins the end of the queue. Only
  the holder can yield; it must first stop using the resource. With no waiter, it keeps its turn.

## Activity view

The sidebar's Agent activity button shows recent duet messages and shared sessions. The server
only returns a message when the viewer can read both endpoint sessions; viewing the panel does
not consume the inbox. Shared lane titles, status and task summaries are taken from the existing
session permissions, and messages marked read were taken from an inbox, not necessarily acted on.

A person can look and override with the API:

```
GET  /v1/resources                         who holds what, and the queues
POST /v1/resources/<name>/release          {"force": true}   free it whoever holds it
```

State lives in `<data dir>/duet/resources.json` and `duet/notes.json`.

## Restarting the server

Use `python3 scripts/restart_server.py`. It boots the new code on a spare port first and only
then restarts the unit, and afterwards checks health, the stored login, the Tailscale proxy and
that the hosts are back. The unit restarts itself on any exit (`always-up.conf`).

## State on 2026-10-31 ~00:00 UTC (for whoever picks this up)

Everything here is uncommitted in the shared worktree; nothing was pushed.

- Done and live: read-only grants between `morys5` and `hipolito` (53 lowered), duet notes,
  resource board, auto-sharing of `RAL` (to hipolito) and `franka_teleop` (to morys5), safe
  restart script, `Restart=always` drop-in. Tests: 219 on the touched suites plus 19 startup.
- Measured: `~/.omnigent/duet/notes.json` holds 5 notes. The note `facba4ae` from our duet
  lane to Hipólito's new lane "Analisar bags e ajustar teleoperação" (franka_teleop) is `read`:
  his agent called `sys_duet_inbox` itself. The other 4 (2 duet tests to his older lanes, 2 robot-queue
  notes from RAL "Testes no robô real") are still `queued`. No note has ever been sent back from any
  of his lanes, so there has been one-way delivery + read, but no two-way agent conversation yet.
  An RAL lane sent two notes unprompted (prompted by the user's request, so spontaneity unproven).
- Hosts: the lab PC host is one edit behind and refuses updates while a lane runs; the Lenovo is
  behind again after later edits; the VM host `ubuntu-gpu` runs old code until restarted (never
  from inside a lane). Resource tools only exist in runners started after a host has this code.
- Open decision: Hipólito can still read the lane `Migração do servidor do Hipólito` (monitoring
  talk). Ask Murilo before revoking. Hiding lanes from the other person's own UI is not built.
- Not built: anything that forces spontaneity (scout turning lane activity into notes, injecting
  queued notes into a lane's next turn).
- Optional: re-merge Hipólito's old local server (3 items after 15:47), stop his old Lenovo server
  once he confirms, move Murilo's own lab-PC host to a git checkout.
- `ruff` reports 6 errors (E501 x5 in `server/hub.py` lines 3070-3915 and
  `_sessions/orchestration.py`, RUF022 in `_sessions/common.py`); none are in lines these changes
  touched.

## Earlier observations (some claims below corrected by the latest update)

- Router fixes live (server restarted 00:18, all hosts reconnected): `to=` matching ignores
  accents and case, accepts a session-id prefix, falls back to keyword matching when the named
  lane does not exist, and logs silent drops with sender + reason. 11 duet tests pass.
- Loopback proof (no model cost, REST only): note `607f433c` sent from `a1c2a2f5` to `3d48f593`
  by ID prefix routed cleanly and was taken from the inbox. Queue left clean (probe consumed).
  Note: `DuetStore.take()` matches `to_session` exactly and case-sensitively; pass the full id.
- Queue (`~/.omnigent/duet/notes.json`): 8 notes, 0 from `c57fb3ea`. To his codex lane: `facba4ae`
  and `2a83c126` read, `b6eb6014` (ID-addressed) still queued. Nothing else was sent.
- Why no reply yet: his codex lane `c57fb3ea` last turned 00:10-00:14 and is quota-dead
  (`harness RuntimeError: You've hit your usage limit`, items 291/294). Hipólito typed at 00:14
  and hit the same wall. His agent reads its inbox unprompted (proven 2x), so the reply path is
  armed and fires on quota reset with zero human relay. A 28-min queue watch (`/tmp/duet_watch.py`
  + Monitor) is armed for the first note from `c57fb3ea`.
- His live lane `4de6de54` (Claude, owns the FR3 right now, edge probe running ~00:49-00:57+) can
  never do duet: the native bridge (`omnigent/claude_native_bridge.py`) exposes no duet/resource
  tools; they exist only in the runner (`omnigent/runner/tool_dispatch.py`, `_DUET_TOOLS`,
  `_RESOURCE_TOOLS`). Specified next step, NOT started: expose `sys_duet_inbox` / `sys_duet_say`
  (+ resource trio) as bridge tools executed via the same REST helpers, with tests mirroring
  `tests/runner/test_comment_relay.py`; needs a lane restart on his side to take effect, so it
  cannot reach a running lane by design.
- Deliberately NOT done: server-side injection of notes into his live arm lane. It would be a
  cross-user write against the read-only grants, noise inside hardware turns, and spend on his
  Claude quota. Revisit only with his opt-in and a hardware-lane guard.

## Routing fix, 2026-10-31

- Hipólito's robot lane `4de6de54` did call `sys_duet_inbox` at 00:17. The
  earlier statement that its Claude bridge could never use duet was wrong.
- His Codex lane attempted an exact send to subagent `b9df00de`; the previous
  router searched only default lanes and only the first 300 accessible sessions.
  Routing now searches all accessible pages and includes subagents when a
  target is explicit. Access is still limited to the sender's grants.
- Explicit targets must resolve uniquely. Failed explicit targeting does not
  fall back to unrelated keyword matches. Inbox notes include sender session ID.
- Tests and isolated server smoke boot passed; the running server has not yet
  been restarted because the live robot lane had a long-running operation.
  No automatic delivery or cross-user reply is proven by these changes.

## Real robot coordination incident, 2026-10-31

- The replay lane `66f62b19` reported at 10:48 that it did not have resource tools;
  it initially stopped. The user later said the robot was free and requested the replay.
- The camera-calibration sweep was started independently by the user. Two publishers
  overlapped at approximately 11:03–11:05; the first recorded `franka-robot` claim
  by calibration was at 11:06, after the overlap. Duet messages warned about it
  after the fact. The earlier attribution of the blocker to the board itself is
  therefore not supported: the board was bypassed rather than asked to deny access.
- New lane briefs now request mutual assistance and say not to command physical
  hardware if resource tools or the board are unavailable. This is model guidance,
  not a physical interlock; existing sessions may not receive it. Changes require
  deployment before taking effect.
- Real exclusion requires a shared command gateway or controller-side ownership
  check covering every publisher and the user's launcher, with failure handled
  safely and hardware state verified before another owner can take over. Quota,
  leases and queue messages cannot provide that guarantee by themselves.

## Limits

- Agents act on their own only as far as their instructions go: the tools say to use them
  unprompted, but nothing forces a model to. Lanes started before a host had this code do not
  have the tools at all; update the host and start a new session.
- Nothing yet turns lane activity into notes by itself, and queued notes reach a lane only when
  its agent reads the inbox.
