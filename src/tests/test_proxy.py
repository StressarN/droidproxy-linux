from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from droidproxy.prefs import PreferencesStore
from droidproxy.proxy import ProxyConfig, ThinkingProxy


class FakeUpstream:
    """Minimal aiohttp server that records the last request it saw."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._response_body: bytes = b'{"ok":true}'
        self._response_status: int = 200
        self._response_headers: dict[str, str] = {"Content-Type": "application/json"}
        self._responses_by_path: dict[str, tuple[int, bytes, dict[str, str]]] = {}
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.port: int = 0

    def set_default(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._response_status = status
        self._response_body = body
        self._response_headers = headers or {"Content-Type": "application/json"}

    def set_response(
        self, path: str, status: int, body: bytes, headers: dict[str, str] | None = None
    ) -> None:
        self._responses_by_path[path] = (status, body, headers or {"Content-Type": "application/json"})

    async def _handle(self, request: web.Request) -> web.Response:
        body = await request.read()
        self.requests.append(
            {
                "method": request.method,
                "path": request.path,
                "query": request.query_string,
                "headers": dict(request.headers),
                "body": body,
            }
        )
        status, body_bytes, headers = self._responses_by_path.get(
            request.path_qs, (self._response_status, self._response_body, self._response_headers)
        )
        return web.Response(status=status, body=body_bytes, headers=headers)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self.runner = web.AppRunner(app, handle_signals=False)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, host="127.0.0.1", port=0)
        await self.site.start()
        assert self.runner.addresses
        self.port = self.runner.addresses[0][1]

    async def stop(self) -> None:
        if self.site is not None:
            await self.site.stop()
        if self.runner is not None:
            await self.runner.cleanup()


@pytest.fixture
async def upstream() -> AsyncIterator[FakeUpstream]:
    server = FakeUpstream()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def proxy(
    upstream: FakeUpstream, tmp_path: Path
) -> AsyncIterator[ThinkingProxy]:
    store = PreferencesStore(path=tmp_path / "config.toml")
    proxy = ThinkingProxy(
        config=ProxyConfig(
            listen_host="127.0.0.1",
            listen_port=0,
            upstream_host="127.0.0.1",
            upstream_port=upstream.port,
            amp_upstream_url="http://127.0.0.1:1",
        ),
        prefs_store=store,
    )
    runner = web.AppRunner(proxy.build_app(), handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    addr = runner.addresses[0]
    proxy._bound_port = addr[1]  # type: ignore[attr-defined]
    proxy._session = aiohttp.ClientSession(auto_decompress=False)
    try:
        yield proxy
    finally:
        await proxy._session.close()
        await site.stop()
        await runner.cleanup()


@pytest.fixture
async def proxy_client(proxy: ThinkingProxy) -> AsyncIterator[tuple[aiohttp.ClientSession, int]]:
    port: int = proxy._bound_port  # type: ignore[attr-defined]
    async with aiohttp.ClientSession() as client:
        yield client, port


async def test_opus_body_transformed_before_forwarding(
    upstream: FakeUpstream,
    proxy_client: tuple[aiohttp.ClientSession, int],
) -> None:
    client, port = proxy_client
    body = {"model": "claude-opus-4-7", "messages": []}
    resp = await client.post(
        f"http://127.0.0.1:{port}/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    await resp.read()
    assert resp.status == 200
    assert len(upstream.requests) == 1
    forwarded = upstream.requests[0]["body"].decode("utf-8")
    assert '"thinking":{"type":"adaptive"}' in forwarded
    assert '"output_config":{"effort":"xhigh"}' in forwarded
    assert '"stream":true' in forwarded


async def test_non_target_model_passes_through_verbatim(
    upstream: FakeUpstream,
    proxy_client: tuple[aiohttp.ClientSession, int],
) -> None:
    client, port = proxy_client
    body = b'{"model":"claude-haiku-3-5","messages":[]}'
    resp = await client.post(
        f"http://127.0.0.1:{port}/v1/messages",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    await resp.read()
    assert len(upstream.requests) == 1
    assert upstream.requests[0]["body"] == body


async def test_gemini_responses_rewritten_to_chat_completions(
    upstream: FakeUpstream,
    proxy_client: tuple[aiohttp.ClientSession, int],
) -> None:
    client, port = proxy_client
    body = b'{"model":"gemini-3.1-pro-preview","input":[]}'
    resp = await client.post(
        f"http://127.0.0.1:{port}/v1/responses",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    await resp.read()
    assert upstream.requests[0]["path"] == "/v1/chat/completions"


async def test_non_gemini_responses_path_is_preserved(
    upstream: FakeUpstream,
    proxy_client: tuple[aiohttp.ClientSession, int],
) -> None:
    client, port = proxy_client
    body = b'{"model":"gpt-5.5","input":[]}'
    resp = await client.post(
        f"http://127.0.0.1:{port}/v1/responses",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    await resp.read()
    assert upstream.requests[0]["path"] == "/v1/responses"


async def test_upstream_404_is_passed_through_without_retry(
    upstream: FakeUpstream,
    proxy_client: tuple[aiohttp.ClientSession, int],
) -> None:
    # Swift's retryWithApiPrefix path is dead code (never enabled); we match
    # that and simply relay the 404 back to the caller.
    client, port = proxy_client
    upstream.set_default(404, b"not found")
    resp = await client.get(f"http://127.0.0.1:{port}/api/v1/whatever")
    await resp.read()
    assert resp.status == 404
    assert len(upstream.requests) == 1


async def test_amp_cli_login_returns_redirect_without_touching_upstream(
    upstream: FakeUpstream,
    proxy_client: tuple[aiohttp.ClientSession, int],
) -> None:
    client, port = proxy_client
    resp = await client.get(
        f"http://127.0.0.1:{port}/auth/cli-login?token=xyz",
        allow_redirects=False,
    )
    await resp.read()
    assert resp.status == 302
    assert resp.headers["Location"].startswith("https://ampcode.com/auth/cli-login")
    assert upstream.requests == []


async def test_api_auth_cli_login_strips_api_prefix_in_redirect(
    proxy_client: tuple[aiohttp.ClientSession, int],
) -> None:
    client, port = proxy_client
    resp = await client.get(
        f"http://127.0.0.1:{port}/api/auth/cli-login",
        allow_redirects=False,
    )
    await resp.read()
    assert resp.status == 302
    assert resp.headers["Location"] == "https://ampcode.com/auth/cli-login"


async def test_provider_path_rewritten_to_api_provider(
    upstream: FakeUpstream,
    proxy_client: tuple[aiohttp.ClientSession, int],
) -> None:
    client, port = proxy_client
    resp = await client.get(f"http://127.0.0.1:{port}/provider/list")
    await resp.read()
    assert upstream.requests[0]["path"] == "/api/provider/list"


async def test_streaming_body_relayed_chunk_by_chunk(
    upstream: FakeUpstream,
    proxy_client: tuple[aiohttp.ClientSession, int],
) -> None:
    client, port = proxy_client
    upstream.set_default(200, b"A" * 4096 + b"B" * 4096)
    resp = await client.get(f"http://127.0.0.1:{port}/v1/anything")
    data = await resp.read()
    assert data == b"A" * 4096 + b"B" * 4096
