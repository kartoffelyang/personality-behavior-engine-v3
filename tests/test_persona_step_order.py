from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import model_story, showcase
from engine.pipeline import default_request_scaffold, generate_behavior_plan, rerun_from_review_step
from engine.trace_logger import PERSONA_DESCRIPTION_TITLE, SCENARIO_STRATEGY_TITLE, normalize_persona_step_order
from web import flow_review_app as review


def make_session(persona="INFP"):
    request = default_request_scaffold(persona, "今天十点前去康源大厦见客户。", "step_order_test")
    result = generate_behavior_plan(request, llm_backend="rules", defer_step7=True)
    return {"persona": persona, "backend": "rules", "request": request,
            "confirmed_step": 6, "result": result}


def legacy_session():
    session = make_session()
    for step in session["result"]["audit_steps"]:
        if step["step"] in {4, 5}:
            step["step"] = 9 - step["step"]
            step["title"] = "MAP：人格场景行动策略" if step["step"] == 4 else "MAP：目标人格描述及行动策略"
            step["input_text"] = step["output_text"] = "旧顺序说明"
    session["result"]["audit_steps"].sort(key=lambda s: s["step"])
    return session


@pytest.mark.parametrize("persona", ["INFP", "ESTJ", "NO_MBTI"])
def test_new_pipeline_supplies_persona_before_scene_and_preserves_story_material(persona):
    session = make_session(persona)
    steps = {s["step"]: s for s in session["result"]["audit_steps"]}
    assert list(steps) == list(range(1, 8))
    assert steps[4]["title"] == PERSONA_DESCRIPTION_TITLE
    assert steps[5]["title"] == SCENARIO_STRATEGY_TITLE
    assert "action_logic" in steps[4]["output"]
    assert "scenario_strategy" in steps[5]["output"]
    assert "基本行动策略" in steps[4]["output_text"]
    assert "场景内策略" in steps[5]["output_text"]
    context = steps[7]["input"]["flow_context"]
    assert context["persona_description"] == steps[4]["output"]
    assert context["persona_scene_strategy"] == steps[5]["output"]
    story = model_story.build_story_input(session)["persona_strategy"]
    assert story["action_logic"] == steps[4]["output"]["action_logic"]
    assert story["scenario_strategy"] == steps[5]["output"]["scenario_strategy"]
    assert story["action_strategy"] == steps[5]["output"]["action_strategy"]


def test_old_record_reorders_without_mutation_or_losing_story_context():
    session = legacy_session()
    original = copy.deepcopy(session)
    old = {s["step"]: s for s in session["result"]["audit_steps"]}
    normalized = review.normalize_review_persona_steps(session)
    new = {s["step"]: s for s in normalized["result"]["audit_steps"]}
    assert new[4]["input"] == old[5]["input"] and new[4]["output"] == old[5]["output"]
    assert new[5]["input"] == old[4]["input"] and new[5]["output"] == old[4]["output"]
    assert "基本行动策略" in new[4]["output_text"]
    assert "场景内策略" in new[5]["output_text"]
    assert model_story.build_story_input(session) == model_story.build_story_input(normalized)
    assert model_story.build_story_input(normalized)["persona_strategy"]["action_logic"] == old[5]["output"]["action_logic"]
    assert session == original
    assert review.normalize_review_persona_steps(normalized) is normalized
    assert normalize_persona_step_order(new.values()) == list(new.values())


def test_partly_confirmed_legacy_record_requires_persona_review_first(monkeypatch, tmp_path):
    session = legacy_session()
    session["confirmed_step"] = 4
    sid = "aabbccddeeff"
    path = tmp_path / (sid + ".json")
    path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    original = path.read_bytes()
    monkeypatch.setattr(review, "OUT_DIR", tmp_path)
    monkeypatch.setattr(review, "_session_store", {})
    monkeypatch.setattr(review, "load_frozen_session", lambda _: None)
    client = review.app.test_client()
    response = client.get(f"/session/{sid}/step/5")
    assert response.status_code == 302 and response.location.endswith("/step/4")
    page = client.get(response.location).get_data(as_text=True)
    assert "Step 4 · " + PERSONA_DESCRIPTION_TITLE in page
    assert "action_logic" in page
    assert review._session_store[sid]["confirmed_step"] == 3
    assert path.read_bytes() == original
    confirmed = client.post(f"/session/{sid}/step/4/confirm")
    assert confirmed.location.endswith("/step/5")
    assert "scenario_strategy" in client.get(confirmed.location).get_data(as_text=True)
    assert client.post(f"/session/{sid}/step/5/confirm").location.endswith("/step/6")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["confirmed_step"] == 5
    assert saved["result"]["audit_steps"][3]["title"] == PERSONA_DESCRIPTION_TITLE


def test_step7_resume_from_old_record_preserves_new_order_and_original_material():
    original = legacy_session()
    before = copy.deepcopy(original)
    resumed = rerun_from_review_step(original["result"], step_no=7, llm_backend="rules")
    assert resumed["audit_steps"][3]["title"] == PERSONA_DESCRIPTION_TITLE
    assert resumed["audit_steps"][4]["title"] == SCENARIO_STRATEGY_TITLE
    assert resumed["audit_steps"][3]["output"] == before["result"]["audit_steps"][4]["output"]
    assert resumed["audit_steps"][4]["output"] == before["result"]["audit_steps"][3]["output"]
    assert original == before


def test_frozen_legacy_case_changes_pages_without_rewriting_saved_results(monkeypatch, tmp_path):
    session = legacy_session()
    session["confirmed_step"] = 8
    sid, batch = "112233aabbcc", "001122aabbcc"
    directory = tmp_path / batch
    directory.mkdir()
    path = directory / (sid + ".json")
    path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    original = path.read_bytes()
    (tmp_path / "current.json").write_text(json.dumps({"batch_id": batch, "cases": [
        {"session_id": sid, "case_id": "case_01", "title": "固定案例", "persona": "INFP"}
    ]}), encoding="utf-8")
    monkeypatch.setattr(showcase, "SHOWCASE_DIR", tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Viewing frozen cases must not generate anything")
    monkeypatch.setattr(review, "generate_behavior_plan", forbidden)
    monkeypatch.setattr(review, "run_story", forbidden)
    client = review.app.test_client()
    for number, title, field in [(4, PERSONA_DESCRIPTION_TITLE, "action_logic"),
                                  (5, SCENARIO_STRATEGY_TITLE, "scenario_strategy")]:
        response = client.get(f"/session/{sid}/step/{number}")
        page = response.get_data(as_text=True)
        assert response.status_code == 200
        assert f"Step {number} · {title}" in page and field in page
    assert path.read_bytes() == original
