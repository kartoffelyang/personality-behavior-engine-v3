"""Local bounded Kev decisions, projected onto the existing demo view contract.

Shared neutral facts and Step 3-6 configuration stay intact. Kev selects from
shared candidates; Python compiles actions and wording. No fallback to a remote
persona planner and no fabricated chain of thought.
"""
import copy
import json
import math
import time
import urllib.error
import urllib.request

from .persona_demo import ROOT, cases, kev_cases, shared_observation
from .pipeline import Engine, _interaction_profile, _function_stack
from .persona_profile import build_persona_profile
from .model_planner import build_step7_capability_catalog, expand_compact_demo_plan
from .planner_validation import validate_plan
from .llm_router import chat_json_with_raw, get_llm
from .kev_atomic import scope_catalog, action_templates, atomic_questions, resolve_actions, key

URL = 'http://127.0.0.1:8009/v1/systemone'


class KevDemoError(ValueError):
    """Safe, user-readable errors; never include upstream bodies or secrets."""


def decide(state, questions):
    payload = {'model': 'kev-latest', 'state': state, 'questions': questions}
    req = urllib.request.Request(URL, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={'Content-Type': 'application/json'})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise KevDemoError('本地 KEV 服务未能返回结果，请检查 8009 端口的模型服务后重试。') from exc
    answers = result.get('answers', {})
    for key, question in questions.items():
        answer = answers.get(key, {})
        scores = answer.get('probabilities', {})
        if answer.get('choice') not in question['criteria'] or set(scores) != set(question['criteria']):
            raise KevDemoError('KEV 返回的候选不符合本轮决策范围，未替换成预设答案。')
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in scores.values()):
            raise KevDemoError('KEV 返回了非法决策分数，请重试。')
        if abs(sum(scores.values()) - 1) > .02:
            raise KevDemoError('KEV 返回的选项概率未归一，请重试。')
    return result, round(time.monotonic() - started, 3)


def persona_context(eng, pid, intent):
    persona = eng.personas.get(pid)
    dominant = _function_stack(persona)['dominant']
    fn = eng.functions.get(dominant)
    sid = intent.get('scenario_id') or intent.get('output', {}).get('scenario_id')
    scene = eng.scenarios.get(sid)
    strategy = (scene.get('function_strategies') or {}).get(dominant) or {}
    profile = build_persona_profile(pid, persona)
    general = {k: fn.get(k) for k in ('action_strategy', 'action_logic')}
    scoped = {'dominant_function': dominant, 'action_strategy': fn.get('action_strategy'),
              'cross_scenario_prototype': fn.get('cross_scenario_prototype'),
              'scenario_strategy': strategy.get('strategy'), 'action_logic': strategy.get('action_logic', [])}
    interaction = _interaction_profile(eng, pid, persona, dominant, intent.get('output') or {})
    return {'persona_profile': profile, 'persona_description': general,
            'persona_scene_strategy': scoped, 'interaction_dimensions': interaction,
            'function_scores': persona.get('function_scores', {})}


def choice(instructions, criteria):
    return {'type': 'choice', 'instructions': instructions, 'criteria': criteria}


def atom(domain, name, params, details):
    return {'c': domain, 'n': name, 'm': 'auto_generate', 'p': params, 'd': details,
            'why': '人格未改变此准备步骤', 'basis': '共同虚拟设定；依赖由程序编排'}


def nav_actions(ident, name, organization):
    return [atom('navigation', '地点搜索', {'名称': name}, f'模拟检索{name}，返回候选{ident}。'),
            atom('navigation', '地点详情查看', {'地址': f'{name}（虚拟地点）'}, f'模拟核对{name}的营业与停车信息。'),
            atom('navigation', '目的地设置', {'POI': f'{ident} {name}'}, f'模拟将目的地设为{name}。'),
            atom('navigation', '路线规划', {'起终点': f'当前驻车点 → {name}', '偏好': organization}, f'模拟规划前往{name}的路线，不虚构实时路况。'),
            atom('navigation', '路线选择', {'路线': f'前往{name}的演示路线', '选择依据': organization}, f'模拟选定前往{name}的路线。'),
            atom('navigation', '导航运行控制', {'开始': True}, f'模拟启动前往{name}的导航，当前车辆仍为驻车。')]


def content_actions(ident, name):
    return [atom('entertainment', '内容搜索', {'关键词': name}, f'从共同虚拟内容库检索{name}。'),
            atom('entertainment', '内容选择', {'内容': f'{ident} {name}'}, f'本轮选择《{name}》。'),
            atom('entertainment', '播放控制', {'播放': True}, f'模拟播放已选内容《{name}》。')]


PRESETS = {
    'rest_stop': [('R1', '禾间简餐', '香菇鸡肉饭配时蔬汤45元，1.2公里，等待5分钟，用餐20分钟，环境普通'),
                 ('R2', '小院面馆', '番茄牛肉面配小菜32元，2.1公里，等待10分钟，用餐25分钟，安静庭院'),
                 ('R3', '湖畔轻食', '烤蔬菜鸡肉拼盘58元，3.0公里，等待8分钟，用餐30分钟，湖景')],
    'weekend': [('P1', '河岸步道', '单程20分钟，停留60分钟，免费'),
                ('P2', '小巷书店', '单程25分钟，停留45分钟，饮品35元'),
                ('P3', '山麓公园', '单程35分钟，停留60分钟，免费')],
    'after_work': [('M1', '雨后钢琴', '纯音乐12分钟'), ('M2', '换个角度看今天', '轻松播客15分钟'),
                   ('M3', '城市慢游', '轻快音乐10分钟')],
}


def prepare_candidates(eng, text, world, case_id, intent):
    # Presets use the very same named resources as the original shared world.
    preset = next((c for c in cases() if c['id'] == case_id and c['text'] == text), None)
    if preset:
        return [{'id': i, 'name': n, 'description': d} for i, n, d in PRESETS[case_id]], {'source': 'shared_demo_world', 'seconds': 0}
    catalog = build_step7_capability_catalog([{'capability': c, **eng.capabilities.get(c)} for c in eng.capabilities.all_ids()])
    prompt = '''为纯虚拟车机演示准备三个中立的、均符合用户明确要求的候选方案，不做人格分配。
每个方案具体且可展示，不要求用户确认，不连接真实车机。允许合理虚构未知资源但在description中标明演示假设。
只输出JSON：{"candidates":[{"id":"A","name":"简短具体方案名","description":"具体安排与虚拟结果","actions":[{"c":"能力域ID","n":"合法原子能力名","m":"auto_generate","p":{},"d":"具体模拟动作","why":"人格未改变此候选步骤","basis":"用户目标与共同设定"}]}]}。
三个id为A/B/C；每个2至10个动作，使用能力库合法参数并补齐前置依赖；不得用空参数、占位符、真实交易或发送冒充结果。'''
    started = time.monotonic()
    try:
        result, _ = chat_json_with_raw(get_llm('openai'), [{'role': 'system', 'content': prompt},
            {'role': 'user', 'content': json.dumps({'text': text, 'world': world, 'intent': intent.get('output'), 'capabilities': catalog}, ensure_ascii=False)}], max_tokens=4200)
        candidates = result['candidates']
        if len(candidates) != 3 or {c['id'] for c in candidates} != {'A', 'B', 'C'}:
            raise ValueError('candidate set')
        for c in candidates:
            if not all(isinstance(c[k], str) and c[k].strip() for k in ('name', 'description')) or not 1 <= len(c['actions']) <= 12:
                raise ValueError('candidate content')
            compile_plan(eng, text, world, c['actions'], c['description'], intent.get('output'))
    except Exception as exc:
        raise KevDemoError('共同候选准备未完成，请重试；没有用预设人格答案替代。') from exc
    return candidates, {'source': 'shared_neutral_llm_candidates', 'seconds': round(time.monotonic()-started, 2)}


def compile_plan(eng, text, world, actions, reply, understanding=None):
    catalog = [{'capability': c, **eng.capabilities.get(c)} for c in eng.capabilities.all_ids()]
    payload = {'flow_context': {'capability_catalog': catalog, 'generation_mode': 'demo',
        'user_utterance': text, 'demo_world': world, 'scenario_understanding': understanding or {},
        'execution_context': {'context': copy.deepcopy((understanding or {}).get('context') or {})}}}
    raw = {'plan_status': 'ready' if actions else 'no_tool_needed', 'clarification_questions': [], 'notes': [],
           'final_explanation': reply, 'behavior_actions': copy.deepcopy(actions)}
    expanded = expand_compact_demo_plan(raw, payload)
    validate_plan(expanded, payload, eng.capabilities.all_ids())
    return expanded


def run_kev_demo(text, world, job_id, on_progress=lambda **kw: None, case_id=None):
    started = time.monotonic()
    eng = Engine(use_llm=True, llm_backend='openai')
    # Fail promptly if the independent local service is unavailable.
    try:
        with urllib.request.urlopen('http://127.0.0.1:8009/v1/models', timeout=3) as response:
            metadata = json.load(response)
        if 'kev-0.8b' not in metadata['models'][0]['run']:
            raise ValueError('wrong checkpoint')
    except Exception as exc:
        raise KevDemoError('本地 KEV-0.8B 服务未启动，请先启动 8009 端口的模型服务。') from exc
    from .kev_intent_atomic import run_atomic_pipeline
    return run_atomic_pipeline(eng,text,world,job_id,on_progress,decide,compile_plan)
