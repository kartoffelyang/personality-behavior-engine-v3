from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class PersonaRegistry:
    def __init__(self, data: Dict[str, Any] | None = None):
        self._data = data or load_yaml(CONFIG_DIR / "personas.yaml")

    def get(self, persona_id: str) -> Dict[str, Any]:
        if persona_id not in self._data:
            raise KeyError(f"Unknown persona: {persona_id}")
        return self._data[persona_id]

    def ids(self):
        return list(self._data.keys())


class CognitiveFunctionRegistry:
    def __init__(self, data: Dict[str, Any] | None = None):
        self._data = data or load_yaml(CONFIG_DIR / "cognitive_functions.yaml")

    def get(self, fn: str) -> Dict[str, Any]:
        return self._data[fn]

    def all(self) -> Dict[str, Any]:
        return self._data


class ScenarioRegistry:
    def __init__(self, scenarios_dir: Path | None = None):
        self._dir = scenarios_dir or (CONFIG_DIR / "scenarios")
        self._cache: Dict[str, Any] = {}
        for path in self._dir.glob("*.yaml"):
            data = load_yaml(path)
            self._cache[data["scenario_id"]] = data

    def get(self, scenario_id: str) -> Dict[str, Any]:
        if scenario_id not in self._cache:
            raise KeyError(f"Unknown scenario: {scenario_id}")
        return self._cache[scenario_id]

    def ids(self):
        return list(self._cache.keys())


class CapabilityRegistry:
    def __init__(self, data: Dict[str, Any] | None = None):
        self._data = data or load_yaml(CONFIG_DIR / "capabilities.yaml")

    def get(self, cap_id: str) -> Dict[str, Any]:
        if cap_id not in self._data:
            raise KeyError(f"Unknown capability: {cap_id}")
        cap = dict(self._data[cap_id])
        cap["id"] = cap_id
        return cap

    def get_available(self, available_ids: list[str]) -> list[Dict[str, Any]]:
        return [self.get(i) for i in available_ids if i in self._data]

    def all_ids(self):
        return list(self._data.keys())


class InteractionDimensionRegistry:
    def __init__(self, data: Dict[str, Any] | None = None):
        self._data = data or load_yaml(CONFIG_DIR / "interaction_dimensions.yaml")

    def get(self, fn: str) -> Dict[str, Any]:
        return self._data[fn]


class ExecutionModeRegistry:
    def __init__(self, data: Dict[str, Any] | None = None):
        self._data = data or load_yaml(CONFIG_DIR / "execution_modes.yaml")

    def get(self, mode_id: str) -> Dict[str, Any]:
        return self._data[mode_id]

    def all(self) -> Dict[str, Any]:
        return self._data


class ScoringConfig:
    def __init__(self, data: Dict[str, Any] | None = None):
        self._data = data or load_yaml(CONFIG_DIR / "scoring.yaml")

    @property
    def weights(self) -> Dict[str, float]:
        return self._data["weights"]

    @property
    def thresholds(self) -> Dict[str, float]:
        return self._data["thresholds"]

    @property
    def max_tool_calls(self) -> Dict[str, int]:
        return self._data["max_tool_calls"]

    @property
    def randomness(self) -> Dict[str, Any]:
        return self._data["randomness"]

    @property
    def execution(self) -> Dict[str, float]:
        return self._data["execution"]


class SafetyRules:
    def __init__(self, data: Dict[str, Any] | None = None):
        self._data = data or load_yaml(CONFIG_DIR / "safety_rules.yaml")

    def all(self) -> Dict[str, Any]:
        return self._data
