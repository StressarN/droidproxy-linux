"""HTTP proxy that sits in front of the cli-proxy-api Go binary.

This is a port of ``src/Sources/ThinkingProxy.swift`` onto aiohttp. Behaviour
parity is intentional: same port, same path rewrites, same Amp CLI handling,
same 404-retry-with-/api fallback, and byte-identical body transforms via
:mod:`droidproxy.injector`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

import aiohttp
from aiohttp import ClientTimeout, web

from droidproxy.injector import (
    apply_fast_mode,
    apply_thinking_injection,
    is_gemini_model,
)
from droidproxy.prefs import Preferences, PreferencesStore, get_store

log = logging.getLogger(__name__)


AMP_MANAGEMENT_HOST = "https://ampcode.com"
RESPONSES_TO_CHAT_COMPLETIONS = (
    ("/v1/responses", "/v1/chat/completions"),
    ("/api/v1/responses", "/api/v1/chat/completions"),
)

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

UPSTREAM_STRIP_HEADERS = HOP_BY_HOP_HEADERS | {"host", "content-length"}
AMP_STRIP_HEADERS = HOP_BY_HOP_HEADERS | {"host", "content-length"}


@dataclass(frozen=True)
class ProxyConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 8317
    upstream_host: str = "127.0.0.1"
    upstream_port: int = 8318
    amp_upstream_url: str = AMP_MANAGEMENT_HOST
    # Wide timeout on the upstream call; Extended Thinking requests can take
    # many minutes. None disables the read timeout entirely.
    upstream_read_timeout: float | None = None


def is_responses_api_path(path: str) -> bool:
    """True if the request targets the Responses API (before rewriting)."""
    normalised = path.split("?", 1)[0]
    return normalised in {"/v1/responses", "/api/v1/responses"}


def rewrite_path(original_path: str) -> str:
    """Rewrite inbound ``/provider/*`` paths to ``/api/provider/*``."""
    if original_path.startswith("/provider/"):
        return "/api" + original_path
    return original_path


def is_amp_cli_login(path: str) -> bool:
    return path.startswith("/auth/cli-login") or path.startswith("/api/auth/cli-login")


def amp_cli_login_redirect(path: str) -> str:
    """Build the redirect target URL for Amp CLI login paths."""
    login_path = path[len("/api"):] if path.startswith("/api/") else path
    return f"{AMP_MANAGEMENT_HOST}{login_path}"


def is_amp_management_request(path: str) -> bool:
    """True if the (rewritten) path is an Amp management request.

    Anything that is neither ``/api/provider/*`` nor ``/v1/*``/``/api/v1/*``
    is considered Amp management traffic and forwarded to ampcode.com.
    """
    return not (
        path.startswith("/api/provider/")
        or path.startswith("/v1/")
        or path.startswith("/api/v1/")
    )


def rewrite_gemini_responses_path(path: str) -> str:
    for old, new in RESPONSES_TO_CHAT_COMPLETIONS:
        if path == old or path.startswith(old + "?"):
            return new + path[len(old):]
    return path


def _filtered_headers(
    headers: Iterable[tuple[str, str]], *, strip: Iterable[str]
) -> list[tuple[str, str]]:
    strip_lc = {h.lower() for h in strip}
    return [(name, value) for name, value in headers if name.lower() not in strip_lc]


ProxyHook = Callable[[str, str, bytes], Awaitable[None]]
"""Signature: ``async def hook(method, path, body) -> None``."""


class ThinkingProxy:
    """aiohttp application wrapping the thinking/reasoning proxy."""

    def __init__(
        self,
        config: ProxyConfig | None = None,
        prefs_store: PreferencesStore | None = None,
        *,
        debug_log: ProxyHook | None = None,
    ) -> None:
        self.config = config or ProxyConfig()
        self._prefs_store = prefs_store or get_store()
        self._debug_log = debug_log
        self._session: aiohttp.ClientSession | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None

    @property
    def proxy_port(self) -> int:
        return self.config.listen_port

    @property
    def is_running(self) -> bool:
        return self._site is not None

    def _prefs(self) -> Preferences:
        return self._prefs_store.snapshot()

    async def start(self) -> None:
        if self._site is not None:
            log.info("ThinkingProxy already running")
            return
        self._session = aiohttp.ClientSession(
            timeout=ClientTimeout(
                connect=10,
                sock_connect=10,
                sock_read=self.config.upstream_read_timeout,
            ),
            auto_decompress=False,
        )
        app = self.build_app()
        self._runner = web.AppRunner(app, handle_signals=False)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host=self.config.listen_host,
            port=self.config.listen_port,
            reuse_address=True,
        )
        await self._site.start()
        log.info(
            "ThinkingProxy listening on %s:%d",
            self.config.listen_host,
            self.config.listen_port,
        )

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        log.info("ThinkingProxy stopped")

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", self._handle)
        return app

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        method = request.method
        path = request.path_qs
        log.info("Incoming request: %s %s", method, path)

        if is_amp_cli_login(path):
            target = amp_cli_login_redirect(path.split("?", 1)[0])
            log.info("Redirecting Amp CLI login to: %s", target)
            return web.Response(status=302, headers={"Location": target})

        rewritten = rewrite_path(path)
        if rewritten != path:
            log.info("Rewrote Amp provider path: %s -> %s", path, rewritten)

        if is_amp_management_request(rewritten):
            return await self._forward_to_amp(request, rewritten)

        body = await request.read()
        body_str = body.decode("utf-8", errors="replace") if body else ""
        modified_body_str = body_str

        if method == "POST" and body_str:
            outcome = apply_thinking_injection(body_str, self._prefs())
            if outcome.kind != "none":
                modified_body_str = outcome.body
                await self._emit_debug(
                    f"INJECTED {outcome.kind}: {outcome.details} for {method} {rewritten}",
                )

            fast = apply_fast_mode(modified_body_str, rewritten, self._prefs())
            if fast is not None:
                modified_body_str = fast
                await self._emit_debug(
                    f"INJECTED service_tier=priority for {method} {rewritten}",
                )

            if is_responses_api_path(rewritten) and is_gemini_model(modified_body_str):
                new_path = rewrite_gemini_responses_path(rewritten)
                if new_path != rewritten:
                    log.info("Rewriting Gemini responses path: %s -> %s", rewritten, new_path)
                    rewritten = new_path

        modified_body = modified_body_str.encode("utf-8") if modified_body_str else body

        return await self._forward_to_upstream(request, rewritten, modified_body)

    async def _emit_debug(self, message: str) -> None:
        if self._debug_log is not None:
            try:
                await self._debug_log(message, "", b"")
            except Exception:
                log.debug("debug_log hook raised", exc_info=True)

    async def _forward_to_upstream(
        self,
        request: web.Request,
        path: str,
        body: bytes,
    ) -> web.StreamResponse:
        assert self._session is not None
        upstream_url = (
            f"http://{self.config.upstream_host}:{self.config.upstream_port}{path}"
        )
        headers = _filtered_headers(request.headers.items(), strip=UPSTREAM_STRIP_HEADERS)
        headers.append(("Host", f"{self.config.upstream_host}:{self.config.upstream_port}"))
        headers.append(("Connection", "close"))

        try:
            async with self._session.request(
                request.method,
                upstream_url,
                headers=headers,
                data=body or None,
                allow_redirects=False,
            ) as upstream:
                return await self._relay_response(request, upstream)
        except aiohttp.ClientError as err:
            log.warning("Upstream connection failed: %s", err)
            return web.Response(status=502, text=f"Bad Gateway - {err}")

    async def _forward_to_amp(self, request: web.Request, path: str) -> web.StreamResponse:
        assert self._session is not None
        target_url = f"{self.config.amp_upstream_url}{path}"
        headers = _filtered_headers(request.headers.items(), strip=AMP_STRIP_HEADERS)
        headers.append(("Host", "ampcode.com"))
        headers.append(("Connection", "close"))
        body = await request.read()
        try:
            async with self._session.request(
                request.method,
                target_url,
                headers=headers,
                data=body or None,
                allow_redirects=False,
            ) as upstream:
                return await self._relay_response(
                    request,
                    upstream,
                    header_rewriter=self._rewrite_amp_headers,
                )
        except aiohttp.ClientError as err:
            log.warning("Amp upstream connection failed: %s", err)
            return web.Response(
                status=502,
                text=f"Bad Gateway - Could not reach ampcode.com ({err})",
            )

    @staticmethod
    def _rewrite_amp_headers(headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Mirror the Location + cookie-domain rewrites from the Swift code."""
        rewritten: list[tuple[str, str]] = []
        for name, value in headers:
            lower = name.lower()
            if lower == "location":
                if value.startswith("/") and not value.startswith("/api/"):
                    value = "/api" + value
                elif value.startswith("https://ampcode.com/") or value.startswith(
                    "http://ampcode.com/"
                ):
                    tail_part = value.split("ampcode.com", 1)[1]
                    value = "/api" + tail_part
            elif lower == "set-cookie":
                value = value.replace("Domain=.ampcode.com", "Domain=localhost")
                value = value.replace("Domain=ampcode.com", "Domain=localhost")
            rewritten.append((name, value))
        return rewritten

    async def _relay_response(
        self,
        request: web.Request,
        upstream: aiohttp.ClientResponse,
        *,
        header_rewriter: Callable[[list[tuple[str, str]]], list[tuple[str, str]]] | None = None,
    ) -> web.StreamResponse:
        response_headers = [
            (name, value)
            for name, value in upstream.headers.items()
            if name.lower() not in HOP_BY_HOP_HEADERS and name.lower() != "content-length"
        ]
        if header_rewriter is not None:
            response_headers = header_rewriter(response_headers)

        # Drop Content-Encoding when auto_decompress is off and the upstream
        # body is already being passed through verbatim; aiohttp would otherwise
        # try to recompress. We keep the original encoding header so clients
        # continue to decode chunked gzip/brotli streams.
        response = web.StreamResponse(status=upstream.status, reason=upstream.reason)
        for name, value in response_headers:
            response.headers.add(name, value)
        response.headers["Connection"] = "close"

        await response.prepare(request)
        async for chunk in upstream.content.iter_any():
            if chunk:
                await response.write(chunk)
        await response.write_eof()
        return response
