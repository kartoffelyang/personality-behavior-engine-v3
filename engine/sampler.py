from __future__ import annotations

import hashlib
import math
import random
from datetime import date
from typing import Any, Dict, List, Mapping, Sequence


def make_seed(request_id: str, persona: str, scenario_id: str, date_bucket: str | None = None) -> int:
    bucket = date_bucket or date.today().isoformat()
    raw = f"{request_id}|{persona}|{scenario_id}|{bucket}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) % (2**31 - 1)


def softmax(scores: Sequence[float], temperature: float) -> List[float]:
    t = max(float(temperature), 1e-6)
    scaled = [s / t for s in scores]
    m = max(scaled)
    exps = [math.exp(s - m) for s in scaled]
    z = sum(exps) or 1.0
    return [e / z for e in exps]


def select(
    candidates: List[Dict[str, Any]],
    persona_id: str,
    request_id: str,
    scenario_id: str,
    randomness_cfg: Mapping[str, Any],
    max_tools: int,
    min_gap: float,
    safety_forced: bool = False,
    explicit_forced: bool = False,
) -> Dict[str, Any]:
    """Select capabilities with optional reproducible near-tie sampling."""
    if not candidates:
        return {
            "selected": [],
            "random_selection": None,
            "rule_notes": ["无候选达到调用阈值，输出 no_tool"],
        }

    if safety_forced or explicit_forced or not randomness_cfg.get("enabled", True):
        selected = candidates[:max_tools]
        # multi-call gap filter
        if len(selected) > 1:
            top = selected[0]["final_score"]
            selected = [selected[0]] + [
                c for c in selected[1:] if top - c["final_score"] <= min_gap
            ]
            selected = selected[:max_tools]
        return {
            "selected": selected,
            "random_selection": None,
            "rule_notes": [
                "安全或显式路径，跳过随机采样" if (safety_forced or explicit_forced) else "随机关闭，确定性取 Top"
            ],
        }

    top_score = candidates[0]["final_score"]
    window = float(randomness_cfg.get("score_window", 0.10))
    top_k = int(randomness_cfg.get("top_k", 2))
    pool = [c for c in candidates if top_score - c["final_score"] <= window][:top_k]
    temps = randomness_cfg.get("temperature") or {}
    temperature = float(temps.get(persona_id, 0.12))

    seed = make_seed(request_id, persona_id, scenario_id)
    rng = random.Random(seed)

    if len(pool) <= 1:
        primary = candidates[0]
        extras = []
        for c in candidates[1:max_tools]:
            if top_score - c["final_score"] <= min_gap:
                extras.append(c)
        selected = [primary] + extras[: max_tools - 1]
        return {
            "selected": selected,
            "random_selection": {
                "seed": seed,
                "sample_pool": [{"tool": primary["capability"], "probability": 1.0}],
                "sampled_tool": primary["capability"],
                "note": "近分候选不足，确定性选择",
            },
            "rule_notes": ["近分候选池不足，确定性选择最高分"],
        }

    probs = softmax([c["final_score"] for c in pool], temperature)
    sampled = rng.choices(pool, weights=probs, k=1)[0]
    # remaining slots by score among non-sampled high scorers
    rest = [c for c in candidates if c["capability"] != sampled["capability"]]
    extras = []
    for c in rest:
        if sampled["final_score"] - c["final_score"] <= min_gap or top_score - c["final_score"] <= min_gap:
            extras.append(c)
        if len(extras) >= max_tools - 1:
            break
    selected = [sampled] + extras[: max_tools - 1]

    return {
        "selected": selected,
        "random_selection": {
            "seed": seed,
            "sample_pool": [
                {"tool": c["capability"], "probability": round(p, 4)}
                for c, p in zip(pool, probs)
            ],
            "sampled_tool": sampled["capability"],
        },
        "rule_notes": [
            f"{', '.join(c['capability'] for c in pool)} 进入随机候选池",
        ],
    }
