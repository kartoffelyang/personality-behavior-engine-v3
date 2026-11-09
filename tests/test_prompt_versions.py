from pathlib import Path

from engine import prompt_versions as store


def test_save_and_activate_prompt_version(tmp_path, monkeypatch):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    step2 = prompt_dir / "step2.txt"
    step7 = prompt_dir / "step7.txt"
    step2.write_text("step 2 current", encoding="utf-8")
    step7.write_text("step 7 current", encoding="utf-8")
    monkeypatch.setattr(store, "STORE_DIR", tmp_path / "versions")
    monkeypatch.setattr(store, "MANIFEST_PATH", tmp_path / "versions" / "manifest.json")
    monkeypatch.setattr(store, "SOURCE_PATHS", {2: step2, 7: step7})
    monkeypatch.setattr(store, "_legacy_step7_prompt", lambda: "")
    monkeypatch.setattr(store, "_import_saved_history_unlocked", lambda manifest: manifest.update(history_imported=True))

    saved = store.save_prompt_version(
        step=7,
        label="candidate",
        notes="test",
        system_prompt="step 7 candidate",
        user_prompt='{"case":"example"}',
    )
    store.activate_prompt_version(saved["id"])
    versions = store.list_prompt_versions(7)

    assert step7.read_text(encoding="utf-8").strip() == "step 7 candidate"
    assert versions[0]["id"] == saved["id"]
    assert versions[0]["active"] is True
    assert versions[0]["user_prompt"] == '{"case":"example"}'


def test_prompt_versions_page_is_available():
    from web.app import app

    response = app.test_client().get("/prompt_versions")
    assert response.status_code == 200
    assert "提示词版本库" in response.get_data(as_text=True)
