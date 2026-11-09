from itertools import product
from engine.persona_demo import cases,kev_cases
from engine.kev_demo import Engine,PRESETS,compile_plan
from engine.kev_scenes import scene_definition
from engine.kev_bundles import feature_set,run_strong_scene


def test_ten_unique_scenes_preserve_original_three_and_compile_all_options():
    eng=Engine(use_llm=False);catalog=kev_cases()
    assert len(catalog)==len({c['id'] for c in catalog})==10
    assert len(cases())==3
    for c in catalog:
        resources=[dict(id=i,name=n,description=d) for i,n,d in PRESETS.get(c['id'],[])]
        bundles,policies,groups,minimum=scene_definition(c['id'],resources)
        assert c['observe'] and c['why_suitable'] and c['category']
        for b in bundles:compile_plan(eng,c['text'],c['world'],b['actions'],b['description'])
        for a,b in product([x for x in bundles if x['family']=='order'],[x for x in bundles if x['family']=='feeling']):
            assert len(feature_set(a['actions'])^feature_set(b['actions']))>=minimum


def test_controlled_song_scene_keeps_same_content_and_volume_and_no_climate():
    bundles,*_=scene_definition('same_song',[])
    for b in bundles:
        selected=next(a for a in b['actions'] if a['n']=='内容选择')
        assert selected['p']['内容']=='M1 雨后钢琴'
        assert next(a for a in b['actions'] if a['n']=='音量调节')['p']['音量']==20
        assert all(a['c']!='climate' for a in b['actions'])
    assert '身体放松' in feature_set(bundles[1]['actions'])-feature_set(bundles[0]['actions'])


def test_all_added_scenes_have_57_real_question_slots_and_matching_final_actions():
    eng=Engine(use_llm=False)
    def decide(state,questions):
        answers={}
        for q,s in questions.items():
            ids=list(s['criteria']);answers[q]={'choice':ids[0],'probabilities':{i:1/len(ids) for i in ids}}
        return {'answers':answers},.01
    for c in kev_cases()[3:]:
        r=run_strong_scene(eng,c['text'],c['world'],[],c['id'],decide,compile_plan,lambda **k:None)
        assert r['atomic_scope']['scored_count']==57
        for v in r['variants'].values():
            assert len(v['atomic_scores'])==57
            assert {s['id'] for s in v['atomic_scores'] if s['adopted']}=={a['domain']+'/'+a['name'] for a in v['actions']}


def test_catalog_visibility_and_new_scene_api_dispatch(monkeypatch,tmp_path):
    from web.app import app
    from web import persona_demo as web
    from engine import kev_demo
    from types import SimpleNamespace
    monkeypatch.setattr(web,'OUT',tmp_path);monkeypatch.setattr(web,'JOBS',{})
    monkeypatch.setattr(web,'POOL',SimpleNamespace(submit=lambda fn,*a:fn(*a)))
    captured=[]
    def fake(text,world,*a,**k):
        captured.append((text,world,k['case_id']))
        return {'variants':{},'audits':{}}
    monkeypatch.setattr(kev_demo,'run_kev_demo',fake)
    client=app.test_client()
    assert client.get('/demo/kev').text.count('data-case=')==10
    assert client.get('/demo').text.count('data-case=')==3
    assert client.post('/api/persona-demo',json={'model':'kev','case_id':'same_song'}).status_code==202
    assert captured[0][2]=='same_song' and '雨后钢琴' in captured[0][0]
    from engine.kev_input import INPUT_VERSION
    (tmp_path/('kev_examples_'+INPUT_VERSION+'.json')).write_text('{"same_song":"saved-local-run"}')
    assert client.get('/api/persona-demo/examples?model=kev').json=={'same_song':'saved-local-run'}
