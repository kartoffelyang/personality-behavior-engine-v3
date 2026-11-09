#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import threading
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, jsonify, render_template, request

WEB_DIR = Path(__file__).resolve().parent
ROOT = WEB_DIR.parent
sys.path.insert(0, str(ROOT))

from engine.llm_router import get_llm, load_runtime, resolve_backend_name  # noqa: E402
from engine.model_story import build_story_input, build_story_display, run_story
from engine.trace_logger import make_step
from engine.model_intent import run_intent  # noqa: E402
from engine.pipeline import Engine, default_request_scaffold, generate_behavior_plan  # noqa: E402
from engine.prompt_versions import (  # noqa: E402
    activate_prompt_version,
    list_prompt_versions,
    save_prompt_version,
)
from web.auto_run import auto_run as auto_run_blueprint  # noqa: E402
from web.auto_run import page as auto_run_page  # noqa: E402
from web.auto_compare import auto_compare as auto_compare_blueprint  # noqa: E402
from web.fast import bp as fast_blueprint
from web.soa_sim import bp as soa_sim_blueprint
from web.persona_demo import bp as persona_demo_blueprint
from web.model_settings import bp as model_settings_blueprint

CASES_PATH = ROOT / "cases" / "welcome_cases.json"
OUT_CASES = ROOT / "output" / "cases"
MAIN_SHOWCASE = ROOT / "output" / "main_showcase"
PERSONA_VARIANTS = [
    {"id": "INFP", "label": "INFP"},
    {"id": "ESTJ", "label": "ESTJ"},
    {"id": "NO_MBTI", "label": "无 MBTI 基线"},
]

app = Flask(
    __name__,
    template_folder=str(WEB_DIR / "templates"),
    root_path=str(WEB_DIR),
)
app.register_blueprint(auto_run_blueprint)
app.register_blueprint(auto_compare_blueprint)
app.register_blueprint(fast_blueprint)
app.register_blueprint(soa_sim_blueprint)
app.register_blueprint(persona_demo_blueprint)
app.register_blueprint(model_settings_blueprint)


def load_case_defs() -> list:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def load_main_showcase() -> list:
    try:
        return json.loads((MAIN_SHOWCASE / 'current.json').read_text(encoding='utf-8'))['cases']
    except (OSError, ValueError, KeyError):
        return []


@app.get('/api/fixed-comparisons/<case_id>')
def fixed_comparison(case_id: str):
    item = next((c for c in load_main_showcase() if c['case_id'] == case_id), None)
    if not item:
        return jsonify({'ok': False, 'error': '固定案例不存在'}), 404
    filename = item.get('file', '')
    if not filename or Path(filename).name != filename:
        return jsonify({'ok': False, 'error': '固定案例路径无效'}), 404
    try:
        return jsonify(json.loads((MAIN_SHOWCASE / filename).read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return jsonify({'ok': False, 'error': '固定案例暂不可用'}), 404


def summarize_result(result: dict | None) -> dict:
    if not result:
        return {}
    final = result.get("final") or {}
    return {
        "call_type": final.get("behavior_call_type") or result.get("behavior_call_type"),
        "capabilities": final.get("selected_capabilities") or result.get("selected_capabilities") or [],
        "O_Behavior": final.get("O_Behavior") or result.get("O_Behavior") or {},
        "O_Action_Plan": final.get("O_Action_Plan") or result.get("O_Action_Plan") or [],
        "O_Priority_Plan": final.get("O_Priority_Plan") or result.get("O_Priority_Plan") or {},
        "scene_plan": final.get("scene_plan") or result.get("scene_plan") or "",
        "behavior_sequence": final.get("behavior_sequence") or result.get("behavior_sequence") or [],
        "audit_steps": result.get("audit_steps") or (result.get("decision_trace") or {}).get("audit_steps") or [],
        "scenario_id": result.get("scenario_id"),
        "llm_backend": result.get("llm_backend"),
        "model_version": result.get("model_version"),
        "parsed_context": result.get("parsed_context") or {},
        "story_step": result.get("story_step") or {},
        "story_display": build_story_display((result.get("story_step") or {}).get("output") or {}, result.get("persona") or ""),
        "generation_status": final.get("generation_status") or result.get("generation_status") or "ok",
        "clarification_questions": final.get("clarification_questions") or result.get("clarification_questions") or [],
        "generation_error": final.get("generation_error") or result.get("generation_error") or {},
        # legacy empty for old UI fragments
        "thinking_narrative": result.get("thinking_narrative") or "",
        "action_plan": result.get("action_plan") or "",
        "spoken_reply": "",
    }


def run_one_variant(
    *,
    backend: str,
    persona: str,
    utterance: str,
    preset_case_id: str | None,
    request_id: str,
    engine: Engine,
    shared_intent: dict | None = None,
) -> tuple[dict, dict]:
    req = default_request_scaffold(persona, utterance, request_id)
    if preset_case_id:
        for c in load_case_defs():
            if c["case_id"] == preset_case_id:
                preset_utt = (c.get("request") or {}).get("user_utterance") or ""
                if not utterance:
                    req["user_utterance"] = preset_utt
                break
    result = generate_behavior_plan(
        req,
        use_llm=(backend != "rules"),
        engine=engine,
        llm_backend=backend,
        shared_intent=shared_intent,
    )
    return req, result


def attach_story(result: dict, req: dict, persona: str, backend: str) -> None:
    step7 = next((s for s in result.get('audit_steps', []) if s['step'] == 7), {})
    output7 = step7.get('output') or {}
    actions = output7.get('validated_actions') or []
    blocked = {'fallback', 'failed', 'pending', 'no_tool_needed'}
    status = summarize_result(result)['generation_status']
    allowed = (actions and all(isinstance(a, dict) and a.get('name') and a.get('capability') for a in actions)
               and step7.get('status') not in blocked and status not in blocked
               and output7.get('generation_status') not in blocked and not result.get('step7_deferred')
               and (output7.get('plan_status') or (output7.get('planner_output') or {}).get('plan_status')) in (None, 'ready', 'needs_clarification'))
    if allowed and backend != 'rules':
        story = run_story(build_story_input({'persona': persona, 'request': req, 'result': result}), backend)
    else:
        story = make_step(8, '场景故事与核心改变点', status='blocked')
        story['error_info'] = {'message': '规则模式不生成故事。' if backend == 'rules' else '行为序列尚未就绪，未生成故事。'}
    result['story_step'] = story

@app.get("/")
def index():
    return auto_run_page()


@app.get("/comparison")
def comparison_index():
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
        "index.html",
        presets=presets,
        fixed_comparisons=load_main_showcase(),
        scenario_catalog=runtime.get("scenario_catalog") or [],
        default_backend=(runtime.get("llm") or {}).get("default_backend") or "rules",
    )


@app.get("/prompt_versions")
def prompt_versions_page():
    return render_template("prompt_versions.html", versions=list_prompt_versions())


@app.get("/api/prompt-versions")
def api_prompt_versions():
    return jsonify({"ok": True, "versions": list_prompt_versions()})


@app.post("/api/prompt-versions")
def api_save_prompt_version():
    data = request.get_json(force=True, silent=True) or {}
    try:
        item = save_prompt_version(
            step=int(data.get("step") or 0),
            label=str(data.get("label") or ""),
            notes=str(data.get("notes") or ""),
            system_prompt=str(data.get("system_prompt") or ""),
            user_prompt=str(data.get("user_prompt") or ""),
            source="version_library",
        )
        return jsonify({"ok": True, "version": item, "versions": list_prompt_versions()})
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/prompt-versions/<version_id>/activate")
def api_activate_prompt_version(version_id: str):
    try:
        item = activate_prompt_version(version_id)
        return jsonify({"ok": True, "version": item, "versions": list_prompt_versions()})
    except KeyError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


@app.post("/api/run")
def api_run():
    data = request.get_json(force=True, silent=True) or {}
    backend = resolve_backend_name(data.get("llm_backend") or data.get("backend"))
    # backward compat: use_llm true/false
    if "llm_backend" not in data and "backend" not in data and "use_llm" in data:
        backend = resolve_backend_name(None, bool(data.get("use_llm")))

    utterance = data.get("user_utterance") or data.get("scenario_text") or ""
    preset_case_id = data.get("preset_case_id")
    compare_variants = data.get("compare_variants")
    compare_mode = True if compare_variants is None else bool(compare_variants)
    requested_persona = (data.get("persona") or "").upper()
    if requested_persona and requested_persona not in {x["id"] for x in PERSONA_VARIANTS}:
        return jsonify({"ok": False, "error": "persona must be INFP / ESTJ / NO_MBTI"}), 400
    variant_ids = [x["id"] for x in PERSONA_VARIANTS] if compare_mode else [requested_persona or "INFP"]
    base_request_id = data.get("request_id") or f"custom_{uuid.uuid4().hex[:8]}"

    try:
        out_dir = ROOT / "output" / "custom"
        out_dir.mkdir(parents=True, exist_ok=True)

        # 每个请求新建一次引擎（只读 registry，可跨线程安全共享）；
        # LLM 客户端在 generate_behavior_plan 内部每次调用新建，天然线程隔离。
        engine = Engine(use_llm=(backend != "rules"), llm_backend=backend)

        # Resolve the actual utterance before the shared, personality-neutral
        # observation. Presets only supply text, never hidden scenario context.
        if not utterance and preset_case_id:
            preset = next((c for c in load_case_defs() if c["case_id"] == preset_case_id), {})
            utterance = (preset.get("request") or {}).get("user_utterance") or ""
        shared_intent = None
        if compare_mode:
            shared_request = default_request_scaffold(
                "NO_MBTI", utterance, f"{base_request_id}_shared_intent"
            )
            intent_started = time.monotonic()
            shared_intent = run_intent(shared_request, backend, get_llm(backend), engine.scenarios)
            shared_intent["elapsed_seconds"] = round(time.monotonic() - intent_started, 2)

        tasks = [
            (idx, persona, f"{base_request_id}_{idx}_{persona.lower()}")
            for idx, persona in enumerate(variant_ids, start=1)
        ]

        def _run_one(item) -> dict:
            idx, persona, request_id = item
            req, result = run_one_variant(
                backend=backend,
                persona=persona,
                utterance=utterance,
                preset_case_id=preset_case_id,
                request_id=request_id,
                engine=engine,
                shared_intent=shared_intent,
            )
            if data.get("include_story"):
                attach_story(result, req, persona, backend)
            result['_comparison_bundle'] = f'{base_request_id}_compare_bundle.json'
            out_path = out_dir / f"{req['request_id']}_{persona}.json"
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return {
                "persona": persona,
                "label": next((x["label"] for x in PERSONA_VARIANTS if x["id"] == persona), persona),
                "request": {
                    "persona": persona,
                    "user_utterance": req["user_utterance"],
                    "llm_backend": backend,
                    "request_id": req["request_id"],
                    "scenario_id": result.get("scenario_id"),
                },
                "result": summarize_result(result),
                "saved": str(out_path),
                "result_file": out_path.name,
            }

        # 三路人格（INFP / ESTJ / 无 MBTI）线程池并行，pool.map 保持结果顺序。
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            results = list(pool.map(_run_one, tasks))

        bundle_path = out_dir / f"{base_request_id}_compare_bundle.json"
        bundle_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        return jsonify(
            {
                "ok": True,
                "mode": "compare" if compare_mode else "single",
                "variants": results,
                "saved_bundle": str(bundle_path),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


_story_files_running = set()
_story_files_lock = threading.Lock()


@app.post('/api/comparison-story')
def comparison_story():
    filename = str((request.get_json(silent=True) or {}).get('result_file') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.json', filename) or filename.endswith('_compare_bundle.json'):
        return jsonify({'ok': False, 'error': '结果标识无效'}), 400
    path = ROOT / 'output' / 'custom' / filename
    with _story_files_lock:
        if filename in _story_files_running:
            return jsonify({'ok': False, 'error': '故事正在生成'}), 409
        _story_files_running.add(filename)
    try:
        try:
            result = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return jsonify({'ok': False, 'error': '结果不存在'}), 404
        if not isinstance(result, dict) or not result.get('audit_steps'):
            return jsonify({'ok': False, 'error': '结果格式无效'}), 400
        if (result.get('story_step') or {}).get('status') != 'ok':
            req = (result.get('_review_internal') or {}).get('request') or {}
            attach_story(result, req, result.get('persona', ''), result.get('llm_backend') or 'rules')
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(path)
        bundle_name = result.get('_comparison_bundle', '')
        if isinstance(bundle_name, str) and re.fullmatch(r'[A-Za-z0-9_-]+_compare_bundle\.json', bundle_name):
            with _story_files_lock:
                bundle_path = path.parent / bundle_name
                if bundle_path.exists():
                    bundle = json.loads(bundle_path.read_text(encoding='utf-8'))
                    for variant in bundle:
                        if variant.get('result_file') == filename:
                            variant['result'] = summarize_result(result)
                    temporary = bundle_path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding='utf-8')
                    temporary.replace(bundle_path)
        return jsonify({'ok': True, 'result': summarize_result(result)})
    finally:
        with _story_files_lock:
            _story_files_running.discard(filename)


class _SubAppMount:
    """把多个 WSGI 应用挂到指定 URL 前缀下（如 /flow_review）。

    挂载的子应用收到剥离前缀后的 PATH_INFO，同时 SCRIPT_NAME 带上前缀，
    因此子应用内 url_for / redirect 生成的链接会自动包含 /flow_review。
    """

    def __init__(self, default_app, mounts: dict[str, object]):
        self.default_app = default_app
        self.mounts = list(mounts.items())

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO") or "/"
        for prefix, sub in self.mounts:
            if path == prefix or path.startswith(prefix + "/"):
                rest = path[len(prefix):] or "/"
                base = (environ.get("SCRIPT_NAME") or "").rstrip("/")
                environ["SCRIPT_NAME"] = base + prefix
                environ["PATH_INFO"] = rest
                return sub(environ, start_response)
        return self.default_app(environ, start_response)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=10086)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # 人格导游在 /，原对比台在 /comparison，分步流程审阅在 /flow_review。
    import flow_review_app
    from werkzeug.serving import run_simple

    combined = _SubAppMount(app.wsgi_app, {"/flow_review": flow_review_app.app.wsgi_app})
    print(f"人格导游           -> http://{args.host}:{args.port}/")
    print(f"原对比台           -> http://{args.host}:{args.port}/comparison")
    print(f"审计版(分步审阅)    -> http://{args.host}:{args.port}/flow_review")
    run_simple(args.host, args.port, combined, threaded=True, use_reloader=args.debug)


if __name__ == "__main__":
    main()
