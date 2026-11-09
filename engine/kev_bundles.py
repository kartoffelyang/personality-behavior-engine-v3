"""Complete-plan decisions. Authored role constraints and raw Kev scores are separate."""
import copy
import time
import math

from .kev_atomic import action_templates, key, scope_catalog
from .kev_personality import personality_input
from .kev_input import compact_state, action_impact, INPUT_VERSION
from .kev_wording import spoken_reply
from .kev_role_scope import role_scope, atomic_decision_reason


POLICIES = {
    'ESTJ': {'label': '恢复秩序型 · 强化演示', 'dominant': 'Te', 'dominant_name': '外倾思维',
        'goal': '先减少外部干扰，让休息有明确边界，以少量实用动作恢复掌控感。',
        'priorities': ['处理通知干扰', '动作少而明确', '不主动营造灯光或身体沉浸体验'],
        'stop': '通知处理与本轮短休息安排完成即停止，不追加座椅和灯光调整。',
        'allowed_family': 'order'},
    'INFP': {'label': '感受恢复型 · 强化演示', 'dominant': 'Fi', 'dominant_name': '内倾情感',
        'goal': '暂时从工作评价中抽离，用音乐、柔和环境和身体放松恢复体验。',
        'priorities': ['选择具体音乐', '柔和灯光与身体放松组成体验', '保持通知设置，不替用户管理工作事务'],
        'stop': '完成本轮恢复体验后留白，不追加事务整理、通知管理或催促。',
        'allowed_family': 'feeling'},
}


def rest_bundles(resources):
    by_id = {r['id']: r for r in resources}
    specs = [
        ('O1', 'order', '静音＋短播客', '静音非紧急通知25分钟，播放《换个角度看今天》15分钟，不改灯光和座椅。', 'M2', ['通知静音控制']),
        ('O2', 'order', '屏蔽干扰＋短播客', '屏蔽非紧急通知25分钟，保留紧急通知，播放《换个角度看今天》，不改灯光和座椅。', 'M2', ['非紧急通知屏蔽']),
        ('O3', 'order', '延后提醒＋安静休息', '将非紧急提醒延后25分钟，不播放内容，不改灯光和座椅，留出安静时段。', None, ['提醒延后']),
        ('F1', 'feeling', '钢琴＋暖光＋按摩', '播放《雨后钢琴》12分钟，开启暖白20%氛围灯与轻柔按摩，通知设置不变。', 'M1', ['座椅按摩控制']),
        ('F2', 'feeling', '钢琴＋暗暖光＋舒展', '播放《雨后钢琴》12分钟，开启暖白15%氛围灯，驻车时轻微后倾靠背，通知设置不变。', 'M1', ['座椅位置调节']),
        ('F3', 'feeling', '轻快音乐＋暖光＋按摩', '播放《城市慢游》10分钟，开启暖白20%氛围灯与轻柔按摩，通知设置不变。', 'M3', ['座椅按摩控制']),
    ]
    bundles = []
    for ident, family, name, description, content_id, extras in specs:
        selected = by_id[content_id] if content_id else by_id['M2']
        templates = action_templates(selected, 'after_work', '完成后安静休息')
        ids = []
        if content_id:
            ids += [key('entertainment', n) for n in ('内容搜索', '内容选择', '播放控制')]
        if family == 'feeling':
            ids += [key('ambient_light', n) for n in ('氛围灯开关', '颜色设置', '亮度调节')]
        ids += [key('notification_mode' if family == 'order' else 'seat', n) for n in extras]
        actions = [copy.deepcopy(templates[i]) for i in ids]
        if ident == 'F2':
            next(a for a in actions if a['n']=='亮度调节')['p']['亮度'] = 15
            next(a for a in actions if a['n']=='亮度调节')['d'] = '模拟将氛围灯亮度设为15%。'
        bundles.append({'id': ident, 'name': name, 'description': description, 'family': family, 'actions': actions,
            'selection_basis':{'criterion':'干扰减少与明确休息边界' if family=='order' else '情绪与身体恢复',
                'facts':[description],'reason':'先处理非紧急提醒，留出明确的休息时段。' if family=='order' else '把音乐与驻车舒适组合起来，不替你处理工作内容。',
                'tradeoff':'不追加灯光和座椅调整。' if family=='order' else '保持通知设置，不整理私人消息。'}})
    return bundles


def feature_set(actions):
    """Count experience decisions, never count light on/color/brightness three times."""
    result = set()
    for a in actions:
        d, n = a['c'], a['n']
        if d == 'notification_mode' and n in {'通知静音控制','非紧急通知屏蔽','提醒延后'}: result.add('通知管理')
        elif d == 'ambient_light': result.add('灯光氛围')
        elif d == 'seat': result.add('身体放松')
        elif d == 'entertainment' and n == '播放控制': result.add('内容播放')
        elif d == 'ecosystem' and n in {'候选方案比较','价格与优惠查询'}: result.add('成本比较')
        elif d == 'navigation' and n == '路线比较': result.add('路线比较')
        elif d == 'navigation' and n == '导航运行控制': result.add('导航启动')
        elif d == 'climate': result.add('环境通风')
        elif d == 'notification_mode' and n in {'通知读取','通知摘要','通知优先级分类'}: result.add('通知梳理')
    return result


def evaluate_bundles(eng, text, world, bundles, scope, decide, compile_plan, on_progress, strong=False, understanding=None, policies=None, groups=None, minimum_differences=3, score_atoms=False):
    for bundle in bundles:
        compile_plan(eng, text, world, bundle['actions'], bundle['description'], understanding)
    universe = {key(a['c'],a['n']) for b in bundles for a in b['actions']}
    scope = copy.deepcopy(scope)
    if score_atoms:
        scope['rows'] += [{**r,'role':'excluded','applicable':False} for r in scope['excluded']]
        for r in scope['rows']:r.setdefault('applicable',True)
        scope['excluded']=[]
    scope.update(mode='bundle_contrast' if strong else 'bundle_selection', bundle_count=len(bundles),
                 probability_kind='sum_of_raw_bundle_scores', scored_count=0,
                 covered_count=sum(r['id'] in universe for r in scope['rows']))
    if score_atoms:scope.update(mode='atomic_bundle_contrast',probability_kind='independent_binary_adoption',scored_count=len(scope['rows']))
    variants, audits = {}, {}
    on_progress(stage='planning', atomic_scope=scope)
    questions = {'bundle': {'type': 'choice',
        'instructions': '依据本轮情境、共同约束与角色规则选择完整方案。若role含authored_demo_scope，只能选择其allowed_plan_ids内的方案。每项是完整安排，不能任意叠加。',
        'criteria': {b['id']: b['name'] for b in bundles}}}
    if score_atoms:
        for row in scope['rows']:
            questions[row['question_id']]={'type':'choice','instructions':f'{row["domain_label"]}/{row["name"]}: {row["definition"]}; {action_impact(row["domain"],row["name"])}. Use or skip?',
                'criteria':{'include':'Use','omit':'Skip'}}
    for pid in ('ESTJ','INFP'):
        policy = (policies or POLICIES)[pid] if (strong or policies) else {
            'label': '目标与效率优先' if pid=='ESTJ' else '体验与自主优先',
            'goal': '依据明确标准，收敛任务，优先可行性和效率。' if pid=='ESTJ' else '依据个人认可与体验，保留弹性，避免无关介入。',
            'boundary': '用户明确要求及当前情境先于风格；不制造与任务冲突的差异。'}
        personality=personality_input(eng,pid) # Full source retained for audit only.
        if not strong:policy=copy.deepcopy(personality['role_policy'])
        scene_policy=role_scope(pid,bundles,policy,understanding)
        state=compact_state(pid,text,world,bundles,understanding,scene_policy)
        raw, seconds = decide(state, questions)
        answer = raw['answers']['bundle']; probs = answer['probabilities']
        eligible = [b for b in bundles if b['id'] in scene_policy['allowed_plan_ids']]
        atomic_probabilities={r['id']:raw['answers'][r['question_id']]['probabilities']['include'] for r in scope['rows']} if score_atoms else {}
        ranking=rank_bundles(bundles,atomic_probabilities,universe) if score_atoms else {}
        selected = max(eligible, key=lambda b: (ranking[b['id']],probs[b['id']])) if score_atoms else max(eligible,key=lambda b:probs[b['id']])
        atomic_preferred=max(bundles,key=lambda b:(ranking[b['id']],probs[b['id']]))['id'] if score_atoms else answer['choice']
        overridden = selected['id'] != atomic_preferred
        actions = copy.deepcopy(selected['actions'])
        explanation = (scene_policy['reason']+' '+policy['goal']+' 本轮取舍：'+(selected.get('role_evidence') or selected['description'])+
                       ' KEV原始分数保持不变，在角色允许范围内按组合匹配度选择。')
        for a in actions:
            a['why'] = ('KEV独立评分原子能力；' if score_atoms else 'KEV独立判断完整方案；')+explanation
            a['basis'] = f'完整方案 {selected["id"]}；'+(f'原子组合匹配度 {ranking[selected["id"]]:.1%}' if score_atoms else f'原始方案分数 {probs[selected["id"]]:.1%}')
        reply,reply_source=spoken_reply(text,selected)
        plan = compile_plan(eng, text, world, actions, reply, understanding)
        actual = {key(a['capability'],a['name']) for a in plan['behavior_actions']}
        raw_choice_ids = {key(a['c'],a['n']) for b in bundles if b['id']==answer['choice'] for a in b['actions']}
        atomic = []
        for row in scope['rows']:
            covered = row['id'] in universe
            probability = sum(probs[b['id']] for b in bundles if any(key(a['c'],a['n'])==row['id'] for a in b['actions'])) if covered and row['role']=='candidate' else None
            if score_atoms:probability=atomic_probabilities[row['id']]
            decision_source,decision_reason=atomic_decision_reason(row,row['id'] in actual,bundles,eligible,selected['id'],scene_policy['applied'])
            atomic.append({'id': row['id'],'decision_source':decision_source,'decision_reason':decision_reason, 'probability': round(probability,4) if probability is not None else None,
                'probability_source': 'independent_binary_adoption' if score_atoms else 'raw_bundle_marginal' if probability is not None else 'not_scored',
                'model_selected': raw['answers'][row['question_id']]['choice']=='include' if score_atoms else row['id'] in raw_choice_ids if covered else None, 'adopted': row['id'] in actual,
                'resolution': '选中完整方案包含此项'+('；经过角色策略约束' if scene_policy['applied'] else '') if row['id'] in actual else
                              '本轮选中方案不包含此项' if covered else ('本场景不适用：'+row['reason'] if not row.get('applicable',True) else '当前完整方案池未覆盖，不单独追加') if score_atoms else '共享完整方案未覆盖，未评分'})
        display = [{'name': a['name'], 'domain': a['capability'], 'text': a['details'], 'parameters': a['parameters_hint'],
                    'why': a['persona_influence'], 'basis': a['decision_basis'], 'order': a['order']} for a in plan['behavior_actions']]
        profile=copy.deepcopy(personality['persona_profile'])
        decision = {'id':'bundle','question':questions['bundle']['instructions'],'choice':answer['choice'],
                    'label': questions['bundle']['criteria'][answer['choice']], 'scores':probs, 'options':questions['bundle']['criteria']}
        variants[pid] = {'persona':pid,'status':'completed','reply':reply,'reply_source':reply_source,'actions':display,'strategy':policy['label'],
            'dimensions':policy.get('priorities',[]),'assumptions':['完整方案均为共享演示候选，话术由程序组织。',explanation],
            'planner_seconds':seconds,'attempts':1,'atomic_scores':atomic,'decisions':[decision],
            'bundle_choice':{'raw_choice':answer['choice'],'selected':selected['id'],'name':selected['name'],
                'input_version':INPUT_VERSION,'runtime_input':copy.deepcopy(state),'policy_version':personality['policy_version'],'scene_role_constraints':scene_policy,'injected_role_policy':copy.deepcopy(personality['role_policy']),'original_persona_profile':copy.deepcopy(personality['persona_profile']),
                'policy_overridden':overridden,'role_scope_applied':scene_policy['applied'],'selection_basis':copy.deepcopy(selected.get('selection_basis') or {}),'selection_effect':'角色规则限定为此方案' if len(eligible)==1 and scene_policy['applied'] else 'KEV组合匹配度排序','eligible':[b['id'] for b in eligible],'role_policy':policy,'atomic_preferred':atomic_preferred,'atomic_fit_scores':ranking,'selection_method':'atomic_log_likelihood' if score_atoms else 'raw_bundle_probability'}}
        audits[pid] = {'persona':pid,'model':'kev-0.8b','request':{'state':state,'questions':questions},'raw_response':raw,
            'shared_bundles':bundles,'eligible_bundles':[b['id'] for b in eligible],'selection':variants[pid]['bundle_choice'],
            'personality_input':personality,'scene_role_constraints':scene_policy,'compiled_plan':plan,'atomic_scope':scope,'atomic_scores':atomic,'explanation':{
                'persona':pid,'profile':profile,'general_strategy':{'action_strategy':policy['label'],'action_logic':policy.get('priorities',[])},
                'scene_strategy':{'action_strategy':policy['label'],'scenario_strategy':policy['goal'],'cross_scenario_prototype':policy['goal']},
                'dimensions':[],'personality_input':personality,'scene_role_constraints':scene_policy,'source':'本轮实际注入的原始人格画像、通用强化规则、独立场景约束与KEV原始分数',
                'attribution':'authored_role_constraints_not_personality_science'}}
        on_progress(variants=dict(variants))
    difference = sorted(feature_set(next(b['actions'] for b in bundles if b['id']==variants['ESTJ']['bundle_choice']['selected'])) ^
                        feature_set(next(b['actions'] for b in bundles if b['id']==variants['INFP']['bundle_choice']['selected'])))
    if strong and len(difference)<minimum_differences:
        raise ValueError('强化演示未达到设定的动作分歧要求，未伪造差异')
    return variants, audits, scope, {'mode':'strong_contrast' if strong else 'atomic_comparison' if score_atoms else 'free_bundle_selection',
        'input_version':INPUT_VERSION,'policy_version':personality['policy_version'],'label':'强对比角色演示' if strong else '自由情境 · 同一套原子评分' if score_atoms else '自由输入完整方案选择','meaningful_differences':difference,
        'role_scope_version':'role-scope-v1','unified_role_rules':True,
        'groups':groups,'score_method':('独立原子能力采用倾向→完整方案匹配度→角色约束内选择' if strong else '共同任务校验→57项原始评分→强化角色允许范围→方案匹配度排序') if score_atoms else '原始完整方案评分',
        'policy_override_count':sum(v['bundle_choice']['policy_overridden'] for v in variants.values()),
        'disclosure':'预设与自由输入使用同一套强化角色筛选。原始KEV分数不改写，最终采用包含人为角色规则；这不是模型自然发现的MBTI规律。'}


def run_strong_rest(eng, text, world, resources, decide, compile_plan, on_progress):
    started = time.monotonic()
    bundles = rest_bundles(resources)
    scope = scope_catalog(eng,'after_work',resources,text)
    variants, audits, scope, contrast = evaluate_bundles(eng,text,world,bundles,scope,decide,compile_plan,on_progress,strong=True)
    return {'input':text,'world':world,'variants':variants,'audits':audits,'mode':'kev_strong_contrast',
        'model':'kev-0.8b','backend':'kev','wording_source':'selected_bundle_compiler',
        'elapsed_seconds':round(time.monotonic()-started,2),'intent_seconds':0,'intent_cached':False,
        'intent_source':'explicit_preset_scene','candidate_preparation':{'source':'shared_authored_bundles','seconds':0},
        'atomic_scope':scope,'contrast':contrast,'model_calls_minimum':2,'local_decision_calls':2}


def rank_bundles(bundles, probabilities, universe):
    """Geometric binary compatibility, not normalized bundle probabilities.

    Only atoms covered by the shared plan pool participate. Unrelated capability
    scores remain visible but cannot reward adding irrelevant actions.
    """
    result={}
    for b in bundles:
        included={key(a['c'],a['n']) for a in b['actions']}
        terms=[math.log(max(1e-6,min(1-1e-6,probabilities[i] if i in included else 1-probabilities[i]))) for i in universe]
        result[b['id']]=math.exp(sum(terms)/len(terms)) if terms else 1.0
    return result


def run_strong_scene(eng,text,world,resources,case_id,decide,compile_plan,on_progress):
    from .kev_scenes import scene_definition
    started=time.monotonic()
    bundles,policies,groups,minimum=scene_definition(case_id,resources)
    scope=scope_catalog(eng,case_id,bundles,text)
    variants,audits,scope,contrast=evaluate_bundles(eng,text,world,bundles,scope,decide,compile_plan,on_progress,
        strong=True,policies=policies,groups=groups,minimum_differences=minimum,score_atoms=True)
    return {'input':text,'world':world,'variants':variants,'audits':audits,'mode':'kev_strong_contrast',
        'model':'kev-0.8b','backend':'kev','wording_source':'selected_bundle_compiler',
        'elapsed_seconds':round(time.monotonic()-started,2),'intent_seconds':0,'intent_cached':False,
        'intent_source':'explicit_preset_scene','candidate_preparation':{'source':'shared_authored_bundles','seconds':0},
        'atomic_scope':scope,'contrast':contrast,'model_calls_minimum':2,'local_decision_calls':2}
