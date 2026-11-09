"""Add real-model speech to frozen cases without regenerating their stories."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.llm_client import extract_json_object
from engine.model_story import build_story_input, run_spoken_reply, validate_spoken_reply
from engine.showcase import SHOWCASE_DIR, read_manifest


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def generate(item, manifest, staging):
    sid = item['session_id']
    session = read(SHOWCASE_DIR / manifest['batch_id'] / f'{sid}.json')
    payload = build_story_input(session)
    folder = staging / f"{item['persona']}_{item['case_id']}"
    saved = folder / 'success.json'
    record = read(saved) if saved.exists() else None
    if record and record['input'] != payload:
        raise ValueError('保存的语音与当前行为序列不一致')
    if not record:
        for path in sorted(folder.glob('attempt_*.json'), reverse=True):
            candidate = read(path)
            if candidate.get('input') != payload or not candidate.get('raw_llm_text'):
                continue
            try:
                output = extract_json_object(candidate['raw_llm_text'])
                validate_spoken_reply(output['spoken_reply'], payload)
            except (ValueError, TypeError, KeyError):
                continue
            record = {**candidate, 'status': 'ok', 'output': output, 'revalidated_from': path.name}
            record.pop('error_info', None)
            write(saved, record)
            break
    for attempt in range(1, 4):
        if record and record.get('status') == 'ok':
            break
        record = run_spoken_reply(payload, session['backend'])
        write(folder / f'attempt_{time.time_ns()}.json', record)
        print(json.dumps({'persona': item['persona'], 'case': item['case_id'],
            'attempt': attempt, 'status': record['status'], 'seconds': record['elapsed_seconds'],
            'error': record.get('error_info', {}).get('message')}, ensure_ascii=False), flush=True)
        if record['status'] == 'ok':
            write(saved, record)
    if not record or record['status'] != 'ok':
        raise ValueError(f"{item['persona']} {item['case_id']} 语音尚未成功")
    validate_spoken_reply(record['output']['spoken_reply'], payload)
    if extract_json_object(record['raw_llm_text']) != record['output']:
        raise ValueError('语音必须保留模型原始输出')
    session['story_step']['spoken_reply_addition'] = record
    return sid, session


def main():
    manifest = read_manifest()
    staging = ROOT / 'output' / 'showcase_speech' / manifest['batch_id']
    results = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(generate, item, manifest, staging) for item in manifest['cases']]
        for future in as_completed(futures):
            sid, session = future.result()
            results[sid] = session
    if read_manifest() != manifest:
        raise ValueError('展示批次已更换，请重新检查后发布')
    batch = uuid.uuid4().hex[:12]
    directory = SHOWCASE_DIR / batch
    directory.mkdir(exist_ok=False)
    write(directory / 'source_manifest.json', manifest)
    for sid, session in results.items():
        # Keep existing URLs and all original records; only append the new trace.
        original = read(SHOWCASE_DIR / manifest['batch_id'] / f'{sid}.json')
        check = json.loads(json.dumps(session, ensure_ascii=False))
        check['story_step'].pop('spoken_reply_addition')
        if check != original:
            raise ValueError('行为序列或原故事发生意外变化')
        write(directory / f'{sid}.json', session)
    updated = {**manifest, 'batch_id': batch, 'speech_added_at': datetime.now().isoformat(timespec='seconds')}
    temporary = SHOWCASE_DIR / f'.{batch}.json'
    write(temporary, updated)
    temporary.replace(SHOWCASE_DIR / 'current.json')
    print(json.dumps({'published': batch, 'cases': len(results)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
