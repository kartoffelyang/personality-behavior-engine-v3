import sys,json,time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from engine.model_intent import run_intent
from engine.pipeline import default_request_scaffold
from engine.llm_router import get_llm
OUT=ROOT/'output'/('step2_stream_five_'+datetime.now().strftime('%Y%m%d_%H%M%S'));OUT.mkdir()
cases={c['case_id']:c for c in json.loads((ROOT/'cases/welcome_cases.json').read_text(encoding='utf-8'))}
tasks=[('case_02','ESTJ'),('case_03','ESTJ'),('case_03','INFP'),('case_02','INFP'),('case_01','ESTJ')]
def run(i,c,p):
 client=get_llm('openai');req=default_request_scaffold(p,cases[c]['request']['user_utterance'],OUT.name+'_'+str(i))
 start=time.monotonic();r=run_intent(req,'openai',client);elapsed=round(time.monotonic()-start,2)
 (OUT/f'run_{i}.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
 diag=getattr(client._call_diagnostics,'winner',None) or (r.get('error_info') or {}).get('diagnostics') or {}
 row={'run':i,'case':c,'title':cases[c]['title'],'persona':p,'success':not r.get('fallback'),'seconds':elapsed,'stream':client.stream,'model':r.get('model_version'),'error':r.get('error_info'),'diagnostics':diag}
 print(json.dumps({k:v for k,v in row.items() if k not in ['diagnostics','error']},ensure_ascii=False),flush=True)
 return row
print(str(OUT),flush=True);start=time.monotonic();rows=[]
with ThreadPoolExecutor(max_workers=2) as pool:
 for f in as_completed([pool.submit(run,i,c,p) for i,(c,p) in enumerate(tasks,1)]):
  rows.append(f.result());(OUT/'summary.json').write_text(json.dumps({'rows':rows,'wall_seconds':round(time.monotonic()-start,2)},ensure_ascii=False,indent=2),encoding='utf-8')
