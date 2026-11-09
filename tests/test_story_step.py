from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import model_story
from engine.llm_router import LLMRequestError
from web import flow_review_app as review


BEHAVIOR_SENTENCE = '助手判断各路线的迟到风险与可用缓冲，只呈现与按时到达相关的有限方案，留出选择空间。'


def story_fixture():
    return {
        'title': '让行程有据可依',
        'paragraphs': [
            {'segments': [{'text': '助手围绕这次赴约安排服务。', 'change_id': None, 'action_ids': []}]},
            {'segments': [{'text': '助手先比较候选路线。' + BEHAVIOR_SENTENCE + '随后准备启动导航。', 'change_id': 'c1', 'action_ids': [1]}]},
            {'segments': [{'text': '助手根据确认结果推进后续行程。', 'change_id': None, 'action_ids': []}]},
        ],
        'core_changes': [{'id': 'c1', 'label': '留出选择空间', 'action_ids': [1], 'highlight_text': BEHAVIOR_SENTENCE}],
    }


def grounded_story_fixture(payload):
    """A deterministic model stub that follows the real supplied action contract."""
    paragraphs = [{'segments': []} for _ in range(3)]
    changes = []
    actions = payload['behavior_sequence']
    chosen = next((a['id'] for a in actions if a.get('execution_mode') == 'choose_execute'), actions[0]['id'])
    for index, action in enumerate(actions):
        conditions = action.get('preconditions') or []
        guard = ('在' + '；'.join(conditions) + '后，') if conditions else ''
        if action.get('execution_mode') == 'conditional_auto' and not guard:
            guard = '满足执行条件后，'
        text = guard + '助手会准备' + action['name']
        if action.get('execution_mode') == 'choose_execute':
            text += '，由用户选择方案'
        elif action.get('execution_mode') == 'confirm_execute':
            text += '，经用户确认后执行'
        text += '。'
        cid = 'c1' if action['id'] == chosen and payload['persona'] != 'NO_MBTI' else None
        paragraphs[min(2, index * 3 // len(actions))]['segments'].append({
            'text': text, 'change_id': cid, 'action_ids': [action['id']],
        })
        if cid:
            changes.append({'id': cid, 'label': '留出选择空间', 'action_ids': [action['id']], 'highlight_text': text})
    for paragraph in paragraphs:
        if not paragraph['segments']:
            paragraph['segments'] = [{'text': '助手关注这次行程。', 'action_ids': [], 'change_id': None}]
    return {'title': '让行程有据可依', 'paragraphs': paragraphs, 'core_changes': changes,
            'spoken_reply': {'text': '在相关条件满足后，我会准备' + actions[0]['name'] + '，由你选择方案，经你确认后执行。',
                             'action_ids': [actions[0]['id']]}}


@pytest.fixture
def flow(monkeypatch, tmp_path):
    monkeypatch.setattr(review, 'OUT_DIR', tmp_path)
    monkeypatch.setattr(review, '_session_store', {})
    calls = []

    class FakeClient:
        model = 'story-test'
        race_n = 3

        def chat(self, messages, **kwargs):
            assert self.race_n == 1
            calls.append(messages)
            return json.dumps(grounded_story_fixture(json.loads(messages[1]['content'])), ensure_ascii=False)

    monkeypatch.setattr(model_story, 'get_llm', lambda backend: FakeClient())
    client = review.app.test_client()
    response = client.post('/start', data={
        'persona': 'INFP', 'llm_backend': 'rules',
        'user_utterance': '今天早上十点前去康源大厦见客户，我第一次去。',
    })
    sid = response.headers['Location'].split('/')[2]
    for step in range(2, 7):
        client.post(f'/session/{sid}/step/{step}/confirm')
    return client, sid, review._session_store[sid], calls


def confirm_sequence(client, sid):
    client.post(f'/session/{sid}/step/7/generate')
    client.post(f'/session/{sid}/step/7/confirm')


def test_story_only_runs_on_explicit_generate_after_confirmed_sequence(flow):
    client, sid, session, calls = flow
    client.post(f'/session/{sid}/step/7/confirm')
    assert session['confirmed_step'] == 6  # Pending Step 7 cannot be skipped.
    client.post(f'/session/{sid}/step/8/generate')
    assert calls == []
    locked = client.get(f'/session/{sid}/step/8')
    assert locked.status_code == 302
    client.post(f'/session/{sid}/step/7/generate')
    client.post(f'/session/{sid}/step/8/generate')
    assert calls == []  # A generated but unconfirmed sequence still blocks Step 8.
    confirm = client.post(f'/session/{sid}/step/7/confirm')
    assert confirm.headers['Location'].endswith('/step/8')
    page = client.get(confirm.headers['Location']).get_data(as_text=True)
    assert '生成场景故事' in page
    assert 'Step 7 已确认' in page
    assert calls == []
    client.post(f'/session/{sid}/step/8/confirm')
    assert session['confirmed_step'] == 7  # Story must exist before confirmation.
    original = copy.deepcopy(session['result'])
    client.post(f'/session/{sid}/step/8/generate')
    assert len(calls) == 1
    assert session['story_step']['status'] == 'ok', session['story_step'].get('error_info')
    payload = json.loads(calls[0][1]['content'])
    assert payload['persona'] == 'INFP'
    assert len(payload['behavior_sequence']) == len(original['audit_steps'][-1]['output']['validated_actions'])
    assert 'capability_catalog' not in payload
    assert session['result'] == original  # The seven-step plan remains intact.
    page = client.get(f'/session/{sid}/step/8').get_data(as_text=True)
    assert '本次输入' in page and '选择的人格' in page and '用户原始情境' in page
    assert 'INFP' in page and '今天早上十点前去康源大厦见客户，我第一次去。' in page
    assert page.index('本次输入') < page.index('Step 7 · 已确认的行为序列')
    quote = session['story_step']['output']['core_changes'][0]['highlight_text']
    assert 'story-INFP' in page and f'<strong>{quote}</strong>' in page
    assert '核心改变点' in page and '留出选择空间' in page
    assert '助手对你说' not in page
    assert session['story_step']['output']['spoken_reply']['text'] not in page
    client.post(f'/session/{sid}/step/8/generate')
    client.get(f'/session/{sid}/step/8')
    client.post(f'/session/{sid}/step/8/confirm')
    assert len(calls) == 1  # Refresh, duplicate submit and confirmation do not regenerate.
    assert session['confirmed_step'] == 8


def test_failed_sequence_cannot_unlock_story(flow):
    client, sid, session, calls = flow
    client.post(f'/session/{sid}/step/7/generate')
    session['result']['audit_steps'][-1]['status'] = 'fallback'
    client.post(f'/session/{sid}/step/7/confirm')
    assert session['confirmed_step'] == 6
    session['confirmed_step'] = 7  # Even a stale confirmation cannot bypass validation.
    client.post(f'/session/{sid}/step/8/generate')
    assert not calls


def test_story_failure_is_visible_and_retry_requires_a_click(flow, monkeypatch):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)
    get_client = model_story.get_llm

    class TimeoutClient:
        model = 'timeout-test'

        def chat(self, *args, **kwargs):
            raise LLMRequestError(category='timeout', message='模型请求超时')

    monkeypatch.setattr(model_story, 'get_llm', lambda backend: TimeoutClient())
    client.post(f'/session/{sid}/step/8/generate')
    assert session['story_step']['status'] == 'failed'
    page = client.get(f'/session/{sid}/step/8').get_data(as_text=True)
    assert '生成失败/超时' in page and '重新生成场景故事' in page
    client.post(f'/session/{sid}/step/8/confirm')
    assert session['confirmed_step'] == 7
    monkeypatch.setattr(model_story, 'get_llm', get_client)
    client.post(f'/session/{sid}/step/8/generate')
    assert session['story_step']['status'] == 'ok' and len(calls) == 1


@pytest.mark.parametrize('route', ['step/2/rerun', 'step/7/rerun', 'rerun_all'])
def test_upstream_regeneration_invalidates_story(flow, route):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)
    client.post(f'/session/{sid}/step/8/generate')
    client.post(f'/session/{sid}/step/8/confirm')
    client.post(f'/session/{sid}/{route}', data={'action': 'reset'})
    assert 'story_step' not in session
    assert session['confirmed_step'] < 7
    client.post(f'/session/{sid}/step/8/generate')
    assert len(calls) == 1


def test_story_response_discarded_if_upstream_changed(flow, monkeypatch):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)

    def changed_while_running(*args):
        session['confirmed_step'] = 6
        return {'status': 'ok', 'output': story_fixture()}

    monkeypatch.setattr(review, 'run_story', changed_while_running)
    client.post(f'/session/{sid}/step/8/generate')
    assert not session.get('story_step')


def test_story_session_restores_without_a_new_model_call(flow):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)
    client.post(f'/session/{sid}/step/8/generate')
    review._session_store.clear()
    page = client.get(f'/session/{sid}/step/8')
    assert page.status_code == 200
    assert review._session_store[sid]['story_step'] == session['story_step']
    assert len(calls) == 1


@pytest.mark.parametrize('persona', ['INFP', 'ESTJ', 'NO_MBTI'])
def test_story_colors_escaping_and_baseline_display(flow, persona):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)
    story = story_fixture()
    story['title'] = '<script>alert(1)</script>'
    session['persona'] = persona
    if persona == 'NO_MBTI':
        story['core_changes'] = []
        story['paragraphs'][1]['segments'][0]['change_id'] = None
    session['story_step'] = {'step': 8, 'status': 'ok', 'output': story, 'elapsed_seconds': 1}
    page = client.get(f'/session/{sid}/step/8').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in page
    assert '&lt;script&gt;' in page
    assert '#64d9ae' in page and '#78b7ff' in page
    assert f'class="story story-{persona}"' in page
    assert ('class="change-tags"' in page) == (persona != 'NO_MBTI')


def test_display_preserves_prose_and_only_highlights_exact_excerpt():
    story = story_fixture()
    original = copy.deepcopy(story)
    display = model_story.build_story_display(story, 'INFP')
    assert story == original
    for before, after in zip(story['paragraphs'], display['paragraphs']):
        assert ''.join(s['text'] for s in before['segments']) == ''.join(s['text'] for s in after['segments'])
    highlighted = [s['text'] for p in display['paragraphs'] for s in p['segments'] if s['change_id']]
    assert highlighted == [BEHAVIOR_SENTENCE]
    assert len(BEHAVIOR_SENTENCE) > 28
    assert len(BEHAVIOR_SENTENCE) / sum(len(s['text']) for p in display['paragraphs'] for s in p['segments']) > 0.25
    assert display['core_changes'] == story['core_changes']


def test_legacy_story_never_substitutes_label_or_paragraph_for_a_missing_quote():
    story = story_fixture()
    del story['core_changes'][0]['highlight_text']
    display = model_story.build_story_display(story, 'INFP')
    segments = display['paragraphs'][1]['segments']
    assert not [s['text'] for s in segments if s['change_id']]
    assert segments[0]['change_id'] is None
    assert display['core_changes'] == story['core_changes']


def test_display_preserves_two_related_sentences_without_length_limits():
    story = story_fixture()
    long_text = BEHAVIOR_SENTENCE + '助手允许用户从满足时间要求的候选中决定本次路线。'
    story['paragraphs'][1]['segments'][0]['text'] = long_text
    story['core_changes'][0]['highlight_text'] = long_text
    display = model_story.build_story_display(story, 'ESTJ')
    assert display['paragraphs'][1]['segments'][0] == {'text': long_text, 'change_id': 'c1'}
    assert display['core_changes'] == story['core_changes']


def test_display_only_marks_one_occurrence_of_the_selected_sentence():
    story = story_fixture()
    repeated = {'text': BEHAVIOR_SENTENCE, 'change_id': 'c1'}
    story['paragraphs'] = [{'segments': [copy.deepcopy(repeated)]} for _ in range(3)]
    display = model_story.build_story_display(story, 'INFP')
    assert display['core_changes'] == story['core_changes']
    assert sum(bool(s['change_id']) for p in display['paragraphs'] for s in p['segments']) == 1


def test_story_clock_comparison_supports_common_chinese_time_forms():
    assert model_story._clock_values('晚上八点半前回家。') == {(20, 30)}
    assert model_story._clock_values('预计20:30到达。') == {(20, 30)}


def test_story_passes_action_parameters_to_the_writer_without_capability_catalog():
    session = {'persona': 'INFP', 'request': {}, 'result': {'audit_steps': [
        {'step': 7, 'output': {'validated_actions': [{'name': '温度调节', 'parameters_hint': {'温度': 23}}]}},
    ]}}
    payload = model_story.build_story_input(session)
    assert payload['behavior_sequence'][0]['parameters_hint'] == {'温度': 23}
    assert 'capability_catalog' not in payload


def test_swimlane_uses_step7_data_without_generating_a_story(flow):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)
    before = copy.deepcopy(session['result'])
    page = client.get(f'/session/{sid}/step/8').get_data(as_text=True)
    match = re.search(r'<script type="application/json" id="behavior-flow-data">(.*?)</script>', page, re.S)
    assert match
    payload = json.loads(match.group(1))
    assert payload['persona'] == 'INFP'
    assert payload['actions'] == before['audit_steps'][-1]['output']['validated_actions']
    assert session['result'] == before and calls == []
    assert page.index('场景故事 · INFP') < page.index('id="behavior-flow"')
    step7 = client.get(f'/session/{sid}/step/7').get_data(as_text=True)
    assert 'behavior-swimlane.js' not in step7 and 'id="behavior-flow"' not in step7
    assert client.get('/static/behavior-swimlane.js').status_code == 200
    assert client.get('/static/behavior-swimlane.css').status_code == 200


def test_swimlane_json_cannot_inject_script_elements(flow):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)
    action = session['result']['audit_steps'][-1]['output']['validated_actions'][0]
    action['name'] = '</script><script>alert(1)</script>'
    page = client.get(f'/session/{sid}/step/8').get_data(as_text=True)
    assert '</script><script>alert(1)</script>' not in page
    match = re.search(r'<script type="application/json" id="behavior-flow-data">(.*?)</script>', page, re.S)
    assert json.loads(match.group(1))['actions'][0]['name'] == action['name']
    assert calls == []


@pytest.mark.parametrize('text, refs, message', [
    ('我会帮你处理。', [99], '编号'),
    ('我已经启动导航。', [1], '已执行'),
    ('我会自动下单早餐。', [1], '下单'),
    ('我保证你准时到达。', [1], '保证'),
    ('我预计09:30到达。', [1], '具体时间'),
])
def test_spoken_reply_rejects_unfounded_claims(text, refs, message):
    payload = {'behavior_sequence': [{'id': 1, 'name': '地点搜索', 'execution_mode': 'auto_generate'}]}
    with pytest.raises(ValueError, match=message):
        model_story.validate_spoken_reply({'text': text, 'action_ids': refs}, payload)


def test_spoken_reply_only_covers_mentioned_actions_and_keeps_user_choice():
    payload = {'behavior_sequence': [
        {'id': 1, 'name': '路线选择', 'execution_mode': 'choose_execute', 'preconditions': ['候选路线已生成']},
        {'id': 2, 'name': '导航运行控制', 'execution_mode': 'conditional_auto'},
    ]}
    model_story.validate_spoken_reply({'text': '在候选路线生成后，我会提供方案，由你选择。', 'action_ids': [1]}, payload)
    with pytest.raises(ValueError, match='选择权'):
        model_story.validate_spoken_reply({'text': '在候选路线生成后，我会直接决定。', 'action_ids': [1]}, payload)


def test_saved_speech_addition_is_escaped_and_does_not_trigger_generation(flow):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)
    text = '我会先听你说。<script>alert(1)</script>'
    session['story_step'] = {'step': 8, 'status': 'ok', 'output': story_fixture(),
        'spoken_reply_addition': {'status': 'ok', 'output': {'spoken_reply': {'text': text, 'action_ids': [1]}}}}
    page = client.get(f'/session/{sid}/step/8').get_data(as_text=True)
    assert '助手对你说' not in page
    assert text not in page and not calls


@pytest.mark.parametrize('paragraph_count', [1, 2, 4, 5])
def test_story_publishes_without_title_or_content_validation(flow, monkeypatch, paragraph_count):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)
    upstream = copy.deepcopy(session['result'])
    text = '我提供方案，你来决定'
    story = {
        'paragraphs': [{'segments': [{'text': text, 'change_id': 'c1', 'action_ids': [999]}]}]
            + [{'segments': [{'text': f'后续体验第{i}段。'}]} for i in range(paragraph_count - 1)],
        'core_changes': [{'id': 'c1', 'label': '自主', 'highlight_text': text, 'action_ids': []}],
    }

    class Client:
        model = 'unvalidated-story-test'
        def chat(self, messages, **kwargs):
            calls.append(messages)
            return json.dumps(story, ensure_ascii=False)

    monkeypatch.setattr(model_story, 'get_llm', lambda backend: Client())
    response = client.post(f'/session/{sid}/step/8/generate', follow_redirects=True)
    assert response.status_code == 200
    assert session['story_step']['status'] == 'ok'
    assert session['story_step']['output'] == story
    assert len(session['story_step']['output']['paragraphs']) == paragraph_count
    page = response.get_data(as_text=True)
    assert f'<strong>{text}</strong>' in page
    assert 'failure-box" role="alert"' not in page
    assert session['result'] == upstream and len(calls) == 1
    client.post(f'/session/{sid}/step/8/confirm')
    assert session['confirmed_step'] == 8


@pytest.mark.parametrize('story', [
    {'paragraphs': ['助手会陪伴这次行程。', '助手准备候选方案。']},
    {'paragraphs': [{'segments': [{'text': '助手准备方案。', 'change_id': ['bad-id']}, None, 42]}],
     'core_changes': [None, {'id': [], 'label': '自主选择'}]},
    {'paragraphs': [{'segments': {'text': '助手呈现候选路线。', 'change_id': 'missing'}}], 'core_changes': None},
    {'paragraphs': [{'segments': [{'text': '助手提供可选方案。', 'change_id': 'c1'}]}],
     'core_changes': [{'id': 'c1', 'label': '留出选择空间', 'highlight_text': '不存在于正文的引用'}]},
])
def test_display_field_variations_keep_all_available_prose(story):
    original = copy.deepcopy(story)
    display = model_story.build_story_display(story, 'INFP')
    assert display['paragraphs']
    assert all(isinstance(s['text'], str) for p in display['paragraphs'] for s in p['segments'])
    assert not any(s['change_id'] for p in display['paragraphs'] for s in p['segments'])
    assert story == original


@pytest.mark.parametrize('raw', [
    '助手会先听你说。\n\n助手准备候选方案。<script>alert(1)</script>',
    '{"paragraphs":[{"segments":[{"text":"助手会准备方案。"',
])
def test_non_json_story_is_shown_as_escaped_text_without_retry(flow, monkeypatch, raw):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)

    class Client:
        model = 'raw-story-test'
        def chat(self, messages, **kwargs):
            calls.append(messages)
            return raw

    monkeypatch.setattr(model_story, 'get_llm', lambda backend: Client())
    page = client.post(f'/session/{sid}/step/8/generate', follow_redirects=True).get_data(as_text=True)
    assert session['story_step']['status'] == 'ok'
    assert session['story_step']['raw_llm_text'] == raw
    assert '助手' in page and '<script>alert(1)</script>' not in page
    assert 'failure-box" role="alert"' not in page
    assert len(calls) == 1


def test_story_retry_does_not_include_old_validation_feedback(flow):
    _, _, session, _ = flow
    payload = model_story.build_story_input(session)
    session['story_step'] = {'status': 'failed', 'input': payload,
        'error_info': {'category': 'invalid_story', 'message': '旧的三段及高亮校验错误'}}
    assert model_story.build_story_retry_input(session) == payload


def test_previous_content_rejection_can_be_viewed_without_a_model_call(flow):
    client, sid, session, calls = flow
    confirm_sequence(client, sid)
    raw = json.dumps({'paragraphs': ['助手准备路线方案。', '助手关注用户的感受。']}, ensure_ascii=False)
    session['story_step'] = {'step': 8, 'status': 'failed', 'raw_llm_text': raw,
        'error_info': {'category': 'invalid_story', 'message': '场景故事需要三段正文'}}
    before = copy.deepcopy(session['story_step'])
    response = client.get(f'/session/{sid}/step/8')
    page = response.get_data(as_text=True)
    assert response.status_code == 200 and '助手准备路线方案。' in page
    assert 'failure-box" role="alert"' not in page and not calls
    assert session['story_step'] == before


def test_current_story_prompt_uses_advisory_paragraphs_without_title():
    prompt = model_story.current_story_system()
    assert '建议写成2—4段' in prompt
    assert '自检' not in prompt and '输出前检查' not in prompt
    example = json.loads(prompt[prompt.index('{'):])
    assert set(example) == {'paragraphs', 'core_changes'}
