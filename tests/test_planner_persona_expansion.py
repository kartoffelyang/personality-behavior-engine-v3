import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.model_planner import run_planner
from planner_fixtures import action, plan


class Client:
    model = 'fixture'

    def chat(self, messages, **kwargs):
        self.payload = json.loads(messages[1]['content'])
        return json.dumps(plan([action('ecosystem', '服务与地点搜索',
              details='搜索沿途风景候选', persona_influence='探索偏好促发沿途候选搜索')]))


def test_persona_can_expand_beyond_blueprint_without_classifications():
    client = Client()
    result = run_planner(
        llm_backend='openai', client=client, persona_id='INFP', scenario_id='pleasant_trip',
        parsed={}, whitelist=['navigation', 'ecosystem'], rule_summary=[],
        flow_context={'capability_catalog': [
            {'capability': 'ecosystem', 'display_name': '生态服务',
             'atomic_abilities': [{'name': '服务与地点搜索', 'core_params': []}]}]},
        blueprint={'mandatory_capabilities': ['navigation'], 'optional_capabilities': [],
                   'capability_hints': [{'capability': 'navigation', 'constraint': 'must'}]})
    assert not result['fallback']
    assert 'whitelist' not in client.payload
    assert 'blueprint' not in client.payload
    assert 'emotion' not in client.payload
    assert 'needs' not in client.payload
    assert 'risk' not in client.payload
    assert 'no_call' not in result['output']
    actions = result['output']['behavior_actions']
    assert len(actions) == 1
    assert actions[0]['capability'] == 'ecosystem'
    assert 'decision_state' not in actions[0]
    assert result['output']['notes'] == []
