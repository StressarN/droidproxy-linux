from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from droidproxy.prefs import Preferences, PreferencesStore


@pytest.fixture
def store(tmp_path: Path) -> PreferencesStore:
    return PreferencesStore(path=tmp_path / "config.toml")


def test_defaults_match_macos_swift(store: PreferencesStore) -> None:
    snap = store.snapshot()
    assert snap.opus47_thinking_effort == "xhigh"
    assert snap.opus46_thinking_effort == "max"
    assert snap.opus45_thinking_effort == "high"
    assert snap.sonnet46_thinking_effort == "high"
    assert snap.gpt53_codex_reasoning_effort == "high"
    assert snap.gpt55_reasoning_effort == "high"
    assert snap.gpt53_codex_fast_mode is False
    assert snap.gpt55_fast_mode is False
    assert snap.gemini31_pro_thinking_level == "high"
    assert snap.gemini3_flash_thinking_level == "high"
    assert snap.claude_max_budget_mode is False
    assert snap.allow_remote is False
    assert snap.secret_key == ""
    assert snap.oled_theme is False
    assert snap.enabled_providers == {
        "claude": True,
        "codex": True,
        "gemini": True,
        "synthetic": True,
        "kimi": True,
        "fireworks": True,
    }
    assert snap.synthetic_api_key == ""
    assert snap.kimi_api_key == ""
    assert snap.fireworks_api_key == ""


def test_round_trip_persists_to_toml(store: PreferencesStore) -> None:
    store.set("opus47_thinking_effort", "max")
    store.set("claude_max_budget_mode", True)

    with store.path.open("rb") as fh:
        raw = tomllib.load(fh)

    assert raw["opus47_thinking_effort"] == "max"
    assert raw["claude_max_budget_mode"] is True


def test_reload_reflects_external_edit(tmp_path: Path) -> None:
    store = PreferencesStore(path=tmp_path / "config.toml")
    store.set("opus47_thinking_effort", "low")

    store.path.write_text('opus47_thinking_effort = "medium"\n')
    store.reload()

    assert store.get("opus47_thinking_effort") == "medium"


def test_invalid_value_rejected(store: PreferencesStore) -> None:
    with pytest.raises(ValueError):
        store.set("opus47_thinking_effort", "turbo")


def test_unknown_key_rejected(store: PreferencesStore) -> None:
    with pytest.raises(KeyError):
        store.set("nonexistent", "whatever")


def test_provider_toggles(store: PreferencesStore) -> None:
    store.set_provider_enabled("claude", False)
    assert store.is_provider_enabled("claude") is False
    assert store.is_provider_enabled("codex") is True
    assert "claude" in store.disabled_providers()
    store.set_provider_enabled("gemini", False)
    assert store.disabled_providers() == ["claude", "gemini-cli"]


def test_update_batch_applies_and_validates(store: PreferencesStore) -> None:
    store.update(
        {
            "sonnet46_thinking_effort": "max",
            "gpt55_fast_mode": True,
        }
    )
    assert store.get("sonnet46_thinking_effort") == "max"
    assert store.get("gpt55_fast_mode") is True


def test_malformed_toml_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("this is = not valid = toml\n")
    store = PreferencesStore(path=path)
    assert store.snapshot() == Preferences()


def test_legacy_enabled_providers_dict_gets_new_keys_merged(tmp_path: Path) -> None:
    """An older config.toml with only the OAuth providers must pick up the new
    direct-API provider keys with their defaults on reload."""
    path = tmp_path / "config.toml"
    path.write_text(
        "[enabled_providers]\n"
        "claude = true\n"
        "codex = false\n"
        "gemini = true\n"
    )
    store = PreferencesStore(path=path)
    providers = store.snapshot().enabled_providers
    assert providers["claude"] is True
    assert providers["codex"] is False
    assert providers["gemini"] is True
    assert providers["synthetic"] is True
    assert providers["kimi"] is True
    assert providers["fireworks"] is True


def test_legacy_gpt54_preferences_migrate_to_gpt55(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('gpt54_reasoning_effort = "xhigh"\ngpt54_fast_mode = true\n')
    store = PreferencesStore(path=path)
    snap = store.snapshot()
    assert snap.gpt55_reasoning_effort == "xhigh"
    assert snap.gpt55_fast_mode is True


def test_direct_api_provider_toggles(store: PreferencesStore) -> None:
    store.set_provider_enabled("synthetic", False)
    store.set_provider_enabled("kimi", False)
    store.set_provider_enabled("fireworks", False)
    assert store.is_provider_enabled("synthetic") is False
    assert store.is_provider_enabled("kimi") is False
    assert store.is_provider_enabled("fireworks") is False
    # Direct-API providers don't map to CLIProxyAPIPlus OAuth exclusions.
    assert store.disabled_providers() == []


def test_unknown_provider_rejected(store: PreferencesStore) -> None:
    with pytest.raises(ValueError):
        store.set_provider_enabled("bogus", False)


def test_direct_api_keys_roundtrip(store: PreferencesStore) -> None:
    store.update(
        {
            "synthetic_api_key": "syn_test123",
            "kimi_api_key": "sk-kimi-test",
            "fireworks_api_key": "fw_test",
        }
    )
    assert store.get("synthetic_api_key") == "syn_test123"
    assert store.get("kimi_api_key") == "sk-kimi-test"
    assert store.get("fireworks_api_key") == "fw_test"
