from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import pytest

from droidproxy.backend import AuthCommand, ServerManager
from droidproxy.prefs import PreferencesStore


def _write_fake_binary(path: Path, script: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def prefs_store(tmp_path: Path) -> PreferencesStore:
    return PreferencesStore(path=tmp_path / "config.toml")


@pytest.fixture
def fake_bundled_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Point the backend at a temp bundled config template and a temp merged path
    # so the test doesn't touch the real ~/.cli-proxy-api directory.
    template = tmp_path / "config.yaml"
    template.write_text(
        "port: 8318\n"
        "host: 127.0.0.1\n"
        "remote-management:\n"
        "  allow-remote: false\n"
        '  secret-key: ""  # Leave empty to disable management API\n'
    )
    merged = tmp_path / "auth" / "merged-config.yaml"

    monkeypatch.setattr("droidproxy.backend.bundled_config_yaml", lambda: template)
    monkeypatch.setattr("droidproxy.backend.merged_config_path", lambda: merged)
    return merged


def test_merged_config_reflects_prefs(
    tmp_path: Path, prefs_store: PreferencesStore, fake_bundled_config: Path
) -> None:
    prefs_store.update(
        {
            "allow_remote": True,
            "secret_key": "s3cr3t",
        }
    )
    prefs_store.set_provider_enabled("codex", False)

    binary = _write_fake_binary(tmp_path / "cli-proxy-api-plus", "#!/bin/sh\nsleep 0.1\n")
    manager = ServerManager(prefs_store=prefs_store, binary_path=binary)
    merged = manager.get_merged_config_path()

    content = merged.read_text()
    assert "allow-remote: true" in content
    assert 'secret-key: "s3cr3t"' in content
    assert "oauth-excluded-models:" in content
    assert "codex:" in content
    assert '    - "*"' in content

    mode = stat.S_IMODE(merged.stat().st_mode)
    assert mode == 0o600


@pytest.mark.asyncio
async def test_start_and_stop_subprocess(
    tmp_path: Path, prefs_store: PreferencesStore, fake_bundled_config: Path
) -> None:
    script = (
        "#!/bin/sh\n"
        'echo "ready"\n'
        "while true; do sleep 0.2; done\n"
    )
    binary = _write_fake_binary(tmp_path / "cli-proxy-api-plus", script)
    manager = ServerManager(prefs_store=prefs_store, binary_path=binary)

    started = await manager.start()
    assert started is True
    assert manager.is_running

    # Give stdout a moment to flush.
    for _ in range(20):
        if any("ready" in line for line in manager.logs()):
            break
        await asyncio.sleep(0.05)
    assert any("ready" in line for line in manager.logs())

    await manager.stop()
    assert not manager.is_running


@pytest.mark.asyncio
async def test_start_reports_failure_when_binary_missing(
    tmp_path: Path, prefs_store: PreferencesStore, fake_bundled_config: Path
) -> None:
    manager = ServerManager(
        prefs_store=prefs_store, binary_path=tmp_path / "does-not-exist"
    )
    assert await manager.start() is False


@pytest.mark.asyncio
async def test_auth_flow_success_when_binary_exits_zero(
    tmp_path: Path, prefs_store: PreferencesStore, fake_bundled_config: Path
) -> None:
    script = (
        "#!/bin/sh\n"
        'echo "Opening browser for Claude login"\n'
        "exit 0\n"
    )
    binary = _write_fake_binary(tmp_path / "cli-proxy-api-plus", script)
    manager = ServerManager(prefs_store=prefs_store, binary_path=binary)

    outcome = await manager.run_auth_command(AuthCommand.CLAUDE)
    assert outcome.success is True
    assert "browser" in outcome.message.lower()


@pytest.mark.asyncio
async def test_auth_flow_failure_when_binary_errors(
    tmp_path: Path, prefs_store: PreferencesStore, fake_bundled_config: Path
) -> None:
    script = (
        "#!/bin/sh\n"
        'echo "boom" >&2\n'
        "exit 3\n"
    )
    binary = _write_fake_binary(tmp_path / "cli-proxy-api-plus", script)
    manager = ServerManager(prefs_store=prefs_store, binary_path=binary)

    outcome = await manager.run_auth_command(AuthCommand.CLAUDE)
    assert outcome.success is False
    assert "boom" in outcome.message.lower()
