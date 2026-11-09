from pathlib import Path
import sys,json,time,shutil,hashlib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine.pipeline import Engine,default_request_scaffold,generate_behavior_plan
from engine.model_intent import run_intent
from engine.llm_router import get_llm
from web.app import attach_story
OUT=ROOT/'output'/('fixed12_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
OUT.mkdir(parents=True)
def save(p,d):
 p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
def emit(d): print(json.dumps(d,ensure_ascii=False),flush=True)
for name in ['prompts','config','cases']:
 dest=OUT/'snapshot'/name;dest.mkdir(parents=True)
 for src in (ROOT/name).rglob('*'):
  if src.is_file() and src.suffix in {'.yaml','.txt','.json'} and src.name!='runtime.yaml':
   dst=dest/src.relative_to(ROOT/name);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
def run(case,persona):
 key=case['case_id']+'_'+persona;folder=OUT/key;folder.mkdir()
 row={'case_id':case['case_id'],'title':case['title'],'persona':persona,'steps':{}}
 started=time.monotonic()
 try:
  eng=Engine(use_llm=True,llm_backend='openai')
  req=default_request_scaffold(persona,case['request']['user_utterance'],OUT.name+'_'+key)
  save(folder/'request.json',req)
  t=time.monotonic();obs=run_intent(req,'openai',get_llm('openai'),eng.scenarios);obs['elapsed_seconds']=round(time.monotonic()-t,2)
  save(folder/'step2.json',obs)
  row['steps']['2']={'status':'failed' if obs.get('fallback') else 'ok','seconds':obs['elapsed_seconds'],'error':obs.get('error_info')}
  emit({'case':key,'step':2,**row['steps']['2']})
  if obs.get('fallback'):return row
  result=generate_behavior_plan(req,use_llm=True,engine=eng,llm_backend='openai',shared_intent=obs)
  save(folder/'result.json',result)
  s=next(x for x in result['audit_steps'] if x['step']==7)
  row['scenario_id']=result.get('scenario_id')
  row['steps']['7']={'status':result.get('generation_status'),'seconds':s.get('elapsed_seconds'),'attempts':[{k:a.get(k) for k in ['attempt','status','elapsed_seconds','error','error_info']} for a in s.get('attempts',[])],'error':result.get('generation_error')}
  emit({'case':key,'step':7,**row['steps']['7']})
  attach_story(result,req,persona,'openai');s=result['story_step'];save(folder/'result.json',result)
  row['steps']['8']={'status':s.get('status'),'seconds':s.get('elapsed_seconds'),'attempts':[{k:a.get(k) for k in ['attempt','status','elapsed_seconds','error_info']} for a in s.get('attempts',[])],'error':s.get('error_info'),'warnings':s.get('quality_warnings',[])}
  emit({'case':key,'step':8,**row['steps']['8']})
 except Exception as e:row['exception']=str(e)
 finally:
  row['wall_seconds']=round(time.monotonic()-started,2)
  row['success']=all(row['steps'].get(str(n),{}).get('status')=='ok' for n in [2,7,8])
  save(folder/'metrics.json',row)
 return row
cases=json.loads((ROOT/'cases/welcome_cases.json').read_text(encoding='utf-8'))
assert len(cases)==6
emit({'output':str(OUT),'groups':12,'workers':2,'step2':'independent per persona','context':'same as main page: original utterance only'})
began=time.monotonic();rows=[]
with ThreadPoolExecutor(max_workers=2) as pool:
 fs=[pool.submit(run,c,p) for c in cases for p in ['ESTJ','INFP']]
 for f in as_completed(fs):
  rows.append(f.result());save(OUT/'summary.json',{'rows':rows,'elapsed_seconds':round(time.monotonic()-began,2),'complete':len(rows)==12});emit({'completed':len(rows),'success':sum(r['success'] for r in rows)})
emit({'finished':str(OUT)})
