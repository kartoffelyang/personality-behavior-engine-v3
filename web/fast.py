"""Separate compact entry; the original routes remain available."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import re
import threading
import time
import uuid

from flask import Blueprint, jsonify, render_template, request
from engine.fast_planner import generate_fast
from engine.llm_router import describe_llm_error
from engine.planner_validation import PlanValidationError

bp = Blueprint('fast', __name__)
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output' / 'fast'
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='fast-plan')
_lock = threading.Lock()
_jobs = {}
_slots = threading.BoundedSemaphore(2)


def _run(jid, utterance):
    started = time.monotonic()
    with _lock:
        _jobs[jid]['status'] = 'running'
    try:
        result = generate_fast(utterance)
        record = {'id': jid, 'created_at': datetime.now().astimezone().isoformat(), 'input': utterance, 'result': result}
        OUT.mkdir(parents=True, exist_ok=True)
        temp = OUT / f'.{jid}.tmp'
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(OUT / f'{jid}.json')
        with _lock:
            _jobs[jid].update(status='completed', result=result)
    except Exception as exc:
        info = describe_llm_error(exc)
        if isinstance(exc, PlanValidationError):
            message = '模型已生成方案，但参数或动作检查未通过：' + str(exc)
        else:
            message = {'auth_missing': '未配置模型密钥。', 'auth_invalid': '模型密钥验证失败。',
                       'model_not_found': '当前模型不可用。'}.get(info.get('category'), '模型请求未完成，请稍后重试。')
        with _lock:
            _jobs[jid].update(status='failed', error=message, stage='plan_validation' if isinstance(exc, PlanValidationError) else 'generation')
        # Failed generations remain diagnosable after a server restart.
        failure = {'id': jid, 'input': utterance, 'status': 'failed', 'error': message,
                   'stage': 'plan_validation' if isinstance(exc, PlanValidationError) else 'generation',
                   'created_at': datetime.now().astimezone().isoformat()}
        try:
            OUT.mkdir(parents=True, exist_ok=True)
            temp = OUT / f'.{jid}.tmp'
            temp.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(OUT / f'{jid}.json')
        except OSError:
            pass
    finally:
        with _lock:
            _jobs[jid]['elapsed_seconds'] = round(time.monotonic() - started, 2)
        _slots.release()


def demo_scenarios():
    cases = json.loads((ROOT / 'config' / 'demo_scenarios.json').read_text(encoding='utf-8'))
    try:
        manifest = json.loads((OUT / 'scenario_trials.json').read_text(encoding='utf-8'))
        trials = {c['id']: c for c in manifest.get('cases', [])}
    except (OSError, ValueError, KeyError, TypeError):
        trials = {}
    result = []
    for case in cases:
        item = {**case, 'job_id': '', 'trial_status': 'pending', 'trial_error': '', 'trial_label': '填入情境'}
        trial = trials.get(case['id'], {})
        # Link only an actual saved generation of the exact scenario text.
        if trial.get('text') == case['text']:
            if trial.get('status') == 'completed' and re.fullmatch(r'[a-f0-9]{32}', trial.get('job_id', '')):
                try:
                    saved = json.loads((OUT / (trial['job_id'] + '.json')).read_text(encoding='utf-8'))
                    if saved.get('input') == case['text'] and isinstance(saved.get('result'), dict):
                        state=saved['result'].get('status')
                        label={'needs_clarification': '待补充信息', 'no_tool_needed': '无工具行为'}.get(state, '查看已存行为 ↗')
                        item.update(job_id=trial['job_id'], trial_status='completed', trial_label=label)
                except (OSError, ValueError):
                    pass
            elif trial.get('status') == 'failed':
                item.update(trial_status='failed', trial_label='未通过检查 · 可重试', trial_error=trial.get('error', '首轮生成未通过，请重新生成。'))
        result.append(item)
    return result


@bp.get('/fast')
def page():
    return render_template('fast.html', demo_scenarios=demo_scenarios())


@bp.post('/api/fast')
def create():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('text'), str):
        return jsonify(error='请填写情境描述。'), 400
    utterance = data['text'].strip()
    if not 1 <= len(utterance) <= 3000:
        return jsonify(error='情境描述需为 1–3000 字。'), 400
    if not _slots.acquire(blocking=False):
        return jsonify(error='已有两项生成任务，请等当前任务完成。'), 429
    jid = uuid.uuid4().hex
    with _lock:
        if len(_jobs) > 100:
            for old in list(_jobs):
                if _jobs[old]['status'] in {'completed', 'failed'}:
                    del _jobs[old]
                    if len(_jobs) <= 80:
                        break
        _jobs[jid] = {'id': jid, 'status': 'queued', 'input': utterance}
    try:
        _pool.submit(_run, jid, utterance)
    except Exception:
        with _lock:
            del _jobs[jid]
        _slots.release()
        return jsonify(error='任务暂时无法启动，请重试。'), 503
    return jsonify(id=jid, status='queued'), 202


@bp.get('/api/fast/<jid>')
def snapshot(jid):
    if not re.fullmatch(r'[a-f0-9]{32}', jid):
        return jsonify(error='任务不存在。'), 404
    with _lock:
        job = _jobs.get(jid)
        if job is not None:
            return jsonify(job)
    try:
        saved = json.loads((OUT / f'{jid}.json').read_text(encoding='utf-8'))
        if saved.get('status') == 'failed':
            return jsonify(id=jid, status='failed', input=saved.get('input', ''), error=saved['error'], stage=saved.get('stage', 'generation'))
        return jsonify(id=jid, status='completed', result=saved['result'], input=saved['input'])
    except (OSError, ValueError, KeyError):
        return jsonify(error='任务不存在或服务已重启，请重新生成。'), 404
