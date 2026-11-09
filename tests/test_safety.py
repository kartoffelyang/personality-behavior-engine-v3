from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import generate_behavior_plan


BASE = {
    "request_id": "t_safety",
    "persona": "INFP",
    "scenario_id": "welcome_on_board",
    "user_utterance": "没事，继续开。",
    "context": {
        "time_period": "night",
        "passenger_count": 1,
        "driving_state": "driving",
        "destination_known": True,
        "destination": "home",
        "estimated_trip_minutes": 40,
        "fatigue_risk": 0.9,
    },
    "history": {"allow_auto_navigation": True, "allow_auto_music": True},
    "available_capabilities": [
        "navigation",
        "climate",
        "seat",
        "ambient_light",
        "entertainment",
        "notification_mode",
        "ecosystem",
    ],
}


def test_safety_overrides_persona_and_blocks_entertainment_primary():
    result = generate_behavior_plan(BASE, use_llm=False)
    assert result["behavior_call_type"] == "safety"
    caps = result["selected_capabilities"]
    assert caps, "safety plan should select capabilities"
    assert "entertainment" not in caps
    assert "ambient_light" not in caps
    assert any(c in caps for c in ("navigation", "ecosystem", "notification_mode"))
