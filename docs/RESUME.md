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

**Open:**
1. User to try /unmute (delay, barge-in, voice) — then decide keep/remove.
2. Disk ~6 GB free. pi05 training (openpi — NEVER touch) wrote ~25 GB last
   night and will fail if it runs again. Candidates the user has NOT approved:
   Isaac shader caches 30 GB (stop Isaac first), spot-teleop venvs in git
   history ~6 GB (blocked: 9 stashes, 12 uncommitted changes and an unpushed
   `master` there), friend-clone-test 3.8 GB, journal 3.7 GB (sudo).
3. T4-codex benchmark never used codex: labels `team.worker=codex` were set but
   both workers spawned as `gemini:`. Trace `resolve_worker` /
   `worker_choices` in `omnigent/team_worker.py`.
4. Qwen-Omni-Realtime (Alibaba, Singapore region, 90-day free quota) is the
   cloud fallback option if Unmute doesn't satisfy; needs the user's account.

**Traps from today:** a daemon started while a venv was hijacked keeps the
bench-tree interpreter path (check `/proc/<pid>/cmdline` before deleting bench
trees); containers write root-owned files (delete via
`docker run --rm -v ... alpine rm -rf`).
