from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.model_planner import build_step7_capability_catalog
from engine.planner_validation import PlanValidationError, validate_plan
from engine.registries import CapabilityRegistry


@pytest.fixture
def catalog():
    registry = CapabilityRegistry()
    return build_step7_capability_catalog([
        {"capability": key, **registry.get(key)} for key in registry.all_ids()
    ])


def payload(catalog, *, travel=False, deadline=False, supplied=None):
    return {
        "persona": "ESTJ",
        "flow_context": {
            "capability_catalog": catalog,
            "execution_context": {"context": supplied or {}, "history": {}},
            "scenario_understanding": {
                "task": {
                    "core_goal": "今天10:00前到达康源大厦" if travel else "回应当前座舱诉求",
                    "explicit_intents": ["前往康源大厦"] if travel else [],
                    "optional_goals": [], "constraints": [], "must_do": [],
                },
                "context": {
                    "destination_reference": "康源大厦" if travel else None,
                    "arrival_deadline": "今天10:00前" if deadline else None,
                },
            },
        },
    }


def action(catalog, name="温度调节", capability="climate", order=1, **overrides):
    cap = next(item for item in catalog if item["capability"] == capability)
    value = {
        "capability": capability, "display_name": cap["display_name"], "name": name,
        "intensity": "medium", "execution_mode": "confirm_execute", "order": order,
        "preconditions": [], "details": f"执行{name}", "parameters_hint": {},
        "scenario_influence": "满足当前任务", "persona_influence": "人格未改变此动作",
        "decision_basis": "用户明确任务和实际上下文",
    }
    value.update(overrides)
    return value


def plan(actions, *, status="ready", questions=None):
    return {"behavior_actions": actions, "final_explanation": "根据任务和已知上下文安排动作。",
            "notes": [], "plan_status": status, "clarification_questions": questions or []}


def validate(raw, data):
    allowed = [item["capability"] for item in data["flow_context"]["capability_catalog"]]
    return validate_plan(raw, data, allowed)


def trip_actions(catalog, *, deadline=False):
    names = ["地点搜索", "地点详情查看", "目的地设置", "路线规划", "路线选择"]
    if deadline:
        names += ["预计到达时间获取", "迟到风险判断"]
    names += ["导航运行控制"]
    return [action(catalog, name, "navigation", index, details=("启动导航" if name == "导航运行控制" else f"执行{name}"))
            for index, name in enumerate(names, 1)]


@pytest.mark.parametrize("overrides,match", [
    ({"execution_mode": "invalid_mode"}, "execution_mode"),
    ({"intensity": "extreme"}, "intensity"),
    ({"parameters_hint": {"不存在的参数": True}}, "未定义"),
    ({"parameters_hint": {"温度": "23"}}, "类型"),
    ({"parameters_hint": {"温度": True}}, "类型"),
    ({"parameters_hint": {"温度": [23]}}, "类型"),
    ({"parameters_hint": {"温度": None}}, "参数值"),
    ({"parameters_hint": {"温度": float("inf")}}, "参数值"),
    ({"parameters_hint": {"区域": ""}}, "参数值"),
    ({"parameters_hint": []}, "必须为对象"),
    ({"preconditions": "用户确认后"}, "数组"),
    ({"preconditions": [None]}, "数组"),
    ({"execution_mode": "conditional_auto"}, "缺少具体前提"),
    ({"display_name": "不存在的域名"}, "能力域名称"),
    ({"name": "万能驾驶"}, "原子能力不存在"),
    ({"details": ""}, "非空字符串"),
    ({"extra": "unsupported"}, "额外字段"),
])
def test_actual_atom_rejects_invalid_fields_and_parameters(catalog, overrides, match):
    with pytest.raises(PlanValidationError, match=match):
        validate(plan([action(catalog, **overrides)]), payload(catalog))


@pytest.mark.parametrize("missing", ["preconditions", "parameters_hint", "decision_basis", "execution_mode"])
def test_missing_action_fields_are_not_silently_defaulted(catalog, missing):
    item = action(catalog)
    del item[missing]
    with pytest.raises(PlanValidationError, match="必填字段"):
        validate(plan([item]), payload(catalog))


@pytest.mark.parametrize("order", [True, 0, -1, 1.0, "1", 2])
def test_action_order_is_a_contiguous_positive_integer(catalog, order):
    with pytest.raises(PlanValidationError, match="order"):
        validate(plan([action(catalog, order=order)]), payload(catalog))


def test_duplicate_order_rejected_but_out_of_array_order_sorted_without_mutation(catalog):
    first = action(catalog, order=1)
    second = action(catalog, order=2)
    with pytest.raises(PlanValidationError, match="order"):
        validate(plan([first, {**second, "order": 1}]), payload(catalog))
    raw = plan([second, first])
    before = copy.deepcopy(raw)
    checked = validate(raw, payload(catalog))
    assert [item["order"] for item in checked["behavior_actions"]] == [1, 2]
    assert raw == before


@pytest.mark.parametrize("mutation", ["missing_status", "legacy_actions", "extra", "bad_status"])
def test_top_level_schema_is_not_silently_repaired(catalog, mutation):
    raw = plan([action(catalog)])
    if mutation == "missing_status":
        del raw["plan_status"]
    elif mutation == "legacy_actions":
        raw["actions"] = raw.pop("behavior_actions")
    elif mutation == "extra":
        raw["unexpected"] = True
    else:
        raw["plan_status"] = "success"
    with pytest.raises(PlanValidationError):
        validate(raw, payload(catalog))


def test_recorded_route_selection_extra_parameter_is_rejected(catalog):
    item = action(catalog, "路线选择", "navigation", parameters_hint={"选择方式": "用户选择"})
    with pytest.raises(PlanValidationError, match="未定义"):
        validate(plan([item]), payload(catalog, supplied={"candidate_routes": [{"id": "r1"}]}))


def test_temperature_range_comes_only_from_declared_vehicle_contract(catalog):
    # The current library declares a number, without a verified hardware range.
    raw = plan([action(catalog, parameters_hint={"温度": 99})])
    assert validate(raw, payload(catalog))["plan_status"] == "ready"
    custom = copy.deepcopy(catalog)
    atom = next(item for item in custom[0]["atomic_abilities"] if item["name"] == "温度调节")
    atom["parameter_schema"]["温度"].update(minimum=18, maximum=30)
    with pytest.raises(PlanValidationError, match="maximum"):
        validate(raw, payload(custom))


def test_empty_ready_plan_is_not_success(catalog):
    with pytest.raises(PlanValidationError, match="空计划"):
        validate(plan([]), payload(catalog, travel=True))


def test_navigation_start_cannot_skip_route_dependency(catalog):
    start = action(catalog, "导航运行控制", "navigation", details="启动前往康源大厦的导航")
    with pytest.raises(PlanValidationError, match="缺少依赖"):
        validate(plan([start]), payload(catalog, travel=True))
    start["preconditions"] = ["路线已经选择"]
    with pytest.raises(PlanValidationError, match="缺少依赖"):
        validate(plan([start]), payload(catalog, travel=True))


def test_dependency_order_is_checked_even_with_valid_order_numbers(catalog):
    actions = [action(catalog, "地点详情查看", "navigation", 1),
               action(catalog, "地点搜索", "navigation", 2)]
    with pytest.raises(PlanValidationError, match="顺序颠倒"):
        validate(plan(actions), payload(catalog))


def test_current_real_route_can_be_used_without_forcing_repeated_planning(catalog):
    current_route = {"id": "route-from-navigation", "destination_poi": "kangyuan-001"}
    actions = [action(catalog, "预计到达时间获取", "navigation", 1),
               action(catalog, "迟到风险判断", "navigation", 2),
               action(catalog, "导航运行控制", "navigation", 3, details="启动现有路线导航")]
    checked = validate(plan(actions), payload(catalog, travel=True, deadline=True,
                                           supplied={"current_route": current_route}))
    assert len(checked["behavior_actions"]) == 3


def test_trip_cannot_be_completed_by_stopping_or_only_suggesting_navigation(catalog):
    data = payload(catalog, travel=True, supplied={"current_route": {"id": "current-route"}})
    for details, mode in [("停止导航", "confirm_execute"), ("不要启动导航", "confirm_execute"),
                          ("建议启动导航", "suggest_only")]:
        item = action(catalog, "导航运行控制", "navigation", details=details, execution_mode=mode)
        with pytest.raises(PlanValidationError, match="启动或继续导航|仅建议执行不能代替"):
            validate(plan([item]), data)


def test_continue_existing_navigation_covers_the_trip_without_new_route_search(catalog):
    data = payload(catalog, travel=True, supplied={"current_route": {"id": "current-route"}})
    item = action(catalog, "导航运行控制", "navigation", details="继续使用当前路线导航至康源大厦")
    checked = validate(plan([item]), data)
    assert checked["plan_status"] == "ready"
    assert len(checked["behavior_actions"]) == 1


@pytest.mark.parametrize("goal", ["取消导航", "暂停当前导航", "取消当前导航"])
def test_actual_cancel_navigation_task_does_not_require_new_route(catalog, goal):
    data = payload(catalog, travel=True)
    task = data["flow_context"]["scenario_understanding"]["task"]
    task["core_goal"] = goal
    task["explicit_intents"] = [goal]
    item = action(catalog, "导航运行控制", "navigation", details=goal)
    assert validate(plan([item]), data)["plan_status"] == "ready"


@pytest.mark.parametrize("goal", ["取消导航", "暂停当前导航", "取消当前导航"])
def test_cancel_or_pause_navigation_is_still_a_tool_task(catalog, goal):
    data = payload(catalog, travel=True)
    task = data["flow_context"]["scenario_understanding"]["task"]
    task["core_goal"] = goal
    task["explicit_intents"] = [goal]
    with pytest.raises(PlanValidationError, match="需要工具"):
        validate(plan([], status="no_tool_needed"), data)


@pytest.mark.parametrize("goal", ["查询去康源大厦的路线", "查一下去康源大厦的路线"])
def test_route_query_without_the_word_only_does_not_imply_departure(catalog, goal):
    data = payload(catalog, travel=True, supplied={"destination_set": True, "destination": "康源大厦"})
    task = data["flow_context"]["scenario_understanding"]["task"]
    task["core_goal"] = goal
    task["explicit_intents"] = [goal]
    item = action(catalog, "路线规划", "navigation", details="生成去康源大厦的候选路线供查看")
    assert validate(plan([item]), data)["plan_status"] == "ready"


def test_destination_name_is_not_an_already_selected_route(catalog):
    start = action(catalog, "导航运行控制", "navigation", details="启动导航")
    with pytest.raises(PlanValidationError, match="缺少依赖"):
        validate(plan([start]), payload(catalog, travel=True, supplied={"destination": "康源大厦"}))


def test_search_only_does_not_finish_an_explicit_trip(catalog):
    search = action(catalog, "地点搜索", "navigation")
    with pytest.raises(PlanValidationError, match="核心任务未覆盖"):
        validate(plan([search]), payload(catalog, travel=True))


def test_not_wanting_to_go_home_does_not_force_navigation_to_home(catalog):
    data = payload(catalog, travel=True)
    task = data['flow_context']['scenario_understanding']['task']
    task.update(core_goal='缓解分别后的情绪并理清今晚暂缓回家的想法',
                explicit_intents=['不想那么快回家', '想知道还能去哪儿'])
    search = action(catalog, '服务与地点搜索', 'ecosystem', execution_mode='auto_generate',
                    details='搜索附近适合短暂停留的地点候选，供后续用户选择')
    assert validate(plan([search]), data)['plan_status'] == 'ready'


def test_deadline_requires_a_risk_assessment(catalog):
    with pytest.raises(PlanValidationError, match="迟到风险判断"):
        validate(plan(trip_actions(catalog)), payload(catalog, travel=True, deadline=True))


def test_complete_trip_allows_personality_auxiliary_capability_expansion(catalog):
    actions = trip_actions(catalog, deadline=True)
    actions.append(action(catalog, "服务与地点搜索", "ecosystem", len(actions) + 1,
                          execution_mode="conditional_auto", preconditions=["按时到达有可用时间余量"],
                          details="搜索路线附近可快速购买早餐的地点", persona_influence="以效率偏好收束候选"))
    raw = plan(actions)
    assert validate(raw, payload(catalog, travel=True, deadline=True))["behavior_actions"] == actions


def test_genuine_search_task_does_not_require_navigation_start(catalog):
    data = payload(catalog, travel=True)
    data["flow_context"]["scenario_understanding"]["task"]["core_goal"] = "只查询去康源大厦的地点信息"
    checked = validate(plan([action(catalog, "地点搜索", "navigation")]), data)
    assert len(checked["behavior_actions"]) == 1


def test_needs_clarification_is_a_distinct_outcome(catalog):
    questions = ["你指的是哪个城市的康源大厦？"]
    raw = plan([], status="needs_clarification", questions=questions)
    checked = validate(raw, payload(catalog, travel=True))
    assert checked["plan_status"] == "needs_clarification"
    assert checked["clarification_questions"] == questions
    start = action(catalog, "导航运行控制", "navigation", details="确认后启动导航",
                   execution_mode="conditional_auto", preconditions=["已有路线经用户确认后才可继续"])
    partial = plan([start], status="needs_clarification", questions=["请确认本次使用哪条已有路线？"])
    assert validate(partial, payload(catalog, travel=True))["plan_status"] == "needs_clarification"


@pytest.mark.parametrize("mode", [None, "live", "demo"])
def test_only_demo_mode_rejects_clarification_for_automatic_repair(catalog, mode):
    data = payload(catalog, travel=True)
    if mode is not None:
        data["flow_context"]["generation_mode"] = mode
    raw = plan([], status="needs_clarification", questions=["请补充目的地。"])
    if mode == "demo":
        with pytest.raises(PlanValidationError, match="needs_clarification"):
            validate(raw, data)
    else:
        assert validate(raw, data)["clarification_questions"] == ["请补充目的地。"]


@pytest.mark.parametrize("name,assumption", [
    ("目的地设置", "目的地地址和坐标已确定。"),
    ("导航运行控制", "已生成并选择一条抵达公园的路线。"),
])
def test_demo_assumptions_do_not_satisfy_real_navigation_dependencies(catalog, name, assumption):
    data = payload(catalog)
    data["flow_context"]["generation_mode"] = "demo"
    raw = plan([action(catalog, name, "navigation", details="启动导航" if name == "导航运行控制" else "设置目的地")])
    raw["notes"] = ["演示假设：" + assumption]
    with pytest.raises(PlanValidationError, match="缺少依赖"):
        validate(raw, data)


@pytest.mark.parametrize("status,questions", [
    ("needs_clarification", []), ("ready", ["你指哪座大厦？"]), ("no_tool_needed", ["你指哪座大厦？"]),
])
def test_clarification_questions_match_outcome(catalog, status, questions):
    with pytest.raises(PlanValidationError, match="需要补充信息"):
        validate(plan([], status=status, questions=questions), payload(catalog))


def test_no_tool_outcome_cannot_hide_an_explicit_trip_or_tool_command(catalog):
    raw = plan([], status="no_tool_needed")
    assert validate(raw, payload(catalog))["plan_status"] == "no_tool_needed"
    with pytest.raises(PlanValidationError, match="明确任务需要工具"):
        validate(raw, payload(catalog, travel=True))
    explicit = payload(catalog)
    explicit["flow_context"]["explicit_request"] = {"exists": True, "capability": "climate"}
    with pytest.raises(PlanValidationError, match="需要工具"):
        validate(raw, explicit)
    with pytest.raises(PlanValidationError, match="不得携带工具动作"):
        validate(plan([action(catalog)], status="no_tool_needed"), payload(catalog))


def test_suggestion_does_not_produce_a_tool_result_for_later_actions(catalog):
    actions = [action(catalog, "地点搜索", "navigation", 1, execution_mode="suggest_only"),
               action(catalog, "地点详情查看", "navigation", 2)]
    with pytest.raises(PlanValidationError, match="缺少依赖"):
        validate(plan(actions), payload(catalog))


def test_missing_legacy_shortcut_does_not_turn_direct_climate_task_into_no_tool(catalog):
    data = payload(catalog)
    data["flow_context"]["scenario_understanding"]["task"]["core_goal"] = "打开空调"
    data["flow_context"]["scenario_understanding"]["task"]["explicit_intents"] = ["打开空调"]
    with pytest.raises(PlanValidationError, match="需要工具"):
        validate(plan([], status="no_tool_needed"), data)
