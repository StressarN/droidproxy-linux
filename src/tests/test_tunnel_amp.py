from __future__ import annotations

import stat
from pathlib import Path

import pytest

from droidproxy import tunnel
from droidproxy.amp import amp_cli_login_target, rewrite_amp_response_headers


def test_rewrite_amp_headers_prepends_api_on_relative_locations() -> None:
    headers = [("Location", "/auth/cli-login/verify")]
    assert rewrite_amp_response_headers(headers) == [
        ("Location", "/api/auth/cli-login/verify")
    ]


def test_rewrite_amp_headers_handles_absolute_urls() -> None:
    headers = [("Location", "https://ampcode.com/auth/cli-login")]
    assert rewrite_amp_response_headers(headers) == [
        ("Location", "/api/auth/cli-login")
    ]


def test_rewrite_amp_headers_leaves_api_locations_unchanged() -> None:
    headers = [("Location", "/api/already-prefixed")]
    assert rewrite_amp_response_headers(headers) == [
        ("Location", "/api/already-prefixed")
    ]


def test_rewrite_amp_cookie_domain_to_localhost() -> None:
    headers = [
        ("Set-Cookie", "sid=abc; Domain=.ampcode.com; Path=/"),
        ("Set-Cookie", "alt=def; Domain=ampcode.com; Secure"),
    ]
    rewritten = rewrite_amp_response_headers(headers)
    assert rewritten[0] == ("Set-Cookie", "sid=abc; Domain=localhost; Path=/")
    assert rewritten[1] == ("Set-Cookie", "alt=def; Domain=localhost; Secure")


def test_amp_cli_login_target_strips_api_prefix() -> None:
    assert (
        amp_cli_login_target("/api/auth/cli-login")
        == "https://ampcode.com/auth/cli-login"
    )
    assert (
        amp_cli_login_target("/auth/cli-login?token=x")
        == "https://ampcode.com/auth/cli-login?token=x"
    )


def test_find_cloudflared_when_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "cloudflared"
    fake.write_text("#!/bin/sh\necho stub\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}:{''}")
    monkeypatch.setattr(tunnel, "_COMMON_PATHS", ())
    found = tunnel.find_cloudflared()
    assert found is not None
    assert found.name == "cloudflared"


def test_find_cloudflared_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/nowhere")
    monkeypatch.setattr(tunnel, "_COMMON_PATHS", ())
    assert tunnel.find_cloudflared() is None


@pytest.mark.asyncio
async def test_start_reports_install_hint_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: None)
    mgr = tunnel.TunnelManager()
    result = await mgr.start(port=8317)
    assert result["running"] is False
    assert "not installed" in result["error"]
    assert mgr.is_running is False


@pytest.mark.asyncio
async def test_start_parses_url_and_stop_terminates_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "cloudflared"
    # Emit a trycloudflare URL on stderr then idle so stop() needs to kill it.
    fake.write_text(
        "#!/bin/sh\n"
        'echo "https://example-tunnel.trycloudflare.com" >&2\n'
        "sleep 30\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: fake)

    mgr = tunnel.TunnelManager()
    result = await mgr.start(port=8317)
    try:
        assert result["running"] is True
        assert result["url"] == "https://example-tunnel.trycloudflare.com"
        assert mgr.is_running
    finally:
        await mgr.stop()
    assert not mgr.is_running
    assert mgr.public_url is None
