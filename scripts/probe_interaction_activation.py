"""Repeat a real Step 2 -> Step 6 -> Step 7 case and preserve auditable results."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import pipeline
from engine.interaction_policy import validate_activations
from engine.llm_router import get_llm
from web import flow_review_app
from web.flow_review_app import make_step_view, save_session_artifact


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def validate_recorded_result(result, entry):
    """Validate saved model output only; never create or call an LLM client."""
    steps = {step['step']: step for step in result['audit_steps']}
    step2, step6, step7 = steps[2], steps[6], steps[7]
    entry.update(
        step2_status=step2['status'], step7_status=step7['status'],
        generation_status=result.get('generation_status'),
        action_count=len(step7['output'].get('validated_actions') or []),
        model_success=(step2['status'] == 'ok' and step7['status'] == 'ok'
                       and result.get('generation_status') == 'ok'),
    )
    if step2['status'] != 'ok':
        entry['error'] = step2.get('error_info') or {'category': 'step2_fallback'}
        raise AssertionError('保存的 Step 2 未返回有效模型结果；本地重检不会重新请求模型。')
    activations = step2['output']['activated_interaction_dimensions']
    validate_activations(activations)
    policy = step6['output']['interaction_policy']
    entry.update(activated_dimensions=activations, constraint_count=len(policy['constraints']))
    assert len(policy['fixed_dimensions']) == 4, '固定维度数量不等于四项'
    assert set(policy['fixed_dimensions']) == {'主动性', '确认严格度', '解释透明度', '控制权保留度'}
    assert policy['activated_dimensions'] == activations, 'Step 2 的激活项未完整传入 Step 6'
    assert len(policy['constraints']) == 4 + len(activations), '合成约束数量不符合 4＋0—4'
    for activation in activations:
        assert activation['context_requirement'] in policy['compiled_prompt'], '本轮情境要求在合成提示词中丢失'
    if step7['status'] != 'ok' or result.get('generation_status') != 'ok':
        entry['error'] = step7['output'].get('generation_error') or step7['output'].get('llm_error') or {}
        raise AssertionError('保存的 Step 7 未生成有效行为序列；本地重检不会重新请求模型。')
    assert entry['action_count'], '该案例应包含实际工具动作'
    prompt = json.loads(step7['prompt_used']['user_prompt'])
    dimensions = prompt['flow_context']['interaction_dimensions']
    assert dimensions['compiled_prompt'] == policy['compiled_prompt'], 'Step 7 提示词与 Step 6 不一致'
    assert not {'raw_interaction', 'scaled_1_to_5', 'function_dimension_descriptions'} & set(dimensions)
    calls = entry.get('model_calls') or []
    assert len(calls) == 2, f'应记录恰好两次模型调用，实际为 {len(calls)} 次'
    assert all(call['status'] == 'ok' for call in calls), '存在失败的真实模型调用'


def render_review_pages(session_id, session):
    """Build the views and render actual GET routes inside Flask's context."""
    previous = flow_review_app._session_store.get(session_id)
    flow_review_app._session_store[session_id] = session
    renders = []
    try:
        for step_no in (2, 6, 7):
            path = f'/session/{session_id}/step/{step_no}'
            with flow_review_app.app.test_request_context(path):
                view = make_step_view(session_id, session, step_no)
                assert view['step_no'] == step_no
                response = flow_review_app.app.make_response(
                    flow_review_app.flow_review_step(session_id, step_no)
                )
                assert response.status_code == 200, f'Step {step_no} 审阅页未正常渲染'
                html = response.get_data(as_text=True)
                assert f'流程问题页 · Step {step_no}' in html, f'Step {step_no} 审阅页内容不匹配'
                if step_no == 6:
                    step6 = next(step for step in session['result']['audit_steps'] if step['step'] == 6)
                    policy = step6['output']['interaction_policy']
                    for constraint in policy['constraints']:
                        assert constraint['dimension'] in html, '第六步展示遗漏已启用维度'
                renders.append({'step': step_no, 'http_status': response.status_code, 'html_chars': len(html)})
    finally:
        if previous is None:
            flow_review_app._session_store.pop(session_id, None)
        else:
            flow_review_app._session_store[session_id] = previous
    return renders


def write_report(directory, case, summary, *, rechecked=False):
    passed = sum(bool(item['pass']) for item in summary)
    model_passed = sum(item.get('step2_status') == 'ok' and item.get('step7_status') == 'ok'
                       and item.get('generation_status') == 'ok' for item in summary)
    lines = [
        '# 交互维度激活实测', '', f'案例：{case["title"]}', '',
        '链路：Step 2 情境理解 → Step 6 合成 → Step 7 行为生成。原始运行使用真实模型，规则回退不计为模型成功。', '',
        f'真实模型链路成功 {model_passed}/{len(summary)}；本地契约与审阅页综合通过 {passed}/{len(summary)}。', '',
    ]
    if rechecked:
        lines += [
            '本报告根据已保存的原始结果重检，新增模型调用为 0。模型输出、动作和原始运行耗时均未改写。', '',
            '测试脚本原先在 Flask 请求上下文之外构建审阅页，导致已经成功生成的轮次被记为失败。现已在正确请求上下文中重新构建视图并渲染 Step 2、6、7 页面。此脚本错误与真实模型的网络或输出错误分别记录；模型失败仍保持失败。', '',
            '原始 summary.json 保存在 summary.original.json；原始报告如存在，保存在 测试报告_原始.md。每轮 original_harness_result 保留原判定与错误。', '',
        ]
    lines += ['| 次数 | 人格 | Step 2 | Step 7 | 动态/总约束 | 动作数 | 真实调用数 | 页面渲染 | 综合结果 |',
              '|---|---|---|---|---:|---:|---:|---|---|']
    for item in summary:
        lines.append(f'| {item["run"]} | {item["persona"]} | {item.get("step2_status", "未完成")} | '
                     f'{item.get("step7_status", "未执行")} | {len(item.get("activated_dimensions", []))}/{item.get("constraint_count", "—")} | '
                     f'{item.get("action_count", "—")} | {len(item.get("model_calls", []))} | '
                     f'{"通过" if item.get("ui_render_pass") else "未通过/未执行"} | {"通过" if item["pass"] else "失败"} |')
    lines += ['', '## 每轮记录']
    for item in summary:
        lines += ['', f'### 第 {item["run"]} 次（{item["persona"]}）',
                  f'- 原始运行耗时：{item.get("seconds", "未记录")} 秒；后端契约核验：{"通过" if item.get("backend_contract_pass") else "未通过"}。']
        for activation in item.get('activated_dimensions', []):
            lines.append(f'- {activation["dimension"]}：{activation["evidence"]}；要求：{activation["context_requirement"]}')
        if item.get('review_url'):
            lines.append(f'- [查看本轮第六步]({item["review_url"]})')
        if item.get('failure'):
            lines.append(f'- 当前错误：{item["failure"]}；类别：{(item.get("error") or {}).get("category", item.get("failure_type"))}')
        if item.get('ui_failure'):
            lines.append(f'- 页面渲染错误：{item["ui_failure"]}')
        original = item.get('original_harness_result') or {}
        if original.get('failure'):
            scope = '测试脚本缺少 Flask 上下文' if 'Working outside of' in original['failure'] else '原始运行失败'
            lines.append(f'- 原始记录保留：{scope}；原判定为失败。详细错误见 summary.original.json。')
    lines += ['', '验证范围：新字段格式、0—4 动态上限、固定四项、情境约束传递、精简模型输入、现有行为校验、Step 2/6/7 审阅页渲染与模型调用数量。本轮未调用 Step 8 故事模型，也未执行实际车辆动作。']
    (directory / '测试报告.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def recheck_directory(directory):
    """Recheck existing files; this path never invokes generation or get_llm."""
    directory = directory.resolve(strict=True)
    summary_path = directory / 'summary.json'
    original_path = directory / 'summary.original.json'
    if not original_path.exists():
        shutil.copy2(summary_path, original_path)
    report = directory / '测试报告.md'
    original_report = directory / '测试报告_原始.md'
    if report.exists() and not original_report.exists():
        shutil.copy2(report, original_report)
    original_summary = json.loads(original_path.read_text(encoding='utf-8'))
    case = json.loads((directory / 'case.json').read_text(encoding='utf-8'))
    summary = []
    for original in original_summary:
        entry = deepcopy(original)
        entry['original_harness_result'] = {
            key: deepcopy(original[key]) for key in ('pass', 'failure_type', 'failure', 'error') if key in original
        }
        for key in ('failure', 'failure_type', 'error', 'ui_failure'):
            entry.pop(key, None)
        entry.update(backend_contract_pass=False, ui_render_pass=False)
        entry['pass'] = False
        entry['recheck'] = {'mode': 'local_saved_result_only', 'additional_model_calls': 0,
                            'checked_at': datetime.now().isoformat(timespec='seconds')}
        result = None
        try:
            result = json.loads((directory / f'run_{entry["run"]}_result.json').read_text(encoding='utf-8'))
            validate_recorded_result(result, entry)
            entry['backend_contract_pass'] = True
        except Exception as exc:
            entry['failure_type'] = type(exc).__name__
            entry['failure'] = str(exc)
        if result is not None:
            req = (result.get('_review_internal') or {}).get('request') or pipeline.default_request_scaffold(
                entry['persona'], case['user_utterance'], f'recheck_{entry["session_id"]}')
            session = {'session_id': entry['session_id'], 'persona': entry['persona'], 'backend': 'openai',
                       'request': req, 'result': result, 'confirmed_step': 6, 'prompt_overrides': {},
                       'saved_path': entry.get('session_file')}
            try:
                entry['ui_renders'] = render_review_pages(entry['session_id'], session)
                entry['ui_render_pass'] = True
            except Exception as exc:
                entry['ui_failure'] = f'{type(exc).__name__}: {exc}'
        entry['pass'] = entry['backend_contract_pass'] and entry['ui_render_pass']
        summary.append(entry)
    write_json(summary_path, summary)
    write_report(directory, case, summary, rechecked=True)
    print(json.dumps({'recheck_complete': True, 'passed': sum(item['pass'] for item in summary),
                      'total': len(summary), 'additional_model_calls': 0, 'directory': str(directory)}, ensure_ascii=False), flush=True)


class TrackedClient:
    def __init__(self, client, records):
        self.client = client
        self.model = client.model
        self.records = records

    def chat(self, messages, **kwargs):
        start = time.monotonic()
        record = {'call': len(self.records) + 1, 'status': 'running'}
        self.records.append(record)
        try:
            result = self.client.chat(messages, **kwargs)
            record.update(status='ok', response_chars=len(result))
            return result
        except Exception as exc:
            record.update(status='error', error_type=type(exc).__name__)
            raise
        finally:
            record['seconds'] = round(time.monotonic() - start, 3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=5)
    parser.add_argument('--personas', default='INFP,ESTJ')
    parser.add_argument('--recheck-directory', type=Path,
                        help='只检查已完成目录中的保存结果与页面，不进行任何模型调用')
    args = parser.parse_args()
    if args.recheck_directory:
        recheck_directory(args.recheck_directory)
        return
    case = json.loads((ROOT / 'cases/kangyuan_step2.json').read_text(encoding='utf-8'))
    directory = ROOT / 'output' / ('interaction_activation_probe_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    directory.mkdir(parents=True)
    write_json(directory / 'case.json', case)
    print('RECORD_DIRECTORY ' + str(directory), flush=True)
    personas = args.personas.split(',')
    summary = []
    original_get_llm = pipeline.get_llm
    try:
        for index in range(args.count):
            persona = personas[index % len(personas)]
            session_id = uuid.uuid4().hex[:12]
            calls = []
            client = get_llm('openai')
            client.race_n = 1
            tracked = TrackedClient(client, calls)
            pipeline.get_llm = lambda *a, **k: tracked
            req = pipeline.default_request_scaffold(persona, case['user_utterance'], f'interaction_probe_{session_id}')
            entry = {'run': index+1, 'persona': persona, 'model': tracked.model, 'session_id': session_id, 'pass': False}
            started = time.monotonic()
            result = None
            print(json.dumps({'event':'started', **entry}, ensure_ascii=False), flush=True)
            try:
                # Stop at Step 6 first so a failed upstream response is never
                # counted as a successful rule-fallback planning run.
                result = pipeline.generate_behavior_plan(req, llm_backend='openai', defer_step7=True)
                steps = {s['step']: s for s in result['audit_steps']}
                step2 = steps[2]
                entry['step2_status'] = step2['status']
                if step2['status'] != 'ok':
                    entry['error'] = step2.get('error_info') or {'category':'step2_fallback'}
                    raise RuntimeError('Step 2 did not return a valid model result')
                activations = step2['output']['activated_interaction_dimensions']
                validate_activations(activations)
                policy = steps[6]['output']['interaction_policy']
                assert len(policy['fixed_dimensions']) == 4
                assert policy['activated_dimensions'] == activations
                assert len(policy['constraints']) == 4 + len(activations)
                entry['activated_dimensions'] = activations
                entry['constraint_count'] = len(policy['constraints'])
                write_json(directory / f'run_{index+1}_step2_step6.json', result)
                print(json.dumps({'event':'step2_complete','run':index+1,'dimensions':[a['dimension'] for a in activations], 'constraints':len(policy['constraints'])},ensure_ascii=False),flush=True)
                result = pipeline.rerun_from_review_step(result, step_no=7, llm_backend='openai')
                steps = {s['step']: s for s in result['audit_steps']}
                step7 = steps[7]
                entry['step7_status'] = step7['status']
                entry['generation_status'] = result.get('generation_status')
                actions = step7['output'].get('validated_actions') or []
                entry['action_count'] = len(actions)
                if result.get('generation_status') != 'ok':
                    entry['error'] = step7['output'].get('generation_error') or step7['output'].get('llm_error') or {}
                    raise RuntimeError('Step 7 did not generate a valid behavior plan')
                assert actions, 'The case requires tool actions'
                prompt = json.loads(step7['prompt_used']['user_prompt'])
                dimensions = prompt['flow_context']['interaction_dimensions']
                assert dimensions['compiled_prompt'] == steps[6]['output']['interaction_policy']['compiled_prompt']
                assert not {'raw_interaction','scaled_1_to_5','function_dimension_descriptions'} & set(dimensions)
                assert len(calls) == 2, f'Expected exactly two model calls, got {len(calls)}'
                entry['model_calls'] = calls
                validate_recorded_result(result, entry)
                entry['backend_contract_pass'] = True
                session = {'session_id':session_id,'persona':persona,'backend':'openai','request':req,'result':result,'confirmed_step':6,'prompt_overrides':{}}
                entry['session_file'] = save_session_artifact(session_id, session)
                entry['review_url'] = f'http://127.0.0.1:10086/flow_review/session/{session_id}/step/6'
                entry['ui_renders'] = render_review_pages(session_id, session)
                entry['ui_render_pass'] = True
                entry['pass'] = True
            except Exception as exc:
                entry['failure_type'] = type(exc).__name__
                entry['failure'] = str(exc)
            finally:
                if result is not None:
                    write_json(directory / f'run_{index+1}_result.json', result)
                entry['seconds'] = round(time.monotonic() - started, 3)
                entry['model_calls'] = calls
                summary.append(entry)
                write_json(directory / 'summary.json', summary)
                print(json.dumps({'event':'finished',**entry},ensure_ascii=False),flush=True)
    finally:
        pipeline.get_llm = original_get_llm
    passed = sum(e['pass'] for e in summary)
    write_report(directory, case, summary)
    print(json.dumps({'complete':True,'passed':passed,'total':len(summary),'directory':str(directory)},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
