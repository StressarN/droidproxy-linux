from __future__ import annotations

import json
from pathlib import Path

import pytest

from droidproxy.installer import (
    ALL_DROID_PROXY_MODELS,
    DROID_PROXY_MODELS,
    FIREWORKS_MODELS,
    KIMI_CODE_MODELS,
    SYNTHETIC_MODELS,
    factory_custom_models_installed,
    factory_settings_path,
    install_challenger_droids,
    install_factory_custom_models,
)

_DIRECT_API_IDS = {
    m["id"] for m in [*SYNTHETIC_MODELS, *KIMI_CODE_MODELS, *FIREWORKS_MODELS]
}


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
    # Without direct-API keys, only the OAuth-routed models make it through.
    assert len(result["installed"]) == len(DROID_PROXY_MODELS)
    # Direct-API model IDs are all skipped when no keys are provided.
    assert set(result["skipped"]) == _DIRECT_API_IDS
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
                    {"id": "custom:droidproxy:opus-4-7", "index": 0, "model": "x"},
                    {"id": "custom:droidproxy:gpt-5.4", "index": 1, "model": "x"},
                    {"id": "custom:CC:old-plugin", "index": 2, "model": "x"},
                    {"id": "custom:mine:keep", "index": 3, "model": "x"},
                ]
            }
        )
    )
    result = install_factory_custom_models(target_home=fake_home)
    settings = _read_settings(fake_home)
    ids = [m["id"] for m in settings["customModels"]]
    # Prior DroidProxy entry gets removed and re-added via current DROID_PROXY_MODELS,
    # not the stale ``model: "x"`` entry. The CC namespace is scrubbed entirely.
    assert "custom:CC:old-plugin" not in ids
    assert "custom:droidproxy:gpt-5.4" not in ids
    assert "custom:droidproxy:gpt-5.5" in ids
    assert "custom:mine:keep" in ids
    droidproxy_entry = next(
        m for m in settings["customModels"] if m["id"] == "custom:droidproxy:opus-4-7"
    )
    assert droidproxy_entry["model"] == "claude-opus-4-7"
    assert "custom:droidproxy:opus-4-7" in result["removed"]
    assert "custom:droidproxy:gpt-5.4" in result["removed"]
    assert "custom:CC:old-plugin" in result["removed"]


def test_apply_skips_disabled_providers(fake_home: Path) -> None:
    result = install_factory_custom_models(
        enabled_providers={"claude": True, "codex": False, "gemini": True},
        target_home=fake_home,
    )
    ids = [m["id"] for m in _read_settings(fake_home)["customModels"]]
    assert "custom:droidproxy:opus-4-7" in ids
    assert "custom:droidproxy:gpt-5.5" not in ids
    assert "custom:droidproxy:gpt-5.3-codex" not in ids
    assert "custom:droidproxy:gemini-3.1-pro" in ids
    assert "custom:droidproxy:gpt-5.5" in result["skipped"]


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


# --- Direct-API providers: Synthetic, Kimi Code, Fireworks Fire Pass ---------


def test_apply_includes_synthetic_models_when_key_set(fake_home: Path) -> None:
    result = install_factory_custom_models(
        api_keys={"synthetic": "syn_abcdef"},
        target_home=fake_home,
    )
    settings = _read_settings(fake_home)
    synthetic_entries = [
        m for m in settings["customModels"]
        if m["id"].startswith("custom:droidproxy:synthetic-")
    ]
    assert len(synthetic_entries) == len(SYNTHETIC_MODELS)
    for entry in synthetic_entries:
        assert entry["apiKey"] == "syn_abcdef"
        assert entry["baseUrl"] == "https://api.synthetic.new/openai/v1"
        # Internal markers must not leak into Factory's settings.
        assert "_provider_key" not in entry
    for m in SYNTHETIC_MODELS:
        assert m["id"] in result["installed"]


def test_apply_includes_kimi_code_when_key_set(fake_home: Path) -> None:
    result = install_factory_custom_models(
        api_keys={"kimi": "sk-kimi-test"},
        target_home=fake_home,
    )
    settings = _read_settings(fake_home)
    entry = next(
        m for m in settings["customModels"] if m["id"] == "custom:droidproxy:kimi-code"
    )
    assert entry["apiKey"] == "sk-kimi-test"
    assert entry["baseUrl"] == "https://api.kimi.com/coding"
    assert entry["provider"] == "anthropic"
    assert "custom:droidproxy:kimi-code" in result["installed"]


def test_apply_includes_fireworks_fire_pass_when_key_set(fake_home: Path) -> None:
    result = install_factory_custom_models(
        api_keys={"fireworks": "fw_test"},
        target_home=fake_home,
    )
    settings = _read_settings(fake_home)
    entry = next(
        m
        for m in settings["customModels"]
        if m["id"] == "custom:droidproxy:fireworks-kimi-k2p5-turbo"
    )
    assert entry["apiKey"] == "fw_test"
    assert entry["model"] == "accounts/fireworks/routers/kimi-k2p5-turbo"
    assert entry["baseUrl"] == "https://api.fireworks.ai/inference/v1"
    assert "custom:droidproxy:fireworks-kimi-k2p5-turbo" in result["installed"]


def test_apply_skips_direct_api_when_provider_disabled(fake_home: Path) -> None:
    result = install_factory_custom_models(
        enabled_providers={"synthetic": False, "kimi": True, "fireworks": True},
        api_keys={
            "synthetic": "syn_x",
            "kimi": "sk-kimi-x",
            "fireworks": "fw_x",
        },
        target_home=fake_home,
    )
    ids = {m["id"] for m in _read_settings(fake_home)["customModels"]}
    # Synthetic models should all be absent.
    for m in SYNTHETIC_MODELS:
        assert m["id"] not in ids
        assert m["id"] in result["skipped"]
    # Kimi + Fireworks should still be present.
    assert "custom:droidproxy:kimi-code" in ids
    assert "custom:droidproxy:fireworks-kimi-k2p5-turbo" in ids


def test_factory_custom_models_installed_considers_direct_api_keys(
    fake_home: Path,
) -> None:
    # With a Synthetic key set, models get installed.
    install_factory_custom_models(
        api_keys={"synthetic": "syn_x"}, target_home=fake_home
    )
    assert (
        factory_custom_models_installed(
            {"claude": True, "codex": True, "gemini": True, "synthetic": True},
            target_home=fake_home,
            api_keys={"synthetic": "syn_x"},
        )
        is True
    )
    # If Synthetic is later enabled with a key but the on-disk file is older
    # (no synthetic entries), the check should be False.
    assert (
        factory_custom_models_installed(
            {"claude": True, "codex": True, "gemini": True, "synthetic": True},
            target_home=fake_home,
            api_keys={"synthetic": "syn_x", "fireworks": "fw_x"},
        )
        is False  # fireworks entry wasn't applied yet
    )


def test_all_droid_proxy_models_ids_are_unique() -> None:
    ids = [m["id"] for m in ALL_DROID_PROXY_MODELS]
    assert len(ids) == len(set(ids))
