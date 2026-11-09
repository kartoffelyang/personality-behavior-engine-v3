from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import generate_behavior_plan


def test_explicit_music_forced():
    req = {
        "request_id": "t_explicit",
        "persona": "ESTJ",
        "scenario_id": "welcome_on_board",
        "user_utterance": "放我上次听的那张专辑。",
        "context": {
            "time_period": "evening",
            "passenger_count": 1,
            "driving_state": "parked",
            "destination_known": True,
            "destination": "home",
        },
        "history": {"allow_auto_music": True},
        "available_capabilities": [
            "navigation",
            "climate",
            "entertainment",
            "ambient_light",
            "notification_mode",
            "ecosystem",
            "seat",
        ],
    }
    result = generate_behavior_plan(req, use_llm=False)
    assert result["behavior_call_type"] == "explicit"
    assert result["selected_capabilities"] == ["entertainment"]
