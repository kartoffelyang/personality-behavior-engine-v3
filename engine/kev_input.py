"""Compact current-state adapter for Kev; no serial reasoning or chat history."""
import copy
from .kev_atomic import key
from .kev_personality import policy_config

INPUT_VERSION='kev-compact-v4'


def action_impact(domain,name):
    if domain=='notification_mode':
        if name in {'通知读取','通知摘要','通知优先级分类'}:return '访问私人通知内容，不发送消息'
        if name in {'回复发送','回拨发起'}:return '对外通信，必须符合明确任务'
        return '改变通知呈现或提醒方式'
    if domain in {'climate','seat','ambient_light'}:return '改变座舱环境或身体感受'
    if domain=='entertainment':return '检索或改变影音体验'
    if domain=='navigation':return '查询或改变导航安排，不控制车辆行驶'
    return '查询或选择外部服务，消费操作须符合明确任务'


def compact_state(pid,text,world,bundles,understanding=None,scene_policy=None):
    cfg=policy_config();u=understanding or {};task=u.get('task') or {}
    facts=[]
    for f in u.get('facts') or []:
        fact=f.get('fact') if isinstance(f,dict) else f
        if isinstance(fact,str) and fact not in facts:facts.append(fact)
    role={'id':pid,'input_version':INPUT_VERSION,'policy_version':cfg['version'],
          'identity':'本轮模拟的车机决策风格，不判断用户人格。',
          'rules':copy.deepcopy(cfg['personas'][pid]['compact_rules'])}
    if scene_policy is not None:
        role['authored_demo_scope']={'rule':scene_policy['goal'],
            'allowed_plan_ids':scene_policy['allowed_plan_ids'],
            'applied':scene_policy['applied'],'reason':scene_policy['reason']}
    return {
        'situation':{'utterance':text,'goal':task.get('core_goal') or text,
                     'facts':facts,'context':{k:v for k,v in (u.get('context') or {}).items() if v is not None},
                     'unknowns':copy.deepcopy(u.get('unknowns') or []),'shared_options':copy.deepcopy(u.get('shared_options') or []),'demo_assumptions':world},
        'constraints':{'must_do':copy.deepcopy(task.get('must_do') or []),
                       'explicit_constraints':copy.deepcopy(task.get('constraints') or []),
                       'common':['安全与明确要求优先，不编造历史或个人偏好。','全部操作仅模拟，不等待真人确认。',
                                 '各题独立判断；动作依赖、互斥和合法组合由程序校验。']},
        'role':role,
        'candidates':{b['id']:{'outcome':b['description'],'decision_style':b.get('family'),'tradeoff':b.get('role_evidence'),'selection_basis':b.get('selection_basis'),'actions':[key(a['c'],a['n']) for a in b['actions']]} for b in bundles}}
