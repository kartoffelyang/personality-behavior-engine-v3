"""One bounded neutral call; validated code expands short action recipes."""
import copy,json,re,time,os
from .kev_atomic import action_templates,key,PROVIDERS
from .planner_validation import DEPENDENCIES
from .llm_router import chat_json_with_raw,get_llm

PREPARATION_VERSION='role-recipes-v5'
PREPARATION_MODEL=os.environ.get('KEV_PREPARATION_MODEL','gemini-2.5-flash')

def preparation_model():
    from .local_cloud import preparation_model as configured_model
    return configured_model(PREPARATION_MODEL)

SYSTEM='''为纯虚拟车机演示理解当前输入，并准备3个共享完整候选。候选按决策风格区分，不能只换名称或话术。只输出JSON：
{"situation":{"task":{"core_goal":"核心目标","constraints":[],"must_do":[],"required_steps":["共同必需的末端能力ID，无则空数组"],"choice_space":true},"facts":["原话事实"],"unknowns":[],"context":{"driving_state":"driving或parked或null"}},"shared_options":[{"id":"P1","name":"共同虚拟选项","facts":["25分钟","免费","临江有树荫"]}],"candidates":[{"id":"A","family":"order或feeling或minimal","role_evidence":"结合原话说明本方案的实际取舍，25字以内","selection_basis":{"criterion":"本方案比较标准","facts":["必须引用shared_options的原文事实"],"reason":"为何这些事实支持本方案","tradeoff":"放弃了什么或不额外做什么"},"name":"具体安排","description":"具体结果，60字以内","spoken_reply":"车机会直接说的自然中文，100字以内，含具体对比依据和取舍，先回应需要再说安排，不说演示假设、策略、参数或能力名称","target":"具体目的地（如需导航）","content":"具体曲目（如需播放）","steps":["能力域/原子能力名"]}]}。
先在shared_options给出2–3个共同虚拟候选的具体信息：涉及地点时提供可比较的时间/价格/环境；涉及座舱时给出共同可用内容/设置及具体效果。所有数字、环境和内容均为演示假设，两种角色必须引用同一事实表，不得给同一地点编造不同信息。selection_basis.facts必须逐字选取shared_options.facts的已有条目，不能另编数字；reason明确说明取舍。spoken_reply必须自然包含至少一项具体事实及选择理由，而不是只说选哪个地点。示例表达结构：A“两个地方都能去，这里近15分钟、免费，我先选它，行程更省事”；B“这里虽然稍远，但临江、有树荫，适合和朋友慢慢走，我选这里”。不得套用示例数字，必须用本轮shared_options真实存在的演示事实。
A/order在体验可选时以完成共同任务即停止为默认，不因有疲劳/朋友就自动放音乐；B/feeling若原话表达体验或情绪需要，可选择相关影音/环境支持。两者至少一个相关辅助动作采用不同，不能双方都播放不同的歌就当作行为强对比。明确要求播放、导航等共同动作除外。路线比较与成本比较优先属于order；feeling优先体验或边界支持，不为凑差异反向追加任务比较。
所有候选必须直接给出具体安排，不能问用户想选哪个，不能等待确认或更多输入。B可以内部考虑多种方向，但最终同样选定一个具体安排；三类都满足required_steps。required_steps只包含用户明确任务或安全共同必需步骤，不把比较、音乐等角色可选步骤放入共同必需；只填default_steps中的合法ID，程序自动补齐每个候选的共同任务：如疲劳驾驶必须前往安全休息点时填navigation/导航运行控制；仅推荐时不要擅自启动导航。
候选风格必须包含：A/order明确安排（Te：可比较标准、效率、组织顺序；Si：只用已有事实，不虚构历史）；B/feeling体验与自主（Fi：回应已表达的感受、保护边界；Ne：保留体验弹性）；C/minimal最小介入。三类是人为强化演示风格，不是用户人格判断，所有候选共享相同事实、资源、必需动作。
有选择空间时，A与B必须在相关辅助动作的采用/不采用或有实际含义的参数上不同：如比较路线还是不追加排名、整理通知还是保留私人边界、任务结束即止还是延续已表达的体验。不要只换POI、曲名或说法。不同不等于多调用；每个额外动作都要有当前原话依据。role_evidence必须说明具体取舍与原话的联系。spoken_reply必须对应本方案的实际步骤和结果，A明确直接、B承接感受并留余地；不能只把同一句话换称呼。安全必要步骤三者都保留。
如果用户指令完全确定且没有合理额外选择（例如只把温度设为24度、不要做其他事），choice_space=false，三个方案可一致，不为性格添加操作；其它情境choice_space=true。
所有原话已知事实保留，未知保持未知。候选共同完成安全主线，辅助动作应有合理取舍，不只换地点名。用户明确禁止的动作不能加入。
疲劳驾驶应安排就近安全停靠休息，不能用音乐替代休息。疲劳驾驶时三种候选必须前往同一个最近的安全休息点，不能为了环境、儿童娱乐或性格差异选择更远的休息点；差异只发生在不延迟停靠的辅助支持。仍在驾驶或驾驶状态未知时，不调驾驶位座椅、不按摩、不编排灯效。不能假定已停车或有人照看孩子。
本轮不查真实数据，地点、内容及外部结果均为共同演示假设；description注明假设。spoken_reply像真实车机简短说话，用你和我，不用您、为您、且等公文措辞，不机械复述用户原话；先承接需要再说具体安排，不宣称已到达或已停车。不能发消息、支付或编造历史偏好。
steps优先只选现有default_steps中的能力ID，代码会补齐依赖和参数。其他能力若确有必要，使用{"c":"域","n":"能力名","p":{合法参数},"d":"具体动作"}。最多3个候选，每个最多6个末端steps，不要重复生成全部前置步骤。
不要输出推理、人格分析、长解释或额外字段。'''


def expand_recipe(eng,recipe,situation,compile_plan,text,world):
    from .kev_demo import KevDemoError
    if not isinstance(recipe,dict) or not all(isinstance(recipe.get(k),str) and recipe[k].strip() for k in ('id','name','description')):raise ValueError('候选字段不完整')
    steps=recipe.get('steps');
    if not isinstance(steps,list) or len(steps)>8:raise ValueError('候选步骤不合法')
    context=situation.get('context') or {};parked=context.get('driving_state')=='parked'
    target=recipe.get('target') or '前方可安全停靠点（共同演示候选）'
    content=recipe.get('content') or '轻松纯音乐（共同演示候选）'
    nav=action_templates({'id':recipe['id'],'name':target,'description':recipe['description']},None,'按当前目标选择可行路线')
    cabin=action_templates({'id':recipe['id'],'name':content,'description':recipe['description']},'after_work','本轮共同安排')
    templates={**nav,**{i:a for i,a in cabin.items() if a['c'] not in {'navigation','ecosystem'}}}
    actions=[];emitted=set();custom={}
    wanted=[]
    for step in steps:
        if isinstance(step,str):wanted.append({'ecosystem/候选方案筛选':'ecosystem/候选服务筛选'}.get(step,step))
        elif isinstance(step,dict) and all(k in step for k in ('c','n','p','d')):
            i=key(step['c'],step['n']);custom[i]=dict(step,m='auto_generate');wanted.append(i)
        else:raise ValueError('候选步骤格式不合法')
    templates.update(custom)
    def emit(i):
        if i in emitted:return
        if i not in templates:raise ValueError('动作缺少合法参数模板')
        a=copy.deepcopy(templates[i]);d,n=a['c'],a['n']
        if not parked and (d=='seat' or d=='ambient_light'):raise ValueError('当前行驶状态不允许驻车舒适动作')
        stopping=d=='navigation' and n=='导航运行控制' and (a['p'].get('结束') is True or a['p'].get('暂停') is True) and not a['p'].get('开始') and not a['p'].get('继续')
        if not stopping:
            for dep in DEPENDENCIES.get((d,n),()):emit(key(*PROVIDERS[dep]))
        if d=='entertainment' and n=='播放控制' and a['p'].get('播放') is True:emit(key('entertainment','内容选择'))
        if d=='ambient_light' and n!='氛围灯开关':emit(key(d,'氛围灯开关'))
        if d=='climate' and n in {'温度调节','风量调节','出风模式切换'}:emit(key(d,'空调开关'))
        basis=recipe.get('selection_basis') or {}
        if d=='ecosystem' and n=='候选方案比较' and basis.get('facts'):
            criterion=str(basis.get('criterion') or '')
            facts='；'.join(basis['facts'])
            field='评价' if recipe.get('family')=='feeling' else '时间' if '时间' in criterion or '车程' in criterion else '价格' if '价格' in criterion or '费用' in criterion else '评价'
            a['p']={field:facts}
            a['d']='模拟按'+criterion+'比较：'+facts+'。'
        if n=='路线规划':a['p']['起终点']=('当前驻车点' if parked else '当前行驶位置')+' → '+target
        if n=='导航运行控制':a['d']=('模拟结束当前导航。' if a['p'].get('结束') is True else '模拟暂停当前导航。') if stopping else '模拟启动前往'+target+'的导航，不表示车辆已到达或已停车。'
        a['why']='共享中立候选，尚未做人格选择';a['basis']='参数由能力模板补齐，资源为共同演示假设'
        actions.append(a);emitted.add(i)
    required=(situation.get('task') or {}).get('required_steps') or []
    if not isinstance(required,list) or not all(isinstance(i,str) and i in templates for i in required):
        raise ValueError('共同任务能力不合法')
    # All three styles must reach the same mandatory terminal operations.
    for i in list(dict.fromkeys(required+wanted)):emit(i)
    if len(actions)>24:raise ValueError('展开后动作过多')
    compile_plan(eng,text,world,actions,recipe['description'],situation)
    spoken=recipe.get('spoken_reply')
    return {'id':recipe['id'],'name':recipe['name'],'description':recipe['description'],'actions':actions,
            'family':recipe.get('family') if recipe.get('family') in {'order','feeling','minimal'} else None,
            'role_evidence':str(recipe.get('role_evidence') or '')[:240],
            'selection_basis':copy.deepcopy(recipe.get('selection_basis') or {}),
            'spoken_reply':spoken.strip() if isinstance(spoken,str) and 0<len(spoken.strip())<=180 else None}


def prepare_shared(eng,text,world,compile_plan):
    from .kev_free import cache_path,write_cache
    from .kev_demo import KevDemoError
    started=time.monotonic()
    sample=action_templates({'id':'X','name':'共同候选','description':'共同演示资源'},'after_work','本轮安排')
    path=cache_path('kev_shared_recipes',{'version':PREPARATION_VERSION,'model':PREPARATION_MODEL,'text':text,'world':world,'system':SYSTEM})
    def validate(raw):
        if not isinstance(raw,dict) or not isinstance(raw.get('situation'),dict):raise ValueError('情境格式不合法')
        situation=copy.deepcopy(raw['situation']);situation['shared_options']=copy.deepcopy(raw.get('shared_options') or []);situation.setdefault('task',{});situation.setdefault('context',{})
        if not isinstance(situation['task'],dict) or not isinstance(situation['context'],dict):raise ValueError('情境字段不合法')
        # Explicit moving evidence wins over any generated parked assumption.
        if re.search(r'还在高速|正在.{0,4}开车|仍在.{0,4}高速|还在.{0,4}开车',text):situation['context']['driving_state']='driving'
        if situation['context'].get('driving_state') not in {'driving','parked',None}:raise ValueError('行驶状态不合法')
        errors=[];candidates=[]
        for r in (raw.get('candidates') or [])[:3]:
            try:
                basis=r.get('selection_basis') or {}
                if basis:
                    facts={f for o in situation['shared_options'] for f in o.get('facts',[]) if isinstance(f,str)}
                    if not isinstance(basis,dict) or not isinstance(basis.get('facts'),list):
                        raise ValueError('选择依据格式不合法')
                    normalized=[]
                    for f in basis['facts']:
                        if not isinstance(f,str) or not f.strip():raise ValueError('选择依据缺少事实')
                        matches=[known for known in facts if f==known or f in known]
                        if f in facts:normalized.append(f)
                        elif len(matches)==1:normalized.append(matches[0])
                        else:raise ValueError('选择依据未引用共同候选事实')
                    r=copy.deepcopy(r);r['selection_basis']['facts']=normalized
                c=expand_recipe(eng,r,situation,compile_plan,text,world)
                if c['id'] in {x['id'] for x in candidates}:raise ValueError('重复候选ID')
                candidates.append(c)
            except (ValueError,TypeError,KeyError):errors.append('candidate_contract_invalid')
        if not candidates:raise ValueError('没有通过校验的候选')
        # Do not silently publish two identical roles after losing one family.
        families={c.get('family') for c in candidates}
        if (errors or situation['task'].get('choice_space') is True) and situation['task'].get('choice_space') is not False and not {'order','feeling'}<=families:
            raise ValueError('角色候选不完整，不能以共同方案替代缺失角色')
        return situation,candidates,errors
    try:
        raw=json.loads(path.read_text());situation,candidates,errors=validate(raw);cached=True
    except (OSError,ValueError,TypeError,KeyError):
        catalog={d:[{'name':a['name'],'params':a['core_params']} for a in eng.capabilities.get(d)['atomic_abilities']] for d in eng.capabilities.all_ids()}
        client=get_llm('openai');client.model=PREPARATION_MODEL;client.timeout=35;client.total_timeout=35;client.idle_timeout=20;client.stream=True
        try:
            raw,_=chat_json_with_raw(client,[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps({'text':text,'world':world,'default_steps':list(sample),'catalog':catalog},ensure_ascii=False)}],max_tokens=3000)
            situation,candidates,errors=validate(raw)
        except Exception as e:
            write_cache(path.with_suffix('.failure.json'),{'version':PREPARATION_VERSION,'elapsed_seconds':round(time.monotonic()-started,2),'failure_type':type(e).__name__})
            raise KevDemoError('共享候选准备未完成（单次请求上限35秒，无自动长重试）；请重试或简化描述。') from e
        write_cache(path,raw);cached=False
    seconds=round(time.monotonic()-started,2)
    return {'output':situation,'elapsed_seconds':0,'cache_hit':cached},candidates,{
        'source':'single_neutral_call_and_recipe_compiler','model':PREPARATION_MODEL,'seconds':seconds,'cache_hit':cached,'repair_used':False,
        'valid_candidates':len(candidates),'rejected_candidates':len(errors),'cloud_call_limit_seconds':35,
        'distinct_action_sets':len({frozenset(key(a['c'],a['n']) for a in c['actions']) for c in candidates}),
        'role_families':sorted({c['family'] for c in candidates if c.get('family')})}
