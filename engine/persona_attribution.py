"""Display evidence for declared personality effects without inferring causation."""
import json
import re


def classify_influence(action, persona, strategy_context=None):
    text = str(action.get('persona_influence') or '').strip()
    if not persona or persona == 'NO_MBTI':
        return {'status': 'none', 'reason': '无人格基线，不标注人格影响。'}
    normalized = re.sub(r'[\s。；，.!！]', '', text)
    if re.fullmatch(r'(?:人格)?(?:未|没有|无)(?:明显|直接|额外|实质|显著)?(?:人格)?(?:影响|改变|差异)(?:此动作|该动作|本动作)?', normalized) or normalized in {'无', '无人格影响', '不适用', 'None', 'none'}:
        return {'status': 'none', 'reason': text}
    if text.startswith('不使用MBTI'):
        return {'status': 'none', 'reason': text}
    # Require an exact source excerpt and exact action excerpt, not just keywords.
    match = re.fullmatch(r'依据[:：](.+?)[；;]\s*改变[:：](.+?)[；;]\s*落点[:：](.+)', text, re.S)
    if match:
        source, change, quote = [v.strip().strip('“”"。') for v in match.groups()]
        context = json.dumps(strategy_context, ensure_ascii=False) if strategy_context else ''
        action_text = ' '.join(str(action.get(k) or '') for k in ('details', 'execution_mode', 'parameters_hint'))
        if len(source) >= 6 and len(change) >= 6 and len(quote) >= 6 and source in context and quote in action_text:
            return {'status': 'grounded', 'reason': change, 'source': source, 'quote': quote}
    return {'status': 'review', 'reason': text or '人格影响字段缺失；不能据此判断没有影响。'}


def annotate_actions(result, persona, actions):
    steps = {s.get('step'): s for s in result.get('audit_steps', [])}
    # Use the strategy actually supplied to this saved generation, not today's config.
    flow = (steps.get(7, {}).get('input') or {}).get('flow_context') or {}
    context = {k: flow[k] for k in ('persona_description', 'persona_scene_strategy', 'interaction_dimensions') if k in flow}
    if not context:
        context = {str(n): steps.get(n, {}).get('output', {}) for n in (4, 5, 6)}
    return [{**a, 'persona_attribution': classify_influence(a, persona, context)} for a in actions]


def has_declared_influence(action, persona):
    """Graph display follows the recorded declaration, without exact-quote review."""
    text = str(action.get('persona_influence') or '').strip()
    return bool(text) and classify_influence(action, persona)['status'] != 'none' and not re.search(r'不确定|无法判断|待核对|尚不明确', text)
