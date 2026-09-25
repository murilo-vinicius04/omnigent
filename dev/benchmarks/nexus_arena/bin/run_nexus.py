"""run_nexus.py <task> <host_id> [worker] [model]: Opus brain (reviews) + one worker.

The default worker is Gemini 3.7 Flash Medium, as bench1 arm D.
"""

import datetime
import os
import pathlib
import subprocess
import sys

import httpx

from omnigent import cli_auth

ARENA = pathlib.Path(__file__).resolve().parent.parent
REPO = pathlib.Path(os.environ.get("ARENA_REPO", ARENA.parents[2]))
# Same rule as mktree.sh: ARENA_WORK as given, else $TMPDIR/nexus-arena.
WORK = pathlib.Path(
    os.environ.get("ARENA_WORK") or pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "nexus-arena"
)
WORK.mkdir(parents=True, exist_ok=True)
B = str(ARENA)
T = sys.argv[1]
W = sys.argv[3] if len(sys.argv) > 3 else "gemini"
M = sys.argv[4] if len(sys.argv) > 4 else "gemini-3.7-flash-medium"
# ARENA_ARM names the run dir when one worker runs several models (hermes' model lives in
# ~/.hermes/config.yaml, not in the label); ARENA_BRAIN picks the reviewing Claude.
A = os.environ.get("ARENA_ARM") or W
BRAIN = os.environ.get("ARENA_BRAIN") or "claude-opus-5"
# ARENA_BRAIN_EFFORT pins the brain's reasoning effort; unset, the brain inherits the
# host's Claude settings (effortLevel in ~/.claude/settings.json).
EFFORT = os.environ.get("ARENA_BRAIN_EFFORT")
D = f"{WORK}/runs/{T}-{A}"
subprocess.run([str(ARENA / "bin" / "mktree.sh"), T, A], check=True)
S = "http://127.0.0.1:6767"
tok = cli_auth.refresh_stored_token(S)
h = {"Authorization": f"Bearer {tok}"} if isinstance(tok, str) else {}
r = httpx.post(
    f"{S}/v1/sessions",
    headers=h,
    timeout=60,
    json={
        "agent_id": "06ec6c26f28940a0b77e83c16acaba3d",
        "host_id": sys.argv[2],
        "workspace": f"{D}/tree",
        "title": f"BENCH {T}-{A} ({BRAIN} brain reviews + {M} worker)",
    },
)
r.raise_for_status()
sid = r.json()["id"]
j = httpx.patch(
    f"{S}/v1/sessions/{sid}",
    headers=h,
    timeout=60,
    json={
        "model_override": BRAIN,
        "labels": {"team.worker": W, "team.worker_model": M},
        **({"reasoning_effort": EFFORT} if EFFORT else {}),
    },
).json()
print(
    "brain:",
    j.get("model_override"),
    j.get("reasoning_effort") or "(host default)",
    "| worker:",
    {k: v for k, v in (j.get("labels") or {}).items() if k.startswith("team.")},
)
pathlib.Path(f"{D}/session.txt").write_text(sid)
pathlib.Path(f"{D}/start.txt").write_text(datetime.datetime.now().astimezone().isoformat())
e = httpx.post(
    f"{S}/v1/sessions/{sid}/events",
    headers=h,
    timeout=60,
    json={
        "type": "message",
        "data": {
            "role": "user",
            "content": [{"type": "input_text", "text": pathlib.Path(f"{D}/task.md").read_text()}],
        },
    },
)
print("send", e.status_code, "| session", sid)
