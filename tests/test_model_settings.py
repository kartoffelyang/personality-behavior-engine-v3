import json,re,stat
import pytest
from engine import local_cloud
from web.app import app


@pytest.fixture
def client(monkeypatch,tmp_path):
    monkeypatch.setattr(local_cloud,'PATH',tmp_path/'cloud.json')
    return app.test_client()


def token(client):
    html=client.get('/settings/models').get_data(as_text=True)
    return re.search(r'const token=("[^"]+")',html)[1].strip('"')


def test_key_saved_locally_but_never_echoed_in_api_or_page(client):
    key='unit-secret-example'
    res=client.post('/api/model-settings',json={'base_url':'https://www.fucheers.top','model':'claude-opus-5-5','api_key':key},headers={'X-Local-Settings-Token':token(client)})
    assert res.status_code==200 and res.json['base_url']=='https://www.fucheers.top/v1'
    assert res.json['key_configured'] and key not in res.get_data(as_text=True)
    assert key not in client.get('/api/model-settings').get_data(as_text=True)
    assert key not in client.get('/settings/models').get_data(as_text=True)
    assert stat.S_IMODE(local_cloud.PATH.stat().st_mode)==0o600


def test_provider_switch_clears_old_key_and_blank_same_host_preserves_key(client):
    local_cloud.save('https://old.example/v1','old-model','old-secret')
    local_cloud.save('https://old.example/v1','new-model','')
    assert local_cloud.load()['api_key']=='old-secret'
    local_cloud.save('https://new.example/v1','new-model','')
    assert local_cloud.load()['api_key']==''


def test_loopback_settings_reject_cross_origin_and_missing_token(client):
    data={'base_url':'https://www.fucheers.top','model':'claude-opus-5-5'}
    assert client.post('/api/model-settings',json=data).status_code==403
    assert client.post('/api/model-settings',json=data,headers={'X-Local-Settings-Token':token(client),'Origin':'https://other.example'}).status_code==403


def test_runtime_uses_new_base_and_model_without_old_key_or_gemini_override(client):
    from engine.llm_router import get_llm
    from engine.kev_preparation import preparation_model
    local_cloud.save('https://www.fucheers.top/v1','claude-opus-5-5')
    cloud=get_llm('openai')
    assert cloud.model=='claude-opus-5-5' and cloud.api_base=='https://www.fucheers.top/v1' and cloud.api_key==''
    assert preparation_model()=='claude-opus-5-5'
    local_cloud.save('https://www.fucheers.top/v1','claude-opus-5-5','unit-secret')
    assert get_llm('openai').api_key=='unit-secret'


def test_opus_budgets_are_adaptive_and_editable_without_exposing_key(client):
    local_cloud.save('https://www.fucheers.top/v1','claude-opus-5-5','unit-secret')
    assert local_cloud.timeout_for('observation')==90 and local_cloud.timeout_for('wording')==45
    response=client.post('/api/model-settings',json={'base_url':'https://www.fucheers.top/v1','model':'claude-opus-5-5','observation_timeout_sec':75,'wording_timeout_sec':30},headers={'X-Local-Settings-Token':token(client)})
    assert response.status_code==200 and response.json['observation_timeout_sec']==75
    assert local_cloud.timeout_for('observation')==75 and local_cloud.load()['api_key']=='unit-secret'
