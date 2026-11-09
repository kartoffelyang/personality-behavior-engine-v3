import sys,json,copy,uuid
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from engine.showcase import SHOWCASE_DIR,read_manifest,load_frozen_session
from engine.model_story import parse_story_response,validate_spoken_reply,build_story_input,current_story_system
from engine.model_planner import current_planner_system
from engine.model_intent import current_intent_system
from engine.story_boundaries import check_story_boundaries
from engine.planner_validation import validate_plan
from engine.llm_client import extract_json_object
folder=Path(sys.argv[1]);source=json.loads((folder/'source_manifest.json').read_text(encoding='utf-8'))
assert read_manifest()==source,'Published manifest changed during generation'
sessions=[]
for item in source['cases']:
 s=json.loads((folder/(item['persona']+'_'+item['case_id'])/'session.json').read_text(encoding='utf-8'))
 old=json.loads((SHOWCASE_DIR/source['batch_id']/(item['session_id']+'.json')).read_text(encoding='utf-8'))
 assert s['session_id']==item['session_id'] and s['request']['user_utterance']==old['request']['user_utterance']
 assert s['request']['context']==old['request']['context'] and s['request']['history']==old['request']['history']
 steps={x['step']:x for x in s['result']['audit_steps']}
 assert set(steps)==set(range(1,8)) and steps[2]['status']=='ok' and steps[7]['status']=='ok'
 assert steps[2]['prompt_used']['system_prompt']==current_intent_system()
 assert steps[7]['prompt_used']['system_prompt']==current_planner_system()
 validate_plan(extract_json_object(steps[7]['raw_llm_text']),steps[7]['input']['planner_input'],s['result']['_review_internal']['whitelist'])
 story=s['story_step'];assert story['status']=='ok' and story['prompt_used']['system_prompt']==current_story_system()
 assert parse_story_response(story['raw_llm_text'])==story['output']
 check_story_boundaries(story['output'],build_story_input(s))
 if 'spoken_reply_addition' in story:
  a=story['spoken_reply_addition'];assert a['status']=='ok' and extract_json_object(a['raw_llm_text'])==a['output'];validate_spoken_reply(a['output']['spoken_reply'],build_story_input(s))
 sessions.append(s)
assert len(sessions)==13
batch=uuid.uuid4().hex[:12];dest=SHOWCASE_DIR/batch;dest.mkdir()
updated=copy.deepcopy(source);updated['batch_id']=batch;updated['created_at']=datetime.now().isoformat(timespec='seconds');updated['updated_at']=updated['created_at'];updated['source_batch_id']=source['batch_id'];updated.pop('speech_added_at',None)
for s,item in zip(sessions,updated['cases']):
 (dest/(s['session_id']+'.json')).write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')
 item['action_count']=len(s['result']['audit_steps'][-1]['output']['validated_actions'])
(dest/'source_manifest.json').write_text(json.dumps(source,ensure_ascii=False,indent=2),encoding='utf-8')
assert read_manifest()==source
pending=SHOWCASE_DIR/('.'+batch+'.json');pending.write_text(json.dumps(updated,ensure_ascii=False,indent=2),encoding='utf-8');pending.replace(SHOWCASE_DIR/'current.json')
(folder/'published.json').write_text(json.dumps(updated,ensure_ascii=False,indent=2),encoding='utf-8')
for s in sessions:assert load_frozen_session(s['session_id'])['request']['request_id']==s['request']['request_id']
print(json.dumps({'published':batch,'count':len(sessions),'urls_preserved':True}))
