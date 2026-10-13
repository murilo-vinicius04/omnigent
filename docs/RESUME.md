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

## 2026-10-13 18:05 — READ FIRST (Hub evening; session d9653808, conversation 247c77f5 = project "omnigent")

**State:** everything below is committed and live (server restarted 18:03:38, page = clean `git archive HEAD web`
build, last code commit e53b54f99). Nothing running, no benchmark. The user drives this chat FROM THE HUB (voice
or typed, pt-BR); the Hub forwards here and reports our final reply back (summary + links, read aloud), so keep
final replies short. Answer in English.
**Done today 16:10→18:05 (details in the block below):** Hub file links open in the Hub's own right rail; Hub
messages narrated (speak_only spoken summaries); watch no longer reports "finished without a reply" while waiting
for a summary; router retries Google 503s; Hub voice calls hang up after a send (GPT path 3975f8f28, Gemini/Unmute
relay path 59abca6b9) and show the running tool; link address only from browser Origins; companion read-only checks
survive cold starts (40 s first turn + fresh-process retry).
**18:35 fix 9c4254ef5 (page only, deployed, no restart):** the 18:18 Gemini Hub call was hung up by the page's
"no reply 30s after speech" watchdog 99 ms after the user last spoke; Gemini had sent ACTIVITY_START with no
ACTIVITY_END (still listening). The owed-reply clock now holds while the reader talks (voiceActivity frames, or
mic level), and the not-hearing notice is skipped while Gemini's detector is open (3.8 transcribes only at turn
end). Hang-up stats carry floorMs. Not yet seen on a real call.
**18:58 99e0ed153 (server restarted 18:58:23):** Hub "finished" reports are now the project's own spoken summary
word for word (user: "resumo do resumo" lost information). The router only translates a summary in another language
(whole) or summarizes the reply when no summary arrived. Dry run on the live DB (scratchpad hubreply/dryreport.py
<chat> <name> <since>): RAL's disk reply → its 923-char summary + table + file links, router not called.
**19:20 5162de080 (page only, deployed):** Gemini/Unmute Hub calls sent each request twice (RAL 19:07, 19:12): the
relay's ask_claude ran, then 3.8's late transcript of the asking turn was filed after the send and forwarded as
"Also, said right after that…". The adapter now files reader words on toolCall and before onRelaySent.
**19:37 8484e015f (server restarted 19:36:50, page deployed):** narration drifted into pt-PT/Spanish because no
voice prompt named a language (Google native-audio voices take no languageCode; docs: restrict via system
instructions). Every live voice (Gemini narrate/call/hub call, GPT call/narrate) now gets
live_voice.speech_language_rule(<chat's language selector>): label spoken_summary_language → project → server
default OMNIGENT_SPOKEN_SUMMARY_LANGUAGE=pt-BR; narration passes language_of=<chat> + lang=<summary lang>. Log line
"gemini live language | mode=… language=…". Kyutai/Unmute TTS is not covered (own voice).
**20:05 20e95f320 + 696236ce9 (page only, deployed):** Hub "finished" reports were not narrated: the page merges
consecutive assistant messages (the hub's, each its own turn) into ONE bubble with the newest turn's id; the
narration hook took the bubble's last summary (re-read the previous "sent" msg at 19:27, filed RAL's report as
history at 19:32) and stopped a reading when its id left the bubble (omnigent report cut 2.6 s in at 19:37:58).
Fix: text items carry turnId; hook reads only the newest turn's own summary; stop only if the turn is gone;
speak_only messages queue (waitTurn) instead of replacing. Also: ReadAloudButton under each hub message; plan-limit
rings in the Hub Projects panel header (ChatPage's status row hides with no host/branch/ring, and ChatPage.tsx
has the other session's uncommitted edits, so not touched). Verified headless: 4 rings, 40 read-aloud buttons.
**20:08 9d82aea91 (server restarted 20:07:53, page deployed):** user disliked hearing/seeing "RAL finished:". Hub
messages about a project now open with a chip (markdown link titled "omnigent-project" → ChatMarkdown ProjectChip,
folder icon, opens /c/<id> in-app); "Open X ↗" footer gone; narration reads hub_chat.own_words (no label). Stored
entry.text keeps the label (router memory, voice-call news). Old messages keep the old look.
**Waiting on the user / not tried live:** a real Gemini voice call in the Hub ("send to RAL …" should hang up;
"check on RAL …" should show "Checking on RAL (read-only)…" and answer).
**Offered, not built:** retry a timed-out spoken summary once; backup router off Google for >90 s outages;
double narration (project's own summary + Hub report) when a project isn't muted; Hub chat shows nothing of what
was SAID on a Gemini call (server tools post only results). Older: close PR #6479 + delete fork, block bench
GitHub (needs explicit yes); rotate the OpenAI key.
**Measured, costly to redo:** agy cold start can spend 15 s in Google sign-in (loadCodeAssist) before reading
stdin; Google "Eligibility check failed: UNAVAILABLE (code 503)" outages lasted ~1 min (16:38, 17:13); a check is
20 s per model turn / 60 s total; Flash Low router turns 2–13 s; the Hub's label `voice_backend: live` (Gemini Live
reads its messages, no TTS recording).
**Tools (scratchpad d9653808…/):** `hubapi.py GET|POST <path> [json]`, `hubtype.py "<text>"` (types into the real
Hub — moves nothing now that only Origin callers set link addresses), `hubreply/rail.py [width]` (file link → rail),
`hubreply/narr.py "<text>"`, `turnat.py CHAT SINCE AT` (replay the watch lookup). Live check:
`hubapi.py POST /v1/discussion/<chat>/check '{"text": "..."}'`. Logs: `~/.omnigent/logs/server/` (narration
decisions, `voice server tool |`, `companion check`), agy `~/.gemini/antigravity-cli/log/cli-*.log`.
**Rules:** format/commit files BY NAME (a dir-wide ruff format touched the other session's routes_core.py at 17:55,
layout only, left as is); SKIP=pyrefly,no-hardcoded-models,web-oxlint,web-tsc after running pyrefly on own files;
never co-author trailers; restart `XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart omnigent-server.service`.

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
