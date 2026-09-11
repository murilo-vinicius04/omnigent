# Friendly layer — session handoff (2026-09-10 / 09-11)

Everything done in this session, everything still queued, and the environment
facts that are easy to get wrong. Written so a fresh context can pick up
without re-deriving any of it.

Branch: `feat/friendly-layer`. Twelve commits, `518ce2900..2a8a55ba2`.
**Nothing is pushed** — the branch has no upstream, deliberately.

**Restore point:** tag `friendly-layer-known-good` and branch
`backup/friendly-layer-known-good`, both at `dc02017b1` — everything through the
summary/narration work, before live voice. `git reset --hard
friendly-layer-known-good` puts it all back.

---

## 1. How this deployment actually runs

| thing | value |
|---|---|
| server | `omnigent-server.service`, a **transient** systemd *user* unit |
| runs from | `/home/nexus/wt/friendly-layer` (NOT `omnigent-fork`) |
| host daemon | `omnigent-host.service`, from `/home/nexus/omnigent-fork` |
| reader's URL | `http://localhost:6767` via `ssh -N -L 6767:127.0.0.1:6767` |
| GPU | L40S, 44 GiB; Chatterbox ~3.1 GiB, Whisper ~1.6 GiB |

Restart (needs `XDG_RUNTIME_DIR`, else systemctl cannot find the user bus):

```bash
export XDG_RUNTIME_DIR=/run/user/1000
systemctl --user restart omnigent-server.service
```

Env the unit carries: `OMNIGENT_SPOKEN_SUMMARY_ENABLED=true`,
`OMNIGENT_SPOKEN_SUMMARY_LANGUAGE=pt-BR`, `OMNIGENT_DICTATION_ENGINE=whisper`,
`OMNIGENT_DICTATION_PREFER_SERVER=1`, `OMNIGENT_DICTATION_WHISPER_LANGUAGE=en`.

**Traps, all hit at least once:**

- **A transient unit dies on reboot.** After the 09-11 reboot another Claude
  session (project `-home-nexus-voice-agent`) restarted the console from
  `omnigent-fork`, which has none of this work. Its memory
  (`voice-agent/memory/omnigent-console-restart.md`) still says the console
  lives there — **unedited, the reader has not approved changing it**. A
  permanent unit + `loginctl enable-linger nexus` is the durable fix; not done.
- **Web changes need `npx vite build` only** (output goes straight to
  `omnigent/server/static/web-ui/`, which the server serves from disk). Server
  changes need a restart. `--emptyOutDir` wipes `static/web-ui/voice-ab/`, the
  demo audio; re-copy from `/home/nexus/omnigent-voice-ab/*.mp3` after a build.
- **`tsc -p tsconfig.json` checks nothing here.** The real check is
  `cd web && node_modules/.bin/tsc -b`.
- **Browser storage is per origin.** Moving between the tailnet IP and
  `localhost` silently resets every browser-side preference. This caused the
  long-running "autoplay works sometimes" complaint.
- **GPU driver**: an unattended update installed a DKMS-built NVIDIA module
  signed with a local key Secure Boot does not trust, so after the reboot the
  driver refused to load. Fixed by `sudo apt remove nvidia-dkms-580` (Ubuntu's
  own signed module then loads). If the GPU vanishes again, check this first.

---

## 2. What shipped

### Voice (`omnigent/server/tts.py`)

Chatterbox, stock voice. Was: a pitched-down clone of its own output, read at
20 chars/s, smeared by a phase vocoder, stuttering and truncated at 40s.

- Stock voice, Chatterbox defaults (`exaggeration 0.5`, `temperature 0.8`,
  `cfg_weight 0.3`). The `~/.omnigent/voice-reference.wav` clone is retired.
- **Chunked at 300 chars.** The 1000-token (~40s) ceiling silently guillotined
  long summaries; the alignment analyzer is also rebuilt per chunk because it
  pins itself to the first generation's text length and then reads a stale
  slice (`stack expects each tensor to be equal size`).
- **Runaway reroll**: a chunk whose audio is >1.6× the duration its text
  implies is regenerated (max 2 retries, shortest take wins). Sampling
  occasionally rambles past the text and loops.
- **ffmpeg does tempo + MP3 in one pass** (`atempo=0.85`, 96k mono). A phase
  vocoder (`librosa.time_stretch`) was the "robotic echo" the reader heard —
  never reintroduce it. 4.4 MB WAV → ~370 KB MP3, which also stopped the
  browser stalling mid-download.

### Dictation (`omnigent/server/dictation_whisper.py`)

Whisper `large-v3-turbo` on the GPU, behind Omnigent's existing engine
registry. Benchmarked on eight of the reader's real messages:

| engine | WER | their terms | per message |
|---|---|---|---|
| Whisper turbo | 5.7% | 10/13 | 0.17s |
| **Whisper + vocabulary** | **3.9%** | **13/13** | **0.13–0.16s** |
| Parakeet v2 | 5.2% | 10/13 | 0.24s |
| Nemotron (built-in) | 8.4% | 11/13 | 0.98s |

- Vocabulary: `~/.omnigent/dictation-vocab.txt`, read fresh per take.
- Silero VAD cuts utterances. **Threshold lowered to 0.35** — the default 0.5
  dropped quiet word endings entirely (a trailing "isn't" never reached
  Whisper). **A short piece is never transcribed alone**: 0.44s of audio with
  no context comes back as "Thank you." or "Loud."; it is joined to the
  previous piece and the words already shown are trimmed off the front.
- CUDA libs are preloaded from PyTorch's wheels via `ctypes` because
  CTranslate2 dlopens `libcublas.so.12` / `libcudnn.so.9` by name.
- Not measured on the reader's real voice. Only synthetic (Chatterbox) audio.

### Inbound repair (`omnigent/server/inbound_translation.py`)

The pass used to be skipped entirely for an English reader — exactly the person
who needs dictation repair. Now: another language → restate in English;
English → repair in place (a separate, deliberately minimal prompt). Costs
~5s per message (agy startup dominates; see §4).

### Narration (web)

- **Follows the reader, not the open conversation** (`crossSessionNarration.ts`):
  listens to the session-updates socket and speaks a finished turn's summary
  whichever conversation is on screen. Bounded to summaries <5 min old;
  snapshot frames ignored so a reconnect never reads a backlog.
- **A queue**: two conversations finishing together play in turn. A newer
  summary from the *same* conversation still replaces the old one.
- **The session's volume is the only switch.** Muted = off. The device-wide
  "Speak responses" setting and the separate per-session boolean are **deleted**
  — three controls could silence the same summary and the browser-storage one
  reset itself across origins.
- **No host speech engine anywhere.** The robotic browser voice is gone from
  autoplay, the read-aloud button, the skim line and the error fallback. A
  summary with no recording waits; it is not spoken and not marked spoken. A
  refused `play()` (autoplay policy) unmarks it so the button still works.

### Summary (`omnigent/server/spoken_summary.py`, `summary_blocks.py`)

- **Whole turn, not the last message.** A native turn is one item per assistant
  message and the idle edge carries only the final one, so a six-message turn
  was summarized from its closing status note (`_native_turn_text`).
- **Text ships first, audio follows.** Synthesis took ~45s median; the written
  summary now lands in ~5–10s with `audio_pending: true`, and the recording
  arrives as a second item. The play control says "Recording it now — one
  moment." Both items carry the `show` blocks so either half stands alone.
- **Length**: measured 46s (too short, dropped endings) → 91s (overcorrected) →
  **~65s now**, target ~60s. `REWRITE_MAX_CHARS = 1200` (~80s at 15 chars/s),
  9 sentences. The cap is a runaway guard; the brief does the real work.
- **Show, don't narrate**: tables, images, links, short output and code are
  extracted from the raw text and offered to the rewriter, which picks with a
  trailing `SHOW: 1, 3` line (stripped before speaking). Rendered under the
  summary, never spoken. **Attached files always show** — no judgement call.
  Reader chooses kinds in Settings; code off by default. Remote images are
  linked, not embedded (the transcript refuses remote images).
- **Pending work**: an artifact's live-update watch is no longer counted as
  work, and running jobs are named rather than the whole reply being recast as
  a progress note.

---

## 2b. Live voice (gpt-live-1) — stage one, working

A second channel, separate from narration: a conversation you open on purpose.
Narration reads a finished turn; this is for talking. **Confirmed working by the
reader on 2026-09-11.**

| | |
|---|---|
| probe page | `http://localhost:6767/v1/live/test` (not in the chat UI) |
| handshake | `POST /v1/live/offer` — server attaches the key, browser never sees it |
| modules | `omnigent/server/live_voice.py`, `routes/live_voice.py`, `routes/live_voice_page.py` |
| key | `~/.omnigent/openai-key` (0600) or `OPENAI_API_KEY` |
| credits | $5 added 2026-09-11 = ~100 minutes; spent so far < $0.05 |

**API facts, all confirmed against the live endpoint, not guessed:**

- Request shape: `{"transport": {"type": "webrtc", "sdp": ...}, "session": {...}}`.
  `session.model` is **required**; `instructions` accepted; voice goes at
  `session.audio.output.voice` — a top-level `session.voice` is **rejected**.
  Unknown keys are rejected outright, so probing is cheap.
- **WebRTC only.** `"Only the webrtc transport is supported."`
- **No ephemeral-token dance needed** — the server forwards the offer directly.
- **There is no way to list or terminate a session.** `DELETE` and `/close` both
  404. Only the peer connection dropping ends one. Session lifetime is therefore
  entirely the client's job: the page closes on `pagehide`/`beforeunload`, and
  `MAX_SESSION_S` (30 min) is a runaway guard, not a UX timeout.
- **Billing is wall-clock**: silence costs the same as speech. $0.05/min.
- **Spend cannot be monitored programmatically** — the org usage endpoints need
  `api.usage.read` and this project key lacks the scope. The page estimates from
  elapsed time; the real number is on the dashboard.
- Measured handshake: HTTP 201 in 877 ms, ICE connected, `session.started`
  received, clean close.

The probe page is deliberately built around the failure that sank an earlier
Gemini Live attempt — a session that stops listening without saying so. Hence
the always-visible state badge, a **local mic meter** (bar moves but no reply =
the far end died, not your mic), and a watchdog that calls a silent session dead.

Served from a route, not `static/web-ui`, because `vite build --emptyOutDir`
erases that directory.

**⚠ The API key in `~/.omnigent/openai-key` is compromised** — it was pasted in
chat, so it is in the transcript, in `chat.db`, and went through the inbound
repair pass to Google. Rotate it when testing is done.

---

## 2c. Warm agy — measured, and it unblocks stage two

The blocker for a Gemini backend was that `run_agy_prompt` spawns a fresh
`--print` process per call (`spoken_summary.py:878`), costing 4–5s regardless of
prompt size. In a live session that is 4–5s of paid dead air per exchange.

**A warm process fixes it.** Measured 2026-09-11:

```
turn 1:  3.1s  (startup)   'ok'
turn 2:  1.0s              '42'    <- context retained
turn 3:  1.0s              'done'
```

Invocation:

```bash
agy --print= --input-format stream-json --output-format stream-json \
    --model gemini-3.8-flash-low --disable-slash-commands
```

**Input shape is load-bearing** — one line of NDJSON per turn:

```json
{"event": "user", "message": {"role": "user", "content": "..."}}
```

`{"type": "user", ...}` fails with *`stream input message is missing the "event"
field`*. A `user` event without a `message` key fails too. Output arrives as
`{"event": "result", "result": {"status": ..., "response": ...}}`.

Bonus nobody has cashed yet: the **inbound repair pass pays that same 4–5s cold
start on every message the reader types**. Warm, it would be ~1s.

---

## 2d. The companion — stage one, built and openable

One warm `agy` per Omnigent session, per the design the reader settled on
2026-09-11. Open it at:

```
http://localhost:6767/v1/discussion/test
```

| thing | where |
| --- | --- |
| session manager | `omnigent/server/discussion.py` |
| routes | `omnigent/server/routes/discussion.py` |
| probe page | `omnigent/server/routes/discussion_page.py` |
| tests | `tests/server/test_discussion.py` (28) |

**The one idea that makes the rest fall out: the ledger is the memory, the
process is a cache.** Every note, question and answer lands in a
`ContextEntry` list on the session — *that* is the conversation. The
subprocess holds the same history only as a warm copy. So any process death
is recoverable: a crash, a timeout, an idle reap, a server restart. The next
question spawns a new process and replays the ledger into it. This is why the
code is free to kill the process whenever its state is in doubt (notably after
a timeout, where a late answer would otherwise pair with the *next* question).

The ledger is also exactly what the UI shows, so "what does it know?" has one
answer rather than one per surface.

**Notes cost nothing until they are needed.** `session.note(...)` does not
touch the process — it appends to the ledger and returns. Undelivered notes
are folded into the next question as a briefing block. So narrating "Claude is
running the tests" is free, and the context arrives exactly when it becomes
relevant. The probe page shows undelivered entries dashed and dimmed.

**Cold start is one turn, not three.** The first version sent the role, then
the ledger replay, then the question — three turns, **8.3s**, *worse* than the
4–5s one-shot this replaces. Folding all three into one message brought it to
**3.5s**. `prewarm()` exists to pay even that ahead of time: call it when a
voice channel opens and the reader's first question is a warm ~1.1s.

API, all under `/v1/discussion/{session_id}` and all returning the whole ledger
so a UI panel cannot drift: `GET` (state), `POST /note`, `POST /ask`,
`POST /prewarm`, `POST /close`.

Lifecycle is wired into the server lifespan (`app.py`): a 60s sweep reaps
processes idle past `OMNIGENT_DISCUSSION_IDLE_S` (default 15 min), and
`close_all()` on shutdown means a restart never orphans an `agy`. Env
overrides: `OMNIGENT_DISCUSSION_AGY_BIN`, `OMNIGENT_DISCUSSION_MODEL`,
`OMNIGENT_DISCUSSION_IDLE_S`.

**Not done:** nothing feeds it from a real session yet — the probe page is the
only thing calling `/note`. Wiring the summaries and Claude's activity in, and
connecting it to the live voice channel, is the next step.

---

## 3. The queue, in the reader's priority order

1. **The companion — stage one is built (§2d); wiring it up is NEXT.** The
   warm session manager, its routes and its probe page exist and are tested.
   What remains is the part that makes it real:
   - **Feed it from an actual session**: call `note("summary", ...)` when a
     spoken summary is generated, and `note("activity", ...)` as Claude works
     (which is item 2 — the same hook serves both).
   - **Show the ledger in the Omnigent UI**, not only on the probe page. The
     reader asked for its context to be visible; `GET /v1/discussion/{id}`
     already returns exactly what the panel needs.
   - **Wire it to live voice (§2b)**: `prewarm()` when the channel opens, then
     `ask()` per utterance. Latency budget is the thing to watch — ~1.1s warm
     plus the live model's own turnaround.
2. **Progress updates while Claude works** — "Claude is doing X now", so the
   reader can follow a long turn instead of waiting blind. Needs a live path;
   everything today is end-of-turn. Natural fit for the same warm session.
3. **Auto-compact at a threshold** (~60%), with a prompt to Claude *before*
   compaction telling it to document its context so nothing is lost, plus a
   configurable percentage in the Omnigent UI. Motivation: cost, and not
   wanting to think about compaction.
4. **Gemini answering directly when Claude is not needed** — a minor question,
   a clarification, a word. Discussed: must be biased toward forwarding, show
   who answered, and offer one-click "ask Claude anyway", because a confident
   wrong answer without the repo context is the failure mode.
5. **Permanent systemd unit + linger**, and the `voice-agent` memory line that
   still points the console at `omnigent-fork` (needs the reader's OK).
6. **`answer_language_instruction` is dead code** (`inbound_translation.py:53`).
   `ANSWER_IN_ENGLISH_INSTRUCTION` never reaches a prompt, so "always answer in
   English" is convention, not enforcement. Wiring it needs both the runtime
   path (`runner/app.py`) and the native launch prompt, which lives in the
   `omnigent-fork` checkout on another branch.

Explicitly dropped: **parallel TTS**. Threads gave 0.56×/0.95× because
Chatterbox decodes token-by-token in Python and the GIL serializes it. Separate
processes would give ~1.8× but need a worker pool with a 3.1 GiB model per
worker. The reader said forget it unless it is trivial. It is not.

---

## 4. Measurements worth not repeating

- **agy cold start is ~4–5s regardless of prompt size** ("reply ok" costs the
  same as a real rewrite), so streaming its tokens saves nothing. **Warm is
  ~1.0s per turn with context retained** — measured, see §2c. Every cold
  `run_agy_prompt` call in the codebase is paying that 4–5s.
- **End-to-end wait before audio** was ~45s median (rewrite ~5–10s, synthesis
  the rest). Decoupling removed it from the *text*; the voice still takes ~45s.
- **Narration pace**: ~15 chars/s. 1200 chars ≈ 80s.
- **Summaries never hit the old caps**: across 224 historical summaries the
  longest was 1100 chars against a 2000 cap. The limiter was always the brief,
  never the clamp.
- **gpt-live-1 handshake**: HTTP 201 in 877 ms. Billing is wall-clock, so
  silence costs the same as speech; $5 ≈ 100 minutes of session time.
- **Companion turns** (2026-09-11, `gemini-3.8-flash-low`, real CLI):

  | path | cost |
  | --- | --- |
  | `note()` — record context | 0.000s, no process |
  | cold `ask()`, role + replay + question as one turn | **3.5s** |
  | the same as three separate turns (rejected) | 8.3s |
  | `prewarm()`, with nobody waiting | 6.2s |
  | first question after a prewarm | **1.1s** |
  | warm question | 1.0–1.5s |

  Restarting after a kill and replaying a 9-entry ledger: 6.4s, and the
  answer correctly said it remembered the parser bug being mentioned but
  did not know the details — "nothing really deep" behaving as designed.

---

## 5. Verification

```bash
cd /home/nexus/wt/friendly-layer
.venv/bin/python -m pytest tests/server/test_spoken_summary.py \
    tests/server/test_summary_tts.py tests/server/test_dictation_whisper.py \
    tests/server/test_inbound_repair.py tests/server/routes/test_dictation.py \
    tests/server/test_live_voice.py tests/server/test_discussion.py -q
cd web && npx vitest run src/ && node_modules/.bin/tsc -b && npx vite build
```

Current: 174 server tests in that set, 6916 web tests, 0 type errors
(`.venv/bin/python -m pyrefly check <files>`). `pre-commit run --all-files` fails only on pre-existing
`omnigent/runtime/telemetry.py` opentelemetry imports — not from this work.

Inspect what a summary actually stored (the fastest way to tell selection from
rendering):

```bash
.venv/bin/python -c "
import sqlite3, json
c = sqlite3.connect('file:/home/nexus/.omnigent/chat.db?mode=ro', uri=True)
b = bytes.fromhex('247c77f5f57748b0a2747e4b14548cac')
for cr, d in c.execute('select created_at,data from conversation_items where conversation_id=? order by created_at desc limit 40', (b,)):
    s = d if isinstance(d, str) else bytes(d).decode('utf8','replace')
    if '\"spoken_summary\"' not in s: continue
    for blk in json.loads(s).get('content', []):
        if blk.get('type') == 'spoken_summary':
            print(blk.get('audio_file_id'), blk.get('audio_pending'), [x['kind'] for x in blk.get('show', [])])
"
```

---

## 6. How this reader works (worth keeping)

- Dictates most messages, so their text carries speech-to-text noise. Read
  through it rather than answering the literal words.
- Wants evidence, not assurances: measure it, show the numbers, say plainly
  when something was not verified. They caught two wrong diagnoses this session
  ("I really said thank you"; "that's not a regression").
- One step at a time. Deliver something they can open, then stop.
- Portuguese and English both; the session language setting is `pt-BR` for
  summaries but the reader switched the session label to `en-US` at one point.
  The PT/EN toggle is in the composer.
