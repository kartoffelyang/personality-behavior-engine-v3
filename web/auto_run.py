"""Resumable, background eight-step generation for the one-click page."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request

from engine.llm_diagnostics import safe_url
from engine.llm_router import LLMRequestError, describe_llm_error, get_llm
from engine.model_intent import run_intent
from engine.model_story import STORY_TITLE, build_story_display, build_story_input, scenario_assumptions, run_story
from engine.pipeline import Engine, default_request_scaffold, generate_behavior_plan, rerun_from_review_step
from engine.trace_logger import PERSONA_DESCRIPTION_TITLE, SCENARIO_STRATEGY_TITLE

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "auto_run"
PERSONAS = ["INFP", "ESTJ", "NO_MBTI"]
BACKENDS = {"openai", "carsona"}
STEP_TITLES = ["输入：人格＋自由情境", "MODEL 1：情境理解", "MAP：人格主导功能查表",
               PERSONA_DESCRIPTION_TITLE, SCENARIO_STRATEGY_TITLE, "MAP：人格交互维度判断",
               "行为序列生成与输出（功能优先级＋调用负担）", STORY_TITLE]
MAX_ATTEMPTS = 3
RETRY_DELAYS = (2, 5)
TERMINAL_RESULTS = {"completed", "needs_clarification", "no_tool_needed"}
ACTIVE_STATUSES = {"queued", "running", "retrying"}
NON_RETRYABLE = {"auth_invalid", "auth_missing", "permission_denied", "model_not_found",
                 "model_unavailable", "configuration_error", "invalid_request", "internal_error"}
RETRYABLE = {"timeout", "gateway_timeout", "network_error", "rate_limited", "upstream_error",
             "empty_response", "invalid_response", "planner_validation", "invalid_story",
             "story_boundary", "invalid_intent", "connection_error"}

auto_run = Blueprint("auto_run", __name__)
bp = auto_run
_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_active: set[str] = set()
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="auto-run")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(job: dict) -> None:
    """Call with _lock held; replace ensures readers never see half a JSON file."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / (job["id"] + ".json")
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    job["updated_at"] = _now()
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(job, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _get_job(job_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return None
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            try:
                job = json.loads((OUT_DIR / (job_id + ".json")).read_text(encoding="utf-8"))
                if (not isinstance(job, dict) or job.get("id") != job_id
                        or job.get("persona") not in PERSONAS or len(job.get("steps", [])) != 8
                        or job.get("llm_backend") not in BACKENDS):
                    return None
            except (OSError, ValueError, TypeError):
                return None
            _jobs[job_id] = job
        if job["status"] in ACTIVE_STATUSES and job_id not in _active:
            # Persisted work cannot be running in this new process. A read is
            # deliberately never permission to make another model request.
            job.update(status="failed", message="服务重启中断了生成，可从未完成阶段继续。",
                       error={"category": "interrupted", "message": "服务重启中断了生成。", "retryable": True})
            unfinished = next((s for s in job["steps"] if s["status"] not in {"completed", "skipped"}), None)
            if unfinished:
                unfinished["status"] = "failed"
                job["current_step"] = unfinished["step"]
            _save(job)
        return job


def _display_text(value: str) -> str:
    def clean_url(match):
        try:
            return safe_url(match.group(0))
        except ValueError:
            return "[地址已隐藏]"

    text = re.sub(r"https?://[^\s<>\"'，。；）)]+", clean_url, str(value), flags=re.I)
    text = re.sub(r"(?i)(bearer\s+|api[_-]?key[=:\s]+)[^\s,;]+", r"\1[已隐藏]", text)
    return re.sub(r"\bsk-[A-Za-z0-9_-]+", "[已隐藏]", text)[:1200]


def _display_error(info: dict | None) -> dict | None:
    if not info:
        return None
    # Never expose upstream response bodies, URL query credentials, or prompts.
    result = {key: info[key] for key in ("category", "message", "hint", "retryable", "status_code") if key in info}
    for key in ("message", "hint"):
        if key in result:
            result[key] = _display_text(result[key])
    diagnostics = info.get("diagnostics") or {}
    if isinstance(diagnostics, dict):
        result["diagnostics"] = {key: diagnostics[key] for key in (
            "failure_kind", "elapsed_seconds", "received_chars", "chunk_count", "timeout_seconds") if key in diagnostics}
    return result


def _snapshot(job: dict) -> dict:
    with _lock:
        result = {key: copy.deepcopy(job.get(key)) for key in (
            "id", "persona", "user_utterance", "llm_backend", "status", "current_step", "message", "steps", "result")}
        result["message"] = _display_text(result.get("message") or "")
        result["error"] = _display_error(job.get("error"))
        result["result_url"] = "/auto_run/" + job["id"]
        result["attempts"] = [{**{key: item[key] for key in ("step", "attempt", "status", "elapsed_seconds") if key in item},
                               "error": _display_error(item.get("error"))}
                              for item in job.get("_attempts", [])]
        return result


def _step_record(plan: dict) -> dict:
    return next((s for s in plan.get("audit_steps", []) if s.get("step") == 7), {})


def _plan_status(plan: dict) -> str:
    record = _step_record(plan)
    output = record.get("output") or {}
    return ((plan.get("final") or {}).get("generation_status") or plan.get("generation_status")
            or output.get("generation_status") or "ok")


def _stage_error(step: int, value: dict) -> dict | None:
    if not isinstance(value, dict):
        return {"category": "invalid_response", "message": "模型返回的阶段结果格式无效。", "retryable": True}
    if step == 2:
        if value.get("fallback") or value.get("error_info"):
            return value.get("error_info") or {"category": "invalid_intent", "message": "情境理解失败，未使用规则结果代替。", "retryable": True}
        if not all(key in value for key in ("scenario_id", "parsed_context", "context", "output")):
            return {"category": "invalid_intent", "message": "情境理解缺少必要字段。", "retryable": True}
    elif step == 7:
        record = _step_record(value)
        output = record.get("output") or {}
        if _plan_status(value) in {"failed", "fallback", "pending"} or record.get("status") in {"failed", "fallback", "pending"}:
            return (value.get("generation_error") or record.get("error_info") or output.get("generation_error")
                    or {"category": "planner_validation", "message": "行为序列生成失败。", "retryable": True})
        actions = output.get("validated_actions") or []
        if _plan_status(value) not in {"needs_clarification", "no_tool_needed"} and not actions:
            return {"category": "planner_validation", "message": "未生成有效行为序列。", "retryable": True}
        if actions and not all(isinstance(a, dict) and a.get("capability") and a.get("name") for a in actions):
            return {"category": "planner_validation", "message": "行为序列字段不完整。", "retryable": True}
    elif step == 8 and value.get("status") != "ok":
        return value.get("error_info") or {"category": "invalid_story", "message": "场景故事生成失败。", "retryable": True}
    return None


def _can_retry(info: dict, step: int) -> bool:
    category = info.get("category")
    if category in NON_RETRYABLE:
        return False
    if category in RETRYABLE or info.get("retryable"):
        return True
    if category == "http_error":
        return info.get("status_code", 0) >= 500 or info.get("status_code") in {408, 429}
    # The existing parsers describe ValueError / JSON errors as unknown_error.
    return category == "unknown_error" and step in {2, 7, 8}


def _attempt(job: dict, step: int, operation, checkpoint: str) -> bool:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        with _lock:
            state = job["steps"][step - 1]
            state.update(status="running", attempt=state["attempt"] + 1)
            total_attempt = state["attempt"]
            job.update(status="running", current_step=step, error=None,
                       message=f"正在生成第 {step} 步：{state['title']}（阶段尝试 {total_attempt}）")
            _save(job)
        started = time.monotonic()
        value = None
        try:
            value = operation()
            error = _stage_error(step, value)
        except Exception as exc:
            error = describe_llm_error(exc)
            if step == 3 or not isinstance(exc, (LLMRequestError, ValueError)):
                error.update(category="internal_error", retryable=False)
        with _lock:
            job["_attempts"].append({"step": step, "attempt": total_attempt,
                                     "status": "failed" if error else "completed",
                                     "elapsed_seconds": round(time.monotonic() - started, 2), "error": error})
            if not error:
                if step == 2:
                    value["elapsed_seconds"] = round(time.monotonic() - started, 2)
                job[checkpoint] = value
                state["status"] = "completed"
                if step == 3:
                    for mapped in job["steps"][3:6]:
                        mapped.update(status="completed", attempt=1)
                _save(job)
                return True
            error = {**error, "retryable": _can_retry(error, step)}
            job["error"] = error
            if not error["retryable"] or attempt == MAX_ATTEMPTS:
                state["status"] = "failed"
                job.update(status="failed", message=f"第 {step} 步未完成：{_display_error(error)['message']}")
                _save(job)
                return False
            state["status"] = "retrying"
            delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
            job.update(status="retrying", message=f"第 {step} 步请求失败，{delay} 秒后自动重新请求（本轮最多 {MAX_ATTEMPTS} 次阶段尝试）。")
            _save(job)
        time.sleep(delay)
    return False


def _finish(job: dict) -> None:
    plan = job["_plan"]
    record = _step_record(plan)
    output = record.get("output") or {}
    story = job.get("_story") or {}
    status = _plan_status(plan)
    final_status = status if status in {"needs_clarification", "no_tool_needed"} else "completed"
    story_display = build_story_display(story.get("output") or {}, job["persona"])
    story_audit = {"step": 8, "title": STORY_TITLE, "status": "ok" if story else "skipped",
                   "input_text": "根据已验证的行为序列生成场景故事，保留动作执行条件。",
                   "output_text": ("\n".join("".join(segment["text"] for segment in paragraph["segments"])
                                            for paragraph in story_display["paragraphs"])
                                   if story else "当前未形成可叙述的行为序列，故事阶段已跳过。")}
    if "elapsed_seconds" in story:
        story_audit["elapsed_seconds"] = story["elapsed_seconds"]
    audit = [{key: step[key] for key in ("step", "title", "status", "elapsed_seconds", "input_text", "output_text", "rules_hit") if key in step}
             for step in list(plan.get("audit_steps") or []) + [story_audit]]
    questions = ((plan.get("final") or {}).get("clarification_questions") or plan.get("clarification_questions")
                 or output.get("clarification_questions") or [])
    with _lock:
        job["result"] = {
            "story_display": story_display,
            "validated_actions": output.get("validated_actions") or [],
            "quality_warnings": story.get("quality_warnings") or [],
            "clarification_questions": questions,
            "scenario_assumptions": scenario_assumptions(plan),
            "audit_steps": audit,
        }
        if not story:
            job["steps"][7]["status"] = "skipped"
        job.update(status=final_status, current_step=8, error=None,
                   message={"completed": "场景故事和行为图已生成。", "needs_clarification": "已完成分析，部分执行条件仍需补充信息。",
                            "no_tool_needed": "已完成分析，此情境无需调用原子能力。"}[final_status])
        _save(job)


def _run_job(job_id: str) -> None:
    job = None
    try:
        job = _get_job(job_id)
        if job is None:
            return
        with _lock:
            job["steps"][0].update(status="completed", attempt=1)
            _save(job)
        backend = job["llm_backend"]
        engine = Engine(use_llm=True, llm_backend=backend)
        req = job["_request"]

        def understand():
            client = get_llm(backend)
            if client is None:
                raise LLMRequestError(category="model_unavailable", message="所选模型后端不可用。")
            if hasattr(client, "race_n"):
                client.race_n = 1
            return run_intent(req, backend, client, engine.scenarios)

        if not job.get("_intent") and not _attempt(job, 2, understand, "_intent"):
            return
        if not job.get("_prepared"):
            if not _attempt(job, 3, lambda: generate_behavior_plan(
                    req, use_llm=True, engine=engine, llm_backend=backend,
                    shared_intent=job["_intent"], defer_step7=True), "_prepared"):
                return
        if not job.get("_plan") and not _attempt(job, 7, lambda: rerun_from_review_step(
                job["_prepared"], step_no=7, engine=engine, llm_backend=backend), "_plan"):
            return
        plan = job["_plan"]
        actions = (_step_record(plan).get("output") or {}).get("validated_actions") or []
        if _plan_status(plan) != "no_tool_needed" and actions:
            if not job.get("_story") and not _attempt(job, 8, lambda: run_story(
                    build_story_input({"persona": job["persona"], "request": req, "result": plan}), backend), "_story"):
                return
        _finish(job)
    except Exception as exc:
        if job is not None:
            with _lock:
                error = describe_llm_error(exc)
                error.update(category="internal_error", retryable=False)
                step = max(1, min(8, job.get("current_step") or 1))
                job["steps"][step - 1]["status"] = "failed"
                job.update(status="failed", error=error, message="任务未能完成，请检查错误后继续。")
                _save(job)
    finally:
        with _lock:
            _active.discard(job_id)


def _schedule(job: dict) -> None:
    """Call under the shared lock, so duplicate retry requests cannot race."""
    _active.add(job["id"])
    try:
        _executor.submit(_run_job, job["id"])
    except Exception:
        _active.discard(job["id"])
        job.update(status="failed", error={"category": "internal_error", "message": "后台任务暂时无法启动。", "retryable": False},
                   message="后台任务暂时无法启动。")
        _save(job)
        raise


def _new_job(persona: str, backend: str, utterance: str, *, comparison_id: str | None = None) -> dict:
    """Create a saved checkpoint under _lock; scheduling is deliberately separate."""
    job_id = uuid.uuid4().hex
    digest = hashlib.sha256(json.dumps([persona, backend, utterance], ensure_ascii=False).encode()).hexdigest()
    job = {"id": job_id, "persona": persona, "user_utterance": utterance, "llm_backend": backend,
           "status": "queued", "current_step": 1, "message": "已提交，等待开始。", "error": None, "result": None,
           "steps": [{"step": i, "title": title, "status": "pending", "attempt": 0}
                     for i, title in enumerate(STEP_TITLES, 1)],
           "_digest": digest, "_request": default_request_scaffold(persona, utterance, "auto_run_" + job_id),
           "_attempts": [], "created_at": _now()}
    job["_request"]["generation_mode"] = "demo"
    if comparison_id is not None:
        job["_comparison_id"] = comparison_id
    _jobs[job_id] = job
    _save(job)
    return job


def _resume_job(job: dict) -> bool:
    """Resume only failed work under _lock; callers may safely repeat a request."""
    if job["id"] in _active:
        return True
    if job["status"] != "failed":
        return False
    job.update(status="queued", error=None, message="已提交，将从未完成阶段继续。")
    _save(job)
    _schedule(job)
    return True


def _presets() -> list:
    try:
        cases = json.loads((ROOT / "cases" / "welcome_cases.json").read_text(encoding="utf-8"))
        return [{"case_id": c["case_id"], "title": c.get("title"),
                 "utterance": (c.get("request") or {}).get("user_utterance") or ""} for c in cases]
    except (OSError, ValueError, KeyError, TypeError):
        return []


@auto_run.get("/auto_run")
@auto_run.get("/auto_run/<job_id>")
def page(job_id: str | None = None):
    if job_id and _get_job(job_id) is None:
        abort(404)
    template = "auto_run.html" if job_id else "auto_compare.html"
    return render_template(template, initial_job_id=job_id, personas=PERSONAS,
                           default_backend="openai", presets=_presets())


@auto_run.post("/api/auto-run")
def create():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请提交有效的 JSON 对象。"), 400
    persona, backend, utterance = data.get("persona"), data.get("llm_backend", "openai"), data.get("user_utterance")
    if not isinstance(persona, str) or persona not in PERSONAS:
        return jsonify(ok=False, error="请选择有效的人格。"), 400
    if not isinstance(backend, str) or backend not in BACKENDS:
        return jsonify(ok=False, error="请选择可用的模型后端。"), 400
    if not isinstance(utterance, str) or not utterance.strip() or len(utterance) > 10000:
        return jsonify(ok=False, error="请输入 1 至 10000 字的情境。"), 400
    utterance = utterance.strip()
    digest = hashlib.sha256(json.dumps([persona, backend, utterance], ensure_ascii=False).encode()).hexdigest()
    with _lock:
        for job_id in _active:
            existing = _jobs[job_id]
            if existing.get("_digest") == digest and not existing.get("_comparison_id"):
                return jsonify(ok=True, job=_snapshot(existing)), 202
        job = _new_job(persona, backend, utterance)
        _schedule(job)
        return jsonify(ok=True, job=_snapshot(job)), 202


@auto_run.get("/api/auto-run/<job_id>")
def status(job_id: str):
    job = _get_job(job_id)
    if job is None:
        return jsonify(ok=False, error="任务不存在。"), 404
    return jsonify(ok=True, job=_snapshot(job))


@auto_run.post("/api/auto-run/<job_id>/retry")
def retry(job_id: str):
    with _lock:
        job = _get_job(job_id)
        if job is None:
            return jsonify(ok=False, error="任务不存在。"), 404
        if not _resume_job(job):
            return jsonify(ok=False, error="仅未完成的失败任务可以继续。", job=_snapshot(job)), 409
        return jsonify(ok=True, job=_snapshot(job)), 202


@auto_run.get("/auto_run/<job_id>/result")
def result_page(job_id: str):
    job = _get_job(job_id)
    if job is None:
        abort(404)
    if job["status"] not in TERMINAL_RESULTS or not job.get("result"):
        return "结果尚未生成。", 409
    result = job["result"]
    return render_template("auto_run_result.html", job=_snapshot(job), persona=job["persona"],
                           story=result["story_display"], final_snapshot={"validated_actions": result["validated_actions"]},
                           audit_steps=result["audit_steps"], quality_warnings=result["quality_warnings"],
                           clarification_questions=result["clarification_questions"],
                           scenario_assumptions=result.get("scenario_assumptions") or [])


@auto_run.get("/auto_run/<job_id>/graph")
def graph_page(job_id: str):
    job = _get_job(job_id)
    if job is None:
        abort(404)
    if job["status"] not in TERMINAL_RESULTS or not job.get("result"):
        return "结果尚未生成。", 409
    result = job["result"]
    return render_template("auto_run_graph.html", job=_snapshot(job), persona=job["persona"],
                           final_snapshot={"validated_actions": result["validated_actions"]},
                           story=result["story_display"], audit_steps=result["audit_steps"],
                           quality_warnings=result["quality_warnings"],
                           clarification_questions=result["clarification_questions"])
