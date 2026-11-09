from __future__ import annotations

from typing import Any, Dict, List, Mapping

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from adapters.instantiate import instantiate  # noqa: E402

from .rule_filter import decide_execution_mode
from .utils import top_functions


def _intensity_for_persona(persona: Mapping[str, Any], capability_id: str) -> str:
    inter = persona.get("interaction") or {}
    auto = float(inter.get("automation_level", 0.5))
    intervention = float(inter.get("intervention_intensity", 0.5))
    level = (auto + intervention) / 2
    if capability_id in {"ambient_light", "entertainment"} and float(inter.get("boundary_protection", 0.5)) > 0.8:
        return "low" if level < 0.6 else "medium"
    if level >= 0.7:
        return "high"
    if level >= 0.4:
        return "medium"
    return "low"


def compose(
    selected: List[Dict[str, Any]],
    persona: Mapping[str, Any],
    persona_id: str,
    scenario: Mapping[str, Any],
    parsed: Mapping[str, Any],
    request: Mapping[str, Any],
    execution_cfg: Mapping[str, float],
    call_type: str,
    dialogue_goal: str | None = None,
    safety_meta: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    inter = persona.get("interaction") or {}
    sequence: List[Dict[str, Any]] = []
    step = 1

    emotion = (parsed.get("emotion") or {}).get("label", "neutral")
    sequence.append(
        {
            "step": step,
            "type": "dialogue",
            "action": "acknowledge_state",
            "content_goal": dialogue_goal
            or (
                "回应用户当前状态并说明接下来的安排"
                if call_type != "no_tool"
                else "低干扰问候，不强行调用工具"
            ),
        }
    )
    step += 1

    # ask destination only
    if call_type == "no_tool" and not (request.get("context") or {}).get("destination_known"):
        sequence.append(
            {
                "step": step,
                "type": "dialogue",
                "action": "ask_destination",
                "content_goal": "询问目的地，不调用无关舒适类工具",
            }
        )
        step += 1

    selected_ids: List[str] = []
    execution_decisions: List[Dict[str, Any]] = []

    for scored in selected:
        cap = scored.get("capability_meta") or {}
        cid = scored["capability"]
        selected_ids.append(cid)
        intensity = _intensity_for_persona(persona, cid)

        # safety ecosystem type override
        ctx = dict(request.get("context") or {})
        req = dict(request)
        if safety_meta and cid == "ecosystem":
            allowed = safety_meta.get("ecosystem_types_allowed") or ["safe_parking"]
            ctx["_ecosystem_type_override"] = allowed[0]
            req["context"] = ctx

        inst = instantiate(cid, intensity, req, parsed, cap)
        exec_dec = decide_execution_mode(cap, persona, request.get("history") or {}, execution_cfg)
        execution_decisions.append({"capability": cid, **exec_dec})

        sequence.append(
            {
                "step": step,
                "type": "tool",
                "capability": cid,
                "execution_mode": exec_dec["mode"],
                "intensity": intensity,
                "parameters": inst["parameters"],
                "mock_result": inst["mock_result"],
            }
        )
        step += 1

    # feedback
    if selected_ids:
        sequence.append(
            {
                "step": step,
                "type": "dialogue",
                "action": "report_or_offer",
                "content_goal": (
                    "保留用户拒绝或调整的权利"
                    if float(inter.get("confirmation", 0.5)) >= 0.7
                    else "汇报执行结果并确认是否还需调整"
                ),
            }
        )
        step += 1

    if float(inter.get("follow_up", 0)) >= 0.6 and selected_ids:
        sequence.append(
            {
                "step": step,
                "type": "dialogue",
                "action": "follow_up",
                "content_goal": "跟进任务是否闭环或体验是否恢复",
            }
        )

    if call_type == "safety":
        behavior_call_type = "safety"
    elif call_type == "explicit":
        behavior_call_type = "explicit"
    elif not selected_ids:
        behavior_call_type = "no_tool"
    elif len(selected_ids) == 1:
        behavior_call_type = "tool"
    else:
        behavior_call_type = "multi_tool"

    top = top_functions(persona.get("function_scores") or {}, 2)
    activated = []
    strategies = scenario.get("function_strategies") or {}
    for fn, score in top:
        activated.append(
            {
                "function": fn,
                "score": score,
                "scenario_strategy": (strategies.get(fn) or {}).get("strategy"),
            }
        )

    return {
        "behavior_call_type": behavior_call_type,
        "selected_capabilities": selected_ids,
        "behavior_sequence": sequence,
        "execution_decisions": execution_decisions,
        "persona_activation": activated,
        "emotion_label": emotion,
    }
