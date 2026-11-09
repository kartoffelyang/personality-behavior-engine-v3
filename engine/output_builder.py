from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from .llm_router import load_runtime

CAP_CN = {
    "climate": "空调",
    "seat": "座椅",
    "entertainment": "影音娱乐",
    "ambient_light": "氛围灯",
    "navigation": "导航",
    "ecosystem": "生态服务",
    "notification_mode": "通知",
}

INTENSITY_CN = {"low": "轻度", "medium": "中度", "high": "较强"}
MODE_CN = {
    "auto_generate": "自动生成",
    "auto_execute": "自动执行",
    "conditional_auto": "条件自动",
    "confirm_execute": "确认后执行",
    "choose_execute": "选择后执行",
    "suggest_only": "仅建议",
}
# O_Action_Plan 展示用：与审计示例逐字一致（条件自动 -> 条件执行）
O_ACTION_PLAN_MODE_CN = {
    "auto_generate": "自动生成",
    "auto_execute": "自动执行",
    "conditional_auto": "条件执行",
    "confirm_execute": "确认后执行",
    "choose_execute": "选择后执行",
    "suggest_only": "仅建议",
}


def capability_display_order() -> List[str]:
    runtime = load_runtime()
    return list(runtime.get("capability_order") or list(CAP_CN.keys()))


def build_o_behavior(
    *,
    all_caps: Sequence[str],
    plan_actions: Sequence[Mapping[str, Any]],
    composed: Mapping[str, Any],
    no_call: Sequence[Mapping[str, Any]] | None = None,
    include_uncalled: bool = True,
) -> Dict[str, str]:
    action_by_id = {a["capability"]: a for a in plan_actions if a.get("capability")}
    compose_by_id = {
        s["capability"]: s
        for s in (composed.get("behavior_sequence") or [])
        if s.get("type") == "tool"
    }
    reasons = {n.get("capability"): n.get("reason") for n in (no_call or [])}

    out: Dict[str, str] = {}
    for i, cid in enumerate(capability_display_order(), start=1):
        name = CAP_CN.get(cid, cid)
        key = f"{i}、{name}"
        if cid in action_by_id:
            a = action_by_id[cid]
            intensity = INTENSITY_CN.get(str(a.get("intensity")), str(a.get("intensity") or "中度"))
            mode = MODE_CN.get(str(a.get("execution_mode")), str(a.get("execution_mode") or ""))
            hint = a.get("parameters_hint") or (compose_by_id.get(cid) or {}).get("parameters") or {}
            reason = a.get("scenario_influence") or a.get("persona_influence") or "按当前情境调用"
            # 先写调用/不调用，再写程度，最后原因
            out[key] = (
                f"【调用】；调用程度：{intensity}（{mode}）；"
                f"原因：{reason}"
                + (f"；参数要点：{hint}" if hint else "")
            )
        else:
            if not include_uncalled:
                continue
            why = reasons.get(cid) or "情境未要求或评分未入选"
            out[key] = f"【不调用】；调用程度：无；原因：{why}"
    return out


def is_called_behavior(desc: str) -> bool:
    return str(desc or "").startswith("【调用】")


def build_o_action_plan(plan_actions: Sequence[Mapping[str, Any]]) -> List[str]:
    ordered = sorted(plan_actions, key=lambda x: int(x.get("order") or 99))
    out: List[str] = []
    for a in ordered:
        cid = a.get("capability")
        dname = a.get("display_name") or CAP_CN.get(cid, cid)
        name = a.get("name") or a.get("action") or a.get("display_name") or CAP_CN.get(cid, cid)
        details = str(a.get("details") or "").strip()
        mode = O_ACTION_PLAN_MODE_CN.get(
            str(a.get("execution_mode") or ""), str(a.get("execution_mode") or "未知")
        )
        pr = a.get("priority") or ""
        bd = a.get("burden_label") or {"low": "低", "medium": "中", "high": "高"}.get(
            str(a.get("burden") or ""), ""
        )
        wave = a.get("wave")
        prefix_bits = []
        if pr:
            prefix_bits.append(str(pr))
        if bd:
            prefix_bits.append(f"负担{bd}")
        if wave is not None:
            prefix_bits.append(f"波次{wave}")
        prefix = f"[{'｜'.join(prefix_bits)}] " if prefix_bits else ""
        if details:
            out.append(f"{prefix}【{dname}】{name}（执行内容：{details};执行方式：{mode}）")
        else:
            out.append(f"{prefix}【{dname}】{name}（执行方式：{mode}）")
    return out


def build_priority_plan(plan_actions: Sequence[Mapping[str, Any]]) -> Dict[str, List[str]]:
    """Group action display lines by T1/T2/T3."""
    buckets: Dict[str, List[str]] = {"T1": [], "T2": [], "T3": []}
    lines = build_o_action_plan(plan_actions)
    ordered = sorted(plan_actions, key=lambda x: int(x.get("order") or 99))
    for a, line in zip(ordered, lines):
        pr = str(a.get("priority") or "T2")
        if pr not in buckets:
            pr = "T2"
        buckets[pr].append(line)
    return buckets


def build_scene_plan(
    *,
    persona_id: str,
    scenario_id: str,
    utterance: str,
    intent_summary: str,
    persona_strategy: Mapping[str, Any],
    o_behavior: Mapping[str, str],
    o_action_plan: Sequence[str],
    safety_notes: Sequence[str],
) -> str:
    """Narrative scene plan without vehicle spoken lines."""
    lines = []
    lines.append(f"- 情境：用户表述为「{utterance or '（无口语文案）'}」。系统识别为场景 `{scenario_id}`。")
    if intent_summary:
        lines.append(f"- 理解：{intent_summary}")
    strat = persona_strategy.get("scenario_strategy") or persona_strategy.get("action_strategy") or ""
    focus = persona_strategy.get("principle") or ""
    if str(persona_id or "").upper() == "NO_MBTI":
        lines.append(
            f"- 模式：无 MBTI 基线，以「{strat}」组织任务"
            + (f"；原则：{focus}" if focus else "")
            + "。本轮不引入人格偏好，只保留情境目标、安全约束与能力边界。"
        )
    else:
        lines.append(
            f"- 人格：{persona_id} 以「{strat}」组织任务"
            + (f"；原则：{focus}" if focus else "")
            + "。人格可影响介入方式、确认严格度与可选能力顺序，但不覆盖安全与 Must-do。"
        )
    if o_action_plan:
        lines.append(f"- 功能优先级序列：{' → '.join(o_action_plan)}。")
    else:
        lines.append("- 功能优先级序列：本轮不以工具调用为主，保持低干扰待命。")
    called = [f"{k}：{v}" for k, v in o_behavior.items() if str(v).startswith("【调用】")]
    idle = [k for k, v in o_behavior.items() if str(v).startswith("【不调用】")]
    if called:
        lines.append("- 将调用：" + "；".join(called[:6]) + ("…" if len(called) > 6 else ""))
    if idle:
        lines.append("- 不调用：" + "、".join(idle[:5]) + ("…" if len(idle) > 5 else ""))
    if safety_notes:
        lines.append("- 安全/规则：" + "；".join(list(safety_notes)[:4]))
    lines.append(
        "- 降级：若路况或疲劳风险升高，立即从体验优化切换为安全优先，限制娱乐类能力并强化导航/生态/通知约束。"
    )
    lines.append("- 说明：本演示不生成车机语音台词，仅给出可执行行为与场景化体验方案。")
    return "\n".join(lines)
