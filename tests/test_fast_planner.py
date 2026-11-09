from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.fast_planner import configuration, compile_result, generate_fast
from engine.planner_validation import PlanValidationError

TEXT = '工作不顺，帮我整理一份不发送的回复草稿。'


@pytest.fixture
def draft():
    tools = configuration()[0]
    tid = next(k for k,v in tools.items() if v['name'] == '回复草稿生成')
    return {'goal':'整理回复草稿', 'scenario_id':'emotional_support', 'status':'ready', 'questions':[],
            'context': {'destination_reference':'', 'arrival_deadline':''},
            'actions':[{'id':'a1','tool':tid,'mode':'auto_generate','p':{},'when':[], 'do':'生成不发送的回复草稿'}],
            'baseline':['a1'], 'variants': {
                'ESTJ': {'steps':['a1'], 'changes':[{'action':'a1','factor':'ESTJ.L2','field':'do','value':'按问题、步骤和后续安排整理草稿，不发送'}]},
                'INFP': {'steps':['a1'], 'changes':[]}}}


def test_compact_one_call_preserves_exact_config_and_actual_change(draft):
    class Client:
        model='fixture'
        calls=0
        def chat(self, messages, **kwargs):
            self.calls+=1
            return json.dumps(draft,ensure_ascii=False)
    c=Client(); result=generate_fast(TEXT,c)
    assert c.calls == result['metrics']['model_calls'] == 1
    assert result['baseline'][0]['details'] == draft['actions'][0]['do']
    diff=result['variants'][0]['differences'][0]
    assert diff['factor']['quote'] == configuration()[2]['ESTJ.L2']['quote']
    assert diff['before'] != diff['after']
    assert result['variants'][1]['differences'] == []
    assert result['execution'] == 'planning_preview_only'


@pytest.mark.parametrize('change',[
    {'factor':'INFP.L1'}, {'factor':'fake'}, {'value':'生成不发送的回复草稿'},
    {'action':'absent'}, {'field':'forged'}, {'field':'order','value':1},
    {'field':'presence','value':False}, {'field':'mode','value':'auto_execute'},
    {'field':'p','value':{'伪造参数':23}},
])
def test_false_differences_and_invalid_contract_rejected(draft,change):
    draft['variants']['ESTJ']['changes'][0].update(change)
    with pytest.raises(PlanValidationError):compile_result(draft,TEXT)


def test_unexplained_action_removal_rejected(draft):
    draft['variants']['INFP']['steps']=[]
    with pytest.raises(PlanValidationError,match='依据'):compile_result(draft,TEXT)


def test_unprovided_context_rejected(draft):
    draft['context']['arrival_deadline']='下午三点'
    with pytest.raises(PlanValidationError,match='引用'):compile_result(draft,TEXT)


def test_missing_navigation_dependencies_rejected(draft):
    tools=configuration()[0]
    tid=next(k for k,v in tools.items() if v['name']=='导航运行控制')
    draft['actions'][0].update(tool=tid,mode='confirm_execute',do='启动导航')
    draft['variants']['ESTJ']['changes']=[]
    with pytest.raises(PlanValidationError,match='依赖'):compile_result(draft,'去康源大厦')


def test_vehicle_control_cannot_claim_auto_preparation(draft):
    tid=next(k for k,v in configuration()[0].items() if v['name']=='温度调节')
    draft['actions'][0]['tool']=tid
    with pytest.raises(PlanValidationError,match='确认'):compile_result(draft,TEXT)


def test_no_tool_is_allowed_without_forced_personality_difference(draft):
    draft.update(status='no_tool_needed',actions=[],baseline=[])
    for v in draft['variants'].values():v.update(steps=[],changes=[])
    assert compile_result(draft,'我想安静一会，不用做任何事')['baseline']==[]


def test_fast_route_and_original_entries_coexist():
    from web.app import app
    c=app.test_client()
    assert '同一情境，如何行动' in c.get('/fast').get_data(as_text=True)
    assert 'auto-compare-form' in c.get('/').get_data(as_text=True)
    assert 'id="runForm"' in c.get('/comparison').get_data(as_text=True)


def test_job_persists_and_can_restore_without_model_call(monkeypatch,tmp_path,draft):
    fast=importlib.import_module('web.fast')
    monkeypatch.setattr(fast,'OUT',tmp_path)
    monkeypatch.setattr(fast,'_jobs',{})
    monkeypatch.setattr(fast,'generate_fast',lambda text:compile_result(draft,text))
    class Immediate:
        def submit(self,fn,*args):fn(*args)
    monkeypatch.setattr(fast,'_pool',Immediate())
    from web.app import app
    c=app.test_client()
    response=c.post('/api/fast',json={'text':TEXT})
    assert response.status_code==202
    jid=response.json['id']
    assert c.get('/api/fast/'+jid).json['status']=='completed'
    fast._jobs.clear()
    assert c.get('/api/fast/'+jid).json['result']['execution']=='planning_preview_only'


@pytest.mark.parametrize('value',[None,[],{'text':123},{'text':''},{'text':'x'*3001}])
def test_api_rejects_bad_input(value):
    from web.app import app
    assert app.test_client().post('/api/fast',json=value).status_code==400


def test_existing_fatigue_rule_blocks_entertainment(draft):
    tid=next(k for k,v in configuration()[0].items() if v['name']=='内容搜索')
    draft['actions'][0].update(tool=tid,do='搜索音乐')
    draft['variants']['ESTJ']['changes']=[]
    with pytest.raises(PlanValidationError,match='安全规则'):
        compile_result(draft,'开车时连续打哈欠，帮我播放音乐')


def test_explanation_is_saved_with_the_exact_referenced_factor(draft):
    reason='用任务收束策略把表达组织为问题和下一步，而非只改写语气。'
    draft['variants']['ESTJ']['changes'][0]['why']=reason
    d=compile_result(draft,TEXT)['variants'][0]['differences'][0]
    assert d['link_explanation']==reason
    assert d['factor']['id']=='ESTJ.L2'


@pytest.mark.parametrize('why',[[],None,'','x'*201])
def test_invalid_explanation_is_not_published(draft,why):
    draft['variants']['ESTJ']['changes'][0]['why']=why
    with pytest.raises(PlanValidationError,match='简短文字'):compile_result(draft,TEXT)


def test_scenario_trials_link_only_exact_saved_inputs(monkeypatch,tmp_path):
    fast=importlib.import_module('web.fast')
    root=tmp_path/'root';out=tmp_path/'out';(root/'config').mkdir(parents=True);out.mkdir()
    cases=[{'id':'one','title':'测试场景','text':'原文'}]
    (root/'config/demo_scenarios.json').write_text(json.dumps(cases))
    jid='b'*32
    (out/'scenario_trials.json').write_text(json.dumps({'cases':[{'id':'one','text':'原文','status':'completed','job_id':jid}]}))
    record=out/(jid+'.json');record.write_text(json.dumps({'input':'原文','result':{'status':'needs_clarification'}}))
    monkeypatch.setattr(fast,'ROOT',root);monkeypatch.setattr(fast,'OUT',out)
    case=fast.demo_scenarios()[0]
    assert case['job_id']==jid and case['trial_label']=='待补充信息'
    record.write_text(json.dumps({'input':'另一场景','result':{}}))
    assert fast.demo_scenarios()[0]['job_id']==''
    record.unlink()
    assert fast.demo_scenarios()[0]['job_id']==''


def test_three_demo_candidates_are_present_without_model_calls(monkeypatch):
    from web.app import app
    fast=importlib.import_module('web.fast')
    monkeypatch.setattr(fast,'generate_fast',lambda *args:pytest.fail('Rendering must not generate'))
    html=app.test_client().get('/fast').get_data(as_text=True)
    for title in ('下班后驻车恢复','不赶时间的周末出游','旅途中休息吃饭'):
        assert title in html
    assert html.count('data-scenario=')==3


def entertainment_raw():
    tid=next(k for k,v in configuration()[0].items() if v['name']=='内容搜索')
    return {'goal':'推荐娱乐内容','scenario_id':'entertainment_relax','status':'ready','questions':[],
            'context':{'destination_reference':'','arrival_deadline':''},
            'actions':[{'id':'a1','tool':tid,'mode':'auto_generate','p':{'关键词':'轻松有趣','类型':'娱乐内容','来源':''},'when':[],'do':'搜索并整理娱乐推荐'}],
            'baseline':['a1'], 'variants':{
                'ESTJ':{'steps':['a1'],'changes':[]},
                'INFP':{'steps':['a1'],'changes':[{'action':'a1','factor':'INFP.L1','field':'p','value':{'关键词':'轻松有趣','类型':'音乐、播客、喜剧','来源':''},'why':'保留不同内容类型供比较。'}]}}}


def test_empty_optional_source_does_not_discard_a_real_recommendation():
    raw=entertainment_raw();before=deepcopy(raw)
    r=compile_result(raw,'今天真的太无聊了，有什么推荐的么')
    assert r['baseline'][0]['name']=='内容搜索'
    assert '来源' not in r['baseline'][0]['parameters_hint']
    assert '来源' not in r['variants'][1]['actions'][0]['parameters_hint']
    assert len(r['normalization_corrections'])==2
    assert raw==before  # Original model response stays intact for traceability.


@pytest.mark.parametrize('empty',[None,'','  ',[],{}])
def test_declared_empty_optional_values_are_omitted(empty):
    raw=entertainment_raw();raw['actions'][0]['p']['来源']=empty
    r=compile_result(raw,'推荐娱乐内容')
    assert '来源' not in r['baseline'][0]['parameters_hint']


def test_unknown_empty_parameter_still_fails_contract_validation():
    raw=entertainment_raw();raw['actions'][0]['p']['未知参数']=''
    with pytest.raises(PlanValidationError,match='未定义'):compile_result(raw,'推荐娱乐内容')


def test_required_parameter_is_not_silently_removed():
    cfg=configuration();raw=entertainment_raw();cfg[0][raw['actions'][0]['tool']]['required_params']=['来源']
    with pytest.raises(PlanValidationError,match='来源'):compile_result(raw,'推荐娱乐内容',cfg)


def test_cleanup_does_not_hide_invalid_factor_or_create_a_fake_difference():
    raw=entertainment_raw();raw['variants']['INFP']['changes'][0]['value']=deepcopy(raw['actions'][0]['p'])
    r=compile_result(raw,'推荐娱乐内容')
    assert r['variants'][1]['differences']==[]
    raw['variants']['INFP']['changes'][0]['factor']='FORGED'
    with pytest.raises(PlanValidationError,match='因素'):compile_result(raw,'推荐娱乐内容')


def test_zero_false_and_invalid_numeric_values_are_not_removed(draft):
    tid=next(k for k,v in configuration()[0].items() if v['name']=='音量调节')
    draft['actions'][0].update(tool=tid,mode='confirm_execute',p={'音量':0,'静音':False})
    draft['variants']['ESTJ']['changes']=[]
    r=compile_result(draft,'音量调到零')
    assert r['baseline'][0]['parameters_hint']=={'音量':0,'静音':False}
    draft['actions'][0]['p']['音量']=float('nan')
    with pytest.raises(PlanValidationError,match='非法'):compile_result(draft,'音量调到零')


def test_validation_failure_survives_restart_and_is_not_called_intent_failure(monkeypatch,tmp_path):
    fast=importlib.import_module('web.fast')
    monkeypatch.setattr(fast,'OUT',tmp_path);monkeypatch.setattr(fast,'_jobs',{})
    def invalid(text):raise PlanValidationError('动作1参数错误')
    monkeypatch.setattr(fast,'generate_fast',invalid)
    class Immediate:
        def submit(self,fn,*args):fn(*args)
    monkeypatch.setattr(fast,'_pool',Immediate())
    from web.app import app
    c=app.test_client();jid=c.post('/api/fast',json={'text':'推荐一点内容'}).json['id']
    fast._jobs.clear();r=c.get('/api/fast/'+jid).json
    assert r['status']=='failed' and r['stage']=='plan_validation'
    assert '模型已生成方案' in r['error']


def test_public_resource_query_is_preparation_but_private_history_is_not(draft):
    tools=configuration()[0]
    tid=next(k for k,v in tools.items() if v['name']=='实时资源状态查询')
    draft['actions'][0].update(tool=tid,do='查询可用餐位',p={})
    draft['variants']['ESTJ']['changes']=[]
    assert compile_result(draft,'查一下餐位')['baseline'][0]['execution_mode']=='auto_generate'
    tid=next(k for k,v in tools.items() if v['name']=='历史目的地获取')
    draft['actions'][0]['tool']=tid
    with pytest.raises(PlanValidationError,match='确认'):compile_result(draft,'查看历史地点')
