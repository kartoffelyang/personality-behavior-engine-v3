from __future__ import annotations

from typing import Any, Mapping


def build_persona_profile(persona_id: str, persona: Mapping[str, Any]) -> dict:
    """Compact Step 3 description; no scores, action sequences or other types."""
    if str(persona_id).upper() == "NO_MBTI":
        return {"persona": persona_id, "mode": "scenario_only", "dominant": None,
                "persona_description": "不使用人格描述或主导功能判断倾向；依据实际情境生成行为。"}
    return {"persona": persona_id, **dict(persona["step3_profile"])}
