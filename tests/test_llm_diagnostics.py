import json
from pathlib import Path
import sys

import pytest
import requests
import httpx
from urllib3.exceptions import ReadTimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import llm_call_log, llm_diagnostics, llm_router


@pytest.fixture
def diagnostic_client(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_call_log, 'LOG_DIR', tmp_path / 'calls')
    monkeypatch.setattr(llm_call_log, 'REQUEST_LOG_DIR', tmp_path / 'requests')
    monkeypatch.setattr(requests.utils, 'get_environ_proxies', lambda url: {
        'https': 'http://user:password@127.0.0.1:7892'})
    client = llm_router.OpenAICompatClient('https://gateway.example/v1', 'test', api_key='secret-key', race_n=1)
    return client, tmp_path


def response(status=200, body=None):
    return httpx.Response(status, headers={'X-Request-Id': 'request-123', 'Set-Cookie': 'private-cookie'},
                          json=body or {'choices': [{'message': {'content': 'OK'}}]})


@pytest.fixture
def wire(monkeypatch):
    original = httpx.AsyncClient
    def install(handler):
        def factory(**kwargs):
            assert kwargs['timeout'].connect == 10
            assert kwargs['timeout'].read == 120
            assert kwargs['proxy'] == 'http://user:password@127.0.0.1:7892'
            return original(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(httpx, 'AsyncClient', factory)
    return install


def log_rows(path):
    return [json.loads(line) for file in path.glob('*.jsonl') for line in file.read_text('utf-8').splitlines()]


def test_success_keeps_transport_timings_request_id_without_credentials(diagnostic_client, wire):
    client, root = diagnostic_client
    def post(request):
        assert 'stream' not in json.loads(request.content)
        assert request.headers['Authorization'] == 'Bearer secret-key'
        return response()
    wire(post)
    assert client.chat([{'role': 'user', 'content': 'test'}]) == 'OK'
    trace = log_rows(root / 'requests')[0]
    assert trace['phase'] == 'response_received' and trace['status'] == 'ok'
    assert trace['response_headers']['x-request-id'] == 'request-123'
    assert trace['proxy'] == 'http://127.0.0.1:7892'
    assert trace['headers_ms'] >= 0 and trace['body_ms'] >= 0
    calls = log_rows(root / 'calls')
    assert calls[0]['diagnostics']['trace_id'] == trace['trace_id']
    for secret in ('secret-key', 'password', 'private-cookie'):
        assert secret not in json.dumps([trace, calls])


def test_connection_error_wrapping_body_timeout_is_identifiable(diagnostic_client, wire):
    client, root = diagnostic_client
    class InterruptedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise requests.ConnectionError(ReadTimeoutError(None, '/v1/chat/completions', 'Read timed out'))
            yield b''
    wire(lambda request: httpx.Response(200, headers={'X-Request-Id': 'partial-request'}, stream=InterruptedBody()))
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    info = llm_router.describe_llm_error(caught.value)
    assert info['category'] == 'network_error'
    trace = info['diagnostics']
    assert trace['failure_kind'] == 'response_body_timeout'
    assert trace['phase'] == 'reading_response_body' and trace['http_status'] == 200
    assert {e['type'] for e in trace['exception_chain']} == {'ConnectionError', 'ReadTimeoutError'}
    assert log_rows(root / 'calls')[0]['error']['diagnostics'] == trace


def test_proxy_failure_records_sanitized_cause(diagnostic_client, wire):
    client, root = diagnostic_client
    def post(*a, **kw):
        raise httpx.ProxyError('Failed http://user:password@proxy.test; Bearer secret-key')
    wire(post)
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    trace = llm_router.describe_llm_error(caught.value)['diagnostics']
    assert trace['failure_kind'] == 'proxy_error' and trace['phase'] == 'awaiting_response_headers'
    assert 'password' not in json.dumps(trace) and 'secret-key' not in json.dumps(trace)


@pytest.mark.parametrize('status', [504, 524])
def test_gateway_timeout_allows_manual_retry_without_automatic_retry(diagnostic_client, wire, status):
    client, root = diagnostic_client
    requests_seen = []

    def post(request):
        requests_seen.append(request)
        if len(requests_seen) == 1:
            return response(status, {'error': 'upstream timeout secret-key'})
        return response()

    wire(post)
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    info = llm_router.describe_llm_error(caught.value)
    assert info['category'] == 'gateway_timeout'
    assert info['retryable'] is True
    assert '上游' in info['message'] and '手动重新生成' in info['hint']
    assert info['status_code'] == status
    assert info['diagnostics']['response_headers']['x-request-id'] == 'request-123'
    assert 'secret-key' not in json.dumps(info)
    assert len(requests_seen) == 1
    assert log_rows(root / 'requests')[0]['status'] == 'http_error'
    assert log_rows(root / 'calls')[0]['error']['category'] == 'gateway_timeout'

    # A later user retry is a fresh request, not a permanently disabled client.
    assert client.chat([]) == 'OK'
    assert len(requests_seen) == 2
    assert [row['status'] for row in log_rows(root / 'requests')] == ['http_error', 'ok']


@pytest.mark.parametrize('body', [{'unexpected': 'value'}, {'choices': []}])
def test_invalid_response_is_not_reported_as_network_failure(diagnostic_client, wire, body):
    client, root = diagnostic_client
    wire(lambda request: response(body=body))
    with pytest.raises(llm_router.LLMRequestError) as caught:
        client.chat([])
    assert caught.value.category == 'invalid_response'
    assert llm_router.describe_llm_error(caught.value)['diagnostics']['http_status'] == 200


def test_each_racing_request_has_its_own_trace(diagnostic_client, monkeypatch, wire):
    import threading
    client, root = diagnostic_client
    client.race_n = 3
    lock, completed = threading.Lock(), threading.Event()
    original_log = llm_diagnostics.log_llm_request
    def log(record):
        with lock:
            original_log(record)
            if len(log_rows(root / 'requests')) == 3:
                completed.set()
    monkeypatch.setattr(llm_diagnostics, 'log_llm_request', log)
    wire(lambda request: response())
    assert client.chat([]) == 'OK'
    assert completed.wait(2)
    rows = log_rows(root / 'requests')
    assert len(rows) == 3 and len({r['trace_id'] for r in rows}) == 3
    assert len({r['call_id'] for r in rows}) == 1 and rows[0]['call_id']
    assert log_rows(root / 'calls')[0]['diagnostics']['trace_id'] in {r['trace_id'] for r in rows}
