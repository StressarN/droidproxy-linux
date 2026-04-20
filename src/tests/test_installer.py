from __future__ import annotations

import json
from pathlib import Path

import pytest

from droidproxy.installer import (
    DROID_PROXY_MODELS,
    factory_custom_models_installed,
    factory_settings_path,
    install_challenger_droids,
    install_factory_custom_models,
)


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def _read_settings(home: Path) -> dict:
    return json.loads(factory_settings_path(home).read_text())


def test_install_challenger_droids_copies_markdown(fake_home: Path) -> None:
    result = install_challenger_droids(target_home=fake_home)
    droids_dir = fake_home / ".factory" / "droids"
    commands_dir = fake_home / ".factory" / "commands"
    assert (droids_dir / "challenger-opus.md").exists()
    assert (commands_dir / "challenge-opus.md").exists()
    assert result["droids"]
    assert result["commands"]


def test_apply_creates_settings_when_missing(fake_home: Path) -> None:
    result = install_factory_custom_models(target_home=fake_home)
    assert len(result["installed"]) == len(DROID_PROXY_MODELS)
    assert result["skipped"] == []
    settings = _read_settings(fake_home)
    ids = [m["id"] for m in settings["customModels"]]
    assert ids == [m["id"] for m in DROID_PROXY_MODELS]
    # indices are assigned 0..N-1 when starting from an empty file
    assert [m["index"] for m in settings["customModels"]] == list(range(len(ids)))


def test_apply_preserves_unrelated_settings(fake_home: Path) -> None:
    path = factory_settings_path(fake_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "customModels": [
                    {
                        "model": "my-local-llm",
                        "id": "custom:mine:local",
                        "index": 0,
                        "baseUrl": "http://localhost:9000",
                        "apiKey": "x",
                        "displayName": "My LLM",
                        "maxOutputTokens": 8000,
                        "noImageSupport": True,
                        "provider": "openai",
                    }
                ],
            }
        )
    )

    install_factory_custom_models(target_home=fake_home)
    settings = _read_settings(fake_home)

    assert settings["theme"] == "dark"
    ids = [m["id"] for m in settings["customModels"]]
    assert ids[0] == "custom:mine:local"
    for m in DROID_PROXY_MODELS:
        assert m["id"] in ids
    # user's existing model keeps its original index, droidproxy models come after
    assert settings["customModels"][0]["index"] == 0
    assert settings["customModels"][1]["index"] == 1
    assert settings["customModels"][-1]["index"] == len(settings["customModels"]) - 1


def test_apply_is_idempotent_and_strips_prior_droidproxy_entries(
    fake_home: Path,
) -> None:
    install_factory_custom_models(target_home=fake_home)
    first = _read_settings(fake_home)
    install_factory_custom_models(target_home=fake_home)
    second = _read_settings(fake_home)
    assert first == second
    # No duplicates
    ids = [m["id"] for m in second["customModels"]]
    assert len(ids) == len(set(ids))


def test_apply_removes_legacy_and_cc_namespace(fake_home: Path) -> None:
    path = factory_settings_path(fake_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "customModels": [
                    {"id": "custom:droidproxy:opus-4-6", "index": 0, "model": "x"},
                    {"id": "custom:CC:old-plugin", "index": 1, "model": "x"},
                    {"id": "custom:mine:keep", "index": 2, "model": "x"},
                ]
            }
        )
    )
    result = install_factory_custom_models(target_home=fake_home)
    settings = _read_settings(fake_home)
    ids = [m["id"] for m in settings["customModels"]]
    assert "custom:droidproxy:opus-4-6" not in ids
    assert "custom:CC:old-plugin" not in ids
    assert "custom:mine:keep" in ids
    assert "custom:droidproxy:opus-4-6" in result["removed"]
    assert "custom:CC:old-plugin" in result["removed"]


def test_apply_skips_disabled_providers(fake_home: Path) -> None:
    result = install_factory_custom_models(
        enabled_providers={"claude": True, "codex": False, "gemini": True},
        target_home=fake_home,
    )
    ids = [m["id"] for m in _read_settings(fake_home)["customModels"]]
    assert "custom:droidproxy:opus-4-7" in ids
    assert "custom:droidproxy:gpt-5.4" not in ids
    assert "custom:droidproxy:gpt-5.3-codex" not in ids
    assert "custom:droidproxy:gemini-3.1-pro" in ids
    assert "custom:droidproxy:gpt-5.4" in result["skipped"]


def test_factory_custom_models_installed_false_when_missing(fake_home: Path) -> None:
    assert factory_custom_models_installed({"claude": True}, target_home=fake_home) is False


def test_factory_custom_models_installed_true_after_apply(fake_home: Path) -> None:
    install_factory_custom_models(target_home=fake_home)
    assert (
        factory_custom_models_installed(
            {"claude": True, "codex": True, "gemini": True}, target_home=fake_home
        )
        is True
    )


def test_factory_custom_models_installed_ignores_disabled_providers(
    fake_home: Path,
) -> None:
    install_factory_custom_models(
        enabled_providers={"claude": True, "codex": False, "gemini": True},
        target_home=fake_home,
    )
    # Still considered installed even though GPT IDs are missing, because
    # the user has disabled the codex provider.
    assert (
        factory_custom_models_installed(
            {"claude": True, "codex": False, "gemini": True}, target_home=fake_home
        )
        is True
    )
    # But with codex re-enabled, the installed check should now fail.
    assert (
        factory_custom_models_installed(
            {"claude": True, "codex": True, "gemini": True}, target_home=fake_home
        )
        is False
    )


def test_apply_writes_atomically_and_with_utf8(fake_home: Path) -> None:
    install_factory_custom_models(target_home=fake_home)
    path = factory_settings_path(fake_home)
    raw = path.read_bytes()
    # Must be valid UTF-8 and parseable JSON.
    decoded = raw.decode("utf-8")
    json.loads(decoded)
    # No leftover tmp file in the factory dir.
    residual = list(path.parent.glob(".factory-settings-*"))
    assert residual == []
