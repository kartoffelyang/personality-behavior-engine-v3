from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import default_request_scaffold, generate_behavior_plan
from web import flow_review_app as review


def test_steps_2_7_8_expose_response_time_labels(monkeypatch, tmp_path):
    result = generate_behavior_plan(
        default_request_scaffold("INFP", "今天十点前去康源大厦。", "timing_test"),
        llm_backend="rules",
    )
    steps = {step["step"]: step for step in result["audit_steps"]}
    assert isinstance(steps[2].get("elapsed_seconds"), (int, float))
    assert isinstance(steps[7].get("elapsed_seconds"), (int, float))

    session = {
        "persona": "INFP",
        "backend": "rules",
        "confirmed_step": 7,
        "request": {"persona": "INFP", "user_utterance": "今天十点前去康源大厦。"},
        "result": result,
        "story_step": {
            "step": 8,
            "title": "场景故事与核心改变点",
            "status": "ok",
            "input": {},
            "output": {
                "title": "行程有序推进",
                "paragraphs": [{"segments": [{
                    "text": "助手搜索目的地并准备候选路线。",
                    "change_id": None,
                    "action_ids": [1],
                }]}],
                "core_changes": [],
            },
            "elapsed_seconds": 1.236,
        },
    }
    monkeypatch.setattr(review, "OUT_DIR", tmp_path)
    monkeypatch.setattr(review, "_session_store", {"timed": session})
    client = review.app.test_client()

    for step_no in (2, 7):
        page = client.get(f"/session/timed/step/{step_no}").get_data(as_text=True)
        assert "响应时间：" in page
        assert "秒" in page
    story_page = client.get("/session/timed/step/8").get_data(as_text=True)
    assert "响应时间：1.24 秒" in story_page


def test_old_step_without_timing_displays_unrecorded(monkeypatch, tmp_path):
    result = generate_behavior_plan(
        default_request_scaffold("INFP", "今天十点前去康源大厦。", "old_timing_test"),
        llm_backend="rules",
    )
    next(step for step in result["audit_steps"] if step["step"] == 2).pop("elapsed_seconds", None)
    session = {
        "persona": "INFP", "backend": "rules", "confirmed_step": 2,
        "request": {"persona": "INFP", "user_utterance": "今天十点前去康源大厦。"},
        "result": result,
    }
    monkeypatch.setattr(review, "OUT_DIR", tmp_path)
    monkeypatch.setattr(review, "_session_store", {"old": session})
    page = review.app.test_client().get("/session/old/step/2").get_data(as_text=True)
    assert "响应时间：未记录" in page
