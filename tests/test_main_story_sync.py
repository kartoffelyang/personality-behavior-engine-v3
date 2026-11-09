import importlib

import pytest


@pytest.mark.parametrize('plan_status,story_status', [('ok', 'ok'), ('ok', 'failed'), ('failed', 'blocked')])
def test_main_story_preserves_plan_and_stops_on_failed_plan(monkeypatch, tmp_path, plan_status, story_status):
    web = importlib.import_module('web.app')
    monkeypatch.setattr(web, 'ROOT', tmp_path)
    monkeypatch.setattr(web, 'Engine', lambda **kwargs: object())
    policy = {'version': 2, 'compiled_prompt': '保留用户选择', 'constraints': []}
    result = {'persona': 'INFP', 'generation_status': plan_status, 'audit_steps': [
        {'step': 6, 'output': {'interaction_policy': policy}},
        {'step': 7, 'status': plan_status, 'output': {'validated_actions': [
            {'name': '地点搜索', 'capability': 'navigation'}]}}
    ]}
    monkeypatch.setattr(web, 'run_one_variant', lambda **kwargs: (
        {'request_id': 'sync-test', 'user_utterance': '回家'}, result))
    calls = []
    def story(payload, backend):
        calls.append(payload)
        return {'step': 8, 'status': story_status, 'output': {'paragraphs': ['准备回家。']},
                'error_info': {'message': '模拟超时'} if story_status == 'failed' else None}
    monkeypatch.setattr(web, 'run_story', story)
    response = web.app.test_client().post('/api/run', json={
        'persona': 'INFP', 'compare_variants': False, 'include_story': True, 'backend': 'openai'})
    assert response.status_code == 200
    output = response.get_json()['variants'][0]['result']
    assert output['generation_status'] == plan_status
    assert output['story_step']['status'] == story_status
    assert len(calls) == (0 if plan_status == 'failed' else 1)
    if calls:
        assert calls[0]['interaction_requirements'] == policy['compiled_prompt']
    assert output['audit_steps'][0]['output']['interaction_policy'] == policy
    assert len(list((tmp_path / 'output/custom').glob('*.json'))) == 2
