"""Replay only Step 7 from a saved activation case, retaining original evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import pipeline
from engine.llm_router import get_llm
from probe_interaction_activation import TrackedClient, render_review_pages, write_json
from web.flow_review_app import save_session_artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output_directory', type=Path)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding='utf-8'))
    args.output_directory.mkdir(parents=True, exist_ok=False)
    client = get_llm('openai')
    client.race_n = 1
    calls = []
    tracked = TrackedClient(client, calls)
    original = pipeline.get_llm
    session_id = uuid.uuid4().hex[:12]
    record = {'kind': 'saved_step2_step7_replay', 'source': str(args.source.resolve()),
              'session_id': session_id, 'model': client.model, 'pass': False}
    start = time.monotonic()
    pipeline.get_llm = lambda *a, **k: tracked
    try:
        result = pipeline.rerun_from_review_step(source, step_no=7, llm_backend='openai')
        write_json(args.output_directory / 'result.json', result)
        steps = {s['step']: s for s in result['audit_steps']}
        record['generation_status'] = result.get('generation_status')
        if result.get('generation_status') != 'ok':
            record['error'] = (steps[7].get('error_info') or
                               steps[7]['output'].get('generation_error') or
                               steps[7]['output'].get('llm_error') or {})
            raise RuntimeError(record['error'].get('message') or 'Step 7 generation failed')
        assert len(calls) == 1, 'Replay must call only Step 7'
        record['action_count'] = len(steps[7]['output']['validated_actions'])
        record['activated_dimensions'] = steps[2]['output']['activated_interaction_dimensions']
        req = result['_review_internal']['request']
        session = {'session_id': session_id, 'persona': req['persona'], 'backend': 'openai',
                   'request': req, 'result': result, 'confirmed_step': 6, 'prompt_overrides': {}}
        record['ui_renders'] = render_review_pages(session_id, session)
        record['session_file'] = save_session_artifact(session_id, session)
        record['review_url'] = f'http://127.0.0.1:10086/flow_review/session/{session_id}/step/7'
        record['pass'] = True
    except Exception as exc:
        record['failure'] = f'{type(exc).__name__}: {exc}'
    finally:
        pipeline.get_llm = original
        record['model_calls'] = calls
        record['seconds'] = round(time.monotonic() - start, 3)
        write_json(args.output_directory / 'summary.json', record)
        print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
