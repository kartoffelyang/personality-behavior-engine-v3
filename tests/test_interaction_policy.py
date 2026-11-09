from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.interaction_policy import (
    build_interaction_policy,
    dimension_catalog,
    dynamic_dimension_catalog,
    validate_activations,
)
from engine.registries import InteractionDimensionRegistry, PersonaRegistry


def activation(name, requirement="本轮只处理用户明确请求的范围。"):
    return {"dimension": name, "evidence": "用户明确要求只处理本次请求。", "context_requirement": requirement}


@pytest.mark.parametrize("invalid", [
    None, {}, "[]", ["时机敏感"],
    [activation("主动性")], [activation("unknown")],
    [activation("服务延伸"), activation("服务延伸")],
    [dict(activation("服务延伸"), extra=True)],
    [{"dimension": "服务延伸", "evidence": "", "context_requirement": "少推荐"}],
    [{"dimension": "服务延伸", "evidence": "   ", "context_requirement": "少推荐"}],
    [{"dimension": "服务延伸", "evidence": "明确", "context_requirement": 1}],
    [activation(name) for name in ("时机敏感", "社会礼仪敏感", "行为深度", "能力组织广度", "策略选择")],
])
def test_reject_invalid_activation_contract(invalid):
    with pytest.raises(ValueError, match="activated_interaction_dimensions"):
        validate_activations(invalid)


def test_catalog_contains_only_dynamic_definitions_and_cannot_mutate_source():
    catalog = dynamic_dimension_catalog()
    assert len(dimension_catalog()) == 18
    assert len(catalog) == 13
    assert "策略选择" not in {item["dimension"] for item in catalog}
    with pytest.raises(ValueError):
        validate_activations([activation("策略选择")])
    assert not {"主动性", "确认严格度", "解释透明度", "控制权保留度"} & {item["dimension"] for item in catalog}
    assert all(set(item) == {"dimension", "definition", "trigger", "boundary"} for item in catalog)
    catalog[0]["dimension"] = "changed"
    assert dynamic_dimension_catalog()[0]["dimension"] != "changed"


@pytest.mark.parametrize("persona_id,dominant", [("INFP", "Fi"), ("ESTJ", "Te")])
@pytest.mark.parametrize("count", [0, 1, 4])
def test_policy_preserves_selection_order_requirements_and_function_sources(persona_id, dominant, count):
    persona = PersonaRegistry().get(persona_id)
    functions = InteractionDimensionRegistry().get(dominant)
    selected = [activation(name) for name in ("服务延伸", "修复响应", "语音信息密度", "记忆调用强度")][:count]
    original = deepcopy((persona, functions, selected))
    policy = build_interaction_policy(persona, dominant, functions, selected)
    assert len(policy["constraints"]) == 4 + count
    assert [item["dimension"] for item in policy["constraints"]] == policy["fixed_dimensions"] + [item["dimension"] for item in selected]
    assert (persona, functions, selected) == original
    assert policy["activated_dimensions"] == selected
    assert policy["activated_dimensions"] is not selected
    assert all(item["source"] == "persona_default" for item in policy["constraints"][:4])
    assert all(item["source"] == "scenario" for item in policy["constraints"][4:])
    for item in selected:
        assert item["context_requirement"] in policy["compiled_prompt"]
    control = next(item for item in policy["constraints"] if item["dimension"] == "控制权保留度")
    assert control["level_source"] == "not_scored"
    assert "legacy_strength" not in control
    assert functions["accountability"]["control"] in control["description"]
    assert all(item["level_source"] == "not_scored" for item in policy["constraints"])
    assert all("legacy_strength" not in item for item in policy["constraints"])
    assert control["function_text_source"] == f"interaction_dimensions.yaml:{dominant}.accountability.control"


def test_restrictive_context_is_kept_even_when_persona_prefers_extension():
    policy = build_interaction_policy(
        PersonaRegistry().get("ESTJ"), "Te", InteractionDimensionRegistry().get("Te"),
        [activation("服务延伸", "不要推荐额外服务，也不要发起后续联络。")],
    )
    assert "情境优先：不要推荐额外服务，也不要发起后续联络。" in policy["compiled_prompt"]
    assert "全部显式目标外的支持，拒绝则不追加" in policy["compiled_prompt"]


@pytest.mark.parametrize("dominant", ["Se", "Si", "Ne", "Ni", "Te", "Ti", "Fe", "Fi"])
def test_v2_scores_do_not_change_styles_and_shared_baselines_apply_to_all_functions(dominant):
    registry = InteractionDimensionRegistry()
    functions = registry.get(dominant)
    names = [item["dimension"] for item in dynamic_dimension_catalog()]
    shared = {
        "确认严格度": registry.get("Se")["accountability"]["confirmation"],
        "语音信息密度": registry.get("Se")["relation"]["information_density"],
    }
    low = {"interaction": {"initiative": 0, "confirmation": 0, "memory_usage": 0, "follow_up": 0}}
    high = {"interaction": {key: 1 for key in low["interaction"]}}
    for start in range(0, len(names), 4):
        selected = [activation(name) for name in names[start:start + 4]]
        policy = build_interaction_policy(low, dominant, functions, selected)
        assert policy == build_interaction_policy(high, dominant, functions, selected)
        for item in policy["constraints"]:
            assert item["function_text_source"].startswith(f"interaction_dimensions.yaml:{dominant}.")
            assert item["level_source"] == "not_scored"
            if item["dimension"] in shared:
                expected = shared[item["dimension"]]
                assert item["description"] == expected
                assert item["description_source"] == "shared_context_baseline"
                assert policy["compiled_prompt"].count(expected) == 1


def test_missing_and_mismatched_legacy_dimensions_use_explicit_baselines():
    policy = build_interaction_policy({}, "Fi", {"relation": {"voice_org": "组织方式不是信息密度"}},
                                      [activation("语音信息密度"), activation("修复响应")])
    assert all(item["description_source"] == "definition_baseline" for item in policy["constraints"])
    assert all(item["level_source"] == "not_scored" for item in policy["constraints"])
    assert "组织方式不是信息密度" not in policy["compiled_prompt"]


def test_no_mbti_uses_only_scenario_constraints_without_persona_defaults():
    selected = [activation("服务延伸", "不要扩展到其他服务。")]
    policy = build_interaction_policy({}, None, InteractionDimensionRegistry().get("Fi"), selected)
    assert policy["fixed_dimensions"] == []
    assert policy["activated_dimensions"] == selected
    assert len(policy["constraints"]) == 1
    assert policy["constraints"][0]["description"] == ""
    assert policy["constraints"][0]["description_source"] == "scenario_only"
    assert "不要扩展到其他服务。" in policy["compiled_prompt"]
    assert "个人价值" not in policy["compiled_prompt"]
    assert "人格倾向：" not in policy["compiled_prompt"]


def test_no_mbti_empty_selection_has_no_constraints():
    policy = build_interaction_policy({}, None, {}, [])
    assert policy["fixed_dimensions"] == []
    assert policy["constraints"] == []
