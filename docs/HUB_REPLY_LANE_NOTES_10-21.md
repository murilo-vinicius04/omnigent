# Hub replies lane: where it stopped, 10-21 (session c4ed1ebf84a0488888c3742c3d8bb58d)

Stopped at the user's request because the week plan window was at 98% (resets 10-23 08:59).
Do not spend quota on live worker tests.

## In the middle of
Investigating "Compaction failed: runner returned 503" for the rosca lane (Hub entry 2987, 11:52:25).
Nothing was changed for it yet; only read logs.

## Found so far (server log ~/.omnigent/logs/server/server-20261021-110151-576502.log)
- Session in the errors: `ee05b147db7b4e0ca931c5fc181d5054`. On 10-19 the Hub called this id "vla"; by 10-21 the Hub log
  calls the lane "rosca". NOT verified that they are the same lane or a rename: check `~/.omnigent/hub/morys5/lane_names.json`
  and entry 2987's neighbours first.
- Every compaction attempt on it failed with HTTP 503 from the runner:
  11:10:01 (manual, via hub_handoff), 11:13:01 (auto 85%), 11:24:44 (auto 87%), 11:35:48 (auto 89%),
  11:49:13 (auto 91%), 11:51:56 (manual, the error the user saw).
- Body of the 503: `{"error":"codex_native_compact_failed","detail":"Codex terminal is not running; reconnect first."}`.
  One attempt (11:24:44) differs: "Request failed on the runner; see the runner log for details" (`~/.omnigent/logs/runner/...`, name cut off in my grep).
- So it is a Codex-native lane whose Codex terminal is not running, and the server keeps asking the runner to compact it
  as context climbs (85 -> 91%). The server side is only forwarding the runner's refusal
  (`omnigent/server/routes/sessions.py`, "Session-change forward rejected"; `hub_handoff.py:178 write_up_and_compact` -> `hub.py:4223 compact`).
- Entry 2986 (11:51:09): the same lane "was interrupted during tool execution and produced no final answer": probably the same
  dead terminal.
- The Hub also sent the user's request to another lane at 11:52:58 (entry 2990, "omnigent · Roteamento e reinício do Hub"),
  so that lane may already be on it. Check it before duplicating work.

## Exact next steps
1. Confirm which lane `ee05b147...` is (lane_names.json, `GET /v1/sessions/ee05b147...` with the bearer token, never print it).
2. Read the runner log from the 11:24:44 line (`ls -t ~/.omnigent/logs/runner | head`, grep the session id) for why the Codex terminal died.
3. Check whether the host still has that lane's Codex terminal (cgroup `omnigent-host.service`; look for its codex process).
   Fix to propose: reconnect/relaunch the lane's terminal. Restarting omnigent-host.service needs the user's OK.
4. Separate code question: auto-compaction retried 4 times at 85/87/89/91% against a dead terminal. It could skip lanes whose terminal is
   not running, or say so once in the Hub instead of failing silently. Not built; propose to the user first.
5. Answer the user in the Hub (English, the translation layer handles Portuguese).

## Done earlier today (all committed in /home/nexus/wt/hub-links, merged into friendly-layer, server restarted 10-20 16:28)
- d6be05807 lanes get the user's exact words first, router text after as labelled context.
- 429a796ac the Hub asks where a message for a busy lane goes unless the router earned trust for that exact place (9 of last 10).
- 889c35463 buddy-style companion summaries; 602d470b4 files written between reports are linked.

## Open (needs the user's yes)
- Ask every ~5th busy-lane message even when a place is trusted (so corrections keep coming).
- Hub stall warning; report landing during a page reconnect (the 10-19 18:13 report that never showed on the user's screen; page rendering not checked).
- Push to the fork: 4dfcea35f 45a479377 cf2f6ff8e 9aca1e5c0 1eaa361ad 6c6429276 9b8b4e417 2b3a5b4b8 5b00afd60 602d470b4 889c35463 d6be05807 429a796ac.
- Lane's stored user turns repeat earlier turns plus system reminders (prompt composition glitch), not investigated.

## Traps
- friendly-layer has ~40 uncommitted files from other lanes. Never `git checkout -- file` there (I wiped a hunk of
  tests/server/test_hub_lanes.py once and restored it from a 10-19 snapshot). Copy a file before touching it.
- Deploy: commit in /home/nexus/wt/hub-links, `git rebase -q feat/friendly-layer`, then `git merge --ff-only` in friendly-layer,
  restart the server only (`XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart omnigent-server.service`); it does not stop lanes
  (they run under omnigent-host.service).
- Known failing test: `test_the_checkouts_a_lane_writes_in_are_found_from_its_paths` (empty /tmp/.git).
- No co-author trailers in commits. Open chat.db read-only.
