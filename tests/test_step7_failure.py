from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine.pipeline as pipeline
from engine.llm_router import LLMRequestError
from engine.model_planner import run_planner
from web import flow_review_app


class TimeoutClient:
    model = "timeout-fixture"

    def chat(self, messages, **kwargs):
        raise LLMRequestError(
            category="timeout",
            message="LLM 请求超时",
            hint="稍后重试",
            retryable=True,
        )


def test_planner_timeout_does_not_substitute_blueprint_actions():
    result = run_planner(
        llm_backend="openai",
        client=TimeoutClient(),
        persona_id="INFP",
        scenario_id="time_pressure",
        parsed={},
        whitelist=["navigation"],
        rule_summary=[],
        flow_context={"capability_catalog": []},
        blueprint={
            "capability_hints": [
                {"capability": "navigation", "constraint": "must", "name": "规划效率路线"}
            ]
        },
    )

    assert result["fallback"] is True
    assert result["error_info"]["category"] == "timeout"
    assert result["output"]["source"] == "llm_error"
    assert result["output"]["behavior_actions"] == []
    assert "no_call" not in result["output"]


def test_pipeline_marks_timeout_as_failure_and_keeps_final_sequence_empty(monkeypatch):
    original_run_intent = pipeline.run_intent

    class Model:
        model = "timeout-fixture"

    monkeypatch.setattr(pipeline, "get_llm", lambda backend: Model())
    monkeypatch.setattr(
        pipeline,
        "run_intent",
        lambda req, backend, client, scenarios, prompt_override=None: original_run_intent(
            req, "rules", None, scenarios, prompt_override=prompt_override
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_planner",
        lambda **kwargs: {
            "model_version": "timeout-fixture",
            "fallback": True,
            "error_info": {
                "category": "timeout",
                "message": "LLM 请求超时",
                "hint": "稍后重试",
                "retryable": True,
            },
            "input": {},
            "output": {
                "behavior_actions": [],
                "source": "llm_error",
                "notes": ["planner_fallback:timeout"],
                "llm_error": {"category": "timeout", "message": "LLM 请求超时"},
            },
        },
    )

    result = pipeline.generate_behavior_plan(
        pipeline.default_request_scaffold(
            "INFP",
            "今天十点前要去康源大厦见客户。",
            "step7_timeout",
        ),
        engine=pipeline.Engine(use_llm=False, llm_backend="rules"),
        llm_backend="openai",
    )

    assert result["generation_status"] == "failed"
    assert result["generation_error"]["category"] == "timeout"
    assert result["behavior_call_type"] is None
    assert result["selected_capabilities"] == []
    assert result["behavior_sequence"] == []
    assert result["O_Action_Plan"] == []
    assert result["O_Behavior"] == {}
    assert result["scene_plan"] == ""
    assert result["action_plan"] == "生成失败/超时"
    assert result["final"]["behavior_sequence"] == []
    step7 = result["audit_steps"][-1]
    assert step7["status"] == "fallback"
    assert "生成失败/超时" in step7["output_text"]
    assert "不使用蓝图结果代替" in step7["output_text"]


def test_flow_review_displays_timeout_instead_of_a_valid_plan():
    session_id = "timeout_ui"
    result = {
        "scenario_id": "time_pressure",
        "llm_backend": "openai",
        "model_version": "timeout-fixture",
        "generation_status": "failed",
        "generation_error": {"category": "timeout", "message": "LLM 请求超时"},
        "O_Action_Plan": [],
        "O_Behavior": {},
        "selected_capabilities": [],
        "final": {
            "generation_status": "failed",
            "generation_error": {"category": "timeout", "message": "LLM 请求超时"},
            "O_Action_Plan": [],
            "O_Behavior": {},
            "selected_capabilities": [],
        },
        "audit_steps": [
            {
                "step": 7,
                "title": "行为序列生成与输出（功能优先级＋调用负担）",
                "status": "fallback",
                "model_version": "timeout-fixture",
                "input": {},
                "output": {
                    "generation_status": "failed",
                    "generation_error": {"category": "timeout", "message": "LLM 请求超时"},
                    "planner_output": {
                        "behavior_actions": [],
                        "llm_error": {"category": "timeout", "message": "LLM 请求超时"},
                    },
                },
                "input_text": "生成行为序列。",
                "output_text": "生成失败/超时：LLM 请求超时",
                "rules_hit": ["planner_fallback", "timeout"],
            }
        ],
    }
    flow_review_app._session_store[session_id] = {
        "session_id": session_id,
        "persona": "INFP",
        "backend": "openai",
        "request": {"request_id": "timeout_ui", "user_utterance": "十点前去康源大厦"},
        "result": result,
        "confirmed_step": 6,
        "prompt_overrides": {},
        "saved_path": "",
    }
    try:
        response = flow_review_app.app.test_client().get(f"/session/{session_id}/step/7")
        text = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "生成失败/超时" in text
        assert "本次没有生成行为序列，也没有使用蓝图结果代替" in text
        assert "没有可展示的有效行为序列" in text
        assert "确认本步，完成审阅" not in text
    finally:
        flow_review_app._session_store.pop(session_id, None)
