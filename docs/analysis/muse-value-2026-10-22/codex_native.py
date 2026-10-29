import json,collections
from pathlib import Path
files=[]
for day in ['21','22']:
 files+=list(Path.home().joinpath('.omnigent/codex-native').glob(f'*/codex-home/sessions/2026/10/{day}/**/*.jsonl'))
rows=[]
for p in files:
 seen=set();model='unknown'
 for l in p.open():
  try:r=json.loads(l)
  except:continue
  payload=r.get('payload',{})
  if r.get('type')=='turn_context':model=payload.get('model',model)
  if r.get('type')!='event_msg' or payload.get('type')!='token_count':continue
  info=payload.get('info') or {};total=info.get('total_token_usage');last=info.get('last_token_usage')
  if not total or not last:continue
  key=json.dumps(total,sort_keys=True)
  if key in seen:continue
  seen.add(key);rows.append({'at':r['timestamp'],'file':p.name,'model':model,**last})
for a,b in [('2026-10-22T11:35:00Z','2026-10-22T12:53:15Z'),('2026-10-22T12:10:32Z','2026-10-22T14:06:41Z')]:
 es=[r for r in rows if a<=r['at']<=b]
 print(a,b,len(es),{k:sum(r.get(k,0) for r in es) for k in ['input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens']},collections.Counter(r['model'] for r in es))
Path('/tmp/muse-audit/codex_native.json').write_text(json.dumps(rows))
