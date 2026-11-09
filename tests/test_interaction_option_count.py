from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine.pipeline as pipeline
from engine.model_planner import run_planner
from planner_fixtures import navigation_plan


def partial_run(persona):
    return pipeline.generate_behavior_plan(
        pipeline.default_request_scaffold(persona, '今天早上十点前去康源大厦见客户。'),
        llm_backend='rules', defer_step7=True,
    )


def assert_quantity(dimensions, expected):
    raw = dimensions['raw_interaction']
    assert 'option_count' not in dimensions['scaled_1_to_5']
    if expected is None:
        assert 'option_count' not in raw
        assert dimensions['scaled_1_to_5'] == {}
    else:
        assert type(raw['option_count']) is int
        assert raw['option_count'] == expected


@pytest.mark.parametrize('persona,count,confirmation', [('INFP', 2, 4), ('ESTJ', 1, 2), ('NO_MBTI', None, None)])
def test_step6_keeps_legacy_quantity_while_pending_step7_uses_selected_policy(persona, count, confirmation):
    result = partial_run(persona)
    steps = {step['step']: step for step in result['audit_steps']}
    dimensions = steps[6]['output']
    assert_quantity(dimensions, count)
    planner_dimensions = steps[7]['input']['flow_context']['interaction_dimensions']
    policy = dimensions['interaction_policy']
    assert planner_dimensions['compiled_prompt'] == policy['compiled_prompt']
    assert planner_dimensions['fixed_dimensions'] == policy['fixed_dimensions']
    assert 'raw_interaction' not in planner_dimensions
    assert 'scaled_1_to_5' not in planner_dimensions
    if count is not None:
        assert dimensions['scaled_1_to_5']['confirmation'] == confirmation
        assert '固定维度：4 项；情境激活：0 项' in steps[6]['output_text']
        assert 'option_count=5' not in steps[6]['output_text']
    else:
        assert '固定维度：0 项；情境激活：0 项' in steps[6]['output_text']
    assert '选项数量' not in steps[6]['output_text']


class CapturingClient:
    model = 'option-count-test'

    def chat(self, messages, **kwargs):
        self.messages = messages
        self.payload = json.loads(messages[1]['content'])
        return json.dumps(navigation_plan(), ensure_ascii=False)


@pytest.mark.parametrize('persona,count', [('INFP', 2), ('ESTJ', 1)])
def test_saved_step7_rerun_repairs_model_input_and_step6_without_repeating_step2(monkeypatch, persona, count):
    previous = json.loads(json.dumps(partial_run(persona)))
    step6 = next(s for s in previous['audit_steps'] if s['step'] == 6)
    step6['output']['scaled_1_to_5']['option_count'] = 5
    step6['output_text'] = f'option_count=5（原始 {count}）'
    previous['_review_internal']['flow_context']['interaction_dimensions']['scaled_1_to_5']['option_count'] = 5
    original = deepcopy(previous)
    client = CapturingClient()
    monkeypatch.setattr(pipeline, 'get_llm', lambda *args, **kwargs: client)

    def unexpected_step2(*args, **kwargs):
        raise AssertionError('Step 7 rerun must reuse Step 2')

    monkeypatch.setattr(pipeline, 'run_intent', unexpected_step2)
    result = pipeline.rerun_from_review_step(previous, step_no=7, llm_backend='openai')
    assert result['generation_status'] == 'ok'
    assert result['O_Action_Plan']
    planner_dimensions = client.payload['flow_context']['interaction_dimensions']
    assert set(planner_dimensions['fixed_dimensions']) == {
        '主动性', '确认严格度', '解释透明度', '控制权保留度',
    }
    assert planner_dimensions['active_dimensions'] == planner_dimensions['fixed_dimensions']
    assert planner_dimensions['compiled_prompt'].strip()
    assert 'raw_interaction' not in planner_dimensions
    assert 'scaled_1_to_5' not in planner_dimensions
    steps = {s['step']: s for s in result['audit_steps']}
    assert_quantity(steps[6]['output'], count)
    assert '固定维度：4 项；情境激活：0 项' in steps[6]['output_text']
    assert '选项数量' not in steps[6]['output_text']
    assert 'option_count=5' not in steps[6]['output_text']
    assert_quantity(steps[7]['input']['flow_context']['interaction_dimensions'], count)
    assert_quantity(result['_review_internal']['flow_context']['interaction_dimensions'], count)
    assert steps[7]['prompt_used']['user_prompt'] == client.messages[1]['content']
    assert previous == original


def test_planner_projection_repairs_legacy_quantity_without_mutating_input():
    context = {
        'interaction_dimensions': {
            'raw_interaction': {'option_count': 2, 'confirmation': 0.85},
            'scaled_1_to_5': {'option_count': 5, 'confirmation': 4},
        },
    }
    original = deepcopy(context)
    client = CapturingClient()
    run_planner(
        llm_backend='openai', client=client, persona_id='INFP', scenario_id='time_pressure',
        parsed={}, whitelist=[], rule_summary=[], flow_context=context, blueprint={},
    )
    assert_quantity(client.payload['flow_context']['interaction_dimensions'], 2)
    assert client.payload['flow_context']['interaction_dimensions']['scaled_1_to_5']['confirmation'] == 4
    assert context == original


def test_explicit_user_prompt_override_is_preserved_verbatim():
    client = CapturingClient()
    override = '{"custom_instruction": "user-authored prompt"}'
    result = run_planner(
        llm_backend='openai', client=client, persona_id='INFP', scenario_id='time_pressure',
        parsed={}, whitelist=[], rule_summary=[], flow_context={}, blueprint={},
        prompt_override={'user_prompt': override},
    )
    assert client.messages[1]['content'] == override
    assert result['prompt_used']['user_prompt'] == override
