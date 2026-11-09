"""Neutral observation -> eligible atomic opportunities -> Kev -> role utility -> compile.

There are no cloud-authored persona plans or replies in this path. Original
model scores, explicit role utilities and final dependency actions are audited.
"""
import copy
import json
import math
import re
import time
from .kev_atomic import action_templates, key, scope_catalog
from .kev_demo import KevDemoError
from .kev_preparation import expand_recipe, PREPARATION_MODEL, preparation_model
from .kev_input import compact_state, action_impact
from .kev_personality import personality_input
from .llm_router import get_llm, chat_json_with_raw

VERSION = 'kev-intent-choice-v3'
TAGS = {'efficiency','structure','experience','emotional_support','privacy','minimal'}
WEIGHTS = {
    'ESTJ': {'efficiency':1.1,'structure':1.1,'experience':-.9,'emotional_support':-.3,'privacy':.1,'minimal':.4},
    'INFP': {'efficiency':-.7,'structure':-.9,'experience':1.1,'emotional_support':1.1,'privacy':.8,'minimal':.4},
}
SYSTEM = '''你只负责车机模拟的中立情境理解与潜在原子能力发现，不生成任何人格方案、执行顺序或车机回复。
输出JSON：{"situation":{"task":{"core_goal":"核心需要","action_requested":false,"constraints":[],"must_do":[]},"facts":[],"needs":[{"need":"明确任务/隐含需要/情绪需要","evidence":"原话依据","explicit":false}],"unknowns":[],"context":{"driving_state":null}},"opportunities":[{"ability":"域/原子能力名","required":false,"reason":"当前输入为何支持这个能力","tags":["efficiency"],"parameters":{}}],"resources":{"places":[{"name":"虚拟地点","minutes":15,"cost":0,"environment":"具体环境特点","facets":{"certainty":0.8,"structure":0.5,"atmosphere":0.4,"empathy":0.2,"novelty":0.3,"freedom":0.4}}],"media":[{"name":"具体虚拟音频名","description":"内容和适用原因","minutes":10,"facets":{"efficiency":0.5,"value":0.5,"certainty":0.7,"structure":0.5,"atmosphere":0.5,"empathy":0.5,"novelty":0.5,"freedom":0.5}}]}}。
区分原话事实、推测的需要和未知状态；一句话可以同时包含驾驶专注、儿童安抚、成人情绪等多个需要，不得只抓第一个。开车不能专心属于正在驾驶的证据。正在驾驶且注意力明显受影响/疲劳，应把就近安全停靠休息作为共同需要，并提供navigation/导航运行控制机会；娱乐不能替代安全处置。没有危险、也没有出行需求时不要默认推荐服务区。
action_requested仅在用户提出建议、查询、推荐或控制等任务时为true，单纯诉说情绪为false；显式任务至少一个对应末端能力标required=true，推荐地点时搜索和给出选择是共同任务，横向比较是可选策略。
逐个检查给出的完整能力目录，找出当前有依据的相关末端能力；常规前置由代码补齐。required只用于用户明确要求或共同安全必需，辅助支持为optional。tags描述的是当前动作的直接方法，而不是整个任务带来的好心情。查询是效率，横向比较/整理是结构，播放/舒适支持是体验；不能把所有查询比较同时标experience，否则不能表达方法取舍。tags仅描述动作在当前情境的收益：efficiency效率、structure信息整理/比较、experience体验、emotional_support情绪安抚、privacy信息边界、minimal少介入；不出现ESTJ、INFP，不为制造差异增加无关动作。
音乐支持、通知干扰管理、环境舒适等确与需求相关时，可以作为可选机会；未出现待处理消息时，不读取/整理通知内容。用户说消息很多但想清静、没有急事时，能力机会应同时覆盖整理信息与不读取内容先隔离干扰这两种可行方法（例如非紧急屏蔽或提醒延后）；摘要和分类只能作为可选方式，不能因为它们能帮助安排就标成共同必需；只有明确要求整理、摘要、阅读消息才是该方法的必需动作。不要因为能力可用就加入机会。行驶状态未知或正在驾驶时，不提出座椅或氛围灯动作。用户禁止的操作不得提出。无需追问真人确认，缺少非关键槽位可使用明确标注的演示参数。
最多10个有理由的机会。ability必须用目录中的完整ID，不发明名称；已有template_id的能力一般省略parameters；用户明确温度、音量等值时填入parameters。没有模板的能力必须按目录合法参数给出具体模拟参数。单纯聊天允许opportunities为空。
resources是独立的可选演示资料，不是理解意图的前置条件：只有需要地点/导航时才提供3个可行、有明显取舍的虚拟地点，只有可能搜索或播放内容时才提供3个同样适合当前需要但取向不同的具体音频；不需要则空数组。用户明确指定地点或曲目时只提供该项，不为对比替换指定内容。未提供真实数据时，所有地点与内容均为虚拟演示资源，不能声称真实检索到或已经有音频文件。安全停靠只给最近一个地点，不为了体验增加距离。候选都必须满足同样的硬约束，避免一个候选时间、价格、环境都占优。要有真实取舍：高效/经济/确定/结构清楚，与氛围/共情/新颖/自由度之间形成不同组合，不能只换名字。高效率、经济的选项应有具体体验限制（如人多、通用化、停留受限）；高体验/新颖的选项要有具体时间、费用或确定性代价。文字特点和属性分必须一致，不能把第一个候选写得又快又免费又安静又自由，再用分值假造取舍。facets各项为0到1的中立演示属性，efficiency效率、value经济性、certainty确定性、structure结构清楚、atmosphere环境体验、empathy情绪匹配、novelty新颖、freedom自由度；评分描述资料，不出现任何人格标签或预分配答案。保留具体价格/车程/环境或内容特点，供最后引用。信息均为模拟，不查真实服务，不编造个人历史，不发消息或支付。不得输出candidates、spoken_reply或人格描述。'''


def observe(eng, text, world):
    model=preparation_model()
    from .local_cloud import timeout_for
    limit=timeout_for('observation')
    from .kev_free import cache_path, write_cache
    start=time.monotonic()
    path=cache_path('kev_neutral_observation',{'version':VERSION,'model':model,'text':text,'world':world,'prompt':SYSTEM})
    catalog=[{'ability':key(d,a['name']),'definition':a['definition'],'parameters':a['core_params']}
             for d in eng.capabilities.all_ids() for a in eng.capabilities.get(d)['atomic_abilities']]
    templates=action_templates({'id':'X','name':'共同演示资源','description':'共同模拟资料'},'after_work','当前目标')
    try:
        raw=json.loads(path.read_text());cached=True
    except (OSError,ValueError):
        client=get_llm('openai');client.model=model
        client.timeout=min(45,limit);client.total_timeout=limit;client.idle_timeout=min(30,limit);client.stream=True
        try:
            raw,_=chat_json_with_raw(client,[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps({'utterance':text,'simulation_context':world,'catalog':catalog,'template_ids':list(templates)},ensure_ascii=False)}],max_tokens=2800)
        except Exception as exc:
            # Only exception classes are persisted; no upstream bodies/credentials.
            write_cache(path.with_suffix('.failure.json'),{'stage':'observation','failure_type':type(exc).__name__})
            from .kev_final_reply import cloud_failure_notice,cloud_timeout_notice
            quota_notice=cloud_failure_notice(exc,'情境理解')
            message='情境理解请求超时（上限25秒）；尚未进入KEV评分。' if isinstance(exc,TimeoutError) or getattr(exc,'failure_kind','').endswith('timeout') else '情境理解服务未返回可解析结果；尚未进入KEV评分。'
            raise KevDemoError(quota_notice or cloud_timeout_notice(exc,'云端情境理解',limit) or message) from exc
        cached=False
    if not isinstance(raw,dict) or not isinstance(raw.get('situation'),dict) or not isinstance(raw.get('opportunities'),list):
        raise KevDemoError('情境理解返回的结构不完整：需要situation和opportunities；未用预设场景替代。')
    situation=copy.deepcopy(raw['situation']);situation.setdefault('task',{});situation.setdefault('context',{})
    if not isinstance(situation['task'],dict) or not isinstance(situation['context'],dict):raise KevDemoError('情境理解的task或context结构错误。')
    moving=bool(re.search(r'还在高速|正在.{0,4}开车|开车.{0,6}(不能|没法|无法).{0,3}专心|在.{0,3}开车',text))
    if moving:situation['context']['driving_state']='driving'
    elif re.search(r'车已经停好|已经停好车|已驻车|安全停好',text):situation['context']['driving_state']='parked'
    if moving and re.search(r'不能专心|无法专心|没法专心|疲劳|困得|撑不住',text):
        # Explicit immediate driving evidence is a common constraint, never a
        # personality choice or an assumption that parking already happened.
        required=next((o for o in raw['opportunities'] if isinstance(o,dict) and o.get('ability')=='navigation/导航运行控制'),None)
        if required is None:
            raw['opportunities'].append({'ability':'navigation/导航运行控制','required':True,'reason':'原话明确驾驶注意力受影响，先就近安全停靠','tags':['efficiency']})
        else:required['required']=True
        situation['task'].setdefault('must_do',[]).append('先就近安全停靠，不用娱乐替代安全处置')
        situation['task']['safety_stop_required']=True
    if situation['context'].get('driving_state') not in {'driving','parked',None}:situation['context']['driving_state']=None
    if not cached:write_cache(path,raw)
    resources=raw.get('resources') if isinstance(raw.get('resources'),dict) else {}
    return situation,raw['opportunities'],resources,{'source':'neutral_intent_and_opportunities','model':model,'seconds':round(time.monotonic()-start,2),'cache_hit':cached}


def resources_for(resources, pid, safety=False):
    places=[p for p in resources.get('places',[]) if isinstance(p,dict) and isinstance(p.get('name'),str) and p['name'].strip()]
    def numeric(p,k):
        v=p.get(k)
        return float(v) if type(v) in (int,float) and math.isfinite(v) and v>=0 else None
    if safety or pid=='ESTJ':
        chosen=min(places,key=lambda p:(numeric(p,'minutes') if numeric(p,'minutes') is not None else 1e6,numeric(p,'cost') if numeric(p,'cost') is not None else 1e6)) if places else None
    else:
        # Final non-safety choice is supplied by Kev; never force the second POI.
        chosen=places[0] if places else None
    media=next((m for m in resources.get('media',[]) if isinstance(m,dict) and m.get('name')),None)
    return chosen,media


def normalize_parameters(eng,domain,name,params):
    ability=next(a for a in eng.capabilities.get(domain)['atomic_abilities'] if a['name']==name)
    allowed=set(ability['core_params']);result={}
    aliases={'type':'类型','keyword':'关键词','关键词':'名称' if name=='地点搜索' else '关键词'}
    for k,v in params.items():
        if k in {'动作','操作'} and isinstance(v,str):
            action={'开启':'开始' if domain=='navigation' else '播放','启动':'开始','停止':'结束' if domain=='navigation' else '停止'}.get(v,v)
            if action in allowed:result[action]=True
            continue
        k=aliases.get(k,k)
        if k not in allowed:continue
        if k=='开关' and isinstance(v,str) and v.lower() in {'开','开启','on','关','关闭','off'}:v=v.lower() in {'开','开启','on'}
        if k in {'温度','档位','亮度','音量'} and isinstance(v,str):
            match=re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*(?:度|℃|%|档)?\s*',v)
            if match:v=float(match[1]) if '.' in match[1] else int(match[1])
        result[k]=v
    return result


def direct_benefits(domain,name,tags):
    if name in {'候选方案比较','价格与优惠查询','路线比较','通知摘要','通知优先级分类'}:return ['structure']
    if domain in {'entertainment','seat','ambient_light'}:return ['experience']+(['emotional_support'] if 'emotional_support' in tags else [])
    return tags


def discover(eng,text,world,situation,opportunities,resources,compile_plan):
    """Validate each opportunity separately; one bad optional item cannot kill all."""
    catalog={key(d,a['name']) for d in eng.capabilities.all_ids() for a in eng.capabilities.get(d)['atomic_abilities']}
    accepted={};rejected=[]
    aliases={'ecosystem/候选方案筛选':'ecosystem/候选服务筛选'}
    for index,op in enumerate(opportunities[:16]):
        if not isinstance(op,dict):rejected.append({'ability':'unknown','reason':'机会字段不是对象'});continue
        ident=aliases.get(op.get('ability'),op.get('ability'))
        reason=op.get('reason')
        if ident not in catalog or not isinstance(reason,str) or not reason.strip():
            rejected.append({'ability':str(ident),'reason':'能力ID不在目录或缺少情境依据'});continue
        domain,name=ident.split('/',1)
        if domain=='notification_mode' and name in {'通知读取','通知摘要','通知优先级分类'} and not re.search(r'整理|梳理|摘要|总结|分类|读.{0,4}(消息|通知)|看.{0,4}(消息|通知)|消息.{0,4}重点',text):
            op=copy.deepcopy(op);op['required']=False

        if not op.get('required') and re.search(r'不要做其他|别做其他|其他.{0,3}(不用|不要)',text):
            rejected.append({'ability':ident,'reason':'用户明确要求不追加其他操作'});continue
        if situation['context'].get('driving_state')!='parked' and domain in {'seat','ambient_light'}:
            rejected.append({'ability':ident,'reason':'当前行驶状态不允许此驻车动作'});continue
        if domain=='notification_mode' and name in {'回复发送','回拨发起'} or domain=='ecosystem' and name in {'支付与缴费','订单管理','预约与排队管理'}:
            rejected.append({'ability':ident,'reason':'当前纯模拟链路不编排对外通信或交易'});continue
        candidate=copy.deepcopy(op);candidate.update(ability=ident,required=op.get('required') is True,
            tags=[t for t in op.get('tags',[]) if t in TAGS],reason=reason.strip())
        candidate['model_tags']=candidate['tags'][:]
        candidate['tags']=direct_benefits(domain,name,candidate['tags'])
        candidate['recipes']={}
        for pid in ('ESTJ','INFP'):
            place,media=resources_for(resources,pid,safety=situation['context'].get('driving_state')=='driving' and (situation['task'].get('safety_stop_required') or '安全' in str(situation['task'])))
            selected={'id':'X','name':place['name'] if place else '就近可安全停靠点（演示地点）' if situation['task'].get('safety_stop_required') else '本轮建议地点（演示假设）','description':'共同演示资料'}
            template_ids=action_templates(selected,'after_work','当前目标')
            params=op.get('parameters') or {}
            if not isinstance(params,dict):candidate['invalid']='参数不是对象';break
            params=normalize_parameters(eng,domain,name,params)
            if ident in template_ids:
                step=copy.deepcopy(template_ids[ident]);step['p'].update(params)
            else:step={'c':domain,'n':name,'p':params,'d':'模拟'+name+'：'+reason}
            if resources.get('_selection_complete') and media and name=='内容搜索':params['关键词']=media['name']
            if resources.get('_selection_complete') and place and name=='地点搜索':params['名称']=place['name']
            if name in {'导航运行控制','播放控制'} and params:
                step['p']=params.copy()
            # Compile with only structural constraints; cross-op required terminals
            # are checked on the final merged plan, not on each individual item.
            local=copy.deepcopy(situation);local['task']={'core_goal':'编译单项机会'}
            recipe={'id':'X','name':name,'description':'模拟'+reason,'target':selected['name'],
                    'content':media['name'] if media else '轻松纯音乐（演示内容）','steps':[step]}
            if domain=='navigation' or domain=='ecosystem':
                # Named-place templates must use the actual selected target.
                step=action_templates(selected,None,'按当前目标选择可行路线').get(ident,step)
                step=copy.deepcopy(step);step['p'].update(params)
                if name in {'导航运行控制','播放控制'} and params:step['p']=params.copy()
                recipe['steps']=[step]
            if domain=='entertainment' and media:
                step=action_templates({'id':'X','name':media['name'],'description':media.get('description','共同演示音频')},'after_work','当前目标').get(ident,step)
                step=copy.deepcopy(step);step['p'].update(params)
                if name in {'导航运行控制','播放控制'} and params:step['p']=params.copy()
                recipe['steps']=[step]
            try:candidate['recipes'][pid]=expand_recipe(eng,recipe,local,compile_plan,text,world)
            except (ValueError,TypeError,KeyError):candidate['invalid']='原子参数或依赖校验未通过';break
        if candidate.get('invalid'):
            rejected.append({'ability':ident,'reason':candidate['invalid']});continue
        if ident in accepted:
            accepted[ident]['required']|=candidate['required']
        else:accepted[ident]=candidate
    if situation['task'].get('action_requested') is True and accepted and not any(o['required'] for o in accepted.values()):
        core=next((o for o in accepted.values() if o['ability'].split('/')[-1] not in {'通知读取','通知摘要','通知优先级分类','候选方案比较','价格与优惠查询','路线比较'}),None)
        if core:core['required']=True
    # A malformed mandatory opportunity is surfaced, not silently discarded.
    required={aliases.get(o.get('ability'),o.get('ability')) for o in opportunities if isinstance(o,dict) and o.get('required') is True and not (str(o.get('ability','')).split('/')[-1] in {'通知读取','通知摘要','通知优先级分类'} and not re.search(r'整理|梳理|摘要|总结|分类|读.{0,4}(消息|通知)|看.{0,4}(消息|通知)|消息.{0,4}重点',text))}
    if required-set(accepted):raise KevDemoError('能力编译未完成：共同必需动作缺少合法参数或当前不可用（'+ '、'.join(str(i) for i in required-set(accepted))+'）。')
    if opportunities and not accepted:raise KevDemoError('已理解情境，但相关能力未通过编译；没有使用固定场景替代。')
    return accepted,rejected


def priority(raw,tags,pid):
    adjustment=sum(WEIGHTS[pid].get(t,0) for t in tags)/max(1,len(tags))
    logit=math.log(max(.001,min(.999,raw))/max(.001,1-min(.999,raw)))
    return 1/(1+math.exp(-(logit+4*adjustment))),adjustment


def wording(text,pid,actions,situation,resources,selected_ops):
    from .kev_wording import spoken_reply
    place,media=resources_for(resources,pid,safety=situation['context'].get('driving_state')=='driving' and (situation['task'].get('safety_stop_required') or '安全' in str(situation['task'])))
    has_place=any(a['c']=='ecosystem' or a['c']=='navigation' and a['n'] in {'目的地设置','地点搜索','路线规划'} for a in actions)
    facts=[]
    if has_place and place:
        for field,unit in [('minutes','分钟车程'),('cost','元')]:
            if type(place.get(field)) in (int,float):facts.append(str(place[field])+unit)
        if place.get('environment'):facts.append(str(place['environment']))
    if not facts and media and any(a['c']=='entertainment' and a['n']=='播放控制' for a in actions):facts=[media['name'],media.get('description','共同演示内容')]
    criterion='用时、可行性与明确安排' if pid=='ESTJ' else '已表达的感受、体验与介入边界'
    reply,_=spoken_reply(text,{'actions':actions,'description':'这轮先陪你聊聊，不追加车控。'})
    if actions and not any(a['n'] in {'目的地设置','播放控制','温度调节','通知静音控制','通知摘要','非紧急通知屏蔽','提醒延后','座椅按摩控制','氛围灯开关'} for a in actions):reply=''
    control=next((a for a in actions if a['n']=='导航运行控制'),None)
    if control and (control['p'].get('结束') is True or control['p'].get('暂停') is True):reply='已结束当前导航。' if control['p'].get('结束') is True else '已暂停当前导航。'
    playback=next((a for a in actions if a['n']=='播放控制'),None)
    if playback and (playback['p'].get('停止') is True or playback['p'].get('暂停') is True):reply='已停止当前播放。' if playback['p'].get('停止') is True else '已暂停当前播放。'
    nav=next((a for a in actions if a['c']=='navigation' and a['n']=='目的地设置'),None)
    if nav and not any(a['n']=='导航运行控制' for a in actions):
        reply='先把它设为候选目的地。' if not any(a['n']=='路线规划' for a in actions) else '路线已规划，暂不启动导航。'
    if has_place and place:
        if situation['context'].get('driving_state')=='driving' and (situation['task'].get('safety_stop_required') or '安全' in str(situation['task'])):lead='先顾好驾驶，'+place['name']+'是本轮共同的就近停靠安排。'
        elif pid=='ESTJ':lead='我先按用时和可行性选'+place['name']+'，'+('、'.join(facts[:2]))+'，行程更明确。'
        else:lead='我更看重这次想放松的体验，选'+place['name']+'，'+str(place.get('environment') or '保留轻松的活动空间')+'。'
        reply=lead+reply
    if not reply:reply=''.join(a.get('d','') for a in actions[-2:]).replace('模拟','') or '这轮先陪你聊聊，不追加车控。'
    reason='；'.join(op['reason'] for op in selected_ops) or '没有需要执行的车控动作，保留对话。'
    return reply,{'criterion':criterion,'facts':facts,'reason':reason,'tradeoff':'未采用的可选支持保持原设置；共同任务和依赖不因角色风格省略。'}


def run_atomic_pipeline(eng,text,world,jid,on_progress,decide,compile_plan):
    started=time.monotonic();on_progress(stage='understanding')
    situation,opportunities,resources,preparation=observe(eng,text,world)
    on_progress(stage='capabilities',understanding=situation)
    pool,rejected=discover(eng,text,world,situation,opportunities,resources,compile_plan)
    bundles=[{'id':i,'description':o['reason'],'actions':o['recipes']['ESTJ']['actions']} for i,o in pool.items()]
    scope=scope_catalog(eng,None,bundles,text)
    all_rows=scope['rows']+[dict(r,role='excluded',applicable=False) for r in scope['excluded']]
    covered={key(a['c'],a['n']) for o in pool.values() for a in o['recipes']['ESTJ']['actions']}
    for r in all_rows:
        r['applicable']=r['id'] in covered
        if not r['applicable']:r['reason']='中立理解未找到当前需求依据，或能力在当前约束下不可用'
    scope.update(rows=all_rows,excluded=[],mode='atomic_direct',scored_count=len(all_rows),selection_version=VERSION)
    questions={r['question_id']:{'type':'choice','instructions':r['name']+'：'+r['definition']+'；'+action_impact(r['domain'],r['name'])+'。根据当前需要和角色规则采用还是不采用？','criteria':{'include':'采用','omit':'不采用'}} for r in all_rows}
    places={f'P{i+1}':p for i,p in enumerate(resources.get('places',[])[:4]) if isinstance(p,dict) and p.get('name')}
    safety_stop=situation['task'].get('safety_stop_required') or situation['context'].get('driving_state')=='driving' and '安全' in str(situation['task'])
    if len(places)>1 and not safety_stop and any(i.startswith(('navigation/','ecosystem/')) for i in pool):
        questions['resource_place']={'type':'choice','instructions':'依据共同需求与本角色的选择标准，从共同演示地点选一个；原子动作另行评分。','criteria':{i:json.dumps(p,ensure_ascii=False) for i,p in places.items()}}
    media={f'M{i+1}':m for i,m in enumerate(resources.get('media',[])[:4]) if isinstance(m,dict) and m.get('name')}
    if len(media)>1 and any(i.startswith('entertainment/') for i in pool):
        questions['resource_media']={'type':'choice','instructions':'从共同模拟内容池选最符合当前需要和角色标准的内容，具体是否播放由原子动作另行决定。','criteria':{i:json.dumps(m,ensure_ascii=False) for i,m in media.items()}}
    variants={};audits={};on_progress(stage='planning',atomic_scope=scope)
    for pid in ('ESTJ','INFP'):
        personality=personality_input(eng,pid)
        state=compact_state(pid,text,world,bundles,situation)
        state['role'].update(input_version=VERSION)
        state['situation']['needs']=situation.get('needs',[])
        state['situation']['demo_resources']=resources
        state['candidates']={i:{'reason':o['reason'],'required':o['required'],'benefits':o['tags']} for i,o in pool.items()}
        raw,seconds=decide(state,questions)
        role_resources=copy.deepcopy(resources);pid_pool=pool;decisions=[];resource_choices={}
        from .kev_resource_choice import choose
        for kind,options,field in [('place',places,'places'),('media',media,'media')]:
            q='resource_'+kind
            if q not in questions:continue
            answer=raw['answers'][q]
            chosen_id,explanation=choose(options,answer['probabilities'],pid)
            resource_choices[kind]=explanation
            role_resources[field]=[options[chosen_id]]
            decisions.append({'id':'selection' if kind=='place' else 'media_selection','question':'共同'+('地点' if kind=='place' else '内容')+'的原始KEV评分',
                'choice':answer['choice'],'label':options[answer['choice']]['name'],'scores':answer['probabilities'],'options':{i:p['name'] for i,p in options.items()}})
        if resource_choices:
            role_resources['_selection_complete']=True
            pid_pool,_=discover(eng,text,world,situation,opportunities,role_resources,compile_plan)
        scores={r['id']:raw['answers'][r['question_id']]['probabilities']['include'] for r in all_rows}
        selected=[];selection={}
        for ident,op in pool.items():
            value,adjustment=priority(scores[ident],op['tags'],pid)
            adopted=op['required'] or value>=.5
            selection[ident]={'priority':round(value,4),'role_adjustment':round(adjustment,4),'selected':adopted,'required':op['required'],'tags':op['tags'],'reason':op['reason']}
            if adopted:selected.append(op)
        for group in ({'seat/座椅加热控制','seat/座椅通风控制'},
                      {'notification_mode/通知静音控制','notification_mode/非紧急通知屏蔽','notification_mode/提醒延后'}):
            competing=[o for o in selected if o['ability'] in group]
            if len(competing)>1:
                if sum(o['required'] for o in competing)>1 and not all(o['ability'].startswith('notification_mode/') for o in competing):raise KevDemoError('共同必需动作存在互斥：'+ '、'.join(o['ability'] for o in competing))
                keep=max(competing,key=lambda o:(o['required'],selection[o['ability']]['priority']))
                for o in competing:
                    if o is not keep:
                        selected.remove(o);selection[o['ability']]['selected']=False;selection[o['ability']]['conflict']='与'+keep['ability']+'互斥或作用重复，保留优先项'
        actions=[];emitted=set()
        for op in sorted(selected,key=lambda o:not o['required']):
            for a in pid_pool[op['ability']]['recipes'][pid]['actions']:
                ident=key(a['c'],a['n'])
                if ident in emitted:continue
                a=copy.deepcopy(a)
                a['why']=('共同明确任务或安全需要。' if op['required'] else '依据KEV原始参考分与公开强化角色收益选择。')+op['reason']
                a['basis']='机会 '+op['ability']+'；原始参考分 '+str(round(scores[op['ability']]*100,1))+'；角色综合优先分 '+str(round(selection[op['ability']]['priority']*100,1))
                actions.append(a);emitted.add(ident)
        reply,basis=wording(text,pid,actions,situation,role_resources,selected)
        if resource_choices:
            active_place=any(a['c'] in {'navigation','ecosystem'} for a in actions)
            active_media=any(a['c']=='entertainment' for a in actions)
            for kind,active in [('place',active_place),('media',active_media)]:
                if kind in resource_choices:resource_choices[kind]['used']=active
            reasons=[r['reason'] for r in resource_choices.values() if r['used']]
            if reasons:basis['reason']=' '.join(reasons)

        try:plan=compile_plan(eng,text,world,actions,reply,situation)
        except (ValueError,TypeError,KeyError) as exc:raise KevDemoError('动作编排校验未通过：共同任务、参数或互斥条件存在冲突；已保留中立理解，未归因于超时。') from exc
        atomic=[]
        for r in all_rows:
            ident=r['id'];s=selection.get(ident);adopted=ident in emitted
            dependency=adopted and (not s or not s['selected'])
            source='必要前置' if dependency else '互斥或重复取舍' if s and s.get('conflict') else '共同任务' if s and s['required'] else '角色综合优先分采用' if s and adopted else '角色综合优先分未达阈值' if s else '必要前置' if adopted else '本场景不适用'
            reason=('已选末端动作需要此依赖；不受可选动作优先分阈值控制。' if dependency else s['conflict'] if s and s.get('conflict') else s['reason']+'；综合优先分 '+str(round(s['priority']*100,1))+'，采用阈值50。' if s and not s['required'] else s['reason'] if s else '已选末端动作需要此依赖。' if adopted else '中立能力池未发现当前需求依据，不因原始分数高而追加。')
            atomic.append({'id':ident,'probability':scores[ident],'probability_source':'independent_binary_adoption','adopted':adopted,'model_selected':raw['answers'][r['question_id']]['choice']=='include','decision_source':source,'decision_reason':reason,'resolution':reason,'priority_score':s['priority'] if s else None,'role_adjustment':s['role_adjustment'] if s else None})
        display=[{'name':a['name'],'domain':a['capability'],'text':a['details'],'parameters':a['parameters_hint'],'why':a['persona_influence'],'basis':a['decision_basis'],'order':a['order']} for a in plan['behavior_actions']]
        variants[pid]={'persona':pid,'status':'completed','reply':reply,'reply_source':'compiled_after_atomic_selection','selection_basis':basis,'actions':display,'strategy':personality['role_policy']['label'],'dimensions':personality['role_policy']['priorities'],'planner_seconds':seconds,'attempts':1,'atomic_scores':atomic,'atomic_selection':selection,'resource_choices':resource_choices,'decisions':decisions}
        audits[pid]={'model':'kev-0.8b','request':{'state':state,'questions':questions},'raw_response':raw,'authored_utility':{'weights':WEIGHTS[pid],'strength':4,'formula':'sigmoid(logit(raw)+4*mean(tag_weight)); optional>=0.5; required and dependencies bypass threshold'},'atomic_selection':selection,'compiled_plan':plan,'neutral_observation':situation,'resources':role_resources,'resource_choices':resource_choices,'explanation':{'persona':pid,'profile':personality['persona_profile'],'general_strategy':{'action_logic':personality['role_policy']['priorities']},'scene_strategy':{'scenario_strategy':basis['reason']},'source':'中立需求、KEV原始分数、公开角色收益规则、依赖编译'}}
        on_progress(variants=copy.deepcopy(variants))
    on_progress(stage='wording')
    from .kev_final_reply import rewrite_replies
    wording_meta=rewrite_replies(text,situation,variants,audits,resources)
    on_progress(variants=copy.deepcopy(variants))
    differences=sorted({a['name'] for a in variants['ESTJ']['actions']} ^ {a['name'] for a in variants['INFP']['actions']})
    return {'input':text,'world':world,'variants':variants,'audits':audits,'mode':'kev_free_atomic_v2','model':'kev-0.8b','backend':'kev','elapsed_seconds':round(time.monotonic()-started,2),'intent_seconds':preparation['seconds'],'intent_cached':preparation['cache_hit'],'decision_seconds':round(sum(v['planner_seconds'] for v in variants.values()),2),'candidate_preparation':preparation,'wording':wording_meta,'atomic_scope':scope,'observation':situation,'opportunity_pool':[{k:v for k,v in o.items() if k!='recipes'} for o in pool.values()],'rejected_opportunities':rejected,'resources':resources,'contrast':{'mode':'atomic_pipeline','input_version':VERSION,'meaningful_differences':differences,'disclosure':'先中立理解并发现相关能力，再由KEV评分、公开强化角色收益和依赖编译决定动作；共享地点和内容由云端生成模拟资料；KEV评分与公开角色规则共同选择动作及资源，最后云端只改写既定结果。综合优先分为人为规则分，不是模型概率。'},'local_decision_calls':2,'model_calls_minimum':2+(not preparation['cache_hit'])+wording_meta.get('cloud_calls',0)}
