"""Single-persona stories derived from a reviewed Step 7, never from a new plan."""
from __future__ import annotations

import time
import copy
import re
from pathlib import Path
from typing import Any

from .llm_client import extract_json_object
from .llm_router import LLMRequestError, chat_json_with_raw, describe_llm_error, dump_json, get_llm
from .trace_logger import make_step, normalize_persona_step_order
from .story_boundaries import StoryBoundaryError, check_story_boundaries

STORY_SYSTEM_PATH = Path(__file__).resolve().parents[1] / 'prompts' / 'story_system.txt'
SPOKEN_SYSTEM_PATH = STORY_SYSTEM_PATH.with_name('spoken_reply_system.txt')
STORY_TITLE = '场景故事与核心改变点'


def current_story_system() -> str:
    return STORY_SYSTEM_PATH.read_text(encoding='utf-8').strip()


def scenario_assumptions(result: dict) -> list[str]:
    step7 = next((step for step in result.get('audit_steps', []) if step.get('step') == 7), {})
    notes = ((step7.get('output') or {}).get('planner_output') or {}).get('notes') or []
    prefix = '演示假设：'
    return [note[len(prefix):].strip() for note in notes
            if isinstance(note, str) and note.startswith(prefix) and note[len(prefix):].strip()]


def build_story_input(session: dict) -> dict:
    steps = {s['step']: s for s in normalize_persona_step_order(session['result'].get('audit_steps', []))}
    output7 = steps.get(7, {}).get('output') or {}
    strategy = steps.get(5, {}).get('output') or {}
    actions = output7.get('validated_actions') or []
    policy = (steps.get(6, {}).get('output') or {}).get('interaction_policy') or {}
    payload = {
        'persona': session['persona'],
        'user_utterance': (session.get('request') or {}).get('user_utterance') or '',
        'interaction_requirements': policy.get('compiled_prompt') or '',
        'persona_strategy': {
            'action_strategy': strategy.get('action_strategy'),
            'scenario_strategy': strategy.get('scenario_strategy'),
            'action_logic': (steps.get(4, {}).get('output') or {}).get('action_logic') or [],
        },
        'behavior_sequence': [
            {'id': index, **{key: action.get(key) for key in (
                'name', 'capability', 'wave', 'details', 'execution_mode',
                'preconditions', 'persona_influence', 'parameters_hint',
            )}}
            for index, action in enumerate(actions, start=1)
        ],
    }
    mode = ((steps.get(7, {}).get('input') or {}).get('flow_context') or {}).get('generation_mode')
    if mode == 'demo' or (session.get('request') or {}).get('generation_mode') == 'demo':
        payload['generation_mode'] = 'demo'
        payload['scenario_assumptions'] = scenario_assumptions(session['result'])
    return payload


_GUARD = re.compile(r'只有|如果|如需|如有|若|当|待|(?:在|经|获得|完成|确认).*(?:后|时|才)|满足.*条件')
_USER_CHOICE = re.compile(r'(?:用户|你|您).*(?:选择|选定|决定|挑选)|(?:供|由|等|让).*(?:用户|你|您).*(?:选择|选定|决定|挑选)|(?:选择权|决定权).*(?:用户|你|您)|(?:用户|你|您)(?:自行|自主|自己)?取舍')
_USER_CONFIRM = re.compile(r'(?:用户|你|您).*(?:确认|同意|许可|授权)|(?:征得|获得).*(?:确认|同意|许可|授权)')
_NEGATION = re.compile(r'(?:不|未|无须|无需|避免|禁止|不能|无法|尚未|不会|不再|不得|without|not|never)\s*[^，,；;。.!?！？]{0,12}$', re.I)
_ARRIVAL_PROMISE = re.compile(r'(?:保证|确保|一定|必定|准保|guarantee\w*).{0,24}(?:到达|抵达|赶上|arrival|arrive)', re.I)
_TRANSACTION = re.compile(r'下单|订购|订餐|付款|支付|缴费|place\w*\s+(?:an?\s+)?order|orders?\s+breakfast', re.I)
_TIME = re.compile(r'(?<!\d)(\d{1,2})[:：](\d{2})(?!\d)|([零〇一二三四五六七八九十两\d]+)[点时](?:([零〇一二三四五六七八九十两\d]+)分|(半))?')


def _positive_matches(pattern: re.Pattern, text: str):
    """Ignore explicit negative claims such as 不下单 / 无法保证到达."""
    return [m for m in pattern.finditer(text) if not _NEGATION.search(text[max(0, m.start() - 18):m.start()])]


def _clock_values(text: str) -> set[tuple[int, int]]:
    digits = dict(zip('零〇一二三四五六七八九两', [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2]))

    def number(value: str | None) -> int:
        if not value:
            return 0
        if value.isascii() and value.isdigit():
            return int(value)
        if '十' in value:
            tens, _, ones = value.partition('十')
            return (digits.get(tens, 1) if tens else 1) * 10 + digits.get(ones, 0)
        return digits.get(value, -1)

    result = set()
    for match in _TIME.finditer(text):
        hour, minute = ((int(match[1]), int(match[2])) if match[1]
                        else (number(match[3]), 30 if match[5] else number(match[4])))
        prefix = text[max(0, match.start() - 8):match.start()]
        if 0 < hour < 12 and re.search(r'(?:下午|晚上|晚间|夜里|傍晚)[^，,。]*$', prefix):
            hour += 12
        result.add((hour, minute))
    return result


def pending_story_step(session: dict) -> dict:
    return make_step(8, STORY_TITLE, status='pending', input_data=build_story_input(session))


def build_story_retry_input(session: dict) -> dict:
    """A retry uses the current story material without content-validation feedback."""
    return build_story_input(session)


def _items(value: Any) -> list:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _story_paragraphs(story: Any) -> list[dict]:
    """Adapt display fields without judging the generated prose or action references."""
    if isinstance(story, str):
        paragraphs = re.split(r'\n\s*\n', story)
    elif isinstance(story, dict):
        paragraphs = story.get('paragraphs')
        if paragraphs is None:
            paragraphs = story.get('text')
    else:
        paragraphs = story
    result = []
    for paragraph in _items(paragraphs):
        raw_segments = paragraph.get('segments', paragraph) if isinstance(paragraph, dict) else paragraph
        segments = []
        for segment in _items(raw_segments):
            text = segment.get('text') if isinstance(segment, dict) else segment
            if not isinstance(text, str) or not text.strip():
                continue
            cid = segment.get('change_id') if isinstance(segment, dict) else None
            cid = str(cid) if type(cid) in (str, int) and str(cid) else None
            segments.append({'text': text, 'change_id': cid})
        if segments:
            result.append({'segments': segments})
    return result


def parse_story_response(raw_text: str) -> dict:
    """Keep returned prose even when the model does not follow the JSON layout."""
    story = extract_json_object(raw_text)
    if isinstance(story, dict) and _story_paragraphs(story):
        return story
    return {'paragraphs': _story_paragraphs(raw_text), 'core_changes': []}


def build_story_display(story: dict, persona: str) -> dict:
    """Display prose unconditionally; unresolved highlighting stays ordinary text."""
    paragraphs = _story_paragraphs(story)
    if not paragraphs and story:
        paragraphs = _story_paragraphs(dump_json(story))
    changes = []
    if persona != 'NO_MBTI' and isinstance(story, dict):
        for change in _items(story.get('core_changes')):
            if isinstance(change, dict) and isinstance(change.get('label'), str) and change['label'].strip():
                changes.append(copy.deepcopy(change))
    by_id = {str(c['id']): c for c in changes if type(c.get('id')) in (str, int)}
    used = set()
    for paragraph in paragraphs:
        segments = []
        for segment in paragraph['segments']:
            text, cid = segment['text'], segment['change_id']
            quote = by_id.get(cid, {}).get('highlight_text')
            if (cid not in used and isinstance(quote, str) and quote.strip() and quote in text):
                before, _, after = text.partition(quote)
                if before:
                    segments.append({'text': before, 'change_id': None})
                segments.append({'text': quote, 'change_id': cid})
                if after:
                    segments.append({'text': after, 'change_id': None})
                used.add(cid)
            else:
                segments.append({'text': text, 'change_id': None})
        paragraph['segments'] = segments
    return {'paragraphs': paragraphs, 'core_changes': changes}


def validate_spoken_reply(reply: Any, payload: dict) -> None:
    """Check explicit contradictions without imposing story coverage on speech."""
    if not isinstance(reply, dict) or set(reply) != {'text', 'action_ids'}:
        raise ValueError('语音文本需要text和action_ids')
    text, refs = reply['text'], reply['action_ids']
    if not isinstance(text, str) or not text.strip() or '\n' in text.strip():
        raise ValueError('语音文本应为一段非空文字')
    actions = {a['id']: a for a in payload['behavior_sequence']}
    if (not isinstance(refs, list) or not refs
            or any(type(i) is not int or i not in actions for i in refs)
            or len(refs) != len(set(refs))):
        raise ValueError('语音文本必须引用有效且不重复的动作编号')
    source = str(payload.get('user_utterance') or '') + dump_json(payload['behavior_sequence'])
    if _positive_matches(_ARRIVAL_PROMISE, text):
        raise ValueError('语音文本不能保证准时到达')
    if not _clock_values(text) <= _clock_values(source):
        raise ValueError('语音文本包含输入中不存在的具体时间')
    linked = [actions[i] for i in refs]
    if _positive_matches(_TRANSACTION, text) and not any(
            _positive_matches(_TRANSACTION, str(a.get('name') or '') + str(a.get('details') or ''))
            or a.get('name') == '订单管理' for a in linked):
        raise ValueError('语音文本新增了行为序列没有的下单或支付')
    for action in linked:
        mode = action.get('execution_mode')
        # A spoken invitation/confirmation also leaves the decision with the user.
        permission_question = bool(re.search(r'(?:要不要|是否需要|可以吗|好吗|你想选|你愿意)', text))
        user_confirmation = bool(_positive_matches(_USER_CONFIRM, text))
        if mode == 'choose_execute' and not (_positive_matches(_USER_CHOICE, text) or user_confirmation or permission_question):
            raise ValueError('语音文本需要保留用户选择权')
        if mode == 'confirm_execute' and not (user_confirmation or permission_question):
            raise ValueError('语音文本需要保留用户确认')
        # Source restrictions on generated drafts are not runtime conditions;
        # concise speech need not recite every internal planning dependency.
        if mode == 'conditional_auto' and not _positive_matches(_GUARD, text):
            raise ValueError('语音文本需要保留执行条件')
    completed = re.compile(r'(?:我(?:已经|已|正在)|已经为你|已为你)(?:帮你|为你)?(?:开启|打开|启动|设置|调整|选定|下单|支付|发送|规划)')
    if _positive_matches(completed, text):
        raise ValueError('语音文本不能把计划说成已执行')


def run_story(payload: dict, backend: str) -> dict:
    payload = {key: value for key, value in payload.items() if key != 'validation_feedback'}
    prompt = {'system_prompt': current_story_system(), 'user_prompt': dump_json(payload)}
    record = make_step(8, STORY_TITLE, status='failed', input_data=payload, prompt_used=prompt)
    started = time.monotonic()
    record['attempts'] = []
    messages = [{'role': 'system', 'content': prompt['system_prompt']},
                {'role': 'user', 'content': prompt['user_prompt']}]
    for attempt in range(2):
        attempt_started = time.monotonic()
        raw_text = ''
        try:
            client = get_llm(backend)
            if client is None:
                raise LLMRequestError(category='model_unavailable', message='场景故事需要模型后端，请选择模型后重新运行流程。')
            if hasattr(client, 'race_n'):
                client.race_n = 1
            if hasattr(client, 'stream'):
                client.stream = True
            record['model_version'] = getattr(client, 'model', backend)
            raw_text = client.chat(messages, max_tokens=2800)
            record['raw_llm_text'] = raw_text
            if not isinstance(raw_text, str) or not raw_text.strip():
                raise LLMRequestError(category='empty_response', message='模型没有返回故事内容。', retryable=True)
            output = parse_story_response(raw_text)
            warnings = check_story_boundaries(output, payload)
            record.update(status='ok', output=output, quality_warnings=warnings)
            record.pop('error_info', None)
            record['attempts'].append({'attempt': attempt + 1, 'status': 'ok', 'elapsed_seconds': round(time.monotonic()-attempt_started, 2),
                                       'raw_llm_text': raw_text, 'messages': list(messages)})
            break
        except Exception as exc:
            info = ({'category': 'story_boundary', 'message': str(exc), 'retryable': True}
                    if isinstance(exc, StoryBoundaryError) else describe_llm_error(exc))
            record['error_info'] = info
            record['attempts'].append({'attempt': attempt + 1, 'status': 'failed', 'elapsed_seconds': round(time.monotonic()-attempt_started, 2),
                                       'error_info': info, 'raw_llm_text': raw_text, 'messages': list(messages)})
            kind = (info.get('diagnostics') or {}).get('failure_kind')
            transient = kind in {'stream_idle_timeout', 'incomplete_stream', 'response_interrupted', 'connection_reset'}
            if attempt or not (isinstance(exc, StoryBoundaryError) or transient):
                break
            if isinstance(exc, StoryBoundaryError):
                messages = messages + [{'role': 'assistant', 'content': raw_text},
                    {'role': 'user', 'content': dump_json({'validation_feedback': str(exc),
                     'instruction': '修正这处执行边界，保留原动作和必要条件，重新输出完整故事JSON。'})}]
    record['elapsed_seconds'] = round(time.monotonic() - started, 2)
    return record


def run_spoken_reply(payload: dict, backend: str) -> dict:
    """One-time speech addition for saved stories; never called by page reads."""
    prompt = {'system_prompt': SPOKEN_SYSTEM_PATH.read_text(encoding='utf-8').strip(),
              'user_prompt': dump_json(payload)}
    record = make_step(8, '助手语音文本', status='failed', input_data=payload, prompt_used=prompt)
    started = time.monotonic()
    try:
        client = get_llm(backend)
        if client is None:
            raise ValueError('语音文本需要模型后端')
        if hasattr(client, 'race_n'):
            client.race_n = 1
        if hasattr(client, 'stream'):
            client.stream = True
        record['model_version'] = getattr(client, 'model', backend)
        output, raw = chat_json_with_raw(client, [
            {'role': 'system', 'content': prompt['system_prompt']},
            {'role': 'user', 'content': prompt['user_prompt']},
        ], max_tokens=600)
        record['raw_llm_text'] = raw
        if not isinstance(output, dict) or set(output) != {'spoken_reply'}:
            raise ValueError('语音输出字段错误')
        validate_spoken_reply(output['spoken_reply'], payload)
        record.update(status='ok', output=output)
    except Exception as exc:
        record['error_info'] = describe_llm_error(exc)
        if isinstance(exc, ValueError):
            record['error_info']['category'] = 'invalid_spoken_reply'
    record['elapsed_seconds'] = round(time.monotonic() - started, 2)
    return record
