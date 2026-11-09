from types import SimpleNamespace
import pytest
from engine import kev_preparation as prep,kev_free
from engine.kev_demo import Engine,compile_plan,KevDemoError


def recipe():
    return {'id':'A','name':'就近休息','description':'共同演示假设：前方休息区可安全停靠。',
            'spoken_reply':'先别硬撑，我先帮你找前方可以安全停靠的地方，停稳后再休息。',
            'target':'前方休息区（演示候选）','steps':['navigation/导航运行控制','notification_mode/非紧急通知屏蔽']}


def test_recipe_compiler_supplies_navigation_dependencies_and_valid_parameters():
    c=prep.expand_recipe(Engine(use_llm=False),recipe(),{'context':{'driving_state':'driving'}},compile_plan,'还在高速，有点累',kev_free.FREE_WORLD)
    assert [a['n'] for a in c['actions']][:6]==['地点搜索','地点详情查看','目的地设置','路线规划','路线选择','导航运行控制']
    assert '当前行驶位置' in next(a for a in c['actions'] if a['n']=='路线规划')['p']['起终点']
    assert all('当前仍为驻车' not in a['d'] for a in c['actions'])
    assert '不表示车辆已到达或已停车' in next(a for a in c['actions'] if a['n']=='导航运行控制')['d']
    bad=recipe();bad['steps']=['seat/座椅按摩控制']
    with pytest.raises(ValueError,match='行驶状态'):prep.expand_recipe(Engine(use_llm=False),bad,{'context':{'driving_state':'driving'}},compile_plan,'还在高速',kev_free.FREE_WORLD)


def test_single_neutral_call_caches_recipes_and_preserves_natural_speech(monkeypatch,tmp_path):
    monkeypatch.setattr(kev_free,'ROOT',tmp_path);client=SimpleNamespace()
    monkeypatch.setattr(prep,'get_llm',lambda *a:client)
    calls=[]
    def generate(*a,**k):
        calls.append(1)
        return {'situation':{'task':{'core_goal':'就近安全休息'},'facts':['还在高速'],'context':{'driving_state':'parked'}},'candidates':[recipe()]},''
    monkeypatch.setattr(prep,'chat_json_with_raw',generate)
    for _ in range(2):
        intent,candidates,meta=prep.prepare_shared(Engine(use_llm=False),'还在高速，累了',kev_free.FREE_WORLD,compile_plan)
        assert intent['output']['context']['driving_state']=='driving'
        assert candidates[0]['spoken_reply']==recipe()['spoken_reply']
    assert len(calls)==1 and meta['cache_hit']
    assert client.total_timeout==35 and client.model==prep.PREPARATION_MODEL


def test_failed_preparation_has_no_automatic_second_cloud_call(monkeypatch,tmp_path):
    monkeypatch.setattr(kev_free,'ROOT',tmp_path);monkeypatch.setattr(prep,'get_llm',lambda *a:SimpleNamespace())
    calls=[]
    def fail(*a,**k):calls.append(1);raise TimeoutError()
    monkeypatch.setattr(prep,'chat_json_with_raw',fail)
    with pytest.raises(KevDemoError,match='无自动长重试'):prep.prepare_shared(Engine(use_llm=False),'新的输入',kev_free.FREE_WORLD,compile_plan)
    assert len(calls)==1


def test_failed_job_is_terminal_and_keeps_elapsed_time(monkeypatch,tmp_path):
    from web.app import app
    from web import persona_demo as web
    from engine import kev_demo
    monkeypatch.setattr(web,'OUT',tmp_path);monkeypatch.setattr(web,'JOBS',{})
    monkeypatch.setattr(web,'POOL',SimpleNamespace(submit=lambda f,*a:f(*a)))
    def fail(text,world,jid,progress,**k):progress(stage='preparing');raise KevDemoError('准备失败')
    monkeypatch.setattr(kev_demo,'run_kev_demo',fail)
    client=app.test_client();jid=client.post('/api/persona-demo',json={'model':'kev','text':'测试失败'}).json['id']
    job=client.get('/api/persona-demo/'+jid).json
    assert job['status']=='failed' and job['stage']=='failed' and job['failed_stage']=='preparing'
    assert job['elapsed_seconds']>=0


def test_shared_required_terminals_are_completed_for_every_style():
    situation={'task':{'required_steps':['navigation/导航运行控制']},'context':{'driving_state':'driving'}}
    r=recipe();r['steps']=['entertainment/播放控制'];r['family']='feeling'
    c=prep.expand_recipe(Engine(use_llm=False),r,situation,compile_plan,'高速上累了',kev_free.FREE_WORLD)
    assert c['family']=='feeling'
    assert {'目的地设置','路线规划','路线选择','导航运行控制','内容选择','播放控制'}<={a['n'] for a in c['actions']}


def test_known_capability_alias_is_normalized_without_an_extra_model_call():
    r=recipe();r['steps']=['ecosystem/候选方案筛选']
    c=prep.expand_recipe(Engine(use_llm=False),r,{'context':{}},compile_plan,'推荐出游地点',kev_free.FREE_WORLD)
    assert [a['n'] for a in c['actions']]==['服务与地点搜索','候选服务筛选']


@pytest.mark.parametrize('fact,accepted',[('25分钟',True),('没有出处的数字',False)])
def test_grounded_short_fact_does_not_drop_infp_and_invalid_family_never_silently_falls_back(monkeypatch,tmp_path,fact,accepted):
    monkeypatch.setattr(kev_free,'ROOT',tmp_path)
    monkeypatch.setattr(prep,'get_llm',lambda *a:SimpleNamespace())
    a=recipe();a.update(family='order',selection_basis={'criterion':'车程','facts':['车程25分钟'],'reason':'较近','tradeoff':'不加音乐'})
    b=recipe();b.update(id='B',family='feeling',selection_basis={'criterion':'环境','facts':[fact],'reason':'舒适','tradeoff':'多花时间'})
    calls=[]
    def generate(*args,**kwargs):
        calls.append(1)
        return {'situation':{'task':{'choice_space':True},'context':{}},'shared_options':[{'id':'P1','facts':['车程25分钟']}],'candidates':[a,b]},''
    monkeypatch.setattr(prep,'chat_json_with_raw',generate)
    if accepted:
        _,bundles,_=prep.prepare_shared(Engine(use_llm=False),'自由出游测试',kev_free.FREE_WORLD,compile_plan)
        assert {b['family'] for b in bundles}=={'order','feeling'}
        assert bundles[1]['selection_basis']['facts']==['车程25分钟']
    else:
        with pytest.raises(KevDemoError):prep.prepare_shared(Engine(use_llm=False),'自由出游测试',kev_free.FREE_WORLD,compile_plan)
    assert len(calls)==1
