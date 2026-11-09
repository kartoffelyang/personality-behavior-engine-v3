"""Safe, per-HTTP-request evidence for locating gateway connection failures."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import requests
import httpx

from .llm_call_log import log_llm_request


def redact(text: str, secret: str = '') -> str:
    value = str(text)
    if secret:
        value = value.replace(secret, '[REDACTED]')
    value = re.sub(r'(?i)(Bearer\s+)[^\s,\"\']+', r'\1[REDACTED]', value)
    value = re.sub(r'(https?://)[^\s/@]+(?::[^\s/@]*)?@', r'\1[REDACTED]@', value)
    return value


def safe_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if ':' in host:
        host = '[' + host + ']'
    port = ':' + str(parsed.port) if parsed.port else ''
    return urlunsplit((parsed.scheme, host + port, parsed.path, '', ''))


def exception_chain(exc: Exception, secret: str = '') -> list[dict]:
    pending, seen, result = [exc], set(), []
    while pending and len(result) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append({'type': type(current).__name__,
                       'message': redact(str(current), secret)[:700]})
        for nested in (current.__cause__, current.__context__, getattr(current, 'reason', None), *current.args):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return result


def failure_kind(exc: Exception, phase: str) -> str:
    if getattr(exc, 'failure_kind', None):
        return exc.failure_kind
    if isinstance(exc, requests.exceptions.ProxyError):
        return 'proxy_error'
    if isinstance(exc, requests.exceptions.SSLError):
        return 'tls_error'
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return 'connect_timeout'
    chain = ' '.join(item['type'] + ' ' + item['message'] for item in exception_chain(exc)).lower()
    if any(word in chain for word in ('sslerror', 'sslcertverificationerror', 'certificate_verify_failed')):
        return 'tls_error'
    if 'readtimeout' in chain or 'read timed out' in chain:
        return 'response_body_timeout' if phase == 'reading_response_body' else 'response_headers_timeout'
    if 'nameresolutionerror' in chain or 'getaddrinfo failed' in chain:
        return 'dns_error'
    if any(word in chain for word in ('10054', 'connectionreset', 'connection reset')):
        return 'connection_reset'
    if any(word in chain for word in ('remotedisconnected', 'incompleteread', 'chunkedencoding', 'remoteprotocolerror')):
        return 'response_interrupted'
    return 'transport_error'


def diagnostics_from_exception(exc: Exception) -> dict | None:
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        details = getattr(current, '_llm_diagnostics', None)
        response = getattr(current, 'response', None)
        if details is None and response is not None:
            details = getattr(response, '_llm_diagnostics', None)
        if details is not None:
            return details
        current = current.__cause__ or current.__context__
    return None


class StreamResponseError(requests.RequestException):
    """A received stream cannot be used as a complete model answer."""

    def __init__(self, message: str, kind: str = 'invalid_stream'):
        super().__init__(message)
        self.failure_kind = kind


async def _bounded(awaitable, seconds: float, kind: str):
    # Cancelling the coroutine unwinds the HTTP context and closes its socket;
    # no background request is left running after a deadline.
    try:
        return await asyncio.wait_for(awaitable, max(0.001, seconds))
    except TimeoutError as exc:
        error = requests.Timeout(kind)
        error.failure_kind = kind
        raise error from exc


async def _stream_content(response, trace, started, first_timeout, idle_timeout, secret):
    if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'text/event-stream':
        raise StreamResponseError('网关未返回约定的 text/event-stream 响应')
    lines = response.aiter_lines().__aiter__()
    parts, event_data = [], []
    finish_reason = None
    trace['sse_events'] = 0
    size = 0
    while True:
        if 'first_content_ms' in trace:
            wait, kind = idle_timeout, 'stream_idle_timeout'
        else:
            wait, kind = first_timeout - (time.monotonic() - started), 'first_content_timeout'
            if wait <= 0:
                error = requests.Timeout(kind)
                error.failure_kind = kind
                raise error
        try:
            line = await _bounded(anext(lines), wait, kind)
        except StopAsyncIteration:
            raise StreamResponseError('模型响应中途结束，未收到完整结束标记', 'incomplete_stream')
        # The independent first-content/total deadlines also apply when a
        # provider only sends heartbeats or reasoning without visible output.
        if line.startswith(':'):
            continue
        if line.startswith('data:'):
            value = line[5:].lstrip(' ')
            size += len(value.encode('utf-8'))
            if size > 4 * 1024 * 1024:
                raise StreamResponseError('模型流式响应超出大小限制')
            event_data.append(value)
            continue
        if line or not event_data:
            continue
        data = '\n'.join(event_data)
        event_data.clear()
        trace['sse_events'] += 1
        trace.setdefault('first_event_ms', round((time.monotonic() - started) * 1000, 2))
        if data.strip() == '[DONE]':
            trace['done_received'] = True
            if finish_reason != 'stop' or not ''.join(parts).strip():
                raise StreamResponseError('模型响应缺少正常结束原因或完整正文', 'incomplete_stream')
            return ''.join(parts)
        try:
            event = json.loads(data)
        except (ValueError, TypeError) as exc:
            raise StreamResponseError('网关返回了无法解析的流式数据') from exc
        if not isinstance(event, dict):
            raise StreamResponseError('网关流式数据不是对象')
        if event.get('error'):
            upstream = event['error']
            if isinstance(upstream, dict):
                trace['upstream_error'] = {key: redact(str(upstream[key]), secret)[:600]
                                           for key in ('code', 'type', 'message')
                                           if isinstance(upstream.get(key), (str, int, float))}
            elif isinstance(upstream, str):
                trace['upstream_error'] = {'message': redact(upstream, secret)[:600]}
            raise StreamResponseError('网关在流式响应中返回错误', 'upstream_stream_error')
        if isinstance(event.get('usage'), dict):
            trace['usage'] = {key: event['usage'][key] for key in
                              ('prompt_tokens', 'completion_tokens', 'total_tokens')
                              if isinstance(event['usage'].get(key), int)}
        choices = event.get('choices', [])
        if not isinstance(choices, list):
            raise StreamResponseError('网关流式 choices 格式错误')
        for choice in choices:
            if not isinstance(choice, dict):
                raise StreamResponseError('网关流式 choice 格式错误')
            if choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta', {})
            if not isinstance(delta, dict):
                raise StreamResponseError('网关流式 delta 格式错误')
            content = delta.get('content')
            if content is not None and not isinstance(content, str):
                raise StreamResponseError('模型流式正文不是文本')
            if content:
                trace.setdefault('first_content_ms', round((time.monotonic() - started) * 1000, 2))
                parts.append(content)
            reason = choice.get('finish_reason')
            if reason is not None:
                finish_reason = reason
                trace['finish_reason'] = reason
                if reason != 'stop':
                    raise StreamResponseError('模型未正常完成输出',
                                              'output_truncated' if reason == 'length' else 'invalid_finish_reason')


async def _perform_request(url, payload, headers, timeout, connect_timeout,
                           idle_timeout, proxy, trace, started, secret):
    response = None
    limits = httpx.Timeout(timeout, connect=connect_timeout, write=connect_timeout, pool=connect_timeout)
    # Use the same effective proxy as requests (including Windows registry
    # settings). No global proxy changes and no automatic transport retries.
    async with httpx.AsyncClient(timeout=limits, proxy=proxy) as client:
        try:
            request = client.build_request('POST', url, json=payload, headers=headers)
            response = await _bounded(client.send(request, stream=True), timeout, 'response_headers_timeout')
            trace['headers_ms'] = round((time.monotonic() - started) * 1000, 2)
            trace['http_status'] = response.status_code
            allowed = {'server', 'date', 'cf-ray', 'x-request-id', 'request-id', 'x-new-api-request-id',
                       'x-amzn-trace-id', 'traceparent', 'retry-after', 'content-type'}
            trace['response_headers'] = {k.lower(): redact(v, secret)[:200]
                                         for k, v in response.headers.items() if k.lower() in allowed}
            trace['phase'] = 'reading_response_body'
            body_start = time.monotonic()
            streaming = payload.get('stream') is True and 200 <= response.status_code < 300
            if streaming:
                content = await _stream_content(response, trace, started, timeout, idle_timeout, secret)
                body = json.dumps({'choices': [{'message': {'content': content},
                                               'finish_reason': 'stop'}]}, ensure_ascii=False).encode('utf-8')
            else:
                chunks, size = [], 0
                iterator = response.aiter_bytes().__aiter__()
                while True:
                    try:
                        chunk = await _bounded(anext(iterator), timeout, 'response_body_timeout')
                    except StopAsyncIteration:
                        break
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise StreamResponseError('网关响应超出大小限制', 'response_too_large')
                    chunks.append(chunk)
                body = b''.join(chunks)
            trace['body_ms'] = round((time.monotonic() - body_start) * 1000, 2)
            trace['phase'] = 'response_received'
            trace['status'] = 'ok' if 200 <= response.status_code < 300 else 'http_error'
            result = requests.Response()
            result.status_code = response.status_code
            result.headers.update(response.headers)
            result.url, result.encoding = url, 'utf-8'
            result._content, result._content_consumed = body, True
            result._llm_diagnostics = trace
            if trace['status'] == 'http_error':
                trace['response_excerpt'] = redact(result.text, secret)[:240]
            return result
        finally:
            if response is not None:
                trace['response_bytes'] = response.num_bytes_downloaded
                await response.aclose()


def traced_post(url: str, *, payload: dict, headers: dict, timeout: float,
                race_n: int, secret: str = '', call_id: str | None = None,
                connect_timeout: float = 10.0, idle_timeout: float = 45.0,
                total_timeout: float = 180.0) -> requests.Response:
    started = time.monotonic()
    trace = {'trace_id': uuid.uuid4().hex, 'started_at': datetime.now().isoformat(timespec='milliseconds'),
             'call_id': call_id,
             'url': safe_url(url), 'model': payload.get('model'), 'race_n': race_n,
             'timeout_seconds': timeout, 'connect_timeout_seconds': connect_timeout,
             'idle_timeout_seconds': idle_timeout, 'total_timeout_seconds': total_timeout,
             'stream': payload.get('stream') is True, 'max_tokens': payload.get('max_tokens'),
             'request_bytes': len(json.dumps(payload).encode('utf-8')),
             'phase': 'awaiting_response_headers'}
    # Record only the proxy endpoint selected for this URL, never credentials.
    proxy = None
    try:
        proxy = requests.utils.select_proxy(url, requests.utils.get_environ_proxies(url))
        trace['proxy'] = safe_url(proxy) if proxy else None
    except Exception:
        trace['proxy'] = 'unavailable'
    try:
        return asyncio.run(_bounded(
            _perform_request(url, payload, headers, timeout, connect_timeout,
                             idle_timeout, proxy, trace, started, secret),
            total_timeout, 'total_timeout'))
    except httpx.HTTPError as exc:
        if isinstance(exc, httpx.ConnectTimeout):
            error = requests.ConnectTimeout('网关连接超时')
        elif isinstance(exc, httpx.WriteTimeout):
            error = requests.Timeout('发送模型请求超时')
            error.failure_kind = 'request_write_timeout'
        elif isinstance(exc, httpx.TimeoutException):
            error = requests.Timeout('网关响应超时')
            error.failure_kind = ('response_body_timeout' if trace['phase'] == 'reading_response_body'
                                  else 'response_headers_timeout')
        elif isinstance(exc, httpx.ProxyError):
            error = requests.exceptions.ProxyError('网关代理连接失败')
        else:
            error = requests.ConnectionError('网关连接中断')
        error.__cause__ = exc
        trace.update(status='error', failure_kind=failure_kind(error, trace['phase']),
                     exception_chain=exception_chain(error, secret))
        error._llm_diagnostics = trace
        raise error from exc
    except requests.RequestException as exc:
        trace.update(status='error', failure_kind=failure_kind(exc, trace['phase']),
                     exception_chain=exception_chain(exc, secret))
        exc._llm_diagnostics = trace
        raise
    finally:
        trace['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
        log_llm_request(trace)
