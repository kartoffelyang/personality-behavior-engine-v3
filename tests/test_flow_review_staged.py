from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import default_request_scaffold, generate_behavior_plan, rerun_from_review_step
from web import flow_review_app


UTTERANCE = "今天早上十点前去康源大厦见客户，我第一次去。"


def test_pipeline_can_defer_step7_and_resume_from_saved_state():
    partial = generate_behavior_plan(
        default_request_scaffold("ESTJ", UTTERANCE, "staged_pipeline"),
        llm_backend="rules",
        defer_step7=True,
    )
    assert partial["step7_deferred"] is True
    assert [step["step"] for step in partial["audit_steps"]] == list(range(1, 8))
    assert partial["audit_steps"][-1]["status"] == "pending"
    assert partial["audit_steps"][-1]["output"] == {}
    assert "execution_modes" not in partial["audit_steps"][-1]["input"]["flow_context"]
    assert "summary" not in partial["audit_steps"][-1]["input"]["flow_context"]["scenario_understanding"]
    assert "evidence" not in partial["audit_steps"][-1]["input"]["flow_context"]["scenario_understanding"]
    assert partial["O_Action_Plan"] == []

    completed = rerun_from_review_step(partial, step_no=7, llm_backend="rules")
    assert not completed.get("step7_deferred")
    assert completed["audit_steps"][-1]["step"] == 7
    assert completed["audit_steps"][-1]["status"] != "pending"
    assert completed["audit_steps"][-1]["output"].get("planner_output") is not None
    assert all("执行方式：" in line for line in completed["O_Action_Plan"])


def test_flow_review_shows_step2_before_step7_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(flow_review_app, "OUT_DIR", tmp_path)
    flow_review_app._session_store.clear()
    client = flow_review_app.app.test_client()

    start = client.post(
        "/start",
        data={"persona": "ESTJ", "llm_backend": "rules", "user_utterance": UTTERANCE},
    )
    assert start.status_code == 302
    assert start.headers["Location"].endswith("/step/2")
    session_id = start.headers["Location"].split("/")[2]
    session = flow_review_app._session_store[session_id]
    assert session["confirmed_step"] == 1
    assert session["result"]["step7_deferred"] is True
    assert session["result"]["audit_steps"][-1]["status"] == "pending"

    step2 = client.get(start.headers["Location"])
    assert step2.status_code == 200
    assert "MODEL 1" in step2.get_data(as_text=True)
    assert "time_pressure" in step2.get_data(as_text=True)

    for step_no in range(2, 7):
        response = client.post(f"/session/{session_id}/step/{step_no}/confirm")
        assert response.status_code == 302

    pending = client.get(f"/session/{session_id}/step/7")
    pending_text = pending.get_data(as_text=True)
    assert pending.status_code == 200
    assert "生成 Step 7 行为序列" in pending_text
    assert "点击后才会调用行为序列模型" in pending_text

    generated = client.post(f"/session/{session_id}/step/7/generate")
    assert generated.status_code == 302
    completed = flow_review_app._session_store[session_id]["result"]
    assert not completed.get("step7_deferred")
    assert completed["audit_steps"][-1]["status"] != "pending"
    assert completed["audit_steps"][-1]["output"].get("planner_output") is not None

    step7 = client.get(generated.headers["Location"])
    step7_text = step7.get_data(as_text=True)
    assert "生成 Step 7 行为序列" not in step7_text
    assert "确认行为序列，进入 Step 8" in step7_text
