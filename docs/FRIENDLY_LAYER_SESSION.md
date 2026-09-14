# Friendly layer — session handoff (2026-09-10 / 09-12)

Everything done in this session, everything still queued, and the environment
facts that are easy to get wrong. Written so a fresh context can pick up
without re-deriving any of it.

**Read §2e first.** It holds the reader's own description of where live mode
is going, and it is the thing most easily lost to a compaction.

Branch: `feat/friendly-layer`, head `3cf2014a7`.
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

## 2d. The companion — it owns the composer now

One warm `agy` per Omnigent session, and **every message the reader types goes
to it before Claude sees it**. The same turn does two jobs: restate the message
in English for the harness (what the cold inbound repair pass used to do at
4–5s) and decide whether Claude is needed at all. Trivia it can answer from
what it already knows, it answers; everything else forwards.

| thing | where |
| --- | --- |
| session manager + routing | `omnigent/server/discussion.py` (`route()`, `_parse_routing`) |
| dispatch hook | `routes/_sessions/orchestration.py` — `_route_through_companion`, `_persist_companion_answer`, `_strip_force_claude` |
| summaries feed | `routes/_sessions/helpers.py` `_tell_companion` |
| ledger API | `routes/discussion.py` (`/v1/discussion/{session_id}`) |
| rail panel | `web/src/shell/CompanionPanel.tsx` (Companion tab) |
| answer marker | `web/src/components/chat/CompanionAnswerNote.tsx` |
| tests | `tests/server/test_discussion.py` (46), 9 web tests |

**What happens to the reader's words is the session's language setting, not
the companion's call.** Routing asks for three different things, matching the
inbound pass's own two gates (`inbound_pass_enabled`, `inbound_translation_enabled`):

| session language | mode | what Claude receives |
| --- | --- | --- |
| another language (`pt-BR`) | `translate` | restated in English |
| English (`en`, `en-*`) | `repair` | their own words, dictation slips fixed only |
| unset / `auto`, or the pass killed | `off` | their words, untouched |

In `off` the caller discards any `english` the model returned, so a model that
restates anyway cannot put words in the reader's mouth. Verified against the
real CLI: `off` → `None`, `repair` → byte-identical, `translate` → rewritten.
The routing decision itself is unconditional in all three.

**Everything fails toward forwarding.** An unparseable reply, a dead process, a
missing CLI, a timeout, `"forward": false` with no answer to show — all forward.
A slow answer from Claude costs seconds; a confident wrong answer from
something that cannot see the code costs much more.

**A kept message never reaches the terminal**, so the server becomes the writer
for that turn: it persists the reader's message (consuming the input) plus an
assistant message carrying a `companion_answer` content part, publishes both,
and marks the session idle. The bubble says *"Answered by the companion — Claude
never saw this"* and offers **Ask Claude anyway**, which re-sends the original
text with a `force_claude` marker that skips routing (the marker is transport:
stripped before anything persists or forwards).

**Measured** (2026-09-11, `gemini-3.8-flash-low`, 7/7 decisions correct):

```
  4.2s  kept     oi, tudo bem?                          -> answered in pt-BR
  1.3s  kept     o que voce esta fazendo agora?
  1.4s  kept     quantos testes passaram?
  1.3s  FORWARD  conserta o bug do parser no tts.py     -> en: fix the parser bug in tts.py
  1.3s  FORWARD  roda os testes de novo
  1.1s  FORWARD  what does the sweep function do?
```

Since it replaces a 4–5s cold repair pass, typing to Claude got *faster*.

**The one idea the rest falls out of: the ledger is the memory, the process is
a cache.** Every note, question and answer lands in a `ContextEntry` list on
the session. The subprocess holds the same history only as a warm copy, so any
process death is recoverable — crash, timeout, idle reap, restart — by
replaying the ledger into a replacement. That is why the code is free to kill
the process whenever its state is in doubt, notably after a timeout, where a
late answer would otherwise pair with the *next* question.

What it knows is deliberately thin: the reader's messages and the spoken
summaries, never the transcript or the code. The **Companion tab** in the right
rail shows the whole ledger, read-only — the composer is how you talk to it,
and a second input there was the mistake this replaced.

**Switches:** `OMNIGENT_COMPANION_ROUTING=0` sends everything straight to
Claude (the companion still listens). Also
`OMNIGENT_COMPANION_ROUTE_TIMEOUT_S` (8s), `OMNIGENT_DISCUSSION_IDLE_S` (15
min), `OMNIGENT_DISCUSSION_MODEL`, `OMNIGENT_DISCUSSION_AGY_BIN`.

**Not done:** no notes *during* a turn (item 2), and not wired to live voice.

---

## 2e. Live mode — what it is now, and the loop it is meant to become

### The reader's idea, in their words

> "Gemini already knows, so Gemini is telling me this, so I'll just answer it.
> But if Gemini doesn't know, I'll ask Claude. I don't want to press an enter
> button. It decides to stop discussing with the user, it already knows the
> user's intention, so it just sends the prompt to Claude, and stops
> listening, stops talking — because that way it will not have to spend
> anymore."

Three layers, each doing only what it is good at:

| layer | job | knows | costs |
| --- | --- | --- | --- |
| `gpt-live-1` | ears and mouth | nothing between sessions | $0.05/min wall clock |
| Gemini companion | what has happened | the session ledger | free, the reader's plan |
| Claude | the work | the workspace | the reader's plan |

The loop the reader wants:

1. They talk. `gpt-live-1` hears.
2. **Gemini decides**: do I already know this?
   - **Yes** → answer out loud. The conversation continues.
   - **No, this is work** → compose the prompt from what was said, send it to
     Claude, and **hang up** — stop listening, stop talking, stop billing.
3. Claude works. The meter is off for the whole of it, which is the expensive
   part: minutes of silence at $0.05/min while a turn runs.
4. The turn finishes, a summary is written, and live mode reads it aloud.

The hang-up is not a detail. It is the reason the design is affordable: the
session is open only while someone is actually talking.

### What is built today (2026-09-12)

Working, committed, and testable:

- **Narration.** Press play, or let a turn finish. Gemini writes the words,
  `gpt-live-1` reads them, ~1s to first sound. Session opens on the text and
  closes 2s after the voice stops. ~$0.016 for a 15s summary.
- **Conversation.** The mic button in live mode opens a two-way session.
  `gpt-live-1` hears, thinks **for itself**, and answers. Native full duplex,
  so interruption works. Briefed at open from the companion ledger.
- **Memory.** Both sides' transcripts are captured in the browser and written
  to the ledger as `question` / `answer`, so the panel shows the conversation
  and the next one opens knowing what the last one said.
- **Cost is visible.** A clock and running total sit in the composer while a
  conversation is open.

### The gap, precisely

**Gemini is not in the spoken conversation.** In live mode `gpt-live-1`
answers natively. It is briefed from the ledger, so it knows *what happened*,
but the judgement is GPT's, and it cannot act:

- It has no tools. It cannot message Claude, run anything, or touch a file.
  It offered to once, was told yes, and did nothing. It is now told plainly
  it cannot, so it says the reader must type it — which is exactly the Enter
  key the reader does not want to press.
- It cannot hand off. There is no path from the spoken session into the
  session's own composer.
- It cannot decide to stop. Only the reader hangs up.

The deciding half already exists for *typed* messages:
`DiscussionSession.route()` in `discussion.py` returns
`Routing(forward, english, answer)` — Gemini looking at a message and saying
"I can answer this" or "this is for Claude". The voice loop is that same
decision applied to speech, plus a hang-up.

### The hard constraint, measured 2026-09-12

**`gpt-live-1` cannot be a mouth for another model in a live conversation.**
Two runs settle it:

- Given the strongest possible instruction ("You are a voice interface, not a
  participant. NEVER answer, greet, acknowledge, or speak on your own
  initiative -- not even a filler word"), it heard a spoken question and
  answered anyway: *"Right, checking the latest now."* Suppression works when
  the session is silent, which is why narration is safe; it does not survive
  real speech.
- Text pushed in afterwards was **generated but never voiced**. The backend
  produced `response.output_text.delta` through `response.completed`, and no
  `session.output_transcript.delta` and no audio followed. The same push is
  spoken normally in a narration session, so the difference is a conversation
  that has already had an audio turn.

> **Correction, 2026-09-13.** That push used `response.item.create` and
> `response.create`, which belong to *responses* delegation. The documented
> way to put another model's words into a live conversation is **client
> delegation**: GPT-Live raises `session.delegation.created` (an id, no task
> text), the application answers with `session.commentary.append` on that
> delegation id, and the voice says it in its own words.
> `session.thinking.append` adds facts without speaking them. Omitting
> `delegation` at session start already selects client mode. See
> [Delegation and tools in GPT-Live](https://developers.openai.com/api/docs/guides/live-delegation)
> and [Managing GPT-Live sessions](https://developers.openai.com/api/docs/guides/live-conversations).
> Live mode is now built on it: `answerDelegation` in
> `web/src/lib/liveConversation.ts` and `DiscussionSession.delegate`. The
> conclusion below that Gemini "cannot be the speaking voice mid-conversation"
> is superseded -- it came from reading one wrong mechanism as a limit
> instead of checking the docs.

What this rules out: Gemini writing the words that come out of the voice
*during a conversation*. What it leaves intact:

- **Its ears are ours.** `session.input_transcript.delta` transcribed the test
  utterance exactly -- "Hey, give me a quick status update on the companion
  work" -- at no cost beyond the per-minute rate. That replaces Whisper.
- **Narration still works the other way.** One-way reading of finished text is
  a different mode and Gemini's words are spoken verbatim there. That replaces
  Chatterbox.
- **Gemini still decides.** Every utterance reaches it as text, so it can route,
  hand off to Claude, and hang up. It just cannot be the speaking voice mid-
  conversation.

So the division is: `gpt-live-1` owns the *audio* end to end -- hearing,
small talk, and speaking -- and Gemini owns *memory and judgement*, shaping
what the voice knows through its briefing and speaking directly whenever the
channel is one-way.

### Why making Gemini the voice's brain is the expensive option

The obvious move — take the brain out of `gpt-live-1` and let Gemini answer
through it — costs more than it looks:

- **Delegation flips.** Answering natively is `client` delegation, which bills
  no tokens at all. Pushing text in requires `responses` delegation and a
  reader model. (Measured, and there is no third mode.)
- **Latency roughly triples.** ~1s native, versus Gemini's ~1.2s plus the
  reader's ~1.4s ≈ 3s. And the voice clock runs the whole time, so a slow
  backend is billed twice: its own tokens, and the dead air.
- **Interruption stops being free.** Barge-in is native when the model answers
  itself. With a mouth-only session we would have to detect the reader
  starting to talk, stop playback and cancel the response by hand.
- **Turn detection becomes ours.** Native VAD decides when the reader has
  finished speaking. Mouth-only, the best signal we have is a gap in
  transcript deltas (currently 1.5s), which is crude.

So the cheap path keeps `gpt-live-1` answering, and adds Gemini *beside* it as
the thing that decides when to stop talking and start working.

### The steps, cheapest first

**Step 1 — the handoff, with GPT still answering.** After each reader
utterance (we already capture it), ask Gemini `route()` in the background:
is this work for Claude? If yes: send the composed prompt into the session as
if typed, tell the reader out loud that it is going to Claude, and hang up.
No delegation change, no latency change, interruption still native. This is
the step that removes the Enter key, and it is most of the reader's idea.

**Step 2 — close the loop coming back.** When the turn finishes in live mode,
the summary should speak itself. The path is wired (`speakLiveSummary` no
longer waits for a recording), but autoplay of a WebRTC stream has not been
confirmed in a real browser — a refused `play()` currently un-marks the
summary silently, which looks identical to nothing happening. Needs a
diagnostic before it needs a fix. Optionally reopen the mic afterwards so the
reader can answer back without reaching for anything.

**Step 3 — ~~Gemini as the voice~~. Ruled out by measurement**, see the hard
constraint above. The voice will always be `gpt-live-1`'s own. Gemini reaches
the reader through the briefing that shapes it, and directly whenever the
channel is one-way.

**Step 0, and it comes first — one Gemini, not four.** Today there are four
separate `agy` invocations and only one of them remembers anything:

| what | where | process |
| --- | --- | --- |
| repairs the transcription on the way in | `inbound_translation.py:236` | cold one-shot |
| writes the spoken summary on the way out | `spoken_summary.py:1016` | cold one-shot |
| revises the voice profile | `voice_profile.py:253` | cold one-shot |
| companion / routing | `discussion.py` | warm, holds the ledger |

The Gemini that reads what the reader said and the Gemini that summarizes the
answer are different processes that have never met, and neither is the
companion -- it is only *told* about them afterwards, as notes. Route all of
them through the one warm session and it stops being told and starts having
been there: one context that heard the question, chose to answer or forward,
and then wrote the summary of what came back. That is the reader's ask, and
it is the same principle as the ledger, applied to the work rather than to
the record.

Watch when doing it: turns in one session serialize, so a summary being
written while the reader speaks makes one wait -- today those run in
parallel. And the summary prompt is tuned as a one-shot; as a turn in an
ongoing conversation it may start referring back to things. The existing rule
still saves us if the process dies: the ledger is the memory, the process is
a cache.

### Things already established that this depends on

- `session.close` exists and stops billing cleanly; there is no way to *list*
  open sessions, so a session whose page vanished is unreachable.
- Billing is wall clock: a session held silent for 75s billed 74.
- `gpt-live-1` generates an opening turn by itself on connect. A narrator with
  nothing to read invents something and says it confidently; the instructions
  forbid speaking with nothing to say.
- The reader model must be pinned small. gpt-5 took 27.8s to first word
  against gpt-4o-mini's 1.4s, and that wait is billed as voice time.
- Handed a bare summary the reader model *answers* it. It must be framed as a
  read-aloud command, and openers ("[sigh]", "Oh.") have to be forbidden
  explicitly.

## 3. The queue, in the reader's priority order

0. **One Gemini, not four — §2e, step 0.** Route the inbound repair and the
   spoken summary through the companion's warm session so one context sees the
   whole exchange. Server-side only, no OpenAI cost, and it is the foundation
   the rest of §2e stands on.
1. **The spoken handoff — see §2e, step 1.**
   Gemini decides, per spoken utterance, whether this is work for Claude; if
   it is, the prompt is sent as if typed and the session hangs up. Removes the
   Enter key and stops the meter while Claude works. Reuses `route()` and the
   transcripts already captured; no delegation change and no latency cost.
   - **Step 2**: confirm the summary speaks itself on the way back. The path
     is wired; a browser refusing to autoplay a WebRTC stream now warns in the
     console rather than failing silently.
   - **Step 3**, only if needed: Gemini as the voice's brain. Costs ~3s per
     reply instead of ~1s, and hand-built barge-in. See §2e for why.
   - **Notes during a turn**, not only at the end. That is item 2, and the
     `discussion.note(session_id, "activity", …)` call it needs already exists.
2. **Progress updates while Claude works** — "Claude is doing X now", so the
   reader can follow a long turn instead of waiting blind. Needs a live path;
   everything today is end-of-turn. Natural fit for the same warm session.
3. **Auto-compact at a threshold** (~60%), with a prompt to Claude *before*
   compaction telling it to document its context so nothing is lost, plus a
   configurable percentage in the Omnigent UI. Motivation: cost, and not
   wanting to think about compaction.
4. ~~**Gemini answering directly when Claude is not needed**~~ — **done**, as
   part of item 1. Biased toward forwarding, shows who answered, one-click
   "ask Claude anyway". See §2d.
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

Current: 192 server tests in that set, 6925 web tests, 0 type errors
(`.venv/bin/python -m pyrefly check <files>`). `pre-commit run --all-files` fails only on pre-existing
`omnigent/runtime/telemetry.py` opentelemetry imports — not from this work.
`pyrefly` reports one pre-existing error in `_sessions/helpers.py:8379`
(`spoken_summary_part.get("show")`), present on `HEAD` before this work too.

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

## 7. Prior art, and what is already in this repo (surveyed 2026-09-12)

The reader asked what Omnigent itself does, and to look at Hermes and
"OhMyPy" before building the one-Gemini change. Findings, cheapest
conclusion first: **most of what we were about to build already exists here.**

### Omnigent's own answer: sessions and sub-agents

`examples/debby` and `examples/polly` are the pattern. A native agent is a
`config.yaml` with an `executor`, a `prompt`, and sub-agents under `agents/`.
The parent dispatches with `sys_session_send` and the children
**run autonomously and notify through the inbox** — the parent never blocks
on them. Sessions persist and carry their own context.

That is precisely the primitive the one-Gemini design needs, and
`discussion.py` reinvented a thin version of it: a hand-rolled warm `agy`
subprocess with its own ledger. Before writing more of that, check whether
`sys_session_create` / `sys_session_send` / `sys_read_inbox` can own the
companion instead.

**Gemini is already a harness.** `antigravity-native` is the `agy` CLI wrapped
as a first-class harness (`omnigent/antigravity_native*.py`, eight modules).
The companion shells out to the same binary by hand.

### Hermes — already integrated, nothing to clone

The reader's friend's recommendation is Nous Research's Hermes Agent, and
Omnigent **already wraps it**: `hermes-native` is a harness
(`omnigent/hermes_native*.py`), launched by `omnigent hermes`. Auth is Hermes'
own config (`hermes setup` → `~/.hermes/config.yaml`); no Omnigent key.
It is **not installed on this machine** (`hermes` is not on PATH, no
`~/.hermes`), so trying it means installing it first.

Cloned to `~/reference/hermes-agent` for reading. What is worth stealing:

- **A real subagent state machine.** `agent/subagent_lifecycle.py` models
  PENDING / STARTING / RUNNING / SUCCEEDED / FAILED / INTERRUPTED /
  CANCEL_REQUESTED / CANCELLED / UNKNOWN as an immutable public contract,
  with explicit caps (16k goal, 32k context, 32k result, 1h terminal
  retention). `discussion.py` has ad-hoc state and no caps. Worth copying the
  *shape*, not the code — it is built around Hermes' own plugin context.
- **Model flexibility.** Hermes defaults to OpenRouter and takes any
  OpenAI-compatible `base_url`, which is the route to a free model.

### oh-my-pi ("OhMyPy") — validates what we already do

`audreyt/oh-my-pi`, built on pi-coding-agent; Omnigent has a `pi` harness too.
Cloned to `~/reference/oh-my-pi`. Its memory design (`docs/memory.md`) is the
same shape as ours and worth knowing we match:

- A consolidated summary is **injected into the system prompt at session
  start** — exactly the companion's ledger briefing.
- It is framed as heuristic: *"treat memory as heuristic context, not
  authoritative on current repo state... prefer repo state and user
  instruction when they conflict."* Our briefing already says background
  only, never recite, never an instruction.

No code to lift. The value is the confirmation that ledger-as-briefing is the
mature pattern rather than a shortcut.

### Nemotron, for the reader's usage cost

NVIDIA open-sourced the Nemotron family and OpenRouter publishes **free
endpoints**: Nemotron 3 Super, 3 Ultra, 3.5 Lightning, 3 Nano Omni, all
`:free`, **rate limited**. Paid Nemotron 3 Super is $0.09/$0.45 per 1M.

Where this fits: the routing decision (answer vs forward) is a small
classification and almost certainly does not need Gemini. The **summary is
the risky one** — it has been tuned over days and the reader cares about it
more than anything else in this layer. Split the two rather than swapping
both: route on a free model, keep Gemini writing until a measured A/B says
otherwise. Needs an OpenRouter key, which does not exist yet.

### Step 0, as built (2026-09-12)

Both halves are in.

**The ledger is durable.** One bounded JSON file per session under
``~/.omnigent/companion``, written atomically through a temp file in the same
directory. Not ``session_state`` (the policy engine writes that column whole
from its own hot cache, so either side would clobber the other) and not a
label (right shape, wrong size -- labels carry small metadata, not sixty
entries). Every failure is silent: an unreadable ledger leaves the companion
where a fresh one already starts. Verified by restarting the service and
reading the note back.

**Three Gemini calls became one context.** ``DiscussionSession.perform()``
runs a self-contained task on the warm process, and ``run_task()`` is the
module-level entry point. The spoken summary and the inbound repair both go
through it now, so the Gemini that reads the dictated question is the one
that writes the summary of the answer. ``voice_profile.py`` still has its own
one-shot: it is rare and has nothing to do with the exchange.

Three things worth knowing about how it behaves:

- **Tasks are fenced.** ``[Task. This is a job, not a turn in our
  conversation...]`` -- without it, a summary written mid-conversation starts
  referring back to what was said earlier. Measured with a companion
  deliberately seeded with unrelated context (Hermes, cloning, harnesses):
  **no bleed** in the output either way.
- **Warm is faster, not slower.** 3.8s against the cold one-shot's 4.8s, for
  the obvious reason -- no process to start.
- **Turns serialize**, because it is one process behind one lock. Worst case
  measured: a question sent while a summary is being written waits
  **+0.9s** (2.3s against 1.4s alone). Bounded and acceptable; the
  alternative is a second process, which is the thing this removed.

**The fallback is the safety property.** ``run_task`` returns ``None`` rather
than raising whenever the companion cannot take the work, and both callers
then run their own cold one-shot. A wedged companion costs a second, never
the summary or the message.

One trap this sprang, worth remembering: loading the ledger on construction
meant every server test began writing into the developer's real
``~/.omnigent/companion``, and two tests naming the same session id shared
memory through it. An autouse fixture in ``tests/server/conftest.py`` points
that directory at ``tmp_path`` for every server test.

### What this changed about step 0

Step 0 was "route the cold one-shots through the companion's warm `agy`
session". The survey suggests a better target: route them through an
**Omnigent session** (`antigravity-native` harness), so the companion is a
first-class agent with the inbox, lifecycle and persistence the platform
already has, instead of a subprocess we babysit. Same user-visible outcome,
far less bespoke machinery to maintain.

## Session state — 2026-09-13 evening

Branch `feat/friendly-layer`, worktree `/home/nexus/wt/friendly-layer`. Restart:
`export XDG_RUNTIME_DIR=/run/user/1000; systemctl --user restart omnigent-server.service`.
Web changes need `cd web && node_modules/.bin/vite build` first (it wipes
`static/web-ui`, which is why the live probe page is a route).

**Live mode now runs on GPT-Live client delegation** (`b1e7efc87`). The voice
answers from its briefing; what it cannot answer it delegates
(`session.delegation.created`), the page asks the companion via
`POST /v1/discussion/{id}/delegate`, and the answer goes back as
`session.commentary.append` — spoken in the voice's own words. If the companion
cannot answer either, the question is sent to Claude with `forceClaude`, the
voice says so, and the call hangs up. Docs: OpenAI's live-delegation,
live-conversations and live-prompting guides. Superseded and not to be
rebuilt: per-utterance spoken routing, the "that's one for Claude" phrase
detector, browser speechSynthesis confirmations, and pushing text with
`response.item.create` (responses-mode commands, never voiced in a call).

**Summaries and autoplay.** A native turn exists under two identities — a
client id while it streams (`live:uuid`) and a server id once stored — which
broke autoplay all day: the hook read the mismatch as "the reader moved on"
and filed every reply as history before its summary arrived. Fixed in
`07c2efb4c` (only a *streaming* turn counts) and `50d1c6349` (a summary that
appears while this client watched the reply wait counts as live). Live-voice
sessions never play a Chatterbox recording (`aa73cce25`).

**Everything decides out loud now.** Summary skips log their reason
(`spoken summary skipped: … reason=…`), and the page reports every autoplay
decision to `POST /v1/narration/decision` (`d465e0f1d`). That is how the last
three bugs were found: check the server log first.

**Auto-compaction** (`6da27c7c2`): at a turn end past 60% of the context
window the session is asked to write its context down, and the turn that
answers is compacted. Threshold label `omnigent.autocompact_pct` (10–95),
kill switch `OMNIGENT_AUTO_COMPACT=0`. Claude Code's own auto-compact is much
later (~967K of 1M; `/autocompact`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`).

**Open, in the user's order:** the UI control for the compaction percentage;
progress updates while Claude works (item 2 — `session.thinking.append` can
feed a live call mid-turn); permanent systemd unit + linger; dead
`answer_language_instruction`. Also: **rotate the OpenAI key** (it was pasted
into chat), per-call cost logging, and 29GB of Qwen weights if disk is needed
again (26GB free).

**Rules learned the hard way:** read the vendor docs before designing (a whole
afternoon of workarounds existed because client delegation was never read
about), and reproduce a bug in a test before fixing it — every fix today that
skipped that step was wrong.

## Session state — 2026-09-13 late (nexus + agy worker)

**Goal.** Save Claude plan quota: `nexus` agent = Claude brain plans and
reviews, ONE cheap worker (Gemini via agy, on the Google plan) does the work.
The voice layer stays as is. Keeping session-per-task is deliberate, since that
isolation is how Omnigent is designed.

**Done (all local commits, never pushed):**
- `ae2b5b57d` UI: the context ring is the auto-compact control (threshold label
  `omnigent.autocompact_pct`, state label `omnigent.autocompact_state`).
- `examples/nexus/` now lives in the repo (`5ad33b178`, `99c93afdd`,
  `e8352982a`). Workers are `gemini` (`permission_mode: bypassPermissions`; without
  it agy's approval prompt appears only in its tmux pane, nothing in the UI) and
  `claude`. opencode is removed: the binary exists but the user says it is not
  set up. The brain writes English. The prompt tells it that worker YAMLs
  dispatched via `sys_session_create(config_path=…)` take effect live.
- `26f304aa4` fixes the reader. The cold start waits 20s for agy's RPC catalog, then
  leaves an `agy_conv_*` placeholder, and nothing ever replaced it, so every
  worker reply was invisible. `_resolve_cascade_id` now binds from
  `<bridge>/agy-home/.gemini/antigravity-cli/conversations/<uuid>.db` (exactly
  one UUID file required) and persists it. It was checked against three real failed
  bridge dirs.

**The trap that cost the evening.** Runner code is loaded by the
`omnigent-host.service` **zygote**, not the server. It dated from Sep 11,
so no Python change had loaded until the host was restarted at 22:53:03.
Check with `ps -o pid,ppid,lstart -p $(pgrep -f omnigent.runner)`. Restarting
the host interrupts every session's runner, so ask first. (Also in memory:
`omnigent-server-runs-from-friendly-layer.md`.)

**Measured:** agy did every delegated task correctly (3 of 3) once it had
bypass; all failures were on Omnigent's side.

**Open:**
1. **Unverified:** a nexus dispatch after the host restart. It has not been observed
   end to end yet. If it is still silent, read the runner log
   `~/.omnigent/logs/runner/runner-<parent session>-*.log`.
2. The bridge paste check (`_PASTE_COMMIT_TIMEOUT_S = 5.0` in
   `antigravity_native_bridge.py`) fails when a task is pasted before agy's splash
   has cleared, leaving the draft unsubmitted.
3. The agent picker shows a stale nexus description (the DB column is not refreshed).
4. The server unit is transient; use the `systemd-run` line in this doc's restart notes
   with both `--agent` flags (debby, nexus).
5. Still queued: progress updates (text in chat only, never a call held open),
   permanent systemd units plus linger, rotate the OpenAI key.
