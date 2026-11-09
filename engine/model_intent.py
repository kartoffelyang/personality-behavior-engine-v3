from __future__ import annotations

import re
import math
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .context_parser import ContextParser, _rule_fallback, extract_explicit_request
from .llm_router import ChatBackend, LLMRequestError, chat_json_with_raw, describe_llm_error, dump_json, model_version_label
from .registries import ScenarioRegistry
from .interaction_policy import dynamic_dimension_catalog, validate_activations

SCENARIO_KEYWORDS: List[tuple[str, List[str]]] = [
    ("fatigue_driving", ["疲劳", "困", "打哈欠", "继续开", "犯困", "疲惫驾驶"]),
    ("emotional_support", ["难受", "心情", "安慰", "委屈", "情绪", "低落", "累死了", "烦"]),
    ("temperature_control", ["太热", "太冷", "空调", "温度", "热死", "冷死"]),
    ("cabin_comfort", ["座椅", "舒适", "按摩", "腰酸", "不舒服"]),
    ("entertainment_relax", ["音乐", "放松", "听听歌", "娱乐", "播放"]),
    ("time_pressure", ["开会", "迟到", "赶时间", "时间紧", "来不及", "二十分钟"]),
    ("festival_celebration", ["生日", "节日", "庆祝", "快乐"]),
    ("pleasant_trip", ["兜风", "出游", "旅行", "愉快", "轻松出行"]),
    ("welcome_on_board", ["上车", "刚上车", "出发", "走吧", "早上好", "回家"]),
]

INTENT_SYSTEM = (Path(__file__).resolve().parents[1] / "prompts" / "intent_system.txt").read_text(encoding="utf-8")


def current_intent_system() -> str:
    return (Path(__file__).resolve().parents[1] / "prompts" / "intent_system.txt").read_text(encoding="utf-8")


def infer_scenario_id(text: str, default: str = "welcome_on_board") -> str:
    utt = text or ""
    # A stated deadline takes precedence over incidental comfort/greeting words.
    if re.search(r"迟到|赶时间|时间紧|来不及|赶到|(?:[零一二三四五六七八九十两\d]+点|\d{1,2}:\d{2}).{0,4}(?:前|之前)", utt):
        return "time_pressure"
    best = default
    best_hits = 0
    for sid, kws in SCENARIO_KEYWORDS:
        hits = sum(1 for k in kws if k in utt)
        if hits > best_hits:
            best_hits = hits
            best = sid
    return best


def _apply_overrides(base: Dict[str, Any], overrides: Mapping[str, Any] | None) -> Dict[str, Any]:
    out = dict(base)
    if not overrides:
        return out
    for k, v in overrides.items():
        if v is None or v == "":
            continue
        out[k] = v
    # Keep the public Step 2 schema intact; adapt names only for downstream context.
    if overrides.get("cabin_temperature_c") is not None:
        out["cabin_temperature"] = overrides["cabin_temperature_c"]
    if overrides.get("destination_reference"):
        reference = overrides["destination_reference"]
        if reference != base.get("destination"):
            out["destination_known"] = False
            out["estimated_trip_minutes"] = None
        out["destination"] = reference
    if overrides.get("arrival_deadline"):
        out["calendar_pressure"] = True
    return out


def _normalize_numeric_context(raw: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Convert only unambiguous numeric strings; retain the untouched raw reply separately."""
    normalized = deepcopy(raw)
    corrections = []
    overrides = normalized.get("context")
    if not isinstance(overrides, dict):
        return normalized, corrections
    for field in ("passenger_count", "cabin_temperature_c"):
        original = overrides.get(field)
        if not isinstance(original, str):
            continue
        try:
            number = json.loads(original.strip())
        except (ValueError, TypeError):
            continue
        if field == "passenger_count":
            valid = type(number) is int and number >= 0
        else:
            valid = type(number) in (int, float) and math.isfinite(number)
        if valid:
            overrides[field] = number
            corrections.append({"field": f"context.{field}", "before": original,
                                "after": number, "reason": "numeric_string_to_number"})
    return normalized, corrections


def _validate_intent_output(raw: Dict[str, Any], catalog: List[str]) -> None:
    """Reject malformed model output rather than silently repairing its judgments."""
    task_keys = "core_goal explicit_intents optional_goals constraints must_do".split()
    context_keys = "time_period cabin_temperature_c thermal_sensation passenger_count destination_reference arrival_deadline driving_state weather".split()
    need_keys = "emotional_comfort physical_comfort task_completion time_efficiency safety_support restore_history user_autonomy".split()
    risk_keys = "fatigue_driving distraction visibility time_pressure overall".split()

    def keys(value, expected):
        if not isinstance(value, dict) or set(value) != set(expected):
            raise ValueError("Step 2 JSON fields do not match the required schema")

    def score(value):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Step 2 score must be a finite number from 0 to 1")

    def strings(value):
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise ValueError("Step 2 expected a list of strings")

    keys(raw, ["scenario_id", "task", "facts", "unknowns", "context", "needs", "emotion", "risk", "activated_interaction_dimensions"])
    validate_activations(raw["activated_interaction_dimensions"])
    if not isinstance(raw["scenario_id"], str) or raw["scenario_id"] not in catalog:
        raise ValueError("Step 2 invalid scenario_id")
    keys(raw["task"], task_keys)
    if not isinstance(raw["task"]["core_goal"], str):
        raise ValueError("Step 2 invalid task.core_goal")
    for k in task_keys[1:]:
        strings(raw["task"][k])
    strings(raw["unknowns"])
    keys(raw["context"], context_keys)
    for k, v in raw["context"].items():
        if v is None:
            continue
        valid = isinstance(v, str)
        if k == "cabin_temperature_c":
            valid = type(v) in (int, float) and math.isfinite(v)
        elif k == "passenger_count":
            valid = type(v) is int and v >= 0
        if not valid:
            raise ValueError(f"Step 2 invalid context.{k}")
    keys(raw["needs"], need_keys)
    keys(raw["risk"], risk_keys + ["items"])
    strings(raw["risk"]["items"])
    for v in raw["needs"].values():
        score(v)
    for k in risk_keys:
        score(raw["risk"][k])
    keys(raw["emotion"], ["label", "intensity"])
    if not isinstance(raw["emotion"]["label"], str):
        raise ValueError("Step 2 invalid emotion label")
    score(raw["emotion"]["intensity"])
    if not isinstance(raw["facts"], list):
        raise ValueError("Step 2 invalid facts")
    for fact in raw["facts"]:
        keys(fact, ["category", "fact", "source", "confidence"])
        if fact["category"] not in ("user", "task", "physical", "social"):
            raise ValueError("Step 2 invalid fact category")
        if fact["source"] not in ("utterance", "provided_context") or not isinstance(fact["fact"], str):
            raise ValueError("Step 2 invalid fact source or text")
        score(fact["confidence"])


def run_intent(
    request: Mapping[str, Any],
    llm_backend: str,
    client: Optional[ChatBackend],
    scenarios: ScenarioRegistry | None = None,
    prompt_override: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    """MODEL1: intent + context understanding. Returns audit-ready dict + parsed_context + scenario_id."""
    scenarios = scenarios or ScenarioRegistry()
    utterance = request.get("user_utterance") or ""
    persona = request.get("persona")
    catalog = sorted(scenarios.ids())
    model_ver = model_version_label(llm_backend, client)

    if client is None:
        scenario_id = infer_scenario_id(utterance, request.get("scenario_id") or "welcome_on_board")
        if scenario_id not in catalog:
            scenario_id = "welcome_on_board"
        # lightly enrich context from keywords before rule parse
        ctx = dict(request.get("context") or {})
        overrides: Dict[str, Any] = {}
        if re.search(r"早上|清晨|早晨", utterance):
            overrides["time_period"] = "morning"
        if re.search(r"晚上|夜里|夜间", utterance):
            overrides["time_period"] = "night"
        if re.search(r"车里.{0,6}(?:凉|冷)|太冷", utterance):
            overrides["thermal_sensation"] = "偏冷"
        if re.search(r"车里.{0,6}热|太热", utterance):
            overrides["thermal_sensation"] = "偏热"
        if re.search(r"女儿|孩子|儿童|乘客", utterance):
            overrides["passenger_count"] = 2
        if re.search(r"疲劳|困|打哈欠|继续开", utterance):
            overrides["fatigue_risk"] = 0.85
            overrides["driving_state"] = "driving"
        if re.search(r"开会|迟到|会议", utterance):
            overrides["calendar_pressure"] = True
            overrides["destination_known"] = True
            if not ctx.get("destination"):
                overrides["destination"] = "meeting"
        if re.search(r"生日|节日", utterance):
            overrides["is_festival"] = True
        if re.search(r"回家", utterance):
            overrides["destination_known"] = True
            overrides.setdefault("destination", "home")
        if re.search(r"学校|接女儿|接孩子", utterance):
            overrides["destination_known"] = True
            overrides["destination"] = "school"
        destination_match = re.search(r"(?:去|到|回)([^，。]{2,20}?)(?:见|开会|上班|，|。|$)", utterance)
        if destination_match and destination_match.group(1) not in {"去", "家"}:
            overrides["destination"] = destination_match.group(1)
            overrides["destination_known"] = False
        deadline_match = re.search(r"((?:今天|明天)?(?:[零一二三四五六七八九十两\d]+点|\d{1,2}:\d{2})(?:前|之前))", utterance)
        if deadline_match:
            overrides["arrival_deadline"] = deadline_match.group(1)
            overrides["calendar_pressure"] = True
        if re.search(r"小雨", utterance):
            overrides["weather"] = "light_rain"
        elif re.search(r"下雨|雨天", utterance):
            overrides["weather"] = "rain"
        if re.search(r"车里就我一个人|车内只有我|独自在车", utterance):
            overrides["passenger_count"] = 1
        ctx = _apply_overrides(ctx, overrides)
        req2 = dict(request)
        req2["context"] = ctx
        req2["scenario_id"] = scenario_id
        parsed = _rule_fallback(req2)
        public_context = {
            "time_period": overrides.get("time_period"),
            "cabin_temperature_c": None,
            "thermal_sensation": overrides.get("thermal_sensation"),
            "passenger_count": overrides.get("passenger_count"),
            "destination_reference": overrides.get("destination"),
            "arrival_deadline": overrides.get("arrival_deadline"),
            "driving_state": overrides.get("driving_state"),
            "weather": overrides.get("weather"),
        }
        need_keys = (
            "emotional_comfort", "physical_comfort", "task_completion", "time_efficiency",
            "safety_support", "restore_history", "user_autonomy",
        )
        needs = {key: float((parsed.get("needs") or {}).get(key) or 0.0) for key in need_keys}
        parsed_risk = parsed.get("risk") or {}
        output = {
            "scenario_id": scenario_id,
            "task": {
                "core_goal": utterance or "等待用户提供具体情境",
                "explicit_intents": [utterance] if utterance else [],
                "optional_goals": [],
                "constraints": [],
                "must_do": ["保持驾驶安全"] if float(parsed_risk.get("overall") or 0) >= 0.7 else [],
            },
            "facts": ([{"category": "task", "fact": utterance, "source": "utterance", "confidence": 0.7}] if utterance else []),
            "unknowns": [],
            "activated_interaction_dimensions": [],
            "context": public_context,
            "needs": needs,
            "emotion": parsed.get("emotion"),
            "risk": {
                "items": [],
                "fatigue_driving": float(parsed_risk.get("fatigue_driving") or 0.0),
                "distraction": float(parsed_risk.get("distraction") or 0.0),
                "visibility": float(parsed_risk.get("visibility") or 0.0),
                "time_pressure": float(parsed_risk.get("time_pressure") or 0.0),
                "overall": float(parsed_risk.get("overall") or 0.0),
            },
        }
        return {
            "scenario_id": scenario_id,
            "parsed_context": parsed,
            "context": ctx,
            "model_version": model_ver,
            "fallback": True,
            "output": output,
            "input": {"persona": persona, "user_utterance": utterance, "allowed_scenarios": catalog},
        }

    # LLM path. Explicitly supplied history is evidence, never a fabricated
    # default or an instruction to restore preferences automatically.
    provided_context = dict(request.get("context") or {})
    if request.get("history"):
        provided_context["history"] = request["history"]
    user_payload = {
        "persona": persona,
        "persona_handling": "仅在流程中透传；本步的事实、目标、需求、风险和强度判断不得受人格影响。",
        "user_utterance": utterance,
        "provided_context": provided_context,
        "context_rules": [
            "从user_utterance提取本次任务；scenario_id独立判断，不沿用测试用例的标签。",
            "provided_context只包含用户、传感器或上游业务明确提供的已有上下文，可能为空；不得补常见默认值，也不得抄入输出context。",
            "原话明确的信息优先于provided_context中的同字段信息；只有原话明确温度数值时才填写context.cabin_temperature_c。",
            "主观冷热写入context.thermal_sensation；前挡发白的原因和实际视野影响需保持不确定。",
            "目的地名称与已确认的具体地点分开；截止时间不等于已确定迟到。",
            "不把未提及的信息当成已知，也不把有条件的次要愿望变成无条件任务。",
        ],
        "allowed_scenarios": catalog,
        "interaction_dimension_candidates": dynamic_dimension_catalog(),
    }
    system_prompt = (prompt_override or {}).get("system_prompt") or current_intent_system()
    user_prompt = (prompt_override or {}).get("user_prompt") or dump_json(user_payload)
    raw_text = None
    corrections = []
    try:
        # Receive incremental output; parse and validate only the complete JSON.
        if hasattr(client, "stream"):
            client.stream = True
        raw, raw_text = chat_json_with_raw(
            client,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=3600,
        )
        raw, corrections = _normalize_numeric_context(raw)
        try:
            _validate_intent_output(raw, catalog)
        except ValueError as exc:
            raise LLMRequestError(
                category="schema_validation_error",
                message=f"Step 2 输出格式校验失败：{exc}",
                hint="模型已返回结果，但字段或数值不符合要求。请查看原始返回和提示词中的字段定义。",
                retryable=False,
            ) from exc
        scenario_id = raw.get("scenario_id") or infer_scenario_id(utterance)
        if scenario_id not in catalog:
            scenario_id = infer_scenario_id(utterance)
        overrides = raw.get("context") or {}
        ctx = _apply_overrides(dict(request.get("context") or {}), overrides)
        # build parsed_context compatible with downstream
        parser = ContextParser(use_llm=False)
        req2 = dict(request)
        req2["context"] = ctx
        req2["scenario_id"] = scenario_id
        parsed = parser.parse(req2)
        # The authoritative task constraints must agree with the legacy shortcut.
        parsed["explicit_request"] = extract_explicit_request(utterance, raw["task"])
        # overlay LLM judgments
        if isinstance(raw.get("needs"), dict) and raw["needs"]:
            parsed["needs"] = {**parsed.get("needs", {}), **raw["needs"]}
        if isinstance(raw.get("emotion"), dict) and raw["emotion"]:
            parsed["emotion"] = raw["emotion"]
        if isinstance(raw.get("risk"), dict) and raw["risk"]:
            parsed["risk"] = raw["risk"]
        facts = raw.get("facts") or []
        if facts:
            parsed["confidence"] = sum(float(item["confidence"]) for item in facts) / len(facts)
            parsed["evidence"] = [
                {"source": item["source"], "fact": item["fact"], "confidence": item["confidence"]}
                for item in facts
            ]
        parsed["parser_fallback"] = False
        parsed["llm_intent"] = True
        return {
            "scenario_id": scenario_id,
            "parsed_context": parsed,
            "context": ctx,
            "model_version": model_ver,
            "fallback": False,
            "output": raw,
            "input": user_payload,
            "prompt_used": {"system_prompt": system_prompt, "user_prompt": user_prompt},
            "raw_llm_text": raw_text,
            "normalization_corrections": corrections,
        }
    except Exception as exc:
        # fall back to rules
        err_info = describe_llm_error(exc)
        fb = run_intent(request, "rules", None, scenarios)
        fb["model_version"] = model_ver
        fb["fallback"] = True
        fb["error"] = str(exc)
        fb["error_info"] = err_info
        fb["prompt_used"] = {"system_prompt": system_prompt, "user_prompt": user_prompt}
        fb["input"] = user_payload
        fb["normalization_corrections"] = corrections
        if raw_text is not None:
            fb["raw_llm_text"] = raw_text
        return fb
