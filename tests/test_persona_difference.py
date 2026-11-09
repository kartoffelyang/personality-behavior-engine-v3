from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import generate_behavior_plan


def _fatigue_home():
    return {
        "request_id": "t_persona",
        "persona": "INFP",
        "scenario_id": "welcome_on_board",
        "user_utterance": "今天真的累死了。",
        "context": {
            "time_period": "night",
            "weather": "rainy",
            "cabin_temperature": 27,
            "passenger_count": 1,
            "driving_state": "parked",
            "destination_known": True,
            "destination": "home",
            "estimated_trip_minutes": 42,
        },
        "history": {
            "preferred_temperature": 23,
            "preferred_music_style": "soft_acoustic",
            "preferred_ambient_scene": "warm_amber",
            "allow_auto_navigation": True,
            "allow_auto_climate": True,
            "allow_auto_music": False,
            "allow_auto_ambient": True,
        },
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


def test_persona_difference_on_fatigue_home():
    infp_req = _fatigue_home()
    estj_req = deepcopy(infp_req)
    estj_req["persona"] = "ESTJ"
    estj_req["request_id"] = "t_persona_estj"

    infp = generate_behavior_plan(infp_req, use_llm=False)
    estj = generate_behavior_plan(estj_req, use_llm=False)

    # At least one stable difference among tools / call type / first exec mode
    diff = False
    if infp["selected_capabilities"] != estj["selected_capabilities"]:
        diff = True
    if infp["behavior_call_type"] != estj["behavior_call_type"]:
        diff = True
    infp_modes = [
        s.get("execution_mode")
        for s in infp["behavior_sequence"]
        if s.get("type") == "tool"
    ]
    estj_modes = [
        s.get("execution_mode")
        for s in estj["behavior_sequence"]
        if s.get("type") == "tool"
    ]
    if infp_modes != estj_modes:
        diff = True
    assert diff, "INFP and ESTJ should differ in tools, call type, or execution mode"
