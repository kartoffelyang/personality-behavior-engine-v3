import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.pipeline import default_request_scaffold, generate_behavior_plan, rerun_from_review_step
from web import flow_review_app as review


@pytest.mark.parametrize('persona,dominant', [('ESTJ', 'Te'), ('INFP', 'Fi'), ('NO_MBTI', None)])
def test_profile_reaches_planner_and_review(persona, dominant, monkeypatch):
    request = default_request_scaffold(persona, '上车后想去公司。')
    result = generate_behavior_plan(request, llm_backend='rules', defer_step7=True)
    steps = {s['step']: s for s in result['audit_steps']}
    profile = steps[3]['output']
    assert profile['dominant'] == dominant
    assert steps[7]['input']['flow_context']['persona_profile'] == profile
    assert not {'scores', 'top4', 'sequence', 'auxiliary', 'inferior'} & profile.keys()
    if dominant:
        for field in ('persona_description', 'dominant_description', 'judgment_style'):
            assert profile[field]
            assert profile[field] in steps[3]['output_text']
        assert sum(len(profile[k]) for k in ('persona_description', 'dominant_description', 'judgment_style')) <= 160
    sid = 'aabb33445566'
    monkeypatch.setattr(review, '_session_store', {sid: {
        'persona': persona, 'backend': 'rules', 'request': request,
        'confirmed_step': 6, 'result': result,
    }})
    monkeypatch.setattr(review, 'load_frozen_session', lambda _: None)
    response = review.app.test_client().get(f'/session/{sid}/step/3')
    assert response.status_code == 200
    assert profile['persona_description'] in response.get_data(as_text=True)


def test_legacy_resume_refreshes_step3_without_mutating_source():
    result = generate_behavior_plan(default_request_scaffold('ESTJ', '上车后想去公司。'),
                                    llm_backend='rules', defer_step7=True)
    # Simulate a saved record predating the new Step 3.
    for step in result['audit_steps']:
        if step['step'] == 3:
            step['output'] = {'dominant': 'Te', 'sequence': 'Te–Si–Se–Ti'}
    for value in result.values():
        if isinstance(value, dict) and isinstance(value.get('flow_context'), dict):
            value['flow_context'].pop('persona_profile', None)
    before = copy.deepcopy(result)
    resumed = rerun_from_review_step(result, step_no=7, llm_backend='rules')
    steps = {s['step']: s for s in resumed['audit_steps']}
    assert steps[3]['output']['judgment_style']
    assert steps[7]['input']['flow_context']['persona_profile'] == steps[3]['output']
    assert result == before
