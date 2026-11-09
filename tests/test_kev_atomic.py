import pytest

from engine.kev_demo import Engine, PRESETS, compile_plan
from engine.kev_atomic import scope_catalog, action_templates, atomic_questions, resolve_actions, key
from engine.persona_demo import cases


def fixture(case_id):
    eng = Engine(use_llm=False)
    case = next(c for c in cases() if c['id'] == case_id)
    candidates = [{'id': i, 'name': n, 'description': d} for i, n, d in PRESETS[case_id]]
    scope = scope_catalog(eng, case_id, candidates, case['text'])
    templates = action_templates(candidates[0], case_id, '有序安排')
    questions = atomic_questions(scope, templates)
    answers = {q: {'choice': 'omit', 'probabilities': {'include': .1, 'omit': .9}} for q in questions}
    return eng, case, scope, templates, answers


@pytest.mark.parametrize('case_id', ['rest_stop', 'weekend', 'after_work'])
def test_every_catalog_entry_is_accounted_for_once_and_candidates_have_templates(case_id):
    eng, case, scope, templates, answers = fixture(case_id)
    ids = [r['id'] for r in scope['rows']+scope['excluded']]
    catalog = {key(d, a['name']) for d in eng.capabilities.all_ids() for a in eng.capabilities.get(d)['atomic_abilities']}
    assert len(ids) == len(set(ids)) == scope['total_catalog'] == 57
    assert set(ids) == catalog
    assert all(r['id'] in templates for r in scope['rows'])
    assert len(answers) == scope['scored_count']
    assert all(r.get('reason') for r in scope['rows']+scope['excluded'])


def test_dependencies_keep_original_model_probabilities_and_final_states_match_plan():
    eng, case, scope, templates, answers = fixture('after_work')
    by_id = {r['id']: r for r in scope['rows']}
    q = by_id[key('entertainment', '播放控制')]['question_id']
    answers[q] = {'choice': 'include', 'probabilities': {'include': .8, 'omit': .2}}
    actions, scores = resolve_actions(scope, templates, answers)
    status = {s['id']: s for s in scores}
    select = status[key('entertainment', '内容选择')]
    assert select['probability'] == .1 and select['model_selected'] is False
    assert select['adopted'] and '依赖补齐' in select['resolution']
    assert status[key('entertainment', '内容搜索')]['probability'] is None
    plan = compile_plan(eng, case['text'], case['world'], actions, '模拟结果')
    assert {key(a['capability'], a['name']) for a in plan['behavior_actions']} == {s['id'] for s in scores if s['adopted']}
    assert [a['name'] for a in plan['behavior_actions']] == ['内容搜索', '内容选择', '播放控制']


def test_competing_controls_keep_higher_probability_without_faking_rejection():
    eng, case, scope, templates, answers = fixture('after_work')
    for r in scope['rows']:
        if r['name'] in {'座椅加热控制', '座椅通风控制'}:
            p = .9 if r['name']=='座椅通风控制' else .8
            answers[r['question_id']] = {'choice': 'include', 'probabilities': {'include': p, 'omit': 1-p}}
    actions, scores = resolve_actions(scope, templates, answers)
    heat = next(s for s in scores if s['id']==key('seat', '座椅加热控制'))
    assert heat['model_selected'] and not heat['adopted'] and heat['probability']==.8
    assert '互斥' in heat['resolution']
    assert [a['n'] for a in actions] == ['座椅通风控制']


@pytest.mark.parametrize('case_id', ['rest_stop', 'weekend', 'after_work'])
def test_all_positive_decisions_compile_into_legal_atomic_plans(case_id):
    eng, case, scope, templates, answers = fixture(case_id)
    for a in answers.values():a.update(choice='include', probabilities={'include': .8, 'omit': .2})
    actions, scores = resolve_actions(scope, templates, answers)
    plan = compile_plan(eng, case['text'], case['world'], actions, '全部为虚拟演示')
    assert len(plan['behavior_actions']) == sum(s['adopted'] for s in scores)


def test_core_task_has_no_invented_probability_and_empty_rest_is_valid():
    eng, case, scope, templates, answers = fixture('rest_stop')
    actions, scores = resolve_actions(scope, templates, answers)
    selected = next(s for s in scores if s['id']==key('ecosystem','候选方案选择'))
    assert selected['adopted'] and selected['probability'] is None
    eng, case, scope, templates, answers = fixture('after_work')
    actions, scores = resolve_actions(scope, templates, answers)
    assert actions == []
    assert compile_plan(eng, case['text'], case['world'], [], '安静休息')['plan_status']=='no_tool_needed'


def test_selected_audio_source_is_applied_before_playback():
    eng, case, scope, templates, answers = fixture('after_work')
    for r in scope['rows']:
        if r['name'] in {'播放控制', '音源切换'}:
            answers[r['question_id']] = {'choice': 'include', 'probabilities': {'include': .8, 'omit': .2}}
    actions, _ = resolve_actions(scope, templates, answers)
    names = [a['n'] for a in actions]
    assert names.index('音源切换') < names.index('播放控制')
