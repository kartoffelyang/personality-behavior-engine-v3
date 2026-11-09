import copy
from types import SimpleNamespace
import pytest
from engine import kev_intent_atomic as flow, kev_free
from engine.kev_demo import Engine, compile_plan, KevDemoError


@pytest.fixture(autouse=True)
def no_cloud_final_wording(monkeypatch):
    monkeypatch.setattr('engine.kev_final_reply.rewrite_replies',lambda *a:dict(source='unit_test_compiled',cloud_calls=0))


def observation():
    return {'task':{'core_goal':'就近安全休息并支持车内情绪','must_do':['安全停靠']},
            'context':{'driving_state':'driving'},'facts':['开车不能专心','孩子哭闹','工作受挫'],
            'needs':[{'need':'驾驶安全','evidence':'开车不能专心','explicit':True},
                     {'need':'儿童安抚','evidence':'孩子哭闹','explicit':False},
                     {'need':'成人情绪支持','evidence':'工作受挫','explicit':False}]}


def opportunities():
    return [{'ability':'navigation/导航运行控制','required':True,'reason':'先就近停靠再处理分心','tags':['efficiency']},
            {'ability':'entertainment/播放控制','required':False,'reason':'用儿童故事提供可选安抚','tags':['experience','emotional_support']}]


def resources():
    return {'places':[{'name':'最近服务区','minutes':5,'cost':0,'environment':'可安全休息'}],
            'media':[{'name':'小熊晚安故事','description':'柔和的儿童故事'}]}


def test_observation_is_persona_neutral_one_call_and_resources_are_optional(monkeypatch,tmp_path):
    monkeypatch.setattr(kev_free,'ROOT',tmp_path);calls=[]
    monkeypatch.setattr(flow,'get_llm',lambda *a:SimpleNamespace())
    raw={'situation':observation(),'opportunities':opportunities()}
    def chat(client,messages,**kwargs):
        calls.append(messages)
        assert '"candidates":' not in messages[0]['content'] and '"spoken_reply":' not in messages[0]['content']
        return copy.deepcopy(raw),''
    monkeypatch.setattr(flow,'chat_json_with_raw',chat)
    for _ in range(2):
        u,ops,r,meta=flow.observe(Engine(use_llm=False),'开车不能专心，孩子哭闹，工作受挫',kev_free.FREE_WORLD)
        assert u['context']['driving_state']=='driving' and len(u['needs'])==3 and r=={}
    assert len(calls)==1 and meta['cache_hit']


def test_one_invalid_optional_op_does_not_destroy_valid_ones():
    ops=opportunities()+[{'ability':'ambient_light/氛围灯开关','reason':'情绪支持','tags':['experience']},
                         {'ability':'wrong/id','reason':'错误名称','tags':[]}]
    pool,rejected=flow.discover(Engine(use_llm=False),'开车不能专心',kev_free.FREE_WORLD,observation(),ops,resources(),compile_plan)
    assert len(pool)==2 and len(rejected)==2
    assert all('氛围灯' not in a['n'] for o in pool.values() for a in o['recipes']['INFP']['actions'])


def test_score_driven_atomic_selection_preserves_common_safety_and_raw_scores(monkeypatch):
    monkeypatch.setattr(flow,'observe',lambda *a:(observation(),opportunities(),resources(),{'seconds':0,'cache_hit':False}))
    requests=[]
    def decide(state,questions):
        requests.append(copy.deepcopy(state))
        assert len(questions)==57 and 'bundle' not in questions
        return {'answers':{q:{'choice':'include','probabilities':{'include':.55,'omit':.45}} for q in questions}},.01
    r=flow.run_atomic_pipeline(Engine(use_llm=False),'开车不能专心，孩子哭闹',kev_free.FREE_WORLD,'x',lambda **k:None,decide,compile_plan)
    estj,infp=r['variants']['ESTJ'],r['variants']['INFP']
    assert '导航运行控制' in {a['name'] for a in estj['actions']} & {a['name'] for a in infp['actions']}
    assert '播放控制' not in {a['name'] for a in estj['actions']}
    assert '播放控制' in {a['name'] for a in infp['actions']}
    assert all(s['probability']==.55 for s in infp['atomic_scores'])
    for v in (estj,infp):
        assert all(s['priority_score']>=.5 for s in v['atomic_scores'] if s['adopted'] and s['priority_score'] is not None and s['decision_source']!='共同任务')
        assert all(not s['adopted'] for s in v['atomic_scores'] if s['id']=='seat/座椅按摩控制')
    assert estj['reply']!=infp['reply'] and '小熊晚安故事' in infp['reply']
    assert requests[0]['candidates']==requests[1]['candidates']
    assert requests[0]['situation']==requests[1]['situation']
    assert r['model_calls_minimum']==3 and r['contrast']['mode']=='atomic_pipeline'


def test_exact_temperature_has_no_optional_personality_side_effects(monkeypatch):
    situation={'task':{'core_goal':'只将温度设为24度','constraints':['不要做其他事情']},'context':{}}
    ops=[{'ability':'climate/温度调节','parameters':{'温度':24},'required':True,'reason':'明确设置24度','tags':[]}]
    monkeypatch.setattr(flow,'observe',lambda *a:(situation,ops,{},dict(seconds=0,cache_hit=False)))
    def decide(state,questions):return {'answers':{q:{'choice':'omit','probabilities':{'include':.01,'omit':.99}} for q in questions}},0
    result=flow.run_atomic_pipeline(Engine(use_llm=False),'只把前排温度设为24度，不要做其他事情',kev_free.FREE_WORLD,'x',lambda **k:None,decide,compile_plan)
    for v in result['variants'].values():
        assert {a['name'] for a in v['actions']}=={'空调开关','温度调节'}
        assert next(a for a in v['actions'] if a['name']=='温度调节')['parameters']['温度']==24


def test_bad_required_op_is_reported_as_compilation_not_timeout():
    with pytest.raises(KevDemoError,match='共同必需动作'):
        flow.discover(Engine(use_llm=False),'开车',kev_free.FREE_WORLD,observation(),[{'ability':'seat/座椅按摩控制','required':True,'reason':'错误必需'}],{},compile_plan)


def test_resource_selection_comes_from_kev_not_fixed_persona_plan(monkeypatch):
    u={'task':{'core_goal':'推荐一个出游地点','action_requested':True},'context':{'driving_state':'parked'}}
    ops=[{'ability':'ecosystem/候选方案选择','required':True,'reason':'给出具体去处','tags':[]}]
    r={'places':[{'name':'近处公园','minutes':10,'cost':0,'environment':'林荫'},
                 {'name':'河边小院','minutes':25,'cost':30,'environment':'临水'}]}
    monkeypatch.setattr(flow,'observe',lambda *a:(u,ops,r,dict(seconds=0,cache_hit=True)))
    def decide(state,questions):
        answers={q:{'choice':'include','probabilities':{'include':.6,'omit':.4}} for q in questions if q!='resource_place'}
        # Reverse any assumed ESTJ=first/INFP=second mapping.
        chosen='P2' if state['role']['id']=='ESTJ' else 'P1'
        answers['resource_place']={'choice':chosen,'probabilities':{'P1':1e-8 if chosen=='P2' else .99999999,'P2':.99999999 if chosen=='P2' else 1e-8}}
        return {'answers':answers},0
    result=flow.run_atomic_pipeline(Engine(use_llm=False),'推荐出游地点',kev_free.FREE_WORLD,'x',lambda **k:None,decide,compile_plan)
    assert '河边小院' in result['variants']['ESTJ']['reply']
    assert '近处公园' in result['variants']['INFP']['reply']
    assert result['variants']['ESTJ']['decisions'][0]['choice']=='P2'


def test_control_action_alias_and_numeric_slots_match_catalog():
    eng=Engine(use_llm=False)
    assert flow.normalize_parameters(eng,'navigation','导航运行控制',{'动作':'开始'})=={'开始':True}
    assert flow.normalize_parameters(eng,'climate','温度调节',{'温度':'24度','extra':'junk'})=={'温度':24}


def test_temperature_wording_does_not_invent_rest_or_silence():
    from engine.kev_wording import spoken_reply
    reply,_=spoken_reply('设为24度',{'actions':[{'c':'climate','n':'温度调节','p':{'温度':24}}],'description':'调温'})
    assert reply=='空调调到24度。'


def test_stop_navigation_does_not_create_a_destination_or_restart_route(monkeypatch):
    u={'task':{'core_goal':'停止导航','action_requested':True},'context':{}}
    ops=[{'ability':'navigation/导航运行控制','required':True,'reason':'明确要求结束当前导航','parameters':{'动作':'停止'},'tags':[]}]
    monkeypatch.setattr(flow,'observe',lambda *a:(u,ops,{},dict(seconds=0,cache_hit=True)))
    def decide(state,questions):return {'answers':{q:{'choice':'include','probabilities':{'include':.9,'omit':.1}} for q in questions}},0
    result=flow.run_atomic_pipeline(Engine(use_llm=False),'停止导航',kev_free.FREE_WORLD,'x',lambda **k:None,decide,compile_plan)
    for v in result['variants'].values():
        assert [a['name'] for a in v['actions']]==['导航运行控制']
        assert v['actions'][0]['parameters']=={'结束':True}
        assert v['reply']=='已结束当前导航。'


def test_quiet_notifications_do_not_require_reading_or_missing_templates():
    eng=Engine(use_llm=False)
    u={'task':{'core_goal':'留出安静休息时间','action_requested':True},'context':{'driving_state':'parked'}}
    ops=[{'ability':'notification_mode/通知摘要','required':True,'reason':'可能先提炼消息','tags':['structure']},
         {'ability':'notification_mode/通知优先级分类','required':True,'reason':'可能先分类','tags':['structure']},
         {'ability':'notification_mode/非紧急通知屏蔽','required':False,'reason':'不读内容先静音','tags':['privacy']}]
    pool,rejected=flow.discover(eng,'好多消息，没有急事，想清静一点，帮我安排',kev_free.FREE_WORLD,u,ops,{},compile_plan)
    assert not rejected
    assert not pool['notification_mode/通知摘要']['required']
    assert not pool['notification_mode/通知优先级分类']['required']
    assert pool['notification_mode/非紧急通知屏蔽']['required']
    assert '通知读取' in {a['n'] for a in pool['notification_mode/通知摘要']['recipes']['ESTJ']['actions']}
