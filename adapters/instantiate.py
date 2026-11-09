from __future__ import annotations

"""Shared capability parameter instantiation (mock vehicle adapters)."""

from typing import Any, Dict, Mapping


def _hist(history: Mapping[str, Any], key: str, default: Any) -> Any:
    v = history.get(key)
    return default if v in (None, "") else v


def instantiate(
    capability_id: str,
    intensity: str,
    request: Mapping[str, Any],
    parsed: Mapping[str, Any],
    capability: Mapping[str, Any],
) -> Dict[str, Any]:
    history = request.get("history") or {}
    context = request.get("context") or {}
    presets = (capability.get("parameter_presets") or {}).get(intensity) or {}

    if capability_id == "climate":
        preferred = _hist(history, "preferred_temperature", 23)
        cabin = context.get("cabin_temperature", preferred)
        if float(cabin) >= 26:
            target = min(float(preferred), float(cabin) - abs(int(presets.get("target_delta", -2))))
        else:
            target = preferred
        return {
            "parameters": {"target_temperature": target, "mode": presets.get("mode", "balanced")},
            "mock_result": {
                "current_state": {"temperature": cabin},
                "success": True,
                "target_temperature": target,
            },
        }

    if capability_id == "seat":
        mode = presets.get("mode") or _hist(history, "preferred_seat_mode", "comfort_light")
        return {
            "parameters": {"mode": mode},
            "mock_result": {
                "current_state": {"mode": "default"},
                "target_mode": mode,
                "success": True,
                "result_state": {"mode": mode},
            },
        }

    if capability_id == "entertainment":
        style = presets.get("style") or _hist(history, "preferred_music_style", "soft_acoustic")
        volume = presets.get("volume", 25)
        params = dict((parsed.get("explicit_request") or {}).get("parameters") or {})
        return {
            "parameters": {"style": style, "volume": volume, **params},
            "mock_result": {
                "is_playing": True,
                "music_style_or_scene": style,
                "volume_state": volume,
                "play_result": "started",
            },
        }

    if capability_id == "ambient_light":
        scene = _hist(history, "preferred_ambient_scene", "warm_amber")
        return {
            "parameters": {
                "scene": scene,
                "brightness": presets.get("brightness", 35),
                "transition": presets.get("transition", "slow"),
            },
            "mock_result": {
                "current_scene": "default",
                "switch_result": "ok",
                "mode_after": scene,
            },
        }

    if capability_id == "navigation":
        dest = context.get("destination") or "unknown"
        eta = context.get("estimated_trip_minutes")
        mode = presets.get("mode", "start_known_destination")
        started = bool(context.get("destination_known")) and mode != "suggest_only"
        return {
            "parameters": {"destination": dest, "mode": mode},
            "mock_result": {
                "recommended_destination": dest,
                "eta_minutes": eta,
                "started": started,
            },
        }

    if capability_id == "ecosystem":
        eco_type = context.get("_ecosystem_type_override") or presets.get("ecosystem_type", "route_assist")
        if eco_type == "rest_area":
            recs = [{"name": "最近服务区", "eta_min": 8}]
        elif eco_type == "safe_parking":
            recs = [{"name": "安全停车点", "eta_min": 5}]
        elif eco_type == "nearby_poi":
            recs = [{"name": "沿途咖啡", "score": 4.5}, {"name": "便利店", "score": 4.2}]
        else:
            recs = [{"name": "最优稳定路线", "eta_min": context.get("estimated_trip_minutes")}]
        return {
            "parameters": {"ecosystem_type": eco_type, "count": presets.get("count", 2)},
            "mock_result": {
                "ecosystem_type": eco_type,
                "recommendations": recs,
                "eta_or_score_or_delivery": recs[0] if recs else None,
                "call_result": "ok",
            },
        }

    if capability_id == "notification_mode":
        mode = presets.get("mode") or _hist(history, "preferred_notification_mode", "focus")
        emotion = (parsed.get("emotion") or {}).get("label")
        if emotion in {"fatigue", "low_mood", "silent"}:
            mode = "dnd" if intensity == "high" else "focus"
        if emotion in {"urgency", "lateness"}:
            mode = "focus"
        return {
            "parameters": {"mode": mode},
            "mock_result": {
                "current_mode": "normal",
                "switch_result": "ok",
                "mode_after": mode,
            },
        }

    return {"parameters": dict(presets), "mock_result": {"success": True}}
