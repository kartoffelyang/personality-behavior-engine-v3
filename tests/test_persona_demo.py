from copy import deepcopy
from types import SimpleNamespace
from engine import persona_demo as demo


def test_demo_shares_observation_and_keeps_full_pipeline_for_both_personas(monkeypatch,tmp_path):
    monkeypatch.setattr(demo,"ROOT",tmp_path)
    calls=[];observation={'scenario_id':'emotional_support','output':{},'fallback':False}
    monkeypatch.setattr(demo,'Engine',lambda **kw:SimpleNamespace(scenarios=None))
    monkeypatch.setattr(demo,'get_llm',lambda backend:object())
    def intent(req,*args):
        calls.append(('intent',deepcopy(req)))
        return observation
    def generate(req,**kwargs):
        calls.append(('planner',deepcopy(req),kwargs))
        return {'persona':req['persona'],'generation_status':'ok'}
    monkeypatch.setattr(demo,'run_intent',intent)
    monkeypatch.setattr(demo,'generate_behavior_plan',generate)
    monkeypatch.setattr(demo,'present',lambda r:{'persona':r['persona'],'status':'completed'})
    r=demo.run_demo('想休息','车辆已驻车','j')
    assert len([c for c in calls if c[0]=='intent'])==1
    plans=[c for c in calls if c[0]=='planner']
    assert {c[1]['persona'] for c in plans}=={'ESTJ','INFP'}
    assert all(c[1]['generation_mode']=='demo' and c[1]['demo_world']=='车辆已驻车' for c in plans)
    assert all(c[2]['shared_intent'] is observation for c in plans)
    assert all('persona_scene_strategy' in c[2]['prompt_overrides'][7]['system_prompt'] for c in plans)
    assert len(r['audits'])==2


def test_failed_intent_is_not_replaced_with_fixed_persona_answers(monkeypatch,tmp_path):
    monkeypatch.setattr(demo,"ROOT",tmp_path)
    import pytest
    monkeypatch.setattr(demo,'Engine',lambda **kw:SimpleNamespace(scenarios=None))
    monkeypatch.setattr(demo,'get_llm',lambda backend:object())
    monkeypatch.setattr(demo,'run_intent',lambda *args:{'fallback':True})
    with pytest.raises(ValueError,match='未用固定模板'):demo.run_demo('试试','虚拟设定','j')


def test_compact_wire_expansion_preserves_decisions_and_checks_unknown_fields():
    from engine.model_planner import expand_compact_demo_plan
    from engine.planner_validation import PlanValidationError
    import pytest
    payload={'flow_context':{'capability_catalog':[{'capability':'entertainment','display_name':'影音'}]}}
    action={'c':'entertainment','n':'内容搜索','m':'auto_generate','p':{'关键词':'雨后钢琴'},'d':'搜索雨后钢琴','why':'低打扰休息','basis':'当前想休息'}
    raw={'behavior_actions':[action]}
    out=expand_compact_demo_plan(raw,payload)['behavior_actions'][0]
    assert out['parameters_hint']==action['p'] and out['persona_influence']==action['why']
    assert out['name']=='内容搜索' and out['order']==1
    assert raw['behavior_actions'][0]==action
    with pytest.raises(PlanValidationError):expand_compact_demo_plan({'behavior_actions':[{**action,'extra':1}]},payload)


def test_demo_api_preserves_original_routes_and_persists_readable_results(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from web import persona_demo as web
    from web.app import app
    monkeypatch.setattr(web,'OUT',tmp_path)
    monkeypatch.setattr(web,'JOBS',{})
    monkeypatch.setattr(web,'POOL',SimpleNamespace(submit=lambda fn,*args:fn(*args)))
    monkeypatch.setattr(web,'run_demo',lambda text,world,jid,progress:{'input':text,'world':world,'variants':{'ESTJ':{'status':'completed'},'INFP':{'status':'completed'}},'audits':{},'elapsed_seconds':1})
    c=app.test_client();response=c.post('/api/persona-demo',json={'case_id':'after_work'})
    assert response.status_code==202
    jid=response.json['id'];saved=c.get('/api/persona-demo/'+jid)
    assert saved.status_code==200 and saved.json['status']=='completed'
    assert saved.json['world'] and saved.json['case_id']=='after_work'
    assert (tmp_path/(jid+'.json')).exists()
    assert c.get('/demo').status_code==200 and c.get('/comparison').status_code==200
    assert c.get('/api/persona-demo/../../.env').status_code==404


def test_demo_compiler_repairs_only_public_prerequisites_not_persona_choices():
    from engine.model_planner import expand_compact_demo_plan
    payload={'flow_context':{'capability_catalog':[{'capability':'navigation','display_name':'导航','atomic_abilities':[{'name':'地点搜索'},{'name':'地点详情查看'}]}]}}
    action={'c':'navigation','n':'地点详情查看','m':'auto_generate','p':{'名称':'禾间简餐'},'d':'核对禾间简餐入口','why':'人格未改变此动作','basis':'后续导航需要地址'}
    result=expand_compact_demo_plan({'behavior_actions':[action],'notes':[]},payload)
    assert [a['name'] for a in result['behavior_actions']]==['地点搜索','地点详情查看']
    assert result['behavior_actions'][1]['parameters_hint']==action['p']
    assert result['notes'] and result['behavior_actions'][0]['persona_influence']=='人格未改变此动作'


def test_explanation_uses_saved_profiles_not_current_registry(monkeypatch,tmp_path):
    import json
    from web import persona_demo as web
    from web.app import app
    monkeypatch.setattr(web,'OUT',tmp_path)
    jid='a'*32
    record={'persona':'ESTJ','audit_steps':[{'step':3,'output':{'dominant':'Te','judgment_style':'保存时的判断逻辑'}},{'step':5,'output':{'scenario_strategy':'保存时的场景策略'}}]}
    (tmp_path/(jid+'_audit.json')).write_text(json.dumps({'personas':{'ESTJ':{'original_full_chain':record}}}))
    r=app.test_client().get('/api/persona-demo/'+jid+'/explanation')
    assert r.status_code==200
    assert r.json['ESTJ']['profile']['judgment_style']=='保存时的判断逻辑'
    assert r.json['ESTJ']['scene_strategy']['scenario_strategy']=='保存时的场景策略'
    assert 'not_causal_proof' in r.json['ESTJ']['attribution']
    assert app.test_client().get('/api/persona-demo/not-a-job/explanation').status_code==404
