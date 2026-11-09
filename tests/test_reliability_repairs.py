import copy
import importlib
import json

import pytest

from engine import model_planner, model_story
from engine.llm_router import LLMRequestError
from engine.story_boundaries import StoryBoundaryError, check_story_boundaries
from engine.registries import CapabilityRegistry
from test_planner_contract_validation import action, plan, payload


@pytest.mark.parametrize('recover', [True, False])
def test_planner_feedback_repairs_actual_invalid_parameter_once(recover):
    reg = CapabilityRegistry()
    catalog = model_planner.build_step7_capability_catalog([{'capability': key, **reg.get(key)} for key in reg.all_ids()])
    valid = plan([action(catalog)])
    invalid = copy.deepcopy(valid)
    invalid['behavior_actions'][0]['parameters_hint'] = {'状态': '打开'}
    calls = []
    class Client:
        model = 'test'
        def chat(self, messages, **kwargs):
            calls.append(copy.deepcopy(messages))
            return json.dumps(valid if recover and len(calls) == 2 else invalid, ensure_ascii=False)
    result = model_planner.run_planner(llm_backend='openai', client=Client(), persona_id='INFP', scenario_id='cabin_comfort',
        parsed={}, whitelist=['climate'], rule_summary=[], flow_context=payload(catalog)['flow_context'], blueprint={})
    assert len(calls) == 2
    assert '未定义' in calls[1][-1]['content']
    assert result['fallback'] is not recover
    assert result['attempts'][0]['status'] == 'failed'
    if not recover:
        assert result['output']['behavior_actions'] == []


def story(text):
    return {'paragraphs': [{'segments': [{'text': text, 'action_ids': [1]}]}]}


STORY_INPUT = {'persona': 'ESTJ', 'behavior_sequence': [{'id': 1, 'name': '通知静音控制', 'execution_mode': 'suggest_only'}]}


def test_suggestion_boundary_and_conservative_warnings():
    with pytest.raises(StoryBoundaryError):
        check_story_boundaries(story('得到确认后，才临时静音相关通知。'), STORY_INPUT)
    assert not check_story_boundaries(story('助手只建议静音通知，是否采用由用户决定。'), STORY_INPUT)
    assert check_story_boundaries(story('助手安排路线。'), {'behavior_sequence': []})


def test_story_repairs_boundary_and_keeps_first_failure(monkeypatch):
    calls = []
    class Client:
        model = 'test'
        def chat(self, messages, **kwargs):
            calls.append(messages)
            return json.dumps(story('助手会静音通知。' if len(calls) == 1 else '助手仅建议静音通知。'), ensure_ascii=False)
    monkeypatch.setattr(model_story, 'get_llm', lambda backend: Client())
    result = model_story.run_story(STORY_INPUT, 'openai')
    assert result['status'] == 'ok' and len(calls) == 2
    assert result['attempts'][0]['error_info']['category'] == 'story_boundary'
    assert 'validation_feedback' in calls[1][-1]['content']


def test_story_retries_known_stream_stall_only_once(monkeypatch):
    calls = []
    class Client:
        model = 'test'
        def chat(self, *args, **kwargs):
            calls.append(1)
            exc = LLMRequestError(category='timeout', message='输出中断', retryable=True)
            exc._llm_diagnostics = {'failure_kind': 'stream_idle_timeout'}
            raise exc
    monkeypatch.setattr(model_story, 'get_llm', lambda backend: Client())
    result = model_story.run_story(STORY_INPUT, 'openai')
    assert len(calls) == 2 and result['status'] == 'failed'
    assert len(result['attempts']) == 2


def test_progressive_story_is_cached_and_cannot_access_other_files(monkeypatch, tmp_path):
    web = importlib.import_module('web.app')
    monkeypatch.setattr(web, 'ROOT', tmp_path)
    folder = tmp_path / 'output/custom'; folder.mkdir(parents=True)
    result = {'persona': 'INFP', 'llm_backend': 'openai', 'audit_steps': [{'step': 7}],
              '_comparison_bundle': 'demo_compare_bundle.json',
              '_review_internal': {'request': {'user_utterance': '回家'}}}
    (folder / 'demo_INFP.json').write_text(json.dumps(result), encoding='utf-8')
    bundle_path = folder / 'demo_compare_bundle.json'
    bundle_path.write_text(json.dumps([{'result_file': 'demo_INFP.json', 'result': {}}]), encoding='utf-8')
    calls = []
    def generate(result, req, persona, backend):
        calls.append(req)
        result['story_step'] = {'status': 'ok', 'output': story('助手仅建议静音通知。')}
    monkeypatch.setattr(web, 'attach_story', generate)
    client = web.app.test_client()
    assert client.post('/api/comparison-story', json={'result_file': '../secret.json'}).status_code == 400
    for _ in range(2):
        assert client.post('/api/comparison-story', json={'result_file': 'demo_INFP.json'}).get_json()['result']['story_step']['status'] == 'ok'
    assert len(calls) == 1 and calls[0]['user_utterance'] == '回家'
    assert json.loads(bundle_path.read_text(encoding='utf-8'))[0]['result']['story_step']['status'] == 'ok'
