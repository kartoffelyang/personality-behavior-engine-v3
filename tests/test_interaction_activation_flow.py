"""Regression coverage for situational activation without an extra model call."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import pipeline
from engine.model_intent import run_intent
from planner_fixtures import navigation_plan
from test_intent_contract import output_fixture


FIXED = {'主动性', '确认严格度', '解释透明度', '控制权保留度'}
ACTIVATIONS = [
    {'dimension': '时机敏感', 'evidence': '用户正在并线',
     'context_requirement': '非紧急提示等用户完成并线后再呈现。'},
    {'dimension': '社会礼仪敏感', 'evidence': '用户说车上有客户，不要念私人安排',
     'context_requirement': '公开播报中不包含用户的私人安排。'},
    {'dimension': '语音信息密度', 'evidence': '用户说只讲关键结论',
     'context_requirement': '单轮只讲关键结论，详细依据按需展开。'},
    {'dimension': '服务延伸', 'evidence': '用户明确说不要推荐其他服务',
     'context_requirement': '仅处理本次目的地导航，不推荐其他服务。'},
]
UTTERANCE = '今天早上十点前去康源大厦见客户。正在并线，只讲关键结论，不要念私人安排，也不要推荐其他服务。'


def partial_run(persona, count):
    request = pipeline.default_request_scaffold(persona, UTTERANCE)
    shared = run_intent(request, 'rules', None)
    shared['output']['activated_interaction_dimensions'] = deepcopy(ACTIVATIONS[:count])
    before = deepcopy(shared)
    result = pipeline.generate_behavior_plan(
        request, llm_backend='rules', shared_intent=shared, defer_step7=True,
    )
    assert shared == before
    return result


@pytest.mark.parametrize('persona', ['INFP', 'ESTJ'])
@pytest.mark.parametrize('count', [0, 1, 4])
def test_step6_combines_four_fixed_dimensions_with_only_selected_optional_dimensions(persona, count):
    result = partial_run(persona, count)
    steps = {step['step']: step for step in result['audit_steps']}
    policy = steps[6]['output']['interaction_policy']
    assert set(policy['fixed_dimensions']) == FIXED
    assert policy['activated_dimensions'] == ACTIVATIONS[:count]
    constraints = policy['constraints']
    assert len(constraints) == 4 + count
    assert {item['dimension'] for item in constraints} == FIXED | {
        item['dimension'] for item in ACTIVATIONS[:count]
    }
    assert len({item['dimension'] for item in constraints}) == len(constraints)
    assert all(item['description'].strip() for item in constraints)
    for selected in ACTIVATIONS[:count]:
        actual = next(item for item in constraints if item['dimension'] == selected['dimension'])
        assert actual['context_requirement'] == selected['context_requirement']
        assert actual['evidence'] == selected['evidence']
        assert selected['context_requirement'] in policy['compiled_prompt']
    step7_flow = steps[7]['input']['flow_context']
    assert step7_flow['scenario_understanding']['activated_interaction_dimensions'] == ACTIVATIONS[:count]


@pytest.mark.parametrize('count', [0, 4])
def test_no_mbti_retains_situational_requirements_without_persona_defaults(count):
    result = partial_run('NO_MBTI', count)
    step6 = next(step for step in result['audit_steps'] if step['step'] == 6)
    policy = step6['output']['interaction_policy']
    assert policy['fixed_dimensions'] == []
    assert policy['activated_dimensions'] == ACTIVATIONS[:count]
    assert len(policy['constraints']) == count
    assert {item['dimension'] for item in policy['constraints']} == {
        item['dimension'] for item in ACTIVATIONS[:count]
    }
    for selected in ACTIVATIONS[:count]:
        assert selected['context_requirement'] in policy['compiled_prompt']


class CountingClient:
    model = 'activation-regression-fixture'

    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        if len(self.calls) == 1:
            output = output_fixture()
            output['activated_interaction_dimensions'] = deepcopy(ACTIVATIONS)
            return json.dumps(output, ensure_ascii=False)
        return json.dumps(navigation_plan(), ensure_ascii=False)


def test_live_flow_uses_only_existing_two_model_calls_and_sends_compact_policy(monkeypatch):
    client = CountingClient()
    monkeypatch.setattr(pipeline, 'get_llm', lambda *args, **kwargs: client)
    result = pipeline.generate_behavior_plan(
        pipeline.default_request_scaffold('ESTJ', UTTERANCE), llm_backend='openai',
    )
    assert result['generation_status'] == 'ok'
    assert len(client.calls) == 2
    assert result['audit_steps'][1]['status'] == 'ok'
    payload = json.loads(client.calls[1][1]['content'])
    interaction_input = payload['flow_context']['interaction_dimensions']
    assert 'raw_interaction' not in interaction_input
    assert 'scaled_1_to_5' not in interaction_input
    assert 'function_dimension_descriptions' not in interaction_input
    serialized = json.dumps(interaction_input, ensure_ascii=False)
    for selected in ACTIVATIONS:
        assert selected['context_requirement'] in serialized


def test_saved_legacy_step2_without_activation_can_resume_without_repeating_step2(monkeypatch):
    previous = partial_run('INFP', 0)
    step2 = next(step for step in previous['audit_steps'] if step['step'] == 2)
    step2['output'].pop('activated_interaction_dimensions', None)
    previous['_review_internal']['intent_output'].pop('activated_interaction_dimensions', None)
    flow = previous['_review_internal']['flow_context']
    flow['scenario_understanding'].pop('activated_interaction_dimensions', None)
    flow['interaction_dimensions'].pop('interaction_policy', None)
    before = deepcopy(previous)

    def unexpected_step2(*args, **kwargs):
        raise AssertionError('Resuming Step 7 must not invoke Step 2 again')

    monkeypatch.setattr(pipeline, 'run_intent', unexpected_step2)
    resumed = pipeline.rerun_from_review_step(previous, step_no=7, llm_backend='rules')
    step7 = next(step for step in resumed['audit_steps'] if step['step'] == 7)
    assert step7['status'] != 'pending'
    assert step7['input']['flow_context']['scenario_understanding']['activated_interaction_dimensions'] == []
    policy = step7['input']['flow_context']['interaction_dimensions']['interaction_policy']
    assert set(policy['fixed_dimensions']) == FIXED
    assert policy['activated_dimensions'] == []
    assert len(policy['constraints']) == 4
    assert previous == before
