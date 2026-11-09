"""Full pipeline demo: shared fictional observations, independent persona plans."""
import json
import time
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from .pipeline import Engine, default_request_scaffold, generate_behavior_plan
from .model_intent import run_intent
from .model_planner import current_planner_system
from .llm_router import get_llm

ROOT=Path(__file__).resolve().parents[1]

def cases():return json.loads((ROOT/'config/persona_demo_cases.json').read_text())

def kev_cases():
    extras=json.loads((ROOT/'config/kev_showcase_cases.json').read_text())
    # Runtime recipes remain server-side; the browser receives scene metadata.
    base=cases()
    for c in base:
        c['category']='座舱体验' if c['id']=='after_work' else '出行选择'
        c['dimension']=c['observe']
        c['trial_observation']='设计观察目标；实际分值与采用状态见本轮记录。强差异包含人为强化角色边界。'
    return base+[{k:v for k,v in c.items() if k in {'id','title','text','world','category','dimension','observe','why_suitable','trial_observation','priority'}} for c in extras]

DEMO_GUIDANCE='''\n【本次运行：纯虚拟人格演示】
以下demo_world与用户输入中的“共同演示设定”是两个人格共享的虚拟事实，不是真实车况。无需真实接口，也不等待用户回答。补充不影响明确意愿的必要设定时，以notes“演示假设：”标记；不得返回needs_clarification。
保留完整persona_profile、persona_description、persona_scene_strategy和已编译interaction_dimensions，具体决策依此形成，不因人格标签机械分配内容。无必要差异可以相同。
你要输出具体演示结果，不是“将查询、将推荐”的空策略：从演示候选中写出具体内容、商户或地点；动作details须说明本步骤的具体选择/处理及模拟返回，合理绑定共同设定中的ID。查询仍须在选择、控制之前，不能省略依赖。未知的结果只可作为明确标注的模拟假设。
确认或choose模式可以保留在行为契约里，但本演示将用户接受作为虚拟交互自动演出，不向真实用户提问，不进行真实车控、发送或消费。
final_explanation直接写本人格在车机会说的一段话，包含具体安排/推荐和原因，约80至140个汉字，避免技术字段名。不说需要用户进一步确认或补充资料。
每项persona_influence用一句话指向输入中的实际策略或交互维度，decision_basis说明如何影响了具体做法；不要仅写“因为是ESTJ/INFP”。每个动作的解释字段各用一短句，不重复长理论。只输出完整合法JSON，不增加字段。不要为凑差异增加无关能力。
【传输格式覆盖，仅减少重复字段，不减少决策信息】
顶层仍为plan_status/clarification_questions/behavior_actions/notes/final_explanation。每个behavior_actions条目仅输出：{"c":"能力域ID","n":"合法原子能力名","m":"执行方式","p":{},"d":"具体动作及模拟结果","why":"实际人格因素怎样改变做法","basis":"当前决策依据"}。
程序会补齐display_name、order、medium强度和共同情境标签，并严格校验完整计划。m优先使用auto_generate/confirm_execute/choose_execute；若使用conditional_auto，必须额外提供w数组写明具体条件。其他模式可省略w。必要依赖必须用前序动作满足，不能靠条件文字绕过。生态服务搜索只产生服务候选，不能代替导航地点搜索；去餐厅导航时仍需地点搜索→地点详情→目的地设置→路线规划→路线选择→导航启动。若任务只需选定套餐，不额外增加导航。why和basis各约15至30字，d约20至45字。final_explanation约60至100字，直接演出已完成的虚拟选择，不再说等待接受或要求用户确认。notes只写必要的演示假设，避免重复免责声明。一般4至8个原子动作；核心任务依赖优先，不硬删必要步骤。'''

def intent_key(combined):
    from .model_intent import current_intent_system
    config_bytes=b''.join(p.read_bytes() for p in sorted((ROOT/'config').rglob('*.yaml')))
    return hashlib.sha256(combined.encode()+current_intent_system().encode()+config_bytes).hexdigest()


def shared_observation(text,world,job_id,eng):
    combined=text+'\n共同演示设定（全部为虚构，仅用于模拟）：'+world
    req=default_request_scaffold('NO_MBTI',combined,job_id+'_intent')
    cache=ROOT/'output'/'persona_demo'/'intent_cache'/(intent_key(combined)+'.json')
    cached=False;t=time.monotonic()
    try:intent=json.loads(cache.read_text());cached=True
    except (OSError,ValueError):
        intent=run_intent(req,'openai',get_llm('openai'),eng.scenarios)
        if intent.get('fallback'):raise ValueError('情境理解未成功，未用固定模板冒充生成结果')
        cache.parent.mkdir(parents=True,exist_ok=True);cache.write_text(json.dumps(intent,ensure_ascii=False))
    intent['elapsed_seconds']=round(time.monotonic()-t,2)
    intent['cache_hit']=cached
    return combined,intent,cached


def run_demo(text,world,job_id,on_progress=lambda **kw:None):
    started=time.monotonic();eng=Engine(use_llm=True,llm_backend='openai')
    on_progress(stage='understanding')
    combined,intent,cached=shared_observation(text,world,job_id,eng)
    on_progress(stage='planning',intent_seconds=intent['elapsed_seconds'])
    variants={};audits={}
    def branch(pid):
        request=default_request_scaffold(pid,combined,job_id+'_'+pid)
        request['generation_mode']='demo';request['demo_world']=world;request['compact_demo']=True
        result=generate_behavior_plan(request,engine=eng,llm_backend='openai',shared_intent=intent,
            prompt_overrides={7:{'system_prompt':current_planner_system()+DEMO_GUIDANCE}})
        return pid,result
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(branch,p) for p in ('ESTJ','INFP')]):
            pid,result=future.result();audits[pid]=result
            if result.get('generation_status')!='ok':
                error=result.get('generation_error') or {}
                variants[pid]={'persona':pid,'status':'failed','error':error.get('message','人格行为方案未通过检查')}
            else:variants[pid]=present(result)
            on_progress(variants=dict(variants))
    return {'input':text,'world':world,'variants':variants,'audits':audits,
            'elapsed_seconds':round(time.monotonic()-started,2),'intent_seconds':intent['elapsed_seconds'],
            'mode':'full_pipeline_demo','intent_cached':cached,'model_calls_minimum':2 if cached else 3}

def present(result):
    steps={s['step']:s for s in result['audit_steps']};p=steps[7]['output']['planner_output']
    actions=steps[7]['output']['validated_actions']
    policy=steps[6]['output']['interaction_policy']
    return {'persona':result['persona'],'status':'completed','reply':p['final_explanation'],
        'actions':[{'name':a['name'],'domain':a['capability'],'text':a['details'],
                    'parameters':a['parameters_hint'],'why':a['persona_influence'],'basis':a['decision_basis'],
                    'order':a['order']} for a in actions],
        'strategy':steps[5]['output'].get('scenario_strategy') or steps[4]['output'].get('action_strategy'),
        'dimensions':[c['dimension'] for c in policy.get('constraints',[])],
        'assumptions':p.get('notes',[]),'planner_seconds':steps[7].get('elapsed_seconds'),
        'attempts':len(steps[7].get('attempts') or [])}


def explain_result(result):
    """Public configuration and recorded rationale, never inferred hidden reasoning."""
    steps={s['step']:s.get('output') or {} for s in result.get('audit_steps',[])}
    profile=steps.get(3,{})
    return {'persona':result.get('persona'),'profile':profile,
            'general_strategy':steps.get(4,{}),'scene_strategy':steps.get(5,{}),
            'dimensions':(steps.get(6,{}).get('interaction_policy') or {}).get('constraints',[]),
            'source':'本次生成保存的Step 3–6配置与Step 7动作解释',
            'attribution':'configuration_and_declared_rationale_not_causal_proof'}
