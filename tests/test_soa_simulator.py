from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.soa_simulator import new_session,execute,snapshot,SimError,initial_state


def action(name,cap='entertainment',params=None,mode='auto_generate'):
    return {'id':name,'capability':cap,'name':name,'parameters_hint':params or {},'execution_mode':mode,'details':name,'preconditions':[]}


def test_queries_return_data_without_claiming_playback_and_manual_play_is_separate():
    s=new_session('a'*32,'ESTJ',[action('内容搜索'),action('内容详情查看')]);original=deepcopy(s)
    s=execute(s,revision=0)
    assert original['cursor']==0 and original['context']=={}
    assert len(s['context']['media_candidates'])==3
    assert s['state']['entertainment']['playing'] is False
    assert s['events'][0]['response']['simulated'] is True
    assert s['events'][0]['request']['service']=='sim.entertainment'
    s=execute(s,revision=s['revision'],manual_content='sim-media-2',confirmed=True)
    assert s['state']['entertainment']['playing'] is True
    assert s['state']['entertainment']['selected']['id']=='sim-media-2'
    assert s['cursor']==1  # Manual UI events are not generated plan steps.
    assert all(e['request']['source']=='user_manual' for e in s['events'][-2:])
    s=execute(s,revision=s['revision'])
    assert snapshot(s)['completed'] is True


def test_vehicle_arguments_and_confirmation_are_required():
    s=new_session('a'*32,'ESTJ',[action('温度调节','climate',mode='confirm_execute')])
    with pytest.raises(SimError,match='确认'):execute(s,revision=0,params={'温度':22})
    with pytest.raises(SimError,match='尚未给定'):execute(s,revision=0,confirmed=True)
    done=execute(s,revision=0,params={'温度':22},confirmed=True)
    assert done['state']['climate']['temperature']==22
    assert s['state']['climate']['temperature']==26
    assert done['events'][0]['state_changes']['climate']['after']['temperature']==22


@pytest.mark.parametrize('value',[90,-10,True,'not-a-number',float('nan')])
def test_bad_parameters_are_recorded_as_failures_without_state_change(value):
    s=new_session('a'*32,'ESTJ',[action('温度调节','climate',mode='confirm_execute')])
    result=execute(s,revision=0,params={'温度':value},confirmed=True)
    assert result['cursor']==0 and result['state']==s['state']
    assert result['events'][0]['response']['code']=='INVALID_ARGUMENT'


def test_timeout_retry_and_revision_prevent_duplicate_state_changes():
    s=new_session('a'*32,'ESTJ',[action('空调开关','climate',{'开关':True},'confirm_execute')])
    failed=execute(s,revision=0,confirmed=True,fault='timeout')
    assert failed['state']==s['state'] and failed['cursor']==0
    assert failed['events'][0]['response']['code']=='SIMULATED_TIMEOUT'
    with pytest.raises(SimError,match='会话已更新'):execute(failed,revision=0,confirmed=True)
    done=execute(failed,revision=1,confirmed=True)
    assert done['state']['climate']['power'] is True and done['cursor']==1


def test_wrong_candidate_and_missing_upstream_data_are_not_success():
    s=new_session('a'*32,'ESTJ',[action('内容详情查看')]);s=execute(s,revision=0)
    assert s['events'][0]['response']['code']=='DEPENDENCY_MISSING'
    with pytest.raises(SimError,match='候选ID'):execute(s,revision=1,manual_content='fake',confirmed=True)
    assert s['state']['entertainment']['playing'] is False


def test_modes_do_not_override_permission_and_history_is_not_invented():
    s=new_session('a'*32,'ESTJ',[action('通知读取','notification_mode')])
    with pytest.raises(SimError,match='确认'):execute(s,revision=0)
    s=execute(s,revision=0,confirmed=True)
    assert s['context']['notifications'][0]['id']=='sim-note-1'
    h=new_session('a'*32,'ESTJ',[action('历史目的地获取','navigation',mode='confirm_execute')])
    h=execute(h,revision=0,confirmed=True)
    assert h['events'][0]['response']['code']=='NO_SAVED_STATE'


def test_navigation_consumes_actual_simulated_query_results_in_order():
    steps=[action('地点搜索','navigation'),action('地点详情查看','navigation'),
           action('目的地设置','navigation',{'POI':'sim-poi-1'},'confirm_execute'),
           action('路线规划','navigation'),action('路线选择','navigation',{'路线':'sim-route-1'},'choose_execute'),
           action('导航运行控制','navigation',{'开始':True},'confirm_execute')]
    s=new_session('a'*32,'ESTJ',steps)
    for _ in steps:s=execute(s,revision=s['revision'],confirmed=True)
    assert s['state']['navigation']['running'] is True
    assert s['state']['navigation']['destination']['id']=='sim-poi-1'
    assert all(e['response']['code']=='OK' for e in s['events'])


def test_public_resource_query_and_other_vehicle_domains():
    steps=[action('服务与地点搜索','ecosystem'),action('实时资源状态查询','ecosystem'),
           action('氛围灯开关','ambient_light',{'开关':True},'confirm_execute'),
           action('颜色设置','ambient_light',{'颜色':'琥珀'},'confirm_execute'),
           action('座椅按摩控制','seat',{'开关':True,'模式':'轻柔','强度':2},'confirm_execute')]
    s=new_session('a'*32,'INFP',steps)
    for _ in steps:s=execute(s,revision=s['revision'],confirmed=True)
    assert s['state']['ambient_light']['color']=='琥珀' and s['state']['seat']['massage'] is True
    assert all(e['response']['code']=='OK' for e in s['events'])


def test_personas_start_with_identical_independent_state():
    a=new_session('a'*32,'ESTJ',[]);b=new_session('a'*32,'INFP',[])
    assert a['state']==b['state']==initial_state()
    a['state']['climate']['power']=True
    assert not b['state']['climate']['power']


def test_http_execution_is_persistent_and_stale_requests_do_not_repeat(monkeypatch,tmp_path):
    web=importlib.import_module('web.soa_sim');root=tmp_path/'root';out=tmp_path/'sessions'
    (root/'output/fast').mkdir(parents=True)
    jid='a'*32
    (root/'output/fast'/f'{jid}.json').write_text(json.dumps({'input':'无聊','result':{'variants':[{'persona':'ESTJ','actions':[action('内容搜索')]}]}}))
    monkeypatch.setattr(web,'ROOT',root);monkeypatch.setattr(web,'OUT',out)
    from web.app import app
    c=app.test_client();r=c.post('/api/soa-sim/sessions',json={'job_id':jid,'persona':'ESTJ'})
    assert r.status_code==201;s=r.json;sid=s['id']
    r=c.post(f'/api/soa-sim/sessions/{sid}/step',json={'revision':0});assert r.status_code==200
    assert len(r.json['context']['media_candidates'])==3
    assert c.post(f'/api/soa-sim/sessions/{sid}/step',json={'revision':0}).status_code==409
    assert len(c.get(f'/api/soa-sim/sessions/{sid}').json['events'])==1
    assert c.get('/api/soa-sim/sessions/../../.env').status_code==404


def test_invalid_params_are_rejected_before_execution():
    s=new_session('a'*32,'ESTJ',[action('内容搜索')])
    with pytest.raises(SimError):execute(s,revision=0,params=[])


@pytest.mark.parametrize('name,cap,params,field,expected',[
    ('音源切换','entertainment',{'蓝牙':True},'source','蓝牙'),
    ('出风模式切换','climate',{'吹脚':True},'air_mode','吹脚'),
    ('灯光效果切换','ambient_light',{'渐变':True},'effect','渐变'),
])
def test_alternative_commands_do_not_require_contradictory_default(name,cap,params,field,expected):
    s=new_session('a'*32,'ESTJ',[action(name,cap,params,'confirm_execute')])
    assert snapshot(s)['pending']['required_fields']==[]
    s=execute(s,revision=0,confirmed=True)
    assert s['events'][0]['response']['code']=='OK'
    assert s['state'][cap][field]==expected


def test_prepare_runs_queries_to_actual_candidates_then_stops_at_choice():
    from engine.soa_simulator import prepare_public
    s=new_session('a'*32,'ESTJ',[
        action('服务与地点搜索','ecosystem',{'类型':'餐厅'}),
        action('服务详情获取','ecosystem'),
        action('候选方案选择','ecosystem',mode='choose_execute')])
    done=prepare_public(s)
    assert done['cursor']==2 and done['state']['ecosystem']['selected'] is None
    candidates=done['context']['service_candidates']
    assert len(candidates)==2 and all(x['type']=='餐厅' for x in candidates)
    assert all(x['dish'] and x['wait_minutes']>=0 for x in candidates)
    assert all(e['trigger']=='automatic_public_preparation' for e in done['events'])
    assert prepare_public(done)==done


def test_prepare_does_not_confirm_private_or_control_calls_or_retry_failures():
    from engine.soa_simulator import prepare_public
    s=new_session('a'*32,'ESTJ',[action('通知读取','notification_mode')])
    assert prepare_public(s)==s
    s=new_session('a'*32,'ESTJ',[action('内容详情查看')])
    failed=prepare_public(s)
    assert len(failed['events'])==1 and failed['cursor']==0
    assert prepare_public(failed)==failed
