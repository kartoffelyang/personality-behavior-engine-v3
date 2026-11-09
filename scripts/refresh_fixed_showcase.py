"""Regenerate exactly the currently published entries; checkpoint every attempt."""
import sys,json,time,copy,shutil
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from engine.showcase import SHOWCASE_DIR,read_manifest
from engine.pipeline import Engine,generate_behavior_plan,rerun_from_review_step
from engine.model_intent import run_intent
from engine.model_story import build_story_input,run_story,run_spoken_reply
from engine.llm_router import get_llm
OUT=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'output'/('refresh13_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
OUT.mkdir(parents=True,exist_ok=True)
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
if not (OUT/'source_manifest.json').exists():
 save(OUT/'source_manifest.json',read_manifest())
 for dirname in ['prompts','config']:
  for src in (ROOT/dirname).rglob('*'):
   if src.is_file() and src.suffix in {'.yaml','.txt'} and src.name!='runtime.yaml':
    dst=OUT/'snapshot'/src.relative_to(ROOT);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
m=read(OUT/'source_manifest.json');assert len(m['cases'])==13

def work(item):
 key=item['persona']+'_'+item['case_id'];folder=OUT/key;folder.mkdir(exist_ok=True)
 if (folder/'session.json').exists():return key
 old=read(SHOWCASE_DIR/m['batch_id']/(item['session_id']+'.json'))
 req=copy.deepcopy(old['request']);req['request_id']=OUT.name+'_'+key
 eng=Engine(use_llm=True,llm_backend='openai')
 def stage(label,fn,ok):
  good=folder/(label+'_success.json')
  if good.exists():return read(good)
  for i in range(4):
   start=time.monotonic();r=fn();elapsed=round(time.monotonic()-start,2)
   if label=='step2':r['elapsed_seconds']=elapsed
   save(folder/(label+'_'+str(time.time_ns())+'.json'),r)
   if label=='step7':
    error=r.get('generation_error');attempts=next(s for s in r['audit_steps'] if s['step']==7).get('attempts',[])
   else:error=r.get('error_info');attempts=r.get('attempts',[])
   event={'case':key,'stage':label,'ok':ok(r),'seconds':elapsed,'error':(error or {}).get('message'),'internal_failed_attempts':sum(a.get('status')=='failed' for a in attempts)}
   with (folder/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
   print(json.dumps(event,ensure_ascii=False),flush=True)
   if ok(r):save(good,r);return r
  raise RuntimeError(key+' '+label+' exhausted 4 attempts; checkpoint saved')
 obs=stage('step2',lambda:run_intent(req,'openai',get_llm('openai'),eng.scenarios),lambda r:not r.get('fallback'))
 partial=generate_behavior_plan(req,use_llm=True,engine=eng,llm_backend='openai',shared_intent=obs,defer_step7=True)
 result=stage('step7',lambda:rerun_from_review_step(partial,step_no=7,engine=eng,llm_backend='openai'),lambda r:r.get('generation_status')=='ok' and bool(next(s for s in r['audit_steps'] if s['step']==7)['output'].get('validated_actions')))
 session={'session_id':item['session_id'],'case_id':item['case_id'],'case_title':item['title'],'persona':item['persona'],'backend':'openai','request':req,'result':result,'confirmed_step':8,'prompt_overrides':{},'frozen':True}
 payload=build_story_input(session)
 session['story_step']=stage('step8',lambda:run_story(payload,'openai'),lambda r:r.get('status')=='ok')
 if old['story_step'].get('spoken_reply_addition') or old['story_step'].get('output',{}).get('spoken_reply'):
  def speech():
   speech_payload=copy.deepcopy(payload)
   failed=sorted(folder.glob('speech_[0-9]*.json'))
   if failed:
    prev=read(failed[-1])
    speech_payload['validation_feedback']={'error':prev.get('error_info',{}).get('message'),'previous_output':prev.get('raw_llm_text'),'instruction':'保留原计划。明确表达由用户选择、确认后执行及相关条件；可以直接说“由你选择，确认后再执行”。不增加动作或未提供的时间。'}
   return run_spoken_reply(speech_payload,'openai')
  session['story_step']['spoken_reply_addition']=stage('speech',speech,lambda r:r.get('status')=='ok')
 save(folder/'session.json',session);return key
print(json.dumps({'output':str(OUT),'cases':13,'workers':2},ensure_ascii=False),flush=True)
errors=[];done=[]
with ThreadPoolExecutor(max_workers=2) as pool:
 jobs={pool.submit(work,item):item for item in m['cases']}
 for f in as_completed(jobs):
  try:done.append(f.result())
  except Exception as e:errors.append(str(e))
  save(OUT/'progress.json',{'done':done,'errors':errors});print(json.dumps({'complete':len(done),'errors':errors},ensure_ascii=False),flush=True)
