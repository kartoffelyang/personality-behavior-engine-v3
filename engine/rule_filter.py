from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from .registries import SafetyRules
from .context_parser import extract_explicit_request


PERMISSION_MAP = {
    "climate": "allow_auto_climate",
    "entertainment": "allow_auto_music",
    "ambient_light": "allow_auto_ambient",
    "navigation": "allow_auto_navigation",
    "seat": "allow_auto_seat",
    "ecosystem": "allow_auto_ecosystem",
    "notification_mode": "allow_auto_notification",
}


def _normalize_utt(text: str) -> str:
    return (text or "").strip().strip("。.!！？?")


def remove_disallowed(
    candidates: List[Dict[str, Any]],
    request: Mapping[str, Any],
    parsed: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    history = request.get("history") or {}
    context = request.get("context") or {}
    available = set(request.get("available_capabilities") or [])
    notes: List[str] = []
    kept: List[Dict[str, Any]] = []

    driving = context.get("driving_state")
    for cap in candidates:
        cid = cap["id"]
        if cid not in available:
            notes.append(f"移除 {cid}: 不在 available_capabilities")
            continue
        perm_key = cap.get("permission_key") or PERMISSION_MAP.get(cid)
        # permission only blocks auto; still allow as suggest candidate unless explicitly False and high automation needed
        # For scoring we keep tools; execution mode later respects permission.
        # Hard-remove only if history says capability disabled completely via f"disable_{cid}"
        if history.get(f"disable_{cid}") is True:
            notes.append(f"移除 {cid}: 用户禁用")
            continue
        if cid == "navigation" and driving == "unavailable":
            notes.append(f"移除 {cid}: 当前状态不允许导航")
            continue
        # vague "走吧" without destination: allow navigation but ecosystem entertainment-like should be filtered later by score
        kept.append(cap)

    # If utterance is minimal and no destination, demote ecosystem by removing when no needs for options
    utterance = _normalize_utt(request.get("user_utterance") or "")
    needs = parsed.get("needs") or {}
    if utterance in {"走吧", "出发", "走"} and not context.get("destination_known"):
        before = len(kept)
        kept = [c for c in kept if c["id"] != "ecosystem"]
        if len(kept) < before:
            notes.append("无明确目的地的简短出发语：移除 ecosystem，避免无关拓展")

    return kept, notes


def try_safety_plan(
    request: Mapping[str, Any],
    parsed: Mapping[str, Any],
    safety_rules: SafetyRules,
    capability_registry,
) -> Optional[Dict[str, Any]]:
    risk = parsed.get("risk") or {}
    overall = float(risk.get("overall") or 0.0)
    rules = safety_rules.all()
    for rule_id, rule in rules.items():
        key = rule.get("trigger_risk_key")
        thr = float(rule.get("trigger_overall_min", 0.75))
        risk_val = float(risk.get(key, 0.0)) if key else 0.0
        if overall >= thr or risk_val >= thr:
            preferred = []
            for cid in rule.get("preferred_capabilities") or []:
                if cid in (request.get("available_capabilities") or []):
                    preferred.append(capability_registry.get(cid))
            return {
                "rule_id": rule_id,
                "behavior_call_type": rule.get("behavior_call_type", "safety"),
                "preferred_capabilities": preferred,
                "forbidden_as_primary": rule.get("forbidden_as_primary") or [],
                "ecosystem_types_allowed": rule.get("ecosystem_types_allowed") or [],
                "dialogue_goal": rule.get("dialogue_goal", "安全优先处置"),
                "notes": [f"触发安全规则 {rule_id}", "安全规则覆盖人格偏好"],
            }
    return None


def try_explicit_request(
    request: Mapping[str, Any],
    parsed: Mapping[str, Any],
    capability_registry,
) -> Optional[Dict[str, Any]]:
    er = parsed.get("explicit_request") or {}
    if not er.get("exists"):
        return None
    cap = er.get("capability")
    # Recheck persisted/legacy parsed data before emitting a priority instruction.
    verified = extract_explicit_request(request.get("user_utterance") or "")
    if not verified.get("exists") or verified.get("capability") != cap:
        return None
    available = request.get("available_capabilities") or []
    if not cap or cap not in available:
        return {
            "behavior_call_type": "explicit",
            "capability": None,
            "notes": [f"显式请求 capability={cap} 但不可用"],
            "preferred_capabilities": [],
            "parameters": er.get("parameters") or {},
        }
    return {
        "behavior_call_type": "explicit",
        "capability": cap,
        "preferred_capabilities": [capability_registry.get(cap)],
        "parameters": er.get("parameters") or {},
        "notes": [f"显式指令优先：满足用户对 {cap} 的明确请求", "仅按用户原话中的动作和条件处理，不扩大授权范围", "人格不得改换工具"],
    }


def decide_execution_mode(
    capability: Mapping[str, Any],
    persona: Mapping[str, Any],
    history: Mapping[str, Any],
    execution_cfg: Mapping[str, float],
) -> Dict[str, Any]:
    props = capability.get("properties") or {}
    inter = persona.get("interaction") or {}
    perm_key = capability.get("permission_key")
    user_permission = 1.0
    if perm_key and history.get(perm_key) is False:
        user_permission = 0.0
    elif perm_key and history.get(perm_key) is True:
        user_permission = 1.0

    auto_score = (
        float(inter.get("automation_level", 0.5))
        * float(props.get("reversibility", 1.0))
        * user_permission
        * (1.0 - float(props.get("risk", 0.0)))
    )
    if props.get("requires_confirmation") or user_permission == 0.0:
        mode = "confirm_execute"
        reason = "REQUIRES_CONFIRMATION" if props.get("requires_confirmation") else "NO_AUTO_PERMISSION"
    elif float(inter.get("confirmation", 0)) >= 0.8 and auto_score < 0.75:
        mode = "confirm_execute"
        reason = "PERSONA_HIGH_CONFIRMATION"
    elif auto_score >= float(execution_cfg.get("auto_threshold", 0.70)):
        mode = "conditional_auto"
        reason = "AUTO_SCORE_HIGH"
    elif auto_score >= float(execution_cfg.get("confirm_threshold", 0.40)):
        mode = "confirm_execute"
        reason = "AUTO_SCORE_MID"
    else:
        mode = "suggest_only"
        reason = "AUTO_SCORE_LOW"

    return {"mode": mode, "auto_score": round(auto_score, 4), "reason_code": reason}
