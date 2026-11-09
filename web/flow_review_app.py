#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import threading
import uuid
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for

WEB_DIR = Path(__file__).resolve().parent
ROOT = WEB_DIR.parent
sys.path.insert(0, str(ROOT))

from engine.llm_router import dump_json, load_runtime, resolve_backend_name  # noqa: E402
from engine.model_intent import current_intent_system  # noqa: E402
from engine.model_planner import current_planner_system  # noqa: E402
from engine.model_story import STORY_TITLE, build_story_display, build_story_retry_input, current_story_system, parse_story_response, pending_story_step, run_story  # noqa: E402
from engine.pipeline import Engine, default_request_scaffold, generate_behavior_plan, rerun_from_review_step  # noqa: E402
from engine.prompt_versions import save_prompt_version  # noqa: E402
from engine.showcase import read_manifest, load_frozen_session  # noqa: E402
from engine.trace_logger import PERSONA_DESCRIPTION_TITLE, SCENARIO_STRATEGY_TITLE, has_legacy_persona_step_order, normalize_persona_step_order  # noqa: E402
from engine.audit_narrator import enrich_audit_steps  # noqa: E402

CASES_PATH = ROOT / "cases" / "welcome_cases.json"
OUT_DIR = ROOT / "output" / "flow_review"
STEP_COUNT = 8

STEP_TITLES = {
    1: "输入：人格＋自由情境",
    2: "MODEL 1：情境理解",
    3: "MAP：人格主导功能查表",
    4: PERSONA_DESCRIPTION_TITLE,
    5: SCENARIO_STRATEGY_TITLE,
    6: "MAP：人格交互维度判断",
    7: "行为序列生成与输出（功能优先级＋调用负担）",
    8: STORY_TITLE,
}

app = Flask(
    __name__,
    template_folder=str(WEB_DIR / "templates"),
    root_path=str(WEB_DIR),
)
_engine_cache: dict[str, Engine] = {}
_session_store: dict[str, dict] = {}
_story_lock = threading.Lock()
_story_running: set[str] = set()
_planner_lock = threading.Lock()
_planner_running: set[str] = set()


def normalize_review_persona_steps(session: dict) -> dict:
    steps = session["result"].get("audit_steps") or []
    legacy_order = has_legacy_persona_step_order(steps)
    reordered = normalize_persona_step_order(steps)
    if reordered == steps:
        return session
    reordered = [enrich_audit_steps([s])[0] if s.get("step") in {4, 5} else s for s in reordered]
    normalized = {**session, "result": {**session["result"], "audit_steps": reordered}}
    # An old confirmation of only the scene step does not confirm the new
    # preceding persona step. Let the user review both in their current order.
    if legacy_order and int(session.get("confirmed_step") or 0) == 4:
        normalized["confirmed_step"] = 3
    return normalized


def get_review_session(session_id: str) -> dict | None:
    frozen = load_frozen_session(session_id)
    if frozen is not None:
        return normalize_review_persona_steps(frozen)
    session = _session_store.get(session_id)
    if session is None and re.fullmatch(r'[0-9a-f]{12}', session_id):
        path = OUT_DIR / f'{session_id}.json'
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            req, result = saved['request'], saved['result']
            session = {
                **saved, 'persona': req['persona'],
                'backend': saved.get('backend') or result['llm_backend'],
                'saved_path': str(path),
            }
        except (OSError, ValueError, KeyError, TypeError):
            return None
    if session is not None:
        session = normalize_review_persona_steps(session)
        _session_store[session_id] = session
    return session


def get_engine(backend: str) -> Engine:
    backend = resolve_backend_name(backend)
    if backend not in _engine_cache:
        _engine_cache[backend] = Engine(use_llm=(backend != "rules"), llm_backend=backend)
    return _engine_cache[backend]


def load_case_defs() -> list:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def pretty_json(data) -> str:
    return json.dumps(data if data is not None else {}, ensure_ascii=False, indent=2)


def build_prompt_info(backend: str, step_no: int, step_input: dict, step: dict) -> dict | None:
    if step_no == 8:
        prompt = step.get('prompt_used') or {}
        return {
            'label': '场景故事 · 本次模型输入',
            # Re-render with the current Step 8 contract so older saved
            # sessions do not resurrect the retired speech feature.
            'system_prompt': current_story_system(),
            'user_prompt': dump_json(step_input),
            'editable': False,
        }
    if backend == "rules":
        return None
    prompt_used = step.get("prompt_used") or {}
    if step_no == 2:
        return {
            "label": "MODEL 1 · 当前 LLM 请求",
            "system_prompt": prompt_used.get("system_prompt") or current_intent_system(),
            "user_prompt": prompt_used.get("user_prompt") or dump_json(step_input or {}),
            "editable": True,
        }
    if step_no == 7:
        user_prompt = prompt_used.get("user_prompt") or dump_json(step_input or {})
        try:
            user_prompt = dump_json(json.loads(user_prompt))
        except (TypeError, ValueError):
            pass
        return {
            "label": "行为序列生成 · 当前 LLM 请求",
            "system_prompt": prompt_used.get("system_prompt") or current_planner_system(),
            "user_prompt": user_prompt,
            "editable": True,
        }
    return None


def session_final_snapshot(result: dict) -> dict:
    final = result.get("final") or {}
    audit_steps = result.get("audit_steps") or []
    step7 = next((s for s in audit_steps if s.get("step") == 7), {})
    step7_output = step7.get("output") or {}
    validated_actions = step7_output.get("validated_actions") or []
    return {
        "scenario_id": result.get("scenario_id"),
        "llm_backend": result.get("llm_backend"),
        "model_version": result.get("model_version"),
        "behavior_call_type": final.get("behavior_call_type") or result.get("behavior_call_type"),
        "selected_capabilities": final.get("selected_capabilities") or result.get("selected_capabilities") or [],
        "O_Action_Plan": final.get("O_Action_Plan") or result.get("O_Action_Plan") or [],
        "validated_actions": validated_actions,
        "O_Priority_Plan": final.get("O_Priority_Plan") or result.get("O_Priority_Plan") or {},
        "O_Behavior": final.get("O_Behavior") or result.get("O_Behavior") or {},
        "scene_plan": final.get("scene_plan") or result.get("scene_plan") or "",
        "generation_status": final.get("generation_status") or result.get("generation_status") or "ok",
        "clarification_questions": final.get("clarification_questions") or result.get("clarification_questions") or step7_output.get("clarification_questions") or [],
        "generation_error": final.get("generation_error") or result.get("generation_error") or {},
    }


def step7_allows_story(result: dict) -> bool:
    step7 = next((step for step in result.get("audit_steps") or [] if step.get("step") == 7), {})
    output = step7.get("output") or {}
    actions = output.get("validated_actions")
    has_validated_actions = isinstance(actions, list) and bool(actions) and all(
        isinstance(action, dict) and action.get("capability") and action.get("name")
        for action in actions
    )
    plan_status = output.get("plan_status") or (output.get("planner_output") or {}).get("plan_status")
    # Demo review is output-first: clarification is advisory when Step 7 has
    # already produced a valid conditional sequence. Hard failures and an
    # actual no-tool outcome remain unable to generate a behavior story.
    blocked = {"fallback", "failed", "pending", "no_tool_needed"}
    return (
        bool(step7)
        and has_validated_actions
        and not result.get("step7_deferred")
        and step7.get("status") not in blocked
        and output.get("generation_status") not in blocked
        and session_final_snapshot(result)["generation_status"] not in blocked
        and plan_status in {None, "ready", "needs_clarification"}
    )


def build_nav_steps(session_id: str, confirmed_step: int, current_step: int, *, allow_story: bool = True) -> list[dict]:
    items = []
    max_open = min(STEP_COUNT, confirmed_step + 1)
    for step_no in range(1, STEP_COUNT + 1):
        accessible = step_no <= max_open and (step_no != 8 or allow_story)
        status = "pending"
        if step_no < current_step:
            status = "confirmed" if step_no <= confirmed_step else "available"
        elif step_no == current_step:
            status = "current"
        elif step_no <= confirmed_step:
            status = "confirmed"
        items.append(
            {
                "step": step_no,
                "title": STEP_TITLES[step_no],
                "status": status,
                "accessible": accessible,
                "url": url_for("flow_review_step", session_id=session_id, step_no=step_no) if accessible else None,
            }
        )
    return items


def get_step_record(result: dict, step_no: int) -> dict:
    for step in result.get("audit_steps") or []:
        if int(step.get("step") or 0) == step_no:
            return step
    raise KeyError(f"step {step_no} not found")


def make_step_view(session_id: str, session: dict, step_no: int) -> dict:
    result = session["result"]
    step = (session.get('story_step') or pending_story_step(session)) if step_no == 8 else get_step_record(result, step_no)
    # Previously rejected prose can now be read without another model request.
    if (step_no == 8 and step.get('status') == 'failed'
            and (step.get('error_info') or {}).get('category') == 'invalid_story'
            and isinstance(step.get('raw_llm_text'), str) and step['raw_llm_text'].strip()):
        step = {**step, 'status': 'ok', 'output': parse_story_response(step['raw_llm_text']), 'error_info': {}}
    prompt_info = build_prompt_info(session["backend"], step_no, step.get("input") or {}, step)
    confirmed_step = int(session.get("confirmed_step") or 0)
    step7_deferred = bool(result.get("step7_deferred"))
    can_open_next = confirmed_step >= step_no and step_no < STEP_COUNT
    final_snapshot = session_final_snapshot(result)
    allow_story = step7_allows_story(result)
    if step_no == 7 and not allow_story:
        can_open_next = False
    raw_llm_text = step.get("raw_llm_text") or ""
    step_output = step.get("output") or {}
    display_output = step_output
    if step_no == 8 and isinstance(step_output, dict):
        # Speech generation is retained for backward-compatible saved records,
        # but is intentionally removed from the Step 8 review surface.
        display_output = dict(step_output)
        display_output.pop('spoken_reply', None)
    planner_output = step_output.get("planner_output") or {}
    llm_error = (
        step.get("error_info")
        or step_output.get("generation_error")
        or step_output.get("llm_error")
        or planner_output.get("llm_error")
        or {}
    ) if step_no in {2, 7, 8} else {}
    generation_failed = step_no in {7, 8} and (
        step_output.get("generation_status") == "failed" or step.get("status") in {'fallback', 'failed'}
    )
    if raw_llm_text:
        raw_llm_pretty = raw_llm_text
        if step_no == 8:
            try:
                raw_story = json.loads(raw_llm_text)
                if isinstance(raw_story, dict):
                    raw_story.pop('spoken_reply', None)
                    raw_llm_pretty = pretty_json(raw_story)
            except (TypeError, ValueError):
                pass
    else:
        parts = ["当前没有可展示的 LLM 原始返回。"]
        if step.get("status") == "fallback":
            parts.append("本步已走 fallback。")
        if llm_error.get("category"):
            parts.append(f"错误类别：{llm_error.get('category')}")
        if llm_error.get("message"):
            parts.append(f"错误信息：{llm_error.get('message')}")
        if llm_error.get("response_excerpt"):
            parts.append(f"响应摘录：{llm_error.get('response_excerpt')}")
        raw_llm_pretty = "\n".join(parts)
    spoken_reply = step_output.get('spoken_reply')
    addition = step.get('spoken_reply_addition') or {}
    if not spoken_reply and addition.get('status') == 'ok':
        spoken_reply = (addition.get('output') or {}).get('spoken_reply')
    elapsed_seconds = step.get('elapsed_seconds')
    if isinstance(elapsed_seconds, (int, float)):
        response_time_label = f"{float(elapsed_seconds):.2f} 秒"
    elif step_no == 8 and step.get('status') == 'pending':
        response_time_label = "尚未生成"
    else:
        response_time_label = "未记录"
    return {
        "step": step,
        "step_no": step_no,
        "title": STEP_TITLES[step_no],
        "response_time_label": response_time_label if step_no in {2, 7, 8} else None,
        "input_pretty": pretty_json(step.get("input")),
        "output_pretty": pretty_json(display_output),
        "interaction_policy": step_output.get("interaction_policy") if step_no == 6 else None,
        "interaction_activations": step_output.get("activated_interaction_dimensions") if step_no == 2 else None,
        "raw_llm_text": raw_llm_text,
        "raw_llm_pretty": raw_llm_pretty,
        "prompt_info": prompt_info,
        "is_confirmed": step_no <= confirmed_step,
        "can_confirm": step_no == confirmed_step + 1 and not (step_no == 7 and not allow_story) and not (step_no == 8 and (not allow_story or step.get('status') != 'ok')),
        "can_generate_step7": step_no == 7 and confirmed_step >= 6 and step7_deferred,
        "can_retry_step7": step_no == 7 and confirmed_step >= 6 and generation_failed and not session.get('frozen'),
        "can_generate_story": step_no == 8 and confirmed_step >= 7 and allow_story and step.get('status') != 'ok',
        "story": build_story_display(step_output, session['persona']) if step_no == 8 and step.get('status') == 'ok' else None,
        "spoken_reply": spoken_reply if step_no == 8 and step.get('status') == 'ok' else None,
        "can_open_next": can_open_next,
        "previous_url": url_for("flow_review_step", session_id=session_id, step_no=step_no - 1) if step_no > 1 else None,
        "next_url": url_for("flow_review_step", session_id=session_id, step_no=step_no + 1) if can_open_next else None,
        "final_snapshot": final_snapshot,
        "can_edit_prompt": step_no in {2, 7} and session["backend"] != "rules",
        "generation_failed": generation_failed,
        "generation_failure_label": "生成失败/超时" if llm_error.get("category") == "timeout" else "生成失败",
        "generation_error": llm_error,
        "generation_error_user_message": llm_error.get('user_message') or llm_error.get('message') or '模型未返回有效结果。',
        "plan_status": final_snapshot["generation_status"],
        "clarification_questions": final_snapshot["clarification_questions"],
    }


def save_session_artifact(session_id: str, session: dict) -> str:
    if session.get('frozen'):
        raise ValueError('固定展示案例只读，不能覆盖')
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{session_id}.json"
    payload = {
        "session_id": session_id,
        "confirmed_step": session.get("confirmed_step", 0),
        "request": session.get("request"),
        "prompt_overrides": session.get("prompt_overrides") or {},
        "backend": session.get('backend'),
        "story_step": session.get('story_step'),
        "result": session.get("result"),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out_path)


@app.get("/")
def index():
    runtime = load_runtime()
    presets = []
    for c in load_case_defs():
        req = c.get("request") or {}
        presets.append(
            {
                "case_id": c["case_id"],
                "title": c.get("title"),
                "utterance": req.get("user_utterance") or "",
            }
        )
    return render_template(
        "flow_review_index.html",
        presets=presets,
        scenario_catalog=runtime.get("scenario_catalog") or [],
        default_backend="openai",
        error=None,
        showcase=[c for c in read_manifest().get('cases', []) if c.get('persona') == request.args.get('persona', 'INFP')],
        showcase_persona=request.args.get('persona', 'INFP'),
    )


@app.before_request
def protect_frozen_cases():
    if request.method == 'POST' and request.view_args and request.view_args.get('session_id'):
        if load_frozen_session(request.view_args['session_id']) is not None:
            abort(409, description='这是固定展示案例，不能重新生成或修改。请返回启动页新建一次运行。')


@app.get('/showcase/<case_id>')
def open_showcase(case_id: str):
    persona = request.args.get('persona', 'INFP')
    item = next((c for c in read_manifest().get('cases', []) if c.get('case_id') == case_id and c.get('persona') == persona), None)
    if not item:
        abort(404)
    return redirect(url_for('flow_review_step', session_id=item['session_id'], step_no=8))


@app.post("/start")
def start_flow_review():
    runtime = load_runtime()
    persona = (request.form.get("persona") or "INFP").upper()
    if persona not in {"INFP", "ESTJ"}:
        presets = []
        for c in load_case_defs():
            item_req = c.get("request") or {}
            presets.append(
                {
                    "case_id": c["case_id"],
                    "title": c.get("title"),
                    "utterance": item_req.get("user_utterance") or "",
                }
            )
        return render_template(
            "flow_review_index.html",
            presets=presets,
            scenario_catalog=runtime.get("scenario_catalog") or [],
            default_backend="openai",
            error="persona must be INFP or ESTJ",
        ), 400

    backend = resolve_backend_name(request.form.get("llm_backend") or "openai")
    utterance = request.form.get("user_utterance") or ""
    preset_case_id = request.form.get("preset_case_id") or ""

    prompt_overrides: dict = {}

    req = default_request_scaffold(
        persona,
        utterance,
        f"flow_review_{uuid.uuid4().hex[:10]}",
    )
    if preset_case_id:
        for c in load_case_defs():
            if c["case_id"] == preset_case_id and not utterance:
                req["user_utterance"] = (c.get("request") or {}).get("user_utterance") or ""
                break

    try:
        result = generate_behavior_plan(
            req,
            use_llm=(backend != "rules"),
            engine=get_engine(backend),
            llm_backend=backend,
            prompt_overrides=prompt_overrides,
            defer_step7=True,
        )
    except Exception as exc:
        presets = []
        for c in load_case_defs():
            item_req = c.get("request") or {}
            presets.append(
                {
                    "case_id": c["case_id"],
                    "title": c.get("title"),
                    "utterance": item_req.get("user_utterance") or "",
                }
            )
        return render_template(
            "flow_review_index.html",
            presets=presets,
            scenario_catalog=runtime.get("scenario_catalog") or [],
            default_backend=backend or "openai",
            error=str(exc),
        ), 500

    session_id = uuid.uuid4().hex[:12]
    session = {
        "session_id": session_id,
        "persona": persona,
        "backend": backend,
        "request": req,
        "result": result,
        "confirmed_step": 1,
        "prompt_overrides": prompt_overrides,
    }
    session["saved_path"] = save_session_artifact(session_id, session)
    _session_store[session_id] = session
    return redirect(url_for("flow_review_step", session_id=session_id, step_no=2))


@app.get("/session/<session_id>/step/<int:step_no>")
def flow_review_step(session_id: str, step_no: int):
    session = get_review_session(session_id)
    if not session:
        return redirect(url_for("index"))
    step_no = max(1, min(STEP_COUNT, step_no))
    max_open = min(STEP_COUNT, int(session.get("confirmed_step") or 0) + 1)
    if not step7_allows_story(session["result"]):
        max_open = min(7, max_open)
    if step_no > max_open:
        return redirect(url_for("flow_review_step", session_id=session_id, step_no=max_open))

    step_view = make_step_view(session_id, session, step_no)
    if session.get('frozen'):
        for key in ('can_confirm', 'can_generate_step7', 'can_generate_story', 'can_edit_prompt'):
            step_view[key] = False
        if step_view.get('prompt_info'):
            step_view['prompt_info']['editable'] = False
    final_snapshot = session_final_snapshot(session["result"])
    return render_template(
        "flow_review_step.html",
        session_id=session_id,
        persona=session["persona"],
        backend=session["backend"],
        request_id=(session.get("request") or {}).get("request_id"),
        user_utterance=(session.get("request") or {}).get("user_utterance"),
        confirmed_step=int(session.get("confirmed_step") or 0),
        step_view=step_view,
        nav_steps=build_nav_steps(session_id, int(session.get("confirmed_step") or 0), step_no, allow_story=step7_allows_story(session["result"])),
        final_snapshot=final_snapshot,
        saved_path=session.get("saved_path"),
        frozen=session.get('frozen', False),
        case_title=session.get('case_title'),
        demo_background=((session.get('request') or {}).get('context') or {}).get('demo_background'),
    )


@app.post("/session/<session_id>/step/<int:step_no>/confirm")
def confirm_step(session_id: str, step_no: int):
    session = get_review_session(session_id)
    if not session:
        return redirect(url_for("index"))
    current_confirmed = int(session.get("confirmed_step") or 0)
    if step_no in {7, 8} and not step7_allows_story(session["result"]):
        return redirect(url_for("flow_review_step", session_id=session_id, step_no=7))
    if step_no == current_confirmed + 1:
        if not 1 <= step_no <= STEP_COUNT or not make_step_view(session_id, session, step_no)['can_confirm']:
            return redirect(url_for('flow_review_step', session_id=session_id, step_no=min(step_no, STEP_COUNT)))
        session["confirmed_step"] = step_no
        session["saved_path"] = save_session_artifact(session_id, session)

    if step_no < STEP_COUNT:
        return redirect(url_for("flow_review_step", session_id=session_id, step_no=step_no + 1))
    return redirect(url_for("flow_review_step", session_id=session_id, step_no=step_no))


@app.post("/session/<session_id>/step/<int:step_no>/rerun")
def rerun_step_with_prompt(session_id: str, step_no: int):
    session = get_review_session(session_id)
    if not session:
        return redirect(url_for("index"))
    if step_no not in {2, 7}:
        return redirect(url_for("flow_review_step", session_id=session_id, step_no=step_no))
    if step_no == 7 and int(session.get('confirmed_step') or 0) < 6:
        return redirect(url_for('flow_review_step', session_id=session_id, step_no=6))

    action = request.form.get("action") or "apply"
    prompt_overrides = dict(session.get("prompt_overrides") or {})
    if action == "reset":
        prompt_overrides.pop(step_no, None)
    else:
        system_prompt = request.form.get("system_prompt") or ""
        user_prompt = request.form.get("user_prompt") or ""
        prompt_overrides[step_no] = {"system_prompt": system_prompt, "user_prompt": user_prompt}
        save_prompt_version(
            step=step_no,
            label=f"Step {step_no} 流程页修改",
            notes=f"来自流程记录 {session_id}",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            source="flow_review",
        )

    session.pop('story_step', None)
    session['confirmed_step'] = min(int(session.get('confirmed_step') or 0), step_no - 1)
    result = rerun_from_review_step(
        session["result"],
        step_no=step_no,
        engine=get_engine(session["backend"]),
        llm_backend=session["backend"],
        prompt_overrides=prompt_overrides,
        defer_step7=(step_no == 2),
    )
    session["result"] = result
    session["prompt_overrides"] = prompt_overrides
    session["confirmed_step"] = min(int(session.get("confirmed_step") or 0), step_no - 1)
    session["saved_path"] = save_session_artifact(session_id, session)
    return redirect(url_for("flow_review_step", session_id=session_id, step_no=step_no))


@app.post("/session/<session_id>/step/7/generate")
def generate_step7(session_id: str):
    session = get_review_session(session_id)
    if not session:
        return redirect(url_for("index"))
    if int(session.get("confirmed_step") or 0) < 6:
        return redirect(url_for("flow_review_step", session_id=session_id, step_no=6))
    with _planner_lock:
        if session_id in _planner_running or not (
                (session.get('result') or {}).get('step7_deferred')
                or make_step_view(session_id, session, 7)['can_retry_step7']):
            return redirect(url_for('flow_review_step', session_id=session_id, step_no=7))
        _planner_running.add(session_id)
    previous_result = session['result']
    session['confirmed_step'] = min(int(session.get('confirmed_step') or 0), 6)
    session.pop('story_step', None)
    try:
        result = rerun_from_review_step(
            previous_result,
            step_no=7,
            engine=get_engine(session['backend']),
            llm_backend=session['backend'],
            prompt_overrides=session.get('prompt_overrides') or {},
        )
        if session['result'] is previous_result and int(session.get('confirmed_step') or 0) >= 6:
            session['result'] = result
            session['saved_path'] = save_session_artifact(session_id, session)
    finally:
        with _planner_lock:
            _planner_running.discard(session_id)
    return redirect(url_for("flow_review_step", session_id=session_id, step_no=7))


@app.post('/session/<session_id>/step/8/generate')
def generate_step8(session_id: str):
    session = get_review_session(session_id)
    if not session:
        return redirect(url_for('index'))
    result = session['result']
    if int(session.get('confirmed_step') or 0) < 7 or not step7_allows_story(result):
        return redirect(url_for('flow_review_step', session_id=session_id, step_no=7))
    with _story_lock:
        if session_id in _story_running or (session.get('story_step') or {}).get('status') == 'ok':
            return redirect(url_for('flow_review_step', session_id=session_id, step_no=8))
        _story_running.add(session_id)
    try:
        record = run_story(build_story_retry_input(session), session['backend'])
        # Discard a response if an earlier step was regenerated while it ran.
        if session['result'] is result and int(session.get('confirmed_step') or 0) >= 7:
            session['story_step'] = record
            session['saved_path'] = save_session_artifact(session_id, session)
    finally:
        with _story_lock:
            _story_running.discard(session_id)
    return redirect(url_for('flow_review_step', session_id=session_id, step_no=8))


@app.post("/session/<session_id>/rerun_all")
def rerun_all(session_id: str):
    session = get_review_session(session_id)
    if not session:
        return redirect(url_for("index"))
    backend = session["backend"]
    req = session.get("request")
    if not req:
        return redirect(url_for("flow_review_step", session_id=session_id, step_no=1))
    session.pop('story_step', None)
    session['confirmed_step'] = 1
    try:
        result = generate_behavior_plan(
            req,
            use_llm=(backend != "rules"),
            engine=get_engine(backend),
            llm_backend=backend,
            prompt_overrides=session.get("prompt_overrides") or {},
            defer_step7=True,
        )
    except Exception:
        return redirect(url_for("flow_review_step", session_id=session_id, step_no=1))
    session["result"] = result
    session["confirmed_step"] = 1
    session["saved_path"] = save_session_artifact(session_id, session)
    return redirect(url_for("flow_review_step", session_id=session_id, step_no=2))


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=10086)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    print(f"Flow Review UI -> http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
