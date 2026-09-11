# Friendly layer — session handoff (2026-09-10 / 09-11)

Everything done in this session, everything still queued, and the environment
facts that are easy to get wrong. Written so a fresh context can pick up
without re-deriving any of it.

Branch: `feat/friendly-layer`. Ten commits, `518ce2900..03a13968e`, 56 files,
+4470/−737. **Nothing is pushed** — the branch has no upstream, deliberately.

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

## 3. The queue, in the reader's priority order

1. **Progress updates while Claude works** — "Claude is doing X now", so the
   reader can follow a long turn instead of waiting blind. Needs a live path;
   everything today is end-of-turn.
2. **Auto-compact at a threshold** (~60%), with a prompt to Claude *before*
   compaction telling it to document its context so nothing is lost, plus a
   configurable percentage in the Omnigent UI. Motivation: cost, and not
   wanting to think about compaction.
3. **Gemini answering directly when Claude is not needed** — a minor question,
   a clarification, a word. Discussed: must be biased toward forwarding, show
   who answered, and offer one-click "ask Claude anyway", because a confident
   wrong answer without the repo context is the failure mode.
4. **Permanent systemd unit + linger**, and the `voice-agent` memory line that
   still points the console at `omnigent-fork` (needs the reader's OK).
5. **`answer_language_instruction` is dead code** (`inbound_translation.py:53`).
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

- **agy startup is ~4–5s regardless of prompt size** ("reply ok" costs the same
  as a real rewrite). So streaming Gemini's tokens saves nothing; the win would
  be a warm process (`--input-format stream-json` runs a turn per NDJSON line).
- **End-to-end wait before audio** was ~45s median (rewrite ~5–10s, synthesis
  the rest). Decoupling removed it from the *text*; the voice still takes ~45s.
- **Narration pace**: ~15 chars/s. 1200 chars ≈ 80s.
- **Summaries never hit the old caps**: across 224 historical summaries the
  longest was 1100 chars against a 2000 cap. The limiter was always the brief,
  never the clamp.

---

## 5. Verification

```bash
cd /home/nexus/wt/friendly-layer
.venv/bin/python -m pytest tests/server/test_spoken_summary.py \
    tests/server/test_summary_tts.py tests/server/test_dictation_whisper.py \
    tests/server/test_inbound_repair.py tests/server/routes/test_dictation.py -q
cd web && npx vitest run src/ && node_modules/.bin/tsc -b && npx vite build
```

Current: 97 server tests in the summary/voice set, 6916 web tests, 0 type
errors. `pre-commit run --all-files` fails only on pre-existing
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
