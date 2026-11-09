from __future__ import annotations

import json
import os
import time
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

import requests
import yaml

from .llm_call_log import log_llm_call
from .llm_diagnostics import StreamResponseError, diagnostics_from_exception, redact, traced_post
from .llm_client import LLMClient, extract_json_object
from .race_first import race_first

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = ROOT / "config" / "runtime.yaml"


def load_runtime() -> Dict[str, Any]:
    with RUNTIME_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _try_load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("'").strip('"')
            if k:
                os.environ[k] = v
    except Exception:
        pass


class ChatBackend(Protocol):
    model: str

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> str: ...


class LLMRequestError(RuntimeError):
    def __init__(
        self,
        *,
        category: str,
        message: str,
        hint: str = "",
        status_code: int | None = None,
        url: str | None = None,
        response_excerpt: str | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.category = category
        self.message = message
        self.hint = hint
        self.status_code = status_code
        self.url = url
        self.response_excerpt = response_excerpt
        self.retryable = retryable


def describe_llm_error(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, LLMRequestError):
        info = {
            "category": exc.category,
            "message": exc.message,
            "hint": exc.hint,
            "retryable": exc.retryable,
        }
        if exc.status_code is not None:
            info["status_code"] = exc.status_code
        if exc.url:
            info["url"] = exc.url
        if exc.response_excerpt:
            info["response_excerpt"] = exc.response_excerpt
        diagnostics = diagnostics_from_exception(exc)
        if diagnostics:
            info['diagnostics'] = diagnostics
        return info
    return {
        "category": "unknown_error",
        "message": str(exc),
        "hint": "检查网关连通性、模型配置和返回格式。",
        "retryable": False,
    }


class OpenAICompatClient:
    """Generic OpenAI-compatible chat client (official OpenAI or custom gateway)."""

    def __init__(
        self,
        api_base: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        race_n: int = 1,
        connect_timeout: float = 10.0,
        idle_timeout: float = 45.0,
        total_timeout: float = 180.0,
    ):
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout = timeout
        self.race_n = race_n
        self.stream = False
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.total_timeout = total_timeout
        self._disabled_error: LLMRequestError | None = None
        self._call_diagnostics = threading.local()

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> str:
        start = time.monotonic()
        self._call_diagnostics.winner = None
        try:
            text = self._chat_impl(messages, temperature, max_tokens)
            log_llm_call(
                client_class=self.__class__.__name__,
                model=self.model,
                api_base=self.api_base,
                messages=messages,
                raw_text=text,
                latency_ms=(time.monotonic() - start) * 1000,
                diagnostics=getattr(self._call_diagnostics, 'winner', None),
            )
            return text
        except LLMRequestError as exc:
            log_llm_call(
                client_class=self.__class__.__name__,
                model=self.model,
                api_base=self.api_base,
                messages=messages,
                error=describe_llm_error(exc),
                latency_ms=(time.monotonic() - start) * 1000,
            )
            raise
        except Exception as exc:
            log_llm_call(
                client_class=self.__class__.__name__,
                model=self.model,
                api_base=self.api_base,
                messages=messages,
                error={"category": "unknown_error", "message": str(exc)},
                latency_ms=(time.monotonic() - start) * 1000,
            )
            raise

    def _chat_impl(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> str:
        if self._disabled_error is not None:
            raise self._disabled_error
        call_id = uuid.uuid4().hex
        text, diagnostics = race_first(
            lambda: self._post_once(messages, temperature, max_tokens, call_id=call_id), self.race_n
        )
        self._call_diagnostics.winner = diagnostics
        return text

    def _post_once(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1200,
        *,
        call_id: str | None = None,
    ) -> tuple[str, dict]:
        url = f"{self.api_base}/chat/completions"
        if not self.api_key.strip():
            err = LLMRequestError(
                category="auth_missing",
                message=f"LLM 鉴权失败：未检测到 API Key，无法请求 {url}",
                hint="请设置 OPENAI_API_KEY，或切换到 rules / carsona 后端。",
                url=url,
                retryable=False,
            )
            self._disabled_error = err
            raise err
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.stream:
            payload['stream'] = True
        resp = None
        try:
            resp = traced_post(url, payload=payload, headers=headers, timeout=self.timeout,
                               race_n=self.race_n, secret=self.api_key, call_id=call_id,
                               connect_timeout=self.connect_timeout, idle_timeout=self.idle_timeout,
                               total_timeout=self.total_timeout)
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                error = LLMRequestError(
                    category="empty_response",
                    message="网关已完成请求，但模型没有返回正文。",
                    hint="本次没有可用输出；可精简输入后重新生成。",
                    status_code=resp.status_code,
                    url=url,
                    response_excerpt=(
                        f"finish_reason={choice.get('finish_reason')}; "
                        f"completion_tokens={usage.get('completion_tokens')}; "
                        f"message_fields={','.join(sorted(message))}"
                    ),
                    retryable=True,
                )
                error._llm_diagnostics = getattr(resp, "_llm_diagnostics", None)
                raise error
            return content, resp._llm_diagnostics
        except requests.Timeout as exc:
            details = diagnostics_from_exception(exc) or {}
            timeout_labels = {
                'connect_timeout': '连接网关超时',
                'request_write_timeout': '发送模型请求超时',
                'response_headers_timeout': '等待网关响应超时',
                'first_content_timeout': '等待模型首段内容超时',
                'stream_idle_timeout': '模型输出中断，等待后续内容超时',
                'response_body_timeout': '等待网关响应内容超时',
                'total_timeout': '已达到整次请求的时间上限',
            }
            raise LLMRequestError(
                category="timeout",
                message=timeout_labels.get(details.get('failure_kind'), '模型请求超时'),
                hint="本次请求已停止；可手动重新生成。请求日志保留了超时阶段和网关请求编号。",
                url=url,
                retryable=True,
            ) from exc
        except requests.HTTPError as exc:
            resp = exc.response
            status = resp.status_code if resp is not None else None
            body = ""
            if resp is not None:
                try:
                    body = (resp.text or "").strip()
                except Exception:
                    body = ""
            body = redact(body, self.api_key)[:240] if body else None
            if status == 401:
                err = LLMRequestError(
                    category="auth_invalid",
                    message=f"LLM 鉴权失败：网关返回 401 Unauthorized（{url}）",
                    hint="当前 OPENAI_API_KEY 可能无效、过期，或该聚光网关不接受这把 key。",
                    status_code=status,
                    url=url,
                    response_excerpt=body,
                    retryable=False,
                )
                self._disabled_error = err
                raise err from exc
            if status == 403:
                err = LLMRequestError(
                    category="permission_denied",
                    message=f"LLM 权限不足：网关返回 403 Forbidden（{url}）",
                    hint="当前 key 可能没有该模型或该网关的调用权限。",
                    status_code=status,
                    url=url,
                    response_excerpt=body,
                    retryable=False,
                )
                self._disabled_error = err
                raise err from exc
            if status == 404:
                err = LLMRequestError(
                    category="model_not_found",
                    message=f"LLM 模型不存在或路由错误：网关返回 404（{url}）",
                    hint="请检查 OPENAI_MODEL、OPENAI_BASE_URL 和网关侧模型映射。",
                    status_code=status,
                    url=url,
                    response_excerpt=body,
                    retryable=False,
                )
                self._disabled_error = err
                raise err from exc
            if status == 429:
                raise LLMRequestError(
                    category="rate_limited",
                    message=f"LLM 请求被限流：网关返回 429（{url}）",
                    hint="稍后重试，或降低并发。",
                    status_code=status,
                    url=url,
                    response_excerpt=body,
                    retryable=True,
                ) from exc
            if status in {504, 524}:
                raise LLMRequestError(
                    category="gateway_timeout",
                    message=f"网关等待上游模型响应超时（HTTP {status}）。",
                    hint="本次未获得完整结果，可稍后手动重新生成；继续延长本地等待时间无法解除网关自身的超时限制。",
                    status_code=status,
                    url=url,
                    response_excerpt=body,
                    retryable=True,
                ) from exc
            raise LLMRequestError(
                category="http_error",
                message=f"LLM 请求失败：网关返回 {status}（{url}）",
                hint="检查网关状态、模型参数和请求体。",
                status_code=status,
                url=url,
                response_excerpt=body,
                retryable=False,
            ) from exc
        except StreamResponseError as exc:
            upstream_error = exc.failure_kind == 'upstream_stream_error'
            raise LLMRequestError(
                category=('upstream_error' if upstream_error else
                          'output_truncated' if exc.failure_kind == 'output_truncated' else 'invalid_response'),
                message=str(exc),
                hint=('网关已返回错误事件，具体原因已记录；可稍后手动重新生成。' if upstream_error else
                      '本次输出不完整或格式无效，未作为结果展示；可手动重新生成。'),
                url=url, retryable=True,
            ) from exc
        except (requests.exceptions.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
            if resp is not None:
                exc._llm_diagnostics = getattr(resp, '_llm_diagnostics', None)
            raise LLMRequestError(
                category='invalid_response', message='网关已返回响应，但模型结果格式无效。',
                hint='检查该请求的响应状态和网关请求编号；这不是网络连接失败。',
                url=url, status_code=resp.status_code if resp is not None else None,
                retryable=True,
            ) from exc
        except requests.RequestException as exc:
            raise LLMRequestError(
                category="network_error",
                message=f"LLM 网络请求失败：{url}",
                hint="检查网络连通性、代理和网关可达性。",
                url=url,
                retryable=True,
            ) from exc


def resolve_backend_name(llm_backend: str | None = None, use_llm: bool | None = None) -> str:
    runtime = load_runtime()
    llm = runtime.get("llm") or {}
    if llm_backend:
        name = str(llm_backend).strip().lower()
    elif use_llm is False:
        name = "rules"
    elif use_llm is True:
        name = "carsona"
    else:
        name = str(llm.get("default_backend") or "rules").lower()
    if name in {"rule", "stub", "none", "false", "0"}:
        return "rules"
    if name in {"carsona", "local", "simpo"}:
        return "carsona"
    if name in {"openai", "gpt", "openai_compat"}:
        return "openai"
    return name


def get_llm(backend: str | None = None, use_llm: bool | None = None) -> Optional[ChatBackend]:
    """Return chat backend or None for pure rules."""
    _try_load_dotenv()
    name = resolve_backend_name(backend, use_llm)
    if name == "rules":
        return None

    runtime = load_runtime()
    llm = runtime.get("llm") or {}

    if name == "carsona":
        cfg = llm.get("carsona") or {}
        return LLMClient(
            api_base=cfg.get("base_url") or "http://127.0.0.1:7106/v1",
            model=cfg.get("model") or "carsona_simpo_4",
            timeout=float(cfg.get("timeout_sec") or 120),
            race_n=int(cfg.get("race_n") or 1),
        )

    if name == "openai":
        cfg = llm.get("openai") or {}
        from .local_cloud import load as load_local_cloud
        local=load_local_cloud()
        # Prefer direct values from runtime.yaml, then fall back to env indirection.
        base = (
            cfg.get("base_url")
            or os.environ.get(cfg.get("base_url_env") or "OPENAI_BASE_URL")
            or cfg.get("default_base_url")
            or "https://ai.juguang.chat/v1"
        )
        model = (
            cfg.get("model")
            or os.environ.get(cfg.get("model_env") or "OPENAI_MODEL")
            or cfg.get("default_model")
            or "gpt-4o"
        )
        key = (
            cfg.get("api_key")
            or os.environ.get(cfg.get("api_key_env") or "OPENAI_API_KEY")
            or ""
        )
        if local:
            base=local["base_url"];model=local["model"];key=local.get("api_key","")
        return OpenAICompatClient(
            api_base=base,
            model=model,
            api_key=key,
            timeout=float(cfg.get("timeout_sec") or 120),
            race_n=int(cfg.get("race_n") or 1),
            connect_timeout=float(cfg.get('connect_timeout_sec') or 10),
            idle_timeout=float(cfg.get('stream_idle_timeout_sec') or 45),
            total_timeout=float(cfg.get('total_timeout_sec') or 180),
        )

    raise ValueError(f"Unknown llm backend: {name}")


def model_version_label(backend: str | None, client: Optional[ChatBackend]) -> str:
    name = resolve_backend_name(backend)
    if name == "rules" or client is None:
        return "rules"
    return getattr(client, "model", name)


def chat_json(
    client: ChatBackend,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int = 1400,
) -> Dict[str, Any]:
    obj, _ = chat_json_with_raw(
        client,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return obj


def chat_json_with_raw(
    client: ChatBackend,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int = 1400,
) -> tuple[Dict[str, Any], str]:
    text = client.chat(messages, temperature=temperature, max_tokens=max_tokens)
    obj = extract_json_object(text)
    if not obj:
        raise ValueError(f"LLM did not return JSON object: {text[:240]}")
    return obj, text


def dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
