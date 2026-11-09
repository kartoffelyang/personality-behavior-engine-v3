from __future__ import annotations

import copy
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional

from adapters.instantiate import instantiate

from .llm_router import get_llm, load_runtime, model_version_label, resolve_backend_name
from .model_intent import run_intent
from .model_planner import build_planner_input, build_step7_capability_catalog, build_step7_scenario_understanding, normalize_interaction_dimensions, run_planner
from .planner_validation import PlanValidationError, validate_plan
from .output_builder import build_o_action_plan, build_o_behavior, build_priority_plan, build_scene_plan
from .priority_burden import enrich_actions_priority_burden, waves_summary
from .registries import (
    CapabilityRegistry,
    CognitiveFunctionRegistry,
    ExecutionModeRegistry,
    InteractionDimensionRegistry,
    PersonaRegistry,
    SafetyRules,
    ScenarioRegistry,
    ScoringConfig,
)
from .rule_filter import remove_disallowed, try_explicit_request, try_safety_plan
from .scorer import score_all
from .trace_logger import PERSONA_DESCRIPTION_TITLE, SCENARIO_STRATEGY_TITLE, build_audit_steps, build_layers, make_step
from .audit_narrator import enrich_audit_steps
from .interaction_policy import build_interaction_policy
from .persona_profile import build_persona_profile


CAPABILITY_REASON = {
    "climate": "处理当前体感与舱内温度问题",
    "seat": "恢复或调整舒适坐姿与身体负担",
    "entertainment": "补充轻量内容体验，不抢主任务",
    "ambient_light": "补充环境氛围，但不应盖过主任务",
    "navigation": "把模糊目标收束为可执行路线",
    "ecosystem": "把外部地点或服务选项整理成可执行候选",
    "notification_mode": "降低打扰，保留与当前任务相关的信息",
}

MODE_LABELS = {
    "auto_generate": "自动生成",
    "auto_execute": "自动执行",
    "conditional_auto": "条件自动",
    "confirm_execute": "确认后执行",
    "choose_execute": "选择后执行",
    "suggest_only": "仅建议",
}

NO_MBTI_PERSONA_ID = "NO_MBTI"
NO_MBTI_PROFILE = {
    "function_scores": {},
    "interaction": {},
}


def _normalize_utt(text: str) -> str:
    return (text or "").strip().strip("。.!！？?")


def _dedupe(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _scale(v: float) -> int:
    return max(1, min(5, int(round(float(v) * 4) + 1)))


def _is_no_mbti(persona_id: str | None) -> bool:
    return str(persona_id or "").upper() == NO_MBTI_PERSONA_ID


def resolve_persona_profile(eng: "Engine", persona_id: str) -> Dict[str, Any]:
    if _is_no_mbti(persona_id):
        return copy.deepcopy(NO_MBTI_PROFILE)
    return eng.personas.get(persona_id)


def _step_prompt_override(
    prompt_overrides: Mapping[int, Mapping[str, str]] | None,
    step_no: int,
) -> Mapping[str, str] | None:
    if not prompt_overrides:
        return None
    return prompt_overrides.get(step_no)


def _intent_summary(intent_output: Mapping[str, Any] | None) -> str:
    output = intent_output or {}
    return str(output.get("summary") or (output.get("task") or {}).get("core_goal") or "")


def default_request_scaffold(persona: str, utterance: str, request_id: str | None = None) -> Dict[str, Any]:
    runtime = load_runtime()
    defaults = runtime.get("defaults") or {}
    return {
        "request_id": request_id or f"run_{uuid.uuid4().hex[:10]}",
        "persona": persona,
        "scenario_id": "welcome_on_board",
        "user_utterance": utterance or "",
        "context": copy.deepcopy(defaults.get("context") or {}),
        "history": copy.deepcopy(defaults.get("history") or {}),
        "available_capabilities": list(
            runtime.get("capability_order")
            or [
                "climate",
                "entertainment",
                "ambient_light",
                "navigation",
                "notification_mode",
                "seat",
                "ecosystem",
            ]
        ),
    }


class Engine:
    def __init__(self, use_llm: bool = True, llm_backend: str | None = None):
        self.personas = PersonaRegistry()
        self.scenarios = ScenarioRegistry()
        self.capabilities = CapabilityRegistry()
        self.functions = CognitiveFunctionRegistry()
        self.interactions = InteractionDimensionRegistry()
        self.execution_modes = ExecutionModeRegistry()
        self.scoring = ScoringConfig()
        self.safety = SafetyRules()
        self.llm_backend = resolve_backend_name(llm_backend, use_llm if llm_backend is None else None)
        self.use_llm = self.llm_backend != "rules"


def _function_stack(persona: Mapping[str, Any]) -> Dict[str, Any]:
    scores = persona.get("function_scores") or {}
    ordered = [fn for fn, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]
    return {
        "dominant": (persona.get("step3_profile") or {}).get("dominant") or (ordered[0] if ordered else None),
        "auxiliary": ordered[1] if len(ordered) > 1 else None,
        "tertiary": ordered[2] if len(ordered) > 2 else None,
        "inferior": ordered[3] if len(ordered) > 3 else None,
        "top4": ordered[:4],
        "sequence": "–".join(ordered[:4]),
        "scores": scores,
    }


def _interaction_profile(
    eng: Engine, persona_id: str, persona: Mapping[str, Any],
    dominant: str | None, intent_output: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compile selected requirements; retain the original values for audit only."""
    dimensions = eng.interactions.get(dominant) if dominant else {}
    notes = []
    if dimensions and not _is_no_mbti(persona_id):
        notes.append({
            "function": dominant,
            "stance": (dimensions.get("relation") or {}).get("stance"),
            "voice_org": (dimensions.get("relation") or {}).get("voice_org"),
            "explanation_focus": (dimensions.get("accountability") or {}).get("explanation_focus"),
            "timing": (dimensions.get("intervention") or {}).get("timing"),
            "ordering": (dimensions.get("action_org") or {}).get("ordering"),
        })
    activated = intent_output.get("activated_interaction_dimensions", [])
    policy = build_interaction_policy(dict(persona), dominant, dimensions, activated)
    return {
        "raw_interaction": dict(persona.get("interaction") or {}),
        "scaled_1_to_5": {
            k: _scale(v) for k, v in (persona.get("interaction") or {}).items()
            if k != "option_count" and isinstance(v, (int, float))
        },
        "function_dimension_descriptions": notes,
        "mode": "scenario_only" if _is_no_mbti(persona_id) else "persona_driven",
        "interaction_policy": policy,
    }


def _rank_capabilities_for_audit(
    eng: Engine,
    request: Mapping[str, Any],
    parsed: Mapping[str, Any],
    persona: Mapping[str, Any],
    scenario: Mapping[str, Any],
) -> tuple[List[Dict[str, Any]], List[str]]:
    candidates = eng.capabilities.get_available(request.get("available_capabilities") or [])
    candidates, filter_notes = remove_disallowed(candidates, request, parsed)
    scored = score_all(
        candidates,
        persona,
        scenario,
        parsed,
        request.get("history") or {},
        request.get("context") or {},
        eng.scoring.weights,
    )
    return scored, list(filter_notes)


def _resolve_rule_path(
    eng: Engine,
    request: Mapping[str, Any],
    parsed: Mapping[str, Any],
) -> Dict[str, Any]:
    rule_results: List[str] = []
    safety_meta = None
    call_type = "tool"
    forced_caps: List[str] = []
    forbidden_caps: List[str] = []
    dialogue_goal = None
    random_selection = None

    safety_plan = try_safety_plan(request, parsed, eng.safety, eng.capabilities)
    if safety_plan is not None:
        safety_meta = safety_plan
        call_type = "safety"
        rule_results.extend(safety_plan.get("notes") or [])
        dialogue_goal = safety_plan.get("dialogue_goal")
        forced_caps = [c["id"] for c in (safety_plan.get("preferred_capabilities") or [])]
        forbidden_caps = list(safety_plan.get("forbidden_as_primary") or [])
        return {
            "rule_results": rule_results,
            "random_selection": random_selection,
            "safety_meta": safety_meta,
            "call_type": call_type,
            "dialogue_goal": dialogue_goal,
            "forced_caps": forced_caps,
            "forbidden_caps": forbidden_caps,
        }

    rule_results.append("未触发安全覆盖")
    explicit = try_explicit_request(request, parsed, eng.capabilities)
    if explicit is not None and explicit.get("preferred_capabilities"):
        call_type = "explicit"
        rule_results.extend(explicit.get("notes") or [])
        forced_caps = [c["id"] for c in (explicit.get("preferred_capabilities") or [])]
        return {
            "rule_results": rule_results,
            "random_selection": random_selection,
            "safety_meta": None,
            "call_type": call_type,
            "dialogue_goal": None,
            "forced_caps": forced_caps,
            "forbidden_caps": [],
        }

    if explicit is not None:
        rule_results.extend(explicit.get("notes") or ["显式工具请求不可直接执行"])
    else:
        rule_results.append("不存在显式工具指令")

    if _normalize_utt(request.get("user_utterance") or "") in {"走吧", "出发", "走"} and not (request.get("context") or {}).get(
        "destination_known"
    ):
        call_type = "no_tool"
        rule_results.append("缺少明确目的地，先询问关键信息")

    return {
        "rule_results": rule_results,
        "random_selection": random_selection,
        "safety_meta": None,
        "call_type": call_type,
        "dialogue_goal": None,
        "forced_caps": forced_caps,
        "forbidden_caps": [],
    }


def _recommended_mode(
    persona_id: str,
    capability_id: str,
    call_type: str,
    request: Mapping[str, Any],
) -> str:
    if call_type == "safety":
        if capability_id in {"navigation", "notification_mode"}:
            return "auto_execute"
        if capability_id == "ecosystem":
            return "auto_generate"
        return "confirm_execute"
    if call_type == "explicit":
        if capability_id == "navigation":
            return "choose_execute"
        if capability_id == "ecosystem":
            return "auto_generate"
        return "auto_execute"
    if _is_no_mbti(persona_id):
        if capability_id in {"entertainment", "ambient_light", "notification_mode"}:
            return "suggest_only"
        if capability_id == "ecosystem":
            return "choose_execute"
        if capability_id == "navigation":
            return "choose_execute"
        if capability_id == "climate":
            return "conditional_auto"
        return "confirm_execute"
    if persona_id == "ESTJ":
        if capability_id in {"navigation", "climate"} and (request.get("context") or {}).get("destination_known", True):
            return "auto_execute" if capability_id == "navigation" else "conditional_auto"
        if capability_id == "notification_mode":
            return "confirm_execute"
        if capability_id == "ecosystem":
            return "auto_generate"
        if capability_id in {"entertainment", "ambient_light"}:
            return "suggest_only"
        return "confirm_execute"
    if capability_id in {"entertainment", "ambient_light", "notification_mode"}:
        return "suggest_only"
    if capability_id == "ecosystem":
        return "auto_generate"
    if capability_id == "navigation":
        return "choose_execute"
    if capability_id == "climate":
        return "conditional_auto"
    return "confirm_execute"


def _recommended_intensity(
    persona_id: str,
    capability_id: str,
    scenario_id: str,
    parsed: Mapping[str, Any],
) -> str:
    emotion = (parsed.get("emotion") or {}).get("label")
    risk = float((parsed.get("risk") or {}).get("overall") or 0.0)
    if capability_id == "navigation" and (scenario_id in {"time_pressure", "fatigue_driving"} or risk >= 0.75):
        return "high"
    if capability_id == "climate" and emotion in {"hot_discomfort"}:
        return "high"
    if _is_no_mbti(persona_id):
        if capability_id in {"entertainment", "ambient_light", "seat", "notification_mode", "ecosystem"}:
            return "medium"
        return "high" if capability_id == "navigation" and scenario_id == "time_pressure" else "medium"
    if capability_id in {"entertainment", "ambient_light"}:
        return "low" if persona_id == "INFP" else "medium"
    if capability_id in {"seat", "notification_mode", "ecosystem"}:
        return "medium"
    return "medium" if persona_id == "INFP" else "high"


def _build_scene_backbone(
    *,
    persona_id: str,
    scenario_id: str,
    request: Mapping[str, Any],
    parsed: Mapping[str, Any],
    call_type: str,
    forced_caps: List[str],
    forbidden_caps: List[str],
    whitelist: List[str],
) -> List[str]:
    context = request.get("context") or {}
    needs = parsed.get("needs") or {}
    backbone: List[str] = []

    if call_type in {"explicit", "safety"}:
        backbone.extend(forced_caps)
    elif call_type == "no_tool":
        backbone = []
    elif scenario_id == "time_pressure":
        backbone.extend(["navigation", "notification_mode"])
        if needs.get("physical_comfort", 0) >= 0.7:
            backbone.append("climate")
    elif scenario_id == "temperature_control":
        backbone.extend(["climate"])
        if needs.get("physical_comfort", 0) >= 0.8:
            backbone.append("seat")
    elif scenario_id == "cabin_comfort":
        backbone.extend(["seat", "climate"])
    elif scenario_id == "entertainment_relax":
        backbone.extend(["entertainment", "ambient_light"])
        if needs.get("physical_comfort", 0) >= 0.65:
            backbone.append("climate")
    elif scenario_id == "festival_celebration":
        backbone.extend(["ambient_light", "entertainment", "notification_mode"])
    elif scenario_id == "pleasant_trip":
        backbone.extend(["ecosystem", "navigation"])
        if _is_no_mbti(persona_id):
            backbone.extend(["notification_mode", "climate"])
        elif persona_id == "INFP":
            backbone.extend(["climate", "notification_mode", "entertainment"])
        else:
            backbone.extend(["notification_mode", "climate"])
    elif scenario_id == "emotional_support":
        if _is_no_mbti(persona_id):
            backbone.extend(["seat", "climate"])
        else:
            backbone.extend(["climate", "seat"] if persona_id == "INFP" else ["seat", "climate", "notification_mode"])
    elif scenario_id == "fatigue_driving":
        backbone.extend(["navigation", "ecosystem", "notification_mode", "climate"])
    else:
        if context.get("destination_known"):
            backbone.append("navigation")
        if needs.get("physical_comfort", 0) >= 0.7 or (parsed.get("emotion") or {}).get("label") == "hot_discomfort":
            backbone.append("climate")
        if persona_id == "INFP" and needs.get("emotional_comfort", 0) >= 0.7:
            backbone.append("seat")
        if persona_id == "ESTJ" and context.get("destination_known"):
            backbone.append("notification_mode")

    if (parsed.get("emotion") or {}).get("label") == "hot_discomfort":
        backbone = ["climate"] + backbone
    if persona_id == "INFP" and scenario_id == "pleasant_trip" and "ecosystem" in backbone and "navigation" in backbone:
        backbone = ["ecosystem", "navigation"] + [x for x in backbone if x not in {"ecosystem", "navigation"}]
    if persona_id == "ESTJ" and "navigation" in backbone:
        backbone = ["navigation"] + [x for x in backbone if x != "navigation"]

    cleaned = []
    for cid in _dedupe(backbone):
        if cid in whitelist and cid not in forbidden_caps:
            cleaned.append(cid)
    return cleaned


def _build_blueprint(
    *,
    eng: Engine,
    request: Mapping[str, Any],
    parsed: Mapping[str, Any],
    persona_id: str,
    scenario_id: str,
    dominant: str | None,
    call_type: str,
    forced_caps: List[str],
    forbidden_caps: List[str],
    whitelist: List[str],
    map2: Mapping[str, Any],
    map3: Mapping[str, Any],
    map5: Mapping[str, Any],
    map6: Mapping[str, Any],
) -> Dict[str, Any]:
    backbone = _build_scene_backbone(
        persona_id=persona_id,
        scenario_id=scenario_id,
        request=request,
        parsed=parsed,
        call_type=call_type,
        forced_caps=forced_caps,
        forbidden_caps=forbidden_caps,
        whitelist=whitelist,
    )
    hints = []
    # 蓝图 = 必须域(scene_backbone) + 可选域(白名单内、非必须、且未被安全规则禁止)
    # no_tool 分支语义为"本轮不调用任何能力"，不提供可选域。
    if call_type == "no_tool":
        optional_caps: List[str] = []
    else:
        optional_caps = [cid for cid in whitelist if cid not in backbone and cid not in forbidden_caps]
    no_call_reasons = {
        cid: "可选能力域：安全允许，本轮由情境与人格决定是否调用" for cid in optional_caps
    }
    no_call_reasons.update(
        {cid: "安全规范禁止作为本轮主动作" for cid in whitelist if cid in forbidden_caps}
    )

    def _make_hint(order: int, cid: str, constraint: str) -> dict:
        cap = eng.capabilities.get(cid)
        intensity = _recommended_intensity(persona_id, cid, scenario_id, parsed)
        execution_mode = _recommended_mode(persona_id, cid, call_type, request)
        inst = instantiate(cid, intensity, request, parsed, cap)
        if _is_no_mbti(persona_id):
            persona_influence = "不使用MBTI信息，完全按情境主任务、安全规则和能力白名单组织动作"
        else:
            persona_influence = (
                "更强调用户选择空间与边界保留" if persona_id == "INFP" else "更强调目标收束与执行闭环"
            )
        scenario_influence = map3.get("persona_scenario_strategy") or map2.get("action_strategy") or scenario_id
        detail = CAPABILITY_REASON.get(cid, "按情境执行")
        if cid == "ecosystem" and persona_id == "INFP":
            detail = "先给少量差异化地点方向，再由用户选择"
        elif cid == "ecosystem" and persona_id == "ESTJ":
            detail = "先替用户收敛为更容易直接执行的地点候选"
        elif cid == "ecosystem" and _is_no_mbti(persona_id):
            detail = "先按当前情境筛出少量高相关候选，再交给用户决定"
        elif cid == "navigation" and persona_id == "INFP":
            detail = "在用户确认后进入路线阶段，并保留路线选择空间"
        elif cid == "navigation" and persona_id == "ESTJ":
            detail = "把目的地与路线快速收束成可直接出发的方案"
        elif cid == "navigation" and _is_no_mbti(persona_id):
            detail = "根据当前任务和风险优先级生成清晰可执行的路线方案"
        return {
            "capability": cid,
            "display_name": cap.get("display_name"),
            "action": None,
            "constraint": constraint,
            "details": detail,
            "intensity": intensity,
            "execution_mode": execution_mode,
            "reason": f"{detail}；遵循{scenario_influence}",
            "persona_influence": persona_influence,
            "scenario_influence": scenario_influence,
            "parameters_hint": inst.get("parameters") or {},
        }

    for order, cid in enumerate(backbone, start=1):
        hints.append(_make_hint(order, cid, "must"))
    for order, cid in enumerate(optional_caps, start=len(backbone) + 1):
        hints.append(_make_hint(order, cid, "optional"))

    if call_type == "no_tool":
        notes = ["当前缺少关键执行条件，先补齐信息，不直接调用能力"]
    elif call_type == "safety":
        notes = ["安全优先：娱乐与氛围类能力不作为主动作", "先降低风险，再讨论体验优化"]
    elif call_type == "explicit":
        notes = ["显式请求优先：围绕用户直接命令执行"]
    elif _is_no_mbti(persona_id):
        notes = ["本轮不引入MBTI人格差异，直接按情境骨架、安全规则和能力约束生成动作"]
    else:
        notes = [
            "先形成共同情境骨架，再叠加人格差异",
            f"主导功能为 {dominant}，通过步骤 4-6 影响动作顺序、执行方式和说明重点",
        ]

    return {
        "scene_backbone": backbone,
        "mandatory_capabilities": list(backbone),
        "optional_capabilities": optional_caps,
        "capability_hints": hints,
        "no_call_reasons": no_call_reasons,
        "notes": notes,
        "persona_description": map5,
        "interaction_profile": map6,
    }


def _call_type_from_actions(base_call_type: str, actions: List[Mapping[str, Any]]) -> str:
    if base_call_type in {"safety", "explicit", "no_tool"}:
        return base_call_type
    if not actions:
        return "no_tool"
    if len(actions) == 1:
        return "tool"
    return "multi_tool"


def _compose_from_actions(
    *,
    eng: Engine,
    req: Mapping[str, Any],
    parsed: Mapping[str, Any],
    persona: Mapping[str, Any],
    scenario: Mapping[str, Any],
    call_type: str,
    actions: List[Mapping[str, Any]],
    top4_functions: List[str],
    dialogue_goal: str | None = None,
) -> Dict[str, Any]:
    sequence: List[Dict[str, Any]] = []
    step = 1
    if actions:
        sequence.append(
            {
                "step": step,
                "type": "dialogue",
                "action": "frame_task",
                "content_goal": dialogue_goal or "说明接下来会如何推进当前情境任务",
            }
        )
        step += 1
    else:
        sequence.append(
            {
                "step": step,
                "type": "dialogue",
                "action": "ask_or_standby",
                "content_goal": "询问缺失关键信息或保持低干扰待命",
            }
        )
        step += 1

    for action in actions:
        cid = action.get("capability")
        cap = eng.capabilities.get(cid)
        intensity = action.get("intensity") or "medium"
        inst = instantiate(cid, intensity, req, parsed, cap)
        sequence.append(
            {
                "step": step,
                "type": "tool",
                "capability": cid,
                    "execution_mode": action.get("execution_mode") or "confirm_execute",
                "intensity": intensity,
                "parameters": action.get("parameters_hint") or inst.get("parameters") or {},
                "mock_result": inst.get("mock_result") or {},
                "action": action.get("action"),
                "details": action.get("details"),
            }
        )
        step += 1

    if actions:
        sequence.append(
            {
                "step": step,
                "type": "dialogue",
                "action": "report_or_offer",
                "content_goal": "汇报当前进展，并保留用户继续调整的空间",
            }
        )

    activated = []
    scores = persona.get("function_scores") or {}
    strategies = scenario.get("function_strategies") or {}
    for fn in top4_functions:
        activated.append(
            {
                "function": fn,
                "score": scores.get(fn),
                "scenario_strategy": (strategies.get(fn) or {}).get("strategy"),
            }
        )

    return {
        "behavior_call_type": _call_type_from_actions(call_type, actions),
        "selected_capabilities": [a.get("capability") for a in actions if a.get("capability")],
        "behavior_sequence": sequence,
        "execution_decisions": [
            {
                "capability": a.get("capability"),
                "mode": a.get("execution_mode"),
                "persona_influence": a.get("persona_influence"),
                "scenario_influence": a.get("scenario_influence"),
            }
            for a in actions
        ],
        "persona_activation": activated,
        "emotion_label": (parsed.get("emotion") or {}).get("label", "neutral"),
    }


def _build_result_from_step10(
    *,
    eng: Engine,
    req: Mapping[str, Any],
    backend: str,
    client: Any,
    versions: Mapping[str, Any],
    persona_id: str,
    scenario_id: str,
    persona: Mapping[str, Any],
    scenario: Mapping[str, Any],
    parsed: Mapping[str, Any],
    candidate_ranking: List[Dict[str, Any]],
    rule_results: List[str],
    random_selection: Mapping[str, Any] | None,
    call_type: str,
    dialogue_goal: str | None,
    forced_caps: List[str],
    whitelist: List[str],
    intent_output: Mapping[str, Any],
    persona_strategy: Mapping[str, Any],
    flow_context: Mapping[str, Any],
    blueprint: Mapping[str, Any],
    top4_functions: List[str],
    audit_prefix: List[Dict[str, Any]],
    prompt_override: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    audit = list(audit_prefix)
    planner_started = time.monotonic()
    plan = run_planner(
        llm_backend=backend,
        client=client,
        persona_id=persona_id,
        scenario_id=scenario_id,
        parsed=parsed,
        whitelist=whitelist,
        rule_summary=rule_results,
        flow_context=flow_context,
        blueprint=blueprint,
        prompt_override=prompt_override,
    )
    planner_elapsed_seconds = round(time.monotonic() - planner_started, 2)
    plan_out = plan.get("output") or {}
    planner_failed = bool(plan.get("fallback") and backend != "rules")
    plan_status = plan_out.get("plan_status") or "ready"
    generation_status = "failed" if planner_failed else ("ok" if plan_status == "ready" else plan_status)
    planner_error = dict(plan.get("error_info") or plan_out.get("llm_error") or {})
    failure_label = "生成失败/超时" if planner_error.get("category") == "timeout" else "生成失败"
    actions = []
    fixes = []
    for item in plan_out.get("behavior_actions") or plan_out.get("actions") or []:
        cid = item.get("capability")
        if cid not in whitelist:
            fixes.append(f"移除当前不可用能力 {cid}")
            continue
        if call_type == "safety" and cid in {"entertainment", "ambient_light"}:
            fixes.append(f"安全优先下移除 {cid}")
            continue
        actions.append(dict(item))

    for i, action in enumerate(actions, start=1):
        action["order"] = i

    # A later safety filter can remove a producer or the last action. Do not
    # retain the model's successful status for that now-incomplete sequence.
    if fixes and not planner_failed and backend != "rules":
        try:
            validate_plan({key: (actions if key == "behavior_actions" else plan_out.get(key)) for key in
                           ("behavior_actions", "notes", "final_explanation", "plan_status", "clarification_questions")},
                          plan.get("input") or {}, whitelist)
        except PlanValidationError as exc:
            planner_failed = True
            generation_status = "failed"
            planner_error = {"category": "planner_validation", "message": str(exc), "retryable": True,
                             "hint": "过滤后的行为序列不完整，请重新编排。"}
            plan["fallback"] = True
            plan["error_info"] = planner_error

    cap_lookup = {cid: eng.capabilities.get(cid) for cid in whitelist if cid in eng.capabilities.all_ids()}
    actions = enrich_actions_priority_burden(actions, capability_lookup=cap_lookup)

    composed = _compose_from_actions(
        eng=eng,
        req=req,
        parsed=parsed,
        persona=persona,
        scenario=scenario,
        call_type=call_type,
        actions=actions,
        top4_functions=top4_functions,
        dialogue_goal=dialogue_goal,
    )
    o_behavior = build_o_behavior(
        all_caps=whitelist,
        plan_actions=actions,
        composed=composed,
        no_call=plan_out.get("no_call"),
        include_uncalled=backend == "rules",
    )
    o_action_plan = build_o_action_plan(actions)
    o_priority_plan = build_priority_plan(actions)
    scene_plan = build_scene_plan(
        persona_id=persona_id,
        scenario_id=scenario_id,
        utterance=req.get("user_utterance") or "",
        intent_summary=_intent_summary(intent_output),
        persona_strategy=persona_strategy,
        o_behavior=o_behavior,
        o_action_plan=o_action_plan,
        safety_notes=rule_results,
    )
    if planner_failed:
        actions = []
        o_behavior = {}
        o_action_plan = []
        o_priority_plan = {}
        scene_plan = ""
    scenario_plan = {
        "O_Behavior": o_behavior,
        "O_Action_Plan": o_action_plan,
        "O_Priority_Plan": o_priority_plan,
        "scene_plan": scene_plan,
        "action_plan": failure_label if planner_failed else ("需要补充信息：" + "；".join(plan_out.get("clarification_questions") or []) if plan_status == "needs_clarification" else (" → ".join(o_action_plan) if o_action_plan else "无需工具调用")),
        "thinking_narrative": failure_label if planner_failed else (_intent_summary(intent_output) or scene_plan.split("\n")[0]),
        "spoken_reply": "",
        "scenario_description": scene_plan,
        "assistant_response": "",
        "generation_status": generation_status,
        "generation_error": planner_error if planner_failed else {},
    }

    audit.append(
        make_step(
            7,
            "行为序列生成与输出（功能优先级＋调用负担）",
            input_data={
                "scenario_id": scenario_id,
                "call_type": call_type,
                "whitelist": whitelist,
                "flow_context": flow_context,
                "blueprint": blueprint,
                "planner_input": plan.get("input"),
            },
            output_data={
                "planner_output": plan_out,
                "validated_actions": actions,
                "validation_fixes": fixes,
                "behavior_call_type": composed.get("behavior_call_type"),
                "selected_capabilities": composed.get("selected_capabilities"),
                "O_Behavior": o_behavior,
                "O_Action_Plan": o_action_plan,
                "O_Priority_Plan": o_priority_plan,
                "waves": waves_summary(actions),
                "scene_plan": scene_plan,
                "generation_status": generation_status,
                "clarification_questions": plan_out.get("clarification_questions") or [],
                "generation_error": planner_error if planner_failed else {},
            },
            rules_hit=list(rule_results) + list(fixes) + (
                ["planner_fallback"] + ([plan["error_info"]["category"]] if plan.get("error_info") else [])
                if plan.get("fallback")
                else []
            ),
            model_version=plan.get("model_version") or versions["model"],
            status="fallback" if planner_failed else generation_status,
            prompt_used=plan.get("prompt_used"),
            raw_llm_text=plan.get("raw_llm_text"),
        )
    )
    audit[-1]["attempts"] = plan.get("attempts") or []
    audit[-1]["elapsed_seconds"] = planner_elapsed_seconds

    exec_decision = composed["execution_decisions"][0] if composed.get("execution_decisions") else None
    trace = build_layers(
        request=req,
        parsed=parsed,
        persona_activation=composed.get("persona_activation") or [],
        candidate_ranking=candidate_ranking,
        rule_results=rule_results,
        random_selection=random_selection,
        composed=composed,
        execution_decision=exec_decision,
        scenario_plan=scenario_plan,
    )
    audit_steps = enrich_audit_steps(build_audit_steps(audit))
    trace["audit_steps"] = audit_steps
    trace["final"]["O_Behavior"] = o_behavior
    trace["final"]["O_Action_Plan"] = o_action_plan
    trace["final"]["O_Priority_Plan"] = o_priority_plan
    trace["final"]["scene_plan"] = scene_plan
    trace["final"]["spoken_reply"] = ""
    trace["final"]["generation_status"] = generation_status
    trace["final"]["clarification_questions"] = plan_out.get("clarification_questions") or []
    trace["final"]["generation_error"] = planner_error if planner_failed else {}
    if planner_failed:
        trace["final"]["behavior_call_type"] = None
        trace["final"]["selected_capabilities"] = []
        trace["final"]["behavior_sequence"] = []
        layer4 = trace.get("layers", {}).get("layer4_behavior_sequence", {})
        layer4["behavior_call_type"] = None
        layer4["selected_capabilities"] = []
        layer4["behavior_sequence"] = []
        layer4["execution_decision"] = None

    return {
        "request_id": req.get("request_id"),
        "persona": persona_id,
        "scenario_id": scenario_id,
        "llm_backend": backend,
        "model_version": versions["model"],
        "parsed_context": parsed,
        "selected_capabilities": [] if planner_failed else composed.get("selected_capabilities"),
        "behavior_call_type": None if planner_failed else composed.get("behavior_call_type"),
        "behavior_sequence": [] if planner_failed else composed.get("behavior_sequence"),
        "O_Behavior": o_behavior,
        "O_Action_Plan": o_action_plan,
        "O_Priority_Plan": o_priority_plan,
        "scene_plan": scene_plan,
        "thinking_narrative": scenario_plan["thinking_narrative"],
        "action_plan": scenario_plan["action_plan"],
        "spoken_reply": "",
        "scenario_description": scene_plan,
        "assistant_response": "",
        "generation_status": generation_status,
        "clarification_questions": plan_out.get("clarification_questions") or [],
        "generation_error": planner_error if planner_failed else {},
        "decision_trace": trace,
        "layers": trace["layers"],
        "audit_steps": audit_steps,
        "final": trace["final"],
        "_review_internal": {
            "request": dict(req),
            "versions": dict(versions),
            "persona_id": persona_id,
            "scenario_id": scenario_id,
            "parsed": parsed,
            "candidate_ranking": candidate_ranking,
            "rule_results": rule_results,
            "random_selection": random_selection,
            "call_type": call_type,
            "dialogue_goal": dialogue_goal,
            "forced_caps": forced_caps,
            "whitelist": whitelist,
            "intent_output": dict(intent_output or {}),
            "persona_strategy": dict(persona_strategy or {}),
            "flow_context": dict(flow_context or {}),
            "blueprint": dict(blueprint or {}),
            "top4_functions": list(top4_functions or []),
            "final_params": dict(flow_context or {}),
        },
    }


def rerun_from_review_step(
    previous_result: Mapping[str, Any],
    *,
    step_no: int,
    engine: Optional[Engine] = None,
    llm_backend: str | None = None,
    prompt_overrides: Mapping[int, Mapping[str, str]] | None = None,
    defer_step7: bool = False,
) -> Dict[str, Any]:
    internal = previous_result.get("_review_internal") or {}
    if step_no == 2:
        req = dict(internal.get("request") or {})
        if not req:
            raise ValueError("缺少 review internal state，无法从 Step 2 重算。")
        backend = resolve_backend_name(
            llm_backend or previous_result.get("llm_backend") or internal.get("versions", {}).get("llm_backend"),
            (llm_backend or previous_result.get("llm_backend")) != "rules" if (llm_backend or previous_result.get("llm_backend")) else None,
        )
        eng = engine or Engine(use_llm=(backend != "rules"), llm_backend=backend)
        return generate_behavior_plan(
            req,
            use_llm=(backend != "rules"),
            engine=eng,
            llm_backend=backend,
            prompt_overrides=prompt_overrides,
            defer_step7=defer_step7,
        )

    if step_no != 7:
        raise ValueError(f"不支持从 Step {step_no} 重算。")
    if not internal:
        raise ValueError("缺少 review internal state，无法从 Step 7 重算。")

    backend = resolve_backend_name(
        llm_backend or previous_result.get("llm_backend") or internal.get("versions", {}).get("llm_backend"),
        (llm_backend or previous_result.get("llm_backend")) != "rules" if (llm_backend or previous_result.get("llm_backend")) else None,
    )
    eng = engine or Engine(use_llm=(backend != "rules"), llm_backend=backend)
    client = get_llm(backend)
    persona_id = internal["persona_id"]
    scenario_id = internal["scenario_id"]
    req = internal["request"]
    persona = resolve_persona_profile(eng, persona_id)
    scenario = eng.scenarios.get(scenario_id)
    audit_prefix = build_audit_steps([dict(step) for step in (previous_result.get("audit_steps") or []) if int(step.get("step") or 0) < 7])
    map3 = build_persona_profile(persona_id, persona)
    # Recompile the current contract from saved Step 2, without another model call
    # or any mutation of the saved source record. Older Step 2 has zero activations.
    map6 = _interaction_profile(
        eng, persona_id, persona, _function_stack(persona)["dominant"],
        internal.get("intent_output") or {},
    )
    for step in audit_prefix:
        if step.get("step") == 3:
            step["output"] = map3
            step["model_version"] = "personas.yaml:step3_profile"
        if step.get("step") == 6:
            step["output"] = map6
            step["model_version"] = "interaction_policy.yaml+interaction_dimensions.yaml"
    flow_context = dict(internal.get("flow_context") or {})
    flow_context["persona_profile"] = map3
    flow_context["persona_description"] = next((s["output"] for s in audit_prefix if s.get("step") == 4), {})
    flow_context["user_utterance"] = req.get("user_utterance") or ""
    if req.get("generation_mode") == "demo":
        flow_context["generation_mode"] = "demo"
        flow_context["demo_world"] = req.get("demo_world", "")
        flow_context["compact_demo"] = req.get("compact_demo", False)
    if internal.get("intent_output"):
        flow_context["scenario_understanding"] = build_step7_scenario_understanding(internal["intent_output"])
    flow_context["interaction_dimensions"] = map6
    return _build_result_from_step10(
        eng=eng,
        req=req,
        backend=backend,
        client=client,
        versions=internal["versions"],
        persona_id=persona_id,
        scenario_id=scenario_id,
        persona=persona,
        scenario=scenario,
        parsed=internal["parsed"],
        candidate_ranking=internal["candidate_ranking"],
        rule_results=internal["rule_results"],
        random_selection=internal.get("random_selection"),
        call_type=internal["call_type"],
        dialogue_goal=internal.get("dialogue_goal"),
        forced_caps=list(internal.get("forced_caps") or []),
        whitelist=list(internal["whitelist"]),
        intent_output=internal.get("intent_output") or {},
        persona_strategy=internal.get("persona_strategy") or {},
        flow_context=flow_context,
        blueprint=internal.get("blueprint") or {},
        top4_functions=list(internal.get("top4_functions") or []),
        audit_prefix=audit_prefix,
        prompt_override=_step_prompt_override(prompt_overrides, 7),
    )


def generate_behavior_plan(
    request: Mapping[str, Any],
    use_llm: bool = True,
    engine: Optional[Engine] = None,
    llm_backend: str | None = None,
    prompt_overrides: Mapping[int, Mapping[str, str]] | None = None,
    defer_step7: bool = False,
    shared_intent: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    if llm_backend is not None:
        backend = resolve_backend_name(llm_backend)
    elif engine is not None:
        backend = resolve_backend_name(getattr(engine, "llm_backend", None), use_llm)
    else:
        backend = resolve_backend_name(None, use_llm)
    eng = engine or Engine(use_llm=(backend != "rules"), llm_backend=backend)

    client = get_llm(backend)
    versions = {
        "llm_backend": backend,
        "model": model_version_label(backend, client),
        "capabilities_version": "capabilities.yaml",
        "rules_version": "safety_rules.yaml",
        "personas_version": "personas.yaml",
    }

    req = dict(request)
    persona_id = req["persona"]
    persona = resolve_persona_profile(eng, persona_id)
    audit: List[Dict[str, Any]] = []

    audit.append(
        make_step(
            1,
            "输入：人格＋自由情境",
            input_data={
                "persona": persona_id,
                "user_utterance": req.get("user_utterance"),
                "raw_request_keys": list(req.keys()),
            },
            output_data={
                "persona": persona_id,
                "utterance": req.get("user_utterance"),
                **({"provided_context": req.get("context")} if req.get("context") else {}),
                "default_history": req.get("history"),
                "available_capabilities": req.get("available_capabilities"),
                "versions": versions,
                "run_id": req.get("request_id"),
            },
            model_version=versions["model"],
        )
    )

    # Each branch owns its copy: downstream context updates must not leak
    # into the other personalities or the original shared observation.
    if shared_intent is not None:
        intent = copy.deepcopy(shared_intent)
    else:
        intent_started = time.monotonic()
        intent = run_intent(
            req,
            backend,
            client,
            eng.scenarios,
            prompt_override=_step_prompt_override(prompt_overrides, 2),
        )
        intent["elapsed_seconds"] = round(time.monotonic() - intent_started, 2)
    scenario_id = intent["scenario_id"]
    parsed = intent["parsed_context"]
    req["context"] = intent["context"]
    req["scenario_id"] = scenario_id
    try:
        scenario = eng.scenarios.get(scenario_id)
    except KeyError:
        scenario_id = "welcome_on_board"
        scenario = eng.scenarios.get(scenario_id)
        req["scenario_id"] = scenario_id

    audit.append(
        make_step(
            2,
            "MODEL 1：情境理解",
            input_data=intent.get("input"),
            output_data=intent.get("output"),
            rules_hit=(
                ["intent_fallback"] + ([intent["error_info"]["category"]] if intent.get("error_info") else [])
                if intent.get("fallback")
                else []
            ),
            model_version=intent.get("model_version") or versions["model"],
            status="fallback" if intent.get("fallback") and backend != "rules" else "ok",
            prompt_used=intent.get("prompt_used"),
            raw_llm_text=intent.get("raw_llm_text"),
        )
    )
    audit[-1]["normalization_corrections"] = intent.get("normalization_corrections") or []
    audit[-1]["error_info"] = intent.get("error_info") or {}
    if isinstance(intent.get("elapsed_seconds"), (int, float)):
        audit[-1]["elapsed_seconds"] = intent["elapsed_seconds"]

    if _is_no_mbti(persona_id):
        stack = {
            "dominant": None,
            "auxiliary": None,
            "tertiary": None,
            "inferior": None,
            "top4": [],
            "sequence": "不使用MBTI",
            "scores": {},
            "mode": "scenario_only",
        }
    else:
        stack = _function_stack(persona)
    map3 = build_persona_profile(persona_id, persona)
    audit.append(
        make_step(
            3,
            "MAP：人格主导功能查表",
            input_data={"persona": persona_id},
            output_data=map3,
            model_version="personas.yaml:step3_profile",
        )
    )

    dominant = stack["dominant"]
    fn_meta = eng.functions.get(dominant) if dominant else {}
    map5 = {
        "action_strategy": fn_meta.get("action_strategy") or "按情境目标与约束组织行动",
        "action_logic": list(fn_meta.get("action_logic") or []),
    }
    audit.append(
        make_step(
            4,
            PERSONA_DESCRIPTION_TITLE,
            input_data={"dominant_function": dominant},
            output_data=map5,
            model_version="cognitive_functions.yaml",
        )
    )

    scenario_strategy = ((scenario.get("function_strategies") or {}).get(dominant) or {})
    if _is_no_mbti(persona_id):
        map2 = {
            "dominant_function": None,
            "action_strategy": "不使用MBTI，直接按情境主任务、安全约束与白名单组织动作",
            "cross_scenario_prototype": "由场景目标和风险直接决定动作方向",
            "scenario_strategy": "以场景主目标为先，再补充舒适与低打扰动作",
            "preferred_needs": [],
        }
    else:
        map2 = {
            "dominant_function": dominant,
            "action_strategy": fn_meta.get("action_strategy"),
            "cross_scenario_prototype": fn_meta.get("cross_scenario_prototype"),
            "scenario_strategy": scenario_strategy.get("strategy"),
            "preferred_needs": scenario_strategy.get("preferred_needs"),
            "action_logic": list(scenario_strategy.get("action_logic") or []),
        }
    audit.append(
        make_step(
            5,
            SCENARIO_STRATEGY_TITLE,
            input_data={"dominant_function": dominant, "scenario_id": scenario_id},
            output_data=map2,
            model_version=f"scenarios/{scenario_id}.yaml+cognitive_functions.yaml",
        )
    )

    map6 = _interaction_profile(eng, persona_id, persona, stack["dominant"], intent.get("output") or {})
    audit.append(
        make_step(
            6,
            "MAP：人格交互维度判断",
            input_data={
                "persona_interaction": persona.get("interaction") or {},
                "dominant_function": stack["dominant"],
                "activated_interaction_dimensions": (intent.get("output") or {}).get("activated_interaction_dimensions", []),
            },
            output_data=map6,
            model_version="interaction_policy.yaml+interaction_dimensions.yaml",
        )
    )

    candidate_ranking, filter_notes = _rank_capabilities_for_audit(eng, req, parsed, persona, scenario)
    rule_state = _resolve_rule_path(eng, req, parsed)
    rule_results = list(filter_notes) + list(rule_state["rule_results"])
    # Actual available capabilities define the catalog, not a scenario selection.
    whitelist = list(req.get("available_capabilities") or eng.capabilities.all_ids())
    capability_catalog = []
    for cid in whitelist:
        cap = eng.capabilities.get(cid)
        capability_catalog.append(
            {
                "capability": cid,
                "display_name": cap.get("display_name"),
                "atomic_abilities": cap.get("atomic_abilities") or [],
            }
        )
    flow_context = {
        "user_utterance": req.get("user_utterance") or "",
        "persona_profile": map3,
        "scenario_understanding": build_step7_scenario_understanding(intent.get("output")),
        "execution_context": {"context": req.get("context") or {}, "history": req.get("history") or {}},
        "persona_description": map5,
        "persona_scene_strategy": map2,
        "interaction_dimensions": map6,
        "capability_catalog": build_step7_capability_catalog(capability_catalog),
    }
    if req.get("generation_mode") == "demo":
        flow_context["generation_mode"] = "demo"
        flow_context["demo_world"] = req.get("demo_world", "")
        flow_context["compact_demo"] = req.get("compact_demo", False)
    blueprint = _build_blueprint(
        eng=eng,
        request=req,
        parsed=parsed,
        persona_id=persona_id,
        scenario_id=scenario_id,
        dominant=dominant,
        call_type=rule_state["call_type"],
        forced_caps=rule_state["forced_caps"],
        forbidden_caps=rule_state["forbidden_caps"],
        whitelist=whitelist,
        map2=map2,
        map3={
            "persona_scenario_strategy": scenario_strategy.get("strategy"),
            "preferred_needs": scenario_strategy.get("preferred_needs"),
        },
        map5=map5,
        map6=map6,
    )
    if _is_no_mbti(persona_id):
        persona_strategy = {
            "scenario_strategy": "不使用MBTI，直接按情境主目标组织动作",
            "action_strategy": "场景优先",
            "principle": "只保留情境、风险与能力约束，不引入人格偏好",
        }
    else:
        persona_strategy = {
            "scenario_strategy": scenario_strategy.get("strategy") or fn_meta.get("action_strategy"),
            "action_strategy": fn_meta.get("action_strategy"),
            "principle": fn_meta.get("principle"),
        }
    if defer_step7:
        audit.append(
            make_step(
                7,
                "行为序列生成与输出（功能优先级＋调用负担）",
                status="pending",
                input_data=build_planner_input(
                    persona_id=persona_id,
                    scenario_id=scenario_id,
                    parsed=parsed,
                    whitelist=whitelist,
                    rule_summary=rule_results,
                    flow_context=flow_context,
                    blueprint=blueprint,
                ),
                output_data={},
                model_version=versions["model"],
            )
        )
        audit_steps = enrich_audit_steps(build_audit_steps(audit))
        audit_steps[-1]["input_text"] = "Step 2–6 已完成；行为序列生成所需情境、人格策略、能力范围和执行约束已准备。"
        audit_steps[-1]["output_text"] = "尚未生成。确认至 Step 6 后，由用户在 Step 7 页面手动触发。"
        return {
            "request_id": req.get("request_id"),
            "persona": persona_id,
            "scenario_id": scenario_id,
            "llm_backend": backend,
            "model_version": versions["model"],
            "parsed_context": parsed,
            "selected_capabilities": [],
            "behavior_call_type": None,
            "behavior_sequence": [],
            "O_Behavior": {},
            "O_Action_Plan": [],
            "O_Priority_Plan": {},
            "scene_plan": "",
            "spoken_reply": "",
            "audit_steps": audit_steps,
            "final": {},
            "step7_deferred": True,
            "_review_internal": {
                "request": dict(req),
                "versions": dict(versions),
                "persona_id": persona_id,
                "scenario_id": scenario_id,
                "parsed": parsed,
                "candidate_ranking": candidate_ranking,
                "rule_results": rule_results,
                "random_selection": rule_state["random_selection"],
                "call_type": rule_state["call_type"],
                "dialogue_goal": rule_state["dialogue_goal"],
                "forced_caps": list(rule_state["forced_caps"]),
                "whitelist": whitelist,
                "intent_output": dict(intent.get("output") or {}),
                "persona_strategy": dict(persona_strategy),
                "flow_context": dict(flow_context),
                "blueprint": dict(blueprint),
                "top4_functions": list(stack["top4"]),
                "final_params": dict(flow_context),
            },
        }
    return _build_result_from_step10(
        eng=eng,
        req=req,
        backend=backend,
        client=client,
        versions=versions,
        persona_id=persona_id,
        scenario_id=scenario_id,
        persona=persona,
        scenario=scenario,
        parsed=parsed,
        candidate_ranking=candidate_ranking,
        rule_results=rule_results,
        random_selection=rule_state["random_selection"],
        call_type=rule_state["call_type"],
        dialogue_goal=rule_state["dialogue_goal"],
        forced_caps=list(rule_state["forced_caps"]),
        whitelist=whitelist,
        intent_output=intent.get("output") or {},
        persona_strategy=persona_strategy,
        flow_context=flow_context,
        blueprint=blueprint,
        top4_functions=list(stack["top4"]),
        audit_prefix=audit,
        prompt_override=_step_prompt_override(prompt_overrides, 7),
    )
