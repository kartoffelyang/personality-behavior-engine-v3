from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import default_request_scaffold, generate_behavior_plan


UTTERANCE = "今天早上十点前去康源大厦见客户，我第一次去，车里有点凉。"


@pytest.mark.parametrize("persona", ["INFP", "ESTJ", "NO_MBTI"])
def test_new_requests_do_not_invent_historical_preferences_or_permissions(persona):
    request = default_request_scaffold(persona, UTTERANCE, "no_default_history")
    assert request["history"] == {}

    result = generate_behavior_plan(request, llm_backend="rules", defer_step7=True)
    step7 = next(step for step in result["audit_steps"] if step["step"] == 7)
    assert step7["input"]["flow_context"]["execution_context"]["history"] == {}
    assert result["_review_internal"]["request"]["history"] == {}


@pytest.mark.parametrize("persona", ["INFP", "ESTJ", "NO_MBTI"])
def test_explicit_history_survives_into_the_planner_context(persona):
    request = default_request_scaffold(persona, UTTERANCE, "explicit_history")
    request["history"] = {
        "preferred_temperature": 22,
        "allow_auto_navigation": False,
        "allow_auto_climate": True,
    }
    original = deepcopy(request)

    result = generate_behavior_plan(request, llm_backend="rules", defer_step7=True)
    step7 = next(step for step in result["audit_steps"] if step["step"] == 7)
    assert step7["input"]["flow_context"]["execution_context"]["history"] == original["history"]
    assert result["_review_internal"]["request"]["history"] == original["history"]
    assert request == original
