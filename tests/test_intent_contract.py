from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.model_intent import INTENT_SYSTEM, _apply_overrides, infer_scenario_id, run_intent
from engine.pipeline import default_request_scaffold


def output_fixture():
    raw = json.loads(INTENT_SYSTEM[INTENT_SYSTEM.index('{'):INTENT_SYSTEM.rindex('}') + 1])
    raw['scenario_id'] = 'time_pressure'
    raw['facts'] = [{'category': 'task', 'source': 'utterance', 'fact': '十点前得赶到', 'confidence': 0.95}]
    raw['task']['core_goal'] = '今天10:00前到达康源大厦'
    raw['context'].update(time_period='morning', thermal_sensation='车内偏凉',
                          destination_reference='康源大厦', arrival_deadline='今天10:00前')
    raw['risk'].update(items=['前挡可能影响视野'], visibility=0.4, time_pressure=0.6)
    raw['activated_interaction_dimensions'] = []
    return raw


def test_deadline_in_rules_fallback_is_not_mistaken_for_a_greeting():
    case = json.loads((ROOT / 'cases/kangyuan_step2.json').read_text(encoding='utf-8'))
    assert infer_scenario_id(case['user_utterance']) == 'time_pressure'
    assert infer_scenario_id('太热了，但十点前得赶到') == 'time_pressure'


def test_default_step1_context_is_empty_when_user_provides_no_context():
    req = default_request_scaffold('ESTJ', '')
    assert req['context'] == {}


def test_rules_fallback_uses_the_same_compact_step2_contract():
    result = run_intent(default_request_scaffold('NO_MBTI', '今天早上十点前去康源大厦'), 'rules', None)
    assert set(result['output']) == {
        'scenario_id', 'task', 'facts', 'unknowns', 'context', 'needs', 'emotion', 'risk',
        'activated_interaction_dimensions',
    }
    assert result['output']['scenario_id'] == 'time_pressure'
    assert result['output']['context']['destination_reference'] == '康源大厦'
    assert result['output']['activated_interaction_dimensions'] == []


def test_step1_does_not_display_an_empty_default_context():
    from engine.pipeline import generate_behavior_plan

    result = generate_behavior_plan(default_request_scaffold('ESTJ', ''), llm_backend='rules')
    step1 = result['audit_steps'][0]['output']
    assert 'default_context' not in step1
    assert 'provided_context' not in step1


class FakeClient:
    model = 'fixture-only'

    def __init__(self, output):
        self.output = output
        self.messages = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        return json.dumps(self.output, ensure_ascii=False)


def test_public_output_and_downstream_context_are_kept_distinct():
    case = json.loads((ROOT / 'cases/kangyuan_step2.json').read_text(encoding='utf-8'))
    req = default_request_scaffold('ESTJ', case['user_utterance'])
    before = deepcopy(req)
    raw = output_fixture()
    result = run_intent(req, 'openai', FakeClient(raw))
    assert not result['fallback']
    assert result['output'] == raw
    assert req == before
    ctx = result['context']
    assert ctx['time_period'] == 'morning'
    assert 'cabin_temperature' not in ctx
    assert ctx['thermal_sensation'] == '车内偏凉'
    assert ctx['destination'] == '康源大厦'
    assert ctx['destination_known'] is False
    assert ctx['calendar_pressure'] is True
    assert ctx['arrival_deadline'] == '今天10:00前'
    assert result['output']['context']['weather'] is None
    assert result['parsed_context']['risk'] == raw['risk']
    assert result['parsed_context']['needs']['restore_history'] == 0


def test_new_unresolved_destination_clears_stale_trip_time():
    ctx = _apply_overrides({'destination': '公司', 'destination_known': True,
                            'estimated_trip_minutes': 20, 'cabin_temperature': 24},
                           {'destination_reference': '康源大厦', 'cabin_temperature_c': 0})
    assert ctx['destination_known'] is False
    assert ctx['estimated_trip_minutes'] is None
    assert ctx['cabin_temperature'] == 0


@pytest.mark.parametrize('field,value', [('passenger_count', True), ('passenger_count', '1人'),
    ('passenger_count', '1.5'), ('passenger_count', '-1'), ('passenger_count', 'true'),
    ('cabin_temperature_c', '24℃'), ('cabin_temperature_c', 'NaN'), ('cabin_temperature_c', 'Infinity')])
def test_bad_context_types_are_visible_fallbacks(field, value):
    raw = output_fixture()
    raw['context'][field] = value
    result = run_intent(default_request_scaffold('ESTJ', '去康源大厦'), 'openai', FakeClient(raw))
    assert result['fallback']
    assert result['error_info']
    assert result['error_info']['category'] == 'schema_validation_error'
    assert '网关' not in result['error_info']['hint']
    assert json.loads(result['raw_llm_text']) == raw


@pytest.mark.parametrize('field,value,expected', [('passenger_count', '1', 1),
    ('passenger_count', '0', 0), ('cabin_temperature_c', '24', 24),
    ('cabin_temperature_c', '-2.5', -2.5)])
def test_numeric_strings_are_converted_with_audit_trail(field, value, expected):
    raw = output_fixture()
    raw['context'][field] = value
    result = run_intent(default_request_scaffold('INFP', '现在车里就我一个人'), 'openai', FakeClient(raw))
    assert not result['fallback']
    assert result['output']['context'][field] == expected
    assert json.loads(result['raw_llm_text']) == raw
    assert result['normalization_corrections'] == [{'field': f'context.{field}',
        'before': value, 'after': expected, 'reason': 'numeric_string_to_number'}]


def test_out_of_range_score_is_rejected():
    raw = output_fixture()
    raw['risk']['visibility'] = 1.2
    result = run_intent(default_request_scaffold('ESTJ', '前挡发白'), 'openai', FakeClient(raw))
    assert result['fallback']


def test_persona_has_no_code_level_modulation_in_step2():
    results = []
    for persona in ('ESTJ', 'INFP', 'NO_MBTI'):
        client = FakeClient(output_fixture())
        result = run_intent(default_request_scaffold(persona, '十点前到康源大厦'), 'openai', client)
        payload = json.loads(client.messages[1]['content'])
        assert payload['persona'] == persona
        assert 'provided_context' in payload
        assert 'default_context' not in payload
        assert 'history' not in payload
        assert 'available_capabilities' not in payload
        candidates = payload['interaction_dimension_candidates']
        assert len(candidates) == 13
        assert len({item['dimension'] for item in candidates}) == 13
        assert not ({item['dimension'] for item in candidates} & {
            '主动性', '确认严格度', '解释透明度', '控制权保留度', '策略选择',
        })
        assert all(set(item) == {'dimension', 'definition', 'trigger', 'boundary'} for item in candidates)
        results.append(result)
    assert results[0]['output'] == results[1]['output'] == results[2]['output']
    assert results[0]['parsed_context'] == results[1]['parsed_context'] == results[2]['parsed_context']


def test_provided_history_reaches_step2_without_inventing_or_mutating_context():
    request = default_request_scaffold('INFP', '车里有点冷')
    request['context'] = {'driving_state': 'parked'}
    request['history'] = {'preferred_temperature_c': 24}
    original = deepcopy(request)
    client = FakeClient(output_fixture())
    run_intent(request, 'openai', client)
    payload = json.loads(client.messages[1]['content'])
    assert payload['provided_context'] == {
        'driving_state': 'parked', 'history': {'preferred_temperature_c': 24},
    }
    assert request == original

    request['history'] = {}
    run_intent(request, 'openai', client)
    payload = json.loads(client.messages[1]['content'])
    assert payload['provided_context'] == {'driving_state': 'parked'}


def test_step2_schema_reaches_step7_without_losing_risks_or_unknowns(monkeypatch):
    from engine import pipeline
    from engine.model_planner import run_planner

    raw = output_fixture()
    raw['unknowns'] = ['康源大厦的具体地址和入口', '当前时间和预计路程']
    raw['context']['passenger_count'] = '1'
    raw['activated_interaction_dimensions'] = [activation('时机敏感')]
    expected = deepcopy(raw)
    expected['context']['passenger_count'] = 1
    client = FakeClient(raw)
    captured = {}

    def offline_planner(**kwargs):
        captured.update(deepcopy(kwargs['flow_context']))
        kwargs['client'] = None
        return run_planner(**kwargs)

    monkeypatch.setattr(pipeline, 'get_llm', lambda *args, **kwargs: client)
    monkeypatch.setattr(pipeline, 'run_planner', offline_planner)
    result = pipeline.generate_behavior_plan(
        default_request_scaffold('ESTJ', '今天早上十点前去康源大厦，前挡发白'), llm_backend='openai')
    assert result['audit_steps'][1]['status'] == 'ok'
    assert result['audit_steps'][1]['output'] == expected
    assert result['audit_steps'][1]['normalization_corrections'][0]['before'] == '1'
    assert json.loads(result['audit_steps'][1]['raw_llm_text']) == raw
    scenario_understanding = captured['scenario_understanding']
    assert scenario_understanding['risk'] == raw['risk']
    assert scenario_understanding['unknowns'] == raw['unknowns']
    assert scenario_understanding['task'] == raw['task']
    assert scenario_understanding['context'] == expected['context']
    assert 'summary' not in scenario_understanding
    assert scenario_understanding['facts'] == raw['facts']
    assert scenario_understanding['activated_interaction_dimensions'] == raw['activated_interaction_dimensions']
    assert len(result['audit_steps']) == 7


def activation(dimension, **overrides):
    return {
        'dimension': dimension,
        'evidence': '用户说明正在并线，非紧急内容等会儿说',
        'context_requirement': '非紧急交互延后至用户可接收的时机。',
        **overrides,
    }


@pytest.mark.parametrize('dimensions', [[], [
    activation('时机敏感'), activation('语音信息密度'),
    activation('排序结构化'), activation('跟进强度'),
]])
def test_live_intent_accepts_zero_or_four_optional_dimensions(dimensions):
    raw = output_fixture()
    raw['activated_interaction_dimensions'] = dimensions
    result = run_intent(default_request_scaffold('ESTJ', '正在并线，等会儿再说'), 'openai', FakeClient(raw))
    assert not result['fallback']
    assert result['output']['activated_interaction_dimensions'] == dimensions
    assert json.loads(result['raw_llm_text']) == raw


@pytest.mark.parametrize('dimensions', [
    [activation(name) for name in ['时机敏感', '语音信息密度', '排序结构化', '跟进强度', '关系站位']],
    [activation('时机敏感'), activation('时机敏感')],
    *[[activation(name)] for name in ['主动性', '确认严格度', '解释透明度', '控制权保留度']],
    [activation('不存在的维度')],
    [activation('时机敏感', evidence='')],
    [activation('时机敏感', evidence='  ')],
    [activation('时机敏感', context_requirement='')],
    [activation('时机敏感', context_requirement='\n\t')],
    [activation('时机敏感', score=5)],
    [{'dimension': '时机敏感', 'evidence': '正在并线'}],
    [activation('时机敏感', evidence=['正在并线'])],
    {'dimension': '时机敏感'},
    None,
])
def test_invalid_live_activation_is_a_visible_schema_failure(dimensions):
    raw = output_fixture()
    raw['activated_interaction_dimensions'] = dimensions
    result = run_intent(default_request_scaffold('ESTJ', '正在并线'), 'openai', FakeClient(raw))
    assert result['fallback']
    assert result['error_info']['category'] == 'schema_validation_error'
    assert json.loads(result['raw_llm_text']) == raw
    assert result['output']['activated_interaction_dimensions'] == []


def test_missing_activation_field_in_new_live_response_is_not_silently_defaulted():
    raw = output_fixture()
    del raw['activated_interaction_dimensions']
    result = run_intent(default_request_scaffold('INFP', '带我回家'), 'openai', FakeClient(raw))
    assert result['fallback']
    assert result['error_info']['category'] == 'schema_validation_error'
    assert json.loads(result['raw_llm_text']) == raw


def test_step2_enables_streaming_and_still_validates_complete_output():
    client = FakeClient(output_fixture())
    client.stream = False
    result = run_intent(default_request_scaffold('INFP', '十点前到康源大厦'), 'openai', client)
    assert client.stream is True
    assert result['fallback'] is False
    assert result['output']['scenario_id'] == 'time_pressure'
