# H3 evidence provenance

**Structure is real, all text is replaced.**

Source: `~/.omnigent/chat.db`, opened read-only on 2026-09-29. Rows are
positions 1496-1529 of the conversation the person called "my other chat", on
2026-09-13 from 12:08 to 12:28 local time. The report was sent at 12:29.

Copied as stored:
- positions, timestamps to the second, and item types;
- roles and agents (`claude-native-ui`, `spoken_summary`);
- how items group into response ids (a prompt has its own id, and each turn has one);
- tool names, in order;
- which spoken-summary items exist and where they sit: 1522 has
  `audio_pending`, 1524 has `audio_file_id`, and turn-2 has none.

Replaced:
- **All message text.** The chat's subject is out of scope, so every text is
  neutral filler of about the same length.
  - The real lengths were 707 / 790 / 2245 / 37 / 352 chars. The filler is
    636 / 740 / 2375 / 40 / 367.
  - The summary's first sentence is the real one: "I'm checking on this right
    now before giving you a final answer." After it, the text is filler that
    keeps the same meaning (still working, no answer yet).
- Tool arguments and outputs: short placeholders.
- Ids: the session id, response ids, item ids, call ids and the audio file id
  are all synthetic.
- `created_by`: dropped.

Not included:
- **No server log.** The oldest server log on disk is from 09-18, so none from
  09-13 survives. The start code also logs nothing when it skips a summary.
  `README.txt` in the bundle says the log is gone.
- **The 12:03 turn.** Its summary item also landed one position before its reply,
  but its content could not be checked without reading out-of-scope text.
- **The person's 12:55 message**, "it's not even summarizing now". It came after
  the wrong fix `6c8997b26` was deployed at 12:39, so it describes a different
  tree. The same symptom already shows under the start code in turn-2 (12:27),
  which has no summary item.

Generator: `h3_evidence.py` in the builder's scratchpad. It holds only
the layout and the filler, not the real text.
