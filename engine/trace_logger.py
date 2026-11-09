from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional


PERSONA_DESCRIPTION_TITLE = "MAP：人格基本行动策略"
SCENARIO_STRATEGY_TITLE = "MAP：当前场景行动策略"


def basic_action_strategy(output: Mapping[str, Any]) -> Dict[str, Any]:
    """Project both current and saved Step 4 content onto action-only fields."""
    entries = output.get("top4_functions") or []
    source = entries[0] if entries else output
    return {"action_strategy": source.get("action_strategy") or "按情境目标与约束组织行动",
            "action_logic": list(source.get("action_logic") or [])}


def has_legacy_persona_step_order(steps: List[Dict[str, Any]]) -> bool:
    by_number = {s.get("step"): s for s in steps}
    scene, persona = by_number.get(4, {}), by_number.get(5, {})
    return bool(
        (scene.get("title") == "MAP：人格场景行动策略"
         or "scenario_strategy" in (scene.get("output") or {}))
        and (persona.get("title") == "MAP：目标人格描述及行动策略"
             or "target_persona_description" in (persona.get("output") or {}))
    )


def normalize_persona_step_order(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Present legacy records in the current order without rewriting their sources."""
    legacy = has_legacy_persona_step_order(steps)
    result = []
    for step in steps:
        item = dict(step)
        if legacy and item.get("step") in {4, 5}:
            item["step"] = 9 - item["step"]
            item["title"] = PERSONA_DESCRIPTION_TITLE if item["step"] == 4 else SCENARIO_STRATEGY_TITLE
        if item.get("step") == 4 and {"action_strategy", "top4_functions", "target_persona_description"}.intersection(item.get("output") or {}):
            item["title"] = PERSONA_DESCRIPTION_TITLE
            item["output"] = basic_action_strategy(item.get("output") or {})
            old_input = item.get("input") or {}
            item["input"] = {"dominant_function": old_input.get("dominant_function") or next(iter(old_input.get("top4_functions") or []), None)}
        result.append(item)
    return sorted(result, key=lambda s: int(s.get("step") or 0))


def make_step(
    step: int,
    title: str,
    *,
    status: str = "ok",
    input_data: Any = None,
    output_data: Any = None,
    rules_hit: Optional[List[str]] = None,
    model_version: str = "rules",
    prompt_used: Optional[Dict[str, str]] = None,
    raw_llm_text: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "step": step,
        "title": title,
        "status": status,
        "input": input_data if input_data is not None else {},
        "output": output_data if output_data is not None else {},
        "rules_hit": list(rules_hit or []),
        "model_version": model_version,
        "prompt_used": dict(prompt_used or {}),
        "raw_llm_text": raw_llm_text or "",
    }


def build_audit_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # ensure ordered 1..n
    return normalize_persona_step_order(steps)


# --- legacy helpers kept for older saved JSON / markdown ---

def risk_label(overall: float) -> str:
    if overall >= 0.75:
        return "high"
    if overall >= 0.4:
        return "medium"
    return "low"


def build_layers(
    request: Mapping[str, Any],
    parsed: Mapping[str, Any],
    persona_activation: List[Dict[str, Any]],
    candidate_ranking: List[Dict[str, Any]],
    rule_results: List[str],
    random_selection: Mapping[str, Any] | None,
    composed: Mapping[str, Any],
    execution_decision: Mapping[str, Any] | None,
    scenario_plan: Mapping[str, str],
) -> Dict[str, Any]:
    """Backward-compatible thin wrapper used by older callers."""
    from .utils import primary_need, secondary_need

    needs = parsed.get("needs") or {}
    evidence = parsed.get("evidence") or []
    input_facts = [e.get("fact") for e in evidence if e.get("fact")]
    if not input_facts and request.get("user_utterance"):
        input_facts.append(f"用户表达：{request.get('user_utterance')}")

    ranking_view = []
    for c in candidate_ranking:
        ranking_view.append(
            {
                "capability": c.get("capability"),
                "need_match": c.get("need_match"),
                "cognitive_match": c.get("cognitive_match"),
                "scenario_match": c.get("scenario_match"),
                "history_match": c.get("history_match"),
                "risk_cost": c.get("risk_cost"),
                "final_score": c.get("final_score"),
            }
        )

    action_plan = scenario_plan.get("action_plan") or scenario_plan.get("scenario_description")
    spoken_reply = scenario_plan.get("spoken_reply") or scenario_plan.get("assistant_response")
    thinking_narrative = scenario_plan.get("thinking_narrative") or ""

    layers = {
        "layer1_context": {
            "input_facts": input_facts,
            "recognized_state": {
                "primary_emotion": (parsed.get("emotion") or {}).get("label"),
                "emotion_intensity": (parsed.get("emotion") or {}).get("intensity"),
                "primary_need": primary_need(needs),
                "secondary_need": secondary_need(needs),
                "safety_risk": risk_label(float((parsed.get("risk") or {}).get("overall") or 0)),
                "needs": needs,
                "risk": parsed.get("risk"),
                "confidence": parsed.get("confidence"),
                "evidence": evidence,
                "parser_fallback": parsed.get("parser_fallback", False),
            },
        },
        "layer2_persona_activation": {
            "persona": request.get("persona"),
            "activation": persona_activation,
        },
        "layer3_candidates_and_rules": {
            "candidate_ranking": ranking_view,
            "rule_results": rule_results,
            "random_selection": random_selection,
        },
        "layer4_behavior_sequence": {
            "behavior_call_type": composed.get("behavior_call_type"),
            "selected_capabilities": composed.get("selected_capabilities"),
            "behavior_sequence": composed.get("behavior_sequence"),
            "execution_decision": execution_decision,
        },
        "layer5_outputs": {
            "thinking_narrative": thinking_narrative,
            "action_plan": action_plan,
            "spoken_reply": spoken_reply,
        },
    }
    final = {
        "behavior_call_type": composed.get("behavior_call_type"),
        "selected_capabilities": composed.get("selected_capabilities"),
        "behavior_sequence": composed.get("behavior_sequence"),
        "thinking_narrative": thinking_narrative,
        "action_plan": action_plan,
        "spoken_reply": spoken_reply,
        "O_Behavior": scenario_plan.get("O_Behavior"),
        "O_Action_Plan": scenario_plan.get("O_Action_Plan"),
        "scene_plan": scenario_plan.get("scene_plan"),
    }
    return {"layers": layers, "final": final}


def to_markdown(result: Mapping[str, Any]) -> str:
    final = result.get("final") or {}
    lines = [
        f"# Case {result.get('request_id', 'unknown')} / {result.get('persona', '-')}",
        "",
        "## 1. 思考过程（模型自述）",
        "",
        final.get("thinking_narrative") or result.get("thinking_narrative") or "（空）",
        "",
        "## 2. 会怎么做（调用程度 + 先后顺序）",
        "",
        final.get("action_plan") or result.get("action_plan") or "（空）",
        "",
        "## 3. 到时候怎么说（功能回复）",
        "",
        "> 本版本默认不输出车机语音台词，仅保留行为序列与场景方案。",
        "",
        "## 附：结构化决策轨迹（供审计）",
        "",
    ]

    parsed = result.get("parsed_context") or {}
    emotion = parsed.get("emotion") or {}
    risk = parsed.get("risk") or {}
    lines.extend(
        [
            "### 情境识别",
            f"- 场景: `{result.get('scenario_id', '-')}`",
            f"- 情绪: {emotion.get('label', 'neutral')} ({emotion.get('intensity', 0)})",
            f"- 安全风险: {risk_label(float(risk.get('overall') or 0))}",
            "",
            "### 行为序列",
            f"- behavior_call_type: `{result.get('behavior_call_type', '-')}`",
            f"- selected: `{result.get('selected_capabilities', [])}`",
        ]
    )
    for step in result.get("behavior_sequence") or []:
        if step.get("type") == "tool":
            lines.append(
                f"- step{step.get('step')}: tool {step.get('capability')} "
                f"[{step.get('execution_mode')}/{step.get('intensity')}] {step.get('parameters')}"
            )
        else:
            lines.append(
                f"- step{step.get('step')}: dialogue/{step.get('action')} — {step.get('content_goal')}"
            )
    lines.append("")
    return "\n".join(lines)
