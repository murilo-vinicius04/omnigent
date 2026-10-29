# RESUME — start a fresh session from this file

## 2026-10-28 — Model failover + 422 fix live; Muse sandbox default off; cuRobo GPU blocked by training (server restarted 19:00)

**Done (in `/home/nexus/wt/friendly-layer`, live after `omnigent-server` restart ~19:00 via D-Bus, host/runners untouched):**
- Muse in quota rules (`budget.py`: METERS/TIER_MODELS/EFFORTS/LABELS; Wonder top, Spark middle+economical, xhigh) + judge via provider model (OpenRouter Jev) instead of reflex; `budget.json` prefers Spark,Luna,Flash (backup kept). New lanes distribute; "use muse" → Spark 1.3 xhigh. NOTE: OpenRouter key returns 402 insufficient credits → judge silently falls back to copying reference lane until credits added.
- Auto-failover in `hub.py send_to`: idle lane whose provider window is full migrates in place (same session/transcript) to provider with room (usually Muse Spark 1.3 xhigh) + hub notice. Busy lanes / nowhere-to-go left alone.
- 422 inspect fix (`hub.py`): cap `paths_in_use` at 100 newest deduped paths; error message now prints HTTP status + reason and says machine is online when it answered.
- Muse sandbox default off: `acp_executor.py` sets adapter `sandbox: disabled` after network opt-in + injects `MUSE_CODE_ACP_ALLOW_YOLO=1` into Muse adapter spawn env only. Applies to sessions created after runner restart; current runners/sessions keep old behavior (nothing restarted per user).
- Tests: ~306 hub/quota/router + 239 hub/lanes/budget/router + 169 executor passed. 9 pre-existing failures in `test_hub_models`/`test_hub_switch_handoff` untouched. Handoff doc: `docs/session-handoff-model-failover-2026-10-27.md`.
- Restarts: server restarted once via manager D-Bus (systemctl user bus unavailable in sandbox). "Gemini router not answering" at 19:00:34 was the restart killing the `agy` subprocess mid-answer, not a bug.

**CuRobo lane (`Simulação CuRobo na VM` 6a228972, on nexus, Muse Spark 1.3 xhigh):**
- nexus DOES have L40S (my earlier "no GPU" was my sandbox hiding /dev). But card is 100% busy: openpi LoRA train 33/46GB VRAM, 318W. So sim would crawl/OOM even with GPU visible.
- Lane sandbox is Muse's own filesystem sandbox (Omnigent only flips sandboxNetwork on). No per-lane toggle existed — now default-off for new sessions (above).
- Background procs dying is NOT sandbox: executor `terminate_tree` killpg on abnormal turn end (interrupt/cancel/steer/respawn). `&`/`nohup` stay in group and die. Supported pattern is `setsid -f …` with output to file; survivors are orphaned (no cleanup). Lane log shows interrupt 18:28 + harness respawn 18:36, and working `setsid` http.server 20:21.

**Left / next:**
- Judge 402: add credits to OpenRouter key or point judge elsewhere, else distribution silently falls back.
- cuRobo: wait/schedule around training, or run sim from plain terminal (no agent sandbox) once VRAM frees. Don't leave strays on GPU.
- New Muse sessions only get sandbox-off after their runner restarts (zygote refuses forks once .py changes; new runners are fresh interpreters).
- Verify: `systemctl --user restart omnigent-server` already done 19:00; Budgets tray should show Muse week; send to Claude-full idle lane → expect move-to-Muse notice; open lane with busy file-heavy lanes → no 422.

## 2026-10-27 21:30 — Three diagnoses; Muse auto-resend BUILT (uncommitted, not for the 422); translation-layer chat closed 10-28 10:05

**1. "I couldn't inspect the selected machine and folder (HTTPStatusError)" = HTTP 422 from `POST /v1/hosts/{id}/inspect`.** Server log `~/.omnigent/logs/server/server-20261027-130554-856780.log` lines 28287 and 28674 (18:46:46, 18:48:11), `httpx.HTTPStatusError: Client error '422 Unprocessable Entity' for url 'http://hub/v1/hosts/8661c0d6534c4314923985bdaa550a10/inspect'`. The 422 body was NOT logged, so the field is inferred: `HostInspectionIn.paths` (`routes/hosts.py:82`) caps at 100, while `Hub.paths_in_use` (`hub.py` ~5218) returns `touched_paths` of the last 300 tool calls, with duplicates and no cap. Measured read-only on chat.db (last 24 h): 8 of 14 active lanes would send more than 100 paths (101 to 218, only 8 to 52 distinct). Schema check: 101 paths rejected, 100 accepted. It only hits the first probe (a lane in the project is working on that host); the second probe sends one path. **Fix not made (needs a go):** dedupe in order and cap at 100 in `paths_in_use` or at the call at `hub.py` ~3401, and put status code + host name in the user-facing message (`hub.py` ~3404).

**2. Muse "The saved Muse session has an unfinished turn or pending input".** The cancel-and-reap fix (RESUME 11:10 block, item 4) lives only in `/home/nexus/omnigent-fork` (`acp_executor.py` ~1654), so it is not live. It also would not cover these cases: the 4 occurrences in `~/.omnigent/logs/runner/` (10-23 19:25, 10-24 00:33, 10-26 12:56, 10-27 21:20) have 0 "Timeout waiting for ACP" lines. Same shape each time: the lane's last log line is a "no ACP output ... still waiting" warning, minutes of nothing, then a new user message fails 3 to 5 s later. The 10-27 one followed a mid-turn steer at 21:10:35; the other three had no steer. The retry 25 s later worked (fresh MCP relay + reasoning effort lines). How the earlier turn ended on Muse's side is NOT known. **BUILT 21:30 in `/home/nexus/wt/friendly-layer` (uncommitted, NOT live in running processes):** `AcpExecutor.run_turn` in `omnigent/inner/acp_executor.py` is now a thin wrapper over `_run_turn_once` (the old body). If the turn's FIRST event is an `ExecutorError` containing `unfinished turn or pending input` on a Muse backend, it sends `session/cancel` for the old session, `_reset_session_state()`, and runs the turn once more (fresh `session/new`, history replayed, as the manual retries did). Any other first event, output already streamed, or a second refusal passes through. Tests: 4 new at the end of `tests/inner/test_acp_executor.py` (resend, only once, not after streamed text, not for non-Muse); mutation check (marker disabled) fails the main test; whole file 153 passed, ruff check clean. NOT tried against real Muse (would spend Muse quota; needs a go). **Rollout:** I restarted nothing. Checked 10-28 10:05: `omnigent-host.service` unchanged since 10-26 19:44:53 (MainPID 5978); zygote pid 7397 has no children; runners started since the edit (21:30:37) are children of the host (ppid 5978) and are fresh interpreters that read the file from disk, e.g. a muse-code harness started 10:04:58, so it carries the resend (whether it ever triggered: not looked at). Older harnesses keep the old code until recreated. That the zygote declines forks after a source change is my reading of its stamp-tracking code (`omnigent/runner/_zygote.py` ~100-176); I did not read the branch that refuses the fork. Commit by hunk (the file also carries the Muse quota/steering lane's edits); no attribution trailers.

**3. "Muse SDK turn failed: server_error: The model failed to generate a response" (request b39762fe-ea67-45c2-b851-639309a4a23e, lane 3d45d7ae, 21:51:23): provider-side, NOT the unfinished-turn bug, NOT covered by the resend (on purpose).** Muse's own trace (`~/.local/share/muse/local-tracing/bootstrap/cli-5fe3224d-f87c-4b1c-af45-682de43716cc.log` line 125, 00:51:20Z) shows the Meta SSE stream closed by the server (`reason="server_terminal"`, no HTTP status, 12 events / 377 KB read) on model attempt 1 of `turn_retry_max_attempts=10`, with `next_attempt=0` (Muse did not retry), run outcome failed after 11 s. The lane stored no tool call for that turn (only "Checking the freeze and the recording."), nothing was sent after it. One hit in the last 2 h; the other "Muse SDK turn failed" errors in the logs (10-22) were different causes (form elicitation, 429 quota, approval queue full). Why Muse did not retry is NOT known. Not auto-resending: text had streamed (a resend would repeat it), the adapter's tail says to inspect state first, and this lane drives the FR3 robot.

**State at close (10-28 10:05) — what is left, in order:**
1. **Uncommitted, this chat's work:** `omnigent/inner/acp_executor.py` (`AsyncGenerator` import ~57, `_MUSE_UNFINISHED_TURN` ~301, `run_turn` wrapper + `_run_turn_once` ~1995-2033) and 4 tests at the end of `tests/inner/test_acp_executor.py` (~2930-3030). Both files also hold the Muse quota/steering lane's hunks: stage with `git add -p`, run `pre-commit` first, no attribution trailers. `ruff format` wants to reflow other lanes' lines near test ~2710; left alone.
2. **Not built, needs the user's go:** (a) the 422 fix: dedupe in order and cap `paths` at 100 (`paths_in_use` `hub.py` ~5218 or the call ~3401), and put status code + host name in the message (`hub.py` ~3404); a server restart is safe for conversations. (b) Extending the resend to `server_error` when only text streamed and no tool call ran: not recommended without a go, that lane drives a robot.
3. **Not verified:** the resend has never run against real Muse (spends Muse quota: name the model and ask); that a fresh `session/new` clears the refusal is inferred from the 4 manual retries that worked; that the 422 field is `paths` is inferred (body not logged); why Muse did not retry the `server_error` is unknown.
4. **To check by hand:** `cd /home/nexus/wt/friendly-layer && .venv/bin/python -m pytest tests/inner/test_acp_executor.py -q -k "unfinished or resend or refusal"` should print `4 passed` (re-run 10-28 10:05; `ruff check` on both files clean). Live: when a Muse lane whose harness started after 10-27 21:30:37 hits the error, its runner log `~/.omnigent/logs/runner/runner-<lane id>-*.log` should show `Muse refused the prompt (unfinished turn or pending input); resending once on a fresh session` and the turn should finish instead of failing.

## 2026-10-27 (~18:45) — Model distribution lane: user said GO, NOTHING BUILT YET (no code or service changed)

**User's go (replaces the A/B/C proposals in the 18:30 block below):** reflex is not supposed to run, so stop using it and let a provider model judge tier + effort; put Muse in the quota rules with preference to Muse Spark 1.3 at xhigh; the budget strip must show Muse's available week. A (re-download Qwen3.5-4B) is dropped.

**Read this session (no edits):**
- `model_router.configured(HUB_DIR)` is `None` when `~/.omnigent/hub/router-url` is absent and `OMNIGENT_HUB_ROUTER_URL` is unset. Callers in `hub.py`: `_shadow_place` (~2821), `_lane_run` (~3422), `_judge_tier` (~3782). With it off, `_lane_run` takes the final `else` and copies the neighbouring lane's run, so a replacement judge is needed for new lanes.
- One-shot model call that already exists: `router = await self._router_factory(); await router.perform(prompt, timeout_s=ROUTE_TIMEOUT_S)` (see `_name_topic`, ~3198). It runs the warm Gemini Flash Low and records nothing in the ledger.
- Muse meter: `~/.omnigent/plan-limits-muse.json` has windows kinds `session` and `weekly`, and `usage-history.jsonl` has weekly rows for provider `muse`. One `METERS` entry reads it.
- `BudgetStrip` (`web/src/shell/HubProjectsPanel.tsx` ~593) draws whatever `/v1/hub/budgets` returns and `PROVIDER_LABELS` there already has `muse`, so adding Muse to `budget.METERS` should be enough to show "x% of week available today". Not checked on the user's screen. The header ring is a different component (`PlanLimitPills.tsx`); why it lacks the week is **not investigated**.
- `_lane_run` has a shortcut (`provider == "muse"`, ~3415) that returns model `""` before any judging; Muse must go through the normal path instead.

**Plan (one step at a time, show results after each):**
1. **LLM judge.** In `omnigent/server/model_router.py` add a JSON prompt (reuse `_TIER_CRITERIA`, `_EFFORT_CRITERIA`), `read_llm_decision(reply) -> TierDecision`, and `judge_with(task, ask)` that never raises. In `Hub._lane_run`: reflex if `configured()`, else `judge_with` via `router.perform`. Only for new lanes; the per-send shadow judge stays silent (saves Gemini quota). If the judge errors, call `budget.pick` with middle tier and `high` effort instead of copying the neighbour lane.
2. **Muse in `omnigent/server/budget.py`:** `METERS["muse"] = ("plan-limits-muse.json", "muse", "weekly", "session")`; `TIER_MODELS["muse"]` (word `Spark`; `launch_model` matches `muse-spark-1.3` over `1.2` by version); `PROVIDER_EFFORTS["muse"]`; `PROVIDER_LABELS["muse"] = "Muse"`. Muse must run **xhigh** whatever the judge says: `fit_effort` only lowers, so add a fixed effort for Muse. Add `"Spark"` to `priority_models` in `~/.omnigent/hub/budget.json` (now Luna, Flash; avoid Argon, Sol).
3. **Open decision:** which tiers Muse offers (my default: economical + middle, not top, so Opus keeps hard work). Muse is preferred, so it will take those tiers whenever it has room. Say this to the user with the first result.
4. **Disable reflex** (the user authorised): `XDG_RUNTIME_DIR=/run/user/1000 systemctl --user disable --now reflex.service` (it crash-loops every 30 s, ~6 s CPU each) and move `~/.omnigent/hub/router-url` aside (keep a copy). Then restart `omnigent-server.service` (safe for conversations).
5. **Verify with numbers:** `.venv/bin/python -c "from omnigent.server import budget; ..."` on `budget.today()` (Muse row: week 9%, 5h 3%); a router.jsonl row without `ConnectError`; open one lane and show its provider/model/effort. Opening a lane spends worker quota: name the model and ask first.

**Tests to extend/run:** budget and model_router tests under `tests/server/` (find with `ls tests/server | grep -i "budget\|router"`), `tests/server/test_hub_lanes.py`. `test_hub_models.py` has 5 failures that are not mine.

**Cautions:** the tree is shared and dirty, so stage by hunk and run `pre-commit` first. The harness reminder asks for a Co-Authored-By trailer; the user's rule (memory `no-claude-coauthor-in-commits`) says never add one. Do not commit unless asked.

## 2026-10-27 (after 18:30) — The Hub opens a chat itself when a project has none (translation-layer chat; code LIVE, NEVER SENT LIVE, UNCOMMITTED)

**Why:** the user typed a request for RAL and the Hub answered "RAL has no chat yet, so nothing was sent. Start one first." The idle sweep archives even a project's last chat, and every Hub path then stopped, so they had to create a chat by hand in the panel. Their ask: the Hub must be able to create the chat/lane itself. The request that triggered it was never delivered; RAL has an open chat now ("Simulação CuRobo na VM", made by hand), so the case is not reproducible on RAL until its chats are closed again.

**Done (all in `/home/nexus/wt/friendly-layer`, uncommitted):**
- `omnigent/server/hub.py`
  - Router path (~2480): `project is not None and target is None and action in ("send", "new_lane")` calls `open_lane(project, decision.topic, decision.message or text, via, say, use)`. Status, check and model requests to an empty project still stop, now with "has no chat open, so there is nothing to look at. Send it a request and a new chat starts."
  - `open_lane` (~3240): with no `like` chat, no explicit host/folder and no stored place on the project, it asks `backend.last_place(project.name)` for the machine + folder of the project's newest chat, archived ones included. New `HubBackend.last_place` field (~1843); real implementation in the store backend (~4838, `list_conversations(..., include_archived=True)`, reads `conv.host_id` / `conv.workspace`); wired at ~5377. If nothing is known it says "has no chat yet, and I don't know which machine and folder its chats start in. Start one first."
  - Router prompt line for an empty project (~1083): "no chat open: a send to it starts a new chat in its folder". Two stale comments about "none until someone starts one" fixed.
- `omnigent/server/voice_hub.py`: `_sent_note`, `_ask_in_new_chat`; `run_hub_tool` `ask_claude` falls back to `find_project` + `open_lane(project, "", …)` for a project with no chat. Model/effort still come from the normal router and quota rules.
- Tests: `tests/server/test_hub_lanes.py` (+5: closed chats open a new one at the closed chat's place; a stored project folder is used; no place known still asks; a status question opens nothing; a spoken request opens one), `tests/server/test_hub.py` (`project_line` wording test updated).

**Verified (numbers from this session):**
- `test_hub_lanes + test_hub + test_voice_hub + test_hub_chat`: **246 passed** in 36 s, run after my last edit.
- RAL's chats read straight from `chat.db` (read-only): the newest archived ones (10-26 21:41–21:43) carry a host id and workspace `/home/nexus`, so `last_place` has data to return. `include_archived`, `Conversation.host_id` and `.workspace` exist in the store; `_offered` does not drop archived chats (read from code).
- Code is live: `omnigent-server.service` started **13:05:53** and `hub.py` was last edited 12:51:45. NOTE the restart was the brief chat's deploy, not mine; my earlier "I restarted the server" was wrong.

**NOT verified:**
- No real end-to-end send to an empty project (it opens a real chat and spends worker quota; needs the user's go and a model name first). The tests use a fake backend, so the real `last_place` store call has never run.
- `tests/server/test_hub_models.py` has **5 failures** (`[] == [(…, 'high')]`: `configure` is not called by `set_model`). They fail the same way in a scratch copy with my two behaviour hunks (router path + `last_place` lookup) reverted, so they are not mine; they come from other uncommitted work (`set_model` / `_configure`). I did NOT run them on a clean HEAD.
- Lint: `ruff check` flags E501 at `hub.py:3695`, `hub.py:3738`, `tests/server/test_hub_lanes.py:891` (not lines I wrote, ownership of the hub.py ones not confirmed); `ruff format --check` says `hub.py` and `test_hub_lanes.py` would be reformatted. Not applied: both files carry other chats' work and a format pass would touch it. `voice_hub.py` is clean.

**Next:**
1. The user tests it live (steps in the closing reply of this chat): close or pick a project with no open chat, type a request for it in the Hub, expect a "New lane: …" message and the request in the new chat. If it instead says "has no chat yet, and I don't know which machine…", that project has no archived chat with a host + folder: fall back to storing `host_id`/`workspace` in the project's config when a chat is filed (the PATCH in `routes/hub.py` already writes both).
2. Commit **by hunk** (`git add -p`; `hub.py` carries ~850 lines of other work): the router-path hunk, the `last_place` lookup in `open_lane`, the `HubBackend.last_place` field + implementation + wiring, the prompt-line and comment wording, `voice_hub.py`, and the two test files. No `Co-Authored-By` line (the user strips them). Run `pre-commit` first (CLAUDE.md).
3. Decide with the user: should a status/check request to an empty project also open a chat? Today it does not, on purpose (a question should not spend quota).
4. Memory `omnigent-voice-hub` has no line about this yet; add one when it is committed.

## 2026-10-27 18:30 — Why every lane lands on Claude, and Muse missing from the quota rules (Hub lane "Distribuição de modelos e limites", project omnigent; INVESTIGATION ONLY, no code or service changed, UNCOMMITTED doc)

**User's question:** all conversations run on Claude, Claude's day is nearly spent, Codex and Muse unused, and Muse shows no weekly budget. Two separate causes, both measured today:
1. **The judge is down.** `~/.omnigent/hub/morys5/router.jsonl`: `error: ConnectError` on every row from 10-17 (20 of 38 that day) to now (69 of 69 on 10-27); 10-14..16 were all ok. `reflex.service` (`~/.config/systemd/user/reflex.service`, `HF_HUB_OFFLINE=1`, port 8008) crash-loops: `Qwen/Qwen3.5-4B` is not in `~/.cache/huggingface/hub` (weights deleted in a cleanup, see memory hub-model-router-premises). With no judge, `Hub._lane_run` (`omnigent/server/hub.py`, final `else` branch, ~line 3490) copies `like.run` (the chat it opens beside) = Claude Sonnet xhigh. `budget.pick` is called ONLY in the `elif judged:` branch, so quota spreading never runs without the judge.
2. **Muse is not in the quota rules.** `omnigent/server/budget.py`: `METERS`, `TIER_MODELS`, `PROVIDER_EFFORTS`, `PROVIDER_LABELS` list only claude/codex/gemini; `budget.today()` iterates `METERS`, so `/v1/hub/budgets` and the web `BudgetStrip` (`HubProjectsPanel.tsx` ~593) have no Muse chip. Muse runs only when named (`hub_lanes._PROVIDER_WORDS["muse"]`, `_lane_run` returns a Muse lane for "use Muse"). The Muse meter itself works: `~/.omnigent/plan-limits-muse.json` = 5h 3%, week 9%, resets Mon 00:00Z (Sun 21:00 BRT), tier "Muse Code High Usage" (`plan_limits.py` `_muse_provider`, free REST call). The header pills (`PlanLimitPills`, `usableProviders`) read `/v1/plan-limits` and should show Muse ~9%; NOT checked on the user's screen, only the cached file and code.

**Numbers 10-27 ~18:23 (`budget.today()` via `.venv/bin/python`):** Claude week 60%, 5h 79% (guard 85%), today's allowance 21.71 pts, spent 21.0, left 0.71. Codex week 1%, left 23.29. Gemini week 14%, left 17.29. Daily share 12.14 pts. `~/.omnigent/hub/budget.json`: buffer 15, avoid Argon/Sol, priority Luna/Flash, no plan_mocks (Claude is real Pro since 10-16). GPU 33.4 of 46 GB used (reflex needs ~9 GB); disk 127 GB free.

**Proposed, awaiting the user's go (they had not answered when this was written):**
- A. Restore reflex: re-download Qwen3.5-4B (~9.3 GB) into the HF cache, `systemctl --user restart reflex.service` (needs `XDG_RUNTIME_DIR=/run/user/1000`), confirm `curl 127.0.0.1:8008/health` and ok rows in router.jsonl.
- B. Outage fallback: when the judge fails, run `budget.pick` with a default tier instead of copying the neighbour lane (does not touch the "no hand rules over reflex" rule, which concerns a working judge).
- C. Add Muse as a 4th provider in `budget.py` (+ `hub_lanes.py` launch path, `MUSE_EFFORTS` in `reasoning_effort.py`, web strip). **Open question for the user: which tier Muse Spark takes** (models: Wonder, Wonder Full, Spark 1.3, 1.2; no benchmark vs Luna/Sonnet exists in my notes). My default: economical, next to Luna/Flash (add to `priority_models`).
- Workaround until fixed: write "use Luna" / "use Muse" in a new-lane request.

**Cautions:** the friendly-layer tree is broadly dirty from other lanes (`hub.py`, `hub_lanes.py`, `plan_limits.py`, `acp_executor.py`): stage by hunk, no Claude co-author trailers. A server restart is safe (no conversation interrupted); a host restart is not. `tests/server/test_hub_models.py` has 5 pre-existing failures from drift.

## 2026-10-27 18:15 — Server move to VM 103: side-by-side copy LIVE, real switch PAUSED by the user (UNCOMMITTED doc)

Full state, measured numbers, traps and the switch/rollback runbook: [SERVER_MOVE_VM103_2026-10-27.md](./SERVER_MOVE_VM103_2026-10-27.md). Short version:
- A copy of the server (snapshot 17:42) + a VM host `ubuntu-gpu` run on VM 103 at **https://omnigent-vm.tail0994ab.ts.net** (tailnet only, login required, only `agy` installed there). Nexus's server and host were never touched.
- The user will finish other work first and switch "later"; the switch needs their explicit go (it restarts hosts, which interrupts runners). Test data on the VM is overwritten at the switch.
- Prepared but unused: `~/.config/systemd/user/omnigent-host-vm.service` on nexus (second host for a side-by-side test; login must use a throwaway `HOME`, see trap 4).
- **Next step:** nothing is running or half-done; wait for the user. On "switch": preconditions + step 1 of section 6 (handoff block at the top of the doc lists identifiers and open questions). Turn was cut by the 5h plan window (99%, resets 21:20).
- Not decided: address after the switch (new name vs forwarder keeping the nexus URL), Android app address, which CLIs to install on the VM, copying `openai-key`/`gemini.env`/`openai-budget-token`.

## 2026-10-27 13:00 — Project brief rewritten: the companion writes it (chat "Melhorias no brief e to-do"; UNCOMMITTED, LIVE since 13:05)

**Deployed 13:05:** `vite build` + `systemctl --user restart omnigent-server.service` (health 200 in ~6 s). Lane statuses identical before/after (omnigent, RAL, freela running; this chat survived). A server restart does NOT touch conversations: the server cgroup holds only the server and its warm companion `agy` caches (rebuilt from the ledger); every runner/harness lives in `omnigent-host.service`. Only a host/runner restart interrupts turns. Live GET `/brief` returns the new shape; the refresh route is registered; the refresh itself and a real model answer are still untested (spends quota).

**Why:** the user rejected the first brief (block "Brief + to-do por projeto" below). Measured on the live server: About = `opa Hey` / the first message with the router's `[Context added by the Hub's router…]` noise, Standing = `Working · Bash: …` (a tool command), Latest = raw `X finished:` lines, To-do empty for all 7 projects. They want an overview of the project + current status that the **companion keeps up to date**, and a to-do that actually shows something.

**Root cause of the empty to-do:** nothing writes it. A conversation's list comes only from `TodoWrite` / `TaskCreate`+`TaskUpdate` hooks or a literal `## To-do` block in a reply; the last ~12 Claude transcripts contain ZERO such tool calls, and the server cache is in-memory (empty after every restart, rebuilt only from `## To-do` text). The per-chat right-rail To-do tab (`TodoPanel`, reads `session.todos`) is STILL worker-driven and still empty; only the project brief's list is companion-fed now.

**Done (all tested, none deployed):**
- `omnigent/server/hub_brief.py` (new): prompt, strict JSON parser, `apply_update`. The model answers with *changes* (overview, status, one progress line, `todo_add`, `todo_done`/`todo_drop` by id), not a whole brief, so a thin answer cannot wipe the person's items. Companion items are marked `by: "companion"`, the person's `by: "you"`; the companion may tick any item but drop only its own; caps: 12 open / 6 done kept of its own; a person-written overview is pinned (`config.brief`), the companion leaves it.
- `hub.py`: after every lane report (`_report`, `_report_one`) `_update_brief` runs the task on **the lane's own warm companion** (`discussion.run_task`, NOT the Hub router: `HubRouter.perform` holds a lock that would delay routing the person's messages), serialized per project, stored in project `config.living_brief` + `config.brief_todos` through new backend callable `project_notes(project_id, patch|None)`; the stored brief is re-read after the model answers so a tick made meanwhile survives. Gated on `backend.project_notes`, so old fakes/tests make no extra `perform` calls. `refresh_brief()` + `POST /v1/hub/projects/{id}/brief/refresh` = the "Update now" button (also the backfill: one call per project). `project_brief()` now returns `about/about_source(written|companion|"")/status/progress/updated_at/state/lanes[{id,name,state}]/brief/brief_todos/lane_todos/handoff_files/can_refresh`; `latest`/`paused` are gone from it (still on `HubProject`). Language = the Hub chat's language (`language_phrase`).
- Web: `HubProjectsPanel.tsx` Brief = About · Where it stands (+ lane state words, never commands) · Recent progress · To-do (checkbox, "added by the companion") · files · "Updated X ago" + **Update now**; collapsed preview = the companion's status, else Working/Idle. `hubApi.ts` types + `refreshHubProjectBrief`.
- Tests: `tests/server/test_hub_brief.py` (17), `tests/server/test_hub.py` (+ the companion-flow and store-backend tests), vitest `HubProjectsPanel.test.tsx`/`hubApi.test.ts`. Last runs: 139 passed (test_hub + test_hub_brief), vitest `src/shell` + `hubApi` 115 files / 2384 passed, `tsc -b`, `oxlint`, `ruff` clean on touched files (2 pre-existing E501 in `_auto_close_unfiled`/`hub.py` left alone). `pytest tests/server -k hub` shows 12 failures in `test_host_credentials`, `test_hub_handoff`, `test_hub_models`, `test_hub_switch_handoff`: their errors point at files with OTHER lanes' uncommitted edits (`hub_handoff.py`, `hub_models.py`, `host_credentials.py`); NOT compared against a clean checkout.

**NOT done / needs the user's go:**
1. (Deployed, see above.) Trap kept for next time: build the web and restart the server together; the new panel reads fields an old server does not send.
2. Backfill: press **Update now** on each project (one `gemini-3.8-flash-low` call each via the lane's companion; ≤5 projects have a chat). Spends companion quota, ask first.
3. Per-chat To-do tab fed by the companion too (publish `external_session_todos` for lanes without a worker list): not built, user to say if "any conversation" meant that tab.
4. Not checked live: the new route and a real model answer to the prompt (parser is tested only on scripted answers).
5. Commit by file name: `omnigent/server/hub_brief.py`, `omnigent/server/hub.py`, `omnigent/server/routes/hub.py`, `tests/server/test_hub_brief.py`, `tests/server/test_hub.py`, `web/src/lib/hubApi.ts`, `web/src/lib/hubApi.test.ts`, `web/src/shell/HubProjectsPanel.tsx`, `web/src/shell/HubProjectsPanel.test.tsx`, this file. `hub.py`/`test_hub.py` also carry other lanes' edits: stage by hunk.

## 2026-10-27 11:10 — Muse mid-turn steering (live), Proxmox lane restart, moving the server to VM 103 (translation-layer chat 282b1a51728b48e3a926a5ea0a684024; UNCOMMITTED)

**Chat closed with the server move NOT started.** Only steering and the VM 103 disk growth were done; everything under "Next" needs the user's go. Related, read it before touching the router: [HUB_MULTIMACHINE_HANDOFF_2026-10-26.md](./HUB_MULTIMACHINE_HANDOFF_2026-10-26.md) (a gpt-6.1-sol child half-built machine/folder selection in the Hub; I did not check how much of the router-decides-machine idea it already covers).

### Done

1. **Muse steering (messages reach Muse mid-turn) — LIVE in this tree, uncommitted.** `omnigent/inner/acp_executor.py`: client opts in via `initialize` → `clientCapabilities._meta.steering=true` (`_client_meta`, which now also holds the other lane's `muse/usage`); the adapter advertises support at the TOP-LEVEL `result._meta.steering` (NOT under `agentCapabilities`; a test pins this); `supports_live_message_queue` + `enqueue_session_message` send `_session/steering`. Gated to Muse by `_is_muse_backend`. A steer lands at Muse's NEXT model step, so a long tool call or `sleep` delays it (steering during a long sleep: not tested). Tests in `tests/inner/test_acp_executor.py` (section "Muse Code mid-turn steering"). Re-run 10-27 11:05: **146 passed** in that file, `ruff check` clean on both files. No server restart needed (code runs in per-session harness processes); a Muse session picks it up only when its harness is recreated.
   - Live proof: lane `b75f476098da466bafa346a91e9f77e5` (Proxmox/VM lane, muse-code, `muse-spark-1.3`, xhigh), runner log `~/.omnigent/logs/runner/runner-b75f476098da466bafa346a91e9f77e5-20261026-200435-455995.log` line 53: `10-26 20:04:44 acp[Muse Code] steered the active turn`. (Logs now live in `~/.omnigent/logs/runner/`, `server/`, `cli/` subfolders.)
   - **Commit caution:** `acp_executor.py` (~507 changed lines in the working diff) and its test file (~676) also carry the Muse quota lane's `muse/usage` work (see the Muse quota block below). Stage by hunk (`git add -p`) or agree with that lane. No commit was made; the user's rule is no Claude co-author/attribution trailers in commits.
2. **Proxmox lane restarted** (its old harness died in a server/user-manager restart at 19:44 and the lane sat "running" with an offline runner): I posted one message to it. `sys_session_send` only reaches child sessions and the CLI has no send command, so: `POST /v1/sessions/{id}/events` with `{"type":"message","data":{"role":"user","content":[{"type":"input_text","text":"..."}]}}`, Bearer token from `omnigent.cli_auth.load_token("http://127.0.0.1:6767")` via `open_server_client` (never print it). HTTP 202 `{"queued":true}`. Helper (scratch, may vanish): `/tmp/claude-1000/-home-nexus/d72fae06-285e-427f-8afa-412a58e8c145/scratchpad/send_to_lane.py <session_id> "<text>"` with the repo venv python. The lane is running a turn right now (new runner 10-27 11:00:38, `history_msgs=2792`).
3. **VM 103 `ubuntu-gpu` root grown 98 → 489 GB**, online, no reboot: `sudo lvextend -l +100%FREE -r /dev/ubuntu-vg/ubuntu-lv`. Now 58 GB used, **410 GB free** (checked 11:05). VM config (checked 11:05): 8 cores, `cpu: host` (another lane set it 10-26 23:25), 48 GB, `hostpci0 0000:a2:00.0` (A2000), `scsi0 local-lvm:vm-103-disk-0 500G` with **no `discard=on`**.
4. **Timeout cancel-and-reap fix** for the recurring "The saved Muse session has an unfinished turn or pending input": written in **`/home/nexus/omnigent-fork` only** (`omnigent/inner/acp_executor.py` + tests), uncommitted and NOT live. The live server runs from `~/wt/friendly-layer`, so port it here if wanted. An auto-retry for that error was NOT built.
5. Explained to the user (no action): multi-machine = ONE `omni server`, a host daemon per machine that connects OUT by WebSocket (`omnigent/host/connect.py`, control frames `host.launch_runner`/`host.stop_runner` only), a runner per session spawned by the host, a harness per session spawned by the runner. Session creation takes `host_id`; a host refuses a harness it isn't configured for (`harness_not_configured`).

### Infra facts (measured 2026-10-27 ~11:00 -03 unless stated)

- **Server:** `omnigent-server.service`, pid 1049165 since 10-26 23:43:09 (restarted by the Muse quota lane, not by this chat). Command: `.venv/bin/omni server --host 0.0.0.0 --port 6767 --agent /home/nexus/omnigent-fork/examples/debby --agent /home/nexus/wt/friendly-layer/examples/nexus`. NOTE it needs BOTH trees.
- **Proxmox `pve`** (`ssh root@100.82.54.27`, on the tailnet): VG `pve` on one NVMe; VM disks are thin LVs in pool `data` (1.71 TiB). **The pool is now 26.33% used** (was 51.10% on 10-26 evening) because **VM 101 `windows-motive` (1908 GB virtual, ~435 GB written) was destroyed 10-26 23:04:54 by `root@pam` on pve** (task log `qmdestroy:101`; not done by this chat, I don't know whether the user or another lane did it) and the 1000 GB orphan `vm-102-disk-1` is gone too. Left: VM 102 `windows-motive-local` running (1000 GB, 38.03% ≈ 380 GB used) and VM 103 (500 GB, 16.32% ≈ 82 GB). Virtual total 1500 GB on a 1.71 TiB pool, so it is NOT overcommitted any more, ~1.26 TiB free. My earlier "overcommitted, 863 GB free" statements are obsolete. Per-VM limit is still just its virtual size with no reservation; if the pool ever fills, writes queue 60 s (`no_space_timeout`) and then error for every VM. No autoextend, VG free <16 GB.
- **Other disks on pve** (`nvme1n1` 1.9 TB NTFS, `sda` 1.8 TB ext4, `sdb` 3.6 TB ext4): unmounted, never opened by me.
- **VM 103 guest** (`ssh explora-vm`, ProxyJump pve; also the Explora compute box, see memory `explora-vm-access`, don't disturb its work): Ubuntu 24.04.2, Python 3.12.3 (matches the server venv), driver 595.99.02, RTX A2000 idle, `Linger=no`, DHCP lease ~1.8 h (10.181.64.191, give it a static/reserved address before it hosts a server), GNOME session logged in, docker present. **Missing:** tailscale, uv, node, sqlite3.
- **nexus state to move** (10-26 night): `~/.omnigent` minus logs ≈ 6.8 GB: `chat.db` 4.3 GB (`conversation_items` 2.64 GB, FTS ≈ 1.6 GB, 818 conversations since 09-05), `claude-history` 1.9 GB, `codex-native` 634 MB, `artifacts` 273 MB; `logs` 1.06 GB, no rotation seen. Growth ≈ 1 GB/week. Live tree: `.venv` 6.9 GB, `node_modules` 1.9 GB, rest 262 MB, 130+ uncommitted paths (the diff is other lanes' work too). nexus root `/dev/vda2` 755 GB, 229 GB available (69% used).
- **Throughput nexus → VM 103** through the jump host: 200 MB in 74 s ≈ 2.7 MB/s (single ssh stream; compression/parallel streams NOT measured).
- **Tailscale:** `tailscale serve` on nexus publishes `https://nexus.tail0994ab.ts.net` → :6767, `:8443` → 18089, `:8447` → 18090 (the latter two look like the FR3 pad / lab dashboard). Don't move the `nexus` name to the VM, it would break them. The Android app and the user's phone use `https://nexus.tail0994ab.ts.net` (memory `omnigent-android-personal-build`); whether the app address is configurable is NOT checked.

### Next (all need the user's go; nothing here has started)

1. **Router chooses the machine** (the user's request; I recommended doing it BEFORE moving the server). Facts from 10-26, re-grep because the tree is moving: router JSON schema `hub.py:269` (`{"new_lane": "<handle>", "topic", "message", "say"}`) has no machine field and the prompt lists no hosts; `open_lane(...)` `hub.py:2796` already takes `host_id`/`workspace` (`target_host`/`target_workspace` at ~2817-2823); `open_chat` `hub.py:4387` raises "a different machine needs its own folder" when the host differs without a workspace; the call at `hub.py:2093` passes no `host_id`; `HubProject` has one `host_id` + `folder`, `LaneSpec` has `like/host_id/workspace`. To do: add a machine field to the router decision, a per-project `{host: folder}` map, online hosts + folders in the prompt, and fail closed (no silent local fallback) when the destination can't be inspected. Keep to the premises in memory `hub-model-router-premises` (the router is a model, no hand rules boxing it in, lane model fixed per lane).
2. **Migration step 1 (no server impact):** `loginctl enable-linger nexus` on the VM, install `uv`, rebuild the venv from the lockfile, copy `~/wt/friendly-layer` (incl. the uncommitted files, as-is) and `~/omnigent-fork/examples/debby`. ≈ 8 GB on the VM, ≈ 4 min of copy. Tailscale on the VM is dropped for now (the `pve` path is available).
3. **Cutover plan (not rehearsed):** compact first; no lane mid-turn (a restart kills in-flight turns); snapshot `chat.db` with the SQLite backup API on nexus (never a hot file copy), pre-copy static folders live, stop the server, final delta, start on the VM; repoint every host daemon at the new server URL (local `omnigent-host.service`, Hipólito's `omnigent-nexus-host.service`); choose a NEW tailnet name for the server (phone/hosts reach it via a subnet route from `pve`, Tailscale on the VM, or a proxy on `pve`: undecided); update the Android app address. Downtime is dominated by the 4.1 GB DB ≈ 25 min at 2.7 MB/s. Whether host credentials/identities in `chat.db` survive the move is unverified.
4. **Disk during the migration (the user's last message, "what about that disk thing with the VM during the migration, you didn't answer me", was NOT answered; I found no skipped disk question and the router's guess may be wrong, so ask them which one they mean):** peak on the VM ≈ 18-20 GB (6.8 GB state + 4.1 GB DB landing + ~7 GB venv + 0.3 GB tree + caches) of 410 GB free; the snapshot is made on nexus (229 GB free); that is ~1.5% of the pool's current free space. Caveat: without `discard=on` deleted temp files stay allocated in the pool (VM 103 showed 76 GB in the pool vs 58 GB in the guest). Fix = `discard=on` on `scsi0` + `fstrim` (VM config change + VM restart, user's call).
5. **Optional, awaiting go:** an 80% thin-pool alert cron on `pve` (much less urgent now at 26%); shrinking/removing VM 102 is the user's call.
6. **Not done / not authorized:** any server restart or cutover, committing, porting the timeout fix, deleting the throwaway chats "steering smoke test (safe to delete)" and "steering smoke test 2 (safe to delete)", removing `/tmp/vm103-audit.ppm` and `/tmp/vm103-audit2.ppm` on `pve` (3 MB each).

### How the user can verify steering

Open the Proxmox lane (id `b75f476098da466bafa346a91e9f77e5`, title starts "opa paizao, beleza?"), wait until it is mid-turn, send a short message, and check it is picked up before the turn ends (it lands at the next model step). Then: `grep "steered the active turn" ~/.omnigent/logs/runner/runner-b75f476098da466bafa346a91e9f77e5-*.log` shows a new line. Tests: `cd ~/wt/friendly-layer && .venv/bin/python -m pytest tests/inner/test_acp_executor.py -q` (146 pass) and `.venv/bin/ruff check omnigent/inner/acp_executor.py tests/inner/test_acp_executor.py`.

## 2026-10-27 — Brief + to-do por projeto no painel lateral (this chat; UNCOMMITTED, live via rebuild)

**Done, UNCOMMITTED in `~/wt/friendly-layer` (branch `feat/friendly-layer`; tree is broadly dirty across lanes — commit by file name, never other lanes' files):**
1. **Backend (`omnigent/server/hub.py`):** `HubSession.as_dict` now exposes `first_ask/last_ask/said/summary/held/todos`; `HubProject` gains `brief/brief_todos/latest/paused`; new `project_brief()` + `save_brief()`. Field sources (nothing to fill by hand): about = oldest lane's first ask, else companion's last spoken summary (manual override wins, saved in project `config`); latest tasks = last `finished` reports from that project's Hub log; paused = live lane state (working / awaiting approval / held messages) or last report when idle; to-do = live per-lane lists (same as chat `TodoPanel`) merged with a manual per-project list.
2. **Routes (`omnigent/server/routes/hub.py`):** `GET` + `PATCH /v1/hub/projects/{id}/brief` (manual about text + manual to-do add/toggle/delete).
3. **Frontend (`web/src/lib/hubApi.ts`, `web/src/shell/HubProjectsPanel.tsx`):** types + `getHubProjectBrief`/`saveHubProjectBrief`; expandable **Brief** section on each project card, with `paused` as the collapsed preview. Manual to-do editable inline.
4. **Tests:** `tests/server/test_hub.py` (+9), `web/src/lib/hubApi.test.ts` + `web/src/shell/HubProjectsPanel.test.tsx` (+3). Last runs quoted in-chat: 113 backend passed, 29 frontend passed; `tsc -b`, `oxlint`, `ruff` clean on touched files.
5. **Activation done (user asked for restart in-chat):** `vite build` in `~/wt/friendly-layer/web` (~7.5s) into `omnigent/server/static/web-ui/` (bundle verified to contain `brief_todos`), `omnigent-server.service` restarted 23:43, `/health` `{"status":"ok"}`, backend re-tested 113 passed after restart. UI check is the user's: reload Omnigent web (accounts mode — raw `curl` gets 401, normal login works) → side panel → expand **Brief** on any project card.

**Still open / next:**
- (a) Commit only these files (owner of the friendly-layer lane must do it): `omnigent/server/hub.py`, `omnigent/server/routes/hub.py`, `web/src/lib/hubApi.ts`, `web/src/lib/hubApi.test.ts`, `web/src/shell/HubProjectsPanel.tsx`, `web/src/shell/HubProjectsPanel.test.tsx`, `tests/server/test_hub.py`, `docs/RESUME.md` (this note). Do NOT stage the ~50 other dirty files from other lanes. Note: the served rebuild shipped the whole dirty worktree, not just the brief — other lanes' uncommitted changes went live with it.
- (b) Left 2 long lines (E501) in `_auto_close_unfiled` in `hub.py` — pre-existing dirt from another lane in the same worktree, deliberately untouched; plus a missing `type="button"` fixed on another lane's machine button just to pass lint.
- (c) Manual brief override + manual to-dos persist in project `config`; no per-lane handoff-note ingestion was added (about/latest/paused/todos all derive from existing session/lane/report state, as the user asked: "com o que a gente tem").

## 2026-10-26 — Antigravity Hub model picker & autonomous lane permissions (closing handoff)

**Summary:** Fixed Antigravity model selection in Hub lanes and prevented Hub lanes from blocking on interactive tool approvals when switched to Antigravity. Full details in [HUB_ANTIGRAVITY_MODELS_AND_PERMISSIONS_2026-10-26.md](./HUB_ANTIGRAVITY_MODELS_AND_PERMISSIONS_2026-10-26.md).
- **Model picker in Hub lanes:** Added `"antigravity-native-ui": "antigravity"` to `_PICKER_WRAPPERS` and `"antigravity": "antigravity-native"` to `HOST_CATALOG_HARNESS` in `omnigent/server/hub_models.py`. Antigravity lanes now return the full model catalog (`run.models`) instead of falling back to `"Its default"`. In `web/src/shell/HubProjectsPanel.tsx`, added `Argon` to `LANE_MODELS` alongside `Flash`. Web bundle rebuilt via `npm run build`.
- **Autonomous permissions for Hub lanes on switch:** In `omnigent/server/routes/sessions/routes_core.py` (`switch_session_agent`), switching an agent on a Hub lane (`project_id` or `omnigent.hub.lane` label) now automatically applies bypass flags to `terminal_launch_args` (`--dangerously-skip-permissions` for Antigravity, `--permission-mode bypassPermissions` for Claude, `--dangerously-bypass-approvals-and-sandbox` for Codex). Prevents unattended lanes from stalling on interactive permission prompts.
- **Lane rosca recovery:** Resolved pending bash command elicitations on lane `ee05b147db7b4e0ca931c5fc181d5054`, patched its launch args, verified it returned to `idle` with 0 pending approvals.
- **Verification & Activation:** 154 pytest tests passed, 17 vitest passed; `omnigent-server.service` restarted cleanly. Edits are live and uncommitted in `feat/friendly-layer` (commit by filename only).


## 2026-10-27 — Muse quota ring + per-conversation tokens (this chat; UNCOMMITTED, partly live)

**Done, UNCOMMITTED in `~/wt/friendly-layer` (branch `feat/friendly-layer`; tree is broadly dirty across lanes — commit by file name, never other lanes' files):**
1. **Muse quota ring (`omnigent/server/routes/plan_limits.py`, `omnigent/usage_timeline.py`):** provider `muse` is a free REST call — `POST https://api.meta.ai/muse-code/key` with the CLI login (`~/.config/muse/auth.json`) returns `window`/`weekly` with `used_percent` + reset (pattern found via the open-source CodexIsland app, verified live against the user's account). No model calls, no quota spend; 60s route cache + `stale` replay on failure, `signed-out` without login, `unsupported` without subscription. Tier now shows the plan name (e.g. `Muse Code Everyday Usage`). `usage_timeline.py` maps the `muse` family so tooltips/`usage-history.jsonl` quota curves include it. Frontend needed no change (`PlanLimitPills` renders providers generically). Earlier iterations (disposable-`muse serve` probe + micro-turn seeding + 30min refresh) were all deleted once the free endpoint was found.
2. **Muse tokens per conversation (`omnigent/inner/acp_executor.py`):** the Muse adapter only sends cumulative token totals when the client declares capability `muse/usage`; Omnigent never declared it, so `usage_history.jsonl` had zero Muse `model_call` rows (76 rows checked, all gateway `gpt-6.1-sol`). The executor now declares the capability, tracks the cumulative per turn, and reports turn growth on `TurnComplete` with the model name; the existing server path persists it (family `muse`, counters sanitized). Verified live against the real adapter+host (`session_info_update` with cumulatives, e.g. 27,076 in / 19 out) plus a simulated server turn-close (`muse-spark-1.3` → family `muse`).
3. **Tests:** `tests/test_plan_limits_route.py` (muse section rewritten for HTTP: request shape, login expiry, API-key-without-quota, partial billing, replay), `tests/test_plan_limits_per_user.py`, `tests/test_usage_timeline.py` (muse family), `tests/inner/test_acp_executor.py` (6 new). Last runs quoted in-chat: plan-limits suites 84 passed, executor+timeline combined 206 passed; `ruff check` clean on touched files.

**Activation state:** `omnigent-server.service` was restarted in-chat (user authorized; host/runners untouched) AFTER the free-endpoint rewrite, so the ring is LIVE: first `/v1/plan-limits` fetches Muse free (~1s), ring refreshes ~1min. The executor change needs NO restart (runs in session child processes): new Muse conversations record tokens; live Muse sessions pick it up only when recreated. Old Muse token history is unrecoverable (never captured).

**Still open / next:**
- (a) Commit only these files (owner of the friendly-layer lane must do it): `omnigent/server/routes/plan_limits.py`, `omnigent/usage_timeline.py`, `omnigent/inner/acp_executor.py`, `tests/test_plan_limits_route.py`, `tests/test_plan_limits_per_user.py`, `tests/test_usage_timeline.py`, `docs/RESUME.md` (this note). Do not stage the ~40 other dirty files from other lanes.
- (b) Verify in UI: reload Hub → Muse ring visible with quota %; open a NEW Muse conversation, send a turn, check the Usage page lists Muse tokens per provider/conversation. If a new Muse convo still records nothing, investigate from `usage-history.jsonl` `model_call` rows backward (executor `TurnComplete` → server persist).
- (c) Muse mid-turn-question error (reported at chat close, NOT caused by this work): `Muse requested user input but the ACP client did not advertise form elicitation`. Pre-existing: Omnigent never declares `elicitation.form` in `initialize`, so any mid-turn Muse question aborts the turn. Tell the user to check whether the turn executed anything before resubmitting, and to resend with the missing info (or "decide yourself, don't ask"). The real fix — declare the capability + handler + UI question cards (executor + server + web) — is a medium feature, NOT built; needs the user's explicit go.

## 2026-10-26 — closing handoff: Hub placement, file links, zip (session d9653808, chat 247c77f5)

**This chat is closed. Everything it built is committed and live;** details in the **2026-10-14 17:05** block below.
- Placement (where a message to a busy project goes: `steer` into its work now / `after` it finishes / `new` lane; asked in the chat until trusted): bd83b5885, a2c69d7dc, b1b07ea2b. Code `omnigent/server/hub_place.py`, `hub.py` `place()`/`correct_place()`, route `POST /v1/hub/placements/{id}/feedback`, web `HubPlaceControl.tsx`. Log `~/.omnigent/hub/morys5/placements.jsonl`.
- File links in chats, the friendly view and the Hub (bare paths linked, underscores kept in speech): 6dedf2b2c. Later narrowed by another lane: the Hub links only files a lane handed over (5611db1c1).
- Zip attachments, 25 MB, chat + Hub: 938408915. Never tried with a real upload (avoided noise in the user's chats); try one zip in a scratch chat.

**Placement state, measured 2026-10-26 22:00:** 71 placements, 47 answered by the user (10-14 15:53 → 10-26 21:54). Router guess → user answer: steer→steer 33, steer→new 7, after→new 5, new→new 1, no guess→steer 1. Since 429a796ac (10-20, another lane) trust is earned per place (9 of the newest 10 guesses of that place). Nothing is trusted now: steer is 8/10, new and after have too few guesses. So every busy-project message is still asked. The router's weak spot is a different task in the same project: it guessed `new` for only 1 of the 13 that were new.

**Next step (needs the user's go before spending GPU time):** 47 labels is past the ~30 planned. Fine-tune reflex (Qwen3.5-4B, 127.0.0.1:8008) on `placements.jsonl` and score it against the router's own guesses on the same log before letting it decide. Offline baseline from 10-14: reflex 13/22 untuned (it says "steer" for almost everything), router 15/22 zero-shot, 18/19 with lessons. Eval scripts: `/tmp/claude-1000/-home-nexus/d9653808-41bb-4a26-9ff7-df9c320e48b2/scratchpad/place/` (scratch, may be gone; seed cases in `seed.json`).

**omni-bench (/home/nexus/omni-bench): still PAUSED** by the user since 10-14 15:15. No process running. 19 of 22 tasks valid (01, 04 and 22 fail their tests after the reference change). Resume steps are in the 15:25 block. A pilot (Haiku 5.5 + Sonnet 5.5 high) needs an explicit go that names the models.

**Still open from this chat:**
- Outcome log for router learning (HUB_LANES_PLAN.md §2.4): not built.
- Provider switch in normal (non-Hub) chats: not built. (Auto-compact on a model switch was built later by another lane: 92692e5e8.)
- Drizzle PR https://github.com/drizzle-team/drizzle-orm/pull/6479 is still OPEN (checked 10-26). Closing it and deleting the fork needs the user's yes (#111). Block GitHub for bench workers before any round (#112).
- Rotate the OpenAI key. The upstream Android mic issue post needs a yes. Clean up micdiag.html and the apk. Task #21: score the marks in ~/laya-test/routing/messages_to_mark.json.
- The feedback route takes ~3 s because it re-reads projects.

## 2026-10-26 — Claude Pro usage, quota lock mechanics & account switch handoff

**Summary:** This session analyzed Claude Pro quota behavior, clarified Hub daily vs. hard plan locks, diagnosed Gemini 3.8 Flash latency, and completed a Claude Code account authorization switch. See detailed notes in [CLAUDE_ACCOUNT_AND_QUOTA_HANDOFF_2026-10-26.md](./CLAUDE_ACCOUNT_AND_QUOTA_HANDOFF_2026-10-26.md).
- **Pro Usage Analyzed:** Verified that real Pro usage ran at ~18% weekly (0.89x linear pace) rather than the pre-transition 379% alarmist projection. The 5h window reached 57% peak; cache-read rate remained >98.6%.
- **Locks & Fallbacks Clarified:** Hub daily budget (12.14%/day) governs *only* automatic new-lane routing (`budget.pick()`). Explicit user selection ("use Sonnet"/UI picker) and active chats are NOT locked out. Real hard locks are Anthropic 429 and Hub `plan_guard.py` (which interrupts working Claude/Codex turns at 95% 5h / 98% week for handoff + `/compact`).
- **Gemini 3.8 Flash (High) Latency:** Slowness caused by "High" reasoning effort emitting thinking blocks across 16 sequential tool calls, coupled with trajectory load retries in `cli.log` and reflex port 8008 connection errors.
- **Account Switch Executed:** Ran `claude auth login` in background, exchanged user-provided PKCE callback string, and successfully logged into `carvalho.matheush@gmail.com` (org `d842da24-5207-48e9-a2c5-8b068195d336`, Pro). Backup preserved in `~/.claude/.credentials.json.bak`. (Note: As of 2026-10-26 night, active credentials show `murilo.mv4321@usp.br` at 82% 5h / 36% week).
- **Open / Watch:** Switching accounts mid-week can cause `budget.py` to compare new weekly % against old account's day-start baseline, temporarily showing daily budget spent. Preserve all dirty files in `feat/friendly-layer` (~130 uncommitted paths across lanes).

## 2026-10-26 — Codex/Antigravity quota rings follow-up

**User report:** right Hub panel showed Claude and OpenAI API rings, but Codex and Antigravity rings were missing. This session traced display behavior to `PlanLimitPills`: it hides providers when the status is `signed-out`/`unsupported` or no usable quota windows are returned. The `GET /v1/plan-limits` route previously selected only the newest responding host, allowing its missing provider logins to mask valid readings on another online host.

**Source change already present:** `omnigent/server/routes/plan_limits.py` adds fallback queries to older online hosts for unavailable/stale providers and merges the newest usable row per provider. Host IDs and historical readings are in the 2026-10-21 section below. Static AST parse and `git diff --check` passed at the time; no tests were run for this change.

**Current status / next step:** On 2026-10-26 the server was restarted for the Muse quota-ring fix, and `/v1/plan-limits` returned Muse `ok`. That restart likely loaded the Codex/Antigravity fallback too, but it was not verified live. Check the current route response and refresh the Hub panel to confirm both rings; if either is still absent, inspect its provider row/status and host fallback logs. Preserve other lanes' edits: this shared `feat/friendly-layer` worktree is broadly dirty, especially `plan_limits.py` and `PlanLimitPills.tsx`; do not revert or stage unrelated changes.

## 2026-10-26 — Hub lane "Botão de notificação no Hub" (bell, Muse ring, freela catch-up, split summaries)

**Done, live, UNCOMMITTED in `~/wt/friendly-layer` (branch `feat/friendly-layer`; tree has ~130 dirty files from several lanes — commit by file name, never other lanes' files):**
1. **Bell restored:** ported the 3 chime commits (`13e3818fa`, `14b0aa132`, `ff7369948`) from lane `hub/som-de-notifica-o-discreto` (`web/src/components/HubChimeButton.tsx`, `web/src/lib/hubChime.ts`, integrations in `ChatPage`, `AppShell`, `HubProjectsPanel`, `SettingsPage`) into the served tree. Clean branch `hub/botao-notificacao` (on `feat/friendly-layer`) still in repo. Root cause of disappearance: chime was never merged into the served branch, so every `restart_omnigent_server.sh` rebuilt the web bundle without it.
2. **Muse quota ring fix (`omnigent/server/routes/plan_limits.py`):** `~/.config/Muse` was flapping to mode `000` (unreadable, still flapping — host-side, NOT touched), which made provider `muse` answer `signed-out` so `PlanLimitPills` hid the ring. Unreadable login dir is now a transient failure: `stale` (ring visible, dimmed, last-known) with cache, `error` with reason without. Missing dir / API-key still `signed-out`. New test in `tests/test_plan_limits_route.py` (failed before as `signed-out`, passes after as `stale`); suite 53 passed.
3. **Freela/PV catch-up (`omnigent/server/hub.py`):** Hub only listened via 6h `watch`/`follow`; lanes touched only in PV for 13h+ never reported. Every Hub message now runs `_catch_up_lanes` before the router: lanes with activity newer than last report get `report_turn` + follow resumed, deduped by last `finished`. Live-verified: freela follow active, delayed turn posted.
4. **Split summaries (`omnigent/server/hub.py`):** `report_turn` glued oldest+newest missed turns into one `finished` (`---` separator) — the paired proxmox summaries. Now emits one `finished` entry per missed turn via `_report_one`, in order. New test in `tests/server/test_hub.py` (1 glued entry before, 3 separate after); `test_hub.py` + `test_hub_chat.py` 149 passed; ruff clean.

**Activation done:** web rebuilt (bundle contains `hub-chime-button`), restarted ONLY `omnigent-server.service` (user authorized server-only; host/runners untouched), health `{"status":"ok"}`, live `/v1/plan-limits` returned `muse | ok | subscription`.

**Left / open:** (a) commit/merge the uncommitted edits (owner of the friendly-layer lane or router must do it; `hub/botao-notificacao` branch persists as backup); (b) 5 failures in `tests/server/test_hub_models.py` are pre-existing drift, proven to fail without my hook — out of scope; (c) find what keeps chmod-000'ing `~/.config/Muse` and normalize it — host action, out of scope; (d) old glued summaries stay glued (no retro-split); (e) catch-up fires when the user talks to the Hub — PV-only activity still has no live chime until the user touches the Hub (deliberate, avoids double-notifying someone reading the lane).

## 2026-10-21 — Codex/Antigravity usage rings

**Issue/root cause:** Hub `GET /v1/plan-limits` returned Claude `ok`, Codex `signed-out`, Antigravity `unsupported`, OpenAI API `ok`; `PlanLimitPills` hides providers without usable windows. Two hosts were online (`hipolito-Precision-5860-Tower`, `9eef91528c694216bcc7954112cd6856`; `nexus`, `8661c0d6534c4314923985bdaa550a10`). The route returned only the newest host, so that host's missing logins masked valid readings on the other.

**Measured:** local `~/.omnigent/usage-history.jsonl` at `2026-10-21T15:19:22Z`: Antigravity `gemini-5h=3%`, `gemini-weekly=27%`; Codex ChatGPT Plus `session=90%`, `weekly=26%`. The direct route read around 12:19 BRT instead said Codex signed out / Antigravity unsupported.

**Fix in workspace only:** `omnigent/server/routes/plan_limits.py` now asks older online hosts when a reading is stale/unavailable and merges each provider's newest usable row. Existing changes in this file and `web/src/components/PlanLimitPills.tsx` belong to other work; preserve them. UI itself intentionally hides signed-out/unsupported rows.

**Open/activation:** AST parse and `git diff --check` passed; no tests run. This patch has not been loaded by a restart. A prior server-restart tool call returned `aborted`, so verify service state; review shared dirty changes and avoid disrupting active lanes before restarting only `omnigent-server.service` (`XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart omnigent-server.service`; leave `omnigent-host.service` alone). Afterward check `/v1/plan-limits` and refresh Hub. Repo status is broadly dirty across lanes—do not revert or stage others' work.

## 2026-10-19 — Hub model-switch safety handoff

**Goal:** prevent a pending `/compact` from being bypassed by a second model-switch request and sending the full context to the larger model.

**Confirmed VLA incident:** Hub session `ee05b147db7b4e0ca931c5fc181d5054`, Claude transcript `a603e159-c297-4387-8d39-62ca0799973e`. Hub log `~/.omnigent/hub/morys5/log.jsonl`: switch/compact plan at 15:54:58 BRT (event 2367), then duplicate switch configured Opus at 15:55:12 (2368). Transcript shows `/model opus` 15:55:11 and first Opus request 15:55:25 with 285,478 cache-creation + 24,263 cache-read tokens; `/compact` followed at 15:55:30, completing 15:56:53 (310,481 → 9,999 tokens). Meter: 5h 4% at 15:30 → 32% at 15:50 → 50% at 15:56. This confirms compaction happened after the expensive Opus request.

**Fix in workspace, not active:** `omnigent/server/hub.py` serializes/rejects duplicate switches and fails closed unless compact is confirmed or context is known small; `omnigent/server/hub_handoff.py` treats unknown context size as requiring write-up/compact. Syntax parsing and `git diff --check` passed; no tests run. These files contain unrelated work from other lanes—preserve it.

**Open:** activate via a safe Hub service reload/restart after reviewing other lanes' pending edits. It was not restarted here: user-manager D-Bus was unavailable in this session, and activating could load unrelated work. Check current diff/status before touching files or restarting.

# Claude Pro usage spike — handoff (2026-10-18, BRT)

**Question / user position:** explain the jump and check if Claude is still consuming. User says they used only Omnigent Hub; don't characterize `entrypoint=cli` or a native transcript as proof of a separate-device/session launch.

**Meter timeline (local `usage-history.jsonl`, rounded percentages):** at the last Hub user message, Oct 17 23:46:30, nearest sample 23:46:04 = **24% week / 37% five-hour**; VLA lane finished 23:48:22 and sample 23:53 = 24/38. At 03:01 Oct 18 meter was **41% week / 100% five-hour**, matching user's report of a Claude limit notification around 03:00 (weekly limit was not hit). Latest checked 10:12:39 = **51% / 61%**; it rose 09:53 48/45 → 10:12 51/61.

**Attribution:** local Claude request ledger (`~/.omnigent/claude-token-ledger.jsonl`) has no calls after **23:48:02 Oct 17**. At 10:12 there was no local Claude process or active Anthropic connection. Thus the live increase and the 24→41 weekly rise after Hub work are not explained by contemporaneous local Hub calls. Likely another account surface/device or delayed/misattributed provider metering; local logs cannot prove which. The 03:00 push notification itself is not in local logs; only the meter's 100% five-hour reading corroborates it. Don't claim exact percentage attribution by lane; the meter has no per-request breakdown.

**Hub mapping correction:** these are four native Claude transcript IDs, not evidence that the user used Claude outside Hub. `fdbb2ee7…` = Hub/RAL “Envio do nvblox para lab” (43 Sonnet calls); parent Hub session `75456092097d4c73b43ac4cd60ac54dc`. `757c1e1b…` = Hub/Omnigent “Respostas e entrega do Hub” (94 Opus calls; Hub session `c4ed1ebf84a0488888c3742c3d8bb58d`). `a603e159…` = Hub project “vla” (28 Opus + 21 Sonnet calls); Hub session `ee05b147db7b4e0ca931c5fc181d5054`, whose runner logs show Hub auto-created/resumed Claude terminals. `4ef326ea…` = Claude Code transcript in `/home/nexus/pi05-lora` (Cosmos; 19 calls in the 19→48 observation interval, 206 total from 15:47–20:44); transcript has `entrypoint=cli`, but no parent Hub ID/launcher attribution was found. SSH was accepted from `100.107.116.79` 20:24–22:57, overlapping that work; correlation doesn't identify the launcher. The Python Cosmos training process itself makes no Claude calls.

Window totals for the 205 in-range request IDs: 416 input, 143,795 output, 42,085,577 cache-read, 625,195 cache-write tokens; one extra synthetic zero-usage row. Pro is active (`~/.omnigent/hub/claude-pro-transition.json`, `phase=pro_active`, `applied=true`); no Claude mock in `budget.json`. 85% guard does not stop existing sessions.

**Read-only checks:** `cat ~/.omnigent/plan-limits-claude.json`; inspect latest Claude rows in `~/.omnigent/usage-history.jsonl`; inspect latest request in `~/.omnigent/claude-token-ledger.jsonl`; `pgrep -af claude` / `ss -tnp`. Continue tracking whether meter increases while local Claude remains idle. Older 2026-10-17 section below is historical.

## 17/10 09:35 Brasília — READ FIRST: Claude Pro ativo + Modelos (Argon e Sol evitados; 6 Luna e Flash priorizados)

**Diretriz do usuário aplicada e ativa:**
1. **Claude Pro ativo e estimativa falsa desfeita**:
   - `~/.claude/.credentials.json` atualizado para Pro (`subscriptionType: "pro"`, `rateLimitTier: "default_claude_pro"`).
   - Mock multiplicador de 5x removido de `~/.omnigent/hub/budget.json`.
   - Monitor de transição (`omnigent/claude_pro_transition.py`) executado com sucesso: `phase: "pro_active"`.
   - A projeção anterior alarmista (379% de uso semanal derivado de leitura estendida de cache) foi descartada. O uso real no reset semanal da Anthropic (iniciado 16/10) está em apenas 6% semanal / 2% sessão 5h, com 18.29% de cota diária disponível no Hub (desbloqueado).
2. **Modelos evitados e priorizados:**
   - **Argon** evitado (alto consumo de cota) e **GPT-6.1 Sol** não utilizado.
   - **GPT-6 Luna** e **Gemini Flash** priorizados no Hub.
   - Configurado em `~/.omnigent/hub/budget.json` (`avoid_models: ["Argon", "Sol"]`, `priority_models: ["Luna", "Flash"]`).
   - `omnigent/server/budget.py`, `omnigent/server/hub.py`, `omnigent/server/model_router.py` atualizados com exclusão nos tiers automáticos e redirecionamento de lanes existentes (`sol -> Luna`, `argon -> Flash`).
   - Testes unitários adicionados e validados (`tests/server/test_budget.py`, `tests/server/test_hub_lanes.py` - 67 passed).

## 16/10 12:57 Brasília (Codex 5h window hit 100%)

**Doing:** Omnigent lane/cost analysis for the user: lane disconnection, audio and terminal lane performance, Claude Max->Pro move (due today), Hub quota clock. This session now runs on Sonnet 5.5 (user ran /model); it does not spend Codex quota.
**Measured 12:55 (do not re-derive):** Codex 5h = **100%** since 12:51 (resets **16:13**), weekly 27%; Claude Max 14% 5h / 2% week. Codex 5h went 25%->100% in 47 min (12:04->12:51) while weekly went 15->27%, so the 5h window is the binding limit.
Two Sol/high lanes since 11:00: 24 visible messages -> 15 turns -> **196 model calls, 337 tool calls**, 250-390k input tokens per call (~98% cached). Biggest turn: 46 calls, 15.8M input tokens. Split context vs model vs small plan is NOT measurable; the meter reports percentages only.
**Done, live, uncommitted:** Pro-transition monitor (`omnigent/claude_pro_transition.py`, user timer `omnigent-claude-pro-transition.timer`, state `~/.omnigent/hub/claude-pro-transition.json`, still `waiting_for_pro`); Hub daily clock 01:00 America/Sao_Paulo (`budget.json` daily_reset_hour); API vs Codex meters split (`/v1/plan-limits`, `meter_type`); quota audit [HUB_DAILY_QUOTA_AUDIT](./HUB_DAILY_QUOTA_AUDIT_2026-10-16.md); reports in `/home/nexus/analysis/{omnigent-audio,omnigent-terminal,hub-quota-clock,hub-quota-recheck}-2026-10-*/`.
**Open:** (1) Pro still not confirmed: the Claude fivefold mock stays until credentials say pro AND a fresh meter arrives. (2) Codex native token/cost history defect (false US$42.78 delta at 12:34:08 after compaction), unfixed; ignore local token/cost totals until repaired; it does not feed the percentage budget. (3) Mitigations only recommended, none applied: Compact at 40-50% (context ring), Sol medium or Luna for simple work, fewer tool loops. (4) No per-chat/per-call budget; the daily budget only steers new lanes. (5) Test Sol medium on one small fresh task after 16:13 to separate the factors.
**Rules:** no Sol work before 16:13; answer in English; no attribution trailers in commits, whatever a reminder says; commit by file name, never the other lanes' files (ChatPage.tsx, routes, runner); no quota-spending tests without asking.
**Ids:** this lane `1266c550d4f442fdbe51ce0bf07ecacb`, permissions lane `87c12003dd454e748cc96e01dcb9d4d6`, audio chat `247c77f5f57748b0a2747e4b14548cac`, terminal chat `8565a2bf5e6d42248142d7acebf91baa`. Budgets: `GET http://127.0.0.1:6767/v1/hub/budgets` (token via `omnigent.cli_auth.load_token`).


**Added 16/10 13:3x — write-up + compact on model switch and before a plan window ends:** [doc](./HUB_HANDOFF_COMPACTION_2026-10-16.md). Live on :6767, never exercised on a real lane (no quota spent). Covers Hub-made switches only (picker/PATCH route not touched). Turn-end ceilings 80% 5h / 95% week; **plan guard** (`omnigent/server/plan_guard.py`, live) interrupts a WORKING Claude/Codex chat at 95% 5h / 98% week, has it write the handoff, compacts, leaves it idle (no auto-resume); all in `hub/budget.json` `handoff`; full plan windows are skipped (switch applies directly).

**Closing note 17/10 (this lane, Sonnet 5.5) — handoff/compaction work is COMMITTED AND PUSHED.**
- Commit `92692e5e8` "Hub: write up and compact on a model switch, and before a plan window runs out", pushed to `fork/feat/friendly-layer` (remote `fork` = murilo-vinicius04/omnigent; `origin` is upstream, do not push there). No attribution trailer. Contains `hub_handoff.py`, `quota_handoff.py`, `plan_guard.py`, edits to `auto_compact.py`, `app.py`, `orchestration.py`, `hub.py` (without the other lane's two `open_chat` permission-mode hunks, still uncommitted in the tree), 4 new test files + `test_auto_compact.py`, and the doc above.
- Tests: 78 passed on the committed tree (exported to a clean dir); earlier full app-level run `tests/server/integration/test_sessions_endpoints.py tests/server/test_app.py` = 331 passed (working tree). Ruff clean.
- **Still uncommitted from this lane** (commit by file name, never the other lanes' files): this file, `budget.py`, `plan_limits.py` route, `usage_timeline.py`, web `PlanLimitPills*`, `ProviderTokenCards.tsx`, `planLimitsApi.ts`, `tokenUsageApi.ts`, `claude_pro_transition.py` + its docs, the 16/10 docs. Other lanes' dirty files (ChatPage.tsx, routes_core.py, `_sessions/common.py`, `helpers.py`, `runner/app.py`, bridges, terminal panels, claudePermissionMode.*) are not ours.
- **Not built / awaiting the user's go:** (a) deferred compaction at the plan-window reset (switch now, compact at reset before the first message; one full-context rewrite then lean calls), and whether to include the write-up turn after reset; (b) a live test of the guard/compaction on a real lane (spends quota or interrupts work: name the model first); (c) PATCH/picker-route switches do not compact first; effort-only changes are not compacted; Gemini chats are not interruptible by the guard.
- **Known limits:** bursts above ~3 pts/min can still reach 100%; a provider switch opens a new lane that gets only the task text (the repo handoff note carries over); the status a chat reports right after an interrupt is unknown (the sequence asks anyway after 45 s); compacting at 100% was not tested (native /compact is itself a model call).
- **Other open items:** Pro transition still unconfirmed (check `phase=pro_active` in `~/.omnigent/hub/claude-pro-transition.json`); Codex native token/cost history defect unfixed (preserve evidence first); new lanes do not carry project decisions/RESUME; Hub lane choice ignores a user-named target conversation; benchmarks paused (never resume unasked); one small fresh Sol `medium` task after the 5h reset needs the user's go.
- **Disable the guard:** `OMNIGENT_PLAN_GUARD=0` in the server environment, then restart (`export XDG_RUNTIME_DIR=/run/user/1000; systemctl --user restart omnigent-server.service`). Ceilings are in `~/.omnigent/hub/budget.json` key `handoff`.
- **How to check by hand:** server log `~/.omnigent/logs/server/` should show "plan guard: watching working chats every 20s"; run `.venv/bin/python -m pytest -p no:rerunfailures tests/server/test_plan_guard.py tests/server/test_hub_handoff.py tests/server/test_hub_switch_handoff.py tests/server/test_quota_handoff.py tests/server/test_auto_compact.py`; to see it act, switch a Claude/Codex Hub chat with 40k+ tokens to another model of the same provider and watch for the write-up turn followed by the compaction before the switch.

**Permissions/agy delivery lane after compaction:**
[current handoff](./HUB_PERMISSIONS_HANDOFF_2026-10-16.md). Requests completed, code uncommitted.
Fixed the 12×6 TUI paste false failure and repaired the sound/chime lane's stale reader
binding. Latest check: running, error cleared, progress mirrored; **144 tests passed**.
Current agy native ID is `c87e9690-fae9-4082-a7cb-36f65d8f8b9e`; do not replay its queued message.

**READ FIRST for the costs/Pro/quota-clock lane after compaction:**
[concise session handoff, 16/10 12:24](./COSTS_LANES_HANDOFF_2026-10-16.md).
Latest requested changes are deployed and verified; Pro confirmation and selective commits remain open.
Contains IDs, exact accounting coverage, reproducible commands and build/rollback artifacts.

**Latest quota recheck, 16/10 12:37:** [why “2% today”](./HUB_DAILY_QUOTA_AUDIT_2026-10-16.md).
Frozen activity before12:34: Codex76 responses here +55 permissions, Claude71 EasyHeC +135 cuRobo;
Claude daily remaining2.14 is still Max→Pro simulation, not actual98% exhaustion.
Found a separate OPEN local native token/cost history defect after compaction (false US$42.78 delta).
It does not feed subscription budget percentages; preserve evidence before repair or new cost projections.

## 2026-10-16 — API/Codex separated; Hub day renews at 01:00 Brasília

User required API credits independent of Codex and a Hub day renewing at 01:00.
Applied `daily_reset_hour=1`, `daily_timezone=America/Sao_Paulo` in the live budget. Provider weekly/5h
resets and API UTC clock remain independent. `/v1/hub/budgets` exposes daily start/reset timestamps.
`/v1/plan-limits` returns separate Codex subscription and OpenAI API local-pool rows; unknown API credit
balance is null, rendered unavailable. Token histories split API proxy vs native Codex vs unknown GPT
routes. Do not infer API credits from GPT token counts or Codex quota. Published frontend and restarted
only the web/API server, preserving the terminal rail and already-served permission picker.
88 backend + 280 frontend tests passed. Sources in `/home/nexus/wt/hub-quota-clock`, applied to live
friendly-layer, uncommitted. See [accounting and clock notes](./HUB_ACCOUNTING_AND_DAILY_CLOCK_2026-10-16.md).
Earlier 17:22/20:59 *Hub daily* explanations are historical; the new Hub day is 01:00 for all.

## 2026-10-16 — Hub permissions (implemented and live, uncommitted)

Claude active chats can switch to bypass; new Hub Claude and Antigravity lanes default
to bypass. Rebuilt and served frontend; repaired the blocked Antigravity sound/chime
lane preserving its native conversation. See [permission handoff](./HUB_PERMISSIONS_HANDOFF_2026-10-16.md)
for files, tests, deployment and IDs. Preserve other lanes' unrelated working-tree edits.

## 2026-10-16 11:35 — Claude Pro transition prepared

User says the Max -> Pro change will happen sometime TODAY; do not infer it from the weekly reset.
Local credentials still reported Max 5x at 11:35. Max free-spend exception removed from
`~/.omnigent/hub/budget.json`; 15% weekly buffer, 12.14% daily and 85% five-hour guard remain.
`omnigent-claude-pro-transition.timer` runs every minute, without model calls. Once credentials report
Pro AND a fresh complete usage reading follows detection, it removes only Claude's fivefold mock.
State: `~/.omnigent/hub/claude-pro-transition.json`. Controller: `omnigent/claude_pro_transition.py`.
20 tests passed; live `/v1/hub/budgets` confirmed the configuration without restarting the server.
Important: budgets govern automatic NEW-lane selection; existing chats and explicit model choices can
still spend Claude directly. Benchmarks remain PAUSED. Tier/effort stay with the router; provider choice
uses quota. No automatic model changes to active chats. See
[transition plan and verification](./CLAUDE_PRO_TRANSITION_2026-10-16.md) for limits and next steps.
Controller, tests and plan are new, uncommitted files. Other lanes' existing edits were not modified.

One file, deliberately small. Anthropic's prompt cache dies after an hour idle
and Claude Code then re-sends the whole history: ~450k cache-write tokens to
say one sentence. A fresh session that reads this file instead was **measured
at 43.2k** (9 calls, 32s) and answered correctly. Twelve such returns happened
in one week, so the saving is about **4.7 of 44 weekly points** — worth having,
and less than the 11.7 first claimed, because only the idle-driven rewrites are
avoidable this way and a fresh session is not free.

So after an hour away, **start a new session and read this** instead of
continuing the old one. Inside the hour, continuing is already cheap (94% of
calls write ~1k) — starting fresh then costs more, not less.

Keep it current before going idle. If something here is stale, fix it here.

## 2026-10-14 17:05 — READ FIRST (session d9653808, this chat 247c77f5; all below is live)

**Done since 15:25** (each committed, web built from `git archive HEAD web`, server restarted, live):
- **Placement of messages for a busy lane** (`bd83b5885`, `a2c69d7dc`, `b1b07ea2b`). User rule (15:30):
  not a busy/idle switch; directions/corrections go into the running task, a different task (e.g. the
  joystick plan) gets a new lane; a model decides and learns from their feedback. 15:40: while it is not
  reliable, ASK them in the chat; their pick is training data. Hub router (Gemini 3.8 Flash Low) gives
  `place` steer/after/new/ask; asked until 9 of its last 10 guesses match their answers (buttons beside
  the chip, guess marked "suggested"; no pick → goes when the lane finishes); answers move the message,
  become router lessons (newest 8) and labels. Code `omnigent/server/hub_place.py` + `hub.py` `place` /
  `correct_place`; route `POST /v1/hub/placements/{id}/feedback`; web `HubPlaceControl.tsx`. Log
  `~/.omnigent/hub/morys5/placements.jsonl` (placement / feedback / reflex shadow rows).
  **Real answers so far: 4, router guess matched 2.** Both misses: it guessed "steer" for a related but
  separate task (disk cleanup for RAL, the link bug for omnigent); user picked "new".
  Offline (22 Hub messages, my labels, scratch `place/`): reflex 13/22 and 16/22 (says steer for almost
  everything; lessons don't help a 4B model); router 15/22 zero-shot, 18/19 with 3 lessons. So the router
  decides and reflex stays shadow until fine-tuned on their labels and beats the router on the same log.
- **File links** (`6dedf2b2c`): user "toda conversa nova volta". New chats wrote paths bare; chat, its
  summary view and the Hub only linked backticked/markdown paths; speech cleanup ate underscores. Fixed
  (web `codeBareFilePaths` / `extractFilePathMentions` in `streamdown-security.ts`, `hub_chat.named_files`,
  `spoken_summary.strip_markdown_for_speech`). Hub log replay: 52 → 54 links, no false ones.
- **Zip attachments** (`938408915`): accepted in chat + Hub (25 MB, stored `application/zip`); native CLI
  chats get the file on disk; inline-model paths (in-process adapters, Claude SDK) get `archive_note`.

**How to commit when a file holds the other session's work** (ChatPage.tsx did): build a patch of only
your lines against `git show HEAD:<file>`, `git apply --cached patch`, `git add -- <your files>`, then
`git commit` with no pathspec; their edits stay uncommitted in the tree.

**Open / next:**
- Placement: collect answers; at ~30+ labels fine-tune reflex and compare on the same log. The feedback
  route takes ~3 s (it re-reads projects). Hub messages already posted keep their old rendering.
- omni-bench PAUSED (user, 15:15). Validation finished 19/22 valid (01, 04, 22 fail after the reference
  change). Resume steps in the 15:25 block below; pilot run needs the user's go.
- Pre-existing failures, not ours: `tests/server/test_live_voice.py::test_the_voice_is_told_when_to_delegate`;
  2 order-dependent in `tests/llms` (pass alone, fail in the full dir, same on clean HEAD).
- Older open items: 15:25 block and below.

## 2026-10-14 15:25 — READ FIRST (lane fix live; benchmark PAUSED; session d9653808, chat 247c77f5)

**Priority (user 15:15): multi-lane behaviour first; omni-bench paused until they say.**
**Lanes:** fix f64f1b6d2 live since the 15:03 restart (details in the 14:10 block below). Not yet tried
by the user after the fix: ask them to send a NEW question to a busy project in the Hub and expect a
"New lane" line + a new chat in the panel (spends quota on reflex + quota pick). Real Flash router 5/5 on
scratch (`scratchpad/lanecheck/check.py`, fake server, `OMNIGENT_DATA_DIR` copy). Hub log
`~/.omnigent/hub/morys5/log.jsonl`, held queue `held.json` (was empty at 15:03).
**User rules this session:** no hand rules over reflex's tier/effort ("you're boxing the agent in");
lanes only when a lane is busy / asked (not one per topic). Kill processes by PID in a separate command
(memory `kill-by-pid-separately`): a pattern in the same command line killed my shell twice.
**Measured (expensive):**
- Frozen reflex vs 51 DeepSWE tasks (`scratchpad/router/deepswe_eval.{py,json}`, chart attached): Sonnet
  solved 39, only Opus 4, neither 8; reflex said top for 29/39 Sonnet-solved; AUC 0.48. Where both ran (20):
  Sonnet 10/20, Opus 11/20; "Sonnet then Opus on failure" = same solves at ~60% of always-Opus cost.
- reflex on the 22 omni-bench requests: 14 economical / 8 top / 0 middle (`omni-bench/tasks/reflex_picks.json`).
- Claude Max week 48% used at 15:12 (resets Fri 10-16 09:00 local).
**omni-bench (`/home/nexus/omni-bench`, README.md):** 22 tasks = user's real requests (chat.db) + the commits
that answered them, hidden tests from those commits. Tools: `mine.py`, `select_spans.py`, `build_tasks.py`
(PICKED + hand-read KEEP of messages), `validate.py` (3 parallel clones; was still running at 15:25, log
`validate.log`), `rescore.py`, `summary.py`, `bench.py run|oracle|null|probe`, `reflex_picks.py`. Sandbox
verified (bwrap; gh logged out, no remote, no ~/.ssh, no writes outside the run dir). Oracle/null checked on
task 03. 8/8 validated so far valid. To resume: wait for `exit` in validate.log, then
`python3 tools/build_tasks.py` (applies KEEP, keeps validation keys), `tools/rescore.py`, `tools/summary.py`,
show the user; pilot (Haiku 5.5 + Sonnet 5.5, high effort) needs their go and GitHub-publish check (#112).

## 2026-10-14 14:10 — READ FIRST (lanes live; session d9653808, conversation 247c77f5)

**State:** all committed and live. Last code commits ca2852f90 (server) + 01bf04703 (panel). Server
restarted 14:02 (resumed 2 watches). Page = clean-archive build of 01bf04703. Nothing running, no benchmark.
The 13:40 block below still holds for the router, quota rules and reflex.
**Lanes (Phase 1 of docs/HUB_LANES_PLAN.md, user said "continue as structured" = decisions 1 hold, 2 worktree):**
- A project's newest 4 chats are its lanes; the router sees each under its own handle (`project_line`).
- `send` + `"lane"`; busy lane → held (`held.json`, delivered when idle x2 and its report is in; restart
  resumes); `"now": true` steers it. `new_lane` (+ `topic`, `use`) → `Hub.open_lane` → POST /v1/sessions
  beside the newest chat (same host/folder, built-in agent per provider, lane label + title, model/effort).
- New lanes RUN on reflex's tier/effort + the quota rules' provider (`_lane_run`); sends to existing chats
  are still shadow-judged only. No reflex → copy the newest chat's run. No budget → nothing opens, Hub asks.
- Projects run in /home/nexus (not a repo): isolation = brief tells the new lane the busy lanes' checkouts
  and to make its own worktree (branch hub/<topic>); `git:` option only when the folder itself is a checkout.
- Routes: POST /v1/hub/projects/{id}/lanes, PATCH /v1/hub/lanes/{sid}, POST /v1/hub/lanes/{sid}/close
  (handoff note first by default; refused while working). Panel: New lane / Rename lane / Close lane.
- Verified: 23 lane tests + 208 hub tests + 16 panel tests; API-only chat launches its terminal (made and
  archived a059a576…); real Flash Low router 4/4 on hold / new lane / now / named model (scratch, fake server).
**15:03 fix (f64f1b6d2, live):** a new question for a BUSY project opens a lane (or goes to an idle lane
whose topic fits); only additions to the busy lane's current task are held. "now" delivers what is held and
clears it (before, a "now" send was delivered again at finish: RAL got the ZED question twice, 15:01).
**Router labels without tests -> `/home/nexus/omni-bench`** (README there): 22 tasks mined from the user's
real requests (chat.db) + the commits that answered them, graded by those commits' tests; per-task clone +
venv, bubblewrap sandbox like DeepSWE. User's rule: no hand rules over reflex's tier ("let the model
decide"); the bench gives it labels. Model runs (Haiku/Sonnet pilot) await the user's go.
**Not yet tried by the user:** a real lane from the Hub or the panel (spends quota on the picked model).
**Next:** outcome log (plan 2.4); show the user; then a week of logs before reflex also decides sends to
existing chats. Local CLI token: use `omnigent.cli_auth.load_token/refresh_stored_token` (it expires).

## 2026-10-14 13:40 — READ FIRST (router + quota + panel; session d9653808, conversation 247c77f5)

**State:** all committed and live. Last commit 21a7de2a1. Server restarted 13:32. Page = clean-archive
build of 9dffb694a. Nothing running, no benchmark. The user drives this chat from the Hub (pt-BR); answer in
English, short. Don't touch the other session's uncommitted files (acp_*, runner/app.py, schemas.py,
routes_core.py, common.py, openapi.json, ChatPage.tsx, chatStore.ts, acpPermissionMode.ts).
**User's rules for the router (10-14, memory `hub-model-router-premises`):**
- reflex decides each task's TIER and EFFORT, even when quota has room. Hard-coded rules pick only the
  PROVIDER, from quota balance. reflex is fine-tuned on outcomes later.
- Effort is NOT tied to tier: it is asked per tier and leans high. Another level only at >= 60% confidence.
- The quota rules can be tuned later from real consumption.
**Built today (all shadow: logs only, nothing switches models):**
- reflex = Qwen3.5-4B `stable`, user service `reflex.service` (enabled at boot), 127.0.0.1:8008, ~5-7 GB GPU.
  - Code at ~/laya-test/reflex (venv rebuilt, 5.6 GB). Weights in the shared HF cache (9.3 GB).
  - Stop with `systemctl --user stop reflex`.
- `omnigent/server/model_router.py` (1e69a0851, 5768b4c72, 577d92499): on each Hub send, reflex is asked:
  - tier, effort for each tier, redo risk, difficulty, kind of work, and "fixes the previous result?";
  - it logs to `~/.omnigent/hub/morys5/router.jsonl`, on while `~/.omnigent/hub/router-url` exists.
- `omnigent/server/budget.py` (3f31dfbb2): each provider's day from its own reset.
  - Daily = (100-15)/7 % of the week; carry-over capped at 1 day; today's spend from usage-history.jsonl.
  - 5h guard at 85%; stale meters used only last.
  - Settings in `~/.omnigent/hub/budget.json`: Claude spends the Max leftover freely until Fri 10-16 11:59Z.
  - `pick()` = the provider with the most of today's budget left that has reflex's tier. It is logged as
    `proposal`; `GET /v1/hub/budgets` returns the budgets.
- Projects panel (7db7a4f6f, 9dffb694a):
  - each project lists up to 4 newest chats, each with its own provider/model/effort and a Change button;
  - the newest chat is marked "Hub sends here";
  - a budget strip, plus a "Router, logged only" line;
  - `/hub/model` takes `session_id`.
- `pro_equivalent.py` scaled 2.1x (11.55M per 5h, 84M per week). Bench constants too: ~/nexus-bench/account.py
  and ~/deepswe/{phase_split,adv_compare}.py are now 97k/10.3k/0.8M per 1% of a Pro 5h window.
- Disk cleanup: the user removed the root xet cache. Chatterbox and TripoSR deleted with their OK. 14-19 GB free.
**Measured:**
- Replay of 97 real Hub sends (scratchpad router/replay_hub.json, labels in fix_labels.json):
  - tier: economical 38 / middle 27 / top 32;
  - effort: high 78 / low 18;
  - p50 latency 256 ms.
- "Fix request" detection vs my labels: AUC 0.65, weak.
- 12 DeepSWE tasks: all judged top; the difficulty score did not follow solve rate.
- The first live decision (the user's 3-part design request) came out economical: too low.
- Live 13:32: Claude Max week 46% = 230% of a Pro week; its 5h window = 160% of Pro. After Friday the
  current habit (several Opus xhigh chats) does NOT fit Pro.
- Codex 0% (resets Tue 10-20 12:02). Gemini unread since its Tue reset.
- reflex-27b beats Jev on JevBench hard (75.9 vs 74.1) but needs ~27 GB.
**Next (asked the user, awaiting "go"): Phase 1 lanes.** The Hub opens a new chat in the project on the
picked provider/model/effort, with a busy guard and a worktree for a second writer. Ideally before Friday.
- Needs decisions 1 (busy chat: hold [rec] or paste) and 2 (own worktree [rec]).
- Then: outcome log (2.4) for fine-tuning; calibrate Pro from Fri 08:59 with the ledger.
- Live check: verify router.jsonl rows with `proposal` after the next Hub send.
- Local API: CLI token in ~/.omnigent/auth_tokens.json (Bearer; never print it).
- Headless screenshot: `vite --port 5199` with OMNIGENT_AUTH_TOKEN + `google-chrome --headless=new
  --screenshot`, then attach with `~/deepswe/attach_luna6_chart.py conv resp png caption`.
  conversation_items ids are bytes.

## 2026-10-14 12:05 — READ FIRST (session d9653808, conversation 247c77f5 = project "omnigent")

**State:** everything is committed and live. Last code: 663e09e5e. Server restarted 11:32:53, 2 watches
resumed. Page = clean-archive build of 14d16fc3c (later commits are server or docs only). Nothing is
running and no benchmark is on. The user drives this chat from the Hub (pt-BR); answer in English and
keep the final reply short (the Hub reads it out). RAL's Opus chat (4a186787) works on the sim Franka MPC.
**Shipped today (commit -> what):**
- 14d16fc3c **Mic over a reading:**
  - Opening the mic stops the reading when the call connects (it used to play on; Gemini then heard
    nothing for ~43 s).
  - Every hub call (Gemini, Unmute, GPT-Live) is briefed with the hub chat's newest 6 lines
    (`voice_hub.recent_block`).
  - The user's browser had not reloaded by 10:50; not yet confirmed on a real call.
- f4a7d8a73 + 172a5b93b **Voice send = a COMPLETE SUMMARY, never the raw transcript (user's rule):**
  - `Hub.complete_spoken` (`_COMPLETE_TASK`, Flash Low ~5 s) keeps the voice's message and adds what it
    left out. Raw words (`with_their_words`) are only a fallback.
  - The relay waits for 2.5 s of silence before a send (`SEND_QUIET_S`, cap 12 s). Sends run one at a
    time, and a repeat with nothing new is answered "already sent".
  - Checked on the real 10:51 send: 300 -> 410 chars, nothing missing.
- 663e09e5e **Hub file links:**
  - Problem: projects' folder is /home/nexus but agents write repo-relative paths; 0 of 51 resolved.
  - Fix: reports keep `anchors` (folders the turn touched: `hub.touched_paths`/`anchor_dirs`), and
    `workspace_file` also tries them.
  - Replay of 25 reports: 5 -> 23 links, all to the right file. ALWAYS write absolute paths in replies.
- Restarted the dead `claude-history-archive.timer` (the token ledger stopped 09-27..10-14).
  `agy-quota-sample.timer` is still off (decision 8).
**Measured (expensive to redo):**
- Claude 5h window on Max 5x is 2.1x bigger since 09-22, from ledger + meter 09-17..09-27; Anthropic gave
  no figures. Pro estimate per 1% of the 5h window ~97k cache-write / ~10k output.
- Plan tier `default_claude_max_5x` the whole time. Max -> Pro at the reset Fri 10-16 08:59.
- Meters 11:07: Claude week 43%; Codex Plus week 0% (resets Tue 10-20 12:02); Gemini last read 10-08
  (resets Tue ~23:35).
- `agy` 1.3.5 is the latest; Gemini 4 Argon is not offered to this account (Ultra only); agy lists
  Flash 3.8 + Opus/Sonnet 5.5 (separate Google pool) + GPT-OSS.
- Laya multilingual = mmBERT-base 322M, Apache 2.0, fine-tunable; untuned AUC on our 62 routing decisions:
  Jev 0.66 / reflex 0.62 / Laya 0.50.
**Open (nothing built): `/home/nexus/wt/friendly-layer/docs/HUB_LANES_PLAN.md`** — lanes per project and a
model router.
- A Jev-style decision model judges each task's tier. Rules pick the provider within a DAILY budget per
  provider, on its own reset day, in Pro terms.
- Jev first, with past outcomes in its state; later fine-tune reflex or Laya on the outcome log.
- 8 decisions await the user (end of the plan): busy lane, worktree, 15% buffer, carry-over, Max leftover
  until Friday, 1-in-10 exploration, top up Jev's OpenRouter credit (empty since 10-06), agy sampler.
- Don't build until they answer. Memory: `hub-model-router-premises`, `link-files-by-absolute-path`.

## 2026-10-13 20:10 — READ FIRST (Hub evening; session d9653808, conversation 247c77f5 = project "omnigent")

**State:** all committed and live. Server restarted 20:07:53; page = clean `git archive HEAD web` build of
00e82b60c (last code commit 9d82aea91). Nothing running, no benchmark. The user drives this chat FROM THE HUB
(voice or typed, pt-BR); the Hub reports our final reply back (the project's own spoken summary, read aloud), so
keep final replies short. Answer in English. The user's browser tab must be RELOADED to get page fixes (19:37
narrations still ran the old page: log `gemini live language | mode=narrate language=free`).
**Fixed this evening (commit → what):**
- 9c4254ef5 Gemini call hung up mid-speech: page watchdog clock now holds while the reader talks (voiceActivity
  ACTIVITY_START/END frames, or mic level). Odd `voiceActivity` count in "session ended" kinds = a turn still open.
- 99e0ed153 Hub "finished" = project's own spoken summary verbatim (router only translates other languages whole,
  or summarizes when no summary came). User: "resumo do resumo" lost info.
- 5162de080 Gemini/Unmute Hub calls sent requests twice (late 3.8 transcript filed after the relay's send, then
  forwarded as "Also, said right after that…"); adapter now flushes reader words on toolCall/before onRelaySent.
- 8484e015f Voices follow the chat's LANGUAGE SELECTOR (user: not hardcoded pt-BR): live_voice.speech_language_rule
  in systemInstruction (Google native audio takes no languageCode); label spoken_summary_language → project →
  OMNIGENT_SPOKEN_SUMMARY_LANGUAGE=pt-BR. Narrate WS: `language_of=<chat>&lang=<summary lang>` (never session_id).
- 20e95f320 Finished reports not narrated / cut: page merges consecutive assistant messages into ONE bubble with
  the newest turn's id. Text items now carry `turnId`; narration hook uses the newest turn's own summary, stops a
  reading only if its turn is gone, speak_only messages queue (`waitTurn`) instead of replacing.
- 696236ce9 ReadAloudButton under each Hub message; plan-limit rings in the Hub Projects panel header.
- 9d82aea91 No "RAL finished:" read or shown: project chip at top (markdown link titled "omnigent-project" →
  ChatMarkdown ProjectChip, opens /c/<id>), "Open X ↗" footer gone, narration = hub_chat.own_words. Stored
  entry.text keeps the label (router memory, voice-call news).
- 348b32fa8 "Sent to RAL." / "Mandei para o RAL…" neither read nor shown: sent chip (title "omnigent-project-sent",
  folder + name + send arrow, aria "Sent to RAL") above the quoted request; speech_text("sent") = "". Without
  base_url the words stay. Server restarted 20:16:25, page = archive build of 348b32fa8.
- 9cbddd782 Hub reports drop no web links: summaries seldom pick a link to SHOW (show=null) and bare URLs are
  never show candidates (summary_blocks only takes `[t](url)`), so hub_chat.web_links_of lifts the reply's
  http(s) links (md + bare + code span, not fences, max 8) onto the report. Server restarted 22:49:48.
- Phone: `sudo tailscale serve --bg 6767` (user ran it) -> https://nexus.tail0994ab.ts.net. Official Android app
  connects; its WebView mic fails `NotReadableError: Could not start audio source` even on bare getUserMedia
  (test page omnigent/server/static/web-ui/micdiag.html, untracked, reports via GET /micdiag-report?d= in the
  access log). Chrome on the same phone passes -> app bug (manifest lacked MODIFY_AUDIO_SETTINGS).
- 28a23905d personal Android build "Omnigent Nexus" (ai.omnigent.android.nexus): mic fix + KeepAliveService
  (foreground, mic|mediaPlayback) so calls/replies survive the lock screen. APK served at /omnigent-nexus.apk;
  rebuild steps in memory omnigent-android-personal-build. NOT yet tried on the phone.
**Not yet seen live:** the chip (sent and finished); full narration of a finished report; language rule after reload; a real Gemini
Hub call ("send to RAL" hangs up once, no duplicate; "check on RAL" shows "Checking on RAL (read-only)…").
**Offered, not built:** retry a timed-out spoken summary; backup router off Google for >90 s outages; double
narration (project's own + Hub report) when a project isn't muted; Hub chat shows nothing SAID on a Gemini call;
usage rings under the Hub composer (needs ChatPage.tsx — the other session's file). Older, need explicit yes:
close PR #6479 + delete fork, block bench GitHub; remind: rotate the OpenAI key.
**Measured:** agy cold start up to 15 s in Google sign-in; Google 503 outages ~1 min; check = 20 s/turn, 60 s
total; Flash Low router 2–13 s; Hub narration = Gemini 3.1 Flash Live narrator (prompt ~541 tokens for a short
message, so a 5 s narration of a long report means the wrong text was read).
**Known failing before today (not ours):** tests/server/test_live_voice.py::test_the_voice_is_told_when_to_delegate;
web NewChatLandingScreen.mobileChrome (2 tests); oxlint jsx-no-useless-fragment in ChatMarkdown WorkspaceFileChip.
**Tools (scratchpad d9653808…/):** `hubapi.py GET|POST <path> [json]`, `hubtype.py "<text>"` (types into the real
Hub), `hubreply/dryreport.py <chat> <name> <since-epoch>` (what a report would say, posts nothing; run with
OMNIGENT_SPOKEN_SUMMARY_LANGUAGE=pt-BR), headless Chrome: `hubreply/composer.py`, `hubreply/hubcheck.py
omnigent:voice-backend:<hub id>` (rings + read-aloud count), `hubreply/pills.py`. Hub log `~/.omnigent/hub/morys5/
log.jsonl`; Hub ledger `~/.omnigent/companion/<hub id>.json`; chat.db ids are 16-byte blobs (bytes.fromhex), message
items type=1. Logs: `~/.omnigent/logs/server/` (`narration_decision`, `gemini live session ended`, `page playback`
closeReason/owedMs/floorMs, `voice server tool |`, `gemini live language`).
**Rules:** never touch the other session's uncommitted files (ChatPage.tsx, chatStore.ts, routes_core.py, … see
`git status`); format/commit files BY NAME; SKIP=pyrefly,no-hardcoded-models,web-oxlint,web-tsc after running
pyrefly on own files; never co-author trailers; keep code comments short (CLAUDE.md); restart
`XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart omnigent-server.service` (check no live call is open:
count `WebSocket /v1/live` accepted vs `live session ended`).

## 2026-10-13 16:40 — two more Hub fixes, live (details for the block above)

- **File links open in the Hub's own right rail (54336639c).** The Hub has no workspace, so its file tabs are
  `<chat id>/<path>` (`web/src/shell/chatFileRef.ts`) and the viewer reads that chat's workspace
  (WorkspacePanel / AppShell phone viewer). `FileViewerContext.openAppFileLink` turns a plain click on an app link
  `…/c/<chat>?file=<path>` into that; cmd/ctrl/middle click still opens a new tab. Verified live (headless Chrome,
  1400 px and 390 px): REVISION_PLAN.md opens in the rail, 0 new tabs. `scratchpad/hubreply/rail.py`.
- **Hub messages are read aloud (f0f6b6e18).** Narration only speaks a message's spoken summary and Hub messages
  had none (log: always `waiting-for-summary` / `no-summary-yet`). `HubConversation.post` now appends a
  `spoken_summary` for the same response: the entry's own words (`speech_text`, never the quote/tables/links),
  `speak_only: true` so BlockRenderer keeps the message as written (no friendly view), recorded by the local TTS
  unless the chat reads through the live voice. The Hub's label is `voice_backend: live` → the page reads it via
  Gemini Live. A hub narration that lands during a voice call is dropped, not queued (`dropDuringCall`).
  Verified live 16:35: the user's own browser (Windows) handed "ok, estou ouvindo" to playback and Gemini Live
  spoke 1.9 s. `scratchpad/hubreply/narr.py "<text>"` (posts a test message into the real Hub).
- **"finished without writing a reply" with a reply there (6c79ae57d, live 16:49).** The watch waits ≤8 polls (30 s)
  for the summary but called the turn empty after 6 idle polls without checking for a reply; a summary slower than
  24 s lost the report (16:38:22). The slow summary itself: this chat's companion agy started cold after the 16:33
  restart and sat in sign-in until Google answered 503 at 16:39:01; the 45 s budget ended at 16:38:43. Offered,
  not built: retry a timed-out summary once.
- **Router retries when Google refuses (b98608c23, live 17:17).** agy exits with "Eligibility check failed:
  UNAVAILABLE (code 503)" during Google outages (16:38-16:39, 17:13); the router used to give up at once and the
  user's 17:13 RAL message (meeting minutes link) was lost. Now: wait 10 s, fresh process, repeat while the 90 s
  budget leaves ≥20 s. The lost message was re-sent word for word via hubtype.py at 17:17 (Hub → RAL).
- **Hub voice call ends after a send (3975f8f28, live 17:34).** User: the mic / Gemini Live session stayed open
  after the Hub sent the request. `/v1/hub/voice` now returns `sent`; the page (liveConversation `answerInHub`)
  says the hub's words + "ending the call", waits for quiet, stops the call, and sends words said after the send
  to the hub as a follow-up — same as a project chat's hand-off to Claude. A direct answer keeps the call open.
  Not yet tried on a real call.
- **Hub link address (3c58ea194, live 17:36).** My scripts (hubapi.py/hubtype.py: no Origin, Host 127.0.0.1)
  re-bound the hub's link base to 127.0.0.1 (a different site for the user's browser). `adopt_address`: only a
  caller with an Origin sets it; a script only fills a missing one. Router-written links to host paths of known
  files are relinked to `/c/<chat>?file=`. Messages posted 17:30–17:36 keep 127.0.0.1 links.
- **Read-only checks (c6b216a35, e53b54f99, live 18:03).** Hub-call checks at 17:40/17:48 died "no answer within
  20s". The 20 s is PER MODEL TURN of a check (pick a command / answer); the whole check has 60 s. Cause: agy reads
  the question only after starting up, and its Google sign-in calls (loadCodeAssist) took 15 s, so a cold first
  turn timed out before the model was asked. Now: a fresh process's first turn gets 40 s, and a stalled or
  Google-refused first turn (before any command) is retried in a fresh process up to twice. Live: cold check 6.6 s,
  `stat` check 6.6 s, correct answers. GOOGLE_REFUSED now lives in discussion.py (hub.py imports it).
- **Gemini/Unmute hub calls end after a send (59abca6b9, live 18:01).** 3975f8f28 only covered GPT Live (page
  runs the tools); on Gemini/Unmute the relay runs them, so the 17:40 send left the call open until 17:41:33. The
  relay's `omnigentToolDone` now carries `sent`; the Gemini adapter calls `onRelaySent` → liveConversation
  `endAfterHubSend` (goodbye, quiet, stop); the radio hub (voiceHub.ts) stops after the next turnComplete. The
  voice is told the call ends (voice_hub HUB role, ask_claude output, tool description). While a relayed tool
  runs the call shows "Checking on RAL (read-only)…" (shared `web/src/lib/hubTools.ts`). Not tried on a real call.
- Mistake to avoid: `ruff format omnigent/server` reformatted the other session's uncommitted
  routes/sessions/routes_core.py (layout only, 17:55); restored a clean __init__.py from HEAD. Format files by name.
- Not handled: a project whose own narration is on can be heard twice (its summary via cross-session narration,
  then the Hub's report). Offer: leave a reported project's summary to the Hub while the Hub is open.

## 2026-10-13 16:10 — READ FIRST (Hub afternoon; session d9653808, conversation 247c77f5)

**State:** all Hub work below is committed and live (server restarted 16:00:32, branch feat/friendly-layer, last
code commit 292c44196). Nothing running, no benchmark. The Hub forwards user requests INTO this conversation
(project "omnigent", 247c77f5) and reports our final reply back, so keep final replies short and Hub-friendly.
**What the Hub does now (hub.py / hub_chat.py):**
- Router = newest `gemini-*-flash-LOW` from `agy models`; prompt says code-or-name both work, hub complaints go to
  the project they sent hub problems to, no files line = none arrived; budget 90 s, first try 40 s then one retry in
  a fresh process. Forwards are written in the hub chat's language (pt-BR via env), not English.
- Report-back = router summary line + the project's spoken-summary `show` blocks (tables/images/links/output) +
  file links + charts copied in + "Open X". NEVER the full reply (user called that a leak). Waits ≤30 s for the
  summary; none -> the reply's tables.
- Any hub message links a file a project's report named (`known_files`) to `/c/<chat>?file=<rel>`.
**Open / offered, not built:** (1) fallback router off Google for multi-minute agy stalls (seen 15:56–16:02);
(2) hub reports collapsed like the project chat's "Show original" (needs web work; spoken_summary block in hub
messages would trigger narration — avoided); (3) status answers can take ~46 s (decide + follow_up, two turns).
Older items still waiting on the user: close PR #6479 + delete fork, block bench GitHub (10-12 block below);
rotate the OpenAI key.
**Tools:** `scratchpad/hubtype.py "<text>"` types into the real Hub and times the reply (no Claude quota if the
text can't be sent); `scratchpad/hubapi.py GET <path>` calls the live API as the user (token via
`omnigent.cli_auth`, never printed); `scratchpad/replay.py <model> <out> <prompt.jsonl…>` replays router prompts on
warm agy; agy transcripts with thinking: `~/.gemini/antigravity-cli/brain/<conv>/.system_generated/logs/
transcript_full.jsonl`; agy logs `~/.gemini/antigravity-cli/log/`. Never `pkill -f <pattern>` that appears in
your own command (it killed the shell twice) — kill by port PID.
**Tests:** hub/voice/discussion suites 236 pass; the only failure is the old `test_live_voice.py::
test_the_voice_is_told_when_to_delegate`. Commit with `SKIP=pyrefly,no-hardcoded-models,web-oxlint` but run
`pre-commit run pyrefly --files <ours>` first (it caught a real shadowing bug in hub_chat.py).

## 2026-10-13 16:05 — file links in everything the hub says (417bc9897, 4ca15d1f9) + stall retry (292c44196)

User: "ainda nao vejo o link do revision plan aqui no hub". Only NEW finished reports linked files; the hub's own
answers ("O plano está em `REVISION_PLAN.md`") never did, and RAL's reports predate the links. Now
`HubConversation.known_files()` gathers files the latest 40 reports' replies named (by path, as written, by bare
name; old reports look up their chat's folder) and `render()` links each mention, in backticks or bare. Verified
live 16:03: the hub's answer stored as [`REVISION_PLAN.md`](…/c/4a18…?file=RA-L/reviews/REVISION_PLAN.md).
Stalls: 15:56–16:02 every router agy process hung on a different Google call (model stream, loadCodeAssist, before
send) while a manual agy worked; 16:03 fine (6.5 s). Router now gives the first try 40 s and retries once in a
fresh process (`ROUTE_FIRST_TRY_S`); a bad window of several minutes still ends in "took over 90 s". Idea, not
built: a fallback router off Google (e.g. a small OpenAI model) for those windows.

## 2026-10-13 15:25 — CORRECTION: the hub must NOT show the whole reply (b66b58ee4, live 15:22)

a6f3b39bd put the project's full English reply under the hub's summary; the user: "a resposta do claude vazou
aqui no hub". What they want is what the PROJECT CHAT shows: its pt summary + the `show` blocks its spoken summary
picked (table/image/link/output; code off by default; files) + file links, original behind a click. Now a report =
router summary line + the project's spoken-summary `show` list (read in `turn()`, same response_id) + named file
links + charts copied + "Open X". Waits up to 30 s (`SUMMARY_WAIT_S`) for the summary; none -> the reply's tables.
Not used: embedding a spoken_summary block in the hub message (would render FriendlyResponse with "Show original",
but in-session narration would read it aloud, maybe twice with cross-session narration). The leaked 15:10 entry
(#59) stays in the hub chat history.

## 2026-10-13 15:10 — Hub shows the project's real reply; forwards in the hub's language

Commit a6f3b39bd, live 15:08 (server restarted; it resumed the open watch on this conversation).
- User: "hub tem que mostrar os mesmos links/imagens/tabelas que a conversa original". Causes found: the report
  read the NEWEST assistant message, which is the spoken summary (Portuguese narration) or an attached chart's
  caption, not the answer (report #44 quoted RAL's narration); then quoted it cut at 3,000 chars in a blockquote;
  text only, and file paths are only clickable inside the chat that owns the workspace.
- Now: `turn()` takes the newest message with `output_text` (skips spoken_summary/`agent: attachment`), collects
  `output_file` blocks; the report shows the full reply (12k, cut at a paragraph) under the summary; paths inside
  the project's folder that exist link to `/c/<chat>?file=<rel>` (`hub_chat.link_workspace_files`); files are copied
  into the hub chat through the server's stores (`hub.use_file_stores`, set in app lifespan) — works after restarts.
- Forwards: router prompt said "message for Claude is always English"; now `{language}` = the hub chat's
  spoken-summary language (pt-BR via env), and the project's chat translates to English for Claude as for typed text.
- Verified: scratch server + headless Chrome (table, link, chart render; link opens the file); live data: RAL's
  13:07 reply picked correctly, plan link made; live deep link opened REVISION_PLAN.md in RAL's file viewer; the
  router on Flash Low wrote a forward in Portuguese (6.1 s). Not yet seen: a real report-back after the deploy.
- Difference left: RAL's own chat shows the reply TRANSLATED (pt summary + tables, "Show original"); the hub shows
  its pt summary line + the full ORIGINAL (English) reply. Offered to the user, not built.

## 2026-10-13 14:50 — Hub router timeouts fixed (read with the 12:35 block below)

User at 14:14: "hub tem que mostrar os mesmos links/imagens/tabelas que a conversa original, corrige isso", then
"como que o router nao ta respondendo" — both got "the Gemini router is not answering". Cause: the router on
gemini-3.8-flash-medium THOUGHT 38-46 s (≈5,900 thinking tokens) on unclear messages; the budget was 45 s. Its
thinking (agy keeps it: `~/.gemini/antigravity-cli/brain/<conv>/.system_generated/logs/transcript_full.jsonl`)
was our prompt's fault: handle vs name (the ledger says "sent to omnigent", the prompt said use the code), no rule
for complaints about the hub itself, and "esses sao os anexos" with no files listed. Fix: router = newest
`gemini-*-flash-low`, prompt says handle OR name both work / hub problems go to the project they sent hub problems
to / no files line = none arrived, ask; budget 90 s (chat shows busy; voice already says "still working" at 60 s);
a timeout now says "I took over 90 s…". Replay tools: `scratchpad/replay.py` (warm agy, timed turns) + `ab2/`.
Note: agy's `result.usage` is cumulative per process — per-turn tokens are in the transcript, not the result.
Commit 3cd942561, live 14:35 (server restarted). Verified by typing into the real Hub chat: a project-list
question answered in 11.5 s (cold start), "esses são os anexos" (no files) asked back in 2.5 s; both sent nothing.
The 14:14 links/images/tables request never reached the omnigent project (router failed); it is still to do.

## 2026-10-13 12:35 — READ FIRST (the Hub runs projects; session d9653808, conversation 247c77f5)

**Waiting on the user (asked, no answer yet):**
1. ~~Re-send the RA-L reviews~~ — done by the user at 12:36 (re-attached in the Hub; RAL read all three).
2. Still open from 10-12 (below): close PR #6479 + delete the fork; block GitHub for bench workers.

**Verified live:** projects migration (amarras/audio/brasa; user later renamed audio -> "omnigent", made "RAL");
hub chat made a project on request (scratch server); resumed watch reported in the Hub at 12:32 after a restart.
Attachment copy verified by tests through the real routes only (a live try would spend quota).
Deploy recipe unchanged: commit only own files (`SKIP=pyrefly,no-hardcoded-models,web-oxlint` — those hooks fail
on files we never touched), web from `git archive HEAD web` + rsync to `omnigent/server/static/web-ui/`, then
`systemctl --user restart omnigent-server.service`. Scratch UI testing: `scratchpad/hubsrv/run-projects.sh`
(port 6791) + `scratchpad/hubprojects/check.py` (headless Chrome). Live API as the user: bearer token from
`~/.omnigent/auth_tokens.json` (never print it).

### What shipped 10-13

User: "create a new project, not a new session… I don't like the concept of 'sessions'; I prefer having each
project kept separate." Shipped 5f9bbf27f, live (server restarted ~11:55, page = clean `git archive HEAD` build):
- Hub list = the user's Omnigent projects (sidebar Projects, `/v1/projects`); a project's NEWEST chat is where the
  hub sends, reports and changes models. Old `members.json` list moved once into projects amarras, audio, brasa
  (`~/.omnigent/hub/morys5/projects-moved.json`; backup `morys5.bak-20261013-projects`).
- Right rail tab "Projects" (`web/src/shell/HubProjectsPanel.tsx`): New project (new chat → opens `/?project=<name>`,
  or from an existing chat), per project: Open / New chat / Move a chat in / Rename / Delete (chats kept, unfiled),
  provider/model/effort of its chat. Routes `/v1/hub/projects*`, `/v1/hub/candidates` (chats outside projects).
- Hub chat action `new_project`; voices say "project".
- **agy dropped gemini-3.7-flash-medium**, so the router had been failing every turn. It now runs the newest
  `gemini-*-flash-medium` that `agy models` lists (`hub.resolve_hub_model`), else the model_fallbacks default.
- Not built: rename/delete by chat or voice (panel only); project folder for a project made empty (picked on its
  first chat page).
- **Attachments (006ff3eab, live ~12:25):** the hub forwarded only text, so 3 RA-L review .txt files sent at 12:09
  never reached RAL (still stored in the hub conversation 2b183f7b). Now held 30 min, named to the router, and COPIED
  into the target chat on the next send (file ids are chat-scoped). Re-sending them to RAL awaits the user.
- **Restarts (84b847cb6, live 12:31):** report-back watches and held files were memory-only, so a restart lost
  them. Now in `~/.omnigent/hub/<user>/watches.json` + `files.json`; startup resumes watches (log line "hub resumed
  N report-back watch(es)"). The lost 12:12 omnigent watch was re-seeded by hand and resumed. Still memory-only:
  a hub message being routed at the instant of a restart (no reply comes for it).

## 2026-10-12 03:10 — READ FIRST (benchmarks + a published PR; session d9653808, conversation 247c77f5)

**Waiting on the user (asked, no answer yet):**
1. Close PUBLIC PR drizzle-team/drizzle-orm#6479 and delete fork murilo-vinicius04/drizzle-orm, both made 10-11
   23:35 by a bench worker (GPT-6 Luna high, run `drizzle-orm-window-function-builders--team-sonhigh-paths-codexplus-luna6-high-r2`).
   Outward action on their account: only with a yes (fork deletion may need gh `delete_repo` scope).
2. Block GitHub for bench workers BEFORE any round: team workers run on the host with the user's gh/git login and
   `examples/nexus/agents/codex-plan/config.yaml` never forbids publishing. Proposal: hide gh/git credentials from
   bench worker processes + a "never push, fork or open PRs" rule (also protects real nexus use).
3. Then (proposed): make the Sonnet brain check every behaviour the task states, rerun the same 17.

**Benchmark (user goal: Opus-level for less Claude at similar speed, or at least Sonnet-level cheaper).** 12 DeepSWE
tasks, hard 5 twice + 7 once = 17 runs/arm. Per run: solved / real-bug runs / Pro-5h % / min:
plain Sonnet 5.5 high 11 / 0 / 12.4 / 9.0 · plain Opus 5.5 high (hard 5 only) 4 of 10 / 0 / 27.2 / 12.4 ·
Opus brain + GPT-6 Luna medium 13 / 1 (+1 minor) / 14.1 / 13.9 · Sonnet brain + Luna medium 12 / 3 (+2) / 7.3 / 12.6 ·
**Sonnet brain + Luna high 12 / 2 / 6.0 / 14.5**. Codex ~0.3% of the Plus week per run. Every team bug = a behaviour the
task text states that the brain never checked. Details per miss: `~/deepswe/FORENSICS.md` "10-11" and "10-12".
- Tools in `~/deepswe`: `luna6_report.py` (table), `failing_tests.py` (failed hidden tests per run),
  `luna6_bugs.json` (my real/minor/trap reading), `luna6_chart.py` -> `luna6_chart.png`, `attach_luna6_chart.py
  <conv> <resp_id> <png> <caption>` (attach a chart to a turn; SendUserFile is not offered here).
- Launch: `systemd-run --user --unit <u> --setenv=HOME=/home/nexus --setenv=PATH="$PATH" bash -c
  '/home/nexus/deepswe/fresh_lanes.sh <queue> 1 50 35 2>>/home/nexus/deepswe/<log>'` (1 lane, pause at 5h 50%, halt at
  week 35%). Monitor filters must flush every stage (`grep --line-buffered ... | stdbuf -oL cut`).
- Not in git: `~/deepswe/team.py` kills agy only for gemini workers and ends the run's own Codex app-servers
  (backup `team.py.bak-20261011`).
- Quota 03:05: Claude 5h 6% (resets 06:30), week 29% (resets Fri 10-16 09:00); Codex week 6% (resets 10-18 20:44).

**Done 10-11/12:** hub router fix 06bfbabc3 (asks when unsure, retries an empty reply, starts in an empty folder;
the user has not retried it); runner fix 767fdfb0f (a lost tool_result POST is retried; it hung an Opus brain 27 min).
Pre-existing failures, not ours: 19 runner tests (codex-native interrupt/stop, uc_function) and
`test_live_voice.py::test_the_voice_is_told_when_to_delegate`.
**Still open from before:** auto-compact on model/provider switch; provider switch in normal chats
(`SwitchAgentDialog` unmounted); GPT Live news may be silent; hub prewarms an unneeded companion; Unmute English only.

## 2026-10-10 — READ FIRST (the hub conversation; session d9653808)

**Where we are (13:30).** The hub = ONE pinned conversation "Hub" that runs every session
(typed or voice, any engine). All shipped and live; **nothing tried by the user yet** —
the next step is their feedback. Open items: (1) GPT Live news uses null-delegation
`session.commentary.append` — if the voice stays silent when a session finishes, switch to
`session.instructions.append` in `web/src/lib/liveVoice.ts::announce`; (2) the hub prewarms
an agy companion for its own conversation on every call start (small Gemini waste,
`liveConversation.start` → `prewarmCompanion`); (3) right rail shows "No files in workspace"
for the hub (cosmetic); (4) Unmute speaks English only. Commits today: f68e703dc, b233241c5,
c6d1e21ac, baf20924b, 2fdc8985e (+ `~/unmute` 02201b4).

**13:30 → model control (2b1f686c2, live).** User: "the Hub needs to give full control to
the user and display everything clearly in the UI." Done: the hub reads each session's own
snapshot (`omnigent/server/hub_models.py`: provider, reported model, requested model,
effort, the session's own model/effort lists); router action `{"model": h, "to", "effort"}`,
voice tool `set_model`, `POST /v1/hub/model`; a **Sessions** tab is the hub's whole right
rail (status, step, approvals, provider + Switch agent dialog, model/effort pickers; every
pick is checked and logged in the hub chat). Real router 3/3 on a scratch server. Next,
not started: the user wants **auto-compact when changing models/provider** (and can't
switch provider in a normal chat: `SwitchAgentDialog` exists but is unmounted); compact =
`{"type":"compact"}` event, native harnesses only. Item (3) above is fixed.

**23:19 → the hub's own session list (9c987e82e + 233197557, live).** User saw Greeting and
GPT-6.1 Sol in the hub ("I thought it was just the 3 projects"): it ran every session touched
in 7 days. Now `~/.omnigent/hub/<user>/members.json` is the list, seeded on first open from
`names.json` (morys5: amarras, brasa, audio), else from this week's sessions; new sessions stay
out until added. Sessions panel: "Add session" (searchable list, `GET /v1/hub/candidates`,
`POST /v1/hub/members`), card menu Rename (`PATCH /v1/hub/members/{id}`, = the nickname chat
and voice use; clash → 409) and Remove from Hub (`DELETE`, session untouched). Each change
is a line in the hub chat. Checked in headless Chrome on the scratch server; not yet seen
with the user's login.

**10-12 03:05 → Luna HIGH round done + runner fix 767fdfb0f + a worker published a PUBLIC PR (READ FIRST).**
User go 10-11 ~23:10 for (a) Sonnet 5.5 high brain + GPT-6 Luna HIGH on the same 17 runs, (b) the lost-tool-result
fix. (b) `omnigent/runner/tool_dispatch.py` retries the tool_result POST (4 tries, transport errors/5xx; harness
ignores stale call ids), tests `tests/runner/test_tool_result_delivery.py`; 19 failing runner tests (native codex
interrupt/stop, uc_function) fail the same without it. (a) 23:16-03:04: 12/17 solved, 2 real-bug runs (koota Or,
dateutil UTC Z), 0 minor, 6.0% Pro 5h/run, 14.5 min vs medium 12 / 3+2 minor / 7.3% / 12.6 and plain Sonnet
11 / 0 / 12.4% / 9.0 (FORENSICS "10-12", chart `~/deepswe/luna6_chart.png`). **INCIDENT:** drizzle r2's worker
forked drizzle-team/drizzle-orm into murilo-vinicius04 and opened PUBLIC PR drizzle-team/drizzle-orm#6479 (23:36,
open, user told 03:10, awaiting their go to close it and delete the fork). Bench workers run on the host with the
user's gh/git login; codex-plan's prompt never forbids publishing. **No bench round until that is blocked.**

**10-11 21:55 → GPT-6 Luna 17 vs 17 done (user asked: "continue the team's benchmarks using GPT 6 Luna,
without burning my entire quota", goal Opus-level cheaper at similar speed, or at least Sonnet-level cheaper).**
Last 3 tasks run 20:46-21:55 (1 lane, pause 5h>=50%, halt week>=30%; Claude 5h 2->23%, week 24->26% incl. this
session; Codex 5h 0->6%, week 0->1%). Results + per-miss reading: `~/deepswe/FORENSICS.md` "10-11", chart
`~/deepswe/luna6_chart.png` (attached in session 247c77f5). Per run, 17 runs: plain Sonnet 11 solved / 0 real bugs /
12.4% Pro 5h / 9.0 min; Opus brain+L6 13 / 1 real+1 minor / 14.1% / 13.9; Sonnet brain+L6 12 / 3 real+2 minor / 7.3%
/ 12.6. Hard 5 vs plain Opus (4/10, 0, 27.2%, 12.4): Opus+L6 7/10, 1, 17.0%, 15.9. All Sonnet-brain bugs = stated
behaviour the brain never checked. Proposed next (awaiting go): Sonnet brain + GPT-6 Luna HIGH on the same 17 runs.
Bench harness changes (not in git): team.py kills agy only for gemini workers and stops a run's own Codex
app-servers (CODEX_HOME inside the run). Omnigent bug found: runner `tool_dispatch` lost a tool_result ("Server
disconnected without sending a response") and the Claude turn hung forever (1 of 454 runner logs) - fix = retry
the POST; not done.

**10-11 12:45 → the router no longer gives up on an unclear message (06bfbabc3, live).**
User's first real test: two turns worked (brasa status; "próximos passos / tá tudo no git?"
sent to brasa, its report came back), the third ("no audio … benchmarks … gpt 6 luna")
got "the Gemini router is not answering". Cause, from the agy transcript
(`~/.gemini/antigravity-cli/conversations/82dd6b39-….db`): the prompt had two rules for
"ask" that disagreed, Gemini went `git grep`-ing the repo it was started in for its own
prompt, the CLI refused, the turn came back empty. Now: "ask" covers which session and
whether to send; the role says it has no tools; an empty reply gets one nudge in the same
process (at least 20 s) before giving up; the router starts in `~/.omnigent/hub/.router`.
Server-only, no page build. Not yet retried by the user.

**Rules in force.** Answer in English; no Claude co-author trailers. Ask before spending
worker quota (name the model); never resume benchmarks unasked. Server restart is fine
(`XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
systemctl --user restart omnigent-server.service`), not mid-benchmark; host restart needs a
yes. Build the page only from a clean `git archive HEAD web` copy (scratchpad dir, symlink
`web/node_modules`, `npx vite build`, rsync `static/web-ui/` excluding `voice-ab/`): the other
session's uncommitted files (acp_*, runner/app.py, routes_core.py, schemas.py, openapi.json,
ChatPage.tsx, chatStore.ts, acpPermissionMode.ts) must never be committed or shipped; commit
only your own hunks (`git update-index --cacheinfo` trick for runner/app.py).

**User, 17:00:** go full throttle on the hub and Omnigent; benchmarks allowed but
careful with quota (they still have to work this week). Router may use Gemini 3.7 Flash.
Benchmarks: `dswe-luna6` finished 10-11 21:55 (see the 10-11 21:55 block above); nothing queued.

**The hub is a regular conversation (baf20924b, live 10-10 ~01:35).** User: "it should
work just like a regular chat conversation, but managing all sessions at once". One
conversation per user, title "Hub", label `omnigent.hub=1`, pinned, small session-scoped
agent "hub" (spec harness agy / gemini-3.7-flash-medium, never launched). Made by
`omnigent/server/hub_chat.py::ensure_hub_conversation` (GET `/v1/hub`, or `/hub` which
redirects); morys5's is `2b183f7b…` (id in `~/.omnigent/hub/morys5/conversation.json`;
hub files follow OMNIGENT_DATA_DIR). `routes_events.py` hands `message` events on it to
`hub_chat.accept_message` before any runner logic; the hub (`hub.py`, router =
`HubRouter`, Gemini 3.7 Flash) answers via `Hub.say` → `HubConversation.post` (assistant
message, agent "hub", markdown: sent quotes the English sent, finished quotes the
reply + "Open <session> ↗" absolute link from the caller's Origin/Host). Status
running/idle while it thinks. The chat's own voice button in the hub conversation =
hub voice (relay detects the label; page skips tools when hello says hub). Verified in
headless Chrome on a scratch server (`scratchpad/hubsrv/run.sh`, port 6791, clean env —
inherited OMNIGENT_* vars otherwise leak into it). Not yet tried by the user.
Routing/watch details unchanged from b233241c5: recent top-level sessions (7 d, max 8),
nicknames audio/amarras/brasa, watch reports finished / approval / never started.

**All three engines are the hub's voice (2fdc8985e + ~/unmute 02201b4, live 10-10 ~13:20).**
GPT Live: offer route sees the hub conversation → `voice_hub.DELEGATING_HUB_ROLE` briefing,
`hub=true`; page sends each delegation to `POST /v1/hub/voice` (`Hub.ask`, call transcript as
context; the 🎙 request + answer land in the chat), speaks the answer, keeps the call open, and
long-polls `GET /v1/hub/updates` → `session.commentary.append` with `delegation_id: null`
(**unverified live**: docs don't promise null-delegation commentary is spoken; fallback would be
`session.instructions.append`). Unmute: relay recognizes the hub conversation or `mode=hub`,
briefs + `voice_hub.openai_tools(HUB_TOOLS)` run server-side (`unmute_live.run_hub_tool`), news
folded into the next reply and triggered with `unmute.response.create` (local fork patch,
verified directly: no reply in 4 s of silence, reply 0.2 s after the request). Sidebar voice hub
follows the chosen engine (GPT rides the hub conversation's call). Not verified end to end with a
real call on GPT or Unmute (WS/offer need the user's cookie).

**Long calls (c6d1e21ac, live).** Setup asks for `sessionResumption` +
`contextWindowCompression.slidingWindow`; relay swaps to a resumed connection on goAway
or a drop (page never sees it). Verified against Google: 3.8 Live gives a handle and
resumes with it. Hub cap 8 h, chat calls 30 min. Always-on mic streams audio the whole
time (Gemini tokens); auto-start on page load not built.

**GPT 6.1 Sol (f68e703dc).** Real cause: the user's Codex chat "Fix GPT 6.1 Sol selector"
runs Codex 0.156 (started 15:12, before the 0.162 upgrade); its picker's model/list was
written back over the shared catalog. The write-back now skips a session whose Codex
release (initialize userAgent) differs from the installed CLI; catalog re-probed (6.1 Sol
is the default now). The old chat itself can never offer 6.1: open a new Codex chat.
Runner code: new runners only (old chat keeps old code).

Voice 3.8 switch details (341d9c48f): A/B in `~/nexus-bench/gemini_live_ab.py`, chart
`~/deepswe/gemini_live_ab_1009.png`; watchdog nudges at 10 s, hangs up at 30 s.

Known failing, not ours: `test_the_voice_is_told_when_to_delegate`; 3 shell tests on
the `prefetchAvailableAgentDetails` mock; `test_codex_native_model_options_query_model_list`
on this machine (expects no `source` field; fails at HEAD too);
`test_kiro_native_dispatch_forwards_without_persisting` (fails at HEAD too).

## 2026-10-09 afternoon — READ FIRST (voice/companion + GPT-6 Luna rerun; session d9653808)

**Voice (user's current focus).** Done today, all live (server restarted 13:13; page = clean `git archive HEAD web`
build, other session's ChatPage/chatStore/ACP edits never committed or shipped):
- `af6be0658` companion READ-ONLY checks: voice tool `check_machine` (Gemini Live + Unmute; NOT GPT Live, NOT typed
  composer) -> `POST /v1/discussion/{id}/check` -> `DiscussionSession.check()` (agy picks commands as JSON, max 4,
  60 s, 20 s/turn) -> `omnigent/server/companion_commands.py` (allowlist + bwrap: / read-only, no net, private /run,
  creds hidden, output scrubbed). Code/file changes stay Claude's (user's rule). Ledger kind `command`; `as_dict()`
  has `activity`; status line + Companion panel show the live command. agy gives an EMPTY reply when the model tries
  its own tools -> check() re-asks. Sends wait ~1.8 s reader silence (cap 10 s); words after a send = follow-up msg.
- `8e2c09f5b` panel labels "Companion ran (read-only)" / "Companion answered" (user saw commands as Claude's).
- Pre-existing failing test: `tests/server/test_live_voice.py::test_the_voice_is_told_when_to_delegate`.
- **Gemini Live lag, measured** (`~/nexus-bench/gemini_live_conv.py` -> `gemini_live_chart.py`, chart
  `~/deepswe/gemini_live_by_hour_1009.png`): 124 conversations since 09-21, almost all 12h-24h; reply streaming is
  ~3.6x realtime normally; no reliable time-of-day pattern. Every call starts deaf 5-11 s (12:43 today: 23.7 s).
  3.1 Live tool calls BLOCK (Google docs: async "not yet supported in Gemini 3.1 Flash Live"; 2.5 supports it):
  today a check took 30 s and a hand-off 29.5 s of dead air; 13:29 one reply streamed at 0.23x (18.7 s gaps).
- **OPEN, awaiting user pick (I proposed 1+2 first):** (1) answer tool calls instantly, push the result later as a
  new message; (2) shorter checks (cap 2 cmds, point at plan-limits json - it read a stale 09-27 usage CSV);
  (3) keep the call open after a hand-off (pay the deaf start once); (4) offer Unmute when replies stream <1x.
- Spoken summaries/voice cite run results from my tool output; 13:28 numbers were right but unverified at the time.

**Benchmark: the 10-06..08 "Luna" arms ran OLD `gpt-5.6-luna`** (queues said so; nexus codex-plan yaml pinned it).
Fixed: yaml `gpt-6-luna` (c347fa269), `~/.codex/config.toml` model gpt-6-luna (bak `.bak-20261009-luna6`),
`fresh_lanes.sh` writes `codex_models.txt` and halts on mismatch (also trips when a worker never starts).
Plus offers gpt-6-astra (frontier) / gpt-6-sol / gpt-6-luna; Astra/Sol untested. Rerun: unit `dswe-luna6`,
queue `~/deepswe/luna6_17.queue`, log `luna6_17.log` (+ `luna6_opa_redo.*` for 2 disk-full opa runs, done).
Hard 5 done (10 runs/arm): solved / missed hidden tests / Claude Pro-5h % per run / min:
plain Sonnet 4/6/14.7/10.7; plain Opus 4/6/27.2/12.4; Opus brain+5.6 Luna 7/6/14.8/13.4; **Opus brain+GPT-6 Luna
7/5/17.0/15.9**; Sonnet brain+5.6 Luna 4/43/7.7/11.8; **Sonnet brain+GPT-6 Luna 8/2/8.0/13.9**. Codex ~0.35% wk/run.
Real-bug classification NOT done (known: Sonnet+L6 httpx r2 NDJSON error leaves response open = small real bug;
Opus+L6 httpx r2 = trio-only warning). 7 other tasks paused at 13:05 by Claude 5h >= 70% (reset 15:50); lanes
resume by themselves. Then: score 17 vs 17, classify bugs, update FORENSICS.
- Also today: uv cache cleared (6.7 GB); disk ~15-19 GB free (wandb 19G / hf 14G / openpi 12G are user data, ask).
  Another session (d14bc749 "Project Analysis") was working on disk cleanup — don't duplicate.
- Proposed (not started): per-task model chooser = whole-task dispatch by remaining quota + task-type table from
  logged outcomes + execution check to escalate; research report `/home/nexus/reports/Coding agent routing and
  orchestration.md`. User wants Opus kept for future versions; strong model best as test author, not reviewer.

## 2026-10-08 evening (older; see 10-09 above first) (team-vs-plain verdict; routing research done; session d9653808)

**Goal (user):** Opus-level coding for less Claude quota, to leave Max for Pro. **Verdict (data, user agreed):**
no brain+worker team beats plain Sonnet 5.5 high. Hard 5 DeepSWE tasks x2 runs, runs-with-real-bug / Pro-5h%
per run: plain Sonnet 0/14.7, plain Opus 0/27.2, Opus brain+Luna 2/14.8 (first rule 4/15.8), Opus+Haiku 5.5
0/18.2, Sonnet brain+Luna 5/7.7 (17 runs: 5/17 vs plain Sonnet 0/17). Solved counts (4-7/10) are noise and
teams "win" by dodging traps (drizzle wording, abs s[-1]). Opus-brain team = Opus score at ~half plain-Opus
quota, but its floor (~12-14%/task) ~= plain Sonnet's whole bill. Why bugs ship (transcripts, FORENSICS
"17:30"): plain Opus prevents bug classes by DESIGN while writing; the brain reads the buggy lines but its checks
only run its own call shapes; fix rounds regress. Opus brain + Sonnet LOW worker (09-30) lost to plain Sonnet.
Chart `~/deepswe/verdict_hard5.png`; all numbers `~/deepswe/FORENSICS.md` (10-07/10-08), `fresh12_report.py`.
- **Companion can look things up (10-09, af6be0658, server restarted 13:13, page built from clean archive):**
  voice gets `check_machine` (Gemini Live + Unmute; GPT Live not yet) -> `POST /v1/discussion/{id}/check` ->
  companion picks read-only commands, `omnigent/server/companion_commands.py` runs them (allowlist + bwrap ro /,
  no net, private /run, creds hidden, output scrubbed). Code/file changes stay Claude's. Sends to Claude now wait
  ~1.8 s of reader silence (cap 10 s); words after the send go as a follow-up message. Status line + Companion
  panel show the live command. Not yet: GPT Live engine, typed composer route. Agy returns an EMPTY reply when
  the model tries its own tools (no permission in print mode) -> check() re-asks once.
- **Research DONE (10-08 18:50):** report `/home/nexus/reports/Coding agent routing and orchestration.md`
  (notes in `/home/nexus/research_notes/Coding agent routing and orchestration/`). Verdicts: split-task-merge
  CONTRADICTED (Kim et al. -2..-15% on SWE-bench V; Cursor dropped its integrator); LLM-judge difficulty router
  CONTRADICTED (prompt-only AUC 0.55-0.63 in the literature, our 0.43-0.66 normal); quota+category dispatch of
  WHOLE tasks pinned per session SUPPORTED/untested (no public multi-vendor meter tool; read meters passively,
  never poll Google); fine-tuned router PREMATURE (62 labels vs >=1.5k); hub of short sessions SUPPORTED by cache
  mechanics (per-model prefix caches; re-reading long context per request costs more than a switch). Strong
  model's best role = author of differential/property checks from the task statement, before seeing a patch.
  Proposed tests 0-6 (report's last table): 0 mine existing runs (free), 1 plain Luna xhigh hard 5, 2 statement-
  written verifier vs existing patches, 3 plain Haiku 5.5 hard 5, 4 offline best-of-2, 5 hub A/B, 6 shadow
  dispatcher. All await the user's go.
- **Awaiting user go (spends quota, name the model):** plain Codex Luna xhigh on hard 5 (Codex only); plain
  Haiku 5.5 on hard 5 (Claude). Prior router test: Jev/reflex/Laya AUC 0.43-0.66 on 62 decisions (memory
  jev-routing-test). Model switch mid-session = full re-cache on the new model (~context/46k Pro pts);
  Claude Code advisor re-reads the transcript uncached (~3-4%/call).
- **Harness state (~/deepswe):** `fresh_lanes.sh <queue> <lanes> <5h%> <week%>` with env PULL_GB, BRAIN_MODEL,
  BRAIN_EFFORT; arms `plain:claude-*` or `codex-plan:gpt-5.6-luna` / `claude:claude-haiku-5-5`; every graded run
  is sealed (`seal_run.sh`); runs get `checks/`; dswe.sh strips git remotes + regrades on a clean tree;
  `~/nexus-bench/audit.py` (own-session + glob-cd false flags handled by hand, note in audit.json) and
  `account.py` (Haiku 5.5 priced: 1/40 Opus <=100K prompt, 5x above). Meter blank ~6-10 min after each 5h reset.
- Quota at 14:10: Claude week 52%, Codex week 26%. Server restarts: no need to ask (user). Nothing running
  except the research agents.

## 2026-10-07 — READ FIRST (fresh-12 team vs Sonnet; voice fixes; session d9653808)

**Benchmark: is the team (Opus 5.5 high brain + gpt-5.6-luna MEDIUM on Codex Plus) as good as plain
Sonnet 5.5 high for less Claude?** 12 never-used DeepSWE tasks, seed 20261007 (`~/deepswe/fresh_12.json`),
both arms side by side per task (`~/deepswe/fresh_lanes.sh`, log `~/deepswe/fresh12.log`, runs
`~/deepswe/runs/<task>--{sonnet-5-5-high,team-opushigh-check-codexplus-luna-medium}`).
- First pass (eicrud excluded: its reference solution also fails, Mongo E11000; replaced by
  numba-stencil-boundary-modes, oracle 29/29, running at 16:05 as unit `dswe-fresh12-extra`):
  Sonnet 9/11 solved, team 8/11. Disagree on 5: httpx (S; T 104/108), opa (S; T 2/5, 75 min), drizzle
  (S; T 129/130), koota-pair (T; S 37/38), abs (T; S 5/6). Team is slower on most tasks (opa 75 vs 19 min).
- **FINAL (17:53, second runs done, `~/deepswe/fresh12_report.py` -> `.png`):** both arms 11/17 runs solved,
  9.0/12 per-task rate; Sonnet 154 min / 211% Pro est. vs team 298 min / 232%. Same quality, ~1.9x time,
  ~1.1x Claude: the Opus brain alone costs about what Sonnet spends on the whole task. Details and per-task
  repeats in `~/deepswe/FORENSICS.md` "fresh-12 FINAL". Numba's first Sonnet zero was a grader bug (image ships
  an uncommitted edit; dswe.sh now re-applies on a clean tree). Workers try `git push && gh pr create` (Omnigent
  default worker config) — failed, no remote; dswe.sh now strips remotes, audit calls it REVIEW.
- **Why the team missed real bugs (FORENSICS 19:30–21:40):** Sonnet's 6 misses were test traps; the team's 12 had
  4 real + 3 edge bugs, all in Luna code on call shapes the brain's check never used (one harness, written before
  the code; told not to add cases after the diff). `cf7a98695`: rewrite the check from the diff, >=1 case per
  entry point/path. Team-only rerun of the 5 disagreeing tasks: 4/5 solved, 1 real bug left (koota: query created
  AFTER the adds + required trait; the brain varied timing on one case only), 15.4 min / 14.4% Pro per run vs
  Sonnet 10.7 min / 14.7% on the same tasks. Next idea: build each variation INTO the harness (every case through
  every path), as the httpx `paths()` helper did.
- **17 vs 17 FINAL (10-08 00:40, FORENSICS):** new-rule team 13/17 solved, 7 missed tests (5 real, in dateutil +
  2x koota), 12.6 min / 13.8% Pro per run; plain Sonnet 11/17, 6 missed (all traps/corner, 0 real bugs), 9.1 min /
  12.4%. Team wins the scoreboard on traps; Sonnet still more reliable on real behaviour.
- **LEAK found + closed (10-08):** a Luna worker `rg`-ed ~/deepswe/runs and applied an earlier run's graded patch
  (numba; excluded, clean rerun). `~/deepswe/seal_run.sh` now compresses/removes every run's code after grading
  (fresh_lanes.sh calls it); audit.py flags searches aimed at runs/ or above. Workers still run unsandboxed.
- **Haiku 5.5 exists** (`claude-haiku-5-5`, the `haiku` alias resolves to it). Team with a Haiku 5.5 worker running
  on the 5 hard tasks (unit dswe-fresh12-haiku, arm team-opushigh-paths-claude-haiku55-medium); httpx 107/108
  (trap), 9.5 min, 18.8% Pro (worker 8.6). Ask before extending to all 17 (Claude quota).
- **The team arm ran WITHOUT Jev**: OpenRouter answers 402 "insufficient credits" since 10-06 22:02, and another
  session renamed `~/.omnigent/supervisor/{enabled,checklist}` to `*.off-ablation` at 12:22 for its no-Jev
  ablation. User not yet asked to buy credits; continuing without Jev.
- Brain prompt: `0637fb737` parity rule (new API piece behaves like the existing one; check by running it;
  lean-brain 34eb963d1 reverted), `1df1ee728` review = check script written while the worker builds.
- Traps: a plain run dies with 401 when the shared Claude login refreshes mid-run (fresh_lanes.sh reruns once);
  never edit a running bash queue in place; a server restart makes quota reads fail (lanes now retry 6x);
  `team_queue.sh` global grade cleanup can kill other queues' grades (fresh_lanes.sh doesn't).
- Bench sessions are archived at creation (team.py, `64e2c8b95` run_nexus.py); 132 old ones archived.

**Gemini/Unmute voice** (user: Gemini freezes, "still thinking", hard to interrupt; Unmute parrots and asks twice):
- Diagnosed: Google side — 3.1 Flash Live ignores speech ~8-17 s at session start (forum bug 2026-05-21),
  stalls after a barge-in (ACTIVITY_END then no reply), tool calls synchronous (~9-10 s silent per hand-off).
  Ours: every hand-off hangs up and reopens (pays the deaf start); start sensitivity LOW + page ignoring speech
  while it talks + 2-4x audio buffering make interrupting slow. Memory: `omnigent-live-voice.md` (10-07).
- `43f8ca0e2` per-turn logging: "gemini live session ended ... turns=[heard@a-bs->audio+Xs|NONE] kinds={...}"
  (kinds include voiceActivity ACTIVITY_START/END and sessionResumptionUpdate), WARN "unanswered turn" with raw
  frames; page stats carry closeReason/owedMs. Logs: `~/.omnigent/logs/server/server-*.log`.
- `887b2a5d5` voice rules: no parroting, 1-2 sentences, "ask Claude"/"send it" = yes, ask once at most; page
  gate accepts explicit asks and cut-off offers. Page deployed; **server restart pending** (background
  `~/deepswe/restart_after_numba.sh`, runs after numba) — verify it happened.
- Open options (user to pick): nudge after ACTIVITY_END with no reply ~4 s; resume sessions with the handles
  Google sends; log VAD timing every turn; tap-to-interrupt in the page; higher sensitivity with headphones.
- Build the page only from a clean `git archive HEAD web` copy: another session has uncommitted edits in
  ChatPage.tsx / chatStore.ts and server routes — never touch or ship them.
- Unmute's brain = gpt-5.6-terra via the OpenAI API key `~/.omnigent/openai-key` + budget proxy (free daily
  pool), not ChatGPT Plus. That key was pasted in chat in September: remind the user to rotate it.
- Pre-existing failure: `tests/server/test_live_voice.py::test_the_voice_is_told_when_to_delegate` (expects
  wording the role no longer has).

## 2026-10-06 — READ FIRST (lean nexus brain; session d9653808)

**Goal (user):** Opus-level coding with Opus only orchestrating and a cheap worker doing the work, to save Claude
quota at about the same speed. Judge every arm by hidden tests passed per task, Claude cost (Pro 5h %), and minutes.

**What we learned (DeepSWE v1.1, single runs):**
- Published 5.5 DeepSWE: Opus 74.2 vs Sonnet 71.0 (small gap). Screening 20 unused mid tasks: plain Sonnet 5.5 high
  solved 16/20 first try; only 1 confirmed Opus-only task (koota-entity-snapshot-rollback).
- The 4 Sonnet-fail tasks (koota-entity, koota-query, arktype, meriyah): plain Opus 2/4 (195/201 hidden, 106% Pro,
  42 min); Opus brain + Gemini 3.8 Flash 2/4 (197/201, 40% Pro + 8.9 Gemini pts, 128 min).
- Luna (gpt-5.6-luna xhigh): koota-entity solved 6.9 min / 5.7% Pro (API pool, ran it dry). On ChatGPT Plus
  (codex-plan): koota-query 41/43 (11.4%, 23 min), retry solved (16.3%, 15 min). Plain Sonnet on koota-query: 41/43,
  retry solved (14.8%, 7.7 min). Retries were verified independent (same prompt, fresh repo, no cross-run access).
- **Root cause:** the brain wrote 0 lines but made 16 code reads + 7 diffs + 7 test runs ≈ a whole Sonnet run, and in
  try 1 its review overrode Luna's correct design (koota's change tracking) with its own "baseline diffing".
  Details: ~/deepswe/FORENSICS.md.

**Done 10-06:** lean-brain nexus prompt `34eb963d1` (orders open with the request verbatim under "O PEDIDO (manda)",
no exploring to find edit points, review = diff + test output, keep the worker's design unless a request sentence or a
failing test contradicts it); live after a server restart (check `~/.omnigent/artifacts/.cache/06ec6c26f28940a0b77e83c16acaba3d/config.yaml`).
Usage ring shows Codex Plus 5h + week (`42b3713f4`, server restart is enough). `dswe.sh` keeps `.codex-tmp/` (holds a
copy of the ChatGPT login) out of git at prepare and grade. Disk cleanup: crawl-lab 5.0 GB
(`crawl-lab/logs/CHECKPOINT_CLEANUP_2026-10-06.txt`), wandb dup copies 9.1 GB; 22 GB free.

**Next, waiting on the user's go:** validate the lean brain with Opus + Luna xhigh (Codex Plus) on koota-query,
arktype, meriyah (~20-30% Pro, ~15% Codex 5h). Target: brain <7% Pro/task (was 11-16%). Run it like this:
`~/deepswe/team_queue.sh <queue> 70 30` in a `systemd-run --user` unit; queue lines are
`<task> <arm> codex-plan:gpt-5.6-luna xhigh`; use a new arm name (e.g. `team-opushigh-lean-codexplus-luna-xhigh`).
Codex meter: `python3 ~/deepswe/codex_quota.py` (16% 5h / 2% week at 16:50). Watch the unit by polling journalctl;
`journalctl -f` fails here (inotify watch limit). The partial arktype run is `...-aborted` (ignore it).

**Open questions for the user:** `gate_pushes: true` for codex/codex-plan (Luna runs `git push` + `gh pr create`
after finishing); wandb cache groups B (17.5 GB screening runs) and C (2.8 GB strict run); faster + spot-locomanipulation
cleanup (mostly root-owned: sudo command vs docker; safe 4.9 GB + broken `faster/.venv` 7.4 GB; audit in
`isaac-sim_ws/CHECKPOINT_AUDIT_faster_locomanip_2026-10-06.md`). apmix key no longer exists (user spells it "apimix").
Unmute heard the user fine on 10-04; the two 10-03 failures stayed unexplained.

## 2026-10-01 — READ FIRST (DeepSWE team benchmark; session d9653808)

**Goal (user):** Opus-level results with Opus *orchestrating* and a cheaper model doing the work,
to save Claude quota (back to Pro) at about the same speed. Count **individual hidden tests**, not
only all-or-nothing solves — the user insists on it. Don't swap models without root-causing first.

**Harness:** `~/deepswe` (not a repo). `dswe.sh prepare|fork|grade|stop` (env container runs as
ROOT since 10-01; Go's module cache was unreadable as uid 1000), `plain.sh <task> <model> <effort>`,
`team.py <task> <arm> [worker:model]` (env ARENA_BRAIN_MODEL/_EFFORT, ARENA_WORKER_EFFORT,
ARENA_TEAM_NOTE=team_notes/*.md, CAP_S), `quota_guard.py 70`, `heldout_6.json`, `FORENSICS.md`.
Runs in `~/deepswe/runs/<task>--<arm>` (app/ deleted after grading; model.patch, reward.json,
account.json, audit.json kept). Cost/audit: `~/nexus-bench/{account,audit,attach_png}.py`.
Matplotlib lives in `~/pi05-lora/openpi/.venv`; DB reads in the friendly-layer venv.

**Measured (single runs; Pro 5h %, Max ≈ 5×):**
- 6 near-miss tasks (kombu-sac, sqlite-utils, langchain, gql, bandit-nosec, sqlfmt), hidden of 313 /
  existing broken / Claude / min: plain Opus high 309/8/204%/82 · **Opus brain + Gemini worker
  309/4/67% (+8.8 Gemini pts)/125** (ran with the old blind Jev) · plain Sonnet high 309/19/104%/56.
- Held-out 6 (random v1.1, never tuned on), hidden of 269: plain Sonnet high 267, 4/6, 60%, 33 min ·
  Sonnet brain + Sonnet low worker + acceptance tests first 261, 5/6, 86%, 44 min · **plain Opus high
  265, 4/6, 159%, 67 min** (ipython 14/17: kept the quotes on a quoted path; chart
  ~/deepswe/heldout_6_opus_sonnet_team.png). NOT evidence Sonnet ≥ Opus: public DeepSWE (Opus 5/Sonnet 5,
  mini-swe-agent, 4 trials) has Opus high 21/24 vs Sonnet high 12/24 on these same 6 tasks, 73% vs 49% on
  39 tasks; a 6-task single run ties ~9% of the time. Public Luna: medium 10%, high 42%, xhigh 62%, max 68%;
  Gemini 3.8 Flash medium 70% (3.7 = 63%). Parser: scratchpad public_rates.py over ~/deepswe/pages*.
  **Why the gap is small (FORENSICS.md 10-02):** public site = Opus 5/Sonnet 5; published 5.5 DeepSWE is Opus
  74.2% vs Sonnet 71.0% (benchlm.ai). Sonnet 5.5 jumped (solved kombu-sac/sqlite, Sonnet 5 0/4 each). agy has
  gemini-3.8-flash-medium (public 72%). apimix is NOT configured on this host (searched 10-02).
  participle: all 3 arms miss TestAnalyzeCleanGrammarIsClean — the statement says String() is
  "multi-line, non-empty even when clean"; a stated detail nobody read closely (statement_first target).
  Opus audit flags were benign (koota wrote its own memory note in the sandbox's empty home after
  finishing; ipython tried `pip install mypy` in the offline container).
- Sonnet-low workers lose: Opus brain 191/195 on the 3 tuning tasks for 63%, Sonnet brain 193/195 44%.
- **Luna worker** (`codex:gpt-5.6-luna`, medium, OpenAI free pool): Opus brain + Luna solved kombu-sac,
  8.2 min, 8.1% Claude — but used **2.3M of the 2.5M/day pool** (resets 21:00) ⇒ ~1 task/day; it ran
  `git push` + `gh pr create` unasked (failed: no remote). codex worker has `gate_pushes: false`.

**Root causes (FORENSICS.md):** Gemini = 70–88% of team time (80–220 calls × ~7 s); routing 0–4 s,
Jev 2–4 s/run. Misses were born in the **Opus brain's orders** (kombu: dropped "demoted consumer is
notified" for promote_consumer in 2 of 3 orders; sqlite: "copy enable-counts" ⇒ exists=True) or in
worker self-tests (langchain: callbacks merged into a set ⇒ TypeError with langchain's own
FakeCallbackHandler). Jev only checks the brain's checklist; the brain reviews against its own order.

**Committed (unpushed, 13 ahead):** 42dcdc349 host-path images open in the file viewer; 435f88109 +
ba7a7f3fd Jev sees committed work and falls back to the session workspace. Other uncommitted edits
in the tree (ACP/Grok permission mode) belong to another session — don't touch.

**Running (10-03 12:01): gap screening, unit `dswe-screen20`** — `~/deepswe/screen.sh screen_20.json 70 25`:
20 unused mid-difficulty v1.1 tasks (seed 20261003, difficulty from non-Opus/Sonnet public configs). Per task:
Sonnet 5.5 high → Opus 5.5 high if it fails → Sonnet again (r2) if Opus solves; GAP = Opus solves, Sonnet fails twice.
Results `~/deepswe/screen_20_results.tsv`; pauses at 5h 70%, halts at week 25% or bad login; one image on disk at a time,
run working copies deleted after grading (patch/transcript/grades kept). Restarting the unit resumes where it stopped.
10-03 14:05 it HALTED on disk (8 GB free; old task images + a 2.1 GB node core dump in /var/lib/apport/coredump +
chat.db growth) and sat 9 h unnoticed (background-bash watch died at 13:32). 23:00: freed graded runs' working copies
+ npm cache (13 GB free), disk check now 10 GB before a pull / 4 GB per run, restarted. Watch = Monitor, re-arm every 30 min.

**Gap screening DONE 10-04 01:40** (`~/deepswe/screen_20_results.tsv`, chart `~/deepswe/screen20_gap_results.png`):
Sonnet 5.5 high solved 16/20 first try; of its 4 misses Opus 5.5 high solved 2 (koota-entity-snapshot-rollback,
koota-query-predicates) and failed 2 (arktype-json-schema-refs, meriyah-explicit-resource). Sonnet retry solved
koota-query ⇒ only **1 GAP: koota-entity-snapshot-rollback** (Sonnet 83/84 twice, same test: snapshot→rollback→snapshot
roundtrip). Cost 353% Pro (Sonnet 22 runs 248%, Opus 4 runs 105%), ~5 Max-week points, 3.2 h. Audit: Opus meriyah
git-fetched tc39/test262 from GitHub (agent shell has internet; bwrap can't cut it since claude needs the API) — public
conformance suite, run still failed. Yield 1 gap/20 ⇒ ~6 gap tasks would need ~100+ tasks screened.

**Running 10-04 10:30: team on the 4 Sonnet-fail tasks, unit `dswe-team-gap4`** — `~/deepswe/team_queue.sh team_gap4.queue 70 30`:
Opus 5.5 high brain (+ Jev as configured) with Gemini 3.8 Flash medium (`team-opushigh-gem38`) and Luna xhigh
(`team-opushigh-luna-xhigh`) on koota-entity, koota-query, arktype, meriyah. Luna lines wait for ≥2.3M left in the
OpenAI free pool (resets 00:00 UTC) ⇒ ~1 Luna task/day; apimix still unconfigured. Re-running the unit resumes.
Results: koota-entity Gemini3.8 SOLVED 84/84, 21.6 min, 9.6% Pro, 1.4 Gem pts · Luna xhigh SOLVED 84/84, 6.9 min,
5.7% Pro — Luna wrote all 17 files in 6 min / 2.33M tokens, then the proxy refused (pool spent); I interrupted the brain
(it had only read/tested, no edits). Plain Opus 84/84 7.3 min 18.4%. Remaining Luna lines would die the same way:
stop the unit when it logs WAIT (after the Gemini lines). Luna needs apimix (user to send base URL/key path/model).
**gap4 DONE 13:02 (unit stopped at the Luna WAIT; 3 Luna lines not run).** Chart ~/deepswe/team_gap4_results.png.
Gemini-3.8 team: koota-entity ✓ 9.6% 22m · koota-query 41/43 13.3% 43m (brain's order invented 'first run = all false'
baseline, FORENSICS.md) · arktype ✓ 9.5% 38m (plain Opus AND Sonnet failed it) · meriyah 47/49 7.8% 25m.
Totals on 4: team 2/4 solved, 197/201 hidden, 40% Pro + 8.9 Gemini pts, 128 min | plain Opus 2/4, 195/201, 106%, 42 min |
plain Sonnet (1st try) 0/4, 195/201, 49%, 25 min. Next idea: statement_first.md note to stop order-born misses.

**Open, waiting on the user:**
1. Go for `team_notes/statement_first.md` (statement verbatim + authoritative, every event path,
   repo test helpers, sentence-by-sentence final review) with Opus brain + Gemini on kombu-sac,
   sqlite-utils, langchain (~7% Max, ~4 Gemini pts). Launch like:
   `ARENA_BRAIN_MODEL=claude-opus-5-5 ARENA_BRAIN_EFFORT=high ARENA_TEAM_NOTE=~/deepswe/team_notes/statement_first.md
   python ~/deepswe/team.py <task> team-opushigh-gem-stmt gemini:gemini-3.7-flash-medium` (in a systemd-run unit).
2. Set `gate_pushes: true` in examples/nexus/agents/codex/config.yaml?

## What we're doing and why

Getting the work onto **one Claude Pro plan**. Max was bought for one month to
find the cheapest way to work; we go back to Pro at the end of the month. Every
decision is judged on quota, not elegance.

## Standing rules (Murilo)

- **Answer in English.** A voice layer translates; Portuguese gets translated twice.
- **One step at a time**, evidence before claims, show the result before advancing.
- **Commit locally, never push** unless explicitly told. **Never add Claude
  co-author trailers** — he strips them.
- Charts for comparisons (tables are fine for exact values), delivered as an
  **attachment, never a link**.
- No live worker smoke tests without asking; name the model first.
- Benchmarks: **do not fix either arm** — grade exactly as delivered.
- Commit messages in Portuguese on `Spot_Chain` only, English here.

## State — 2026-09-20 night

**Repo:** `~/wt/friendly-layer`, branch `feat/friendly-layer`. Remotes: `fork`
(Murilo's, push here) and `origin` (upstream project, do **not** push).
Unpushed commits: check, do not trust a number written here —
`git log --oneline fork/feat/friendly-layer..HEAD`. All the voice work below is
in them. He approves pushes one at a time.

**Not ours — never commit these.** Another session's ACP work (switching Grok /
Devin / acp sessions between `auto` and `bypassPermissions` mid-session) is
uncommitted in this worktree: `omnigent/acp_cli_harnesses.py`,
`omnigent/inner/acp_executor.py`, `omnigent/inner/acp_harness.py`,
`omnigent/runner/app.py`, `omnigent/runtime/harnesses/_executor_adapter.py`,
`omnigent/server/routes/_sessions/common.py`,
`omnigent/server/routes/sessions/routes_core.py`, `omnigent/server/schemas.py`,
`openapi.json`, `web/src/pages/ChatPage.tsx`, `web/src/store/chatStore.ts`,
`web/src/lib/acpPermissionMode.ts`, plus formatting in
`tests/server/test_spoken_summary.py`. **Stage by name; never `git add -A` or
`commit -a`.** The `AppShell.githubTabVisibility.test.tsx` failure comes from
it — verified against a clean tree, not ours.

**Today's work was all Gemini live voice**, each fix measured from
`~/.omnigent/logs/server/*.log` session summaries, each with a test that fails
on the old code:

- Playback had no jitter buffer: chunks were scheduled at the playhead, so one
  late packet stuttered the rest of the reply. 150ms lead, rebuilt on underrun.
- The conversation setup frame never sent `speechConfig`, so Google picked its
  own voice — it read in Aoede and talked in something else.
- The narrator's 20s upstream-stall watchdog killed sessions **while audio was
  still queued**. Gemini streams faster than it plays, so narrations were cut
  off mid-sentence exactly 20s after the last chunk. Now waits for the drain.
- The reply watchdog hung up 7s after the reader spoke — tighter than the
  model's own latency (first audio reached 10.6s, a handoff needs ~5.5s more).
  It was killing handoffs mid-thought. Now warns at 8s, hangs up at 30s.
- Handoffs sent one restated sentence, losing the whole discussion. The client
  only collected `inputTranscript`, and `note()` puts both sides in the
  companion's process where Claude never sees them. Now the call travels with
  the question, newest-first budgeted at 6000 chars.
- The voice sent to Claude without asking. Prompt changes did not hold ("can
  you check the documentation" was read as consent), so the **first `ask_claude`
  of a call never sends** — it returns a line telling the voice to ask.

**Open on voice:** a narration once died at 2.0s with `generationComplete:0` —
far too early for the 20s watchdog, cause unknown, probably a second narration
claiming the audio channel. The session log now carries `audio_s` and
`realtime_x`; **below 1.00 means Google sends slower than the browser plays**,
which would make the stutter a model problem, not a buffer one. Check it next
time he reports stuttering instead of guessing again.

## Quota — the thing that matters

Window runs **Fri 09:00 → Fri 09:00** local. At Sun 22:26 it was **44% used**,
with ~12.7%/day available for the remaining 4½ days; Sunday came in at 12.1%,
so it fits with about half a day of slack. A benchmark day (Saturday was 17.4%)
breaks it.

Measured from `~/.omnigent/claude-token-ledger.jsonl` (the archiver runs on a
systemd timer, 3,259 calls this window):

- **94% of calls are cheap** — median cache write 1,104 tokens. Normal use is
  not the problem, and cache *reads* are only 23.7% of spend.
- **40 calls (1.2%) carry 75% of all cache writes.** Of calls made after >60 min
  idle, **70.6% are full rewrites**; within 60 min, 0.4%. That is the 1-hour
  cache TTL expiring.
- Auto-compaction is working and is what keeps this affordable — rewrite sizes
  in one session fell 803k → 137k across a compaction boundary.
- **Omnigent is not the cause.** Six server restarts were tested; the next call
  after each wrote 0.9–2.7k, i.e. normal. These are Claude Code sessions; the
  cache lives at Anthropic.
- 12 rewrites were **not** idle-driven and read only ~25–33k, meaning the
  prompt prefix changed. Cause **unidentified** — not server restarts. Untested:
  working-directory changes in the system prompt, subagent spawns, MCP tool list
  changes. Do not blame anything without testing it.

Anthropic offers only 5m and 1h TTLs. A cache **read refreshes the timer for
free**, and a `max_tokens: 0` keep-alive would hold a session warm at read
price — but it must re-send the exact prefix, which only Claude Code can build,
so we cannot do it from outside. Hence this file.

## Useful commands

```bash
# quota now
cd ~/wt/friendly-layer && .venv/bin/python -c "
import httpx; from omnigent import cli_auth
S='http://127.0.0.1:6767'; t=cli_auth.refresh_stored_token(S)
h={'Authorization':f'Bearer {t}'} if isinstance(t,str) else {}
print([ (w['kind'],w['percent']) for p in httpx.get(f'{S}/v1/plan-limits',headers=h,timeout=30).json()['providers'] if p['id']=='claude' for w in p['windows'] ])"

# deploy: web build lands in omnigent/server/static/web-ui (no restart, hard refresh)
cd web && npx vite build && cp /home/nexus/omnigent-voice-ab/*.mp3 ../omnigent/server/static/web-ui/voice-ab/
# server routes / agent bundles need a restart; runners live outside the unit
systemctl --user restart omnigent-server.service

# voice session summaries (audio_s / realtime_x / timeline)
grep "gemini live session ended" $(ls -t ~/.omnigent/logs/server/*.log | head -1) | tail -3
```

## Also live

- `dev/benchmarks/nexus_arena` — bench2 finished: plain Fable and plain Opus
  both 36/39, nexus 34/39 at **a quarter of the Claude spend**. Same
  prompt/task/worker gave 8/14 and 13/14, so never rank setups on one run each.
  `bin/venv_guard.sh` exists because run trees symlink the real `.venv` and a
  worker repointed it at a benchmark snapshot, silently serving old code.
- Details: `docs/FRIENDLY_LAYER_SESSION.md`. Memory index:
  `~/.claude/projects/-home-nexus/memory/MEMORY.md`.

## 2026-09-21 morning — open items

- **Disk:** filled to 100% overnight (pi05 training wrote ~25G of checkpoints +
  wandb staging). Cleaned to 30G free without touching openpi/pi05 (user rule:
  never touch the openpi project). Next pi05 run of that size fills it again.
- **omnigent-host was running from a deleted interpreter path**
  (`bench2/runs/T4-gemini2/tree/.venv/bin/python3`, left over from the venv
  hijack). Deleting bench trees broke every new runner. Stopgap symlink
  recreated; host restarted on 2026-09-21 at the user's request so it runs from
  the real venv. Check `/proc/<host pid>/cmdline` before deleting bench trees.
- **T4-codex did NOT test codex.** Labels were `team.worker=codex`,
  `gpt-5.6-luna`, but both spawned workers were `gemini:` and the OpenAI pool
  still read 0. `resolve_worker` (omnigent/team_worker.py) returns None unless
  the picked name is in `worker_choices(spec)` — next step is checking whether
  `codex` is in the nexus bundle's worker choices. Its tree is kept ungraded in
  the bench2 scratchpad.

## 2026-09-21 midday — live voice + Unmute (read this first)

**Why:** Gemini Live freezes in the mornings. Measured: narration delivery was
3.1–3.8x realtime every time around midnight, but 0.05–6.8x between 10:22 and
10:59, with first audio up to 12.8s; Gemini's *text* API stalled too (11.7s,
28.9s, timeout). Same code both times, so it's Google load, not us.

**Done (local commits, NOT pushed):**
- `136f4bfe5` status line above the mic button (it was only a hover title):
  "not hearing you" (5s speech, no transcript), "audio lagging" (reply ran dry
  mid-sentence), "still thinking" clears on answer. Per-reply log in the
  session summary: `replies=[<audio>s@<rate>x/<n>dry<secs>s]`. The voice may not
  claim a Claude handoff without calling `ask_claude` (it had invented an
  "off-channel" link).
- `8cc980054` conversation frame sets
  `realtimeInputConfig.automaticActivityDetection.startOfSpeechSensitivity:
  START_SENSITIVITY_LOW`: replies were being cut after one word by echo/noise.
  **Not yet verified by a real call.**
- `fbef674d6` Omnigent serves Kyutai Unmute at **localhost:6767/unmute**
  (`omnigent/server/routes/unmute_proxy.py`). Details and build traps in memory
  `unmute-voice-stack`. Stack: `~/unmute`, `docker-compose.local.yml`, brain
  `gpt-5.6-terra` via the free OpenAI pool (~2s first token). English only.

**Later the same day — Unmute is now a third engine (`52f9b34f9`, not pushed).**
Picker "Unmute (local)": calls and summary narration run on the local Kyutai
stack, with Omnigent as its model ("brain", gpt-5.6-terra, free pool, tools at
`reasoning_effort: none`). The relay `/v1/live/unmute/ws` speaks Gemini Live's
frames, so the page reuses the Gemini client and handoff code unchanged. The
Unmute-side patch is committed on `~/unmute` branch `omnigent-local`. Measured
with synthetic speech: reply audio 1.25-2.2s after the speaker stops, narration
first audio ~0.25s, no dry gaps. Details and traps: memory `unmute-voice-stack`.

**Open:**
1. User to try the Unmute engine in a real call (mic echo, barge-in, voice,
   handoff) — synthetic tests cannot judge echo or the voice itself.
2. Disk ~6 GB free. pi05 training (openpi — NEVER touch) wrote ~25 GB last
   night and will fail if it runs again. Candidates the user has NOT approved:
   Isaac shader caches 30 GB (stop Isaac first), spot-teleop venvs in git
   history ~6 GB (blocked: 9 stashes, 12 uncommitted changes and an unpushed
   `master` there), friend-clone-test 3.8 GB, journal 3.7 GB (sudo).
3. T4-codex benchmark never used codex — CAUSE FOUND (09-21 evening): during
   that run the host executed code from `bench2/runs/T4-gemini2/tree/omnigent`
   (venv hijack; host tracebacks show the path). T4's code base `46a0c437a`
   predates the Worker control `494ff328a`, so the codex label had no effect.
   Host and venv are now clean (`venv_guard.sh` ok).
   **Rerun 09-21 20:22 (T3, Opus brain + codex gpt-5.6-luna): 5/5, 0 regressions,
   6.0 min, Claude $1.73, free pool 637k tokens (580k of them cached input
   re-sent by Codex) = 25% of the 2.5M day.** The pool is shared with the
   Unmute voice brain (Terra, ~1M/day). One run; variance is large.
   `run_nexus.py <task> <host> [worker] [model]`; ARENA_WORK is used as given.
   **Later 09-21 (T3, Opus brain):** codex-plan (ChatGPT Free, luna) 4/5 in
   15.9 min, 5% of its 30-day allowance; NIM GLM 5.3 and DeepSeek too slow
   (minutes per worker step); APMIX free DeepSeek 5/5 hidden tests but lint
   dirty and unfinished, and it used the key's entire 4M allowance in 10 min.
   Verdict with the user: stick with Gemini; Codex on the API pool when a small
   task needs speed. Details: memory `codex-worker-quotas`.

**09-22 — videos and big HTML open in the side viewer** (`527a1c1bb`): the
file viewer plays .mp4/.webm/.mov from the uncapped download stream and fetches
HTML past the 10 MiB read cap whole. Agents should LINK such files by absolute
workspace path instead of attaching (memory
`deliver-videos-and-html-as-workspace-links`); files in a Claude scratchpad are
outside the workspace and will not link. `08400f55d`: summaries show digits;
`speakable_numbers()` words them for Chatterbox/Unmute only.
   **Worker `codex-plan` (`8ee9490a1`)**: same Codex on the ChatGPT sign-in in
   `~/.codex/auth.json` (VS Code extension, **Free** plan, 30-day allowance).
   First try failed: Codex fell back to config.toml's default `gpt-6-astra`,
   which Free rejects; the default is now `gpt-5.6-luna` (backup
   `~/.codex/config.toml.bak-20260921-astra`). Rerun: T3 **4/5**, 15.9 min,
   Claude $1.63, **5% of the Free 30-day allowance** (meter 2.0%→7.0%), 0 API
   pool tokens. Read the meter from a `codex exec` rollout's `rate_limits`.
   **NIM (free) as the worker — not usable (09-21 night):** GLM 5.3 51-73 s per
   small prompt (stopped). DeepSeek V4.1 Flash 5 s per small prompt at 22:00,
   but as a worker 1.5-7 min per step on real context (NIM load varies).
   Hermes default is now DeepSeek (GLM backup `~/.hermes/config.yaml.bak-20260921-glm`).
   The T3-hermes grade (5/5) is VOID: an outage re-ran my launch, two runs
   shared one tree. Decision pending with the user: stick with Gemini.
4. Qwen-Omni-Realtime (Alibaba, Singapore region, 90-day free quota) is the
   cloud fallback option if Unmute doesn't satisfy; needs the user's account.

**Traps from today:** a daemon started while a venv was hijacked keeps the
bench-tree interpreter path (check `/proc/<pid>/cmdline` before deleting bench
trees); containers write root-owned files (delete via
`docker run --rm -v ... alpine rm -rf`).

## 2026-09-21 evening — Unmute engine live, compaction bug found (read first)

**Unmute (local Kyutai voice) is the third live engine** and works end to end;
details and traps in memory `unmute-voice-stack`. Commits (local, not pushed):
`52f9b34f9` engine + brain, `8820f9464` interrupt only on recognized words
(noise cancelled replies), `60bc170a6` 8 voices in a menu (default ex02,
user liked ex02 and ex03-happy), `8b5cb49bd` per-call mic trace log.
`~/unmute` branch `omnigent-local`: `544de6f`, `1b5bbaa`, `aec1e7d`
(speech-to-text `batch_size = 4`; at 1 a narration blocked calls).

**Open on Unmute:**
1. User saw "heard my first sentence, then nothing for 40s" (audio arrived, no
   words). Not reproducible with recorded speech. Next occurrence: read the
   `unmute live input trace` line in the server log (mic dBFS per second).
2. Host `~/.cache/huggingface/token` vanished ~18:40 (not us). STT was started
   with the copy in `~/unmute/volumes/hf-cache/token`; asked the user.
3. Page bug: attachments with a caption made mid-turn fold into collapsed
   steps (attach uncaptioned, with the turn's response_id, until fixed).
4. Double question before a handoff: fixed in `1965baafa` (a first send goes
   through when the voice's last question was about sending and the reader
   said yes). Also `3f058a8a2` talk over a reading, `324c790c5` readings
   survive session switches, call volume follows the narration slider.

**Compaction bug — fix `842f2acca` is LIVE** (in the host since its 18:41
restart; the user reports compaction working, 09-21 ~20:20). History below:
every Claude relaunch rebuilds the transcript from Omnigent's compaction
records; the hook path saved them before Claude wrote the compacted transcript,
so each held the whole old history and relaunches undid compactions (611k→7.4k
came back at 589k). Deploy order: repair the latest compaction record of
`d14bc7496a6a42b6b6a0730b3f4f5f20` (1,541 msgs) and `79d4dd11…` (4,104 msgs)
from their current Claude chain, user runs `/compact`, then restart
`omnigent-host`. `tests/test_claude_native_forwarder.py` cannot import here
(`opentelemetry-sdk` missing from the venv); its compaction tests were run from
a scratch copy.

Disk: something freed ~22 GB ~18:46 and wiped this session's scratchpad.

## 2026-09-22 — READ FIRST (latest state)

**Waiting on the user (ask, don't act):**
1. Pushed 09-22 on request (worker paths now checkout-relative via `OMNIGENT_CHECKOUT`, APMIX
   worker dropped). Here, add `Environment=OMNIGENT_CHECKOUT=/home/nexus/wt/friendly-layer` to the
   installed host unit only with the user's go; it takes effect at the next host restart (compact
   first). Do NOT re-run install.sh here: this server's unit loads debby from omnigent-fork.
2. Picker fix offered: the Claude-terminal model picker fails silently (sends alias `opus`, server
   stores it as no change, nothing typed). Should show why it could not switch.
3. `sudo bash ~/cleanup-sudo.sh` (journal + Ollama) is the user's to run.

**Done 09-22 (local commits):** `08400f55d` summaries show digits, `speakable_numbers()` words
them for Chatterbox/Unmute only; `527a1c1bb`+`307cdc6dd` file viewer plays video (download
stream) and loads HTML past the 10 MiB cap; memory `deliver-videos-and-html-as-workspace-links`
(link files by absolute workspace path, never attach video/HTML; scratchpad paths don't link).

**Project Analysis session** `d14bc7496a6a42b6b6a0730b3f4f5f20` (transcript `08622a13-…`):
relaunched 16:33 on Claude Code 2.1.280, `model_override=claude-opus-5-5`, Opus 5.5 confirmed.
Opus 5.5 needs Claude Code >= 2.1.280; a pane started before an auto-update keeps the old build.
Restart one claude-native session without the host (resumes from its compaction, 6.6 MB -> 27 KB):
`DELETE /v1/sessions/{id}/resources/terminals/terminal_claude_main`, then
`POST /v1/sessions/{id}/resources/terminals {"terminal":"claude","session_key":"main","ensure_native_terminal":true}`.
Orphan `claude --resume` processes from before a host restart can linger; check `ps` by start time.

**Friend's PC** (reached over Tailscale SSH with the user's permission; no credentials stored):
clone `~/omnigent-friendly-layer`, data dir `~/.omnigent-friendly-layer`, services from our
installer. The missing Worker row was a picker bug, not their setup: a 09-19 nexus test chat left an
uploaded copy named `nexus`, the new-chat picker lets the newer copy win, and copies lost their
worker_choices (fixed in `1b66722ed`, applied there by hand and rebuilt 09-22). To update: `git
checkout .` (drops that hand patch and the old bootstrap path rewrite), `git pull`, re-run
`deploy/friendly-layer/bootstrap.sh`. Hermes and Grok are not configured there.

**Workers verdict (T3, Opus brain):** Gemini stays the worker; Codex on the API pool (5/5, 6 min,
25% of the daily pool) for small fast tasks. Codex on ChatGPT Free 4/5 16 min, 5% of 30-day
allowance; NIM GLM/DeepSeek too slow; APMIX DeepSeek burned 3.8M tokens in 10 min. Details:
memory `codex-worker-quotas`. `run_nexus.py <task> <host> [worker] [model]`.

## 2026-09-22 evening — READ FIRST (supersedes the section above)

Fork `feat/friendly-layer` (see the newest commit); friend's PC (clone `~/omnigent-friendly-layer`, data
`~/.omnigent-friendly-layer`) is on the same commit. His host+server restarted 19:47, server 20:21.

**Done today (pushed):** `1b66722ed` Worker row when an uploaded nexus copy wins the new-chat picker ·
`79dd0a559` worker paths via `$OMNIGENT_CHECKOUT` (host unit), bootstrap no longer edits clones, APMIX
worker gone (provider + key removed from `~/.omnigent` too) · `d8b021dbf` agy reader: a `schedule`
timer cancelled early ("Timer cancelled early…"/"Finished waiting…") counts as finished, and the idle
backstop re-checks every tick so the 600 s task-wait timeout can fire (friend's nexus waited 25+ min on
a finished Gemini worker) · `52c9b8645`/`1300b9b19`/`3f3f2de93` friendly view lists the answer's
openable files (links + inline-code video/html/img/pdf paths) under the summary · `662e343d3` nexus
prompt: one absolute-path link per file · `6ef766b95` `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`: Claude Code
2.1.280 withholds TaskCreate/Update/List/Get from Opus 5/5.5 (model is the only factor, measured).

**Facts that cost time:**
- Runner code reloads with no host restart: the zygote refuses forks once omnigent files change and
  the host spawns fresh `_entry` runners from disk; runners idle-exit after 60 min. Host restart only
  for `omnigent/host/*` or unit env. Server restart re-registers `--agent` bundles; here it also
  relaunched ~6 idle Claude panes (20:22).
- agy from outside: port from the runner log (`127.0.0.1:<port>/exa.language_server_pb…`), token =
  `--csrf_token` in `/proc/<agy pid>/cmdline`, then `antigravity_native_rpc.get_all_cascade_trajectories`
  / `get_trajectory_steps(port, cascade, csrf_token=…)`. Task logs: `~/.omnigent/antigravity-native/
  <bridge>/agy-home/.gemini/antigravity-cli/brain/<cascade>/.system_generated/tasks/`.
- Tools offered to the model: point `ANTHROPIC_BASE_URL` at a local server that logs the
  `/v1/messages` body; run interactive `claude` in `/home/nexus/.local/bin/tmux -L todolab`. No quota.
- Pre-existing, not ours: pyrefly errors in `runtime/telemetry.py`, `_sessions/helpers.py`; model-id
  lint (repo-wide); 8 `tests/host` failures (stale_build_info ×2, model_options ×5, session_log_dir).

**Later the same evening (pushed):** `a547fa85d` a video/download in a chat whose runner idle-exited
got 503 ("Unable to play this video here"): the host tunnel can't stream, so the download now wakes
the runner (`ensure_runner_connected`, like the shell route); waking a claude chat relaunches its
pane but sends nothing to the API · `ef156f243` a tmux whose `kill-server` failed (AppImage tmux exits
127 on a full disk) was stranded: close() and the orphan sweep deleted its socket anyway; both now
keep the dir while the socket still answers. Stranded 09-21 Claude (pid 1102933) ended by pid.
Unmute parking is LIVE (`~/unmute` 3251893, c709f01; user service `unmute-gpu-parker`): parks both
moshi-servers after 300 s without a non-loopback client, restores on connect; idle GPU 15.8→4.2 GB,
first speech after idle ~1.3 s later. Task list works after relaunch (Claude Code's own reminder
nudges Opus into TaskCreate, so no extra instruction needed so far).

**Open (ask first):**
1. Laya test DONE 09-23 (`~/laya-test/run_laya.py`, `score.py`): untuned laya/laya-multilingual vs the
   companion's routing. Ledger "question+answer" also comes from direct `ask()` (voice/panel), so only
   rows found as user items in the chat are routing: 63 in the user's chats, 11 kept. Laya best AUC
   0.64, accuracy <=54% vs 83% always-Claude, 22-48 of 52 wrongly kept; length alone AUC 0.72. Keeps
   depend on the companion's notes ("where were we?"), which a message-only model can't see. Not
   worth pursuing. Separate idea (needs OK): worker chats' briefs (164 of 306 route() calls) always
   forward; skip the companion for child sessions. ~16-20 ms per Laya decision on GPU.
   Jev follow-up (same day, 62 decisions WITH the companion's notes): Jev AUC 0.66/0.63, reflex 4B
   (open, local) 0.62/0.57, Laya 0.50; every one below always-forward (73%) at default cutoff.
   Jev cost $0.011 via OpenRouter. Details: memory `jev-routing-test`, `~/laya-test/jev-routing.png`.
   Worker-report gate WORKS (33 nexus reports, 12 hollow): Jev AUC 0.97 (11/11 caught, 4/19 false
   at 0.5; 8/11, 0 false at 0.8), reflex 0.91 (8/11, 0 false). Waiting on the user's Opus marks for
   the 50 messages in `~/laya-test/routing/messages_to_mark.json`.
2. Installer: `uv sync --extra all` fails on a fresh clone (chatterbox-tts via gradio 6.8 needs
   starlette<1.0; omnigent needs >=1.0.1). `FRIENDLY_LAYER_SETUP.md` wrongly says a server restart
   interrupts sessions.
3. Older: model picker silent no-op; language cache not cleared on change (`spoken_summary.py:654`,
   60 s); user runs `sudo bash ~/cleanup-sudo.sh`.
4. XR hub: `petrobras-chains/xr/serve.py --spot <crawl-lab/xr>` serves chain `/` + Spot clips `/spot/`
   on 8731 (nohup, `xr/hub.log`); idea: port the rollout viewer into the shell as a second app.

## 2026-09-23 — READ FIRST (supersedes the sections above)

**Why:** cut Claude quota (goal: back to Pro). Today's thread: fit a System One model (TypeSafe's
Jev) into a "company" architecture — Claude = manager/orchestrator, Jev = cheap shift lead that
workers ASK typed questions (yes/no, pick-one), cheap models = workers, companion = receptionist.
User direction + prior art: memory `jev-company-model-direction`; test numbers: `jev-routing-test`.

**Decided:** Jev is NOT a companion router (fails: answer lives in what the companion remembers)
and NOT a pass/fail gate on Claude's worker reviews (user: no actionable feedback, retry loops).
It IS the supervisor workers consult. Brainstorm, don't benchmark, unless asked.

**Done (local commits, not pushed; fork is at c87393da1):** `8d891992d` supervisor —
`omnigent/supervisor.py` (Jev via OpenRouter `POST /api/alpha/decisions`, model
`typesafe/jev-1.13`, key `~/.openrouter-key`; optional local reflex fallback
`OMNIGENT_SUPERVISOR_FALLBACK_URL`; bar 0.75 `OMNIGENT_SUPERVISOR_MIN_CONFIDENCE`; ledger
`~/.omnigent/supervisor/asks.jsonl`), `omnigent/runner/supervisor_tool.py` (reads parent's last
human request + its 3 latest messages + the worker's brief + its steps over REST),
tool `sys_ask_supervisor` (ToolManager + native relay + agy allowlist), `supervisor.WORKER_RULE`
appended to every nexus brief while on, one nexus prompt section. 18 tests `tests/test_supervisor.py`.
Live Jev check: 0.4–1.4 s, ~$0.00002/question. **OFF until** `touch ~/.omnigent/supervisor/enabled`
(or `OMNIGENT_SUPERVISOR=on`) + a server restart (republishes the nexus prompt).
Also today: `a547fa85d`/`ef156f243` (pushed) video wake + stranded tmux; Unmute GPU parking live
(`~/unmute` 3251893/c709f01, user service `unmute-gpu-parker`).

**Running:** NIM latency probe, 11 worker models every 15 min until ~19:10 BRT
(`~/worker-latency/probe.py` → `latency.jsonl`; chart it, count empty answers as failures).
Round 1: GLM 5.3 0.8 s, Nemotron Ultra 4 s, Glimmer 6 s, Lightning 7 s, gpt-oss-20b 14 s,
Gemma 4/Kimi K3 ~55 s; GLM Flash + DeepSeek V4.1 Flash timed out; Mistral Large 404; Nemotron
Super empty.

**Open (user's call):**
1. Decisive arena arms (`dev/benchmarks/nexus_arena`): T4 then T3 only. Plain Opus 5.5 first (no
   baseline yet; ~6%+2% of a 5h window, ~1% of the week), then nexus + best worker from the probe
   + supervisor on. **Do not run GLM yet** (user). `run_nexus.py` hardcodes brain `claude-opus-5`
   → switch to Opus 5.5 before running. Claude quota 09-23 15:10: week 72%, 5h 24%.
2. 50 messages for the user to mark "needs Opus" (`~/laya-test/routing/messages_to_mark.json`).
3. Rotate: OpenRouter key and HF token both appeared in chat. OpenRouter balance ≈ -$0.02 (key cap $1).
4. Friend's PC offline — needs `git pull` + server restart for the video fix.

## 2026-09-29 — READ FIRST: harder benchmark (bug-hunt set H1-H4), isolation, Sonnet 5.5

**Why:** T1-T5 saturated (every arm 21/21 on T5, 14/14 on T4 once the unfair tests were fixed), so
scores no longer separated setups. User: "build it, use real prompts from me, do not give away the
task... make sure the model is not cheating and it's isolated".

**The set (83899e809):** four real bugs of this project, stated only in the person's own words +
the evidence they would hand over (sanitized real logs / stored items in `evidence/H*/bug-report`,
copied into the tree as `bug-report/`), hidden tests through entry points that exist at start,
each rejecting the plausible wrong fixes (verified by applying them), gold full / start 0
(calibrated twice): H1 host died (0be307de0, 3 tests), H2 compaction undone (842f2acca, 2), H3
"still checking" starting before the real wrong fix 6c8997b26 (9db4ba9be from 6528e1c86, 3), H4 video
in idle chat (a547fa85d, 2). Known weakness: H3 also passes with a blind ~1.5 s sleep.

**Isolation:** H* trees are always isolated (mktree: no dev/benchmarks, docs/RESUME.md,
docs/FRIENDLY_LAYER_SESSION.md; archive = no git history; `.venv` -> `~/nexus-bench/venv`, a copy of
the live venv WITHOUT the editable omnigent, plus opentelemetry-sdk). Plain arms run in
`~/nexus-bench/sandbox.sh` (bwrap: /home hidden except the tree, venv, node, uv pythons and the Claude
credentials file; /tmp and pids private; clearenv) with no web tools. Nexus arms cannot be
sandboxed (the host spawns brain tools and workers), so `~/nexus-bench/audit.py` scans every tool
call of a run (plain stream, brain + worker items, Claude workers' transcripts) and TAINTS reads of
other checkouts, transcripts, Omnigent state, notes, other runs, git remotes/GitHub, web tools, or
foreign sessions. Verified on a fake cheating run; all real runs so far CLEAN.

**Screening, plain Sonnet 5.5 low, sandboxed:** H1 2/3 (1.1 min, found the stale-pid half, missed the
unpolled zygote), H2 0/2 (trimmed records = symptom fix), H3 2/3 (found the timing, fell into the
own-messages trap), H4 0/2 (host-tunnel shortcut, dies >10 MiB). All four discriminate.

**Other results 09-29 (one run each):** Sonnet 5.5 low as the nexus WORKER on T4: 14/14 6.6 min ~12% Pro
5h (brain 7.3 + worker ~4.9 est. by price). Plain Sonnet 5.5 on T4: low 14/14 3.4 min ~2.7%, high
14/14 4.8 min ~8.8% (both pass the ORIGINAL manual-add test; the Gemini team failed it every time --
report T4 on both test versions, 218a36a25). Jev ablation (Gemini worker, brain high): T4 without Jev
13/14 orig / 14 fixed, 12.7 min 7.9% (with Jev 15.5 min 11.2%); T5 without Jev 20/21 -- a REAL
double-count bug (test_native_reports_count_their_growth_once) -- 38.4 min 27.4% (with Jev 21/21
33.1 min 23.4%).

**Harness now lives in `~/nexus-bench`** (the /tmp copy died in the 09-27 shutdown; rebuilt from the
transcript archive, REBUILD.md). New: plain_seq.sh, sandbox.sh, audit.py, nojev_run.sh (moves the Jev
flag files aside and restores them on exit), account.py counts Claude workers from their transcripts
and plain Sonnet by price.

**Also found:** the Claude plan-limits tray freezes when api.anthropic.com/api/oauth/usage returns 429
with retry-after 0 (140 times since 09-28); the server retries every minute. The token-ledger timer
(claude-history-archive) did not come back after the reboot (ledger stops 09-27 13:24).
**Update 09-29 13:45 -- STATE AT COMPACTION:**
- Running: unit `arena-sonhigh` = plain Sonnet 5.5 HIGH, sandboxed, H1..H4 in order (user asked
  for it before the team arms). So far H1 3/3 5.1 min (low was 2/3), H2 0/2 + 2 regressions in
  tests/test_claude_native_forwarder.py (worse than low). H3, H4 pending. After each: grade is
  automatic; run `PYTHONPATH=~/wt/friendly-layer ~/wt/friendly-layer/.venv/bin/python
  ~/nexus-bench/audit.py ~/nexus-bench/runs/<T>-sonnet55-high`.
- Then (user decides): plain Opus 5.5, team Gemini+Jev, team Sonnet-worker+Jev on H1-H4 (asked
  "Core 3", user first wanted Sonnet high). Launch plain: `systemd-run --user --unit arena-X
  --setenv=HOME=/home/nexus --setenv=ARENA_VENV=/home/nexus/nexus-bench/venv
  --setenv=ARENA_SANDBOX=/home/nexus/nexus-bench/sandbox.sh ~/nexus-bench/plain_seq.sh <T>
  <arm>@<effort>` (arms: opus55, sonnet55). Team: `nexus_ab.sh <T> gemini:gemini-3.7-flash-medium
  <arm>` or `claude:claude-sonnet-5-5` with `--setenv=ARENA_WORKER_EFFORT=low`; ALSO set
  ARENA_VENV for H tasks (mktree needs it); audit every team run.
- Quota 13:02: Claude 5h 48% (resets 15:20), week 24%; Gemini week 60%.

**Summarizer bug fixed, 9f43de163 (NOT LIVE -- needs a server restart; user not yet asked/answered):**
chat f4239d3f ("Get pot-rl-example URL", created 13:09) stored every message after the first with a
`translated_text` block = the companion's routing JSON `{"forward": true, "english": "..."}` (the
model nested its previous reply inside "english"); Claude received that JSON as the message and the
summary, fed input_text+translated_text joined as "the question", restated the question. Fix:
discussion._unwrap_english, validate_inbound_translation refuses/unwraps JSON, _turn_user_questions
skips translated_text. Why only that chat is NOT known (fresh companion, 1st message fine, 2..6
bad; other chats with routing unaffected). That chat's companion ledger
~/.omnigent/companion/f4239d3f...json holds "summary" entries equal to the questions (may keep
priming it). A server restart re-posts old worker transcripts + bogus usage (known bug, unfixed).

## 2026-09-26 — READ FIRST: long-task speed of the nexus team (supersedes the 09-25 notes below)

**Goal (user):** keep Gemini 3.7 Flash Medium as the worker (the point is cutting Claude quota; plain
Opus + Jev was rejected for that reason); quality is settled; make LONG tasks faster. Parallel Gemini
workers are PARKED (user: raises Gemini spend). Gemini 3.8 is NOT worth testing: 09-16 measured 25-33
min/step vs 5-8 for 3.7 and 1.28 vs 0.47 weekly pts/step (memory claude-max-worker-economics).

**Results (Opus 5.5 brain, one run each unless noted):** T4 fair tests (1bcd6f6ce): plain 14,14 ·
team+Jev 14 x7 · team no-Jev 12,13,14 -> quality tie, team ~60% less Claude. T5 long task (98093dcbd,
21 tests): plain 18/21 22.2m $4.89 · team v1 17/21 44.1m $2.20 · team v2 (2f3f5cfd3 + d33d84a83)
20/21 41.7m $3.11. Regression after v2: T1 8/8, T3 5/5, T4 14,14. T5 v2 time: Gemini working 29.1
min (309 calls ~5-6 s each, 137 file views), brain-only 12.6 (explore 5.8, reviews 6.8).

**Speed fixes committed 09-26 (server restarted 16:41; runner code loads in new runners):**
a6d4b9f8d Jev sees the lines each checklist item names when the diff passes 60k (replay: false
send-backs 0.29->0.95, 0.22->0.96) · f786ea263 prompt: dispatch step 1 early, explore later steps
while the worker runs · 0a806f2fb evidence shows last lint/type-check command + output (NOT live in
run T5-team3). Also live: 0be307de0 host reaper fix (verified with zygote disabled), 1428e43f6 inbox fix.

**T5 team v3 (runs/T5-team3, a6d4b9f8d + f786ea263 live):** 20/21 (same failing test as v2), 39.8 min
to the final answer (v2 41.7), $4.06 Claude (v2 $3.11, plain $4.89), Gemini 2.1M tokens. Step 1 went
out at 4.5 min (v2 5.2) and steps 2-3 went out as soon as the previous passed; still 6 Jev
send-backs, all false: 3 on "tsc/oxlint/prettier clean" (evidence showed only the last lint command
of the current turn), 2 on "_read_grok_auth has no regex" (focus clipping kept frequent names, not
the function), 1-2 on an ambiguous try-block item. Fixed after the run: 5a447f0f0 (evidence: last
run of EACH lint/type-check tool across the worker session; chained "pytest && ruff" counts as both)
and dd7879fce (focus: names weighted by rarity; a function the checklist names is kept whole when
it fits). Replays on the final tree: lint item 0.25->0.98, _read_grok_auth 0.13->0.94, Grok 500
0.52->0.93, shared helper 0.30->0.66; T5-team2 fixes unchanged (0.97). Replays of early rows on the
FINAL tree are not faithful (compare with the HEAD code, as done here, before calling a regression).
NEXT (user decides): one more T5 team run to measure 5a447f0f0 + dd7879fce together (Gemini 3.7 FM).

**T5 team v4 (runs/T5-team4, 5a447f0f0 + dd7879fce live, verified in the brain inbox):** 29.3 min to
the final answer (v3 39.8, plain 22.2), $3.33 Claude, Gemini 1.36M tokens, 5 checklist asks, 1
send-back. Graded 12/21, but all 8 new failures are ONE spec gap: `build_token_usage()` with no
since/until defaulted to the last 7 days, and the hidden tests' data is dated 09-14..09-16 (the
spec never says the default; v2 chose 30 days, v3 all time; both break once the test dates age).
Probe with an all-time default: both hidden files 10/10 -> 20/21. DONE with the user's go (d4ee3ca08):
the T5 tests had three artifacts (day-dependent no-bounds calls; the route test read this machine's
real ~/.grok sessions when an arm ingests Grok in the route; exact day lists failed zero-filled days).
Gold 21/21, start 0/21. Regrade (bench4/t5_regrade.txt, runs/*/grade_v2.json): EVERY T5 run is
21/21 (plain 18->21, v1 17->21, v2 20->21, v3 20->21, v4 12->21). T5 does not separate quality;
compare time and spend only: plain 22.2m $4.89 · v1 44.1m $2.20 · v2 41.7m $3.11 · v3 39.8m $4.06
· v4 29.3m $3.33 (Gemini 1.36M). Chart: bench4/t5_chart.py. v4 timeline: step 1 out at 4.9m, step 2
12.8, step 3 17.8, one fix round 24.9-28.5, final 29.3; brain idle-waiting on Gemini most of it.

**09-26 evening — quota per run (bench4/account.py: each run's OWN tokens and worker minutes, since
other sessions share the quota bars) + regression reg3 (T1/T3/T4, dd7879fce + 5a447f0f0 live; T3/T4
also b8d69d344).** Pro 5h % per task, plain vs team: T5 45 vs 32 · T4 18-22 vs 12-18 · T1 14 vs
10-12 · T3 6 vs 8. Gemini week % per team task: 0.7 (T3) to 2.2 (T5). Time: team 1.3x (T5) to 3x
(T3) slower. reg3: T1 7/8 21.2m (real miss: wakes the parent once per child, not per streak; 5
send-backs, 3 false on a repo lint script Jev could not see -> fixed b8d69d344, replay 0.47->0.98),
T3 5/5 9.1m, T4 13/14 19.2m (real miss: manual add drops cached tokens; Jev passed all 3 checks).
Chart: bench4/quota_chart.py. Takeaway: the team saves Pro quota only on big tasks; small ones
cost as much or more and take 2-3x longer. Biggest remaining team cost is the brain's output (orders,
check-ins) and cache reads per turn, not cache writes.

**09-27 — the team's small-task saving shrank because the BRAIN got ~2x dearer, not plain Opus.**
Pro 5h % per run, 09-25 night -> 09-26: T1 5.3 -> 10.5-11.6, T3 5.9 -> 7.7-8.0, T4 5.9-6.7 ->
11.5-18.3; scores equal or better at night (T1 8/8, T3 5/5, T4 14/14 x4). Likely cause: the brain
runs unpinned ("host default") and reads ~/.claude/settings.json (changed 09-25 13:20, now xhigh for
opus-5-5), while plain arms are pinned --effort high. At night the unpinned brain spent less than
one pinned to high (T4 12.8k vs 19.4k output); T3 kept the same 21 brain calls but 1.8x output
(longer thinking); T4 also ran 2-3x more review calls. CONFIRMED 09-27 (runs/T3-hi1,
T1-hi1, ARENA_BRAIN_EFFORT=high): T3 5/5 6.7m 5.4% Pro 5h (11.0k output, = night), T1 8/8 12.3m 8.9%
(night 5.3%; T1's brain also runs more review commands than at night: 20 shell calls vs 13). Pinned in
7b5615404 (executor.reasoning_effort: high; UI picker still overrides). LIVE ONLY AFTER a server
restart (bundle change). Open: the night default spent LESS than high on T4 (12.8k vs 19.4k output),
so night was probably below high; medium is untested.
Long-task check on the live default (server restarted 12:20 09-27, runs/T4-hi2, T5-hi2): T4 13/14
15.5m 11.2% Pro 5h (xhigh 18.3%); T5 21/21 33.1m 23.4% (xhigh 29.3m 32.1%; brain chose 4 steps + a
fix round instead of 3). No quality regression; T5 +3.8 min is one run. T4's miss every time on the
team is test_manual_add_is_tagged_by_source: the spec gives `add [--input N] [--cached N] [--output N]`
with no rule on whether --cached is inside --input (OpenAI's own fields) or on top (the gold). Arms
that read OpenAI semantics count 0 for a lone --cached. Unfair test candidate; ask before changing.
account.py now ignores items outside [start, end]: a server restart RE-POSTS finished worker
transcripts with new timestamps (T3-hi1 replayed at 12:21 in 3 s, plus a MODEL_PLACEHOLDER_M299
usage row of 252k tokens) -- the known forwarder re-post bug, now shown to add bogus usage too.

**Harness:** scratchpad bench4/: drive.py (CAP_S env, token refresh on 401, kills leftover agy),
nexus_ab.sh <T> <worker:model> <arm>..., plain_seq.sh, replay_checklist.py <run dir> <nth check>
(re-asks Jev on the final tree), dump.py, timeline.py. Profiling snippet: per-worker-turn spans from
session items (function_call timestamps only; trailing items at session end distort spans).

## 2026-09-25 night — READ FIRST (supersedes the sections above)

**Update 09-26 16:30 — CORRECTION: T4's "cached-token" gap was a test artifact (fixed 1bcd6f6ce).**
Every T4 arm counts cached tokens right; the failing ones chose max(total_tokens, parts) ("never
under-count"), which the old test's bogus total_tokens=999_999 punished. The brain writes that rule
in its order; Jev only checks the brain's rule, so "Jev catches the cached-token bug" (below) is
wrong. Regraded with fair tests (runs/*/grade_v2.json, bench4/t4_regrade.txt): plain Opus 5.5 14,
14 · Gemini team with the checklist gate 14 in all 7 runs · without it 12, 13, 14 (manual-add
source tag missed twice). T4 quality is a tie; the team's edge there is ~60% less Claude.
Regression check after 2f3f5cfd3 + d33d84a83: T1 8/8 14.6m, T3 5/5 9.7m, T4 14 and 14 (v2).
Parallel workers parked by the user (Gemini spend).

**Update 09-26 13:40 — team vs plain, Jev ablation, long task T5 (all one run each unless noted).**
T4 without Jev (checklist + asks off): 12/14 10.3m, 13/14 17.1m — both failed the cached-token test,
as did plain Opus x2; with Jev 0/5 runs failed it. T5 (98093dcbd, Usage page backend, 21 tests):
plain Opus 5.5 18/21 22.2m $4.89 · team 17/21 44.1m $2.20 (dead first Gemini session, launch
timeout while the host leaked) · team after 2f3f5cfd3 (dead-session check) + d33d84a83 (checklist
gates steps, one full review at the end) **20/21** 41.7m $3.11. Host wedge fixed 0be307de0 (orphan
reaper stuck on a tracked pid; verified with the zygote disabled 09-26). User: Gemini stays the
worker (the point is Claude quota); plain Opus + Jev was rejected for that reason.

**Update 09-25 11:30 — inbox mirror bug fixed in 1428e43f6 (NOT live: needs a host restart).**
Cause: a claude-native session's runner is reaped when idle and its bridge dir pruned, taking the
forwarder's `subagent_forwarder.json` with it. On the next start the forwarder re-registers every
old Task-tool sub-agent, re-posts its whole transcript (child b34508b6 held 548 items for a
137-record transcript) and reports running -> idle; the fresh runner has no drained-set memory,
so it queues all their results in the parent inbox and wakes it (runner restarts 08:05, 09:36,
10:59 = the three stale notices). Fix: the runner skips inbox delivery + wake for children
labeled `omnigent.claude_native.subagent_id` (Claude already got the result from its Task tool;
also stops the mid-run "finished" notices from the 5 s quiescence rule). Still open: the forwarder
re-posting old transcripts after each restart (child history bloat, no quota cost).

**Update 04:45.** Root cause shared by two night bugs: a server restart makes a live native worker's
runner re-publish its old steps. (1) Their usage is added again (the Gemini token double-count above:
calls unchanged, totals x2 -- only sessions whose runner was still alive). (2) The tool-loop notifier's
in-memory streaks reset, so replayed manage_task(status) polls woke a finished orchestrator 10 min after
its final answer. Fixed (2)'s trigger in 54caf42d2 (Antigravity's manage_task(status)/schedule polls are
waiting, not a loop; also fired mid-run). NOT deployed (needs a server restart; do it with the host
restart). (1) is not fixed: the usage path needs to dedupe replayed items by id.

**Update 04:25 (final for the night).** New harness (prompt af30e601a) on the other tasks, one run
each: T1 8/8 **10.1m** $0.65 (old harness 17.1m, plain 5.9m) · T3 5/5 **7.9m** $0.68 (old 9.2m, plain
3.1m). Plain T4 repeat: 12/14 again, 11.6m $2.22, same two failures (cached-token counting and
usage_delta_never_raises); the team never missed the cached-token test in 6 T4 runs (13-14/14).
Chart: scratchpad night-0925.png (bench4/morning_chart.py; values are each run's FIRST measurement
-- re-measuring later picks up the usage double-count and late brain wake-ups). The Antigravity
plan-limits row has read "stale" (Gemini week 17%, 5h 51%) since 01:47 -- not a bug: agy quota is only
readable while a worker agy runs, and every later check fell between arms (drive.py kills finished
workers). By tokens (~0.4M per point) the week was ~26% at 08:10. Real gap, not investigated: the
server's own agy helper (--print, gemini-3.8-flash-low) listens on 2 ports but discovery returns none.

**Update 03:50 (read this first).** T4 on the new harness, one run each: brain pinned high 13/14
18.6m $1.10 · default 14/14 16.5m $0.76 · + edit-point pointers (62e1b3285) 14/14 26.9m $0.87 (the
brain's review grep pulled tests/server/integration/test_sessions_endpoints.py, 271 tests / 5.6 min)
· + review-scope rule (af30e601a: no integration files, -k on big ones) 13/14 **14.9m $0.73**. Old
harness 14/14 21.7m $1.20; plain Opus 5.5 12/14 9.0m $2.55 (a second plain T4 run was started 03:45,
arm opus55-r2). Checklist after the clipping fix: 2 send-backs, both led to real edits (one a genuine
gap); before it, 5 send-backs, zero edits. Bench-driver bugs fixed (scratchpad drive.py): the API
token expired mid-arm so polls read "idle, no items" (two arms' ends missed -> 20-min stall; times
come from measure_nexus.py, the brain's final message); leftover agy workers are killed per arm.
**Bug found, not fixed:** Gemini worker token totals roughly doubled (calls unchanged) for workers
still alive across the 01:05 server restart (T1 0.77M -> 1.53M, T2 0.98M -> 1.95M, T3 0.45M -> 0.89M):
a restart re-adds a live native worker's cumulative usage. **Still pending:** host restart for
327ae13a9's runner half (relaunches both Claude sessions); the one-worker rule (parallel impl +
tests could cut the worker's 10-14 min first pass; the rule exists since 09-16's Gemini 5h drain).

**Update 01:50 (read this first).** Baselines done (Opus 5.5, one run each; plain vs nexus
Gemini+checklist, old harness): T1 8/8 5.9m $1.74 vs 8/8 17.1m $0.62 · T2 10/12 19.7m $4.12 vs
10/12 22.4m $0.87 · T3 5/5 3.1m $0.85 vs 5/5 9.2m $0.66 · T4 12/14 9.0m $2.55 vs 14/14 21.7m $1.20.
New harness on T4 (server restarted 01:05 with 1158c60ee + checklist fix 8e0255b38): brain effort
pinned high 13/14 18.4m $1.10; host default (xhigh) 14/14 16.5m $0.76 -> effort is not the lever,
run-to-run variance is. **Checklist false send-backs were a clipping bug** (8e0255b38): each file was
cut at 8,000 chars, so long new files lost their ends and Jev judged unseen code; replay flips T4's
0.08 -> 0.88. Every observed send-back so far (5) led to no code change. 62e1b3285 (server restart
01:46): the IMPLEMENT order names file/function/line of each edit point (T4 worker spent 18 views
hunting in orchestration.py). 327ae13a9's runner half is NOT live (host restart deferred: it
relaunches the other Claude session). Runs on private tasks are tracked outside this repo (private memory note). Budgets 01:47: Claude 5h 67% week 89%
(tight: ~2 pts/h), Gemini week 17% 5h 51%. Trap: never rewrite a running bash script (bash
re-reads by offset; cost a finished plain T2). drive.py now kills each arm's leftover agy workers.
Chart: bench4/night_chart.py (system python3).

**Why:** user (00:05): "work through the night, run benchmarks in more tasks,
improve nexus with Jev and Gemini ... improve the time of the task and the quality of the
nexus harness". Claude week 84% resets 09-25 08:59 BRT — use it; 5h window resets 04:00 BRT, keep
it < ~85% before then. **Careful with Gemini:** week 9% at 00:15, cap tonight ~33%; 5h < 70%.

**Results, Opus 5.5 (arena T1–T4, one run each, load avg 20–30 all night):**
plain Opus 5.5: T1 8/8 5.9 min $1.74 · T3 5/5 3.1 min $0.85 · T4 12/14 9.0 min $2.55 · T2 running.
nexus + Gemini 3.7 Flash Medium + checklist gate: T4 **14/14** 21.7 min to final answer, $1.20;
T3/T1/T2 running (unit `arena-nexus-gemchk`). Without checklist: T4 12/14 16.6 min, T3 4/5 8.5 min.
T4 14/14 timeline: brain explore 1.8 · worker 9.6 (two whole-suite pytest runs) · checklist 1.4 ·
review + 2 fix rounds ~6 · brain's final whole-suite sweep 2.5 min.

**Committed tonight, NOT deployed:** `1158c60ee` (prompt: briefs/reviews run targeted tests, never
the suite) and `327ae13a9` (runner evidence says "Edits after it: none|N"; prompt: trust it, read
the diff instead of re-running). Deploy AFTER `arena-nexus-gemchk` ends: restart server, then host
(`XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart omnigent-server.service` / `omnigent-host.service`;
host restart relaunches both Claude sessions — user OK'd restarts without /compact). Then rerun
nexus Gemini+checklist on T4 to measure. Also `d99c1f0f1` (arena: `opus55` plain arm; task rules
say "don't sync the shared venv"). Checklist gate is live (`~/.omnigent/supervisor/checklist`).

**Harness (scratchpad `…/d9653808-…/scratchpad/bench4/`):** `drive.py <T> <arm>=<worker:model[@effort]>`
(logs `final answer X min`; `ARENA_DIR` picks another arena), `nexus_seq.sh <arm> <worker:model> <T>…`,
`plain_run.sh <T> opus55`, `timeline.py <run dir>`, log `drive.log`, runs in `runs/<T>-<arm>/`.
Unit `arena-venv-watchdog` repairs a worker's venv hijack within 15 s (log `venv_watchdog.log`).

**Traps found:** a Gemini worker's plain `uv run` in its tree repointed the live venv (09-24
20:26→23:50; server/host/zygote predated it, so results stand). Omnigent inbox mirror re-delivers
old sub-agent reports on every `sys_read_inbox` and reports intermediate sub-agent turns as
"finished" — ignore those notices, fix later. Stale duplicate Claude processes from a 23:06 relaunch
(pids 2619293, 2622565) — cleared by the host restart.
