"""measure2.py <run dir>: time, cost, tokens, Pro-weighted points, final report text of a plain arm."""
import json, sys, pathlib, datetime
import os, pathlib
ARENA = pathlib.Path(__file__).resolve().parent.parent
REPO = pathlib.Path(os.environ.get("ARENA_REPO", ARENA.parents[2]))
WORK = pathlib.Path(os.environ.get("ARENA_WORK", os.environ.get("TMPDIR", "/tmp")) ) / "nexus-arena"
WORK.mkdir(parents=True, exist_ok=True)
d = pathlib.Path(sys.argv[1]); res = None; rl = []; tools = 0
for l in open(d / "run.jsonl"):
    try: r = json.loads(l)
    except ValueError: continue
    if r.get("type") == "result": res = r
    if r.get("type") == "rate_limit_event": rl.append(r["rate_limit_info"].get("unifiedWindows", {}))
    if r.get("type") == "assistant":
        tools += sum(c.get("type") == "tool_use" for c in r["message"].get("content", []))
u = res["usage"]; cw, cr, out = u["cache_creation_input_tokens"], u["cache_read_input_tokens"], u["output_tokens"]
row = {"minutes": round(res["duration_ms"] / 60000, 1), "usd": round(res["total_cost_usd"], 2), "turns": res["num_turns"], "tools": tools,
       "cache_write": cw, "cache_read": cr, "output": out, "subtype": res["subtype"],
       "5h_first_last": [rl[0].get("five_hour", {}).get("utilization") if rl else None, rl[-1].get("five_hour", {}).get("utilization") if rl else None]}
(d / "measure.json").write_text(json.dumps(row, indent=1))
print(json.dumps(row)); print("REPORT:", res.get("result", "")[:1500])
