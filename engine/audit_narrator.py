from __future__ import annotations

from typing import Any, Dict, List, Mapping


def _lines(items: List[str]) -> str:
    return "\n".join(f"· {x}" for x in items if x)


def _persona_label(value: Any) -> str:
    if str(value or "").upper() == "NO_MBTI":
        return "无 MBTI 基线"
    return str(value or "—")


def describe_step(step: Mapping[str, Any]) -> Dict[str, str]:
    """Turn audit step I/O into natural-language demo text (no raw JSON dump)."""
    n = int(step.get("step") or 0)
    out = step.get("output") or {}
    inp = step.get("input") or {}
    rules = step.get("rules_hit") or []

    if n == 1:
        return {
            "input_text": f"接收模式「{_persona_label(inp.get('persona') or out.get('persona'))}」与情境原文。",
            "output_text": _lines(
                [
                    f"模式：{_persona_label(out.get('persona'))}",
                    f"情境原文：{out.get('utterance') or '（空）'}",
                    f"运行编号：{out.get('run_id')}",
                    f"后端/模型：{(out.get('versions') or {}).get('llm_backend')} / {(out.get('versions') or {}).get('model')}",
                    f"能力版本：{(out.get('versions') or {}).get('capabilities_version')}",
                    f"规则版本：{(out.get('versions') or {}).get('rules_version')}",
                    "未提供的信息保持未知，不要求用户填写固定字段。",
                ]
            ),
        }

    if n == 2:
        task = out.get("task") or {}
        facts = out.get("facts") or []
        return {
            "input_text": f"人格与开放情境进入意图识别（允许场景共 {len(inp.get('allowed_scenarios') or [])} 类）。",
            "output_text": _lines(
                [
                    f"激活场景：{out.get('scenario_id')}",
                    f"核心目标：{task.get('core_goal') or out.get('core_goal')}",
                    f"明确目标：{'、'.join(task.get('explicit_intents') or []) or out.get('explicit_intent') or '—'}",
                    f"可选目标：{'、'.join(task.get('optional_goals') or out.get('implicit_needs') or []) or '—'}",
                    f"约束：{'、'.join(task.get('constraints') or []) or '—'}",
                    f"Must-do：{'、'.join(task.get('must_do') or out.get('must_do') or []) or '—'}",
                    f"关键事实：{'；'.join([str(x.get('fact')) for x in facts][:8]) or '—'}",
                    f"未知信息：{'；'.join(out.get('unknowns') or []) or '未提及字段保持未知'}",
                    f"风险：{out.get('risks') or out.get('risk')}",
                ]
            ),
        }

    if n == 3:
        if "persona_description" in out:
            lines = [f"人格描述：{out['persona_description']}"]
            if out.get("dominant"):
                lines.extend([
                    f"主导功能：{out['dominant']}（{out['dominant_name']}）——{out['dominant_description']}",
                    f"判断方式：{out['judgment_style']}",
                ])
            return {"input_text": f"当前模式：{_persona_label(inp.get('persona'))}",
                    "output_text": _lines(lines)}
        return {
            "input_text": f"当前模式：{_persona_label(inp.get('persona'))}",
            "output_text": _lines(
                [
                    f"功能序列：{out.get('sequence')}",
                    f"主导：{out.get('dominant')}｜辅助：{out.get('auxiliary')}｜第三：{out.get('tertiary')}｜劣势：{out.get('inferior')}",
                ]
            ),
        }

    if n == 5:
        return {
            "input_text": f"主导功能 {inp.get('dominant_function')} 进入场景 {inp.get('scenario_id')}。",
            "output_text": _lines(
                [
                    f"行动策略：{out.get('action_strategy')}",
                    f"跨场景原型：{out.get('cross_scenario_prototype')}",
                    f"场景内策略：{out.get('scenario_strategy')}",
                    f"优先需求：{'、'.join(out.get('preferred_needs') or []) or '—'}",
                ]
            ),
        }

    if n == 4:
        return {
            "input_text": f"依据主导功能 {inp.get('dominant_function') or '无 MBTI'} 查取基本行动策略。",
            "output_text": _lines(
                [
                    f"基本行动策略：{out.get('action_strategy') or '—'}",
                    f"行动逻辑：{' → '.join(out.get('action_logic') or []) or '—'}",
                ]
            ),
        }

    if n == 6:
        policy = out.get("interaction_policy")
        if isinstance(policy, dict):
            fixed = policy.get("fixed_dimensions") or []
            activated = policy.get("activated_dimensions") or []
            bits = [f"固定维度：{len(fixed)} 项；情境激活：{len(activated)} 项"]
            bits.append(policy.get("compiled_prompt") or "无额外交互偏好，仅按当前情境与基础规则处理。")
            return {
                "input_text": "读取主导功能交互规则，并合并 Step 2 选择的零至四项情境要求。",
                "output_text": _lines(bits),
            }
        scaled = out.get("scaled_1_to_5") or {}
        raw = out.get("raw_interaction") or {}
        dim_desc = out.get("function_dimension_descriptions") or []
        bits = [f"{k}={scaled.get(k)}（原始 {raw.get(k)}）" for k in sorted(scaled.keys())]
        if "option_count" in raw:
            bits.append(f"选项数量：{raw['option_count']} 个（人格偏好，实际数量受情境约束）")
        if out.get("mode") == "scenario_only":
            bits.insert(0, "不使用 MBTI 交互维度，当前只保留情境驱动基线。")
        bits.extend(
            [
                f"{d.get('function')}（主导功能）：立场 {d.get('stance')}｜说明重点 {d.get('explanation_focus')}｜介入时机 {d.get('timing')}"
                for d in dim_desc[:1]
            ]
        )
        return {
            "input_text": "读取人格交互维度初值，仅检索主导功能对应维度。",
            "output_text": _lines(bits or ["（无交互参数）"]),
        }

    if n == 7:
        planner = out.get("planner_output") or {}
        if out.get("generation_status") in {"needs_clarification", "no_tool_needed"}:
            clarification = out.get("generation_status") == "needs_clarification"
            return {
                "input_text": "根据本次情境检查任务与行为编排条件。",
                "output_text": ("需要补充信息：" + "；".join(planner.get("clarification_questions") or [])
                                if clarification else "本次无需工具调用：" + str(planner.get("final_explanation") or "")),
            }
        generation_error = out.get("generation_error") or planner.get("llm_error") or {}
        if out.get("generation_status") == "failed" or step.get("status") == "fallback":
            label = "生成失败/超时" if generation_error.get("category") == "timeout" else "生成失败"
            detail = generation_error.get("message") or "行为序列模型未返回有效结果。"
            return {
                "input_text": f"在场景 {inp.get('scenario_id')} 下生成最终行为序列。",
                "output_text": f"{label}：{detail}\n本次不生成行为序列，也不使用蓝图结果代替。",
            }
        actions = out.get("validated_actions") or []
        plan = out.get("O_Action_Plan") or []
        waves = out.get("waves") or []
        pri = out.get("O_Priority_Plan") or {}
        return {
            "input_text": f"在场景 {inp.get('scenario_id')} 下，综合流程材料和原子能力库生成最终行为序列，并标注功能优先级与调用负担。",
            "output_text": _lines(
                [
                    f"行为调用类型：{out.get('behavior_call_type')}",
                    f"最终选中能力：{'、'.join(out.get('selected_capabilities') or []) or '无'}",
                    f"动作数：{len(actions)}",
                    f"功能优先级 T1：{len(pri.get('T1') or [])}｜T2：{len(pri.get('T2') or [])}｜T3：{len(pri.get('T3') or [])}",
                    f"波次：{len(waves)}（可并行波次用同波多动作表示）",
                    f"优先级序列：{' → '.join(plan[:6]) or '无工具调用'}" + ("…" if len(plan) > 6 else ""),
                    f"planner notes：{'；'.join(planner.get('notes') or []) or '—'}",
                    f"校正记录：{'；'.join(out.get('validation_fixes') or []) or '无'}",
                    f"规则命中：{'；'.join(rules[:10]) or '—'}",
                ]
            ),
        }

    return {
        "input_text": str(inp)[:500] if inp else "—",
        "output_text": str(out)[:800] if out else "—",
    }


def enrich_audit_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched = []
    for s in steps:
        d = dict(s)
        narr = describe_step(d)
        d["input_text"] = narr["input_text"]
        d["output_text"] = narr["output_text"]
        enriched.append(d)
    return enriched
