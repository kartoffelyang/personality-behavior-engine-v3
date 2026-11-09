"""Two independent, resumable personality runs for one situation.

The parent is a persisted association, never a worker waiting on child futures.
Both branches use auto_run's shared two-worker pool and checkpoint handling.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid

from flask import Blueprint, abort, jsonify, render_template, request

from web import auto_run as flow

OUT_DIR = flow.ROOT / "output" / "auto_compare"
PERSONAS = ["ESTJ", "INFP"]
auto_compare = Blueprint("auto_compare", __name__)
bp = auto_compare
_comparisons: dict[str, dict] = {}


def _save_comparison(job: dict) -> None:
    """Use the same process lock as the children and replace files atomically."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / (job["id"] + ".json")
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(job, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _get_comparison(job_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return None
    with flow._lock:
        job = _comparisons.get(job_id)
        if job is None:
            try:
                job = json.loads((OUT_DIR / (job_id + ".json")).read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return None
        if (not isinstance(job, dict) or job.get("id") != job_id
                or job.get("personas") != PERSONAS
                or not isinstance(job.get("llm_backend"), str)
                or job.get("llm_backend") not in flow.BACKENDS
                or not isinstance(job.get("user_utterance"), str)
                or not isinstance(job.get("_branch_ids"), dict)
                or set(job["_branch_ids"]) != set(PERSONAS)):
            return None
        for persona in PERSONAS:
            child_id = job["_branch_ids"][persona]
            if not isinstance(child_id, str):
                return None
            child = flow._get_job(child_id)
            if (child is None or child.get("_comparison_id") != job_id
                    or child.get("persona") != persona
                    or child.get("user_utterance") != job["user_utterance"]
                    or child.get("llm_backend") != job["llm_backend"]):
                return None
        _comparisons[job_id] = job
        return job


def _aggregate(branches: list[dict]) -> tuple[str, str]:
    statuses = [branch["status"] for branch in branches]
    active = [status for status in statuses if status in flow.ACTIVE_STATUSES]
    if active:
        if all(status == "queued" for status in statuses):
            return "queued", "已提交同一情境，ESTJ 与 INFP 将分别运行八步流程。"
        if "retrying" in active:
            return "retrying", "失败阶段正在自动重试，两条链路独立保存进度。"
        if "failed" in statuses:
            return "running", "一条链路仍在生成，另一条未完成，可单独重试。"
        return "running", "ESTJ 与 INFP 正在分别生成，已完成的结果可立即查看。"
    finished = sum(status in flow.TERMINAL_RESULTS for status in statuses)
    if finished == len(PERSONAS):
        return "completed", "两条链路均已完成，请对照查看场景故事和行为图。"
    if finished:
        return "partial_failed", "一条链路已完成，另一条未完成；重试将保留已有结果。"
    return "failed", "两条链路尚未完成，可从各自的失败阶段继续。"


def _snapshot(job: dict) -> dict:
    with flow._lock:
        branches = [flow._snapshot(flow._jobs[job["_branch_ids"][persona]]) for persona in PERSONAS]
        status, message = _aggregate(branches)
        return {"id": job["id"], "user_utterance": job["user_utterance"],
                "llm_backend": job["llm_backend"], "personas": list(PERSONAS),
                "status": status, "message": message, "branches": branches,
                "result_url": "/auto_run/compare/" + job["id"]}


@auto_compare.get("/auto_run/compare/<job_id>")
def page(job_id: str):
    if _get_comparison(job_id) is None:
        abort(404)
    return render_template("auto_compare.html", initial_job_id=job_id,
                           personas=PERSONAS, default_backend="openai", presets=flow._presets())


@auto_compare.post("/api/auto-compare")
def create():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请提交有效的 JSON 对象。"), 400
    backend, utterance = data.get("llm_backend", "openai"), data.get("user_utterance")
    if not isinstance(backend, str) or backend not in flow.BACKENDS:
        return jsonify(ok=False, error="请选择可用的模型后端。"), 400
    if not isinstance(utterance, str) or not utterance.strip() or len(utterance) > 10000:
        return jsonify(ok=False, error="请输入 1 至 10000 字的情境。"), 400
    utterance = utterance.strip()
    digest = hashlib.sha256(json.dumps([PERSONAS, backend, utterance], ensure_ascii=False).encode()).hexdigest()
    with flow._lock:
        for existing in _comparisons.values():
            if (existing.get("_digest") == digest
                    and any(child_id in flow._active for child_id in existing["_branch_ids"].values())):
                return jsonify(ok=True, job=_snapshot(existing)), 202
        job_id = uuid.uuid4().hex
        children = [flow._new_job(persona, backend, utterance, comparison_id=job_id) for persona in PERSONAS]
        job = {"id": job_id, "user_utterance": utterance, "llm_backend": backend,
               "personas": list(PERSONAS), "_digest": digest, "created_at": flow._now(),
               "_branch_ids": {child["persona"]: child["id"] for child in children}}
        _save_comparison(job)
        _comparisons[job_id] = job
        # Persist the full association before either worker can start. No parent
        # task occupies a worker slot, so both personality chains can progress.
        for child in children:
            try:
                flow._schedule(child)
            except Exception:
                # _schedule records the startup error in this child. The other
                # personality remains independently runnable and retryable.
                pass
        return jsonify(ok=True, job=_snapshot(job)), 202


@auto_compare.get("/api/auto-compare/<job_id>")
def status(job_id: str):
    with flow._lock:
        job = _get_comparison(job_id)
        if job is None:
            return jsonify(ok=False, error="对比任务不存在。"), 404
        return jsonify(ok=True, job=_snapshot(job))


@auto_compare.post("/api/auto-compare/<job_id>/retry")
def retry(job_id: str):
    data = request.get_json(silent=True) if request.data else {}
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请提交有效的 JSON 对象。"), 400
    persona = data.get("persona")
    if "persona" in data and (not isinstance(persona, str) or persona not in PERSONAS):
        return jsonify(ok=False, error="请选择 ESTJ 或 INFP 链路。"), 400
    with flow._lock:
        job = _get_comparison(job_id)
        if job is None:
            return jsonify(ok=False, error="对比任务不存在。"), 404
        selected = [persona] if persona else PERSONAS
        accepted = False
        for selected_persona in selected:
            child = flow._jobs[job["_branch_ids"][selected_persona]]
            if child["id"] in flow._active:
                accepted = True
            elif child["status"] == "failed":
                accepted = True
                try:
                    flow._resume_job(child)
                except Exception:
                    pass
        snapshot = _snapshot(job)
        if not accepted:
            return jsonify(ok=False, error="所选链路已完成，无需重试。", job=snapshot), 409
        return jsonify(ok=True, job=snapshot), 202
