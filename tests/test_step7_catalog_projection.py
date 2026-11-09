from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.model_planner import run_planner
from engine.pipeline import default_request_scaffold, generate_behavior_plan
from engine.registries import CapabilityRegistry
from planner_fixtures import action, plan


def assert_complete_catalog(catalog, registry):
    assert len(catalog) == 7
    assert {cap['capability'] for cap in catalog} == set(registry.all_ids())
    assert sum(len(cap['atomic_abilities']) for cap in catalog) == 57
    for cap in catalog:
        assert set(cap) == {'capability', 'display_name', 'atomic_abilities'}
        original = registry.get(cap['capability'])
        assert cap['display_name'] == original['display_name']
        assert [atom['name'] for atom in cap['atomic_abilities']] == [atom['name'] for atom in original['atomic_abilities']]
        for atom in cap['atomic_abilities']:
            assert {'name', 'definition', 'core_params'} <= set(atom)
            assert 'service_sources' not in atom
            assert set(atom) <= {'name', 'definition', 'core_params', 'parameter_schema'}


@pytest.mark.parametrize('persona', ['NO_MBTI', 'INFP', 'ESTJ'])
def test_new_pipeline_keeps_every_capability_for_every_persona(persona):
    baseline = json.loads((ROOT / 'cases/kangyuan_step7_acceptance.json').read_text(encoding='utf-8'))
    request = default_request_scaffold(persona, baseline['case']['user_utterance'], 'catalog_projection')
    result = generate_behavior_plan(request, llm_backend='rules', defer_step7=True)
    assert_complete_catalog(result['audit_steps'][-1]['input']['flow_context']['capability_catalog'], CapabilityRegistry())


def test_saved_full_catalog_is_slimmed_in_actual_model_request_without_mutation():
    registry = CapabilityRegistry()
    legacy_context = {
        'scenario_understanding': {'core_goal': 'arrive before deadline'},
        'capability_catalog': [dict(registry.get(cid), capability=cid) for cid in registry.all_ids()],
    }
    saved = deepcopy(legacy_context)

    class CapturingClient:
        model = 'fixture'

        def chat(self, messages, **kwargs):
            self.messages = messages
            self.payload = json.loads(messages[1]['content'])
            return json.dumps(plan([action('navigation', '地点搜索')]))

    client = CapturingClient()
    result = run_planner(
        llm_backend='openai', client=client, persona_id='INFP', scenario_id='time_pressure',
        parsed={}, whitelist=registry.all_ids(), rule_summary=[],
        flow_context=legacy_context, blueprint={},
    )
    assert not result['fallback']
    assert_complete_catalog(client.payload['flow_context']['capability_catalog'], registry)
    assert '\n' not in client.messages[1]['content']
    assert client.payload['flow_context']['scenario_understanding'] == saved['scenario_understanding']
    assert legacy_context == saved
