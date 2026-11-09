"""Scene-wide atomic adoption experiment, with complete catalog accounting.

The scene scope is neutral and shared. Model adoption, compiler dependencies,
and the final action list remain separate, including when they disagree.
"""
import copy

from .planner_validation import DEPENDENCIES, PRODUCES


SCENE_DOMAINS = {
    'rest_stop': {'ecosystem', 'navigation', 'entertainment', 'notification_mode'},
    'weekend': {'ecosystem', 'navigation', 'entertainment', 'notification_mode'},
    'after_work': {'climate', 'seat', 'entertainment', 'ambient_light', 'notification_mode'},
}
PREREQUISITES = {'地点搜索', '地点详情查看', '路线规划', '内容搜索', '服务与地点搜索', '通知读取'}
PROVIDERS = {
    'destination_candidates': ('navigation', '地点搜索'), 'destination_resolved': ('navigation', '地点详情查看'),
    'destination_set': ('navigation', '目的地设置'), 'routes': ('navigation', '路线规划'),
    'selected_route': ('navigation', '路线选择'), 'eta': ('navigation', '预计到达时间获取'),
    'content_candidates': ('entertainment', '内容搜索'), 'service_candidates': ('ecosystem', '服务与地点搜索'),
    'notifications': ('notification_mode', '通知读取'),
}


def key(domain, name):
    return domain + '/' + name


def scope_catalog(eng, case_id, candidates, text):
    domains = SCENE_DOMAINS.get(case_id) or {a['c'] for c in candidates for a in c.get('actions', [])}
    evidenced = {key(a['c'], a['n']) for c in candidates for a in c.get('actions', [])}
    rows, excluded = [], []
    index = 0
    for domain in eng.capabilities.all_ids():
        cap = eng.capabilities.get(domain)
        for ability in cap['atomic_abilities']:
            index += 1
            name = ability['name']
            row = {'id': key(domain, name), 'question_id': f'atomic_{index:02}', 'domain': domain,
                   'domain_label': cap['display_name'], 'name': name, 'definition': ability['definition']}
            reason = None
            if domain not in domains:
                reason = '当前任务与中立候选未涉及此能力域'
            elif '恢复' in name or name == '历史目的地获取':
                reason = '本轮未提供可用的历史设置或偏好，不能假造记忆'
            elif name in {'前挡除雾开关', '后挡除雾开关'}:
                reason = '未出现起雾或视野问题'
            elif name in {'预约与排队管理', '订单管理', '支付与缴费', '服务进度查询'}:
                reason = '当前目标不涉及预约、订单、支付或已有服务进度'
            elif name in {'回复草稿生成', '回复发送', '回拨发起'}:
                reason = '没有需要回复或回拨的对象与通信任务'
            elif name in {'通知读取', '通知摘要', '通知优先级分类'}:
                reason = '本轮未提供待处理通知；保留防打扰设置的候选'
            elif name == '路线重新规划':
                reason = '本轮没有已有路线发生变化的事件'
            elif name == '途经点管理':
                reason = '当前任务只选一个目的地，没有多站目标'
            elif name == '迟到风险判断' and case_id != 'weekend' and not any(s in text for s in ('迟到', '赶时间', '之前', '前到')):
                reason = '没有明确到达期限或迟到风险'
            if row['id'] in evidenced:
                reason = None  # Explicit neutral candidate steps are evidence for free-input tasks.
            if reason:
                excluded.append({**row, 'reason': reason})
                continue
            role = 'prerequisite' if name in PREREQUISITES else 'candidate'
            if name == '候选方案选择' and case_id in {'rest_stop', 'weekend'}:
                role = 'core'
            row.update(role=role, reason={'candidate': '情境相关的可选动作，由 KEV 逐项判断',
                       'prerequisite': '仅在后续动作需要时补齐，不伪造采用概率',
                       'core': '完成当前推荐任务的共同步骤；具体候选由 KEV 选择'}[role])
            rows.append(row)
    return {'version': 1, 'total_catalog': index, 'rows': rows, 'excluded': excluded,
            'scope_source': 'shared_scene_domains_and_explicit_context_exclusions',
            'scored_count': sum(r['role'] == 'candidate' for r in rows)}


def action_templates(selected, case_id, arrangement):
    name, ident = selected['name'], selected['id']
    content = name if case_id == 'after_work' else '轻松纯音乐（共享演示假设）'
    item = f'{ident} {name}' if case_id == 'after_work' else content
    # Parameters are common candidate hypotheses, never inferred personal history.
    definitions = [
        ('climate', '空调开关', {'开关': True, '作用区域': '前排'}, '模拟开启前排空调'),
        ('climate', '温度调节', {'温度': 24, '区域': '前排'}, '模拟将前排温度设为24℃'),
        ('climate', '风量调节', {'档位': 1, '区域': '前排'}, '模拟将前排风量调为1档'),
        ('climate', '出风模式切换', {'吹脚': True}, '模拟切换为吹脚出风'),
        ('climate', '空气净化开关', {'开关': True, '净化模式': '自动'}, '模拟开启自动空气净化'),
        ('seat', '座椅位置调节', {'靠背': '轻微后倾'}, '模拟在驻车时轻微后倾驾驶位靠背'),
        ('seat', '座椅加热控制', {'开关': True, '档位': 1, '座位': '驾驶位'}, '模拟开启驾驶位1档加热'),
        ('seat', '座椅通风控制', {'开关': True, '档位': 1, '座位': '驾驶位'}, '模拟开启驾驶位1档通风'),
        ('seat', '座椅按摩控制', {'开关': True, '模式': '轻柔', '强度': '低', '座位': '驾驶位'}, '模拟开启驾驶位轻柔按摩'),
        ('ambient_light', '氛围灯开关', {'开关': True, '区域': '座舱'}, '模拟开启座舱氛围灯'),
        ('ambient_light', '颜色设置', {'颜色': '暖白', '区域': '座舱'}, '模拟将氛围灯设为暖白'),
        ('ambient_light', '亮度调节', {'亮度': 20, '区域': '座舱'}, '模拟将氛围灯亮度设为20%'),
        ('ambient_light', '灯光效果切换', {'静态': True}, '模拟采用静态灯效'),
        ('entertainment', '内容搜索', {'关键词': content}, f'模拟检索共同内容候选《{content}》'),
        ('entertainment', '内容详情查看', {'名称': content}, f'模拟查看《{content}》的内容信息'),
        ('entertainment', '内容选择', {'内容': item}, f'选择《{content}》'),
        ('entertainment', '播放控制', {'播放': True}, f'模拟播放《{content}》'),
        ('entertainment', '音源切换', {'车机': True}, '模拟切换为车机音源'),
        ('entertainment', '音量调节', {'音量': 15}, '模拟将播放音量设为15%'),
        ('notification_mode', '通知读取', {'来源':'本轮共同模拟消息集','时间':'刚才','内容范围':'非紧急消息'}, '模拟读取共同消息集，不连接真实账号'),
        ('notification_mode', '通知摘要', {'通知集合':'本轮共同模拟消息集','摘要长度':'一段简短摘要'}, '模拟摘要：普通讨论与日常通知，没有紧急事项'),
        ('notification_mode', '通知优先级分类', {'来源':'本轮共同模拟消息集','联系人':'模拟发送方','任务相关性':'均无紧急事项'}, '模拟按当前任务相关性整理消息优先级'),
        ('notification_mode', '提醒延后', {'延后时间': '25分钟', '恢复条件': '休息结束'}, '模拟将非紧急提醒延后25分钟'),
        ('notification_mode', '通知静音控制', {'开关': True, '时长': '25分钟', '应用范围': '非紧急应用'}, '模拟静音非紧急应用通知25分钟'),
        ('notification_mode', '非紧急通知屏蔽', {'屏蔽范围': '非紧急通知', '白名单': '紧急通知', '时长': '25分钟'}, '模拟屏蔽非紧急通知25分钟，保留紧急通知'),
        ('ecosystem', '服务与地点搜索', {'类型': '餐厅' if case_id == 'rest_stop' else '出游地点', '关键词': '共同虚拟候选'}, '读取共同地点与服务候选'),
        ('ecosystem', '服务详情获取', {'地址': f'{name}（虚拟地点）'}, f'模拟查看{name}的服务信息'),
        ('ecosystem', '候选服务筛选', {'距离': '按共同候选比较', '价格': '按共同候选比较'}, '按共同候选给出的距离与价格筛选'),
        ('ecosystem', '候选方案比较', {'时间': '比较共同候选用时', '价格': '比较共同候选费用'}, '对照共同候选的时间、费用与体验'),
        ('ecosystem', '价格与优惠查询', {'原价': '以共同候选的费用为准', '优惠价': '无已知优惠，不推断折扣'}, '查看共同候选费用，不编造优惠'),
        ('ecosystem', '实时资源状态查询', {'车位': '演示设定有车位', '营业状态': '演示设定开放'}, '核对共同演示设定中的开放与停车状态'),
        ('ecosystem', '候选方案选择', {'候选项': f'{ident} {name}', '选择方式': arrangement}, f'选择{name}：{selected["description"]}'),
        ('navigation', '地点搜索', {'名称': name}, f'模拟检索{name}'),
        ('navigation', '地点详情查看', {'地址': f'{name}（虚拟地点）'}, f'模拟核对{name}地址与入口'),
        ('navigation', '目的地设置', {'POI': f'{ident} {name}'}, f'模拟将目的地设为{name}'),
        ('navigation', '路线规划', {'起终点': f'当前驻车点 → {name}', '偏好': arrangement}, f'模拟规划前往{name}的路线'),
        ('navigation', '路线比较', {'时间': '按共同演示用时比较', '距离': '按共同候选距离比较'}, '模拟比较路线，不虚构实时路况'),
        ('navigation', '路线选择', {'路线': f'前往{name}的演示路线'}, f'模拟选定前往{name}的路线'),
        ('navigation', '导航运行控制', {'开始': True}, f'模拟启动前往{name}的导航；当前仍为驻车'),
        ('navigation', '预计到达时间获取', {'路线': f'前往{name}的演示路线', '路况': '仅使用共同演示用时'}, '根据共同演示用时计算到达安排，不提供真实ETA'),
        ('navigation', '迟到风险判断', {'到达时间': '按共同演示用时', '任务时间': '16:00前返回' if case_id == 'weekend' else '用户明确的截止时间', '缓冲时间': '按共同演示时间核对'}, '核对共同设定的行程是否满足用户截止时间'),
    ]
    result = {key(d, n): {'c': d, 'n': n, 'm': 'auto_generate', 'p': p, 'd': s+'。',
                         'why': 'KEV逐项判断是否采用；不是心理因果解释。', 'basis': '具体参数为两种人格共用的候选假设'}
              for d, n, p, s in definitions}
    if case_id not in SCENE_DOMAINS:
        for a in selected.get('actions', []):
            result[key(a['c'], a['n'])] = copy.deepcopy(a)
    return result


def atomic_questions(scope, templates):
    result = {}
    for row in scope['rows']:
        if row['role'] != 'candidate':
            continue
        proposed = templates[row['id']]['d']
        result[row['question_id']] = {'type': 'choice',
            'instructions': f'结合同一情境、明确意愿与人格配置，本轮是否采用“{row["name"]}”？{row["definition"]}。各项独立判断，不因能力可用就必须调用。',
            'criteria': {'include': f'采用：{proposed}', 'omit': f'本轮不采用{row["name"]}，保持相关设置'}}
    return result


def resolve_actions(scope, templates, answers, required=()):
    by_id = {r['id']: r for r in scope['rows']}
    statuses = {}
    for r in scope['rows']:
        a = answers.get(r['question_id']) if r['role'] == 'candidate' else None
        statuses[r['id']] = {'probability': a['probabilities']['include'] if a else None,
            'model_selected': a['choice'] == 'include' if a else None, 'adopted': False,
            'resolution': '本轮未采用' if a else '按需补齐' if r['role'] == 'prerequisite' else '共同任务'}
    wanted = {i for i, s in statuses.items() if s['model_selected']}
    wanted.update(r['id'] for r in scope['rows'] if r['role'] == 'core')
    required = set(required)
    wanted.update(required)
    # Heat and ventilation are competing settings, not two independent effects.
    heat, vent = key('seat', '座椅加热控制'), key('seat', '座椅通风控制')
    if heat in wanted and vent in wanted:
        loser = vent if statuses[heat]['probability'] >= statuses[vent]['probability'] else heat
        wanted.remove(loser)
        statuses[loser]['resolution'] = ('两项同分，按共同目录顺序去重' if statuses[heat]['probability'] == statuses[vent]['probability']
                                       else '与另一温控动作互斥，保留采用概率较高者')
    emitted, actions, visiting = set(), [], set()
    def emit(i, dependency_for=None):
        if i in emitted:
            return
        if i in visiting or i not in by_id or i not in templates:
            raise ValueError('本轮能力依赖无法满足：'+i)
        visiting.add(i)
        domain, name = i.split('/', 1)
        deps = [key(*PROVIDERS[t]) for t in DEPENDENCIES.get((domain, name), ())]
        if name == '播放控制':
            deps.append(key('entertainment', '内容选择'))
            deps.extend(i for i in (key('entertainment', '音源切换'), key('entertainment', '音量调节')) if i in wanted)
        if domain == 'ambient_light' and name != '氛围灯开关': deps.append(key('ambient_light', '氛围灯开关'))
        if domain == 'climate' and name in {'温度调节', '风量调节', '出风模式切换'}: deps.append(key('climate', '空调开关'))
        for dep in deps: emit(dep, name)
        visiting.remove(i)
        status = statuses[i]
        status['adopted'] = True
        if i in required:
            status['resolution'] = '选中方案的必要步骤'
        elif by_id[i]['role'] == 'core':
            status['resolution'] = '共同任务，具体内容由候选选择决定'
        elif dependency_for and not status['model_selected']:
            status['resolution'] = '依赖补齐：'+dependency_for
        elif status['model_selected']:
            status['resolution'] = 'KEV选择采用'
        a = copy.deepcopy(templates[i])
        a['basis'] = status['resolution'] + (f'；模型采用概率{status["probability"]:.1%}' if status['probability'] is not None else '；不设置模型概率')
        actions.append(a);emitted.add(i)
    # Stable, persona-neutral catalog order; dependency recursion orders prerequisites.
    ordered = sorted(scope['rows'], key=lambda r: 0 if r['domain']=='ecosystem' else 1)
    for row in ordered:
        if row['id'] in wanted: emit(row['id'])
    return actions, [{'id': r['id'], **statuses[r['id']]} for r in scope['rows']]
