from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from .persona_attribution import classify_influence, has_declared_influence
from .output_builder import CAP_CN, O_ACTION_PLAN_MODE_CN
from .priority_burden import BURDEN_CN, waves_summary

PERSONA_COLOR = {
    "ESTJ": "estj",  # blue
    "INFP": "infp",  # green
    "NO_MBTI": "baseline",
}


def build_gantt(actions: Sequence[Mapping[str, Any]], *, persona_id: str = "") -> Dict[str, Any]:
    """Swimlane Gantt-like structure for UI rendering."""
    persona = str(persona_id or "").upper()
    color = PERSONA_COLOR.get(persona, "baseline")
    caps_order = list(CAP_CN.keys())
    max_wave = max((int(a.get("wave") or 1) for a in actions), default=1)

    # lanes by capability domain
    lanes = []
    for cid in caps_order:
        bars = []
        for a in actions:
            if a.get("capability") != cid:
                continue
            persona_touched = has_declared_influence(a, persona)
            bars.append(
                {
                    "wave": int(a.get("wave") or 1),
                    "order": a.get("order"),
                    "priority": a.get("priority") or "T2",
                    "burden": a.get("burden") or "medium",
                    "burden_label": a.get("burden_label") or BURDEN_CN.get(str(a.get("burden")), "中"),
                    "name": a.get("name") or a.get("action") or CAP_CN.get(cid, cid),
                    "execution_mode": a.get("execution_mode"),
                    "persona_highlight": persona_touched and persona in {"ESTJ", "INFP"},
                    "highlight_class": color if persona_touched and persona in {"ESTJ", "INFP"} else "",
                    "details": a.get("details") or "",
                    "persona_influence": a.get("persona_influence") or "",
                    "scenario_influence": a.get("scenario_influence") or "",
                }
            )
        if bars:
            lanes.append(
                {
                    "capability": cid,
                    "display_name": CAP_CN.get(cid, cid),
                    "bars": bars,
                }
            )

    return {
        "persona": persona,
        "color_scheme": color,
        "wave_count": max_wave,
        "waves": waves_summary(list(actions)),
        "lanes": lanes,
        "legend": {
            "T1": "最高优先级（先做）",
            "T2": "次优先级",
            "T3": "可后置",
            "burden": "低/中可同波并行；高负担独占波次；同能力域不并行",
            "highlight": "蓝=ESTJ 人格影响差异；绿=INFP 人格影响差异",
        },
    }


def _mode_cn(mode: str) -> str:
    return O_ACTION_PLAN_MODE_CN.get(mode, mode or "未知")


def _highlight(text: str, persona_id: str, *, enabled: bool) -> str:
    if not enabled or not text:
        return text
    cls = PERSONA_COLOR.get(str(persona_id).upper(), "")
    if cls not in {"estj", "infp"}:
        return text
    return f'<mark class="hl-{cls}">{text}</mark>'


def build_scene_story(
    *,
    persona_id: str,
    scenario_id: str,
    utterance: str,
    actions: Sequence[Mapping[str, Any]],
    intent_summary: str = "",
) -> Dict[str, Any]:
    """Scene story with optional HTML highlights for persona-influenced bits."""
    persona = str(persona_id or "").upper()
    lines_plain: List[str] = []
    lines_html: List[str] = []

    if persona == "NO_MBTI":
        opener = (
            f"冬季出行基线：系统按情境「{scenario_id}」依次响应需求，"
            f"不做人格偏好改写。用户表述：{utterance or '（无）'}。"
        )
    elif persona == "ESTJ":
        opener = (
            f"ESTJ 场景故事：在情境「{scenario_id}」下，优先把任务推进到可执行结果，"
            f"减少重复确认。用户表述：{utterance or '（无）'}。"
        )
    else:
        opener = (
            f"INFP 场景故事：在情境「{scenario_id}」下，先照顾当下感受与自主选择，"
            f"再逐步展开任务。用户表述：{utterance or '（无）'}。"
        )
    lines_plain.append(opener)
    lines_html.append(opener)
    if intent_summary:
        lines_plain.append(f"情境理解：{intent_summary}")
        lines_html.append(f"情境理解：{intent_summary}")

    by_wave: Dict[int, List[Mapping[str, Any]]] = {}
    for a in actions:
        by_wave.setdefault(int(a.get("wave") or 1), []).append(a)

    if not by_wave:
        empty = "本轮无工具调用，保持低干扰待命。"
        return {
            "persona": persona,
            "title": f"{persona or '未知'}场景故事",
            "plain": opener + "\n" + empty,
            "html": opener + "<br/>" + empty,
            "paragraphs_plain": [opener, empty],
            "paragraphs_html": [opener, empty],
        }

    for w in sorted(by_wave):
        items = sorted(by_wave[w], key=lambda x: int(x.get("order") or 99))
        parallel = len(items) > 1
        prefix = f"波次 {w}" + ("（并行）" if parallel else "") + "："
        chunk_plain = []
        chunk_html = []
        for a in items:
            dname = a.get("display_name") or CAP_CN.get(str(a.get("capability")), "")
            name = a.get("name") or a.get("action") or ""
            details = str(a.get("details") or "").strip()
            mode = _mode_cn(str(a.get("execution_mode") or ""))
            pr = a.get("priority") or "T2"
            bd = a.get("burden_label") or BURDEN_CN.get(str(a.get("burden")), "中")
            persona_inf = str(a.get("persona_influence") or "").strip()
            touched = classify_influence(a, persona)["status"] == "grounded"

            core = f"【{dname}】{name}"
            if details:
                detail_bit = f"执行内容：{details}"
                mode_bit = f"执行方式：{mode}"
                if touched:
                    # highlight persona-influenced execution content or mode
                    detail_h = _highlight(detail_bit, persona, enabled=True)
                    mode_h = _highlight(mode_bit, persona, enabled=True)
                    body_plain = f"{core}（{detail_bit}；{mode_bit}）"
                    body_html = f"{core}（{detail_h}；{mode_h}）"
                else:
                    body_plain = f"{core}（{detail_bit}；{mode_bit}）"
                    body_html = body_plain
            else:
                body_plain = f"{core}（执行方式：{mode}）"
                body_html = (
                    f"{core}（{_highlight(f'执行方式：{mode}', persona, enabled=touched)}）"
                    if touched
                    else body_plain
                )

            meta = f"[{pr}｜负担{bd}]"
            chunk_plain.append(f"{meta} {body_plain}")
            chunk_html.append(f"{meta} {body_html}")

        joiner = "；同时 " if parallel else "；接着 "
        para_plain = prefix + joiner.join(chunk_plain)
        para_html = prefix + joiner.join(chunk_html)
        lines_plain.append(para_plain)
        lines_html.append(para_html)

    if persona == "ESTJ":
        closer = "整体节奏偏效率：核心行程先闭环，干扰类能力后置或并行收束。"
    elif persona == "INFP":
        closer = "整体节奏偏陪伴：关键选择留给用户，舒适与情绪相关动作更早出现。"
    else:
        closer = "基线按固定线性顺序推进，每步等待确认后再继续。"
    lines_plain.append(closer)
    lines_html.append(closer)

    return {
        "persona": persona,
        "title": f"{'无 MBTI 基线' if persona == 'NO_MBTI' else persona}场景故事",
        "plain": "\n".join(lines_plain),
        "html": "<br/>".join(lines_html),
        "paragraphs_plain": lines_plain,
        "paragraphs_html": lines_html,
    }


def build_step8_viz(
    *,
    persona_id: str,
    scenario_id: str,
    utterance: str,
    actions: Sequence[Mapping[str, Any]],
    intent_summary: str = "",
) -> Dict[str, Any]:
    gantt = build_gantt(actions, persona_id=persona_id)
    story = build_scene_story(
        persona_id=persona_id,
        scenario_id=scenario_id,
        utterance=utterance,
        actions=actions,
        intent_summary=intent_summary,
    )
    return {"gantt": gantt, "scene_story": story}
