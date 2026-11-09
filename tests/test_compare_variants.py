from __future__ import annotations

import sys
import copy
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import default_request_scaffold, generate_behavior_plan
from web.app import app
from planner_fixtures import navigation_plan


@pytest.mark.parametrize('preset_only', [False, True])
def test_compare_shares_one_model_observation(monkeypatch, tmp_path, preset_only):
    import engine.pipeline as pipeline
    from engine.model_intent import run_intent

    web_app = importlib.import_module('web.app')
    utterance = '今天早上十点前去康源大厦见重要客户，我第一次去，前挡有些发白。'
    observation = run_intent(default_request_scaffold('NO_MBTI', utterance), 'rules', None)
    calls = []

    class FakeClient:
        model = 'shared-intent-test'

        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]['content'])
            calls.append(payload)
            if 'Step 2' in messages[0]['content']:
                return json.dumps(observation['output'], ensure_ascii=False)
            return json.dumps(navigation_plan(), ensure_ascii=False)

    monkeypatch.setattr(web_app, 'ROOT', tmp_path)
    monkeypatch.setattr(web_app, 'load_case_defs', lambda: [
        {'case_id': 'shared_case', 'request': {'user_utterance': utterance}}
    ])
    monkeypatch.setattr(web_app, 'get_llm', lambda backend: FakeClient())
    monkeypatch.setattr(pipeline, 'get_llm', lambda backend: FakeClient())
    response = app.test_client().post('/api/run', json={
        'llm_backend': 'openai', 'compare_variants': True,
        'user_utterance': '' if preset_only else utterance,
        'preset_case_id': 'shared_case' if preset_only else None,
    })
    assert response.status_code == 200
    variants = response.get_json()['variants']
    assert len(calls) == 4
    assert calls[0]['persona'] == 'NO_MBTI'
    assert calls[0]['user_utterance'] == utterance
    assert {c['persona'] for c in calls[1:]} == {'INFP', 'ESTJ', 'NO_MBTI'}
    steps = [v['result']['audit_steps'] for v in variants]
    assert steps[0][1]['output'] == steps[1][1]['output'] == steps[2][1]['output']
    contexts = [c['flow_context']['scenario_understanding'] for c in calls[1:]]
    assert contexts[0] == contexts[1] == contexts[2]
    strategies = {c['persona']: c['flow_context']['persona_scene_strategy'] for c in calls[1:]}
    assert strategies['INFP']['dominant_function'] != strategies['ESTJ']['dominant_function']
    assert strategies['NO_MBTI']['dominant_function'] is None
    assert all(v['result']['generation_status'] == 'ok' for v in variants)
    assert all(v['result']['O_Action_Plan'] for v in variants)
    assert len(list((tmp_path / 'output' / 'custom').glob('*.json'))) == 4


def test_shared_observation_isolated_and_failure_audit_preserved(monkeypatch):
    import engine.pipeline as pipeline
    from engine.model_intent import run_intent

    utterance = '今天早上十点前去康源大厦。'
    shared = run_intent(default_request_scaffold('NO_MBTI', utterance), 'rules', None)
    shared['error_info'] = {'category': 'timeout', 'message': 'test failure'}
    original = copy.deepcopy(shared)

    def unexpected_intent(*args, **kwargs):
        raise AssertionError('A branch must not repeat Step 2')

    monkeypatch.setattr(pipeline, 'run_intent', unexpected_intent)
    outputs = [generate_behavior_plan(
        default_request_scaffold(persona, utterance), llm_backend='rules',
        shared_intent=shared, defer_step7=True,
    ) for persona in ('INFP', 'ESTJ', 'NO_MBTI')]
    assert shared == original
    assert all(r['audit_steps'][1]['error_info'] == shared['error_info'] for r in outputs)
    outputs[0]['audit_steps'][1]['output']['task']['constraints'].append('branch-local')
    assert outputs[1]['audit_steps'][1]['output'] == original['output']
    assert shared == original


def test_no_mbti_variant_runs_rules_backend():
    req = default_request_scaffold("NO_MBTI", "今天想找个地方轻松兜一会儿。", "t_no_mbti")
    result = generate_behavior_plan(req, use_llm=False, llm_backend="rules")
    assert result["persona"] == "NO_MBTI"
    assert len(result.get("audit_steps") or []) == 7
    assert result.get("scene_plan")
    assert result.get("O_Action_Plan") is not None
    assert "gantt" not in result
    assert "scene_story" not in result
    step7 = next(s for s in result["audit_steps"] if s["step"] == 7)
    acts = step7["output"].get("validated_actions") or []
    if acts:
        assert acts[0].get("priority") in {"T1", "T2", "T3"}
        assert acts[0].get("burden") in {"low", "medium", "high"}
        assert acts[0].get("wave") >= 1


def test_compare_endpoint_returns_three_variants():
    client = app.test_client()
    resp = client.post(
        "/api/run",
        json={
            "user_utterance": "晚上我刚上车，准备回家，今天有点累。",
            "llm_backend": "rules",
            "compare_variants": True,
        },
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    variants = payload.get("variants") or []
    assert [v["persona"] for v in variants] == ["INFP", "ESTJ", "NO_MBTI"]
