"""Run the current Step 2 in isolation and preserve the exact model exchange."""
from __future__ import annotations

import json
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.llm_router import get_llm
from engine.model_intent import INTENT_SYSTEM, run_intent
from engine.pipeline import default_request_scaffold


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--personas', default='ESTJ,INFP,NO_MBTI')
    args = parser.parse_args()
    case = json.loads((ROOT / 'cases/kangyuan_step2.json').read_text(encoding='utf-8'))
    directory = ROOT / 'output' / ('step2_probe_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    directory.mkdir(parents=True)
    (directory / 'system_prompt.txt').write_text(INTENT_SYSTEM, encoding='utf-8')
    print(str(directory), flush=True)

    def run(persona):
        client = get_llm('openai')
        # One request per persona is sufficient; no racing duplicate requests for this probe.
        client.race_n = 1
        client.timeout = args.timeout
        req = default_request_scaffold(persona, case['user_utterance'], f'step2_probe_{persona}')
        started = time.monotonic()
        result = run_intent(req, 'openai', client)
        elapsed = round(time.monotonic() - started, 2)
        (directory / f'{persona}_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        (directory / f'{persona}_user_prompt.json').write_text(result['prompt_used']['user_prompt'], encoding='utf-8')
        (directory / f'{persona}_output.json').write_text(json.dumps(result['output'], ensure_ascii=False, indent=2), encoding='utf-8')
        if result.get('raw_llm_text'):
            (directory / f'{persona}_raw.txt').write_text(result['raw_llm_text'], encoding='utf-8')
        summary = {'persona': persona, 'model': result['model_version'], 'fallback': result['fallback'],
                   'seconds': elapsed, 'scenario_id': result['scenario_id'],
                   'context': result['output'].get('context'),
                   'needs': result['output'].get('needs'), 'risk': result['output'].get('risk')}
        if result.get('error_info'):
            summary['error_category'] = result['error_info']['category']
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return summary

    with ThreadPoolExecutor(max_workers=3) as pool:
        summaries = list(pool.map(run, args.personas.split(',')))
    (directory / 'summary.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
