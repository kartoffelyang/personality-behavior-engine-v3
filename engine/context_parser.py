from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .llm_client import LLMClient, extract_json_object
from .utils import clamp01

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "context_parser.txt"


DOMAIN_TERMS = {
    "entertainment": r"专辑|音乐|歌曲|听歌|播放|播客",
    "climate": r"空调|调温|温度|温控|暖风|冷风|制冷|制热|除雾",
    "navigation": r"导航|路线|带我去|带我回|回家|目的地",
    "ambient_light": r"氛围灯|灯氛围|灯光",
    "seat": r"座椅|按摩",
    "notification_mode": r"勿扰|专注模式|通知",
}

# Only direct, locally bound commands may take the legacy explicit-command path.
# Ambiguous/conditional language remains in the task for the planner to resolve.
COMMAND_PATTERNS = {
    "entertainment": r"(?:播放|放|听).{0,20}(?:专辑|音乐|歌曲|歌|播客)",
    "climate": r"(?:(?:打开|开启|开).{0,6}空调|(?:把)?空调.{0,10}(?:打开|开启|调到|设为)|调温到)",
    "navigation": r"(?:导航到|导航去|带我去|带我回|打开导航).+",
    "ambient_light": r"(?:(?:打开|开启|切换到).{0,10}(?:氛围灯|灯光)|(?:把)?(?:氛围灯|灯光).{0,10}(?:打开|开启|设为|切换到))",
    "seat": r"(?:(?:打开|开启).{0,8}(?:座椅|按摩)|(?:把)?座椅.{0,10}(?:打开|开启|调到|设为))",
    "notification_mode": r"(?:(?:打开|开启|设为|切换到).{0,8}(?:勿扰|专注模式|通知)|(?:把)?通知.{0,8}(?:设为|切换到))",
}
NEGATION = re.compile(r"不要|不用|无需|不必|不得|禁止|不允许|不想|不准|不能|暂缓|(?<!特)别|勿(?!扰)")
CONDITION = re.compile(
    r"如果|要是|假如|除非|只有|若|的话|之后|之前|以后|最好|等.{0,20}(?:再|才|后)"
    r"|(?:需|先|等|待).{0,8}确认|确认.{0,12}(?:后|再)|经.{0,8}(?:同意|授权)"
    r"|.{1,15}(?:再|才)(?:打开|开启|播放|调到|导航)"
)
COMMAND_PREFIX = re.compile(r"^(?:(?:请你|请|帮我|给我|麻烦你|麻烦|现在|立即|马上|先|就|再|你|能不能|可以)(?:\s*))*")


def extract_explicit_request(utterance: str, task: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Conservatively identify an unconditional direct command, never an authorization.

    A condition scopes over its sentence, including comma-separated consequent
    clauses. Conflicting commands in the same domain are left to task planning.
    The successful Step 2 task can veto this legacy shortcut; it cannot create a
    command that is absent from the original utterance.
    """
    empty = {"exists": False, "capability": None, "parameters": {}}
    clauses = []
    blocked = set()
    for sentence in re.split(r"[。.!！？?；;\n]+", utterance or ""):
        conditional = bool(CONDITION.search(sentence))
        for part in re.split(r"[,，]|但是|不过|而是|但|并且|同时|然后", sentence):
            clause = part.strip()
            if not clause:
                continue
            restricted = conditional or bool(NEGATION.search(clause))
            if restricted:
                mentioned = {cap for cap, terms in DOMAIN_TERMS.items() if re.search(terms, clause)}
                blocked.update(mentioned)
                if not mentioned and NEGATION.search(clause) and re.search(r"操作|执行|自动|照做|指令|^(?:不要|别|禁止)$", clause):
                    blocked.update(DOMAIN_TERMS)
            clauses.append((clause, restricted))

    if task is not None:
        # Absence of a model-extracted explicit goal is not permission to infer one.
        if not task.get("explicit_intents"):
            return empty
        for field in ("constraints", "optional_goals", "explicit_intents"):
            for text in task.get(field) or []:
                if not isinstance(text, str):
                    continue
                restricted = field == "optional_goals" or NEGATION.search(text) or CONDITION.search(text)
                if not restricted:
                    continue
                mentioned = {cap for cap, terms in DOMAIN_TERMS.items() if re.search(terms, text)}
                if mentioned:
                    blocked.update(mentioned)
                elif re.search(r"操作|执行|自动|确认|允许|同意|指令", text):
                    # A global confirmation/operation constraint applies to all tools.
                    blocked.update(DOMAIN_TERMS)

    for clause, restricted in clauses:
        if restricted:
            continue
        command = COMMAND_PREFIX.sub("", clause)
        for cap, pattern in COMMAND_PATTERNS.items():
            if cap in blocked or not re.match(pattern, command):
                continue
            parameters = {}
            if cap == "entertainment" and "专辑" in command and "上次" in command:
                parameters["album"] = "last_played"
            return {"exists": True, "capability": cap, "parameters": parameters}
    return empty


def _rule_fallback(request: Mapping[str, Any]) -> Dict[str, Any]:
    utterance = (request.get("user_utterance") or "").strip()
    utterance_norm = utterance.strip("。.!！？?")
    context = request.get("context") or {}
    evidence = []
    if utterance:
        evidence.append({"source": "user_utterance", "fact": f"用户说：{utterance}"})
    if context.get("time_period"):
        evidence.append({"source": "context", "fact": f"时段为{context['time_period']}"})
    if context.get("destination"):
        evidence.append({"source": "context", "fact": f"目的地为{context['destination']}"})

    explicit = extract_explicit_request(utterance)

    emotion = {"label": "neutral", "intensity": 0.2}
    needs = {
        "emotional_comfort": 0.2,
        "physical_comfort": 0.2,
        "task_completion": 0.25,
        "time_efficiency": 0.15,
        "safety_support": 0.15,
        "restore_history": 0.25,
        "user_autonomy": 0.4,
        "group_balance": 0.0,
    }
    risk = {"fatigue_driving": 0.05, "distraction": 0.05, "overall": 0.05}

    if re.search(r"累|疲惫|困|哈欠", utterance):
        emotion = {"label": "fatigue", "intensity": 0.78}
        needs["emotional_comfort"] = 0.82
        needs["physical_comfort"] = 0.58
        needs["task_completion"] = 0.25
        risk["fatigue_driving"] = 0.25
        risk["overall"] = 0.25
    if re.search(r"没事.*继续开|连续打哈欠|疲劳风险", utterance) or float(context.get("fatigue_risk") or 0) >= 0.75:
        emotion = {"label": "fatigue", "intensity": 0.9}
        needs["safety_support"] = 0.95
        needs["emotional_comfort"] = 0.3
        risk["fatigue_driving"] = float(context.get("fatigue_risk") or 0.85)
        risk["overall"] = max(risk["overall"], risk["fatigue_driving"])
    if re.search(r"开会|迟到|二十分钟", utterance):
        emotion = {"label": "urgency", "intensity": 0.85}
        needs["task_completion"] = 0.95
        needs["time_efficiency"] = 0.95
        needs["emotional_comfort"] = 0.15
    if re.search(r"热", utterance):
        emotion = {"label": "hot_discomfort", "intensity": 0.8}
        needs["physical_comfort"] = 0.95
        needs["immediate_feedback"] = 0.9
    if re.search(r"什么都不想说|不想说", utterance):
        emotion = {"label": "silent", "intensity": 0.7}
        needs["emotional_comfort"] = 0.75
        needs["user_autonomy"] = 0.95
        needs["personal_boundary"] = 0.95
        needs["task_completion"] = 0.1
    if utterance_norm in {"走吧", "出发", "走"}:
        emotion = {"label": "ready", "intensity": 0.4}
        if context.get("destination_known"):
            needs["task_completion"] = 0.7
            needs["time_efficiency"] = 0.55
        else:
            needs["task_completion"] = 0.4
            needs["dependency_check"] = 0.7
    if re.search(r"生日|节日|快乐", utterance) or context.get("is_festival"):
        emotion = {"label": "festive", "intensity": 0.6}
        needs["emotional_comfort"] = 0.7
        needs["user_autonomy"] = 0.7
    if int(context.get("passenger_count") or 1) > 1:
        needs["group_balance"] = 0.9
        if re.search(r"冷|热", utterance):
            needs["physical_comfort"] = 0.85

    # morning commute mild / empty greeting
    if (not utterance_norm) or utterance_norm in {"早上好", "嗨", "你好"}:
        if context.get("time_period") == "morning" and context.get("destination_known"):
            needs["task_completion"] = max(needs.get("task_completion", 0), 0.55)
            needs["time_efficiency"] = max(needs.get("time_efficiency", 0), 0.5)
            needs["restore_history"] = max(needs.get("restore_history", 0), 0.45)
            evidence.append({"source": "context", "fact": "工作日早晨上车且目的地已知"})

    # known destination "走吧"
    if utterance_norm in {"走吧", "出发", "走"} and context.get("destination_known"):
        needs["task_completion"] = max(needs.get("task_completion", 0), 0.75)
        needs["time_efficiency"] = max(needs.get("time_efficiency", 0), 0.6)

    if context.get("cabin_temperature") and float(context["cabin_temperature"]) >= 27:
        needs["physical_comfort"] = max(needs.get("physical_comfort", 0), 0.7)
        evidence.append({"source": "context", "fact": f"舱温{context['cabin_temperature']}℃"})

    return {
        "explicit_request": explicit,
        "emotion": emotion,
        "needs": {k: clamp01(v) for k, v in needs.items()},
        "risk": {k: clamp01(v) for k, v in risk.items()},
        "confidence": 0.55,
        "evidence": evidence,
        "parser_fallback": True,
    }


def _validate_parsed(obj: Dict[str, Any]) -> Dict[str, Any]:
    er = obj.get("explicit_request") or {}
    if not isinstance(er, dict):
        er = {"exists": False, "capability": None, "parameters": {}}
    er.setdefault("exists", False)
    er.setdefault("capability", None)
    er.setdefault("parameters", {})
    emotion = obj.get("emotion") or {"label": "neutral", "intensity": 0.2}
    needs = obj.get("needs") or {}
    risk = obj.get("risk") or {"overall": 0.1}
    risk.setdefault("fatigue_driving", 0.0)
    risk.setdefault("distraction", 0.0)
    risk.setdefault("overall", 0.0)
    evidence = obj.get("evidence") or []
    return {
        "explicit_request": er,
        "emotion": {
            "label": str(emotion.get("label", "neutral")),
            "intensity": clamp01(float(emotion.get("intensity", 0.2))),
        },
        "needs": {str(k): clamp01(float(v)) for k, v in needs.items()},
        "risk": {str(k): clamp01(float(v)) for k, v in risk.items()},
        "confidence": clamp01(float(obj.get("confidence", 0.5))),
        "evidence": evidence if isinstance(evidence, list) else [],
        "parser_fallback": False,
    }


class ContextParser:
    def __init__(self, client: Optional[LLMClient] = None, use_llm: bool = True):
        self.client = client or LLMClient()
        self.use_llm = use_llm
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    def parse(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        # Prefer rule fallback enrichment for safety/explicit reliability, then optionally LLM
        fallback = _rule_fallback(request)
        if not self.use_llm:
            return fallback

        user_payload = {
            "persona": request.get("persona"),
            "scenario_id": request.get("scenario_id"),
            "user_utterance": request.get("user_utterance"),
            "context": request.get("context"),
            "history": request.get("history"),
            "available_capabilities": request.get("available_capabilities"),
        }
        try:
            import json

            content = self.client.chat(
                [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                temperature=0.1,
                max_tokens=700,
            )
            obj = extract_json_object(content)
            if not obj:
                return fallback
            parsed = _validate_parsed(obj)
            # hard override: if rule detected high fatigue risk from context, keep it
            if float((fallback.get("risk") or {}).get("overall", 0)) >= 0.75:
                parsed["risk"] = fallback["risk"]
                parsed["needs"]["safety_support"] = max(parsed["needs"].get("safety_support", 0), 0.95)
            # Model keywords cannot turn a denied/conditional request into a command.
            parsed["explicit_request"] = fallback["explicit_request"]
            # merge evidence facts (facts only)
            facts = {(e.get("source"), e.get("fact")) for e in parsed.get("evidence") or []}
            for e in fallback.get("evidence") or []:
                key = (e.get("source"), e.get("fact"))
                if key not in facts:
                    parsed.setdefault("evidence", []).append(e)
            return parsed
        except Exception:
            return fallback
