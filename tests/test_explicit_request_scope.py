from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.context_parser import ContextParser, extract_explicit_request
from engine.model_intent import INTENT_SYSTEM, run_intent
from engine.rule_filter import try_explicit_request


@pytest.mark.parametrize("utterance", [
    "不要打开空调。", "空调先别打开。", "不用播放音乐。", "不要导航到公司。",
    "如果太冷，打开空调。", "打开空调之前先问我。", "等我确认后再播放音乐。",
    "打开空调，等我确认。",
    "最好能播放音乐。", "打开空调，不要打开空调。", "我刚才打开了空调。",
    "禁止操作，打开空调。",
])
def test_restricted_or_unbound_phrases_do_not_force_a_positive_command(utterance):
    parsed = ContextParser(use_llm=False).parse({"user_utterance": utterance})
    assert parsed["explicit_request"]["exists"] is False


@pytest.mark.parametrize("utterance,capability", [
    ("打开空调。", "climate"), ("请把空调调到23度。", "climate"),
    ("放我上次听的那张专辑。", "entertainment"), ("播放轻音乐。", "entertainment"),
    ("导航到康源大厦。", "navigation"), ("带我回家。", "navigation"),
    ("空调有点凉，打开导航到公司。", "navigation"),
    ("打开勿扰模式。", "notification_mode"),
    ("不要打开空调，播放音乐。", "entertainment"),
    ("不要播放音乐，但打开空调。", "climate"),
    ("如果太冷，打开空调。播放音乐。", "entertainment"),
])
def test_direct_commands_remain_bound_to_their_own_domain(utterance, capability):
    explicit = extract_explicit_request(utterance)
    assert explicit["exists"] is True
    assert explicit["capability"] == capability


def test_last_album_parameter_requires_history_reference_in_same_command():
    assert extract_explicit_request("放我上次听的那张专辑")["parameters"] == {"album": "last_played"}
    assert extract_explicit_request("播放这张专辑")["parameters"] == {}


class FakeClient:
    model = "local-scope-test"

    def __init__(self, output):
        self.output = output

    def chat(self, messages, **kwargs):
        return json.dumps(self.output, ensure_ascii=False)


def intent_fixture(*, intents, constraints, optional=None):
    output = json.loads(INTENT_SYSTEM[INTENT_SYSTEM.index("{"):INTENT_SYSTEM.rindex("}") + 1])
    output["scenario_id"] = "cabin_comfort"
    output["task"] = {
        "core_goal": "按用户约束处理座舱需求", "explicit_intents": intents,
        "optional_goals": optional or [], "constraints": constraints, "must_do": [],
    }
    output["facts"] = []
    return output


@pytest.mark.parametrize("utterance,intents,constraints,optional", [
    ("不要打开空调。", [], ["禁止打开空调"], []),
    ("打开空调。", ["打开空调"], ["任何操作须经用户确认后执行"], []),
    ("打开空调。", ["打开空调"], ["任何操作需用户确认"], []),
    ("打开空调。", ["改善车内体感"], [], ["如果冷可以打开空调"]),
    ("打开空调。", ["暂时不要打开空调"], [], []),
])
def test_successful_step2_task_constraints_veto_legacy_priority(utterance, intents, constraints, optional):
    output = intent_fixture(intents=intents, constraints=constraints, optional=optional)
    request = {"persona": "ESTJ", "user_utterance": utterance, "available_capabilities": ["climate"]}
    result = run_intent(request, "openai", FakeClient(output))
    assert result["fallback"] is False
    assert result["output"]["task"] == output["task"]
    assert result["parsed_context"]["explicit_request"]["exists"] is False
    assert try_explicit_request(request, result["parsed_context"], None) is None


def test_successful_step2_preserves_direct_request_with_unrelated_constraint():
    output = intent_fixture(intents=["打开空调"], constraints=["不要播放音乐"])
    request = {"persona": "INFP", "user_utterance": "打开空调，不要播放音乐。"}
    result = run_intent(request, "openai", FakeClient(output))
    assert not result["fallback"]
    assert result["parsed_context"]["explicit_request"]["capability"] == "climate"


def test_filter_rejects_stale_positive_result_for_negative_utterance():
    request = {"user_utterance": "不要打开空调。", "available_capabilities": ["climate"]}
    stale = {"explicit_request": {"exists": True, "capability": "climate", "parameters": {}}}
    assert try_explicit_request(request, stale, None) is None


def test_optional_legacy_llm_cannot_restore_a_negated_command():
    client = FakeClient({"explicit_request": {"exists": True, "capability": "climate"}})
    result = ContextParser(client=client).parse({"user_utterance": "不要打开空调"})
    assert result["parser_fallback"] is False
    assert result["explicit_request"]["exists"] is False
