import asyncio
import json
from pathlib import Path
import sys
import time

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import llm_call_log, llm_router, model_planner, model_story
from engine.registries import CapabilityRegistry
from planner_fixtures import navigation_plan


def event(content=None, reason=None):
    return ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': content},
                                               'finish_reason': reason}]}, ensure_ascii=False) + '\r\n\r\n').encode()


class Body(httpx.AsyncByteStream):
    def __init__(self, chunks, *, delay=0, repeat=False):
        self.chunks, self.delay, self.repeat = chunks, delay, repeat
        self.closed = False

    async def __aiter__(self):
        while True:
            for chunk in self.chunks:
                await asyncio.sleep(self.delay)
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk
            if not self.repeat:
                return

    async def aclose(self):
        self.closed = True


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_call_log, 'LOG_DIR', tmp_path / 'calls')
    monkeypatch.setattr(llm_call_log, 'REQUEST_LOG_DIR', tmp_path / 'requests')
    monkeypatch.setattr(llm_router.requests.utils, 'get_environ_proxies', lambda url: {})
    original = httpx.AsyncClient
    calls = []

    def install(body=None, *, handler=None, content_type='text/event-stream', status=200):
        async def serve(request):
            calls.append(json.loads(request.content))
            if handler:
                return await handler(request)
            return httpx.Response(status, headers={'content-type': content_type, 'x-request-id': 'stream-123'},
                                  stream=body)
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(serve)))
        client = llm_router.OpenAICompatClient('https://gateway.example/v1', 'test', 'secret')
        client.stream = True
        return client

    return install, calls, tmp_path


def rows(root, name):
    return [json.loads(line) for p in (root / name).glob('*.jsonl') for line in p.read_text('utf-8').splitlines()]


def test_fragmented_utf8_stream_returns_only_complete_content_and_records_timings(setup):
    install, calls, root = setup
    raw = (b': ping\r\n\r\n' + event() + event('{"text":"助手') + event('已准备好"}') + event(reason='stop')
           + b'data: {"choices":[],"usage":{"completion_tokens":12}}\r\n\r\n' + b'data: [DONE]\r\n\r\n')
    body = Body([bytes([byte]) for byte in raw])
    client = install(body)
    assert client.chat([]) == '{"text":"助手已准备好"}'
    assert body.closed and len(calls) == 1 and calls[0]['stream'] is True
    trace = rows(root, 'requests')[0]
    assert trace['status'] == 'ok' and trace['finish_reason'] == 'stop' and trace['done_received']
    assert trace['headers_ms'] <= trace['first_event_ms'] <= trace['first_content_ms'] <= trace['elapsed_ms']
    assert trace['response_bytes'] == len(raw)
    assert trace['usage']['completion_tokens'] == 12
    assert rows(root, 'calls')[0]['diagnostics']['trace_id'] == trace['trace_id']


@pytest.mark.parametrize('ending,kind', [
    (b'', 'incomplete_stream'),
    (event(reason='stop'), 'incomplete_stream'),
    (b'data: [DONE]\n\n', 'incomplete_stream'),
    (event(reason='length'), 'output_truncated'),
    (event(reason='content_filter'), 'invalid_finish_reason'),
    (b'data: {"error":{"message":"upstream failed"}}\n\n', 'upstream_stream_error'),
    (b'data: not-json\n\n', 'invalid_stream'),
])
def test_partial_or_bad_stream_never_publishes_or_retries(setup, ending, kind):
    install, calls, root = setup
    body = Body([event('{"partial":'), ending])
    client = install(body)
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    assert llm_router.describe_llm_error(caught.value)['diagnostics']['failure_kind'] == kind
    assert body.closed and len(calls) == 1
    assert rows(root, 'calls')[0]['status'] == 'error'


def test_non_sse_response_is_not_silently_retried(setup):
    install, calls, root = setup
    client = install(Body([b'{"choices":[]}']), content_type='application/json')
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    assert caught.value.category == 'invalid_response' and len(calls) == 1


def test_nonstream_empty_completion_has_specific_error(setup):
    install, calls, root = setup
    payload = json.dumps({
        'choices': [{'message': {'role': 'assistant'}, 'finish_reason': 'stop'}],
        'usage': {'completion_tokens': 0},
    }).encode()
    client = install(Body([payload]), content_type='application/json')
    client.stream = False
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    info = llm_router.describe_llm_error(caught.value)
    assert info['category'] == 'empty_response'
    assert 'completion_tokens=0' in info['response_excerpt']
    assert info['diagnostics']['http_status'] == 200
    assert len(calls) == 1


def test_upstream_event_error_code_preserved_without_credentials(setup):
    install, calls, root = setup
    body = Body([b'data: {"error":{"code":"upstream_timeout","type":"gateway_error",'
                 b'"message":"Bearer secret http://user:password@proxy.test","internal":"private"}}\n\n'])
    client = install(body)
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    info = llm_router.describe_llm_error(caught.value)
    assert info['category'] == 'upstream_error'
    assert info['diagnostics']['upstream_error']['code'] == 'upstream_timeout'
    for hidden in ('secret', 'password', 'private'):
        assert hidden not in json.dumps(info)
    assert len(calls) == 1 and body.closed


def test_stream_http_error_retains_status_request_id_and_safe_excerpt(setup):
    install, calls, root = setup
    body = Body([b'{"error":"gateway timeout secret"}'])
    client = install(body, status=524, content_type='application/json')
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    info = llm_router.describe_llm_error(caught.value)
    assert info['status_code'] == 524
    assert info['diagnostics']['response_headers']['x-request-id'] == 'stream-123'
    assert 'secret' not in json.dumps(info) and body.closed and len(calls) == 1


@pytest.mark.parametrize('mode,kind', [
    ('first', 'first_content_timeout'),
    ('idle', 'stream_idle_timeout'),
    ('total', 'total_timeout'),
    ('nonstream_total', 'total_timeout'),
])
def test_deadlines_cancel_and_close_request_even_with_heartbeats(setup, mode, kind):
    install, calls, root = setup
    chunks = [b': heartbeat\n\n'] if mode == 'first' else [event('a')]
    body = Body(chunks, delay=0.05 if mode == 'idle' else 0.005, repeat=True)
    client = install(body)
    if mode == 'first':
        client.timeout = 0.025
    elif mode == 'idle':
        client.idle_timeout = 0.025
    else:
        client.total_timeout = 0.04
    if mode == 'nonstream_total':
        client.stream = False
    started = time.monotonic()
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    assert time.monotonic() - started < 1
    assert caught.value.category == 'timeout'
    assert llm_router.describe_llm_error(caught.value)['diagnostics']['failure_kind'] == kind
    assert body.closed and len(calls) == 1


def test_header_deadline_cancels_inflight_request(setup):
    install, calls, root = setup
    cancelled = []
    async def slow(request):
        try:
            await asyncio.sleep(2)
        finally:
            cancelled.append(True)
    client = install(handler=slow)
    client.timeout = 0.02
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    assert cancelled and len(calls) == 1
    assert llm_router.describe_llm_error(caught.value)['diagnostics']['failure_kind'] == 'response_headers_timeout'


def test_connect_timeout_keeps_its_own_category(setup):
    install, calls, root = setup
    async def connect_failure(request):
        raise httpx.ConnectTimeout('connect failed', request=request)
    client = install(handler=connect_failure)
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    assert llm_router.describe_llm_error(caught.value)['diagnostics']['failure_kind'] == 'connect_timeout'


def test_transport_disconnect_never_returns_partial_content(setup):
    install, calls, root = setup
    body = Body([event('partial'), httpx.RemoteProtocolError('peer closed connection')])
    client = install(body)
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    assert caught.value.category == 'network_error' and body.closed and len(calls) == 1
    assert llm_router.describe_llm_error(caught.value)['diagnostics']['failure_kind'] == 'response_interrupted'


def test_story_enables_streaming_without_changing_other_clients(monkeypatch):
    class Client:
        model = 'test'
        race_n = 3
        stream = False
        def chat(self, *args, **kwargs):
            assert self.race_n == 1 and self.stream is True
            return '{}'
    client = Client()
    monkeypatch.setattr(model_story, 'get_llm', lambda backend: client)
    model_story.run_story({}, 'test')
    assert client.stream is True


@pytest.mark.parametrize('response_mode', ['complete', 'interrupted', 'gateway_timeout'])
def test_planner_uses_one_stream_and_publishes_only_a_complete_valid_plan(setup, response_mode):
    install, calls, root = setup
    raw_plan = json.dumps(navigation_plan(), ensure_ascii=False)
    body = Body([event(raw_plan[:40]), event(raw_plan[40:])]
                + ([event(reason='stop'), b'data: [DONE]\n\n'] if response_mode == 'complete' else []))
    if response_mode == 'gateway_timeout':
        body = Body([b'{"error":"upstream timeout"}'])
        client = install(body, status=524, content_type='application/json')
    else:
        client = install(body)
    # Start from the ordinary client defaults: Step 7 must opt in itself.
    client.stream = False
    client.race_n = 3
    registry = CapabilityRegistry()
    result = model_planner.run_planner(
        llm_backend='openai', client=client, persona_id='INFP', scenario_id='welcome_on_board',
        parsed={}, whitelist=['navigation'], rule_summary=[],
        flow_context={'capability_catalog': [{'capability': 'navigation', **registry.get('navigation')}]},
        blueprint={'capability_hints': [{'capability': 'navigation', 'constraint': 'must', 'name': '导航'}]},
    )
    assert len(calls) == 1 and calls[0]['stream'] is True and body.closed
    assert rows(root, 'requests')[0]['race_n'] == 1
    assert len(result['attempts']) == 1
    if response_mode == 'complete':
        assert result['fallback'] is False
        assert result['output']['source'] == 'llm'
        assert len(result['output']['behavior_actions']) == len(navigation_plan()['behavior_actions'])
    else:
        assert result['fallback'] is True
        assert result['output']['source'] == 'llm_error'
        assert result['output']['behavior_actions'] == []
        assert result['raw_llm_text'] == ''
        if response_mode == 'gateway_timeout':
            assert result['error_info']['status_code'] == 524
        else:
            assert result['error_info']['diagnostics']['failure_kind'] == 'incomplete_stream'


def test_runtime_and_default_client_disable_duplicate_requests():
    runtime = llm_router.load_runtime()['llm']
    assert runtime['openai']['race_n'] == 1
    assert runtime['carsona']['race_n'] == 1
    assert runtime['openai']['timeout_sec'] == 240
    assert runtime['openai']['total_timeout_sec'] == 240
    assert llm_router.OpenAICompatClient('https://example.test', 'test', 'secret').race_n == 1
    assert llm_router.LLMClient().race_n == 1
