from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from droidproxy.updater import (
    InstallMethod,
    Updater,
    _parse_version,
    detect_install_method,
)


def test_parse_version_handles_prerelease_suffix() -> None:
    assert _parse_version("v1.8.7") == (1, 8, 7)
    assert _parse_version("1.8.7-0") == (1, 8, 7, 0)
    assert _parse_version("v1.8.7-beta.2") == (1, 8, 7, 0, 2)


def test_detect_install_method_identifies_appimage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPIMAGE", "/tmp/droidproxy.AppImage")
    assert detect_install_method() is InstallMethod.APPIMAGE


def test_detect_install_method_identifies_pipx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(
        "droidproxy.updater.sys",
        mock.Mock(executable="/home/user/.local/pipx/venvs/droidproxy/bin/python"),
    )
    assert detect_install_method() is InstallMethod.PIPX


def test_detect_install_method_defaults_to_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(
        "droidproxy.updater.sys",
        mock.Mock(executable="/usr/bin/python3.14"),
    )
    monkeypatch.setattr(
        "droidproxy.updater.Path.is_file", lambda self: False
    )
    assert detect_install_method() is InstallMethod.SOURCE


@pytest.mark.asyncio
async def test_check_for_updates_reports_newer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    updater = Updater(
        current_version="1.8.7",
        release_url="https://example.invalid/latest",
        cache_file=tmp_path / "update.json",
    )

    def _fake_blocking(self: Updater) -> object:
        from droidproxy.updater import UpdateInfo

        return UpdateInfo(
            installed="1.8.7",
            latest="v1.9.0",
            newer_available=True,
            install_method=InstallMethod.PIPX,
            upgrade_hint="pipx upgrade droidproxy",
            html_url="https://github.com/anand-92/droidproxy/releases/tag/v1.9.0",
        )

    monkeypatch.setattr(Updater, "_fetch_update_info_blocking", _fake_blocking)
    info = await updater.check_for_updates()
    assert info.newer_available is True
    assert info.latest == "v1.9.0"
    cached = json.loads((tmp_path / "update.json").read_text())
    assert cached["latest"] == "v1.9.0"


@pytest.mark.asyncio
async def test_check_for_updates_handles_network_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    updater = Updater(
        current_version="1.8.7",
        release_url="https://192.0.2.1/latest",
        cache_file=tmp_path / "update.json",
    )

    def _fake_blocking(self: Updater) -> object:
        from droidproxy.updater import UpdateInfo

        return UpdateInfo(
            installed="1.8.7",
            latest=None,
            newer_available=False,
            install_method=InstallMethod.SOURCE,
            upgrade_hint="unused",
        )

    monkeypatch.setattr(Updater, "_fetch_update_info_blocking", _fake_blocking)
    info = await updater.check_for_updates()
    assert info.latest is None
    assert info.newer_available is False
