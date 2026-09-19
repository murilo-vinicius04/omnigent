"""run_nexus.py <task>: Opus brain (reviews) + Gemini 3.7 Flash Medium worker, same config as bench1 arm D."""
import datetime, subprocess, sys, httpx
from omnigent import cli_auth
import os, pathlib
ARENA = pathlib.Path(__file__).resolve().parent.parent
REPO = pathlib.Path(os.environ.get("ARENA_REPO", ARENA.parents[2]))
WORK = pathlib.Path(os.environ.get("ARENA_WORK", os.environ.get("TMPDIR", "/tmp")) ) / "nexus-arena"
WORK.mkdir(parents=True, exist_ok=True)
B = str(ARENA)
T = sys.argv[1]; D = f"{WORK}/runs/{T}-gemini"
subprocess.run([str(ARENA / "bin" / "mktree.sh"), T, "gemini"], check=True)
S = "http://127.0.0.1:6767"
tok = cli_auth.refresh_stored_token(S); h = {"Authorization": f"Bearer {tok}"} if isinstance(tok, str) else {}
r = httpx.post(f"{S}/v1/sessions", headers=h, timeout=60, json={
    "agent_id": "06ec6c26f28940a0b77e83c16acaba3d", "host_id": sys.argv[2],
    "workspace": f"{D}/tree", "title": f"BENCH2 {T}-gemini (opus brain reviews + gemini 3.7 worker)"})
r.raise_for_status(); sid = r.json()["id"]
j = httpx.patch(f"{S}/v1/sessions/{sid}", headers=h, timeout=60, json={
    "model_override": "claude-opus-5",
    "labels": {"team.worker": "gemini", "team.worker_model": "gemini-3.7-flash-medium"}}).json()
print("brain:", j.get("model_override"), "| worker:", {k: v for k, v in (j.get("labels") or {}).items() if k.startswith("team.")})
open(f"{D}/session.txt", "w").write(sid)
open(f"{D}/start.txt", "w").write(datetime.datetime.now().astimezone().isoformat())
e = httpx.post(f"{S}/v1/sessions/{sid}/events", headers=h, timeout=60, json={
    "type": "message", "data": {"role": "user", "content": [{"type": "input_text", "text": open(f"{D}/task.md").read()}]}})
print("send", e.status_code, "| session", sid)
