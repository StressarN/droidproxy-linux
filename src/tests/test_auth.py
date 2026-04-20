from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from droidproxy.auth import AuthManager, AuthWatcher, ServiceType


def _write_account(directory: Path, filename: str, payload: dict) -> Path:
    path = directory / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manager_groups_accounts_by_service(tmp_path: Path) -> None:
    _write_account(
        tmp_path,
        "claude-alice.json",
        {"type": "Claude", "email": "alice@example.com"},
    )
    _write_account(
        tmp_path,
        "codex-bob.json",
        {"type": "codex", "login": "bob"},
    )
    _write_account(
        tmp_path,
        "gemini-expired.json",
        {
            "type": "gemini",
            "email": "c@example.com",
            "expired": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )

    manager = AuthManager(directory=tmp_path)
    claude = manager.accounts_for(ServiceType.CLAUDE)
    codex = manager.accounts_for(ServiceType.CODEX)
    gemini = manager.accounts_for(ServiceType.GEMINI)

    assert [a.display_name for a in claude] == ["alice@example.com"]
    assert [a.display_name for a in codex] == ["bob"]
    assert gemini[0].is_expired is True


def test_toggle_disabled_persists_flag(tmp_path: Path) -> None:
    path = _write_account(
        tmp_path,
        "claude-a.json",
        {"type": "claude", "email": "a@example.com"},
    )
    _write_account(
        tmp_path,
        "claude-b.json",
        {"type": "claude", "email": "b@example.com"},
    )
    manager = AuthManager(directory=tmp_path)
    assert manager.toggle_disabled("claude-a.json") is True

    updated = json.loads(path.read_text())
    assert updated["disabled"] is True


def test_toggle_refuses_to_disable_last_enabled_account(tmp_path: Path) -> None:
    _write_account(
        tmp_path,
        "claude-only.json",
        {"type": "claude", "email": "a@example.com"},
    )
    manager = AuthManager(directory=tmp_path)
    assert manager.toggle_disabled("claude-only.json") is False


def test_delete_account_removes_file(tmp_path: Path) -> None:
    path = _write_account(
        tmp_path,
        "codex-z.json",
        {"type": "codex", "login": "z"},
    )
    manager = AuthManager(directory=tmp_path)
    assert manager.delete_account("codex-z.json") is True
    assert not path.exists()
    assert manager.accounts_for(ServiceType.CODEX) == []


def test_missing_directory_is_handled_gracefully(tmp_path: Path) -> None:
    ghost = tmp_path / "does-not-exist"
    manager = AuthManager(directory=ghost)
    assert manager.all_accounts() == []


@pytest.mark.asyncio
async def test_watcher_fires_on_new_credential_file(tmp_path: Path) -> None:
    manager = AuthManager(directory=tmp_path)
    watcher = AuthWatcher(manager)
    watcher.DEBOUNCE_SECONDS = 0.05  # type: ignore[misc]

    event = asyncio.Event()
    captured: dict[str, object] = {}

    async def listener(snapshot):
        captured.update(snapshot)
        event.set()

    watcher.add_listener(listener)
    watcher.start()
    try:
        _write_account(tmp_path, "new-claude.json", {"type": "claude", "email": "d@x"})
        await asyncio.wait_for(event.wait(), timeout=2.0)
    finally:
        watcher.stop()

    assert any(acc["display_name"] == "d@x" for acc in captured.get("claude", []))
