"""measure_nexus.py <run dir>: brain Claude cost/tokens, worker tokens (Gemini with weekly points, or any other model), wall time to the brain's final answer."""
import json, sqlite3, sys, pathlib
from datetime import datetime
from omnigent.db.compression import decode
import os, pathlib
ARENA = pathlib.Path(__file__).resolve().parent.parent
REPO = pathlib.Path(os.environ.get("ARENA_REPO", ARENA.parents[2]))
WORK = pathlib.Path(os.environ.get("ARENA_WORK", os.environ.get("TMPDIR", "/tmp")) ) / "nexus-arena"
WORK.mkdir(parents=True, exist_ok=True)
STATE = pathlib.Path(os.environ.get("OMNIGENT_DATA_DIR", pathlib.Path.home() / ".omnigent"))
d = pathlib.Path(sys.argv[1]); con = sqlite3.connect(f"file:{STATE}/chat.db?mode=ro", uri=True)
s = lambda v: float(v) / 1000 if float(v) > 1e11 else float(v)  # noqa: E731
C = bytes.fromhex((d / "session.txt").read_text().strip())
start = datetime.fromisoformat((d / "start.txt").read_text().strip()).timestamp()
brain = {"output": 0, "cache_read": 0, "cache_write": 0, "usd": 0.0}; gem = {"tokens": 0, "calls": 0, "workers": 0}; other = {}
for cid, title, usage, parent in con.execute("select m.id, c.title, m.session_usage, c.parent_conversation_id from omnigent_conversation_metadata m join conversations c on c.id=m.id"):
    if not (cid == C or parent == C): continue
    if parent == C: gem["workers"] += 1
    if not usage: continue
    try: u = json.loads(decode(usage))
    except Exception: continue
    for model, b in (u.get("by_model") or {}).items():
        if model.lower().startswith("gemini"):
            gem["tokens"] += int(b.get("total_tokens", 0) or 0); gem["calls"] += int(b.get("calls", 0) or 0)
        elif parent == C:  # a non-Gemini worker, e.g. Codex on the OpenAI free pool
            o = other.setdefault(model, {"tokens": 0, "calls": 0}); o["tokens"] += int(b.get("total_tokens", 0) or 0); o["calls"] += int(b.get("calls", 0) or 0)
        elif cid == C:
            brain["output"] += int(b.get("output_tokens", 0) or 0); brain["cache_read"] += int(b.get("cache_read_input_tokens", 0) or 0)
            brain["cache_write"] += int(b.get("cache_creation_input_tokens", 0) or 0); brain["usd"] += float(b.get("total_cost_usd", 0) or 0)
# end = the brain's last assistant output_text (not the spoken-summary carrier)
end = None; final = ""
for ca, data in con.execute("select created_at, data from conversation_items where conversation_id=? order by created_at", (C,)):
    j = json.loads(data)
    if j.get("role") == "assistant" and j.get("agent") != "spoken_summary":
        t = " ".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "output_text")
        if t.strip(): end, final = s(ca), t
pts = []
for line in open(STATE / "usage-history.jsonl"):
    try: r = json.loads(line)
    except ValueError: continue
    if r.get("kind") != "plan_limits" or r.get("provider") != "antigravity": continue
    t = datetime.fromisoformat(r["at"].replace("Z", "+00:00")).timestamp()
    w = (r.get("windows") or {}).get("gemini-weekly")
    if isinstance(w, (int, float)) and start - 300 <= t <= (end or t) + 600: pts.append(w)
paused = 0.0
if (d / "pauses.txt").exists():
    for ln in (d / "pauses.txt").read_text().split("\n"):
        if ln.strip():
            a, b = ln.split()[:2]; paused += datetime.fromisoformat(b).timestamp() - datetime.fromisoformat(a).timestamp()
row = {"minutes": round((end - start - paused) / 60, 1) if end else None, "paused_min": round(paused / 60, 1), "usd": round(brain["usd"], 2), "brain": brain, "gemini": gem, "other_workers": other,
       "gemini_weekly_points": (max(pts) - min(pts)) if len(pts) > 1 else None}
(d / "measure.json").write_text(json.dumps(row, indent=1)); print(json.dumps(row)); print("REPORT:", final[:900])
