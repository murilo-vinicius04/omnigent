# RESUME — start a fresh session from this file

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
1. Laya test (user: worth it). `~/laya-test/.venv` (laya 0.3.6, torch cu130); `decisions.jsonl` from
   `build_dataset.py`: 306 companion routing decisions, 50 kept / 256 to Claude (84% baseline; some are
   bench prompts). Next: untuned `laya` + `laya-multilingual`, chart agreement and latency.
2. Installer: `uv sync --extra all` fails on a fresh clone (chatterbox-tts via gradio 6.8 needs
   starlette<1.0; omnigent needs >=1.0.1). `FRIENDLY_LAYER_SETUP.md` wrongly says a server restart
   interrupts sessions.
3. Older: model picker silent no-op; language cache not cleared on change (`spoken_summary.py:654`,
   60 s); user runs `sudo bash ~/cleanup-sudo.sh`.
4. XR hub: `petrobras-chains/xr/serve.py --spot <crawl-lab/xr>` serves chain `/` + Spot clips `/spot/`
   on 8731 (nohup, `xr/hub.log`); idea: port the rollout viewer into the shell as a second app.
