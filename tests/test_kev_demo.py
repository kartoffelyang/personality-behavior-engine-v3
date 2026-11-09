import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from engine import kev_demo
from engine.persona_demo import cases


def test_presets_share_candidates_without_persona_answers():
    case = cases()[0]
    pool, source = kev_demo.prepare_candidates(None, case['text'], case['world'], case['id'], {})
    assert [c['id'] for c in pool] == ['R1', 'R2', 'R3']
    assert all(c['name'] in case['world'] for c in pool)
    assert source['source'] == 'shared_demo_world'
    assert not any('persona' in c for c in pool)


def test_kev_rejects_out_of_catalog_selection(monkeypatch):
    @contextmanager
    def response(*args, **kwargs):
        from io import StringIO
        yield StringIO(json.dumps({'answers': {'pick': {'choice': 'invented', 'probabilities': {'A': 1}}}}))
    monkeypatch.setattr(kev_demo.urllib.request, 'urlopen', response)
    with pytest.raises(kev_demo.KevDemoError, match='候选'):
        kev_demo.decide({}, {'pick': kev_demo.choice('pick', {'A': 'allowed'})})


def test_local_failure_does_not_fallback_to_remote_planner(monkeypatch):
    monkeypatch.setattr(kev_demo.urllib.request, 'urlopen', lambda *a, **k: (_ for _ in ()).throw(OSError('private body')))
    with pytest.raises(kev_demo.KevDemoError, match='8009') as error:
        kev_demo.decide({}, {'pick': kev_demo.choice('pick', {'A': 'allowed'})})
    assert 'private body' not in str(error.value)


def test_compiler_checks_atomic_ability_and_navigation_dependencies():
    eng = kev_demo.Engine(use_llm=False)
    actions = kev_demo.nav_actions('R1', '禾间简餐', '明确安排')
    plan = kev_demo.compile_plan(eng, '去禾间简餐并导航', '虚拟驻车', actions, '模拟导航已安排')
    assert plan['behavior_actions'][-1]['name'] == '导航运行控制'
    bad = kev_demo.atom('navigation', '不存在的能力', {}, '不应通过')
    with pytest.raises(ValueError):
        kev_demo.compile_plan(eng, '帮我安排', '虚拟驻车', [bad], '测试')


def test_model_dispatch_storage_and_explanation_are_separate(monkeypatch, tmp_path):
    from web import persona_demo as web
    from web.app import app
    calls = []
    monkeypatch.setattr(web, 'OUT', tmp_path)
    monkeypatch.setattr(web, 'JOBS', {})
    monkeypatch.setattr(web, 'POOL', SimpleNamespace(submit=lambda fn, *args: fn(*args)))
    def fake(text, world, jid, progress, case_id=None):
        calls.append(case_id)
        return {'variants': {'ESTJ': {'status': 'completed'}, 'INFP': {'status': 'completed'}},
                'audits': {'ESTJ': {'model': 'kev-0.8b', 'explanation': {'source': 'saved KEV'}}}}
    monkeypatch.setattr(kev_demo, 'run_kev_demo', fake)
    monkeypatch.setattr(web, 'run_demo', lambda *a: pytest.fail('KEV must not call original planner'))
    client = app.test_client()
    response = client.post('/api/persona-demo', json={'case_id': 'rest_stop', 'model': 'kev'})
    assert response.status_code == 202
    jid = response.json['id']
    assert calls == ['rest_stop']
    assert client.get('/api/persona-demo/'+jid).json['model'] == 'kev'
    assert client.get('/api/persona-demo/'+jid+'/explanation').json['ESTJ']['source'] == 'saved KEV'
    assert client.get('/api/persona-demo/examples?model=kev').json == {}
    assert client.post('/api/persona-demo', json={'model': 'other', 'text': 'test'}).status_code == 400
    assert b'KEV-0.8B' in client.get('/demo/kev').data
    assert client.get('/demo').status_code == 200
