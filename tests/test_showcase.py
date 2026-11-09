import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import showcase
from web import flow_review_app as web


@pytest.fixture
def batch(monkeypatch, tmp_path):
    monkeypatch.setattr(showcase, 'SHOWCASE_DIR', tmp_path / 'showcase')
    cases, sessions = [], []
    for i in range(1, 7):
        cid = f'case_{i:02}'
        req = {'persona': 'INFP', 'user_utterance': f'案例{i}的真实情境', 'request_id': cid}
        cases.append({'case_id': cid, 'title': f'案例{i}', 'request': req})
        steps = [{'step': n, 'title': f'Step {n}', 'status': 'ok', 'input': {}, 'output': {}} for n in range(1, 8)]
        steps[-1]['output'] = {'generation_status': 'ok', 'validated_actions': [{
            'capability': 'navigation', 'name': '地点搜索', 'details': '搜索地点',
            'order': 1, 'wave': 1, 'priority': 'T1', 'burden': 'medium', 'execution_mode': 'auto_generate',
        }]}
        story = {'title': '固定故事', 'paragraphs': [{'segments': [{'text': f'助手呈现固定故事第{n}段。', 'change_id': None}]} for n in range(1, 4)], 'core_changes': []}
        sessions.append({'case_id': cid, 'persona': 'INFP', 'backend': 'openai', 'request': req,
            'confirmed_step': 8, 'result': {'llm_backend': 'openai', 'generation_status': 'ok', 'audit_steps': steps},
            'story_step': {'step': 8, 'title': '故事', 'status': 'ok', 'input': {}, 'output': story}})
    return cases, sessions


def test_only_complete_batch_is_published_and_survives_reload(batch):
    cases, sessions = batch
    manifest = showcase.publish_showcase(sessions, cases)
    assert len(manifest['cases']) == 6
    assert showcase.read_manifest() == manifest
    session = showcase.load_frozen_session(manifest['cases'][0]['session_id'])
    assert session['confirmed_step'] == 8 and session['frozen']
    assert session['story_step']['output'] == sessions[0]['story_step']['output']
    assert showcase.load_frozen_session('../../outside') is None


def test_twelve_saved_results_are_selected_from_gallery(batch):
    cases, sessions = batch
    other = copy.deepcopy(sessions)
    for session in other:
        session['persona'] = session['request']['persona'] = 'ESTJ'
    manifest = showcase.publish_showcase(sessions + other, cases)
    assert len(manifest['cases']) == 12
    client = web.app.test_client()
    for persona in ['INFP', 'ESTJ']:
        index = client.get('/?persona=' + persona).get_data(as_text=True)
        assert index.count('个动作 · 查看场景故事') == 6
        for case in cases:
            assert f'/showcase/{case["case_id"]}?persona={persona}' in index
            response = client.get('/showcase/' + case['case_id'] + '?persona=' + persona)
            sid = response.location.split('/')[2]
            saved = showcase.load_frozen_session(sid)
            assert saved['persona'] == persona and saved['case_id'] == case['case_id']
            page = client.get(response.location).get_data(as_text=True)
            assert '/showcase/' not in page
            assert '返回主页面</a>' in page
    incomplete = sessions + other[:-1]
    with pytest.raises(ValueError):
        showcase.publish_showcase(incomplete, cases)
    assert showcase.read_manifest() == manifest


@pytest.mark.parametrize('failure', ['missing_case', 'step2_fallback', 'step7_failed', 'empty_actions', 'story_failed', 'changed_utterance'])
def test_invalid_batch_cannot_replace_working_showcase(batch, failure):
    cases, sessions = batch
    original = showcase.publish_showcase(sessions, cases)
    invalid = copy.deepcopy(sessions)
    if failure == 'missing_case':
        invalid.pop()
    elif failure == 'step2_fallback':
        invalid[0]['result']['audit_steps'][1]['status'] = 'fallback'
    elif failure == 'step7_failed':
        invalid[0]['result']['generation_status'] = 'failed'
    elif failure == 'empty_actions':
        invalid[0]['result']['audit_steps'][-1]['output']['validated_actions'] = []
    elif failure == 'story_failed':
        invalid[0]['story_step']['status'] = 'failed'
    else:
        invalid[0]['request']['user_utterance'] = '偷偷换了一个情境'
    with pytest.raises(ValueError):
        showcase.publish_showcase(invalid, cases)
    assert showcase.read_manifest() == original


def test_gallery_and_all_steps_read_saved_results_without_model_calls(batch, monkeypatch):
    cases, sessions = batch
    manifest = showcase.publish_showcase(sessions, cases)
    def forbidden(*a, **kw):
        raise AssertionError('固定案例不能触发生成')
    monkeypatch.setattr(web, 'generate_behavior_plan', forbidden)
    monkeypatch.setattr(web, 'run_story', forbidden)
    monkeypatch.setattr(web, 'rerun_from_review_step', forbidden)
    web._session_store.clear()
    client = web.app.test_client()
    landing = client.get('/').get_data(as_text=True)
    assert '固定展示案例' in landing
    for item in manifest['cases']:
        sid = item['session_id']
        opened = client.get(f'/showcase/{item["case_id"]}')
        assert opened.status_code == 302 and opened.location.endswith(f'/session/{sid}/step/8')
        for step in range(1, 9):
            response = client.get(f'/session/{sid}/step/{step}')
            text = response.get_data(as_text=True)
            assert response.status_code == 200
            assert '固定展示' in text
            assert '<h3>固定展示案例</h3>' not in text
            assert '/showcase/' not in text
            assert 'Step 导航' in text
            assert '重新生成情境理解</button>' not in text
            assert '恢复默认 prompt 并重算</button>' not in text
        assert '助手呈现固定故事第1段。' in text


@pytest.mark.parametrize('suffix', ['rerun_all', 'step/2/rerun', 'step/7/rerun', 'step/7/generate', 'step/8/generate', 'step/7/confirm'])
def test_frozen_case_mutation_endpoints_are_blocked(batch, suffix):
    cases, sessions = batch
    manifest = showcase.publish_showcase(sessions, cases)
    sid = manifest['cases'][0]['session_id']
    path = Path(showcase.load_frozen_session(sid)['saved_path'])
    original = path.read_bytes()
    response = web.app.test_client().post(f'/session/{sid}/{suffix}')
    assert response.status_code == 409
    assert path.read_bytes() == original
