"""Deterministic selection and composition of the 18 interaction dimensions.

Step 2 chooses only dynamic dimension names and scenario requirements. This module
combines those choices with four fixed dimensions and function style text. It
does not call a model or pretend a calibrated 18-dimension score table exists.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import math
from typing import Any

from .registries import CONFIG_DIR, load_yaml


@lru_cache(maxsize=1)
def _configuration() -> dict[str, Any]:
    config = load_yaml(CONFIG_DIR / "interaction_policy.yaml")
    dimensions = config["dimensions"]
    names = [item["dimension"] for item in dimensions]
    fixed = [item["dimension"] for item in dimensions if item["activation"] == "fixed"]
    dynamic = [item for item in dimensions if item["activation"] == "dynamic"]
    if len(names) != 18 or len(set(names)) != 18 or len(dynamic) != 14:
        raise ValueError("interaction_policy config requires 18 unique dimensions, including 14 dynamic")
    if set(fixed) != set(config["fixed_order"]) or len(fixed) != 4:
        raise ValueError("interaction_policy config requires exactly four fixed dimensions")
    return config


def dimension_catalog() -> list[dict[str, Any]]:
    """Return an independent copy of all definitions for configuration/audit UI."""
    return deepcopy(_configuration()["dimensions"])


def dynamic_dimension_catalog() -> list[dict[str, str]]:
    """The only dimensions Step 2 is allowed to consider; no persona data."""
    return [
        {key: item[key] for key in ("dimension", "definition", "trigger", "boundary")}
        for item in _configuration()["dimensions"]
        if item["activation"] == "dynamic" and item.get("step2_enabled", True)
    ]


def validate_activations(value: Any) -> None:
    """Strict Step 2 contract; callers normalize missing *legacy* data to []."""
    config = _configuration()
    if not isinstance(value, list):
        raise ValueError("activated_interaction_dimensions must be a list")
    if len(value) > config["max_dynamic_dimensions"]:
        raise ValueError("activated_interaction_dimensions must contain 0–4 items")
    allowed = {item["dimension"] for item in dynamic_dimension_catalog()}
    seen: set[str] = set()
    expected_keys = {"dimension", "evidence", "context_requirement"}
    for index, item in enumerate(value):
        label = f"activated_interaction_dimensions[{index}]"
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise ValueError(f"{label} must contain exactly dimension, evidence, context_requirement")
        for key in expected_keys:
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f"{label}.{key} must be a non-empty string")
        if item["dimension"] not in allowed:
            raise ValueError(f"{label}.dimension must name an enabled Step 2 dynamic dimension")
        if item["dimension"] in seen:
            raise ValueError(f"{label}.dimension must not be duplicated")
        seen.add(item["dimension"])


def _function_text(function_dimensions: dict[str, Any], path: str | None) -> str:
    value: Any = function_dimensions
    if not path:
        return ""
    for key in path.split("."):
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value.strip().rstrip("。；") if isinstance(value, str) else ""


def _legacy_strength(persona: dict[str, Any], key: str | None) -> float | None:
    value = (persona.get("interaction") or {}).get(key) if key else None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def build_interaction_policy(
    persona: dict[str, Any],
    dominant: str | None,
    function_dimensions: dict[str, Any],
    activated: list[dict[str, str]],
) -> dict[str, Any]:
    """Compose fixed four plus zero to four choices, preserving scenario limits.

    ``persona`` is the existing persona registry entry, and
    ``function_dimensions`` is InteractionDimensionRegistry.get(dominant).
    Passing dominant=None produces scenario constraints only for NO_MBTI.
    V2 styles use scenario limits, not legacy persona strength scores. Legacy
    paths remain readable for earlier policy configurations.
    """
    validate_activations(activated)
    config = _configuration()
    by_name = {item["dimension"]: item for item in config["dimensions"]}
    fixed_names = list(config["fixed_order"]) if dominant else []
    choices = {item["dimension"]: item for item in activated}
    constraints = []
    for name in fixed_names + list(choices):
        item = by_name[name]
        choice = choices.get(name)
        if not dominant:
            constraints.append({
                "dimension": name,
                "source": "scenario",
                "description": "",
                "description_source": "scenario_only",
                "evidence": choice["evidence"],
                "context_requirement": choice["context_requirement"],
                "level_source": "not_scored",
            })
            continue
        function_path = item.get("function_path")
        text_path = function_path or item.get("legacy_path")
        function_text = _function_text(function_dimensions, text_path)
        strength_key = item.get("strength_key") if not function_path else None
        strength = _legacy_strength(persona, strength_key)
        baseline = item["baseline"].strip().rstrip("。；")
        if function_path and function_text:
            description_source = "shared_context_baseline" if function_text == baseline else "function_style_with_shared_boundary"
            parts = [f"{function_text}。"]
        else:
            description_source = "existing_function_text_with_definition_boundary" if function_text else "definition_baseline"
            parts = [f"人格倾向：{function_text}。"] if function_text else []
        bands = item.get("legacy_strength_bands") if not function_path else None
        if bands and strength is not None:
            # Initial interpretation of an existing strength, not empirical calibration.
            band = "low" if strength < 0.4 else "high" if strength >= 0.7 else "medium"
            parts.append(bands[band])
            description_source = "existing_strength_with_initial_description"
        if baseline and (not function_path or baseline != function_text):
            parts.append(f"{baseline}。")
        constraint = {
            "dimension": name,
            "source": "scenario" if choice else "persona_default",
            "description": "".join(parts),
            "description_source": description_source,
            "evidence": choice["evidence"] if choice else "固定启用",
            "context_requirement": choice["context_requirement"] if choice else "",
        }
        if function_text:
            source_key = "function_text_source" if function_path else "legacy_text_source"
            constraint[source_key] = f"interaction_dimensions.yaml:{dominant}.{text_path}"
        if strength is not None:
            constraint["legacy_strength"] = strength
            constraint["level_source"] = f"existing_persona_config:interaction.{strength_key}"
        else:
            constraint["level_source"] = "not_scored"
        constraints.append(constraint)

    lines = ["本轮交互约束："]
    lines.extend(f"- {rule}" for rule in config["global_rules"])
    for index, constraint in enumerate(constraints, 1):
        line = f"{index}. {constraint['dimension']}：{constraint['description']}"
        if constraint["context_requirement"]:
            line += f" 情境优先：{constraint['context_requirement']}"
        lines.append(line)
    return {
        "version": config["version"],
        "dominant_function": dominant,
        "fixed_dimensions": fixed_names,
        "activated_dimensions": deepcopy(activated),
        "constraints": constraints,
        "compiled_prompt": "\n".join(lines),
        "scoring_status": config.get("scoring_status", "reused_existing_text_and_matching_strengths; full_18_dimension_levels_not_calibrated") if dominant else "scenario_only",
    }
