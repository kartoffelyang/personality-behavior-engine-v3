from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.registries import CapabilityRegistry
from engine.scorer import score_capability
from engine.registries import PersonaRegistry, ScenarioRegistry, ScoringConfig


def test_new_capability_scoring_without_core_change():
    # Simulate a newly registered capability shape
    aroma = {
        "id": "aroma",
        "display_name": "香氛",
        "supported_needs": {"emotional_comfort": 0.9, "physical_comfort": 0.4},
        "function_affinity": {"Fi": 0.9, "Fe": 0.6, "Se": 0.5, "Te": 0.1},
        "properties": {
            "risk": 0.05,
            "intrusiveness": 0.2,
            "reversibility": 1.0,
            "requires_confirmation": False,
            "max_persona_randomness": 0.1,
        },
        "history_keys": [],
    }
    persona = PersonaRegistry().get("INFP")
    scenario = ScenarioRegistry().get("welcome_on_board")
    weights = ScoringConfig().weights
    parsed = {
        "needs": {"emotional_comfort": 0.8, "physical_comfort": 0.4},
        "emotion": {"label": "fatigue", "intensity": 0.7},
        "risk": {"overall": 0.1},
    }
    detail = score_capability(
        aroma,
        persona,
        scenario,
        parsed,
        {},
        {"time_period": "night"},
        weights,
    )
    assert 0.0 <= detail["final_score"] <= 1.0
    assert detail["capability"] == "aroma"


def test_registry_has_seven_capabilities():
    regs = CapabilityRegistry()
    ids = set(regs.all_ids())
    assert ids == {
        "climate",
        "seat",
        "entertainment",
        "ambient_light",
        "navigation",
        "ecosystem",
        "notification_mode",
    }
