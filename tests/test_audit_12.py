from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import default_request_scaffold, generate_behavior_plan


def test_seven_audit_steps_rules_backend():
    req = default_request_scaffold(
        "INFP",
        "晚上，我刚上车，准备去学校接女儿，然后一起回家。今天有点累。",
        "t_audit_12",
    )
    result = generate_behavior_plan(req, use_llm=False, llm_backend="rules")
    steps = result.get("audit_steps") or []
    assert len(steps) == 7
    assert [s["step"] for s in steps] == list(range(1, 8))
    assert result.get("O_Behavior")
    assert len(result["O_Behavior"]) == 7
    assert isinstance(result.get("O_Action_Plan"), list)
    assert result.get("scene_plan")
    assert "gantt" not in result
    assert "scene_story" not in result
    assert result.get("spoken_reply") == ""
    assert result["llm_backend"] == "rules"
    assert result.get("scenario_id")
    step7 = next(s for s in steps if s["step"] == 7)
    acts = (step7.get("output") or {}).get("validated_actions") or []
    for a in acts:
        assert a.get("priority") in {"T1", "T2", "T3"}
        assert a.get("burden") in {"low", "medium", "high"}
        assert int(a.get("wave") or 0) >= 1


def test_o_behavior_call_format():
    req = default_request_scaffold("ESTJ", "开会要迟到了，赶紧导航。", "t_ob_fmt")
    result = generate_behavior_plan(req, llm_backend="rules")
    vals = list(result["O_Behavior"].values())
    assert any(v.startswith("【调用】") for v in vals)
    assert any(v.startswith("【不调用】") for v in vals)
    assert any("调用程度" in v and "原因" in v for v in vals)
    step = result["audit_steps"][1]
    assert step.get("input_text")
    assert step.get("output_text")
