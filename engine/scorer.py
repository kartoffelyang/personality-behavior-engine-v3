from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .utils import as_float_map, clamp01, weighted_dot


def build_scenario_need_bonus(
    persona_scores: Mapping[str, float],
    scenario: Mapping[str, Any],
) -> Dict[str, float]:
    bonus: Dict[str, float] = {}
    strategies = scenario.get("function_strategies") or {}
    for fn, score in persona_scores.items():
        strategy = strategies.get(fn) or {}
        preferred = strategy.get("preferred_needs") or {}
        for need_name, weight in preferred.items():
            bonus[need_name] = bonus.get(need_name, 0.0) + float(score) * float(weight)
    # normalize roughly by max
    if bonus:
        mx = max(bonus.values()) or 1.0
        bonus = {k: clamp01(v / mx) for k, v in bonus.items()}
    return bonus


def history_match(capability: Mapping[str, Any], history: Mapping[str, Any]) -> float:
    keys = capability.get("history_keys") or []
    if not keys:
        return 0.35
    hits = 0
    for k in keys:
        if history.get(k) not in (None, "", False):
            hits += 1
    return clamp01(0.4 + 0.6 * (hits / max(len(keys), 1)))


def context_match(capability: Mapping[str, Any], context: Mapping[str, Any], parsed: Mapping[str, Any]) -> float:
    cap_id = capability.get("id")
    score = 0.4
    cabin_temp = context.get("cabin_temperature")
    needs = parsed.get("needs") or {}
    primary = max(needs.items(), key=lambda x: x[1])[0] if needs else None

    if cap_id == "climate" and isinstance(cabin_temp, (int, float)):
        if cabin_temp >= 26 or cabin_temp <= 18:
            score += 0.55
        if primary == "physical_comfort":
            score += 0.25
    if cap_id == "navigation" and context.get("destination_known"):
        score += 0.35
        # demote navigation when the salient need is cabin comfort, not trip task
        if primary == "physical_comfort" and float(needs.get("task_completion", 0)) < 0.6:
            score -= 0.45
        if primary in {"emotional_comfort", "personal_boundary", "user_autonomy"} and float(needs.get("task_completion", 0)) < 0.4:
            score -= 0.25
    if cap_id == "notification_mode" and context.get("calendar_pressure"):
        score += 0.25
    if cap_id == "ecosystem" and context.get("destination_known"):
        score += 0.15
        if primary == "physical_comfort":
            score -= 0.3
    emotion = (parsed.get("emotion") or {}).get("label")
    if emotion in {"fatigue", "low_mood", "silent"} and cap_id in {"ambient_light", "entertainment", "seat"}:
        score += 0.2
    if emotion in {"urgency", "lateness"} and cap_id in {"navigation", "ecosystem", "notification_mode"}:
        score += 0.3
    if emotion == "hot_discomfort" and cap_id == "climate":
        score += 0.35
    if emotion == "hot_discomfort" and cap_id in {"navigation", "entertainment", "ambient_light", "ecosystem"}:
        score -= 0.25
    passengers = context.get("passenger_count") or 1
    if passengers and int(passengers) > 1 and cap_id in {"climate", "notification_mode"}:
        score += 0.15
    return clamp01(score)


def interaction_match(
    capability: Mapping[str, Any],
    persona: Mapping[str, Any],
    execution_mode_hint: str | None = None,
) -> float:
    inter = persona.get("interaction") or {}
    props = capability.get("properties") or {}
    auto = float(inter.get("automation_level", 0.5))
    confirm = float(inter.get("confirmation", 0.5))
    boundary = float(inter.get("boundary_protection", 0.5))
    intrusion = float(props.get("intrusiveness", 0.3))
    # Fi-like personas prefer low intrusion capabilities
    score = 0.5
    score += (1.0 - intrusion) * boundary * 0.35
    score += auto * float(props.get("reversibility", 1.0)) * 0.25
    if props.get("requires_confirmation") and confirm > 0.7:
        score += 0.1
    if execution_mode_hint == "suggest":
        score += confirm * 0.1
    return clamp01(score)


def score_capability(
    capability: Mapping[str, Any],
    persona: Mapping[str, Any],
    scenario: Mapping[str, Any],
    parsed: Mapping[str, Any],
    history: Mapping[str, Any],
    context: Mapping[str, Any],
    weights: Mapping[str, float],
) -> Dict[str, Any]:
    needs = as_float_map(parsed.get("needs") or {})
    persona_scores = as_float_map(persona.get("function_scores") or {})
    supported = as_float_map(capability.get("supported_needs") or {})
    affinity = as_float_map(capability.get("function_affinity") or {})
    props = capability.get("properties") or {}
    scenario_bonus = build_scenario_need_bonus(persona_scores, scenario)

    need = weighted_dot(needs, supported)
    cognitive = weighted_dot(persona_scores, affinity)
    scenario_m = weighted_dot(scenario_bonus, supported)
    hist = history_match(capability, history)
    inter = interaction_match(capability, persona)
    ctx = context_match(capability, context, parsed)
    risk_cost = float(props.get("risk", 0.0))
    intrusion_cost = float(props.get("intrusiveness", 0.0))

    final = (
        weights["need_match"] * need
        + weights["cognitive_match"] * cognitive
        + weights["scenario_match"] * scenario_m
        + weights["history_match"] * hist
        + weights["interaction_match"] * inter
        + weights["context_match"] * ctx
        - weights["risk_cost"] * risk_cost
        - weights["intrusion_cost"] * intrusion_cost
    )
    # Te-dominant personas keep trip orchestration when destination is known
    if capability.get("id") == "navigation" and float(persona_scores.get("Te", 0)) >= 0.8:
        if context.get("destination_known"):
            final += 0.10
    if capability.get("id") == "notification_mode" and float(persona_scores.get("Te", 0)) >= 0.8:
        if (parsed.get("emotion") or {}).get("label") in {"fatigue", "urgency", "lateness"}:
            final += 0.06
    if capability.get("id") == "climate" and (parsed.get("emotion") or {}).get("label") == "hot_discomfort":
        final += 0.12
    if capability.get("id") == "seat" and (parsed.get("emotion") or {}).get("label") == "hot_discomfort":
        final -= 0.08
    final = clamp01(final)

    return {
        "capability": capability["id"],
        "display_name": capability.get("display_name"),
        "need_match": round(need, 4),
        "cognitive_match": round(cognitive, 4),
        "scenario_match": round(scenario_m, 4),
        "history_match": round(hist, 4),
        "interaction_match": round(inter, 4),
        "context_match": round(ctx, 4),
        "risk_cost": round(risk_cost, 4),
        "intrusion_cost": round(intrusion_cost, 4),
        "final_score": round(final, 4),
        "max_persona_randomness": float(props.get("max_persona_randomness", 0.1)),
        "properties": props,
        "capability_meta": capability,
    }


def score_all(
    candidates: List[Mapping[str, Any]],
    persona: Mapping[str, Any],
    scenario: Mapping[str, Any],
    parsed: Mapping[str, Any],
    history: Mapping[str, Any],
    context: Mapping[str, Any],
    weights: Mapping[str, float],
) -> List[Dict[str, Any]]:
    scored = [
        score_capability(c, persona, scenario, parsed, history, context, weights)
        for c in candidates
    ]
    scored.sort(key=lambda x: x["final_score"], reverse=True)
    return scored
