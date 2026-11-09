import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import pipeline
from engine.model_intent import run_intent
from planner_fixtures import action, plan


def test_filter_cannot_turn_a_nonempty_model_plan_into_empty_success(monkeypatch):
    request = pipeline.default_request_scaffold('INFP', '连续打哈欠，已经很困了。')
    observation = run_intent(request, 'rules', None)
    entertainment = action('navigation', '内容搜索', details='搜索休息时可听的内容')
    entertainment.update(capability='entertainment', display_name='影音娱乐')
    class Client:
        model = 'filter-contract-fixture'
        def chat(self, messages, **kwargs):
            return json.dumps(plan([entertainment]), ensure_ascii=False)
    monkeypatch.setattr(pipeline, 'get_llm', lambda *_: Client())
    result = pipeline.generate_behavior_plan(request, llm_backend='openai', shared_intent=observation)
    assert result['generation_status'] == 'failed'
    assert result['generation_error']['category'] == 'planner_validation'
    assert result['O_Action_Plan'] == []
    assert '安全优先下移除 entertainment' in result['audit_steps'][-1]['output']['validation_fixes']
