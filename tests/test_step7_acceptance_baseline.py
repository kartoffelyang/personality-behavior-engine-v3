from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.model_planner import build_planner_input, build_step7_scenario_understanding


def _baseline():
    return json.loads((ROOT / "cases/kangyuan_step7_acceptance.json").read_text(encoding="utf-8"))


def test_kangyuan_baseline_fixes_shared_step2_and_acceptance_rules():
    baseline = _baseline()
    assert baseline["baseline_id"] == "kangyuan_time_pressure_v1"
    assert baseline["case"]["personas"] == ["NO_MBTI", "INFP", "ESTJ"]
    step2 = baseline["fixed_step2"]
    assert step2["scenario_id"] == "time_pressure"
    assert step2["context"]["cabin_temperature_c"] is None
    assert step2["context"]["destination_reference"] == "康源大厦"
    assert step2["context"]["arrival_deadline"] == "今天10:00前"
    assert step2["risk"]["visibility"] > 0
    assert step2["risk"]["time_pressure"] > 0
    assert {item["id"] for item in baseline["acceptance"]["common_outcomes"]} == {
        "visibility_safety", "destination_resolution", "deadline_assessment", "conditional_breakfast"
    }
    assert baseline["acceptance"]["run_policy"]["shared_step2_across_personas"] is True
    assert baseline["acceptance"]["run_policy"]["fallback_counts_as_pass"] is False


def test_step7_input_does_not_repeat_step2_emotion_needs_or_risk_at_top_level():
    baseline = _baseline()
    step2 = baseline["fixed_step2"]
    flow_context = {"scenario_understanding": step2, "capability_catalog": []}
    payloads = []
    for persona in baseline["case"]["personas"]:
        payload = build_planner_input(
            persona_id=persona,
            scenario_id=step2["scenario_id"],
            parsed={"emotion": step2["emotion"], "needs": step2["needs"], "risk": step2["risk"]},
            whitelist=[],
            rule_summary=[],
            flow_context=flow_context,
            blueprint={},
        )
        assert "emotion" not in payload
        assert "needs" not in payload
        assert "risk" not in payload
        assert payload["flow_context"]["scenario_understanding"] == step2
        payloads.append(payload)

    assert all(p["flow_context"]["scenario_understanding"] == payloads[0]["flow_context"]["scenario_understanding"] for p in payloads)


def test_step2_projection_keeps_planning_fields_and_removes_audit_repetition():
    full_step2 = {
        "scenario_id": "time_pressure",
        "task": {"core_goal": "十点前到达康源大厦", "explicit_intents": ["十点前到达"],
                 "optional_goals": ["顺路买早餐"], "constraints": ["早餐不能耽误"],
                 "must_do": ["确认前挡视野"]},
        "facts": [{"category": "task", "source": "utterance", "fact": "十点前到达", "confidence": 1.0}],
        "unknowns": ["具体入口"],
        "context": {"destination_reference": "康源大厦"},
        "needs": {"time_efficiency": 0.9},
        "emotion": {"label": "anxious", "intensity": 0.5},
        "risk": {"visibility": 0.5, "time_pressure": 0.8, "overall": 0.5},
    }
    projected = build_step7_scenario_understanding(full_step2)
    assert set(projected) == {"task", "facts", "unknowns", "context", "needs", "emotion", "risk", "activated_interaction_dimensions"}
    assert projected["activated_interaction_dimensions"] == []
    assert projected["context"]["destination_reference"] == "康源大厦"
    assert "scenario_id" not in projected
    assert projected["facts"] == full_step2["facts"]
