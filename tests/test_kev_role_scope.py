import copy
from engine.kev_role_scope import role_scope
from engine.kev_bundles import evaluate_bundles, rest_bundles
from engine.kev_demo import Engine, PRESETS, compile_plan
from engine.kev_atomic import scope_catalog
from engine.persona_demo import cases

POLICY = {'goal': '测试角色取舍', 'priorities': ['共同任务优先']}


def test_names_alone_cannot_activate_role_filter():
    def bundle(i, family, poi):
        return {'id': i, 'family': family, 'description': poi,
                'actions': [{'c':'navigation','n':'目的地设置','p':{'POI':poi}}]}
    bundles=[bundle('A','order','公园'),bundle('B','feeling','绿道')]
    scope=role_scope('ESTJ',bundles,POLICY)
    assert not scope['applied'] and scope['allowed_plan_ids']==['A','B']


def test_explicit_single_task_disables_authored_filter():
    bundles=[{'id':'A','family':'order','description':'24度','actions':[{'c':'climate','n':'温度调节','p':{'温度':24}}]},
             {'id':'B','family':'feeling','description':'25度','actions':[{'c':'climate','n':'温度调节','p':{'温度':25}}]}]
    assert role_scope('ESTJ',bundles,POLICY)['applied']
    assert not role_scope('ESTJ',bundles,POLICY,{'task':{'choice_space':False}})['applied']


def test_generated_and_preset_plans_use_same_filter_keep_raw_scores_and_differ_in_action():
    eng=Engine(use_llm=False);case=next(c for c in cases() if c['id']=='after_work')
    resources=[dict(id=i,name=n,description=d) for i,n,d in PRESETS['after_work']]
    bundles=rest_bundles(resources)
    for b in bundles:
        b['spoken_reply']='先处理提醒，再安静休息。' if b['family']=='order' else '先放段钢琴，再把环境调柔和。'
    def decide(state,questions):
        # Both personas' raw predictions deliberately prefer O1.
        answers={q:{'choice':'include','probabilities':{'include':.61,'omit':.39}}
                 for q in questions if q!='bundle'}
        answers['bundle']={'choice':'O1','probabilities':{b['id']:(.9 if b['id']=='O1' else .02) for b in bundles}}
        return {'answers':answers},.01
    before=copy.deepcopy(bundles);results=[]
    for strong in (False,True):
        v,a,scope,contrast=evaluate_bundles(eng,case['text'],case['world'],bundles,
            scope_catalog(eng,'after_work',bundles,case['text']),decide,compile_plan,lambda **k:None,strong=strong,score_atoms=True)
        assert v['ESTJ']['bundle_choice']['selected'].startswith('O')
        assert v['INFP']['bundle_choice']['selected'].startswith('F')
        assert v['ESTJ']['reply']!=v['INFP']['reply']
        assert v['ESTJ']['decisions'][0]['scores']==v['INFP']['decisions'][0]['scores']
        assert all(x['probability']==.61 for x in v['INFP']['atomic_scores'])
        assert {x['name'] for x in v['ESTJ']['actions']}!={x['name'] for x in v['INFP']['actions']}
        assert contrast['unified_role_rules'] and '人为' in contrast['disclosure']
        assert all(v[p]['bundle_choice']['runtime_input']['role']['authored_demo_scope']['applied'] for p in v)
        results.append(v)
    assert results[0]['INFP']['bundle_choice']['eligible']==results[1]['INFP']['bundle_choice']['eligible']
    assert bundles==before


def test_low_reference_score_can_be_a_dependency_with_explicit_reason():
    from engine.kev_role_scope import atomic_decision_reason
    bundles=[{'id':'A','actions':[{'c':'entertainment','n':'内容搜索'}]},
             {'id':'B','actions':[]}]
    source,reason=atomic_decision_reason({'id':'entertainment/内容搜索','role':'prerequisite'},True,bundles,[bundles[0]],'A',True)
    assert source=='必要前置' and '不作为开关' in reason
    source,reason=atomic_decision_reason({'id':'entertainment/内容搜索','role':'candidate'},False,bundles,[bundles[1]],'B',True)
    assert source=='角色规则排除' and '参考分保留' in reason


def test_spoken_reply_names_the_actual_selected_place_without_another_cloud_request():
    from engine.kev_wording import spoken_reply
    selected={'spoken_reply':'这里25分钟、免费，我先选它。',
              'actions':[{'c':'navigation','n':'目的地设置','p':{'POI':'B 江畔公园'}}]}
    reply,source=spoken_reply('出去走走',selected)
    assert reply=='江畔公园25分钟、免费，我先选它。'
    assert source=='shared_cloud_candidate'
