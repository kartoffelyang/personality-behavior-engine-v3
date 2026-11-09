"""Stateful local demonstration transport. No hardware or network calls.

Service/method names in this module are OUR demonstration contract, not an OEM
SOA dictionary. Every response, candidate and initial state is simulated.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import re
import time
import uuid
from .registries import CapabilityRegistry
from .fast_planner import PREPARATION_ACTIONS

REGISTRY = CapabilityRegistry()
MEDIA = [
    {'id':'sim-media-1','name':'演示·轻音乐片段','type':'音乐','duration_seconds':180,'author':'演示内容库'},
    {'id':'sim-media-2','name':'演示·轻松话题','type':'播客','duration_seconds':600,'author':'演示内容库'},
    {'id':'sim-media-3','name':'演示·趣味故事','type':'故事','duration_seconds':300,'author':'演示内容库'},
]
POIS = [{'id':'sim-poi-1','name':'演示目的地A','entrance':'演示入口A'}, {'id':'sim-poi-2','name':'演示目的地B','entrance':'演示入口B'}]
SERVICES = [
    {'id':'sim-service-1','name':'禾间简餐（演示）','type':'餐厅','dish':'香菇鸡肉饭 + 时蔬汤','distance_km':1.2,'price':45,'rating':4.3,'open':True,'wait_minutes':5,'detour_minutes':4,'meal_minutes':20,'description':'简餐套餐，适合短暂停留后继续行程','data_source':'固定演示数据，非真实商户'},
    {'id':'sim-service-2','name':'小院面馆（演示）','type':'餐厅','dish':'番茄牛肉面 + 凉拌小菜','distance_km':2.1,'price':32,'rating':4.1,'open':True,'wait_minutes':10,'detour_minutes':7,'meal_minutes':25,'description':'面食套餐，价格较低，预计多花一些时间','data_source':'固定演示数据，非真实商户'},
    {'id':'sim-service-3','name':'林间休息点（演示）','type':'休息点','distance_km':3.4,'price':0,'rating':4.0,'open':True,'description':'休息设施，不提供餐饮','data_source':'固定演示数据，非真实设施'}]

ROUTES = [{'id':'sim-route-1','label':'演示路线A','minutes':20,'distance_km':12,'toll':10},
          {'id':'sim-route-2','label':'演示路线B','minutes':28,'distance_km':14,'toll':0}]
# Required arguments of this demo adapter; these are not OEM vehicle ranges.
REQUIRED = {
 '空调开关':['开关'], '温度调节':['温度'], '风量调节':['档位'], '出风模式切换':['吹脸'],
 '空气净化开关':['开关'], '前挡除雾开关':['开关'], '后挡除雾开关':['开关'],
 '内容选择':['内容'], '播放控制':['播放'], '音量调节':['音量'], '音源切换':['车机'],
 '氛围灯开关':['开关'], '颜色设置':['颜色'], '亮度调节':['亮度'], '灯光效果切换':['静态'],
 '座椅位置调节':['靠背'], '座椅加热控制':['开关','档位'], '座椅通风控制':['开关','档位'], '座椅按摩控制':['开关','模式','强度'],
 '目的地设置':['POI'], '路线选择':['路线'], '导航运行控制':['开始'], '迟到风险判断':['任务时间'], '途经点管理':['地点'],
 '通知静音控制':['开关'], '非紧急通知屏蔽':['屏蔽范围'], '提醒延后':['延后时间'],
 '回复草稿生成':['内容'], '回复发送':['接收人','内容'], '回拨发起':['号码'],
 '候选方案选择':['候选项'], '预约与排队管理':['时间','人数'], '订单管理':['项目','数量'], '支付与缴费':['金额','确认'],
}
NUMBERS = {'温度':(16,32),'档位':(0,7),'音量':(0,100),'亮度':(0,100),'强度':(0,5),'人数':(1,20),'数量':(1,100),'金额':(0,100000),'靠背':(80,130)}
BOOLS = {'开关','播放','暂停','继续','停止','开始','结束','静音','确认','吹脸','吹脚','吹窗','蓝牙','车机','广播','App','静态','呼吸','渐变','律动'}

class SimError(ValueError):
    def __init__(self, code, message):self.code,self.message=code,message;super().__init__(message)


def initial_state():
    return {'climate':{'power':False,'temperature':26,'fan':1,'air_mode':'吹脸','purifier':False,'front_defog':False,'rear_defog':False},
            'entertainment':{'selected':None,'playing':False,'volume':15,'source':'演示车机'},
            'ambient_light':{'power':False,'color':'白色','brightness':30,'effect':'静态'},
            'seat':{'backrest':100,'heat':0,'ventilation':0,'massage':False},
            'navigation':{'destination':None,'route':None,'running':False,'waypoints':[]},
            'notification_mode':{'muted':False,'blocked':False,'draft':None,'outbox':[], 'calls':[]},
            'ecosystem':{'selected':None,'orders':[],'reservations':[],'payments':[]},
            'simulation':{'connected_to_vehicle':False,'clock':'09:25','data_source':'固定演示数据，不是真实车况或查询结果'}}


def new_session(job_id, persona, actions):
    return {'id':uuid.uuid4().hex,'job_id':job_id,'persona':persona,'actions':deepcopy(actions),'cursor':0,
            'fixture_version':2,'revision':0,'state':initial_state(),'context':{},'events':[],'transport':'local_simulator'}


def atom_for(action):
    cid,name=action['capability'],action['name']
    if cid not in REGISTRY.all_ids():raise SimError('UNSUPPORTED','能力域不存在')
    atom=next((a for a in REGISTRY.get(cid)['atomic_abilities'] if a['name']==name),None)
    if not atom:raise SimError('UNSUPPORTED','原子能力不存在')
    return atom


def to_bool(value):
    if type(value) is bool:return value
    if str(value).lower() in {'true','on','开启','打开','开始','是','1'}:return True
    if str(value).lower() in {'false','off','关闭','停止','否','0'}:return False
    raise SimError('INVALID_ARGUMENT','开关参数应为开启或关闭')


def choices_for(session, field):
    key={'内容':'media_candidates','POI':'poi_candidates','路线':'routes','候选项':'service_candidates'}.get(field)
    return deepcopy(session['context'].get(key,[])) if key else []


def required_keys(name, params):
    req=list(REQUIRED.get(name, []))
    alternatives={'播放控制':('播放','暂停','继续','停止'),
                  '导航运行控制':('开始','暂停','继续','结束'),
                  '音源切换':('车机','蓝牙','广播','App'),
                  '出风模式切换':('吹脸','吹脚','吹窗'),
                  '灯光效果切换':('静态','呼吸','渐变','律动')}
    if name in alternatives and any(k in params for k in alternatives[name]):
        return []
    return req


def pending(session):
    if session['cursor']>=len(session['actions']):return None
    a=session['actions'][session['cursor']];atom=atom_for(a);params=a.get('parameters_hint') or {}
    req=required_keys(a['name'],params)
    fields=[]
    for key in req:
        spec={'name':key,'value':params.get(key),'choices':choices_for(session,key),'type':'boolean' if key in BOOLS else 'number' if key in NUMBERS else 'text'}
        if key in NUMBERS:spec.update(min=NUMBERS[key][0],max=NUMBERS[key][1])
        fields.append(spec)
    return {'action':a,'service':'sim.'+a['capability'],'method':a['name'],'required_fields':fields,
            'needs_confirmation':(a['capability'],a['name']) not in PREPARATION_ACTIONS or a['execution_mode'] in {'confirm_execute','choose_execute','conditional_auto'},
            'preconditions':a.get('preconditions',[]),'supported':True}


def _normalize(params, atom):
    if not isinstance(params,dict) or set(params)-set(atom.get('core_params',[])):raise SimError('INVALID_ARGUMENT','参数不在该原子能力定义中')
    result=deepcopy(params)
    for key,value in result.items():
        if value is None or (isinstance(value,str) and not value.strip()):raise SimError('INVALID_ARGUMENT',key+'未填写')
        if key in BOOLS:result[key]=to_bool(value)
        if key in NUMBERS:
            if isinstance(value,bool):raise SimError('INVALID_ARGUMENT',key+'需要数值，不能为布尔值')
            try:v=float(value)
            except (TypeError,ValueError):raise SimError('INVALID_ARGUMENT',key+'需要数值')
            lo,hi=NUMBERS[key]
            if not lo<=v<=hi:raise SimError('INVALID_ARGUMENT',f'{key}超出模拟器范围 {lo}–{hi}')
            result[key]=int(v) if v.is_integer() else v
    return result


def _pick(candidates, value):
    chosen=next((x for x in candidates if value in (x.get('id'),x.get('name'),x.get('label'))),None)
    if not chosen:raise SimError('DATA_UNAVAILABLE','必须选择前序查询实际返回的演示候选ID')
    return deepcopy(chosen)


def _dispatch(s, a, p):
    state,ctx=s['state'],s['context'];cid,name=a['capability'],a['name'];domain=state[cid]
    if name.endswith('恢复'):raise SimError('NO_SAVED_STATE','没有提供可恢复的历史记录；未伪造用户偏好')
    if cid=='entertainment':
        if name=='内容搜索':ctx['media_candidates']=deepcopy(MEDIA);return {'candidates':ctx['media_candidates'],'query':p}
        if name=='内容详情查看':
            if not ctx.get('media_candidates'):raise SimError('DEPENDENCY_MISSING','先搜索内容')
            return {'details':ctx['media_candidates']}
        if name=='内容选择':domain['selected']=_pick(ctx.get('media_candidates',[]),p['内容']);return {'selected':domain['selected']}
        if name=='播放控制':
            if p.get('播放') or p.get('继续'):
                if not domain['selected']:raise SimError('DEPENDENCY_MISSING','先选择返回的内容，再播放')
                domain['playing']=True
            elif p.get('暂停') or p.get('停止'):domain['playing']=False
            else:raise SimError('INVALID_ARGUMENT','缺少有效播放指令')
        elif name=='音量调节':domain['volume']=p['音量'];domain['muted']=p.get('静音',False)
        elif name=='音源切换':domain['source']=next((k for k,v in p.items() if k in BOOLS and v),'演示车机')
    elif cid=='climate':
        key={'空调开关':'power','空气净化开关':'purifier','前挡除雾开关':'front_defog','后挡除雾开关':'rear_defog'}.get(name)
        if key:domain[key]=p['开关']
        elif name=='温度调节':domain['temperature']=p['温度']
        elif name=='风量调节':domain['fan']=p['档位']
        elif name=='出风模式切换':domain['air_mode']='、'.join(k for k in ['吹脸','吹脚','吹窗'] if p.get(k))
    elif cid=='ambient_light':
        if name=='氛围灯开关':domain['power']=p['开关']
        elif name=='颜色设置':domain['color']=p['颜色']
        elif name=='亮度调节':domain['brightness']=p['亮度']
        elif name=='灯光效果切换':domain['effect']='、'.join(k for k in ['静态','呼吸','渐变','律动'] if p.get(k))
    elif cid=='seat':
        if name=='座椅位置调节':domain['backrest']=p['靠背']
        elif name=='座椅加热控制':domain['heat']=p['档位'] if p['开关'] else 0
        elif name=='座椅通风控制':domain['ventilation']=p['档位'] if p['开关'] else 0
        elif name=='座椅按摩控制':domain.update(massage=p['开关'],massage_mode=p['模式'],massage_intensity=p['强度'])
    elif cid=='navigation':
        if name=='地点搜索':ctx['poi_candidates']=deepcopy(POIS);return {'candidates':ctx['poi_candidates'],'query':p}
        if name=='地点详情查看':
            if not ctx.get('poi_candidates'):raise SimError('DEPENDENCY_MISSING','先搜索地点')
            return {'details':ctx['poi_candidates']}
        if name=='历史目的地获取':raise SimError('NO_SAVED_STATE','没有提供真实历史；模拟器未内置用户历史')
        if name=='目的地设置':domain['destination']=_pick(ctx.get('poi_candidates',[]),p['POI'])
        elif name in {'路线规划','路线重新规划'}:
            if not domain['destination']:raise SimError('DEPENDENCY_MISSING','先设置已查询的目的地')
            ctx['routes']=deepcopy(ROUTES);return {'routes':ctx['routes']}
        elif name=='路线比较':
            if not ctx.get('routes'):raise SimError('DEPENDENCY_MISSING','先规划路线')
            return {'comparison':ctx['routes'],'criteria':p}
        elif name=='路线选择':domain['route']=_pick(ctx.get('routes',[]),p['路线'])
        elif name=='预计到达时间获取':
            routes=[domain['route']] if domain['route'] else ctx.get('routes',[])
            if not routes:raise SimError('DEPENDENCY_MISSING','先规划路线')
            clock=datetime.strptime(state['simulation']['clock'],'%H:%M')
            ctx['etas']=[{'route_id':r['id'],'eta':(clock+timedelta(minutes=r['minutes'])).strftime('%H:%M')} for r in routes]
            return {'estimated_arrivals':ctx['etas'],'clock_source':'模拟器09:25演示时钟'}
        elif name=='迟到风险判断':
            if not ctx.get('etas'):raise SimError('DEPENDENCY_MISSING','先获取预计到达时间')
            deadline=str(p['任务时间']);match=re.search(r'([0-2]?\d):([0-5]\d)',deadline)
            if not match or int(match.group(1))>23:raise SimError('INVALID_ARGUMENT','模拟器的任务时间请填写HH:MM，不自动推定日期或时区')
            return {'deadline':deadline,'risks':[{'route_id':x['route_id'],'late':x['eta']>f'{int(match.group(1)):02d}:{match.group(2)}'} for x in ctx['etas']]}
        elif name=='导航运行控制':
            if p.get('开始') or p.get('继续'):
                if not domain['route']:raise SimError('DEPENDENCY_MISSING','先选路线')
                domain['running']=True
            elif p.get('暂停') or p.get('结束'):domain['running']=False
            else:raise SimError('INVALID_ARGUMENT','缺少有效导航指令')
        elif name=='途经点管理':domain['waypoints'].append(p['地点'])
    elif cid=='ecosystem':
        if name=='服务与地点搜索':
            kind=str(p.get('类型',''))
            ctx['service_candidates']=deepcopy([x for x in SERVICES if not kind or kind in x['type'] or x['type'] in kind])
            return {'candidates':ctx['service_candidates'],'query':p,'data_source':'固定演示数据，不使用真实位置'}
        if name in {'服务详情获取','候选服务筛选','候选方案比较','价格与优惠查询','实时资源状态查询'}:
            if not ctx.get('service_candidates'):raise SimError('DEPENDENCY_MISSING','先搜索服务候选')
            return {'items':ctx['service_candidates'],'criteria_received':p,'filtering':'仅回显演示候选与条件，未连接实际商户'}
        if name=='候选方案选择':domain['selected']=_pick(ctx.get('service_candidates',[]),p['候选项'])
        elif name=='预约与排队管理':domain['reservations'].append({'id':'sim-res-'+str(len(domain['reservations'])+1),**p})
        elif name=='订单管理':domain['orders'].append({'id':'sim-order-'+str(len(domain['orders'])+1),**p})
        elif name=='支付与缴费':
            if not p['确认']:raise SimError('CONFIRMATION_REQUIRED','模拟支付也需显式确认')
            domain['payments'].append({'id':'sim-payment-'+str(len(domain['payments'])+1),**p})
        elif name=='服务进度查询':return {'orders':domain['orders'],'reservations':domain['reservations'],'source':'模拟会话记录'}
    elif cid=='notification_mode':
        if name=='通知读取':ctx['notifications']=[{'id':'sim-note-1','text':'这是一条演示通知，不来自真实消息'}];return {'items':ctx['notifications']}
        if name in {'通知摘要','通知优先级分类'}:
            if not ctx.get('notifications'):raise SimError('DEPENDENCY_MISSING','先确认读取演示通知')
            return {'items':ctx['notifications'],'operation':name}
        if name=='回复草稿生成':domain['draft']={'content':p['内容'],'sent':False}
        elif name=='回复发送':domain['outbox'].append({'to':p['接收人'],'content':p['内容'],'simulated':True})
        elif name=='回拨发起':domain['calls'].append({'number':p['号码'],'simulated':True})
        elif name=='通知静音控制':domain['muted']=p['开关']
        elif name=='非紧急通知屏蔽':domain['blocked']=True;domain['scope']=p['屏蔽范围']
        elif name=='提醒延后':domain['defer']=p['延后时间']
    else:raise SimError('UNSUPPORTED','尚未实现该模拟服务')
    return {'state':deepcopy(domain)}


def execute(session, *, revision, params=None, confirmed=False, fault=None, manual_content=None):
    if type(revision) is not int or revision!=session['revision']:raise SimError('STALE_REVISION','会话已更新，请刷新，避免重复执行')
    if fault not in (None,'','timeout','rejected'):raise SimError('INVALID_ARGUMENT','未知故障类型')
    if not isinstance(params,(dict,type(None))):raise SimError('INVALID_ARGUMENT','参数必须为对象')
    s=deepcopy(session)
    if manual_content is not None:
        if confirmed is not True:raise SimError('CONFIRMATION_REQUIRED','选择并模拟播放需要确认')
        _pick(s['context'].get('media_candidates',[]),manual_content)
        # Explicit user operation, not an action attributed to the generated plan.
        sequence=[{'id':'manual-select','capability':'entertainment','name':'内容选择','execution_mode':'choose_execute','parameters_hint':{'内容':manual_content}},
                  {'id':'manual-play','capability':'entertainment','name':'播放控制','execution_mode':'confirm_execute','parameters_hint':{'播放':True}}]
    else:
        if s['cursor']>=len(s['actions']):raise SimError('PLAN_COMPLETED','计划步骤已结束')
        sequence=[s['actions'][s['cursor']]]
    for a in sequence:
        atom=atom_for(a);p={**(a.get('parameters_hint') or {}),**(params or {})} if manual_content is None else a['parameters_hint']
        if not isinstance(params,(dict,type(None))):raise SimError('INVALID_ARGUMENT','参数必须为对象')
        if a.get('execution_mode')=='suggest_only':raise SimError('SUGGESTION_ONLY','该步骤仅建议，尚无可执行授权')
        control=(a['capability'],a['name']) not in PREPARATION_ACTIONS or a.get('execution_mode') in {'confirm_execute','choose_execute','conditional_auto'}
        if control and confirmed is not True:raise SimError('CONFIRMATION_REQUIRED','请确认后模拟执行')
        for key in required_keys(a['name'],p):
            if key not in p:
                raise SimError('MISSING_ARGUMENT',key+'尚未给定，请填写，不从人格推测参数')
        before=deepcopy(s['state']);context_before=deepcopy(s['context']);started=time.monotonic()
        request={'request_id':uuid.uuid4().hex,'transport':'local_simulator','service':'sim.'+a['capability'],'method':a['name'],'params':p,'source':'user_manual' if manual_content is not None else 'generated_plan','confirmed':confirmed is True,'dispatched':False}
        try:
            normalized=_normalize(p,atom)
            request['params']=deepcopy(normalized)
            request['dispatched']=True
            if fault:raise SimError('SIMULATED_TIMEOUT' if fault=='timeout' else 'SIMULATED_REJECTION','人为注入的模拟故障')
            data=_dispatch(s,a,normalized)
            response={'code':'OK','data':data,'simulated':True}
            changed={k:{'before':before[k],'after':deepcopy(s['state'][k])} for k in before if before[k]!=s['state'][k]}
            if manual_content is None:s['cursor']+=1
        except SimError as exc:
            s['state']=before;s['context']=context_before
            response={'code':exc.code,'message':exc.message,'simulated':True};changed={}
        s['revision']+=1
        s['events'].append({'step_id':a['id'],'request':request,'response':response,'state_changes':changed,'elapsed_ms':round((time.monotonic()-started)*1000,2)})
        if response['code']!='OK':break
    return s


def prepare_public(session):
    """Run only public preparation calls, stopping at user choice or missing args."""
    s=deepcopy(session)
    for _ in range(len(s['actions'])):
        next_step=pending(s)
        if not next_step or next_step['needs_confirmation'] or next_step['action'].get('execution_mode')!='auto_generate':break
        if s['events'] and s['events'][-1]['response']['code']!='OK':break
        params=next_step['action'].get('parameters_hint') or {}
        if any(f['name'] not in params for f in next_step['required_fields']):break
        s=execute(s,revision=s['revision'])
        s['events'][-1]['trigger']='automatic_public_preparation'
        if s['events'][-1]['response']['code']!='OK':break
    return s


def snapshot(s):
    return {**deepcopy(s),'pending':pending(s),'completed':s['cursor']>=len(s['actions']),
            'notice':'本地模拟传输；服务名为演示协议，不是厂商SOA接口。无实车、音频、消息、支付等外部副作用。'}
