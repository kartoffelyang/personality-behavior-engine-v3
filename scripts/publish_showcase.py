"""Verify and publish the twelve completed real-model case snapshots."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.llm_client import extract_json_object
from engine.model_story import parse_story_response
from engine.planner_validation import validate_plan
from engine.showcase import publish_showcase

parser = argparse.ArgumentParser()
parser.add_argument('--infp', required=True)
parser.add_argument('--estj', required=True)
parser.add_argument('--infp-school')
args = parser.parse_args()
cases = json.loads((ROOT / 'cases/welcome_cases.json').read_text(encoding='utf-8'))
sessions = []
for persona, directory in [('INFP', args.infp), ('ESTJ', args.estj)]:
    for case in cases:
        folder = Path(args.infp_school) if persona == 'INFP' and case['case_id'] == 'case_01' and args.infp_school else Path(directory)
        session = json.loads((folder / case['case_id'] / 'session.json').read_text(encoding='utf-8'))
        if session['persona'] != persona:
            raise ValueError('人格不一致')
        step7 = session['result']['audit_steps'][-1]
        validate_plan(extract_json_object(step7['raw_llm_text']), step7['input']['planner_input'], session['result']['_review_internal']['whitelist'])
        story = session['story_step']
        raw_story = parse_story_response(story['raw_llm_text'])
        if raw_story != story['output']:
            raise ValueError('不得手工改写模型正文冒充原始输出')
        sessions.append(session)
if len(sessions) != 12:
    raise ValueError('必须完成两种人格共12份结果')
manifest = publish_showcase(sessions, cases)
print(json.dumps(manifest, ensure_ascii=False, indent=2))
