"""run_nexus.py <task> <host_id> [worker] [model]: Opus brain (reviews) + one worker; default Gemini 3.7 Flash Medium, as bench1 arm D."""
import datetime, subprocess, sys, httpx
from omnigent import cli_auth
import os, pathlib
ARENA = pathlib.Path(__file__).resolve().parent.parent
REPO = pathlib.Path(os.environ.get("ARENA_REPO", ARENA.parents[2]))
# Same rule as mktree.sh: ARENA_WORK as given, else $TMPDIR/nexus-arena.
WORK = pathlib.Path(os.environ.get("ARENA_WORK") or pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "nexus-arena")
WORK.mkdir(parents=True, exist_ok=True)
B = str(ARENA)
T = sys.argv[1]; W = sys.argv[3] if len(sys.argv) > 3 else "gemini"
M = sys.argv[4] if len(sys.argv) > 4 else "gemini-3.7-flash-medium"
D = f"{WORK}/runs/{T}-{W}"
subprocess.run([str(ARENA / "bin" / "mktree.sh"), T, W], check=True)
S = "http://127.0.0.1:6767"
tok = cli_auth.refresh_stored_token(S); h = {"Authorization": f"Bearer {tok}"} if isinstance(tok, str) else {}
r = httpx.post(f"{S}/v1/sessions", headers=h, timeout=60, json={
    "agent_id": "06ec6c26f28940a0b77e83c16acaba3d", "host_id": sys.argv[2],
    "workspace": f"{D}/tree", "title": f"BENCH {T}-{W} (opus brain reviews + {M} worker)"})
r.raise_for_status(); sid = r.json()["id"]
j = httpx.patch(f"{S}/v1/sessions/{sid}", headers=h, timeout=60, json={
    "model_override": "claude-opus-5",
    "labels": {"team.worker": W, "team.worker_model": M}}).json()
print("brain:", j.get("model_override"), "| worker:", {k: v for k, v in (j.get("labels") or {}).items() if k.startswith("team.")})
open(f"{D}/session.txt", "w").write(sid)
open(f"{D}/start.txt", "w").write(datetime.datetime.now().astimezone().isoformat())
e = httpx.post(f"{S}/v1/sessions/{sid}/events", headers=h, timeout=60, json={
    "type": "message", "data": {"role": "user", "content": [{"type": "input_text", "text": open(f"{D}/task.md").read()}]}})
print("send", e.status_code, "| session", sid)
