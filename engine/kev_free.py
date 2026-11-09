"""Free text -> neutral facts -> validated complete candidates -> local Kev."""
import copy
import hashlib
import json
import time

from .persona_demo import ROOT
from .pipeline import default_request_scaffold
from .model_intent import run_intent, current_intent_system
from .model_planner import build_step7_capability_catalog
from .llm_router import get_llm, chat_json_with_raw
from .kev_atomic import scope_catalog, key
from .kev_bundles import evaluate_bundles

FREE_WORLD = ('用户原话是事实来源。未提及的行驶状态、乘员人数、时间地点和历史偏好保持未知；'
              '不得擅自假定已经驻车、独自乘坐或儿童已离车。缺少的内容与地点资源可补充共同虚拟候选，'
              '必须标明演示假设且不覆盖已知事实。全部操作仅为模拟，不连接实车，不向真人追问确认。')


def cache_path(kind, data):
    digest = hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return ROOT/'output'/'persona_demo'/kind/(digest+'.json')


def write_cache(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    # Distinct temp names make concurrent identical free inputs safe.
    import uuid
    tmp = path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.replace(path)


def free_client():
    client=get_llm('openai')
    if client is not None:
        client.timeout=75
        client.total_timeout=75
        client.stream=True
    return client


def free_observation(eng,text,world,jid):
    path=cache_path('kev_free_intent',{'v':1,'text':text,'world':world,'prompt':current_intent_system()})
    started=time.monotonic()
    try:
        intent=json.loads(path.read_text());cached=True
    except (OSError,ValueError):
        request=default_request_scaffold('NO_MBTI',text,jid+'_intent')
        request['context']={};request['history']={}
        intent=None
        for attempt in range(2):
            intent=run_intent(request,'openai',free_client(),eng.scenarios)
            if not intent.get('fallback'):break
        if intent.get('fallback'):
            from .kev_demo import KevDemoError
            raise KevDemoError('自由输入的情境理解未完成，未使用固定场景替代；请重试。')
        write_cache(path,intent);cached=False
    intent=copy.deepcopy(intent)
    intent['elapsed_seconds']=round(time.monotonic()-started,2);intent['cache_hit']=cached
    return intent,cached


PREPARE_SYSTEM = '''为纯虚拟车机场景准备3个均满足用户明确要求的完整行动方案。它们是两种角色共享的中立候选，不按人格预分配。
已知驾驶状态、乘员、儿童、截止时间不得覆盖。未提及的信息保持未知；没有真实数据时地点、路线、内容须标为共同演示假设，不冒充真实查询结果。
不得索取真人确认；模拟缺失信息可合理补足，但不得改变正在驾驶/有乘客等原话事实。
驾驶中或行驶状态未知时，不编排驾驶位靠背、座椅位置调节、按摩或沉浸灯效；疲劳驾驶不得用娱乐代替必要的安全处置。
优先完成目标，给出有意义的行动取舍。三个候选不应只是更换地点名：共同必需任务相同，但合理情况下应存在相关辅助动作的采用/不采用差异。可考虑减少通知干扰、影音陪伴或环境舒适；这些必须与原话相关，不为凑差异增加无关能力。安全主线不能被替换为娱乐。遵守用户明确的“不用/不要”要求。
只输出JSON：{"candidates":[{"id":"A","name":"具体方案名称","description":"直接演出本方案的具体安排与模拟结果","actions":[{"c":"能力域ID","n":"合法原子能力名称","p":{},"d":"具体模拟动作，20字以内"}]}]}。
使用A/B/C，不增加其他顶层字段。每个方案0至12个动作。name不超过12字，description不超过70字。不要重复解释，不输出why/basis/m字段，由程序统一补齐。参数只能使用能力库core_params，数值字段使用数值。
补齐地点搜索→详情→设置目的地→路线规划→路线选择→启动导航等真实依赖；纯聊天才允许空动作。
候选中不要生成没有参数方案的无效操作；不得编造用户历史设置、支付、发送或已完成的真实车辆执行。'''


def prepare_free_candidates(eng,text,world,intent,compile_plan):
    catalog=build_step7_capability_catalog([{'capability':c,**eng.capabilities.get(c)} for c in eng.capabilities.all_ids()])
    context=(intent.get('output') or {}).get('context') or {}
    driving=context.get('driving_state')
    path=cache_path('kev_free_candidates',{'v':2,'text':text,'world':world,'intent':intent.get('output'),
                                         'catalog':catalog,'prompt':PREPARE_SYSTEM})
    def valid(candidate):
        if not isinstance(candidate,dict) or not all(isinstance(candidate.get(k),str) and candidate[k].strip() for k in ('id','name','description')):
            raise ValueError('候选字段不完整')
        if not isinstance(candidate.get('actions'),list) or len(candidate['actions'])>16:raise ValueError('动作数组不合法')
        if driving!='parked' and any(a.get('n') in {'座椅位置调节','座椅按摩控制','灯光效果切换'} for a in candidate['actions']):
            raise ValueError('驾驶中或状态未知时不能调整驾驶位休息姿态或编排沉浸灯效')
        for a in candidate['actions']:
            a.update(m='auto_generate',why='共享候选，不做人格归因',basis='用户目标和已知事实')
        compile_plan(eng,text,world,candidate['actions'],candidate['description'],intent.get('output'))
        return candidate
    started=time.monotonic()
    try:
        candidates=json.loads(path.read_text());[valid(c) for c in candidates]
        return candidates,{'source':'shared_neutral_llm_candidates','seconds':round(time.monotonic()-started,2),'cache_hit':True,'distinct_action_sets':len({frozenset(key(a['c'],a['n']) for a in c['actions']) for c in candidates})}
    except (OSError,ValueError,KeyError,TypeError):pass
    accepted={};failures=[];raw_previous=None
    for attempt in range(2):
        data={'text':text,'world':world,'understanding':intent.get('output'),'capabilities':catalog}
        if attempt:
            data.update(previous_candidates=raw_previous,validation_errors=failures,
                        repair='修正参数和依赖错误，保持原话事实，返回完整的三个合法候选。')
        try:
            result,_=chat_json_with_raw(free_client(),[{'role':'system','content':PREPARE_SYSTEM},
                {'role':'user','content':json.dumps(data,ensure_ascii=False)}],max_tokens=4500)
            raw_previous=result.get('candidates',[])
            if not isinstance(raw_previous,list):raise ValueError('candidates必须为数组')
            failures=[]
            for c in raw_previous[:5]:
                try:
                    c=valid(c);accepted[c['id']]=c
                except (ValueError,KeyError,TypeError) as exc:failures.append(str(exc)[:500])
            if len(accepted)>=2:break
        except Exception:
            failures=['候选生成或校验未成功，重新生成合法JSON与能力参数。']
    if not accepted:
        from .kev_demo import KevDemoError
        raise KevDemoError('情境已理解，但共享候选的参数或依赖仍未通过校验；未用固定场景替代，请重试。')
    candidates=list(accepted.values())[:3]
    write_cache(path,candidates)
    return candidates,{'source':'shared_neutral_llm_candidates','seconds':round(time.monotonic()-started,2),
                       'cache_hit':False,'valid_candidates':len(candidates),'repair_used':attempt>0,
                       'distinct_action_sets':len({frozenset(key(a['c'],a['n']) for a in c['actions']) for c in candidates})}


def run_free_demo(eng,text,world,jid,on_progress,decide,compile_plan):
    started=time.monotonic();on_progress(stage='preparing')
    from .kev_preparation import prepare_shared
    intent,bundles,preparation=prepare_shared(eng,text,world,compile_plan)
    cached=preparation['cache_hit']
    scope=scope_catalog(eng,None,bundles,text)
    policies=free_policies()
    groups=free_groups(eng,bundles,policies)
    variants,audits,scope,contrast=evaluate_bundles(eng,text,world,bundles,scope,decide,compile_plan,
                                                on_progress,strong=False,understanding=intent.get('output'),score_atoms=True,policies=policies,groups=groups)
    # Count displayed experience categories, rather than technical substeps.
    def signature(a):
        import re
        params={k:re.sub(r'^[A-Z]\d*\s+','',value) if isinstance(value,str) and k in {'POI','候选项','内容'} else value for k,value in a['parameters'].items()}
        return a['name'],json.dumps(params,ensure_ascii=False,sort_keys=True)
    used={p:{g[0]:{signature(a) for a in v['actions'] if a['domain']==g[1] and (not g[2] or a['name'] in g[2])} for g in groups} for p,v in variants.items()}
    contrast['meaningful_differences']=sorted(g[0] for g in groups if used['ESTJ'][g[0]]!=used['INFP'][g[0]])
    contrast['candidate_action_sets']=preparation.get('distinct_action_sets')
    for a in audits.values():a['neutral_observation']=intent
    return {'input':text,'world':world,'variants':variants,'audits':audits,'mode':'kev_free_atomic',
        'model':'kev-0.8b','backend':'kev','wording_source':'shared_candidate_and_bundle_compiler',
        'elapsed_seconds':round(time.monotonic()-started,2),'intent_seconds':intent['elapsed_seconds'],
        'intent_cached':cached,'decision_seconds':round(sum(v['planner_seconds'] for v in variants.values()),2),'candidate_preparation':preparation,'atomic_scope':scope,'contrast':contrast,
        'model_calls_minimum':2+(0 if cached else 1),'local_decision_calls':2}


def free_policies():
    from .kev_personality import role_policies
    return role_policies()


def free_groups(eng,bundles,policies):
    """Project actual shared candidates into the same concise comparison view."""
    union={key(a['c'],a['n']) for b in bundles for a in b['actions']}
    preferred={'navigation':['目的地设置','路线比较','导航运行控制','迟到风险判断'],
               'ecosystem':['候选服务筛选','候选方案比较','候选方案选择'],
               'entertainment':['播放控制','音量调节'],
               'notification_mode':['通知静音控制','非紧急通知屏蔽','提醒延后']}
    result=[]
    for domain in eng.capabilities.all_ids():
        available=[i.split('/',1)[1] for i in sorted(union) if i.startswith(domain+'/')]
        if not available:continue
        optional=[n for n in available if sum(any(a['c']==domain and a['n']==n for a in b['actions']) for b in bundles)<len(bundles)]
        names=list(dict.fromkeys([n for n in preferred.get(domain,available) if n in available]+optional)) or available
        result.append([eng.capabilities.get(domain)['display_name'],domain,names,
                       [policies[p]['goal'] for p in ('ESTJ','INFP')]])
    return result
