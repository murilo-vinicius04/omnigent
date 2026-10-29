import json,collections,sqlite3,datetime
from pathlib import Path
from omnigent.muse_native_usage import _record_candidates,normalize_usage_record
ROOT=Path.home()/'.local/share/muse/sessions'
seen=set();events=[];intents=[];sessions=[]
for p in sorted(ROOT.rglob('session.jsonl')):
 totals=collections.Counter();low=10**30;high=0
 for raw in p.open():
  try:rs=_record_candidates(json.loads(raw))
  except:continue
  for r in rs:
   rid=r.get('id');ts=r.get('recorded_at',0)/1e6
   if not rid or rid in seen:continue
   seen.add(rid)
   payload=r.get('payload',{})
   root=p.relative_to(ROOT).parts[3]
   if r.get('payload_type')=='runtime.user_intent.accepted':
    text='\n'.join(b.get('text','') for m in payload.get('model_messages',[]) for b in m.get('content',[]) if isinstance(b,dict))
    intents.append({'root':root,'sid':p.parent.name,'at':ts,'text':text})
   e=normalize_usage_record(r)
   if e:
    ev=payload['event'];u=ev['usage']
    e['cached_tokens']=u.get('cached_tokens',u.get('cached_input_tokens',u.get('cache_read_tokens',0)))
    e['event_kind']=ev.get('kind');e['model']=ev['model'].get('model_id') if isinstance(ev.get('model'),dict) else ev.get('model','unknown')
    e.update(at=ts,root=root,sid=p.parent.name,subagent='subagent' in p.parts)
    events.append(e);totals.update({k:e.get(k,0) for k in ['input_tokens','output_tokens','cached_tokens','cache_read_tokens','reasoning_tokens']});totals['events']+=1
    low=min(low,ts);high=max(high,ts)
 if totals:sessions.append({'sid':p.parent.name,'root':p.relative_to(ROOT).parts[3],'subagent':'subagent' in p.parts,'start':low,'end':high,**totals})
quota=[];ledger=[]
for f in [Path.home()/'.omnigent/usage-history.jsonl.1',Path.home()/'.omnigent/usage-history.jsonl']:
 for l in f.open():
  try:r=json.loads(l)
  except:continue
  if r.get('kind')=='plan_limits':quota.append(r)
  elif r.get('kind')=='model_call':ledger.append(r)
result={'native_events':events,'intents':intents,'sessions':sessions,'quota':quota,'ledger':ledger}
Path('/tmp/muse-audit/data.json').write_text(json.dumps(result))
def iso(t):return datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat()
print('TOTAL',len(sessions),'sessions',len(events),'events', {k:sum(e.get(k,0) for e in events) for k in ['input_tokens','output_tokens','cached_tokens','cache_read_tokens','reasoning_tokens']})
print('TIME',iso(min(e['at'] for e in events)),iso(max(e['at'] for e in events)))
for root in sorted(set(e['root'] for e in events)):
 es=[e for e in events if e['root']==root]
 if sum(e['input_tokens'] for e in es)<1000000:continue
 its=[i for i in intents if i['root']==root and i['root']==i['sid']]
 print('ROOT',root,'calls',len(es),'workers',len(set(e['sid'] for e in es if e['subagent'])),'start',iso(min(e['at'] for e in es)),'end',iso(max(e['at'] for e in es)),{k:sum(e.get(k,0) for e in es) for k in ['input_tokens','output_tokens','cached_tokens']})
 print('FIRST INTENT',its[0]['text'][-700:] if its else '')
print('QUOTA CHANGES')
prev=None
for r in quota:
 if r.get('provider')!='muse':continue
 w=r.get('windows');
 if w!=prev:print(r['at'],w);prev=w
print('MODELS',collections.Counter(e['model'] for e in events))
