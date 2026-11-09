from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
BURDEN_PATH = ROOT / "config" / "burden.yaml"

_BURDEN_CFG: Dict[str, Any] | None = None

BURDEN_CN = {"low": "低", "medium": "中", "high": "高"}
PRIORITY_LEVELS = ("T1", "T2", "T3")


def load_burden_config() -> Dict[str, Any]:
    global _BURDEN_CFG
    if _BURDEN_CFG is None:
        with BURDEN_PATH.open("r", encoding="utf-8") as f:
            _BURDEN_CFG = yaml.safe_load(f) or {}
    return _BURDEN_CFG


def _match_keyword(name: str, keywords: Sequence[str]) -> bool:
    text = name or ""
    return any(k and k in text for k in keywords)


def infer_burden(
    *,
    capability: str,
    name: str = "",
    execution_mode: str = "",
    intensity: str = "",
    cap_meta: Mapping[str, Any] | None = None,
) -> str:
    """Return low|medium|high."""
    cfg = load_burden_config()
    domain_default = cfg.get("domain_default") or {}
    burden = str(domain_default.get(capability) or "medium")

    name = name or ""
    if _match_keyword(name, cfg.get("high_keywords") or []):
        burden = "high"
    elif _match_keyword(name, cfg.get("medium_keywords") or []):
        if burden == "low":
            burden = "medium"
    elif _match_keyword(name, cfg.get("low_keywords") or []):
        if burden == "high":
            pass
        else:
            burden = "low"

    # properties fallback bump
    props = (cap_meta or {}).get("properties") or {}
    try:
        if float(props.get("intrusiveness") or 0) >= 0.45 and burden == "low":
            burden = "medium"
        if float(props.get("risk") or 0) >= 0.18:
            burden = "high" if burden != "low" else "medium"
    except (TypeError, ValueError):
        pass

    bump = (cfg.get("execution_mode_bump") or {}).get(str(execution_mode) or "")
    rank = {"low": 0, "medium": 1, "high": 2}
    if bump and rank.get(bump, 0) > rank.get(burden, 0):
        burden = bump

    if intensity == "high" and burden == "low":
        burden = "medium"
    return burden if burden in rank else "medium"


def assign_priority(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tag each action with priority T1/T2/T3.

    Prefer decision_state; if missing/uniform, split by order thirds.
    """
    if not actions:
        return actions
    out = [dict(a) for a in actions]
    states = [str(a.get("decision_state") or "") for a in out]
    useful = [s for s in states if s in {"required", "conditional", "optional"}]
    if useful and len(set(useful)) >= 2:
        for a in out:
            ds = str(a.get("decision_state") or "optional")
            if ds == "required":
                a["priority"] = "T1"
            elif ds == "conditional":
                a["priority"] = "T2"
            else:
                a["priority"] = "T3"
        return out

    n = len(out)
    for i, a in enumerate(out):
        # order already 1..n
        if i < max(1, (n + 2) // 3):
            a["priority"] = "T1"
        elif i < max(2, (2 * n + 2) // 3):
            a["priority"] = "T2"
        else:
            a["priority"] = "T3"
    return out


def annotate_burden(
    actions: List[Dict[str, Any]],
    *,
    capability_lookup: Mapping[str, Mapping[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    out = []
    for a in actions:
        item = dict(a)
        cid = str(item.get("capability") or "")
        meta = (capability_lookup or {}).get(cid) or {}
        item["burden"] = infer_burden(
            capability=cid,
            name=str(item.get("name") or item.get("action") or ""),
            execution_mode=str(item.get("execution_mode") or ""),
            intensity=str(item.get("intensity") or ""),
            cap_meta=meta,
        )
        item["burden_label"] = BURDEN_CN.get(item["burden"], item["burden"])
        out.append(item)
    return out


def assign_waves(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Assign parallel wave index. Same-domain stays serial; high burden starts new wave."""
    if not actions:
        return actions
    ordered = sorted(actions, key=lambda x: int(x.get("order") or 99))
    waves: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_caps: set[str] = set()

    def flush() -> None:
        nonlocal current, current_caps
        if current:
            waves.append(current)
        current = []
        current_caps = set()

    for a in ordered:
        item = dict(a)
        cid = str(item.get("capability") or "")
        burden = str(item.get("burden") or "medium")
        can_join = bool(current) and burden != "high" and all(
            str(x.get("burden") or "") != "high" for x in current
        ) and cid not in current_caps

        if can_join:
            current.append(item)
            current_caps.add(cid)
        else:
            flush()
            current = [item]
            current_caps = {cid}
            if burden == "high":
                # high burden occupies its own wave
                flush()
    flush()

    out: List[Dict[str, Any]] = []
    for wi, wave in enumerate(waves, start=1):
        for item in wave:
            item["wave"] = wi
            out.append(item)
    # restore order field order
    out.sort(key=lambda x: int(x.get("order") or 99))
    return out


def enrich_actions_priority_burden(
    actions: List[Dict[str, Any]],
    *,
    capability_lookup: Mapping[str, Mapping[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    tagged = annotate_burden(actions, capability_lookup=capability_lookup)
    tagged = assign_priority(tagged)
    tagged = assign_waves(tagged)
    return tagged


def waves_summary(actions: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_wave: Dict[int, List[Mapping[str, Any]]] = {}
    for a in actions:
        w = int(a.get("wave") or 1)
        by_wave.setdefault(w, []).append(a)
    summary = []
    for w in sorted(by_wave):
        items = by_wave[w]
        summary.append(
            {
                "wave": w,
                "parallel": len(items) > 1,
                "actions": [
                    {
                        "order": a.get("order"),
                        "priority": a.get("priority"),
                        "burden": a.get("burden"),
                        "capability": a.get("capability"),
                        "display_name": a.get("display_name"),
                        "name": a.get("name") or a.get("action"),
                    }
                    for a in items
                ],
            }
        )
    return summary
