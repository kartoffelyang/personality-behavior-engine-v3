"""The real eight-step pipeline runs with deterministic model transport doubles."""
from __future__ import annotations

import json
import sys
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from flask import Flask
from jinja2 import DictLoader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import model_intent, model_story, pipeline
from engine.llm_router import LLMRequestError
from web import auto_run as flow
from planner_fixtures import navigation_plan


class QueuedExecutor:
    def __init__(self):
        self.pending = []

    def submit(self, function, *args):
        self.pending.append((function, args))

    def drain(self):
        while self.pending:
            function, args = self.pending.pop(0)
            function(*args)


class Model:
    model = "auto-run-test"
    race_n = 3
    stream = False

    def __init__(self):
        self.calls = {2: 0, 7: 0, 8: 0}
        self.failures = {2: [], 7: [], 8: []}
        self.plan_status = "ready"
        self.plans = []
        self.messages = {2: [], 7: [], 8: []}
        self.no_actions = False

    def chat(self, messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        stage = 2 if "allowed_scenarios" in payload else 8 if "behavior_sequence" in payload else 7
        self.calls[stage] += 1
        self.messages[stage].append(deepcopy(messages))
        assert self.race_n == 1
        if self.failures[stage]:
            error = self.failures[stage].pop(0)
            if error:
                if error == "bad_json":
                    return "{broken"
                raise LLMRequestError(category=error, message="测试请求失败", retryable=error in {"gateway_timeout", "timeout"})
        if stage == 2:
            prompt = model_intent.INTENT_SYSTEM
            raw = json.loads(prompt[prompt.index("{"):prompt.rindex("}") + 1])
            raw["scenario_id"] = "welcome_on_board"
            raw["task"].update(core_goal="前往康源大厦", explicit_intents=["前往康源大厦"], optional_goals=[], constraints=[], must_do=[])
            raw["facts"] = [{"category": "task", "source": "utterance", "fact": "前往康源大厦", "confidence": 0.95}]
            raw["context"].update(destination_reference="康源大厦")
            raw["activated_interaction_dimensions"] = []
            return json.dumps(raw, ensure_ascii=False)
        if stage == 7:
            if self.plans:
                return json.dumps(self.plans.pop(0), ensure_ascii=False)
            plan = navigation_plan()
            plan["plan_status"] = self.plan_status
            if self.no_actions:
                plan["behavior_actions"] = []
            if self.plan_status == "needs_clarification":
                plan["clarification_questions"] = ["请确认康源大厦的具体地点。"]
            return json.dumps(plan, ensure_ascii=False)
        segments = []
        for assumption in payload.get("scenario_assumptions", []):
            segments.append({"text": "按演示假设，" + assumption, "action_ids": [], "change_id": None})
        for action in payload["behavior_sequence"]:
            guard = "经用户确认后，" if action.get("execution_mode") == "confirm_execute" else ""
            segments.append({"text": guard + "助手准备" + action["name"] + "。", "action_ids": [action["id"]], "change_id": None})
        return json.dumps({"paragraphs": [{"segments": segments}], "core_changes": []}, ensure_ascii=False)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    executor, model = QueuedExecutor(), Model()
    monkeypatch.setattr(flow, "OUT_DIR", tmp_path)
    monkeypatch.setattr(flow, "_jobs", {})
    monkeypatch.setattr(flow, "_active", set())
    monkeypatch.setattr(flow, "_executor", executor)
    monkeypatch.setattr(flow, "RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(flow, "get_llm", lambda _: model)
    monkeypatch.setattr(pipeline, "get_llm", lambda _: model)
    monkeypatch.setattr(model_story, "get_llm", lambda _: model)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.jinja_loader = DictLoader({"auto_run.html": "{{ initial_job_id or 'new' }}",
                                  "auto_compare.html": "compare {{ initial_job_id or 'new' }}",
                                  "auto_run_graph.html": "{{ persona }} {{ final_snapshot|tojson }}",
                                  "auto_run_result.html": "{{ final_snapshot|tojson }} {{ story|tojson }}"})
    app.register_blueprint(flow.bp)
    return app.test_client(), executor, model


def submit(client, **overrides):
    body = {"persona": "INFP", "llm_backend": "openai", "user_utterance": "上车去康源大厦见客户。", **overrides}
    response = client.post("/api/auto-run", json=body)
    assert response.status_code == 202
    return response.json["job"]["id"]


def read(client, job_id):
    response = client.get("/api/auto-run/" + job_id)
    assert response.status_code == 200
    return response.json["job"]


def test_runs_all_eight_steps_and_only_one_model_request_per_stage(setup):
    client, executor, model = setup
    job_id = submit(client)
    assert read(client, job_id)["status"] == "queued"
    assert model.calls == {2: 0, 7: 0, 8: 0}
    executor.drain()
    job = read(client, job_id)
    assert job["status"] == "completed", job
    assert model.calls == {2: 1, 7: 1, 8: 1}
    assert [s["status"] for s in job["steps"]] == ["completed"] * 8
    assert len(job["result"]["audit_steps"]) == 8
    assert all(s.get("input_text") and s.get("output_text") for s in job["result"]["audit_steps"])
    assert job["result"]["validated_actions"]
    assert job["result"]["story_display"]["paragraphs"]
    assert job["llm_backend"] == "openai"
    assert client.get("/auto_run/" + job_id + "/result").status_code == 200
    serialized = json.dumps(job)
    assert "prompt_used" not in serialized and "system_prompt" not in serialized and "_review_internal" not in serialized


@pytest.mark.parametrize("stage", [2, 7, 8])
def test_transient_stage_failure_retries_without_repeating_successes(setup, stage):
    client, executor, model = setup
    model.failures[stage] = ["gateway_timeout"]
    job_id = submit(client)
    executor.drain()
    job = read(client, job_id)
    assert job["status"] == "completed", job
    assert model.calls == {s: (2 if s == stage else 1) for s in [2, 7, 8]}
    assert job["steps"][stage - 1]["attempt"] == 2
    assert any(a["step"] == stage and a["error"]["category"] == "gateway_timeout" for a in job["attempts"] if a["error"])


@pytest.mark.parametrize("stage", [2, 7, 8])
def test_exhausted_retries_resume_only_failed_stage_including_after_restart(setup, stage):
    client, executor, model = setup
    model.failures[stage] = ["timeout"] * 3
    job_id = submit(client)
    executor.drain()
    job = read(client, job_id)
    assert job["status"] == "failed", job
    assert job["current_step"] == stage
    assert model.calls[stage] == 3
    assert all(model.calls[s] == 0 for s in [2, 7, 8] if s > stage)
    # Simulate a fresh process reading the atomically saved checkpoint.
    flow._jobs.clear()
    counts = dict(model.calls)
    assert read(client, job_id)["status"] == "failed"
    assert model.calls == counts
    response = client.post("/api/auto-run/" + job_id + "/retry")
    assert response.status_code == 202
    executor.drain()
    assert read(client, job_id)["status"] == "completed"
    assert model.calls == {s: (4 if s == stage else 1) for s in [2, 7, 8]}
    assert not list(flow.OUT_DIR.glob("*.tmp"))


@pytest.mark.parametrize("stage", [2, 7, 8])
@pytest.mark.parametrize("category", ["auth_invalid", "auth_missing", "permission_denied", "model_not_found"])
def test_non_retryable_errors_stop_immediately(setup, stage, category):
    client, executor, model = setup
    model.failures[stage] = [category]
    job_id = submit(client)
    executor.drain()
    job = read(client, job_id)
    assert job["status"] == "failed"
    assert job["error"]["category"] == category
    assert job["error"]["retryable"] is False
    assert model.calls[stage] == 1


def test_step2_bad_format_cannot_fall_back_to_rules(setup):
    client, executor, model = setup
    model.failures[2] = ["bad_json"] * 3
    job_id = submit(client)
    executor.drain()
    assert read(client, job_id)["status"] == "failed"
    assert model.calls == {2: 3, 7: 0, 8: 0}
    assert "_intent" not in flow._jobs[job_id]


def test_duplicate_submissions_and_retry_requests_do_not_queue_duplicate_work(setup):
    client, executor, model = setup
    model.failures[7] = ["timeout"] * 3
    first = submit(client)
    assert submit(client) == first
    assert len(executor.pending) == 1
    executor.drain()
    assert read(client, first)["status"] == "failed"
    assert client.post("/api/auto-run/" + first + "/retry").status_code == 202
    assert client.post("/api/auto-run/" + first + "/retry").status_code == 202
    assert len(executor.pending) == 1
    executor.drain()
    assert model.calls == {2: 1, 7: 4, 8: 1}
    assert client.post("/api/auto-run/" + first + "/retry").status_code == 409


def test_every_get_is_read_only_and_restart_marks_unfinished_work(setup):
    client, executor, model = setup
    job_id = submit(client)
    assert client.get("/auto_run").status_code == 200
    assert client.get("/auto_run/" + job_id).status_code == 200
    assert client.get("/auto_run/" + job_id + "/result").status_code == 409
    assert read(client, job_id)["status"] == "queued"
    flow._jobs.clear()
    flow._active.clear()
    executor.pending.clear()
    job = read(client, job_id)
    assert job["status"] == "failed" and job["error"]["category"] == "interrupted"
    assert model.calls == {2: 0, 7: 0, 8: 0}
    assert client.post("/api/auto-run/" + job_id + "/retry").status_code == 202
    executor.drain()
    assert read(client, job_id)["status"] == "completed"


@pytest.mark.parametrize("body", [None, [], "text", {}, {"persona": []},
                                  {"persona": "OTHER", "user_utterance": "上车"},
                                  {"persona": "INFP", "user_utterance": " "},
                                  {"persona": "INFP", "user_utterance": 1},
                                  {"persona": "INFP", "user_utterance": "上车", "llm_backend": []},
                                  {"persona": "INFP", "user_utterance": "上车", "llm_backend": "rules"}])
def test_invalid_input_is_rejected_without_model_calls(setup, body):
    client, executor, model = setup
    assert client.post("/api/auto-run", json=body).status_code == 400
    assert not executor.pending and not any(model.calls.values())


def test_missing_invalid_and_corrupt_jobs_are_404(setup):
    client, _, _ = setup
    for job_id in ["invalid", "f" * 32]:
        assert client.get("/api/auto-run/" + job_id).status_code == 404
        assert client.get("/auto_run/" + job_id).status_code == 404
        assert client.post("/api/auto-run/" + job_id + "/retry").status_code == 404
    flow.OUT_DIR.mkdir(exist_ok=True)
    (flow.OUT_DIR / ("b" * 32 + ".json")).write_text("[]", encoding="utf-8")
    assert client.get("/api/auto-run/" + "b" * 32).status_code == 404


def test_demo_clarification_is_corrected_and_assumptions_reach_story_without_becoming_facts(setup):
    client, executor, model = setup
    clarification = navigation_plan()
    clarification.update(plan_status="needs_clarification", behavior_actions=[],
                         clarification_questions=["请确认康源大厦所在城市。"])
    assumption = "本次康源大厦按用户所在城市的同名地点进行演示搜索。"
    complete = navigation_plan()
    complete["notes"] = ["演示假设：" + assumption, "实际目的地信息通过地点搜索获取。"]
    model.plans = [clarification, complete]
    job_id = submit(client)
    assert flow._jobs[job_id]["_request"]["generation_mode"] == "demo"
    executor.drain()
    job = read(client, job_id)
    assert job["status"] == "completed", job
    assert model.calls == {2: 1, 7: 2, 8: 1}
    assert job["result"]["clarification_questions"] == []
    assert job["result"]["scenario_assumptions"] == [assumption]
    story_text = "".join(segment["text"] for paragraph in job["result"]["story_display"]["paragraphs"]
                         for segment in paragraph["segments"])
    assert assumption in story_text
    story_input = json.loads(model.messages[8][0][1]["content"])
    assert story_input["generation_mode"] == "demo"
    assert story_input["scenario_assumptions"] == [assumption]
    first_call, corrected_call = model.messages[7]
    assert len(first_call) == 2 and len(corrected_call) == 4
    feedback = json.loads(corrected_call[-1]["content"])
    assert "演示" in feedback["instruction"] and "假设" in feedback["instruction"]
    assert "缺信息且无法查询时使用needs_clarification" not in feedback["instruction"]
    assert "needs_clarification" in feedback["validation_feedback"]
    stored = flow._jobs[job_id]
    generated_flow = flow._step_record(stored["_prepared"])["input"]["flow_context"]
    resumed_flow = json.loads(first_call[1]["content"])["flow_context"]
    for current in (generated_flow, resumed_flow):
        assert current["generation_mode"] == "demo"
        assert assumption not in json.dumps(current.get("scenario_understanding"), ensure_ascii=False)
        assert assumption not in json.dumps(current.get("execution_context"), ensure_ascii=False)


def test_demo_persistent_clarification_retries_instead_of_asking_the_user(setup):
    client, executor, model = setup
    model.plan_status = "needs_clarification"
    job_id = submit(client)
    executor.drain()
    job = read(client, job_id)
    assert job["status"] == "failed", job
    assert job["error"]["category"] == "planner_validation"
    assert model.calls == {2: 1, 7: 6, 8: 0}
    assert job["steps"][6]["attempt"] == 3


@pytest.mark.parametrize("terminal", ["needs_clarification", "no_tool_needed"])
def test_legitimate_terminal_without_actions_skips_story(setup, monkeypatch, terminal):
    client, executor, model = setup
    # This boundary fixture isolates worker handling of legitimate planner
    # outcomes from the navigation-specific model fixture's task constraints.
    original = flow.rerun_from_review_step

    def terminal_plan(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan["generation_status"] = terminal
        plan["final"]["generation_status"] = terminal
        output = flow._step_record(plan)["output"]
        output.update(validated_actions=[], generation_status=terminal, plan_status=terminal,
                      clarification_questions=["请补充地点。"] if terminal == "needs_clarification" else [])
        return plan

    monkeypatch.setattr(flow, "rerun_from_review_step", terminal_plan)
    job_id = submit(client)
    executor.drain()
    job = read(client, job_id)
    assert job["status"] == terminal
    assert job["steps"][7]["status"] == "skipped"
    assert len(job["result"]["audit_steps"]) == 8
    assert model.calls[8] == 0
    assert client.get("/auto_run/" + job_id + "/result").status_code == 200


def test_unexpected_programming_error_does_not_repeat_requests(setup, monkeypatch):
    client, executor, model = setup

    def broken(*args, **kwargs):
        raise KeyError("programming bug")

    monkeypatch.setattr(flow, "run_intent", broken)
    job_id = submit(client)
    executor.drain()
    job = read(client, job_id)
    assert job["status"] == "failed"
    assert job["error"]["category"] == "internal_error"
    assert job["steps"][1]["attempt"] == 1


@pytest.mark.parametrize("url", [
    "https://user:secret@gateway.test/v1?token=example-secret&access_token=other-secret",
    "https://user:secret@gateway.test/v1?token=example-secret#other-secret",
    "https://user:secret@gateway.test:invalid/v1?token=example-secret&access_token=other-secret",
])
def test_error_and_status_message_hide_url_credentials_without_changing_saved_diagnostics(setup, monkeypatch, url):
    client, executor, _ = setup
    raw_message = "LLM request failed: " + url

    def denied(*args, **kwargs):
        raise LLMRequestError(category="auth_invalid", message=raw_message, hint=raw_message)

    monkeypatch.setattr(flow, "run_intent", denied)
    job_id = submit(client)
    executor.drain()
    # Older persisted jobs can have raw errors embedded in their status message.
    with flow._lock:
        job = flow._jobs[job_id]
        job["message"] = raw_message
        flow._save(job)
    public = read(client, job_id)
    for surface in [public["message"], public["error"]["message"], public["error"]["hint"],
                    public["attempts"][0]["error"]["message"]]:
        assert "secret" not in surface
        assert "user:" not in surface
        assert "token=" not in surface
    saved = json.loads((flow.OUT_DIR / (job_id + ".json")).read_text(encoding="utf-8"))
    assert saved["message"] == raw_message
    assert saved["error"]["message"] == raw_message
    assert saved["_attempts"][0]["error"]["message"] == raw_message


def test_at_most_two_jobs_execute_in_parallel(setup, monkeypatch):
    client, _, _ = setup
    entered = threading.Event()
    release = threading.Event()
    counting_lock = threading.Lock()
    active = maximum = calls = 0
    original = flow.run_intent

    def held(*args, **kwargs):
        nonlocal active, maximum, calls
        with counting_lock:
            active += 1
            calls += 1
            maximum = max(maximum, active)
            if active == 2:
                entered.set()
        release.wait(timeout=5)
        try:
            return original(*args, **kwargs)
        finally:
            with counting_lock:
                active -= 1

    monkeypatch.setattr(flow, "run_intent", held)
    with ThreadPoolExecutor(max_workers=2) as executor:
        monkeypatch.setattr(flow, "_executor", executor)
        ids = [submit(client, user_utterance=f"上车去康源大厦见第 {i} 位客户。") for i in range(3)]
        try:
            assert entered.wait(timeout=5)
            assert calls == 2
            assert read(client, ids[2])["status"] == "queued"
        finally:
            release.set()
    assert maximum == 2
    assert all(read(client, job_id)["status"] == "completed" for job_id in ids)
