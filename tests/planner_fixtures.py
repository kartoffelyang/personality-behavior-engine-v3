"""Complete planner responses for tests focused on input propagation and UI."""
def action(capability, name, order=1, **overrides):
    result = {
        'capability': capability,
        'display_name': {'navigation': '导航', 'ecosystem': '生态服务', 'climate': '空调'}[capability],
        'name': name, 'order': order, 'intensity': 'medium',
        'execution_mode': 'auto_generate', 'preconditions': [], 'parameters_hint': {},
        'details': name, 'scenario_influence': '处理明确任务',
        'persona_influence': '人格未改变此动作', 'decision_basis': '依据任务与已有条件',
    }
    result.update(overrides)
    return result


def plan(actions, status='ready', questions=None):
    return {'behavior_actions': actions, 'plan_status': status, 'clarification_questions': questions or [],
            'final_explanation': '根据本次情境完成行为编排，动作须等待前提满足后执行。', 'notes': []}


def navigation_plan():
    names = ['地点搜索', '地点详情查看', '目的地设置', '路线规划', '路线比较',
             '路线选择', '导航运行控制', '预计到达时间获取', '迟到风险判断']
    actions = [action('navigation', name, i) for i, name in enumerate(names, 1)]
    for a in actions:
        if a['name'] in {'目的地设置', '路线选择', '导航运行控制'}:
            a['execution_mode'] = 'confirm_execute'
            a['preconditions'] = ['用户确认对应地点或路线']
        if a['name'] == '导航运行控制':
            a['details'] = '启动导航'
    return plan(actions)
