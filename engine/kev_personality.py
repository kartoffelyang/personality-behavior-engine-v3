"""Versioned shared personality input, independent of authored scene constraints."""
import copy
import json
from pathlib import Path
from .persona_profile import build_persona_profile

POLICY_PATH=Path(__file__).resolve().parents[1]/'config/kev_personality_policies.json'


def policy_config():
    return json.loads(POLICY_PATH.read_text())


def role_policies():
    config=policy_config()
    return {p:{**copy.deepcopy(value),'version':config['version']} for p,value in config['personas'].items()}


def personality_input(eng,pid):
    config=policy_config()
    return {'policy_version':config['version'],
            'role_identity':config['role_identity'],
            'persona_profile':build_persona_profile(pid,eng.personas.get(pid)),
            'role_policy':{**copy.deepcopy(config['personas'][pid]),'version':config['version']},
            'decision_contract':config['decision_contract']}
