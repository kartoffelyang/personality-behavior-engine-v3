import copy
from itertools import product

from engine.kev_demo import Engine, PRESETS, compile_plan
from engine.kev_bundles import rest_bundles, feature_set, evaluate_bundles, run_strong_rest
from engine.kev_atomic import scope_catalog, key
from engine.persona_demo import cases


def setup():
    eng=Engine(use_llm=False)
    case=next(c for c in cases() if c['id']=='after_work')
    resources=[{'id':i,'name':n,'description':d} for i,n,d in PRESETS['after_work']]
    return eng,case,resources,rest_bundles(resources)


def fake_decide(state,questions):
    ids=list(questions['bundle']['criteria'])
    # Simulate an unhelpful model choosing the same order-plan for both roles.
    probs={i:(.5 if i=='O1' else .1) for i in ids}
    return {'answers':{'bundle':{'choice':'O1','probabilities':probs}}},.01


def test_every_allowed_pair_has_three_experience_differences_not_three_light_steps():
    eng,c,resources,bundles=setup()
    for b in bundles:compile_plan(eng,c['text'],c['world'],b['actions'],b['description'])
    order=[b for b in bundles if b['family']=='order'];feeling=[b for b in bundles if b['family']=='feeling']
    for a,b in product(order,feeling):
        difference=feature_set(a['actions'])^feature_set(b['actions'])
        assert {'通知管理','灯光氛围','身体放松'} <= difference
        assert all(x not in feature_set(a['actions']) for x in ('灯光开关','颜色设置','亮度调节'))


def test_raw_scores_are_unchanged_and_policy_override_is_disclosed():
    eng,c,resources,bundles=setup()
    scope=scope_catalog(eng,'after_work',resources,c['text'])
    variants,audits,scope,contrast=evaluate_bundles(eng,c['text'],c['world'],bundles,scope,fake_decide,compile_plan,lambda **k:None,strong=True)
    assert contrast['policy_override_count']==1
    assert variants['INFP']['bundle_choice']['raw_choice']=='O1'
    assert variants['INFP']['bundle_choice']['policy_overridden']
    assert audits['INFP']['raw_response']['answers']['bundle']['probabilities']['O1']==.5
    assert len(contrast['meaningful_differences'])>=3
    assert audits['ESTJ']['request']['questions']==audits['INFP']['request']['questions']
    for pid,v in variants.items():
        assert {s['id'] for s in v['atomic_scores'] if s['adopted']}=={key(a['domain'],a['name']) for a in v['actions']}


def test_bundle_marginals_are_derived_and_uncovered_controls_not_scored():
    eng,c,resources,bundles=setup()
    result=run_strong_rest(eng,c['text'],c['world'],resources,fake_decide,compile_plan,lambda **k:None)
    scores={s['id']:s for s in result['variants']['INFP']['atomic_scores']}
    assert scores[key('ambient_light','颜色设置')]['probability']==.3
    assert scores[key('ambient_light','颜色设置')]['probability_source']=='raw_bundle_marginal'
    assert scores[key('climate','温度调节')]['probability'] is None
    assert 'compiled_prompt' not in str(result['audits']['ESTJ']['request'])
    assert result['audits']['ESTJ']['request']['state']['role']['authored_demo_scope']['allowed_plan_ids']==['O1','O2','O3']


def test_full_catalog_scores_are_independent_and_change_selection_inside_role_family():
    from engine.kev_bundles import run_strong_scene
    def scored(state,questions):
        result=fake_decide(state,questions)[0]
        for q,spec in questions.items():
            if q=='bundle':continue
            p=.95 if any(n in spec['instructions'] for n in ('非紧急通知屏蔽','内容搜索','内容选择','播放控制')) else .05
            result['answers'][q]={'choice':'include' if p>.5 else 'omit','probabilities':{'include':p,'omit':1-p}}
        return result,.01
    eng,c,resources,_=setup()
    result=run_strong_scene(eng,c['text'],c['world'],resources,'after_work',scored,compile_plan,lambda **k:None)
    assert len(result['atomic_scope']['rows'])==57
    assert result['atomic_scope']['scored_count']==57
    assert result['variants']['ESTJ']['bundle_choice']['selected']=='O2' # raw bundle vote favored O1
    for v in result['variants'].values():
        assert len(v['atomic_scores'])==57
        assert all(s['probability_source']=='independent_binary_adoption' for s in v['atomic_scores'])
        assert all(s['probability'] is not None for s in v['atomic_scores'])
    assert result['variants']['ESTJ']['bundle_choice']['atomic_fit_scores']['O2'] > result['variants']['ESTJ']['bundle_choice']['atomic_fit_scores']['O1']


def test_other_two_strong_scenes_compile_every_pair_and_show_three_adoption_categories():
    from engine.kev_scenes import scene_definition
    for cid in ('rest_stop','weekend'):
        eng=Engine(use_llm=False);c=next(c for c in cases() if c['id']==cid)
        resources=[dict(id=i,name=n,description=d) for i,n,d in PRESETS[cid]]
        bundles,policies,groups,minimum=scene_definition(cid,resources)
        for b in bundles:compile_plan(eng,c['text'],c['world'],b['actions'],b['description'])
        for a,b in product(bundles[:3],bundles[3:]):
            assert {'成本比较','路线比较','内容播放'}<=feature_set(a['actions'])^feature_set(b['actions'])
            assert len(feature_set(a['actions'])^feature_set(b['actions']))>=minimum
        assert len(groups)==3
