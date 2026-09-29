# H4 evidence provenance

Not copied into the run tree (only `bug-report/` is).

## Sources

- The person's words: their chat message of 2026-09-22 23:10 (quoted in the
  task statement exactly as written, from CANDIDATES.md section 5). It names no
  chat title, so no rename was needed.
- `bug-report/server.log`: **real**. These are lines from the live server log
  `~/.omnigent/logs/server/server-20260922-202205-560168.log`, built by the
  script `h4_sanitize.py`, which is kept in the builder's scratchpad and not
  shipped. It keeps:
  - 20:22-20:33: every `?download=true` request for the chat that had the video.
    These were an earlier video in the same folder, and all returned 200.
  - 23:04:45-23:09:30: that chat's access lines for `GET /v1/sessions/<id>`
    and `/resources/*`. File requests outside the video's folder and
    `/comments` are dropped. The same chat's `Runner unavailable` WARN lines
    and `Relay: skipping` INFO lines are also kept. Those two kinds of line are
    what a grep of the log for the chat id returns. The script dropped nothing
    else from that chat's lines in the window, apart from the omitted endpoint
    families (`/items`, `/comments`, `/health`, `/read-state`, `/permissions`,
    `/agent`, `/child_sessions`, `/stream`).
  - Lines from other chats and the server's unrelated lines were left out. This
    includes the runner-tunnel disconnect at 23:04:12, which does not name the
    chat.
- `bug-report/network.txt`: **reconstructed** from those same access lines
  (23:05:09-23:05:15, one open of the video). No browser capture was
  saved. The browser sends exactly these requests, so method, path and status
  match what DevTools would have shown. Timing and headers are omitted.
- `bug-report/viewer.txt`: the message text comes from the person's report. It
  is also the string `web/src/shell/CodeViewer.tsx` renders when the video
  fetch fails. The folder and file name come from the log (the folder listing
  returned 200, then the file was requested).

## Sanitization

- The chat id, the runner id and the workspace folder path and file names were
  replaced with synthetic ones. There is one consistent mapping per file:
  chat `5b0f3c9e...`, runner `runner_token_8d41e2a7...`, folder
  `experiments/renders`, files `compare_v1_1x.mp4` / `compare_v2_1x.mp4`.
- The browser user-agent (`ua=`) and request ids (`rid=`) were removed.
- The script asserts that no original identifier survives: the real path
  components, ids, any other 32-hex value, and any IP other than 127.0.0.1.
- No tokens, passwords, emails or message content are included. Only request
  lines and log metadata are.

## Deliberately absent

The cause (the chat's runner exits after an idle hour, and downloads have no
host fallback) and the fix are not described anywhere in `bug-report/`. The
real log lines show the symptom: 503 on `?download=true` and "Runner
unavailable ... is offline". They do not show how long the chat had been idle.
