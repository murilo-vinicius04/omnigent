# Rosca Antigravity approval recovery

- User reported repeated approval prompts despite wanting autonomous execution.
- Session `ee05b147db7b4e0ca931c5fc181d5054` now uses Antigravity / `gemini-3.8-flash-medium`; native conversation `93d38dc8-b8cc-428d-a9d9-4e22d4541d32`.
- Stored `terminal_launch_args` already contained `--dangerously-skip-permissions`, but the running agy PID `876101` lacked it. Runner logs confirmed repeated native permission interactions. Configuration and actual process diverged; no new source fix was necessary.
- Verified lane idle, saved metadata snapshot `/tmp/antigravity-rosca-permissions-before.json`, then used authenticated session events `stop_session` and `retry_session`. Recovery returned 202 / `runner_relaunched`. No prompt sent or task replayed.
- Verified replacement PID `2037658` has both `--dangerously-skip-permissions` and `--conversation` with the SAME native UUID. Bridge state agrees. tmux reports `pane_dead=0`, process `agy`; no approval prompt visible. Session remains idle.
- Bridge: `~/.omnigent/antigravity-native/a9bb3635bc1075c828ad278b7933d9e6`. Never print csrf tokens or complete process argv; inspect only selected flags.
- Existing launcher already passes persisted args through `build_agy_launch(extra_args=...)`. Saving launch args alone does not modify an already running process. Future diagnosis should compare stored options against live argv before changing code.
- User verification: send the next intended command in rosca; the native Antigravity tool gate should no longer ask for per-command approval. Other Omnigent policy gates retain their own behavior.
