from copy import deepcopy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.pipeline import default_request_scaffold, generate_behavior_plan, rerun_from_review_step
from engine.model_intent import run_intent


def test_social_fact_only_in_facts_survives_new_and_resumed_planning(monkeypatch):
    import engine.pipeline as pipeline
    utterance = '带我回家，后排宝宝已经睡着了。'
    request = default_request_scaffold('INFP', utterance)
    shared = run_intent(request, 'rules', None)
    fact = {'category': 'social', 'source': 'utterance', 'fact': '后排宝宝已经睡着了', 'confidence': 1.0}
    shared['output']['facts'] = [fact]
    shared['output']['task']['core_goal'] = '回家'
    shared['output']['task']['explicit_intents'] = ['回家']
    before = deepcopy(shared)
    partial = generate_behavior_plan(request, llm_backend='rules', shared_intent=shared, defer_step7=True)
    flow = partial['audit_steps'][-1]['input']['flow_context']
    assert flow['scenario_understanding']['facts'] == [fact]
    assert flow['user_utterance'] == utterance
    assert shared == before
    # Simulate an old projection that dropped facts; rerun restores from Step 2.
    partial['_review_internal']['flow_context']['scenario_understanding'].pop('facts')
    captured = {}
    original = pipeline.run_planner
    def capture(**kwargs):
        captured.update(deepcopy(kwargs['flow_context']))
        return original(**kwargs)
    monkeypatch.setattr(pipeline, 'run_planner', capture)
    rerun_from_review_step(partial, step_no=7, llm_backend='rules')
    assert captured['scenario_understanding']['facts'] == [fact]
    assert captured['user_utterance'] == utterance
