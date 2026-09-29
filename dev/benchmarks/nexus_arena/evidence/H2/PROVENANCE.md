# H2 evidence: where each number comes from

Stays outside the tree; `mktree.sh` copies only `bug-report/`.

| file | source | real or reconstructed |
|---|---|---|
| `context-readings.txt`, other chat | `~/.omnigent/claude-token-ledger.jsonl`, Claude session `08622a13…` (Omnigent conversation `d14bc749…`), rows on 09-20/09-21 converted to -03. context = input + cache_read + cache_write | **real** rows, copied exactly; a selection, not every row |
| `context-readings.txt`, "/compact sent 15:25:03", "record stored 15:26:04" | `~/.omnigent/chat.db` `conversation_items` of `d14bc749…`: the `/compact` user item (15:25:03, position 7178) and the compaction item (15:26:04, position 7179) | **real** |
| `context-readings.txt`, "preTokens ~611k, postTokens ~7.4k" | the agent's report in conversation `247c77f5…` at 09-21 16:14 ("did compact at 15:26 (611k → 7.4k tokens)") and commit `842f2acca`'s message. Claude's original transcript line is gone: later relaunches rewrote that transcript | **rounded, from a secondary source** |
| `context-readings.txt`, "ring at 61%" | the agent's 15:57:51 message ("compacting … at 61% → done" at 15:25:03); matches 610,488 / 1M | real |
| `context-readings.txt`, this chat 09-15 | `chat.db`, conversation `247c77f5…`: compaction items 12:54:41 and 15:15:58, the agent's 15:05 message (host restarted 14:54:13), the agent's 15:08 message ("461k tokens of a 1M window is 46%"), the user's `/compact` at 15:14:03. The token ledger starts 09-18, so there are no per-request rows for this day | real, as reported at the time |
| `stored-compactions.txt` | `chat.db`, every `compaction` item of both conversations in the listed range: `len(compacted_messages)`, `len(json.dumps(compacted_messages))`, first/last message checked by prefix only | **real**, computed |
| `stored-compaction-0921.json` | the 15:26:04 item of `d14bc749…`: real keys, real roles/block types/lengths of the first 3 and last 3 messages, real role counts (591 user, 950 assistant) | real structure; all text replaced. The `summary` field is replaced too (it held a fixed placeholder string from the code, which would point at the persist function) |
| user quotes | `chat.db`, conversation `247c77f5…`: 09-15 15:07:37 and 09-21 15:57:40 (the latter is stored twice, dictated; quoted once) | verbatim |

Not included on purpose:

- Runner/host logs: 09-15..09-21 logs have rotated out of `~/.omnigent/logs`; no log line
  was reconstructed.
- Hook timing (`hooks.jsonl` of the bridge dirs): rotated; the "hook about a second before
  the transcript" fact exists only in the fix commit message, and it is the cause, so it is
  not in the bundle.
- The 09-14 quote about quota after host restarts: the statement quotes only the two
  compaction reports.
- Content of the other chat (an unrelated project) and every conversation text.

Checked for secrets: no tokens, passwords, IPs, emails or hostnames in `bug-report/`
(`grep -rniE 'token_|sk-|hf_|password|@|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+'` finds only
the `token_count` key and version-free numbers).
