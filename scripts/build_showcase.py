"""Explicit one-time generation of the six frozen demonstration cases.

Checkpoint successful stages, retain failed responses, and publish atomically
only after every case has passed Step 8. This is never invoked by a page GET.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine.pipeline as pipeline
import engine.model_story as model_story
from engine.llm_router import get_llm, dump_json
from engine.model_intent import run_intent
from engine.showcase import publish_showcase


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def emit(case_id, stage, **fields):
    print(json.dumps({'case': case_id, 'stage': stage, **fields}, ensure_ascii=False), flush=True)


def batch_client(backend):
    client = get_llm(backend)
    if client is not None:
        # Two case workers, one upstream request each; no request racing here.
        client.race_n = 1
        client.timeout = 180
    return client


def build_case(case, destination, persona, attempts):
    cid = case['case_id']
    folder = destination / cid
    folder.mkdir(parents=True, exist_ok=True)
    finished = read(folder / 'session.json')
    if finished and finished.get('story_step', {}).get('status') == 'ok':
        if finished['persona'] != persona or finished['request']['user_utterance'] != case['request']['user_utterance']:
            raise ValueError('已有检查点与本次人格/情境不一致，请换一个工作目录')
        emit(cid, 'reuse_complete')
        return finished
    req = pipeline.default_request_scaffold(persona, case['request']['user_utterance'], f'showcase_{destination.name}_{cid}')
    context_file = ROOT / 'cases/showcase_context.json'
    contexts = read(context_file) or {}
    req['context'] = contexts.get(cid, {})
    observation = read(folder / 'step2.json')
    if observation and (observation.get('input', {}).get('provided_context') or {}) != req['context']:
        observation = None
    if observation and observation.get('input', {}).get('user_utterance') != req['user_utterance']:
        raise ValueError('Step 2 检查点情境不一致')
    for attempt in range(1, attempts + 1):
        if observation and not observation.get('fallback'):
            break
        began = time.monotonic()
        observation = run_intent(req, 'openai', batch_client('openai'))
        write(folder / f'step2_attempt_{time.time_ns()}.json', observation)
        emit(cid, 'step2', attempt=attempt, ok=not observation.get('fallback'),
             seconds=round(time.monotonic()-began, 2), error=observation.get('error_info'))
        if not observation.get('fallback'):
            write(folder / 'step2.json', observation)
    if not observation or observation.get('fallback'):
        raise RuntimeError(f'{cid} Step 2 尚未成功')
    result = read(folder / 'result.json')
    saved_context = (((result or {}).get('_review_internal') or {}).get('request') or {}).get('context') or {}
    if result and saved_context.get('demo_background') != req['context'].get('demo_background'):
        result = None
    if result and result.get('persona') != persona:
        raise ValueError('Step 7 检查点人格不一致')
    partial = pipeline.generate_behavior_plan(req, llm_backend='openai', shared_intent=observation, defer_step7=True)
    feedback = None
    for attempt in range(1, attempts + 1):
        if result and result.get('generation_status') == 'ok':
            break
        payload = dict(partial['audit_steps'][-1]['input'])
        payload['showcase_planning_notes'] = (
            '这是行为编排演示，不代表车控执行。未知地图、路况、营业结果可通过序列中的查询能力获取，'
            '查询后由用户选择/确认再执行，条件需保留；不能把尚未调用服务误当成必须现在补全的事实。'
            '不愿立即回家的情感场景可先提供附近去处候选，不强迫用户先指定目的地或立即启动导航。'
            '如果确实缺少无法由能力查询或用户选择环节解决的信息，则如实输出needs_clarification。'
        )
        if req['context'].get('demo_background'):
            payload['approved_demo_background'] = req['context']['demo_background']
            payload['showcase_planning_notes'] += ' 本案例已明确学校名称为实验小学，可搜索该名称；具体匹配地点及入口仍需核验。'
        overrides = {7: {'user_prompt': dump_json(payload)}}
        if feedback:
            payload['validation_feedback'] = feedback
            overrides[7] = {'user_prompt': dump_json(payload)}
        began = time.monotonic()
        result = pipeline.rerun_from_review_step(partial, step_no=7, llm_backend='openai', prompt_overrides=overrides)
        write(folder / f'step7_attempt_{time.time_ns()}.json', result)
        emit(cid, 'step7', attempt=attempt, status=result.get('generation_status'),
             seconds=round(time.monotonic()-began, 2), error=result.get('generation_error'))
        if result.get('generation_status') == 'ok':
            write(folder / 'result.json', result)
        elif result.get('generation_status') in {'needs_clarification', 'no_tool_needed'}:
            feedback = {'previous_status': result['generation_status'],
                        'questions': result.get('clarification_questions') or [],
                        'instruction': '请核对已提供的演示背景与可查询能力。能编排查询和用户选择步骤时先编排，不能编排时保持真实的待补充状态，不得捏造数据。'}
        elif result.get('generation_error', {}).get('category') == 'planner_validation':
            feedback = {'error': result['generation_error']['message'],
                        'instruction': '请修正上述结构或依赖错误，仍严格遵守原任务与能力库，完整重新输出。不新增用户事实或授权。'}
    if not result or result.get('generation_status') != 'ok':
        raise RuntimeError(f'{cid} Step 7 尚未成功')
    session = {'case_id': cid, 'case_title': case['title'], 'persona': persona, 'backend': 'openai',
               'request': req, 'result': result, 'confirmed_step': 7, 'prompt_overrides': {}}
    payload = model_story.build_story_input(session)
    payload['display_guidelines'] = (
        '本次用于固定展示。最多选择2个最有代表性的核心改变点，其他行为保持普通文字。'
        '以助手为主体，建议2—4段，相邻动作可以自然衔接，保留必要条件。'
        '高亮引用正文中体现人格影响的句子，不是每个人格影响都需要高亮。'
    )
    story = None
    # Reuse returned prose for this plan without content validation.
    for saved_story in sorted(folder.glob('step8_attempt_*.json'), reverse=True):
        candidate = read(saved_story)
        if not candidate.get('raw_llm_text'):
            continue
        if (candidate.get('input') or {}).get('behavior_sequence') != payload['behavior_sequence']:
            continue
        output = model_story.parse_story_response(candidate['raw_llm_text'])
        story = {**candidate, 'status': 'ok', 'output': output, 'reused_from': saved_story.name}
        story.pop('error_info', None)
        emit(cid, 'step8_reused', source=saved_story.name)
        break
    for attempt in range(1, attempts + 1):
        if story and story['status'] == 'ok':
            break
        story = model_story.run_story(payload, 'openai')
        write(folder / f'step8_attempt_{time.time_ns()}.json', story)
        emit(cid, 'step8', attempt=attempt, status=story['status'], seconds=story['elapsed_seconds'], error=story.get('error_info'))
        if story['status'] == 'ok':
            break
    if not story or story['status'] != 'ok':
        raise RuntimeError(f'{cid} Step 8 尚未成功')
    session.update(story_step=story, confirmed_step=8)
    write(folder / 'session.json', session)
    emit(cid, 'complete', actions=len(result['O_Action_Plan']))
    return session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--persona', choices=['INFP', 'ESTJ'], default='INFP')
    parser.add_argument('--work-dir', required=True)
    parser.add_argument('--attempts', type=int, default=4)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--no-publish', action='store_true')
    parser.add_argument('--case-id')
    args = parser.parse_args()
    destination = Path(args.work_dir)
    destination.mkdir(parents=True, exist_ok=True)
    pipeline.get_llm = batch_client
    model_story.get_llm = batch_client
    cases = json.loads((ROOT / 'cases/welcome_cases.json').read_text(encoding='utf-8'))
    if args.case_id:
        cases = [c for c in cases if c['case_id'] == args.case_id]
    sessions, errors = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(build_case, case, destination, args.persona, args.attempts): case for case in cases}
        for future in as_completed(pending):
            case = pending[future]
            try:
                sessions.append(future.result())
            except Exception as exc:
                errors.append({'case': case['case_id'], 'error': str(exc)})
                emit(case['case_id'], 'incomplete', error=str(exc))
    write(destination / 'summary.json', {'completed': [s['case_id'] for s in sessions], 'errors': errors, 'updated_at': datetime.now().isoformat()})
    if errors:
        raise SystemExit(1)
    if args.no_publish:
        emit('all', 'completed_not_published', count=len(sessions))
        return
    manifest = publish_showcase(sessions, cases)
    write(destination / 'published.json', manifest)
    emit('all', 'published', batch_id=manifest['batch_id'], count=len(manifest['cases']))


if __name__ == '__main__':
    main()
