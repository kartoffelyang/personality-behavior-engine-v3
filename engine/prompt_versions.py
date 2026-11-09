from __future__ import annotations

import ast
import hashlib
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = ROOT / "prompt_versions"
MANIFEST_PATH = STORE_DIR / "manifest.json"
SOURCE_PATHS = {
    2: ROOT / "prompts" / "intent_system.txt",
    7: ROOT / "prompts" / "planner_system_v2.txt",
}

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _empty_manifest() -> dict[str, Any]:
    return {"schema_version": 1, "history_imported": False, "active": {}, "versions": []}


def _read_manifest_unlocked() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return _empty_manifest()
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
            return _empty_manifest()
        data.setdefault("active", {})
        data.setdefault("history_imported", False)
        return data
    except (OSError, json.JSONDecodeError):
        return _empty_manifest()


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _write_manifest_unlocked(manifest: dict[str, Any]) -> None:
    _write_text_atomic(MANIFEST_PATH, json.dumps(manifest, ensure_ascii=False, indent=2))


def _digest(step: int, system_prompt: str, user_prompt: str) -> str:
    raw = f"{step}\0{system_prompt}\0{user_prompt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _content_path(version_id: str, kind: str) -> Path:
    return STORE_DIR / f"{version_id}_{kind}.txt"


def _save_unlocked(
    manifest: dict[str, Any],
    *,
    step: int,
    label: str,
    notes: str,
    system_prompt: str,
    user_prompt: str = "",
    source: str = "manual",
    activate: bool = False,
    created_at: str | None = None,
) -> dict[str, Any]:
    if step not in SOURCE_PATHS:
        raise ValueError("只支持 Step 2 和 Step 7")
    system_prompt = str(system_prompt or "").strip()
    user_prompt = str(user_prompt or "").strip()
    if not system_prompt:
        raise ValueError("系统提示词不能为空")
    content_hash = _digest(step, system_prompt, user_prompt)
    existing = next((v for v in manifest["versions"] if v.get("content_hash") == content_hash), None)
    if existing:
        if activate:
            manifest["active"][str(step)] = existing["id"]
        return existing

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    version_id = f"step{step}-{stamp}-{content_hash[:8]}"
    _write_text_atomic(_content_path(version_id, "system"), system_prompt + "\n")
    if user_prompt:
        _write_text_atomic(_content_path(version_id, "user"), user_prompt + "\n")
    item = {
        "id": version_id,
        "step": step,
        "label": str(label or f"Step {step} 版本").strip(),
        "notes": str(notes or "").strip(),
        "created_at": created_at or _now(),
        "source": source,
        "content_hash": content_hash,
        "system_file": _content_path(version_id, "system").name,
        "user_file": _content_path(version_id, "user").name if user_prompt else None,
        "system_chars": len(system_prompt),
        "system_lines": len(system_prompt.splitlines()),
        "user_chars": len(user_prompt),
        "user_lines": len(user_prompt.splitlines()) if user_prompt else 0,
    }
    manifest["versions"].append(item)
    if activate:
        manifest["active"][str(step)] = version_id
    return item


def _legacy_step7_prompt() -> str:
    source = ROOT / "engine" / "model_planner.py"
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "PLANNER_SYSTEM_LEGACY"
                for target in node.targets
            ):
                value = ast.literal_eval(node.value)
                return value if isinstance(value, str) else ""
    except (OSError, SyntaxError, ValueError):
        pass
    return ""


def _import_saved_history_unlocked(manifest: dict[str, Any]) -> None:
    if manifest.get("history_imported"):
        return
    review_dir = ROOT / "output" / "flow_review"
    if review_dir.exists():
        for path in sorted(review_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            created = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
            overrides = payload.get("prompt_overrides") or {}
            result = payload.get("result") or {}
            steps = result.get("audit_steps") or []
            for step in (2, 7):
                override = overrides.get(str(step)) or overrides.get(step) or {}
                if override.get("system_prompt"):
                    _save_unlocked(
                        manifest,
                        step=step,
                        label=f"Step {step} 流程页修改",
                        notes=f"从历史流程记录 {path.stem} 导入",
                        system_prompt=override["system_prompt"],
                        user_prompt=override.get("user_prompt") or "",
                        source="flow_review_history",
                        created_at=created,
                    )
                audit_step = next((s for s in steps if s.get("step") == step), {})
                used = audit_step.get("prompt_used") or {}
                system_prompt = used.get("system_prompt") or ""
                if system_prompt:
                    _save_unlocked(
                        manifest,
                        step=step,
                        label=f"Step {step} 历史默认版",
                        notes="从历史运行记录中检测并去重保存",
                        system_prompt=system_prompt,
                        source="run_history",
                        created_at=created,
                    )
    manifest["history_imported"] = True


def ensure_prompt_history() -> None:
    with _LOCK:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        manifest = _read_manifest_unlocked()
        legacy = _legacy_step7_prompt().strip()
        if legacy:
            _save_unlocked(
                manifest,
                step=7,
                label="Step 7 完整约束版",
                notes="精简前的系统提示词，包含白名单、决策状态和 no_call 约束",
                system_prompt=legacy,
                source="code_archive",
            )
        _import_saved_history_unlocked(manifest)
        for step, source_path in SOURCE_PATHS.items():
            if not source_path.exists():
                continue
            current = source_path.read_text(encoding="utf-8").strip()
            active_id = manifest.get("active", {}).get(str(step))
            active_item = next((v for v in manifest["versions"] if v.get("id") == active_id), None)
            active_text = ""
            if active_item:
                active_text = (STORE_DIR / active_item["system_file"]).read_text(encoding="utf-8").strip()
            if active_item and active_text == current:
                item = active_item
            else:
                matching = next(
                    (
                        v
                        for v in manifest["versions"]
                        if v.get("step") == step
                        and (STORE_DIR / v["system_file"]).read_text(encoding="utf-8").strip() == current
                    ),
                    None,
                )
                item = matching or _save_unlocked(
                    manifest,
                    step=step,
                    label=f"Step {step} 当前默认版",
                    notes="当前后台默认使用的系统提示词",
                    system_prompt=current,
                    source="current_default",
                )
            manifest["active"][str(step)] = item["id"]
        _write_manifest_unlocked(manifest)


def list_prompt_versions(step: int | None = None, *, include_content: bool = True) -> list[dict[str, Any]]:
    ensure_prompt_history()
    with _LOCK:
        manifest = _read_manifest_unlocked()
        items = []
        for stored in manifest["versions"]:
            if step is not None and stored.get("step") != step:
                continue
            item = dict(stored)
            item["active"] = manifest.get("active", {}).get(str(item["step"])) == item["id"]
            if include_content:
                item["system_prompt"] = (STORE_DIR / item["system_file"]).read_text(encoding="utf-8").rstrip("\n")
                user_file = item.get("user_file")
                item["user_prompt"] = (
                    (STORE_DIR / user_file).read_text(encoding="utf-8").rstrip("\n") if user_file else ""
                )
            items.append(item)
        return sorted(items, key=lambda v: (bool(v["active"]), v["created_at"], v["id"]), reverse=True)


def save_prompt_version(
    *,
    step: int,
    label: str,
    notes: str,
    system_prompt: str,
    user_prompt: str = "",
    source: str = "manual",
) -> dict[str, Any]:
    ensure_prompt_history()
    with _LOCK:
        manifest = _read_manifest_unlocked()
        item = _save_unlocked(
            manifest,
            step=step,
            label=label,
            notes=notes,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            source=source,
        )
        _write_manifest_unlocked(manifest)
        return dict(item)


def activate_prompt_version(version_id: str) -> dict[str, Any]:
    ensure_prompt_history()
    with _LOCK:
        manifest = _read_manifest_unlocked()
        item = next((v for v in manifest["versions"] if v.get("id") == version_id), None)
        if not item:
            raise KeyError("找不到该提示词版本")
        step = int(item["step"])
        system_prompt = (STORE_DIR / item["system_file"]).read_text(encoding="utf-8").rstrip("\n")
        _write_text_atomic(SOURCE_PATHS[step], system_prompt + "\n")
        manifest["active"][str(step)] = version_id
        _write_manifest_unlocked(manifest)
        result = dict(item)
        result["active"] = True
        return result
