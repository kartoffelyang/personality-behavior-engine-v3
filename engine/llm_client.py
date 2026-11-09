from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional

import requests

from .llm_call_log import log_llm_call
from .race_first import race_first

DEFAULT_BASE = "http://127.0.0.1:7106/v1"
DEFAULT_MODEL = "carsona_simpo_4"


class LLMClient:
    def __init__(
        self,
        api_base: str = DEFAULT_BASE,
        model: str = DEFAULT_MODEL,
        timeout: float = 120.0,
        race_n: int = 1,
    ):
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.race_n = race_n

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> str:
        start = time.monotonic()
        try:
            text = self._chat_impl(messages, temperature, max_tokens)
            log_llm_call(
                client_class=self.__class__.__name__,
                model=self.model,
                api_base=self.api_base,
                messages=messages,
                raw_text=text,
                latency_ms=(time.monotonic() - start) * 1000,
            )
            return text
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
        max_tokens: int = 800,
    ) -> str:
        return race_first(
            lambda: self._post_once(messages, temperature, max_tokens), self.race_n
        )

    def _post_once(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> str:
        url = f"{self.api_base}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    # strip markdown fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # find first {...}
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            # Some gateways append redundant closing braces to an otherwise
            # complete object. Decode that complete object, never guess fields
            # or accept a second object / a truncated response.
            try:
                obj, consumed = json.JSONDecoder().raw_decode(text[start:])
                tail = text[start + consumed:].strip()
                if isinstance(obj, dict) and re.fullmatch(r"[}\s`]*", tail):
                    return obj
            except json.JSONDecodeError:
                pass
            return None
    return None
