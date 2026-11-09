from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import generate_behavior_plan
from engine.sampler import make_seed, select


def test_seed_stable():
    a = make_seed("case_002", "INFP", "welcome_on_board", "2026-07-30")
    b = make_seed("case_002", "INFP", "welcome_on_board", "2026-07-30")
    assert a == b


def test_explicit_not_randomized():
    req = {
        "request_id": "t_rand_explicit",
        "persona": "INFP",
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
    r1 = generate_behavior_plan(req, use_llm=False)
    r2 = generate_behavior_plan(req, use_llm=False)
    assert r1["selected_capabilities"] == r2["selected_capabilities"] == ["entertainment"]
    rnd = r1["layers"]["layer3_candidates_and_rules"].get("random_selection")
    # explicit path skips random pool or marks forced
    assert r1["behavior_call_type"] == "explicit"


def test_sampler_reproducible_pool():
    candidates = [
        {"capability": "ambient_light", "final_score": 0.78},
        {"capability": "entertainment", "final_score": 0.74},
        {"capability": "climate", "final_score": 0.55},
    ]
    cfg = {"enabled": True, "top_k": 2, "score_window": 0.10, "temperature": {"INFP": 0.16}}
    a = select(candidates, "INFP", "req1", "welcome_on_board", cfg, 2, 0.08)
    b = select(candidates, "INFP", "req1", "welcome_on_board", cfg, 2, 0.08)
    assert a["random_selection"]["sampled_tool"] == b["random_selection"]["sampled_tool"]
    assert a["random_selection"]["seed"] == b["random_selection"]["seed"]
