import importlib
import json


def test_fixed_comparison_reads_saved_result_without_generation(monkeypatch, tmp_path):
    web = importlib.import_module('web.app')
    monkeypatch.setattr(web, 'MAIN_SHOWCASE', tmp_path)
    def unexpected(*args, **kwargs):
        raise AssertionError('Viewing a fixed result must not generate again')
    monkeypatch.setattr(web, 'get_llm', unexpected)
    saved = {'ok': True, 'title': '雨天接孩子', 'variants': [{'persona': 'INFP'}]}
    (tmp_path / 'rain.json').write_text(json.dumps(saved), encoding='utf-8')
    (tmp_path / 'current.json').write_text(json.dumps({'cases': [
        {'case_id': 'case_01', 'title': '雨天接孩子', 'file': 'rain.json'}]}), encoding='utf-8')
    client = web.app.test_client()
    assert client.get('/api/fixed-comparisons/case_01').get_json() == saved
    assert client.get('/api/fixed-comparisons/missing').status_code == 404
    home = client.get('/').get_data(as_text=True)
    assert 'id="auto-compare-form"' in home
    assert '选你过今天的方式（效率；享受）' in home
    page = client.get('/comparison').get_data(as_text=True)
    assert '固定测试场景' in page and 'data-case-id="case_01"' in page


def test_no_published_cases_leaves_main_page_available(monkeypatch, tmp_path):
    web = importlib.import_module('web.app')
    monkeypatch.setattr(web, 'MAIN_SHOWCASE', tmp_path)
    client = web.app.test_client()
    assert client.get('/').status_code == 200
    assert client.get('/comparison').status_code == 200
    assert web.load_main_showcase() == []
