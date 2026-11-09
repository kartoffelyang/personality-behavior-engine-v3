from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .llm_client import LLMClient, extract_json_object

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "response_generator.txt"

CAP_CN = {
    "climate": "空调",
    "seat": "座椅",
    "entertainment": "影音娱乐",
    "ambient_light": "氛围灯",
    "navigation": "导航",
    "ecosystem": "生态服务",
    "notification_mode": "通知",
}

MODE_CN = {
    "auto_generate": "自动生成",
    "auto_execute": "自动执行",
    "conditional_auto": "条件自动",
    "confirm_execute": "确认后执行",
    "choose_execute": "选择后执行",
    "suggest_only": "仅建议",
}

INTENSITY_CN = {
    "low": "轻度",
    "medium": "中度",
    "high": "较强",
}


def _tool_steps(composed: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [s for s in (composed.get("behavior_sequence") or []) if s.get("type") == "tool"]


def _caps_cn(caps: List[str]) -> str:
    return "、".join(CAP_CN.get(c, c) for c in caps) if caps else "不调用座舱工具"


def _ordered_action_text(composed: Mapping[str, Any]) -> str:
    lines = []
    for step in composed.get("behavior_sequence") or []:
        n = step.get("step")
        if step.get("type") == "dialogue":
            lines.append(f"第{n}步【对话/{step.get('action')}】：{step.get('content_goal')}")
        else:
            cid = step.get("capability")
            mode = MODE_CN.get(step.get("execution_mode", ""), step.get("execution_mode"))
            intensity = INTENSITY_CN.get(step.get("intensity", ""), step.get("intensity"))
            params = step.get("parameters") or {}
            lines.append(
                f"第{n}步【工具/{CAP_CN.get(cid, cid)}】：调用程度={mode}；强度={intensity}；参数={params}"
            )
    return " → ".join(lines) if lines else "无执行步骤"


def _template_response(
    persona_id: str,
    parsed: Mapping[str, Any],
    composed: Mapping[str, Any],
    interaction_snippets: list[str],
) -> Dict[str, str]:
    emotion = (parsed.get("emotion") or {}).get("label", "neutral")
    caps = list(composed.get("selected_capabilities") or [])
    call_type = composed.get("behavior_call_type")
    activation = composed.get("persona_activation") or []
    strategies = "、".join(
        f"{a.get('function')}（{a.get('scenario_strategy')}）" for a in activation
    ) or "默认迎宾策略"
    primary_need = None
    needs = parsed.get("needs") or {}
    if needs:
        primary_need = max(needs.items(), key=lambda x: x[1])[0]

    # thinking narrative
    if persona_id == "INFP":
        persona_self = "我是 INFP 取向的车载助手，更看重个人感受、边界与低干扰介入"
    else:
        persona_self = "我是 ESTJ 取向的车载助手，更看重目标达成、效率编排与可验证结果"

    if call_type == "safety":
        thinking = (
            f"{persona_self}。当前识别到高安全风险（情绪={emotion}），"
            f"因此安全规则优先于人格偏好，决策改为强制安全处置，选定 {_caps_cn(caps)}；"
            f"人格只影响怎么说话，不改变安全动作。"
        )
    elif call_type == "explicit":
        thinking = (
            f"{persona_self}。用户给出了明确工具指令，显式指令规则优先，"
            f"所以直接执行 {_caps_cn(caps)}，不再按人格偏好改换工具。"
        )
    elif call_type == "no_tool":
        thinking = (
            f"{persona_self}。当前情境信息不足或无明显可执行需求（主需求={primary_need}），"
            f"激活策略为 {strategies}。为避免为了体现人格而乱调氛围/影音，我选择先对话澄清，不调用工具。"
        )
    else:
        thinking = (
            f"{persona_self}。当前情绪={emotion}，主需求={primary_need}。"
            f"因此激活 {strategies}，在候选能力中按需求匹配、人格亲和与场景策略评分后选择 {_caps_cn(caps)}。"
            f"{'我倾向先确认再执行，保留用户拒绝权。' if persona_id == 'INFP' else '我倾向尽快组织执行并闭环任务。'}"
        )
        if interaction_snippets:
            thinking += f"关系站位：{interaction_snippets[0]}。"

    # action plan with degree + order
    order_text = _ordered_action_text(composed)
    action_plan = (
        f"调用类型={call_type}。按先后顺序执行：{order_text}。"
        f"说明：execution_mode 表示执行方式（自动生成/自动执行/条件自动/确认后执行/选择后执行/仅建议），"
        f"intensity 表示调节强度（轻度/中度/较强）。"
    )

    # spoken reply
    if persona_id == "INFP":
        if call_type == "no_tool":
            spoken = "好，我们按你的节奏来。目的地确定了吗？"
            if emotion == "silent":
                spoken = "好，我不多问。需要时你叫我就行。"
        elif call_type == "safety":
            spoken = "我有点担心你现在的状态。我们先找个安全的地方停一下，好吗？"
        elif call_type == "explicit" and "entertainment" in caps:
            spoken = "好，接着放你上次那张专辑。"
        elif call_type == "explicit":
            spoken = "好，按你说的来。"
        elif emotion in {"urgency", "lateness"} and "navigation" in caps:
            spoken = "时间有点紧。我先帮你启动比较稳的那条路线，可以吗？"
        elif emotion == "hot_discomfort" and "climate" in caps:
            spoken = "车里确实有点闷热。我帮你把温度调舒服一点，可以吗？"
        elif emotion in {"fatigue", "silent", "low_mood"}:
            spoken = "听起来今天挺累的。要不要我先把座舱调得柔和一点？"
        elif "climate" in caps:
            spoken = "我先帮你调一下空调，好吗？"
        elif "navigation" in caps:
            spoken = "要我帮你打开去目的地的导航吗？"
        else:
            spoken = "我在这儿。需要的话跟我说一声。"
    else:
        if call_type == "no_tool":
            spoken = "目的地还没定。你要去哪里？"
        elif call_type == "safety":
            spoken = "检测到较高疲劳风险。建议立即前往最近安全停车点，我已准备导航。"
        elif call_type == "explicit" and "entertainment" in caps:
            spoken = "收到，正在播放你上次那张专辑。"
        elif call_type == "explicit":
            spoken = "收到，开始执行。"
        elif emotion in {"urgency", "lateness"} and "navigation" in caps:
            spoken = "时间紧。已规划最快稳定路线，现在启动。"
        elif emotion == "hot_discomfort" and "climate" in caps:
            spoken = "舱温偏高，正在恢复你的常用温度。"
        elif "navigation" in caps and "climate" in caps:
            spoken = "已同步导航与空调恢复，减少后续操作。"
        elif "navigation" in caps:
            spoken = "已规划前往目的地，并同步关键行程信息。"
        elif "climate" in caps:
            spoken = "正在调节空调至舒适温度。"
        elif "notification_mode" in caps:
            spoken = "已切换通知模式，降低干扰。"
        else:
            spoken = "已为你整理出发事项。"

    return {
        "thinking_narrative": thinking,
        "action_plan": action_plan,
        "spoken_reply": spoken,
        "scenario_description": action_plan,
        "assistant_response": spoken,
    }


class ResponseGenerator:
    def __init__(self, client: Optional[LLMClient] = None, use_llm: bool = True):
        self.client = client or LLMClient()
        self.use_llm = use_llm
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    def generate(
        self,
        persona_id: str,
        parsed: Mapping[str, Any],
        composed: Mapping[str, Any],
        interaction_snippets: list[str] | None = None,
    ) -> Dict[str, str]:
        snippets = interaction_snippets or []
        fallback = _template_response(persona_id, parsed, composed, snippets)
        if not self.use_llm:
            return fallback

        tool_results = []
        for step in _tool_steps(composed):
            tool_results.append(
                {
                    "capability": step.get("capability"),
                    "capability_cn": CAP_CN.get(step.get("capability"), step.get("capability")),
                    "execution_mode": step.get("execution_mode"),
                    "execution_mode_cn": MODE_CN.get(step.get("execution_mode", ""), ""),
                    "intensity": step.get("intensity"),
                    "intensity_cn": INTENSITY_CN.get(step.get("intensity", ""), ""),
                    "parameters": step.get("parameters"),
                    "mock_result": step.get("mock_result"),
                    "step": step.get("step"),
                }
            )
        payload = {
            "persona": persona_id,
            "ordered_steps_hint": _ordered_action_text(composed),
            "recognized_state": {
                "emotion": parsed.get("emotion"),
                "needs": parsed.get("needs"),
                "risk": parsed.get("risk"),
            },
            "activated_strategy": composed.get("persona_activation"),
            "behavior_call_type": composed.get("behavior_call_type"),
            "selected_capabilities": composed.get("selected_capabilities"),
            "behavior_sequence": [
                {k: v for k, v in s.items() if k != "mock_result"}
                for s in (composed.get("behavior_sequence") or [])
            ],
            "tool_results": tool_results,
            "interaction_hints": snippets[:4],
            "rule_hint": "若 behavior_call_type 为 safety/explicit/no_tool，请在 thinking_narrative 明确写出规则优先原因。",
        }
        try:
            content = self.client.chat(
                [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.55,
                max_tokens=900,
            )
            obj = extract_json_object(content)
            if not obj:
                return fallback
            thinking = str(obj.get("thinking_narrative") or "").strip()
            action_plan = str(
                obj.get("action_plan") or obj.get("scenario_description") or ""
            ).strip()
            spoken = str(
                obj.get("spoken_reply") or obj.get("assistant_response") or ""
            ).strip()
            if not thinking or not action_plan or not spoken:
                # partial fill from fallback
                thinking = thinking or fallback["thinking_narrative"]
                action_plan = action_plan or fallback["action_plan"]
                spoken = spoken or fallback["spoken_reply"]
            return {
                "thinking_narrative": thinking,
                "action_plan": action_plan,
                "spoken_reply": spoken,
                "scenario_description": action_plan,
                "assistant_response": spoken,
            }
        except Exception:
            return fallback
