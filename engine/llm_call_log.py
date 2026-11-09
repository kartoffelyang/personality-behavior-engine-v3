"""Append-only JSONL log of every LLM call (input + output).

One JSON object per line. Each line records the full request messages
(input) and the raw model reply (output), plus latency / error info.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "output" / "llm_calls"
REQUEST_LOG_DIR = ROOT / "output" / "llm_requests"

_lock = threading.Lock()


def _log_path() -> Path:
    date = datetime.now().strftime("%Y%m%d")
    return LOG_DIR / f"llm_calls_{date}.jsonl"


def log_llm_call(
    *,
    client_class: str,
    model: str,
    api_base: str,
    messages: List[Dict[str, Any]],
    raw_text: Optional[str] = None,
    error: Optional[Dict[str, Any]] = None,
    latency_ms: Optional[float] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> None:
    record: Dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "client": client_class,
        "model": model,
        "api_base": api_base,
        "status": "error" if error else "ok",
        "latency_ms": latency_ms,
        "messages": messages,
        "raw_text": raw_text,
    }
    if error:
        record["error"] = error
    if diagnostics:
        record['diagnostics'] = diagnostics
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _lock:
            with _log_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Logging must never break the LLM call flow.
        pass


def log_llm_request(record: Dict[str, Any]) -> None:
    """One transport record per wire request, including every racing copy."""
    try:
        REQUEST_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = REQUEST_LOG_DIR / f'llm_requests_{datetime.now():%Y%m%d}.jsonl'
        with _lock:
            with path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass
