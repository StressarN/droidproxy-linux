from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from droidproxy.auth import AuthManager, AuthWatcher
from droidproxy.backend import ServerManager
from droidproxy.context import AppContext
from droidproxy.paths import web_assets_dir
from droidproxy.prefs import PreferencesStore
from droidproxy.proxy import ProxyConfig, ThinkingProxy
from droidproxy.web import WebUI


@pytest.fixture
async def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppContext:
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "claude-alice.json").write_text(
        json.dumps({"type": "claude", "email": "alice@example.com"})
    )
    monkeypatch.setattr("droidproxy.auth.auth_dir", lambda: auth_dir)

    prefs = PreferencesStore(path=tmp_path / "config.toml")
    loop = asyncio.get_running_loop()
    server = ServerManager(prefs_store=prefs, binary_path=tmp_path / "not-installed")
    proxy = ThinkingProxy(config=ProxyConfig(listen_port=0), prefs_store=prefs)
    manager = AuthManager(directory=auth_dir)
    return AppContext(
        prefs=prefs,
        auth_manager=manager,
        auth_watcher=AuthWatcher(manager, loop=loop),
        server=server,
        proxy=proxy,
        loop=loop,
    )


@pytest.fixture
async def server(
    context: AppContext,
) -> AsyncIterator[tuple[aiohttp.ClientSession, int]]:
    app = WebUI(context, host="127.0.0.1", port=0, assets_dir=web_assets_dir()).build_app()
    runner = web.AppRunner(app, handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    port = runner.addresses[0][1]
    async with aiohttp.ClientSession() as session:
        try:
            yield session, port
        finally:
            await site.stop()
            await runner.cleanup()


async def test_index_and_assets_served(
    server: tuple[aiohttp.ClientSession, int],
) -> None:
    session, port = server
    index = await session.get(f"http://127.0.0.1:{port}/")
    html = await index.text()
    assert index.status == 200
    assert "<title>DroidProxy</title>" in html

    css = await session.get(f"http://127.0.0.1:{port}/styles.css")
    text = await css.text()
    assert css.status == 200
    assert "body" in text


async def test_status_payload_contains_accounts_and_effort_options(
    server: tuple[aiohttp.ClientSession, int],
) -> None:
    session, port = server
    resp = await session.get(f"http://127.0.0.1:{port}/api/status")
    data = await resp.json()
    assert resp.status == 200
    assert data["server_running"] is False
    assert data["proxy_url"].startswith("http://localhost:")
    assert "opus47_thinking_effort" in data["effort_options"]
    assert any(acc["display_name"] == "alice@example.com" for acc in data["accounts"]["claude"])


async def test_patch_prefs_persists_changes(
    server: tuple[aiohttp.ClientSession, int],
    context: AppContext,
) -> None:
    session, port = server
    resp = await session.patch(
        f"http://127.0.0.1:{port}/api/prefs",
        json={"opus47_thinking_effort": "max", "claude_max_budget_mode": True},
    )
    body = await resp.json()
    assert resp.status == 200
    assert body["applied"]["opus47_thinking_effort"] == "max"
    assert context.prefs.get("opus47_thinking_effort") == "max"
    assert context.prefs.get("claude_max_budget_mode") is True


async def test_patch_prefs_rejects_invalid_values(
    server: tuple[aiohttp.ClientSession, int],
) -> None:
    session, port = server
    resp = await session.patch(
        f"http://127.0.0.1:{port}/api/prefs",
        json={"opus47_thinking_effort": "turbo"},
    )
    assert resp.status == 400


async def test_toggle_provider_updates_prefs(
    server: tuple[aiohttp.ClientSession, int],
    context: AppContext,
) -> None:
    session, port = server
    resp = await session.post(
        f"http://127.0.0.1:{port}/api/prefs/providers/claude",
        json={"enabled": False},
    )
    data = await resp.json()
    assert resp.status == 200
    assert data == {"provider": "claude", "enabled": False}
    assert context.prefs.is_provider_enabled("claude") is False


async def test_delete_auth_account_removes_file(
    server: tuple[aiohttp.ClientSession, int],
    context: AppContext,
) -> None:
    session, port = server
    resp = await session.delete(
        f"http://127.0.0.1:{port}/api/auth/claude/claude-alice.json"
    )
    data = await resp.json()
    assert resp.status == 200
    assert data == {"ok": True}
    assert not (context.auth_manager.directory / "claude-alice.json").exists()


async def test_install_droids_copies_bundled_markdown(
    server: tuple[aiohttp.ClientSession, int],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, port = server
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(
        "droidproxy.installer.install_challenger_droids",
        lambda: {
            "droids": ["challenger-opus.md"],
            "commands": ["challenge-opus.md"],
            "droids_target": [str(fake_home / ".factory/droids")],
            "commands_target": [str(fake_home / ".factory/commands")],
        },
    )
    resp = await session.post(f"http://127.0.0.1:{port}/api/droids/install", json={})
    data = await resp.json()
    assert resp.status == 200
    assert "challenger-opus.md" in data["droids"]


async def test_apply_factory_models_endpoint(
    server: tuple[aiohttp.ClientSession, int],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, port = server
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(
        "droidproxy.installer.Path.home", classmethod(lambda cls: fake_home)
    )

    status_before = await (
        await session.get(f"http://127.0.0.1:{port}/api/status")
    ).json()
    assert status_before["factory"]["models_installed"] is False

    resp = await session.post(
        f"http://127.0.0.1:{port}/api/factory/models/apply", json={}
    )
    data = await resp.json()
    assert resp.status == 200
    assert data["installed"]
    assert data["settings_path"].endswith("/.factory/settings.json")
    assert (fake_home / ".factory" / "settings.json").exists()

    status_after = await (
        await session.get(f"http://127.0.0.1:{port}/api/status")
    ).json()
    assert status_after["factory"]["models_installed"] is True
