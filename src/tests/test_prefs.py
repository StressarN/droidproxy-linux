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
    assert snap.sonnet46_thinking_effort == "high"
    assert snap.gpt53_codex_reasoning_effort == "high"
    assert snap.gpt54_reasoning_effort == "high"
    assert snap.gpt53_codex_fast_mode is False
    assert snap.gpt54_fast_mode is False
    assert snap.gemini31_pro_thinking_level == "high"
    assert snap.gemini3_flash_thinking_level == "high"
    assert snap.claude_max_budget_mode is False
    assert snap.allow_remote is False
    assert snap.secret_key == ""
    assert snap.oled_theme is False
    assert snap.enabled_providers == {"claude": True, "codex": True, "gemini": True}


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
            "gpt54_fast_mode": True,
        }
    )
    assert store.get("sonnet46_thinking_effort") == "max"
    assert store.get("gpt54_fast_mode") is True


def test_malformed_toml_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("this is = not valid = toml\n")
    store = PreferencesStore(path=path)
    assert store.snapshot() == Preferences()
