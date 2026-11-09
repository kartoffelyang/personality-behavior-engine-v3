import json
from types import SimpleNamespace

from engine import kev_free
from engine.kev_demo import Engine,compile_plan


def test_api_free_text_never_invents_parked_or_alone(monkeypatch,tmp_path):
    from web import persona_demo as web
    from web.app import app
    from engine import kev_demo
    monkeypatch.setattr(web,'OUT',tmp_path);monkeypatch.setattr(web,'JOBS',{})
    monkeypatch.setattr(web,'POOL',SimpleNamespace(submit=lambda fn,*args:fn(*args)))
    capture=[]
    def fake(text,world,*args,**kwargs):
        capture.append((text,world,kwargs.get('case_id')))
        return {'variants':{},'audits':{}}
    monkeypatch.setattr(kev_demo,'run_kev_demo',fake)
    response=app.test_client().post('/api/persona-demo',json={'model':'kev','case_id':'after_work','text':'还在高速，后排儿童哭闹'})
    assert response.status_code==202
    assert capture[0][2] is None
    assert '已安全驻车，用户独自乘坐' not in capture[0][1]
    assert capture[0][0]=='还在高速，后排儿童哭闹'


def test_free_observation_has_no_default_context_and_reuses_neutral_cache(monkeypatch,tmp_path):
    monkeypatch.setattr(kev_free,'ROOT',tmp_path)
    calls=[]
    def intent(req,*a):
        calls.append(req)
        return {'fallback':False,'output':{'context':{'driving_state':'driving','passenger_count':2}},'scenario_id':'fatigue_driving'}
    monkeypatch.setattr(kev_free,'run_intent',intent);monkeypatch.setattr(kev_free,'get_llm',lambda *a:None)
    for _ in range(2):
        result,_=kev_free.free_observation(SimpleNamespace(scenarios=None),'高速上后排有儿童',kev_free.FREE_WORLD,'j')
        assert result['output']['context']['driving_state']=='driving'
    assert len(calls)==1 and calls[0]['context']=={} and calls[0]['history']=={}


def test_candidate_validation_repairs_and_caches_without_persona_answers(monkeypatch,tmp_path):
    monkeypatch.setattr(kev_free,'ROOT',tmp_path);monkeypatch.setattr(kev_free,'get_llm',lambda *a:None)
    calls=[]
    def chat(*a,**k):
        calls.append(1)
        name='不存在的能力' if len(calls)==1 else '空调开关'
        return {'candidates':[{'id':i,'name':'模拟凉爽方案'+i,'description':'模拟开启空调',
           'actions':[{'c':'climate','n':name,'m':'auto_generate','p':{'开关':True},'d':'模拟开启空调',
                      'why':'共享候选','basis':'用户要求'}]} for i in ('A','B','C')]},''
    monkeypatch.setattr(kev_free,'chat_json_with_raw',chat)
    eng=Engine(use_llm=False);intent={'output':{'context':{'driving_state':'driving'}}}
    for _ in range(2):
        candidates,meta=kev_free.prepare_free_candidates(eng,'开空调',kev_free.FREE_WORLD,intent,compile_plan)
        assert len(candidates)==3
    assert len(calls)==2 and meta['cache_hit']


def test_free_input_uses_full_catalog_raw_scores_and_same_comparison_contract(monkeypatch):
    from engine.kev_atomic import action_templates,key
    monkeypatch.setattr(kev_free,'free_observation',lambda *a:({'output':{'context':{'driving_state':'driving'}},'elapsed_seconds':0,'cache_hit':True},True))
    templates=action_templates({'id':'M1','name':'共享内容','description':'演示假设'},'after_work','共同安排')
    actions=[templates[key('climate','空调开关')]]
    candidates=[{'id':'A','name':'开启空调','description':'模拟开启前排空调。','actions':actions},
                {'id':'B','name':'开启空调并设温度','description':'模拟开启空调，前排24℃。','actions':actions+[templates[key('climate','温度调节')]]}]
    from engine import kev_preparation
    monkeypatch.setattr(kev_preparation,'prepare_shared',lambda *a:({'output':{'context':{'driving_state':'driving'}},'elapsed_seconds':0},candidates,{'seconds':0,'cache_hit':True,'distinct_action_sets':2}))
    requests=[]
    def decide(state,questions):
        requests.append(questions)
        p=.21 if state['role']['id']=='ESTJ' else .32
        answers={q:{'choice':'omit','probabilities':{'include':p,'omit':1-p}} for q in questions if q!='bundle'}
        answers['bundle']={'choice':'A','probabilities':{'A':.7,'B':.3}}
        return {'answers':answers},.01
    r=kev_free.run_free_demo(Engine(use_llm=False),'太热了，开一下空调',kev_free.FREE_WORLD,'free_test',lambda **k:None,decide,compile_plan)
    assert r['contrast']['mode']=='atomic_comparison'
    assert r['mode']=='kev_free_atomic'
    assert r['atomic_scope']['scored_count']==57
    assert r['contrast']['groups'] and r['contrast']['candidate_action_sets']==2
    assert requests[0]==requests[1] and len(requests[0])==58
    for pid,p in [('ESTJ',.21),('INFP',.32)]:
        assert len(r['variants'][pid]['atomic_scores'])==57
        assert all(s['probability']==p and s['probability_source']=='independent_binary_adoption' for s in r['variants'][pid]['atomic_scores'])
        assert r['variants'][pid]['bundle_choice']['eligible']==['A','B']
    # An atom present in every candidate still retains its raw .21/.32 value,
    # rather than being displayed as a manufactured 100% marginal.
    assert r['variants']['ESTJ']['atomic_scores'][0]['probability']==.21
