from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web import flow_review_app as review
from web.app import summarize_result


def result_fixture(status):
    questions = ["请提供康源大厦所在城市。", "目前是否已停车？"] if status == "needs_clarification" else []
    return {
        "scenario_id": "time_pressure",
        "llm_backend": "openai",
        "generation_status": status,
        "clarification_questions": questions,
        "final": {"generation_status": status, "clarification_questions": questions},
        "audit_steps": [{
            "step": 7, "title": "行为序列生成", "status": status,
            "input": {}, "output": {
                "generation_status": status,
                "clarification_questions": questions,
                "planner_output": {"plan_status": "ready" if status == "ok" else status},
                "validated_actions": [{"capability": "navigation", "name": "地点搜索"}]
                if status in {"ok", "needs_clarification"} else [],
            },
        }],
    }


@pytest.fixture
def session_factory(monkeypatch, tmp_path):
    monkeypatch.setattr(review, "_session_store", {})
    monkeypatch.setattr(review, "OUT_DIR", tmp_path)

    def unexpected_story(*args, **kwargs):
        raise AssertionError("A terminal Step 7 result must not generate a story")

    monkeypatch.setattr(review, "run_story", unexpected_story)

    def create(status):
        sid = "status_ui"
        session = {
            "persona": "INFP", "backend": "openai", "confirmed_step": 6,
            "result": result_fixture(status),
            "request": {"persona": "INFP", "user_utterance": "十点前去康源大厦"},
        }
        review._session_store[sid] = session
        return review.app.test_client(), sid, session

    return create


def test_no_tool_status_is_visible_and_cannot_unlock_story(session_factory):
    status = "no_tool_needed"
    label = "本次情境无需调用车载能力，流程在本步结束。"
    client, sid, session = session_factory(status)
    page = client.get(f"/session/{sid}/step/7").get_data(as_text=True)
    assert label in page
    assert 'class="failure-box" role="alert"' not in page
    assert "最终结果快照" not in page
    assert "确认行为序列，进入 Step 8" not in page
    confirmation = client.post(f"/session/{sid}/step/7/confirm")
    assert confirmation.headers["Location"].endswith("/step/7")
    assert session["confirmed_step"] == 6

    session["confirmed_step"] = 7  # A stale confirmation must not bypass server checks.
    for route in ("step/8", "step/8/generate", "step/8/confirm", "step/7/confirm"):
        response = client.get(f"/session/{sid}/{route}") if route == "step/8" else client.post(f"/session/{sid}/{route}")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/step/7")
    assert "story_step" not in session


def test_clarification_with_actions_is_advisory_and_can_generate_story(session_factory, monkeypatch):
    client, sid, session = session_factory("needs_clarification")
    page = client.get(f"/session/{sid}/step/7").get_data(as_text=True)
    assert "待确认信息 · 2 项" in page
    assert "不阻断本次 Demo" in page
    assert "请提供康源大厦所在城市。" in page
    assert "确认行为序列，进入 Step 8" in page

    confirmation = client.post(f"/session/{sid}/step/7/confirm")
    assert confirmation.headers["Location"].endswith("/step/8")
    assert session["confirmed_step"] == 7
    assert client.get(f"/session/{sid}/step/8").status_code == 200

    monkeypatch.setattr(review, "run_story", lambda payload, backend: {
        "status": "ok", "input": payload, "output": {
            "title": "演示故事", "paragraphs": [{"segments": [{
                "text": "助手先搜索康源大厦候选地点。", "action_ids": [1], "change_id": None,
            }]}], "core_changes": [], "spoken_reply": {"text": "正在搜索候选地点。"},
        },
    })
    generated = client.post(f"/session/{sid}/step/8/generate")
    assert generated.headers["Location"].endswith("/step/8")
    assert session["story_step"]["status"] == "ok"


def test_legacy_success_remains_confirmable_without_rewriting_result(session_factory):
    client, sid, session = session_factory("ok")
    session["result"]["audit_steps"][0]["output"]["planner_output"].pop("plan_status")
    original = deepcopy(session["result"])
    page = client.get(f"/session/{sid}/step/7").get_data(as_text=True)
    assert "确认行为序列，进入 Step 8" in page
    client.post(f"/session/{sid}/step/7/confirm")
    assert session["confirmed_step"] == 7
    assert session["result"] == original


@pytest.mark.parametrize("actions", [[], None, [{}], "已有渲染话术"])
def test_success_label_without_structured_actions_cannot_unlock_story(session_factory, actions):
    client, sid, session = session_factory("ok")
    output = session["result"]["audit_steps"][0]["output"]
    output["planner_output"].pop("plan_status")
    output["validated_actions"] = actions
    session["result"]["final"]["O_Action_Plan"] = ["搜索目的地并启动导航"]
    original = deepcopy(session["result"])

    confirmation = client.post(f"/session/{sid}/step/7/confirm")
    assert confirmation.headers["Location"].endswith("/step/7")
    assert session["confirmed_step"] == 6
    session["confirmed_step"] = 7
    generated = client.post(f"/session/{sid}/step/8/generate")
    assert generated.headers["Location"].endswith("/step/7")
    assert "story_step" not in session
    assert session["result"] == original


def test_homepage_summary_preserves_clarification_questions():
    result = result_fixture("needs_clarification")
    summary = summarize_result(result)
    assert summary["generation_status"] == "needs_clarification"
    assert summary["clarification_questions"] == result["clarification_questions"]


def test_homepage_mixed_results_have_distinct_status_counts_and_escape_questions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to exercise homepage rendering helpers")
    template = (ROOT / "web/templates/index.html").read_text(encoding="utf-8")
    helpers = template[template.index("    function esc("):template.index("    function renderResult(")]
    variants = [
        {"persona": "INFP", "result": {"generation_status": "needs_clarification", "clarification_questions": ["<script>bad</script>", "城市？"]}},
        {"persona": "ESTJ", "result": {"generation_status": "no_tool_needed"}},
        {"persona": "NO_MBTI", "result": {"generation_status": "ok", "O_Action_Plan": ["搜索地点"], "O_Behavior": {"导航": "【调用】地点搜索"}}},
    ]
    script = helpers + "\nconst variants = " + json.dumps(variants, ensure_ascii=False) + ";\n" + """
console.log(JSON.stringify({
  summary: comparisonRunStatus(variants),
  cards: renderOverviewCards(variants),
  matrix: renderBehaviorMatrix(variants),
  details: variants.map(renderVariantAuditCard).join(''),
}));
"""
    completed = subprocess.run([node], input=script, text=True, encoding="utf-8", capture_output=True, check=True)
    output = json.loads(completed.stdout)
    assert output["summary"]["className"] == "status"
    assert "行为序列就绪 1 组" in output["summary"]["text"]
    assert "待补充信息 1 组（2 项）" in output["summary"]["text"]
    assert "无需工具 1 组" in output["summary"]["text"]
    assert "待补充信息（2 项）" in output["matrix"]
    assert "无需工具调用" in output["matrix"]
    assert "<script>bad</script>" not in output["cards"]
    assert "&lt;script&gt;bad&lt;/script&gt;" in output["cards"]


@pytest.mark.parametrize('next_status', ['ok', 'failed'])
def test_step7_failed_output_can_retry_without_rerunning_upstream(session_factory, monkeypatch, next_status):
    client, sid, session = session_factory('failed')
    session['prompt_overrides'] = {7: {'system_prompt': '保留本次提示词', 'user_prompt': '保留输入'}}
    original = session['result']
    upstream = [{'step': n, 'status': 'ok', 'input': {}, 'output': {'saved': n}} for n in range(1, 7)]
    original['audit_steps'] = deepcopy(upstream) + original['audit_steps']
    session['story_step'] = {'status': 'ok', 'output': {}}  # A stale downstream result is invalidated.
    calls = []

    def rerun(previous, **kwargs):
        assert previous is original
        assert kwargs['step_no'] == 7
        assert kwargs['prompt_overrides'] == session['prompt_overrides']
        calls.append(kwargs)
        # A second submission during the first request cannot call the model again.
        duplicate = client.post(f'/session/{sid}/step/7/generate')
        assert duplicate.status_code == 302 and len(calls) == 1
        result = result_fixture(next_status)
        result['audit_steps'] = deepcopy(upstream) + result['audit_steps']
        return result

    monkeypatch.setattr(review, 'rerun_from_review_step', rerun)
    monkeypatch.setattr(review, 'get_engine', lambda backend: None)
    page = client.get(f'/session/{sid}/step/7').get_data(as_text=True)
    assert '>重新生成</button>' in page and not calls
    response = client.post(f'/session/{sid}/step/7/generate')
    assert response.status_code == 302 and len(calls) == 1
    assert session['result']['audit_steps'][:6] == upstream
    assert 'story_step' not in session and session['confirmed_step'] == 6
    page = client.get(response.location).get_data(as_text=True)
    assert ('>重新生成</button>' in page) == (next_status == 'failed')
    assert ('确认行为序列，进入 Step 8' in page) == (next_status == 'ok')
    saved = json.loads(Path(session['saved_path']).read_text(encoding='utf-8'))
    assert saved['result'] == session['result'] and len(calls) == 1


@pytest.mark.parametrize('status', ['ok', 'needs_clarification', 'no_tool_needed'])
def test_step7_retry_does_not_rerun_completed_or_clarification_states(session_factory, monkeypatch, status):
    client, sid, session = session_factory(status)
    def forbidden(*args, **kwargs):
        raise AssertionError('This state must not retry through the generation button')
    monkeypatch.setattr(review, 'rerun_from_review_step', forbidden)
    page = client.get(f'/session/{sid}/step/7').get_data(as_text=True)
    assert '>重新生成</button>' not in page
    assert client.post(f'/session/{sid}/step/7/generate').status_code == 302
