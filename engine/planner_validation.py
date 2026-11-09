"""Validate planning contracts without selecting a fixed personality action set.

This checks declared dependencies and explicit task signals, not arbitrary natural
language entailment. Planned actions are never treated as completed tool results.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping

from .context_parser import extract_explicit_request


class PlanValidationError(ValueError):
    pass


MODES = {'auto_generate', 'auto_execute', 'conditional_auto', 'confirm_execute', 'choose_execute', 'suggest_only'}
ACTION_FIELDS = set('capability display_name name intensity execution_mode order preconditions details parameters_hint scenario_influence persona_influence decision_basis'.split())
TOP_FIELDS = {'behavior_actions', 'final_explanation', 'notes', 'plan_status', 'clarification_questions'}

# An action may consume previously supplied state instead of generating it again.
# These are dependency types, not a mandatory action skeleton.
DEPENDENCIES = {
    ('navigation', '地点详情查看'): ('destination_candidates',),
    ('navigation', '目的地设置'): ('destination_resolved',),
    ('navigation', '路线规划'): ('destination_set',),
    ('navigation', '路线比较'): ('routes',),
    ('navigation', '路线选择'): ('routes',),
    ('navigation', '导航运行控制'): ('selected_route',),
    ('navigation', '路线重新规划'): ('destination_set',),
    ('navigation', '途经点管理'): ('destination_set',),
    ('navigation', '预计到达时间获取'): ('routes',),
    ('navigation', '迟到风险判断'): ('eta',),
    ('entertainment', '内容详情查看'): ('content_candidates',),
    ('entertainment', '内容选择'): ('content_candidates',),
    ('ecosystem', '服务详情获取'): ('service_candidates',),
    ('ecosystem', '候选服务筛选'): ('service_candidates',),
    ('ecosystem', '候选方案比较'): ('service_candidates',),
    ('ecosystem', '候选方案选择'): ('service_candidates',),
    ('notification_mode', '通知摘要'): ('notifications',),
    ('notification_mode', '通知优先级分类'): ('notifications',),
}
PRODUCES = {
    ('navigation', '地点搜索'): {'destination_candidates'},
    ('navigation', '地点详情查看'): {'destination_resolved'},
    ('navigation', '历史目的地获取'): {'destination_candidates', 'destination_resolved'},
    ('navigation', '目的地设置'): {'destination_set'},
    ('navigation', '路线规划'): {'routes'},
    ('navigation', '路线重新规划'): {'routes'},
    ('navigation', '路线选择'): {'selected_route'},
    ('navigation', '预计到达时间获取'): {'eta'},
    ('entertainment', '内容搜索'): {'content_candidates'},
    ('ecosystem', '服务与地点搜索'): {'service_candidates'},
    ('notification_mode', '通知读取'): {'notifications'},
}
DEPENDENCY_LABELS = {
    'destination_candidates': '地点候选', 'destination_resolved': '目的地地址或坐标',
    'destination_set': '已设置目的地', 'routes': '候选路线',
    'selected_route': '已选择路线', 'eta': '预计到达时间',
    'content_candidates': '内容候选', 'service_candidates': '服务候选',
    'notifications': '通知内容',
}


def _require(test: bool, message: str) -> None:
    if not test:
        raise PlanValidationError(message)


def _strings(value: Any, field: str) -> None:
    _require(isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value), f'{field} 必须为非空字符串组成的数组')


def _json_value(value: Any) -> bool:
    if value is None:
        return False  # Unknown arguments must remain preconditions.
    if type(value) in (str, bool, int):
        return not isinstance(value, str) or bool(value.strip())
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return bool(value) and all(_json_value(v) for v in value)
    if isinstance(value, dict):
        return bool(value) and all(isinstance(k, str) and _json_value(v) for k, v in value.items())
    return False


def _parameter(value: Any, spec: Mapping[str, Any], field: str) -> None:
    types = {'string': lambda v: isinstance(v, str), 'number': lambda v: type(v) in (int, float) and math.isfinite(v),
             'integer': lambda v: type(v) is int, 'boolean': lambda v: type(v) is bool,
             'array': lambda v: isinstance(v, list), 'object': lambda v: isinstance(v, dict)}
    expected = spec.get('type')
    if expected:
        accepted = expected if isinstance(expected, list) else [expected]
        _require(any(t in types and types[t](value) for t in accepted), f'{field} 参数类型应为 {expected}')
    if 'enum' in spec:
        _require(value in spec['enum'], f'{field} 不在能力契约枚举内')
    for bound, comparison in [('minimum', lambda x, y: x >= y), ('maximum', lambda x, y: x <= y)]:
        if bound in spec:
            _require(type(value) in (int, float) and comparison(value, spec[bound]), f'{field} 超出能力契约的 {bound}')


def _provided_state(context: Mapping[str, Any]) -> set[str]:
    state = set()
    # Only actual supplied data counts. A destination name alone is unresolved.
    for key, token in [('destination_candidates', 'destination_candidates'),
                       ('destination_coordinates', 'destination_resolved'), ('destination_poi', 'destination_resolved'),
                       ('candidate_routes', 'routes'), ('current_route', 'selected_route'),
                       ('selected_route', 'selected_route'), ('estimated_arrival_time', 'eta'),
                       ('content_candidates', 'content_candidates'), ('service_candidates', 'service_candidates'),
                       ('notifications', 'notifications')]:
        if context.get(key):
            state.add(token)
    if context.get('destination_known') is True and context.get('destination'):
        state.add('destination_resolved')
    if context.get('destination_set') is True and context.get('destination'):
        state.update({'destination_set', 'destination_resolved'})
    if 'selected_route' in state:
        state.add('routes')
    return state


def _task_requirements(payload: Mapping[str, Any]) -> tuple[set[tuple[str, str]], set[str]]:
    flow = payload.get('flow_context') or {}
    situation = flow.get('scenario_understanding') or {}
    task = situation.get('task') or {}
    context = situation.get('context') or {}
    supplied = (flow.get('execution_context') or {}).get('context') or {}
    goal = str(task.get('core_goal') or '')
    intents = '；'.join(task.get('explicit_intents') or [])
    required, domains = set(), set()
    travel = bool(context.get('destination_reference') or supplied.get('destination')) and bool(re.search(r'去|到达|赶到|前往|导航|回家', goal + intents))
    # Search-only and stop-navigation tasks must not imply a new trip.
    stop_navigation = bool(re.search(r'(?:取消|停止|结束|暂停|关闭).{0,6}导航', goal))
    query_only = bool(re.search(r'^(?:请|帮我|先)?(?:查询|查一下|搜索|搜一下|查看|比较|推荐|规划)|只(?:想|要)?(?:查|搜)', goal))
    query_only = query_only and not re.search(r'启动导航|开始导航|带我|送我|并(?:出发|前往)', goal)
    defer_home = bool(re.search(r'暂缓回家|不(?:想|愿|急).{0,8}回(?:去|家)|不.{0,5}那么快回(?:去|家)', goal + intents))
    travel = travel and not (stop_navigation or query_only or defer_home or re.search(r'不要导航|不用导航', goal))
    if stop_navigation:
        domains.add('navigation')
    if travel:
        domains.add('navigation')
        required.add(('navigation', '导航运行控制'))
        if context.get('arrival_deadline'):
            required.add(('navigation', '迟到风险判断'))
    explicit = flow.get('explicit_request') or extract_explicit_request(flow.get('user_utterance') or goal, task if task.get('explicit_intents') else None)
    if explicit.get('exists'):
        domains.add(explicit['capability'])
    return required, domains


def validate_plan(raw: Any, payload: Mapping[str, Any], whitelist: list[str]) -> dict:
    _require(isinstance(raw, dict) and set(raw) == TOP_FIELDS, 'Step 7 顶层字段不符合输出契约')
    status = raw['plan_status']
    _require(isinstance(status, str) and status in {'ready', 'needs_clarification', 'no_tool_needed'}, 'plan_status 非法')
    if (payload.get('flow_context') or {}).get('generation_mode') == 'demo':
        _require(status != 'needs_clarification',
                 '演示模式不返回 needs_clarification、不向用户追问信息；请自行补全合理场景设定，在 notes 中以“演示假设：”标明，并编排完整行为序列。真实查询结果仍需由前序能力提供。')
    _strings(raw['notes'], 'notes')
    _strings(raw['clarification_questions'], 'clarification_questions')
    _require(isinstance(raw['final_explanation'], str) and bool(raw['final_explanation'].strip()), '缺少 final_explanation')
    _require(isinstance(raw['behavior_actions'], list), 'behavior_actions 必须为数组')
    questions = raw['clarification_questions']
    _require(bool(questions) == (status == 'needs_clarification'), '需要补充信息时必须提供问题，其他状态不得携带待补充问题')
    actions = raw['behavior_actions']
    _require(bool(actions) if status == 'ready' else True, '空计划不得标记为 ready')
    _require(not actions if status == 'no_tool_needed' else True, 'no_tool_needed 不得携带工具动作')
    catalog = {c['capability']: c for c in (payload.get('flow_context') or {}).get('capability_catalog', [])}
    orders = []
    for index, a in enumerate(actions, 1):
        path = f'动作 {index}'
        _require(isinstance(a, dict) and set(a) == ACTION_FIELDS, f'{path} 缺少必填字段或包含额外字段')
        for field in ACTION_FIELDS - {'order', 'preconditions', 'parameters_hint'}:
            _require(isinstance(a[field], str) and bool(a[field].strip()), f'{path}.{field} 必须为非空字符串')
        cap, name = a['capability'], a['name']
        _require(cap in whitelist and cap in catalog, f'{path} 能力不存在或不可用：{cap}')
        atom = next((v for v in catalog[cap].get('atomic_abilities', []) if v['name'] == name), None)
        _require(atom is not None, f'{path} 原子能力不存在：{cap}/{name}')
        _require(a['display_name'] == catalog[cap].get('display_name'), f'{path} 能力域名称与能力库不符')
        _require(a['intensity'] in {'low', 'medium', 'high'}, f'{path} intensity 非法')
        _require(a['execution_mode'] in MODES, f'{path} execution_mode 非法')
        _require(type(a['order']) is int and a['order'] > 0, f'{path} order 必须为正整数')
        orders.append(a['order'])
        _strings(a['preconditions'], f'{path}.preconditions')
        _require(a['execution_mode'] != 'conditional_auto' or bool(a['preconditions']), f'{path} 条件执行缺少具体前提')
        params = a['parameters_hint']
        _require(isinstance(params, dict), f'{path}.parameters_hint 必须为对象')
        _require(set(params) <= set(atom.get('core_params') or []), f'{path} parameters_hint 含能力库未定义的参数：{sorted(set(params) - set(atom.get("core_params") or []))}')
        for key, value in params.items():
            _require(_json_value(value), f'{path}.{key} 参数值为空、非有限数值或类型非法')
            _parameter(value, (atom.get('parameter_schema') or {}).get(key, {}), f'{path}.{key}')
    _require(sorted(orders) == list(range(1, len(actions) + 1)), '动作 order 必须唯一且从1连续编号')
    actions = sorted(actions, key=lambda a: a['order'])
    flow = payload.get('flow_context') or {}
    context = (flow.get('execution_context') or {}).get('context') or {}
    state = _provided_state(context)
    for index, a in enumerate(actions):
        key = (a['capability'], a['name'])
        dependencies = DEPENDENCIES.get(key, ())
        if key == ('navigation', '导航运行控制') and re.search(r'暂停|停止|结束|取消', a['details']) and not re.search(r'启动|开始', a['details']):
            dependencies = ()
        for dependency in dependencies:
            if dependency in state:
                continue
            later = any(dependency in PRODUCES.get((b['capability'], b['name']), set()) for b in actions[index + 1:])
            _require(not later, f'动作 {a["order"]} {a["name"]} 的依赖顺序颠倒：{DEPENDENCY_LABELS[dependency]}')
            # Missing external state can be a genuine clarification outcome;
            # a precondition string alone never makes it a complete plan.
            _require(status == 'needs_clarification' and a['execution_mode'] in {'conditional_auto', 'confirm_execute', 'choose_execute', 'suggest_only'} and bool(a['preconditions']),
                     f'动作 {a["order"]} {a["name"]} 缺少依赖：{DEPENDENCY_LABELS[dependency]}')
        if a['execution_mode'] != 'suggest_only':
            state.update(PRODUCES.get(key, set()))
    required, domains = _task_requirements(payload)
    chosen = {(a['capability'], a['name']) for a in actions}
    if status == 'ready':
        _require(required <= chosen, f'核心任务未覆盖：{sorted(required - chosen)}；无法继续时应输出 needs_clarification')
        _require(domains <= {a['capability'] for a in actions}, f'明确任务能力未覆盖：{sorted(domains - {a["capability"] for a in actions})}')
        _require(required <= {(a['capability'], a['name']) for a in actions if a['execution_mode'] != 'suggest_only'},
                 '仅建议执行不能代替核心任务所需的动作')
        if ('navigation', '导航运行控制') in required:
            _require(any(a['capability'] == 'navigation' and a['name'] == '导航运行控制'
                         and re.search(r'启动|开始|继续|开启|恢复', a['details'])
                         and not re.search(r'不(?:要|得|能|会)?(?:启动|开始|继续|开启|恢复)', a['details'])
                         and a['execution_mode'] != 'suggest_only' for a in actions),
                     '前往目的地的任务必须包含启动或继续导航，停止导航或仅建议不能完成该目标')
    if status == 'no_tool_needed':
        _require(not domains, '当前明确任务需要工具，不能标为 no_tool_needed')
        explicit = (payload.get('flow_context') or {}).get('explicit_request') or {}
        _require(not explicit.get('exists'), '存在明确工具请求，不能标为 no_tool_needed')
    return {**raw, 'behavior_actions': actions}
