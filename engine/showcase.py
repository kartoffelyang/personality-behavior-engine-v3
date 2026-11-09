"""Persist a complete, read-only set of model-generated demonstration cases."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

SHOWCASE_DIR = Path(__file__).resolve().parents[1] / 'output' / 'showcase'


def read_manifest() -> dict:
    try:
        data = json.loads((SHOWCASE_DIR / 'current.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) and isinstance(data.get('cases'), list) else {}
    except (OSError, ValueError):
        return {}


def load_frozen_session(session_id: str) -> dict | None:
    if not re.fullmatch(r'[0-9a-f]{12}', session_id):
        return None
    manifest = read_manifest()
    batch = manifest.get('batch_id', '')
    if not re.fullmatch(r'[0-9a-f]{12}', batch):
        return None
    item = next((c for c in manifest.get('cases', []) if c.get('session_id') == session_id), None)
    if not item:
        return None
    path = SHOWCASE_DIR / batch / f'{session_id}.json'
    try:
        session = json.loads(path.read_text(encoding='utf-8'))
        session.update(frozen=True, case_id=item['case_id'], case_title=item['title'],
                       saved_path=str(path), confirmed_step=8)
        return session
    except (OSError, ValueError, KeyError):
        return None


def publish_showcase(sessions: list[dict], cases: list[dict]) -> dict:
    """Publish only a complete successful batch, leaving earlier batches intact."""
    personas = {s['persona'] for s in sessions}
    expected = {(c['case_id'], persona) for c in cases for persona in personas}
    if not personas or len(sessions) != len(expected) or {(s['case_id'], s['persona']) for s in sessions} != expected:
        raise ValueError('固定展示必须包含全部预设案例，不能发布未完成的一批')
    for session in sessions:
        steps = {s['step']: s for s in session['result']['audit_steps']}
        if (set(steps) != set(range(1, 8)) or steps[2].get('status') != 'ok'
                or steps[7].get('status') != 'ok' or session['result'].get('generation_status') != 'ok'
                or not (steps[7].get('output') or {}).get('validated_actions')
                or session.get('story_step', {}).get('status') != 'ok'):
            raise ValueError(f'{session["case_id"]} 尚未完整成功运行至 Step 8')
        case = next(c for c in cases if c['case_id'] == session['case_id'])
        if session['request']['user_utterance'] != case['request']['user_utterance']:
            raise ValueError('展示输入必须保留预设案例原文')
    batch = uuid.uuid4().hex[:12]
    directory = SHOWCASE_DIR / batch
    directory.mkdir(parents=True, exist_ok=False)
    entries = []
    for source in sorted(sessions, key=lambda s: (s['persona'], s['case_id'])):
        case = next(c for c in cases if c['case_id'] == source['case_id'])
        session_id = uuid.uuid4().hex[:12]
        frozen = {**source, 'session_id': session_id, 'frozen': True, 'confirmed_step': 8,
                  'case_title': case['title']}
        (directory / f'{session_id}.json').write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding='utf-8')
        actions = frozen['result']['audit_steps'][-1]['output']['validated_actions']
        entries.append({'case_id': case['case_id'], 'title': case['title'], 'session_id': session_id,
                        'persona': frozen['persona'], 'utterance': frozen['request']['user_utterance'],
                        'action_count': len(actions)})
    manifest = {'batch_id': batch, 'created_at': datetime.now().isoformat(timespec='seconds'), 'cases': entries}
    temporary = SHOWCASE_DIR / f'.{batch}.json'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(SHOWCASE_DIR / 'current.json')
    return manifest
