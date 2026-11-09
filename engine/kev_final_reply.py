"""One bounded cloud request writes both replies after decisions are frozen."""
import copy,json,re,time
from .llm_router import get_llm,chat_json_with_raw
from .kev_preparation import PREPARATION_MODEL, preparation_model

SYSTEM='''你是纯模拟车机的最终表达层。两种角色的动作、具体地点/内容和选择依据已经固定，禁止重新决策、添加动作或换推荐对象。只返回JSON {"ESTJ":"自然中文回复","INFP":"自然中文回复"}。
每段50至100字，2至3个短句：先直接回应当前需要，再给具体安排；有地点或内容选择时提到实际选中的名称，并引用输入中可核对的车程/费用/环境/内容特点，说明这次的选择标准与取舍。可比较其他共同候选，但不得另编数字、真实路况、价格、用户历史或已发生的真实执行结果。
ESTJ清楚、直接，说明事实与可执行安排；INFP承接已表达的感受，说明体验/边界与余地，避免两段复制同一句。语气差异不能替代实际动作差异。没有实质选择空间时可以相似，不能编造不同结果。信息是演示资料，不能称“实时查到”，不能暗示存在真实音频文件。叙事里不要出现“模拟”“虚拟”“演示”“假设”等字样；这些是内部运行背景，不是说给用户听的台词。不要说“按参考设定”“根据演示假设”“按照本轮设定”“根据内部规则”等过程说明，直接说具体安排及有事实支持的选择理由，例如更近、费用更低、环境更安静。按自然车机口吻反馈，但不得添加未选动作或无来源事实。使用你和我，不用您、为您等客服腔。不使用人格标签、策略、原子能力、置信度等术语。不要让用户再次确认或补充参数。不写抒情长段、客服腔或“包裹思绪”“满足想象”等修辞。搜索、比较不等于规划路线；没有路线规划动作就不能说路线已规划，没有启动导航动作就不能承诺启动导航，没有播放动作就不能说已播放。
如果只调温度、停止导航等简单明确任务，直接简短回应，不为了字数扩写。'''


def cloud_failure_notice(exc,stage):
    try:
        body=json.loads(getattr(exc,'response_excerpt','') or '{}')
        code=(body.get('error') or {}).get('code')
    except (ValueError,TypeError,AttributeError):code=None
    if code=='insufficient_user_quota':return '云端API账户额度不足，'+stage+'暂不可用；不是输入过于复杂或KEV故障。'
    if getattr(exc,'status_code',None)==403:return '云端API拒绝访问（403），'+stage+'暂不可用；请检查接口账户权限。'
    return None


def cloud_timeout_notice(exc,stage,limit):
    from .llm_diagnostics import diagnostics_from_exception
    details=diagnostics_from_exception(exc) or {}
    if getattr(exc,'category',None)=='timeout' or isinstance(exc,TimeoutError) or str(details.get('failure_kind','')).endswith('timeout'):
        partial=details.get('first_content_ms') is not None
        return stage+'超时（总时限'+str(limit)+'秒；'+('模型已开始输出，但尚未完成' if partial else '未在时限内返回完整内容')+'），不是意图校验失败。'
    return None


def constrain_reply(reply,actions,allowed_numbers=None):
    names={a['name'] for a in actions};repairs=[];kept=[]
    route=bool(names & {'路线规划','路线重新规划','路线选择'})
    playing=any(a['name']=='播放控制' and a['parameters'].get('播放') is True for a in actions)
    starting=any(a['name']=='导航运行控制' and (a['parameters'].get('开始') is True or a['parameters'].get('继续') is True) for a in actions)
    for sentence in re.split(r'(?<=[。！？])',reply):
        if not sentence:continue
        if allowed_numbers is not None and set(re.findall(r'\d+(?:\.\d+)?',sentence))-allowed_numbers:
            repairs.append('移除资料中不存在的数字陈述');continue
        negative=bool(re.search(r'尚未|没有|不(?:会|再|额外|需要)?(?:启动|播放|规划)',sentence))
        route_claim=not route and re.search(r'(?:已|正在|这就|为你|帮你|我).{0,10}(?:规划|设好|设置好|安排好).{0,8}(?:路线|导航)|(?:路线|导航).{0,8}(?:规划好|安排好|设置好)',sentence)
        start_claim=not starting and re.search(r'(?:已|正在|这就|为你|帮你|我).{0,10}(?:启动|开启|开始).{0,4}导航',sentence)
        play_claim=not playing and re.search(r'(?:已|正在|这就|为你|帮你|给你|我|顺便).{0,10}(?:播放|放首|放段|放点|打开了《)',sentence)
        if not negative and (route_claim or start_claim or play_claim):repairs.append('移除未采用动作的执行承诺');continue
        kept.append(sentence)
    return ''.join(kept),repairs


def resource_mentioned(name,reply):
    def canonical(text):
        text=re.sub(r'^[A-Z]\d+\s*(?=《)', '',text)
        text=re.sub(r'[（(](?:虚拟|演示|模拟)[^）)]*[）)]','',text)
        return re.sub(r'[《》\s]','',text)
    return name in reply or canonical(name) in canonical(reply)


def rewrite_replies(text,situation,variants,audits,resources):
    model=preparation_model()
    from .local_cloud import timeout_for
    limit=timeout_for('wording')
    from .kev_free import cache_path,write_cache
    start=time.monotonic();payload={'utterance':text,'situation':situation,'shared_demo_resources':resources,'variants':{}}
    for pid,v in variants.items():
        active_domains={a['domain'] for a in v['actions']}
        selected=copy.deepcopy(audits[pid]['resources'])
        params_text=json.dumps([a['parameters'] for a in v['actions']],ensure_ascii=False)
        if any(a['name']=='目的地设置' for a in v['actions']):
            selected['places']=[p for p in selected.get('places',[]) if p['name'] in params_text]
        payload['variants'][pid]={'actions':[{k:a[k] for k in ('name','text','parameters')} for a in v['actions']],'selected_resources':{
            'places':selected.get('places',[]) if active_domains & {'navigation','ecosystem'} else [],
            'media':selected.get('media',[]) if 'entertainment' in active_domains else []},
            'choice_basis':v['selection_basis'],'resource_choices':{k:{'reason':r['reason'],'used':r.get('used',False)} for k,r in v.get('resource_choices',{}).items()},'draft':v['reply']}
    path=cache_path('kev_final_reply',{'version':1,'model':model,'system':SYSTEM,'payload':payload})
    cloud_calls=0;cached=False;repairs={}
    try:
        try:raw=json.loads(path.read_text());cached=True
        except (OSError,ValueError):
            client=get_llm('openai');client.model=model;client.timeout=min(45,limit);client.total_timeout=limit;client.idle_timeout=min(30,limit);client.stream=True
            cloud_calls=1
            raw,_=chat_json_with_raw(client,[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],max_tokens=650)
        allowed_numbers=set(re.findall(r'\d+(?:\.\d+)?',json.dumps({'utterance':text,'resources':resources,'actions':{pid:p['actions'] for pid,p in payload['variants'].items()}},ensure_ascii=False)))
        for pid in ('ESTJ','INFP'):
            reply=raw.get(pid)
            if isinstance(reply,str):reply,repairs[pid]=constrain_reply(reply,variants[pid]['actions'],allowed_numbers);raw[pid]=reply
            if not isinstance(reply,str) or not reply.strip() or len(reply)>500:raise ValueError('reply_contract')
            if set(re.findall(r'\d+(?:\.\d+)?',reply))-allowed_numbers:raise ValueError('unsupported_numbers')
            for resource in payload['variants'][pid]['selected_resources']['places']:
                # Selected destination must remain visible, not silently replaced.
                if any(a['name'] in {'目的地设置','候选方案选择'} for a in variants[pid]['actions']) and not resource_mentioned(resource['name'],reply):raise ValueError('missing_selected_place')
            for resource in payload['variants'][pid]['selected_resources']['media']:
                if any(a['name']=='播放控制' and a['parameters'].get('播放') for a in variants[pid]['actions']) and not resource_mentioned(resource['name'],reply):raise ValueError('missing_selected_media')
        if not cached:write_cache(path,raw)
        for pid,v in variants.items():
            v['reply']=raw[pid].strip();v['reply_source']='cloud_after_frozen_decision'
            audits[pid]['compiled_plan']['final_explanation']=v['reply']
            audits[pid]['final_wording']={'model':model,'input':copy.deepcopy(payload['variants'][pid]),'reply':v['reply']}
        return {'source':'cloud_after_frozen_decision','model':model,'seconds':round(time.monotonic()-start,2),'cache_hit':cached,'cloud_calls':cloud_calls,'repairs':repairs}
    except Exception as exc:
        # Wording cannot invalidate an already compiled plan or change its actions.
        return {'source':'compiled_fallback','model':model,'seconds':round(time.monotonic()-start,2),'cache_hit':False,'cloud_calls':cloud_calls,'failure_type':type(exc).__name__,
                'notice':(cloud_failure_notice(exc,'最终表达') or cloud_timeout_notice(exc,'云端最终表达',limit) or '表达模型未完成或未通过结果一致性检查。')+'已保留已选动作和本地事实回复。'}
