"""Tests for the ``droidproxy gui`` subcommand."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator

import pytest
from aiohttp import web as aiohttp_web

from droidproxy.cli import _open_gui
from droidproxy.proxy import ProxyConfig

try:
    from droidproxy.app import AppOptions
except ImportError:  # pragma: no cover
    pytest.skip("AppOptions not importable", allow_module_level=True)


class _OpenedUrls:
    """Records every URL that xdg-open / webbrowser would have opened."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def record(self, url: str) -> bool:
        self.seen.append(url)
        return True


@pytest.fixture
def stub_browser(monkeypatch: pytest.MonkeyPatch) -> _OpenedUrls:
    opened = _OpenedUrls()

    # Force the webbrowser fallback path by pretending xdg-open is missing.
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.record(url))
    return opened


async def _start_fake_ui(port: int) -> tuple[aiohttp_web.AppRunner, aiohttp_web.TCPSite]:
    async def _hello(_req: aiohttp_web.Request) -> aiohttp_web.Response:
        return aiohttp_web.Response(text="ok")

    app = aiohttp_web.Application()
    app.router.add_get("/", _hello)
    runner = aiohttp_web.AppRunner(app, handle_signals=False)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, host="127.0.0.1", port=port)
    await site.start()
    return runner, site


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def running_ui() -> AsyncIterator[int]:
    port = _free_port()
    runner, site = await _start_fake_ui(port)
    try:
        yield port
    finally:
        await site.stop()
        await runner.cleanup()


@pytest.fixture
def options_factory():
    def _make(*, web_port: int, proxy_port: int = 18317, upstream_port: int = 18318) -> AppOptions:
        return AppOptions(
            web_port=web_port,
            proxy_config=ProxyConfig(
                listen_port=proxy_port,
                upstream_port=upstream_port,
            ),
        )

    return _make


async def test_gui_opens_browser_when_daemon_already_running(
    running_ui: int,
    stub_browser: _OpenedUrls,
    options_factory,
    capsys,
) -> None:
    options = options_factory(web_port=running_ui)
    rc = _open_gui(options, start_if_needed=True, print_url_only=False)
    assert rc == 0
    assert stub_browser.seen == [f"http://127.0.0.1:{running_ui}/"]


async def test_gui_print_url_skips_browser(
    running_ui: int,
    stub_browser: _OpenedUrls,
    options_factory,
    capsys,
) -> None:
    options = options_factory(web_port=running_ui)
    rc = _open_gui(options, start_if_needed=True, print_url_only=True)
    assert rc == 0
    assert stub_browser.seen == []  # no browser call
    out = capsys.readouterr().out.strip()
    assert out == f"http://127.0.0.1:{running_ui}/"


async def test_gui_no_start_exits_nonzero_when_ui_unreachable(
    stub_browser: _OpenedUrls,
    options_factory,
    capsys,
) -> None:
    dead_port = _free_port()  # nothing listening on it
    options = options_factory(web_port=dead_port)
    rc = _open_gui(options, start_if_needed=False, print_url_only=False)
    assert rc == 1
    err = capsys.readouterr().err
    assert "not reachable" in err
    assert f"http://127.0.0.1:{dead_port}/" in err
    assert stub_browser.seen == []


async def test_gui_start_if_needed_fails_when_daemon_does_not_come_up(
    stub_browser: _OpenedUrls,
    options_factory,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    dead_port = _free_port()
    options = options_factory(web_port=dead_port)

    # Fake the "spawn a detached daemon" step so the test never actually
    # tries to start a background process -- we just want to exercise the
    # readiness-wait timeout branch.
    def _noop_run(cmd, check=True):  # type: ignore[no-untyped-def]
        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("subprocess.run", _noop_run)
    # Short readiness window so the test doesn't hang for 10 seconds.
    monkeypatch.setattr("time.monotonic", _fast_monotonic(start=0.0, step=3.0))

    rc = _open_gui(options, start_if_needed=True, print_url_only=False)
    assert rc == 1
    err = capsys.readouterr().err
    assert "didn't answer" in err
    assert stub_browser.seen == []


def _fast_monotonic(*, start: float, step: float):
    """Monotonic that advances by `step` each call."""
    state = {"t": start}

    def _tick() -> float:
        state["t"] += step
        return state["t"]

    return _tick
