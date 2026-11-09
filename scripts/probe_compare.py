"""Measure one real homepage comparison request and save its timing evidence."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    started_at = datetime.now()
    run_id = 'compare_probe_' + started_at.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    directory = ROOT / 'output' / run_id
    directory.mkdir()
    payload = {
        'user_utterance': '今天早上要去康源大厦见一位重要客户，我第一次去，十点前得赶到。外面挺冷，车里也有点凉，前挡看着有些发白。出门太急没吃早饭，路上最好能顺便买点，但别耽误太久。我现在有点着急。',
        'llm_backend': 'openai', 'compare_variants': True, 'request_id': run_id,
    }
    (directory / 'request.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('RECORD_DIRECTORY ' + str(directory), flush=True)
    request = Request('http://127.0.0.1:10086/api/run', data=json.dumps(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json'}, method='POST')
    timer = time.monotonic()
    with urlopen(request, timeout=600) as response:
        body = response.read()
        http_status = response.status
    elapsed = time.monotonic() - timer
    ended_at = datetime.now()
    result = json.loads(body)
    (directory / 'response.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    records = []
    for path in (ROOT / 'output' / 'llm_calls').glob('llm_calls_*.jsonl'):
        if path.stat().st_mtime < started_at.timestamp():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                record = json.loads(line)
                stamp = datetime.fromisoformat(record['ts'])
                if started_at <= stamp <= ended_at:
                    records.append(record)
            except (ValueError, KeyError):
                continue
    stages = []
    variants = result.get('variants', [])
    for variant in variants:
        for step in variant['result'].get('audit_steps', []):
            if step['step'] not in (2, 7) or (step['step'] == 2 and stages):
                continue
            prompt = step.get('prompt_used') or {}
            matches = [r for r in records if r.get('messages') == [
                {'role': 'system', 'content': prompt.get('system_prompt')},
                {'role': 'user', 'content': prompt.get('user_prompt')},
            ]]
            stage = {'step': step['step'], 'persona': 'SHARED' if step['step'] == 2 else variant['persona'],
                     'audit_status': step.get('status'), 'matching_log_count': len(matches)}
            if len(matches) == 1:
                log = matches[0]
                stage.update(seconds=round(log['latency_ms'] / 1000, 3), completed_at=log['ts'],
                             model=log['model'], call_status=log['status'])
            stages.append(stage)
    summary = {
        'request_id': run_id, 'started_at': started_at.isoformat(), 'ended_at': ended_at.isoformat(),
        'http_status': http_status, 'total_seconds': round(elapsed, 3), 'ok': result.get('ok'),
        'variants': [{'persona': v['persona'], 'generation_status': v['result'].get('generation_status'),
                      'action_count': len(v['result'].get('O_Action_Plan') or [])} for v in variants],
        'stages': stages,
        'measurement': 'HTTP submission through complete response body; excludes browser rendering',
    }
    if variants:
        observations = [next(s['output'] for s in v['result']['audit_steps'] if s['step'] == 2) for v in variants]
        summary['identical_step2_outputs'] = all(o == observations[0] for o in observations)
    (directory / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)


if __name__ == '__main__':
    main()
