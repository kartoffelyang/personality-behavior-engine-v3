"""Comparison orchestration uses the real worker with deterministic stage doubles."""
from __future__ import annotations

import copy
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from jinja2 import DictLoader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.llm_router import LLMRequestError
from web import auto_compare as compare
from web import auto_run as flow


class QueuedExecutor:
    def __init__(self):
        self.pending = []

    def submit(self, operation, *args):
        self.pending.append((operation, args))

    def run_next(self):
        operation, args = self.pending.pop(0)
        operation(*args)

    def drain(self):
        while self.pending:
            self.run_next()


class Stages:
    def __init__(self):
        self.calls = {persona: {2: 0, 3: 0, 7: 0, 8: 0} for persona in flow.PERSONAS}
        self.failures = {persona: {2: [], 7: [], 8: []} for persona in flow.PERSONAS}
        self.terminal = {}
        self.requests = []

    def called(self, persona, stage):
        self.calls[persona][stage] += 1
        failures = self.failures[persona].get(stage, [])
        if failures:
            category = failures.pop(0)
            if category:
                raise LLMRequestError(category=category, message="测试失败", retryable=category == "timeout")

    def intent(self, request, backend, client, scenarios):
        persona = request["persona"]
        self.requests.append(copy.deepcopy(request))
        self.called(persona, 2)
        return {"persona": persona, "scenario_id": "welcome_on_board", "parsed_context": {},
                "context": {}, "output": {"persona": persona}, "utterance": request["user_utterance"]}

    def prepare(self, request, *, shared_intent, defer_step7, **kwargs):
        persona = request["persona"]
        self.called(persona, 3)
        assert shared_intent["persona"] == persona
        assert shared_intent["utterance"] == request["user_utterance"]
        assert defer_step7 is True
        return {"persona": persona, "utterance": request["user_utterance"]}

    def plan(self, prepared, *, step_no, **kwargs):
        persona = prepared["persona"]
        assert step_no == 7
        self.called(persona, 7)
        status = self.terminal.get(persona, "ok")
        actions = [] if status != "ok" else [{"id": persona + "-1", "name": persona + "温度调节",
                                              "capability": "空调", "domain": "座舱舒适"}]
        steps = [{"step": i, "title": title, "status": "ok", "input_text": persona + "输入",
                  "output_text": persona + "输出"} for i, title in enumerate(flow.STEP_TITLES[:7], 1)]
        steps[-1]["output"] = {"validated_actions": actions, "generation_status": status,
                               "clarification_questions": ["请补充信息。"] if status == "needs_clarification" else []}
        return {"persona": persona, "generation_status": status, "final": {"generation_status": status},
                "audit_steps": steps}

    def story(self, payload, backend):
        persona = payload["persona"]
        assert payload["request"]["persona"] == persona
        assert payload["result"]["persona"] == persona
        self.called(persona, 8)
        return {"status": "ok", "output": {"paragraphs": [{"segments": [{"text": persona + "场景故事。"}]}],
                                            "core_changes": []}}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    executor, stages = QueuedExecutor(), Stages()
    monkeypatch.setattr(flow, "OUT_DIR", tmp_path / "children")
    monkeypatch.setattr(compare, "OUT_DIR", tmp_path / "parents")
    monkeypatch.setattr(flow, "_jobs", {})
    monkeypatch.setattr(flow, "_active", set())
    monkeypatch.setattr(compare, "_comparisons", {})
    monkeypatch.setattr(flow, "_executor", executor)
    monkeypatch.setattr(flow, "RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(flow, "Engine", lambda **kwargs: SimpleNamespace(scenarios={}))
    monkeypatch.setattr(flow, "get_llm", lambda backend: SimpleNamespace(race_n=1))
    monkeypatch.setattr(flow, "run_intent", stages.intent)
    monkeypatch.setattr(flow, "generate_behavior_plan", stages.prepare)
    monkeypatch.setattr(flow, "rerun_from_review_step", stages.plan)
    monkeypatch.setattr(flow, "build_story_input", lambda value: value)
    monkeypatch.setattr(flow, "run_story", stages.story)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.jinja_loader = DictLoader({
        "auto_run.html": "legacy {{ initial_job_id or 'new' }}",
        "auto_compare.html": "compare {{ initial_job_id or 'new' }}",
        "auto_run_result.html": "{{ persona }} {{ story|tojson }}",
        "auto_run_graph.html": "graph {{ persona }} {{ final_snapshot|tojson }}",
    })
    app.register_blueprint(flow.bp)
    app.register_blueprint(compare.bp)
    return app, executor, stages


def submit(client, **overrides):
    response = client.post("/api/auto-compare", json={"user_utterance": "上车有点热，请调到23度。", **overrides})
    assert response.status_code == 202, response.json
    return response.json["job"]


def read(client, job_id):
    response = client.get("/api/auto-compare/" + job_id)
    assert response.status_code == 200
    return response.json["job"]


def test_two_independent_eight_step_chains_share_only_the_input(setup):
    app, executor, stages = setup
    client = app.test_client()
    created = submit(client, user_utterance="  一起去出游  ")
    assert created["personas"] == ["ESTJ", "INFP"]
    assert [branch["persona"] for branch in created["branches"]] == ["ESTJ", "INFP"]
    assert len(executor.pending) == 2
    assert {operation for operation, _ in executor.pending} == {flow._run_job}
    assert created["status"] == "queued"
    assert [branch["user_utterance"] for branch in created["branches"]] == ["一起去出游"] * 2
    assert created["branches"][0]["id"] != created["branches"][1]["id"]
    assert all(not any(calls.values()) for calls in stages.calls.values())
    executor.drain()
    job = read(client, created["id"])
    assert job["status"] == "completed"
    for branch in job["branches"]:
        persona = branch["persona"]
        assert stages.calls[persona] == {2: 1, 3: 1, 7: 1, 8: 1}
        assert [step["status"] for step in branch["steps"]] == ["completed"] * 8
        assert len(branch["result"]["audit_steps"]) == 8
        assert branch["result"]["story_display"]["paragraphs"][0]["segments"][0]["text"] == persona + "场景故事。"
        assert branch["result"]["validated_actions"][0]["id"] == persona + "-1"
        assert client.get("/auto_run/" + branch["id"] + "/graph").status_code == 200
    assert len(stages.requests) == 2
    assert stages.requests[0]["request_id"] != stages.requests[1]["request_id"]
    assert client.get(job["result_url"]).status_code == 200
    assert "_request" not in json.dumps(job) and "_branch_ids" not in json.dumps(job)


def test_both_chains_can_run_simultaneously_without_a_parent_worker(setup, monkeypatch):
    app, _, stages = setup
    entered = threading.Barrier(3)
    release = threading.Event()

    def hold_intent(*args, **kwargs):
        entered.wait(timeout=5)
        assert release.wait(timeout=5)
        return stages.intent(*args, **kwargs)

    monkeypatch.setattr(flow, "run_intent", hold_intent)
    with ThreadPoolExecutor(max_workers=2) as executor:
        monkeypatch.setattr(flow, "_executor", executor)
        client = app.test_client()
        created = submit(client)
        try:
            entered.wait(timeout=5)
            running = read(client, created["id"])
            assert running["status"] == "running"
            assert [branch["current_step"] for branch in running["branches"]] == [2, 2]
        finally:
            release.set()
    assert read(client, created["id"])["status"] == "completed"


@pytest.mark.parametrize("stage", [2, 7, 8])
def test_partial_failure_retries_only_failed_branch_and_preserves_checkpoints(setup, stage):
    app, executor, stages = setup
    stages.failures["INFP"][stage] = ["timeout"] * 3
    client = app.test_client()
    created = submit(client)
    executor.drain()
    partial = read(client, created["id"])
    assert partial["status"] == "partial_failed"
    assert [branch["status"] for branch in partial["branches"]] == ["completed", "failed"]
    estj = copy.deepcopy(partial["branches"][0])
    assert stages.calls["INFP"][stage] == 3
    response = client.post("/api/auto-compare/" + created["id"] + "/retry")
    assert response.status_code == 202
    assert len(executor.pending) == 1
    assert response.json["job"]["branches"][0] == estj
    executor.drain()
    completed = read(client, created["id"])
    assert completed["status"] == "completed"
    assert completed["branches"][0] == estj
    assert stages.calls["ESTJ"] == {2: 1, 3: 1, 7: 1, 8: 1}
    assert stages.calls["INFP"] == {s: (4 if s == stage else 1) for s in [2, 3, 7, 8]}


def test_failed_branch_can_retry_while_other_branch_still_active(setup):
    app, executor, stages = setup
    client = app.test_client()
    stages.failures["ESTJ"][2] = ["auth_invalid"]
    created = submit(client)
    executor.run_next()
    running = read(client, created["id"])
    assert running["status"] == "running"
    assert [branch["status"] for branch in running["branches"]] == ["failed", "queued"]
    for _ in range(3):
        response = client.post("/api/auto-compare/" + created["id"] + "/retry", json={"persona": "ESTJ"})
        assert response.status_code == 202
    assert len(executor.pending) == 2  # original INFP plus one ESTJ continuation
    executor.drain()
    assert read(client, created["id"])["status"] == "completed"
    assert stages.calls["ESTJ"][2] == 2 and stages.calls["INFP"][2] == 1


def test_selected_completed_branch_is_not_rerun(setup):
    app, executor, stages = setup
    client = app.test_client()
    stages.failures["INFP"][7] = ["timeout"] * 3
    created = submit(client)
    executor.drain()
    response = client.post("/api/auto-compare/" + created["id"] + "/retry", json={"persona": "ESTJ"})
    assert response.status_code == 409
    assert not executor.pending
    assert read(client, created["id"])["status"] == "partial_failed"


def test_concurrent_duplicate_submissions_and_retries_are_idempotent(setup):
    app, executor, stages = setup
    for persona in compare.PERSONAS:
        stages.failures[persona][7] = ["timeout"] * 3
    with ThreadPoolExecutor(max_workers=6) as callers:
        results = list(callers.map(lambda _: submit(app.test_client()), range(12)))
    ids = {job["id"] for job in results}
    assert len(ids) == 1 and len(executor.pending) == 2
    job_id = ids.pop()
    executor.drain()
    assert read(app.test_client(), job_id)["status"] == "failed"
    with ThreadPoolExecutor(max_workers=6) as callers:
        responses = list(callers.map(lambda _: app.test_client().post("/api/auto-compare/" + job_id + "/retry"), range(12)))
    assert all(response.status_code == 202 for response in responses)
    assert len(executor.pending) == 2
    executor.drain()
    assert read(app.test_client(), job_id)["status"] == "completed"
    assert all(stages.calls[persona] == {2: 1, 3: 1, 7: 4, 8: 1} for persona in compare.PERSONAS)


def test_refresh_and_restart_restore_both_branches_without_new_requests(setup):
    app, executor, stages = setup
    client = app.test_client()
    created = submit(client)
    executor.run_next()  # ESTJ finishes, INFP has not started.
    before = copy.deepcopy(stages.calls)
    assert client.get("/auto_run").data == b"compare new"
    for _ in range(2):
        assert read(client, created["id"])["status"] == "running"
        assert client.get(created["result_url"]).status_code == 200
    assert stages.calls == before and len(executor.pending) == 1
    compare._comparisons.clear()
    flow._jobs.clear()
    flow._active.clear()
    executor.pending.clear()
    restored = read(client, created["id"])
    assert restored["status"] == "partial_failed"
    assert restored["branches"][1]["error"]["category"] == "interrupted"
    assert stages.calls == before and not executor.pending
    assert client.post("/api/auto-compare/" + created["id"] + "/retry").status_code == 202
    executor.drain()
    assert read(client, created["id"])["status"] == "completed"
    assert all(stages.calls[persona] == {2: 1, 3: 1, 7: 1, 8: 1} for persona in compare.PERSONAS)
    compare._comparisons.clear()
    flow._jobs.clear()
    assert read(client, created["id"])["status"] == "completed"
    assert not list(compare.OUT_DIR.glob("*.tmp"))


@pytest.mark.parametrize("terminal", ["needs_clarification", "no_tool_needed"])
def test_legitimate_non_story_result_preserves_each_branch_semantics(setup, terminal):
    app, executor, stages = setup
    client = app.test_client()
    stages.terminal["INFP"] = terminal
    created = submit(client)
    executor.drain()
    completed = read(client, created["id"])
    assert completed["status"] == "completed"
    assert completed["branches"][1]["status"] == terminal
    assert completed["branches"][1]["steps"][7]["status"] == "skipped"
    assert stages.calls["INFP"][8] == 0
    assert client.post("/api/auto-compare/" + created["id"] + "/retry").status_code == 409


@pytest.mark.parametrize("body", [None, [], "text", {}, {"user_utterance": 1}, {"user_utterance": " "},
                                  {"user_utterance": "a" * 10001},
                                  {"user_utterance": "上车", "llm_backend": []},
                                  {"user_utterance": "上车", "llm_backend": "rules"}])
def test_bad_create_input_does_not_start_work(setup, body):
    app, executor, stages = setup
    assert app.test_client().post("/api/auto-compare", json=body).status_code == 400
    assert not executor.pending and not compare._comparisons


@pytest.mark.parametrize("body", [None, [], {"persona": None}, {"persona": []}, {"persona": "NO_MBTI"}])
def test_bad_retry_input_does_not_start_work(setup, body):
    app, executor, _ = setup
    client = app.test_client()
    job = submit(client)
    response = client.post("/api/auto-compare/" + job["id"] + "/retry", data=json.dumps(body), content_type="application/json")
    assert response.status_code == 400
    assert len(executor.pending) == 2


def test_old_single_personality_api_and_saved_pages_still_work_without_cross_deduplication(setup):
    app, executor, _ = setup
    client = app.test_client()
    compared = submit(client)
    single_body = {"persona": "INFP", "user_utterance": compared["user_utterance"]}
    single = client.post("/api/auto-run", json=single_body).json["job"]
    assert single["id"] not in {branch["id"] for branch in compared["branches"]}
    assert client.post("/api/auto-run", json=single_body).json["job"]["id"] == single["id"]
    assert client.get(single["result_url"]).data == ("legacy " + single["id"]).encode()
    assert len(executor.pending) == 3
    executor.drain()
    assert client.get("/api/auto-run/" + single["id"]).json["job"]["status"] == "completed"
    assert client.get("/auto_run/" + single["id"] + "/result").status_code == 200
    assert read(client, compared["id"])["status"] == "completed"


def test_new_finished_submission_gets_new_children_and_separate_input_does_not_mix(setup):
    app, executor, _ = setup
    client = app.test_client()
    first = submit(client)
    second = submit(client, user_utterance="去另一处地点")
    assert first["id"] != second["id"]
    assert not ({branch["id"] for branch in first["branches"]} & {branch["id"] for branch in second["branches"]})
    executor.drain()
    third = submit(client)
    assert third["id"] != first["id"]
    assert len(executor.pending) == 2


def test_missing_corrupt_and_cross_linked_comparisons_are_not_exposed(setup):
    app, executor, _ = setup
    client = app.test_client()
    for job_id in ["invalid", "f" * 32]:
        assert client.get("/api/auto-compare/" + job_id).status_code == 404
        assert client.get("/auto_run/compare/" + job_id).status_code == 404
        assert client.post("/api/auto-compare/" + job_id + "/retry").status_code == 404
        assert client.get("/auto_run/" + job_id + "/graph").status_code == 404
    compare.OUT_DIR.mkdir(exist_ok=True)
    (compare.OUT_DIR / ("b" * 32 + ".json")).write_text("[]", encoding="utf-8")
    assert client.get("/api/auto-compare/" + "b" * 32).status_code == 404
    first = submit(client)
    second = submit(client, user_utterance="另一情境")
    assert client.get("/auto_run/" + first["branches"][0]["id"] + "/graph").status_code == 409
    invalid_backend = copy.deepcopy(compare._comparisons[first["id"]])
    invalid_backend.update(id="c" * 32, llm_backend=[])
    compare._save_comparison(invalid_backend)
    assert client.get("/api/auto-compare/" + "c" * 32).status_code == 404
    corrupted = compare._comparisons[first["id"]]
    corrupted["_branch_ids"]["INFP"] = second["branches"][1]["id"]
    compare._save_comparison(corrupted)
    compare._comparisons.clear()
    assert client.get("/api/auto-compare/" + first["id"]).status_code == 404
    assert len(executor.pending) == 4


def test_scheduling_failure_is_reported_on_one_branch_without_blocking_other(setup, monkeypatch):
    app, executor, _ = setup
    original = executor.submit

    def submit_one(operation, child_id):
        if flow._jobs[child_id]["persona"] == "ESTJ":
            raise RuntimeError("test worker unavailable")
        original(operation, child_id)

    monkeypatch.setattr(executor, "submit", submit_one)
    client = app.test_client()
    created = submit(client)
    assert [branch["status"] for branch in created["branches"]] == ["failed", "queued"]
    executor.drain()
    assert read(client, created["id"])["status"] == "partial_failed"
