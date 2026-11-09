from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def weighted_dot(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    num = sum(float(a[k]) * float(b[k]) for k in keys)
    den = sum(float(a[k]) for k in keys) or 1.0
    return clamp01(num / den)


def top_functions(scores: Mapping[str, float], k: int = 2) -> list[tuple[str, float]]:
    items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return items[:k]


def normalize_needs(needs: Mapping[str, float]) -> Dict[str, float]:
    return {str(k): clamp01(float(v)) for k, v in needs.items()}


def primary_need(needs: Mapping[str, float]) -> str | None:
    if not needs:
        return None
    return max(needs.items(), key=lambda x: x[1])[0]


def secondary_need(needs: Mapping[str, float]) -> str | None:
    if len(needs) < 2:
        return None
    ordered = sorted(needs.items(), key=lambda x: x[1], reverse=True)
    return ordered[1][0]


def as_float_map(d: Any) -> Dict[str, float]:
    if not isinstance(d, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in d.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out
