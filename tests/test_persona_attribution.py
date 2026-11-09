import pytest
from engine.persona_attribution import classify_influence, annotate_actions


@pytest.mark.parametrize('text', ['人格未改变此动作', '人格未改变此动作。', '无明显人格影响', '无人格影响', '没有明显影响', '无'])
def test_negative_variants_are_not_colored(text):
    assert classify_influence({'persona_influence': text}, 'INFP')['status'] == 'none'


def test_unknown_is_not_treated_as_no_effect_or_verified_effect():
    for text in ('', '维护用户隐私', 'INFP更注重用户感受'):
        assert classify_influence({'persona_influence': text}, 'INFP')['status'] == 'review'


def test_exact_strategy_and_action_evidence_required():
    strategy = '任务状态下，优先调用通知模块'
    details = '优先分类任务相关通知，按截止时间排序。'
    action = {'details': details, 'persona_influence': f'依据：{strategy}；改变：任务相关通知优先处理；落点：优先分类任务相关通知'}
    assert classify_influence(action, 'ESTJ', {'strategy': strategy})['status'] == 'grounded'
    assert classify_influence(action, 'NO_MBTI', {'strategy': strategy})['status'] == 'none'
    assert classify_influence(action, 'ESTJ', {})['status'] == 'review'
    assert classify_influence({**action, 'details': '普通动作'}, 'ESTJ', {'strategy': strategy})['status'] == 'review'


def test_annotation_uses_saved_strategy_and_does_not_mutate_source():
    action = {'persona_influence': '人格未改变此动作。'}
    result = {'audit_steps': []}
    annotated = annotate_actions(result, 'INFP', [action])
    assert annotated[0]['persona_attribution']['status'] == 'none'
    assert 'persona_attribution' not in action
