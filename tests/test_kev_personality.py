import copy
from engine.kev_personality import personality_input,role_policies
from engine.kev_bundles import evaluate_bundles,rest_bundles
from engine.kev_demo import Engine,PRESETS,compile_plan
from engine.kev_atomic import scope_catalog
from engine.persona_demo import cases
from engine.persona_profile import build_persona_profile


def test_versioned_policy_preserves_original_description_and_contextual_boundaries():
    eng=Engine(use_llm=False)
    for pid in ('ESTJ','INFP'):
        value=personality_input(eng,pid)
        assert value['persona_profile']==build_persona_profile(pid,eng.personas.get(pid))
        assert value['policy_version']=='role-policy-v2'
        assert all(value['role_policy'][k] for k in ('cognitive_basis','priorities','prefer_when','avoid_when','stop','interaction'))
        assert '不是判断说话用户的人格' in value['role_identity']
        assert '不等待真人确认' in value['role_policy']['interaction']
    infp=role_policies()['INFP']
    assert set(infp['cognitive_basis'])=={'Fi','Ne'}
    assert any('默认音乐、灯光或按摩' in s for s in infp['avoid_when'])
    assert set(role_policies()['ESTJ']['cognitive_basis'])=={'Te','Si'}


def test_actual_decision_requests_share_strengthened_rules_and_unified_role_scope():
    eng=Engine(use_llm=False);c=next(c for c in cases() if c['id']=='after_work')
    resources=[dict(id=i,name=n,description=d) for i,n,d in PRESETS['after_work']]
    bundles=rest_bundles(resources);scope=scope_catalog(eng,'after_work',bundles,c['text'])
    def run(strong):
        captured=[]
        def decide(state,questions):
            captured.append(copy.deepcopy(state))
            return {'answers':{q:{'choice':next(iter(s['criteria'])),'probabilities':{k:1/len(s['criteria']) for k in s['criteria']}} for q,s in questions.items()}},.01
        variants,audits,*_=evaluate_bundles(eng,c['text'],c['world'],bundles,scope,decide,compile_plan,lambda **k:None,strong=strong,score_atoms=True)
        return captured,variants,audits
    free,fv,fa=run(False);scene,sv,sa=run(True)
    for idx,pid in enumerate(('ESTJ','INFP')):
        assert set(free[idx])==set(scene[idx])=={'situation','constraints','role','candidates'}
        assert free[idx]['role']['rules']==scene[idx]['role']['rules']==role_policies()[pid]['compact_rules']
        assert 'persona_profile' not in free[idx] and 'role_policy' not in free[idx]
        assert free[idx]['role']['authored_demo_scope']['allowed_plan_ids']==scene[idx]['role']['authored_demo_scope']['allowed_plan_ids']
        assert scene[idx]['role']['authored_demo_scope']['allowed_plan_ids']==(['O1','O2','O3'] if pid=='ESTJ' else ['F1','F2','F3'])
        assert fv[pid]['bundle_choice']['input_version']=='kev-compact-v4'
        assert fa[pid]['personality_input']['persona_profile']==fa[pid]['explanation']['profile']
        assert sa[pid]['selection']['scene_role_constraints']['allowed_family'] in {'order','feeling'}
