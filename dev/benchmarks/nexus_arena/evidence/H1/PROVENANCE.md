# H1 evidence provenance

Not copied into the run tree (only `bug-report/` is). Everything in `bug-report/` is **real**
data from 2026-09-23..25; nothing was reconstructed. The builder script is
`h1_evidence.py` in the builder's scratchpad (not shipped).

## Sources

- The person's words: chat turns of 2026-09-25 22:04 and 22:07 (CANDIDATES.md section 1). The
  first one was dictated as "i don't know that happened" and stored repaired as "i don't know
  what happened"; the task quotes the repaired text. No chat title appears, so no rename.
- `host-20260924-202305.log`: the live host log
  `~/.omnigent/logs/host/host-20260924-202305-765976.log`, all 222 lines. The only cut is
  the end of a Bun crash banner (the bun.report link), replaced by a marker line.
- `journal-omnigent-host-2026-09-25.txt`: `journalctl --user -u omnigent-host` for 09-25,
  cut at 22:05. The later 22:18 restart (the one that deployed the fix) is left out.
- `pidwatch.sh` / `pidwatch.log`: the person's own sampler `~/.omnigent/pidwatch.sh` and
  its log `~/.omnigent/logs/pidwatch.log`. 09-23 22:00 to 09-24 23:59 is thinned to every
  5th minute; 09-25 00:00 to 22:04 is complete. Also cut at 22:05.
- `host-check-2026-09-25-1510.txt`: tool outputs from the voice-agent Claude Code session
  (transcript `-home-nexus-voice-agent/1215fbe3...jsonl`, 2026-09-25T18:10-18:15Z), taken
  when the person asked "what happened with the host?" at 15:10. The commands are shown as
  run; the agent's own explanation of the numbers is **not** included.
- `restart-2026-09-25-2202.txt`: the same session's restart at 22:02 (01:02Z), abridged to
  the charge before/after, plus the `uptime` / `systemctl show` output that the fixing
  session captured at 22:04:53 (Omnigent chat `247c77f5`, stored tool output).
- `after-restart-2026-09-25-2205.txt`: tool outputs of the fixing session between 22:05:47
  and 22:09:22, taken before any code was changed. Left out: its greps of the source code,
  its `/proc/<pid>/status` read, its reads of `connect.py`, and `cat /proc/sys/kernel/pid_max`
  (22:11). These are diagnosis steps, not symptoms.

## Sanitizing

- `runner_token_<32 hex>` ids, 32-hex session ids and the host id are replaced by
  `sha256("h1-bench-" + id)[:32]`. This is one-way and consistent across files.
- The benchmark workspace paths under `/tmp/claude-1000/.../bench4/runs/<name>/tree` become
  `/home/user/bench/runs/run-NN/tree`. That removes the run names, including the private
  task names from an unrelated project.
- `/home/nexus/wt/friendly-layer` becomes `/home/user/omnigent`, `/home/nexus` becomes
  `/home/user`, and the machine/host name `nexus` becomes `devbox`. The unit description
  "Omnigent host (friendly-layer)" becomes "Omnigent host".
- No tokens, passwords, e-mails or non-loopback IPs are present (checked with grep). The
  only address left is `127.0.0.1:6767`.

## What the bundle does and does not show

It shows the symptom: 117,6xx `tmux` zombies whose parent is the host, `pids.current` pinned
at the unit's `pids.max` (117,681), 20,180 refused forks, `[Errno 11]` and
"can't start new thread" at 22:00-22:01, runners dying at start, and the same saturation
on the previous host instance (09-24 01:00-10:00 in `pidwatch.log`). After a restart the
fresh host keeps zombies under about 2 s old.

Real clues it keeps, because a person would hand them over: the 00:18 "Runner zygote
unavailable ... omnigent changed on disk ... disabling it" warning; the runner pids in the
journal wrapping (3,917,864 to 848,896 at 02:26; 3,742,147 to 373,584 at 10:59); and the
oldest zombie being 15,315 s old at about 15:14, which dates the stall to about 10:59. No
line names the reaper, `waitid` or any function in `omnigent/host/connect.py`. The only
tracebacks are real, and they land in asyncio/threading and in `_entry.py`/`ambient.py`
(thread start failures), not in the buggy code.
