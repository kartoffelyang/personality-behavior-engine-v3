"""One transparent authored role filter for curated and generated candidates."""
import json
import re

ROLE_SCOPE_VERSION = 'role-scope-v1'
FAMILIES = {'ESTJ': 'order', 'INFP': 'feeling'}


def action_signature(bundle):
    # Changing only POI/song names or wording is not a style-level contrast.
    items = []
    for a in bundle['actions']:
        params = a.get('p', {})
        if a['c'] == 'navigation':
            params = {k: v for k, v in params.items() if k in {'偏好', '策略', '避让'}}
        elif a['c'] in {'ecosystem', 'entertainment'}:
            params = {k: v for k, v in params.items() if k in {'音量', '播放模式', '排序', '筛选条件'}}
        items.append((a['c'], a['n'], json.dumps(params, sort_keys=True, ensure_ascii=False)))
        basis=bundle.get('selection_basis') or {}
        if basis.get('facts') and basis.get('criterion') and a['n'] in {'目的地设置','内容选择'}:
            # A resource choice backed by explicit shared facts is a parameter
            # decision; ungrounded renaming above still cannot trigger a filter.
            value=a.get('p',{}).get('POI' if a['n']=='目的地设置' else '内容','')
            value=re.sub(r'^[A-Z]\d*\s+', '',str(value))
            items.append(('grounded_outcome',a['n'],value))
    return frozenset(items)


def role_scope(pid, bundles, policy, understanding=None):
    """Constrain style only where validated plans offer a concrete choice.

    Families describe authored decision styles, not psychological predictions or
    model confidence. The same code handles preset and freely generated plans.
    """
    task = (understanding or {}).get('task') or {}
    target = FAMILIES[pid]
    available = {family: [b for b in bundles if b.get('family') == family]
                 for family in FAMILIES.values()}
    contrast = (available['order'] and available['feeling'] and
                any(action_signature(a) != action_signature(b)
                    for a in available['order'] for b in available['feeling']))
    adaptation = None
    minimal = [b for b in bundles if b.get('family') == 'minimal']
    if not contrast and minimal and available['order'] and available['feeling']:
        if any(action_signature(a) != action_signature(b) for a in minimal for b in available['feeling']):
            # All plans already satisfy common terminals. Finishing there is a
            # general authored stopping rule, not a keyword scenario fallback.
            available['order'] = minimal
            contrast = True
            adaptation = '明确安排与体验候选的能力组合相同；任务完成即停止规则改用共同最小介入候选。'
    applied = bool(contrast and task.get('choice_space') is not False)
    allowed = available[target] if applied else bundles
    reason = ('多个合法方案存在实际动作或参数取舍，应用强化角色偏好。' if applied else
              '明确要求无额外选择空间，或候选未形成真实行为取舍；保留全部合法方案，不强造差异。')
    return {'version': ROLE_SCOPE_VERSION, 'applied': applied,
            'allowed_family': target if applied else None,
            'allowed_plan_ids': [b['id'] for b in allowed],
            'goal': policy['goal'], 'priorities': policy.get('priorities', []),
            'reason': reason,'candidate_adaptation': adaptation if applied else None,
            'candidate_evidence': {b['id']: b.get('role_evidence') or b['description'] for b in allowed},
            'source': 'authored_role_rule_not_model_probability'}


def atomic_decision_reason(row, adopted, bundles, eligible, selected_id, applied):
    """Explain the actual selector, not a fabricated probability threshold."""
    atom = row['id']
    contains = lambda b: any(a['c']+'/'+a['n']==atom for a in b['actions'])
    in_pool = [b for b in bundles if contains(b)]
    if not row.get('applicable', True):
        return '本场景不适用', row.get('reason', '当前任务没有此需求')
    if adopted and row['role']=='prerequisite':
        return '必要前置', '后续选中动作需要此步骤，单项参考分不作为开关。'
    if adopted and len(in_pool)==len(bundles):
        return '共同任务', '全部合法候选均包含此项，角色风格不改变共同步骤。'
    if applied and in_pool and not any(contains(b) for b in eligible):
        return '角色规则排除', '本角色允许的方案不含此项；模型参考分保留，未按单项高低采用。'
    if applied and adopted and all(contains(b) for b in eligible):
        return '角色规则采用', '本角色允许的方案均含此项；最终采用来自强化规则，不是单项分数过线。'
    if adopted:
        return '方案整体采用', '属于最终方案 '+selected_id+'；按完整组合排序，单项参考分不是执行概率。'
    if in_pool:
        return '方案整体取舍', '此项只在未选中方案中；比较完整安排后未采用。'
    return '未进入候选', '原始参考分仍保留，但没有合法参数与完整候选时不追加调用。'
